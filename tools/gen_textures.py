"""程序化生成 M6 的贴图（不依赖任何外部素材）。

**为什么需要一张网格贴图**

M6 第 8 条要解决「画面观感」。加了光照之后柱子有了明暗面，但**地面还是纯色**：
跑动时脚下没有任何参照，速度感全靠柱子，一跑到空地上就"感觉没在动"。
最便宜的办法是给地面铺一张 2 米一格的网格 —— 一格一格地过，快慢立刻看得出来。

**为什么自己画而不是找素材**

和 `gen_sfx.py` 同样的理由：外部素材有版权和体积问题，而这张图只有几百字节。
用标准库 `zlib` + `struct` 直接写 PNG，不需要 Pillow。

用法：
    python tools/gen_textures.py          # 已存在则跳过
    python tools/gen_textures.py --force  # 重新生成

尺寸 64×64、无 alpha（RGB）、可无缝平铺：线画在贴图**两条边上**，
拼接时两条边线并成一条，不会出现双线或断线。
"""
from __future__ import annotations

import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets", "textures")

SIZE = 64

# 颜色。地面本身很暗（窗口背景是 16,17,22），所以底色不能太黑，
# 否则网格线就是"暗底上的暗线"，等于没有。
# 但也不能太亮：地面是全场唯一没挂光照着色器的表面（见 arena.py），
# 底色一亮，它就会比被打亮的墙和柱子还抢眼，整个画面"下半截发白"。
BASE_RGB = (36, 39, 47)
LINE_RGB = (54, 60, 74)


def write_png(path: str, width: int, height: int, rows: list[list[tuple[int, int, int]]]) -> None:
    """写一张 8bit RGB PNG。rows[y][x] = (r, g, b)。"""
    raw = bytearray()
    for row in rows:
        raw.append(0)                      # 每行的 filter type：0 = None
        for r, g, b in row:
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")

    with open(path, "wb") as fh:
        fh.write(png)


def grid_rows(size: int = SIZE) -> list[list[tuple[int, int, int]]]:
    """网格：上边和左边各 2 像素线。

    只在两条边上画线，拼接时相邻的两条边并成一条，不会出现双线或断线。

    第一版还在每格正中间画了个小十字，本意是"远处缩小了也能看出格子的节奏"。
    离屏截图一看，那些十字在 6 米外活像地上的碎屑/污点，密度还很高 ——
    远处真正需要的是"线糊掉就糊掉"，而不是给画面撒噪点。删掉。
    """
    rows: list[list[tuple[int, int, int]]] = []
    for y in range(size):
        row: list[tuple[int, int, int]] = []
        for x in range(size):
            row.append(LINE_RGB if (x < 2 or y < 2) else BASE_RGB)
        rows.append(row)
    return rows


def main() -> int:
    force = "--force" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    target = os.path.join(OUT_DIR, "grid.png")

    if os.path.isfile(target) and not force:
        print(f"[skip] {target} 已存在（--force 可覆盖）")
        return 0

    write_png(target, SIZE, SIZE, grid_rows())
    print(f"[ok]   {target}  {SIZE}x{SIZE}  {os.path.getsize(target)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
