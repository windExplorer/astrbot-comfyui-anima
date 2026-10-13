"""底模「优先语种」契约回归（v7.7.65）。

背景（用户 2026-10-13 报）：底模库里明明配了「自然语言 + 优先语种=中文」，出图却仍是
**英文 + 标签**。根因是 `_llm_refine_prompt` 的语种处理**只有单向**：
「必须英文」的底模（标签系 / Qwen 非中文优先语种）有中→英翻译兜底，而
「优先语种=中文」的底模在提示词**本来就是英文标签**时，链路上没有任何一步把它改回中文——
v7.7.54 的中文改写只在"输入含中文"时触发。于是 AI 自己写成英文标签时，设置形同没有。

修复（v7.7.65）：优先语种改成**双向契约**——不论输入是中文还是英文写法，只要与底模契约
不符就按契约归一化一次：
  · Qwen 家族 → 走官方规范改写（lang=优先语种）；
  · 其它自然语言系 → 走 `_rewrite_to_natural_llm`（新增，措辞中立，只做语种/写法归一化）；
  · 标签系（`wf.is_anima`，即 Danbooru 底模）仍以**英文标签**为契约，不受影响；
  · 优先语种**未配**（旧数据）一律不动，维持旧行为。

跑法：python tests/test_prompt_lang_contract.py
"""

import ast
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED = []

WANT_FUNCS = (
    "_llm_refine_prompt",
    "_need_prompt_refine",
    "_resolve_prompt_plan",
    "_prompt_skill_of",
    "_is_qwen_image",
    "_is_char_sheet_mode",
    "_is_char_sheet_request",
    "_looks_english",
)
WANT_CONSTS = ("_PROMPT_SKILLS",)

EN_TAGS = ("1girl, silver hair, blue eyes, snow field, night, winter coat, "
           "looking at viewer, masterpiece, best quality")
ZH_TEXT = "雪地里站着一位银发蓝眼的少女，穿着冬装，正看向镜头"


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"FAIL: {msg}")


class _Log:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


