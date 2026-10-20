"""三个界面：主菜单 / 暂停面板 / 结算面板。

**它们只负责"长什么样"和"点了做什么"，不碰游戏状态**

按钮回调由 `main.py` 传进来（`session.start_run()` / `state.set(MENU)` / `application.quit`）。
界面层不认识玩家、敌人、波次 —— 这样重开流程仍然只有 `RunSession` 一处真相源，
不会出现"菜单里点重开走一套、TAB 循环走另一套"的分裂。

**显示/隐藏由 `RunSession.on_state_changed` 驱动**，界面自己不订阅事件。
原因和 M4 一致：`clear_listeners()` 会清掉所有订阅，界面如果自己订阅，
重开后就再也不响应了，而且完全没有报错。

`root.enabled = False` **不会**隐藏节点（ursina 8 里 `enabled` 只是个标志位，
`stash()` 只对 `is_singleton` 判为 False 的实体生效，普通 Entity 不满足）。
所以显隐一律走 `visible`，并且隐藏时把按钮的 `collider` 一起摘掉 ——
没有碰撞体，鼠标射线就扫不到它，打游戏时点左键不会误触菜单按钮。
"""
from __future__ import annotations

from dataclasses import dataclass

from ursina import Entity, application, camera

from src.core import config
from src.ui.widgets import (
    RGB_ACCENT,
    RGB_DANGER,
    RGB_MUTED,
    card,
    backdrop,
    button,
    label,
)


def format_time(seconds: float) -> str:
    """秒 → `m:ss`。纯函数，结算面板和探针共用。"""
    total = int(max(0.0, seconds))
    return f"{total // 60}:{total % 60:02d}"


@dataclass(frozen=True)
class RunSummary:
    score: int
    kills: int
    seconds: float
    wave: int
    best: int
    new_best: bool


def build_summary(score: int, kills: int, seconds: float, wave: int,
                  best: int, best_at_start: int) -> RunSummary:
    """结算数据。`new_best` 必须和**开局时**的最高分比。

    不能和现在的 `best` 比：`player_died` 触发时，计分系统已经把
    `best = max(best, score)` 写完了，这时候比永远是 False。
    """
    return RunSummary(
        score=int(score),
        kills=int(kills),
        seconds=max(0.0, float(seconds)),
        wave=int(wave),
        best=int(max(best, score)),
        new_best=int(score) > int(best_at_start),
    )


class _Panel:
    """面板基类：显示时做一个 0.18 秒的弹出，避免"啪"地一下出现。

    **显隐必须用 `visible`，不能用 `enabled`。**
    实测（ursina 8.3.0）`Entity.enabled = False` **不会把节点隐藏**：
    `enabled_setter` 只有在实体带 `is_singleton` 且判定为非单例时才 `stash()`，
    而普通 `Entity` 走的是 `is_singleton()` 返回 True 的分支，
    结果只是置了一个标志位，网格照样渲染。
    用 `enabled` 做显隐的表现是：主菜单、暂停面板、结算面板同时叠在屏幕上。

    另外，光 `visible=False` 不足以挡住点击：鼠标拾取是碰撞遍历，
    隐藏的按钮可能仍被射线扫到。所以按钮还要把 `collider` 摘掉 ——
    没有碰撞体就不会成为 `mouse.hovered_entity`，
    "对着准星中间点一下就把暂停面板的 RESUME 按了"这种事才不会发生。
    """

    def __init__(self, name: str) -> None:
        self.root = Entity(parent=camera.ui, name=name, enabled=False)
        self.root.visible = False
        self._pop = 0.0
        self._card: Entity | None = None
        self._buttons: list = []

    # ---- 显示 ----
    def show(self) -> None:
        self.root.visible = True
        self.root.enabled = True
        self._set_buttons_active(True)
        self._pop = config.MENU_FADE_TIME

    def hide(self) -> None:
        self._set_buttons_active(False)
        self.root.visible = False
        self.root.enabled = False
        self._pop = 0.0

    def reset(self) -> None:
        self.hide()

    # ---- 内部 ----
    def _add_button(self, content, position, on_click, audio, **kwargs):
        btn = button(self.root, content, position, on_click, audio=audio, **kwargs)
        # 面板一创建就是隐藏的，按钮必须同步进入"点不到"状态。
        # 少了这两行，暂停面板那个位于屏幕正中的 RESUME 会在对局里
        # 悄悄顶着一个不可见的碰撞体，开枪的点击可能把它按了。
        btn.collider = None
        btn.disabled = True
        self._buttons.append(btn)
        return btn

    def _set_buttons_active(self, active: bool) -> None:
        for btn in self._buttons:
            btn.disabled = not active
            # 摘掉 collider 才是"点不到"的硬保证；disabled 只挡住 Button.input，
            # 挡不住 mouse.py 直接调 hovered_entity.on_click()。
            btn.collider = "box" if active else None

    # ---- 每帧（ui_system）----
    def update(self, dt: float) -> None:
        if not self.root.visible or self._card is None:
            return
        if self._pop <= 0.0:
            return
        self._pop = max(0.0, self._pop - dt)
        k = 1.0 - self._pop / config.MENU_FADE_TIME
        ease = 1.0 - (1.0 - k) ** 2
        factor = 0.94 + 0.06 * ease
        self._card.scale = (self._card_scale[0] * factor, self._card_scale[1] * factor)

    def _make_card(self, scale: tuple[float, float], alpha: float = 0.94) -> Entity:
        self._card_scale = scale
        self._card = card(self.root, scale=scale, alpha=alpha)
        return self._card


