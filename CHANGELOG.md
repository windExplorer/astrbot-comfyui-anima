# 更新日志

本文件记录插件各版本的改动。版本号与 `metadata.yaml` 保持一致。

## v2.1.4

- **修复打包脚本 `build_zip.ps1` 因相对路径导致 `pages/` 目录漏打包的 bug**。原脚本 `Compress-Archive -Path "pages"` 依赖当前工作目录，若脚本在非插件根目录下执行则找不到目录，导致 zip 包中缺失 `pages/`，Dashboard 自然看不到 WebUI 入口。现改为全部使用基于 `$PSScriptRoot` 的绝对路径 + `-LiteralPath`，确保任意位置执行都能正确打包。同时补充了之前漏掉的 `workflow/` 目录。
- 升级后请重新上传 zip 安装，重启 AstrBot 即可在插件卡片上看到「打开WebUI」按钮。

## v2.1.3

- **严格对齐 AstrBot 官方插件页面文档与伴侣插件**，重写 Dashboard WebUI 的桥接与接口协议：
  - 前端改用官方标准用法 `window.AstrBotPluginPage`（无需手动引入 SDK），初始化先 `await bridge.ready()` 再发请求；`apiGet`/`apiPost` 的 endpoint 使用插件内相对路径，由 bridge 自动拼 `/api/plugins/extensions/<plugin_name>/` 前缀并携带鉴权。移除自造的 `window.parent` 回退与裸 `fetch` 回退。
  - 后端返回值统一为 bridge 协议格式 `{"status":"ok","msg","data"}` / `{"status":"error","msg"}`（原自定义 `success` 字段已废弃）。bridge 对 `ok` 自动解包为 `data`，对 `error` 自动 reject。
  - 图库图片接口由二进制 `FileResponse` 改为返回 `data_url`（base64 内嵌 JSON），前端 `img.src = data_url` 直接渲染。原因：bridge 下的 `apiGet` 按 JSON 解包，二进制响应无法被正确处理，伴侣插件同样采用 `data_url` 方案。
  - 详见官方文档：https://astrbot.app/dev/star/guides/plugin-pages.html

## v2.1.2

- **修复 WebUI 在 Dashboard iframe 中打不开/空白的问题**。根因是页面加载时 `AstrBotPluginPage` 桥接对象尚未注入，且 iframe 场景下桥接挂在父窗口。本版对齐伴侣插件 `astrbot_plugin_private_companion` 的做法：
  - `getBridge()` 增加 `window.parent.AstrBotPluginPage` 回退（iframe 中 bridge 在父窗口）。
  - 新增 `waitForBridge()` 轮询等待（最多 2.5s），所有 `apiGet`/`apiPost`/`apiImageBlobUrl` 调用前先等待桥接就绪，避免首屏请求直接走裸 `fetch` 导致 401/404。
  - 初始化流程改为 `await waitForBridge()` 后再 `loadCfg/loadLogs/loadGalStats/galSearch`，确保从 AstrBot 后台打开时能正常携带鉴权加载数据。
- 入口位置不变：AstrBot Dashboard → 「插件」列表 → 点开本插件 → 页面 Tab（即 `/pages/astrbot_plugin_comfyui_anima/anima-console/`）。重装后若仍未出现，请重启 AstrBot 重载插件。

## v2.1.1

- **修复 WebUI 前端无法调用后端 API 的问题**。上一版（v2.1.0）前端错误地使用了 `bridge.fetch()` 接口，而 AstrBot 页面 bridge 实际提供的是 `window.AstrBotPluginPage.apiGet(endpoint, params)` / `apiPost(endpoint, body)`（父窗口自动拼 `/api/plugins/extensions` 前缀并携带 Dashboard 鉴权）。本版重写为正确的 bridge 调用方式，并针对图片 `<img>` 无法携带鉴权头的问题，改用 `apiGet` 拉取二进制再转 `blob:` URL 渲染缩略图。若 bridge 不可用则回退到裸 `fetch('/api/plugins/extensions' + endpoint)` + `localStorage` 中的 `astrbot_token`。
- **说明入口位置**：WebUI 页面入口不在「设置」里，而是在 AstrBot Dashboard 的「插件」列表 → 点开本插件 → 页面 Tab（即 `/pages/astrbot_plugin_comfyui_anima/anima-console/`）。若重装后仍未出现，请确认插件已重新加载/重启 AstrBot。

## v2.1.0

- **新增 Anima 控制台 WebUI（大更新）**。参考 `astrbot_plugin_private_companion` 的页面机制，在 `metadata.yaml` 的 `pages:` 声明 `anima-console` 页面（AstrBot 自动挂载到 `/pages/astrbot_plugin_comfyui_anima/anima-console/`），并新增后端 `webui_api.py` 与前端点 `pages/anima-console/index.html`。控制台包含四个模块：
  - **配置**：在线读取/编辑/保存 `_conf_schema.json` 对应的插件配置（JSON 编辑器，保存即生效）。
  - **日志**：实时读取插件运行日志（内存环形缓冲 + `data_dir/webui.log`，支持自动刷新、行数选择）。
  - **调试**：列出已配置的 ComfyUI 服务器并一键测试连通性（调用 `/system_stats`，附带节点数）。
  - **图库**：图库统计（成品/参考/用户/收藏/带标签数量与体积）、关键词+类型+仅收藏检索、缩略图预览、点击放大、收藏/取消收藏、删除。
- 在 `main.py` 安装日志镜像 handler（环形缓冲 + 滚动文件），并在 `initialize` 注册、`terminate` 卸载 WebUI 路由。
- 打包脚本新增 `pages/` 与 `webui_api.py`、`image_store.py` 进包（之前 zip 漏打包 `image_store.py`）。

## v2.0.1

- **打包流程加固：禁止撞版发布**。在 `build_zip.ps1` 中新增版本重复检测——若 `dist/` 下已存在同一版本号的 zip（如本次的 `astrbot_plugin_comfyui_anima_v2.0.1.zip`），脚本直接报错退出，强制开发者先升版本号再打包，避免"改了配置/代码却用旧版本号重打、与已发版本撞版"的问题（此问题曾在 v2.0.0 配置样式修正时复现）。规则：任何改动在打包前都必须先在 `metadata.yaml` 升版本号、并在 `CHANGELOG.md` 追加对应条目。

## v2.0.0

