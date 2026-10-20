"""玩家实体。

注意：这个类**不实现 update()**。Ursina 会把 update 分发给场景里所有实体，
顺序不可控；玩家的更新由 GameLoop 调 PlayerController 完成。
实体自己只负责「长什么样、有什么状态」。

血量逻辑放在 `core/health.py`：无敌帧的边界条件必须能被单测钉死，
而 `Health` 不依赖引擎，测试里直接 new 一个就能跑。

M4 增加两个状态：
- `shield_charges`：护盾层数，每次受击消耗一层，**先于血量结算**；
- `speed_mult` / `speed_boost_left`：加速道具，倍率由计时器驱动，
  计时归零必须把倍率恢复成 1.0，否则加速会永久生效。

护盾外壳（`shield_aura`）是玩家的子实体，专门解决"有盾看不出来"：
- 不挂 collider —— 挂了之后子弹会停在自己的罩子上，表现是"开枪没伤害"；
- 作为子实体，相机贴墙把玩家藏掉时（`visible=False`）它跟着一起消失，
  不会出现"人没了还剩一个球"；
- 尺寸按父级非等比缩放换算：父级是 (0.7, 1.8, 0.7)，所以 x/z 放大 1.55、
  y 只放大 1.18，罩子才是贴着身体的椭球而不是被拉成一根柱子。

M6 把「一个蓝色方块」换成了装甲兵外观（见 `player_body.py`）。
做法是**父实体退化成纯碰撞盒**：先按 `model="cube"` 建好 `collider="box"`，
再把 `model` 摘掉。这样：
- 碰撞体是构建时按 `model_bounds` 拟合的，摘模型不影响它（尺寸仍是 0.7×1.8×0.7），
  所以射线、相机贴墙、柱子推开这些逻辑一行都不用改；
- 父实体不再渲染任何东西，不会出现"外壳方块把里面的人挡住"；
- 外观整体挂在 rig 下，`visible` / `position` / `rotation_y` 的语义完全不变。
"""
from __future__ import annotations

import math

from ursina import Entity, color

from src.core import config, event_bus
from src.core.health import Health
from src.entities import ground_shadow, player_body


