"""角色卡片（Character Card）逻辑层：人格解析、命中、渲染与注入。

存储见 ``character_store.py``；本模块只负责「怎么把卡片变成提示词」：

1. **人格解析**（「画你」链路）：``resolve_persona_name()`` 取当前会话最终生效的人格名
   （AstrBot 4.27.4：``context.persona_manager.resolve_selected_persona()``，
   回退 ``get_default_persona_v3(umo)``；persona 的 ``name`` 即 persona_id）。
2. **命中**：在用户原话 / 提示词里扫角色名与别名（``CharacterStore.match_characters``），
   再叠加「你」这类自称 → 绑定当前人格的卡。
3. **渲染**：
   - 单角色：把锚点标签**追加**进提示词（去重，已存在的不重复写）；
   - 多角色：生成计数标签 + 每角色一个 ``(标签:权重)`` 分组，并把模型自己写的权重分组
     （通常就是它每轮重新发明的角色外观）**剥掉**，避免两套描述打架；
   - 锚点自带 ``lora_name`` 时，返回给调用方并入 loras 参数。
4. **回写**（v6.3.0）：出图命中了哪个锚点，就把成品图按那个锚点自动关联进角色卡
   （``auto_link_generated``）—— 引用 ``gallery/`` 里的原文件、不复制，省掉人工「存参考图」。
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger("astrbot_plugin_comfyui_anima.character")

# 「你」类自称：命中即按当前会话人格找卡。
# ★注意中文语序：用户说「画一张**你和**薄荷的合照」——「你和」才是常见写法（v5.16.1
# 实测修正：早期只写了「和你」，导致这句最典型的请求反而命中不了人格绑定卡）。
_SELF_RE = re.compile(
    r"(?:你自己|你本人|你与|你和|和你|跟你|画你|你的自拍|自拍|你的样子|你长什么样|"
    r"bot\s*自己|机器人自己)"
)
# 计数标签（用于推断性别与人数）
_FEMALE_TAG = re.compile(r"^\d*girls?$|^female$", re.IGNORECASE)
_MALE_TAG = re.compile(r"^\d*boys?$|^male$", re.IGNORECASE)
_COUNT_TAG = re.compile(
    r"^(?:solo|\d+girls?|\d+boys?|multiple\s+girls|multiple\s+boys|"
    r"1girl\s+1boy|1boy\s+1girl|group|crowd)$",
    re.IGNORECASE,
)
# 画质前缀（打头用）：多角色重组时把这些标签提到最前
_QUALITY_RE = re.compile(
    r"masterpiece|best quality|high quality|very aesthetic|absurdres|"
    r"ultra-?detailed|highres|detailed|score_\d+",
    re.IGNORECASE,
)


def _split_top_commas(text: str) -> list[str]:
    """按顶层逗号切分（忽略括号内逗号）。"""
    out, buf, depth = [], [], 0
    for ch in text or "":
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def strip_weight_groups(text: str) -> str:
    """剥掉所有「末尾带权重」的顶层括号分组，返回剩余标签串。

    多角色注入时用来清掉模型自己写的分组（它每轮都会重新发明一套角色外观），
    只保留场景/动作/构图等非分组标签，让卡片锚点成为唯一的外观来源。
    """
    s = text or ""
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "(":
            depth = 0
            j = i
            while j < n:
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            inner = s[i + 1:j] if j < n else s[i + 1:]
            if j < n and re.search(r":\s*\d+(?:\.\d+)?\s*$", inner):
                i = j + 1  # 整个分组丢弃
                continue
        out.append(ch)
        i += 1
    joined = "".join(out)
    joined = re.sub(r"\s*,\s*(?:,\s*)+", ", ", joined).strip().strip(",").strip()
    return joined


def _card_tags(anchor: dict, drop_count_tags: bool = False) -> list[str]:
    """锚点的正标签串 → 去重后的标签列表（顶层逗号切分）。

    ``drop_count_tags=True`` 时剔除 `1girl` / `2girls` 这类计数标签——多人分组时
    计数由**全局那一个**计数标签统一表达，组内再写 `1girl` 只会互相打架。
    """
    tags: list[str] = []
    for t in _split_top_commas(anchor.get("positive") or ""):
        if not t:
            continue
        if drop_count_tags and _COUNT_TAG.match(t.strip()):
            continue
        if t not in tags:
            tags.append(t)
    return tags


def _merge_negative(negative: str, anchors: list[dict]) -> str:
    """把锚点负向合并进提示词负向（去重、以逗号拼接）。"""
    parts = [p.strip() for p in re.split(r"[,，\n]+", negative or "") if p.strip()]
    for a in anchors:
        for p in re.split(r"[,，\n]+", (a.get("negative") or "")):
            p = p.strip()
            if p and p not in parts:
                parts.append(p)
    return ", ".join(parts)


def guess_count_tag(card_anchor_pairs: list[tuple[dict, dict]]) -> str:
    """按各卡片的锚点标签推断计数标签（1girl / 2girls / 1girl 1boy…）。

    没有任何性别线索时默认按全女处理（本插件主要面向动漫女角色场景）。
    """
    girls = 0
    boys = 0
    unknown = 0
    for _card, anchor in card_anchor_pairs:
        tags = _card_tags(anchor)
        g = any(_FEMALE_TAG.match(t) for t in tags)
        b = any(_MALE_TAG.match(t) for t in tags)
        if b and not g:
            boys += 1
        elif g and not b:
            girls += 1
        else:
            unknown += 1
    n = len(card_anchor_pairs)
    if boys and not girls and not unknown:
        return f"{boys}boys" if boys > 1 else "1boy"
    if girls and not boys and not unknown:
        return f"{girls}girls" if girls > 1 else "1girl"
    if girls or boys:
        # 混合：补足未知数量
        girls += unknown
        if boys == 0:
            return f"{girls}girls" if girls > 1 else "1girl"
        if girls == 0:
            return f"{boys}boys" if boys > 1 else "1boy"
        return "1girl 1boy" if (girls == 1 and boys == 1) else f"{girls}girls {boys}boys"
    # 全未知：按总数默认全女
    return f"{n}girls" if n > 1 else "1girl"


async def resolve_persona_name(self, event) -> str:
    """取当前会话最终生效的人格名（取不到返回空串，全程容错）。"""
    try:
        pm = getattr(self.context, "persona_manager", None)
        if pm is None:
            return ""
        umo = getattr(event, "unified_msg_origin", "") or ""
        plat = ""
        try:
            plat = event.get_platform_name() or ""
        except Exception:
            plat = ""
        # 首选 resolve_selected_persona：它会读会话级强制人格（session_service_config）
        try:
            _resolver = getattr(pm, "resolve_selected_persona", None)
            if _resolver is not None:
                _pid, persona, _forced, _web = await _resolver(
                    umo=umo,
                    conversation_persona_id=None,
                    platform_name=plat,
                    provider_settings=None,
                )
                nm = _persona_name(persona)
                if nm:
                    return nm
        except Exception as e:
            logger.warning(f"【角色卡】 resolve_selected_persona 失败（回退默认人格）: {e}")
        try:
            persona = await pm.get_default_persona_v3(umo=umo)
            return _persona_name(persona)
        except Exception as e:
            logger.warning(f"【角色卡】 get_default_persona_v3 失败: {e}")
    except Exception as e:
        logger.warning(f"【角色卡】 人格解析异常: {e}")
    return ""


def _persona_name(persona) -> str:
    """从 Personality（dict-like / 对象）里取 name（即 persona_id）。"""
    if not persona:
        return ""
    try:
        if isinstance(persona, dict):
            return str(persona.get("name") or "").strip()
        return str(getattr(persona, "name", "") or "").strip()
    except Exception:
        return ""


def _self_referenced(text: str) -> bool:
    return bool(_SELF_RE.search(text or ""))


def collect_hits(self, text: str, prefer_anchor_text: str = "") -> list[tuple[dict, dict]]:
    """从文本里解析出 (角色卡, 锚点) 列表。

    锚点选择：若 ``prefer_anchor_text``（通常是用户原话）里出现了该角色的某个锚点名，
    则用那个锚点，否则用主锚点。
    """
    store = getattr(self, "character", None)
    if store is None:
        return []
    hits: list[tuple[dict, dict]] = []
    seen: set[int] = set()
    for card in store.match_characters(text):
        cid = int(card["id"])
        if cid in seen:
            continue
        anchor = None
        _pt = (prefer_anchor_text or "").lower()
        if _pt:
            for a in store.list_anchors(cid):
                _n = (a.get("name") or "").strip().lower()
                if _n and len(_n) >= 2 and _n in _pt:
                    anchor = a
                    break
        if anchor is None:
            anchor = store.get_anchor(cid)
        if anchor is None:
            logger.info(f"【角色卡】 命中角色「{card.get('name')}」但没有锚点，跳过注入")
            continue
        seen.add(cid)
        hits.append((card, anchor))
    return hits


async def resolve_hits(
    self, event, user_text: str, prompt_text: str
) -> list[tuple[dict, dict]]:
    """综合「用户原话 + 提示词」解析命中的角色卡，并叠加「你」→ 当前人格绑定卡。"""
    cfg = _cfg(self)
    if not cfg.get("enabled", True):
        return []
    scan = " ".join([(user_text or ""), (prompt_text or "")]).strip()
    hits = collect_hits(self, scan, prefer_anchor_text=user_text or scan)
    if cfg.get("auto_bind_persona", True) and _self_referenced(scan):
        store = getattr(self, "character", None)
        persona = await resolve_persona_name(self, event)
        if store is not None and persona:
            card = store.find_by_persona(persona)
            if card is not None and int(card["id"]) not in {int(c["id"]) for c, _a in hits}:
                anchor = collect_hits(self, card["name"], prefer_anchor_text=user_text)
                anchor = anchor[0][1] if anchor else store.get_anchor(int(card["id"]))
                if anchor is not None:
                    hits.insert(0, (card, anchor))
                    logger.info(
                        f"【角色卡】 自称「你」→ 当前人格「{persona}」绑定角色「{card.get('name')}」"
                        f"（锚点「{anchor.get('name')}」）"
                    )
        elif store is not None:
            logger.info(
                f"【角色卡】 检测到自称「你」，但未解析到人格"
                f"（persona={persona or '未知'}）或无绑定角色卡，跳过人格绑定注入"
            )
    if hits:
        logger.info(
            "【角色卡】 命中角色: " + "、".join(
                f"{c.get('name')}[{a.get('name')}]" for c, a in hits
            )
        )
    return hits


def _cfg(self) -> dict:
    try:
        _c = self._cfg("character_card", {}) or {}
        return _c if isinstance(_c, dict) else {}
    except Exception:
        return {}


def inject(
    self,
    prompt: str,
    hits: list[tuple[dict, dict]],
    cfg: dict | None = None,
    negative: str = "",
) -> dict:
    """把命中的角色锚点注入提示词（确定性渲染）。

    返回 ``{"prompt": 新提示词, "negative": 合并后负向（可能为空串）, "loras": [LoRA 名...],
    "mode": "single"|"multi"|"none", "note": 日志说明}``。

    - 单角色：锚点标签**追加**进原提示词（已存在的不重复写），负向合并；
    - 多角色（≥2）：生成计数标签 + ``(标签:权重)`` 分组，并剥掉模型自己的权重分组，
      只保留场景/动作类标签，避免两套角色描述互相打架。

    ``negative``（v6.0.0 新增）：调用方现有的负向提示词，会与锚点负向**合并**后返回。
    旧实现只返回锚点负向，调用方直接覆盖 → 用户/模型原有的负向提示词被丢掉。
    """
    _cfg_ = cfg if isinstance(cfg, dict) else _cfg(self)
    base = (prompt or "").strip()
    if not hits:
        return {"prompt": base, "negative": "", "loras": [], "mode": "none", "note": ""}
    try:
        _w_default = float(_cfg_.get("default_weight") or 1.2)
    except (TypeError, ValueError):
        _w_default = 1.2
    loras: list[str] = []
    for card, _a in hits:
        _ln = (card.get("lora_name") or "").strip()
        if _ln and _ln not in loras:
            loras.append(_ln)
    negatives = _merge_negative(negative or "", [a for _c, a in hits])

    # ---- 单角色 ----
    if len(hits) == 1:
        card, anchor = hits[0]
        existing = {t.strip().lower() for t in _split_top_commas(base)}
        _add = [
            t for t in _card_tags(anchor)
            if t and t.strip().lower() not in existing and t.strip().lower() not in base.lower()
        ]
        new_prompt = base
        if _add:
            new_prompt = (base + ", " if base else "") + ", ".join(_add)
        logger.info(
            f"【角色卡·注入】 单角色「{card.get('name')}」锚点「{anchor.get('name')}」"
            f"追加标签 {len(_add)} 个: {_add}"
        )
        return {
            "prompt": new_prompt, "negative": negatives, "loras": loras,
            "mode": "single", "note": f"单角色 {card.get('name')}/{anchor.get('name')}",
        }

    # ---- 多角色：计数标签 + 每组一个权重分组 ----
    if not _cfg_.get("auto_group_multi", True):
        # 关闭自动分组：退化为「逐个追加标签」（不推荐，但保留开关语义）
        merged = base
        for card, anchor in hits:
            for t in _card_tags(anchor):
                if t and t.strip().lower() not in merged.lower():
                    merged = (merged + ", " if merged else "") + t
        return {
            "prompt": merged, "negative": negatives, "loras": loras,
            "mode": "multi", "note": "多角色（未分组，auto_group_multi=false）",
        }
    count_tag = guess_count_tag(hits)
    groups: list[str] = []
    for _card, anchor in hits:
        # 组内剔除计数标签（1girl/2girls…）：计数只由全局 count_tag 表达，
        # 组内再写会让每个角色分组各自声明「一个人」，CPLIP 侧互相打架。
        tags = _card_tags(anchor, drop_count_tags=True) or _card_tags(anchor)
        if not tags:
            continue
        try:
            _w = float(anchor.get("weight") or _w_default)
        except (TypeError, ValueError):
            _w = _w_default
        _w = max(1.1, min(1.3, _w))
        groups.append("(" + ", ".join(tags) + f":{_w:.2f})")
    if len(groups) != len(hits):
        # v7.6.0：有角色卡拿不出可用锚点标签（锚点空/被清空）时**不剥**模型自己写的分组——
        # 否则锚点与模型分组两边都空，该角色特征直接全丢（比串味更糟）。改为逐个追加锚点标签。
        logger.warning(
            f"【角色卡·注入】 {len(hits)} 个角色里只有 {len(groups)} 个锚点有可用标签，"
            f"保留模型原有分组（不剥），仅追加锚点标签"
        )
        merged = base
        for _card, anchor in hits:
            for t in _card_tags(anchor, drop_count_tags=True) or _card_tags(anchor):
                if t and t.strip().lower() not in merged.lower():
                    merged = (merged + ", " if merged else "") + t
        return {
            "prompt": merged, "negative": negatives, "loras": loras,
            "mode": "multi",
            "note": f"多角色（{len(groups)}/{len(hits)} 个锚点有标签，保留模型分组）",
        }
    rest = strip_weight_groups(base)
    # 去掉 rest 里重复的计数标签与已被分组覆盖的标签
    _group_blob = " ".join(groups).lower()
    rest_tags = [
        t for t in _split_top_commas(rest)
        if not _COUNT_TAG.match(t.strip()) and t.strip().lower() not in _group_blob
    ]
    # 画质前缀必须打头（masterpiece / best quality / absurdres…），再计数标签，
    # 再角色分组，最后才是场景/动作标签——顺序会影响 CLIP 的注意力分配。
    _quality = [t for t in rest_tags if _QUALITY_RE.search(t)]
    _others = [t for t in rest_tags if not _QUALITY_RE.search(t)]
    parts = _quality + [count_tag] + groups + _others
    new_prompt = ", ".join(p for p in parts if p and p.strip())
    logger.info(
        f"【角色卡·注入】 多角色分组（{len(groups)} 组，计数={count_tag}）: {new_prompt}"
    )
    return {
        "prompt": new_prompt, "negative": negatives, "loras": loras,
        "mode": "multi",
        "note": "多角色 " + "、".join(f"{c.get('name')}/{a.get('name')}" for c, a in hits),
    }


def describe_hits(hits: list[tuple[dict, dict]]) -> str:
    """把命中结果格式化成给 LLM / 用户看的一行摘要。"""
    if not hits:
        return "（未命中角色卡）"
    return "；".join(
        f"{c.get('name')}（锚点「{a.get('name')}」：{(a.get('positive') or '')[:80]}…）"
        if len(a.get("positive") or "") > 80
        else f"{c.get('name')}（锚点「{a.get('name')}」：{a.get('positive') or ''}）"
        for c, a in hits
    )


# ---------------------------------------------------------------------- #
# M3：参考图落地（含 NSFW 打标）与联网补全（走已有 danbooru 标签服务）
# ---------------------------------------------------------------------- #
async def _detect_ref_nsfw(self, path: str) -> float:
    """对参考图跑一遍 NSFW 检测，返回置信度；不可用/失败返回 -1（不阻断落地）。"""
    import asyncio as _aio

    try:
        try:
            from .nsfw_detector import get_detector
        except ImportError:
            from nsfw_detector import get_detector
        _thr = 0.5
        try:
            if getattr(self, "gallery", None) is not None:
                _thr = self.gallery._nsfw_threshold()
        except Exception:
            pass
        det = get_detector(_thr)
        if det is None:
            return -1.0
        _is, score, available = await _aio.to_thread(det.detect, path)
        return float(score) if available else -1.0
    except Exception as e:
        logger.warning(f"【角色卡】 参考图 NSFW 检测异常（跳过打标）: {e}")
        return -1.0


async def land_ref(
    self,
    char_id,
    data: bytes,
    filename: str = "",
    url: str = "",
    note: str = "",
    anchor_id: int = 0,
    created_by: str = "",
    origin: str = "upload",
) -> dict | None:
    """参考图落地：写文件（内容寻址）→ NSFW 打标 → 落库，返回记录。

    `url` 非空表示来源是联网抓取，受 `character_card.allow_web_fetch` 约束
    （关闭时直接拒绝，避免用户没授权就联网落图）。
    `anchor_id`（v6.1.0）：把图片挂到某个锚点下（0 = 角色级），前端可按锚点分组展示。
    `created_by` / `origin`（v6.3.0）：谁从哪个渠道存进来的，详情页要展示。
    """
    store = getattr(self, "character", None)
    if store is None:
        return None
    cfg = _cfg(self)
    _url = (url or "").strip()
    if _url and not cfg.get("allow_web_fetch", False):
        raise ValueError(
            "联网获取参考图未开启（配置「角色卡片 → 允许联网补全角色资料」= false）"
        )
    _ext = Path(filename).suffix if filename else ".png"
    ref = store.store_ref_bytes(
        char_id, data, ext=_ext or ".png", url=_url, note=note, anchor_id=anchor_id,
        created_by=created_by, origin=origin,
    )
    if ref is None:
        return None
    if not ref.get("dedup") and (ref.get("path") or ""):
        score = await _detect_ref_nsfw(self, ref["path"])
        if score >= 0:
            try:
                store.update_ref_nsfw(int(ref["id"]), score)
                ref["nsfw_score"] = score
            except Exception as e:
                logger.warning(f"【角色卡】 NSFW 分数回写失败: {e}")
    return ref


def _first_text(*vals) -> str:
    """取第一个非空文本（None / 空串 / 纯空白都跳过）。"""
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _sender_attr(sender, *names) -> str:
    """按候选字段名依次取 sender 上的属性（不同 OneBot11 实现字段名不统一）。"""
    if sender is None:
        return ""
    vals = []
    for n in names:
        try:
            vals.append(getattr(sender, n, None))
        except Exception:
            vals.append(None)
    return _first_text(*vals)


def actor_label(event) -> str:
    """把消息事件压成「谁」：`昵称(QQ号)`；只拿到一项就退化成 `QQ:号` 或 `昵称`。

    ★ 不能只信 AstrBot 的 ``get_sender_id()``：4.28.1 里它要求 ``sender.user_id`` 是 **str**
    才返回（``astrbot/core/platform/astr_message_event.py`` 的 isinstance 判定），而
    OneBot11 / NapCat 实际给的是 **int** → 直接返回空串，表现就是「创建者只记到昵称、QQ 号丢了」。
    所以这里自己再兜一层原始字段；昵称同理（群名片在 ``card``、昵称在 ``nickname``）。
    """
    if event is None:
        return ""
    try:
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
    except Exception:
        sender = None

    name = ""
    try:
        name = str(getattr(event, "get_sender_name", lambda: "")() or "").strip()
    except Exception:
        name = ""
    if not name:
        name = _sender_attr(sender, "card", "cardname", "card_name", "nickname", "nick", "user_name")

    uid = ""
    try:
        uid = str(getattr(event, "get_sender_id", lambda: "")() or "").strip()
    except Exception:
        uid = ""
    if not uid:
        uid = _sender_attr(sender, "user_id", "user_Id", "qq", "uid")
    if not uid:
        # 最后兜底：私聊的 umo 形如 `aiocqhttp:Private_Message:1479221500`，尾段就是对方号；
        # 群聊尾段是**群号**，拿它当用户 id 会把整群记成创建者，所以只在私聊用。
        # 注意 is_private_chat 是方法不是 property（直接读属性恒为真值）。
        try:
            priv = getattr(event, "is_private_chat", None)
            if callable(priv) and bool(priv()):
                _umo = str(getattr(event, "unified_msg_origin", "") or "")
                _seg = _umo.rsplit(":", 1)[-1].strip()
                if _seg.isdigit() and _umo.count(":") >= 2:
                    uid = _seg
        except Exception:
            pass

    if name and uid:
        return f"{name}({uid})"
    if uid:
        return f"QQ:{uid}"
    return name


async def auto_link_generated(
    self,
    hits: list[tuple[dict, dict]],
    img_path: str,
    actor: str = "",
    nsfw_score: float = -1,
    note: str = "出图自动关联",
) -> list[dict]:
    """出图成功后，把成品图自动挂到本次命中的每个锚点下（v6.3.0）。

    用户在卡片里配好锚点、又用这个锚点出了图——这张成片本来就是该锚点最真实的样例，
    以前只能靠「/角色 参考图 记住」或 WebUI 手工上传，现在出图即入库。

    - **引用而非复制**：图片留在 gallery/ 里（`store_ref_link`，`external=1`），
      不再抄一份进角色目录；删角色卡里的这条记录不会动图库文件。
    - **封面只在角色原本一张图都没有时**才自动设，绝不覆盖人工挑选的封面。
    - **只清理 `origin='auto'` 的历史图**（每锚点保留 `auto_link_keep` 张，0=不限），
      手工上传/指令录入的图属于用户资产，永不自动删。

    失败只记日志，绝不影响出图（调用方也已包一层 try）。
    """
    import asyncio as _aio

    store = getattr(self, "character", None)
    if store is None or not hits:
        return []
    cfg = _cfg(self)
    if not cfg.get("auto_link_ref", True):
        return []
    p = Path(str(img_path or ""))
    if not p.exists() or not p.is_file():
        return []
    try:
        _keep = int(cfg.get("auto_link_keep", 6) or 0)
    except (TypeError, ValueError):
        _keep = 6
    _auto_cover = bool(cfg.get("auto_link_cover", True))
    out: list[dict] = []
    # sha 在别处算好再传进去：CharacterStore 的连接属于本线程（to_thread 里用 sqlite
    # 会直接抛「created in a thread can only be used in that same thread」），
    # 唯独文件 IO 适合丢线程池。
    _sha = await _aio.to_thread(store.file_sha256, str(p))
    for card, anchor in hits:
        try:
            cid = int(card["id"])
            aid = int((anchor or {}).get("id") or 0)
            # 「原本有没有图」必须在插入前问，插完再判永远是 True
            _had_refs = bool(store.list_refs(cid))
            ref = store.store_ref_link(
                cid, str(p), sha256=_sha, anchor_id=aid, note=note,
                nsfw_score=float(nsfw_score) if nsfw_score is not None else -1,
                created_by=actor or "", origin="auto",
            )
            if ref is None or ref.get("dedup"):
                continue
            out.append(ref)
            if _auto_cover and not _had_refs and not int(card.get("cover_ref_id") or 0):
                store.set_cover_ref(cid, int(ref["id"]))
            if _keep > 0:
                store.trim_auto_refs(cid, aid, _keep)
        except Exception as e:
            logger.warning(f"【角色卡】 出图自动关联失败（不影响出图）: {e}")
    if out:
        logger.info(
            f"【角色卡】 本次成品图自动关联 {len(out)} 张："
            + "、".join(f"{c.get('name')}/{a.get('name')}" for c, a in hits)
        )
    return out


async def suggest_anchor(self, name: str, work: str = "") -> dict:
    """按角色名联网/标签服务补全候选锚点标签（**需人工确认后才落库**）。

    走插件已有的 danbooru 标签服务链路（`_build_danbooru()`，本机/自建服务，
    不是抓 danbooru 官网——官网 403/限流且 docstring 已明令禁止）。
    返回 `{"ok": bool, "query": str, "tags": str, "msg": str}`。
    """
    _n = (name or "").strip()
    if not _n:
        return {"ok": False, "msg": "缺少角色名"}
    client = None
    try:
        client = self._build_danbooru()
    except Exception as e:
        logger.warning(f"【角色卡】 构建 danbooru 客户端失败: {e}")
    if client is None:
        return {
            "ok": False,
            "msg": "未启用 danbooru 标签服务（配置 → Anima 翻译 → danbooru 服务地址/开关），无法自动补全；"
                   "可以先手动 /角色 记住，或让用户在 WebUI 里填标签串",
        }
    _q = f"{_n} {work}".strip()
    try:
        tags = await client.search(_q)
    except Exception as e:
        return {"ok": False, "query": _q, "msg": f"标签服务查询失败: {e}"}
    if not (tags or "").strip():
        return {"ok": False, "query": _q, "msg": f"标签服务没查到结果（查询词：{_q}）"}
    return {"ok": True, "query": _q, "tags": tags.strip(), "source": "danbooru"}
