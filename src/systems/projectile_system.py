"""远程敌人的弹丸：低速、可见、可躲。

为什么不用 hitscan：
规划书 §5.3 特意把 shooter 设计成「蓄力 0.8s → 射出**低速**弹丸」。
hitscan 的子弹无法躲，玩家只能靠掩体，开阔场地里就是「站着挨打」。
弹丸飞 20 米要接近 1 秒，配合前摇，玩家有明确的两段反应窗口 ——
这才是「玩家只会后退会被打死，但会走位就能活」的关键。

命中判定用「线段 vs 球」而不是逐帧点检测：
22 m/s × 1/60 s ≈ 0.37 米/帧，比玩家还宽，逐帧点检测会直接穿过去。
"""
from __future__ import annotations

from ursina import Entity, Vec3, color

from src.core import config, event_bus
from src.core.pool import EntityPool
from src.core.ray import raycast


def segment_hits_sphere(p0, p1, center, radius: float) -> bool:
    """线段到球心的最近距离是否小于半径。

    纯函数，单测直接给坐标；也解释了为什么必须是「线段」——
    只测终点的话，高速弹丸会在两帧之间整个穿过玩家。
    """
    ax, ay, az = p0[0], p0[1], p0[2]
    bx, by, bz = p1[0], p1[1], p1[2]
    cx, cy, cz = center[0], center[1], center[2]

    dx, dy, dz = bx - ax, by - ay, bz - az
    seg2 = dx * dx + dy * dy + dz * dz
    if seg2 < 1e-12:
        px, py, pz = ax, ay, az
    else:
        t = ((cx - ax) * dx + (cy - ay) * dy + (cz - az) * dz) / seg2
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        px, py, pz = ax + dx * t, ay + dy * t, az + dz * t

    ex, ey, ez = px - cx, py - cy, pz - cz
    return ex * ex + ey * ey + ez * ez <= radius * radius


class ProjectileSystem:
    def __init__(self, player, enemies=None) -> None:
        self.player = player
        self.enemies = enemies
        self.pool = EntityPool(self._make, config.PROJECTILE_POOL_SIZE)
        # [entity, vx, vy, vz, 剩余寿命, 伤害]
        self._active: list[list] = []
        self.dropped = 0
        self.shots = 0
        self.hits = 0

    @staticmethod
    def _make() -> Entity:
        return Entity(
            model="sphere",
            color=color.rgb32(255, 190, 80),
            scale=config.PROJECTILE_RADIUS * 2,
        )

    def fire(self, origin: Vec3, direction: Vec3, speed: float, damage: float) -> None:
        entity = self.pool.acquire()
        if entity is None:
            # 池空就丢弃，不扩容（规划书 §4.5）：突发流量下新建对象只会更卡。
            self.dropped += 1
            return
        self.shots += 1
        entity.position = Vec3(origin)
        self._active.append([
            entity, direction.x * speed, direction.y * speed, direction.z * speed,
            config.PROJECTILE_LIFE, damage,
        ])

    def clear(self) -> None:
        for item in self._active:
            self.pool.release(item[0])
        self._active.clear()

    def update(self, dt: float) -> None:
        if not self._active:
            return

        player = self.player
        alive_target = player is not None and player.alive
        # 玩家与所有敌人都不参与「挡子弹」：敌人的子弹不该被自己人拦下，
        # 玩家用球体判定单独处理（要兼顾无敌帧）。
        ignore = [player] if player is not None else []
        if self.enemies is not None:
            ignore.extend(self.enemies.enemies)

        hit_radius = config.PLAYER_RADIUS + config.PROJECTILE_RADIUS
        player_center = Vec3(player.x, player.y, player.z) if player is not None else None

        still: list[list] = []
        for item in self._active:
            entity, vx, vy, vz, life, damage = item
            prev = Vec3(entity.position)
            step = Vec3(vx * dt, vy * dt, vz * dt)
            nxt = prev + step
            life -= dt

            hit_player = (
                alive_target
                and segment_hits_sphere(prev, nxt, player_center, hit_radius)
            )
            if hit_player:
                self.hits += 1
                player.take_damage(damage)
                self.pool.release(entity)
                continue

            length = step.length()
            world_hit = None
            if length > 1e-6:
                world_hit = raycast(prev, step / length, distance=length, ignore=ignore)
            if world_hit is not None and world_hit.hit:
                event_bus.emit("projectile_impact", position=world_hit.world_point)
                self.pool.release(entity)
                continue

            if life <= 0.0:
                self.pool.release(entity)
                continue

            entity.position = nxt
            item[4] = life
            still.append(item)

        self._active = still
