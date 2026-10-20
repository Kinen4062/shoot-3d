"""结算数据与时间格式（`src/ui/menus.py` 里的纯函数）。

界面本身没法在无窗口环境里测（要 `camera.ui`），但**结算数字错了比界面丑严重得多**：
玩家死了看到 `TIME 0:00`、或者打了新纪录却标 `NEW RECORD` 没出现，
会直接怀疑存档坏了。

所以把这两件事抽成纯函数单独钉住。
"""
from __future__ import annotations

import pytest

from src.ui.menus import build_summary, format_time


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0.0, "0:00"),
        (9.9, "0:09"),
        (59.0, "0:59"),
        (60.0, "1:00"),
        (65.0, "1:05"),
        (3599.0, "59:59"),
        (-5.0, "0:00"),      # 负数不能显示成 -1:-5
    ],
)
def test_format_time(seconds, expected):
    assert format_time(seconds) == expected


def test_summary_flags_new_record():
    summary = build_summary(score=1200, kills=4, seconds=61.0, wave=3,
                            best=1200, best_at_start=1000)
    assert summary.new_best is True
    assert summary.best == 1200


def test_summary_without_record_keeps_old_best():
    summary = build_summary(score=800, kills=2, seconds=30.0, wave=2,
                            best=1500, best_at_start=1500)
    assert summary.new_best is False
    assert summary.best == 1500


def test_summary_never_regresses_best():
    """存档里的最高分如果比本局还低（换了台机器 / 删了存档），取本局更大的那个。"""
    summary = build_summary(score=2000, kills=9, seconds=300.0, wave=7,
                            best=100, best_at_start=100)
    assert summary.best == 2000
    assert summary.new_best is True


def test_summary_zero_score_is_not_a_record():
    summary = build_summary(score=0, kills=0, seconds=1.0, wave=1,
                            best=0, best_at_start=0)
    assert summary.new_best is False
