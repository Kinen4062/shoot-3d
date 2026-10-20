"""屏幕中心射线的契约。

它必须满足一件事：相机对着哪儿，射线就指向哪儿 ——
这样「准星压住靶子」才等价于「子弹打在靶子上」。
"""
import pytest
from ursina import Entity, Vec3, camera, destroy

from src.core import screen_ray


@pytest.fixture
def restore_camera():
    pos, rot = Vec3(camera.position), Vec3(camera.rotation)
    yield
    camera.position = pos
    camera.rotation = rot


def test_returns_unit_direction():
    _, direction = screen_ray.screen_center_ray()
    assert direction.length() == pytest.approx(1.0, abs=1e-5)


def test_direction_matches_camera_forward():
    _, direction = screen_ray.screen_center_ray()
    fwd = Vec3(camera.forward).normalized()
    assert direction.dot(fwd) == pytest.approx(1.0, abs=1e-5)


def test_centered_target_is_hit(restore_camera):
    """相机转向某处后，屏幕中心射线必须打中那一处的东西。"""
    from src.core.ray import raycast

    camera.position = (2.0, 3.0, -8.0)
    camera.rotation = (5, 20, 0)

    fwd = Vec3(camera.forward).normalized()
    target = Entity(
        model="cube",
        position=Vec3(camera.world_position) + fwd * 12,
        collider="box",
        name="center_target",
    )
    try:
        origin, direction = screen_ray.screen_center_ray()
        hit = raycast(origin, direction)
        assert hit.hit is True
        assert hit.entity is target
    finally:
        destroy(target)


def test_origin_is_camera_position(restore_camera):
    camera.position = (1.5, 2.5, -3.5)
    origin, _ = screen_ray.screen_center_ray()
    assert tuple(origin) == pytest.approx(tuple(Vec3(camera.world_position)))
