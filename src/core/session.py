"""一局的生命周期：事件订阅注册 + 重开契约 + 死亡结算。

**这是 M4 最容易被低估的一块。** 规划书 §5.6 只写了一句
「每个系统都必须实现 `reset()`，同时 `clear_listeners()` + 清空实体 + 归还池对象」，
但落到代码上有一个致命的顺序问题：

    `clear_listeners()` 会**连延迟队列一起清空**（`event_bus.py:64-68`）。

而 `EnemySystem.clear()` 是往延迟队列里 `defer(destroy, enemy)` 的。
如果先 clear_listeners 再 clear 敌人，那些 destroy 会被下一轮的 clear 静默吞掉 ——
上一局的敌人**永远不会被销毁**，它们留在场景里，新一局的敌人和它们重叠在一起。
表现是"重开后怪越来越多、打不死、还站在出生点"。

所以 `reset_run()` 的顺序被固定成：

    flush() → 各系统清场 → flush() → register_listeners()

第 1 次 flush 清上一帧遗留，第 2 次 flush 把清场排下的 destroy 真正执行掉，
最后注册时延迟队列已经是空的，`clear_listeners()` 清掉的是"空队列"，安全。

**为什么订阅必须集中在这里**

事件总线没有"按 owner 反注册"的能力（只有 `off(event, fn)`，
要求调用方自己记住每个 handler 引用）。系统各自在 `__init__` 里
`event_bus.on()` 看起来很内聚，但重开时一次 `clear_listeners()`
会把它们全部清掉，而且**没有任何报错** —— 表现是：

- 开枪不再惊动敌人（听觉静默失效）
- 受击不抖屏、不红闪
- `player_died` 不触发 → **玩家死了游戏也不会结束**

这些症状都极难和"逻辑写错了"区分开。集中注册让"重开后订阅一定还在"
变成一条可以断言的验收，而不是靠记忆。
"""
from __future__ import annotations

from src.core import config, event_bus
from src.core.game_state import State


