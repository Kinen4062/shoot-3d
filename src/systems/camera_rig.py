"""第三人称相机：pivot 控角度，camera 挂在 pivot 后方的过肩位置。

层级：
    pivot   (位置 = 玩家眼睛高度, rotation = (pitch, yaw, 0))
      └── camera  (局部 (CAM_SHOULDER, 0, -distance))

pivot 的高度按「脚底往上 CAM_HEIGHT」算，而玩家原点在身体中心，
所以实际偏移要减去半个身高（否则镜头会浮在头顶以上 0.8 米）。
过肩偏移让角色偏到画面左下，准星正前方保持空视野。

为什么用父子层级，而不是每帧 camera.look_at(player)：
look_at 会和鼠标俯仰互相覆盖，两者每帧都在抢 camera 的旋转，
表现出来就是视角抖动、贴墙时镜头翻面。层级方案里相机姿态完全由 pivot 决定，
插值和碰撞都只是改一个标量 distance，不会打架。

鼠标增量语义（ursina/mouse.py:186-204）：
锁定后 mouse.velocity = 本帧指针偏移，且引擎当帧就把 position 归零，
所以它是「本帧位移」而不是「速度」，不要乘 dt。
"""
from __future__ import annotations

import math

from ursina import Entity, Vec3, camera, lerp_exponential_decay, mouse

from src.core import config
from src.core.game_state import State
from src.core.ray import raycast


def head_distance(hit_distance: float, lateral_offset: float) -> float:
    """把「从偏移后的探针出发的命中距离」换算成「离头部多远」。

    侧向射线的起点沿 right 偏了 lateral_offset，它到同一面墙的距离天然更近，
    直接拿来当相机距离会白扣掉半个身位（实测把 0.10 压成 0.00）。
    命中点 = 头 + right*offset + dir*d，而 right ⊥ dir，
    所以离头的真实距离就是 hypot(d, offset)。
    """
    return math.hypot(hit_distance, lateral_offset)


def clamp_camera_distance(hit_distance: float, max_distance: float) -> float:
    """把「射线能走多远」换算成「相机该离头多远」。

    核心约束：返回值**永远不超过 hit_distance - 余量**。
    一旦相机距离超过这段可用空间，镜头就落到墙体内部；
    立方体是背面剔除的，于是整堵墙变得不可见，看起来就是「穿进墙里」。

    所以这里不能有硬下限（早期版本用 max(0.9, ...)，
    贴墙时可用空间只有 0.1 米，被 0.9 顶进墙里 0.8 米，必现穿模）。
    空间真不够时宁可把相机贴到头上（此时角色模型会被隐藏），
    也不能让它出现在墙的另一侧。

    hit_distance 传 inf 表示畅通无阻。
    """
    available = hit_distance - config.CAM_COLLIDE_MARGIN
    return max(0.0, min(max_distance, available))


