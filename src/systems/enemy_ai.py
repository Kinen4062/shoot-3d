"""敌人行为（状态机），**完全纯逻辑**。

为什么不含任何 Ursina 类型：
这个状态机是整个 M3 最容易写错的地方 —— 丢失目标、进入前摇、
前摇结束的那一帧、远程兵的后撤与横移，每一种都可以用「跑了多少秒、
距离多少、能不能看见」直接断言。把它和渲染解耦，才可能真被测试覆盖。

状态取值与规划书 §5.3 的对应关系：

    规划书      这里
    IDLE        IDLE      站着，什么都没发生
    （搜索）     SEARCH    被枪声惊动 / 刚丢失目标，走向最后已知位置
    CHASE       CHASE     有目标，接近中（远程兵维持距离）
    ATTACK      ATTACK    前摇中，结束后结算伤害
    DEAD        DEAD      已死，不再推进

规划书把「回 IDLE 并走向最后已知位置」写成一条，这里显式拆成 SEARCH ——
否则 IDLE 要同时表示「发呆」和「正在找人」，调试覆盖层上根本分不出来。

`Brain.tick()` 每帧都会被调用，**只写字段、不返回新对象**：
30 个敌人 × 60 帧 × 每帧一个 dataclass = 每秒 1800 次分配，GC 会周期性卡一下。
"""
from __future__ import annotations

import math
from enum import Enum

from src.core import config
from src.core.enemy_types import EnemyData
from src.systems.perception import turn_toward, wrap_deg, yaw_to

# 走到最后已知位置多近算「到了」。
SEARCH_ARRIVE = 0.8
# 待机时缓慢扫视的角速度（度/秒）。
# 不扫视的话，敌人一旦背对玩家就永远发现不了 —— 实测出生时全部朝 +Z，
# 玩家只要从侧面靠近，整波怪会像雕塑一样站着。
IDLE_SCAN_RATE = 25.0
# 远程兵左右横移的换向周期（秒）。固定周期 = 可预测 = 玩家能练；随机反而更难也更不公平。
STRAFE_PERIOD = 1.6
# 远程兵想维持的距离带（相对 preferred_range 的比例）。
KEEP_MAX = 1.15
KEEP_MIN = 0.55


class BrainState(Enum):
    IDLE = "IDLE"
    SEARCH = "SEARCH"
    CHASE = "CHASE"
    ATTACK = "ATTACK"
    DEAD = "DEAD"


