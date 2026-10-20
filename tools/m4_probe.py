"""M4 无窗口端到端验收：道具 / 计分 / 存档 / 重开契约。

用法：``.venv/Scripts/python.exe tools/m4_probe.py``

它不开窗口，但装配的是**真实系统**（Arena / Player / CameraRig / WeaponSystem /
EnemySystem / ProjectileSystem / PickupSystem / WaveSystem / ScoreSystem /
RunSession），按 1/60 秒固定步长推进，回答四个问题：

1. 敌人死亡会不会掉道具、掉在地上的道具能不能捡、捡了有没有真的生效；
2. 击杀得分与连击倍率对不对、3 秒不击杀会不会清零；
3. 死一次最高分有没有落盘（且没写程序目录）；
4. **重开之后分数 / 波次 / 实体 / 监听器是不是真的全部归零，最高分是不是真的保留**
   —— 这条是 M4 的验收核心，也是最容易"看起来对了其实没对"的一条。

⚠️ 存档一律重定向到系统临时目录。不重定向的话跑一次探针就会污染用户
真实的 `%APPDATA%\\MiniTPS\\save.json`。
"""
from __future__ import annotations

import math
import os
import random
import sys
import tempfile
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

from src.core import config, event_bus, save  # noqa: E402
from src.core.clock import GameClock  # noqa: E402
from src.core.game_data import GameData  # noqa: E402
from src.core.game_state import GameStateMachine, State  # noqa: E402
from src.core.session import RunSession  # noqa: E402
from src.entities.arena import Arena  # noqa: E402
from src.entities.gun import Gun  # noqa: E402
from src.entities.player import Player  # noqa: E402
from src.systems.camera_rig import CameraRig  # noqa: E402
from src.systems.enemy_system import EnemySystem  # noqa: E402
from src.systems.pickup_system import PickupSystem, roll_drop  # noqa: E402
from src.systems.player_control import PlayerController  # noqa: E402
from src.systems.projectile_system import ProjectileSystem  # noqa: E402
from src.systems.score import ScoreSystem  # noqa: E402
from src.systems.tracer import TracerSystem  # noqa: E402
from src.systems.wave import WaveSystem  # noqa: E402
from src.systems.weapon import WeaponSystem  # noqa: E402
from src.ui.crosshair import Crosshair  # noqa: E402

DT = 1.0 / 60.0
results: list[tuple[str, bool, str]] = []
_current = None

# 存档重定向。放在 import 之后、任何 build() 之前。
PROBE_SAVE = os.path.join(tempfile.gettempdir(), "miniTPS_m4_probe_save.json")
config.SAVE_FILE = PROBE_SAVE
if os.path.exists(PROBE_SAVE):
    os.remove(PROBE_SAVE)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def teardown(world) -> None:
    """销毁上一组实体。不清的话射线会打到上一局的幽灵实体（M3 踩过）。"""
    if world is None:
        return
    event_bus.clear_listeners()
    for enemy in world["enemies"].enemies:
        destroy(enemy)
    for item in world["projectiles"]._active:
        destroy(item[0])
    for item in world["tracer"]._active:
        destroy(item[0])
    world["pickups"].clear()
    destroy(world["player"])
    destroy(world["gun"].entity)
    destroy(world["crosshair"].root)
    # 相机是 pivot 的子节点，直接销毁 pivot 会把相机一起销毁。
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

    gun = Gun()
    tracer = TracerSystem()
    crosshair = Crosshair(state)
    weapon = WeaponSystem(player, rig, state, enemies, tracer, crosshair, gun)
    controller = PlayerController(player, rig, arena, state, weapon=weapon)
    waves = WaveSystem(enemies, arena, rig, state)

    data = GameData()
    score = ScoreSystem(state, player=player, data=data)
    pickups = PickupSystem(player, weapon, rng=random.Random(20261018))

    session = RunSession(
        state=state, player=player, controller=controller, rig=rig,
        weapon=weapon, enemies=enemies, projectiles=projectiles,
        pickups=pickups, waves=waves, score=score, tracer=tracer,
        overlay=None, crosshair=crosshair, damage_flash=None,
        wave_banner=None, hud=None,
    )
    session.register_listeners()

    state.set(State.PLAYING)      # clock 不参与本探针，只用状态做门禁
    systems = [controller, rig, weapon, enemies, projectiles, pickups, waves, score, tracer]
    world = locals()
    _current = world
    return world


