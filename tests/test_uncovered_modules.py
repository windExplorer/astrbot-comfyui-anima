# -*- coding: utf-8 -*-
"""此前零行为覆盖的 5 个顶层模块自检（v7.8.15）。

覆盖 platform_store / quota_store / token_store / oplog_store / nsfw_detector，
全部只用标准库 + 临时目录，不碰网络与真实 NSFW 模型（分类器注入假实现）。

背景：这 5 个模块此前只被 test_module_lists.py 做「清单存在性」守卫，
没有任何行为级断言；test_static_lint.py 的 TARGETS 也不含它们。
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import nsfw_detector  # noqa: E402
import oplog_store  # noqa: E402
import platform_store  # noqa: E402
import quota_store  # noqa: E402
import token_store  # noqa: E402


def test_platform_store_pure():
    assert platform_store.resolve_nai_size("4K竖图") == platform_store.NAI_SIZES["4Kportrait"]
    assert platform_store.resolve_nai_size("竖图") == platform_store.NAI_SIZES["portrait"]
    assert platform_store.resolve_nai_size("832x1216") == (832, 1216)
    assert platform_store.resolve_nai_size("832*1216") == (832, 1216)
    assert platform_store.resolve_nai_size("832×1216") == (832, 1216)
    assert platform_store.resolve_nai_size("乱写") is None

    assert platform_store.render_template("{{a}}-{{b}}", {"a": 1, "b": None}) == "1-"
    v = platform_store.build_render_variables(prompt="a b")
    assert v["prompt_encoded"] == "a%20b" and v["seed"] == -1

    vars_ = {"api_key": "secret"}
    assert platform_store.normalize_headers(
        {"headers": {"X-Key": "{{api_key}}"}}, vars_
    ) == {"X-Key": "secret"}
    assert platform_store.normalize_headers(
        {"headers": [{"key": "X-Key", "value": "{{api_key}}"}]}, vars_
    ) == {"X-Key": "secret"}

    got = platform_store.render_extra_params([
        {"key": "n", "value": "3", "vtype": "number"},
        {"key": "f", "value": "3.5", "vtype": "number"},
        {"key": "yes", "value": "YES", "vtype": "bool"},
        {"key": "no", "value": "off", "vtype": "bool"},
        {"key": "j", "value": '{"a":1}', "vtype": "json"},
        {"key": "bad", "value": "{oops", "vtype": "json"},
    ])
    assert got["n"] == 3 and got["f"] == 3.5
    assert got["yes"] is True and got["no"] is False
    assert got["j"] == {"a": 1} and got["bad"] == "{oops"

    assert platform_store.extract_path({"a": {"b": [{"c": 7}]}}, "a.b.0.c") == 7
    assert platform_store.extract_path({"a": 1}, "a.b") is None
    print("PASS platform_store 六个纯函数")


def test_quota_store():
    assert quota_store._hour_start(3600 * 1.5) == 3600
    # Windows 的 mktime 对 1970 年附近会溢出，用当前时间戳验证当日 0 点对齐
    ts = time.time()
    lt = time.localtime(ts)
    assert quota_store._day_start(ts) == int(
        time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    )

    tmp = Path(tempfile.mkdtemp(prefix="anima_quota_"))
    try:
        q = quota_store.QuotaStore(tmp)
        q.record_used("u1", "小明")
        usage = q.get_usage("u1")
        assert usage["total_used"] == 1 and usage["hour_used"] == 1 and usage["day_used"] == 1
        assert q.peek("u1")["user_name"] == "小明"
        q.reset_user("u1")
        assert q.get_usage("u1")["total_used"] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS quota_store 时间对齐与计数/重置")


def test_token_store():
    ts = 1700000000.0
    assert token_store._hour_bucket(ts) == time.strftime("%Y-%m-%d %H:00", time.localtime(ts))
    assert token_store._day_bucket(ts) == time.strftime("%Y-%m-%d", time.localtime(ts))
    start, end = token_store._day_range_buckets(1, 0)
    assert start < end and start.endswith(" 00:00") and end.endswith(" 00:00")

    tmp = Path(tempfile.mkdtemp(prefix="anima_token_"))
    try:
        t = token_store.TokenStore(tmp)
        t.record_used("u1", "chat", output=5)
        row = t._conn_get().execute(
            "SELECT * FROM llm_usage WHERE user_id=?", ("u1",)
        ).fetchone()
        assert row is not None and 5 in dict(row).values(), dict(row) if row else None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS token_store 桶键与写入")


def test_oplog_store():
    tmp = Path(tempfile.mkdtemp(prefix="anima_oplog_"))
    try:
        store = oplog_store.OpLogStore(tmp)
        store.add("test_event", "你好", user_id="u1", extra={"a": 1})
        rows = store.query(event="test_event")
        assert len(rows) == 1, rows
        assert rows[0]["extra"] == {"a": 1}, rows[0]
        assert store.count(event="test_event") == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS oplog_store 写入/查询/计数与 extra 解析")


def test_nsfw_detector():
    assert nsfw_detector.NSFWDetector(-1).threshold == 0.0
    assert nsfw_detector.NSFWDetector(2).threshold == 1.0
    assert nsfw_detector.NSFWDetector("bad").threshold == 0.5
    assert nsfw_detector.NSFWDetector().detect("/nonexistent/x.png") == (False, 0.0, False)

    tmp = Path(tempfile.mkdtemp(prefix="anima_nsfw_"))
    try:
        img = tmp / "x.png"
        img.write_bytes(b"png")
        d = nsfw_detector.NSFWDetector(0.7)
        d._clf = SimpleNamespace(classify=lambda p: SimpleNamespace(nsfw=0.8))
        assert d.detect(str(img)) == (True, 0.8, True)
        # 阈值是 >=：score 恰好等于阈值也算 NSFW
        d2 = nsfw_detector.NSFWDetector(0.8)
        d2._clf = SimpleNamespace(classify=lambda p: SimpleNamespace(nsfw=0.8))
        assert d2.detect(str(img)) == (True, 0.8, True)
        # 分类器返回缺 nsfw 字段时按 0.0 处理
        d3 = nsfw_detector.NSFWDetector()
        d3._clf = SimpleNamespace(classify=lambda p: SimpleNamespace())
        assert d3.detect(str(img)) == (False, 0.0, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS nsfw_detector 阈值钳制/缺路径/假分类器判定")


if __name__ == "__main__":
    test_platform_store_pure()
    test_quota_store()
    test_token_store()
    test_oplog_store()
    test_nsfw_detector()
    print("uncovered modules OK")
