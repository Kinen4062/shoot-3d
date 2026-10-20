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
from src.systems.perception import yaw_delta, yaw_to

# 生成点重采样上限。规划书写的是 10，别调大 ——
# 场地里挤满了人时，重采样只会白烧 CPU，还生成不出来。
SPAWN_TRIES = 10
MIN_ENEMY_GAP = 1.5
# 与相机 forward 的最小夹角（度）
MIN_SPAWN_ANGLE = 75.0
# 离墙至少留这么多，避免敌人一半卡在墙里
WALL_MARGIN = 1.5


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
        print(f"[M3] waves loaded -> {len(table.waves)} 波  rest={table.rest_time}s")
        return table
    except (OSError, ValueError, KeyError) as exc:
        print(f"[M3] 波次表读取失败（{exc}），改用内置兜底")
        return FALLBACK_WAVES


# ---------------------------------------------------------------- 纯逻辑

def hp_multiplier(wave: int) -> float:
    """封顶 4.0。不封顶的话第 30 波小兵血量 1800，TTK 直接失控。"""
    return min(1.0 + 0.10 * wave, 4.0)


def speed_multiplier(wave: int) -> float:
    return min(1.0 + 0.03 * wave, 1.5)


def compose_wave(wave: int, table: WaveTable) -> dict[str, int]:
    """第 wave 波（从 1 开始）的敌人配比。

    表内直接取；表外用公式继续加码，保证「打不完」——
    否则通关压力会消失，验收里的「玩家会死」也就不成立了。
    """
    if 1 <= wave <= len(table.waves):
        return dict(table.waves[wave - 1])

    count = min(3 + wave, config.MAX_ENEMIES)
    shooters = min(1 + wave // 3, 6)
    runners = min(1 + wave // 2, 8)
    grunts = max(1, count - shooters - runners)
    total = grunts + runners + shooters
    if total > config.MAX_ENEMIES:
        grunts = max(1, config.MAX_ENEMIES - shooters - runners)
    return {"grunt": grunts, "runner": runners, "shooter": shooters}


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

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        if not self.state.in_state(State.PLAYING):
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
        if self.enemies.alive_count == 0:
            # 注意用 alive_count 而不是列表长度：死亡动画期间实体还在列表里，
            # 但已经不可攻击，用长度判断会让下一波永远等不来。
            self.phase = "REST"
            self.timer = self.table.rest_time
            event_bus.emit("wave_cleared", wave=self.wave)

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
        print(f"[M3] wave {self.wave} started  {comp}  hp×{hp_multiplier(self.wave):.2f}")

    def _spawn_step(self, dt: float) -> None:
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
            [(p.x, p.z, max(p.scale_x, p.scale_z) * 0.5) for p in self.arena.pillars],
        )
        if point is None:
            self.skipped += 1
            self.queue.pop(0)
            self.spawn_timer = self.table.spawn_interval
            return

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
