"""弹丸命中测试：线段 vs 球体。

为什么必须按线段测：22 m/s × 1/60 s ≈ 0.37 米/帧，
比玩家半径（0.45）还接近 —— 只测帧末位置会整颗穿过去。
"""
from __future__ import annotations

from src.systems.projectile_system import segment_hits_sphere


def test_segment_through_sphere_center_hits():
    assert segment_hits_sphere((0, 0, 0), (0, 0, 10), (0, 0, 5), 0.5)


def test_segment_passing_beside_sphere_misses():
    assert not segment_hits_sphere((0, 0, 0), (0, 0, 10), (2, 0, 5), 0.5)


def test_high_speed_segment_catches_tunneling():
    """一帧跨过整个球体：只测终点会漏，测线段才能抓住。"""
    start = (0, 0, 0)
    end = (0, 0, 5)                  # 一步跨 5 米，球在中间
    center = (0, 0, 2.5)
    assert segment_hits_sphere(start, end, center, 0.4)
    # 只测终点（错误做法）会判定没命中 —— 用这个断言把它钉住
    dx, dy, dz = center[0] - end[0], center[1] - end[1], center[2] - end[2]
    assert dx * dx + dy * dy + dz * dz > 0.4 * 0.4


def test_segment_clamps_to_endpoints():
    # 球在起点后方，线段不该反向延伸去命中
    assert not segment_hits_sphere((0, 0, 0), (0, 0, 10), (0, 0, -3), 1.0)
    # 球正好在起点上
    assert segment_hits_sphere((0, 0, 0), (0, 0, 10), (0, 0, 0), 0.5)


def test_zero_length_segment_behaves_like_point():
    assert segment_hits_sphere((1, 1, 1), (1, 1, 1), (1, 1, 1), 0.1)
    assert not segment_hits_sphere((1, 1, 1), (1, 1, 1), (3, 1, 1), 0.1)


def test_radius_boundary():
    # 距离刚好等于半径算命中（<=）
    assert segment_hits_sphere((0, 0, 0), (0, 0, 10), (0.5, 0, 5), 0.5)
    assert not segment_hits_sphere((0, 0, 0), (0, 0, 10), (0.51, 0, 5), 0.5)
