"""血量 + 无敌帧（纯逻辑，不依赖引擎）。

单独抽出来是因为无敌帧有两个反直觉的地方，都是靠测试钉住的：

1. **无敌帧必须在「受击成功」时才起算**。如果写成「每帧刷新」，
   连续命中会变成永远无敌；
2. **无敌帧期间的受击要能被区分出来**。近战兵贴脸时每秒打 3 下，
   如果静默丢弃，玩家看到的是「敌人贴着我但我不掉血」，会以为敌人坏了。
   所以 `take()` 返回 False，上层可以据此播「叮」的格挡反馈。

`iframe_time = 0` 时没有任何无敌帧（测试和纯数值场景用）。
"""
from __future__ import annotations


class Health:
    def __init__(self, max_hp: float, iframe_time: float = 0.0) -> None:
        self.max_hp = float(max_hp)
        self.hp = float(max_hp)
        self.iframe_time = float(iframe_time)
        self.iframe = 0.0
        self.alive = True

    def tick(self, dt: float) -> None:
        if self.iframe > 0.0:
            self.iframe = max(0.0, self.iframe - dt)

    @property
    def invulnerable(self) -> bool:
        return self.iframe > 0.0

    def take(self, amount: float) -> bool:
        """扣血。返回 False 表示这一下被无敌帧吃掉（或已经死了）。"""
        if not self.alive or self.iframe > 0.0 or amount <= 0.0:
            return False
        self.hp -= amount
        if self.hp <= 0.0:
            self.hp = 0.0
            self.alive = False
        if self.iframe_time > 0.0:
            self.iframe = self.iframe_time
        return True

    def heal(self, amount: float) -> float:
        """回血，返回实际回复量（M4 医疗包用）。"""
        if not self.alive:
            return 0.0
        before = self.hp
        self.hp = min(self.max_hp, self.hp + amount)
        return self.hp - before

    def reset(self) -> None:
        self.hp = self.max_hp
        self.iframe = 0.0
        self.alive = True
