# ComfyUI 后端接口与中转站（任务调度）对接文档

> 适用插件：`astrbot_plugin_comfyui_anima`（ComfyUI 绘图）
> 目标读者：打算搭建「任务调度中转站」的开发者 / 管理员
> 文档定位：完整描述**插件会向 ComfyUI 后端发起的所有 HTTP 接口请求**（方法、路径、请求体、响应、调用时机、并发要点），并给出中转站如何在这些接口之上做任务调度、避免多任务互相抢占资源导致卡死的设计建议与参考实现。

---

## 0. 背景：为什么要做中转站

插件默认把 `comfyui_servers[].url` 直连到 ComfyUI（如 `http://127.0.0.1:8188`）。当多个用户同时发 `/draw`、`comfyui_draw` 工具调用时，插件会**同时**向 ComfyUI 的 `/prompt` 提交多个任务。ComfyUI 自身虽然会排队执行，但：

- 多个任务在同一时刻涌入，GPU / VRAM / 内存并发争抢，容易出现**资源互相拥挤、任务互相卡死**；
- 插件侧对每个任务单独轮询 `/history`，缺少「一次只放行一个任务」的全局节流；
- 图生图任务还伴随 `/upload/image` 上传，高并发下上传与推理互相干扰。

**中转站的作用**：在插件与真实 ComfyUI 之间插入一层 HTTP 代理/调度服务。插件把 `comfyui_servers[].url` 改为指向中转站（例如 `http://127.0.0.1:9000`），中转站：

1. 透传只读接口（`/history`、`/view`）与上传接口（`/upload/image`）；
2. 对**写任务接口 `/prompt` 做串行/限流调度**：同一时刻只向真实 ComfyUI 放行 `N` 个任务（通常 `N=1`），其余任务在中转站排队；
3. 维护每个 `prompt_id` 的状态（排队中 / 执行中 / 完成 / 失败），供外部查询。

---

## 1. 接口总览

插件（`ComfyUIClient`，见 `comfyui_client.py`）只依赖以下 **5 个** HTTP 接口，**不使用** WebSocket、不使用 `/queue`、不使用 `/settings`、不使用 `/object_info`。

| # | 方法 | 路径 | 用途 | 中转站是否需要调度 |
| --- | --- | --- | --- | --- |
| 1 | `POST` | `/prompt` | 提交一个绘图工作流任务，返回 `prompt_id` | ✅ 核心，需串行/限流 |
| 2 | `POST` | `/upload/image` | 上传图生图参考图（multipart） | ⚠️ 建议限流或转真后端 |
| 3 | `GET` | `/history/{prompt_id}` | 查询**单个**任务结果（输出图片元信息） | 透传（无需调度） |
| 4 | `GET` | `/history` | 查询全部任务历史 | 透传（无需调度） |
| 5 | `GET` | `/view` | 按文件名下载输出图片 | 透传（无需调度） |

> 中转站只需实现/转发上述 5 个接口即可。**插件从不调用 `/queue`**（其注释明确"不依赖 ComfyUI 的 /queue 接口"，排队位置由插件本地自行统计）。

---

## 2. 接口详细说明

### 2.1 `POST /prompt` —— 提交绘图任务（核心）

**调用方**：`main.py` `_do_draw` → `ComfyUIClient.queue_prompt()`。每次绘图（文生图 / 图生图 / LLM 工具）都会调用一次。

**请求头**：`Content-Type: application/json`

**请求体**：

