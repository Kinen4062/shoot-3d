"""存档：最高分等跨局数据。

**只存"跨局"数据**。当前局的分数、连击、波次一律不落盘 ——
那些属于 `core/game_data.py` 的运行时状态，重开必须归零。

四条硬规则（每一条都对应一次真实的踩坑）：

1. **不能写程序目录**。装到 `Program Files` 后没有写权限，
   第一次 `open(..., 'w')` 就抛异常，而且是在"游戏结束"这个最不该崩的时刻。
   路径统一走 `config.SAVE_FILE`（`%APPDATA%\\MiniTPS\\save.json`）。
2. **写失败必须静默**。存档挂了不该让游戏崩；返回 `False` 让调用方决定是否提示。
3. **读到的数据必须洗一遍**。用户手改、半截写入、旧版本字段缺失都会出现；
   下游拿到 `best_score=None` 再参与比较会变成 TypeError。
4. **写入要原子**。先写 `.tmp` 再 `os.replace` —— 直接覆盖原文件时断电，
   文件会变成 0 字节，下次启动读到的就是"最高分丢了"。
"""
from __future__ import annotations

import json
import os

from src.core import config
from src.core.log import get_logger

log = get_logger("save")

# 存档字段。加新字段时把默认值写在这里，sanitize 会自动补齐。
DEFAULTS: dict[str, int] = {
    "best_score": 0,
    "best_wave": 0,
    "total_kills": 0,
    "runs": 0,
}


def default_save() -> dict[str, int]:
    return dict(DEFAULTS)


def sanitize(raw: object) -> dict[str, int]:
    """把任意输入洗成合法存档。

    - 不是 dict → 全默认
    - 字段缺失 → 补默认
    - 值不是数字 / 是 bool / 是负数 → 归默认
    - 多余字段 → 丢弃（避免旧版本的脏字段一直传下去）
    """
    out = default_save()
    if not isinstance(raw, dict):
        return out

    for key, fallback in DEFAULTS.items():
        if key not in raw:
            continue
        value = raw[key]
        # bool 是 int 的子类，True 会被当成 1 混进来，显式排除。
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        out[key] = max(fallback, number)
    return out


def load_save(path: str | None = None) -> dict[str, int]:
    """读存档。文件不存在、读不动、JSON 坏了，一律返回默认值。"""
    target = path or config.SAVE_FILE
    try:
        with open(target, encoding="utf-8") as fp:
            return sanitize(json.load(fp))
    except FileNotFoundError:
        return default_save()
    except (OSError, ValueError) as exc:
        log.warning(f"[M4] 存档读取失败（{exc}），本次按默认值处理")
        return default_save()


def write_save(data: dict[str, int], path: str | None = None) -> bool:
    """原子写。返回 False 表示没写成（只影响最高分，不影响本局）。"""
    target = path or config.SAVE_FILE
    tmp = target + ".tmp"
    try:
        folder = os.path.dirname(target)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(sanitize(data), fp, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
        return True
    except OSError as exc:
        log.error(f"[M4] 存档写入失败（{exc}）")
        # 半截的临时文件留着没有意义，能删就删掉，删不掉也不影响
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def record_run(score: int, wave: int, kills: int,
               path: str | None = None) -> dict[str, int]:
    """一局结束后结算存档。返回更新后的完整存档。

    只有"更好"的时候才覆盖，否则打了一局烂的会把最高分洗掉。
    """
    data = load_save(path)
    data["best_score"] = max(data["best_score"], int(score))
    data["best_wave"] = max(data["best_wave"], int(wave))
    data["total_kills"] = data["total_kills"] + max(0, int(kills))
    data["runs"] = data["runs"] + 1
    write_save(data, path)
    return data
