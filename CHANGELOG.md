# 更新日志

本文件记录插件各版本的改动。版本号与 `metadata.yaml` 保持一致。

## v4.5.6

- **涩图检测结果恢复显示置信度百分比**：
  - 现象：安全图片的结果（`✅ 安全`）此前不显示百分比，只有涩涩/擦边才显示。
  - 修复：所有判定分支都带上百分比——`🔞 涩涩内容（可能性约 x%）`、`⚠️ 有点擦边（可能性约 x%）`、`✅ 安全（可能性很低，约 x%）`。

## v4.5.5

- **涩图检测不再阻塞画图**：
  - 根因：`cmd_nsfw` 指令在 AstrBot 事件循环里**同步**调用 `NSFWDetector.detect()`（onnxruntime CPU 推理，单张可达数百毫秒~1 秒+），会阻塞整个事件循环，影响画图任务/其他指令排队。
  - 修复：检测循环改为 `asyncio.to_thread` 丢到线程池并行执行，多张图也并行，事件循环不被卡住，画图等其他事件不受影响。
  - 检测中提示「⏳ 检测中，请稍候…」保留。

## v4.5.4

- **修复独立 WebUI 图库部分接口 Not Found**：
  - 独立服务 `standalone_webui.py` 的 `/api/gallery/*` 缺少 `check_nsfw`、`set_nsfw`、`set_blur` 三个接口（此前仅内嵌页有），导致独立环境报 `Not Found: /gallery/check_nsfw` / `/gallery/set_nsfw`。
  - 补齐：`/gallery/check_nsfw`（单图检测，GET）、`/gallery/set_nsfw`（人工标记/取消 NSFW，POST）、`/gallery/set_blur`（单图模糊覆盖，POST），返回结构与内嵌页一致，并写入操作日志。
  - 至此独立服务 `/api/gallery/*` 与内嵌页接口全集对齐。

## v4.5.3

- **新增图库一键全量重新检测（调整 NSFW 阈值后刷新旧图标记）**：
  - 场景：调整 `gallery.nsfw.threshold` 后，旧图仍是旧阈值的结果，需要重扫。
  - 指令：`/图库 重扫`（管理员）触发全量重扫（用当前阈值重扫所有图，后台执行）；`/图库 重扫状态` 查看进度。`/图库` 帮助新增说明。
  - WebUI：图库页工具条新增「重新检测」按钮（带确认弹窗），触发全量重扫（only=0）；原「一键检测」仍只扫未检测图。后端接口复用已有 `gallery/scan_nsfw`（独立服务亦已接入）。
  - `image_store.scan_nsfw_progress` 增加未初始化状态兜底，避免从未扫描时查询报错。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2855 模块，0 错误）。

## v4.5.2

- **涩图检测指令体验优化**：
  - 指令发出后立即返回「⏳ 检测中，请稍候…」提示。
  - 单张图返回**简单结论**（不再用列表、不展示阈值）：`🔞 涩涩内容（可能性约 86%）` / `✅ 安全（涩涩内容可能性很低）` / 擦边警告。
  - 置信度改**通俗叫法**「可能性约 x%」替代专业术语「P(nsfw) 置信度 / 阈值」；多张图保留列表但同样用通俗话术。
  - 帮助指令（`/绘图帮助`/`/drawhelp`）与 README 新增涩图检测说明（含全部别名与依赖提示）。

## v4.5.1

- **修复右上角主题切换按钮无效**：
  - 根因：`App.vue` 的主题开关 `n-switch` 同时绑定了 `v-model:value="isDark"` 与 `@update:value="toggleDark"`，二者叠加——`v-model` 更新 `isDark`，`toggleDark` 又翻转一次，主题「切了又切回」，表现为点不了。
  - 修复：移除 `@update:value="toggleDark"`，仅保留 `v-model:value="isDark"`（`watch(isDark)` 自动同步 `<html>` 主题与 CSS 变量）。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2855 模块，0 错误）。

## v4.5.0

- **版本进位**：按约定（小版本 Z 超 10 进位），自 v4.4.50 后中间版本进位为 v4.5.0，后续从 4.5.x 继续，Z 到 .11 再进位到 4.6.0。
- 本版本内容即 v4.4.50 的改动（图库图片直链加载，替代 base64），详见下方 v4.4.50 条目。

## v4.4.50

- **独立 WebUI 图库图片改为直链加载（替代 base64），大幅提升加载速度与内存占用**：
  - 背景：此前图库缩略图/大图均通过 `apiGet("gallery/thumb")` 返回 base64 data URL 内联，体积 +33%、无法走浏览器缓存、图库多图时慢且占内存。用户正是因此弃用 AstrBot 内嵌页（其 base64 更慢）才做独立 WebUI。
  - 后端：`standalone_webui.py` 新增图库图片直链端点 `GET /img/{sha}` 与 `/img/{sha}/thumb`（支持 `?size=` 缩略、带 token 鉴权、返回原始/缩略图片二进制 + 长缓存头）。`<img>` 直接加载 + 浏览器缓存，避免 base64 内联。
  - 前端：`bridge.ts` 新增 `standaloneImgUrl(sha, size)`（拼 token 生成直链）；`fetchThumb` 独立模式下直接返回直链 URL（内嵌页仍走 base64）。`ImageViewer.vue` 大图/参考图独立模式改用直链。图库网格、日志缩略图自动受益。
  - 说明：LoRA 封面仍走 base64（小图、量级小，影响有限）；如需也改直链可再迭代。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2855 模块，0 错误）。

## v4.4.49

- **修复登录成功后不跳转（停留在口令页）**：
  - 根因：`LoginView` 登录成功用 `window.location.reload()`，reload 后 URL 仍是 `#/login`，路由守卫对 `login` 放行 → 一直停在口令页。
  - 修复：登录成功改为 `authState='authed'` + `router.replace(来源页或 /config)`，无需 reload 直接进入控制台；口令已存 localStorage，后续请求自动携带。来源页由守卫跳转时携带的 `?redirect=` 提供。
  - 至此独立 WebUI 完整认证链路闭环：访问任意页 → 守卫探测(ping 401) → 跳 `/#/login`(全屏无侧边栏) → 输口令 → 跳回控制台。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2855 模块，0 错误）。

## v4.4.48

- **修复独立 WebUI 不跳转登录页 + 登录页仍显示侧边栏**：
  - **根因 1（不跳转）**：后端 `/api/ping` 直接注册到 `_handle_ping`，**未经过 `_authed` token 鉴权**，始终返回 200。前端路由守卫用 ping 探测认证状态 → 误判「已认证」→ 放行控制台；而控制台其他 API 才返回 401，导致「接口 401 但页面在控制台、不跳登录页」。
  - 修复：`_handle_ping` 增加 token 鉴权（未带有效 token 返回 401）。前端守卫探测到 401 → `unauthed` → 强制跳转 `/#/login`。
  - **根因 2（登录页有侧边栏）**：`App.vue` 是布局组件（含侧边栏/顶栏），登录页渲染在 `<router-view>` 内但仍带布局。
  - 修复：`App.vue` 当 `route.name === 'login'` 时**只渲染 `<router-view>`**（不含侧边栏/顶栏），登录页为独立全屏页面。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2855 模块，0 错误）。

## v4.4.47

- **独立 WebUI 改为真正的 `/login` 口令登录页路由**（替代依赖自动探测的方案）：
  - 背景：此前登录页由 `App.vue` 依据 `authState` 在根路径渲染，自动跳转不可靠（部分环境未识别为独立模式），且无独立 URL 可手动访问。
  - 新增 `LoginView.vue`：口令登录页独立组件（居中卡片 + 口令输入 + 确认，支持深色/移动端）。
  - `router/index.ts` 新增 `/login` 路由（`/#/login`），并加**认证守卫**：独立模式下未认证的路由一律强制跳转 `/#/login`；登录页放行；内嵌页（AstrBot）不做独立口令校验直接放行。
  - 新增 `composables/auth.ts`：全局 `authState`（checking/unauthed/authed）+ `checkStandaloneAuth()` + `submitToken()`，供路由守卫与登录页共用。
  - `App.vue` 移除内联登录页与认证逻辑，只负责控制台布局。
  - 独立服务认证链：访问任意页面 → 守卫探测 → 401 则跳转 `/#/login` → 输口令成功 → reload 进入控制台。也可手动访问 `http://IP:端口/#/login` 验证登录页。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2855 模块，0 错误）。

## v4.4.46

- **独立 WebUI 支持 `?login=1` 强制口令登录页（调试/验证用）**：
  - 在 URL 后加 `?login=1`（如 `http://IP:端口/?login=1`）时，前端无条件显示口令登录页，不经过自动探测。
  - 用于排查「自动跳转登录页不生效」问题：先强刷加载最新前端，再访问 `/?login=1` 手动确认登录页存在；随后反馈「正常访问首页仍不跳登录页」时，多半是浏览器缓存旧 JS 或后端 token 未配置。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。

## v4.4.45

- **NSFW 检测指令增加更多别名**：`涩图检测`、`色图检测`、`色色检测`、`瑟瑟检测`、`瑟图检测`（保留 `涩涩检测`、`nsfw`、`NSFW`、`nsfw检测图片`）。引用图片后发送任一别名即可检测。

## v4.4.44

- **修复独立 WebUI 口令登录页仍不出现（独立模式判定不可靠）**：
  - 现象：接口已正确返回 401「未授权」，但前端始终不跳转到口令登录页。
  - 根因：前端 `isStandaloneMode()` 依赖「无 AstrBot 桥接」的间接判断，在部分环境（如页面加载时序/跨源访问 parent 异常）下返回 false，导致 `App.vue` 走 `authState='authed'`，**根本没触发认证探测**。
  - 修复：
    - `standalone_webui.py`：`_handle_index` 在返回 `index.html` 时注入 `<script>window.__ANIMA_STANDALONE__=true;</script>` 标记（仅独立服务会注入，AstrBot 内嵌页不会）。
    - 前端 `bridge.ts`：`isStandaloneMode()` **优先检查 `window.__ANIMA_STANDALONE__`**，存在即 100% 判定为独立模式 → 触发认证探测 → 401 时显示口令登录页。兜底逻辑保留。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。

## v4.4.43

- **修复 WebUI 配置页嵌套对象显示为 `[object Object]`**：
  - 现象：配置页里 `gallery` 配置块下的嵌套对象（如 `gallery.nsfw`）渲染成 `[object Object]`，导致无法在 WebUI 里配置 `gallery.nsfw.threshold`（NSFW 判定阈值）等子项。
  - 根因：`ConfigField.vue` 未处理 `type: "object"` 字段，落到通用 `n-input` 分支，把对象 `String()` 成 `[object Object]`。
  - 修复：`ConfigField.vue` 新增 `object` 类型递归渲染——子字段用虚线分组面板展示，任一层子字段变更时向上拼出完整新对象并 emit，支持任意层级的嵌套对象（如 gallery → nsfw → threshold）。
  - 效果：WebUI 配置页可正常编辑 `gallery.nsfw.threshold`（判定阈值）等嵌套配置项。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。

## v4.4.42

- **新增图片 NSFW 检测指令**：
  - 指令 `/nsfw检测`（别名 `/nsfw`、`/涩涩检测`、`/NSFW`）：引用或附带图片后发送，返回每张图的 NSFW 判断与置信度（P(nsfw) 百分比）及当前判定阈值。
  - `/nsfw检测 阈值`：查看当前判定阈值与说明。
  - 置信度与判定关系：模型输出 P(nsfw)（0~1）为「色情内容」置信度，`score >= 阈值(默认0.5)` 即标记 NSFW。泳装/紧身/露肤等场景因与训练样本特征相似，可能给出较高置信度被误判；可调高 `gallery.nsfw.threshold`（如 0.7）降低误报。
  - 依赖缺失（onnxruntime / opennsfw-onnx）时返回「检测不可用」提示，不阻塞。
  - 检测复用图库 NSFW 检测器（`get_detector`），阈值与图库配置一致。

## v4.4.41

- **WebUI 添加站点图标 favicon**：
  - 新增 `favicon.ico`（仓库根目录），复制到 `webui-src/public/`，Vite 构建时复制到产物 `pages/anima-console-vue/favicon.ico`。
  - `index.html` 的 `<head>` 添加 `<link rel="icon" href="/favicon.ico" />`，浏览器标签页/收藏夹显示该图标。
  - 独立 WebUI 与 AstrBot 内嵌页均生效（静态服务会正常返回 `/favicon.ico`，不再走 204 兜底）。
  - 构建产物 `pages/anima-console-vue/` 已重新生成（2851 模块，0 错误），含 `favicon.ico`。

## v4.4.40

- **修复独立 WebUI 登录页不出现（浏览器缓存旧前端）**：
  - 现象：接口已正确返回 401「未授权：请填写访问口令」，但前端未跳转/未显示口令登录页。
  - 根因：独立服务静态资源此前加了 `Cache-Control: max-age=31536000`（一年强缓存），`index.html` 未加任何缓存头。升级到新版本后，浏览器可能命中缓存的**旧 index.html**，仍加载**旧版 JS**（如 v4.4.38 弹窗版/更早，无整页登录页），导致看不到口令页。
  - 修复：`standalone_webui.py` 的 `_handle_index` 为 `index.html` 显式添加 `Cache-Control: no-cache`，确保每次获取最新 index.html 及其引用的 hash 资源。
  - 用户操作：升级到 v4.4.40 后，如仍看到旧界面，请在浏览器**强制刷新（Ctrl+F5）或清缓存**。

## v4.4.39

- **独立 WebUI 认证改为「整页登录页」**（替代 v4.4.38 的弹窗方案）：
  - 需求：口令未输入或失效时，整个页面只显示登录页，**完全不渲染控制台内容**；口令正确后才进入控制台。
  - `App.vue`：新增认证状态机 `checking / unauthed / authed`。独立模式下启动先 `ping` 探测：
    - `unauthed`（需口令/口令失效）→ **只渲染登录页**（输入口令 → 确认进入），不显示侧边栏/任何控制台内容；
    - `authed`（已带正确口令）→ 渲染完整控制台；
    - `checking`（探测中）→ 显示简单加载占位，避免闪烁。
  - 登录页：居中卡片式设计（logo + 标题 + 口令输入 + 确认按钮），支持深色模式；口令错误/验证失败时停留登录页并给出错误提示。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。

## v4.4.38

- **独立 WebUI 增加访问口令登录弹窗**：
  - 现象：独立服务配置了 token 时，页面加载后没有输入口令的地方，仅后台报 401，用户无法进入。
  - 修复（前端）：
    - `bridge.ts`：新增 `standaloneAuthState`（可订阅认证状态）、`setStandaloneToken`（存 localStorage）；`standaloneRequest` 收到 401 时触发 `authRequired` 状态。
    - `App.vue`：独立模式下启动时探测 `/api/ping`；若后端要求 token 则弹出「访问口令」登录框（密码输入 + 确认进入），校验通过后存 token 并重载页面进入；口令错误则继续停留弹窗。
    - 后端 `_handle_api` 对 `/api/ping` 同样做鉴权（未带 token 返回 401），作为前端探测的依据。
  - 体验：设置了 token 的独立服务，打开页面先看到口令输入框，输对才能进。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。

## v4.4.37

- **修复独立 WebUI 静态资源 500（JS 加载失败）**：
  - 现象：独立服务能启动、`index.html` 能打开，但 `/assets/index-*.js` 返回 `500 Internal Server Error`（浏览器 `ERR_ABORTED`）。
  - 修复：`standalone_webui.py` 的静态文件处理不再用 aiohttp 的 `web.FileResponse`（在部分环境（Windows/容器）下会 500），改为**手动读取文件字节返回**，并显式设置 `content_type`（缺失时回退 `application/octet-stream`）与长缓存头；读取异常时返回明确的 500 JSON 而非静默失败。
  - 构建产物不变（`pages/anima-console-vue/` 结构正常），仅后端静态服务逻辑调整。
  - 附带：`favicon.ico` 缺失时返回 204 空响应，消除浏览器控制台 favicon 404 噪音（不影响功能）。

## v4.4.36

- **修复打包遗漏：独立 WebUI / 操作日志模块未打进 zip**：
  - **严重 bug**：`build_zip.ps1` 的 `$includeList` 漏掉了 `standalone_webui.py` 与 `oplog_store.py`，导致 v4.4.33~v4.4.35 打包出的 zip 里**没有这两个文件**。
  - 后果：用户升级后，`main.py` import 这两个模块失败被吞 → `self.standalone_webui=None`（独立 WebUI 打不开）、`self.oplog=None`（操作日志页空）。这解释了此前「独立服务没启动」「日志页空」等所有"改了没生效"现象，根因都是 zip 缺文件而非代码逻辑。
  - 修复：`build_zip.ps1` 文件清单补入 `oplog_store.py` 与 `standalone_webui.py`，并核对全部核心模块均已包含。
  - 强烈建议：升级到 v4.4.36 后，若此前装的是 v4.4.33~35，请**重新安装 v4.4.36 的 zip**，并确认插件目录出现 `standalone_webui.py`、`oplog_store.py`。

## v4.4.35

- **独立 WebUI 服务默认仅本机监听，支持配置监听地址**：
  - 安全修复：此前独立服务监听 `0.0.0.0`（所有网卡），未设 token 时局域网任何设备均可访问操作。现默认改为 `127.0.0.1`（仅本机，最安全）。
  - 新增配置项 `webui_standalone.host`：默认 `127.0.0.1`（仅本机）；如需局域网其他设备访问，改为 `0.0.0.0`，配置提示中强调「绑定 0.0.0.0 且不设 token 时局域网任何设备都可操作，务必设置访问口令」。
  - 启动日志在监听 `0.0.0.0` 时提示「未设 token 局域网任何设备均可访问」。

## v4.4.34

- **独立 WebUI 服务补齐 LoRA / 翻译调试接口**：
  - 独立版新增 `POST /lora/fetch`（C 站抓取封面+触发词+描述+底模）、`GET /lora/image`（LoRA 封面缩略图）、`POST /lora/upload_image`（封面上传）、`POST /translate/test`（翻译调试）。
  - 实现方式：复用 `WebUIApi` 的原始实现（`standalone_webui.py` 实例化 `webui_api.WebUIApi`），通过 aiohttp 请求适配器（`_AioReqAdapter`，适配 `request.query/json/body/headers`）+ 串行锁临时替换 `webui_api` 模块级 `request`/`json_response`/`error_response`，调用后还原，避免与 AstrBot 内嵌页并发冲突。
  - 至此独立服务已覆盖全部 WebUI 功能，可作为完整独立入口使用。

## v4.4.33

- **新增独立 WebUI 服务（standalone），与 AstrBot 内嵌页共存，绕开内嵌页的接口 404/超时问题**：
  - 背景：AstrBot 内嵌页依赖 `context.register_web_api` 挂载路由，常因插件未重载/路由未注册出现「接口 404 / 6s 超时」。
  - 新增 `standalone_webui.py`：用 aiohttp 启动一个**独立端口**的 HTTP 服务，直接提供静态前端（`pages/anima-console-vue/`）+ 全部后端 API（复用存储层 gallery/quota/oplog/token_store），浏览器访问 `http://服务器IP:端口` 即可，不依赖 AstrBot 路由。
  - `main.py`：`__init__` 初始化 `standalone_webui`；`initialize` 按配置启动、`terminate` 优雅停止（AppRunner 非阻塞，与 AstrBot 事件循环共存）。
  - 配置（`_conf_schema.json` 新增 `webui_standalone` 块）：`enabled`（是否启动）、`port`（端口，默认 8848）、`token`（访问口令，留空不鉴权）。修改后需重启插件生效。
  - 鉴权：设置 token 后，页面首次访问需输入口令（前端存 localStorage），所有 `/api/*` 请求需带 `Authorization: Bearer <token>` 或 `?token=`。
  - API 覆盖：config/schema、logs、records、oplog、gallery（stats/search/thumb/image/star/delete/restore/purge/tags/trash/backup）、quota（users/reset/config）、token（summary/reset）、stats（ranking/trend）。
  - 前端 `bridge.ts`：新增独立模式检测——无 AstrBot 桥接时（从独立端口打开），`apiGet/apiPost` 自动走同源 `HTTP /api/<endpoint>`（支持超时 AbortController），内嵌页仍走原 AstrBot 桥接，两者共存。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。
  - 使用：先在插件配置开启 `webui_standalone.enabled` 并设端口（建议同时设 token），重启插件后浏览器访问 `http://127.0.0.1:端口`（内网用服务器实际 IP）。

## v4.4.32

- **oplog 诊断加固**：初始化成功后写入 `oplog_init` 自检事件，用于确认独立操作日志链路是否真正打通。若操作日志页仍为空，请检查：AstrBot 加载的插件目录是否完整包含 `oplog_store.py`（v4.4.31 新增文件），以及 AstrBot 启动日志是否出现 `[init] 操作日志已就绪`（出现且操作日志页有 `oplog_init` 记录 = 链路通）或 `[init] 操作日志初始化失败`（初始化异常，多为插件副本缺少 `oplog_store.py`）。

## v4.4.31

- **新增独立业务操作日志系统（oplog），与 AstrBot logging 完全解耦，保证关键事件不遗漏**：
  - 背景：此前日志页为空、且难以溯源「限额计数 2 但图库/出图记录仅 1」这类对账问题——依赖 AstrBot 的 logger 传播链不可靠，且业务事件没有结构化记录。
  - 新增 `oplog_store.py`：独立 SQLite（`data_dir/oplog.db`），结构化记录 `ts / event / user / session / summary / detail / ref_sha / extra`，事件类型包括：`draw_success`（生图成功）、`draw_fail`（生图失败）、`gallery_dedup`（图库去重命中）、`gallery_new`（图库新增）、`quota_inc`（限额扣减）、`quota_reset`（限额重置）、`config_save`（配置保存）、`gallery_delete/restore/purge/star/tags`（图库操作）。全程 try/except，日志失败绝不影响主流程。
  - `main.py`：
    - `__init__` 初始化 `self.oplog = OpLogStore(data_dir)`；
    - 出图成功 yield 后写 `draw_success`（带 user / seed / sha256 前 16 位 / 尺寸 / 耗时 / 工作流）；
    - 限额扣减 `_record_draw_used` 写 `quota_inc`（记录 total/hour/day，标注「每次成功出图 +1，与图库去重无关」）；
    - 新增 `_oplog_dedup` 回调，`archive_image` 去重命中时写 `gallery_dedup`（use_count 变化，解释「图库不新增行但限额照加」）。
  - `image_store.py`：`archive_image` 新增 `on_dedup` 回调参数，去重命中（含仅计数+1 的两处分支）时通知调用方写 oplog。
  - `webui_api.py`：新增 `GET /oplog` 接口（分页 + 事件/关键词/用户筛选）；配置保存、限额重置、图库收藏/删除/恢复/彻底删除/打标签等系统操作均写 oplog。
  - 前端日志页：新增「操作日志」tab，结构化表格展示（时间/类型/用户/摘要/详情），支持按事件类型筛选、搜索、分页。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。

## v4.4.30

- **修复 WebUI 日志页长期为空**：
  - 背景：`/logs` 接口（读内存 `LOG_BUFFER` + 落盘 `data_dir/webui.log`）在出图后仍返回空，怀疑插件业务日志未进入 `logging.root` 的 handler 链。
  - 加固：`_install_webui_log_handler` 不再只把 handler 挂到 `logging.root`，而是**同时挂到 root 与本插件使用的 AstrBot logger**（`from astrbot.api import logger`），并强制 `propagate=True`，避免 AstrBot 自定义 logger 的传播链被关闭时业务日志丢失。内存环形缓冲 + `webui.log` 落盘两个 handler 一并双挂。
  - 若升级后日志页仍空，请确认：AstrBot 实际加载的插件副本是否为最新（非工作区代码未同步）；并检查插件数据目录下 `webui.log` 是否生成。

## v4.4.29

- **新增业务操作日志（解释「限额计数 > 图库/出图记录条数」对账问题）**：
  - 背景：用户反馈「当前小时只出 1 张图，但限额统计显示 2、图库/出图记录只能找到 1 张」。根因是图库 `archive_image` 按内容寻址（sha256）去重——产出与已有图完全相同的图片时只 `use_count+1`、不插入新记录，而限额 `record_used` 每次成功出图都 +1，两者口径天然不一致。
  - 新增日志（`data_dir/webui.log` 落盘 + WebUI 日志页可读）：
    - `image_store.archive_image` 去重命中时打印 `sha256 前 16 位` 与原/新 `use_count`，明确「本次不插入新行」；
    - `main.py` 出图成功 yield 后打印 `user / seed / sha256 前 16 位`，便于与图库去重日志对照；
    - `_record_draw_used` 扣减后打印 `total / hour / day` 三项计数（标注「每次成功出图 +1，与图库是否去重无关」）。
  - `quota_store.QuotaStore` 新增 `peek(user_id)` 只读方法，供日志读取当前计数（不改计数）。
  - 注：前端日志页此前"空"因无业务日志，现已补全；后端 `_install_webui_log_handler` 在 `initialize` 已挂载 root logger，业务日志会自动进日志页。本次无前端改动，日志页逻辑不变（`apiGet("logs")` 读 `webui.log`）。

## v4.4.28

- **前端 WebUI：放宽 `lora/fetch`（C 站抓取）前端超时从 6s 到 60s**：
  - 根因：`bridge.ts` 的 `apiPost` 默认超时仅 6s（`withTimeout` 兜底文案即此前报出的「`POST lora/fetch` 超时（6s 无响应，可能后端路由未注册或插件未重载）」），而后端 `lora_fetch` 最坏耗时约 10s（C 站 API）+ 6×15s（逐张下载候选封面），C 站稍慢即被前端 6s 先断开。
  - 修复：`apiPost` 新增第三个可选参数 `{ timeout }` 透传给 `bridgeRequest`；`LorasView.vue` 与 `WorkflowsView.vue` 的 `lora/fetch` 调用均传入 `{ timeout: 60000 }`，对应 `message.loading` 时长同步放宽到 60s。其他接口维持默认 6s 不变。
  - 注：此改动仅解决「路由存在但 C 站慢 → 前端先超时」一类问题；若仍报「未找到该路由」（404），属后端路由未注册，需确认 AstrBot 实际加载的插件副本是否为最新（非工作区代码未同步）。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。

## v4.4.27

- **前端 WebUI 弹窗修复（LoRA 编辑/删除）**：
  1. **编辑/删除确认弹窗点击后不关闭**：根因为 `apiPost` 失败路径未兜底关闭——`saveEdit` 的 `editShow.value=false` 写在 `try` 内 `apiPost` 之后，保存接口一抛错（走 `catch`）就不关；`removeLora` 的 `onPositiveClick` 为 async，`apiPost` 一旦 reject，Naive `useDialog` 在回调 rejected 时不自动关闭弹窗（但 `splice` 已同步执行，故表现为「已经删了但弹窗还在」）。修复：`saveEdit` 将 `editShow.value=false` 移入 `finally`，无论成功失败都关闭（失败已有错误提示）；`removeLora` 改为乐观更新（确认即同步移除前端项并立即 resolve 关闭弹窗，保存改为后台 `.then/.catch`，失败则重新 `load()` 还原列表）。
  2. **编辑弹窗占满屏幕**：`n-modal` 经 teleport 渲染到 `<body>`，其 `class="lora-modal"`/`class="wf-modal"` 宽度样式定义在**组件 scoped `<style>`** 内，scoped 属性选择器无法命中 teleport 元素，导致 `width:680px; max-width:92vw` 完全失效、弹窗占满全屏。修复：将 `.lora-modal`/`.wf-modal` 宽度定义从 LorasView/WorkflowsView 的 scoped 样式迁移到 `App.vue` 的**全局非 scoped `<style>`**，桌面固定宽度、移动端限宽 `92vw`。（`CoverPicker` 本就用内联 `min(680px,92vw)`，不受影响。）
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。

## v4.4.26

- **前端 WebUI 移动端适配（第二轮修复）**，针对实测发现的四个问题：
  1. **下拉/选择项文本过长不换行**：在全局样式（App.vue 非 scoped `<style>`）中给 Naive `n-base-select-option__content` 与已选文本 `n-base-selection__input` 加 `white-space:normal; word-break:break-word; overflow-wrap:anywhere`，长选项在移动端自动换行不再溢出/截断。
  2. **分页器移动端放不下**：`Pager.vue` 新增 `@media (max-width:768px)`——隐藏「每页数量选择器」与「跳页」控件，仅保留页码（n-pagination 自身会折叠为省略号），整体居中并允许横向滚动兜底；`.pager` 加 `justify-content:center; width:100%`。
  3. **图库页面看不到图片**：根因为上一版把 `.app-content` 改为 `overflow:auto` 破坏了 `height:100%` 高度链，导致图库/表格的 `flex:1` 内部滚动区塌缩。修复为移动端 `.app-content` 仍 `overflow:hidden` 由各视图内部自管滚动；同时给 `.gal-item` 加 `min-height:200px` 兜底、`.gal-grid` 加 `align-items:start` 防止 grid `stretch` 拉伸破坏 `aspect-ratio`、`.gal-scroll` 加 `min-height:320px` 兜底，保证封面在短屏也可见。
  4. **表格高度太低（短屏手机看不到内容）**：图库/限额/日志三处表格容器原本仅靠 `flex:1 + min-height:0`，在移动端被上方标题/工具栏堆叠挤占而压扁。现给 `.gal-scroll`/`.table-wrap`/`.table-scroll` 在移动端分别加 `min-height:320/280/300px` 兜底，保证至少可见数行且内部滚动。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。