```json
{
  "prompt": {
    "4": {
      "class_type": "CheckpointLoaderSimple",
      "inputs": { "ckpt_name": "anima_lacrimosa.safetensors" }
    },
    "6": {
      "class_type": "CLIPTextEncode",
      "inputs": { "text": "1girl, silver hair, ...", "clip": ["4", 1] }
    },
    "9": {
      "class_type": "KSampler",
      "inputs": {
        "seed": 12345,
        "steps": 30,
        "cfg": 7,
        "sampler_name": "euler",
        "scheduler": "normal",
        "denoise": 1.0,
        "model": ["4", 0],
        "positive": ["6", 0],
        "negative": ["7", 0],
        "latent_image": ["5", 0]
      }
    }
  },
  "client_id": "astrbot-comfyui-a1b2c3d4"
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `prompt` | `object` | 完整工作流。键为**节点 ID 字符串**，值为节点对象：`class_type` 为节点类型，`inputs` 为该节点输入。节点间连线用 `["节点ID", 输出索引]` 三元组引用（图生图时 `LoadImage` 的 `image` 输入为**纯字符串文件名**，非三元组，见 2.2） |
| `client_id` | `string` | 客户端标识。由插件生成，格式 `astrbot-comfyui-` + 8 位随机 hex；若配置里显式填了 `client_id` 则用配置值。中转站可用它区分请求来源 |

**成功响应（HTTP 200）**：

```json
{ "prompt_id": "e1f2...（32 位十六进制）" }
```

**失败响应**：ComfyUI 校验失败时返回非 200（常见 400），插件会捕获并提示用户"提交任务失败"。

**调用时序**：每次 `_do_draw` 恰调用一次 `/prompt`，拿到 `prompt_id` 后进入「本地队列登记 → 轮询 `/history/{prompt_id}` 直到出图 → `/view` 下载」。因此 `/prompt` 是唯一的任务入口，也是中转站**必须串行化**的接口。

**并发要点**：
- 若不调度，插件 A/B/C 可能几乎同时发出 `/prompt`，真实 ComfyUI 一次性收到 3 个任务 → 资源争抢。中转站应在此处做**单飞（serialize）**：同一时刻只向真后端放行 `N` 个 `/prompt`。
- 排队中的请求建议**阻塞等待**（保持连接、轮询或回调），待槽位空出再转发，这样插件侧不用改任何代码就能获得"排到队"的效果。若选择立刻返回 429/排队号，则需插件配合——**但当前插件不识别排队响应，因此中转站应采用「内部排队 + 阻塞转发」**，对插件完全透明。

---

### 2.2 `POST /upload/image` —— 上传图生图参考图

**调用方**：`main.py` `_do_draw` 中图生图分支 → `ComfyUIClient.upload_image()`。仅在 `init_images` 非空（图生图）时调用，一张参考图一次调用。

**请求头**：`Content-Type: multipart/form-data`

**表单字段**：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `image` | file | 本地图片文件，`filename` 为原始文件名（如 `photo.png`） |
| `type` | string | 固定 `input`（写入真实 ComfyUI 的 `input` 目录） |

**成功响应（HTTP 200）**：

```json
{
  "name": "photo.png",
  "subfolder": "",
  "type": "input"
}
```

> 插件取 `info["name"]`（缺省回退 `info["filename"]`、再回退 `os.path.basename(img_path)`）作为文件名，注入工作流 `LoadImage` 节点的 `image` 输入。**中转站必须保证返回的 `name` 是真实 ComfyUI 上可被 `/prompt` 引用的文件名**——即要么把上传的图片透传/落盘到真实 ComfyUI 的 `input` 目录，要么返回真实 ComfyUI 已存在的文件名。

**并发要点**：
- 图生图时先上传、后 `/prompt`。中转站应保证「某任务的参考图上传完成」后才放行该任务的 `/prompt`（时序天然满足，但若中转站拆开处理需注意）。
- 多图并发上传也可能占满磁盘/IO，建议给上传也做轻量限流（如同步上传，或限制同时上传数）。

---

### 2.3 `GET /history/{prompt_id}` —— 查询单个任务结果

**调用方**：`ComfyUIClient.wait_for_result()` 轮询（间隔 `queue_poll_interval`，默认 2s）→ `get_history(prompt_id)`；超时兜底时也会再查一次。

**响应（HTTP 200）**：一个以 `prompt_id` 为键的历史字典。

```json
{
  "e1f2...": {
    "prompt": { "...": {} },
    "outputs": {
      "12": {
        "images": [
          { "filename": "00001.png", "subfolder": "", "type": "output" }
        ]
      }
    },
    "status": { "status_str": "success", "completed": true, "messages": [] }
  }
}
```

插件用 `comfyui_client.extract_images()` 从 `outputs` 里提取图片列表（优先 `output_node` 指定节点，兼容 `images` 与 `gifs` 键）。**只有 `prompt_id` 出现在历史里，插件才认为任务完成**。

**并发要点**：
- 纯查询，无副作用，中转站可直接透传到真实 ComfyUI。
- **重要**：真实 ComfyUI 的 `/history` 会持久保留已完成任务。中转站若做内部排队，需在「任务尚未真正提交给真实 ComfyUI」时，对该 `prompt_id` 的 `/history` 查询返回"尚不存在"（即空 `{}` 或不含该 id），否则插件会误判任务已完成却没图。见第 4 节设计建议。

---

### 2.4 `GET /history` —— 查询全部历史

**调用方**：`get_history(prompt_id=None)`。当前插件主流程未主动调用（仅透传能力），但保留以实现完整性。中转站直接透传即可。

---

### 2.5 `GET /view` —— 下载输出图片

**调用方**：`ComfyUIClient.get_image()`。在 `/history` 确认出图后，对每张输出图片调用一次，下载图片二进制。

**请求参数（Query String）**：

```
/view?filename=00001.png&subfolder=&type=output
```

| 参数 | 说明 |
| --- | --- |
| `filename` | 输出文件名（来自 `/history` 的 `images[].filename`） |
| `subfolder` | 子目录（通常为空） |
| `type` | 图片类型，通常 `output` |

**成功响应（HTTP 200）**：图片二进制（`image/png` 等）。

**并发要点**：纯下载，透传即可。中转站需保证 `filename`/`subfolder`/`type` 原样透传给真实 ComfyUI 的 `/view`，因为图片实际存在真实 ComfyUI 的输出目录。

---

## 3. 中转站接入方式（插件侧零改动）

1. 启动中转站服务（监听某端口，如 `0.0.0.0:9000`），并把上游指向真实 ComfyUI（如 `http://127.0.0.1:8188`）。
2. 打开插件 WebUI 或 `_conf_schema.json`，把对应服务器的 `comfyui_servers[].url` 改为 `http://127.0.0.1:9000`。
3. 插件发起的所有请求都走中转站；中转站按第 2 节规则透传/调度。

