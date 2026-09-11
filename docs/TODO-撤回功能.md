# 待办：出图消息撤回功能

> 状态：**已实现（v5.12.7）**
> 提出日期：2026-07-16
> 来源：用户需求
> 实落日期：2026-09-11
> 关键调整（相对原规划）：用户要求**自动撤回按秒填**（非原规划的分钟）；并增加**「撤回功能白名单」**（仅管理员 + 白名单用户可用 `/撤回`）；配置按「权限归一、配置多的独立成组」整理，不再堆在顶层「其他」。

## 一、需求概述

为「图片生成结果」增加撤回能力，分两部分，二者均可独立开关：

1. **自动撤回（定时）**
   - 默认关闭（不自动撤回）。
   - 可配置撤回时间，**单位用秒**（用户明确要求不要分钟）：
     - `30` = 30 秒后撤回
     - `120` = 2 分钟后撤回
   - 最长不超过 **120 秒**（2 分钟，代码里硬性截断；QQ 群机器人撤回自家消息约 2 分钟窗口）。

2. **手动撤回（`/撤回` 指令）**
   - 用户对机器人发出的某张图片**回复**并输入 `/撤回`（alias：`recall`、`撤回图片`），即可撤回那张图片。
   - 该指令本身可配置开/关（默认开）。
   - **权限**：仅管理员，或「权限 → 撤回功能白名单」内的用户可用；白名单留空 = 仅管理员。

> 两个功能（自动撤回 / 手动 `/撤回`）各自可开启或关闭。

## 二、配置项设计（实际落地于 `_conf_schema.json`，已分组）

按「权限归一 + 配置多的独立成组」整理，不再堆在顶层：

- 新增「**权限**」分组（`permissions`）：
  - `permissions.recall_whitelist`（text）：撤回功能白名单（用户 ID，QQ 号，每行一个/逗号分隔）。留空 = 仅管理员。
  - `permissions.nsfw_group_whitelist`（text）：由原顶层 `nsfw_group_whitelist` 迁入；旧顶层键保留为 `invisible` 隐藏项，插件自动迁移旧值，升级不丢白名单。
- 新增「**撤回**」分组（`recall`）：
  - `recall.enabled`（bool，默认 false）：总开关。
  - `recall.auto_seconds`（int，默认 0，slider 0~120）：自动撤回延时**秒**；0 = 不自动撤回；>120 截断到 120。
  - `recall.manual_enabled`（bool，默认 true）：是否启用 `/撤回` 指令。

> 原规划里平铺的 `recall_enabled` / `recall_auto_minutes`(分钟,float) / `recall_manual_enabled` 已改为上述分组结构；分钟改为秒、并新增白名单。

## 三、实现要点（技术调研结论）

### 1. 主动撤回的 API
- AstrBot 平台基类（4.27/4.28）**没有**撤回 API；`event.send()` 返回 `None`，拿不到机器人自己发出消息的 `message_id`。
- 撤回只能走协议端：`event.bot.call_action("delete_msg", message_id=...)`，且 message_id 必须在**发送时**由协议端返回值捕获。
- 因此自动撤回开启时，出图发送改走 `send_group_msg` / `send_private_msg`（aiocqhttp）并捕获返回 `message_id`，再 `asyncio.create_task` 延时 `delete_msg`；关闭自动撤回时保持原 `event.send`，**行为零变化**。
- 当前仅 **aiocqhttp（OneBot V11）** 支持撤回自己发出的消息；其它平台 `_recall_call_action` 返回 None，自动跳过（记日志、不崩溃）。

### 2. 拿到机器人发出图片的 message_id
- 自动撤回：在出图主链路 / 图生图 / 后台续画 / 剧情模式兜底四处发送点，统一走 `_send_image_with_recall()`，开启自动撤回时直接调用协议端 API 发送并取回 `message_id`。
- 手动 `/撤回`：用户回复图片 → 被回复消息带 `Reply` 组件，`_extract_replied_message_id()` 从 `event.message_obj.message` 取出 `Reply.id` 即原图 `message_id`。

