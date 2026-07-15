# astrbot_plugin_comfyui_anima

AstrBot 的 ComfyUI 绘图插件。可通过**指令**或**与 AI 对话**触发绘图，支持：

- 配置多个 ComfyUI 服务器（同一时间仅启用一个）
- 配置多个工作流，每个工作流可绑定一个 ComfyUI 服务器（只有一个服务器时默认绑定）
- 每个工作流可配置节点 ID（正向/负向提示词、宽度、高度）以及 **LoRA 管理**（启用/禁用、权重，默认权重 1）
- AI 对话中可通过“使用 xxx lora”等方式临时启用指定 LoRA
- 提交任务后返回「前面还有多少位」的队列位置
- 可配置 Danbooru 标签搜索服务器；Anima 工作流会先把中文提示词翻译成英文 Danbooru 标签再绘图

> AstrBot 插件开发文档：<https://docs.astrbot.app/dev/star/plugin-new.html>

## 安装

通过 AstrBot WebUI 的「插件管理 → 上传 zip」安装本插件（选 `astrbot_plugin_comfyui_anima.zip`），或把整个插件目录放到 AstrBot 的 `data/plugins/` 下。安装后 `requirements.txt` 中的依赖会被自动安装。

插件启动后会在其**数据目录** `data/plugin_data/astrbot_plugin_comfyui_anima/`（AstrBot 数据根下的 `plugin_data/<插件id>/`）下自动创建两个子目录：

- `temp/`：存放 ComfyUI 返回的图片（超过 1 天自动清理）
- `workflow/`：存放工作流 JSON 文件

## 配置（插件配置页）

在 AstrBot 管理面板中编辑本插件的配置，主要字段：

### comfyui_servers（多个服务器，只启用一个）
- `name`：服务器名称，供工作流绑定
- `url`：地址，需带 `http://`，如 `http://127.0.0.1:8188`
- `enabled`：是否启用（多个服务器只能有一个为 true）
- `client_id`：可留空自动生成

### workflows（工作流列表）
每个工作流：
- `name`：工作流名称（指令 / AI 调用时引用）
- `server_name`：绑定的服务器名称；留空则自动用唯一启用的那个
- `is_anima`：是否为 Anima 工作流（是则先翻译中文提示词）
- `workflow_name`：工作流 JSON 文件名（放在 `data/plugin_data/astrbot_plugin_comfyui_anima/workflow/` 下，如 `sd.json` 或 `sd`；只需填名字，不用写全路径，缺 `.json` 自动补）。优先级高于下一项
- `workflow_json`：直接粘贴 ComfyUI 导出的 **API 格式**工作流 JSON（与 `workflow_name` 二选一）
- `positive_node` / `positive_input`：正向提示词节点 ID 与输入框名（默认 `text`）
- `negative_node` / `negative_input`：负向提示词节点 ID 与输入框名（默认 `text`）
- `width_node` / `width_input`：宽度节点 ID 与输入框名（默认 `width`）
- `height_node` / `height_input`：高度节点 ID 与输入框名（默认 `height`）
- `output_node`：输出图片节点 ID（SaveImage/PreviewImage）；留空自动取最后一个含图片的节点
- `default_width` / `default_height`：默认尺寸
- `loras`：LoRA 列表，每项含：
  - `name`：标识名（AI 说“使用 xxx lora”时按此名匹配）
  - `keywords`：触发关键词，逗号分隔（如 `猫娘,catgirl`），出现在提示词里会自动启用
  - `model_name`：ComfyUI 中 LoRA 文件名（含扩展名，如 `xxx.safetensors`）
  - `load_node`：LoRA 加载节点 ID（LoraLoader 节点）
  - `enabled`：默认是否启用
  - `weight`：默认权重，默认 `1.0`
  - `model_input` / `strength_model_input` / `strength_clip_input`：输入框名，一般无需改

### danbooru（标签翻译服务器）
- `enabled`：是否启用
- `url`：`http://127.0.0.1:11111`
- `api_path`：`/api/search`
- `limit` / `show_nsfw` / `use_segmentation` / `append_original`
- `popularity`：标签流行度阈值（0~1，默认 `0.15`），仅返回流行度不低于该值的标签，值越高过滤掉的生僻标签越多
- `top_k`：候选标签数量上限（默认 `20`）

### 其他
- `default_workflow`：默认工作流名
- `draw_timeout`：出图等待超时（秒，默认 120）
- `queue_poll_interval`：轮询出图结果的间隔（秒，默认 2）
- `return_queue_position`：提交后是否发送可爱提示（无排队说在出图，有排队说前面几位）

## 使用

### 指令
- `/draw 一只白色水手服少女` — 基础绘图
- `/draw 一只猫 --wf sd --lora catgirl:0.8 --w 768 --h 768` — 指定工作流、LoRA、尺寸
- `/loralist [--wf 名称]` — 列出 LoRA
- `/loraon 名称 [--wf 名称]` / `/loraoff 名称 [--wf 名称]` — 持久启用/禁用 LoRA
- `/queuestatus [--wf 名称]` — 查看队列和你前面的排队位数
- `/workflows [set 名称]` — 列出/设置默认工作流
- `/drawhelp` — 帮助

### AI 对话
直接对机器人说，例如：
> 画一只在雨中奔跑的白色水手服少女，使用 catgirl lora

AI 会调用 `comfyui_draw` 工具，并按你提到的 LoRA 名称启用对应 LoRA。Anima 工作流下，中文描述会先被翻译成 Danbooru 标签。

## 工作流 JSON 获取
在 ComfyUI 界面点「Queue」旁边的菜单 → **Export (API Format)**，把得到的 JSON 放到插件数据目录的 `workflow/` 下（如 `workflow/sd.json`），然后配置里填 `workflow_name: sd` 即可；或直接把 JSON 粘贴到 `workflow_json`。节点 ID 可在该 JSON 的顶层键中找到（如 `"6": {...}`）。

## LoRA 的启用 / 禁用 / 注入

本插件把 LoRA 配置作为「唯一真相源」，在提交前对工作流图做处理：

- **启用**：工作流里已有的 `LoraLoader` 节点会被改写（写入 LoRA 文件名与权重）。
- **真禁用**：禁用某个 LoRA 时，会把对应的 `LoraLoader` 节点**从工作流中删除**，并把它的上下游（model / clip）直接接通。这样该节点不再执行——**不加载文件、不占显存、也不会因文件缺失而报错**。这与旧版「把权重置 0」不同：置 0 时节点仍会加载文件、仍可能报错，并不是真正禁用。
- **按配置注入**：如果工作流里**没有** `LoraLoader` 节点，插件可以按配置自动新建。只需在工作流配置里填 `lora_anchor`（底模 `CheckpointLoader` 节点的键名，如 `4`）；留空则自动探测 `CheckpointLoader`。插件会在锚点之后链式新建 `LoraLoader` 节点、接好线，实现「一份干净工作流 + 不同 LoRA 配置 = 不同出图」，无需为每种组合手动导出工作流。
- 工作流里存在、但**没有被任何配置项覆盖**的 `LoraLoader` 节点，会保持原样不动。

> 注入默认按标准 `LoraLoader`（同时处理 model 与 clip）新建。若你的底模加载器输出插槽不是 slot0=MODEL / slot1=CLIP，或想注入其它变体节点，请提前确认。

## 说明
- 多个 ComfyUI 服务器请只把其中一个 `enabled` 设为 true，否则会报错提示。
