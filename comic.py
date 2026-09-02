"""表情包 / 漫画（带字）功能逻辑层。

集中存放「指令拆分（v5.5.0）」相关的纯逻辑：工作流类型判定、special_features
功能解析、槽位渲染、LLM 造词、槽位注入。所有函数均为模块级，首参 self 即插件实例
（ComfyUIDrawPlugin），内部通过 self._cfg / self._find_workflow_by_name /
self._lora_library / self.context.llm_generate 等访问配置与依赖方法；少数纯函数
（slot_var_hint / is_comic_intent）无 self。main.py 仅保留薄封装方法。
"""

import json
import logging
import re

from . import workflow_builder

logger = logging.getLogger(__name__)

# 三类带字功能的 key
COMIC_FEATURE_KEYS = ("meme_text", "meme_img", "comic")

# 表情包/漫画意图关键词：命中即判为用户想出「带文字」的表情包/漫画
_COMIC_INTENT_KEYWORDS = (
    "表情包", "表情图", "梗图", "气泡", "带字", "底部文字",
    "meme", "sticker", "comic", "漫画",
)


# --------------------------------------------------------------------------- #
# 工作流类型 / 功能解析
# --------------------------------------------------------------------------- #
def workflow_kind(self, wf: dict | None) -> str:
    """返回工作流类型：comic（带 prompt_slots 多槽位注入）或 draw。

    优先用显式 kind 字段；未设置（旧工作流）时按是否配了 prompt_slots 推断，
    保证旧配置向后兼容。
    """
    if not wf:
        return "draw"
    _k = (wf.get("kind") or "").strip().lower()
    if _k in ("comic", "draw"):
        return _k
    if normalize_prompt_slots(self, wf.get("prompt_slots")):
        return "comic"
    return "draw"


def feature_by_key(self, key: str) -> dict | None:
    """按 key 取 special_features 里的功能配置。

    兼容：当 special_features 为空且旧配置 default_comic_workflow 有值时，自动
    迁移出一条 meme_text 功能（仅 key=meme_text 时触发），让旧用户无缝过渡。
    """
    _raw = self._cfg("special_features", []) or []
    if isinstance(_raw, dict):
        _raw = _raw.get("value", []) or []
    for _f in (_raw or []):
        if (str(_f.get("key") or "").strip().lower()
                == (key or "").strip().lower()):
            return _f
    # 迁移：旧 default_comic_workflow → meme_text
    if not _raw and (key or "").strip().lower() == "meme_text":
        _legacy = (self._cfg("default_comic_workflow", "") or "").strip()
        if _legacy:
            return {
                "key": "meme_text",
                "name": "表情生成",
                "workflow": _legacy,
                "default_lora": "",
                "default_negative": "",
                "enabled": True,
            }
    return None


def resolve_comic_workflow(
    self, feature_key: str, wf_arg: str = ""
) -> tuple[str | None, str | None]:
    """按功能 key 解析漫画工作流名（带校验，不支持则返回友好错误）。

    feature_key: meme_text / meme_img / comic
    wf_arg: 用户 --wf 显式指定（可空）

    返回 (workflow_name, error_msg)；error_msg 非空即失败，调用方应中止并提示。
    """
    if (feature_key or "") not in COMIC_FEATURE_KEYS:
        return None, (
            f"未知功能类型：{feature_key}（应为 meme_text / meme_img / comic）"
        )
    _feat = feature_by_key(self, feature_key)
    if not _feat:
        return None, (
            f"未配置「{feature_key}」功能：请在插件「功能配置」页添加该功能并绑定一个"
            f"已配置 prompt_slots 的漫画工作流。"
        )
    if not _feat.get("enabled", True):
        return None, f"功能「{feature_key}」已禁用，当前不可用。"
    _bound = (_feat.get("workflow") or "").strip()
    _target = (wf_arg or "").strip() or _bound
    if not _target:
        return None, (
            f"功能「{feature_key}」未绑定工作流，且未用 --wf 指定。"
        )
    _wf = self._find_workflow_by_name(_target)
    if not _wf:
        return None, f"找不到工作流「{_target}」，请检查名称或到 WebUI 工作流列表确认。"
    if workflow_kind(self, _wf) != "comic":
        return None, (
            f"「{_target}」不是表情包/漫画工作流（未配置 prompt_slots），不能用于"
            f"「{feature_key}」。请改用已配置 prompt_slots 的工作流，或在「功能配置」重新绑定。"
        )
    if feature_key == "meme_img" and not (_wf.get("image_node") or "").strip():
        return None, (
            f"「{_target}」缺少 image_node（参考图节点），无法用于图生表情包（meme_img）。"
        )
    return _target, None


