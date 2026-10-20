"""脚下的接触阴影（一块贴地的小圆片）。

为什么需要它：没有阴影时角色是"浮"在地面上的 —— 第三人称镜头从后上方看下去，
角色和地板之间没有任何过渡，距离感全靠猜。一个 0.5 米的半透明黑圆片就能解决，
成本是每帧一次 alpha 混合，比真阴影（每帧渲一张阴影贴图）便宜两个数量级。

两个必须处理的细节：
1. **圆片要贴着地面**，但**不能正好在 y=0**，否则和地面 z-fighting，
   表现为圆片一闪一闪。所以抬 LIFT 米。
2. 宿主是**非等比缩放**（玩家 0.7×1.8×0.7，敌人每种体型都不一样）。
   圆片转过 90°（`rotation_x=90`）才贴地，而父级缩放是在子节点旋转**之后**
   才作用的，所以：圆片的局部 x 落在世界 x 上、吃宿主 x 缩放；
   局部 y 转到世界 z 上、吃宿主 **z** 缩放。
   拿 y 缩放去补偿第二个轴是错的（那是 1.8，会和 x 的 0.7 差 2.6 倍，
   影子变成一个压扁的椭圆）。换算见 `shadow_scale`。
"""
from __future__ import annotations

from ursina import Circle, Entity, color

# 离地高度（米）。0.012 在 720p、相机 6 米外完全不会有可见的悬空感，
# 又足够拉开深度差，不会和地面闪烁。
LIFT = 0.012


def shadow_scale(parent_scale_x: float, parent_scale_z: float,
                 radius: float) -> tuple[float, float]:
    """把「世界半径 radius 米」换算成 Circle 的两个局部缩放。

    Circle 的半径固定 0.5（直径 1 单位），所以局部缩放数值上就是世界直径。
    转过 90° 之后：局部 x 仍是世界 x，局部 y 变成世界 z。
    """
    sx = abs(parent_scale_x)
    sz = abs(parent_scale_z)
    if sx < 1e-6:
        sx = 1.0
    if sz < 1e-6:
        sz = 1.0
    return (radius * 2.0) / sx, (radius * 2.0) / sz


def shadow_local_y(parent_scale_y: float) -> float:
    """圆片在宿主坐标系里的 y。

    约定：宿主的原点是**身体中心**，所以 -0.5 就是脚底（世界 y=0）。
    """
    sy = abs(parent_scale_y)
    if sy < 1e-6:
        sy = 1.0
    return -0.5 + LIFT / sy


def attach(parent: Entity, radius: float, alpha: float) -> Entity:
    """在 parent 脚下挂一块阴影。alpha 是 0~1 的不透明度。"""
    byte = max(0, min(255, round(alpha * 255)))
    scale_x, scale_y = shadow_scale(parent.scale_x, parent.scale_z, radius)

    return Entity(
        parent=parent,
        model=Circle(resolution=24),
        color=color.rgba32(0, 0, 0, byte),
        rotation_x=90,            # ngon 默认躺在 XY 平面，转 90° 才贴地
        scale=(scale_x, scale_y, 1),
        position=(0, shadow_local_y(parent.scale_y), 0),
        name="ground_shadow",
    )