## v4.4.25

- **前端 WebUI 移动端适配（响应式）**：未改组件结构，纯 CSS 媒体查询（断点 `max-width: 768px`），不依赖 AstrBot 外层 iframe 是否带 viewport（已确认 `index.html` 含 `<meta name="viewport">`）。
  - 整体布局：≤768px 时 `.app-shell` 由横向改为纵向，左侧 200px 侧边栏改为**顶部横向导航条**（菜单可横向滚动、隐藏品牌副标题、隐藏折叠按钮），主区全宽并改用 `100dvh` 规避移动端地址栏高度抖动。
  - 各视图 `view-head`（标题 + 按钮）改为竖向堆叠、按钮换行；工具栏固定宽度控件（搜索框 280/220/200/320px、分页 140px、类型选择 110/120px 等）在窄屏改为全宽换行，避免溢出。
  - 表单类：ConfigField 的 int/number 框（140px）与滑块并排改为纵向堆叠；ConfigSection / WorkflowsView / LorasView 的 `form-grid` 由两列退化为单列。
  - 图库：网格在窄屏退化为单列（并兜底 `minmax(140px,1fr)`）；**移动端无 hover，收藏/删除/重载图标改为常显**，否则手机无法操作。
  - 弹窗：LorasView（520/680px）、WorkflowsView（720px）的内联固定宽度改为 class + `max-width:92vw`，移动端限宽 92vw 防溢出；ImageViewer 详情弹窗图片 `max-width:92vw`、图生图并排改纵向堆叠、信息面板转底部。
  - 构建产物 `pages/anima-console-vue/` 已重新生成并通过 `vite build`（2851 模块，0 错误）。

## v4.4.24

- **图库封面右上角新增收藏 / 删除图标**：
  - 收藏（★）：点击直接切换收藏/取消收藏，复用 `gallery/star`；已收藏时星标高亮且常显（便于辨认）。
  - 删除（🗑）：点击弹出 `n-popconfirm` 二次确认「确定删除这张图片吗？将移入回收站。」，确认后移入回收站（沿用 `gallery/delete`，不重复弹整页 `dialog`）。
  - 两个图标 hover 整卡时浮现；点击均 `@click.stop` 避免误触发大图查看。删除逻辑从 `onDelete` 拆出核心 `deleteImage`，查看器内的整页确认弹窗仍走 `onDelete`。

## v4.4.23

- **修复插件页在 sandbox iframe 下因 localStorage 抛 SecurityError 而整页崩溃**（这才是「接口已返回数据但封面不渲染」的真相，而非之前的响应式推测）：AstrBot 插件页运行在缺少 `allow-same-origin` 的 sandbox iframe 中，直接访问 `localStorage` 会抛 `SecurityError`，导致 JS bundle 在初始化阶段中断、后续所有逻辑（含缩略图渲染）都不执行。
  - 新增 `webui-src/src/api/storage.ts`：`lsGet/lsSet` 安全封装，首次访问探测可用性，失败时降级到内存 Map，**绝不向上抛错**。
  - 三个视图（GalleryView / LogsView / TokenView）中所有裸 `localStorage` 调用统一替换为 `lsGet/lsSet`，覆盖每页数量缓存、NSFW 模糊开关等。
  - 注：sandbox 下 localStorage 不可用时会降级为内存存储，刷新页面后“每页数量”等偏好不再持久（仅本次会话有效），但页面不再崩溃。

## v4.4.22

- **修复图库封面「接口已返回 base64 但不渲染」**：根因是 `thumbCache` 用动态 key（sha）赋值，在 `v-for` + 异步预取的场景下 Vue 不一定对该新增 key 建立响应式依赖，导致 data URL 写进了缓存但 `<img :src>` 不重渲染。
  - 进入列表即**预填充 `thumbCache` 的所有 key 为 undefined**，确保首屏就建立响应式依赖，后续赋值必然触发重渲染。
  - 缩略图请求超时从 6s 放宽到 15s（大原图 Pillow 压图可能更久，避免被 `withTimeout` 吞掉）。
  - 封面 `<img>` 增加 `@error` 兜底：data URL 解码失败（损坏/超大）时标记 `thumbFailed` 并显示「重载封面」按钮，可单独重试。

## v4.4.21

- **修复图库部分封面“一直不加载” + 新增单图重载封面**：
  - 根因：列表首屏对每页所有图（默认 20 张）一次性并发请求缩略图，一次性压垮后端导致部分请求超时失败；且失败被 `.catch(() => {})` 静默吞掉，失败的图永久停留在占位符、无重试入口。
  - 改为**限并发预取**（默认最多 4 个在途请求），显著降低批量超时；失败的 sha 记入 `thumbFailed`，不再被永久丢弃。
  - 网格每一项在封面未加载/加载失败时，**hover 显示「↻ 重载封面」按钮**，点击单独重新拉取该图缩略图（后端仍生成失败会提示“原图可能已损坏”），可针对个别图反复重试。

## v4.4.20

- **新增人工标记/取消 NSFW（误判纠正）**：自动 NSFW 检测存在误判，现支持在大图查看器中手动纠正。
  - 后端 `image_store.set_nsfw(sha256, on)` 直接写入 `nsfw` 字段并置 `nsfw_checked=1`（人工已确认，不会被一键扫描覆盖回去）。
  - 新增接口 `POST /gallery/set_nsfw {sha, on(0/1)}`。
  - 前端大图查看器（ImageViewer）在 NSFW 行新增「标记为 NSFW」/「取消 NSFW」按钮：未标记/未检测图显示「标记为 NSFW」，已标记图显示「取消 NSFW」；操作成功后会同步更新画廊网格的 NSFW 状态，无需刷新。

## v4.4.19

- **分页每页数量缓存到 localStorage，刷新页面后保持不变**：图库（GalleryView）、出图记录（LogsView）、配额明细（TokenView）三个页面的每页数量，切换后写入 `localStorage`（key 分别 `anima_gallery_page_size` / `anima_logs_page_size` / `anima_token_page_size`），页面刷新或重开仍保留上次选择，不再每次重置为默认值。

## v4.4.18

- **GIF 图生图只取第一帧，避免连发多张图**：此前传入 GIF 做图生图时，整张动图原样上传给 ComfyUI，其 LoadImage 节点会把多帧展开导致「连续发送很多张图片」。现图生图注入前统一检测 GIF，用 Pillow 提取首帧转存为静态 webp（落 temp/ 目录，随 24h 清理），再上传/注入/归档，确保只取第一帧。非 GIF、或无 Pillow 环境会降级为原样上传。

## v4.4.17

- **大图「取消模糊/设为模糊」改为纯前端临时切换**：此前该按钮会调用 `gallery/set_blur` 写数据库。现改为只在前端临时切换该大图的模糊/清晰，便于查看，**不请求接口、不改数据库**；关闭大图重新打开后按 `nsfw`/`nsfw_blur` 恢复默认。按钮文案与高亮状态改为反映当前临时模糊状态。

## v4.4.16

- **大图单图检测后，图库列表封面实时同步模糊（无需刷新/重新搜索）**：此前在大图里点「检测」只更新大图局部对象，图库列表封面需刷新页面或重新搜索才会模糊。现大图检测成功后派发 `anima:nsfw-updated` 事件，图库列表监听并按 sha 在本地同步该图的 `nsfw`/`nsfw_score`/`nsfw_checked`，封面即时模糊，**不重新请求接口**；组件卸载时自动清理监听。

## v4.4.15

- **出图记录（日志页）缩略图支持 NSFW 模糊**：出图记录表格的预览缩略图，对 NSFW 图同样按 `nsfw` + 全局模糊开关 + 单图 `nsfw_blur` 做模糊处理（并在右上角加 🔞 角标），与图库封面行为一致。逻辑复用图库相同的全局开关（localStorage `anima_gal_nsfw_blur`）。

## v4.4.14

- **修复打包漏掉 `nsfw_detector.py` 导致 NSFW 检测不可用**：此前 `build_zip.ps1` 的打包清单（`$includeList`）没有包含新模块 `nsfw_detector.py`，导致打出的所有 zip 都缺该文件，用户安装后 `import nsfw_detector` 失败，检测器加载报「无法加载检测器」——即使依赖已装好也无用。现已在打包清单中补上 `nsfw_detector.py`。

## v4.4.13

- **NSFW 依赖安装提示改回 Naive UI dialog 并提升层级**：上一版改用 `window.alert`，但 WebUI 运行在 iframe 沙箱中（未开 `allow-modals`），`alert()` 被浏览器直接忽略、无法弹出。现改回 Naive UI `dialog.warning`，并在 App.vue 全局把 dialog 容器的 z-index 提升到 10001（高于大图查看器的 9999），确保弹窗既正常显示、又不被大图查看器遮挡。

## v4.4.12

- **NSFW 检测不可用时返回真实错误原因**：此前检测器不可用一律返回硬编码「请先安装 onnxruntime + opennsfw-onnx」，掩盖了真实原因（可能是依赖缺失，也可能是模型初始化失败/模型文件异常）。现检测器记录 `last_error`，`gallery/check_nsfw`、`gallery/scan_nsfw` 返回「NSFW 检测不可用：<真实原因>」，便于定位问题（如模型加载失败的具体异常）。

## v4.4.11

- **NSFW 检测器装好依赖后无需重启即可生效**：原实现把「依赖缺失」标记为永久失败（`_init_failed`），且依赖可用性在模块加载时缓存，导致用户后装 `onnxruntime`/`opennsfw-onnx` 后仍提示不可用、必须重启进程。现改为每次调用动态检查依赖、失败不再锁死（仅 10 秒防刷日志），装好依赖后重新点「检测」即可直接生效（无需重启）。

## v4.4.10

- **修复大图内点检测时 NSFW 安装提示被大图遮挡**：NSFW 检测不可用的弹窗原用 Naive UI dialog，其层级低于大图查看器的全屏覆盖层（z-index: 9999），会被遮住。改为原生 `window.alert`，必定显示在浏览器最顶层，不再被遮挡。

## v4.4.9

- **NSFW 依赖安装提示改为引导 AstrBot 内置安装入口**：把弹窗文案从「执行 pip 命令」改为「在 AstrBot 日志页右上角的『安装 pip 库』入口依次填入 `onnxruntime`、`opennsfw-onnx` 并安装」，更贴合实际使用方式。

## v4.4.8

- **NSFW 检测不可用时前端弹窗提示安装命令**：单图「检测/重新检测」与「一键检测」在 NSFW 检测器不可用（未安装 `onnxruntime` + `opennsfw-onnx`）时，会弹出明确提示框，给出安装命令 `pip install onnxruntime opennsfw-onnx`，替代原来仅有的一行错误 toast。
- 注：单图检测首次点击可能同时发出多个「不同路由风格」的探测请求（`/gallery/check_nsfw`、`/astrbot_plugin_comfyui_anima/gallery/check_nsfw` 等），这是前端 bridge 为兼容不同 AstrBot 版本的路由自动探测机制，属一次性开销，成功后会自动缓存路由风格，后续请求只发一个。

## v4.4.7

- **修复 `gallery/check_nsfw` 400（单图检测）**：`check_nsfw` 查询用精确匹配 `sha256=?`，前端传的 sha 可能是内容寻址前缀（sha256[:16]），导致查不到而返回 400「未找到该图」。现改为前缀 `LIKE` 匹配并取第一条；`set_nsfw_blur` / `clear_nsfw_blur` 同步改为「先解析完整 sha 再更新」，避免前缀 UPDATE 命中多条。

## v4.4.6

- **修复 NSFW 检测报 `No module named 'nsfw_detector'`**：`image_store` 里对 `nsfw_detector` 用的是绝对导入 `from nsfw_detector import ...`，在 AstrBot 用相对导入加载插件时找不到模块，导致归档 NSFW 检测异常（但不阻塞归档）。现改为模块级兼容性导入 `_get_detector()`（先试相对导入 `.nsfw_detector`，再退回到绝对导入 `nsfw_detector`），所有调用点统一走它；检测不可用时静默降级。

## v4.4.5

- **修复图库封面出图人昵称与类型标签重叠**：`.gal-user` 昵称标签原先放在顶部 overlay 容器内，`bottom` 定位相对该容器导致与左上角「文生图/图生图」类型标签重叠。现将其移出 overlay、作为封面项直接子元素，定位到图片左下角（`z-index` 提升），不再重叠。
- **出图人昵称最多显示 8 个字**：封面昵称超过 8 个字自动省略号截断，完整昵称悬浮显示（title）。

## v4.4.4

- **配置栏分区调整**：将原本合并在一起的「服务器与模型」折叠组拆分为三个**同级独立**折叠组——「服务器与模型」（`comfyui_servers`）、「工作流列表」（`workflows`）、「LoRA 列表」（`loras`），默认展开服务器与工作流两组，更清晰便于单独配置。

## v4.4.3

- **图库「一键检测」后台扫描 + 进度查看**：
  - WebUI 图库工具栏新增「一键检测」按钮，后台线程扫描所有未检测的图（不阻塞界面）。
  - 新增「↻」刷新进度图标按钮，手动点击即可查看检测进度（**不用轮询**）；检测中显示「检测中 已扫/总数」，完成后显示上次检测结果并自动刷新列表。
  - 后端 `gallery/scan_nsfw` 改为后台启动，新增 `gallery/scan_nsfw_progress` 查进度；`image_store` 新增 `scan_nsfw_start()` / `scan_nsfw_progress()` / `scan_nsfw_progress`，后台线程用独立 SQLite 连接避免跨线程共用连接。

## v4.4.2

- **图库封面显示出图人昵称**：封面左下角新增昵称角标（`user_name`），一目了然知道图是谁出的；昵称过长自动省略。
- **大图详情 SHA 完整展示**：SHA 不再截断，改为完整展示全部字符（可点击复制），方便对照/检索。

## v4.4.1

- **大图查看器支持单张 NSFW 检测**：WebUI 图库大图详情的信息面板新增「检测 / 重新检测」按钮（未检测过显示「检测」，已检测显示「重新检测」），点击即对该张图做一次 open_nsfw 检测并写回 `nsfw` / `nsfw_score` / `nsfw_checked`，随后立即刷新显示判定与置信度。未检测的旧图也能单独测出分数。
- **新增后端接口** `gallery/check_nsfw`（GET ?sha=xxx）用于单图检测；`image_store` 新增 `check_nsfw(sha)` 方法。

## v4.4.0

- **图库新增 NSFW 检测**（基于本地 open_nsfw 模型，离线、保护隐私）：
  - 新增 `nsfw_detector.py`：懒加载封装 `opennsfw-onnx`（模型内置在包内约 22.5MB），容错降级——依赖/模型缺失时检测不可用但不阻塞归档。
  - `images` 表新增 `nsfw` / `nsfw_score` / `nsfw_blur` / `nsfw_checked` 字段（旧库自动 ALTER 兼容）。
  - 归档时自动检测打标；`image_store.scan_nsfw()` 支持手动扫描旧图（默认只扫未检测的，可全量重扫）。
  - 检索支持 `nsfw` 筛选（`0`=仅常规 / `1`=仅 NSFW / 空=全部），统计接口返回 NSFW 数量与未检测数。
- **WebUI 图库 NSFW 展示控制**：
  - 工具栏新增 **NSFW 筛选**下拉（全部 / 仅常规 / 仅 NSFW）与 **NSFW 模糊**全局开关（localStorage 持久化，一键开/关所有 NSFW 图模糊）。
  - NSFW 缩略图默认模糊遮罩（🔞 点击查看）；大图查看器同样默认模糊，可「点击查看」临时看清。
  - **单图开关**：大图信息面板提供「取消模糊 / 设为模糊」按钮，写回该图 `nsfw_blur` 字段；信息面板显示 NSFW 判定与置信度。
- **新增 API**：`gallery/set_blur`（单图模糊设置）、`gallery/scan_nsfw`（手动扫描）；`gallery/search` 支持 `nsfw` 参数。
- **依赖**：`requirements.txt` 新增 `onnxruntime>=1.15.0`、`opennsfw-onnx>=0.1.0`（未安装时 NSFW 检测不可用，不影响其余功能）。
- **配置**：`gallery` 新增 `nsfw` 子配置（`enabled` 启用开关 / `threshold` 判定阈值默认 0.5 / `blur_default` 默认模糊）。
- 注意：本版暂**不**对检索/发图做强制 NSFW 过滤，仅标记 + 模糊展示，过滤策略后续版本再定。

## v4.3.8

- **修复 Token 统计接口报 `name 'conn' is not defined`**：v4.3.7 为支持「今天/昨天」自然日区间改造 `token_store` 时，`list_detail` 误删了 `conn = self._conn_get()` 定义但仍用 `conn.execute`，导致打开 Token 用量页报错。已改为直接 `self._conn_get().execute`。经临时脚本验证，`query_summary` / `list_scenes` / `list_users` / `list_models` / `list_detail` / `count_detail` / `list_daily` / `list_hourly` 在昨天区间下均正常。

## v4.3.7

- **WebUI 统计页加「昨天」选项**：生图统计（`StatsView`）与 Token 用量（`TokenView`）的时间范围都新增「昨天」，可查看昨天的生图排行 / token 用量（后端 `stats/ranking`、`token/summary` 支持 `yesterday` 参数，按自然日「昨天 0 点到今天 0 点」精确统计）。
- **修复 Token 用量「今天」在凌晨混入昨天数据**：此前「今天」的汇总卡片用的是「滚动 24 小时」（`query_summary(days=1)`），凌晨 1 点前点「今天」会把昨天一整天的数据也算进来。现「今天」改为**自然日今天 0 点起**统计（不含昨天），汇总 / 场景 / 用户 / 模型 / 明细 / 趋势全部用自然日边界，与趋势图一致。
- **每日 Token 消耗趋势空态加提示**：趋势为空（无记录）时显示「该时段无 token 消耗记录，请切换范围或等待使用后刷新」，替代原来的「暂无数据」。
- **`/绘图排行` 指令加日期参数**：支持 `/绘图排行 [今天|昨天|周|月|全部]`（默认今天），与 `/绘图统计` 的日期参数一致。`/绘图帮助` 与 README 指令表已同步。

## v4.3.6

- **修复「说发两张但只发一张、第二张怎么都出不来」**：`draw_auto` 的会话级预算 `_llm_draw_budget` 记账有误——它按「剩余配额 allowed」记入时间戳，而不是按「实际出图张数」。导致用户要两张时，第一次只出 1 张却把 3 个预算名额全占满，90 秒窗口内后续调用全被误判「预算用尽」而拦截，第二张永远出不来。现改为只按实际出的张数记账，1 张只占 1 个名额，多张能正常逐张发出，画满上限才停。
- **强化预算拦截提示**：预算用尽时，返回文本明确告知模型「画图已完成、必须简短收尾、【绝对不要】再次调用画图工具或用相同参数重试；只有用户下一条新消息明确要求再画才继续」，避免模型因「任务没完成」反复调用导致重复刷屏。
- **新增同参去重（防模型死循环）**：`comfyui_draw` / `comfyui_img2img` 在 30 秒窗口内用「相同 prompt + 相同 seed」重复调用时，判定为模型无脑重试/死循环，直接拒绝。模型画多张本应改变 seed，相同参数即为重复生成同一张图。伴侣插件主动调用（带 source）不受此限制。

## v4.3.5

- **修正「用户没提数量也出 3 张」的问题**：v4.3.4 引入了 `default_count` 并把「模型没传 count」一律按默认 3 张处理，导致用户只说了「画张图 / 画个女孩」（完全没提数量）也会一次出 3 张，误伤。现彻底修正，去掉 `default_count` 配置，改为由模型按用户意图区分：
  - 用户**完全没提数量**（如「画张图」）→ 固定出 **1 张**（还原 v4.3.3 行为）；
  - 用户说**具体数字**（如「来 3 张」「来 5 张」）→ 按数字出；
  - 用户说**泛化多张表达**（如「来几张 / 再来几张 / 发点图 / 一些 / 多画几张」，没给具体数字）→ 出 **3 张**。
  - `comfyui_draw` / `comfyui_img2img` 的工具描述与 `count` 参数说明已同步这三条规则；`count` 参数保留默认 0（未指定），代码未指定时固定出 1 张。`draw_auto` 配置恢复为 `max` / `window` / `admin_exempt` 三项。

## v4.3.4

- **（已废弃）`draw_auto` 拆出 `default_count` 配置**：曾试图区分「默认几张」与「单次上限」，但因把「用户没提数量」误判为默认多张、导致普通请求也一次出 3 张，已在 v4.3.5 移除并修正，此条目仅作记录。

## v4.3.3

- **修复「模型自己连续画图停不下来」**：AI 对话里模型有时会基于上下文中较早的负面反馈（如「画得不对」「再来一张」）自我驱动地反复调用画图工具，一张接一张。现新增会话级出图预算 `draw_auto`（配置项）：
  - `max`（默认 3）：一次用户请求里模型连续画图最多出这么多张，达到即强制停止并提示等待用户指示；
  - `window`（默认 90 秒）：此秒数内连续出图视为同一次请求的连发；
  - `admin_exempt`（默认 true）：管理员是否豁免此限制。
- **支持一次出多张**：`comfyui_draw` / `comfyui_img2img` 新增 `count`（张数）参数，用户说「来 3 张 / 发几张」时按张数出（"几张"未给具体数默认 3 张），逐张循环出图并统一发送。
- **强化出图后返回文本**：明确告知模型「本次已出图，不要自己再主动连续画图；只有用户当前消息明确要求改图/加图/再来几张时才再次调用；也不要基于历史里较早的负面反馈继续画」。

## v4.3.2

- **移除「伴侣插件提示词过滤」功能**：该功能已不再使用，现已彻底移除——删除配置项 `filter_companion_prompt`、专属格式化方法 `_format_companion_prompt` 及其在 `llm_draw` 里的过滤分支。提示词现原样透传给 ComfyUI 出图（通用拆分 `_split_external_prompt` 仍在 `llm_img2img` 中保留使用）。

## v4.3.1

- **`/绘图lora`（`/loralist`）输出简化**：只返回 LoRA 名称，去掉别名/分类/底模/文件等冗余信息；新增可选分类过滤 `角色` / `风格`，如 `/绘图lora 角色` 只列角色 LoRA。
- **新增 `/绘图工作流lora 工作流名`**：列出指定工作流可使用的 LoRA（按底模匹配），如 `/绘图工作流lora 动漫`，方便为某工作流快速选可用 LoRA。`/绘图帮助` 与 README 指令表已同步。

## v4.3.0

- **LoRA / 工作流大图详情修复黑底与关闭交互**：封面大图四周不再出现大片黑色背景（去除 `.iv-imgwrap` 的黑色块，图片浮于暗色遮罩上，视觉更接近图库大图）；点击左侧任意区域（含图片周边）即可关闭大图，右侧字段面板点击不关闭。版本号按规则进位到 4.3.0（小版本 Z 已超 10）。

## v4.2.31

- **LoRA 描述支持渲染 HTML**：此前 LoRA 详情中的描述若含 HTML 标签（如 C 站描述里的 `<strong>`、`<p>`、`<br>`）会以纯文本显示。现新增 `sanitizeHtml` 白名单净化工具，在详情弹窗与大图详情中按净化后的 HTML 渲染描述；仅保留安全标签与属性、剥离脚本/事件属性与危险协议，杜绝 XSS。

## v4.2.30

- **为英文指令补充中文别名**：`/img2img` → `/图生图`（/图转图）、`/loralist` → `/绘图lora`（/绘图LoRA / lora列表）、`/queuestatus` → `/绘图队列`（/队列状态）、`/workflows` → `/绘图工作流`（/工作流列表）。中文别名与英文指令等价，参数解析已同步适配（`_strip_command` 支持多触发词剥离）。`/绘图帮助` 与 README 指令表已改为展示中文指令（标注英文兼容写法）。

## v4.2.29

- **修复 `/loralist` 提示"工作流未配置 LoRA"**：此前 `/loralist` 默认列出的是**当前（默认）工作流**配置的 LoRA，当默认工作流未配置 LoRA 时就会提示「工作流「动漫」未配置任何 LoRA」，与用户「列出全部 LoRA」的直觉不符。现改为默认列出**全局 LoRA 库**（名称/别名/分类/底模/文件）；如需查看某工作流实际启用的 LoRA，加 `--wf 名称` 即可（保留原工作流视图）。

## v4.2.28

- **新增 `/绘图帮助` 指令**：只展示常用中文指令 + 一句话说明的简单帮助（画图/画/绘图/图生图/LoRA/工作流/统计/排行/状态/图库等），别名 `画图帮助`/`作图帮助`/`绘图说明`/`画图说明`；与 `/drawhelp`（完整参数帮助）互补。README 指令表已同步。

## v4.2.27

- **LoRA / 工作流大图详情封面自适应铺满**：封面大图不再保持原始小图尺寸，而是占满左侧展示区（宽度或屏幕高度，取较小约束自适应），`object-fit: contain` 保持比例居中，小图也会放大铺满，浏览体验更好。

## v4.2.26

- **LoRA 新增分类（角色 / 风格）**：LoRA 配置新增 `category` 字段（可选：角色 / 风格 / 未分类）。WebUI LoRA 库新增分类筛选栏（与底模筛选叠加），卡片、详情弹窗、编辑表单与大图详情均展示分类。
- **`comfyui_loras` 工具支持按分类过滤**：新增 `category` 参数（角色 / 风格），输出行附带分类标签；可按「用户要某角色 / 某风格」缩小查询范围。
- **强化 AI 对话的 LoRA 查找引导**：此前用户说「用某某风格 / 某某画风 / 画某某」时，大模型常直接跳过 LoRA 查找、留空 `loras`。现强化 `comfyui_loras` 与 `comfyui_draw`/`comfyui_img2img` 的工具描述：明确要求「用户提到风格 / 画风 / 角色时，即使没给具体 LoRA 名，也必须先调 `comfyui_loras` 查匹配 LoRA 再填入」，减少跳过查找的情况。

## v4.2.25

- **修复 LoRA 抓取未填充 C 站标题（别名）**：此前新版 WebUI 抓取 LoRA 时漏掉了「C 站标题并入别名」逻辑（旧版会把标题追加进别名 keywords）。现恢复：抓取时若 C 站返回标题且当前别名中不存在，则并入别名（支持换行/逗号分隔，避免重复）。触发词、描述、底模等其余字段填充逻辑保持不变。

## v4.2.24

- **修复 LoRA 抓取底模大小写/匹配问题**：此前抓取时直接把 C 站返回的 `baseModel` 原样存入（如 `Anima`），而编辑下拉与底模筛选只有小写选项（`anima` 等），导致无法正确匹配选中。现后端 `lora_fetch` 新增 `_normalize_base_model` 归一化（转小写 + 白名单过滤，不在白名单视为通用），前端 `fetchLora` 同步兜底归一化，并在加载时修正存量 LoRA 的大写/非法底模值。

## v4.2.23

- **README 图库指令表改为三列**：图库（`/gallery`、`/图库`）指令说明表格从两列（指令/说明）改为三列——指令、中文指令、说明，逐一列出每条子命令的英文与中文写法（列表/搜索/标签/找标签/取图/收藏/取消收藏/收藏列表/保存/统计）；同时去掉已关闭的删除子命令，补上收藏列表（`starred`）子命令。

## v4.2.22

- **README 指令文档补全**：补充此前新增但未写入文档的指令说明——`/绘图统计` 的时间范围参数（昨天/周/月/全部）、新增的 `/绘图排行`、`/拉黑`、`/解黑`、`/黑名单` 指令，以及 `/绘图状态` 展示生图限额配置；并补充图库指令的中文入口 `/图库`（含中文子命令说明）。

## v4.2.21

- **恢复「抓取时选择封面」功能**：此前新版 WebUI 抓取 LoRA / 工作流封面时只自动取第一张候选图，无法像旧版那样选择封面。现新增 `CoverPicker.vue` 封面选择弹窗——抓取返回**多张候选封面**时，弹出候选图网格，点击一张作为封面后再保存；仅一张时仍自动采用。

