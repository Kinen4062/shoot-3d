"""窗口失焦自动暂停（规划书 §M6「边界情况：窗口失焦暂停」）。

**为什么必须有**：第三人称射击里敌人一直在打你。玩家 Alt+Tab 出去看一眼浏览器，
回来时人已经躺了 —— 这不算难度，算 bug。

实现选择：**轮询窗口属性**，不用 `base.accept('window-event')`。

- 事件回调在无窗口环境（`window_type='none'`，探针和 CI 都靠它）根本不会触发，
  等于这段逻辑永远测不到；轮询可以注入假的"焦点状态"直接测。
- 事件回调还要处理消息名（`window-deactivate`）这类字符串，平台差异更脆。
  `WindowProperties.getForeground()` 是跨平台的稳定值。

**不自动恢复**：焦点回来时保持暂停，让玩家点 RESUME。自动恢复等于"你一回来
就被继续打"，和这个功能的目的相反。

**状态未知时什么都不做**：`hasForeground()` 为 False 表示这个属性从没被设置过
（某些平台/无头环境），此时取值恒为 False —— 直接当"失焦"会导致一启动就暂停。
所以三态：True / False / None，None 一律跳过。
"""
from __future__ import annotations

from ursina import application

from src.core.game_state import State


def window_foreground():
    """当前窗口是否在前台。拿不到状态时返回 None（无窗口 / 属性未设置）。

    `hasForeground()` 判断的是"这个字段有没有被设置"，`getForeground()` 才是值。
    混用会让你在无头环境里永远拿到 False。
    """
    base = getattr(application, "base", None)
    win = getattr(base, "win", None) if base is not None else None
    if win is None:
        return None
    try:
        props = win.getProperties()
        if props is None or not props.hasForeground():
            return None
        if props.hasMinimized() and props.getMinimized():
            return False                      # 最小化等同于失焦
        return bool(props.getForeground())
    except Exception:                         # noqa: BLE001 - 查询失败就当未知
        return None


class FocusWatch:
    """失焦就暂停对局。`poll` 可注入，测试里用假的焦点序列。"""

    def __init__(self, state, poll=None) -> None:
        self.state = state
        self._poll = poll or window_foreground
        self._was_focused = True
        self.unknown = 0        # 拿不到状态的帧数（第一次遇到会打一条日志）
        self.auto_pauses = 0    # 因为失焦自动暂停了几次（覆盖层/探针可查）
        self._logged_unknown = False

    def update(self, dt: float) -> None:
        focused = self._poll()
        if focused is None:
            self.unknown += 1
            if not self._logged_unknown:
                self._logged_unknown = True
                print("[M6] 拿不到窗口焦点状态，失焦暂停不生效（无窗口环境属正常）")
            return

        if self._was_focused and not focused:
            self.auto_pauses += 1
            # 只在真正在玩的时候暂停：菜单/结算变成 PAUSED 会把界面切错。
            if self.state.in_state(State.PLAYING):
                self.state.set(State.PAUSED)
        self._was_focused = focused

    def reset(self) -> None:
        """重开一局：把"上一帧在前台"重置掉。

        不重置的话，如果玩家在结算界面切出去又回来点 RESTART，第一次 update
        会看到"上一帧没焦点、这一帧有焦点"—— 方向虽然反过来不会误暂停，
        但状态一直是脏的，下次真失焦时可能漏掉。
        """
        self._was_focused = True
