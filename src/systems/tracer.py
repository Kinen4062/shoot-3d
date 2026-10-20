"""弹道拖尾（纯视觉）。

伤害是 hitscan 瞬时结算的，这条线只负责「看得见弹道」：
0.05 秒内从枪口画到命中点，然后归还池子。**不挂 collider、不参与判定**。
"""
from __future__ import annotations

import math

from ursina import Entity, color

from src.core import config
from src.core.pool import EntityPool


def tracer_angles(delta) -> tuple[float, float]:
    """把「枪口 → 命中点」的位移换算成实体旋转 (pitch, yaw)，单位度。

    约定与相机一致：rotation_y = 0 时实体 +Z 朝前，rotation_x 为正表示 +Z 向下压。
    这里手算而不用 `look_at`：Panda3D 的 look_at 是对着 +Y 的，
    Ursina 的 +Z 约定要靠 axis 参数绕一圈，容易反，而且没法写单测。
    """
    horiz = math.hypot(delta.x, delta.z)
    if horiz < 1e-6:
        return (-90.0 if delta.y > 0 else 90.0), 0.0
    yaw = math.degrees(math.atan2(delta.x, delta.z))
    pitch = -math.degrees(math.atan2(delta.y, horiz))
    return pitch, yaw


class TracerSystem:
    def __init__(self) -> None:
        self.pool = EntityPool(self._make, config.TRACER_POOL_SIZE)
        self._active: list[list] = []      # [[entity, 剩余寿命], ...]

    @staticmethod
    def _make() -> Entity:
        return Entity(
            model="cube",
            color=color.rgb32(255, 226, 150),
            scale=(config.TRACER_WIDTH, config.TRACER_WIDTH, 1),
        )

    def spawn(self, start, end) -> None:
        delta = end - start
        length = delta.length()
        if length < 1e-4:
            return

        tracer = self.pool.acquire()
        if tracer is None:                 # 池空就这一发没有拖尾，不扩容
            return

        pitch, yaw = tracer_angles(delta)
        tracer.position = start + delta * 0.5
        tracer.rotation = (pitch, yaw, 0)
        tracer.scale = (config.TRACER_WIDTH, config.TRACER_WIDTH, length)
        self._active.append([tracer, config.TRACER_LIFE])

    def clear(self) -> None:
        """重开一局：把还在飞的拖尾全部归还。不归还的话它们会挂在新局画面里。"""
        for item in self._active:
            self.pool.release(item[0])
        self._active.clear()

    def update(self, dt: float) -> None:
        if not self._active:
            return
        still_alive = []
        for item in self._active:
            item[1] -= dt
            if item[1] > 0:
                still_alive.append(item)
            else:
                self.pool.release(item[0])
        self._active = still_alive
