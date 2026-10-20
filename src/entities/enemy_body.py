"""敌人的外观：一套按体型参数化生成的低模。

为什么不像玩家那样用一张固定的零件表
------------------------------------
玩家的 scale 是常量 `(0.7, 1.8, 0.7)`，零件表写死就行。敌人不是：
四种类型的 `data.scale` 从 `(0.7, 1.5, 0.7)` 到 `(1.3, 2.4, 1.3)`。
如果共用一张零件表再让父级去拉伸，tank 的腿会被横向拉宽 1.44 倍、
runner 的头会被纵向拉长 —— 这正是"一个模型打四个人"最典型的失败样子。

所以这里按 `(w, h, d)` **现算**：躯干宽取 `w` 的 74%~96%，腿长取 `h` 的 36%~50%。
同一段代码，四种体型各自长成它该有的样子，而不是同一张皮被拉扯。

四种类型怎么让人一眼分出来
--------------------------
6 米机位下，玩家看到的是剪影和亮点，不是细节。所以区分只做两件事：

* **剪影**：runner 腿长细、tank 矮壮加双肩甲、shooter 肩上支出一根炮管；
* **发光位置**：grunt 胸口横条 → tank 胸口粗条 → runner 腿侧竖条 → shooter 单点。

BOSS「Liang」（`kind="liang"`，3 米）没有"要跟谁区分"的问题，它要的是**在人群里
一眼被认出来**：头顶五齿冠 + 双肩塔尖，全部是轮廓件。颜色照旧由
`data.color_rgb` 派生，所以红/黄/紫的阵营关系没变。

一次绘制画完一只敌人
--------------------
M6 时每只敌人是 18~20 个独立 Entity，也就是 18~20 次 draw call。
30 只同屏就是 600 次 —— 实测（`tools/_probe_cost.py`）满编场景 853 次调用里，
有 599 次是敌人贡献的，而它们的顶点加起来只有 3.7 万个。

现在整只敌人烘焙成一个带顶点色的 mesh（`core/mesh_builder.box_batch`）：
**零件表还在，只是不再各建一个实体**，所以外观、比例、白模导出全都不受影响。
发光件（眼条、胸灯）靠顶点色 alpha=1 在着色器里跳过光照，不再需要"不挂着色器"
这种按零件区分的做法。

坐标约定（改任何数字之前先读）
------------------------------
* 单位是**米**。原点在身体中心：脚底 `y = -h/2`，头顶 `y = +h/2`。
* 宿主的 scale 是非等比的，`rig` 用倒数把它抵消掉，内部回到"1 单位 = 1 米"。
  反过来说：**往这里加零件不用乘任何缩放系数**，写多少米就是多少米。
* 正面朝 **+Z**（`spawn_enemy` 用 `data.yaw` 让敌人转向玩家）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ursina import Entity

from src.core import config, lighting, mesh_builder
from src.core.mesh_builder import BoxPart as Part


@dataclass(frozen=True)
class Shape:
    """一种敌人的体型系数。全部是**比例**，乘上实际的 w/h 才是米。

    腿/躯干/头的高度比例之和 ≈ 1.0（差额留给盔沿），这样头顶正好落在 h/2 附近。
    第一版按 1.8 米的身高把数字写成绝对值，grunt 好看、tank 头顶却差 0.2 米 ——
    比例化之后四种体型一次对。
    """

    foot: float       # 脚高 / h
    leg: float        # 腿长 / h
    torso: float      # 躯干高 / h
    neck: float       # 颈高 / h
    head: float       # 头高 / h
    torso_w: float    # 躯干宽 / w
    arm: float        # 上臂宽 / w
    shoulder: float   # 护肩中心 |x| / w
    eye: tuple[float, float]      # 眼条 (宽/头宽, 高/头高)
    feature: str = "chest_bar"    # 差异零件，见 _add_feature


# 四种体型。数字是照着"它该死成什么样"调的，不是凑数：
# tank 腿短（重心低）、躯干最宽，护肩再往外撑；runner 腿占一半身高、躯干收窄。
SHAPES: dict[str, Shape] = {
    "grunt": Shape(foot=0.055, leg=0.420, torso=0.300, neck=0.030, head=0.175,
                   torso_w=0.86, arm=0.17, shoulder=0.44, eye=(0.85, 0.30)),
    "runner": Shape(foot=0.050, leg=0.500, torso=0.270, neck=0.025, head=0.145,
                    torso_w=0.74, arm=0.13, shoulder=0.40, eye=(0.60, 0.22),
                    feature="leg_stripes"),
    "tank": Shape(foot=0.060, leg=0.360, torso=0.360, neck=0.030, head=0.150,
                  torso_w=0.96, arm=0.21, shoulder=0.47, eye=(0.80, 0.34),
                  feature="pauldrons"),
    "shooter": Shape(foot=0.050, leg=0.410, torso=0.310, neck=0.030, head=0.160,
                     torso_w=0.78, arm=0.15, shoulder=0.43, eye=(0.35, 0.34),
                     feature="cannon"),
    # BOSS「Liang」：3.4 米高、躯干最宽，扛着双肩塔尖和头顶冠。
    #
    # 比例之和刻意只到 0.85（其余类型都在 0.97 上下）—— 省下来的 15% 是为了
    # **给冠留出位置**。身高是硬预算：零件顶到 h/2，`test_enemy_body_fits_its_hitbox`
    # 会量。冠要是硬贴在满预算的头顶上，就会戳出碰撞盒，玩家瞄准冠打会打空。
    # 所以这里把腿和躯干各让一点出来，冠和盔沿加起来正好填满到 1.0。
    "liang": Shape(foot=0.050, leg=0.330, torso=0.330, neck=0.025, head=0.115,
                   torso_w=1.00, arm=0.24, shoulder=0.52, eye=(0.90, 0.36),
                   feature="crown"),
}

# 没登记的类型（`TargetDummy` 的 name 是 target_dummy）用标准兵体型兜底。
DEFAULT_SHAPE = SHAPES["grunt"]


@dataclass
class Body:
    rig: Entity
    shell: Entity | None = None
    eye: Part | None = None
    parts: list[Part] = field(default_factory=list)


def palette(rgb: tuple[int, int, int]) -> dict[str, tuple[int, int, int]]:
    """从本体色派生一整套：暗甲 / 四肢 / 主装甲 / 亮甲片 / 发光。"""
    return {
        "dark": lighting.scale_rgb(rgb, 0.50),
        "limb": lighting.scale_rgb(rgb, 0.68),
        "main": rgb,
        "light": lighting.blend(rgb, (255, 255, 255), 0.30),
        "glow": lighting.blend(rgb, (255, 255, 255), config.ENEMY_EYE_LIGHTEN),
    }


def unit_scale(parent_scale) -> tuple[float, float, float]:
    """抵消宿主非等比缩放的倒数。和 `player_body.unit_scale` 同一个约定。"""
    # 长度断言同 `player_body.unit_scale`。
    return tuple(1.0 / s if abs(s) > 1e-6 else 1.0 for s in parent_scale)  # type: ignore[return-value]


def build(parent: Entity, kind: str, size, rgb: tuple[int, int, int]) -> Body:
    """在 parent 下搭出外观，返回 Body。

    `size` 是宿主的**世界尺寸**（就是 `data.scale`），不是半径也不是半宽。
    """
    w, h, d = (abs(float(v)) or 1.0 for v in size)
    hh = h * 0.5
    shape = SHAPES.get(kind, DEFAULT_SHAPE)
    pen = palette(rgb)

    rig = Entity(
        parent=parent,
        scale=unit_scale((parent.scale_x, parent.scale_y, parent.scale_z)),
        name="enemy_body",
    )
    body = Body(rig=rig)
    boxes: list = []

    def add(name, size_, center, key, lit=True) -> Part:
        part = Part(name, tuple(size_), tuple(center), pen[key], emissive=not lit)
        body.parts.append(part)
        # 不自发光 → alpha 0（正常受光）；自发光 → alpha 1（着色器跳过光照）
        boxes.append((size_, center, mesh_builder.rgba(pen[key], 0.0 if lit else 1.0)))
        return part

    # ---- 腿：脚 -> 腿 -> 髋 ----
    foot_h = h * shape.foot
    leg_h = h * shape.leg
    hip_y = -hh + foot_h + leg_h

    for side, sx in (("l", -1.0), ("r", 1.0)):
        add(f"foot_{side}", (w * 0.22, foot_h, d * 0.34),
            (sx * w * 0.17, -hh + foot_h / 2, d * 0.04), "dark")
        add(f"leg_{side}", (w * 0.20, leg_h, d * 0.24),
            (sx * w * 0.17, -hh + foot_h + leg_h / 2, 0.0), "limb")
        # 护膝：整条腿只有这一块亮色，跑动时它是最容易辨认的节奏点。
        add(f"knee_{side}", (w * 0.22, h * 0.030, d * 0.26),
            (sx * w * 0.17, -hh + foot_h + leg_h * 0.52, d * 0.02), "light")

    add("pelvis", (w * (shape.torso_w - 0.08), h * 0.070, d * 0.44),
        (0.0, hip_y + h * 0.02, 0.0), "dark")

    # ---- 躯干 ----
    torso_h = h * shape.torso
    torso_y = hip_y + torso_h / 2
    torso_top = hip_y + torso_h
    add("torso", (w * shape.torso_w, torso_h, d * 0.48), (0.0, torso_y, 0.0), "main")

    # ---- 肩与臂 ----
    shoulder_y = torso_top - h * 0.045
    for side, sx in (("l", -1.0), ("r", 1.0)):
        add(f"shoulder_{side}", (w * 0.20, h * 0.085, d * 0.30),
            (sx * w * shape.shoulder, shoulder_y, 0.0), "light")
        # 手臂要比护肩再往外一点。贴着躯干的话，远看只是"躯干侧面有个方块"，
        # 看起来像没有手臂；往外挪半个臂宽，"肩—臂"的轮廓才立起来。
        add(f"arm_{side}", (w * shape.arm, h * 0.20, d * 0.19),
            (sx * w * (shape.shoulder + 0.045), shoulder_y - h * 0.155, 0.0), "limb")

    # ---- 颈与头 ----
    neck_h = h * shape.neck
    head_h = h * shape.head
    head_w = w * 0.33
    head_d = d * 0.33
    head_y = torso_top + neck_h + head_h / 2

    add("neck", (w * 0.14, neck_h, d * 0.14), (0.0, torso_top + neck_h / 2, 0.0), "dark")
    add("head", (head_w, head_h, head_d), (0.0, head_y, 0.0), "main")
    # 颌部：头下面一道深色，视觉上把"头"和"颈"分开，否则远看是一根柱子。
    add("jaw", (head_w * 0.72, head_h * 0.22, d * 0.05),
        (0.0, head_y - head_h * 0.34, head_d * 0.48), "dark")
    add("helm", (w * 0.37, h * 0.035, d * 0.37),
        (0.0, torso_top + neck_h + head_h + h * 0.0175, 0.0), "light")

    # ---- 眼：唯一的朝向标识 ----
    # 自发光：顶点色 alpha=1，着色器跳过光照。要的就是"自己会亮"的平涂感。
    eye_w = head_w * shape.eye[0]
    eye_h = head_h * shape.eye[1]
    body.eye = add(
        "enemy_eye", (eye_w, eye_h, d * 0.035),
        (0.0, head_y + head_h * 0.06, head_d / 2 + d * 0.005),
        "glow", lit=False,
    )

    _add_feature(body, shape, pen, add, w, h, d,
                 torso_top, torso_h, hip_y, leg_h, foot_h, shoulder_y, head_w,
                 head_y, head_h)

    # ---- 烘焙成一个 mesh：一只敌人 = 一次绘制 ----
    body.shell = Entity(
        parent=rig,
        model=mesh_builder.box_batch(boxes, "enemy_shell"),
        name="enemy_shell",
    )
    lighting.apply_vcolor_lit(body.shell)
    return body


def _add_feature(body: Body, shape: Shape, pen, add, w: float, h: float, d: float,
                 torso_top: float, torso_h: float, hip_y: float,
                 leg_h: float, foot_h: float, shoulder_y: float,
                 head_w: float, head_y: float, head_h: float) -> None:
    """各类型各自的"记号"。这是混战里唯一来得及分辨的东西。"""
    if shape.feature == "chest_bar":
        # 普通兵：胸口一道横条，位置对着人眼高度，最容易被注意到。
        add("chest_bar", (w * 0.40, h * 0.035, d * 0.04),
            (0.0, torso_top - torso_h * 0.30, d * 0.24), "glow", lit=False)

    elif shape.feature == "pauldrons":
        # 重甲：双肩各压一块方甲，把剪影撑成"梯形"。胸口再来一发粗灯。
        for side, sx in (("l", -1.0), ("r", 1.0)):
            # 护肩压在肩线**以下**：顶到肩线以上的话，2.4 米体型的头会被
            # 肩甲埋掉一半，远看分不清"它有没有头"，朝向就丢了。
            add(f"pauldron_{side}", (w * 0.26, h * 0.10, d * 0.34),
                (sx * w * 0.50, shoulder_y - h * 0.010, 0.0), "main")
        # 横条而不是方块：方块在胸口像一块贴上去的补丁，横条才像"能量槽"。
        add("chest_core", (w * 0.36, h * 0.050, d * 0.04),
            (0.0, torso_top - torso_h * 0.32, d * 0.245), "glow", lit=False)

    elif shape.feature == "leg_stripes":
        # 快腿：两条腿外侧各一道竖灯。跑起来是两条竖线在闪，
        # 比任何几何细节都更能表达"这个家伙很快"。
        for side, sx in (("l", -1.0), ("r", 1.0)):
            add(f"stripe_{side}", (w * 0.045, leg_h * 0.55, d * 0.05),
                (sx * w * 0.115, hip_y - leg_h * 0.45, d * 0.125), "glow", lit=False)

    elif shape.feature == "cannon":
        # 远程兵：右肩支出一根前伸的炮管，朝 +Z。肩上多一个方块当炮座。
        add("cannon_mount", (w * 0.16, h * 0.07, d * 0.18),
            (w * 0.40, shoulder_y + h * 0.055, d * 0.04), "dark")
        add("cannon", (w * 0.11, h * 0.060, d * 0.62),
            (w * 0.40, shoulder_y + h * 0.055, d * 0.30), "light")

    elif shape.feature == "crown":
        # BOSS「Liang」。6 米机位下细节全糊，能传出去的只有剪影，
        # 所以记号全部做在轮廓上：头顶三齿冠 + 双肩塔尖，身上不贴任何装饰。
        #
        # 冠**从盔沿起算**而不是从头皮起算 —— 头本身只有 head_h，盔沿比它再高
        # 2×0.0175h。从头皮起算的话，齿的下半截会埋进盔里，露出来的只剩一小截。
        crest_base = head_y + head_h / 2 + h * 0.035
        for i, (sx, tall) in enumerate(((-0.44, 0.62), (0.0, 1.0), (0.44, 0.62))):
            tooth = h * 0.115 * tall
            # 齿底下沉 8% 埋进盔沿，避免"浮在头顶上方"的那道缝。
            # 中间那根最高，两侧对称降到 62% —— 三根粗齿的剪影比五根细齿
            # 更能扛住 1280px 下的像素化（五齿版本实测糊成一块方板）。
            add(f"crown_{i}", (head_w * 0.30, tooth, head_w * 0.34),
                (sx * head_w, crest_base + tooth * 0.42, 0.0), "light")
        # 肩塔：三层递减往外上方收。盒子是轴对齐的（`mesh_builder` 只吃轴对齐
        # 盒子），做不了真正的斜角 —— 阶梯式收窄在 6 米机位下读起来一样。
        for side, sx in (("l", -1.0), ("r", 1.0)):
            for tier in range(3):
                shrink = 1.0 - tier * 0.24
                add(f"spike_{side}_{tier}",
                    (w * 0.26 * shrink, h * 0.045, d * 0.32 * shrink),
                    (sx * w * (0.50 + 0.05 * tier),
                     shoulder_y + h * 0.065 + h * 0.042 * tier, 0.0),
                    "light" if tier == 0 else "main")
        # 胸口核心：全身上下唯一的自发光件（眼睛除外）。BOSS 的血量靠它做阶段提示，
        # 也顺便让"哪个是 BOSS"在混战里有第二个线索，不只靠剪影。
        add("core", (w * 0.30, h * 0.040, d * 0.04),
            (0.0, torso_top - torso_h * 0.30, d * 0.245), "glow", lit=False)
