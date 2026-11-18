# TODO — 在线生图（WebUI 生图工作台）

> 状态：设计定稿，待实现。本文档是「在线生图」页的唯一口径来源。
> 目标版本：v7.9.0（新增功能系列，Y +1）。

## 一、背景与目标

目前插件的全部生图入口都在聊天侧（`/draw`、`/画`、`/img2img`、LLM 工具）。
用户希望在 WebUI 控制台里直接出图：不用切到 QQ，就能选工作流、选 LoRA、
调参数、让 LLM 帮忙写提示词，出完图直接看大图 / 下载 / 拿去二次加工。

三个模式：**文生图（t2i）/ 图生图（img2img 单参考）/ 多参考图生图（multi-ref）**。
本页只走 ComfyUI 工作流链路（第三方平台 NAI 等留 v2）。

核心设计原则：**生图执行不另起炉灶，全部复用 `_do_draw(external=True)`**
——它是本插件唯一的出图写入口（权限、LLM 润色、LoRA/预设、尺寸开关、
上传参考图、NSFW、图库归档、出图记录全在里面）。WebUI 层只做「参数收集
+ 任务管理 + 结果展示」，保证与聊天侧行为完全一致。

## 二、后端设计

### 2.1 新增顶层模块 `studio_api.py`

三个职责（单文件，进 `$includeList` + `main.py` 热重载列表）：

1. **`StudioStore`** —— SQLite（`data_dir/studio.db`），表
   `studio_templates(id, name, category, content, is_negative, enabled, sort_order,
   created_at, updated_at)`：预设提示词模板库。首次初始化 `reseed_defaults()`
   补齐内置模板（画质增强 / 视角构图 / 光影氛围 / 服装配饰 / 表情动作 / 风格
   六类约 24 条）。
2. **`StudioJobManager`** —— 内存任务注册表 + 每任务一个 `asyncio.Task`：
   - `submit(params) -> job_id`；job 状态机 `queued → running → done | error | discarded`；
   - 任务协程：ref_ids 解析回本地路径 → 构造 `_HeadlessEvent` →
     `async for node, p in plugin._do_draw(headless_event, ..., external=True,
     source=SOURCE_WEBUI_STUDIO, notify_pending=False, raw_prompt=not llm_refine)`
     收集图片路径（`external=True` 时产出 `(None, path)`，归档已由 `_do_draw` 完成）；
   - 结果记 `{sha, w, h, prompt, workflow}`（从图库记录回查），供前端直连
     `/img/{sha}`（独立通道）或 `gallery/thumb`（内嵌通道）；
   - 历史：内存保留最近 ~50 条（重启清空，UI 提示；成品图在图库不丢）；
   - `discard(job_id)`（放弃等待）/ `interrupt()`（尽力而为中断 ComfyUI 当前任务）。
3. **HTTP handler 函数**（供 `webui_api.WebUIApi` 方法一行委托）。

### 2.2 `main.py` 改动（外科手术式，共 4 处）

1. 常量 `SOURCE_WEBUI_STUDIO = "webui_studio"`（挨着 `SOURCE_COMPANION_PLUGIN`，main.py:790）。
2. **`_do_draw` 准入放行**（main.py:8184-8199）：`external=True 且 source==webui_studio`
   时跳过聊天白/黑名单与配额（控制台有独立口令保护，归属记 `plugin:webui_studio`，
   不占用聊天配额）。其余路径一行不动。
3. `__init__` 挂 `self.studio = studio_api.StudioManager(self)`（挨着 standalone_webui，~main.py:1972）。
4. **热重载列表**（~main.py:1996-2019）加入 `studio_api`（按依赖序）。

### 2.3 `comfyui_client.py`

加 `async def interrupt(self) -> dict`：`POST /interrupt`（尽力而为，全局中断，
前端二次确认后才调）。

### 2.4 双通道注册（沿用既有约定：实现一份，两通道复用）

- **`webui_api.py`**：`WebUIApi` 加 ~12 个 `studio_*` 方法（内部委托
  `plugin.studio`），加进 `routes` 列表 —— `ROUTE_METHODS` 自动重建，
  独立通道的方法校验随之生效。
