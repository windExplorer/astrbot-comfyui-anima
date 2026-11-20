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
import asyncio
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
        return {"1038800862", "145326153"}

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


def test_session_umo_cross_platform() -> None:
    """v7.11.8：qq_official 前缀的同群 UMO 归一到 aiocqhttp，杜绝同群两条会话。"""
    ns = _load_main_fns({"_session_id_of", "_session_umo_of", "_session_remember_draw"})
    Fake = type("_Fake", (), {
        "sessions": None,
        "_session_id_of": ns["_session_id_of"],
        "_session_umo_of": ns["_session_umo_of"],
        "_session_remember_draw": ns["_session_remember_draw"],
    })
    p = Fake()
    p.sessions = _FakeSessions()

    ev = _Ev("145326153", "qq_official:GroupMessage:145326153")
    assert p._session_umo_of(ev) == "aiocqhttp:GroupMessage:145326153"
    p._session_remember_draw(ev)
    origin, kw = p.sessions.calls[-1]
    assert origin == "aiocqhttp:GroupMessage:145326153", origin
    print("== 2b. 跨平台同群 UMO 归一到 aiocqhttp OK")


def test_merge_group_sessions() -> None:
    """v7.11.8：同群双 origin 的配置/额度并进主行、副行删除。"""
    with tempfile.TemporaryDirectory() as td:
        st = SessionStore(Path(td))
        st.upsert("aiocqhttp:GroupMessage:145326153", name="星际机器人26届家校共育群",
                  is_group=True, group_id="145326153")
        st.upsert("qq_official:GroupMessage:145326153", name="Scum",
                  is_group=True, group_id="145326153")
        # 副行带额度与配置，主行没有
        st.record_usage("qq_official:GroupMessage:145326153")
        st.set_config("qq_official:GroupMessage:145326153", {"wf_t2i": "moss", "max_day": "10"})
        assert st.merge_group_sessions({"145326153"}) == 1
        rows = st.list_all()
        assert len(rows) == 1, rows
        assert rows[0]["origin"] == "aiocqhttp:GroupMessage:145326153"
        assert rows[0]["name"] == "星际机器人26届家校共育群"
        assert rows[0]["wf_t2i"] == "moss" and rows[0]["max_day"] == 10
        assert st.get_usage("aiocqhttp:GroupMessage:145326153")["total_used"] == 1
        st.close()
    print("== 6. 同群双 origin 配置/额度归并 OK")


def _make_gallery(root: Path, row: tuple) -> None:
    """造一张最小图库行，供 history_stats 聚合。"""
    gconn = sqlite3.connect(str(root / "gallery.db"))
    gconn.execute(
        "CREATE TABLE IF NOT EXISTS images (sha256 TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, "
        "user_name TEXT, group_name TEXT, group_id TEXT, workflow TEXT, "
        "created_at REAL, is_img2img INT, deleted INT DEFAULT 0, status INT DEFAULT 0)"
    )
    gconn.execute(
        "INSERT INTO images (sha256,session_id,user_id,user_name,group_name,group_id,workflow,created_at,is_img2img) "
        "VALUES (?,?,?,?,?,?,?,?,?)", row,
    )
    gconn.commit()
    gconn.close()


def test_history_stats_no_snapshot() -> None:
    """v7.11.7 盲区守卫：没点过同步联系人时，靠 sessions 表已登记的群行也要掰回群聊。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        st = SessionStore(root)
        # contacts meta 完全缺失 → 旧实现 known_group_ids() 返回空集，修复形同虚设
        assert st.known_group_ids() == set()
        st.upsert("aiocqhttp:GroupMessage:1038800862", name="小叽大家族",
                  is_group=True, group_id="1038800862")
        assert st.known_group_ids() == {"1038800862"}
        _make_gallery(root, ("b", "1038800862", "1479221500", "涟漪", "", "", "wf", time.time(), 0))
        stats = st.history_stats()
        assert "aiocqhttp:GroupMessage:1038800862" in stats, stats.keys()
        assert "aiocqhttp:FriendMessage:1038800862" not in stats, stats.keys()
        st.close()
    print("== 3. 无联系人快照时靠 sessions 已登记群行掰回群聊 OK")


def test_history_stats_group_name_hint() -> None:
    """v7.11.7：图库旧图 group_id 为空时，group_name 命中已知群名也要判群聊。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        st = SessionStore(root)
        # 群号故意不在快照里（id=999999），只有群名相同 → 只能靠 group_name 判据
        st.set_meta("contacts", {
            "groups": 1, "friends": 0, "friend_items": [],
            "group_items": [{"id": "999999", "name": "小叽大家族"}],
        })
        assert st.known_group_names() == {"小叽大家族"}
        assert "1038800862" not in st.known_group_ids()
        _make_gallery(root, ("c", "1038800862", "1479221500", "涟漪", "小叽大家族", "", "wf", time.time(), 0))
        stats = st.history_stats()
        assert "aiocqhttp:GroupMessage:1038800862" in stats, stats.keys()
        st.close()
    print("== 4. 图库旧图 group_name 命中已知群名时判群聊 OK")


