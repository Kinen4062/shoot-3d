"""波次提示：屏幕中上方短暂显示 `WAVE 3`。

M3 的玩法验收需要「玩家能看出波次在推进」，正式的 HUD 在 M5，
这里先放一个最小可用的提示条，避免验证时只能靠控制台输出。

**为什么要认识状态**：横幅是按真实时间淡出的（1.8 秒显示 + 0.6 秒淡出）。
第 30 波清完时 `WAVE CLEARED` 刚亮起来，画面同一帧就切到了通关面板 ——
不主动收掉的话，它会压在面板上方整整两秒，和面板标题的 `RUN CLEARED`
并排出现两个"cleared"，看起来像重复报错。
"""
from __future__ import annotations

from ursina import Text, color, window

from src.core.game_state import State

SHOW_TIME = 1.8
FADE_TIME = 0.6


class WaveBanner:
    def __init__(self, state=None) -> None:
        self.state = state
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
        # 只在"看得见 3D 画面"的两个状态里显示。注意是**主动清掉**而不是暂停
        # 倒计时：暂停时把横幅留着没问题（暂停面板会盖住），
        # 但通关 / 死亡时它会压在结算面板上。
        if self.state is not None and not self.state.in_state(State.PLAYING, State.PAUSED):
            if self.text.text:
                self.text.text = ""
            self._left = 0.0
            return
        if self._left <= 0.0:
            return
        self._left -= dt
        if self._left <= 0.0:
            self.text.text = ""
            return
        if self._left < FADE_TIME:
            self.text.color = color.rgba(*self._rgb, self._left / FADE_TIME)
