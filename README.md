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

插件启动后会在其**数据目录** `data/plugin_data/astrbot_plugin_comfyui_anima/`（AstrBot 数据根下的 `plugin_data/<插件id>/`）下自动创建子目录：

- `temp/`：下载中转，存放 ComfyUI 返回的图片（超过配置 `keep_temp_hours` 小时自动清理）
- `workflow/`：存放工作流 JSON 文件
- `gallery/YYYY-MM/`：文生图/图生图**成品图**永久归档（内容寻址文件名 `sha256[:16].ext`）
- `refs/YYYY-MM/`：图生图**参考图**与用户**收藏图**永久归档
- `gallery.db`：SQLite 索引库（图片元数据 + 语义标签）

> 成品图经过内容寻址后从 `temp/` 移动（os.replace）到 `gallery/`，因此 `temp/` 不会与画廊重复占空间。模型本身不会、也不需要接触任何本地文件路径——发图由插件在本地用 `event.send(Image(file=绝对路径))` 完成。

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
- `image_node`（可选）：图生图时参考图注入的 LoadImage 节点 ID；留空则自动查找工作流里的 LoadImage 节点
- `loras`：LoRA 列表，每项含：
  - `name`：标识名（AI 说“使用 xxx lora”时按此名匹配）
  - `keywords`：触发关键词，逗号分隔（如 `猫娘,catgirl`），出现在提示词里会自动启用
  - `model_name`：ComfyUI 中 LoRA 文件名（含扩展名，如 `xxx.safetensors`）
  - `load_node`：LoRA 加载节点 ID（LoraLoader 节点）
  - `enabled`：默认是否启用
  - `weight`：默认权重，默认 `1.0`
  - `model_input` / `strength_model_input` / `strength_clip_input`：输入框名，一般无需改
  - `presets`：提示词预设（可多套），格式 `[预设名|提示词]`，多套空格隔开，如 `[预设1|solo, 1girl] [预设2|smile]`。调用：`/draw 提示词 --名称/预设名`（名称与预设名之间用 `/` 分隔，以免和 LoRA 名字里常见的 `-` 冲突）。**特殊：若某套预设名填 `0`（如 `[0|always on]`），则该 LoRA 被启用时，这个 `0` 预设的提示词【每次都自动带上】——无论你是否用别的预设、甚至完全不带预设都会生效（已显式指定 `--名称/0` 时不会重复）。适合放该 LoRA 必备、不希望被遗漏的关键词。**

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

### gallery（图片画廊与语义标签召回）
- `enabled`：是否启用图库归档（默认 `true`）
- `max_total_mb`：图库总容量上限（默认 `2048` MB）。超出后按 LRU（最早创建优先）淘汰，但**收藏图与带语义标签的图永不淘汰**
- `cross_session`：跨会话默认可检索（默认 `false`）。关闭时 `/gallery` 与 `comfyui_gallery` 仅能检索到「当前会话」生成的图；开启后所有会话的图都可被检索
- `keep_temp_hours`：`temp/` 中转目录下载副本的保留小时数（默认 `24`），到期清理

## 使用

### 指令
- `/draw 一只白色水手服少女` — 基础绘图
- `/draw 一只猫 --wf sd --lora catgirl:0.8 --w 768 --h 768` — 指定工作流、LoRA、尺寸
- `/draw 一只猫 --安魂曲` — 临时启用名为「安魂曲」的 LoRA（按名字从全局库取模型文件名注入）
- `/draw 一只猫 --安魂曲/预设1` — 启用「安魂曲」并使用其「预设1」提示词；`--安魂曲/0` 显式使用「0 预设」。若某 LoRA 配了名为 `0` 的预设，则它只要被启用就会自动带上该预设，无需显式指定。
- `/loralist [--wf 名称]` — 列出 LoRA
- `/loraon 名称 [--wf 名称]` / `/loraoff 名称 [--wf 名称]` — 持久启用/禁用 LoRA
- `/queuestatus [--wf 名称]` — 查看队列和你前面的排队位数
- `/workflows [set 名称]` — 列出/设置默认工作流
- `/drawhelp` — 帮助（也可直接说「画画帮助」「作图帮助」「绘图帮助」「绘画帮助」等触发）
- `/img2img 描述 [--wf 工作流] [--lora 名称[:权重]] [--w 宽] [--h 高] [--seed 数字]` — 图生图：先在消息里附带一张参考图，再写变换描述，如 `/img2img 把背景换成星空`

#### 中文绘图指令（「画」系）

除 `/draw` 外，插件提供一组更口语化的中文触发词，专为中文用户免记斜杠命令设计：

