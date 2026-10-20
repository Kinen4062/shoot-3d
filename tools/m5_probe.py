"""M5 端到端验收：菜单 → 对局 → 暂停 → 死亡 → 结算 → 重开 → 返回菜单。

和 `m4_probe.py` 不同的地方：这个探针**复用 `main.build_world()`**，
装配的是生产代码里那一套接线（包括按钮回调和 session）。
如果探针自己搭一遍，验证的就不是玩家实际跑的那条路径了。

无窗口运行：
    python tools/m5_probe.py

它会临时把 `config.SAVE_FILE` 指到系统临时目录，**不会动你的真实存档**。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panda3d.core import LensNode, PerspectiveLens  # noqa: F401,E402
from ursina import Ursina, camera, mouse, window  # noqa: E402

app = Ursina(window_type="none")
# 无窗口时 mouse.locked 的 setter 会碰 base.win，把它退化掉。
type(mouse).locked = property(lambda self: False, lambda self, value: None)

from src.core import config, event_bus  # noqa: E402
from src.core.game_state import State  # noqa: E402
from src.ui import widgets  # noqa: E402

config.SAVE_FILE = os.path.join(tempfile.gettempdir(), "m5_probe_save.json")
# 上一轮探针留下的存档会让"是否新纪录"的判断失真（best 已经被写成同一个分数）。
if os.path.exists(config.SAVE_FILE):
    os.remove(config.SAVE_FILE)

import main  # noqa: E402

world = main.build_world()

DT = 1.0 / 60.0
results: list[tuple[bool, str, str]] = []


def check(title: str, ok: bool, detail: str = "") -> None:
    results.append((bool(ok), title, detail))


def step(seconds: float) -> None:
    """按固定 dt 推进整条流水线，和 GameLoop.update 的顺序保持一致。"""
    loop = world["loop"]
    frames = max(1, int(round(seconds / DT)))
    for _ in range(frames):
        for system in loop.systems:
            system.update(DT)
        for system in loop.ui_systems:
            system.update(DT)
        event_bus.flush()


def visible(panel) -> bool:
    # 必须看 visible：ursina 8 的 enabled=False 只是标志位，节点照样渲染。
    return bool(panel.root.visible)


state = world["state"]
session = world["session"]
data = world["data"]
player = world["player"]
waves = world["waves"]
hud = world["combat_hud"]
crosshair = world["crosshair"]

print("--- 1. 开局应当停在主菜单 ---")
check("初始状态是 MENU", state.state is State.MENU, state.state.name)
check("主菜单可见", visible(world["main_menu"]))
check("暂停/结算面板不可见",
      not visible(world["pause_menu"]) and not visible(world["game_over"]))
check("HUD 不可见（菜单里不该有血条弹药）", not hud.root.visible)
check("准星不可见", not crosshair.root.visible)
check("菜单按钮可点", all(b.collider is not None for b in world["main_menu"]._buttons))
check("后台面板的按钮点不到",
      all(b.collider is None for b in world["pause_menu"]._buttons)
      and all(b.collider is None for b in world["game_over"]._buttons))
# 实机截图里这堆等宽文字直接压在卡片上，连 [TAB] cycle 那行都和 START 叠在一起。
overlay = world["overlay"]
check("调试覆盖层在菜单里隐藏", not overlay.label.visible,
      "否则十几行文字会压在菜单卡片上")
check("帧率读数在菜单里隐藏", not world["fps_counter"].text.visible)
# 按钮底色和卡片同色时，实机上只剩两行白字悬空，看不出哪里能点。
check("按钮底色明显区别于卡片",
      sum(widgets.RGB_BTN) - sum(widgets.RGB_CARD) >= 30
      and sum(widgets.RGB_BTN_HOVER) > sum(widgets.RGB_BTN),
      f"btn={sum(widgets.RGB_BTN)} card={sum(widgets.RGB_CARD)}")

print("--- 2. 菜单背景镜头在缓慢环绕 ---")
yaw0 = world["rig"].yaw
# 瞄准中死亡时 fov 停在 AIM_FOV，不进对局就没人恢复它 —— 先打坏它再验。
camera.fov = config.AIM_FOV
step(0.5)
moved = abs(world["rig"].yaw - yaw0) > 0.5
check("MENU 下镜头环绕", moved, f"yaw {yaw0:.2f} -> {world['rig'].yaw:.2f}")
# 6 米机位下玩家那个 0.7×1.8 的方块占掉画面左侧三分之一，菜单像贴了张蓝色卡片。
check("菜单取景比游戏中更远更平",
      abs(camera.position.z + config.MENU_CAM_DIST) < 1e-6
      and abs(world["rig"].pivot.rotation_x - config.MENU_CAM_PITCH) < 1e-6,
      f"dist={-camera.position.z:.1f} pitch={world['rig'].pivot.rotation_x:.1f}")
check("菜单 fov 已从瞄准值恢复", abs(camera.fov - config.HIP_FOV) < 1e-6,
      f"fov={camera.fov:.0f}")

print("--- 3. 点 START 进入对局 ---")
session.start_run()            # 等价于主菜单 START 按钮
step(DT)
check("状态切到 PLAYING", state.state is State.PLAYING, state.state.name)
check("主菜单已隐藏", not visible(world["main_menu"]))
check("HUD 已显示", hud.root.visible)
check("准星已显示", crosshair.root.visible)
check("对局中调试覆盖层恢复显示", overlay.label.visible)
fps_text = world["fps_counter"].text
check("对局中帧率读数出现", fps_text.visible and fps_text.text.startswith("FPS "),
      fps_text.text)
check("对局中菜单按钮点不到",
      all(b.collider is None for b in world["main_menu"]._buttons),
      "否则开枪的点击会误触菜单")
check("重启计数 +1", session.restarts == 1, str(session.restarts))

# 首波有 2 秒准备时间（别让玩家一开局就被贴脸），再加上逐只刷新的间隔。
step(2.4)
check("波次系统开始工作", waves.wave >= 1, f"wave={waves.wave} phase={waves.phase}")
check("敌人已刷出", len(world["enemies"].enemies) > 0, f"{len(world['enemies'].enemies)} 只")

print("--- 3b. 击杀飘字 ---")
popup = world["score_popup"]
# fov 是 _update_aim 插值出来的中间量，探针里第 2 帧可能还没到 40。
camera.fov = config.HIP_FOV
victim = world["enemies"].enemies[0]
# 挪到玩家正前方 8 米：原地刷新的敌人可能在相机背后，那样投影会（正确地）拒绝飘字。
victim.position = (player.x, player.y, player.z + 8)
victim.take_damage(9999.0)
step(DT)
live = [s for s in popup._slots if s["live"]]
check("击杀后飘字出现", popup.shown >= 1 and bool(live), f"shown={popup.shown}")
check("飘字内容是 +分数", bool(live) and live[0]["label"].text.startswith("+"),
      live[0]["label"].text if live else "-")
check("飘字落在屏幕内",
      bool(live)
      and abs(live[0]["base"][0]) <= window.aspect_ratio / 2
      and abs(live[0]["base"][1]) <= 0.5,
      f"{live[0]['base'] if live else '-'}")
check("飘字确实在向上飘", bool(live) and live[0]["label"].y > live[0]["base"][1] - 1e-6,
      f"y={live[0]['label'].y:.3f} base={live[0]['base'][1]:.3f}" if live else "-")
step(config.POPUP_LIFE + 0.1)
check("飘字到时自动回收", sum(1 for s in popup._slots if s["live"]) == 0,
      f"还活着 {sum(1 for s in popup._slots if s['live'])} 个")

print("--- 4. ESC 暂停 ---")
state.toggle_pause()
step(DT)
check("暂停面板出现", visible(world["pause_menu"]))
check("HUD 在暂停时仍然可见（背后能看到自己的血/弹）", hud.root.visible)
check("时钟已暂停", world["clock"].paused)

world["pause_menu"]._cycle_volume()
volume = world["audio"].volume_percent
check("音量按钮能切档", volume in [int(round(s * 100)) for s in config.VOLUME_STEPS],
      f"{volume}%")

state.toggle_pause()
step(DT)
check("恢复后回到 PLAYING", state.state is State.PLAYING)
check("暂停面板已隐藏", not visible(world["pause_menu"]))

print("--- 5. 死亡 → 结算 ---")
data.score = 1234
data.kills = 9
data.time_alive = 95.0
player.shield_charges = 0
player.health.iframe = 0.0
player.take_damage(9999.0)
step(DT)

check("状态切到 GAME_OVER", state.state is State.GAME_OVER, state.state.name)
check("结算面板出现", visible(world["game_over"]))
check("结算时覆盖层隐藏（这一屏是给玩家截图看的）", not overlay.label.visible)
panel = world["game_over"]
check("结算 SCORE 正确", panel._rows["SCORE"].text == "1234", panel._rows["SCORE"].text)
check("结算 KILLS 正确", panel._rows["KILLS"].text == "9", panel._rows["KILLS"].text)
check("结算 TIME 正确", panel._rows["TIME"].text == "1:35", panel._rows["TIME"].text)
check("结算 WAVE 有数字", panel.wave_text.text.startswith("WAVE "), panel.wave_text.text)
check("创造了新纪录", panel.record_text.text == "NEW RECORD", panel.record_text.text)

print("--- 6. RESTART ---")
session.start_run()            # 等价于结算面板 RESTART
step(DT)
check("回到 PLAYING", state.state is State.PLAYING)
check("结算面板已隐藏", not visible(world["game_over"]))
check("只重开一次（restarts=2）", session.restarts == 2, str(session.restarts))
check("分数归零", data.score == 0, str(data.score))
check("波次归零", waves.wave == 0, f"wave={waves.wave}")
check("敌人清空", len(world["enemies"].enemies) == 0, str(len(world["enemies"].enemies)))
check("玩家满血", player.hp == player.max_hp, f"{player.hp:.0f}")
check("最高分保留", data.best_score >= 1234, str(data.best_score))

print("--- 7. 暂停 → 返回主菜单 ---")
state.toggle_pause()
step(DT)
session.return_to_menu()        # 等价于 MAIN MENU 按钮
step(DT)
check("回到 MENU", state.state is State.MENU)
check("主菜单重新出现", visible(world["main_menu"]))
check("HUD 已隐藏", not hud.root.visible)
# 32 = 28（M5）+ 4（通关：run_cleared / boss_cleared 各接两个 handler）。
# 数字变了就要跟着改 —— 这条的重点是"重开前后一致"，硬编码只是顺手把
# "订阅总数意外增多"也一起盯着。
check("监听器没有翻倍", session.listener_count == 32,
      f"listeners={session.listener_count}")

print("--- 7b. 三十波通关 → 结算面板 ---")
# 手打 30 波要二十多分钟，探针里直接把波次系统摆到"第 30 波、场上没人"的状态，
# 走的是**生产代码里那条真实的 FIGHT 结算分支**，不是伪造事件。
session.start_run()
step(DT)
waves.wave = 30
waves.phase = "FIGHT"
waves.queue.clear()
step(DT)
clear_panel = world["clear_panel"]
check("状态切到 CLEARED", state.state is State.CLEARED, state.state.name)
check("通关面板可见", visible(clear_panel))
check("结算面板没弹（通关不是死亡）", not visible(world["game_over"]))
check("面板标题是 RUN CLEARED", clear_panel.title_text.text == "RUN CLEARED",
      clear_panel.title_text.text)
check("「进入 BOSS 层」按钮在", clear_panel.boss_button.visible)
check("按钮真的点得到", clear_panel.boss_button.collider is not None,
      "隐藏面板的按钮必须没有 collider")
check("世界已冻结", world["clock"].paused is True)
check("分数已落盘", data.best_score > 0, str(data.best_score))

print("--- 7c. 进入 BOSS 层（第 31 波）---")
check("进 BOSS 层成功", session.enter_boss_layer() is True)
step(DT)
check("回到 PLAYING", state.state is State.PLAYING, state.state.name)
check("通关面板已隐藏", not visible(clear_panel))
check("世界已解冻", world["clock"].paused is False, "否则点完按钮什么都不动")
check("分数保留（不是新开一局）", data.score > 0, str(data.score))
check("重开次数没变（还是 3）", session.restarts == 3, str(session.restarts))

# 3 秒准备 + 逐只刷新，最多等 10 秒把 8 只放完。
boss = None
for _ in range(200):
    step(0.05)
    boss = next((e for e in world["enemies"].enemies if e.name == "liang"), None)
    if boss is not None and waves.phase == "FIGHT":
        break
check("BOSS 层阵容 = 2 tank + 5 远程 + 1 BOSS",
      sorted(e.name for e in world["enemies"].enemies if e.alive)
      == sorted(["liang"] + ["tank"] * 2 + ["shooter"] * 5),
      str(sorted(e.name for e in world["enemies"].enemies if e.alive)))
check("BOSS 已刷出", boss is not None)
check("BOSS 身高 3.4 米", boss is not None and abs(boss.scale_y - 3.4) < 1e-6,
      f"{boss.scale_y if boss else '-'}")
step(0.2)
check("BOSS 血条出现", hud.boss_bg.visible and hud.boss_fill.visible)
check("BOSS 血条文字", hud.boss_text.text.startswith("LIANG"),
      hud.boss_text.text)
check("血条满格", abs(hud.boss_fill.scale_x - hud._boss_w) < 1e-6,
      f"{hud.boss_fill.scale_x:.3f}/{hud._boss_w:.3f}")
boss.take_damage(boss.max_hp * 0.5)
step(0.2)
check("掉一半血后血条减半",
      abs(hud.boss_fill.scale_x - hud._boss_w * 0.5) < 0.01,
      f"{hud.boss_fill.scale_x:.3f}")

print("--- 7d. 打死 BOSS → 真结局 ---")
boss.take_damage(99999.0)
for enemy in list(world["enemies"].enemies):
    if enemy.alive:
        enemy.take_damage(99999.0)
step(0.4)
check("状态切回 CLEARED", state.state is State.CLEARED, state.state.name)
check("结局是 boss", session.cleared_kind == "boss", session.cleared_kind)
check("面板标题是 LIANG DEFEATED",
      clear_panel.title_text.text == "LIANG DEFEATED", clear_panel.title_text.text)
check("「进入 BOSS 层」按钮已收起", not clear_panel.boss_button.visible,
      "留着的话点了没反应，玩家会以为卡住")
# 整块 HUD 在 CLEARED 下一起隐藏（和 GAME_OVER 一致），所以验的是 root，
# 不是 `boss_bg.visible` —— 子节点还留着 True 也看不见。
check("HUD 已整体隐藏（BOSS 血条跟着消失）", not hud.root.visible)

print("--- 7e. 真结局后 PLAY AGAIN ---")
session.start_run()
step(DT)
check("重开后 BOSS 血条状态清干净",
      not (hud.boss_bg.visible or hud.boss_fill.visible or hud.boss_text.visible),
      f"bg={hud.boss_bg.visible} fill={hud.boss_fill.visible}")
check("重开后血条长度复位",
      abs(hud.boss_fill.scale_x - hud._boss_w) < 1e-6, f"{hud.boss_fill.scale_x:.3f}")
check("重开后回到第 0 波", waves.wave == 0, f"wave={waves.wave}")
check("重开后 cleared_kind 复位", session.cleared_kind == "run", session.cleared_kind)

print("--- 8. 音效 ---")
audio = world["audio"]
check("音效已装载（无缺失）", not audio.missing, f"missing={sorted(audio.missing)}")
check("整局播放过音效", audio.plays > 0, f"plays={audio.plays} 去重={audio.suppressed}")
# 这一条专门防"文件存在但 clip 是空的"：ursina 的 Audio 传 str 绝对路径时
# 会静默把 clip 设成 None，症状是完全没有声音且没有报错。
empty = [name for name, pool in audio._pools.items()
         for clip in pool if getattr(clip, "clip", None) is None]
check("每个音效实例都真的装上了 clip", not empty, f"空 clip: {empty}")

failed = [r for r in results if not r[0]]
for ok, title, detail in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {title}{('  ' + detail) if detail else ''}")
print(f"\n共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项")
if failed:
    print("失败项：" + "；".join(t for _ok, t, _d in failed))
    raise SystemExit(1)