# --------------------------------------------------------------------------- #
# 旧式自动解析（兼容 _auto_comic_workflow / _resolve_comic_wf；逐步由上方替代）
# --------------------------------------------------------------------------- #
def auto_comic_workflow(self, requested: str) -> tuple[str | None, str | None]:
    """解析表情包/漫画要用的工作流名（仅限配置了 prompt_slots 的漫画工作流）。

    优先级：显式指定 > 配置 default_comic_workflow > 自动探测。
    显式指定/配置默认若不是漫画工作流，不报错卡死，回退自动探测。
    返回 (wf_name, error)；wf_name 为 None 时 error 给出友好提示。
    """
    _all = self._workflows()
    _comic_wf = [w for w in _all if normalize_prompt_slots(self, w.get("prompt_slots"))]
    _names = "、".join(f"「{w.get('name', '')}」" for w in _comic_wf) or "（未配置任何漫画工作流）"

    def _is_comic(name: str) -> bool:
        _w = self._find_workflow_by_name(name)
        return _w is not None and bool(normalize_prompt_slots(self, _w.get("prompt_slots")))

    _req = (requested or "").strip() or (self._cfg("default_comic_workflow", "") or "").strip()
    if _req and not _is_comic(_req):
        logger.warning(
            f"【漫画工作流】「{_req}」未配置 prompt_slots，不是漫画工作流，"
            f"回退到自动探测漫画工作流（可用：{_names}）。"
        )
        _req = ""
    if _req:
        return _req, None
    if len(_comic_wf) == 1:
        return (_comic_wf[0].get("name") or "").strip() or None, None
    if not _comic_wf:
        return None, (
            "未配置任何表情包/漫画工作流（需配置 prompt_slots 多槽位注入）。"
            "请在 WebUI 给某工作流配置 prompt_slots，或在配置里设置 default_comic_workflow 为漫画工作流。"
        )
    return None, (
        f"检测到多个漫画工作流：{_names}。请用 --wf <工作流名> 或 workflow 参数指定，"
        f"或在配置里设置 default_comic_workflow 默认值。"
    )


def resolve_comic_wf(self, requested: str, is_img2img: bool) -> tuple[str | None, str | None]:
    """在 auto_comic_workflow 基础上，对图生图场景优先选「带 image_node 的漫画工作流」。"""
    _name, _err = auto_comic_workflow(self, requested)
    if _err or not _name:
        return _name, _err
    _wf = self._find_workflow_by_name(_name) or {}
    if is_img2img and not (_wf.get("image_node") or "").strip():
        _alt = next(
            (w for w in self._workflows()
             if normalize_prompt_slots(self, w.get("prompt_slots"))
             and (w.get("image_node") or "").strip()),
            None,
        )
        if _alt:
            logger.info(
                f"【漫画工作流】「{_name}」无 image_node，图生图改选带 image_node 的"
                f"「{_alt.get('name')}」"
            )
            return (_alt.get("name") or ""), None
    return _name, None


# 否定词：识别「不要 X / 别发 X / no X」这类反向指令
_NEG_WORDS = (
    "不要", "别", "不加", "不用", "不是", "不想", "无需", "不许", "不能", "取消",
    "关掉", "关闭", "禁用", "去掉", "移除", "省略", "无", "没有",
    "no", "not", "without", "never", "none",
)


def _keyword_negated(text: str, index: int) -> bool:
    """判断 text 里位于 index 处的关键词是否被**紧邻**的否定词否定。

    只认「否定词末尾距关键词开头 ≤2 字」的情况，避免误伤：
    - 「不要发表情包」夹 1 字 → 命中（否定）
    - 「别人在做表情包」夹 3 字 → 不命中（肯定）
    """
    _prefix = text[max(0, index - 8):index]
    for _neg in _NEG_WORDS:
        _p = _prefix.rfind(_neg)
        if _p >= 0 and (len(_prefix) - _p - len(_neg)) <= 2:
            return True
    return False