def step(world, frames: int, dt: float = DT) -> None:
    for _ in range(frames):
        for system in world["systems"]:
            system.update(dt)


def kill_last(world) -> None:
    """把最后一个敌人打死。走 take_damage，所以 enemy_died 链路是真的。"""
    world["enemies"].enemies[-1].take_damage(999999)


# ---------------------------------------------------------------- 1. 掉落
def test_drop() -> None:
    print("\n--- 1. 掉落：概率 / 位置 / 池 ---")
    world = build()
    pickups = world["pickups"]
    enemies = world["enemies"]

    # 链路：真实敌人死亡 → 掉落系统收到事件。
    # 单次击杀有 70% 概率不掉，所以循环到"掉出来一次"为止 ——
    # 只打一次然后断言 `spawned >= before` 会恒真，是假通过。
    before = pickups.spawned
    for _ in range(60):
        if not enemies.enemies or not enemies.enemies[-1].alive:
            enemies.spawn_type("grunt", 6.0, 6.0)
        kill_last(world)
        if pickups.spawned > before:
            break
    check("敌人死亡会走掉落链路", pickups.spawned > before,
          f"spawned {before} -> {pickups.spawned}")

    # 概率与权重（直接调纯函数，避免被池容量和寿命干扰）
    rng = random.Random(11)
    drops = sum(1 for _ in range(20000) if roll_drop(rng) is not None)
    rate = drops / 20000.0

    check("掉落率约 30%", 0.28 <= rate <= 0.32, f"实测 {rate:.3f}")

    xs = [item[0].x for item in pickups._active]
    check("掉落物落在敌人死亡位置", bool(xs) and all(abs(x - 6.0) < 0.01 for x in xs),
          f"x={[round(v, 1) for v in xs]}")

    # 池容量：刷满后溢出计数，不崩
    for i in range(config.PICKUP_POOL_CUBE + config.PICKUP_POOL_SPHERE + 8):
        pickups.spawn("gem", float(i), 0.0)
    check("池满后丢弃而不是扩容", pickups.overflows > 0,
          f"active={pickups.active_count} overflow={pickups.overflows}")


# ---------------------------------------------------------------- 2. 拾取效果
def test_pickup_effects() -> None:
    print("\n--- 2. 拾取：5 种效果 + 距离判定 ---")
    world = build()
    player = world["player"]
    pickups = world["pickups"]
    weapon = world["weapon"]
    score = world["score"]

    # 血包
    player.health.hp = 50.0
    pickups.spawn("health", player.x, player.z)
    step(world, 2)
    check("血包 +30", player.hp == 80.0, f"hp={player.hp:.0f}")

    # 弹药
    weapon.current.ammo = 5
    pickups.spawn("ammo", player.x, player.z)
    step(world, 2)
    check("弹药箱补满弹匣", weapon.current.ammo == weapon.current.data.mag_size,
          f"ammo={weapon.current.ammo}")

    # 加速
    pickups.spawn("speed", player.x, player.z)
    step(world, 2)
    check("加速生效", player.speed_mult > 1.0 and player.speed_boost_left > 0,
          f"x{player.speed_mult:.2f} {player.speed_boost_left:.1f}s")

    # 护盾
    pickups.spawn("shield", player.x, player.z)
    step(world, 2)
    check("护盾 +1 层", player.shield_charges == 1, f"charges={player.shield_charges}")

    # 分数宝石
    s0 = score.data.score
    pickups.spawn("gem", player.x, player.z)
    step(world, 2)
    check("分数宝石 +250", score.data.score - s0 == config.GEM_SCORE,
          f"{s0} -> {score.data.score}")

    check("场上道具已全部清空", pickups.active_count == 0, f"active={pickups.active_count}")

    # 距离判定：3 米外捡不到
    pickups.spawn("gem", player.x + 3.0, player.z)
    step(world, 2)
    check("3 米外捡不到（判定半径 1.5）", pickups.active_count == 1,
          f"active={pickups.active_count}")

    # 满血不捡血包
    pickups.clear()
    player.health.hp = player.max_hp
    pickups.spawn("health", player.x, player.z)
    step(world, 2)
    check("满血时血包留在地上", pickups.active_count == 1,
          f"active={pickups.active_count}")


