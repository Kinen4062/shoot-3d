"""敌人状态机测试：状态迁移、前摇结算、丢失目标、远程兵的站位。

全部用纯逻辑断言，不需要窗口，也不需要 Ursina。
"""
from __future__ import annotations

import itertools
import math

import pytest

from src.core import config
from src.core.enemy_types import FALLBACK_ENEMY_TYPES
from src.systems.enemy_ai import (
    KEEP_MAX,
    KEEP_MIN,
    Brain,
    BrainState,
    separation_push,
)

GRUNT = FALLBACK_ENEMY_TYPES["grunt"]
SHOOTER = FALLBACK_ENEMY_TYPES["shooter"]


def make(data=GRUNT) -> Brain:
    return Brain(data)


def tick(brain: Brain, seconds: float, x: float = 0.0, z: float = 0.0,
         px: float = 0.0, pz: float = 0.0, sees: bool = True,
         dt: float = 1.0 / 60.0) -> None:
    for _ in range(round(seconds / dt)):
        brain.tick(dt, x, z, px, pz, sees)


# ---------------------------------------------------------------- 基本迁移

def test_idle_to_chase_when_seen():
    b = make()
    assert b.state is BrainState.IDLE
    b.tick(1 / 60, 0, 0, 0, 10, sees=True)
    assert b.state is BrainState.CHASE


def test_idle_without_target_stays_idle_and_scans():
    b = make()
    yaw0 = b.face_yaw
    tick(b, 0.5, sees=False)
    assert b.state is BrainState.IDLE
    # 待机时会缓慢扫视（否则背对玩家就永远发现不了）
    assert b.face_yaw != yaw0


def test_hearing_noise_switches_to_search_at_noise_point():
    b = make()
    b.hear(4.0, -6.0)
    assert b.state is BrainState.SEARCH
    assert b.last_seen == (4.0, -6.0)
    tick(b, 0.1, x=0.0, z=0.0, px=0.0, pz=0.0, sees=False)
    # 朝噪声点走
    assert b.move_x > 0 and b.move_z < 0


def test_search_arrives_then_returns_to_idle():
    b = make()
    b.hear(6.0, 0.0)
    x = 0.0
    for _ in range(600):
        b.tick(1 / 60, x, 0.0, 0.0, 0.0, sees=False)
        x += b.move_x * 3.0 * (1 / 60)      # 手动按 3m/s 推进，模拟 EnemySystem
        if b.state is BrainState.IDLE:
            break
    assert b.state is BrainState.IDLE
    assert b.last_seen is None
    assert x == pytest.approx(6.0, abs=1.0)


# ---------------------------------------------------------------- 攻击

def test_chase_enters_attack_inside_range_and_windup_resolves():
    b = make()
    # 站在 1.5 米（攻击距离 2.2 内）
    b.tick(1 / 60, 0.0, 1.5, 0.0, 0.0, sees=True)
    assert b.state is BrainState.ATTACK
    assert b.windup == pytest.approx(GRUNT.attack_windup)

    # 前摇期间不出伤害，也不能移动
    fired = 0
    for _ in range(5):
        b.tick(1 / 60, 0.0, 1.5, 0.0, 0.0, sees=True)
        if b.attack_now:
            fired += 1
        assert b.move_x == 0 and b.move_z == 0
    assert fired == 0

    # 前摇走完的那一帧出伤害，且只出一帧
    for _ in range(30):
        b.tick(1 / 60, 0.0, 1.5, 0.0, 0.0, sees=True)
        if b.attack_now:
            fired += 1
    assert fired == 1
    assert b.state in (BrainState.CHASE, BrainState.ATTACK)


def test_attack_respects_cooldown():
    b = make()
    tick(b, 1.0, x=0.0, z=1.5, px=0.0, pz=0.0, sees=True)
    # 冷却期间不会再进 ATTACK
    assert b.attack_cd > 0
    for _ in range(20):
        b.tick(1 / 60, 0.0, 1.5, 0.0, 0.0, sees=True)
        assert b.state is not BrainState.ATTACK


def test_out_of_range_enemy_keeps_moving():
    b = make()
    b.tick(1 / 60, 0.0, 6.0, 0.0, 0.0, sees=True)
    assert b.state is BrainState.CHASE
    d = math.hypot(b.move_x, b.move_z)
    assert d == pytest.approx(1.0, abs=1e-6)
    assert b.move_z < 0     # 朝玩家（-Z 方向）走


# ---------------------------------------------------------------- 丢失目标

def test_lost_target_after_timeout_goes_search():
    b = make()
    tick(b, 0.1, x=0.0, z=10.0, px=0.0, pz=0.0, sees=True)
    assert b.state is BrainState.CHASE
    tick(b, config.LOSE_TARGET_TIME - 0.2, x=0.0, z=10.0, px=0.0, pz=0.0, sees=False)
    assert b.state is BrainState.CHASE, "还没到 3 秒，不该放弃"
    tick(b, 0.4, x=0.0, z=10.0, px=0.0, pz=0.0, sees=False)
    assert b.state in (BrainState.SEARCH, BrainState.IDLE)


