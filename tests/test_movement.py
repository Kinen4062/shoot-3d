"""M1 单元测试：输入方向到世界方向的换算。

这块逻辑最容易出错且最难肉眼发现，比如 yaw 符号反了会导致
「按 W 往左跑」，靠试玩很难描述清楚，用测试锁死最省事。
"""
import math

import pytest

from src.systems.player_control import move_direction


def length(v) -> float:
    return math.sqrt(v.x ** 2 + v.z ** 2)


def test_zero_input_returns_zero():
    d = move_direction(0, 0, 0)
    assert d.x == 0 and d.z == 0


def test_forward_at_yaw_zero_is_plus_z():
    d = move_direction(1, 0, 0)
    assert d.x == pytest.approx(0, abs=1e-6)
    assert d.z == pytest.approx(1)


def test_forward_at_yaw_90_is_plus_x():
    d = move_direction(1, 0, 90)
    assert d.x == pytest.approx(1)
    assert d.z == pytest.approx(0, abs=1e-6)


def test_forward_at_yaw_180_is_minus_z():
    d = move_direction(1, 0, 180)
    assert d.z == pytest.approx(-1)


def test_back_is_opposite_of_forward():
    f = move_direction(1, 0, 37)
    b = move_direction(-1, 0, 37)
    assert f.x == pytest.approx(-b.x)
    assert f.z == pytest.approx(-b.z)


def test_strafe_right_at_yaw_zero_is_plus_x():
    d = move_direction(0, 1, 0)
    assert d.x == pytest.approx(1)
    assert d.z == pytest.approx(0, abs=1e-6)


def test_diagonal_is_normalized():
    """斜着走不能比直着快，否则玩家会一直按 W+D。"""
    d = move_direction(1, 1, 0)
    assert length(d) == pytest.approx(1.0)
