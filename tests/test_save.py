"""存档的容错与原子性。

这个文件的重点不是"能存能读"，而是**存不进去时不能崩、读不出来时不能污染游戏**。
每一条 `test_*` 对应一种真实会发生的坏情况：
用户手改了 json、断电写了半截、旧版本缺字段、%APPDATA% 只读。
"""
import json
import os

import pytest

from src.core import save


def test_missing_file_returns_defaults(tmp_path):
    assert save.load_save(str(tmp_path / "nope.json")) == save.default_save()


def test_corrupt_json_returns_defaults(tmp_path):
    target = tmp_path / "save.json"
    target.write_text("{ this is not json", encoding="utf-8")
    assert save.load_save(str(target)) == save.default_save()


def test_sanitize_rejects_wrong_types():
    raw = {
        "best_score": "9999",      # 字符串：宁可丢，也不要去 int() 猜
        "best_wave": True,         # bool 是 int 的子类，必须显式排除
        "total_kills": -5,         # 负数
        "runs": 3.9,               # 浮点可以接受，截断成 int
        "unknown_field": 1,        # 多余字段：丢弃
    }
    out = save.sanitize(raw)
    assert out["best_score"] == 0
    assert out["best_wave"] == 0
    assert out["total_kills"] == 0
    assert out["runs"] == 3
    assert "unknown_field" not in out


def test_sanitize_fills_missing_fields():
    out = save.sanitize({"best_score": 1200})
    assert out["best_score"] == 1200
    assert out["best_wave"] == 0
    assert set(out) == set(save.DEFAULTS)


def test_write_then_load_roundtrip(tmp_path):
    target = str(tmp_path / "nested" / "save.json")
    assert save.write_save({"best_score": 4321, "best_wave": 9,
                            "total_kills": 120, "runs": 3}, target) is True
    assert save.load_save(target)["best_score"] == 4321
    # 临时文件不能残留
    assert not os.path.exists(target + ".tmp")


def test_write_failure_returns_false(tmp_path):
    """把目标指向一个"目录"，open/os.replace 一定失败，函数必须返回 False 而不是抛。"""
    blocker = tmp_path / "blocked"
    blocker.mkdir()
    assert save.write_save({"best_score": 1}, str(blocker)) is False
    # 失败后不能留下半截临时文件
    assert not os.path.exists(str(blocker) + ".tmp")


def test_record_run_only_raises_best(tmp_path):
    target = str(tmp_path / "save.json")

    first = save.record_run(1000, wave=5, kills=30, path=target)
    assert first["best_score"] == 1000
    assert first["best_wave"] == 5
    assert first["runs"] == 1

    # 打了一局更烂的：最高分不能被洗掉，但局数要 +1
    second = save.record_run(200, wave=2, kills=5, path=target)
    assert second["best_score"] == 1000
    assert second["best_wave"] == 5
    assert second["runs"] == 2
    assert second["total_kills"] == 35

    # 刷新纪录
    third = save.record_run(2500, wave=12, kills=80, path=target)
    assert third["best_score"] == 2500
    assert third["best_wave"] == 12
    assert third["runs"] == 3


def test_record_run_survives_corrupt_file(tmp_path):
    target = tmp_path / "save.json"
    target.write_text("garbage", encoding="utf-8")
    out = save.record_run(777, wave=3, kills=10, path=str(target))
    assert out["best_score"] == 777
    # 坏文件被合法内容覆盖掉了
    assert json.loads(target.read_text(encoding="utf-8"))["best_score"] == 777
