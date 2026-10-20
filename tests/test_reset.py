"""重开契约（M4 的第三条验收）。

这个文件锁的是 **`RunSession.reset_run()` 的执行顺序**，因为它有一步
几乎不可能靠肉眼发现的问题：

`event_bus.clear_listeners()` 会连**延迟队列**一起清空。
而 `EnemySystem.clear()` 是往延迟队列里 `defer(destroy, enemy)` 的。
如果顺序写成「先清监听器再清敌人」，那些 destroy 会被立刻吞掉 ——
上一局的敌人永远留在场景里，和新一局的敌人叠在一起。
画面上的表现是"重开后怪越来越多、打不死"，极难反推到事件总线上。

所以这里有一条 `test_pending_destroy_is_flushed_before_listener_reset`：
让假敌人的 `clear()` 真的往延迟队列排一个回调，然后断言它执行了。

其余几条锁的是"不翻倍"：
- `register_listeners()` 连调两次，`enemy_died` 只能触发一次计分；
- `reset_run()` 连调三次，订阅数必须还是原来那个数。
监听器翻倍的表现是"伤害翻倍、分数翻倍"，同样是静默错误。
"""
import pytest

from src.core import event_bus
from src.core.game_data import GameData, set_data
from src.core.game_state import State
from src.core.session import RunSession
from src.systems.score import ScoreSystem


class Stub:
    """任何属性都返回一个可调用对象，并把调用名记下来。

    这样不用为 11 个系统各写一个假类；需要精确控制的方法
    直接赋值到实例上（会绕过 `__getattr__`）。
    """

    def __init__(self, name="stub") -> None:
        self.name = name
        self.calls: list[str] = []

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)

        def fn(*_args, **_kwargs):
            self.calls.append(item)
            return 0

        return fn

    def called(self, name: str, times: int = 1) -> bool:
        return self.calls.count(name) == times


class FakePlayer:
    def __init__(self) -> None:
        self.alive = True
        self.resets = 0
        self.shake_hits = 0

    def reset(self) -> None:
        self.resets += 1
        self.alive = True


class FakeState:
    def __init__(self) -> None:
        self.current = State.MENU
        self.set_calls: list[State] = []

    def in_state(self, *states) -> bool:
        return self.current in states

    def set(self, new) -> None:
        self.current = new
        self.set_calls.append(new)


@pytest.fixture(autouse=True)
def clean_bus(tmp_path, monkeypatch):
    """每个用例前后清空事件总线，并把存档指到 tmp。

    `player_died` 会触发 `ScoreSystem.on_player_died` → 写最高分。
    不重定向 `SAVE_FILE` 的话，跑一次测试就会污染用户真实的
    `%APPDATA%\\MiniTPS\\save.json`（局数 +1、写入测试分数）。
    """
    from src.core import config
    monkeypatch.setattr(config, "SAVE_FILE", str(tmp_path / "save.json"))
    event_bus.clear_listeners()
    set_data(None)
    yield
    event_bus.clear_listeners()
    set_data(None)


def build(player=None, state=None, enemies=None):
    """装配一个全部由 Stub 组成的 session，只测流程不测行为。"""
    player = player or FakePlayer()
    state = state or FakeState()
    enemies = enemies or Stub("enemies")
    score = ScoreSystem(state, player=player, data=GameData())
    session = RunSession(
        state=state,
        player=player,
        controller=Stub("controller"),
        rig=Stub("rig"),
        weapon=Stub("weapon"),
        enemies=enemies,
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
    )
    session.register_listeners()
    return session, state, player, enemies


# ---------------------------------------------------------------- 订阅幂等

def test_register_listeners_is_idempotent():
    session, _state, _player, _enemies = build()
    first = session.listener_count
    assert first > 0

    session.register_listeners()
    assert session.listener_count == first

    # 关键断言：注册两次之后，一次击杀只能触发一次计分
    before = session.score.data.kills
    event_bus.emit("enemy_died", enemy=None)
    assert session.score.data.kills == before + 1


def test_reset_run_does_not_duplicate_listeners():
    session, _state, _player, _enemies = build()
    base = session.listener_count

    for _ in range(3):
        session.reset_run()

    assert session.listener_count == base
    session.score.data.kills = 0
    event_bus.emit("enemy_died", enemy=None)
    assert session.score.data.kills == 1


# ---------------------------------------------------------------- reset 覆盖面

def test_reset_run_touches_every_system():
    session, _state, player, enemies = build()
    session.reset_run()

    assert player.resets == 1
    assert enemies.called("clear")
    assert session.projectiles.called("clear")
    assert session.pickups.called("reset")
    assert session.waves.called("reset")
    assert session.tracer.called("clear")
    assert session.controller.called("reset")
    assert session.rig.called("reset")
    assert session.weapon.called("reset")
    assert session.crosshair.called("reset")
    assert session.damage_flash.called("reset")
    assert session.wave_banner.called("reset")
    assert session.hud.called("reset")