- **重大版本：新增 SQLite 图库（gallery）与语义标签召回**：把生成的成品图、图生图参考图、以及用户在聊天里发来/收藏的图，按内容寻址（sha256[:16]）永久归档到数据目录下的 `gallery/YYYY-MM/` 与 `refs/YYYY-MM/`，写入 SQLite（`gallery.db`）便于检索。本次升级为 2.0.0 主要即因引入此全新能力模块。
  - 新增 `image_store.py`：`ImageStore` 负责建表、`archive_image`/`archive_user_image`（移动转正、内容寻址去重）、标签增删查、按提示词 LIKE 检索、按语义标签召回、LRU 容量淘汰（收藏/带标签图永不淘汰）。
  - `_conf_schema.json` / 配置页新增 `gallery` 块：`enabled`、`max_total_mb`、`cross_session`、`keep_temp_hours`。
  - `_do_draw` 落盘后自动归档成品图（补采 LoRA 列表、真实宽高、seed、denoise、图生图参考图 sha256）；图生图参考图同时归档到 `refs/`。
  - 新增指令 `/gallery`：`list` / `search` / `tag` / `findByTag` / `send` / `star` / `unstar` / `del` / `save` / `stats`。`tag`/`save` 不指定图时按「上条消息的图 > 本会话生成 > 本会话收到」指代消解取「这张图」；`findByTag`/`recall` 命中多张时列出让用户选。
  - 新增 LLM 工具 `comfyui_gallery`（mode: recall/search/save/send/list/stats），支持「把我们的合照发我」「收藏这张，标签叫合照」等自然语言。模型只做语义路由，图片由插件本地 `event.send(Image(file=绝对路径))` 代发，模型全程不接触文件路径。
  - 明确边界：`comfyui_draw` 永远只生成新图、绝不复用旧图；发旧图/找某类图/收藏走 `comfyui_gallery`。`comfyui_gallery` 已加入 `required: ["mode"]`，避免空参数调用。
  - `temp/` 仅作下载中转，成品图经 `os.replace` 移动转正，不重复占空间；`_cleanup_temp` 顺带按 `keep_temp_hours` 与 LRU 触发清理。
  - 详见 `README.md` 的「图片画廊与语义标签（gallery）」章节。
  - 配置样式修正：`_conf_schema.json` 的 `gallery` 配置块补全 `hint` 字段（区块说明与子项说明），并将子项的长说明从 `description` 移到 `hint`，对齐 AstrBot dashboard（V4 渲染）规范——`description` 仅作短名称、`hint` 作说明文字，避免面板里该区块说明缺失、子项名称过长的问题。

## v1.2.5

- **精简 LLM 绘图工具的可见参数，降低模型吐畸形/空 JSON 概率**：`llm_tool` 的 JSON schema 完全由 docstring 的 `Args:` 段生成，而 deepseek 等模型在参数较多（尤其 `loras` 数组、`image` URL）时极易产出空串或非法 JSON，被 `openai_source` 兜底成 `{}` 并报「解析参数失败」。现从 docstring 中移除非核心参数（width/height/loras/seed/source/image/denoise），仅保留 prompt/negative_prompt/workflow/img2img_workflow，这些高级项改由函数默认值与内部逻辑处理。同时给 `prompt` 加上「必填、不要包裹自然语言」的强约束措辞。配合 v1.2.3 的 `event.message_str` 兜底与 v1.2.4 的 schema `required` 补丁，三重保障。
- 注意：函数签名保持不变（参数均带默认值），仅收缩了暴露给模型的 schema，内部逻辑不受影响。

## v1.2.4

- **根治 LLM 工具「参数传不进去」问题（核心修复）**：顺着 AstrBot 源码定位到根因——`llm_tool` 装饰器仅靠 docstring 生成 schema，不会标记 `required`，导致 `comfyui_draw`/`comfyui_img2img` 的全部参数对模型都是可选的；模型常以空 `{}` 调用工具，且模型返回的非合法 JSON arguments 被 `openai_source` 兜底成 `{}`，最终 `prompt` 永远为空、反复失败。现于插件 `initialize()` 中遍历全局 `llm_tools`，手动给 `comfyui_draw`/`comfyui_img2img` 的 schema 补 `required: ["prompt"]`，强制模型必填 prompt。配合 v1.2.3 的消息文本兜底，双层保障。
- 注：此前 v1.2.2 给 `prompt` 加默认值无效，正是因为 schema 由 docstring 生成、不读签名默认值；本版从 schema 层修复。

## v1.2.3

- **`comfyui_draw` / `comfyui_img2img` 增加 prompt「参数空洞」兜底**：日志发现模型有时把画面描述写进思考链却未填入 tool_call 参数，导致 LLM 反复以空 `{}` 调用工具、陷入「空参数→报错→重试→空参数」死循环。现当 `prompt` 为空时，自动从用户原始消息文本（`event.message_str`）兜底取描述（剥离 `/draw`、`/img2img` 等触发词），仅在消息也无内容时才返回友好提示。真人自然语言对话（如「画一只猫」）即使模型忘记填参数也能正常出图。

## v1.2.2

- **修复 LLM 工具 `comfyui_draw` / `comfyui_img2img` 因 `prompt` 无默认值而报错 `missing 1 required positional argument: 'prompt'`**：AstrBot 的 LLM 工具框架在按位置/JSON schema 绑定参数时未能正确传入 `prompt`，导致 AI 对话触发绘图直接失败。现给两个工具的 `prompt` 补上默认值 `""`，并在函数体开头校验为空时返回明确的友好提示（而非崩溃）。`comfyui_img2img` 同样存在该隐患，一并修复。

## v1.2.1

- **修复 LLM 工具（AI 对话）出图后用户看不到图**：`comfyui_draw` / `comfyui_img2img` 原本在原生对话下仅把图片节点 `return` 给 LLM 工具框架，而 LLM 工具的返回值只会作为文本结果回传给模型、不会被框架渲染成图片发给用户，导致生图成功但聊天里没有图。`/draw` 指令因走命令管线 yield 节点能正常出图，暴露出两条路径的不一致。修复：原生对话（不带 `source`）时改为主动 `await event.send(...)` 把图真正发到聊天，再回一句中性文本给模型。带 `source` 的伴侣插件场景维持 JSON 返回路径由调用方发图，不重复发送。
- **LLM 生图成功/失败的话术改为中性事实、交由模型按人格回复**：出图成功后原本写死「图片已生成，请查看上面奴家发出来的图~」、失败后写死「呜…这次画图好像出了点小状况…」等卖萌提示，等于插件替模型说话、人格被固化。现改为中性事实陈述（如「绘图已完成，图片已发送给用户。请根据你的人设自然回复用户」），让模型依据自身人设组织回复；同时不再回传本地路径等内部信息给模型，避免经模型转述泄露给用户。

## v1.2.0

- **新增：出图完成后的贴心小报告**：图片生成并发送后，会用随机的萌系口吻向用户汇报本次出图的「文件生成时间 / 像素尺寸 / 文件大小 / 生图耗时」。共 6 条文案随机取一（如「好啦好啦，画好咯…尺寸 768×768、文件 1.2 MB，耗时 12.3 秒～」），避免每次一个样。
  - 像素尺寸优先用 Pillow 读取真实图片；环境未装 Pillow 时自动降级为本次请求的宽高（新增可选依赖 `Pillow>=10.0.0`，缺失不报错）。
  - 报告发送失败不会影响已生成的图，仅记一条 warning 日志。

## v1.1.9

