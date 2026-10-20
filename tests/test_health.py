"""血量 / 无敌帧测试。"""
from __future__ import annotations

import pytest

from src.core.health import Health


def test_damage_and_death():
    h = Health(100.0, iframe_time=0.5)
    assert h.take(30.0) is True
    assert h.hp == pytest.approx(70.0)
    assert h.alive

    h.tick(1.0)                      # 等无敌帧过去
    assert h.take(100.0) is True
    assert h.hp == 0.0
    assert not h.alive
    # 死后再打不该返回 True
    assert h.take(10.0) is False


def test_iframe_blocks_second_hit():
    h = Health(100.0, iframe_time=0.5)
    assert h.take(10.0) is True
    assert h.take(10.0) is False      # 无敌帧内
    assert h.hp == pytest.approx(90.0)

    h.tick(0.4)
    assert h.take(10.0) is False      # 还没到 0.5 秒
    h.tick(0.2)
    assert h.take(10.0) is True       # 过去了
    assert h.hp == pytest.approx(80.0)


def test_iframe_is_set_only_on_successful_hit():
    """被无敌帧吃掉的那一下不能刷新计时，否则连击会变成永久无敌。"""
    h = Health(100.0, iframe_time=0.5)
    h.take(10.0)
    h.tick(0.45)
    assert h.take(10.0) is False
    assert h.iframe == pytest.approx(0.05), "失败的一击不该重置无敌帧"
    h.tick(0.06)
    assert h.take(10.0) is True


def test_zero_iframe_means_no_immunity():
    h = Health(100.0, iframe_time=0.0)
    assert h.take(10.0) is True
    assert h.take(10.0) is True
    assert h.hp == pytest.approx(80.0)


def test_heal_is_capped():
    h = Health(100.0)
    h.take(60.0)
    assert h.heal(30.0) == pytest.approx(30.0)
    assert h.hp == pytest.approx(70.0)
    assert h.heal(50.0) == pytest.approx(30.0)
    assert h.hp == pytest.approx(100.0)


def test_reset_restores_everything():
    h = Health(100.0, iframe_time=0.5)
    h.take(999.0)
    h.reset()
    assert h.hp == 100.0 and h.alive and h.iframe == 0.0
