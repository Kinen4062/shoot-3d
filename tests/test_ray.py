"""自建射线检测（src/core/ray.py）的回归约束。

这组测试锁住的是 M2 那次「瞄准了却打不中」的根因，别让它们回来：

1. 必须命中**最近**的那个实体 —— 旧实现按 `get_t()` 排序，
   而实测多个命中体的 t 全是同一个值（2.000），排序形同虚设；
2. 连续两次完全相同的调用结果必须一致 —— 旧实现第一次用的是
   上一次调用留下的射线位置，起点差了近 3 米；
3. 别的系统的探针（相机碰撞每帧三条）不能污染射击判定。
"""
import pytest
from ursina import Entity, destroy

from src.core.ray import raycast


@pytest.fixture
def spawn():
    made = []

    def _spawn(name, position, collider="box", **kwargs):
        e = Entity(model="cube", position=position, collider=collider, name=name, **kwargs)
        made.append(e)
        return e

    yield _spawn
    for e in made:
        destroy(e)


def test_hits_nearest_entity(spawn):
    spawn("far", (0, 0, 40))
    spawn("near", (0, 0, 20))

    hit = raycast((0, 0, 0), (0, 0, 1))

    assert hit.hit is True
    assert hit.entity.name == "near"
    assert hit.distance == pytest.approx(19.5, abs=0.3)


def test_repeated_call_is_stable(spawn):
    """同一原点同一方向连打两次，命中的必须是同一件事、同一个点。"""
    spawn("target", (2.87, 0.89, 10))     # 正好落在这条斜射线上
    args = ((3.7, 2.1, -5.0), (-0.055, -0.08, 0.995))

    first = raycast(*args, distance=100)
    second = raycast(*args, distance=100)

    assert first.hit is True and second.hit is True
    assert first.entity is second.entity
    assert tuple(first.world_point) == pytest.approx(tuple(second.world_point))


def test_other_probes_do_not_pollute_shot(spawn):
    """模拟相机碰撞的三条反向探针，之后再开枪仍要打中靶子。"""
    target = spawn("target", (0, 0, 10))

    for _ in range(3):
        raycast((0, 2, -6), (0, 0, -1), distance=6)     # 相机往身后探
    raycast((0, 2, 10), (0, 1, 0), distance=6)          # 再来一条完全不相干的

    hit = raycast((0, 1.7, -6), (0, -0.1, 1), distance=100)
    assert hit.hit is True
    assert hit.entity is target


def test_ignore_uses_identity_not_name(spawn):
    """两个同名实体，ignore 掉一个不能连带忽略另一个。"""
    a = spawn("twin", (0, 0, 10))
    spawn("twin", (0, 0, 20))

    hit = raycast((0, 0, 0), (0, 0, 1), ignore=[a])

    assert hit.hit is True
    assert hit.entity is not a
    assert hit.distance == pytest.approx(19.5, abs=0.3)


def test_distance_limit_is_respected(spawn):
    spawn("target", (0, 0, 30))

    assert raycast((0, 0, 0), (0, 0, 1), distance=10).hit is False
    assert raycast((0, 0, 0), (0, 0, 1), distance=40).hit is True


def test_entity_without_collider_is_not_hit(spawn):
    spawn("ghost", (0, 0, 10), collider=None)
    solid = spawn("solid", (0, 0, 20))

    hit = raycast((0, 0, 0), (0, 0, 1))

    assert hit.hit is True
    assert hit.entity is solid


def test_zero_direction_returns_miss(spawn):
    spawn("target", (0, 0, 10))
    assert raycast((0, 0, 0), (0, 0, 0)).hit is False