def _load_webui_session_list() -> dict:
    """用 AST 抠 WebUIApi.session_list / _session_row_score，隔离 astrbot 运行时依赖。"""
    src = (ROOT / "webui_api.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WebUIApi")
    ns = {
        "logger": _Log(), "session_store": ss_mod, "time": time,
        "json_response": lambda v: v, "error_response": lambda m: {"error": m},
    }
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
                "session_list", "_session_row_score", "_sessions"):
            seg = ast.get_source_segment(src, node) or ""
            body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
            exec(textwrap.dedent(body), ns)  # noqa: S102
    assert "session_list" in ns and "_session_row_score" in ns
    return ns


class _FakeST:
    """最小 SessionStore 替身：返回一真群行 + 一假私聊行。"""

    def list_all(self):
        return [
            {"origin": "aiocqhttp:GroupMessage:1038800862", "name": "小叽大家族",
             "is_group": 1, "group_id": "1038800862", "user_id": "", "last_active": 100,
             "wf_t2i": "", "wf_i2i": "", "pin_t2i": 0, "pin_i2i": 0,
             "max_total": None, "max_hour": None, "max_day": None,
             "nsfw": None, "card": None, "enabled": None, "note": ""},
            {"origin": "aiocqhttp:FriendMessage:1038800862", "name": "涟漪",
             "is_group": 0, "group_id": "", "user_id": "1479221500", "last_active": 200,
             "wf_t2i": "", "wf_i2i": "", "pin_t2i": 0, "pin_i2i": 0,
             "max_total": None, "max_hour": None, "max_day": None,
             "nsfw": None, "card": None, "enabled": None, "note": ""},
        ]

    def get_meta(self, key, default=None):
        return default  # 无联系人快照

    def known_group_ids(self):
        return {"1038800862"}  # 来自 sessions 表已登记群行

    def history_stats(self):
        return {
            "aiocqhttp:GroupMessage:1038800862": {
                "draw_total": 3, "draw_today": 1, "group_name": "小叽大家族",
                "user_name": "涟漪", "last_draw": time.time(),
                "recent_workflow": "wf", "recent_i2i_workflow": "",
            }
        }

    def get_usage(self, origin):
        return {"total_used": 0, "hour_used": 0, "day_used": 0}


class _FakePlugin:
    def __init__(self, st):
        self.sessions = st

    def _pick_default_workflow_name(self, is_img2img):
        return ""

    def _workflows(self):
        return []

    def _draw_limit_cfg(self):
        return 0


def test_session_list_merges_fake_private() -> None:
    """v7.11.7：会话列表把「人名假私聊」并入真群行，不再一正一误两条。"""
    ns = _load_webui_session_list()
    FakeApi = type("_FakeApi", (), {
        "__init__": lambda self, plugin: setattr(self, "plugin", plugin),
        "session_list": ns["session_list"],
        "_sessions": ns["_sessions"],
        "_session_row_score": staticmethod(ns["_session_row_score"]),
    })
    api = FakeApi(_FakePlugin(_FakeST()))
    result = asyncio.run(api.session_list())
    assert "error" not in result, result
    groups = [x for x in result["sessions"] if x.get("is_group")]
    assert len(groups) == 1, result["sessions"]
    assert groups[0]["origin"] == "aiocqhttp:GroupMessage:1038800862"
    assert groups[0]["name"] == "小叽大家族", groups[0]
    assert groups[0]["draw_total"] == 3
    assert not any(x["origin"] == "aiocqhttp:FriendMessage:1038800862" for x in result["sessions"])
    print("== 5. 会话列表把假私聊并入真群行 OK")