def test_dead_brain_stops_moving_and_attacking():
    b = make()
    b.kill()
    b.tick(1 / 60, 0.0, 1.0, 0.0, 0.0, sees=True)
    assert b.state is BrainState.DEAD
    assert not b.attack_now and not b.shoot_now
    assert b.move_x == 0 and b.move_z == 0


# ---------------------------------------------------------------- 远程兵

def test_shooter_holds_distance_and_shoots():
    b = make(SHOOTER)
    # 距离 13（preferred_range）：先蓄力射一发，然后横移而不是贴脸。
    fired = 0
    for _ in range(180):
        b.tick(1 / 60, 0.0, 13.0, 0.0, 0.0, sees=True)
        if b.shoot_now:
            fired += 1
    assert fired >= 1, "蓄力 0.8s 内应当至少射出一发"

    # 开火后进入冷却，此时应当在射程带内左右横移：不前进、不后退
    for _ in range(5):
        b.tick(1 / 60, 0.0, 13.0, 0.0, 0.0, sees=True)
    assert b.state is BrainState.CHASE
    assert abs(b.move_z) < 0.1, f"在理想射程内不该前后移动，move_z={b.move_z}"
    assert abs(b.move_x) == pytest.approx(1.0, abs=1e-6)


def test_shooter_backs_off_when_too_close():
    b = make(SHOOTER)
    b.tick(1 / 60, 0.0, 2.0, 0.0, 0.0, sees=True)
    assert b.move_z > 0, "被贴脸时要后退（玩家在 -Z 侧，后退是 +Z）"


def test_range_bands_are_ordered():
    # 这两个常量定反了会让远程兵一直贴着玩家或一直后退，做一个下限保护
    assert 0.0 < KEEP_MIN < KEEP_MAX


# ---------------------------------------------------------------- 分离力

def test_separation_pushes_overlapping_enemies_apart():
    push = separation_push(0.0, 0.0, 0.5, 0.0, 0.5, 0.5)
    assert push is not None
    dx, dz = push
    assert dx < 0 and dz == pytest.approx(0.0)
    # A 推走一半、B 推走一半，推完正好是 r1 + r2 + gap
    assert 0.5 + 2 * abs(dx) == pytest.approx(0.5 + 0.5 + config.SEPARATION_GAP, abs=1e-6)


def test_separation_returns_none_when_far_enough():
    assert separation_push(0.0, 0.0, 10.0, 0.0, 0.5, 0.5) is None


def test_separation_handles_exact_overlap():
    push = separation_push(1.0, 2.0, 1.0, 2.0, 0.5, 0.5)
    assert push is not None
    assert push != (0.0, 0.0)

# ---------------------------------------------------------------- BOSS 炮击

LIANG = FALLBACK_ENEMY_TYPES["liang"]


def test_boss_volley_fires_once_per_second():
    b = make(LIANG)
    marks = []
    for i in range(600):                     # 10 秒
        b.tick(1 / 60, 0, 0, 0, 10, sees=True)
        if b.volley_now:
            marks.append(i / 60)
    assert len(marks) >= 9, f"10 秒只有 {len(marks)} 发"
    gaps = [b - a for a, b in itertools.pairwise(marks)]
    assert all(abs(g - 1.0) < 0.05 for g in gaps), f"间隔不均：{gaps}"


def test_boss_volley_needs_sight_and_range():
    far = make(LIANG)
    far.tick(1 / 60, 0, 0, 0, 40, sees=True)
    assert not far.volley_now, "28 米外不该开炮"

    blind = make(LIANG)
    blind.tick(1 / 60, 0, 0, 0, 10, sees=False)
    assert not blind.volley_now, "看不见目标时不能开炮"


def test_boss_keeps_firing_while_it_melees():
    """贴脸时近战和炮击并行 —— 这才是"加强"，不然它还是个大号沙包。"""
    b = make(LIANG)
    b.tick(1 / 60, 0, 0, 1.0, 0, sees=True)
    assert b.state is BrainState.ATTACK
    assert b.volley_now


def test_only_the_boss_has_a_volley():
    for name, data in FALLBACK_ENEMY_TYPES.items():
        if name != "liang":
            assert data.volley_cd == 0.0, f"{name} 不该有炮击"


def test_liang_volley_values_are_configured():
    assert LIANG.volley_cd == pytest.approx(1.0)
    assert LIANG.volley_range >= 20
    assert LIANG.volley_damage > 0
    assert LIANG.volley_color_rgb is not None, "弹丸要能和小兵的黄球分开"


def test_shipped_enemy_table_matches_the_fallback():
    """data/enemies.json 和内置兜底必须一致，否则打包后行为会悄悄变。"""
    from src.core.enemy_types import load_enemy_types

    shipped = load_enemy_types(config.resource_path(config.ENEMY_FILE))
    assert shipped == FALLBACK_ENEMY_TYPES
