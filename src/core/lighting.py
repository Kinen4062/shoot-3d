"""方块光照：一个按面朝向着色的小着色器 + 几个配色工具。

为什么不用引擎自带的三条路
--------------------------
* `unlit_with_fog_shader`（ursina 默认）：纯色平涂。柱子的四个侧面、墙的正反面、
  地面和顶面全是一个颜色，看起来像贴图没加载 —— 这正是 M6 第 8 条要解决的问题。
* `lit_with_shadows_shader` + `DirectionalLight`：有真实投影，但每帧要多渲一张
  阴影贴图。30 个敌人 + 14 根柱子 + 4 面墙全开阴影，帧率直接掉一半，
  而规划书 §M6 明确要求「低于 40 帧就要优化」。
* `basic_lighting_shader`（引擎自带，也是按法线着色的）：方向是对的，
  但它把法线编码到 0~1 再算灰阶，最暗面只有 0.14、最亮面 0.85。
  暗色场景里柱子背面会黑成一团 —— 分辨不出"这是柱子"的话，
  光照反而比平涂更糟。

所以自己写一个：**主光 + 补光 + 56% 底光**，六面亮度如下（见 `shade_for_normal`）：

    顶面 0.95   左面 0.91   前面 0.76   后面 0.80   右面 0.71   底面 0.64

最暗也留 0.64，任何一块面都不会糊掉。代价是"光不会动"——
可本项目的光本来也不动，收益是零阴影开销、无光源实体、参数全在 config 里。

两个引擎坑（改这里之前务必看）
------------------------------
1. **`p3d_MultiTexCoord0` 不含 texture_scale。** Panda 的贴图平铺放在
   纹理矩阵里，自定义着色器不读 `p3d_TextureMatrix` 就吃不到 `texture_scale`。
   所以本项目的**地面（靠 texture_scale 平铺网格）继续用默认着色器**，
   `lighting` 只挂在"一张纯白贴图、不需要平铺"的方块上。
   哪天要给带平铺贴图的实体上光，先把纹理矩阵乘进 uv。
2. **着色器要采样 `p3d_Texture0`**，没贴图的实体在部分驱动上会采到未定义内容
   （表现是整块变黑）。`apply_lit` 会补一张 `white_cube`（纯白，乘 1 不改变颜色）。
"""
from __future__ import annotations

import math

from panda3d.core import TransparencyAttrib
from ursina import Shader

from src.core import config


# 着色器源码里所有数字都来自 config，避免出现第二套魔法参数。
# 唯一要小心的格式：必须是浮点字面量（`1` 在 GLSL 里是整型，`1 / 2` 会变成 0）。
def _f(value: float) -> str:
    return f"{float(value):.4f}"


def _vec3(rgb: tuple[float, float, float]) -> str:
    return "vec3(" + ", ".join(_f(v) for v in rgb) + ")"


_VERTEX = """
#version 140

uniform mat4 p3d_ModelViewProjectionMatrix;
uniform mat4 p3d_ModelMatrix;

in vec4 p3d_Vertex;
in vec2 p3d_MultiTexCoord0;
in vec3 p3d_Normal;

out vec2 texcoord;
out vec3 world_normal;

void main() {
    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;
    texcoord = p3d_MultiTexCoord0;
    // 世界法线而不是视图法线：光照方向和物体怎么转无关，
    // 于是同一段着色器可以贴在任何位置、任何朝向的方块上。
    world_normal = normalize(mat3(p3d_ModelMatrix) * p3d_Normal);
}
"""

_FRAGMENT = f"""
#version 140

uniform sampler2D p3d_Texture0;
uniform vec4 p3d_ColorScale;

in vec2 texcoord;
in vec3 world_normal;

out vec4 fragColor;

void main() {{
    vec3 n = normalize(world_normal);
    float key  = max(dot(n, normalize({_vec3(config.LIGHT_KEY_DIR)})), 0.0);
    float fill = max(dot(n, normalize({_vec3(config.LIGHT_FILL_DIR)})), 0.0);
    float shade = ({_f(config.LIGHT_AMBIENT)}
                   + {_f(config.LIGHT_KEY_WEIGHT)} * key
                   + {_f(config.LIGHT_FILL_WEIGHT)} * fill) * {_f(config.LIGHT_GAIN)};

    vec4 tex = texture(p3d_Texture0, texcoord);
    // 颜色(带 alpha) × 贴图 × 亮度。alpha 走 ColorScale，护盾那类半透明才能用。
    fragColor = vec4(tex.rgb * p3d_ColorScale.rgb * shade,
                     tex.a * p3d_ColorScale.a);
}}
"""


