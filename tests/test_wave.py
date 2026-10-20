"""波次与生成点规则测试（规划书 §5.4 的三条硬约束）。"""
from __future__ import annotations

import math
import random

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
