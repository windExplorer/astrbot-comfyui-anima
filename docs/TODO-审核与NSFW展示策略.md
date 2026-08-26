# TODO：审核与 NSFW 展示策略（插件 WebUI 新功能）

> 状态：方案设计（已多轮讨论收敛，待实现）
> 范围：**本插件 WebUI 侧**功能（图库 / 分享站 / 审核页）
> 关联：远程检测服务部署见 `nsfw-detection-service.md`（本功能消费其 `/detect` 返回的 score/level）

---

## 1. 背景与问题

当前插件内置检测为 **Yahoo OpenNSFW（opennsfw-onnx，ResNet-50）**，只有一个二分类分数 `P(nsfw)`，配合单阈值（默认 0.5）一刀切判定，两类误判严重：

1. **18R 没被标记 / 置信度低**：OpenNSFW 对动漫艺术化、半遮、擦边内容几乎不敏感，分数常卡 0.3~0.5，低于阈值不判 NSFW。
2. **正常图被误标**：皮肤占比高、比基尼、健身、写实人体、暖色调等易被给到 0.5+，误杀正常图。

根因：**单模型（且本地模型不准）+ 单阈值 + 无人工兜底**。

解决方向（已与远程检测服务方案配合定稿）：
- 引入**远程 CLIP ViT-L/14 检测服务**作主判（部署见 `nsfw-detection-service.md`）；
- 本地 OpenNSFW 仅作**参考 / 分歧预警**，**不参与任何判定门槛**；
- 三级**自动判定**（高/低置信直接定）+ 中间带与分歧**进人工复核**；
- 图库 / 分享站**只展示已审核图**，NSFW 展示受**全局 / 用户级策略**约束。

---

## 2. 判定模型（远程主判 + 本地参考）

### 2.1 角色定位（已定）

- **远程 CLIP 分（`nsfw_remote_score`）是主判**：决定自动安全 / 自动 NSFW / 进复核。
- **本地 OpenNSFW 分（`nsfw_local_score`）仅参考**：持久化存储供审核员对照"两模型差多少"；与远程分差距超阈值 → 标记分歧，强制进复核。**绝不用本地分做放行/判定门槛**（本地不准才换强模型，不能让它回头当裁判）。

### 2.2 三级自动判定（以远程分为准）

```
远程 score <  LOW(0.35)  → 自动判 安全 (review_status=1, review_auto=1, level=safe)，不进复核
LOW ~ HIGH(0.35~0.8)     → 进 人工复核 (review_status=0)，不自动定，图库/分享站暂不展示
远程 score >= HIGH(0.8)  → 自动判 NSFW (review_status=2, review_auto=1)，按 show_nsfw 策略模糊；仍可人工推翻
```

### 2.3 分歧

- 本地与远程分差 `> diverge(0.3)` → `diverge=1`，**强制进人工复核**（优先级最高），捕捉两模型互相矛盾的可疑图。

### 2.4 最终真值收敛（写回 `images.nsfw` / `nsfw_blur`）

优先级：**人工结论（`review_status` 且非 auto） > 远程自动判定 > 本地分**（本方案不采用本地兜底判定）。
- 人工复核后：结论为最终真相，覆盖机器。
- 未审核时：展示/过滤使用远程 `level`（自动安全/自动 NSFW），待复核的不展示。

---

## 3. 双分数存储（DB 字段）

复用 `images` 表现有：`nsfw INTEGER`、`nsfw_score REAL`（升级后建议等于 `nsfw_remote_score`）、`nsfw_blur INTEGER`、`nsfw_checked INTEGER`。

新增：
```sql
ALTER TABLE images ADD COLUMN nsfw_local_score  REAL DEFAULT NULL;  -- 本地 OpenNSFW 分（仅参考/分歧）
ALTER TABLE images ADD COLUMN nsfw_remote_score REAL DEFAULT NULL;  -- 远程 CLIP 分（主判）
ALTER TABLE images ADD COLUMN nsfw_remote_level TEXT DEFAULT NULL;  -- safe/review/nsfw
ALTER TABLE images ADD COLUMN review_status     INTEGER DEFAULT 0;  -- 0待复核 1安全 2 NSFW
ALTER TABLE images ADD COLUMN review_auto       INTEGER DEFAULT 0;  -- 1=机器自动审 0=人工审
ALTER TABLE images ADD COLUMN review_level      TEXT DEFAULT NULL;  -- 评级 safe/questionable/explicit
ALTER TABLE images ADD COLUMN review_by         TEXT DEFAULT NULL;  -- 'auto' 或 审核员
ALTER TABLE images ADD COLUMN review_at         REAL DEFAULT NULL;
ALTER TABLE images ADD COLUMN diverge           INTEGER DEFAULT 0;  -- 1=本地/远程分歧
```
> 两种检测分数都存，互不覆盖，便于复核与复盘。