def is_comic_intent(user_text: str, prompt: str = "") -> bool:
    """判断用户是否想要「带文字的表情包/漫画」。

    关键词命中即判为 meme 意图，**但被否定词修饰的关键词不算命中**。
    否则「画个猫，不要发表情包」会因为含「表情包」三个字被误判成想要表情包，
    既走错漫画工作流，又把「不要发表情包」当成画面描述语。
    """
    _t = f"{user_text or ''} {prompt or ''}".lower()
    for _k in _COMIC_INTENT_KEYWORDS:
        _start = 0
        while True:
            _i = _t.find(_k, _start)
            if _i < 0:
                break
            if not _keyword_negated(_t, _i):
                return True
            _start = _i + len(_k)
    return False


# 「不要发表情包」这类元指令：是对出图方式的否定要求，不是画面描述
_COMIC_NEG_PATTERN = re.compile(
    r"(不要|别|不加|不用|不是|不想|无需|不许|取消|去掉|移除|省略|no|not|without|never)"
    r"\s*[发画带做是用搞生成要]?\s*"
    r"(表情包|表情图|梗图|气泡|底部文字|旁白|漫画|meme|sticker|comic)",
    re.IGNORECASE,
)


def strip_comic_negations(text: str) -> str:
    """剔除用户原话里的『不要发表情包』类元指令，只留下真正的画面描述。

    这些句子若不清掉，会被当成描述语写进画面提示词 / 槽位造词，
    导致出图内容受污染（用户反馈："把『不要发表情包』当做描述语了"）。
    没有匹配到元指令时原样返回。
    """
    if not text:
        return text or ""
    _out = _COMIC_NEG_PATTERN.sub("", text)
    _out = re.sub(r"\s{2,}", " ", _out).strip(" ,，、。.;；")
    return _out or (text or "").strip()


def slot_vars(self, wf: dict) -> list[str]:
    """取出工作流 prompt_slots 的槽位变量名（去重、保序），用于直填模式按序映射。"""
    _slots = normalize_prompt_slots(self, wf.get("prompt_slots"))
    _vars: list[str] = []
    for _s in _slots:
        if not isinstance(_s, dict):
            continue
        for _v in (_s.get("vars") or []):
            _v = str(_v).strip()
            if _v and _v not in _vars:
                _vars.append(_v)
    return _vars


# --------------------------------------------------------------------------- #
# prompt_slots 归一化 / 槽位渲染
# --------------------------------------------------------------------------- #
def normalize_prompt_slots(self, raw) -> list:
    """把配置里的 prompt_slots（JSON 字符串或对象数组）归一化为列表。"""
    if isinstance(raw, str) and raw.strip():
        try:
            _p = json.loads(raw)
        except Exception:
            return []
        return _p if isinstance(_p, list) else ([_p] if isinstance(_p, dict) else [])
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [raw]
    return []


def slot_var_hint(var_name: str, slot: dict) -> str:
    """推断某个槽位变量的语义说明，供内部 LLM 造词时理解该写啥。

    优先用 slot.hints[var] 的自定义说明；否则按变量名特征给通用提示
    （bubble=角色台词、bottom=底部旁白、comic=整段分镜）。
    """
    _cat = _var_category(var_name)
    if _cat == "bottom":
        return (
            "【底部旁白】画面**最下方**整条横幅/字幕条上的文字——它不是角色说的话，"
            "是旁白、吐槽、真相点破或总结句（例：「这就是程序员的日常」「我裂开了」）。"
            "第三人称，概括整张图。务必短——建议 ≤6 字，最多不超过 10 字"
        )
    if _cat == "bubble":
        return (
            "【气泡台词】画面**内部**、人物旁边的白色气泡框里的文字——角色**亲口说出**的话，"
            "第一人称、口语化、带情绪（例：「我不会写代码！」「别催了」）。"
            "务必短——建议 ≤8 字，最多不超过 12 字"
        )
    if _cat == "comic":
        return "整段分镜/剧情描述：随格数展开，文字不限"
    _hints = slot.get("hints") if isinstance(slot.get("hints"), dict) else {}
    _custom = (_hints or {}).get(var_name)
    if _custom:
        return str(_custom)
    return f"该槽位（{var_name}）应写的文字（尽量简短）"


