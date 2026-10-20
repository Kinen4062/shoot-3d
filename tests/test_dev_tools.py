"""开发期调试工具（`MINITPS_DEV=1`：下一波 / 清场 / 无敌）的回归约束。

这三个动作都**绕过玩法**，所以它们最典型的坏法不是崩，而是"改了一半"：

- 清场只清了场上、没清没出场的队列 → 下一帧又冒出几只，看起来像"清场键没用"；
- 下一波不清场 → 两波叠在同一屏，"第 N 波几只"这个数永远对不上；
- 无敌放在护盾之后 → 无敌期间护盾照掉，用一个看不见的状态改掉一个看得见的数字。

另外还有一条同样重要的：**正式版里这三个键必须没有落点**。
所以这里既测行为，也测"默认是关的"。
"""
from __future__ import annotations

import os
import random
from types import SimpleNamespace
from typing import ClassVar

import pytest

from src.core import config
from src.core.input_map import InputMap
from src.entities.player import Player
from src.systems.dev_tools import DevTools

# ---------------------------------------------------------------- 假对象


class _FakeEnemy:
    def __init__(self, name: str = "grunt", alive: bool = True) -> None:
        self.data = SimpleNamespace(name=name)
        self.alive = alive
        # 生成点重采样会读这两个字段（真波次系统从这里取坐标）。
        self.x = 5.0
        self.z = 5.0


class _FakeEnemies:
    def __init__(self, specs: list[tuple[str, bool]] | None = None) -> None:
        self.enemies = [_FakeEnemy(name, alive) for name, alive in (specs or [])]
        self.player = SimpleNamespace(x=0.0, z=0.0)
        self.spawned: list[str] = []
        self.clear_calls = 0

    @property
    def alive_count(self) -> int:
        return sum(1 for e in self.enemies if e.alive)

    def clear(self) -> None:
        self.clear_calls += 1
        self.enemies.clear()

    def spawn_type(self, name: str, x: float, z: float, **_kwargs) -> None:
        self.spawned.append(name)
        self.enemies.append(_FakeEnemy(name))


class _FakeWaves:
    """只记录"被要求跳波"，具体的波次推进由下面的真 WaveSystem 测。"""

    def __init__(self, wave: int = 3) -> None:
        self.wave = wave
        self.queue = ["grunt", "runner"]
        self.skips = 0

    def skip_to_next_wave(self) -> int:
        self.skips += 1
        self.wave += 1
        self.queue.clear()
        return self.wave


class _Arena:
    pillar_boxes: ClassVar[list] = []


class _Rig:
    yaw = 0.0


class _PlayingState:
    def in_state(self, *_states) -> bool:
        return True


def make_waves(wave: int = 3, phase: str = "FIGHT"):
    """造一个真的 WaveSystem（不启窗口、不建实体）。"""
    from src.systems.wave import FALLBACK_WAVES, WaveSystem

    enemies = _FakeEnemies()
    waves = WaveSystem(enemies, _Arena(), _Rig(), _PlayingState(),
                       table=FALLBACK_WAVES, rng=random.Random(1))
    waves.wave = wave
    waves.phase = phase
    return waves, enemies


# ---------------------------------------------------------------- 下一波


def test_next_wave_clears_field_before_advancing():
    """先清场再跳波：残留敌人会和下一波叠在一起，数字对不上。"""
    waves = _FakeWaves(wave=3)
    enemies = _FakeEnemies([("grunt", True), ("grunt", True)])
    dev = DevTools(waves=waves, enemies=enemies, player=SimpleNamespace(god_mode=False))

    assert dev.next_wave() == 4
    assert enemies.enemies == [], "上一波的残余必须消失"
    assert enemies.clear_calls == 1
    assert waves.queue == [], "没出场的队列也要一起丢"
    assert waves.skips == 1


def test_skip_advances_exactly_one_wave():
    waves, _enemies = make_waves(wave=7)
    from src.systems.wave import compose_wave

    waves.queue = ["grunt"] * 5
    assert waves.skip_to_next_wave() == 8
    assert waves.phase == "SPAWN", "跳波之后应当直接进入出场阶段"
    assert waves.queue, "新一波要重新排好出场队列（旧队列已被丢掉）"
    assert sorted(waves.queue) == sorted(
        t for t, n in compose_wave(8, waves.table).items() for _ in range(n)
    )


def test_skip_stops_at_the_boss_layer():
    """BOSS 层之上没有"下一波"，硬跳会落进 32 波的公式分支。"""
    from src.systems.wave import BOSS_WAVE

    waves, _enemies = make_waves(wave=BOSS_WAVE, phase="FIGHT")
    assert waves.skip_to_next_wave() == BOSS_WAVE
    assert waves.phase == "FIGHT"


