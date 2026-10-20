"""离屏渲染截图：用来肉眼验收 M6 的外观改动（不是自动化测试）。

用法：
    python tools/_probe_look.py

输出到 tools/_shots/ 下三张 PNG：gameplay / player_front / arena。
无头环境下 `window_type='none'` 拿不到像素，必须用 'offscreen'；
离屏窗口里 camera.ui 的缩放不对（UI 会糊满全屏），所以截图前把 UI 整体藏掉。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panda3d.core import Filename  # noqa: E402
from ursina import Ursina, camera, mouse  # noqa: E402

app = Ursina(window_type="offscreen", size=(1280, 720))
type(mouse).locked = property(lambda self: False, lambda self, v: None)

import main                                   # noqa: E402
from src.core.game_state import State         # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(OUT, exist_ok=True)

world = main.build_world()
state = world["state"]
player = world["player"]
rig = world["rig"]
arena = world["arena"]

# UI 全部藏掉：离屏窗口下 camera.ui 的坐标换算和真实窗口不一致，会盖满画面。
camera.ui.visible = False

state.set(State.PLAYING)
for _ in range(6):
    rig.update(1 / 60)
# 枪是场景根下的独立实体，位置由 WeaponSystem 每帧 follow 写入。
# 探针里没跑主循环，得手动摆一次，否则枪还停在原点（陷在腿里）。
world["weapon"].gun.follow(player, rig)
camera.fov = 40.0


def shot(name: str) -> None:
    """渲染一帧并存图。saveScreenshot 要在 renderFrame 之后调。"""
    app.graphicsEngine.renderFrame()
    path = os.path.join(OUT, name)
    app.win.saveScreenshot(Filename.fromOsSpecific(path))
    print("[shot]", path, os.path.getsize(path), "bytes")


# 1. 游戏内视角（第三人称，相机在玩家背后）
shot("gameplay.png")

# 2. 正面 3/4 近景：看外观、面罩、护肩和枪是不是对得上手。
#    ursina 的相机是**局部 +Z 朝前**，所以要看正面就必须站在 +Z 一侧，
#    并把 rotation_y 转到 180（第一版把机位放在 -Z，拍到的是后背）。
player.position = (0, 0.9, 0)
player.rotation_y = 0
world["weapon"].gun.follow(player, None)
camera.fov = 38.0
camera.position = (2.1, 1.55, 4.3)
camera.look_at(player.world_position + (0, 0.25, 0))
camera.rotation_z = 0     # look_at 会带一点横滚，截图看着像镜头歪了
shot("player_front.png")

# 3. 场地全景：看墙和柱子的明暗面、地面网格。
#    场地 half = 28 米，所以机位要退到 30 米开外才装得下（第一版在 -18，
#    右半张图全是场地外的黑）。
camera.fov = 55.0
camera.position = (0, 15.0, -30.0)
camera.rotation = (30, 0, 0)
shot("arena.png")

# 4. 四种敌人一字排开：看体型差异、发光位置、眼条朝向。
#    这是「一个模型打四个人」最容易露馅的地方 —— 如果四种剪影分不出来，
#    混战里玩家就只是在打四团颜色。
from src.entities.enemy import spawn_enemy           # noqa: E402
from src.core.enemy_types import FALLBACK_ENEMY_TYPES  # noqa: E402

order = ("grunt", "runner", "tank", "shooter")
squad = []
for i, kind in enumerate(order):
    data = FALLBACK_ENEMY_TYPES[kind]
    # yaw=0 才面朝 +Z（相机在 +Z 一侧）。写 180 的话看到的全是后背，
    # 而眼条是判断朝向的唯一线索，背对就等于没验。
    squad.append(spawn_enemy(kind, data, -4.5 + i * 3.0, 6.0, yaw=0))

# 玩家正站在队列正中，会挡住 grunt。探针只验敌人，先藏起来。
player.visible = False

# 相机略微偏到一侧（约 12°）：正对着拍只能看到炮管的端面，
# 斜一点才看得出"肩上是往前伸的一根管子"和背包的厚度。
camera.fov = 50.0
camera.position = (3.6, 2.05, 17.4)
camera.look_at((0, 1.15, 6.0))
camera.rotation_z = 0
shot("enemies.png")
player.visible = True

print("[ok] 四张图在", OUT)
print("     grid texture =", world["arena"].ground.texture)
print("     static lit   =", arena.static.shader is not None,
      " 碰撞代理 =", len(arena.solids))
print("     ground lit   =", arena.ground.shader is not None)
print("     player parts =", len(player.body.children))
for kind, enemy in zip(order, squad):
    print(f"     {kind:8s} parts={len(enemy.skin_parts):2d} "
          f"scale={tuple(round(v, 2) for v in enemy.scale)} "
          f"eye_z={enemy.eye.world_z - enemy.world_position.z:+.2f}")
