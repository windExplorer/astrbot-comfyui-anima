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

通过 AstrBot WebUI 的「插件管理 → 上传 zip」安装本插件（选 `astrbot_plugin_comfyui_anima.zip`），或在 `data/plugins/` 下放入本仓库（文件夹名 `astrbot-comfyui-anima`）。安装后 `requirements.txt` 中的依赖会被自动安装。

插件启动后会在其**数据目录**（由 `StarTools.get_data_dir()` 提供，通常为 `data/plugins/astrbot_plugin_comfyui_anima/`）下自动创建两个子目录：

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

> 这是**可添加多条**的列表：在 WebUI 里点「添加」即可新增一个工作流条目，每条都能展开配置下面的全部字段（含其内部的 LoRA 列表，同样可添加多条）。`comfyui_servers`（服务器）也是同样的可添加多条列表。

每个工作流：
- `name`：工作流名称（指令 / AI 调用时引用）
- `server_name`：绑定的服务器名称；留空则自动用唯一启用的那个
- `is_anima`：是否为 Anima 工作流（是则先翻译中文提示词）
- `workflow_name`：工作流 JSON 文件名（放在插件数据目录的 `workflow/` 下，如 `sd.json` 或 `sd`；只需填名字，不用写全路径，缺 `.json` 自动补）。优先级高于下一项
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

### 其他
- `default_workflow`：默认工作流名
- `draw_timeout`：出图等待超时（秒，默认 120）
- `queue_poll_interval`：轮询间隔（秒，默认 2）
- `return_queue_position`：提交后是否返回排队位置

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

## 说明
- 被禁用的 LoRA 通过把 `strength_model`/`strength_clip` 置 0 实现“无效果”；若想完全不加载该 LoRA，可不把它接入工作流或在 ComfyUI 中旁路该节点。
- 多个 ComfyUI 服务器请只把其中一个 `enabled` 设为 true，否则会报错提示。
