# 多平台生图方案（Multi-Platform Image Generation）

> 状态：方案已确认，实施中（第一期）。
> 本文是唯一方案来源，实施/续会话以本文为准。最后更新：2026-09-06。

## 1. 背景与目标

当前插件强依赖 ComfyUI（`comfyui_client.py` 是 ComfyUI 专属协议），服务器失联即无法出图。
目标：**在现有插件内扩展多平台生图能力**（不做新插件、不大改架构），ComfyUI 降级为平台之一。
第一期接入 NAI / OpenAI（兼容中转）/ 自定义 HTTP 三类平台，后续可继续扩展聚合平台等。

用户已确认的关键决策：
- 不做自动降级，通过 **WebUI「生图平台」页 / 配置** 手动切换当前平台；
- WebUI **新开独立页面**（方案 B），数据存自管 JSON；
- 第三方平台**本来就不支持** LoRA/工作流（不是一个品类），无需「能力缺失」特殊交互，
  分流时自动忽略 ComfyUI 专属参数并在返回文案提示即可；
- 数据库：**改造原有 images 表（加列）**，不新建表——图库是统一资产，检索/收藏/gallery 工具全部复用。

## 2. 平台模型（统一抽象）

### 2.1 三类平台 type

| type | 说明 | 接入协议 |
|---|---|---|
| `nai` | NovelAI（官方或中转站） | 官方 `POST https://image.novelai.net/ai/generate-image`（Bearer 持久 token，响应为 zip 内嵌 png）；中转站（如 nai.sta1n.cn）支持 `via_middle_station` 开关走 GET `/generate`（移植参考插件 `_References/astrbot_plugin_nai_image` 逻辑） |
| `openai` | OpenAI 官方 / 兼容中转（newapi、聚合平台） | `POST {base_url}/v1/images/generations`；图生图预留 `/v1/images/edits`（第二期） |
| `custom` | 自定义 HTTP 接口（实验） | URL + JSON 模板渲染 + 响应提取（见 2.4） |

ComfyUI **不进** platforms 数组：`active_platform` 为 `"comfyui"` 时走现有 `_do_draw` 主流程（一行不改），
ComfyUI 服务器管理仍在原配置页 `comfyui_servers`。

### 2.2 存储：`data_dir/image_platforms.json`

```json
{
  "active_platform": "comfyui",
  "platforms": [
    {
      "id": "uuid4",
      "type": "nai",
      "name": "显示名",
      "enabled": true,
      "base_url": "https://image.novelai.net",
      "api_key": "...",
      "model": "nai-diffusion-4-5-full",
      "via_middle_station": false,
      "defaults": {
        "size": "portrait",
        "count": 1,
        "steps": 28,
        "scale": 6,
        "cfg_rescale": 0.3,
        "sampler": "k_dpmpp_2m_sde",
        "noise_schedule": "karras",
        "negative": ""
      }
    },
    {
      "id": "uuid4",
      "type": "openai",
      "name": "newapi 中转",
      "enabled": true,
      "base_url": "https://xxx",
      "api_key": "sk-xxx",
      "model": "gpt-image-1",
      "size": "1024x1024",
      "quality": "",
      "count": 1,
      "negative": ""
    },
    {
      "id": "uuid4",
      "type": "custom",
      "name": "某某聚合站",
      "enabled": false,
      "url": "https://xxx/api/gen",
      "method": "POST",
      "body_template": "{\"prompt\":\"{{prompt}}\",\"negative\":\"{{negative}}\",\"width\":{{width}},\"height\":{{height}},\"seed\":{{seed}}}",
      "resp_type": "b64_json",
      "resp_path": "data[0].b64_json",
      "headers": {"Authorization": "Bearer {{api_key}}"},
      "api_key": "",
      "defaults": {"negative": "", "count": 1}
    }
  ],
  "artist_presets": [
    {"id": "uuid4", "name": "韩漫小清新", "content": "best quality, amazing quality, ...", "enabled": true}
  ],
  "negative_presets": [
    {"id": "uuid4", "name": "通用低质", "content": "lowres, bad anatomy, ...", "enabled": true}
  ]
}
```

- `artist_presets` / `negative_presets` 为**跨平台共享预设区**（NAI 画师串、ComfyUI/custom 的负面词模板都可引用）。
- 启用规则（与 `comfyui_servers` 一致）：同一 type 下同一时间只启用一个服务；生图取启用的；未启用用第一个；多启用报错。

### 2.3 尺寸档位（NAI）

`portrait`（832x1216）/ `landscape`（1216x832）/ `square`（1024x1024）/ `2K竖图` 等
（2K=1536x2304 / 2304x1536，4K=2048x3072 / 3072x2048，按 NAI 官方档位）。也接受自由 `宽x高`。

### 2.4 custom 模板渲染

