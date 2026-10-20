"""M0 单元测试：工程骨架自检。

把「目录齐不齐、包能不能导入、版本对不对」变成可重复执行的检查，
避免换机器 / 重装依赖后靠记忆验收。
"""
import importlib
from pathlib import Path

import ursina

ROOT = Path(__file__).resolve().parent.parent

PACKAGES = ["src", "src/core", "src/entities", "src/systems", "src/ui"]

MODULES = [
    "src.core.config",
    "src.core.clock",
    "src.core.event_bus",
    "src.core.game_state",
    "src.core.input_map",
    "src.entities.arena",
    "src.entities.player",
    "src.systems.camera_rig",
    "src.systems.player_control",
    "src.ui.debug_overlay",
]


def test_packages_have_init():
    """缺 __init__.py 的话 import src.xxx 会直接 ModuleNotFoundError。"""
    missing = [p for p in PACKAGES if not (ROOT / p / "__init__.py").is_file()]
    assert missing == []


def test_core_modules_importable():
    for name in MODULES:
        importlib.import_module(name)


def test_ursina_version():
    from importlib.metadata import version

    major, minor = (int(x) for x in version("ursina").split(".")[:2])
    assert (major, minor) >= (8, 3), f"ursina {version('ursina')} 过低，需要 >= 8.3"


def test_resource_path_returns_absolute():
    from src.core import config

    p = config.resource_path("assets")
    assert Path(p).is_absolute()
    assert Path(p).is_dir()


def test_save_path_not_in_project():
    """存档必须落在 %APPDATA%，不能写程序目录（装到 Program Files 后无权限）。"""
    from src.core import config

    assert str(ROOT) not in config.SAVE_FILE


def test_icon_exists():
    from src.core import config

    assert (ROOT / config.ICON).is_file()