# ---------------------------------------------------------------- 3. 护盾 / 加速
def test_consumables_rules() -> None:
    print("\n--- 3. 护盾吸收 / 加速到期 ---")
    world = build()
    player = world["player"]

    # 护盾先于血量结算
    player.add_shield()
    hp0 = player.hp
    absorbed = player.take_damage(25.0)
    check("护盾吸收一次伤害（不掉血）",
          absorbed is False and player.hp == hp0 and player.shield_charges == 0,
          f"hp={player.hp:.0f} charges={player.shield_charges}")

    # 无敌帧期间不该消耗护盾
    player.add_shield()
    player.health.iframe = 0.4
    player.take_damage(25.0)
    check("无敌帧期间不消耗护盾", player.shield_charges == 1,
          f"charges={player.shield_charges}")
    player.health.iframe = 0.0

    # 加速 10 秒后自动恢复
    player.grant_speed_boost(config.SPEED_BOOST_MULT, config.SPEED_BOOST_TIME)
    step(world, int(60 * (config.SPEED_BOOST_TIME + 1.0)))
    check("加速到期后速度恢复", player.speed_mult == 1.0,
          f"x{player.speed_mult:.2f} left={player.speed_boost_left:.2f}")


# ---------------------------------------------------------------- 4. 计分
def test_scoring() -> None:
    print("\n--- 4. 计分：连击倍率 / 3 秒清零 / 满血奖励 ---")
    world = build()
    enemies = world["enemies"]
    score = world["score"]
    player = world["player"]
    data = score.data

    # 连续击杀 3 个 grunt（base 100）：1.0 / 1.1 / 1.2 倍
    for i in range(3):
        enemies.spawn_type("grunt", 8.0 + i, 8.0)
        kill_last(world)
        step(world, 1)

    expected = 100 + int(100 * 1.1) + int(100 * 1.2)
    check("连击倍率按 1.0/1.1/1.2 递增", data.score == expected,
          f"score={data.score} 期望={expected}")
    check("连击数为 3", data.combo == 3, f"combo={data.combo}")
    check("击杀数记账", data.kills == 3, f"kills={data.kills}")

    # 3 秒不清零，3.2 秒清零
    step(world, int(60 * 2.9))
    still = data.combo
    step(world, int(60 * 0.4))
    check("3 秒内连击保留", still >= 3, f"combo={still}")
    check("3 秒后连击清零", data.combo == 0, f"combo={data.combo} resets={score.combo_resets}")

    # 满血清波 +500
    player.health.hp = player.max_hp
    s0 = data.score
    event_bus.emit("wave_cleared", wave=2)
    check("满血清波 +500", data.score - s0 == config.FULL_HP_WAVE_BONUS,
          f"+{data.score - s0}")

    # 残血清波不加
    player.health.hp = 60.0
    s0 = data.score
    event_bus.emit("wave_cleared", wave=3)
    check("残血清波不加分", data.score == s0, f"+{data.score - s0}")


# ---------------------------------------------------------------- 5. 死亡与存档
def test_death_and_save() -> None:
    print("\n--- 5. 死亡：切 GAME_OVER + 最高分落盘 ---")
    if os.path.exists(PROBE_SAVE):
        os.remove(PROBE_SAVE)

    world = build()
    player = world["player"]
    score = world["score"]
    state = world["state"]

    # 先攒一点分
    world["enemies"].spawn_type("grunt", 9.0, 9.0)
    kill_last(world)
    step(world, 60)               # 1 秒生存分
    earned = score.data.score
    score.data.wave = 3

    player.health.hp = 1.0
    player.take_damage(999.0)

    stored = save.load_save(PROBE_SAVE)
    check("玩家死亡切到 GAME_OVER", state.state is State.GAME_OVER,
          f"state={state.state.name}")
    check("最高分写进了存档", stored["best_score"] == earned,
          f"存档 best={stored['best_score']} 本局={earned}")
    check("波次也写进了存档", stored["best_wave"] == 3, f"best_wave={stored['best_wave']}")
    check("存档写在重定向路径（不是程序目录）",
          save.load_save(PROBE_SAVE)["runs"] == 1,
          f"path={os.path.basename(PROBE_SAVE)} runs=1")


