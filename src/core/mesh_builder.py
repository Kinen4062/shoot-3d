"""把一堆「轴对齐盒子」烘焙成单个 Geom。

为什么需要这个
--------------
ursina 里**每个带 model 的 Entity 就是一次独立的 draw call**。玩家的 36 个零件、
敌人的 18~20 个零件，每只怪都是一小堆方块，画面上 30 只怪就是 600 次调用 ——
而它们加起来只有 3.7 万个顶点。瓶颈完全在调用次数上，不在几何量上。

项目里所有外观零件都是**轴对齐的盒子**（见 `player_body.PARTS` 与
`enemy_body._part`：只有尺寸和中心，没有旋转），所以可以在这里直接按数据
生成三角形，一次烘焙成一个 mesh。合并之后：

* 颜色从「每个实体一个 `color`」变成**顶点色**（每个顶点自带 RGBA）；
* alpha 通道兼职**自发光权重**（0 = 正常受光，1 = 完全不受光照），
  这样面罩、灯条这些"自己会亮"的零件可以和其余零件待在同一个 mesh 里；
* 受击闪白从「逐个零件改色再复原」变成**一个 uniform**（见 `lighting`）。

坐标约定与零件表一致：单位是米、原点在身体中心、正面朝 +Z。
生成后由 rig 挂在宿主下面，宿主的非等比缩放照旧由 rig 的倒数抵消。

顶点布局：每个盒子 24 个顶点（6 面 × 4 角）、36 个索引。
必须逐面复制顶点 —— 相邻面之间不能共享法线，否则盒子的棱会被插值成圆角。
"""
from __future__ import annotations

from dataclasses import dataclass

from panda3d.core import Geom, GeomTriangles, GeomVertexData, GeomVertexFormat, GeomVertexWriter
from ursina import Mesh


@dataclass(frozen=True)
class BoxPart:
    """一个零件的**数据**（合并之后没有独立节点了）。

    玩家和敌人都用这一份：外观由零件表生成，合并成一个 mesh 之后，
    测试和调试仍然需要知道"每个零件本来在哪、多大、什么颜色"
    （包围盒、镜像对称、眼条朝向都靠这些数字断言）。
    """

    name: str
    size: tuple[float, float, float]
    center: tuple[float, float, float]
    rgb: tuple[int, int, int]
    emissive: bool = False

    @property
    def top(self) -> float:
        return self.center[1] + self.size[1] / 2

    @property
    def bottom(self) -> float:
        return self.center[1] - self.size[1] / 2

    def as_box(self):
        """转成 `box_batch` 要的 (size, center, rgba)。"""
        return (self.size, self.center,
                rgba(self.rgb, 1.0 if self.emissive else 0.0))

# 六个面的 (法线, u 轴, v 轴)。四角按「u×v = 法线」的顺序排列，
# 保证从盒子外侧看是逆时针（Panda 用逆时针做正面）。
# 顺序错了的表现是「模型看起来是里外翻转的」：看到的是背面，光照全暗。
FACES = (
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
)

# 每个盒子产生的顶点/索引数，测试与文档都引用这两个常量。
VERTS_PER_BOX = 24
TRIS_PER_BOX = 12

# 每个面的四个角对应的 UV。贴图是纯白色，UV 在视觉上无所谓 ——
# 但**这一列必须有**：`lighting.apply_vcolor_lit` 的着色器声明了
# `in vec2 p3d_MultiTexCoord0`，几何体没有 texcoord 列时会链接失败。
FACE_UVS = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))


def _corner(center, half, normal, u, v, su, sv) -> tuple[float, float, float]:
    """一个面的四角之一。

    位置 = 中心 + 法线方向的半尺寸（**把面推到盒子的表面上**）
                + 沿 u / v 的 ±半尺寸（在这个面内铺开四个角）。

    漏掉 `normal` 那一项是很容易犯的错：六个面会全部塌在盒子中心平面上，
    变成一个"交叉的薄片"。包围盒量起来完全正确（每个轴都还有两个面在撑），
    只有截图能看出来 —— 柱子会变成半透明似的重影。
    """
    return (
        center[0] + half[0] * (normal[0] + u[0] * su + v[0] * sv),
        center[1] + half[1] * (normal[1] + u[1] * su + v[1] * sv),
        center[2] + half[2] * (normal[2] + u[2] * su + v[2] * sv),
    )


