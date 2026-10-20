"""对象池的行为约束。

重点是两条容易被「优化掉」的规则：
回收必须清 collider；池空必须返回 None 而不是偷偷扩容。
"""
from types import SimpleNamespace

from src.core.pool import EntityPool


def make():
    return SimpleNamespace(alive=True, visible=True, collider="box", ignore=False)


def test_all_objects_are_parked_at_start():
    pool = EntityPool(make, 3)
    assert pool.free_count == 3
    assert pool.active_count == 0


def test_acquire_wakes_object():
    pool = EntityPool(make, 1)
    obj = pool.acquire()
    assert obj.alive is True
    assert obj.visible is True
    assert obj.ignore is False
    assert pool.active_count == 1


def test_release_clears_collider():
    """visible=False 不摘碰撞体，回收必须显式置空，否则会变成隐形障碍。"""
    pool = EntityPool(make, 1)
    obj = pool.acquire()
    pool.release(obj)
    assert obj.alive is False
    assert obj.visible is False
    assert obj.collider is None
    assert obj.ignore is True


def test_pool_does_not_grow_when_empty():
    pool = EntityPool(make, 2)
    first = pool.acquire()
    pool.acquire()
    assert pool.acquire() is None
    assert pool.overflows == 1

    pool.release(first)
    assert pool.acquire() is first


def test_double_release_is_ignored():
    pool = EntityPool(make, 1)
    obj = pool.acquire()
    pool.release(obj)
    pool.release(obj)
    assert pool.free_count == 1
