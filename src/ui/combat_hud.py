"""正式 HUD：血条 / 护盾格 / 弹药 / 波次。

M4 阶段这些数字都写在左上角的调试覆盖层里 —— 那不是给玩家看的：
- 覆盖层是等宽字体堆在一起的十几行，读它等于读日志；
- 它默认常开，正式玩的时候会挡视线。

这一层只放"打起来必须余光可见"的四件事，其余（分数/连击/拾取飘字）
仍由 `score_hud.py` 负责，两边互不重叠：

    左上：调试覆盖层      顶部居中：WAVE n · n LEFT
    左下：HP + 护盾格     右下：武器 + 弹药 + 换弹条

**为什么血条要变颜色**：只有一个长度变化时，玩家在激烈交火里分不清
"还剩七成"和"还剩三成"。颜色分档（>60% 绿 / 30~60% 黄 / <30% 红）
是第二重编码，扫一眼就知道危险程度。

**为什么不每帧写文本**：拼字符串 + 重设 Text 会触发文字纹理重建，
0.1 秒刷新一次在这个尺度上完全看不出延迟。
"""
from __future__ import annotations

from ursina import Entity, camera, color, window

from src.core.game_state import State
from src.ui.widgets import RGB_MUTED, RGB_TEXT, Z_TEXT, label, rgba32

TEXT_REFRESH = 0.1

BAR_WIDTH = 0.26
BAR_HEIGHT = 0.014
PAD = 0.025

RGB_HP_OK = (110, 235, 150)
RGB_HP_WARN = (250, 200, 90)
RGB_HP_LOW = (235, 90, 85)
RGB_SHIELD = (110, 235, 235)


def hp_rgb(ratio: float) -> tuple[int, int, int]:
    """血条颜色分档。抽成纯函数，方便单测边界（0.6 / 0.3）。"""
    if ratio > 0.6:
        return RGB_HP_OK
    if ratio > 0.3:
        return RGB_HP_WARN
    return RGB_HP_LOW


class CombatHud:
    def __init__(self, state, player, weapon, waves=None, enemies=None) -> None:
        self.state = state
        self.player = player
        self.weapon = weapon
        self.waves = waves
        self.enemies = enemies

        self.root = Entity(parent=camera.ui, name="combat_hud")
        left = window.bottom_left
        right = window.bottom_right

        # ---- 左下：血条 ----
        self._bar_x = left.x + PAD
        self._bar_y = left.y + 0.055
        self.hp_bg = Entity(parent=self.root, model="quad",
                            color=rgba32((0, 0, 0), 0.55),
                            scale=(BAR_WIDTH + 0.006, BAR_HEIGHT + 0.006),
                            position=(self._bar_x + BAR_WIDTH / 2, self._bar_y),
                            z=Z_TEXT + 0.01)
        self.hp_fill = Entity(parent=self.root, model="quad",
                              color=rgba32(RGB_HP_OK),
                              origin=(-0.5, 0),
                              scale=(BAR_WIDTH, BAR_HEIGHT),
                              position=(self._bar_x, self._bar_y),
                              z=Z_TEXT)
        self.hp_text = label(self.root, "HP 100", (self._bar_x, self._bar_y + 0.030),
                             scale=1.0, origin=(-0.5, 0))

        # ---- 护盾格：一共 3 格，空的也画出来（暗色），玩家才知道上限 ----
        self.shield_pips = []
        for i in range(3):
            pip = Entity(parent=self.root, model="quad",
                         color=rgba32(RGB_SHIELD, 0.15),
                         scale=(0.016, 0.010),
                         position=(self._bar_x + BAR_WIDTH + 0.012 + i * 0.021,
                                   self._bar_y),
                         z=Z_TEXT)
            self.shield_pips.append(pip)

        # ---- 右下：武器 / 弹药 ----
        self.weapon_text = label(self.root, "", (right.x - PAD, right.y + 0.062),
                                 scale=0.9, rgb=RGB_MUTED, origin=(1, 0))
        self.ammo_text = label(self.root, "", (right.x - PAD, right.y + 0.025),
                               scale=1.7, origin=(1, 0))
        self.reload_bg = Entity(parent=self.root, model="quad",
                                color=rgba32((0, 0, 0), 0.5),
                                origin=(1, 0),
                                scale=(0.14, 0.007),
                                position=(right.x - PAD, right.y + 0.010),
                                z=Z_TEXT + 0.01, visible=False)
        self.reload_fill = Entity(parent=self.root, model="quad",
                                  color=rgba32((240, 210, 120)),
                                  origin=(1, 0),
                                  scale=(0.14, 0.007),
                                  position=(right.x - PAD, right.y + 0.010),
                                  z=Z_TEXT, visible=False)

        # ---- 顶部居中：波次 + 剩余敌人 ----
        self.wave_text = label(self.root, "", (0, window.top.y - 0.045),
                               scale=1.05, rgb=RGB_MUTED)

        self._acc = TEXT_REFRESH
        self.reset()

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        # 显隐用 visible（enabled 在 ursina 8 里不会隐藏节点，见 menus._Panel 的说明）。
        visible = self.state.in_state(State.PLAYING, State.PAUSED)
        if self.root.visible != visible:
            self.root.visible = visible
        if not visible:
            return

        self._acc += dt
        if self._acc < TEXT_REFRESH:
            return
        self._acc = 0.0
        self._refresh()

    def _refresh(self) -> None:
        player = self.player
        hp = player.hp
        max_hp = player.max_hp or 1.0
        ratio = max(0.0, min(1.0, hp / max_hp))

        self.hp_fill.scale_x = max(BAR_WIDTH * ratio, 0.0)
        self.hp_fill.color = rgba32(hp_rgb(ratio))
        self.hp_text.text = f"HP {hp:.0f}"

        charges = getattr(player, "shield_charges", 0)
        for i, pip in enumerate(self.shield_pips):
            pip.color = rgba32(RGB_SHIELD, 0.85 if i < charges else 0.14)

        weapon = self.weapon.current
        data = weapon.data
        self.weapon_text.text = data.name.upper()
        self.ammo_text.text = f"{weapon.ammo} / {data.mag_size}"
        self.ammo_text.color = rgba32(
            RGB_HP_LOW if weapon.ammo == 0 else RGB_TEXT
        )

        if weapon.reloading:
            total = data.reload_time or 1.0
            left = max(0.0, min(1.0, weapon.reload_left / total))
            self.reload_bg.visible = True
            self.reload_fill.visible = True
            self.reload_fill.scale_x = max(0.14 * (1.0 - left), 0.001)
        else:
            self.reload_bg.visible = False
            self.reload_fill.visible = False

        wave = self.waves.wave if self.waves is not None else 0
        alive = self.enemies.alive_count if self.enemies is not None else 0
        if wave <= 0:
            self.wave_text.text = ""
        else:
            self.wave_text.text = f"WAVE {wave}   ·   {alive} LEFT"

    # ---- 重开 ----
    def reset(self) -> None:
        self.root.visible = False
        self._acc = TEXT_REFRESH
        self.hp_text.text = f"HP {self.player.hp:.0f}"
        self.hp_fill.scale_x = BAR_WIDTH
        self.hp_fill.color = rgba32(RGB_HP_OK)
        self.ammo_text.text = ""
        self.weapon_text.text = ""
        self.wave_text.text = ""
        for pip in self.shield_pips:
            pip.color = rgba32(RGB_SHIELD, 0.14)
        self.reload_bg.visible = False
        self.reload_fill.visible = False
