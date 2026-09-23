"""Anima 控制台 WebUI 后端 API。

通过 AstrBot 的 context.register_web_api 注册路由，配合 pages/anima-console-vue/
（新版）与 pages/anima-console-vue-legacy/（旧版）下的前端页面使用。

严格遵循 AstrBot 官方文档（docs/zh/dev/star/guides/plugin-pages.md）：
1) 使用 astrbot.api.web 提供的 request / json_response / error_response，
   不暴露 Starlette / Quart / FastAPI 的原始请求对象。
2) handler 不声明 request 参数，需要请求信息时直接用模块级 `request`。
3) 返回值用 json_response(value)（value 可为 dict/list/scalar），或 error_response(msg)。
   前端桥接 apiGet/apiPost 会直接 resolve 为 value 本身（无需再解包 status/data）。
4) 路由前缀含插件名 /<plugin_name>/page/...（与前端 API_PREFIX="page/" 拼接后
   形成 /api/plugins/extensions/<plugin_name>/page/<endpoint>）。

功能：
- /schema          读取插件配置 schema（_conf_schema.json），用于前端结构化渲染
- /config          GET 读取 / POST 保存插件配置
- /logs           读取内存日志环形缓冲（由 main.py 安装的 handler 填充）
- /gallery/stats  图库统计
- /gallery/search 图库检索（每条返回 data_url 缩略图）
- /gallery/image  单图（data_url 形式）
- /gallery/star   收藏/取消收藏
- /gallery/delete 删除(移入回收站)
- /gallery/trash  回收站列表
- /gallery/restore 从回收站恢复
- /gallery/purge  彻底删除(回收站内)
- /gallery/tags   添加标签
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import mimetypes
import os
import re
import time
import uuid
from collections import OrderedDict, deque
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlparse

from astrbot.api.web import error_response, file_response, json_response, request

# 模块级 logger（v6.1.1）：此前本文件多处用了 `logger.`（如平台测试入库失败分支），
# 但**从未定义** —— 一旦走到那些 except 分支就会 NameError，把原始错误盖掉。
# 测试环境（桩掉 astrbot.api.web）没有 astrbot 包，回退标准 logging。
try:
    from astrbot.api import logger as logger  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    import logging as _logging

    logger = _logging.getLogger("astrbot_plugin_comfyui_anima.webui")

# 插件名（与 metadata.yaml 的 name 一致）。路由前缀必须含它，否则 AstrBot
# 的插件页面桥接会把请求发到错误路径（全部 404 / 前端永远加载中）。
PLUGIN_NAME = "astrbot_plugin_comfyui_anima"

# 路由方法表（v6.0.0）：`register_web_api` 注册时填充，键为含插件名前缀的完整路径，
# 值为允许的 HTTP 方法。独立 WebUI 通道（standalone_webui）复用它做方法校验。
ROUTE_METHODS: "dict[str, list[str]]" = {}


def build_route_methods(routes) -> "dict[str, list[str]]":
    """把 routes 列表归并成「路径 → 允许方法集」。

    ★v6.0.1 修复：同一路径在 routes 里可能是**多条**条目（`/config` = GET 读 + POST 写、
    `/story/session` = GET 详情 + POST 更新）。早期实现直接 `dict[key] = methods`
    会让后一条**覆盖**前一条，表里只剩最后一个方法 → 另一方向一律被判 405
    （实测 `GET /api/config` 全 405）。这里按路径归并、去重。
    跳过 handler 为 None 的条目（热更残留旧类时会被 _h() 判为缺方法）。
    """
    out: "dict[str, list[str]]" = {}
    for item in routes or []:
        try:
            _p, _hd, _ms = item[0], item[1], item[2]
        except Exception:
            continue
        if _hd is None:
            continue
        key = str(_p).rstrip("/")
        lst = out.setdefault(key, [])
        for m in (_ms or []):
            _m = str(m).upper()
            if _m not in lst:
                lst.append(_m)
    return out

# 内存日志环形缓冲（main.py 的日志 handler 会写入这里）
LOG_BUFFER: "deque[str]" = deque(maxlen=2000)

# 允许的底模（归一化白名单，与前端 baseModelOptions 一致）。
# C 站返回的 baseModel 可能带大小写（如 "Anima"），统一转小写后按此白名单过滤，
# 不在白名单内的置空（视为通用），避免前端下拉/筛选匹配不上。
BASE_MODEL_WHITELIST = ("anima", "z-image-turbo", "krea2", "illustrious")


async def run_platform_test(plugin, plat: dict, prompt: str) -> dict:
    """生图平台连通性测试：用传入配置真实生图一张并归档入库（打「平台测试」标签）。

    WebUI 与独立 WebUI 共用；plat 为平台条目 dict（可用表单未保存值直接测试）。
    返回 {ok, data_url, sha, cost_sec, w, h, seed, platform, model, archived}。
    """
    import asyncio as _aio
    import base64 as _b64
    import random as _random
    import uuid as _uuid

    try:
        from . import nai_client
    except ImportError:
        import nai_client
    try:
        from .platform_store import resolve_nai_size
    except ImportError:
        from platform_store import resolve_nai_size

    ptype = (plat.get("type") or "").strip()
    if not ptype:
        raise ValueError("缺少平台类型")
    model = (plat.get("model") or "").strip()
    # 尺寸：平台默认档位解析，兜底 nai 竖图 / 其他 1024 方图
    defaults = plat.get("defaults") if isinstance(plat.get("defaults"), dict) else {}
    size_key = str(defaults.get("size") or plat.get("size") or ("portrait" if ptype == "nai" else "1024x1024"))
    w, h = resolve_nai_size(size_key) or ((832, 1216) if ptype == "nai" else (1024, 1024))
    # 负面词：平台配置 > 启用的负面词模板
    negative = str(defaults.get("negative") or plat.get("negative") or "").strip()
    if not negative:
        try:
            negative = plugin._platform_store().enabled_negative_text()
        except Exception:
            negative = ""
    # 画师串（NAI）：取第一个启用预设
    artist = ""
    if ptype == "nai":
        try:
            presets = plugin._platform_store().artist_presets(enabled_only=True)
            if presets:
                artist = (presets[0].get("content") or "").strip()
        except Exception:
            artist = ""
    seed = _random.randint(0, 2**31 - 1)

    # 生图请求超时：优先平台自身配置的 timeout（秒），否则用全局 platform_gen_timeout（默认 180s）
    _plat_to = plat.get("timeout")
    try:
        _global_to = int(plugin._cfg("platform_gen_timeout", 180) or 180)
    except Exception:
        _global_to = 180
    try:
        eff_timeout = float(_plat_to) if _plat_to not in (None, "", 0) else _global_to
    except Exception:
        eff_timeout = _global_to

    capture: dict = {}
    t0 = time.time()
    try:
        images = await nai_client.generate(
            plat, prompt=prompt, negative=negative, width=w, height=h,
            seed=seed, count=1, artist=artist, capture=capture,
            timeout=eff_timeout,
        )
    except Exception as e:
        # 失败也回传 debug（实际请求流程/响应），前端「查看请求详情」可看
        return {"ok": False, "error": str(e), "debug": capture}
    cost = time.time() - t0
    if not images:
        return {"ok": False, "error": "平台未返回图片", "debug": capture}
    data = images[0]

    # 入库：临时文件 → archive_image（platform/model/negative/extra 与出图链路同构）
    sha = ""
    w_real, h_real = w, h
    try:
        temp_dir = getattr(plugin, "temp_dir", None) or Path("temp")
        tmp_path = Path(temp_dir) / f"platform_test_{_uuid.uuid4().hex}.png"
        tmp_path.write_bytes(data)
        g = getattr(plugin, "gallery", None)
        if g is not None:
            try:
                from PIL import Image as _PIL
                with _PIL.open(tmp_path) as _im:
                    w_real, h_real = _im.width, _im.height
            except Exception:
                pass
            _final = g.archive_image(
                str(tmp_path), source="gen", prompt=prompt, prompt_raw=prompt,
                workflow="", loras=[], seed=seed, w=w_real, h=h_real,
                is_img2img=False,
                platform=ptype, model=model, negative=negative,
                platform_name=plat.get("name") or "",
                extra={"test": True},
                size_bytes=len(data), cost_sec=cost,
                trigger_msg="[平台连通性测试]",
            )
            if _final:
                try:
                    from .image_store import _sha256_of
                except ImportError:
                    from image_store import _sha256_of
                sha = _sha256_of(_final)
                try:
                    g.add_tags(sha, ["平台测试"])
                except Exception:
                    pass
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"[平台] 测试图入库失败（不影响测试结果返回）: {e}")

    return {
        "ok": True,
        "data_url": f"data:image/png;base64,{_b64.b64encode(data).decode('ascii')}",
        "sha": sha,
        "cost_sec": round(cost, 1),
        "w": w_real,
        "h": h_real,
        "seed": seed,
        "platform": ptype,
        "model": model,
        "timeout": eff_timeout,
        "archived": bool(sha),
        # 区分「图库未初始化」与「图库在但归档失败」，避免统一误报「图库未启用」
        "gallery_disabled": g is None,
        "debug": capture,
    }


def _normalize_base_model(raw) -> str:
    """归一化 C 站底模：转小写、去空格；命中白名单则返回小写名，否则返回空（通用）。"""
    bm = str(raw or "").strip().lower()
    if not bm:
        return ""
    for w in BASE_MODEL_WHITELIST:
        if bm == w:
            return w
    # 容错：包含但不完全相等时也尝试精确匹配（如 "anima v1" -> 命中 anima）
    for w in BASE_MODEL_WHITELIST:
        if bm.startswith(w):
            return w
    return ""

# 缩略图 data URL 内存缓存：key=(路径, max_w)，避免每次请求都重新读盘 + Pillow 缩放 + base64，
# 显著降低图库/出图记录缩略图的加载耗时（局域网下尤其明显）。用 OrderedDict 做简单 LRU。
_thumb_cache: "OrderedDict[str, str]" = OrderedDict()
_THUMB_CACHE_MAX = 512


def _thumb_cached(path, max_w: int) -> str:
    key = f"{path}|{max_w}"
    hit = _thumb_cache.get(key)
    if hit is not None:
        _thumb_cache.move_to_end(key)
        return hit
    val = _thumb_data_url(path, max_w)
    if val:
        _thumb_cache[key] = val
        if len(_thumb_cache) > _THUMB_CACHE_MAX:
            _thumb_cache.popitem(last=False)
    return val


class WebUIApi:
    """封装控制台后端逻辑，避免污染 main.py。"""

    def __init__(self, plugin):
        # plugin 即 ComfyUIAnima 实例
        self.plugin = plugin

    # -------------------------------------------------------------- #
    # 配置
    # -------------------------------------------------------------- #
    async def get_config(self):
        try:
            # 兼容：query 带 workflow_sampler=<工作流文件名> 时，返回该工作流文件的
            # 采样器参数（steps/cfg/denoise）。复用已长期可用的 /config 路由，
            # 避免新增路由在前端桥接候选路径上的兼容问题。
            _ws_name = (request.query.get("workflow_sampler", "") or "").strip()
            if _ws_name:
                try:
                    from astrbot.api import logger as _api_logger
                except Exception:
                    _api_logger = _log
                _api_logger.info(f"[WebUI] 读取采样器参数: workflow_name={_ws_name!r}")
                return await self._read_workflow_sampler_file(_ws_name)
            cfg = self.plugin.config
            # AstrBot 的 Config 对象本身是可映射的，直接转 dict 后序列化
            try:
                safe = dict(cfg)
            except Exception:
                safe = json.loads(json.dumps(cfg, default=lambda o: str(o)))
            return json_response(safe)
        except Exception as e:
            return error_response(f"读取配置失败: {e}")

    async def _read_workflow_sampler_file(self, wname: str):
        """读取工作流文件（相对插件 workflow/ 目录）中的采样器参数。"""
        try:
            wdir = getattr(self.plugin, "workflow_dir", None)
            prompt = None
            if wdir is not None:
                p = Path(wdir) / wname
                if not p.suffix:
                    p = p.with_suffix(".json")
                if p.is_file():
                    try:
                        prompt = json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        prompt = None
            if not isinstance(prompt, dict):
                return error_response("未找到工作流文件或 JSON 无效")
            try:
                from . import workflow_builder
            except ImportError:
                import workflow_builder
            return json_response(workflow_builder.get_sampler_defaults(prompt))
        except Exception as e:
            return error_response(f"读取采样器参数失败: {e}")

    # ------------------ 生图平台管理（多平台生图，docs/multi-platform-image-plan.md） ------------------

    def _platform_store(self):
        """懒加载平台配置存储（挂 plugin.data_dir，热更新 reload 后重建）。"""
        store = getattr(self, "_platform_store_inst", None)
        if store is None:
            try:
                from .platform_store import PlatformStore
            except ImportError:
                from platform_store import PlatformStore
            store = PlatformStore(getattr(self.plugin, "data_dir", None) or Path("data"))
            self._platform_store_inst = store
        return store

    async def platforms_get(self):
        """读取生图平台配置全量（active_platform + platforms + 预设）。"""
        try:
            return json_response(self._platform_store().summary_full())
        except Exception as e:
            return error_response(f"读取平台配置失败: {e}")

    async def platforms_save(self):
        """整包保存生图平台配置。"""
        try:
            payload = await request.json(default={})
            if not isinstance(payload, dict):
                return error_response("请求体必须是对象")
            self._platform_store().save(payload)
            return json_response({"ok": True})
        except ValueError as e:
            return error_response(str(e))
        except Exception as e:
            return error_response(f"保存平台配置失败: {e}")

    async def platforms_test(self):
        """平台连通性测试：用表单当前配置真实生图一张并入库（不必先保存）。"""
        try:
            payload = await request.json(default={}) or {}
            plat = payload.get("platform")
            prompt = str(payload.get("prompt") or "").strip() or (
                "1girl, solo, simple background, upper body, looking at viewer, masterpiece"
            )
            if not isinstance(plat, dict):
                return error_response("缺少平台配置")
            result = await run_platform_test(self.plugin, plat, prompt)
            return json_response(result)
        except ValueError as e:
            return error_response(str(e))
        except Exception as e:
            return error_response(f"平台测试失败: {e}")

    async def platforms_quota(self):
        """查询 NAI 平台剩余额度（仅 nai 类型）。供 WebUI 生图平台列表展示与手动刷新。

        入参 body.platform 为平台条目 dict（取 base_url / api_key）；未保存的编辑值可直接传。
        返回 nai_client.fetch_quota 的结果：{"ok": True, "balance", "enabled"} 或 {"ok": False, "message"}。"""
        try:
            payload = await request.json(default={}) or {}
        except Exception:
            return error_response("请求体解析失败")
        plat = payload.get("platform")
        if not isinstance(plat, dict):
            return error_response("缺少平台配置")
        if (plat.get("type") or "") != "nai":
            return error_response("仅 NAI 平台支持查询余额")
        try:
            from .nai_client import fetch_quota
        except ImportError:
            from nai_client import fetch_quota
        try:
            # 查询超时与生图测试同口径：优先平台自身 timeout（秒），否则全局
            # platform_gen_timeout（默认 180s）。此前用 nai_client 默认 120s、
            # 而前端独立模式 15s 就 abort，慢中转下余额查询必超时。
            _to = 0.0
            try:
                _raw = plat.get("timeout")
                _to = float(_raw) if _raw not in (None, "", 0) else 0.0
            except (TypeError, ValueError):
                _to = 0.0
            if _to <= 0:
                try:
                    _to = float(self.plugin._cfg("platform_gen_timeout", 180) or 180)
                except Exception:
                    _to = 180.0
            result = await fetch_quota(plat, timeout=_to)
        except Exception as e:
            return error_response(f"查询余额异常: {e}")
        return json_response(result)

    async def get_schema(self):
        try:
            schema_path = Path(__file__).resolve().parent / "_conf_schema.json"
            if not schema_path.exists():
                return error_response("找不到 _conf_schema.json")
            raw = schema_path.read_text(encoding="utf-8")
            schema = json.loads(raw)
            return json_response(schema)
        except Exception as e:
            return error_response(f"读取配置 schema 失败: {e}")

    async def save_config(self):
        try:
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            # 采样器参数读取（POST body 传参：桥接链路里 body 传递比 GET query 可靠，
            # 避免 config GET 接口的 query 在页面桥接中丢失而误返回整个配置）。
            # body: { "_read_sampler": true, "workflow_name": "xxx.json" }
            if body.get("_read_sampler"):
                _wn = (body.get("workflow_name") or "").strip()
                if not _wn:
                    return error_response("缺少 workflow_name")
                try:
                    from astrbot.api import logger as _api_logger
                except Exception:
                    _api_logger = _log
                _api_logger.info(f"[WebUI] 读取采样器参数(POST): workflow_name={_wn!r}")
                return await self._read_workflow_sampler_file(_wn)
            new_cfg = body.get("config")
            if not isinstance(new_cfg, dict):
                return error_response("config 必须是对象")
            # 安全合并：仅覆盖顶层键，保留未提交键
            cfg = self.plugin.config
            # 兜底：template_list（workflows/loras/comfyui_servers）元素补 __template_key，
            # 避免历史数据/自定义弹窗保存缺该字段导致 AstrBot 格式校验失败
            for _tl_key in ("workflows", "loras", "comfyui_servers"):
                if _tl_key in new_cfg and isinstance(new_cfg[_tl_key], list):
                    _fixed = []
                    for _it in new_cfg[_tl_key]:
                        if isinstance(_it, dict) and not (_it.get("__template_key") or _it.get("template")):
                            _it["__template_key"] = "default"
                        _fixed.append(_it)
                    new_cfg[_tl_key] = _fixed
            for k, v in new_cfg.items():
                try:
                    cfg[k] = v
                except Exception as e:
                    return error_response(f"写入配置键 {k} 失败: {e}")
            try:
                cfg.save_config()
            except Exception as e:
                return error_response(f"保存配置失败（已写入内存）: {e}")
            try:
                _op = getattr(self.plugin, "oplog", None)
                if _op is not None:
                    _op.add("config_save", "控制台保存配置",
                            detail="顶层键: " + ", ".join(list(new_cfg.keys())[:20]))
            except Exception:
                pass
            return json_response({"msg": "配置已保存"})
        except Exception as e:
            return error_response(f"保存配置失败: {e}")

    # -------------------------------------------------------------- #
    # 工作流采样器参数读取
    # -------------------------------------------------------------- #
    async def workflow_sampler(self):
        """读取工作流文件（或前端粘贴的 JSON）中的采样器参数（steps / cfg / denoise）。

        query: workflow_name=文件名（相对插件 workflow/ 目录）或 workflow_json=JSON 文本。
        返回 {"steps": int|null, "cfg": float|null, "denoise": float|null}。
        """
        try:
            wname = (request.query.get("workflow_name", "") or "").strip()
            wjson = (request.query.get("workflow_json", "") or "").strip()
            prompt = None
            if wjson:
                try:
                    prompt = json.loads(wjson)
                except Exception:
                    prompt = None
            if not isinstance(prompt, dict) and wname:
                wdir = getattr(self.plugin, "workflow_dir", None)
                if wdir is not None:
                    p = Path(wdir) / wname
                    if not p.suffix:
                        p = p.with_suffix(".json")
                    if p.is_file():
                        try:
                            prompt = json.loads(p.read_text(encoding="utf-8"))
                        except Exception:
                            prompt = None
            if not isinstance(prompt, dict):
                return error_response("未找到工作流文件或 JSON 无效")
            try:
                from . import workflow_builder
            except ImportError:
                import workflow_builder
            return json_response(workflow_builder.get_sampler_defaults(prompt))
        except Exception as e:
            return error_response(f"读取采样器参数失败: {e}")

    # -------------------------------------------------------------- #
    # 翻译调试
    # -------------------------------------------------------------- #
    async def translate_test(self):
        """翻译调试：用指定模式实际翻译一段文本，返回结果/耗时/错误。

        请求体：{"mode": "danbooru|llm|api", "text": "中文描述"}
        不修改插件全局/工作流配置，仅作连接与效果验证。
        """
        try:
            body = await request.json(default={}) or {}
            mode = (body.get("mode") or "").strip().lower()
            text = (body.get("text") or "").strip()
            if not mode:
                return error_response("缺少 mode（danbooru / llm / api）")
            if not text:
                return error_response("缺少待翻译文本 text")
            # 先确认配置：对应模式未启用时给出明确提示
            if mode == "danbooru" and not (self.plugin._danbooru_cfg() or {}).get("enabled"):
                return error_response("danbooru 模式未启用：请在插件配置的 danbooru 块开启 enabled")
            if mode == "api":
                tcfg = self.plugin._translate_cfg() or {}
                if not tcfg.get("enabled"):
                    return error_response("api 模式未启用：请在插件配置的 translate_api 块开启 enabled")
                if not (tcfg.get("url") or "").strip():
                    return error_response("api 模式未配置接口地址：请在 translate_api.url 填写")
            result = await self.plugin.translate_test(mode, text)
            return json_response(result)
        except Exception as e:
            return error_response(f"翻译测试失败: {e}")

    # -------------------------------------------------------------- #
    # 日志
    # -------------------------------------------------------------- #
    async def get_records(self):
        """WebUI 出图记录：返回结构化出图记录（用户/消息/尺寸/大小/耗时/状态/缩略图）。"""
        try:
            if self.plugin.gallery is None:
                return json_response({"records": [], "total": 0})
            try:
                only_failed = bool(int(request.query.get("failed", 0)))
            except Exception:
                only_failed = False
            try:
                page = request.query.get("page", 1, type=int)
            except Exception:
                page = 1
            try:
                size = request.query.get("size", 40, type=int)
            except Exception:
                size = 40
            if page < 1:
                page = 1
            if size < 1 or size > 200:
                size = 40
            kw = (request.query.get("keyword", "") or "").strip()
            rows = self.plugin.gallery.recent_records(
                limit=size, only_failed=only_failed, offset=(page - 1) * size, keyword=kw
            )
            total = self.plugin.gallery.count_records(only_failed=only_failed, keyword=kw)
            # 记录列表只返回元数据（含 sha），不内联缩略图 base64——避免一多就超时。
            # 缩略图由前端经 bridge 调 gallery_thumb 按需懒加载拉取单张 data URL。
            for r in rows:
                sha = (r.get("sha256") or "").strip()
                r["sha"] = sha
                r["thumb_url"] = ""
                r["data_url"] = None
            return json_response({"records": rows, "total": total, "page": page, "size": size})
        except Exception as e:
            return error_response(f"读取出图记录失败: {e}")

    async def get_oplog(self):
        """WebUI 独立操作日志：返回结构化业务事件（生图/去重/限额/图库操作等），可筛选。"""
        try:
            oplog = getattr(self.plugin, "oplog", None)
            if oplog is None:
                return json_response({"records": [], "total": 0})
            try:
                page = request.query.get("page", 1, type=int)
            except Exception:
                page = 1
            try:
                size = request.query.get("size", 40, type=int)
            except Exception:
                size = 40
            if page < 1:
                page = 1
            if size < 1 or size > 200:
                size = 40
            event = (request.query.get("event", "") or "").strip()
            kw = (request.query.get("keyword", "") or "").strip()
            user = (request.query.get("user", "") or "").strip()
            rows = oplog.query(event=event, keyword=kw, user=user, limit=size, offset=(page - 1) * size)
            total = oplog.count(event=event, keyword=kw, user=user)
            return json_response({"records": rows, "total": total, "page": page, "size": size})
        except Exception as e:
            return error_response(f"读取操作日志失败: {e}")

    async def get_logs(self):
        try:
            # 优先用内存环形缓冲；若为空（刚重载/尚未产生日志），回退读取落盘日志文件
            # data_dir/webui.log 的尾部，保证页面始终能展示历史日志。
            lines = list(LOG_BUFFER)
            try:
                n = request.query.get("n", 2000, type=int)
            except Exception:
                n = 2000
            if not lines:
                try:
                    plugin = self.plugin
                    log_path = getattr(plugin, "data_dir", None)
                    if log_path is not None:
                        log_file = Path(log_path) / "webui.log"
                        if log_file.exists():
                            raw = log_file.read_text(encoding="utf-8", errors="ignore")
                            file_lines = [ln for ln in raw.splitlines() if ln.strip()]
                            lines = file_lines[-n:]
                except Exception:
                    pass
            if n > 0:
                lines = lines[-n:]
            return json_response({"lines": lines, "total": len(lines)})
        except Exception as e:
            return error_response(f"读取日志失败: {e}")

    # -------------------------------------------------------------- #
    # 图库
    # -------------------------------------------------------------- #
    def _gallery(self):
        return getattr(self.plugin, "gallery", None)

    async def gallery_stats(self):
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            return json_response(g.stats())
        except Exception as e:
            return error_response(f"统计失败: {e}")

    # -------------------------------------------------------------- #
    # 用户生图统计
    # -------------------------------------------------------------- #
    async def stats_ranking(self):
        """用户生图数量排行。query: days=today|yesterday|3|7|all（默认 all）；merge=1 时合并其他插件记录。"""
        g = self._gallery()
        if g is None:
            return json_response({"scope": "all", "total": 0, "rows": []})
        try:
            days_raw = str(request.query.get("days", "all")).strip().lower()
            start_ts = end_ts = None
            if days_raw == "yesterday":
                # 昨天：昨天 0 点到今天 0 点（自然日）
                _lt = time.localtime(time.time())
                _today0 = time.mktime((_lt.tm_year, _lt.tm_mon, _lt.tm_mday, 0, 0, 0, 0, 0, -1))
                start_ts = _today0 - 86400
                end_ts = _today0
                days = None
            else:
                days = {"today": 0, "3": 3, "7": 7, "all": None}.get(days_raw, None)
            merge = request.query.get("merge", "0") == "1"
            merge_names = ["PrivateCompanion"] if merge else None
            return json_response(g.user_ranking(
                days=days, merge_alsoknown=merge_names, start_ts=start_ts, end_ts=end_ts,
            ))
        except Exception as e:
            return error_response(f"统计排行失败: {e}")

    async def stats_trend(self):
        """近 24 小时用户生图数量面积图数据（按小时分桶，滚动窗口）。query: hours=24（默认）。"""
        g = self._gallery()
        if g is None:
            return json_response({"scope": "24h", "buckets": []})
        try:
            try:
                hours = request.query.get("hours", 24, type=int)
            except Exception:
                hours = 24
            if hours < 1:
                hours = 1
            if hours > 24 * 7:
                hours = 24 * 7
            return json_response(g.hourly_trend(hours=hours))
        except Exception as e:
            return error_response(f"统计趋势失败: {e}")

    # -------------------------------------------------------------- #
    # 生图次数限制（配额）
    # -------------------------------------------------------------- #
    def _quota(self):
        return getattr(self.plugin, "quota", None)

    async def quota_users(self):
        """返回生图限额数据：用户列表（用量 + 单独配置）+ 全局配置。"""
        q = self._quota()
        if q is None:
            return json_response({"global": {}, "users": []})
        try:
            g = self.plugin._draw_limit_cfg()
            users = q.list_users()
            return json_response(
                {
                    "global": {
                        "enabled": bool(g.get("enabled", False)),
                        "max_total": int(g.get("max_total", -1)),
                        "max_hour": int(g.get("max_hour", -1)),
                        "max_day": int(g.get("max_day", -1)),
                        "admin_exempt": bool(g.get("admin_exempt", True)),
                    },
                    "users": users,
                }
            )
        except Exception as e:
            return error_response(f"读取限额数据失败: {e}")

    async def quota_save_config(self):
        """保存某用户的单独生图限额。-1 表示不限制；max_total/max_hour/max_day 任一缺省用 -1。"""
        q = self._quota()
        if q is None:
            return error_response("生图限额未启用或初始化失败")
        try:
            body = await request.json(default={}) or {}
            user_id = (body.get("user_id") or "").strip()
            if not user_id:
                return error_response("缺少 user_id")
            max_total = int(body.get("max_total", -1))
            max_hour = int(body.get("max_hour", -1))
            max_day = int(body.get("max_day", -1))
            q.set_user_config(user_id, max_total, max_hour, max_day)
            return json_response({"ok": True, "user_id": user_id})
        except Exception as e:
            return error_response(f"保存限额配置失败: {e}")

    async def quota_save_global(self):
        """保存全局限额（draw_limit）配置。供「限额」页直接编辑全局默认值。"""
        try:
            body = await request.json(default={}) or {}
            cur = self.plugin.config.get("draw_limit", {}) or {}
            if not isinstance(cur, dict):
                cur = {}
            if "enabled" in body:
                cur["enabled"] = bool(body.get("enabled"))
            for k in ("max_total", "max_hour", "max_day"):
                if k in body:
                    cur[k] = int(body.get(k, -1))
            if "admin_exempt" in body:
                cur["admin_exempt"] = bool(body.get("admin_exempt"))
            self.plugin.config["draw_limit"] = cur
            try:
                self.plugin.config.save_config()
            except Exception as e:
                return error_response(f"保存配置失败（已写入内存）: {e}")
            return json_response({"ok": True, "draw_limit": cur})
        except Exception as e:
            return error_response(f"保存全局限额失败: {e}")

    async def quota_reset(self):
        """重置生图次数。body: {"user_id": "xxx"} 重置单个；省略 user_id 或 {"all": true} 重置全部。"""
        q = self._quota()
        if q is None:
            return error_response("生图限额未启用或初始化失败")
        try:
            body = await request.json(default={}) or {}
            user_id = (body.get("user_id") or "").strip()
            if not user_id:
                n = q.reset_all()
                try:
                    _op = getattr(self.plugin, "oplog", None)
                    if _op is not None:
                        _op.add("quota_reset", f"重置全部用户限额（{n} 人）", detail="清零 total/hour/day")
                except Exception:
                    pass
                return json_response({"ok": True, "reset_all": True, "count": n})
            ok = q.reset_user(user_id)
            try:
                _op = getattr(self.plugin, "oplog", None)
                if _op is not None:
                    _op.add("quota_reset", f"重置用户限额：{user_id}", user_id=user_id, detail="清零 total/hour/day")
            except Exception:
                pass
            return json_response({"ok": ok, "reset_user": user_id})
        except Exception as e:
            return error_response(f"重置生图次数失败: {e}")

    # -------------------------------------------------------------- #
    # LLM token 用量统计
    # -------------------------------------------------------------- #
    def _token_store(self):
        return getattr(self.plugin, "token_store", None)

    # -------------------------------------------------------------- #
    # 分享链接管理
    # -------------------------------------------------------------- #
    async def share_tokens(self):
        """管理端：分享链接列表（含绑定 IP），按创建时间倒序。
        query: limit=200 条数上限。"""
        try:
            g = getattr(self.plugin, "gallery", None)
            if g is None:
                return error_response("图库未启用")
            limit = int((request.query.get("limit") or 200))
            records = g.share_token_records(limit=limit)
            return json_response({"tokens": records})
        except Exception as e:
            return error_response(f"读取分享链接失败: {e}")

    async def share_token_invalidate(self):
        """管理端：作废指定分享令牌（按完整 token 或前 10 位前缀匹配）。"""
        try:
            g = getattr(self.plugin, "gallery", None)
            if g is None:
                return error_response("图库未启用")
            body = await _read_json_body()
            tok = (body.get("token") or "").strip()
            if not tok:
                return error_response("缺少 token")
            g.invalidate_share_token(tok)
            return json_response({"ok": True})
        except Exception as e:
            return error_response(f"作废分享链接失败: {e}")

    async def token_summary(self):
        """返回 LLM token 统计：汇总 + 场景分类 + 用户排行 + 明细。
        query: days=30&scope=today|1|...&user_id=可选过滤&merge=1 合并插件记录。
        """
        ts = self._token_store()
        if ts is None:
            return error_response("LLM token 统计未启用或初始化失败")
        try:
            days = int((request.query.get("days") or 30))
            if days <= 0:
                # 全部历史：daily 用 0 表示不设下限；其余聚合用足够大的窗口
                days = -1
            else:
                days = max(1, min(days, 3650))
            scope = (request.query.get("scope") or "").strip().lower()
            user_id = (request.query.get("user_id") or "").strip()
            merge = request.query.get("merge", "0") == "1"
            # 明细分页：page 从 1 起；page_size 默认 30，上限 200
            page = max(1, int(request.query.get("page") or 1))
            page_size = max(1, min(int(request.query.get("page_size") or 30), 200))
            merge_names = ["PrivateCompanion"] if merge else None
            # 自然日边界：today=今天0点到明天0点；yesterday=昨天0点到今天0点。
            # 提供时所有聚合用精确区间，避免「今天」凌晨混入昨天数据。
            _now = time.time()
            _lt = time.localtime(_now)
            _today0 = time.mktime((_lt.tm_year, _lt.tm_mon, _lt.tm_mday, 0, 0, 0, 0, 0, -1))
            _start_bucket = None
            _end_bucket = None

            def _hb(t: float) -> str:
                lt2 = time.localtime(t)
                return f"{lt2.tm_year:04d}-{lt2.tm_mon:02d}-{lt2.tm_mday:02d} {lt2.tm_hour:02d}:00"

            if scope == "today":
                _start_bucket = _hb(_today0)
                _end_bucket = _hb(_today0 + 86400)
            elif scope == "yesterday":
                _start_bucket = _hb(_today0 - 86400)
                _end_bucket = _hb(_today0)
            # 聚合查询：today/yesterday 用自然日区间，其余用 days 滚动窗口
            summary = ts.query_summary(
                user_id=user_id, days=max(days, 1),
                start_bucket=_start_bucket, end_bucket=_end_bucket,
            )
            scenes = ts.list_scenes(days=max(days, 1), start_bucket=_start_bucket, end_bucket=_end_bucket)
            users = ts.list_users(days=max(days, 1), merge_alsoknown=merge_names,
                                  start_bucket=_start_bucket, end_bucket=_end_bucket)
            models = ts.list_models(days=max(days, 1), start_bucket=_start_bucket, end_bucket=_end_bucket)
            if scope == "today":
                daily = ts.list_daily(days=1)
            elif scope == "yesterday":
                daily = ts.list_daily(days=2, start_bucket=_start_bucket, end_bucket=_end_bucket)
            else:
                daily = ts.list_daily(days=days)
            # 小时趋势：今天/昨天用自然日区间，近1天用滚动24小时，其余不提供
            if scope == "today":
                hourly = ts.list_hourly(since_day_start=True)
            elif scope == "yesterday":
                hourly = ts.list_hourly(start_ts=_today0 - 86400, end_ts=_today0)
            elif scope == "1":
                hourly = ts.list_hourly(hours=24)
            else:
                hourly = []
            detail_total = ts.count_detail(
                user_id=user_id, days=max(days, 1),
                start_bucket=_start_bucket, end_bucket=_end_bucket,
            )
            detail = ts.list_detail(
                user_id=user_id,
                days=max(days, 1),
                offset=(page - 1) * page_size,
                limit=page_size,
                start_bucket=_start_bucket, end_bucket=_end_bucket,
            )
            return json_response(
                {
                    "summary": summary,
                    "scenes": scenes,
                    "users": users,
                    "models": models,
                    "daily": daily,
                    "hourly": hourly,
                    "detail": detail,
                    "detail_total": detail_total,
                    "page": page,
                    "page_size": page_size,
                    "days": days,
                    "merge": merge,
                }
            )
        except Exception as e:
            return error_response(f"读取 token 统计失败: {e}")

    async def token_reset(self):
        """重置 LLM token 统计。body: {"user_id": "xxx"} 重置单个；省略或 {"all": true} 重置全部。"""
        ts = self._token_store()
        if ts is None:
            return error_response("LLM token 统计未启用或初始化失败")
        try:
            body = await request.json(default={}) or {}
            user_id = (body.get("user_id") or "").strip()
            if not user_id:
                n = ts.reset_all()
                return json_response({"ok": True, "reset_all": True, "count": n})
            ok = ts.reset_user(user_id)
            return json_response({"ok": ok, "reset_user": user_id})
        except Exception as e:
            return error_response(f"重置 token 统计失败: {e}")

    # -------------------------------------------------------------- #
    # LoRA 封面 / C 站抓取
    # -------------------------------------------------------------- #
    @staticmethod
    def _thumb_data_url_sync(path: Path, max_size: int = 640) -> tuple[str, str] | None:
        """把本地图片压成 data URL（优先 PIL 缩略图），返回 (data_url, mime)；失败返回 None。

        抽自 lora_image（角色卡参考图同样需要），避免两处各写一份缩略逻辑。
        """
        try:
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            data = None
            try:
                from PIL import Image as _PImage
                import io as _io

                with _PImage.open(path) as _im:
                    _im.thumbnail((max_size, max_size))
                    _buf = _io.BytesIO()
                    _fmt = _im.format or "JPEG"
                    if _fmt.upper() == "PNG":
                        _im.save(_buf, "PNG")
                        mime = "image/png"
                    else:
                        if _im.mode in ("RGBA", "P", "LA"):
                            _im = _im.convert("RGB")
                        _im.save(_buf, "JPEG", quality=82)
                        mime = "image/jpeg"
                    data = _buf.getvalue()
            except Exception:
                data = None
            if data is None:
                data = Path(path).read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{b64}", mime
        except Exception:
            return None

    @staticmethod
    def _orig_data_url_sync(path: Path, max_bytes: int = 24 * 1024 * 1024) -> tuple[str, str] | None:
        """读**原图**整字节并编码 data URL（不缩放），供「查看大图」使用。

        超过 `max_bytes`（默认 24MB）返回 None——调用方回退缩略图，避免一次性
        把超大文件 base64 进内存把页面卡死。
        """
        try:
            if Path(path).stat().st_size > max_bytes:
                return None
            raw = Path(path).read_bytes()
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}", mime
        except Exception:
            return None

    def _lora_assets_dir(self) -> Path:
        d = getattr(self.plugin, "lora_assets_dir", None)
        if d is None:
            d = (getattr(self.plugin, "data_dir", None) or Path(os.getcwd())) / "lora_assets"
        d = Path(d)
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def lora_image(self):
        """返回 LoRA 封面图（lora_assets/ 下的文件）。query: name=文件名。"""
        fname = (request.query.get("name", "") or "").strip()
        if not fname:
            return error_response("缺少 name 参数")
        # 仅允许文件名，防目录穿越
        if "/" in fname or "\\" in fname or ".." in fname:
            return error_response("非法文件名", status_code=400)
        path = self._lora_assets_dir() / fname
        if not path.exists() or not path.is_file():
            return error_response("图片不存在", status_code=404)
        try:
            # v6.1.3：size=orig 返回**原图**（大图查看器用）；缺省仍 640px 缩略图
            # （卡片列表用缩略图，避免原图 base64 过大导致前端 <img> 无法显示/卡顿）
            _size = (request.query.get("size", "") or "").strip().lower()
            if _size in ("orig", "original", "full", "0"):
                _res = await asyncio.to_thread(self._orig_data_url_sync, path)
                if not _res:
                    return error_response("原图过大或读取失败", status_code=413)
                return json_response({"name": fname, "url": _res[0]})
            _res = await asyncio.to_thread(self._thumb_data_url_sync, path, 640)
            if not _res:
                return error_response("生成缩略图失败")
            return json_response({"name": fname, "url": _res[0]})
        except Exception as e:
            return error_response(f"读取图片失败: {e}")

    async def lora_upload_image(self):
        """上传 LoRA 封面图片（multipart 或 base64），保存到 lora_assets/。"""
        try:
            raw = await request.body()
            # 兼容两种格式：{filename, data(base64)} JSON 或 原始二进制
            data_bytes = None
            filename = f"lora_{uuid.uuid4().hex}.png"
            ctype = (request.headers.get("content-type") or "").lower()
            if "json" in ctype:
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    payload = {}
                fname_in = (payload.get("filename") or "").strip()
                if fname_in:
                    filename = os.path.basename(fname_in)
                b64 = payload.get("data") or payload.get("base64") or ""
                # 兼容 dataURL：前端 readAsDataURL 返回的串带 "data:image/jpeg;base64," 前缀，
                # 必须先去掉前缀再解码，否则前缀里的字母会被当 base64 解出损坏数据（封面裂图/解码失败）。
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                try:
                    data_bytes = base64.b64decode(b64)
                except Exception:
                    return error_response("base64 数据无效")
            else:
                data_bytes = raw
                fname_in = (request.headers.get("x-filename") or "").strip()
                if fname_in:
                    filename = os.path.basename(fname_in)
                if not (filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))):
                    filename = filename.rsplit(".", 1)[0] + ".png"
            if not data_bytes or len(data_bytes) < 16:
                return error_response("图片数据为空或过小")
            # 覆盖安全：用时间戳 + 短随机后缀，避免覆盖同名旧图
            stem = os.path.splitext(filename)[0]
            ext = os.path.splitext(filename)[1] or ".png"
            safe_name = re.sub(r"[^\w\-.]", "_", stem)[:60]
            final_name = f"{safe_name}_{uuid.uuid4().hex[:8]}{ext}"
            out_path = self._lora_assets_dir() / final_name
            await asyncio.to_thread(out_path.write_bytes, data_bytes)
            return json_response({"name": final_name, "msg": "上传成功"})
        except Exception as e:
            return error_response(f"上传失败: {e}")

    # ------------------ 底模库（v7.0.0：底模实体化管理） ------------------

    def _basemodel_store(self):
        """懒加载底模库存储（挂 plugin.data_dir，热更新 reload 后重建）。"""
        store = getattr(self, "_basemodel_store_inst", None)
        if store is None:
            try:
                from .basemodel_store import BaseModelStore
            except ImportError:
                from basemodel_store import BaseModelStore
            store = BaseModelStore(getattr(self.plugin, "data_dir", None) or Path("data"))
            self._basemodel_store_inst = store
        return store

    def _basemodel_assets_dir(self) -> Path:
        d = (getattr(self.plugin, "data_dir", None) or Path(os.getcwd())) / "basemodel_assets"
        d = Path(d)
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def basemodels_list(self):
        try:
            return json_response({"items": self._basemodel_store().list_all()})
        except Exception as e:
            return error_response(f"读取底模库失败: {e}")

    async def basemodels_save(self):
        try:
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                return error_response("请求体必须是对象")
            mid, err = self._basemodel_store().save(body)
            if err:
                return error_response(f"保存失败: {err}")
            return json_response({"id": mid, "msg": "保存成功"})
        except Exception as e:
            return error_response(f"保存失败: {e}")

    async def basemodels_delete(self):
        try:
            body = await request.json(default={}) or {}
            mid = body.get("id")
            if not mid:
                return error_response("缺少 id")
            err = self._basemodel_store().delete(int(mid))
            if err:
                return error_response(f"删除失败: {err}")
            return json_response({"msg": "删除成功"})
        except Exception as e:
            return error_response(f"删除失败: {e}")

    async def basemodel_image(self):
        """返回底模封面图（basemodel_assets/ 下）。query: name=文件名，size=orig 可选。"""
        fname = (request.query.get("name", "") or "").strip()
        if not fname:
            return error_response("缺少 name 参数")
        if "/" in fname or "\\" in fname or ".." in fname:
            return error_response("非法文件名", status_code=400)
        path = self._basemodel_assets_dir() / fname
        if not path.exists() or not path.is_file():
            return error_response("图片不存在", status_code=404)
        try:
            _size = (request.query.get("size", "") or "").strip().lower()
            if _size in ("orig", "original", "full", "0"):
                _res = await asyncio.to_thread(self._orig_data_url_sync, path)
                if not _res:
                    return error_response("原图过大或读取失败", status_code=413)
                return json_response({"name": fname, "url": _res[0]})
            _res = await asyncio.to_thread(self._thumb_data_url_sync, path, 640)
            if not _res:
                return error_response("生成缩略图失败")
            return json_response({"name": fname, "url": _res[0]})
        except Exception as e:
            return error_response(f"读取图片失败: {e}")

    async def basemodels_upload_image(self):
        """上传底模封面（multipart 或 base64 JSON），保存到 basemodel_assets/。"""
        try:
            raw = await request.body()
            data_bytes = None
            filename = f"bm_{uuid.uuid4().hex}.png"
            ctype = (request.headers.get("content-type") or "").lower()
            if "json" in ctype:
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    payload = {}
                fname_in = (payload.get("filename") or "").strip()
                if fname_in:
                    filename = os.path.basename(fname_in)
                b64 = payload.get("data") or payload.get("base64") or ""
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                try:
                    data_bytes = base64.b64decode(b64)
                except Exception:
                    return error_response("base64 数据无效")
            else:
                data_bytes = raw
                fname_in = (request.headers.get("x-filename") or "").strip()
                if fname_in:
                    filename = os.path.basename(fname_in)
                if not (filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))):
                    filename = filename.rsplit(".", 1)[0] + ".png"
            if not data_bytes or len(data_bytes) < 16:
                return error_response("图片数据为空或过小")
            stem = os.path.splitext(filename)[0]
            ext = os.path.splitext(filename)[1] or ".png"
            safe_name = re.sub(r"[^\w\-.]", "_", stem)[:60]
            final_name = f"{safe_name}_{uuid.uuid4().hex[:8]}{ext}"
            out_path = self._basemodel_assets_dir() / final_name
            await asyncio.to_thread(out_path.write_bytes, data_bytes)
            return json_response({"name": final_name, "msg": "上传成功"})
        except Exception as e:
            return error_response(f"上传失败: {e}")

    async def basemodels_fetch(self):
        """C 站链接抓取底模：标题 / 描述 / 封面（下载到 basemodel_assets/）。

        body: {"url": "https://civitai.com/models/12345" 或 /model-versions/xxx}
        返回 {"title", "description", "image"(本地封面文件名), "civitai_url"}
        """
        try:
            body = await request.json(default={}) or {}
            url = (body.get("url") or "").strip()
            if not url:
                return error_response("缺少 url 参数")
            import aiohttp

            mvid_path = re.search(r"/model-versions/(\d+)", url)
            m = re.search(r"/models/(\d+)", url)
            if mvid_path:
                api_url = f"https://civitai.com/api/v1/model-versions/{mvid_path.group(1)}"
            elif m:
                api_url = f"https://civitai.com/api/v1/models/{m.group(1)}"
            else:
                return error_response("无法从链接中识别 C 站模型 ID（需包含 /models/数字 或 /model-versions/数字）")
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            }
            try:
                _ck = ((self.plugin._cfg("civitai_api_key", "")) or "").strip()
            except Exception:
                _ck = ""
            if _ck:
                headers["Authorization"] = f"Bearer {_ck}"
            proxy = None
            try:
                plugin_proxy = ((self.plugin._cfg("http_proxy", "")) or "").strip()
            except Exception:
                plugin_proxy = ""
            if plugin_proxy:
                proxy = plugin_proxy
            else:
                try:
                    from astrbot.api import GLOBAL_CONFIG
                    proxy = (GLOBAL_CONFIG.get("http_proxy") or "").strip() or None
                except Exception:
                    proxy = None
            timeout = aiohttp.ClientTimeout(total=15)
            try:
                async with aiohttp.ClientSession(headers=headers, trust_env=True) as sess:
                    async with sess.get(api_url, timeout=timeout, proxy=proxy) as resp:
                        if resp.status in (401, 403):
                            return error_response(
                                "C 站 API 拒绝请求（%d）。请在插件配置里填写 civitai_api_key 后重试。" % resp.status
                            )
                        if resp.status == 429:
                            return error_response("C 站 API 限流（HTTP 429），请稍后重试。")
                        if resp.status != 200:
                            return error_response(f"C 站 API 请求失败: HTTP {resp.status}")
                        data = await resp.json()
            except asyncio.TimeoutError:
                return error_response("C 站 API 请求超时，请检查网络/代理。")
            except aiohttp.ClientError as e:
                return error_response(f"C 站连接失败: {e}")
            # 统一取版本对象
            version = None
            if isinstance(data, dict) and data.get("modelVersions"):
                versions = data.get("modelVersions") or []
                version = versions[0] if versions else None
                if not (data.get("name") or "").strip():
                    data["name"] = ""  # models 接口有 name；versions 接口没有
            elif isinstance(data, dict):
                version = data
            version = version or {}
            title = (data.get("name") if isinstance(data, dict) else "") or version.get("model", {}).get("name", "") or ""
            description = (version.get("description") or data.get("description") or "")[:2000]
            # 收集封面候选（跳过视频），下载第一张
            import html as _html
            import re as _re

            def _clean(s: str) -> str:
                s = _re.sub(r"<[^>]+>", " ", s or "")
                return _html.unescape(s).strip()

            candidates = []
            for img in (version.get("images") or []):
                u = img.get("url") or ""
                if not u or (img.get("type") or "").lower() == "video":
                    continue
                u = u.replace("/width=450/", "/width=original/")
                candidates.append(u)
            image_name = ""
            if candidates:
                dl_err = ""
                for cu in candidates[:3]:
                    try:
                        async with aiohttp.ClientSession(headers=headers, trust_env=True) as sess:
                            async with sess.get(cu, timeout=aiohttp.ClientTimeout(total=30), proxy=proxy) as resp:
                                if resp.status != 200:
                                    continue
                                ctype = (resp.headers.get("Content-Type") or "").lower()
                                if not ctype.startswith("image/"):
                                    continue
                                img_data = await resp.read()
                        if not img_data or len(img_data) > 20 * 1024 * 1024:
                            continue
                        ext = ".png"
                        for _e in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                            if _e in (cu or "").lower():
                                ext = _e
                                break
                        image_name = f"bm_{uuid.uuid4().hex[:10]}{ext}"
                        out = self._basemodel_assets_dir() / image_name
                        await asyncio.to_thread(out.write_bytes, img_data)
                        break
                    except Exception as _de:
                        dl_err = str(_de)
                        continue
                if not image_name and dl_err:
                    logger.info(f"[底模抓取] 封面下载失败（忽略，可手动上传）: {dl_err}")
            return json_response({
                "title": _clean(str(title))[:120],
                "description": _clean(description),
                "image": image_name,
                "civitai_url": url,
                "fetched": True,
            })
        except Exception as e:
            return error_response(f"抓取失败: {e}")


    # ------------------ 基础工作流库（v7.0.0：上传解析入库，替代手动拷文件） ------------------

    def _workflow_store(self):
        """懒加载基础工作流库（挂 plugin.data_dir，热更新 reload 后重建）。"""
        store = getattr(self, "_workflow_store_inst", None)
        if store is None:
            try:
                from .workflow_store import WorkflowStore
            except ImportError:
                from workflow_store import WorkflowStore
            store = WorkflowStore(getattr(self.plugin, "data_dir", None) or Path("data"))
            self._workflow_store_inst = store
        return store

    def _baseworkflow_refs(self, wf_id: int) -> list[str]:
        """统计出图工作流配置里 base_id 指向该基础工作流的实例名（删除保护用）。"""
        try:
            wfs = self.plugin._cfg("workflows", []) or []
            return [
                (w.get("name") or "(未命名)")
                for w in wfs
                if isinstance(w, dict) and str(w.get("base_id") or "") == str(wf_id)
            ]
        except Exception:
            return []

    async def baseworkflows_list(self):
        try:
            store = self._workflow_store()
            items = store.list_all()
            try:
                _bms = {b["id"]: b for b in self._basemodel_store().list_all()}
            except Exception:
                _bms = {}
            for it in items:
                it["ref_count"] = len(self._baseworkflow_refs(it["id"]))
                _bm = _bms.get(int(it.get("basemodel_id") or 0))
                it["basemodel_name"] = (_bm or {}).get("name") or ""
            return json_response({"items": items})
        except Exception as e:
            return error_response(f"读取基础工作流库失败: {e}")

    async def baseworkflows_upload(self):
        """上传基础工作流。body: {name, content(JSON 文本), filename(原始文件名，可选)}。

        解析失败 → 拒绝入库并在 message 里给出全部原因。
        """
        try:
            body = await request.json(default={}) or {}
            name = (body.get("name") or "").strip()
            content = body.get("content") or ""
            filename = (body.get("filename") or "").strip()
            if not content:
                return error_response("缺少工作流 JSON 内容")
            wf_id, roles, err = self._workflow_store().import_json(name, content, filename)
            if err:
                return error_response(err)
            # v7.0.3：按「底模匹配关键字」自动关联底模（模型族），可在编辑弹窗手动改
            _bm_matched = None
            try:
                _bm = self._basemodel_store().match_model(
                    (roles or {}).get("model_file") or "",
                    (roles or {}).get("model_class") or "",
                )
                if _bm:
                    self._workflow_store().update_meta(wf_id, {"basemodel_id": _bm["id"]})
                    _bm_matched = _bm.get("name")
            except Exception as _e:
                logger.warning(f"【基础工作流】 底模自动关联失败（忽略）: {_e}")
            return json_response({"id": wf_id, "roles": roles, "basemodel": _bm_matched, "msg": "入库成功"})
        except Exception as e:
            return error_response(f"上传失败: {e}")

    async def baseworkflows_delete(self):
        try:
            body = await request.json(default={}) or {}
            wf_id = body.get("id")
            if not wf_id:
                return error_response("缺少 id")
            refs = self._baseworkflow_refs(int(wf_id))
            err = self._workflow_store().delete(int(wf_id), referenced_names=refs)
            if err:
                return error_response(err)
            return json_response({"msg": "删除成功"})
        except Exception as e:
            return error_response(f"删除失败: {e}")

    async def baseworkflows_reparse(self):
        try:
            body = await request.json(default={}) or {}
            wf_id = body.get("id")
            if not wf_id:
                return error_response("缺少 id")
            roles, err = self._workflow_store().reparse(int(wf_id))
            if err and roles is None:
                return error_response(err)
            return json_response({"roles": roles, "msg": err or "重解析完成"})
        except Exception as e:
            return error_response(f"重解析失败: {e}")

    async def baseworkflows_json(self):
        """读取基础工作流原始 JSON 文本（前端预览/下载）。query: id。"""
        try:
            wf_id = (request.query.get("id", "") or "").strip()
            rec = self._workflow_store().get(int(wf_id), with_json=True) if wf_id else None
            if not rec:
                return error_response("记录不存在", status_code=404)
            return json_response({"name": rec.get("name"), "filename": rec.get("file_name"), "content": rec.get("wf_json")})
        except Exception as e:
            return error_response(f"读取失败: {e}")

    async def baseworkflows_fetch(self):
        """基础工作流 C 站抓取：标题/描述/封面（封面下载到 basemodel_assets/）。

        body: {"url": civitai 链接或图片直链, "direct_image": bool}
        返回 {"title", "description", "image"(本地封面文件名), "civitai_url", "fetched"}
        """
        try:
            body = await request.json(default={}) or {}
            url = (body.get("url") or "").strip()
            if not url:
                return error_response("缺少 url 参数")
            import aiohttp

            # 通用代理/请求头取法（与 LoRA 抓取一致）
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            }
            try:
                _ck = ((self.plugin._cfg("civitai_api_key", "")) or "").strip()
            except Exception:
                _ck = ""
            if _ck:
                headers["Authorization"] = f"Bearer {_ck}"
            proxy = None
            try:
                plugin_proxy = ((self.plugin._cfg("http_proxy", "")) or "").strip()
            except Exception:
                plugin_proxy = ""
            if plugin_proxy:
                proxy = plugin_proxy
            else:
                try:
                    from astrbot.api import GLOBAL_CONFIG
                    proxy = (GLOBAL_CONFIG.get("http_proxy") or "").strip() or None
                except Exception:
                    proxy = None

            # direct_image：任意图片直链当封面下载
            if body.get("direct_image"):
                from urllib.parse import urlparse
                p = urlparse(url)
                if p.scheme not in ("http", "https") or not p.netloc:
                    return error_response("仅支持 http/https 图片直链")
                async with aiohttp.ClientSession(headers=headers, trust_env=True) as sess:
                    async with sess.get(url, timeout=aiohttp.ClientTimeout(total=30), proxy=proxy) as resp:
                        if resp.status != 200:
                            return error_response(f"下载失败: HTTP {resp.status}")
                        ctype = (resp.headers.get("Content-Type") or "").lower()
                        if not ctype.startswith("image/"):
                            return error_response(f"链接返回的不是图片（{ctype}）")
                        data = await resp.read()
                if len(data) > 20 * 1024 * 1024:
                    return error_response("图片过大（超过 20MB）")
                ext = os.path.splitext(p.path)[1].lower() or ".png"
                if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    ext = ".png"
                fname = f"bw_{uuid.uuid4().hex}{ext}"
                out = self._basemodel_assets_dir() / fname
                await asyncio.to_thread(out.write_bytes, data)
                return json_response({"image": fname, "fetched": True})

            mvid_path = re.search(r"/model-versions/(\d+)", url)
            m = re.search(r"/models/(\d+)", url)
            if mvid_path:
                api_url = f"https://civitai.com/api/v1/model-versions/{mvid_path.group(1)}"
            elif m:
                api_url = f"https://civitai.com/api/v1/models/{m.group(1)}"
            else:
                return error_response("无法从链接中识别 C 站模型 ID（需包含 /models/数字 或 /model-versions/数字）")
            timeout = aiohttp.ClientTimeout(total=15)
            try:
                async with aiohttp.ClientSession(headers=headers, trust_env=True) as sess:
                    async with sess.get(api_url, timeout=timeout, proxy=proxy) as resp:
                        if resp.status in (401, 403):
                            return error_response("C 站 API 拒绝请求（%d）。请配置 civitai_api_key 后重试。" % resp.status)
                        if resp.status == 429:
                            return error_response("C 站 API 限流（HTTP 429），请稍后重试。")
                        if resp.status != 200:
                            return error_response(f"C 站 API 请求失败: HTTP {resp.status}")
                        data = await resp.json()
            except asyncio.TimeoutError:
                return error_response("C 站 API 请求超时，请检查网络/代理。")
            except aiohttp.ClientError as e:
                return error_response(f"C 站连接失败: {e}")
            version = None
            if isinstance(data, dict) and data.get("modelVersions"):
                versions = data.get("modelVersions") or []
                version = versions[0] if versions else None
            elif isinstance(data, dict):
                version = data
            version = version or {}
            title = (data.get("name") if isinstance(data, dict) else "") or (version.get("model") or {}).get("name", "") or ""
            description = (version.get("description") or data.get("description") or "")[:2000]
            import html as _html

            def _clean(s: str) -> str:
                s = re.sub(r"<[^>]+>", " ", s or "")
                return _html.unescape(s).strip()

            image_name = ""
            for img in (version.get("images") or []):
                u = img.get("url") or ""
                if not u or (img.get("type") or "").lower() == "video":
                    continue
                u = u.replace("/width=450/", "/width=original/")
                try:
                    async with aiohttp.ClientSession(headers=headers, trust_env=True) as sess:
                        async with sess.get(u, timeout=aiohttp.ClientTimeout(total=30), proxy=proxy) as resp:
                            if resp.status != 200:
                                continue
                            ctype = (resp.headers.get("Content-Type") or "").lower()
                            if not ctype.startswith("image/"):
                                continue
                            img_data = await resp.read()
                    if not img_data or len(img_data) > 20 * 1024 * 1024:
                        continue
                    ext = ".png"
                    for _e in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                        if _e in u.lower():
                            ext = _e
                            break
                    image_name = f"bw_{uuid.uuid4().hex[:10]}{ext}"
                    out = self._basemodel_assets_dir() / image_name
                    await asyncio.to_thread(out.write_bytes, img_data)
                    break
                except Exception:
                    continue
            return json_response({
                "title": _clean(str(title))[:120],
                "description": _clean(description),
                "image": image_name,
                "civitai_url": url,
                "fetched": True,
            })
        except Exception as e:
            return error_response(f"抓取失败: {e}")

    async def baseworkflows_meta(self):
        """更新基础工作流元数据（名称/C站链接/描述/封面）。body: {id, ...fields}。"""
        try:
            body = await request.json(default={}) or {}
            wf_id = body.get("id")
            if not wf_id:
                return error_response("缺少 id")
            err = self._workflow_store().update_meta(
                int(wf_id),
                {k: body.get(k) for k in ("name", "civitai_url", "image", "description")
                 if k in body},
            )
            if err:
                return error_response(err)
            return json_response({"msg": "已保存"})
        except Exception as e:
            return error_response(f"保存失败: {e}")

    async def lora_fetch_image(self, url: str = ""):
        """从任意图片直链下载封面图到 lora_assets/（支持 C站 / 魔搭 / HuggingFace 等任意图片 URL）。

        由 lora_fetch 的 direct_image 分支调用；返回 {"name": 本地文件名}
        """
        try:
            import aiohttp
            from urllib.parse import urlparse
            if not url:
                return error_response("缺少 url 参数")
            p = urlparse(url)
            if p.scheme not in ("http", "https") or not p.netloc:
                return error_response("仅支持 http/https 图片直链")
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Referer": f"{p.scheme}://{p.netloc}/",
            }
            # 代理：优先插件配置，其次 AstrBot 全局；环境变量由 trust_env 兜底
            proxy = None
            try:
                plugin_proxy = ((self.plugin._cfg("http_proxy", "")) or "").strip()
            except Exception:
                plugin_proxy = ""
            if plugin_proxy:
                proxy = plugin_proxy
            else:
                try:
                    from astrbot.api import GLOBAL_CONFIG
                    proxy = (GLOBAL_CONFIG.get("http_proxy") or "").strip() or None
                except Exception:
                    proxy = None
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(headers=headers, trust_env=True) as sess:
                async with sess.get(url, timeout=timeout, proxy=proxy) as resp:
                    if resp.status != 200:
                        return error_response(f"下载失败: HTTP {resp.status}")
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if not ctype.startswith("image/"):
                        return error_response(f"链接返回的不是图片（Content-Type: {ctype}）")
                    data = await resp.read()
            if len(data) > 20 * 1024 * 1024:
                return error_response("图片过大（超过 20MB）")
            ext = os.path.splitext(p.path)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                ext = ".png"
            fname = f"cover_{uuid.uuid4().hex}{ext}"
            out = self._lora_assets_dir() / fname
            await asyncio.to_thread(out.write_bytes, data)
            return json_response({"name": fname, "msg": "下载成功"})
        except Exception as e:
            return error_response(f"下载失败: {e}")

    async def lora_fetch(self):
        """C 站链接抓取：输入 civitai 链接，抓取封面图（下载到本地）+ 触发词 + 描述 + 底模。

        body: {"url": "https://civitai.com/models/12345" 或含 /model-versions/xxx}
        返回 {"image": 本地文件名, "trigger_words": str, "description": str, "base_model": str}
        """
        try:
            body = await request.json(default={}) or {}
            url = (body.get("url") or "").strip()
            if not url:
                return error_response("缺少 url 参数")
            # direct_image=true：把任意图片直链当封面下载（不局限于 C站）
            if body.get("direct_image"):
                return await self.lora_fetch_image(url)
            import aiohttp

            # 从链接解析模型 id / 版本 id
            api_url = None
            # 优先识别路径中的 /model-versions/数字；C 站也常见 ?modelVersionId=xxx 查询参数
            mvid_path = re.search(r"/model-versions/(\d+)", url)
            m = re.search(r"/models/(\d+)", url)
            # 查询参数 modelVersionId（父页面仅型号链接常见）
            mvid_query = ""
            try:
                from urllib.parse import parse_qs, urlparse as _urlparse

                _qs = parse_qs(_urlparse(url).query)
                if _qs.get("modelVersionId"):
                    mvid_query = _qs["modelVersionId"][0]
            except Exception:
                mvid_query = ""
            target_version_id = mvid_query or (mvid_path.group(1) if mvid_path else "")
            if mvid_path:
                api_url = f"https://civitai.com/api/v1/model-versions/{mvid_path.group(1)}"
            elif m:
                api_url = f"https://civitai.com/api/v1/models/{m.group(1)}"
            else:
                return error_response("无法从链接中识别 C 站模型 ID（需包含 /models/数字、/model-versions/数字 或 ?modelVersionId=）")
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            }
            # 可选：Civitai API Key（避免限流/拿不到图片）
            try:
                _ck = ((self.plugin._cfg("civitai_api_key", "")) or "").strip()
            except Exception:
                _ck = ""
            if _ck:
                headers["Authorization"] = f"Bearer {_ck}"
            proxy = None
            # 优先用插件自己的 http_proxy 配置；其次 AstrBot 全局 http_proxy；环境变量由 trust_env 兜底
            try:
                plugin_proxy = ((self.plugin._cfg("http_proxy", "")) or "").strip()
            except Exception:
                plugin_proxy = ""
            if plugin_proxy:
                proxy = plugin_proxy
            else:
                try:
                    from astrbot.api import GLOBAL_CONFIG

                    proxy = (GLOBAL_CONFIG.get("http_proxy") or "").strip() or None
                except Exception:
                    proxy = None
            # 环境变量代理交给 aiohttp trust_env
            timeout = aiohttp.ClientTimeout(total=10)
            try:
                async with aiohttp.ClientSession(headers=headers, trust_env=True) as sess:
                    async with sess.get(api_url, timeout=timeout, proxy=proxy) as resp:
                        if resp.status == 401 or resp.status == 403:
                            # 区分「未配置 key（真匿名）」与「已配置 key 但 C 站仍拒（key 无效/过期/被代理剥离）」，
                            # 否则用户填了 key 也会看到一模一样的「匿名请求被拒」而困惑。
                            try:
                                from astrbot.api import logger as _log
                                _log.warning(
                                    f"[LoRA抓取] C站返回 {resp.status}；"
                                    f"当前是否已配置 civitai_api_key={'是(len=%d)' % len(_ck) if _ck else '否'}"
                                )
                            except Exception:
                                pass
                            if _ck:
                                return error_response(
                                    "已检测到已配置的 civitai_api_key（长度 %d），但 C 站仍返回 %d。"
                                    "说明该 Key 无效 / 已过期，或被代理剥离了 Authorization 头。"
                                    "请到 C 站 Settings → Account → API Keys 重新生成一份并粘贴保存后重试；"
                                    "若使用 http_proxy，请确认代理未丢弃请求头。" % (len(_ck), resp.status)
                                )
                            return error_response(
                                "C 站 API 拒绝了匿名请求（%d）。请在插件配置「网络与代理」里填写 "
                                "civitai_api_key（C 站 Settings → Account → API Keys 生成），否则无法获取描述/封面。" % resp.status
                            )
                        if resp.status == 429:
                            return error_response("C 站 API 限流（HTTP 429）。请稍后重试，或配置 civitai_api_key 提升额度。")
                        if resp.status != 200:
                            return error_response(f"C 站 API 请求失败: HTTP {resp.status}")
                        data = await resp.json()
            except asyncio.TimeoutError:
                try:
                    from astrbot.api import logger as _log
                    _log.warning(f"[LoRA抓取] C站 API 超时: {api_url}")
                except Exception:
                    pass
                return error_response("C 站 API 请求超时（10s 未响应）。请检查网络/代理是否可达 civitai.com，或稍后重试；也可手动填写描述与触发词。")
            except aiohttp.ClientConnectorError as e:
                try:
                    from astrbot.api import logger as _log
                    _log.warning(f"[LoRA抓取] C站连接失败: {api_url} proxy={proxy} err={e}")
                except Exception:
                    pass
                return error_response(f"无法连接 C 站 API（连接失败：{e.host if getattr(e, 'host', None) else '未知'}）。请检查网络/代理配置。")
            except aiohttp.ClientError as e:
                return error_response(f"C 站 API 请求出错（{type(e).__name__}: {e or '未知网络错误'}）。请检查网络后重试。")
            # 统一取 modelVersions 列表（models 接口）或单版本对象（model-versions 接口）
            versions = []
            if isinstance(data, dict) and data.get("modelVersions"):
                versions = data.get("modelVersions") or []
            elif isinstance(data, dict) and ("trainedWords" in data or "images" in data):
                versions = [data]
            if not versions and isinstance(data, dict) and (data.get("error") or data.get("message") or not any(k in data for k in ("name", "id", "modelVersions", "trainedWords", "images"))):
                # 响应不是有效的模型数据（匿名限流/错误响应但 HTTP 200）
                return error_response("C 站 API 返回了异常/受限数据（可能是匿名限流）。请在插件配置「网络与代理」填写 civitai_api_key 后重试。")
            version = None
            if versions:
                if target_version_id and len(versions) > 1:
                    # 用户链接指定了具体版本：按 id 精确匹配（避免多版本时取错底模）
                    for _v in versions:
                        if str(_v.get("id") or "") == str(target_version_id):
                            version = _v
                            break
                if version is None:
                    # 无指定版本：取「最新」版本（按 publishedAt/createdAt 时间戳，若无则回退数组第一个）
                    def _ver_ts(v):
                        for f in ("publishedAt", "createdAt", "updatedAt"):
                            ts = v.get(f)
                            if ts:
                                try:
                                    import datetime
                                    if isinstance(ts, (int, float)):
                                        return float(ts)
                                    return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                                except Exception:
                                    try:
                                        return float(ts)
                                    except Exception:
                                        continue
                        return -1.0
                    version = max(versions, key=_ver_ts)
            # 触发词/底模/描述
            trigger_words = ""
            base_model = ""
            description = ""
            title = str((data.get("name") if isinstance(data, dict) else "") or "").strip()
            if version:
                tw = version.get("trainedWords") or []
                trigger_words = "\n".join(str(x) for x in tw if x)
                base_model = _normalize_base_model(version.get("baseModel"))
                description = str(data.get("description") or version.get("description") or "").strip()
            # 封面图：收集候选图（下载后由前端选图）
            image_name = ""
            cover_url = ""
            fetched_covers = []  # 已下载保存的候选封面文件名（前端选图）
            if version:
                images = version.get("images") or []
                candidates = []
                for _im in images:
                    if not isinstance(_im, dict):
                        continue
                    # C 站 images 条目有 type 字段：图片=image，视频=video；缺省视为图片
                    itype = str(_im.get("type") or "image").lower()
                    if itype and itype not in ("image", "photo"):
                        continue  # 跳过视频/其它类型
                    u = str(_im.get("url") or "").strip()
                    if not u:
                        continue
                    try:
                        w = int(_im.get("width") or 0)
                        h = int(_im.get("height") or 0)
                    except (TypeError, ValueError):
                        w = h = 0
                    # 若 URL 明确指向视频后缀也跳过
                    path_low = urlparse(u).path.lower()
                    if path_low.endswith((".mp4", ".mov", ".webm", ".avi", ".m4v")):
                        continue
                    # C 站 API 的 images[].url 默认是「压缩缩略图」URL（形如
                    # .../width=450/xxx.jpeg），直接下载会很模糊。C 站图片服务支持
                    # width=original 参数返回原始分辨率，这里生成原图 URL；下载时
                    # 优先原图，失败则回退到原始 url。
                    u_orig = re.sub(r"/width=[^/]+/", "/width=original/", u)
                    candidates.append((u_orig, u, w, h))
                if candidates:
                    # C 站主图 URL 文件名以「00001-」开头（作者主封面）；优先选它，否则取第一张
                    cover_url = candidates[0][0]
                    for _uo, _ur, _w, _h in candidates:
                        _base = os.path.basename(urlparse(_uo).path).lower()
                        if _base.startswith("00001-") or "00001." in _base:
                            cover_url = _uo
                            break
                    # 诊断：记录候选图 URL 与尺寸，便于比对页面封面
                    try:
                        from astrbot.api import logger as _log
                        _log.info(f"[LoRA抓取] 候选封面 {len(candidates)} 张: " + "; ".join(f"{_uo} ({_w}x{_h})" for _uo, _ur, _w, _h in candidates[:5]))
                    except Exception:
                        pass
                    # 逐个下载候选图（最多 6 张有效图），前端选图用
                    img_headers = dict(headers)
                    img_headers["Referer"] = "https://civitai.com/"
                    cover_timeout = aiohttp.ClientTimeout(total=15)
                    fetched_covers = []  # 已保存的封面文件名
                    for _uo, _ur, _w, _h in candidates:
                        if not _uo:
                            continue
                        if len(fetched_covers) >= 6:
                            break
                        _img_data = b""
                        try:
                            # 优先原图 URL（width=original）；失败则回退到原始 url
                            for _curl in (_uo, _ur):
                                if not _curl:
                                    continue
                                try:
                                    async with aiohttp.ClientSession(headers=img_headers, trust_env=True) as _sess:
                                        async with _sess.get(_curl, timeout=cover_timeout, proxy=proxy) as _resp:
                                            if _resp.status != 200:
                                                continue
                                            _img_data = await _resp.read()
                                    if _img_data:
                                        break
                                except Exception:
                                    _img_data = b""
                                    continue
                            if not _img_data:
                                continue
                            ext_by_magic = ""
                            if _img_data[:3] == b"\xff\xd8\xff":
                                ext_by_magic = ".jpg"
                            elif _img_data[:8] == b"\x89PNG\r\n\x1a\n":
                                ext_by_magic = ".png"
                            elif _img_data[:4] == b"RIFF" and _img_data[8:12] == b"WEBP":
                                ext_by_magic = ".webp"
                            elif _img_data[:4] == b"GIF8":
                                ext_by_magic = ".gif"
                            if not (ext_by_magic and len(_img_data) >= 64):
                                continue
                            _fn = f"civitai_{uuid.uuid4().hex[:10]}{ext_by_magic}"
                            try:
                                _dest = self._lora_assets_dir() / _fn
                                _dest.write_bytes(_img_data)
                                if not _dest.exists() or _dest.stat().st_size < 64:
                                    continue
                                fetched_covers.append(_fn)
                                try:
                                    from astrbot.api import logger as _log
                                    _log.info(f"[LoRA抓取] 封面已保存(候选): {_dest}")
                                except Exception:
                                    pass
                            except Exception as _we:
                                try:
                                    from astrbot.api import logger as _log
                                    _log.warning(f"[LoRA抓取] 封面写入失败: {_we}")
                                except Exception:
                                    pass
                                continue
                        except Exception:
                            continue
            return json_response({
                "image": "",
                "images": fetched_covers,
                "save_dir": str(self._lora_assets_dir()) if fetched_covers else "",
                "cover_url": cover_url,
                "trigger_words": trigger_words,
                "description": description[:2000],
                "base_model": base_model,
                "title": title,
                "fetched": bool(version),
            })
        except Exception as e:
            detail = str(e) or type(e).__name__ or "未知错误"
            return error_response(f"抓取失败: {detail}")

    async def gallery_search(self):
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            kw = request.query.get("keyword", "")
            user_filter = request.query.get("user", "")
            stype = request.query.get("type", "") or None
            if stype in ("", "all"):
                stype = None
            starred = request.query.get("starred", "0") == "1"
            trash = request.query.get("trash", "0") == "1"
            nsfw = str(request.query.get("nsfw", "") or "").strip() or ""
            tag = str(request.query.get("tag", "") or "").strip() or ""
            platform = str(request.query.get("platform", "") or "").strip() or ""
            try:
                page = request.query.get("page", 1, type=int)
            except Exception:
                page = 1
            try:
                size = request.query.get("size", 40, type=int)
            except Exception:
                size = 40
            if page < 1:
                page = 1
            if size < 1:
                size = 40
            if size > 200:
                size = 200
            offset = (page - 1) * size
            rows = g.search(keyword=kw, type=stype, starred_only=starred,
                            trash=trash, limit=size, offset=offset, user_filter=user_filter,
                            nsfw=nsfw, tag=tag, platform=platform)
            total = g.count_search(keyword=kw, type=stype,
                                   starred_only=starred, trash=trash, user_filter=user_filter,
                                   nsfw=nsfw, tag=tag, platform=platform)
            # 列表只返回元数据（含 sha256），不内联任何缩略图 base64——避免一次几十张
            # 图导致响应体爆炸/超时。缩略图由前端经 bridge 调用 gallery_thumb 按需、
            # 懒加载、带 LRU 缓存地拉取单张 data URL（参考 astrbot_plugin_stealer 图库）。
            for r in rows:
                # 统一保留 sha256 作为前端取缩略图/大图的 key
                sha = (r.get("sha256") or "").strip()
                r["sha"] = sha
                r.pop("thumb", None)
                r.pop("thumb_url", None)
            return json_response({"images": rows, "total": total, "page": page, "size": size})
        except Exception as e:
            return error_response(f"检索失败: {e}")

    async def gallery_thumb(self):
        """返回单张压缩缩略图 data URL（前端懒加载按需调用，走 bridge）。

        设计参照 astrbot_plugin_stealer 图库：列表接口只给元数据，缩略图由前端在
        图片进入视口时逐个调用本接口拉取 data URL，配 LRU 缓存。这样既不走 AstrBot
        裸路径（404/401），也不会一次内联几十张图导致响应体爆炸/超时。
        """
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        sha = request.query.get("sha", "")
        if not sha:
            return error_response("缺少 sha 参数")
        try:
            size = request.query.get("size", 300, type=int)
        except Exception:
            size = 300
        try:
            path = g.path_of(sha)
        except Exception as e:
            return error_response(f"路径解析失败: {e}")
        if not path or not Path(path).exists():
            return error_response("图片不存在", status_code=404)
        try:
            data_url = await asyncio.to_thread(_thumb_cached, path, size)
            if not data_url:
                return error_response("生成缩略图失败")
            return json_response({"sha": sha, "url": data_url})
        except Exception as e:
            return error_response(f"生成缩略图失败: {e}")

    async def gallery_image(self):
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        sha = request.query.get("sha", "")
        if not sha:
            return error_response("缺少 sha 参数")
        # meta=1 时返回 JSON（含元数据 + data_url 兜底），供前端大图弹窗取信息用；
        # 否则直接以图片 binary 返回（file_response），浏览器原生加载、支持断点。
        want_meta = request.query.get("meta", "0") == "1"
        try:
            path = g.path_of(sha)
        except Exception as e:
            return error_response(f"路径解析失败: {e}")
        if not path or not Path(path).exists():
            return error_response("图片不存在", status_code=404)
        if want_meta:
            meta = None
            try:
                meta = g.get_by_sha(sha)
            except Exception:
                meta = None
            # noimg=1 时只回元数据，不生成大图 base64（前端大图走直链加载）。
            # 否则默认仍返回原图 data_url 作为兜底（避免个别环境直链不可用）。
            noimg = request.query.get("noimg", "0") == "1"
            if not noimg:
                try:
                    # raw=1：返回原图 base64（不经 1600px 缩放、不经 JPEG 重编码），
                    # 供前端「下载原图」按钮取归档原图字节。
                    raw_mode = request.query.get("raw", "0") == "1"
                    if raw_mode:
                        _raw = await asyncio.to_thread(Path(path).read_bytes)
                        _raw_mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
                        data_url = f"data:{_raw_mime};base64,{base64.b64encode(_raw).decode('ascii')}"
                    else:
                        # 大图默认限制在 1600px 内转 data URL，避免图生图并排时两张原图 base64
                        # 体积过大导致加载慢/超时；需要更大可传 size=原尺寸。
                        try:
                            view_size = request.query.get("size", 1600, type=int)
                        except Exception:
                            view_size = 1600
                        data_url = await asyncio.to_thread(_thumb_cached, path, view_size)
                        if not data_url:
                            raw = await asyncio.to_thread(Path(path).read_bytes)
                            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
                            data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
                    return json_response({"data_url": data_url, "mime": None, "meta": meta})
                except Exception as e:
                    return error_response(f"读取图片失败: {e}")
            return json_response({"data_url": None, "mime": None, "meta": meta})
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        return file_response(path, filename=f"{sha}.{_ext_of(mime)}", content_type=mime)

    async def gallery_star(self):
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            payload = await request.json(default={}) or {}
            sha = payload.get("sha", "")
            # 注意：on 缺失时默认「不收藏」，避免任何不带 on 的请求被静默标星
            # （收藏需显式 on:true；普通前端 onStar 始终带 on，无回归）。
            on = 1 if payload.get("on", False) else 0
            if not sha:
                return error_response("缺少 sha")
            ok = g.star(sha, on=on)
            try:
                _op = getattr(self.plugin, "oplog", None)
                if _op is not None:
                    _op.add("gallery_star", f"图库{'收藏' if on else '取消收藏'}",
                            ref_sha=sha, extra={"on": bool(on)})
            except Exception:
                pass
            return json_response({"msg": "已更新收藏" if ok else "未找到该图"})
        except Exception as e:
            return error_response(f"操作失败: {e}")

    async def gallery_set_blur(self):
        """单图设置 NSFW 模糊覆盖。POST {sha, on(0/1)}；on 为空/null 时清除覆盖恢复跟随全局。"""
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            payload = await request.json(default={}) or {}
            sha = payload.get("sha", "")
            if not sha:
                return error_response("缺少 sha")
            on = payload.get("on")
            if on is None:
                ok = g.clear_nsfw_blur(sha)
                msg = "已恢复跟随全局模糊"
            else:
                ok = g.set_nsfw_blur(sha, 1 if on else 0)
                msg = "已设置模糊" if on else "已取消模糊"
            return json_response({"msg": msg if ok else "未找到该图"})
        except Exception as e:
            return error_response(f"操作失败: {e}")

    async def gallery_set_nsfw(self):
        """人工直接标记/取消单图 NSFW（误判纠正），绕过自动检测模型。
        POST {sha, on(0/1)}：on=1 标记为 NSFW，on=0 取消 NSFW。"""
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            payload = await request.json(default={}) or {}
            sha = payload.get("sha", "")
            on = payload.get("on")
            if not sha:
                return error_response("缺少 sha")
            if on is None:
                return error_response("缺少 on(0/1)")
            ok = g.set_nsfw(sha, 1 if on else 0)
            msg = "已标记为 NSFW" if on else "已取消 NSFW"
            return json_response({"msg": msg if ok else "未找到该图"})
        except Exception as e:
            return error_response(f"操作失败: {e}")

    async def gallery_scan_nsfw(self):
        """后台启动一键 NSFW 扫描（默认只扫未检测的图）。GET ?only=1/0。"""
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            only = request.query.get("only", "1") != "0"
            res = g.scan_nsfw_start(only_unchecked=only)
            if res.get("last_err"):
                return error_response(res["last_err"])
            return json_response(res)
        except Exception as e:
            return error_response(f"扫描失败: {e}")

    async def gallery_scan_nsfw_progress(self):
        """查询 NSFW 扫描进度。GET，返回 {running, total, done, nsfw, started_at, finished_at, last_err}。"""
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            return json_response(g.scan_nsfw_progress())
        except Exception as e:
            return error_response(f"查询进度失败: {e}")

    async def gallery_check_nsfw(self):
        """对单张图做 NSFW 检测。GET ?sha=xxx，返回检测结果并写回库。"""
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            sha = (request.query.get("sha") or "").strip()
            if not sha:
                return error_response("缺少 sha")
            res = g.check_nsfw(sha)
            if res.get("available") is False:
                return error_response(res.get("msg") or "检测不可用")
            return json_response({
                "nsfw": res.get("nsfw", False),
                "nsfw_score": res.get("nsfw_score"),
                "msg": res.get("msg", "检测完成"),
            })
        except Exception as e:
            return error_response(f"检测失败: {e}")

    async def gallery_delete(self):
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            payload = await request.json(default={}) or {}
            sha = payload.get("sha", "")
            if not sha:
                return error_response("缺少 sha")
            ok = g.delete(sha)
            if not ok:
                return error_response("未找到该图（收藏图不可删除）")
            try:
                _op = getattr(self.plugin, "oplog", None)
                if _op is not None:
                    _op.add("gallery_delete", "图库删除（移入回收站）", ref_sha=sha)
            except Exception:
                pass
            return json_response({"msg": "已移入回收站"})
        except Exception as e:
            return error_response(f"删除失败: {e}")

    async def gallery_trash(self):
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            rows = g.search(trash=True, limit=200, offset=0)
            for r in rows:
                sha = r.get("sha256", "")
                try:
                    p = g.path_of(sha)
                    if p and Path(p).exists():
                        raw = await asyncio.to_thread(Path(p).read_bytes)
                        mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
                        r["thumb"] = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
                    else:
                        r["thumb"] = ""
                except Exception:
                    r["thumb"] = ""
            return json_response(rows)
        except Exception as e:
            return error_response(f"读取回收站失败: {e}")

    async def gallery_restore(self):
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            payload = await request.json(default={}) or {}
            sha = payload.get("sha", "")
            if not sha:
                return error_response("缺少 sha")
            ok = g.restore(sha)
            try:
                _op = getattr(self.plugin, "oplog", None)
                if _op is not None:
                    _op.add("gallery_restore", "图库恢复（从回收站）", ref_sha=sha)
            except Exception:
                pass
            return json_response({"msg": "已恢复" if ok else "恢复失败"})
        except Exception as e:
            return error_response(f"恢复失败: {e}")

    async def gallery_purge(self):
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            payload = await request.json(default={}) or {}
            sha = payload.get("sha", "")
            if not sha:
                return error_response("缺少 sha")
            ok = g.purge(sha)
            try:
                _op = getattr(self.plugin, "oplog", None)
                if _op is not None:
                    _op.add("gallery_purge", "图库彻底删除", ref_sha=sha)
            except Exception:
                pass
            return json_response({"msg": "已彻底删除" if ok else "未找到该图"})
        except Exception as e:
            return error_response(f"彻底删除失败: {e}")

    async def gallery_tags(self):
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            payload = await request.json(default={}) or {}
            sha = payload.get("sha", "")
            tags = payload.get("tags", [])
            action = str(payload.get("action") or "add").strip().lower()
            if not sha or not tags:
                return error_response("缺少 sha 或 tags")
            tag_list = tags if isinstance(tags, list) else [tags]
            if action == "del":
                g.remove_tags(sha, tag_list)
                msg = "标签已删除"
                ev = "gallery_untag"
                desc = f"图库删除标签：{','.join(tag_list)}"
            else:
                g.add_tags(sha, tag_list)
                msg = "标签已添加"
                ev = "gallery_tags"
                desc = f"图库打标签：{','.join(tag_list)}"
            try:
                _op = getattr(self.plugin, "oplog", None)
                if _op is not None:
                    _op.add(ev, desc, ref_sha=sha, extra={"tags": tag_list, "action": action})
            except Exception:
                pass
            return json_response({"msg": msg})
        except Exception as e:
            return error_response(f"打标签失败: {e}")

    async def backup_db(self):
        """备份图库数据库（gallery.db），返回 base64 便于前端触发下载。

        数据库文件通常不大（SQLite 元数据），走 bridge 拉取 base64 后在前端
        构造 Blob 下载，规避 AstrBot 裸路径需登录 token 的问题。

        注意：图库开启了 WAL 模式，直接读取主文件会漏掉 -wal 中尚未合并的
        新建表/数据（如 users 表）。因此这里用 SQLite online backup API 导出
        完整快照（自动包含 WAL 内容），保证备份与活库一致。
        """
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            db_path = getattr(g, "db_path", None)
            if not db_path or not Path(db_path).exists():
                return error_response("图库数据库文件不存在")
            import sqlite3
            import tempfile
            import os as _os

            def _export_db() -> bytes:
                _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
                _tmp.close()
                try:
                    _src = sqlite3.connect(str(db_path))
                    _dst = sqlite3.connect(_tmp.name)
                    try:
                        _src.backup(_dst)
                    finally:
                        _dst.close()
                        _src.close()
                    return Path(_tmp.name).read_bytes()
                finally:
                    try:
                        _os.unlink(_tmp.name)
                    except OSError:
                        pass

            raw = await asyncio.to_thread(_export_db)
            encoded = base64.b64encode(raw).decode("ascii")
            ts = time.strftime("%Y%m%d-%H%M%S")
            filename = f"gallery_backup_{ts}.db"
            return json_response({
                "filename": filename,
                "data_url": f"application/octet-stream;base64,{encoded}",
                "size_bytes": len(raw),
            })
        except Exception as e:
            return error_response(f"备份失败: {e}")

    # ------------------------------------------------------------------ #
    # 剧情模式档案
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # 角色卡片（Character Card）：列表 / 详情 / 保存 / 删除 / 锚点 / 导入导出
    # ------------------------------------------------------------------ #
    # WebUI 侧写入的创建者标识（v6.3.0）。面板只有口令鉴权、没有「用户」概念，
    # 因此默认记渠道名；使用者可在角色卡页自报「昵称 + QQ」（v6.3.1），自报值优先。
    _CHAR_WEBUI_ACTOR = "WebUI 控制台"
    _CHAR_ACTOR_MAX = 48

    @classmethod
    def _norm_actor(cls, raw) -> str:
        """规范化前端上报的「我是谁」：压掉换行/控制字符、限长，空则回落渠道名。

        不校验真伪 —— 能进面板的人本来就有写权限，这里只防把 500 字或换行塞进列表展示。
        """
        s = re.sub(r"[\r\n\t\x00-\x1f\x7f]", " ", str(raw or ""))
        s = re.sub(r"\s+", " ", s).strip()
        return s[: cls._CHAR_ACTOR_MAX] or cls._CHAR_WEBUI_ACTOR

    @staticmethod
    def _character_nsfw_policy(plugin) -> dict:
        """参考图打码要用的口径，与图库同源（别在前端另写一个 0.5）。

        三项分别取：旧版本图库可能缺 `_nsfw_default_blur` / `_nsfw_enabled`，
        一起 try 会让阈值也跟着丢，所以每项各自兜底。
        """
        out = {"threshold": 0.5, "blur_default": True, "enabled": True}
        g = getattr(plugin, "gallery", None)
        if g is None:
            return out
        for _key, _meth, _dflt in (
            ("threshold", "_nsfw_threshold", 0.5),
            ("blur_default", "_nsfw_default_blur", True),
            ("enabled", "_nsfw_enabled", True),
        ):
            try:
                out[_key] = getattr(g, _meth)()
            except Exception:
                out[_key] = _dflt
        try:
            out["threshold"] = float(out["threshold"])
        except (TypeError, ValueError):
            out["threshold"] = 0.5
        return out

    @staticmethod
    def _character_store(plugin):
        return getattr(plugin, "character", None)

    @staticmethod
    def _character_mod():
        """拿 character 逻辑模块（兼容包内/非包环境，沿用仓库既有导入范式）。"""
        try:
            from . import character as _cm
        except ImportError:
            import character as _cm
        return _cm

    @staticmethod
    def _character_cfg(plugin) -> dict:
        try:
            _c = plugin._cfg("character_card", {}) or {}
            return _c if isinstance(_c, dict) else {}
        except Exception:
            return {}

    async def character_list(self):
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            keyword = (request.query.get("keyword", "") or "").strip()
            rows = store.list_characters(keyword)
            # 角色数量通常很少，列表直接带全锚点与参考图，前端一次拿全、免二次请求
            for c in rows:
                c["anchors"] = store.list_anchors(int(c["id"]))
                c["refs"] = store.list_refs(int(c["id"]))
            cfg = self._character_cfg(self.plugin)
            return json_response({
                "characters": rows,
                "total": len(rows),
                "enabled": bool(cfg.get("enabled", True)),
                "can_edit": True,  # WebUI 本身即管理面（独立 WebUI 需 token、内嵌页在面板内）
                # 供前端在列表上说明「图是怎么进去的」：自动关联开着几张、封面会不会自动改
                "auto_link": {
                    "enabled": bool(cfg.get("auto_link_ref", True)),
                    "keep": int(cfg.get("auto_link_keep", 6) or 0),
                    "cover": bool(cfg.get("auto_link_cover", True)),
                },
                # 参考图 NSFW 打码口径（阈值与默认开关都跟图库同源，前端不自己写死）
                "nsfw": self._character_nsfw_policy(self.plugin),
            })
        except Exception as e:
            return error_response(f"读取角色卡片失败: {e}")

    async def character_detail(self):
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            key = (request.query.get("name", "") or request.query.get("id", "") or "").strip()
            if not key:
                return error_response("缺少 name 或 id")
            ch = store.get_character(key)
            if ch is None:
                return error_response(f"没找到角色「{key}」")
            ch["anchors"] = store.list_anchors(int(ch["id"]))
            ch["refs"] = store.list_refs(int(ch["id"]))
            return json_response(ch)
        except Exception as e:
            return error_response(f"读取角色详情失败: {e}")

    async def character_save(self):
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            name = (body.get("name") or "").strip()
            if not name:
                return error_response("缺少角色名 name")
            _aliases = body.get("aliases") or []
            if isinstance(_aliases, str):
                _aliases = [p.strip() for p in re.split(r"[,，]", _aliases) if p.strip()]
            ch = store.get_character(body.get("id") or name)
            if ch is None:
                ch = store.create_character(
                    name, aliases=_aliases,
                    persona_name=(body.get("persona_name") or "").strip(),
                    work=(body.get("work") or "").strip(),
                    lora_name=(body.get("lora_name") or "").strip(),
                    note=(body.get("note") or "").strip(),
                    enabled=bool(body.get("enabled", True)),
                    source="webui", created_by=self._norm_actor(body.get("created_by")),
                )
                return json_response({"ok": True, "created": True, "id": int(ch["id"])})
            upd: dict = {"name": name}
            for _k in ("persona_name", "work", "lora_name", "note"):
                if _k in body:
                    upd[_k] = (body.get(_k) or "").strip()
            if "aliases" in body:
                upd["aliases"] = _aliases
            if "enabled" in body:
                upd["enabled"] = bool(body.get("enabled"))
            store.update_character(int(ch["id"]), **upd)
            return json_response({"ok": True, "created": False, "id": int(ch["id"])})
        except Exception as e:
            return error_response(f"保存角色失败: {e}")

    async def character_delete(self):
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            ch = store.get_character(body.get("id") or body.get("name") or "")
            if ch is None:
                return error_response("没找到该角色")
            store.delete_character(int(ch["id"]))
            return json_response({"ok": True, "deleted": ch["name"]})
        except Exception as e:
            return error_response(f"删除角色失败: {e}")

    async def character_anchor_save(self):
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            positive = (body.get("positive") or "").strip()
            if not positive:
                return error_response("锚点标签（positive）不能为空")
            try:
                weight = float(body.get("weight") or 1.2)
            except (TypeError, ValueError):
                weight = 1.2
            anchor_id = body.get("id") or body.get("anchor_id")
            if anchor_id:
                # 编辑路径：只按锚点 id 定位，**不要求传 character_id**
                # （v5.16.2：此前编辑也强制先查角色，接口无谓地脆弱）
                if store.get_anchor_by_id(int(anchor_id)) is None:
                    return error_response(f"没找到锚点 id={anchor_id}")
                store.update_anchor(
                    int(anchor_id),
                    name=(body.get("anchor_name") or body.get("name") or "默认装").strip() or "默认装",
                    positive=positive,
                    kind=(body.get("kind") or "full").strip(),
                    negative=(body.get("negative") or "").strip(),
                    weight=weight,
                    lora_name=(body.get("lora_name") or "").strip(),
                    skip_trigger_words=bool(body.get("skip_trigger_words", True)),
                    note=(body.get("note") or "").strip(),
                )
                return json_response({"ok": True, "created": False, "id": int(anchor_id)})
            ch = store.get_character(body.get("character_id") or body.get("character_name") or "")
            if ch is None:
                return error_response("没找到该角色（新增锚点需要 character_id 或 character_name）")
            a = store.add_anchor(
                int(ch["id"]),
                (body.get("anchor_name") or body.get("name") or "默认装").strip() or "默认装",
                positive,
                kind=(body.get("kind") or "full").strip(),
                negative=(body.get("negative") or "").strip(),
                weight=weight,
                lora_name=(body.get("lora_name") or "").strip(),
                skip_trigger_words=bool(body.get("skip_trigger_words", True)),
                note=(body.get("note") or "").strip(),
                created_by=self._norm_actor(body.get("created_by")), source="webui",
            )
            if a is None:
                return error_response("新增锚点失败")
            return json_response({"ok": True, "created": True, "id": int(a["id"])})
        except Exception as e:
            return error_response(f"保存锚点失败: {e}")

    async def character_anchor_delete(self):
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            anchor_id = body.get("id") or body.get("anchor_id")
            if anchor_id:
                ok = store.delete_anchor(int(anchor_id))
                return json_response({"ok": bool(ok)})
            ch = store.get_character(body.get("character_id") or body.get("character_name") or "")
            if ch is None:
                return error_response("缺少 anchor_id，且角色不存在")
            a = store.get_anchor(int(ch["id"]), body.get("anchor_name") or None)
            if a is None:
                return error_response("没找到该锚点")
            return json_response({"ok": store.delete_anchor(int(a["id"]))})
        except Exception as e:
            return error_response(f"删除锚点失败: {e}")

    async def character_anchor_primary(self):
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            ch = store.get_character(body.get("character_id") or body.get("character_name") or "")
            if ch is None:
                return error_response("没找到该角色")
            a = store.set_primary_anchor(
                int(ch["id"]), body.get("anchor_id") or body.get("anchor_name") or None
            )
            if a is None:
                return error_response("没找到该锚点")
            return json_response({"ok": True, "primary": a["name"]})
        except Exception as e:
            return error_response(f"设置主锚点失败: {e}")

    async def character_ref_upload(self):
        """上传角色参考图（M3）：JSON {character_id, filename, data(base64)} 或原始二进制。

        与 `lora_upload_image` 同一套入参约定（raw 二进制时用 `x-character-id` / `x-filename` 头），
        因此独立 WebUI 的 `_AioReqAdapter` 无需改动即可复用。落盘后跑 NSFW 打标。
        """
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            raw = await request.body()
            ctype = (request.headers.get("content-type") or "").lower()
            # 容错：某些反向代理/客户端会丢 content-type；body 以 `{` 开头即按 JSON 解析
            if "json" not in ctype and raw[:1] == b"{":
                ctype = "application/json"
            char_key = ""
            anchor_id = 0
            actor_raw = ""
            filename = f"ref_{uuid.uuid4().hex}.png"
            data_bytes = None
            if "json" in ctype:
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    payload = {}
                char_key = str(payload.get("character_id") or payload.get("character_name") or "").strip()
                actor_raw = str(payload.get("created_by") or "")
                try:
                    anchor_id = int(payload.get("anchor_id") or 0)
                except Exception:
                    anchor_id = 0
                if (payload.get("filename") or "").strip():
                    filename = os.path.basename(str(payload["filename"]).strip())
                b64 = payload.get("data") or payload.get("base64") or ""
                if "," in b64:  # 兼容 dataURL 前缀
                    b64 = b64.split(",", 1)[1]
                try:
                    data_bytes = base64.b64decode(b64)
                except Exception:
                    return error_response("base64 数据无效")
            else:
                data_bytes = raw
                char_key = (request.headers.get("x-character-id") or "").strip()
                actor_raw = request.headers.get("x-created-by") or ""
                try:
                    anchor_id = int(request.headers.get("x-anchor-id") or 0)
                except Exception:
                    anchor_id = 0
                if (request.headers.get("x-filename") or "").strip():
                    filename = os.path.basename(request.headers.get("x-filename").strip())
            if not data_bytes or len(data_bytes) < 16:
                return error_response("图片数据为空或过小")
            if len(data_bytes) > 12 * 1024 * 1024:
                return error_response("图片过大（上限 12MB）")
            ch = store.get_character(char_key)
            if ch is None:
                return error_response("缺少有效的 character_id（或角色不存在）")
            try:
                _char_mod = self._character_mod()
            except Exception:
                return error_response("character 模块不可用")
            ref = await _char_mod.land_ref(
                self.plugin, int(ch["id"]), data_bytes, filename=filename,
                note="WebUI 上传", anchor_id=anchor_id,
                created_by=self._norm_actor(actor_raw), origin="upload",
            )
            if ref is None:
                return error_response("参考图落地失败")
            return json_response({
                "ok": True, "id": int(ref["id"]), "dedup": bool(ref.get("dedup")),
                "nsfw_score": float(ref.get("nsfw_score") or -1),
                "sha256": ref.get("sha256") or "",
                "anchor_id": int(ref.get("anchor_id") or 0),
            })
        except Exception as e:
            return error_response(f"上传参考图失败: {e}")

    async def character_ref_image(self):
        """返回参考图缩略图 data URL（query: id=）。"""
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            _id = (request.query.get("id", "") or "").strip()
            if not _id.isdigit():
                return error_response("缺少 id 参数")
            ref = store.get_ref(int(_id))
            if ref is None:
                return error_response("参考图不存在", status_code=404)
            _p = Path(ref.get("path") or "")
            if not _p.exists():
                return error_response("参考图文件缺失", status_code=404)
            try:
                _size_raw = (request.query.get("size", "") or "").strip().lower()
            except Exception:
                _size_raw = ""
            if _size_raw in ("orig", "original", "full", "0"):
                # v6.1.3：size=orig 返回原图（「查看大图」用），不缩放
                _res = await asyncio.to_thread(self._orig_data_url_sync, _p)
                if not _res:
                    return error_response("原图过大或读取失败", status_code=413)
                return json_response({"id": int(_id), "url": _res[0]})
            try:
                _size = int(_size_raw or "640")
            except Exception:
                _size = 640
            _res = await asyncio.to_thread(
                self._thumb_data_url_sync, _p, max(64, min(1600, _size))
            )
            if not _res:
                return error_response("生成缩略图失败")
            return json_response({"id": int(_id), "url": _res[0]})
        except Exception as e:
            return error_response(f"读取参考图失败: {e}")

    async def character_ref_delete(self):
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            _id = body.get("id") or body.get("ref_id")
            if not _id:
                return error_response("缺少 id")
            ok = store.delete_ref(int(_id))
            return json_response({"ok": bool(ok)})
        except Exception as e:
            return error_response(f"删除参考图失败: {e}")

    async def character_ref_from_gallery(self):
        """把图库里某张图复制为角色参考图（避免用户重复上传）。body: {character_id, sha}"""
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            g = self._gallery()
            if g is None:
                return error_response("图库未启用")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            ch = store.get_character(body.get("character_id") or body.get("character_name") or "")
            if ch is None:
                return error_response("没找到该角色")
            sha = (body.get("sha") or body.get("sha256") or "").strip()
            if not sha:
                return error_response("缺少 sha")
            src = g.path_of(sha)
            if not src or not Path(src).exists():
                return error_response("图库里没有这张图", status_code=404)
            try:
                _char_mod = self._character_mod()
            except Exception:
                return error_response("character 模块不可用")
            try:
                _aid = int(body.get("anchor_id") or 0)
            except Exception:
                _aid = 0
            ref = await _char_mod.land_ref(
                self.plugin, int(ch["id"]), Path(src).read_bytes(),
                filename=Path(src).name, note=f"来自图库 {sha[:12]}", anchor_id=_aid,
                created_by=self._norm_actor(body.get("created_by")), origin="gallery",
            )
            if ref is None:
                return error_response("参考图落地失败")
            return json_response({"ok": True, "id": int(ref["id"]), "dedup": bool(ref.get("dedup"))})
        except Exception as e:
            return error_response(f"从图库导入失败: {e}")

    async def character_options(self):
        """角色卡表单的下拉候选（v6.1.1）：**角色类 LoRA** + AstrBot 人格列表。

        为什么要专用接口：LoRA 库在 `config.loras` 里，但条目含 C 站描述（HTML，动辄数 KB），
        整份 config 拉到角色页太重；这里只回精简字段，且只取「分类=角色」的 LoRA
        （角色卡关联的就是角色 LoRA）。人格列表来自 AstrBot 的 `persona_manager`
        （`personas_v3` 与 `selected_default_persona_v3`），取不到时回空列表并给提示，
        前端仍可手输人格名。
        """
        out: dict = {
            "loras": [], "lora_role_total": 0, "lora_total": 0,
            "personas": [], "default_persona": "",
        }
        # ---- 角色类 LoRA ----
        try:
            lib = []
            try:
                lib = self.plugin._lora_library() or []
            except Exception:
                lib = list((getattr(self.plugin, "config", {}) or {}).get("loras") or [])
            out["lora_total"] = len(lib)
            _slim = []
            for l in lib:
                if not isinstance(l, dict):
                    continue
                _name = (l.get("name") or "").strip()
                if not _name:
                    continue
                if (l.get("category") or "").strip() != "角色":
                    continue
                _slim.append({
                    "name": _name,
                    "base_model": (l.get("base_model") or "").strip(),
                    "trigger_words": (l.get("trigger_words") or "").strip()[:200],
                    "cover": (l.get("cover") or "").strip(),
                    "enabled": l.get("enabled", True) is not False,
                })
            _slim.sort(key=lambda x: x["name"])
            out["loras"] = _slim
            out["lora_role_total"] = len(_slim)
        except Exception as e:
            logger.warning(f"【角色卡】 读取 LoRA 库失败（下拉留空）: {e}")
        # ---- 人格列表 ----
        try:
            mgr = getattr(getattr(self.plugin, "context", None), "persona_manager", None)
            if mgr is not None:
                _seen: set[str] = set()
                _plist = []
                for p in (getattr(mgr, "personas_v3", None) or []):
                    if isinstance(p, dict):
                        _n = str(p.get("name") or "").strip()
                        _pr = str(p.get("prompt") or "")
                    else:
                        _n = str(getattr(p, "name", "") or "").strip()
                        _pr = str(getattr(p, "prompt", "") or "")
                    if not _n or _n in _seen:
                        continue
                    _seen.add(_n)
                    _plist.append({
                        "name": _n,
                        "preview": " ".join(_pr.split())[:60],
                    })
                _dflt = getattr(mgr, "selected_default_persona_v3", None)
                if isinstance(_dflt, dict):
                    out["default_persona"] = str(_dflt.get("name") or "").strip()
                elif _dflt is not None:
                    out["default_persona"] = str(getattr(_dflt, "name", "") or "").strip()
                out["personas"] = _plist
        except Exception as e:
            logger.warning(f"【角色卡】 读取人格列表失败（下拉留空）: {e}")
        return json_response(out)

    async def character_cover_set(self):
        """设置 / 清除角色封面（v6.1.0）。body: {character_id, ref_id}；ref_id=0 → 清除。"""
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            ch = store.get_character(
                body.get("character_id") or body.get("character_name") or body.get("id") or ""
            )
            if ch is None:
                return error_response("没找到该角色")
            try:
                _rid = int(body.get("ref_id") or 0)
            except Exception:
                _rid = 0
            if not _rid:
                store.clear_cover_ref(int(ch["id"]))
                return json_response({"ok": True, "ref_id": 0, "auto": True})
            _ref = store.get_ref(_rid)
            if _ref is None or int(_ref.get("character_id") or 0) != int(ch["id"]):
                return error_response(f"「{ch['name']}」下没有 id={_rid} 的图片")
            store.set_cover_ref(int(ch["id"]), _rid)
            return json_response({"ok": True, "ref_id": _rid, "auto": False})
        except ValueError as e:
            return error_response(str(e))
        except Exception as e:
            return error_response(f"设置封面失败: {e}")

    async def character_ref_anchor(self):
        """把一张图挪到某个锚点下（v6.1.0）。body: {id, anchor_id}（anchor_id=0 → 角色级）。"""
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            try:
                _rid = int(body.get("id") or body.get("ref_id") or 0)
            except Exception:
                _rid = 0
            if not _rid:
                return error_response("缺少 id")
            ref = store.get_ref(_rid)
            if ref is None:
                return error_response("图片不存在", status_code=404)
            try:
                _aid = int(body.get("anchor_id") or 0)
            except Exception:
                _aid = 0
            out = store.set_ref_anchor(_rid, _aid)
            if out is None:
                return error_response("调整归属失败")
            return json_response({"ok": True, "id": _rid, "anchor_id": int(out.get("anchor_id") or 0)})
        except Exception as e:
            return error_response(f"调整归属失败: {e}")

    async def character_suggest(self):
        """用 danbooru 标签服务补全候选锚点标签（**仅返回候选，需人工确认后再落库**）。"""
        try:
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            name = (body.get("name") or "").strip()
            work = (body.get("work") or "").strip()
            if not name:
                return error_response("缺少 name")
            try:
                _char_mod = self._character_mod()
            except Exception:
                return error_response("character 模块不可用")
            res = await _char_mod.suggest_anchor(self.plugin, name, work)
            if not res.get("ok"):
                return error_response(res.get("msg") or "补全失败")
            return json_response(res)
        except Exception as e:
            return error_response(f"补全失败: {e}")

    async def character_export(self):
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            return json_response(store.export_all())
        except Exception as e:
            return error_response(f"导出失败: {e}")

    async def character_import(self):
        try:
            store = self._character_store(self.plugin)
            if store is None:
                return error_response("角色卡片模块未启用（存储初始化失败）")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            data = body.get("data") if isinstance(body.get("data"), dict) else body
            if not isinstance(data, dict) or not data.get("characters"):
                return error_response("导入数据格式不对（需要本插件导出的 JSON）")
            n = store.import_all(data)
            return json_response({"ok": True, "imported": n})
        except Exception as e:
            return error_response(f"导入失败: {e}")

    async def story_sessions(self):
        try:
            store = getattr(self.plugin, "story", None)
            if store is None:
                return error_response("剧情模块未启用")
            page = int((request.query.get("page", "1") or "1"))
            size = min(int(request.query.get("size", "20") or "20"), 200)
            keyword = (request.query.get("keyword", "") or "").strip()
            user_id = (request.query.get("user_id", "") or "").strip()
            status = (request.query.get("status", "") or "").strip()
            date_from = (request.query.get("date_from", "") or "").strip()
            date_to = (request.query.get("date_to", "") or "").strip()
            rows, total = store.get_sessions(
                page=page, size=size, keyword=keyword, user_id=user_id,
                status=status, date_from=date_from, date_to=date_to,
            )
            return json_response({"sessions": rows, "total": total, "page": page, "size": size})
        except Exception as e:
            return error_response(f"读取剧情档案失败: {e}")

    async def story_session_detail(self):
        try:
            store = getattr(self.plugin, "story", None)
            if store is None:
                return error_response("剧情模块未启用")
            sid = int((request.query.get("id", "0") or "0"))
            sess = store.get_session(sid)
            if sess is None:
                return error_response("未找到该剧情档案")
            return json_response(sess)
        except Exception as e:
            return error_response(f"读取详情失败: {e}")

    async def story_session_update(self):
        try:
            store = getattr(self.plugin, "story", None)
            if store is None:
                return error_response("剧情模块未启用")
            body = await request.json(default={}) or {}
            if not isinstance(body, dict):
                body = {}
            sid = int(body.get("id") or 0)
            if not sid:
                return error_response("缺少 id")
            fields = {k: body[k] for k in (
                "title", "summary", "mood", "scene", "characters", "tags",
                "rating", "notes", "status", "source",
            ) if k in body}
            store.update_session(sid, fields)
            return json_response({"ok": True})
        except Exception as e:
            return error_response(f"更新失败: {e}")

    async def story_session_delete(self):
        try:
            store = getattr(self.plugin, "story", None)
            if store is None:
                return error_response("剧情模块未启用")
            body = await request.json(default={}) or {}
            ids = body.get("ids") or []
            if not isinstance(ids, list):
                ids = [ids]
            ids = [int(x) for x in ids if str(x).isdigit()]
            deleted = store.delete_sessions(ids)
            return json_response({"ok": True, "deleted": deleted})
        except Exception as e:
            return error_response(f"删除失败: {e}")

    async def story_stats(self):
        try:
            store = getattr(self.plugin, "story", None)
            if store is None:
                return error_response("剧情模块未启用")
            return json_response(store.stats())
        except Exception as e:
            return error_response(f"统计失败: {e}")


def _ext_of(mime: str) -> str:
    """根据 MIME 推断文件扩展名（用于 gallery_image 下载文件名）。"""
    ext_map = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/bmp": "bmp",
        "image/avif": "avif",
    }
    return ext_map.get((mime or "").lower(), "jpg")


def _thumb_bytes(path, max_w: int = 300) -> tuple:
    """生成缩略图二进制（JPEG 字节），用于独立 WebUI 直连返回。

    与 _thumb_data_url 的区别：直接返回压缩后的 JPEG 字节，省去 base64 编解码的
    往返开销（base64 比原字节大 ~33% 且需再解码）；大图经此返回体积更小、加载更快。
    返回 (bytes, mime)；失败降级为 (原图字节, 原图 mime)。"""
    try:
        p = str(path)
        if not p or not Path(p).exists():
            return (b"", "image/jpeg")
        try:
            from PIL import Image as _PILImage
        except Exception:
            _PILImage = None
        mime = mimetypes.guess_type(p)[0] or "image/jpeg"
        raw = Path(p).read_bytes()
        if _PILImage is not None:
            try:
                with _PILImage.open(p) as im:
                    im.seek(0)
                    w, h = im.size
                    if w > max_w:
                        nh = max(1, int(h * max_w / w))
                        im = im.resize((max_w, nh), _PILImage.LANCZOS)
                    # 统一转 WebP：体积远小于 PNG/JPEG，支持透明，浏览器全兼容
                    if im.mode in ("P", "LA"):
                        im = im.convert("RGBA")
                    elif im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGB")
                    buf = io.BytesIO()
                    try:
                        im.save(buf, format="WEBP", quality=72, method=4)
                        return (buf.getvalue(), "image/webp")
                    except Exception:
                        # 旧 Pillow 不支持 WebP 时降级 JPEG
                        im = im.convert("RGB") if im.mode != "RGB" else im
                        im.save(buf, format="JPEG", optimize=True, quality=72)
                        return (buf.getvalue(), "image/jpeg")
            except Exception:
                pass
        return (raw, mime)
    except Exception:
        return (b"", "image/jpeg")


def _thumb_data_url(path, max_w: int = 300) -> str:
    """生成缩略图 data URL（不走 AstrBot 路由、无需 token，前端 <img> 直连可用）。

    背景：AstrBot 插件 API 挂在 /api/v1/plugins/extensions/<插件名>/... 下且需要登录
    token，浏览器 <img> 直连后端返回的裸路径要么 404 要么 401；而直接内联整图 base64
    又会因图库一次几十张原图导致 10s 超时（v2.2.26 曾因此改为 URL）。这里用 Pillow 把
    图压到小尺寸再 base64，体积小、不走路由，同时规避 404 与超时。环境无 Pillow 时
    降级为直接内联原图（数量受限时也能用）。"""
    try:
        p = str(path)
        if not p or not Path(p).exists():
            return ""
        try:
            from PIL import Image as _PILImage
        except Exception:
            _PILImage = None
        mime = mimetypes.guess_type(p)[0] or "image/jpeg"
        raw = Path(p).read_bytes()
        if _PILImage is not None:
            try:
                with _PILImage.open(p) as im:
                    im.seek(0)
                    w, h = im.size
                    if w > max_w:
                        nh = max(1, int(h * max_w / w))
                        im = im.resize((max_w, nh), _PILImage.LANCZOS)
                    # 统一转 WebP：体积远小于 PNG/JPEG，支持透明，浏览器全兼容
                    if im.mode in ("P", "LA"):
                        im = im.convert("RGBA")
                    elif im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGB")
                    buf = io.BytesIO()
                    try:
                        im.save(buf, format="WEBP", quality=72, method=4)
                        return f"data:image/webp;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
                    except Exception:
                        # 旧 Pillow 不支持 WebP 时降级 JPEG
                        im = im.convert("RGB") if im.mode != "RGB" else im
                        im.save(buf, format="JPEG", optimize=True, quality=72)
                        return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
            except Exception:
                pass
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    except Exception:
        return ""


def _log_request(handler, route_desc: str):
    """包装 WebUI handler，打印每次请求的路由、参数、耗时与返回状态，
    便于在「页面在另一个地址打开导致请求超时/404」时定位问题。"""
    from astrbot.api import logger as _log

    @wraps(handler)
    async def wrapper(*args, **kwargs):
        t0 = time.time()
        # 尝试读取当前请求的 path / 客户端信息（astrbot.api.web.request 为模块级当前请求）
        req_path = route_desc
        client = "-"
        try:
            from astrbot.api.web import request as _req
            if _req is not None:
                req_path = getattr(_req.url, "path", route_desc) or route_desc
                client = getattr(_req.client, "host", "-") or "-"
        except Exception:
            pass
        try:
            result = await handler(*args, **kwargs)
            cost = (time.time() - t0) * 1000
            _log.info(
                f"[WebUI] 请求 {req_path} ({route_desc}) from={client} "
                f"耗时={cost:.0f}ms 状态=OK"
            )
            return result
        except Exception as e:
            cost = (time.time() - t0) * 1000
            _log.warning(
                f"[WebUI] 请求 {req_path} ({route_desc}) from={client} "
                f"耗时={cost:.0f}ms 状态=ERR: {e}"
            )
            raise

    return wrapper


def get_webui_api_class():
    """从磁盘源码可靠获取最新 WebUIApi 类。

    AStrBot 热更新只重载 main.py，webui_api 的 sys.modules 缓存可能是旧版；而
    reload 也可能因加载方式不同拿到孤立新对象、或直接失败。为保证拿到的一定是
    磁盘上最新代码定义的 WebUIApi（含 story_sessions 等新接口），按序尝试：
    1) reload 本模块后取类；2) 从磁盘 __file__ 强制重新执行源码取类。
    内嵌路由注册与 main.py 重建独立 WebUI 的 _api 实例都复用本函数。
    """
    try:
        import importlib
        _mod = importlib.reload(importlib.import_module(__name__))
        _cls = getattr(_mod, "WebUIApi", None)
        if _cls is not None:
            return _cls
    except Exception:
        pass
    try:
        import importlib.util
        _spec = importlib.util.spec_from_file_location(__name__ + "._fresh", __file__)
        if _spec is not None and _spec.loader is not None:
            _fresh = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_fresh)
            _cls = getattr(_fresh, "WebUIApi", None)
            if _cls is not None:
                return _cls
    except Exception:
        pass
    return WebUIApi


def register_web_api(plugin) -> None:
    """在插件 initialize 时调用，注册所有控制台路由。"""
    from astrbot.api import logger as _log
    # 关键：必须从 reload 返回的模块对象上取 WebUIApi 类，而不是用本函数所在模块的
    # 全局名。AStrBot 热更新/非标准加载下，当前模块全局的 WebUIApi 可能仍是旧类
    # （缺 story_sessions 等新方法），直接用旧类建实例会被下方 _h 的 getattr 判为
    # 「缺少方法」而跳过全部剧情路由。
    _WebUIApi = get_webui_api_class()
    api = _WebUIApi(plugin)
    ctx = plugin.context
    # 路由必须含插件名：/<plugin_name>/<endpoint>。
    # 对齐 AstrBot 官方《插件 Pages》约定：后端注册带插件名前缀、
    # 不带 /page；前端 bridge endpoint 写相对路径（不带插件名、不带 /page），
    # 由 Dashboard 自动转发到 /api/v1/plugins/extensions/<plugin_name>/<endpoint>。
    prefix = f"/{PLUGIN_NAME}"

    # 安全获取 handler：AStrBot 热更新若仍残留旧版 WebUIApi 类（缺某些新增方法，
    # 例如剧情接口 story_sessions / story_stats），用 getattr 取不到就返回 None，
    # 仅跳过该路由，避免整个 routes 列表构建抛出 AttributeError 导致主控制台全部
    # 路由注册失败（那样 schema/config/gallery 等也全挂掉）。
    def _h(name: str):
        return getattr(api, name, None)

    routes = [
        (f"{prefix}/schema", _h("get_schema"), ["GET"], "读取配置 schema"),
        (f"{prefix}/config", _h("get_config"), ["GET"], "读取控制台配置"),
        (f"{prefix}/config", _h("save_config"), ["POST"], "保存控制台配置"),
        (f"{prefix}/logs", _h("get_logs"), ["GET"], "读取控制台日志"),
        (f"{prefix}/records", _h("get_records"), ["GET"], "读取出图记录"),
        (f"{prefix}/oplog", _h("get_oplog"), ["GET"], "独立操作日志"),
        (f"{prefix}/gallery/stats", _h("gallery_stats"), ["GET"], "图库统计"),
        (f"{prefix}/gallery/search", _h("gallery_search"), ["GET"], "图库检索"),
        (f"{prefix}/gallery/thumb", _h("gallery_thumb"), ["GET"], "图库缩略图"),
        (f"{prefix}/gallery/image", _h("gallery_image"), ["GET"], "图库图片"),
        (f"{prefix}/gallery/star", _h("gallery_star"), ["POST"], "图库收藏"),
        (f"{prefix}/gallery/set_blur", _h("gallery_set_blur"), ["POST"], "图库单图NSFW模糊"),
        (f"{prefix}/gallery/set_nsfw", _h("gallery_set_nsfw"), ["POST"], "图库单图人工标记/取消NSFW"),
        (f"{prefix}/gallery/scan_nsfw", _h("gallery_scan_nsfw"), ["GET"], "图库NSFW一键扫描"),
        (f"{prefix}/gallery/scan_nsfw_progress", _h("gallery_scan_nsfw_progress"), ["GET"], "图库NSFW扫描进度"),
        (f"{prefix}/gallery/check_nsfw", _h("gallery_check_nsfw"), ["GET"], "图库单图NSFW检测"),
        (f"{prefix}/gallery/delete", _h("gallery_delete"), ["POST"], "图库删除(移入回收站)"),
        (f"{prefix}/gallery/trash", _h("gallery_trash"), ["GET"], "图库回收站"),
        (f"{prefix}/gallery/restore", _h("gallery_restore"), ["POST"], "图库恢复"),
        (f"{prefix}/gallery/purge", _h("gallery_purge"), ["POST"], "图库彻底删除"),
        (f"{prefix}/gallery/tags", _h("gallery_tags"), ["POST"], "图库打标签"),
        (f"{prefix}/gallery/backup", _h("backup_db"), ["GET"], "备份图库数据库"),
        (f"{prefix}/stats/ranking", _h("stats_ranking"), ["GET"], "用户生图排行"),
        (f"{prefix}/stats/trend", _h("stats_trend"), ["GET"], "生图小时趋势"),
        (f"{prefix}/quota/users", _h("quota_users"), ["GET"], "生图限额用户列表"),
        (f"{prefix}/quota/config", _h("quota_save_config"), ["POST"], "生图限额配置保存"),
        (f"{prefix}/quota/save_global", _h("quota_save_global"), ["POST"], "生图全局限额保存"),
        (f"{prefix}/quota/reset", _h("quota_reset"), ["POST"], "生图次数重置"),
        (f"{prefix}/token/summary", _h("token_summary"), ["GET"], "LLM token 用量统计"),
        (f"{prefix}/token/reset", _h("token_reset"), ["POST"], "LLM token 统计重置"),
        (f"{prefix}/lora/fetch", _h("lora_fetch"), ["POST"], "C站 LoRA 抓取 / 任意图片直链下载封面"),
        (f"{prefix}/lora/upload_image", _h("lora_upload_image"), ["POST"], "LoRA 封面图上传"),
        (f"{prefix}/lora/image", _h("lora_image"), ["GET"], "LoRA 封面图读取"),
        (f"{prefix}/basemodels", _h("basemodels_list"), ["GET"], "底模库列表"),
        (f"{prefix}/basemodels/save", _h("basemodels_save"), ["POST"], "底模保存"),
        (f"{prefix}/basemodels/delete", _h("basemodels_delete"), ["POST"], "底模删除"),
        (f"{prefix}/basemodels/fetch", _h("basemodels_fetch"), ["POST"], "底模 C站抓取"),
        (f"{prefix}/basemodels/upload_image", _h("basemodels_upload_image"), ["POST"], "底模封面上传"),
        (f"{prefix}/basemodels/image", _h("basemodel_image"), ["GET"], "底模封面读取"),
        (f"{prefix}/baseworkflows", _h("baseworkflows_list"), ["GET"], "基础工作流列表"),
        (f"{prefix}/baseworkflows/upload", _h("baseworkflows_upload"), ["POST"], "基础工作流上传入库"),
        (f"{prefix}/baseworkflows/delete", _h("baseworkflows_delete"), ["POST"], "基础工作流删除"),
        (f"{prefix}/baseworkflows/reparse", _h("baseworkflows_reparse"), ["POST"], "基础工作流重解析"),
        (f"{prefix}/baseworkflows/json", _h("baseworkflows_json"), ["GET"], "基础工作流原始 JSON"),
        (f"{prefix}/baseworkflows/fetch", _h("baseworkflows_fetch"), ["POST"], "基础工作流 C站抓取"),
        (f"{prefix}/baseworkflows/meta", _h("baseworkflows_meta"), ["POST"], "基础工作流元数据更新"),
        (f"{prefix}/translate/test", _h("translate_test"), ["POST"], "翻译调试（测试三种翻译模式）"),
        (f"{prefix}/workflows/sampler", _h("workflow_sampler"), ["GET"], "读取工作流采样器参数"),
        (f"{prefix}/platforms", _h("platforms_get"), ["GET"], "读取生图平台配置"),
        (f"{prefix}/platforms/save", _h("platforms_save"), ["POST"], "保存生图平台配置"),
        (f"{prefix}/platforms/test", _h("platforms_test"), ["POST"], "生图平台连通性测试"),
        (f"{prefix}/platforms/quota", _h("platforms_quota"), ["POST"], "查询 NAI 平台余额"),
        (f"{prefix}/share/tokens", _h("share_tokens"), ["GET"], "分享链接管理列表"),
        (f"{prefix}/share/token/invalidate", _h("share_token_invalidate"), ["POST"], "分享链接作废"),
        (f"{prefix}/character/list", _h("character_list"), ["GET"], "角色卡片列表"),
        (f"{prefix}/character/detail", _h("character_detail"), ["GET"], "角色卡片详情"),
        (f"{prefix}/character/save", _h("character_save"), ["POST"], "角色卡片新增/更新"),
        (f"{prefix}/character/delete", _h("character_delete"), ["POST"], "角色卡片删除"),
        (f"{prefix}/character/anchor/save", _h("character_anchor_save"), ["POST"], "锚点新增/更新"),
        (f"{prefix}/character/anchor/delete", _h("character_anchor_delete"), ["POST"], "锚点删除"),
        (f"{prefix}/character/anchor/primary", _h("character_anchor_primary"), ["POST"], "设主锚点"),
        (f"{prefix}/character/ref/upload", _h("character_ref_upload"), ["POST"], "上传角色参考图"),
        (f"{prefix}/character/ref/image", _h("character_ref_image"), ["GET"], "角色参考图缩略图"),
        (f"{prefix}/character/ref/delete", _h("character_ref_delete"), ["POST"], "删除角色参考图"),
        (f"{prefix}/character/ref/from_gallery", _h("character_ref_from_gallery"), ["POST"], "从图库导入参考图"),
        (f"{prefix}/character/ref/anchor", _h("character_ref_anchor"), ["POST"], "调整图片的锚点归属"),
        (f"{prefix}/character/cover/set", _h("character_cover_set"), ["POST"], "设置/清除角色封面"),
        (f"{prefix}/character/options", _h("character_options"), ["GET"], "角色卡下拉候选（角色类 LoRA / 人格）"),
        (f"{prefix}/character/suggest", _h("character_suggest"), ["POST"], "联网补全候选标签"),
        (f"{prefix}/character/export", _h("character_export"), ["GET"], "角色卡片导出"),
        (f"{prefix}/character/import", _h("character_import"), ["POST"], "角色卡片导入"),
        (f"{prefix}/story/sessions", _h("story_sessions"), ["GET"], "剧情档案列表"),
        (f"{prefix}/story/session", _h("story_session_detail"), ["GET"], "剧情档案详情"),
        (f"{prefix}/story/session", _h("story_session_update"), ["POST"], "剧情档案更新"),
        (f"{prefix}/story/session/delete", _h("story_session_delete"), ["POST"], "剧情档案删除"),
        (f"{prefix}/story/stats", _h("story_stats"), ["GET"], "剧情档案统计"),
    ]
    # v6.0.0：把「路径 → 允许的方法」落成模块级表，供独立 WebUI 通道复用做方法校验。
    # 背景：独立通道的 catch-all 路由对 GET/POST 一视同仁，而部分破坏性端点
    # （quota/reset、token/reset…）在内嵌通道是 POST-only → 曾被 GET（含 <img src>
    # 式 CSRF）触发。这里复用同一张方法表，避免两套通道语义漂移。
    ROUTE_METHODS.clear()
    ROUTE_METHODS.update(build_route_methods(routes))

    registered = []
    for path, handler, methods, desc in routes:
        if handler is None:
            _log.warning(f"[WebUI] 跳过未实现路由（WebUIApi 缺少方法）: {path}")
            continue
        try:
            ctx.register_web_api(path, _log_request(handler, desc), methods, desc)
            registered.append(path)
        except Exception as e:
            _log.warning(f"[WebUI] 注册路由失败 {path}: {e}")
    _log.info(f"[WebUI] 已注册控制台路由 {len(registered)} 个: {registered}")