def box_batch(boxes, name: str = "box_batch") -> Mesh:
    """把 `(size, center, rgba)` 列表烘焙成一个 Mesh。

    * `size`   —— 盒子的**完整**尺寸（不是半尺寸），和 `PARTS` 里的写法一致；
    * `center` —— 中心点；
    * `rgba`   —— 0~1 浮点，alpha 是自发光权重（见模块开头）。

    返回的 Mesh 可以直接当 `Entity(model=...)`，它自带顶点色列。
    """
    vertices: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    colors: list[tuple[float, float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    triangles: list[int] = []

    for size, center, rgba in boxes:
        half = (size[0] / 2.0, size[1] / 2.0, size[2] / 2.0)

        for normal, u, v in FACES:
            # 四角：(-u-v), (+u-v), (+u+v), (-u+v) —— 这个顺序让 (v1-v0)×(v2-v0) = 法线
            for (su, sv), uv in zip(((-1, -1), (1, -1), (1, 1), (-1, 1)), FACE_UVS, strict=True):
                vertices.append(_corner(center, half, normal, u, v, su, sv))
                normals.append(normal)
                colors.append(rgba)
                uvs.append(uv)

            # 这一面第一个顶点在总表里的位置（不能加盒子的起点：
            # 面循环里 len 一直在涨，再加一次起点会指到别的盒子上去）
            start = len(vertices) - 4
            # 一个四边形拆两个三角形。**绕序要按左手系来**：
            # 引擎启动时设了 `coordinate-system y-up-left`（见 ursina/window.py），
            # 左手系里的正面是顺时针。按右手系逆时针生成的话，六个面全成了背面，
            # 被剔除后看到的是盒子内壁 —— 表现是"柱子塌了一块、顶面斜着穿过去"，
            # 既不报错也不掉帧，只有截图能发现。
            triangles.extend((start, start + 2, start + 1, start, start + 3, start + 2))

    mesh = Mesh(vertices=vertices, triangles=triangles,
                normals=normals, colors=colors, uvs=uvs)
    if name:
        mesh.set_name(name)
    return mesh


def rgba(rgb: tuple[int, int, int], emissive: float = 0.0) -> tuple[float, float, float, float]:
    """0~255 的 RGB + 自发光权重 -> 顶点色。"""
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, float(emissive))


def model_from_boxes(boxes, name: str = "box_batch"):
    """手动拼 Geom 的版本（不经过 ursina.Mesh）。

    只在 `ursina.Mesh` 不可用的极端场合留作备用；正常路径用 `box_batch`。
    两个函数产生的顶点数必须一致，`test_visuals` 会断言这一点。
    """
    fmt = GeomVertexFormat.getV3n3c4t2()
    vdata = GeomVertexData(name, fmt, Geom.UHStatic)
    vdata.set_num_rows(len(boxes) * VERTS_PER_BOX)
    vertex = GeomVertexWriter(vdata, "vertex")
    normal_w = GeomVertexWriter(vdata, "normal")
    color_w = GeomVertexWriter(vdata, "color")
    uv_w = GeomVertexWriter(vdata, "texcoord")

    prim = GeomTriangles(Geom.UHStatic)
    for size, center, rgba_ in boxes:
        half = (size[0] / 2.0, size[1] / 2.0, size[2] / 2.0)
        base = vertex.get_write_row()
        for n, u, v in FACES:
            for (su, sv), uv in zip(((-1, -1), (1, -1), (1, 1), (-1, 1)), FACE_UVS, strict=True):
                vertex.add_data3(*_corner(center, half, n, u, v, su, sv))
                normal_w.add_data3(*n)
                color_w.add_data4(*rgba_)
                uv_w.add_data2(*uv)
            prim.add_vertices(base, base + 2, base + 1)
            prim.add_vertices(base, base + 3, base + 2)
            base += 4

    geom = Geom(vdata)
    geom.add_primitive(prim)
    from panda3d.core import GeomNode
    node = GeomNode(name)
    node.add_geom(geom)
    return node
