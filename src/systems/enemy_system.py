"""敌人总调度：感知（8Hz）→ 行为 → 移动 → 分离 → 攻击。

**顺序不能换**：
1. 先感知，拿到「这一帧谁能看见玩家」；
2. 再让每个 Brain 出意图；
3. 才把意图应用到位置；
4. 分离力放在最后 —— 先分离再移动会互相抵消，敌人会原地抖动。

感知按 8Hz 闸门跑，但**每个敌人每帧仍会 tick**：
状态机的时间推进（前摇、冷却、丢失计时）必须是连续时间，
只有「视线判定」这种昂贵的部分才降频。
"""
from __future__ import annotations

import math
from functools import partial

from ursina import Vec3, destroy

from src.core import config, event_bus
from src.core.enemy_types import EnemyData, load_or_fallback
from src.core.log import get_logger
from src.core.ray import raycast
from src.entities.enemy import Enemy, TargetDummy, spawn_enemy
from src.systems.enemy_ai import BrainState, separation_push
from src.systems.perception import PerceiveThrottle, can_see, turn_toward, yaw_to

log = get_logger("enemies")


class EnemySystem:
    def __init__(self, arena=None, player=None, projectiles=None) -> None:
        self.arena = arena
        self.player = player
        self.projectiles = projectiles
        self.enemies: list[Enemy] = []
        self.types: dict[str, EnemyData] = load_or_fallback()
        self.perception = PerceiveThrottle(config.PERCEPTION_HZ)
        self.spawned_total = 0

        # 枪声 = 噪声事件。这里**不自己订阅**，由 `core/session.py`
        # 的 register_listeners() 统一注册。
        # 原因：重开一局要调 event_bus.clear_listeners()，分散在各处的
        # on() 会被一次性清掉且不会自己回来 —— 表现是"重开后开枪不再惊动敌人"，
        # 没有任何报错。集中注册才能保证"清掉之后一定重新挂上"。

    # ---- 生成 / 移除 ----
    def add(self, enemy: Enemy) -> Enemy:
        self.enemies.append(enemy)
        return enemy

    def spawn_targets(self) -> None:
        """M2 验收用的静态靶子。位置在 config 里，避开固定种子的柱子。"""
        for x, z in config.TARGET_POSITIONS:
            self.add(TargetDummy(x, z))

    def spawn_type(self, type_name: str, x: float, z: float,
                   hp_mult: float = 1.0, speed_mult: float = 1.0,
                   yaw: float | None = None) -> Enemy | None:
        data = self.types.get(type_name)
        if data is None:
            log.warning(f"[M3] 未知敌人类型 {type_name!r}，跳过")
            return None
        if yaw is None:
            # 默认朝玩家出生。默认 0（朝 +Z）会让整波怪背对场地中心，
            # 要等待机扫视转过来才发现玩家，看起来像 AI 坏了。
            yaw = (yaw_to(self.player.x - x, self.player.z - z)
                   if self.player is not None else 0.0)
        self.spawned_total += 1
        return self.add(spawn_enemy(type_name, data, x, z, hp_mult, speed_mult, yaw))

    def clear(self) -> None:
        """M4 重开用。不在遍历中销毁，统一走延迟队列。"""
        for enemy in self.enemies:
            event_bus.defer(destroy, enemy)
        self.enemies.clear()
        self.perception.reset()

    def on_noise(self, position=None, radius: float = 0.0, **_ignored) -> None:
        """枪声惊动半径内的敌人。名字是 public 的：订阅在 core/session.py 里注册。"""
        if self.player is None or position is None:
            return
        nx, nz = position[0], position[2]
        r2 = radius * radius
        for enemy in self.enemies:
            if not enemy.alive or enemy.brain is None:
                continue
            dx = enemy.x - nx
            dz = enemy.z - nz
            if dx * dx + dz * dz <= r2:
                enemy.brain.hear(nx, nz)

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        if not self.enemies:
            return

        # 玩家死了就不再推进 AI：否则尸体会被一群怪围着继续打，
        # GAME_OVER 的慢镜头里看着像bug。
        active = self.player is not None and self.player.alive
        if active:
            if self.perception.ready(dt):
                self._perceive()
            self._think_and_move(dt)
            self._separate()

        for enemy in tuple(self.enemies):
            enemy.update_visual(dt)
            if enemy.removed:
                self.enemies.remove(enemy)
                # 不能在遍历中直接销毁，交给主循环末尾统一 flush。
                event_bus.defer(destroy, enemy)

    def _perceive(self) -> None:
        """8Hz：只有「距离 + 夹角」都通过的敌人才会打一条遮挡射线。"""
        player = self.player
        px, pz = player.x, player.z
        py = player.y + 0.2
        for enemy in self.enemies:
            if not enemy.alive or enemy.brain is None:
                continue
            dx = px - enemy.x
            dz = pz - enemy.z
            data = enemy.data
            assert data is not None
            sees = can_see(
                dx, dz, enemy.rotation_y, data.view_range, config.VIEW_HALF_ANGLE,
                blocked=partial(self._blocked, enemy, px, py, pz, dx, dz),
            )
            enemy.sees_player = sees

    def _blocked(self, enemy: Enemy, px: float, py: float, pz: float,
                 dx: float, dz: float) -> bool:
        """敌人眼睛 → 玩家胸口之间有没有东西挡住。

        忽略自己（否则射线一出门就命中自己的碰撞体）。
        **不忽略玩家**：命中玩家本身就是「看得见」的证据，
        反过来把玩家忽略掉，射线会穿过去打到后面的墙，判成被挡。
        """
        dist = math.hypot(dx, dz)
        if dist < 1e-4:
            return False
        eye = Vec3(enemy.x, enemy.y + enemy.scale_y * 0.3, enemy.z)
        target = Vec3(px, py, pz)
        direction = (target - eye).normalized()
        hit = raycast(eye, direction, distance=dist + 1.0, ignore=[enemy])
        if not hit.hit:
            return False
        if hit.entity is self.player:
            return False
        # 命中点几乎贴着玩家（比如玩家自己的枪、或其他敌人挡在脸上）时仍算看见。
        return hit.distance < dist - 0.2

    def _think_and_move(self, dt: float) -> None:
        player = self.player
        if player is None:
            return
        px, pz = player.x, player.z

        for enemy in self.enemies:
            if not enemy.alive or enemy.brain is None:
                continue
            brain = enemy.brain
            data = enemy.data
            assert data is not None

            brain.tick(dt, enemy.x, enemy.z, px, pz, enemy.sees_player)
            enemy.rotation_y = turn_toward(
                enemy.rotation_y, brain.face_yaw, config.ENEMY_TURN_RATE * dt)

            # 前摇时站定不动（这就是玩家能躲的窗口），蓄力缩放做视觉预警。
            if brain.state is BrainState.ATTACK and data.attack_windup > 0:
                k = 1.0 - max(brain.windup, 0.0) / data.attack_windup
                enemy.scale = enemy.base_scale * (1.0 + config.ATTACK_TELEGRAPH_SCALE * k)
                enemy.telegraphing = True
            elif enemy.telegraphing:
                enemy.scale = enemy.base_scale
                enemy.telegraphing = False

            if brain.move_x or brain.move_z:
                speed = data.speed * enemy.speed_mult
                enemy.x += brain.move_x * speed * dt
                enemy.z += brain.move_z * speed * dt
                if self.arena is not None:
                    self.arena.clamp(enemy, enemy.radius)
                    self.arena.resolve_obstacles(enemy, enemy.radius)

            # 结算攻击。距离在这里重新量一次：前摇 0.3 秒里玩家可能已经退开，
            # 用 0.3 秒前的距离结算会打出「隔空一掌」。
            if brain.attack_now:
                self._resolve_melee(enemy, data, px, pz)
            if brain.shoot_now:
                self._resolve_ranged(enemy, data)

    def _resolve_melee(self, enemy: Enemy, data: EnemyData, px: float, pz: float) -> None:
        dx = px - enemy.x
        dz = pz - enemy.z
        if math.hypot(dx, dz) <= data.attack_range + config.ATTACK_RANGE_TOLERANCE:
            self.player.take_damage(data.damage)

    def _resolve_ranged(self, enemy: Enemy, data: EnemyData) -> None:
        if self.projectiles is None:
            return
        origin = Vec3(enemy.x, enemy.y + enemy.scale_y * 0.25, enemy.z)
        aim = Vec3(self.player.x, self.player.y, self.player.z)
        direction = (aim - origin)
        if direction.length() < 1e-4:
            return
        self.projectiles.fire(origin, direction.normalized(),
                              speed=data.projectile_speed, damage=data.damage)

    def _separate(self) -> None:
        """两两推开。30 个敌人 = 435 对/帧，纯平方距离比较，开销可忽略。"""
        alive = [e for e in self.enemies if e.alive and e.brain is not None]
        n = len(alive)
        for i in range(n):
            a = alive[i]
            for j in range(i + 1, n):
                b = alive[j]
                push = separation_push(a.x, a.z, b.x, b.z, a.radius, b.radius)
                if push is None:
                    continue
                ax, az = push
                a.x += ax
                a.z += az
                b.x -= ax
                b.z -= az

        if self.arena is not None:
            for enemy in alive:
                self.arena.clamp(enemy, enemy.radius)

    # ---- 统计 ----
    @property
    def alive_count(self) -> int:
        return sum(1 for e in self.enemies if e.alive)

    def brain_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.enemies:
            if e.alive and e.brain is not None:
                key = e.brain.state.value
                counts[key] = counts.get(key, 0) + 1
        return counts