def _var_category(name: str) -> str:
    """把槽位变量归类到语义类别：bottom / bubble / comic / other。

    ⚠ 必须用**子串匹配**：真实配置里的变量名多是 ``bubble_text`` / ``bottom_text``
    （见 docs/comic-meme-design.md），裸等值匹配 ``"bubble"`` 会全部落空，
    导致 LLM 拿不到任何语义说明、把气泡和底部写反。
    先判 bottom，避免 ``bottom_caption`` 之类被气泡规则吃掉。
    """
    _n = (name or "").strip().lower()
    if _n == "sub" or any(k in _n for k in ("bottom", "subtitle", "底部", "旁白", "真相")):
        return "bottom"
    if any(k in _n for k in ("bubble", "caption", "speech", "气泡", "台词", "对话")):
        return "bubble"
    if any(k in _n for k in ("comic", "desc", "分镜", "剧情", "story")):
        return "comic"
    return "other"


# 用户「不要某位置文字」的否定词 / 目标词，用于确定性禁用（不依赖 LLM 是否听话）
_NEG_TOKENS = (
    "不要", "不加", "别加", "别写", "别放", "别", "去掉", "移除", "省略",
    "省去", "无", "没有", "不需要", "不用", "禁止", "omit", "remove",
    "without", "no", "none", "skip",
)
_BOTTOM_TARGETS = ("底部", "旁白", "字幕", "副标题", "caption", "bottom", "subtitle", "sub")
_BUBBLE_TARGETS = ("气泡", "台词", "对话", "caption", "bubble", "speech")


def _detect_disabled_slots(text: str, vars_list: list[dict]) -> set[str]:
    """识别用户原话里『不要某位置文字』的指令，返回应被禁用的槽位变量名集合。

    纯文本规则匹配（不依赖 LLM 是否听话），保证用户明确说『不要底部 / 不加旁白』
    时一定生效：命中后对应槽位强制返回空字符串，节点被清空、不出该位置文字。
    """
    if not text:
        return set()
    _t = text.lower()
    _disabled: set[str] = set()
    for _v in vars_list:
        _name = _v.get("name") or ""
        _cat = _var_category(_name)
        _targets = _BOTTOM_TARGETS if _cat == "bottom" else (_BUBBLE_TARGETS if _cat == "bubble" else None)
        if not _targets:
            continue
        for _neg in _NEG_TOKENS:
            _nl = _neg.lower()
            for _tg in _targets:
                _tg = _tg.lower()
                # 否定词与目标词都要出现，且否定词出现在目标词前/附近（避免『要底部』反向命中）
                if (_nl in _t) and (_tg in _t) and _t.find(_nl) <= _t.find(_tg) + len(_tg) + 4:
                    _disabled.add(_name)
                    break
            if _name in _disabled:
                break
    return _disabled


def slots_few_shot(vars_list: list[dict]) -> str:
    """按**实际槽位变量名**生成一条「气泡 / 底部」对照示例，防止 LLM 把两者写反。

    同时存在气泡槽与底部槽时才产出；无槽位或只有一类时返回空串。
    """
    _bubble = next((v.get("name") for v in vars_list if _var_category(v.get("name")) == "bubble"), None)
    _bottom = next((v.get("name") for v in vars_list if _var_category(v.get("name")) == "bottom"), None)
    if not (_bubble and _bottom):
        return ""
    _example = json.dumps({"slots": {_bubble: "我不会写代码！", _bottom: "这就是程序员"}}, ensure_ascii=False)
    return (
        "\n对照示例（务必看懂两者的区别，千万不要写反）：\n"
        f"{_example}\n"
        f"也就是说：{_bubble} 填**角色亲口说的话**（画面内气泡框），"
        f"{_bottom} 填**旁白吐槽/真相总结**（画面最下方字幕条），两者语义完全不同。"
    )


