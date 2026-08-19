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
        return bool(self._cfg("enabled", False))

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
        app.router.add_get("/api/ping", self._handle_ping)
        # 所有业务 API（前缀 /api/*），走统一鉴权 + 分发
        for method in ("GET", "POST"):
            app.router.add_route(method, "/api/{tail:.*}", self._handle_api)
        # 静态资源：index.html 之外的 js/css/图等
        app.router.add_get("/{path:.+}", self._handle_static)

    # 鉴权中间件（内联在每个 handler 前）
    def _authed(self, request: web.Request) -> web.Response | None:
        if not self._check_token(request):
            return _err("未授权：请填写访问口令", status=401)
        return None

    async def _handle_ping(self, request: web.Request) -> web.Response:
        return _ok({"pong": True, "ts": time.time()})

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
        tail = request.match_info.get("tail", "") or ""
        path = "/" + tail.strip("/")
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
            new_cfg = body.get("config") if isinstance(body, dict) else None
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
            page = max(1, self._qint(request, "page", 1))
            size = min(self._qint(request, "size", 40), 200)
            offset = (page - 1) * size
            rows = g.search(keyword=kw, type=stype, starred_only=starred, trash=trash,
                            limit=size, offset=offset, nsfw=nsfw)
            total = g.count_search(keyword=kw, type=stype, starred_only=starred,
                                   trash=trash, nsfw=nsfw)
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
                meta = g.get_by_sha(sha)
                size = self._qint(request, "size", 1600)
                data_url = await asyncio.to_thread(self._thumb_cached, p, size)
                return _ok({"data_url": data_url or "", "mime": None, "meta": meta})
            ctype = mimetypes.guess_type(str(p))[0] or "image/jpeg"
            return web.FileResponse(p, content_type=ctype)
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
            if not sha or not tags:
                return _err("缺少 sha 或 tags")
            tag_list = tags if isinstance(tags, list) else [tags]
            g.add_tags(sha, tag_list)
            self._oplog_add("gallery_tags", f"图库打标签：{','.join(tag_list)}", ref_sha=sha)
            return _ok({"msg": "标签已添加"})
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
            return _ok(q.list_users())
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
            # 全局限额实际存于插件 config（draw_limit），前端走 /config 保存；
            # 这里保留占位，避免前端请求 404。
            return _ok({"msg": "全局限额请通过配置保存"})
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