> 插件配置项 `url` 的 hint 原文："服务地址，需包含 http(s)://，例如 http://127.0.0.1:8188"。指向中转站同理填中转站地址即可。`client_id` 可留空（自动生成）或显式配置。

---

## 4. 任务调度设计建议

### 4.1 核心目标：`/prompt` 单飞

同一台真实 ComfyUI 同一时刻只接受 `N` 个 `/prompt`（`N` 建议 `1`，即一次只跑一个任务，彻底避免资源互相拥挤）。实现：**任务队列 + 信号量/槽位**。

```
收到 POST /prompt（task A）
  → 入队（队列最大长度可配，超过则按需拒绝/丢弃）
  → 等待空槽位
  → 槽位空闲 → 转发 POST /prompt 到真实 ComfyUI，拿到 prompt_id
  → 立即把 prompt_id 返回给插件（注意：不是等任务跑完才返回）
  → 任务在真实 ComfyUI 执行期间仍占住槽位，直到：
      - 通过轮询真实 /history 发现该 prompt_id 完成 → 释放槽位；或
      - 超时 → 释放槽位（标记失败）
```

> **关键**：`/prompt` 的响应 `prompt_id` 必须在**提交成功**后立即返回插件，绝不能等推理完成才返回——因为插件拿到 `prompt_id` 后会立刻开始轮询 `/history/{prompt_id}`。

### 4.2 排队任务与 `/history` 的一致性

插件用「`prompt_id` 是否出现在 `/history` 中」判断任务是否完成。因此中转站在**排队阶段**（任务尚未真正提交给真实 ComfyUI）时：

- 对 `GET /history/{prompt_id}` 应返回 **不含该 id** 的历史（`{}`），或明确 404；
- 这样插件会一直轮询等待，直到任务真正提交、完成，才在历史里看到它。

### 4.3 槽位占用与释放

槽位应在**任务整个生命周期**内占用（提交 → 真实 ComfyUI 执行 → 出图），而非仅占「提交那一瞬间」。否则多个任务仍会同时压到真实 ComfyUI 上执行。建议中转站维护一张 `prompt_id → 状态` 表：

| 状态 | 触发 |
| --- | --- |
| `queued` | 收到 `/prompt`，进入内部队列 |
| `running` | 已转发给真实 ComfyUI，占槽位 |
| `done` | 轮询真实 `/history` 发现该 id 完成，释放槽位 |
| `failed/timeout` | 真实 ComfyUI 校验失败 / 轮询超时，释放槽位 |

### 4.4 超时与错误处理

- 插件侧 `draw_timeout`（默认 120s）会因本地队列 `ahead` 动态放大，故中转站排队导致的延迟插件能容忍，但中转站自身仍建议给每个任务一个兜底超时（如 10 分钟）并主动清理状态表。
- 转发 `/prompt` 失败（真实 ComfyUI 返回 400 等）时，中转站应把**同样的错误响应**原样回传给插件（插件依赖状态码/提示文案向用户展示）。

### 4.5 可选增强

- **按 `client_id` 分流**：不同工作流/来源分配到不同真实 ComfyUI（中转站可代理多台后端）。
- **任务去重/冷却**：对高频相同 `prompt` 做幂等合并（可选，非必需）。
- **监控**：暴露 `/healthz`、内部队列长度、槽位占用等只读端点，方便运维观察。

---

## 5. 参考实现（FastAPI 最小中转站）

以下为「单飞调度 + 透明透传」的最小可运行示例，仅演示核心调度逻辑，未含鉴权、持久化与生产加固：