class Player(Entity):
    def __init__(self) -> None:
        super().__init__(
            model="cube",
            color=color.rgb32(*config.PLAYER_ARMOR_RGB),
            scale=(config.PLAYER_WIDTH, config.PLAYER_HEIGHT, config.PLAYER_WIDTH),
            position=(0, config.PLAYER_HEIGHT / 2, 0),
            collider="box",
            name="player",
        )

        # 碰撞盒已经拟合好了，把模型摘掉：父实体从此只是"带盒体的变换节点"。
        self.model = None

        # 外观。36 个方块，内部坐标是米（rig 会抵消父级的非等比缩放）。
        self.body = player_body.build_body(self)

        # 脚下的接触阴影。跟着 visible 一起开关，贴墙藏人时不会剩一块黑饼。
        self.shadow = ground_shadow.attach(
            self, config.PLAYER_SHADOW_RADIUS, config.PLAYER_SHADOW_ALPHA
        )

        self.health = Health(config.PLAYER_MAX_HP, config.I_FRAME_TIME)

        # ---- M4 ----
        self.shield_charges = 0
        self.speed_mult = 1.0
        self.speed_boost_left = 0.0

        # 护盾外壳。alpha 从 0 开始，靠 tick_shield_aura 每帧按层数/呼吸曲线写。
        # 尺寸：父级 scale 是 (0.7, 1.8, 0.7)，乘上这里的局部倍数后
        # 世界尺寸约 (1.09, 2.12, 1.09)，比碰撞盒 (0.7, 1.8, 0.7) 各边宽 0.16~0.19 米，
        # 也仍然能把 M6 的装甲外观（最宽处是护肩，约 0.88 米）整个罩住。
        self.shield_aura = Entity(
            parent=self,
            model="sphere",
            color=color.rgba32(*config.SHIELD_AURA_RGB, 0),
            scale=(1.55, 1.18, 1.55),
            visible=False,
            name="player_shield_aura",
        )
        # 呼吸相位与上一次写入的 alpha 字节值（量化后比较，见 tick_shield_aura）。
        self._aura_phase = 0.0
        self._aura_alpha = -1

    # ---- 血量（转发到 Health，调用方不必知道它在哪）----
    @property
    def hp(self) -> float:
        return self.health.hp

    @property
    def max_hp(self) -> float:
        return self.health.max_hp

    @property
    def alive(self) -> bool:
        return self.health.alive

    @property
    def iframe(self) -> float:
        return self.health.iframe

    def take_damage(self, amount: float) -> bool:
        """返回 False 表示这一下被无敌帧 / 护盾吃掉。

        判定顺序很重要：**先无敌帧，再护盾，最后血量**。
        反过来写的话，无敌帧期间挨打也会消耗护盾 ——
        玩家闪避翻滚时明明是无敌的，护盾却白白掉了一层。
        """
        if not self.health.alive or self.health.invulnerable:
            return False

        if self.shield_charges > 0:
            self.shield_charges -= 1
            event_bus.emit("shield_absorbed", charges=self.shield_charges)
            return False

        if not self.health.take(amount):
            return False
        event_bus.emit("player_hurt", amount=amount, hp=self.health.hp,
                       max_hp=self.health.max_hp)
        if not self.health.alive:
            event_bus.emit("player_died")
        return True

    def heal(self, amount: float) -> float:
        return self.health.heal(amount)

    # ---- M4 道具效果 ----
    def add_shield(self, max_charges: int = config.SHIELD_MAX_CHARGES) -> int:
        self.shield_charges = min(max_charges, self.shield_charges + 1)
        return self.shield_charges

    def grant_speed_boost(self, mult: float, duration: float) -> None:
        """重复拾取只刷新时长，不叠倍率 —— 叠倍率会让走位彻底失控。"""
        self.speed_mult = max(self.speed_mult, mult)
        self.speed_boost_left = max(self.speed_boost_left, duration)

    def tick_buffs(self, dt: float) -> None:
        if self.speed_boost_left <= 0.0:
            return
        self.speed_boost_left -= dt
        if self.speed_boost_left <= 0.0:
            self.speed_boost_left = 0.0
            self.speed_mult = 1.0

    def tick_shield_aura(self, dt: float) -> None:
        """按层数更新护盾外壳：没盾就藏起来，有盾就按层数变亮并轻微呼吸。

        由 `PlayerController.update` 每帧调用（和 `tick_buffs` 同一个位置），
        不实现 `update()` 是因为 Ursina 会把 update 派发给所有实体，顺序不可控。
        """
        aura = self.shield_aura
        charges = self.shield_charges

        if charges <= 0 or not self.alive:
            if aura.visible:
                aura.visible = False
            self._aura_phase = 0.0
            self._aura_alpha = -1        # 下次有盾时强制重写一次颜色
            return

        self._aura_phase = (self._aura_phase
                            + dt * math.tau * config.SHIELD_AURA_PULSE_HZ) % math.tau
        alpha = (config.SHIELD_AURA_ALPHA
                 + config.SHIELD_AURA_ALPHA_STEP * (charges - 1)
                 + math.sin(self._aura_phase) * config.SHIELD_AURA_PULSE)
        alpha = 0.05 if alpha < 0.05 else (0.5 if alpha > 0.5 else alpha)

        # 只有量化后的值真的变了才写 color：color setter 会重建 Vec4、
        # 重设 color scale，还会让 Panda 重排透明队列 —— 60fps 无条件写纯属浪费。
        # 量化到 1/64（步长 4/255）而不是逐字节：呼吸幅度只有 0.09，
        # 逐字节时一秒要写四十多次，而这些差别肉眼完全看不出来。
        byte = (int(alpha * 255.0) // 4) * 4
        if not aura.visible or byte != self._aura_alpha:
            self._aura_alpha = byte
            aura.color = color.rgba32(*config.SHIELD_AURA_RGB, byte)
            aura.visible = True

    def reset(self) -> None:
        self.health.reset()
        self.shield_charges = 0
        self.speed_mult = 1.0
        self.speed_boost_left = 0.0
        self.shield_aura.visible = False
        self._aura_phase = 0.0
        self._aura_alpha = -1
        self.position = (0, config.PLAYER_HEIGHT / 2, 0)
        self.rotation = (0, 0, 0)
        self.visible = True
