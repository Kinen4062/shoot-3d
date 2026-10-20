"""把玩家现有的方块零件表导出成「建模起点」。

产出三样东西（默认写到 tools/_blockout/）：

1. `player_blockout.obj` —— 28 个方块的 OBJ。**直接拖进 Blender 就能用**，
   每个零件是一个独立 object（按零件名命名：chest / helm / visor …），
   位置和引擎里完全一致。这就是你的白模骨架。
2. `player_parts.csv` —— 零件名、尺寸、中心坐标，Excel / 记事本都能看。
3. `player_views.svg` —— 正交四视图（正 / 侧 / 背 / 顶），带 0.1 米网格和
   身高标注。这是给「三视图」用的：截图看不出比例，图纸可以。

用法：
    python tools/export_blockout.py

坐标约定（**已经实测**，见 tools/_probe_glb.py）：
    引擎和导出的 OBJ 都是 Y 轴朝上、+Z 是正面、+X 是右手边。
    Blender 导入 OBJ 用默认设置（Up=Y, Forward=-Z）就会自动变成
    Z 轴朝上、正面朝 -Y —— 也就是 Blender 里"标准站姿"的朝向。
    **不要去改那个导入设置**，改了模型就会躺下。
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import config                     # noqa: E402
from src.entities import player_body            # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_blockout")

# 方块 8 个角在单位立方体里的顺序：下 4 个 + 上 4 个。
# 面索引按**从外面看逆时针**给，这样 OBJ 的法线朝外；
# 给顺时针的话导进 Blender 会是一堆黑面（背面剔除）。
_CORNERS = (
    (-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1),
    (-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1),
)
_FACES = (
    (0, 3, 2, 1),   # 下
    (4, 5, 6, 7),   # 上
    (0, 1, 5, 4),   # -Z
    (2, 3, 7, 6),   # +Z
    (3, 0, 4, 7),   # -X
    (1, 2, 6, 5),   # +X
)


def box_vertices(size, center):
    """返回 8 个顶点。size 是**全尺寸**（不是半尺寸），和 PARTS 口径一致。"""
    sx, sy, sz = size
    cx, cy, cz = center
    return [
        (cx + dx * sx / 2, cy + dy * sy / 2, cz + dz * sz / 2)
        for dx, dy, dz in _CORNERS
    ]


def write_obj(path: str) -> None:
    """写一个只含方块和 group 的 OBJ。

    顶点序号是**全局递增**的：OBJ 规范里 `f` 用的是整个文件的顶点序号，
    每个 `o` 不会重置计数。手写成局部索引是这种"分物体导出"最常见的错，
    表现是导入后一堆三角形乱连。
    """
    lines = [
        "# tps_mini 玩家白模（tools/export_blockout.py 生成，别手改）",
        "# 单位：米。Y 轴朝上，+Z 是正面，原点在身体中心（脚底 = y -0.90）。",
        "",
    ]
    offset = 1   # OBJ 顶点序号从 1 开始

    for name, size, center, _key in player_body.PARTS:
        lines.append(f"o {name}")
        for vx, vy, vz in box_vertices(size, center):
            lines.append(f"v {vx:.5f} {vy:.5f} {vz:.5f}")
        for face in _FACES:
            lines.append("f " + " ".join(str(i + offset) for i in face))
        lines.append("")
        offset += 8

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


def write_csv(path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["part", "size_x", "size_y", "size_z",
                         "pos_x", "pos_y", "pos_z", "palette"])
        for name, size, center, key in player_body.PARTS:
            writer.writerow([name, *[f"{v:.3f}" for v in size],
                             *[f"{v:.3f}" for v in center], key])
        writer.writerow([])
        writer.writerow(["# 脚底 y =", f"{player_body.BODY_BOTTOM:.3f}",
                         "头顶 y =", f"{player_body.BODY_TOP:.3f}",
                         "总高 =", f"{player_body.BODY_TOP - player_body.BODY_BOTTOM:.3f}"])


# ----------------------------------------------------------------- 四视图 SVG

# 投影方向 -> 把世界 (x, y, z) 变成图纸的 (px, py)。
# 推导：相机在 +Z 看向 -Z、up = +Y 时，屏幕右 = +X、屏幕上 = +Y，
# 所以 FRONT 就是 (x, y)。其余三个同理（推导写在 M6_形象设计建议.md）。
PROJECTIONS = {
    "FRONT": lambda x, y, z: (x, y),      # 相机在 +Z，看到正面
    "LEFT": lambda x, y, z: (-z, y),      # 相机在 +X，正面朝图纸左侧
    "BACK": lambda x, y, z: (-x, y),      # 相机在 -Z
    "TOP": lambda x, y, z: (x, -z),       # 相机在上方，正面朝图纸下方
}

# 图纸布局（单位：米）。所有 y 换算只走 write_svg 里的 ty()，
# 避免出现"框按脚底算、零件按身体中心算"这种对不上的经典错误。
VIEW_W = 1.50
GAP = 0.30
PAD = 0.26
TOP = player_body.BODY_TOP + 0.14
BOT = player_body.BODY_BOTTOM - 0.26      # 底部留出标题位置


def _rect_in_view(size, center, project):
    """一个方块投影到某个视图后的轴对齐矩形 (x0, y0, x1, y1)。"""
    pts = [project(*c) for c in box_vertices(size, center)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _palette_rgb(key: str) -> tuple[int, int, int]:
    table = {
        "dark": config.PLAYER_ARMOR_DARK_RGB,
        "main": config.PLAYER_ARMOR_RGB,
        "limb": config.PLAYER_ARMOR_LIMB_RGB,
        "light": config.PLAYER_ARMOR_LIGHT_RGB,
        "visor": config.PLAYER_VISOR_RGB,
        "accent": config.PLAYER_ACCENT_RGB,
        "boot": config.PLAYER_BOOT_RGB,
    }
    return table.get(key, (128, 128, 128))


def write_svg(path: str) -> None:
    """正交四视图。深色底、0.1 米网格、身高标注。

    为什么是 SVG 而不是渲染出来的 PNG：图纸要能量、能打印、能标尺寸，
    截图做不到。而且 SVG 是纯文本，改一个数字 diff 出来就是那一行。
    """
    names = list(PROJECTIONS)
    total_w = PAD * 2 + len(names) * VIEW_W + (len(names) - 1) * GAP
    total_h = PAD * 2 + (TOP - BOT)

    def ty(world_y: float) -> float:
        """世界 y -> 图纸 y。全图只有这一个换算。"""
        return TOP - world_y

    px_per_m = 190
    W, H = int(total_w * px_per_m), int(total_h * px_per_m)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {total_w:.3f} {total_h:.3f}">',
        "<style>",
        "  text{font-family:Consolas,Menlo,monospace;fill:#c8d4e4}",
        "  .t{font-size:0.092px;letter-spacing:0.006px;fill:#9fb3cc}",
        "  .d{font-size:0.062px;fill:#8fa6c4}",
        "</style>",
        f'<rect width="{total_w:.3f}" height="{total_h:.3f}" fill="#12151c"/>',
    ]

    half = VIEW_W / 2

    for i, view in enumerate(names):
        # 每个视图的局部原点在框的左上角；局部 x 0..VIEW_W 对应世界 -half..+half
        ox = PAD + i * (VIEW_W + GAP)
        project = PROJECTIONS[view]

        out.append(f'<g transform="translate({ox:.4f},{PAD:.4f})">')
        out.append(f'<rect x="0" y="0" width="{VIEW_W:.3f}" '
                   f'height="{TOP - BOT:.4f}" fill="none" stroke="#252d3a" '
                   f'stroke-width="0.0035"/>')

        # 0.1 米网格
        step = -half
        while step <= half + 1e-9:
            lx = step + half
            out.append(f'<line x1="{lx:.4f}" y1="0" x2="{lx:.4f}" '
                       f'y2="{TOP - BOT:.4f}" stroke="#1c222c" stroke-width="0.0016"/>')
            step = round(step + 0.1, 4)

        wy = -1.0
        while wy <= 1.0001:
            out.append(f'<line x1="0" y1="{ty(wy):.4f}" x2="{VIEW_W:.3f}" '
                       f'y2="{ty(wy):.4f}" stroke="#1c222c" stroke-width="0.0016"/>')
            wy = round(wy + 0.1, 4)

        # 地面线（脚底）
        out.append(f'<line x1="0" y1="{ty(player_body.BODY_BOTTOM):.4f}" '
                   f'x2="{VIEW_W:.3f}" y2="{ty(player_body.BODY_BOTTOM):.4f}" '
                   f'stroke="#3b5570" stroke-width="0.005"/>')

        # 中心线（世界 x=0 / z=0 的投影）
        out.append(f'<line x1="{half:.4f}" y1="0" x2="{half:.4f}" '
                   f'y2="{TOP - BOT:.4f}" stroke="#243040" stroke-width="0.002"/>')

        # 零件：后画的盖前面的
        parts = list(player_body.PARTS)
        if view == "TOP":
            parts.sort(key=lambda p: p[2][1])          # 低的先画
        elif view == "FRONT":
            parts.sort(key=lambda p: p[2][2])          # 先画背后（z 小）
        elif view == "BACK":
            parts.sort(key=lambda p: -p[2][2])
        else:                                           # LEFT
            parts.sort(key=lambda p: -p[2][0])         # 先画远侧（x 小）

        for _name, size, center, key in parts:
            x0, y0, x1, y1 = _rect_in_view(size, center, project)
            r, g, b = _palette_rgb(key)
            glow = key in player_body.GLOW_KEYS
            out.append(
                f'<rect x="{x0 + half:.4f}" y="{ty(y1):.4f}" '
                f'width="{x1 - x0:.4f}" height="{y1 - y0:.4f}" '
                f'fill="#{r:02x}{g:02x}{b:02x}" fill-opacity="0.88" '
                f'stroke="{"#eaf3ff" if glow else "#0a0d12"}" '
                f'stroke-width="{0.0045 if glow else 0.0024}"/>'
            )

        out.append(f'<text class="t" x="{VIEW_W / 2:.3f}" y="{ty(BOT) + 0.11:.3f}" '
                   f'text-anchor="middle">{view}</text>')
        out.append("</g>")

    # 身高标注：第一个视图左侧
    h = player_body.BODY_TOP - player_body.BODY_BOTTOM
    ax = PAD - 0.10
    y_top, y_bot = ty(player_body.BODY_TOP), ty(player_body.BODY_BOTTOM)
    out.append(f'<line x1="{ax:.3f}" y1="{y_top:.4f}" x2="{ax:.3f}" '
               f'y2="{y_bot:.4f}" stroke="#5d86b8" stroke-width="0.004"/>')
    for yy in (y_top, y_bot):
        out.append(f'<line x1="{ax - 0.030:.3f}" y1="{yy:.4f}" '
                   f'x2="{ax + 0.030:.3f}" y2="{yy:.4f}" '
                   f'stroke="#5d86b8" stroke-width="0.004"/>')
    mid = (y_top + y_bot) / 2
    out.append(f'<text class="d" x="{ax - 0.040:.3f}" y="{mid:.4f}" text-anchor="middle" '
               f'transform="rotate(-90 {ax - 0.040:.3f} {mid:.4f})">{h:.2f} m</text>')

    out.append("</svg>")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out))


def shoulder_width() -> float:
    """肩宽是**跨度**不是零件尺寸：护肩中心在 ±0.35、自己宽 0.18，
    所以两肩外沿之间是 2 × (0.35 + 0.09) = 0.88。"""
    return 2.0 * max(abs(p[2][0]) + p[1][0] / 2
                     for p in player_body.PARTS
                     if p[0].startswith("shoulder"))


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    obj = os.path.join(OUT_DIR, "player_blockout.obj")
    csv_path = os.path.join(OUT_DIR, "player_parts.csv")
    svg = os.path.join(OUT_DIR, "player_views.svg")

    write_obj(obj)
    write_csv(csv_path)
    write_svg(svg)

    height = player_body.BODY_TOP - player_body.BODY_BOTTOM
    print(f"[ok] {len(player_body.PARTS)} 个零件")
    print(f"     身高 {height:.3f} m    肩宽(含护肩) {shoulder_width():.3f} m")
    for p in (obj, csv_path, svg):
        print(f"     {os.path.getsize(p):>7} bytes  {p}")


if __name__ == "__main__":
    main()
