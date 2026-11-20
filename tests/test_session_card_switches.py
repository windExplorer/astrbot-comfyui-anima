"""会话级四类信息卡片开关自检（v7.12.0）。

覆盖：全局门槛（总开关一票否决 / scope 私聊）、会话级四类三态覆盖、
旧单字段 card 兼容兜底、DB 字段读写与 reset/迁移。用 AST 抠真实函数，
隔离 astrbot 运行时依赖。

    uv run --no-project --python 3.12 python tests/test_session_card_switches.py
"""
from __future__ import annotations

import ast
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import session_store as ss_mod  # noqa: E402
from session_store import SessionStore  # noqa: E402


class _Log:
    def debug(self, *a, **k):
        pass
    info = warning = error = debug


def _load_card_fns() -> dict:
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ComfyUIDrawPlugin")
    want = {"_card_should_send", "_card_would_send", "_CARD_KIND_MAP"}
    ns: dict = {"logger": _Log()}
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in want:
                    exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)
        if isinstance(node, ast.FunctionDef) and node.name in want:
            seg = ast.get_source_segment(src, node) or ""
            body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
            exec(textwrap.dedent(body), ns)  # noqa: S102
    assert all(k in ns for k in want), f"没摘到: {want - set(ns)}"
    return ns


def _make_harness_cls(ns):
    """用 type() 把抠出的函数挂到类上，确保 self 自动绑定。"""
    return type("_HarnessBase", (), {
        "_CARD_KIND_MAP": ns["_CARD_KIND_MAP"],
        "_card_should_send": ns["_card_should_send"],
        "_card_would_send": ns["_card_would_send"],
    })


class _HarnessMixin:
    """提供可注桩的 cfg / session / private 判定。"""
    def _card_cfg(self):
        defaults = {
            "enabled": True, "scope": "group", "result_enabled": True,
            "fail_enabled": True, "result_text": True,
        }
        defaults.update(self._cfg_data or {})
        return defaults

    def _session_cfg(self, event):
        return self._sess_data

    def _is_private_event(self, event):
        return self._private


def _Harness(ns, cfg, sess, private=False):
    """工厂：动态类同时带抠出的判断函数和桩方法。"""
    Base = _make_harness_cls(ns)
    Full = type("_Harness", (_HarnessMixin, Base), {})
    obj = Full()
    obj._cfg_data = cfg
    obj._sess_data = sess
    obj._private = private
    return obj


def check(title, cond, detail=""):
    if cond:
        print(f"  [OK] {title}")
    else:
        raise AssertionError(f"{title} {detail}")


def test_global_gates():
    """全局门槛：总开关一票否决、scope=group 私聊不发。"""
    ns = _load_card_fns()
    h = _Harness(ns, {"enabled": False}, {"card_done": 1})
    check("全局总开关关闭时会话『允许』也不发", h._card_should_send(None, "done") is False)
    h = _Harness(ns, {"scope": "group"}, {"card_done": 1}, private=True)
    check("scope=group 私聊不发", h._card_should_send(None, "done") is False)
    h = _Harness(ns, {"scope": "all"}, {"card_done": 1}, private=True)
    check("scope=all 私聊可发", h._card_should_send(None, "done") is True)


def test_session_override_and_global_follow():
    """会话级四类三态覆盖；NULL 跟随对应全局键。"""
    ns = _load_card_fns()
    # 会话禁止结果卡 → 不发（即使全局 result_enabled=True）
    h = _Harness(ns, {}, {"card_done": 0})
    check("会话结果卡=禁止 → 不发", h._card_should_send(None, "done") is False)
    # 会话允许失败卡 → 发（即使全局 fail_enabled=False）
    h = _Harness(ns, {"fail_enabled": False}, {"card_fail": 1})
    check("会话失败卡=允许 → 发（覆盖全局关闭）", h._card_should_send(None, "fail") is True)
    # 跟随全局：各类回落对应全局键
    h = _Harness(ns, {"result_enabled": False, "fail_enabled": True, "result_text": False}, {})
    check("跟随全局 result_enabled=False → 结果卡不发", h._card_should_send(None, "done") is False)
    check("跟随全局 fail_enabled=True → 失败卡发", h._card_should_send(None, "fail") is True)
    check("跟随全局 result_text=False → 结果文字不发", h._card_should_send(None, "result_text") is False)
    # 出图卡跟随全局=总开关（门槛已判）
    h = _Harness(ns, {}, {})
    check("出图卡跟随全局 → 发（总开关开）", h._card_should_send(None, "drawing") is True)


