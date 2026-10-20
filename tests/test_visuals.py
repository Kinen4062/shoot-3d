"""M6 画面观感的回归约束（规划书 §M6 第 8 条）。

外观是「改坏了不会报错、但一眼就看得出来」的典型：
少一个零件、影子变成椭圆、光照参数把最暗面压到 0.3、零件被父级缩放拉长 ——
这些都不会崩、不会掉帧，只是画面变难看，而"难看"很难在验收表里打勾。

所以这里把四类约定钉死：
1. **光照**：六面亮度的相对关系和取值范围（顶面最亮、底面最暗、最暗不低于 0.6）；
2. **零件表**：不超出身高、左右严格镜像、没有任何 collider；
3. **缩放补偿**：宿主的非等比缩放必须被 rig 完全抵消（否则零件被垂直拉伸）；
4. **阴影**：正圆、贴地、跟着宿主走。
"""
import math

import pytest
from panda3d.core import LPoint3
from ursina import Entity, color

from src.core import config, lighting, mesh_builder
from src.core.ray import raycast
from src.entities import ground_shadow, player_body
from src.entities.arena import Arena
from src.entities.enemy import Enemy
from src.entities.gun import Gun
from src.entities.player import Player


# ---------------------------------------------------------------- 光照

def test_top_face_is_brightest_and_bottom_is_darkest():
    """六面亮度必须拉开顺序，否则光照等于没开。

    这条一旦反过来（比如补光比主光强），柱子和墙会"底部发光"，
    看着像从地底下打光，比平涂更怪。
    """
    top = lighting.shade_for_normal(0, 1, 0)
    bottom = lighting.shade_for_normal(0, -1, 0)
    sides = [lighting.shade_for_normal(1, 0, 0), lighting.shade_for_normal(-1, 0, 0),
             lighting.shade_for_normal(0, 0, 1), lighting.shade_for_normal(0, 0, -1)]

    assert all(top > side > bottom for side in sides)
    assert len(set(round(s, 3) for s in sides)) >= 3, "四个侧面至少要能分出三档深浅"


def test_no_face_gets_crushed_to_black():
    """最暗面必须留在 0.6 以上。

    项目背景色是 (16,17,22)，墙面基色 (68,74,88) 乘 0.64 之后约 (44,47,56)，
    还能看出"这是墙"。乘 0.39（ursina 自带 basic_lighting 的 -X 面）就是 (27,29,34)，
    和背景几乎同色，柱子背面直接糊进黑里。
    """
    faces = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    shades = [lighting.shade_for_normal(*f) for f in faces]
    assert min(shades) >= 0.60
    assert max(shades) <= 1.0, "超过 1.0 会被 clamp，等于白费权重"


def test_shade_ignores_length_of_normal():
    """法线传进来是不是单位向量都要给同一个结果（着色器里也 normalize 了）。"""
    assert math.isclose(lighting.shade_for_normal(0, 1, 0),
                        lighting.shade_for_normal(0, 5.5, 0), rel_tol=1e-9)


def test_apply_lit_sets_shader_and_fills_missing_texture():
    """挂光照时必须补一张贴图。

    着色器要采样 `p3d_Texture0`，没贴图的实体在部分驱动上会采到未定义内容，
    表现是整块变黑 —— 这种 bug 只在别人的机器上出现，只能靠这条守住。
    """
    entity = Entity(model="cube")
    try:
        assert not entity.texture, "刚建出来的实体不该有贴图，否则这条测不到东西"
        lighting.apply_lit(entity)
        assert entity.shader is lighting.lit_shader
        assert entity.texture, "apply_lit 必须补一张纯白贴图"
    finally:
        from ursina import destroy
        destroy(entity)


def test_apply_lit_keeps_existing_texture():
    """已经有贴图时不能再覆盖。

    这里**不能**用 `texture="white_cube"` 建实体：`load_texture` 在没有窗口的
    环境里会走到 `TexturePool.loadTexture` 并直接崩掉（栈溢出，不是异常），
    而 pytest 就是那个环境。改用内存贴图，测的还是"不覆盖"这个行为。
    """
    entity = Entity(model="cube", texture=lighting._white_texture())
    try:
        before = entity.texture
        lighting.apply_lit(entity)
        assert entity.texture is before
    finally:
        from ursina import destroy
        destroy(entity)


def test_lighting_can_be_switched_off(monkeypatch):
    """config.LIT_SHADER = False 要能退回平涂（对比排查用）。"""
    monkeypatch.setattr(config, "LIT_SHADER", False)
    entity = Entity(model="cube")
    try:
        lighting.apply_lit(entity)
        assert entity.shader is not lighting.lit_shader
    finally:
        from ursina import destroy
        destroy(entity)


