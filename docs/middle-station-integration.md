# Middle Station 对接说明（AstrBot 插件方）

> 面向：AstrBot 插件（CosyVoice 语音 / ComfyUI 绘图）的开发者或维护者。
> 目的：说明插件如何接入任务调度中转站，以及可选的「排队位置感知」扩展。
> 发送对象可直接阅读本文档，无需配合方修改任何协议。

---

## 1. 中转站是什么

一个本地任务调度服务，位于 **AstrBot 插件与真实后端（CosyVoice / ComfyUI）之间**：

```
AstrBot 插件 ──标准接口──> Middle Station（队列/调度/GPU·显存感知并发）
                                ├──> CosyVoice 后端
                                └──> ComfyUI 后端
```

它做的事情：
- **统一排队**：TTS 与绘图任务进入同一个优先级队列，按 `priority`（越小越先）+ 入队时间调度
- **资源感知并发**：根据 GPU 算力占用率、可用显存水位自动调整并发（防 OOM / 争抢）
- **状态管理**：每个任务记录完整生命周期（排队/等待/运行/完成/失败/超时/取消）
- **严格状态码**：429 / 503 / 504 / 500 语义与插件现有重试逻辑完全兼容

## 2. 插件接入（零代码改动，仅改配置）

| 插件 | 配置项 | 原值（示例） | 改为 |
| --- | --- | --- | --- |
| 语音插件 | `base_url` | `http://127.0.0.1:50002`（直连 CosyVoice） | `http://127.0.0.1:9000`（中转站） |
| 绘图插件 | `comfyui_servers[].url` | `http://127.0.0.1:8188`（直连 ComfyUI） | `http://127.0.0.1:9000`（中转站） |

- 插件**已有的**重试、指数退避、熔断冷却、并发限流逻辑全部不变
- 插件调用的**接口路径、方法、参数、响应格式完全不变**（见 §3）
- 切换只发生在中转站配置里，插件侧一次配置后无感知

## 3. 标准接口（插件已兼容，不要求改动）

插件照常调用以下接口，中转站按相同契约提供：

| 接口 | 说明 | 响应 |
| --- | --- | --- |
| `GET /` | 健康检查 + 采样率 | `{status, model_loaded, sample_rate}` |
| `POST /inference_zero_shot` | 零样本合成（multipart） | **裸 int16 PCM**（24kHz 单声道，无 WAV 头） |
| `POST /inference_instruct2` | 指令合成（兼容保留） | 裸 PCM |
| `POST /prompt` | 提交绘图工作流 | `{prompt_id}`（32 位 hex） |
| `POST /upload/image` | 上传图生图参考图 | `{name, subfolder, type}` |
| `GET /history/{prompt_id}` | 查询单任务结果 | 透传真实后端历史 JSON |
| `GET /history` | 全部历史 | 透传 |
| `GET /view` | 下载输出图片 | 图片二进制 |
| `GET /voices` | 参考音频列表（排错用） | `{voices_dir, files, texts}` |

> 完整契约见 `docs/backend-api.md` 与 `docs/comfyui-backend-api.md`。

## 4. 排队位置感知（新增能力，可选，需插件配合读取）

中转站并发受限（可配 `max_concurrent`，常用 `1`）时，任务会排队。**中转站在所有提交接口的成功响应头中返回 `X-Queue-Position`**，表示「该任务入队那一刻，前方还有几个任务（**含正在运行的**）」。

- 首个任务：`X-Queue-Position: 0`
- 前方有 1 个任务在跑、自己在排队：`X-Queue-Position: 1`

**读取方式（插件侧）**：

```python
# httpx
r = await client.post(url, data={...}, files={...})
pos = r.headers.get("X-Queue-Position")   # "0" / "1" / "2" ...

# requests
r = requests.post(url, data={...}, timeout=150)
pos = r.headers.get("X-Queue-Position")
```

**适用接口**：`/inference_zero_shot`、`/inference_instruct2`、`/prompt` 的成功响应。

**说明**：
- 该响应头是**增量**能力，不影响既有响应体（PCM 字节流 / `{prompt_id}` JSON），不读它的插件完全不受影响
- 位置是「入队时刻」的快照；随队列推进实际等待会变化
- 用途示例：插件可在返回给用户的消息里提示「前面还有 N 个任务，请稍候」

## 5. 状态码语义（插件现有重试逻辑直接生效）

| 状态码 | 含义 | 插件行为 |
| --- | --- | --- |
| `200` | 成功 | 正常解析 |
| `400` | 参数错误 | 不重试，直接报错 |
| `429` | 排队等待超时（队列满） | **指数退避重试** |
| `503` | 模型未加载 / 服务繁忙 | 退避重试 |
| `504` | 推理超时 | 退避重试 |
| `500` | 内部错误 / 上游失联 / 空音频 | 不重试，进冷却 |

## 6. 超时调配（重要，配合插件侧配置）

插件读超时（`timeout`，默认 **150s**）必须 ≥ 中转站 `queue_wait + infer_timeout`：

```
插件 timeout(150s) ≥ 中转站 queue_wait(10s) + infer_timeout(60s) = 70s   ✓
```

- 中转站默认 `queue_wait=10s`、`infer_timeout=60s`，合计 70s < 150s，**无需改动**
- 若中转站排队/推理超时调大，请同步调大插件 `timeout`，否则插件先断连会触发重试验崩
- ComfyUI 出图跟踪超时（`watch_timeout=180s`）作用于中转站内部，不影响插件

## 7. 辅助接口（可选，调试用）

| 接口 | 说明 |
| --- | --- |
| `GET /health` | 系统状态（GPU 负载、模型加载、队列长度） |
| `GET /monitor` | 实时资源（CPU/RAM/GPU/显存 + 有效并发 + 降级原因） |
| `GET /queue` | 当前队列与任务状态 |
| `GET /stats?hours=24` | 历史统计（成功率、耗时、按类型分布） |
| `GET /config` | 当前生效配置 |

---

**示例环境**：中转站默认 `http://127.0.0.1:9000`，WebUI 监控面板 `http://127.0.0.1:9000/ui`。