## v4.2.20

- **LoRA / 工作流「查看封面」改为「大图详情」模式**：点开卡片封面不再弹出居中单图的小预览窗，改为全屏大图详情——左侧大部分区域展示封面大图，右侧展示字段信息（LoRA：名称/别名/底模/模型/默认权重/触发词/预设/描述/C 站链接；工作流：名称/别名/底模/服务器/工作流文件/Anima 模式/默认尺寸/预设 LoRA/C 站链接）。由新组件 `ItemViewer.vue` 统一实现，废弃原 `ImagePreview.vue`。
- **移除「加载预览」等待提示**：此前每次点开封面都会弹出 `message.loading("加载预览…")` 且要等封面请求返回才消失。现改为打开大图详情后由组件内"封面加载中…"占位反馈，封面异步加载完成即显示，不再弹全局 loading 消息。

## v4.2.19

- **WebUI LoRA 库新增底模分类筛选**：LoRA 库卡片区上方新增「底模」筛选栏，可按底模分类（全部 / anima / z-image-turbo / krea2 / illustrious / 通用）一键过滤卡片，并实时显示各分类下的 LoRA 数量；当前分类无结果时给出空态提示。仅新版控制台生效（旧版控制台不再维护）。

## v4.2.18

- **WebUI 配置页字段名中文化**：配置项标题与子字段名不再直接显示英文 key，改为优先使用 schema 里的中文 `label`/`description`（如「ComfyUI 服务器列表」「服务器名称」「服务地址」等），无中文说明时才回退英文字段名。新旧控制台均生效；新版还支持在 schema 中为特定字段额外指定 `label` 作为更短的标题。

## v4.2.17

- **新增绘图黑名单**：可按「群」和「人」（QQ 号）拉黑，被拉黑后无法使用任何画图方式（指令绘图、AI 对话画图、伴侣插件等），在统一入口 `_do_draw` 拦截。新增 `blacklist` 配置块（`enabled` 开关、`users` 黑名单用户、`groups` 黑名单群、`admin_exempt` 管理员豁免）。
- **新增黑名单管理指令**（仅管理员）：`/拉黑 [群|用户] 号码`、`/解黑 [群|用户] 号码`、`/黑名单`（查看列表）。拉黑时自动开启开关；全部清空时自动关闭。
- **WebUI 配置页**：「权限与图库」分组新增「绘图黑名单」配置项（新旧控制台均已接入），管理员可直接在控制台编辑。

## v4.2.16

- **修复 WebUI 封面加载慢 / 加载几张就停住**：LoRA 库与工作流界面的封面图此前在加载时用 `forEach` **一次性并发**发起所有请求，而 AstrBot 的 postMessage 桥接是串行/有限并发处理，请求一次涌入会把桥接队列堵死，表现为"加载几张就卡住、必须刷新页面才能恢复"。现新增全局 `v-cover-lazy` 指令，封面改为**视口懒加载 + 受限并发（同一时刻最多 3 个）+ 全局缓存**：只加载可见封面、队列逐个处理不再打爆桥接、同名封面只请求一次。封面大图预览改为点击时按需请求。

## v4.2.15

- **`/绘图统计` 支持时间范围**：新增可选参数，`/绘图统计`（默认今天）、`/绘图统计 昨天`、`/绘图统计 周`（最近一周）、`/绘图统计 月`（最近 30 天）、`/绘图统计 全部`。出图数量、Token 用量、热门工作流 Top5 均跟随所选范围。新增 `image_store.range_stats(start_ts, end_ts)` 支持按任意时间区间精确统计成功成品图数量与热门工作流。
- **`/绘图状态` 展示生图限额配置**：末尾追加「生图限额配置」——开关状态、全局总次数/每小时/每天上限（-1 为不限）、管理员豁免，以及今日全群已生图次数。
- **新增 `/绘图排行` 指令**（别名 `drawrank`/`画图排行`）：展示今日生图数量前五名用户（🥇🥈🥉...），排除伴侣插件等非真人自动生图记录，展示 QQ 号对应的昵称（无昵称回退为 QQ 号）。

## v4.2.14

- **WebUI 大图查看器补充「触发消息」展示**：图片信息面板新增「触发消息」字段，展示触发本次生图的用户消息原文（`trigger_msg`），与旧版控制台一致；该字段来自图库元数据，无记录时自动隐藏。
- **Token 用量「明细」改为后端分页**：明细不再一次性返回全部（数据量大时会拉取过重、渲染卡顿）。`token/summary` 新增 `page`/`page_size` 参数，后端 `list_detail` 支持 `offset`/`limit` 并新增 `count_detail` 统计总条数；WebUI 明细表下方接入分页器，支持每页条数切换（10~100），切换时间范围/合并插件时自动回到第 1 页。

## v4.2.13

- **修复 v4.2.12 升级后 Token 统计报 `no such column: hour_bucket`**：旧库仍为 `day_bucket` 结构时，`_init_db` 在**迁移旧表之前**就执行了 `CREATE INDEX ... ON llm_usage (hour_bucket)`，旧表尚无该列导致建索引抛错、迁移流程中断，后续查询 hour_bucket 列失败。现调整初始化顺序为「先迁移旧表、再建 hour_bucket 索引」，旧库升级后会自动平滑重建为小时粒度，不再报错。

## v4.2.12

- **修复 Token 趋势图「最后一条像总数」**：`/绘图统计` 与 WebUI「Token 用量」里，今天/近 1 天范围展示的「每日趋势」实际渲染的是小时数据。旧版 `llm_usage` 表按「天」聚合、只记最后一次调用时间，小时趋势用该时间近似归属，导致**一天内的累计用量被整体堆到最后一次调用所在小时**，最后一根柱子虚高、看起来像当日总量。
- **存储改为小时粒度**：`llm_usage` 主键由 `(user_id, scene, model, 日期)` 迁移为 `(user_id, scene, model, 小时)`（`hour_bucket=YYYY-MM-DD HH:00`），新写入按小时落桶。旧数据自动迁移（旧记录按 `updated_at` 所在小时近似归属，每日总量不变）。
- **小时趋势精确化**：`list_hourly` 直接按 `hour_bucket` 分桶，不再用 `updated_at` 近似，一天的用量准确分配到实际发生的各小时。`list_daily` 改按小时桶的日期前缀聚合，每日数值不受影响。
- **明细展示细化**：WebUI Token 明细由「日期」粒度细化为「时间」（小时）粒度，字段 `hour_bucket`（`YYYY-MM-DD HH:00`）。

## v4.2.11

- **修复 WebUI 保存/新增/LoRA 报错**：`Failed to execute 'postMessage' on 'Window': [object Object] could not be cloned`。根因是前端把 Vue 响应式 Proxy 对象直接作为 body 传给 astrbot 的 `apiPost`，而 `postMessage` 走结构化克隆、无法克隆 Proxy。现已在 `bridge.ts` 的请求层统一做深拷贝（`JSON.parse(JSON.stringify(...))`），从源头剥掉 Proxy，覆盖所有页面的保存/新增/删除/上传封面等调用点。

## v4.2.10

- **优化 `/绘图状态` 延迟测量口径**：`probe()` 改为「预热 + 正式测量」两段式——先发一次请求完成建连/DNS/TLS 握手（不计时），再用同一连接池内第二次请求的耗时作为 HTTP 往返延迟，避免把握手/建连开销误报成高延迟。
- **缩短探测超时**：状态指令整体超时由 20s 收到 15s，探测用独立的 8s 短超时，服务器不可达时更快返回、不会干等。
- **输出口径更正**：状态展示由「延迟」改为「HTTP 往返」，该值反映的是网络往返 + 服务器响应时间，避免与纯网络延迟混淆。

## v4.2.9

- **修复 `/绘图状态` 探测兼容性**：`system_stats` 在部分 ComfyUI/中转站上返回 404。连通性与延迟探测改为访问根路径 `/`（任何 ComfyUI 均存在），只检查 HTTP 2xx 不解析内容；队列状态仍优先查询 `/queue`，不可用时回退本地队列近似。

## v4.2.8

- **优化 `/绘图状态` 探测与展示**：
  - 不再请求 `system_stats`：改为用 `/queue` 一次请求同时测得连通性、延迟与队列状态，更轻量。
  - 只探测「使用中」（已启用）的服务器；展示不暴露服务器名称与 IP 地址，统一以「服务器 N」称呼。

## v4.2.7

- **`/绘图统计` Token 用量友好化**：今日 Token 用量改用 `K/M/B` 单位展示（如 `12.3K`、`1.23M`），与 WebUI 前端口径一致。

## v4.2.6

- **修复 `/绘图统计` 工作流统计口径**：工作流出图数量与平均耗时原按「全部历史」统计，现改为按「今日」统计（时间范围与今日出图数量一致）。
- **工作流统计改为前 5 名**：`/绘图统计` 中热门工作流出图从 3 个扩展为最多 5 个，README 描述同步更新。

## v4.2.5

- **README 指令表格化**：将「指令」「中文绘图指令」「图片画廊 /gallery」等指令说明统一改为表格形式展示，并补录 v4.2.4 新增的 `/绘图统计`、`/绘图状态` 两个指令。

## v4.2.4

- **新增 `/绘图统计` 指令**：返回累计出图数量、今日出图数量、今日 Token 用量，以及出图最多的前 5 个工作流的出图数量与平均耗时。
- **新增 `/绘图状态` 指令**：逐台返回绘图服务器连通情况（延迟 ms）、正在出图还是空闲、队列数量。优先读取 ComfyUI `/queue` 实时队列，接口不可用时回退本地排队队列近似。
- ComfyUI 客户端新增 `ping()`（连通性 + 延迟探测）与 `get_queue()`（实时队列查询）方法。

## v4.2.3

- **默认打开新版 WebUI**：此前 AstrBot 按 `pages/` 目录扫描顺序决定默认入口，旧版目录 `anima-console` 字典序在新版 `anima-console-vue` 之前，导致点进 WebUI 默认进旧版。已将旧版目录重命名为 `anima-console-vue-legacy`（字典序排在新版之后），并同步更新 `metadata.yaml` pages 名称与打包脚本路径，现无论 AstrBot 按目录顺序还是 metadata 顺序，默认入口都是新版「Anima 控制台」。旧版仍保留在页面列表中可回退。

## v4.2.2

- **大图查看器点击空白处即可关闭**：此前关闭点击只在遮罩层最外层 `.self` 上监听，但内容区 100% 铺满遮罩导致几乎无法命中；现图片四周留白、图片区及遮罩均可点击关闭，图片/信息面板内点击不误关。
- **大图加载提速（局域网本地仍偏慢的根因修复）**：大图走 AstrBot 桥接 JSON 需 base64 内联，体积膨胀且串行，瓶颈在客户端数据流而非网络。
  - 图生图时主图与参考图由「串行等待」改为**并行加载**（`Promise.all`）。
  - 主图限制 `size=1400`、参考图 `size=900`（后端转 data URL 时缩小），显著减小 base64 体积、加快生成与传输。

## v4.2.1

- **主题与 AstrBot 联动**：插件页面的深色/浅色模式现跟随 AstrBot 控制台主题自动切换。
  - 深色触发从仅 `html.dark` 改为同时兼容 AstrBot 维护的 `html[data-theme="dark"]`。
  - `useTheme` 初始读取 AstrBot context.isDark（或 `html[data-theme]`），并新增 `initThemeBridge`：通过 `bridge.onContext` + MutationObserver 监听 `html` 的 `data-theme`/`class` 变化，AstrBot 切换主题时插件页面（含 Naive UI、面板、图表）即时联动。
  - 面积图同样监听 `data-theme`/`class` 变化，切换主题时重算配色。

## v4.2.0

- **修复深色主题切换无效**：此前 `toggleDark` 只翻转了 `isDark` 状态（驱动 Naive UI 组件），但从未给 `<html>` 添加 `dark` class，导致页面背景、文字及依赖 CSS 变量（`--bg-body`/`--bg-panel`/`--text-*` 等）的自定义样式（面板、图表等）始终停留在浅色。现 `useTheme` 在初始化与切换时同步 `document.documentElement.classList.toggle("dark")`，深色模式整体生效。
- **面积图颜色跟随主题**：`AreaChart` 通过 MutationObserver 监听 `<html>` 的 `class` 变化，主题切换时重算图表配色（曲线/网格/文字/峰值标签等），不再停留在旧主题色。

## v4.1.10

- **Token 数值单位改为 K/M/B**：Token 计量单位统一用业界标准 `K`（千）、`M`（百万）、`B`（十亿），如 `1.2K`、`123.5K`、`3.4M`，替代此前的 `万`/`亿` 中文单位，更符合 token 场景习惯。

## v4.1.9

- **Token 用量数值友好化**：汇总卡片、各表格（场景/模型/用户/明细）的 token 数值及趋势图 Y 轴/峰值标签加单位友好展示，不再被长数字撑爆布局；调用次数保留千分位显示。

## v4.1.8

- **修复 Token 页「每日 Token 消耗趋势」显示为空**：后端每日数据字段为 `total`（`llm_usage` 表按日聚合），前端此前只读 `tokens`/`total_tokens` 导致 Y 值恒为 0、曲线画不出来。现改为兼容 `total`/`tokens`/`total_tokens`，每日趋势恢复正常。

## v4.1.7

- **面积图改进（统计/Token 页）**：
  - 图表占满容器宽度，并随窗口/容器尺寸变化（ResizeObserver + window.resize 兜底）自动重算布局，不再留白。
  - 修复时间刻度重复拼出秒数：近一天/今日趋势的后端已返回 `HH:00`，此前前端又补 `:00` 变成 `HH:00:00`，现按 `HH:mm` 展示。
  - 修复 Token 页「每日趋势」X 轴标签为空：改用后端 `day_bucket`（`YYYY-MM-DD`）作为日期标签。
  - 增大顶部留白，避免峰值标签与 hover 提示被容器裁切遮挡。

## v4.1.6

- **出图记录分页器默认每页 10 条**：此前默认 40 条，改为 10 条，浏览更清晰，分页器依旧支持 10/20/24/40/60/100 自由切换。

## v4.1.5

- **图表彻底改用手写 SVG，移除 VChart**：VChart/ECharts 核心引擎在 AstrBot 沙箱 iframe 下依赖受限的 Canvas 量字 API（`getContext('2d')`、`document.fonts` 等），即便 `renderMode:'svg'` 也无法根治 init 失败。本次新建手写 `AreaChart.vue`（平滑贝塞尔曲线 + 渐变填充 + 光晕 + 网格 + hover 提示 + 峰值标签，观感接近 VChart），统计页「近一天生图数量」与 Token 页趋势图全面替换。
- **构建体积大幅下降**：移除 `@visactor/vchart` 依赖后单文件产物从约 4MB 降至约 1.58MB（gzip 436KB），图表渲染 100% 依赖基础 DOM/SVG API，在沙箱 iframe 下绝对可靠。

## v4.1.4

- **图表仍未显示问题再次修复**：VChart 面积图改用官方最简 spec（仅类型+数据+字段，去掉可能引发 init 失败的复杂配置），并强制 `renderMode: 'svg'`（SVG 渲染），避开 AstrBot 沙箱 iframe 下 Canvas 渲染受限导致图表空白的问题。统计页「近一天生图数量」与 Token 页趋势图现可渲染。
- **修复统计页「今天」范围与「全部」相同**：前端把「今天」的 `days` 参数误传为 `"0"`，而后端映射字典的键是 `"today"`，导致 `"0"` 匹配失败回退为全部。已改为传 `"today"`，今天统计正确生效。

## v4.1.3

- **修复新版 WebUI 图表初始化崩溃**：VChart 面积图此前使用顶层 `area/line/point` 配置导致 `init chart fail`，并因初始化失败后内部 chart 为 `undefined` 触发 `Cannot read properties of undefined (reading 'updateSpec')`。
  - 改用 VChart 官方推荐的 `series` 数组 + `curveType` 平滑曲线写法（面积图/Token 趋势图），spec 更规范、初始化稳定。
  - VChart 组件加固：`renderAsync` 的异步 init 错误被捕获，避免 unhandled rejection；init 失败后不再反复重试刷屏；spec 变化时重置失败标记允许重新初始化。
  - 统计页「近一天生图数量」与 Token 页「每日 Token 消耗趋势」面积图现可正常渲染。

## v4.1.2

- **新版 WebUI 多项修复与优化**：
  - **大图收藏按钮清晰化**：收藏按钮改为自定义金色样式（未收藏描边半透明、已收藏金色实底），在全屏深色弹窗中清晰可辨。
  - **分页器每页数量选项扩充**：新增 10、24 选项（`[10, 20, 24, 40, 60, 100]`）。
  - **图库顶部统计美化**：统计卡改为带图标（🖼️ 图片总数 / ⭐ 收藏数 / 💾 总大小 / 🗑️ 回收站）+ 渐变圆底 + 大数字的卡片式设计。
  - **生图限额展示更清晰**：区分「未单独配置（显示 全局）」与「配置为不限（显示 不限）」，不再把未配置误显为"不限"。
  - **Token 用量统计修复**：修正汇总卡片与各表格列的字段名以匹配后端（`total`/`input_other`/`input_cached`/`output`/`call_count`），此前字段名不匹配导致统计看起来全为空；默认时间范围改为「近 1 天」。
  - **图表显示健壮性增强**：VChart 在 spec 变化时重建图表（此前仅 updateSpec 可能不生效），配合 ResizeObserver 确保面积图在统计/Token 页正常显示。

## v4.1.1

- **新版 WebUI 整体视觉改为萌系粉色风格（契合插件「萌绘」）**：
  - 主题色从紫色 `#7c4dff` 全面改为萌系樱花粉 `#ff8fb3`，覆盖 Naive UI 主题、CSS 变量、滚动条、图表（面积图/折线/排行色）、图生图徽标、品牌 Logo。
  - 品牌标题由「Anima 控制台」改为「**萌绘控制台**」，Logo 改为粉色渐变圆底 + 渐变字，整体更圆润、更萌。
- **分页器增强**：新增统一 `Pager` 组件（显示总条数 + 每页数量选择 + 页码跳转输入框），图库与出图记录使用。
- **图库类型徽标修正 + 美化**：类型判断改为按 `is_img2img`/`ref_sha256` 正确区分（此前图生图成品误显示为文生图）；徽标改为分类型配色的圆角渐变标签。
- **图库缩略图放大**：网格列宽加大、图片比例改为 3:4（与 LoRA 封面一致）。
- **图表渲染健壮性**：VChart 组件加 `ResizeObserver` 与容器尺寸检查，侧栏折叠/窗口缩放/布局就绪后自动重建图表，解决图表不显示。
- **缩略图加载提速**：后端缩略图生成加内存 LRU 缓存（512 条），避免每次请求重复读盘 + Pillow 缩放 + base64，图库/出图记录缩略图显著加快。

## v4.1.0

- **新版 WebUI 多项体验修复与增强**：
  - **图生图大图并排**：大图接口默认将图片限制在 1600px 内转 data URL，图生图「参考图 + 结果图」并排能快速加载显示，不再因两张原图 base64 过大而加载慢/超时。
  - **图库缩略图加载优化**：缩略图改为小尺寸并发预取，加载更快。
  - **图库统计修复**：修正前端读取的统计字段名（`total/starred/size_mb/trash_count`），图库统计卡正确显示。
  - **图库新增用户筛选**：可按用户昵称或 QQ 号筛选图片（后端 `gallery/search` 新增 `user` 参数，`search`/`count_search` 支持按 `user_id/user_name` 模糊匹配）。
  - **图表修复**：VChart 组件修复「数据异步到达时 chart 未创建导致图表不渲染」的问题，统计/Token 页面积图正常显示。
  - **布局全宽**：去掉各页面 `max-width` 限制，内容占满可用宽度，不再右侧大面积留白。
  - **大图查看器按钮组件化**：操作按钮改用 Naive UI `n-button`，与整体 UI 一致。

## v4.0.10

- **新版 WebUI 图库与大图查看重构**：
  - **新增全屏大图查看器组件 `ImageViewer`**：半透明蒙层 + 图生图「参考图 + 结果图」并排展示（各占一半、带标签），右侧信息面板展示 SHA/类型/工作流/尺寸/大小/耗时/出图时间/用户/Seed/Denoise/使用次数/提示词（提示词可独立滚动），支持收藏/删除/恢复/彻底删除操作，深色适配。
  - **图库改为缩略图网格**：每页缩略图从 40 张减到 20 张，与出图记录一致；点击缩略图才打开大图查看器看原图（原图经 bridge 拉取，清晰）。
  - **出图记录展示图生图**：缩略图左上角标注「图生图」徽标（记录 `is_img2img` 或存在参考图时显示）；点击缩略图打开大图查看器，图生图时并排展示参考图与结果图，与旧版体验一致。

## v4.0.9

- **修复新版 WebUI 三处数据展示问题**：
  - **出图记录预览不显示图片**：缩略图缓存从普通 `Map` 改为响应式对象（`reactive`），并在加载列表时主动预取本页缩略图；拉取成功后自动触发表格重新渲染，图片正常显示。
  - **图库无内容**：GET 请求的参数序列化会 `String(undefined)` 变成 `"undefined"` 字符串，当「类型」为空时把 `type=undefined` 传给后端，导致检索条件错误、匹配不到任何图。已修复 `apiGet`：过滤掉 `undefined / null / 空串` 参数，类型为空时不再传 `type`，图库正常检索。
  - **限额编辑弹窗不展示用户限额数据**：改用 `NInputNumber` 的 `default-value`（非受控）确保每次打开弹窗都能正确展示该用户的限额初始值，并统一处理 `null / undefined / 非法值` 为 `-1`（不限）。

## v4.0.8

- **修复新版 WebUI 滚动区域不精确（底部分页器仍需滚动才可见）**：
  - **外壳改为纯 flex 布局**：不再依赖 Naive UI `n-layout` 组件，改用纯 `div` + flex 弹性布局；内容区用 `flex:1 + min-height:0 + overflow:hidden` 精确填满顶部标题栏以下全部空间，彻底移除 `calc(100vh)` 手算高度（此前因手算偏差导致内容高出视口、分页器需滚动才可见）。
  - **出图记录改为手写 tab + flex 布局**：弃用 `n-tabs`（其内部 DOM 使高度控制不可靠），改为按钮切换 + 两个独立面板，面板用 flex 占满剩余空间；表格用 `flex-height` 使列表内部滚动、底部工具栏与分页器固定可见。
  - **限额用户表格**同样改用 `flex-height`，列表内部滚动、顶部配置区固定。

## v4.0.7

- **新版 WebUI 整体布局改为「标题固定 + 内容滚动」**：内容区固定高度、不再整页滚动，每个页面头部标题固定可见；有分页器的页面（出图记录、图库）底部工具栏与分页器固定可见。
  - 出图记录：表格设 `max-height` 内部滚动，页面顶部标题与底部工具栏/分页器固定不动。
  - 图库：图片网格区独立滚动，顶部搜索栏/统计与底部翻页器固定。
  - 限额：全局配置区固定，用户表格内部滚动。
  - 配置 / 统计 / Token / 工作流 / LoRA：内容区内部滚动，头部标题固定。
  - 整体外壳（App）：侧边栏与顶部标题栏固定，内容区 `overflow:hidden`，滚动完全交由各页面内部管理。

## v4.0.6

- **修复新版 WebUI 侧边栏菜单显示渲染函数源码**：侧边栏每个菜单项图标定义错误——`iconSvg` 返回的是「返回 VNode 的函数」，而 `icon` 配置里又包了一层函数，导致 Naive UI 拿到的是函数而非 VNode，把 `()=>v("span",...)` 这种函数源码当文本显示。改为 `iconSvg` 直接返回 VNode，菜单图标正常渲染。

## v4.0.5

- **修复新版 WebUI 样式失效（侧边栏等 CSS 代码显示出来）**：部分 AstrBot 版本下，页面 `<link>` 引用的独立 CSS 文件无法被正确加载/应用，导致侧边栏等区域样式丢失、看起来像「样式代码显示出来」。改为把全部 CSS 通过 `vite-plugin-css-injected-by-js` **内联进 JS**（构建产物收敛为单个 `index.js`，样式由 JS 运行时注入 `<style>`），页面只依赖单个 JS 资源、不再有独立 CSS 文件，彻底绕开 `<link>` 加载问题。
- **出图记录列表改为表格内部滚动**：出图记录表格设固定 `max-height`，列表在表格内滚动，页面顶部标题与底部工具栏/分页器固定可见，不再整页滚动。运行日志查看器同样改为固定高度内部滚动。
- **工作流 / LoRA 封面图比例改为 3:4**：与旧版一致，封面不再被固定高度拉伸变形。
- **封面点击可看大图**：点击工作流/LoRA 封面改为在弹窗中显示大图预览（改用 `n-modal`，避免沙箱 iframe 下 `window.open(dataURL)` 被拦截导致无法查看）。

## v4.0.4

- **修复新版 WebUI 打开后空白（Naive UI provider 未就绪导致崩溃）**：v4.0.3 的单文件构建能正常加载 JS 了，但页面仍空白——根因是 `App.vue`（新版控制台根组件）在自身 `setup` 阶段调用了 `useMessage()`，而 App 是 `<n-message-provider>` 的祖先组件、provider 尚未挂载，导致「No outer <n-message-provider/>」错误，整个应用启动即崩溃。
  - 移除 `App.vue` setup 里的 `useMessage()`（App 自身不用弹消息，只负责分发全局刷新事件）；各功能 View 是 provider 的后代，其中的 `useMessage()`/`useDialog()` 正常可用。
  - 顺带清理 `GalleryView` 中一个多余的 `useMessage as _um` 导入。

## v4.0.3

- **修复新版 WebUI 在 AstrBot 里打开空白的问题**：v4.0.0 的新版控制台（`anima-console-vue`）在部分 AstrBot 版本中无法加载——构建产物为多个 JS chunk 且存在跨 chunk 的 `import`，而运行中的 AstrBot 的页面服务无法把动态 chunk 重写为带 `asset_token` 的地址，导致 `assets/*.js` 以无 token 的相对路径请求被 401 拒绝、CORS 拦截，页面空白。
  - 改为**单文件构建**：Vite 配置 `inlineDynamicImports: true` + 关闭 `manualChunks` 分包 + `cssCodeSplit: false`，且路由从懒加载（`import()`）改为静态 `import`。
  - 产物收敛为单个 `assets/index-*.js` + 单个 `assets/style-*.css`，入口 `index.html` 直接引用，AstrBot 只需重写一次入口资源即可全部加载，兼容所有 AstrBot 版本（与旧版原生 JS 页面加载方式一致）。
  - 产物体积约 2.6 MB（gzip 705 KB），对控制台可接受。

## v4.0.2

- **修复多轮改图误用「上次生成的图」当参考图的问题**：用户先发原图让 AI 改图（生成结果图），之后再说「再改一下/重新改」（不再发原图）时，AI 常把**自己上次生成的结果图**当参考图，导致改出来的图基于错误的底图。
  - **提示词引导**：增强 `comfyui_draw` 与 `comfyui_img2img` 两个工具的说明，明确「参考图 = 用户自己发的那张原图，AI 生成的结果图不是参考图」；用户说「重新改/再改一下/继续改这张图」时应回到最初用户发的那张原图，找不到就提示重发图，不要擅自用最近一次生成的图顶替；仅当用户明确引用刚生成的图做二次加工时才允许用 AI 生成图。
  - **会话原图记忆兜底**：新增会话级「最近一次图生图使用的用户原图」记忆（`g_session_i2i_ref`）。当多轮改图、参考图未进入当前事件时，兜底取图优先级改为：上次图生图原图 → 最近收到的用户图 → 用户历史图 → 最近生成图（最后兜底），避免优先误用 AI 生成的结果图。

## v4.0.1

- **修复图生图误用文生图工作流的问题**：AI 对话走 `comfyui_draw` 工具做图生图时，LLM 常把「文生图工作流名」（如"动漫"）填进 `workflow` 参数，而 `img2img_workflow` 留空，导致插件用没有 `LoadImage` 图加载节点的文生图工作流做图生图而报错「工作流没有 LoadImage 类节点」。
  - **提示词引导**：增强 `comfyui_draw` 工具的说明，明确要求图生图时工作流应填 `img2img_workflow`（而非 `workflow`），调用前先查 `comfyui_workflows` 确认哪个工作流「支持图生图」，优先选名称含「图生图」的，不确定就留空用默认图生图工作流。
  - **代码兜底**：即便 LLM 仍填错，`llm_draw` 会判断 LLM 指定的工作流是否配置了 `image_node`（具备图生图能力）；若只是文生图工作流，则自动回退到配置的「默认图生图工作流」（按风格优先级选择），不再直接硬用文生图工作流。

## v4.0.0