def test_blend_and_scale_rgb_clamp_to_byte_range():
    """颜色工具不能溢出：`rgb32` 收到 300 会变成乱码色。"""
    assert lighting.blend((0, 0, 0), (255, 255, 255), 0.5) == (128, 128, 128)
    assert lighting.blend((0, 0, 0), (255, 255, 255), -1.0) == (0, 0, 0)
    assert lighting.blend((0, 0, 0), (255, 255, 255), 9.0) == (255, 255, 255)
    assert lighting.scale_rgb((100, 100, 100), 5.0) == (255, 255, 255)
    assert lighting.scale_rgb((100, 100, 100), -1.0) == (0, 0, 0)


# ---------------------------------------------------------------- 玩家外观

@pytest.fixture
def player():
    p = Player()
    yield p
    from ursina import destroy
    destroy(p)


def test_body_has_all_parts(player):
    """36 个零件必须全在合并 mesh 里。

    合并之后 `body.children` 只剩 1 个 shell，所以改数顶点：
    每个盒子 24 个顶点，少一个零件就少 24 个。
    """
    assert len(player_body.PARTS) >= 20
    assert len(player.body.children) == 1, "外观应该只有一个可见实体"
    assert (len(player.body.shell.model.vertices)
            == len(player_body.PARTS) * mesh_builder.VERTS_PER_BOX)


def test_parts_are_mirrored(player):
    """左右成对的零件必须严格镜像。

    手抄位置最容易差 0.01，而这种不对称在镜头里会被"角色的重心"放大，
    看起来像歪着站。规则：`xxx_l` 必须有对应的 `xxx_r`，除了 x 取反全都一样。
    """
    by_name = {name: (size, pos, key) for name, size, pos, key in player_body.PARTS}
    pairs = [n for n in by_name if n.endswith("_l")]

    assert pairs, "没有成对的零件，这条测试等于没测"
    # 例外清单本身也要钉住：新增一对不对称的零件必须显式声明，
    # 否则"手滑把左边写成 0.20 右边写成 0.22"会被这条测试漏过去。
    skipped = {n[:-2] + "_r" for n in pairs} & set(player_body.ASYMMETRIC_PARTS)
    assert skipped == set(player_body.ASYMMETRIC_PARTS), "例外清单里有名字对不上零件表"

    for left in pairs:
        right = left[:-2] + "_r"
        assert right in by_name, f"{left} 没有对应的 {right}"
        if right in player_body.ASYMMETRIC_PARTS:
            continue

        l_size, l_pos, l_key = by_name[left]
        r_size, r_pos, r_key = by_name[right]
        assert l_size == r_size, f"{left}/{right} 尺寸不一致"
        assert l_key == r_key, f"{left}/{right} 配色不一致"
        assert math.isclose(l_pos[0], -r_pos[0], abs_tol=1e-9), f"{left}/{right} 的 x 不是镜像"
        assert math.isclose(l_pos[1], r_pos[1], abs_tol=1e-9), f"{left}/{right} 的 y 不一致"
        assert math.isclose(l_pos[2], r_pos[2], abs_tol=1e-9), f"{left}/{right} 的 z 不一致"


def test_parts_stay_inside_declared_height():
    """零件不能超出声明的高度范围（脚底 -0.9、头顶 0.925）。

    超出去的表现是「模型插进地板」或者「头顶穿进相机近平面」，
    而 collision box 还是 1.8 米，看起来像悬空/陷地。
    """
    lowest = min(pos[1] - size[1] / 2 for _, size, pos, _ in player_body.PARTS)
    highest = max(pos[1] + size[1] / 2 for _, size, pos, _ in player_body.PARTS)

    assert math.isclose(lowest, player_body.BODY_BOTTOM, abs_tol=0.005)
    assert math.isclose(highest, player_body.BODY_TOP, abs_tol=0.005)
    # 整身 1.8 米是碰撞盒高度，外观允许略微超出（盔沿/天线），但不能超过 5 厘米
    assert highest - config.PLAYER_HEIGHT / 2 <= 0.05


def test_body_parts_have_no_colliders(player):
    """零件绝对不能挂 collider。

    这是 M4 护盾外壳踩过的坑：子实体一旦有 collider，射线会停在它身上，
    表现是「贴着敌人开枪却没伤害」，而且完全不报错。
    """
    offenders = [c.name for c in player.body.children if c.collider]
    assert not offenders, f"这些零件挂了 collider：{offenders}"


def test_body_rig_cancels_host_scale(player):
    """rig 的世界缩放必须是 1，否则零件全被垂直拉长 1.8 倍。

    玩家实体的 scale 是 (0.7, 1.8, 0.7)，而零件表的单位是米 ——
    两者之间靠 rig 的倒数缩放衔接。
    """
    scale = player.body.world_scale
    assert math.isclose(scale[0], 1.0, abs_tol=1e-3)
    assert math.isclose(scale[1], 1.0, abs_tol=1e-3)
    assert math.isclose(scale[2], 1.0, abs_tol=1e-3)


