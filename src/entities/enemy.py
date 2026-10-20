"""敌人实体：一个方盒 + 血量 + 受击表现 + 行为状态机的载体。

**实体不实现 `update()`**（全项目唯一 update 入口是 GameLoop）。
这里只提供「长什么样、剩多少血、死了没有」，移动与决策都由
`systems/enemy_ai.py` + `systems/enemy_system.py` 推进。

`collider='box'` 必须加，而且尺寸和模型一致 ——
射线只检测碰撞体，没有 collider 的敌人「看着能打，实际打不中」。
BoxCollider 挂在节点上会跟着 scale 走，所以出生时设一次就够。
"""
from __future__ import annotations

from ursina import Entity, Vec3, color

from src.core import config, event_bus
from src.core.enemy_types import EnemyData
from src.entities import enemy_body, ground_shadow
from src.systems.enemy_ai import Brain


def enemy_from(entity):
    """从射线命中的实体往上找 Enemy。

    命中点可能落在敌人的子部件上（M3 会有头/身体），所以不能只判一层。
    """
    while entity is not None:
        if isinstance(entity, Enemy):
            return entity
        entity = getattr(entity, "parent", None)
    return None


class Enemy(Entity):
    def __init__(
        self,
        hp: float,
        position=(0, 0, 0),
        color_rgb=config.ENEMY_COLOR_RGB,
        scale=config.ENEMY_SCALE,
        respawn_delay: float = 0.0,
        name: str = "enemy",
        data: EnemyData | None = None,
        speed_mult: float = 1.0,
    ) -> None:
        # 模型选择顺序：本类型专用 → 通用 ENEMY_MODEL → 方块。
        # 四种敌人共用一套 scale 拉伸，所以自制模型统一按 1.8 米标准身材建
        # （见 config.ENEMY_MODELS 的注释），类型差异由拉伸和配色体现。
        kind = data.name if data is not None else name
        model_path = config.ENEMY_MODELS.get(kind, "") or config.ENEMY_MODEL
        custom_model = bool(model_path)
        super().__init__(
            model=model_path or "cube",
            color=color.rgb32(*color_rgb),
            scale=scale,
            position=position,
            collider="box",
            name=name,
        )
        self.max_hp = float(hp)
        self.hp = float(hp)
        self.alive = True
        self.removed = False            # EnemySystem 据此把它从列表里摘掉
        self.respawn_delay = respawn_delay

        self.base_color = self.color
        self.base_scale = Vec3(*scale)
        self._flash = 0.0               # >0 表示还在闪白
        self._dying = 0.0               # >0 表示正在播放死亡缩放
        self._respawn = 0.0             # >0 表示等待重生

        # ---- M6 外观 ----
        # `skin_parts` 是 [(零件, 它本来的颜色)]，受击闪白要靠它逐个改色。
        # 不能只改父实体的 color —— 拼装外观的父实体已经摘掉模型了（见下）。
        self.eye = None
        self.skin_parts: list = []
        self.body = None

        if custom_model:
            # 自制模型自带材质，所以不盖光照着色器、也不加眼条
            # （眼条是按方块头的尺寸算的位置，换模型就没意义了）。
            pass
        else:
            # 父实体退化成纯碰撞盒，做法和 Player 一样：先按 model="cube" 让
            # super().__init__ 把 collider 拟合好，再把模型摘掉。这样射线、
            # 相机遮挡、玩家推挤全都不用改（碰撞盒仍是 0.9×1.8×0.9 这种尺寸）。
            self.model = None
            self.body = enemy_body.build(self, kind, scale, color_rgb)
            self.eye = self.body.eye
            self.skin_parts = self.body.parts

        # 脚下的接触阴影，半径跟着体型走（每种敌人的 scale 都不一样）。
        self.shadow = ground_shadow.attach(
            self, max(scale[0], scale[2]) * 0.55, config.ENEMY_SHADOW_ALPHA
        )

        # ---- M3 ----
        self.data = data
        self.speed_mult = speed_mult
        self.radius = data.radius if data is not None else config.PLAYER_RADIUS * 0.8
        self.brain = Brain(data) if data is not None else None
        # 感知结果由 EnemySystem 在 8Hz 闸门里刷新，这里只保存上一次的结论。
        # 放在实体上而不是 Brain 上：Brain.tick 每帧都会收到它，不该自己维护缓存。
        self.sees_player = False
        self.telegraphing = False

    # ---- 受击 ----
    def _set_flash(self, on: bool) -> None:
        """全身闪白 / 复原。

        外观是十几个零件拼的，父实体没有模型，改 `self.color` 一点用都没有。
        所以逐个零件改；复原时**各自复原自己的颜色** —— 统一涂回 `base_color`
        的话，装甲的明暗分层会当场消失，敌人变成一块纯色板。
        """
        self.color = color.white if on else self.base_color
        for part, base in self.skin_parts:
            part.color = color.white if on else color.rgb32(*base)

    def take_damage(self, amount: float) -> bool:
        """扣血。返回 True 表示这一击致死。"""
        if not self.alive:
            return False

        self.hp -= amount
        self._set_flash(True)
        self._flash = config.HIT_FLASH_TIME

        if self.hp <= 0:
            self.hp = 0.0
            self.alive = False
            # 立刻摘掉碰撞体：死亡动画期间不能再被射线打中，
            # 也不能作为一个「已经死了但还能挡枪」的障碍物。
            self.collider = None
            self._dying = config.DEATH_ANIM_TIME
            if self.brain is not None:
                self.brain.kill()
            event_bus.emit("enemy_died", enemy=self)
            return True
        return False

    # ---- 每帧表现（由 EnemySystem 调用，实体自己不实现 update）----
    def update_visual(self, dt: float) -> None:
        if self._flash > 0:
            self._flash -= dt
            if self._flash <= 0:
                self._set_flash(False)

        if self.alive:
            return

        if self._dying > 0:
            self._dying -= dt
            k = max(self._dying, 0.0) / config.DEATH_ANIM_TIME
            self.scale = self.base_scale * (0.05 + 0.95 * k)
            if self._dying <= 0:
                self.visible = False
                if self.respawn_delay > 0:
                    self._respawn = self.respawn_delay
                else:
                    self.removed = True
        elif self._respawn > 0:
            self._respawn -= dt
            if self._respawn <= 0:
                self.respawn()

    def respawn(self) -> None:
        self.hp = self.max_hp
        self.alive = True
        self.visible = True
        self.scale = Vec3(self.base_scale)
        self._set_flash(False)
        self.collider = "box"
        self._flash = 0.0
        self._dying = 0.0
        self._respawn = 0.0


