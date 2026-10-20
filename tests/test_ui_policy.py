"""界面策略：什么时候显示什么、颜色能不能分辨。

这些不是"好看不好看"的审美问题，是实机上真的会坏的两类事：

1. **调试覆盖层盖在菜单上** —— 十几行等宽文字 + 一行 `[TAB] cycle …`
   直接叠在 START 按钮上，玩家第一眼看到的就是这个；
2. **按钮底色和卡片同色** —— 实机截图里 START / QUIT 只剩两行白字悬空，
   鼠标移上去也没有任何变化，玩家会以为"这里不能点"。

两者都只用常量或纯函数就能钉住，不需要启动窗口。
"""
from __future__ import annotations

import pytest

from src.core import config
from src.core.game_state import State
from src.ui import widgets
from src.ui.debug_overlay import overlay_visible


class _FakeState:
    """只实现 in_state，够 overlay_visible 用。"""

    def __init__(self, state: State) -> None:
        self._state = state

    def in_state(self, *states: State) -> bool:
        return self._state in states


@pytest.mark.parametrize("state_name, expected", [
    (State.PLAYING, True),
    (State.PAUSED, True),
    (State.MENU, False),
    (State.GAME_OVER, False),
])
def test_overlay_visibility_per_state(state_name, expected):
    assert overlay_visible(_FakeState(state_name)) is expected


def test_overlay_hidden_in_menu_is_not_just_disabled():
    """菜单里必须是"不显示"，不能只是不再刷新文本。

    `Text` 的实现和普通 Entity 一样：`enabled=False` 不会隐藏节点，
    只有 `visible` 才真的不画。这条注释是为了防止后人把可见性判断
    挪进 `enabled`。
    """
    from src.ui import debug_overlay
    source = open(debug_overlay.__file__, encoding="utf-8").read()
    assert "self.label.visible = show" in source
    assert "self.label.enabled" not in source


def test_button_fill_is_brighter_than_card():
    """按钮底色必须明显亮于卡片，否则按钮等于不存在。"""
    gap = sum(widgets.RGB_BTN) - sum(widgets.RGB_CARD)
    assert gap >= 30, f"按钮和卡片只差 {gap}（RGB 合计），实机看不出按钮边界"


def test_button_hover_and_pressed_are_distinct():
    """三态亮度必须递增：底色 < 悬停，按下最暗。"""
    base = sum(widgets.RGB_BTN)
    hover = sum(widgets.RGB_BTN_HOVER)
    pressed = sum(widgets.RGB_BTN_PRESS)
    assert hover > base > pressed


def test_menu_camera_is_wider_than_gameplay():
    """菜单机位要比游戏内更远，否则玩家那个方块会糊住半个画面。"""
    assert config.MENU_CAM_DIST > config.CAM_DIST


def test_menu_pitch_is_within_gameplay_clamp():
    """菜单俯角不能超出游戏内的俯仰限位，否则切回对局会跳一下。"""
    assert config.CAM_PITCH_MIN <= config.MENU_CAM_PITCH <= config.CAM_PITCH_MAX
