"""分级日志：级别解析 / 一键静音 / 文件轮转 / 流不绑死。

这一层的价值全在"发布版能不能一键静音"上，所以重点测三件事：
1. `MINITPS_LOG` 的每一档都解析得对，写错值不能炸；
2. `OFF` 真的什么都不输出（这是"发布版静音"的唯一承诺）；
3. 日志文件不可写、流被关掉时**不能把主循环带下去**。
"""
from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

import pytest

from src.core import log as log_mod

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def fresh_log():
    """把日志模块和根 logger 恢复到"从未配置"的状态，用例结束再还原。

    `configure()` 是幂等的、并且会往根 logger 上挂 handler —— 不隔离的话
    用例顺序一变结论就变（典型症状：轮转用例在别处跑过之后就再也挂不上
    文件 handler 了）。
    """
    root = logging.getLogger(log_mod.ROOT_NAME)
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_configured = log_mod._configured
    saved_path = log_mod._file_path

    root.handlers.clear()
    log_mod._configured = False
    log_mod._file_path = None
    try:
        yield root
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                handler.close()
        root.handlers.clear()
        root.handlers.extend(saved_handlers)
        root.setLevel(saved_level)
        log_mod._configured = saved_configured
        log_mod._file_path = saved_path


# ---------------------------------------------------------------- 级别解析

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("WARN", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("OFF", logging.CRITICAL + 10),
    ],
)
def test_every_tier_resolves(raw, expected):
    assert log_mod.resolve_level(raw) == expected


@pytest.mark.parametrize("raw", ["  debug  ", "Debug", "dEbUg"])
def test_level_text_is_normalised(raw):
    assert log_mod.resolve_level(raw) == logging.DEBUG


@pytest.mark.parametrize("raw", ["LOUD", "", "1", "CRITICAL"])
def test_unknown_level_falls_back_to_default(raw):
    """写错环境变量不能崩，也不能悄悄变成"全静音"以外的怪东西。"""
    assert log_mod.resolve_level(raw) == log_mod._LEVELS[log_mod.default_level()]


def test_off_sits_above_every_real_level():
    """`OFF` 必须比 CRITICAL 还高，否则 `log.critical` 还能漏出来。"""
    assert log_mod._LEVELS["OFF"] > logging.CRITICAL


