"""BOSS 层端到端复现：炮击是不是真的打出来了、增援是不是 10 秒一批、BOSS 死了停不停。

用法：``.venv/Scripts/python.exe tools/_probe_boss.py``

不开窗口（``window_type='none'``），走真实接线：`WaveSystem` → `EnemySystem`
→ `Brain` → `ProjectileSystem`。单元测试只能证到"计时器到点"，这里证的是
"弹丸真的进了池子、池子里的伤害值就是 BOSS 的"。

它回答四个问题：
1. 进第 31 层是不是 2 tank + 5 shooter + 1 liang；
2. liang 是不是 1 秒一发、弹丸伤害/颜色是不是它自己的；
3. 增援是不是每 10 秒 6 只，而且是普通兵种；
4. BOSS 死了以后是不是立刻停手（不然永远打不到结算）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panda3d.core import LensNode, PerspectiveLens  # noqa: E402
from ursina import Ursina, camera, mouse  # noqa: E402

app = Ursina(window_type="none")

camera.lens = PerspectiveLens()
camera.lens_node = LensNode("_perspective_lens_node", camera.lens)
camera._cam = camera
camera.fov = 40
type(mouse).locked = property(lambda self: False, lambda self, value: None)

from src.core import event_bus  # noqa: E402
from src.core.clock import GameClock  # noqa: E402
from src.core.game_state import GameStateMachine, State  # noqa: E402
from src.entities.arena import Arena  # noqa: E402
from src.entities.player import Player  # noqa: E402
from src.systems.camera_rig import CameraRig  # noqa: E402
from src.systems.enemy_system import EnemySystem  # noqa: E402
from src.systems.projectile_system import ProjectileSystem  # noqa: E402
from src.systems.wave import (  # noqa: E402
    BOSS_KIND,
    BOSS_REINFORCE_COUNT,
    BOSS_REINFORCE_INTERVAL,
    BOSS_WAVE,
    WaveSystem,
)

DT = 1 / 60
results: list[tuple[bool, str, str]] = []


def check(title: str, ok: bool, detail: str = "") -> None:
    results.append((bool(ok), title, detail))


def build():
    clock = GameClock()
    state = GameStateMachine(clock)
    arena = Arena()
    player = Player()
    # 血量拉满：这一层的杂兵会一直打玩家，测的是 BOSS 的节奏，不是玩家的耐久。
    # `Player.hp` 是只读转发，改血量要落到 `player.health` 上。
    player.health.max_hp = player.health.hp = 1e6
    rig = CameraRig(player, state)
    enemies = EnemySystem(arena=arena, player=player)
    projectiles = ProjectileSystem(player, enemies)
    enemies.projectiles = projectiles
    waves = WaveSystem(enemies, arena, rig, state)
    state.set(State.PLAYING)
    return state, arena, player, rig, enemies, projectiles, waves


def step(seconds: float, enemies, projectiles, waves) -> None:
    for _ in range(round(seconds / DT)):
        waves.update(DT)
        enemies.update(DT)
        projectiles.update(DT)
        event_bus.flush()


def ring(enemies, player, radius: float) -> None:
    """把所有敌人摆到玩家周围的圆环上，保证视线通畅、距离固定。

    同时把朝向直接掰到玩家身上：`turn_toward` 要一秒多才能转完 180°，
    等它转过来的话"前两秒没开炮"到底是 AI 坏了还是没看见，
    在这条探针里说不清 —— 干脆把朝向钉死。
    """
    import math

    alive = [e for e in enemies.enemies if e.alive]
    for i, enemy in enumerate(alive):
        angle = -math.pi / 2 + i * (2 * math.pi / max(1, len(alive)))
        enemy.x = player.x + math.cos(angle) * radius
        enemy.z = player.z + math.sin(angle) * radius
        enemy.rotation_y = math.degrees(math.atan2(player.x - enemy.x, player.z - enemy.z))


def main() -> None:
    state, arena, player, rig, enemies, projectiles, waves = build()

    print("--- 1. 进入第 31 层 ---")
    waves.wave = BOSS_WAVE - 1
    waves.phase = "CLEARED"
    check("通关状态才允许进 BOSS 层", waves.start_boss_layer() is True)
    step(3.2 + 4.0, enemies, projectiles, waves)

    names = [e.data.name for e in enemies.enemies if e.alive]
    check("BOSS 层是 31 波", waves.wave == BOSS_WAVE, f"wave={waves.wave}")
    check("阵容 2 tank + 5 shooter + 1 liang",
          (names.count("tank"), names.count("shooter"), names.count(BOSS_KIND)) == (2, 5, 1),
          f"{names}")
    liang = next(e for e in enemies.enemies if e.alive and e.data.name == BOSS_KIND)
    check("BOSS 血量 = 1200 × 4.0 倍率", liang.max_hp == 4800.0, f"{liang.max_hp:.0f}")

    print("\n--- 2. 炮击：1 秒一发 ---")
    ring(enemies, player, 10.0)

    # 拦一层 fire：直接数"BOSS 的弹丸"比数池子里的总数可靠 ——
    # 5 个 shooter 也在开火，混在一起就分不清是谁打的。
    fired: list[tuple[float, tuple | None]] = []
    original_fire = projectiles.fire

    def spy(origin, direction, speed, damage, tint=None):
        fired.append((damage, tint))
        return original_fire(origin, direction, speed, damage, tint)

    projectiles.fire = spy
    step(4.0, enemies, projectiles, waves)
    boss_shots = [f for f in fired if f[0] == liang.data.volley_damage]
    check("4 秒内开了 4 炮（1 发/秒）", 3 <= len(boss_shots) <= 5, f"{len(boss_shots)} 炮")
    check("BOSS 弹丸用的是自己的颜色",
          all(f[1] == liang.data.volley_color_rgb for f in boss_shots),
          f"{boss_shots[0][1] if boss_shots else '-'}")
    check("BOSS 炮击间隔 = 1.0 秒", liang.data.volley_cd == 1.0, f"{liang.data.volley_cd}")
    check("杂兵也在开火（不是只有 BOSS 在打）", len(fired) > len(boss_shots),
          f"总 {len(fired)} 发 / BOSS {len(boss_shots)} 发")
    projectiles.fire = original_fire

    print("\n--- 3. 增援：每 10 秒 6 只 ---")
    before = waves.reinforcements
    step(0.5, enemies, projectiles, waves)
    check("不到 10 秒不刷", waves.reinforcements == before, f"{waves.reinforcements}")
    step(BOSS_REINFORCE_INTERVAL, enemies, projectiles, waves)
    check(f"10 秒后刷了 {BOSS_REINFORCE_COUNT} 只",
          waves.reinforcements == before + BOSS_REINFORCE_COUNT,
          f"累计 {waves.reinforcements}")
    check("增援里没有第二只 BOSS",
          [e.data.name for e in enemies.enemies].count(BOSS_KIND) == 1,
          f"{[e.data.name for e in enemies.enemies].count(BOSS_KIND)} 只")

    print("\n--- 4. BOSS 死了就停 ---")
    liang.take_damage(999999)
    event_bus.flush()
    check("BOSS 已阵亡", not liang.alive)
    sealed = waves.reinforcements
    step(BOSS_REINFORCE_INTERVAL * 2.5, enemies, projectiles, waves)
    check("BOSS 死后不再补兵", waves.reinforcements == sealed,
          f"{sealed} -> {waves.reinforcements}")

    cleared: list[int] = []
    event_bus.on("boss_cleared", lambda **_k: cleared.append(1))
    for enemy in list(enemies.enemies):
        if enemy.alive:
            enemy.take_damage(999999)
    event_bus.flush()
    step(2.0, enemies, projectiles, waves)
    check("清场后进结算（真结局）", bool(cleared), f"{len(cleared)} 次 boss_cleared")
    check("BOSS 层之后不再刷怪", waves.phase == "CLEARED", f"{waves.phase}")

    failed = [r for r in results if not r[0]]
    for ok, title, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {title}{('  ' + detail) if detail else ''}")
    print(f"\n共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
