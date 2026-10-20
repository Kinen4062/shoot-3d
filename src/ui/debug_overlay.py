"""M0 调试覆盖层：左上角显示状态、帧率、时钟。

它没有实现 update() 之外的引擎回调 —— 引擎只会把 update 分发给 Entity，
而这里是个普通对象，由 GameLoop 显式调用，符合「单主循环」约定。

**只在 PLAYING / PAUSED 显示**（见 `overlay_visible`）。
M5 加了主菜单和结算面板之后，这堆十几行的等宽文字会直接压在卡片上，
连 `[TAB] cycle …` 那行都和 START 按钮叠在一起 —— 实机截图里很难看，
而且玩家根本不该看到 fps / iframe 这些内部量。
"""
from __future__ import annotations

from typing import Any

from ursina import Text, application, color, mouse, window

from src.core import config
from src.core.clock import GameClock
from src.core.game_state import GameStateMachine, State


def overlay_visible(state) -> bool:
    """覆盖层该不该显示。抽成纯函数，方便断言边界而不启动窗口。

    菜单是"第一印象"，结算是"要截图"的那一屏，两者都要干净；
    暂停时保留，因为"暂停后数值不对"是最常见的排查入口。
    """
    return state.in_state(State.PLAYING, State.PAUSED)


class DebugOverlay:
    def __init__(self, state: GameStateMachine, clock: GameClock) -> None:
        self.state = state
        self.clock = clock
        # 下面这组是"后挂的指针"：`main.build_world` 在系统全部构造完之后
        # 才把它们塞进来。标注成 Any 是有意的 —— 覆盖层只读几个字段，
        # 给每个系统写一遍精确类型只会制造一个互相导入的网。
        self.loop: Any = None
        self.watch: Any = None           # 指向玩家，用来显示实时坐标
        self.rig: Any = None             # 指向相机，用来显示贴墙时的拉近距离
        self.weapon: Any = None          # 指向武器系统，显示弹药/换弹状态
        self.enemies: Any = None         # 指向敌人系统，显示存活靶子数
        self.waves: Any = None           # 指向波次系统，显示波数与阶段
        self.controller: Any = None      # 指向玩家控制器，显示闪避冷却
        self.score: Any = None           # 指向计分系统，显示分数/连击/最高分
        self.pickups: Any = None         # 指向道具系统，显示场上道具数
        self.audio: Any = None           # 指向音效系统，显示播放/去重/缺文件计数
        self.input_map: Any = None       # 指向输入路由，显示最近一次按键
        self._acc = config.DEBUG_REFRESH_INTERVAL   # 首帧就刷新一次

        # 订阅由 core/session.py 统一注册（重开会 clear_listeners）。

        # Text 默认 parent=camera.ui，origin 默认 (-.5, .5) 即左上角对齐，
        # 所以 position 直接给屏幕左上角再加一点内边距即可。
        self.label = Text(
            text="",
            position=(window.top_left.x + 0.015, window.top_left.y - 0.015),
            origin=(-0.5, 0.5),
            scale=1.1,
            color=color.white,
            visible=overlay_visible(state),   # 开局是 MENU，直接不显示
        )

    def update(self, dt: float) -> None:
        self._acc += dt
        if self._acc < config.DEBUG_REFRESH_INTERVAL:
            return
        self._acc = 0.0
        self._refresh()

    def on_state_changed(self, **kwargs) -> None:
        """状态一变立刻重画，不等下一个 0.25s 周期 ——
        否则按下键的瞬间看着像没反应。由 session 注册。"""
        if not hasattr(self, "label"):
            return
        self._acc = 0.0
        self._refresh()

    def _refresh(self) -> None:
        show = overlay_visible(self.state)
        if self.label.visible != show:
            self.label.visible = show
        if not show:
            # 不拼字符串：一屏二十行文本，MENU 下每 0.25 秒白拼一次毫无意义。
            return

        fps = 0.0
        if application.base is not None:
            fps = application.base.clock.getAverageFrameRate()

        frame = self.loop.frame if self.loop is not None else 0

        lines = [
            "M5 UI + AUDIO",
            f"state   : {self.state.state.name}",
            f"fps     : {fps:5.1f}",
            f"clock   : paused={self.clock.paused}  scale={self.clock.time_scale:.2f}  "
            f"elapsed={self.clock.elapsed:6.1f}s",
            f"frames  : {frame}",
        ]
        if self.watch is not None:
            # 玩家挂在场景根下，x/z 就是世界坐标；不走 world_position 少一次节点链计算。
            lines.append(f"pos     : x={self.watch.x:6.2f}  z={self.watch.z:6.2f}")
            if hasattr(self.watch, "health"):
                hp = self.watch.health
                shield = getattr(self.watch, "shield_charges", 0)
                lines.append(
                    f"hp      : {hp.hp:5.1f}/{hp.max_hp:.0f}  iframe={hp.iframe:4.2f}  "
                    f"shield={shield}"
                )
        if self.controller is not None:
            lines.append(f"dodge   : cd={self.controller.dodge_cd:4.2f}  n={self.controller.dodges}")
        if self.rig is not None:
            body = "off" if (self.watch is not None and not self.watch.visible) else "on"
            lines.append(f"camera  : dist={self.rig.distance:4.2f}  body={body}")
        if self.weapon is not None:
            gun = self.weapon.current
            mode = "RELOAD" if gun.reloading else ("AIM" if self.weapon.aiming else "HIP")
            # 霰弹枪的 spread 带距离分段，实际开火时会按目标距离放大到 4 倍，
            # 所以这里标上弹丸数 —— 看到 "x7" 就知道 spread 那一列是**基准值**。
            pellets = f"  x{gun.data.pellets}" if gun.data.pellets > 1 else ""
            lines.append(
                f"gun     : {self.weapon.weapon_name}  {gun.ammo:2d}/{gun.data.mag_size}  {mode}"
                f"  spread={self.weapon.spread_deg:.2f}deg{pellets}"
            )
            lines.append(
                f"shots   : {self.weapon.shots}  hits={self.weapon.hits}  "
                f"last={getattr(self.weapon, 'last_hit_name', '-')} "
                f"dmg={'Y' if getattr(self.weapon, 'last_damaged', False) else 'N'}"
            )
        if self.enemies is not None:
            counts = self.enemies.brain_counts() if hasattr(self.enemies, "brain_counts") else {}
            ai = " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "-"
            lines.append(f"targets : {self.enemies.alive_count} alive   ai: {ai}")
        if self.waves is not None:
            lines.append(
                f"wave    : {self.waves.wave}  {self.waves.phase}  "
                f"queued={len(self.waves.queue)}  skipped={self.waves.skipped}"
            )
        if self.score is not None:
            d = self.score.data
            lines.append(
                f"score   : {d.score}  x{d.combo_multiplier:.2f}  combo={d.combo}  "
                f"best={d.best_score}"
            )
            lines.append(
                f"combo   : {d.combo_timer:4.2f}s  resets={self.score.combo_resets}  "
                f"kills={d.kills}"
            )
        if self.pickups is not None:
            lines.append(
                f"loot    : {self.pickups.active_count} active  drops={self.pickups.spawned}  "
                f"got={self.pickups.collected}  expired={self.pickups.expired}"
            )
        if self.watch is not None and getattr(self.watch, "speed_boost_left", 0.0) > 0.0:
            lines.append(
                f"boost   : x{self.watch.speed_mult:.2f}  {self.watch.speed_boost_left:4.1f}s"
            )
        if self.audio is not None:
            # 这一行用来区分「没声音」的三种原因：
            # miss>0 = 文件没生成；plays>0 但听不到 = 系统音量；两者都 0 = 事件没触发。
            lines.append(
                f"audio   : plays={self.audio.plays}  dedup={self.audio.suppressed}  "
                f"miss={len(self.audio.missing)}  vol={self.audio.volume_percent}%"
            )

        # 输入诊断：右键 / AIM 不对时看这一行。
        # last 里有 'right mouse down' 说明事件到了，是状态同步的问题；
        # 按住右键但 R=0，说明引擎压根没派发，和游戏代码无关。
        last = self.input_map.last_key if self.input_map is not None else "-"
        lines.append(
            f"input   : last={last or '-'}  L={int(mouse.left)} R={int(mouse.right)}"
        )

        lines.append("")
        lines.append("[TAB] cycle  [ESC] pause  WASD move  LMB fire  RMB aim  R reload  "
                     "SPACE dodge  1/2 switch")
        self.label.text = "\n".join(lines)
