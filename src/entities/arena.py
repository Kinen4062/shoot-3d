"""场地：地面 + 四面边界墙 + 若干柱子。

柱子的作用是双重的：
1. 给玩家视觉参照物，否则纯色地面跑起来没有速度感；
2. 给相机碰撞提供遮挡物，M1 就能验证「贴墙/贴柱转镜头不穿模」。

地面必须**有** collider：相机压低俯仰时若不挡住，镜头会钻到地板下面去，
从背面看地面就是一片虚空。用 cube 而不是 plane，是为了拿到规整的盒体碰撞，
顶面正好在 y=0，玩家脚底贴地。

渲染：36 个盒子合并成一次绘制
------------------------------
M6 的做法是墙面/压条/柱子/盖板各建一个 Entity —— 视觉上没问题，但那是
**36 次 draw call**，而它们的顶点加起来不到 900 个。2026-10-20 的实测画像
（`tools/_probe_cost.py`）里，空场景 254 次调用有 36 次花在这上面。

现在把 36 个盒子烘焙成一个带顶点色的 mesh（`core/mesh_builder.box_batch`），
只画一次。颜色差异（每根柱子 ±6 的色调、盖板比柱身亮）全部走顶点色，
所以看起来和合并前一模一样。

**碰撞一点没省**：视觉合并了，但墙和柱子仍然各有一个**不可见**的碰撞代理
（`visible=False`，不产生任何绘制）。没这么做的话，射线会打在一个覆盖全场
的巨型盒子上 —— 子弹飞到半空就"命中"了，柱子也挡不住视线。

地面单独留着，不参与合并：它靠 `texture_scale` 平铺网格贴图，而自定义着色器
读不到 Panda 的纹理矩阵（详见 `core/lighting.py` 开头第 1 个坑）。地板本来就
只有一个朝向，用默认着色器正好。
"""
from __future__ import annotations

import math
import random

from ursina import Entity, Texture, color

from src.core import config, lighting, mesh_builder


