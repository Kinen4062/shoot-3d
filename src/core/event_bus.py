"""极简发布订阅 + 延迟调用队列。

两条硬规则：
1. 监听器抛异常必须被吃掉并记录，否则一个坏监听器会打断主循环，
   而 emit 通常发生在「敌人死亡」这类关键时刻 —— 计分 / 音效 / 掉落会一起失效。
2. 禁止在遍历实体列表时直接 destroy()，一律 defer()，由主循环末尾统一 flush()。
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any

from src.core.log import get_logger

log = get_logger("events")

_listeners: dict[str, list[Callable[..., Any]]] = {}
_deferred: deque[tuple[Callable[..., Any], tuple, dict]] = deque()

# flush 单帧上限：防止某个排队函数又 defer 自己，形成无限循环卡死游戏。
_MAX_FLUSH_PER_FRAME = 1000


def on(event: str, fn: Callable[..., Any]) -> None:
    """订阅。重开时务必配合 clear_listeners()，否则监听器会翻倍。"""
    _listeners.setdefault(event, []).append(fn)


def off(event: str, fn: Callable[..., Any]) -> None:
    handlers = _listeners.get(event)
    if handlers and fn in handlers:
        handlers.remove(fn)


def emit(event: str, **kwargs: Any) -> None:
    # list() 取快照：允许监听器在回调里退订自己。
    for fn in list(_listeners.get(event, ())):
        try:
            fn(**kwargs)
        except Exception:
            log.exception("[event:%s] listener failed", event)


def defer(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    _deferred.append((fn, args, kwargs))


def flush() -> None:
    """在主循环末尾调用。暂停时也要跑，否则队列会卡死。"""
    budget = _MAX_FLUSH_PER_FRAME
    while _deferred:
        if budget <= 0:
            log.error("deferred queue exceeded %d calls in one frame, dropped %d",
                      _MAX_FLUSH_PER_FRAME, len(_deferred))
            _deferred.clear()
            break
        budget -= 1
        fn, args, kwargs = _deferred.popleft()
        try:
            fn(*args, **kwargs)
        except Exception:
            log.exception("deferred call failed: %r", fn)


def clear_listeners() -> None:
    """重开一局时调用。它同时清空延迟队列，避免上一局的销毁操作打到新对象。"""
    _listeners.clear()
    _deferred.clear()
