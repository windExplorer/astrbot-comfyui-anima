"""Anima 控制台 WebUI 后端 API。

通过 AstrBot 的 context.register_web_api 注册路由，配合 pages/anima-console/
下的前端页面使用。

重要（已踩坑）：AstrBot 在调用 web_api handler 时，只通过 path_params（路由
占位符）传参，并把 Quart 全局 request 绑定到当前上下文（见 AstrBot 源码
astrbot/dashboard/asgi_runtime.py 的 call_request_view / bind_quart_request_context）。
因此：
1) handler 签名里**不能**声明 request 参数——否则会 TypeError 导致接口 500；
   需要请求信息时用 `from quart import request` 的全局对象（request.args / get_json）。
2) 路由前缀写相对路径（如 /page/config），不含插件名；宿主会按插件名前缀匹配。
   参考伴侣插件 astrbot_plugin_private_companion 的 page_api 全部用相对路径。

功能：
- /schema          读取插件配置 schema（_conf_schema.json），用于前端结构化渲染
- /config          读取/保存插件配置
- /logs            读取内存日志环形缓冲（由 main.py 安装的 handler 填充）
- /gallery/stats   图库统计
- /gallery/search  图库检索（每条返回 data_url 缩略图）
- /gallery/star    收藏/取消收藏
- /gallery/delete  删除
- /gallery/tags    为某图添加标签
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import time
from collections import deque
from pathlib import Path

from starlette.responses import JSONResponse, Response

# AstrBot 在 Quart test_request_context 中执行 handler，全局 request 可用。
try:
    from quart import request as qrequest
except Exception:  # pragma: no cover
    qrequest = None

# 内存日志环形缓冲（main.py 的日志 handler 会写入这里）
LOG_BUFFER: "deque[str]" = deque(maxlen=2000)


def _ok(data=None, msg="ok"):
    return JSONResponse({"status": "ok", "msg": msg, "data": data})


def _err(msg="error", code=400, data=None):
    return JSONResponse({"status": "error", "msg": msg, "data": data}, status_code=code)


class WebUIApi:
    """封装控制台后端逻辑，避免污染 main.py。"""

    def __init__(self, plugin):
        # plugin 即 ComfyUIAnima 实例
        self.plugin = plugin

    # -------------------------------------------------------------- #
    # 配置
    # -------------------------------------------------------------- #
    async def get_config(self) -> Response:
        try:
            cfg = self.plugin.config
            # AstrBot 的 Config 对象本身是可映射的，转 dict 后序列化
            try:
                safe = dict(cfg)
            except Exception:
                safe = json.loads(json.dumps(cfg, default=lambda o: str(o)))
            return _ok(safe)
        except Exception as e:
            return _err(f"读取配置失败: {e}")

    async def get_schema(self) -> Response:
        try:
            schema_path = Path(__file__).resolve().parent / "_conf_schema.json"
            if not schema_path.exists():
                return _err("找不到 _conf_schema.json")
            raw = schema_path.read_text(encoding="utf-8")
            schema = json.loads(raw)
            return _ok(schema)
        except Exception as e:
            return _err(f"读取配置 schema 失败: {e}")

    async def save_config(self) -> Response:
        try:
            body = await qrequest.get_json(silent=True) if qrequest else {}
            if not isinstance(body, dict):
                body = {}
            new_cfg = body.get("config")
            if not isinstance(new_cfg, dict):
                return _err("config 必须是对象")
            # 安全合并：仅覆盖顶层键，保留未提交键
            cfg = self.plugin.config
            for k, v in new_cfg.items():
                try:
                    cfg[k] = v
                except Exception as e:
                    return _err(f"写入配置键 {k} 失败: {e}")
            try:
                cfg.save_config()
            except Exception as e:
                return _err(f"保存配置失败（已写入内存）: {e}")
            return _ok(msg="配置已保存")
        except Exception as e:
            return _err(f"保存配置失败: {e}")

    # -------------------------------------------------------------- #
    # 日志
    # -------------------------------------------------------------- #
    async def get_logs(self) -> Response:
        try:
            lines = list(LOG_BUFFER)
            # 支持前端按行数截取（默认全部 2000 行）
            try:
                n = int(qrequest.args.get("n", "2000")) if qrequest else 2000
            except Exception:
                n = 2000
            if n > 0:
                lines = lines[-n:]
            return _ok({"lines": lines, "total": len(LOG_BUFFER)})
        except Exception as e:
            return _err(f"读取日志失败: {e}")

    # -------------------------------------------------------------- #
    # 图库
    # -------------------------------------------------------------- #
    def _gallery(self):
        return getattr(self.plugin, "gallery", None)

    async def gallery_stats(self) -> Response:
        g = self._gallery()
        if g is None:
            return _err("图库未启用或初始化失败")
        try:
            return _ok(g.stats())
        except Exception as e:
            return _err(f"统计失败: {e}")

    async def gallery_search(self) -> Response:
        g = self._gallery()
        if g is None:
            return _err("图库未启用或初始化失败")
        try:
            kw = (qrequest.args.get("keyword", "") if qrequest else "")
            stype = (qrequest.args.get("type", "") or None) if qrequest else None
            if stype in ("", "all"):
                stype = None
            starred = (qrequest.args.get("starred", "0") == "1") if qrequest else False
            try:
                limit = int(qrequest.args.get("limit", "40")) if qrequest else 40
            except Exception:
                limit = 40
            try:
                offset = int(qrequest.args.get("offset", "0")) if qrequest else 0
            except Exception:
                offset = 0
            rows = g.search(keyword=kw, type=stype, starred_only=starred, limit=limit, offset=offset)
            # 列表中直接返回 data_url 缩略图（前端 img.src 用），避免依赖外部路径
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
            return _ok(rows)
        except Exception as e:
            return _err(f"检索失败: {e}")

    async def gallery_image(self) -> Response:
        g = self._gallery()
        if g is None:
            return _err("图库未启用或初始化失败")
        sha = (qrequest.args.get("sha", "") if qrequest else "")
        if not sha:
            return _err("缺少 sha 参数")
        try:
            path = g.path_of(sha)
        except Exception as e:
            return _err(f"路径解析失败: {e}")
        if not path or not Path(path).exists():
            return _err("图片不存在", code=404)
        # 对齐伴侣插件：图片以 data_url（base64）形式放入 JSON 返回，
        # 由 AstrBot bridge 正常解包，前端 img.src = data_url 直接渲染。
        try:
            raw = await asyncio.to_thread(Path(path).read_bytes)
        except Exception as e:
            return _err(f"读取图片失败: {e}")
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        encoded = base64.b64encode(raw).decode("ascii")
        return _ok({"data_url": f"data:{mime};base64,{encoded}", "mime": mime})

    async def gallery_star(self) -> Response:
        g = self._gallery()
        if g is None:
            return _err("图库未启用或初始化失败")
        try:
            payload = await _read_json()
            sha = payload.get("sha", "")
            on = 1 if payload.get("on", True) else 0
            if not sha:
                return _err("缺少 sha")
            ok = g.star(sha, on=on)
            return _ok(msg="已更新收藏" if ok else "未找到该图")
        except Exception as e:
            return _err(f"操作失败: {e}")

    async def gallery_delete(self) -> Response:
        g = self._gallery()
        if g is None:
            return _err("图库未启用或初始化失败")
        try:
            payload = await _read_json()
            sha = payload.get("sha", "")
            if not sha:
                return _err("缺少 sha")
            ok = g.delete(sha)
            return _ok(msg="已删除" if ok else "未找到该图")
        except Exception as e:
            return _err(f"删除失败: {e}")

    async def gallery_tags(self) -> Response:
        g = self._gallery()
        if g is None:
            return _err("图库未启用或初始化失败")
        try:
            payload = await _read_json()
            sha = payload.get("sha", "")
            tags = payload.get("tags", [])
            if not sha or not tags:
                return _err("缺少 sha 或 tags")
            g.add_tags(sha, tags if isinstance(tags, list) else [tags])
            return _ok(msg="标签已添加")
        except Exception as e:
            return _err(f"打标签失败: {e}")


async def _read_json():
    """从全局 quart request 读取 JSON body（AstrBot 不向 handler 传 request 参数）。"""
    if not qrequest:
        return {}
    try:
        return await qrequest.get_json(silent=True) or {}
    except Exception:
        return {}


def register_web_api(plugin) -> None:
    """在插件 initialize 时调用，注册所有控制台路由。"""
    from astrbot.api import logger as _log
    api = WebUIApi(plugin)
    ctx = plugin.context
    # 路由写相对路径（不含插件名）。参考伴侣插件 astrbot_plugin_private_companion
    # 的 page_api：其路由全部是 /overview、/config/... 这类相对路径且能正常工作，
    # 说明 AstrBot 会按插件名前缀匹配。前端 bridge 的 endpoint 传 "page/config"，
    # 宿主自动拼 /api/v1/plugins/extensions/<plugin_name>/ 前缀。
    prefix = "/page"

    routes = [
        (f"{prefix}/schema", api.get_schema, ["GET"], "读取配置 schema"),
        (f"{prefix}/config", api.get_config, ["GET"], "读取控制台配置"),
        (f"{prefix}/config", api.save_config, ["POST"], "保存控制台配置"),
        (f"{prefix}/logs", api.get_logs, ["GET"], "读取控制台日志"),
        (f"{prefix}/gallery/stats", api.gallery_stats, ["GET"], "图库统计"),
        (f"{prefix}/gallery/search", api.gallery_search, ["GET"], "图库检索"),
        (f"{prefix}/gallery/image", api.gallery_image, ["GET"], "图库图片"),
        (f"{prefix}/gallery/star", api.gallery_star, ["POST"], "图库收藏"),
        (f"{prefix}/gallery/delete", api.gallery_delete, ["POST"], "图库删除"),
        (f"{prefix}/gallery/tags", api.gallery_tags, ["POST"], "图库打标签"),
    ]
    registered = []
    for path, handler, methods, desc in routes:
        try:
            ctx.register_web_api(path, handler, methods, desc)
            registered.append(path)
        except Exception as e:
            _log.warning(f"[WebUI] 注册路由失败 {path}: {e}")
    _log.info(f"[WebUI] 已注册控制台路由 {len(registered)} 个: {registered}")
