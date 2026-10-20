"""波次与生成点规则测试（规划书 §5.4 的三条硬约束）。"""
from __future__ import annotations

import math
import random
from types import SimpleNamespace
from typing import ClassVar

import pytest

from src.core import config
from src.systems.perception import yaw_delta, yaw_to
from src.systems.wave import (
    FALLBACK_WAVES,
    compose_wave,
    hp_multiplier,
    pick_spawn_point,
    speed_multiplier,
)

# ---------------------------------------------------------------- 配比

def test_table_wave_is_used_verbatim():
    assert compose_wave(1, FALLBACK_WAVES) == {"grunt": 4}
    assert compose_wave(4, FALLBACK_WAVES) == {"grunt": 6, "runner": 3, "shooter": 1}


def test_beyond_table_keeps_scaling_but_capped():
    for wave in (7, 12, 40, 200):
        comp = compose_wave(wave, FALLBACK_WAVES)
        assert sum(comp.values()) <= config.MAX_ENEMIES
        assert comp.get("shooter", 0) >= 1, "后期必须还有远程兵，否则没有张力"
        assert comp.get("grunt", 0) >= 1


def test_difficulty_multipliers_are_capped():
    assert hp_multiplier(0) == pytest.approx(1.0)
    assert hp_multiplier(10) == pytest.approx(2.0)
    assert hp_multiplier(999) == pytest.approx(4.0)
    assert speed_multiplier(999) == pytest.approx(1.5)


# ---------------------------------------------------------------- 生成点

def test_spawn_point_is_on_ring_and_behind_camera():
    rng = random.Random(7)
    for _ in range(200):
        point = pick_spawn_point(rng, 0.0, 0.0, 0.0, [])
        assert point is not None
        x, z = point
        r = math.hypot(x, z)
        assert config.SPAWN_RING[0] - 1e-6 <= r <= config.SPAWN_RING[1] + 1e-6
        angle = abs(yaw_delta(0.0, yaw_to(x, z)))
        assert angle >= 75.0, f"生成点落在视野里了：{angle:.1f}°"


def test_spawn_point_avoids_other_enemies():
    rng = random.Random(11)
    existing = [(0.0, 20.0), (0.0, -20.0), (20.0, 0.0), (-20.0, 0.0)]
    for _ in range(100):
        point = pick_spawn_point(rng, 0.0, 0.0, 0.0, existing)
        if point is None:
            continue
        x, z = point
        for ex, ez in existing:
            assert math.hypot(x - ex, z - ez) >= 1.5 - 1e-9


def test_spawn_point_returns_none_when_arena_too_small():
    # 圆环 18~26 米，但场地内边界只有 1 米：任何点都出界，必须放弃而不是硬塞
    rng = random.Random(3)
    assert pick_spawn_point(rng, 0.0, 0.0, 0.0, [], inner=1.0) is None


def test_spawn_point_respects_pillar_blockers():
    rng = random.Random(5)
    # 用一圈柱子把圆环全部堵死（半径 26 的环带 18~26，柱子间距很小）
    blockers = [(math.sin(a) * 22.0, math.cos(a) * 22.0, 6.0)
                for a in [i * math.tau / 16 for i in range(16)]]
    assert pick_spawn_point(rng, 0.0, 0.0, 0.0, [], blockers) is None


# ---------------------------------------------------------------- 30 波 + BOSS 层

def test_max_wave_is_thirty():
    from src.systems.wave import BOSS_WAVE, MAX_WAVE

    assert MAX_WAVE == 30
    assert BOSS_WAVE == MAX_WAVE + 1, "BOSS 层紧跟在通关之后"


def test_tank_appears_every_six_waves():
    """重甲兵的出场节奏：6/12/18 各一只，24/30 各两只，其余没有。

    这条守的是"第 7 波之后再也不出重甲"那个缺口 —— 一旦回退，
    后期会变成一场没有起伏的消耗战，而不会有任何报错。
    """
    from src.systems.wave import tank_count

    expected = {6: 1, 12: 1, 18: 1, 24: 2, 30: 2}
    for wave in range(1, 31):
        assert tank_count(wave) == expected.get(wave, 0), f"第 {wave} 波的坦克数不对"


def test_tanks_actually_show_up_in_the_composition():
    """`tank_count` 对了不算数，要真的进到配比表里。"""
    from src.systems.wave import tank_count

    for wave in range(6, 31):
        comp = compose_wave(wave, FALLBACK_WAVES)
        assert comp.get("tank", 0) == tank_count(wave), f"第 {wave} 波配比里没有坦克"


def test_every_wave_fits_the_on_screen_cap():
    """重甲名额必须从总数里扣，不能把上限顶爆。

    顶爆的表现很隐蔽：生成队列被 `MAX_ENEMIES` 卡住，最后几只只能等前面死掉，
    玩家看到的是"这波明明清了却一直不进下一波"。
    """
    for wave in range(1, 31):
        comp = compose_wave(wave, FALLBACK_WAVES)
        assert sum(comp.values()) <= config.MAX_ENEMIES, f"第 {wave} 波超出同屏上限"


def test_boss_layer_lineup_is_fixed():
    from src.systems.wave import BOSS_LAYER, BOSS_WAVE

    assert compose_wave(BOSS_WAVE, FALLBACK_WAVES) == BOSS_LAYER
    assert BOSS_LAYER == {"tank": 2, "shooter": 5, "liang": 1}


def test_boss_layer_wave_count_is_one_shot():
    """31 层只有 BOSS 那一波，不能顺着公式越算越大。"""
    from src.systems.wave import BOSS_LAYER, BOSS_WAVE

    assert compose_wave(BOSS_WAVE, FALLBACK_WAVES) == BOSS_LAYER
    # 32 波在正常流程里走不到，但万一有人手改 wave，也不能凭空再刷一只 BOSS。
    assert "liang" not in compose_wave(BOSS_WAVE + 1, FALLBACK_WAVES)