def test_skip_does_nothing_after_the_run_is_cleared():
    from src.systems.wave import MAX_WAVE

    waves, _enemies = make_waves(wave=MAX_WAVE, phase="CLEARED")
    assert waves.skip_to_next_wave() == MAX_WAVE
    assert waves.phase == "CLEARED", "结算面板还开着，不该偷偷刷怪"


# ---------------------------------------------------------------- 清场


def test_clear_field_reports_alive_count_and_empties_the_queue():
    waves = _FakeWaves()
    enemies = _FakeEnemies([("grunt", True), ("runner", True), ("grunt", False)])
    dev = DevTools(waves=waves, enemies=enemies, player=SimpleNamespace(god_mode=False))

    assert dev.clear_field() == 2, "只数还活着的：死亡动画里的那只不算"
    assert enemies.enemies == []
    assert waves.queue == []


def test_clear_field_does_not_touch_score():
    """清场不能产生击杀事件 —— 否则"这一波该得多少分"就没基准了。"""
    from src.core import event_bus

    seen: list[str] = []
    event_bus.on("enemy_died", lambda **_k: seen.append("died"))
    try:
        dev = DevTools(waves=_FakeWaves(), enemies=_FakeEnemies([("grunt", True)]),
                       player=SimpleNamespace(god_mode=False))
        dev.clear_field()
        event_bus.flush()
        assert seen == []
    finally:
        event_bus.clear_listeners()


# ---------------------------------------------------------------- 无敌


def test_toggle_god_flips_the_flag():
    player = SimpleNamespace(god_mode=False)
    dev = DevTools(waves=_FakeWaves(), enemies=_FakeEnemies(), player=player)

    assert dev.toggle_god() is True
    assert player.god_mode is True
    assert dev.god is True
    assert dev.toggle_god() is False
    assert player.god_mode is False


@pytest.fixture
def player():
    p = Player()
    yield p
    from ursina import destroy
    destroy(p)


def test_god_mode_swallows_damage(player):
    player.god_mode = True
    assert player.take_damage(50) is False
    assert player.hp == config.PLAYER_MAX_HP


def test_god_mode_does_not_eat_shield_charges(player):
    """无敌时掉护盾层数 = 用一个看不见的状态改一个看得见的数字。"""
    player.god_mode = True
    for _ in range(3):
        player.add_shield()
    assert player.shield_charges == 3
    player.take_damage(50)
    assert player.shield_charges == 3


def test_without_god_mode_damage_lands(player):
    player.god_mode = False
    assert player.take_damage(10) is True
    assert player.hp < config.PLAYER_MAX_HP


def test_god_mode_survives_a_restart(player):
    """重开一局还得是无敌 —— 否则每死一次都要重按，调试节奏全断。"""
    player.god_mode = True
    player.reset()
    assert player.god_mode is True


# ---------------------------------------------------------------- 按键路由


class _FakeGame:
    def __init__(self, playing: bool = True) -> None:
        self.playing = playing

    def in_state(self, *_states) -> bool:
        return self.playing

    def toggle_pause(self) -> None:
        pass

    def debug_cycle(self) -> None:
        pass


class _RecordingDev:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def next_wave(self) -> int:
        self.calls.append("next")
        return 1

    def clear_field(self) -> int:
        self.calls.append("clear")
        return 0

    def toggle_god(self) -> bool:
        self.calls.append("god")
        return True


def test_debug_keys_reach_dev_tools():
    from src.core.input_map import KEY_DEV_CLEAR, KEY_DEV_GOD, KEY_DEV_NEXT_WAVE

    dev = _RecordingDev()
    keys = InputMap(_FakeGame(), dev=dev)
    for key in (KEY_DEV_NEXT_WAVE, KEY_DEV_CLEAR, KEY_DEV_GOD):
        keys._dispatch(key)
    assert dev.calls == ["next", "clear", "god"]


def test_debug_keys_are_inert_without_the_dev_object():
    """正式版：dev 是 None，按键不该命中任何分支，也不该崩。"""
    from src.core.input_map import KEY_DEV_NEXT_WAVE

    keys = InputMap(_FakeGame())
    keys._dispatch(KEY_DEV_NEXT_WAVE)      # 不抛异常即通过
    assert keys.dev is None


def test_debug_keys_do_not_fire_outside_playing():
    """暂停/结算面板上按 F1 不该在背后刷怪。"""
    from src.core.input_map import KEY_DEV_NEXT_WAVE

    dev = _RecordingDev()
    keys = InputMap(_FakeGame(playing=False), dev=dev)
    keys._dispatch(KEY_DEV_NEXT_WAVE)
    assert dev.calls == []


def test_dev_tools_are_off_unless_the_env_variable_is_set():
    raw = os.environ.get("MINITPS_DEV", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        pytest.skip("这次测试本身就是在 MINITPS_DEV=1 下跑的")
    assert config.DEVELOPMENT_TOOLS is False, "正式版不该带这三个热键"
