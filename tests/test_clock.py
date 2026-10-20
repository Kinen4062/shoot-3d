"""M0 单元测试：GameClock。

这些测试不启动窗口，只验证与引擎时间解耦后的逻辑。
"""
import pytest
from ursina import time

from src.core import config
from src.core.clock import GameClock


@pytest.fixture(autouse=True)
def reset_clock():
    yield
    time.dt = 0.0


def test_normal_tick(monkeypatch):
    monkeypatch.setattr(time, "dt", 0.016)
    clock = GameClock()
    assert clock.tick() == pytest.approx(0.016)
    assert clock.real_dt == pytest.approx(0.016)


def test_paused_returns_zero(monkeypatch):
    monkeypatch.setattr(time, "dt", 0.016)
    clock = GameClock()
    clock.paused = True
    assert clock.tick() == 0.0
    # real_dt 仍然更新：界面靠它继续刷新
    assert clock.real_dt == pytest.approx(0.016)


def test_elapsed_does_not_advance_when_paused(monkeypatch):
    monkeypatch.setattr(time, "dt", 0.1)
    clock = GameClock()
    clock.tick()
    before = clock.elapsed
    clock.paused = True
    clock.tick()
    assert clock.elapsed == before


def test_frame_dt_is_clamped(monkeypatch):
    """切窗口回来时 time.dt 会很大，必须夹到 MAX_FRAME_DT，否则会穿墙。"""
    monkeypatch.setattr(time, "dt", 3.0)
    clock = GameClock()
    assert clock.tick() == pytest.approx(config.MAX_FRAME_DT)


def test_time_scale(monkeypatch):
    """慢动作：先夹 MAX_FRAME_DT，再乘 time_scale。"""
    monkeypatch.setattr(time, "dt", 0.02)
    clock = GameClock()
    clock.time_scale = 0.3
    assert clock.tick() == pytest.approx(0.006)


def test_clamp_happens_before_time_scale(monkeypatch):
    monkeypatch.setattr(time, "dt", 0.1)   # 会被夹到 0.05
    clock = GameClock()
    clock.time_scale = 0.3
    assert clock.tick() == pytest.approx(config.MAX_FRAME_DT * 0.3)


def test_running_flag():
    clock = GameClock()
    assert clock.running is True
    clock.paused = True
    assert clock.running is False
