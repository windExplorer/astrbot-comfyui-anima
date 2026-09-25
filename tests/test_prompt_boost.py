"""提示词丰富化主链路（v7.6.0）：风格分档 / 扩写触发 / 外观护栏 / 丰富化总入口。

这些方法住在 main.py（依赖 astrbot 运行时，本地装不了），沿用 tests/test_size_helpers.py
的做法：用 ast 把源码摘出来，配一个假 self 执行。

跑法：python tests/test_prompt_boost.py
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

import prompt_guard  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


class _Log:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _load(want_methods: set[str], want_modfuncs: set[str], want_consts: set[str]) -> dict:
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")   # main.py 带 BOM
    tree = ast.parse(src)
    ns: dict = {"re": re, "time": time, "asyncio": asyncio, "logger": _Log(),
                "prompt_guard": prompt_guard}
    _FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, _FUNCS) and node.name in want_modfuncs:
            exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)  # noqa: S102
        elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") in want_consts for t in node.targets):
            exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)  # noqa: S102
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    for node in cls.body:
        if isinstance(node, _FUNCS) and node.name in want_methods:
            seg = ast.get_source_segment(src, node) or ""
            body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
            exec(textwrap.dedent(body), ns)  # noqa: S102
    missing = (want_methods | want_modfuncs | want_consts) - set(ns)
    assert not missing, f"没摘到: {missing}"
    return ns


NS = _load(
    {"_prompt_boost_cfg", "_prompt_style", "_should_enhance", "_guard_appearance",
     "_boost_positive"},
    {"_default_prompt_boost_cfg"},
    {"_APPEARANCE_NOUNS", "_APPEARANCE_CHANGE_RE"},
)


class _FakePlugin:
    """假 self：只实现这些方法依赖的最小接口。"""

    def __init__(self, boost=None, provider="prov-1", enrich=None, hard_cap=100):
        self.boost = dict(NS["_default_prompt_boost_cfg"]())
        self.boost.update(boost or {})
        self.provider = provider
        self._enrich = enrich
        self.hard_cap = hard_cap
        self.enhance_calls = 0

    # --- 被依赖的原方法 ---
    def _cfg(self, key, default=None):
        if key == "prompt_boost":
            return self.boost
        if key == "draw_wait_hard_cap":
            return self.hard_cap
        if key == "llm_rewrite_timeout":
            return 60
        if key == "character_card":
            return {"enabled": True}
        return default

    def _basemodel_of_workflow(self, wf):
        return (wf or {}).get("_bm") or {}

    def _resolve_translate_provider_id(self):
        return self.provider

    # --- 被替换的 LLM 扩写（免网络） ---
    async def _enhance_prompt_llm(self, positive, *, style="tags"):
        self.enhance_calls += 1
        if isinstance(self._enrich, Exception):
            raise self._enrich
        if callable(self._enrich):
            return self._enrich(positive)
        return self._enrich if self._enrich is not None else positive


for _name in ("_prompt_boost_cfg", "_prompt_style", "_should_enhance", "_guard_appearance",
              "_boost_positive"):
    setattr(_FakePlugin, _name, NS[_name])


def _p(boost=None, **kw):
    return _FakePlugin(boost=boost, **kw)


def test_prompt_style():
    pl = _p()
    assert NS["_prompt_style"](pl, {"name": "动漫日常", "is_anima": True}) == "tags"
    assert NS["_prompt_style"](pl, {"_bm": {"name": "Pony Diffusion V6"}}) == "pony"
    assert NS["_prompt_style"](pl, {"_bm": {"name": "Flux.1-dev"}}) == "natural"
    assert NS["_prompt_style"](pl, {"_bm": {"name": "随便", "prompt_style": "natural"}}) == "natural"
    assert NS["_prompt_style"](pl, {"_bm": {"name": "Anima Pencil XL"}}) == "tags"
    assert NS["_prompt_style"](pl, {"name": "没标注"}) == "unknown"
    print("== 1. 底模风格分档（tags/pony/natural/unknown） OK")


def test_should_enhance():
    pl = _p()
    ok, _ = NS["_should_enhance"](pl, "1girl, smile, night", "tags", pl.boost)
    assert ok, "短描述 + 标签系应触发扩写"
    # 已经够丰富
    long_text = ", ".join(f"tag{i}" for i in range(12))
    ok, why = NS["_should_enhance"](pl, long_text, "tags", pl.boost)
    assert not ok and "标签数" in why, why
    # 含角色 tag → 外观冻结，不扩写
    ok, why = NS["_should_enhance"](pl, "belle \\(zenless zone zero\\), night", "tags", pl.boost)
    assert not ok and "角色" in why, why
    # 命中角色卡名单 → 同样冻结
    ok, why = NS["_should_enhance"](pl, "1girl, 星野, night", "tags", pl.boost,
                                    known_names=("星野", "薄荷"))
    assert not ok and "角色" in why, why
    # 自然语言系默认不扩写
    ok, why = NS["_should_enhance"](pl, "a girl at night", "natural", pl.boost)
    assert not ok and "底模风格" in why, why
    # 关掉「只标签系」后，自然语言系也允许（走整句扩写）
    pl2 = _p({"enhance_only_tag_family": False})
    ok, _ = NS["_should_enhance"](pl2, "a girl at night", "natural", pl2.boost)
    assert ok
    # 无可用 LLM
    pl3 = _p(provider="")
    ok, why = NS["_should_enhance"](pl3, "1girl, night", "tags", pl3.boost)
    assert not ok and "LLM" in why, why
    # 预算不足（已耗时超过硬上限的 35%）
    pl4 = _p(hard_cap=100)
    ok, why = NS["_should_enhance"](pl4, "1girl, night", "tags", pl4.boost,
                                    draw_start=time.time() - 90)
    assert not ok and "预算" in why, why
    # 空提示词
    ok, _ = NS["_should_enhance"](pl4, "   ", "tags", pl4.boost)
    assert not ok
    print("== 2. 扩写触发条件（短描述/角色冻结/风格/LLM/预算） OK")


def test_guard_appearance():
    pl = _p()
    hits = [({"name": "薄荷"}, {"name": "默认", "positive": "long hair, green hair, green eyes"})]
    out = NS["_guard_appearance"](pl, "1girl, white hair, smile, night", hits)
    assert "white hair" not in out and "smile" in out and "night" in out, out
    # 用户明确要改外观 → 放行（两种语序都认）
    for _said in ("把头发染成粉色", "换成红裙子", "眼睛改成红色"):
        out2 = NS["_guard_appearance"](pl, "1girl, pink hair, night", hits, user_text=_said)
        assert "pink hair" in out2, (_said, out2)
    # 只是普通说话 → 不放行（照剥）
    out2b = NS["_guard_appearance"](pl, "1girl, pink hair, night", hits,
                                    user_text="画一张樱花树下的图")
    assert "pink hair" not in out2b, out2b
    # 开关关闭 → 不动
    pl2 = _p({"guard_appearance": False})
    raw = "1girl, white hair, night"
    assert NS["_guard_appearance"](pl2, raw, hits) == raw
    # 没命中角色卡 → 不动
    assert NS["_guard_appearance"](pl, raw, []) == raw
    # 多人（两组锚点）：括号分组不剥，组外冲突照剥
    multi_hits = [
        ({"name": "A"}, {"name": "默认", "positive": "green hair, cat ears"}),
        ({"name": "B"}, {"name": "默认", "positive": "white hair, fox ears"}),
    ]
    multi = "2girls, (cat ears, green hair:1.20), white hair, night"
    out3 = NS["_guard_appearance"](pl, multi, multi_hits)
    assert "(cat ears, green hair:1.20)" in out3 and "night" in out3, out3
    print("== 3. 外观护栏（冲突剥除/用户改外观放行/多人分组保护） OK")


def test_boost_positive():
    async def _run(pl, text, **kw):
        return await NS["_boost_positive"](pl, text, **kw)

    # ① 扩写只加自由维度 → 保留；画质前缀补上
    pl = _p(enrich="1girl, smile, night, depth of field, backlighting")
    out = asyncio.run(_run(pl, "1girl, smile, night", style="tags"))
    assert "depth of field" in out and "backlighting" in out, out
    assert out.startswith("masterpiece"), out
    assert "1girl, smile, night" in out
    # ② 扩写偷偷加了外观 → 剥掉外观、保留自由维度
    pl2 = _p(enrich="1girl, smile, night, white hair, rim light")
    out2 = asyncio.run(_run(pl2, "1girl, smile, night", style="tags"))
    assert "white hair" not in out2 and "rim light" in out2, out2
    # ③ 扩写丢了原文标签 → 整条放弃（保留原文）
    pl3 = _p(enrich="night, smile, bokeh")
    out3 = asyncio.run(_run(pl3, "1girl, smile, night", style="tags"))
    assert "1girl, smile, night" in out3 and "bokeh" not in out3, out3
    # ④ 扩写异常 → 保留原文，但仍补前缀
    pl4 = _p(enrich=RuntimeError("LLM 挂了"))
    out4 = asyncio.run(_run(pl4, "1girl, night", style="tags"))
    assert "1girl, night" in out4 and out4.startswith("masterpiece"), out4
    # ⑤ 自然语言系：不加质量词，也不扩写
    pl5 = _p(enrich="a girl at night, depth of field")
    out5 = asyncio.run(_run(pl5, "a girl at night", style="natural"))
    assert out5 == "a girl at night", out5
    assert pl5.enhance_calls == 0
    # ⑥ 关闭画质前缀
    pl6 = _p({"quality_prefix": False}, enrich="1girl, night, bokeh")
    out6 = asyncio.run(_run(pl6, "1girl, night", style="tags"))
    assert "bokeh" in out6 and "masterpiece" not in out6, out6
    # ⑦ 已有画质词 → 不重复加
    pl7 = _p({"enhance_llm": False})
    out7 = asyncio.run(_run(pl7, "masterpiece, 1girl", style="tags"))
    assert out7.count("masterpiece") == 1, out7
    # ⑧ 平台风格 nai 档
    pl8 = _p({"enhance_llm": False})
    out8 = asyncio.run(_run(pl8, "1girl, night", platform_style="nai"))
    assert out8.startswith("best quality") and "masterpiece" not in out8, out8
    # ⑨ 关掉扩写 → 一次都不调
    pl9 = _p({"enhance_llm": False}, enrich="1girl, night, bokeh")
    out9 = asyncio.run(_run(pl9, "1girl, night", style="tags"))
    assert pl9.enhance_calls == 0 and "bokeh" not in out9, out9
    # ⑩ 空提示词原样返回
    assert asyncio.run(_run(pl9, "", style="tags")) == ""
    print("== 4. 丰富化总入口（扩写护栏/前缀/异常回退/平台档） OK")


if __name__ == "__main__":
    test_prompt_style()
    test_should_enhance()
    test_guard_appearance()
    test_boost_positive()
    print("提示词丰富化主链路全部通过")
