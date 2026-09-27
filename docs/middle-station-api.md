# TaskHub API 接口文档

**版本**：1.2  
**Base URL**：`http://<host>:9000`（默认 `0.0.0.0:9000`）  
**WebUI**：`/ui`　|　**API 文档网页版**：`/ui/api.html`　|　**Swagger**：`/docs`（FastAPI 自动生成）

> 文档覆盖全部 HTTP 接口、WebSocket 与状态码语义。接口分四类：
> ① TTS 标准接口（AstrBot 语音插件对接）② ComfyUI 标准接口（绘图插件对接）
> ③ 监控与任务管理（含设备状态、上游自检）④ WebSocket 实时推送。

---

## 0. 通用说明

### 0.1 状态码语义（严格遵守）

| 状态码 | 含义 | 插件/调用方行为 |
| --- | --- | --- |
| `200` | 成功 | 正常解析 |
| `400` | 参数错误（缺 tts_text / 缺参考音频 / 非法 JSON） | 不重试，直接报错 |
| `404` | 任务不存在 / 非排队状态 | 不重试 |
| `429` | 排队等待超时（队列满或等待超过 `server.queue_wait`） | **指数退避重试** |
| `503` | 模型未加载完成 / 服务繁忙 | 退避重试 |
| `504` | 推理超时（超过 `server.infer_timeout`） | 退避重试 |
| `500` | 内部错误 / 上游失联 / 空音频 | 不重试，进冷却 |

> 上游（真实 CosyVoice / ComfyUI）返回的非 200 状态码**原样透传**给调用方。

### 0.2 数据格式

- TTS 合成结果：**裸 int16 PCM 字节流**（`application/octet-stream`），24kHz 单声道，无 WAV 头（WAV 头由插件补）。
- ComfyUI 任务：JSON `{"prompt_id": "32位hex"}`。
- 时间戳字段：Unix 秒（float）。

### 0.3 运维相关能力速览

日常排障最常用的四个：

| 想干什么 | 用哪个 | 章节 |
| --- | --- | --- |
| 看这台机器/显卡现在什么状态（含温度功耗、上游 ComfyUI 与 CosyVoice 状态） | `GET /device`（`?raw=1` 带上游原始信息） | 3.2.1 |
| 看「任务是不是卡住了」「上游是不是把 prompt 丢了」 | `GET /self-check`（异常清单） | 3.1.1 / 3.9 |
| 立刻重跑一次上游核对，不等巡检周期 | `POST /self-check/run` | 3.10 |
| 看每个任务的真实进度（节点 x/总数、采样步数） | `GET /queue` 的 `tasks[].progress`、`WS /ws/tasks` | 3.4 / 4.2 |

> 三条已实现的自愈/兜底行为（上层无需感知，供排障时对照）：
> ① 上游把 prompt 丢弃（重启/OOM 崩溃）→ 自动终结任务、释放单飞槽位、`/history/{prompt_id}` 返回明确失败条目；
> ② 出图跟踪有硬超时（`comfyui.watch_timeout`，按单个任务计）；
> ③ 服务重启后，上次遗留的 `running`/`queued` 历史记录会自动收敛为终态。

---

## 1. TTS 标准接口（CosyVoice 语音插件）

> 契约来源：`docs/backend-api.md`。插件零改动对接。

### 1.1 `GET /` — 健康检查 + 采样率

插件**首次合成前**必读，用返回的 `sample_rate` 覆盖本地配置（否则音调会变）。

**响应 200**：

```json
{ "status": "ok", "model_loaded": true, "sample_rate": 24000 }
```

### 1.2 `POST /inference_zero_shot` — 零样本合成（主路径）

`multipart/form-data`：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `tts_text` | ✅ | 目标文本（插件已分段，每段一次请求） |
| `prompt_text` | 可选 | 参考音频对应的纯人声文本；**缺省时**中转站按 `prompt_wav_path` 文件名从 voices 映射自动回退 |
| `prompt_wav` | 二选一 | 上传的参考音频文件 |
| `prompt_wav_path` | 二选一 | 参考音频在**后端**的文件名/路径（推荐，免上传大文件） |

