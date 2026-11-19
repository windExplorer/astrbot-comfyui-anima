"""会话管理存储自检（纯标准库，可直接 python tests/test_session_store.py 运行）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session_store import SessionStore, parse_origin  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        st = SessionStore(Path(td))

        # UMO 解析
        g = parse_origin("aiocqhttp:GroupMessage:12345")
        p = parse_origin("aiocqhttp:FriendMessage:67890")
        assert g["is_group"] and g["group_id"] == "12345" and not g["user_id"]
        assert not p["is_group"] and p["user_id"] == "67890" and not p["group_id"]

        origin = "aiocqhttp:GroupMessage:12345"
        st.upsert(origin, name="测试群")
        row = st.get(origin)
        assert row and row["name"] == "测试群" and row["is_group"] == 1

        # 覆盖配置 + 固定工作流
        st.set_config(origin, {
            "wf_t2i": "动漫工作流", "pin_t2i": 1,
            "wf_i2i": "图生工作流", "pin_i2i": 1,
            "nsfw": 0, "card": 0, "enabled": 1,
            "max_total": 2, "max_hour": -1, "max_day": -1,
            "note": "测试备注",
        })
        row = st.get(origin)
        assert row["pin_t2i"] == 1 and row["wf_t2i"] == "动漫工作流"
        assert st.pinned_workflow(origin, False) == "动漫工作流"
        assert st.pinned_workflow(origin, True) == "图生工作流"
        assert not st.is_disabled(origin)

        # 会话限额：前两次放行，第三次因 max_total=2 拒绝
        assert st.has_quota_config(origin)
        assert st.check_quota(origin)[0]
        st.record_usage(origin)
        assert st.check_quota(origin)[0]
        st.record_usage(origin)
        ok, reason = st.check_quota(origin)
        assert not ok and "总次数" in reason
        assert st.get_usage(origin)["total_used"] == 2

        st.reset_usage(origin)
        assert st.get_usage(origin)["total_used"] == 0

        # 禁用
        st.set_config(origin, {"enabled": 0})
        assert st.is_disabled(origin)

        # 恢复跟随全局
        st.reset_config(origin)
        row = st.get(origin)
        assert row["pin_t2i"] == 0 and row["max_total"] is None and row["nsfw"] is None
        assert not st.has_quota_config(origin)

        # 元信息 + 删除
        st.set_meta("contacts", {"friends": 3, "groups": 2})
        assert st.get_meta("contacts", {})["groups"] == 2
        assert st.delete(origin)
        assert st.get(origin) is None

        # 空库统计不报错
        assert st.history_stats() == {}
        st.close()
        print("RESULT: session_store OK")


if __name__ == "__main__":
    main()
