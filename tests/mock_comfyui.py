"""本地 Mock ComfyUI 服务，用于在无真实 ComfyUI 环境下测试插件链路。

支持端点：
  POST /prompt             提交工作流，返回 {"prompt_id": ...}
  GET  /queue              返回 {"queue_running":[], "queue_pending":[...]}
  GET  /history            返回全部历史
  GET  /history/<pid>      返回指定任务历史
  GET  /view?filename=...  返回一张 1x1 PNG
  GET  /api/search?text=... 模拟 Danbooru 标签搜索（可选）
"""
import base64
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):  # 静默
        pass

    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.rstrip("/") == "/prompt":
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            pid = uuid.uuid4().hex
            with self.server.lock:
                self.server.seq += 1
                self.server.pending.append([self.server.seq, {"prompt_id": pid}, []])
                self.server.prompts[pid] = body.get("prompt", {})
            threading.Timer(1.0, self.server.complete, args=(pid,)).start()
            self._send_json({"prompt_id": pid})
        elif self.path.rstrip("/") == "/api/search":
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            text = body.get("query", "")
            self._send_json(
                {
                    "tags_all": ["1girl", "cat_ears", "sailor_collar", "smile"],
                    "tags_sfw": ["1girl", "cat_ears", "sailor_collar", "smile"],
                    "query": text,
                }
            )
        elif self.path.rstrip("/") == "/api/translate":
            # 通用 HTTP 翻译接口 mock：从 text_field（默认 text）取原文，返回英文标签
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            text = body.get("text", "")
            self._send_json(
                {
                    "data": {
                        "translated": "1girl, cat_ears, sailor_collar, smile, masterpiece"
                    },
                    "source": text,
                }
            )
        else:
            self._send_json({"error": "not found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/queue":
            with self.server.lock:
                self._send_json(
                    {
                        "queue_running": self.server.running,
                        "queue_pending": self.server.pending,
                    }
                )
        elif path == "/history":
            with self.server.lock:
                self._send_json(self.server.history)
        elif path.startswith("/history/"):
            pid = path.split("/history/", 1)[1]
            with self.server.lock:
                self._send_json(self.server.history.get(pid, {}))
        elif path == "/view":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG_1PX)))
            self.end_headers()
            self.wfile.write(PNG_1PX)
        elif path.rstrip("/") == "/api/search":
            qs = parse_qs(parsed.query)
            text = qs.get("text", [""])[0]
            self._send_json(
                {
                    "tags_all": ["1girl", "cat_ears", "sailor_collar", "smile"],
                    "tags_sfw": ["1girl", "cat_ears", "sailor_collar", "smile"],
                    "query": text,
                }
            )
        else:
            self._send_json({"error": "not found"}, 404)


class MockComfyUIServer(HTTPServer):
    def __init__(self, addr):
        super().__init__(addr, Handler)
        self.pending = []
        self.running = []
        self.history = {}
        self.prompts = {}
        self.seq = 0
        self.lock = threading.Lock()
        # 预置 2 个虚拟排队任务，用于测试“前面还有 N 位”
        for _ in range(2):
            self.seq += 1
            pid = "seed-" + uuid.uuid4().hex[:8]
            self.pending.append([self.seq, {"prompt_id": pid}, []])

    def complete(self, pid):
        with self.lock:
            self.pending = [p for p in self.pending if p[1].get("prompt_id") != pid]
            self.history[pid] = {
                "outputs": {
                    "save": {
                        "images": [
                            {
                                "filename": f"{pid}.png",
                                "subfolder": "",
                                "type": "output",
                            }
                        ]
                    }
                },
                "status": {"status_str": "success"},
            }


def start_mock(host: str = "127.0.0.1", port: int = 0):
    """启动 mock 服务，返回 (server, base_url)。port=0 表示随机端口。"""
    server = MockComfyUIServer((host, port))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    actual_port = server.server_address[1]
    return server, f"http://{host}:{actual_port}"