### 3. 指令注册
- `@filter.command("撤回", alias={"recall","撤回图片"})`：先判 `recall.enabled` → `recall.manual_enabled` → 权限（`_recall_allowed`：管理员或白名单）→ 是否回复图片 → 取 `message_id` 撤回 → 失败给友好提示并写日志。全程 `event.stop_event()`，不抛异常。

### 4. 平台差异注意
- 各平台对「撤回时限 / 能否撤回机器人自己消息」限制不同（QQ 群机器人约 2 分钟窗口），自动撤回上限 120 秒与之对齐。
- 撤回失败兜底：定时任务与指令都不抛异常，只记日志 / 回提示。

## 四、影响面 / 验收

- [x] `_conf_schema.json`：新增「权限」「撤回」分组；`recall_whitelist` 白名单；秒级 `auto_seconds`。
- [x] `main.py`：出图四处发送点接 `_send_image_with_recall`，开启自动撤回时记录 message_id 并定时撤回。
- [x] `main.py`：新增 `/撤回` 指令（回复图片撤回）+ 总开关/手动开关/管理员白名单权限校验。
- [x] 两个开关 + 自动撤回上限 120 秒的硬性截断。
- [x] 更新 `CHANGELOG.md`。
- [x] 编译校验 + 打包 + 提交（v5.12.7）。

## 五、备注
- 原规划（2026-07-16）误以为 AstrBot 走 `Platform` 基类 `recall_message(message_id, target_id)`，实际 4.27/4.28 该基类无此方法，改用 `event.bot.call_action("delete_msg", ...)`。详见上文「实现要点」。


## 三、实现要点（技术调研）

### 1. 主动撤回的 API
- AstrBot 通过**平台适配器**撤回消息，核心抽象在 `Platform` 基类。
- 撤回机器人自己发出的消息需要拿到**该消息的 `message_id`**，再调用对应适配器方法。
- 关键调用路径（参考 AstrBot 架构）：
  - `self.context.get_platform_adapter(<platform>)` 获取适配器实例；
  - 调用其撤回方法（如 `recall_message(message_id, target_id)`）。
  - `target_id` 一般来自 `event.message_obj`（群号 / 会话 id）。

### 2. 拿到机器人发出图片的 message_id
- 出图时 `_do_draw` 通过 `yield event.image_result(str(tmp_path))` 把图片交给 pipeline 发送。
- 自动撤回场景：需要在**图片实际发送后**拿到返回/记录的 `message_id`（注意异步 yield 与真正发送的时序），再 `asyncio.sleep(延时秒)` 后调用撤回。
- 手动 `/撤回` 场景：用户回复图片 → 被回复消息里带有原图的 `message_id`，从 `event.message_obj`（或 raw_message 的 `message_id` / `reply` 字段）取出后调用撤回。

### 3. 指令注册
- 新增 `@filter.command("撤回")` 处理器：
  - 先判断 `recall_enabled` 与 `recall_manual_enabled` 是否开启，否则提示未启用；
  - 校验当前消息是否为「回复了机器人图片」；
  - 提取被回复消息的 `message_id`，调用适配器撤回；
  - 失败（如超出平台撤回时限、权限不足）时给用户友好提示，并写日志。

### 4. 平台差异注意
- 各平台对「撤回时限」「能否撤回机器人自己消息」「是否需要管理员权限」限制不同（如 QQ 群机器人有 2 分钟撤回窗口），自动撤回上限 2 分钟即与之对齐。
- 撤回失败要兜底，不能让定时任务 / 指令直接抛异常。

## 四、影响面 / 验收

- [ ] `_conf_schema.json` 增加 3 个配置项
- [ ] `main.py`：`_do_draw` 出图后记录 message_id，按配置延时自动撤回
- [ ] `main.py`：新增 `/撤回` 指令处理（回复图片撤回）
- [ ] 两个开关 + 自动撤回上限 2 分钟的校验
- [ ] 更新 `README.md` 与 `CHANGELOG.md`
- [ ] 编译校验 + 打包 + 提交

## 五、备注
- 2026-07-16 首次调研：已确认 AstrBot 撤回走平台适配器、需 `message_id` + `target_id`；精确方法签名待在目标运行环境（已装 astrbot 的实例）中二次确认后落地。
