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
import time
from collections import deque
from functools import wraps
from pathlib import Path

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
            rows = self.plugin.gallery.recent_records(limit=300, only_failed=only_failed)
            # 把裸路径 thumb_url 替换为 Pillow 压缩的小尺寸 data URL（浏览器 <img> 直连），
            # 规避 AstrBot 插件 API 裸路径 404/401 与内联整图 base64 超时的问题。
            for r in rows:
                sha = (r.get("sha256") or "").strip()
                if r.get("ext") != "fail" and sha:
                    p = None
                    try:
                        p = self.plugin.gallery.path_of(sha)
                    except Exception:
                        p = None
                    r["thumb_url"] = await asyncio.to_thread(_thumb_data_url, p) if p else ""
                else:
                    r["thumb_url"] = ""
                r["data_url"] = None
            return json_response({"records": rows, "total": len(rows)})
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
                limit = request.query.get("limit", 40, type=int)
            except Exception:
                limit = 40
            try:
                offset = request.query.get("offset", 0, type=int)
            except Exception:
                offset = 0
            rows = g.search(keyword=kw, type=stype, starred_only=starred,
                            trash=trash, limit=limit, offset=offset)
            # 返回 Pillow 压缩的小尺寸缩略图 data URL（前端 <img src> 直连渲染）：
            # AstrBot 插件 API 需登录 token，裸路径直连会 404/401；内联整图 base64
            # 又会因一次几十张原图导致超时。缩略图体积小且不走路由，两者都规避。
            for r in rows:
                sha = r.get("sha256", "")
                if sha:
                    try:
                        p = g.path_of(sha)
                    except Exception:
                        p = None
                    r["thumb"] = await asyncio.to_thread(_thumb_data_url, p) if p else ""
                else:
                    r["thumb"] = ""
            return json_response(rows)
        except Exception as e:
            return error_response(f"检索失败: {e}")

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
        (f"{prefix}/gallery/image", api.gallery_image, ["GET"], "图库图片"),
        (f"{prefix}/gallery/star", api.gallery_star, ["POST"], "图库收藏"),
        (f"{prefix}/gallery/delete", api.gallery_delete, ["POST"], "图库删除(移入回收站)"),
        (f"{prefix}/gallery/trash", api.gallery_trash, ["GET"], "图库回收站"),
        (f"{prefix}/gallery/restore", api.gallery_restore, ["POST"], "图库恢复"),
        (f"{prefix}/gallery/purge", api.gallery_purge, ["POST"], "图库彻底删除"),
        (f"{prefix}/gallery/tags", api.gallery_tags, ["POST"], "图库打标签"),
    ]
    registered = []
    for path, handler, methods, desc in routes:
        try:
            ctx.register_web_api(path, _log_request(handler, desc), methods, desc)
            registered.append(path)
        except Exception as e:
            _log.warning(f"[WebUI] 注册路由失败 {path}: {e}")
    _log.info(f"[WebUI] 已注册控制台路由 {len(registered)} 个: {registered}")
