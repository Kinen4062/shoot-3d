"""击杀分数飘字（规划书 §5.7「击杀 → 分数飘字」）。

**为什么值得单独一层**：HUD 右上的 `SCORE` 每帧都在变，玩家根本不会盯着它看；
伤害反馈靠"数字跳了"传达，等于没有反馈。飘字把"这一枪值多少钱"直接贴在
敌人倒下的位置，是唯一能在交火中读到的计分信息。

三条实现约束：

1. **对象池**：一波 8 只怪，每只 0.9 秒飘一次，峰值十几个；每次 new 一个
   `Text` 会重建文字纹理（和 HUD 那边同一个坑），所以固定 12 个对象轮转。
2. **世界 → 屏幕**：飘字必须出现在**敌人死的地方**，不能固定在屏幕中央。
3. **相机背后不飘**：算出来的点如果落在相机后方，会在屏幕另一侧冒出一个
   翻转的 "+100"，所以先判深度。

**为什么不用 Panda 的 `camLens.project()`**

本项目里相机的正前方是**局部 +Z**（`src/core/screen_ray.py` 里记了四处独立
实现都依赖这个约定），而 Panda 的 lens 约定是 **+Y 朝前**。实测：

    camera.getRelativePoint(render, 玩家位置) == (-0.55, -0.78, +6.17)

y 是负的 —— 直接把这三个分量喂给 `camLens.project()` 会返回 False
（它以为点在相机背后）。要做就得自己把 (x, y, z) 换成 (x, z, y)，
再依赖 lens 的 fov 语义；不如直接用相机的基向量做点乘，公式短、能单测、
也不依赖有没有开窗口。

推导（`fov` 是**水平**视场角，见 ursina/camera.py:60）：

    z = (P-C)·forward,  x = (P-C)·right,  y = (P-C)·up
    t = tan(fov/2)
    ui_x = (x/z) / t · aspect/2          # 屏幕右边缘对应 x/z = t
    ui_y = (y/z) · aspect/t · 0.5        # 垂直半角正切 = t/aspect

注意 ui_x 里的 aspect/2 和 `t` 是两回事：前者是坐标系换算（ui 的 x 范围是
±aspect/2），后者是投影本身。把它俩混起来就会得到"只有正中间准"的怪现象。
"""
from __future__ import annotations

import math

from ursina import Entity, Text, camera, window

from src.core import config
from src.core.game_state import State
from src.ui.widgets import rgba32

# 颜色沿用 HUD 的金色系：飘字是"奖励",和红色的伤害反馈区分开。
RGB_POPUP = (255, 208, 110)
RGB_POPUP_COMBO = (140, 225, 255)      # 连击时换成青色，和护盾/连击提示同族

NEAR = 0.25          # 深度下限（米）。再近就不飘了，那是贴脸，不是击杀点


def project_ui(x: float, y: float, z: float, fov_deg: float, aspect: float):
    """相机坐标系下的偏移 → `camera.ui` 坐标。在相机后方返回 None。

    x/y/z 分别是相对相机位置在 right / up / forward 上的投影。
    """
    if z <= NEAR:
        return None
    t = math.tan(math.radians(max(1.0, fov_deg)) * 0.5)
    return (x / z) / t * aspect * 0.5, (y / z) * aspect / t * 0.5


def popup_curve(age: float, life: float) -> tuple[float, float]:
    """飘字动画：返回 (上升量 0..1, 不透明度 0..1)。

    - 上升用 sqrt 曲线：一开始窜得快、后面几乎停住，像被打飞出去一样；
      线性上升看起来像"匀速电梯"，很廉价。
    - 透明度前 35% 保持满亮（否则刚出现就看不清），之后线性淡出。
    """
    if life <= 0.0:
        return 1.0, 0.0
    k = max(0.0, min(1.0, age / life))
    rise = k ** 0.5
    if k < 0.35:
        alpha = 1.0
    else:
        alpha = 1.0 - (k - 0.35) / 0.65
    return rise, max(0.0, alpha)


class ScorePopup:
    """击杀飘字。事件订阅由 `RunSession` 统一注册（`score_gained`）。"""

    def __init__(self, state) -> None:
        self.state = state
        self.root = Entity(parent=camera.ui, name="score_popup")
        self.root.visible = False

        self._slots: list[dict] = []
        for _ in range(config.POPUP_COUNT):
            label = Text(
                text="", parent=self.root, origin=(0, 0), scale=config.POPUP_FONT,
                position=(0, 0), color=rgba32(RGB_POPUP, 0.0),
            )
            label.visible = False
            self._slots.append({
                "label": label, "age": 0.0, "base": (0.0, 0.0),
                "live": False, "rgb": RGB_POPUP,
            })
        self._next = 0
        self.shown = 0            # 统计：飘过多少次（探针/覆盖层可查）

    # ---------------------------------------------------------------- 事件
    def on_score_gained(self, amount: int = 0, position=None, combo: int = 0, **_kwargs) -> bool:
        """在击杀位置飘一个 `+N`。返回 False 表示这次没飘（位置投影失败等）。"""
        if not self.state.in_state(State.PLAYING):
            return False
        if amount <= 0 or position is None:
            return False
        ui = self._project(position)
        if ui is None:
            return False

        slot = self._slots[self._next]
        self._next = (self._next + 1) % len(self._slots)

        label = slot["label"]
        label.text = f"+{int(amount)}"
        # 连击 ≥2 时换个颜色，玩家不用看 HUD 就知道倍率生效了。
        slot["rgb"] = RGB_POPUP_COMBO if combo >= 2 else RGB_POPUP
        label.color = rgba32(slot["rgb"], 1.0)
        slot["base"] = ui
        slot["age"] = 0.0
        slot["live"] = True
        label.position = ui
        label.visible = True
        self.shown += 1
        return True

    def _project(self, world_pos) -> tuple[float, float] | None:
        """世界坐标 → `camera.ui` 坐标。相机背后返回 None。"""
        from ursina import Vec3

        try:
            delta = Vec3(*world_pos) - camera.world_position
            x = delta.dot(camera.right)
            y = delta.dot(camera.up)
            z = delta.dot(camera.forward)
            return project_ui(x, y, z, camera.fov, window.aspect_ratio)
        except Exception:             # noqa: BLE001 - 投影失败不该打断主循环
            return None

    # ---------------------------------------------------------------- 每帧
    def update(self, dt: float) -> None:
        playing = self.state.in_state(State.PLAYING)
        if self.root.visible != playing:
            self.root.visible = playing
        if not playing:
            return                    # 暂停时冻住，别让数字自己飘走

        life = config.POPUP_LIFE
        for slot in self._slots:
            if not slot["live"]:
                continue
            slot["age"] += dt
            rise, alpha = popup_curve(slot["age"], life)
            if alpha <= 0.0:
                slot["live"] = False
                slot["label"].visible = False
                continue
            bx, by = slot["base"]
            slot["label"].position = (bx, by + rise * config.POPUP_RISE)
            slot["label"].color = rgba32(slot["rgb"], alpha)

    # ---------------------------------------------------------------- 重开
    def reset(self) -> None:
        for slot in self._slots:
            slot["live"] = False
            slot["age"] = 0.0
            slot["label"].visible = False
        self.root.visible = False
        self._next = 0