- 两者都不给 → `400`。
- `prompt_text` 含 LLM 污染标记（`<|endofprompt|>` 等）或 >150 字会被自动净化丢弃，由后端 voices 映射回退。
- **响应 200**：裸 PCM 字节流。其他状态码透传后端。

**curl**：

```bash
# 参考音频走服务端路径（推荐）
curl -X POST http://127.0.0.1:9000/inference_zero_shot \
  -F "tts_text=你好，今天天气不错。" \
  -F "prompt_wav_path=xiaoyu.wav"

# 上传参考音频（AstrBot 本地文件）
curl -X POST http://127.0.0.1:9000/inference_zero_shot \
  -F "tts_text=你好" \
  -F "prompt_text=你好，我是小宇。" \
  -F "prompt_wav=@/path/to/xiaoyu.wav"
```

**Python（httpx，带 429 退避）**：

```python
import httpx, time

def synthesize(text, voice="xiaoyu.wav", max_retry=3):
    for attempt in range(max_retry):
        r = httpx.post("http://127.0.0.1:9000/inference_zero_shot",
                       data={"tts_text": text, "prompt_wav_path": voice},
                       timeout=130)
        if r.status_code == 200:
            return r.content
        if r.status_code in (429, 503, 504):
            time.sleep(0.5 * 2 ** attempt)
            continue
        print("[错误]", r.status_code, r.text)
        return None
```

### 1.3 `POST /inference_instruct2` — 指令合成（兼容保留）

字段同 1.2，另加：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `instruct_text` | ✅ | 语气指令，如「请用开心的语气说」 |

响应格式同 1.2。

### 1.4 `GET /voices` — 参考音频列表（排错用）

**响应 200**：

```json
{
  "voices_dir": "D:/CosyVoice/voices",
  "files": ["xiaoyu.wav", "boss.wav"],
  "texts": { "xiaoyu.wav": "你好，我是小宇。" }
}
```

---

## 2. ComfyUI 标准接口（绘图插件）

> 契约来源：`docs/comfyui-backend-api.md`。`/prompt` 走**单飞调度**（同一时刻仅放行 `serialize_concurrent` 个任务），其余接口透传。

### 2.1 `POST /prompt` — 提交绘图任务（核心，单飞）

**请求体 JSON**：

```json
{
  "prompt": { "4": { "class_type": "CheckpointLoaderSimple", "inputs": {} } },
  "client_id": "astrbot-comfyui-xxxx"
}
```

- 排队中的请求**内部阻塞等待槽位**（不返回 429），提交成功后立即返回 prompt_id。
- **响应 200**：`{ "prompt_id": "e1f2...32位hex" }`（这是中转站的**虚拟 pid**，插件照旧用它轮询 `/history/{prompt_id}`）。
- 校验失败：透传真实 ComfyUI 的状态码与文案（常见 400）。
- **转发时会改写 `client_id`**：统一成中转站自己的固定值（`taskhub-xxxxxxxx`，见 `/config` → `comfyui.client_id`；原值保留在同级字段 `_client_id_original` 里）。
  原因：真实 ComfyUI 的 `executing` / `executed` / `progress` / `progress_state` 事件是
  `send_sync(event, data, server.client_id)` 发出的，而执行期间 `server.client_id` 等于该 prompt 的
  `client_id`，sid 不在线时事件会被**静默丢弃**；中转站必须用同一个 id 建 `/ws` 才收得到真实进度
  （见 3.4 的 `progress`）。所以在 ComfyUI 网页端的队列里看到 client 是 `taskhub-xxxx` 属正常现象，
  想固定它就在配置里写死 `comfyui.client_id`。
- 转发前会剥掉以 `_` 开头的私有字段（如内部的 `_virtual_pid`），不会透给真实 ComfyUI。

### 2.2 `POST /upload/image` — 上传图生图参考图

`multipart/form-data`：`image`（文件）、`type`（固定 `input`）。

**响应 200**：

```json
{ "name": "photo.png", "subfolder": "", "type": "input" }
```

> 中转站保证图片落到真实 ComfyUI 的 `input` 目录，`name` 可被 `/prompt` 引用。

### 2.3 `GET /history/{prompt_id}` — 查询单个任务结果

