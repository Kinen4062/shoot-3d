"""拾取：掉落概率与权重、可拾取条件、效果施加。

`PickupSystem` 本身要建实体（池化），这三块逻辑都抽成了模块级纯函数，
所以这个文件完全不碰引擎 —— 掉落表的概率分布是最该被钉死的东西，
一旦写反（比如先选类型再掷概率），它在地图上表现为"血包多得离谱"，
但没有任何报错。
"""
import random

import pytest

from src.core import config
from src.systems.pickup_system import (
    DROP_TABLE,
    KINDS,
    PickupResult,
    apply_pickup,
    can_pickup,
    roll_drop,
)


class FakePlayer:
    def __init__(self, hp=100.0, max_hp=100.0) -> None:
        self.hp = hp
        self.max_hp = max_hp
        self.shield_charges = 0
        self.speed_mult = 1.0
        self.speed_boost_left = 0.0
        self.healed = 0.0

    def heal(self, amount: float) -> float:
        before = self.hp
        self.hp = min(self.max_hp, self.hp + amount)
        self.healed = self.hp - before
        return self.healed

    def grant_speed_boost(self, mult: float, duration: float) -> None:
        self.speed_mult = max(self.speed_mult, mult)
        self.speed_boost_left = max(self.speed_boost_left, duration)

    def add_shield(self, max_charges: int = 3) -> int:
        self.shield_charges = min(max_charges, self.shield_charges + 1)
        return self.shield_charges


class FakeWeaponState:
    def __init__(self, ammo=10, mag=30) -> None:
        from types import SimpleNamespace
        self.ammo = ammo
        # 真实 WeaponState 的容量是**实例字段**（第 20 波会换弹匣），
        # data.mag_size 只是初始值，拾取系统读的是前者。
        self.mag_size = mag
        self.data = SimpleNamespace(mag_size=mag)


class FakeWeapon:
    def __init__(self, ammo=10, mag=30) -> None:
        self.current = FakeWeaponState(ammo, mag)
        self.refilled = 0

    def refill_current(self) -> int:
        added = self.current.mag_size - self.current.ammo
        self.current.ammo = self.current.mag_size
        self.refilled = added
        return added


# ---------------------------------------------------------------- 掉落

def test_zero_chance_never_drops():
    rng = random.Random(1)
    assert all(roll_drop(rng, chance=0.0) is None for _ in range(200))


def test_full_chance_always_drops_something():
    rng = random.Random(2)
    kinds = {roll_drop(rng, chance=1.0) for _ in range(200)}
    assert kinds <= set(KINDS)
    assert None not in kinds


def test_drop_rate_matches_config():
    rng = random.Random(3)
    hits = sum(1 for _ in range(20000) if roll_drop(rng, chance=0.30) is not None)
    rate = hits / 20000
    assert 0.28 <= rate <= 0.32, f"实测掉落率 {rate:.3f}，配置是 0.30"


def test_weight_distribution_follows_table():
    rng = random.Random(4)
    counts = dict.fromkeys(KINDS, 0)
    total = 30000
    for _ in range(total):
        kind = roll_drop(rng, chance=1.0)
        counts[kind] += 1

    weight_sum = sum(w for _, w in DROP_TABLE)
    for kind, weight in DROP_TABLE:
        expected = weight / weight_sum
        got = counts[kind] / total
        assert abs(got - expected) < 0.02, (
            f"{kind}: 期望 {expected:.3f}，实测 {got:.3f}"
        )


def test_empty_table_is_safe():
    rng = random.Random(5)
    assert roll_drop(rng, chance=1.0, table=()) is None


# ---------------------------------------------------------------- 可拾取条件

def test_health_pack_skipped_at_full_hp():
    player = FakePlayer(hp=100.0, max_hp=100.0)
    assert can_pickup("health", player, FakeWeapon()) is False
    player.hp = 70.0
    assert can_pickup("health", player, FakeWeapon()) is True


def test_ammo_skipped_when_mag_full():
    player = FakePlayer()
    assert can_pickup("ammo", player, FakeWeapon(ammo=30, mag=30)) is False
    assert can_pickup("ammo", player, FakeWeapon(ammo=29, mag=30)) is True


def test_other_pickups_always_allowed():
    player = FakePlayer(hp=100.0, max_hp=100.0)
    weapon = FakeWeapon(ammo=30, mag=30)
    for kind in ("speed", "gem", "shield"):
        assert can_pickup(kind, player, weapon) is True


# ---------------------------------------------------------------- 效果

def test_health_pack_heals_thirty():
    player = FakePlayer(hp=60.0, max_hp=100.0)
    result = apply_pickup("health", player, FakeWeapon())
    assert isinstance(result, PickupResult)
    assert result.amount == 30
    assert player.hp == 90.0
    assert "30" in result.label


def test_health_pack_never_exceeds_max():
    player = FakePlayer(hp=90.0, max_hp=100.0)
    result = apply_pickup("health", player, FakeWeapon())
    assert result.amount == 10
    assert player.hp == 100.0


def test_ammo_refills_current_mag():
    weapon = FakeWeapon(ammo=4, mag=30)
    result = apply_pickup("ammo", FakePlayer(), weapon)
    assert result.amount == 26
    assert weapon.current.ammo == 30


def test_speed_boost_sets_multiplier_and_duration():
    player = FakePlayer()
    result = apply_pickup("speed", player, FakeWeapon())
    assert player.speed_mult == pytest.approx(config.SPEED_BOOST_MULT)
    assert player.speed_boost_left == pytest.approx(config.SPEED_BOOST_TIME)
    assert result.amount == int(config.SPEED_BOOST_TIME)


def test_shield_adds_one_charge():
    player = FakePlayer()
    apply_pickup("shield", player, FakeWeapon())
    assert player.shield_charges == 1
    apply_pickup("shield", player, FakeWeapon())
    assert player.shield_charges == 2


def test_gem_returns_score_amount():
    result = apply_pickup("gem", FakePlayer(), FakeWeapon())
    assert result.amount == config.GEM_SCORE
    assert result.kind == "gem"


def test_unknown_kind_is_harmless():
    result = apply_pickup("banana", FakePlayer(), FakeWeapon())
    assert result.amount == 0