- **WebUI 控制台全新重写为 Vue3 + Naive UI + VChart**：
  - 新增 `pages/anima-console-vue/` 新版控制台（Vue3 + Vite + TypeScript），作为默认主入口「Anima 控制台」；旧版 `pages/anima-console/` 保留并标注「(旧版)」，可随时切换回退。
  - 8 个模块全部用 Naive UI 组件重写：配置（基于 `_conf_schema.json` 的 schema 动态渲染，含服务器/工作流/LoRA 模板列表、对象嵌套、滑块/下拉/多行等字段类型）、出图记录/运行日志、生图统计（排行 + 面积图）、工作流卡片、LoRA 卡片、图库（网格 + 详情弹窗 + 回收站 + 收藏/删除/恢复/彻底删除 + 数据库备份）、生图限额、Token 用量（汇总卡片 + 面积图 + 多张明细表）。
  - 图表用 **VChart**（`@visactor/vchart`）自封装 Vue 组件替代旧版手写 SVG 面积图，深色模式自动适配。
  - 深色模式跟随 AstrBot 桥接 context（`isDark`），顶栏可手动切换，Naive UI 主题与 VChart 主题联动。
  - 后端 `webui_api.py` 的 30 个接口零改动，新版前端原样复用 bridge 通信。
- **前端工程化**：新增 `webui-src/`（Vue3 npm 子项目）与 `build_webui.ps1` 构建脚本；`vite build` 产物输出到 `pages/anima-console-vue/`（hash 路由、相对路径资源，适配 AstrBot 静态资源重写）。
- `metadata.yaml` pages 列表调整：新版 `anima-console-vue`（Anima 控制台）排第一作默认入口，旧版 `anima-console`（Anima 控制台 (旧版)）排第二。

## v3.8.17

- **修复 Token 统计页出现两个「近 1 天」按钮**：v3.8.16 在调整默认范围为近 1 天时，HTML 里误插入了重复的「近 1 天」按钮（一个无 `data-active`、一个带 `data-active`）。现删除重复项，保留带默认激活的「近 1 天」按钮。

## v3.8.16

- **修正 Token 趋势图「今天」与「近 1 天」的范围，并调整默认范围为近 1 天**：
  - 此前两者都统一按近 24 小时展示，导致「今天」在非 0 点查看时会混入昨天的时段。现区分：
    - **今天**：从今天 0 点的整点起，覆盖今天已过去的各小时（`list_hourly(since_day_start=True)`）。
    - **近 1 天**：过去 24 小时滚动窗口（`list_hourly(hours=24)`），跨昨天。
  - 后端 `token_summary` 新增 `scope` 参数识别范围；前端 `loadToken` 传 `scope=tokenScope`。
  - **默认展示范围由「近 30 天」改为「近 1 天」**（HTML 默认激活 + 前端初始 `tokenScope` 同步调整）。

## v3.8.15

- **Token 统计页多项增强与调整**：
  - **用户排行展示用户名**：`token_store` 表新增 `user_name` 列（含旧库迁移），记录每次调用时的用户名；用户 Token 排行改为「用户 / ID」双列展示，同名用户一眼可辨，合并行可展开查看全部 ID。
  - **用户排行新增「合并插件记录」开关**：与生图统计一致，开启后把 PrivateCompanion 等插件来源的分散记录合并为一条（ID 逗号拼接、token/次数求和），关闭恢复按用户 ID 分组；WebUI 顶部范围行加入该开关。
  - **趋势图「今天 / 近 1 天」按小时（HH:mm）展示**：新增 `token_store.list_hourly`（按 updated_at 逐小时近似聚合），前端在「今天 / 近 1 天」范围用小时粒度渲染 X 轴与悬浮提示，其余范围仍按天（MM-DD）。
  - **屏蔽 Token 重置功能**：移除用户行「重置」按钮与顶部「重置全部」按钮及对应交互，Token 统计页改为只读，避免误删统计记录（后端 `token/reset` 接口保留）。

## v3.8.14

- **修复「画图对话」token 记录检测不到模型**：v3.8.13 新增的画图主对话 token 统计，当初 model 记的是空串，而 WebUI「按所用 LLM 模型」汇总会过滤 `model=''` 的行，导致画图对话那几笔记录按场景能看见、按模型却找不到。现修复：
  - 记录时从 AstrBot「当前正在使用」的对话 provider 取 model（provider id）——画图工具被调用那一刻（`on_using_llm_tool`）就缓存到会话标记里，`on_llm_response` 记录时优先用缓存值，取不到才回退运行时当前 provider。
  - 因此「画图对话」场景现在也能正确显示所用的 LLM 模型，模型汇总/模型对比不再漏掉画图主对话消耗。

## v3.8.13

- **新增「用户对话触发画图」的 token 用量统计**：此前 token 统计只覆盖插件自发起的辅助 LLM 调用（翻译/改写/参数提取），用户在 AI 对话里说「画一张小女孩」这类进入 LLM Agent 流程的主对话调用发生在 AstrBot 核心层、插件拿不到 usage，故统计不到。现新增：
  - 新增 `filter.on_llm_response` 钩子（agent 结束时广播最终 LLM 响应的 usage），配合 `on_using_llm_tool` 的「画图会话」标记，把用户通过对话触发画图的**主对话 LLM 消耗**计入 token 统计，场景标记为「画图对话」（`scene=agent_draw`）。
  - 新增 `filter.on_agent_done` 钩子清除会话标记，避免后续普通对话被误计入。
  - WebUI Token 页「按调用场景」新增「画图对话」分类标签。
  - 说明：AstrBot 的 `on_llm_response` 只在 agent 结束广播一次，故记录的是画图收尾总结那次（input 含完整上下文、消耗大头）；触发工具意图那次属中间调用，AstrBot 不回调，无法单独记录——属插件架构边界，已优于原先「完全统计不到」。

## v3.8.12

- **更名插件展示名为「ComfyUI萌绘」**：将 `metadata.yaml` 中的 `display_name` 从 `ComfyUI 绘图 (Anima)` 改为 `ComfyUI萌绘`，更简洁、有记忆点，且不再局限于二次元，契合插件多工作流/多风格的定位。仅影响 UI 展示，插件 id、数据目录、指令、日志命名均不受影响。

## v3.8.11

- **更换插件图标 `logo.png`**：将默认生成的图标替换为用户提供的自绘图标（白发动漫少女 + 画板 + 调色板 + 星星，深紫底）。

## v3.8.10

- **新增插件图标 `logo.png`**：插件根目录新增可爱少女 + 画板 + 魔法画笔的方形图标（512x512，312 KB），深紫底色突出"ComfyUI 绘图"主题。AstrBot 启动时会自动识别并显示在插件列表。`build_zip.ps1` 同步纳入打包。

## v3.8.9

- **修复中文绘图指令误报「找不到工作流」**：此前 `绘图 一个女孩`、`画 一个女孩` 等指令会把首 token（如"一个女孩"）误判为工作流名去校验，长度 ≤10 且不是已知工作流时直接报错，与文档不符。现已修复：
  - `绘图 / 绘画 / 生图 / 画图 / 作画 / 画画` 语义明确为「用默认工作流」，整句一律当提示词，**不再解析工作流名**（如 `/绘图 一个女孩` 正常出图）。
  - `画` 触发词下工作流名可选：仅当首 token 命中已知工作流才作为工作流名（`/画 真人 一个女孩`），未命中则视为提示词用默认工作流（`/画 一个女孩` 正常出图），不再误报。

## v3.8.8

- **修复 C 站下载封面图模糊**：此前直接下载 C 站 API 返回的 `images[].url`，该 URL 是**压缩缩略图**（形如 `.../width=450/xxx.jpeg`，只有几百像素宽），所以封面看着很糊。现改为把 URL 里的 `/width=NNN/` 段替换为 `/width=original/` 获取**原始分辨率原图**；若 `width=original` 请求失败（个别图源不支持），自动回退到原 URL，保证不丢图。

## v3.8.7

- **修复封面大图全屏查看两个问题**：
  - 图片未真正放大到全屏：此前 `img` 用 `width:auto;height:auto`，原始尺寸小于视口时不会拉伸。现改为 `width:100vw;height:100vh` + `object-fit:contain`，始终铺满视口且等比不变形。
  - 点击图片外空白处不关闭：此前弹窗内 `form` 填满整个 dialog，点击空白时事件目标是 `form` 而非 dialog，关闭判断失效。现同时判断点击到 form 背景即关闭。

## v3.8.6

- **Token 用量页升级可视化**：
  - 时间范围由下拉框改为按钮组，新增「今天 / 近 1 天 / 近 3 天 / 近 30 天 / 全部」，保留近 7 天 / 近 90 天。
  - 新增**每日 Token 消耗趋势面积图**（SVG 折线 + 渐变填充，数据点悬浮显示数量）。
  - 新增**按 LLM 模型汇总**（非缓存输入 / 缓存命中 / 输出 / 合计 / 调用次数 / 占比），一眼看出各模型消耗。
  - 调用场景、用户排行、模型汇总均加**进度条占比对比**；明细表保留。
  - 后端 `token_store` 新增 `list_daily`（按日期聚合趋势，有限窗口自动补全空日期、全部历史不补全）与 `list_models`（按模型聚合）。

## v3.8.5

- **新增 LLM token 用量统计**：统计插件自发起的辅助 LLM 调用（翻译 / 动漫改写 / 写实清理 / 参数提取）的 token 消耗，写入独立 `llm_token.db`，在 WebUI 新增「Token」页查看。
  - 记录字段：非缓存输入（`input_other`）、**缓存命中**（`input_cached`）、输出（`output`）、合计（`total`）、调用次数、所用模型；按 `(用户, 场景, 模型, 日期)` 聚合，跨天自动 rollover，避免表无限膨胀。
  - 新增 `token_store.py`（独立 `llm_token.db`，仿 `quota_store.py` 模式），新增配置 `llm_token_stats`（默认开启）。
  - WebUI「Token」页：汇总卡片 + 按场景分类 + 用户排行 + 明细表，支持按近 7/30/90/365 天切换，可重置单个用户或全部。仅 WebUI 管理员可见。
  - **统计边界说明**：用户在 AI 对话里触发画图那一次主对话调用发生在 AstrBot 核心层，插件拿不到 usage，**不计入**本统计。

## v3.8.4

- **LoRA / 工作流封面大图支持全屏查看**：点击封面后弹窗改为铺满整个视口，图片 `object-fit: contain` 自适应全屏完整展示（不变形），点击图片或背景关闭。此前为 92vw/88vh 的局部弹窗，图较小。

## v3.8.3

- **WebUI 新增的 LoRA / 工作流排在最前**：新增条目由追加到末尾改为插入到列表最前，保存后自动显示在最上面，方便查看最近新增的配置。

## v3.8.2

- **优化 WebUI 封面图加载，保存/重渲染不再反复请求所有图片**：此前每次新增/编辑/删除 LoRA 或工作流后，都会整体重渲染列表，且每个封面图都重新 `apiGet("lora/image")`，导致所有图片重新加载。现为封面 URL 加前端缓存（`coverCache`）：重渲染时命中缓存的封面直接复用，不再发请求；只有首次才请求后端。覆盖 LoRA 列表、工作流列表、封面选择弹窗等场景。

## v3.8.1

- **修复 LoRA 未配置权重（空字符串）导致生图报错**：当 LoRA 的 `weight` 是空字符串 `''` 时，`float('')` 抛 `ValueError: could not convert string to float`，生图直接失败。现新增 `_safe_lora_weight` 安全解析：空字符串/缺失/非法值统一回退默认权重 **1.0**（NaN 也过滤），已覆盖全局 LoRA 库补全、工作流合并等多处读取权重的地方。
- **新增 LoRA 时自动填充权重 1**：WebUI 新增 LoRA 时默认权重填 `1`（此前为空，需手动填），避免漏填导致空字符串报错。

## v3.8.0

- **新增「仅在排队时发送队列提示」配置**：`queue_hint_only_when_queued`（默认开启）。开启后，只有前面有生图任务（排队中，`ahead>0`）时才发「前面还有 N 个」提示；前面无排队时不发提示（静默等待出图）。关闭则保持原行为（无排队也发「稍等，马上来」）。需 `return_queue_position` 开启才生效。

## v3.7.10

- **真人/写实工作流被第三方插件调用时也用 LLM 清理提示词**：此前只有动漫（Anima）工作流在第三方插件调用时会走 LLM 改写；真人工作流会把夹带结构标记（`[User image request]`、`[Scene, style and final preset]`、`[section compacted]`、`Avoid ...` 等）且中英混杂的描述原样传给模型。现新增 `_rewrite_to_real_llm`：真人工作流 + 第三方插件调用（`source` 非空）时，用 LLM 去掉结构标记、统一为连贯的中文写实提示词、保留写实/摄影风格（8K、胶片颗粒、35mm、浅景深等）。原生调用不受影响。复用 `llm_rewrite_timeout` 超时保护，失败回退保留原提示词。

## v3.7.9

- **修复动漫工作流出图却有写实感**：第三方插件调用 Anima 工作流时，LLM 改写提示词的 prompt 未明确「动漫/二次元风格」约束，导致把原文的『真实摄影、手机拍照、胶片颗粒、35mm、浅景深』等写实元素原样转成 `photo / candid photography / film grain / 35mm / realistic` 等写实标签，与动漫工作流冲突、把模型往写实方向带。现已强化 LLM 改写 prompt：明确输出必须保持动漫风格，并**禁止输出写实/摄影类标签**，即使原文如此描述也要忽略或转成动漫等价表达（如 detail, clean lineart, cel shading）。原文描述的『真人/摄影』场景统一用动漫风格标签表达。

## v3.7.8

- **修复第三方插件调用 Anima 工作流时生图卡死**：v3.7.7 引入的 LLM 改写提示词在 `_do_draw` 主流程里同步 `await llm_generate`，且无超时保护。当 `translate_llm_model` 留空走默认对话模型、而默认模型未配好或 LLM 服务无响应时，`text_chat` 会挂起导致整个生图流程卡死。
  - 给 LLM 改写（`_rewrite_to_anima_llm`）与 LLM 翻译（`_translate_llm`）统一加 `asyncio.wait_for` 超时保护，默认 60s，超时自动回退保留原提示词，不再卡死。
  - 新增配置项 `llm_rewrite_timeout`（秒，默认 60），可调 LLM 等待上限。
  - 调用点本就有 try/except 兜底（LLM 失败保留原提示词），加超时后彻底避免挂起。

## v3.7.7

- **第三方插件调用 Anima 工作流时改用 LLM 改写提示词**：当其他插件（如伴侣插件，`source` 非空）调用 `comfyui_draw` / `comfyui_img2img` 且最终工作流为 Anima（`is_anima=true`）时，插件不再用 api/danbooru 翻译破坏结构，而是让 LLM 理解传入的描述并改写为纯英文 Anima 生图提示词。原生调用（`source` 为空）仍维持原「仅翻译中文片段」逻辑。LLM 用 `translate_llm_model`（留空走默认对话模型），失败时回退保留原提示词。

## v3.7.6

- **优化 WebUI 分页器**：出图记录与图库两处的分页器由「上一页/下一页」升级为完整分页控件：**首页、上一页、页码列表（当前页高亮、多页自动省略号）、下一页、末页、以及跳转到指定页**（输入页码回车或点「跳转」）。分页算法最多显示 7 个页码、当前页居中。

## v3.7.5

- **修复 WebUI 工作流编辑弹窗缺少「默认尺寸」字段**：此前编辑工作流时无法看到/设置默认宽度与默认高度。现已在编辑弹窗的「分辨率节点 / 宽度字段 / 高度字段」之后补充「默认宽度」「默认高度」两个输入框。

## v3.7.4

- **修复「限额」页不显示用户生图记录**：此前仅在 `draw_limit.enabled` 开启时才记录配额用量，默认关闭时 `quota_usage` 为空，导致限额页提示"暂无生图记录"。现改为**始终记录**每个用户的生图用量（总/小时/天），`enabled` 只控制是否真正拦截生图；限额页因此总能展示用户生图数量。
- **「限额」页新增全局限额编辑**：可直接在限额页编辑全局默认限额（总/小时/天上限、管理员豁免、限制开关）并保存，无需再到配置页查找。新增后端 `quota/save_global`。
- **说明**：限额页的用量来自独立 `quota.db`，**历史图库记录不会自动回填**——修复后需用户再次生图才会开始计入该用户用量。

## v3.7.3

- **新增「每天生图次数限制」**：可限制每个用户每天的生图次数，每天 0 点自动重置。
  - 全局：`draw_limit.max_day`（每天上限，-1 不限）。
  - 按用户：WebUI「限额」页可为单个用户单独配置每天上限（`max_day`），未单独配置回退全局。
  - 每天计数按本地时区 0 点边界自动滚动重置；超限提示「你今天的生图次数已用完，请在每天 0 点后刷新次数再试」。
  - 旧版 `quota.db` 自动升级（补 `day_used`/`day_start`/`max_day` 列），无需手动迁移。
  - WebUI「限额」页新增「当天生图数」「每天上限」两列。

## v3.7.2

- **优化生图限额超限提示文案**：总次数用尽时不再提示「联系管理员」，改为「你的生图次数已用尽，暂时无法继续生图，请稍后再试」。

## v3.7.1

- **优化生图限额超限提示文案**：不再向用户透露具体的上限次数。
  - 总次数用尽：提示「你的生图次数已用尽……请联系管理员重置」，不暴露次数值。
  - 每小时次数用完：提示「……请到 {下个整点 HH:MM} 后再试」，明确告知刷新时间。

## v3.7.0

- **新增「生图次数限制」功能**：可限制每个用户的总生图次数与每小时生图次数。
  - **全局默认**：`draw_limit` 配置块（`enabled` 开关、`max_total` 总次数上限、`max_hour` 每小时上限、`admin_exempt` 管理员是否豁免），`-1` 表示不限制。
  - **按用户覆盖**：在 WebUI「限额」页为单个用户单独配置 `max_total` / `max_hour`；未单独配置的用户自动回退使用全局默认。
  - **计数**：插件自身维护（独立 SQLite `quota.db`，与图库归档解耦），生图成功才计数、失败不占额度。每小时次数按整点小时自动滚动重置。
  - **超限提示**：到达总次数或当前小时上限时，插件会提示用户并拒绝本次生图。
  - **WebUI 新页面「限额」**：表格展示每个用户的用户名、QQ号、总生图数、当前小时生图数、总次数上限、每小时上限；可在线编辑保存单个用户限额、重置单个用户次数、或一键重置全部次数（总次数与小时次数都清零）。
  - **管理员豁免**：配置 `admin_exempt` 后，管理员（`event.is_admin`）不受次数限制。
  - 新增 `quota_store.py` 模块；`build_zip.ps1` 已包含该文件。

## v3.6.3

- **引导 LLM 优先用 Danbooru MCP 工具翻译动漫标签**：在 `comfyui_draw` / `comfyui_img2img` 两个工具的说明中，新增对动漫/二次元工作流的引导——当工具列表里有「Danbooru tag search」类 MCP 工具时，LLM 优先调用它查询/确认标准 Danbooru 标签后再填入 prompt；没有该类工具时才退化为模型自带翻译能力。这是纯提示词层面的"软引导"，无需插件内调用 MCP，也不改任何代码逻辑。

## v3.6.2

- **通用 HTTP 翻译接口支持额外固定参数（`extra_params`）**：此前翻译接口只能把原文放进 `text_field` 一个字段，无法携带接口要求的其他参数（如 DeepL 的 `target_lang`、百度的 `from/to/appid/salt`、自定义接口的 token/固定参数等）。现新增 `translate_api.extra_params`（JSON 字符串），随 POST 请求体或 GET query 一起发送；值里可用 `{text}` 占位符表示原文（例如 `{"q":"{text}","target_lang":"EN"}`），GET 模式同样生效。

## v3.6.1

- **Anima 提示词翻译改为「只翻译中文片段、保留已有英文」**：此前整段中文提示词会被翻译结果整体替换，若提示词混有英文描述会被一起丢掉。现在按逗号切分，仅对含中文的片段调用翻译，已有英文片段原样保留，再按原顺序拼接（尽量保持原格式）。单个片段翻译失败时该片段保留原文，不影响其余片段。
- **WebUI 新增「翻译调试」面板**：在控制台「配置」页底部，可任选 danbooru / llm / api 三种模式，输入一段中文点「测试翻译」，实时返回是否连通、耗时与翻译结果/错误信息。新增后端路由 `/translate/test`，仅做单次调用不改动任何配置。后端 `translate_test(mode, text)` 供调试与脚本复用。

## v3.6.0

- **Anima 工作流提示词翻译支持三种模式（danbooru / llm / api）**：此前中文动漫提示词只能发到 Danbooru 标签服务器翻译。现新增：
  - **LLM 翻译**（`translator_mode=llm`）：用 LLM 把中文描述改写为英文 Danbooru 风格标签。可独立配置 `translate_llm_model`，留空则走 AstrBot 当前默认对话模型。
  - **通用 HTTP 翻译接口**（`translator_mode=api`）：接入任意支持 HTTP 的翻译服务（DeepL、百度翻译、自定义中转等），通过 `translate_api` 配置接口地址、方法、请求头、字段映射（`text_field` / `result_field` / `json_body`）等。
  - **模式选择**：全局 `translator_mode` 三选一；每个 `is_anima` 工作流可用自己的 `translator_mode` 覆盖全局。
  - 新文件 `translate_client.py` 提供通用 HTTP 翻译客户端；新增本地测试 `test_translate_api`。

## v3.5.29

- **修复"伴侣插件文生图被误判成图生图、还从图库扒旧图当参考"**：图生图补图兜底原先用 `is_img2img`（含弱信号）判定，导致调用方（如伴侣）文生图请求只是顺带传了 `img2img_workflow` 就会被错误转为图生图，并从 gallery 历史捞旧图当参考图。现收紧为仅 `strong_img2img`（显式传了 image 或消息里真有图）才进入补图兜底；弱信号直接回退对应风格文生图，不再误中断调用方。

## v3.5.28

- **修复保存配置报错「缺少模板选择 …: 需要 __template_key」**：AstrBot 配置校验要求 template_list（workflows/loras/服务器）每个元素带 `__template_key`，此前自定义弹窗保存的数据缺该字段导致校验失败。前端工作流/LoRA 弹窗保存时自动补 `__template_key`，后端 `save_config` 兜底补齐任意来源的缺失项。

## v3.5.27

- **修复"画真人走动漫工作流"**：未指定工作流时，现在会先按提示词语义自动判断「真人/写实」还是「动漫/二次元」（真人/照片/写实/摄影 vs anime/动漫/二次元/卡通等关键词），命中则选用对应类型的默认工作流（`default_workflow_real` / `default_workflow`，图生图同理用 `default_img2img_workflow_real` / `default_img2img_workflow`）；语义不明才回退到全局 `default_style_priority`。不再出现"说真人却用动漫工作流"。

## v3.5.26

- **完善 comfyui-draw 技能（SKILL.md）**：补齐用户痛点相关规则——铁律 0「必须真调工具禁止只说不动」、默认只画一张、图生图只认本次消息参考图（历史/群聊图不算）、LoRA 查询指引（自己用 comfyui_loras 查，别让用户给确切名字）、群聊/被动场景说明。`comfyui_draw` 工具说明增加一句"详细细则见 comfyui-draw 技能"，引导 LLM 读取技能后再操作。

## v3.5.25

- **修复 comfyui_loras 工具对 LLM 不可用**：工具 docstring 用了「参数：」而非 AstrBot 要求的标准 `Args:` 格式，导致该工具注册时参数 schema 为空、模型看不到/不会调用它（表现为"让它用 LoRA 却要用户给确切名字"）。已改为标准 `Args:` + 类型注释，LLM 现在能正确发现并调用 LoRA 查询工具。

## v3.5.24

- **修复 AI 画图"只说不动"**：`comfyui_draw` 工具说明大幅精简（从 90+ 行压到 ~30 行），核心新增两条强指令：
  - 用户要图时必须**立即调用工具**，禁止只回复"好/马上/快了"而不真正画（不调用工具=没画）。
  - **图生图只认本次消息附带的参考图**，历史/群聊旧图不算——修复"群聊里总是被误判成图生图卡住"。
  - 弱化"必须先查 comfyui_workflows/loras"的强制链：改为"用户给了名字直接用，不确定才查"，修复"让它用 LoRA 却要求用户给确切名字"。

## v3.5.23

- **工作流编辑弹窗补全节点配置**：新增正/负提示词节点、分辨率节点与宽高字段、参考图节点、输出节点、LoRA 主模锚点、工作流 JSON 粘贴等字段。
- **LoRA 编辑弹窗补全预设**：新增提示词预设（`[预设名|提示词]`）与「仅模型」开关（model_only）。

## v3.5.22

- **画图默认只发一张**：修复 AI 对话被模型记忆影响而连续发多张的问题。
  - `comfyui_draw` docstring 新增「数量规则」：默认只生成 1 张，除非用户明确要求多张，禁止连续重复调用。
  - 代码级单张保护：同一会话 4 秒内重复调用画图工具（`llm_draw`/`llm_img2img`）视为模型死循环，直接拦截并让模型收尾（第三方插件带 source 的主动调用不受影响）。

## v3.5.21

- **封面选图功能**：C 站抓取后不再自动定封面，而是下载最多 6 张候选图，弹出缩略图网格让用户**点选一张作为封面**（LoRA 与工作流封面均支持）；单张候选时自动采用。解决了 C 站 images 数组顺序与页面封面不一致导致抓错图的问题。

## v3.5.20

- **封面优先选 C 站主图**：C 站作者主封面的图片文件名以 `00001-` 开头（如 `00001-89982050.jpeg`）。选图时优先匹配该命名的条目（页面那张主图），否则回退第一张；候选图 URL/尺寸仍写日志便于比对。

## v3.5.19

- **封面抓取诊断**：抓取时记录候选图 URL 与尺寸到插件日志；响应新增 `cover_url` 字段，抓取成功 toast 显示"封面原址 URL"，便于比对 C 站页面那张封面到底是不是选中的那张（帮助定位图片选择偏差）。

## v3.5.18

- **封面落盘校验与路径透出**：封面图写入后校验文件是否存在且非空，失败时不再返回假成功（之前 `write_bytes` 异常被吞导致"文件夹没有图但配置显示有"）；写入成功/失败均写插件日志；响应新增 `save_dir` 字段，前端 toast 显示封面实际保存目录，便于排查路径。

## v3.5.17

- **封面选择与 C 站页面一致**：优先取版本内「第一张图」（即 C 站页面显示的封面/主图，作者排序第一），不再按竖图偏好选（可能选偏成示例图）。第一张下载失败时自动逐个 fallback 到后续图片。

## v3.5.16

- **C 站封面抓取加固**：多个候选图逐个尝试下载（单张 403/非图片不再导致整体失败）；封面图下载加 `Referer: https://civitai.com/` 头（CDN 可能要求），下载失败写日志。
- **新增 `civitai_api_key` 配置**：可选填 C 站 API Token，抓取请求带 `Authorization: Bearer`，规避 C 站对无 token 请求的限流/图片列表缺失（明明有预览图却拿不到的一种常见原因）。

## v3.5.15

- **删除确认弹窗居中**：`confirmDialog` 加 `margin:auto`，弹窗在屏幕水平垂直居中（此前默认左上角）。

## v3.5.14

- **修复 C 站封面抓到视频/无法打开**：封面选择只取 `type=image` 条目（排除视频）；下载后按 **magic bytes** 校验真实图片格式（JPEG/PNG/WebP/GIF），非图片内容直接丢弃，不再把视频存成 .jpg。扩展名按真实格式落盘。
- **抓取封面失败提示明确**：无有效图片预览时（该版本只有视频/无图），前端提示"未获取到有效封面"，不再静默。

## v3.5.13

- **C 站抓取封面/版本选择优化**：
  - 无 `modelVersionId` 时，不再盲信 API 数组顺序，改为按 `publishedAt` / `createdAt` / `updatedAt` 时间戳取**最新版本**（LoRA 与工作流封面都生效）。
  - 封面图选择：优先选 `images` 中**竖图**（宽≤高，符合 C 站封面习惯），否则取第一张。

## v3.5.12

- **工作流封面**：工作流新增封面图能力——卡片顶部显示 3:4 封面（点击看大图、无图显示占位）；「抓封面」按钮通过 C 站链接自动抓取封面图到本地（复用 lora_assets/ 目录）；「传封面」按钮上传本地图片；编辑弹窗新增 C 站链接与封面图字段。

## v3.5.11

- **工作流 / LoRA 卡片新增删除**：卡片操作区新增「删除」按钮（红色），点击弹二次确认后删除。
- **白名单改 textarea**：`allow_draw_users` 配置项改为多行文本框（每行一个 ID，兼容逗号），与提示一致。
- **工作流复制**：工作流卡片新增「复制」按钮，复制一份新工作流（名称置空）并打开编辑弹窗，填写名称并保存后创建；不填名称无法保存。

