"""截图：通关面板 / BOSS 战 HUD（肉眼验收，不是自动化断言）。

用法：
    python tools/_probe_clear_shot.py

输出到 tools/_shots/ 下三张 PNG：clear_panel / boss_bar / boss_end。

**为什么用真窗口而不是 `window_type="offscreen"`**

`camera.ui` 的节点 scale 是 20、正交相机视野也是 20，两者抵消之后
位置才是窗口坐标（见 `ui/widgets.py` 的说明）。离屏窗口下这层换算不成立：
实测卡片 `scale=(0.74, 0.74)` 会铺满整个 1280×720 的画面，
文字全部跑到画外 —— 截出来的三张图只有一片纯色，什么也验不了。
所以这里开一个真的 1280×720 窗口，截完立刻退出（会闪一下）。

环境变量 `NO_WINDOW=1` 时退回离屏模式：只跑逻辑、不截图，
用来在 CI / 无显示器的机器上确认这条链路不炸。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panda3d.core import Filename  # noqa: E402
from ursina import Ursina, application, camera, mouse  # noqa: E402

NO_WINDOW = os.environ.get("NO_WINDOW") == "1"
app = Ursina(window_type="offscreen" if NO_WINDOW else None, size=(1280, 720))
type(mouse).locked = property(lambda self: False, lambda self, v: None)

import tempfile                                       # noqa: E402

from src.core import config, event_bus                # noqa: E402

# 探针不许动真实存档（`%APPDATA%\\MiniTPS\\save.json`）。
config.SAVE_FILE = os.path.join(tempfile.gettempdir(), "clear_shot_save.json")
if os.path.exists(config.SAVE_FILE):
    os.remove(config.SAVE_FILE)

import main                                        # noqa: E402
from src.core.game_state import State              # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(OUT, exist_ok=True)

world = main.build_world()
state = world["state"]
session = world["session"]
waves = world["waves"]
enemies = world["enemies"]
hud = world["combat_hud"]
DT = 1.0 / 60.0


def step(seconds: float) -> None:
    for _ in range(max(1, int(round(seconds / DT)))):
        for system in world["loop"].systems:
            system.update(DT)
        for system in world["loop"].ui_systems:
            system.update(DT)
        event_bus.flush()


def shot(name: str) -> None:
    if NO_WINDOW:
        return
    # 真窗口下 UI 的换算是靠 aspect / 视口算出来的，几何要先过一遍
    # `update_aspect_ratio`，否则第一帧的卡片还是按旧尺寸画的。
    try:
        from ursina import window
        window.update_aspect_ratio()
    except Exception:       # pragma: no cover - 只在引擎版本差异时走到
        pass
    # **必须多渲几帧再截**：`saveScreenshot` 拿到的可能是上一帧的后备缓冲，
    # 只渲一帧的话截图里会留着"隐藏之前"的 HUD（第一版就是这样，
    # 通关面板右上角还挂着 WAVE / KILLS / COMBO）。
    for _ in range(3):
        app.graphicsEngine.renderFrame()
    path = os.path.join(OUT, name)
    app.win.saveScreenshot(Filename.fromOsSpecific(path))
    print("[shot]", path, os.path.getsize(path), "bytes")


# ---- 1. 通关面板 ----
session.start_run()
step(DT)
waves.wave = 30
waves.phase = "FIGHT"
waves.queue.clear()
# 探针直接改 wave 字段，绕过了 `wave_started` 事件 —— 计分系统的 wave 数字
# 是由那个事件推进的，不补这一行，面板上会显示 WAVE 0（纯探针假象）。
world["data"].wave = 30
step(DT)
shot("clear_panel.png")
print("     state =", state.state.name, " kind =", session.cleared_kind)

# ---- 2. BOSS 层：血条 + 场面 ----
session.enter_boss_layer()
step(0.1)
boss = None
for _ in range(240):
    step(0.05)
    boss = next((e for e in enemies.enemies if e.name == "liang"), None)
    if boss is not None and waves.phase == "FIGHT":
        break

# 相机摆到 BOSS 正前方：这一张要同时看见"它有多大"和"顶上那条血条"。
# 生成点在半径 18~26 的圆环上，直接按 BOSS 的实际坐标反推机位，
# 别写死角度 —— 每次生成方向都不同，写死的机位大概率拍到空气。
#
# **必须先 step 再摆相机**：`CameraRig.update` 是 systems 里的一员，
# 每帧都会按玩家位置重写 camera.position。先摆后 step 等于白摆
# （第一版就是这样，拍到的是别人家的小腿）。
step(0.1)
bx, bz = boss.x, boss.z
camera.fov = 55.0
camera.position = (bx, 2.6, bz + 10.0)
camera.look_at((bx, 1.7, bz))
camera.rotation_z = 0
shot("boss_bar.png")
print("     boss hp =", f"{boss.hp:.0f}/{boss.max_hp:.0f}",
      " bar =", f"{hud.boss_fill.scale_x:.3f}")
print("     squad =", sorted(e.name for e in enemies.enemies if e.alive))

# ---- 2b. 炮击：直接让它开三炮再拍 ----
# 不等 AI 走完"看见 → 冷却 → 开炮"，那样要空跑好几秒，中途玩家可能先被打死。
# 这里只验"画面上能不能分辨出粉紫色的 BOSS 弹丸"。
for _ in range(3):
    enemies._resolve_volley(boss, boss.data)
step(0.35)                     # 让弹丸飞出去一段再拍
camera.fov = 55.0
camera.position = (bx + 9.0, 3.2, bz + 6.0)
camera.look_at((bx, 1.6, bz))
camera.rotation_z = 0
shot("boss_volley.png")
print("     projectiles =", world["projectiles"].shots, "发")

# ---- 3. BOSS 倒下 → 真结局面板 ----
for enemy in list(enemies.enemies):
    if enemy.alive:
        enemy.take_damage(99999.0)
step(0.3)
shot("boss_end.png")
print("     state =", state.state.name, " kind =", session.cleared_kind)

# ---- 4. 霰弹枪 HUD：拿着它 / 还没解锁时的提示 ----
session.start_run()
step(DT)
weapon = world["weapon"]
hud = world["combat_hud"]

# 4a. 第 15 波，按 3 切到霰弹枪 → 右下角应该显示 SHOTGUN 7/7
waves.wave = 15
world["data"].wave = 15
step(0.15)
assert weapon.switch(weapon.slots.index("shotgun")) is True
step(0.2)
shot("hud_shotgun.png")
print("     weapon =", weapon.weapon_name,
      " ammo =", f"{weapon.current.ammo}/{weapon.current.mag_size}")

# 4b. 第 5 波，按 3 → 切不过去，但要在弹药上方留一行 3 秒的锁定提示。
session.start_run()
step(DT)
waves.wave = 5
world["data"].wave = 5
step(0.15)
weapon.switch(weapon.slots.index("shotgun"))
step(0.2)
shot("hud_locked.png")
print("     weapon =", weapon.weapon_name, " locked =", repr(weapon.locked_name))