---

## 4. 插件端配置

```yaml
nsfw:
  enabled: true
  threshold: 0.5            # 仅本地 OpenNSFW 对照时使用
  blur_default: true
  remote:
    enabled: false          # 开启则走远程检测服务（主判）
    url: "http://localhost:8890"
    timeout: 5              # 秒
    low: 0.35               # 安全自动放行门槛（远程分 < low 才放行）
    high: 0.8               # NSFW 自动判定门槛
    diverge: 0.3            # 本地/远程分差超此值 → 分歧，强制进复核
  show_nsfw:
    global: "blur"          # never(不展示) / blur(展示但模糊) / original(原图)
    per_user: {}            # user_id -> never/blur/original，覆盖 global
```

---

## 5. 插件端调用与降级（`nsfw_detector.py` / `check_nsfw`）

1. **双分数采集**：本地 `get_detector().detect()` → `nsfw_local_score`；远程（若启用且 `/healthz` OK）`POST /detect` → `nsfw_remote_score` + `nsfw_remote_level`。
2. 按 §2.2 / §2.3 定 `review_status`(auto) / `review_level` / `diverge` 写回 `images`。
3. **降级（已定）**：远程不可用 / 未配置 / 超时 → 标记 `review_status=0`（待复核），**不回退**本地 OpenNSFW 单模型判定（避免远程不可信时瞎判）。本地分仍存作参考。
4. 保持现有"依赖缺失不阻塞主流程"降级风格。

---

## 6. 展示策略（图库 / 分享站只展示已审核）

### 6.1 可见性规则

| 机器/人工结论 | review_status | 图库 / 分享站展示 |
|---|---|---|
| 自动安全 | 1 (auto) | ✅ 展示 |
| 自动 NSFW | 2 (auto) | 按 `show_nsfw` 策略：never 不展示 / blur 模糊 / original 原图 |
| 人工安全 | 1 (人工) | ✅ 展示 |
| 人工 NSFW | 2 (人工) | 按 `show_nsfw` 策略 |
| 待复核 / 分歧 | 0 | ❌ 不展示，进复核页 |

> "已审核" = 机器自动审 + 人工审都算；只有 `review_status=0` 不展示。存量/新图都不会因"等人审"消失，仅真可疑的卡住。

### 6.2 图库 `search()` 改造

加参数 `only_reviewed=True`（默认）→ SQL `AND review_status != 0`。前端图库页默认传 `only_reviewed=1`。

### 6.3 分享站（`_handle_share_api` 的 `/world`、`/gallery`）

- 过滤 `review_status != 0`；
- 再按 `show_nsfw` 策略过滤 NSFW 图：
  - `never` → 排除 `nsfw=1`；
  - `blur` → 保留但 `nsfw_blur=1`（强制模糊，忽略个人设置）；
  - `original` → 保留原样；
- **用户级（已定）**：按**分享链接创建者 user_id** 取 `show_nsfw.per_user`（覆盖 global）。即分享者自己控制其分享内容的展示策略。

### 6.4 升级迁移（关键，否则老图库清空）

插件升级时一次性迁移，沿用旧 `nsfw` 判定把存量标"已审核"：
```sql
UPDATE images
SET review_status = CASE WHEN nsfw=1 THEN 2 ELSE 1 END,
    review_auto   = 1,
    review_level  = CASE WHEN nsfw=1 THEN 'explicit' ELSE 'safe' END,
    review_by     = 'auto',
    review_at     = strftime('%s','now'),
    nsfw_local_score  = nsfw_score,
    nsfw_remote_score = nsfw_score
WHERE review_status = 0;
```
（存量图按旧判定自动过审，新图才走三级判定 + 复核流程。）

---

## 7. 人工复核后台（独立 ReviewView 页面）

风格对齐 `ShareManageView` / `TokenView`（`.panel` 分块 + 汇总卡 + `Pager` 分页 + 筛选）。

### 7.1 数据来源

`GET /api/gallery/review_list`（新）：
- 默认筛选 **「待复核」**（`review_status=0`）；可切「已自动审 / 已人工审 / 全部」；
- 检测源：本地可疑 / 远程可疑 / 分歧(`diverge=1`)；
- 分数区间（本地、远程各自）、关键字（sha/user）。

### 7.2 页面结构

