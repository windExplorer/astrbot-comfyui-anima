"""Anima 控制台 WebUI 后端 API。

通过 AstrBot 的 context.register_web_api 注册路由，配合 pages/anima-console/
下的前端页面使用。

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
from collections import deque
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlparse

from astrbot.api.web import error_response, file_response, json_response, request

# 插件名（与 metadata.yaml 的 name 一致）。路由前缀必须含它，否则 AstrBot
# 的插件页面桥接会把请求发到错误路径（全部 404 / 前端永远加载中）。
PLUGIN_NAME = "astrbot_plugin_comfyui_anima"

# 内存日志环形缓冲（main.py 的日志 handler 会写入这里）
LOG_BUFFER: "deque[str]" = deque(maxlen=2000)


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
            cfg = self.plugin.config
            # AstrBot 的 Config 对象本身是可映射的，直接转 dict 后序列化
            try:
                safe = dict(cfg)
            except Exception:
                safe = json.loads(json.dumps(cfg, default=lambda o: str(o)))
            return json_response(safe)
        except Exception as e:
            return error_response(f"读取配置失败: {e}")

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
            return json_response({"msg": "配置已保存"})
        except Exception as e:
            return error_response(f"保存配置失败: {e}")

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
        """用户生图数量排行。query: days=today|3|7|all（默认 all）；merge=1 时合并其他插件记录。"""
        g = self._gallery()
        if g is None:
            return json_response({"scope": "all", "total": 0, "rows": []})
        try:
            days_raw = request.query.get("days", "all")
            days = {"today": 0, "3": 3, "7": 7, "all": None}.get(str(days_raw).strip().lower(), None)
            merge = request.query.get("merge", "0") == "1"
            merge_names = ["PrivateCompanion"] if merge else None
            return json_response(g.user_ranking(days=days, merge_alsoknown=merge_names))
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
                return json_response({"ok": True, "reset_all": True, "count": n})
            ok = q.reset_user(user_id)
            return json_response({"ok": ok, "reset_user": user_id})
        except Exception as e:
            return error_response(f"重置生图次数失败: {e}")

    # -------------------------------------------------------------- #
    # LoRA 封面 / C 站抓取
    # -------------------------------------------------------------- #
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
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            # 压缩缩略图（最大宽/高 640px），避免原图 base64 过大导致前端 <img> 无法显示/卡顿
            data = None
            try:
                from PIL import Image as _PImage
                import io as _io

                with _PImage.open(path) as _im:
                    _im.thumbnail((640, 640))
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
                data = await asyncio.to_thread(path.read_bytes)
            b64 = base64.b64encode(data).decode("ascii")
            return json_response({"name": fname, "url": f"data:{mime};base64,{b64}"})
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
                            return error_response("C 站 API 拒绝了匿名请求（401/403）。请在插件配置「网络与代理」里填写 civitai_api_key（C 站 Settings → Account → API Keys 生成），否则无法获取描述/封面。")
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
                base_model = str(version.get("baseModel") or "").strip()
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
                    candidates.append((u, w, h))
                if candidates:
                    # C 站主图 URL 文件名以「00001-」开头（作者主封面）；优先选它，否则取第一张
                    cover_url = candidates[0][0]
                    for _u, _w, _h in candidates:
                        _base = os.path.basename(urlparse(_u).path).lower()
                        if _base.startswith("00001-") or "00001." in _base:
                            cover_url = _u
                            break
                    # 诊断：记录候选图 URL 与尺寸，便于比对页面封面
                    try:
                        from astrbot.api import logger as _log
                        _log.info(f"[LoRA抓取] 候选封面 {len(candidates)} 张: " + "; ".join(f"{_u} ({_w}x{_h})" for _u, _w, _h in candidates[:5]))
                    except Exception:
                        pass
                    # 逐个下载候选图（最多 6 张有效图），前端选图用
                    img_headers = dict(headers)
                    img_headers["Referer"] = "https://civitai.com/"
                    cover_timeout = aiohttp.ClientTimeout(total=15)
                    fetched_covers = []  # 已保存的封面文件名
                    for _u, _w, _h in candidates:
                        if not _u:
                            continue
                        if len(fetched_covers) >= 6:
                            break
                        try:
                            async with aiohttp.ClientSession(headers=img_headers, trust_env=True) as _sess:
                                async with _sess.get(_u, timeout=cover_timeout, proxy=proxy) as _resp:
                                    if _resp.status != 200:
                                        continue
                                    _img_data = await _resp.read()
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
            stype = request.query.get("type", "") or None
            if stype in ("", "all"):
                stype = None
            starred = request.query.get("starred", "0") == "1"
            trash = request.query.get("trash", "0") == "1"
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
                            trash=trash, limit=size, offset=offset)
            total = g.count_search(keyword=kw, type=stype,
                                   starred_only=starred, trash=trash)
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
            data_url = await asyncio.to_thread(_thumb_data_url, path, max_w=size)
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
            try:
                raw = await asyncio.to_thread(Path(path).read_bytes)
                mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
                encoded = base64.b64encode(raw).decode("ascii")
                return json_response({"data_url": f"data:{mime};base64,{encoded}", "mime": mime, "meta": meta})
            except Exception as e:
                return error_response(f"读取图片失败: {e}")
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        return file_response(path, filename=f"{sha}.{_ext_of(mime)}", content_type=mime)

    async def gallery_star(self):
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            payload = await request.json(default={}) or {}
            sha = payload.get("sha", "")
            on = 1 if payload.get("on", True) else 0
            if not sha:
                return error_response("缺少 sha")
            ok = g.star(sha, on=on)
            return json_response({"msg": "已更新收藏" if ok else "未找到该图"})
        except Exception as e:
            return error_response(f"操作失败: {e}")

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
            if not sha or not tags:
                return error_response("缺少 sha 或 tags")
            g.add_tags(sha, tags if isinstance(tags, list) else [tags])
            return json_response({"msg": "标签已添加"})
        except Exception as e:
            return error_response(f"打标签失败: {e}")

    async def backup_db(self):
        """备份图库数据库（gallery.db），返回 base64 便于前端触发下载。

        数据库文件通常不大（SQLite 元数据），走 bridge 拉取 base64 后在前端
        构造 Blob 下载，规避 AstrBot 裸路径需登录 token 的问题。
        """
        g = self._gallery()
        if g is None:
            return error_response("图库未启用或初始化失败")
        try:
            db_path = getattr(g, "db_path", None)
            if not db_path or not Path(db_path).exists():
                return error_response("图库数据库文件不存在")
            raw = await asyncio.to_thread(Path(db_path).read_bytes)
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
                    buf = io.BytesIO()
                    fmt = "JPEG" if mime == "image/jpeg" else "PNG"
                    if im.mode in ("RGBA", "LA", "P"):
                        im = im.convert("RGBA")
                    im.save(buf, format=fmt, optimize=True)
                    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
                    cmime = "image/jpeg" if fmt == "JPEG" else "image/png"
                    return f"data:{cmime};base64,{encoded}"
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


def register_web_api(plugin) -> None:
    """在插件 initialize 时调用，注册所有控制台路由。"""
    from astrbot.api import logger as _log
    api = WebUIApi(plugin)
    ctx = plugin.context
    # 路由必须含插件名：/<plugin_name>/<endpoint>。
    # 对齐 AstrBot 官方《插件 Pages》约定：后端注册带插件名前缀、
    # 不带 /page；前端 bridge endpoint 写相对路径（不带插件名、不带 /page），
    # 由 Dashboard 自动转发到 /api/v1/plugins/extensions/<plugin_name>/<endpoint>。
    prefix = f"/{PLUGIN_NAME}"

    routes = [
        (f"{prefix}/schema", api.get_schema, ["GET"], "读取配置 schema"),
        (f"{prefix}/config", api.get_config, ["GET"], "读取控制台配置"),
        (f"{prefix}/config", api.save_config, ["POST"], "保存控制台配置"),
        (f"{prefix}/logs", api.get_logs, ["GET"], "读取控制台日志"),
        (f"{prefix}/records", api.get_records, ["GET"], "读取出图记录"),
        (f"{prefix}/gallery/stats", api.gallery_stats, ["GET"], "图库统计"),
        (f"{prefix}/gallery/search", api.gallery_search, ["GET"], "图库检索"),
        (f"{prefix}/gallery/thumb", api.gallery_thumb, ["GET"], "图库缩略图"),
        (f"{prefix}/gallery/image", api.gallery_image, ["GET"], "图库图片"),
        (f"{prefix}/gallery/star", api.gallery_star, ["POST"], "图库收藏"),
        (f"{prefix}/gallery/delete", api.gallery_delete, ["POST"], "图库删除(移入回收站)"),
        (f"{prefix}/gallery/trash", api.gallery_trash, ["GET"], "图库回收站"),
        (f"{prefix}/gallery/restore", api.gallery_restore, ["POST"], "图库恢复"),
        (f"{prefix}/gallery/purge", api.gallery_purge, ["POST"], "图库彻底删除"),
        (f"{prefix}/gallery/tags", api.gallery_tags, ["POST"], "图库打标签"),
        (f"{prefix}/gallery/backup", api.backup_db, ["GET"], "备份图库数据库"),
        (f"{prefix}/stats/ranking", api.stats_ranking, ["GET"], "用户生图排行"),
        (f"{prefix}/stats/trend", api.stats_trend, ["GET"], "生图小时趋势"),
        (f"{prefix}/quota/users", api.quota_users, ["GET"], "生图限额用户列表"),
        (f"{prefix}/quota/config", api.quota_save_config, ["POST"], "生图限额配置保存"),
        (f"{prefix}/quota/save_global", api.quota_save_global, ["POST"], "生图全局限额保存"),
        (f"{prefix}/quota/reset", api.quota_reset, ["POST"], "生图次数重置"),
        (f"{prefix}/lora/fetch", api.lora_fetch, ["POST"], "C站 LoRA 抓取"),
        (f"{prefix}/lora/upload_image", api.lora_upload_image, ["POST"], "LoRA 封面图上传"),
        (f"{prefix}/lora/image", api.lora_image, ["GET"], "LoRA 封面图读取"),
        (f"{prefix}/translate/test", api.translate_test, ["POST"], "翻译调试（测试三种翻译模式）"),
    ]
    registered = []
    for path, handler, methods, desc in routes:
        try:
            ctx.register_web_api(path, _log_request(handler, desc), methods, desc)
            registered.append(path)
        except Exception as e:
            _log.warning(f"[WebUI] 注册路由失败 {path}: {e}")
    _log.info(f"[WebUI] 已注册控制台路由 {len(registered)} 个: {registered}")