# 模块导入时就建好：`Shader.__init__` 不做编译（编译发生在第一次赋给实体时），
# 所以无窗口环境（测试、探针）导入本模块是安全的。
lit_shader = Shader(
    name="wrap_light_shader",
    language=Shader.GLSL,
    vertex=_VERTEX,
    fragment=_FRAGMENT,
)


# ---------------------------------------------------------------- 顶点色版本
#
# 合并几何之后，颜色不再是「每个实体一个 `p3d_ColorScale`」，而是每个顶点自带。
# 所以这一版多两件事：
#   1. 读顶点色 `p3d_Color`，alpha 当**自发光权重**（面罩/灯条不能被光照压暗）；
#   2. 多一个 `flash` uniform，受击时整体向白色插值 —— 这就是原来
#      「逐个零件改色再各自复原」做到的视觉效果，成本从 N 次赋值降到 1 次。
#
# **这个 shader 只能给带顶点色的 mesh 用**：没有 color 列时 `p3d_Color` 绑不上，
# 轻则整块变黑，重则着色器链接失败（画面全糊）。所以它和 `lit_shader` 是两个
# 独立的 Shader 对象，不能合并成一个。

_VC_VERTEX = """
#version 140

uniform mat4 p3d_ModelViewProjectionMatrix;
uniform mat4 p3d_ModelMatrix;

in vec4 p3d_Vertex;
in vec2 p3d_MultiTexCoord0;
in vec3 p3d_Normal;
in vec4 p3d_Color;

out vec2 texcoord;
out vec3 world_normal;
out vec4 vcolor;

void main() {
    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;
    texcoord = p3d_MultiTexCoord0;
    world_normal = normalize(mat3(p3d_ModelMatrix) * p3d_Normal);
    vcolor = p3d_Color;
}
"""

_VC_FRAGMENT = f"""
#version 140

uniform sampler2D p3d_Texture0;
uniform vec4 p3d_ColorScale;
uniform float flash;

in vec2 texcoord;
in vec3 world_normal;
in vec4 vcolor;

out vec4 fragColor;

void main() {{
    vec3 n = normalize(world_normal);
    float key  = max(dot(n, normalize({_vec3(config.LIGHT_KEY_DIR)})), 0.0);
    float fill = max(dot(n, normalize({_vec3(config.LIGHT_FILL_DIR)})), 0.0);
    float shade = ({_f(config.LIGHT_AMBIENT)}
                   + {_f(config.LIGHT_KEY_WEIGHT)} * key
                   + {_f(config.LIGHT_FILL_WEIGHT)} * fill) * {_f(config.LIGHT_GAIN)};

    // alpha = 自发光权重：1 的零件完全不受光照（面罩、灯条、眼睛）
    float emissive = clamp(vcolor.a, 0.0, 1.0);
    float light = mix(shade, 1.0, emissive);

    // 受击闪白：整体向纯白插值。闪的是"整身"，不是某个零件。
    vec3 base = mix(vcolor.rgb, vec3(1.0), clamp(flash, 0.0, 1.0));

    vec4 tex = texture(p3d_Texture0, texcoord);
    fragColor = vec4(tex.rgb * base * p3d_ColorScale.rgb * light,
                     tex.a * p3d_ColorScale.a);
}}
"""

vcolor_lit_shader = Shader(
    name="wrap_light_vcolor_shader",
    language=Shader.GLSL,
    vertex=_VC_VERTEX,
    fragment=_VC_FRAGMENT,
)



def shade_for_normal(nx: float, ny: float, nz: float) -> float:
    """`_FRAGMENT` 里那三行的 Python 镜像。

    存在的意义不是给运行时用，而是**把着色器的参数钉死**：
    参数是三个权重，改动之后画面会整体变亮或变暗，肉眼很难判断"暗了多少"。
    有了这个函数，测试可以直接断言「顶面最亮、底面最暗、最暗不低于 0.6」。
    """
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-9:
        return config.LIGHT_AMBIENT * config.LIGHT_GAIN
    nx, ny, nz = nx / length, ny / length, nz / length

    def facing(direction) -> float:
        dx, dy, dz = direction
        dl = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dl < 1e-9:
            return 0.0
        return max((nx * dx + ny * dy + nz * dz) / dl, 0.0)

    shade = (config.LIGHT_AMBIENT
             + config.LIGHT_KEY_WEIGHT * facing(config.LIGHT_KEY_DIR)
             + config.LIGHT_FILL_WEIGHT * facing(config.LIGHT_FILL_DIR))
    return shade * config.LIGHT_GAIN