- **修复 AI 对话路径（`comfyui_draw`）的"误翻历史图"回归**：v1.1.7 移除了 `_extract_images` 的历史兜底，但为 `comfyui_draw`（AI Agent 自主 tool_call 主路径）保留了"LLM 要求图生图时回退 `g_last_generated`/`g_last_received`"的兜底。该兜底在 AI 对话场景会跨消息残留——只要会话中途发过图、或本插件刚生成过图，LLM 一旦触发图生图参数，就从历史里捞图，导致两个典型问题：
  - 纯续画（如"再来一张"）被误判为图生图，反过来找用户要参考图；
  - 工作流被错误切到 `default_img2img_workflow`（如"动漫"突然变成"真人"图生图）。
  - 修复：彻底删除 `comfyui_draw` 中基于历史缓存的兜底取图。`is_img2img` 现在**仅**由"本次请求真正携带的图"决定——即 LLM 传入 `image` 参数、或本次消息/引用里附带图片。除非用户明确要求图生图，否则一律按文生图处理，不翻任何历史消息。

## v1.1.8

- **强化图生图取图校验，规避"每次下载都失败"**：`_image_to_local_path` 在原有"路径存在"校验之上，新增**图片有效性校验**（非空 + 合法扩展名或文件头魔数 PNG/JPEG/WEBP/GIF/BMP）。此前 `convert_to_file_path` 有时会把"下载到错误页/空文件"也当成成功返回路径，导致上层误判 `got_explicit_image=True`、进而跳过当前消息里真正可用的图、最终落到历史兜底。现在此类无效文件一律视为下载失败并回退到消息内真图。
- **工具描述新增"禁止 get_message_detail 回拉原始消息"提示**：在 `comfyui_img2img` 工具 docstring 明确告知调用方——若用户已在当前消息里附带图片，直接把该图传入即可，**不要**调用 `get_message_detail` 之类接口去回拉"原始消息"再重新下载图片：回拉到的原始图片 URL 通常无法在本机直接下载（带签名时效/内网地址），既耗时又必然失败，而当前消息里的图已可直接使用。
  - 注：`get_message_detail` 的调用本身发生在伴侣插件 `astrbot_plugin_private_companion` 侧，本插件无法从根源禁止，仅能通过工具描述约束其 Agent 行为；伴侣插件侧不做改动。

## v1.1.7

- **修复 v1.1.6 引入的回归：纯文生图指令被误判为图生图**。根因是 `_extract_images`（取图方法）里原本内置了「引用消息取不到图时回退本插件历史生成图 `g_last_generated`」的兜底，而该方法被所有指令共用（含 `/画`、`/draw` 等纯文生图）。导致 `/画动漫 美女` 这类无图指令也拿到了 5 张旧生成图，`is_img2img` 被误判为真、整张画变成图生图并可能被吞事件。
  - 修复：从 `_extract_images` 移除该兜底，使其**只返回消息内真实存在的图片**（消息内 / 引用内 / 卡片），绝不回退历史图；
  - `g_last_generated`/`g_last_received` 兜底改为**仅限图生图意图入口**生效：`/img2img` 指令、`comfyui_img2img` 工具、`comfyui_draw`（当 `want_img2img`）且 `init_images` 为空时。
  - 纯文生图（`/画`、`/draw`、Agent 文生图）从此不再受历史图污染。

## v1.1.6

- **图生图贯彻「大模型不读图」原则**：图生图的参考图是直接作为像素喂给 ComfyUI 的 LoadImage 节点的，大模型不需要、也不应该去"理解/描述"参考图内容；它只负责把用户的变换意图翻译成英文 prompt。因此当 `image` 参数（LLM 传入的参考图 URL）已成功取到图时，`comfyui_img2img` / `comfyui_draw` 的图生图分支**不再去 event / last_event 做无谓的兜底探测、也不再混入 `g_last_generated`（本插件历史生成图）**，避免把用户上几次生成的旧图一起塞进重绘造成结果污染（此前日志出现「注入 3 张参考图」即源于此）。同时在工具 docstring 中明确告知模型：不要浪费步骤调用视觉转述/读取图片内容。

## v1.1.4

- **修复图生图取不到引用图片（平台临时图被清理）**：Agent 做图生图时常传入平台压缩后的临时图路径（如 `/AstrBot/data/temp/compressed_xxx.jpg`），但工具执行时该临时文件已被平台清理删除，导致取图失败、提示"请先发送参考图"。
  - 新增「本插件最近生成的图」兜底（`g_last_generated`）：本插件每次生图成功都记录真实大图路径，引用本插件生成的图做图生图时直接回退到真实路径。
  - 新增「会话最近收到的图」兜底（`g_last_received`）：在 LLM 工具调用前（`_capture_llm_event`）趁图片尚未被剥离/清理时提前缓存用户发来的图路径，引用自己发的图做图生图时回退使用。
  - `comfyui_draw` / `comfyui_img2img` 在事件与参数均未取到图时，依次回退 `g_last_generated` → `g_last_received`。

## v1.1.3

- **根治与伴侣插件共存时的重复发图**：`comfyui_draw` / `comfyui_img2img` 工具不再在工具内部调用 `event.send` 自发生图。改为统一返回结果，由调用方（或 AstrBot 框架）负责发送：
  - 伴侣插件通过 proactive 管道调用（带 `source: "我会永远陪着你"`）时，返回 `{"image_path": ...}` JSON 文本，由伴侣插件解析并自行发图；
  - 原生对话或伴侣 Agent 自主 tool_call（不带 source）时，直接返回图片节点，由框架渲染给用户。
  这样无论哪种调用方，生图只走本插件一次、发图只由调用方完成，彻底消除重复出图。

## v1.1.2

- **新增 `enable_llm_tools` 配置开关**：关闭后 LLM 对话不再自动调用画图工具，仅支持指令画图，避免与伴侣插件等第三方插件共存时双重大模型触发导致重复出图。

- **修复 `enable_llm_tools` 开关误伤伴侣插件**：关闭开关后，伴侣插件等第三方通过 tool_call 主动调用（带 `source` 标记）不再被拦截。

## v1.1.1

- **修复 `llm_img2img`（图生图工具）重复发送图片**：此前只有 `llm_draw`（文生图）在伴侣插件调用时跳过 `event.send` 避免重复发图，但 `llm_img2img` 函数签名缺少 `source` 参数且发图处没有拦截逻辑，导致伴侣插件调用图生图时图片被重复发送。现对齐两个工具的行为：`llm_img2img` 新增 `source` 参数，发图前同样判断是否为伴侣插件调用，若是则跳过 `event.send`，由伴侣插件负责发图。

## v1.0.73

- **新增 denoise（降噪幅度/重绘强度）支持**：图生图时控制输出偏离原图的程度。
  - **工作流配置**：新增 `default_denoise` 字段（0~1，默认 -1 表示不注入/沿用工作流原始值）。
  - **指令参数**：`/draw`、`/img2img`、`/画xxx` 均支持 `--denoise 0.7`。
  - **LLM 工具**：`comfyui_draw` 和 `comfyui_img2img` 均新增 `denoise` 参数，大模型可根据用户意图自动调整：说"微调"用低值（0.4~0.6），说"大改/风格转换"用高值（0.7~0.9）。
  - **实现**：`workflow_builder.set_denoise` 遍历所有采样器节点注入 denoise 值。

## v1.0.72

