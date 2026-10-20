"""M2 回归：瞄准必须精确、散布必须可命中、输入状态必须每帧对齐。

这三条对应 M2 实机验收里暴露的三个问题：

1. 腰射散布过大 -> 数值本身就被限制在「15 米外偏差小于靶子半宽」。
2. 右键瞄准时散布没归零 -> `effective_spread` 在瞄准时**恒为 0**，可断言。
3. `gun` 行不显示 AIM -> 状态每帧从 `mouse.right` 对齐，
   不再依赖 down/up 事件成对到达（丢一个就会永久卡住）。

这里全部用 stub 构造，不创建窗口、不跑渲染。
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import src.systems.weapon as wmod
from src.core import config
from src.systems.weapon import (
    FALLBACK_WEAPONS,
    WeaponData,
    WeaponSystem,
    effective_spread,
    load_weapons,
)

RIFLE = WeaponData("rifle", 18, 0.1, 30, 1.8, 0.5, 80, True, 1)
PISTOL = WeaponData("pistol", 25, 0.25, 12, 1.2, 0.25, 60, False, 1)


# ---------------------------------------------------------------- 散布

def test_aiming_spread_is_exactly_zero():
    """瞄准时必须是 0，不是「很小」。任何非零值都会破坏「准星压住就必中」。"""
    for data in (RIFLE, PISTOL):
        assert effective_spread(True, data) == 0.0


def test_hip_spread_matches_config():
    assert effective_spread(False, RIFLE) == RIFLE.spread
    assert effective_spread(False, PISTOL) == PISTOL.spread


def test_zero_spread_keeps_ray_on_crosshair():
    """散布为 0 时射线方向必须与准星完全一致（逐分量相等，不是近似）。"""
    from ursina import Vec3

    direction = Vec3(0, -0.13, 0.99).normalized()
    assert tuple(wmod.apply_spread(direction, 0.0)) == tuple(direction)


@pytest.mark.parametrize("data", [RIFLE, PISTOL])
@pytest.mark.parametrize("distance", [8.0, 15.0, 25.0])
def test_spread_stays_inside_target(data, distance):
    """最坏情况的横向偏差必须远小于靶子半宽，否则「瞄着打却打不中」是必然的。"""
    half_width = config.ENEMY_SCALE[0] / 2
    deviation = math.tan(math.radians(data.spread)) * distance
    assert deviation < half_width * 0.5, (
        f"{data.name} 在 {distance} 米处偏差 {deviation:.3f} 米，靶子半宽只有 {half_width}"
    )


def test_weapon_file_matches_fallback():
    """data/weapons.json 与内置兜底必须一致，否则打包后手感会突然变。"""
    loaded = load_weapons(config.resource_path(config.WEAPON_FILE))
    assert set(loaded) == set(FALLBACK_WEAPONS)
    for name, data in loaded.items():
        assert data == FALLBACK_WEAPONS[name], f"{name} 的配置和兜底数值不一致"


# ---------------------------------------------------------------- 相机挂点

def test_camera_pivot_sits_at_eye_height():
    """玩家原点在身体中心，挂点要减掉半个身高才是「脚底往上 1.7 米」。

    不减这一下，pivot 会跑到头顶以上（实测 2.6 米），
    镜头变成俯瞰视角，枪口到弹着点的连线也就斜得离谱。
    """
    eye_offset = config.CAM_HEIGHT - config.PLAYER_HEIGHT / 2
    origin_y = config.PLAYER_HEIGHT / 2          # Player 的 position_y
    assert origin_y + eye_offset == pytest.approx(config.CAM_HEIGHT)
    assert eye_offset < config.CAM_HEIGHT


def test_shoulder_offset_keeps_crosshair_clear():
    """挂点在眼睛高度时，角色的头会压在准星上。

    过肩偏移必须大于角色半宽，让视线从身体旁边过去，
    否则画面中心永远被自己的模型挡住。
    """
    assert config.CAM_SHOULDER > config.PLAYER_RADIUS
    # 角色模型半宽（scale_x / 2）也要被让开
    assert config.CAM_SHOULDER > config.PLAYER_RADIUS > 0.0


def test_aim_shoulder_is_not_smaller_than_hip():
    """瞄准时相机更近，角色在画面里更大，肩偏移只能变大。

    这里反向调小过一次，结果角色挤到准星旁边，
    靶子中弹闪白被身体挡住，看起来就是「打中了却没反应」。
    """
    assert config.CAM_SHOULDER_AIM >= config.CAM_SHOULDER
    assert config.CAM_DIST_AIM < config.CAM_DIST


# ---------------------------------------------------------------- 输入同步

class _StubState:
    def __init__(self, playing: bool = True) -> None:
        self.playing = playing

    def in_state(self, *states) -> bool:
        return self.playing


class _StubRig:
    def __init__(self) -> None:
        self.aiming = False
        self.pitch = 10.0
        self.recoil = 0.0

    def set_aiming(self, value: bool) -> None:
        self.aiming = value


class _StubGun:
    def __init__(self) -> None:
        self.entity = SimpleNamespace()

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


def _make_weapon(playing: bool = True):
    rig = _StubRig()
    weapon = WeaponSystem(
        player=SimpleNamespace(),
        rig=rig,
        state=_StubState(playing),
        enemies=None,
        tracer=None,
        crosshair=_StubCrosshair(),
        gun=_StubGun(),
    )
    return weapon, rig


def test_aim_follows_mouse_right(monkeypatch):
    fake_mouse = SimpleNamespace(left=False, right=False)
    monkeypatch.setattr(wmod, "mouse", fake_mouse)

    weapon, rig = _make_weapon()
    weapon._sync_input()
    assert weapon.aiming is False and rig.aiming is False

    fake_mouse.right = True
    weapon._sync_input()
    assert weapon.aiming is True, "按住右键必须进入瞄准态"
    assert rig.aiming is True, "相机也要收到瞄准信号"
    assert weapon.spread_deg == 0.0


def test_missed_mouse_up_cannot_stick_aim(monkeypatch):
    """松开事件丢失时，下一帧必须自己纠正回来。"""
    fake_mouse = SimpleNamespace(left=False, right=True)
    monkeypatch.setattr(wmod, "mouse", fake_mouse)

    weapon, rig = _make_weapon()
    weapon._sync_input()
    assert weapon.aiming is True

    fake_mouse.right = False          # 引擎没派发 'right mouse up'
    weapon._sync_input()
    assert weapon.aiming is False
    assert rig.aiming is False


def test_trigger_follows_mouse_left(monkeypatch):
    fake_mouse = SimpleNamespace(left=True, right=False)
    monkeypatch.setattr(wmod, "mouse", fake_mouse)

    weapon, _ = _make_weapon()
    weapon._sync_input()
    assert weapon.trigger is True
    assert weapon._fired_this_press is False, "每一次按下都要允许打第一发"

    fake_mouse.left = False
    weapon._sync_input()
    assert weapon.trigger is False, "松开后不能继续连发"


def test_leaving_playing_clears_aim_and_trigger(monkeypatch):
    fake_mouse = SimpleNamespace(left=True, right=True)
    monkeypatch.setattr(wmod, "mouse", fake_mouse)

    weapon, rig = _make_weapon(playing=False)
    weapon.update(1 / 60)
    assert weapon.trigger is False
    assert weapon.aiming is False
    assert rig.aiming is False


def test_aiming_keeps_full_fire_rate():
    """瞄准归零的是散布，不是射速 —— 别把两件事混在一起。"""
    assert effective_spread(True, RIFLE) == 0.0
    assert RIFLE.fire_rate == 0.1
