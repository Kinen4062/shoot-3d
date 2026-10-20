"""打包后白屏的防线：资源路径与资源清单。

PyInstaller 打包后有两类问题都不会报错，只会"看起来坏了"：

1. **裸相对路径**：开发期从项目根启动时恰好能找到，打包后 CWD 变成 exe 所在
   目录，于是图标没换、数据读不到、贴图白掉。所有资源都必须经过
   `config.resource_path()`（它在打包后指向 `sys._MEIPASS`）。
2. **资源没被打包进去**：`--add-data` 漏了某个目录，或者代码引用了不存在的
   文件名。这类问题在开发机上永远复现不了，只能靠"清单对得上"来保证。

所以这里做两件事：
- 扫源码：除 `config.py` 外，任何代码里出现 `"assets/..."` / `"data/..."` 字面量都算违规
  （路径只能定义在 config，使用处必须 `resource_path(...)`）；
- 扫磁盘：config 里声明的每个资源、连同类游戏里会引用到的所有音效，都必须存在。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.core import config

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = (ROOT / "src" / "core" / "config.py").resolve()


def _string_literals(path: Path):
    """产出文件里所有"代码里的字符串常量"，跳过 docstring 和注释。

    docstring 里写 `assets/audio/*.wav` 是在解释背景，不该算违规，
    所以把 `Expr(Constant)` 这种"裸字符串语句"全部剔除。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue                    # 模块/类/函数 docstring
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node.lineno


def _project_sources():
    yield from sorted((ROOT / "src").rglob("*.py"))
    yield ROOT / "main.py"


@pytest.mark.parametrize("path", list(_project_sources()), ids=lambda p: p.name)
def test_no_raw_resource_paths_outside_config(path: Path):
    """资源路径只能在 `config.py` 里写，其余地方必须走 `resource_path`。"""
    if path.resolve() == CONFIG_PATH:
        return
    offenders = [
        (value, lineno)
        for value, lineno in _string_literals(path)
        if value.startswith(("assets/", "data/")) or value.startswith(("assets\\", "data\\"))
    ]
    assert not offenders, (
        f"{path.relative_to(ROOT)} 里出现裸资源路径 {offenders}；"
        "路径应定义在 config.py 并用 config.resource_path() 解析"
    )


def test_resource_path_is_absolute_and_inside_project():
    resolved = Path(config.resource_path("assets")).resolve()
    assert resolved.is_absolute()
    assert ROOT.resolve() in resolved.parents or resolved == ROOT.resolve()


@pytest.mark.parametrize("rel", [
    config.ICON,
    config.WEAPON_FILE,
    config.ENEMY_FILE,
    config.WAVE_FILE,
    config.BGM_FILE,
    config.GROUND_GRID_TEXTURE,
])
def test_declared_resource_files_exist(rel: str):
    """config 里点名的文件必须真的在磁盘上，否则打包时会被静默漏掉。"""
    target = Path(config.resource_path(rel))
    assert target.is_file(), f"缺少资源文件 {rel}（{target}）"


def test_all_sound_effects_exist():
    """`SOUND_VOLUMES` 里的每个键都要有对应 wav。

    少一个不会崩（`AudioSystem` 会静默降级），但玩家会听到"某类事件没声音"，
    而且只在打包后才发现 —— 开发机上 `tools/gen_sfx.py` 早就生成好了。
    """
    audio_dir = Path(config.resource_path(config.AUDIO_DIR))
    missing = [name for name in config.SOUND_VOLUMES if not (audio_dir / f"{name}.wav").is_file()]
    assert not missing, f"缺少音效 {missing}；跑 `python tools/gen_sfx.py` 可补齐"


def test_audio_dir_exists():
    assert Path(config.resource_path(config.AUDIO_DIR)).is_dir()


def test_no_absolute_user_paths_in_source():
    """源码里不能出现本机的绝对路径（比如我随手写进去的 C:\\Users\\...）。

    这类路径在开发机上跑得好好的，换台机器/打包后必挂，而且报错指向
    一个和功能无关的位置。存档目录除外（`SAVE_DIR` 用 %APPDATA% 拼出来）。
    """
    bad = []
    for path in _project_sources():
        for value, lineno in _string_literals(path):
            low = value.lower()
            if low.startswith(("c:\\", "c:/", "/users/", "/home/")) and "appdata" not in low:
                bad.append((str(path.relative_to(ROOT)), lineno, value))
    assert not bad, f"源码里有本机绝对路径：{bad}"


def test_scanner_actually_detects_raw_paths(tmp_path):
    """防止扫描器本身写坏了 —— 一个什么都不返回的扫描器会让上面全部假通过。"""
    bad = tmp_path / "bad.py"
    bad.write_text('AUDIO = "assets/audio/shoot.wav"\n', encoding="utf-8")
    found = [v for v, _ in _string_literals(bad) if v.startswith("assets/")]
    assert found == ["assets/audio/shoot.wav"]


def test_scanner_ignores_docstrings(tmp_path):
    """docstring 里提到 `assets/audio/*.wav` 是在解释背景，不该判违规。"""
    doc = tmp_path / "doc.py"
    doc.write_text('"""缺了 assets/audio/x.wav 会静默降级。"""\nA = 1\n', encoding="utf-8")
    assert [v for v, _ in _string_literals(doc) if v.startswith("assets/")] == []
