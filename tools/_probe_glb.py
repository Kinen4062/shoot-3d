"""量一下 .glb 进 Panda3D 之后到底被怎么摆（写文档前的事实核查）。

`docs/M6_建模指南.md` 里要告诉用户「模型要朝哪个方向、原点放哪、1 单位多长」。
这些如果只凭印象写，用户按着做出来是躺着的 / 大 100 倍的，返工成本极高。

所以这里直接造一个**已知尺寸、已知朝向**的最小 glb 丢给引擎，
把引擎吐回来的包围盒量出来：

    几何: x ∈ [-1, +1]（宽 2），y ∈ [0, +2]（高 2），z ∈ [-0.5, +0.5]（深 1）
          —— 在 glTF 的约定里 y 是"上"，所以这是一个"高 2 米"的盒子

如果加载后**高度仍然在 y 上**，说明 Y-up 被原样保留；
如果高度跑到 z 上，说明 panda3d-gltf 做了 Y-up → Z-up 的转换（也就是模型会躺下）。

用法：
    python tools/_probe_glb.py
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ursina import Entity, Ursina, application  # noqa: E402

app = Ursina(window_type="none")

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(OUT_DIR, exist_ok=True)
TARGET = os.path.join(OUT_DIR, "probe_box.glb")

# ---- 一个已知尺寸的盒子：8 顶点 12 三角形 ----
X, Y, Z = 1.0, 2.0, 1.0
CORNERS = [
    (-X / 2, 0.0, -Z / 2), (X / 2, 0.0, -Z / 2), (X / 2, 0.0, Z / 2), (-X / 2, 0.0, Z / 2),
    (-X / 2, Y, -Z / 2), (X / 2, Y, -Z / 2), (X / 2, Y, Z / 2), (-X / 2, Y, Z / 2),
]
FACES = [
    (0, 1, 2), (0, 2, 3),   # 底
    (4, 6, 5), (4, 7, 6),   # 顶
    (0, 4, 5), (0, 5, 1),
    (1, 5, 6), (1, 6, 2),
    (2, 6, 7), (2, 7, 3),
    (3, 7, 4), (3, 4, 0),
]
INDICES = [i for face in FACES for i in face]


def build_glb(path: str) -> None:
    pos_bytes = b"".join(struct.pack("<3f", *c) for c in CORNERS)
    idx_bytes = b"".join(struct.pack("<H", i) for i in INDICES)
    idx_bytes += b"\x00" * ((4 - len(idx_bytes) % 4) % 4)   # 对齐到 4 字节
    binary = pos_bytes + idx_bytes

    gltf = {
        "asset": {"version": "2.0", "generator": "tps_mini probe"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_bytes), "byteLength": len(idx_bytes), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(CORNERS), "type": "VEC3",
             "min": [-X / 2, 0.0, -Z / 2], "max": [X / 2, Y, Z / 2]},
            {"bufferView": 1, "componentType": 5123, "count": len(INDICES), "type": "SCALAR"},
        ],
    }

    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)

    body = struct.pack("<I", len(js)) + b"JSON" + js
    body += struct.pack("<I", len(binary)) + b"BIN\x00" + binary
    glb = struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body

    with open(path, "wb") as fh:
        fh.write(glb)


build_glb(TARGET)
print("[glb] ", TARGET, os.path.getsize(TARGET), "bytes")

# 走生产路径：ursina 的 load_model 只会在给定目录里 glob 文件名，
# 不接受绝对路径（和贴图是同一个坑，见 lighting.py / arena.py 的注释）。
from pathlib import Path  # noqa: E402

from ursina import load_model  # noqa: E402

node = load_model("probe_box.glb", folder=Path(OUT_DIR))
start, end = node.getTightBounds()
size = end - start
print()
print("glb 里写的尺寸      : x=%.3f  y=%.3f  z=%.3f   （y=2 是高度）" % (X, Y, Z))
print("引擎量到的包围盒尺寸: x=%.3f  y=%.3f  z=%.3f" % (size.x, size.y, size.z))
print("包围盒中心          : %.3f  %.3f  %.3f" % ((start.x + end.x) / 2,
                                                  (start.y + end.y) / 2,
                                                  (start.z + end.z) / 2))

axes = {"x": size.x, "y": size.y, "z": size.z}
tallest = max(axes, key=lambda k: axes[k])
print()
print("=> 高度落在 %s 轴上" % tallest)
print("=> Y-up 被保留" if tallest == "y" else "=> 被转换成了 Z-up（模型在 ursina 里是躺着的）")
print("=> 没有出现 100 倍缩放" if abs(size.y - Y) < 0.01 or abs(size.z - Y) < 0.01
      else "=> 尺寸被缩放过了，注意单位")

# 围绕质心放一个小方块，量 z 轴是不是被取反了（文档 §2-③ 的结论）
centre = (start + end) / 2
print("=> 包围盒中心 (%.3f, %.3f, %.3f)：y 不等于 0 说明 glTF 的\"底在 y=0\"被保留了"
      % (centre.x, centre.y, centre.z))

# 顺便验证两条路径的差别：相对名 + folder 能加载，绝对路径不行。
from ursina import application  # noqa: E402

application.asset_folder = Path(OUT_DIR)
relative = Entity(model="probe_box.glb")
absolute = Entity(model=str(Path(TARGET).resolve()))
print("=> Entity(model='probe_box.glb') 加载成功:", relative.model is not None)
print("=> Entity(model='<绝对路径>') 加载成功:", absolute.model is not None,
      "（model 只能是 asset_folder 下的相对名，见 M6_建模指南.md §6）")

