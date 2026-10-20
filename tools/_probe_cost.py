"""性能画像：量「空场景」与「满编 30 只敌人」的 draw call 数，以及纯逻辑每帧耗时。

改渲染相关代码（合并几何、换着色器、加特效）前后各跑一次，用同一组数字对比。
不需要窗口：

    python tools/_probe_cost.py

输出三块：
1. `Geoms` —— 每个 Geom 基本就是一次 draw call，这是弱显卡上的第一瓶颈；
2. `transforms` —— 每帧要重算的节点变换数；
3. `[逻辑]` —— AI tick + 分离力 + 移动的每帧耗时（不含渲染）。

当前基线（2026-10-20，30 只敌人）：
    空场景 254 geoms / 294 transforms
    满编   853 geoms / 953 transforms   <- 30 只敌人吃掉了 599 次调用
    逻辑   0.91ms/帧（16.6ms 预算的 5.5%）

顶点总共才 3738 个、三角形 8.2 万 —— 几何量极小，几乎全是**调用次数**的问题。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panda3d.core import GeomNode  # noqa: E402
from ursina import Ursina, mouse, scene  # noqa: E402

app = Ursina(window_type="none")
# 无窗口时 mouse.locked 的 setter 会碰 base.win，把它退化掉。
type(mouse).locked = property(lambda self: False, lambda self, value: None)

import main  # noqa: E402

world = main.build_world()


def visible_geoms() -> int:
    """真正会被提交给显卡的 GeomNode 数（= draw call 的下界）。

    不能用 `analyze()` 的 Geoms：它把**隐藏节点**也算进去，而场地的碰撞代理
    正是隐藏的（`visible=False`），合并收益会被完全掩盖。所以这里自己判
    "自己和所有祖先都没被 hide"。
    """
    count = 0
    for np_ in scene.findAllMatches("**/+GeomNode"):
        if np_.isHidden() or any(a.isHidden() for a in np_.getAncestors()):
            continue
        count += 1
    return count


def stats(label: str) -> None:
    print(f"\n===== {label} =====")
    print(f"节点数 = {len(scene.findAllMatches('**'))}")
    print(f"可见 Geom（≈draw call）= {visible_geoms()}")
    scene.analyze()


stats("空场景（场地 + 玩家 + 靶子 + UI）")

enemies = world["enemies"]
kinds = ["grunt", "runner", "tank", "shooter"]
for i in range(30):
    enemies.spawn_type(kinds[i % 4], 8.0 + (i % 6) * 1.5, 8.0 + (i // 6) * 1.5)

stats("满编 30 只（grunt/runner/tank/shooter 各 7~8 只）")
print("每只敌人的零件数：",
      {k: len(e.skin_parts) for k, e in zip(kinds, enemies.enemies)})

# ---- 纯逻辑每帧耗时（AI tick + 分离力 + 移动，不含渲染与 UI）----
frames = 600
dt = 1.0 / 60.0
t0 = time.perf_counter()
for _ in range(frames):
    enemies.update(dt)
t1 = time.perf_counter()
print(f"\n[逻辑] {frames} 帧共 {(t1 - t0) * 1000:.1f}ms，"
      f"每帧 {(t1 - t0) / frames * 1000:.3f}ms（30 只敌人）")
print("PROBE-DONE")