def test_player_entity_is_now_only_a_collider(player):
    """父实体不渲染任何东西，但碰撞盒必须还在（尺寸不变）。

    外观交给子实体之后，玩家的 `model` 是 None；如果哪天有人顺手把
    `collider` 也一起清了，射线就打不中玩家 —— 而相机贴墙、柱子推开
    全都依赖这个盒体。
    """
    assert player.model is None
    assert player.collider is not None

    size = player.collider.size
    assert math.isclose(size.x * player.scale_x, config.PLAYER_WIDTH, abs_tol=1e-3)
    assert math.isclose(size.y * player.scale_y, config.PLAYER_HEIGHT, abs_tol=1e-3)
    assert math.isclose(size.z * player.scale_z, config.PLAYER_WIDTH, abs_tol=1e-3)


def test_glow_parts_are_marked_emissive(player):
    """面罩/灯条要"自己会亮"：靠顶点色 alpha=1 让着色器跳过光照。

    合并前这是"发光件不挂 lit_shader"，合并后整只模型只有一个着色器，
    所以改用 alpha 通道表达。写错的后果是面罩被面亮度乘暗，
    看起来和普通装甲一样 —— 朝向标识就没了。
    """
    assert player.body.shell.shader is lighting.vcolor_lit_shader

    glow_parts = sum(1 for _, _, _, key in player_body.PARTS
                     if key in player_body.GLOW_KEYS)
    emissive_boxes = sum(1 for c in player.body.shell.model.colors if c[3] > 0.5)

    assert glow_parts >= 2, "至少要有面罩和一个灯"
    assert emissive_boxes == glow_parts * mesh_builder.VERTS_PER_BOX


def test_visor_faces_forward(player):
    """朝向标识必须在 +Z 面且够亮 —— 没有它，一个方块转不转看不出来。

    合并之后面罩是零件数据里的一个盒子，所以读 `body.parts` 而不是子实体。
    """
    visor = next(p for p in player.body.parts if p.name == "visor")
    assert visor.center[2] > 0.1, "面罩得在脸的正前方"

    def luminance(rgb255):
        return 0.21 * rgb255[0] + 0.71 * rgb255[1] + 0.07 * rgb255[2]

    assert luminance(visor.rgb) > luminance(config.PLAYER_ARMOR_RGB) * 1.5, \
        "面罩要比装甲亮得多"
    assert visor.rgb[1] > 200, "面罩要偏青（绿通道高位）"


# ---------------------------------------------------------------- 脚下阴影

def test_shadow_scale_compensates_anisotropy():
    """圆片转 90° 后，局部 y 落在世界 z 上，吃的是宿主的 z 缩放。"""
    sx, sy = ground_shadow.shadow_scale(0.7, 1.4, 0.5)
    assert math.isclose(sx, 1.0 / 0.7, rel_tol=1e-6)
    assert math.isclose(sy, 1.0 / 1.4, rel_tol=1e-6)


def test_shadow_is_a_circle_on_the_ground(player):
    """量世界尺寸而不是看 scale：转了 90° 的东西 scale 说明不了形状。"""
    matrix = player.shadow.getNetTransform().getMat()
    points = [matrix.xformPoint(LPoint3(x, y, 0))
              for x, y in ((0.5, 0), (-0.5, 0), (0, 0.5), (0, -0.5))]

    extent_x = (points[0] - points[1]).length()
    extent_z = (points[2] - points[3]).length()

    assert math.isclose(extent_x, extent_z, rel_tol=1e-6), "影子不是正圆"
    assert math.isclose(extent_x, config.PLAYER_SHADOW_RADIUS * 2, rel_tol=1e-3)
    assert math.isclose(points[0].y, ground_shadow.LIFT, abs_tol=1e-6)


def test_shadow_follows_player_and_hides_with_it(player):
    player.position = (7, config.PLAYER_HEIGHT / 2, -3)
    assert math.isclose(player.shadow.world_x, 7, abs_tol=1e-6)
    assert math.isclose(player.shadow.world_z, -3, abs_tol=1e-6)

    player.visible = False
    assert player.shadow.is_hidden() is True
    player.visible = True


# ---------------------------------------------------------------- 场地与枪

@pytest.fixture
def arena():
    a = Arena()
    yield a
    from ursina import destroy
    for entity in (a.ground, a.static, *a.solids):
        destroy(entity)


