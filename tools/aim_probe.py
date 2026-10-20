"""不开窗口的端到端瞄准复现：组装真实系统，对准屏幕中心开火，打印判定结果。

用法：``.venv/Scripts/python.exe tools/aim_probe.py``

它回答三个问题：
1. 屏幕中心射线与 ``camera.forward`` 的夹角是不是 0（两条独立算法是否一致）；
2. 准星压在靶子上时，raycast 到底打中了谁（靶子 / 柱子 / 地面 / 墙）；
3. 命中靶子时，伤害有没有真的结算（hits / dmg）。

窗口被换成 ``window_type='none'``，所以不渲染，只跑几何与判定。
相机在无窗口时不会执行 ursina 的 ``_set_up()``，这里手工补一个 lens。
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panda3d.core import LensNode, PerspectiveLens  # noqa: E402
from ursina import Ursina, Vec3, camera, mouse  # noqa: E402

app = Ursina(window_type="none")

# ---- 补上 ursina 在无窗口模式下跳过的相机初始化 ----
camera.lens = PerspectiveLens()
camera.lens_node = LensNode("_perspective_lens_node", camera.lens)
camera._cam = camera          # 相机实体自身当 cam 节点，相对变换为单位矩阵
camera.fov = 40

# 无窗口时 mouse.locked 的 setter 要碰 base.win，直接换成空实现。
type(mouse).locked = property(lambda self: False, lambda self, value: None)

from src.core import config  # noqa: E402
from src.core.clock import GameClock  # noqa: E402
from src.core.game_state import GameStateMachine, State  # noqa: E402
from src.core.screen_ray import screen_center_ray  # noqa: E402
from src.entities.arena import Arena  # noqa: E402
from src.entities.gun import Gun  # noqa: E402
from src.entities.player import Player  # noqa: E402
from src.systems.camera_rig import CameraRig  # noqa: E402
from src.systems.enemy_system import EnemySystem  # noqa: E402
from src.systems.player_control import PlayerController  # noqa: E402
from src.systems.tracer import TracerSystem  # noqa: E402
from src.systems.weapon import WeaponSystem  # noqa: E402
from src.ui.crosshair import Crosshair  # noqa: E402


def build():
    clock = GameClock()
    state = GameStateMachine(clock)
    arena = Arena()
    player = Player()
    rig = CameraRig(player, state)
    enemies = EnemySystem()
    enemies.spawn_targets()
    gun = Gun()
    weapon = WeaponSystem(player, rig, state, enemies, TracerSystem(), Crosshair(state), gun)
    controller = PlayerController(player, rig, arena, state, weapon=weapon)
    state.set(State.PLAYING)
    return state, arena, player, rig, enemies, weapon, controller


def aim_at(rig, target_pos, dt=1 / 60, iterations=12) -> None:
    """把 rig 的 yaw/pitch 调到「相机视线穿过 target_pos」。"""
    for _ in range(iterations):
        rig.update(dt)
        origin = Vec3(camera.world_position)
        d = Vec3(target_pos) - origin
        horiz = math.hypot(d.x, d.z) or 1e-6
        rig.yaw = math.degrees(math.atan2(d.x, d.z))
        rig.pitch = -math.degrees(math.atan2(d.y, horiz))
    rig.update(dt)


def angle_between(a: Vec3, b: Vec3) -> float:
    a, b = Vec3(a).normalized(), Vec3(b).normalized()
    return math.degrees(math.acos(max(-1.0, min(1.0, a.dot(b)))))


def main() -> None:
    state, arena, player, rig, enemies, weapon, controller = build()

    print(f"[probe] 靶子 {len(enemies.enemies)} 个  "
          f"柱子 {len(arena.pillars)} 根  武器 {weapon.slots}")
    for p in arena.pillars:
        print(f"        柱子 x={p.x:6.2f} z={p.z:6.2f} w={p.scale_x:4.2f} d={p.scale_z:4.2f}")

    for idx, target in enumerate(enemies.enemies):
        tpos = Vec3(target.world_position)
        aim_at(rig, tpos)

        origin, center_dir = screen_center_ray()
        fwd = Vec3(camera.forward).normalized()
        want = (tpos - origin).normalized()

        print(f"\n[target {idx}] pos={tuple(round(v, 2) for v in tpos)}")
        print(f"  cam={tuple(round(v, 2) for v in camera.world_position)}  "
              f"yaw={rig.yaw:7.2f} pitch={rig.pitch:6.2f}")
        print(f"  angle(屏幕中心射线, camera.forward) = {angle_between(center_dir, fwd):7.3f} deg")
        print(f"  angle(屏幕中心射线, 指向靶心)       = {angle_between(center_dir, want):7.3f} deg")

        # 直接开一枪（跳过输入层，直接走 weapon 的判定路径）
        weapon.aiming = True
        weapon.shots = 0
        weapon.hits = 0
        weapon.current.ammo = weapon.current.data.mag_size
        weapon.current.cooldown = 0.0
        weapon._fired_this_press = False
        weapon.trigger = True
        weapon._fire(weapon.current)
        hp = target.hp
        print(f"  shot -> last={weapon.last_hit_name:12s} dmg={weapon.last_damaged} "
              f"hits={weapon.hits} 靶子 hp={hp:.0f}/{target.max_hp:.0f} alive={target.alive}")

    # ---- 连发验收：按住左键+右键，跑 0.5 秒，看命中率与击杀 ----
    print("\n[bunt] 按住左键 + 右键 0.5s（rifle 射速 0.1s）")
    target = enemies.enemies[0]
    target.respawn()
    weapon.index = weapon.slots.index("rifle")
    weapon.current.ammo = weapon.current.data.mag_size
    weapon.current.cooldown = 0.0
    weapon.shots = weapon.hits = 0
    aim_at(rig, Vec3(target.world_position))

    mouse.right = True
    mouse.left = True
    for _ in range(30):
        rig.update(1 / 60)
        weapon.update(1 / 60)
    mouse.left = mouse.right = False

    print(f"  shots={weapon.shots} hits={weapon.hits} "
          f"靶子 hp={max(target.hp, 0):.0f} alive={target.alive} mode={'AIM' if weapon.aiming else 'HIP'}")


if __name__ == "__main__":
    main()
