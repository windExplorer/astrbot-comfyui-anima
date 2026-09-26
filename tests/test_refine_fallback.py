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
    def __init__(self, *, refine_on=True, rewrite_ok=True, translate_result="anima tags EN",
                 qwen=False, qwen_ok=True, qwen_result="The image is a square photorealistic close-up of a cat.",
                 sheet=False, sheet_ok=True, sheet_result="白底 CG 少女设定板 · 左侧主视觉大全身立绘……"):
        self.refine_on = refine_on
        self.rewrite_ok = rewrite_ok
        self.translate_result = translate_result
        self.qwen = qwen
        self.qwen_ok = qwen_ok
        self.qwen_result = qwen_result
        self.sheet = sheet
        self.sheet_ok = sheet_ok
        self.sheet_result = sheet_result
        self.translate_called = 0
        self.rewrite_calls = 0
        self.qwen_calls = 0
        self.qwen_edit_flag = None
        self.sheet_calls = 0

    def _cfg(self, key, default=None):
        return self.refine_on if key == "third_party_llm_refine" else default

    def _is_qwen_image(self, wf):
        return self.qwen

    def _is_char_sheet_mode(self, wf, text):
        return bool(self.sheet and self._is_qwen_image(wf))

    async def _rewrite_to_char_sheet_llm(self, text):
        self.sheet_calls += 1
        if not self.sheet_ok:
            raise RuntimeError("设定板改写未配置可用模型")
        return self.sheet_result

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

    async def _rewrite_to_qwen_llm(self, text, *, edit_mode=False):
        self.qwen_calls += 1
        self.qwen_edit_flag = edit_mode
        if not self.qwen_ok:
            raise RuntimeError("Qwen 改写未配置可用模型")
        return self.qwen_result

    async def _translate_prompt(self, wf, text):
        self.translate_called += 1
        return self.translate_result


def _run(fn, self_, wf, positive, source, has_input_image=False):
    return asyncio.run(fn(self_, wf, positive, source, "t-1", has_input_image))


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

    # ⑨ Qwen 文生图 + 第三方调用 → 按官方规范改写（不走 anima/real 链路）
    s = FakeSelf(qwen=True)
    got = _run(fn, s, real, zh, "partner")
    assert got.startswith("The image is"), got
    assert s.qwen_calls == 1 and s.qwen_edit_flag is False
    assert s.rewrite_calls == 0 and s.translate_called == 0

    # ⑩ Qwen 文生图 + 改写失败 + 中文 → 回退翻译（文生图正文必须英文）
    s = FakeSelf(qwen=True, qwen_ok=False)
    got = _run(fn, s, real, zh, "partner")
    assert got == "anima tags EN", got
    assert s.translate_called == 1

    # ⑪ Qwen 图像编辑（有输入图）+ 第三方调用 → 用编辑规范（edit_mode=True）
    s = FakeSelf(qwen=True)
    got = _run(fn, s, real, zh, "partner", has_input_image=True)
    assert got.startswith("The image is"), got
    assert s.qwen_calls == 1 and s.qwen_edit_flag is True

    # ⑫ Qwen 图像编辑 + 改写失败 + 中文 → 原样（编辑规范允许中文正文，不翻译）
    s = FakeSelf(qwen=True, qwen_ok=False)
    got = _run(fn, s, real, zh, "partner", has_input_image=True)
    assert got == zh, got
    assert s.translate_called == 0

    # ⑬ Qwen 文生图 + 关闭 LLM 整理 + 第三方 + 中文 → 翻译兜底（文生图必须英文）
    s = FakeSelf(qwen=True, refine_on=False)
    got = _run(fn, s, real, zh, "partner")
    assert got == "anima tags EN", got
    assert s.qwen_calls == 0 and s.translate_called == 1

    # ⑭ Qwen 文生图 + 原生调用 + 中文 → 按规范改写；纯英文 → 不打扰
    s = FakeSelf(qwen=True)
    got = _run(fn, s, real, zh, "")
    assert got.startswith("The image is") and s.qwen_calls == 1
    s = FakeSelf(qwen=True)
    got = _run(fn, s, real, "a red bicycle leaning on a brick wall", "")
    assert got == "a red bicycle leaning on a brick wall"
    assert s.qwen_calls == 0 and s.translate_called == 0

    print("== 提示词回退链（14 组：改写/回退翻译/关闭整理/标签系/Qwen 文生图/Qwen 编辑/原生） OK")


