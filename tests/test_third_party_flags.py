"""第三方插件调用参数：silent（静默生图） / raw_prompt（跳过 LLM 处理）（v7.7.46）。

覆盖：
  ① `_coerce_bool_flag`：字符串/数字/中文布尔都能解析，不认识时用默认值；
  ② `_is_silent_call` + `_send`：静默时**不发文字**（不静默时照发）；
  ③ `_wrap_silent`：静默作用域挂标记 → 迭代结束（含调用方提前 break）后清理；
  ④ `_boost_positive(skip_llm=True)`：跳过 LLM 扩写（raw_prompt 语义）；
  ⑤ 工具签名：comfyui_draw / comfyui_img2img 有 silent + raw_prompt，_do_draw 有 raw_prompt。

跑法：python tests/test_third_party_flags.py
"""

import ast
import asyncio
import sys
import time  # noqa: F401  （_boost_positive 内部用到）
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import prompt_guard  # noqa: E402  （本地模块，无 astrbot 依赖）


class _Log:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


class _FakeChain:
    def __init__(self, chain=None):
        self.chain = list(chain or [])


class _FakePlain:
    def __init__(self, text=""):
        self.text = text


def _source() -> str:
    return (ROOT / "main.py").read_text(encoding="utf-8-sig")


def _cls(tree):
    return next(n for n in tree.body if isinstance(n, ast.ClassDef))


def _base_ns() -> dict:
    """摘出来的代码用到的模块级名字（注解 / 消息链类）都要给个替身。"""
    return {
        "logger": _Log(),
        "__file__": str(ROOT / "main.py"),
        "AstrMessageEvent": object,
        "MessageChain": _FakeChain,
        "Plain": _FakePlain,
    }


def _load_methods(names: tuple[str, ...], consts: tuple[str, ...] = (),
                  ns_extra: dict | None = None) -> dict:
    """从 main.py 的插件类里摘方法 + 类级常量（测试跑真身逻辑，不复制一份）。"""
    src = _source()
    tree = ast.parse(src)
    cls = _cls(tree)
    ns = _base_ns()
    if ns_extra:
        ns.update(ns_extra)
    out: dict = {}
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), "<x>", "exec"), ns)  # noqa: S102
            out[node.name] = ns[node.name]
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in consts:
            out[node.targets[0].id] = ast.literal_eval(node.value)
    return out


def _load_module_funcs(names: tuple[str, ...], ns_extra: dict | None = None) -> dict:
    """摘模块级函数（如 _coerce_bool_flag）。"""
    src = _source()
    tree = ast.parse(src)
    ns = _base_ns()
    if ns_extra:
        ns.update(ns_extra)
    out: dict = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), "<x>", "exec"), ns)  # noqa: S102
            out[node.name] = ns[node.name]
    return out


# ---------------------------------------------------------------- ① 布尔解析
def test_coerce_bool_flag():
    fn = _load_module_funcs(("_coerce_bool_flag",))["_coerce_bool_flag"]
    assert fn(None, True) is True
    assert fn(None, False) is False
    assert fn(True, False) is True
    assert fn("true", False) is True
    assert fn("False", True) is False          # 关键：bool("false") 会是 True，这里必须为 False
    assert fn("1", False) is True
    assert fn("0", True) is False
    assert fn("是", False) is True
    assert fn("否", True) is False
    assert fn("", True) is False
    assert fn("???", True) is True             # 无法识别 → 用默认值
    assert fn("???", False) is False
    print("== _coerce_bool_flag（字符串/数字/中文布尔/未知回落） OK")


# ------------------------------------------------- ② 静默时 _send 不发文字
class _FakeEvent:
    def __init__(self, silent: bool = False):
        self.sent = []
        self._anima_silent_trace = "1" if silent else ""

    async def send(self, chain):
        self.sent.append(chain)


def test_send_silenced():
    members = _load_methods(("_send", "_is_silent_call"), ("_SILENT_ATTR",),
                            {"MessageChain": _FakeChain, "Plain": _FakePlain})

    class S:
        _SILENT_ATTR = members["_SILENT_ATTR"]
        _is_silent_call = members["_is_silent_call"]
        _send = members["_send"]

    s = S()
    ev = _FakeEvent(silent=False)
    asyncio.run(s._send(ev, "正常提示"))
    assert len(ev.sent) == 1, "非静默时应当发文字"

    ev2 = _FakeEvent(silent=True)
    asyncio.run(s._send(ev2, "不该发出去的提示"))
    assert ev2.sent == [], f"静默时不应发文字，实际发了 {len(ev2.sent)} 条"
    assert s._is_silent_call(ev2) is True
    print("== _send 静默短路（非静默照发 / 静默不发） OK")


