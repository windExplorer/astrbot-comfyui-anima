# -*- coding: utf-8 -*-
"""角色卡链路（v8.0.0）：读老婆插件 waifu.v1 契约，文本命中角色 → 主参考图。

只依赖 astrbot 的 context（跨插件契约），不直接读对方 DB。
契约：context.get_registered_star("astrbot_plugin_moe_waifu").star_cls.api
      → api.main_ref_map()（async，返回 {"status","data","missing"}）
取不到 / 版本不符 / status != ok → 一律安全降级（返回空映射），不影响原有出图。
"""
import logging
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