- 排队中/尚未提交的真实任务返回 `{}`（插件会持续轮询，不会误判完成）。
- 完成后透传真实 ComfyUI 的历史 JSON（含 `outputs.images[]`）。
- 任务已失败（上游执行报错 / `watch_timeout` 超时 / prompt 被上游丢弃）时返回
  **合成错误条目**：`{prompt_id: {outputs: {}, status: {status_str: "error", completed: false, messages: [[..., {exception_message: "taskhub: ..."}]]}}}`。
  插件据此立即结束轮询并提示失败，不会空转到自己的超时。

> 单飞槽位**必定释放**：出图完成、硬超时 `comfyui.watch_timeout`、或「丢失检测」
> （`watch_lost_grace` 后连续 `watch_lost_confirm` 次既不在真实 `/queue` 也不在真实
> `/history`，说明真实 ComfyUI 中途重启/崩溃把 prompt 丢了）三条路径任一命中即终结任务，
> 不会出现单飞槽位被永久占用、后续任务（含 TTS）全部排队饿死的情况。

### 2.4 `GET /history` — 全部历史（透传）

### 2.5 `GET /view` — 下载输出图片（透传）

Query：`filename`、`subfolder`、`type`（通常 `type=output`）。响应为图片二进制。

---

## 3. 监控与任务管理接口

### 3.1 `GET /health` — 系统状态

```json
{
  "status": "ok",
  "gpu_load": 0.11,
  "model_loaded": true,
  "queue_length": 2,
  "running": 1,
  "max_concurrent": 3,
  "sample_rate": 24000,
  "self_check": { "level": "warn", "count": 1, "items": [ /* 见 3.9 */ ] }
}
```

### 3.1.1 上游自检（inspector）

中转站每 `inspector.interval`（默认 10s）核对一次「调度器认为在跑的任务」与「真实上游」，
把异常写进日志、`/health`、`/monitor` 与 WebSocket，并标在对应任务上
（`tasks[].anomaly = {level, code, msg, ts}`，WebUI 会标红）。

| code | level | 含义 | 是否主动处置 |
| --- | --- | --- | --- |
| `lost` | error | prompt 既不在上游 `/queue` 也不在 `/history`（上游重启/OOM 崩溃丢弃） | **是**：终结任务并释放单飞槽位 |
| `slow` | warn | 耗时超过同类成功任务 P95 × `inspector.slow_factor`（下限 `slow_min_seconds`） | 否，仅告警 |
| `stalled` | warn | 上游在跑但 `inspector.stall_seconds` 内没有任何进度事件（需 `/ws` 订阅在线） | 否，仅告警 |
| `slot_wait` | warn | 等待单飞槽位过久（通常意味着上一个任务卡住） | 否 |
| `upstream_external` | info | 上游有非本站已知任务在跑/排队（网页端提交或重启前遗留），说明资源被占用 | 否 |
| `upstream_vram_low` | warn | 上游显存可用低于 `monitoring.vram_min_free_gb` | 否（调度器会降并发） |
| `upstream_unreachable` | error | 上游 `/queue` 不可达（进程退出？） | 否 |
| `tts_not_ready` | warn | CosyVoice 后端未就绪，语音会 503 | 否 |

判据来源：`GET /queue`（在不在上游手里）、`GET /history/{pid}`（是否已出图）、
`/ws` 事件（是否真的在往前算）、`GET /system_stats`（上游显存）、本站 SQLite 历史耗时基线。

### 3.2 `GET /monitor` — 实时资源快照

```json
{
  "cpu_percent": 13.6,
  "ram_available_gb": 12.02,
  "ram_used_gb": 19.8,
  "ram_total_gb": 31.81,
  "gpu_load": 0.11,
  "gpu_free_gb": 6.43,
  "gpu_total_gb": 8.0,
  "gpu_threshold": 0.8,
  "ts": 1786033708.8,
  "queue_length": 0,
  "running": 0,
  "max_concurrent": 3,
  "effective_concurrent": 3
}
```

