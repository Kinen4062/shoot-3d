"""击杀飘字的数学部分（`src/ui/score_popup.py` 的纯函数）。

飘字错位在实机上特别难查：帧率低的时候数字会出现在敌人身后、或者干脆
出现在屏幕另一侧（相机后方投影翻转）。这两类都不是"崩了"，是"看着有点怪"，
只能靠公式本身正确 + 边界断言来保。

对象池和 Text 的生命周期由 `tools/m5_probe.py` 在真实装配下验（要 engine）。
"""
from __future__ import annotations

import math

import pytest

from src.ui.score_popup import NEAR, popup_curve, project_ui

FOV = 40.0          # 水平视场角（ursina 的 camera.fov 是水平，见 ursina/camera.py:60）
ASPECT = 16 / 9


def test_center_of_screen_is_origin():
    """正前方 → ui (0, 0)。"""
    ux, uy = project_ui(0.0, 0.0, 10.0, FOV, ASPECT)
    assert ux == pytest.approx(0.0, abs=1e-9)
    assert uy == pytest.approx(0.0, abs=1e-9)


def test_horizontal_edge_matches_fov():
    """水平半角正切处（x/z = tan(fov/2)）必须落在屏幕右边缘 aspect/2。"""
    z = 10.0
    x = z * math.tan(math.radians(FOV) / 2)
    ux, _ = project_ui(x, 0.0, z, FOV, ASPECT)
    assert ux == pytest.approx(ASPECT / 2, rel=1e-6)


def test_vertical_edge_is_half_screen():
    """垂直方向的半屏距离 = aspect/tan 换算：y/z = tan(fov/2)/aspect 时到上边缘。"""
    z = 10.0
    y = z * math.tan(math.radians(FOV) / 2) / ASPECT
    _, uy = project_ui(0.0, y, z, FOV, ASPECT)
    assert uy == pytest.approx(0.5, rel=1e-6)


def test_signs_are_right():
    """右边 / 上边必须是正数 —— 写反了飘字会镜像。"""
    ux, _ = project_ui(1.0, 0.0, 10.0, FOV, ASPECT)
    _, uy = project_ui(0.0, 1.0, 10.0, FOV, ASPECT)
    assert ux > 0 and uy > 0


def test_behind_camera_returns_none():
    """相机后方不飘。不拦的话会在屏幕另一侧冒出一个翻转的 +100。"""
    assert project_ui(0.0, 0.0, -5.0, FOV, ASPECT) is None
    assert project_ui(0.0, 0.0, NEAR * 0.5, FOV, ASPECT) is None
    assert project_ui(0.0, 0.0, NEAR * 2, FOV, ASPECT) is not None


def test_narrower_fov_pushes_points_outward():
    """瞄准时 fov 从 40 收到 32，同一个点的屏幕偏移必须变大（放大效应）。"""
    far = project_ui(2.0, 0.0, 10.0, 40.0, ASPECT)[0]
    near = project_ui(2.0, 0.0, 10.0, 32.0, ASPECT)[0]
    assert near > far


def test_fov_zero_does_not_explode():
    """fov=0 会 tan(0)=0 除零。真机上 ursina 初始 fov 就是 0，必须兜住。"""
    ux, uy = project_ui(1.0, 1.0, 10.0, 0.0, ASPECT)
    assert math.isfinite(ux) and math.isfinite(uy)


# ---------------------------------------------------------------- 曲线

def test_curve_starts_visible_at_origin():
    rise, alpha = popup_curve(0.0, 0.9)
    assert rise == pytest.approx(0.0)
    assert alpha == pytest.approx(1.0)


def test_curve_holds_full_alpha_then_fades():
    _, a_during = popup_curve(0.9 * 0.3, 0.9)      # 30% < 35%，还该满亮
    _, a_half = popup_curve(0.9 * 0.5, 0.9)
    assert a_during == pytest.approx(1.0)
    assert 0.0 < a_half < 1.0


def test_curve_ends_invisible_and_risen():
    rise, alpha = popup_curve(0.9, 0.9)
    assert alpha == pytest.approx(0.0, abs=1e-9)
    assert rise == pytest.approx(1.0)


def test_curve_is_monotonic_and_clamped():
    """超过寿命不能变成负数 alpha（会变成"越飘越黑"的鬼影）。"""
    prev_rise = -1.0
    for i in range(20):
        rise, alpha = popup_curve(i * 0.1, 0.9)
        assert 0.0 <= alpha <= 1.0
        assert rise >= prev_rise
        prev_rise = rise
    assert popup_curve(5.0, 0.9) == (1.0, 0.0)


def test_curve_with_zero_life_is_immediately_gone():
    assert popup_curve(0.0, 0.0) == (1.0, 0.0)
