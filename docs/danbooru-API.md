# API 文档（Danbooru 标签语义搜索）

本仓库提供**两套对外接口**，均由 `ui_nicegui.py` 统一启动并挂载：

| 接口类型 | 挂载路径 | 说明 |
|----------|----------|------|
| HTTP REST API | `/api/*` | 基于 FastAPI（`api_fastapi.py`），供脚本 / 第三方调用 |
| MCP（模型上下文协议） | `/mcp/mcp` | 基于 `FastMCP`，供 AI Agent / 大模型工具调用 |
| Swagger 交互文档 | `/api/docs` | FastAPI 自动生成的在线接口文档（OpenAPI） |

> 默认服务地址：`http://127.0.0.1:11111`（由 `platform_utils.get_host_port()` 决定）。
> 自托管环境下接口**无需认证**。

---

## 1. 通用说明

### 1.1 数据格式
- 请求与响应均为 `application/json`（MCP 工具以 JSON 字符串返回）。
- 所有返回中的标签（tag）均使用 **Danbooru canonical 命名**：小写、空格替换为下划线，例如 `white_serafuku`、`1girl`。

### 1.2 引擎预热
模型权重（bge-m3，数 GB）在**首次请求前**由后台任务异步加载（见 `ui_nicegui.py` 的 `on_startup`）。冷启动后前几次请求可能较慢；`GET /api/health` 的 `loaded` 字段可确认模型是否已就绪。

### 1.3 标签自动纠错
`/api/related` 与 `/api/artists` 会对传入的标签做拼写纠错（编辑距离匹配）。若发生纠错，响应中会附带：
```json
{
  "correction_note": "标签拼写错误，已经纠错: selafuku → sailor_uniform",
  "corrections": { "selafuku": "sailor_uniform" }
}
```

### 1.4 匹配层（layers）与类别（categories）
- **匹配层 `target_layers`**：`'英文'` / `'中文扩展词'` / `'释义'` / `'中文核心词'` / `'artist'`。
- **类别 `target_categories`**：`'General'` / `'Artist'` / `'Copyright'` / `'Character'` / `'Meta'`。

---

## 2. HTTP REST API

基类路径：`/api`

### 2.1 `POST /api/search` — 语义标签搜索

通过自然语言（建议中文）检索最匹配的 Danbooru 标签，返回可直接用于提示词的逗号分隔字符串。

**请求体（`application/json`）**

| 字段 | 类型 | 默认值 | 约束 / 说明 |
|------|------|--------|-------------|
| `query` | string | —（必填） | 自然语言画面描述，如「白色水手服的少女在雨中奔跑」 |
| `top_k` | int | `5` | `1 ≤ top_k ≤ 50`，语义召回候选数 |
| `limit` | int | `80` | `1 ≤ limit ≤ 500`，最终返回标签条数 |
| `popularity_weight` | float | `0.15` | `0.0 ≤ w ≤ 1.0`，热度权重（越高越偏向热门标签） |
| `show_nsfw` | bool | `true` | 是否包含 NSFW 标签 |
| `use_segmentation` | bool | `true` | 是否启用分词（多概念拆分） |
| `target_layers` | string[] | `['英文','中文扩展词','释义','中文核心词']` | 参与匹配的语义层，可加 `'artist'` |
| `target_categories` | string[] | `['General','Character','Copyright']` | 限定搜索类别 |
| `group_mode` | string | `'off'` | `'off'` / `'expand'`（扩展候选）/ `'diverse'`（多样性去重） |
| `max_per_group` | int | `2` | 仅 `diverse` 模式生效，每组最多保留条数 |

**响应（`200`）**

| 字段 | 类型 | 说明 |
|------|------|------|
| `tags_all` | string | 逗号分隔的标签串（含 NSFW，当 `show_nsfw=true`） |
| `tags_sfw` | string | 逗号分隔的标签串（仅 SFW） |
| `results` | object[] | 详细结果列表，见下方 `TagOut` |
| `keywords` | string[] | 从查询中提取的关键词 |

`TagOut` 单项结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `tag` | string | canonical 标签名 |
| `cn_name` | string | 中文名 |
| `category` | string | 类别（General / Character / ...） |
| `nsfw` | string | `'0'` 或 `'1'` |
| `final_score` | float | 综合得分 |
| `semantic_score` | float | 语义得分 |
| `count` | int | 该标签在 Danbooru 的出现次数 |
| `source` | string | 命中来源（语义 / 共现等） |
| `layer` | string | 命中的匹配层 |
| `wiki` | string | wiki 说明（可能为空） |
| `artist_top_tags` | string[] | 仅 artist 层命中时返回，该画师常见标签 |