> `effective_concurrent` 为 GPU 过载时降并发后的有效值；`gpu_load > gpu_threshold` 时自动降为 `max(1, max_concurrent*0.5)`。
> `/monitor` 同样返回 `self_check`（WebUI 顶部「自检」徽标与工具提示用它）。

### 3.2.1 `GET /device?raw=0` — 当前设备状态（本机 + 上游聚合）

即时采样（不等 `/monitor` 的采样周期）。**「上游聚合」** 指中转站自己作为客户端，把真实后端的
设备/健康信息一次问全并归一化后返回，省得再分别去点 ComfyUI 的 `/system_stats` 和 CosyVoice 的 `/`：

| 分组 | 数据来源 | 内容 |
| --- | --- | --- |
| `host` | 本机 NVML + psutil + 注册表 | 主机名、**系统版本详情**、开机/服务运行时长、CPU 型号·核数·频率·占用、内存、服务所在磁盘、本进程 RSS/线程、**每张显卡**的型号/利用率/显存/温度/功耗上限/风扇/频率、驱动版本 |
| `host.os` | 注册表 + `platform` | `caption`（如 `Windows 11 专业工作站版`）、`edition`/`edition_id`、`display_version`（如 `25H2`）、`build`/`ubr`、`version`（完整 `10.0.26200.9550`）、`arch`、`kernel`、`install_time`、`python` |
| `host.thermal` / `host.fans` | LHM/OHM 的 WMI 命名空间，退化到 ACPI 性能计数器 | CPU 封装/每核温度、主板温度、机箱风扇 RPM；GPU 温度/风扇见 `host.gpus[]`。**读不到时字段为 `null`/`[]` 并带 `note` 说明原因**（见下方说明） |
| `upstreams.comfyui` | `GET /queue` + `GET /system_stats` | 是否可达、执行中/排队 prompt、`queue_remaining`（取自 `/ws`）、`/ws` 订阅状态、ComfyUI 版本·OS·Python·PyTorch·上游内存、上游各设备显存与 torch 占用 |
| `upstreams.cosyvoice` | `GET /` | 是否可达、`model_loaded`、真实 `sample_rate`、音色数、参考音频目录、探测时间、上游自述信息 |
| `scheduler` | 内存 | 队列长度、运行中、有效并发、降级原因、累计入队/完成 |
| `self_check` | 自检巡检 | 同 3.1.1（`level`/`count`/`items`） |

> **CPU 温度 / 机箱风扇为什么可能是 `null`**：Windows 读 CPU 封装温度必须走 Ring0 驱动，
> 纯 Python + WMI 只能拿到 ACPI 热区（`Win32_PerfFormattedData_Counters_ThermalZoneInformation`，
> 台式机上通常反映主板/PCH，不是 CPU 核心；`MSAcpi_ThermalZoneTemperature` 在多数主板上直接不可用，
> `Win32_Fan` 也不给转速）。所以：
> - **装了** LibreHardwareMonitor（或 OpenHardwareMonitor / HWiNFO，启用 WMI provider）→ 自动读到
>   CPU Package / 每核温度 / 主板温度 / 机箱风扇 RPM，`thermal.source` 为 `librehardwaremonitor`；
> - **没装** → `thermal.cpu_package_c: null`、`fans.system: []`，`acpi_zones_c` 里通常还有一个热区温度，
>   `note` 写明原因。中转站不会编造数值。
> - Linux/BSD 上走 psutil 的 `sensors_temperatures()`/`sensors_fans()`（`source: psutil`）。
>
> 探测要起一次 PowerShell 子进程（约 0.3s），因此结果缓存 15s；首次 `/device` 约 0.8s，之后 <50ms。

- 任一上游不可达只体现为 `reachable: false`（并带 `system_stats_ok: false`），接口本身**仍返回 200**，
  不会因为上游挂了而超时或失败；本机与另一个上游的数据照常返回。
- `?raw=1` 时额外返回真实 ComfyUI `/system_stats` 的原始 JSON（`raw_system_stats`），排障用。
- 动态指标逐项 `try`：老卡或被动散热卡没有风扇转速等字段时会**缺项**而不是报错；
  多卡是 `host.gpus[]` 数组，`host.gpus[0]` 同时驱动调度器的降并发判断。

