"""感知：视锥 + 遮挡 + 8Hz 节流。

**这一层必须是纯函数**。原因：视锥角度、角度回绕（±180）、
「先廉价粗筛再打射线」的顺序，全都是容易写反又难在画面上看出来的东西。
做成不依赖引擎的函数，就能用 pytest 把每条边界钉死。

约定（全项目一致）：`rotation_y = 0` 时前方是 +Z，所以朝向向量是
`(sin yaw, cos yaw)` —— 和 `player_control.move_direction`、`tracer_angles`
用的是同一套，别在这里另立一套。
"""
from __future__ import annotations

import math
from typing import Callable

# 浮点比较的容差：视锥边界正好等于半角时应当算「看得见」，
# 不留容差的话 60.0000001 与 60.0 的差别会让边界行为随机。
_ANGLE_EPS = 1e-6


def wrap_deg(angle: float) -> float:
    """把角度折回 (-180, 180]。敌人转身、瞄准都要用，直接比较会绕远路。"""
    angle = math.fmod(angle, 360.0)
    if angle > 180.0:
        angle -= 360.0
    elif angle <= -180.0:
        angle += 360.0
    return angle


def yaw_delta(current: float, target: float) -> float:
    """从 current 转到 target 的最短角度差（度，带符号）。"""
    return wrap_deg(target - current)


def turn_toward(current: float, target: float, max_step: float) -> float:
    """每帧最多转 max_step 度，避免敌人瞬间甩头。

    max_step <= 0 时直接对准（测试里常用）。
    """
    delta = yaw_delta(current, target)
    if max_step <= 0.0:
        return wrap_deg(target)
    if abs(delta) <= max_step:
        return wrap_deg(target)
    return wrap_deg(current + math.copysign(max_step, delta))


def yaw_to(dx: float, dz: float) -> float:
    """从「相对位移」求朝向角。与 `move_direction` / `tracer_angles` 同一约定。"""
    return math.degrees(math.atan2(dx, dz))


def within_view_cone(dx: float, dz: float, yaw: float, view_range: float,
                     half_angle: float) -> bool:
    """廉价粗筛：距离 + 夹角。**不打射线**。

    用平方距离比较，省掉一次 sqrt —— 30 个敌人 × 8Hz 也无所谓，
    但这是全项目统一的做法，避免以后再出现一个「每帧 sqrt 30 次」的热点。
    """
    d2 = dx * dx + dz * dz
    if d2 > view_range * view_range:
        return False
    # 贴身时不看夹角：玩家贴到背后也该被发现，否则会有「站在脸上它还发呆」。
    if d2 < 1.0:
        return True
    return abs(yaw_delta(yaw, yaw_to(dx, dz))) <= half_angle + _ANGLE_EPS


def can_see(dx: float, dz: float, yaw: float, view_range: float, half_angle: float,
            blocked: Callable[[], bool] | None = None) -> bool:
    """看得见 = 在视锥内 **且** 没有遮挡。

    `blocked` 是一个「打射线判断是否被挡」的回调，只在前两步都通过后才调用。
    顺序很重要：反过来就是每帧每个敌人一次引擎射线，性能直接塌。
    """
    if not within_view_cone(dx, dz, yaw, view_range, half_angle):
        return False
    if blocked is not None and blocked():
        return False
    return True


class PerceiveThrottle:
    """固定频率闸门。

    首帧就要放行：否则进入 PLAYING 的头 1/8 秒里所有敌人都是瞎的，
    看起来像「一开始不动，过一下突然全体转向」。
    """

    def __init__(self, hz: float) -> None:
        self.hz = max(0.1, hz)
        self.interval = 1.0 / self.hz
        self._acc = self.interval

    def ready(self, dt: float) -> bool:
        self._acc += dt
        if self._acc + 1e-9 < self.interval:
            return False
        # 用 -= 而不是置零：低帧率下不会把节流频率拖得更低。
        self._acc -= self.interval
        # 一次卡顿几十帧时不要把欠账一直攒着，最多补一次。
        if self._acc > self.interval:
            self._acc = self.interval
        return True

    def reset(self) -> None:
        self._acc = self.interval