```python
import asyncio
from collections import deque

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI()
UPSTREAM = "http://127.0.0.1:8188"   # 真实 ComfyUI
MAX_CONCURRENT = 1                    # 单飞：一次只放行 1 个任务
MAX_QUEUE = 50                        # 内部排队上限

_sem = asyncio.Semaphore(MAX_CONCURRENT)
_queue = deque()                      # 内部任务队列（含 /prompt 请求体）
_states: dict[str, str] = {}          # prompt_id -> queued/running/done/failed
_http = aiohttp.ClientSession()


async def forward(method: str, path: str, **kw) -> Response:
    async with _http.request(method, UPSTREAM + path, **kw) as r:
        body = await r.read()
        return Response(
            content=body,
            status_code=r.status,
            headers={k: v for k, v in r.headers.items() if k.lower() in
                     ("content-type", "content-disposition")},
        )


@app.post("/prompt")
async def submit_prompt(req: Request):
    payload = await req.json()
    # 入队并等待空槽位（排队阶段阻塞保持连接，对插件透明）
    async def _run():
        async with _sem:
            return await forward("POST", "/prompt", json=payload)
    resp = await _run()
    # 登记状态：拿到 prompt_id 后开始跟踪
    try:
        data = await resp.json()
        pid = data.get("prompt_id")
        if pid:
            _states[pid] = "running"
            asyncio.create_task(_watch(pid))   # 后台轮询真实 history 释放槽位
    except Exception:
        pass
    return resp


async def _watch(prompt_id: str):
    """轮询真实 ComfyUI /history，任务完成/超时后释放（槽位随 semaphore 自动释放，此处只清状态）。"""
    try:
        for _ in range(150):            # 每 4s 查一次，最多 10 分钟
            await asyncio.sleep(4)
            async with _http.get(f"{UPSTREAM}/history/{prompt_id}") as r:
                hist = await r.json()
            if prompt_id in hist:
                _states[prompt_id] = "done"
                return
        _states[prompt_id] = "failed"
    except Exception:
        _states[prompt_id] = "failed"


# 其余接口透明透传（无需调度）
@app.api_route("/history", methods=["GET"])
async def history(req: Request):          return await forward("GET", "/history" + req.url.query)

@app.api_route("/history/{prompt_id}", methods=["GET"])
async def history_one(req: Request, prompt_id: str):
    return await forward("GET", f"/history/{prompt_id}")

@app.api_route("/view", methods=["GET"])
async def view(req: Request):             return await forward("GET", "/view?" + req.url.query)

@app.post("/upload/image")
async def upload(req: Request):
    body = await req.body()
    return await forward("POST", "/upload/image",
                         data=body,
                         headers={"Content-Type": req.headers.get("content-type", "")})


@app.get("/healthz")
async def healthz():
    return JSONResponse({"queue": len(_queue), "concurrent": _sem._value,
                         "states": _states})
```

> 该示例为**最小单飞**：排队逻辑直接依赖 `asyncio.Semaphore`（`await _sem` 自然阻塞），`_queue` 仅为监控展示；`_watch` 仅在任务完成后清理状态、不影响槽位释放（槽位在 `_sem` 释放时即空出）。生产落地建议把 `_states` 与队列落到内存 dict / Redis，并补充鉴权、日志、优雅关闭。

---

## 6. 常见问题（FAQ）

- **中转站需不需要实现 `/queue`？**
  不需要。插件明确不调用 `/queue`，排队位置由插件本地 `_local_queue_*` 自行统计。

- **插件认不认得「排队中」的响应？**
  不认得。因此中转站不要对 `/prompt` 返回自定义排队码（如 429/排队号），而应**内部阻塞等待空槽位后再转发**，把真实 ComfyUI 的响应原样返回——这样才能对插件零改动地生效。

- **图生图的参考图怎么处理？**
  中转站必须让 `/upload/image` 上传的图片落到真实 ComfyUI 可访问的 `input` 目录，并返回真实文件名；否则后续 `/prompt` 里引用该文件名会失败。

- **一台中转站能代理多台真实 ComfyUI 吗？**
  可以。按 `comfyui_servers` 的 `name`/`url` 或 `client_id` 区分后端，各自维护独立槽位与队列即可。

---

## 7. 相关代码索引

| 文件 | 相关实现 |
| --- | --- |
| `comfyui_client.py` | `ComfyUIClient`：`queue_prompt` / `upload_image` / `get_history` / `get_image` / `wait_for_result`；`extract_images` |
| `main.py` | `_build_client`（构建客户端）、`_do_draw`（提交+轮询+下载全流程）、`_local_queue_*`（插件本地队列统计） |
| `_conf_schema.json` | `comfyui_servers`（`url` / `client_id` / `enabled` / `name`）、`draw_timeout`、`queue_poll_interval` 等 |
