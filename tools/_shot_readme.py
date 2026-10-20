"""生成 README 用的一张干净对局截图（关掉调试覆盖层）。

用法：
    python tools/_shot_readme.py

输出：docs/screenshots/gameplay.png（1280×720）

为什么单独写一个：`_probe_look.py` 拍的是"没有 HUD、没有敌人"的白模检查图，
`_probe_clear_shot.py` 拍的是面板，两张都不适合放在 README 顶部。这里要的是
"玩家 + 一群敌人 + HUD" 的常规游玩视角，而且**不能有调试覆盖层** ——
那一整块坐标/射线诊断信息放在文档里只会让人以为这游戏满屏调试字。

和 `_probe_clear_shot.py` 一样必须开真窗口：离屏窗口下 `camera.ui` 的换算
不成立，HUD 会铺满全屏。截完立刻退，会闪一下。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panda3d.core import Filename  # noqa: E402
from ursina import Ursina, mouse  # noqa: E402

app = Ursina(size=(1280, 720),
                 # 必须显式关掉：ursina 的 development_mode 默认是 True，
                 # 会在右上角挂一块 "fps / entities / colliders" 的小浮层，
                 # 截图里看起来就像游戏自带调试界面（main.py 里是关着的）。
                 development_mode=False)
type(mouse).locked = property(lambda self: False, lambda self, v: None)

import tempfile                                       # noqa: E402

from src.core import config, event_bus                # noqa: E402

# 探针不许动真实存档。
config.SAVE_FILE = os.path.join(tempfile.gettempdir(), "readme_shot_save.json")
if os.path.exists(config.SAVE_FILE):
    os.remove(config.SAVE_FILE)

import main                                          # noqa: E402

OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "screenshots",
)
os.makedirs(OUT, exist_ok=True)

world = main.build_world()
waves = world["waves"]
enemies = world["enemies"]
player = world["player"]
rig = world["rig"]
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
    from ursina import window
    try:
        window.update_aspect_ratio()
    except Exception:      # pragma: no cover - 引擎版本差异
        pass
    # 连渲三帧再截：saveScreenshot 拿到的可能是上一帧的后备缓冲。
    for _ in range(3):
        app.graphicsEngine.renderFrame()
    path = os.path.join(OUT, name)
    app.win.saveScreenshot(Filename.fromOsSpecific(path))
    print("[shot]", path, os.path.getsize(path), "bytes")


world["session"].start_run()
step(0.2)

# 调试覆盖层整块关掉。它只有一个 Text，整屏左上方那一大段都是它。
world["overlay"].label.enabled = False
# 右上角的帧率读数也一起收起来：截图里挂一个 "60" 会被当成 HUD 的一部分。
world["fps_counter"].text.enabled = False


# 直接把波次摆到 12：不用真打十一波，只要 HUD 上有个像样的数字。
waves.wave = 12
waves.phase = "FIGHT"
waves.queue.clear()
world["data"].wave = 12

# 摆一排敌人（都在玩家正前方，距离 6~14 米，正好在画框里）。
player.x, player.z = 0.0, 0.0
rig.yaw = 0.0
lineup = [
    ("grunt", -3.4, 9.5),
    ("runner", 2.6, 8.0),
    ("tank", -0.9, 13.5),
    ("shooter", 4.0, 11.5),
    ("grunt", 1.2, 16.0),
]
for name, x, z in lineup:
    enemies.spawn_type(name, x, z)

# 让它们转过身来、站稳：敌人朝向有个 540°/s 的转速上限，不 step 的话
# 截图里全是背对镜头的后脑勺。
step(0.6)

# 相机由 CameraRig 每帧重写，必须放在 step 之后 —— 提前摆会被下一帧覆盖。
hud.update(DT)
shot("gameplay.png")

print("[probe] 场上", sum(1 for e in enemies.enemies if e.alive), "只")
app.userExit()
