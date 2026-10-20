"""开发期调试热键（下一波 / 清场 / 无敌）+ 霰弹枪专属枪声的端到端复现。

用法：
    .venv/Scripts/python.exe tools/_probe_dev.py

探针**自己**把 `MINITPS_DEV=1` 设进环境再 import config —— 因为
`config.DEVELOPMENT_TOOLS` 是 import 时求值的，顺序错了会得到 False，
然后所有检查都会以"dev 是 None"的方式失败，看起来像功能没做。

它回答六个问题：
1. `main.build_world()` 在开关打开时是不是真的造了 DevTools，并接到按键/覆盖层上；
2. F1 是不是"先清场、再 +1 波"（而不是两波叠在一起）；
3. F2 是不是连"还没出场的队列"一起清（否则清完又冒出来）；
4. F3 的无敌吃不吃伤害、会不会偷偷扣护盾、再按一次能不能恢复；
5. 暂停时按 F1 会不会在结算面板背后刷怪；
6. 霰弹枪的枪声是不是真的换了一段（文件、时长、低频、运行时装载、事件里带的名字）。
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import wave
from itertools import pairwise

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在 import config 之前：DEVELOPMENT_TOOLS 在模块导入时求值。
os.environ["MINITPS_DEV"] = "1"

from panda3d.core import LensNode, PerspectiveLens  # noqa: F401,E402
from ursina import Ursina, mouse  # noqa: E402

app = Ursina(window_type="none")
type(mouse).locked = property(lambda self: False, lambda self, value: None)

from src.core import config, event_bus  # noqa: E402
from src.core.game_state import State  # noqa: E402

config.SAVE_FILE = os.path.join(tempfile.gettempdir(), "probe_dev_save.json")
if os.path.exists(config.SAVE_FILE):
    os.remove(config.SAVE_FILE)

import main  # noqa: E402

DT = 1.0 / 60.0
results: list[tuple[bool, str, str]] = []


def check(title: str, ok: bool, detail: str = "") -> None:
    results.append((bool(ok), title, detail))


def step(seconds: float) -> None:
    loop = world["loop"]
    for _ in range(max(1, int(round(seconds / DT)))):
        for system in loop.systems:
            system.update(DT)
        for system in loop.ui_systems:
            system.update(DT)
        event_bus.flush()


def press(key: str) -> None:
    world["keys"]._dispatch(key)


def audio_stats(name: str) -> tuple[float, float]:
    """返回 (时长秒, 过零率/秒)。

    过零率当"亮不亮"的客观代理：同一段合成器下，低频更重的样本过零更少。
    人耳听"更闷"和这个数字同方向，而且不需要声卡。
    """
    path = os.path.join(config.resource_path(config.AUDIO_DIR), f"{name}.wav")
    with wave.open(path) as fh:
        frames = fh.getnframes()
        rate = fh.getframerate()
        raw = fh.readframes(frames)
    samples = struct.unpack(f"<{frames}h", raw)
    crossings = sum(1 for a, b in pairwise(samples) if (a < 0) != (b < 0))
    return frames / rate, crossings / (frames / rate)


world = main.build_world()


def main_() -> None:
    dev = world["dev"]
    state = world["state"]
    waves = world["waves"]
    enemies = world["enemies"]
    player = world["player"]
    audio = world["audio"]

    print("--- 1. 开关接线 ---")
    check("MINITPS_DEV=1 时 config 认出来了", config.DEVELOPMENT_TOOLS is True)
    check("build_world 造了 DevTools", dev is not None)
    check("按键路由拿到了同一个对象", world["keys"].dev is dev)
    check("覆盖层拿到了同一个对象", world["overlay"].dev is dev)

    world["session"].start_run()
    step(0.1)
    check("已进入 PLAYING", state.in_state(State.PLAYING), state.state.name)

    print("\n--- 2. F1 下一波：先清场，再 +1 波 ---")
    press("f1")
    step(1.2)                      # 让第 1 波放出几只
    wave_before = waves.wave
    alive_before = enemies.alive_count
    check("开局按 F1 直接到第 1 波", wave_before == 1, f"wave={wave_before}")
    check("第 1 波确实在场上有敌人", alive_before > 0, f"alive={alive_before}")

    press("f1")
    event_bus.flush()
    check("波次只 +1", waves.wave == wave_before + 1, f"{wave_before} -> {waves.wave}")
    check("上一波的敌人已清空（不会两波叠在一起）",
          enemies.alive_count == 0, f"alive={enemies.alive_count}")

    print("\n--- 3. F2 清场：连未出场的队列一起清 ---")
    step(1.5)
    spawned = enemies.alive_count
    queued = len(waves.queue)
    check("清场前场上有人", spawned > 0, f"alive={spawned}  queue={queued}")
    press("f2")
    event_bus.flush()
    check("清场后场上 0 只", enemies.alive_count == 0, f"alive={enemies.alive_count}")
    check("未出场的队列也清空了", waves.queue == [], f"queue={waves.queue}")
    step(1.5)
    check("清场后不会又冒出来", enemies.alive_count == 0, f"alive={enemies.alive_count}")

    print("\n--- 4. F3 无敌 ---")
    hp0 = player.hp
    press("f3")
    check("第一次按开启", dev.god is True)
    swallowed = player.take_damage(999)
    check("无敌时伤害被吃掉", swallowed is False, f"take_damage -> {swallowed}")
    check("血量一点没掉", player.hp == hp0, f"{hp0:.0f} -> {player.hp:.0f}")
    check("无敌时护盾也不掉", player.shield_charges == 0, f"{player.shield_charges}")

    press("f3")
    check("第二次按关闭", dev.god is False)
    landed = player.take_damage(10)
    check("关闭后伤害正常落地", landed is True and player.hp < hp0,
          f"hp={player.hp:.0f}")

    print("\n--- 5. 暂停时按键不生效 ---")
    wave_before = waves.wave
    state.set(State.PAUSED)
    press("f1")
    check("暂停中 F1 不推进波次", waves.wave == wave_before, f"wave={waves.wave}")
    state.set(State.PLAYING)

    print("\n--- 6. 霰弹枪专属枪声 ---")
    shoot_dur, shoot_zcr = audio_stats("shoot")
    shotgun_dur, shotgun_zcr = audio_stats("shotgun")
    check("shotgun.wav 已生成", shotgun_dur > 0.0, f"{shotgun_dur:.3f}s")
    check("比步枪枪声长（尾巴拉长了）", shotgun_dur > shoot_dur * 1.5,
          f"{shoot_dur:.3f}s -> {shotgun_dur:.3f}s")
    check("比步枪枪声更闷（过零率更低）", shotgun_zcr < shoot_zcr,
          f"{shoot_zcr:.0f}/s -> {shotgun_zcr:.0f}/s")

    check("运行时能播出来（不属于缺文件）", audio.play("shotgun") is True)
    check("没有被记进 missing", "shotgun" not in audio.missing,
          f"missing={sorted(audio.missing)}")

    weapon = world["weapon"]
    heard: list[str | None] = []
    event_bus.on("noise", lambda sfx=None, **_k: heard.append(sfx))
    waves.wave = 10                 # 解锁霰弹枪
    for name in ("rifle", "shotgun"):
        weapon.index = weapon.slots.index(name)
        weapon.current.ammo = 10
        weapon.current.cooldown = 0.0
        weapon.current.reload_left = 0.0
        weapon._fire(weapon.current)
    event_bus.flush()
    event_bus.clear_listeners()
    check("步枪开火发的是 shoot", heard[:1] == ["shoot"], f"{heard}")
    check("霰弹枪开火发的是 shotgun", heard[1:2] == ["shotgun"], f"{heard}")


main_()

failed = [r for r in results if not r[0]]
for ok, title, detail in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {title}{('  ' + detail) if detail else ''}")
print(f"\n共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项")
if failed:
    sys.exit(1)
