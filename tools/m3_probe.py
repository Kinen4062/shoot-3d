"""M3 无窗口端到端验收：AI / 感知 / 攻击 / 弹丸 / 波次全部跑真实系统。

用法：``.venv/Scripts/python.exe tools/m3_probe.py``

它不渲染，但会把真实的 Arena / Player / CameraRig / EnemySystem /
ProjectileSystem / WaveSystem 装配起来，按 1/60 秒固定步长推进，
回答五个问题：

1. 敌人会不会发现玩家、追过来、在攻击距离内开打（且伤害真的落到玩家身上）；
2. 远程兵会不会停在远处、蓄力、射出「会飞的」弹丸；
3. 玩家受伤有没有正确处理无敌帧、闪避有没有给无敌帧；
4. 波次会不会按规则刷怪（圆环半径、视野夹角、同屏上限、清场推进）；
5. 「玩家不还手会不会死」—— 这条是 M3 验收的核心，也最容易自我欺骗。

⚠️ 每次 build() 会先把上一组实体销毁。不销毁的话场景里会同时存在两个玩家，
敌人的视线射线会先打中那个看不见的旧玩家，表现为「远程兵永远不开火」。
"""
from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panda3d.core import LensNode, PerspectiveLens  # noqa: E402
from ursina import Ursina, Vec3, camera, destroy, mouse, scene  # noqa: E402

app = Ursina(window_type="none")

# ---- 无窗口模式下 ursina 不会执行相机初始化，手工补上 ----
camera.lens = PerspectiveLens()
camera.lens_node = LensNode("_probe_lens_node", camera.lens)
camera._cam = camera
camera.fov = 40
type(mouse).locked = property(lambda self: False, lambda self, value: None)

from src.core import config, event_bus  # noqa: E402
from src.core.clock import GameClock  # noqa: E402
from src.core.game_state import GameStateMachine, State  # noqa: E402
from src.entities.arena import Arena  # noqa: E402
from src.entities.player import Player  # noqa: E402
from src.systems.camera_rig import CameraRig  # noqa: E402
from src.systems.enemy_ai import BrainState  # noqa: E402
from src.systems.enemy_system import EnemySystem  # noqa: E402
from src.systems.perception import yaw_delta, yaw_to  # noqa: E402
from src.systems.player_control import PlayerController  # noqa: E402
from src.systems.projectile_system import ProjectileSystem  # noqa: E402
from src.systems.wave import WaveSystem  # noqa: E402

DT = 1.0 / 60.0
results: list[tuple[str, bool, str]] = []
_current = None


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def teardown(world) -> None:
    """销毁上一组实体。不做这一步，射线会打到上一局的幽灵实体。"""
    if world is None:
        return
    # M4 起订阅由 RunSession 集中注册，探针里没有 session，直接清空总线即可。
    event_bus.clear_listeners()
    for enemy in world["enemies"].enemies:
        destroy(enemy)
    for item in world["projectiles"]._active:
        destroy(item[0])
    destroy(world["player"])
    # 相机是 pivot 的子节点，直接销毁 pivot 会把相机本体一起销毁，
    # 后面的世界就没有相机了。先把它摘回场景根。
    camera.parent = scene
    destroy(world["rig"].pivot)
    destroy(world["arena"].ground)
    for wall in world["arena"].walls:
        destroy(wall)
    for pillar in world["arena"].pillars:
        destroy(pillar)


def build():
    global _current
    teardown(_current)

    clock = GameClock()
    state = GameStateMachine(clock)
    arena = Arena()
    player = Player()
    rig = CameraRig(player, state)
    enemies = EnemySystem(arena=arena, player=player)
    projectiles = ProjectileSystem(player, enemies)
    enemies.projectiles = projectiles
    controller = PlayerController(player, rig, arena, state)
    waves = WaveSystem(enemies, arena, rig, state)
    state.set(State.PLAYING)          # clock 不参与本探针，只用状态做门禁
    systems = [controller, rig, enemies, projectiles, waves]
    world = locals()
    _current = world
    return world