def render_slot_template(self, slot: dict, template, values: dict) -> str | None:
    """渲染单个提示词槽位（prompt_slots）的模板。

    template 支持两种写法：
    1. **扁平字符串**：直接 ``str.format(**values)``（兼容 {{{var}}} 三花括号）。
    2. **分块结构**（推荐）：``{prefix, blocks: [{var, max_chars, tiers|text}], suffix}``
       - 每个 block **仅当其 var 非空时**才渲染 —— 避免生成「空气泡」；
       - block 支持 ``tiers`` 分档：按变量实际字数取档，实现气泡大小/字号/行数自适应；
       - ``prefix`` / ``suffix`` 始终渲染（一致性锁与风格锁）。

    返回渲染后的文本；**无需注入时返回 None**（调用方跳过该槽位，保留工作流 JSON 内的原文）。
    字数越界只记日志警告，**不截断**。
    """
    key = (slot.get("key") or "").strip()
    vars_: list[str] = [
        str(v).strip() for v in (slot.get("vars") or []) if str(v).strip()
    ]
    if not vars_ and isinstance(template, dict):
        vars_ = [
            (b.get("var") or "").strip()
            for b in (template.get("blocks") or [])
            if isinstance(b, dict) and (b.get("var") or "").strip()
        ]
    filled = {v: str(values.get(v, "") or "").strip() for v in vars_}

    if vars_ and not any(filled.values()):
        logger.info(f"【槽位】 {key} 变量均为空，渲染为空（交由注入层清空节点）")
        return None

    if isinstance(template, str):
        _t = template
        for _v, _val in filled.items():
            _t = re.sub(r"\{\{\{\s*" + re.escape(str(_v)) + r"\s*\}\}\}", str(_val), _t)
        if re.search(r"\{\s*\w+\s*\}", _t):
            try:
                _t = _t.format(
                    **{_v: str(_val).replace("{", "{{").replace("}", "}}") for _v, _val in filled.items()}
                )
            except Exception as e:
                logger.warning(f"【槽位】 {key} 扁平模板渲染失败: {e}")
                return None
        return _t or None

    if not isinstance(template, dict):
        logger.warning(f"【槽位】 {key} 模板格式非法（应为字符串或对象），已跳过")
        return None

    parts: list[str] = []
    prefix = (template.get("prefix") or "").strip()
    if prefix:
        parts.append(prefix)

    for block in template.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        vn = (block.get("var") or "").strip()
        val = filled.get(vn, "")
        if not val:
            continue
        _mc = block.get("max_chars")
        if _mc:
            try:
                if len(val) > int(_mc):
                    logger.warning(
                        f"【槽位】 {key}.{vn} 共 {len(val)} 字，超过建议上限 {_mc}，"
                        f"可能出现错字或排版漂移"
                    )
            except (TypeError, ValueError):
                pass
        if block.get("text"):
            try:
                parts.append(str(block["text"]).format(**{vn: val}))
            except Exception as e:
                logger.warning(f"【槽位】 {key}.{vn} 文本渲染失败: {e}")
            continue
        tiers = block.get("tiers") or []
        if not tiers:
            logger.warning(f"【槽位】 {key}.{vn} 既无 text 也无 tiers，已跳过")
            continue
        picked = None
        last_valid = None
        for t in tiers:
            if not isinstance(t, dict):
                continue
            last_valid = t
            try:
                if len(val) <= int(t.get("max_chars", 999)):
                    picked = t
                    break
            except (TypeError, ValueError):
                continue
        if picked is None:
            picked = last_valid
        if picked is None:
            logger.warning(f"【槽位】 {key}.{vn} tiers 中没有合法档位，已跳过")
            continue
        try:
            parts.append(str(picked.get("text") or "").format(**{vn: val}))
        except Exception as e:
            logger.warning(f"【槽位】 {key}.{vn} 分档渲染失败: {e}")

    suffix = (template.get("suffix") or "").strip()
    if suffix:
        parts.append(suffix)
    return "".join(parts) or None


def inject_slots(self, prompt: dict, wf: dict, slot_values: dict | None) -> None:
    """把 slot_values 按 prompt_slots 声明注入到工作流各文本节点（_do_draw 内调用）。

    slot_values 为 None = 「本次未尝试填槽位」（保留工作流预设、空值跳过）；
    非 None = 「已尝试生成文字」，空值代表该位置有意留空 → 清空节点。
    """
    _slots_raw = wf.get("prompt_slots")
    _slots: list = []
    if isinstance(_slots_raw, str) and _slots_raw.strip():
        try:
            _parsed = json.loads(_slots_raw)
            if isinstance(_parsed, list):
                _slots = _parsed
            elif isinstance(_parsed, dict):
                _slots = [_parsed]
            else:
                logger.warning("【槽位】 prompt_slots 应为数组或对象，已忽略")
        except Exception as e:
            logger.warning(f"【槽位】 prompt_slots JSON 解析失败，已跳过: {e}")
    elif isinstance(_slots_raw, list):
        _slots = _slots_raw
    _fill_intent = slot_values is not None
    for _slot in _slots:
        if not isinstance(_slot, dict):
            continue
        _key = (_slot.get("key") or "").strip()
        _node = _slot.get("node")
        if not _key or _node in (None, ""):
            continue
        _field = (_slot.get("field") or "text").strip() or "text"
        _vars = [str(v).strip() for v in (_slot.get("vars") or []) if str(v).strip()]
        _tpl = _slot.get("template")
        if _tpl:
            _val = render_slot_template(self, _slot, _tpl, slot_values or {})
        else:
            _lookup = slot_values or {}
            _val = str(_lookup.get(_vars[0], "") or "").strip() if _vars else ""
            if not _val:
                _val = str(_lookup.get(_key, "") or "").strip()
        if _val is None:
            _val = ""
        if not _fill_intent and not _val:
            continue
        if workflow_builder.set_text_node(prompt, _node, _field, _val or ""):
            logger.info(f"【槽位】 {_key} → 节点 {_node}.{_field}（{len(_val or '')} 字）")


