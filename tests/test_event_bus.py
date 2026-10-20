"""M0 单元测试：事件总线。

重点是「一个坏监听器不能拖垮全场」和「延迟队列必须等 flush」。
"""
import pytest

from src.core import event_bus


@pytest.fixture(autouse=True)
def clean_bus():
    event_bus.clear_listeners()
    yield
    event_bus.clear_listeners()


def test_on_emit():
    seen = []
    event_bus.on("hit", lambda **kw: seen.append(kw))
    event_bus.emit("hit", damage=10)
    assert seen == [{"damage": 10}]


def test_broken_listener_does_not_block_others():
    seen = []

    def bad(**kw):
        raise RuntimeError("boom")

    event_bus.on("x", bad)
    event_bus.on("x", lambda **kw: seen.append("ok"))
    event_bus.emit("x")
    assert seen == ["ok"]


def test_emit_without_listener_is_noop():
    event_bus.emit("nobody_listens", value=1)


def test_off():
    seen = []
    fn = lambda **kw: seen.append(1)
    event_bus.on("y", fn)
    event_bus.off("y", fn)
    event_bus.emit("y")
    assert seen == []


def test_off_unknown_is_safe():
    event_bus.off("never_registered", lambda: None)


def test_defers_until_flush():
    order = []
    event_bus.defer(order.append, "a")
    assert order == []
    event_bus.flush()
    assert order == ["a"]


def test_deferred_exception_is_isolated():
    order = []

    def boom():
        raise ValueError("bad deferred")

    event_bus.defer(boom)
    event_bus.defer(order.append, "after")
    event_bus.flush()
    assert order == ["after"]


def test_clear_listeners():
    seen = []
    event_bus.on("z", lambda **kw: seen.append(1))
    event_bus.defer(lambda: seen.append(2))
    event_bus.clear_listeners()
    event_bus.emit("z")
    event_bus.flush()
    assert seen == []
