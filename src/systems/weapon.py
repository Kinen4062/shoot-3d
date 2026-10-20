"""武器系统：hitscan 判定 + 弹药/换弹 + 表现。

**命中机制只有一种**（规划书 §5.2）：伤害交给 `raycast` 瞬时结算，
拖尾是纯视觉、飞完即回收。不要在这里引入物理弹道 ——
两者并存会导致「看得见子弹飞过去，伤害却已经结算完了」的自相矛盾。

**射线从相机出发，不是从枪口**。这是 TPS 的铁律：
准星在敌人身上就必须打中，枪口只决定拖尾的起点。
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass

from ursina import Vec3, mouse

from src.core import config, event_bus
from src.core.game_state import State
from src.core.ray import raycast
from src.core.screen_ray import log_geometry_once, screen_center_ray
from src.entities.enemy import enemy_from


# ---------------------------------------------------------------- 数据

@dataclass(frozen=True)
class WeaponData:
    name: str
    damage: float
    fire_rate: float        # 秒 / 发
    mag_size: int
    reload_time: float
    spread: float           # 度（腰射）
    range: float
    auto: bool
    pellets: int = 1        # 霰弹枪预留，MVP 恒为 1


_REQUIRED = ("damage", "fire_rate", "mag_size", "reload_time", "spread", "range", "auto")


def _weapon_from(name: str, raw: dict) -> WeaponData:
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise ValueError(f"武器 {name!r} 缺少字段 {missing}")
    return WeaponData(
        name=name,
        **{k: raw[k] for k in _REQUIRED},
        pellets=int(raw.get("pellets", 1)),
    )


def load_weapons(path: str) -> dict[str, WeaponData]:
    with open(path, encoding="utf-8") as fp:
        raw = json.load(fp)
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{path} 不是预期的武器表")
    return {name: _weapon_from(name, item) for name, item in raw.items()}


# 打包后 data/ 丢了也要能玩：内置一份和 weapons.json 一致的兜底
FALLBACK_WEAPONS: dict[str, WeaponData] = {
    "pistol": WeaponData("pistol", 25, 0.25, 12, 1.2, 0.25, 60, False, 1),
    "rifle": WeaponData("rifle", 18, 0.1, 30, 1.8, 0.5, 80, True, 1),
}


# ---------------------------------------------------------------- 纯逻辑

class WeaponState:
    """一把枪的运行时状态。不依赖引擎，单测直接实例化。"""

    def __init__(self, data: WeaponData) -> None:
        self.data = data
        self.ammo = data.mag_size
        self.cooldown = 0.0
        self.reload_left = 0.0

    @property
    def reloading(self) -> bool:
        return self.reload_left > 0.0

    def update(self, dt: float) -> None:
        if self.cooldown > 0.0:
            self.cooldown -= dt
        if self.reload_left > 0.0:
            self.reload_left -= dt
            if self.reload_left <= 0.0:
                self.reload_left = 0.0
                self.ammo = self.data.mag_size

    def can_fire(self) -> bool:
        return self.cooldown <= 0.0 and not self.reloading and self.ammo > 0

    def consume(self) -> bool:
        """扣一发。返回 False 表示这一枪打不出去。"""
        if not self.can_fire():
            return False
        self.ammo -= 1
        self.cooldown = self.data.fire_rate
        if self.ammo == 0:
            self.request_reload()      # 打空自动换弹
        return True

    def request_reload(self) -> bool:
        if self.reloading or self.ammo >= self.data.mag_size:
            return False
        self.reload_left = self.data.reload_time
        # 换弹提示音从这里发，而不是从输入层发：
        # 打空弹匣会自动换弹（consume() 里调这个方法），那条路径不经过按键。
        event_bus.emit("reload_started", weapon=self.data.name)
        return True


def effective_spread(aiming: bool, data: WeaponData) -> float:
    """这一发的真实散布角（度）。

    **瞄准时严格为 0，不做任何缩放。** M2 的验收标准是「准星压住就必中」，
    只要留下一个小角度，远距离就会有肉眼可见的偏差；
    而腰射保留散布是为了给「不开镜」一点代价。

    `config.SPREAD_DISABLED` 是排查开关：打开后连腰射也归零，
    用来把「散布太大」和「弹道方向偏了」两类问题分开。
    """
    if config.SPREAD_DISABLED or aiming:
        return 0.0
    return data.spread


def apply_spread(direction: Vec3, spread_deg: float, rng: random.Random | None = None) -> Vec3:
    """把射向在圆锥内随机偏一点。spread_deg = 0 时原样返回。

    采样方式：极角取 sqrt(u) 保证圆盘内均匀，否则弹着点会堆在中心。
    """
    if spread_deg <= 0.0:
        return direction

    rng = rng or random
    radius = math.radians(spread_deg)
    theta = radius * math.sqrt(rng.random())
    phi = rng.random() * math.tau

    axis = Vec3(direction).normalized()
    helper = Vec3(0, 1, 0) if abs(axis.y) < 0.99 else Vec3(1, 0, 0)
    right = axis.cross(helper).normalized()
    up = right.cross(axis).normalized()

    offset = right * (math.tan(theta) * math.cos(phi)) + up * (math.tan(theta) * math.sin(phi))
    return (axis + offset).normalized()


# ---------------------------------------------------------------- 系统

class WeaponSystem:
    def __init__(self, player, rig, state, enemies, tracer, crosshair, gun) -> None:
        self.player = player
        self.rig = rig
        self.state = state
        self.enemies = enemies
        self.tracer = tracer
        self.crosshair = crosshair
        self.gun = gun

        self.weapons = self._load()
        self.slots = list(self.weapons.keys())
        self.index = self.slots.index(config.DEFAULT_WEAPON) if config.DEFAULT_WEAPON in self.slots else 0
        self.states = {name: WeaponState(data) for name, data in self.weapons.items()}

        self.trigger = False
        self.aiming = False
        self.shots = 0
        self.hits = 0
        # 最近一发的命中诊断：覆盖层会显示，用来区分「打偏了」和「打中了但没判成敌人」。
        self.last_hit_name = "-"
        self.last_damaged = False
        self._fired_this_press = False
        self._rng = random.Random(20261017)
        self._ignore = [player, gun.entity]

    # ---- 只读属性（覆盖层/HUD 用）----
    @property
    def current(self) -> WeaponState:
        return self.states[self.slots[self.index]]

    @property
    def weapon_name(self) -> str:
        return self.slots[self.index]

    @property
    def spread_deg(self) -> float:
        """当前这一发的真实散布角（度）。瞄准时恒为 0。"""
        return effective_spread(self.aiming, self.current.data)

    # ---- 数据加载 ----
    def _load(self) -> dict[str, WeaponData]:
        path = config.resource_path(config.WEAPON_FILE)
        try:
            data = load_weapons(path)
            summary = "  ".join(f"{k}:{v.damage}伤/{v.fire_rate}s/{v.mag_size}发" for k, v in data.items())
            print(f"[M2] weapons loaded -> {summary}")
            return data
        except (OSError, ValueError, KeyError) as exc:
            print(f"[M2] 武器表读取失败（{exc}），改用内置兜底数值")
            return dict(FALLBACK_WEAPONS)

    # ---- 输入回调 ----
    def on_fire_down(self) -> None:
        self.trigger = True
        self._fired_this_press = False

    def on_fire_up(self) -> None:
        self.trigger = False
        self._fired_this_press = False

    def start_reload(self) -> None:
        self.current.request_reload()

    def set_aiming(self, aiming: bool) -> None:
        self.aiming = aiming
        self.rig.set_aiming(aiming)

    def switch(self, index: int) -> None:
        if 0 <= index < len(self.slots) and index != self.index:
            self.index = index
            self._fired_this_press = False

    def refill_current(self) -> int:
        """弹药箱：把当前弹匣补满，返回补了几发。

        **必须同时清掉换弹计时**。否则会出现：弹匣空了正在换弹，
        捡到弹药箱后弹匣满了，但 1.8 秒的换弹动画还在跑，
        跑完又"补"一次，玩家看到弹药数没变却卡了 1.8 秒不能开枪。
        """
        weapon = self.current
        before = weapon.ammo
        weapon.reload_left = 0.0
        weapon.ammo = weapon.data.mag_size
        return weapon.ammo - before

    def reset(self) -> None:
        """重开一局。所有枪回满弹、状态清零、切回默认武器。"""
        for weapon in self.states.values():
            weapon.ammo = weapon.data.mag_size
            weapon.cooldown = 0.0
            weapon.reload_left = 0.0
        default = config.DEFAULT_WEAPON
        self.index = self.slots.index(default) if default in self.slots else 0
        self.trigger = False
        self.aiming = False
        self.shots = 0
        self.hits = 0
        self.last_hit_name = "-"
        self.last_damaged = False
        self._fired_this_press = False
        self.rig.set_aiming(False)

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        # 枪口火焰的计时不受暂停影响：否则刚开完枪就按 ESC，火球会一直挂在那里。
        self.gun.update_flash(dt)

        if not self.state.in_state(State.PLAYING):
            self.trigger = False
            self.set_aiming(False)
            return

        # 以鼠标的实时状态为准，而不是只靠 down/up 事件。
        # 事件会在「按住时切窗口 / 松开时失去焦点」这类情况下丢一个，
        # 丢了 down 就永远进不了瞄准态（表现为右键没反应、散布不归零），
        # 丢了 up 则会一直连发。每帧对一次，两种都修掉。
        self._sync_input()

        weapon = self.current
        weapon.update(dt)

        self.gun.follow(self.player, self.rig)

        if not self.trigger:
            return
        if not weapon.data.auto and self._fired_this_press:
            return          # 半自动：按住不放只打一发
        self._fire(weapon)

    def _sync_input(self) -> None:
        """把 mouse.left / mouse.right 同步成本系统的 trigger / aiming。

        保留 on_fire_down/up 作为事件入口：它们负责「按下瞬间」的边沿语义，
        这里只在状态确实不一致时才补一刀，不会重复触发。
        """
        if mouse.right != self.aiming:
            self.set_aiming(mouse.right)

        if mouse.left:
            if not self.trigger:
                self.on_fire_down()
        elif self.trigger:
            self.on_fire_up()

    def _fire(self, weapon: WeaponState) -> None:
        if not weapon.consume():
            return

        self._fired_this_press = True
        self.shots += 1
        log_geometry_once()

        self.gun.show_flash()
        self.rig.add_recoil(config.RECOIL_DEG)
        self.crosshair.on_shot()

        # 枪声 = 听觉事件（规划书 §5.3）。半径内的敌人会被惊动并走向这里。
        # 用事件而不是直接调 EnemySystem：武器层不认识敌人，M4 加音效时
        # 同一个事件可以挂多个监听者，不用改武器代码。
        event_bus.emit("noise", position=self.player.world_position,
                       radius=config.NOISE_RADIUS)

        # 方向走 lens 反投影（屏幕正中心），不用 camera.forward：
        # 后者取节点局部 +Z，它是否等于投影轴取决于引擎内部约定，
        # 一旦不一致，弹道整体偏离准星、且不报任何错。
        origin, direction = screen_center_ray()
        # 瞄准时 spread_deg 恒为 0，射线与准星完全共线：
        # 「准星压住就必中」靠的就是这一条。
        direction = apply_spread(direction, self.spread_deg, self._rng)

        hit = raycast(origin, direction, distance=weapon.data.range, ignore=self._ignore)
        end = hit.world_point if hit.hit else origin + direction * weapon.data.range

        self.last_hit_name = "none"
        self.last_damaged = False
        if hit.hit:
            self.last_hit_name = getattr(hit.entity, "name", None) or "?"
            target = enemy_from(hit.entity)
            if target is not None:
                self.hits += 1
                target.take_damage(weapon.data.damage)
                self.crosshair.on_hit()
                # 命中音效走事件：M5 的音频系统订阅它，武器层不需要认识音频。
                # 击杀会紧接着发 enemy_died，两者音色不同，叠在一起才有打击感。
                event_bus.emit("enemy_hit", damage=weapon.data.damage)
                self.last_damaged = True

        # 瞄准状态下没造成伤害才留一行现场：瞄准时散布为 0，
        # 「没打中靶子」就一定意味着弹道方向或遮挡有问题，必须能带出证据。
        # 腰射本来就允许偏，不打印，避免刷屏。
        if config.SHOT_MISS_LOG and self.aiming and not self.last_damaged:
            print(
                f"[shot] 瞄准未命中  spread={self.spread_deg:.2f}deg  last={self.last_hit_name}  "
                f"dist={hit.distance:5.2f}  origin={tuple(round(v, 2) for v in origin)}  "
                f"dir={tuple(round(v, 3) for v in direction)}"
            )

        self.tracer.spawn(self.gun.muzzle_world, end)
