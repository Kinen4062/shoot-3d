"""玩家移动控制：WASD + 疾跑 + 闪避（带无敌帧）。

输入 → 世界方向 → 位移 → 约束（边界、柱子），全部在 dt 里推进，
不读 time.dt，暂停时由主循环直接跳过这一层。

闪避是 M3 的核心生存工具：敌人的攻击都有前摇，闪避的无敌帧是唯一
能「穿过」伤害的手段。没有它，远程兵的弹丸只能靠跑位躲，
在 60×60 的开阔场地上会变成纯粹的运气。
"""
from __future__ import annotations

import math

from ursina import Vec3, held_keys

from src.core import config, event_bus, input_map
from src.core.game_state import State


def move_direction(forward: float, right: float, yaw_deg: float) -> Vec3:
    """把「前后 / 左右」输入按相机朝向转成世界方向。

    抽成纯函数，方便单测：不依赖引擎、不依赖全局状态。
    yaw=0 时前方是 +z，与 Ursina 的 forward 约定一致。
    """
    if forward == 0 and right == 0:
        return Vec3(0, 0, 0)

    yaw = math.radians(yaw_deg)
    fwd = Vec3(math.sin(yaw), 0, math.cos(yaw))
    rgt = Vec3(math.cos(yaw), 0, -math.sin(yaw))
    return (fwd * forward + rgt * right).normalized()


class PlayerController:
    def __init__(self, player, rig, arena, state, weapon=None) -> None:
        self.player = player
        self.rig = rig
        self.arena = arena
        self.state = state
        self.weapon = weapon
        self.moving = False

        # 闪避状态：剩余时间 / 冷却 / 方向
        self.dodge_left = 0.0
        self.dodge_cd = 0.0
        self.dodge_dir = Vec3(0, 0, 0)
        self._input_dir = Vec3(0, 0, 0)
        self.dodges = 0

    # ---- 输入 ----
    def request_dodge(self) -> bool:
        """空格。返回 False 表示冷却中或已经在闪避。"""
        if self.dodge_left > 0.0 or self.dodge_cd > 0.0:
            return False
        direction = self._input_dir
        if direction.length() < 1e-6:
            # 站着不动按空格：朝相机正前方滚，符合玩家直觉。
            yaw = math.radians(self.rig.yaw)
            direction = Vec3(math.sin(yaw), 0, math.cos(yaw))
        self.dodge_dir = Vec3(direction).normalized()
        self.dodge_left = config.DODGE_TIME
        self.dodge_cd = config.DODGE_TIME + config.DODGE_COOLDOWN
        # 无敌帧覆盖整个位移过程，再留一点余量：
        # 位移结束时刚好有敌人挥拳，玩家会觉得自己「滚出去了还被打」。
        self.player.health.iframe = max(self.player.health.iframe, config.DODGE_TIME + 0.05)
        self.dodges += 1
        event_bus.emit("dodge", position=self.player.world_position)
        return True

    # ---- 重开 ----
    def reset(self) -> None:
        self.dodge_left = 0.0
        self.dodge_cd = 0.0
        self.dodge_dir = Vec3(0, 0, 0)
        self._input_dir = Vec3(0, 0, 0)
        self.dodges = 0
        self.moving = False

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        # 无敌帧 / 闪避计时不受 PLAYING 判断影响之外的东西干扰；
        # 暂停时主循环不会调用这里，所以时间自然冻住。
        self.player.health.tick(dt)
        # 加速道具的计时也在这里推进：和 health.tick 同一个位置，
        # 保证"暂停时不掉 buff"这条规则只有一处实现。
        self.player.tick_buffs(dt)
        # 护盾外壳同理：它要跟着层数变，也必须由 dt 驱动，
        # 放在这里暂停时才会一起冻住（否则罩子会在暂停菜单里继续呼吸）。
        self.player.tick_shield_aura(dt)
        if self.dodge_cd > 0.0:
            self.dodge_cd = max(0.0, self.dodge_cd - dt)

        if not self.state.in_state(State.PLAYING) or not self.player.alive:
            self.moving = False
            self.dodge_left = 0.0
            return

        if self.dodge_left > 0.0:
            self.dodge_left -= dt
            self.player.position += self.dodge_dir * config.DODGE_SPEED * dt
            self.arena.clamp(self.player)
            self.arena.resolve_obstacles(self.player)
            self.moving = False
            # 闪避时角色朝闪避方向，而不是继续朝着镜头 —— 否则看起来在侧滑。
            self.player.rotation_y = math.degrees(
                math.atan2(self.dodge_dir.x, self.dodge_dir.z))
            return

        forward = held_keys[input_map.KEY_FORWARD] - held_keys[input_map.KEY_BACK]
        right = held_keys[input_map.KEY_RIGHT] - held_keys[input_map.KEY_LEFT]

        direction = move_direction(forward, right, self.rig.forward_yaw)
        self._input_dir = direction
        self.moving = forward != 0 or right != 0

        if self.moving:
            # 加速道具只作用于走路。闪避速度不乘倍率：
            # 闪避位移是 14 m/s × 0.18s ≈ 2.5 米，再乘 1.5 会直接把玩家送进墙里
            # （clamp 能挡住，但手感会变成"撞墙"）。
            speed = config.PLAYER_SPEED * self.player.speed_mult
            if held_keys[input_map.KEY_SPRINT]:
                speed *= config.SPRINT_MULT

            self.player.position += direction * speed * dt
            self.arena.clamp(self.player)
            self.arena.resolve_obstacles(self.player)

        # 朝向：瞄准或开火时始终朝准星，否则朝移动方向。
        # 少了这条，站着开火时枪会一直朝着上次跑动的方向。
        if self.rig.aiming or (self.weapon is not None and self.weapon.trigger):
            self.player.rotation_y = self.rig.yaw
        elif self.moving:
            self.player.rotation_y = math.degrees(math.atan2(direction.x, direction.z))
