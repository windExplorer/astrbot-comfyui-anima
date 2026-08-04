# 跨插件生图对接指南

> 适用插件：`astrbot_plugin_comfyui_anima`（ComfyUI 绘图）
> 目标读者：本插件管理员 / 接入本插件的其它插件作者
> 文档定位：说明「其它插件如果也要走工具生图，应如何统一接入本插件」的规范与配置方法。

---

## 1. 为什么统一走本插件

本插件是 ComfyUI 绘图的唯一出口，统一接它能保证：

- **一套工作流**：真人/动漫工作流、默认工作流、`is_anima` 判定都收口在一处，不会出现各插件各配一套导致画风/工作流混乱。
- **一套 LoRA**：LoRA 的启用/预设统一由本插件管理。
- **一套图库**：生成的成品图/参考图统一归档到本插件 `gallery/` 与 `refs/`，配 SQLite 索引，可复用、可检索。
- **一套语言规范**：真人用中文、动漫用英文，提示词质量统一可控（见 `docs/prompt-language-guide.md`）。
- **一套队列与错误处理**：排队位置、超时、失败提示都统一。

因此：**任何插件需要生成图片（文生图 / 图生图 / 自拍 / 换装 / 表情包等），都应调用本插件的 `comfyui_draw` / `comfyui_img2img` LLM 工具，而不是各自再实现一套 ComfyUI 客户端或接其它生图后端。**

---

## 2. 其它插件如何调用本插件生图

### 方式 A（推荐）：通过 AstrBot 的 LLM 工具机制

本插件用 `@filter.llm_tool` 注册了四个工具：

| 工具名 | 用途 | 核心参数 |
| --- | --- | --- |
| `comfyui_draw` | 文生图（消息带图时自动图生图） | `prompt`(必填), `negative_prompt`, `workflow`, `img2img_workflow`, `width`, `height`, `loras`, `seed`, `image`, `denoise`, `source` |
| `comfyui_img2img` | 基于参考图变换 / 重绘 | `prompt`(必填), `image`, `img2img_workflow`, ... |
| `comfyui_workflows` | 查询工作流列表 | 无参数 |
| `comfyui_gallery` | 发旧图 / 收藏图 / 检索 | `mode` |

其它插件可以用 AstrBot 的 LLM 工具管理器拿到 `comfyui_draw` 的 handler 并直接调用：

```python
manager = context.get_llm_tool_manager()
tool = manager.get_func("comfyui_draw")
handler = getattr(tool, "handler", None) or tool
# 传入一个合成/真实 AstrMessageEvent 作为 event
result = await handler(event, prompt="一只猫", source="<你的插件标识>")
```

### 方式 B：通过支持「tool_call 生图后端」的宿主插件配置

如果宿主插件自带「调用其它插件 LLM 工具生图」的能力（例如 `astrbot_plugin_private_companion` 的 `photo_generation_backend = "tool_call"`），把它配置成指向本插件的 `comfyui_draw` 即可，无需写代码。

---

## 3. 重要：必须传 `source`，否则拿不到图片路径

本插件的 `comfyui_draw` / `comfyui_img2img` 在成功生图后，**根据 `source` 参数决定返回方式**：

- `source` **命中** `"我会永远陪着你"`（`SOURCE_COMPANION_PLUGIN`）时 → 返回 **JSON 文本** `{"image_path": "<本地路径>", "status": "ok"}`，**由调用方负责发图**。
- `source` 为空或未命中时 → 工具会**自己 `event.send` 把图发给用户**，并只返回一句纯文本（"绘图已完成…"），**不返回图片路径**。

> 因此：其它插件要「拿到图片自己处理/转发」，**必须**传 `source`（建议用各插件自己的标识，或统一使用本插件识别的值）。
>
> 若你想让它返回 JSON 路径，直接传 `source="我会永远陪着你"` 即可——本插件会返回 `{"image_path": ...}`。如果你的宿主插件要求解析其它字段，也以 JSON 返回为准。

---

## 4. 以「伴侣插件（astrbot_plugin_private_companion）」为例

伴侣插件 `photo_generation_backend = "tool_call"` 时，按以下配置即可统一走本插件：

| 伴侣插件配置项 | 值 | 说明 |
| --- | --- | --- |
| `photo_generation_backend` | `tool_call` | 使用「调用其它插件 LLM 工具」后端 |
| `custom_photo_tool_name` | `comfyui_draw` | 调本插件的文生图/自动图生图工具 |
| `custom_photo_tool_prompt_param` | `prompt`（默认） | 本插件提示词参数名就是 `prompt` |
| `custom_photo_tool_kind_param` | 留空 | 本插件没有 `kind`（`text2img/selfie/edit`）语义，映射到 `workflow` 会传入假工作流名，**不要填** |
| `custom_photo_tool_reference_param` | `image` | 参考图走本插件 `image` 参数 |
| `custom_photo_tool_extra_params` | `{"source": "我会永远陪着你"}` | **必填**，否则拿不到图片路径 |

可选 `extra_params` 补充（按需）：
```json
{"source": "我会永远陪着你", "width": 768, "height": 768}
```
- `workflow`：若要指定真实工作流名，必须在 `extra_params` 里写**真实工作流名**（用 `comfyui_workflows` 查询），不能写 `text2img/selfie` 这类语义值。
- `seed` / `denoise` / `negative_prompt`：按需传入。

---

## 5. 其它插件 / 自定义接入时的通用清单

1. **工具名**：`comfyui_draw`（文生图/自动图生图）或 `comfyui_img2img`（强制图生图）。
2. **`prompt`**：必填；按语言规范——真人工作流用中文，动漫工作流用英文标签。
3. **`source`**：务必传本插件识别的值（或你的插件标识），否则拿不到图片路径。
4. **参考图**：图生图需要把本地路径或 URL 放到 `image` 参数；工具内部会转成本地路径。
5. **工作流**：除非明确要特定画风，否则不传 `workflow` 用默认；要传就必须先 `comfyui_workflows` 查询真实名称。
6. **结果解析**：`source` 命中时返回 `{"image_path": ..., "status": "ok"}`，从 `image_path` 取本地图片路径。
7. **超时**：本插件出图受 ComfyUI 速度影响，建议宿主插件 `tool_call_timeout` 设到 120s 以上。

---

## 6. 禁止事项

- ❌ 不要绕开本插件，在其它插件里再实现一套 ComfyUI 客户端/接 SDGen/在线 API 出图。
- ❌ 不要把 `workflow_kind`（`text2img/selfie/edit`）这种语义值直接当成 `workflow` 参数传给本插件。
- ❌ 不传 `source` 还指望能拿到图片路径。
- ❌ 图生图忘传参考图，导致工具走纯文生图或报「无图加载节点」。
