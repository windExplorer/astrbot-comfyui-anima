"""强制出图标记（v7.7.0）：`t 前缀 = 这条必须画出来`。

设计回顾（用户需求）：不是把 `t` 做成「绕过 LLM 的指令」，而是**标记 + 保底**——
消息照常进 LLM 让它自己画；本轮它真没画出来，才由插件兜底补画。

因此这里的核心断言是：
  · 标记识别**不能误伤英文句子**（`thanks` / `the cat` 不算标记）；
  · 群里必须 @机器人才算（私聊不要求）；
  · 本轮**已出图** → 标记消费掉、**不**兜底；
  · 本轮**没出图** → 触发兜底补画。

这些方法住在 main.py（依赖 astrbot），沿用 tests/test_size_helpers.py 的 ast 摘取做法。

跑法：python tests/test_force_draw.py
"""
import ast
import asyncio
import re
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Log:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


_WANT_METHODS = {
    "_force_draw_cfg", "_force_draw_prefix", "_force_draw_at_bot", "_force_draw_scan",
    "_strip_force_marker", "_remember_force_draw", "_force_draw_pending",
    "_clear_force_draw", "_take_force_draw", "_draw_run_msg_fp", "_force_draw_enforce",
}
_WANT_MODFUNCS = {"_default_force_draw_cfg"}
_WANT_CONSTS = {"_FORCE_DRAW_DEFAULT_PREFIX", "_FORCE_DRAW_FALLBACK_HINTS"}
_WANT_CLASSVARS = {"_DRAW_RUN_TTL"}


def _load() -> dict:
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")   # main.py 带 BOM
    tree = ast.parse(src)
    ns: dict = {"re": re, "time": time, "asyncio": asyncio, "logger": _Log(),
                "random": __import__("random")}
    _FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, _FUNCS) and node.name in _WANT_MODFUNCS:
            exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)  # noqa: S102
        elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") in _WANT_CONSTS for t in node.targets):
            exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)  # noqa: S102
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    for node in cls.body:
        if isinstance(node, _FUNCS) and node.name in _WANT_METHODS:
            seg = ast.get_source_segment(src, node) or ""
            body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
            exec(textwrap.dedent(body), ns)  # noqa: S102
        elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") in _WANT_CLASSVARS for t in node.targets):
            exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)  # noqa: S102
    missing = (_WANT_METHODS | _WANT_MODFUNCS | _WANT_CONSTS | _WANT_CLASSVARS) - set(ns)
    assert not missing, f"没摘到: {missing}"
    return ns


NS = _load()


class _Comp:
    """假消息组件（At / Plain）。"""

    def __init__(self, kind: str, qq: str = ""):
        self.__class__.__name__ = kind      # 代码是按 type(comp).__name__ 判断的
        self.qq = qq


class _MsgObj:
    def __init__(self, mid: str, ts: int, chain: list):
        self.message_id = mid
        self.timestamp = ts
        self.message = chain


class _Ev:
    """假事件：session_id / message_str / message_obj / @信息。"""

    def __init__(self, text: str, sid: str = "group_1", *, at_bot: bool = False,
                 private: bool = False, mid: str = "m1", ts: int = 1):
        self.message_str = text
        self.session_id = sid
        self._private = private
        self._self_id = "99999"
        chain = [_Comp("Plain")]
        if at_bot:
            chain.append(_Comp("At", "99999"))
        self.message_obj = _MsgObj(mid, ts, chain)

    def get_sender_id(self):
        return "10086"

    def get_self_id(self):
        return self._self_id


class _Fake:
    """假 self：实现被摘方法依赖的最小接口。"""

    def __init__(self, cfg=None, ok=0, execute=None):
        self.cfg = dict(NS["_default_force_draw_cfg"]())
        self.cfg.update(cfg or {})
        self.ok = ok
        self.executed: list[str] = []
        self._execute_stub = execute

    # --- 被依赖的原方法 ---
    def _cfg(self, key, default=None):
        if key == "force_draw":
            return self.cfg
        return default

    def _is_private_event(self, event):
        return bool(getattr(event, "_private", False))

    def _draw_run_state_of(self, event):
        return {"ok": self.ok}

    async def _force_draw_execute(self, event, text, cfg):
        self.executed.append(text)
        if self._execute_stub:
            await self._execute_stub(event, text)


for _n in _WANT_METHODS:
    setattr(_Fake, _n, NS[_n])
for _n in _WANT_CLASSVARS:
    setattr(_Fake, _n, NS[_n])


def test_cfg_defaults():
    p = _Fake()
    assert p._force_draw_cfg()["prefix"] == "t"
    assert p._force_draw_cfg()["require_at_in_group"] is True
    p2 = _Fake({"prefix": "tt", "fallback_draw": False})
    c = p2._force_draw_cfg()
    assert c["prefix"] == "tt" and c["fallback_draw"] is False
    print("== 1. 配置默认值 / 覆盖 OK")


