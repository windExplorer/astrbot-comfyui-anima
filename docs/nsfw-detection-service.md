# NSFW 远程检测服务（独立部署，CLIP ViT-L）

> 状态：方案设计（已定，待新开独立项目实现）
> 范围：**独立的远程检测服务**（部署在出图服务器，GPU ~7GB）
> 消费方：本插件 WebUI 如何消费其 score/level、审核页与展示策略，见 `TODO-审核与NSFW展示策略.md`

---

## 1. 为什么需要独立服务

本插件内置的本地 OpenNSFW（opennsfw-onnx）误判严重（动漫 18R 漏判、正常图误杀）。为获得更准确的检测，把"重"的检测解耦到出图服务器的 GPU 上，作为常驻 HTTP 服务，插件端轻量调用、不装 `torch`/`transformers`。

插件侧如何把返回的 score/level 用于三级自动判定、双分数存储、人工复核、展示策略，**不在本文档**，见 `TODO-审核与NSFW展示策略.md`。

---

## 2. 模型选型：CLIP ViT-L/14 零样本

| 候选 | 准确度 | 依赖 | 7G 可行 | 备注 |
|---|---|---|---|---|
| OpenNSFW（现状，本地） | 低 | onnx（已有） | ✅ | 误判严重，插件侧仅作参考/分歧，非主判 |
| Falconsai ViT-Base | 中 | torch+transformers | ✅ | 干净但比 CLIP-L 弱 |
| **CLIP ViT-L/14 零样本** | **中高** | **torch+transformers** | ✅ | **选定：只用 `openai/clip-vit-large-patch14`，无额外头/框架** |
| NeMo / LAION NSFW 头 | 高 | nemo-curator / tensorflow | ⚠️ | 框架重、依赖脏，不采用 |
| Qwen2.5-VL-7B | 最高(语义) | torch 大模型 | ❌ 7G 装不下 | 仅作疑难图二次复核，不在本方案 |

**关键决策**：不采用 NeMo / LAION 的 NSFW 头（分别绑 Dask 集群、Autokeras/TensorFlow，与出图服务器环境冲突）。直接用 CLIP ViT-L/14 的零样本图文匹配能力判 NSFW，权重即 `openai/clip-vit-large-patch14`（~600MB，HuggingFace 自带），只依赖 `transformers`。

### 2.1 判定逻辑（零样本）

用 CLIP 的 `logits_per_image` 对一组文本提示做 softmax，取 NSFW 类概率合计作为 `score`：

```python
from transformers import CLIPProcessor, CLIPModel
import torch

model = CLIPModel.from_pretrained("openai/clip-vit-large-patch14").to("cuda")
processor = CLIPProcessor.from_pretrained("openai/clip-vit-large-patch14")

TEXTS = [
    "a safe for work, normal photo",
    "explicit NSFW pornographic content",
    "artistic nudity",
    "explicit hentai anime content",
]

def score_image(img: "PIL.Image") -> float:
    inputs = processor(text=TEXTS, images=img, return_tensors="pt", padding=True).to("cuda")
    with torch.no_grad():
        out = model(**inputs)
    probs = out.logits_per_image.softmax(-1)[0]
    return float(probs[1:].sum())   # NSFW 类（索引 1,2,3）概率合计
```

> 提示词与 NSFW 类权重建议用真实样本回测校准。零样本对"动漫 18R"边界区分度有限，终极边界由插件侧人工复核兜底（见 `TODO-审核与NSFW展示策略.md`）。

### 2.2 资源占用（7G 实测预期）

- CLIP ViT-L/14 权重常驻显存约 **1.5~2.5 GB**；
- 与 ComfyUI 出图共用 GPU，单张检测毫秒级，批量（batch≤8）几百张/秒；
- 出图高峰若显存吃紧，检测服务应错峰或限制 batch，避免与出图抢显存 OOM。

---

## 3. 服务设计（nsfw_service）

常驻 HTTP 服务，模型只加载一次；**作为出图服务器上手动运行的独立程序**（非插件内子服务）**，默认 `http://localhost:8890`。

### 3.1 接口契约

**POST `/detect`**
- 请求：`multipart/form-data`，字段 `file` = 图片二进制（也可支持 `?url=` 传本地路径）
- 响应：
```json
{
  "nsfw": false,
  "score": 0.13,
  "level": "safe",
  "model": "openai/clip-vit-large-patch14-zs"
}
```
- `level` 取值（阈值由插件侧配置 `low/high` 给定，服务按相同值划分；已定 `low=0.35 / high=0.8`）：
  - `safe`：`score < 0.35` → 自动判安全
  - `review`：`0.35 <= score < 0.8` → 进人工复核
  - `nsfw`：`score >= 0.8` → 自动判 NSFW（插件侧按策略模糊，仍可人工推翻）

**GET `/healthz`**
- 返回 `{"ok": true, "model_loaded": true}`，供插件端探测可用性。

**POST `/detect/batch`**（可选）：收多张，batch≤8，返回数组，用于历史库批量扫描提吞吐。

### 3.2 服务要点

- 模型常驻显存，进程内单例；首次从 HF 下载权重（或预置 `HF_HOME` 离线）。
- 可选 `torch.float16` 推理（FP16）省显存，对 ViT-L 收益有限但无害。
- batch 默认 ≤8（保守，防显存抖动）；历史扫描可调大。
- 进程守护：systemd / nohup / docker 均可；监听内网，勿暴露公网。

### 3.3 最小服务骨架（伪代码）

```python
from fastapi import FastAPI, File
from PIL import Image
import io, torch
from transformers import CLIPProcessor, CLIPModel

app = FastAPI()
DEVICE = 0 if torch.cuda.is_available() else -1
model = CLIPModel.from_pretrained("openai/clip-vit-large-patch14").to(DEVICE)
processor = CLIPProcessor.from_pretrained("openai/clip-vit-large-patch14")
TEXTS = ["a safe for work, normal photo", "explicit NSFW pornographic content",
         "artistic nudity", "explicit hentai anime content"]
LOW, HIGH = 0.35, 0.8

def _level(s: float) -> str:
    return "safe" if s < LOW else ("nsfw" if s >= HIGH else "review")

@app.post("/detect")
async def detect(file: bytes = File(...)):
    img = Image.open(io.BytesIO(file)).convert("RGB")
    inputs = processor(text=TEXTS, images=img, return_tensors="pt", padding=True).to(DEVICE)
    with torch.no_grad():
        probs = model(**inputs).logits_per_image.softmax(-1)[0]
    s = float(probs[1:].sum())
    return {"nsfw": s >= HIGH, "score": s, "level": _level(s),
            "model": "openai/clip-vit-large-patch14-zs"}
```

---

## 4. 风险与备注

- **动漫 18R 边界**：CLIP 零样本对二次元艺术化/半遮/擦边区分度有限，靠插件侧人工复核兜底（见 `TODO-审核与NSFW展示策略.md`）。
- **显存共用**：检测服务与 ComfyUI 抢同一张 7G 卡，高峰需限 batch / 错峰，防 OOM。
- **权重下载**：首次需联网访问 huggingface.co；生产建议预置 `HF_HOME` 离线缓存。
