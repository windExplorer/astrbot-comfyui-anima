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


# --------------------------------------------------------------------------- #
# boogu「自然语言指令」模式（mode: "nl"）
# --------------------------------------------------------------------------- #
# boogu 是**指令式图生图**模型：节点 prompt 就是一段【中文自然语言指令】，
# 不认识任何字段名（bubble= / bottom= 等）。所以第二个槽位（boogu 加字）应当让
# LLM / 用户直接产出「一整段自然语言」，而不是拆成 bubble_text / bottom_text 字段回填。
# 本组常量与函数支撑 prompt_slots 的 mode:"nl" 槽位：
#   - prefix / suffix：固定的「一致性锁」与「风格锁」自然语言（工程补偿，防止 boogu 改坏参考图）
#   - 中段：LLM 写 / 直填模板包，都是自然语言
#   - 最终注入节点的是 prefix + 中段 + suffix 拼成的一整段自然语言

# 一致性锁：boogu 图生图一致性弱，必须显式要求「保持参考图不变」，否则人物会被改
DEFAULT_BOOGU_PREFIX = (
    "保持参考图中的人物、表情、构图、背景完全不变，不要改变人物的动作、姿势和表情，"
    "不要添加与文字无关的新元素。"
)
# 风格锁：仅保留「防错字 + 保持参考图其余部分」，不再锁定可爱卡通/配色/线条——
# 气泡形状/位置/字体/描边颜色交由 LLM 按情绪现编，避免外观被写死。
DEFAULT_BOOGU_SUFFIX = (
    "画面其他部分与参考图保持一致，文字清晰可读无错别字。"
)


def slot_mode(slot: dict) -> str:
    """槽位模式：nl（自然语言指令）= 让 LLM/用户直接写一段自然语言；vars（默认）= 旧字段回填。"""
    _m = (slot.get("mode") or "").strip().lower()
    return _m if _m in ("nl", "vars") else "vars"


# bot 有时会误把「气泡文字 / boogu 形状描述」写成 danbooru 字段或标签塞进段1(anima 绘图)提示词，
# 例如 "...cute fluffy cloud bubble with soft pink border, text: 有点困了" / "Text: I'm a bit tired"。
# 这既污染绘图提示词（让 anima 去画云朵气泡），又没进到槽位2(boogu 自然语言)。
# 下面两个清理函数把这类内容从段1剥离、并把字段里的文字抽出来交给槽位2。
_BUBBLE_FIELD_RE = re.compile(
    r"(?i)(?:,\s*)?"                                  # 可选前置逗号
    r"(?:text|bubble|caption|对话框|气泡文字|气泡|文字|台词|对白)"  # 字段名
    r"\s*[:：]\s*"                                     # 冒号（中英文）
    r"(?P<content>[^\n]+?)"                            # 内容（到行尾）
    r"\s*$"                                            # 行尾
)
_BUBBLE_SHAPE_RE = re.compile(
    r"(?i)\s*[,，]?\s*"                               # 前置逗号/顿号
    r"(?:cute\s+fluffy\s+cloud\s+bubble|fluffy\s+cloud\s+bubble|cloud\s+bubble"
    r"|speech\s+bubble|speech\s+balloon|thought\s+bubble|thinking\s+bubble"
    r"|explosion\s+bubble|burst\s+bubble|star\s+bubble|comic\s+speech\s+bubble)"
    r"\b"
)


def strip_bubble_field_from_prompt(prompt: str):
    """清理段1(anima 绘图)提示词里的「气泡文字字段」与「boogu 形状描述」。

    返回 (clean_prompt, bubble_text)：
    - 命中 ``text: / Text: / bubble: / 气泡: / 文字:`` 等字段时，抽出字段文字作为 bubble_text，
      并从段1删除该字段（避免污染绘图提示词）；
    - 同时删除段1里的 boogu 形状标签（cloud bubble / speech bubble / thought bubble …），
      这些本该由槽位2(boogu)负责，塞进段1会让 anima 画错。
    未命中则原样返回、bubble_text 为空串。
    """
    if not prompt:
        return prompt or "", ""
    _prompt = prompt
    _m = _BUBBLE_FIELD_RE.search(_prompt)
    _bubble = ""
    if _m:
        _bubble = _m.group("content").strip().strip(",，").strip()
        _prompt = (_prompt[: _m.start()] + _prompt[_m.end():]).strip().strip(",，").strip()
    _prompt = _BUBBLE_SHAPE_RE.sub("", _prompt).strip().strip(",，").strip()
    return _prompt, _bubble