def test_liang_exists_in_every_enemy_table():
    """BOSS 的数值必须三处同时存在：JSON、兜底表、体型表。

    少任何一处的表现都是 `KeyError` 或者"BOSS 长得跟 grunt 一样"。
    """
    from src.core.enemy_types import FALLBACK_ENEMY_TYPES
    from src.entities.enemy_body import SHAPES
    from src.systems.wave import BOSS_KIND

    assert BOSS_KIND in FALLBACK_ENEMY_TYPES
    assert BOSS_KIND in SHAPES
    assert SHAPES[BOSS_KIND].feature == "crown"

# ---------------------------------------------------------------- BOSS 层增援

class _FakeEnemy:
    def __init__(self, name: str, alive: bool = True, x: float = 5.0, z: float = 5.0) -> None:
        self.data = SimpleNamespace(name=name)
        self.alive = alive
        # 生成点重采样会读现存的敌人坐标，缺这两个字段会 AttributeError。
        self.x = x
        self.z = z


class _FakeEnemies:
    def __init__(self) -> None:
        self.enemies: list[_FakeEnemy] = []
        self.player = None
        self.spawned: list[str] = []

    @property
    def alive_count(self) -> int:
        return sum(1 for e in self.enemies if e.alive)

    def spawn_type(self, name: str, x: float, z: float, **_kwargs) -> None:
        self.spawned.append(name)
        self.enemies.append(_FakeEnemy(name))


class _Arena:
    # 类属性 + ClassVar：波次系统会读 `arena.pillar_boxes`，
    # 写成可变默认值会被 ruff 判成"共享可变状态"。
    pillar_boxes: ClassVar[list] = []


class _Rig:
    yaw = 0.0


class _PlayingState:
    def in_state(self, *_states) -> bool:
        return True


def make_boss_wave(seed: int = 3):
    """造一个"已经在第 31 层 FIGHT 阶段"的波次系统。"""
    from src.systems.wave import BOSS_WAVE, WaveSystem

    enemies = _FakeEnemies()
    enemies.enemies.append(_FakeEnemy("liang"))
    waves = WaveSystem(enemies, _Arena(), _Rig(), _PlayingState(),
                       table=FALLBACK_WAVES, rng=random.Random(seed))
    waves.wave = BOSS_WAVE
    waves.phase = "FIGHT"
    waves.boss_spawned = True
    return waves, enemies


def step(waves, seconds: float, dt: float = 1 / 60) -> None:
    for _ in range(round(seconds / dt)):
        waves.update(dt)


def test_boss_layer_reinforcements_arrive_every_ten_seconds():
    from src.systems.wave import BOSS_REINFORCE_COUNT, BOSS_REINFORCE_INTERVAL

    waves, enemies = make_boss_wave()
    step(waves, BOSS_REINFORCE_INTERVAL - 0.2)
    assert waves.reinforcements == 0, "不到 10 秒不该补兵"

    step(waves, 0.4)
    assert waves.reinforcements == BOSS_REINFORCE_COUNT
    # 排队的同时已经在往外放了，所以按"队列 + 已出场"算总数。
    assert len(waves.queue) + len(enemies.spawned) == BOSS_REINFORCE_COUNT
    assert waves.phase in ("SPAWN", "FIGHT")


def test_reinforcements_are_made_of_normal_enemy_types():
    from src.systems.wave import BOSS_REINFORCE_TYPES

    waves, _enemies = make_boss_wave(seed=9)
    step(waves, 10.2)
    assert waves.queue
    assert set(waves.queue) <= set(BOSS_REINFORCE_TYPES)
    assert "liang" not in waves.queue, "增援里再刷一只 BOSS 就没法通关了"


def test_reinforcements_stop_once_the_boss_is_dead():
    """BOSS 一死就不再补兵：不然 alive_count 永远回不到 0，永远不通关。"""
    waves, enemies = make_boss_wave()
    step(waves, 10.2)
    first = waves.reinforcements

    # 让队列先放完，回到 FIGHT（否则 update 走的是 SPAWN 分支）。
    step(waves, 5.0)
    waves.phase = "FIGHT"
    enemies.enemies[0].alive = False        # BOSS 倒了，杂兵还在

    step(waves, 25.0)
    assert waves.reinforcements == first, "BOSS 死后不该再有增援"
    assert waves.phase == "FIGHT"


def test_pending_reinforcements_are_cancelled_after_the_boss_dies():
    """队列里还没出场的那几只直接作废，别让玩家以为"BOSS 死了还在刷"。"""
    waves, enemies = make_boss_wave()
    step(waves, 10.2)
    assert waves.queue

    enemies.enemies[0].alive = False
    waves.update(1 / 60)
    assert waves.queue == []
    assert waves.phase == "FIGHT"


def test_reinforcements_respect_the_on_screen_cap():
    waves, enemies = make_boss_wave()
    enemies.enemies.extend(
        _FakeEnemy("grunt") for _ in range(config.MAX_ENEMIES - 1)
    )
    assert enemies.alive_count == config.MAX_ENEMIES

    step(waves, 20.5)
    assert waves.reinforcements == 0, "场上满了就该跳过，而不是往队列里堆"


def test_boss_layer_state_resets_between_runs():
    waves, _enemies = make_boss_wave()
    step(waves, 10.2)
    waves.reset()
    assert (waves.reinforce_timer, waves.reinforcements, waves.boss_spawned) == (0.0, 0, False)