## v3.5.10

- **LoRA 抓取并入标题到别名**：C 站抓取后把模型标题加入别名（若不存在），方便 LLM 区分。
- **别名改 textarea**：编辑弹窗中别名字段改为多行文本框（每行一个别名），更易分辨；兼容旧逗号分隔数据。
- **列表别名只展示第一个**：LoRA 卡片别名只显示第一个（多余显示 +N 角标），详情弹窗展示全部别名。

## v3.5.9

- **修复 LoRA 封面大图关不掉**：关闭按钮原来定位在弹窗顶部外（-40px），可能被裁剪/不可点。改为悬浮在图片右上角内部（z-index 提升），并支持点击图片、点击遮罩关闭（ESC 由原生支持）。

## v3.5.8

- **LoRA 封面改进**：卡片封面改为 3:4 比例，点击封面弹出大图查看。
- **C 站链接新标签页打开**：链接点击在新标签页打开（`target="_blank" + rel="noopener noreferrer"`）。
- **LoRA 卡片精简**：列表不再直接展示触发词/描述，新增「详情」按钮弹窗查看（含名称/别名/底模/触发词/描述/C 站链接）。
- **C 站抓取版本识别修复**：支持链接中的 `?modelVersionId=` 查询参数（及 `/model-versions/数字` 路径），多版本 LoRA 会按链接指定版本精确匹配，不再默认取第一个版本导致底模抓错（如 ill / anima 版本混用）。

## v3.5.7

- **修复插件安装失败（KeyError: 'type'）**：上一版把配置分区元数据写进了 `_conf_schema.json`，AstrBot 加载插件时会把它当配置 schema 递归解析、报 `'type'` 错误导致安装失败。现已把分区映射移到前端硬编码，`_conf_schema.json` 保持纯配置 schema（所有节点均有 `type`）。

## v3.5.6

- **新增插件独立代理配置 `http_proxy`**：插件访问外部网络（C 站抓取等）可单独配置代理地址（如 `http://127.0.0.1:7890`），不再依赖 AstrBot 全局代理；优先使用本插件配置，其次 AstrBot 全局 `http_proxy`，最后环境变量兜底。
- **WebUI 配置界面分区 + 可折叠**：配置项按「服务器与模型 / 默认工作流 / AI 对话与 LLM / 出图行为 / 网络与代理 / 权限与图库」分组成卡片，可点击折叠/展开，页面不再一长条，便于区分与快速定位。

## v3.5.5

- **C 站抓取错误信息明确化**：C 站 API 请求失败不再显示空错误「抓取失败: 」，而是分类明确提示（10s 超时 / 连接失败 / 网络错误），并在插件日志记录详细信息；后端请求超时降到 10s，失败反馈更快。

## v3.5.4

- **工作流弹窗的 Anima 改为开关样式**：Anima 工作流从复选框改为开关（toggle），带说明文字，更直观。

## v3.5.3

- **C 站抓取超时优化**：前端抓取请求超时从 6s 放宽到 60s（原 6s 不足以等 C 站响应）；后端封面图下载独立 15s 超时，避免总耗时超限。若仍报「路由不存在(404)」，说明插件 Python 代码未重载——请重启/重载 AstrBot 插件（`lora/fetch` 路由需 v3.5.0+ 并重载后注册）。

## v3.5.2

- **日志搜索修复**：搜索改为后端全量搜索并重新分页（不再只搜当前页）；支持按 QQ 号（user_id）、用户、消息、提示词模糊匹配。
- **LoRA / 工作流弹窗编辑**：添加/编辑改为弹窗形式（不再跳转配置页），弹窗内可编辑名称、底模、别名、触发词、描述、C 站链接、封面图（LoRA）；名称、底模、别名、服务器、文件、Anima、默认 LoRA（工作流）。
- **抓取按钮提示**：未配置 C 站链接时点击「抓取」会提示先填链接，不再无响应。
- **添加按钮美化**：LoRA / 工作流新增按钮改为主题色样式。

## v3.5.1

- **WebUI 新增「工作流」卡片视图**：与 LoRA 视图类似，卡片展示工作流名称、别名、底模、服务器、Anima 标记、文件与可用 LoRA 列表（按底模匹配），支持跳转配置页编辑。
- **日志「出图记录」表格优化**：表格高度加高（560→640px），分页器从表格滚动容器内移出，固定在表格下方，翻页更顺手。

## v3.5.0

- **LoRA 增加底模（base_model）分类**：LoRA 库与工作流均可选 anima / z-image-turbo / krea2 / illustrious（留空=通用）。工作流与 LoRA 底模匹配校验，`/loralist` 展示底模与不匹配标注。
- **LoRA 配置扩展**：新增触发词（多行）、描述、C 站链接、封面图字段；`keywords` 语义改为「别名」，与工作流别名统一；`loras_text` 支持 `名称|权重|启用|底模` 四字段（兼容旧格式）。
- **C 站抓取**：WebUI 填 C 站链接点「抓取」按钮，自动下载封面图到本地 `lora_assets/` 并填入触发词/描述/底模；代理自动使用 AstrBot 的 http_proxy。
- **comfyui_loras LLM 工具**：AI 对话可查询 LoRA 库（按底模过滤），携带别名/描述/触发词，绘图工具 docstring 要求先查再引用。
- **WebUI LoRA 卡片视图**：新增「LoRA」页，卡片式展示封面图（无图默认占位图）、别名、底模、触发词、描述，支持编辑/上传封面/抓取。
- 新增接口：`POST /lora/fetch`、`POST /lora/upload_image`、`GET /lora/image`。

## v3.4.6

- **统计排行健壮性修复与优化**：同 user_id 改名后排行显示确定的名字（`MAX(user_name)`）；排行输出补 `last_ts` 字段（合并行与普通行一致）；新增 `created_at` 索引，加速近 24 小时趋势查询（图库量大时更明显）。

## v3.4.5

- **WebUI 统计「查看更多」弹窗优化**：弹窗列表中每个 QQ 号现在同时显示对应的生图数量；弹窗改为在整个浏览器窗口居中显示。

## v3.4.4

- **WebUI 统计「合并插件记录」的 QQ 列优化**：合并后 QQ 号很多时，最多只展示前 3 个，并提供「查看更多」按钮，点击弹出弹窗查看该用户的完整 QQ 号列表。
- **近一天生图数量图数字配色调整**：图上数量数字改用青色（不再与紫色面积图同色），视觉效果更清晰。

## v3.4.3

- **WebUI 生图统计新增「合并插件记录」开关**：开启后把 user_name 为 PrivateCompanion 等插件来源的分散记录合并为一条（user_id 逗号拼接、数量求和、重新排名）；关闭恢复默认按用户 ID 分组统计。
- **近一天生图数量趋势图优化**：改为 24 小时滚动窗口（从昨天当前整点到今天当前整点）；X 轴只显示「小时:00」（不再带日期）；每个数据点带悬浮提示（小时 - 数量 张），非 0 时段直接在图上显示数量。

## v3.4.2

- **WebUI 生图统计排行新增「QQ」列**：排行榜展示用户（user_name）与 QQ（user_id）两列，便于区分同一名称下的不同用户 ID，也能看清其他插件（如 PrivateCompanion）发图记录对应的真实 ID。

## v3.4.1

- **新增发图白名单 `allow_draw_users`**：可配置允许绘图/发图的用户 ID 列表（逗号或换行分隔）。留空则所有用户都能发图（默认）；非空时，仅名单内用户可触发指令绘图 / AI 对话发图，其余用户会收到无权提示且不会真正生图。

## v3.4.0

- **WebUI 新增「统计」页**：按用户统计生图数量排行（支持今天 / 近 3 天 / 近 7 天 / 全部 四个范围），以及近一天的生图数量面积图（按小时分桶）。新增后端接口 `GET /stats/ranking`（用户生图排行）与 `GET /stats/trend`（小时趋势），每个面板带「刷新」按钮。

## v3.3.9

- **AI 对话尊重用户取消/拒绝画图**：完善 `comfyui_draw` 与 `comfyui_img2img` 两个 LLM 工具的触发提示，新增「用户明确表示不要发图/取消/停止/不需要画图时，绝不调用画图工具、仅用文字回应」的规则，避免用户明确拒绝后仍被误触发调用画图工具（从而在服务器离线时反复报「连接不上绘图服务器」）。

## v3.3.8

- **出图完成的文件信息小报告改为配置开关**：新增 `show_draw_report`（bool，默认 false）。关闭时，出图后不再输出包含尺寸/大小/耗时/时间的小报告；开启后恢复输出。

## v3.3.7

- **调整 `convert_webp_to_png` 配置项位置**：从默认工作流配置中间移到「出图行为」分组（`return_queue_position` 旁），避免与工作流配置混排。

## v3.3.6

- **webp 转 png 发送副本改为配置开关**：新增 `convert_webp_to_png`（bool，默认 false）。开启时，出图发送前用 Pillow 把 webp 转成 png 临时副本再发送（归档仍保留原 webp）；默认关闭，直接用原图发送。

## v3.3.5

- **回退 v3.3.2 / v3.3.3 的「AI 对话画图 LLM 收尾 + photo_tool_sent 抑制尾随」方案**（实测效果不理想）。代码回退到 v3.3.1 行为：`comfyui_draw`/`comfyui_img2img` 发图时仍做 **webp→png 发送副本**，保留原有队列提示、小报告（`_DRAW_DONE_HINTS`）与 `event.send` 发图逻辑；移除 `_llm_generate_closing`、`notify_done` 参数、`_private_companion_photo_tool_sent` 标志设置。若 AI 对话画图仍出现文本重复，根因在伴侣插件 6.0.10 的 `suppress_empty_photo_tool_followup` 只覆盖自家 photo 工具，需从伴侣插件侧解决。

## v3.3.1

- **修复生成的 webp 图片推送失败、只剩 `<pc_history_media images="1" />` 占位符**：ComfyUI 输出常为 webp，而部分适配器（onebot/QQ 等）在 Agent 工具场景下对 webp 内联推送失败，AstrBot 会把该图片转成历史媒体占位、导致图片没发出来（图库仍正常归档）。现改为：`_do_draw` 出图后，若文件为 webp 且环境有 Pillow，发送前先用 Pillow 转一个 **png 临时副本**用于 `event.send` / 伴侣发图；**归档仍保留原 webp**（内容寻址不变），图生图兜底缓存也用原 webp。

## v3.3.0

- **修复 WebUI 底部出现空白悬浮小块**：底部提示 toast 在未显示时仅靠 `translateY(160%)` 移出屏幕，空容器仍会露出一小块。现改为默认 `opacity:0; visibility:hidden`，`.show` 时显示，不显示时彻底不可见。

## v3.2.10

- **图生图大图并排布局调整**：参考图（源图）移到**左侧**，结果图移到**右侧**；「参考图 / 结果图」标注文字移到**图片下方**（此前在图片上方）。

## v3.2.9

- **图库大图信息面板：仅提示词区域独立滚动，其余信息固定**：信息面板改为 flex 纵向布局且不再整体滚动（`overflow:hidden`），其余信息项（SHA/类型/工作流/尺寸/大小/耗时/出图时间等）固定显示；「提示词」单独一项占满剩余高度、在该区域内独立滚动（`.info-prompt`）。小屏（≤680px）下仍允许信息面板整体滚动以便查看。
- **信息栏加宽**：右侧信息面板宽度由 340px → 380px。

## v3.2.8

- **图库大图改为全屏 + 半透明蒙层展示**：大图弹窗由「小弹窗」改为占满屏幕（100vw×100vh），背景加深半透明蒙层（带轻微模糊）。右侧信息面板固定宽度（340px，`flex:0 0 340px`），图生图并排展示时不再被图片区挤压，始终可见。
- **修复图库「图生图」类型筛选筛不出来**：图生图成品图的 `source` 仍是 `gen`（`is_img2img=1`），而后端 `search` 用 `source=?` 过滤，前端下拉传的 `type=img2img` 匹配不到任何记录。已改为 `type=img2img` 时按 `is_img2img=1` 过滤，其余 `gen/ref/user` 仍按 `source` 过滤。
- **图库类型筛选下拉框美化**：改为自定义下拉外观（去原生箭头、加渐变三角、更舒适的内边距与聚焦高亮），与整体风格一致。

## v3.2.7

- **修正工作流别名 `aliases` 字段类型**：v3.2.6 将 `aliases` 设为 `type: "string"` + `multiline`，但 WebUI 渲染 textarea 实际是依赖 `type: "text"`（与 LoRA 的「提示词预设」一致），导致该字段在界面上仍是单行、无法换行输入。已改为 `type: "text"`，现可在 WebUI 与原生后台正确显示为多行 textarea，支持逗号或换行填写多个别名。

## v3.2.6

- **「工作流别名」改为每个工作流条目内的字段**：在工作流列表每个条目的「工作流名称」表单项下方新增「工作流别名」（`aliases`，textarea 多行）——把外部调用方（如伴侣插件）传入的、会命中本工作流的其它名字填进来，多个用逗号或换行隔开，如 `anime_selfie_workflow`、`ComfyUI default`。`_resolve_workflow` 匹配前会先遍历各工作流的别名（大小写不敏感、忽略首尾空白），命中即映射到该工作流的真实名称；未命中仍按原逻辑回退默认工作流。WebUI 渲染为多行 textarea，支持换行输入。（替代此前 v3.2.5 的全局 `workflow_aliases` 配置项，已移除该全局项。）

## v3.2.4

- **修复画图开始提示与队列提示重复**：v3.2.0 引入了「提交前即时反馈（稍等，马上来）」+「提交后队列位置提示（前面排着 N 个）」，两条会同时出现、显得重复。现**去掉提交前即时反馈**，统一在提交返回后只发一条 `_queue_hint`：无队列（ahead<=0）→「稍等，马上来」；有队列（ahead>0）→「前面排着 N 个」。伴侣 proactive（`notify_pending=False`）不发。

## v3.2.3

- **修复「找不到工作流」报错：绘图入口容错回退默认工作流**：`_do_draw`（绘图真正入口，可能收到伴侣/LLM 传入的无效工作流名，如 `ComfyUI default`）改为 `fallback_on_missing=True`——当传入的工作流名在当前列表里找不到时，不再报错中断（不再提示「绘图配置有误：找不到名为…」），而是容错回退到按「风格优先级 + 文生图/图生图」配置的默认工作流：
  - 未传图（文生图）→ 按 `default_style_priority`（默认 anime）找对应**文生图**默认工作流（动漫文生图 / 真人文生图）
  - 传了图（图生图）→ 按优先级找对应**图生图**默认工作流（动漫图生图 / 真人图生图）
  - 默认工作流也未配置 → 回退第一个工作流
- 同时保留校验能力：`/draw --wf`、`/workflows set` 等显式指定工作流的指令仍走 `fallback_on_missing=False`，用户指定不存在的工作流名仍会提示可用列表（校验行为不变）。新增 `_pick_default_workflow_name()` 统一「按风格优先级 + 文生图/图生图选默认工作流」逻辑，供 `_resolve_workflow` 复用。

## v3.2.2

- **修复伴侣插件文生图被误判为图生图而中断**：v3.1.8 把「指定了 `img2img_workflow`」也当作图生图意图，但伴侣插件主动生图（proactive）即使是文生图也会顺带带上默认的 `img2img_workflow` 参数。结果伴侣文生图被误判为「图生图但无参考图」，在 `img2img_fallback=prompt` 下直接返回「图生图需要一张参考图」提示，伴侣以为失败就回退到在线图片 API。现区分「强 / 弱」图生图信号：
  - **强信号**：显式传了 `image` 参数（无论解析成败）或本次消息/引用里真有图 —— 确为图生图，无参考图时走 `img2img_fallback`（prompt 提示 or txt2img 回退）。
  - **弱信号**：只指定了 `img2img_workflow`、但既没传 `image`、消息里也没图 —— 常见于伴侣文生图顺带带上默认工作流，此时直接回退对应风格文生图（真人图生图→真人文生图，动漫图生图→动漫文生图），不再中断，避免误伤伴侣文生图。

## v3.2.1

- **修复 AI 对话（comfyui_draw / comfyui_img2img）画图后模型复述文件信息**：工具成功生图后返回给模型的文本原为「图片已发送给用户（含文件详情）…」，其中「含文件详情」措辞会诱导模型把图片的文件名/尺寸/大小/耗时/格式等元数据复述给用户。已改为不含任何文件信息提示的文本：明确指示「不要描述图片的文件名、尺寸、大小、耗时、格式或任何技术细节」，只做简短自然收尾。

## v3.2.0

- **画图指令/AI对话提交后立刻返回「正在处理」即时反馈**：此前排队提示要在 `POST /prompt` 返回后才发送；当后端是中转站（其 `/prompt` 可能同步阻塞到前面任务推进才返回）或直连 ComfyUI 排队时，用户会一直等到上一幅图画完才收到反馈。现改为：`_do_draw` 在提交 `queue_prompt` **之前**先 `_send` 一条「正在处理/稍等」即时提示（受 `return_queue_position` 控制），确保立刻得到响应；提交返回后再按 `X-Queue-Position`/本地队列补发精确的「前面还有 N 位」提示（仅 `ahead>0` 时补发，避免无排队时与即时提示重复）。伴侣插件 proactive（机器人主动生图）通过 `notify_pending=False` 跳过该即时提示，避免打扰。

## v3.1.10

- **修复 WebUI 底部 toast 提示被屏幕遮住一部分**：`bottom:32px` 抬高到 `56px`、`z-index` 提到 `99999`，并加 `max-width`/自动换行，避免长文本溢出或贴边被遮挡。
- **修复大图弹窗图片被截断、展示不全**：此前 `.image-dialog-imgwrap` 用 `flex:1 1 0`（basis 0）配合 `align-items:stretch`，会被强制拉伸/压缩，加上 `overflow:hidden` 导致长图/高图被截断。现改为图片按 `max-height` 完整自适应显示（`flex:0 1 auto` + `align-items:flex-start`），弹窗体 `overflow:auto` 兜底，图生图并排两张图也各自 `object-fit:contain` 完整展示。

## v3.1.9

- **排队位置优先读取中转站响应头 `X-Queue-Position`，直连 ComfyUI 时回退本地队列**：`ComfyUIClient.queue_prompt` 现解析中转站成功响应头 `X-Queue-Position`（语义＝「入队那一刻前方还有几个任务，含正在运行的」）并随返回体带回；`_do_draw` 提交后优先用它作为排队提示与动态超时估算的 `ahead`，日志区分「来自中转站响应头 / 回退本地队列」。由于后端地址不一定是中转站（可能是直连 ComfyUI），未带该响应头时自动回退到原有的本地队列统计，两端逻辑互不影响。

## v3.1.8

- **修复「只指定 img2img_workflow 却没传参考图」被静默当作文生图**：`comfyui_draw` 的图生图意图判定此前只看 `image` 参数；当 LLM/调用方只传了 `img2img_workflow`（如用户说「再来一次图生图」但没带图）而没传 `image`、消息里也无图时，会被误判为 `is_img2img=False`，导致 `img2img_workflow` 被忽略、直接跑默认文生图工作流。现改为：指定了 `img2img_workflow` 同样视为「图生图意图」，取不到参考图时进入 `img2img_fallback` 处理（默认 `prompt` 提示重发图；设 `txt2img` 则按风格回退对应文生图），不再静默乱画。

## v3.1.7

- **大图弹窗：图生图的「结果图 + 参考图」改为横向并排，同一屏可见**：此前两张图受宽度限制会换行变成上下排列，用户要求能在一屏里同时看到两张图。现改为强制横向并排（`image-dialog-imgs` 设 `flex-wrap:nowrap`，图生图时两张图各占 50%、各自 `object-fit:contain` 保持比例），右侧信息面板保持不变。

## v3.1.6

- **新增「图生图取不到参考图时的处理方式」配置项 `img2img_fallback`**：可选 `prompt`（默认，提示用户重发图，不降级）/ `txt2img`（回退为文生图）。修复根因：当调用方/伴侣插件传入的 `image` 路径在本机不可达时，`got_explicit_image` 为 False 且事件里也无图，导致 `is_img2img` 被误判为 False、静默当作文生图去跑默认工作流瞎画。现改为只要调用方显式传了 `image` 参数即视为「图生图意图」；取不到参考图时按配置决定：`prompt` 直接提示、`txt2img` 按原风格回退（真人图生图→真人文生图，动漫图生图→动漫文生图），绝不混用默认文生图工作流。
- **WebUI 出图记录新增「工作流」列**：表格列头增加「工作流」，展示每条记录使用的工作流名（取 `recent_records` 返回的 `workflow` 字段，超长省略显示）。
- **WebUI 大图弹窗美化与标签修正**：① 清理重复/冲突的 `.image-dialog` 样式定义，统一弹窗布局（结果图 + 参考图并排，参考图更窄），修复 form 布局被 `align-items:center` 覆盖导致的错位；② 文生图不再顶部显示「结果」标签，只有确实绑定参考图的图生图才并排展示「结果图 / 参考图」两个标签；③ 参考图缺失（旧图生图记录未绑定 ref_sha256）时保持干净的单图展示，不再留一块空白。
- **WebUI 配置渲染支持 options 下拉**：`renderField` 对带 `options` 字符串数组的 string 配置渲染为下拉框，覆盖 `default_style_priority`（anime/real）与新配置 `img2img_fallback`（prompt/txt2img）。

## v3.1.5

- **WebUI 出图记录：图生图可查看并关联源参考图**：① 出图记录「预览」列，图生图记录增加「图生图」标记，并在结果缩略图旁附加一张「参考图」小缩略图（点击并排查看结果 + 参考图）；② 大图弹窗改造为可并排展示「结果」与「参考图（图生图源图）」两张图并各自标注，图生图记录点击任一张图均进入并排视图；依赖图库 `images.ref_sha256` 字段（后端 `gallery/image?meta=1` 已返回）。
- **WebUI 配置项：默认工作流改为下拉选择**：`default_workflow` / `default_workflow_real` / `default_img2img_workflow` / `default_img2img_workflow_real` 四个默认工作流配置项，在 WebUI 配置页渲染为「从已配置工作流名」动态下拉，避免手敲工作流名出错；`default_style_priority`（anime/real）本就为下拉。

## v3.1.4

- **修复 `default_style_priority` 配置项在 WebUI 显示 `[object Object]`**：`options` 误用 `{value,label}` 对象数组，AstrBot 前端只支持字符串数组，已改为 `["anime", "real"]`。

## v3.1.3

- **新增「风格优先级」配置项 `default_style_priority`，决定默认工作流动漫/真人谁优先**：可选 `anime`（动漫优先，默认）/ `real`（真人优先）。此前默认工作流顺序被写死为动漫优先，现改为由该配置控制——`real` 时未指定工作流会优先取真人默认、空了再回退动漫；并补一条日志打印最终选定的默认工作流名与优先级。4 个具体工作流名配置（`default_workflow` / `default_workflow_real` / `default_img2img_workflow` / `default_img2img_workflow_real`）保持不变。

## v3.1.2

- **新增两个真人默认工作流配置项，形成 2×2 默认矩阵**：`default_workflow`（默认动漫文生图）、`default_workflow_real`（默认真人文生图）、`default_img2img_workflow`（默认动漫图生图）、`default_img2img_workflow_real`（默认真人图生图）。`_resolve_workflow` 在未指定工作流时按「动漫优先、真人兜底」选择（文生图：动漫文生图→真人文生图→第一个；图生图：动漫图生图→真人图生图→动漫文生图→第一个）。
- **`/workflows` 指令可设置全部 4 个默认**：`set`（动漫文生图）、`set_real`（真人文生图）、`set_img2img`（动漫图生图）、`set_img2img_real`（真人图生图）；列表与 `comfyui_workflows` 工具回复均展示 4 个默认工作流当前值。

## v3.1.1

- **补充关键决策日志（便于定位"跑错工作流/过滤没生效"）**：① `_do_draw` 解析工作流后打印 `请求名 / is_img2img / 实际选用工作流名 / server`，一眼看清是否回退到默认工作流；② `llm_draw` 工作流决策处打印 `is_img2img / 指定 img2img_workflow / 指定 workflow / 最终选用工作流`；③ `llm_draw` 过滤分支打印 `过滤开关 / 来源 / 是否伴侣插件`，确认过滤是否真的生效。
- **版本号升到 v3.1.1**：前次 v3.1.0 因打包撞版检测（dist 已存在同版本 zip 会拒绝覆盖）导致后几次代码改动未进包；本轮升级小版本号以强制重新打包，确保日志增强等改动真正落地。

## v3.1.0

- **提示词过滤改为配置总开关（默认关闭，不过滤）**：保留 `filter_companion_prompt` 配置项（默认 `false`）。关闭时——无论原生调用（`/draw`、AI 对话、Agent）还是伴侣插件调用——都**完全不做任何提示词改写，原始提示词原样透传** ComfyUI（连通用拆分/清洗都不做）。开启时，伴侣插件（`source` 命中标记）走完整过滤（通用拆分清洗 + 专属过滤：`_format_companion_prompt` 抽取用户诉求与构图连续性，过滤时间/日程/位置/情绪等无关事实与 Avoid/Do not 负面约束）；原生调用同样归属此过滤功能，由本开关统一控制。
- **图生图取不到参考图不再降级为默认文生图工作流**：`llm_draw` 中原本"判定为图生图但参考图没取到就静默降级成文生图、用默认工作流瞎画一张无关图"的行为已移除。现在一旦判定为图生图却取不到参考图（用户发图/引用解析失败、LLM image 参数下载失败等），直接返回提示「请先发送一张参考图……」交给调用方回传用户，不再偷偷走默认工作流。`/img2img` 指令与 `comfyui_img2img` 工具本就如此，行为已统一。
- **增强取图诊断日志（解决"图生图老跑到默认工作流"难定位）**：① `image` 参数解析失败日志现在明确指出"该路径在本机不存在（调用方/伴侣插件传来的可能是另一容器或已清理的 temp 路径），若本应走图生图请传入当前服务器真实可用的图片路径或 URL"；② 图生图无图/终止日志现在打印用户期望的工作流名（如 `期望工作流=真人图生图`），并明确"不会降级为文生图/默认工作流"；③ 文生图模式日志改为"无图生图意图"，与图生图意图区分，避免误导。

## v3.0.19

- **`filter_companion_prompt` 改为提示词处理总开关**：之前该开关只控制「专属过滤」，而通用拆分/清洗（清方括号标题、删 Negative 段、切 Avoid 软信号）始终生效，导致关闭后日志仍出现「过滤」字样且提示词仍被改写。现改为：关闭时**完全不做任何提示词改写，原始提示词原样透传** ComfyUI；开启时才做通用拆分 + 专属过滤。对应 `_conf_schema.json` 的 hint 同步更新。
- **拆 prompt 调试日志降级为 DEBUG**：`_split_external_prompt` 内的 `[拆prompt][DBG]` 日志原先无条件用 `logger.info` 打印（含「过滤后正向提示词」字样），会污染 INFO 日志。改为 `logger.debug`，仅写入 webui.log（DEBUG 级），控制台不再出现。

## v3.0.18

- **图库单张收藏/取消收藏补回执**：之前 `/图库 收藏 10`（单张）收藏成功后没有任何回复，用户误以为"指令没生效"。改为单张收藏成功回复「已收藏 ★（10）」、取消收藏回复「已取消收藏（10）。」；多张仍走「已收藏 N 张，跳过 M 张 ★」汇总。

## v3.0.17

- **图库每页显示数量做成配置项**：`gallery.page_size`（整数，默认 5，有效范围夹紧到 1~50）控制列表/收藏列表每页条目数；原硬编码的 `page_size = 5` 改为读取该配置。`_conf_schema.json` 新增对应配置项（图库面板可调）。

## v3.0.16

- **图库列表去掉「工作流:」「时间:」「标签:」字样**：展示只保留值本身，用 `|` 分隔，不再显示冗余字段名。普通/收藏列表形如 `序号 描述 | default | 08-06 03:13 | 👤 用户名`；搜索列表额外以 ` | #标签1 #标签2` 接在描述后。

## v3.0.15

- **图库列表格式改回单行 + `|` 分隔**：上一版把工作流/时间/标签拆成「中文标题 + 换行」两行，用户反馈其实只想要主行后面用 `|` 串起来、不要额外加「工作流:」标题和换行。故三处列表（普通列表、收藏列表、搜索结果）统一改回 `序号 描述 | 工作流: xxx | 时间: xxx`（搜索额外 ` | 标签: ...`，管理员视图 ` | 👤 用户名`）的单行 `|` 分隔样式。同时补上搜索循环里漏定义的 `_uid`/`_uname` 变量，避免管理员视图渲染时 NameError。