### 3.3 `GET /stats?hours=24` — 历史统计（SQLite）

```json
{
  "window_hours": 24,
  "total": 40, "done": 35, "failed": 0,
  "success_rate": 87.5,
  "by_type": { "tts": 35, "comfyui": 5 },
  "avg_run_seconds": 0.36
}
```

### 3.4 `GET /queue?limit=100` — 队列与任务状态

```json
{
  "queue_length": 2, "running": 1,
  "max_concurrent": 3, "effective_concurrent": 3,
  "gpu_threshold": 0.8, "gpu_load": 0.11,
  "total_queued": 123, "total_completed": 120,
  "tasks": [ { "task_id": "tts_xxx", "task_type": "tts", "priority": 0,
              "status": "running", "status_code": 200, "error": "",
              "created_at": 1786033708.8, "started_at": 1786033709.0,
              "finished_at": null, "queue_seconds": 0.2, "run_seconds": 1.5,
              "resource_weight": 1.0, "estimated_duration": 30.0,
              "progress": {}, "anomaly": null } ]
}
```

任务状态：`queued`（排队中）/ `waiting`（等待单飞槽位）/ `running`（运行中）/ `done`（完成）/ `failed`（失败）/ `timeout`（超时）/ `cancelled`（已取消）。

`progress`（仅绘图任务、需 `/ws` 订阅在线）：`{nodes_done, nodes_total, node, step, step_total, state, updated_at}`
—— 来自真实 ComfyUI 的 `progress_state` / `progress` / `executing` / `executed` 事件，最多每秒推送一次。
`anomaly`：自检异常标记（见 3.1.1），只在 WebSocket `task_update` 里实时推送，不落库。

### 3.5 `GET /tasks` — 全部任务（分页 + 筛选，查 SQLite 历史）

Query 参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `page` | 1 | 页码 |
| `page_size` | 20 | 每页条数（最大 200） |
| `task_type` | 空 | 按类型筛选：`tts` / `comfyui` |
| `status` | 空 | 按状态筛选：`queued` / `waiting` / `running` / `done` / `failed` / `timeout` / `cancelled` |
| `keyword` | 空 | 模糊搜索 task_id / 错误信息 / 类型 |

```json
{ "total": 40, "page": 1, "page_size": 20, "pages": 2,
  "tasks": [ /* 同 /queue 的 tasks 元素 */ ] }
```

> 任务记录持久化于 SQLite（`server.db_path`），服务重启不丢失；请求参数存档在 `task_payloads` 表（一般不用看，仅作记录）。

### 3.6 `POST /tasks/{task_id}/promote?priority=-1` — 插队

把排队中任务提到指定优先级（数值越小越优先）。成功：`{"ok": true, "task_id": "...", "priority": -1}`；不存在或非排队：`404`。

### 3.7 `POST /tasks/{task_id}/cancel` — 取消排队任务

成功：`{"ok": true, "task_id": "..."}`；不存在或非排队：`404`。

### 3.8 `GET /config` — 当前生效配置（只读）

返回 `server` / `tts` / `comfyui` / `monitoring` / `inspector` 五组配置摘要与运行时值（含探测后的真实 `sample_rate`、提交给真实 ComfyUI 的 `client_id`）。修改请编辑 `middle-station.yaml` 后重启。

与本文接口行为强相关的配置项（完整列表见 `middle-station.yaml` 注释）：