class CameraRig:
    def __init__(self, target: Entity, state) -> None:
        self.target = target
        self.state = state

        self.yaw = 0.0          # 水平环绕角（度）
        self.pitch = 12.0       # 俯仰角（度），正 = 往下看
        self.distance = config.CAM_DIST
        self.max_distance = config.CAM_DIST
        self.aiming = False
        self.recoil = 0.0       # 后坐力的临时俯仰偏移，会自己衰减回 0
        # 受击抖动：单独一条通道，衰减回 0，不写进 pitch/yaw。
        # 写进去的话抖完视角就永久偏了，玩家会以为鼠标飘了。
        self.shake = 0.0
        self._shake_t = 0.0
        # 肩偏移随瞄准插值：腰射时偏得多（看得见角色和场景），
        # 瞄准时收小（准星附近不被角色遮挡，弹道观感也更贴近中线）。
        self.shoulder = config.CAM_SHOULDER

        # 玩家的原点在身体中心（y = PLAYER_HEIGHT/2），不是脚底。
        # 所以「脚底往上 CAM_HEIGHT」要减掉半个身高才是相对原点的偏移。
        # 不减这一下，pivot 会比头顶还高 0.8 米（实测 2.6 而不是 1.7），
        # 镜头俯瞰角色、枪口到弹着点的连线也会斜得很夸张。
        self.eye_offset = config.CAM_HEIGHT - config.PLAYER_HEIGHT / 2

        self.pivot = Entity(name="camera_pivot")
        self.pivot.position = target.world_position + Vec3(0, self.eye_offset, 0)

        camera.parent = self.pivot
        camera.position = (self.shoulder, 0, -self.distance)
        camera.rotation = (0, 0, 0)
        # ursina 8.3 里 `camera.fov` 初始是 0（Camera.__init__ 里的 self.fov = 40
        # 会被窗口初始化覆盖），而 fov 只由 _update_aim 每帧插值上来 ——
        # 于是开局头半秒是从"几乎没有视场"拉出来的，画面像在急速变焦。
        # 这里显式给一个起始值，插值只在真正瞄准时才发生。
        camera.fov = config.HIP_FOV
        # 先把初始俯仰摆好，否则 MENU 阶段是平视，进 PLAYING 会突然俯下去一下。
        self.pivot.rotation = (self.pitch, self.yaw, 0)

    # ---- 每帧 ----
    def update(self, dt: float) -> None:
        if not self.state.in_state(State.PLAYING):
            # 离开 PLAYING 时（暂停/菜单）把角色显示回来，否则会被上一次的贴墙状态留在隐藏态。
            self._set_target_visible(True)
            if self.state.in_state(State.MENU):
                self._menu_orbit(dt)
            return

        self._apply_mouse_look()
        self._update_aim(dt)
        self.pivot.position = self.target.world_position + Vec3(0, self.eye_offset, 0)
        self.pivot.rotation = (self._view_pitch(), self.yaw, 0)
        self._apply_shake(dt)
        self._resolve_collision(dt)

    def _menu_orbit(self, dt: float) -> None:
        """主菜单背景：镜头绕着角色慢慢转。

        为什么要有它：菜单如果只是一张静止画面，玩家会以为游戏卡住了；
        缓慢环绕能证明"引擎在跑"，成本又只有一行角度累加。
        进对局时 `reset()` 会把 yaw 拨回 0，所以这里转多少都不影响开局朝向。

        **取景用独立的 MENU_CAM_DIST / MENU_CAM_PITCH**，不复用游戏内的 6m。
        实测 6 米时那个 0.7×1.8 的方块（玩家本体）会占掉画面左侧三分之一，
        菜单背景像贴了块蓝色卡片；9 米 + 18° 俯角才看得出这是竞技场。

        这里**不做碰撞射线**：相机跑到柱子背面时，正确的做法是重新取景，
        而不是像对局中那样把镜头怼到玩家脸上 —— 那会让菜单背景变成一张脸。
        柱子是有限的、位置固定，9 米的环形轨迹本来就绕开了它们。

        `fov` 也要拨回来：玩家瞄准中死亡时 fov 停在 AIM_FOV(32°)，不进对局
        就再也没人恢复它，菜单会一直是"长焦"画面。
        """
        self.yaw = (self.yaw + config.MENU_ORBIT_DEG * dt) % 360.0
        camera.fov = config.HIP_FOV
        self.pivot.position = self.target.world_position + Vec3(0, self.eye_offset, 0)
        self.pivot.rotation = (config.MENU_CAM_PITCH, self.yaw, 0)
        camera.position = (self.shoulder, 0, -config.MENU_CAM_DIST)

    # ---- 瞄准 / 后坐力 ----
    def set_aiming(self, aiming: bool) -> None:
        self.aiming = aiming

    def add_recoil(self, deg: float) -> None:
        """deg > 0 表示枪口上跳 = 视角抬高 = pitch 减小。

        只加在 `recoil` 偏移上，不写进 `pitch`：
        这样手指不动时准星会自己回到原处，不会越打越朝天。
        """
        self.recoil = max(-14.0, self.recoil - deg)

    def add_shake(self, deg: float) -> None:
        """受击抖动。多个敌人同时打中时叠加，但有上限 —— 否则屏幕会甩飞。"""
        self.shake = min(config.SHAKE_MAX, self.shake + deg)

    def _apply_shake(self, dt: float) -> None:
        """把抖动叠加到 pivot 的旋转上。

        用两条频率不同的正弦而不是随机数：
        - 随机数在高帧率下退化成高频噪声，看起来是「画面在爬」而不是「被打了」；
        - 正弦可复现，无窗口探针里也能断言数值。
        """
        if self.shake <= 1e-3:
            self.shake = 0.0
            return
        self._shake_t += dt * 42.0
        self.shake = lerp_exponential_decay(self.shake, 0.0, dt, config.SHAKE_DECAY)
        pitch = self.pivot.rotation_x + math.sin(self._shake_t * 1.7) * self.shake
        yaw = self.pivot.rotation_y + math.sin(self._shake_t * 2.3 + 1.1) * self.shake * 0.6
        self.pivot.rotation = (pitch, yaw, 0)

    def _view_pitch(self) -> float:
        return max(config.CAM_PITCH_MIN, min(config.CAM_PITCH_MAX, self.pitch + self.recoil))

    def _update_aim(self, dt: float) -> None:
        target_dist = config.CAM_DIST_AIM if self.aiming else config.CAM_DIST
        self.max_distance = lerp_exponential_decay(self.max_distance, target_dist, dt, 10.0)
        self.recoil = lerp_exponential_decay(self.recoil, 0.0, dt, config.RECOIL_RECOVER_RATE)

        target_shoulder = config.CAM_SHOULDER_AIM if self.aiming else config.CAM_SHOULDER
        self.shoulder = lerp_exponential_decay(self.shoulder, target_shoulder, dt, 10.0)

        target_fov = config.AIM_FOV if self.aiming else config.HIP_FOV
        camera.fov = lerp_exponential_decay(camera.fov, target_fov, dt, 10.0)


    def _apply_mouse_look(self) -> None:
        sens = config.MOUSE_SENS
        self.yaw += mouse.velocity[0] * sens
        self.pitch -= mouse.velocity[1] * sens
        self.pitch = max(config.CAM_PITCH_MIN, min(config.CAM_PITCH_MAX, self.pitch))

        if self.yaw > 180:
            self.yaw -= 360
        elif self.yaw < -180:
            self.yaw += 360

    def _resolve_collision(self, dt: float) -> None:
        """从 pivot 向相机方向打射线，被挡就把相机拉到墙前面。

        不做这一步，贴墙时镜头会插进墙里，画面直接看到墙内黑屏。
        """
        hit_distance = self._clear_distance()
        wanted = clamp_camera_distance(hit_distance, self.max_distance)

        if wanted < self.distance:
            # 拉近必须立即生效，慢一帧就够穿墙了。
            self.distance = wanted
        else:
            # 恢复可以柔和一点，否则从墙边走过会一顿一顿地弹。
            self.distance = lerp_exponential_decay(self.distance, wanted, dt, 6.0)

        camera.position = (self.shoulder, 0, -self.distance)
        # 贴到脸上时角色模型会挡住整个画面，藏掉它，行为退化成第一人称。
        self._set_target_visible(self.distance >= config.CAM_HIDE_PLAYER_DIST)

    def _clear_distance(self) -> float:
        """沿相机方向找最近的遮挡，返回射线命中距离（未扣余量）。

        没有任何遮挡时返回 inf，让上层直接取 max_distance。
        不能返回 max_distance 本身：那会被当成「正好在 6 米处有堵墙」，
        相机就永远被扣掉一个余量，正常开阔地也拉不满。
        打三条射线：正中一条，左右各偏移 CAM_COLLIDE_SPREAD。
        只有正中一条时，镜头贴着墙角或柱子棱边旋转，射线会从缝隙里漏过去，
        表现就是「有时穿、有时不穿」，非常难复现。
        侧向的结果必须先换算成「离头多远」再比较，否则偏移量会被重复扣一次。
        """
        origin = self.pivot.world_position
        back = self.pivot.back
        right = self.pivot.right
        # 相机实际站在过肩偏移点上，射线必须从那里出发，
        # 否则算的是「没有肩偏移的吊臂」，贴右侧墙时会晚一帧才拉近。
        origin = origin + right * self.shoulder
        best = float("inf")

        for offset in (-config.CAM_COLLIDE_SPREAD, 0.0, config.CAM_COLLIDE_SPREAD):
            probe = origin + right * offset
            hit = raycast(probe, back, distance=self.max_distance, ignore=[self.target])
            if hit.hit:
                best = min(best, head_distance(hit.distance, offset))

        return best

    def _set_target_visible(self, visible: bool) -> None:
        if self.target.visible != visible:
            self.target.visible = visible

    # ---- 重开 ----
    def reset(self) -> None:
        """回到初始视角。

        必须把 `max_distance` 和 `fov` 一起恢复：它们都是插值出来的中间量，
        只把 `distance` 拨回 6.0 而让 `max_distance` 停在瞄准时的 2.0，
        下一帧 `_resolve_collision` 会立刻把镜头拉回 2 米，
        表现为「重开后镜头一直贴在角色背上」。
        """
        self.yaw = 0.0
        self.pitch = 12.0
        self.distance = config.CAM_DIST
        self.max_distance = config.CAM_DIST
        self.aiming = False
        self.recoil = 0.0
        self.shake = 0.0
        self._shake_t = 0.0
        self.shoulder = config.CAM_SHOULDER
        camera.fov = config.HIP_FOV
        self._set_target_visible(True)
        self.pivot.position = self.target.world_position + Vec3(0, self.eye_offset, 0)
        self.pivot.rotation = (self.pitch, self.yaw, 0)
        camera.position = (self.shoulder, 0, -self.distance)

    # ---- 供玩家控制器读取朝向 ----
    @property
    def forward_yaw(self) -> float:
        return self.yaw
