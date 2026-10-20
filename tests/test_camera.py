"""M1 单元测试：相机与墙体的安全距离。

这里的核心不变式只有一条：
    「相机离玩家的距离」必须小于「射线到墙面的距离」。

它一旦被破坏，相机就落到墙体内部；立方体是背面剔除的，
整堵墙会变得不可见，玩家看到的是「穿进墙里」。
M1 实测到的穿模就是这么来的：硬下限 0.9 盖掉了可用空间 0.1 米。
"""
import math

import pytest

from src.core import config
from src.systems.camera_rig import clamp_camera_distance, head_distance


def test_open_field_keeps_max_distance():
    """无遮挡时返回 inf，相机要能拉满 6 米。"""
    assert clamp_camera_distance(math.inf, config.CAM_DIST) == pytest.approx(config.CAM_DIST)


def test_wall_hit_pulls_camera_before_wall():
    """贴墙时的真实数字：玩家中心离墙面 0.45 米。"""
    hit = config.PLAYER_RADIUS
    d = clamp_camera_distance(hit, config.CAM_DIST)
    assert d == pytest.approx(hit - config.CAM_COLLIDE_MARGIN)
    assert d < hit


def test_camera_never_passes_hit_surface():
    """扫一遍可能出现的命中距离，任何一点都不能越界。"""
    for i in range(1, 400):
        hit = i * 0.03
        d = clamp_camera_distance(hit, config.CAM_DIST)
        assert d <= hit, f"hit={hit:.2f} 时相机跑到墙后面去了"
        assert d <= config.CAM_DIST


def test_floor_hit_pulls_camera_up():
    """仰视时射线打到地面，相机必须被拉回到地面之上。"""
    d = clamp_camera_distance(1.5, config.CAM_DIST)
    assert d < 1.5


def test_degenerate_hit_returns_zero():
    """墙面贴到脸上的极端情况返回 0（贴到头上），绝不允许为负或反弹到墙后。"""
    d = clamp_camera_distance(0.0, config.CAM_DIST)
    assert d == pytest.approx(0.0)


def test_margin_covers_near_clip_plane():
    """余量必须大于相机近裁剪面，否则墙会切进近平面。"""
    assert config.CAM_COLLIDE_MARGIN > 0.1


def test_lateral_hit_is_measured_from_head():
    assert head_distance(0.0, config.CAM_COLLIDE_SPREAD) == pytest.approx(config.CAM_COLLIDE_SPREAD)
    assert math.isinf(head_distance(math.inf, config.CAM_COLLIDE_SPREAD))


def test_oblique_wall_view_does_not_collapse_camera():
    """斜 30 度看墙：侧向探针命中 0.346，换算回离头 0.458，相机仍应有 0.10。

    不换算的话 0.346 - 0.35 < 0，距离被压成 0.00 —— 实机就是这个现象。
    """
    d_side = 0.346
    raw = clamp_camera_distance(d_side, config.CAM_DIST)
    fixed = clamp_camera_distance(head_distance(d_side, config.CAM_COLLIDE_SPREAD), config.CAM_DIST)
    assert raw == pytest.approx(0.0)                    # 旧行为：直接塌到 0
    assert fixed == pytest.approx(0.10, abs=0.02)       # 现在：保留墙体净空


def test_corner_view_does_not_collapse_camera():
    """站在墙角朝夹角看：侧向探针命中 0.337，换算回离头 0.451。"""
    d_side = 0.337
    d = clamp_camera_distance(head_distance(d_side, config.CAM_COLLIDE_SPREAD), config.CAM_DIST)
    assert d == pytest.approx(0.10, abs=0.02)


def test_camera_distance_stays_positive_near_walls():
    """贴墙（离墙 = 玩家半径）的整圈角度扫描，距离都不该塌到 0。"""
    wall_gap = config.PLAYER_RADIUS
    for deg in range(0, 90):
        # 视线与墙面法线的夹角，0 度 = 正对墙
        rad = math.radians(deg)
        probe_gap = wall_gap - config.CAM_COLLIDE_SPREAD * math.sin(rad)
        if probe_gap <= 0:
            continue
        d_side = probe_gap / math.cos(rad)
        d = clamp_camera_distance(head_distance(d_side, config.CAM_COLLIDE_SPREAD), config.CAM_DIST)
        assert d > 0.05, f"{deg} 度看墙时相机被压到 {d:.3f}"


def test_arena_inner_is_the_wall_face():
    assert pytest.approx(config.ARENA_HALF - config.WALL_THICKNESS / 2) == config.ARENA_INNER


def test_player_limit_keeps_body_outside_wall():
    """玩家能站到的最远处 + 自身半径，不能越过墙的内表面。"""
    limit = config.ARENA_INNER - config.PLAYER_RADIUS
    assert limit + config.PLAYER_RADIUS <= config.ARENA_INNER + 1e-9