def step(world, frames: int, dt: float = DT) -> None:
    for _ in range(frames):
        for system in world["systems"]:
            system.update(dt)


def dist_to_player(world, index: int = 0) -> float:
    e = world["enemies"].enemies[index]
    p = world["player"]
    return math.hypot(e.x - p.x, e.z - p.z)


# ---------------------------------------------------------------- 1. 近战追击
def test_melee_chase() -> None:
    print("\n--- 1. 近战兵：视锥发现 → 追击 → 前摇 → 伤害 ---")
    world = build()
    enemies = world["enemies"]
    player = world["player"]
    enemies.spawn_type("grunt", 0.0, 14.0)
    enemy = enemies.enemies[0]
    player.health.hp = 1e6           # 本项只关心 AI 行为，不希望中途死亡打断

    step(world, 30)
    started = enemy.brain.state
    d0 = dist_to_player(world)
    hp0 = player.hp
    states = set()
    for _ in range(900):             # 15 秒
        step(world, 1)
        states.add(enemy.brain.state)
    d1 = dist_to_player(world)

    check("发现玩家后离开 IDLE", started is not BrainState.IDLE, f"state={started.value}")
    check("向玩家靠近", d1 < d0 - 3.0, f"{d0:.1f}m -> {d1:.1f}m")
    check("停在攻击距离内（不穿模）",
          d1 <= config.ATTACK_RANGE_TOLERANCE + 3.0, f"dist={d1:.2f}m")
    check("状态机经历过 ATTACK", BrainState.ATTACK in states,
          " / ".join(sorted(s.value for s in states)))
    check("近战伤害真的落到玩家身上", player.hp < hp0,
          f"hp {hp0:.0f} -> {player.hp:.0f}")


# ---------------------------------------------------------------- 2. 玩家会死
def test_player_can_die() -> None:
    print("\n--- 2. 验收核心：站着不还手会不会被打死 ---")
    world = build()
    player = world["player"]
    world["enemies"].spawn_type("grunt", 0.0, 12.0)
    died_at = None
    for i in range(60 * 40):         # 最多 40 秒
        step(world, 1)
        if not player.alive:
            died_at = i / 60.0
            break
    check("单一 grunt 能打死站着不动的玩家", died_at is not None,
          f"死亡用时 {died_at:.1f}s" if died_at else "40 秒内没死")


# ---------------------------------------------------------------- 3. 远程兵
def test_shooter() -> None:
    print("\n--- 3. 远程兵：保持距离 → 蓄力 → 射出弹丸 ---")
    world = build()
    enemies = world["enemies"]
    player = world["player"]
    enemies.spawn_type("shooter", 0.0, 18.0)
    projectiles = world["projectiles"]

    step(world, 60)
    d_start = dist_to_player(world)
    shots_before = projectiles.shots
    hp0 = player.hp
    step(world, 420)                 # 7 秒
    d_after = dist_to_player(world)

    check("远程兵会开火", projectiles.shots > shots_before, f"shots={projectiles.shots}")
    check("弹丸命中过玩家", projectiles.hits > 0, f"hits={projectiles.hits}")
    check("玩家被远程伤害到", player.hp < hp0,
          f"hp {hp0:.0f} -> {player.hp:.0f}")
    check("远程兵不会贴脸（维持 6m 以上）", d_after > 5.0,
          f"起手 {d_start:.1f}m -> {d_after:.1f}m")


# ---------------------------------------------------------------- 4. 无敌帧
def test_iframe() -> None:
    print("\n--- 4. 玩家受伤 / 无敌帧 / 闪避 ---")
    world = build()
    player = world["player"]
    hp0 = player.hp
    first = player.take_damage(10.0)
    second = player.take_damage(10.0)
    check("第一下扣血", first and player.hp == hp0 - 10, f"hp={player.hp:.0f}")
    check("无敌帧内第二下被吃掉", not second and player.hp == hp0 - 10, f"hp={player.hp:.0f}")

    world["controller"].update(0.6)  # 推进到无敌帧结束
    third = player.take_damage(10.0)
    check("无敌帧结束后可再次受伤", third and player.hp == hp0 - 20, f"hp={player.hp:.0f}")

    player.health.reset()
    first_dodge = world["controller"].request_dodge()
    check("闪避自带无敌帧", player.health.invulnerable, f"iframe={player.health.iframe:.2f}")
    second_dodge = world["controller"].request_dodge()
    check("闪避有冷却（不能连滚）", first_dodge and not second_dodge,
          f"cd={world['controller'].dodge_cd:.2f}")


