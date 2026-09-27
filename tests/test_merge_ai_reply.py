"""AI 配文与图片合并成一条消息（v7.7.49）。

覆盖：
  ① `_defer_draw_images`：合并开 + AI 对话路径 → 暂存并清掉「图已发出」标记；
     第三方调用（有 source）/ 合并关闭 / 静默调用 → 不接管（立即发送）；
     同一轮多张（prompts 多条）→ 追加进同一批；
  ② `_merge_ai_reply_with_images`：发送前把 AI 文本与图片、@ 合成一条
     （顺序：@ → AI 配文 → 图）；AI 无文本时退化为「@ + 图」；
     流式输出时跳过合并改为直接发图（避免文本重复）；无暂存图时什么都不做；
  ③ `_flush_pending_images`：兜底直发并清空暂存。

跑法：python tests/test_merge_ai_reply.py
"""

import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Log:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


class _FakeAt:
    def __init__(self, qq="", name=""):
        self.qq = str(qq)


class _FakeImage:
    def __init__(self, path=""):
        self.path = str(path)
        self.file = ""

    @staticmethod
    def fromFileSystem(p):
        return _FakeImage(str(p))


class _FakePlain:
    def __init__(self, text=""):
        self.text = text


class _FakeChain:
    def __init__(self, chain=None):
        self.chain = list(chain or [])


class _FakeResult:
    def __init__(self, chain=None):
        self.chain = list(chain or [])
        self.use_t2i_ = None
        self.result_content_type = None


class _Ev:
    def __init__(self, sid="s1", result=None):
        self.session_id = sid
        self._result = result
        self.sent_chains = []
        self._anima_silent_trace = ""

    def get_result(self):
        return self._result

    def set_result(self, r):
        self._result = r

    def chain_result(self, chain):
        return _FakeResult(list(chain))

    def get_sender_id(self):
        return "12345"

    async def send(self, chain):
        self.sent_chains.append(chain)


def _source() -> str:
    return (ROOT / "main.py").read_text(encoding="utf-8-sig")


def _load(names: tuple[str, ...], ns_extra: dict | None = None) -> dict:
    src = _source()
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    def _hook_register(_name):
        def deco(fn):
            return fn
        return deco

    ns = {"logger": _Log(), "At": _FakeAt, "Image": _FakeImage, "Plain": _FakePlain,
          "MessageChain": _FakeChain, "time": __import__("time"),
          "asyncio": asyncio, "os": __import__("os"),
          "AstrMessageEvent": object, "_hook_register": _hook_register}
    if ns_extra:
        ns.update(ns_extra)
    out: dict = {}
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), "<x>", "exec"), ns)  # noqa: S102
            out[node.name] = ns[node.name]
    return out


class _S:
    """假插件：提供合并逻辑依赖的少数方法/配置。"""

    def __init__(self, members, g_sessions, g_sent, g_pending, *, merge_on=True,
                 ic_cfg=None, private=False):
        self.m = members
        self.g_sessions = g_sessions
        self.g_sent = g_sent
        self.g_pending = g_pending
        self.merge_on = merge_on
        self.ic_cfg = ic_cfg if ic_cfg is not None else {}
        self.private = private
        self.flushed = []

    # 被摘出来的真实方法（真身引用模块级 g_* → 注入的就是同一份 dict）
    def _merge_reply_enabled(self):
        if not self.merge_on:
            return False
        return self.m["_merge_reply_enabled"](self)

    def _cfg_image_caption(self):
        return dict(self.ic_cfg, enabled=True, merge_ai_reply=self.merge_on)

    def _is_private_event(self, event):
        return self.private

    _image_paths_of = staticmethod(_load(("_image_paths_of",))["_image_paths_of"])

    def _maybe_at_prefix(self, event, comps):
        return self.m["_maybe_at_prefix"](self, event, comps)

    def _is_silent_call(self, event):
        return bool(getattr(event, "_anima_silent_trace", ""))

    def _defer_draw_images(self, event, node, source=""):
        return self.m["_defer_draw_images"](self, event, node, source=source)

    async def _flush_pending_images(self, event, sid, reason=""):
        got = await self.m["_flush_pending_images"](self, event, sid, reason)
        self.flushed.append((sid, list(got)))
        return got

    async def _send_image_with_recall(self, event, chain):
        event.sent_chains.append(chain)

    async def _merge_ai_reply_with_images(self, event):
        return await self.m["_merge_ai_reply_with_images"](self, event)


# 摘出来的函数闭包引用的是 exec 时的 ns，因此这里统一用注入的同一批 dict
_MEMBERS = _load(("_merge_reply_enabled", "_defer_draw_images", "_flush_pending_images",
                  "_maybe_at_prefix", "_merge_ai_reply_with_images"),
                 {"g_draw_agent_sessions": {}, "g_draw_sent": {}, "g_pending_images": {}})