- 触发词（任选其一）：`画`、`绘图`、`绘画`、`生图`、`画图`、`作画`、`画画`
- 语法：`触发词 [工作流名] 提示词 [...]`
  - 不带工作流名 → 用默认文生图工作流，如 `/绘图 一个女孩` 或 `画画 一只猫`
  - 带工作流名（以空格分隔）→ 用指定工作流，如 `/画 真人 一个女孩`
  - 消息或回复里带了图片 → 自动切换为图生图模式
- 工作流名校验：首 token 长度 ≤10 且不是已有工作流时，会回复「找不到名为「xxx」的工作流」并列出全部可用工作流；首 token 长度 >10 则视为提示词用默认工作流出图
- 其余参数（`--lora` / `--w` / `--h` / `--seed` / `--denoise` / `--wf` 等）与 `/draw` 完全一致
- 误触发规避：触发词必须后接空格才视为指令（`画风成熟点，再来` 这种闲聊不会触发画图）

### AI 对话
直接对机器人说，例如：
> 画一只在雨中奔跑的白色水手服少女，使用 catgirl lora

AI 会调用 `comfyui_draw` 工具，并按你提到的 LoRA 名称启用对应 LoRA。Anima 工作流下，中文描述会先被翻译成 Danbooru 标签。

**图生图**：在消息里附带一张图片并对机器人说变换需求（如「把这张图变成油画风格」「图生图：转绘成动漫风」），AI 会调用 `comfyui_img2img` 工具，把图片作为参考图重绘；若只发文字则走普通 `comfyui_draw`。

> **尊重用户取消/拒绝**：当用户明确表示不要发图、取消、停止或不需要画图（如「不用画了」「别画了」「算了别发了」「取消」「先别发了」）时，AI 会尊重用户，**不再调用画图工具**、也不会发图，仅用文字回应。判断以用户当前消息的明确意图为准，对话历史里画过图不等于当前还要画。

> **图生图工作流命名建议**：为图生图单独配置的工作流，建议把 `name` 命名成带「图生图」字样（如 `xx图生图`）。AI 在选图生图工作流时会**优先匹配名字带「图生图」的**；若没有任何可用图生图工作流，会直接回复"没找到对应的画图流程"。

### 图片画廊与语义标签（gallery）

生成的成品图、图生图参考图、以及用户在聊天里发来/收藏的图，都会被永久归档到 `gallery/`（成品）与 `refs/`（参考图/收藏图），并用 SQLite 索引。

**指令 `/gallery`**
- `/gallery list [n]` — 列出最近 n 张（默认 10）
- `/gallery search <关键词>` — 按提示词 LIKE 检索
- `/gallery tag [图] <标签...>` — 给图打语义标签（不指定「图」则默认指向当前/上一条消息的图）
- `/gallery findByTag <标签>` — 按标签召回（命中多张列出让你选）
- `/gallery send <序号或sha前几位>` — 发图（使用次数 +1）
- `/gallery star <sha前几位>` / `/gallery unstar <sha前几位>` — 收藏/取消收藏（收藏图永不淘汰）
- `/gallery del <sha前几位>` — 删除（收藏图不可删）
- `/gallery save [标签...]` — 收藏当前/上一条消息里的图（支持方案 B：把用户发来的现实照片也存进画廊）
- `/gallery stats` — 统计张数/容量/收藏数/标签数

**AI 对话**
- 召回旧图（生图意图之外的「发以前的图」）走 `comfyui_gallery` 工具，例如：
  > 把我们的合照发我
- 收藏并打标签：
  > 这张是我们的合照，以后我找你要你就发这张
- 模型只负责语义路由（说「要合照」），图片由插件在本地发送；模型全程不接触文件路径。

> 边界：`comfyui_draw` 永远只生成【新】图，绝不复用图库旧图；任何「发已有的图/找某类图/收藏这张」都走 `comfyui_gallery`。

## WebUI 控制台（Anima 控制台）

插件自带 WebUI 控制台（AstrBot 插件页面），包含「配置 / 日志 / 统计 / 图库」四个模块：

- **配置**：可视化编辑插件配置（工作流、服务器、LoRA 等）。
- **日志**：出图记录（谁、什么消息、尺寸/大小/耗时/成败）与运行日志。
- **统计**（v3.4.0 新增）：按用户统计生图数量排行，支持「今天 / 近 3 天 / 近 7 天 / 全部」四个范围；另有近一天的生图数量面积图（按小时分桶）。两个面板都带「刷新」按钮，点击立即重新拉取数据。
- **图库**：浏览、检索、打标签、收藏、管理回收站。

计数口径：只统计**成功生成的成品图**（`source='gen'` 且 `status=0`），失败记录与参考图/收藏图不计入。

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