def _load_real_defs() -> dict:
    """从 main.py 原样抠出真代码（含新增的 _looks_english）——测的是磁盘上的实现。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    ns = {"logger": _Log(), "re": re}
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in WANT_FUNCS:
            exec(compile(ast.get_source_segment(src, node), "<x>", "exec"), ns)  # noqa: S102
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in WANT_CONSTS:
                    exec(compile(ast.get_source_segment(src, node), "<x>", "exec"), ns)  # noqa: S102
    return ns


REAL = _load_real_defs()


class FakeSelf:
    """假 self：只桩掉 LLM 调用与底模查询，其余（plan/skill/qwen 判定）都用真代码。"""

    def __init__(self, base=None, *, source_is_anima=False):
        self.base = base or {}
        self.source_is_anima = source_is_anima
        self.qwen_calls = 0
        self.qwen_lang = None
        self.qwen_edit = None
        self.translate_calls = 0
        self.anima_calls = 0
        self.real_calls = 0
        self.sheet_calls = 0
        self.natural_calls = 0
        self.natural_lang = None
        self.cfg = {}

    # -- 底模 / 配置 ----------------------------------------------------------
    def _cfg(self, key, default=None):
        if key == "third_party_llm_refine":
            return default
        return self.cfg.get(key, default)

    def _basemodel_of_workflow(self, wf):
        return self.base

    # -- LLM 步骤（全部桩掉） --------------------------------------------------
    async def _translate_prompt(self, wf, text):
        self.translate_calls += 1
        return "TRANSLATED-EN-TAGS"

    async def _rewrite_to_qwen_llm(self, text, *, edit_mode=False, lang=""):
        self.qwen_calls += 1
        self.qwen_lang = lang
        self.qwen_edit = edit_mode
        return f"QWEN(lang={lang!r},edit={edit_mode})"

    async def _rewrite_to_natural_llm(self, text, lang="中文"):
        self.natural_calls += 1
        self.natural_lang = lang
        return f"NATURAL(lang={lang!r})"

    async def _rewrite_to_real_llm(self, text):
        self.real_calls += 1
        return "REAL-CN"

    async def _rewrite_to_anima_llm(self, text):
        self.anima_calls += 1
        return "ANIMA-EN"

    async def _rewrite_to_char_sheet_llm(self, text):
        self.sheet_calls += 1
        return "SHEET-CN"

    @staticmethod
    def _has_chinese(text):
        return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


for _n in WANT_FUNCS:
    if _n == "_looks_english":
        # 真身是 staticmethod，假的也必须按 staticmethod 挂（否则会被多传一个 self）
        FakeSelf._looks_english = staticmethod(REAL[_n])
    else:
        setattr(FakeSelf, _n, REAL[_n])
FakeSelf._PROMPT_SKILLS = REAL["_PROMPT_SKILLS"]


def _run(base, text, *, source="", has_image=False, is_anima=False, wf_extra=None):
    fake = FakeSelf(base, source_is_anima=is_anima)
    wf = {"name": "wf1", "prompt_skill": "auto"}
    if is_anima:
        wf["is_anima"] = True
    if wf_extra:
        wf.update(wf_extra)
    out = asyncio.run(REAL["_llm_refine_prompt"](fake, wf, text, source, "t-1", has_image))
    return out, fake


QWEN_CN = {"name": "Qwen Image 2.1", "prompt_style": "qwen", "priority_lang": "中文"}
NATURAL_CN = {"name": "Z-Image Turbo", "prompt_style": "natural", "priority_lang": "中文"}
NATURAL_EN = {"name": "Z-Image Turbo", "prompt_style": "natural", "priority_lang": "英文"}
NATURAL_NONE = {"name": "Some SDXL", "prompt_style": "natural", "priority_lang": ""}


def main() -> int:
    # ① Qwen + 优先语种=中文 + AI 写成英文标签 → 必须按中文规范改写（用户报的场景）
    out, s = _run(QWEN_CN, EN_TAGS)
    check(out.startswith("QWEN(") and s.qwen_lang == "中文",
          f"Qwen 底模配中文时，英文标签输入也必须按中文规范改写，实际 out={out!r} lang={s.qwen_lang!r}")

    # ② Qwen + 中文 + 中文输入 → 仍然按中文规范改写（v7.7.54 既有行为不回归）
    out, s = _run(QWEN_CN, ZH_TEXT)
    check(out.startswith("QWEN(") and s.qwen_lang == "中文",
          f"Qwen 底模配中文时，中文输入应走中文规范改写，实际 {out!r}")

    # ③ Qwen + 优先语种未配 + 英文输入 → 不动（Qwen 官方本来就吃英文长段）
    out, s = _run({"name": "Qwen Image 2.1", "prompt_style": "qwen", "priority_lang": ""}, EN_TAGS)
    check(out == EN_TAGS and s.qwen_calls == 0,
          f"Qwen 未配优先语种时不该多跑一次改写，实际 {out!r}")

    # ④ 自然语言底模 + 优先语种=中文 + 英文标签 → 按底模契约归一化为中文（v7.7.65 新增路径）
    out, s = _run(NATURAL_CN, EN_TAGS)
    check(out.startswith("NATURAL(") and s.natural_lang == "中文",
          f"自然语言底模配中文时，英文标签输入必须归一化成中文，实际 out={out!r} lang={s.natural_lang!r}")

    # ⑤ 自然语言底模 + 中文 + 中文输入 → 原样（别白跑一次 LLM）
    out, s = _run(NATURAL_CN, ZH_TEXT)
    check(out == ZH_TEXT and s.natural_calls == 0,
          f"输入已经符合底模契约时不该再改写，实际 {out!r}")

    # ⑥ 自然语言底模 + 优先语种未配（旧数据）+ 英文 → 原样，维持旧行为
    out, s = _run(NATURAL_NONE, EN_TAGS)
    check(out == EN_TAGS and s.natural_calls == 0,
          f"优先语种未配时不该改动提示词，实际 {out!r}")

    # ⑦ 标签系（is_anima，Danbooru 底模）+ 优先语种=中文 + 英文标签 → **不得**被改写成中文
    #    （标签系的契约就是英文标签；"优先语种=中文"对它只影响*中文*输入要不要翻译）
    out, s = _run({"name": "某一个 Danbooru 底模", "prompt_style": "danbooru",
                   "priority_lang": "中文", "danbooru_ready": True},
                  EN_TAGS, is_anima=True)
    check(out == EN_TAGS and s.natural_calls == 0 and s.translate_calls == 0,
          f"标签系底模的英文标签不得被中文化，实际 {out!r}")

    # ⑧ 标签系 + 中文优先语种 + 中文输入 → 仍然走翻译（既有行为）
    out, s = _run({"name": "某一个 Danbooru 底模", "prompt_style": "danbooru",
                   "priority_lang": "中文", "danbooru_ready": True},
                  ZH_TEXT, is_anima=True)
    check(out == "TRANSLATED-EN-TAGS" and s.translate_calls == 1,
          f"标签系底模收到中文仍应翻译为英文标签，实际 {out!r}")

    # ⑨ 自然语言底模 + 优先语种=英文 + 中文输入 → 按契约归一化为英文（反向对称）
    out, s = _run(NATURAL_EN, ZH_TEXT)
    check(out.startswith("NATURAL(") and s.natural_lang == "英文",
          f"底模配英文时中文输入应归一化为英文，实际 out={out!r} lang={s.natural_lang!r}")

    # ⑩ 第三方调用 + 自然语言底模 + 中文优先语种 + 英文标签 → 走既有「写实/中文清理」（不改动顺序）
    out, s = _run(NATURAL_CN, EN_TAGS, source="partner")
    check(out == "REAL-CN" and s.real_calls == 1,
          f"第三方调用应走既有清理路径，实际 {out!r}")

    # ⑪ Qwen 图像编辑（有输入图）+ 中文优先语种 + 英文 → **不**做语种归一化
    #    （Qwen 编辑规范：正文随指令语言；旧口径也只在该场景含中文时才进）
    out, s = _run(QWEN_CN, EN_TAGS, has_image=True)
    check(out == EN_TAGS and s.qwen_calls == 0,
          f"Qwen 图像编辑不该被语种契约强制改写，实际 {out!r}")

    # ⑫ 接线守卫：新路径真的接在链路上（否则测试桩再对也白搭）
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    check("_rewrite_to_natural_llm" in src, "缺少 _rewrite_to_natural_llm（语种归一化实现）")
    check(src.count("_try_natural(") >= 3,
          f"两个方向的归一化兜底都要接上（+1 处定义），当前 {src.count('_try_natural(')} 处")
    check("_looks_english" in src, "缺少 _looks_english（英文写法粗判）")
    check('阶段=按底模语种归一化提示词' in src, "缺少语种归一化的日志埋点")

    # ⑬ v7.7.66 调度闸门：`_need_prompt_refine` 必须与 `_llm_refine_prompt` 同口径。
    #     教训（用户 2026-10-14 日志）：v7.7.65 只改了 `_llm_refine_prompt` 内部，
    #     `_do_draw` 里那份手写闸门仍按旧口径（"原生调用只在含中文时整理"）→
    #     Qwen 底模配中文 + AI 写英文标签 → 判据 False → task 根本没创建
    #     （日志表现：`LLM 整理 0.0s`、一条 `【绘图·LLM*】` 都没有），修复等于没生效。
    need = REAL["_need_prompt_refine"]

    def _need(base, text, *, source="", has_image=False, is_anima=False, wf_extra=None):
        fake = FakeSelf(base, source_is_anima=is_anima)
        wf = {"name": "wf1", "prompt_skill": "auto"}
        if is_anima:
            wf["is_anima"] = True
        if wf_extra:
            wf.update(wf_extra)
        return need(fake, wf, text, source, has_image)

    check(_need(QWEN_CN, EN_TAGS) is True,
          "★用户场景：Qwen 底模配中文 + AI 写出英文标签 → 必须进 LLM 整理（否则修复不生效）")
    check(_need(QWEN_CN, ZH_TEXT) is True, "Qwen 底模配中文 + 中文输入 → 要进（按规范改写）")
    check(_need({"name": "Qwen Image 2.1", "prompt_style": "qwen",
                 "priority_lang": ""}, EN_TAGS) is False,
          "Qwen 未配优先语种 + 英文 → 不必空转一次整理")
    check(_need(NATURAL_CN, EN_TAGS) is True, "自然语言底模配中文 + 英文标签 → 要进（反向归一化）")
    check(_need(NATURAL_CN, ZH_TEXT) is False, "自然语言底模配中文 + 中文输入 → 无需整理")
    check(_need(NATURAL_EN, ZH_TEXT) is True, "自然语言底模配英文 + 中文输入 → 要进（反向归一化）")
    check(_need(NATURAL_NONE, EN_TAGS) is False, "优先语种未配（旧数据）→ 不进，维持旧行为")
    check(_need({"name": "Danbooru 底模", "prompt_style": "danbooru",
                 "danbooru_ready": True}, EN_TAGS, is_anima=True) is False,
          "标签系 + 英文标签 → 无需整理")
    check(_need({"name": "Danbooru 底模", "prompt_style": "danbooru",
                 "danbooru_ready": True}, ZH_TEXT, is_anima=True) is True,
          "标签系 + 中文 → 要进（翻译兜底）")
    check(_need(NATURAL_EN, EN_TAGS, wf_extra={"prompt_skill": "qwen-t2i"}) is True,
          "工作流显式关联 skill → 一律进")
    check(_need(NATURAL_CN, EN_TAGS, source="partner") is True, "第三方调用 + 整理开 → 进")
    check(_need(QWEN_CN, EN_TAGS, has_image=True) is False,
          "Qwen 图像编辑 + 英文 → 不进（正文随指令语言，别空转）")
    check(_need(QWEN_CN, ZH_TEXT, has_image=True) is True,
          "Qwen 图像编辑 + 中文 → 要进（按编辑规范改写）")
    check(src.count("_need_prompt_refine(") == 2,
          f"整理判据必须只有一份（定义 + `_do_draw` 调用），当前 {src.count('_need_prompt_refine(')} 处")
    check("_must_en_now" not in src,
          "`_do_draw` 里不应再有第二份手写判据（_must_en_now 应已删除）")

    if FAILED:
        print(f"\n{len(FAILED)} 项失败")
        return 1
    print("\n底模优先语种契约回归（Qwen/自然语言/标签系 × 中文/英文/未配）全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