def _bubble_slot_key_from_wf(wf: dict) -> str:
    """从工作流 prompt_slots 找到 boogu/nl 气泡槽的变量名（找不到返回 ''）。"""
    _raw = (wf or {}).get("prompt_slots")
    if isinstance(_raw, str):
        try:
            _raw = json.loads(_raw)
        except Exception:
            return ""
    if isinstance(_raw, dict):
        _raw = [_raw]
    if not isinstance(_raw, list):
        return ""
    for _s in _raw:
        if not isinstance(_s, dict):
            continue
        if slot_mode(_s) == "nl":
            _k = (_s.get("key") or "").strip()
            if _k and _var_category(_k) == "bubble":
                return _k
    return ""


def _is_boogu_node(wf: dict, slot: dict) -> bool:
    """判断槽位是否指向 boogu 编辑节点（TextEncodeBooguEdit）。

    boogu 是【指令式图生图】模型，节点 prompt 只认一段自然语言，不认识 bubble=/bottom=
    字段。因此 boogu 槽即使被配成 目录/vars 模式，也应升级为『LLM 写整段自然语言指令』，
    否则气泡形状/位置/字体会被 _boogu_style_desc 写死（用户要的是 LLM 现编整段、外观随内容变）。
    """
    _node = str((slot or {}).get("node") or "").strip()
    if not _node or not isinstance(wf, dict):
        return False
    _n = wf.get(_node) or {}
    return str(_n.get("class_type") or "").strip() == "TextEncodeBooguEdit"


def apply_bubble_fallback(slot_values: dict | None, wf: dict, bubble_text: str) -> dict | None:
    """确定性兜底：已从 prompt 抽出气泡文字(bubble_text) 但槽位气泡为空时，强制填入，
    避免 bot 误写 text:/气泡: 字段被剥离后气泡整段丢失（『字段为空』）。无气泡槽则不动。"""
    if not bubble_text:
        return slot_values
    _key = _bubble_slot_key_from_wf(wf)
    if not _key:
        return slot_values
    if slot_values is None:
        slot_values = {}
    if not (slot_values.get(_key) or "").strip():
        slot_values[_key] = bubble_text
    return slot_values


