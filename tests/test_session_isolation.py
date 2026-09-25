"""会话隔离（v7.6.2）：私聊画完的角色不能再串进群聊。

**报障**：私聊画了个角色 → 群聊里只说「画张图」（没点名谁）→ 群里出的却是私聊刚画的那个角色。
**根因**：`self._last_event` 是个**全局单槽**，被私聊那次事件覆盖；群聊出图时
`llm_draw` 的「参考图兜底」直接读它，于是把**私聊那张图**当成群聊的参考图 → 走图生图
→ 出图当然就是私聊那个角色。此外多处 sid 键缓存（g_session_i2i_ref / g_last_generated…）
在没有 session_id 时会塌缩进同一个「空键桶」，同样会串味。

修法：最近事件**按会话存**，跨会话兜底一律拒绝；拿不到 session_id 时不去历史里捞图。

这几个方法住在 main.py（依赖 astrbot），沿用 tests/test_size_helpers.py 的 ast 摘取做法。

跑法：python tests/test_session_isolation.py
"""
import ast
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Log:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _load(want: set[str]) -> dict:
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")   # main.py 带 BOM
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    ns: dict = {"logger": _Log()}
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in want:
            continue
        seg = ast.get_source_segment(src, node) or ""
        body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
        exec(textwrap.dedent(body), ns)  # noqa: S102
    missing = want - set(ns)
    assert not missing, f"没摘到: {missing}"
    return ns


NS = _load({"_remember_last_event", "_last_event_for"})


class _Ev:
    """假事件：只需要 session_id。"""

    def __init__(self, sid: str, tag: str = ""):
        self.session_id = sid
        self.tag = tag

    def __repr__(self):
        return f"<Ev {self.tag or self.session_id}>"


class _Fake:
    """假 self：只要有 _remember_last_event / _last_event_for。"""

    _remember_last_event = NS["_remember_last_event"]
    _last_event_for = NS["_last_event_for"]


def test_same_session():
    p = _Fake()
    priv = _Ev("private_10086", "私聊")
    assert p._last_event_for(priv) is None      # 什么都没记时，不瞎给
    p._remember_last_event(priv)
    assert p._last_event_for(priv) is priv
    print("== 1. 同会话可取到自己的最近事件 OK")


def test_cross_session_rejected():
    """★本次报障的核心：私聊事件绝不能当群聊的兜底来源。"""
    p = _Fake()
    priv = _Ev("private_10086", "私聊")
    group = _Ev("group_555", "群聊")
    p._remember_last_event(priv)
    assert p._last_event_for(group) is None, "跨会话兜底必须被拒绝"
    assert p._last_event_for(priv) is priv
    print("== 2. 私聊事件不会被群聊当兜底（串味根治） OK")


def test_global_slot_overwritten():
    """全局槽被别的会话覆盖后，本会话仍取到自己的那份。"""
    p = _Fake()
    priv = _Ev("private_10086", "私聊")
    group = _Ev("group_555", "群聊")
    p._remember_last_event(priv)
    p._remember_last_event(group)          # 全局槽现在指向群聊
    assert p._last_event is group
    assert p._last_event_for(priv) is priv  # 私聊仍拿到私聊那份
    assert p._last_event_for(group) is group
    print("== 3. 全局槽被覆盖也不影响会话桶 OK")


def test_empty_sid_and_escape_hatch():
    p = _Fake()
    priv = _Ev("private_10086", "私聊")
    p._remember_last_event(priv)
    # 当前事件拿不到会话标识：只能谨慎用全局槽（此时无从判断是否同会话）
    assert p._last_event_for(_Ev("", "无sid")) is priv
    # 显式放行时才允许跨会话
    assert p._last_event_for(_Ev("group_555"), allow_cross_session=True) is priv
    print("== 4. 无会话标识时保守兜底 + 显式放行口 OK")


def test_bucket_bounded():
    p = _Fake()
    for i in range(60):
        p._remember_last_event(_Ev(f"s{i}"))
    assert len(p._last_events) <= 40, len(p._last_events)
    assert p._last_event_for(_Ev("s59")) is not None
    assert p._last_event_for(_Ev("s0")) is None      # 最老的被淘汰
    print(f"== 5. 会话桶有上限（当前 {len(p._last_events)} ≤ 40） OK")


if __name__ == "__main__":
    test_same_session()
    test_cross_session_rejected()
    test_global_slot_overwritten()
    test_empty_sid_and_escape_hatch()
    test_bucket_bounded()
    print("会话隔离测试全部通过")
