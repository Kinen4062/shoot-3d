"""失焦自动暂停（`src/systems/focus_watch.py`）。

这个功能最怕两件事：

1. **一启动就暂停** —— 把"属性没设置"当成"失焦"。无头环境里 `getForeground()`
   恒为 False，所以三态判定（True / False / None）必须是硬约束。
2. **在菜单/结算里乱暂停** —— 失焦时状态不是 PLAYING 就什么都不做，
   否则界面会从"结算"跳成"暂停"，玩家回来看到的是错的面板。

两者都不需要真实窗口：注入一个假的焦点序列即可。
"""
from __future__ import annotations

from src.core.game_state import State
from src.systems.focus_watch import FocusWatch


class FakeState:
    """最小状态机：记录 set() 调用，行为足够 FocusWatch 用。"""

    def __init__(self, state: State) -> None:
        self.state = state
        self.sets: list[State] = []

    def in_state(self, *states: State) -> bool:
        return self.state in states

    def set(self, new: State) -> None:
        self.sets.append(new)
        self.state = new


class FakePoll:
    """按脚本给出焦点状态，用完之后一直重复最后一个值。"""

    def __init__(self, *values) -> None:
        self.values = list(values)
        self.calls = 0

    def __call__(self):
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return value


def test_losing_focus_pauses_gameplay():
    state = FakeState(State.PLAYING)
    watch = FocusWatch(state, poll=FakePoll(True, False))
    watch.update(0.016)          # 前台
    watch.update(0.016)          # 失焦 → 暂停
    assert state.sets == [State.PAUSED]
    assert watch.auto_pauses == 1


def test_only_fires_on_transition():
    """一直失焦不能每帧都调一次 set（会不断重置面板动画）。"""
    state = FakeState(State.PLAYING)
    watch = FocusWatch(state, poll=FakePoll(True, False, False, False))
    for _ in range(4):
        watch.update(0.016)
    assert state.sets == [State.PAUSED]
    assert watch.auto_pauses == 1


def test_does_not_pause_menu_or_game_over():
    """菜单 / 结算失焦不该变成 PAUSED —— 玩家回来会看到错的面板。"""
    for st in (State.MENU, State.GAME_OVER, State.PAUSED):
        state = FakeState(st)
        watch = FocusWatch(state, poll=FakePoll(True, False))
        watch.update(0.016)
        watch.update(0.016)
        assert state.sets == [], f"{st} 下不该暂停"


def test_unknown_state_never_pauses():
    """无头环境：焦点状态恒未知，绝不能暂停（否则游戏一启动就卡在暂停）。

    这条是拿"把 None 当 False"的写法试出来的：那样写一切正常场景都过，
    只有真正没窗口时才会一启动就暂停 —— 探针里根本看不出来。
    """
    state = FakeState(State.PLAYING)
    watch = FocusWatch(state, poll=FakePoll(None, None, None))
    for _ in range(10):
        watch.update(0.016)
    assert state.sets == []
    assert watch.unknown == 10


def test_regaining_focus_does_not_auto_resume():
    """回来必须保持暂停：自动恢复等于"你一回来就继续挨打"。"""
    state = FakeState(State.PLAYING)
    watch = FocusWatch(state, poll=FakePoll(True, False, True, True))
    for _ in range(4):
        watch.update(0.016)
    assert state.sets == [State.PAUSED]
    assert state.state is State.PAUSED


def test_pause_again_after_resume_when_refocus_lost():
    """暂停 → 玩家手动 RESUME → 再失焦，必须还能再暂停一次。"""
    state = FakeState(State.PLAYING)
    poll = FakePoll(True, False, True)
    watch = FocusWatch(state, poll=poll)
    watch.update(0.016)          # 前台
    watch.update(0.016)          # 失焦 → 自动暂停
    assert state.state is State.PAUSED

    state.state = State.PLAYING  # 玩家点了 RESUME
    watch.update(0.016)          # 回到前台（这一帧不该有任何动作）
    assert state.sets == [State.PAUSED]

    poll.values.append(False)    # 再切出去
    watch.update(0.016)
    assert watch.auto_pauses == 2
    assert state.sets == [State.PAUSED, State.PAUSED]


def test_reset_clears_stale_focus():
    """重开一局后视作"上一帧在前台"，避免脏状态漏掉下一次失焦。"""
    state = FakeState(State.PLAYING)
    watch = FocusWatch(state, poll=FakePoll(False, False))
    watch.update(0.016)          # 第一帧就失焦（was_focused=True → 暂停）
    assert state.sets == [State.PAUSED]

    state.state = State.PLAYING
    state.sets.clear()
    watch.reset()
    watch.update(0.016)          # 还是失焦，但 reset 后应视作刚回前台
    assert state.sets == [State.PAUSED]
