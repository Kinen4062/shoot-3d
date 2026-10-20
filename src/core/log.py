"""分级日志。**唯一目的：发布版能一键静音。**

改之前的状态是 27 处裸 `print`，散在 12 个文件里。要静音只有一条路：
把这 27 行全部注释掉 —— 但那样连"存档写失败""音效装载失败"也一起没了。
反过来想查某个问题，也没有任何办法只看一类信息。

现在分成四档，靠环境变量切换：

    MINITPS_LOG=DEBUG     每条生成点重采样、每次换弹都打（排查用）
    MINITPS_LOG=INFO      默认。波次、重开、装载结果
    MINITPS_LOG=WARNING   只留"能跑但不对劲"
    MINITPS_LOG=ERROR     只留"功能已经坏了"
    MINITPS_LOG=OFF       全静音

**默认值**：开发期 `INFO`；打包成 exe（`sys.frozen`）后自动降成 `WARNING`。
打包版不该在市场里刷一屏调试信息，但也不能把错误吞掉 —— 玩家报"存档没了"
时，日志文件是唯一能复盘的东西。

**同时写文件**：`%APPDATA%\\MiniTPS\\log.txt`，超过 1 MB 轮转一份。
控制台只打消息本身（和以前的输出长得一样），文件里带时间/级别/模块名。
文件打不开时不抛异常 —— 只读目录、权限受限的机器照样要能玩。

**为什么消息里还留着 `[M3]` / `[M5]` 这些前缀**

它们是里程碑标签，`M3_验收报告.md` / `M5_验收清单.md` 里的验收步骤
直接按这些字符串检索（"控制台打 `[M5] run reset`"）。为了不破坏那些清单，
前缀保留在消息体里；**新代码不需要再加**，用模块名区分就够了。
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

ENV_VAR = "MINITPS_LOG"
# 控制台只打消息本身：以前的 `print` 输出就是这样，验收清单里的原文才对得上。
CONSOLE_FORMAT = "%(message)s"
# 文件里必须有时间，否则崩溃之后翻日志不知道先后。
FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)-12s %(message)s"

LOG_FILE_NAME = "log.txt"
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 1

# 开发期 / 发布版的默认级别。发布版从 WARNING 起。
DEV_LEVEL = "INFO"
RELEASE_LEVEL = "WARNING"

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    # OFF 用一个比 CRITICAL 还高的数：`Logger.setLevel` 是"低于此级丢弃"，
    # 所以定得比所有真实级别都高就等于全关。关闭 handler 的话
    # 漏掉一条自己 `log.error` 的反而更糟。
    "OFF": logging.CRITICAL + 10,
}

# 根 logger 名。所有模块都挂在它下面，改一次级别就全生效。
ROOT_NAME = "minitps"

_configured = False
_file_path: str | None = None


class _StdoutHandler(logging.StreamHandler):
    """永远写**当前**的 `sys.stdout`。

    直接 `StreamHandler(sys.stdout)` 会把启动那一刻的流对象绑死。
    pytest 每个用例都会替换 `sys.stdout`（capture 的 `EncodedFile`），
    绑死的 handler 之后往已经关闭的流里写，会抛
    `ValueError: I/O operation on closed file` —— 而且是在**别的**用例里冒出来，
    极难定位。用一个返回当前流的 property 就绕开了。
    """

    def __init__(self) -> None:
        super().__init__(stream=None)

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, _value) -> None:
        # 父类构造函数会赋值一次，这里故意吞掉 —— 要的就是不绑定。
        pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except (ValueError, OSError):
            # 流被关掉时宁可丢这一行，也不能让日志把主循环打断。
            pass


def log_dir() -> str:
    """日志目录。Windows 是 `%APPDATA%\\MiniTPS`，其它平台退回用户目录。"""
    appdata = os.environ.get("APPDATA")
    base = appdata if appdata else os.path.expanduser("~")
    return os.path.join(base, "MiniTPS")


def default_level() -> str:
    """没设环境变量时该用哪一档。打包版从 WARNING 起。"""
    return RELEASE_LEVEL if getattr(sys, "frozen", False) else DEV_LEVEL


def resolve_level(raw: str | None = None) -> int:
    """把 `MINITPS_LOG` 的值变成 logging 级别。无法识别的值退回默认。"""
    text = (raw if raw is not None else os.environ.get(ENV_VAR, "")).strip().upper()
    if text in _LEVELS:
        return _LEVELS[text]
    return _LEVELS[default_level()]


def level_name(raw: str | None = None) -> str:
    level = resolve_level(raw)
    for name, value in _LEVELS.items():
        if value == level:
            return name
    return default_level()


def file_path() -> str | None:
    """当前日志文件路径。文件没能打开时返回 None。"""
    return _file_path


def configure(raw_level: str | None = None, path: str | None = None) -> int:
    """装好 handler 并设级别。**幂等**：重复调用只是更新级别。

    `path=""` 表示不写文件（CI / 探针里不需要落盘）。
    """
    global _configured, _file_path

    root = logging.getLogger(ROOT_NAME)
    level = resolve_level(raw_level)
    root.setLevel(level)
    # 子 logger 不设级别，全部继承根 —— 否则改根级别时它们不会跟着变。
    root.propagate = False

    if _configured:
        return level

    console = _StdoutHandler()
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root.addHandler(console)

    target = log_dir() if path is None else path
    if target:
        full = target if target.endswith(".txt") else os.path.join(target, LOG_FILE_NAME)
        try:
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            handler = RotatingFileHandler(
                full, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter(FILE_FORMAT))
            root.addHandler(handler)
            _file_path = full
        except OSError as exc:
            # 只读目录 / 权限受限：不写文件也要能玩。这里用 print 而不是 log ——
            # `log` 本身还没装好，绕回自己会递归。
            print(f"[log] 日志文件不可写（{exc}），只输出到控制台")

    _configured = True
    return level


def get_logger(name: str) -> logging.Logger:
    """取一个模块级 logger。第一次调用时自动完成配置。"""
    if not _configured:
        configure()
    return logging.getLogger(f"{ROOT_NAME}.{name}")


def set_level(raw_level: str) -> int:
    """运行期改级别（探针 / 测试用）。返回实际生效的级别。"""
    level = resolve_level(raw_level)
    logging.getLogger(ROOT_NAME).setLevel(level)
    return level
