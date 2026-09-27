"""群聊出图：图片消息 @ 触发者 + 抑制重复的 AI 收尾回复（v7.7.47）。

覆盖：
  ① `_maybe_at_prefix`：群聊加 `At`（链首）、私聊不加、配置关不加、已有 At 不重复、
     取不到用户 id 不加、At 组件不可用（旧版 AstrBot）原样返回；
  ② `_mark_caption_sent`：只按 session 打标；
  ③ `_suppress_draw_reply`：三个条件（画图 run + 配文已发 + 本次无工具调用）同时成立才清空
     收尾文本；配置关、非画图 run、配文未发、带工具调用都放行。

跑法：python tests/test_at_and_suppress.py
"""

import ast
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Log:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


class _FakeAt:
    def __init__(self, qq="", name=""):
        self.qq = str(qq)
        self.name = name


class _FakeFilter:
    """假 filter：on_llm_response 装饰器原样返回函数。"""

    def on_llm_response(self):
        return lambda fn: fn


def _source() -> str:
    return (ROOT / "main.py").read_text(encoding="utf-8-sig")


def _load_methods(names: tuple[str, ...], ns_extra: dict | None = None) -> dict:
    src = _source()
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    ns = {"logger": _Log(), "At": _FakeAt, "filter": _FakeFilter(),
          "time": __import__("time"), "AstrMessageEvent": object}
    if ns_extra:
        ns.update(ns_extra)
    out: dict = {}
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), "<x>", "exec"), ns)  # noqa: S102
            out[node.name] = ns[node.name]
    return out


class _Ev:
    def __init__(self, group: str = "", uid: str = "12345", sid: str = "s1"):
        self.group = group
        self.uid = uid
        self.session_id = sid

    def get_sender_id(self):
        return self.uid


# ------------------------------------------------- ① 图片消息 @ 触发者
def test_at_prefix():
    members = _load_methods(("_maybe_at_prefix",))

    class S:
        group = True          # 群聊
        cfg = {"at_user": True}

        def _cfg_image_caption(self):
            return self.cfg

        def _is_private_event(self, event):
            return not self.group

        def _maybe_at_prefix(self, event, comps):
            return members["_maybe_at_prefix"](self, event, comps)

    s = S()
    ev = _Ev(group="g1")

    got = s._maybe_at_prefix(ev, ["img"])
    assert len(got) == 2 and isinstance(got[0], _FakeAt) and got[0].qq == "12345", got
    assert got[1] == "img"

    # 已有 At 不重复加
    got = s._maybe_at_prefix(ev, [_FakeAt(qq="1"), "img"])
    assert sum(isinstance(c, _FakeAt) for c in got) == 1, got

    # 私聊不加
    s.group = False
    got = s._maybe_at_prefix(_Ev(group=""), ["img"])
    assert got == ["img"], got
    s.group = True

    # 配置关闭不加
    s.cfg = {"at_user": False}
    assert s._maybe_at_prefix(ev, ["img"]) == ["img"]
    s.cfg = {"at_user": True}

    # 取不到用户 id 不加
    assert s._maybe_at_prefix(_Ev(group="g1", uid=""), ["img"]) == ["img"]

    # 旧版 AstrBot（At 不可用）原样返回
    members2 = _load_methods(("_maybe_at_prefix",), {"At": None})
    got = members2["_maybe_at_prefix"](s, ev, ["img"])
    assert got == ["img"], got
    print("== 图片消息 @（群聊加/私聊不加/配置关/不重复/无 uid/组件不可用） OK")


# ------------------------------------- ②③ 打标 + 抑制重复收尾回复
def _load_suppress():
    g_sessions: dict = {}
    g_caption: dict = {}
    members = _load_methods(
        ("_suppress_draw_reply", "_mark_caption_sent"),
        {"g_draw_agent_sessions": g_sessions, "g_caption_sent": g_caption},
    )
    return members, g_sessions, g_caption


class _Resp:
    def __init__(self, text="给你画好啦～", tools=None):
        self.completion_text = text
        self.tools_call_name = list(tools or [])


def test_suppress_reply():
    members, g_sessions, g_caption = _load_suppress()

    class S:
        cfg = {"suppress_reply": True}

        def _cfg_image_caption(self):
            return self.cfg

        def _mark_caption_sent(self, event):
            return members["_mark_caption_sent"](self, event)

        async def _suppress_draw_reply(self, event, response=None):
            return await members["_suppress_draw_reply"](self, event, response)

    s = S()
    ev = _Ev(sid="s1")

    async def run():
        # 三条件齐全 → 清空
        g_sessions.clear()
        g_caption.clear()
        g_sessions["s1"] = "model"
        s._mark_caption_sent(ev)
        assert "s1" in g_caption
        r = _Resp("给你画好啦～")
        await s._suppress_draw_reply(ev, r)
        assert r.completion_text == "", "配文已发 + 画图 run + 无工具调用 → 应清空收尾文本"

        # 带工具调用（画图请求本身）→ 不动
        r = _Resp("正在画", tools=["comfyui_draw"])
        await s._suppress_draw_reply(ev, r)
        assert r.completion_text == "正在画"

        # 配文没发过 → 不动
        g_caption.clear()
        r = _Resp("说明一下")
        await s._suppress_draw_reply(ev, r)
        assert r.completion_text == "说明一下"

        # 不是画图 run（普通对话）→ 不动
        g_caption["s1"] = 1.0
        g_sessions.clear()
        r = _Resp("普通回复")
        await s._suppress_draw_reply(ev, r)
        assert r.completion_text == "普通回复"

        # 配置关闭 → 不动
        g_sessions["s1"] = "model"
        s.cfg = {"suppress_reply": False}
        r = _Resp("关了就照说")
        await s._suppress_draw_reply(ev, r)
        assert r.completion_text == "关了就照说"
        s.cfg = {"suppress_reply": True}

        # 空文本 / response 缺失 → 不炸
        await s._suppress_draw_reply(ev, None)
        r = _Resp("")
        await s._suppress_draw_reply(ev, r)
        assert r.completion_text == ""

    asyncio.run(run())
    print("== 抑制重复收尾回复（三条件/带工具/未发配文/非画图run/配置关闭） OK")


if __name__ == "__main__":
    test_at_prefix()
    test_suppress_reply()
    print("群聊出图：@ 触发者 + 抑制重复回复 全部通过")
