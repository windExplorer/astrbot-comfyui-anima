# 待办：出图消息撤回功能

> 状态：未开始（规划中）
> 提出日期：2026-07-16
> 来源：用户需求（暂未实现，先记到待办）

## 一、需求概述

为「图片生成结果」增加撤回能力，分两部分，二者均可独立开关：

1. **自动撤回（定时）**
   - 默认关闭（不自动撤回）。
   - 可配置撤回时间，**单位用分钟**，支持小数：
     - `0.5` = 30 秒后撤回
     - `2` = 2 分钟后撤回
   - 最长不超过 **2 分钟**（配置上限需做校验/截断）。

2. **手动撤回（`/撤回` 指令）**
   - 用户对机器人发出的某张图片**回复**并输入 `/撤回`，即可撤回那张图片。
   - 该指令本身可配置开/关。

> 两个功能（自动撤回 / 手动 `/撤回`）各自可开启或关闭。

## 二、配置项设计（拟加到 `_conf_schema.json`）

参考现有字段风格（如 `draw_timeout`、`queue_poll_interval`）：

```json
"recall_enabled": {
  "type": "bool",
  "description": "启用图片撤回功能",
  "hint": "总开关。关闭后自动撤回与 /撤回 指令都不生效。",
  "default": false
},
"recall_auto_minutes": {
  "type": "float",
  "description": "自动撤回延时(分钟)",
  "hint": "出图后多少分钟自动撤回机器人发的图。支持小数（0.5=30秒，2=2分钟）。0 或不填表示不自动撤回。最长不超过 2 分钟。",
  "default": 0
},
"recall_manual_enabled": {
  "type": "bool",
  "description": "启用 /撤回 指令",
  "hint": "开启后，用户回复机器人发出的图片并输入 /撤回 可手动撤回该图。",
  "default": false
}
```

> 注：自动撤回上限 2 分钟建议在代码里做硬性截断（避免配置误填过大）。

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