- 占位符：`{{prompt}}` `{{negative}}` `{{width}}` `{{height}}` `{{seed}}` `{{model}}` `{{api_key}}` `{{artist}}`；
- 渲染后 `json.loads` 校验合法；请求按 `method` 发送，`headers` 同样渲染（api_key 占位）；
- 响应提取：`resp_type=b64_json` → 按 `resp_path`（如 `data[0].b64_json`，简单点路径解析支持 `a.b.0.c`）取 base64；
  `url` → 下载该 URL；`binary` → 直接取响应字节。

## 3. WebUI「生图平台」页（方案 B，已确认）

### 3.1 前端

- 路由 `/platforms`，导航名「生图平台」，位置挨着「LoRA」；`webui-src/src/views/PlatformsView.vue`；
- 布局：
  - 顶部条：「当前使用平台」切换（下拉/单选：ComfyUI / 各启用平台）+ ComfyUI 提示「服务器在原配置页管理」；
  - 平台卡片列表（三类字段不同，卡片框架/启用单选/编辑/删除同一套）：
    - nai：地址、Token（密码框）、模型（下拉+自定义）、via_middle_station 开关、默认参数折叠面板；
    - openai：地址、Key、模型（可自由输入）、尺寸、张数、负面词；
    - custom：URL、方法、请求体模板（多行文本+占位符说明）、响应类型/提取路径、Headers；
  - 「+ 添加平台」按钮 → 按 type 出对应表单；
  - 预设区（表格+行内编辑，仿 LoRA 页）：画师串预设、负面词模板；
  - （第二期）NAI 测试面板（prompt → 预览）。
- API（webui_api.py 注册 + standalone_webui.py 桥接，沿用现有模式，参考 /lora 系列做法）：
  - `GET  /platforms` → 全量配置 JSON；
  - `POST /platforms/save` → 整包保存（前端整包读写，简单可靠）；
  - `POST /platforms/test` → 测试生图（第二期）。

### 3.2 后端平台管理模块

- 新文件 `platform_store.py`：`PlatformStore` 类（load/save/active_platform/platforms CRUD 辅助/
  pick_enabled(type)/渲染 custom 模板等纯逻辑，不碰 aiohttp）；
- `webui_api.py` / `standalone_webui.py` 注册路由并桥接（照 _lora 系列的双侧注册模式）。

## 4. 数据库改动（images 表加列，已确认不新建表）

`image_store.py`：
1. `CREATE TABLE images` 增加：
   - `platform TEXT NOT NULL DEFAULT 'comfyui'`（生图平台：comfyui/nai/openai/custom）
   - `model TEXT DEFAULT NULL`（NAI 模型名 / openai 模型名 / ComfyUI checkpoint，暂可空）
   - `negative TEXT DEFAULT NULL`（负面提示词——**历史疏漏补齐，ComfyUI 也写**）
   - `extra TEXT DEFAULT NULL`（JSON：平台专有参数，NAI 的 steps/scale/sampler/noise_schedule 等，避免每平台加稀疏列）
2. 旧库迁移：`ALTER TABLE images ADD COLUMN ...` 列表（183-207 行处）同样追加 4 列；
3. `archive_image()` 签名加 `platform/model/negative/extra` 参数；去重补齐 UPDATE 与 INSERT 同步加列；
4. `_row_to_dict()` 返回加 `platform/model/negative/extra` 字段（extra 反序列化为 dict 或原样字符串）；
5. `add_failed_record()` 同步写 platform（失败记录也标注平台）。

`main.py`：ComfyUI 归档调用（3774 附近）传 `platform="comfyui"`、`negative=negative`。

前端 `ImageViewer.vue`：大图详情加「平台」「模型」「负面词」行（interface 同步加字段）。

## 5. 生图分流（_do_draw）

- `_do_draw` 入口处解析目标平台：`active_platform = platform_store.active_platform`；
  （`llm_draw` 新增 `platform` 参数可临时指定；`/draw` 指令后续可加 `--platform`，第二期）
- `comfyui` → 现有主流程一行不动；
- 非 comfyui → 走独立协程 `_do_draw_nai_style(event, ..., platform_cfg)`（独立 async generator，
  **不塞进主流程缝隙**，主流程零风险）：
  1. 提示词预处理：可选 LLM 转译为 NAI tags（复用现有 `_resolve_translate_provider_id()` 翻译链路，
     提示词含中文时调用；openai/custom 平台跳过转译直接透传）；
  2. 拼装平台请求（画家串预设追加、负面词模板合并）→ 调用对应 client；
  3. 返回 bytes → 写 `self.temp_dir` 临时文件；
  4. **复用同一套归档**（`archive_image(platform=..., model=..., negative=..., extra={...})`）
     → NSFW 群聊检测 → 图文消息/caption → yield 发送（与 ComfyUI 链路同构，代码少量复制、逻辑一致）；
  5. 失败走 `_record_failed(platform=...)`；
- ComfyUI 专属参数（loras/workflow/denoise/宽高比例）对第三方平台自动忽略，
  发送文案附带一句「由 NAI/OpenAI 平台生成（不支持 LoRA/工作流）」；caption 机制保留。

