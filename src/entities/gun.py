"""枪模型（纯视觉）。

**为什么不 parent 到玩家身上**：玩家的 `scale` 就是模型尺寸 (0.7, 1.8, 0.7)，
Ursina 的缩放会沿父级继承，枪挂上去会被垂直拉长 1.8 倍。
所以枪自己站在场景根下，每帧按玩家的位置和朝向摆放。

它没有 collider，也不参与判定：命中用的是相机射线，
枪口只决定拖尾的起点（规划书 §5.1 第 2 条）。
"""
from __future__ import annotations

import math

from ursina import Entity, Vec3, color

from src.core import config, lighting


class Gun:
    def __init__(self) -> None:
        # 本体就是"机匣"。M6 在它上面加了枪管/弹匣/枪托：
        # 只有一个长方体的枪在近景里像一根方木，玩家甚至认不出那是枪。
        # 子实体全部用**父级局部坐标**（±0.5 = 边界），父级缩放是 (0.13,0.13,0.75)，
        # 所以 y 方向 0.5 只有 6.5 厘米 —— 这和玩家身上那套"米"是两套单位。
        #
        # `config.GUN_MODEL` 填了路径就用自制模型。那种非等比缩放不会改，
        # 所以模型要按同样的比例建：0.13 米宽、0.75 米长、原点在枪身中心。
        # 配色比装甲**亮一档**：枪是玩家最需要看清的东西（它指着哪，
        # 子弹就往哪飞）。用装甲的深色会在 (16,17,22) 的背景里消失，
        # 第三人称背后视角尤其明显 —— 玩家会以为"我没拿枪"。
        self.entity = Entity(
            model=config.GUN_MODEL or "cube",
            color=color.rgb32(62, 68, 80),
            scale=(0.13, 0.13, 0.75),
            name="gun",
        )
        if config.GUN_MODEL:
            # 自制模型自带材质，不再挂着色器、也不再拼细节零件。
            self.muzzle = Entity(
                parent=self.entity,
                position=(0, 0, config.GUN_MUZZLE_FORWARD),
                name="gun_muzzle",
            )
            self._add_flash()
            return

        lighting.apply_lit(self.entity)

        # 枪管：比机匣细，往前伸到枪口附近
        self.barrel = lighting.apply_lit(Entity(
            parent=self.entity,
            model="cube",
            color=color.rgb32(88, 94, 110),
            scale=(0.45, 0.45, 0.55),
            position=(0, 0.05, 0.52),
            name="gun_barrel",
        ))
        # 弹匣：往下压一点，让侧面轮廓不是一条直边
        self.magazine = lighting.apply_lit(Entity(
            parent=self.entity,
            model="cube",
            color=color.rgb32(48, 52, 62),
            scale=(0.62, 1.0, 0.22),
            position=(0, -0.58, -0.06),
            name="gun_magazine",
        ))
        # 枪托
        self.stock = lighting.apply_lit(Entity(
            parent=self.entity,
            model="cube",
            color=color.rgb32(54, 58, 70),
            scale=(0.7, 0.85, 0.28),
            position=(0, -0.05, -0.52),
            name="gun_stock",
        ))

        # 机匣上的青色指示灯：和玩家的面罩/核心灯同一套语言。
        # 它的作用不是好看，是**让玩家在任何角度都能一眼定位枪在哪** ——
        # 从正后方看，枪的长轴朝着远方，投影只有一个小方块。
        self.pip = Entity(
            parent=self.entity,
            model="cube",
            color=color.rgb32(*config.PLAYER_ACCENT_RGB),
            scale=(0.30, 0.22, 0.10),
            position=(0, 0.55, 0.06),
            name="gun_pip",
        )

        self.muzzle = Entity(
            parent=self.entity,
            position=(0, 0, config.GUN_MUZZLE_FORWARD),
            name="gun_muzzle",
        )
        self._add_flash()

    def _add_flash(self) -> None:
        self.flash = Entity(
            parent=self.entity,
            model="sphere",
            color=color.rgb32(255, 238, 170),
            scale=0.26,
            position=(0, 0, config.GUN_MUZZLE_FORWARD + 0.04),
            visible=False,
            name="muzzle_flash",
        )
        self._flash_time = 0.0

    @property
    def muzzle_world(self):
        return self.muzzle.world_position

    def follow(self, player, rig=None) -> None:
        """按玩家位置摆放枪，同时把枪口压向准星方向。

        只跟水平朝向（不传 rig）时枪永远平指，
        但子弹是按相机俯仰飞的，看起来就是「枪朝前、子弹朝下」。
        所以把 rig 的俯仰（含后坐力）也加到枪的 rotation_x 上。
        """
        yaw = math.radians(player.rotation_y)
        forward = Vec3(math.sin(yaw), 0, math.cos(yaw))
        right = Vec3(math.cos(yaw), 0, -math.sin(yaw))

        self.entity.position = (
            player.world_position
            + right * config.GUN_OFFSET_RIGHT
            + Vec3(0, config.GUN_OFFSET_UP, 0)
            + forward * config.GUN_OFFSET_FORWARD
        )
        pitch = 0.0 if rig is None else rig.pitch + rig.recoil
        self.entity.rotation = (pitch, player.rotation_y, 0)

    def show_flash(self) -> None:
        self.flash.visible = True
        self._flash_time = config.MUZZLE_FLASH_TIME

    def update_flash(self, dt: float) -> None:
        if self._flash_time <= 0:
            return
        self._flash_time -= dt
        if self._flash_time <= 0:
            self.flash.visible = False