def blend(a_rgb, b_rgb, t: float) -> tuple[int, int, int]:
    """两色线性插值，返回 0~255 整数。敌人眼睛的颜色就是从本体色往白里混出来的。"""
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return tuple(
        round(a + (b - a) * t) for a, b in zip(a_rgb, b_rgb, strict=True)
    )


def scale_rgb(rgb, k: float) -> tuple[int, int, int]:
    """整体提亮/压暗，自动夹到 0~255（int 超过 255 传给 rgb32 会溢出成乱码色）。"""
    return tuple(max(0, min(255, round(c * k))) for c in rgb)


def _white_texture():
    """纯内存的 2×2 白贴图（模块级单例）。

    为什么要自己搓一张，而不是 `texture="white_cube"`：
    `load_texture()` 在没有窗口 / 没有 `base` 的环境里会**栈溢出**。
    注意是栈溢出，不是抛异常 —— `apply_lit` 外面那层 `try/except` 拦不住它，
    进程直接死掉。无窗口探针（`tools/m5_probe.py`）和一部分单测正好在那个环境里，
    所以贴图必须走「只碰内存」这条路。

    纯白、无渐变：着色器里是 `tex.rgb * ColorScale * shade`，
    乘 1 不改变颜色，和 `white_cube` 在视觉上完全等价。
    """
    global _WHITE_TEX
    if _WHITE_TEX is not None:
        return _WHITE_TEX

    from panda3d.core import PNMImage
    from panda3d.core import Texture as PandaTexture
    from ursina import Texture

    image = PNMImage(2, 2)
    image.fill(1.0, 1.0, 1.0)
    texture = PandaTexture("lit_white")
    texture.load(image)
    _WHITE_TEX = Texture(texture)
    return _WHITE_TEX


_WHITE_TEX = None


def apply_lit(entity, fill_texture: bool = True):
    """给实体挂上方块光照，返回实体本身（方便链式调用）。

    `fill_texture=False` 用于「实体已经有贴图」的场合；默认补一张纯白，
    见 `_white_texture` 的注释（这条路径**不能**走磁盘）。

    失败时静默退化成平涂：这台机器上着色器编不过，也不该让游戏起不来。
    """
    if not config.LIT_SHADER or entity is None:
        return entity

    try:
        # 注意用真假判断而不是 `is None`：未设贴图时 ursina 给的是空字符串，
        # `is None` 判不出来，着色器就会去采样一个不存在的 p3d_Texture0。
        if fill_texture and not entity.texture:
            entity.texture = _white_texture()
        entity.shader = lit_shader
    except Exception:      # noqa: BLE001 - 只在无 GL 的极端环境触发
        pass
    return entity


def apply_vcolor_lit(entity, fill_texture: bool = True):
    """给**带顶点色**的合并 mesh 挂光照（`mesh_builder.box_batch` 的产物）。

    和 `apply_lit` 的区别：颜色来自顶点，且多一个 `flash` uniform。
    同样会补一张纯白贴图 —— 着色器要采样 `p3d_Texture0`。
    """
    if not config.LIT_SHADER or entity is None:
        return entity

    try:
        if fill_texture and not entity.texture:
            entity.texture = _white_texture()
        entity.shader = vcolor_lit_shader
        # ursina 在 `model` setter 里给**所有**模型都设了 `M_dual`（半透明混合）。
        # 合并 mesh 的顶点 alpha 是"自发光权重"、普通零件正好是 0，
        # 于是 Panda 会把整块几何丢进透明队列：混合 + 排序错乱，
        # 表现是柱子底部出现穿帮的横条、面与面互相透视。
        # 这里强制不透明；alpha 只给着色器读，不参与混合。
        entity.set_transparency(TransparencyAttrib.M_none)
        # 显式给个 0：GL 里未设置的 uniform 默认就是 0，但探针/测试要读它，
        # 有值才能区分"没闪"和"根本没设过"。
        entity.set_shader_input("flash", 0.0)
    except Exception:      # noqa: BLE001 - ursina 内部抛什么都有可能
        pass
    return entity


def set_flash(entity, amount: float) -> None:
    """受击闪白。`amount` 0~1，1 = 整身纯白。

    合并几何之前这里是"遍历每个零件改色、再各自复原"：
    N 个零件就是 N 次 `entity.color = ...`，而且恢复时很容易漏掉某一个，
    表现是"打完怪身上有一块永远白着"。现在只有一个 uniform。
    """
    if entity is None:
        return
    try:
        entity.set_shader_input("flash", max(0.0, min(1.0, float(amount))))
    except Exception:      # noqa: BLE001 - ursina 内部抛什么都有可能
        pass

