"""提示词防漂移与丰富化护栏（v7.6.0）。

**为什么要这个模块**：历史上出过「LLM 拿到角色/作品 tag 后自作主张补发色/瞳色/发型」，
把角色画成了另一个人（真实案例：本来是绿发，被补了 white_hair）。当时的补救是在
docstring 里写一句「禁止补外观」——但这只是**说服模型**，没有任何代码兜底：
单角色时模型自己写的 `white hair` 与角色卡锚点里的 `green hair` 会同时进 prompt。

本模块把那条规则变成可执行的代码，并给出「可以放心丰富」的白名单：

1. **外观维度（冻结区）**：把标签按维度归类（发色 / 瞳色 / 发型 / 兽化 / 体型 / 服装 / 配饰），
   中英文都认（`white hair` / `白发`）；
2. **冻结判定**：文本里出现角色/作品 tag（`name \\(work\\)` 形态）或命中角色卡名单 → 外观冻结；
3. **冲突剥除**：同一维度上出现与「权威外观」（角色卡锚点）不同的值 → 剥掉非权威的那一份；
4. **丰富化护栏**：扩写后的文本相对原文**新增或改写**的外观标签一律剥掉，且必须保留原文全部标签；
5. **自由维度（丰富区）**：镜头/构图/光影/氛围/天气/材质/场景细节/画质——这才是"加料"该去的地方；
6. **质量前缀分档**：按底模风格决定加哪套画质词（标签系 / pony / NAI / 自然语言系不加）。

纯函数实现，不依赖 AstrBot 运行时，便于单测。
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# 标签切分（顶层逗号；忽略括号内逗号）
# --------------------------------------------------------------------------- #


def split_tags(text: str) -> list[str]:
    """按顶层逗号切分标签串（括号内的逗号不算分隔符）。"""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text or "":
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return [s for s in out if s]


# --------------------------------------------------------------------------- #
# 外观维度词表（冻结区）
# 每条规则的正则在「单个标签」范围内匹配，第 1 个捕获组即该维度的值。
# --------------------------------------------------------------------------- #

_EN_COLOR = (r"black|white|silver|grey|gray|blonde|golden|gold|brown|blue|green|pink|"
             r"purple|red|aqua|orange|teal|cyan|magenta|platinum|multicolored|"
             r"multi-?colored|two-?tone|two\s*tone|streaked|gradient|rainbow")
_ZH_COLOR = r"黑|白|银|灰|金|棕|蓝|绿|粉|紫|红|橙|青|彩色|双色"

APPEARANCE_RULES: dict[str, list[re.Pattern]] = {
    # 发色：颜色 + 头发/刘海/双马尾等发部名词
    "hair_color": [
        re.compile(rf"\b({_EN_COLOR})\s+(?:streaked\s+|gradient\s+)?"
                   r"(?:hair|bangs|ahoge|twintails|ponytail|braids?|sidelocks)\b", re.I),
        re.compile(rf"({_ZH_COLOR})色?(?:的)?(?:长|短|大|波浪)?(?:头发|发型|发丝|刘海|发)"),
    ],
    # 瞳色
    "eye_color": [
        re.compile(rf"\b({_EN_COLOR}|amber|heterochromia)\s+eyes?\b", re.I),
        re.compile(rf"({_ZH_COLOR}|琥珀)色?(?:的)?(?:眼睛|眼眸|眼瞳|双瞳|瞳孔|瞳|眼)"),
    ],
    # 发型 / 发长
    "hair_style": [
        re.compile(r"\b(very long|long|medium|short|shoulder-length|twin\s*tails?|twintails?|"
                   r"side\s*ponytail|ponytail|twin\s*braids?|braids?|buns?|double\s*bun|bob|"
                   r"hime\s*cut|blunt\s*bangs|bangs|sidelocks|ahoge|curly|wavy|straight|messy|"
                   r"spiky|undercut|buzz\s*cut|drill\s*hair|hair\s+ribbon|hair\s+bun)\b"
                   r"\s*(?:hair|bangs)?", re.I),
        re.compile(r"(双马尾|马尾|麻花辫|丸子头|包子头|齐刘海|斜刘海|公主切|长发|短发|卷发|"
                   r"直发|波浪卷|波波头|齐肩发|碎发|披肩发)"),
    ],
    # 兽化 / 非人特征
    "animal": [
        re.compile(r"\b(?:(cat|fox|wolf|rabbit|dog|animal|horse|deer|mouse|bear|dragon|demon|"
                   r"angel|elf|devil)?\s*(ears?|tails?|horns?|wings?|feathers|scales|halo|"
                   r"fangs?|claws?|paws?))\b", re.I),
        re.compile(r"(猫耳|狐耳|狼耳|兔耳|兽耳|耳朵|尾巴|角|翅膀|羽毛|光环|獠牙|爪子|鳞片)"),
    ],
    # 体型
    "body": [
        re.compile(r"\b(petite|tall|short|slim|slender|skinny|plump|chubby|curvy|muscular|"
                   r"buff|busty|flat\s*chest|loli|shota)\b", re.I),
        re.compile(r"(娇小|高挑|纤细|瘦弱|微胖|丰满|肌肉|巨乳|平胸)"),
    ],
    # 服装 / 配饰：**不参与**锚点冲突剥除（本图换装是允许的），只用于扩写护栏
    "outfit": [
        re.compile(r"\b(dress|skirt|shirt|blouse|hoodie|sweater|jacket|coat|kimono|yukata|"
                   r"uniform|sailor\s*uniform|maid|suit|bikini|swimsuit|lingerie|apron|robe|"
                   r"cape|scarf|gloves?|sleeves?|stockings?|thighhighs?|pantyhose|boots?|shoes?|hat|"
                   r"ribbon|necktie|tie|choker|necklace|earrings?|glasses|goggles|"
                   r"ornaments?|hair\s?ornaments?|hairpins?|hair\s?pins?|hairpieces?|"
                   r"tassels?|bells?|scrunchie|pendants?|"
                   # v7.7.70：实测案例里 LLM 自己编的 "armor" / "hanfu" 此前识别不出是服装，
                   # 剥除因此漏掉（两人各穿一套仍被保留）。中式/古装与装备类一并纳入。
                   r"armou?r|hanfu|qipao|cheongsam|chinese\s?clothes|chinese\s?dress|"
                   r"wedding\s+dress|evening\s+gown|cloak|tunic|vest|jerkin|plate\s?armou?r)\b",
                   re.I),
        re.compile(r"(连衣裙|裙子|衬衫|卫衣|毛衣|外套|水手服|制服|和服|浴衣|泳装|内衣|围裙|"
                   r"斗篷|围巾|手套|丝袜|过膝袜|靴子|鞋子|帽子|发带|领带|项圈|项链|耳环|眼镜)"),
    ],
}

# 锚点冲突剥除时使用的维度（服装/配饰不在此列：本图换装允许，多人规则也要求每角色写本图服装）
FREEZE_DIMS: tuple[str, ...] = ("hair_color", "eye_color", "hair_style", "animal", "body")

# 扩写护栏使用的维度：外观一律不许新增（含服装/配饰），要加只能加自由维度
ENHANCE_FORBIDDEN_DIMS: tuple[str, ...] = FREEZE_DIMS + ("outfit",)

# 角色/作品 tag：`hatsune_miku \(vocaloid\)` / `belle (zenless zone zero)`
_CHARACTER_TAG_RE = re.compile(
    r"[a-z0-9_\-'.]+(?:\s+[a-z0-9_\-'.]+)*\s*\\?\(\s*[a-z0-9_\-'. ]+\s*\\?\)", re.I)


# --------------------------------------------------------------------------- #
# 自由维度（丰富区）：加料只该加这些
# --------------------------------------------------------------------------- #

FREE_DIM_CLUES: dict[str, str] = {
    "镜头/视角": "close-up, upper body, cowboy shot, full body, from above, from below, "
                 "from side, from behind, wide shot, depth of field, bokeh",
    "构图": "centered, dynamic pose, dutch angle, rule of thirds, negative space",
    "光影": "cinematic lighting, backlighting, rim light, god rays, soft lighting, "
            "dramatic shadows, glowing, lens flare",
    "氛围/天气": "night, sunset, dawn, dusk, rainy, snowing, fog, mist, clouds, "
                 "starry sky, petals falling",
    "场景细节": "detailed background, cityscape, forest, classroom, window, curtains, "
                "flowers, water reflection",
    "材质/质感": "intricate details, detailed fabric, lace, metallic, glossy, matte, "
                 "wet clothes, texture",
}

# 自由维度的判定词（用于统计"已覆盖几个维度"，仅作日志/触发判断，不参与剥除）
_FREE_HINTS: dict[str, tuple[str, ...]] = {
    "镜头/视角": ("close-up", "upper body", "cowboy shot", "full body", "from above",
                  "from below", "from side", "from behind", "wide shot", "depth of field",
                  "bokeh", "pov", "特写", "半身", "全身", "俯视", "仰视", "视角"),
    "构图": ("composition", "centered", "dutch angle", "dynamic pose", "构图", "留白", "对称"),
    "光影": ("lighting", "backlight", "rim light", "god rays", "glow", "shadow",
             "lens flare", "光", "逆光", "侧光", "光斑", "氛围"),
    "氛围/天气": ("night", "sunset", "sunrise", "dawn", "dusk", "rain", "snow", "fog",
                  "mist", "cloud", "starry", "petal", "夜", "黄昏", "清晨", "雨", "雪",
                  "雾", "星空", "樱花", "花瓣"),
    "场景细节": ("background", "city", "forest", "classroom", "room", "window", "flower",
                 "water", "street", "sky", "场景", "背景", "街道", "花海", "室内", "室外"),
    "材质/质感": ("detail", "texture", "fabric", "lace", "metal", "gloss", "matte",
                  "reflection", "wet", "材质", "质感", "细节", "纹理", "反光"),
}


# --------------------------------------------------------------------------- #
# 基础判定
# --------------------------------------------------------------------------- #


def _norm(v: str) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


def appearance_dims(text: str) -> dict[str, set[str]]:
    """扫出文本里各外观维度上出现的取值，返回 {维度: {取值…}}。

    只在**顶层标签**范围内判定（括号分组内的整组按一个标签处理，避免把锚点分组拆散）。
    """
    found: dict[str, set[str]] = {}
    for tag in split_tags(text):
        for dim, rules in APPEARANCE_RULES.items():
            for rx in rules:
                for m in rx.finditer(tag):
                    val = _norm(" ".join(g for g in m.groups() if g))
                    if not val:
                        continue
                    found.setdefault(dim, set()).add(val)
    return found


def has_character_tag(text: str, known_names: tuple[str, ...] = ()) -> bool:
    """文本里是否出现角色/作品 tag（`name \\(work\\)` 形态）或已知角色名。"""
    if _CHARACTER_TAG_RE.search(text or ""):
        return True
    low = (text or "").lower()
    for n in known_names:
        n = _norm(n)
        if not n:
            continue
        # 中文名两个字就有辨识度；纯英文名太短容易误伤（如 "ai"），要 4 个字符以上
        _min = 2 if any("\u4e00" <= ch <= "\u9fa5" for ch in n) else 4
        if len(n) >= _min and n in low:
            return True
    return False


def appearance_frozen(text: str, known_names: tuple[str, ...] = ()) -> bool:
    """是否进入「外观冻结」模式：出现角色/作品 tag 即冻结（外观只能来自锚点/用户明说）。"""
    return has_character_tag(text, known_names)


def free_dim_coverage(text: str) -> set[str]:
    """文本已覆盖了哪些自由维度（用于日志与"是否够丰富"的判断）。"""
    low = (text or "").lower()
    return {dim for dim, hints in _FREE_HINTS.items() if any(h in low for h in hints)}


def count_tags(text: str) -> int:
    """顶层标签数量（自然语言整句也会得到一个粗略的"段数"）。"""
    return len(split_tags(text))


# --------------------------------------------------------------------------- #
# 护栏：冲突剥除 / 扩写校验
# --------------------------------------------------------------------------- #


def _strip_tags(text: str, drop: list[str]) -> str:
    """按顶层标签剥除指定标签（保持原有顺序与其余文本）。"""
    drop_low = {_norm(d) for d in drop}
    keep = [t for t in split_tags(text) if _norm(t) not in drop_low]
    return ", ".join(keep)


def strip_conflicting_appearance(text: str, authority: str, *,
                                 dims: tuple[str, ...] = FREEZE_DIMS,
                                 include_groups: bool = False) -> tuple[str, list[str]]:
    """剥掉与「权威外观」冲突的外观标签（返回 (新文本, 被剥掉的标签)）。

    - ``authority``：角色卡锚点标签串（权威来源，永不被剥）；
    - 判定：同一维度上，权威取值为 A、某个标签取值为 B 且 B∩A=∅ → 该标签剥掉；
    - ``include_groups``：是否连括号权重分组一起判（默认 False）。
      多人时分组由插件按锚点自己重建、每组属于不同角色，权威值只对其中一个角色成立，
      贸然按组判会把另一个角色的分组整组误删——所以默认只动**组外的裸标签**；
      单角色场景（只有一个权威角色）可传 True，把模型自己写的冲突分组也剥掉。
    只在真正剥了东西时重建字符串。
    """
    auth: dict[str, set[str]] = {}
    _all_dims = appearance_dims(authority)
    for dim in dims:
        vals = _all_dims.get(dim) or set()
        if vals:
            auth[dim] = vals
    if not auth or not (text or "").strip():
        return text, []
    auth_tags = {_norm(t) for t in split_tags(authority)}
    dropped: list[str] = []
    for tag in split_tags(text):
        if _norm(tag) in auth_tags:
            continue
        if not include_groups and "(" in tag:
            continue                      # 括号分组整组跳过（多人时属于别的角色）
        d = appearance_dims(tag)
        for dim in dims:
            av = auth.get(dim)
            if not av:
                continue
            vals = d.get(dim) or set()
            if vals and not (vals & av):
                dropped.append(tag)
                break
    if not dropped:
        return text, []
    return _strip_tags(text, dropped), dropped


def strip_new_appearance(original: str, enriched: str) -> tuple[str, list[str]]:
    """扩写护栏：剥掉相对原文**新增或改写**的外观标签（返回 (新文本, 被剥掉的标签)）。"""
    orig = appearance_dims(original)
    dropped: list[str] = []
    for tag in split_tags(enriched):
        d = appearance_dims(tag)
        for dim in ENHANCE_FORBIDDEN_DIMS:
            vals = d.get(dim) or set()
            if not vals:
                continue
            if not vals <= (orig.get(dim) or set()):
                dropped.append(tag)
                break
    if not dropped:
        return enriched, []
    return _strip_tags(enriched, dropped), dropped


def keeps_original_tags(original: str, enriched: str) -> bool:
    """扩写结果必须是原文的**超集**（原有标签逐字保留，只增不改）。"""
    low = (enriched or "").lower()
    orig_tags = split_tags(original)
    if not orig_tags:
        return True
    return all(_norm(t) in low for t in orig_tags)


# --------------------------------------------------------------------------- #
# 质量前缀（按底模风格分档）
# --------------------------------------------------------------------------- #

# 底模风格 → 画质前缀（自然语言系必须为空：masterpiece / very aesthetic 这类词会被
# 自然语言系底模当成"要画出来的文字"渲染到图上，见 docstring 的警告）
QUALITY_PRESETS: dict[str, str] = {
    "tags": "masterpiece, best quality, very aesthetic, absurdres",
    "pony": "score_9, score_8_up, score_7_up",
    "nai": "best quality, very aesthetic, absurdres",
    "natural": "",
    "unknown": "masterpiece, best quality, very aesthetic, absurdres",
}

_NATURAL_FAMILIES = ("flux", "qwen", "z-image", "zimage", "z image", "krea", "sd3",
                     "natural", "自然语言")
_PONY_FAMILIES = ("pony",)
_TAG_FAMILIES = ("anima", "illustrious", "noobai", "noob", "sd15", "sd1.5", "sdxl",
                 "danbooru", "tags")


def prompt_style_of(*, is_anima: bool = False, base_name: str = "",
                    prompt_style: str = "", wf_name: str = "") -> str:
    """推断底模的提示词风格：tags / pony / natural / unknown。

    优先级：底模库显式配置(`prompt_style`) → 底模名/工作流名的家族特征 → is_anima 兜底。
    """
    blob = _norm(f"{base_name} {wf_name}")
    ps = _norm(prompt_style)
    if ps in ("natural", "自然语言", "sentence"):
        return "natural"
    if ps in ("danbooru", "danbooru tags", "tags", "标签"):
        return "pony" if any(k in blob for k in _PONY_FAMILIES) else "tags"
    if any(k in blob for k in _PONY_FAMILIES):
        return "pony"
    if any(k in blob for k in _NATURAL_FAMILIES):
        return "natural"
    if any(k in blob for k in _TAG_FAMILIES):
        return "tags"
    if is_anima:
        return "tags"
    return "unknown"


def quality_prefix_for(style: str, custom: str = "") -> str:
    """取该风格的画质前缀；custom 非空则一律用它（用户自定义优先）。"""
    c = (custom or "").strip()
    if c:
        return c
    return QUALITY_PRESETS.get(_norm(style), QUALITY_PRESETS["unknown"])


def ensure_quality_prefix(text: str, style: str, custom: str = "") -> tuple[str, str]:
    """缺失才补画质前缀，返回 (新文本, 实际加入的前缀)；自然语言系/已有前缀/空文本 → 原样返回。"""
    body = (text or "").strip()
    prefix = quality_prefix_for(style, custom)
    if not body or not prefix:
        return text, ""
    _tokens = [t for t in re.split(r"[,\s]+", prefix.lower()) if len(t) >= 4]
    low = body.lower()
    if any(t in low for t in _tokens):
        return text, ""       # 已经有画质词了（用户/模型自己写了），不重复加
    return f"{prefix}, {body}", prefix


def is_short_prompt(text: str, max_tags: int = 8) -> bool:
    """描述是否偏短（用于决定要不要做一次扩写）。"""
    return 0 < count_tags(text) <= max(1, int(max_tags))


# --------------------------------------------------------------------------- #
# 多人场景「连体畸形」兜底（v7.7.54）
# --------------------------------------------------------------------------- #
# 现象（用户反馈）：两人**贴在一起**（拥抱 / 贴脸 / 搭肩）时频繁出现
# 「一个角色长出两个头」「A 的身体长在 B 的身体上」这类融合畸形。
#
# 为什么这是结构性的、本模块只能压不能治：权重分组 `(charA:1.2), (charB:1.2)`
# 只做「注意力加权」，不做「空间隔离」——像素上重叠的两人，其注意力区域也重叠，
# 特征必然互串，下一步就是身体融合。真要根治得靠区域控制（Regional Prompter /
# 蒙版分区域）或分开生成再合成，插件当前没有这套能力（见 skills 的说明）。
#
# 本模块做两件「零风险」的事，都不改变用户想要的构图（**不**把人拉远）：
#   ① 负向注入：把畸形抑制词并入 negative（只增不覆盖）——负向只压畸形，
#      完全不影响两人靠多近，是唯一「贴近也能用」的杠杆；
#   ② 风险词告警：贴身构图时最该避免的是「裁切到腰线以上」和「笼统接触词」，
#      命中就在日志里点出来（供 LLM 下轮改写 / 排查），不擅自改写用户的词。

# 畸形抑制词（Anima / Illustrious / Pony 系通用）。不要在这里放 `solo`、`1girl`
# 之类「人数」词——那是抑制少人的，与多人目标相反。
#
# v7.7.71：并入 Anima 参考多人负向词表（参考插件 `anima/prompts/multi_person_prompt.py:41-54`）。
# 参考那套词直击「同脸 / 多脸 / 分屏 / 克隆」：twins（双胞胎）、duplicate characters、
# cloned character、extra person、fused characters、merged bodies、character sheet、
# multiple views、split screen、comic panels。下面按「参考词优先、通用词兜底」排列。
MULTI_FUSION_NEGATIVE: tuple[str, ...] = (
    # —— 参考多人负向（docs/anima-master）——
    "twins",
    "duplicate characters",
    "cloned character",
    "extra person",
    "extra girl",
    "extra boy",
    "fused characters",
    "merged bodies",
    "character sheet",
    "multiple views",
    "split screen",
    "comic panels",
    # —— 通用畸形抑制（连体 / 多头 / 多肢）——
    "conjoined",
    "fused bodies",
    "shared limbs",
    "conjoined twins",
    "two heads",
    "extra head",
    "multiple heads",
    "extra torso",
    "extra arms",
    "extra legs",
    "fused fingers",
    "fused hands",
    "extra fingers",
    "bad anatomy",
    "malformed limbs",
    "poorly drawn hands",
    "overlapping bodies",
)

# 多人判定：英文计数标签（与 main 的 _MULTI_COUNT_RE 同口径）+ 中文人数表述
_MULTI_EN_RE = re.compile(
    r"(?:[2-9]\s*girls|[2-9]\s*boys|multiple\s+girls|multiple\s+boys|"
    r"1girl\s+1boy|1boy\s+1girl|\bgroup\b|\bcrowd\b)",
    re.I,
)
_ZH_MULTI_RE = re.compile(
    # 「三个 / 两位 / 4名 / 两人 / 俩」+ 人称量词（后接「数」如「四位数」不算人数）
    r"(?:[两俩二三四五六七八九2-9]\s*(?:个|位|名)\s*(?:人|女孩|男孩|少年|少女|孩子|角色)(?!数)"
    r"|[两俩二三四五六七八九2-9]\s*(?:人|女孩|男孩|少年|少女)"
    r"|一群|合照|合影|多人|两人)"
)

# 贴身场景下最容易连体的写法（只告警）
_TIGHT_CROP_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bclose[\s\-_]?up\b", re.I), "close-up"),
    (re.compile(r"\bbust\s+shot\b", re.I), "bust shot"),
    (re.compile(r"\bupper\s*body\b", re.I), "upper body"),
    (re.compile(r"\b(?:face|head)\s*(?:close[\s\-_]?up|shot)\b", re.I), "face closeup"),
    (re.compile(r"(?:特写|胸像|半身|上半身)"), "中文近景（特写/胸像/半身）"),
)
_VAGUE_CONTACT_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bhug(?:ging|s)?\b", re.I), "hugging"),
    (re.compile(r"\bholding\s+hands?\b|\bhand\s*in\s*hand\b", re.I), "holding hands"),
    (re.compile(r"\blean(?:ing)?\s+on\s+each\s+other\b", re.I), "leaning on each other"),
    (re.compile(r"\barm\s*in\s*arm\b", re.I), "arm in arm"),
    (re.compile(r"\b(?:embrac\w*|arms?\s+around)\b", re.I), "embrace / arms around"),
    (re.compile(r"\bkiss(?:ing|ed)?\b", re.I), "kissing"),
    (re.compile(r"(?:拥抱|搂着|依偎|牵手|接吻|亲吻)"), "中文笼统接触词"),
)
# 明确部位的接触写法 = **推荐** 形态，先从文本里剔掉，避免被当成「笼统接触」告警
_SPECIFIC_CONTACT_RE = re.compile(
    r"\b(?:hug(?:ging|s)?\s+from\s+behind|cheek\s+to\s+cheek|"
    r"head\s+on\s+\w+\s+shoulder|lean(?:ing)?\s+on\s+(?:his|her|their|the)\s+shoulder|"
    r"shoulder\s+to\s+shoulder|side\s+hug)\b",
    re.I,
)


def is_multi_person(text: str) -> bool:
    """文本是否描述了 ≥2 人（英文计数标签或中文人数表述）。"""
    t = text or ""
    return bool(_MULTI_EN_RE.search(t) or _ZH_MULTI_RE.search(t))


def multi_person_hints(text: str) -> tuple[bool, list[str], list[str]]:
    """多人场景体检：返回 ``(是否多人, 贴身易连体的裁切词, 笼统接触词)``。

    两个风险清单都只用于**日志告警**，不会改写提示词。
    """
    t = text or ""
    if not is_multi_person(t):
        return False, [], []
    probe = _SPECIFIC_CONTACT_RE.sub(" ", t)
    crops = [label for rx, label in _TIGHT_CROP_PATTERNS if rx.search(t)]
    contact = [label for rx, label in _VAGUE_CONTACT_PATTERNS if rx.search(probe)]
    return True, crops, contact


def merge_multi_negative(negative: str, custom: str = "") -> tuple[str, list[str]]:
    """把畸形抑制词并入负向（**只增不覆盖**），返回 ``(新负向, 实际加入的词)``。

    - ``custom`` 非空时**用它替换**默认词表（逗号/分号/换行分隔），便于站点定制；
    - 已存在的词按小写去重，不重复追加；原负向的词序与内容一律保持不动。
    """
    parts = [p.strip() for p in re.split(r"[,，;\n]+", negative or "") if p.strip()]
    have = {_norm(p) for p in parts}
    words = [w.strip() for w in re.split(r"[,，;\n]+", custom or "") if w.strip()]
    if not words:
        words = list(MULTI_FUSION_NEGATIVE)
    added = [w for w in words if _norm(w) not in have]
    return ", ".join(parts + added), added


# --------------------------------------------------------------------------- #
# 「角色 LoRA 已自带形象」：剥掉 LLM 对形象的复述（v7.7.68）
# --------------------------------------------------------------------------- #
# 用户诉求（2026-10-14）：「我只是画两个角色并加了她们的 LoRA，为什么还要写发色发型？
# 角色 LoRA 自带画风与形象，只有我说了穿什么衣服、做什么动作才需要加。」
#
# 这条以前只对**角色卡**（character_card）有代码兜底（`strip_conflicting_appearance`），
# 角色 **LoRA** 路径完全没有约束，于是 LLM 照着 skill 里那条「每个角色的分组里还必须再写
# 2~4 个最有辨识度的外观标签」把外形复述一遍，后果有三：① 用弱文本标签**稀释/覆盖** LoRA
# 训练进去的形象；② 凭记忆写外形会写错；③ 两个角色外形词写得越像（甚至撞同一套衣服），
# 越画成同一张脸。
#
# 剥除范围分两档，由调用方按「用户本轮有没有干预该维度」分别决定：
#   - 外形（发色/发型/瞳色/兽化/体型）：LoRA 自带，用户没提就别复述；
#   - 服装配饰：同样由 LoRA 自带（v7.7.69 起纳入）——用户实测里 LLM 会**自己编**衣服，
#     而且两个角色编成同一套（两边都是 `black strapless dress` + `detached sleeves`），
#     标签系底模直接理解成双胞胎 → 两人同脸。真正的换装诉求（"给她换成红裙"）由
#     `outfit_intent` / `appearance_change_intent` 放行，绝不误伤。
LORA_ECHO_DIMS: tuple[str, ...] = FREEZE_DIMS
LORA_ECHO_DIMS_WITH_OUTFIT: tuple[str, ...] = FREEZE_DIMS + ("outfit",)

# 分组标签尾部的权重记号：`:1.20` / `:1.2)`
_GROUP_TAIL_RE = re.compile(r":\s*\d+(?:\.\d+)?\s*\)?$")


def _has_echo_dim(tag: str, dims: tuple[str, ...]) -> bool:
    """单个标签是否命中指定维度集合。"""
    return bool(set(appearance_dims(tag or "")) & set(dims))


def strip_lora_echo_appearance(
        text: str, dims: tuple[str, ...] = LORA_ECHO_DIMS
    ) -> tuple[str, list[str]]:
    """剥掉「复述角色 LoRA 自带形象」的标签，返回 ``(新文本, 被剥标签)``。

    - ``dims``：要剥的维度集合。默认只剥外形（发色/发型/瞳色/兽化/体型）；传入
      ``LORA_ECHO_DIMS_WITH_OUTFIT`` 会连**服装配饰**一起剥（用户本轮没提换装时用）。
    - 括号权重分组**逐标签剥**：`(:1.25)` 只摘掉组内的目标标签，组内其余标签
      （角色 tag、用户明说的动作/场景）与权重原样保留；剥空了的组整组删除；
    - 镜头、光影、氛围、场景、画师串等一律不动。
    """
    dropped: list[str] = []
    out: list[str] = []
    for tag in split_tags(text or ""):
        raw = tag.strip()
        body = raw[1:-1] if (raw.startswith("(") and raw.endswith(")")) else ""
        m = _GROUP_TAIL_RE.search(body) if body else None
        if body and m:
            kept: list[str] = []
            for sub in split_tags(body[: m.start()]):
                sub = sub.strip()
                if _has_echo_dim(sub, dims):
                    dropped.append(sub)
                elif sub:
                    kept.append(sub)
            if kept:
                out.append(f"({', '.join(kept)}{body[m.start():]})")
            continue
        if _has_echo_dim(raw, dims):
            dropped.append(raw)
            continue
        out.append(raw)
    if not dropped:
        return text or "", []
    return ", ".join(t for t in out if t), dropped


# 「用户本轮明确要改外形」的意图判定。命中即**整体放弃剥除**（保守：宁可少剥也不改错）。
_APPEARANCE_CHANGE_RE = re.compile(
    r"(?:染成|染回|染成粉|剪短|剪长|剪成|留成|留长|留短|改成|换成|变成|变成粉|加.{0,4}(?:耳|角|尾巴|发)"
    r"|去掉|摘掉|脱下|换上|穿上"
    r"|dye|dyeing|shorten|cut\s+(?:her|his|their|it)?\s*hair|change\s+(?:her|his|their)?\s*hair"
    r"|\bwithout\b\s+(?:cat\s*ears|ears|horns|tail|hair)|grow\s+long\s+hair)",
    re.I,
)


def appearance_change_intent(text: str) -> bool:
    """用户原话里是否有「明确要改外形」的意图（染/剪/换/去掉/戴…）。"""
    return bool(_APPEARANCE_CHANGE_RE.search(text or ""))


# 用户本话里提到服装/配饰（含"穿/戴/脱/套…"动词，或直接说了服饰名）。
# 命中即视为「用户在管这一维度」→ 不剥服装，让 LLM 写的衣服保留。
_OUTFIT_INTENT_RE = re.compile(
    r"(?:穿|戴|系|挂|披|套|换|脱|摘|褪|裸|着)(?:上|着|了|过)?[^，。！？\n]{0,8}?"
    r"(?:衣|裙|服|装|鞋|靴|袜|帽|披肩|斗篷|围巾|手套|头饰|耳环|项链|饰品|围裙|泳衣|和服|校服)"
    r"|(?:undress|topless|swimsuit|bikini|outfit\s+change)",
    re.I,
)


def outfit_intent(text: str) -> bool:
    """用户原话里是否干预了服装/配饰（提到服饰名或穿/戴/脱等动作）。"""
    t = text or ""
    if _OUTFIT_INTENT_RE.search(t):
        return True
    if appearance_dims(t).get("outfit"):
        return True
    # 兜底：中英连写时 `\b` 不成立（"戴个choker" 里"个"与 c 之间没有词边界，
    # Python 的 \w 含 CJK 字母），所以英文服饰词在**用户原话**这种短文本上
    # 直接做子串判断更可靠。
    low = t.lower()
    return any(w in low for w in _EN_OUTFIT_HINTS)


_EN_OUTFIT_HINTS: tuple[str, ...] = (
    "choker", "dress", "skirt", "shirt", "coat", "jacket", "sweater", "hoodie",
    "uniform", "kimono", "suit", "boots", "shoes", "socks", "gloves", "scarf",
    "hat", "ribbon", "ornament", "hairpin", "tassel", "bikini", "swimsuit",
    "apron", "cloak", "robe", "necklace", "earrings", "ring",
)


# --------------------------------------------------------------------------- #
# 多人撞脸自查：各角色分组共有的外形 / 服装标签（v7.7.68）
# --------------------------------------------------------------------------- #

_GROUP_SCAN_RE = re.compile(r"\(([^()]*:\s*\d+(?:\.\d+)?)\)")

# --------------------------------------------------------------------------- #
# 多人计数标签冲突清理（v7.7.70）
# --------------------------------------------------------------------------- #
# 实测案例：`2girls, 1girl standing on the left and 1girl standing on the right, ...`
# —— LLM 把「左右站位」写成一句自然语言，里面还各带一个 `1girl`，与 `2girls` 直接打架
# （此前只清分组内的计数标签，裸的自然语言句清不掉）。危害：① 人数语义冲突，模型可能
# 只画一个或画三个；② 自然语言元描述在标签系底模里是垃圾 token。
#
# 规则：多人提示词里凡与多人计数冲突的**单人计数**（`1girl` / `1boy` / `solo`）一律剔除；
# 含这些词的**自然语言标签**整条剔除（它们承载不了站位信息，CLIP 权重分组也用不上）。

_SINGLE_COUNT_RE = re.compile(r"^(?:solo|1girl|1boy)$", re.I)


def clean_multi_count_conflicts(text: str) -> tuple[str, list[str]]:
    """多人提示词里剔除与人数冲突的单人计数标签，返回 ``(新文本, 被剔除项)``。

    非多人场景原样返回。分组内与分组外都处理；分组被剥空时整组删除。
    """
    if not is_multi_person(text or ""):
        return text or "", []
    dropped: list[str] = []
    out: list[str] = []
    for tag in split_tags(text or ""):
        raw = tag.strip()
        body = raw[1:-1] if (raw.startswith("(") and raw.endswith(")")) else ""
        m = _GROUP_TAIL_RE.search(body) if body else None
        if body and m:
            kept: list[str] = []
            for s in split_tags(body[: m.start()]):
                s = s.strip()
                if not s:
                    continue
                if _SINGLE_COUNT_RE.match(s) or re.search(r"\b(?:solo|1girl|1boy)\b", s, re.I):
                    dropped.append(s)
                else:
                    kept.append(s)
            if kept:
                out.append(f"({', '.join(kept)}{body[m.start():]})")
            continue
        if _SINGLE_COUNT_RE.match(raw) or re.search(r"\b(?:solo|1girl|1boy)\b", raw, re.I):
            dropped.append(raw)
            continue
        out.append(raw)
    if not dropped:
        return text or "", []
    return ", ".join(t for t in out if t), dropped


# --------------------------------------------------------------------------- #
# 参考混合式多人渲染（v7.7.71）
# --------------------------------------------------------------------------- #
# 来源声明（v7.7.72 补）：`_Refs/anima-master` 是**第三方 AstrBot 插件**，不是 Anima 官方仓库。
# 下面这套「混合式 + 身份锚点」是**该参考插件的方案选择**（它实测可用、且有测试断言背书），
# 不等于 Anima 官方规范；模型层面的权威依据只有 circlestone-labs 的 Anima 模型卡
# （架构 / Turbo 10 步 CFG 1 / 标签与自然语言混用 / 权重括号建议比 SDXL 更大 / 艺术家名带 @）。
# 另注：模型卡**没有**任何关于"多角色 LoRA 同开"的说明，别把参考插件的取舍当成模型限制。
#
# 依据该参考插件源码：
#   · `anima/prompts/multi_person_prompt.py:164-241` 多人规划模板；
#   · `tests/test_multi_person_prompt.py:273-294` 断言的最终提示词形态；
#   · `docs/prompting.md:53`「公共人数/场景/镜头走 Tag 清洗，人物块与互动关系**不**送清洗」。
#   · `anima/prompts/multi_person_prompt.py:164-241` 多人规划模板；
#   · `tests/test_multi_person_prompt.py:273-294` 断言的最终提示词形态；
#   · `docs/prompting.md:53`「公共人数/场景/镜头走 Tag 清洗，人物块与互动关系**不**送清洗」。
# 参考实现的最终形态是**混合式**：`2girls, duo, holding hands, <公共tag…>` +
# `green-eyed girl: green eyes, dark school uniform, …`（每人一个自然语言块）+
# 互动自然语言句。**不用 CLIP 权重括号绑定身份**——参考只给"易混淆身份锚点"加
# `(tag:1.3)`（`anima/prompts/prompt_pipeline.py:672-677`），从不文档化 1.2 这类通用权重。
#
# 本函数把「权重分组」形态重写为上述参考实现形态；任一角色拿不到权威锚点就**放弃重构、
# 原样返回**（宁可不改，也不能把两人写成没区分的两个"空标签人"）。

_ANCHOR_DIM_ORDER: tuple[str, ...] = (
    "hair_color", "eye_color", "hair_style", "animal", "outfit", "body",
)
_ANCHOR_SKIP_TAGS = {"1girl", "1boy", "solo", "girl", "boy", "child", "female", "male"}


def identity_anchor_tags(tag_str: str, limit: int = 5) -> list[str]:
    """从 Danbooru / 角色卡锚点标签串里挑出「身份锚点」（参考要求 3~6 个可见特征）。

    按维度优先级取（发色 → 瞳色 → 发型 → 兽化 → 服装 → 体型），跳过角色 tag 本身
    （`name (series)` 形态）与人数词；同一维度最多取 2 个，避免全是发色。
    """
    by_dim: dict[str, list[str]] = {}
    for tag in split_tags(tag_str or ""):
        t = tag.strip()
        if not t:
            continue
        if re.search(r"\\?\(.*\)\s*$", t):        # 角色 tag（含作品消歧括号）不作锚点
            continue
        if _norm(t) in _ANCHOR_SKIP_TAGS:
            continue
        for dim in APPEARANCE_RULES:
            if dim in by_dim and len(by_dim[dim]) >= 2:
                continue
            if appearance_dims(t).get(dim):
                by_dim.setdefault(dim, []).append(t)
                break
    picked: list[str] = []
    for dim in _ANCHOR_DIM_ORDER:
        for t in by_dim.get(dim, []):
            if t not in picked:
                picked.append(t)
    return picked[: max(1, int(limit))]


def visual_label_for(anchors: list[str], fallback: str = "") -> str:
    """按参考 `visual_label` 规则生成**唯一的可见特征描述**（不含名字、不含 left/right）。

    例：`["red hair", "fox ears"]` → `red-haired fox`；`["long white hair", "orange eyes"]`
    → `white-haired`。参考模板明确禁止用名字 / 序数 / left / right 当 visual_label
    （`multi_person_prompt.py:221`），所以这里只从外观维度拼。
    """
    hair = eye = animal = ""
    for t in anchors or []:
        d = appearance_dims(t)
        if not hair and d.get("hair_color"):
            hair = sorted(d["hair_color"])[0]
        if not eye and d.get("eye_color"):
            eye = sorted(d["eye_color"])[0]
        if not animal and d.get("animal"):
            m = re.match(r"([a-z]+)", sorted(d["animal"])[0])
            animal = m.group(1) if m else ""
    seg: list[str] = []
    if hair:
        words = hair.split()
        seg.append(f"{words[0]}-haired" if len(words) == 1 else " ".join(words[:-1]) + "-haired")
    elif eye:
        seg.append(f"{eye}-eyed")
    if animal:
        seg.append(animal)
    return " ".join(seg) if seg else (fallback or "")


def build_hybrid_multi_person(
    positive: str, per_char: list[dict], assign_personal: bool = False
) -> tuple[str, list[str]]:
    """把权重分组形态的多人提示词重写为参考混合式，返回 ``(新提示词, 改写说明)``。

    ``per_char`` 每项 ``{"anchors": [权威锚点]}``，顺序与提示词里权重分组出现顺序一致。规则：

    - 公共部分（人数 / 关系 / 场景 / 镜头 / 光影）保留原 tag；
    - 每个角色展开成一个自然语言块 ``visual_label: tag1, tag2, …``，块内不再加权；
    - 组内的裸触发词、以及与锚点重复的标签剔除（身份已由 label + 锚点表达）；
    - ``assign_personal=True`` 时，把**公共区里的角色专属标签**（服饰/体型/发色/动作/表情，
      实测 LLM 常把"各自的体型服饰动作"写在括号外）按出现顺序**轮流归位**到各角色块——
      留在公共区会被模型理解成两人共有，正是同脸的主因之一。只在用户本轮明确要求
      "各自不同"时启用（见 `differentiation_intent`），否则公共标签两人共有是用户自己的选择；
    - **任一组没有权威锚点就整体放弃重构**（返回原文）：没锚点的角色块无法与他人区分，
      硬写等于造出两个"空标签人"，比现状更糟。
    """
    try:
        text = (positive or "").strip()
        if not text or not per_char:
            return positive, []
        groups: list[list[str]] = []
        rest_parts: list[str] = []
        last = 0
        for m in _GROUP_SCAN_RE.finditer(text):
            inner = m.group(1)
            body = inner[: inner.rindex(":")] if ":" in inner else inner
            groups.append([s.strip() for s in split_tags(body) if s.strip()])
            rest_parts.append(text[last:m.start()])
            last = m.end()
        rest_parts.append(text[last:])
        if not groups or len(groups) != len(per_char):
            return positive, []
        common = [t.strip() for part in rest_parts for t in split_tags(part) if t.strip()]
        # 公共区里的角色专属标签 → 按顺序轮流归位给各角色（仅在用户要求差异化时）
        assigned: list[list[str]] = [[] for _ in groups]
        if assign_personal:
            common, personal = classify_common_tags(", ".join(common))
            for _i, _t in enumerate(personal):
                assigned[_i % len(groups)].append(_t)
        blocks: list[str] = []
        notes: list[str] = []
        for gi, (grp, info) in enumerate(zip(groups, per_char)):
            anchors = [a for a in (info.get("anchors") or []) if a]
            if not anchors:
                return positive, []
            _anorm = {_norm(a) for a in anchors}
            # 触发词 = 组内第一个「不是外观维度也不是人数词」的标签（Danbooru 角色 tag）
            trigger = ""
            for t in grp:
                if _norm(t) in _ANCHOR_SKIP_TAGS or appearance_dims(t):
                    continue
                trigger = t
                break
            label = visual_label_for(anchors, fallback=trigger)
            if not label:
                return positive, []
            keep = [t for t in grp
                    if _norm(t) != _norm(trigger) and _norm(t) not in _anorm]
            merged: list[str] = []
            seen: set[str] = set()
            for t in keep + assigned[gi] + anchors:
                if t and _norm(t) not in seen:
                    seen.add(_norm(t))
                    merged.append(t)
            blocks.append(f"{label}: " + ", ".join(merged) + ".")
            notes.append(
                f"{label} ← [{', '.join(anchors)}]"
                + (f" + 归位 [{', '.join(assigned[gi])}]" if assigned[gi] else "")
            )
        parts = ([", ".join(common)] if common else []) + blocks
        return ", ".join(p for p in parts if p), notes
    except Exception:
        return positive, []


# 角色专属 vs 公共：LLM 常把"各自的体型/服饰/动作"写到公共区（实测），而公共区的标签
# 模型理解成**两人共有** → 同脸。参考实现对此的硬规则是：公共部分只放人数/关系/场景/镜头/
# 光影，每个角色的 clothing/expression/pose/props 必须在自己的语义块里
# （`_Refs/anima-master` 多人模板的 `common_tags` 与 per-character 字段划分）。
#
# 动作/表情词表刻意保守：只收"明确属于某个人"的动作与表情；`holding hands` /
# `looking at each other` 这类**关系**词不进名单（两人共有才对）。
_PERSONAL_ACTION_TAGS = frozenset((
    "standing", "sitting", "walking", "running", "jumping", "kneeling", "crouching",
    "lying", "sleeping", "squatting", "leaning", "stretching", "dancing", "flying",
    "holding", "carrying", "raising arm", "raised arm", "arms crossed", "waving",
    "reaching", "pointing", "touching", "covering", "hand on cheek",
    "smile", "smiling", "blush", "blushing", "expressionless", "pouting", "crying",
    "tears", "closed eyes", "open mouth", "angry", "surprised", "shy", "embarrassed",
))
_PERSONAL_ONLY_DIMS = ("outfit", "body", "animal", "hair_color", "eye_color", "hair_style")


def classify_common_tags(text: str) -> tuple[list[str], list[str]]:
    """把顶层标签分成 ``(公共标签, 角色专属标签)``。

    角色专属 = 命中外观维度（服饰/体型/兽化/发色/发型/瞳色）或动作/表情词表。
    其余（人数、关系、场景、镜头、光影、氛围、画质、画师串）一律算公共。判定保守，
    宁可漏判（漏判只是维持现状）也不误判（误判会把公共标签塞进某个人）。
    """
    common: list[str] = []
    personal: list[str] = []
    for tag in split_tags(text or ""):
        t = tag.strip()
        if not t:
            continue
        dims = appearance_dims(t)
        if any(dims.get(d) for d in _PERSONAL_ONLY_DIMS):
            personal.append(t)
            continue
        if _norm(t) in _PERSONAL_ACTION_TAGS:
            personal.append(t)
            continue
        common.append(t)
    return common, personal


# 用户"要求两人不同"的显式意图（中文/英文）。只有用户确实要求差异化时，才把公共区的
# 角色专属标签按顺序**轮流归位**给各角色——否则公共动作/服饰两人共有正是用户自己的选择。
_DIFFERENTIATE_INTENT_RE = re.compile(
    r"各自|分别|不同|不一样|各有|都不同|区别|区分|差异化|迥异|迥然|完全不同|"
    r"distinct|different|varied|various",
    re.I,
)


def differentiation_intent(text: str) -> bool:
    """用户本轮是否明确要求「两个角色要有差异」（各自不同 / 各有各的）。"""
    return bool(_DIFFERENTIATE_INTENT_RE.search(text or ""))


def multi_shared_appearance(text: str) -> tuple[bool, list[str]]:
    """多人时各权重分组**共有**的外形/服装标签（撞脸高发信号）。

    返回 ``(是否多人, 共有标签列表)``。实测过的翻车形态：两个角色分组都写
    ``black strapless dress`` + 同款 sleeves——标签系底模眼里这就是双胞胎，
    于是两张脸被平均成同一张。本函数只做检测与告警，**不改写提示词**
    （到底换哪件衣服得听用户的）。
    """
    if not is_multi_person(text or ""):
        return False, []
    groups: list[set[str]] = []
    for m in _GROUP_SCAN_RE.finditer(text or ""):
        inner = m.group(1)
        inner = inner[: inner.rindex(":")] if ":" in inner else inner
        tags: set[str] = set()
        for t in split_tags(inner):
            t = t.strip()
            if not t:
                continue
            d = appearance_dims(t)
            if (set(d) & set(LORA_ECHO_DIMS)) or d.get("outfit"):
                tags.add(_norm(t))
        if tags:
            groups.append(tags)
    if len(groups) < 2:
        return True, []
    shared = set(groups[0]).intersection(*groups[1:])
    return True, sorted(shared)