| 配置项 | 默认 | 影响的接口/行为 |
| --- | --- | --- |
| `server.queue_wait` | 30 | 排队等待超时 → `429`（TTS 等走 `submit()` 的接口） |
| `server.infer_timeout` | 110 | 推理超时 → `504`。两者之和须 ≤ 调用方读超时 − 10s（语音插件 150s → 140s），否则插件先断连、音频白算 |
| `server.max_concurrent` | 1 | 全局并发；GPU/显存过载时自动降半，见 `/monitor.effective_concurrent` |
| `comfyui.serialize_concurrent` | 1 | `/prompt` 单飞放行数 |
| `comfyui.watch_interval` | 2 | `_watch` 轮询真实 `/history` 间隔 |
| `comfyui.watch_timeout` | 900 | 出图跟踪硬超时（按**单个任务**计，不含排队） |
| `comfyui.watch_lost_grace` | 30 | 提交后多久开始核对真实 `/queue` 判断 prompt 是否被上游丢弃 |
| `comfyui.watch_lost_confirm` | 2 | 连续多少次核对都查无此 prompt 才判定丢失 |
| `comfyui.progress_stream` | true | 是否订阅真实 ComfyUI `/ws`（任务 `progress` 与 `stalled` 自检依赖它） |
| `comfyui.client_id` | 自动 | 转发 `/prompt` 时使用的 client_id（进度事件只发给它） |
| `inspector.enabled` | true | 是否启用上游自检巡检 |
| `inspector.interval` | 10 | 自检周期（秒） |
| `inspector.slow_factor` / `slow_min_seconds` | 3.0 / 60 | `slow` 异常阈值：`max(60s, 同类 P95 × 3)` |
| `inspector.stall_seconds` | 240 | `stalled` 异常阈值：上游在跑但这么久无进度事件 |

### 3.9 `GET /self-check` — 上游自检详情

```json
{
  "level": "warn", "count": 2, "cycles": 42, "last_run": 1790348487.9,
  "items": [
    { "key": "resource:vram", "level": "warn", "code": "upstream_vram_low",
      "msg": "上游显存仅剩 0.63GB (< 1.0GB)，出图/语音可能 OOM", "task_id": null, "ts": 1790348487.6 },
    { "key": "upstream:external", "level": "info", "code": "upstream_external",
      "msg": "上游有 1 个执行中 / 0 个排队的非本站已知任务", "task_id": null, "ts": 1790348487.6 }
  ],
  "probe": { "reachable": true, "running": ["eba82a3a-..."], "pending": [],
             "vram_free_gb": 0.63, "vram_total_gb": 8.0 },
  "progress_stream": { "connected": true, "last_event_ago": 0.3, "queue_remaining": 1 }
}
```

- `level`：`ok` / `info` / `warn` / `error`，取所有异常里最严重的一档；`count` 为当前异常条数。
- `items[].code` 的取值与含义见 **3.1.1**；`key` 是内部去重键（`task:<task_id>:<规则>` / `resource:vram` / `upstream:external` / `tts`）。
- `probe`：最近一次上游探测快照；`progress_stream`：`/ws` 订阅状态。
- 终态类异常（如 `lost`）会在任务终结后**粘性保留 120s**，避免用户还没看到就消失。

### 3.10 `POST /self-check/run` — 手动触发一次自检

排查用，不用等下一个巡检周期（默认 10s）。返回结构与 `GET /self-check` 相同。

---

## 4. WebSocket 实时推送

### 4.1 `WS /ws/monitor` — 资源监控（每秒一帧）

```json
{ "cpu_percent": 12.7, "ram_available_gb": 11.88, "gpu_load": 0.15,
  "gpu_free_gb": 6.43, "gpu_total_gb": 8.0, "gpu_threshold": 0.8,
  "queue_length": 0, "running": 0, "max_concurrent": 3,
  "effective_concurrent": 3, "ts": 1786033736.9,
  "self_check": { "level": "ok", "count": 0, "items": [] } }
```

> 字段与 `GET /monitor` 一致；`self_check` 同 3.1.1（WebUI 顶部「自检」徽标用它，悬停出明细）。
> 更完整的设备信息（型号/温度/功耗/上游状态）走 `GET /device`，不在这里每秒推送。

### 4.2 `WS /ws/tasks` — 任务事件

连接后先发全量 `init`，之后每次状态变更推 `task_update`，空闲时每 15s 发 `ping` 保活：

```json
{ "event": "init", "tasks": [ /* 全部任务，同 /queue 的 tasks 元素 */ ] }
{ "event": "task_update", "task": { /* 单个任务对象，同上 */ } }
{ "event": "ping" }
```

`task_update` 有两种触发来源，**事件格式完全相同**：

| 来源 | 触发 | 是否落库 | 说明 |
| --- | --- | --- | --- |
| 状态变更 | 入队/开始/等待槽位/完成/失败/超时/取消 | ✅ 写 SQLite | 低频，一任务数次 |
| **实时更新** | 真实上游进度（`progress`）、自检异常（`anomaly`） | ❌ 不落库 | 最多每秒一次，避免刷库；仅用于界面实时展示 |