# --------------------------------------------------------------------------- #
# LLM 造词
# --------------------------------------------------------------------------- #
async def comic_write_slots_llm(self, wf: dict, user_text: str, scene: str) -> dict:
    """用内部 LLM 为带 prompt_slots 的工作流生成各槽位文字。

    成功时返回扁平的 {var_name: text} 且**覆盖所有槽位变量**（空 → "" 清空节点）；
    无法生成时返回 None，调用方跳过槽位、沿用工作流默认文字。
    """
    if not wf:
        return None
    _slots = normalize_prompt_slots(self, wf.get("prompt_slots"))
    if not _slots:
        return None
    _seen: set[str] = set()
    _vars: list[dict] = []
    for _s in _slots:
        if not isinstance(_s, dict):
            continue
        for _v in (_s.get("vars") or []):
            _v = str(_v).strip()
            if _v and _v not in _seen:
                _seen.add(_v)
                _vars.append({"name": _v, "hint": slot_var_hint(_v, _s)})
    # 确定性识别用户「不要某位置文字」的指令（如『不要底部/不加旁白』），命中后强制清空该槽位
    _disabled = _detect_disabled_slots(user_text or scene, _vars)
    # 剔除「不要发表情包」这类元指令，避免被当成画面描述语（检测必须在剥离前）
    _clean_text = strip_comic_negations(user_text or scene)
    if not _vars:
        return None
    model = self._cfg("llm_model", "").strip()
    if not model:
        model = self._resolve_translate_provider_id() or ""
    if not model:
        logger.info("【槽位·造词】 未配置可用 LLM（llm_model 与 translate_llm_model 均为空），跳过得词（沿用工作流默认文字）")
        return None
    _spec = "\n".join(f"- {v['name']}: {v['hint']}" for v in _vars)
    prompt = (
        "你正在为一句想法生成「表情包/漫画」的画面文字。请只输出一个 JSON 对象"
        "（不要任何解释、不要 markdown 代码块、不要反引号），键必须严格等于下方列出的"
        "槽位变量名，值为该槽位应写的文字。\n\n"
        f"用户原话/想法：\n{_clean_text}\n\n"
        f"画面描述（将作为出图提示词）：\n{scene}\n\n"
        "槽位变量（键名必须严格一致）：\n"
        f"{_spec}\n\n"
        "任意槽位若不需要文字，返回空字符串或省略该键即可（节点会被清空，不出该位置文字）。\n"
        "文字务必简短：气泡台词通常 6~8 字、底部旁白通常 6 字以内、最多不超过 10 字；宁短勿长。\n"
        + (slots_few_shot(_vars) + "\n" if slots_few_shot(_vars) else "")
        + "只输出 JSON 对象："
    )
    if _disabled:
        _disabled_names = "、".join(_disabled)
        prompt += (
            f"\n【重要·按用户要求禁用】用户明确要求不要写以下位置，对应键必须返回空字符串"
            f"（或省略），绝对不要生成：{_disabled_names}。"
        )
    try:
        logger.info(f"【槽位·造词】 使用模型({model}) 生成槽位文字")
        llm_resp = await self.context.llm_generate(chat_provider_id=model, prompt=prompt)
        self._record_llm_token("comic_slots", model, llm_resp)
        text = getattr(llm_resp, "completion_text", "") or ""
    except Exception as e:
        logger.warning(f"【槽位·造词】 LLM 调用失败，沿用默认文字: {e}")
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text, flags=re.DOTALL)
    try:
        _parsed = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                _parsed = json.loads(m.group(0))
            except Exception:
                return None
        else:
            return None
    if not isinstance(_parsed, dict):
        return None
    _allowed = {v["name"] for v in _vars}
    _out = {k: str(_parsed.get(k) or "").strip() for k in _allowed}
    # 确定性兜底：用户说『不要底部/不加旁白』的槽位，无论 LLM 是否听话都强制清空
    for _d in _disabled:
        if _d in _out:
            _out[_d] = ""
            logger.info(f"【槽位·造词】 按用户要求禁用槽位：{_d}（强制清空）")
    return _out


