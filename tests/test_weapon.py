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
    assert set(data) == {"pistol", "rifle", "shotgun"}
    assert data["rifle"].auto is True
    assert data["pistol"].auto is False
    assert data["rifle"].mag_size == 30
    assert data["rifle"].spread > 0
    assert data["rifle"].range >= data["pistol"].range
    # 霰弹枪：连射、10 颗弹丸、15 发弹匣、第 10 波解锁。
    assert data["shotgun"].pellets == 10
    assert data["shotgun"].mag_size == 15
    assert data["shotgun"].auto is True, "改版后是连射霰弹枪"
    assert data["shotgun"].unlock_wave == 10
    # 第 20 波换大弹匣：数值写在数据里，阈值写在 config（MAG_UPGRADE_WAVE）。
    assert data["pistol"].late_mag_size == 18
    assert data["rifle"].late_mag_size == 45
    assert data["shotgun"].late_mag_size == 20
    assert config.MAG_UPGRADE_WAVE == 20
    # 距离分段是"2 格 / 4 格"，1 格 = GROUND_GRID_CELL 米。
    # 写死 4/8 会把这层关系藏起来，改成按格子反推，改格子大小的时候能立刻发现。
    assert data["shotgun"].tight_range == config.GROUND_GRID_CELL * 2
    assert data["shotgun"].wide_range == config.GROUND_GRID_CELL * 4


def test_fallback_matches_shipped_json():
    """打包后 data/ 丢了会退回内置数值，两者必须一致，否则行为会悄悄变。"""
    shipped = load_weapons(config.resource_path(config.WEAPON_FILE))
    assert shipped == FALLBACK_WEAPONS


def test_shotgun_has_its_own_shot_sound():
    """霰弹枪不再复用步枪的枪声。

    这条退化的表现极其安静：换枪之后音色不变，玩家只会觉得"换枪没什么区别"，
    不会报任何错。所以映射本身要钉住，名字还必须真的在 `SOUND_VOLUMES` 里
    （音频系统拿它当白名单，写错会退回步枪、而不是变成一枪没声）。
    """
    from src.systems.weapon import shot_sfx_for

    assert shot_sfx_for("shotgun") == "shotgun"
    assert shot_sfx_for("shotgun") in config.SOUND_VOLUMES
    assert shot_sfx_for("rifle") == "shoot"
    assert shot_sfx_for("pistol") == "shoot"
    assert shot_sfx_for("__nope__") == config.DEFAULT_SHOT_SFX


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

# ---------------------------------------------------------------- 局内成长

def test_damage_bonus_steps_every_five_waves():
    """每 5 波单颗弹丸 +1：第 5 波 +1、第 10 波 +2 …… 第 30 波 +6。"""
    from src.systems.weapon import wave_damage_bonus

    assert [wave_damage_bonus(w) for w in (0, 1, 4)] == [0, 0, 0]
    assert [wave_damage_bonus(w) for w in (5, 9)] == [1, 1]
    assert [wave_damage_bonus(w) for w in (10, 14)] == [2, 2]
    assert wave_damage_bonus(30) == 6
    assert wave_damage_bonus(31) == 6, "BOSS 层不该比 30 波更离谱"
    assert wave_damage_bonus(-5) == 0, "异常波次不能把伤害算成负数"


def test_damage_bonus_is_applied_before_the_far_multiplier():
    """先加再乘。先乘后加的话远距离会"多赚"加成，霰弹枪的收束设计就废了。"""
    from src.systems.weapon import damage_at, wave_damage_bonus

    shotgun = FALLBACK_WEAPONS["shotgun"]
    bonus = wave_damage_bonus(30)
    assert damage_at(shotgun, 1.0, bonus) == shotgun.damage + bonus
    assert damage_at(shotgun, 10.0, bonus) == (shotgun.damage + bonus) * 0.5
    assert damage_at(shotgun, 10.0, bonus) == 13          # 不是 20*0.5+6 = 16


def test_mag_upgrade_keeps_the_rounds_already_in_the_gun():
    state = WeaponState(light_gun(mag=30))
    state.ammo = 12
    state.set_mag_size(45)
    # 12 + (45-30) = 27：升级只补差额，不吞手上的子弹，也不整匣补满。
    assert (state.mag_size, state.ammo) == (45, 27)


def test_mag_shrink_clamps_ammo():
    state = WeaponState(light_gun(mag=30))
    state.ammo = 30
    state.set_mag_size(12)
    assert (state.mag_size, state.ammo) == (12, 12)


def test_reload_fills_to_the_new_magazine():
    state = WeaponState(light_gun(mag=30, reload_time=1.0))
    state.ammo = 0
    state.set_mag_size(45)
    assert state.request_reload()
    state.update(1.0)
    assert state.ammo == 45
