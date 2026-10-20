"""敌人类型数据（`data/enemies.json`）。

单独放一个模块，是为了打断循环依赖：
`entities/enemy.py`（实体）和 `systems/enemy_ai.py`（行为）都要用它，
如果它长在任何一个里面，另一个就必须反向 import。

**数值单位统一**：
- 距离 = 米，速度 = 米/秒，角度 = 度，时间 = 秒；
- `color` 在 JSON 里写 0~1，读进来立刻换成 0~255 ——
  渲染层只认 `color.rgb32()`，转换放在解析处，别让 0~1 流到下游。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from src.core import config

# 缺字段时不要静默给默认值：数值表写错一个键名，敌人会变成「打不死也打不到人」，
# 但游戏照常运行，极难排查。宁可启动时报错，也不要把错误藏起来。
_REQUIRED = (
    "hp", "speed", "damage", "attack_range", "attack_cd", "attack_windup",
    "view_range", "score", "radius", "color", "scale",
)


@dataclass(frozen=True)
class EnemyData:
    name: str
    hp: float
    speed: float
    damage: float
    attack_range: float
    attack_cd: float
    attack_windup: float
    view_range: float
    score: int
    radius: float
    color_rgb: tuple[int, int, int]
    scale: tuple[float, float, float]
    ranged: bool = False
    preferred_range: float = 12.0     # 远程敌人想维持的距离
    projectile_speed: float = 22.0


def _to_rgb(raw) -> tuple[int, int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        raise ValueError(f"color 必须是 [r,g,b] 三元组，收到 {raw!r}")
    # 两种写法都接受：0~1 浮点（规划书口径）或 0~255 整数。
    if max(raw[:3]) <= 1.0:
        return tuple(max(0, min(255, int(round(c * 255)))) for c in raw[:3])  # type: ignore[return-value]
    return tuple(max(0, min(255, int(c))) for c in raw[:3])  # type: ignore[return-value]


def _parse(name: str, raw: dict) -> EnemyData:
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise ValueError(f"敌人类型 {name!r} 缺少字段 {missing}")
    scale = raw["scale"]
    if not isinstance(scale, (list, tuple)) or len(scale) != 3:
        raise ValueError(f"敌人类型 {name!r} 的 scale 必须是三元组")
    return EnemyData(
        name=name,
        hp=float(raw["hp"]),
        speed=float(raw["speed"]),
        damage=float(raw["damage"]),
        attack_range=float(raw["attack_range"]),
        attack_cd=float(raw["attack_cd"]),
        attack_windup=float(raw["attack_windup"]),
        view_range=float(raw["view_range"]),
        score=int(raw["score"]),
        radius=float(raw["radius"]),
        color_rgb=_to_rgb(raw["color"]),
        scale=(float(scale[0]), float(scale[1]), float(scale[2])),
        ranged=bool(raw.get("ranged", False)),
        preferred_range=float(raw.get("preferred_range", 12.0)),
        projectile_speed=float(raw.get("projectile_speed", 22.0)),
    )


def parse_enemy_types(raw: dict) -> dict[str, EnemyData]:
    """校验并转换整张表。抽成纯函数，单测不碰磁盘。"""
    if not isinstance(raw, dict) or not raw:
        raise ValueError("敌人表为空或格式不对")
    return {name: _parse(name, item) for name, item in raw.items()}


def load_enemy_types(path: str) -> dict[str, EnemyData]:
    with open(path, encoding="utf-8") as fp:
        return parse_enemy_types(json.load(fp))


# 打包后 data/ 丢了也要能玩：数值与 enemies.json 保持一致。
FALLBACK_ENEMY_TYPES: dict[str, EnemyData] = parse_enemy_types({
    "grunt": {
        "hp": 60, "speed": 3.0, "damage": 10, "attack_range": 2.2, "attack_cd": 1.2,
        "attack_windup": 0.3, "view_range": 25, "score": 100, "radius": 0.5,
        "color": [0.86, 0.27, 0.24], "scale": [0.9, 1.8, 0.9],
    },
    "runner": {
        "hp": 35, "speed": 6.0, "damage": 8, "attack_range": 1.8, "attack_cd": 0.7,
        "attack_windup": 0.15, "view_range": 30, "score": 150, "radius": 0.4,
        "color": [1.0, 0.8, 0.2], "scale": [0.7, 1.5, 0.7],
    },
    "tank": {
        "hp": 200, "speed": 1.8, "damage": 25, "attack_range": 2.6, "attack_cd": 2.0,
        "attack_windup": 0.5, "view_range": 20, "score": 300, "radius": 0.8,
        "color": [0.6, 0.2, 0.8], "scale": [1.3, 2.4, 1.3],
    },
    "shooter": {
        "hp": 45, "speed": 2.6, "damage": 12, "attack_range": 22, "attack_cd": 2.4,
        "attack_windup": 0.8, "view_range": 30, "score": 200, "radius": 0.45,
        "color": [0.95, 0.85, 0.3], "scale": [0.8, 1.7, 0.8],
        "ranged": True, "preferred_range": 13, "projectile_speed": 22,
    },
})


def load_or_fallback() -> dict[str, EnemyData]:
    path = config.resource_path(config.ENEMY_FILE)
    try:
        data = load_enemy_types(path)
        print("[M3] enemies loaded -> " + "  ".join(
            f"{k}:{int(v.hp)}hp/{v.speed}m/s" for k, v in data.items()))
        return data
    except (OSError, ValueError, KeyError) as exc:
        print(f"[M3] 敌人表读取失败（{exc}），改用内置兜底数值")
        return dict(FALLBACK_ENEMY_TYPES)