def test_ground_has_grid_texture_and_stays_unlit(arena):
    """地面必须**不挂**光照着色器。

    自定义着色器读不到 Panda 的纹理矩阵，`texture_scale` 会失效，
    整块地面只剩一张没平铺的贴图（看起来像地板中央糊了一块）。
    地板本来就只有一个朝向，本来也不需要光照。
    """
    assert arena.ground.texture, "地面贴图没加载上"
    assert arena.ground.shader is not lighting.lit_shader
    assert arena.ground.texture_scale[0] == pytest.approx(
        config.ARENA_HALF * 2 / config.GROUND_GRID_CELL)


def test_static_scene_is_one_lit_mesh(arena):
    """墙 / 压条 / 柱子 / 盖板必须合并成**一次**绘制。

    M6 时它们是 36 个独立 Entity，也就是 36 次 draw call，而顶点加起来
    不到 900 个。合并之后同一个画面只花 1 次调用 —— 这是 M7 性能优化
    里最直接的一条，`tools/_probe_cost.py` 量得出来。

    这条同时钉住三件事：顶点数没有漏盒子、颜色走的是顶点色（不是
    `ColorScale`）、合并体**没有**碰撞（有的话子弹会在半空命中巨型外壳）。
    """
    boxes = 4 + 4 + len(arena.pillar_boxes) * 2      # 墙 + 压条 + (柱 + 盖板)
    assert len(arena.static.model.vertices) == boxes * mesh_builder.VERTS_PER_BOX
    assert len(arena.static.model.triangles) == boxes * mesh_builder.TRIS_PER_BOX * 3
    assert arena.static.shader is lighting.vcolor_lit_shader
    assert arena.static.collider is None, "合并体覆盖全场，挂碰撞会让子弹半空命中"


def test_collision_lives_on_invisible_proxies(arena):
    """视觉合并了，碰撞必须一点不少地留在不可见代理上。

    代理是 `visible=False` 的普通盒子：不产生绘制（cull 阶段就被丢掉），
    但 Panda 的碰撞遍历照样打得到。盖板/压条**不**建代理 —— 它们贴在半空，
    让镜头撞上去只会出现"莫名其妙被拉近"。
    """
    assert len(arena.solids) == 4 + len(arena.pillar_boxes)
    assert {e.name for e in arena.solids} == {"wall_n", "wall_s", "wall_e", "wall_w", "pillar"}
    assert all(e.collider is not None for e in arena.solids)
    assert all(e.visible is False for e in arena.solids), "代理绝不能被画出来"


def test_rays_still_hit_walls_and_pillars(arena):
    """合并不影响命中：子弹和相机遮挡都靠这些代理。

    墙的内表面在 27.5（墙中心 28，厚 1）。柱子的命中距离从外侧 2 米处
    打过去，误差留 0.15 —— 柱身宽度不等，射线可能擦到侧面。
    """
    hit = raycast((0, 1.0, 0), (1, 0, 0), 100)
    assert hit.entity is not None and hit.entity.name == "wall_e"
    assert math.isclose(hit.distance, config.ARENA_HALF - config.WALL_THICKNESS / 2, abs_tol=1e-3)

    x, z, r = arena.pillar_boxes[0]
    pillar_hit = raycast((x + r + 2, 1.0, z), (-1, 0, 0), 10)
    assert pillar_hit.entity is not None and pillar_hit.entity.name == "pillar"
    assert pillar_hit.distance < 2.5


def test_gun_has_silhouette_details():
    """枪要有枪管/弹匣/枪托，否则近景里就是一根方木。"""
    gun = Gun()
    try:
        names = {c.name for c in gun.entity.children}
        assert {"gun_barrel", "gun_magazine", "gun_stock", "gun_muzzle"} <= names
        assert gun.entity.shader is lighting.lit_shader
    finally:
        from ursina import destroy
        destroy(gun.entity)


# ---------------------------------------------------------------- 敌人

def test_enemy_gets_eye_and_shadow():
    """四种敌人共用的三条底线：有眼、有影子、身体有明暗。

    注意坐标要读**世界**坐标：外观挂在 rig 下，rig 内部是米制、
    父级 scale 已经被抵消掉了，所以 `eye.z` 只有 0.15 而不是 0.49。
    """
    enemy = Enemy(hp=10.0, position=(0, 0.9, 0))
    try:
        # 坐标读 rig 内部的米制：眼条中心 z 应该在头的正面
        assert enemy.eye.center[2] > 0.1, "眼条要在正面（+Z）"
        assert enemy.eye.rgb[1] / 255 > enemy.base_color[1], "眼睛要比身体亮"
        assert enemy.eye.emissive, "眼条必须标注成自发光，否则会被光照压暗"

        assert enemy.shadow is not None

        # 整只敌人只有一个可见实体，挂在顶点色光照上。
        # 发光件靠顶点 alpha=1 跳过光照，所以数发光顶点就知道标对没有。
        assert enemy.body.shell.shader is lighting.vcolor_lit_shader
        emissive_boxes = sum(1 for c in enemy.body.shell.model.colors if c[3] > 0.5)
        glow_parts = sum(1 for p in enemy.skin_parts if p.emissive)
        assert glow_parts >= 1
        assert emissive_boxes == glow_parts * mesh_builder.VERTS_PER_BOX
    finally:
        from ursina import destroy
        destroy(enemy)


