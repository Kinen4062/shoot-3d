"""状态机 + 单主循环。

为什么只有 GameLoop 一个 Entity 挂 update()：
Ursina 按场景图顺序给每个 Entity 分发 update()，玩家和敌人谁先跑无法保证。
如果各系统自己实现 update()，想要的「固定帧序」根本不会生效。
所以实体只暴露 update(dt) 普通方法，由 GameLoop 按显式顺序调用。
"""
from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Any, Protocol

from ursina import Entity, mouse

from . import event_bus


class System(Protocol):
    """主循环里每个系统的最小契约。"""

    def update(self, dt: float) -> None: ...


class State(Enum):
    MENU = "MENU"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    # 打完第 30 波（或打完 BOSS）后的"结算中"。
    #
    # 为什么不能借 GAME_OVER：`RunSession.on_state_changed` 用
    # `not player.alive` 判断该不该重开，而通关时玩家**还活着** ——
    # 借 GAME_OVER 会让"进入 BOSS 层"这条路走成"重开一局"。
    # 也不能借 PAUSED：暂停面板会同时弹出来，而且 ESC 会把它切回 PLAYING。
    CLEARED = "CLEARED"
    GAME_OVER = "GAME_OVER"


_ORDER = (State.MENU, State.PLAYING, State.PAUSED, State.CLEARED, State.GAME_OVER)


class GameStateMachine:
    def __init__(self, clock) -> None:
        self.clock = clock
        self._state: State | None = None
        self.set(State.MENU)

    @property
    def state(self) -> State:
        return self._state  # type: ignore[return-value]

    def in_state(self, *states: State) -> bool:
        return self._state in states

    def set(self, new: State) -> None:
        if new is self._state:
            return
        old = self._state
        if old is not None:
            self._on_exit(old)
        self._state = new
        self._on_enter(new)
        event_bus.emit("state_changed", old=old, new=new)

    # ---- 进入 / 离开各状态的副作用集中在这里 ----
    def _on_enter(self, s: State) -> None:
        if s is State.PLAYING:
            self.clock.paused = False
            mouse.locked = True
        elif s is State.PAUSED:
            self.clock.paused = True
            mouse.locked = False
        elif s is State.CLEARED:
            # 冻住世界再看结算面板。不暂停的话，玩家在面板上点"进入 BOSS 层"的
            # 那几秒里敌人 AI 和计分的生存时间还在跑 —— 他一边读结算一边掉血。
            self.clock.paused = True
            mouse.locked = False      # 面板上的按钮要能点
        elif s is State.MENU:
            self.clock.paused = False
            self.clock.time_scale = 1.0
            mouse.locked = False
        elif s is State.GAME_OVER:
            self.clock.time_scale = 0.3   # 死亡慢镜头
            mouse.locked = False

    def _on_exit(self, s: State) -> None:
        if s is State.PAUSED:
            self.clock.paused = False
        elif s is State.CLEARED:
            # 进 BOSS 层 = CLEARED → PLAYING。忘了这行，世界会永久静止，
            # 表现是"点了按钮、面板消失了、但什么都不动"。
            self.clock.paused = False
        elif s is State.GAME_OVER:
            self.clock.time_scale = 1.0

    # ---- 供输入层调用 ----
    def toggle_pause(self) -> None:
        if self._state is State.PLAYING:
            self.set(State.PAUSED)
        elif self._state is State.PAUSED:
            self.set(State.PLAYING)

    def debug_cycle(self) -> None:
        """M0 验收用：MENU -> PLAYING -> PAUSED -> CLEARED -> GAME_OVER -> MENU。

        加上 CLEARED 是为了能在不做完 30 波的前提下直接验通关面板：
        手打 30 波要二十多分钟，改动结算界面不可能每次都那么验。
        """
        idx = _ORDER.index(self._state)
        self.set(_ORDER[(idx + 1) % len(_ORDER)])


class GameLoop(Entity):
    """全项目唯一的 update() 入口。

    系统分两组，这是暂停能正常工作的关键：
    - systems    : 游戏逻辑，dt=0（暂停）时整组跳过
    - ui_systems : 界面，始终用 real_dt 更新

    如果只有一组，暂停时 dt=0 会把 HUD 一起冻住 ——
    屏幕停在暂停前的画面，玩家根本看不出已经暂停了。
    """

    def __init__(
        self,
        systems: list[System],
        clock,
        state: GameStateMachine,
        ui_systems: Sequence[Any] = (),
    ) -> None:
        super().__init__(name="game_loop")
        self.systems = list(systems)
        self.ui_systems = list(ui_systems)
        self.clock = clock
        self.state = state
        self.frame = 0

    def update(self) -> None:
        dt = self.clock.tick()

        if dt > 0.0:
            self.frame += 1
            for system in self.systems:
                system.update(dt)

        # UI 用未缩放的真实帧间隔：暂停 / 慢动作时也要正常刷新。
        real_dt = self.clock.real_dt
        for system in self.ui_systems:
            system.update(real_dt)

        # 暂停时也要 flush，否则延迟销毁队列会一直卡着。
        event_bus.flush()