def test_default_level_follows_frozen_flag(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert log_mod.default_level() == log_mod.DEV_LEVEL

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert log_mod.default_level() == log_mod.RELEASE_LEVEL


def test_env_var_is_the_switch(monkeypatch):
    monkeypatch.setenv(log_mod.ENV_VAR, "error")
    assert log_mod.resolve_level() == logging.ERROR
    monkeypatch.setenv(log_mod.ENV_VAR, "off")
    assert log_mod.resolve_level() == log_mod._LEVELS["OFF"]


# ---------------------------------------------------------------- 一键静音

def test_off_really_silences_everything(fresh_log, capsys):
    log_mod.configure("OFF", path="")
    logger = log_mod.get_logger("test")
    logger.debug("d")
    logger.info("i")
    logger.warning("w")
    logger.error("e")
    logger.critical("c")
    assert capsys.readouterr().out == ""


def test_info_keeps_warnings_but_drops_debug(fresh_log, capsys):
    log_mod.configure("INFO", path="")
    logger = log_mod.get_logger("test")
    logger.debug("不该出现")
    logger.warning("该出现")
    out = capsys.readouterr().out
    assert "该出现" in out
    assert "不该出现" not in out


def test_set_level_switches_at_runtime(fresh_log, capsys):
    log_mod.configure("DEBUG", path="")
    logger = log_mod.get_logger("test")
    log_mod.set_level("ERROR")
    logger.info("被压掉")
    logger.error("留下来")
    out = capsys.readouterr().out
    assert "留下来" in out
    assert "被压掉" not in out


def test_child_loggers_inherit_the_root_level(fresh_log):
    """子 logger 不设自己的级别，改根级别才能一次全生效。"""
    log_mod.configure("INFO", path="")
    child = log_mod.get_logger("weapon")
    assert child.name == f"{log_mod.ROOT_NAME}.weapon"
    assert child.level == logging.NOTSET
    assert child.getEffectiveLevel() == logging.INFO


# ---------------------------------------------------------------- 文件落盘

def test_writes_to_file_and_rotates(fresh_log, tmp_path, monkeypatch):
    # 把阈值压小：真按 1 MB 写，用例要几秒。
    monkeypatch.setattr(log_mod, "LOG_MAX_BYTES", 512)
    monkeypatch.setattr(log_mod, "LOG_BACKUPS", 1)

    log_mod.configure("DEBUG", path=str(tmp_path))
    target = log_mod.file_path()
    assert target is not None and target.endswith(log_mod.LOG_FILE_NAME)

    logger = log_mod.get_logger("file")
    for i in range(40):
        logger.info("x" * 80 + f" {i}")

    assert Path(target).exists()
    assert Path(target + ".1").exists(), "超过阈值必须轮转出 .1"


def test_file_line_carries_time_level_and_module(fresh_log, tmp_path):
    log_mod.configure("INFO", path=str(tmp_path))
    log_mod.get_logger("wave").warning("第三十波")
    text = Path(log_mod.file_path()).read_text(encoding="utf-8")
    line = [row for row in text.splitlines() if "第三十波" in row][-1]
    assert "WARNING" in line
    assert "minitps.wave" in line
    # 行首是 asctime，形如 2026-10-20 04:05:27,123
    assert line[:4].isdigit() and line[4] == "-"


def test_empty_path_means_no_file(fresh_log):
    """CI / 探针里不需要落盘，`path=""` 就该什么都不写。"""
    log_mod.configure("INFO", path="")
    assert log_mod.file_path() is None


def test_unwritable_path_does_not_raise(fresh_log, tmp_path, capsys):
    """只读目录 / 权限受限的机器上，游戏照样要能玩。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("我是文件，不是目录", encoding="utf-8")
    # 往"文件"底下写目录，makedirs 会抛 NotADirectoryError（OSError 的子类）
    level = log_mod.configure("INFO", path=str(blocker / "log.txt"))
    assert level == logging.INFO
    assert log_mod.file_path() is None
    assert "不可写" in capsys.readouterr().out


def test_configure_is_idempotent(fresh_log, tmp_path):
    """`get_logger` 每次都检查是否已配置，重复配置不能把 handler 叠起来。"""
    log_mod.configure("INFO", path=str(tmp_path))
    count = len(fresh_log.handlers)
    log_mod.configure("ERROR", path=str(tmp_path))
    assert len(fresh_log.handlers) == count
    assert fresh_log.level == logging.ERROR


def test_log_dir_is_under_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert Path(log_mod.log_dir()) == tmp_path / "MiniTPS"


# ---------------------------------------------------------------- 流绑死

def test_handler_follows_the_current_stdout(fresh_log, monkeypatch):
    """pytest 每个用例都换 `sys.stdout`，handler 必须跟着换。"""
    import io

    log_mod.configure("INFO", path="")
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    log_mod.get_logger("stream").info("后来的流")
    assert "后来的流" in buffer.getvalue()


def test_handler_swallows_a_closed_stream(fresh_log, monkeypatch):
    """往关掉的流里写会抛 ValueError —— 绝不能让它打断主循环。"""
    import io

    log_mod.configure("INFO", path="")
    dead = io.StringIO()
    dead.close()
    monkeypatch.setattr(sys, "stdout", dead)
    log_mod.get_logger("stream").error("写不进去就算了")     # 不抛就算过


# ---------------------------------------------------------------- 收尾检查

def test_no_bare_print_left_outside_log_module():
    """27 处裸 `print` 已经全部改成分级日志，别让新的混进来。

    `src/core/log.py` 里留了一处刻意的 print：日志文件打不开时，
    日志系统自己还没配好，绕回去会递归。
    """
    offenders = []
    targets = [*sorted((ROOT / "src").rglob("*.py")), ROOT / "main.py"]
    for path in targets:
        if path == ROOT / "src" / "core" / "log.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "print"):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], f"还有裸 print：{offenders}"