**错误**
- `503`：搜索超时（120s），detail 为 `"搜索超时（120s），请简化查询或稍后重试"`。

**示例**
```bash
curl -X POST http://127.0.0.1:11111/api/search \
  -H "Content-Type: application/json" \
  -d '{"query":"白色水手服的少女在雨中奔跑","top_k":5,"limit":20,"show_nsfw":false}'
```

---

### 2.2 `POST /api/related` — 关联标签推荐

基于 NPMI **共现表**，给定一组种子标签，返回经常与之共同出现的互补标签。

**请求体**

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `tags` | string[] | —（必填） | canonical 标签列表，如 `["white_serafuku","sailor_collar"]` |
| `limit` | int | `50` | `1 ≤ limit ≤ 200` |
| `show_nsfw` | bool | `true` | 是否包含 NSFW |

**响应（`200`）**
```json
{
  "results": [
    { "tag": "sailor_collar", "cn_name": "水手领", "sources": ["white_serafuku"], "wiki": "" }
  ]
}
```
- 若所有标签均不存在：`{ "error": "所有传入的标签均不存在于标签表中", "invalid_tags": [...] }`（带候选时附 `candidates`）。
- 发生纠错时附加 `correction_note` / `corrections`（见 §1.3）。

**示例**
```bash
curl -X POST http://127.0.0.1:11111/api/related \
  -H "Content-Type: application/json" \
  -d '{"tags":["white_serafuku","sailor_collar"],"limit":20}'
```

---

### 2.3 `POST /api/artists` — 画师推荐

基于标签-画师 **NPMI 共现数据**，推荐擅长绘制给定标签组合的画师。

**请求体**

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `tags` | string[] | —（必填，非空） | canonical 标签列表 |
| `limit` | int | `30` | `1 ≤ limit ≤ 100` |
| `min_cooc` | int | `3` | `1 ≤ min_cooc ≤ 100`，单 `(tag, artist)` 对最小共现次数 |
| `show_nsfw` | bool | `true` | 是否包含 NSFW 画师数据 |

**响应（`200`）**
```json
{
  "results": [
    {
      "artist": "mika_pikazo",
      "cooc_count": 1234,
      "post_count": 567,
      "sources": ["1girl","blue_hair"],
      "top_tags": ["1girl","blue_hair","school_uniform"]
    }
  ]
}
```
- `tags` 为空或全无效时返回 `error` 说明。

**示例**
```bash
curl -X POST http://127.0.0.1:11111/api/artists \
  -H "Content-Type: application/json" \
  -d '{"tags":["1girl","blue_hair","school_uniform"],"limit":10}'
```

---

### 2.4 `GET /api/health` — 健康检查

**响应（`200`）**
```json
{ "status": "ok", "loaded": true }
```
- `loaded`：`true` 表示语义模型已加载完成，可正常响应搜索请求。

**示例**
```bash
curl http://127.0.0.1:11111/api/health
```

---

## 3. MCP 接口

接入地址：`http://127.0.0.1:11111/mcp/mcp`
传输方式：Streamable HTTP（`mcp.streamable_http_app()`）
协议：Model Context Protocol，供支持 MCP 的客户端（如 Claude Desktop、本地 Agent）接入。

可用工具（`@mcp.tool`）如下：

### 3.1 `search_tags`
自然语言搜索视觉 / 角色 / 作品标签，返回可直接用于提示词的 tag 列表。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `query` | string | — | 自然语言画面描述（推荐中文） |
| `search_mode` | string | `'full_scene'` | `'full_scene'`（默认，具体场景）/ `'concept_explore'`（概念浏览）/ `'subject_describe'`（单一概念）/ `'precise_lookup'`（精确查词/纠错） |
| `category` | string | `'all'` | `'all'` / `'general'` / `'character'` / `'copyright'` |
| `show_nsfw` | bool | `true` | 是否包含 NSFW |
| `include_wiki` | bool | `false` | 结果是否附带 wiki 说明 |

**返回**：JSON 字符串，含 `prompt`（逗号分隔标签）、`keywords`、`results`（每项 `tag` / `cn_name`，`include_wiki=true` 时含 `wiki`）。检测到英文查询时会附带 `hint` 建议使用中文。

