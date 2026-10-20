"""拾取道具：5 种 + 掉落表 + 拾取效果（规划书 §5.5）。

**为什么道具不带 collider —— 这是有意偏离规划书的一处**

规划书 §5.5 写「池对象 `acquire()` 后要重设 collider（`release()` 时已置空），
否则捡不到」。那条规则成立的前提是"用碰撞触发拾取"。这里不用碰撞触发，
而是每帧算一次水平距离：

1. 道具是**非实体装饰**，不应该挡子弹。一旦挂上 collider，
   玩家对着地上的血包开枪，射线会停在道具上 —— 表现为「子弹被道具吃掉」。
   `weapon._fire()` 里 `enemy_from(道具)` 返回 None，于是既没伤害也没有命中反馈。
2. 距离判定只有 5 个道具 × 1 次平方距离比较，比维护一圈碰撞体便宜得多。
3. 池化对象不带 collider，`release()` 就不需要做"摘掉再挂上"的来回操作，
   少一个出错点。

所以池对象的 `collider` 全程是 `None`。**如果以后改成碰撞触发，
必须补上 `acquire()` 后 `collider = 'box'`，否则会变成"看得见捡不到"。**

**满血 / 弹匣满时不拾取**：血包和弹药会留在地上，玩家可以打完再回来捡。
直接消耗掉会让玩家看到"捡了没反应"，比留在地上更糟。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ursina import Entity, color

from src.core import config, event_bus
from src.core.log import get_logger
from src.core.pool import EntityPool

log = get_logger("pickup")

# 掉落权重表。想调"更常掉血包"就改这里，代码里没有第二个地方写权重。
DROP_TABLE: tuple[tuple[str, int], ...] = (
    ("health", 30),
    ("ammo", 25),
    ("gem", 25),
    ("speed", 10),
    ("shield", 10),
)

KINDS: tuple[str, ...] = tuple(k for k, _ in DROP_TABLE)

# 外形。cube / sphere 决定用哪个池，其余是 acquire 后要贴上去的属性。
_LOOK: dict[str, dict] = {
    "health": {"shape": "cube", "rgb": (78, 220, 120), "scale": 0.34, "alpha": 1.0},
    "ammo": {"shape": "cube", "rgb": (241, 198, 62), "scale": 0.36, "alpha": 1.0},
    "speed": {"shape": "sphere", "rgb": (92, 160, 255), "scale": 0.34, "alpha": 1.0},
    "gem": {"shape": "cube", "rgb": (186, 108, 242), "scale": 0.30, "alpha": 1.0},
    "shield": {"shape": "sphere", "rgb": (92, 230, 230), "scale": 0.38, "alpha": 0.62},
}

LABELS: dict[str, str] = {
    "health": "HEALTH",
    "ammo": "AMMO",
    "speed": "SPEED",
    "gem": "SCORE",
    "shield": "SHIELD",
}

# 每种道具的主题色（0~255）。HUD 飘字和拾取闪光**共用这一份**，
# 两边各写一份的话，改了颜色只改一半，玩家会看到"蓝字配绿光"。
PICKUP_RGB: dict[str, tuple[int, int, int]] = {
    "health": (110, 240, 150),
    "ammo": (250, 210, 90),
    "speed": (110, 170, 255),
    "gem": (200, 130, 250),
    "shield": (110, 240, 240),
}


@dataclass(frozen=True)
class PickupResult:
    """一次拾取的实际效果。`amount<=0` 表示这次没捡起来（效果被浪费）。"""
    kind: str
    amount: int
    label: str


# ---------------------------------------------------------------- 纯逻辑

def roll_drop(rng: random.Random, chance: float | None = None,
              table: tuple[tuple[str, int], ...] = DROP_TABLE) -> str | None:
    """先掷概率，再按权重轮盘选类型。返回 None 表示不掉落。

    两次随机而不是一次查表：查表要展开成十几项常量数组，
    改权重就得重算数组；两段式只改 `DROP_TABLE` 的数字。
    """
    if chance is None:
        chance = config.PICKUP_DROP_CHANCE
    if rng.random() >= chance:
        return None

    total = sum(weight for _, weight in table)
    if total <= 0:
        return None
    roll = rng.random() * total
    for kind, weight in table:
        roll -= weight
        if roll <= 0.0:
            return kind
    return table[-1][0]        # 浮点误差兜底


def can_pickup(kind: str, player, weapon) -> bool:
    """血包满血不捡、弹药满弹匣不捡。其余无限制。"""
    if kind == "health":
        return player.hp < player.max_hp - 1e-6
    if kind == "ammo":
        current = weapon.current
        return current.ammo < current.mag_size
    return True


def apply_pickup(kind: str, player, weapon) -> PickupResult:
    """施加效果。**不判断能不能捡** —— 那是 `can_pickup` 的职责。

    `player` / `weapon` 是鸭子类型，测试里塞 SimpleNamespace 即可。
    """
    if kind == "health":
        healed = round(player.heal(config.HEALTH_PACK_AMOUNT))
        return PickupResult(kind, healed, f"+{healed} HP")
    if kind == "ammo":
        added = int(weapon.refill_current())
        return PickupResult(kind, added, f"+{added} AMMO")
    if kind == "speed":
        player.grant_speed_boost(config.SPEED_BOOST_MULT, config.SPEED_BOOST_TIME)
        return PickupResult(kind, int(config.SPEED_BOOST_TIME), "SPEED UP")
    if kind == "shield":
        charges = player.add_shield(config.SHIELD_MAX_CHARGES)
        return PickupResult(kind, charges, f"SHIELD x{charges}")
    if kind == "gem":
        return PickupResult(kind, config.GEM_SCORE, f"+{config.GEM_SCORE}")
    return PickupResult(kind, 0, LABELS.get(kind, kind))


# ---------------------------------------------------------------- 系统

def _make_cube() -> Entity:
    return Entity(model="cube")


def _make_sphere() -> Entity:
    return Entity(model="sphere")


class PickupSystem:
    def __init__(self, player, weapon, rng: random.Random | None = None) -> None:
        self.player = player
        self.weapon = weapon
        self.rng = rng or random.Random(20261018)

        self._pools = {
            "cube": EntityPool(_make_cube, config.PICKUP_POOL_CUBE),
            "sphere": EntityPool(_make_sphere, config.PICKUP_POOL_SPHERE),
        }
        # [entity, kind, shape, base_y, phase, life, spin]
        self._active: list[list] = []

        self.spawned = 0
        self.collected = 0
        self.expired = 0
        self.overflows = 0

    # ---- 生成 ----
    def spawn(self, kind: str, x: float, z: float) -> Entity | None:
        look = _LOOK.get(kind)
        if look is None:
            log.warning(f"[M4] 未知道具类型 {kind!r}，跳过")
            return None

        shape = look["shape"]
        entity = self._pools[shape].acquire()
        if entity is None:
            # 池空就丢掉这次掉落，不扩容（规划书 §4.5）。
            self.overflows += 1
            return None

        base_y = config.PICKUP_BASE_HEIGHT
        entity.position = (x, base_y, z)
        entity.color = color.rgb32(*look["rgb"])
        entity.scale = look["scale"]
        # 宝石摆成菱形，其余保持正立；rotation_y 每帧自转。
        entity.rotation = (45, 0, 45) if kind == "gem" else (0, 0, 0)
        entity.alpha = look["alpha"]
        # 关键：不带 collider，否则会挡子弹（见模块 docstring）。
        entity.collider = None
        entity.visible = True

        self._active.append([entity, kind, shape, base_y, 0.0,
                             config.PICKUP_LIFETIME, 0.0])
        self.spawned += 1
        return entity

    def on_enemy_died(self, enemy=None, **_kwargs) -> None:
        """敌人死亡时按概率掉落。事件参数只有实体，位置从实体上取。"""
        if enemy is None:
            return
        kind = roll_drop(self.rng)
        if kind is None:
            return
        self.spawn(kind, enemy.x, enemy.z)

    # ---- 回收 ----
    def _release(self, item: list) -> None:
        entity, shape = item[0], item[2]
        entity.visible = False
        self._pools[shape].release(entity)

    def clear(self) -> None:
        for item in self._active:
            self._release(item)
        self._active.clear()

    def reset(self) -> None:
        self.clear()
        self.spawned = 0
        self.collected = 0
        self.expired = 0
        self.overflows = 0

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        if not self._active:
            return

        player = self.player
        can_take = player is not None and player.alive
        px, pz = (player.x, player.z) if can_take else (0.0, 0.0)
        radius2 = config.PICKUP_RADIUS * config.PICKUP_RADIUS
        float_speed = math.tau * config.PICKUP_FLOAT_HZ

        still: list[list] = []
        for item in self._active:
            entity = item[0]
            kind = item[1]

            item[5] -= dt
            life = item[5]
            if life <= 0.0:
                self._release(item)
                self.expired += 1
                continue

            # 浮动与自转全部由主循环的 dt 驱动，不读 time.time()：
            # 否则暂停时道具还在转，玩家一眼就看出是"假暂停"。
            item[4] += dt * float_speed
            item[6] += dt * config.PICKUP_SPIN_RATE
            entity.y = item[3] + math.sin(item[4]) * config.PICKUP_FLOAT_AMP
            entity.rotation_y = item[6]

            # 最后几秒闪烁，提示"快消失了"。
            if life <= config.PICKUP_BLINK_TIME:
                entity.visible = math.sin(life * math.tau * config.PICKUP_BLINK_HZ) > 0.0
            elif not entity.visible:
                entity.visible = True

            if can_take and can_pickup(kind, player, self.weapon):
                dx = entity.x - px
                dz = entity.z - pz
                if dx * dx + dz * dz <= radius2:
                    result = apply_pickup(kind, player, self.weapon)
                    event_bus.emit("pickup", kind=result.kind,
                                   amount=result.amount, label=result.label)
                    self._release(item)
                    self.collected += 1
                    continue

            still.append(item)

        self._active = still

    # ---- 统计 ----
    @property
    def active_count(self) -> int:
        return len(self._active)
