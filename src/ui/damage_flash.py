"""全屏色闪：受伤红 / 护盾吸收青 / 拾取柔光。

为什么不用「把玩家模型染红」：
第三人称下玩家只占画面一小块，贴墙时相机拉近还会把模型藏掉 ——
受伤时**必须**有一个不依赖角色的反馈，否则玩家只会看到血条悄悄变短。

它是一个 alpha 缓出的全屏 quad，挂在 `camera.ui` 下：
- 不参与世界坐标，怎么转头都在正中间；
- 不挂 collider，不挡射线；
- 只用 alpha，不改变画面亮度以外的任何东西（不遮挡准星，只叠一层色）。

三种闪光的强度必须**明显不同**，否则玩家分不清发生了什么：
- 受伤：红、alpha 0.32、0.28 秒 —— 是警告；
- 护盾吸收：青、alpha 0.26、0.22 秒 —— 是"叮"一下，不是警告；
- 拾取：道具主题色、alpha 0.10、0.30 秒 —— 只是余光里亮一下，
  不能盖过视野（玩家可能正在对枪）。

订阅由 `core/session.py` 统一注册，这里不自己 `event_bus.on()`。
"""
from __future__ import annotations

from ursina import Entity, camera, color, window

from src.core import config
from src.systems.pickup_system import PICKUP_RGB

_RED = (190, 25, 25)
_CYAN = (70, 205, 220)

# 护盾吸收的闪光：比受击短、比受击淡。它只是"叮"一下，不是警告。
SHIELD_FLASH_TIME = 0.22
SHIELD_FLASH_ALPHA = 0.26

# 拾取闪光：只让余光里亮一下。alpha 再高就会挡视线。
PICKUP_FLASH_TIME = 0.30
PICKUP_FLASH_ALPHA = 0.10


class DamageFlash:
    def __init__(self) -> None:
        # camera.ui 的坐标系是 ±aspect（横）× ±1（纵），乘 2 才能铺满。
        self.entity = Entity(
            parent=camera.ui,
            model="quad",
            color=color.rgba(*_RED, 0),
            scale=(window.aspect_ratio * 2.0 + 0.1, 2.1),
            z=-0.1,                 # 负 z 更靠前，压在其他 UI 之上
        )
        self._left = 0.0
        self._total = config.DAMAGE_FLASH_TIME
        self._rgb = _RED
        self._alpha = config.DAMAGE_FLASH_ALPHA

    # ---- 事件 ----
    def on_hurt(self, **_kwargs) -> None:
        self._flash(_RED, config.DAMAGE_FLASH_ALPHA, config.DAMAGE_FLASH_TIME)

    def on_shield(self, **_kwargs) -> None:
        self._flash(_CYAN, SHIELD_FLASH_ALPHA, SHIELD_FLASH_TIME)

    def on_pickup(self, kind: str | None = None, **_kwargs) -> None:
        rgb = PICKUP_RGB.get(kind or "")
        if rgb is None:
            return
        self._flash(rgb, PICKUP_FLASH_ALPHA, PICKUP_FLASH_TIME)

    def _flash(self, rgb, alpha: float, duration: float) -> None:
        self._rgb = rgb
        self._alpha = alpha
        self._total = max(duration, 1e-6)
        self._left = duration
        self.entity.color = color.rgba(*rgb, alpha)

    def reset(self) -> None:
        self._left = 0.0
        # 配色也要一起复位：否则重开后第一次受伤会沿用上一局最后一次拾取的颜色。
        self._rgb = _RED
        self._alpha = config.DAMAGE_FLASH_ALPHA
        self._total = config.DAMAGE_FLASH_TIME
        self.entity.color = color.rgba(*_RED, 0)

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        if self._left <= 0.0:
            return
        self._left = max(0.0, self._left - dt)
        k = self._left / self._total
        self.entity.color = color.rgba(*self._rgb, self._alpha * k)
