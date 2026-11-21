# -*- coding: utf-8 -*-
"""角色卡链路（v8.0.0）：读老婆插件 waifu.v1 契约，文本命中角色 → 主参考图。

只依赖 astrbot 的 context（跨插件契约），不直接读对方 DB。
契约：context.get_registered_star("astrbot_plugin_moe_waifu").star_cls.api
      → api.main_ref_map()（async，返回 {"status","data","missing"}）
取不到 / 版本不符 / status != ok → 一律安全降级（返回空映射），不影响原有出图。
"""
import logging
import re
import time

logger = logging.getLogger(__name__)

WAIFU_PLUGIN = "astrbot_plugin_moe_waifu"
CONTRACT = "waifu.v1"
CACHE_TTL = 60.0  # 角色库不常变，缓存 1 分钟；WebUI 手动刷新可清

# 模块级缓存：(映射, 缺失列表, 取到的时间戳)
_cache = None


def clear_cache():
    global _cache
    _cache = None


async def load_ref_map(context) -> tuple[dict, list]:
    """从老婆插件 API 读「角色 → 主参考图」映射。失败返回 ({}, [])。"""
    global _cache
    now = time.time()
    if _cache is not None and (now - _cache[2]) < CACHE_TTL:
        return _cache[0], _cache[1]
    try:
        star = context.get_registered_star(WAIFU_PLUGIN)
        api = getattr(getattr(star, "star_cls", None), "api", None) if star else None
        if api is None:
            raise RuntimeError("老婆插件未安装 / 未提供 api 契约")
        fingerprint = getattr(api, "fingerprint", lambda: "")()
        if not str(fingerprint).startswith(CONTRACT):
            raise RuntimeError(f"契约版本不符（{fingerprint!r}），需要 {CONTRACT}")
        res = await api.main_ref_map()
    except Exception as e:
        logger.info(f"【角色卡】 读取角色库失败（安全降级）: {e}")
        return {}, []
    if not isinstance(res, dict) or res.get("status") != "ok" or not isinstance(res.get("data"), dict):
        logger.info(f"【角色卡】 契约返回异常（安全降级）: {str(res)[:160]}")
        return {}, []
    data = res["data"]
    missing = list(res.get("missing") or [])
    _cache = (data, missing, now)
    return data, missing


def match_role(text: str, ref_map: dict):
    """用户文本命中哪个角色卡（名字 / 别名，长名优先避免子串误命中）。

    返回 (char_id, entry)；没命中返回 (None, None)。
    """
    t = (text or "").strip().lower()
    if not t or not ref_map:
        return None, None
    cands = []
    for cid, entry in ref_map.items():
        if not isinstance(entry, dict):
            continue
        names = [str(entry.get("name") or "")] + list(entry.get("aliases") or [])
        for nm in names:
            nm = nm.strip().lower()
            if nm and nm in t:
                cands.append((len(nm), cid, entry))
                break
    if not cands:
        return None, None
    cands.sort(key=lambda x: -x[0])  # 长名优先
    return cands[0][1], cands[0][2]


def search_roles(keyword: str, ref_map: dict, limit: int = 30,
                 any_word: bool = False) -> list[dict]:
    """按关键词查角色库（供 LLM 工具 comfyui_role 用；只读、不暴露本地路径）。

    keyword 为空 → 返回前 limit 个（按名字排）；非空 → 名字/别名/作品/tags_en 匹配。
    多个词空格分隔 = AND（每个词都要命中，顺序不限）——LLM 常把「作品名 角色名」
    一起传进来（如「异环 曳尔沐」），整串子串匹配会漏，必须逐词匹配。
    返回精简字段：char_id/name/aliases/series/tags_en/has_ref（不回 ref 绝对路径）。
    """
    words = [w for w in (keyword or "").strip().lower().split() if w]
    rows = []
    for cid, e in (ref_map or {}).items():
        if not isinstance(e, dict):
            continue
        blob = " ".join([
            str(e.get("name") or ""), str(e.get("series") or ""),
            str(e.get("tags_en") or ""),
            " ".join(str(x) for x in (e.get("aliases") or [])),
        ]).lower()
        if words:
            hit = any(w in blob for w in words) if any_word else all(w in blob for w in words)
            if not hit:
                continue
        rows.append({
            "char_id": cid,
            "name": str(e.get("name") or cid),
            "aliases": list(e.get("aliases") or []),
            "series": str(e.get("series") or ""),
            "tags_en": str(e.get("tags_en") or ""),
            "has_ref": bool(str(e.get("ref") or "").strip()),
        })
    rows.sort(key=lambda r: r["name"])
    try:
        limit = max(1, min(int(limit or 30), 100))
    except (TypeError, ValueError):
        limit = 30
    return rows[:limit]


def build_edit_instruction(user_text: str, style: str = "", *,
                           framed: bool = True) -> str:
    """程序化拼一条 Qwen-Image-2.1 图生图的**中文编辑指令**（照搬老婆插件口径）。

    确定性、不依赖 LLM——保证任何情况下角色卡出图的提示词都是编辑指令而不是英文 tag 罗列。
    结构：动作+目标+约束 → 画风固定句式 → 句尾一句总括保身份。
    """
    txt = (user_text or "").strip()
    if not txt and not style:
        return ""
    parts = []
    # 用户文字本身已是中文编辑意图（如「深夜阳台喝杯热的」「书桌前写字」）→ 直接转成动词句
    if txt:
        # 去掉可能的角色名残留无妨；把常见开头润成操作动词
        _t = txt
        if _t and not re.match(r"^(把|将|让|换|改|裁|脱|戴|置于|确保|保持)", _t):
            _t = f"按以下内容调整画面：{_t}"
        parts.append(_t)
    # 画风固定句式（老婆插件同款：把整体画风改为X（人物面部与发型保持可辨认））
    if style:
        parts.append(f"将整体画风改为{style}（人物面部与发型保持可辨认）")
    # 默认近景（与 v8.8.0 一致）
    if framed and not re.search(r"全身|远景|大场景|全景|合影|群像|wide|full.?body", txt, re.I):
        parts.append("裁切为半身近景，人物占画面主要面积，不要远景小人")
    # 三视图参考图收敛
    parts.append("若参考图为多视角拼接，只保留其中一个正面视角，去掉其余拼接")
    # 句尾总括：只锁身份，锁的是内容不是改动强度
    tail = "保持人物的面部特征、发型发色与角色辨识度不变，只做上述改动。"
    return "。".join(p.rstrip("。") for p in parts if p) + "。" + tail


def build_role_prompt(entry: dict, positive: str) -> str:
    """把角色 tags_en 作为提示词补充（不覆盖用户原词）。"""
    tags = str((entry or {}).get("tags_en") or "").strip()
    if not tags:
        return positive
    base = (positive or "").strip()
    if not base:
        return tags
    # 已含同名 tag 就不重复追加（大小写不敏感的简单串判定）
    if tags.lower() in base.lower():
        return base
    return f"{base}, {tags}"