# boogu 气泡/文字样式目录：驱动「自然语言指令」的气泡多样化（详见 skills/boogu-meme-bubbles）。
# - desc：可直接写进 boogu 节点 prompt 中段的自然语言片段，{text} 会被替换为实际文字。
#   其中的颜色/字号是「基准」，内部 LLM 可按角色发色、情绪、强调程度改写（见 boogu_nl_hint）。
# - tokens：直填模式下可用「样式名:文字」强制指定的前缀（ASCII 冒号）。
# - use_for：何时用该样式（给内部 LLM 做自动选型参考）。
BOOGU_BUBBLE_CATALOG = [
    {
        "name": "云朵气泡",
        "tokens": ["云朵", "云朵气泡", "cloud", "气泡", "普通气泡"],
        "use_for": "可爱、温柔、日常、轻松的对话",
        "desc": "在人物头部右上方添加一个云朵形状的对话气泡，边缘圆润蓬松、底部一个小尖角指向人物的嘴；气泡底色用【半透明白或淡彩色】，描边颜色跟随角色发色或情绪（如害羞→粉、平静→浅灰褐），不必固定深棕。气泡内用圆润卡通体写简体中文「{text}」，字号随字数自适应（≤4字可稍大，5-8字中等，更多则缩小并自动换行），整体偏小、留出内边距确保文字不溢出气泡，横向居中。",
    },
    {
        "name": "圆角对话气泡",
        "tokens": ["圆角", "对话气泡", "说话", "speech"],
        "use_for": "普通说话、陈述、解释、提醒",
        "desc": "在人物头部一侧添加一个圆角矩形对话气泡，一条小尾巴指向人物；气泡底色用【半透明白】，描边颜色跟随情绪（平静→墨色、害羞→粉、开心→暖橙），不必固定深棕。气泡内用圆润卡通体写简体中文「{text}」，字号随字数自适应、整体适中偏小、留边距不溢出，横向居中。",
    },
    {
        "name": "思考气泡",
        "tokens": ["思考", "os", "内心", "悄悄话", "thought", "疑问", "问号"],
        "use_for": "内心想法、OS、悄悄话、犹豫、自言自语",
        "desc": "在人物头部上方添加一串由小到大的圆形思考气泡连向头部，最上方大气泡用圆润手写体写深灰色的简体中文「{text}」，字号中等偏小、线条细，表达内心想法；气泡可半透明。",
    },
    {
        "name": "爆炸气泡",
        "tokens": ["爆炸", "星形", "burst", "震惊", "激动", "大喊", "尖叫"],
        "use_for": "大喊、震惊、激动、强调、吐槽爆发",
        "desc": "在画面上方炸开一个星形/爆炸形状气泡（底色用橙红或亮黄、描边用深色锯齿），气泡内用粗黑体写简体中文「{text}」，字号随字数自适应（字数多则缩小、勿溢出爆炸边缘），线条粗、字形夸张外扩，表现大喊或震惊。",
    },
    {
        "name": "尖角气泡",
        "tokens": ["尖角", "闪电", "怒", "怒气", "急促", "spiky"],
        "use_for": "怒气、急促、电竞感、质问",
        "desc": "在人物头部一侧添加带尖锐棱角/锯齿边缘的对话气泡，描边颜色可用怒气红或深色；气泡底色用【半透明白】。气泡内用粗体写简体中文「{text}」，字号随字数自适应、整体适中、留边距不溢出，表现怒气或急促。",
    },
    {
        "name": "底部字幕条",
        "tokens": ["字幕", "底部", "旁白", "caption", "说明", "总结"],
        "use_for": "【默认不加】仅旁白/吐槽/真相总结/用户明确要字幕时才用",
        "desc": "【默认不要加】只在吐槽/真相总结/用户明确要字幕时才用。若使用：在图片底部约六分之一处添加一条【半透明】圆角横条字幕，用粗圆润卡通体写简体中文「{text}」，带描边，字号随字数自适应、整体适中偏小、横向居中，可爱但不抢眼。",
    },
    {
        "name": "无气泡白字黑边",
        "tokens": ["无气泡", "经典", "白字", "meme", "大字", "标题"],
        "use_for": "通用梗图、大字标题、上方/下方居中配字",
        "desc": "不使用气泡框，在画面合适位置（底部或顶部）用粗体写简体中文「{text}」，白色填充+深黑粗描边，字号随字数自适应（字数多则缩小并换行、勿超出画面边缘），经典表情包大字但整体克制、不盲目超大，横向居中。",
    },
    {
        "name": "无气泡直接压字",
        "tokens": ["压字", "直接", "叠加", "overlay"],
        "use_for": "把字直接压在画面安静区、不挡主体",
        "desc": "不使用气泡框，在画面较空区域用圆润卡通体写简体中文「{text}」，加白色细描边保可读，字号中等偏小、自然摆放；文字可半透明叠加在背景上不抢主体。",
    },
    {
        "name": "放射爆裂大字",
        "tokens": ["放射", "爆裂", "拟声", "冲击", "放射状", "speed"],
        "use_for": "拟声词（咚/啪/汪）、极度强调、冲击感",
        "desc": "在画面中央/人物附近用放射状爆发的粗体字写简体中文「{text}」，字形向外炸开带速度线，橙红配色加深色描边，字号随字数自适应（勿溢出画面），表现冲击与强调。",
    },
]