class _FakeSTCrossPlatform(_FakeST):
    """截图场景：同群两条 GroupMessage（aiocqhttp 有出图、qq_official 有额度+昵称）。"""

    def list_all(self):
        return [
            {"origin": "aiocqhttp:GroupMessage:145326153", "name": "星际机器人26届家校共育群",
             "is_group": 1, "group_id": "145326153", "user_id": "", "last_active": 100,
             "wf_t2i": "", "wf_i2i": "", "pin_t2i": 0, "pin_i2i": 0,
             "max_total": None, "max_hour": None, "max_day": None,
             "nsfw": None, "card": None, "enabled": None, "note": ""},
            {"origin": "qq_official:GroupMessage:145326153", "name": "Scum",
             "is_group": 1, "group_id": "145326153", "user_id": "", "last_active": 200,
             "wf_t2i": "moss", "wf_i2i": "", "pin_t2i": 0, "pin_i2i": 0,
             "max_total": 16, "max_hour": None, "max_day": 10,
             "nsfw": None, "card": None, "enabled": None, "note": ""},
        ]

    def get_meta(self, key, default=None):
        if key == "contacts":
            return {"group_items": [{"id": "145326153", "name": "星际机器人26届家校共育群"}],
                    "friend_items": []}
        return default

    def history_stats(self):
        return {
            "aiocqhttp:GroupMessage:145326153": {
                "draw_total": 316, "draw_today": 9, "group_name": "星际机器人26届家校共育群",
                "user_name": "", "last_draw": time.time(),
                "recent_workflow": "moss", "recent_i2i_workflow": "qw21t图生图",
            }
        }

    def get_usage(self, origin):
        if origin == "qq_official:GroupMessage:145326153":
            return {"total_used": 16, "hour_used": 1, "day_used": 10}
        return {"total_used": 0, "hour_used": 0, "day_used": 0}


def test_session_list_cross_platform_merge() -> None:
    """v7.11.8：截图场景——同群双平台前缀并成一条，名字/统计/额度/配置都正确。"""
    ns = _load_webui_session_list()
    FakeApi = type("_FakeApi", (), {
        "__init__": lambda self, plugin: setattr(self, "plugin", plugin),
        "session_list": ns["session_list"],
        "_sessions": ns["_sessions"],
        "_session_row_score": staticmethod(ns["_session_row_score"]),
    })
    api = FakeApi(_FakePlugin(_FakeSTCrossPlatform()))
    result = asyncio.run(api.session_list())
    assert "error" not in result, result
    groups = [x for x in result["sessions"] if x.get("is_group")]
    assert len(groups) == 1, result["sessions"]
    g = groups[0]
    assert g["origin"] == "aiocqhttp:GroupMessage:145326153", g
    assert g["name"] == "星际机器人26届家校共育群", g  # 不是 Scum
    assert g["draw_total"] == 316, g
    assert g["usage"]["total_used"] == 16, g  # 额度累加
    assert g["wf_t2i"] == "moss" and g["max_day"] == 10, g  # 配置补齐
    print("== 7. 同群双平台前缀在列表层并成一条 OK")


def test_session_pin_switch_numeric_value() -> None:
    """v7.11.9：编辑弹窗的 pin 开关必须显式声明数字 checked/unchecked。

    后端 pin_t2i/pin_i2i 是数字 1/0；n-switch 默认 checked-value=true（boolean），
    裸绑数字 1 严格比较不等于 true → 「已固定」在弹窗里显示成关，与列表的
    wf_now_t2i.pinned（后端算的 boolean）矛盾。守卫住两处都带数字值声明。
    """
    src = (ROOT / "webui-src" / "src" / "views" / "SessionView.vue").read_text(encoding="utf-8")
    for field in ("pin_t2i", "pin_i2i"):
        import re
        m = re.search(r"<n-switch[^>]*v-model:value=\"draft\." + field + r"\"[^>]*/>", src)
        assert m, f"没找到 {field} 的 n-switch"
        tag = m.group(0)
        assert ":checked-value=\"1\"" in tag and ":unchecked-value=\"0\"" in tag, (
            f"{field} 的 n-switch 缺数字 checked/unchecked 声明：{tag}"
        )
    print("== 8. 编辑弹窗 pin 开关数字值绑定 OK")


if __name__ == "__main__":
    test_history_stats_known_group()
    test_session_umo_and_name()
    test_session_umo_cross_platform()
    test_history_stats_no_snapshot()
    test_history_stats_group_name_hint()
    test_session_list_merges_fake_private()
    test_session_list_cross_platform_merge()
    test_merge_group_sessions()
    test_session_pin_switch_numeric_value()
    print("RESULT: session_group_fix OK")