# ---------------------------------------------------------------- 5. 波次
def test_waves() -> None:
    print("\n--- 5. 波次：圆环生成 / 视野死角 / 同屏上限 ---")
    world = build()
    enemies = world["enemies"]
    waves = world["waves"]
    rig = world["rig"]
    rig.yaw = 0.0

    spawn_points: list[tuple[float, float, float]] = []
    original = enemies.spawn_type

    def spy(type_name, x, z, hp_mult=1.0, speed_mult=1.0, yaw=None):
        spawn_points.append((x, z, rig.yaw))
        return original(type_name, x, z, hp_mult, speed_mult, yaw)

    enemies.spawn_type = spy  # type: ignore[assignment]
    step(world, 60 * 25)             # 25 秒：首波 2s + 逐个刷出
    enemies.spawn_type = original  # type: ignore[assignment]

    radii = [math.hypot(x, z) for x, z, _ in spawn_points]
    angles = [abs(yaw_delta(yaw, yaw_to(x, z))) for x, z, yaw in spawn_points]

    check("第 1 波已开始", waves.wave >= 1, f"wave={waves.wave} phase={waves.phase}")
    check("生成了 4 个敌人", len(spawn_points) == 4, f"count={len(spawn_points)}")
    check("生成点在圆环 [18,26] 上",
          all(config.SPAWN_RING[0] - 0.01 <= r <= config.SPAWN_RING[1] + 0.01 for r in radii),
          f"r={[round(r, 1) for r in radii]}")
    check("生成点都在视野夹角 75° 之外",
          all(a >= 75.0 for a in angles), f"angle={[round(a, 1) for a in angles]}")
    check("同屏数量不超过上限", enemies.alive_count <= config.MAX_ENEMIES,
          f"alive={enemies.alive_count}")

    # 清场后应进入 REST 并开下一波
    for e in enemies.enemies:
        e.hp = 1.0
        e.take_damage(999)
    step(world, 120)                 # 死亡动画
    step(world, 60 * 8)              # REST 4s + 下一波开始
    check("清场后推进到第 2 波", waves.wave >= 2, f"wave={waves.wave} phase={waves.phase}")


# ---------------------------------------------------------------- 6. 性能
def test_perf() -> None:
    print("\n--- 6. 同屏 30 敌人的单帧开销 ---")
    world = build()
    enemies = world["enemies"]
    player = world["player"]
    for i in range(config.MAX_ENEMIES):
        a = math.tau * i / config.MAX_ENEMIES
        enemies.spawn_type("grunt", math.sin(a) * 12.0, math.cos(a) * 12.0)

    # 压测期间不让玩家死：否则敌人会因为「玩家已死」而停摆，
    # 测出来的是空闲帧，不是最坏帧。
    player.health.hp = 1e9

    step(world, 10)
    frames = 300
    t0 = time.perf_counter()
    step(world, frames)
    elapsed = time.perf_counter() - t0
    per_frame = elapsed / frames * 1000.0
    # 60FPS 的预算是 16.7ms；这里只测 AI 部分，超过 6ms 就已经吃掉太多预算
    check("AI 单帧开销 < 6ms（不含渲染）", per_frame < 6.0,
          f"{per_frame:.2f} ms/frame, alive={enemies.alive_count}")


def main() -> int:
    test_melee_chase()
    test_player_can_die()
    test_shooter()
    test_iframe()
    test_waves()
    test_perf()
    teardown(_current)

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 60)
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for name, _ok, detail in failed:
        print(f"  FAIL: {name}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
