"""波次提示：屏幕中上方短暂显示 `WAVE 3`。

M3 的玩法验收需要「玩家能看出波次在推进」，正式的 HUD 在 M5，
这里先放一个最小可用的提示条，避免验证时只能靠控制台输出。
"""
from __future__ import annotations

from ursina import Text, color, window

SHOW_TIME = 1.8
FADE_TIME = 0.6


class WaveBanner:
    def __init__(self) -> None:
        self.text = Text(
            text="",
            origin=(0, 0),
            position=(0, window.top.y - 0.10),
            scale=2.2,
            color=color.white,
        )
        self._left = 0.0
        # 只存 rgb（0~1），alpha 在每帧按剩余时间重算。
        self._rgb = (1.0, 1.0, 1.0)
        # 订阅由 core/session.py 统一注册（重开会 clear_listeners）。

    def on_wave_started(self, wave: int = 0, **_kwargs) -> None:
        self._show(f"WAVE {wave}", (1.0, 1.0, 1.0))

    def on_wave_cleared(self, **_kwargs) -> None:
        self._show("WAVE CLEARED", (0.5, 0.85, 1.0))

    def reset(self) -> None:
        self._left = 0.0
        self.text.text = ""

    def _show(self, message: str, rgb: tuple[float, float, float]) -> None:
        self.text.text = message
        self._rgb = rgb
        self.text.color = color.rgba(*rgb, 1.0)
        self._left = SHOW_TIME

    def update(self, dt: float) -> None:
        if self._left <= 0.0:
            return
        self._left -= dt
        if self._left <= 0.0:
            self.text.text = ""
            return
        if self._left < FADE_TIME:
            self.text.color = color.rgba(*self._rgb, self._left / FADE_TIME)
