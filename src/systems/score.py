"""计分：击杀得分 + 连击倍率 + 生存时间 + 满血清波奖励。

**规则全部写在 `GameData` 里，这个系统只负责"什么时候调它"**。
把"连击怎么算倍率""倒计时怎么清零"放进数据对象，是为了让
`tests/test_score.py` 能在没有引擎、没有实体的情况下把边界钉死：
连击 3 秒清零、11 连封顶 2.0 倍、生存分按整秒累加而不是 `int(dt)`。

订阅关系由 `core/session.py` 统一注册，本文件不自己 `event_bus.on()`
—— 重开时一次 `clear_listeners()` 会清掉所有订阅，分散注册必漏。
"""
from __future__ import annotations

from src.core import config, event_bus, save
from src.core.game_data import GameData, get_data
from src.core.game_state import State


class ScoreSystem:
    def __init__(self, state, player=None, data: GameData | None = None) -> None:
        self.state = state
        self.player = player
        self.data = data if data is not None else get_data()

        # 生存分累加器。规划书 §5.6 特意写了"按 >= 1.0s 累加，别用 int(dt)"：
        # 用 int(dt) 时 dt=1/60 恒为 0，生存分永远加不上去；
        # 60FPS 下 dt 还可能略小于 1/60，累计一帧误差就会一直扣着。
        self._survival_acc = 0.0
        self.combo_resets = 0        # 连击清零次数（调试覆盖层可见）
        self.last_gain = 0           # 最近一次击杀得分，HUD 飘字用

    # ---- 重开 ----
    def reset(self) -> None:
        self.data.reset()
        self._survival_acc = 0.0
        self.combo_resets = 0
        self.last_gain = 0

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        if not self.state.in_state(State.PLAYING):
            return

        self.data.time_alive += dt

        self._survival_acc += dt
        while self._survival_acc >= 1.0:
            self._survival_acc -= 1.0
            self.data.score += config.SCORE_PER_SECOND

        if self.data.tick_combo(dt):
            self.combo_resets += 1
            event_bus.emit("combo_changed", combo=0, multiplier=1.0)

    # ---- 事件 ----
    def on_enemy_died(self, enemy=None, **_kwargs) -> int:
        """击杀结算。返回本此得分（测试断言用）。"""
        data = getattr(enemy, "data", None)
        base = int(getattr(data, "score", 0) or 0)

        self.data.push_combo()
        mult = self.data.combo_multiplier
        # int() 而不是 round()：向下取整，避免连击奖励出现"多给 1 分"的观感。
        gained = int(base * mult)
        self.data.score += gained
        self.data.kills += 1
        self.last_gain = gained

        event_bus.emit("combo_changed", combo=self.data.combo, multiplier=mult)
        # 飘字要知道"在哪儿飘"。位置**在这里取**：延迟队列里的 destroy
        # 会在这一帧的 flush 中执行，晚一步 enemy 就已经不在场景里了。
        event_bus.emit(
            "score_gained",
            amount=gained,
            position=getattr(enemy, "world_position", None),
            combo=self.data.combo,
        )
        return gained

    def on_wave_started(self, wave: int = 0, **_kwargs) -> None:
        # 只增不减：万一事件乱序也不该让波次数字倒退。
        self.data.wave = max(self.data.wave, int(wave))

    def on_wave_cleared(self, wave: int = 0, **_kwargs) -> None:
        """满血清空一波 +500（规划书 §5.6）。"""
        if self.player is None or not self.player.alive:
            return
        if self.player.hp < self.player.max_hp - 1e-6:
            return
        self.data.score += config.FULL_HP_WAVE_BONUS
        event_bus.emit("bonus_awarded", kind="full_hp_wave",
                       amount=config.FULL_HP_WAVE_BONUS)

    def on_pickup(self, kind: str | None = None, **_kwargs) -> None:
        if kind == "gem":
            self.data.score += config.GEM_SCORE

    def on_player_died(self, **_kwargs) -> None:
        """结算并落盘。最高分是唯一需要跨局保留的东西。"""
        self.record_run("run over")

    def on_run_finished(self, wave: int = 0, **_kwargs) -> None:
        """通关落盘（30 波清完 / BOSS 打完）。

        和 `on_player_died` 走同一段落盘逻辑，但**不能合并成一个 handler**：
        只有 `player_died` 会把玩家标记为死亡，通关时玩家还活着。
        另外通关不写"最高波次"以外的任何东西 —— 玩家可能在这里点
        「主菜单」直接收工，不落盘的话这一局的分数就白打了。
        """
        self.record_run(f"run cleared @ wave {wave}")

    def record_run(self, reason: str) -> None:
        stored = save.record_run(self.data.score, self.data.wave, self.data.kills)
        self.data.best_score = max(self.data.best_score, stored["best_score"])
        self.data.best_wave = max(self.data.best_wave, stored["best_wave"])
        print(
            f"[M4] {reason}  score={self.data.score}  kills={self.data.kills}  "
            f"wave={self.data.wave}  best={self.data.best_score}"
        )
