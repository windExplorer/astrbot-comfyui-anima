"""独立的 WebUI 服务（standalone），与 AstrBot 内嵌页共存。

背景：AstrBot 内嵌页依赖 context.register_web_api 挂载路由，常因插件未重载/
路由未注册而出现「接口 404 / 6s 超时」。本模块用 aiohttp 启动一个独立端口的
HTTP 服务，直接提供静态前端 + 全部后端 API（复用插件的存储层：gallery/quota/
oplog/token_store），浏览器访问 http://host:port 即可，绕开 AstrBot 路由问题。

设计：
- 用 aiohttp.web.AppRunner 非阻塞启动，与 AstrBot 事件循环共存；
  插件 terminate 时调用 stop() 优雅关闭。
- 简单访问口令（token）：页面首次访问时前端弹窗输入，存 localStorage；
  所有 /api/* 请求需带 ?token= 或 Authorization: Bearer <token>。
- 静态页根目录为 pages/anima-console-vue/（构建产物）。
- 所有 API 返回 JSON：成功 {"success": true, "data": ...}；失败
  {"success": false, "error": "..."}，与前端 bridge.normalizeResponse 对齐。
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import time
from pathlib import Path

import aiohttp
from aiohttp import web

try:
    from astrbot.api import logger as _log
except ImportError:  # pragma: no cover - 非 AstrBot 环境
    import logging
    _log = logging.getLogger("standalone_webui")

try:
    from . import webui_api
except ImportError:
    import webui_api

# 静态页目录（相对本文件：pages/anima-console-vue/）
PAGES_DIR = Path(__file__).resolve().parent / "pages" / "anima-console-vue"

# 成功/失败响应包装（与前端 bridge.normalizeResponse 兼容）
def _ok(data) -> web.Response:
    return web.json_response({"success": True, "data": data})


def _err(msg: str, status: int = 200) -> web.Response:
    return web.json_response({"success": False, "error": str(msg)}, status=status)


class StandaloneWebUI:
    """独立 WebUI 服务：aiohttp 应用 + 启停 + 鉴权 + 路由。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        # 复用 WebUIApi 的 lora_fetch / lora_upload_image / lora_image / translate_test
        # （这些方法引用 webui_api 模块级 `request`，需用适配器 + 串行锁避免全局竞态）
        self._api = None
        try:
            self._api = webui_api.WebUIApi(plugin)
        except Exception:
            self._api = None
        self._request_lock = asyncio.Lock()
        self._saved_request = getattr(webui_api, "request", None)

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def _cfg(self, key: str, default=None):
        try:
            v = (self.plugin._cfg("webui_standalone", {}) or {}).get(key, default)
        except Exception:
            v = default
        return v if v is not None else default

    @property
    def enabled(self) -> bool:
        if bool(self._cfg("enabled", False)):
            return True
        # 分享站由独立 WebUI 服务承载（公开、同域直连），开启分享站即视为启用
        try:
            if self.plugin._cfg("share_webui", {}).get("enabled", True):
                return True
        except Exception:
            pass
        return False

    @property
    def port(self) -> int:
        try:
            return int(self._cfg("port", 8848) or 8848)
        except (TypeError, ValueError):
            return 8848

    @property
    def host(self) -> str:
        """监听地址。默认 127.0.0.1（仅本机）；改 0.0.0.0 允许局域网访问。"""
        h = str(self._cfg("host", "") or "").strip().lower()
        if not h:
            return "127.0.0.1"
        return h

    @property
    def token(self) -> str:
        return str(self._cfg("token", "") or "").strip()

    def _check_token(self, request: web.Request) -> bool:
        t = self.token
        if not t:
            return True  # 未配置口令则不鉴权
        # 优先 Authorization: Bearer xxx
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            if auth[7:].strip() == t:
                return True
        # 其次 ?token=xxx
        q = request.query.get("token", "")
        if q and q == t:
            return True
        return False

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        if not self.enabled:
            return
        if self._runner is not None:
            return
        app = web.Application()
        self._setup_routes(app)
        try:
            self._runner = web.AppRunner(app)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self.host, self.port)
            await self._site.start()
            from astrbot.api import logger as _log
            if self.host in ("0.0.0.0", "::"):
                _log.info(
                    f"[独立WebUI] 已启动（监听 {self.host}）http://服务器IP:{self.port} "
                    f"(token鉴权={'开启' if self.token else '关闭'}"
                    f"{'，注意：未设 token 时局域网任何设备均可访问' if not self.token else ''})"
                )
            else:
                _log.info(
                    f"[独立WebUI] 已启动 http://127.0.0.1:{self.port} "
                    f"(token鉴权={'开启' if self.token else '关闭'})"
                )
        except Exception as e:
            from astrbot.api import logger as _log
            _log.warning(f"[独立WebUI] 启动失败: {e}")
            self._runner = None
            self._site = None

    async def stop(self) -> None:
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                pass
        self._runner = None
        self._site = None
        if self._task is not None:
            try:
                self._task.cancel()
            except Exception:
                pass
            self._task = None

    @property
    def is_running(self) -> bool:
        return self._site is not None

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #
    def _setup_routes(self, app: web.Application) -> None:
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/s/{token}", self._handle_share_landing)
        app.router.add_get("/api/ping", self._handle_ping)

        # 分享管理（admin 鉴权，与分享站临时令牌链路解耦）：
        # 必须注册在 /api/share/{tail:.*} 之前，否则会被分享站处理器截获，
        # 因缺分享令牌而误报「链接无效或已过期」。
        app.router.add_route("*", "/api/share/tokens", self._handle_api)
        app.router.add_route("*", "/api/share/token/invalidate", self._handle_api)

        # 分享站（公开，分享令牌鉴权，与独立服务 admin token 解耦）
        app.router.add_route("*", "/api/share/{tail:.*}", self._handle_share_api)
        app.router.add_get("/share/img/{sha}", self._handle_share_img)
        app.router.add_get("/share/img/{sha}/thumb", self._handle_share_img)
        app.router.add_get("/share/avatar/{user_id}", self._handle_share_avatar)

        # 所有业务 API（前缀 /api/*），走统一鉴权 + 分发
        for method in ("GET", "POST"):
            app.router.add_route(method, "/api/{tail:.*}", self._handle_api)
        # 图库图片直链 /img/{sha}：<img> 直接加载原始图片（带鉴权），避免 base64 内联。
        # 必须注册在 /{path:.+} 之前，否则会被静态路由吞掉。
        app.router.add_get("/img/{sha}", self._handle_img)
        app.router.add_get("/img/{sha}/thumb", self._handle_img)
        # LoRA/工作流封面文件直链：独立模式下 <img> 直接加载，避免 base64 内联。
        # 必须注册在静态路由之前。带 token 鉴权 + 防目录穿越。
        app.router.add_get("/lora/file", self._handle_lora_file)
        app.router.add_get("/workflow/file", self._handle_lora_file)
        # 静态资源：index.html 之外的 js/css/图等
        app.router.add_get("/{path:.+}", self._handle_static)

    # 鉴权中间件（内联在每个 handler 前）
    def _authed(self, request: web.Request) -> web.Response | None:
        if not self._check_token(request):
            return _err("未授权：请填写访问口令", status=401)
        return None

    async def _handle_ping(self, request: web.Request) -> web.Response:
        # 关键：ping 也做 token 鉴权。前端守卫用它探测认证状态——若 ping 不鉴权，
        # 守卫会误判「已认证」而放行控制台，导致未认证仍进入控制台（其他 API 才 401）。
        denied = self._authed(request)
        if denied is not None:
            return denied
        return _ok({"pong": True, "ts": time.time()})

    async def _handle_img(self, request: web.Request) -> web.Response:
        """图库图片直链：/img/{sha} 或 /img/{sha}/thumb。

        返回原始图片二进制（支持 ?size= 缩略），<img> 直接加载 + 浏览器缓存，
        避免 base64 内联的体积/内存开销。带 token 鉴权（?token= 或 Authorization）。
        """
        denied = self._authed(request)
        if denied is not None:
            return denied
        sha = request.match_info.get("sha", "") or ""
        if not sha:
            return _err("缺少 sha", status=400)
        g = self.plugin.gallery
        if g is None:
            return _err("图库未启用", status=500)
        try:
            p = g.path_of(sha)
        except Exception as e:
            return _err(f"路径解析失败: {e}", status=500)
        if not p or not Path(p).exists():
            return _err("图片不存在", status=404)
        # 缩略：仅当显式请求缩略（路径含 /thumb）或 ?size= 显式给了较小值时才缩放；
        # 大图直连 /img/{sha}（无 /thumb、无 size 参数）直接返回原图二进制，不缩放、保真且更快。
        want_thumb = request.match_info.get("sha") is not None and "/thumb" in request.path
        explicit_size = request.query.get("size")
        try:
            size = self._qint(request, "size", 0)
        except Exception:
            size = 0
        if want_thumb or (explicit_size is not None and size < 200000 and size > 0):
            try:
                data_url = await asyncio.to_thread(self._thumb_cached, p, size or 300)
                if data_url and data_url.startswith("data:"):
                    # data:image/jpeg;base64,xxx → 解码为字节
                    header, _, b64 = data_url.partition(",")
                    cmime = header.replace("data:", "").split(";")[0] or "image/jpeg"
                    try:
                        raw = base64.b64decode(b64)
                    except Exception:
                        raw = Path(p).read_bytes()
                        cmime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
                    return web.Response(body=raw, content_type=cmime,
                                        headers={"Cache-Control": "public, max-age=31536000"})
            except Exception:
                pass
        # 原图直返（含大图直连）：读字节后用 web.Response 返回，避免 web.FileResponse
        # 在特定环境（Windows 路径/文件锁/aiohttp 版本）下抛 500；不缩放、保真且更快。
        try:
            raw = await asyncio.to_thread(Path(p).read_bytes)
        except Exception as e:
            return _err(f"读取图片失败: {e}", status=500)
        ctype = mimetypes.guess_type(str(p))[0] or "image/jpeg"
        return web.Response(body=raw, content_type=ctype,
                            headers={"Cache-Control": "public, max-age=31536000"})

    async def _handle_lora_file(self, request: web.Request) -> web.Response:
        """LoRA/工作流封面文件直链：/lora/file?name=xxx 或 /workflow/file?name=xxx。

        独立模式下 <img> 直接加载，避免 base64 内联。带 token 鉴权 + 防目录穿越。
        """
        denied = self._authed(request)
        if denied is not None:
            return denied
        name = (request.query.get("name", "") or "").strip()
        if not name:
            return _err("缺少 name 参数", status=400)
        # 仅允许纯文件名，防目录穿越
        if "/" in name or "\\" in name or ".." in name:
            return _err("非法文件名", status=400)
        assets_dir = getattr(self.plugin, "lora_assets_dir", None)
        if assets_dir is None:
            assets_dir = (getattr(self.plugin, "data_dir", None) or Path(os.getcwd())) / "lora_assets"
        path = Path(assets_dir) / name
        if not path.exists() or not path.is_file():
            return _err("图片不存在", status=404)
        try:
            raw = await asyncio.to_thread(path.read_bytes)
        except Exception as e:
            return _err(f"读取图片失败: {e}", status=500)
        ctype = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        return web.Response(body=raw, content_type=ctype,
                            headers={"Cache-Control": "public, max-age=31536000"})

    async def _handle_index(self, request: web.Request) -> web.Response:
        idx = PAGES_DIR / "index.html"
        if not idx.exists():
            return _err("未找到前端页面（请先构建 pages/anima-console-vue）", status=404)
        text = idx.read_text(encoding="utf-8")
        # 注入独立模式标记：前端据此 100% 判定当前页面来自独立服务（区别于 AstrBot 内嵌页）
        marker = "<script>window.__ANIMA_STANDALONE__=true;</script>"
        if "__ANIMA_STANDALONE__" not in text:
            text = text.replace("<head>", "<head>\n    " + marker, 1)
        # index.html 不缓存：确保每次拿到最新引用的 hash 资源，避免升级后浏览器用旧 JS
        return web.Response(text=text, content_type="text/html", charset="utf-8",
                            headers={"Cache-Control": "no-cache"})

    async def _handle_share_landing(self, request: web.Request) -> web.Response:
        """分享链接引导页：/s/{token}。

        分享链接的 token 放在 URL 路径里（扫码工具几乎不会丢）。此页由后端直接渲染
        （极简 HTML，不依赖 Vue/无缓存），内嵌 JS 从路径取 token → 先调后端校验 →
        有效则用 location.replace 由 JS 拼出标准分享站地址（#/share?token=...）跳转。
        这样 token 的最终传递完全由 JS 控制，绕开扫码工具对 URL query 的处理和旧 JS
        缓存不认新格式的问题。
        """
        token = (request.match_info.get("token") or "").strip()
        _esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        token_js = _esc(token)
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>萌绘图库</title>
<style>
  body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#fff6f9;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;}}
  .card{{background:#fff;border:1px solid #ffe3ec;border-radius:16px;padding:40px 48px;box-shadow:0 8px 30px rgba(255,143,179,.18);text-align:center;max-width:340px;}}
  .emoji{{font-size:42px;}}
  .title{{margin:12px 0 8px;font-size:18px;font-weight:700;color:#3a2a33;}}
  .sub{{font-size:13px;color:#9a7a88;line-height:1.6;}}
</style>
</head>
<body>
<div class="card">
  <div class="emoji">🎨</div>
  <div class="title" id="t">正在进入萌绘图库…</div>
  <div class="sub" id="s">请稍候</div>
</div>
<script>
(function(){{
  var token = "{token_js}";
  var title = document.getElementById("t");
  var sub = document.getElementById("s");
  function fail(){{
    title.textContent = "链接已失效";
    sub.innerHTML = "该分享链接已过期或不存在，请联系分享者重新分享。";
  }}
  if(!token){{ fail(); return; }}
  fetch("/api/share/me?token=" + encodeURIComponent(token))
    .then(function(r){{ return r.json(); }})
    .then(function(j){{
      if(j && j.success){{
        // 由 JS 拼出标准分享站地址并跳转：token 放在 hash 内 query，兼容所有版本前端
        location.replace("/index.html#/share?share_t=" + encodeURIComponent(token));
      }} else {{
        fail();
      }}
    }})
    .catch(fail);
}})();
</script>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html", charset="utf-8",
                            headers={"Cache-Control": "no-cache"})

    async def _handle_static(self, request: web.Request) -> web.Response:
        path = request.match_info.get("path", "")
        if not path or path.startswith("api/"):
            return _err("Not Found", status=404)
        # 安全：只允许相对路径、禁止穿越
        clean = path.lstrip("/").replace("\\", "/")
        if ".." in clean.split("/"):
            return _err("Bad Request", status=400)
        fp = (PAGES_DIR / clean).resolve()
        if not str(fp).startswith(str(PAGES_DIR.resolve())):
            return _err("Forbidden", status=403)
        if not fp.exists() or not fp.is_file():
            # favicon 缺失时返回空，避免浏览器控制台 404 噪音
            if clean in ("favicon.ico", "favicon.png"):
                return web.Response(body=b"", status=204, content_type="image/x-icon")
            return _err("Not Found", status=404)
        ctype = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        # 手动读字节返回，避免 aiohttp FileResponse 在部分环境（Windows/容器）下 500
        try:
            data = await asyncio.to_thread(fp.read_bytes)
        except Exception as e:
            return _err(f"读取静态文件失败: {e}", status=500)
        return web.Response(body=data, content_type=ctype,
                            headers={"Cache-Control": "public, max-age=31536000"})

    # ------------------------------------------------------------------ #
    # API 分发
    # ------------------------------------------------------------------ #
    async def _handle_api(self, request: web.Request) -> web.Response:
        denied = self._authed(request)
        if denied is not None:
            return denied
        # 优先取 {tail} 分组（/api/{tail:.*}）；字面量路由（如显式注册的
        # /api/share/tokens）无该分组时，从真实路径反推，避免 path 退化成 "/"
        match_tail = request.match_info.get("tail", None)
        if match_tail is None:
            full = request.rel_url.path
            if full.startswith("/api"):
                full = full[4:]
            match_tail = full
        path = "/" + match_tail.strip("/")
        method = request.method.upper()
        try:
            return await self._dispatch(path, method, request)
        except Exception as e:
            return _err(f"处理失败: {e}")

    def _q(self, request: web.Request, key: str, default=""):
        return request.query.get(key, default)

    def _qint(self, request: web.Request, key: str, default: int):
        try:
            return int(request.query.get(key, default))
        except (TypeError, ValueError):
            return default

    async def _api_story(self, path: str, request: web.Request) -> web.Response:
        """独立版剧情档案接口：复用 WebUIApi 实现（经 aiohttp 适配器 + 串行锁）。"""
        if self._api is None:
            return _err("剧情模块未初始化")
        sub = path[len("/story/"):].split("?", 1)[0].rstrip("/")
        method = request.method.upper()
        name = None
        if sub == "sessions" and method == "GET":
            name = "story_sessions"
        elif sub == "stats" and method == "GET":
            name = "story_stats"
        elif sub == "session" and method == "GET":
            name = "story_session_detail"
        elif sub == "session" and method == "POST":
            name = "story_session_update"
        elif sub == "session/delete" and method == "POST":
            name = "story_session_delete"
        if name is None:
            return _err("Not Found: " + path, status=404)
        adapter = self._AioReqAdapter(request)

        def _ok_dict(payload):
            return {"__ok__": True, "data": payload}

        def _err_dict(msg):
            return {"__ok__": False, "error": str(msg)}

        async with self._request_lock:
            had_req = hasattr(webui_api, "request")
            prev_req = getattr(webui_api, "request", None)
            had_ok = hasattr(webui_api, "json_response")
            prev_ok = getattr(webui_api, "json_response", None)
            had_err = hasattr(webui_api, "error_response")
            prev_err = getattr(webui_api, "error_response", None)
            try:
                webui_api.request = adapter
                webui_api.json_response = _ok_dict
                webui_api.error_response = _err_dict
                result = await getattr(self._api, name)()
            finally:
                webui_api.request = prev_req if had_req else None
                webui_api.json_response = prev_ok if had_ok else None
                webui_api.error_response = prev_err if had_err else None
        if isinstance(result, dict) and result.get("__ok__") is not None:
            if result.get("__ok__"):
                return _ok(result["data"])
            return _err(result.get("error", "处理失败"))
        return _ok(result)

    async def _dispatch(self, path: str, method: str, request: web.Request) -> web.Response:
        g = self.plugin.gallery
        op = self.plugin.oplog
        q = self.plugin.quota
        tok = self.plugin.token_store

        # ---------- 基础 ----------
        if path == "/schema" and method == "GET":
            sp = Path(__file__).resolve().parent / "_conf_schema.json"
            if not sp.exists():
                return _err("找不到 _conf_schema.json")
            return _ok(json.loads(sp.read_text(encoding="utf-8")))
        if path == "/config" and method == "GET":
            return _ok(dict(self.plugin.config))
        if path == "/config" and method == "POST":
            body = await request.json() if request.body_exists else {}
            if not isinstance(body, dict):
                body = {}
            # 采样器参数读取（独立 WebUI 直接处理，不经过 webui_api.save_config）
            if body.get("_read_sampler"):
                _wn = (body.get("workflow_name") or "").strip()
                if not _wn:
                    return _err("缺少 workflow_name")
                try:
                    from . import workflow_builder
                except ImportError:
                    import workflow_builder
                prompt = None
                wdir = getattr(self.plugin, "workflow_dir", None)
                if wdir is not None:
                    p = Path(wdir) / _wn
                    if not p.suffix:
                        p = p.with_suffix(".json")
                    if p.is_file():
                        try:
                            prompt = json.loads(p.read_text(encoding="utf-8"))
                        except Exception:
                            prompt = None
                if not isinstance(prompt, dict):
                    return _err("未找到工作流文件或 JSON 无效")
                return _ok(workflow_builder.get_sampler_defaults(prompt))
            new_cfg = body.get("config")
            if not isinstance(new_cfg, dict):
                return _err("config 必须是对象")
            cfg = self.plugin.config
            for k, v in new_cfg.items():
                cfg[k] = v
            cfg.save_config()
            return _ok({"msg": "配置已保存"})

        # ---------- 日志 ----------
        if path == "/logs" and method == "GET":
            return await self._api_logs(request)
        if path == "/records" and method == "GET":
            if g is None:
                return _ok({"records": [], "total": 0})
            page = self._qint(request, "page", 1)
            size = min(self._qint(request, "size", 40), 200)
            only_failed = self._q(request, "failed", "0") == "1"
            kw = self._q(request, "keyword", "")
            rows = g.recent_records(limit=size, only_failed=only_failed,
                                    offset=(page - 1) * size, keyword=kw)
            total = g.count_records(only_failed=only_failed, keyword=kw)
            for r in rows:
                r["sha"] = r.get("sha256", "")
                r["thumb_url"] = ""
                r["data_url"] = None
            return _ok({"records": rows, "total": total, "page": page, "size": size})

        # ---------- 独立操作日志 ----------
        if path == "/oplog" and method == "GET":
            if op is None:
                return _ok({"records": [], "total": 0})
            page = self._qint(request, "page", 1)
            size = min(self._qint(request, "size", 40), 200)
            event = self._q(request, "event", "")
            kw = self._q(request, "keyword", "")
            user = self._q(request, "user", "")
            rows = op.query(event=event, keyword=kw, user=user,
                            limit=size, offset=(page - 1) * size)
            total = op.count(event=event, keyword=kw, user=user)
            return _ok({"records": rows, "total": total, "page": page, "size": size})

        # ---------- 分享管理 ----------
        if path.startswith("/share/"):
            return await self._api_share_admin(path, request, g)

        # ---------- 图库 ----------
        if path.startswith("/gallery/"):
            return await self._api_gallery(path, request, g)

        # ---------- 限额 ----------
        if path.startswith("/quota/"):
            return await self._api_quota(path, request, q)

        # ---------- token 用量 ----------
        if path.startswith("/token/"):
            return await self._api_token(path, request, tok)

        # ---------- LoRA / 封面 / C 站抓取 ----------
        if path.startswith("/lora/") or path == "/translate/test":
            return await self._api_lora_translate(path, request)

        # ---------- 统计 ----------
        if path.startswith("/stats/"):
            return await self._api_stats(path, request)

        # ---------- 剧情档案 ----------
        if path.startswith("/story/"):
            return await self._api_story(path, request)

        return _err("Not Found: " + path, status=404)

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    async def _api_logs(self, request: web.Request) -> web.Response:
        n = self._qint(request, "n", 2000)
        lines = []
        try:
            from .webui_api import LOG_BUFFER
        except Exception:
            LOG_BUFFER = None
        if LOG_BUFFER is not None:
            lines = list(LOG_BUFFER)
        if not lines:
            lp = Path(self.plugin.data_dir) / "webui.log"
            if lp.exists():
                raw = lp.read_text(encoding="utf-8", errors="ignore")
                lines = [ln for ln in raw.splitlines() if ln.strip()]
        if n > 0:
            lines = lines[-n:]
        return _ok({"lines": lines, "total": len(lines)})

    # ------------------------------------------------------------------ #
    # 分享管理（admin）
    # ------------------------------------------------------------------ #
    async def _api_share_admin(self, path: str, request: web.Request, g):
        if g is None:
            return _err("图库未启用或初始化失败")
        if path == "/share/tokens" and request.method == "GET":
            limit = min(self._qint(request, "limit", 200), 500)
            offset = max(0, self._qint(request, "offset", 0))
            rows = g.share_token_records(limit=limit, offset=offset)
            total = g.count_share_tokens()
            return _ok({"tokens": rows, "total": total})
        if path == "/share/token/invalidate" and request.method == "POST":
            body = await request.json() if request.body_exists else {}
            token = (body.get("token") or "") if isinstance(body, dict) else ""
            if not token:
                return _err("缺少 token", status=400)
            g.invalidate_share_token(token)
            self._oplog_add("share_token_invalidate", "作废分享链接", ref_sha="")
            return _ok({"msg": "已作废"})
        return _err("Not Found: " + path, status=404)

    # ------------------------------------------------------------------ #
    # 图库
    # ------------------------------------------------------------------ #
    async def _api_gallery(self, path: str, request: web.Request, g):
        if g is None:
            return _err("图库未启用或初始化失败")
        if path == "/gallery/stats":
            return _ok(g.stats())
        if path == "/gallery/search":
            kw = self._q(request, "keyword", "")
            stype = self._q(request, "type", "") or None
            if stype in ("", "all"):
                stype = None
            starred = self._q(request, "starred", "0") == "1"
            trash = self._q(request, "trash", "0") == "1"
            nsfw = self._q(request, "nsfw", "")
            tag = self._q(request, "tag", "")
            page = max(1, self._qint(request, "page", 1))
            size = min(self._qint(request, "size", 40), 200)
            offset = (page - 1) * size
            rows = g.search(keyword=kw, type=stype, starred_only=starred, trash=trash,
                            limit=size, offset=offset, nsfw=nsfw, tag=tag)
            total = g.count_search(keyword=kw, type=stype, starred_only=starred,
                                   trash=trash, nsfw=nsfw, tag=tag)
            for r in rows:
                r["sha"] = r.get("sha256", "")
                r.pop("thumb", None)
                r.pop("thumb_url", None)
            return _ok({"images": rows, "total": total, "page": page, "size": size})
        if path == "/gallery/thumb":
            sha = self._q(request, "sha", "")
            size = self._qint(request, "size", 300)
            p = g.path_of(sha)
            if not p or not Path(p).exists():
                return _err("图片不存在", status=404)
            data_url = await asyncio.to_thread(self._thumb_cached, p, size)
            return _ok({"sha": sha, "url": data_url or ""})
        if path == "/gallery/image":
            sha = self._q(request, "sha", "")
            want_meta = self._q(request, "meta", "0") == "1"
            p = g.path_of(sha)
            if not p or not Path(p).exists():
                return _err("图片不存在", status=404)
            if want_meta:
                # 只回元数据，绝不生成 data_url（大图走 /img/{sha} 直链，避免 base64 卡顿）
                meta = None
                try:
                    meta = g.get_by_sha(sha)
                except Exception:
                    meta = None
                return _ok({"data_url": None, "mime": None, "meta": meta})
            ctype = mimetypes.guess_type(str(p))[0] or "image/jpeg"
            return web.Response(body=await asyncio.to_thread(Path(p).read_bytes),
                                content_type=ctype,
                                headers={"Cache-Control": "public, max-age=31536000"})
        if path == "/gallery/star":
            body = await request.json() if request.body_exists else {}
            sha = (body.get("sha") or "") if isinstance(body, dict) else ""
            on = 1 if (body.get("on", True) if isinstance(body, dict) else True) else 0
            ok = g.star(sha, on=on)
            self._oplog_add("gallery_star", "图库收藏" if on else "图库取消收藏", ref_sha=sha)
            return _ok({"msg": "已更新收藏" if ok else "未找到该图"})
        if path == "/gallery/delete":
            body = await request.json() if request.body_exists else {}
            sha = (body.get("sha") or "") if isinstance(body, dict) else ""
            ok = g.delete(sha)
            if not ok:
                return _err("未找到该图（收藏图不可删除）")
            self._oplog_add("gallery_delete", "图库删除（移入回收站）", ref_sha=sha)
            return _ok({"msg": "已移入回收站"})
        if path == "/gallery/restore":
            body = await request.json() if request.body_exists else {}
            sha = (body.get("sha") or "") if isinstance(body, dict) else ""
            ok = g.restore(sha)
            self._oplog_add("gallery_restore", "图库恢复", ref_sha=sha)
            return _ok({"msg": "已恢复" if ok else "恢复失败"})
        if path == "/gallery/purge":
            body = await request.json() if request.body_exists else {}
            sha = (body.get("sha") or "") if isinstance(body, dict) else ""
            ok = g.purge(sha)
            self._oplog_add("gallery_purge", "图库彻底删除", ref_sha=sha)
            return _ok({"msg": "已彻底删除" if ok else "未找到该图"})
        if path == "/gallery/tags":
            body = await request.json() if request.body_exists else {}
            sha = (body.get("sha") or "") if isinstance(body, dict) else ""
            tags = body.get("tags", []) if isinstance(body, dict) else []
            action = str(body.get("action") or "add").strip().lower() if isinstance(body, dict) else "add"
            if not sha or not tags:
                return _err("缺少 sha 或 tags")
            tag_list = tags if isinstance(tags, list) else [tags]
            if action == "del":
                g.remove_tags(sha, tag_list)
                self._oplog_add("gallery_untag", f"图库删除标签：{','.join(tag_list)}", ref_sha=sha)
                return _ok({"msg": "标签已删除"})
            g.add_tags(sha, tag_list)
            self._oplog_add("gallery_tags", f"图库打标签：{','.join(tag_list)}", ref_sha=sha)
            return _ok({"msg": "标签已添加"})
        if path == "/gallery/check_nsfw":
            sha = self._q(request, "sha", "").strip()
            if not sha:
                return _err("缺少 sha")
            res = g.check_nsfw(sha)
            if res.get("available") is False:
                return _err(res.get("msg") or "检测不可用")
            return _ok({"nsfw": res.get("nsfw", False),
                        "nsfw_score": res.get("nsfw_score"),
                        "msg": res.get("msg", "检测完成")})
        if path == "/gallery/set_nsfw":
            body = await request.json() if request.body_exists else {}
            sha = (body.get("sha") or "") if isinstance(body, dict) else ""
            on = (body.get("on") if isinstance(body, dict) else None)
            if not sha:
                return _err("缺少 sha")
            if on is None:
                return _err("缺少 on(0/1)")
            ok = g.set_nsfw(sha, 1 if on else 0)
            msg = "已标记为 NSFW" if on else "已取消 NSFW"
            self._oplog_add("gallery_set_nsfw", msg, ref_sha=sha,
                            extra={"on": 1 if on else 0})
            return _ok({"msg": msg if ok else "未找到该图"})
        if path == "/gallery/set_blur":
            body = await request.json() if request.body_exists else {}
            sha = (body.get("sha") or "") if isinstance(body, dict) else ""
            on = (body.get("on") if isinstance(body, dict) else None)
            if not sha:
                return _err("缺少 sha")
            if on is None:
                ok = g.clear_nsfw_blur(sha)
                msg = "已恢复跟随全局模糊"
            else:
                ok = g.set_nsfw_blur(sha, 1 if on else 0)
                msg = "已设置模糊" if on else "已取消模糊"
            self._oplog_add("gallery_set_blur", msg, ref_sha=sha,
                            extra={"on": on})
            return _ok({"msg": msg if ok else "未找到该图"})
        if path == "/gallery/trash":
            rows = g.search(trash=True, limit=200, offset=0)
            for r in rows:
                sha = r.get("sha256", "")
                p = g.path_of(sha)
                if p and Path(p).exists():
                    raw = await asyncio.to_thread(Path(p).read_bytes)
                    mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
                    r["thumb"] = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
                else:
                    r["thumb"] = ""
            return _ok(rows)
        if path == "/gallery/scan_nsfw":
            only = self._q(request, "only", "1") != "0"
            res = g.scan_nsfw_start(only_unchecked=only)
            if res.get("last_err"):
                return _err(res["last_err"])
            return _ok(res)
        if path == "/gallery/scan_nsfw_progress":
            return _ok(g.scan_nsfw_progress())
        if path == "/gallery/backup":
            dbp = getattr(g, "db_path", None)
            if not dbp or not Path(dbp).exists():
                return _err("图库数据库文件不存在")
            raw = await asyncio.to_thread(Path(dbp).read_bytes)
            encoded = base64.b64encode(raw).decode("ascii")
            ts = time.strftime("%Y%m%d-%H%M%S")
            return _ok({"filename": f"gallery_backup_{ts}.db",
                        "data_url": f"application/octet-stream;base64,{encoded}",
                        "size_bytes": len(raw)})
        return _err("Not Found: " + path, status=404)

    # ------------------------------------------------------------------ #
    # 限额
    # ------------------------------------------------------------------ #
    async def _api_quota(self, path: str, request: web.Request, q):
        if q is None:
            return _err("生图限额未启用或初始化失败")
        if path == "/quota/users":
            # 与 AstrBot 内嵌模式(_api_quota_users)保持一致：返回 {global, users}
            # 否则前端 data.global / data.users 解包失败，限额页用户列表与全局配置空白
            global_cfg = {}
            try:
                global_cfg = self.plugin._draw_limit_cfg() or {}
            except Exception:
                global_cfg = {}
            return _ok({"global": global_cfg, "users": q.list_users()})
        if path == "/quota/reset":
            body = await request.json() if request.body_exists else {}
            user_id = (body.get("user_id") or "").strip() if isinstance(body, dict) else ""
            if not user_id:
                n = q.reset_all()
                self._oplog_add("quota_reset", f"重置全部用户限额（{n} 人）")
                return _ok({"ok": True, "reset_all": True, "count": n})
            ok = q.reset_user(user_id)
            self._oplog_add("quota_reset", f"重置用户限额：{user_id}", user_id=user_id)
            return _ok({"ok": ok, "reset_user": user_id})
        if path == "/quota/config":
            body = await request.json() if request.body_exists else {}
            if isinstance(body, dict):
                uid = (body.get("user_id") or "").strip()
                if uid:
                    q.set_user_config(uid,
                                      int(body.get("max_total", -1)),
                                      int(body.get("max_hour", -1)),
                                      int(body.get("max_day", -1)))
                    return _ok({"msg": "已保存"})
            return _err("缺少 user_id")
        if path == "/quota/save_global":
            # 全局限额存于插件 config 的 draw_limit，复用与 /config POST 一致的保存方式。
            try:
                body = await request.json() if request.body_exists else {}
                if not isinstance(body, dict):
                    body = {}
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
                cfg = self.plugin.config
                cfg["draw_limit"] = cur
                cfg.save_config()
                return _ok({"ok": True, "draw_limit": cur})
            except Exception as e:
                return _err(f"保存全局限额失败: {e}")
        return _err("Not Found: " + path, status=404)

    # ------------------------------------------------------------------ #
    # token 用量
    # ------------------------------------------------------------------ #
    async def _api_token(self, path: str, request: web.Request, tok):
        if tok is None:
            return _err("LLM token 统计未启用或初始化失败")
        if path == "/token/summary":
            days = self._qint(request, "days", 30)
            if days <= 0:
                days = -1
            else:
                days = max(1, min(days, 3650))
            scope = (self._q(request, "scope", "") or "").lower()
            user_id = self._q(request, "user_id", "")
            merge = self._q(request, "merge", "0") == "1"
            page = max(1, self._qint(request, "page", 1))
            page_size = max(1, min(self._qint(request, "page_size", 30), 200))
            merge_names = ["PrivateCompanion"] if merge else None
            _now = time.time()
            _lt = time.localtime(_now)
            _today0 = time.mktime((_lt.tm_year, _lt.tm_mon, _lt.tm_mday, 0, 0, 0, 0, 0, -1))

            def _hb(t: float) -> str:
                lt2 = time.localtime(t)
                return f"{lt2.tm_year:04d}-{lt2.tm_mon:02d}-{lt2.tm_mday:02d} {lt2.tm_hour:02d}:00"

            _start_bucket = None
            _end_bucket = None
            if scope == "today":
                _start_bucket = _hb(_today0)
                _end_bucket = _hb(_today0 + 86400)
            elif scope == "yesterday":
                _start_bucket = _hb(_today0 - 86400)
                _end_bucket = _hb(_today0)
            summary = tok.query_summary(user_id=user_id, days=max(days, 1),
                                        start_bucket=_start_bucket, end_bucket=_end_bucket)
            scenes = tok.list_scenes(days=max(days, 1), start_bucket=_start_bucket, end_bucket=_end_bucket)
            users = tok.list_users(days=max(days, 1), merge_alsoknown=merge_names,
                                   start_bucket=_start_bucket, end_bucket=_end_bucket)
            models = tok.list_models(days=max(days, 1), start_bucket=_start_bucket, end_bucket=_end_bucket)
            if scope == "today":
                daily = tok.list_daily(days=1)
            elif scope == "yesterday":
                daily = tok.list_daily(days=2, start_bucket=_start_bucket, end_bucket=_end_bucket)
            else:
                daily = tok.list_daily(days=days)
            if scope == "today":
                hourly = tok.list_hourly(since_day_start=True)
            elif scope == "yesterday":
                hourly = tok.list_hourly(start_ts=_today0 - 86400, end_ts=_today0)
            elif scope == "1":
                hourly = tok.list_hourly(hours=24)
            else:
                hourly = []
            detail_total = tok.count_detail(user_id=user_id, days=max(days, 1),
                                            start_bucket=_start_bucket, end_bucket=_end_bucket)
            detail = tok.list_detail(user_id=user_id, days=max(days, 1),
                                     offset=(page - 1) * page_size, limit=page_size,
                                     start_bucket=_start_bucket, end_bucket=_end_bucket)
            return _ok({
                "summary": summary, "scenes": scenes, "users": users, "models": models,
                "daily": daily, "hourly": hourly, "detail": detail,
                "detail_total": detail_total, "page": page, "page_size": page_size,
                "days": days, "merge": merge,
            })
        if path == "/token/reset":
            body = await request.json() if request.body_exists else {}
            uid = (body.get("user_id") or "").strip() if isinstance(body, dict) else ""
            if not uid:
                n = tok.reset_all()
                self._oplog_add("token_reset", "重置全部 LLM token 统计")
                return _ok({"ok": True, "reset_all": True, "count": n})
            ok = tok.reset_user(uid)
            self._oplog_add("token_reset", f"重置 LLM token 统计：{uid}", user_id=uid)
            return _ok({"ok": ok, "reset_user": uid})
        return _err("Not Found: " + path, status=404)

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    async def _api_stats(self, path: str, request: web.Request):
        g = self.plugin.gallery
        if path == "/stats/ranking":
            if g is None:
                return _ok({"scope": "all", "total": 0, "rows": []})
            days_raw = (self._q(request, "days", "all") or "").strip().lower()
            start_ts = end_ts = None
            if days_raw == "yesterday":
                _lt = time.localtime(time.time())
                _today0 = time.mktime((_lt.tm_year, _lt.tm_mon, _lt.tm_mday, 0, 0, 0, 0, 0, -1))
                start_ts = _today0 - 86400
                end_ts = _today0
                days = None
            else:
                days = {"today": 0, "3": 3, "7": 7, "all": None}.get(days_raw, None)
            merge = self._q(request, "merge", "0") == "1"
            merge_names = ["PrivateCompanion"] if merge else None
            return _ok(g.user_ranking(days=days, merge_alsoknown=merge_names,
                                      start_ts=start_ts, end_ts=end_ts))
        if path == "/stats/trend":
            if g is None:
                return _ok({"scope": "24h", "buckets": []})
            hours = self._qint(request, "hours", 24)
            hours = max(1, min(hours, 24 * 7))
            return _ok(g.hourly_trend(hours=hours))
        return _err("Not Found: " + path, status=404)

    # ------------------------------------------------------------------ #
    # LoRA 抓取 / 封面上传 / 封面图 / 翻译调试
    # ------------------------------------------------------------------ #
    class _AioReqAdapter:
        """把 aiohttp.Request 适配成 webui_api 方法预期的 request 对象。

        webui_api 方法内使用：request.query.get(k, d)（同步）、
        request.json(default)（await）、request.body()（await）、
        request.headers.get(k)（同步）。
        """

        def __init__(self, req: web.Request) -> None:
            self._req = req

        @property
        def query(self):
            return self._req.query

        @property
        def headers(self):
            return self._req.headers

        async def json(self, default=None):
            try:
                if self._req.can_read_body and self._req.body_exists:
                    return await self._req.json()
            except Exception:
                pass
            return default if default is not None else {}

        async def body(self):
            return await self._req.read()

    async def _api_lora_translate(self, path: str, request: web.Request) -> web.Response:
        """独立版 lora_fetch / lora_image / lora_upload_image / translate_test。

        复用 WebUIApi 的原始实现（引用 webui_api 模块级 request），通过
        aiohttp 适配器 + 串行锁临时替换 request，避免与 AstrBot 内嵌页并发冲突。
        """
        if self._api is None:
            return _err("WebUIApi 不可用")
        handler = {
            "/lora/fetch": ("lora_fetch", "POST"),
            "/lora/image": ("lora_image", "GET"),
            "/lora/upload_image": ("lora_upload_image", "POST"),
            "/translate/test": ("translate_test", "POST"),
        }.get(path)
        if handler is None:
            return _err("Not Found: " + path, status=404)
        method_name, _expected = handler
        if request.method.upper() != _expected:
            return _err("Method Not Allowed", status=405)

        adapter = self._AioReqAdapter(request)

        # 本地 json_response / error_response 替代品（返回普通 dict，便于外层归一化）。
        # webui_api 方法内引用的是模块级 json_response/error_response，临时替换之。
        def _ok_dict(payload):
            return {"__ok__": True, "data": payload}

        def _err_dict(msg, status_code=200):
            return {"__ok__": False, "error": str(msg)}

        async with self._request_lock:
            had_req = hasattr(webui_api, "request")
            prev_req = getattr(webui_api, "request", None)
            had_ok = hasattr(webui_api, "json_response")
            prev_ok = getattr(webui_api, "json_response", None)
            had_err = hasattr(webui_api, "error_response")
            prev_err = getattr(webui_api, "error_response", None)
            try:
                webui_api.request = adapter
                webui_api.json_response = _ok_dict
                webui_api.error_response = _err_dict
                method = getattr(self._api, method_name)
                result = await method()
            finally:
                webui_api.request = prev_req if had_req else None
                webui_api.json_response = prev_ok if had_ok else None
                webui_api.error_response = prev_err if had_err else None
        # 归一化 handler 返回的 dict
        if isinstance(result, dict):
            if result.get("__ok__") is not None:
                if result["__ok__"]:
                    return _ok(result.get("data"))
                return _err(result.get("error") or "请求失败")
            return _ok(result)
        if isinstance(result, web.Response):
            return result
        return _ok(result)

    # ------------------------------------------------------------------ #
    # 分享站（公开，分享令牌鉴权，与独立服务 admin token 解耦）
    # ------------------------------------------------------------------ #
    def _share_token_from(self, request: web.Request) -> str:
        # 分享令牌多渠道收集，按优先级返回：
        #  1) URL query token（分享链接 #/share?token=xxx，请求时以 query 传给后端）
        #  2) cookie anima_share_token（前端把分享令牌写入 cookie，同源请求自动携带，
        #     即使某次请求漏带 query token 也能兜住）
        #  3) Authorization 头（仅作为最后兜底，正常情况下分享令牌不会走这里，
        #     避免与独立服务管理口令混淆）
        # 当前格式用 share_t 参数（避免陪伴插件脱敏正则 token= 误伤）；兼容旧 token 参数
        q = request.query.get("share_t", "") or request.query.get("token", "")
        if q:
            return q.strip()
        try:
            ck = request.cookies.get("anima_share_token", "")
            if ck:
                return ck.strip()
        except Exception:
            pass
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def _client_ip(self, request: web.Request) -> str:
        """获取客户端 IP：优先 X-Forwarded-For（反代场景），其次 X-Real-IP，最后 peer。"""
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
        real = request.headers.get("X-Real-IP", "")
        if real:
            return real.strip()
        return request.remote or ""

    async def _handle_share_api(self, request: web.Request) -> web.Response:
        tail = request.match_info.get("tail", "") or ""
        path = "/" + tail.strip("/")
        tok = self._share_token_from(request)
        g = self.plugin.gallery
        info, allowed = (g.verify_share_token(tok, self._client_ip(request)) if (g and tok) else (None, False))
        if not info or not allowed:
            # 诊断：记录收到的令牌与查询条件，便于排查「分享链接已失效」
            _log.warning(
                f"[独立WebUI] 分享令牌拒绝 path={path} token_len={len(tok)} "
                f"token_head={(tok[:8] or '(empty)')} ip={self._client_ip(request)} query={str(request.query)}"
            )
            return _err("分享链接无效或已过期", status=404)
        try:
            return await self._dispatch_share(path, request.method.upper(), request, info)
        except Exception as e:
            return _err(f"处理失败: {e}")

    async def _dispatch_share(self, path: str, method: str, request: web.Request, info: dict) -> web.Response:
        g = self.plugin.gallery
        if g is None:
            return _err("图库未启用", status=500)
        uid = info.get("user_id", "")

        async def _body() -> dict:
            try:
                if getattr(request, "body_exists", False):
                    b = await request.json()
                    return b if isinstance(b, dict) else {}
            except Exception:
                pass
            return {}

        if path == "/me" and method == "GET":
            return _ok({"user_id": uid, "user_name": info.get("user_name"), "expire_at": info.get("expire_at")})
        if path == "/world" and method == "GET":
            limit = max(1, min(self._qint(request, "limit", 40), 100))
            offset = max(0, self._qint(request, "offset", 0))
            return _ok(g.world_list(user_id=uid, limit=limit, offset=offset))
        if path == "/gallery" and method == "GET":
            vis = (self._q(request, "vis", "all") or "all").strip().lower()
            if vis not in ("all", "public", "private"):
                vis = "all"
            limit = max(1, min(self._qint(request, "limit", 40), 100))
            offset = max(0, self._qint(request, "offset", 0))
            return _ok(g.gallery_list(user_id=uid, visibility=vis, limit=limit, offset=offset))
        if path == "/favorites" and method == "GET":
            limit = max(1, min(self._qint(request, "limit", 60), 200))
            offset = max(0, self._qint(request, "offset", 0))
            return _ok(g.favorites_list(user_id=uid, limit=limit, offset=offset))
        if path == "/profile" and method == "GET":
            return _ok(g.profile_stats(user_id=uid))
        if path == "/recycle" and method == "GET":
            return _ok({"images": g.recycle_list(user_id=uid)})
        if path == "/like" and method == "POST":
            b = await _body()
            sha = (b.get("sha") or "").strip()
            if not sha:
                return _err("缺少 sha")
            on = bool(b.get("on", True))
            ok = g.like(sha, uid, info.get("user_name")) if on else g.unlike(sha, uid)
            return _ok({"ok": ok, "liked": on, "like_count": g.like_count(sha)})
        if path == "/favorite" and method == "POST":
            b = await _body()
            sha = (b.get("sha") or "").strip()
            if not sha:
                return _err("缺少 sha")
            on = bool(b.get("on", True))
            ok = g.favorite(sha, uid, info.get("user_name")) if on else g.unfavorite(sha, uid)
            return _ok({"ok": ok, "favorited": on, "favorite_count": g.favorite_count(sha)})
        if path == "/set_public" and method == "POST":
            b = await _body()
            sha = (b.get("sha") or "").strip()
            if not sha:
                return _err("缺少 sha")
            on = bool(b.get("on", True))
            ok = g.set_public(sha, on, owner=uid)
            return _ok({"ok": ok, "is_public": on})
        if path == "/delete" and method == "POST":
            b = await _body()
            sha = (b.get("sha") or "").strip()
            if not sha:
                return _err("缺少 sha")
            ok = g.recycle(sha, owner=uid)
            return _ok({"ok": ok})
        if path == "/restore" and method == "POST":
            b = await _body()
            sha = (b.get("sha") or "").strip()
            if not sha:
                return _err("缺少 sha")
            m = g.get_by_sha(sha)
            if not m or m.get("user_id") != uid:
                return _err("无权操作该图", status=403)
            ok = g.restore(sha)
            return _ok({"ok": ok})
        return _err("Not Found: " + path, status=404)

    async def _handle_share_avatar(self, request: web.Request) -> web.Response:
        """分享站用户头像：/share/avatar/{user_id}。

        优先返回本地缓存的头像（data_dir/avatars/{uid}/current.png）；无缓存时尝试从
        QQ 头像接口拉取并缓存（拉取前把旧头像移入 history/ 作为历史记录）。失败返回 404。
        """
        tok = self._share_token_from(request)
        g = self.plugin.gallery
        info, allowed = (g.verify_share_token(tok, self._client_ip(request)) if (g and tok) else (None, False))
        if not info or not allowed:
            return _err("分享链接无效或已过期", status=404)
        uid = request.match_info.get("user_id", "") or ""
        if not uid:
            return _err("缺少 user_id", status=400)
        data_dir = getattr(self.plugin, "data_dir", None) or Path.cwd()
        av_dir = Path(data_dir) / "avatars" / uid
        try:
            av_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        current = av_dir / "current.png"
        if current.is_file():
            try:
                raw = await asyncio.to_thread(current.read_bytes)
                return web.Response(body=raw, content_type="image/png",
                                    headers={"Cache-Control": "public, max-age=3600"})
            except Exception:
                pass
        # 尝试从 QQ 拉取头像并缓存（含历史备份）
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s=640",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        raw = await resp.read()
                        if raw and len(raw) > 100:
                            if current.is_file():
                                hist = av_dir / "history"
                                try:
                                    hist.mkdir(parents=True, exist_ok=True)
                                    old = await asyncio.to_thread(current.read_bytes)
                                    ts = time.strftime("%Y%m%d-%H%M%S")
                                    await asyncio.to_thread((hist / f"{ts}.png").write_bytes, old)
                                except Exception:
                                    pass
                            try:
                                await asyncio.to_thread(current.write_bytes, raw)
                            except Exception:
                                pass
                            return web.Response(body=raw, content_type="image/png",
                                                headers={"Cache-Control": "public, max-age=3600"})
        except Exception:
            pass
        return _err("头像不存在", status=404)

    async def _handle_share_img(self, request: web.Request) -> web.Response:
        tok = self._share_token_from(request)
        g = self.plugin.gallery
        info, allowed = (g.verify_share_token(tok, self._client_ip(request)) if (g and tok) else (None, False))
        if not info or not allowed:
            return _err("分享链接无效或已过期", status=404)
        sha = request.match_info.get("sha", "") or ""
        if not sha:
            return _err("缺少 sha", status=400)
        if g is None:
            return _err("图库未启用", status=500)
        try:
            p = g.path_of(sha)
        except Exception as e:
            return _err(f"路径解析失败: {e}", status=500)
        if not p or not Path(p).exists():
            return _err("图片不存在", status=404)
        m = g.get_by_sha(sha)
        if m is None:
            return _err("图片不存在", status=404)
        is_owner = (m.get("user_id") or "") == info.get("user_id")
        if not is_owner:
            # 非 owner：仅允许查看公开且未删除的图；回收站/私有图只有 owner 可看（回收站可预览）
            if m.get("deleted") or not m.get("is_public"):
                return _err("无权限查看该图", status=403)
        want_thumb = "/thumb" in request.path
        size = self._qint(request, "size", 0)
        if want_thumb or (size and size > 0 and size < 200000):
            try:
                data_url = await asyncio.to_thread(self._thumb_cached, p, size or 320)
                if data_url and data_url.startswith("data:"):
                    header, _, b64 = data_url.partition(",")
                    cmime = (header.replace("data:", "").split(";")[0] or "image/jpeg").strip()
                    try:
                        raw = base64.b64decode(b64)
                    except Exception:
                        raw = await asyncio.to_thread(Path(p).read_bytes)
                        cmime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
                    return web.Response(
                        body=raw, content_type=cmime,
                        headers={"Cache-Control": "public, max-age=31536000"},
                    )
            except Exception:
                pass
        try:
            raw = await asyncio.to_thread(Path(p).read_bytes)
        except Exception as e:
            return _err(f"读取图片失败: {e}", status=500)
        ctype = mimetypes.guess_type(str(p))[0] or "image/jpeg"
        return web.Response(
            body=raw, content_type=ctype,
            headers={"Cache-Control": "public, max-age=31536000"},
        )

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    def _oplog_add(self, event: str, summary: str, **kw) -> None:
        try:
            if self.plugin.oplog is not None:
                self.plugin.oplog.add(event, summary, **kw)
        except Exception:
            pass

    @staticmethod
    def _thumb_cached(path, max_w: int) -> str:
        """生成缩略图 data URL（带模块级 LRU 缓存）。"""
        from . import webui_api
        return webui_api._thumb_data_url(path, max_w)


def create_standalone_webui(plugin):
    return StandaloneWebUI(plugin)
