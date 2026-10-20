"""帧率显示（规划书 §M6「帧率检查，目标 60，低于 40 要优化」）。

**为什么要自己挂一个 Text**：ursina 8.3 里没有 `Application.fps_counter`
（规划书 §M6 的注记里写过）。`window.fps_counter` 存在，但它挂在 `editor_ui`
下面，而那个父节点随 `development_mode` 一起关掉了 —— 打开它也不会显示。
所以只有一个可靠办法：自己读 `base.clock.getAverageFrameRate()` 再画出来。

**为什么按颜色分档**：数字本身要"读"才知道好坏；颜色是余光可见的。
`>55` 绿、`40~55` 黄、`<40` 红 —— 和血条同一套编码方式。

**为什么 0.5 秒刷一次**：`getAverageFrameRate()` 是滑动平均，问得再勤也不会
更准，反而每帧重建文字纹理会拖慢帧率，把仪表本身变成噪声源。
"""
from __future__ import annotations

from ursina import Text, application, camera, window

from src.core import config
from src.core.game_state import State
from src.ui.widgets import RGB_MUTED, rgba32

RGB_FPS_GOOD = (140, 235, 170)
RGB_FPS_WARN = (245, 205, 105)
RGB_FPS_BAD = (235, 105, 100)


def fps_rgb(fps: float) -> tuple[int, int, int]:
    """帧率 → 颜色。抽成纯函数，好钉边界（40 / 55 两头都容易写反）。"""
    if fps <= 0.0:
        return RGB_MUTED
    if fps >= config.FPS_GOOD:
        return RGB_FPS_GOOD
    if fps >= config.FPS_MIN_OK:
        return RGB_FPS_WARN
    return RGB_FPS_BAD


def format_fps(fps: float) -> str:
    """读数 → 文本。取不到时显示 `FPS --`，不要显示 `FPS 0`（那看起来像卡死）。"""
    if fps <= 0.0:
        return "FPS --"
    return f"FPS {fps:.0f}"


def read_average_fps() -> float:
    """从 Panda 的时钟读平均帧率。无窗口环境返回 0（调用方显示 `--`）。"""
    base = getattr(application, "base", None)
    if base is None:
        return 0.0
    try:
        return float(base.clock.getAverageFrameRate())
    except Exception:              # noqa: BLE001 - 仪表坏了不能影响游戏
        return 0.0


class FpsCounter:
    """右上角的帧率读数。位置在 `BEST` 下面，不和对局 HUD 抢地方。"""

    def __init__(self, state, poll=None) -> None:
        self.state = state
        self._poll = poll or read_average_fps
        self._acc = config.FPS_REFRESH          # 首帧就刷新一次

        right = window.top_right
        self.text = Text(
            text=format_fps(self._poll()),
            parent=camera.ui,
            origin=(1, 1),
            position=(right.x - 0.02, right.y - 0.098),
            scale=0.85,
            color=rgba32(RGB_MUTED),
            visible=False,
        )

    def update(self, dt: float) -> None:
        visible = config.FPS_SHOW and self.state.in_state(State.PLAYING, State.PAUSED)
        if self.text.visible != visible:
            # 和别处一致：显隐只用 visible（enabled 在 ursina 8 里不隐藏节点）。
            self.text.visible = visible
        if not visible:
            return

        self._acc += dt
        if self._acc < config.FPS_REFRESH:
            return
        self._acc = 0.0
        fps = self._poll()
        self.text.text = format_fps(fps)
        self.text.color = rgba32(fps_rgb(fps))

    def reset(self) -> None:
        self._acc = config.FPS_REFRESH
        self.text.text = format_fps(self._poll())
        self.text.color = rgba32(fps_rgb(self._poll()))
        self.text.visible = False