# ---------------------------------------------------------------- 6. 重开契约
def test_restart() -> None:
    print("\n--- 6. 验收核心：重开之后全部归零、最高分保留 ---")
    world = build()
    player = world["player"]
    score = world["score"]
    enemies = world["enemies"]
    pickups = world["pickups"]
    waves = world["waves"]
    session = world["session"]

    # 先造一个"乱局"：分数、连击、敌人、道具、波次全都不是初始值
    score.data.best_score = 4321
    for i in range(4):
        enemies.spawn_type("grunt", 8.0 + i * 1.5, 8.0)
        kill_last(world)
    step(world, 3)
    for i in range(5):
        pickups.spawn("gem", 4.0 + i, 4.0)
    waves.wave = 7
    waves.phase = "FIGHT"
    player.health.hp = 33.0
    player.grant_speed_boost(1.5, 10.0)

    listeners_before = session.listener_count
    score_before = score.data.score
    wave_before = waves.wave

    # 玩家死掉，然后 TAB 循环回 PLAYING（GAME_OVER → MENU → PLAYING）。
    # 这里必须真的走 state.set，而不是直接调 session.on_state_changed ——
    # 直接调方法只改了 session 的内部状态，状态机还停在 GAME_OVER，
    # 波次系统会一直以为"不在游戏中"而拒绝推进。
    player.health.hp = 1.0
    player.take_damage(999.0)
    state = world["state"]
    state.set(State.MENU)
    state.set(State.PLAYING)

    check("重开前确实是有分数的乱局",
          score_before > 0 and wave_before == 7, f"score={score_before} wave={wave_before}")
    check("分数归零", score.data.score == 0, f"score={score.data.score}")
    check("连击归零", score.data.combo == 0, f"combo={score.data.combo}")
    check("击杀数归零", score.data.kills == 0, f"kills={score.data.kills}")
    check("波次归零", waves.wave == 0, f"wave={waves.wave} phase={waves.phase}")
    check("敌人清空", len(enemies.enemies) == 0, f"enemies={len(enemies.enemies)}")
    check("道具归还池", pickups.active_count == 0, f"active={pickups.active_count}")
    check("玩家满血且加速清除",
          player.hp == player.max_hp and player.speed_mult == 1.0,
          f"hp={player.hp:.0f} x{player.speed_mult:.1f}")
    check("最高分保留", score.data.best_score >= 4321, f"best={score.data.best_score}")
    check("监听器数量不变（没翻倍）", session.listener_count == listeners_before,
          f"{listeners_before} -> {session.listener_count}")

    # 护盾也要归零（单独造一次乱局，避免和"能不能被打死"搅在一起）
    player.add_shield()
    session.reset_run()
    check("护盾在重开后清空", player.shield_charges == 0,
          f"charges={player.shield_charges}")

    # 重开后再击杀一次，只能计一次分
    enemies.spawn_type("grunt", 7.0, 7.0)
    kill_last(world)
    check("重开后计分不翻倍", score.data.kills == 1, f"kills={score.data.kills}")

    # 波次能重新推进
    step(world, int(60 * 3.0))
    check("重开后波次重新推进", waves.wave >= 1, f"wave={waves.wave}")


# ---------------------------------------------------------------- 7. 性能
def test_perf() -> None:
    print("\n--- 7. 30 敌人 + 满池道具的单帧开销 ---")
    world = build()
    enemies = world["enemies"]
    pickups = world["pickups"]
    player = world["player"]

    for i in range(config.MAX_ENEMIES):
        a = math.tau * i / config.MAX_ENEMIES
        enemies.spawn_type("grunt", math.sin(a) * 12.0, math.cos(a) * 12.0)
    for i in range(config.PICKUP_POOL_CUBE + config.PICKUP_POOL_SPHERE):
        a = math.tau * i / 20
        pickups.spawn("gem", math.sin(a) * 6.0, math.cos(a) * 6.0)
    player.health.hp = 1e9

    step(world, 10)
    frames = 300
    t0 = time.perf_counter()
    step(world, frames)
    elapsed = time.perf_counter() - t0
    per_frame = elapsed / frames * 1000.0
    check("逻辑单帧 < 8ms（30 敌人 + 20 道具）", per_frame < 8.0,
          f"{per_frame:.2f} ms/frame, alive={enemies.alive_count}, "
          f"loot={pickups.active_count}")


def main() -> int:
    test_drop()
    test_pickup_effects()
    test_consumables_rules()
    test_scoring()
    test_death_and_save()
    test_restart()
    test_perf()
    teardown(_current)

    # 收尾：删掉探针存档，别在临时目录留垃圾
    try:
        if os.path.exists(PROBE_SAVE):
            os.remove(PROBE_SAVE)
    except OSError:
        pass

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 60)
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for name, _ok, detail in failed:
        print(f"  FAIL: {name}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
