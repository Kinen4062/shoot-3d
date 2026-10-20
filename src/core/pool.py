"""固定容量的对象池。

规则（规划书 §4.5）：

1. **不要用 `Entity.enabled` 做回收标志**。它的 setter 会 `stash()`，把实体
   从场景图整枝摘除；引擎的输入分发里还有 `if e.enabled is False: continue`。
   这类开关适合「整类关闭」，不适合高频复用。池用自己维护的 `alive`。
2. **回收必须把 `collider` 置空**。`visible = False` 只影响渲染，
   隐形碰撞体照样挡住 `raycast` —— 表现是「对着空地开枪，子弹被空气吃掉」。
3. **池空就丢弃，不扩容**。突发流量下创建对象反而更卡，而且会掩盖容量估算错误。

池对对象的要求只有四个可写属性：`alive / visible / collider / ignore`。
所以它不依赖 Ursina，测试里可以直接塞 SimpleNamespace。
"""
from __future__ import annotations

from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class EntityPool(Generic[T]):
    def __init__(self, factory: Callable[[], T], size: int) -> None:
        self.capacity = size
        self.overflows = 0
        self._free: list[T] = []
        for _ in range(size):
            obj = factory()
            self._park(obj)
            self._free.append(obj)

    # ---- 对象状态 ----
    @staticmethod
    def _park(obj: T) -> None:
        obj.alive = False           # type: ignore[attr-defined]
        obj.visible = False         # type: ignore[attr-defined]
        obj.collider = None         # type: ignore[attr-defined]
        obj.ignore = True           # type: ignore[attr-defined]

    @staticmethod
    def _wake(obj: T) -> None:
        obj.alive = True            # type: ignore[attr-defined]
        obj.visible = True          # type: ignore[attr-defined]
        obj.ignore = False          # type: ignore[attr-defined]

    # ---- 取用 / 归还 ----
    def acquire(self) -> T | None:
        if not self._free:
            self.overflows += 1
            return None
        obj = self._free.pop()
        self._wake(obj)
        return obj

    def release(self, obj: T) -> None:
        if not getattr(obj, "alive", False):
            return                  # 重复归还保护
        self._park(obj)
        self._free.append(obj)

    # ---- 统计（调试覆盖层 / 压测用）----
    @property
    def free_count(self) -> int:
        return len(self._free)

    @property
    def active_count(self) -> int:
        return self.capacity - len(self._free)
