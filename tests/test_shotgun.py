"""霰弹枪：距离分段 / 多弹丸 / 波次解锁 / 后台换弹。

这四条都是**数值契约**，靠打游戏验不出来（打中了但伤害少了 10 点，玩家分不清
是散布还是衰减），所以全部压成纯函数断言：

- 2 格以内：散布最小、伤害全额 → 7 颗全中 = 140，第 10 波的小兵（120 血）一枪一个；
- 2 格外：伤害阶跃减半 → 同样的命中只剩 70；
- 4 格外：散布涨到 4 倍 → 弹丸真的会飞散，"远距离随机散射"；
- 第 10 波之前按 3 切不过去。

外加后台换弹：没拿在手上的枪自己补弹，但只有 0.5 倍速；换回来时
**接着装**而不是从头装 —— 从头装的话切枪就等于白等，后台换弹毫无意义。
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import src.systems.weapon as wmod
from src.core import config
from src.systems.weapon import (
    FALLBACK_WEAPONS,
    damage_at,
    effective_spread,
    spread_at,
)

SHOTGUN = FALLBACK_WEAPONS["shotgun"]
RIFLE = FALLBACK_WEAPONS["rifle"]
TIGHT = SHOTGUN.tight_range          # 4.0 米 = 2 格
WIDE = SHOTGUN.wide_range            # 8.0 米 = 4 格


# ---------------------------------------------------------------- 距离分段

def test_bands_line_up_with_the_ground_grid():
    """"2 格 / 4 格"必须真的等于格子数，不能是碰巧写对的 4.0 / 8.0。"""
    assert TIGHT == config.GROUND_GRID_CELL * 2
    assert WIDE == config.GROUND_GRID_CELL * 4


def test_damage_is_full_inside_tight_range():
    for distance in (0.0, 1.0, TIGHT):
        assert damage_at(SHOTGUN, distance) == SHOTGUN.damage


def test_damage_is_halved_beyond_tight_range():
    """阶跃，不是渐变 —— 边界两侧必须差一倍，中间不能有过渡值。"""
    assert damage_at(SHOTGUN, TIGHT + 0.001) == SHOTGUN.damage * SHOTGUN.far_damage
    for distance in (5.0, WIDE, 30.0):
        assert damage_at(SHOTGUN, distance) == SHOTGUN.damage * 0.5


def test_spread_does_not_grow_inside_tight_range():
    for distance in (0.0, 2.0, TIGHT):
        assert spread_at(SHOTGUN, distance) == pytest.approx(SHOTGUN.spread)


def test_spread_grows_between_bands_then_stops():
    mid = spread_at(SHOTGUN, (TIGHT + WIDE) / 2)
    assert SHOTGUN.spread < mid < SHOTGUN.spread * SHOTGUN.far_spread, "中间要有过渡"
    # 到 wide_range 就涨满，再远不再继续涨（否则 30 米外弹丸会飞到身后）。
    assert spread_at(SHOTGUN, WIDE) == pytest.approx(SHOTGUN.spread * SHOTGUN.far_spread)
    assert spread_at(SHOTGUN, 100.0) == pytest.approx(SHOTGUN.spread * SHOTGUN.far_spread)


def test_single_pellet_weapons_ignore_every_band():
    """手枪/步枪没有分段配置，行为和 M2 完全一致 —— 这条防止分段逻辑误伤老枪。"""
    for data in (RIFLE, FALLBACK_WEAPONS["pistol"]):
        assert spread_at(data, 0.0) == spread_at(data, 100.0) == data.spread
        assert damage_at(data, 0.0) == damage_at(data, 100.0) == data.damage


def test_aiming_keeps_a_cone_for_pellets():
    """霰弹枪瞄准时只收窄，不归零。

    归零的话 7 颗弹丸走同一条射线 = 一把精度无限、单发 140 的狙击枪，
    比步枪还强，距离分段整个失去意义。单发武器仍然严格为 0。
    """
    aimed = effective_spread(True, SHOTGUN)
    assert aimed == pytest.approx(SHOTGUN.spread * config.SHOTGUN_AIM_MULT)
    assert 0.0 < aimed < SHOTGUN.spread
    assert effective_spread(True, RIFLE) == 0.0


# ---------------------------------------------------------------- 数值契约

def test_point_blank_burst_vs_late_wave_grunts():
    """10 颗 × 20 = 200 的贴脸一发，和后期血量的对照。

    改版前的口径是 7 颗 × 20 = 140：第 20 波（180 血）就打不动了。
    现在 200 > 180，第 20 波仍是一枪；第 30 波（240 血）打不死，
    但带上"每 5 波 +1"（第 30 波单颗 26 → 260）又追了回来 ——
    成长曲线本身就是设计的一部分，两个方向都要钉住。
    """
    from src.systems.wave import hp_multiplier
    from src.systems.weapon import wave_damage_bonus

    burst = SHOTGUN.pellets * damage_at(SHOTGUN, 2.0)
    assert burst == 200
    assert burst > 60 * hp_multiplier(10)
    assert burst > 60 * hp_multiplier(20)
    assert burst < 60 * hp_multiplier(30), "不带成长时第 30 波打不死"
    late = SHOTGUN.pellets * damage_at(SHOTGUN, 2.0, wave_damage_bonus(30))
    assert late > 60 * hp_multiplier(30), "带上成长又追回来了"


def test_tight_range_burst_fits_inside_a_grunt():
    """4 米处的弹着圆半径必须明显小于小兵半宽，否则"全中"只是运气。"""
    radius = math.tan(math.radians(spread_at(SHOTGUN, TIGHT))) * TIGHT
    # grunt 的 radius 写在 enemies.json 里（0.5），这里只关心"弹着圆比它小一圈"。
    grunt_radius = 0.5
    assert radius < grunt_radius * 0.6, f"4 米处弹着圆半径 {radius:.2f} 太大了"


def test_far_burst_scatters_and_loses_damage():
    """8 米外：单颗伤害减半，而且弹着圆已经远大于小兵 —— "随机散射"。

    注意 10 颗**全中**只剩 100，这个数字本身不能说明"很弱"（第 1 波小兵才 60 血）。
    真正让它弱的是命中率：弹着圆半径 1.4 米，小兵半径 0.5 米，
    均匀采样下只有约 (0.5/1.4)² ≈ 13% 的弹丸能落在身上。
    """
    assert SHOTGUN.pellets * damage_at(SHOTGUN, WIDE) == 100
    radius = math.tan(math.radians(spread_at(SHOTGUN, WIDE))) * WIDE
    assert radius > 1.0, f"8 米处弹着圆半径只有 {radius:.2f} 米，散不开"
    landed = SHOTGUN.pellets * (0.5 / radius) ** 2
    assert landed < 2.0, f"8 米处预计还有 {landed:.1f} 颗能中，散射不够"


# ---------------------------------------------------------------- 解锁

class _StubState:
    def __init__(self, playing: bool = True) -> None:
        self.playing = playing

    def in_state(self, *_states) -> bool:
        return self.playing


class _StubRig:
    def __init__(self) -> None:
        self.aiming = False
        self.yaw = 0.0

    def set_aiming(self, value: bool) -> None:
        self.aiming = value

    def add_recoil(self, amount: float) -> None:
        pass


class _StubGun:
    def __init__(self) -> None:
        self.entity = SimpleNamespace()
        self.muzzle_world = SimpleNamespace()

    def update_flash(self, dt: float) -> None:
        pass

    def follow(self, player, rig=None) -> None:
        pass

    def show_flash(self) -> None:
        pass


class _StubCrosshair:
    def on_shot(self) -> None:
        pass

    def on_hit(self) -> None:
        pass


@pytest.fixture
def no_mouse(monkeypatch):
    """`WeaponSystem.update` 每帧读 mouse，测试里必须给个静止的假鼠标。"""
    monkeypatch.setattr(wmod, "mouse", SimpleNamespace(left=False, right=False))


def make_weapon(wave: int = 0):
    weapon = wmod.WeaponSystem(
        player=SimpleNamespace(world_position=None),
        rig=_StubRig(),
        state=_StubState(True),
        enemies=None,
        tracer=SimpleNamespace(spawn=lambda *a: None),
        crosshair=_StubCrosshair(),
        gun=_StubGun(),
    )
    weapon.waves = SimpleNamespace(wave=wave)
    return weapon


def test_slot_order_is_pistol_rifle_shotgun():
    """数字键 1/2/3 按下标映射，顺序由 weapons.json 决定 —— 顺序变了要立刻发现。"""
    weapon = make_weapon(30)
    assert weapon.slots == ["pistol", "rifle", "shotgun"]


def test_shotgun_is_locked_before_wave_ten():
    weapon = make_weapon(9)
    assert weapon.switch(2) is False
    assert weapon.weapon_name == config.DEFAULT_WEAPON
    assert weapon.locked_name == "shotgun"
    assert weapon.locked_left > 0.0, "要留提示，否则玩家以为键位坏了"


def test_shotgun_unlocks_at_wave_ten():
    weapon = make_weapon(10)
    assert weapon.switch(2) is True
    assert weapon.weapon_name == "shotgun"


def test_lock_hint_expires(no_mouse):
    weapon = make_weapon(5)
    weapon.switch(2)
    assert weapon.locked_name == "shotgun"
    # 一秒不够，三秒才够 —— 提示时长是 LOCKED_HINT_TIME。
    for _ in range(60):
        weapon.update(1 / 60)
    assert weapon.locked_name == "shotgun"
    for _ in range(180):
        weapon.update(1 / 60)
    assert weapon.locked_name == ""


def test_wave_zero_means_everything_locked():
    """没有波次来源时按第 0 波算，不能静默把锁定武器放出来。"""
    weapon = make_weapon(0)
    weapon.waves = None
    assert weapon.wave_number == 0
    assert weapon.switch(2) is False


# ---------------------------------------------------------------- 后台换弹

def test_background_reload_runs_at_half_speed():
    state = wmod.WeaponState(RIFLE)
    state.ammo = 0
    state.request_reload()
    full = state.reload_left

    state.update(1.0, reload_scale=config.BACKGROUND_RELOAD_SCALE)
    assert state.reload_left == pytest.approx(full - 1.0 * config.BACKGROUND_RELOAD_SCALE)

    # 前台仍然是一倍速：同一秒推进两倍的量。
    state.reload_left = full
    state.update(1.0)
    assert state.reload_left == pytest.approx(full - 1.0)


def test_background_weapon_reloads_without_being_held(no_mouse):
    """把枪收起来之后它自己补弹。不收的话切枪等于从空弹匣重新开始。

    用第 19 波而不是第 30 波：第 20 波会换大弹匣，`set_mag_size` 会顺手补上
    差额，那就不是在验"换弹速度"了。
    """
    weapon = make_weapon(19)
    pistol = weapon.states["pistol"]
    pistol.ammo = 3
    assert weapon.weapon_name == config.DEFAULT_WEAPON != "pistol"

    weapon.update(1 / 60)
    assert pistol.reloading, "收起状态也要开始换弹"

    # 0.5 倍速：1 秒只推进半秒的量，1.2 秒的换弹要 2.4 秒才完成。
    for _ in range(60):
        weapon.update(1 / 60)
    assert pistol.reloading, "一秒内不该换完（后台慢一半）"
    assert pistol.ammo == 3, "还没换完就不该有子弹"

    for _ in range(120):
        weapon.update(1 / 60)
    assert not pistol.reloading
    assert pistol.ammo == pistol.mag_size == 12


def test_switching_back_continues_the_same_reload(no_mouse):
    """换弹中切走再切回来，必须接着装，不能从头开始。"""
    weapon = make_weapon(30)
    rifle = weapon.states["rifle"]
    rifle.ammo = 5
    weapon.start_reload()
    assert rifle.reloading
    total = rifle.data.reload_time
    assert rifle.reload_left == pytest.approx(total)

    weapon.switch(0)                       # 换成手枪，步枪进后台
    for _ in range(30):                    # 0.5 秒真实时间 → 后台只走 0.25 秒
        weapon.update(1 / 60)
    remaining = rifle.reload_left
    assert 0.0 < remaining < total, "后台也要继续推进"
    assert remaining > total - 0.5, "后台是半速，不能和前台一样快"

    weapon.switch(1)                       # 换回步枪
    assert rifle.reloading, "切回来应该还在装"
    assert rifle.reload_left == pytest.approx(remaining), "不能重置成满时长"


# ---------------------------------------------------------------- 数据校验

def test_unknown_optional_field_is_rejected():
    """拼错的可选字段（tigh_range）只会静默失效，必须在加载阶段拦下来。"""
    raw = {
        "damage": 20, "fire_rate": 0.8, "mag_size": 7, "reload_time": 1.2,
        "spread": 2.5, "range": 40, "auto": False,
        "tigh_range": 4.0,          # 少了一个 t
    }
    with pytest.raises(ValueError, match="未知字段"):
        wmod._weapon_from("broken", raw)

# ---------------------------------------------------------------- 第 20 波换弹匣

def test_magazine_grows_at_wave_twenty(no_mouse):
    """三把枪一起升级：30→45 / 12→18 / 15→20。"""
    weapon = make_weapon(19)
    weapon.update(1 / 60)
    assert weapon.states["rifle"].mag_size == 30
    assert weapon.states["shotgun"].mag_size == 15

    weapon.waves = SimpleNamespace(wave=20)
    weapon.update(1 / 60)
    assert weapon.states["rifle"].mag_size == 45
    assert weapon.states["pistol"].mag_size == 18
    assert weapon.states["shotgun"].mag_size == 20


def test_magazine_upgrade_is_applied_once(no_mouse):
    """每帧同步必须是幂等的 —— 否则弹药会被一直往上补到爆。"""
    weapon = make_weapon(20)
    weapon.update(1 / 60)                 # 同步发生在 update 里
    rifle = weapon.states["rifle"]
    assert rifle.ammo == 45
    for _ in range(30):
        weapon.update(1 / 60)
    assert rifle.ammo == 45


def test_reset_puts_the_base_magazine_back(no_mouse):
    """重开一局要回到小弹匣，不然新一局开局就有 45 发。"""
    weapon = make_weapon(21)
    weapon.update(1 / 60)
    assert weapon.states["rifle"].mag_size == 45
    weapon.reset()
    assert weapon.states["rifle"].mag_size == 30
    assert weapon.states["rifle"].ammo == 30


def test_damage_bonus_follows_the_wave(no_mouse):
    weapon = make_weapon(14)
    assert weapon.damage_bonus == 2
    weapon.waves = SimpleNamespace(wave=30)
    assert weapon.damage_bonus == 6
    weapon.waves = None
    assert weapon.damage_bonus == 0
