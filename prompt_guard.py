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
                   r"cape|scarf|gloves?|stockings?|thighhighs?|pantyhose|boots?|shoes?|hat|"
                   r"ribbon|necktie|tie|choker|necklace|earrings?|glasses|goggles)\b", re.I),
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