## 6. 客户端实现

- 新文件 `nai_client.py`：
  - `class NaiClient`：官方 API（zip 解包拿 png）+ `via_middle_station`（GET 中转）；
  - `class OpenAIImageClient`：`/v1/images/generations`（b64_json / url 两种响应）；
  - `class CustomHttpClient`：模板渲染 + 提取；
  - 统一入口 `async def generate(cfg: dict, prompt, negative, w, h, seed, count, extra) -> bytes`；
  - 用 aiohttp（插件已有依赖），超时/重试对齐 comfyui_client 风格。
- 参考：`_References/astrbot_plugin_nai_image`（只读参考，禁止修改）——
  请求参数格式、档位尺寸、采样器枚举、重试退避（408/429/502/503/504 按 2/4/8s）。

## 7. LLM 工具 / 指令集成

- `comfyui_draw` 加 `platform(string)` 参数（可选）：「不传=用默认平台；传 comfyui/nai/openai/custom 或
  平台名=临时指定」。工具描述说明各平台能力差异（NAI 吃 Danbooru 标签、openai 吃自然语言、都不支持 LoRA）；
- 工具描述补充：当配置了非 ComfyUI 平台且 ComfyUI 未配置/失联时，LLM 可主动选择可用平台；
- `/image` 独立指令第一期不加（避免与 /draw 冲突），第二期再议。

## 8. 第一期实施顺序（checklist）

1. [x] 方案文档（本文）
2. [x] `image_store.py`：加 4 列（platform/model/negative/extra）+ archive_image/_row_to_dict/add_failed_record 扩展
3. [x] `main.py`：ComfyUI 归档补 `platform/negative`
4. [x] `platform_store.py`：平台配置管理（JSON 读写 + 模板渲染 + pick_enabled）
5. [x] `nai_client.py`：NaiClient / OpenAIImageClient / CustomHttpClient
6. [x] `webui_api.py` + `standalone_webui.py`：/platforms GET+save 路由（双侧）
7. [x] `main.py`：_do_draw 分流 + `_do_draw_nai_style` + 归档/失败记录带 platform
8. [x] `comfyui_draw` 工具加 platform 参数 + 描述
9. [x] `webui-src`：PlatformsView.vue + 路由 + 导航；ImageViewer.vue 加平台/模型/负面词行
10. [x] 版本 v5.8.0 + CHANGELOG + build_webui + build_zip + commit + push（commit 03ad4e5，2026-09-06）

版本规则：v5.7.7 → **v5.8.0**（新功能系列，minor 进位）。

## 9. 第二期及以后 backlog

- NAI vibe 参考 / img2img（/v1/images/edits）/ director 精准参考（多参考图，参考插件 v2.4/v2.5 逻辑）；
- NAI 多角色坐标（--char / characterPrompts）；
- 反推（多模态 LLM → NAI tags）；
- 服装缓存池；
- NAI 测试面板（平台页内置调试）；
- ComfyUI 服务器桥接进平台页统一展示；
- `/image` 指令、--platform 参数、平台级配额统计。

## 10. 风险与边界

- 第三方平台无 LoRA/工作流概念：分流自动忽略，返回文案明示来源平台，图库 platform 列区分；
- NAI 官方 API 响应是 zip：用 zipfile 从内存解包 png；
- custom 模板渲染失败/提取失败：明确报错返回，绝不假装出图；
- API Key 存储：自管 JSON 在 data_dir 下（与 AstrBot 配置同等安全域），WebUI 返回时 key 脱敏可选（第一期明文回传，便于编辑；与 lora 封面等现有做法一致）；
- 兼容性：旧库无新列由迁移自动补齐；active_platform 缺省 comfyui，老用户行为零变化。

## 11. 关键代码位置索引（续会话用）

- `image_store.py`：images 建表 ~149-180；迁移列表 ~183-207；archive_image ~550；去重补齐 UPDATE ~646-661；
  INSERT ~704-727；add_failed_record ~754；_row_to_dict ~948；
- `main.py`：_do_draw ~2892（签名含 trigger_words）；归档调用 ~3774；发送/NSFW ~3855-3923；
  _record_failed ~2745；llm_draw ~6871（签名）；_do_draw 调用处 ~7577；_cfg ~986；
  template_key 自动补丁 ~913（新增 image_platforms 相关列表无需此补丁——自管 JSON 不走 AstrBot 配置）；
- `_conf_schema.json`：comfyui_servers template_list 范式 ~1-44（本期不用改 schema——平台配置走自管 JSON）；
- `webui_api.py`：路由注册 ~1809；gallery 系列接口范式 ~1119-1330；
- `standalone_webui.py`：/api 分发 ~430-812（_dispatch 风格）；
- `webui-src/src/views/`：LoRA 页（资源列表管理范式）；router 在 `webui-src/src/App.tsx 或 router.ts`；
- `ImageViewer.vue`：interface ~97-130；信息行 ~63-81。