### 3.2 `get_related_tags`
基于 NPMI 共现的关联标签推荐（仅 General / Character / Copyright，不支持画师与 meta 标签）。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `tags` | string[] | — | canonical 标签列表（下划线） |
| `limit` | int | `50` | 最多返回数量 |
| `show_nsfw` | bool | `true` | 是否包含 NSFW |
| `include_wiki` | bool | `false` | 是否附带 wiki |

**返回**：JSON 字符串，`results` 按聚合 NPMI 分数降序，每项含 `tag` / `cn_name` / `sources`。

### 3.3 `get_artist_recommendations`
tag → 画师推荐（按 NPMI 排序）。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `tags` | string[] | — | canonical 标签列表（**非画师名**） |
| `limit` | int | `30` | 最多返回画师数 |
| `min_cooc` | int | `3` | 单 `(tag, artist)` 最小共现次数 |
| `show_nsfw` | bool | `true` | 是否包含 NSFW 画师数据 |

**返回**：JSON 字符串，`results` 含 `artist` / `cooc_count` / `post_count` / `sources` / `top_tags`（该画师前 10 常见标签，带中文名）。

### 3.4 `get_artist_profile`
查询单个画师的常见共现标签（画师 → 标签）。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `artist_name` | string | — | 画师名或 Danbooru 画师 tag（允许大小写/空格差异，自动规范化） |
| `top_n` | int | `20` | 最多返回常见标签数（`1 ≤ top_n ≤ 100`） |
| `show_nsfw` | bool | `true` | 是否包含 NSFW 常见标签 |

**返回**：JSON 字符串，含 `artist`（规范化后）、`input`、`matched_by`、`post_count`、`top_tags`、`note`。未找到唯一画师时返回 `artist_not_found` 与候选列表。

### 3.5 `get_anima_format`
返回 **Anima** 文生图模型（Hybrid 混合提示词）的完整格式规范（长文本 Markdown）。当提及「Anima 提示词 / 格式」时调用。

### 3.6 `get_newbie_format`
返回 **NewBie** 文生图模型的 **XML** 格式提示词规范（长文本）。当提及「NewBie 提示词 / 格式」时调用。

---

## 4. 其他路由

| 方法 & 路径 | 说明 |
|-------------|------|
| `GET /robots.txt` | 爬虫规则：禁止 `/api/`、`/_nicegui/`、`/socket.io/` |
| `HEAD /` | 根路径存活探测，返回空体 `200` |

---

## 5. 启动方式

### 5.1 一体化启动（推荐）
直接运行 UI 服务，REST API 与 MCP 会一并挂载：
```bash
uv run python ui_nicegui.py
```
启动后：
- Web UI：`http://127.0.0.1:11111/`
- REST API：`/api/*`
- Swagger 文档：`/api/docs`
- MCP：`/mcp/mcp`

### 5.2 仅启动 REST API（独立 FastAPI）
```bash
uvicorn api_fastapi:app --host 0.0.0.0 --port 8000
```
此时端点路径为 `/search`、`/related`、`/artists`、`/health`（不带 `/api` 前缀），Swagger 文档在 `/docs`。

---

## 6. 核心数据模型参考（`core/models.py`）

### `SearchRequest`
`query: str`、`top_k: int=5`、`limit: int=80`、`popularity_weight: float=0.15`、
`show_nsfw: bool=True`、`use_segmentation: bool=True`、
`target_layers: list[str]=['英文','中文扩展词','释义','中文核心词']`、
`target_categories: list[str]=['General','Character','Copyright']`、
`group_mode: str='off'`（`'off'`/`'expand'`/`'diverse'`）、`max_per_group: int=2`。

### `SearchResponse`
`tags_all: str`、`tags_sfw: str`、`results: list[TagResult]`、`keywords: list[str]`、
`segments: list[str]`（分词片段）、`cached_queries: list[str]`（命中缓存的查询）。

### `TagResult`（搜索结果单项）
`tag`、`cn_name`、`category`、`nsfw`、`final_score`、`semantic_score`、`count`、`source`、`layer`、
`wiki: str=''`、`artist_top_tags: list[str]=[]`。

### `RelatedTag`（关联推荐单项）
`tag`、`cn_name`、`category`、`nsfw`、`cooc_count: int`、`cooc_score: float`、
`sources: list[str]`、`post_count: int=0`、`wiki: str=''`。

### `ArtistResult`（画师结果单项）
`artist`、`score: float`、`cooc_count: int`、`post_count: int`、`sources: list[str]`、`hit_count: int`。