def boogu_nl_hint(slot: dict) -> str:
    """给内部 LLM 的「boogu 提示词写法指南」——约束它怎么写，并按情绪选多样化气泡。

    系统会自动在前后补一致性锁 / 风格锁，所以 LLM 只写中间的「加气泡 / 加底部」描述。
    """
    _styles = "、".join(f"{_c['name']}({_c['use_for']})" for _c in BOOGU_BUBBLE_CATALOG)
    return (
        "【boogu 编辑指令（自然语言·中段）】为 boogu 图像编辑模型写一段【中文自然语言】，"
        "描述要在已生成的卡通图上【添加哪些文字元素】。⚠️ boogu 不认识任何字段名"
        "（bubble= / bottom= 等），只认自然语言，禁止输出键值对 / JSON。\n"
        "系统会在前面补「保持参考图不变」的一致性锁（你不必重复写），你只需写中间的添加描述，"
        "且必须写【完整】：明确气泡的【形状 / 位置 / 字体 / 描边颜色 / 线条粗细】，"
        "并让它们随情绪与内容变化——不要只写『加个气泡说 X』，更不要每次都用同一种气泡。\n"
        f"★ 气泡/文字样式必须多样化，不要每次都用同一种。可选样式有：{_styles}。\n"
        "★ 按语气/情绪/内容自动选合适样式：当内容像是在【心里嘀咕 / 自问自答 / 内心OS / 对自己说话】"
        "（而非对画中人或观众喊话、陈述）时，一律优先用【思考气泡】，不要用带尖尾巴的对话气泡；"
        "可爱日常→云朵气泡；普通说话→圆角对话气泡；内心OS/犹豫→思考气泡；大喊/震惊/激动→爆炸气泡；"
        "怒气/急促→尖角气泡；通用梗图大字→无气泡白字黑边；拟声/冲击→放射爆裂大字。\n"
        "★ 旁白/底部字幕条【默认绝对不加】：绝大多数表情包只有气泡台词、没有旁白；"
        "只有内容明显是吐槽/真相总结、或用户明确要『字幕/旁白』时才用底部字幕条。"
        "即使用户没提、即使工作流可能自带默认字幕，也一律默认不加底部字幕。\n"
        "★ 用户若明确指定了样式（如『用爆炸气泡』『不要气泡』『底部字幕条』『经典白字黑边』），优先满足。\n"
        "★ 用户只要『不要底部 / 不加旁白』时，只去掉底部字幕，**气泡台词照常写**，绝不要把整张图弄成无字；"
        "只要『不要气泡』时只去掉气泡、底部照常。两者互不牵连。\n"
        "★ 字号必须【随字数自适应、整体偏小、宁小勿大】：字数≤4 可稍大；5-8 字中等；"
        ">8 字缩小并自动换行。任何情况下文字都要在气泡/图形内【留出内边距、绝不溢出边缘】"
        "（文字超出气泡是严重错误）。不要动不动就『超大/很大』。\n"
        "★ 边框/描边与背景【必须多变、不要一尘不变】：\n"
        "  - 描边颜色跟随角色发色或情绪色（愤怒→红、害羞→粉、平静→墨色/原色），不要次次深棕或黑；\n"
        "  - 气泡底色可用【半透明白 / 淡彩色半透明】，或仅描边无填充（透明背景）；不要永远纯白实心；\n"
        "  - 线条粗细：大喊/震惊→粗，温柔/悄悄话/内心OS→细。\n"
        "  （目录里各样式的颜色/字号只是『基准』，务必按上面规则改写，不要机械照抄。）\n"
        "对选中的样式，写清：位置、形状/底色、文字内容、边框颜色、线条粗细、字体"
        "（圆润卡通体/粗黑体/手写体）、字号（随字数自适应）。文字务必短：气泡 ≤8 字、底部 ≤6 字。\n"
        "只输出这一段自然语言描述本身，不要解释、不要 JSON、不要 markdown。"
    )


_BOTTOM_DISABLE_KW = (
    "不要底部", "不加底部", "不要旁白", "不加旁白", "不要字幕", "不要底部文字",
    "无底部", "no bottom", "no caption", "without caption",
)
_BUBBLE_DISABLE_KW = (
    "不要气泡", "不加气泡", "不要台词", "不要对话气泡", "无气泡",
    "no bubble", "no speech", "without bubble",
)


_FEEDBACK_KW = (
    "重来", "再来一张", "换一个", "换张", "气泡多余", "字幕多余", "改一下", "重新画",
    "重新生成", "重新来", "不对", "不是这样", "去掉气泡", "去掉字幕", "别加气泡", "别加字幕",
    "气泡太多", "字幕太多", "重画", "redo", "again",
)


