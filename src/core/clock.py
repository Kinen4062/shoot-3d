"""GameClock —— 全项目唯一的 dt 出口。

为什么不用 ursina 的 application.paused 做暂停：
引擎的输入分发在 ursina/main.py:278 是 `if not application.paused:`，
它会把全局 input() 回调一起关掉 —— 用了它，ESC 就再也收不到，
游戏卡在暂停态只能强杀进程。

所以暂停走自己的时钟：只影响逻辑 dt，不影响输入分发。
"""
from __future__ import annotations

from ursina import time

from . import config


class GameClock:
    def __init__(self) -> None:
        self.paused = False
        self.time_scale = 1.0
        self.real_dt = 0.0   # 未缩放的真实帧间隔，只给 UI 淡入淡出这类用
        self.elapsed = 0.0   # 未暂停的累计时长，用于统计「存活时间」

    def tick(self) -> float:
        """每帧调用一次，返回本帧逻辑步长（秒）。暂停时返回 0。"""
        raw = min(time.dt, config.MAX_FRAME_DT)
        self.real_dt = raw
        if self.paused:
            return 0.0

        dt = raw * self.time_scale
        self.elapsed += dt
        return dt

    @property
    def running(self) -> bool:
        return not self.paused and self.time_scale > 0.0