def test_enemy_children_have_no_colliders():
    enemy = Enemy(hp=10.0, position=(0, 0.9, 0))
    try:
        assert all(c.collider is None for c in enemy.children)
    finally:
        from ursina import destroy
        destroy(enemy)


def test_enemy_eye_color_tracks_body_color():
    """不同兵种的眼光颜色必须跟着本体色走，否则四种敌人长得一样。"""
    red = Enemy(hp=10.0, position=(0, 0.9, 0), color_rgb=(220, 70, 60))
    blue = Enemy(hp=10.0, position=(0, 0.9, 0), color_rgb=(70, 120, 220))
    try:
        assert red.eye.rgb[0] > red.eye.rgb[2]
        assert blue.eye.rgb[2] > blue.eye.rgb[0]
    finally:
        from ursina import destroy
        destroy(red)
        destroy(blue)


# ------------------------------------------------------- 自制模型（M6 接口）

def _write_glb(path, size=(1.0, 2.0, 1.0)):
    """现造一个最小的 .glb 盒子（8 顶点 12 三角形，无材质）。

    **为什么测试里要自己造模型**：`config.PLAYER_MODEL` 这条路只有在
    "文件真的存在且能加载"时才有意义。用现成的素材会引入一个仓库里
    不该有的二进制文件，而真实模型的加载失败（路径写错、格式不对）
    恰恰是这条接口最容易出问题的地方。
    """
    import json
    import struct

    x, y, z = size
    corners = [(-x / 2, 0, -z / 2), (x / 2, 0, -z / 2), (x / 2, 0, z / 2), (-x / 2, 0, z / 2),
               (-x / 2, y, -z / 2), (x / 2, y, -z / 2), (x / 2, y, z / 2), (-x / 2, y, z / 2)]
    faces = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1),
             (1, 5, 6), (1, 6, 2), (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    indices = [i for face in faces for i in face]

    positions = b"".join(struct.pack("<3f", *c) for c in corners)
    index_bytes = b"".join(struct.pack("<H", i) for i in indices) + b"\x00\x00"
    binary = positions + index_bytes

    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(index_bytes),
             "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3",
             "min": [-x / 2, 0, -z / 2], "max": [x / 2, y, z / 2]},
            {"bufferView": 1, "componentType": 5123, "count": len(indices), "type": "SCALAR"},
        ],
    }

    js = json.dumps(document, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    body = struct.pack("<I", len(js)) + b"JSON" + js
    body += struct.pack("<I", len(binary)) + b"BIN\x00" + binary
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)


@pytest.fixture
def model_folder(tmp_path, monkeypatch):
    """造一个 glb 并让 load_model 找得到它。

    `load_model` 只搜 `application.asset_folder`，所以测试里给它指到临时目录 ——
    这也顺便验证了「模型路径必须是 asset_folder 下的相对名」这条约定。
    """
    from ursina import application

    _write_glb(tmp_path / "hero.glb")
    monkeypatch.setattr(application, "asset_folder", tmp_path)
    monkeypatch.setattr(config, "PLAYER_MODEL", "hero.glb")
    return tmp_path


def test_player_uses_custom_model_when_configured(model_folder):
    """填了 PLAYER_MODEL 就不再拼 26 个方块，只挂一个模型实体。

    rig 必须保留（它是"米"和宿主非等比缩放之间的换算层），
    否则自制模型会被拉成 1.8 倍高。
    """
    player = Player()
    try:
        assert len(player.body.children) == 1
        node = player.body.children[0]
        assert node.name == "player_model"
        assert node.model is not None

        scale = player.body.world_scale
        assert math.isclose(scale[0], 1.0, abs_tol=1e-3)
        assert math.isclose(scale[1], 1.0, abs_tol=1e-3)
        assert math.isclose(scale[2], 1.0, abs_tol=1e-3)
    finally:
        from ursina import destroy
        destroy(player)