def _feedback_note(text: str) -> str:
    """识别用户原话像是对上一张图的修改意见/吐槽（而非一句新表情包想法），
    返回约束句，避免 LLM 把『气泡多余』『重来』这类反馈原话写进气泡/底部。"""
    _t = (text or "").lower()
    if any(k in _t for k in _FEEDBACK_KW):
        return (
            "用户这句话像是对【上一张图】的修改意见 / 吐槽，不是一句新的表情包想法；"
            "请把它理解为对画面的【调整要求】（如『去掉气泡』『重画一张』），"
            "表情包文字应据此重新构思或留空，绝不要把『气泡多余』『重来』『不对』这类原话"
            "写进气泡 / 底部文字。"
        )
    return ""


def _nl_disable_notes(text: str) -> str:
    """识别用户「不要底部 / 不要气泡」指令，返回要塞进 LLM 提示的 boogu 约束句。"""
    _t = (text or "").lower()
    _notes: list[str] = []
    _bottom_off = any(k in _t for k in _BOTTOM_DISABLE_KW)
    _bubble_off = any(k in _t for k in _BUBBLE_DISABLE_KW)
    if _bottom_off:
        # 仅禁用底部：明确气泡照常写，避免 LLM 看到否定语气把整张图都弄成无字
        _notes.append("绝对不要添加底部字幕 / 旁白文字（但气泡台词照常生成，不要因此不写气泡）")
    if _bubble_off:
        _notes.append("绝对不要添加对话气泡文字（但底部字幕照常写）")
    return "；".join(_notes)


_THOUGHT_KW = (
    "内心", "os", "心想", "寻思", "琢磨", "暗自", "脑内", "脑海", "心里想",
    "心里嘀咕", "自言自语", "嘟囔", "嘀咕", "心里话", "腹诽", "脑补",
)


def _detect_thought(user_text: str) -> str:
    """识别用户内容明显是「内心想法 / OS / 自言自语」，返回强制用思考气泡的约束句（空串=不强制）。"""
    _t = (user_text or "").lower()
    if any(k in _t for k in _THOUGHT_KW):
        return (
            "用户内容明显是【内心想法 / OS / 自言自语】，必须使用【思考气泡】"
            "（头顶一连串由小到大的圆形小泡，深灰圆润手写体），"
            "严禁使用对话气泡 / 尖角气泡 / 云朵气泡（它们都带指向别人的尖尾巴，不像内心想法）。"
        )
    return ""


def _boogu_style_desc(style_name: str, text: str) -> str | None:
    """取目录中某样式的自然语言描述片段，并把 {text} 替换为实际文字。"""
    for _c in BOOGU_BUBBLE_CATALOG:
        if _c["name"] == style_name:
            return _c["desc"].replace("{text}", text)
    return None


def _parse_boogu_style_prefix(value: str):
    """直填：检测『样式名:文字』前缀（ASCII 冒号），返回 (样式名|None, 文字)。"""
    _v = (value or "").strip()
    if ":" in _v:
        _head, _rest = _v.split(":", 1)
        _head = _head.strip().lower()
        for _c in BOOGU_BUBBLE_CATALOG:
            if _head == _c["name"].lower() or _head in [str(t).lower() for t in _c["tokens"]]:
                return _c["name"], _rest.strip()
    return None, _v