class RunSession:
    def __init__(
        self,
        *,
        state,
        player,
        controller,
        rig,
        weapon,
        enemies,
        projectiles,
        pickups,
        waves,
        score,
        tracer,
        overlay=None,
        crosshair=None,
        damage_flash=None,
        wave_banner=None,
        hud=None,
        combat_hud=None,
        audio=None,
        main_menu=None,
        pause_menu=None,
        game_over=None,
        score_popup=None,
        clear_panel=None,
    ) -> None:
        self.state = state
        self.player = player
        self.controller = controller
        self.rig = rig
        self.weapon = weapon
        self.enemies = enemies
        self.projectiles = projectiles
        self.pickups = pickups
        self.waves = waves
        self.score = score
        self.tracer = tracer

        self.overlay = overlay
        self.crosshair = crosshair
        self.damage_flash = damage_flash
        self.wave_banner = wave_banner
        self.hud = hud
        self.combat_hud = combat_hud
        self.audio = audio
        self.main_menu = main_menu
        self.pause_menu = pause_menu
        self.game_over = game_over
        self.score_popup = score_popup
        self.clear_panel = clear_panel

        self.restarts = 0
        # 进 CLEARED 的是哪一次通关："run" = 30 波清完（还能进 BOSS 层），
        # "boss" = BOSS 也打完了。面板靠它决定要不要显示「进入 BOSS 层」。
        self.cleared_kind = "run"
        # 本局开始时的历史最高分。结算面板的「NEW RECORD」必须和它比，
        # 而不是和「当前 best」比 —— 死亡事件里计分系统已经把 best 更新完了。
        self._best_at_start = getattr(getattr(score, "data", None), "best_score", 0)
        # 订阅之后由 register_listeners() 一次性挂上。构造时不做，
        # 让调用方能先把所有对象装配完（比如 overlay 的引用要后设）。
        self.listener_count = 0

    # ---------------------------------------------------------------- 订阅
    def register_listeners(self) -> int:
        """清空并重新注册全部订阅。**幂等**：调多少次结果都一样。

        先 clear 再注册是刻意的：如果只注册不 clear，
        第二次调用会让每个监听器存在两份 —— 伤害翻倍、分数翻倍、
        敌人一次受击触发两次死亡动画。这类 bug 极难从现象反推原因。
        """
        event_bus.clear_listeners()
        count = 0

        def sub(event: str, fn) -> None:
            nonlocal count
            if fn is None:
                return
            event_bus.on(event, fn)
            count += 1

        # ---- 战斗 ----
        sub("noise", self.enemies.on_noise)             # 枪声 → 敌人听觉
        sub("enemy_died", self.score.on_enemy_died)     # 击杀 → 计分/连击
        sub("enemy_died", self.pickups.on_enemy_died)   # 击杀 → 掉落
        sub("player_hurt", self._on_player_hurt)        # 受击 → 相机抖动
        sub("player_hurt", getattr(self.damage_flash, "on_hurt", None))
        sub("shield_absorbed", getattr(self.damage_flash, "on_shield", None))
        sub("score_gained", getattr(self.score_popup, "on_score_gained", None))
        sub("pickup", self.score.on_pickup)             # 分数宝石
        sub("pickup", getattr(self.hud, "on_pickup", None))
        sub("pickup", getattr(self.damage_flash, "on_pickup", None))

        # ---- 波次 ----
        sub("wave_started", self.score.on_wave_started)
        sub("wave_cleared", self.score.on_wave_cleared)
        sub("wave_started", getattr(self.wave_banner, "on_wave_started", None))
        sub("wave_cleared", getattr(self.wave_banner, "on_wave_cleared", None))

        # ---- 状态 / 结算 ----
        sub("player_died", self.score.on_player_died)   # 写最高分
        sub("player_died", self.on_player_died)         # 切 GAME_OVER
        # 通关：30 波清完 / BOSS 打完。两条都落盘，否则"打完就回主菜单"
        # 这条路上的分数永远不会写进存档。
        sub("run_cleared", self.score.on_run_finished)
        sub("boss_cleared", self.score.on_run_finished)
        sub("run_cleared", self.on_run_cleared)
        sub("boss_cleared", self.on_run_cleared)
        sub("state_changed", self.on_state_changed)     # 重开 + 界面切换
        sub("state_changed", getattr(self.overlay, "on_state_changed", None))
        sub("state_changed", getattr(self.hud, "on_state_changed", None))

        # ---- M5 音效（订阅同一批事件，武器/敌人层不认识音频）----
        sub("noise", getattr(self.audio, "on_shot", None))
        sub("enemy_hit", getattr(self.audio, "on_hit", None))
        sub("enemy_died", getattr(self.audio, "on_kill", None))
        sub("reload_started", getattr(self.audio, "on_reload", None))
        sub("player_hurt", getattr(self.audio, "on_hurt", None))
        sub("shield_absorbed", getattr(self.audio, "on_shield", None))
        sub("pickup", getattr(self.audio, "on_pickup", None))
        sub("wave_started", getattr(self.audio, "on_wave", None))
        sub("player_died", getattr(self.audio, "on_game_over", None))

        self.listener_count = count
        return count

    # ---------------------------------------------------------------- 事件
    def _on_player_hurt(self, **_kwargs) -> None:
        self.rig.add_shake(config.SHAKE_ON_HIT)

    def on_player_died(self, **_kwargs) -> None:
        # 只切状态。分数落盘由 ScoreSystem 负责 —— 它才知道分数是多少。
        self.state.set(State.GAME_OVER)

    def on_run_cleared(self, wave: int = 0, **_kwargs) -> None:
        """30 波清完 或 BOSS 打完 → 进结算。

        用 `wave` 区分两次通关，而不是各自写一个 handler：
        两个事件的参数完全一样，分开写只会有两份几乎相同的代码。
        `BOSS_WAVE` 在这里 import 会绕成循环依赖（wave 系统已经
        import 了 game_state），所以直接比常量值 —— 判据是"打过 30 波了没有"。
        """
        from src.systems.wave import MAX_WAVE

        self.cleared_kind = "run" if int(wave) <= MAX_WAVE else "boss"
        self.state.set(State.CLEARED)

    def enter_boss_layer(self) -> bool:
        """通关面板上的「进入 BOSS 层」。返回 False 表示当下没这个选项。

        三件事的顺序不能换：
        1. 先让波次系统把 wave 推到 30（`start_boss_layer` 里 +1 变 31）。
           放到 `set(PLAYING)` 之后的话，中间会有一帧 WaveSystem 还停在
           CLEARED 阶段，虽然不致命，但下一帧才会刷怪这件事很难解释。
        2. 再切 PLAYING。此时玩家还活着，`on_state_changed` 不会触发 reset_run
           —— 这正是我们要的：BOSS 层是这一局的延续，不是新的一局。
        3. 计分系统的 wave 数字由 `wave_started` 事件推进，不用手动同步。
        """
        if self.state.state is not State.CLEARED or self.cleared_kind != "run":
            return False
        if not self.waves.start_boss_layer():
            return False
        self.state.set(State.PLAYING)
        return True

    def on_state_changed(self, old=None, new=None, **_kwargs) -> None:
        """从"死亡状态"回到 PLAYING 时自动重开一局。

        判据是 `not player.alive` 而不是 `old is GAME_OVER`：
        TAB 循环路径是 GAME_OVER → MENU → PLAYING，中间隔了一站，
        按 old 判断会漏掉。而"玩家已死"这个条件在 MENU 阶段进来时同样成立，
        正好覆盖两条路径。
        """
        if new is State.PLAYING and not self.player.alive:
            self.reset_run()
        self.sync_ui(new)
        # BGM 也跟着状态走：暂停时停、其它状态恢复。
        # 放在这里而不是订阅 state_changed，是为了不增加监听器数量 ——
        # 重开契约、探针断言都盯着那个数字。
        if self.audio is not None:
            self.audio.on_state_changed(new)

    # ---------------------------------------------------------------- 界面
    def sync_ui(self, state=None) -> None:
        """按状态显隐三块界面。界面自己不订阅事件，全部由这里驱动。"""
        state = self.state.state if state is None else state

        if self.main_menu is not None:
            if state is State.MENU:
                if hasattr(self.main_menu, "refresh_best"):
                    self.main_menu.refresh_best(getattr(self.score, "data", None))
                self.main_menu.show()
            else:
                self.main_menu.hide()

        if self.pause_menu is not None:
            if state is State.PAUSED:
                self.pause_menu.show()
            else:
                self.pause_menu.hide()

        if self.game_over is not None:
            if state is State.GAME_OVER:
                self.game_over.show(self.summary())
            else:
                self.game_over.hide()

        if self.clear_panel is not None:
            if state is State.CLEARED:
                self.clear_panel.show(self.summary(), self.cleared_kind)
            else:
                self.clear_panel.hide()

    def summary(self):
        """把整局数据整理成结算面板要的字段。没有计分系统时返回 None。"""
        from src.ui.menus import build_summary

        data = getattr(self.score, "data", None)
        if data is None:
            return None
        return build_summary(
            score=data.score,
            kills=data.kills,
            seconds=data.time_alive,
            wave=data.wave,
            best=data.best_score,
            best_at_start=self._best_at_start,
        )

    def return_to_menu(self) -> None:
        """暂停面板 / 结算面板的「MAIN MENU」。"""
        self.state.set(State.MENU)

    def start_run(self) -> None:
        """主菜单 START / 结算 RESTART 的唯一入口。

        三种情况都要正确处理，否则会出现"点 START 没反应"或"重开两次"：
        1. 玩家已死（结算后重开）：`set(PLAYING)` 会触发 `on_state_changed`
           → `reset_run()`，所以这里**不能**再调一次，否则波次会瞬间推进两次；
        2. 玩家还活着（暂停 → 主菜单 → START）：`set(PLAYING)` 不会触发 reset，
           必须手动清场，否则会带着上一局的敌人开局；
        3. 已经在对局中（理论上不可达）：同样按重开处理。
        """
        was_alive = self.player.alive
        self.state.set(State.PLAYING)
        if was_alive:
            self.reset_run()

    def request_restart(self) -> bool:
        """兼容入口：等价于 `start_run()`，返回 False 表示什么都没做。"""
        if self.state.in_state(State.PLAYING) and self.player.alive:
            return False        # 已经在玩，不重复重开
        self.start_run()
        return True

    # ---------------------------------------------------------------- 重开
    def reset_run(self) -> None:
        """把整个世界恢复到"第一秒"。

        顺序不能改，原因见模块 docstring 的表格。
        """
        event_bus.flush()          # ① 跑掉上一帧遗留

        self.player.reset()
        self.controller.reset()
        self.rig.reset()
        self.weapon.reset()
        self.tracer.clear()
        self.enemies.clear()       # 这里会往延迟队列排 destroy
        self.projectiles.clear()
        self.pickups.reset()
        self.waves.reset()
        self.score.reset()

        # 重开时重新记一次"开局最高分"，结算面板的 NEW RECORD 靠它判断。
        self._best_at_start = getattr(getattr(self.score, "data", None), "best_score", 0)
        self.cleared_kind = "run"

        for ui in (self.crosshair, self.damage_flash, self.wave_banner,
                   self.hud, self.combat_hud, self.score_popup):
            # focus_watch 不在 session 里持有：它不需要重开契约，
            # 每帧自己比对焦点变化，`reset()` 由它自己的实例负责。
            reset = getattr(ui, "reset", None)
            if reset is not None:
                reset()

        if self.audio is not None:
            self.audio.reset()     # 只清去重窗口，不重载 clip

        event_bus.flush()          # ② 立刻执行 ① 之后排下的 destroy
        self.register_listeners()  # ③ 重新挂订阅（内部会 clear）
        self.restarts += 1

        if self.overlay is not None:
            self.overlay.on_state_changed()     # 立刻重画，别等 0.25s 节流
        print(f"[M5] run reset  (restarts={self.restarts}, listeners={self.listener_count})")
