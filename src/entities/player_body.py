"""玩家的外观：用一堆方块拼出来的装甲兵。

为什么单独一个模块
------------------
M6 第 8 条的验收标准是「玩家不再是一根蓝柱子」。拼外观要用二十多个零件，
塞进 `player.py` 会把血量/护盾/道具那摊逻辑淹掉；更实际的理由是——
**这是将来换成 .glb 模型时唯一要动的地方**（见 docs/M6_建模指南.md）。

坐标约定（改任何数字之前先读这段）
----------------------------------
* 本模块所有数字都是**米**，原点在身体中心：脚底 y=-0.9，头顶 y=+0.9。
  玩家实体本身的原点也在身体中心（`position.y = PLAYER_HEIGHT/2`），
  所以这里的 y 直接就是"离身体中线多高"，不用跟脚底换算。
* 宿主的 scale 是 (0.7, 1.8, 0.7) 这种**非等比**缩放，直接往里塞零件会被
  垂直拉长 1.8 倍。所以先建一个 `rig`，把宿主的缩放取倒数抵消掉，
  让 rig 内部回到"1 单位 = 1 米"的空间。这也顺便解决了枪的挂载问题
  （枪当年就是因为这个缩放才不敢挂到玩家身上，见 gun.py 开头）。
* 正面朝 +Z（和玩家 rotation_y = 0 时一致）。面罩、枪、脚尖都在 +Z 一侧。

零件表为什么留在这里而不进 config
----------------------------------
config 里放的是**会被别处引用的调色板**（面罩颜色要和准星/UI 呼应）；
这张表是纯几何数据，26 行、每行一个零件，搬进 config 只会让 config 变成模型文件。
"""
from __future__ import annotations

from ursina import Entity

from src.core import config, lighting, mesh_builder

# 调色板键 -> 0~255 颜色。发光件（visor / accent）不挂光照着色器，
# 保持"自发光"的平涂感；其余零件挂光照，才有明暗面。
PALETTE = {
    "dark": config.PLAYER_ARMOR_DARK_RGB,
    "limb": config.PLAYER_ARMOR_LIMB_RGB,
    "main": config.PLAYER_ARMOR_RGB,
    "light": config.PLAYER_ARMOR_LIGHT_RGB,
    "visor": config.PLAYER_VISOR_RGB,
    "accent": config.PLAYER_ACCENT_RGB,
    "boot": config.PLAYER_BOOT_RGB,
}
GLOW_KEYS = ("visor", "accent")

# 右臂是"持枪臂"：上臂短一点、小臂朝前伸、手落在枪的握把上。
# 这半边**故意不对称** —— 双臂对称地垂着是待机姿势，端着枪的时候不该这样。
# 列在这里是为了让 `test_visuals.test_parts_are_mirrored` 知道
# "这几对本来就不该镜像"，其余零件仍然必须严格对称。
ASYMMETRIC_PARTS = ("upper_arm_r", "forearm_r", "hand_r")