def test_reset_run_clears_score_but_keeps_best():
    session, _state, _player, _enemies = build()
    session.score.data.score = 5000
    session.score.data.best_score = 8000
    session.score.data.combo = 4
    session.score.data.kills = 20

    session.reset_run()

    assert session.score.data.score == 0
    assert session.score.data.combo == 0
    assert session.score.data.kills == 0
    assert session.score.data.best_score >= 8000


# ---------------------------------------------------------------- 延迟销毁

def test_pending_destroy_is_flushed_before_listener_reset():
    """`clear()` 排下的 destroy 必须在 `clear_listeners()` 之前执行掉。

    这正是模块 docstring 里说的那个坑：`clear_listeners()` 会清空延迟队列，
    顺序反了的话，上一局的实体永远不会被销毁。
    """
    destroyed: list[str] = []
    enemies = Stub("enemies")

    def clear_with_defer() -> None:
        event_bus.defer(lambda: destroyed.append("enemy"))

    enemies.clear = clear_with_defer          # 直接赋值，绕过 __getattr__

    session, _state, _player, _enemies = build(enemies=enemies)
    session.reset_run()

    assert destroyed == ["enemy"], "上一局的实体没被销毁 —— flush 顺序错了"


# ---------------------------------------------------------------- 状态迁移

def test_state_changed_restarts_when_player_is_dead():
    player = FakePlayer()
    player.alive = False
    session, _state, _player, _enemies = build(player=player)

    session.on_state_changed(old=State.MENU, new=State.PLAYING)

    assert session.restarts == 1
    assert player.resets == 1


def test_state_changed_does_nothing_while_alive():
    session, _state, _player, _enemies = build()
    session.on_state_changed(old=State.PAUSED, new=State.PLAYING)
    assert session.restarts == 0


def test_player_died_switches_to_game_over():
    session, state, _player, _enemies = build()
    event_bus.emit("player_died")
    assert State.GAME_OVER in state.set_calls


def test_death_then_tab_back_to_playing_restarts():
    """完整走一遍玩家实际会走的路：死亡 → GAME_OVER → MENU → PLAYING。"""
    player = FakePlayer()
    session, state, _player, _enemies = build(player=player)

    # 死亡
    player.alive = False
    event_bus.emit("player_died")
    assert state.current is State.GAME_OVER

    # TAB 循环：GAME_OVER → MENU → PLAYING
    session.on_state_changed(old=State.GAME_OVER, new=State.MENU)
    assert session.restarts == 0

    session.on_state_changed(old=State.MENU, new=State.PLAYING)
    assert session.restarts == 1
    assert player.resets == 1


def test_request_restart_is_noop_when_playing():
    session, state, _player, _enemies = build()
    state.current = State.PLAYING
    assert session.request_restart() is False
    assert state.set_calls == []


# ---------------------------------------------------------------- 订阅确实挂上

def test_listeners_survive_reset():
    """重开之后每一条关键订阅都必须还在。

    单独断言，是因为"某一条被漏挂"不会报错，只会让某个功能静默失效：
    最严重的是 player_died —— 玩家死了游戏也不结束。
    """
    session, state, _player, _enemies = build()
    session.reset_run()

    state.current = State.PLAYING
    state.set_calls.clear()

    # 计分
    session.score.data.kills = 0
    event_bus.emit("enemy_died", enemy=None)
    assert session.score.data.kills == 1

    # 相机抖动
    session.rig.calls.clear()
    event_bus.emit("player_hurt", amount=10, hp=90, max_hp=100)
    assert session.rig.called("add_shake")

    # 红闪
    session.damage_flash.calls.clear()
    event_bus.emit("player_hurt", amount=10, hp=90, max_hp=100)
    assert session.damage_flash.called("on_hurt")

    # 护盾青闪
    event_bus.emit("shield_absorbed", charges=0)
    assert session.damage_flash.called("on_shield")

    # 波次提示
    session.wave_banner.calls.clear()
    event_bus.emit("wave_started", wave=3, total=8)
    event_bus.emit("wave_cleared", wave=3)
    assert session.wave_banner.called("on_wave_started")
    assert session.wave_banner.called("on_wave_cleared")

    # 拾取提示
    session.hud.calls.clear()
    event_bus.emit("pickup", kind="health", amount=30, label="+30 HP")
    assert session.hud.called("on_pickup")

    # 听觉
    session.enemies.calls.clear()
    event_bus.emit("noise", position=(0, 0, 0), radius=30)
    assert session.enemies.called("on_noise")

    # 玩家死亡 → GAME_OVER
    state.set_calls.clear()
    event_bus.emit("player_died")
    assert State.GAME_OVER in state.set_calls