- **修复引用图片（Reply）取不到的致命 Bug**：`_extract_images` 用 `isinstance(comp, Reply)` 判断 Reply 组件，但生产环境中 `from astrbot.api.message_components import Reply` 可能导入失败（`Reply = None`），导致即便日志显示 `ComponentType.Reply` 且有内嵌 Image，也永远进不了 Reply 分支。改为用 `comp.type` 属性（字符串 `"Reply"`）判断，不依赖类引用。
  - 同时修复了 `Image` 和 `CardImage` 的同类隐患：`isinstance` 失败时以 `comp.type` 兜底。
- **`llm_img2img` 工具补齐 `image` / `img2img_workflow` 参数**：此前只给 `comfyui_draw` 加了这两个参数，`comfyui_img2img` 漏了，导致 LLM 传 `image` 时直接报 `unexpected keyword argument`。现在两个工具的接口一致，LLM 调哪个都能正常工作。

## v1.0.71

- **修复 `/drawhelp` 文本中的中文弯引号导致加载报错**：字符串内嵌的 `"` `"` 被解析器误识别为 Python 定界符，改为单引号包裹。

## v1.0.70

- **LLM 工具 `comfyui_draw` 全面支持图生图**：新增 `image` 参数（参考图 URL）和 `img2img_workflow` 参数（图生图专用工作流名）。LLM 调用时传入 `image` 即可触发图生图模式，无需切换 `comfyui_img2img` 工具。图片来源同时支持：`image` 参数 URL 下载、事件自动提取（消息附带/引用回复图片）、兜底原始事件回退，三者合并并自动去重。
  - 工作流选择优先级：`image` + `img2img_workflow` 双指定 → 走对应图生图工作流；只传 `image` → 语义匹配 `workflow` 参数，未指定则走图生图默认工作流；不传 `image` → 纯文生图。
- **分离默认工作流**：新增配置项 `default_img2img_workflow`，图生图时独立指定默认工作流。文生图仍用 `default_workflow`。所有绘图入口（`/draw`、`/img2img`、`/画xxx`、`llm_draw`、`llm_img2img`）均正确传递 `is_img2img` 标志。
- **工作流名匹配增强**：`_resolve_workflow` 新增按文件名（`workflow_name`）回退匹配，支持精确文件名、带/不带 `.json` 后缀三种情况。解决 LLM 把工作流文件名（如 `sd.json`）误当工作流名称（如 `默认` 或 `真人图`）传入的问题。LLM 工具描述也增加了提示说明。
- **`/workflows` 命令增强**：新增 `set_img2img 名称` 子命令，可在聊天窗口设置图生图默认工作流；列表视图同时显示文生图/图生图两个默认标记。
- **修复 `_resolve_workflow` 错误信息**：工作流未配置时的报错文案从「未配置任何 ComfyUI 服务器」修正为「未配置任何工作流」。

## v1.0.69

- **修复 ComfyUI 提交 prompt 返回 400 错误**：图生图注入 LoadImage 节点时，错误地把图片引用设为 `[name, subfolder, type]` 列表格式——这是 ComfyUI 节点连线的内部引用格式，不是 LoadImage `image` 输入的正确值。LoadImage 的 `image` 输入应为**字符串（文件名）**。列表格式导致 ComfyUI 在验证工作流时拒绝接受（400 Bad Request）。改为直接传入 `upload_image` 返回的 `name` 字符串。

## v1.0.68

- **修复取图永远为空的致命 Bug**：`_extract_images` 一直用 `getattr(event, "message_components", None)` 来读消息组件链，但 `message_components` **不是** `AstrMessageEvent` 的属性（正确属性是 `event.get_messages()` 返回的 `message_obj.message`），导致 `comps` 永远是空列表 `[]`，无论用户发的是直接图片、引用图片还是卡片图片，取图都返回空。改为 `event.get_messages()` 后，aiocqhttp 适配器正确解析的 `Image`/`Reply` 组件链会被正确读到。

## v1.0.67

- **修复「画真人图 + 引用图片」不触发图生图**：根因是 `画` 系指令（`cmd_draw_wf`，正则 `^[/／]?画\S*`）与 `/draw` 指令调用 `_do_draw` 时根本没传 `init_images`，只有 `/img2img` 才会取图，导致用户用「画真人图 + 引用图片」这种自然语言方式时被当作文生图直接画出、参考图节点丝毫未改。
  - 现在 `cmd_draw_wf` 与 `/draw` 也会在绘图前调用 `_extract_images(event)`：若消息或引用(回复)里带了图片，则自动按图生图处理（参考图注入 LoadImage 节点）；无图则照旧文生图。无需用户强写 `/img2img`。
  - 因此「画真人图 + 引用图片」「/draw 猫 + 图片」等组合现在都能正常图生图，且全程带 `[取图]` 诊断日志（此前这类路径连 `[取图]` 日志都不会出现，正是排查盲区）。

## v1.0.66

- **修复图生图「图片没传上去 / 参考图节点仍是原值」**：根因是 `_extract_images` 返回空，`if init_images:` 分支整段被跳过（既不上传也不注入）。加固点：
  - 新增 `on_using_llm_tool` 钩子，在 LLM 工具调用前捕获**完整原始事件**（图片组件尚在），并由 `comfyui_img2img` 在「工具 event 取不到图」时回退到该原始事件再取一次，解决「工具被调用时 event 里的图片已被 LLM 消费/剥离」的常见坑。
  - `_image_to_local_path` 解析后新增 `os.path.exists` 校验：平台只给「裸文件名」而非本地路径时不再误当成本地文件上传，并明确打日志。
  - 取不到任何图时，把每个消息组件的 `repr` 打进日志，便于区分「event 里压根没图」还是「图解析失败（url/file/path 值是什么）」。
  - 说明：注入只在提交给 ComfyUI 的内存 `prompt` 里做，不会修改磁盘上的工作流 `.json` 文件，因此查看原文件看到「节点还是原来的」是正常现象。

## v1.0.65

- **图生图取图改为多来源 + 详细日志**：`_extract_images` 现在支持从多种渠道获取参考图，不再是单一的「消息内直接附带图片」。
  - 来源覆盖：① 消息中直接附带的图片（含「文字 + 图片」混合、`/img2img 描述 + 图片`）；② 引用/回复消息里的图片（先读 `Reply.chain` 内嵌的 `Image`，若引用只含占位符再用 AstrBot 内置 `quoted_message_parser.extract_quoted_message_images(event)` 按 `reply.id` 走平台 API 回退拉原图）；③ 卡片图片 `CardImage`。
  - 单张图 `convert_to_file_path` 失败或无 `url`/`file` 时，回退到 `path` 字段；兼容 `data:image/...;base64,` 形式并剥离 `file:///` 前缀；多来源自动去重。
  - 取图全程打日志：打印消息组件清单、引用消息 id 与链内组件数、每张图的成功来源与本地路径、失败时的 `url/file/path` 实际值，以及最终取得数量；取不到任何图时明确提示，便于排查「发了图却说没收到」的问题。该内置 API 在旧版本缺失时自动降级为空，不影响主流程。

## v1.0.62