- 顶部汇总卡：待复核 / 已安全 / 已 NSFW / 本地可疑 / 远程可疑 / 分歧
- 筛选工具条：状态（默认待复核）/ 检测源 / 分数区间 / kw；移动端收底栏
- 列表：缩略图 + 本地分 + 远程分 + 远程 level + 分歧标记 + 当前状态 + 操作
- `Pager` 分页
- 大图预览：点缩略图看大图，侧栏显示本地分/远程分/level + 操作按钮

### 7.3 审核操作（行 / 批量）

- 「通过(安全)」→ `review_status=1, review_auto=0, review_level='safe'`；`nsfw=0, nsfw_blur=0`
- 「标记 NSFW」→ `review_status=2, review_auto=0, review_level='explicit'`；`nsfw=1`，按 `show_nsfw` 模糊
- 「评级」下拉：safe / questionable / explicit
- 批量通过 / 批量标记
- 写回同步 `nsfw` / `nsfw_blur`，并 `_oplog_add`（复用现有审核日志）

### 7.4 后端 API 清单

- `GET /api/gallery/review_list`（新）：复核队列 + 筛选
- `POST /api/gallery/review`（新）：`{sha, decision:"pass"|"nsfw", level?}` 写人工结论并收敛 `nsfw/nsfw_blur`
- `POST /api/gallery/review_batch`（新）：批量
- 改造 `GET /api/gallery/search`：默认 `only_reviewed=1`
- 改造 `GET /api/gallery/check_nsfw`：返回双分数 + level + diverge
- 改造分享站 `/world`、`/gallery`：加 `review_status!=0` 过滤 + `show_nsfw` 策略
- `image_store` 需加：`review_list()`、`set_review()`、`check_nsfw()` 写双分数、`search()` 加审核过滤、升级迁移脚本

---

## 8. 实施分阶段（建议）

1. **DB + 配置 + 迁移**：新增字段、`nsfw` 配置块、`review_status` 迁移脚本。
2. **双分数采集 + 三级判定**：`nsfw_detector.py` 远程模式 + 降级 + 写双分数与 `review_status`(auto)。
3. **展示策略**：图库 `search` 默认已审核；分享站加审核过滤 + `show_nsfw` 全局/用户级。
4. **复核后台**：复核页 + 复核 API + 汇总卡/筛选/分页/大图预览。
5. （可选）真实样本微调 CLIP embedding → 小 MLP 头，替代零样本提升准确度。

---

## 9. 风险与备注

- **动漫 18R 边界**：CLIP 零样本对二次元艺术化/半遮/擦边区分度有限，靠人工复核兜底，不可依赖机器 100% 判对。
- **服务可用性**：远程检测是外部依赖，插件端必须做好降级，避免检测服务挂掉导致出图/归档阻塞。
- **显存共用**：检测服务与 ComfyUI 抢同一张 7G 卡（见 `nsfw-detection-service.md`）。
- **升级迁移必做**：否则存量图全部 `review_status=0` 从图库/分享站消失。

---

## 10. 已确认决策（多轮讨论落定）

1. 检测方式：CLIP ViT-L/14 零样本（非微调头），只用 `openai/clip-vit-large-patch14` + `transformers`（部署见 `nsfw-detection-service.md`）。
2. 服务形态：出图服务器上手动运行的独立程序；插件端可配置远程地址，默认 `http://localhost:8890`。
3. 主判定位：远程 CLIP 为主判；本地 OpenNSFW **仅参考/分歧预警**，**不参与任何放行/判定门槛**（本地不准才换强模型）。
4. 降级策略：远程不可用/未配置/超时 → 标 `review_status=0`（待复核），不回退本地 OpenNSFW 单模型判定。
5. 三级自动判定：远程 `score<0.35` 自动安全；`0.35~0.8` 进复核；`≥0.8` 自动 NSFW（可人工推翻）。阈值 `low=0.35 / high=0.8`（已定）。
6. 分歧：本地/远程分差 `>0.3` → `diverge=1`，强制进复核。
7. 双分数存储：`nsfw_local_score` + `nsfw_remote_score` 都存，互不覆盖。
8. 审核页：独立页面（ShareManageView 同款风格），默认展示待复核。
9. 展示策略：图库/分享站只展示 `review_status!=0`；NSFW 按 `show_nsfw` 全局/用户级（never/blur/original）过滤。
10. **分享站用户级策略：按"分享链接创建者 user_id"取 `show_nsfw.per_user`**（覆盖 global）。
11. 升级迁移：存量图按旧 `nsfw` 判定标"已审核"，防图库清空。
