"""屏幕中心射线：准星圆心对应的那条世界空间射线。

**结论：屏幕中心方向就是相机的 `forward`（节点局部 +Z）。**

这个项目里「+Z 朝前」是全局约定，四处独立实现都依赖它，且都已被实机验证
（相机挂在 pivot 后方 -Z 上、玩家按 (sin yaw, 0, cos yaw) 跑动、
枪口按 rotation_y 摆向、拖尾按 atan2(dx, dz) 算偏航）。
真正的坑不在这里，而在 `raycast` —— 见 `src/core/ray.py`。

曾试过用 lens 反投影（`CollisionRay.set_from_lens`）来"保证共线"，
实测这条路不可用：同一个 `PerspectiveLens`，`extrude((0,0))` 给出的中心方向是
+Z，而 `set_from_lens` 给出的是 +Y，两者差 90°，且会随调用环境变化。
拿它做弹道只会在某些机器上整体偏 90°，所以这里回到唯一自洽的定义。

`log_geometry_once()` 在首次射击时把起点与方向打一行，方便现场复核。
"""
from __future__ import annotations

from ursina import Vec3, camera

from src.core.log import get_logger

log = get_logger("aim")

_logged = False


def screen_center_ray() -> tuple[Vec3, Vec3]:
    """返回 (origin, direction)：相机世界位置 + 已归一化的正前方。"""
    origin = Vec3(camera.world_position)
    direction = Vec3(camera.forward).normalized()
    return origin, direction


def log_geometry_once() -> None:
    """第一次射击时打一行几何，方便对着现场日志复核。"""
    global _logged
    if _logged:
        return
    _logged = True

    origin, direction = screen_center_ray()
    log.debug(
        f"[aim] origin={tuple(round(v, 3) for v in origin)}  "
        f"dir={tuple(round(v, 4) for v in direction)}"
    )
