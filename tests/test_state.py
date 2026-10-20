"""M0 单元测试：状态机与时钟联动。

这里覆盖 M0 的验收条款：
- TAB 能在 4 个状态间循环
- ESC 能在 PLAYING / PAUSED 之间来回（证明没用 application.paused）
- 慢动作只在 GAME_OVER 生效，离开时还原
"""
import pytest

from src.core import event_bus
from src.core.clock import GameClock
from src.core.game_state import GameStateMachine, State


@pytest.fixture(autouse=True)
def clean_bus():
    event_bus.clear_listeners()
    yield
    event_bus.clear_listeners()


def make():
    clock = GameClock()
    return clock, GameStateMachine(clock)


def test_starts_in_menu():
    _, sm = make()
    assert sm.state is State.MENU


def test_debug_cycle_order():
    _, sm = make()
    order = [sm.state]
    for _ in range(4):
        sm.debug_cycle()
        order.append(sm.state)
    assert order == [
        State.MENU,
        State.PLAYING,
        State.PAUSED,
        State.GAME_OVER,
        State.MENU,
    ]


def test_pause_toggles_both_ways():
    clock, sm = make()
    sm.set(State.PLAYING)
    sm.toggle_pause()
    assert sm.state is State.PAUSED
    assert clock.paused is True

    sm.toggle_pause()
    assert sm.state is State.PLAYING
    assert clock.paused is False


def test_pause_ignored_outside_playing():
    """GAME_OVER / MENU 下按 ESC 不能乱切。"""
    _, sm = make()
    sm.set(State.GAME_OVER)
    sm.toggle_pause()
    assert sm.state is State.GAME_OVER

    sm.set(State.MENU)
    sm.toggle_pause()
    assert sm.state is State.MENU


def test_game_over_slow_motion_and_restore():
    clock, sm = make()
    sm.set(State.GAME_OVER)
    assert clock.time_scale == pytest.approx(0.3)
    sm.set(State.MENU)
    assert clock.time_scale == pytest.approx(1.0)


def test_clock_not_paused_after_leaving_paused():
    """PAUSED -> GAME_OVER 时如果忘记恢复 paused，整个游戏会永久静止。"""
    clock, sm = make()
    sm.set(State.PLAYING)
    sm.toggle_pause()
    assert clock.paused is True
    sm.set(State.GAME_OVER)
    assert clock.paused is False


def test_state_changed_event():
    _, sm = make()
    seen = []
    event_bus.on("state_changed", lambda **kw: seen.append((kw["old"], kw["new"])))
    sm.set(State.PLAYING)
    assert seen == [(State.MENU, State.PLAYING)]


def test_set_same_state_is_noop():
    _, sm = make()
    seen = []
    event_bus.on("state_changed", lambda **kw: seen.append(1))
    sm.set(State.MENU)
    assert seen == []