class Arena:
    def __init__(self) -> None:
        self.half = config.ARENA_HALF
        self.inner = config.ARENA_INNER       # 墙面（玩家真正能站到的边界）
        size = self.half * 2
        thickness = config.GROUND_THICKNESS

        # 一格 GROUND_GRID_CELL 米：贴图在整块地面上重复这么多次。
        cells = size / config.GROUND_GRID_CELL

        # 注意这里给的是 **Texture 对象**而不是路径字符串。
        # `texture="<路径>"` 只会拿 asset_folder 去做 `glob('**/' + 名字)`，
        # 传绝对路径（resource_path 的结果）会 glob 不到，只打一行
        # "missing texture" 警告然后地板变成纯色 —— 打包后就是这个表现。
        # 自己构造 Texture 才能既走 resource_path（打包后指向 _MEIPASS）又不丢贴图。
        grid_texture = Texture(config.resource_path(config.GROUND_GRID_TEXTURE))

        self.ground = Entity(
            model="cube",
            scale=(size, thickness, size),
            position=(0, -thickness / 2, 0),
            color=color.white,        # 颜色已经烘进贴图，这里不能再乘一层
            texture=grid_texture,
            texture_scale=(cells, cells),
            collider="box",
            name="ground",
        )

        # ---- 要合并进同一个 mesh 的盒子：(尺寸, 中心, 顶点色) ----
        boxes: list[tuple[tuple[float, float, float], tuple[float, float, float], tuple]] = []

        wall_rgb = (68, 74, 88)
        wall_h = config.WALL_HEIGHT
        wall_t = config.WALL_THICKNESS
        wall_specs = (
            ("wall_n", (size, wall_h, wall_t), (0, wall_h / 2, self.half)),
            ("wall_s", (size, wall_h, wall_t), (0, wall_h / 2, -self.half)),
            ("wall_e", (wall_t, wall_h, size), (self.half, wall_h / 2, 0)),
            ("wall_w", (wall_t, wall_h, size), (-self.half, wall_h / 2, 0)),
        )
        for _name, scale, position in wall_specs:
            boxes.append((scale, position, mesh_builder.rgba(wall_rgb)))

        # 墙顶压条：一条 0.12 米高的浅色带子。没有它，墙和背景几乎同色，
        # 远处"哪儿是墙"只能靠轮廓猜。
        trim_rgb = (104, 112, 130)
        trim_h = 0.12
        for scale, position in (
            ((size, trim_h, wall_t), (0, wall_h + trim_h / 2, self.half)),
            ((size, trim_h, wall_t), (0, wall_h + trim_h / 2, -self.half)),
            ((wall_t, trim_h, size), (self.half, wall_h + trim_h / 2, 0)),
            ((wall_t, trim_h, size), (-self.half, wall_h + trim_h / 2, 0)),
        ):
            boxes.append((scale, position, mesh_builder.rgba(trim_rgb)))

        # 柱子。固定种子：每次开局布局一致，方便对照调试。
        rng = random.Random(20261017)
        # 亮度扰动用**独立的**随机流。写在主 rng 里会多消耗一次抽样，
        # 后面所有柱子的位置都会跟着挪 —— 纯外观改动不该改变可玩布局。
        tone_rng = random.Random(20261018)
        # (x, z, 半径)。碰撞代理和 spaw/推开逻辑都用这份数据，
        # 不再从实体上反查 scale —— 合并之后没有"每根柱子一个实体"了。
        self.pillar_boxes: list[tuple[float, float, float]] = []
        pillar_specs: list[tuple[tuple, tuple, tuple]] = []
        for _ in range(14):
            x = rng.uniform(-self.half + 4, self.half - 4)
            z = rng.uniform(-self.half + 4, self.half - 4)
            if abs(x) < 5 and abs(z) < 5:   # 别把出生点堵死
                continue
            h = rng.uniform(2.0, 4.5)
            w = rng.uniform(1.2, 2.5)
            d = rng.uniform(1.2, 2.5)
            # 每根柱子亮度略有不同（±6）。完全同色时一排柱子像贴了一张图，
            # 这个差别小到说不出原因，但"一眼看过去有远近了"。
            tone = tone_rng.randint(-6, 6)
            pillar_specs.append((
                (w, h, d), (x, h / 2, z),
                mesh_builder.rgba((82 + tone, 86 + tone, 100 + tone)),
            ))
            cap_h = 0.14
            boxes.append((
                (w + 0.08, cap_h, d + 0.08), (x, h + cap_h / 2, z),
                mesh_builder.rgba((108 + tone, 114 + tone, 132 + tone)),
            ))
            self.pillar_boxes.append((x, z, max(w, d) * 0.5))
        boxes.extend(pillar_specs)

        # ---- 一次绘制画完所有静态几何 ----
        self.static = Entity(
            model=mesh_builder.box_batch(boxes, "arena_static"),
            name="arena_static",
            # 这里**绝对不能**加 collider：它覆盖整个场地，
            # 一发子弹会在半空"命中"这个巨型外壳。
        )
        lighting.apply_vcolor_lit(self.static)

        # ---- 碰撞代理：不可见，不产生绘制，但射线/相机照样撞得到 ----
        self.walls = [self._collider_proxy(name, scale, position)
                      for name, scale, position in wall_specs]
        self.pillars = [self._collider_proxy("pillar", size3, center3)
                        for size3, center3, _rgba in pillar_specs]

        # 相机碰撞只认真正挡视线的实体。盖板和压条不参与合并后的碰撞：
        # 它们贴在顶部，把镜头挡在半空反而更奇怪。
        self.solids = [*self.walls, *self.pillars]

    @staticmethod
    def _collider_proxy(name: str, scale, position) -> Entity:
        """一个只有碰撞、不渲染的盒子。

        `visible=False` 走的是 `NodePath.hide()`，只影响剔除，不影响
        Panda 的碰撞遍历 —— 所以不可见但打得到。这也是为什么它不会
        增加 draw call（隐藏节点在 cull 阶段就被丢了）。
        """
        return Entity(
            model="cube",
            scale=scale,
            position=position,
            collider="box",
            name=name,
            visible=False,
        )

    def clamp(self, entity: Entity, margin: float | None = None) -> None:
        """把实体限制在场地内。没有这一步，玩家会一路跑到虚空里。

        边界用 inner（墙的内表面）而不是 half（墙中心线），
        否则玩家会站进墙里半个身位，相机也就跟着贴进墙里。
        """
        if margin is None:
            margin = config.PLAYER_RADIUS
        limit = self.inner - margin
        x = max(-limit, min(limit, entity.x))
        z = max(-limit, min(limit, entity.z))
        if x != entity.x or z != entity.z:
            entity.position = (x, entity.y, z)

    def resolve_obstacles(self, entity: Entity, radius: float | None = None) -> None:
        """圆柱近似推开，防止走进柱子里。

        用平方距离先做粗筛，命中才开方 —— 柱子多起来后这个差别有意义。

        `radius` 可覆盖：玩家和敌人（tank 半径 0.8）体型不同，
        统一用 PLAYER_RADIUS 会让坦克半个身子插进柱子里。
        """
        r = config.PLAYER_RADIUS if radius is None else radius
        for px, pz, pr in self.pillar_boxes:
            min_d = pr + r
            dx = entity.x - px
            dz = entity.z - pz
            d2 = dx * dx + dz * dz
            if d2 >= min_d * min_d or d2 < 1e-8:
                continue
            d = math.sqrt(d2)
            push = min_d - d
            entity.x += dx / d * push
            entity.z += dz / d * push