- **`comfyui_draw` 新增 `source` 来源参数，支持伴侣插件专属格式化**：`source` 命中「我会永远陪着你」时，对传入的整段提示词启用专属处理（`_format_companion_prompt`）——按 `Negative prompt:` 拆分正/负向后，正向只抽取「用户原始诉求(user request)」与「构图连续性([Composition and continuity])」两块标准内容，负向保留标签并去除 `Do not ...` 元指令，同时过滤掉时间/日程/位置/情绪等无关事实、分节标题、`[section compacted]` 与 `dup` 等截断占位符；其它来源或留空仍走通用拆分清洗(`_split_external_prompt`)。
  - 伴侣插件侧需在「自定义生图工具额外参数(extra_params)」配置 `{"source": "我会永远陪着你"}` 即可触发。

## v1.0.61

- **修复伴侣插件传入提示词「正向/负向混在一起 + 含无效标记」**：`astrbot_plugin_private_companion` 的 tool_call 生图会把整段（含 `Positive prompt:` / `Negative prompt:` 段落、`[section compacted]` 占位符、`[User image request]` 等分节方括号标题）塞进单个 `prompt` 参数。原逻辑把它整体当正向、负向留空，导致负向内容（如 `cropped head, nsfw ...`）混入正向、且方括号被当成 prompt-editing 语法扰乱生成。
  - 新增 `_split_external_prompt`：按 `Negative prompt:` 拆分正/负，去掉 `Positive prompt:` 标签，清除 `[section compacted]` 与含空格的方括号分节标题，并压缩空白。
  - `llm_draw` 现先拆分清洗再分别传入 `_do_draw` 的正/负向（负向优先用拆分出的，否则回退到 `negative_prompt` 参数）。未含 `Negative prompt:` 标记的普通提示词原样透传，不影响 `/draw` 与常规 AI 对话。

## v1.0.60

- **修复 `comfyui_draw` 被 `astrbot_plugin_private_companion` 解析不到图片**：伴侣插件的生图后端（tool_call）会 `await` 调用本工具，并把返回值 `str(result)` 解析为图片路径或图片数据（优先按 JSON 找 `image_path`/`path` 等键，否则正则匹配路径/URL/base64）。此前工具 `return` 的是俏皮文本，故解析失败。
  - `_do_draw` 现以 `(图片节点, 本地绝对路径)` 元组产出；`/draw` 与「画」系指令解包后只 `yield` 节点，行为不变。
  - `llm_draw` 取出本地路径，以 `json.dumps({"image_path": <绝对路径>, "status": "ok"})` 返回，显式带 `image_path` 键供伴侣插件按 JSON 解析为图片（直接返回 Windows 路径字符串会被正则只截到文件名，解析失败，故用 JSON）。
  - 伴侣插件传入的是合成事件（无真实平台），`llm_draw` 内的 `event.send` 会失败，现已忽略该异常，图片仍通过 `return` 交回给它解析；原生对话里图片照常 `event.send` 展示，工具结果文本交给 LLM。

## v1.0.59

- **修复 `comfyui_draw` LLM 工具被第三方插件 `astrbot_plugin_private_companion` 主动生图时崩溃**：原工具是异步生成器（`async def ... yield`），伴侣插件用 `await handler(...)` 调用会报 `object async_generator can't be used in 'await' expression`。现改为普通协程，遍历 `_do_draw` 产出的图片节点并主动 `event.send` 发出，两种调用方（AstrBot 原生工具管线 / 第三方 `await`）均兼容。文本类提示本就由 `_do_draw` 经 `_send` 直发，不受影响。
- **打包改进**：`build_zip.ps1` 产物文件名带版本号（如 `astrbot_plugin_comfyui_anima_v1.0.59.zip`）并统一放入 `dist/` 目录，不再覆盖历史已打的包，便于归档回滚。

## v1.0.58

- 移除「画」系指令（`cmd_draw_wf`）的临时诊断日志（`logger.info("[cmd_draw_wf] 收到消息...")`）。功能与 v1.0.57 完全一致，仅清理调试输出。

## v1.0.57

- **修复「画」系指令无法识别（如 `/画真人`）**：原 `@filter.regex(r"^[/／]画\S*")` 强制要求前导斜杠，而 AstrBot 的 regex 过滤器匹配的是去除前导 `/` 后的文本，导致 `/画真人` 匹配不到。改为斜杠可选 `^[/／]?画\S*`，并在解析时 `lstrip` 统一触发词，无论是否带 `/` 都能正确提取工作流名。

## v1.0.56

- **新增「画」系中文绘图指令（新增指令，非 `/draw` 别名）**：
  - `/画<工作流名> 提示词` 用指定工作流作画，如 `/画真人 一个女孩`；
  - `/画` `/绘图` `/绘画` `/画图` `/画画` + 提示词 用默认工作流，如 `/绘图 一个女孩`；
  - 找不到指定工作流时俏皮提示并退回默认工作流；只写触发词没给提示词时撒娇提醒。
  - 与 `/draw` 并存、互不冲突，并复用其参数解析（`--lora`/`--w`/`--h`/`--seed` 等，`--wf` 优先级最高）。
  - `/drawhelp` 已补充分类说明。

## v1.0.55

- **文档/注释修正：预设分隔符统一为 `/`（非 `-`）**。正确语法是 `--名称/预设名`（如 `--安魂曲/预设1`），代码 `_parse_draw_args` 自 v1.0.48 起即用 `/` 分隔（以免和 LoRA 名字里常见的 `-` 冲突）。此前 v1.0.54 在 README、`_conf_schema.json` hint、`/help` 文本与代码注释里误写成 `--名称-预设名`/`-`，本次全部改为 `/`，与代码实际行为一致。

## v1.0.54

- **文档补全：常驻预设 `0` 功能说明**。v1.0.52 新增的「名为 `0` 的预设每次都自动带上」之前只在代码与变更日志里，用户无感知。本次：
  - `_conf_schema.json` 的 `presets` 字段 hint 增加该说明（配置界面即可看到）；
  - `README.md` 的「LoRA 库字段」与「指令」两处补充 `--名称/预设名` 用法及常驻预设 `0` 的解释。

## v1.0.53

- **移除「🎨 本次启用 LoRA」回显消息**：出图时不再向用户发送「本次启用 LoRA」的提示消息。启用信息仍完整记录到后台日志（`[LoRA] 启用 名称 → 文件名`），便于排查，但不再打扰用户。

## v1.0.52

- **新增「常驻预设」：名为 `0` 的预设每次都带上**。LoRA 预设里若配置了名字为 `0` 的预设（如 `[0|...]`），则该 LoRA 被启用时，无论用户是否用别的预设（`--名称/预设名`）甚至完全不带预设，这个 `0` 预设的提示词都会自动追加。已显式指定 `--名称/0` 时不会重复追加。
  - 实现：在 `active_map` 算定后，收集所有被启用 LoRA 中名为 `0` 的预设，调用既有 `_apply_lora_presets` 追加，并重新写入正向提示词节点（因提示词在更早位置已写过一次）。
  - 已用等价实现复现验证三种场景：纯 `--安魂曲`（带 0）、`--安魂曲/1`（1 与 0 都带）、`--安魂曲/0`（0 仅一次）。