def _render_nl_slot(self, slot: dict, slot_values: dict | None) -> str:
    """nl 模式：boogu 编辑指令 = 一致性锁(prefix) + 中段 + 风格锁(suffix)，一整段自然语言。

    中段来源（优先级）：
    - LLM 模式：slot_values[slot.key] 是一整段自然语言中段（内部 LLM 按气泡目录自动选型）；
    - 直填·目录模式：用 vars（如 bubble_text/bottom_text）经气泡目录拼成自然语言，
      支持「样式名:文字」强制指定样式（如「爆炸:午安」）；
    - 直填·模板模式：slot 配了 template 时仍走模板（旧用法，兼容）。

    ★ 关键：没有任何实际文字内容时，整段返回空字符串（__绝不__画出「空气泡 / 空字幕条」）。
    即用户说「气泡为空 / 不要气泡 / 不出字」时，boogu 不应再生成气泡形状，只画主体图。
    """
    _key = (slot.get("key") or "").strip()
    _sv = slot_values or {}
    _middle = ""
    if _sv.get(_key, "").strip():
        # LLM 模式：直接给了一段自然语言中段
        _middle = _sv[_key].strip()
    elif slot.get("template"):
        # 直填·模板模式（兼容旧配置）
        _middle = render_slot_template(self, slot, slot.get("template"), _sv) or ""
    else:
        # 直填·目录模式：每个 var 映射一个文字位，按样式目录生成自然语言
        _vars = [str(v).strip() for v in (slot.get("vars") or []) if str(v).strip()]
        _parts: list[str] = []
        for _i, _vn in enumerate(_vars):
            _raw = str(_sv.get(_vn, "") or "").strip()
            if not _raw:
                continue
            _style, _text = _parse_boogu_style_prefix(_raw)
            if _style is None:
                # 未指定样式：气泡位默认用 bubble_style，其余（如底部）默认用 bottom_style
                _style = slot.get("bottom_style") if _i >= 1 else slot.get("bubble_style")
                _style = _style or ("底部字幕条" if _i >= 1 else "云朵气泡")
            _d = _boogu_style_desc(_style, _text)
            if _d:
                _parts.append(_d)
        _middle = "\n\n".join(_parts)
    if not _middle.strip():
        # 没有任何实际文字：返回空，避免 boogu 画出「空气泡 / 空字幕条」
        return ""
    return _nl_join(slot, _middle)


def _nl_join(slot: dict, middle: str) -> str:
    """把「一致性锁 + 中段 + 风格锁」拼成一整段自然语言（中段空则不渲染中段）。"""
    _prefix = (slot.get("prefix") or DEFAULT_BOOGU_PREFIX).strip()
    _suffix = (slot.get("suffix") or DEFAULT_BOOGU_SUFFIX).strip()
    return "\n\n".join(p for p in (_prefix, (middle or "").strip(), _suffix) if p)


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
        # boogu 编辑节点：即使配成 目录/vars 模式，也升级为「LLM 写整段自然语言指令」，
        # 避免气泡形状/位置/字体被 _boogu_style_desc 写死。无 key 时补一个以便注入整段指令。
        if _is_boogu_node(wf, _slot) and not _key:
            _key = f"boogu_{_node}"
            _slot["key"] = _key
        if not _key or _node in (None, ""):
            continue
        _field = (_slot.get("field") or "text").strip() or "text"
        if slot_mode(_slot) == "nl" or _is_boogu_node(wf, _slot):
            # 自然语言指令模式 / boogu 节点：LLM 写整段自然语言，最终拼成一段指令
            _val = _render_nl_slot(self, _slot, slot_values)
        else:
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


def _perspective_rule(subject: str) -> str:
    """表情包/漫画的『主角视角』规则：按 subject 区分画面主角。

    - subject="user"（默认）：用户自己的表情包，角色=正在说话的用户本人（第一人称）。
    - subject="bot"：bot（助手/伴侣）自己发的表情包，角色=bot 本体（第一人称），
      且绝不能把用户正在做的事当成 bot 在做的事。
    """
    if subject == "bot":
        return (
            "【角色即 bot 本体·第一人称】这是 bot（助手/伴侣）自己发的表情包，画面角色就是"
            "【bot 本人】，用第一人称『我』代表 bot。写 bot 自己的状态 / 心情 / 陪伴"
            "（如『我陪着你呢』『想你啦』『在呢』），围绕 bot 与用户的互动来写。"
            "★ 绝不要把你（用户）正在做的事当成 bot 在做的事：用户在上班 / 通勤 / 吃饭 ≠ bot 在上班，"
            "bot 没有现实里的人类日常，别编造 bot 在做这些；提到用户时用『你』称呼，"
            "bot 用自己的口吻说话，不要把用户的活动搬进 bot 的台词 / 场景。"
        )
    # 默认：用户自己的表情包
    return (
        "【角色即用户本人·第一人称】表情包/漫画里的角色就是【正在说话的用户本人】，一律用第一人称"
        "『我/本人』看待，不是某个第三方人物。当用户用『我/俺/本宝宝/咱』等描述自己的状态、动作、"
        "情绪或处境时，让角色去演绎那个状态，并把气泡/台词写成第一人称"
        "（如用户说『我在上班还没下班』→气泡写『我还没下班』），"
        "绝不要写成旁观者视角的第三人称（如『某人在上班』『它还在这儿加班』）。"
        "用户没提自己时用泛称即可；但只要用户以第一人称自述，就保持第一人称、角色=用户。"
    )