因此订阅方不要用 `task_update` 的频率推断任务是否在推进；判「是否卡住」请看 `task.anomaly`
或 `GET /self-check`。任务对象里的 `progress` / `anomaly` 字段见 **3.4**。

### 4.3 `WS /ws/logs` — 日志流

连接后增量推送（每条日志一行），格式：

```json
{ "ts": 1786033736.9, "level": "INFO", "message": "task tts_xxx done" }
```

---

## 5. 示例：完整调用链（Python）

```python
import httpx

BASE = "http://127.0.0.1:9000"

with httpx.Client(timeout=10) as c:
    # 1. 健康检查（拿采样率）
    print(c.get(f"{BASE}/").json())

    # 2. 提交 TTS
    r = c.post(f"{BASE}/inference_zero_shot",
               data={"tts_text": "你好", "prompt_wav_path": "xiaoyu.wav"}, timeout=150)
    if r.status_code == 200:
        pcm = r.content  # 裸 int16 PCM

    # 3. 提交 ComfyUI 绘图
    r = c.post(f"{BASE}/prompt",
               json={"prompt": {"4": {"class_type": "CheckpointLoaderSimple",
                                      "inputs": {"ckpt_name": "model.safetensors"}}},
                     "client_id": "demo"}, timeout=150)
    pid = r.json()["prompt_id"]

    # 4. 轮询结果
    import time
    for _ in range(120):
        h = c.get(f"{BASE}/history/{pid}").json()
        if pid in h:
            print("done:", h[pid]["outputs"])
            break
        time.sleep(2)

    # 5. 下载图片
    img = c.get(f"{BASE}/view", params={"filename": "00001.png",
                                        "subfolder": "", "type": "output"})
    open("out.png", "wb").write(img.content)
```

### 5.1 运维检查（设备 + 自检）

```python
import httpx

BASE = "http://127.0.0.1:9000"
with httpx.Client(timeout=15) as c:
    # 设备状态：本机 + 上游后端一次看全
    d = c.get(f"{BASE}/device").json()
    g = (d["host"]["gpus"] or [{}])[0]
    print(f'{g.get("name")} {g.get("util_percent")}% '
          f'显存 {g.get("mem_free_gb")}/{g.get("mem_total_gb")}GB '
          f'{g.get("temperature_c")}°C {g.get("power_w")}/{g.get("power_limit_w")}W')
    print("ComfyUI:", d["upstreams"]["comfyui"]["reachable"],
          d["upstreams"]["comfyui"]["system"].get("comfyui_version"),
          "队列", d["upstreams"]["comfyui"]["queue"])
    print("CosyVoice:", d["upstreams"]["cosyvoice"]["reachable"],
          d["upstreams"]["cosyvoice"]["sample_rate"], "Hz")

    # 自检：有异常时逐条打印（level >= warn 就值得看一眼）
    sc = c.get(f"{BASE}/self-check").json()
    print("self-check:", sc["level"], sc["count"])
    for a in sc["items"]:
        print(f'[{a["level"]}] {a["code"]}: {a["msg"]}')

    # 怀疑任务卡住时：立刻重跑一次核对
    print(c.post(f"{BASE}/self-check/run").json()["items"])
    # 再对着 /queue 看哪个任务带着 anomaly
    for t in c.get(f"{BASE}/queue").json()["tasks"]:
        if t.get("anomaly") or t.get("progress"):
            print(t["task_id"], t["status"], t.get("progress"), t.get("anomaly"))
```

---

## 6. 相关资源

| 资源 | 位置 |
| --- | --- |
| WebUI 监控面板 | `/ui` |
| API 文档网页版 | `/ui/api.html` |
| Swagger | `/docs`（FastAPI 自动生成） |
| 计划文档 | `docs/middle-station-plan.md` |
| 对接契约（TTS） | `docs/backend-api.md` |
| 对接契约（ComfyUI） | `docs/comfyui-backend-api.md` |
| 配置 | 仓库根目录 `middle-station.yaml` |
