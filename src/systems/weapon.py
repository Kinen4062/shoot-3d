"""武器系统：hitscan 判定 + 弹药/换弹 + 表现。

**命中机制只有一种**（规划书 §5.2）：伤害交给 `raycast` 瞬时结算，
拖尾是纯视觉、飞完即回收。不要在这里引入物理弹道 ——
两者并存会导致「看得见子弹飞过去，伤害却已经结算完了」的自相矛盾。

**射线从相机出发，不是从枪口**。这是 TPS 的铁律：
准星在敌人身上就必须打中，枪口只决定拖尾的起点。

**多弹丸（霰弹）靠一次扣弹打 N 条射线**，不是 N 发子弹：
`consume()` 一次只扣 1 发（一个弹壳），然后对同一个圆锥采 N 个方向，
每条射线各自算命中与距离衰减，最后**按目标合并伤害**再结算一次。
合并这一步不能省 —— 7 颗各自调 `take_damage()` 会发出 7 次 `enemy_hit`、
7 次闪白、7 声命中音，打击感会糊成一团噪音。

**距离分段**（`tight_range` / `wide_range`）只对声明了它的武器生效：
`tight_range` 以内散布最小、伤害全额；到 `wide_range` 散布线性涨到
`far_spread` 倍并停在那里；伤害在 `tight_range` 处**阶跃**到 `far_damage` 倍
（不是渐变 —— 规划里写的是"2 格外伤害减半"，一刀切更好读）。
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ursina import Vec3, mouse

from src.core import config, event_bus
from src.core.game_state import State
from src.core.log import get_logger
from src.core.ray import raycast
from src.core.screen_ray import log_geometry_once, screen_center_ray
from src.entities.enemy import enemy_from

if TYPE_CHECKING:
    # 只在标注里用到：波次系统反过来也依赖武器系统（掉落、解锁判断），
    # 运行时导入会成环。
    from src.systems.wave import WaveSystem

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
    pellets: int = 1        # 一次扣弹打几条射线。1 = 普通枪
    unlock_wave: int = 0    # 从第几波起可以切到这把枪（0 = 一开始就有）
    late_mag_size: int = 0  # 第 `MAG_UPGRADE_WAVE` 波起的弹匣容量。0 = 不升级
    # ---- 距离分段。tight_range = 0 表示这把枪不分段（手枪/步枪）----
    tight_range: float = 0.0    # 这个距离以内：最小散布 + 全额伤害
    wide_range: float = 0.0     # 到这个距离：散布涨到 far_spread 倍
    far_damage: float = 1.0     # 超过 tight_range 之后的伤害倍率
    far_spread: float = 1.0     # 到 wide_range 时（及以后）的散布倍率


_REQUIRED = ("damage", "fire_rate", "mag_size", "reload_time", "spread", "range", "auto")
# 可选字段：JSON 里没写就用 dataclass 的默认值。全部显式列出来，
# 避免哪天在 JSON 里写错键名（比如 "tight" 写成 "tight_range_"）却静默走默认值。
_OPTIONAL = ("pellets", "unlock_wave", "late_mag_size",
             "tight_range", "wide_range", "far_damage", "far_spread")


def _weapon_from(name: str, raw: dict) -> WeaponData:
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise ValueError(f"武器 {name!r} 缺少字段 {missing}")
    unknown = [k for k in raw if k not in _REQUIRED and k not in _OPTIONAL]
    if unknown:
        # 拼错的可选字段不会报错、只会静默失效（霰弹枪变成单发枪），
        # 所以在加载阶段就直接拦下来。
        raise ValueError(f"武器 {name!r} 有未知字段 {unknown}")
    kwargs = {k: raw[k] for k in _REQUIRED}
    if "pellets" in raw:
        kwargs["pellets"] = int(raw["pellets"])
    if "unlock_wave" in raw:
        kwargs["unlock_wave"] = int(raw["unlock_wave"])
    if "late_mag_size" in raw:
        kwargs["late_mag_size"] = int(raw["late_mag_size"])
    for key in ("tight_range", "wide_range", "far_damage", "far_spread"):
        if key in raw:
            kwargs[key] = float(raw[key])
    return WeaponData(name=name, **kwargs)


def load_weapons(path: str) -> dict[str, WeaponData]:
    with open(path, encoding="utf-8") as fp:
        raw = json.load(fp)
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{path} 不是预期的武器表")
    return {name: _weapon_from(name, item) for name, item in raw.items()}


# 打包后 data/ 丢了也要能玩：内置一份和 weapons.json 一致的兜底
FALLBACK_WEAPONS: dict[str, WeaponData] = {
    "pistol": WeaponData("pistol", 25, 0.25, 12, 1.2, 0.25, 60, False, 1,
                         late_mag_size=18),
    "rifle": WeaponData("rifle", 18, 0.1, 30, 1.8, 0.5, 80, True, 1,
                        late_mag_size=45),
    "shotgun": WeaponData(
        "shotgun", 20, 0.5, 15, 1.2, 2.5, 40, True,
        pellets=10, unlock_wave=10, late_mag_size=20,
        tight_range=4.0, wide_range=8.0, far_damage=0.5, far_spread=4.0,
    ),
}


# ---------------------------------------------------------------- 纯逻辑

class WeaponState:
    """一把枪的运行时状态。不依赖引擎，单测直接实例化。"""

    def __init__(self, data: WeaponData) -> None:
        self.data = data
        # **不是 `data.mag_size` 的只读代理**：第 20 波会换弹匣，
        # 而 `data` 是 frozen 的共享数据。所有容量判断都读这个字段，
        # 别再出现 `self.data.mag_size`（否则升级之后换弹会按老容量截断）。
        self.mag_size = data.mag_size
        self.ammo = self.mag_size
        self.cooldown = 0.0
        self.reload_left = 0.0

    def set_mag_size(self, size: int) -> None:
        """换弹匣。`size` 变大时**补上差额**，不吞玩家已经压着的子弹。

        比如第 20 波步枪 30 → 45：手上还剩 12 发，升级后变成 27 发。
        玩家不会因为"刚好在升级那一刻没补满"而吃亏，也不需要重装一遍。
        容量变小时截断到新上限（重开一局会把容量还原，这时不补）。
        """
        delta = size - self.mag_size
        self.mag_size = size
        if delta > 0:
            self.ammo = min(size, self.ammo + delta)
        else:
            self.ammo = min(self.ammo, size)
        # 不碰 `reload_left`：升级**不主动打断**正在进行的换弹。
        # 正在装填时换弹匣，装填结束时自然补到新容量（`update` 读的是 self.mag_size）。

    @property
    def reloading(self) -> bool:
        return self.reload_left > 0.0

    def update(self, dt: float, reload_scale: float = 1.0) -> None:
        """推进计时。`reload_scale` 只影响换弹，不影响射击冷却。

        后台武器（没拿在手上）的换弹用 0.5 倍速，所以这里必须能单独缩放 ——
        把整个 dt 乘上去的话，切走再切回来的枪冷却也会莫名其妙地变慢。
        """
        if self.cooldown > 0.0:
            self.cooldown -= dt
        if self.reload_left > 0.0:
            self.reload_left -= dt * reload_scale
            if self.reload_left <= 0.0:
                self.reload_left = 0.0
                self.ammo = self.mag_size

    def needs_reload(self) -> bool:
        return self.ammo < self.mag_size and not self.reloading

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
        if self.reloading or self.ammo >= self.mag_size:
            return False
        self.reload_left = self.data.reload_time
        # 换弹提示音从这里发，而不是从输入层发：
        # 打空弹匣会自动换弹（consume() 里调这个方法），那条路径不经过按键。
        event_bus.emit("reload_started", weapon=self.data.name)
        return True


def shot_sfx_for(weapon_name: str) -> str:
    """这把枪开火时该播哪个音效名。

    抽成纯函数是为了能在不起窗口的情况下钉住映射：
    "霰弹枪有专属枪声"这件事一旦退化成 `play("shoot")`，听感上只会觉得
    "换枪没什么区别"，没有任何报错会提示你。
    """
    return config.WEAPON_SHOT_SFX.get(weapon_name, config.DEFAULT_SHOT_SFX)


def wave_damage_bonus(wave: int) -> int:
    """第 `wave` 波时，**单颗弹丸**的额外伤害。

    每 `config.DAMAGE_BONUS_STEP`（5）波 +1：第 1~4 波 +0，第 5~9 波 +1，
    …… 第 30 波 +6，BOSS 层（31）+6。

    为什么是"每 5 波 +1"而不是按比例乘：按比例乘的话，三把枪的差距会
    被一起放大（手枪 25 → 34、步枪 18 → 24），后期仍然是"手枪最猛"；
    固定加值反而让弹丸多的枪收益更高（霰弹枪 10 颗 = 每波 +10 总伤），
    这正好是后期最需要补的那把。
    """
    return max(0, int(wave)) // config.DAMAGE_BONUS_STEP


def effective_spread(aiming: bool, data: WeaponData) -> float:
    """这一发的真实散布角（度），**不含距离分段**。

    **单发武器瞄准时严格为 0，不做任何缩放。** M2 的验收标准是
    「准星压住就必中」，只要留下一个小角度，远距离就会有肉眼可见的偏差；
    而腰射保留散布是为了给「不开镜」一点代价。

    多弹丸武器（霰弹）是唯一的例外：瞄准时收到 `SHOTGUN_AIM_MULT` 倍，
    不归零。归零等于 7 颗全中同一点 —— 一把精度无限、单发 140 的狙击枪，
    比步枪还强，霰弹枪的"距离分段"设计会整个失效。
    这个例外只影响 `pellets > 1`，单发武器仍然恒为 0。

    `config.SPREAD_DISABLED` 是排查开关：打开后连腰射也归零，
    用来把「散布太大」和「弹道方向偏了」两类问题分开。
    """
    if config.SPREAD_DISABLED:
        return 0.0
    if not aiming:
        return data.spread
    if data.pellets > 1:
        return data.spread * config.SHOTGUN_AIM_MULT
    return 0.0


def spread_at(data: WeaponData, distance: float, aiming: bool = False) -> float:
    """按**目标距离**算这一枪的散布角。

    分段规则只对声明了 `tight_range` 的武器生效：
    `tight_range` 以内用 `effective_spread` 的值，到 `wide_range` 线性涨到
    `far_spread` 倍，再远就停在那儿。没声明分段的武器恒等于
    `effective_spread`，行为和 M2 完全一致。
    """
    base = effective_spread(aiming, data)
    if data.tight_range <= 0.0 or distance <= data.tight_range:
        return base
    if data.wide_range <= data.tight_range:
        return base * data.far_spread
    span = data.wide_range - data.tight_range
    t = min(1.0, (distance - data.tight_range) / span)
    return base * (1.0 + (data.far_spread - 1.0) * t)


def damage_at(data: WeaponData, distance: float, bonus: float = 0.0) -> float:
    """单颗弹丸的伤害。超过 `tight_range` 直接阶跃到 `far_damage` 倍。

    用阶跃而不是渐变：规划里的口径是"2 格外伤害减半"，一刀切玩家能立刻感知到
    "该走近再打"；线性衰减在实战里只表现为"有时候伤害莫名其妙低一点"。

    `bonus` 是波次成长（`wave_damage_bonus`）。**先加再乘**：
    远距离的减半是"这一枪打得远"的惩罚，成长是"枪本身变强了"，
    两者是乘法关系 —— 先乘后加会让霰弹枪在 30 波时远距离反而更划算。
    """
    per_pellet = data.damage + bonus
    if data.tight_range > 0.0 and distance > data.tight_range:
        return per_pellet * data.far_damage
    return per_pellet


log = get_logger("weapon")

# 按了还没解锁的槽位时，HUD 上的提示持续多久（秒）。
LOCKED_HINT_TIME = 3.0


def apply_spread(direction: Vec3, spread_deg: float, rng: random.Random | None = None) -> Vec3:
    """把射向在圆锥内随机偏一点。spread_deg = 0 时原样返回。

    采样方式：极角取 sqrt(u) 保证圆盘内均匀，否则弹着点会堆在中心。
    """
    if spread_deg <= 0.0:
        return direction

    # 没传 rng 就用模块级的那套。两者都能提供 `.random()`，但类型不同
    # （`Random.random` 是绑定方法、模块里是普通函数），所以先取成样本函数 ——
    # 直接写 `rng = rng or random` 会让 `rng` 的标注从 `Random|None` 变成模块。
    sample = rng.random if rng is not None else random.random
    radius = math.radians(spread_deg)
    theta = radius * math.sqrt(sample())
    phi = sample() * math.tau

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
        # 波次来源由 `main.build_world` 后挂（和 `enemies.projectiles` 同一个约定）：
        # 武器比波次系统先构造，而且武器只需要读一个 `wave` 数字。
        # 传 None 就当作"第 0 波"——锁定武器全部不可用，不会静默解锁。
        # 波次系统比武器晚构造，这里后挂（用来判断霰弹枪解锁没有）。
        self.waves: WaveSystem | None = None

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
        # 按了锁定武器的槽位 → 记下名字和剩余提示时间，HUD 会读它。
        self.locked_name = ""
        self.locked_left = 0.0
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
        """当前这一发的真实散布角（度）。瞄准时单发武器恒为 0。"""
        return effective_spread(self.aiming, self.current.data)

    @property
    def wave_number(self) -> int:
        """当前波次。没有波次来源时返回 0（= 所有锁定武器都关着）。"""
        return int(getattr(self.waves, "wave", 0) or 0)

    @property
    def damage_bonus(self) -> int:
        """这一波每颗弹丸的额外伤害（每 5 波 +1，见 `wave_damage_bonus`）。"""
        return wave_damage_bonus(self.wave_number)

    def _sync_mag_sizes(self) -> None:
        """按当前波次对齐每把枪的弹匣容量（第 `MAG_UPGRADE_WAVE` 波换大弹匣）。

        每帧比一次而不是"在第 20 波那一刻触发一次"：波次是可以被跳过的
        （探针直接改 `waves.wave`、以后加调试跳波），事件式会在这些路径上失效，
        而"每帧对齐到目标值"天然幂等 —— 重开归零也会自动缩回去。
        """
        upgraded = self.wave_number >= config.MAG_UPGRADE_WAVE
        for state in self.states.values():
            target = state.data.mag_size
            if upgraded and state.data.late_mag_size > 0:
                target = state.data.late_mag_size
            if target != state.mag_size:
                before = state.mag_size
                state.set_mag_size(target)
                log.info(f"[M2] {state.data.name} 弹匣 {before} -> {target} 发")

    def available(self, name: str) -> bool:
        """这把枪现在能不能用。"""
        return self.weapons[name].unlock_wave <= self.wave_number

    def unlock_hint(self, name: str) -> str:
        return f"WAVE {self.weapons[name].unlock_wave}"

    def _tick_locked_hint(self, dt: float) -> None:
        if self.locked_left > 0.0:
            self.locked_left -= dt
            if self.locked_left <= 0.0:
                self.locked_name = ""

    # ---- 数据加载 ----
    def _load(self) -> dict[str, WeaponData]:
        path = config.resource_path(config.WEAPON_FILE)
        try:
            data = load_weapons(path)
            summary = "  ".join(f"{k}:{v.damage}伤/{v.fire_rate}s/{v.mag_size}发" for k, v in data.items())
            log.info(f"[M2] weapons loaded -> {summary}")
            return data
        except (OSError, ValueError, KeyError) as exc:
            log.warning(f"[M2] 武器表读取失败（{exc}），改用内置兜底数值")
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

    def switch(self, index: int) -> bool:
        """切枪。返回 False 表示没切（越界 / 已经是这把 / 还没解锁）。

        锁定武器不切，但要留下一条 3 秒的提示，否则玩家会以为按键坏了 ——
        霰弹枪是第 10 波才解锁的，前 9 波按 3 完全没反应是很难受的体验。
        """
        if not (0 <= index < len(self.slots)) or index == self.index:
            return False
        name = self.slots[index]
        if not self.available(name):
            self.locked_name = name
            self.locked_left = LOCKED_HINT_TIME
            return False
        self.index = index
        self._fired_this_press = False
        return True

    def refill_current(self) -> int:
        """弹药箱：把当前弹匣补满，返回补了几发。

        **必须同时清掉换弹计时**。否则会出现：弹匣空了正在换弹，
        捡到弹药箱后弹匣满了，但 1.8 秒的换弹动画还在跑，
        跑完又"补"一次，玩家看到弹药数没变却卡了 1.8 秒不能开枪。
        """
        weapon = self.current
        before = weapon.ammo
        weapon.reload_left = 0.0
        weapon.ammo = weapon.mag_size
        return weapon.ammo - before

    def reset(self) -> None:
        """重开一局。所有枪回满弹、状态清零、切回默认武器。"""
        for weapon in self.states.values():
            # 先还原基础弹匣再补满。重开之后波次归零，不还原的话
            # 第 20 波之后重开会带着 45 发步枪、20 发霰弹开局。
            weapon.set_mag_size(weapon.data.mag_size)
            weapon.ammo = weapon.mag_size
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
        self.locked_name = ""
        self.locked_left = 0.0
        self._fired_this_press = False
        self.rig.set_aiming(False)

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        # 枪口火焰的计时不受暂停影响：否则刚开完枪就按 ESC，火球会一直挂在那里。
        self.gun.update_flash(dt)
        self._tick_locked_hint(dt)

        if not self.state.in_state(State.PLAYING):
            self.trigger = False
            self.set_aiming(False)
            return

        # 以鼠标的实时状态为准，而不是只靠 down/up 事件。
        # 事件会在「按住时切窗口 / 松开时失去焦点」这类情况下丢一个，
        # 丢了 down 就永远进不了瞄准态（表现为右键没反应、散布不归零），
        # 丢了 up 则会一直连发。每帧对一次，两种都修掉。
        self._sync_input()

        self._sync_mag_sizes()
        self._update_weapons(dt)

        self.gun.follow(self.player, self.rig)

        if not self.trigger:
            return
        if not self.current.data.auto and self._fired_this_press:
            return          # 半自动：按住不放只打一发
        self._fire(self.current)

    def _update_weapons(self, dt: float) -> None:
        """所有枪一起走计时，**没拿在手上的按 0.5 倍速换弹**。

        只推进当前这把枪的话，换到别的枪就得从"上一局留下的空弹匣"重新开始，
        玩家实际感受到的是"换枪惩罚"—— 而这游戏只有三把枪、切枪只是战术选择，
        不该带惩罚。慢一半是为了保留"现在换回来会卡一下"的取舍，
        不是让后台换弹变成免费。
        """
        current = self.current
        for state in self.states.values():
            if state is current:
                state.update(dt)
                continue
            state.update(dt, reload_scale=config.BACKGROUND_RELOAD_SCALE)
            if state.needs_reload():
                # 后台自动补弹。`request_reload` 会发 `reload_started`，
                # 所以手里的和背后的换弹音效是同一套，不用另加分支。
                state.request_reload()

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
        # **一次扣弹只发一次**：霰弹枪 7 颗弹丸各发一次的话，
        # 噪音半径会因为重复触发而变得完全不可预期。
        #
        # `sfx` 让音频系统知道该放哪段枪声（霰弹枪有专属的一条）。
        # 名字在这里算而不是让音频系统去猜"当前拿的是什么枪" ——
        # 音频层不该认识武器表，否则每次加枪都要改两个地方。
        event_bus.emit("noise", position=self.player.world_position,
                       radius=config.NOISE_RADIUS,
                       sfx=shot_sfx_for(weapon.data.name))

        # 方向走 lens 反投影（屏幕正中心），不用 camera.forward：
        # 后者取节点局部 +Z，它是否等于投影轴取决于引擎内部约定，
        # 一旦不一致，弹道整体偏离准星、且不报任何错。
        origin, direction = screen_center_ray()
        data = weapon.data
        if data.pellets <= 1:
            self._fire_single(origin, direction, data)
        else:
            self._fire_pellets(origin, direction, data)

    def _fire_single(self, origin: Vec3, direction: Vec3, data: WeaponData) -> None:
        """单发武器：M2 那条路径，一行没改。瞄准时 spread 为 0，射线与准星共线。"""
        direction = apply_spread(direction, self.spread_deg, self._rng)
        hit = raycast(origin, direction, distance=data.range, ignore=self._ignore)
        end = hit.world_point if hit.hit else origin + direction * data.range

        self.last_hit_name = "none"
        self.last_damaged = False
        if hit.hit:
            self.last_hit_name = getattr(hit.entity, "name", None) or "?"
            target = enemy_from(hit.entity)
            if target is not None:
                self.hits += 1
                damage = data.damage + self.damage_bonus
                target.take_damage(damage)
                self.crosshair.on_hit()
                # 命中音效走事件：M5 的音频系统订阅它，武器层不需要认识音频。
                # 击杀会紧接着发 enemy_died，两者音色不同，叠在一起才有打击感。
                event_bus.emit("enemy_hit", damage=damage)
                self.last_damaged = True

        self._log_miss_if_aiming(hit, origin, direction)
        self.tracer.spawn(self.gun.muzzle_world, end)

    def _player_distance(self, point) -> float:
        """某个点到**玩家**的水平距离（米）。

        **不能用 `hit.distance`**：射线的起点是相机，而第三人称相机在玩家
        身后 6 米左右，同一次命中的 `hit.distance` 会比实际交战距离多出整个
        机位长度 —— 4 米的目标会算成 10 米，伤害直接掉进"远距离减半"那一档。
        水平距离（只用 x/z）而不是直线距离：地面格子是铺在 XZ 平面上的，
        "2 格"说的是站在几格外，跟高低差无关。
        """
        origin = self.player.world_position
        dx = point[0] - origin[0]
        dz = point[2] - origin[2]
        return math.hypot(dx, dz)

    def _combat_distance(self, hit) -> float:
        """这次命中的"交战距离"。

        打中敌人时用**敌人的中心**，不是命中点：命中点落在朝向玩家的那一面，
        比中心近半个身位，tank 那么宽的身体会让"4.2 米的目标"算成 3.5 米，
        于是该减半的伤害没有减半。打中墙/地就用命中点本身。
        """
        if not hit.hit:
            return float("inf")
        target = enemy_from(hit.entity)
        if target is not None:
            return self._player_distance(target.world_position)
        return self._player_distance(hit.world_point)

    def _fire_pellets(self, origin: Vec3, direction: Vec3, data: WeaponData) -> None:
        """霰弹：先打一条中心射线量出"目标有多远"，再按距离决定这一枪的散布。

        **为什么要先打中心射线**：分段规则是"2 格内无散布、4 格外散开"，
        也就是说散布取决于目标距离；而距离只有打出去之后才知道。
        用中心射线（准星正指着的东西）的距离当代理量，语义正好是
        "我瞄的这个东西有多远"。瞄着天空/远处地面时距离按射程算，
        此时散布最大 —— 而这个距离上本来也没有近距离目标可打。
        """
        # 无散布的中心射线：只用来测距，不结算伤害。
        probe = raycast(origin, direction, distance=data.range, ignore=self._ignore)
        target_distance = self._combat_distance(probe)
        if target_distance == float("inf"):
            target_distance = data.range
        cone = spread_at(data, target_distance, self.aiming)

        self.last_hit_name = "none"
        self.last_damaged = False
        # 同一只敌人被 N 颗弹丸打中时要合并成一次伤害结算。
        # 分开结算会发 N 次 `enemy_hit`、触发 N 次闪白重置、N 声命中音，
        # 7 颗全中时听起来像背景噪声，看不出"这一枪打实了"。
        damage_by_target: dict[int, list] = {}
        for index in range(data.pellets):
            # 第一颗沿准星走，其余按圆锥采样。这样"瞄准时至少有一颗必中"
            # 这条性质对霰弹枪也成立，近距离瞄头不会因为随机而全空。
            shot_dir = direction if index == 0 else apply_spread(direction, cone, self._rng)
            hit = raycast(origin, shot_dir, distance=data.range, ignore=self._ignore)
            end = hit.world_point if hit.hit else origin + shot_dir * data.range
            self.tracer.spawn(self.gun.muzzle_world, end)
            if not hit.hit:
                continue

            name = getattr(hit.entity, "name", None) or "?"
            if self.last_hit_name == "none":
                self.last_hit_name = name
            target = enemy_from(hit.entity)
            if target is None:
                continue
            slot = damage_by_target.setdefault(id(target), [target, 0.0])
            slot[1] += damage_at(data, self._combat_distance(hit), self.damage_bonus)

        total = 0.0
        for target, damage in damage_by_target.values():
            if damage <= 0.0:
                continue
            target.take_damage(damage)
            total += damage
        if damage_by_target:
            self.hits += 1
            self.crosshair.on_hit()
            event_bus.emit("enemy_hit", damage=total)
            self.last_damaged = True

        # 不调 `_log_miss_if_aiming`：霰弹枪瞄准时本来就有一个小圆锥，
        # 打远目标必然有弹丸飞偏，在这里打印只会刷屏。单发武器那条诊断路径不变。

    def _log_miss_if_aiming(self, hit, origin: Vec3, direction: Vec3) -> None:
        # 瞄准状态下没造成伤害才留一行现场：瞄准时散布为 0，
        # 「没打中靶子」就一定意味着弹道方向或遮挡有问题，必须能带出证据。
        # 腰射本来就允许偏，不打印，避免刷屏。
        if config.SHOT_MISS_LOG and self.aiming and not self.last_damaged:
            log.debug(
                f"[shot] 瞄准未命中  spread={self.spread_deg:.2f}deg  last={self.last_hit_name}  "
                f"dist={hit.distance:5.2f}  origin={tuple(round(v, 2) for v in origin)}  "
                f"dir={tuple(round(v, 3) for v in direction)}"
            )
