"""会话归属修复自检（v7.11.6）。

报障：群聊 A 经星语等外部插件出图后，会话页「群聊」里 A 的名字变成触发者昵称
「涟漪」，「非好友」里同时多出一条 ID 同群号的假私聊。

根因：
1. 外部插件合成 event 的 get_group_id() 为空 → 图库只留 session_id=群号，
   history_stats/normalize_origin 把群判成私聊 → 落进「非好友」。
2. _session_remember_draw 拿不到 get_group_name() 时回退 get_sender_name()，
   upsert 把联系人同步写好的群名覆盖成触发者昵称。

跑法：python tests/test_session_group_fix.py
"""
from __future__ import annotations

import ast
import sqlite3
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import session_store as ss_mod  # noqa: E402
from session_store import SessionStore  # noqa: E402


def test_history_stats_known_group() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        st = SessionStore(root)
        st.set_meta("contacts", {
            "groups": 1, "friends": 1,
            "friend_items": [{"id": "1479221500", "name": "涟漪"}],
            "group_items": [{"id": "1038800862", "name": "小叽大家族"}],
        })
        assert st.known_group_ids() == {"1038800862"}
        gconn = sqlite3.connect(str(root / "gallery.db"))
        gconn.execute(
            "CREATE TABLE images (sha256 TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, "
            "user_name TEXT, group_name TEXT, group_id TEXT, workflow TEXT, "
            "created_at REAL, is_img2img INT, deleted INT DEFAULT 0, status INT DEFAULT 0)"
        )
        gconn.execute(
            "INSERT INTO images (sha256,session_id,user_id,user_name,group_name,group_id,workflow,created_at,is_img2img) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("a", "1038800862", "1479221500", "涟漪", "", "", "wf", time.time(), 0),
        )
        gconn.commit()
        gconn.close()
        stats = st.history_stats()
        assert "aiocqhttp:GroupMessage:1038800862" in stats, stats.keys()
        assert "aiocqhttp:FriendMessage:1038800862" not in stats, stats.keys()
        assert stats["aiocqhttp:GroupMessage:1038800862"]["draw_total"] == 1
        st.close()
    print("== 1. 群号被当私聊 ID 时按联系人快照掰回群聊 OK")


class _Log:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _load_main_fns(want: set[str]) -> dict:
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ComfyUIDrawPlugin")
    ns = {"logger": _Log(), "session_store": ss_mod, "time": time}
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in want:
            continue
        seg = ast.get_source_segment(src, node) or ""
        body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
        exec(textwrap.dedent(body), ns)  # noqa: S102
    missing = want - set(ns)
    assert not missing, f"没摘到: {missing}"
    return ns


class _Ev:
    """模拟外部插件合成 event：有 session_id / UMO，没有 get_group_id / get_group_name。"""

    def __init__(self, sid: str, umo: str):
        self.session_id = sid
        self.unified_msg_origin = umo

    def get_sender_name(self):
        return "涟漪"

    def get_sender_id(self):
        return "1479221500"


class _FakeSessions:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def known_group_ids(self):
        return {"1038800862"}

    def upsert(self, origin, **kw):
        self.calls.append((origin, kw))


class _Fake:
    sessions = None


def test_session_umo_and_name() -> None:
    ns = _load_main_fns({"_session_id_of", "_session_umo_of", "_session_remember_draw"})
    # 函数必须挂在类上才会绑定 self（直接塞实例属性不会自动传 self）
    Fake = type("_Fake", (), {
        "sessions": None,
        "_session_id_of": ns["_session_id_of"],
        "_session_umo_of": ns["_session_umo_of"],
        "_session_remember_draw": ns["_session_remember_draw"],
    })
    p = Fake()
    p.sessions = _FakeSessions()

    # event 把群号塞进 FriendMessage → 应被纠正为 GroupMessage
    ev = _Ev("1038800862", "aiocqhttp:FriendMessage:1038800862")
    sid = p._session_umo_of(ev)
    assert sid == "aiocqhttp:GroupMessage:1038800862", sid

    p._session_remember_draw(ev)
    origin, kw = p.sessions.calls[-1]
    assert origin == "aiocqhttp:GroupMessage:1038800862", origin
    assert kw["name"] == "", kw  # 群名不能被触发者昵称覆盖
    print("== 2. 合成 event 的群 UMO 纠正 + 群名不被昵称覆盖 OK")


if __name__ == "__main__":
    test_history_stats_known_group()
    test_session_umo_and_name()
    print("RESULT: session_group_fix OK")
