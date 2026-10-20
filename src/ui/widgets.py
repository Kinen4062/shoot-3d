"""UI 基础件：面板、标签、按钮。

**为什么单独一层**

M5 的三个界面（主菜单 / 暂停 / 结算）如果各写一遍按钮，
颜色、字号、点击音效会三处不一致，改一次要改三遍。
这里把「长什么样」和「点了做什么」分开：界面只传文字和回调。

**尺寸坐标系**（实测确认，别凭直觉）

`camera.ui` 的节点 scale 是 20，正交相机视野也是 20 个单位，两者抵消，
所以**位置仍然是窗口坐标**：x ∈ ±aspect/2，y ∈ ±0.5。
但 `Text` 的 `scale=1` 只占**屏高的 2.5%**（0.025 × 20 / 20），
一屏高 ≈ 40 个 scale 单位。想做半屏高的标题，scale 给到 6~7，不是 0.3。
"""
from __future__ import annotations

from ursina import Button, Entity, Text, camera, color

# 配色。和游戏内的 HUD 用同一套，避免"菜单是蓝的、HUD 是灰的"。
RGB_TEXT = (236, 241, 247)
RGB_MUTED = (150, 160, 175)
RGB_ACCENT = (110, 190, 240)
RGB_DANGER = (235, 96, 88)
RGB_PANEL = (10, 13, 18)
RGB_CARD = (18, 22, 30)
# 按钮底色必须**明显亮于卡片**，否则按钮等于不存在 ——
# 第一版把按钮也给了 RGB_CARD(18,22,30)，实机截图里 START / QUIT
# 只剩两行白字悬在卡片上，看不出哪里可以点。
RGB_BTN = (34, 42, 56)
RGB_BTN_HOVER = (46, 80, 108)
RGB_BTN_PRESS = (22, 30, 40)

Z_BACKDROP = 0.20      # 离相机最远
Z_CARD = 0.10
Z_TEXT = 0.00
Z_BUTTON = -0.01       # 最近，保证在卡片和文字之上

BTN_WIDTH = 0.34
BTN_HEIGHT = 0.062


def rgba32(rgb: tuple[int, int, int], alpha: float = 1.0) -> color.Color:
    return color.rgba(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, alpha)


def backdrop(parent, alpha: float = 0.78) -> Entity:
    """全屏压暗层。盖住 3D 画面，让文字不用描边也读得清。"""
    from ursina import window
    return Entity(
        parent=parent,
        model="quad",
        color=rgba32(RGB_PANEL, alpha),
        scale=(window.aspect_ratio, 1.0),
        z=Z_BACKDROP,
    )


def card(parent, scale=(0.66, 0.52), alpha: float = 0.92) -> Entity:
    """中间那块不透明的卡片。缩放在 update 里做弹出动画。"""
    return Entity(
        parent=parent,
        model="quad",
        color=rgba32(RGB_CARD, alpha),
        scale=scale,
        z=Z_CARD,
    )


def label(parent, content: str, position=(0, 0), scale: float = 1.0,
          rgb: tuple[int, int, int] = RGB_TEXT, origin=(0, 0),
          z: float = Z_TEXT) -> Text:
    return Text(
        text=content,
        parent=parent,
        position=position,
        scale=scale,
        color=rgba32(rgb),
        origin=origin,
        z=z,
    )


def button(parent, content: str, position, on_click, audio=None,
           width: float = BTN_WIDTH, height: float = BTN_HEIGHT,
           rgb: tuple[int, int, int] = RGB_TEXT) -> Button:
    """标准按钮。点击时先响一声再执行回调（音效在 UI 层是"确认"，不是装饰）。

    悬停色比底色亮一档，按下再压暗 —— 三者必须都能分辨：
    只有一个底色时，鼠标移上去没有任何反馈，玩家会怀疑"是不是没点中"。
    """
    def _click() -> None:
        if audio is not None:
            audio.on_click()
        on_click()

    return Button(
        parent=parent,
        text=content,
        position=position,
        scale=(width, height),
        text_size=0.85,
        color=rgba32(RGB_BTN),
        highlight_color=rgba32(RGB_BTN_HOVER),
        pressed_color=rgba32(RGB_BTN_PRESS),
        text_color=rgba32(rgb),
        highlight_text_color=color.white,
        z=Z_BUTTON,
        on_click=_click,
    )