def test_card_would_send_mapping():
    """_card_would_send 的 state → kind 映射。"""
    ns = _load_card_fns()
    h = _Harness(ns, {}, {"card_done": 0, "card_fail": 0, "card_drawing": 1})
    check("state=done 映射 card_done", h._card_would_send(None, "done") is False)
    check("state=failed 映射 card_fail", h._card_would_send(None, "failed") is False)
    check("state=blocked 映射 card_fail", h._card_would_send(None, "blocked") is False)
    check("state=drawing 映射 card_drawing", h._card_would_send(None, "drawing") is True)


def test_old_card_compat():
    """旧单字段 card 作为四类共享兜底：仅四类全 NULL 时生效。"""
    ns = _load_card_fns()
    h = _Harness(ns, {}, {"card": 0})
    check("旧 card=0 → 四类都不发", all(not h._card_should_send(None, k)
          for k in ("drawing", "done", "fail", "result_text")))
    # 任一类单独配置后即视为已迁移到新体系：旧 card 整体失效，
    # 该类用自己的值、其余未配类跟随全局（避免新旧混用互相压制）。
    h = _Harness(ns, {}, {"card": 0, "card_done": 1})
    check("card_done 显式=1 时不被旧 card=0 拖累", h._card_should_send(None, "done") is True)
    check("已迁移后未配的 fail 跟随全局（不被旧 card 压制）", h._card_should_send(None, "fail") is True)


def test_db_fields_and_reset():
    """DB 四个新列可写可读、老库迁移补列、reset 清空。"""
    with tempfile.TemporaryDirectory() as td:
        st = SessionStore(Path(td))
        origin = "aiocqhttp:GroupMessage:1"
        st.upsert(origin, is_group=True, group_id="1")
        st.set_config(origin, {"card_drawing": 1, "card_done": 0,
                               "card_fail": 1, "card_result_text": 0})
        row = st.get(origin)
        check("四字段写入 DB", row["card_drawing"] == 1 and row["card_done"] == 0
              and row["card_fail"] == 1 and row["card_result_text"] == 0)
        st.reset_config(origin)
        row2 = st.get(origin)
        check("reset 后四字段回到 NULL",
              all(row2[f] is None for f in ss_mod.CARD_FIELDS))
        st.close()
        # 老库迁移：造一张没有新列的表，重开 SessionStore 应自动补列
        import sqlite3
        db = Path(td) / "sessions.db"
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE IF EXISTS sessions")
        conn.execute("CREATE TABLE sessions (origin TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', "
                     "is_group INTEGER NOT NULL DEFAULT 0, group_id TEXT NOT NULL DEFAULT '', "
                     "user_id TEXT NOT NULL DEFAULT '', last_active REAL NOT NULL DEFAULT 0, "
                     "updated_at REAL NOT NULL DEFAULT 0, wf_t2i TEXT NOT NULL DEFAULT '', "
                     "wf_i2i TEXT NOT NULL DEFAULT '', pin_t2i INTEGER NOT NULL DEFAULT 0, "
                     "pin_i2i INTEGER NOT NULL DEFAULT 0, max_total INTEGER DEFAULT NULL, "
                     "max_hour INTEGER DEFAULT NULL, max_day INTEGER DEFAULT NULL, "
                     "nsfw INTEGER DEFAULT NULL, card INTEGER DEFAULT NULL, "
                     "enabled INTEGER DEFAULT NULL, note TEXT NOT NULL DEFAULT '')")
        conn.commit()
        conn.close()
        st2 = SessionStore(Path(td))
        cols = {r[1] for r in st2._conn().execute("PRAGMA table_info(sessions)")}
        check("老库自动补四列", all(f in cols for f in ss_mod.CARD_FIELDS))
        st2.close()


if __name__ == "__main__":
    test_global_gates()
    test_session_override_and_global_follow()
    test_card_would_send_mapping()
    test_old_card_compat()
    test_db_fields_and_reset()
    print("RESULT: session_card_switches OK")
