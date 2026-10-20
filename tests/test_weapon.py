"""武器逻辑（射速 / 弹药 / 换弹 / 散布）的单元测试。

这块全是纯数值逻辑，不依赖引擎窗口，出问题最难靠试玩定位：
「按了没反应」可能是冷却、可能是换弹、也可能是弹药为 0。
"""
import math
import random

import pytest
from ursina import Vec3

from src.core import config
from src.systems.tracer import tracer_angles
from src.systems.weapon import (
    FALLBACK_WEAPONS,
    WeaponData,
    WeaponState,
    apply_spread,
    load_weapons,
)

RIFLE = WeaponData("rifle", 18, 0.1, 30, 1.8, 1.2, 80, True, 1)


def light_gun(mag=5, fire_rate=0.1, reload_time=1.0):
    return WeaponData("t", 10, fire_rate, mag, reload_time, 0.0, 50, True, 1)


# ---------------------------------------------------------------- 数据

def test_weapons_json_loads_and_is_complete():
    data = load_weapons(config.resource_path(config.WEAPON_FILE))
    assert set(data) == {"pistol", "rifle"}
    assert data["rifle"].auto is True
    assert data["pistol"].auto is False
    assert data["rifle"].mag_size == 30
    assert data["rifle"].spread > 0
    assert data["rifle"].range >= data["pistol"].range


def test_fallback_matches_shipped_json():
    """打包后 data/ 丢了会退回内置数值，两者必须一致，否则行为会悄悄变。"""
    shipped = load_weapons(config.resource_path(config.WEAPON_FILE))
    assert FALLBACK_WEAPONS == shipped


# ---------------------------------------------------------------- 射速

def test_fire_rate_gate():
    state = WeaponState(RIFLE)
    assert state.consume() is True
    assert state.ammo == 29

    state.update(0.05)
    assert state.can_fire() is False      # 冷却未结束
    state.update(0.06)                    # 累计 0.11 > 0.1
    assert state.can_fire() is True


def test_cooldown_does_not_go_negative():
    state = WeaponState(RIFLE)
    state.consume()
    state.update(1.0)
    assert state.cooldown <= 0.0


# ---------------------------------------------------------------- 弹药 / 换弹

def test_auto_reload_when_mag_empty():
    state = WeaponState(light_gun(mag=2))
    assert state.consume() is True
    state.update(0.2)
    assert state.consume() is True
    assert state.ammo == 0
    assert state.reloading is True        # 打空自动换弹
    assert state.can_fire() is False


def test_reload_takes_time_and_refills():
    state = WeaponState(light_gun(mag=5, reload_time=1.0))
    state.consume()
    assert state.request_reload() is True

    state.update(0.5)
    assert state.ammo == 4                # 换弹期间不补弹
    assert state.reloading is True

    state.update(0.6)
    assert state.ammo == 5
    assert state.reloading is False


def test_cannot_reload_full_magazine():
    state = WeaponState(light_gun(mag=5))
    assert state.request_reload() is False


def test_reload_is_not_restarted_while_running():
    state = WeaponState(light_gun(mag=5, reload_time=1.0))
    state.consume()
    state.request_reload()
    state.update(0.9)
    assert state.request_reload() is False    # 不能靠连按 R 刷新进度
    assert state.reload_left == pytest.approx(0.1, abs=1e-6)


# ---------------------------------------------------------------- 散布

def test_zero_spread_keeps_direction():
    direction = Vec3(0, 0, 1)
    result = apply_spread(direction, 0.0, random.Random(1))
    assert result.x == pytest.approx(direction.x)
    assert result.z == pytest.approx(direction.z)


def test_spread_stays_inside_cone():
    rng = random.Random(7)
    limit = 2.0
    for _ in range(300):
        v = apply_spread(Vec3(0, 0, 1), limit, rng)
        assert v.length() == pytest.approx(1.0)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, v.z))))
        assert angle <= limit + 1e-6


def test_spread_is_not_biased_to_center():
    """极角必须按 sqrt(u) 采样，否则弹着点会堆在准星中心。"""
    rng = random.Random(11)
    radii = []
    for _ in range(400):
        v = apply_spread(Vec3(0, 0, 1), 10.0, rng)
        radii.append(math.degrees(math.acos(min(1.0, v.z))))
    near = sum(1 for r in radii if r < 5.0)
    assert 0.15 < near / len(radii) < 0.45


# ---------------------------------------------------------------- 拖尾朝向

def test_tracer_angles_forward():
    pitch, yaw = tracer_angles(Vec3(0, 0, 5))
    assert pitch == pytest.approx(0.0)
    assert yaw == pytest.approx(0.0)


def test_tracer_angles_downward_is_positive_pitch():
    """rotation_x 为正表示 +Z 向下压，和相机约定一致。"""
    pitch, _ = tracer_angles(Vec3(0, -1, 1))
    assert pitch == pytest.approx(45.0)


def test_tracer_angles_straight_up():
    pitch, _ = tracer_angles(Vec3(0, 3, 0))
    assert pitch == pytest.approx(-90.0)


def test_tracer_angles_yaw_follows_xz():
    _, yaw = tracer_angles(Vec3(1, 0, 1))
    assert yaw == pytest.approx(45.0)
