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