# (名字, 尺寸 xyz, 中心位置 xyz, 调色板键)
# 左右成对的零件必须**只差 x 符号**，`test_visuals.py` 会检查这一点：
# 手抄一遍镜像位置最容易偏 0.01，两边不对称在 6 米外也能看出来。
#
# 尺寸是照 1.8 米身高定的：头高 0.22（约 1/8 身，成年男性比例），
# 肩宽 0.88（含护肩）。第一版头给了 0.26 加一顶 0.06 的盔沿 —— 截图里是明显的
# Q 版大头，而且头一大，身体就显得短，整个人像被压扁了。
PARTS: tuple[tuple[str, tuple[float, float, float], tuple[float, float, float], str], ...] = (
    # ---- 腿 ----
    ("boot_l", (0.21, 0.14, 0.30), (-0.145, -0.83, 0.04), "boot"),
    ("boot_r", (0.21, 0.14, 0.30), (0.145, -0.83, 0.04), "boot"),
    ("leg_l", (0.17, 0.70, 0.20), (-0.145, -0.53, 0.00), "limb"),
    ("leg_r", (0.17, 0.70, 0.20), (0.145, -0.53, 0.00), "limb"),
    ("knee_l", (0.19, 0.07, 0.21), (-0.145, -0.36, 0.02), "main"),
    ("knee_r", (0.19, 0.07, 0.21), (0.145, -0.36, 0.02), "main"),
    # 重装的三块"外挂甲"：护胫、大腿外侧板、胯甲垂片。
    # 它们不改变下面的骨架比例（肩宽 0.88 那条契约不动），只是在剪影上
    # 往外撑出一层 —— 这是"重装"和"普通兵"在 6 米外唯一的区别。
    ("shin_l", (0.19, 0.34, 0.06), (-0.145, -0.68, 0.125), "light"),
    ("shin_r", (0.19, 0.34, 0.06), (0.145, -0.68, 0.125), "light"),
    ("thigh_plate_l", (0.05, 0.24, 0.18), (-0.235, -0.48, 0.00), "limb"),
    ("thigh_plate_r", (0.05, 0.24, 0.18), (0.235, -0.48, 0.00), "limb"),
    ("hip_plate_l", (0.09, 0.16, 0.22), (-0.225, -0.18, 0.02), "main"),
    ("hip_plate_r", (0.09, 0.16, 0.22), (0.225, -0.18, 0.02), "main"),
    # ---- 腰与躯干：胸甲最宽，腰带做一道浅色分割线 ----
    ("pelvis", (0.38, 0.18, 0.26), (0.0, -0.12, 0.00), "dark"),
    ("belt", (0.41, 0.05, 0.28), (0.0, -0.02, 0.00), "limb"),
    ("abdomen", (0.40, 0.20, 0.26), (0.0, 0.08, 0.00), "dark"),
    ("chest", (0.52, 0.34, 0.32), (0.0, 0.35, 0.00), "main"),
    ("chest_plate", (0.26, 0.13, 0.05), (0.0, 0.37, 0.165), "light"),
    ("core", (0.07, 0.045, 0.02), (0.0, 0.37, 0.192), "accent"),
    # 领口：胸甲顶到脖子之间那道深色围边。没有它，头像是直接插在胸口上。
    ("collar", (0.26, 0.05, 0.18), (0.0, 0.525, 0.00), "dark"),
    # 背包朝 -Z。第三人称镜头在背后，看到的最多的其实是它，
    # 所以背面必须有细节，否则"换了个模型"在游戏里根本看不出来。
    # 用 limb 而不是 dark：dark(36,44,58) 再乘背面的 0.80 亮度，和背景
    # (16,17,22) 几乎分不开 —— 截图里就是个黑洞，不是背包。
    ("pack", (0.34, 0.30, 0.14), (0.0, 0.36, -0.21), "limb"),
    ("pack_light", (0.14, 0.04, 0.03), (0.0, 0.45, -0.285), "accent"),
    # ---- 肩与手臂 ----
    # 右臂整体比左臂外移一点：枪挂在 x=+0.46，手要够得着握把，
    # 同时不能超过护肩太多（超过就变成"举着手臂跑"）。
    ("shoulder_l", (0.18, 0.20, 0.30), (-0.35, 0.44, 0.00), "light"),
    ("shoulder_r", (0.18, 0.20, 0.30), (0.35, 0.44, 0.00), "light"),
    ("upper_arm_l", (0.14, 0.26, 0.16), (-0.35, 0.22, 0.00), "limb"),
    ("forearm_l", (0.13, 0.24, 0.15), (-0.35, 0.00, 0.06), "main"),
    ("hand_l", (0.12, 0.10, 0.12), (-0.35, -0.14, 0.08), "boot"),
    ("upper_arm_r", (0.14, 0.22, 0.16), (0.37, 0.24, 0.02), "limb"),
    ("forearm_r", (0.13, 0.13, 0.30), (0.39, 0.20, 0.18), "main"),
    ("hand_r", (0.13, 0.13, 0.13), (0.42, 0.30, 0.36), "boot"),
    # ---- 头：面罩是唯一的朝向标识（原来的 `nose` 已经换成它）----
    ("neck", (0.12, 0.10, 0.12), (0.0, 0.575, 0.00), "dark"),
    ("head", (0.22, 0.22, 0.24), (0.0, 0.73, 0.00), "main"),
    ("jaw", (0.17, 0.06, 0.05), (0.0, 0.645, 0.115), "dark"),
    ("visor", (0.16, 0.055, 0.04), (0.0, 0.760, 0.125), "visor"),
    # 眉沿：面罩上方一段外挑的盔檐。头盔有了"屋檐"才有厚度，
    # 否则那块 0.22 的方块看起来就是个盒子。
    ("brow", (0.24, 0.03, 0.06), (0.0, 0.795, 0.12), "light"),
    ("helm", (0.24, 0.05, 0.26), (0.0, 0.865, 0.00), "light"),
    ("crest", (0.05, 0.05, 0.20), (0.0, 0.905, -0.03), "accent"),
)

