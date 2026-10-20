"""波次生成器。

三条规则全部来自规划书 §5.4，且都是**可验证的硬约束**，所以抽成纯函数：

1. 生成点在半径 [18, 26] 的圆环上；
2. 生成方向与**相机正前方**的夹角 > 75°（不能凭空出现在枪口前）；
3. 与所有现存敌人的距离 ≥ 1.5 米，重采样最多 10 次，失败就跳过这次。

注意第 2 条用的是「相机 forward」而不是「玩家 forward」：
玩家在瞄准时身体朝向可能和镜头不一致，按身体朝向刷怪仍会出现在视野里。
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass

from src.core import config, event_bus
from src.core.game_state import State
from src.core.log import get_logger
from src.systems.perception import yaw_delta, yaw_to

log = get_logger("wave")

# 生成点重采样上限。规划书写的是 10，别调大 ——
# 场地里挤满了人时，重采样只会白烧 CPU，还生成不出来。
SPAWN_TRIES = 10
MIN_ENEMY_GAP = 1.5
# 与相机 forward 的最小夹角（度）
MIN_SPAWN_ANGLE = 75.0
# 离墙至少留这么多，避免敌人一半卡在墙里
WALL_MARGIN = 1.5

# ---- 30 波主线 + 31 层 BOSS ----
# 第 30 波清完 = 通关（不是继续无限刷）。之后可以主动进 31 层打 BOSS。
MAX_WAVE = 30
BOSS_WAVE = 31
# 重甲兵的出场节奏：每 6 波一只，第 24/30 波两只。
TANK_INTERVAL = 6
DOUBLE_TANK_WAVES = (24, 30)
# BOSS 层的固定阵容。BOSS 本身也算一只怪，由同一个敌人列表管理。
BOSS_LAYER = {"tank": 2, "shooter": 5, "liang": 1}
# 哪一型敌人是 BOSS。HUD 要靠它把"顶部血条挂在谁身上"这件事钉死，
# 不写成散落各处的字面量 —— 改名字时只该改这一行。
BOSS_KIND = "liang"
# BOSS 层的增援：每 10 秒 6 只。
# 加这个是因为 BOSS 只会上来近战：玩家在 3.2 米外绕圈，它就只是个血包。
# 有增援之后场上始终有杂兵，玩家没法把它当成唯一目标慢慢磨。
BOSS_REINFORCE_INTERVAL = 10.0
BOSS_REINFORCE_COUNT = 6
# 增援的类型权重。tank 只占 5%：BOSS 层本来就有 2 只重甲，
# 再频繁刷重甲会让"先清杂兵"从选择变成义务，节奏就散了。
BOSS_REINFORCE_TYPES = ("grunt", "runner", "shooter", "tank")
BOSS_REINFORCE_WEIGHTS = (0.50, 0.30, 0.15, 0.05)


@dataclass(frozen=True)
class WaveTable:
    rest_time: float
    spawn_interval: float
    waves: tuple[dict[str, int], ...]


FALLBACK_WAVES = WaveTable(
    rest_time=4.0,
    spawn_interval=0.55,
    waves=(
        {"grunt": 4},
        {"grunt": 5, "runner": 2},
        {"grunt": 5, "runner": 3},
        {"grunt": 6, "runner": 3, "shooter": 1},
        {"grunt": 6, "runner": 4, "shooter": 2},
        {"grunt": 7, "runner": 4, "tank": 1, "shooter": 1},
    ),
)


def parse_waves(raw: dict) -> WaveTable:
    if not isinstance(raw, dict) or "waves" not in raw:
        raise ValueError("波次表格式不对：缺少 waves")
    waves = []
    for i, item in enumerate(raw["waves"], start=1):
        if not isinstance(item, dict) or not item:
            raise ValueError(f"第 {i} 波不是有效的配比表")
        waves.append({str(k): int(v) for k, v in item.items()})
    if not waves:
        raise ValueError("波次表是空的")
    return WaveTable(
        rest_time=float(raw.get("rest_time", 4.0)),
        spawn_interval=float(raw.get("spawn_interval", 0.55)),
        waves=tuple(waves),
    )


def load_waves(path: str) -> WaveTable:
    with open(path, encoding="utf-8") as fp:
        return parse_waves(json.load(fp))


def load_or_fallback() -> WaveTable:
    path = config.resource_path(config.WAVE_FILE)
    try:
        table = load_waves(path)
        log.info(f"[M3] waves loaded -> {len(table.waves)} 波  rest={table.rest_time}s")
        return table
    except (OSError, ValueError, KeyError) as exc:
        log.warning(f"[M3] 波次表读取失败（{exc}），改用内置兜底")
        return FALLBACK_WAVES


# ---------------------------------------------------------------- 纯逻辑

def hp_multiplier(wave: int) -> float:
    """封顶 4.0。不封顶的话第 30 波小兵血量 1800，TTK 直接失控。"""
    return min(1.0 + 0.10 * wave, 4.0)


def speed_multiplier(wave: int) -> float:
    return min(1.0 + 0.03 * wave, 1.5)


def tank_count(wave: int) -> int:
    """第 wave 波该出几只重甲。

    第 6/12/18 波一只，第 24/30 波两只 —— 最后两波是"通关前的两道墙"。
    第 7~30 波此前走的是公式分支，里面**根本没有 tank**，所以玩家打得越久
    越看不到最有压迫感的兵种；这个函数就是补那个缺口。
    """
    if wave in DOUBLE_TANK_WAVES:
        return 2
    return 1 if wave % TANK_INTERVAL == 0 else 0


def compose_wave(wave: int, table: WaveTable) -> dict[str, int]:
    """第 wave 波（从 1 开始）的敌人配比。

    前 6 波是手写节奏（表内直接取）；第 7 波起用公式继续加码，
    但**每一波都要按 `tank_count` 补重甲** —— 只靠 grunt/runner/shooter
    的公式里没有重甲，30 波会变成一场没有起伏的消耗战。

    第 31 波是 BOSS 层，阵容写死在 `BOSS_LAYER`。
    """
    if wave == BOSS_WAVE:
        return dict(BOSS_LAYER)
    if 1 <= wave <= len(table.waves):
        return dict(table.waves[wave - 1])

    tanks = tank_count(wave)
    count = min(3 + wave, config.MAX_ENEMIES)
    shooters = min(1 + wave // 3, 6)
    runners = min(1 + wave // 2, 8)
    # 重甲占掉的名额要从总数里扣，否则第 30 波会变成 32 只挤 30 个位子，
    # 生成队列会被同屏上限卡住，最后几只只能等前面死掉才出来。
    grunts = max(1, count - shooters - runners - tanks)
    total = grunts + runners + shooters + tanks
    if total > config.MAX_ENEMIES:
        grunts = max(1, config.MAX_ENEMIES - shooters - runners - tanks)
    comp = {"grunt": grunts, "runner": runners, "shooter": shooters}
    if tanks:
        comp["tank"] = tanks
    return comp


def pick_spawn_point(
    rng: random.Random,
    player_x: float,
    player_z: float,
    camera_yaw: float,
    existing: list[tuple[float, float]],
    blockers: list[tuple[float, float, float]] | None = None,
    ring: tuple[float, float] = config.SPAWN_RING,
    inner: float = config.ARENA_INNER,
    tries: int = SPAWN_TRIES,
) -> tuple[float, float] | None:
    """在圆环上找一个「玩家看不见、不撞柱子、不撞人」的点。

    返回 None 表示这 10 次都没找到 —— 调用方应当**跳过**这次生成，
    而不是放宽条件硬塞（硬塞就会出现「怪刷在脸上」）。
    """
    r_lo, r_hi = ring
    limit = inner - WALL_MARGIN
    gap2 = MIN_ENEMY_GAP * MIN_ENEMY_GAP

    for _ in range(tries):
        angle = rng.uniform(-180.0, 180.0)
        radius = rng.uniform(r_lo, r_hi)
        x = player_x + math.sin(math.radians(angle)) * radius
        z = player_z + math.cos(math.radians(angle)) * radius

        if abs(x) > limit or abs(z) > limit:
            continue

        # 与相机 forward 的夹角。夹角太小 = 正好刷在准星前面。
        spawn_yaw = yaw_to(x - player_x, z - player_z)
        if abs(yaw_delta(camera_yaw, spawn_yaw)) < MIN_SPAWN_ANGLE:
            continue

        if any((x - ex) ** 2 + (z - ez) ** 2 < gap2 for ex, ez in existing):
            continue

        if blockers:
            blocked = False
            for bx, bz, br in blockers:
                need = br + 1.0
                if (x - bx) ** 2 + (z - bz) ** 2 < need * need:
                    blocked = True
                    break
            if blocked:
                continue

        return x, z
    return None


class WaveSystem:
    """REST（准备）→ SPAWN（逐个放出）→ FIGHT（等清场）→ REST ..."""

    def __init__(self, enemies, arena, rig, state, table: WaveTable | None = None,
                 rng: random.Random | None = None) -> None:
        self.enemies = enemies
        self.arena = arena
        self.rig = rig
        self.state = state
        self.table = table or load_or_fallback()
        self.rng = rng or random.Random(20261017)

        self.wave = 0
        self.phase = "REST"
        self.timer = 2.0          # 首波准备时间短一点，别让玩家空等
        self.spawn_timer = 0.0
        self.queue: list[str] = []
        self.skipped = 0          # 重采样失败被跳过的次数（调试用）
        # BOSS 层增援的计时与累计（覆盖层/HUD 会读，也方便测试断言）。
        self.reinforce_timer = 0.0
        self.reinforcements = 0
        # "BOSS 是否已经真正出场过"。不能用 `boss_alive` 代替：
        # 增援队列刚排好、BOSS 还没放出来时 `boss_alive` 也是 False，
        # 用它当开关会把整层的第一波（含 BOSS 本体）直接判成"已打完"。
        self.boss_spawned = False

    def reset(self) -> None:
        """重开一局（M4 会把它接进正式的 reset 契约）。

        只清本系统的状态；敌人实体由 EnemySystem.clear() 负责。
        """
        self.wave = 0
        self.phase = "REST"
        self.timer = 2.0
        self.spawn_timer = 0.0
        self.queue.clear()
        self.skipped = 0
        self.reinforce_timer = 0.0
        self.reinforcements = 0
        self.boss_spawned = False

    @property
    def cleared(self) -> bool:
        """30 波已经打完、正在等玩家决定要不要进 BOSS 层。"""
        return self.phase == "CLEARED" and self.wave == MAX_WAVE

    def start_boss_layer(self) -> bool:
        """从通关结算进入第 31 层。返回 False 表示现在不是那个时机。

        只允许在 `cleared` 状态调用一次：重复调用会把 BOSS 层重新刷一遍，
        而玩家此时正在结算面板上点按钮。
        """
        if not self.cleared:
            return False
        self.wave = BOSS_WAVE - 1      # `_begin_wave` 会 +1 变成 31
        self.phase = "REST"
        self.timer = 3.0               # 给玩家一点喘息和看 HUD 的时间
        self.spawn_timer = 0.0
        self.queue.clear()
        # 计时从"进入这一层"开始算，而不是从上一局的某个时刻接着算。
        self.reinforce_timer = 0.0
        self.reinforcements = 0
        self.boss_spawned = False
        return True

    def skip_to_next_wave(self) -> int:
        """开发期调试：立刻开始下一波，返回新的波次号。

        **只管波次自己的状态**（队列 / 阶段 / 计时），不碰场上的敌人 ——
        那属于 `EnemySystem`，由调用方 `DevTools.next_wave()` 先清掉。
        已经通关或已在 BOSS 层时原样返回：这两种情况没有"下一波"可言，
        硬跳会让 `compose_wave` 落进 32 波以上的公式分支，配比变得没意义。
        """
        if self.phase == "CLEARED" or self.wave >= BOSS_WAVE:
            return self.wave
        self.queue.clear()
        self.spawn_timer = 0.0
        self.reinforce_timer = 0.0
        self._begin_wave()
        return self.wave

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        if not self.state.in_state(State.PLAYING):
            return
        if self.phase == "CLEARED":
            # 已通关（或已打完 BOSS）：不再刷怪，等结算面板做决定。
            return
        if self.phase == "REST":
            self.timer -= dt
            if self.timer <= 0.0:
                self._begin_wave()
            return

        if self.phase == "SPAWN":
            self._spawn_step(dt)
            return

        # FIGHT
        if self.wave >= BOSS_WAVE:
            self._tick_reinforce(dt)
        if self.enemies.alive_count == 0:
            # 注意用 alive_count 而不是列表长度：死亡动画期间实体还在列表里，
            # 但已经不可攻击，用长度判断会让下一波永远等不来。
            event_bus.emit("wave_cleared", wave=self.wave)

            if self.wave >= BOSS_WAVE:
                # BOSS 层清完 = 真结局
                self.phase = "CLEARED"
                event_bus.emit("boss_cleared", wave=self.wave)
                return
            if self.wave >= MAX_WAVE:
                # 第 30 波清完 = 通关。之后可以主动进 31 层打 BOSS，
                # 但不是必须 —— 结算面板上给的是"可选"按钮。
                self.phase = "CLEARED"
                event_bus.emit("run_cleared", wave=self.wave)
                return

            self.phase = "REST"
            self.timer = self.table.rest_time

    @property
    def boss_alive(self) -> bool:
        """BOSS 本体还活着吗。BOSS 层之外恒为 False。"""
        for enemy in self.enemies.enemies:
            data = getattr(enemy, "data", None)
            if enemy.alive and data is not None and data.name == BOSS_KIND:
                return True
        return False

    def _tick_reinforce(self, dt: float) -> None:
        """BOSS 层：每 `BOSS_REINFORCE_INTERVAL` 秒补一批杂兵。

        **BOSS 一死就停**。不停的话 `alive_count` 永远回不到 0，
        `boss_cleared` 永远等不到 —— "打死了 BOSS 却不过关"比没有增援更糟。
        """
        if not self.boss_alive:
            return
        self.reinforce_timer += dt
        if self.reinforce_timer < BOSS_REINFORCE_INTERVAL:
            return
        self.reinforce_timer -= BOSS_REINFORCE_INTERVAL
        if self.enemies.alive_count >= config.MAX_ENEMIES:
            # 同屏已满：这一轮**直接放弃**，不排队等下一轮。
            # 排进去的话，玩家清完一波会突然冒出两批，节奏完全对不上。
            self.reinforce_timer = 0.0
            return
        picks = self.rng.choices(
            BOSS_REINFORCE_TYPES, weights=BOSS_REINFORCE_WEIGHTS,
            k=BOSS_REINFORCE_COUNT,
        )
        self.queue.extend(picks)
        self.phase = "SPAWN"
        self.spawn_timer = 0.0
        self.reinforcements += len(picks)
        counts: dict[str, int] = {}
        for name in picks:
            counts[name] = counts.get(name, 0) + 1
        log.info(f"[M3] BOSS 层增援 {len(picks)} 只  {counts}")

    # ---- 内部 ----
    def _begin_wave(self) -> None:
        self.wave += 1
        comp = compose_wave(self.wave, self.table)
        queue: list[str] = []
        for type_name, count in comp.items():
            queue.extend([type_name] * count)
        # 打乱放出顺序：否则同一波里「先 8 个 grunt 再 2 个 shooter」，
        # 玩家能背下节奏，波次就变成了固定脚本。
        self.rng.shuffle(queue)
        self.queue = queue
        self.phase = "SPAWN"
        self.spawn_timer = 0.0
        event_bus.emit("wave_started", wave=self.wave, total=len(queue))
        log.info(f"[M3] wave {self.wave} started  {comp}  hp×{hp_multiplier(self.wave):.2f}")

    def _spawn_step(self, dt: float) -> None:
        if self.wave >= BOSS_WAVE and self.boss_spawned and not self.boss_alive:
            # BOSS 已经倒下：还没放出来的增援全部作废。
            # 不做这一步的话，打死 BOSS 之后队列里的 6 只还会陆续冒出来，
            # 玩家看到的是"BOSS 倒了还在刷怪"，而且必须清完才算通关。
            if self.queue:
                log.info(f"[M3] BOSS 已倒下，取消 {len(self.queue)} 只未出场的增援")
                self.queue.clear()
            self.phase = "FIGHT"
            return

        self.spawn_timer -= dt
        if self.spawn_timer > 0.0:
            return
        if not self.queue:
            self.phase = "FIGHT"
            return
        if self.enemies.alive_count >= config.MAX_ENEMIES:
            # 同屏上限：不合格的留在队列里，等下一帧再看。
            return

        type_name = self.queue[0]
        point = pick_spawn_point(
            self.rng,
            self.enemies.player.x if self.enemies.player is not None else 0.0,
            self.enemies.player.z if self.enemies.player is not None else 0.0,
            self.rig.yaw,
            [(e.x, e.z) for e in self.enemies.enemies if e.alive],
            list(self.arena.pillar_boxes),
        )
        if point is None:
            self.skipped += 1
            self.queue.pop(0)
            self.spawn_timer = self.table.spawn_interval
            return

        if type_name == BOSS_KIND:
            self.boss_spawned = True
        self.enemies.spawn_type(
            type_name, point[0], point[1],
            hp_mult=hp_multiplier(self.wave),
            speed_mult=speed_multiplier(self.wave),
            yaw=yaw_to(
                (self.enemies.player.x if self.enemies.player is not None else 0.0) - point[0],
                (self.enemies.player.z if self.enemies.player is not None else 0.0) - point[1],
            ),
        )
        self.queue.pop(0)
        self.spawn_timer = self.table.spawn_interval

        if not self.queue:
            self.phase = "FIGHT"
