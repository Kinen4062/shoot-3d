"""霰弹枪端到端复现：按距离逐段开火，量真实结算出来的伤害。

用法：``.venv/Scripts/python.exe tools/_probe_shotgun.py``

不开窗口（``window_type='none'``），只跑几何与判定。相机初始化按
`aim_probe.py` 的做法手工补齐 —— 无窗口时 ursina 不会执行 `_set_up()`。

它回答四个问题：
1. 2 格（4 米）以内是不是 7 颗全中、伤害 140；
2. 2 格外伤害是不是立刻减半；
3. 4 格（8 米）以外是不是真的散掉、命中数明显下降；
4. 一次扣弹只消耗 1 发、只发一次噪音、命中音效只响一次。
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panda3d.core import LensNode, PerspectiveLens  # noqa: E402
from ursina import Ursina, Vec3, camera, mouse  # noqa: E402

app = Ursina(window_type="none")

camera.lens = PerspectiveLens()
camera.lens_node = LensNode("_perspective_lens_node", camera.lens)
camera._cam = camera
camera.fov = 40

type(mouse).locked = property(lambda self: False, lambda self, value: None)

from src.core import config, event_bus  # noqa: E402
from src.core.clock import GameClock  # noqa: E402
from src.core.game_state import GameStateMachine, State  # noqa: E402
from src.core.enemy_types import FALLBACK_ENEMY_TYPES  # noqa: E402
from src.entities.arena import Arena  # noqa: E402
from src.entities.gun import Gun  # noqa: E402
from src.entities.player import Player  # noqa: E402
from src.systems.camera_rig import CameraRig  # noqa: E402
from src.systems.enemy_system import EnemySystem  # noqa: E402
from src.systems.tracer import TracerSystem  # noqa: E402
from src.systems.weapon import WeaponSystem  # noqa: E402
from src.ui.crosshair import Crosshair  # noqa: E402

results: list[tuple[bool, str, str]] = []


def check(title: str, ok: bool, detail: str = "") -> None:
    results.append((bool(ok), title, detail))


def build():
    clock = GameClock()
    state = GameStateMachine(clock)
    arena = Arena()
    player = Player()
    rig = CameraRig(player, state)
    enemies = EnemySystem()
    gun = Gun()
    weapon = WeaponSystem(player, rig, state, enemies, TracerSystem(),
                          Crosshair(state), gun)
    weapon.waves = type("W", (), {"wave": 30})()
    state.set(State.PLAYING)
    return state, arena, player, rig, enemies, weapon


def aim_at(rig, target_pos, dt=1 / 60, iterations=16) -> None:
    for _ in range(iterations):
        rig.update(dt)
        origin = Vec3(camera.world_position)
        d = Vec3(target_pos) - origin
        horiz = math.hypot(d.x, d.z) or 1e-6
        rig.yaw = math.degrees(math.atan2(d.x, d.z))
        rig.pitch = -math.degrees(math.atan2(d.y, horiz))
    rig.update(dt)


def fire_once(weapon, rig, target) -> tuple[float, int, int]:
    """对 target 打一枪霰弹，返回 (实际伤害, 噪音事件数, 命中事件数)。"""
    noises: list[int] = []
    hits: list[float] = []
    event_bus.on("noise", lambda **_k: noises.append(1))
    event_bus.on("enemy_hit", lambda damage=0, **_k: hits.append(damage))

    weapon.index = weapon.slots.index("shotgun")
    weapon.current.ammo = weapon.current.data.mag_size
    weapon.current.cooldown = 0.0
    weapon.current.reload_left = 0.0
    weapon.aiming = False
    weapon.trigger = True
    weapon._fired_this_press = False

    aim_at(rig, Vec3(target.world_position))
    before = target.hp
    weapon._fire(weapon.current)
    event_bus.flush()
    event_bus.clear_listeners()
    return before - target.hp, len(noises), len(hits)


def place(enemies, player, distance: float):
    """在玩家正前方 distance 处放一只重甲兵（200 血，一枪打不死，好量伤害）。"""
    enemies.clear()
    event_bus.flush()
    enemies.spawn_type("tank", player.x, player.z + distance, yaw=180)
    tank = enemies.enemies[-1]
    # 血量加满到 100000，避免"打死了所以伤害被截断"这种假象。
    tank.hp = tank.max_hp = 100000.0
    return tank


def main() -> None:
    state, arena, player, rig, enemies, weapon = build()
    data = weapon.weapons["shotgun"]
    tight, wide = data.tight_range, data.wide_range
    print(f"[probe] shotgun = {data}")
    print(f"         1 格 = {config.GROUND_GRID_CELL} 米  tight={tight}  wide={wide}")

    print("\n--- 1. 2 格以内：7 颗全中 ---")
    tank = place(enemies, player, 2.0)
    damage, noises, hit_events = fire_once(weapon, rig, tank)
    check("2 米处打出 140（7×20）", abs(damage - 140) < 0.01, f"{damage:.1f}")
    check("一次扣弹只发一次噪音", noises == 1, f"{noises} 次")
    check("命中音效只响一次", hit_events == 1, f"{hit_events} 次")
    check("一次扣弹只消耗 1 发", weapon.current.ammo == data.mag_size - 1,
          f"{weapon.current.ammo}/{data.mag_size}")

    tank = place(enemies, player, 3.9)
    damage, _n, _h = fire_once(weapon, rig, tank)
    check("3.9 米（2 格内边界）仍是 140", abs(damage - 140) < 0.01, f"{damage:.1f}")

    print("\n--- 2. 2 格外：伤害立刻减半 ---")
    tank = place(enemies, player, 4.2)
    damage, _n, _h = fire_once(weapon, rig, tank)
    check("4.2 米处剩 70（7×10）", abs(damage - 70) < 0.01, f"{damage:.1f}")
    check("减半是阶跃不是渐变", damage <= 70.001, f"{damage:.1f}")

    print("\n--- 3. 4 格外：散掉，命中数明显下降 ---")
    tank = place(enemies, player, 12.0)
    damage, _n, _h = fire_once(weapon, rig, tank)
    check("12 米处伤害远低于 70", damage < 70.0 * 0.75, f"{damage:.1f}")
    check("12 米处仍会中一两颗（不是全空）", damage > 0.0, f"{damage:.1f}")
    landed = damage / (data.damage * data.far_damage)
    check("12 米处命中的弹丸数 < 4", landed < 4.0, f"{landed:.1f} 颗")

    print("\n--- 4. 瞄准只收窄、不归零 ---")
    tank = place(enemies, player, 12.0)
    weapon.aiming = True
    weapon.current.ammo = data.mag_size
    weapon.current.cooldown = 0.0
    aim_at(rig, Vec3(tank.world_position))
    before = tank.hp
    weapon._fire(weapon.current)
    aimed_damage = before - tank.hp
    check("瞄准后远距离伤害上升（散布收窄）", aimed_damage > damage,
          f"腰射 {damage:.1f} → 瞄准 {aimed_damage:.1f}")
    check("瞄准时散布仍是正数", weapon.spread_deg > 0.0, f"{weapon.spread_deg:.2f}deg")

    print("\n--- 5. 波次解锁 ---")
    for wave, want in ((0, False), (9, False), (10, True), (30, True)):
        # 每次都先回到默认武器：`switch()` 对"已经拿着这把"直接返回 False，
        # 不清回去的话第 10 波成功之后，第 30 波会因为"已经是霰弹枪"而误判。
        weapon.index = weapon.slots.index(config.DEFAULT_WEAPON)
        weapon.waves = type("W", (), {"wave": wave})()
        got = weapon.switch(weapon.slots.index("shotgun"))
        check(f"第 {wave} 波可以切霰弹枪 = {want}", got is want, f"实际 {got}")

    failed = [r for r in results if not r[0]]
    for ok, title, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {title}{('  ' + detail) if detail else ''}")
    print(f"\n共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项：" + "；".join(t for _ok, t, _d in failed))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