def merge_feature_lora(self, feature: dict | None, lora_map: dict, negative: str) -> tuple[dict, str]:
    """把 special_features 里功能的 default_lora / default_negative 合并进出图参数。

    default_lora 每行格式：名称|权重|0/1（0=禁用）。仅当用户未显式指定同名 LoRA 时补入；
    default_negative 仅在用户未给负向提示词时补入。返回 (lora_map, negative)。
    """
    if not feature:
        return lora_map, negative
    _dl = (feature.get("default_lora") or "").strip()
    if _dl and isinstance(lora_map, dict):
        for _line in _dl.splitlines():
            _line = _line.strip()
            if not _line:
                continue
            _pp = _line.split("|")
            _nm = _pp[0].strip()
            if not _nm:
                continue
            _w = None
            try:
                _w = float(_pp[1]) if len(_pp) > 1 and _pp[1].strip() else None
            except (TypeError, ValueError):
                _w = None
            _on = (_pp[2].strip() if len(_pp) > 2 else "1") or "1"
            if _on == "0":
                continue
            if _nm not in lora_map:
                lora_map[_nm] = _w
    _dn = (feature.get("default_negative") or "").strip()
    if _dn and not (negative or "").strip():
        negative = _dn
    return lora_map, negative


async def comic_build_prompts_llm(self, wf, idea, lora_map, want_prompt=True, want_slots=True):
    """用内部 LLM 把用户一句想法展开为：Anima 画面提示词 + 表情包槽位文字 + 识别到的 LoRA。

    返回 (positive_prompt, slot_values, lora_extracted)。无模型/失败则兜底（不崩）。
    """
    _slots = normalize_prompt_slots(self, wf.get("prompt_slots")) if want_slots else []
    if not (want_prompt or _slots):
        return idea, None, {}
    model = self._cfg("llm_model", "").strip()
    if not model:
        model = self._resolve_translate_provider_id() or ""
    if not model:
        logger.info("【表情包·造词】 未配置可用 LLM（llm_model 与 translate_llm_model 均为空），跳过造词（用原始想法作为提示词/默认文字）")
        return idea, None, {}
    _seen: set[str] = set()
    _vars: list[dict] = []
    for _s in _slots:
        if not isinstance(_s, dict):
            continue
        for _v in (_s.get("vars") or []):
            _v = str(_v).strip()
            if _v and _v not in _seen:
                _seen.add(_v)
                _vars.append({"name": _v, "hint": slot_var_hint(_v, _s)})
    # 确定性识别用户「不要某位置文字」的指令（如『不要底部/不加旁白』），命中后强制清空该槽位
    _disabled = _detect_disabled_slots(idea, _vars)
    # 剔除「不要发表情包」这类元指令后再喂给 LLM，避免被当成画面描述语（检测必须在剥离前）
    _clean_idea = strip_comic_negations(idea)
    lora_hint = ""
    if lora_map:
        lora_hint = "用户指定的 LoRA：" + "、".join(
            f"{k}(权重{round(float(v), 2)})" for k, v in lora_map.items()
        ) + "。画面提示词里不要写 <lora:> 标签，系统会自动注入。"
    _lora_catalog: list[str] = []
    for _l in self._lora_library():
        _nm = (_l.get("name") or "").strip()
        if _nm and _nm not in _lora_catalog:
            _lora_catalog.append(_nm)
        for _al in (_l.get("aliases") or []):
            _al = str(_al).strip()
            if _al and _al not in _lora_catalog:
                _lora_catalog.append(_al)
    _req: list[str] = []
    if want_prompt:
        _req.append(
            '"positive_prompt": 字符串，Anima 底模用的动漫画面提示词——详细的英文 Danbooru 风格标签，'
            "包含主体、动作、场景、画质词（masterpiece、best quality 等），贴合用户想法"
        )
    if _vars:
        _req.append(
            '"slots": 对象，键为下方槽位变量名（必须严格一致），值为该槽位应写的文字——'
            "要幽默、贴合梗、口语化，不要把用户原话直接搬过来当文字；"
            "若某个位置（如气泡或底部）不需要文字，返回空字符串或省略该键即可"
            "（节点会被清空，不出该位置文字）"
        )
    if _lora_catalog:
        _req.append(
            '"loras": 数组，用户想用的 LoRA 名称列表——只能从下方「可选 LoRA」清单里挑'
            "（清单里没有就返回空数组 []）；不要把 LoRA 名当画面词写进 positive_prompt，"
            "也不要写 <lora:> 标签，系统会按清单名称自动注入"
        )
    _system = (
        "你是表情包/漫画提示词助手。用户用一句中文描述想法，请只输出一个 JSON 对象"
        "（不要任何解释、不要 markdown 代码块、不要反引号）。\n"
        "文字务必简短：气泡台词通常 6~8 字、底部旁白通常 6 字以内、最多不超过 10 字；"
        "除非用户明确要求写长文，否则宁短勿长。"
    )
    if _req:
        _system += "\nJSON 需包含以下字段：\n- " + "\n- ".join(_req)
    _user = f"用户想法：\n{_clean_idea}\n"
    if lora_hint:
        _user += lora_hint + "\n"
    if _lora_catalog:
        _user += "可选 LoRA（用户想用时只能从中挑选，名称须完全一致）：" + "、".join(_lora_catalog) + "\n"
    if _vars:
        _spec = "\n".join(f"- {v['name']}: {v['hint']}" for v in _vars)
        _user += (
            f"槽位变量（键名必须严格一致）：\n{_spec}\n"
            "提示：任意槽位若不需要文字，返回空字符串或省略该键即可，系统不会强行生成。"
            + slots_few_shot(_vars)
        )
        if _disabled:
            _disabled_names = "、".join(_disabled)
            _user += (
                f"\n【重要·按用户要求禁用】用户明确要求不要写以下位置，对应槽位必须返回空字符串"
                f"（或省略该键），绝对不要自作主张生成：{_disabled_names}。"
            )
    try:
        logger.info(f"【表情包·造词】 使用模型({model}) 生成提示词/文字")
        _resp = await self.context.llm_generate(chat_provider_id=model, prompt=_system + "\n\n" + _user)
        self._record_llm_token("comic_prompt", model, _resp)
        _text = getattr(_resp, "completion_text", "") or ""
    except Exception as e:
        logger.warning(f"【表情包·造词】 LLM 调用失败，用兜底: {e}")
        return idea, None, {}
    _text = _text.strip()
    if _text.startswith("```"):
        _text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", _text, flags=re.DOTALL)
    try:
        _parsed = json.loads(_text)
    except Exception:
        _m = re.search(r"\{.*\}", _text, re.DOTALL)
        if _m:
            try:
                _parsed = json.loads(_m.group(0))
            except Exception:
                _parsed = {}
        else:
            _parsed = {}
    if not isinstance(_parsed, dict):
        _parsed = {}
    positive = _clean_idea
    if want_prompt:
        _p = str(_parsed.get("positive_prompt") or "").strip()
        if _p:
            positive = _p
    slot_values = None
    if _vars:
        _llm_slots = _parsed.get("slots") if isinstance(_parsed.get("slots"), dict) else {}
        slot_values = {}
        for _v in _vars:
            _name = _v["name"]
            slot_values[_name] = str((_llm_slots or {}).get(_name) or "").strip()
        # 确定性兜底：用户说『不要底部/不加旁白』的槽位，无论 LLM 是否听话都强制清空
        for _d in _disabled:
            if _d in slot_values:
                slot_values[_d] = ""
                logger.info(f"【表情包·造词】 按用户要求禁用槽位：{_d}（强制清空）")
    _extracted: dict[str, float | None] = {}
    if _lora_catalog:
        _raw = _parsed.get("loras")
        if isinstance(_raw, list):
            for _item in _raw:
                _item = str(_item).strip()
                if not _item:
                    continue
                _match = next(
                    (
                        (l.get("name") or "").strip()
                        for l in self._lora_library()
                        if workflow_builder._lora_name_matches((l.get("name") or "").strip(), _item)
                        or any(
                            workflow_builder._lora_name_matches(str(a).strip(), _item)
                            for a in (l.get("aliases") or [])
                        )
                    ),
                    None,
                )
                if _match:
                    _extracted[_match] = None
            if _extracted:
                logger.info(f"【表情包·造词】 从自由文本识别到 LoRA：{list(_extracted.keys())}")
    return positive, slot_values, _extracted
