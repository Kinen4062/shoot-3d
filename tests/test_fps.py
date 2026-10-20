"""帧率显示（`src/ui/fps_counter.py`）的取值与分档。

仪表本身不该成为噪声源：数字取错了（比如永远 0）比不显示更糟，
因为你会以为"优化没效果"。
"""
from __future__ import annotations

import pytest

from src.core import config
from src.ui.fps_counter import (
    RGB_FPS_BAD,
    RGB_FPS_GOOD,
    RGB_FPS_WARN,
    format_fps,
    fps_rgb,
)


def test_format_fps_rounds_to_integer():
    assert format_fps(59.6) == "FPS 60"
    assert format_fps(40.4) == "FPS 40"


def test_format_fps_hides_zero_as_dashes():
    """取不到帧率时显示 `FPS 0` 会让人以为卡死，必须显示 `--`。"""
    assert format_fps(0.0) == "FPS --"
    assert format_fps(-1.0) == "FPS --"


def test_color_thresholds():
    assert fps_rgb(60.0) == RGB_FPS_GOOD
    assert fps_rgb(config.FPS_GOOD) == RGB_FPS_GOOD
    assert fps_rgb(config.FPS_GOOD - 0.1) == RGB_FPS_WARN
    assert fps_rgb(config.FPS_MIN_OK) == RGB_FPS_WARN
    assert fps_rgb(config.FPS_MIN_OK - 0.1) == RGB_FPS_BAD
    assert fps_rgb(0.0) == (150, 160, 175)      # 未知 → 灰色，不是红色


def test_min_ok_is_below_target():
    """规划书的目标是 60、红线是 40。两个常量写反了会导致全绿或全红。"""
    assert config.FPS_MIN_OK < config.FPS_GOOD < 60, \
        "FPS_GOOD 应低于目标 60（60 是理想值不是及格线）"


@pytest.mark.parametrize("fps, expected", [
    (144.0, RGB_FPS_GOOD),
    (59.9, RGB_FPS_GOOD),
    (45.0, RGB_FPS_WARN),
    (39.9, RGB_FPS_BAD),
    (12.0, RGB_FPS_BAD),
])
def test_typical_readings(fps, expected):
    assert fps_rgb(fps) == expected