# --------------------------------------------------------------------------- #
# LLM 造词
# --------------------------------------------------------------------------- #
async def comic_write_slots_llm(self, wf: dict, user_text: str, scene: str, subject: str = "user") -> dict:
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
    _nl_slots: list[dict] = []
    for _s in _slots:
        if not isinstance(_s, dict):
            continue
        if slot_mode(_s) == "nl":
            # 自然语言指令模式：该槽位由 LLM 直接写一段自然语言（以 slot.key 为变量名）
            _k = (_s.get("key") or "").strip()
            if _k and _k not in _seen:
                _seen.add(_k)
                _vars.append({"name": _k, "hint": boogu_nl_hint(_s)})
                _nl_slots.append(_s)
        elif _is_boogu_node(wf, _s):
            # boogu 编辑节点：即使配成 目录/vars 模式，也升级为『LLM 写整段自然语言指令』，
            # 避免气泡形状/位置/字体被 _boogu_style_desc 写死（用户要 LLM 现编整段、外观随内容变）。
            _k = (_s.get("key") or "").strip() or f"boogu_{_s.get('node')}"
            if _k not in _seen:
                _seen.add(_k)
                _vars.append({"name": _k, "hint": boogu_nl_hint(_s)})
                _nl_slots.append(_s)
        else:
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
    _nl_notes = _nl_disable_notes(user_text or scene)
    _thought_note = _detect_thought(user_text or scene)
    _is_nl = bool(_nl_slots)
    prompt = (
        "你正在为一句想法生成「表情包/漫画」的画面文字。请只输出一个 JSON 对象"
        "（不要任何解释、不要 markdown 代码块、不要反引号），键必须严格等于下方列出的"
        "槽位变量名，值为该槽位应写的文字。\n\n"
        + _perspective_rule(subject) + "\n\n"
        f"用户原话/想法：\n{_clean_text}\n\n"
        f"画面描述（将作为出图提示词）：\n{scene}\n\n"
        "（若下方槽位含『画面 / positive / draw 绘图提示词』：请写详细英文 Danbooru 风格标签，"
        "含 masterpiece、best quality、主体、表情、动作；表情包讲究『字能读、脸能懂』——"
        "表情要夸张（瞪眼/张嘴/脸红/炸毛/流泪）、角色上半身或大头特写且四周留白给气泡、"
        "背景简洁（simple background / plain background）以便白字气泡可读、用干净利落的动漫赛璐璐风，"
        "不要写成简短中文。）\n\n"
        "槽位变量（键名必须严格一致）：\n"
        f"{_spec}\n\n"
    )
    if _is_nl:
        prompt += (
            "其中带【boogu 编辑指令】说明的槽位，其值必须是一整段【自然语言指令】"
            "（可一两句话，描述加什么气泡 / 加什么底部文字），不要写短词、不要写键值对。"
            "（旁白/底部字幕默认不加：多数表情包只有气泡台词，只有吐槽/总结/用户要字幕时才写底部。）\n"
        )
    else:
        prompt += (
            "任意槽位若不需要文字，返回空字符串或省略该键即可（节点会被清空，不出该位置文字）。\n"
            "文字务必简短：气泡台词通常 6~8 字、底部旁白通常 6 字以内、最多不超过 10 字；宁短勿长。\n"
        )
    if slots_few_shot(_vars):
        prompt += slots_few_shot(_vars) + "\n"
    prompt += "只输出 JSON 对象："
    if _disabled:
        _disabled_names = "、".join(_disabled)
        prompt += (
            f"\n【重要·按用户要求禁用】用户明确要求不要写以下位置，对应键必须返回空字符串"
            f"（或省略），绝对不要生成：{_disabled_names}。"
        )
    if _nl_notes:
        prompt += f"\n【重要·boogu 指令约束】{_nl_notes}。"
    if _thought_note:
        prompt += f"\n【重要·气泡样式】{_thought_note}。"
    _fb_note = _feedback_note(user_text or scene)
    if _fb_note:
        prompt += f"\n【重要·反馈处理】{_fb_note}。"
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