def test_enemy_and_gun_take_custom_model(model_folder, monkeypatch):
    monkeypatch.setattr(config, "ENEMY_MODEL", "hero.glb")
    monkeypatch.setattr(config, "GUN_MODEL", "hero.glb")

    enemy = Enemy(hp=10.0, position=(0, 0.9, 0))
    gun = Gun()
    try:
        assert enemy.model is not None
        assert enemy.eye is None, "换模型后不该再挂按方盒算位置的眼条"
        assert enemy.shader is not lighting.lit_shader, "自制模型自带材质，不该再盖着色器"

        assert gun.entity.model is not None
        assert not hasattr(gun, "barrel"), "换模型后不该再拼细节零件"
        assert gun.muzzle is not None and gun.flash is not None
    finally:
        from ursina import destroy
        destroy(enemy)
        destroy(gun.entity)


def test_absolute_model_paths_do_not_load():
    """把绝对路径喂给 model= 会静默失败 —— 记住这条，别在 config 里用 resource_path。

    ursina 的 load_model 是拿 asset_folder 做 `glob('**/' + 名字)` 的，
    绝对路径（还带盘符和反斜杠）永远 glob 不到，只打一行 warning。
    打包后白屏/模型不显示的坑就在这儿。
    """
    entity = Entity(model=config.resource_path("assets/textures/icon.ico"))
    assert entity.model is None


# --------------------------------------------------- 按类型换模型（四种敌人）

def _enemy_data(kind: str = "grunt", scale=(0.9, 1.8, 0.9)):
    from src.core.enemy_types import EnemyData

    return EnemyData(
        name=kind, hp=60.0, speed=3.0, damage=10.0, attack_range=2.2,
        attack_cd=1.2, attack_windup=0.3, view_range=25.0, score=100,
        radius=0.5, color_rgb=(220, 70, 60), scale=scale,
    )


def _model_extent(model) -> float:
    """模型自身坐标系下的**最长边**。

    不取 y 高度：pytest 里没有 Ursina 实例，panda3d-gltf 在这条路径上
    不做 Y-up→Z-up 转换，1×2×1 的盒子量出来是 (1, 1, 2) —— 高度落在 Z 轴上，
    模型是"躺着"的。带引擎跑（main.py、tools/_probe_glb.py）时才正常。
    所以朝向只能靠 _probe_glb.py 验，这里只确认「几何真的读进来了」。
    """
    low, high = model.getTightBounds()
    size = high - low
    return max(size.x, size.y, size.z)


def test_enemy_models_can_differ_per_type(model_folder, monkeypatch):
    """每种敌人可以各用各的模型。

    `data.scale` 把 grunt 和 tank 拉开到 1.8 米 vs 2.4 米、宽度差一倍多，
    共用一个模型全靠拉伸会把细节全拉变形，所以留了按类型覆盖的口子。
    """
    _write_glb(model_folder / "tank.glb", size=(1.0, 3.0, 1.0))
    monkeypatch.setattr(config, "ENEMY_MODELS",
                        {"grunt": "hero.glb", "tank": "tank.glb"})
    monkeypatch.setattr(config, "ENEMY_MODEL", "")

    grunt = Enemy(hp=60.0, position=(0, 0.9, 0), data=_enemy_data("grunt"))
    tank = Enemy(hp=200.0, position=(0, 1.2, 0),
                 data=_enemy_data("tank", (1.3, 2.4, 1.3)))
    shooter = Enemy(hp=45.0, position=(0, 0.85, 0), data=_enemy_data("shooter"))
    try:
        # 「用到了哪个文件」直接读节点路径。比量尺寸可靠，
        # 也不依赖 glTF 朝向（pytest 里没引擎实例，模型是躺着的）。
        # 注意 ursina 的 cube 在内部也是 NodePath，所以只能比字符串。
        assert "hero.glb" in str(grunt.model)
        assert "tank.glb" in str(tank.model)

        # 再确认几何真的读进来了（1×2×1 与 1×3×1）
        assert math.isclose(_model_extent(grunt.model), 2.0, abs_tol=1e-3)
        assert math.isclose(_model_extent(tank.model), 3.0, abs_tol=1e-3)

        # 没配的类型回落到**代码拼装的外观**，而不是"顺手捡一个同族模型"。
        # 顺手回落是最坑的：看起来有模型，实际穿的是别人的皮。
        assert shooter.model is None, "拼装外观的父实体是纯碰撞盒"
        assert shooter.body is not None and len(shooter.skin_parts) >= 12

        assert grunt.eye is None and tank.eye is None
        assert shooter.eye is not None, "方块敌人仍然要靠眼条表达朝向"
    finally:
        from ursina import destroy
        for enemy in (grunt, tank, shooter):
            destroy(enemy)


