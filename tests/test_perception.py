"""感知层测试：视锥边界、遮挡回调的调用时机、8Hz 闸门。"""
from __future__ import annotations

import pytest

from src.systems.perception import (
    PerceiveThrottle,
    can_see,
    turn_toward,
    within_view_cone,
    wrap_deg,
    yaw_delta,
    yaw_to,
)

# ---------------------------------------------------------------- 角度工具

@pytest.mark.parametrize("raw, expected", [
    (0.0, 0.0),
    (180.0, 180.0),
    (-180.0, 180.0),          # 折回 (-180, 180]，负端并入正端
    (190.0, -170.0),
    (-190.0, 170.0),
    (360.0, 0.0),
    (720.0, 0.0),
])
def test_wrap_deg(raw, expected):
    assert wrap_deg(raw) == pytest.approx(expected)


def test_yaw_delta_takes_shortest_path():
    # 从 170° 转到 -170° 只差 20°，不是 340°
    assert yaw_delta(170.0, -170.0) == pytest.approx(20.0)
    assert yaw_delta(-170.0, 170.0) == pytest.approx(-20.0)


def test_yaw_to_matches_project_convention():
    # yaw=0 前方是 +Z（与 move_direction / tracer_angles 一致）
    assert yaw_to(0.0, 1.0) == pytest.approx(0.0)
    assert yaw_to(1.0, 0.0) == pytest.approx(90.0)
    assert yaw_to(0.0, -1.0) == pytest.approx(180.0)


def test_turn_toward_is_rate_limited_and_wraps():
    # 每帧最多 10 度，且不会越过目标
    assert turn_toward(0.0, 90.0, 10.0) == pytest.approx(10.0)
    assert turn_toward(85.0, 90.0, 10.0) == pytest.approx(90.0)
    # 跨越 ±180 时走短边
    assert turn_toward(175.0, -175.0, 10.0) == pytest.approx(-175.0)


# ---------------------------------------------------------------- 视锥

def test_within_view_cone_distance_and_angle():
    # 正前方 10 米
    assert within_view_cone(0.0, 10.0, 0.0, 25.0, 60.0)
    # 背后 10 米：看不见
    assert not within_view_cone(0.0, -10.0, 0.0, 25.0, 60.0)
    # 超出视距
    assert not within_view_cone(0.0, 30.0, 0.0, 25.0, 60.0)
    # 正好在 60° 边界上仍然算看见（容差内的边界行为必须稳定）
    import math
    x = math.sin(math.radians(60.0)) * 10.0
    z = math.cos(math.radians(60.0)) * 10.0
    assert within_view_cone(x, z, 0.0, 25.0, 60.0)
    # 61° 就看不见了
    x2 = math.sin(math.radians(61.0)) * 10.0
    z2 = math.cos(math.radians(61.0)) * 10.0
    assert not within_view_cone(x2, z2, 0.0, 25.0, 60.0)


def test_within_view_cone_ignores_angle_when_very_close():
    # 贴脸时不再看夹角，否则玩家站在背后会变成「隐形」
    assert within_view_cone(0.0, -0.5, 0.0, 25.0, 60.0)


# ---------------------------------------------------------------- 遮挡

def test_blocked_callback_only_called_after_cheap_checks():
    calls = []

    def blocked():
        calls.append(1)
        return True

    # 背后 -> 廉价检查就否掉，不该打射线
    assert not can_see(0.0, -10.0, 0.0, 25.0, 60.0, blocked=blocked)
    assert calls == []
    # 正前方 -> 才打射线，被挡则看不见
    assert not can_see(0.0, 10.0, 0.0, 25.0, 60.0, blocked=blocked)
    assert calls == [1]


def test_can_see_without_occlusion_is_true_in_cone():
    assert can_see(0.0, 10.0, 0.0, 25.0, 60.0)


# ---------------------------------------------------------------- 8Hz 闸门

def test_throttle_fires_immediately_then_at_fixed_rate():
    t = PerceiveThrottle(8.0)
    assert t.ready(0.0) is True, "首帧必须放行，否则开局 1/8 秒敌人全瞎"

    hits = 0
    for _ in range(60):                          # 正好 1 秒（60 帧）
        if t.ready(1.0 / 60.0):
            hits += 1
    # 1 秒 8 次，允许 ±1 的采样误差
    assert 7 <= hits <= 9


def test_throttle_does_not_starve_on_low_fps():
    # 30FPS 下仍然要接近 8Hz（不能因为帧率低就变慢）
    t = PerceiveThrottle(8.0)
    hits = 0
    for _ in range(30):
        if t.ready(1.0 / 30.0):
            hits += 1
    assert 7 <= hits <= 9