# 轮廓的上下界（米，相对身体中心）。reset/相机逻辑都按 PLAYER_HEIGHT 算，
# 外观超出去一点点没关系，但超太多就会出现「模型插进地板」。
BODY_BOTTOM = -0.90
BODY_TOP = 0.93


def unit_scale(parent_scale) -> tuple[float, float, float]:
    """抵消宿主非等比缩放的倒数。

    用倒数而不是"把零件尺寸乘回去"：后者要求每个数字都记得乘，
    改一个尺寸就可能漏乘，表现是零件突然被拉长。
    """
    # 上游传进来的是 Vec3（3 元），但 `tuple(生成器)` 在类型上长度不可知，
    # 只能在这里断言一下返回值形状。
    return tuple(1.0 / s if abs(s) > 1e-6 else 1.0 for s in parent_scale)  # type: ignore[return-value]


def build_body(parent: Entity) -> Entity:
    """在 parent 下搭出整套外观，返回 rig。

    `config.PLAYER_MODEL` 填了路径就用自制模型，否则用下面那张零件表拼。
    两种方式共用同一个 rig —— rig 负责把宿主的非等比缩放抵消掉，
    所以自制模型也必须是「1 单位 = 1 米、原点在身体中心」。

    拼装模式下 36 个零件会烘焙成**一个** mesh（`core/mesh_builder`），
    挂在 rig 下。36 个独立 Entity 就是 36 次 draw call，而它们加起来
    只有 864 个顶点 —— 合并之后的画面和合并前逐像素一致。

    rig 上额外挂两个属性给测试/调试用：
    `rig.shell`（合并后的实体，`PLAYER_MODEL` 模式下是 None）、
    `rig.parts`（零件数据，不是实体）。
    """
    rig = Entity(
        parent=parent,
        scale=unit_scale((parent.scale_x, parent.scale_y, parent.scale_z)),
        name="player_body",
    )

    if config.PLAYER_MODEL:
        # 模型路径**不能**包 resource_path：ursina 的 load_model 只会在
        # asset_folder 下 glob 相对名，绝对路径一律找不到（只打一行 warning）。
        # asset_folder 由 main.anchor_asset_folder() 锚到项目根 / _MEIPASS。
        Entity(parent=rig, model=config.PLAYER_MODEL,
               scale=config.PLAYER_MODEL_SCALE, name="player_model")
        rig.shell = None
        rig.parts = []
        return rig

    parts = [
        mesh_builder.BoxPart(name, size, position, PALETTE[key], emissive=key in GLOW_KEYS)
        for name, size, position, key in PARTS
    ]
    shell = Entity(
        parent=rig,
        model=mesh_builder.box_batch([part.as_box() for part in parts], "player_shell"),
        name="player_shell",
    )
    # 发光件（面罩/领口灯）靠顶点色 alpha=1 跳过光照，不再需要"不挂着色器"。
    lighting.apply_vcolor_lit(shell)

    rig.shell = shell
    rig.parts = parts
    return rig