class TargetDummy(Enemy):
    """M2 的静态靶子：站着不动，被打死后 3 秒原地复活，方便反复验收。"""

    def __init__(self, x: float, z: float) -> None:
        half_h = config.ENEMY_SCALE[1] / 2
        super().__init__(
            hp=config.TARGET_HP,
            position=(x, half_h, z),
            respawn_delay=config.TARGET_RESPAWN,
            name="target_dummy",
        )


def spawn_enemy(type_name: str, data: EnemyData, x: float, z: float,
                hp_mult: float = 1.0, speed_mult: float = 1.0,
                yaw: float = 0.0) -> Enemy:
    """按类型表造一个会动的敌人。

    血量倍率在**出生时**结算一次，而不是每次受击时算：
    波次难度写在血量上限里，玩家看到的血条长度才和实际耐久一致。

    `yaw` 是出生朝向。默认 0（朝 +Z）会让一整波怪都背对中心，
    必须由生成器传入「朝向玩家」的角度，否则它们要等扫视转过来才会发现玩家。
    """
    scale = data.scale
    enemy = Enemy(
        hp=data.hp * hp_mult,
        position=(x, scale[1] / 2, z),
        color_rgb=data.color_rgb,
        scale=scale,
        name=type_name,
        data=data,
        speed_mult=speed_mult,
    )
    enemy.rotation_y = yaw
    if enemy.brain is not None:
        enemy.brain.face_yaw = yaw
    return enemy