_G_SESSIONS = _MEMBERS["_defer_draw_images"].__globals__["g_draw_agent_sessions"]
_G_SENT = _MEMBERS["_defer_draw_images"].__globals__["g_draw_sent"]
_G_PENDING = _MEMBERS["_defer_draw_images"].__globals__["g_pending_images"]


def _s(**kw):
    return _S(_MEMBERS, _G_SESSIONS, _G_SENT, _G_PENDING, **kw)


def _mk_node(*paths):
    return _FakeChain([_FakeImage(p) for p in paths])


def test_defer():
    _G_SESSIONS.clear(); _G_SENT.clear(); _G_PENDING.clear()
    s = _s()
    ev = _Ev()
    _G_SESSIONS["s1"] = "model"

    # AI 对话路径（无 source、非静默）→ 暂存 + 清掉「图已发出」标记
    _G_SENT["s1"] = 1.0
    ok = s._defer_draw_images(ev, _mk_node(__file__))
    assert ok is True, "AI 对话路径应接管发送"
    assert _G_PENDING["s1"][1] == [__file__]
    assert "s1" not in _G_SENT, "图没发出 → 必须清掉抑制标记"

    # 同一轮多张 → 追加进同一批（最终一条消息发多张）
    ok = s._defer_draw_images(ev, _mk_node(__file__))
    assert ok is True and _G_PENDING["s1"][1] == [__file__, __file__], _G_PENDING["s1"]

    # 第三方调用（带 source）→ 不接管
    _G_PENDING.clear()
    assert s._defer_draw_images(ev, _mk_node(__file__), source="partner") is False
    assert "s1" not in _G_PENDING

    # 合并关闭 → 不接管
    s2 = _s(merge_on=False)
    assert s2._defer_draw_images(ev, _mk_node(__file__)) is False

    # 静默调用 → 不接管
    ev2 = _Ev()
    ev2._anima_silent_trace = "1"
    assert s._defer_draw_images(ev2, _mk_node(__file__)) is False

    # 没有可用图片路径 → 不接管
    assert s._defer_draw_images(_Ev(sid="s9"), _FakeChain([_FakePlain("x")])) is False
    print("== 出图暂存（AI 路径接管 / 第三方与静默不接管 / 同轮追加 / 非图片不接管） OK")


def test_merge():
    _G_SESSIONS.clear(); _G_SENT.clear(); _G_PENDING.clear()
    s = _s()
    ev = _Ev()

    async def run():
        # AI 有配文 → 一条：[@, AI 文本, 图]
        _G_PENDING["s1"] = (1.0, [__file__])
        ev._result = _FakeResult([_FakePlain("给你画好啦～")])
        await s._merge_ai_reply_with_images(ev)
        chain = ev.get_result().chain
        kinds = [type(c).__name__ for c in chain]
        assert kinds == ["_FakeAt", "_FakePlain", "_FakeImage"], kinds
        assert chain[0].qq == "12345"
        assert chain[1].text == "给你画好啦～"
        assert chain[2].path == __file__
        assert ev.get_result().use_t2i_ is False, "必须关掉 t2i，否则配文会把真图挤掉"
        assert "s1" not in _G_PENDING, "合并后应清空暂存"

        # AI 没说话（没有 result）→ 仍要发图：[@, 图]
        _G_PENDING["s1"] = (1.0, [__file__])
        ev._result = None
        await s._merge_ai_reply_with_images(ev)
        kinds = [type(c).__name__ for c in ev.get_result().chain]
        assert kinds == ["_FakeAt", "_FakeImage"], kinds

        # 流式输出 → 跳过合并，直接发图（避免文本重复）
        _G_PENDING["s1"] = (1.0, [__file__])
        ev._result = _FakeResult([_FakePlain("流式文本")])
        ev._result.result_content_type = SimpleNamespace(name="STREAMING_FINISH")
        ev.sent_chains.clear()
        await s._merge_ai_reply_with_images(ev)
        assert len(ev.sent_chains) == 1, "流式场景应直接发图"
        assert ev.get_result().chain[0].text == "流式文本", "流式文本不做改动"
        assert "s1" not in _G_PENDING

        # 没有暂存图 → 什么都不做
        ev.sent_chains.clear()
        before = list(ev.get_result().chain)
        await s._merge_ai_reply_with_images(ev)
        assert ev.get_result().chain == before and not ev.sent_chains

        # 兜底补发：直接发图并清空暂存
        _G_PENDING["s1"] = (1.0, [__file__])
        ev.sent_chains.clear()
        got = await s._flush_pending_images(ev, "s1", "测试兜底")
        assert got == [__file__] and len(ev.sent_chains) == 1
        assert "s1" not in _G_PENDING

    asyncio.run(run())
    print("== 合并发送（@ + AI 配文 + 图 / AI 未配文 / 流式跳过 / 无暂存 / 兜底补发） OK")


if __name__ == "__main__":
    test_defer()
    test_merge()
    print("AI 配文与图片合并成一条：全部通过")