## v3.0.14

- **图库 render 模式改回 AstrBot 官方渲染为主路径**：之前自写的 Pillow 渲染被反馈「字粗糙、不如别人用 AstrBot 渲染的漂亮」。复盘确认此前「字模糊」的真正根因是 `Image(url=)` 报错导致 AstrBot 渲染从未成功（直接回退文字），并非模板质量问题。故将 `_send_display` 的 render 分支优先级反转：**优先用 AstrBot 自带 `text_to_image`（官方 HTML 模板，美观清晰）**，仅当该服务不可用/返回空/发送异常时，才用内置 Pillow 渲染做兜底，再失败才回退文字（带 ⚠ 提示）。`display_mode` 配置项仍为下拉框 `text`/`render`，无需改动。

## v3.0.13

- **图库管理员/全部视图不再展示 `sid:` 技术字段**：列表、收藏列表、搜索结果的管理员视图（及全部列表）原先显示 `| sid:xxxx` 这类内部会话标识，对管理员无意义且不友好。改为显示用户图标 + 名称：`| 👤 用户名`（无名称时回退 user_id，再无则「匿名」）。管理员仍能直观区分是谁的图，且不再暴露内部 sid。

## v3.0.12

- **图库列表字段区分 + 修复 Pillow 渲染换行错乱**：
  - 列表/收藏列表/搜索结果的展示格式改进：工作流、时间、标签都加上中文标签并分行显示（如 `工作流: default   时间: 08-06 03:13`、`标签: #合照 #猫`），不再把「工作流 | 时间」裸挤在一行，字段一目了然。
  - 修复 Pillow 渲染图片的换行错乱：`_render_gallery_text_pillow` 原按 `font.getlength(ch)` 折行，但 emoji（❤️）等字符 getlength 返回 0/异常值，导致整行折行计算错乱。新增 `_ch_w` 估算：emoji/异常字符用保守近似宽度（约一个字宽），空白约 0.3 字宽；并对超长无空格串（URL/sha 等）强制硬断，避免溢出画布。

## v3.0.11

- **图库 render 模式改用内置 Pillow 高清渲染（解决字模糊）**：之前 `/图库` 的 render 展示依赖 AstrBot 的 `text_to_image`（HTML 模板渲染），其 `render_t2i` 不接受字号/宽度/缩放参数，模板默认字小且生成图易被压缩，导致渲染出的字发虚模糊。
  - 新增 `_render_gallery_text_pillow`：用 Pillow 把展示文字绘制成图片，**2x 超采样抗锯齿**（大画布大字绘制后缩回目标尺寸）+ 22px 字号 + 1.6 行距 + 白底深灰字；按字符宽度自动折行兼容中英文混排；自动探测系统中文字体（Windows 雅黑/黑体、Linux 文泉驿/Noto CJK、macOS 苹方），输出 PNG 到 `data_dir/gallery_render/`。
  - `_send_display` 的 render 分支改为优先用该内置渲染；Pillow 不可用/失败 → 回退 AstrBot `text_to_image` → 再失败 → 回退文字（带 ⚠ 提示）。

## v3.0.10

- **图库提示文案去掉 sha 相关说明**：列表/搜索/收藏列表/统计等展示里的「发图用：/图库 取图 <序号>」提示，以及「取图/收藏/取消收藏/打标签/公开/私有」的用法提示，统一移除「也支持 sha 前几位」「<编号或sha前几位>」等面向用户的 sha 说明，改为只提示用序号（上方「N.」左侧的数字）。（注：用 sha 前几位定位图片的能力本身仍保留，仅提示文案不再提及，避免对普通用户造成困惑。）

## v3.0.9

- **修复：图库 render 模式发送图片报 `Image.__init__() missing 1 required positional argument: 'file'`**：`_send_display` 误用 `Image(url=url)` 构造图片组件，而 AstrBot 的 `Image.__init__` 第一个必填参数是 `file`（不是 `url`）。`text_to_image` 返回的是**本地文件路径**，故改用 `Image.fromFileSystem(path)`（http(s) 才走 `Image.fromURL`）。修复后 render 模式可正常把列表/搜索/统计渲染成图片发送。

## v3.0.8

- **修复：伴侣专属过滤把中文描图整体丢弃**：`_format_companion_prompt` 的白名单策略只保留带英文标题（如 `user request`、`[Composition and continuity]`）的段，而用户的**中文描图**常落在非白名单段（裸中文段落、`[Scene, style and final preset]` 等），会被整体过滤掉，表现为「中文提示词被过滤」。
  - 新增「中文保护」兜底：白名单段之外、所有**含中文字符且非方括号标题行**的内容行也一并保留（中文是用户出图意图核心，绝不丢；纯英文事实段仍按白名单丢弃）。已保留的 chunk 内容做去重，避免重复。
  - 注意：Anima 工作流（is_anima=true）下，含中文的 prompt 仍会走 Danbooru 翻译成英文标签（这是二次元模型出图刚需）。翻译成功时整段中文被替换为英文标签属预期行为；若希望中文也保留，可在配置 `danbooru.append_original=true` 改为「中文 + 英文标签」并存。翻译失败时自动回退保留原始中文。

## v3.0.7

- **图库 `/gallery` 支持多张批量操作**：「取图 / 收藏 / 取消收藏」现支持一次传多个目标，序号或 sha 前缀用逗号或空格隔开（如 `/图库 取图 1,2,3`、`/图库 收藏 1 2 5`）；LLM 工具 `comfyui_gallery` 的 send 模式也支持 `keyword="1,2,3"` 一次发多张。新增 `_parse_gallery_targets` 统一按 `, ，` 与空白切分；多张时汇总「已发 N / 失败 M」「已收藏 N / 跳过 M」。
- **修复：收藏列表跨会话不显示（「收藏两张只显示一张」）**：收藏列表此前带 `session` 过滤，`cross_session=false` 时只显示当前会话收藏的图，其他会话收藏的被隐藏。改为用户级（跨会话）可见。同时把 `ImageStore.star` 的更新从 `WHERE sha256 LIKE 前缀%` 改为**完整 sha256 精确匹配**，消除短前缀误中多张导致收藏计数异常的隐患。
- **列表已收藏标记更醒目**：普通列表 / 收藏列表 / 搜索结果中，已收藏的图改用红色爱心 `❤️` 标记（未收藏不占位），替代原来的灰色 `★`。
- **图库展示方式改为下拉框 + 渲染失败可见提示**：`_conf_schema.json` 的 `gallery.display_mode` 加 `options: ["text","render"]`（面板渲染为下拉框）；`render` 模式下若渲染服务返回空或抛异常，回退文字时附 `⚠` 提示，便于定位 AstrBot「文本转图片」是否启用且已选激活模板。
- **伴侣插件专属过滤修复（v3.0.6 已合入本版基线）**：`_format_companion_prompt` 改为白名单段保留，放行 `additional outfit preference` / `additional visual recognition notes` / `visual continuity reference` 等承载出图标签的节，配置框里的词不再被丢弃。

## v3.0.6

- **修复伴侣插件专属过滤 `_format_companion_prompt` 丢弃穿搭/外观配置词**：此前过滤逻辑只保留 `user request:` 首行与 `[Composition and continuity]` 区块，把 `additional outfit preference:`（伴侣插件的 `daily_outfit_photo_prompt` 就落在这里）、`additional visual recognition notes:`（狐娘等角色人设）、`visual continuity reference:` 等承载出图标签的节整体丢弃，导致用户在伴侣插件配置框填的「teenager, 18-19 years old, cute」等词完全不生效。
  - 改为「白名单段保留」策略：用统一正则按标题把正向段切成 `(标题, 内容)` 块，只保留白名单内的节（`user request` / `additional visual recognition notes` / `additional outfit preference` / `visual continuity reference` / `composition and continuity`），丢弃纯事实/元指令节；首个白名单块之前的零散首行仍当作 user request 补回。负向段处理不变（保留标签、去掉 `Do not ...` 元指令与占位符）。
  - 注意：即使放行，真人/写实工作流画二次元兽耳娘（狐娘等）仍勉强，建议在 `daily_outfit_photo_prompt` 等配置里补性别锚（如 `1girl` / `female`）以稳定出女性角色。

## v3.0.5

- **调试日志改为完整展示原始提示词与过滤结果**：`[拆prompt][DBG]` 现在会把 `_split_external_prompt` 收到的**原始输入**和**过滤后的正向提示词**完整打印（超长内容按 400 字符分段，避免单行被截断），便于人工直接对比"过滤前/过滤后"到底差在哪。仅用于排查提示词切分问题，确认无误后可移除。

## v3.0.4

- **最终兜底清理正向内的负面词表**：在 `llm_draw` 中，无论走通用拆分还是「陪伴插件专属过滤」，最后都会对正向提示词再执行一次 `_strip_inline_negative`——把 `Avoid` / `Do not` / `Respect ... exclusions` 软信号之后的残留负面（含尾部大段逗号负向词表）强制清除，杜绝"专属过滤覆盖回原文导致负面残留"的漏网。
- **新增调试日志** `[拆prompt][DBG]`：在 `_split_external_prompt` 中打印输入长度、是否含 `Negative prompt:` 标记、是否含 Avoid 软信号、走了哪个分支及返回结果。用于排查"提示词未被切分"类问题（确认该函数是否真的被调用、收到什么内容）。
- 说明：若上一条 v3.0.3 修复后仍复现，本版本通过"最终兜底 + 调试日志"双保险定位并兜底。

## v3.0.3

- **修复：专属过滤不再依赖 `source` 字段**。此前「陪伴插件提示词专属过滤」(`filter_companion_prompt`) 要求 `source` 命中「我会永远陪着你」才生效，但伴侣插件很难把 source 透传过来，导致开启开关也没用。现改为**仅由开关控制**——只要开启就对 `comfyui_draw` 传入的提示词做过滤。
- **增强通用兜底 `_split_external_prompt`**：无论是否含 `Negative prompt:` 标记，都会再次用 `Avoid` / `Do not` / `Respect ... exclusions` 软信号切分，把残留在正向段内的负面词表（如尾部大段逗号负向词表）清理掉，正向只保留构图描述与约束。

## v3.0.2

- **外部提示词统一「只取正向」**：`_split_external_prompt` 增强为对**所有来源**生效（不带 source 也处理）——正向与构图约束全部保留，负面直接删除（不输出到负向节点，回退调用方自带的 negative_prompt）。新增 `_clean_prompt_markers` 统一清理方括号分节标题（`[User image request]`、`[Scene, style and final preset]`）、`[section compacted]` 占位符与控制字符，**保留中文描图**；无 `Negative prompt:` 标记时，用 `Avoid` / `Do not` / `Respect ... exclusions` 软信号切分，软信号之后视为负面直接删除，无标记的自然语言描述原样返回。
- **新增配置开关「陪伴插件提示词专属过滤」**（`filter_companion_prompt`，默认关闭）：开启后，当「我会永远陪着你」等伴侣插件带 source 标记调用时，额外启用 `_format_companion_prompt` 抽取用户诉求与构图连续性段落、过滤与出图无关的事实/元指令；关闭时（默认）仅做上述通用处理，不误伤常规 /draw 与 AI 对话调用。

## v3.0.1

- **图生图优先选「名字带图生图」的工作流**：`comfyui_draw` / `comfyui_img2img` 的工具描述（docstring）新增"优先选择名称含「图生图」字样的工作流"的规则，LLM 选图生图工作流时优先匹配专为图生图设计的工作流。
- **图生图失败提示收敛**：找不到图加载节点（LoadImage）时，不再把"工作流没有 LoadImage 节点 / 请在 image_node 手动填键名"等内部配置细节发给用户，改为日志记录详细原因、给用户回复简洁的"没找到对应的画图流程"。
- **技能与文档同步**：`skills/comfyui-draw/SKILL.md` 新增"铁律 2.5 图生图优先选名字带图生图的工作流"，README 的图生图说明补充"图生图工作流最好命名为带「图生图」字样"的建议。

## v3.0.0

- **图库展示支持图片渲染**：新增 `gallery.display_mode` 配置（`text`=纯文字 / `render`=图片渲染），控制 `/图库` 的列表 / 搜索 / 找标签 / 收藏列表 / 统计等展示内容如何发送。
  - `render` 模式：调用 AstrBot 的渲染服务（`text_to_image`，底层 Pillow 渲染）把展示内容渲染成图片发送；**渲染失败或服务不可用时自动回退为纯文字**，保证功能不中断。
  - `text` 模式（默认）：维持原纯文字展示，行为不变。
  - 底层新增 `_send_display` 方法统一处理「渲染→发送→失败回退」链路。
- 升级为大版本 v3.0.0：引入渲染展示能力，配置项向后兼容（默认 `text`，不改变既有行为）。

## v2.2.91

- **列表不再展示 sha 前缀**：`/图库` 的列表 / 搜索 / 找标签 / 收藏列表，以及 `comfyui_gallery` 的 list / recall / search 列表，均去掉 `[sha前16位]` 展示，只保留图库编号（序号），列表更清爽。
  - **sha 功能仍保留**：`/图库 取图 <sha前几位>` 等仍支持用 sha 定位，只是列表里不再显示 sha，操作引导以序号为主（提示中仍注明"支持 sha 前几位"）。

## v2.2.90

- **新增收藏列表**：`/图库 收藏列表 [页码]`（或 `/图库 我的收藏`）可单独查看自己收藏的图（★），分页与 `/图库 列表` 一致；管理员可用 `/图库 全部 收藏列表` 看全库收藏。help/usage 文案同步补充。

## v2.2.89

- **彻底隐藏普通用户侧的删除相关文案**：除已注释的删除/回收站/清空/恢复指令外，`/图库 统计` 与 `comfyui_gallery` 的 stats 统计也不再展示「回收站占用」字样，避免向普通用户暴露删除相关概念。管理员 WebUI 不受影响。

## v2.2.88

- **暂时关闭普通用户的图库删除功能**：`/图库` 聊天指令中的「删除 / 回收站 / 恢复 / 清空」已注释屏蔽，普通用户在聊天里无法删除/恢复/查看回收站图片（help、usage 文案同步隐藏）。**管理员使用的 WebUI 删除功能不受影响，保留可删**。
- 说明：此为用户侧策略，属临时关闭，代码以注释保留便于后续重新开启。

## v2.2.87

- **图库操作增加归属权限校验**：图片的修改类操作（打标签 / 删除 / 彻底删除 / 收藏 / 取消收藏 / 恢复 / 公开 / 私有）现在**只有图片所有者（user_id 归属者）和管理员能执行**，其他人无法操作。
  - 此前仅「发图」「改可见性(s公钥分支)」做了归属校验，`tag` 的 sha 分支、`del`、`purge`、`star`、`unstar`、`restore` 完全无校验——尤其 `purge` 可**彻底删除他人图片**，属严重越权漏洞，已修复。
  - 新增 `_can_operate_image`（归属校验：图主 / 管理员 / 无主图仅管理员）与 `_resolve_op_target`（解析编号或 sha 并校验）两个 helper，统一应用于 `/图库` 指令与 `comfyui_gallery` 工具（tag/public/private）。
  - 权限语义：图片**公开**只代表「他人可查看/发送」，不代表他人可修改；无主图（user_id 为空）仅管理员可操作。`/图库 取图` 仍保持「公开图任何人可发、私有图仅本人」的查看语义。

## v2.2.86

- **修复图库列表序号与取图定位不一致**：此前 `/图库 列表` 用全局编号，但 `/图库 搜索`、`/图库 找标签`、`comfyui_gallery` 召回/检索列表用「结果集局部序号 1-N」，而取图（`/图库 取图` / `comfyui_gallery` send）用 `get_by_global_no`（按全局排序定位），导致「列表里看到第 3 张，取图却取到全局第 3 条」，序号对不上。
  - 修复：图库编号统一为「基础过滤（owner/user_id）下、按 created_at DESC + sha256 排序」的**全局唯一编号**（新增 `ImageStore._gidx_rank`）。编号不依赖搜索关键词、标签或会话范围，因此从「列表/搜索/找标签」任何一个入口看到的编号，`get_by_global_no(编号)` 都能无状态定位到同一张图。
  - 所有图库列表（列表/搜索/找标签/comfyui_gallery 的 list/recall/search）统一展示该全局唯一编号 + sha16。
- **列表补充展示 sha16**：`/图库 列表` 之前不展示 sha 但提示「可用 sha 取图」，现在每行展示 `[sha前16位]`，提示与实际可操作项一致；`/图库 取图 <编号或sha前几位>` 两种方式都真正可用。
- 权限语义不变：编号只基于 owner（user_id）隔离，同用户不同会话的图编号口径统一、本人均可取用；`session_id` 仍作为元数据记录。

## v2.2.85

- **修复 comfyui_gallery 取图发送失败**：`_gallery_send_image` 原来直接 `event.send(Image(file=...))` 传裸 `Image` 组件，在 AstrBot 新版（v4.27.x）的 LLM 工具调用场景（`comfyui_gallery` 的 recall/search/send）会报 `'Image' object has no attribute 'chain'`，导致"找到图但发送失败"。已改为 `event.send(MessageChain([Image(file=...)]))`，与 `_send`/`comfyui_draw` 的既有正确用法一致。

## v2.2.84

- **图库列表描述改用用户消息**：发给用户的图库列表（`/图库 列表` / `/图库 找标签` / `comfyui_gallery` 召回与检索）中，图片无标签时，描述由「提示词前 N 字」改为「用户发送的消息（trigger_msg）前 N 字」——因为提示词可能被 Danbooru 翻译成英文标签，对用户不直观，而用户当时的原始消息更易辨认。无触发消息时回退取提示词。新增辅助方法 `_gallery_desc` 统一处理。
- **WebUI 图片详情拆分展示用户与会话**：图片大图详情里，原来把用户名和用户 ID 合并显示为「用户: 小明 · 123456」，现拆分为独立的「用户名」「用户ID」字段，并新增展示「会话ID」（session_id），便于管理员审计图片所属人与所属会话两个维度。

## v2.2.83

- **图库支持备份数据库下载**：控制台「图库」页新增「备份数据库」按钮，点击后把图库数据库（`gallery.db`）下载到本地（文件名 `gallery_backup_<时间戳>.db`），便于备份/迁移。数据经桥接走 base64 构造 Blob 下载，规避 AstrBot 裸路径需登录 token 的问题。后端新增 `GET /gallery/backup` 接口。

## v2.2.82

- **让 `session_id` 过滤真正生效（跨会话配置落地）**：此前 `search`/`count_search`/`get_by_global_no` 收到 `session` 参数后直接忽略（仅兼容签名不参与 SQL），导致 `cross_session=false`（默认）时"仅检索当前会话"的语义没有落地——实际查了所有会话、仅靠 `user_id` 隔离。本次修复：
  - `search`/`count_search`/`get_by_global_no` 在 `session` 非空时新增 `AND session_id=?` 过滤，`cross_session=false` 时 `/图库` 与 `comfyui_gallery` 列表/搜索/计数仅命中当前会话内的图。
  - `get_by_global_no` 新增 `session` 参数，且 `main.py` 中所有按编号取图（取图/打标签/公开/私有/send）调用都传入与列表一致的 `session`，避免"列表带会话过滤、按编号取图却不带"导致的编号错位。
  - 为 `images` 表补 `idx_images_session` 索引，`session_id` 已纳入建表语句（旧库仍由 ALTER 兼容补列）。
- **权限语义保持不变**：`session_id` 仅作为检索范围缩小，不替代权限判断——`owner`（`user_id` 归属）与 `is_public` 过滤始终保留。只有当事人（图库归属的那个 `user_id`）能取到/发送自己的私有图，公开图除外；`session_id` 仍作为来源元数据被记录，供管理员审计展示所属会话。

## v2.2.81

- **严重修复：出图无法归档入库（v2.2.69~v2.2.80 受影响）**：`_do_draw` 主方法中**未定义 `user_id`/`user_name` 变量**，但成品归档（`archive_image`）和参考图归档引用了它们，导致每次归档时抛 `NameError` 被吞掉 → 出图不写库、WebUI 出图日志与图库均看不到、`/图库 列表` 提示"没出过任何图"。
  - 修复：在 `_do_draw` 开头定义 `user_id`/`user_name`（用 `get_sender_id()`/`get_sender_name()`），使成品图、参考图归档正常写入数据库并带上用户标识。
  - 受影响版本用户升级到本版后，后续出图将正常入库。

## v2.2.80

- **修复图库隐私漏洞（老图无主可见）**：此前 owner 过滤含 `user_id IS NULL OR ''` 的「无主图放行」，导致升级前无 user_id 的老图对**所有普通用户**可见（谁都能搜到/看到）。已改为：普通用户只能看到「公开图 + 自己的私有图」，无主老图不对普通用户暴露，**仅管理员（/图库 全部 全库模式）可见**。
- **图片序号改为数据库全局编号**：列表/召回展示的序号不再是「每页局部编号」，而是基于全局排序（created_at DESC）的**稳定全局编号**（`gidx`）。用户直接输入列表里看到的编号即可操作（`/图库 取图 8`、`/图库 打标签 5 合照`、`/图库 公开 3`），编号与 sha 前几位两种方式都支持。
  - 新增 `ImageStore.get_by_global_no(no, owner)` 按全局编号取记录；`search`/`recall_by_tag` 返回结果附带 `gidx`。
  - 指令与 LLM 工具（send/tag/public/private/list/recall）的数字输入全部改用全局编号解析。

## v2.2.79

- **图库命令提示统一用中文 `/图库`**：所有面向用户的提示文本（帮助、用法、翻页、发图、收藏/删除/恢复/清空/公开/私有等）从 `/gallery` 改为 `/图库`，并同步改为中文子命令（如 `/图库 取图`、`/图库 打标签`、`/图库 删除`）。`/gallery` 英文入口仍兼容，但提示优先展示中文。

## v2.2.78

- **图库「全部」全库模式（管理员专用）**：`/图库 全部 列表 [页码]`、`/图库 全部 搜索 <关键词>` 可查看**所有用户**的图片（不受 owner 隔离限制），列表带 `sid` 和 `user_name`，便于管理员审计。普通用户无权使用，会提示"只有管理员可以查看全库图片"。管理员平时仍默认只看自己的图，仅在显式用「全部」命令时才看全库。

## v2.2.77

- **图库列表精简展示**：`/图库 列表` 每条只显示「序号 + 标签（无标签取提示词前10字）+ 工作流 + 出图时间」，不再展示 sha 全称/source/尺寸/use_count/完整 prompt。管理员（`event.is_admin()`）额外显示该图所属会话 `sid`（审计信息）。owner 隔离对管理员同样生效——管理员也只看自己的图，不会全量看所有用户。
- **新增 session_id 字段**：`images` 表加 `session_id` 列，归档（成品/参考图/收藏图）时写入，用于管理员审计展示所属会话。

## v2.2.76

- **修复 /图库 列表分页 TypeError**：`/图库 列表` 调用 `count_search(session=...)`，但 `count_search` 无 `session` 参数导致崩溃。已为 `count_search` 增加 `session` 参数（签名与 `search` 对齐；images 表无 session_id 列，暂不按会话过滤，仅防调用报错）。

## v2.2.75

- **新增图库帮助命令**：`/图库 帮助` 或 `/图库 help` 显示完整的图库指令中文说明（列表/搜索/打标签/找标签/取图/收藏/公开私有/保存/删除回收站/统计等），并附示例。

## v2.2.74

- **图库列表分页**：`/图库 列表` 改为每页 5 条、按页码翻页（`/图库 列表 2` = 第 2 页），并展示「总数量 + 总页数」。列表头部显示"图库（第 X/Y 页，共 N 张）"，底部提示翻页命令。

## v2.2.73

- **图库指令支持中文**：
  - `/gallery` 增加中文别名入口 `/图库`（`filter.command(alias=...)`）。
  - 子命令全部支持中文说法：列表/list、搜索/search、打标签/tag、找标签/findByTag、取图/send、收藏/star、取消收藏/unstar、删除/del、回收站/trash、恢复/restore、清空/purge、保存/save、统计/stats、公开/public、私有/private。
  - 例如：`/图库 列表 5`、`/图库 取图 1`、`/图库 打标签 合照`、`/图库 公开 1`。
  - 未知子命令提示改为中文用法说明。

## v2.2.72

- **修复「引用图片打标签识别不准」**：此前指代消解（/gallery tag 不带序号时）用 `path_of(sha) == 路径字符串全等` 反查，但引用图返回的是 temp 路径、归档图是 gallery/refs 路径，两者目录不同永远不相等，导致反查失败、提示"没找到这张图"。
  - 改为**内容寻址 sha256** 反查：对引用图算 sha256（与归档算法一致），`get_by_sha` 定位图库记录，准确识别引用图对应的图。
  - 若引用图尚未入库（未生成/未收藏过）→ 明确提示"这张图还没入库，请先 /gallery save 或指定序号"。
  - `comfyui_gallery` 工具的 `tag`/`public`/`private` 模式缺省反查同步改为内容寻址。
  - 新增 `ImageStore.sha_of(path)` 公开方法。

## v2.2.71

- **图库新增「公开 / 私有」可见性**：图片默认**私有**（仅本人可检索/发送），可设为公开后其他用户也能检索到并索取。
  - `images` 表新增 `is_public` 列（默认 0=私有，自动迁移）；`search`/`count_search`/`recall_by_tag`/发图归属校验均改为「公开图所有人可见 + 本人私有图/历史无主图」。
  - 指令：`/gallery public <序号/sha>`、`/gallery private <序号/sha>` 设置可见性。
  - LLM 工具 `comfyui_gallery` 新增 `public`/`private`/`tag` 三种 mode，可让 AI 直接设公开/私有、打标签。
  - 打标签（`tag`）也可通过 LLM 工具完成；指令 `/gallery tag` 早已支持。
  - 发图归属校验同步收紧：他人私有图无法通过 sha 前缀发送，公开图可发。

## v2.2.70

- **出图等待超时改为动态累加**：原等待出图的超时是固定值 `draw_timeout`，排队任务越多、排得越靠后的任务越容易在等待时被误判超时失败。
  - 现改为 `timeout = min(max_draw_timeout, draw_timeout + ahead * queue_extra_timeout)`，按前面排队任务数逐任务累加等待时间。
  - 新增配置项：`queue_extra_timeout`（每排队一个任务额外累加秒数，默认取 draw_timeout）、`max_draw_timeout`（动态超时封顶，防无限放大，默认基础超时的 31 倍）。

## v2.2.69

- **图库用户隔离（防串图，重要）**：此前图库的 `search`/`send`/`recall` 等**不按用户过滤**，群聊里任何用户都可能检索/发送到他人保存的图（隐私风险）。本次修复：
  - `images` 表本就含 `user_id` 列，但 `search`/`count_search` 从未用它过滤——现新增 `owner` 参数，按当前用户 `user_id` 过滤（兼容历史无主图）。
  - 指令 `/gallery list/search/send/tag/findByTag/trash` 与 LLM 工具 `comfyui_gallery` 的 `recall/search/send/list` 全部按当前用户过滤。
  - 发图 `_gallery_send_image` 增加归属校验：传 sha 直发（含 sha 前缀）前先确认该图属于当前用户，否则拒绝，堵住"知道 sha 前缀就能取他人图"的漏洞。
  - **修复归档 user_id 恒为空**：原代码用不存在的 `event.user_id` 取用户ID导致归档的图 user_id 全为空（无主图对所有人可见）。已改为 `get_sender_id()`，并给成品图、参考图、收藏图（`archive_user_image`）都正确写入 `user_id`。
  - 修复 `resolve_ref` 对 list 值调 `os.path.exists` 的 TypeError。
  - `recall_by_tag` 增加 owner 过滤并补 `AND deleted=0`。
- **兼容性说明**：升级前已存在的旧图 `user_id` 为空（无主图），本版仍允许所有人检索/发送这些历史图；新产生的图会正确带上用户标识、严格隔离。如需把历史图也收紧，可手动 `UPDATE images SET user_id='<QQ>' WHERE user_id=''` 后生效。

## v2.2.68

- **队列/开始提示改为朋友式平级口吻**：去掉"收到/请稍候/请等待"等请示、卑微感表达，改为像朋友间自然告知（如"在弄了，稍等一下""前面还有 {n} 个，轮到就给你"），让 AI 更有人样、不低三下气。

## v2.2.67

- **队列/开始提示去掉画图导向词**：`_QUEUE_HINTS_GENERATING` 与 `_QUEUE_HINTS_QUEUED` 中原有"正在生成/出图中/任务已提交/入队"等词，对"帮我拍张照"等非画图语境不贴切。已改为通用表达（如"正在处理，请稍候""前面还有 {n} 个在排队"），兼容"画图"与"拍照"等不同触发语境。

## v2.2.66