def test_scan_prefix_rules():
    p = _Fake()
    cases = [
        ("t发张照片", True, "发张照片"),
        ("t 一个女孩", True, "一个女孩"),
        ("T猫耳少女", True, "猫耳少女"),
        ("t，画个猫", True, "画个猫"),
        ("t2 猫", True, "2 猫"),
        ("thanks", False, ""),
        ("the cat is cute", False, ""),
        ("t画", True, "画"),
        ("今天t画个猫", False, ""),          # 不在行首
        ("t", False, ""),                    # 只有标记、没内容
        ("t   ", False, ""),
    ]
    for text, exp_hit, exp_clean in cases:
        hit, clean = p._force_draw_scan(_Ev(text, private=True))
        assert hit is exp_hit, (text, hit, exp_hit)
        if exp_hit:
            assert clean == exp_clean, (text, clean, exp_clean)
    print("== 2. 前缀识别（防误伤英文 + 行首 + 去标记） OK")


def test_scan_group_and_custom_prefix():
    p = _Fake()
    # 群聊：没 @ 不算，@ 了才算
    assert p._force_draw_scan(_Ev("t画个猫"))[0] is False
    assert p._force_draw_scan(_Ev("t画个猫", at_bot=True))[0] is True
    # 关掉「必须 @」后群里也认
    p2 = _Fake({"require_at_in_group": False})
    assert p2._force_draw_scan(_Ev("t画个猫"))[0] is True
    # 私聊不需要 @
    assert p._force_draw_scan(_Ev("t画个猫", private=True))[0] is True
    # 自定义前缀：tt 生效，单个 t 不再生效
    p3 = _Fake({"prefix": "tt"})
    assert p3._force_draw_scan(_Ev("tt画个猫", private=True))[0] is True
    assert p3._force_draw_scan(_Ev("t画个猫", private=True))[0] is False
    # 总开关关闭
    p4 = _Fake({"enabled": False})
    assert p4._force_draw_scan(_Ev("t画个猫", private=True))[0] is False
    print("== 3. 群聊需 @机器人 / 自定义前缀 / 总开关 OK")


def test_strip_marker():
    p = _Fake()
    assert p._strip_force_marker("t发张照片") == "发张照片"
    assert p._strip_force_marker("thanks") == "thanks"          # 不是标记，别动
    assert p._strip_force_marker("the cat") == "the cat"
    assert p._strip_force_marker("1girl, solo") == "1girl, solo"
    assert p._strip_force_marker("t") == "t"                    # 没内容，原样
    print("== 4. 提示词里的残留标记剥离 OK")


def test_pending_lifecycle():
    p = _Fake()
    ev1 = _Ev("t发张照片", sid="group_1")
    p._remember_force_draw(ev1, "发张照片")
    item = p._force_draw_pending(ev1)
    assert item and item["text"] == "发张照片"
    # 另一条消息（指纹不同）→ 不认
    assert p._force_draw_pending(_Ev("t别的", sid="group_1")) is None
    # 另一个会话 → 不认
    assert p._force_draw_pending(_Ev("t发张照片", sid="group_2")) is None
    # 消费一次后清空
    text, hit = p._take_force_draw(ev1)
    assert hit and text == "发张照片"
    assert p._force_draw_pending(ev1) is None
    # 过期清理
    p._remember_force_draw(ev1, "发张照片")
    p._force_draw_marks["group_1"]["ts"] = time.time() - 10_000
    assert p._force_draw_pending(ev1) is None
    print("== 5. 标记生命周期（指纹/会话隔离/消费一次/过期） OK")


def test_enforce_behavior():
    """核心：本轮已出图 → 不兜底；没出图 → 兜底。"""
    async def _run(p, ev):
        return await p._force_draw_enforce(ev)

    # ① 本轮没出图 → 兜底补画
    p = _Fake(ok=0)
    ev = _Ev("t发张照片", sid="s1")
    p._remember_force_draw(ev, "发张照片")
    asyncio.run(_run(p, ev))
    assert p.executed == ["发张照片"], p.executed
    assert p._force_draw_pending(ev) is None, "兜底后标记应被消费"
    # ② 本轮已出图 → 只消费标记，不兜底
    p2 = _Fake(ok=1)
    ev2 = _Ev("t发张照片", sid="s2")
    p2._remember_force_draw(ev2, "发张照片")
    asyncio.run(_run(p2, ev2))
    assert p2.executed == [], p2.executed
    assert p2._force_draw_pending(ev2) is None
    # ③ 兜底开关关闭 → 不兜底（标记仍消费，避免反复触发）
    p3 = _Fake({"fallback_draw": False}, ok=0)
    ev3 = _Ev("t发张照片", sid="s3")
    p3._remember_force_draw(ev3, "发张照片")
    asyncio.run(_run(p3, ev3))
    assert p3.executed == [], p3.executed
    # ④ 没有标记的正常消息 → 什么都不做
    p4 = _Fake(ok=0)
    asyncio.run(_run(p4, _Ev("画张图", sid="s4")))
    assert p4.executed == []
    # ⑤ 标记属于别的消息 → 不兜底（防止串到下一轮）
    p5 = _Fake(ok=0)
    p5._remember_force_draw(_Ev("t发张照片", sid="s5", mid="m1"), "发张照片")
    asyncio.run(_run(p5, _Ev("随便聊聊", sid="s5", mid="m2")))
    assert p5.executed == []
    print("== 6. 兜底判定（已出图不补 / 没出图补 / 开关 / 指纹） OK")


if __name__ == "__main__":
    test_cfg_defaults()
    test_scan_prefix_rules()
    test_scan_group_and_custom_prefix()
    test_strip_marker()
    test_pending_lifecycle()
    test_enforce_behavior()
    print("强制出图测试全部通过")
