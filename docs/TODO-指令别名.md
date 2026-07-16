# TODO：/draw 指令中文别名

## 需求

`/draw` 增加中文触发别名，用户在以下任一方式均可触发绘图：

- `/draw`
- `/绘图`
- `/绘画`
- `/画`
- `/画图`
- `/画画`

## 现状（代码位置：main.py）

- 指令注册：`@filter.command("draw")`（约 813 行），`async def cmd_draw`。
- 命令词剥离：`_strip_command(message_str, cmd)`（约 348 行）——只认单个 `cmd`（"draw"），
  若消息首词以 `cmd` 结尾则去掉首词返回剩余参数。该函数目前**不认别名**，
  如果只加别名而不改它，参数文本里会残留 `/绘图` 等前缀。
- 其它指令（`loralist` / `loraon` / `loraoff` / `queuestatus` / `workflows` / `drawhelp`）
  本次**不改**，仅针对 `/draw` 系列。

## 实现方案（两个候选，二选一）

### 方案 A：利用 `@filter.command` 的 `aliases` 参数（优先，若支持）
```python
DRAW_ALIASES = ["绘图", "绘画", "画", "画图", "画画"]

@filter.command("draw", aliases=DRAW_ALIASES)
async def cmd_draw(self, event):
    args = self._strip_command(event.message_str, "draw", DRAW_ALIASES)
    ...
```
同时把 `_strip_command` 改成接受 `aliases: list[str]`，对主命令词 + 所有别名都做剥离匹配：
```python
@staticmethod
def _strip_command(message_str, cmd, aliases=None):
    text = (message_str or "").strip()
    parts = text.split(None, 1)
    if not parts:
        return ""
    first = parts[0]
    words = {cmd, *(aliases or [])}
    for w in words:
        if first.lower() == w.lower() or first.lower().endswith(w.lower()):
            return parts[1].strip() if len(parts) > 1 else ""
    return text
```

> ⚠️ **待确认**：`@filter.command` 是否真的带 `aliases` 参数。
> 之前未在已安装 astrbot 的实例里验证（本机未装 astrbot）。
> 需在真实环境 `python -c "import astrbot.api.event.filter as f; help(f.command)"`
> 或查 `astrbot/api/event/filter/command.py` 的 `CommandFilter.__init__` 确认参数名。
> 若不支持 `aliases`，改用方案 B。

### 方案 B：不依赖 `aliases`，自行多触发词匹配（稳妥兜底）
`@filter.command` 只认一个命令词，无法一次注册多词。改用：
- 用 `@filter.regex(r"^[/.](draw|绘图|绘画|画|画图|画画)\b")` 单点匹配，
  或在 `_do_dispatch` 里手动做前缀匹配后调用同一绘制核心函数；
- 仍是把触发词集合传给 `_strip_command` 做剥离。
- 注意：`/画` 是单字别名，正则里放最前（`画` 在字符类 `[/.](draw|绘图|...|画|...)`）
  避免被 `/画图` 等先误匹配时需用更严谨的分词（建议用 `^[/／.](draw|绘图|绘画|画|画图|画画)(\s|$)`）。

## 验收清单

- [ ] 用 `/draw 猫咪`、`/绘图 猫咪`、`/绘画 猫咪`、`/画 猫咪`、`/画图 猫咪`、`/画画 猫咪`
      均能正常出图，且参数「猫咪」正确传入（不被别名前缀污染）。
- [ ] `drawhelp` / `loralist` 等帮助文本中提示新的触发方式。
- [ ] 别名与单字 `/画` 不和其它指令或 LLM 工具冲突。
