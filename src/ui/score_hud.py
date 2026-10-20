"""计分 HUD：右上角分数 + 中下方连击 + 拾取提示。

M4 就做了这一层，因为「连击 3 秒清零」「拾取有明显效果」这两条必须肉眼可验证。
M5 把血条/弹药/波次交给 `combat_hud.py`，这里的位置也从右下移到了右上 ——
右下要留给弹药，两个数字挤在一起会互相读不清。

文案全英文（规划书 §5.9）：Ursina 默认字体不含中文字形，写中文会变成豆腐块。

所有元素挂在 `camera.ui` 下，坐标范围是 ±aspect（横）× ±1（纵）。
整组元素挂在 `self.root` 上，方便按游戏状态一键显隐。
"""
from __future__ import annotations

from ursina import Entity, Text, camera, color, window

from src.core import config
from src.core.game_data import GameData, get_data
from src.core.game_state import State
from src.systems.pickup_system import PICKUP_RGB as _PICKUP_RGB

# 分数文本的刷新间隔。每帧拼字符串纯属浪费，0.1 秒已经完全看不出延迟。
TEXT_REFRESH = 0.1

TOAST_TIME = 1.1
COMBO_BAR_WIDTH = 0.34
COMBO_BAR_HEIGHT = 0.009

_COMBO_RGB = (255, 208, 88)


class ScoreHud:
    def __init__(self, data: GameData | None = None, state=None) -> None:
        self.data = data if data is not None else get_data()
        self.state = state
        self.root = Entity(parent=camera.ui, name="score_hud")

        right = window.top_right
        self.score_text = Text(
            text="SCORE 0",
            parent=self.root,
            origin=(1, 1),
            position=(right.x - 0.02, right.y - 0.025),
            scale=1.6,
            color=color.white,
        )
        self.best_text = Text(
            text="BEST 0",
            parent=self.root,
            origin=(1, 1),
            position=(right.x - 0.02, right.y - 0.062),
            scale=1.0,
            color=color.rgba(0.62, 0.68, 0.76, 1.0),
        )

        self.combo_text = Text(
            text="",
            parent=self.root,
            origin=(0, 0),
            position=(0, -0.30),
            scale=1.5,
            color=color.rgb32(*_COMBO_RGB),
        )
        # 倒计时条：用 origin=(-0.5, 0) 固定左端，改 scale_x 只会向右缩。
        # 用 origin=(0,0) 的话条会从中间往两边缩，看起来像"弹性动画"而不是倒计时。
        self.combo_bar_bg = Entity(
            parent=self.root, model="quad",
            color=color.rgba(1, 1, 1, 0.10),
            scale=(COMBO_BAR_WIDTH, COMBO_BAR_HEIGHT),
            position=(-COMBO_BAR_WIDTH / 2, -0.345),
        )
        self.combo_bar = Entity(
            parent=self.root, model="quad",
            color=color.rgba(*[c / 255 for c in _COMBO_RGB], 0.92),
            origin=(-0.5, 0),
            scale=(COMBO_BAR_WIDTH, COMBO_BAR_HEIGHT),
            position=(-COMBO_BAR_WIDTH / 2, -0.345),
            z=-0.01,
        )

        self.toast_text = Text(
            text="",
            parent=self.root,
            origin=(0, 0),
            position=(0, -0.17),
            scale=1.5,
            color=color.white,
        )

        self._text_acc = 0.0
        self._toast_left = 0.0
        self._toast_total = TOAST_TIME
        self.reset()

    # ---- 事件 ----
    def on_pickup(self, kind: str | None = None, label: str = "", **_kwargs) -> None:
        if not label:
            return
        rgb = _PICKUP_RGB.get(kind or "", (255, 255, 255))
        self.toast_text.text = label
        self.toast_text.color = color.rgb32(*rgb)
        self._toast_left = TOAST_TIME
        self._toast_total = TOAST_TIME

    def on_state_changed(self, **_kwargs) -> None:
        self._text_acc = TEXT_REFRESH      # 立刻刷新一次，不等节流周期

    # ---- 每帧（ui_system，用未缩放的 real_dt）----
    def update(self, dt: float) -> None:
        if self.state is not None:
            visible = self.state.in_state(State.PLAYING, State.PAUSED)
            if self.root.visible != visible:
                # 用 visible 而不是 enabled：ursina 8 的 enabled=False 不会隐藏节点。
                self.root.visible = visible
            if not visible:
                return

        data = self.data

        self._text_acc += dt
        if self._text_acc >= TEXT_REFRESH:
            self._text_acc = 0.0
            self.score_text.text = f"SCORE {data.score}"
            self.best_text.text = f"BEST {max(data.best_score, data.score)}"

        self._update_combo()
        self._update_toast(dt)

    def _update_combo(self) -> None:
        data = self.data
        combo = data.combo
        if combo < 2:
            # 1 连不算连击。显示 "COMBO x1.00" 只会让玩家困惑。
            self.combo_text.text = ""
            self._hide_combo_bar()
            return

        self.combo_text.text = f"COMBO x{data.combo_multiplier:.1f}  ({combo})"
        ratio = data.combo_timer / config.COMBO_TIMEOUT
        ratio = 0.0 if ratio < 0.0 else (1.0 if ratio > 1.0 else ratio)
        # 用 visible 而不是 enabled：enabled=False 会走 Entity.stash()，
        # 把节点从场景图整枝摘掉再插回来，高频切换没有意义且有副作用。
        self.combo_bar.visible = True
        self.combo_bar_bg.visible = True
        self.combo_bar.scale_x = max(COMBO_BAR_WIDTH * ratio, 0.0)

    def _hide_combo_bar(self) -> None:
        if self.combo_bar.visible:
            self.combo_bar.visible = False
        if self.combo_bar_bg.visible:
            self.combo_bar_bg.visible = False

    def _update_toast(self, dt: float) -> None:
        if self._toast_left <= 0.0:
            return
        self._toast_left -= dt
        if self._toast_left <= 0.0:
            self._toast_left = 0.0
            self.toast_text.text = ""
            return
        # 最后 40% 时间里淡出
        fade = self._toast_left / self._toast_total
        alpha = 1.0 if fade > 0.4 else fade / 0.4
        self.toast_text.color = color.rgba(
            self.toast_text.color.r, self.toast_text.color.g,
            self.toast_text.color.b, alpha,
        )

    # ---- 重开 ----
    def reset(self) -> None:
        self._text_acc = TEXT_REFRESH
        self._toast_left = 0.0
        self.toast_text.text = ""
        self.combo_text.text = ""
        self._hide_combo_bar()
        self.score_text.text = f"SCORE {self.data.score}"
        self.best_text.text = f"BEST {self.data.best_score}"
        self.root.visible = False