- **出图相关文案末尾统一加轻量表情**：队列提示（正在生成 / 排队等待）与出图完成小报告末尾补充表情符号（如 ⏳ ✨ ✅ 🖼️ 👍），保持中性风格的同时更有温度、更自然。

## v2.2.65

- **出图排队/开始提示改为中性文案**：`_QUEUE_HINTS_GENERATING`（提交后正在生成）与 `_QUEUE_HINTS_QUEUED`（排队等待）原为萌系"画图"导向（"小画家动笔""给你画上"），不适用于"拍个照给我"等语境。已改为中性表述（如"已收到，正在处理中，请稍候""已排队，前面还有 {n} 个任务"），不预设具体动作、去掉过度颜文字，兼容不同触发语境。

## v2.2.64

- **LLM 工具出图后不再让大模型补话**：`comfyui_draw` / `comfyui_img2img` 出图后 return 给模型的文案，从「请根据你的人设自然回复用户」改为「请用一句话简短收尾即可，不要重复文件信息」，避免大模型在插件已发图 + 固定小报告后仍补述文件信息、措辞不稳定。
- **修复图库占用统计不准确**：
  - 新增 `ImageStore._compute_sizes`：区分「有效占用」与「回收站占用」。回收站（软删除 `deleted=1`）的图文件仍占磁盘，但不应计入有效占用。
  - `/gallery stats` 与 `comfyui_gallery stats` 的 `size_mb` 改为有效占用，并新增 `trash_size_mb`（回收站占用）展示，消除「删进回收站后占用不变」的困惑。
  - `enforce_lru` 改为**优先清理回收站图**（用户已删除，应最先腾空间），再淘汰有效图中非收藏、无标签的旧图。

## v2.2.63

- **出图完成话术改为中性折中文案**：原 `_DRAW_DONE_HINTS` 偏"画图"导向（"画好了""绘制完成"），不适用于"帮我拍个照给我"这类请求语境。已改为 6 条**不预设具体动作**的中性文案（如"好了，这张 {wh}、{size}…"），只客观报尺寸/大小/耗时/时间，兼容"画图"与"拍照"等不同触发语境，仍随机取一、不请求大模型。

## v2.2.62

- **修复「引用图取不到」的根本原因**：`_extract_images` 判断组件类型时用 `str(comp.type)`，而 `comp.type` 是 **str 子类枚举**（如 `ComponentType.Reply`），`str()` 返回 `"ComponentType.Reply"`（带前缀）而非 `"Reply"`，导致 `ct == "Reply"` 永远不匹配，**引用(Reply)分支从未进入**，引用图自然取不到。
  - 修复：改用 `comp.type.value / .name` 判断，并兼容 `"ComponentType.Reply"` 形式，使 Reply / CardImage / Image 三种组件都能被正确识别。
  - 修复后：引用图会走引用消息内嵌图 + `_extract_quoted_images`（`get_msg` 远程拉取）解析；两者都失败且消息确有 Reply 时，回退到「本会话用户最近发的图」。

## v2.2.61

- **进一步修复「引用图取不到」**：
  - `_extract_images` 遍历到引用(Reply)组件时，现在会**显式把该 Reply 组件传给引用消息 API 回退**（`_extract_quoted_images(event, reply_component=...)`），避免 AstrBot 内部二次查找 Reply 失败，提高 `get_msg` 远程拉取被引用消息图片的成功率。
  - `_extract_quoted_images` 增加可选 `reply_component` 参数并透传给 AstrBot 解析器。
  - `_extract_images` 末尾新增兜底：当消息里确实存在引用(Reply)组件但取不到图时，从「本会话用户最近发的图」（`g_last_received` / `g_recent_user_images`）兜底。仅当出现 Reply 才启用，纯文生图不受影响。

## v2.2.60

- **修复指令图生图"引用/最近发的图取不到"**：
  - 根因：纯指令（`/draw` `/img2img` `/画`）被 command/regex handler 拦截后**不进 LLM Agent 流程**，而此前缓存「用户最近发的图」用的是 `on_agent_begin` / `on_using_llm_tool`（仅 Agent 流程触发），导致指令场景下这两个缓存一直是空的，引用/最近发的图兜底失效。
  - 新增 `@filter.event_message_type(EventMessageType.ALL, priority=20)` 钩子 `_capture_user_images_on_message`，在**每条消息（含纯指令）执行前**用高优先级提前缓存消息内/引用图片到 `g_last_received` 与 `g_recent_user_images`。
  - 抽出 `_collect_user_images` / `_record_user_images` 复用取图与缓存逻辑（覆盖消息内图、引用内嵌图、引用 API 回退）。
  - 修正 `/img2img` 兜底优先级：**用户最近发的图**（`g_last_received` / `g_recent_user_images`）优先于**本插件最近生成的图**（`g_last_generated`），并取最近一张。
  - 说明：`/draw` `/画` 为纯文生图入口，**不引入**历史图兜底，避免把纯文生图误判成图生图；它们带图/引用时仍会走 `_extract_images` 自动切图生图。

## v2.2.59

- **新增「跨插件生图对接指南」文档**：明确其他插件若走工具生图应统一接入本插件（一套工作流/LoRA/图库/语言规范），并给出对接方法。
  - 说明 `comfyui_draw` / `comfyui_img2img` 作为 LLM 工具被其它插件直接调用，或通过宿主插件 `tool_call` 后端配置指向本插件。
  - 强调关键点：必须传 `source`（命中 `SOURCE_COMPANION_PLUGIN` 才返回 `{"image_path": ...}` JSON，否则工具自己发图且不回路径）。
  - 以伴侣插件为例给出完整配置映射（`custom_photo_tool_name=comfyui_draw`、`reference_param=image`、`extra_params={"source":"我会永远陪着你"}`、`kind_param` 留空等），并列出通用接入清单与禁止事项。

## v2.2.58

- **内置 Skill「comfyui-draw」补充参数使用要点**：在"你能做什么"后新增"参数使用要点"段落，用规则式（而非清单式）告诉 LLM 各参数何时该填、何时留空——`prompt` 必填、`workflow` 默认不传仅用户要特定画风才传且必先查列表、`image` 有参考图才传、`seed`/`denoise` 用户明确要求才传、其余参数不要求就留空。参数定义本身仍由框架随工具自动注入，Skill 只补充"怎么填"的策略，避免重复与乱传参数。

## v2.2.57

- **修复内置 Skill「comfyui-draw」的 frontmatter 格式**：`description` 原用 YAML 折叠标量 `>-` 多行写法，虽可被 `yaml.safe_load` 解析，但不符 Anthropic Skills 标准（应为单行纯文本）。已改为标准单行 `description`，确保 AstrBot 的 `_parse_frontmatter_description` 稳定解析出技能描述。

## v2.2.56

- **新增内置 Skill「comfyui-draw」，根治 LLM 聊久了忘记怎么画图/忘记工作流**：
  - 新增 `skills/comfyui-draw/SKILL.md`，AstrBot 会从插件内置 `skills/` 目录自动发现并注入。
  - Skill 以"操作手册"形式教会 LLM 三铁律：① 只要用户要新图就必须真调工具（含各种隐晦说法、以及聊久后的延续/催促）；② 指定工作流前必须先调用 `comfyui_workflows` 查真实列表，禁止凭记忆/猜测；③ 按工作流类型选提示词语言——真人/写实（非 Anima）首选中文、动漫/二次元（Anima）必须英文标签化。
  - 由于 Skill 采用渐进式加载（初始只暴露名称与描述，命中画图场景才加载全文），不占满上下文，又能在需要时强制注入完整流程。
  - `build_zip.ps1` 打包清单新增 `skills` 目录，确保 Skill 随插件一起分发。

## v2.2.55

- **强化「画图前必查工作流」约束，缓解 LLM 聊久了忘记工作流**：
  - 在 `comfyui_draw` 工具描述中新增「工作流必查规则」段落：禁止凭记忆/猜测工作流名，每次需要指定工作流时先调用 `comfyui_workflows` 查询真实列表，按用户画风/类型语义匹配后再选；用户完全没指定画风时才可省略交给默认工作流。
  - 收紧 `comfyui_draw` 的 `workflow`、`img2img_workflow` 与 `comfyui_img2img` 的 `img2img_workflow` 参数说明：不再写「大多数情况留空即可」（此措辞诱导模型偷懒不查工作流），改为「用户明确要特定画风时必先查询再填、禁止凭记忆」。

## v2.2.54

- **新增「提示词语言规范」并写入 LLM 工具约束**：
  - 规范：真人/写实工作流（`is_anima=false`）的 prompt 首选中文、除非用户明确要求英文才用英文；动漫/二次元工作流（`is_anima=true`）的 prompt **必须为英文标签化描述**，即使用户用中文描述也要翻译改写为英文 Danbooru 风格标签，不得原样透传中文。
  - 已将上述规范直接写入 `comfyui_draw` 与 `comfyui_img2img` 两个 LLM 工具的 docstring（含 `negative_prompt` 同步遵循），使大模型在每次自动生成提示词时都必须遵守。
  - 新增规范文档 `docs/prompt-language-guide.md`，作为给使用者/管理员/知识库的权威参考。

## v2.2.53

- **补全"画"系中文绘图指令的说明与帮助入口**：
  - `/drawhelp` 帮助文本新增说明：任意中文触发词（画/绘图/绘画/生图/画图/作画/画画）后跟「帮助/说明/怎么用」也会显示帮助。
  - 在 `cmd_draw_wf` 中识别自然语言帮助词（帮助/说明/怎么用/咋用/help），命中时复用 `/drawhelp` 输出。因此「画画帮助」「作图帮助」「绘图帮助」「绘画帮助」等提问都会给出绘图帮助。
  - README 指令区新增「中文绘图指令（画系）」段落：列出 7 个触发词、新语法（工作流名可选、空格分隔）、工作流名≤10字校验、其余参数与 `/draw` 一致、误触发规避规则。

## v2.2.52

- **画系指令：首 token 解析规则收紧**。上一版（v2.2.51）中，若首 token 不是已知工作流会整句当作提示词用默认工作流画图，导致「画 真人 一个女孩」中拼错的「真人」被静默忽略、直接出图，用户无法察觉工作流名写错。
  - 现在：触发词后的首 token **长度 ≤10 且不是已知工作流**时，明确回复「找不到名为「xxx」的工作流。可用工作流：…」并列出全部可用，不再静默当提示词。
  - 首 token **长度 >10**（多半是用户直接写提示词）时，仍按整句提示词用默认工作流出图，避免长描述被误判为工作流名。
  - 已知工作流名照常拆出作为指定工作流。

## v2.2.51

- **重构「画」系绘图指令，规避闲聊误触发**：此前指令语法为 `/画<工作流名> 提示词`，正则 `^[/／]?画\S*` 会把任何以「画」开头且后接非空白的消息（如「画风成熟点，再来」）误判为绘图指令，并回复「工作流是「画风成熟点，再来」，可你还没说画啥呀」。
  - 新语法：`/画 [工作流名] 提示词`——触发词后**必须跟空格**才视为指令，紧贴其它字（如「画风…」）不再触发，直接放给 LLM 正常对话。
  - 触发词扩展为：`画`、`绘图`、`绘画`、`生图`、`画图`、`作画`、`画画`。
  - 工作流名变为**可选**且必须以空格与提示词分隔：首 token 若为已知工作流则拆出作为指定工作流，否则整句当作提示词用默认工作流。
  - 指定工作流不存在时，直接回复「找不到名为「xxx」的工作流。可用工作流：…」并列出全部可用工作流，**不再静默回退默认工作流**；若用 `--wf` 显式指定不存在的工作流同样会报错。
  - 同步更新了 `_WF_HINTS.no_arg` 话术与 `/help` 帮助文本。

## v2.2.50

- **去除绘图话术中的卖萌/卑微腔调**：此前生图完成、工作流提示、各类错误提示里混入了「奴家」「乖」「人家」「好不好嘛」「呜…」「小脾气」「打盹」等自降身段的表述。已全部改为中性、干脆的陈述口吻（如「画好了，尺寸…耗时…秒」「连接不上绘图服务器，请检查服务是否在线」），去掉过多表情符号与撒娇语气。同时把 comfyui_draw 触发时机 docstring 中「不要只用文字答应『我这就画』」改为「不要只用文字承诺会画图」。
  - 注：本次仅改本插件侧的提示语。模型实际回复口吻由陪伴/人格插件决定，不在本仓库范围。

## v2.2.49

- **优化 comfyui_draw 触发时机，解决「要指名道姓才画图」问题**：此前工具 docstring 只列举了「画一只猫」「来张图」这类显式动词，导致用户用延续性说法（「再来点」「续上」「再来几张同款」「换个姿势」「刚才那张重跑」）或催促（「你咋忘了怎么画图了」）时，LLM 只回文字描述、不真正调用工具。
  - 已在触发时机中补充三类必须调用的场景：① 显式动词；② 延续前文画图的请求（即使无「画/图」动词也视为新画图意图）；③ 用户吐槽/提醒没画图（视为催促，必须立即调用）。
  - 新增核心原则：**只要用户想要新图，无论多隐晦都调本工具，绝不用文字复述「我会画 XX」替代真实调用**。
  - 预期效果：减少用户反复强调才能触发画图的情况。

## v2.2.48

- **修复图库详情看不到 Seed**：前端大图弹窗（`anima-console/app.js`）误读字段名 `img.positive_seed`，但后端 `_row_to_dict` 实际返回的是 `seed` 字段，导致 Seed 行永不渲染。已改为正确的 `img.seed`。
  - 背景：种子早已在出图时存入 `images.seed`（`main.py` 调 `archive_image(..., seed=seeds_used[0])`），后端查询与 `gallery/image?meta=1` 接口均正常返回，仅是前端展示字段名不匹配。
  - 现在图库详情弹窗的「Seed」可正常显示，便于复现/对照生成参数。

## v2.2.47

- **图库与出图记录改为翻页式分页**：将此前「加载更多」按钮改为标准的**上一页 / 第 X / N 页 / 下一页**翻页控件（图库每页 40 张、出图记录每页 40 条）。每次翻页替换当前页数据，不再累加。后端 `gallery/search` 与 `records` 均已支持 `page/size` 返回真实 `total`。

## v2.2.46

- **修复「文生图被误判成图生图导致报错」**：`llm_draw` 旧逻辑无条件取图——只要从历史/会话/生成图里捞到一张旧图就判定为图生图，导致用户明明是文生图，却被拿去走图生图工作流、去找 `LoadImage` 节点，而工作流没有该节点就报错。
  - 现在严格区分：**判定图生图的唯一依据 = LLM 显式传了 `image` 参数，或用户本次消息/引用里真的有图**（`_extract_images` 从原始事件取到）。
  - 历史/会话/生成图兜底（`g_last_received` / `g_recent_user_images` / `g_last_generated`）**只在已判定为图生图、但参考图还没进 event 时**用来补图，**绝不让捞到旧图反过来把文生图误判成图生图**。
  - 若判定为图生图但最终没取到任何参考图，**降级为文生图提交**，不再无图硬走图生图工作流。
  - 效果：文生图稳定走文生图工作流不再报错；图生图（本次消息带图 / LLM 传 image）正常取参考图。

## v2.2.45

- **画廊不展示失败项目**：`image_store.search`/`count_search` 检索图库时排除失败记录（`status=1`/`ext='fail'`），图库网格只显示成功生成的图。
- **WebUI 出图记录（日志页）分页**：`get_records` 支持 `page/size` 分页（每页 40 条），并用 `count_records` 返回真实总数；前端出图记录表格新增「加载更多」按钮，滚动累加加载（按 sha 去重）。避免一次拉取几百条记录导致响应慢。

## v2.2.44

- **进一步降低「AI 选错工作流」概率**：`comfyui_draw`/`comfyui_img2img` 的 `workflow`/`img2img_workflow` 参数说明改为**明确引导「大多数情况留空即可，插件自动用默认工作流」**；只有用户明确要求特定画风、且通过 `comfyui_workflows` 查询到确切名称时才传。避免 AI 凭记忆/猜工作流名（工作流靠 `name` 引用，与文件名无关，猜名必失败）。

## v2.2.43

- **改善「AI 总是找不对工作流」的问题**：
  1. `_resolve_workflow` 匹配更宽容：名称匹配增加**大小写不敏感 + 去除首尾空格**（AI 常把 `Default` 写成 `default` 之类）；并保留按文件名（`workflow_name`，带/不带 `.json`）回退。
  2. 工作流找不到时，**报错提示里列出所有可用工作流名**（如 `找不到名为「xx」的工作流。可用工作流：sd、sdxl、manga。`），让用户/AI 一眼看出该传哪个名字。
  3. `comfyui_draw`/`comfyui_img2img` 的 `workflow`/`img2img_workflow` 参数描述加强：必须用 `comfyui_workflows` 查询返回的**确切名称**，禁止凭记忆/猜测，否则会找不到工作流。

## v2.2.42

- **LLM 工具执行异常不再冒泡成 AstrBot 的「调用工具报错」**：此前若 `comfyui_draw`/`comfyui_img2img` 内部有未捕获异常，会直接抛到 AstrBot，用户在对话里只看到笼统的「工具调用报错」，无法定位。现给两个工具加 `_safe_llm_tool` 装饰器：
  1. 任何未捕获异常都被捕获，`logger.error` 打印**完整 Traceback 堆栈**到插件日志；
  2. 工具返回一句可读失败说明（不冒泡），用户看到的不是「报错」而是明确提示。
  - 目的：让「调了工具却出不来图」这类问题的**真实异常原因**浮出日志。若装上后仍复现，请把日志里 `[comfyui_draw] 工具执行异常` 或 `[comfyui_img2img] 工具执行异常` 那一段 `Traceback` 发给作者即可精确定位。
  - 注意：装饰器用 `functools.wraps` 保留 docstring，不影响 `@filter.llm_tool` 解析工具 schema。

## v2.2.41

- **重构 WebUI 图库的图片获取与展示，彻底解决「图库/出图记录图片加载不出来」**。参考成熟实现 `astrbot_plugin_stealer` 的图库方案重构：
  1. **列表接口只返回元数据**：`gallery/search` 不再把任何缩略图 base64 内联进 JSON（此前 v2.2.39 一次内联几十张缩略图，图一多就超时），只返回 `{ images, total, page, size }`。
  2. **新增按需缩略图接口 `gallery/thumb?sha=&size=300`**：单张返回 Pillow 压缩的小尺寸 base64 data URL。
  3. **前端懒加载**：图库卡片/出图记录缩略图改为 `IntersectionObserver`，图片**进入视口**时才经 bridge `apiGet('gallery/thumb')` 拉取单张 data URL，配 **LRU 缓存**（200 张）与占位图。既不走 AstrBot 裸路径（404/401），也不会一次拉几十张图超时。
  4. **分页**：`gallery/search` 支持 `page/size`，前端新增「加载更多」按钮，滚动累积加载，`total` 用真实 COUNT（新增 `image_store.count_search`）。
  5. 大图弹窗沿用经 bridge 拉原图 data_url 的方式。
  - 效果：无论图库多少张图，列表响应轻快，图片滚动到哪加载到哪，不再空白/超时。

## v2.2.40

- **恢复 LLM 绘图工具的高级参数暴露给大模型**：此前 v1.2.5 为"降低空 JSON 概率"曾把 `comfyui_draw`/`comfyui_img2img` 暴露给大模型的参数精简为仅 `prompt/negative_prompt/workflow/img2img_workflow`，导致大模型**无法主动传宽高、LoRA、seed、denoise、参考图**（例如用户要求"画一张 1024x768 的图""固定种子复现"时无从传达）。
  1. `llm_draw` 的 `Args:` 重新暴露 `width(number)/height(number)/loras(array[string])/seed(number)/image(string)/denoise(number)`；
  2. `llm_img2img` 的 `Args:` 重新暴露 `loras(array[string])/seed(number)/image(string)/denoise(number)`；
  3. 二次提取（`_llm_extract_args`）的 spec 与回填逻辑同步补上 `width/height/seed/denoise`，模型空参数时仍能从用户原话兜底提取；
  4. 参数传递链路确认：`llm_draw` 已将 `width/height/seed/denoise/loras` 传给 `_do_draw`（`width or None`、`seed or None`、`denoise if denoise >= 0 else None`），模型传入即可生效。
  - 保留 `prompt` 必填保护：模型未填 prompt 时仍走二次提取/原始消息兜底，避免"参数空洞"死循环。
  - 兼容性：`width(number)`/`loras(array[string])` 等类型均在 AstrBot `SUPPORTED_TYPES` 内，注册校验通过，不影响工具加载。

## v2.2.39

- **修复 WebUI 图库/出图记录图片全部加载不出来（图库空白）**：根因是后端 `gallery_search` / `get_records` 返回的 `thumb`/`thumb_url` 用了**裸路径** `/{插件名}/gallery/image?sha=...`，而 AstrBot 的插件 API 实际挂在 `/api/v1/plugins/extensions/<插件名>/...` 下且需登录 token，浏览器 `<img>` 直连该裸路径 → 404/401 → 图加载不出来。同时大图弹窗 `imageUrl()` 也用了同款裸路径，点开大图同样空白。
  1. 后端新增 `_thumb_data_url`：用 Pillow 把图**压缩到小尺寸（宽 300px）再 base64 内联**成 data URL，前端 `<img src>` 直连即显示——data URL 不走路由、无需 token，且体积小，不会像 v2.2.26 之前那样因一次内联几十张**整图** base64 导致 10s 超时（超时根因是"整图"，不是 base64 本身）。环境无 Pillow 时降级为直接内联原图。
  2. `gallery_search` / `get_records` 改为返回该缩略图 data URL；
  3. 前端大图弹窗 `openImage` 改为经 bridge `apiGet("gallery/image?sha=...&meta=1")` 拉取原图 data URL 渲染，不再直连裸路径。
  - 影响：图库缩略图、出图记录缩略图、大图弹窗均可正常显示。

## v2.2.38

- **修复图生图引用图解析失败时「服务器有图却用不上」**：用户引用本插件之前生成的图做图生图时，引用图走平台 `get_msg` API 拉取，常因协议不支持而失败（"本地读不到"）；但那张图其实就在 AstrBot 部署服务器的 `gallery/` 目录里、路径也已记录在 `g_last_generated[sid]`。此前 `comfyui_img2img` / `llm_draw` 取图兜底**刻意跳过**了本插件生成的图，导致明明服务器有图却取不到。现增加三级兜底：在用户发来的图、历史图都取不到时，回退到**本会话最近 1 张本插件生成的图**（服务器本地 gallery 路径，含存在性校验），限定本会话+最近1张以压低误用旧图风险。典型场景：引用 AI 刚生成的图、平台拉不到引用消息时，直接从服务器取图做参考图。

## v2.2.37

- **修复 v2.2.36 启动报错 `filter has no attribute 'on_message'`**：本版本 AstrBot 的 `astrbot.api.event.filter` 未提供 `on_message` 装饰器（仅有 `on_agent_begin` / `on_llm_request` / `on_using_llm_tool` 等）。将新增的「每条用户消息抓图」钩子由 `@filter.on_message()` 改为 `@filter.on_agent_begin()`——它在本条用户消息进入 LLM 前仅触发一次，event 即为用户原始消息（含图片与引用图），同样能在最早时机把图写入 `g_recent_user_images` 滚动缓存，覆盖前一条消息图与引用图未回填的兜底需求。

## v2.2.36

- **修复图生图取不到「前一条消息发的图」和「引用消息里的图」**：旧实现只在 LLM 工具即将被调用时（`_capture_llm_event`）才去抓图片，而此时 event 已是 AI 自己的回复上下文，往往不含图——导致用户「先发图、AI 回复后说改这张图」这类场景取不到之前的图；引用图又只依赖 `extract_quoted_message_images` API（多数平台返回空）。现改为在**每条用户消息到达时**（`@filter.on_message` 钩子）即捕获该消息里的图片（含引用图），写入新的按会话滚动缓存 `g_recent_user_images`（保留最近 12 张）。`comfyui_draw` / `comfyui_img2img` 在「当前消息 + 引用 + `_last_event` + `g_last_received`」都取不到图时，再回退到 `g_recent_user_images` 最近 1 张，从而覆盖前一条消息图与引用图未回填的情况。

## v2.2.35

- **修复「参考图没填充到工作流」**：旧逻辑只认标准 `LoadImage` 节点（`find_node_by_class(prompt, "LoadImage")`）。但很多图生图工作流用的是非标准图加载节点（如 `LoadImageFromPath` / `LoadImageV2` / `ImageLoader` 等），导致自动查找找不到 → 注入被跳过 / 报错「没有 LoadImage 节点」，参考图没填进去。
  1. 新增 `find_image_loader_node`：优先标准 `LoadImage`，否则按 `IMAGE_LOADER_HINTS` 前缀/全称匹配常见图加载类节点；
  2. 找不到图加载节点时，报错信息会**列出工作流里疑似图加载节点的 id**（如 `39(LoadImageFromPath)`），方便用户在插件配置的 `image_node` 里手动指定键名；
  3. 注意：ComfyUI 前端 LoadImage 节点的 image 下拉框**不显示**通过 `/prompt` API 注入的文件名是正常现象，服务端执行时即用该文件名去 input 目录取图；以日志里「已注入参考图到节点 XX -> 文件名」为准。

## v2.2.34

- **修复「对话里 AI 自动图生图、引用了用户之前发的真实照片」读不到图**：用户引用一条带图历史消息让 AI 改图时，被引用图挂在那条历史消息的 `Reply` 上，AI 调 `comfyui_draw` 时只传 `prompt`+`workflow`、既不带图也不带引用，工具拿到的 event 里 `Reply.chain` 常为空，AstrBot 引用解析器回拉到的又是带签名内网 URL，`convert_to_file_path` 下载失败 → 引用图读不到 → 静默文生图。修复：
  1. 新增 `_download_url_to_temp`：`convert_to_file_path` 失败时，对 http(s) URL（引用图床地址）自带 UA/同域 Referer 兜底下载到本地 `temp/`，并校验内容非空；
  2. `_capture_llm_event` 缓存「会话最近收到图片」时，额外专程解析「引用消息里的图」并落地，确保 `comfyui_draw` / `comfyui_img2img` 的 `g_last_received` 兜底（v2.2.32 引入）能命中用户引用的图；
  3. 全程补充 [取图] 诊断日志，方便下次确认引用图落在哪一步。

## v2.2.33

- **修复图生图提交 ComfyUI 报 400 Bad Request**：注入参考图时把 `[filename, subfolder, type]` 三元组写进了 `LoadImage` 节点的 `image` 输入，但标准 `LoadImage` 在 `/prompt` API 下 `image` 输入期望**字符串文件名**；三元组是节点间连线的引用格式，当单输入框值会直接 400。这是 v2.2.29 把文件名改三元组引入的回归（v1.0.69 当年改字符串后 400 即消失，方向本就正确）。现改回只传字符串文件名——`upload_image` 已把图写到 `type=input` 目录，LoadImage 凭文件名即可在 input 目录找到。注意：v2.2.29 那个 "Permission denied" 是远端 ComfyUI `input` 目录权限问题，与用字符串还是三元组无关，不应因此改格式。

## v2.2.32

- **修复「AI 总结后调图生图但图片没传过来」导致按文生图提交（LoadImage 为空）**：用户先发一张图，AI 用自己的话总结并调用 `comfyui_draw` / `comfyui_img2img` 做图生图时，工具收到的 event 是 AI 的纯文本回复，图片既未被引用也没被带入 event，`_extract_images` 取不到（日志表现为「消息组件共 1 个 -> Plain」「未取到任何参考图，按文生图处理」）。v2.2.31 一刀切删掉历史兜底后，这种合理场景也失败了。现恢复「本会话用户最近发来的图」(`g_last_received`) 兜底——该缓存在 LLM 工具调用前趁图片还在时已由 `_capture_llm_event` 写入，正好覆盖"用户刚发图、AI 随后调用绘图工具"的场景。仍**不回退本插件自己生成的图** (`g_last_generated`)，避免续画/上次出图被误当图生图参考图。

## v2.2.31

- **修复「写入失败记录出错：'NoneType' object is not subscriptable」**：`add_failed_record` 误把字符串 bytes 传给只接受文件路径的 `_sha256_of`（内部 `open(bytes)` 抛异常被吞、返回 None），随后 `None[:16]` 切片崩溃。现改为直接用 `hashlib.sha256(...).hexdigest()[:16]` 计算失败记录的去重键，崩溃消除，出图失败记录可正常写入「出图记录」。
- **修复 AI 误用图生图工作流（用户没发图就图生图）**：`comfyui_img2img` 工具此前在「本次消息/引用都没取到图」时会回退到「本插件历史生成图 / 会话历史收到图」缓存，导致用户纯文字让 AI「改图」却根本没发图时，工具仍带着一张来路不明的旧图跑图生图（表现为「AI 没取图就用图生图工作流」、且常因旧图失效导致无图）。现移除历史缓存兜底，只认 `image` 参数 + 本次消息/引用里的图 + LLM 调用前趁图还在时缓存的 `_last_event`；都没有则明确报错「请先发送一张参考图」，让 AI 自己意识到需要用户先发图，而非静默用旧图出图。