## v1.0.51

- **配置文案修正**：全局「LoRA 库」里的 `model_name` 字段，显示名称由「ComfyUI 文件名（别名）」改为「模型文件名」，提示文案同步说明「/draw --名称 就是按这个名字去 ComfyUI 加载对应的 LoRA 文件」。字段代码 key 不变（仍是 `model_name`，避免破坏兼容）；仅改配置界面显示与说明，不影响功能。

## v1.0.50

- **修复 `--名称` 临时启用 LoRA 时「节点: 无 / 最终启用: 无」（核心根因修复）**：
  - 根因：`apply_loras` 只遍历「工作流配置的 `loras_config`」来注入/改写的 LoRA。若工作流没在 `loras_text` 里引用该 LoRA（即 `loras_config` 为空），即便用户 `--安魂曲` 请求了，循环也不执行，节点永远加不进去。之前看起来「`--安魂曲/1` 正常」只是预设文本让图变了样，节点其实同样没加（两种命令日志都是 `LoraLoader 节点: 无`）。
  - 修复：在调用 `apply_loras` 前，若 `--名称` 请求的 LoRA 不在工作流的 `loras_config` 里，则自动从**全局 LoRA 库**补全完整配置（含真实 `model_name`、权重、`model_only`），使 `apply_loras` 能据此新建节点。这样纯 `--安魂曲`（不带预设）即可直接启用，无需在工作流里预先引用。
  - 已用真实模块复现验证：工作流无 LoraLoader 节点、`loras_config` 仅含「安魂曲」一项时，`apply_loras` 成功新建 `LoraLoaderModelOnly` 节点并正确接入底模链路（`lora_name=anhunqu.safetensors`、`strength_model=1.0`）。
  - 兜底提示：若 `--名称` 请求的 LoRA 在全局库也找不到，会明确告警「请先在全局 LoRA 库配置并填好 model_name」。
  - 注意：注入新节点依赖底模锚点探测（优先 CheckpointLoader 类节点），标准 Anima 工作流一般可自动探测到；若探测失败日志会提示在工作流配置填 `lora_anchor`。

## v1.0.49

- **`/draw` 增加「本次启用 LoRA」回显**：跑完会直接告诉你启用了哪些 LoRA、加载了哪个 `.safetensors` 文件（未配置 `model_name` 时明确告警「节点沿用工作流默认文件，可能不是该 LoRA」）。这样纯 `--名称`（不带预设）是否真的生效一目了然，不再只能靠看图猜。
- **排查结论（重要）**：经真实模块复现验证，`--安魂曲` 与 `--安魂曲/预设` 对 LoRA 节点的处理**完全对称**——两者产出的启用请求都是 `{安魂曲: None}`，差异仅在于 `/预设` 会额外往提示词追加预设文本。`--名称/预设` **只改提示词、不改变 LoRA 文件加载**；文件由配置 `model_name` 决定，与是否带预设无关。若你觉得「纯 `--安魂曲` 没加上、带预设却正常」，多半是 `model_name` 没填：节点会沿用工作流默认文件（两种命令都一样），而带预设时因为提示词变了图「看起来像加了」。请检查 安魂曲 这一项的 `model_name` 是否填了真实文件名。

## v1.0.48

- **进一步增强 `--名称` 简写对「带版本/后缀 LoRA 名字」的匹配（更健壮）**：
  - v1.0.46 仅用 `名称-` / `名称_` 前缀匹配，仍漏掉 `安魂曲1`、`安魂曲_v1`、`安魂曲 v1`、`安魂曲V1` 这类没有 `-`/`_` 分隔、或用了别的后缀的写法。
  - 新增 `_lora_name_matches` / `_normalize_lora_name`：匹配支持「精确 / 对称前缀（带 `- _ 空格 （ ( 【 [` 分隔，命令短名与配置全名双向互通）/ 去掉末尾版本号后缀后相等」三种方式。`/draw 1girl --安魂曲` 现在能命中配置名为 `安魂曲-1`、`安魂曲1`、`安魂曲_v1`、`安魂曲 v1`、`安魂曲V1`、`安魂曲` 等任意写法；反向（`--安魂曲-1` 命中配置短名 `安魂曲`）也成立。
  - 已用真实模块复现脚本验证上述全部写法均正确改写对应 LoraLoader 节点。
- **未填真实文件名时给出明确告警**：LoRA 已启用但配置 `model_name` 为空时，节点只会改权重、文件名仍保留工作流默认值。现通过 `on_warning` 回调明确提示，便于排查「看着没加上 / 出的不是这个 LoRA」的问题（多半是漏填 model_name）。

## v1.0.46

- **修复 `--名称` 简写无法启用「名字含 `-` 后缀」的 LoRA（如 `安魂曲-1`）**：
  - 根因：v1.0.41 引入 `--名称-预设名` 时把第一个 `-` 当成了「名称-预设」分隔符，导致名字里本就含 `-` 的 LoRA（很多 LoRA 文件名带 `-1`/`-v2` 之类）被错误撕开，命令解析出的 key 与工作流配置的名字对不上，于是 `--安魂曲` 匹配不到 `安魂曲-1`。
  - 预设分隔符由 `-` 改为 `/`：`--安魂曲/预设1` 引用预设，名字里的 `-` 不再被误撕（如 `--安魂曲-1` 现在是完整名字 `安魂曲-1`）。
  - `apply_loras` 增加**前缀匹配**：命令简写 `安魂曲` 也能启用工作流里名为 `安魂曲-1`、`安魂曲_v2` 这类带版本/后缀的 LoRA（精确优先，未命中再按 `名称-`/`名称_` 前缀匹配），所以 `/draw 1girl --安魂曲` 现在就能加上对应 LoRA 了。

## v1.0.45

- **报错提示不再直接把原始异常抛给用户**：真实错误（含堆栈）只写进日志，用户看到的是经过包装的可爱萌系提示。
  - 新增 `_classify_error`（按异常类型粗分为 连接失败 / 超时 / 服务器错误 / 其它）、`_friendly_error`（记日志 + 取话术）与话术池 `_ERR_HINTS`（每类多条随机，避免千篇一律）。
  - 覆盖 `_do_draw` 全部对外报错点：提交任务失败、服务器无任务 ID、出图超时、未找到输出图片、下载图片失败、工作流加载失败；以及 `/loraon`/`/loraoff` 的保存异常。
  - 例：ComfyUI 连不上时，用户看到的是「呜…绘图服务器好像联系不上了呢，可能它正在打盹，麻烦联系管理员看看吧～」之类，而不是 `Cannot connect to host ...`。
  - 配置类问题（未配置服务器/工作流等）保留可读原因，但用可爱口吻包裹并提示联系管理员。

## v1.0.44

- **修复 `/draw` 指令解包参数个数不匹配导致的崩溃**：`_parse_draw_args` 自 v1.0.41 起返回 7 元组（含 `lora_presets`），但 `cmd_draw` 的解包仍按 6 个写，导致任意 `/draw` 调用都直接抛 `ValueError: too many values to unpack (expected 6)`。现已补上 `lora_presets`，并把解析出的预设正确传给 `_do_draw`（如 `/draw 1girl --鉴定师-1`）。