def test_char_sheet():
    """角色设定板（三视图/四视图）分支（v7.7.43）。"""
    fn = _load_refine()
    real = {"is_anima": False}
    zh = "画一个白底 CG 少女设定板，三视图"

    # ⑮ Qwen 底模 + 设定板意图 + 第三方调用 → 按设定板规范改写（不走通用 Qwen / 翻译）
    s = FakeSelf(qwen=True, sheet=True)
    got = _run(fn, s, real, zh, "partner")
    assert got.startswith("白底 CG 少女设定板"), got
    assert s.sheet_calls == 1 and s.qwen_calls == 0 and s.translate_called == 0
    assert "t-1" in s._sheet_ok_traces, "应打标以便后续跳过英文锚点注入"

    # ⑯ 设定板改写失败 + 中文 → 原样（纯中文规范，绝不翻译成英文）
    s = FakeSelf(qwen=True, sheet=True, sheet_ok=False)
    got = _run(fn, s, real, zh, "partner")
    assert got == zh, got
    assert s.translate_called == 0 and s.qwen_calls == 0

    # ⑰ 关闭 LLM 整理 + 第三方 → 尊重配置：不改写、也不翻译
    s = FakeSelf(qwen=True, sheet=True, refine_on=False)
    got = _run(fn, s, real, zh, "partner")
    assert got == zh and s.sheet_calls == 0 and s.translate_called == 0

    # ⑱ 非 Qwen 底模 + 设定板意图 → 不走设定板规范（回落原有分支：写实清理）
    s = FakeSelf(qwen=False, sheet=True)
    got = _run(fn, s, real, zh, "partner")
    assert got == "写实中文清理结果", got
    assert s.sheet_calls == 0 and s.rewrite_calls == 1

    # ⑲ 设定板 + 原生调用 + 纯英文（无中文）→ 仍按规范改写
    s = FakeSelf(qwen=True, sheet=True)
    got = _run(fn, s, real, "character sheet for a white-haired girl, turn-around", "")
    assert got.startswith("白底 CG 少女设定板") and s.sheet_calls == 1

    print("== 角色设定板分支（5 组：规范改写/失败保留中文/关闭整理/非Qwen不启用/原生英文） OK")


def test_char_sheet_doc_and_context():
    """规范文档读取 + 用户点名的角色卡资料会带进设定板改写输入。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    fn_doc = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_char_sheet_skill_text")
    fn_ctx = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_char_sheet_context")
    ns = {"logger": _Log(), "Path": Path, "__file__": str(ROOT / "main.py")}
    exec(compile(ast.get_source_segment(src, fn_doc), "<x>", "exec"), ns)  # noqa: S102
    exec(compile(ast.get_source_segment(src, fn_ctx), "<x>", "exec"), ns)  # noqa: S102
    read_doc, build_ctx = ns["_char_sheet_skill_text"], ns["_char_sheet_context"]

    class DocSelf:
        _SHEET_SKILL_DIR = "skills/character-sheet"
        _SHEET_SKILL_FILE = "character-design-sheet.md"
        _SHEET_RULE = "内置精简规则（不该被用到）"

    doc = read_doc(DocSelf())
    assert "角色设定板生成器 v5.4" in doc, doc[:160]
    assert "内置精简规则" not in doc, "应读到 skills/character-sheet/ 下的规范文档"

    class FakeStore:
        def list_characters(self):
            return [{"id": 1, "name": "白芷", "aliases": ["baizhi"],
                     "work": "《测试》", "note": "银发红瞳的少女剑士"}]

        def get_anchor(self, cid):
            return {"positive": "1girl, silver hair, red eyes", "negative": "lowres"}

    class CtxSelf:
        character = FakeStore()

    ctx = build_ctx(CtxSelf(), "给白芷画一张三视图设定板")
    assert "银发红瞳的少女剑士" in ctx, ctx
    assert "silver hair" in ctx and "禁止出现在输出里" in ctx, ctx
    # 没点名的角色不进上下文
    assert build_ctx(CtxSelf(), "画一张三视图设定板") == ""
    print("== 设定板规范文档 + 角色资料上下文（点名才带、英文标签转写要求） OK")


def test_qwen_skill_doc():
    """Qwen 规范文档必须真的能被读到（否则悄悄退回内置精简规则，用户以为在用 skill）。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == "_qwen_skill_text")
    attrs = {}
    for node in cls.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id.startswith("_QWEN_"):
            attrs[node.targets[0].id] = ast.literal_eval(node.value)
    # __file__ 指向 main.py —— 真身用 Path(__file__).parent 定位插件根下的 skills/
    ns = {"logger": _Log(), "Path": Path, "__file__": str(ROOT / "main.py")}
    exec(compile(ast.get_source_segment(src, fn), "<x>", "exec"), ns)  # noqa: S102
    read_skill = ns["_qwen_skill_text"]

    class DocSelf:
        pass

    d = DocSelf()
    for k, v in attrs.items():
        setattr(d, k, v)

    t2i = read_skill(d, False)
    edit = read_skill(d, True)
    # 规范文档标题里有「生成规范」，内置精简规则里没有 —— 用它区分「读了文件」与「退回内置」
    assert "Qwen-Image-2.1" in t2i and "生成规范" in t2i, t2i[:160]
    assert "Qwen-Image-2.1" in edit and "生成规范" in edit, edit[:160]
    assert "三视图" in t2i, "规范文档内容不完整"
    print("== Qwen-Image 规范文档读取（文生图 / 图像编辑两套均命中 skills/qwen-image/） OK")


if __name__ == "__main__":
    test_refine_fallback()
    test_qwen_skill_doc()
    test_char_sheet()
    test_char_sheet_doc_and_context()
    print("提示词回退链 + Qwen 规范 + 角色设定板全部通过")