## v2.2.30

- **修复图生图"参考图根本没传上去 / LoadImage 节点为空"（核心取图门控 bug）**：旧逻辑只在 LLM 显式传入 `image` 参数时才去消息里取图；但用户最常见的图生图方式是「直接发一张图 + 一句文字（如『改成水彩风』）」，此时图片作为多模态输入连同文字一起发给大模型，LLM 不会、也不应再传 `image` 参数。结果取图分支被整个跳过，`init_images` 为空，任务以文生图提交，ComfyUI 端 `LoadImage` 节点 `image` 字段保持为空——正是用户看到的"图生图根本没传图片上去"。现改为：只要 `image` 参数没成功取到图，就无条件去本次消息/引用里自动提取图片（含 LLM 工具调用前趁图还在时缓存的 `_last_event` 兜底），一旦取到图即判定为图生图并把参考图注入 LoadImage 节点。仍严格不翻历史缓存（`g_last_received`/`g_last_generated`），避免"再来一张"被误判为图生图。

## v2.2.29

- **修复图生图参考图注入路径错误（导致 ComfyUI 端 `Permission denied: .../input`）**：图生图时插件把上传后的参考图以**裸文件名字符串**注入 `LoadImage` 节点的 `image` 输入，部分 ComfyUI 加载类节点会把裸字符串误解析为 `input` 目录，触发 `av.open` 打开目录的 `PermissionError`。现按 ComfyUI 官方 API 约定改为注入 `[filename, subfolder, type]` 三元组（完整使用 `upload_image` 返回信息），由 ComfyUI 正确拼出文件路径。

## v2.2.28

- **修复「写入失败记录出错：'NoneType' object is not subscriptable」**：`_record_failed` 在出图失败时写失败记录，但工作流名/用户信息取值不够健壮，偶发崩溃。现改为统一安全取法（`_wf_name` 兼容 dict/None/其他类型；用户信息取值防御 event 为 None），并在异常时打印完整栈（`exc_info=True`）+ 参数类型，便于下次精准定位。失败记录不再因崩溃而丢失。
- **降低「任务完成但未找到输出图片节点」误报**：`extract_images` 此前只认 `outputs[...].images`。部分工作流（VideoCombine / AnimateDiff 等）用 `gifs` 字段输出，导致明明出图却被判为无图。现兼容 `images` 与 `gifs`，减少无图误报。

## v2.2.27

- **大图详情面板补充「工作流」字段**：之前只显示类型，缺少生成该图所用的工作流名称。数据库 `images.workflow` 字段本来就有数据（`get_by_sha` 的 `meta` 也已返回），是前端 `openImage` 漏渲染了。现已在「类型」之后追加显示工作流名称。

## v2.2.26

- **图库改为返回图片链接而非 base64 内联（修复超时根因）**：之前 `gallery/search`、`gallery_image`、`recent_records` 都把整张原图 base64 内联进 JSON，图一多响应体爆炸（几十张图可达数 MB~数十 MB），序列化/传输/解析在 10s 内传不完，表现成「搜索超时」。改为：
  - `gallery/image` 用 `file_response` 直接返回图片 binary（浏览器原生 `<img src>` 加载、支持断点、不占 API JSON 体积）；`?meta=1` 时仍返回含元数据 + data_url 兜底的 JSON，供大图弹窗取信息。
  - `gallery/search` 与 `recent_records` 只返回图片 URL（`/astrbot_plugin_comfyui_anima/gallery/image?sha=xxx`），前端列表用 `<img loading="lazy">` 懒加载。
  - 前端大图弹窗 `openImage` 改为直接 `imageUrl(sha)` 立即显示、meta 异步补充，不再等 base64 大图。
- **前端 bridge 容错增强**：候选路径顺序调整为绝对路径（`astrbot_plugin_comfyui_anima/...`）优先，且「路由不存在(404) / 超时 / 网络异常」任一候选失败都继续尝试后续候选，单次超时从 10s 降到 6s，最终失败才抛出并附带全部尝试记录。

## v2.2.25

- **修复「在另一个地址访问时 gallery 搜索超时」的根因**：之前后端路由注册成了 `/<plugin_name>/page/<action>`，前端又硬塞 `page/` 前缀，形成比官方约定多一层的 `/page`。在默认的 AstrBot 后台 iframe 里能侥幸命中，但一旦访问方式 / bridge 版本有细微差异（如在另一地址打开），转发规则对 `page/` 这层失配，请求 hang 到 10s 超时。现对齐 AstrBot 官方《插件 Pages》约定：后端路由改为 `/<plugin_name>/<action>`（去掉 `/page`），前端候选不再加 `page/` 前缀，与其他正常插件完全一致。控制台日志（v2.2.24 加的）保留，便于后续排查。

## v2.2.24

- **增强控制台请求日志（排查「另一个地址访问超时」问题）**：
  - 后端 `webui_api.py` 给所有控制台路由包了请求级日志 wrapper，每次请求打印：实际命中路径、客户端 IP、`耗时(ms)`、返回状态（OK / ERR 及异常信息）。可在后端日志里直接确认「请求到底有没有打到后端、卡在哪」。
  - 前端 `bridgeRequest` 在浏览器 console 打印每次候选路径的尝试：风格(extension/plugins/plugin)、完整 endpoint、耗时、结果（命中 / 路由不存在 404 / 异常）。最终失败时把「已尝试的全部路径」写进错误信息，便于对照实际访问地址。
  - 说明：`gallery/search` 的超时多半是「在 AstrBot 之外的另一个地址（如反代子路径、非 iframe 宿主）打开页面」导致桥接把请求发到了错误的 host/path，请求 hang 到 10s 超时。后端若完全没有对应路径的 OK 日志，说明请求没进后端，应检查反向代理/访问地址；若有 OK 但前端仍超时，则是响应体过大/网络问题。

## v2.2.23

- **`llm_model` 配置改为下拉选择框**：此前是文本输入框，需手动填写 LLM 服务提供商名称/ID。现改用 AstrBot 配置 schema 的 `_special: "select_provider"`，在管理面板里直接以下拉框形式列出 WebUI 已配置的 LLM 服务提供商，无需手动抄写，避免填错；留空项表示沿用 AstrBot 默认对话模型。后端取值逻辑不变（`chat_provider_id` 仍接受 provider 名称/ID 字符串）。

## v2.2.22

- **新增「指定大模型调用工具」配置（`llm_model`）**：部分默认对话模型不支持 Function Calling，导致 AI 调用 `comfyui_draw` / `comfyui_gallery` 等工具时传参全空、出图失败。新增配置项 `llm_model`（可选，填 LLM 服务提供商的名称/ID），插件在工具参数空洞时，会用该指定模型重新理解用户原始指令并补全参数（prompt/keyword 等）。留空则沿用 AstrBot 默认对话模型，行为不变。接入位置：`llm_draw` / `llm_img2img` 的 prompt 兜底分支与 `llm_gallery` 的 search 模式 keyword 兜底分支，统一走新增的 `_llm_extract_args()`（基于 `context.llm_generate(chat_provider_id=...)` 提取 JSON）。

## v2.2.21

- **大图弹窗体验优化**：
  1. 放大大图显示尺寸：图片 `max-width` 从 `min(640px,60vw)` 提升到 `min(900px,78vw)`，弹窗 `max-width` 从 `960px` 提升到 `min(1240px,96vw)`，图片区 `flex:1` 自适应占更大空间，弹窗高度上限提到 `90vh`。
  2. 点击空白/背景区域关闭大图：给 `#imageDialog` 绑定 click，当点击的不是图片、不是信息面板、不是关闭按钮时（即点 backdrop 或图片周围空白）自动关闭弹窗。

## v2.2.20

- **大图弹窗三项体验修复**：
  1. 弹窗在屏幕中居中：`.image-dialog` 增加 `margin:auto`。
  2. 大图信息面板不再空白：后端 `gallery/image` 接口除 `data_url` 外，新增返回该图的完整元数据 `meta`（提示词/尺寸/耗时/用户/触发消息/Seed/状态/收藏等），前端 `openImage` 改用 `data.meta` 填充信息面板。
  3. 出图记录列表的缩略图支持点击查看大图：缩略图加 `data-open` 属性并绑定点击，调 `openImage(sha)`（失败图无真实图片则不绑定）。

## v2.2.19

- **修复图库（非回收站）永远空白的真 bug：`search()` 参数绑定数量不匹配**。`search(trash=False)` 时 SQL 拼成 `AND deleted=0`（硬编码，无占位符 `?`），但代码仍向参数列表 `append(0)`，导致实际占位符数（仅 `LIMIT ? OFFSET ?` 共 2 个）比传入参数（多出一个 `0` 共 3 个）少一个，SQLite 抛 `Incorrect number of bindings supplied` 异常，被 `search` 的 try/except 吞掉返回空数组——于是图库主视图永远空、且日志里出现 `检索失败`。回收站（`trash=True`）用了 `?` 并正确 append，所以反而能查到，这也解释了「出图记录有图、图库空白」的表象。改为仅 `trash=True` 时使用 `AND deleted=?` 并 append 参数，否则硬编码 `deleted=0` 不 append。

## v2.2.18

- **给前端静态资源加版本化缓存破坏**。`build_zip.ps1` 打包时自动给 `pages/anima-console/index.html` 里的 `./app.js` / `./styles.css` 追加 `?v=<版本号>` 查询参数（仅写入 zip，不改动仓库源码），每次发布强制浏览器拉取新文件，避免旧缓存导致「统计有数但图库空白」等假象。

## v2.2.17

- **优化图库空状态提示，区分「库是空的」与「搜索无结果」**：之前无论图库是否为空、是否输入了筛选条件，统一显示「没有找到匹配的图片」，容易让人误以为有 bug。现在未输入任何筛选且图库确实为空时，提示「图库里还没有图片，先让插件出一张图」；输入筛选后仍为空才提示「没有找到匹配的图片」。纯前端优化，不影响数据逻辑。

## v2.2.16

- **修复图库一直空白、回收站点击无反应**：根因是前端 `galSearch()` 解析响应时只认 `data.results` / `data.images`，但后端 `gallery/search` 实际返回的是 `{rows, total}`，导致 `state.galResults` 永远为空数组——图库永远显示空、回收站也永远显示空（看起来像「点击无反应」）。改为兼容 `data.rows || data.results || data.images`。纯前端修复。

## v2.2.15

- **修复 WebUI 一直显示「部分数据加载失败」报错条**：根因是 `.global-error` 样式设置了 `display:flex`，覆盖了元素 `hidden` 属性的默认 `display:none`，导致这个错误条在没有任何错误时也会被强制显示（里面写着占位的「部分数据加载失败。」文案）。新增 `[hidden]{display:none!important}` 通用规则，让所有带 `hidden` 的元素（错误条、回收站角标、日志子面板等）恢复正常隐藏逻辑。纯前端修复，数据与接口无变化。

## v2.2.14

- **图库交互优化 + 回收站（软删除）机制**：
  - **点击图片直接看大图**：取消单独的「放大」按钮，点任意缩略图即打开大图弹窗（`<dialog>`，图片+信息分栏）。
  - **大图弹窗显示图片信息**：SHA、类型(文生图/图生图/参考图/用户收藏)、尺寸、占用大小、出图耗时、出图时间、用户、触发消息、成败状态、收藏、Seed、提示词全文。
  - **回收站（软删除）**：`images` 表新增 `deleted`/`deleted_at`；普通「删除」=移入回收站（保留文件，收藏图不可删）；图库新增「回收站」tab（带未读角标），回收站内可「恢复」或「彻底删除」（二次确认，真删文件+记录）。`stats` 统计排除已删除项并给出回收站计数。
  - 后端新增 `gallery/trash`、`gallery/restore`、`gallery/purge`；`gallery/search` 支持 `trash=1`；`gallery/delete` 改为软删除。
  - 指令新增 `/gallery trash`(查看回收站) / `restore <sha>`(恢复) / `purge <sha>`(彻底删除)；`/gallery del` 改为移入回收站。
  - 未引入第三方组件：受限 iframe 下直接采用原生 `<dialog>` + CSS 实现大图查看器，更稳更轻量。

## v2.2.13

- **「日志」界面改为出图记录为主，并补全图库出图信息**：
  - `images` 表新增字段：`size_bytes`（占用大小）、`cost_sec`（出图耗时）、`user_id`/`user_name`（哪个用户）、`trigger_msg`（触发的消息）、`status`（0 成功 / 1 失败）；旧库自动 ALTER TABLE 迁移补全。
  - 出图成功时 `archive_image` 写入以上字段（耗时=出图开始到归档；大小=文件字节；用户/消息取自 `event`）。
  - 出图失败时（提交失败 / 超时 / 无图）通过 `add_failed_record` 写入一条「失败记录」，含用户、消息、耗时、失败原因。
  - 新增 `webui_api /records` 接口与 `ImageStore.recent_records`：返回结构化出图记录（含缩略图 data_url）。
  - WebUI「日志」页拆分为双 tab：**出图记录**（默认，表格展示 时间/用户/触发消息/尺寸/大小/耗时/状态/提示词/预览，支持搜索与「仅看失败」）与 **运行日志**（原日志）。
  - 图库项现已包含：图片尺寸（w×h）、占用大小、出图耗时、出图时间、触发用户与消息、成败状态。

## v2.2.12

- **修复出图后收不到图片（temp 文件被归档移动后指向失效）**：
  - 根因：`ImageStore.archive_image` 会把成品图从 `temp/` **移动**到 `gallery/`（移动转正），但 `_do_draw` 仍用旧的 `temp/` 路径去 `event.send` / 上报 / 加入会话图列表，导致 `[Errno 2] No such file or directory: '.../temp/....webp'` 与「主动发送图片失败」。
  - 改动：`archive_image` 现返回**归档后的最终路径**（去重命中返回已存在文件真实路径）；`_do_draw` 在归档后用返回值覆盖 `img_path`，使发送、报告、会话最近图均指向真实文件。`archive_user_image` 仍返回 sha（从最终路径反算）以保持 `/gallery save` 等指令兼容；参考图归档处同样反算 sha 回填 `ref_sha256`。

## v2.2.11

- **修复日志界面空白 + “部分数据加载失败”提示**：
  - 后端 `webui_api.get_logs` 在内存环形缓冲 `LOG_BUFFER` 为空（刚重载/尚未产生日志）时，回退读取 `data_dir/webui.log` 文件尾部，保证页面始终能展示历史日志，不再因缓冲为空而显示空。
  - 前端日志默认级别由 `WARN / ERROR` 改为「全部级别」，避免刚打开日志界面因默认过滤而显示为空。
  - `loadLogs` 在真正加载失败时抛出错误，使刷新逻辑能正确把“日志”记入失败项、顶部横幅提示更精准；空结果给出明确提示文案。

## v2.2.10

- **彻底对齐参考插件 astrbot_plugin_private_companion 的前端桥接层（这是空壳/读不到配置项的真正根因，此前一直漏看它的前端写法）**：
  - `getBridge()` 同时查找 `window.AstrBotPluginPage` **和 `window.parent.AstrBotPluginPage`**：插件页面在 AstrBot 后台以 iframe 嵌入，桥接对象挂在 parent 上。旧实现只查 `window`，导致 iframe 内 bridge 为 null → 配置/日志/画廊全部加载失败。
  - 移植 `bridgeRequest` + `bridgeEndpointCandidates`：对同一 endpoint 依次尝试 6 种路径风格（page/bare/slash/full/fullSlash/cached 含与不含插件名），命中 404/「未找到路由」自动换下一个候选，不再依赖单一前缀写法。
  - 移植 `normalizeResponse`：把后端返回值统一为 `{success, data, error}`，前端只取 `data`，彻底消除此前私有 `{status,data}` 协议与框架解包不一致的问题。
  - 删除旧的重复 `getBridge`（只查 window、最多 8s 轮询）与全局 `bridge` 变量，统一走 `getPageBridge()`。
  - 后端 webui_api.py（v2.2.8 起的 `astrbot.api.web` + `json_response(value)` 写法）与这套前端完全兼容：json_response(value) → normalizeResponse 包成 {success:true,data:value}。

## v2.2.9

- **打包修复：从 build_zip.ps1 的 `includeList` 中移除 `workflow` 目录**。仓库根 `workflow/*.json` 只是默认/参考工作流样例，插件运行时工作流来自 `data_dir/workflow/`（main.py 的 `self.workflow_dir.mkdir` 自建），不应把样例打进插件包，避免污染用户 data_dir 或造成路径混淆。
- **前端诊断增强**：给 `apiGet/apiPost` 加 10s 超时（`Promise.race`），接口 hang（路由未注册/插件未重载）时不再永远停在「正在读取…」空壳，而是抛出明确错误（如「GET config 超时…可能后端路由未注册或插件未重载」），便于定位。
- 说明：webui_api.py 维持按官方文档 plugin-pages.md 的 `astrbot.api.web` 写法（v2.2.8）。若刷新后页面显示「超时」红字，说明当前运行的插件实例未重载到新版本（请重载/重装插件）；若显示「读取配置失败：xxx」，则 xxx 即为后端真实报错，可据此定位。

## v2.2.8

- **彻底按 AstrBot 官方文档（docs/zh/dev/star/guides/plugin-pages.md）重写 `webui_api.py`，纠正此前所有猜测式写法**：
  - 后端改用 `from astrbot.api.web import request, json_response, error_response`，不再暴露 Starlette / Quart / FastAPI 原始请求对象（文档明确要求）。
  - handler 不再声明 `request` 参数，请求数据统一用模块级 `request`（如 `request.query.get("x", 20, type=int)`、`await request.json(default={})`）。
  - handler 返回 `json_response(value)` / `error_response(msg)`，不再返回 `JSONResponse`/`Response`。bridge 的 `apiGet/apiPost` 会把响应体原样 resolve 为 value 本身，前端无需再解包 `{status,data}`。
  - 路由前缀保持含插件名 `/<plugin_name>/page`（v2.2.7 已确认，本轮沿用并补充文档佐证）。
- **前端 `app.js` 同步修正桥接调用**：
  - `apiGet/apiPost` 增加 `_unwrap`，把后端 `error_response` 返回的 `{status:"error",message}` 形态转为异常，调用处统一走 catch。
  - `loadLogs` 取值由错误的 `data.logs` 改为 `data.lines`（后端返回 `{lines,total}`）。
  - 配置/图库/schema 等接口现在直接拿到解包后的数据本身，不再误判 `data.results/data.images`。
- 文档关键结论：插件 WebUI 路由必须由 `context.register_web_api(path, handler, methods, desc)` 注册；路径是相对插件页面的路径；请求/响应统一走 `astrbot.api.web`，不要混用框架原生对象。

## v2.2.7

- **纠正 v2.2.6 关于「路由前缀应为相对路径」的错误结论——这正是 WebUI 一直加载中、读配置读一年、图库无数据的根因**。重新核对伴侣插件 `astrbot_plugin_private_companion/page_api.py`：其 `PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"`，前端 `HTTP_API = "/astrbot_plugin_private_companion/page"`，**路由必须含插件名**。AstrBot 的插件页面桥接会把前端请求拼成 `/api/plugins/extensions/<plugin_name>/page/<endpoint>`，后端也必须按含插件名的完整路径注册，否则全部 404 → 前端永远转圈、读不到配置、图库空。现把 `webui_api.py` 的 `prefix` 改为 `f"/{PLUGIN_NAME}/page"`（新增 `PLUGIN_NAME` 常量），修复后配置/图库/日志三个接口全部正常返回。
- **修复图库数据库 `image_store.py` 缺失 `import json` 的致命 bug**：原文件在模块最底部才 `import json`，而 `archive_image` 在顶部逻辑里就调用 `json.dumps`，导入顺序导致 `NameError`，图库写入与检索全部崩溃。现已把 `import json` 提到标准位置（模块顶部），删除底部重复导入。

## v2.2.6

- **修复 WebUI「页面一直在加载中、没数据」的真正根因（两次误判后的实锤）**。
  通过阅读 AstrBot 源码 `astrbot/dashboard/asgi_runtime.py` 的 `call_request_view` / `bind_quart_request_context` 确认：**宿主在调用 web_api handler 时，只通过路由占位符传参，从不向 handler 传递 `request` 参数**，而是把 Quart 全局 `request` 绑定到当前上下文。因此：
  1. 之前的 handler 全部把 `request: Request` 声明为函数参数，宿主调用时缺少该参数 → `TypeError: missing required positional argument 'request'` → 所有接口 500/挂起 → 前端永远拿不到数据、一直转圈。现改为去掉 `request` 参数，改用 `from quart import request` 的全局对象（`request.args` / `await request.get_json()`），与伴侣插件 `astrbot_plugin_private_companion/page_api.py` 的写法一致。
  2. 路由前缀回退为**相对路径** `/page`（不含插件名）：伴侣插件所有路由均为相对路径 `/overview`、`/config/...` 且正常工作，说明 AstrBot 按插件名前缀匹配，无需在 path 里写插件名。v2.2.5 加回插件名前缀属于误判，本次回退。
  3. 图库列表 `gallery/search` 直接返回每张图的 `data_url` 缩略图（前端 `img.src` 直接用，不再依赖外部路径或二次请求），避免缩略图裂图与多次请求导致的"加载中"观感。前端卡片渲染相应改为直接使用 `img.thumb`。
  4. `bridge.ready()` 增加 3s 超时保护，避免极端情况下卡在"正在连接…"。

## v2.2.5

- （误判记录，已在 v2.2.6 回退）曾误以为 v2.2.2 缺插件名前缀导致 404；实为方向判断错误。路由前缀在 v2.2.6 恢复为相对路径。

## v2.2.4

- **重写 WebUI 两大核心功能，使其真正可用**：
  - **配置编辑器基于 `_conf_schema.json` 重写**：原来把 config 当黑盒拍平成几十上百个裸输入框（卡死、无说明、保存不了）。现改为读取插件配置 schema，按字段类型结构化渲染：分组（section）、嵌套对象、布尔开关（switch）、数字+滑块联动（slider）、多行文本，以及 `template_list` 类型（ComfyUI 服务器列表 / LoRA 库 / 工作流列表）支持可视化增删条目。每个字段展示 description 与默认值 hint，修改即标脏、可一键保存。保存时按 schema 结构构造完整 config 回传（`/config`，body 为 `{config: ...}`）。
  - **图库管理真正可用**：修复了搜索参数名错误（`q` → `keyword`，与后端 `/gallery/search` 对齐）导致搜索无效的问题；进入图库视图自动加载列表（缩略图走 `data_url`，不依赖外部路径）；支持按关键字/类型/仅收藏筛选、点击放大预览（含 prompt、参数、LoRA、种子、来源等元信息）、收藏/取消收藏、删除（带确认弹窗）。
  - 后端新增 `/schema` 接口返回 `_conf_schema.json`；`/gallery/search` 增加 `offset` 分页参数（image_store.search 同步支持）。

## v2.2.3

- **移除 WebUI 中多余的「调试」模块（连 ComfyUI 服务器的功能）**。该模块是之前自行添加的画蛇添足：列出 ComfyUI 服务器、`/servers` 与 `/test_server` 路由、以及「测试连接」按钮。ComfyUI 连通性本应由插件自身运行验证，不应在控制台里让人手动连服务器。现前端删除调试视图/导航/相关代码，后端删除 `servers`、`test_server` 两个路由与 `aiohttp` 依赖。控制台仅保留配置、日志、图库三块。

## v2.2.2

- **修复 WebUI「正在连接…/桥接不可用/部分数据加载失败/按钮全部点不了」的根因**。
  - 后端路由前缀错误：`webui_api.py` 的 `register_web_api` 路径里多写了插件名（`/{plugin_name}/page`），而 AstrBot 的 bridge 会自动拼接 `/api/plugins/extensions/<plugin_name>/` 前缀，导致最终请求变成 `/api/.../<plugin_name>/<plugin_name>/page/...` 双重插件名而 404。现改为相对路径 `/page`，前端 `page/config` 即可正确命中。
  - 前端 bridge 获取时机错误：原代码在脚本顶层 `const bridge = window.AstrBotPluginPage` 立即取值，但 AstrBot 的桥接对象由宿主异步注入，若脚本先于注入执行，`bridge` 即为 `undefined`，直接走进"桥接不可用"死路、所有按钮绑不上事件。现改为 `getBridge()` 轮询等待（最多约 8 秒），对齐伴侣插件 / get_px 的标准写法。
  - 加载过程中状态栏明确显示"正在连接…"，就绪后切换为"已就绪"。

## v2.2.1

- **修复 WebUI 所有按钮无响应、数据不加载的问题**。根因是 `app.js` 中 `apiGet`/`apiPost` 的 endpoint 缺少 `page/` 前缀。后端 `webui_api.py` 的路由注册在 `/{plugin_name}/page/` 下，但前端直接调用 `bridge.apiGet("config")` 时，AstrBot bridge 拼出的完整路径是 `/api/plugins/extensions/<plugin_name>/config`，与实际路由 `/api/plugins/extensions/<plugin_name>/page/config` 不匹配。本版在 `apiGet`/`apiPost` 中自动补齐 `page/` 前缀，对齐后端路由。
- 同时简化了 bridge 返回值的解包逻辑，去掉冗余的 `status` 字段判断（bridge 已自动对 `{status:"ok",data}` 解包为 `data`，对错误自动 reject）。

## v2.2.0

- **WebUI 控制台全面重写**，对齐 `astrbot_plugin_get_px`（画境拾珍）的专业风格：
  - **三文件分离**：`index.html`（页面骨架）+ `styles.css`（完整样式）+ `app.js`（业务逻辑），告别单文件内联的混乱。
  - **CSS 变量体系**：支持亮色/暗色自动切换（`prefers-color-scheme: dark`），统一色调与间距。
  - **原生 `<dialog>` 弹窗**：确认对话框与图片放大预览均使用 `<dialog>` 元素，不再使用简陋的 `alert`/`confirm`。
  - **Toast 通知**：操作反馈改为底部浮动 Toast，不打断用户操作流。
  - **IIFE 模块化**：`app.js` 采用 IIFE + `state` 对象管理模式，与参考插件一致的编码风格。
  - **`_page.json`**：新增页面元信息（title + description），AstrBot Dashboard 可读取显示友好标题。
  - 四个功能模块保持不变：配置 / 日志 / 调试 / 图库，但 UI 焕然一新。

## v2.1.7

- **修复 zip 包路径分隔符导致 Linux/Docker 下目录变文件名的严重 bug**。`Compress-Archive` 在 Windows 上打包时使用反斜杠 `\` 作为 zip 内部路径分隔符，而 AstrBot 运行在 Linux 上时，Python 的 `zipfile.extractall()` 原样保留 `\`，导致 `pages\anima-console\index.html` 被解压为单个文件而非目录层级。本版重写 `build_zip.ps1`，弃用 `Compress-Archive`，改用 `.NET ZipFile` 手动构建 zip，强制所有路径使用正斜杠 `/`，确保跨平台兼容。
- 此前因此 bug 导致 `pages/` 目录在 Linux 部署时完全不可用，Dashboard 自然无法发现 WebUI 入口。

## v2.1.6

- **修复 `ModuleNotFoundError: No module named 'image_store'/'webui_api'`**。AstrBot 以 package 方式加载插件时，`sys.path` 不包含插件目录，导致 `from image_store import ImageStore` 这种绝对导入失败。改为优先使用 `from .image_store import ImageStore` 相对导入，回退兼容绝对导入。受影响的导入：`ImageStore`、`SRC_REF`、`SRC_USER`、`SRC_GEN`、`LOG_BUFFER`、`register_web_api`。
- 该 bug 导致图库（`ImageStore`）和 WebUI 控制台（`webui_api`）在插件加载时全部初始化失败，是之前"看不到画廊目录 + 看不到 WebUI 入口"的真正根因之一。

## v2.1.5

- **图库初始化增加目录路径日志**：插件启动时打印 `data_dir`、`gallery/`、`refs/`、`gallery.db` 的完整路径，方便排查"看不到画廊目录"的问题。
- 图库目录 `gallery/YYYY-MM/` 与 `refs/YYYY-MM/` 在 `ImageStore.__init__` 中通过 `mkdir(parents=True, exist_ok=True)` 自动创建，**无需提前在仓库里放置空目录**。若重启后仍无目录，请检查 AstrBot 日志中 `[init] 图库已就绪` 或 `[init] 图库初始化失败` 的输出，定位具体原因。

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