class MainMenu(_Panel):
    def __init__(self, on_start, on_quit, audio=None, data=None) -> None:
        super().__init__("main_menu")
        # 0.62 会把竞技场压成一片黑，只剩玩家那个青色方块显眼。
        # 正文全部落在不透明的卡片上，所以周边压暗到 0.55 就够读，还能看见场地。
        backdrop(self.root, 0.55)
        self._make_card((0.74, 0.58))

        label(self.root, "MINI TPS", (0, 0.185), scale=6.5)
        label(self.root, "survive the waves", (0, 0.120), scale=1.2, rgb=RGB_MUTED)
        self.best_text = label(self.root, "BEST 0", (0, 0.075), scale=1.05, rgb=RGB_ACCENT)

        self._add_button("START", (0, -0.020), on_start, audio)
        self._add_button("QUIT", (0, -0.100), on_quit, audio)

        label(self.root, "WASD  MOVE      SHIFT  SPRINT      SPACE  DODGE",
              (0, -0.190), scale=0.95, rgb=RGB_MUTED)
        label(self.root, "LMB  FIRE    RMB  AIM    R  RELOAD    1 / 2  SWAP    ESC  PAUSE",
              (0, -0.225), scale=0.95, rgb=RGB_MUTED)

        if data is not None:
            self.refresh_best(data)

    def refresh_best(self, data) -> None:
        self.best_text.text = f"BEST {max(data.best_score, data.score)}"


class PauseMenu(_Panel):
    def __init__(self, on_resume, on_menu, audio=None, audio_system=None) -> None:
        super().__init__("pause_menu")
        self.audio_system = audio_system
        backdrop(self.root, 0.55)
        self._make_card((0.56, 0.48))

        label(self.root, "PAUSED", (0, 0.145), scale=3.6)
        self._add_button("RESUME", (0, 0.020), on_resume, audio)
        self.volume_button = self._add_button("VOLUME 80%", (0, -0.060),
                                              self._cycle_volume, audio)
        self._add_button("MAIN MENU", (0, -0.140), on_menu, audio)
        self.refresh_volume()

    def _cycle_volume(self) -> None:
        if self.audio_system is None:
            return
        self.audio_system.cycle_volume()
        self.refresh_volume()

    def refresh_volume(self) -> None:
        if self.audio_system is None:
            self.volume_button.text = "VOLUME 80%"
            return
        self.volume_button.text = f"VOLUME {self.audio_system.volume_percent}%"


class GameOverPanel(_Panel):
    def __init__(self, on_restart, on_menu, audio=None) -> None:
        super().__init__("game_over")
        backdrop(self.root, 0.72)
        self._make_card((0.68, 0.60))

        label(self.root, "YOU DIED", (0, 0.205), scale=4.0, rgb=RGB_DANGER)
        self.wave_text = label(self.root, "WAVE 0", (0, 0.150), scale=1.15, rgb=RGB_MUTED)

        self._rows = {}
        rows = (("SCORE", 0.080), ("KILLS", 0.030), ("TIME", -0.020), ("BEST", -0.070))
        for name, y in rows:
            label(self.root, name, (-0.16, y), scale=1.1, rgb=RGB_MUTED, origin=(-0.5, 0))
            self._rows[name] = label(self.root, "-", (0.16, y), scale=1.1, origin=(1, 0))

        self.record_text = label(self.root, "", (0, -0.108), scale=1.05, rgb=RGB_ACCENT)

        self._add_button("RESTART", (0, -0.170), on_restart, audio)
        self._add_button("MAIN MENU", (0, -0.250), on_menu, audio)

    def show(self, summary: RunSummary | None = None) -> None:
        super().show()
        if summary is None:
            return
        self.wave_text.text = f"WAVE {summary.wave}"
        self._rows["SCORE"].text = str(summary.score)
        self._rows["KILLS"].text = str(summary.kills)
        self._rows["TIME"].text = format_time(summary.seconds)
        self._rows["BEST"].text = str(summary.best)
        self.record_text.text = "NEW RECORD" if summary.new_best else ""


__all__ = [
    "RunSummary", "build_summary", "format_time",
    "MainMenu", "PauseMenu", "GameOverPanel",
]


def quit_game() -> None:
    """给主菜单的 QUIT 按钮用。

    单独包一层是为了在退出前留一条日志：窗口"闪一下就没了"的时候，
    至少能从控制台确认是玩家点的退出，而不是崩在某处。
    """
    print("[M5] quit by menu")
    application.quit()
