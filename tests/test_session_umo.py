"""会话主键归一 / 插件聚合自检（纯标准库，可直接 python tests/test_session_umo.py 运行）。

覆盖 v7.11.3 三个回归点：
1. 纯 ID / UMO / plugin: 前缀的归一化；
2. gallery 里 user_id=plugin:<source> 的记录只按插件名聚合，且**不**用 session_id
   当 key（否则用某插件出过图的群会被误判成插件、同一插件在每个群重复一条）；
3. oplog 里的纯 ID 不再凭空造会话，只给已存在的会话补活跃时间。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session_store import SessionStore, normalize_origin  # noqa: E402


def main() -> None:
    # ---- 1. 归一化 ----
    assert normalize_origin("aiocqhttp:FriendMessage:123") == "aiocqhttp:FriendMessage:123"
    assert normalize_origin("aiocqhttp:GroupMessage:999") == "aiocqhttp:GroupMessage:999"
    assert normalize_origin("plugin:萌萌星语") == "plugin:萌萌星语"
    assert normalize_origin("123") == "aiocqhttp:FriendMessage:123"           # 纯 ID 私聊
    assert normalize_origin("999", group_id="999") == "aiocqhttp:GroupMessage:999"  # 纯 ID 群
    assert normalize_origin("") == ""
    print("== 1. UMO 归一化 OK")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        st = SessionStore(root)

        # ---- 2. gallery 统计：插件聚合 + 会话归一 ----
        gconn = sqlite3.connect(str(root / "gallery.db"))
        gconn.execute(
            """CREATE TABLE images (
                sha256 TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, user_name TEXT,
                group_name TEXT, group_id TEXT, workflow TEXT, created_at REAL, is_img2img INT,
                deleted INT DEFAULT 0, status INT DEFAULT 0
            )"""
        )
        now = time.time()
        # 同一个插件在两个不同群出图 → 应只产生一条 plugin:萌萌星语，且不产生群会话
        gconn.executemany(
            "INSERT INTO images (sha256,session_id,user_id,user_name,group_name,group_id,workflow,created_at,is_img2img) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("a", "111", "plugin:萌萌星语", "萌萌星语", "群A", "111", "wf1", now, 0),
                ("b", "222", "plugin:萌萌星语", "萌萌星语", "群B", "222", "wf1", now, 0),
                # 普通私聊：纯 ID + 空 group_id
                ("c", "333", "333", "张三", "", "", "wf2", now, 0),
                # 普通群聊：纯 ID + group_id
                ("d", "444", "555", "李四", "测试群", "444", "wf3", now, 0),
                # 已归一化 UMO 的私聊（联系人同步口径）
                ("e", "aiocqhttp:FriendMessage:666", "666", "王五", "", "", "wf2", now, 0),
            ],
        )
        gconn.commit()
        gconn.close()

        stats = st.history_stats()
        assert "plugin:萌萌星语" in stats, stats.keys()
        p = stats["plugin:萌萌星语"]
        assert p["is_plugin"] is True
        assert p["draw_total"] == 2, p  # 两条插件出图合并成一条
        assert "111" not in stats and "222" not in stats  # 群没被误判成插件
        assert "aiocqhttp:FriendMessage:333" in stats
        assert "aiocqhttp:GroupMessage:444" in stats
        assert "aiocqhttp:FriendMessage:666" in stats
        print("== 2. 插件仅按插件名聚合 + 会话 UMO 归一 OK")

        # ---- 3. oplog 不造新会话 ----
        oconn = sqlite3.connect(str(root / "oplog.db"))
        oconn.execute(
            "CREATE TABLE oplog (id INTEGER PRIMARY KEY, ts REAL, event TEXT, "
            "user_id TEXT, user_name TEXT, session_id TEXT, summary TEXT, detail TEXT, "
            "ref_sha TEXT, extra TEXT)"
        )
        oconn.execute(
            "INSERT INTO oplog (ts, event, user_id, session_id) VALUES (?,?,?,?)",
            (now + 10, "draw_success", "999", "999"),  # 从未出过图的纯 ID
        )
        oconn.commit()
        oconn.close()

        stats2 = st.history_stats()
        assert "aiocqhttp:FriendMessage:999" not in stats2, stats2.keys()  # 没凭空造会话
        assert stats2["aiocqhttp:FriendMessage:333"]["last_draw"] >= now
        print("== 3. oplog 不凭空造会话 OK")
        st.close()

    print("RESULT: session_umo OK")


if __name__ == "__main__":
    main()
