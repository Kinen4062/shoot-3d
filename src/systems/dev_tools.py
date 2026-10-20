"""开发期调试工具：下一波 / 清场 / 无敌。

三个动作都是**直接改状态、绕过玩法**，所以默认不存在：只有
`MINITPS_DEV=1` 启动时 `main.build_world` 才会把它造出来并挂到按键上
（见 `config.DEVELOPMENT_TOOLS` 里的理由）。

**为什么需要"清场"而不只是"下一波"**：后期一波 30 只，单只小兵要 10 发步枪，
想看一眼第 28 波长什么样得先打完第 27 波 —— 二十多分钟的验证成本，
改一次配比就没法再验一次。清场把这件事压到几秒，也是目前唯一能反复
验证 BOSS 层的办法。

**为什么每个方法都返回值**：调试键最难受的失败不是"报错"，而是
"按下去什么都没发生"，然后分不清是键没派发、还是逻辑没跑。所以
波次号 / 清掉几只 / 无敌状态都回给调用方，覆盖层和日志各显示一份。
"""
from __future__ import annotations

from src.core.log import get_logger

log = get_logger("dev")


class DevTools:
    """把"作弊"收在一个小对象里，三个依赖都是后挂的指针。"""

    def __init__(self, waves, enemies, player) -> None:
        self.waves = waves
        self.enemies = enemies
        self.player = player

    @property
    def god(self) -> bool:
        """无敌当前是否开启。读取方是覆盖层，不缓存 —— 免得和玩家状态不同步。"""
        return bool(getattr(self.player, "god_mode", False))

    # ---------------------------------------------------------------- 动作
    def next_wave(self) -> int:
        """清掉当前波并立刻开始下一波，返回新的波次号。

        **必须先清场**：不清的话，上一波的残余和下一波会叠在同一屏，
        "第 N 波到底几只"这个数字就永远对不上 —— 那正是调试时要看的东西。
        """
        self._despawn_all()
        wave = self.waves.skip_to_next_wave()
        log.info(f"[DEV] next wave -> {wave}")
        return wave

    def clear_field(self) -> int:
        """瞬清场上所有敌人（含还没出场的队列），返回清掉几只。"""
        count = self.enemies.alive_count
        self._despawn_all()
        log.info(f"[DEV] clear field  {count} 只")
        return count

    def toggle_god(self) -> bool:
        """切换无敌，返回切换后的状态。"""
        self.player.god_mode = not self.god
        log.info(f"[DEV] god={'on' if self.god else 'off'}")
        return self.god

    # ---------------------------------------------------------------- 内部
    def _despawn_all(self) -> None:
        """让敌人直接消失，**不走 `take_damage`**。

        走伤害路径会连带产生击杀分、连击、飘字和掉落 —— 调试清场一旦
        改了分数，"这个波次的分数对不对"就失去了基准。`clear()` 只是
        把实体从列表摘掉并丢进延迟销毁队列，不产生任何游戏事件。
        """
        self.enemies.clear()
        self.waves.queue.clear()