async def comic_build_prompts_llm(self, wf, idea, lora_map, want_prompt=True, want_slots=True, subject: str = "user"):
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
    _nl_slots: list[dict] = []
    for _s in _slots:
        if not isinstance(_s, dict):
            continue
        if slot_mode(_s) == "nl":
            _k = (_s.get("key") or "").strip()
            if _k and _k not in _seen:
                _seen.add(_k)
                _vars.append({"name": _k, "hint": boogu_nl_hint(_s)})
                _nl_slots.append(_s)
        elif _is_boogu_node(wf, _s):
            # boogu 编辑节点：即使配成 目录/vars 模式，也升级为『LLM 写整段自然语言指令』，
            # 避免气泡形状/位置/字体被 _boogu_style_desc 写死（用户要 LLM 现编整段、外观随内容变）。
            _k = (_s.get("key") or "").strip() or f"boogu_{_s.get('node')}"
            if _k not in _seen:
                _seen.add(_k)
                _vars.append({"name": _k, "hint": boogu_nl_hint(_s)})
                _nl_slots.append(_s)
        else:
            for _v in (_s.get("vars") or []):
                _v = str(_v).strip()
                if _v and _v not in _seen:
                    _seen.add(_v)
                    _vars.append({"name": _v, "hint": slot_var_hint(_v, _s)})
    # 确定性识别用户「不要某位置文字」的指令（如『不要底部/不加旁白』），命中后强制清空该槽位
    _disabled = _detect_disabled_slots(idea, _vars)
    # 剔除「不要发表情包」这类元指令后再喂给 LLM，避免被当成画面描述语（检测必须在剥离前）
    _clean_idea = strip_comic_negations(idea)
    _nl_notes = _nl_disable_notes(idea)
    _thought_note = _detect_thought(idea)
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
        if _nl_slots:
            _req.append(
                '"slots": 对象，键为下方槽位变量名（必须严格一致），值为该槽位应写的'
                "【自然语言 boogu 编辑指令】——直接写一段中文自然语言，描述加什么气泡 / 加什么底部文字，"
                "不要写键值对；若某位置不需要，返回空字符串或省略该键。"
                "（旁白/底部字幕默认不加：多数表情包只有气泡台词，只有吐槽/总结/用户要字幕时才写底部。）"
            )
        else:
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
        + _perspective_rule(subject) + "\n"
    )
    if not _nl_slots:
        _system += (
            "文字务必简短：气泡台词通常 6~8 字、底部旁白通常 6 字以内、最多不超过 10 字；"
            "除非用户明确要求写长文，否则宁短勿长。\n"
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
        )
        if _nl_slots:
            _user += (
                "提示：带【boogu 编辑指令】的槽位值必须是一整段自然语言（可一两句话），"
                "不是短词、不是键值对；不需要的位置返回空字符串或省略该键。\n"
            )
        else:
            _user += (
                "提示：任意槽位若不需要文字，返回空字符串或省略该键即可，系统不会强行生成。\n"
            )
        _user += slots_few_shot(_vars)
        if _disabled:
            _disabled_names = "、".join(_disabled)
            _user += (
                f"\n【重要·按用户要求禁用】用户明确要求不要写以下位置，对应槽位必须返回空字符串"
                f"（或省略该键），绝对不要自作主张生成：{_disabled_names}。"
            )
        if _nl_notes:
            _user += f"\n【重要·boogu 指令约束】{_nl_notes}。"
        if _thought_note:
            _user += f"\n【重要·气泡样式】{_thought_note}。"
        _fb_note = _feedback_note(idea)
        if _fb_note:
            _user += f"\n【重要·反馈处理】{_fb_note}。"
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
