"""场地：地面 + 四面边界墙 + 若干柱子。

柱子的作用是双重的：
1. 给玩家视觉参照物，否则纯色地面跑起来没有速度感；
2. 给相机碰撞提供遮挡物，M1 就能验证「贴墙/贴柱转镜头不穿模」。

地面必须**有** collider：相机压低俯仰时若不挡住，镜头会钻到地板下面去，
从背面看地面就是一片虚空。用 cube 而不是 plane，是为了拿到规整的盒体碰撞，
顶面正好在 y=0，玩家脚底贴地。

M6 的观感改动（第 8 条）：
- 墙和柱子挂上 `lighting.apply_lit`，四个侧面从此有明暗差（之前是全平涂）；
- 柱子顶端加一块浅色"盖板"，让柱子的高度和轮廓在远景里也读得出来；
- 地面铺 2 米网格贴图。**地面故意不挂光照着色器** —— 它靠 `texture_scale`
  平铺，而自定义着色器读不到 Panda 的纹理矩阵，网格会缩成一小块
  （详见 `core/lighting.py` 开头第 1 个坑）。地板是水平面，本来就只有一个面，
  光照对它没有意义，用默认着色器正好。
"""
from __future__ import annotations

import math
import random

from ursina import Entity, Texture, color

from src.core import config, lighting


class Arena:
    def __init__(self) -> None:
        self.half = config.ARENA_HALF
        self.inner = config.ARENA_INNER       # 墙面（玩家真正能站到的边界）
        size = self.half * 2
        thickness = config.GROUND_THICKNESS

        # 一格 GROUND_GRID_CELL 米：贴图在整块地面上重复这么多次。
        cells = size / config.GROUND_GRID_CELL

        # 注意这里给的是 **Texture 对象**而不是路径字符串。
        # `texture="<路径>"` 只会拿 asset_folder 去做 `glob('**/' + 名字)`，
        # 传绝对路径（resource_path 的结果）会 glob 不到，只打一行
        # "missing texture" 警告然后地板变成纯色 —— 打包后就是这个表现。
        # 自己构造 Texture 才能既走 resource_path（打包后指向 _MEIPASS）又不丢贴图。
        grid_texture = Texture(config.resource_path(config.GROUND_GRID_TEXTURE))

        self.ground = Entity(
            model="cube",
            scale=(size, thickness, size),
            position=(0, -thickness / 2, 0),
            color=color.white,        # 颜色已经烘进贴图，这里不能再乘一层
            texture=grid_texture,
            texture_scale=(cells, cells),
            collider="box",
            name="ground",
        )

        wall_color = color.rgb32(68, 74, 88)
        wall_h = config.WALL_HEIGHT
        wall_t = config.WALL_THICKNESS
        self.walls = [
            Entity(model="cube", color=wall_color, collider="box", name="wall_n",
                   scale=(size, wall_h, wall_t), position=(0, wall_h / 2, self.half)),
            Entity(model="cube", color=wall_color, collider="box", name="wall_s",
                   scale=(size, wall_h, wall_t), position=(0, wall_h / 2, -self.half)),
            Entity(model="cube", color=wall_color, collider="box", name="wall_e",
                   scale=(wall_t, wall_h, size), position=(self.half, wall_h / 2, 0)),
            Entity(model="cube", color=wall_color, collider="box", name="wall_w",
                   scale=(wall_t, wall_h, size), position=(-self.half, wall_h / 2, 0)),
        ]
        for wall in self.walls:
            lighting.apply_lit(wall)

        # 墙顶压条：一条 0.12 米高的浅色带子。没有它，墙和背景几乎同色，
        # 远处"哪儿是墙"只能靠轮廓猜。
        self.trims = []
        trim_color = color.rgb32(104, 112, 130)
        trim_h = 0.12
        for scale, position in (
            ((size, trim_h, wall_t), (0, wall_h + trim_h / 2, self.half)),
            ((size, trim_h, wall_t), (0, wall_h + trim_h / 2, -self.half)),
            ((wall_t, trim_h, size), (self.half, wall_h + trim_h / 2, 0)),
            ((wall_t, trim_h, size), (-self.half, wall_h + trim_h / 2, 0)),
        ):
            self.trims.append(lighting.apply_lit(Entity(
                model="cube", color=trim_color, scale=scale, position=position,
                name="wall_trim",
            )))

        self.pillars: list[Entity] = []
        self.caps: list[Entity] = []
        rng = random.Random(20261017)   # 固定种子：每次开局布局一致，方便对照调试
        # 亮度扰动用**独立的**随机流。写在主 rng 里会多消耗一次抽样，
        # 后面所有柱子的位置都会跟着挪 —— 纯外观改动不该改变可玩布局。
        tone_rng = random.Random(20261018)
        for _ in range(14):
            x = rng.uniform(-self.half + 4, self.half - 4)
            z = rng.uniform(-self.half + 4, self.half - 4)
            if abs(x) < 5 and abs(z) < 5:   # 别把出生点堵死
                continue
            h = rng.uniform(2.0, 4.5)
            w = rng.uniform(1.2, 2.5)
            d = rng.uniform(1.2, 2.5)
            # 每根柱子亮度略有不同（±6）。完全同色时一排柱子像贴了一张图，
            # 这个差别小到说不出原因，但"一眼看过去有远近了"。
            tone = tone_rng.randint(-6, 6)
            self.pillars.append(lighting.apply_lit(Entity(
                model="cube",
                color=color.rgb32(82 + tone, 86 + tone, 100 + tone),
                collider="box",
                scale=(w, h, d),
                position=(x, h / 2, z),
                name="pillar",
            )))
            cap_h = 0.14
            self.caps.append(lighting.apply_lit(Entity(
                model="cube",
                color=color.rgb32(108 + tone, 114 + tone, 132 + tone),
                scale=(w + 0.08, cap_h, d + 0.08),
                position=(x, h + cap_h / 2, z),
                name="pillar_cap",
            )))

        # 相机碰撞只认真正挡视线的实体。盖板和压条不加进来：
        # 它们贴在顶部，把镜头挡在半空反而更奇怪。
        self.solids = [*self.walls, *self.pillars]

    def clamp(self, entity: Entity, margin: float | None = None) -> None:
        """把实体限制在场地内。没有这一步，玩家会一路跑到虚空里。

        边界用 inner（墙的内表面）而不是 half（墙中心线），
        否则玩家会站进墙里半个身位，相机也就跟着贴进墙里。
        """
        if margin is None:
            margin = config.PLAYER_RADIUS
        limit = self.inner - margin
        x = max(-limit, min(limit, entity.x))
        z = max(-limit, min(limit, entity.z))
        if x != entity.x or z != entity.z:
            entity.position = (x, entity.y, z)

    def resolve_obstacles(self, entity: Entity, radius: float | None = None) -> None:
        """圆柱近似推开，防止走进柱子里。

        用平方距离先做粗筛，命中才开方 —— 柱子多起来后这个差别有意义。

        `radius` 可覆盖：玩家和敌人（tank 半径 0.8）体型不同，
        统一用 PLAYER_RADIUS 会让坦克半个身子插进柱子里。
        """
        r = config.PLAYER_RADIUS if radius is None else radius
        for p in self.pillars:
            pr = max(p.scale_x, p.scale_z) * 0.5
            min_d = pr + r
            dx = entity.x - p.x
            dz = entity.z - p.z
            d2 = dx * dx + dz * dz
            if d2 >= min_d * min_d or d2 < 1e-8:
                continue
            d = math.sqrt(d2)
            push = min_d - d
            entity.x += dx / d * push
            entity.z += dz / d * push