## v1.0.43

- **LoRA 预设提示词改为单个文本框（textarea）**，不再用嵌套的对象数组。
  - 配置格式：``[预设名|提示词] [名字2|提示词, solo, 1girl]``——每套预设用 `[名称|提示词]` 表示，多套之间用空格隔开；提示词里可以包含逗号（中英文均可，不会因逗号被拆散）。
  - 好处：直接粘贴一串标签即可，不用在多个子字段里逐个填；`_conf_schema.json` 的 `presets` 由 `template_list` 改为 `text`。
  - 预设提示词统一追加到**正向**提示词（`/`draw 提示词 --名称-预设名`）；旧的「正向/负向」双字段格式已不再支持，但解析器对旧版对象数组做了兼容兜底，升级不会直接崩。
  - 新增 `_parse_presets` 解析器（字符串按 `[...]` 切块、块内首个 `|` 分隔名称与提示词），`_loras_of` 与 `_apply_lora_presets` 均走它，并用 `--安魂曲-预设1` 按名称命中。

## v1.0.42

- **移除工作流级的「仅模型」配置项（`lora_model_only`）**。
  - 原因：自 v1.0.41 起，每个 LoRA 是否「仅模型」已在全局 LoRA 库里逐 LoRA 配置（`model_only`），工作流级那个只是「几乎永远用不到的兜底默认值」，属于冗余。现在统一由全局库决定，工作流配置更简洁。
  - 行为不变：装配 LoRA 时一律优先用该 LoRA 在全局库里的 `model_only`（库里没有的 LoRA 默认按「仅模型」处理）。`lora_clip` 的提示文案也同步改为引用全局库里的开关。
  - `_conf_schema.json` 删除了工作流模板里的 `lora_model_only` 字段；`main.py` 不再读取它，回退值固定为「仅模型」(`True`)。

## v1.0.41

- **LoRA 配置大重构：抽出「全局 LoRA 库」+「工作流默认启用列表」**。
  - 新增顶层 `loras` 配置（数组对象，类似工作流列表），每个 LoRA 含：`name`(引用名)、`model_name`(ComfyUI 文件名)、`model_only`(是否仅模型，可逐 LoRA 配置)、`weight`(默认权重)、`keywords`(触发词)、`presets`(多套提示词预设，每套含 `name/positive/negative`)。不再用 `|` 拼字段，配置更直观。
  - 工作流里的 `loras_text` 简化为 `名称|权重|是否启用` 三字段，仅作「本工作流默认启用/权重」；真正文件名、是否仅模型、预设提示词都去全局库里按名称查。留空则本工作流不默认启用任何 LoRA（仍可用 `/draw --名称` 临时启用）。即使写了、标记为禁用也不生效。
  - 组装时按名称把「工作流默认」与「全局库」合并成完整配置；库里找不到的名称仅用工作流里的有限信息（文件名缺失，注入时告警）。
- **LoRA 预设提示词**：`/draw 1girl --安魂曲-预设1` 表示用「安魂曲」的「预设1」预设，把其 positive/negative 追加进工作流的正/负向提示词。预设不存在时记录告警并跳过、不影响出图。
- **逐 LoRA 独立 `model_only`**：每个 LoRA 可单独决定用「仅模型」(LoraLoaderModelOnly) 还是完整 (LoraLoader) 注入；工作流的 `lora_model_only` 作为缺省回退。
- **修复多 LoRA 链式接线 / 全模型模式 bug**：旧版多个注入节点都直接接主模、中间节点输出被丢弃（仅最后一个生效）；新版改为正确链式串联（第 n 个接第 n-1 个输出），并把 clip 路改接到链末端最后一个完整 LoraLoader。同时修复了当 `model_src==clip_src`（如 CheckpointLoader 同时出 model 与 clip）时，clip 消费节点误把 KSampler 也算入、导致其 `model` 被错写成 slot1 的问题（旧版全模型模式即存在，因默认仅模型未暴露）。
- `/loraon`/`/loraoff` 改为直接操作工作流 `loras_text`；若目标 LoRA 不在默认列表但存在于全局库，会自动追加一条默认启用/禁用项。`/loralist` 现展示「仅模型/模型+CLIP」与预设名。`/drawhelp` 同步说明预设语法。
- `tests/test_logic.py` 的 1c/1d 断言已同步为 `model_only` 默认 True 的行为。
- **文档与打包完善**：
  - `README.md` 新增「LoRA 的启用 / 禁用 / 注入」章节，说明启用改写、真禁用（删节点重连）、按配置注入与未覆盖节点保持原样的行为，便于用户理解「为什么配置里开了 LoRA 工作流里却看不到 / 禁用了仍报错」等常见疑问。
  - `build_zip.ps1` 打包文件清单加入 `CHANGELOG.md`，使发布包自带更新日志。

## v1.0.40

- **`/draw` 指令新增 LoRA 简写语法**：
  - `--安魂曲` 等价于 `--lora 安魂曲:1`（权重默认 1.0）。
  - `--安魂曲:0.5` 等价于 `--lora 安魂曲:0.5`。
  - 名称与权重之间的冒号同时支持半角 `:` 与全角 `：`（已做归一）。
  - 解析方式由正则改为 token 化遍历：先处理已知取值型参数（`--lora`/`--wf`/`--w`/`--h`/`--seed`，会吃掉其后一个值 token），其余未知 `--xxx` 一律视为 LoRA 简写，因此 `--wf sd` 等不会误判成 LoRA。旧写法 `--lora 名称[:权重]` 完全兼容。
  - 同步更新了 `cmd_draw` 文档串与 `/drawhelp` 帮助文本。

## v1.0.39

- **修复 `/draw --lora 名称:权重` 解析丢失中文名（只取首字）的 bug**：
  - 根因：原正则 `--lora\s+(\S+?(?::\d+(?:\.\d+)?)?)` 中 `\S+?` 非贪婪、后接可选组 `(?::\d+...)?`，正则引擎在只吃下第一个汉字（如「安」）后，因后续字符非冒号、可选组匹配空即停止回溯，于是 `--lora 安魂曲:1` 被解析成 `{'安': None}`，与配置里的「安魂曲」匹配不上，导致命令行指定的 LoRA 静默不加载（即使配置里禁用、命令行想强制启用也失效）。
  - 修复：改为贪婪捕获整个 token `--lora\s+(\S+)`，权重交给已有的 `token.split(":", 1)` 处理（`安魂曲:1` → name=`安魂曲`、weight=`1`；`安魂曲` → name=`安魂曲`）。

## v1.0.38

- **修复完整模式（`lora_model_only=false`）注入时崩溃**：`clip_consumers` 在上一版改为 `(node, field, slot)` 三元组，但重接 clip 路的循环仍按二元组解包，触发 `ValueError: too many values to unpack (expected 2)`。现已修正为该循环解包三个变量（第三个为实际 slot，循环内未使用）。

## v1.0.37

- **修复完整模式（lora_model_only=false）下 LoRA 的 CLIP 没接上的问题**：
  - 根因 1：锚点探测里 clip 源判定被限制为 `val[1] == 1`，而 CLIPLoader 等节点的 CLIP 输出在 **slot0**，导致 `clip_src` 探测不到、`clip_consumers` 为空，正向/负向提示词都接不上。现已去掉 slot 限制，按实际引用探测。
  - 根因 2：注入的 `LoraLoader` 其 `clip` 输入被硬编码为 `[clip_src, 1]`，对 CLIPLoader（CLIP 在 slot0）是错的。现改为**从编码器实际引用 clip 源的 slot 推导**，兼容 CheckpointLoader(slot1) 与 CLIPLoader(slot0)。
  - 新增诊断日志：注入前打印 `模式/model源/clip源`，注入时打印 `clip消费节点` 清单，便于真机核对。

## v1.0.36

- **修复配置项显示问题**：
  - `lora_model_only` 的 `type` 从 `boolean` 改为 `bool`（AstrBot 仅认 `bool` 渲染成开关，原先被退化成输入框）。现在它是一个正常的 switch 开关。
  - `lora_anchor` 显示名改为「**主模节点 ID**」（更直观）；提示里把示例数字（如 4）明确标注为「仅举例、非默认值」，避免被误读成默认值。
  - `lora_clip` 同步改用「主模 CLIP 节点 ID」口径，与上方主模节点保持一致。

## v1.0.35

- **新增 `lora_clip` 配置项（工作流级，可选）**：显式指定 CLIP 模型节点 ID（如 CLIPLoader / CheckpointLoader 的 CLIP 一侧）。
  - 与已有的 `lora_anchor`（model 源）分离，可分别指定 model 源与 clip 源，兼容「正向 / 负向提示词用不同 CLIPLoader」等分离式工作流，也用于在完整模式（lora_model_only=false）下自动探测失败或接错 clip 源时手动兜底。
  - 仅模型模式（默认）不需要此项；填了也会被忽略。

## v1.0.34

- **LoRA 注入改用「加载LoRA（仅模型）」节点（LoraLoaderModelOnly）**。此前在没有现成 `LoraLoader` 节点、需要自动注入时，插件会新建完整的 `LoraLoader`（同时接 model 与 clip 两条路），要求工作流既能探测到 model 锚点、又能探测到 clip 锚点。若工作流没有 clip 源（如未配置 `lora_anchor`、或 CLIP 路结构特殊），注入就会失败、LoRA 被静默跳过。
  - 现在注入默认使用 `LoraLoaderModelOnly`：**只把 LoRA 叠加到去噪网络（MODEL），只需 model 锚点即可**，不再要求 clip 锚点，兼容性最好。
  - 新增配置项 `lora_model_only`（工作流级，默认 `true`）：关闭后退回完整 `LoraLoader`（同时影响 model + clip），适用于需要文本编码器权重的 LoRA（此时仍需工作流能探测到 model + clip 两个锚点）。
  - 注入日志现在会标注「（仅模型）」或「（模型+CLIP）」，便于核对。

## v1.0.33

- **LoRA 注入失败告警升级为「可操作诊断」**。此前当工作流既没有 `LoraLoader` 节点、又没有配置 `lora_anchor`（底模加载节点）时，插件只会打一行模糊 warning 然后静默跳过 LoRA（即用户反映的「设置里开了 LoRA，但最终工作流里看不到」）。现在该告警会：
  - 明确指出本次被跳过的 LoRA 名称；
  - 给出两种解决办法（填 `lora_anchor` / 在工作流里加 `LoraLoader` 节点）；
  - **直接列出当前工作流所有节点的「键名 + class_type」**，用户照着把对应键名（如 CheckpointLoader 的键名）抄进工作流配置的 `lora_anchor` 即可，无需再猜。

## v1.0.32

- **新增：LoRA 决策全过程日志**，用于排查「设置里开了 LoRA，但最终工作流里看不到」。现在每次绘图会依次打印：
  - `LoRA 配置解析`：`loras_text` 解析出的每个 LoRA（名称/别名(文件名)/权重/enabled/load_node）；
  - `LoRA active_map`：本次实际请求启用的 LoRA 集合；
  - `[LoRA] ...`：工作流现有 `LoraLoader` 节点、以及每个 LoRA 被判定为「改写已有节点 N / 待注入 / 禁用删除 / 跳过」的结果，注入完成后打印新建节点 ID 与锚点来源，最终打印实际启用列表。
- **提示**：当 LoRA 未填「别名(文件名)」且被分配到已有 `LoraLoader` 节点时，日志会明确警告「仅改权重、未改文件名」——这是常见的“看起来没生效”的原因之一。

## v1.0.31

- **修复：工作流无 LoRA 节点时注入锚点探测过窄**。旧逻辑只在 `class_type` 含 `checkpointloader` 的节点里找注入锚点，若工作流用 `UNETLoader`+`CLIPLoader`、或自定义底模节点（类名不含 checkpointloader），开启的 LoRA 会被**静默丢弃**。现在改为与节点类名无关的探测：从采样器的 `model` 输入、CLIP 文本编码的 `clip` 输入反推上游，兼容 `UNETLoader+CLIPLoader` 分离式工作流（model 与 clip 可来自不同上游节点）。
- **新增：注入失败显式告警**。配置了启用 LoRA、但工作流无 LoRA 节点且探测不到任何锚点时，通过 `on_warning` 回调打印告警（提示去配 `lora_anchor` 或检查底模节点），不再无声吞掉配置。

## v1.0.30

- **调试日志**：`_do_draw` 在把工作流提交给 ComfyUI 之前，会用 `logger.info` 打印最终拼接完成的工作流 JSON（含提示词、宽高、LoRA 注入/禁用后的节点、种子）。便于核对「配置组装的工作流是否正常」，日志搜索关键字 `最终工作流（提交给 ComfyUI）` 即可定位。

## v1.0.29

- **LoRA 真禁用**：禁用某个 LoRA 时，不再只是把 `strength_model`/`strength_clip` 置 0，而是把对应的 `LoraLoader` 节点从工作流图中**删除**并接通其上下游（model/clip）。彻底避免「权重 0 但仍加载文件、文件缺失即报错、占用显存」的问题；支持链式 LoRA（A→B→C）的穿透重连。
- **LoRA 按配置注入**：工作流本身没有 `LoraLoader` 节点时，可在工作流配置中填写 `lora_anchor`（底模 `CheckpointLoader` 节点的键名，如 `4`；留空则自动探测 `CheckpointLoader`），插件会在锚点之后链式新建 `LoraLoader` 节点并接好线，实现「一份干净工作流 + 不同 LoRA 配置 = 不同出图」。禁用项不会被注入。
- **保留未配置节点**：工作流里存在但未被任何 `loras_text` 配置项覆盖的 `LoraLoader` 节点，保持原样不动。
- **配置项新增**：`_conf_schema.json` 增加 `lora_anchor` 字段。
- **测试**：`tests/test_logic.py` 新增真禁用重连、按配置注入、链式多注入三组用例（纯逻辑测试已通过）。

## v1.0.28

- 初始已发布功能（多服务器、多工作流、LoRA 管理、Anima 标签翻译等）。
