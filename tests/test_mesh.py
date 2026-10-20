"""合并几何（`core/mesh_builder`）的回归约束。

这一层的错误**全部**是"不报错但画面坏了"的类型：
绕序反了 → 模型看着是里外翻转的；顶点色写错通道 → 面罩不发光；
索引算错 → Panda 直接抛 `references vertices up to N`（这个反而好查）。

所以这里只测四件在当前实现里最容易写错、又最难以肉眼发现的事：
顶点数、绕序与法线是否一致、包围盒、顶点色（含 alpha 自发光权重）。
"""
from __future__ import annotations

import math

import pytest

from src.core import mesh_builder


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def test_vertex_and_index_counts():
    """每个盒子 24 顶点 / 12 三角形，多一个少一个都会让索引错位。"""
    boxes = [
        ((1.0, 1.0, 1.0), (0.0, 0.0, 0.0), mesh_builder.rgba((255, 0, 0))),
        ((0.5, 2.0, 0.5), (3.0, 1.0, 0.0), mesh_builder.rgba((0, 255, 0))),
    ]
    mesh = mesh_builder.box_batch(boxes)

    assert len(mesh.vertices) == 2 * mesh_builder.VERTS_PER_BOX
    assert len(mesh.triangles) == 2 * mesh_builder.TRIS_PER_BOX * 3
    assert len(mesh.normals) == len(mesh.vertices)
    assert len(mesh.colors) == len(mesh.vertices)
    assert len(mesh.uvs) == len(mesh.vertices)


def test_winding_is_clockwise_because_engine_is_left_handed():
    """三角形的绕序必须按**左手系**来（顺时针 = 正面）。

    引擎启动时设了 `coordinate-system y-up-left`。左手系里用右手叉积算出来的
    方向与"正面"相反，所以这里断言 `dot(cross, normal) < 0`。

    写反的后果：六个面全变背面被剔除，看到的是盒子内壁 ——
    "柱子塌了一块，顶面斜着穿过去"，不报错、不掉帧，只有截图能发现。
    """
    mesh = mesh_builder.box_batch([((1.6, 0.8, 2.4), (0.3, -0.2, 0.5),
                                    mesh_builder.rgba((120, 140, 160)))])
    verts, tris, normals = mesh.vertices, mesh.triangles, mesh.normals

    for i in range(0, len(tris), 3):
        a, b, c = tris[i], tris[i + 1], tris[i + 2]
        cross = _cross(_sub(verts[b], verts[a]), _sub(verts[c], verts[a]))
        assert _dot(cross, normals[a]) < 0, (
            f"第 {i // 3} 个三角形绕序反了（左手系里正面是顺时针）")


def test_bounding_box_matches_input_size():
    """包围盒必须正好是零件尺寸 —— 大了会和相邻零件穿插，小了会露缝。"""
    size, center = (1.2, 0.4, 2.0), (5.0, -1.5, 3.0)
    mesh = mesh_builder.box_batch([(size, center, mesh_builder.rgba((255, 255, 255)))])
    verts = mesh.vertices

    for axis in range(3):
        values = [v[axis] for v in verts]
        assert math.isclose(min(values), center[axis] - size[axis] / 2, abs_tol=1e-6)
        assert math.isclose(max(values), center[axis] + size[axis] / 2, abs_tol=1e-6)


def test_vertex_color_carries_rgb_and_emissive():
    """顶点色 = 0~1 的 RGB + alpha 自发光权重。

    alpha 不是透明度：所有零件都不透明。它被 `lighting.vcolor_lit_shader`
    当作"完全不受光照"的比例，面罩和灯条靠它保持自己会亮。
    """
    rgb = (60, 220, 230)
    mesh = mesh_builder.box_batch([
        ((1, 1, 1), (0, 0, 0), mesh_builder.rgba(rgb, emissive=1.0)),
        ((1, 1, 1), (2, 0, 0), mesh_builder.rgba(rgb, emissive=0.0)),
    ])
    first = mesh.colors[0]
    second = mesh.colors[mesh_builder.VERTS_PER_BOX]

    assert pytest.approx(first[0]) == rgb[0] / 255
    assert pytest.approx(first[1]) == rgb[1] / 255
    assert pytest.approx(first[2]) == rgb[2] / 255
    assert first[3] == 1.0, "自发光件的 alpha 必须是 1"
    assert second[3] == 0.0, "普通零件的 alpha 必须是 0"


def test_manual_geom_builder_has_same_topology():
    """`model_from_boxes` 是绕开 ursina.Mesh 的备用路径，拓扑必须一致。

    两条路径的顶点数一旦不同，说明其中一个少写了面 —— 而它只在
    "ursina.Mesh 不可用"的极端情况才会被启用，平时没人会发现它坏了。
    """
    boxes = [((1, 2, 3), (0, 0, 0), mesh_builder.rgba((10, 20, 30))),
             ((0.5, 0.5, 0.5), (1, 1, 1), mesh_builder.rgba((40, 50, 60)))]
    mesh = mesh_builder.box_batch(boxes)
    node = mesh_builder.model_from_boxes(boxes)

    geom = node.get_geom(0)
    assert geom.get_vertex_data().get_num_rows() == len(mesh.vertices)
    assert geom.get_primitive(0).get_num_primitives() == len(mesh.triangles) // 3


def test_faces_lie_on_the_box_surface():
    """每个面的四个顶点必须正好落在盒子表面上。

    这是最容易漏、又最难查的一条：生成顶点时如果不把面沿法线推出半尺寸，
    六个面会全部塌在**盒子中心的那个平面**上，变成"交叉的薄片"。

    而此时包围盒量起来完全正确（每个轴都还有两个面在撑）、绕序也自洽、
    测试全绿 —— 只有截图能看出来：柱子会像半透明的重影。
    """
    size, center = (1.0, 2.0, 3.0), (0.0, 0.0, 0.0)
    mesh = mesh_builder.box_batch([(size, center, mesh_builder.rgba((255, 255, 255)))])
    half = (size[0] / 2, size[1] / 2, size[2] / 2)

    for i, vertex in enumerate(mesh.vertices):
        normal = mesh.normals[i]
        along = sum((vertex[a] - center[a]) * normal[a] for a in range(3))
        expect = sum(half[a] * abs(normal[a]) for a in range(3))
        assert math.isclose(along, expect, abs_tol=1e-6), (
            f"第 {i} 个顶点不在盒子表面上：沿法线 {along:.4f}，应为 {expect:.4f}")

        for axis in range(3):
            if normal[axis] == 0:
                assert abs(vertex[axis] - center[axis]) <= half[axis] + 1e-6
