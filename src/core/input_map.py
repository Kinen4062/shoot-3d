"""键位集中定义 + 输入接管。

规则：业务代码里禁止出现按键字符串字面量，一律引用这里的常量。

为什么不用 input_handler.rebind：
rebind(to_key, from_key) 的语义是「把 A 键别名到 B 键」，不接受 callable。

为什么不能写 input_handler.input = my_input：
那不是回调槽位，它就是引擎维护 held_keys 的函数本体。
覆盖之后 held_keys 永不更新 -> WASD 静默失灵，不报错也不崩溃。
引擎真正查的是 __main__.input（ursina/main.py:279-281）。
"""
from __future__ import annotations

import __main__

from src.core.game_state import State

# ---- 键名常量 ----
KEY_FORWARD = "w"
KEY_BACK = "s"
KEY_LEFT = "a"
KEY_RIGHT = "d"
KEY_SPRINT = "shift"
KEY_DODGE = "space"
KEY_RELOAD = "r"

KEY_FIRE = "left mouse down"    # 按下瞬间（单发 / 开始连发）
KEY_FIRE_HOLD = "left mouse"    # 按住（自动武器）
KEY_AIM = "right mouse down"
KEY_AIM_UP = "right mouse up"

# 切枪：数字键。数据里的顺序由 weapons.json 决定，这里按下标映射。
KEY_WEAPON_1 = "1"
KEY_WEAPON_2 = "2"

KEY_PAUSE = "escape"
KEY_DEBUG_CYCLE = "tab"

# 鼠标锁定后引擎仍会派发 'mouse up'，方便判断松开。
KEY_FIRE_UP = "left mouse up"


class InputMap:
    """把引擎的按键事件路由到状态机 / 武器 / 玩家控制器。

    game 需要提供 toggle_pause() 和 debug_cycle()；
    weapon 需要 on_fire_down / on_fire_up / start_reload / set_aiming / switch；
    controller 需要 request_dodge()。
    """

    def __init__(self, game, weapon=None, controller=None) -> None:
        self.game = game
        self.weapon = weapon
        self.controller = controller
        # 最近一次收到的原始按键，覆盖层会显示它。
        # 右键没反应时，先看这行有没有 'right mouse down'：
        # 有 -> 是业务逻辑的问题；没有 -> 事件根本没到，跟游戏代码无关。
        self.last_key = ""

    def install(self) -> None:
        # 注册到 __main__ 而不是 self：引擎查的是 __main__.input。
        __main__.input = self._dispatch  # type: ignore[attr-defined]

    def _dispatch(self, key: str) -> None:
        self.last_key = key
        if key == KEY_PAUSE:
            self.game.toggle_pause()
            return
        if key == KEY_DEBUG_CYCLE:
            self.game.debug_cycle()
            return

        # 只有在对局中才把按键交给战斗相关对象。
        # 不加这一层的话，在菜单里点 START 会同时触发一次开火
        # （按钮和武器都监听 'left mouse down'），开局第一发子弹凭空打出去。
        if not self.game.in_state(State.PLAYING):
            return

        if key == KEY_DODGE and self.controller is not None:
            self.controller.request_dodge()
        elif self.weapon is not None:
            self._dispatch_weapon(key)

    def _dispatch_weapon(self, key: str) -> None:
        weapon = self.weapon
        if key == KEY_FIRE:
            weapon.on_fire_down()
        elif key == KEY_FIRE_UP:
            weapon.on_fire_up()
        elif key == KEY_RELOAD:
            weapon.start_reload()
        elif key == KEY_AIM:
            weapon.set_aiming(True)
        elif key == KEY_AIM_UP:
            weapon.set_aiming(False)
        elif key == KEY_WEAPON_1:
            weapon.switch(0)
        elif key == KEY_WEAPON_2:
            weapon.switch(1)