class Brain:
    def __init__(self, data: EnemyData) -> None:
        self.data = data
        self.state = BrainState.IDLE
        self.last_seen: tuple[float, float] | None = None
        self.lost_time = 0.0
        self.attack_cd = 0.0
        self.windup = 0.0
        self.sees = False

        # 下面这些是「本帧的输出」，由 EnemySystem 读取后应用到实体上。
        self.move_x = 0.0
        self.move_z = 0.0
        self.face_yaw = 0.0
        self.attack_now = False      # 近战：这一帧结算伤害
        self.shoot_now = False       # 远程：这一帧发射弹丸

        self._strafe_phase = 0.0
        self._strafe_dir = 1.0

    # ---- 外部事件 ----
    def hear(self, x: float, z: float) -> None:
        """听到枪声。已经知道玩家在哪的敌人不需要再被打扰。"""
        if self.state in (BrainState.DEAD, BrainState.CHASE, BrainState.ATTACK):
            return
        self.last_seen = (x, z)
        if self.state is BrainState.IDLE:
            self.state = BrainState.SEARCH

    def kill(self) -> None:
        self.state = BrainState.DEAD
        self.move_x = self.move_z = 0.0
        self.attack_now = self.shoot_now = False

    @property
    def busy(self) -> bool:
        """前摇中 / 已死：这两种状态敌人不该被推动。"""
        return self.state in (BrainState.ATTACK, BrainState.DEAD)

    # ---- 每帧 ----
    def tick(self, dt: float, x: float, z: float, px: float, pz: float, sees: bool) -> None:
        # 每帧先清输出，避免上一帧的意图残留（经典 bug：敌人死了还在往前冲）。
        self.attack_now = False
        self.shoot_now = False
        self.move_x = 0.0
        self.move_z = 0.0

        if self.state is BrainState.DEAD:
            return

        self.attack_cd = max(0.0, self.attack_cd - dt)
        self.sees = sees

        dx = px - x
        dz = pz - z
        dist = math.hypot(dx, dz) or 1e-6

        if sees:
            self.last_seen = (px, pz)
            self.lost_time = 0.0
        else:
            self.lost_time += dt

        if self.state is BrainState.IDLE:
            if sees:
                self.state = BrainState.CHASE
            elif self.last_seen is not None:
                self.state = BrainState.SEARCH
            else:
                # 待机扫视：慢慢转头，给玩家「绕后」制造真实的窗口期，
                # 而不是让敌人 360° 全知全能。
                self.face_yaw = wrap_deg(self.face_yaw + IDLE_SCAN_RATE * dt)
                return

        if self.state is BrainState.SEARCH:
            if sees:
                self.state = BrainState.CHASE
            elif not self._walk_to_last_seen(dt, x, z):
                return

        if self.state is BrainState.CHASE:
            if not sees and self.lost_time >= config.LOSE_TARGET_TIME:
                # 丢失过久：不再贴着玩家坐标（那是作弊），改去最后看见的地方找。
                self.state = BrainState.SEARCH
                self._walk_to_last_seen(dt, x, z)
                return
            self._chase(dt, x, z, dist, dx, dz, sees)
            return

        if self.state is BrainState.ATTACK:
            self._attack(dt, dx, dz)

    # ---- 各状态行为 ----
    def _walk_to_last_seen(self, dt: float, x: float, z: float) -> bool:
        """走向最后已知位置。返回 False 表示已经到达并切回 IDLE。"""
        if self.last_seen is None:
            self.state = BrainState.IDLE
            return False
        tx, tz = self.last_seen
        dx = tx - x
        dz = tz - z
        d = math.hypot(dx, dz)
        if d <= SEARCH_ARRIVE:
            self.last_seen = None
            self.state = BrainState.IDLE
            return False
        self.move_x = dx / d
        self.move_z = dz / d
        self.face_yaw = yaw_to(dx, dz)
        return True

    def _chase(self, dt: float, x: float, z: float, dist: float,
               dx: float, dz: float, sees: bool) -> None:
        data = self.data
        ux = dx / dist
        uz = dz / dist
        self.face_yaw = yaw_to(dx, dz)

        # 看不见时朝最后已知位置走，而不是「透视」玩家当前位置。
        # 直接追实时坐标会让敌人隔着墙精确贴脸，玩家会觉得在作弊。
        if not sees and self.last_seen is not None:
            tx, tz = self.last_seen
            mdx = tx - x
            mdz = tz - z
            m = math.hypot(mdx, mdz)
            if m > 1e-6:
                ux = mdx / m
                uz = mdz / m

        if data.ranged:
            self._ranged_positioning(dt, dist, ux, uz, sees)
            if sees and dist <= data.attack_range and self.attack_cd <= 0.0:
                self.state = BrainState.ATTACK
                self.windup = data.attack_windup
            return

        # 近战：走到攻击距离内就开打，否则继续追。
        if sees and dist <= data.attack_range and self.attack_cd <= 0.0:
            self.state = BrainState.ATTACK
            self.windup = data.attack_windup
            return
        if dist > data.attack_range * 0.9:
            self.move_x = ux
            self.move_z = uz

    def _ranged_positioning(self, dt: float, dist: float, ux: float, uz: float,
                            sees: bool) -> None:
        data = self.data
        keep = data.preferred_range
        if not sees:
            self.move_x = ux
            self.move_z = uz
            return

        if dist > data.attack_range * 0.9:
            self.move_x = ux
            self.move_z = uz
        elif dist > keep * KEEP_MAX:
            self.move_x = ux * 0.6
            self.move_z = uz * 0.6
        elif dist < keep * KEEP_MIN:
            # 太近就后撤。远程兵被贴脸还站着不动，等于送分。
            self.move_x = -ux
            self.move_z = -uz
        else:
            self._strafe_phase += dt
            if self._strafe_phase >= STRAFE_PERIOD:
                self._strafe_phase = 0.0
                self._strafe_dir = -self._strafe_dir
            # 朝向的右手方向：与 (ux, uz) 垂直。
            self.move_x = -uz * self._strafe_dir
            self.move_z = ux * self._strafe_dir

    def _attack(self, dt: float, dx: float, dz: float) -> None:
        self.face_yaw = yaw_to(dx, dz)
        self.windup -= dt
        if self.windup > 0.0:
            return
        if self.data.ranged:
            self.shoot_now = True
        else:
            self.attack_now = True
        self.attack_cd = self.data.attack_cd
        self.state = BrainState.CHASE


def separation_push(ax: float, az: float, bx: float, bz: float,
                    ra: float, rb: float) -> tuple[float, float] | None:
    """两个敌人重叠时，A 该被推开的位移。返回 None 表示不需要推。

    用平方距离比较（省 sqrt），但真要推开时必须开方 —— 否则推开的距离不对，
    敌人会像被压扁的弹簧一样抖。
    """
    min_d = ra + rb + config.SEPARATION_GAP
    dx = ax - bx
    dz = az - bz
    d2 = dx * dx + dz * dz
    if d2 >= min_d * min_d:
        return None
    if d2 < 1e-8:
        # 完全重合：随便挑一个方向，否则除零。
        return (min_d * 0.5, 0.0)
    d = math.sqrt(d2)
    overlap = (min_d - d) * 0.5
    return (dx / d * overlap, dz / d * overlap)