def test_enemy_falls_back_to_shared_model(model_folder, monkeypatch):
    """只配了 ENEMY_MODEL 时，所有类型都用它（旧的单模型路径不能坏）。"""
    monkeypatch.setattr(config, "ENEMY_MODELS", {})
    monkeypatch.setattr(config, "ENEMY_MODEL", "hero.glb")

    enemy = Enemy(hp=60.0, position=(0, 0.9, 0), data=_enemy_data("tank"))
    try:
        assert "hero.glb" in str(enemy.model)
        assert math.isclose(_model_extent(enemy.model), 2.0, abs_tol=1e-3)
    finally:
        from ursina import destroy
        destroy(enemy)


# ------------------------------------------------ 白模导出（进 Blender 的起点）

def test_blockout_obj_matches_part_table(tmp_path):
    """白模的顶点 / 面 / 包围盒必须和零件表逐项一致。

    这个文件是「进 Blender 改模型」的起点，差一个数量级（比如把全尺寸
    当成半径用）在 Blender 里只表现为"模型大了一倍"，很难往坐标表上想。
    """
    from tools import export_blockout as eb

    path = tmp_path / "blockout.obj"
    eb.write_obj(str(path))

    verts, faces, objects = [], [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "o":
            objects.append(parts[1])
        elif parts[0] == "v":
            verts.append(tuple(float(v) for v in parts[1:4]))
        elif parts[0] == "f":
            faces.append([int(v) for v in parts[1:]])

    assert objects == [p[0] for p in player_body.PARTS]
    assert len(verts) == 8 * len(objects)
    assert len(faces) == 6 * len(objects)
    assert all(len(f) == 4 for f in faces), "方块的面给四边形，别拆成三角形"

    # 索引必须**全局递增**：每组从 1 重新数是这种分物体导出最常见的错，
    # 表现是导入后一堆面乱连。
    assert min(min(f) for f in faces) == 1
    assert max(max(f) for f in faces) == len(verts)

    ys = [v[1] for v in verts]
    assert math.isclose(min(ys), player_body.BODY_BOTTOM, abs_tol=1e-4)
    assert math.isclose(max(ys), player_body.BODY_TOP, abs_tol=1e-4)

    for i, (_name, size, center, _key) in enumerate(player_body.PARTS):
        box = verts[i * 8:(i + 1) * 8]
        for axis in range(3):
            values = [v[axis] for v in box]
            assert math.isclose(min(values), center[axis] - size[axis] / 2, abs_tol=1e-4)
            assert math.isclose(max(values), center[axis] + size[axis] / 2, abs_tol=1e-4)


def test_shoulder_width_is_a_span_not_a_part_size():
    """肩宽是两只护肩的**外沿跨度**，不是其中一个护肩的宽度。

    我第一版按 `max(size_x)` 算，得出 0.18 —— 差了一个数量级，
    而且这个数字是要写进建模文档给用户对比例用的。
    """
    from tools import export_blockout as eb

    assert math.isclose(eb.shoulder_width(), 0.88, abs_tol=1e-6)


def test_blockout_svg_is_valid_xml(tmp_path):
    """四视图必须是合法 XML —— 自己拼字符串最容易漏个引号或尖括号。"""
    import xml.etree.ElementTree as ET

    from tools import export_blockout as eb

    path = tmp_path / "views.svg"
    eb.write_svg(str(path))

    root = ET.parse(path).getroot()
    assert root.tag.endswith("svg")

    rects = [e for e in root.iter() if e.tag.endswith("rect")]
    # 每格 1 个视图边框 + 28 个零件，另加背景
    assert len(rects) == 1 + 4 * (1 + len(player_body.PARTS))


# ------------------------------------------------- 敌人外观（M6 四种体型）

def _squad():
    from src.core.enemy_types import FALLBACK_ENEMY_TYPES
    from src.entities.enemy import spawn_enemy
    return [(kind, data, spawn_enemy(kind, data, i * 3.0, 8.0, yaw=0))
            for i, (kind, data) in enumerate(FALLBACK_ENEMY_TYPES.items())]


def test_enemy_body_fits_its_hitbox():
    """外观要落在碰撞盒里：脚贴地、头顶≈身高。

    这条守的是「看着能打中、实际打空」以及「模型插进地板」两类问题。
    外观挂在 rig 下（米制），碰撞盒是父级的 scale，两边一旦对不上就会露馅。
    """
    from ursina import destroy

    squad = _squad()
    try:
        for kind, data, enemy in squad:
            # 零件数据是 rig 内部的米制，换算回世界要加上宿主的 y
            top = enemy.world_y + max(p.top for p in enemy.skin_parts)
            bottom = enemy.world_y + min(p.bottom for p in enemy.skin_parts)
            height = data.scale[1]

            assert abs(bottom) < 0.05, f"{kind} 的脚没贴地（{bottom:+.3f}）"
            assert abs(top - height) < 0.08, (
                f"{kind} 的头顶 {top:.2f} 和身高 {height:.2f} 差太多")
    finally:
        for _, _, enemy in squad:
            destroy(enemy)


def test_enemy_rig_cancels_parent_scale():
    """rig 的**世界**缩放必须是 1：内部才能当米用。

    这一条是敌人外观和玩家的关键差别 —— 敌人必须按各自体型现算零件，
    不能共用一张表再让父级拉伸（tank 的腿会被横向拉宽 1.44 倍）。
    """
    from ursina import destroy

    squad = _squad()
    try:
        for kind, _data, enemy in squad:
            scale = enemy.body.rig.world_scale
            for axis, value in zip("xyz", scale):
                assert math.isclose(value, 1.0, abs_tol=1e-4), (
                    f"{kind} 的 rig {axis} 缩放是 {value:.3f}，内部坐标不是米了")
    finally:
        for _, _, enemy in squad:
            destroy(enemy)


def test_enemy_flash_is_one_uniform_and_goes_back_to_zero():
    """受击闪白必须是一个 uniform，而且**必须能回到 0**。

    合并之后零件没有独立节点，能改的只有 shell 的 `flash` 输入。
    M6 时这里是"逐个零件改色、恢复时各自涂回原色"—— 那一圈最容易漏零件，
    表现是"打完怪身上有一块永远白着"。现在只有一个数，漏不掉。

    这条同时守住"装甲分层不会丢"：颜色全在顶点里，闪白只是着色器里
    往白色插值，恢复后顶点色原样还在。
    """
    from ursina import destroy

    enemy = Enemy(hp=999.0, position=(0, 0.9, 0), data=_enemy_data("grunt"))
    shell = enemy.body.shell
    try:
        assert float(shell.get_shader_input("flash")) == 0.0, "初始化就该是 0"

        # 顶点色本身要有分层，否则"恢复后颜色丢失"这条根本测不出来
        distinct = {tuple(c[:3]) for c in shell.model.colors}
        assert len(distinct) >= 4, "装甲要有分层"

        enemy.take_damage(1.0)
        assert float(shell.get_shader_input("flash")) == 1.0, "受击要整身穿白"

        enemy.update_visual(config.HIT_FLASH_TIME + 0.01)
        assert float(shell.get_shader_input("flash")) == 0.0, "闪完必须复原"
    finally:
        destroy(enemy)


def test_enemy_shapes_are_not_one_stretched_model():
    """每种体型必须真的不一样，而不是同一套比例乘上 scale。

    tank 的腿要短、runner 的腿要长。如果哪天有人把它们合并成一套零件表，
    这条会立刻失败 —— 那种回归在画面上表现为"敌人全都长得一样"，
    但不会有任何报错。

    BOSS 的腿也被设成重甲的短腿（比 tank 还短一点，因为它最高、最重）。
    所以这里断言的是"基础四种里 tank 最短"，BOSS 单独断言 —— 否则加一个
    重甲类型就会误报。
    """
    from src.entities import enemy_body

    legs = {name: shape.leg for name, shape in enemy_body.SHAPES.items()}
    features = {name: shape.feature for name, shape in enemy_body.SHAPES.items()}
    # 基础四种：既要拉开差距，又各自是极值。
    basic = {k: v for k, v in legs.items() if k != "liang"}

    assert max(basic.values()) - min(basic.values()) > 0.10, "腿长比例要拉得开"
    assert basic["runner"] == max(basic.values()), "runner 应该腿最长"
    assert basic["tank"] == min(basic.values()), "tank 应该腿最短"
    assert legs["liang"] <= basic["tank"], "BOSS 也是重甲，腿不该比 tank 长"
    # 记号不能撞车：撞了就等于两只敌人共用同一套辨识特征。
    assert len(set(features.values())) == len(features), "各类型的记号不能重复"


# ---------------------------------------------------------------- 窗口图标

def test_window_icon_is_os_specific_filename():
    """窗口图标必须用 `Filename.fromOsSpecific` 传，不能传字符串。

    `Ursina(icon="C:/.../icon.ico")` 最终落到
    `WindowProperties.setIconFilename(str)`，**即使文件真的存在**，
    Windows 窗口层也会打

        :display:windisplay(warning): Could not find icon filename ...

    然后退回 Panda3D 默认图标 —— 不崩、不报错，只是窗口顶上那个图标不对，
    属于"看到了也不一定会查"的类型。四组对照实测见 `tools/_probe_icon.py`。
    """
    from panda3d.core import Filename

    import main

    icon = main.window_icon()
    assert isinstance(icon, Filename), "返回字符串就会静默失效，必须是 Filename"
    assert icon.exists(), "assets/textures/icon.ico 得真的存在"
    assert icon.isFullyQualified(), "要给绝对路径，否则打包后 CWD 一变就找不到"