- **`standalone_webui.py`**：`_dispatch` 加 `/studio/*` 前缀分支 →
  新 `_api_studio(path, request)`，逐字复刻 `_api_lora_translate`
  （main.py 之外的 adapter + `webui_api.request/json_response/error_response`
  临时替换 + 串行锁）的做法。上传体上限已豁免（`_MAX_UPLOAD_BYTES=24MB`）。

### 2.5 API 契约

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/studio/overview` | 引导数据一次拿全：工作流列表（kind=t2i/img2img 标注 + 底模/服务器/默认尺寸/是否支持多参考）、LoRA 库（含预设/触发词/底模兼容）、尺寸档位、参考图上限、LLM 是否可用、模板列表 |
| POST | `/studio/upload_ref` | `{data_url}` → 存 `data/temp/studio_ref_<uuid>.png` → `{ref_id}` |
| POST | `/studio/upload_ref_gallery` | `{sha}` → 从图库文件复制一份当参考图 → `{ref_id}` |
| POST | `/studio/generate` | 提交任务 → `{job_id}`；参数：`mode / workflow / positive / negative / width / height / seed / steps / cfg / denoise / sampler / loras:[{name,weight,preset}] / ref_ids / llm_refine / custom_size` |
| GET | `/studio/jobs` | 最近任务历史（含状态、结果 sha、参数快照） |
| GET | `/studio/job` | `?id=` 单任务状态（前端 1.5s 轮询） |
| POST | `/studio/job/discard` | `{id}` 放弃等待（ComfyUI 自然完成，不再展示） |
| POST | `/studio/interrupt` | 中断 ComfyUI 当前任务（二次确认） |
| POST | `/studio/llm_prompt` | `{mode, text, lora_ref?}` → `{text}`；mode：`expand`（扩写→`_enhance_prompt_llm`）/ `translate`（中文→标签→`_translate_llm`）/ `anima`（→`_rewrite_to_anima_llm`）/ `natural`（→`_rewrite_to_natural_llm`）/ `qwen_edit`（→`_rewrite_to_qwen_llm(edit_mode=True)`）；默认按所选工作流风格预选 |
| GET | `/studio/templates` | 模板列表 |
| POST | `/studio/templates/save` / `delete` / `reseed` | 模板 CRUD + 补齐内置 |

错误信封、`success/error` 归一化全部沿用现有 `_ok/_err` 与 bridge 约定。

## 三、前端设计（webui-src）

### 3.1 文件

- `src/views/StudioView.vue` —— 主页面（路由 `/studio`，title「在线生图」）；
- `src/components/studio/`：`WorkflowPicker.vue`（卡片选择，封面/kind/底模角标）、
  `RefUploadPanel.vue`（拖拽 + 粘贴 + 多图列表，上限取 `max_refs`）、
  `LoraPicker.vue`（折叠列表 + 权重滑条 + 预设勾选 + 触发词展示）、
  `PromptPanel.vue`（正/负向编辑器 + AI 按钮组 + 模板抽屉入口 + 快捷标签 chips）、
  `TemplateDrawer.vue`（分类页签 + 插入/替换 + 模板管理）、
  `ParamsPanel.vue`（尺寸档位 + 自定义宽高 + steps/cfg 滑条 + denoise（图生图）+ seed）、
  `JobStatusList.vue`（排队/运行中/耗时/报错/取消）、`ResultPanel.vue`（大图 + 成果条）；
- `src/api/studio.ts` —— 类型化封装（走 `apiGet/apiPost`，端点不带前导斜杠）。

路由、`nav.ts`、`App.vue menuOptions` **三处同步**（PC 侧栏与移动抽屉是两份清单）。

### 3.2 布局（PC 三栏，移动端堆叠）

```
┌────────────────────────────────────────────────────────────────┐
│ 在线生图   [文生图 | 图生图 | 多参考]        任务：●运行中 ×2     │
├──────────────┬─────────────────────────────────┬───────────────┤
│ 生成配置      │  画布：最新成果大图 / 加载动画    │ 任务队列       │
│ · 工作流选择  │  空态引导；排队位置/耗时提示      │ · 运行中+耗时  │
│ · 参考图上传  │  操作：大图 下载 重跑            │ · 历史可复用参数│
│   (图生图时)  │       用作图生图 复制提示词      │ · 中断(确认)   │
│ · LoRA 选择  ├─────────────────────────────────┴───────────────┤
│ · 生成参数    │  提示词区：正向/负向 + AI 按钮组 + 模板 + 快捷标签  │
├──────────────┴──────────────────────────────────────────────────┤
│ 本次会话成果条（横向缩略图流，点击进大图）                          │
└─────────────────────────────────────────────────────────────────┘
```

- 主题走现有 CSS 变量（`--bg-body/--bg-panel/--text-main/--accent`…），
  深浅色自动适配；NaiveUI + `useMessage/useDialog`；刷新按钮接 `useRefresh`。
- 参考图预览用本地 `FileReader` data URL（双通道一致，不依赖后端回显）；
  结果图用 `fetchThumb`/`standaloneImgUrl`（bridge 双模式已封装）。
- 大图复用 `ImageViewer.vue`；NSFW 模糊沿用图库策略。
- UI 偏好（上次选的工作流/尺寸档位/LoRA 等）存 `storage.ts`（沙箱安全包装）。

### 3.3 与插件其它页的联动

- 成果全部经 `_do_draw` 归档进图库 → **图库页 / 出图记录自动可见**
  （归属 `plugin:webui_studio`），无需新页面；
- 「用作图生图」：结果 sha → `/studio/upload_ref_gallery` 变参考图，一键接力；
- 模板抽屉与 LoRA 封面、底模角标复用既有数据（`/lora/file`、`basemodel` 封面）。

## 四、权限与配额口径

- 控制台生图**不判聊天白/黑名单、不占聊天配额**（独立口令保护 + 归属
  `plugin:webui_studio`）；`_do_draw` 内的 LLM 润色照常走、token 照常入账
  （token_store）。
- 「提交前 LLM 润色」开关关闭时 `raw_prompt=True`（跳过插件 LLM 加工，只用
  原文——提示词已由页面上的 AI 按钮润色过）；开启时与聊天侧一致走按工作流
  风格分发的润色链。
- 任务内历史仅内存（重启清空）；成品图永久在图库。

## 五、边界与风险

1. **`build_zip.ps1` 是显式 `$includeList` 且无守卫**——漏列 `studio_api.py`
   就是静默残包，发布 checklist 必须核对 zip 内含 `studio_api.py`。
2. `main.py` 热重载列表漏加 → 热更后 studio 跑旧代码，必须同步。
3. 内嵌通道图片走 base64（沿 gallery/thumb 既有约定，不做新直链）。
4. 旧版内联 `workflow_json` 的工作流：overview 时现解析一次并缓存 kind。
5. ComfyUI 中断是**全局**操作（会打断正在跑的任何任务）——按钮放「任务队列」
   头部，红色 + 二次确认。
6. 多参考模式仅对带 `TextEncodeQwenImage21`（multi_image）类工作流生效，
   工作流选择器按能力标注「支持多参考」；`max_refs` 默认 3。
7. `_do_draw` 体量大、单任务全流程 await——失败/异常必须被 job 协程兜住
   写进 `job.error`，不允许炸掉插件事件循环。

## 六、发布清单（对齐 AGENTS.md）

- [ ] `metadata.yaml` → `v7.9.0`；CHANGELOG 顶部加条目（`## v7.9.0（在线生图工作台：…）`，
      按 anima 格式：需求 / 方案 / 要点，文件名方法名反引号）
- [ ] `studio_api.py` 加入 `build_zip.ps1` `$includeList` **和** `main.py` 热重载列表
- [ ] `build_webui.ps1` 重建前端，`pages/anima-console-vue/` 产物入库
- [ ] `python -m compileall` 全部改动 py 文件
- [ ] 打包 → `python -c zipfile` 复核：结构与历史包一致（散放）、含 `studio_api.py`
- [ ] 浏览器冒烟：桩 API 页面（stub `/api/*`）确认三模式渲染 / 无控制台报错
- [ ] 中文提交用文件法（`git commit -F`）；`git push origin master`
- [ ] 真机联调（用户环境）：三模式出图 / LoRA / 模板 / LLM 按钮 / 取消 / 内嵌页通道

## 七、v2 预留（本期不做）

平台（NAI 等）生图页签 · 成品发送到指定会话 · 任务持久化 + SSE 实时进度
（ComfyUI websocket）· 工作流串联（生成→放大）· 控制台配额开关。
