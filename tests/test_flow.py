"""界面流转 + 重开入口（`RunSession.start_run` / `sync_ui` / `summary`）。

M5 的验收标准是「菜单开始 → 打完一波 → 暂停 → 死亡 → 重开 → 返回菜单，
全程无崩溃、无逻辑错乱」。这条链路里最容易错乱的是**重开被触发两次**
和**界面没跟着状态切换**，两者都不会报错：

- 重开两次：波次瞬间推进两格、监听器注册两遍（伤害翻倍）；
- 界面不切换：主菜单叠在游戏上面、或者死了却看不到结算面板。

所以这里用假面板把「什么时候显示什么、重开发生几次」钉死，
不需要窗口，也不需要 urisna 的按钮。
"""
from __future__ import annotations

import pytest

from src.core import event_bus
from src.core.game_data import GameData, set_data
from src.core.game_state import State
from src.core.session import RunSession
from src.systems.score import ScoreSystem


class FakeState:
    """只实现 session 用到的那两个方法，并把 set() 变成真事件。"""

    def __init__(self) -> None:
        self.state = State.MENU

    def in_state(self, *states) -> bool:
        return self.state in states

    def set(self, new) -> None:
        if new is self.state:
            return
        old = self.state
        self.state = new
        event_bus.emit("state_changed", old=old, new=new)


class FakePlayer:
    def __init__(self) -> None:
        self.alive = True
        self.hp = 100.0
        self.resets = 0

    def reset(self) -> None:
        self.alive = True
        self.hp = 100.0
        self.resets += 1


class Stub:
    def __init__(self, name: str = "stub") -> None:
        self.name = name
        self.calls: list[str] = []

    def __getattr__(self, item):
        def _record(*_args, **_kwargs):
            self.calls.append(item)
        return _record


class FakePanel:
    def __init__(self) -> None:
        self.visible = False
        self.shows: list = []

    def show(self, *args) -> None:
        self.visible = True
        self.shows.append(args)

    def hide(self) -> None:
        self.visible = False

    def reset(self) -> None:
        self.visible = False


class FakeBestPanel(FakePanel):
    def __init__(self) -> None:
        super().__init__()
        self.best_texts: list[str] = []

    def refresh_best(self, data) -> None:
        self.best_texts.append(data.best_score if data is not None else 0)


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    from src.core import config

    monkeypatch.setattr(config, "SAVE_FILE", str(tmp_path / "save.json"))
    event_bus.clear_listeners()
    set_data(None)
    yield
    event_bus.clear_listeners()
    set_data(None)


def build(player=None, data=None):
    state = FakeState()
    player = player or FakePlayer()
    data = data or GameData()
    score = ScoreSystem(state, player=player, data=data)
    panels = {
        "main_menu": FakeBestPanel(),
        "pause_menu": FakePanel(),
        "game_over": FakePanel(),
    }
    session = RunSession(
        state=state,
        player=player,
        controller=Stub("controller"),
        rig=Stub("rig"),
        weapon=Stub("weapon"),
        enemies=Stub("enemies"),
        projectiles=Stub("projectiles"),
        pickups=Stub("pickups"),
        waves=Stub("waves"),
        score=score,
        tracer=Stub("tracer"),
        overlay=Stub("overlay"),
        crosshair=Stub("crosshair"),
        damage_flash=Stub("flash"),
        wave_banner=Stub("banner"),
        hud=Stub("hud"),
        combat_hud=Stub("combat_hud"),
        main_menu=panels["main_menu"],
        pause_menu=panels["pause_menu"],
        game_over=panels["game_over"],
    )
    session.register_listeners()
    return session, state, player, panels


# ---------------------------------------------------------------- 界面切换

def test_sync_ui_follows_state():
    session, state, _player, panels = build()

    session.sync_ui(State.MENU)
    assert panels["main_menu"].visible and not panels["pause_menu"].visible
    assert not panels["game_over"].visible

    session.sync_ui(State.PLAYING)
    assert not any(p.visible for p in panels.values()), "对局中不许有任何面板挡着"

    session.sync_ui(State.PAUSED)
    assert panels["pause_menu"].visible

    session.sync_ui(State.GAME_OVER)
    assert panels["game_over"].visible
    assert not panels["pause_menu"].visible


def test_menu_refreshes_best_on_show():
    session, _state, _player, panels = build()
    session.score.data.best_score = 4321
    session.sync_ui(State.MENU)
    assert panels["main_menu"].best_texts == [4321]


# ---------------------------------------------------------------- 重开入口

def test_start_run_from_menu_resets_once():
    session, state, player, _panels = build()
    assert state.state is State.MENU

    session.start_run()

    assert state.state is State.PLAYING
    assert session.restarts == 1
    assert player.resets == 1, "从菜单开局必须清场，且只清一次"


def test_start_run_after_death_resets_exactly_once():
    """死亡后重开走的是 on_state_changed 那条路，不能再手动清一次。

    清两次的后果：波次直接跳两格、监听器注册两遍（伤害翻倍）。

    注意这里必须真的发一次 `player_died`：状态机只有在**状态变化**时
    才会广播 `state_changed`，如果直接把 alive 改成 False 而状态还停在
    PLAYING，`set(PLAYING)` 是空操作 —— 那不是 bug，是状态机的契约。
    """
    session, state, player, _panels = build()
    session.start_run()                     # 先正常开一局
    assert session.restarts == 1

    player.alive = False
    event_bus.emit("player_died")           # 真实流程：死亡 → GAME_OVER
    assert state.state is State.GAME_OVER

    session.start_run()

    assert state.state is State.PLAYING
    assert session.restarts == 2, "死亡后重开只该多一次"
    assert player.resets == 2


def test_return_to_menu_switches_panel():
    session, state, _player, panels = build()
    session.start_run()
    assert state.state is State.PLAYING

    session.return_to_menu()

    assert state.state is State.MENU
    assert panels["main_menu"].visible
    assert not panels["pause_menu"].visible


def test_request_restart_is_noop_when_playing():
    session, state, _player, _panels = build()
    session.start_run()
    before = session.restarts
    assert session.request_restart() is False
    assert session.restarts == before


# ---------------------------------------------------------------- 结算数据

def test_summary_reports_stats_and_new_record():
    session, _state, _player, _panels = build()
    data = session.score.data
    data.best_score = 500
    session._best_at_start = 500

    data.score = 900
    data.kills = 7
    data.time_alive = 95.0
    data.wave = 6

    summary = session.summary()
    assert summary.score == 900
    assert summary.kills == 7
    assert summary.wave == 6
    assert summary.seconds == pytest.approx(95.0)
    assert summary.best == 900, "最高分取 max(存档, 本局)"
    assert summary.new_best is True


def test_summary_is_not_a_record_when_lower():
    session, _state, _player, _panels = build()
    session.score.data.best_score = 5000
    session._best_at_start = 5000
    session.score.data.score = 1200

    summary = session.summary()
    assert summary.new_best is False
    assert summary.best == 5000


def test_reset_run_rebases_new_record_threshold():
    """重开之后必须重新记一次"开局最高分"，否则上一局的成绩会一直算新纪录。"""
    session, _state, _player, _panels = build()
    session._best_at_start = 0
    session.score.data.best_score = 3000

    session.start_run()

    assert session._best_at_start == 3000
    session.score.data.score = 1000
    assert session.summary().new_best is False