# --------------------------------------- ③ _wrap_silent 作用域与清理
def test_wrap_silent_scope():
    members = _load_methods(("_is_silent_call", "_wrap_silent"), ("_SILENT_ATTR",))

    class S:
        _SILENT_ATTR = members["_SILENT_ATTR"]
        _is_silent_call = members["_is_silent_call"]
        _wrap_silent = members["_wrap_silent"]

    s = S()

    async def _gen(values):
        for v in values:
            yield v

    async def run():
        ev = _FakeEvent(silent=False)
        # silent=False：不加标记、原样透传
        got = [x async for x in s._wrap_silent(_gen([1, 2, 3]), ev, False)]
        assert got == [1, 2, 3] and s._is_silent_call(ev) is False

        # silent=True：迭代期间带标记，正常结束后清理
        ev2 = _FakeEvent(silent=False)
        inside = []
        async for x in s._wrap_silent(_gen([1, 2]), ev2, True):
            inside.append((x, s._is_silent_call(ev2)))
        assert inside == [(1, True), (2, True)], inside
        assert s._is_silent_call(ev2) is False, "迭代结束后必须清理静默标记"

        # 调用方提前中断：生成器被关闭（aclose）时同样要清理 —— 真实链路里工具层
        # 完整消费、异常也会传播进生成器，所以正常与异常路径都会走到 finally。
        ev3 = _FakeEvent(silent=False)
        agen = s._wrap_silent(_gen([1, 2, 3]), ev3, True)
        async for _x in agen:
            break
        await agen.aclose()
        assert s._is_silent_call(ev3) is False, "生成器关闭后必须清理静默标记"

    asyncio.run(run())
    print("== _wrap_silent（透传 / 迭代期带标记 / 结束与提前 break 都清理） OK")


# ------------------------------------- ④ raw_prompt：跳过 LLM 扩写
class _FakeBoostSelf:
    def __init__(self):
        self.enhance_calls = 0

    def _should_enhance(self, out, style, cfg, known_names=(), draw_start=0.0):
        return True, "测试强制扩写"

    async def _enhance_prompt_llm(self, out, style="unknown"):
        self.enhance_calls += 1
        return out + ", cinematic lighting"


def test_boost_skip_llm():
    fn = _load_methods(("_boost_positive",), (),
                       {"prompt_guard": prompt_guard, "time": time})["_boost_positive"]
    cfg = {"enhance_llm": True, "quality_prefix": False}

    s1 = _FakeBoostSelf()
    out1 = asyncio.run(fn(s1, "1girl, solo", style="tags", cfg=cfg, skip_llm=False))
    assert s1.enhance_calls == 1, "默认（skip_llm=False）应当走 LLM 扩写"
    assert "cinematic lighting" in out1, out1

    s2 = _FakeBoostSelf()
    out2 = asyncio.run(fn(s2, "1girl, solo", style="tags", cfg=cfg, skip_llm=True))
    assert s2.enhance_calls == 0, "raw_prompt（skip_llm=True）不应调用 LLM 扩写"
    assert out2.strip() == "1girl, solo", f"提示词应原样保留，实际 {out2!r}"
    print("== _boost_positive（skip_llm 时跳过 LLM 扩写、原样保留） OK")


# --------------------------------------------------- ⑤ 工具签名/接线检查
def test_signatures_and_wiring():
    src = _source()
    tree = ast.parse(src)
    cls = _cls(tree)
    funcs = {n.name: n for n in cls.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    for tool in ("llm_draw", "llm_img2img"):
        args = [a.arg for a in funcs[tool].args.args] + [a.arg for a in funcs[tool].args.kwonlyargs]
        assert "silent" in args, f"{tool} 缺少 silent 参数"
        assert "raw_prompt" in args, f"{tool} 缺少 raw_prompt 参数"
    assert "raw_prompt" in [a.arg for a in funcs["_do_draw"].args.args] \
        or "raw_prompt" in [a.arg for a in funcs["_do_draw"].args.kwonlyargs], "_do_draw 缺少 raw_prompt"
    assert "raw_prompt" in [a.arg for a in funcs["_do_draw_nai_style"].args.kwonlyargs], \
        "_do_draw_nai_style 缺少 raw_prompt"

    # raw_prompt 必须真的让 LLM 调度让路
    assert "if not _fixed_prompt and not raw_prompt:" in src, "raw_prompt 未接入 LLM 调度条件"
    assert "skip_llm=raw_prompt" in src, "raw_prompt 未传给提示词丰富化"
    # 静默：卡片与文字两个公共出口都要短路；图片发送不受影响
    assert "_is_silent_call(event)" in src
    assert "notify_pending=False, source=source,\n                        raw_prompt=raw_prompt," in src
    print("== 工具签名与接线（silent/raw_prompt 贯穿 _do_draw / 平台 / 续画 / boost） OK")


if __name__ == "__main__":
    test_coerce_bool_flag()
    test_send_silenced()
    test_wrap_silent_scope()
    test_boost_skip_llm()
    test_signatures_and_wiring()
    print("第三方调用参数（silent / raw_prompt）全部通过")
