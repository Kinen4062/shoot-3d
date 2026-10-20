"""准星：四条短线 + 开火扩散 + 命中标记。

放在 `camera.ui` 下：`camera.ui` 的父级是独立的 ui_camera（`ursina/camera.py:81`），
不会跟着第三人称相机一起平移，所以 HUD 不受相机碰撞拉近影响。
"""
from __future__ import annotations

from ursina import Entity, camera, color, lerp_exponential_decay

from src.core import config
from src.core.game_state import State


class Crosshair:
    def __init__(self, state) -> None:
        self.state = state
        self.root = Entity(parent=camera.ui, name="crosshair")

        w, h = config.CROSSHAIR_BAR
        self._up = Entity(parent=self.root, model="quad", color=color.white, scale=(w, h))
        self._down = Entity(parent=self.root, model="quad", color=color.white, scale=(w, h))
        self._left = Entity(parent=self.root, model="quad", color=color.white, scale=(h, w))
        self._right = Entity(parent=self.root, model="quad", color=color.white, scale=(h, w))

        mark = color.rgb32(255, 120, 110)
        self._marks = [
            Entity(parent=self.root, model="quad", color=mark, scale=(0.014, 0.0012),
                   rotation_z=45, visible=False),
            Entity(parent=self.root, model="quad", color=mark, scale=(0.014, 0.0012),
                   rotation_z=-45, visible=False),
        ]

        self._bloom = 0.0
        self._hitmark = 0.0
        # 开局就是 MENU，不显式关掉的话第一帧之前会在菜单上看到一个准星。
        self.root.visible = False
        self._layout(0.0)

    def update(self, dt: float) -> None:
        self.root.visible = self.state.in_state(State.PLAYING)

        if self._bloom > 0.0:
            self._bloom = lerp_exponential_decay(self._bloom, 0.0, dt, config.CROSSHAIR_BLOOM_DECAY)
            self._layout(self._bloom)

        if self._hitmark > 0.0:
            self._hitmark -= dt
            if self._hitmark <= 0.0:
                for m in self._marks:
                    m.visible = False

    def _layout(self, bloom: float) -> None:
        gap = config.CROSSHAIR_BASE_GAP + bloom * config.CROSSHAIR_BLOOM_GAP
        _, h = config.CROSSHAIR_BAR
        self._up.position = (0, gap + h * 0.5)
        self._down.position = (0, -(gap + h * 0.5))
        self._left.position = (-(gap + h * 0.5), 0)
        self._right.position = (gap + h * 0.5, 0)

    # ---- 事件 ----
    def on_shot(self) -> None:
        self._bloom = min(1.0, self._bloom + config.CROSSHAIR_BLOOM_PER_SHOT)

    def on_hit(self) -> None:
        self._hitmark = config.CROSSHAIR_HITMARK_TIME
        for m in self._marks:
            m.visible = True

    def reset(self) -> None:
        """重开一局：扩散收拢、命中标记熄灭。

        不重置 `_bloom` 的话，玩家死亡瞬间连射的准星会保持张开状态进入新一局，
        看起来像"准星卡住了"。
        """
        self._bloom = 0.0
        self._hitmark = 0.0
        self.root.visible = False
        self._layout(0.0)
        for m in self._marks:
            m.visible = False
