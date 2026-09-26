"""第三方插件调用的提示词回退链（v7.7.41）。

覆盖 `_llm_refine_prompt` 的真实逻辑（ast 摘出函数 + fake self）：
  ① 标签系（必须英文）+ LLM 整理成功 → 用改写结果；
  ② 标签系 + 整理失败（无模型/超时）+ 中文 → **回退翻译**；
  ③ 标签系 + 关闭整理（third_party_llm_refine=false）+ 中文 → 仍走翻译；
  ④ 标签系 + 关闭整理 + 纯英文 → 原样（不翻译）；
  ⑤ 非标签系（写实）+ 关闭整理 → 原样（中文可直接用）；
  ⑥ 非标签系 + 整理失败 → 原样（不翻译）；
  ⑦ 原生调用 + 标签系 + 中文 → 翻译（旧行为不回归）。

跑法：python tests/test_refine_fallback.py
"""

import ast
import asyncio
import re
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


def _load_refine():
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    fn = next(n for n in cls.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_llm_refine_prompt")
    ns = {"logger": _Log(), "re": re}
    exec(compile(ast.get_source_segment(src, fn), "<x>", "exec"), ns)  # noqa: S102
    return ns["_llm_refine_prompt"]


class FakeSelf:
    def __init__(self, *, refine_on=True, rewrite_ok=True, translate_result="anima tags EN"):
        self.refine_on = refine_on
        self.rewrite_ok = rewrite_ok
        self.translate_result = translate_result
        self.translate_called = 0
        self.rewrite_calls = 0

    def _cfg(self, key, default=None):
        return self.refine_on if key == "third_party_llm_refine" else default

    @staticmethod
    def _has_chinese(text):
        return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))

    async def _rewrite_to_anima_llm(self, text):
        self.rewrite_calls += 1
        if not self.rewrite_ok:
            raise RuntimeError("LLM 改写未配置可用模型")
        return "1girl, solo, masterpiece" if text else ""

    async def _rewrite_to_real_llm(self, text):
        self.rewrite_calls += 1
        if not self.rewrite_ok:
            raise RuntimeError("LLM 清理未配置可用模型")
        return "写实中文清理结果"

    async def _translate_prompt(self, wf, text):
        self.translate_called += 1
        return self.translate_result


def _run(fn, self_, wf, positive, source):
    return asyncio.run(fn(self_, wf, positive, source, "t-1"))


def test_refine_fallback():
    fn = _load_refine()
    anima = {"is_anima": True}
    real = {"is_anima": False}
    zh = "一个女孩站在樱花树下"

    # ① 标签系 + 整理开 + 成功
    s = FakeSelf()
    got = _run(fn, s, anima, zh, "partner")
    assert got == "1girl, solo, masterpiece", got
    assert s.translate_called == 0 and s.rewrite_calls == 1

    # ② 标签系 + 整理失败（无模型）→ 回退翻译（中文）
    s = FakeSelf(rewrite_ok=False)
    got = _run(fn, s, anima, zh, "partner")
    assert got == "anima tags EN", got
    assert s.translate_called == 1, s.translate_called

    # ③ 标签系 + 关闭整理 + 中文 → 仍翻译
    s = FakeSelf(refine_on=False)
    got = _run(fn, s, anima, zh, "partner")
    assert got == "anima tags EN", got
    assert s.translate_called == 1 and s.rewrite_calls == 0

    # ④ 标签系 + 关闭整理 + 纯英文 → 原样，不翻译
    s = FakeSelf(refine_on=False)
    got = _run(fn, s, anima, "1girl, solo, white hair", "partner")
    assert got == "1girl, solo, white hair", got
    assert s.translate_called == 0

    # ⑤ 非标签系 + 关闭整理 → 原样（中文可直接用）
    s = FakeSelf(refine_on=False)
    got = _run(fn, s, real, zh, "partner")
    assert got == zh and s.translate_called == 0

    # ⑥ 非标签系 + 整理失败 → 原样，不翻译
    s = FakeSelf(rewrite_ok=False)
    got = _run(fn, s, real, zh, "partner")
    assert got == zh and s.translate_called == 0

    # ⑦ 原生调用（source 空）+ 标签系 + 中文 → 翻译（旧行为）
    s = FakeSelf()
    got = _run(fn, s, anima, zh, "")
    assert got == "anima tags EN", got
    assert s.translate_called == 1 and s.rewrite_calls == 0

    # ⑧ 翻译本身失败（返回空）→ 保留原文
    s = FakeSelf(refine_on=False, translate_result="")
    got = _run(fn, s, anima, zh, "partner")
    assert got == zh, got

    print("== 提示词回退链（8 组：改写/回退翻译/关闭整理/非标签系/原生） OK")


if __name__ == "__main__":
    test_refine_fallback()
    print("提示词回退链全部通过")
