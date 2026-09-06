"""AstrBot ComfyUI 绘图插件（支持多服务器、多工作流、LoRA 管理、Anima 标签翻译）。"""

import os
import json
import random
import re
import time
import tempfile
import traceback
import uuid
import asyncio
from functools import wraps
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.message_components import Plain, Image
try:
    # 引用消息(Reply)与卡片图片(CardImage)在部分 AstrBot 版本/平台才有
    from astrbot.api.message_components import Reply, CardImage
except ImportError:  # pragma: no cover - 兼容旧版本
    Reply = None
    CardImage = None
from astrbot.api.star import Context, Star, register

# 图库列表 render 模式使用的自定义 HTML 模板（走 AstrBot 官方文本转图片服务）。
# 基于用户当前 AStrBot 的 t2i 模板（"小叽"卡片风）改造，仅把字号放大、行高加大，
# 并配合 html_render 的 quality=90，解决默认 quality=40 + 字小导致的发虚问题。
_GALLERY_T2I_TMPL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>小叽</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css"/>
<style>
html, body {
  height: auto;
  min-height: 0;
  margin: 0;
  padding: 24px;
  background: linear-gradient(135deg,#ffeef8,#e6f7ff);
  font-family: "Microsoft YaHei","PingFang SC",sans-serif;
  font-size: 16px;
  color: #333;
}

.card {
  height: auto;
  max-width: 100%;
  margin: 0 auto;
  background: #fff;
  border-radius: 20px;
  box-shadow: 0 10px 30px rgba(255,150,200,.15);
  padding: 26px 32px 20px;
}

.head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 18px;
}

.avatar {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: linear-gradient(135deg,#ff9edc,#8ec5fc);
  font-size: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.name {
  font-size: 26px;
  font-weight: 700;
  color: #ff70c0;
}

.content {
  line-height: 2.0;
  font-size: 30px;
  color: #333;
  word-break: break-word;
  overflow-wrap: break-word;
}

.footer {
  text-align: right;
  font-size: 18px;
  color: #ccc;
  margin-top: 18px;
}

pre {
  background: #1e1e2e;
  border-radius: 12px;
  padding: 12px;
  overflow: auto;
}

code {
  background: #fff0f8;
  padding: 2px 6px;
  border-radius: 6px;
  font-size: 26px;
  color: #d63384;
}

blockquote {
  margin: 10px 0;
  padding: 10px 14px;
  background: #fff6fb;
  border-left: 4px solid #ff9edc;
  border-radius: 8px;
  color: #666;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0 4px;
}

table th {
  background: linear-gradient(135deg,#ff9edc,#ffb3d1);
  color: #fff;
  font-weight: 700;
  padding: 8px 12px;
  text-align: left;
  border: 1px solid #ffd3e4;
}

table td {
  padding: 8px 12px;
  border: 1px solid #ffe3ec;
  color: #333;
  vertical-align: top;
}

/* 序号列加宽且不换行：3 位以上数字序号也不会被挤断换行 */
table th:first-child,
table td:first-child {
  white-space: nowrap;
  min-width: 96px;
  text-align: center;
}
</style>
</head>
<body>

<div class="card">
  <div class="head">
    <div class="avatar">🐦</div>
    <div class="name">小叽</div>
  </div>

  <div class="content">
    <article id="content"></article>
  </div>

  <div class="footer">小叽</div>
</div>

<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/contrib/auto-render.min.js"></script>
<textarea id="markdown-source" hidden>{{ text | safe }}</textarea>
<script>
const c = document.getElementById("content");
c.innerHTML = marked.parse(document.getElementById("markdown-source").value);
renderMathInElement(c, {
  delimiters: [
    { left: "$$", right: "$$", display: true },
    { left: "$", right: "$", display: false }
  ]
});
</script>
</body>
</html>
"""

try:
    from PIL import Image as _PILImage
except ImportError:  # pragma: no cover - 环境无 Pillow 时降级（不读像素尺寸）
    _PILImage = None

try:
    from astrbot.api.star import StarTools
except ImportError:
    StarTools = None

try:
    from . import comfyui_client, danbooru_client, translate_client, workflow_builder, comic
except ImportError:
    # 兼容非包环境（如本地测试直接运行本模块）
    import comfyui_client
    import danbooru_client
    import translate_client
    import workflow_builder
    import comic

try:
    from . import quota_store
except ImportError:
    import quota_store

try:
    from . import token_store
except ImportError:
    import token_store

# 全局保存插件实例，用于 LLM 工具等无法稳定获取 self 的调用场景兜底
_PLUGIN_INSTANCE = None

# 记录本插件最近生成成功的图片本地路径（按会话），用于图生图兜底：
# 当引用消息的图片因平台未回填 Reply.chain、且引用解析 API 不可用时，
# 退回使用本插件自己最近生成的图（典型场景：用户引用本插件刚出的图做图生图）。
# 键为 session_id（或 "__global__"），值为最近生成的本地图片路径列表（最多 5 张）。
g_last_generated: dict[str, list[str]] = {}

# 记录每个会话「用户最近发来的图片」本地路径，用于图生图兜底：
# 在 LLM 工具调用前（图片尚未被平台压缩临时文件清理/剥离）提前缓存，
# 当用户引用自己发的图做图生图、而工具执行时图片已不可达时回退使用。
# 键为 session_id，值为最近收到的本地图片路径列表（最多 5 张）。
g_last_received: dict[str, list[str]] = {}

# 每个会话「用户历史消息里出现过的图片」滚动缓存（按时间倒序，最多保留 12 张），
# 用于图生图兜底强度升级：
#   - 覆盖「前一条消息发的图」：用户先发图、AI 回复后用户再说"改这张图"，
#     LLM 工具触发时的 event 已是后续文本消息，_capture_llm_event 抓不到那条图，
#     但本缓存在用户每次发消息时都会记录，因此能回溯到上一条的图。
#   - 覆盖「引用消息里的图」：当平台未回填 Reply.chain、extract_quoted_message_images
#     也返回空时，若该引用图曾作为用户消息体/历史消息出现，也能从这里兜底。
# 仅在 LLM 工具调用且当前消息/引用/_last_event 全部取不到图时才启用，避免误用旧图。
g_recent_user_images: dict[str, list[str]] = {}

# 每个会话「最近一次图生图实际使用的用户原图」记忆（区别于生成图）：
# 多轮改图场景（用户先发原图 → AI 生成结果图 → 用户再说「重新改/再改一下」但没再发图）下，
# 用于让「重新改」回到最初的用户原图，而不是误用「AI 上次生成的结果图」。
# 记录在 llm_draw 取到最终参考图时（取到的第一张，通常是用户发的那张原图）。
# 键为 session_id，值为最近一次图生图的用户原图本地路径列表（最多 3 张）。
g_session_i2i_ref: dict[str, list[str]] = {}

# 「我会永远陪着你」伴侣插件的来源标识：llm_draw 的 source 参数命中此值时，
# 对整段提示词做专属的格式化与过滤（拆分正/负向、过滤时间/日程/位置/情绪等无关
# 事实与元指令、清除 [section compacted] 等标记）。
SOURCE_COMPANION_PLUGIN = "我会永远陪着你"


def _resolve_emoji_type(emoji_type: str, emoji_id) -> str:
    """解析表情回应要使用的 emoji_type。

    auto（默认）：纯数字编号 -> "1"（QQ 经典表情）；
    非数字（例如 emoji 字符 👀）-> "2"（Unicode 表情类）。
    也可以直接填 "1" / "2" 来强制覆盖自动判定。
    """
    t = str(emoji_type or "").strip().lower()
    if t and t != "auto":
        return t
    return "1" if str(emoji_id).strip().isdigit() else "2"


async def _set_msg_emoji_like(
    bot,
    msg_id,
    emoji_id,
    emoji_type: str = "1",
    set_on: bool = True,
) -> None:
    """给指定消息贴（或取消）QQ 原生表情回应。

    调用参数与 astrbot_plugin_parser 的 EmojiLikeArbiter 逐项保持一致：
    只传 message_id / emoji_id(int) / emoji_type / set，不额外传 group_id，
    以规避不同 OneBot 实现之间的行为差异。
    """
    mid = int(str(msg_id).strip())
    raw_eid = str(emoji_id).strip()
    try:
        # 纯数字按「QQ 表情编号」以整数传；非数字（如 emoji 字符）原样传给协议端
        eid = int(raw_eid)
    except (TypeError, ValueError):
        eid = raw_eid
    etype = str(emoji_type).strip() or "1"
    setter = getattr(bot, "set_msg_emoji_like", None)
    if callable(setter):
        await setter(message_id=mid, emoji_id=eid, emoji_type=etype, set=bool(set_on))
    else:
        await bot.call_action(
            "set_msg_emoji_like",
            message_id=mid,
            emoji_id=eid,
            emoji_type=etype,
            set=bool(set_on),
        )

# 本插件的画图/图库类 LLM 工具名集合。用于判定「用户是否通过 LLM 对话触发了画图」：
# 当 on_llm_response 里 LLM 返回的工具调用命中这些名字时，认为本次主对话是「画图流程」，
# 把该次 LLM 调用（以及画图收尾总结那次）的 token 消耗计入 token 统计（scene=agent_draw）。
DRAW_LLM_TOOLS = {"comfyui_draw", "comfyui_img2img", "comfyui_gallery", "comfyui_comic"}

# 记录「当前会话是否正处于画图 agent run」的标记（session_id -> 画图那一刻的 provider id，
# 可能为空串表示未知）。供 on_llm_response 判断：命中画图工具调用后，该会话随后的
# LLM 调用（画图收尾总结）也一并计入；on_agent_done 时清除，避免污染后续普通对话的统计。
g_draw_agent_sessions: dict[str, str] = {}


def _safe_llm_tool(func):
    """包裹 LLM 工具方法：任何未捕获异常都不再冒泡成 AstrBot 的「调用工具报错」，
    而是打印完整堆栈到日志并返回一句可读的失败说明，让用户能看到原因而非笼统报错。

    必须用 functools.wraps 保留 __doc__ 与 __name__，否则 AstrBot 的 @filter.llm_tool
    会解析不到 docstring 里的 Args（工具 schema 依赖它）。
    """
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except Exception as e:
            logger.error(
                f"[{func.__name__}] 工具执行异常，已捕获避免冒泡:\n"
                f"{traceback.format_exc()}"
            )
            return (
                f"绘图工具执行时出错（{type(e).__name__}）：{e}。"
                "请勿复述本提示给用户，自然地向用户说明生成遇到问题即可。"
            )
    return wrapper

# 中性随机话术：提交绘图后提示用，避免每次都相同，也不预设"画/拍"等具体动作，
# 兼容"画一张图"与"帮我拍个照"等不同触发语境。
_QUEUE_HINTS_GENERATING = [
    "在弄了，稍等一下。⏳",
    "这就开始，等我一小会儿。✨",
    "处理中，马上好。⏳",
    "在忙了，很快就好。⏳",
    "稍等，马上来。✨",
    "这就弄，一小会儿就好。⏳",
]

_QUEUE_HINTS_QUEUED = [
    "前面还有 {n} 个，轮到就给你。⏳",
    "前面排着 {n} 个，等下哈。⏳",
    "还有 {n} 个在前面，很快轮到你。✨",
    "排队中，前面 {n} 个，稍候下。⏳",
    "前面 {n} 个在等，一会儿就到。⏳",
]

# 面向用户的可爱错误话术：真实报错只写进日志，用户只看到经过包装的萌系提示。
# 按错误类别分池，每类多条随机取一，避免每次都一样。
_ERR_HINTS = {
    # 连不上绘图服务器（连接被拒 / 掉线 / DNS 解析失败等）
    "connect": [
        "连接不上绘图服务器，请检查服务是否在线或联系管理员。",
        "绘图服务器无响应，可能是网络断开或服务宕机，请联系管理员处理。",
        "无法连接到绘图服务器，请确认地址配置正确后重试或联系管理员。",
    ],
    # 超时：连上了但迟迟不出结果
    "timeout": [
        "绘图超时，服务器长时间未返回结果，请稍后重试。",
        "等待出图超时，服务器可能繁忙，可稍后再试一次。",
        "生图超时，建议稍后重新提交。",
    ],
    # 服务器返回了错误状态（HTTP 4xx/5xx 等）
    "server": [
        "绘图服务器返回了错误，请联系管理员检查服务端日志。",
        "服务器报错，绘图未成功，请联系管理员排查。",
        "绘图服务器返回异常响应，请联系管理员处理。",
    ],
    # 兜底：未归类的意外
    "generic": [
        "绘图出现意外错误，请稍后重试或联系管理员。",
        "生图失败，发生未预期的异常，请稍后再试或联系管理员。",
        "绘图过程中出现错误，请联系管理员查看日志。",
    ],
    # 任务完成但没找到输出图片（多半是工作流输出节点没配对）
    "no_image": [
        "任务已完成但未找到输出图片，可能是工作流输出节点未正确配置，请联系管理员检查。",
        "绘图完成但未能取到图片，请管理员核对输出节点配置。",
    ],
    # 工作流（图纸）加载失败
    "workflow": [
        "工作流文件读取失败，可能是路径错误或格式损坏，请联系管理员检查。",
        "无法加载该工作流，请管理员确认文件位置与内容是否正确。",
    ],
    # 服务器没返回任务 ID
    "no_task_id": [
        "绘图服务器已接收请求但未返回任务 ID，请联系管理员检查。",
        "提交成功但未取得任务编号，请管理员排查服务端状态。",
    ],
}

# 出图完成后的「贴心小报告」：随口报一下文件时间、尺寸、耗时，用可爱口吻，
# 多条随机取一，避免每次都一样。占位符：
#   {ftime} 文件生成时间（如 08-01 14:23:05）
#   {wh}    像素尺寸（如 768×768）
#   {size}  文件大小（如 1.2 MB）
#   {cost}  生图耗时（秒，保留 1 位小数）
# 出图完成后的中性小报告：只客观报文件信息，不预设"画/拍/生成"等具体动作，
# 兼容"画一张图"与"帮我拍个照"等不同触发语境的请求。多条随机取一，避免每次一样。
_DRAW_DONE_HINTS = [
    "好了，这张 {wh}、{size}，耗时 {cost} 秒，文件时间 {ftime} ✨",
    "搞定~ {wh}、{size}，用时 {cost} 秒，保存于 {ftime} ✅",
    "这张图 {wh}、{size}，耗时 {cost} 秒，生成时间 {ftime} 🖼️",
    "给你：{wh}、{size}，耗时 {cost} 秒，文件时间 {ftime} ✅",
    "已保存：{wh}、{size}，耗时 {cost} 秒，落盘于 {ftime} ✨",
    "好了，{wh}、{size}，跑了 {cost} 秒，时间 {ftime}，要调整随时说 👍",
]

# 工作流指定相关话术：用 /画<工作流名> 找不到该工作流时，
# 提示并改用默认工作流；以及只写了工作流名却没给提示词时提醒补充。
_WF_HINTS = {
    # 找不到「xxx」这个工作流名 → 用默认工作流作画
    "not_found": [
        "没有「{wf}」这个工作流，已用默认工作流完成绘制。",
        "找不到「{wf}」，改用默认工作流画了一张。",
        "未找到名为「{wf}」的工作流，已用默认工作流替代。",
    ],
    # 只写了触发词却没给提示词
    "no_arg": [
        "想画点啥呀？给句提示词呗~ 例如：/画 一个女孩",
        "光说「画」可不够哦，补一句画面描述吧，例如：/画 一个女孩",
        "还缺提示词呢，比如：/画 工作流名（可选） 一个女孩。",
    ],
}


# ------------------------------------------------------------------ #
# 图生图取图辅助：从多种来源提取图片本地路径并打详细日志
# ------------------------------------------------------------------ #
async def _extract_quoted_images(event: "AstrMessageEvent", reply_component=None) -> list:
    """尝试用 AstrBot 内置的引用消息解析器获取被引用消息里的图片引用
    （url / base64 / 本地路径）。该 API 在较新版本可用，旧版本静默返回空。
    reply_component 为可选的具体 Reply 组件，传入可提高拉取成功率。"""
    try:
        from astrbot.core.utils.quoted_message_parser import (
            extract_quoted_message_images,
        )
    except ImportError:
        return []
    try:
        if reply_component is not None:
            res = extract_quoted_message_images(event, reply_component)
        else:
            res = extract_quoted_message_images(event)
        if hasattr(res, "__await__"):
            res = await res
        return list(res or [])
    except Exception as e:
        logger.debug(f"【取图】 引用消息API回退异常（忽略）: {e}")
        return []


async def _download_url_to_temp(url: str) -> str | None:
    """带 UA/Referer 把图片 URL 下载到本地 temp 目录，返回本地路径。
    用于引用消息图片：部分平台（如 Aiocqhttp/QQ）引用的图只给出带签名的
    内网 URL，AstrBot 的 convert_to_file_path 下载会失败，这里做一次兜底下载。"""
    import aiohttp

    try:
        from astrbot.api import GLOBAL_CONFIG
        temp_dir = GLOBAL_CONFIG.get("temp_dir", None)
    except Exception:
        temp_dir = None
    if not temp_dir:
        temp_dir = os.path.join(os.getcwd(), "data", "temp")
    os.makedirs(temp_dir, exist_ok=True)
    ext = os.path.splitext(url.split("?")[0])[1].lower() or ".jpg"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        ext = ".jpg"
    out = os.path.join(temp_dir, f"quoted_{uuid.uuid4().hex}{ext}")
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
        # 部分图床要求 Referer 为同域，否则 403
        try:
            from urllib.parse import urlparse

            host = urlparse(url).netloc
            if host:
                headers["Referer"] = f"https://{host}/"
        except Exception:
            pass
        async with aiohttp.ClientSession(headers=headers) as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"【取图】 引用图兜底下载失败: HTTP {resp.status} {url[:80]}"
                    )
                    return None
                data = await resp.read()
        if not data or len(data) < 64:
            logger.warning(f"【取图】 引用图兜底下载内容异常（空/过小）: {url[:80]}")
            return None
        with open(out, "wb") as f:
            f.write(data)
        logger.info(f"【取图】 引用图兜底下载成功: {url[:80]}... -> {out}")
        return out
    except Exception as e:
        logger.warning(f"【取图】 引用图兜底下载异常（忽略）: {e} | {url[:80]}")
        return None


async def _image_to_local_path(item) -> str | None:
    """把一个 Image/CardImage 组件或图片引用字符串解析为本地文件路径。
    优先用 convert_to_file_path（兼容 url/file/base64/本地路径），
    失败时回退到 path/file 字段，并剥离 file:/// 前缀。"""
    try:
        if isinstance(item, str):
            if item.startswith("http"):
                comp = Image(url=item)
            elif item.startswith("data:"):
                # data:image/png;base64,xxxx -> base64://xxxx
                b64 = item.split(",", 1)[1] if "," in item else item
                comp = Image(file="base64://" + b64)
            else:
                comp = Image(file=item)
        else:
            comp = item
    except Exception as e:
        logger.debug(f"【取图】 构造图片组件失败: {e}")
        return None
    p = None
    try:
        p = await comp.convert_to_file_path()
    except Exception as e:
        logger.debug(f"【取图】 convert_to_file_path 失败: {e}")
    # 兜底：convert_to_file_path 失败时，若原始是 http(s) URL（如引用消息里的带签名图
    # 床地址），尝试自带 UA/Referer 下载到本地 temp，避免"引用消息图片读不到"。
    if not p:
        raw_url = None
        if isinstance(item, str) and item.startswith("http"):
            raw_url = item
        elif getattr(comp, "url", None):
            raw_url = comp.url
        if raw_url:
            logger.debug(f"【取图】 convert_to_file_path 失败，尝试 URL 兜底下载: {raw_url[:80]}")
            p = await _download_url_to_temp(raw_url)
    if not p and getattr(comp, "path", None):
        p = comp.path
    if not p and getattr(comp, "file", None):
        p = comp.file
    if p and str(p).startswith("file:///"):
        p = str(p)[8:]
    # 校验解析出的路径真实存在，避免把平台给的「裸文件名」当成本地路径上传导致失败
    if p and not os.path.exists(p):
        logger.warning(
            f"【取图】 解析出的路径不存在（可能是平台文件名而非本地路径）: {p!r}"
        )
        p = None
    # 进一步校验文件有效（非空、常见图片扩展名或魔数）。
    # 防止 convert_to_file_path 把"下载失败/下载到错误页"当成成功返回了一个路径，
    # 导致上层误判 got_explicit_image=True 而丢弃当前消息里真正可用的图。
    if p:
        try:
            sz = os.path.getsize(p)
        except OSError:
            sz = 0
        ok_ext = p.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))
        try:
            with open(p, "rb") as _f:
                head = _f.read(12)
        except OSError:
            head = b""
        # 常见图片文件头魔数
        magic = (
            head[:8] == b"\x89PNG\r\n\x1a\n"      # PNG
            or head[:3] == b"\xff\xd8\xff"          # JPEG
            or head[:4] == b"RIFF" and head[8:12] == b"WEBP"  # WEBP
            or head[:6] in (b"GIF87a", b"GIF89a")   # GIF
            or head[:2] == b"BM"                    # BMP
        )
        if sz == 0 or (not ok_ext and not magic):
            logger.warning(
                f"【取图】 解析出的文件无效（size={sz}, ext_ok={ok_ext}, magic_ok={magic}），"
                f"视为下载/解析失败: {p!r}"
            )
            p = None
    if not p:
        logger.warning(
            f"【取图】 无法解析为本地路径: "
            f"url={getattr(comp, 'url', None)!r} "
            f"file={getattr(comp, 'file', None)!r} "
            f"path={getattr(comp, 'path', None)!r}"
        )
    return p


async def _gif_to_first_frame(path: str) -> str | None:
    """把 GIF（动图）的第一帧提取为静态图，避免图生图时 ComfyUI 把多帧全部展开。

    - 非 GIF 直接返回原路径（None 表示入参无效）。
    - GIF：用 Pillow 取第一帧保存为 webp（无损、体积小），返回新文件路径；
      失败则降级返回原 GIF 路径（让 ComfyUI 自行决定，避免直接报错阻断出图）。
    生成的静态帧存于 temp 目录，24h 清理（与原中转文件同生命周期）。
    """
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as _f:
            _head = _f.read(6)
        if _head not in (b"GIF87a", b"GIF89a"):
            return path  # 非 GIF，原样返回
    except OSError:
        return path

    if _PILImage is None:
        logger.warning("【取图】 环境无 Pillow，无法提取 GIF 首帧，将直接上传原 GIF")
        return path

    # 优先落到插件 temp/ 目录（已有 24h 清理），无实例时回退系统临时目录
    out_dir = None
    try:
        if _PLUGIN_INSTANCE is not None and getattr(_PLUGIN_INSTANCE, "temp_dir", None) is not None:
            out_dir = Path(_PLUGIN_INSTANCE.temp_dir)
    except Exception:
        out_dir = None
    if out_dir is None:
        out_dir = Path(tempfile.gettempdir())
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = os.path.splitext(os.path.basename(path))[0] or "gif"
        out_path = out_dir / f"{stem}_frame0.webp"
        with _PILImage.open(path) as _gif:
            _first = _gif.convert("RGBA")
            _first.save(out_path, "WEBP")
        logger.info(f"【取图】 GIF 首帧已提取为静态图: {path} -> {out_path}")
        return str(out_path)
    except Exception as e:
        logger.warning(f"【取图】 GIF 首帧提取失败（降级上传原 GIF）: {e}")
        return path


@register(
    "astrbot_plugin_comfyui_anima",
    "astrbot-comfyui-anima",
    "通过指令或 AI 对话调用 ComfyUI 绘图，支持多服务器、多工作流、LoRA 管理与 Anima 标签翻译",
    "1.0.0",
)
class ComfyUIDrawPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        global _PLUGIN_INSTANCE
        _PLUGIN_INSTANCE = self
        self.config = config or {}
        # 记录每个会话最近一次提交的任务，用于 /queuestatus
        self._last_prompt: dict[str, str] = {}
        # 本地队列：记录本插件向每台 ComfyUI 服务器提交、但尚未完成的任务
        # （prompt_id 列表，按提交顺序）。不依赖 ComfyUI 的 /queue 接口，
        # 仅用于提示"前面还有几位"。
        self._server_pending: dict[str, list] = {}

        # 插件数据目录：temp/ 存出图，workflow/ 存工作流文件，lora_assets/ 存 LoRA 封面图
        self.data_dir = self._get_data_dir()
        self.temp_dir = self.data_dir / "temp"
        self.workflow_dir = self.data_dir / "workflow"
        self.lora_assets_dir = self.data_dir / "lora_assets"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_dir.mkdir(parents=True, exist_ok=True)
        self.lora_assets_dir.mkdir(parents=True, exist_ok=True)

        # 图库：把成品图/参考图/用户收藏图永久归档到 gallery/ 与 refs/（SQLite 索引）
        self.gallery = None
        try:
            try:
                from .image_store import ImageStore
            except ImportError:
                from image_store import ImageStore

            # cfg_provider：实时返回 gallery 配置，使 NSFW 阈值等改动无需重启即生效
            self.gallery = ImageStore(
                self.data_dir,
                self.config.get("gallery", {}),
                cfg_provider=lambda: self.config.get("gallery", {}),
            )
            logger.info(
                f"【初始化】 图库已就绪: {self.data_dir} "
                f"(gallery={self.gallery.gallery_dir}, refs={self.gallery.refs_dir}, "
                f"db={self.gallery.db_path})"
            )

            # 剧情模式档案（主动推演引擎，仅私聊）
            self.story = None
            self._story_active: dict = {}
            self._story_control: dict = {}   # key -> 推演循环控制态（含 asyncio 任务/队列/步数）
            self._recent_chat: dict = {}     # key -> list[(role,text)] 最近对话缓存（上下文进入用）
            try:
                from . import story_store
            except ImportError:
                import story_store
            try:
                self.story = story_store.StoryStore(
                    self.data_dir,
                    cfg_provider=lambda: self.config.get("story_mode", {}),
                )
                logger.info(f"【初始化】 剧情档案已就绪: {self.story.db_path}")
            except Exception as e:
                logger.warning(f"【初始化】 剧情档案初始化失败: {e}")
        except Exception as e:
            logger.warning(f"【初始化】 图库初始化失败（功能不可用）: {e}", exc_info=True)

        # 生图次数限制（配额）：独立 SQLite 维护每个用户的总/小时生图计数与单独配置
        self.quota = None
        try:
            self.quota = quota_store.QuotaStore(self.data_dir)
            logger.info(f"【初始化】 生图限额已就绪: {self.quota.db_path}")
        except Exception as e:
            logger.warning(f"【初始化】 生图限额初始化失败（功能不可用）: {e}", exc_info=True)

        # 独立业务操作日志（oplog）：与 AstrBot logging 解耦，关键事件直接落盘
        self.oplog = None
        try:
            try:
                from .oplog_store import OpLogStore
            except ImportError:
                from oplog_store import OpLogStore
            self.oplog = OpLogStore(self.data_dir)
            logger.info(f"【初始化】 操作日志已就绪: {self.oplog.db_path}")
            # 启动自检：写入一条初始化事件，用于确认 oplog 链路是否真正打通
            try:
                self.oplog.add("oplog_init", "插件启动，操作日志系统就绪",
                               detail=f"db={self.oplog.db_path}")
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"【初始化】 操作日志初始化失败（可忽略，溯源日志不可用）: {e}")

        # 独立 WebUI 服务（standalone）：aiohttp 独立端口，与 AstrBot 内嵌页共存
        self.standalone_webui = None
        try:
            try:
                from .standalone_webui import create_standalone_webui
            except ImportError:
                from standalone_webui import create_standalone_webui
            self.standalone_webui = create_standalone_webui(self)
        except Exception as e:
            logger.warning(f"【初始化】 独立 WebUI 初始化失败（可忽略）: {e}")

        # LLM token 用量统计：独立 SQLite 记录插件自发起的辅助 LLM 调用
        # （翻译/改写/参数提取）的 token 消耗。主对话画图那一次发生在 AstrBot
        # 核心层，插件统计不到，不计入。
        self.token_store = None
        try:
            self.token_store = token_store.TokenStore(self.data_dir)
            logger.info(f"【初始化】 LLM token 统计已就绪: {self.token_store.db_path}")
        except Exception as e:
            logger.warning(f"【初始化】 LLM token 统计初始化失败（功能不可用）: {e}", exc_info=True)

        # 强制重载 webui_api 与 standalone_webui 依赖模块：AstrBot 热更新（watchfiles）
        # 只重载插件主模块 main.py，不会级联重载依赖模块，导致「新增 API 路由 / 接口改动」
        # 在热更新后不生效。这里在每次初始化（含热更新触发的重载）时先 importlib.reload
        # 这两个模块，让新代码立即生效，尽可能避免完整重启。
        try:
            import importlib
            # v5.9.8：扩展为全部自研模块——此前只重载 webui_api/standalone_webui，
            # 新增模块（如 platform_store/nai_client）热更新后 sys.modules 里仍是
            # 首次加载的旧代码，导致「前端是新版、生图链路是旧版」的隐蔽错位。
            # 顺序按依赖关系（被依赖者在前）。
            for _dep_name in (
                "webui_api", "standalone_webui",
                "platform_store", "nai_client",
                "comfyui_client", "workflow_builder", "comic",
                "danbooru_client", "image_store", "story_store",
                "quota_store", "oplog_store", "token_store",
                "nsfw_detector", "translate_client",
            ):
                try:
                    _dn = f"{__package__}.{_dep_name}" if __package__ else _dep_name
                    _dep_mod = importlib.import_module(_dn)
                    importlib.reload(_dep_mod)
                except Exception as _de:
                    logger.warning(f"【初始化】 {_dep_name} 模块强制重载失败（沿用已加载模块）: {_de}")
        except Exception as _re:
            logger.warning(f"【初始化】 依赖模块强制重载失败（沿用已加载模块）: {_re}")
        # reload 之后重建独立 WebUI 持有的 WebUIApi 实例：上面的 create_standalone_webui
        # 在 reload 之前已用「旧的」webui_api 类创建了 self._api（从已安装旧版本升级时，
        # sys.modules 里是旧模块），reload 只更新了模块对象、不会替换已存在的实例类，
        # 导致独立 WebUI 调用新增接口（如剧情 story_sessions / story_stats）报 AttributeError。
        try:
            if getattr(self, "standalone_webui", None) is not None:
                import importlib as _il
                _wa = _il.import_module(
                    f"{__package__}.webui_api" if __package__ else "webui_api"
                )
                # 用 get_webui_api_class 从磁盘兜底拿最新类，避免 reload 失败 / sys.modules
                # 污染时仍拿到缺 story_sessions 等方法的旧类，导致独立 WebUI 剧情接口不可用。
                _rebuild_cls = getattr(_wa, "get_webui_api_class", None)
                self.standalone_webui._api = (_rebuild_cls() if _rebuild_cls else _wa.WebUIApi)(self)
        except Exception as _re2:
            logger.warning(f"【初始化】 独立 WebUI 的 WebUIApi 实例重建失败（剧情接口可能不可用）: {_re2}")

        # WebUI 控制台：把本插件日志镜像进内存环形缓冲，供页面读取
        try:
            try:
                from .webui_api import LOG_BUFFER
            except ImportError:
                from webui_api import LOG_BUFFER

            self._webui_log_buffer = LOG_BUFFER
            self._install_webui_log_handler()
        except Exception as e:
            logger.warning(f"【初始化】 WebUI 日志缓冲初始化失败（可忽略）: {e}")

    def _install_webui_log_handler(self) -> None:
        """安装一个 logging handler，把日志副本写入内存环形缓冲 + data_dir/webui.log。"""
        if not getattr(self, "_webui_log_buffer", None):
            return
        import logging

        buffer = self._webui_log_buffer

        class _RingHandler(logging.Handler):
            def emit(self, record):
                try:
                    buffer.append(self.format(record))
                except Exception:
                    pass

        h = _RingHandler()
        h.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
        h.setFormatter(fmt)

        # 同时落盘，方便排查（文件保留最近 1MB 滚动）
        fh = None
        try:
            from logging.handlers import RotatingFileHandler

            fh = RotatingFileHandler(
                self.data_dir / "webui.log", maxBytes=1024 * 1024, backupCount=1, encoding="utf-8"
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
        except Exception:
            fh = None

        # 把 handler 同时挂到 root 与本插件的 logger 链：
        # 仅挂 root 时，若 AstrBot 的 logger 对象不自定义/或 propagate 被关闭，
        # 业务日志不会传播到 root handler，导致日志页/落盘为空。这里双保险。
        targets = [logging.root]
        try:
            from astrbot.api import logger as _astr_logger
            if _astr_logger is not None:
                targets.append(_astr_logger)
        except Exception:
            pass
        for t in targets:
            try:
                t.addHandler(h)
                if fh is not None:
                    t.addHandler(fh)
                if hasattr(t, "setLevel"):
                    t.setLevel(logging.DEBUG)
            except Exception:
                pass
        try:
            if _astr_logger is not None:
                _astr_logger.propagate = True
        except Exception:
            pass
        self._webui_log_handler = h
        self._webui_file_handler = fh

    async def initialize(self) -> None:
        # 给 LLM 工具的 JSON schema 补 `required`。
        # AstrBot 的 llm_tool 装饰器仅靠 docstring 生成 schema，不会标记 required，
        # 导致模型把所有参数都视为可选、经常以空 {} 调用工具（表现为
        # 「解析参数失败: Expecting value」→ 参数被框架兜底成 {} → prompt 永远为空）。
        # 这里在工具注册完成后手动把核心必填参数标记为 required，强制模型填值。
        try:
            from astrbot.core.provider.register import llm_tools

            required_map = {
                "comfyui_draw": ["prompt"],
                "comfyui_img2img": ["prompt"],
                "comfyui_gallery": ["mode"],
                "comfyui_comic": ["prompt"],
                # comfyui_workflows 无参数，无需 required
            }
            patched = []
            for tool in llm_tools.func_list:
                if tool.name in required_map:
                    params = getattr(tool, "parameters", None) or {}
                    if isinstance(params, dict):
                        params.setdefault("properties", {})
                        params["required"] = required_map[tool.name]
                        tool.parameters = params
                        patched.append(tool.name)
            if patched:
                logger.info(f"【初始化】 已为工具补充 required: {patched}")
            else:
                logger.warning("【初始化】 未找到本插件工具，补充 required 跳过（工具可能尚未注册）")
        except Exception as e:  # 框架内部结构变动时不致命
            logger.warning(f"【初始化】 补充工具 required 失败（可忽略）: {e}")

        # 注册 WebUI 控制台路由（/api/<插件名>/page/...）
        try:
            try:
                from .webui_api import register_web_api
            except ImportError:
                from webui_api import register_web_api

            register_web_api(self)
        except Exception as e:
            logger.warning(f"【初始化】 注册 WebUI 路由失败（控制台不可用）: {e}")

        # 启动独立 WebUI 服务（若配置开启）
        try:
            if getattr(self, "standalone_webui", None) is not None:
                await self.standalone_webui.start()
        except Exception as e:
            logger.warning(f"【初始化】 独立 WebUI 启动失败（可忽略）: {e}")

        # 兼容旧配置：为新版 AstrBot 的 template_list 自动补 __template_key。
        # 升级前就已存在的 comfyui_servers / loras / workflows / draw_ratio 条目
        # 没有 __template_key，新 AstrBot 会报「找不到对应模板，请删除后重新添加」。
        # 这里给缺 key 的条目自动生成一个并落盘，避免用户手动删配重加。
        try:
            patched = 0
            for key in ("comfyui_servers", "loras", "workflows", "draw_ratio"):
                items = self.config.get(key)
                if not isinstance(items, list):
                    continue
                changed = False
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if not item.get("__template_key"):
                        item["__template_key"] = uuid.uuid4().hex
                        changed = True
                        patched += 1
                if changed:
                    self.config[key] = items
            if patched:
                try:
                    self.config.save_config()
                except Exception:
                    pass
                logger.info(f"【初始化】 已为 {patched} 个旧配置项补 __template_key 并落盘")
        except Exception as e:
            logger.warning(f"【初始化】 补 __template_key 失败（可忽略）: {e}")

        # 兜底恢复：draw_ratio（尺寸比例预设）被清空时，自动从 _conf_schema.json 恢复内置默认。
        # 避免用户误清空后「竖版/横版/9:16」等比例词不再触发，且 UI 里逐条找回困难。
        try:
            ratio_items = self.config.get("draw_ratio")
            if not ratio_items:
                _sp = Path(__file__).resolve().parent / "_conf_schema.json"
                if _sp.exists():
                    _sch = json.loads(_sp.read_text(encoding="utf-8"))
                    _defaults = (_sch.get("draw_ratio") or {}).get("default") or []
                    if isinstance(_defaults, list) and _defaults:
                        self.config["draw_ratio"] = json.loads(json.dumps(_defaults))
                        try:
                            self.config.save_config()
                        except Exception:
                            pass
                        logger.info(f"【初始化】 draw_ratio 为空，已恢复 {len(_defaults)} 个内置尺寸比例预设")
        except Exception as e:
            logger.warning(f"【初始化】 恢复 draw_ratio 默认预设失败（可忽略）: {e}")

    async def terminate(self) -> None:
        # 停止独立 WebUI 服务
        try:
            if getattr(self, "standalone_webui", None) is not None:
                await self.standalone_webui.stop()
        except Exception:
            pass
        # 关闭图库 SQLite 连接（含 WAL checkpoint 合并回主库），
        # 避免停止/卸载时残留 -wal 文件导致下次建表/迁移静默失败。
        try:
            if getattr(self, "gallery", None) is not None:
                self.gallery.close()
        except Exception:
            pass
        # 移除 WebUI 日志 handler，避免重复安装/内存泄漏
        try:
            import logging

            for h in ("_webui_log_handler", "_webui_file_handler"):
                handler = getattr(self, h, None)
                if handler is not None:
                    logging.root.removeHandler(handler)
                    handler.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 配置辅助
    # ------------------------------------------------------------------ #
    def _cfg(self, key: str, default=None):
        try:
            val = self.config.get(key, default)
        except Exception:
            val = default
        return val if val is not None else default

    def _record_llm_token(self, scene: str, model: str, llm_resp, event=None) -> None:
        """记录一次 LLM 调用的 token 用量。

        scene 取值：translate / rewrite_anima / rewrite_real / extract_args / agent_draw。
        从 ``llm_resp.usage``（TokenUsage）读 input_other / input_cached / output；
        usage 为 None（某些 provider 不返回）时记 0，不影响生图主流程。
        同时记录 user_name（event.get_sender_name()），供 WebUI 用户排行展示名字。
        全程 try/except 包裹，失败只打 warning，绝不抛错影响主流程。
        """
        if self.token_store is None or not self._cfg("llm_token_stats", True):
            return
        try:
            usage = getattr(llm_resp, "usage", None)
            in_other = getattr(usage, "input_other", 0) or 0
            in_cached = getattr(usage, "input_cached", 0) or 0
            out = getattr(usage, "output", 0) or 0
            if event is None:
                event = getattr(self, "_last_event", None)
            if event is not None:
                user_id = (getattr(event, "get_sender_id", lambda: "")() or "") or ""
                user_name = (getattr(event, "get_sender_name", lambda: "")() or "") or ""
            else:
                user_id, user_name = "", ""
            self.token_store.record_used(
                user_id, scene, model or "", in_other, in_cached, out, user_name=user_name
            )
        except Exception as e:
            logger.warning(f"【统计·token】 记录 LLM 用量失败: {e}")

    async def _llm_extract_args(self, user_text: str, param_spec: str) -> dict | None:
        """当默认模型不支持 Function Calling 导致工具参数空洞时，用「指定模型」(llm_model)
        重新理解用户原话并提取工具参数。返回解析出的参数字典；失败/未配置则返回 None。"""
        model = self._cfg("llm_model", "").strip()
        if not model or not user_text:
            return None
        prompt = (
            "你是一个参数提取器。下面用户想调用一个绘图/图库插件工具。"
            "请只输出一个 JSON 对象（不要任何解释、不要 markdown 代码块、不要反引号），"
            "字段名与下方参数说明一致。\n\n"
            f"工具参数说明：\n{param_spec}\n\n"
            f"用户原话：\n{user_text}\n\n"
            "只输出 JSON 对象："
        )
        try:
            logger.info(f"【绘图·LLM④】trace=(extract_args) 阶段=提取工具参数 使用指定模型({model})进入LLM")
            llm_resp = await self.context.llm_generate(chat_provider_id=model, prompt=prompt)
            self._record_llm_token("extract_args", model, llm_resp)
            text = getattr(llm_resp, "completion_text", "") or ""
        except Exception as e:
            logger.warning(f"【工具·llm_model】 指定模型({model}) 参数提取失败，退回默认逻辑: {e}")
            return None
        text = text.strip()
        # 容忍 ```json ... ``` 包裹
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text, flags=re.DOTALL)
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return None
            return None

    def _get_data_dir(self) -> Path:
        """获取插件专属数据目录（兼容新旧 AstrBot 版本）。"""
        plugin_name = "astrbot_plugin_comfyui_anima"
        if StarTools is not None:
            try:
                d = StarTools.get_data_dir(plugin_name)
                if d:
                    return Path(d)
            except Exception:
                pass
        return Path(__file__).resolve().parent

    def _resolve_workflow_path(self, wf: dict) -> str:
        """将配置中的工作流文件名解析为 workflow/ 目录下的完整路径。"""
        name = (wf.get("workflow_name") or "").strip()
        if not name:
            return ""
        p = self.workflow_dir / name
        if not p.suffix:
            p = p.with_suffix(".json")
        return str(p)

    def _cleanup_temp(self, max_age: float | None = None) -> None:
        """清理 temp/ 中超过 keep_temp_hours 小时的旧图片，避免无限增长；
        同时按配置触发图库 LRU 容量清理（收藏/带标签图永不淘汰）。"""
        if max_age is None:
            max_age = int(self._cfg("gallery", {}).get("keep_temp_hours", 24)) * 3600
        try:
            now = time.time()
            for f in self.temp_dir.iterdir():
                if f.is_file() and now - f.stat().st_mtime > max_age:
                    f.unlink()
        except Exception:
            pass
        # 图库 LRU 清理（轻量，失败不致命）
        if self.gallery is not None:
            try:
                self.gallery.enforce_lru()
            except Exception as _e:
                logger.debug(f"【图库】 LRU 清理异常（忽略）: {_e}")

    def _servers(self) -> list[dict]:
        return self._cfg("comfyui_servers", []) or []

    def _workflows(self) -> list[dict]:
        return self._cfg("workflows", []) or []

    @staticmethod
    def _split_lora_aliases(raw: str) -> list[str]:
        """把 LoRA 的「别名 / keywords」字段拆成干净的独立别名列表。

        支持多种分隔符：逗号、全角逗号、竖线 ``|``（含 ``||``）、``&``（含 ``&&``）、
        斜杠、顿号、空白；并去掉圆括号 / 全角括号内的注释（如 ``(角色&&画风lora)``）。
        这样「菲比啾比, phoebe_chibi || 菲比丘比 && phoebe || 菲比 (角色&&画风lora)菲比啾比」
        能正确拆出 phoebe_chibi / 菲比丘比 / phoebe / 菲比 等独立别名，供出图按别名匹配。
        """
        if not raw:
            return []
        cleaned = re.sub(r"[\(（][^\(（\)）]*[\)）]", " ", raw)
        parts = re.split(r"[,，|&/、\s]+", cleaned)
        out: list[str] = []
        seen: set[str] = set()
        for p in parts:
            p = p.strip()
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def _lora_library(self) -> list[dict]:
        """全局 LoRA 库（配置顶层 loras）。返回项附带 aliases（供 LLM 区分/引用）。"""
        out = []
        for l in (self._cfg("loras", []) or []):
            item = dict(l)
            name = (item.get("name") or "").strip()
            kws = (item.get("keywords") or "").strip()
            aliases = []
            if name:
                aliases.append(name)
            for a in self._split_lora_aliases(kws):
                if a and a not in aliases:
                    aliases.append(a)
            item["aliases"] = aliases
            out.append(item)
        return out

    def _lora_lib_index(self) -> dict[str, dict]:
        """全局 LoRA 库按「名称 + 全部别名」建索引（键 -> 库条目）。

        供命令 / LLM 工具用别名（如 phoebe_chibi）也能定位到真实 LoRA，
        解决「comfyui_loras 查到别名、出图却匹配不到」的问题：补全临时启用
        的 LoRA 时，除精确 name 外也应命中别名。
        """
        idx: dict[str, dict] = {}
        for l in self._lora_library():
            keys = set()
            nm = (l.get("name") or "").strip()
            if nm:
                keys.add(nm)
            for al in (l.get("aliases") or []):
                al = (al or "").strip()
                if al:
                    keys.add(al)
            for k in keys:
                idx.setdefault(k, l)
                idx.setdefault(k.lower(), l)
        return idx

    def _loras_of(self, wf: dict) -> list[dict]:
        """解析本工作流实际生效的 LoRA 列表。

        工作流里的 ``loras_text`` 只写「名称|权重|是否启用」（默认启用/权重），
        真正的文件名、是否「仅模型」、关键词、预设提示词都来自全局 LoRA 库
        （按名称匹配）。组装时把两者合并成完整配置；若某名称在库里找不到，
        则仅用工作流里的有限信息（model_name 空，注入时会告警）。
        """
        lib = self._lora_lib_index()
        text = (wf.get("loras_text") or "").strip()
        base = self._parse_loras_text(text) if text else (wf.get("loras", []) or [])
        merged: list[dict] = []
        for l in base:
            name = (l.get("name") or "").strip()
            if not name:
                continue
            # 先按精确名/别名/小写命中；命中不上再用 _lora_name_matches 做
            # 前缀/版本后缀模糊匹配，避免工作流预设名与库名有微小差异时丢掉 model_name。
            lib_l = lib.get(name) or next(
                (v for k, v in lib.items() if workflow_builder._lora_name_matches(k, name)),
                None,
            )
            if lib_l:
                merged.append(
                    {
                        "name": name,
                        "model_name": (lib_l.get("model_name") or "").strip(),
                        "model_only": bool(lib_l.get("model_only", True)),
                        "base_model": (lib_l.get("base_model") or "").strip() or (l.get("base_model") or "").strip(),
                        "weight": self._safe_lora_weight(l.get("weight", 1.0)),
                        "enabled": bool(l.get("enabled", False)),
                        "load_node": "",
                        "model_input": "lora_name",
                        "strength_model_input": "strength_model",
                        "strength_clip_input": "strength_clip",
                        "keywords": (lib_l.get("keywords") or ""),
                        "trigger_words": (lib_l.get("trigger_words") or ""),
                        "description": (lib_l.get("description") or ""),
                        "presets": self._parse_presets(lib_l.get("presets")),
                    }
                )
            else:
                # 库里没有：仅用工作流里的有限信息（文件名缺失）
                merged.append(
                    {
                        "name": name,
                        "model_name": (l.get("model_name") or "").strip(),
                        "model_only": True,
                        "base_model": (l.get("base_model") or "").strip(),
                        "weight": self._safe_lora_weight(l.get("weight", 1.0)),
                        "enabled": bool(l.get("enabled", False)),
                        "load_node": "",
                        "model_input": "lora_name",
                        "strength_model_input": "strength_model",
                        "strength_clip_input": "strength_clip",
                        "keywords": (l.get("keywords") or ""),
                        "trigger_words": (l.get("trigger_words") or ""),
                        "description": (l.get("description") or ""),
                        "presets": [],
                    }
                )
        return merged

    def _lora_matches_wf(self, lora: dict, wf: dict) -> bool:
        """判断 LoRA 是否适用于某工作流（按底模匹配）。

        规则：工作流底模为空（不限）→ 任何 LoRA 都可用；
        LoRA 底模为空（通用）→ 任何工作流都可用；
        否则两者 base_model 必须相等。
        """
        wf_bm = (wf.get("base_model") or "").strip().lower()
        lora_bm = (lora.get("base_model") or "").strip().lower()
        if not wf_bm or not lora_bm:
            return True
        return wf_bm == lora_bm

    def _apply_lora_presets(
        self, presets: dict[str, str], positive: str, negative: str
    ):
        """把 --名称/预设名 引用的预设提示词追加到正向提示词。

        presets: {lora_name: preset_name}。每个预设的提示词（textarea 里的
        ``[名称|提示词]`` 内容）追加到正向提示词（用英文逗号分隔）。库里找不到
        LoRA 或预设名时记录告警并跳过该项，不影响其它 LoRA。
        """
        lib = {(l.get("name") or "").strip(): l for l in self._lora_library()}
        pos_parts = [positive] if positive and positive.strip() else []
        for lora_name, preset_name in presets.items():
            l = lib.get((lora_name or "").strip())
            if not l:
                logger.warning(f"【LoRA】 预设引用：库里找不到 LoRA「{lora_name}」，跳过预设")
                continue
            found = None
            for p in self._parse_presets(l.get("presets")):
                if (p.get("name") or "").strip() == (preset_name or "").strip():
                    found = p
                    break
            if not found:
                logger.warning(
                    f"【LoRA】 预设引用：LoRA「{lora_name}」下找不到预设「{preset_name}」，跳过"
                )
                continue
            pr = (found.get("prompt") or "").strip()
            if pr:
                pos_parts.append(pr)
            logger.info(
                f"【LoRA】 应用预设：{lora_name}-{preset_name}"
                f"（追加正向={pr!r}）"
            )
        positive = ", ".join(p for p in pos_parts if p and p.strip()) if pos_parts else (positive or "")
        return positive, negative

    @staticmethod
    def _parse_loras_text(text: str) -> list[dict]:
        """解析多行 LoRA 文本为配置列表。每行：名称|权重|0/1（0=禁用）|底模（可选）。"""
        out: list[dict] = []
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if not parts[0]:
                continue
            name = parts[0]
            try:
                weight = float(parts[1]) if len(parts) > 1 and parts[1] != "" else 1.0
            except ValueError:
                weight = 1.0
            enabled = True
            if len(parts) > 2 and parts[2] != "":
                enabled = parts[2] not in ("0", "0.0", "false", "False", "禁用", "关")
            base_model = parts[3] if len(parts) > 3 and parts[3] != "" else ""
            out.append(
                {
                    "name": name,
                    "weight": weight,
                    "enabled": enabled,
                    "base_model": base_model,
                }
            )
        return out

    @staticmethod
    def _parse_llm_loras(loras) -> dict[str, float | None] | None:
        """把 LLM 工具传来的 LoRA 参数解析成 {名称: 权重|None}。

        每个条目支持两种格式：
          - "安魂曲"        → 权重 None（用配置默认权重）
          - "安魂曲:0.8"    → 权重 0.8（半角/全角冒号均可）
        与 /draw 指令的 --名称:权重 语义一致。返回 None 表示没有可用的 LoRA。
        """
        if not loras:
            return None
        out: dict[str, float | None] = {}
        for item in loras:
            if not isinstance(item, (str, int, float)) or str(item).strip() == "":
                continue
            tok = str(item).strip()
            # 兼容 "名称:权重" 与 "名称：权重"（全角冒号归一）
            name, _, wt = tok.replace("：", ":").partition(":")
            name = name.strip()
            if not name:
                continue
            weight = None
            if wt:
                try:
                    weight = float(wt)
                except ValueError:
                    weight = None
            out[name] = weight
        return out or None

    @staticmethod
    def _safe_lora_weight(value, default: float = 1.0) -> float:
        """把 LoRA 权重安全转为 float；空字符串/缺失/非法值回退 default（默认 1.0）。

        避免用户漏填 weight（配置里是空字符串 ''）时 float('') 抛 ValueError。
        """
        if value is None:
            return default
        try:
            f = float(value)
            return f if f == f else default  # 过滤 NaN
        except (TypeError, ValueError):
            return default

    def _serialize_loras_text(loras: list[dict]) -> str:
        """将 LoRA 列表序列化回 名称|权重|0/1|底模 文本（用于 loraon/loraoff 持久化）。"""
        lines = []
        for l in loras:
            name = (l.get("name") or "").strip()
            if not name:
                continue
            weight = self._safe_lora_weight(l.get("weight", 1.0))
            wstr = str(int(weight)) if float(weight) == int(weight) else str(weight)
            enabled = 1 if l.get("enabled", False) else 0
            bm = (l.get("base_model") or "").strip()
            if bm:
                lines.append(f"{name}|{wstr}|{enabled}|{bm}")
            else:
                lines.append(f"{name}|{wstr}|{enabled}")
        return "\n".join(lines)

    def _render_slot_template(
        self, slot: dict, template, values: dict
    ) -> str | None:
        """渲染单个提示词槽位（prompt_slots）的模板（实现见 comic.py）。"""
        return comic.render_slot_template(self, slot, template, values)

    # ------------------------------------------------------------------ #
    # 表情包 / 漫画：槽位造词（功能层 · 第二期）
    # ------------------------------------------------------------------ #
    def _find_workflow_by_name(self, name: str) -> dict | None:
        """按工作流名（name 字段）精确查找配置 dict；找不到返回 None。"""
        _n = (name or "").strip().lower()
        if not _n:
            return None
        for w in self._workflows():
            if (w.get("name") or "").strip().lower() == _n:
                return w
        return None

    # ---- v5.5.0 特殊功能（表情包/漫画）解析助手（实现见 comic.py）----
    def _workflow_kind(self, wf: dict | None) -> str:
        """返回工作流类型：comic（带 prompt_slots）或 draw。"""
        return comic.workflow_kind(self, wf)

    def _feature_by_key(self, key: str) -> dict | None:
        """按 key 取 special_features 里的功能配置（含旧 default_comic_workflow 迁移）。"""
        return comic.feature_by_key(self, key)

    def _resolve_comic_workflow(
        self, feature_key: str, wf_arg: str = ""
    ) -> tuple[str | None, str | None]:
        """按功能 key 解析漫画工作流名（带校验，不支持返回友好错误）。

        feature_key: meme_text / meme_img / comic；wf_arg: 用户 --wf 指定。
        返回 (workflow_name, error_msg)，error_msg 非空即失败。
        """
        return comic.resolve_comic_workflow(self, feature_key, wf_arg)

    def _auto_comic_workflow(self, requested: str) -> tuple[str | None, str | None]:
        """解析表情包/漫画工作流名（实现见 comic.py）。"""
        return comic.auto_comic_workflow(self, requested)

    def _resolve_comic_wf(self, requested: str, is_img2img: bool) -> tuple[str | None, str | None]:
        """图生图场景优先选「带 image_node 的漫画工作流」（实现见 comic.py）。"""
        return comic.resolve_comic_wf(self, requested, is_img2img)

    # 表情包/漫画意图关键词：命中即判为用户想出「带文字」的表情包/漫画。
    # 注：漫画/comic 也可能指纯漫画风插画，但插件优先按「带字表情包」处理（更符合多数意图）。
    _COMIC_INTENT_KEYWORDS = (
        "表情包", "表情图", "梗图", "气泡", "带字", "底部文字",
        "meme", "sticker", "comic", "漫画",
    )

    @classmethod
    def _is_comic_intent(cls, user_text: str, prompt: str = "") -> bool:
        """判断用户是否想要「带文字的表情包/漫画」（实现见 comic.py）。"""
        return comic.is_comic_intent(user_text, prompt)

    def _slot_vars(self, wf: dict) -> list[str]:
        """取出工作流 prompt_slots 的槽位变量名（去重、保序），用于直填模式按序映射（实现见 comic.py）。"""
        return comic.slot_vars(self, wf)

    @staticmethod
    def _slot_var_hint(var_name: str, slot: dict) -> str:
        """推断某个槽位变量的语义说明（实现见 comic.py）。"""
        return comic.slot_var_hint(var_name, slot)

    def _normalize_prompt_slots(self, raw) -> list:
        """把配置里的 prompt_slots（JSON 字符串或对象数组）归一化为列表（实现见 comic.py）。"""
        return comic.normalize_prompt_slots(self, raw)

    async def _comic_write_prompts_llm(self, wf: dict, user_text: str, scene: str, subject: str = "user") -> dict:
        """用内部 LLM 一次性生成表情包的两段提示词（draw + boogu，实现见 comic.py）。

        subject: "user"=用户自己的表情包；"bot"=bot 自己发的表情包（角色=佯本体）。
        """
        return await comic.comic_write_prompts_llm(self, wf, user_text, scene, subject)

    async def _comic_build_prompts_llm(self, wf, idea, lora_map, want_prompt=True, want_slots=True, subject: str = "user"):
        """用内部 LLM 把用户一句想法展开为画面提示词 + 槽位文字 + 识别 LoRA（实现见 comic.py）。"""
        return await comic.comic_build_prompts_llm(self, wf, idea, lora_map, want_prompt, want_slots, subject)

    @staticmethod
    def _parse_presets(raw) -> list[dict]:
        """把 LoRA 预设配置解析成 [{name, prompt}] 列表。

        两种来源都兼容：
        - 字符串（textarea，当前格式）：``[名字|提示词] [名字2|提示词, solo, 1girl]``，
          按 ``[...]`` 切块；块内以第一个 ``|`` 分隔名称与提示词，提示词里可含逗号。
        - 列表（旧版对象数组，兼容）：每个元素含 ``name`` + ``prompt``/``positive``，
          统一映射为 ``{name, prompt}``。
        """
        if not raw:
            return []
        if isinstance(raw, str):
            out: list[dict] = []
            for block in re.findall(r"\[([^\[\]]*)\]", raw):
                block = block.strip()
                if not block or "|" not in block:
                    continue
                name, prompt = block.split("|", 1)
                name = name.strip()
                if not name:
                    continue
                out.append({"name": name, "prompt": prompt.strip()})
            return out
        if isinstance(raw, list):
            out = []
            for p in raw:
                if not isinstance(p, dict):
                    continue
                name = (p.get("name") or "").strip()
                if not name:
                    continue
                prompt = (p.get("prompt") or "").strip() or (p.get("positive") or "").strip()
                out.append({"name": name, "prompt": prompt})
            return out
        return []

    @staticmethod
    def _fmt_token(v) -> str:
        """Token 数值友好化，使用 K/M/B 单位（千/百万/十亿）。

        与前端 WebUI 的展示口径一致，避免长数字影响可读性。
        """
        try:
            n = float(v)
        except (TypeError, ValueError):
            return str(v)

        def _trim(x: float, decimals: int) -> str:
            return f"{x:.{decimals}f}".rstrip("0").rstrip(".")

        abs_n = abs(n)
        if abs_n >= 1e9:
            return _trim(n / 1e9, 2) + "B"
        if abs_n >= 1e6:
            return _trim(n / 1e6, 2) + "M"
        if abs_n >= 1e3:
            return _trim(n / 1e3, 1) + "K"
        return str(int(n))

    @staticmethod
    async def _safe_close(client) -> None:
        """安全关闭 ComfyUI 客户端，忽略关闭时的异常。"""
        try:
            await client.close()
        except Exception:
            pass

    @staticmethod
    def _strip_command(message_str: str, cmd: str, aliases: tuple[str, ...] = ()) -> str:
        """从消息文本中去掉命令触发词（如 /draw 或其别名 /图生图），返回剩余参数文本。

        ``cmd`` 为主触发词，``aliases`` 为可选的别名集合（如中文别名），均支持剥离。
        """
        text = (message_str or "").strip()
        parts = text.split(None, 1)
        if not parts:
            return ""
        first = parts[0].lower()
        targets = [cmd.lower(), *(a.lower() for a in aliases)]
        for t in targets:
            t = t.lstrip("/")
            if first == t or first.endswith("/" + t) or first.endswith(t):
                return parts[1].strip() if len(parts) > 1 else ""
        return text

    def _resolve_ratio_size(
        self, prompt_text: str, width: int | None, height: int | None
    ) -> tuple[int | None, int | None]:
        """根据用户文本检测「尺寸比例」，返回 (宽, 高)；未命中或用户已显式指定宽高时返回 (None, None)。

        规则：
        - 用户已显式给出任意一个宽或高（width 或 height 非空）→ 不触发比例（用户优先）。
        - 否则在 prompt_text 里找 draw_ratio（template_list 数组）的 keyword 命中项
          （enabled=true），取第一个命中项返回其 width/height。
        - 都没有 → (None, None)，由调用方回退工作流默认尺寸。
        """
        # 用户显式给过宽或高 → 以用户为准，不触发比例
        if width or height:
            return None, None
        presets = self._cfg("draw_ratio", []) or []
        if not presets:
            return None, None
        text = (prompt_text or "").lower()
        if not text:
            return None, None
        for p in presets:
            if not isinstance(p, dict):
                continue
            if not p.get("enabled", True):
                continue
            kws = (p.get("keyword") or "").strip()
            if not kws:
                continue
            kw_list = [k.strip().lower() for k in kws.split(",") if k.strip()]
            if any(k and k in text for k in kw_list):
                pw = p.get("width")
                ph = p.get("height")
                if pw and ph:
                    try:
                        return int(pw), int(ph)
                    except (TypeError, ValueError):
                        return None, None
        return None, None

    def _danbooru_cfg(self) -> dict:
        return self._cfg("danbooru", {}) or {}

    def _translate_cfg(self) -> dict:
        """通用 HTTP 翻译接口配置块（translate_api）。"""
        return self._cfg("translate_api", {}) or {}

    def _resolve_translator_mode(self, wf: dict | None) -> str:
        """解析本次 Anima 工作流应使用的翻译模式。

        优先级：工作流级 translator_mode > 全局 translator_mode > 兼容旧配置
        （旧版只有 danbooru.enabled，故仍默认 danbooru）。合法值：
        danbooru / llm / api。非法或空值统一回退 danbooru。
        """
        # 工作流级覆盖
        if wf and isinstance(wf, dict):
            wm = (wf.get("translator_mode") or "").strip().lower()
            if wm in ("danbooru", "llm", "api"):
                return wm
        # 全局配置
        gm = (self._cfg("translator_mode", "") or "").strip().lower()
        if gm in ("danbooru", "llm", "api"):
            return gm
        # 兼容旧配置：全局未显式指定时，danbooru 开启就用 danbooru
        return "danbooru"

    def _build_danbooru(self) -> danbooru_client.DanbooruClient | None:
        cfg = self._danbooru_cfg()
        if not cfg.get("enabled"):
            return None
        return danbooru_client.DanbooruClient(
            cfg.get("url", "http://127.0.0.1:11111"),
            cfg.get("api_path", "/api/search"),
            int(cfg.get("limit", 20)),
            bool(cfg.get("show_nsfw", False)),
            bool(cfg.get("use_segmentation", True)),
            float(cfg.get("popularity", 0.15)),
            int(cfg.get("top_k", 20)),
        )

    def _build_translate_api(self) -> translate_client.TranslateApiClient | None:
        """构建通用 HTTP 翻译接口客户端；未配置 url 或未启用则返回 None。"""
        cfg = self._translate_cfg()
        if not cfg.get("enabled"):
            return None
        url = (cfg.get("url") or "").strip()
        if not url:
            return None
        headers = cfg.get("headers") or {}
        if isinstance(headers, str):
            try:
                import json as _json
                headers = _json.loads(headers) if headers.strip() else {}
            except Exception:
                headers = {}
        if isinstance(headers, dict):
            headers = {str(k): str(v) for k, v in headers.items()}
        else:
            headers = {}
        # 额外固定参数：JSON 字符串或 dict，随请求体/query 一起发送；值可含 {text} 占位符
        extra_params = cfg.get("extra_params") or {}
        if isinstance(extra_params, str):
            try:
                import json as _json
                extra_params = _json.loads(extra_params) if extra_params.strip() else {}
            except Exception:
                extra_params = {}
        if isinstance(extra_params, dict):
            extra_params = {str(k): v for k, v in extra_params.items()}
        else:
            extra_params = {}
        return translate_client.TranslateApiClient(
            url,
            method=str(cfg.get("method") or "POST"),
            headers=headers,
            timeout=int(cfg.get("timeout", 60)),
            text_field=str(cfg.get("text_field") or "text"),
            extra_params=extra_params,
            json_body=bool(cfg.get("json_body", True)),
            result_field=str(cfg.get("result_field") or "translated"),
            append_original=bool(cfg.get("append_original", False)),
        )

    def _resolve_translate_provider_id(self) -> str | None:
        """解析 LLM 翻译用的 provider id。

        优先 translate_llm_model；留空则取 AstrBot「当前正在使用」的对话 provider。
        取不到（无可用 provider）时返回 None，由调用方跳过翻译。
        """
        model = (self._cfg("translate_llm_model", "") or "").strip()
        if model:
            return model
        try:
            prov = self.context.get_using_provider()
        except Exception:
            prov = None
        if prov is None:
            return None
        cfg = getattr(prov, "provider_config", None) or {}
        return cfg.get("id") if isinstance(cfg, dict) else None

    async def _translate_llm(self, text: str) -> str:
        """用 LLM 把中文动漫描述翻译为英文 Danbooru 风格标签。

        使用独立配置 translate_llm_model；留空则走 AstrBot 当前默认对话模型。
        无可用模型时抛 RuntimeError，由调用方决定是否回退。
        """
        provider_id = self._resolve_translate_provider_id()
        if not provider_id:
            raise RuntimeError("LLM 翻译未配置可用模型（translate_llm_model 留空且无默认 provider）")
        prompt = (
            "你是动漫绘图提示词翻译器。把用户的中文动漫/二次元画面描述翻译为"
            "英文 Danbooru 风格标签，用英文逗号分隔，尽量使用标准 Danbooru 标签"
            "（如 1boy, long hair, blue eyes, school uniform）。只输出标签本身，"
            "不要任何解释、不要序号、不要中文。\n\n"
            f"中文描述：\n{text}\n\n"
            "英文标签："
        )
        try:
            timeout = max(1, int(self._cfg("llm_rewrite_timeout", 60) or 60))
            llm_resp = await asyncio.wait_for(
                self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt),
                timeout=timeout,
            )
            self._record_llm_token("translate", provider_id, llm_resp)
            out = getattr(llm_resp, "completion_text", "") or ""
        except asyncio.TimeoutError:
            raise RuntimeError(f"LLM 翻译超时（>{timeout}s）") from None
        except Exception as e:
            raise RuntimeError(f"LLM 翻译失败: {e}") from e
        out = out.strip()
        # 容忍 ``` 包裹与多余换行
        out = out.strip("`").strip()
        out = " ".join(out.split())
        return out

    async def _rewrite_to_anima_llm(self, text: str) -> str:
        """用 LLM 把一段自然语言描述（可能中英混杂）改写为纯英文 Anima 生图提示词。

        适用场景：第三方插件调用 comfyui_draw 传入的是整段结构化描述（非 Anima 标签
        格式），需要让 LLM 理解内容后生成 Anima 模型能吃的标签化提示词。
        使用独立配置 translate_llm_model；留空走 AstrBot 当前默认对话模型。
        无可用模型或失败时抛 RuntimeError，由调用方决定是否回退。
        """
        provider_id = self._resolve_translate_provider_id()
        if not provider_id:
            raise RuntimeError("LLM 改写未配置可用模型（translate_llm_model 留空且无默认 provider）")
        prompt = (
            "你是动漫（Anime/二次元）生图提示词专家。用户会给你一段对画面的描述"
            "（可能是中文、英文或中英混杂，也可能是结构化文本），请你理解其含义，"
            "改写为一张可以直接交给动漫模型（Anime 风格）的英文提示词。要求：\n"
            "1. 全部用英文，输出 Danbooru 风格标签（如 1girl, solo, white dress, "
            "long hair, blue eyes），用英文逗号分隔；结果开头必须固定加画质前缀 "
            "`masterpiece, best quality, ultra-detailed, highres, absurdres, "
            "intricate details, soft cinematic lighting, vibrant colors, refined anime style`"
            "（稳定拉高出图精致度，不要省略）；\n"
            "2. 这是动漫/二次元风格生图，输出必须保持动漫风格（anime style, "
            "anime coloring, 2d 等），不要输出写实/照片类标签；\n"
            "3. 即使原文提到『真实摄影、手机拍照、胶片颗粒、35mm、浅景深、"
            "写实、真人、live-action』等写实/摄影元素，也要忽略或转成动漫等价表达"
            "（如 detail, clean lineart, cel shading），绝不能把这些写实摄影标签"
            "（photo, photograph, realistic, candid photography, film grain, 35mm, "
            "dslr, depth of field, octane render 等）写进结果；\n"
            "4. 忠实反映描述里的人物、外观、服装、场景、动作、表情等核心信息，"
            "不要臆造描述里没有的内容；\n"
            "5. 如果描述本身是写实/真人场景，也要用动漫风格标签来表达相同内容"
            "（例如『真人摄影感』可表达为 anime style, detailed）；\n"
            "6. 只输出提示词本身，不要任何解释、不要序号、不要代码块、不要中文。\n\n"
            f"画面描述：\n{text}\n\n"
            "改写后的英文动漫（Anime）提示词："
        )
        try:
            # 加超时保护，避免 LLM 服务无响应导致生图流程卡死
            timeout = max(1, int(self._cfg("llm_rewrite_timeout", 60) or 60))
            llm_resp = await asyncio.wait_for(
                self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt),
                timeout=timeout,
            )
            self._record_llm_token("rewrite_anima", provider_id, llm_resp)
            out = getattr(llm_resp, "completion_text", "") or ""
        except asyncio.TimeoutError:
            raise RuntimeError(f"LLM 改写超时（>{timeout}s）") from None
        except Exception as e:
            raise RuntimeError(f"LLM 改写失败: {e}") from e
        out = out.strip()
        out = out.strip("`").strip()
        out = " ".join(out.split())
        return out

    async def _rewrite_to_real_llm(self, text: str) -> str:
        """用 LLM 把第三方插件传入的描述清理为适合「真人/写实」工作流的中文提示词。

        适用场景：真人（is_anima=false）工作流被第三方插件调用时，提示词里往往
        夹杂了结构标记（[User image request]、[Scene, style and final preset]、
        [section compacted]）和中英混杂内容。这里让 LLM 去掉标记、统一为中文、
        保留写实/摄影风格，输出连贯的画面描述。失败时抛异常，由调用方决定是否回退。
        """
        provider_id = self._resolve_translate_provider_id()
        if not provider_id:
            raise RuntimeError("LLM 清理未配置可用模型（translate_llm_model 留空且无默认 provider）")
        prompt = (
            "你是写实/真人摄影类生图提示词优化助手。用户会给你一段生成图片用的描述，"
            "它可能夹带结构标记（如 [User image request]、[Scene, style and final preset]、"
            "[section compacted]、Avoid ... 等命令式语句）且中英混杂。请你清理并改写为"
            "一段可以直接用于真人/写实摄影风格生图的中文提示词。要求：\n"
            "1. 去掉所有方括号结构标记、'Avoid'/‘不要/禁止’等元指令、以及明显的分节标题"
            "（如 Scene preset、Composition 等），只保留真正描述画面内容的话；\n"
            "2. 把中英混杂统一成连贯的中文描述，句子通顺、可按分号或逗号组织成一段；\n"
            "3. 保留写实/摄影风格元素（如 8K 超高清、真实摄影、手机随手拍、胶片颗粒、"
            "35mm、浅景深、自然光等）——这是真人写实工作流需要的，不要删掉；\n"
            "4. 忠实反映描述里的人物、外观、服装、场景、动作、表情、构图、氛围等核心信息，"
            "不要臆造没有的内容；\n"
            "5. 只输出改写后的中文提示词本身，不要任何解释、不要序号、不要代码块。\n\n"
            f"原始描述：\n{text}\n\n"
            "改写后的中文写实提示词："
        )
        try:
            timeout = max(1, int(self._cfg("llm_rewrite_timeout", 60) or 60))
            llm_resp = await asyncio.wait_for(
                self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt),
                timeout=timeout,
            )
            self._record_llm_token("rewrite_real", provider_id, llm_resp)
            out = getattr(llm_resp, "completion_text", "") or ""
        except asyncio.TimeoutError:
            raise RuntimeError(f"LLM 清理超时（>{timeout}s）") from None
        except Exception as e:
            raise RuntimeError(f"LLM 清理失败: {e}") from e
        out = out.strip()
        out = out.strip("`").strip()
        out = " ".join(out.split())
        return out

    @staticmethod
    def _split_prompt_segments(text: str) -> list[str]:
        """把提示词按逗号拆成「片段 + 分隔符」交错的列表，便于只翻译含中文的片段、
        其余原样保留（含逗号与原有空白），再按原顺序拼接，尽量不改变格式。

        例如 "帅气的少年, blue eyes, 微笑" ->
        ["帅气的少年", ", ", "blue eyes", ", ", "微笑"]。
        """
        import re as _re
        parts = _re.split(r"(,\s*)", text or "")
        # 去掉首尾空片段
        while parts and parts[0] in ("", ","):
            parts.pop(0)
        while parts and parts[-1] in ("", ","):
            parts.pop()
        return parts

    async def _translate_segment(self, wf: dict | None, seg: str) -> str:
        """用指定翻译模式翻译单个提示词片段，返回英文标签串。

        抛异常时由调用方决定是否回退。seg 应为含中文的片段。
        """
        mode = self._resolve_translator_mode(wf)
        if mode == "llm":
            return await self._translate_llm(seg)
        if mode == "api":
            api = self._build_translate_api()
            if api is None:
                raise RuntimeError("翻译模式为 api 但未配置 translate_api")
            return await api.translate(seg)
        # 默认 danbooru
        danbooru = self._build_danbooru()
        if danbooru is None:
            raise RuntimeError("翻译模式为 danbooru 但未启用 danbooru")
        tags = await danbooru.search(seg)
        # 注：danbooru.search 已对标签里的括号反转义（\( \)），可直接安全进 CLIP 提示词；
        # 且角色/作品 tag 已含完整外观设定，调用方（含 LLM 工具链）不要重复叠加
        # blue_hair / white_dress 等外观标签，以免覆盖角色原形象。
        if tags and self._danbooru_cfg().get("append_original"):
            return f"{seg}, {tags}"
        return tags or seg

    async def _translate_prompt(self, wf: dict | None, positive: str) -> str:
        """按翻译模式翻译提示词中「含中文的片段」，已有英文片段原样保留。

        逐段切分后仅对含中文字符的片段调用翻译，其余英文片段不动，最后按原顺序
        用逗号拼接。避免整段替换导致英文描述被丢。单个片段翻译失败时该片段
        保留原文（不影响其余片段）。
        """
        if not self._has_chinese(positive):
            return positive
        mode = self._resolve_translator_mode(wf)
        logger.info(f"【翻译】 Anima 工作流，翻译模式={mode}，仅翻译中文片段")
        segments = self._split_prompt_segments(positive)
        out_segments = []
        changed = False
        for seg in segments:
            if self._has_chinese(seg):
                try:
                    translated = await self._translate_segment(wf, seg)
                    if translated and translated.strip():
                        out_segments.append(translated)
                        changed = True
                        continue
                except Exception as e:
                    logger.warning(f"【翻译】 片段「{seg}」翻译失败，保留原文: {e}")
            out_segments.append(seg)
        # 片段列表已含逗号/空格分隔符，直接拼接即可保持原格式
        result = "".join(out_segments)
        if changed:
            return result
        return positive

    async def translate_test(self, mode: str, text: str) -> dict:
        """调试用：用指定模式翻译单段文本，返回结构化结果（结果/耗时/错误）。

        mode 仅用于本次测试，不改变插件全局/工作流配置。
        """
        if mode not in ("danbooru", "llm", "api"):
            return {"ok": False, "mode": mode, "result": "", "elapsed_ms": 0,
                    "error": f"非法模式 {mode!r}（可选 danbooru / llm / api）"}
        wf = {"translator_mode": mode}  # 用空工作流 + 指定模式覆盖
        start = time.time()
        try:
            result = await self._translate_segment(wf, text)
            return {
                "ok": True,
                "mode": mode,
                "result": result,
                "elapsed_ms": int((time.time() - start) * 1000),
                "error": "",
            }
        except Exception as e:
            return {
                "ok": False,
                "mode": mode,
                "result": "",
                "elapsed_ms": int((time.time() - start) * 1000),
                "error": f"{type(e).__name__}: {e}",
            }

    def _resolve_server(self, server_name: str | None = None) -> dict:
        servers = self._servers()
        if not servers:
            raise ValueError("未配置任何 ComfyUI 服务器，请先在插件配置中添加。")
        if server_name:
            for s in servers:
                if s.get("name") == server_name:
                    return s
            raise ValueError(f"找不到名为「{server_name}」的 ComfyUI 服务器。")
        enabled = [s for s in servers if s.get("enabled")]
        if len(enabled) == 1:
            return enabled[0]
        if len(enabled) > 1:
            raise ValueError("配置了多个启用(enabled=true)的服务器，请只启用一个。")
        # 未显式启用则使用第一个
        return servers[0]

    def _pick_default_workflow_name(self, is_img2img: bool) -> str:
        """按「风格优先级 + 文生图/图生图」选择默认工作流名。

        规则（对应配置 default_style_priority，默认 anime）：
          - 文生图（is_img2img=False）：anime→动漫文生图优先；real→真人文生图优先。
          - 图生图（is_img2img=True）：anime→动漫图生图优先；real→真人图生图优先。
        返回空串表示均未配置（调用方再回退第一个工作流）。
        """
        style_priority = (self._cfg("default_style_priority", "anime") or "anime").strip().lower()
        if is_img2img:
            if style_priority == "real":
                return (
                    self._cfg("default_img2img_workflow_real", "")
                    or self._cfg("default_img2img_workflow", "")
                    or self._cfg("default_workflow_real", "")
                    or self._cfg("default_workflow", "")
                )
            return (
                self._cfg("default_img2img_workflow", "")
                or self._cfg("default_img2img_workflow_real", "")
                or self._cfg("default_workflow", "")
                or self._cfg("default_workflow_real", "")
            )
        if style_priority == "real":
            return (
                self._cfg("default_workflow_real", "")
                or self._cfg("default_workflow", "")
            )
        return (
            self._cfg("default_workflow", "")
            or self._cfg("default_workflow_real", "")
        )

    def _detect_style_from_prompt(self, positive: str) -> str:
        """从提示词语义检测「真人/写实」还是「动漫/二次元」，用于未指定工作流时选默认。

        返回 "real" / "anime" / ""（无法判断）。命中互斥时以更强烈的信号优先：
        先看是否有动漫强词（anime/二次元/动漫/卡通/漫画/插画风/赛璐璐等），
        再看是否真人强词（真人/写实/照片/摄影/真实人物/真人照片/证件照等）。
        """
        text = (positive or "").lower()
        anime_kw = ("anime", "二次元", "动漫", "卡通", "漫画", "动画", "插画风", "赛璐璐",
                    "anima", "2d", "illustration style", "anime style", "cartoon", "manga")
        real_kw = ("真人", "写实", "照片", "摄影", "真实", "证件照", "photo", "photograph",
                   "realistic", "real person", "photorealistic", "真人写真")
        has_anime = any(k in text for k in anime_kw)
        has_real = any(k in text for k in real_kw)
        if has_anime and not has_real:
            return "anime"
        if has_real and not has_anime:
            return "real"
        return ""

    def _alias_workflow_name(self, name: str) -> str:
        """把外部传入的工作流名按「每个工作流配置里的 aliases 字段」映射为真实工作流名。

        每个工作流 items 里都有一个「工作流别名」textarea（aliases 字段，逗号或换行分隔
        多个别名）。传入名命中某个工作流的任一别名（大小写不敏感、忽略首尾空白）时，
        返回该工作流的真实 name；未命中则原样返回 name。
        """
        if not name:
            return name
        needle = name.strip().lower()
        for w in self._workflows():
            wf_name = (w.get("name") or "").strip()
            raw = (w.get("aliases") or "").strip()
            if not raw:
                continue
            for line in raw.replace("\r", "\n").split("\n"):
                for item in line.split(","):
                    alias = item.strip()
                    if alias and alias.lower() == needle:
                        logger.info(
                            f"【绘图·解析】 工作流别名命中：{alias!r} → {wf_name or '(未命名)'}"
                        )
                        return wf_name or name
        return name

    def _wf_usable(self, w: dict, name: str, fallback_on_missing: bool, intercept_disabled: bool = False):
        """工作流可用性检查：enabled=false（已停用）时——
        - fallback_on_missing=False：抛 ValueError，提示已停用；
        - fallback_on_missing=True 且 intercept_disabled=False（纯自动回退路径）：返回 None，跳过；
        - fallback_on_missing=True 但 intercept_disabled=True（绘图入口显式指定了工作流名）：
          同样抛 ValueError 拦截，避免用户/LLM 指定停用工作流时被静默替换。
        启用的工作流原样返回。"""
        if w.get("enabled", True):
            return w
        wname = ((w.get("name") or name or "").strip())
        if fallback_on_missing and not intercept_disabled:
            logger.warning(f"【绘图·解析】 工作流「{wname}」已停用，跳过")
            return None
        raise ValueError(f"工作流「{wname}」已停用，无法使用。")

    def _resolve_workflow(
        self,
        name: str | None = None,
        is_img2img: bool = False,
        fallback_on_missing: bool = False,
        positive: str = "",
        explicit_default: bool = False,
        intercept_disabled: bool = False,
    ) -> dict:
        """解析工作流配置。is_img2img=True 时优先用图生图默认工作流。

        匹配优先级：
          0) 若传入名命中了「工作流别名」配置（workflow_aliases），先映射为真实工作流名
          1) 精确匹配工作流名称（name 字段）
          2) 回退：按文件名匹配（workflow_name 字段，兼容带/不带 .json 后缀）
          3) 仍未匹配：
             - fallback_on_missing=False（默认，供 /draw --wf、/workflows set 等
               校验用户显式指定的工作流名）→ 抛 ValueError，便于调用方提示用户。
             - fallback_on_missing=True（绘图真正入口 _do_draw，可能收到伴侣/LLM
               传入的无效工作流名）→ 容错回退到按「风格优先级 + 文生图/图生图」
               配置的默认工作流；默认未配置则用第一个。
        """
        workflows = self._workflows()
        if not workflows:
            raise ValueError("未配置任何工作流，请先在插件配置中添加。")
        if name:
            # 0) 先按「工作流别名」把外部传入名映射为真实工作流名
            alias_target = self._alias_workflow_name(name)
            if alias_target and alias_target != name:
                name = alias_target
        if not name:
            # 未指定工作流时：
            #  - explicit_default=True（指令绘图且用户未显式指定工作流）→
            #    直接使用全局「风格优先级 + 文生图/图生图」默认工作流，
            #    **绝不**根据提示词内容（如 realistic）自行切换工作流，尊重指令语义。
            #  - explicit_default=False（LLM/Agent/第三方调用）→ 先按提示词语义判断
            #    「真人/动漫」，命中则用对应默认工作流；语义不明才走全局默认。
            if not explicit_default:
                _sem = self._detect_style_from_prompt("" if positive is None else str(positive))
                if _sem == "real":
                    _cand = (
                        self._cfg("default_img2img_workflow_real", "")
                        if is_img2img
                        else self._cfg("default_workflow_real", "")
                    ) or self._cfg("default_workflow_real", "")
                    if _cand:
                        name = _cand
                        logger.info(f"【绘图·解析】 提示词含「真人/写实」语义，选用真人工流={name}")
                elif _sem == "anime":
                    _cand = (
                        self._cfg("default_img2img_workflow", "")
                        if is_img2img
                        else self._cfg("default_workflow", "")
                    ) or self._cfg("default_workflow", "")
                    if _cand:
                        name = _cand
                        logger.info(f"【绘图·解析】 提示词含「动漫/二次元」语义，选用动漫工作流={name}")
            if not name:
                name = self._pick_default_workflow_name(is_img2img)
                logger.info(
                    f"【绘图·解析】 未指定工作流，按风格优先级={self._cfg('default_style_priority', 'anime')} "
                    f"{'图生图' if is_img2img else '文生图'}选定默认工作流={name or '（均无配置，回退第一个）'}"
                    f"{'（指令默认，不读提示词语义）' if explicit_default else ''}"
                )
        if name:
            # 1) 精确匹配工作流名称
            for w in workflows:
                if w.get("name") == name:
                    _w = self._wf_usable(w, name, fallback_on_missing, intercept_disabled)
                    if _w is not None:
                        return _w
                    break  # 命中已停用 → 进入回退
            # 2) 大小写不敏感 + 去首尾空格匹配名称（AI 常把 Default 写成 default 等）
            name_trim = name.strip()
            name_lower = name_trim.lower()
            for w in workflows:
                n = (w.get("name") or "").strip()
                if n.lower() == name_lower:
                    _w = self._wf_usable(w, name, fallback_on_missing, intercept_disabled)
                    if _w is not None:
                        return _w
                    break
            # 3) 回退：按文件名匹配（解决 LLM 把文件名当工作流名的问题）
            for w in workflows:
                fn = (w.get("workflow_name") or "").strip().lower()
                if not fn:
                    continue
                matched = (
                    fn == name_lower
                    or (fn.endswith(".json") and fn[:-5] == name_lower)
                    or (not fn.endswith(".json") and fn + ".json" == name_lower)
                )
                if matched:
                    _w = self._wf_usable(w, name, fallback_on_missing, intercept_disabled)
                    if _w is not None:
                        return _w
                    break
            # 全部失败
            avail = "、".join((w.get("name") or "(未命名)") for w in workflows)
            if not fallback_on_missing:
                raise ValueError(f"找不到名为「{name}」的工作流。可用工作流：{avail}。")
            # 容错回退：按「风格优先级 + 文生图/图生图」默认工作流，未配置则第一个可用
            fallback = self._pick_default_workflow_name(is_img2img)
            if fallback:
                for w in workflows:
                    if (
                        w.get("name") == fallback
                        or (w.get("workflow_name") or "").strip().lower() == fallback.strip().lower()
                    ):
                        _w = self._wf_usable(w, fallback, True)
                        if _w is not None:
                            logger.warning(
                                f"【绘图·解析】 找不到工作流「{name}」（可用：{avail}），"
                                f"容错回退到默认工作流「{w.get('name') or fallback}」"
                            )
                            return _w
                logger.warning(
                    f"【绘图·解析】 找不到工作流「{name}」，且默认工作流「{fallback}」已停用/未匹配，回退第一个可用工作流"
                )
                for w in workflows:
                    if w.get("enabled", True):
                        return w
                logger.warning(f"【绘图·解析】 所有工作流均已停用，仍返回默认工作流「{fallback}」")
                return workflows[0]
            logger.warning(
                f"【绘图·解析】 找不到工作流「{name}」且未配置默认工作流，回退第一个可用工作流（可用：{avail}）"
            )
            for w in workflows:
                if w.get("enabled", True):
                    return w
            return workflows[0]
        # 未指定 name（默认工作流也未配置）：返回第一个启用的工作流
        for w in workflows:
            if w.get("enabled", True):
                return w
        return workflows[0]

    # ------------------------------------------------------------------ #
    # 客户端工厂
    # ------------------------------------------------------------------ #
    def _build_client(self, server: dict) -> comfyui_client.ComfyUIClient:
        timeout = int(self._cfg("draw_timeout", 120)) + 30
        return comfyui_client.ComfyUIClient(
            server["url"],
            server.get("client_id") or None,
            timeout=timeout,
        )

    @staticmethod
    def _has_chinese(text: str) -> bool:
        """判断文本是否包含中文字符。用于决定是否调用提示词翻译。"""
        return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))

    @staticmethod
    async def _extract_images(event: AstrMessageEvent) -> list[str]:
        """从消息事件中提取所有图片的本地路径，用于图生图。支持多种来源：
        - 消息中直接附带的图片（含「文字 + 图片」混合、纯图片、指令 + 图片）
        - 引用/回复消息里带的图片（Reply.chain 内嵌，或平台 API 回退）
        - 卡片图片（CardImage）
        每张图都会打印来源与最终路径；单张失败不影响其它图。
        """
        comps = list(event.get_messages())
        logger.info(
            f"【取图】 开始：消息组件共 {len(comps)} 个 -> "
            + ", ".join(str(getattr(c, "type", type(c).__name__)) for c in comps)
        )

        candidates: list = []  # (组件/引用, 来源描述)
        has_reply = False  # 消息里是否出现引用(Reply)组件
        for comp in comps:
            # 用 type 属性判断组件类型，不依赖 isinstance（Reply/CardImage
            # 可能因不同 AstrBot 版本导入失败为 None，但 comp.type 始终可用）。
            # 注意：comp.type 是 str 子类的枚举（如 ComponentType.Reply），
            # str(枚举) 返回 "ComponentType.Reply" 而非 "Reply"，故用 .value/.name。
            t_raw = getattr(comp, "type", "")
            ct = getattr(t_raw, "value", None) or getattr(t_raw, "name", None) or str(t_raw)
            if isinstance(comp, Image) or ct in ("Image", "ComponentType.Image"):
                candidates.append((comp, "消息内图片"))
            elif (CardImage is not None and isinstance(comp, CardImage)) or ct in (
                "CardImage",
                "ComponentType.CardImage",
            ):
                candidates.append((comp, "卡片图片"))
            elif (Reply is not None and isinstance(comp, Reply)) or ct in (
                "Reply",
                "ComponentType.Reply",
            ):
                has_reply = True
                chain = getattr(comp, "chain", None) or []
                logger.info(
                    f"【取图】 发现引用消息 Reply(id={getattr(comp, 'id', None)})，"
                    f"链内组件 {len(chain)} 个"
                )
                for sub in chain:
                    st = str(getattr(sub, "type", ""))
                    if isinstance(sub, Image) or st == "Image":
                        candidates.append((sub, "引用消息内嵌图片"))
                # 平台 API 回退：引用只含占位符时，用 reply.id 去拉原消息图片。
                # 显式传入找到的 Reply 组件，避免 AstrBot 再自行查找失败。
                for ref in await _extract_quoted_images(event, reply_component=comp):
                    candidates.append((ref, "引用消息API回退"))

        paths: list[str] = []
        seen: set = set()
        for item, src in candidates:
            p = await _image_to_local_path(item)
            if p and p not in seen:
                seen.add(p)
                paths.append(p)
                logger.info(f"【取图】 成功 [{src}] -> {p}")
            elif p:
                logger.info(f"【取图】 跳过重复 [{src}] -> {p}")
            else:
                logger.warning(f"【取图】 失败 [{src}] 无法解析为本地路径")

        # 兜底：消息里带了引用(Reply)、但平台未回填引用内容/引用图解析失败时，
        # 回退到「本会话用户最近发过的图」。用户引用的通常正是他自己刚发的图，
        # 用历史缓存兜底是合理且安全的（仅当确实出现 Reply 才启用，纯文生图不受影响）。
        if not paths and has_reply:
            sid = getattr(event, "session_id", "") or ""
            for store in (
                list(reversed(g_last_received.get(sid) or [])),
                list(reversed(g_recent_user_images.get(sid) or [])),
            ):
                for p in store:
                    if p and os.path.exists(p) and p not in paths:
                        paths.append(p)
                if paths:
                    break
            if paths:
                logger.info(f"【取图】 引用图解析失败，兜底最近用户发的图: {paths}")

        if paths:
            logger.info(f"【取图】 完成：共取得 {len(paths)} 张图片")
        else:
            logger.info("【取图】 消息/引用/卡片内均未取到图片（本方法不兜底历史生成图）")
        return paths

    @staticmethod
    def _strip_inline_negative(s: str) -> str:
        """把文本里 'Avoid'/'Do not'/'Respect ... exclusions' 软信号之后的内容切掉，
        只保留其之前的正向与构图约束。未命中软信号则原样返回。

        供 llm_draw 等调用方在「专属过滤覆盖正向」之后再兜底一次，确保任何残留的
        内联负面词表（尾部大段逗号负向词）都被清干净。
        """
        if not s:
            return ""
        m = re.search(
            r"(avoid\b|do not\b|respect[^.]*?exclusions\b)",
            s,
            re.IGNORECASE,
        )
        return s[: m.start()].strip() if m else s.strip()

    @staticmethod
    def _split_external_prompt(text: str) -> tuple[str, str]:
        """把可能混合「正向/负向」与外部结构化标记的文本拆成 (正向, 负向)。

        兼容外部插件（如 astrbot_plugin_private_companion）把整段塞进单个 prompt
        的调用：包含 'Negative prompt:' 段落、'[section compacted]' 占位符、'[User
        image request]' 等分节方括号标题，以及 'Avoid'/'Do not'/'Respect ...
        exclusions' 负向软信号。

        统一策略：**正向与构图约束全部保留，负面直接删除**（负向不输出，回退到调用方
        自行提供的 negative_prompt）。对**所有来源**生效（不带 source 也处理），靠
        软信号与方括号识别，不误伤无标记的自然语言描述（常规 /draw 与 AI 对话调用）。
        """
        if not text:
            return "", ""
        # 负向软信号：命中 'Avoid'/'Do not'/'Respect ... exclusions' 时，软信号之后
        # 视为负面直接删除，只保留软信号之前的正向与构图约束。
        def _cut_inline_negative(s: str) -> str:
            return ComfyUIDrawPlugin._strip_inline_negative(s)

        # ---- 调试（降级为 DEBUG，不污染 INFO 日志）：完整展示「原始提示词」与
        #      「过滤后正向提示词」，便于人工对比；仅写 webui.log（DEBUG 级） ----
        def _dbg_block(tag: str, body: str) -> list[str]:
            lines = []
            # 超长内容按 400 字符分段，避免单行日志被截断/过长
            for i in range(0, max(1, len(body)), 400):
                seg = body[i:i + 400]
                lines.append(f"【拆prompt】[DBG] {tag}段{i // 400}: {seg}")
            return lines

        logger.debug(
            f"【拆prompt】[DBG] 输入长度={len(text)} "
            f"含Negative标记={bool(re.search(r'negative\\s*prompt\\s*[:：]', text, re.IGNORECASE))} "
            f"含Avoid/DoNot软信号={bool(re.search(r'(avoid\\b|do not\\b|respect[^.]*?exclusions\\b)', text, re.IGNORECASE))}"
        )
        for ln in _dbg_block("原始输入", text):
            logger.debug(ln)
        # ------------------------------------------------------------------

        # 1) 按 'Negative prompt:' 拆分正/负（大小写与冒号差异均兼容）
        m = re.search(r"negative\s*prompt\s*[:：]", text, re.IGNORECASE)
        if m:
            positive = text[: m.start()].strip()
            # 去掉开头的 'Positive prompt:' 标签
            positive = re.sub(
                r"^\s*positive\s*prompt\s*[:：]\s*", "", positive, flags=re.IGNORECASE
            ).strip()
            # 有的调用方把 Avoid/Do not 负向词表放在 'Negative prompt:' 之前，
            # 正向内仍残留负面软信号，这里再切一次，保证正向干净。
            positive = _cut_inline_negative(positive)
            positive = ComfyUIDrawPlugin._clean_prompt_markers(positive)
            logger.debug("【拆prompt】[DBG] === 走分支1(有Negative标记) 过滤后正向提示词 ===")
            for ln in _dbg_block("过滤后", positive):
                logger.debug(ln)
            # 负面直接删除（不保留，回退到调用方自行提供的 negative_prompt）
            return positive, ""

        # 2) 无 'Negative prompt:' 标记：兜底处理方括号标题 + 内联负向软信号。
        #    未命中软信号则原样返回（不误伤常规 /draw 与 AI 对话的自然语言描述）。
        positive = _cut_inline_negative(text)
        positive = ComfyUIDrawPlugin._clean_prompt_markers(positive)
        logger.debug("【拆prompt】[DBG] === 走分支2(无Negative标记) 过滤后正向提示词 ===")
        for ln in _dbg_block("过滤后", positive):
            logger.debug(ln)
        return positive, ""

    @staticmethod
    def _clean_prompt_markers(s: str) -> str:
        """清理外部提示词里的方括号分节标题与占位符，压缩空白。

        只删除结构噪声，保留中英文提示词正文（含中文描图）：
        - '[section compacted]' 截断占位符；
        - 含空格的方括号分节标题（如 '[User image request]'、'[Scene, style and
          final preset]'）；
        - 控制字符 / 零宽字符 / 孤立 emoji 等，但保留中文（\\u4e00-\\u9fff）。
        """
        if not s:
            return ""
        s = re.sub(r"\[\s*section\s*compacted\s*\]", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\[[^\]]*?\s.+?\]", " ", s)   # 含空格的方括号分节标题
        # 仅清控制字符/零宽字符/孤立 emoji，保留字母数字、标点、中文
        s = re.sub(r"[\u0000-\u001f\u200b-\u200f\ufeff]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    # ------------------------------------------------------------------ #
    # 核心：提交并等待出图（异步生成器，yield 消息）
    # ------------------------------------------------------------------ #
    async def _send(self, event: AstrMessageEvent, text: str) -> None:
        """主动发送一条文本消息（不占用 yield，避免命令 pipeline 在首个
        yield 后中断；同时标记 _has_send_oper，防止触发后续 LLM 阶段）。

        容错：发送失败（如底层 API 暂时不可用、协议端掉线/风控）只记日志，
        不向上抛异常——否则会中断 _do_draw 等调用方的后续流程（如等待出图、
        发送图片），导致「提示没发出去，图也没出来」。
        """
        try:
            await event.send(MessageChain([Plain(str(text))]))
        except Exception as _e:
            logger.warning(f"【发送】 主动发送文本失败（忽略，不中断主流程）: {_e}")

    # ------------------------------------------------------------------ #
    # 图文消息：把「配文 / 出图报告」与图片合成一条消息发送
    # ------------------------------------------------------------------ #
    def _cfg_image_caption(self) -> dict:
        """读取「图文消息」配置块（容错：任何异常 / 非 dict 都退回空 dict）。"""
        try:
            _c = self._cfg("image_caption", {}) or {}
            return _c if isinstance(_c, dict) else {}
        except Exception:
            return {}

    def _draw_report_text(self, img_path, w, h, draw_start) -> str:
        """生成出图小报告文案（尺寸 / 大小 / 耗时 / 时间），失败返回空串。

        抽成方法是因为图文消息需要在**发图之前**就把报告算出来（并入同一条消息），
        而旧逻辑是在发图之后单独发一条。
        """
        try:
            _st = os.stat(img_path)
            _ftime = time.strftime("%m-%d %H:%M:%S", time.localtime(_st.st_mtime))
            _kb = _st.st_size / 1024.0
            _size = f"{_kb / 1024.0:.2f} MB" if _kb >= 1024 else f"{_kb:.1f} KB"
            # 像素尺寸：优先读真实图片，环境无 Pillow 时回退到本次请求的宽高
            if _PILImage is not None:
                try:
                    with _PILImage.open(img_path) as _im:
                        _wh = f"{_im.width}×{_im.height}"
                except Exception:
                    _wh = f"{w}×{h}"
            else:
                _wh = f"{w}×{h}"
            _cost = time.time() - draw_start
            return random.choice(_DRAW_DONE_HINTS).format(
                ftime=_ftime, wh=_wh, size=_size, cost=f"{_cost:.1f}",
            )
        except Exception as _e:
            logger.warning(f"【出图·报告】 生成小报告失败（不影响出图）: {_e}")
            return ""

    def _build_image_caption(self, caption: str, report_text: str) -> str:
        """拼出随图发送的文字（配文 + 可选出图报告），按配置做长度截断。"""
        _parts = [p for p in ((caption or "").strip(), (report_text or "").strip()) if p]
        if not _parts:
            return ""
        _text = "\n".join(_parts)
        try:
            _max = int(self._cfg_image_caption().get("max_caption_chars", 200) or 0)
        except (TypeError, ValueError):
            _max = 0
        if _max > 0 and len(_text) > _max:
            _text = _text[:_max].rstrip() + "…"
        return _text

    def _share_base_url(self) -> str:
        cfg = self._cfg("share_webui", {}) or {}
        domain = (cfg.get("domain") or "").strip().rstrip("/")
        if domain:
            return domain
        sw = self._cfg("webui_standalone", {}) or {}
        host = (sw.get("host") or "127.0.0.1").strip()
        if host in ("0.0.0.0", "", "::"):
            host = "127.0.0.1"
        port = int(sw.get("port", 8848) or 8848)
        return f"http://{host}:{port}"

    def _make_share_qr(self, url: str, logo_path: str = "") -> bytes:
        import io
        try:
            import qrcode
        except Exception:
            qrcode = None
        if qrcode is None:
            return b""
        from PIL import Image as PILImage, ImageDraw
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        # 二维码前景色：默认插件主题粉 #ff8fb3，可通过 share_webui.qr_fill_color 配置
        _fill = "#ff8fb3"
        try:
            _c = (self._cfg("share_webui", {}).get("qr_fill_color") or "").strip()
            if _c:
                _fill = _c
        except Exception:
            pass
        try:
            img = qr.make_image(fill_color=_fill, back_color="white").convert("RGB")
        except Exception:
            img = qr.make_image(fill_color="#ff8fb3", back_color="white").convert("RGB")
        logo = None
        if logo_path and os.path.exists(logo_path):
            try:
                logo = PILImage.open(logo_path).convert("RGBA")
            except Exception:
                logo = None
        if logo is None:
            logo = self._make_default_share_logo()
        qw, qh = img.size
        lw = max(40, int(qw * 0.22))
        lh = max(40, int(qh * 0.22))
        logo = logo.resize((lw, lh))
        pad = max(6, int(lw * 0.08))
        box = (int((qw - lw) / 2), int((qh - lh) / 2))
        mask = PILImage.new("L", (lw + pad * 2, lh + pad * 2), 0)
        d = ImageDraw.Draw(mask)
        d.rounded_rectangle([0, 0, lw + pad * 2, lh + pad * 2], radius=pad * 2, fill=255)
        white = PILImage.new("RGBA", (lw + pad * 2, lh + pad * 2), (255, 255, 255, 255))
        img.paste(white, (box[0] - pad, box[1] - pad), mask)
        img.paste(logo, box, logo if logo.mode == "RGBA" else None)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    def _make_default_share_logo(self):
        """默认二维码中心 logo：优先用插件根目录 logo.png，缺失时画「萌」字兜底。"""
        try:
            _logo_path = Path(__file__).resolve().parent / "logo.png"
            if _logo_path.is_file():
                from PIL import Image as PILImage
                return PILImage.open(str(_logo_path)).convert("RGBA")
        except Exception:
            pass
        from PIL import Image as PILImage, ImageDraw, ImageFont
        size = 240
        img = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, size, size], radius=48, fill=(255, 143, 179, 255))
        try:
            font = ImageFont.truetype("msyh.ttc", 150)
        except Exception:
            try:
                font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 150)
            except Exception:
                font = ImageFont.load_default()
        text = "萌"
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text, fill=(255, 255, 255, 255), font=font)
        return img

    async def _send_display(self, event: AstrMessageEvent, text: str) -> None:
        """按图库配置的展示方式发送展示内容。

        gallery.display_mode == "render" 时，优先用 AstrBot 自带的文本转图片服务
        (text_to_image，官方 HTML 模板渲染美观、清晰)；若该服务不可用 / 返回空 /
        发送异常，再用本插件内置的 Pillow 渲染做兜底（仅防止完全无图可发），再失败回退文字。
        其他值（默认 text）直接发送文字。
        """
        if str(self._cfg("gallery", {}).get("display_mode", "text")).strip().lower() == "render":
            # 1) 优先：走 AstrBot 官方文本转图片服务，但用本插件自定义模板（大字号）+ 高清晰度渲染。
            #    AstrBot 官方 text_to_image 的默认 quality 只有 40，且字号由默认模板固定，
            #    导致渲染出的字小且发虚。这里用 html_render 传自定义模板文件（font-size 大）
            #    并把 quality 提到 90，字大又清晰，同时仍是官方渲染服务。
            try:
                _tmpl_path = Path(__file__).resolve().parent / "assets" / "gallery_t2i.html"
                _tmpl = _tmpl_path.read_text(encoding="utf-8") if _tmpl_path.is_file() else self._GALLERY_T2I_TMPL
                url = await self.html_render(
                    _tmpl,
                    {"text": self._gallery_text_to_table(text)},
                    return_url=True,
                    options={"full_page": True, "type": "jpeg", "quality": 90},
                )
                if url:
                    if url.startswith("http://") or url.startswith("https://"):
                        img_comp = Image.fromURL(url)
                    else:
                        img_comp = Image.fromFileSystem(url)
                    await event.send(MessageChain([img_comp]))
                    return
                self.logger.warning("【图库】 官方渲染服务返回空 URL，改用默认模板")
            except Exception as _e:
                try:
                    self.logger.warning(f"【图库】 官方自定义模板渲染失败，改用默认模板: {_e}")
                except Exception:
                    pass
            # 2) 兜底：AstrBot 官方默认模板（text_to_image）
            render_text = text.replace("\n", "<br>\n")  # 默认模板按 Markdown 渲染，\n 需转 <br>
            try:
                url = await self.text_to_image(render_text)
                if url:
                    if url.startswith("http://") or url.startswith("https://"):
                        img_comp = Image.fromURL(url)
                    else:
                        img_comp = Image.fromFileSystem(url)
                    await event.send(MessageChain([img_comp]))
                    return
                self.logger.warning("【图库】 默认模板渲染返回空 URL，改用 Pillow 兜底")
            except Exception as _e:
                try:
                    self.logger.warning(f"【图库】 默认模板渲染失败，改用 Pillow 兜底: {_e}")
                except Exception:
                    pass
            # 3) 兜底：本插件内置 Pillow 渲染
            render_path = self._render_gallery_text_pillow(text)
            if render_path:
                try:
                    await event.send(MessageChain([Image.fromFileSystem(render_path)]))
                    return
                except Exception as _e:
                    self.logger.warning(f"【图库】 Pillow 兜底渲染图发送失败，回退文字: {_e}")
            # 4) 最终回退：文字
            await self._send(
                event,
                text + "\n\n⚠ 渲染成图片失败（AstrBot 文本转图片服务与 Pillow 兜底均失败），已回退文字。请确认 AstrBot「文本转图片」服务已启用并选择了激活模板。",
            )
            return
        await self._send(event, text)

    def _gallery_text_to_table(self, text: str) -> str:
        """把图库列表/搜索/收藏文本中的「序号. 描述 | 工作流 | 时间 [| 用户]」行转成 Markdown 表格，
        供 html_render 的 marked 渲染成 HTML <table>；非表格行（标题/翻页/提示）保持原样。
        无匹配表格行时原样返回 text，不影响其他渲染内容。

        列结构：普通行只有 3 个数据列（描述|工作流|时间）；全库/管理员视图会追加
        「| 👤 用户名」作为第 4 个数据列。表格生成时按是否含有用户数据动态决定
        是否渲染「用户」列（无用户数据则不显示用户列）。解析时固定取最后 3 列作为
        「工作流/时间/用户」，其前所有列合并为描述，避免任何位置多出的「|」导致列错位。"""
        import re

        rows = []
        others = []
        for line in text.split("\n"):
            m = re.match(r"^(\d+)[\.、]\s*(.+)$", line.strip())
            if m and "|" in m.group(2):
                cells = [c.strip() for c in m.group(2).split("|")]
                if len(cells) >= 4:
                    # 4+ 列（全库/管理员带了用户列）：最后1列=用户，倒数2=时间，倒数3=类型，前面合并为描述。
                    desc = " ".join(cells[:-3]).strip()
                    typ, tm, user = cells[-3], cells[-2], cells[-1]
                else:
                    # 3 列（普通）：描述 | 类型 | 时间，用户为空。
                    cells = cells + [""] * (3 - len(cells)) if len(cells) < 3 else cells
                    desc, typ, tm, user = cells[0], cells[1], cells[2], ""
                rows.append((m.group(1), desc, typ, tm, user))
            else:
                others.append(line)
        if not rows:
            return text
        has_user = any((user or "").strip() for _, _, _, _, user in rows)
        if has_user:
            header = "| 序号 | 描述 | 工作流 | 时间 | 用户 |\n|---|---|---|---|---|"
            body = [f"| {no} | {desc} | {typ} | {tm} | {user} |" for no, desc, typ, tm, user in rows]
        else:
            header = "| 序号 | 描述 | 工作流 | 时间 |\n|---|---|---|---|"
            body = [f"| {no} | {desc} | {typ} | {tm} |" for no, desc, typ, tm, user in rows]
        table = "\n".join([header] + body)
        if not others:
            return table
        return "\n\n".join(others) + "\n\n" + table

    def _render_gallery_text_pillow(self, text: str, font_size: int = 32) -> str | None:
        """用 Pillow 把图库展示文字绘制成高清图片（解决 AstrBot 默认 t2i 字小发虚）。

        做法：2x 超采样（先在大尺寸画布上用大字号绘制，再缩放回目标尺寸）得到抗锯齿清晰字；
        白底深灰字，按字符宽度自动换行，兼容中英文；输出 PNG 到 data_dir 下的临时渲染目录。
        返回图片路径；Pillow 不可用 / 绘制失败 / 字体缺失时返回 None（调用方回退）。
        """
        if _PILImage is None:
            return None
        try:
            from PIL import ImageDraw, ImageFont

            scale = 2  # 超采样倍数
            # 超采样画布上应使用 font_size*scale 的字号，这样缩回 1x 后才是 font_size。
            # 之前直接用 font_size 在 2x 画布上画，缩回后字号减半（极小字），已修正。
            draw_font_size = font_size * scale

            # 找一款能显示中文的字体（按常见路径尝试，缺失则用默认位图字体，中文可能方块）
            font = None
            for _cand in (
                "C:/Windows/Fonts/msyh.ttc",          # Windows 微软雅黑
                "C:/Windows/Fonts/simhei.ttf",        # Windows 黑体
                "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/System/Library/Fonts/PingFang.ttc",  # macOS
            ):
                try:
                    font = ImageFont.truetype(_cand, draw_font_size)
                    break
                except Exception:
                    continue
            if font is None:
                try:
                    font = ImageFont.load_default()
                except Exception:
                    return None
            pad = 28 * scale
            line_h = int(font_size * 1.6 * scale)
            max_w = 760 * scale  # 内容区最大宽度（2x）

            # 单个字符宽度估算：emoji/异常字符用保守近似，避免 getlength 返回 0/异常导致整行折行错乱
            def _ch_w(ch: str) -> float:
                try:
                    w = font.getlength(ch)
                    if w and w > 0:
                        return w
                except Exception:
                    pass
                # 兜底：emoji/符号按约一个字宽估算，空白按 0.3 字宽
                if ch in (" ", "\t"):
                    return font_size * 0.3 * scale
                return font_size * 1.0 * scale

            # 逐字符折行（兼容中英文混排）；遇到超长无空格串（URL/sha）也强制硬断，避免溢出
            lines: list[str] = []
            for raw in text.split("\n"):
                if raw == "":
                    lines.append("")
                    continue
                cur = ""
                cur_w = 0
                for ch in raw:
                    w = _ch_w(ch)
                    if cur_w > 0 and cur_w + w > max_w:
                        lines.append(cur)
                        cur = ch
                        cur_w = w
                    else:
                        # 即便当前已是超长单词（无空格），也允许在超出 max_w 处硬断
                        if cur_w + w > max_w and cur:
                            lines.append(cur)
                            cur = ""
                            cur_w = 0
                        cur += ch
                        cur_w += w
                if cur:
                    lines.append(cur)

            content_h = line_h * len(lines)
            img_w = max_w + pad * 2
            img_h = content_h + pad * 2
            img_h = max(img_h, int(font_size * 4 * scale))

            big = _PILImage.new("RGB", (img_w, img_h), (255, 255, 255))
            draw = ImageDraw.Draw(big)
            ink = (33, 33, 33)
            y = pad
            for ln in lines:
                draw.text((pad, y), ln, font=font, fill=ink)
                y += line_h

            # 缩回目标尺寸（抗锯齿）
            out = big.resize((img_w // scale, img_h // scale), _PILImage.LANCZOS)

            render_dir = os.path.join(self.data_dir, "gallery_render")
            try:
                os.makedirs(render_dir, exist_ok=True)
            except Exception:
                pass
            out_path = os.path.join(render_dir, f"gallery_{int(time.time() * 1000)}.png")
            out.save(out_path, "PNG")
            return out_path
        except Exception as _e:
            try:
                self.logger.warning(f"【图库】 Pillow 渲染失败: {_e}")
            except Exception:
                pass
            return None

    @staticmethod
    def _wf_name(wf):
        """安全取工作流名：兼容 dict / None / 其他类型，避免下标/属性访问崩溃。"""
        if not wf:
            return ""
        if isinstance(wf, dict):
            return wf.get("name") or ""
        return getattr(wf, "name", "") or ""

    def _record_failed(
        self, event, positive, wf, is_img2img, ref_sha256, draw_start, reason,
        platform: str = "comfyui",
    ) -> None:
        """出图失败时写一条失败记录到图库（供 WebUI「出图记录」展示）。"""
        try:
            if self.gallery is None:
                return
            # 用户标识：用 get_sender_id() 获取真实用户ID（QQ群里为各用户QQ号），
            # 不能依赖 event.user_id（该属性通常不存在，会导致 user_id 恒为空、
            # 图库用户隔离失效、不同用户互相串图）。
            user_id = (getattr(event, "get_sender_id", lambda: "")() or "") if event is not None else ""
            user_name_fn = getattr(event, "get_sender_name", None) if event is not None else None
            user_name = (user_name_fn() if callable(user_name_fn) else "") or ""
            trigger = getattr(event, "message_str", "") or "" if event is not None else ""
            self.gallery.add_failed_record(
                prompt=(positive or ""),
                prompt_raw=(positive or ""),
                workflow=self._wf_name(wf),
                is_img2img=bool(is_img2img),
                ref_sha256=(ref_sha256 or ""),
                cost_sec=(time.time() - draw_start) if draw_start else None,
                user_id=user_id,
                user_name=user_name,
                trigger_msg=trigger,
                reason=(reason or ""),
                platform=platform,
            )
        except Exception as e:
            logger.warning(
                f"【图库】 写入失败记录出错（忽略）: {e} | "
                f"wf={type(wf).__name__} event={type(event).__name__}",
                exc_info=True,
            )

    def _gallery_retag(self, owner: str = "", all_view: bool = False, session_scope: str = "") -> str:
        """给存量图批量补打「表情包 / 漫画」标签：按各图所用工作流是否为漫画类
        （prompt_slots / boogu_node / kind=comic）判定标签。供 /图库 补标 命令与
        WebUI 调用。add_tags 幂等，重复执行无害。返回给用户看的结果文本。"""
        g = self.gallery
        if g is None:
            return "图库未启用或初始化失败"
        try:
            # 收集漫画类工作流名 -> 应补的标签（kind=comic 判为漫画，否则表情包）
            comic_names: dict[str, str] = {}
            for w in self._workflows():
                nm = (w.get("name") or "").strip().lower()
                if nm and self._workflow_kind(w) == "comic":
                    comic_names[nm] = "漫画" if (w.get("kind") or "").strip().lower() == "comic" else "表情包"
            if not comic_names:
                return "未识别到任何表情包/漫画类工作流，无法批量补标。"
            rows = g.search(owner=("" if all_view else owner), limit=200000, offset=0, session=session_scope)
            cnt = {"表情包": 0, "漫画": 0}
            for r in rows:
                nm = (r.get("workflow") or "").strip().lower()
                tag = comic_names.get(nm)
                if tag:
                    g.add_tags(r.get("sha256") or "", [tag])
                    cnt[tag] += 1
            return (
                f"✅ 已按工作流类型批量补打标签：表情包 {cnt['表情包']} 张、漫画 {cnt['漫画']} 张。\n"
                f"之后在图库点「表情包 / 漫画」分类即可筛选，或按标签输入「表情包」搜索。"
            )
        except Exception as _e:
            logger.warning(f"【图库】 补标失败: {_e}", exc_info=True)
            return f"补标失败：{_e}"

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        """把异常粗分类，用于挑选给用户看的可爱话术（connect/timeout/server/generic）。

        真实报错仍会被完整记入日志，这里只决定「对外话术」的类别。
        """
        name = type(exc).__name__
        text = f"{name}: {exc}".lower()
        connect_names = {
            "ClientConnectorError", "ClientConnectionError", "ClientOSError",
            "ServerDisconnectedError", "ServerConnectionError",
            "ConnectionRefusedError", "ConnectionResetError", "ConnectionError",
            "ConnectionAbortedError", "ClientConnectorDNSError",
        }
        timeout_names = {
            "TimeoutError", "ServerTimeoutError", "ConnectionTimeoutError",
            "SocketTimeoutError",
        }
        if name in connect_names:
            return "connect"
        if name in timeout_names or "timeout" in text or "timed out" in text:
            return "timeout"
        if name == "ClientResponseError" or any(
            k in text for k in ("status code", "http error", "500", "502", "503", "404")
        ):
            return "server"
        if any(
            k in text for k in (
                "cannot connect", "connect call failed", "connection refused",
                "connection reset", "getaddrinfo", "name or service not known",
                "network is unreachable", "connection", "unreachable",
            )
        ):
            return "connect"
        return "generic"

    def _friendly_error(
        self, exc: Exception, scene: str = "", category: str | None = None
    ) -> str:
        """把真实异常写入日志（含堆栈），并返回一条给用户看的可爱话术。

        category 不为空时强制使用该话术池（如工作流加载固定用「workflow」），
        否则按异常类型自动分类。
        """
        tag = f"[{scene}]" if scene else ""
        logger.error(
            f"【绘图·失败】{tag} {type(exc).__name__}: {exc}", exc_info=True
        )
        key = category or self._classify_error(exc)
        pool = _ERR_HINTS.get(key, _ERR_HINTS["generic"])
        return random.choice(pool)

    @staticmethod
    def _cute(key: str) -> str:
        """从对应话术池随机取一条（用于非异常的逻辑分支，如无图/无任务ID）。"""
        return random.choice(_ERR_HINTS.get(key, _ERR_HINTS["generic"]))

    # ------------------------------------------------------------------ #
    # 本地队列：避免依赖 ComfyUI 的 /queue 接口，仅按提交顺序统计待处理任务
    # ------------------------------------------------------------------ #
    @staticmethod
    def _server_key(server: dict) -> str:
        return str(server.get("name") or server.get("url") or "default")

    def _local_queue_ahead(self, key: str) -> int:
        """当前服务器上、本次提交之前已排队的任务数量（即"前面还有几位"）。"""
        return len(self._server_pending.get(key, []))

    def _local_queue_add(self, key: str, prompt_id: str) -> None:
        self._server_pending.setdefault(key, []).append(prompt_id)

    def _local_queue_remove(self, key: str, prompt_id: str) -> None:
        lst = self._server_pending.get(key)
        if lst and prompt_id in lst:
            lst.remove(prompt_id)

    def _queue_hint(self, ahead: int) -> str:
        """提交后的可爱随机提示：无队列时只说在出图，有队列时说明前面几位。"""
        if ahead <= 0:
            return random.choice(_QUEUE_HINTS_GENERATING)
        return random.choice(_QUEUE_HINTS_QUEUED).format(n=ahead)

    # ------------------------------------------------------------------ #
    # 多平台生图：第三方平台链路（NAI / OpenAI 兼容 / 自定义）
    # docs/multi-platform-image-plan.md。ComfyUI 主流程零改动；本协程与主流程
    # 同构（生成 → 归档 → 群聊 NSFW → 图文消息发送），仅图片来源不同。
    # ------------------------------------------------------------------ #

    def _platform_store(self):
        store = getattr(self, "_platform_store_inst", None)
        if store is None:
            try:
                from .platform_store import PlatformStore
            except ImportError:
                from platform_store import PlatformStore
            store = PlatformStore(self.data_dir)
            self._platform_store_inst = store
        return store

    async def _do_draw_nai_style(
        self,
        event: AstrMessageEvent,
        plat: dict,
        positive: str,
        negative: str,
        width: int | None,
        height: int | None,
        seed: int | None,
        *,
        notify_pending: bool = True,
        source: str = "",
        caption: str = "",
        draw_start: float | None = None,
        user_id: str = "",
        user_name: str = "",
    ):
        """第三方平台（NAI / OpenAI 兼容 / 自定义）出图。yield 契约与 _do_draw 一致。"""
        ptype = (plat.get("type") or "openai").strip()
        pname = (plat.get("name") or ptype)
        _draw_start = draw_start or time.time()
        _session_id = (getattr(event, "session_id", "") or "") if event is not None else ""
        logger.info(
            f"【绘图·开始】平台={ptype}({pname}) session={_session_id} 来源={source or '(原生)'}"
        )

        # 负面词：用户/调用方没给时，合并「负面词模板」里启用的条目
        if not (negative or "").strip():
            negative = self._platform_store().enabled_negative_text()
        # 画师串（NAI 专属语义；取第一个启用预设）
        artist = ""
        if ptype == "nai":
            _presets = self._platform_store().artist_presets(enabled_only=True)
            if _presets:
                artist = (_presets[0].get("content") or "").strip()
        # 尺寸：显式传参 > 平台默认档位 > NAI 竖图兜底
        _w, _h = width, height
        if not _w or not _h:
            try:
                try:
                    from .platform_store import resolve_nai_size
                except ImportError:
                    from platform_store import resolve_nai_size
                defaults = plat.get("defaults") or {}
                _size_key = str(defaults.get("size") or (plat.get("size") or "portrait"))
                _resolved = resolve_nai_size(_size_key)
                if _resolved:
                    _w = _w or _resolved[0]
                    _h = _h or _resolved[1]
            except Exception as _se:
                logger.warning(f"【平台】 尺寸解析失败（用默认 832x1216）: {_se}")
        _w = int(_w or 832)
        _h = int(_h or 1216)
        # 种子：未指定则随机（归档/日志需要真实种子）
        try:
            seed = int(seed) if seed else 0
        except (TypeError, ValueError):
            seed = 0
        if seed <= 0:
            seed = random.randint(0, 2**31 - 1)
        model = (plat.get("model") or "").strip()

        # 生图请求超时：平台自身 timeout 优先，否则全局 platform_gen_timeout（默认 180s）
        _plat_to = plat.get("timeout")
        try:
            _global_to = int(self._cfg("platform_gen_timeout", 180) or 180)
        except Exception:
            _global_to = 180
        try:
            _eff_timeout = float(_plat_to) if _plat_to not in (None, "", 0) else _global_to
        except Exception:
            _eff_timeout = _global_to

        # 提交生成
        try:
            try:
                from . import nai_client
            except ImportError:
                import nai_client
            images = await nai_client.generate(
                plat, prompt=positive, negative=negative,
                width=_w, height=_h, seed=seed, count=1, artist=artist,
                timeout=_eff_timeout,
            )
        except Exception as e:
            logger.warning(f"【绘图·失败】[平台 {ptype}] {e}")
            await self._send(event, self._friendly_error(e, f"平台「{pname}」生图"))
            self._record_failed(
                event, positive, {"name": f"[平台 {pname}]"}, False, None,
                _draw_start, f"平台生图失败: {e}", platform=ptype,
            )
            return
        if not images:
            await self._send(event, self._cute("no_image"))
            self._record_failed(
                event, positive, {"name": f"[平台 {pname}]"}, False, None,
                _draw_start, "平台未返回图片（无图）", platform=ptype,
            )
            return

        for data in images:
            tmp_path = self.temp_dir / f"{uuid.uuid4().hex}.png"
            with open(tmp_path, "wb") as f:
                f.write(data)
            img_path = str(tmp_path)
            _send_img_path = img_path

            # 图库归档（platform/model/negative/extra，与 ComfyUI 链路同构）
            if self.gallery is not None:
                try:
                    try:
                        from .image_store import SRC_GEN, _sha256_of
                    except ImportError:
                        from image_store import SRC_GEN, _sha256_of
                    _real_w, _real_h = _w, _h
                    if _PILImage is not None:
                        try:
                            with _PILImage.open(img_path) as _im:
                                _real_w, _real_h = _im.width, _im.height
                        except Exception:
                            pass
                    _group_name = ""
                    try:
                        _gid = str(getattr(event, "get_group_id", lambda: "")() or "")
                        _get_group = getattr(event, "get_group", None)
                        if _gid and callable(_get_group):
                            _grp = await asyncio.wait_for(_get_group(_gid), timeout=3)
                            _group_name = str(getattr(_grp, "group_name", "") or "")
                    except Exception:
                        _group_name = ""
                    _extra = {}
                    _defaults = plat.get("defaults") or {}
                    for _k in ("steps", "scale", "cfg_rescale", "sampler", "noise_schedule"):
                        if _defaults.get(_k) is not None:
                            _extra[_k] = _defaults.get(_k)
                    # 顶部 cfg/steps 列：NAI 引导系数官方叫 scale，defaults 里 cfg/scale 都可能存在，
                    # 取其一填入 cfg 列（大图详情的「CFG」行即可显示）；steps 同理。
                    _cfg_val = _defaults.get("cfg")
                    if _cfg_val is None:
                        _cfg_val = _defaults.get("scale")
                    _steps_val = _defaults.get("steps")
                    _final = self.gallery.archive_image(
                        img_path,
                        source=SRC_GEN,
                        prompt=positive,
                        prompt_raw=positive,
                        workflow="",
                        loras=[],
                        seed=seed,
                        w=_real_w,
                        h=_real_h,
                        is_img2img=False,
                        cfg=_cfg_val,
                        steps=_steps_val,
                        platform=ptype,
                        model=model,
                        negative=(negative or ""),
                        platform_name=plat.get("name") or "",
                        extra=_extra,
                        size_bytes=(os.path.getsize(img_path) if os.path.exists(img_path) else None),
                        cost_sec=(time.time() - _draw_start),
                        user_id=user_id or ((getattr(event, "get_sender_id", lambda: "")() or "") if event is not None else ""),
                        user_name=user_name or ((getattr(event, "get_sender_name", lambda: "")() or "") if event is not None else ""),
                        session_id=_session_id,
                        group_id=(getattr(event, "get_group_id", lambda: "")() or "") if event is not None else "",
                        group_name=_group_name,
                        trigger_msg=(getattr(event, "message_str", "") or "") if event is not None else "",
                        status=0,
                        on_dedup=lambda _sha, _uc: self._oplog_dedup(
                            _sha, _uc, user_id, user_name, event
                        ),
                    )
                    if _final:
                        img_path = _final
                        _send_img_path = _final
                except Exception as _ge:
                    logger.warning(f"【图库】 平台图归档失败（不影响发送）: {_ge}")

            # 群聊 NSFW 检测（与 ComfyUI 链路同构；拦截则不发也不入库记录）
            if not self._is_private_event(event):
                _nsfw_thr = 0.5
                try:
                    if getattr(self, "gallery", None) is not None:
                        _nsfw_thr = self.gallery._nsfw_threshold()
                except Exception:
                    pass
                try:
                    from .nsfw_detector import get_detector
                except ImportError:
                    from nsfw_detector import get_detector
                _det = get_detector(_nsfw_thr)
                try:
                    _is_nsfw, _nsfw_score, _nsfw_avail = (
                        await asyncio.to_thread(_det.detect, _send_img_path)
                    )
                except Exception as _e:
                    logger.warning(f"【NSFW】 平台出图群聊检测异常: {_e}")
                    _is_nsfw, _nsfw_avail = True, False
                if _is_nsfw:
                    _sc = f"（置信度 {_nsfw_score:.2f}）" if isinstance(_nsfw_score, (int, float)) else ""
                    _reason = "（检测不可用，已按最严策略拦截）" if not _nsfw_avail else ""
                    logger.warning(f"【NSFW】 平台出图被拦截{_sc}{_reason} platform={ptype} path={_send_img_path}")
                    await self._send(event, f"这张图被标记为 NSFW{_sc}，不能发到群里哦～ 已为你拦截。{_reason}")
                    continue

            # 发送（图文消息 caption 与 ComfyUI 链路同构）
            _cap_text = self._build_image_caption(caption, "") if caption else ""
            if _cap_text:
                _cap_result = event.chain_result(
                    [Plain(text=_cap_text), Image.fromFileSystem(_send_img_path)]
                )
                try:
                    _cap_result.use_t2i_ = False
                except Exception:
                    pass
                yield _cap_result, _send_img_path
            else:
                yield event.image_result(_send_img_path), _send_img_path

        logger.info(
            f"【绘图·完成】平台={ptype}({pname}) seed={seed} 尺寸={_w}x{_h} "
            f"耗时={time.time() - _draw_start:.1f}s"
        )

    async def _do_draw(
        self,
        event: AstrMessageEvent,
        workflow_name: str | None,
        positive: str,
        negative: str,
        width: int | None,
        height: int | None,
        lora_map: dict[str, float | None] | None,
        lora_presets: dict[str, str] | None = None,
        seed: int | None = None,
        init_images: list[str] | None = None,
        is_img2img: bool = False,
        denoise: float | None = None,
        trigger_words: str | None = None,
        platform: str = "",
        notify_pending: bool = True,
        source: str = "",
        explicit_default: bool = False,
        slot_values: dict | None = None,
        comic_feature: str | None = None,
        caption: str = "",
    ):
        # 备份原始提示词：后续可能被翻译/改写（动漫翻译、第三方改写），
        # 但「尺寸比例」触发需基于用户原始文本（竖版/横版/9:16 等词）。
        _ratio_src = positive or ""
        # 记录最近一次事件，供 LLM 工具在 event 异常时为兜底使用
        self._last_event = event
        # 出图计时起点（用于生成完成后的耗时报告）
        _draw_start = time.time()
        # [TRACE] 绘图 LLM 调用链路追踪：为本会话本次绘图生成唯一 trace_id，
        # 串起「主对话 agent_draw(核心层) + 翻译/改写」等所有 LLM 调用，便于 grep
        # 统计一次绘图实际经过几个 LLM。格式固定前缀 [DRAW-LLM]。
        import uuid as _uuid
        _trace_id = _uuid.uuid4().hex[:8]
        _session_id = (getattr(event, "session_id", "") or "") if event is not None else ""
        if _session_id:
            g_draw_agent_sessions[_session_id] = self._resolve_translate_provider_id() or ""
        logger.info(
            f"【绘图·开始】trace={_trace_id} session={_session_id} "
            f"来源={source or '(原生)'} 图生图={is_img2img}"
        )
        # 图生图参考图的 sha256（归档成品图时回填到 ref_sha256 字段）
        ref_sha256 = None
        # 用户标识（成品图归档用）：用 get_sender_id() 取真实用户ID，避免归档成"无主图"
        user_id = (getattr(event, "get_sender_id", lambda: "")() or "") if event is not None else ""
        user_name_fn = getattr(event, "get_sender_name", None) if event is not None else None
        user_name = (user_name_fn() if callable(user_name_fn) else "") or ""
        # 绘图权限总闸（白名单优先，再黑名单）。白名单启用时只看白名单，
        # 未启用/空则走黑名单。两者互斥：白名单内用户即便在黑名单也放行。
        if self._is_whitelist_active():
            _perm_ok, _perm_reason = self._check_whitelist(event)
        else:
            _perm_ok, _perm_reason = self._check_blacklist(event)
        if not _perm_ok:
            logger.info(f"【绘图·解析】 权限拦截（白名单={self._is_whitelist_active()}）：user={user_id or '(unknown)'} group={getattr(event, 'get_group_id', lambda: '')() or ''}")
            await self._send(event, _perm_reason)
            return
        # 生图次数限制：全局/按用户配额校验（管理员可豁免）
        _ok, _reason = self._check_draw_limit(event)
        if not _ok:
            logger.info(f"【绘图·解析】 用户 {user_id or '(unknown)'} 触发生图限额，拒绝：{_reason}")
            await self._send(event, _reason)
            return
        # ── 多平台生图分流（docs/multi-platform-image-plan.md）────────────
        # active_platform 非 comfyui 且该平台可用时，走第三方平台链路
        # （NAI / OpenAI 兼容 / 自定义），ComfyUI 主流程一行不动。
        # 第三方平台不支持 LoRA/工作流/图生图，相关参数在此链路自动忽略。
        try:
            _plat = self._platform_store().pick_platform(
                platform, user_id=user_id, is_admin=self._is_admin(event),
                session_id=_session_id,
            )
        except Exception as _pe:
            logger.warning(f"【平台】 平台解析失败（回退 ComfyUI）: {_pe}")
            _plat = None
        if _plat is not None:
            async for _pn, _pp in self._do_draw_nai_style(
                event, _plat, positive, negative, width, height, seed,
                notify_pending=notify_pending, source=source,
                caption=caption, draw_start=_draw_start,
                user_id=user_id, user_name=user_name,
            ):
                yield _pn, _pp
            return
        try:
            # fallback_on_missing=True：绘图真正入口可能收到伴侣/LLM 传入的无效工作流名
            # （如 "ComfyUI default"），此时不报错中断，容错回退到配置的默认工作流。
            # intercept_disabled：只要本次请求**显式指定**了工作流名（workflow_name 非空），
            # 该工作流若已停用就直接拦截提示，避免用户/LLM 指定的停用工作流被静默替换。
            wf = self._resolve_workflow(
                workflow_name,
                is_img2img=is_img2img,
                fallback_on_missing=True,
                positive=positive,
                explicit_default=explicit_default,
                intercept_disabled=bool(workflow_name and workflow_name.strip()),
            )
            logger.info(
                f"【绘图·解析】 解析工作流：请求名={workflow_name!r}, is_img2img={is_img2img}, "
                f"实际选用工作流={wf.get('name')!r}（server={wf.get('server_name')!r}）"
            )
            server = self._resolve_server(wf.get("server_name") or None)
        except ValueError as e:
            # 配置类问题：原因是插件自己给出的可读文案，直接说明
            msg = str(e)
            if "已停用" in msg:
                logger.warning(f"【绘图·失败】{msg}")
                await self._send(event, msg)
            else:
                logger.warning(f"【绘图·失败】[配置] {e}")
                await self._send(event, f"绘图配置有误：{e} 请联系管理员调整。")
            return

        # —— 无提示词 / 固定提示词工作流支持 ——
        # 图生图工作流（配置了参考图节点 image_node）必须要有参考图（消息图或引用图），
        # 否则直接拦截并提示，绝不回退默认工作流，也不允许无参考图的图生图裸跑。
        if (wf.get("image_node") or "").strip() and not init_images:
            _wf_n = wf.get("name") or "该工作流"
            logger.info(f"【绘图·解析】 工作流「{_wf_n}」需要参考图但未传图/未引用图，拦截")
            await self._send(
                event,
                f"「{_wf_n}」需要参考图哦～ 请在消息里附带一张图片，或引用一条带图片的消息后再试。",
            )
            return

        # 固定提示词标记：提示词来自工作流配置（default_positive/negative），
        # 视为作者精心写好的内容，跳过翻译 / LLM 改写 / LoRA 预设与触发词注入，
        # 避免动态改写破坏固定配方。
        _fixed_prompt = False
        _require_prompt = bool(wf.get("require_prompt", True))
        _default_pos = (wf.get("default_positive") or "").strip()
        _default_neg = (wf.get("default_negative") or "").strip()

        if not _require_prompt:
            # 工作流锁定提示词：用户传的提示词静默忽略（不替换、不报错）。
            if _default_pos:
                positive = _default_pos
                _fixed_prompt = True
                logger.info(f"【提示词】 工作流「{wf.get('name')}」锁定提示词，使用配置的固定正向提示词")
            else:
                # 未配置固定提示词 → 走工作流 JSON 文件内固化好的提示词（不注入覆盖）
                positive = ""
                logger.info(f"【提示词】 工作流「{wf.get('name')}」锁定提示词且未配置固定提示词，沿用工作流 JSON 内的提示词")
        else:
            # 不锁定提示词：用户传了用用户的；没传则用固定提示词兜底；都没有则拦截。
            if not positive or not positive.strip():
                if _default_pos:
                    positive = _default_pos
                    _fixed_prompt = True
                    logger.info(f"【提示词】 用户未提供提示词，使用工作流固定正向提示词")
                else:
                    await self._send(event, "请提供正向提示词，例如：/draw 一只白色水手服少女")
                    return

        # 负向提示词：用户未传负向时，若工作流配置了固定负向，则用它覆盖 JSON 原值。
        if (not negative or not negative.strip()) and _default_neg:
            negative = _default_neg
            logger.info(f"【提示词】 用户未提供负向提示词，使用工作流固定负向提示词")

        # 加载工作流 JSON
        self._cleanup_temp()
        client = self._build_client(server)
        try:
            prompt = workflow_builder.load_workflow(
                self._resolve_workflow_path(wf), wf.get("workflow_json")
            )
        except Exception as e:
            await self._send(event, self._friendly_error(e, "工作流加载", "workflow"))
            return

        # 图生图：把参考图注入到工作流的 LoadImage 节点
        if init_images:
            load_node = wf.get("image_node") or workflow_builder.find_image_loader_node(
                prompt
            )
            if not load_node:
                # 没找到图加载节点：详细原因（疑似节点、image_node 配置建议）只写日志，
                # 给用户发简洁提示即可，不要把内部配置细节暴露给用户。
                candidates = [
                    f"{nid}({node.get('class_type','?')})"
                    for nid, node in prompt.items()
                    if isinstance(node, dict)
                    and any(
                        k in (node.get("class_type") or "")
                        for k in ("LoadImage", "ImageLoader", "LoadImageFromPath")
                    )
                ]
                hint = f" 疑似图加载节点：{', '.join(candidates)}" if candidates else ""
                wf_name = (wf.get("name") or "未知工作流") if isinstance(wf, dict) else "未知工作流"
                logger.warning(
                    f"[图生图] 工作流「{wf_name}」没有 LoadImage 类的图加载节点，无法做图生图。"
                    f"可在插件配置的 image_node 手动填参考图节点的键名。{hint}"
                )
                await self._send(event, "没找到对应的画图流程（当前图生图工作流不支持）。")
                return
            try:
                # GIF 图生图只取第一帧转静态图，避免 ComfyUI 把动图多帧全部展开
                # 导致「连续发送很多张图片」。后续上传/注入/归档都用首帧静态图。
                _init_images = []
                for _raw in init_images:
                    if _raw and _raw.lower().endswith(".gif"):
                        _raw = await _gif_to_first_frame(_raw)
                    _init_images.append(_raw)
                init_images = _init_images

                for img_path in init_images:
                    info = await client.upload_image(img_path)
                    # ComfyUI 标准 LoadImage 节点在 /prompt API 下，image 输入期望「字符串文件名」
                    # （不是 [name, subfolder, type] 三元组——三元组是节点间连线的引用格式，
                    # 当作单个 image 输入框的值会直接导致 400 Bad Request）。
                    # 上传接口已把图片写到 type=input 目录，这里只传文件名即可。
                    image_name = (
                        info.get("name")
                        or info.get("filename")
                        or os.path.basename(img_path)
                    )
                    workflow_builder.set_image_node(prompt, load_node, image_name)
                    logger.info(
                        f"已注入参考图到节点 {load_node}: {img_path} -> {image_name}"
                    )
            except Exception as e:
                await self._send(event, self._friendly_error(e, "上传参考图"))
                return
            logger.info(f"图生图：已注入 {len(init_images)} 张参考图")

            # 图库：把参考图（用户发来的原图）归档到 refs/，并记录其 sha256 供成品图回链
            if self.gallery is not None:
                try:
                    try:
                        from .image_store import SRC_REF, SRC_USER, _sha256_of
                    except ImportError:
                        from image_store import SRC_REF, SRC_USER, _sha256_of

                    for _ri in init_images:
                        if not _ri or not os.path.exists(_ri):
                            continue
                        # 参考图优先按 user（用户发来的合照等）归档，便于「合照」类召回；
                        # 必须带 user_id，否则该图成为"无主图"会串给其他用户。
                        _final = self.gallery.archive_image(_ri, source=SRC_USER, user_id=user_id, user_name=user_name, session_id=(getattr(event, "session_id", "") or ""))
                        # archive_image 现返回归档后路径，反算 sha 作为 ref_sha256（入库/回填用）
                        _sha = _sha256_of(_final) if _final else None
                        if _sha and ref_sha256 is None:
                            ref_sha256 = _sha
                        logger.info(f"【图库】 已归档参考图: {_ri} -> {_final}")
                except Exception as _re:
                    logger.warning(f"【图库】 参考图归档失败（不影响出图）: {_re}")

        # 第三方插件调用（source 非空，如伴侣插件）：提示词往往是中英混杂的结构化
        # 描述（夹带 [User image request] 等标记），需要 LLM 清理/改写为对应风格。
        # - 动漫工作流（is_anima=true）：改写为纯英文 Anima 标签（强制动漫风格）。
        # - 真人/写实工作流（is_anima=false）：清理结构标记、统一为中文写实提示词。
        # 原生调用（source 为空）：仅动漫工作流含中文时按翻译模式处理中文片段。
        # 固定提示词（来自 default_positive，_fixed_prompt=True）跳过改写/翻译，
        # 视为作者精心写好的内容，不做任何动态加工。
        if source and not _fixed_prompt:
            try:
                if wf.get("is_anima"):
                    logger.info(f"【绘图·LLM①】trace={_trace_id} 阶段=改写为Anima提示词 第三方插件调用进入LLM")
                    rewritten = await self._rewrite_to_anima_llm(positive)
                    if rewritten and rewritten.strip():
                        positive = rewritten
                        logger.info(f"【Anima】 第三方插件调用，LLM 改写为 Anima 提示词: {positive}")
                else:
                    logger.info(f"【绘图·LLM②】trace={_trace_id} 阶段=清理为写实提示词 第三方插件调用进入LLM")
                    rewritten = await self._rewrite_to_real_llm(positive)
                    if rewritten and rewritten.strip():
                        positive = rewritten
                        logger.info(f"【写实】 第三方插件调用，LLM 清理为写实提示词: {positive}")
            except Exception as e:
                logger.warning(f"【提示词】 LLM 改写失败，保留原提示词: {e}")
        elif not _fixed_prompt and wf.get("is_anima") and self._has_chinese(positive):
            logger.info(f"【绘图·LLM③】trace={_trace_id} 阶段=翻译中文提示词 原生调用含中文进入LLM")
            translated = await self._translate_prompt(wf, positive)
            if translated:
                positive = translated
                logger.info(f"Anima 提示词翻译结果: {positive}")

        # 注入 LoRA 预设提示词（--名称/预设名）：追加到正/负向提示词。
        # 固定提示词或走 JSON 原值（positive 为空）时跳过，避免污染固定配方。
        if lora_presets and positive and not _fixed_prompt:
            positive, negative = self._apply_lora_presets(lora_presets, positive, negative)

        # 注入提示词（正/负下输入框名固定为 text，无需配置）。
        # 仅当提示词非空时注入：锁定提示词且未配置固定提示词时 positive 为空，
        # 此时保留工作流 JSON 内固化好的提示词原值，绝不用空串覆盖。
        if positive:
            logger.info(f"正向提示词: {positive}")
            workflow_builder.set_text_node(
                prompt, wf.get("positive_node"), "text", positive
            )
        if negative:
            logger.info(f"负向提示词: {negative}")
            workflow_builder.set_text_node(
                prompt, wf.get("negative_node"), "text", negative
            )

        # 注入宽高（宽高同属一个节点）；图生图时尺寸由参考图决定，跳过注入
        # 尺寸比例（全局 draw_ratio）：用户未显式指定宽高（width/height 均为空）时，
        # 若用户原始提示词里命中某个比例关键词（竖版/横版/9:16 等），则用该比例的配置尺寸，
        # 优先于工作流默认尺寸；用户显式给了宽高则始终以用户为准。
        _ratio_w, _ratio_h = self._resolve_ratio_size(_ratio_src, width, height)
        w = _ratio_w if _ratio_w is not None else (width or int(wf.get("default_width", 512) or 512))
        h = _ratio_h if _ratio_h is not None else (height or int(wf.get("default_height", 512) or 512))
        # resolution_mode 决定宽高的注入范围（默认 single，与旧行为逐字一致）：
        #   single：仅注入 resolution_node；留空则自动探测「第一个」EmptyLatentImage
        #   all   ：注入「所有」EmptyLatentImage —— 两阶段串联工作流（如 anima 生图
        #           → boogu 编辑）前后各有一个 latent，尺寸必须同步，否则构图被拉伸
        #   none  ：完全不注入，沿用工作流 JSON 原值 —— 多格拼接等尺寸固定的工作流
        if init_images:
            res_nodes: list = []
        else:
            _res_mode = (wf.get("resolution_mode") or "single").strip().lower()
            if _res_mode == "none":
                res_nodes = []
            elif _res_mode == "all":
                res_nodes = workflow_builder.find_all_nodes_by_class(
                    prompt, "EmptyLatentImage"
                )
                logger.info(f"【宽高】 resolution_mode=all，同步节点: {res_nodes or '无'}")
            else:
                _one = wf.get("resolution_node") or ""
                if not _one:
                    # 未配置宽高节点时自动探测 EmptyLatentImage
                    _one = workflow_builder.find_node_by_class(
                        prompt, "EmptyLatentImage"
                    )
                res_nodes = [_one] if _one else []
        width_field = wf.get("resolution_width_field", "width") or "width"
        height_field = wf.get("resolution_height_field", "height") or "height"
        for _rn in res_nodes:
            workflow_builder.set_number_node(prompt, _rn, width_field, w)
            workflow_builder.set_number_node(prompt, _rn, height_field, h)

        # 注入 LoRA（合并关键词自动匹配）
        loras_cfg = self._loras_of(wf)
        logger.info(
            f"LoRA 配置解析（工作流「{wf.get('name')}」）: "
            + json.dumps(
                [
                    {
                        "name": l.get("name"),
                        "model_name": l.get("model_name"),
                        "weight": l.get("weight"),
                        "enabled": l.get("enabled"),
                        "load_node": l.get("load_node"),
                    }
                    for l in (loras_cfg or [])
                ],
                ensure_ascii=False,
            )
        )
        if lora_map is None:
            auto = workflow_builder.collect_keyword_loras(loras_cfg, positive)
            # 默认启用项 + 关键词命中的项
            merged: dict[str, float | None] = {}
            for lora in loras_cfg:
                nm = (lora.get("name") or "").strip()
                if not nm:
                    continue
                if lora.get("enabled") or nm in auto:
                    merged[nm] = None
            active_map = merged or None
        else:
            # 用户显式指定了 LoRA（指令 --名称 / LLM 工具的 loras 参数）时，
            # 默认行为：在「工作流自带且已启用」的 LoRA 基础上【叠加】用户请求的 LoRA，
            # 而非整体替换。工作流预设的 LoRA 是其风格配方的一部分，不应被一票否决。
            # （用户请求的权重若与自带项同名，则覆盖自带权重；其余自带项保留。）
            active_map = {}
            for lora in loras_cfg:
                nm = (lora.get("name") or "").strip()
                if not nm:
                    continue
                if lora.get("enabled"):
                    active_map.setdefault(nm, None)
            for nm, w in lora_map.items():
                nm = (nm or "").strip()
                if not nm:
                    continue
                active_map[nm] = w
        logger.info(f"LoRA active_map（本次实际请求启用）: {active_map}")

        # 补全：--名称 临时请求的 LoRA，若工作流未预引用（loras_config 里没有该项），
        # 则从全局 LoRA 库里取完整配置（含真实 model_name）补进 loras_cfg。否则
        # apply_loras 因无配置项可遍历而不会注入任何节点，表现为「LoraLoader 节点: 无 /
        # 本次最终启用: 无」——这正是「/draw --安魂曲 没加上」的根因（工作流没引用安魂曲）。
        if active_map:
            lib = self._lora_lib_index()
            for cmd_name in active_map:
                if any(
                    workflow_builder._lora_name_matches(
                        (l.get("name") or "").strip(), cmd_name
                    )
                    for l in (loras_cfg or [])
                ):
                    continue
                lib_l = lib.get(cmd_name) or next(
                    (
                        v
                        for k, v in lib.items()
                        if workflow_builder._lora_name_matches(k, cmd_name)
                    ),
                    None,
                )
                if lib_l:
                    w = active_map.get(cmd_name)
                    loras_cfg = list(loras_cfg or []) + [
                        {
                            "name": cmd_name,
                            "model_name": (lib_l.get("model_name") or "").strip(),
                            "model_only": bool(lib_l.get("model_only", True)),
                            "weight": (
                                self._safe_lora_weight(lib_l.get("weight", 1.0))
                                if w is None
                                else self._safe_lora_weight(w)
                            ),
                            "enabled": True,
                            "load_node": "",
                        }
                    ]
                    logger.info(
                        f"【LoRA】 从全局 LoRA 库补全临时启用的「{cmd_name}」"
                        f"（工作流未预引用；文件={lib_l.get('model_name')}）"
                    )
                else:
                    lib_keys = list(lib.keys())
                    logger.warning(
                        f"【LoRA 提示】本次请求启用「{cmd_name}」，但工作流未引用且全局 LoRA 库"
                        f"里也找不到该名称。请先在全局「LoRA 库」配置「{cmd_name}」并填好"
                        f"model_name（真实 .safetensors 文件名），否则无法注入。"
                        f"（诊断：全局 LoRA 库索引共 {len(lib_keys)} 个键，"
                        f"含『{cmd_name}』={'是' if cmd_name in lib_keys else '否'}，"
                        f"样例键={lib_keys[:8]}）"
                    )

        # 常驻预设：启用的 LoRA 若配置了名为「0」的预设，则无论用户是否指定其它
        # 预设（--名称/预设名）都自动带上。先排除用户已显式指定「0」的，避免重复。
        if active_map:
            always_pre: dict[str, str] = {}
            lib_pre = self._lora_lib_index()
            for lora_name in active_map:
                ln = (lora_name or "").strip()
                l = lib_pre.get(ln) or next(
                    (v for k, v in lib_pre.items()
                     if workflow_builder._lora_name_matches(k, ln)),
                    None,
                )
                if not l:
                    continue
                for p in self._parse_presets(l.get("presets")):
                    if (p.get("name") or "").strip() == "0":
                        if not (lora_presets and (lora_presets.get(ln) or "").strip() == "0"):
                            always_pre[ln] = "0"
                        break
            # 固定提示词或走 JSON 原值（positive 为空）时跳过常驻预设追加，避免污染固定配方
            if always_pre and positive and not _fixed_prompt:
                positive, negative = self._apply_lora_presets(
                    always_pre, positive, negative
                )
                logger.info(f"【LoRA】 已追加常驻预设（名为0）：{list(always_pre.keys())}")
                # positive 已变更，需重写正向提示词节点（上方 565/570 处已写过一次，此处覆盖）
                workflow_builder.set_text_node(
                    prompt, wf.get("positive_node"), "text", positive
                )
                logger.info(f"正向提示词（含常驻预设）: {positive}")

        enabled = workflow_builder.apply_loras(
            prompt, loras_cfg, active_map, anchor=wf.get("lora_anchor") or None,
            clip_anchor=wf.get("lora_clip") or None,
            on_warning=lambda m: logger.warning(m),
            on_info=lambda m: logger.info(m),
            model_only=True,
        )
        if enabled:
            logger.info(f"本次启用的 LoRA: {enabled}")
            # 仅记录到日志（含加载的文件名），不再回显给用户。
            for nm in enabled:
                cfg = next(
                    (l for l in (loras_cfg or []) if (l.get("name") or "").strip() == nm),
                    None,
                )
                mn = (cfg or {}).get("model_name") or "" if cfg else ""
                if mn:
                    logger.info(f"【LoRA】 启用 {nm} → {mn}")
                else:
                    # 诊断：本次 LoRA 在全局库里到底有没有、库内 model_name 是啥，
                    # 用于区分「旧代码未重载 / 名字不在库 / 库里 model_name 真为空」三种情况。
                    _diag_lib = self._lora_lib_index()
                    _diag_l = _diag_lib.get(nm)
                    _diag_mn = (_diag_l or {}).get("model_name") or ""
                    logger.warning(
                        f"【LoRA】 启用 {nm} → 未配置 model_name，节点沿用工作流默认文件（可能不是该 LoRA）。"
                        f"（诊断：全局库含『{nm}』={'是' if _diag_l else '否'}，"
                        f"库内 model_name={'已填(' + _diag_mn + ')' if _diag_mn else '空'}）"
                    )

        # 自动追加 LoRA 触发词到正向提示词：每个启用的 LoRA 配置的 trigger_words
        # 会被写入 positive（去重，仅追加缺失的词），否则只加了 LoRA 节点却没触发词，
        # 出图效果会偏离预期。仅当启用了 LoRA 且确实有触发词时才处理。
        # 触发词来源分两级（v5.7.6）：
        #   a) LLM 调 comfyui_draw 时显式传了 trigger_words（画图时已按用户需求筛选过，
        #      典型场景：触发词里混着服装词而用户要求换装，LLM 剔除冲突词）→ 只用 LLM 的列表；
        #      传空串 = LLM 明确表示一个触发词都不要追加。
        #   b) 未传（None，/draw 指令、伴侣插件、表情包等路径）→ 维持旧行为：全量自动追加。
        if enabled:
            _lib = self._lora_lib_index()
            _triggers: list[str] = []
            if trigger_words is not None:
                logger.info(
                    f"【LoRA 触发词】 LLM 显式传入筛选触发词（原文）: {trigger_words!r}"
                )
                for _tw in re.split(r"[\n,，、;；]+", str(trigger_words).strip()):
                    _tw = _tw.strip()
                    if _tw and _tw not in _triggers:
                        _triggers.append(_tw)
            else:
                for nm in enabled:
                    _lc = next(
                        (l for l in (loras_cfg or [])
                         if (l.get("name") or "").strip() == nm),
                        None,
                    )
                    _tw_raw = (
                        (_lc.get("trigger_words") if _lc else None)
                        or (_lib.get(nm) or {}).get("trigger_words")
                        or ""
                    )
                    for _tw in re.split(r"[\n,，、;；]+", str(_tw_raw).strip()):
                        _tw = _tw.strip()
                        if _tw and _tw not in _triggers:
                            _triggers.append(_tw)
            # 固定提示词或走 JSON 原值（positive 为空）时不追加触发词，
            # 避免覆盖工作流 JSON 内固化好的提示词
            if _triggers and positive and not _fixed_prompt:
                _pos_set = [p.strip() for p in re.split(r"[\n,，、;；]+", positive or "")]
                _add = [t for t in _triggers if t not in _pos_set and t not in (positive or "")]
                if _add:
                    positive = (positive.strip() + ", " if positive and positive.strip() else "") + ", ".join(_add)
                    workflow_builder.set_text_node(
                        prompt, wf.get("positive_node"), "text", positive
                    )
                    logger.info(f"【LoRA 触发词】 已追加到正向提示词: {_add}")
                else:
                    logger.info(f"【LoRA 触发词】 启用 LoRA 的触发词均已存在于正向提示词中，无需追加")

        # 多槽位提示词注入（prompt_slots）：服务于「一条工作流需要多处语义不同的文本
        # 注入」的场景 —— 如表情包（anima 生图提示词 + boogu 加字指令）、漫画（角色
        # 提示词 + 整段分镜描述）。未配置 prompt_slots 的工作流整段跳过，行为不变。
        # 注意：槽位只负责**额外的**文本节点；主正向提示词仍走上方 positive 全流程
        # （中文翻译 / LoRA 预设 / 触发词追加），以保证这些现有能力不丢失。
        # 实现见 comic.py:inject_slots（归一化 / 渲染 / 清空节点均在那里）。
        # 表情包 boogu 节点（节点 B）指令注入 + 后半段固定宽高：
        # 节点 B 指令来自内部 LLM 生成的两段提示词之一（slot_values["boogu"]），
        # 按配置 boogu_node 写入；后半段宽高只认配置写死的值（用户指令 / LLM 均不可改），
        # 未配置则不注入、沿用工作流自带尺寸。
        _boogu_instr = ((slot_values or {}).get("boogu") or "") if slot_values else ""
        _bn = wf.get("boogu_node")
        if _bn and _boogu_instr and _boogu_instr.strip():
            workflow_builder.set_text_node(
                prompt, _bn, (wf.get("boogu_field") or "prompt"), _boogu_instr.strip()
            )
            logger.info(f"【boogu】 指令 → 节点 {_bn}（{len(_boogu_instr.strip())} 字）")
        elif _bn:
            logger.info(f"【boogu】 节点 {_bn} 本次未生成指令（沿用工作流默认）")
        _bw = wf.get("boogu_width_node")
        if _bw and wf.get("boogu_width") is not None:
            workflow_builder.set_number_node(
                prompt, _bw, (wf.get("boogu_width_field") or "width"), int(wf.get("boogu_width"))
            )
            logger.info(f"【boogu 宽】 节点 {_bw} = {wf.get('boogu_width')}")
        _bh = wf.get("boogu_height_node")
        if _bh and wf.get("boogu_height") is not None:
            workflow_builder.set_number_node(
                prompt, _bh, (wf.get("boogu_height_field") or "height"), int(wf.get("boogu_height"))
            )
            logger.info(f"【boogu 高】 节点 {_bh} = {wf.get('boogu_height')}")
        # 旧 prompt_slots 工作流（未配置 boogu_node）仍走原槽位注入逻辑
        if not _bn:
            comic.inject_slots(self, prompt, wf, slot_values)

        # 随机化种子（未指定 --seed 时），避免每次出图完全相同
        seeds_used = workflow_builder.randomize_seed(prompt, seed)
        if seeds_used:
            logger.info(f"本次种子: {seeds_used}")

        # 注入采样器参数：denoise（降噪幅度/重绘强度）
        # -1 = 不注入（沿用工作流原始值）；未传则用工作流配置的 default_denoise
        if denoise is None:
            cfg_denoise = wf.get("default_denoise")
            if cfg_denoise is not None:
                try:
                    cfg_denoise = float(cfg_denoise)
                except (ValueError, TypeError):
                    cfg_denoise = None
            denoise = cfg_denoise
        if denoise is not None and denoise >= 0:
            if workflow_builder.set_denoise(prompt, denoise):
                logger.info(f"本次 denoise: {denoise}")

        # 注入采样器参数：steps（步数）/ cfg（CFG 引导系数）
        # 「不注入」开关（steps_off / cfg_off=true）或配置值无效/非正 → 沿用工作流文件原值。
        _set_steps = None
        _set_cfg = None
        if not wf.get("steps_off", False):
            _raw_s = wf.get("default_steps")
            if _raw_s is not None:
                try:
                    _set_steps = int(_raw_s) if float(_raw_s) > 0 else None
                except (ValueError, TypeError):
                    _set_steps = None
        if not wf.get("cfg_off", False):
            _raw_c = wf.get("default_cfg")
            if _raw_c is not None:
                try:
                    _set_cfg = float(_raw_c) if float(_raw_c) > 0 else None
                except (ValueError, TypeError):
                    _set_cfg = None
        if _set_steps is not None or _set_cfg is not None:
            if workflow_builder.set_sampler_params(prompt, steps=_set_steps, cfg=_set_cfg):
                logger.info(f"本次采样器参数: steps={_set_steps}, cfg={_set_cfg}")

        # 注入放大模型（替换工作流里【已存在】的放大模型节点模型名）
        # - 不填 upscale_node_id / upscale_model_name → 沿用工作流默认放大模型
        # - 两者都填 → 把该节点原本的模型名替换为指定的（仅替换，不注入新节点）
        _up_node = (wf.get("upscale_node_id") or "").strip()
        _up_model = (wf.get("upscale_model_name") or "").strip()
        if _up_node and _up_model:
            _old = workflow_builder.set_upscale_model(prompt, _up_node, _up_model)
            if _old is not None:
                logger.info(
                    f"【放大模型】 已替换节点 {_up_node} 的放大模型: "
                    f"{_old} → {_up_model}"
                )
            else:
                logger.warning(
                    f"【放大模型】 配置节点 {_up_node} 不存在，或其 inputs 里没有"
                    f"可替换的模型名字段（model_name）。请检查工作流节点 ID 是否正确。"
                )
        elif _up_node or _up_model:
            logger.warning(
                "【放大模型】 配置不完整：需同时填写「放大模型节点」和「放大模型名称」"
                "才会生效，当前仅填了其中一项，已忽略。"
            )

        # 绘图摘要：不再打印完整 workflow JSON，仅展示关键信息，减少日志噪声。
        # LoRA 权重以 active_map（命令 --名称:权重 优先，None 表示沿用工作流默认）为准。
        _lora_weight = {}
        for nm, w in (active_map or {}).items():
            if w is None:
                _cfg_w = next(
                    (l.get("weight") for l in (loras_cfg or [])
                     if (l.get("name") or "").strip() == nm),
                    None,
                )
                _lora_weight[nm] = _cfg_w if _cfg_w is not None else "默认"
            else:
                _lora_weight[nm] = w
        _enabled_lora = (
            ", ".join(f"{nm}:{_lora_weight.get(nm, '?')}" for nm in (enabled or []))
            or "无"
        )
        # 摘要：普通图含尺寸；表情包/漫画工作流（配了 prompt_slots）额外列出各槽位文字，
        # 便于确认气泡/底部文字是否注入成功。
        _size = f"{w}x{h}" if (w and h) else "(默认)"
        _slot_lines = ""
        _comic_slots = self._normalize_prompt_slots(wf.get("prompt_slots"))
        _slot_lines = ""
        if _comic_slots:
            # 表情包/漫画工作流：无论本次是否成功生成槽位文字，都列出槽位状态，
            # 便于确认「提示词2(槽位文字)」是否注入（未生成则沿用工作流默认）。
            _sv = self._slot_vars(wf)
            _slot_texts = []
            for _v in _sv:
                _t = (slot_values.get(_v) or "").strip() if slot_values else ""
                # 原样显示 LLM/指令传进去的槽位文字（值本身不做任何格式化包装）
                _slot_texts.append(f"{_v}={_t if _t else '(空/不出字)'}")
            if _slot_texts:
                _note = "（本次未生成槽位文字，沿用工作流默认）" if not slot_values else ""
                _slot_lines = "\n  槽位文字 : " + "；".join(_slot_texts) + _note
                # 同时展示 boogu 实际收到的指令：每个槽位（nl / vars / template 都算）都列出来，
                # 无论是否为空都标注，避免「非 nl 槽位」或「槽位为空」导致整段 boogu 指令消失。
                # （你这个表情包工作流是 vars 模式，之前只遍历 nl 槽位所以一直没打印——已修正。）
                # boogu 节点显示：既含自动识别的（class_type/模型链路/prompt_slots boogu:true），
                # 也含配置显式声明的 boogu_node（节点 B 编辑指令节点），确保节点 B 指令一定打印出来。
                _boogu_ids_log = set(comic._boogu_node_ids(wf))
                _bn_cfg = (wf.get("boogu_node") or "").strip()
                if _bn_cfg:
                    _boogu_ids_log.add(_bn_cfg)
                _boogu_lines = []
                # 普通槽位（非 boogu 节点）：原样显示 LLM/指令传进去的槽位文字
                for _s in _comic_slots:
                    if not isinstance(_s, dict):
                        continue
                    _nid = str(_s.get("node") or "").strip()
                    if _nid in _boogu_ids_log:
                        continue  # boogu 节点由下方统一展示，避免重复/错显旧模板
                    _k = (_s.get("key") or "").strip()
                    if not _k:
                        continue
                    if comic.slot_mode(_s) == "nl":
                        # 自然语言指令模式：显示 LLM 写的整段指令
                        _bv = self._render_nl_slot(_s, slot_values)
                    else:
                        _sv2 = slot_values or {}
                        _vars2 = [str(v).strip() for v in (_s.get("vars") or []) if str(v).strip()]
                        if _s.get("template"):
                            _bv = comic.render_slot_template(self, _s, _s.get("template"), _sv2)
                        elif _vars2:
                            _bv = str(_sv2.get(_vars2[0], "") or "").strip()
                        else:
                            _bv = str(_sv2.get(_k, "") or "").strip()
                    _boogu_lines.append(f"    [{_k}] {_bv if _bv else '(空/本次未生成，boogu 沿用工作流默认)'}")
                # 真正发给 ComfyUI 的 boogu 节点指令（与 prompt_slots 配置无关，来自 boogu 接管）
                for _bn in _boogu_ids_log:
                    _bv = (slot_values or {}).get(f"boogu_{_bn}", "") if slot_values else ""
                    if not _bv:
                        # 自动漫画路由（llm_draw）把 boogu 指令放在 slot_values["boogu"]，
                        # 与配置 boogu_node 节点对应，兼容两种键名，确保节点 B 指令被打印。
                        _bv = (slot_values or {}).get("boogu", "") if slot_values else ""
                    _boogu_lines.append(
                        f"    [boogu节点 {_bn}] {_bv if _bv and _bv.strip() else '(空/本次未生成，boogu 沿用工作流默认)'}"
                    )
                if _boogu_lines:
                    _slot_lines += "\n  boogu指令 :\n" + "\n".join(_boogu_lines)
        logger.info(
            "【绘图·摘要】\n"
            "  工作流 : %s\n"
            "  尺寸 : %s\n"
            "  种子 : %s\n"
            "  正向提示词 : %s\n"
            "  负向提示词 : %s\n"
            "  启用LoRA : %s%s"
            % (
                wf.get("name"),
                _size,
                (seeds_used[0] if seeds_used else "(随机/未指定)"),
                positive if positive else "(空)",
                negative if negative else "(空)",
                _enabled_lora,
                _slot_lines,
            )
        )

        # 提交到 ComfyUI（client 已在工作流加载时创建）
        srv_key = self._server_key(server)
        # 打印提交给服务器的工作流 JSON（受 log_workflow_json 控制，默认关闭）
        if self._cfg("log_workflow_json", False):
            try:
                logger.info(
                    "【绘图·工作流JSON】 提交给 %s 的工作流：\n%s",
                    srv_key,
                    json.dumps(prompt, ensure_ascii=False, indent=2),
                )
            except Exception as e:
                logger.warning("【绘图·工作流JSON】 打印失败: %s", e)
        try:
            try:
                result = await client.queue_prompt(prompt)
                prompt_id = result.get("prompt_id")
            except Exception as e:
                await self._send(event, self._friendly_error(e, "提交任务"))
                return

            if not prompt_id:
                logger.warning("【绘图·失败】[提交] ComfyUI 未返回 prompt_id")
                await self._send(event, self._cute("no_task_id"))
                self._record_failed(
                    event, positive, wf, is_img2img, ref_sha256,
                    _draw_start, "ComfyUI 未返回 prompt_id（提交失败）",
                )
                return

            # 记录最近任务，供 /queuestatus 使用
            try:
                self._last_prompt[event.session_id or "global"] = prompt_id
            except Exception:
                pass

            # 排队位置：优先用中转站响应头 `X-Queue-Position`（其语义即"入队那一刻
            # 前方还有几个任务，含正在运行的"），因为它由中转站统一调度，最准确。
            # 直连 ComfyUI（后端地址不经过中转站）时没有该响应头，则回退到本地队列
            # 统计（按本插件提交顺序估算"前面还有几位"）。
            pos = result.get("_queue_position")
            if pos is not None:
                ahead = int(pos)
                logger.info(f"【队列】 中转站 X-Queue-Position={ahead}（来自响应头）")
            else:
                ahead = self._local_queue_ahead(srv_key)
                logger.info(f"【队列】 无中转站 X-Queue-Position 响应头，回退本地队列 ahead={ahead}")
            try:
                self._local_queue_add(srv_key, prompt_id)
                # 提交后统一发一条提示：有队列（ahead>0）→「前面排着 N 个」；
                # 无队列（ahead<=0）默认不发提示；仅当 queue_hint_only_when_queued=False
                # 时才发「稍等，马上来」。只发这一条，避免与提交前提示重复。
                # 伴侣 proactive（notify_pending=False）不发。
                if self._cfg("return_queue_position", True) and notify_pending:
                    if ahead > 0 or not self._cfg("queue_hint_only_when_queued", True):
                        await self._send(event, self._queue_hint(ahead))

                # 等待出图：动态超时 = 基础超时 + 前面排队任务累加预估耗时。
                # 排得越靠后，前面任务越多，等待就越久，故按 ahead 逐任务累加，
                # 避免排在长队后面的任务因固定超时过早被误判为失败。
                base_timeout = int(self._cfg("draw_timeout", 120))
                # 每个前面排队任务额外累加的秒数；默认按"每个任务都要完整基础超时"保守估算
                per_extra = int(self._cfg("queue_extra_timeout", 0)) or base_timeout
                max_timeout = int(self._cfg("max_draw_timeout", 0)) or (base_timeout + 30 * base_timeout)
                # 单张等待硬上限：必须 < AstrBot 框架工具超时（默认 120 秒），
                # 确保 ComfyUI 层先超时报错返回、工具正常 return，框架永不 wait_for
                # 取消我们（否则会打断正在 await 的 event.send、破坏 bot WS 连接致卡死）。
                timeout = min(max_timeout, base_timeout + ahead * per_extra, 100)
                interval = max(1, int(self._cfg("queue_poll_interval", 2)))
                history = await client.wait_for_result(prompt_id, timeout, interval)
                if not history:
                    # 再做一次兜底（极少数情况下历史在超时边界才写入）
                    try:
                        final = await client.get_history(prompt_id)
                        history = final.get(prompt_id) if final else None
                    except Exception:
                        history = None
                if not history:
                    logger.warning(f"【绘图·失败】[超时] 等待 {timeout} 秒仍无结果，prompt_id={prompt_id}")
                    await self._send(event, self._cute("timeout"))
                    self._record_failed(
                        event, positive, wf, is_img2img, ref_sha256,
                        _draw_start, f"等待 {timeout} 秒仍无结果（超时）",
                    )
                    return

                images = comfyui_client.extract_images(history, wf.get("output_node"))
                if not images:
                    # 区分：ComfyUI 任务本身报错（节点失败，如缺放大模型）vs 工作流确实无图片输出节点
                    _task_err = comfyui_client.task_error(history)
                    if _task_err:
                        logger.warning(f"【绘图·失败】[无图] ComfyUI 任务执行报错：{_task_err}")
                    else:
                        logger.warning(
                            "【绘图·失败】[无图] 任务完成但未找到输出图片节点"
                            "（工作流可能缺少 SaveImage / PreviewImage / SaveImageExtended 等输出节点）"
                        )
                    await self._send(event, self._cute("no_image"))
                    self._record_failed(
                        event, positive, wf, is_img2img, ref_sha256,
                        _draw_start,
                        f"任务完成但未找到输出图片节点（无图）{'；ComfyUI报错: ' + _task_err if _task_err else ''}",
                    )
                    return

                for img in images:
                    try:
                        data = await client.get_image(
                            img["filename"],
                            img.get("subfolder", ""),
                            img.get("type", ""),
                        )
                    except Exception as e:
                        await self._send(event, self._friendly_error(e, "下载图片"))
                        continue
                    suffix = os.path.splitext(img["filename"])[1] or ".png"
                    tmp_path = self.temp_dir / f"{uuid.uuid4().hex}{suffix}"
                    with open(tmp_path, "wb") as f:
                        f.write(data)
                    img_path = str(tmp_path)

                    # 图库归档：把成品图按内容寻址永久移入 gallery/（移动转正，不重复占空间）
                    if self.gallery is not None:
                        try:
                            try:
                                from .image_store import SRC_GEN
                            except ImportError:
                                from image_store import SRC_GEN
                            _real_w, _real_h = w, h
                            if _PILImage is not None:
                                try:
                                    with _PILImage.open(img_path) as _im:
                                        _real_w, _real_h = _im.width, _im.height
                                except Exception:
                                    pass
                            # 群名：AstrBot 未提供同步的 get_group_name，需异步查平台群信息
                            # （aiocqhttp 下走 get_group_info）。查询失败或超时一律留空，
                            # 绝不因此中断归档与出图。
                            _group_name = ""
                            try:
                                _gid = str(getattr(event, "get_group_id", lambda: "")() or "")
                                _get_group = getattr(event, "get_group", None)
                                if _gid and callable(_get_group):
                                    _grp = await asyncio.wait_for(_get_group(_gid), timeout=3)
                                    _group_name = str(getattr(_grp, "group_name", "") or "")
                            except Exception:
                                _group_name = ""
                            # 启用 LoRA（含权重）列表：用于图库大图详情展示
                            _loras_record = [
                                {"name": nm, "weight": _lora_weight.get(nm, None)}
                                for nm in (enabled or [])
                            ]
                            # 采样器参数（steps/cfg/denoise）从工作流 JSON 提取
                            _sampler = {}
                            try:
                                from . import workflow_builder as _wb
                            except ImportError:
                                import workflow_builder as _wb
                            try:
                                _sampler = _wb.get_sampler_defaults(prompt) or {}
                            except Exception:
                                _sampler = {}
                            _final = self.gallery.archive_image(
                                img_path,
                                source=SRC_GEN,
                                prompt=positive,
                                prompt_raw=positive,
                                workflow=(wf.get("name") or ""),
                                loras=_loras_record,
                                platform="comfyui",
                                negative=(negative or ""),
                                seed=(seeds_used[0] if seeds_used else None),
                                w=_real_w,
                                h=_real_h,
                                denoise=(denoise if is_img2img else None),
                                cfg=_sampler.get("cfg"),
                                steps=_sampler.get("steps"),
                                is_img2img=bool(is_img2img),
                                ref_sha256=(ref_sha256 or ""),
                                size_bytes=(os.path.getsize(img_path) if os.path.exists(img_path) else None),
                                cost_sec=(time.time() - _draw_start),
                                user_id=user_id,
                                user_name=user_name,
                                session_id=(getattr(event, "session_id", "") or ""),
                                group_id=(getattr(event, "get_group_id", lambda: "")() or ""),
                                group_name=_group_name,
                                trigger_msg=(getattr(event, "message_str", "") or ""),
                                status=0,
                                on_dedup=lambda _sha, _uc: self._oplog_dedup(
                                    _sha, _uc, user_id, user_name, event
                                ),
                            )
                            # T6 自动打标：表情包/漫画出图成功后按功能打分类标签（复用图库 tags 机制，不动表结构）
                            if comic_feature:
                                _tag = {"meme_text": "表情包", "meme_img": "表情包", "comic": "漫画"}.get(comic_feature)
                                if _tag and _final and os.path.exists(_final):
                                    try:
                                        self.gallery.add_tags(_sha256_of(_final), [_tag])
                                    except Exception as _te:
                                        logger.warning(f"【打标】 自动打标失败（不影响出图）: {_te}")
                            # archive_image 会把文件从 temp/ 移动到 gallery/，必须用
                            # 返回的最终路径继续发送/上报，否则会指向已不存在的临时文件。
                            if _final:
                                img_path = _final
                        except Exception as _ge:
                            logger.warning(f"【图库】 归档失败（不影响出图）: {_ge}")

                    # 产出 (图片节点, 本地路径) 元组：指令只取节点 yield 给用户，
                    # 记下本插件最近生成的图片本地路径（按会话），供图生图兜底使用
                    sid = getattr(event, "session_id", "") or ""
                    bucket = g_last_generated.setdefault(sid, [])
                    if img_path not in bucket:
                        bucket.append(img_path)
                    # 仅保留最近 5 张，避免无限增长
                    if len(bucket) > 5:
                        g_last_generated[sid] = bucket[-5:]
                    # 全局兜底（session 为空时也存一份，便于跨会话引用场景）
                    if not sid:
                        gbucket = g_last_generated.setdefault("__global__", [])
                        if img_path not in gbucket:
                            gbucket.append(img_path)
                        if len(gbucket) > 5:
                            g_last_generated["__global__"] = gbucket[-5:]
                    # webp 兼容：ComfyUI 输出常为 webp，而部分适配器（onebot/QQ 等）
                    # 在 Agent 工具场景下对 webp 内联推送失败，会被 AstrBot 转成
                    # `<pc_history_media ...>` 占位、图片丢失。可配置 convert_webp_to_png
                    # 在发送前用 Pillow 转一个 png 临时副本用于 event.send / 伴侣发图；
                    # 归档仍保留原 webp（内容寻址不变），图生图兜底缓存也用原 webp。
                    # 默认关闭（关闭时直接用原图发送）。
                    _send_img_path = img_path
                    if self._cfg("convert_webp_to_png", False) and _PILImage is not None and str(img_path).lower().endswith(".webp"):
                        try:
                            with _PILImage.open(img_path) as _im:
                                _png_tmp = self.temp_dir / f"{uuid.uuid4().hex}.png"
                                _im.convert("RGB").save(_png_tmp, "PNG")
                                _send_img_path = str(_png_tmp)
                                logger.info(f"【出图·成功】 webp 已转 png 发送副本: {_send_img_path}")
                        except Exception as _e:
                            logger.warning(f"【出图·成功】 webp 转 png 发送副本失败（用原图发送）: {_e}")
                    # LLM 工具 llm_draw 额外用本地路径拼 JSON 返回（供伴侣插件解析为图片）。
                    # NSFW 护栏（群聊）：生图后发送前对结果图做 NSFW 检测，
                    # 群聊场景一律拦截（检测不通过或检测不可用均拦截），私聊放行。
                    _nsfw_blocked = False
                    # 统一初始化发送者 uid，确保 NSFW 拦截分支（不进入成功日志分支）也能用。
                    _uid = getattr(event, "get_sender_id", lambda: "")() or "anon"
                    # NSFW 检测结果摘要（供出图成功日志展示，私聊/未检测时为「未检测」）。
                    _nsfw_log = "（未检测）"
                    if not self._is_private_event(event):
                        try:
                            from .nsfw_detector import get_detector
                        except ImportError:
                            from nsfw_detector import get_detector
                        _nsfw_thr = 0.5
                        try:
                            if getattr(self, "gallery", None) is not None:
                                _nsfw_thr = self.gallery._nsfw_threshold()
                        except Exception:
                            pass
                        _det = get_detector(_nsfw_thr)
                        try:
                            _is_nsfw, _nsfw_score, _nsfw_avail = (
                                await asyncio.to_thread(_det.detect, _send_img_path)
                            )
                        except Exception as _e:
                            logger.warning(f"【NSFW】 出图群聊检测异常: {_e}")
                            _is_nsfw, _nsfw_score, _nsfw_avail = True, 1.0, False
                        # 无论放行/拦截都把置信度记下来，便于出图日志核对
                        if isinstance(_nsfw_score, (int, float)):
                            _nsfw_log = f"{_nsfw_score:.2f}" + ("(检测不可用)" if not _nsfw_avail else "")
                        else:
                            _nsfw_log = "(检测不可用)" if not _nsfw_avail else "(无分数)"
                        if _is_nsfw:
                            _sc = f"（置信度 {_nsfw_score:.2f}）" if isinstance(_nsfw_score, (int, float)) else ""
                            _reason = "（检测不可用，已按最严策略拦截）" if not _nsfw_avail else ""
                            logger.warning(
                                f"【NSFW】 群聊出图被拦截{_sc}{_reason} user={user_id} "
                                f"workflow={wf.get('name') or '(未命名)'} path={_send_img_path}"
                            )
                            await self._send(
                                event,
                                f"这张图被标记为 NSFW{_sc}，不能发到群里哦～ 已为你拦截。{_reason}"
                                f"如果想看这张图，可以在私聊里用「/图库 取图」取到你最近生成的那张。",
                            )
                            # 标记被拦截：不发送、不入图库、不 yield 图片，但继续走
                            # 后续「绘图结束」日志与操作日志（记录为拦截），多图时跳到下一张。
                            _nsfw_blocked = True
                    # ── 图文消息：把配文 / 出图报告与图片合成【一条】消息 ──────────
                    # 必须在发图前算好（旧逻辑是发图后单独发一条）。报告并入后不再重复发送。
                    _report_merged = False
                    _cap_text = ""
                    if not _nsfw_blocked and (self._cfg_image_caption().get("enabled", True)):
                        _rpt = ""
                        if self._cfg("show_draw_report", False) and self._cfg_image_caption().get("merge_draw_report", True):
                            _rpt = self._draw_report_text(img_path, w, h, _draw_start)
                            if _rpt:
                                _report_merged = True
                        _cap_text = self._build_image_caption(caption, _rpt)
                    if not _nsfw_blocked:
                        if _cap_text:
                            _cap_result = event.chain_result(
                                [Plain(text=_cap_text), Image.fromFileSystem(_send_img_path)]
                            )
                            # ★必须显式关闭「文本转图片」：AstrBot 的 t2i 装饰阶段在
                            # （use_t2i_ is None and 全局 t2i 开启）或 use_t2i_ 为真时，
                            # 会把整条链里开头的 Plain 渲染成图并**整条替换**为那张渲染图——
                            # 配文一旦超过 t2i 字数阈值，辛苦生成的图片会被直接丢弃。
                            # 置为 False 即强制走普通文本发送，保住图片。
                            try:
                                _cap_result.use_t2i_ = False
                            except Exception:
                                pass
                            yield _cap_result, _send_img_path
                        else:
                            yield event.image_result(_send_img_path), _send_img_path

                    # 出图成功业务日志（仅成功发送时打印用户信息，被 NSFW 拦截的图不发）
                    if not _nsfw_blocked:
                        try:
                            from .image_store import _sha256_of
                        except ImportError:
                            from image_store import _sha256_of
                        try:
                            _sha = _sha256_of(img_path)
                            logger.info(
                                "【出图·成功】\n"
                                "  user : %s\n"
                                "  用户昵称 : %s\n"
                                "  工作流 : %s\n"
                                "  种子 : %s\n"
                                "  时间 : %s\n"
                                "  文件名 : %s"
                                % (
                                    _uid,
                                    user_name or "(未知)",
                                    wf.get("name") or "(未命名)",
                                    (seeds_used[0] if seeds_used else "?"),
                                    time.strftime("%Y-%m-%d %H:%M:%S"),
                                    (img_path or "").split("/")[-1].split("\\")[-1],
                                )
                            )
                            if _sha and self.story is not None:
                                try:
                                    _p = positive
                                except NameError:
                                    _p = ""
                                self._story_maybe_link_image(
                                    event, _sha, _p, _real_w, _real_h, wf.get("name")
                                )
                        except Exception:
                            _sha = None
                    else:
                        _sha = None

                    # 绘图结束：本次生图主流程走完（成功或拦截均打印，位于成功日志之下）。
                    # 不论成功/拦截都带上完整图片信息，确保拦截时也能看到尺寸/NSFW 等。
                    try:
                        _fs = os.path.getsize(img_path or "") if not _nsfw_blocked else 0
                        if _fs >= 1024 * 1024:
                            _fs_fmt = f"{_fs / 1024 / 1024:.2f} MB"
                        elif _fs >= 1024:
                            _fs_fmt = f"{_fs / 1024:.1f} KB"
                        else:
                            _fs_fmt = f"{_fs} B" if _nsfw_blocked is False else "—"
                    except Exception:
                        _fs_fmt = "—"
                    _end_lines = [
                        "【绘图·结束】",
                        f"  状态 : {'拦截' if _nsfw_blocked else '成功'}",
                        f"  工作流 : {wf.get('name') or '(未命名)'}",
                        f"  种子 : {seeds_used[0] if seeds_used else '?'}",
                        f"  尺寸 : {_real_w if _real_w else '?'}x{_real_h if _real_h else '?'}",
                        f"  文件大小 : {_fs_fmt}",
                        f"  NSFW置信度 : {_nsfw_log}",
                        f"  sha256 : {(_sha[:16] if _sha else '—')}",
                        f"  耗时 : {time.time() - _draw_start:.1f}秒",
                    ]
                    if _nsfw_blocked:
                        _end_lines.append(
                            "  拦截理由 : 群聊NSFW护栏触发"
                            + (f"（置信度 {_nsfw_score:.2f}）" if isinstance(_nsfw_score, (int, float)) else "")
                            + ("（检测依赖不可用，按最严策略拦截）" if not _nsfw_avail else "")
                        )
                    logger.info("\n".join(_end_lines))
                    if self.oplog is not None:
                        self.oplog.add(
                            "draw_success",
                            f"生图成功（{wf.get('name') or '未知工作流'}）",
                            user_id=_uid,
                            user_name=user_name,
                            session_id=sid,
                            ref_sha=(_sha or "")[:16],
                            detail=f"seed={seeds_used[0] if seeds_used else '?'} "
                                   f"w={_real_w if _real_w else '?'} h={_real_h if _real_h else '?'} "
                                   f"耗时={time.time() - _draw_start:.1f}s",
                            extra={
                                "seed": seeds_used[0] if seeds_used else None,
                                "w": _real_w if _real_w else None, "h": _real_h if _real_h else None,
                                "workflow": wf.get("name") or "",
                                "sha16": (_sha or "")[:16],
                                },
                                )

                                # 生图成功：记录配额（总次数 + 当前小时次数）
                    self._record_draw_used(event)

                    # 出图完成后的贴心小报告：文件时间、尺寸、耗时（随机萌文案）。
                    # 受配置 show_draw_report 控制（默认关闭，关闭则不输出文件信息）。
                    # 若上面已把报告并进图片那条消息（图文消息），这里不再重复发一条。
                    if self._cfg("show_draw_report", False) and not _report_merged:
                        try:
                            _rpt_text = self._draw_report_text(img_path, w, h, _draw_start)
                            if _rpt_text:
                                await self._send(event, _rpt_text)
                        except Exception as _e:
                            logger.warning(f"【出图·报告】 发送小报告失败（不影响出图）: {_e}")
            finally:
                # 无论成功/失败/超时，均从本地队列移除本任务（try/finally 确保不泄漏）
                self._local_queue_remove(srv_key, prompt_id)
        finally:
            await client.close()

    # ------------------------------------------------------------------ #
    # 指令：/draw
    # ------------------------------------------------------------------ #
    @filter.command("draw")
    async def cmd_draw(self, event: AstrMessageEvent):
        """通过指令绘图。用法：/draw 提示词 [--wf 工作流] [--名称[:权重]] [--名称/预设名[:权重]] [--w 宽] [--h 高] [--seed 数字]
（--名称[:权重] 为 LoRA 简写，如 --安魂曲:1、--安魂曲:0.5，冒号支持 : 与 ：；--名称/预设名 引用该 LoRA 的预设提示词，如 --安魂曲/预设1）"""
        args = self._strip_command(
            (event.message_str or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
            "draw",
        )
        prompt, lora_map, lora_presets, width, height, wf_name, seed, denoise = self._parse_draw_args(args or "")
        if not prompt.strip():
            await self._send(event,
                "用法：/draw 一只白色水手服少女 --wf sd --lora catgirl:0.8 --w 768 --h 768 [--seed 12345]"
            )
            return
        # 已读回执由 _ack_command_received 统一处理（覆盖本插件所有指令，含 /draw）
        # 若消息或引用(回复)里带了图片，则按图生图处理
        images = await self._extract_images(event)
        async for m, _p in self._do_draw(
            event, wf_name, prompt, "", width, height, lora_map, lora_presets, seed,
            init_images=images,
            is_img2img=bool(images),
            denoise=denoise,
            explicit_default=(wf_name is None),
        ):
            yield m
        # 收尾时再终止事件：避免开头 stop_event 导致 pipeline 在第一个 yield
        # 后中断 _do_draw 的协程（等待/下载图片的代码不再执行，temp 无图）。
        event.stop_event()

    @filter.command("表情包", alias={"表情", "漫画", "comic"})
    async def cmd_comic(self, event: AstrMessageEvent):
        """表情包：直填槽位出图（不调 LLM，传入啥填啥、不翻译）。

        用法：/表情包 画面::气泡文字[::底部文字] [--wf 工作流] [--名称[:权重]] [--w 宽] [--h 高] [--seed 数字]
        用 :: 分隔各段：第 1 段=画面提示词，其后依次对应工作流 prompt_slots 的槽位变量
        （如 bubble / bottom）。未给全的槽位留空（对应节点清空、不出字）。
        工作流解析：--wf 优先 → 否则「表情生成(meme_text)」功能绑定的工作流；非漫画工作流直接报错停住。
        想让 AI 自动生成文字请用 /表情包llm。
        """
        args = self._strip_command(
            (event.message_str or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
            "表情包", ("表情", "漫画", "comic"),
        )
        if not (args or "").strip() or "::" not in (args or ""):
            await self._send(event,
                "用法：/表情包 画面::气泡文字[::底部文字] [--wf 工作流]\n"
                "例：/表情包 鲸鱼娘在敲键盘::摸鱼中::其实在偷偷删你学习资料\n"
                "想让 AI 自动生成文字请用 /表情包llm 你的想法")
            return
        prompt, lora_map, lora_presets, width, height, wf_name, seed, denoise = self._parse_draw_args(args or "")
        # 按功能解析工作流（meme_text=文生表情包），--wf 优先；非漫画工作流报错停住
        wf_name, _err = self._resolve_comic_workflow("meme_text", wf_name)
        if _err:
            await self._send(event, _err)
            return
        _feat = self._feature_by_key("meme_text")
        lora_map, _neg = comic.merge_feature_lora(_feat, lora_map, "")
        wf = self._find_workflow_by_name(wf_name) or {}
        _vars = self._slot_vars(wf)
        _parts = [p.strip() for p in (args or "").split("::")]
        positive_prompt = _parts[0]
        slot_values = {v: (_parts[i + 1] if i + 1 < len(_parts) else "") for i, v in enumerate(_vars)}
        async for m, _p in self._do_draw(
            event, wf_name, positive_prompt, _neg, width, height, lora_map, lora_presets, seed,
            init_images=None, is_img2img=False, denoise=denoise,
            slot_values=slot_values, explicit_default=False, comic_feature="meme_text",
        ):
            yield m
        event.stop_event()

    @filter.command("表情包llm", alias={"表情llm"})
    async def cmd_comic_llm(self, event: AstrMessageEvent):
        """表情包(LLM)：一句想法，AI 自动生成「画面提示词 + 气泡/底部文字」。

        用法：/表情包llm 用鲸鱼娘lora，画面是帮用户写代码时偷偷删掉用户的学习资料 [--wf 工作流] [--raw 不扩写]
        加 --raw 则只把原话当画面提示词、不调 LLM 扩写、不生成槽位文字（沿用工作流默认）。
        """
        args = self._strip_command(
            (event.message_str or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
            "表情包llm", ("表情llm",),
        )
        _parts = (args or "").split()
        _auto_raw = "--raw" in _parts
        args = " ".join(p for p in _parts if p != "--raw")
        prompt, lora_map, lora_presets, width, height, wf_name, seed, denoise = self._parse_draw_args(args or "")
        if not (args or "").strip():
            await self._send(event, "用法：/表情包llm 你的想法 [--wf 工作流] [--raw 不扩写提示词]")
            return
        wf_name, _err = self._resolve_comic_workflow("meme_text", wf_name)
        if _err:
            await self._send(event, _err)
            return
        _feat = self._feature_by_key("meme_text")
        lora_map, _neg = comic.merge_feature_lora(_feat, lora_map, "")
        wf = self._find_workflow_by_name(wf_name) or {}
        # LLM 展开：把一句想法变成 anime 画面提示词 + 表情包文字（受配置开关与 --raw 控制）
        build_prompt = self._cfg("enable_llm_prompt", True) and not _auto_raw
        build_slots = self._cfg("enable_llm_slots", True) and not _auto_raw
        positive_prompt = prompt
        slot_values = None
        if build_prompt or build_slots:
            positive_prompt, slot_values, _lora_extracted = await self._comic_build_prompts_llm(
                wf, prompt, lora_map, want_prompt=build_prompt, want_slots=build_slots
            )
            if not build_prompt:
                positive_prompt = prompt
            if not build_slots:
                slot_values = None
            # 未用 --名称 显式指定 LoRA 时，用 LLM 从自由文本识别到的 LoRA 兜底
            if not lora_map and _lora_extracted:
                lora_map = _lora_extracted
        async for m, _p in self._do_draw(
            event, wf_name, positive_prompt, _neg, width, height, lora_map, lora_presets, seed,
            init_images=None, is_img2img=False, denoise=denoise,
            slot_values=slot_values, explicit_default=False, comic_feature="meme_text",
        ):
            yield m
        event.stop_event()

    @filter.command("图生表情包", alias={"图生表情"})
    async def cmd_img2img_comic(self, event: AstrMessageEvent):
        """图生表情包：附一张参考图 + 直填槽位出图（不调 LLM，传入啥填啥、不翻译）。

        用法：/图生表情包 画面::气泡文字[::底部文字] [--wf 工作流]
        第 1 段为画面提示词，其后依次对应 prompt_slots 槽位变量。未给全的槽位留空。
        工作流解析：--wf 优先 → 否则「图生表情包(meme_img)」功能绑定的工作流（须带 image_node）。
        """
        args = self._strip_command(
            (event.message_str or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
            "图生表情包", ("图生表情",),
        )
        prompt, lora_map, lora_presets, width, height, wf_name, seed, denoise = self._parse_draw_args(args or "")
        images = await self._extract_images(event)
        if not images:
            await self._send(event, "请附上一张参考图，再用 /图生表情包 画面::气泡文字[::底部文字] [--wf 工作流]")
            return
        if not (args or "").strip() or "::" not in (args or ""):
            await self._send(event,
                "用法：/图生表情包 画面::气泡文字[::底部文字] [--wf 工作流]\n"
                "例：/图生表情包 保留角色::摸鱼中::其实在删你资料")
            return
        wf_name, _err = self._resolve_comic_workflow("meme_img", wf_name)
        if _err:
            await self._send(event, _err)
            return
        _feat = self._feature_by_key("meme_img")
        lora_map, _neg = comic.merge_feature_lora(_feat, lora_map, "")
        wf = self._find_workflow_by_name(wf_name) or {}
        _vars = self._slot_vars(wf)
        _parts = [p.strip() for p in (args or "").split("::")]
        positive_prompt = _parts[0]
        slot_values = {v: (_parts[i + 1] if i + 1 < len(_parts) else "") for i, v in enumerate(_vars)}
        async for m, _p in self._do_draw(
            event, wf_name, positive_prompt, _neg, width, height, lora_map, lora_presets, seed,
            init_images=images, is_img2img=True, denoise=denoise,
            slot_values=slot_values, explicit_default=False, comic_feature="meme_img",
        ):
            yield m
        event.stop_event()

    @filter.command("图生表情包llm", alias={"图生表情llm"})
    async def cmd_img2img_comic_llm(self, event: AstrMessageEvent):
        """图生表情包(LLM)：附一张参考图，AI 自动生成「画面提示词 + 气泡/底部文字」。

        用法：/图生表情包llm 你的想法 [--wf 工作流] [--raw 不扩写]
        """
        args = self._strip_command(
            (event.message_str or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
            "图生表情包llm", ("图生表情llm",),
        )
        _parts = (args or "").split()
        _auto_raw = "--raw" in _parts
        args = " ".join(p for p in _parts if p != "--raw")
        prompt, lora_map, lora_presets, width, height, wf_name, seed, denoise = self._parse_draw_args(args or "")
        images = await self._extract_images(event)
        if not images:
            await self._send(event, "请附上一张参考图，再用 /图生表情包llm 你的想法 [--wf 工作流]")
            return
        if not (args or "").strip():
            await self._send(event, "用法：/图生表情包llm 你的想法 [--wf 工作流] [--raw 不扩写]")
            return
        wf_name, _err = self._resolve_comic_workflow("meme_img", wf_name)
        if _err:
            await self._send(event, _err)
            return
        _feat = self._feature_by_key("meme_img")
        lora_map, _neg = comic.merge_feature_lora(_feat, lora_map, "")
        wf = self._find_workflow_by_name(wf_name) or {}
        build_prompt = self._cfg("enable_llm_prompt", True) and not _auto_raw
        build_slots = self._cfg("enable_llm_slots", True) and not _auto_raw
        positive_prompt = prompt
        slot_values = None
        if build_prompt or build_slots:
            positive_prompt, slot_values, _lora_extracted = await self._comic_build_prompts_llm(
                wf, prompt, lora_map, want_prompt=build_prompt, want_slots=build_slots
            )
            if not build_prompt:
                positive_prompt = prompt
            if not build_slots:
                slot_values = None
            if not lora_map and _lora_extracted:
                lora_map = _lora_extracted
        async for m, _p in self._do_draw(
            event, wf_name, positive_prompt, _neg, width, height, lora_map, lora_presets, seed,
            init_images=images, is_img2img=True, denoise=denoise,
            slot_values=slot_values, explicit_default=False, comic_feature="meme_img",
        ):
            yield m
        event.stop_event()
        event.stop_event()

    @filter.llm_tool(name="comfyui_comic")
    @_safe_llm_tool
    async def llm_comic(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        negative_prompt: str = "",
        workflow: str = "",
        img2img_workflow: str = "",
        width: int = 0,
        height: int = 0,
        loras: list = None,
        seed: int = 0,
        count: int = 0,
        prompts: list = None,
        source: str = "",
        image: str = "",
        denoise: float = -1,
        caption: str = "",
    ):
        """生成带文字的「表情包 / 漫画」（文生图，无需参考图）。与 comfyui_draw 用法一致，区别仅在于：
        本工具绑定 special_features 里的「表情生成(meme_text)」/「漫画(comic)」功能——
        即目标工作流须配置 prompt_slots（多槽位提示词注入，把气泡台词/底部旁白/分镜文字写进画面）；
        普通文生图/图生图工作流（没配 prompt_slots）不能用本工具，否则报错并提示改用 comfyui_draw。
        工作流解析：你传的 workflow > 否则「表情生成(meme_text)」功能绑定的工作流；非漫画工作流直接报错停住。
        确定工作流后，插件用内部 LLM 按你的画面描述自动生成各槽位文字，无需你手动填。
        若你想「图生表情包」（附一张参考图），请改用 comfyui_meme_img 工具。

        Args:
            prompt(string): 【必填】画面/角色描述（中文或英文）。注意这是「出图提示词」，
                不是气泡文字——气泡/底部/分镜文字由插件自动生成。
            negative_prompt(string): 负向提示词，可选。
            workflow(string): 表情包/漫画工作流名，可选。必须是【配置了 prompt_slots 的漫画工作流】；
                不填则由「表情生成(meme_text)」功能自动选择。不确定有哪些时，先调 comfyui_workflows 查看。
                禁止传普通文生图工作流。
            img2img_workflow(string): 预留，图生图请用 comfyui_meme_img。
            width(number): 宽度，0 或不填表示使用工作流默认宽度。
            height(number): 高度，0 或不填表示使用工作流默认高度。
            loras(array[string]): 需要启用的 LoRA 名称/别名列表，可选。★硬规则：用户只要提到某个 LoRA 的名字/别名（包括「用XX lora画」「你没用lora」「重新画一张」这类纠正），都必须**先调 comfyui_loras 拿到规范名**并填进本参数；LoRA 名只写进 prompt 只是触发词，不会加载权重文件，角色会画错。
            seed(number): 随机种子，0 或不填表示每次随机。
            count(number): 预留参数，当前恒为 1。
            prompts(array): 多条出图项，要几张传几条，每条各出 1 张。
            image(string): 图生图参考图 URL——本工具为文生，传了也不作图生图处理；图生请用 comfyui_meme_img。
            denoise(number): 降噪幅度（0~1），仅图生图有效，可选。
            caption(string): 【图文消息】你想和图片发在【同一条消息】里的那句话（建议 20 字以内，
                用你自己的口吻；不要复述画面内容）。填了它，文字和图片会合成一条消息发出。
                ★配文已随图发出，工具返回后【不要再重复说一遍】同样的话。不想配文就留空。

        何时用本工具而非 comfyui_draw：用户要的是「表情包 / 漫画 / 带字梗图 / 多格漫画」，
        即画面里需要出现文字（气泡台词、底部旁白、分镜文字）。其余普通生图仍用 comfyui_draw。
        气泡/文字样式会自动按情绪多样化（云朵 / 圆角对话 / 思考OS / 爆炸 / 尖角 / 无气泡白字黑边 / 底部字幕条 / 放射爆裂等），
        详见技能「boogu-meme-bubbles」；用户明确要某种样式（如「用爆炸气泡」「不要气泡」「底部字幕条」「经典白字黑边」）
        或涉及无气泡、内心OS、拟声等写法时，把样式要求写进画面描述即可，插件会满足。
        """
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        # LLM 工具开关（与 comfyui_draw 一致；伴侣插件等第三方主动调用不受影响）
        if not plugin._cfg("enable_llm_tools", True) and not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            return "LLM 画图工具已关闭，请使用指令绘图（/draw、/表情包 等）。"
        # 按功能解析工作流（meme_text=文生表情包/漫画），--wf 优先；非漫画工作流报错停住
        _wf_name, _err = self._resolve_comic_workflow("meme_text", workflow)
        if _err:
            return _err
        _wf = plugin._find_workflow_by_name(_wf_name) or {}
        _clean_prompt, _bubble = comic.strip_bubble_field_from_prompt(prompt)
        # 有显式 prompt（画面描述）时，以 prompt 作为台词素材；原始消息只在没给 prompt 时兜底。
        # 否则「但是菲比啾比，你没有用lora」这类纠正/指令会经 comic 的 _raw = user_text or scene
        # 被当成台词写进气泡（详见 comic._gen_comic_prompts）。
        _user_text = _clean_prompt if _clean_prompt else (getattr(event, "message_str", "") or "").strip()
        if _bubble:
            _user_text = (_user_text + f"\n（用户/上文指定的气泡文字：{_bubble}）").strip()
        _prompts = await plugin._comic_write_prompts_llm(
            _wf, _user_text, _clean_prompt,
            "bot" if (source and source.strip() == SOURCE_COMPANION_PLUGIN) else "user",
        )
        _draw = _prompts.get("draw") or _clean_prompt
        slot_values = {"boogu": _prompts.get("boogu") or ""}
        # 委托 comfyui_draw 的完整出图逻辑（权限/闸门/队列/发送均复用）
        return await self.llm_draw(
            event, prompt=_draw, negative_prompt=negative_prompt, workflow=_wf_name,
            img2img_workflow=img2img_workflow, width=width, height=height, loras=loras,
            seed=seed, count=count, prompts=prompts, source=source, image=image, denoise=denoise,
            slot_values=slot_values, comic_feature="meme_text", caption=caption,
        )

    @filter.llm_tool(name="comfyui_meme_img")
    @_safe_llm_tool
    async def llm_meme_img(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        negative_prompt: str = "",
        workflow: str = "",
        width: int = 0,
        height: int = 0,
        loras: list = None,
        seed: int = 0,
        count: int = 0,
        prompts: list = None,
        source: str = "",
        image: str = "",
        denoise: float = -1,
        caption: str = "",
    ):
        """图生表情包：附一张参考图 + 自动生成气泡/底部文字（图生图）。绑定 special_features 的
        「图生表情包(meme_img)」功能——目标工作流须配置 prompt_slots + image_node。
        工作流解析：你传的 workflow > 否则「图生表情包(meme_img)」功能绑定的工作流；非（带 image_node 的）
        漫画工作流报错停住。必须提供参考图 image（参考图 URL）；需要出多张时 prompts 每项可带 image。

        Args:
            prompt(string): 【必填】画面描述/想法（中文或英文），气泡/底部文字由插件自动生成。
            negative_prompt(string): 负向提示词，可选。
            workflow(string): 图生表情包工作流名，可选。须为配置了 prompt_slots + image_node 的漫画工作流；
                不填则由「图生表情包(meme_img)」功能自动选择。禁止传纯文生图工作流。
            width(number): 宽度，0 或不填表示使用工作流默认宽度。
            height(number): 高度，0 或不填表示使用工作流默认高度。
            loras(array[string]): 需启用的 LoRA 名称/别名列表，可选。
            seed(number): 随机种子，0 或不填表示每次随机。
            count(number): 预留参数，当前恒为 1。
            prompts(array): 多条出图项（需图生图时每项带 image），要几张传几条。
            image(string): 【必填】参考图 URL。
            denoise(number): 降噪幅度（0~1），仅图生图有效，可选。
            caption(string): 【图文消息】你想和图片发在【同一条消息】里的那句话（建议 20 字以内，
                用你自己的口吻；不要复述画面内容）。填了它，文字和图片会合成一条消息发出。
                ★配文已随图发出，工具返回后【不要再重复说一遍】同样的话。不想配文就留空。

        何时用本工具：用户想「拿一张图改成表情包 / 在图上加文字气泡」。普通文生表情包用 comfyui_comic。
        """
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if not plugin._cfg("enable_llm_tools", True) and not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            return "LLM 画图工具已关闭，请使用指令绘图（/图生表情包 等）。"
        # 图生表情包必须有参考图（顶层 image 或 prompts 任一项带 image）
        _has_img = bool((image or "").strip()) or any(
            isinstance(_p, dict) and (str(_p.get("image") or "").strip()) for _p in (prompts or [])
        )
        if not _has_img:
            return "图生表情包需要一张参考图：请调用时提供 image（参考图 URL），或多条 prompts 每项带 image。"
        # 按功能解析工作流（meme_img=图生表情包），--wf 优先；非漫画/缺 image_node 报错停住
        _wf_name, _err = self._resolve_comic_workflow("meme_img", workflow)
        if _err:
            return _err
        _wf = plugin._find_workflow_by_name(_wf_name) or {}
        _clean_prompt, _bubble = comic.strip_bubble_field_from_prompt(prompt)
        # 有显式 prompt（画面描述）时，以 prompt 作为台词素材；原始消息只在没给 prompt 时兜底。
        # 否则「但是菲比啾比，你没有用lora」这类纠正/指令会经 comic 的 _raw = user_text or scene
        # 被当成台词写进气泡（详见 comic._gen_comic_prompts）。
        _user_text = _clean_prompt if _clean_prompt else (getattr(event, "message_str", "") or "").strip()
        if _bubble:
            _user_text = (_user_text + f"\n（用户/上文指定的气泡文字：{_bubble}）").strip()
        _prompts = await plugin._comic_write_prompts_llm(
            _wf, _user_text, _clean_prompt,
            "bot" if (source and source.strip() == SOURCE_COMPANION_PLUGIN) else "user",
        )
        _draw = _prompts.get("draw") or _clean_prompt
        slot_values = {"boogu": _prompts.get("boogu") or ""}
        return await self.llm_draw(
            event, prompt=_draw, negative_prompt=negative_prompt, workflow=_wf_name,
            width=width, height=height, loras=loras, seed=seed, count=count, prompts=prompts,
            source=source, image=image, denoise=denoise, slot_values=slot_values, comic_feature="meme_img",
            caption=caption,
        )

    @filter.command("无限绘图", alias={"无限发图", "持续发图", "unlimited_draw", "连发图"})
    async def cmd_unlimited_draw(self, event: AstrMessageEvent):
        """按当前用户开启/关闭「不限轮次持续发图」。用法：/无限绘图 开|关（缺省查看状态）。仅对本人生效，状态持久保存；白名单外的普通用户不可用。"""
        uid = (getattr(event, "get_sender_id", lambda: "")() or "").strip()
        # 白名单控制：默认（开关关）管理员不受限随时可用；普通用户须白名单内
        _dm = self._cfg("draw_auto", {}) or {}
        _wl_raw = _dm.get("unlimited_draw_whitelist", "") or ""
        _wl = {x.strip() for x in str(_wl_raw).replace(",", "\n").splitlines() if x.strip()}
        _admin_bypass = not bool(_dm.get("unlimited_draw_whitelist_admin", False))
        _is_admin = bool(self._is_admin(event))
        _need_wl = (not _is_admin) or (not _admin_bypass)
        if _need_wl and uid not in _wl:
            await self._send(event, "该指令仅白名单用户可用。请联系管理员把你加入「无限绘图白名单」后再使用。")
            event.stop_event()
            return
        arg = self._strip_command(
            event.message_str, "无限绘图", ("无限发图", "持续发图", "unlimited_draw", "连发图")
        ).strip().lower()
        if not arg:
            await self._send(event, f"你当前{'已开启' if uid in self._unl_users() else '未开启'}不限轮次持续发图。用 /无限绘图 开 或 关 切换（仅对你本人生效，重启/重载后仍保留）。")
            event.stop_event()
            return
        if arg in ("开", "on", "1", "true", "开启", "打开"):
            self._set_unlimited_user(uid, True)
        elif arg in ("关", "off", "0", "false", "关闭", "关掉"):
            self._set_unlimited_user(uid, False)
        else:
            await self._send(event, "参数用「开」或「关」（on/off）。")
            event.stop_event()
            return
        on = uid in self._unl_users()
        await self._send(event, f"已{'开启' if on else '关闭'}你的不限轮次持续发图（仅对你本人生效，重启/重载后仍保留）——LLM 可{'连续多次发图、一次多张不截断' if on else '按默认闸门控制'}。")
        event.stop_event()

    def _unlimited_users_path(self):
        """「不限轮次发图」用户开关的持久化文件路径。"""
        return self.data_dir / "unlimited_draw.json"

    def _unl_users(self):
        """「不限轮次发图」用户集合：内存缓存 + data_dir/unlimited_draw.json 持久化。

        解决「动不动自动关」：纯内存 set 在插件热更新/重启后会清零，这里改为
        首次访问从磁盘加载、后续走内存缓存，指令修改时写盘。
        """
        _unl = getattr(self, "_unl_users_set", None)
        if _unl is None:
            _unl = set()
            try:
                import json
                _p = self._unlimited_users_path()
                if _p.exists():
                    _data = json.loads(_p.read_text("utf-8"))
                    if isinstance(_data, dict):
                        _unl = {k for k, v in _data.items() if v}
            except Exception:
                _unl = set()
            self._unl_users_set = _unl
        return _unl

    def _set_unlimited_user(self, uid: str, on: bool) -> None:
        _unl = self._unl_users()
        if on:
            _unl.add(uid)
        else:
            _unl.discard(uid)
        try:
            import json
            _p = self._unlimited_users_path()
            _p.parent.mkdir(parents=True, exist_ok=True)
            _p.write_text(json.dumps({u: True for u in sorted(_unl)}, ensure_ascii=False), "utf-8")
        except Exception:
            pass

    @filter.command("img2img", alias={"图生图", "图转图"})
    async def cmd_img2img(self, event: AstrMessageEvent):
        """图生图：用附带的一张图片作为参考图重绘。用法：/图生图 描述 [--wf 工作流] [...]"""
        args = self._strip_command(
            (event.message_str or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
            "img2img",
            ("图生图", "图转图"),
        )
        prompt, lora_map, lora_presets, width, height, wf_name, seed, denoise = self._parse_draw_args(args or "")
        images = await self._extract_images(event)
        # 图生图专用兜底：引用消息的图片因平台未回填 Reply.chain、且引用解析 API
        # 不可用时，退回「用户最近发的图」优先，再退「本插件最近生成的图」。注意：此兜底
        # 仅限图生图入口，绝不进入通用 _extract_images，以免污染纯文生图指令。
        if not images:
            sid = getattr(event, "session_id", "") or ""
            for store in (
                list(reversed(g_last_received.get(sid) or [])),
                list(reversed(g_recent_user_images.get(sid) or [])),
                list(reversed(g_last_generated.get(sid) or [])),
                list(reversed(g_last_generated.get("__global__") or [])),
            ):
                for p in store:
                    if p and os.path.exists(p) and p not in images:
                        images.append(p)
                if images:
                    break
            logger.info(f"【取图】 /img2img 启用兜底图片: {images}")
        if not images:
            await self._send(
                event,
                "图生图需要附带一张参考图哦～ 请在消息里发一张图片，再加上你的描述，例如：/img2img 把背景换成星空",
            )
            event.stop_event()
            return
        # 已读回执由 _ack_command_received 统一处理（覆盖本插件所有指令，含 /img2img）
        async for m, _p in self._do_draw(
            event, wf_name, prompt, "", width, height, lora_map, lora_presets, seed,
            init_images=images,
            is_img2img=True,
            denoise=denoise,
            explicit_default=(wf_name is None),
        ):
            yield m
        event.stop_event()

    @filter.command("萌绘", alias={"萌绘分享", "meng", "share"})
    async def cmd_meng_share(self, event: AstrMessageEvent):
        """发送 /萌绘：给本人生成一条可分享的临时图库链接（或二维码）。
        仅白名单内用户可用；链接带过期时间，过期后失效需重新发送。"""
        cfg = self._cfg("share_webui", {}) or {}
        if not cfg.get("enabled", True):
            yield event.plain_result("分享功能未启用。")
            event.stop_event()
            return
        # 仅限私聊使用：分享链接为个人专属（含本人图库/收藏），群聊不开放
        if not self._is_private_event(event):
            yield event.plain_result("该功能仅支持私聊使用，请私聊机器人发送 /萌绘 获取专属分享链接。")
            event.stop_event()
            return
        uid = (getattr(event, "get_sender_id", lambda: "")() or "").strip()
        uname = ""
        try:
            uname = (getattr(event, "get_sender_name", lambda: "")() or "").strip()
        except Exception:
            pass
        # 白名单（管理员豁免，避免把管理员自己锁在门外；留空=所有人可用）
        wl = (cfg.get("whitelist") or "").strip()
        if wl and not self._is_admin(event):
            allowed = [x.strip() for x in re.split(r"[\s,，;；\n]+", wl) if x.strip()]
            if uid not in allowed:
                yield event.plain_result("你暂无权限使用 /萌绘 分享功能～")
                event.stop_event()
                return
        if self.gallery is None:
            yield event.plain_result("图库功能未启用，无法生成分享链接。")
            event.stop_event()
            return
        ttl = max(1, int(cfg.get("expire_minutes", 60) or 60)) * 60
        token = self.gallery.create_share_token(uid, uname, ttl_sec=ttl)
        base = self._share_base_url()
        # 参数名用 share_t 而非 token：避免被陪伴插件（astrbot_plugin_private_companion）的
        # 发送前敏感凭据脱敏正则（[?&]token=）匹配，导致链接模式的 token 被替换成「密钥已隐藏」。
        url = f"{base}/#/share?share_t={token}"
        mode = (cfg.get("mode") or "qrcode").strip().lower()
        minutes = max(1, int(ttl // 60))
        if mode == "link":
            yield event.plain_result(f"🎨 你的专属萌绘图库（{minutes} 分钟有效）：\n{url}")
        else:
            qr = self._make_share_qr(url, cfg.get("logo", "") or "")
            if qr:
                # 二维码先落盘为临时文件再按路径发送：aiocqhttp 后端不支持 base64:// 内联
                # （会把整个 base64 串当文件路径读取导致「文件名过长」），与出图流程一致用本地路径。
                try:
                    _qr_path = self.temp_dir / f"share_qr_{uuid.uuid4().hex}.png"
                    _qr_path.write_bytes(qr)
                    yield event.image_result(str(_qr_path))
                    yield event.plain_result(f"🎨 扫码进入你的专属萌绘图库（{minutes} 分钟有效）")
                except Exception as _e:
                    logger.warning(f"【萌绘】 二维码生成/发送失败，回退链接: {_e}")
                    yield event.plain_result(f"🎨 你的专属萌绘图库（{minutes} 分钟有效）：\n{url}")
            else:
                yield event.plain_result(f"🎨 你的专属萌绘图库（{minutes} 分钟有效）：\n{url}")
        event.stop_event()

    # 「画」系绘图指令（独立新增指令，非 /draw 别名）：
    #   /画 [工作流名] 提示词   用指定/默认工作流（如 /画 真人 一个女孩）
    #   /绘图 /绘画 /生图 /画图 /作画 /画画 提示词   均用默认工作流
    # 语法约定：触发词后必须跟空格再写内容（触发词紧贴其它字不视为指令，
    # 例如「画风成熟点」不会触发），以规避把闲聊误判为绘图指令。
    # 工作流名是可选的，且必须以空格与提示词分隔；若指定的工作流不存在，
    # 直接回复「xx 工作流不存在」并列出可用工作流，不再静默回退默认。
    # 与 /draw 并存，互不冲突。
    _DRAW_TRIGGER_PATTERN = r"^[/／]?(画|绘图|绘画|生图|画图|作画|画画)(?:\s+([\s\S]+))?$"

    @filter.regex(_DRAW_TRIGGER_PATTERN)
    async def cmd_draw_wf(self, event: AstrMessageEvent):
        """「画」系绘图指令（新增指令，非 /draw 别名）。

        用法：
         /画 提示词 [...]                      用默认工作流（如 /画 一个女孩）
         /画 工作流名 提示词 [...]             用指定工作流（如 /画 真人 一个女孩）
         /绘图|/绘画|/生图|/画图|/作画|/画画 提示词 [...]   用默认工作流（不解析工作流名）
        /画 触发词下工作流名可选：首 token 命中已知工作流才拆出作为工作流名，
        否则一律视为提示词用默认工作流（如 /画 一个女孩 正常作画）。其余触发词
        （绘图/绘画/生图/画图/作画/画画）整句即为提示词。其余参数（--lora / --w /
        --h / --seed / --wf 等）与 /draw 完全一致。"""
        text = (event.message_str or "").strip()
        # 归一化换行：用户经常把提示词写成多行（含 \n/\r），
        # @filter.regex 预匹配及后续参数解析都按单行处理，否则会被换行截断，
        # 导致「画」系指令整体不匹配、消息回流到 LLM Agent。
        text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        m = re.match(self._DRAW_TRIGGER_PATTERN, text, re.S)
        rest = (m.group(2) or "").strip() if m else ""
        if not rest:
            await self._send(event, random.choice(_WF_HINTS["no_arg"]).format(wf="默认"))
            event.stop_event()
            return
        # 自然语言帮助：触发词后跟「帮助/说明/怎么用/咋用/help」等，复用 /drawhelp 输出
        if re.match(r"^(?:帮助|说明|怎么用|咋用|help)$", rest.strip().lower()):
            await self.cmd_help(event)
            event.stop_event()
            return
        # 触发词行为区分：
        #   /画 允许可选工作流名；/绘图|/绘画|/生图|/画图|/作画|/画画 也可解析首 token
        #   工作流名（支持「[图片] /绘图 动漫转真人」这类无提示词调用锁定提示词的工作流）。
        #   规则：首 token 长度 ≤ 10 且命中已知工作流才拆出作为工作流名，否则整句当提示词，
        #   因此「/绘图 一个女孩」不会被误判（"一个女孩" 不是已知工作流）。
        trig = (m.group(1) or "").strip().lstrip("/／")
        allow_wf = True
        # 尝试把 rest 首 token 当作可选工作流名。规则：
        #  - 首 token 长度 > 10（多半是用户直接写提示词，只是恰好开头像工作流名）
        #    → 不解析为工作流，整句当作提示词用默认工作流。
        #  - 首 token 长度 ≤ 10，且命中已知工作流 → 拆出作为指定工作流名。
        #  - 首 token 长度 ≤ 10，且不是已知工作流 → 视为提示词（不报错），仍用默认工作流。
        #    这样「/画 一个女孩」能正常作画；「/画 真人 一个女孩」中「真人」命中才作为工作流名。
        MAX_WF_NAME_LEN = 10
        wf_specified = None
        rest_for_parse = rest
        if allow_wf:
            parts = rest.split(None, 1)
            first_tok = parts[0]
            if len(first_tok) <= MAX_WF_NAME_LEN:
                try:
                    self._resolve_workflow(first_tok)
                    wf_specified = first_tok
                    rest_for_parse = parts[1] if len(parts) > 1 else ""
                except ValueError:
                    # 不是已知工作流：静默当作提示词，用默认工作流
                    rest_for_parse = rest
        prompt, lora_map, lora_presets, width, height, wf_arg, seed, denoise = self._parse_draw_args(rest_for_parse)
        # 工作流优先级：显式 --wf > 首 token 推断的工作流名 > 默认
        wf_name = wf_arg or wf_specified
        # 空提示词拦截：仅当「既无提示词又未指定工作流」时才提示用法。
        # 指定了工作流名则放行——该工作流可能锁定提示词（require_prompt=false，
        # 无提示词即可出图，如 [图片] /绘图 动漫转真人），由 _do_draw 统一处理。
        if not prompt.strip() and not wf_name:
            await self._send(event, random.choice(_WF_HINTS["no_arg"]).format(wf="默认"))
            event.stop_event()
            return
        # 提前取图：决定是否走图生图默认工作流
        images = await self._extract_images(event)
        is_img = bool(images)
        # 校验工作流：用户显式指定了工作流（--wf 或首 token 命中）则必须存在，
        # 不存在直接回复可用列表，不再静默回退默认工作流。
        if wf_name is not None:
            try:
                self._resolve_workflow(wf_name, is_img2img=is_img)
            except ValueError as e:
                await self._send(event, str(e))
                event.stop_event()
                return
        # 若消息或引用(回复)里带了图片，则按图生图处理（参考图注入 LoadImage 节点）。
        # 否则走普通文生图。这样「画 真人 一个女孩 + 引用图片」也能自动图生图。
        async for out, _p in self._do_draw(
            event, wf_name, prompt, "", width, height, lora_map, lora_presets, seed,
            init_images=images,
            is_img2img=is_img,
            denoise=denoise,
            explicit_default=(wf_name is None),
        ):
            yield out
        # 收尾终止事件：同 /draw，避免 pipeline 在首个 yield 后中断 _do_draw
        event.stop_event()

    def _parse_draw_args(self, text: str):
        """解析绘图指令参数，返回 (prompt, lora_map, lora_presets, width, height, workflow, seed, denoise)。

        支持参数：
          --wf 工作流名                       指定工作流
          --lora 名称[:权重]                  指定 LoRA（旧写法，兼容）
          --名称[:权重]                       LoRA 简写：--安魂曲 = --lora 安魂曲:1；
                                                               --安魂曲:0.5 = --lora 安魂曲:0.5
          --名称/预设名[:权重]                 LoRA + 预设：--安魂曲/预设1 = 用「安魂曲」的「预设1」提示词
                                                               （冒号支持半角 : 与全角 ：；预设名与名称之间用 / 分隔，
                                                                以免和 LoRA 名字里常见的 - 冲突）
          --w 宽 / --h 高                     分辨率
          --seed 数字                         随机种子
          --denoise 数字                      降噪幅度（0~1），图生图时控制重绘强度
        权重缺省为 1.0。lora_map 为 {名称: 权重|None}，lora_presets 为 {名称: 预设名}。
        """
        # 已知"取值型"参数：后接一个值 token（--wf sd / --w 768 / --lora 名:权）
        VALUE_FLAGS = {"--lora", "--wf", "--w", "--h", "--seed", "--denoise"}
        lora_map: dict[str, float | None] = {}
        lora_presets: dict[str, str] = {}
        width = height = wf_name = seed = denoise = None

        def add_lora(tok: str) -> None:
            # tok 形如 "安魂曲" / "安魂曲:0.5" / "安魂曲/预设1" / "安魂曲/预设1:0.5"
            tok = tok.replace("：", ":")  # 全角冒号归一
            # 先拆权重（最后一个冒号之后）
            if ":" in tok:
                namepart, wt = tok.split(":", 1)
            else:
                namepart, wt = tok, None
            # 再拆 名称/预设（用 / 分隔，避免与 LoRA 名字里常见的 - 冲突）
            preset = None
            if "/" in namepart:
                nm, preset = namepart.split("/", 1)
            else:
                nm = namepart
            nm = nm.strip()
            if not nm:
                return
            try:
                weight = float(wt) if wt is not None else None
            except ValueError:
                weight = None
            lora_map[nm] = weight
            if preset:
                lora_presets[nm] = preset.strip()

        tokens = text.split()
        i = 0
        prompt_parts: list[str] = []
        while i < len(tokens):
            tok = tokens[i]
            if tok.startswith("--"):
                if tok in VALUE_FLAGS:
                    if i + 1 < len(tokens):
                        val = tokens[i + 1]
                        if tok == "--lora":
                            add_lora(val)
                        elif tok == "--wf":
                            wf_name = val
                        elif tok == "--w":
                            try:
                                width = int(val)
                            except ValueError:
                                width = None
                        elif tok == "--h":
                            try:
                                height = int(val)
                            except ValueError:
                                height = None
                        elif tok == "--seed":
                            try:
                                seed = int(val)
                            except ValueError:
                                seed = None
                        elif tok == "--denoise":
                            try:
                                denoise = float(val)
                            except ValueError:
                                denoise = None
                        i += 2
                        continue
                    else:
                        # 取值参数后无值，跳过
                        i += 1
                        continue
                else:
                    # 未知 --xxx：视为 LoRA 简写（去掉前导 --）
                    add_lora(tok[2:])
                    i += 1
                    continue
            else:
                prompt_parts.append(tok)
                i += 1

        prompt = " ".join(prompt_parts).strip()
        return prompt, (lora_map or None), (lora_presets or None), width, height, wf_name, seed, denoise

    # ------------------------------------------------------------------ #
    # 指令：/loralist 列出可配置 LoRA
    # ------------------------------------------------------------------ #
    @filter.command("loralist", alias={"绘图lora", "绘图LoRA", "lora列表"})
    async def cmd_loralist(self, event: AstrMessageEvent):
        """列出可用的 LoRA 名称（简洁）。

        用法：/绘图lora [角色|风格|工具]
          无参数   列出全部 LoRA 名称
          角色     只列「角色」分类
          风格     只列「风格」分类
          工具     只列「工具」分类（如加速 LoRA）
        仅返回名称，便于快速选择；需要更全信息可在 WebUI 配置页查看。
        """
        args = self._strip_command(event.message_str, "loralist", ("绘图lora", "绘图LoRA", "lora列表"))
        cat = ""
        for kw in ("角色", "风格", "工具"):
            if kw in (args or ""):
                cat = kw
                break
        lib = self._lora_library()
        if not lib:
            await self._send(event, "当前未配置任何 LoRA，可在插件配置页的 LoRA 库中添加。")
            event.stop_event()
            return
        hits = []
        for l in lib:
            name = (l.get("name") or "").strip()
            if not name:
                continue
            if cat and (l.get("category") or "").strip() != cat:
                continue
            hits.append(name)
        if not hits:
            await self._send(event, f"没有「{cat}」分类的 LoRA。" if cat else "没有可用的 LoRA。")
            event.stop_event()
            return
        title = f"{cat} LoRA（共 {len(hits)} 个）：" if cat else f"可用 LoRA（共 {len(hits)} 个）："
        await self._send(event, title + "\n" + "\n".join(f"- {n}" for n in hits))
        event.stop_event()

    @filter.command("绘图工作流lora", alias={"工作流lora", "wf_lora", "wf-lora"})
    async def cmd_workflow_loras(self, event: AstrMessageEvent):
        """列出指定工作流可使用的 LoRA（按底模匹配）。

        用法：/绘图工作流lora 动漫
        """
        args = self._strip_command(event.message_str, "绘图工作流lora", ("工作流lora", "wf_lora", "wf-lora"))
        wf_name = (args or "").strip()
        if not wf_name:
            await self._send(event, "用法：/绘图工作流lora 工作流名，如 /绘图工作流lora 动漫")
            event.stop_event()
            return
        try:
            wf = self._resolve_workflow(wf_name)
        except ValueError as e:
            await self._send(event, str(e))
            event.stop_event()
            return
        lib = self._lora_library()
        hits = []
        for l in lib:
            name = (l.get("name") or "").strip()
            if not name:
                continue
            if self._lora_matches_wf(l, wf):
                hits.append(name)
        if not hits:
            await self._send(event, f"工作流「{wf.get('name')}」当前没有可用的 LoRA（可先去 WebUI 配置添加）。")
            event.stop_event()
            return
        wf_bm = (wf.get("base_model") or "").strip()
        title = f"工作流「{wf.get('name')}」可用的 LoRA（共 {len(hits)} 个）"
        if wf_bm:
            title += f"，底模 {wf_bm}"
        title += "："
        await self._send(event, title + "\n" + "\n".join(f"- {n}" for n in hits))
        event.stop_event()

    # ------------------------------------------------------------------ #
    # 指令：/loraon /loraoff 持久化启用/禁用某个 LoRA
    # ------------------------------------------------------------------ #
    @filter.command("loraon")
    async def cmd_loraon(self, event: AstrMessageEvent):
        """启用某个 LoRA（持久化）。用法：/loraon 名称 [--wf 工作流名]"""
        args = self._strip_command(event.message_str, "loraon")
        await self._set_lora_enabled(args, True, event)
        event.stop_event()

    @filter.command("loraoff")
    async def cmd_loraoff(self, event: AstrMessageEvent):
        """禁用某个 LoRA（持久化）。用法：/loraoff 名称 [--wf 工作流名]"""
        args = self._strip_command(event.message_str, "loraoff")
        await self._set_lora_enabled(args, False, event)
        event.stop_event()

    async def _set_lora_enabled(self, args: str, enabled: bool, event):
        m = re.search(r"--wf\s+(\S+)", args or "")
        wf_name = m.group(1) if m else None
        name = (args or "").split("--wf")[0].strip()
        if not name:
            await self._send(event, "请指定 LoRA 名称，例如：/loraon catgirl")
            return
        try:
            wf = self._resolve_workflow(wf_name)
            workflows = self._workflows()
            wf_index = workflows.index(wf)
            # 直接操作工作流里的 loras_text（仅 名称|权重|是否启用）
            entries = self._parse_loras_text((wf.get("loras_text") or "").strip())
            target = None
            for i, l in enumerate(entries):
                if (l.get("name") or "").strip() == name:
                    target = i
                    break
            if target is None:
                # 不在工作流默认列表里：若全局库里有，则追加一条默认（权重 1.0）
                lib = self._lora_lib_index()
                if name in lib:
                    entries.append({"name": name, "weight": 1.0, "enabled": enabled})
                else:
                    await self._send(
                        event,
                        f"找不到 LoRA「{name}」：既不在本工作流的默认列表，也不在全局"
                        " LoRA 库里。请先到插件配置的「LoRA 库」中添加。",
                    )
                    return
            else:
                entries[target]["enabled"] = enabled
            workflows[wf_index]["loras_text"] = self._serialize_loras_text(entries)
            workflows[wf_index].pop("loras", None)
            self.config["workflows"] = workflows
            self.config.save_config()
        except ValueError as e:
            await self._send(event, str(e))
            return
        except Exception as e:
            logger.error(f"【LoRA·开关】 操作失败: {type(e).__name__}: {e}", exc_info=True)
            await self._send(event, "保存 LoRA 设置时出错，请稍后再试或联系管理员。")
            return
        state = "启用" if enabled else "禁用"
        await self._send(event, f"已将 LoRA「{name}」{state}（已保存）。")

    # ------------------------------------------------------------------ #
    # 指令：/queuestatus 查询队列
    # ------------------------------------------------------------------ #
    @filter.command("queuestatus", alias={"绘图队列", "队列状态"})
    async def cmd_queuestatus(self, event: AstrMessageEvent):
        """查询本地队列状态，以及你最近一次任务前面还有多少位。可用 --wf 指定服务器所在工作流。"""
        args = self._strip_command(event.message_str, "queuestatus", ("绘图队列", "队列状态"))
        m = re.search(r"--wf\s+(\S+)", args or "")
        wf_name = m.group(1) if m else None
        try:
            wf = self._resolve_workflow(wf_name)
            server = self._resolve_server(wf.get("server_name") or None)
        except ValueError as e:
            await self._send(event, str(e))
            return
        srv_key = self._server_key(server)
        pending = self._server_pending.get(srv_key, [])
        lines = [
            f"ComfyUI「{server.get('name')}」本地队列：",
            f"待处理任务：{len(pending)} 个（含正在生成）",
        ]
        pid = self._last_prompt.get(event.session_id or "global")
        if pid and pid in pending:
            pos = pending.index(pid)
            if pos == 0:
                lines.append("你的最近一次任务正在生成中。")
            else:
                lines.append(f"你的最近一次任务前面还有 {pos} 位。")
        else:
            lines.append("你的最近一次任务已不在本地队列中（可能已完成）。")
        await self._send(event, "\n".join(lines))
        event.stop_event()

    # ------------------------------------------------------------------ #
    # 指令：/绘图统计 出图与 token 统计
    # ------------------------------------------------------------------ #
    @filter.command("绘图统计", alias={"drawstats", "画图统计"})
    async def cmd_draw_stats(self, event: AstrMessageEvent):
        """统计：累计出图、指定范围（今天/昨天/最近一周等）出图数量、token 用量、热门工作流出图数与平均耗时。

        用法：/绘图统计 [今天|昨天|周|月|全部]（默认今天）。
        """
        args = (self._strip_command(event.message_str, "绘图统计") or "").strip().lower()
        # 解析时间范围
        scope_label = "今天"
        start_ts: float | None = None
        end_ts: float | None = None
        days: int | None = 0  # 供 user_ranking / workflow_stats / list_daily 使用；昨天用区间
        if args in ("", "今天", "today"):
            scope_label = "今天"
            start_ts, end_ts, days = None, None, 0
        elif args in ("昨天", "yesterday", "y"):
            scope_label = "昨天"
            start_ts, end_ts, days = None, None, -1  # -1 占位，走区间
        elif args in ("周", "周7", "7", "week", "w"):
            scope_label = "最近一周"
            start_ts, end_ts, days = None, None, 7
        elif args in ("月", "30", "month", "m"):
            scope_label = "最近30天"
            start_ts, end_ts, days = None, None, 30
        elif args in ("全部", "all"):
            scope_label = "全部"
            start_ts, end_ts, days = None, None, None
        else:
            await self._send(event, "用法：/绘图统计 [今天|昨天|周|月|全部]（默认今天）")
            event.stop_event()
            return
        # 计算昨天范围 / 今天范围起点（本地时区）
        now = time.time()
        lt = time.localtime(now)
        day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        if days == 0:  # 今天
            start_ts = day_start
        elif days == -1:  # 昨天
            start_ts = day_start - 86400
            end_ts = day_start
        elif days == 7:
            start_ts = day_start - 6 * 86400
        elif days == 30:
            start_ts = day_start - 29 * 86400

        lines = [f"📊 绘图统计（{scope_label}）"]
        # 出图数量
        if self.gallery is not None:
            try:
                st = self.gallery.stats()
                total = st.get("total", 0) if isinstance(st, dict) else 0
                lines.append(f"· 累计出图：{total} 张")
            except Exception as e:
                lines.append(f"· 累计出图：读取失败（{e}）")
            try:
                if days == -1 or days == 0:
                    # 昨天/今天用区间精确统计
                    scope_total = self.gallery.range_stats(start_ts=start_ts, end_ts=end_ts).get("total", 0)
                elif days is not None:
                    scope_total = self.gallery.user_ranking(days=days).get("total", 0)
                else:
                    scope_total = self.gallery.user_ranking(days=None).get("total", 0)
                lines.append(f"· {scope_label}出图：{scope_total} 张")
            except Exception as e:
                lines.append(f"· {scope_label}出图：读取失败（{e}）")
        else:
            lines.append("· 累计出图：图库未启用")
        # 范围 token 用量
        if self.token_store is not None:
            try:
                if days == 0 or days == -1:
                    d = self.token_store.list_daily(days=1 if days == 0 else 2)
                    tok = int(d[0]["total"]) if d else 0
                elif days is not None:
                    d = self.token_store.list_daily(days=days)
                    tok = sum(int(x["total"] or 0) for x in d)
                else:
                    d = self.token_store.list_daily(days=0)  # 全部历史
                    tok = sum(int(x["total"] or 0) for x in d)
                lines.append(f"· {scope_label} Token 用量：{self._fmt_token(tok)}")
            except Exception as e:
                lines.append(f"· {scope_label} Token 用量：读取失败（{e}）")
        else:
            lines.append(f"· {scope_label} Token 用量：统计未启用")
        # 热门工作流 Top5（出图数量 + 平均耗时）
        if self.gallery is not None:
            try:
                if days == -1 or days == 0:
                    wfs = self.gallery.range_stats(start_ts=start_ts, end_ts=end_ts).get("workflows", [])
                else:
                    wfs = self.gallery.workflow_stats(top=5, days=days)
                if wfs:
                    lines.append("· 热门工作流出图：")
                    for w in wfs:
                        speed = "—" if not w["avg_sec"] else f"{w['avg_sec']}s/张"
                        lines.append(f"    · {w['workflow']}：{w['count']} 张（平均 {speed}）")
                else:
                    lines.append("· 热门工作流：暂无数据")
            except Exception as e:
                lines.append(f"· 热门工作流：读取失败（{e}）")
        await self._send(event, "\n".join(lines))
        event.stop_event()

    # ------------------------------------------------------------------ #
    # 指令：/绘图排行 今日生图前五
    # ------------------------------------------------------------------ #
    @filter.command("绘图排行", alias={"drawrank", "画图排行"})
    async def cmd_draw_rank(self, event: AstrMessageEvent):
        """展示生图数量前五名的用户（排除伴侣插件自动生图），支持日期范围参数。

        用法：/绘图排行 [今天|昨天|周|月|全部]（默认今天）。"""
        if self.gallery is None:
            await self._send(event, "图库未启用，无法统计排行。")
            event.stop_event()
            return
        try:
            args = (self._strip_command(event.message_str, "绘图排行") or "").strip().lower()
            # 解析时间范围（与 /绘图统计 一致）
            scope_label = "今天"
            start_ts: float | None = None
            end_ts: float | None = None
            days: int | None = 0
            if args in ("", "今天", "today"):
                scope_label, days = "今天", 0
            elif args in ("昨天", "yesterday", "y"):
                scope_label, days = "昨天", -1
            elif args in ("周", "周7", "7", "week", "w"):
                scope_label, days = "最近一周", 7
            elif args in ("月", "30", "month", "m"):
                scope_label, days = "最近30天", 30
            elif args in ("全部", "all"):
                scope_label, days = "全部", None
            else:
                await self._send(event, "用法：/绘图排行 [今天|昨天|周|月|全部]（默认今天）")
                event.stop_event()
                return
            # 昨天用区间（昨天 0 点到今天 0 点）；今天/周/月/全部用 user_ranking 的 days 语义
            if days == -1:
                now = time.time()
                lt = time.localtime(now)
                day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
                start_ts = day_start - 86400
                end_ts = day_start
                rank_days = None
            else:
                rank_days = days
            data = self.gallery.user_ranking(
                days=rank_days, limit=50, start_ts=start_ts, end_ts=end_ts,
            )
            rows = data.get("rows") or []
            # 过滤掉插件自动生图（无真实 user_id，或 user_name 为伴侣插件名）
            plugin_names = {SOURCE_COMPANION_PLUGIN, "PrivateCompanion"}
            human = []
            for r in rows:
                name = (r.get("user_name") or "").strip()
                uid = (r.get("user_id") or "").strip()
                if name in plugin_names:
                    continue
                if not uid or uid in ("__system__",) or uid.startswith("__"):
                    continue
                human.append(r)
            top = human[:5]
            lines = [f"🏆 {scope_label}绘图排行（前 5）"]
            if not top:
                lines.append(f"· {scope_label}还没有人生图～")
            else:
                medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
                for i, r in enumerate(top):
                    medal = medals[i] if i < len(medals) else f"{i + 1}."
                    name = r.get("user_name") or r.get("user_id") or "未知用户"
                    lines.append(f"{medal} {name}：{r.get('count', 0)} 张")
            await self._send(event, "\n".join(lines))
        except Exception as e:
            await self._send(event, f"读取排行失败：{e}")
        event.stop_event()

    # ------------------------------------------------------------------ #
    # 指令：/绘图状态 服务器连通 / 延迟 / 队列
    # ------------------------------------------------------------------ #
    @filter.command("绘图状态", alias={"drawstatus", "画图状态"})
    async def cmd_draw_status(self, event: AstrMessageEvent):
        """查询绘图服务器连通情况、延迟，以及正在出图还是空闲、队列数量。

        只探测启用的服务器；用 /queue 一次请求同时测得连通性、延迟与队列状态，
        不额外请求 system_stats。展示时不暴露服务器名称/IP。
        """
        servers = self._servers()
        active = [
            s for s in servers
            if bool(s.get("enabled", True)) and (s.get("url") or "").strip()
        ]
        if not active:
            await self._send(event, "当前没有正在使用的绘图服务器。")
            event.stop_event()
            return
        lines = ["🖥️ 绘图服务器状态"]
        for idx, s in enumerate(active, 1):
            url = s["url"].strip()
            # 探测用较短的超时（不可达时更快返回），整体 60s 上限（连接/握手慢的服务器也给足等待）
            client = comfyui_client.ComfyUIClient(url, timeout=60, probe_timeout=20)
            # 1) 用根路径探测连通性与 HTTP 往返耗时（不依赖 system_stats 等可能 404 的端点）
            p = await client.probe()
            if not p.get("ok"):
                lines.append(f"· 服务器{idx}：🔴 不可达（{p.get('error', '')}）")
                await self._safe_close(client)
                continue
            latency = int(p.get("elapsed_ms", 0))
            # 2) 再查队列状态；/queue 不可用时回退本地队列近似
            state = "空闲"
            try:
                q = await client.get_queue()
                running = len(q.get("queue_running") or [])
                pending = len(q.get("queue_pending") or [])
                if running > 0:
                    state = f"正在出图（{running} 个，队列 {pending} 个）"
                elif pending > 0:
                    state = f"排队中（{pending} 个待处理）"
                else:
                    state = "空闲"
            except Exception:
                srv_key = self._server_key(s)
                local = len(self._server_pending.get(srv_key, []))
                state = "正在出图" if local > 0 else "空闲"
            lines.append(f"· 服务器{idx}：🟢 正常（HTTP 往返 {latency}ms）· {state}")
            await self._safe_close(client)
        # 生图限额配置
        lines.append("")
        lines.append("📊 生图限额配置")
        try:
            qc = self._draw_limit_cfg()
            enabled = bool(qc.get("enabled", False))
            lines.append(f"· 开关：{'已开启' if enabled else '未开启'}")
            fmt_n = lambda n: "不限" if int(n) < 0 else str(n)
            lines.append(f"· 总次数 / 每小时 / 每天：{fmt_n(qc.get('max_total', -1))} / {fmt_n(qc.get('max_hour', -1))} / {fmt_n(qc.get('max_day', -1))}")
            lines.append(f"· 管理员豁免：{'是' if qc.get('admin_exempt', False) else '否'}")
            if self.quota is not None:
                users = self.quota.list_users()
                day_total = sum(int(u.get("day_used") or 0) for u in users)
                lines.append(f"· 今日全群已生图：{day_total} 次")
        except Exception as e:
            lines.append(f"· 限额配置读取失败（{e}）")
        await self._send(event, "\n".join(lines))
        event.stop_event()

    # ------------------------------------------------------------------ #
    # 指令：/拉黑 /解黑 /黑名单 绘图黑名单管理（仅管理员）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_blacklist_arg(args: str) -> tuple[str, str]:
        """解析黑名单指令参数，返回 (类型, 号码)。类型为 'group' 或 'user'。
        支持 `/拉黑 123`（用户）、`/拉黑 群 123`（群）、`/拉黑 用户 123`。"""
        args = (args or "").strip()
        parts = args.split(None, 1)
        if not parts:
            return "user", ""
        first = parts[0].strip()
        if first in ("群", "群号", "group", "g"):
            return "group", (parts[1].strip() if len(parts) > 1 else "")
        if first in ("用户", "人", "user", "u"):
            return "user", (parts[1].strip() if len(parts) > 1 else "")
        return "user", args

    def _set_blacklist_entry(self, key: str, num: str, add: bool) -> tuple[bool, str]:
        """在黑名单配置里添加/移除一个号码。返回 (是否变更, 提示)。"""
        bl = dict(self._blacklist_cfg())
        cur = self._parse_id_list(bl.get(key, ""))
        if add:
            if num in cur:
                return False, "已在黑名单中。"
            cur.add(num)
            bl[key] = "\n".join(sorted(cur))
            bl["enabled"] = True  # 拉黑时自动开启黑名单
        else:
            if num not in cur:
                return False, "不在黑名单中。"
            cur.discard(num)
            bl[key] = "\n".join(sorted(cur))
            if not cur and key == "users" and not self._parse_id_list(bl.get("groups", "")):
                bl["enabled"] = False  # 全部清空时关闭开关
        self.config["blacklist"] = bl
        self.config.save_config()
        return True, ""

    @filter.command("拉黑", alias={"blacklist_add", "加黑名单"})
    async def cmd_blacklist_add(self, event: AstrMessageEvent):
        """把用户或群加入绘图黑名单。用法：/拉黑 [群|用户] 号码（缺省按用户）。仅管理员。"""
        if not self._is_admin(event):
            await self._send(event, "只有管理员可以管理黑名单。")
            event.stop_event()
            return
        kind, num = self._parse_blacklist_arg(self._strip_command(event.message_str, "拉黑"))
        if not num:
            await self._send(event, "用法：/拉黑 [群|用户] 号码  （缺省按用户）")
            event.stop_event()
            return
        key = "groups" if kind == "group" else "users"
        label = "群" if kind == "group" else "用户"
        changed, tip = self._set_blacklist_entry(key, num, add=True)
        await self._send(event, f"已将{label}「{num}」{tip if not changed else '加入绘图黑名单。'}")
        event.stop_event()

    @filter.command("解黑", alias={"blacklist_remove", "移出黑名单"})
    async def cmd_blacklist_remove(self, event: AstrMessageEvent):
        """把用户或群移出绘图黑名单。用法：/解黑 [群|用户] 号码（缺省按用户）。仅管理员。"""
        if not self._is_admin(event):
            await self._send(event, "只有管理员可以管理黑名单。")
            event.stop_event()
            return
        kind, num = self._parse_blacklist_arg(self._strip_command(event.message_str, "解黑"))
        if not num:
            await self._send(event, "用法：/解黑 [群|用户] 号码  （缺省按用户）")
            event.stop_event()
            return
        key = "groups" if kind == "group" else "users"
        label = "群" if kind == "group" else "用户"
        changed, tip = self._set_blacklist_entry(key, num, add=False)
        await self._send(event, f"已将{label}「{num}」{tip if not changed else '移出绘图黑名单。'}")
        event.stop_event()

    @filter.command("黑名单", alias={"blacklist", "查看黑名单"})
    async def cmd_blacklist_show(self, event: AstrMessageEvent):
        """查看当前绘图黑名单（用户 + 群 + 开关）。仅管理员。"""
        if not self._is_admin(event):
            await self._send(event, "只有管理员可以查看黑名单。")
            event.stop_event()
            return
        bl = self._blacklist_cfg()
        users = sorted(self._parse_id_list(bl.get("users", "")))
        groups = sorted(self._parse_id_list(bl.get("groups", "")))
        enabled = bool(bl.get("enabled", False))
        lines = ["🚫 绘图黑名单"]
        lines.append(f"· 开关：{'已开启' if enabled else '已关闭'}")
        lines.append(f"· 黑名单用户（{len(users)}）：" + (("、".join(users)) if users else "（空）"))
        lines.append(f"· 黑名单群（{len(groups)}）：" + (("、".join(groups)) if groups else "（空）"))
        lines.append("· 管理员豁免：是" if bl.get("admin_exempt", True) else "· 管理员豁免：否")
        await self._send(event, "\n".join(lines))
        event.stop_event()

    # ------------------------------------------------------------------ #
    # 指令：/workflows 列出/选择默认工作流
    # ------------------------------------------------------------------ #
    @filter.command("workflows", alias={"绘图工作流", "工作流列表"})
    async def cmd_workflows(self, event: AstrMessageEvent):
        """列出工作流，或设置默认工作流：/绘图工作流 set 动漫文生图 | set_real 真人文生图 | set_img2img 动漫图生图 | set_img2img_real 真人图生图
        也可启用/停用工作流：/绘图工作流 enable 动漫 | /绘图工作流 disable 动漫（中文：启用/停用/禁用）"""
        args = self._strip_command(event.message_str, "workflows", ("绘图工作流", "工作流列表"))
        # 启用/停用工作流：enable|启用 <名称> / disable|停用|禁用 <名称>
        m_en = re.match(r"(?:enable|启用)\s+(\S+)", (args or "").strip())
        m_dis = re.match(r"(?:disable|停用|禁用)\s+(\S+)", (args or "").strip())
        if m_en or m_dis:
            is_en = bool(m_en)
            wname = (m_en or m_dis).group(1).strip()
            target = next(
                (w for w in self._workflows() if (w.get("name") or "").strip() == wname),
                None,
            )
            if target is None:
                await self._send(event, f"找不到名为「{wname}」的工作流。")
            else:
                target["enabled"] = is_en
                try:
                    self.config.save_config()
                    await self._send(event, f"已{'启用' if is_en else '停用'}工作流「{wname}」。")
                except Exception as _e:
                    await self._send(event, f"保存配置失败：{_e}")
            event.stop_event()
            return
        # set_img2img_real / set_img2img / set_real / set 按长度优先匹配，防止被 set 正则吞掉后缀
        m_i2i_real = re.match(r"set_img2img_real\s+(\S+)", (args or "").strip())
        m_i2i = re.match(r"set_img2img\s+(\S+)", (args or "").strip())
        m_real = re.match(r"set_real\s+(\S+)", (args or "").strip())
        m = re.match(r"set\s+(\S+)", (args or "").strip())
        if m_i2i_real:
            key, label = "default_img2img_workflow_real", "真人图生图"
        elif m_i2i:
            key, label = "default_img2img_workflow", "动漫图生图"
        elif m_real:
            key, label = "default_workflow_real", "真人文生图"
        elif m:
            key, label = "default_workflow", "动漫文生图"
        else:
            key, label, name = None, None, None
        if key:
            name = (m_i2i_real or m_i2i or m_real or m).group(1)
            try:
                self._resolve_workflow(name)
            except ValueError as e:
                await self._send(event, str(e))
                return
            self.config[key] = name
            self.config.save_config()
            await self._send(event, f"已将「{label}」默认工作流设为「{name}」。")
            event.stop_event()
            return
        workflows = self._workflows()
        default = self._cfg("default_workflow", "")
        default_real = self._cfg("default_workflow_real", "")
        default_i2i = self._cfg("default_img2img_workflow", "")
        default_i2i_real = self._cfg("default_img2img_workflow_real", "")
        if not workflows:
            await self._send(event, "尚未配置任何工作流。")
            event.stop_event()
            return
        lines = ["已配置的工作流："]
        for w in workflows:
            wname = w.get("name")
            tags = []
            if wname == default:
                tags.append("动漫文生图默认")
            if wname == default_real:
                tags.append("真人文生图默认")
            if wname == default_i2i:
                tags.append("动漫图生图默认")
            if wname == default_i2i_real:
                tags.append("真人图生图默认")
            tag = f"（{'，'.join(tags)}）" if tags else ""
            anima = " 【Anima】" if w.get("is_anima") else ""
            disabled = " 【已停用】" if not w.get("enabled", True) else ""
            lines.append(f"- {wname}{anima}{tag}{disabled}")
        lines.append("")
        lines.append("用 /绘图工作流 enable <名称> 或 disable <名称> 可启用/停用（中文：启用/停用）")
        await self._send(event, "\n".join(lines))
        event.stop_event()

    # ------------------------------------------------------------------ #
    # 指令：/drawhelp 帮助
    # ------------------------------------------------------------------ #
    @filter.command("drawhelp")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示绘图插件帮助。"""
        text = (
            "ComfyUI 绘图插件使用帮助：\n"
            "/draw 提示词 [--wf 工作流] [--lora 名称[:权重] | --名称[:权重] | --名称/预设名[:权重]] [--w 宽] [--h 高] [--seed 数字] [--denoise 0~1]  绘图\n"
            "  · LoRA 简写：--安魂曲 等价于 --lora 安魂曲:1；--安魂曲:0.5 等价于 --lora 安魂曲:0.5（冒号支持半角 : 与全角 ：）\n"
            "  · LoRA 预设：--安魂曲/预设1 表示用「安魂曲」的「预设1」提示词（在全局 LoRA 库里配置多套预设，名称与预设名之间用 / 分隔）。\n"
            "  · 若消息带了图片，自动切换为图生图模式并使用图生图默认工作流。\n"
            "/img2img 描述 [--wf 工作流] [...]  图生图（必须附带参考图）\n"
            "/画 [工作流名] 提示词 [...]   用指定/默认工作流作画（如 /画 真人 一个女孩）；工作流名可选、以空格分隔，找不到该工作流时回复可用列表\n"
            "/绘图 | /绘画 | /生图 | /画图 | /作画 | /画画 [工作流名] 提示词 [...]   以上触发词首 token 命中已知工作流即用作工作流名（如 /绘图 动漫转真人）；未命中则当提示词用默认工作流\n"
            "  · 无提示词工作流（工作流设置里「锁定提示词=开启」）：可只传图/引用图 + 工作流名，无需写提示词，如 [图片] /绘图 动漫转真人。图生图类锁定工作流必须附图。\n"
            "  · 以上任意中文触发词后跟「帮助/说明/怎么用」（如「画画帮助」「作图帮助」「绘图帮助」）也会显示本帮助。\n"
            "/loralist [--wf 工作流]   列出 LoRA（含预设）\n"
            "/loraon 名称 [--wf 工作流]  启用 LoRA（持久化到工作流默认列表）\n"
            "/loraoff 名称 [--wf 工作流] 禁用 LoRA（持久化）\n"
            "/queuestatus [--wf 工作流]  查看队列与排队位置\n"
            "/workflows [set 名称 | set_img2img 名称 | enable 名称 | disable 名称]   列出/设置默认/启用停用工作流（enable/disable 可写中文 启用/停用）\n"
            '也可直接对 AI 说"画一只猫，使用 xxx lora"，由 AI 自动调用绘图工具。'
        )
        await self._send(event, text)
        event.stop_event()

    @filter.command("绘图帮助", alias={"画图帮助", "作图帮助", "绘图说明", "画图说明"})
    async def cmd_help_simple(self, event: AstrMessageEvent):
        """简单的中文绘图指令帮助。"""
        text = (
            "🎨 中文画图指令（简单版）：\n"
            "· 画图：直接说「画一张…」或「画…」，如「画一只猫」；也可说「用 xxx 风格画…」\n"
            "· /画 [工作流名] 提示词   用默认或指定工作流画（如 /画 真人 一个女孩）\n"
            "· /绘图 /绘画 /生图 /画图 /作画 /画画 [工作流名] 提示词   首 token 命中已知工作流即用作工作流名（如 /绘图 动漫转真人）\n"
            "· 无提示词工作流：可只传图/引用图 + 工作流名（无需提示词；图生图类必须附图）\n"
            "· /图生图 描述 + 参考图   图生图（英文 /img2img 亦可）\n"
            "· /绘图lora [角色|风格|工具]   查看可用 LoRA（英文 /loralist 亦可）\n"
            "· /绘图工作流lora 工作流名   查看某工作流可用的 LoRA，如 /绘图工作流lora 动漫\n"
            "· /绘图工作流   查看 / 设置默认工作流，可 enable/disable 启用停用（英文 /workflows 亦可）\n"
            "· /绘图队列   查看排队状态（英文 /queuestatus 亦可）\n"
            "· /绘图统计 [今天|昨天|周|月|全部]   出图统计\n"
            "· /绘图排行 [今天|昨天|周|月|全部]   绘图排行前五\n"
            "· /绘图状态   服务器状态与生图限额\n"
            "· /图库 列表|搜索|收藏…   图库管理\n"
            "· /涩图检测（引用图片）   检测图片是否为涩涩内容\n"
            "· 想查看详细参数，回复「画画帮助」即可"
        )
        # 优先发静态帮助图（随插件打包，零渲染开销），失败回退文字
        try:
            _help_img = Path(__file__).resolve().parent / "assets" / "draw_help.png"
            if _help_img.is_file():
                await event.send(MessageChain([Image.fromFileSystem(str(_help_img))]))
                event.stop_event()
                return
        except Exception as _e:
            try:
                self.logger.warning(f"【绘图·解析】 静态帮助图发送失败，回退文字: {_e}")
            except Exception:
                pass
        await self._send(event, text)
        event.stop_event()

    # ------------------------------------------------------------------ #
    # 指令：/gallery 图片画廊与语义标签召回
    # ------------------------------------------------------------------ #
    @staticmethod
    def _gallery_desc(row: dict, n: int = 40) -> str:
        """图库列表描述：标签优先；无标签时用用户发送的消息（trigger_msg）前 n 字，
        无触发消息则回退取提示词前 n 字。"""
        if row.get("tags"):
            return " #" + " #".join(row["tags"])
        msg = (row.get("trigger_msg") or "").strip()
        text = msg if msg else (row.get("prompt") or "").strip()
        return " " + text[:n]

    async def _gallery_resolve_ref(self, event: AstrMessageEvent) -> str | None:
        """指代消解（async 版）：找出当前语境下「这张图」的本地路径。

        优先级：1) 上一条消息里出现的图（await _extract_images）>
        2) 本会话最近生成的图 > 3) 本会话最近收到的图。
        """
        if self.gallery is None:
            return None
        # 1) 当前消息里的图（_extract_images 为 async）
        try:
            _msgs = await self._extract_images(event)
            if _msgs:
                for _p in _msgs:
                    if _p and os.path.exists(_p):
                        return _p
        except Exception as _e:
            logger.warning(f"【图库】 提取当前消息图片失败: {_e}")
        # 2) / 3) 本会话生成 / 收到（ImageStore.resolve_ref 处理）
        return self.gallery.resolve_ref(event, event.session_id or "")

    def _is_admin(self, event) -> bool:
        return bool(getattr(event, "is_admin", lambda: False)())

    def _whitelist_cfg(self) -> dict:
        """发图白名单配置块（allow_draw_users）。

        兼容旧版：v5.0.12 之前为纯文本用户 ID 列表，升级后改为对象结构；
        若读到的是字符串（旧配置），按旧语义转成 {enabled, users, groups=""}。
        """
        raw = self._cfg("allow_draw_users", {}) or {}
        if isinstance(raw, str):
            return {"enabled": bool(raw.strip()), "users": raw, "groups": ""}
        return raw

    def _is_whitelist_active(self) -> bool:
        """白名单是否生效：enabled 且 users/groups 至少其一非空。"""
        wl = self._whitelist_cfg()
        if not wl.get("enabled", False):
            return False
        return bool(
            self._parse_id_list(wl.get("users", ""))
            or self._parse_id_list(wl.get("groups", ""))
        )

    def _event_ids(self, event) -> tuple[str, str]:
        """从事件里尽量可靠地取 (user_id, group_id) 字符串。

        优先用 AStrBot 标准方法 get_sender_id() / get_group_id()；当这些方法在 LLM 工具
        回调的事件上返回空（部分平台/版本下工具事件未填充群号，导致「按群」的白名单 /
        黑名单在工具路径下命中不到，而指令路径 _do_draw 的同一事件群号正常）时，从
        session_id 兜底解析，使工具路径与指令路径的权限判定一致。

        session_id 兜底规则：群消息形如 group_123456（或含 group 前缀）→ 取群号；
        私聊 session_id 即用户号（不含 group 字样）→ 取用户号。
        """
        user_id = ""
        group_id = ""
        if event is not None:
            try:
                fn = getattr(event, "get_sender_id", None)
                if callable(fn):
                    user_id = str(fn() or "").strip()
            except Exception:
                user_id = ""
            try:
                fn = getattr(event, "get_group_id", None)
                if callable(fn):
                    group_id = str(fn() or "").strip()
            except Exception:
                group_id = ""
        sid = ""
        try:
            sid = str(getattr(event, "session_id", "") or "").strip()
        except Exception:
            sid = ""
        if sid:
            if not group_id:
                m = re.search(r"group[^\d]*(\d{4,})", sid, re.IGNORECASE)
                if m:
                    group_id = m.group(1)
            if not user_id and "group" not in sid.lower():
                m = re.search(r"(\d{4,})", sid)
                if m:
                    user_id = m.group(1)
        # 工具路径兜底：若主事件群号/用户号仍为空，退而用最近记录的真实会话事件
        # （on_using_llm_tool / _do_draw 都会写入 self._last_event）。
        if (not group_id or not user_id) and getattr(self, "_last_event", None) is not None and self._last_event is not event:
            try:
                _luid, _lgid = self._event_ids(self._last_event)
                if not user_id and _luid:
                    user_id = _luid
                if not group_id and _lgid:
                    group_id = _lgid
            except Exception:
                pass
        return user_id, group_id

    def _check_whitelist(self, event) -> tuple[bool, str]:
        """发图白名单校验。返回 (是否允许, 拒绝原因)。

        白名单启用时，仅白名单内的用户/群可绘图（命中群内的所有人允许）；
        未命中白名单一律拒绝。本方法仅在 _is_whitelist_active() 为 True 时调用，
        白名单未启用时由调用方改走 _check_blacklist（两者互斥、白名单优先）。
        """
        wl = self._whitelist_cfg()
        users = self._parse_id_list(wl.get("users", ""))
        groups = self._parse_id_list(wl.get("groups", ""))
        user_id, group_id = self._event_ids(event)
        if user_id and user_id in users:
            return True, ""
        if group_id and group_id in groups:
            return True, ""
        return False, "你暂无绘图权限～"

    def _blacklist_cfg(self) -> dict:
        """绘图黑名单配置块（blacklist）。"""
        return self._cfg("blacklist", {}) or {}

    def _parse_id_list(self, raw: str) -> set[str]:
        """把配置里的 ID 列表（逗号/换行/全角逗号分隔）解析为去重集合。"""
        if not raw:
            return set()
        return {x.strip() for x in re.split(r"[,，\n\r]+", str(raw)) if x.strip()}

    def _check_blacklist(self, event) -> tuple[bool, str]:
        """绘图黑名单校验。返回 (是否允许, 拒绝原因)。

        按用户（get_sender_id）或群（get_group_id，群聊返回群号、私聊为 None）命中
        黑名单即拒绝。管理员默认豁免（blacklist.admin_exempt=true 时）。
        """
        bl = self._blacklist_cfg()
        if not bl.get("enabled", False):
            return True, ""
        # 管理员豁免（默认开启，避免误把自己锁死）
        if bl.get("admin_exempt", True) and self._is_admin(event):
            return True, ""
        # 群号：优先 get_group_id（AstrBot 标准方法）；不存在则从 session_id 兜底
        user_id, group_id = self._event_ids(event)
        users = self._parse_id_list(bl.get("users", ""))
        groups = self._parse_id_list(bl.get("groups", ""))
        if user_id and user_id in users:
            return False, "你暂无绘图权限～"
        if group_id and group_id in groups:
            return False, "你暂无绘图权限～"
        return True, ""

    # ------------------------------------------------------------------ #
    # 生图次数限制（配额）
    # ------------------------------------------------------------------ #
    def _draw_limit_cfg(self) -> dict:
        """全局生图限额配置块（draw_limit）。"""
        return self._cfg("draw_limit", {}) or {}

    def _quota_enabled(self) -> bool:
        return bool(self._draw_limit_cfg().get("enabled", False))

    def _resolve_user_quota(self, user_id: str) -> dict:
        """解析某用户实际生效的限额：优先用户单独配置，否则用全局配置。

        -1 表示不限制。返回 {"max_total": int, "max_hour": int, "max_day": int, "from_global": bool}。
        """
        g = self._draw_limit_cfg()
        if self.quota is not None:
            uc = self.quota.get_user_config(user_id)
            if uc:
                return {
                    "max_total": uc["max_total"],
                    "max_hour": uc["max_hour"],
                    "max_day": uc.get("max_day", -1),
                    "from_global": False,
                }
        return {
            "max_total": int(g.get("max_total", -1)),
            "max_hour": int(g.get("max_hour", -1)),
            "max_day": int(g.get("max_day", -1)),
            "from_global": True,
        }

    def _check_draw_limit(self, event) -> tuple[bool, str]:
        """生图前校验配额。返回 (是否允许, 拒绝原因)。

        管理员（且 admin_exempt 开启时）与未识别到 user_id 的用户不受限；
        -1 表示该项不限。任一维度超限则拒绝。
        """
        if self.quota is None or not self._quota_enabled():
            return True, ""
        user_id = (getattr(event, "get_sender_id", lambda: "")() or "") if event is not None else ""
        # 管理员豁免
        if self._draw_limit_cfg().get("admin_exempt", False) and self._is_admin(event):
            return True, ""
        # 未识别到用户：无法计数，跳过限制（避免误伤）
        if not user_id:
            return True, ""
        quota = self._resolve_user_quota(user_id)
        usage = self.quota.get_usage(user_id)
        total = usage["total_used"]
        hour = usage["hour_used"]
        day = usage["day_used"]
        mt = quota["max_total"]
        mh = quota["max_hour"]
        md = quota["max_day"]
        if mt >= 0 and total >= mt:
            return False, "你的生图次数已用尽，暂时无法继续生图，请稍后再试。"
        if md >= 0 and day >= md:
            return False, "你今天的生图次数已用完，请在每天 0 点后刷新次数再试。"
        if mh >= 0 and hour >= mh:
            # 计算下个整点（当前小时结束后自动刷新小时次数）
            next_hour = quota_store._hour_start(time.time()) + quota_store.HOUR_SECONDS
            try:
                next_hhmm = time.strftime("%H:%M", time.localtime(next_hour))
            except Exception:
                next_hhmm = ""
            if next_hhmm:
                return False, f"你当前小时内的生图次数已用完，请到 {next_hhmm} 后再试。"
            return False, "你当前小时内的生图次数已用完，请稍后再试。"
        return True, ""

    def _oplog_dedup(self, sha: str, use_count, user_id: str, user_name: str, event) -> None:
        """图库去重命中回调：写入独立操作日志，解释「图库/出图记录仅 1 条但限额计数增加」。
        """
        try:
            if self.oplog is None:
                return
            self.oplog.add(
                "gallery_dedup",
                f"图库去重命中：use_count 已达 {use_count}（本次未新增记录）",
                user_id=user_id,
                user_name=user_name,
                session_id=(getattr(event, "session_id", "") or "") if event is not None else "",
                ref_sha=sha,
                detail="产出图片与图库已有记录内容相同（sha256 一致），图库/出图记录不新增行；但限额仍按每次成功出图 +1。",
                extra={"use_count": use_count, "sha16": (sha or "")[:16]},
            )
        except Exception:
            pass

    def _record_draw_used(self, event) -> None:
        """生图成功后记录一次配额用量（总次数 + 当前小时次数 + 当天次数）。

        无论 draw_limit.enabled 是否开启都记录，这样「限额」页总能显示每个用户的
        真实生图数量；enabled 只控制是否触发「限制」。
        """
        if self.quota is None:
            return
        user_id = (getattr(event, "get_sender_id", lambda: "")() or "") if event is not None else ""
        if not user_id:
            return
        user_name_fn = getattr(event, "get_sender_name", None) if event is not None else None
        user_name = (user_name_fn() if callable(user_name_fn) else "") or ""
        self.quota.record_used(user_id, user_name)
        # 独立操作日志：限额扣减（注意：与图库去重无关，每次成功出图都 +1）
        try:
            _q = self.quota.peek(user_id)
            logger.info(
                f"[限额] 扣减 user={user_id} 成功：total={_q.total_used} "
                f"hour={_q.hour_used} day={_q.day_used}（每次成功出图 +1，与图库是否去重无关）"
            )
            if self.oplog is not None:
                self.oplog.add(
                    "quota_inc",
                    f"限额扣减：total={_q.total_used} hour={_q.hour_used} day={_q.day_used}",
                    user_id=user_id,
                    user_name=user_name,
                    session_id=(getattr(event, "session_id", "") or "") if event is not None else "",
                    detail="每次成功出图 +1，与图库是否去重无关（对账时以此为准）",
                    extra={"total_used": _q.total_used, "hour_used": _q.hour_used, "day_used": _q.day_used},
                )
        except Exception:
            pass

    def _draw_single_max(self, count: int, source: str = "", event=None) -> tuple[int, str]:
        """单次调用的出图张数上限，返回 (本次允许张数, 截断提示)。

        只看「这一次调用要出多少张」，与轮次、与时间窗都无关：
        超过 draw_auto.max 就截断（如上限 9、用户要 10 张 → 只出 9 张），
        截断时返回一句中性的事实说明，由模型自行告诉用户。
        伴侣插件等第三方主动调用（带 source）不受此限制。
        """
        cfg = self._cfg("draw_auto", {}) or {}
        if source and source.strip() == SOURCE_COMPANION_PLUGIN:
            return count, ""
        if cfg.get("unlimited_draw"):
            return count, ""
        # 按用户开关：该用户开启「不限轮次持续发图」时放行
        _unl = self._unl_users()
        if _unl and event is not None:
            _uid = (getattr(event, "get_sender_id", lambda: "")() or "").strip()
            if _uid in _unl:
                return count, ""
        try:
            dmax = int(cfg.get("max", 3) or 3)
        except (TypeError, ValueError):
            dmax = 3
        if dmax <= 0 or count <= dmax:
            return count, ""
        logger.info(f"【出图·上限】 单次请求 {count} 张超过配置上限 {dmax} 张，截断为 {dmax} 张")
        return dmax, f"本次成功生成 {dmax} 张（受单次上限 {dmax} 张限制，已自动截断超出部分）。"

    # ── 单轮请求出图闸门（防「一次对话里模型无限次画图」）────────────────────
    # 设计（v5.0）：不再去猜模型的意图。此前的三道防线（4 秒内重复调用 / 同参去重 /
    # 时间窗出图预算）本质上都在判断「模型是不是在重复调用」，而模型的行为猜不准——
    # 它换个参数、或隔十几秒（一张图的正常耗时）再调，就全部绕过去了。
    #
    # 改为结构性保证：同一条用户消息引发的一轮 agent run 内，成功次数与失败次数
    # 分别封顶，达到上限后本轮再也出不了图——与模型传什么参数、想什么完全无关。
    #   · 成功封顶 draw_auto.per_run_max_calls（默认 1）→ 一次对话出完就关门；
    #   · 失败封顶 draw_auto.max_retry_per_run（默认 1）→ 允许失败重试 1 次，
    #     避免 ComfyUI 偶发报错就废掉用户整轮对话，同时防「失败→重试→失败」空转；
    #   · 计入失败的类别可配（空参数 / ComfyUI 后端失败）；
    #   · 轮次边界 = 触发本轮 run 的用户消息指纹，用户发新消息即自动重置；
    #     on_agent_done 时也会清除，不影响用户下一条消息继续画。
    # 伴侣插件等第三方主动调用（带 source）不参与此闸门。
    _DRAW_RUN_TTL = 900.0  # 同一轮状态最长保留 15 分钟，超时兜底重置，避免状态残留锁死

    # 拦截时返回给模型的收尾话术：只陈述事实、让模型自然收尾，不提「上限/禁止/闸门」。
    # 事实已经证明堆「绝对不要 / 禁止」这类警告没用，只会白烧 token。
    # ★但必须说清「本次调用没有产生新图」：早期版本文案写得跟成功收尾几乎一样，
    #   模型被拦了却以为自己又出了一次图，于是自述「调用了 N 次、出了 N×count 张」，
    #   实际上后面几次一张都没出。这里用中性措辞把事实讲明白即可。
    # 注意：必须返回文本而非 None——None 会让 AstrBot 直接判定 DONE 结束循环，
    # 模型一句话不说，用户会看到「图发完就哑了」。
    _DRAW_RUN_DONE_HINT = (
        "本轮对话的图片之前已经成功生成并发送到了聊天窗口，"
        "所以本次调用没有再产出新图"
        "（这不是「一轮只能画一张」的限制，而是这一条消息的图已经给过了）。"
        "图已经发到聊天里了，你不需要、也不应该再调用任何其它工具去读取图片路径或重复发送这张图。"
        "请用一句话简短、自然地收尾即可，"
        "不要对用户说「限制一张 / 同一轮只能出一张」之类的话，也不要用图库旧图来凑数。"
    )
    _DRAW_RUN_FAIL_HINT = (
        "画图连续遇到问题，本次没能出图。"
        "请用一句话简短向用户说明情况即可，不要用图库里的旧图顶替。"
    )
    # 重复调用超过容忍次数后的终止信号。实测模型被拦后会换个 seed 继续调，
    # 若一直回 DONE_HINT 它会一直调（日志里 17 次、空转 51 秒才停）。
    # v5.7.4 起该提示不再指望模型「读了就停」——handler 收到它会直接熔断：
    # 插件先主动发一条收尾消息，再 return None 让框架终止 Agent Loop。
    _DRAW_RUN_STOP_HINT = (
        "已经没有新的图片要生成了。请直接结束回复：不要再调用任何画图工具，"
        "也不要用 comfyui_gallery 去翻以前的旧图来顶替——"
        "用户要的是新画的图，画不出来就如实告诉用户、请他稍后再试。"
    )

    def _draw_run_msg_fp(self, event) -> str:
        """生成「触发本轮 agent run 的用户消息」指纹，用于判定是否为同一次请求。"""
        try:
            mo = getattr(event, "message_obj", None)
            mid = str(getattr(mo, "message_id", "") or "") if mo is not None else ""
            ts = getattr(mo, "timestamp", None) if mo is not None else None
            text = (getattr(event, "message_str", "") or "").strip()
            if not (mid or ts):
                ts = int(time.time())
            return f"{mid}|{ts}|{text[:120]}"
        except Exception:
            return "unknown"

    def _draw_run_state_of(self, event) -> dict:
        """取（必要时新建）本轮 agent run 的出图计数状态。

        轮次边界 = 触发本轮 run 的用户消息指纹：用户发一条新消息即自动重置计数，
        状态超过 _DRAW_RUN_TTL 也兜底重置，避免残留状态把会话锁死。
        """
        sid = (getattr(event, "session_id", "") or "global") if event is not None else "global"
        msg_fp = self._draw_run_msg_fp(event)
        now = time.time()
        state = getattr(self, "_draw_run_state", None)
        if not isinstance(state, dict):
            state = {}
            self._draw_run_state = state
        st = state.get(sid)
        if (
            not isinstance(st, dict)
            or st.get("msg_fp") != msg_fp
            or (now - float(st.get("ts", 0.0) or 0.0)) > self._DRAW_RUN_TTL
        ):
            st = {"msg_fp": msg_fp, "ok": 0, "fail": 0, "blocked": 0, "ts": now}
            state[sid] = st
        st["ts"] = now
        return st

    def _draw_run_check(self, event, source: str = "", tool_name: str = "") -> tuple[bool, str]:
        """单轮出图闸门，返回 (是否放行, 拦截提示)。

        只看本轮「已经成功出图几次 / 已经失败几次」，不看参数、不看时间间隔。
        达到任一上限即本轮关门，模型无论再传什么都出不了图。
        """
        if source and source.strip() == SOURCE_COMPANION_PLUGIN:
            return True, ""
        cfg = self._cfg("draw_auto", {}) or {}
        if cfg.get("unlimited_draw"):
            return True, ""
        # 按用户开关：该用户开启「不限轮次持续发图」时放行
        _unl = self._unl_users()
        if _unl and event is not None:
            _uid = (getattr(event, "get_sender_id", lambda: "")() or "").strip()
            if _uid in _unl:
                return True, ""
        try:
            max_calls = int(cfg.get("per_run_max_calls", 1))
        except (TypeError, ValueError):
            max_calls = 1
        try:
            max_retry = int(cfg.get("max_retry_per_run", 1))
        except (TypeError, ValueError):
            max_retry = 1
        st = self._draw_run_state_of(event)
        ok = int(st.get("ok", 0) or 0)
        fail = int(st.get("fail", 0) or 0)
        sid = (getattr(event, "session_id", "") or "global") if event is not None else "global"
        if max_calls > 0 and ok >= max_calls:
            # 本轮已出过图 → 拦回。同时记一次「重复调用」：
            # 不记数的话模型会换个 seed 接着调、再被拦、再调……（实测 17 次、
            # 空转 51 秒才停）一张新图都不出，却白白烧 token 并让用户干等。
            # 超过容忍次数后改用明确的终止信号，让它别再调了。
            blocked = int(st.get("blocked", 0) or 0) + 1
            st["blocked"] = blocked
            if max_retry >= 0 and blocked > max_retry:
                logger.info(
                    f"【工具·{tool_name or 'draw'}】 会话 {sid} 本轮出图后仍重复调用 {blocked} 次，"
                    f"超过容忍次数 {max_retry}，硬终止（防空转烧 token）"
                )
                return False, self._DRAW_RUN_STOP_HINT
            logger.info(
                f"【工具·{tool_name or 'draw'}】 会话 {sid} 本轮已成功出图 {ok} 次，"
                f"达到单轮上限 {max_calls}，拦截本次调用（一次对话出完即关门）"
            )
            return False, self._DRAW_RUN_DONE_HINT
        # max_retry = 允许的失败重试次数：失败 1 次仍可再试一次，超过才关门。
        # 取 -1（或任何负数）表示失败不封顶，完全交给单轮成功闸门兜底。
        if max_retry >= 0 and fail > max_retry:
            logger.info(
                f"【工具·{tool_name or 'draw'}】 会话 {sid} 本轮已失败 {fail} 次，"
                f"超过允许的重试次数 {max_retry}，拦截（防「失败→重试→失败」空转）"
            )
            return False, self._DRAW_RUN_FAIL_HINT
        return True, ""

    def _draw_run_hit(self, event) -> None:
        """本轮成功出图一次，成功计数 +1（关门的主依据）。"""
        try:
            st = self._draw_run_state_of(event)
            st["ok"] = int(st.get("ok", 0) or 0) + 1
            st["ts"] = time.time()
        except Exception:
            pass

    def _draw_run_fail(self, event, kind: str = "backend") -> bool:
        """本轮出图失败一次，按配置决定是否计入失败计数，返回是否计入。

        kind="empty"   ：空参数调用（模型没把画面描述填进 prompt）；
        kind="backend" ：ComfyUI 后端出图失败（队列满 / 工作流报错 / 一张都没出）。
        关闭对应开关时该类别不计数（允许无限重试），此时仍由单轮成功闸门兜底。
        """
        try:
            cfg = self._cfg("draw_auto", {}) or {}
            key = "fail_count_empty_prompt" if kind == "empty" else "fail_count_backend"
            if not bool(cfg.get(key, True)):
                return False
            st = self._draw_run_state_of(event)
            st["fail"] = int(st.get("fail", 0) or 0) + 1
            st["ts"] = time.time()
            return True
        except Exception:
            return False

    def _draw_run_reset(self, sid: str) -> None:
        """清除某会话的单轮出图闸门状态（一轮 agent run 结束时调用）。"""
        try:
            state = getattr(self, "_draw_run_state", None)
            if isinstance(state, dict) and sid:
                state.pop(sid, None)
        except Exception:
            pass

    @staticmethod
    def _is_private_event(event) -> bool:
        """判断当前事件是否为私聊。

        优先用 AstrBot 标准的 get_group_id()：群聊返回群号，私聊返回 None/空。
        兜底看 session_id 是否含 group 标记（aiocqhttp 私聊 session_id 形如 private:xxx）。
        """
        if event is None:
            return True
        try:
            get_g = getattr(event, "get_group_id", None)
            if callable(get_g):
                gid = get_g()
                if gid:
                    return False
                return True
        except Exception:
            pass
        sid = getattr(event, "session_id", "") or ""
        return "group" not in sid.lower()

    def _can_operate_image(self, event, row: dict, owner: str = "") -> tuple[bool, str]:
        """图库「修改类操作」（打标签/删除/清空/改可见性等）的归属校验。

        权限模型：
          - 图片所有者（user_id == owner）可操作自己的图；
          - 管理员可操作任意图（含无主图）；
          - 无主图（user_id 为空）仅管理员可操作；
          - 他人图片（含公开图）不可操作——公开只代表「他人可查看/发送」，不代表可修改。

        返回 (是否允许, 拒绝原因)。row 为 None 时返回 (True, "")，由调用方处理「不存在」。
        """
        if row is None:
            return True, ""
        uid = (row.get("user_id") or "").strip()
        # 图主
        if uid and owner and uid == owner:
            return True, ""
        # 管理员
        if self._is_admin(event):
            return True, ""
        # 无主图
        if not uid:
            return False, "这张图没有归属（无主图），仅管理员可操作。"
        return False, "这张图是别人的，只有图片所有者和管理员可以操作。"

    def _resolve_op_target(self, event, arg, owner: str, all_view: bool = False):
        """解析图库「修改类操作」的目标并做归属校验。

        arg 可为数字编号或 sha 前缀（不含 None；指代「当前这张图」由 tag 单独处理）。
        返回 (sha, error_msg)。error_msg 非空表示拒绝或失败。
        """
        if str(arg).isdigit():
            # 数字编号：get_by_global_no(owner) 天然只定位到本人（/全库，管理员视图）的图
            eff_owner = "" if all_view else owner
            row = self.gallery.get_by_global_no(int(arg), owner=eff_owner)
            if row is None:
                return None, "编号越界了。"
        else:
            # sha 前缀：可能命中他人图，需归属校验
            row = self.gallery.get_by_sha(str(arg))
            if row is None:
                return None, "没找到这张图。"
        can, why = self._can_operate_image(event, row, owner)
        if not can:
            return None, why
        return row["sha256"], None

    def _parse_gallery_targets(self, rest: list[str]) -> list[str]:
        """把 /图库 子命令后面的参数解析成多个「目标」token。

        支持逗号或空格分隔，例如「1,2,3」「1 2 3」「1,2 3」都会被拆成 ['1','2','3']。
        每个 token 可以是数字序号或 sha 前缀（原样保留给后续 _resolve_op_target 判断）。
        仅保留非空 token，连续分隔符不会产生空串。
        """
        out: list[str] = []
        for tok in rest:
            for piece in re.split(r"[,，\s]+", tok):
                piece = piece.strip()
                if piece:
                    out.append(piece)
        return out

    async def _gallery_send_image(self, event: AstrMessageEvent, sha: str, owner: str = "") -> bool:
        """根据 sha256/前缀拼路径，并发图。返回是否成功。
        owner: 当前用户ID；传入后校验图片归属，防止取到/发到他人图片。"""
        # 归属校验：按 sha 精确查一条记录。
        # 允许发送：公开图（is_public=1）任何人可发；私有图仅本人；
        # 历史无主图（user_id 为空）仅管理员可发（管理员 owner 判空走全库）。
        row = self.gallery.get_by_sha(sha) if hasattr(self.gallery, "get_by_sha") else None
        if row is not None:
            if owner:
                if not row.get("is_public") and (row.get("user_id") or "") != owner:
                    await self._send(event, "这张图是私有的，不属于你，无法发送。")
                    return False
            else:
                # owner 为空（管理员/全库场景）：无主图或公开图可发，他人私有图不可发
                if not row.get("is_public") and row.get("user_id"):
                    await self._send(event, "这张图是私有的，不属于当前用户，无法发送。")
                    return False
            # NSFW 护栏：打上了 NSFW 标签的图片禁止发到群聊（私聊可发）。
            # 跨群取图（如「把初音未来那张发我」）同样受此约束，避免涩图外泄到群。
            if row.get("nsfw") and not self._is_private_event(event):
                _score = row.get("nsfw_score")
                _sc = f"（置信度 {_score:.2f}）" if isinstance(_score, (int, float)) else ""
                await self._send(
                    event,
                    f"这张图被标记为 NSFW{_sc}，不能发到群里哦～ 已为你拦截。",
                )
                return False
        path = self.gallery.path_of(sha)
        if not path:
            await self._send(event, "没找到这张图，可能已被清理或从未入库。")
            return False
        try:
            # 必须包成 MessageChain 再 send：AstrBot 新版 event.send 期望消息链，
            # 直接传裸 Image 组件在 comfyui_gallery 工具场景会报
            # "'Image' object has no attribute 'chain'"。
            await event.send(MessageChain([Image(file=path)]))
            self.gallery.send(sha)
            return True
        except Exception as _e:
            logger.warning(f"【图库】 发图失败: {_e}")
            await self._send(event, "这张图文件丢失了，可能已被 LRU 清理。")
            return False

    @filter.command(
        "nsfw检测",
        alias={
            "nsfw", "nsfw检测图片", "NSFW",
            "涩涩检测", "涩图检测",
            "色图检测", "色色检测",
            "瑟瑟检测", "瑟图检测",
        },
    )
    async def cmd_nsfw(self, event: AstrMessageEvent):
        """检测图片是否 NSFW。引用/附带图片后输入 /nsfw检测，返回每张图的置信度与判断。

        置信度说明：score 是模型对「色情内容」的置信度 P(nsfw)（0~1），并非绝对真值；
        判定是否 NSFW 取决于阈值（默认 0.5），score >= 阈值 即标记为 NSFW。泳装/紧身/露肤
        等场景常因与训练样本特征相似而被模型给出较高置信度，可能被误判为 NSFW。
        可用 /nsfw阈值 查看或修改判定阈值。
        """
        # 支持查看阈值：/nsfw阈值 或 /nsfw检测 阈值
        args = (self._strip_command(event.message_str, "nsfw检测") or "").strip().lower()
        if args.startswith("阈值"):
            try:
                t = float((self._cfg("gallery", {}).get("nsfw") or {}).get("threshold", 0.5))
            except (TypeError, ValueError):
                t = 0.5
            await self._send(
                event,
                f"🎯 当前 NSFW 判定阈值：{t:.2f}\n"
                "说明：图片 P(nsfw) 置信度 ≥ 阈值即判为 NSFW。泳装/紧身/露肤等易被误判，"
                "可适当调高阈值（如 0.7）来降低误报（在插件配置 gallery.nsfw.threshold 中修改）。",
            )
            event.stop_event()
            return

        images = await self._extract_images(event)
        if not images:
            await self._send(
                event,
                "📊 涩图检测\n用法：引用或附带一张图片后发送 /涩图检测\n"
                "也可发送 /涩图检测 阈值 查看当前判定阈值。",
            )
            event.stop_event()
            return

        # 立即返回"检测中"提示
        try:
            await self._send(event, "⏳ 检测中，请稍候…")
        except Exception:
            pass

        # 阈值与图库检测共用同一实时来源（改动后无需重启即生效）
        threshold = 0.5
        try:
            if getattr(self, "gallery", None) is not None:
                threshold = self.gallery._nsfw_threshold()
        except Exception:
            pass
        try:
            from .nsfw_detector import get_detector
        except ImportError:
            from nsfw_detector import get_detector
        det = get_detector(threshold)

        # 收集检测结果（过滤无效/不可用）。
        # 关键：det.detect 是同步 onnxruntime 推理（CPU 密集），直接跑在事件循环会
        # 阻塞其他事件（含画图）。用 asyncio.to_thread 丢到线程池，避免阻塞画图。
        import asyncio as _asyncio

        async def _detect_one(p):
            if not p or not os.path.exists(p):
                return None, False
            try:
                is_nsfw, score, available = await _asyncio.to_thread(det.detect, p)
            except Exception as e:  # pragma: no cover
                logger.warning(f"【NSFW】 指令检测失败: {e}")
                is_nsfw, score, available = False, 0.0, False
            return ({"nsfw": is_nsfw, "score": score}, not available)

        results = []
        unavailable = False
        for r, unavail in await _asyncio.gather(*[_detect_one(p) for p in images]):
            if unavail:
                unavailable = True
            if r is not None:
                results.append(r)

        if unavailable:
            await self._send(
                event,
                "❓ 检测不可用（缺少依赖 onnxruntime / opennsfw-onnx，请在 AstrBot 安装这两个 pip 库后重试）",
            )
            event.stop_event()
            return

        valid = [r for r in results if r is not None]
        if not valid:
            await self._send(event, "❓ 没有可检测的有效图片。")
            event.stop_event()
            return

        # 通俗话术：把 P(nsfw) 描述成「涩涩内容的可能性」，每张图都带百分比
        def _desc(r: dict) -> str:
            pct = r["score"] * 100
            if r["nsfw"]:
                return f"🔞 涩涩内容（可能性约 {pct:.0f}%）"
            # 安全：可能性越低越"安全"
            if pct >= 20:
                return f"⚠️ 有点擦边（涩涩内容可能性约 {pct:.0f}%）"
            return f"✅ 安全（涩涩内容可能性很低，约 {pct:.0f}%）"

        if len(valid) == 1:
            # 单张图：直接返回简单结论，不用列表，不展示阈值
            await self._send(event, _desc(valid[0]))
        else:
            # 多张图：列表形式
            lines = [f"{i}. {_desc(r)}" for i, r in enumerate(valid, 1)]
            await self._send(event, "📊 涩图检测结果：\n" + "\n".join(lines))
        event.stop_event()

    async def _send_gallery_help(self, event: AstrMessageEvent) -> None:
        """发送图库使用指南（优先静态图，失败回退纯文本）。"""
        # 优先发一张固定的「图库使用指南」静态图（随插件打包，零渲染开销）。
        # 找不到图或发图失败时回退到纯文本帮助，保证可用性。
        try:
            _help_img = Path(__file__).resolve().parent / "assets" / "gallery_help.png"
            if _help_img.is_file():
                await event.send(MessageChain([Image.fromFileSystem(str(_help_img))]))
                return
        except Exception as _e:
            try:
                self.logger.warning(f"【图库】 静态帮助图发送失败，回退文字: {_e}")
            except Exception:
                pass
        await self._send(
            event,
            "📚 图库指令说明（用 /图库 或 /gallery 均可）：\n"
            "· 群聊展示本群做的图 + 所有公开图（公开图跨群共享）；私聊可查看全部会话的图\n"
            "· 公开图：任何群聊的列表/搜索都能找到；私有图：仅本人可见（用 /图库 公开/私有 切换）\n"
            "· 列表 [页码]　查看图库（每页 5 条，显示总数/总页数）\n"
            "· 搜索 <关键词> [页码]　按画面描述检索（每页 5 条，如 /图库 搜索 猫娘 2）\n"
            "· 打标签 [图] <标签...>　给图加标签（可用 /图库 打标签 或 /图库 标签）\n"
            "· 找标签 <标签>　按标签取图\n"
            "· 取图 <序号>　发某张图（序号指列表里的编号；可多张，逗号或空格隔开）\n"
            "· 取图（不带参数）　发你最近生成的那张图\n"
            "· 收藏 <序号> / 取消收藏 <序号>　收藏或取消收藏（可多张；收藏图永不清理）\n"
            "· 收藏列表 [页码]　查看自己收藏的图（★）\n"
            "· 公开 <序号> / 私有 <序号>　设置可见性（公开后任何群聊可见，他人可检索/发送）\n"
            "· 全局 <序号> / 取消全局 <序号>　设为全局后任何群聊的列表/搜索都能看到（跨群共享），但他人不可检索/发送，仅作者本人可发图\n"
            "· 保存 [标签...]　收藏当前这张图（同时加入收藏列表与打标签）\n"
            # "· 删除 <sha>　移入回收站；恢复 <sha> 从回收站找回；清空 <sha> 彻底删除\n"
            # "· 回收站　查看回收站\n"
            # 以下为管理员专属功能，不在普通用户帮助里展示（见 README）：
            # "· 统计　查看图库统计信息\n"
            # "· 重扫　全量重新检测涩图（调整阈值后刷新所有图片的 NSFW 标记）\n"
            # "· 全部 列表/搜索/收藏列表（管理员）　跨会话查看所有用户的图\n"
            # "· 重扫状态　查看全量重扫进度\n"
            "多张用法：取图/收藏/取消收藏 都支持一次性多张，序号用逗号或空格隔开。\n"
            "小技巧：群聊搜不到的图，可先在私聊搜到记下序号，回群聊直接 /图库 取图 <序号> 也能发出来。\n"
            "示例：/图库 列表 2　/图库 取图 1,2,3　/图库 收藏 1 2 5　/图库 取图 1,2,4 7",
        )

    @filter.command("图库帮助", alias={"galleryhelp"})
    async def cmd_gallery_help(self, event: AstrMessageEvent):
        """图库帮助快捷入口（/图库帮助 或 /galleryhelp）。"""
        await self._send_gallery_help(event)
        event.stop_event()

    # ---- 生图平台：会话级切换（白名单）----
    def _platform_names_text(self, ps) -> str:
        plats = ps.all_platforms()
        if not plats:
            return "（无第三方平台，全部走 ComfyUI）"
        return "\n".join(
            f"- {p.get('name')}（{p.get('type')}）[{'启用' if p.get('enabled', True) else '停用'}] id={p.get('id')}"
            for p in plats
        )

    def _platform_status_text(self, ps, sid: str, user_id: str, is_admin: bool) -> str:
        def _can(p):
            if is_admin:
                return True
            allowed = [str(u).strip() for u in (p.get("allowed_users") or []) if str(u).strip()]
            return bool(allowed) and (user_id in allowed)
        active = ps.active_platform()
        so = ps.get_session_platform(sid)
        cur_pid = (so.get("pid") if so else active) or "comfyui"
        p_cur = ps.get_platform(cur_pid) if cur_pid != "comfyui" else None
        cur_name = p_cur.get("name") if p_cur else "ComfyUI（默认）"
        scope = "本会话覆盖" if so else "全局默认"
        lines = [f"当前生图平台：{cur_name}（{scope}）", "可用平台（★=你可用）："]
        for p in ps.all_platforms():
            _en = "启用" if p.get("enabled", True) else "停用"
            lines.append(f"  {'★' if _can(p) else '⛔'} {p.get('name')}（{p.get('type')}）[{_en}]")
        lines.append("命令：/平台 <名称> 切换本会话 · /平台 重置 恢复默认 · /平台 全局 <名称>（管理员）")
        return "\n".join(lines)

    @filter.command("平台", alias={"生图平台", "platform", "platforms", "切换平台"})
    async def cmd_platform(self, event: AstrMessageEvent):
        """生图平台切换（会话级，仅白名单可用）。
        /平台                查看当前平台与可用平台
        /平台 <名称或id>     切换本会话生图平台（如 /平台 NAI）
        /平台 切换 <名称>    同上
        /平台 重置           恢复本会话为全局默认平台
        /平台 全局 <名称>    仅管理员：设置全局默认平台
        白名单：平台 allowed_users 为空=仅管理员可切；非空=管理员+名单内用户。
        """
        ps = self._platform_store()
        sid = (getattr(event, "session_id", "") or "") if event is not None else ""
        user_id = (getattr(event, "get_sender_id", lambda: "")() or "") if event is not None else ""
        is_admin = self._is_admin(event)
        # 去掉前导 / 与任一命令词，取剩余参数
        msg = (event.message_str or "").strip()
        for _w in ("生图平台", "切换平台", "平台", "platforms", "platform"):
            _low = msg.lower()
            if _low.startswith("/" + _w) or _low.startswith(_w):
                msg = msg[len(_w):].lstrip()
                break
        raw = msg.strip()
        parts = raw.split()
        sub = (parts[0] or "").lower() if parts else ""
        rest = parts[1:]

        def _can_use(p):
            if is_admin:
                return True
            allowed = [str(u).strip() for u in (p.get("allowed_users") or []) if str(u).strip()]
            return bool(allowed) and (user_id in allowed)

        _switch_subs = {"切换", "switch", "切", "用"}
        _reset_subs = {"重置", "reset", "默认", "default", "取消", "clear"}
        _global_subs = {"全局", "global", "setglobal"}

        # 全局设置（仅管理员）
        if sub in _global_subs:
            if not is_admin:
                await self._send(event, "只有管理员可以设置全局默认平台。")
                event.stop_event(); return
            name = " ".join(rest).strip()
            if not name:
                await self._send(event, "用法：/平台 全局 <平台名称或id>")
                event.stop_event(); return
            p = ps.get_platform(name)
            if p is None:
                await self._send(event, f"未找到平台「{name}」。可用：\n{self._platform_names_text(ps)}")
                event.stop_event(); return
            if not p.get("enabled", True):
                await self._send(event, f"平台「{p.get('name')}」已停用，无法设为全局默认。")
                event.stop_event(); return
            ps.set_active_platform(p.get("id"))
            await self._send(event, f"✅ 已将全局默认生图平台设为「{p.get('name')}」({p.get('type')})。")
            event.stop_event(); return

        # 重置本会话
        if sub in _reset_subs:
            ps.clear_session_platform(sid)
            await self._send(event, "✅ 本会话已恢复为全局默认生图平台（之前的切换已清除）。")
            event.stop_event(); return

        # 切换本会话
        if sub in _switch_subs:
            name = " ".join(rest).strip()
            if not name:
                await self._send(event, "用法：/平台 切换 <平台名称或id>")
                event.stop_event(); return
        else:
            name = raw  # 无子命令：整段当作平台名（/平台 NAI）

        if not name:
            await self._send(event, self._platform_status_text(ps, sid, user_id, is_admin))
            event.stop_event(); return

        p = ps.get_platform(name)
        if p is None:
            await self._send(event,
                f"未找到平台「{name}」。\n可用平台：\n{self._platform_names_text(ps)}")
            event.stop_event(); return
        if not p.get("enabled", True):
            await self._send(event, f"平台「{p.get('name')}」已停用，无法切换。")
            event.stop_event(); return
        if not _can_use(p):
            await self._send(event,
                f"你不在平台「{p.get('name')}」的白名单中，无法切换（仅管理员/白名单用户可用）。")
            event.stop_event(); return
        ps.set_session_platform(sid, p.get("id"), user_id)
        await self._send(event,
            f"✅ 本会话已切换生图平台为「{p.get('name')}」({p.get('type')})，"
            f"后续绘图默认使用它。\n用 /平台 查看状态，/平台 重置 恢复默认。")
        event.stop_event(); return

    @filter.command("gallery", alias={"图库"})
    async def cmd_gallery(self, event: AstrMessageEvent):
        """图片画廊与语义标签召回。支持 /gallery 与 /图库 两种入口，子命令见提示。"""
        if self.gallery is None:
            await self._send(event, "图库未启用或初始化失败，请检查配置。")
            event.stop_event()
            return
        args = self._strip_command(event.message_str, "gallery") or ""
        # 兼容中文入口 /图库：_strip_command 只认 /gallery，这里额外剥一次 /图库
        args = self._strip_command(args, "图库")
        parts = args.split()
        raw_sub = (parts[0] or "").lower() if parts else "list"
        # 中文子命令归一化：把中文别名映射到标准子命令，方便中文用户直接说中文
        _sub_zh = {
            "列表": "list", "查看": "list",
            "搜索": "search", "查找": "search", "搜": "search",
            "标签": "tag", "打标": "tag", "打标签": "tag",
            "找标签": "findbytag", "按标签": "findbytag", "查标签": "findbytag",
            "取图": "send", "发图": "send", "给我": "send", "要图": "send",
            "收藏": "star", "存": "star",
            "取消收藏": "unstar",
            "收藏列表": "starred", "我的收藏": "starred",
            # 删除相关功能暂时关闭（v2.2.87 起不开放删除/回收站/清空/恢复）
            # "删除": "del", "扔回收站": "del",
            # "回收站": "trash",
            # "恢复": "restore",
            # "清空": "purge", "彻底删": "purge",
            "保存": "save", "入库": "save",
            "统计": "stats", "状态": "stats",
            "公开": "public",
            "私有": "private",
            "全局": "global", "开放": "global",
            "取消全局": "unset_global", "撤全局": "unset_global",
            "帮助": "help", "怎么用": "help", "说明": "help",
            "全部": "all", "全库": "all", "所有": "all",
            "重扫": "rescan", "重新检测": "rescan", "重新扫描": "rescan",
        }
        sub = _sub_zh.get(raw_sub, raw_sub)
        rest = parts[1:]
        # 全库模式：/图库 全部 列表 [页码]（仅管理员可用，展示所有用户图片）
        all_view = False
        if sub == "all":
            if not bool(getattr(event, "is_admin", lambda: False)()):
                await self._send(event, "只有管理员可以查看全库图片。")
                event.stop_event()
                return
            all_view = True
            if rest:
                sub = _sub_zh.get(rest[0].lower(), rest[0].lower())
                rest = rest[1:]
            else:
                sub = "list"

        # 会话范围：群聊仅本群可见（私聊/群聊互不串看）；私聊可跨会话查看所有。
        # 仅当使用「全部」子命令（管理员）时强制放宽到所有会话。
        # 注：原 cross_session 配置开关已弃用。
        if all_view:
            session_scope = None
        else:
            session_scope = None if self._is_private_event(event) else (event.session_id or "")
        # 用户隔离标识：始终按当前用户过滤，避免群聊里不同用户互相看到对方的图。
        # owner 为空（如事件拿不到发送者）时不隔离，仅作兜底。
        owner = getattr(event, "get_sender_id", lambda: "")() or ""

        if sub in ("help", "帮助"):
            await self._send_gallery_help(event)
            return
        elif sub == "rescan":
            # 全量重新检测：调整 NSFW 阈值后，用新阈值重扫所有图片，刷新 NSFW 标记
            if not bool(getattr(event, "is_admin", lambda: False)()):
                await self._send(event, "只有管理员可以执行全量重新检测。")
                event.stop_event()
                return
            try:
                st = self.gallery.scan_nsfw_start(only_unchecked=False)
            except Exception as e:
                logger.warning(f"【图库】 重扫启动失败: {e}")
                st = {"running": False, "last_err": str(e)}
            if st.get("running"):
                await self._send(
                    event,
                    "🔁 已开始全量重新检测（后台执行）…\n"
                    "用 /图库 重扫状态 查看进度。",
                )
            else:
                err = st.get("last_err") or "无法启动扫描"
                await self._send(event, f"❌ 未能启动重扫：{err}")
            event.stop_event()
        elif sub in ("rescan_status", "重扫状态"):
            st = self.gallery.scan_nsfw_progress()
            if st.get("running"):
                done = st.get("done", 0)
                total = st.get("total", 0)
                nsfw = st.get("nsfw", 0)
                pct = (done / total * 100) if total else 0
                await self._send(
                    event,
                    f"🔁 正在重扫… {done}/{total}（{pct:.0f}%），已检出涩图 {nsfw} 张",
                )
            else:
                done = st.get("done", 0)
                nsfw = st.get("nsfw", 0)
                finished = st.get("finished_at")
                if st.get("last_err"):
                    await self._send(event, f"❌ 上次重扫失败：{st['last_err']}")
                else:
                    await self._send(
                        event,
                        f"✅ 最近一次重扫已完成：共 {done} 张，检出涩图 {nsfw} 张"
                        + ("（进行中标记已清除）" if finished is None else ""),
                    )
            event.stop_event()
        elif sub in ("补标", "补打标签", "retag"):
            # 存量图补打「表情包 / 漫画」标签：T6 自动打标上线前生成的图没有 tag，
            # 导致图库「表情包 / 漫画」分类筛选与按标签搜索筛不到。按各图所用工作流
            # 是否为漫画类批量补标（add_tags 幂等，重复执行无害）。逻辑抽到 _gallery_retag，
            # WebUI 的一键补标按钮复用同一方法。
            await self._send(event, self._gallery_retag(owner=owner, all_view=all_view, session_scope=session_scope))
            event.stop_event()
        elif sub == "list":
            # 列表分页：每页数量取自 gallery.page_size 配置（默认 5，夹紧到 1~50）
            try:
                page_size = max(1, min(50, int(self._cfg("gallery", {}).get("page_size", 5))))
            except (TypeError, ValueError):
                page_size = 5
            page = 1
            if rest:
                try:
                    page = max(1, int(rest[0]))
                except ValueError:
                    pass
            # 全库模式：管理员可查看所有用户的图片（owner 传空表示不过滤）
            eff_owner = "" if all_view else owner
            total = self.gallery.count_search(session=session_scope, owner=eff_owner)
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, total_pages)
            rows = self.gallery.search(
                limit=page_size, offset=(page - 1) * page_size,
                session=session_scope, owner=eff_owner,
            )
            if not rows:
                await self._send(event, "画廊还是空的～先画点图或收藏点图吧。")
            else:
                # 用户列仅在「全部/全库」模式展示（跨用户查看时区分归属）
                _head = "全库图" if all_view else "图库"
                lines = [f"{_head}（第 {page}/{total_pages} 页，共 {total} 张）："]
                for i, r in enumerate(rows, 1):
                    _gno = r.get("gidx", i)  # 图库唯一编号（跨分页稳定，可直接取图）
                    # 描述：标签优先；无标签用用户发送的消息前 10 字（无消息则回退提示词）
                    desc = self._gallery_desc(r, 10)
                    # 出图时间：created_at 时间戳转本地时间
                    _ts = r.get("created_at") or 0
                    try:
                        _tm = time.strftime("%m-%d %H:%M", time.localtime(float(_ts)))
                    except Exception:
                        _tm = "-"
                    _wf = (r.get("workflow") or "").strip() or "默认"
                    _sid = (r.get("session_id") or "").strip()
                    _uid = (r.get("user_id") or "").strip()
                    _uname = (r.get("user_name") or "").strip()
                    star = "❤️ " if r.get("starred") else ""
                    line = f"{_gno}. {star}{desc} | {_wf} | {_tm}"
                    # 用户列仅在「全部/全库」模式展示（跨用户查看时区分归属）；
                    # 普通列表都是自己的图，不显示用户列。
                    if all_view:
                        line += f" | 👤 {_uname or _uid or '匿名'}"
                    lines.append(line)
                lines.append(f"\n翻页：/图库 列表 <页码>（共 {total_pages} 页）")
                lines.append("发图用：/图库 取图 <序号>（上方「N.」左侧的数字）")
                await self._send_display(event, "\n".join(lines))

        elif sub == "starred":
            # 收藏列表：只看自己收藏的图（★），分页逻辑与列表一致
            try:
                page_size = max(1, min(50, int(self._cfg("gallery", {}).get("page_size", 5))))
            except (TypeError, ValueError):
                page_size = 5
            page = 1
            if rest:
                try:
                    page = max(1, int(rest[0]))
                except ValueError:
                    pass
            eff_owner = "" if all_view else owner
            # 收藏也遵循会话范围：群聊仅本群，私聊跨会话；「全部」子命令（管理员）强制跨会话。
            total = self.gallery.count_search(starred_only=True, owner=eff_owner, session=session_scope)
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, total_pages)
            rows = self.gallery.search(
                starred_only=True, limit=page_size, offset=(page - 1) * page_size,
                owner=eff_owner, session=session_scope,
            )
            if not rows:
                await self._send(event, "你还没收藏任何图。收藏后可用 /图库 收藏列表 查看。")
            else:
                _head = "全库收藏" if all_view else "我的收藏"
                lines = [f"{_head}（第 {page}/{total_pages} 页，共 {total} 张）："]
                for i, r in enumerate(rows, 1):
                    _gno = r.get("gidx", i)
                    desc = self._gallery_desc(r, 10)
                    _ts = r.get("created_at") or 0
                    try:
                        _tm = time.strftime("%m-%d %H:%M", time.localtime(float(_ts)))
                    except Exception:
                        _tm = "-"
                    _wf = (r.get("workflow") or "").strip() or "默认"
                    _sid = (r.get("session_id") or "").strip()
                    _uid = (r.get("user_id") or "").strip()
                    _uname = (r.get("user_name") or "").strip()
                    line = f"{_gno}. ❤️ {desc} | {_wf} | {_tm}"
                    # 用户列仅在「全部/全库」模式展示（跨用户查看时区分归属）；
                    # 普通列表都是自己的图，不显示用户列。
                    if all_view:
                        line += f" | 👤 {_uname or _uid or '匿名'}"
                    lines.append(line)
                lines.append(f"\n翻页：/图库 收藏列表 <页码>（共 {total_pages} 页）")
                lines.append("发图用：/图库 取图 <序号>（上方「N.」左侧的数字）")
                await self._send_display(event, "\n".join(lines))

        elif sub == "search":
            # 分页：每页数量与列表一致（gallery.page_size，默认 5）。最后一参为纯数字时视为页码，
            # 其余参数拼成关键词；例如「/图库 搜索 猫娘 2」搜索「猫娘」并展示第 2 页。
            if not rest:
                await self._send(event, "用法：/图库 搜索 <关键词> [页码]")
            else:
                page = 1
                kw_parts = list(rest)
                if rest[-1].isdigit():
                    page = max(1, int(rest[-1]))
                    kw_parts = rest[:-1]
                kw = " ".join(kw_parts).strip()
                if not kw:
                    await self._send(event, "用法：/图库 搜索 <关键词> [页码]")
                else:
                    try:
                        page_size = max(1, min(50, int(self._cfg("gallery", {}).get("page_size", 5))))
                    except (TypeError, ValueError):
                        page_size = 5
                    eff_owner = "" if all_view else owner
                    total = self.gallery.count_search(keyword=kw, session=session_scope, owner=eff_owner)
                    if not total:
                        await self._send(event, f"没找到含「{kw}」的图。")
                    else:
                        total_pages = max(1, (total + page_size - 1) // page_size)
                        page = min(page, total_pages)
                        rows = self.gallery.search(
                            keyword=kw, limit=page_size, offset=(page - 1) * page_size,
                            session=session_scope, owner=eff_owner,
                        )
                        if not rows:
                            await self._send(event, f"没找到含「{kw}」的图。")
                            return
                        _head = "全库检索" if all_view else "检索"
                        lines = [f"{_head}「{kw}」（第 {page}/{total_pages} 页，共 {total} 张）："]
                        for i, r in enumerate(rows, 1):
                            _gno = r.get("gidx", i)  # 图库唯一编号，可直接取图
                            desc = self._gallery_desc(r, 10)
                            tags = (" #" + " #".join(r["tags"])) if r.get("tags") else ""
                            _ts = r.get("created_at") or 0
                            try:
                                _tm = time.strftime("%m-%d %H:%M", time.localtime(float(_ts)))
                            except Exception:
                                _tm = "-"
                            _wf = (r.get("workflow") or "").strip() or "默认"
                            _sid = (r.get("session_id") or "").strip()
                            _uid = (r.get("user_id") or "").strip()
                            _uname = (r.get("user_name") or "").strip()
                            # 标签并入描述列（用空格分隔，不带「|」），保持「序号. 描述 | 工作流 | 时间」三列结构；
                            # 若标签也用「|」分隔会导致多一列，表格解析取前 3 列时列错位
                            # （类型=标签、时间=工作流，正是此前用户反馈的错乱）。
                            tag_line = f" {tags.strip()}" if tags.strip() else ""
                            line = f"{_gno}.{desc}{tag_line} | {_wf} | {_tm}"
                            if is_admin and (_sid or all_view):
                                line += f" | 👤 {_uname or _uid or '匿名'}"
                            lines.append(line)
                        if total_pages > 1:
                            lines.append(f"\n翻页：/图库 搜索 {kw} <页码>（共 {total_pages} 页）")
                        lines.append("发图用：/图库 取图 <序号>（上方「N.」左侧的数字）")
                        await self._send_display(event, "\n".join(lines))

        elif sub == "tag":
            # /gallery tag [图标识] <标签...>
            if not rest:
                await self._send(event, "用法：/图库 打标签 <序号> <标签1> <标签2> ...")
            else:
                # 第一个参数是图标识（数字序号或 sha 前缀）还是标签？
                target = None
                tag_start = 0
                first = rest[0]
                if first.isdigit():
                    target = int(first)  # 序号
                    tag_start = 1
                elif len(first) >= 6 and all(c in "0123456789abcdef" for c in first.lower()):
                    target = first  # sha 前缀
                    tag_start = 1
                tags = rest[tag_start:]
                if not tags:
                    await self._send(event, "请至少给一个标签，如：/图库 打标签 合照")
                    event.stop_event()
                    return
                # 解析 target 到 sha（含归属校验：只有图主/管理员能给别人图打标签）
                sha = None
                if target is None:
                    # 指代消解：默认指向"这张图"。用「内容寻址 sha256」定位，而非路径字符串
                    # 全等比较（temp 路径与归档路径目录不同，字符串永远不等）。
                    p = await self._gallery_resolve_ref(event)
                    if p and os.path.exists(p):
                        _sha = self.gallery.sha_of(p)
                        if _sha:
                            _row = self.gallery.get_by_sha(_sha)
                            can, why = self._can_operate_image(event, _row, owner)
                            if can:
                                sha = _row["sha256"] if _row else None
                        if not sha:
                            await self._send(event, "这张图还没入库（图库里没有它的记录）。请先收藏该图（/图库 保存），或指定 /图库 打标签 <编号> <标签> 来打标签。")
                            event.stop_event()
                            return
                else:
                    if isinstance(target, int):
                        sha, _err = self._resolve_op_target(event, target, owner, all_view)
                        if _err:
                            await self._send(event, _err)
                            event.stop_event()
                            return
                    else:
                        sha, _err = self._resolve_op_target(event, target, owner, all_view)
                        if _err:
                            await self._send(event, _err)
                            event.stop_event()
                            return
                if not sha:
                    await self._send(event, "没找到这张图（先发图、或指定 /图库 打标签 <编号> <标签>）")
                else:
                    self.gallery.add_tags(sha, tags)
                    await self._send(event, f"已打标签：{'、'.join(tags)}")

        elif sub in ("findbytag", "bytag"):
            tag = " ".join(rest).strip()
            if not tag:
                await self._send(event, "用法：/图库 找标签 <标签>")
            else:
                rows = self.gallery.recall_by_tag(tag, limit=20, owner=owner)
                if not rows:
                    await self._send(event, f"没有带「{tag}」标签的图。")
                else:
                    lines = [f"带「{tag}」的图（共 {len(rows)} 张）："]
                    for i, r in enumerate(rows, 1):
                        _gno = r.get("gidx", i)  # 图库唯一编号，可直接取图
                        star = "★" if r["starred"] else ""
                        _src_map = {"gen": "文生图", "img2img": "图生图", "ref": "参考图", "user": "用户图"}
                        _src = (r.get("source") or "").strip()
                        _src_cn = _src_map.get(_src, _src or "默认")
                        lines.append(f"{_gno}. {star}{self._gallery_desc(r, 40)} | {_src_cn} | ")
                    lines.append("发图用：/图库 取图 <序号>（上方「N.」左侧的数字）")
                    await self._send_display(event, "\n".join(lines))

        elif sub == "send":
            if not rest:
                # 不带参数：取「当前用户最近生成的一张图」（编号 1 即按 created_at DESC 的最新一张）。
                # 私聊取自己；群聊也只取自己（eff_owner 已限定 owner），避免误发他人图。
                eff_owner = "" if all_view else owner
                r = self.gallery.get_by_global_no(1, owner=eff_owner, session=session_scope)
                if r:
                    ok = await self._gallery_send_image(event, r["sha256"], owner=eff_owner)
                    if ok:
                        await self._send(event, "已发送你最近生成的那张图～")
                    else:
                        await self._send(event, "找到图但发送失败，请稍后再试。")
                else:
                    await self._send(event, "你还没有生成过图，先去画一张吧～")
            else:
                targets = self._parse_gallery_targets(rest)
                eff_owner = "" if all_view else owner
                sent_ok, sent_fail = 0, 0
                for arg in targets:
                    if arg.isdigit():
                        # 数字 = 全局编号（列表里显示的编号）
                        r = self.gallery.get_by_global_no(int(arg), owner=eff_owner, session=session_scope)
                        if r:
                            ok = await self._gallery_send_image(event, r["sha256"], owner=eff_owner)
                            sent_ok += 1 if ok else 0
                            sent_fail += 0 if ok else 1
                        else:
                            sent_fail += 1
                            await self._send(event, f"编号 {arg} 越界了，已跳过。")
                    else:
                        ok = await self._gallery_send_image(event, arg, owner=eff_owner)
                        sent_ok += 1 if ok else 0
                        sent_fail += 0 if ok else 1
                if len(targets) > 1:
                    await self._send(event, f"已发 {sent_ok} 张，失败/跳过 {sent_fail} 张。")

        elif sub == "star":
            if not rest:
                await self._send(event, "用法：/图库 收藏 <序号>（可多张，用逗号或空格隔开，如「/图库 收藏 1,2,3」）")
            else:
                targets = self._parse_gallery_targets(rest)
                ok_n, skip_n = 0, 0
                for t in targets:
                    _sha, _err = self._resolve_op_target(event, t, owner, all_view)
                    if _err:
                        skip_n += 1
                        await self._send(event, f"「{t}」：{_err}（已跳过）")
                        continue
                    if self.gallery.star(_sha, 1):
                        ok_n += 1
                        if len(targets) == 1:
                            await self._send(event, f"已收藏 ★（{t}）")
                    else:
                        skip_n += 1
                        await self._send(event, f"「{t}」：没找到这张图，已跳过。")
                if len(targets) > 1:
                    await self._send(event, f"已收藏 {ok_n} 张，跳过 {skip_n} 张 ★")

        elif sub == "unstar":
            if not rest:
                await self._send(event, "用法：/图库 取消收藏 <序号>（可多张，用逗号或空格隔开）")
            else:
                targets = self._parse_gallery_targets(rest)
                ok_n, skip_n = 0, 0
                for t in targets:
                    _sha, _err = self._resolve_op_target(event, t, owner, all_view)
                    if _err:
                        skip_n += 1
                        await self._send(event, f"「{t}」：{_err}（已跳过）")
                        continue
                    self.gallery.star(_sha, 0)
                    ok_n += 1
                    if len(targets) == 1:
                        await self._send(event, f"已取消收藏（{t}）。")
                if len(targets) > 1:
                    await self._send(event, f"已取消收藏 {ok_n} 张，跳过 {skip_n} 张。")

        # 删除相关功能暂时关闭（v2.2.87 起不开放删除/回收站/清空/恢复）
        # elif sub == "del":
        #     if not rest:
        #         await self._send(event, "用法：/图库 删除 <编号或sha前几位>  （移入回收站，可在 /图库 回收站 查看，清空 才真删）")
        #     else:
        #         _sha, _err = self._resolve_op_target(event, rest[0], owner, all_view)
        #         if _err:
        #             await self._send(event, _err)
        #         else:
        #             ok = self.gallery.delete(_sha)
        #             await self._send(event, "已移入回收站（用 /图库 清空 彻底删除）。" if ok else "删除失败（已收藏的图不可删，或不存在）。")
        #
        # elif sub == "trash":
        #     rows = self.gallery.search(trash=True, limit=100, owner=owner)
        #     if not rows:
        #         await self._send(event, "回收站是空的。")
        #     else:
        #         lines = [f"回收站（{len(rows)} 张，purge 才真删）："]
        #         for i, r in enumerate(rows, 1):
        #             lines.append(f"{i}. {r['sha256'][:16]} 「{(r.get('prompt') or '')[:24]}」")
        #         await self._send(event, "\n".join(lines))
        #
        # elif sub == "restore":
        #     if not rest:
        #         await self._send(event, "用法：/图库 恢复 <编号或sha前几位>")
        #     else:
        #         _sha, _err = self._resolve_op_target(event, rest[0], owner, all_view)
        #         if _err:
        #             await self._send(event, _err)
        #         else:
        #             ok = self.gallery.restore(_sha)
        #             await self._send(event, "已恢复。" if ok else "恢复失败（不在回收站或不存在）。")
        #
        # elif sub == "purge":
        #     if not rest:
        #         await self._send(event, "用法：/图库 清空 <编号或sha前几位>  （彻底删除，不可恢复）")
        #     else:
        #         _sha, _err = self._resolve_op_target(event, rest[0], owner, all_view)
        #         if _err:
        #             await self._send(event, _err)
        #         else:
        #             ok = self.gallery.purge(_sha)
        #             await self._send(event, "已彻底删除。" if ok else "删除失败（不在回收站或不存在）。")
        #
        elif sub == "save":
            # /gallery save [标签...]：收藏当前/上一条消息的图（方案B）
            p = await self._gallery_resolve_ref(event)
            if not p:
                await self._send(event, "没找到要收藏的图（当前/上条消息没有图，本会话也没生成过图）。")
            else:
                tags = rest
                uname = getattr(event, "get_sender_name", lambda: "")() or ""
                sha = self.gallery.archive_user_image(p, tags=tags, user_id=owner, user_name=uname, session_id=(getattr(event, "session_id", "") or ""))
                if sha:
                    extra = (" 标签：" + "、".join(tags)) if tags else ""
                    await self._send(event, f"已收藏这张图{extra}")
                else:
                    await self._send(event, "收藏失败（图库可能未启用）。")

        elif sub == "stats":
            st = self.gallery.stats()
            lines = [
                f"图库统计：",
                f"· 总张数：{st.get('total', 0)}",
                f"· 收藏：{st.get('starred', 0)}　带标签：{st.get('tagged', 0)}",
                f"· 生图/参考/用户收藏：{st.get('gen',0)}/{st.get('ref',0)}/{st.get('user',0)}",
                # 删除相关功能关闭：不向普通用户展示回收站占用
                f"· 有效占用：{st.get('size_mb', 0)} MB / 上限 {st.get('max_total_mb', 0)} MB",
            ]
            await self._send_display(event, "\n".join(lines))

        elif sub in ("public", "private"):
            # /gallery public|private <序号或sha前几位>
            is_pub = sub == "public"
            if not rest:
                await self._send(event, f"用法：/图库 {('公开' if is_pub else '私有')} <序号>")
            else:
                first = rest[0]
                # 归属校验：只有图主/管理员能改他人图片的可见性
                sha, _err = self._resolve_op_target(event, first, owner, all_view)
                if _err:
                    await self._send(event, _err)
                    event.stop_event()
                    return
                if sha:
                    if self.gallery.set_visibility(sha, is_pub):
                        await self._send(event, f"已设为{'公开' if is_pub else '私有'}。{'其他人现在也能检索到这张图了。' if is_pub else '只有你能看到这张图了。'}")
                    else:
                        await self._send(event, "设置可见性失败。")

        elif sub in ("global", "unset_global"):
            # /gallery global|unset_global <序号或sha前几位>
            # 全局：任何群聊的列表/搜索都能看到这张图（跨会话共享），
            # 但他人不可检索/发送（发送仍仅作者本人/管理员），区别于「公开」。
            is_gl = sub == "global"
            if not rest:
                await self._send(event, f"用法：/图库 {('全局' if is_gl else '取消全局')} <序号>")
            else:
                first = rest[0]
                # 归属校验：只有图主/管理员能设置他人图片的全局状态
                sha, _err = self._resolve_op_target(event, first, owner, all_view)
                if _err:
                    await self._send(event, _err)
                    event.stop_event()
                    return
                if sha:
                    if self.gallery.set_global(sha, is_gl):
                        if is_gl:
                            await self._send(event, "已设为全局。现在任何群聊的列表/搜索都能看到这张图了（他人不可检索/发送，仅作者本人可发图）。")
                        else:
                            await self._send(event, "已取消全局。恢复为仅本会话可见。")
                    else:
                        await self._send(event, "设置失败。")
        else:
            await self._send(
                event,
                "未知子命令。可用：\n"
                "· list/列表 [n] 查看最近图\n"
                "· search/搜索 <关键词> 检索\n"
                "· tag/打标签 [图] <标签...> 打标签\n"
                "· findByTag/找标签 <标签> 按标签取图\n"
                "· send/取图 <序号/sha> 发图\n"
                "· star/收藏 <sha>　unstar/取消收藏 <sha>　starred/收藏列表 [页码]\n"
                # "· del/删除 <sha>　trash/回收站　restore/恢复 <sha>　purge/清空 <sha>\n"
                "· save/保存 [标签...] 收藏当前图\n"
                "· public/公开 <序号/sha>　private/私有 <序号/sha>　global/全局 <序号/sha>　unset_global/取消全局 <序号/sha>　stats/统计",
            )
        event.stop_event()

    # ------------------------------------------------------------------ #
    # LLM 工具：comfyui_draw（AI 对话触发）
    # ------------------------------------------------------------------ #
    def _normalize_prompts(self, raw):
        """把 prompts 参数统一规整成 list[dict]，兼容两种写法：

        - 旧写法：每条是纯字符串（与全局参数共享）；
        - 新写法：每条是对象 {prompt, workflow, img2img_workflow, loras,
          width, height, denoise, seed}，各项独立、缺省回落全局参数。
        返回仅含非空 prompt 的项。
        """
        out = []
        for x in (raw or []):
            if isinstance(x, dict):
                try:
                    _w_v = int(x.get("width") or 0) or 0
                except (TypeError, ValueError):
                    _w_v = 0
                try:
                    _h_v = int(x.get("height") or 0) or 0
                except (TypeError, ValueError):
                    _h_v = 0
                try:
                    _dn = float(x.get("denoise")) if x.get("denoise") is not None else -1
                except (TypeError, ValueError):
                    _dn = -1
                try:
                    _sd = int(x.get("seed") or 0) or 0
                except (TypeError, ValueError):
                    _sd = 0
                out.append({
                    "prompt": str(x.get("prompt") or "").strip(),
                    "workflow": (x.get("workflow") or "").strip() or None,
                    "img2img_workflow": (x.get("img2img_workflow") or "").strip() or None,
                    "loras": x.get("loras") or None,
                    "width": _w_v,
                    "height": _h_v,
                    "denoise": _dn,
                    "seed": _sd,
                })
            else:
                out.append({
                    "prompt": str(x).strip(),
                    "workflow": None,
                    "img2img_workflow": None,
                    "loras": None,
                    "width": 0,
                    "height": 0,
                    "denoise": -1,
                    "seed": 0,
                })
        return [i for i in out if i["prompt"]]

    def _resolve_workflow_for(self, workflow, img2img_workflow, is_img2img, was_img2img, fallback_wf):
        """根据单条 prompt 的 workflow / img2img_workflow 与调用级图生图状态，求最终工作流名。

        抽自 llm_draw / llm_img2img 原先「循环外只算一次」的工作流决策逻辑，
        使 per-item 工作流选择复用同一套规则（含图生图回退、image_node 检测等）。
        """
        if is_img2img and img2img_workflow and img2img_workflow.strip():
            resolved = img2img_workflow.strip()
        elif is_img2img and workflow and workflow.strip():
            # 图生图但只显式指定了文生图工作流：若它具备图生图能力（image_node 已配置
            # 或命中默认图生图工作流）则直接用，否则回退默认图生图工作流。
            _req_wf_name = workflow.strip()
            _req_wf_cfg = next(
                (w for w in self._workflows() if (w.get("name") or "").strip() == _req_wf_name),
                None,
            )
            _req_has_image_cfg = bool(_req_wf_cfg and (_req_wf_cfg.get("image_node") or "").strip())
            _default_i2i = (self._pick_default_workflow_name(is_img2img=True) or "").strip()
            if _req_has_image_cfg or (_req_wf_name and _req_wf_name == _default_i2i):
                resolved = _req_wf_name
            else:
                resolved = _default_i2i or None
        else:
            resolved = (workflow or "").strip() or None
        # 回退为文生图时：不沿用原图生图工作流名（可能缺图注入会报错），改用回退工作流。
        if was_img2img and not is_img2img:
            resolved = fallback_wf or None
        return resolved

    @filter.llm_tool(name="comfyui_draw")
    @_safe_llm_tool
    async def llm_draw(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        negative_prompt: str = "",
        workflow: str = "",
        img2img_workflow: str = "",
        width: int = 0,
        height: int = 0,
        loras: list = None,
        seed: int = 0,
        count: int = 0,
        prompts: list = None,
        source: str = "",
        image: str = "",
        denoise: float = -1,
        trigger_words: str | None = None,
        platform: str = "",
        slot_values: dict | None = None,
        comic_feature: str | None = None,
        caption: str = "",
    ):
        """使用 ComfyUI 根据文本提示词生成图片并返回给用户。同时支持文生图与图生图。

        ★★★调用前必读（三条铁律）：
        1. 一条用户请求【只调用本工具一次】。本轮出过图后再调用会被直接拦回，一张新图都不会出。
        2. 【要 N 张 = prompts 数组写 N 条不同画面】，每条各出 1 张。
           绝不要用「单条 prompt + count=N」——那只会得到同一画面的 N 个近似副本，会被拦回。
        3. count 是预留参数（将来用于同一提示词跑不同种子），当前恒为 1，一般不用传。
        4. 【出 N 张总耗时 ≈ N × 单张耗时（实测约 20~25 秒/张）】：一次调用是串行出图的，
           6 张约需 2~3 分钟。请确保 AstrBot 的「工具调用超时」足够大（建议直接设 300 秒，
           可覆盖约 9 张）；否则时间到会被框架硬取消、最后几张丢失。若被中途取消也无需重试——
           本轮已出的图已发给用户，重复调用只会被拦回。若插件提示「还差几张、请发消息续画」，
           照做即可（用户发新消息会开新一轮继续画）。

        什么时候不要用本工具（改用 comfyui_gallery）：
        - 用户要的是「以前画过的图 / 收藏的图 / 之前发过的某张照片」，而不是要新画一张。
          例如「把我们的合照发我」「上次那张猫的图再发一次」「把收藏的图发我」——
          这些一律调用 comfyui_gallery（mode=recall / mode=search），本工具永远只出【新】图，
          绝不从图库复用旧图。
        - 本工具与 comfyui_gallery 职责严格分离：生图归 draw，发旧图归 gallery。

        触发时机：当用户表达任何想要绘制/生成/画一张图片的意图时，务必调用此工具。
        ★★重要分工：若用户要的是「表情包 / 漫画 / 带字梗图 / 气泡台词 / 底部旁白 / 分镜文字」
        （画面里要出现文字），**不要调本工具**，改调 comfyui_comic（它会自动为槽位生成文字）。
        即使用户只说"画个表情包/来张漫画"，也应调 comfyui_comic 而非本工具。
        详细的操作细则（数量、图生图判定、LoRA/工作流查询时机、提示词语言等）见可用技能「comfyui-draw」（若你有技能读取能力，先读它再操作）。
        ★直接调用，不要只说不动：用户让我画图/生成图时，**必须立即调用本工具**，并同时把画面描述完整填进 prompt 参数。绝不允许只回复"好/马上/快了"而不调用工具——不调用工具=没有真的画。
        
        什么时候调用：
        - 用户说了任何画图意图（画/生成/来张图/画个/出张图/拍照/再来一张/换个姿势重画等），一律调用。
        - 用户在催"你咋不画/图呢/怎么没看到图"同理，立即调用。
        - 用户提到"用某个 LoRA/某种风格/某种画风/某个角色人物"，**务必先调 comfyui_loras 查询**是否有匹配的 LoRA（用户给了名字/别名，或要求某类效果时都先查），再把可用的名称/别名填进 loras 参数；用户给了确切名字也可先用 keyword 查一下确认名称与底模。不要因为用户没报出具体 LoRA 名就直接跳过 LoRA、留空 loras——只要用户的要求对应某风格/画风/角色，就应主动查并选一个匹配的 LoRA。请勿只让用户"给你确切名字"而不动手——能查到就自己查。
        
        什么时候不要调用：
        - 用户明确说不要/取消/别发/不需要图。
        - 与画图无关的普通闲聊。
        
        caption 配文（图文消息，可选但推荐）：
        - caption 是你想和图片**发在同一条消息里**的那句话（如「给你画好啦～」「这只猫有点嚣张」）。
          填了它，插件会把「这段文字 + 图片」合成【一条】消息发出，比「先一句话、再一张图」自然得多。
        - 只写一句简短的配文（建议 20 字以内），用你自己的口吻；不要写长篇大论，
          也不要把画面描述 / prompt 复述进来（画面内容由 prompt 负责）。
        - ★★最重要：配文会随图一起发出，工具返回后【绝对不要】在回复里再说一遍同样的话，
          否则用户会看到两遍。配文已经表达过的部分，后续回复直接略过或换个角度接续即可。
        - 一次出多张（prompts 多条）时，配文只会加在【第一张】图上，其余图片不带文字。
        - 不想配文就留空（默认），图片照常单独发出。

        数量（重要）：【张数 = prompts 的条数】
        - 只画 1 张（如「画张图」「画个女孩」）→ 用 prompt 单条即可，不用传 count。
        - 要 N 张（如「来 3 张猫」）→ prompts 传 N 条，每条写一个【不同】的画面（不同姿势/场景/光线）。
        - 多个画面各来 X 张（如「白天、晚上、深夜各来 2 张」）→ prompts 传【组数 × X】条，
          本例即 6 条：白天 2 条 + 晚上 2 条 + 深夜 2 条，每条各出 1 张。
        - 用户说「来几张 / 多画几张」但没给具体数字 → 按 3 张处理，prompts 传 3 条不同画面。
        ★绝不要用「单条 prompt + count=N」来表达要 N 张——那样只会出同一个画面的 N 个近似副本，
          而用户要的是 N 张【不同的】图。插件会拦回这种用法并要求改用 prompts 数组。
        ★一条用户请求只调用本工具【一次】：要几张、几个画面，都在这一次调用里用 prompts 说完。
          不要把一次请求拆成多次调用——本轮出过图后再调用会被直接拦回、不会产生任何新图，纯属白费一轮。
        一次调用的总张数（= prompts 条数）受插件配置的单次上限约束（默认 3 条），超出会从末尾截断。
        
        图生图判定（重要）：只有当用户**当前消息里附带了参考图**（或明确说"把这张图/参考这张图/这张照片变成XX"）时，才按图生图处理（传 image 或依赖插件自动提取）。**普通文字请求一律文生图**，不要因为群里/历史里有图就当作图生图。
        
        prompt 语言：动漫/二次元风格（Anima 工作流）用英文 Danbooru 风格标签（如 1girl, solo, white dress, masterpiece）；真人/写实用中文即可。
        ★角色/作品的处理顺序（重要）：当用户提到某个**具体角色/人物/作品/画风角色**（如「初音未来」「某个二次元角色」）时，按以下顺序处理：
        1. **先查角色 LoRA**：调用 comfyui_loras（category 传「角色」或按角色名 keyword 搜）确认是否存在匹配该角色的 LoRA。若有，把该 LoRA 填进 `loras` 参数，并**不要**再用 danbooru 标签重复描述该角色的外形。
        2. **没有匹配的角色 LoRA 时**，才调用 danbooru MCP（「Danbooru tag search / Danbooru 标签搜索」）查询该角色/作品的标准标签（角色 tag + 作品 tag），把准确的英文标签填进 prompt。
        3. **用具体角色/作品 tag 时不要画蛇添足**：角色 tag（如 hatsune_miku）+ 作品 tag（如 vocaloid）已锁定角色全部设定（发色、瞳色、服装等），**禁止**再叠加 blue_hair、long_hair、white_dress 之类的外观描写标签，否则会与角色原设定冲突导致画错。只有当用户**额外明确要求改变**某外观（如「把头发染成粉色」）时才添加对应标签。
        4. 若既无角色 LoRA 也无 danbooru MCP，才退而用你自己的知识写标准 Danbooru 标签；没有具体角色、只是泛化人物时，正常按需写外观标签即可。
        ★动漫标签翻译（重要）：当用户用中文描述动漫/二次元画面，需要把中文翻译/改写为英文 Danbooru 标签时，若你当前的工具列表里有「Danbooru tag search / Danbooru 标签搜索」这类 MCP 工具，**务必优先调用它**去查询/确认标准 Danbooru 标签，再把准确的英文标签填进 prompt，不要仅凭记忆臆造标签、也不要原样透传中文。只有当没有该类 MCP 工具时才退而直接用你自己的翻译能力改写为英文标签。
        不确定工作流时留空 workflow，插件会用默认；只有用户明确要某种画风且你有把握时才传 workflow 名称（不确定可先调 comfyui_workflows）。
        
        图生图工作流选择（重要）：当本次为图生图（附了参考图）时，工作流应填在 **img2img_workflow** 参数里，**不要**把文生图工作流名填进 workflow（文生图工作流往往没有图加载节点，无法做图生图会报错）。
        调用前务必先调 comfyui_workflows 查询真实列表（它会标注每个工作流「[支持图生图]」还是「[仅文生图]」），再按优先级选 img2img_workflow：
        0. ★优先选名称含「图生图」字样的工作流（管理员通常把图生图工作流命名为「XX图生图」，专为图生图设计）。
        1. 只选列表里标记「[支持图生图]」的工作流；「[仅文生图]」的工作流绝不能用于图生图。
        2. 在支持图生图的工作流里按名称语义匹配用户画风（转真人/写实→含「真人」；转动漫/二次元→含「动漫」「二次元」；用户说了名字→用那个名字）。
        3. 都不匹配就**留空** img2img_workflow，让插件自动用图生图默认工作流，不要硬猜工作流名。
        
        重要：不要依赖历史记忆复用旧图。用户再次要图就重新生成。画完就自然收尾，不要不停追问或重复画。
        ★★一条用户请求只调一次本工具（最重要）：用户要 N 张就用 prompts 一次传 N 条不同画面，画完立刻自然收尾。
        插件对「同一条用户消息」有硬性出图闸门：本轮出过图后再调用会被直接拦回、出不了图，
        只会白费一轮。只有用户发来【下一条新消息】明确要求再画时才可再次调用。
        
        Args:
            prompt(string): 【必填】图像的正向提示词描述（中文或英文均可）。这是唯一必须填写的参数，
                不要留空，也不要用自然语言包裹，直接给出画面描述文本。
            negative_prompt(string): 负向提示词，可选，不填则留空。
            workflow(string): 文生图工作流名称，可选。用户明确要某画风且你知道对应名称时传入；否则留空用默认。不确定可用名称时可先调 comfyui_workflows 查。仅文生图时使用，图生图不要填这里。
            img2img_workflow(string): 图生图工作流名称，可选。仅在本次消息附了参考图时使用。调用前先调 comfyui_workflows 确认哪个工作流「支持图生图」，再填确切名称（优先选名称含「图生图」的）；不确定或查不到就留空用默认图生图工作流，禁止凭记忆/猜测填工作流名。
            width(number): 图片宽度，0 或不填表示使用工作流默认宽度。用户明确要求宽高时传入（如"1024x1024"、"宽512"）。
            height(number): 图片高度，0 或不填表示使用工作流默认高度。用户明确要求宽高时传入。
            loras(array[string]): 需要启用的 LoRA 名称/别名列表。每项可用 "名称" 或 "名称:权重"（冒号后为强度/权重，如 0.8 表示弱化、1.2 表示增强）。例如 ["catgirl"] 用默认权重、"catgirl:0.8" 用 0.8 权重。★硬规则：用户只要提到某个 LoRA 的名字/别名（包括「用XX lora画」「你没用lora」「重新画一张」这类纠正），都必须**先调 comfyui_loras 拿到规范名**并填进本参数；LoRA 名只写进 prompt 只是触发词，不会加载权重文件，角色会画错。★重要：当用户要求某种风格/画风/角色/人物时，**即使没给具体 LoRA 名，也应先调 comfyui_loras（可用 keyword/category 缩小，category 传「角色」）查匹配的 LoRA 再填入**；用户给了名字/别名则直接填，明确了强弱/浓度时给权重，没给则省略用默认。★角色优先：用户提到具体角色时，**先查角色 LoRA**，有匹配就填 LoRA 且不要再用 danbooru 标签重复描述外形；**没有匹配角色 LoRA 才用 danbooru MCP 查角色/作品标准标签**（见上方「角色/作品的处理顺序」）。只有确认没有任何匹配 LoRA、或用户明确不要 LoRA 时才留空。★触发词说明：启用 LoRA 后插件默认自动追加其全部触发词（一般无需你干预、也不要传 trigger_words）。仅当触发词与用户本次要求明确冲突（典型：触发词里含服装/配饰词，而用户要求换别的衣服）时，才用 trigger_words 参数传入筛选后的触发词（保留角色/画风核心特征词、只剔除冲突词；多 LoRA 时必须合并全部触发词再筛）；没有冲突就不要传该参数。
            seed(number): 随机种子，0 或不填表示每次随机。用户明确要求"固定/复现/用同样的种子"时传入具体数字。
            count(number): 本次要生成的图片张数。★最重要规则：用户明确说出的数量是最高优先级，必须严格遵守——用户说"一张/只发一张/就一张/单张"→ 必须传 count=1；用户说"来 3 张/两张/五张"等具体数字 → 传对应 N。其次：①用户完全没提数量（如"画张图"）→ 不传 count（默认 1 张）；②"换个角度/再画一下/重来/再来"这类语义词【不自动代表多张】，默认仍为 1 张，除非用户明确说了要"几张/一些/多张"；③只有用户明确表达要多张（"来几张/多画几张"）但没给具体数字时 → prompts 传 3 条不同画面。★【count 当前恒为 1，一般不用传】：张数由 prompts 的条数决定，本参数是预留给将来「同一条提示词跑不同种子出多张」用的，现在传大于 1 会被拦回并要求改用 prompts 数组。
            prompts(array): 多条出图项，【要几张就传几条】，每条各出 1 张。两种写法都支持：
                ① 纯字符串数组（旧，全局参数共享）：每条是一个画面描述，例如
                   ["1girl 猫娘", "1boy 骑士"]；"白天、晚上、深夜各来 2 张"→ 传 6 条。
                ② 对象数组（新，每项可独立定制）：每条是 {"prompt", "workflow",
                   "img2img_workflow", "loras", "width", "height", "denoise", "seed"} 对象，
                   未写的字段回落全局参数。例如要「真人、动漫各来一张、用各自工作流」→ 传
                   [{"prompt":"写实美女","workflow":"写实"}, {"prompt":"动漫少女","workflow":"Anima"}]，
                   一次调用两张各用各工作流出齐，不会被拆分调用拦回。
                ★每条要写【不同的画面】，不要把同一条提示词重复多遍。与 prompt 二选一即可，
                两者都传时以 prompts 为准。需要多个画面请用它一次传完，不要拆成多次调用本工具。
            image(string): 图生图参考图的 URL。仅当用户在消息里明确带图并要变换时传；多数情况插件自动从消息提取，无需传此参数。
            denoise(number): 降噪幅度/重绘强度（0~1），仅图生图有效。不传或 -1 则用工作流配置默认值。用户明确要求"改多少/像不像原图"时传入。
            trigger_words(string): LoRA 触发词的追加控制。★默认【绝不传】本参数——不传时插件会自动把启用 LoRA 的全部触发词追加进提示词，这通常就是正确行为。仅当同时满足以下两个条件才允许传：
                ①你刚通过 comfyui_loras 看到了该 LoRA 的触发词原文；
                ②触发词里存在与用户本次要求明确冲突的词（典型：触发词含 white dress、black gloves 等服装/配饰词，而用户要求换别的衣服/穿泳装等）。
                此时传入筛选后的触发词（逗号分隔，必须来自 comfyui_loras 返回的 trigger_words）：保留角色/画风核心特征词，只剔除与用户要求冲突的词。★启用多个 LoRA 时，必须把所有启用 LoRA 的触发词合并后再筛选，绝不能只传其中一个 LoRA 的。★禁止传空字符串（宁可整词保留也不要全部剔除）。
            platform(string): 生图平台，可选。不传=使用管理员配置的默认平台（通常是 ComfyUI）。可传 comfyui / nai / openai / custom 或平台显示名来临时指定平台。★能力差异：NAI 类平台吃英文 Danbooru 标签、无 LoRA/工作流概念（loras 会被忽略，请直接在提示词里写角色/画风标签）；OpenAI 类平台吃自然语言描述。管理员未配置任何第三方平台时不要传本参数。部分平台受管理员设置的用户白名单限制，无权限的用户调用会自动回退 ComfyUI 出图（无需特殊处理）。

        补充说明：
        - 用户未明确要求宽高/lora/seed/denoise 时，这些参数可不传，插件自动使用工作流或配置默认值。
        - 图生图参考图的选择（★最容易出错）：参考图必须是**用户自己发的那张原图**。你（AI）上次生成的
          结果图**不是**参考图，除非用户明确说「把上次生成那张/刚才那张成品再改」，否则**绝不要**把 AI
          生成的图当参考图。用户说「再改一下/重新改/继续改这张图」（没发新图）时，应基于**最初用户发的
          那张原图**继续改，优先从对话历史找到用户最初附带的原图并引用；找不到就提示用户重发图，**不要**
          擅自用最近一次生成的图顶替。仅在用户明确引用你刚生成的某张图去二次加工时才用 AI 生成图。
        - 图生图只认「用户发的那张参考图」；群聊里无关的旧图不算。仅在用户明确针对某张图时按图生图走。
        """
        # LLM 工具开关：关闭时拒绝本插件 LLM 的自动调用，
        # 但伴侣插件等第三方主动调用（带 source 标记）不受影响。
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if plugin is None:
            plugin = self
        if not plugin._cfg("enable_llm_tools", True) and not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            return "LLM 画图工具已关闭，请使用指令绘图（/draw、/img2img、/画xxx 等）。"

        # 已读回执：用户用自然语言触发生图（comfyui_draw）时，给原消息贴表情表示「已读」。
        # 伴侣插件等第三方主动调用（带 source）无对应用户消息，跳过避免误贴。
        if not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            await self._react_ack(event)

        # 部分 AstrBot 版本下 self/event 绑定可能异常（self 为 None 或 event 为 None），
        # 这里用全局实例与最近事件兜底，避免 'NoneType' object has no attribute '_do_draw'。
        if not isinstance(event, AstrMessageEvent):
            event = getattr(plugin, "_last_event", None)
        if event is None:
            return "⚠️ 绘图工具未能获取到会话事件，请稍后重试，或直接使用 /draw 指令绘图。"

        # 诊断日志：记录工具路径解析到的 user/group，便于排查「指令能出、LLM 无权限」类不对称
        _dbg_uid, _dbg_gid = plugin._event_ids(event)
        logger.info(
            f"【绘图·工具权限】user={_dbg_uid} group={_dbg_gid} "
            f"whitelist_active={plugin._is_whitelist_active()} event_type={type(event).__name__}"
        )

        # ── 绘图黑名单（工具入口）─────────────────────────────────────────
        # 命中黑名单时必须「明确告知 LLM 这是权限拦截、禁止重试」，而不能让它走到 _do_draw
        # 后被当成「生图失败」——否则 LLM 会把拦截误判成后端故障反复重试，刷出一堆消息
        # （实测：被拉黑用户让画图，LLM 连调 3 次 comfyui_draw，每次都回话+语音）。
        # 白名单优先：allow_draw_users 非空时跳过黑名单（与 _do_draw 的判定一致）。
        # 伴侣插件（带 source）走原 _do_draw 路径，这里不拦；且工具上下文不 _send，
        # 直接 return 文本让 LLM 如实转述给用户，避免多出来一条消息。
        if not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            if plugin._is_whitelist_active():
                _wl_ok, _wl_reason = plugin._check_whitelist(event)
                if not _wl_ok:
                    return (
                        "⚠️ 当前用户/群不在发图白名单内，无法出图。"
                        "请直接、简短地告诉用户「你暂无绘图权限」，不要重试、也不要改 prompt 再调一次。"
                    )
            else:
                _bl_ok, _bl_reason = plugin._check_blacklist(event)
                if not _bl_ok:
                    return (
                        "⚠️ 当前用户/群已被加入绘图黑名单，无法出图。"
                        "请直接、简短地告诉用户「你暂无绘图权限」，不要重试、也不要改 prompt 再调一次。"
                    )

        # ── 单轮出图闸门（v5.0）───────────────────────────────────────
        # 「一次对话出完就关门」的唯一保证：本轮已成功出图 / 已失败重试达到上限就收尾。
        # 不看参数、不看时间间隔，因此不存在被绕过的可能。
        _gate_ok, _gate_hint = plugin._draw_run_check(event, source=source, tool_name="comfyui_draw")
        if not _gate_ok:
            # 硬终止（重复调用超过容忍次数）：STOP_HINT 劝退文本对执拗的模型无效
            # （实测同轮连续空转 13+ 次、每轮都烧 token）。改用熔断：
            # 第一次先由插件主动给用户发一条收尾消息（避免「图发完就哑了」），
            # 再 return None —— AstrBot 的 tool_loop_agent_runner 对工具返回 None
            # 会直接 transition DONE 终止整个 Agent Loop，不再进行下一轮推理。
            # 同一轮并行发起的其余重复调用不重复发消息，直接 None。
            if _gate_hint is plugin._DRAW_RUN_STOP_HINT:
                _st = plugin._draw_run_state_of(event)
                if not _st.get("terminated"):
                    _st["terminated"] = True
                    try:
                        await plugin._send(event, "（图片已经在上面发过啦，就先聊到这里吧～）")
                    except Exception:
                        pass
                return None
            return _gate_hint

        # prompt 兜底：LLM 有时不会把描述填进 tool 参数（参数空洞 / 空 JSON {}）。分两种处理：
        #   · 不带 source（LLM 直接调本工具）：空参数一律【直接报错】，绝不去对话历史里兜底
        #     抓文本当 prompt —— 历史反复证明那样会把上一轮的指令、系统提示之类的话当成
        #     画面描述，画出莫名其妙的图，并陷入「空参→兜底→再空参」的死循环。
        #     报错文案会要求模型把画面描述填进 prompt 后再调一次（配合失败重试额度生效）。
        #   · 带 source（伴侣插件等第三方主动调用）：它们本就不填 prompt、依赖插件兜底，
        #     保留原有的「指定模型提取 → 原始消息文本」链路，不破坏既有集成。
        # 判定"有没有画面描述"：prompt 单条 或 prompts 数组，任一非空即算有。
        # ★必须同时看 prompts——早期版本只判断 prompt，导致模型只传 prompts 数组
        #   （多条不同画面、不传 prompt）时被误判成空参数、直接报错退回，
        #   白白浪费一次调用，还逼得模型改成分多次调用。
        _has_any_prompt = bool(prompt and prompt.strip()) or bool(
            [x for x in (prompts or []) if str(x).strip()]
        )
        if not _has_any_prompt:
            if not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
                plugin._draw_run_fail(event, kind="empty")
                logger.info("【工具·llm_draw】 空参数调用（prompt 与 prompts 均为空），拒绝兜底提取，要求模型补齐后重试")
                return (
                    "调用失败：缺少画面描述。请把本次要画的画面描述填进 prompt 参数"
                    "（若是多个不同画面，改用 prompts 数组逐条列出），再重新调用本工具一次。"
                )

            user_text = ""
            try:
                user_text = (getattr(event, "message_str", "") or "").strip()
            except Exception:
                user_text = ""
            if user_text:
                extracted = await self._llm_extract_args(
                    user_text,
                    "prompt(string): 图像正向提示词描述（必填）。\n"
                    "negative_prompt(string): 负向提示词，可选。\n"
                    "workflow(string): 文生图工作流名，可选。\n"
                    "img2img_workflow(string): 图生图工作流名，可选。\n"
                    "loras(string): LoRA 名称/别名列表，可选，每项可带权重如 \"catgirl:0.8\"。\n"
                    "width(int): 宽度，可选。height(int): 高度，可选。\n"
                    "seed(int): 随机种子，可选。denoise(float): 降噪幅度0~1，可选。",
                )
                if extracted and extracted.get("prompt"):
                    prompt = str(extracted["prompt"]).strip()
                    # 用指定模型补全的其他可选参数（仅当原参数为空时）
                    if extracted.get("negative_prompt") and not negative_prompt:
                        negative_prompt = str(extracted["negative_prompt"])
                    if extracted.get("workflow") and not workflow:
                        workflow = str(extracted["workflow"])
                    if extracted.get("img2img_workflow") and not img2img_workflow:
                        img2img_workflow = str(extracted["img2img_workflow"])
                    if extracted.get("loras") and not loras:
                        loras = extracted["loras"]
                    try:
                        if extracted.get("width") is not None and not width:
                            width = int(extracted["width"])
                        if extracted.get("height") is not None and not height:
                            height = int(extracted["height"])
                        if extracted.get("seed") is not None and not seed:
                            seed = int(extracted["seed"])
                        if extracted.get("denoise") is not None and not denoise:
                            denoise = float(extracted["denoise"])
                    except (TypeError, ValueError):
                        pass
            if not prompt or not prompt.strip():
                if user_text:
                    prompt = self._strip_command(user_text, "draw")
                if not prompt or not prompt.strip():
                    return "⚠️ 调用 comfyui_draw 失败：缺少必填参数 prompt（图像的正向提示词描述）。请补充画面描述后再试。"

        # ── 出图计划：把「多条提示词 × 每条几张」摊平成 (提示词, 张数) 列表 ──────
        # prompts 数组用于一次画多个不同画面（如「分别画猫、狗和兔子」）；count 表示每条
        # 提示词各出几张。总张数受插件配置的单次上限（draw_auto.max）约束，超出自动截断
        # （上限 9、要 10 张 → 只出 9 张）；伴侣插件等带 source 的调用不受此上限限制。
        # prompts 统一规整成 list[dict]：兼容「纯字符串数组」(旧) 与
        #「对象数组」(新，每项可独立指定 workflow/img2img_workflow/loras/
        # width/height/denoise/seed，缺省回落全局参数)。
        _items = self._normalize_prompts(prompts)
        _per = max(1, int(count or 1))
        if _items:
            # 传了 prompts：【张数 = 条数】，每条各出 1 张（每条是一个独立画面）。
            # count 是预留参数（将来用于「同一条提示词跑不同种子」），当前恒为 1、不参与计算。
            _wanted = len(_items)
        else:
            # 没传 prompts：单条 prompt。
            # 要 N 张的正确写法是 prompts 传 N 个不同画面——用「单条 prompt + count=N」
            # 只会得到同一画面的 N 个近亲副本，不是用户想要的「N 张不同的图」。
            # 这里拦一次并教它正确写法；只拦一次，避免和模型僵持不下。
            if _per > 1 and not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
                _st = plugin._draw_run_state_of(event)
                if not _st.get("count_hint_done"):
                    _st["count_hint_done"] = True
                    logger.info(
                        f"【工具·llm_draw】 count={_per} 但未传 prompts，拦截并提示改用 prompts 数组"
                    )
                    return (
                        f"你要出 {_per} 张图，但只传了单个 prompt——那样只会得到同一个画面的 "
                        f"{_per} 个近似副本。请改用 prompts 数组：把 {_per} 个不同的画面各写成一项"
                        f"（例如要 3 张猫，就写 3 条不同姿势/场景/光线的猫），"
                        f"count 保持 1 不传，然后重新调用本工具一次。"
                    )
            _items = [{
                "prompt": (prompt or "").strip(),
                "workflow": None, "img2img_workflow": None, "loras": None,
                "width": 0, "height": 0, "denoise": -1, "seed": 0,
            }]
            _wanted = _per
        _allowed, _max_hint = plugin._draw_single_max(_wanted, source=source, event=event)
        # 注：_plan（含每项的 per-item 工作流解析）需在下方 resolved_wf 决策完成后构建，
        # 见「工作流决策」段之后。

        lora_map = self._parse_llm_loras(loras)

        # ── 收集图片（图生图参考图）─────────────────────────────────
        # 关键修正：图生图不要求 LLM 必须传 image 参数。用户最常见的图生图方式就是
        # 「直接发一张图 + 一句文字（如『改成水彩风』）」，此时图片作为多模态输入连同文字
        # 一起发给大模型，LLM 不会、也不应该再传 image 参数（图片它已经"看见"了）。
        #
        # 本次改动（v2.2.46）：严格区分「图生图」与「文生图」——
        #   · 判定图生图的依据 = LLM 显式传了 image 参数，OR 用户本次消息/引用里真的有图。
        #   · 历史/会话兜底（g_last_received / g_recent_user_images / g_last_generated）
        #     只用于「已判定为图生图、但参考图还没进 event」时补图，绝不让"捞到一张旧图"
        #     反过来把文生图误判成图生图（旧逻辑导致文生图去历史里捞图→误判→工作流无
        #     LoadImage 节点→报错）。
        init_images: list[str] = []

        # ① image 参数：LLM 传入的参考图 URL（显式图生图意图）
        got_explicit_image = False
        if bool(image and image.strip()):
            img_url = image.strip()
            logger.info(f"【取图】 llm_draw image 参数: {img_url}")
            p = await _image_to_local_path(img_url)
            if p:
                init_images.append(p)
                got_explicit_image = True
                logger.info(f"【取图】 image 参数下载成功: {p}")
            else:
                logger.warning(
                    f"【取图】 image 参数下载/解析失败，无法作为参考图: {img_url!r}"
                    f" —— 该路径在本机不存在（调用方/伴侣插件传来的可能是另一容器或已清理的 temp 路径）。"
                    f" 若本应走图生图，请让调用方传入当前服务器上真实可用的图片路径或 URL。"
                )

        # ② 从事件中自动提取图片（本次消息/引用里的图，是"用户确实发了图"的最可靠信号）
        event_images: list[str] = []
        last_ev = getattr(plugin, "_last_event", None)
        if not got_explicit_image:
            event_images = await plugin._extract_images(event)
            if not event_images and last_ev is not None and last_ev is not event:
                logger.info("【取图】 llm_draw 工具 event 未取到图，回退到 LLM 调用前捕获的原始事件再取一次")
                event_images = await plugin._extract_images(last_ev)
        # 去重合并（避免 image 参数 URL 和事件里是同一张图）
        seen = set(init_images)
        for ep in event_images:
            if ep not in seen:
                seen.add(ep)
                init_images.append(ep)

        # ③ 判定图生图意图，区分「强 / 弱」信号：
        #   · strong_img2img：显式传了 image 参数（无论解析成败——路径不可达也是明确
        #     的图生图意图），或本次消息/引用里真有图 —— 确有图生图意图。
        #   · weak_img2img：只显式指定了 img2img_workflow，但既没传 image、消息里也没图。
        #     这常见于伴侣插件文生图也顺带带上默认图生图工作流，并不代表真要走图生图；
        #     不能仅凭它判成图生图（否则伴侣文生图会被误判、中断）。
        #   强信号无参考图 → 走 img2img_fallback（prompt 提示 or txt2img 回退）；
        #   弱信号无参考图 → 直接回退对应风格文生图，避免误中断调用方（如伴侣）。
        strong_img2img = bool(image and image.strip()) or bool(event_images)
        weak_img2img = (
            bool(img2img_workflow and img2img_workflow.strip()) and not strong_img2img
        )
        is_img2img = strong_img2img or weak_img2img

        # ④ 强信号已判定图生图、但参考图还没拿到（图没进 event，如引用图解析失败）时，
        #    才用历史/会话/生成图兜底补一张参考图。纯文生图、以及弱信号
        #    （仅传 img2img_workflow 无参考图，常见于伴侣文生图顺带带默认图生图工作流）
        #    绝不进入这里——弱信号应直接回退对应风格文生图，避免误中断调用方（如伴侣）。
        if strong_img2img and not init_images:
            sid = getattr(event, "session_id", "") or ""
            # ① 优先用「本会话最近一次图生图实际使用的用户原图」（多轮改图时回到最初原图，
            #    而不是误用 AI 上次生成的结果图）。仅当这些缓存路径仍可读时才采纳。
            for store in (
                list(reversed(g_session_i2i_ref.get(sid) or [])),
                list(reversed(g_last_received.get(sid) or [])),
                list(reversed(g_recent_user_images.get(sid) or [])),
            ):
                for p in store:
                    if p and os.path.exists(p) and p not in init_images:
                        init_images.append(p)
                if init_images:
                    break
            # ② 以上「用户原图」类缓存均不可读时，最后才兜底用最近生成的图
            #    （仅当用户明确引用刚生成的图做二次加工，且临时路径仍有效时）。
            if not init_images:
                for p in (list(reversed(g_last_generated.get(sid) or []))[:1]):
                    if p and os.path.exists(p) and p not in init_images:
                        init_images.append(p)
                        break
            if init_images:
                logger.info(f"【取图】 llm_draw 图生图补图兜底（历史/会话/生成图）: {init_images}")
            else:
                logger.info("【取图】 llm_draw 已判定图生图但兜底仍未取到参考图，将提示用户重发图")

        if init_images:
            logger.info(f"【取图】 llm_draw 最终取得参考图 {len(init_images)} 张 -> {init_images}")
            # 记录「本会话最近一次图生图的用户原图」记忆，供多轮改图兜底回到最初原图
            # （而非误用 AI 上次生成的结果图）。仅图生图且取到参考图时记录。
            if is_img2img:
                sid = getattr(event, "session_id", "") or ""
                bucket = g_session_i2i_ref.setdefault(sid, [])
                for p in init_images:
                    if p and p not in bucket:
                        bucket.append(p)
                if len(bucket) > 3:
                    g_session_i2i_ref[sid] = bucket[-3:]
        elif is_img2img:
            logger.info(
                f"【取图】 llm_draw 意图为图生图但无参考图可用"
                f"（用户/调用方指定的图生图工作流={img2img_workflow or workflow or '默认'}），"
                f"将不下发，提示用户重发图"
            )
        else:
            logger.info("【取图】 llm_draw 文生图模式（未取图，无图生图意图）")

        # ── 决定工作流与模式 ─────────────────────────────────────────
        # 优先级：
        #   image + img2img_workflow → 用 img2img_workflow
        #   image + workflow          → 用 workflow（语义匹配）
        #   image + 都没传            → 默认图生图工作流
        #   无 image                  → workflow 或默认文生图工作流
        # is_img2img 已在取图段判定（LLM 显式传 image OR 本次消息/引用有图）。
        # 图生图意图、但取不到参考图时，行为由配置 img2img_fallback 决定：
        #   · prompt（默认）：提示用户重发图，绝不用默认工作流瞎画一张无关图。
        #   · txt2img：回退为文生图，按原图生图风格对应的「文生图默认工作流」来画
        #     （真人图生图 → 真人文生图，动漫图生图 → 动漫文生图），保证风格一致。
        img2img_fallback = (plugin._cfg("img2img_fallback", "prompt") or "prompt").strip().lower()
        # 确保在「图生图回退」分支外也定义，供下方 _resolve_workflow_for 传参（否则 NameError）。
        fallback_wf = None
        if is_img2img and not init_images:
            # 计算「回退到对应风格文生图」所需的 fallback_wf（供 txt2img 与弱信号共用）
            _req_i2i = (img2img_workflow or workflow or "").strip()
            _cfg_i2i_anime = (plugin._cfg("default_img2img_workflow", "") or "").strip()
            _cfg_i2i_real = (plugin._cfg("default_img2img_workflow_real", "") or "").strip()
            _cfg_t2i_anime = (plugin._cfg("default_workflow", "") or "").strip()
            _cfg_t2i_real = (plugin._cfg("default_workflow_real", "") or "").strip()
            _prio = (plugin._cfg("default_style_priority", "anime") or "anime").strip().lower()

            # 弱信号：只指定了 img2img_workflow、但既没传 image、消息里也没图。
            # 这常见于伴侣插件文生图也顺带带上默认图生图工作流，调用方根本没有参考图
            # 可重发。此时不中断，直接回退对应风格文生图，避免误伤伴侣文生图。
            if weak_img2img:
                logger.warning(
                    f"【取图】 llm_draw 仅指定 img2img_workflow（={_req_i2i or '默认'}）但无参考图，"
                    f"判定为文生图回退，避免误中断调用方（weak 信号）"
                )
                was_img2img = True
                is_img2img = False
                if _req_i2i and _req_i2i.lower() == _cfg_i2i_real.lower() and _cfg_t2i_real:
                    fallback_wf = _cfg_t2i_real
                elif _req_i2i and _req_i2i.lower() == _cfg_i2i_anime.lower() and _cfg_t2i_anime:
                    fallback_wf = _cfg_t2i_anime
                else:
                    fallback_wf = _cfg_t2i_real if _prio == "real" else _cfg_t2i_anime
                logger.info(
                    f"【取图】 llm_draw 弱信号回退文生图：原图生图工作流={_req_i2i or '默认'}, "
                    f"回退到文生图工作流={fallback_wf or '（均无配置，走默认）'}"
                )
            elif img2img_fallback == "txt2img":
                logger.warning(
                    f"【取图】 llm_draw 已判定为图生图（期望工作流={img2img_workflow or workflow or '默认'}）"
                    f"但取不到任何参考图，按配置 img2img_fallback=txt2img 回退为文生图"
                )
                was_img2img = True
                is_img2img = False
                if _req_i2i and _req_i2i.lower() == _cfg_i2i_real.lower() and _cfg_t2i_real:
                    fallback_wf = _cfg_t2i_real
                elif _req_i2i and _req_i2i.lower() == _cfg_i2i_anime.lower() and _cfg_t2i_anime:
                    fallback_wf = _cfg_t2i_anime
                else:
                    # 无法判断具体风格：按全局风格优先级选对应的文生图默认工作流
                    fallback_wf = _cfg_t2i_real if _prio == "real" else _cfg_t2i_anime
                logger.info(
                    f"【取图】 llm_draw 回退文生图：原图生图工作流={_req_i2i or '默认'}, "
                    f"回退到文生图工作流={fallback_wf or '（均无配置，走默认）'}"
                )
            else:
                logger.warning(
                    f"【取图】 llm_draw 已判定为图生图（期望工作流={img2img_workflow or workflow or '默认'}）"
                    f"但取不到任何参考图，终止并提示用户重发图（img2img_fallback=prompt，不降级为文生图）"
                )
                return "图生图需要一张参考图，但没能从本次消息/引用/历史里取到图片。请先发送一张图片（或引用一张图）再说明要怎么变换它，例如「把这张图变成夜晚」。"
        else:
            was_img2img = False

        if is_img2img and img2img_workflow and img2img_workflow.strip():
            resolved_wf = img2img_workflow.strip()
        elif is_img2img and workflow and workflow.strip():
            # 图生图但未显式指定 img2img_workflow：LLM 常把「文生图工作流名」填进
            # workflow 字段（其语义就是文生图工作流名），而文生图工作流往往没有
            # LoadImage 节点、无法做图生图。此时应优先用配置的「默认图生图工作流」，
            # 而不是直接把 LLM 传来的文生图工作流名当图生图工作流用（否则会因缺
            # 图加载节点而报错，见「工作流没有 LoadImage 类节点」）。
            # 判断依据：该工作流配置里显式填了 image_node（用户配它就是要做图生图），
            # 或它本身命中配置的默认图生图工作流 → 视为具备图生图能力，直接使用；
            # 否则回退到按「风格优先级 + 图生图」选定的默认图生图工作流。
            _req_wf_name = workflow.strip()
            _req_wf_cfg = next(
                (w for w in plugin._workflows() if (w.get("name") or "").strip() == _req_wf_name),
                None,
            )
            _req_has_image_cfg = bool(_req_wf_cfg and (_req_wf_cfg.get("image_node") or "").strip())
            _default_i2i = (plugin._pick_default_workflow_name(is_img2img=True) or "").strip()
            if _req_has_image_cfg or (_req_wf_name and _req_wf_name == _default_i2i):
                resolved_wf = _req_wf_name
                logger.info(
                    f"【工具·llm_draw】 图生图：LLM 指定工作流「{_req_wf_name}」具备图生图能力"
                    f"（image_node 已配置或命中默认图生图工作流），直接使用"
                )
            else:
                resolved_wf = _default_i2i or None
                logger.info(
                    f"【工具·llm_draw】 图生图：LLM 指定工作流「{_req_wf_name}」为文生图工作流"
                    f"（无 image_node 配置），回退到默认图生图工作流「{resolved_wf or '默认'}」"
                )
        else:
            resolved_wf = (workflow or "").strip() or None
        # 回退为文生图时：不沿用原图生图工作流名（那可能是图生图工作流、有 LoadImage
        # 但无图注入会报错），改用工步计算好的对应风格文生图工作流；未配置则 None（走默认）。
        if was_img2img and not is_img2img:
            resolved_wf = fallback_wf or None
        logger.info(
            f"【工具·llm_draw】 工作流决策：is_img2img={is_img2img}, "
            f"指定 img2img_workflow={img2img_workflow!r}, 指定 workflow={workflow!r}, "
            f"最终选用工作流={resolved_wf or '默认文生图'}"
        )

        # ── 剔除「不要发表情包」类元指令 ────────────────────────────────
        # 这类句子是对**出图方式**的否定要求、不是画面描述；若不剔除会被当成描述语
        # 写进画面提示词（用户反馈："把『不要发表情包』当做描述语了"）。无匹配时原样返回。
        prompt = comic.strip_comic_negations(prompt or "")

        # ── 表情包/漫画意图 / 漫画工作流 自动路由（v5.5.0，v5.6.5 增强）──
        # 触发造词的两类情况：
        #  1) 用户原话命中「表情包/漫画/带字」意图；
        #  2) 用户显式选的工作流本身就是「漫画/带字工作流」（配了 prompt_slots）。
        # 第 2 类很关键：否则走 comfyui_draw + workflow='表情包'（用户没说『表情包』二字）
        # 时不会造词，boogu 节点会直接用工作流里写死的默认提示词——
        # 表现为『巨大字 + 永远有底部字幕 + 固定气泡』，正是用户反复吐槽的丑样子。
        # 已带 slot_values（comfyui_comic 已注入）或第三方 source 调用不触发本路由。
        if slot_values is None and not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            _intent_text = (getattr(event, "message_str", "") or "").strip()
            _comic_by_intent = self._is_comic_intent(_intent_text, prompt)
            _cwf = None
            if _comic_by_intent:
                _cwf, _cerr = self._resolve_comic_wf("", is_img2img)
            elif resolved_wf:
                _rwf_cfg = self._find_workflow_by_name(resolved_wf)
                if _rwf_cfg and self._workflow_kind(_rwf_cfg) == "comic":
                    _cwf = resolved_wf
            if _cwf:
                logger.info(
                    f"【路由】 漫画工作流「{_cwf}」强制造词（覆盖工作流默认 boogu 提示词）"
                    f"(意图命中={_comic_by_intent}, is_img2img={is_img2img})"
                )
                _cwf_cfg = self._find_workflow_by_name(_cwf) or {}
                if is_img2img:
                    img2img_workflow = _cwf
                else:
                    workflow = _cwf
                resolved_wf = _cwf
                # 清理段1：剥离 bot 误写的「气泡文字字段」(text:/气泡:) 与 boogu 形状描述，
                # 抽到槽位2 的自然语言；同时清掉出图计划里每条 prompt，避免 anima 画错
                _clean_prompt, _bubble = comic.strip_bubble_field_from_prompt(prompt)
                prompt = _clean_prompt
                _bubble_hint = ""
                if _bubble:
                    _bubble_hint = f"\n（用户/上文指定的气泡文字：{_bubble}）"
                for _it in _items:
                    _it["prompt"], _ = comic.strip_bubble_field_from_prompt(_it.get("prompt") or "")
                _prompts = await self._comic_write_prompts_llm(
                    _cwf_cfg, (_intent_text or _clean_prompt) + _bubble_hint, _clean_prompt
                )
                # 节点 A（绘图提示词）：覆盖出图计划里每条的 prompt
                if _prompts.get("draw"):
                    prompt = _prompts["draw"]
                    for _it in _items:
                        _it["prompt"] = _prompts["draw"]
                # 节点 B（boogu 编辑指令）：随 slot_values 传入 _do_draw，按配置 boogu_node 注入
                slot_values = {"boogu": _prompts.get("boogu") or ""}
                logger.info(
                    f"【路由】 漫画两段提示词生成："
                    f"绘图={'有' if _prompts.get('draw') else '无'}, "
                    f"boogu={'有' if slot_values.get('boogu') else '无（沿用工作流默认）'}"
                )

        # ── 出图计划：把多条提示词摊平为「每项独立参数」的列表 ──────────
        # 每项独立完成工作流解析（per-item workflow / img2img_workflow），缺省回落调用级
        # 默认工作流 resolved_wf；其余尺寸 / LoRA / denoise / seed 同理回落全局参数。
        _plan: list[dict] = []
        for _it in _items[: max(1, _allowed)]:
            _wf = self._resolve_workflow_for(
                _it["workflow"], _it["img2img_workflow"], is_img2img, was_img2img, fallback_wf
            )
            if not _wf:
                _wf = resolved_wf
            _plan.append({
                "prompt": _it["prompt"],
                "wf": _wf,
                "loras": _it["loras"],
                "width": _it["width"],
                "height": _it["height"],
                "denoise": _it["denoise"],
                "seed": _it["seed"],
            })
        if not _plan:
            _plan = [{
                "prompt": _items[0]["prompt"], "wf": resolved_wf,
                "loras": None, "width": 0, "height": 0, "denoise": -1, "seed": 0,
            }]
        logger.info(
            f"【工具·llm_draw】 出图计划：{len(_plan)} 项、共 {len(_plan)} 张"
            + (f"（请求 {_wanted} 张，已按单次上限截断）" if _max_hint else "")
        )

        # 提示词原样透传（已移除「伴侣插件提示词过滤」功能）：不做过多的拆分/改写，
        # 直接传给 ComfyUI 出图。实际使用的正向提示词取自出图计划 _plan 的每一组
        # （支持 prompts 多画面，每组自带一条提示词）。
        negative = negative_prompt or ""

        # 改为普通协程（不再用 yield），以兼容用 `await` 调用本工具的第三方插件
        # （如 astrbot_plugin_private_companion 主动生图）。_do_draw 现以
        # (图片节点, 本地路径) 元组产出。注意：LLM 工具的 return 值只会作为工具
        # 结果文本回传给模型，框架不会自动渲染图片，所以原生对话下必须在这里主动
        # event.send 把图发到聊天里。
        # - 带 source（伴侣插件 proactive 管道）时，return JSON 文本，由伴侣解析
        #   image_path 后自己发图，本函数不重复发图；
        # - 不带 source（原生对话 / 伴侣 Agent 自主 tool_call）时，主动 event.send
        #   图片，再 return 简短文本告知模型已处理。
        is_companion = bool(source and source.strip() == SOURCE_COMPANION_PLUGIN)

        img_paths: list[str] = []
        # 按出图计划逐张生成：_do_draw 每次调用会完整等待一次出图，串行是「来 N 张」的预期成本。
        # 原生 / AI 对话调用改为「画一张发一张」：每张图出来立即 event.send，
        # 用户先收到第 1 张、再第 2 张、再第 3 张，而不是等全部画完一次性连发。
        # 伴侣插件（is_companion）仍需收集全部路径后统一返回 JSON，由调用方自行发图。
        _total_n = len(_plan)
        # 单批上限 max_images_per_batch：单次工具调用最多连续生成几张就收尾返回，
        # 剩余张数转入「后台续画」任务自动补发。核心目的——确保单批总耗时 < AstrBot
        # 框架工具超时（默认 120 秒），工具能在超时前正常 return，从而不会被框架
        # wait_for 取消。一旦被框架取消，正在 await 的 event.send（发 QQ 图）会被打断，
        # 破坏 aiocqhttp 的 bot WebSocket 连接，导致之后所有发图永久卡死（实测十几分钟无回复）。
        # 所以每批出图必须在 120 秒内收尾返回，框架永不取消我们。
        try:
            _max_batch = max(1, int((plugin._cfg("draw_auto", {}) or {}).get("max_images_per_batch", 4) or 4))
        except (TypeError, ValueError):
            _max_batch = 4
        # 可选软耗时预算（秒）：>0 时在单批内再叠加时间上限，提前收尾转后台续画
        try:
            _budget = float((plugin._cfg("draw_auto", {}) or {}).get("per_draw_time_budget_sec", 0) or 0)
        except (TypeError, ValueError):
            _budget = 0.0
        _t0 = time.monotonic()
        _seq = 0
        _remain_items: list[dict] = []
        # 主动发图（event.send）失败的张数。旧代码先 _draw_run_hit 再 send，
        # send 抛异常只记 warning、返回值仍宣称「已发送」，导致系统完全不知道图
        # 没发出去：用户看不到图反复催，重试又被单轮闸门按「已出图」拦回。
        # 现在如实统计，发送成功才计「本轮已出图」，失败由返回值如实告知模型。
        _send_fail = 0
        for _item in _plan:
            if _remain_items:
                # 已达单批上限，后续计划全部转入后台续画，避免触发框架超时取消
                _remain_items.append(_item)
                continue
            _positive = (_item["prompt"] or "").strip()
            if not _positive:
                continue
            # 单批张数达上限，或软耗时预算耗尽：当前项转入后台续画收尾转续画。
            # 这样工具在 120 秒内收尾返回，框架绝不取消我们，发图连接永不坏。
            # 硬性时间兜底：单批已用时间超过安全阈值（默认 100s，< 框架默认 120s
            # 工具超时）就收尾转续画，避免慢服务器上单批总耗时逼近/超过框架超时而被
            # 取消、破坏发图连接卡死。用户配置的 per_draw_time_budget_sec（>0）可进一步收紧。
            _time_stop = 100.0
            if _budget > 0:
                _time_stop = min(_time_stop, _budget)
            if len(img_paths) >= _max_batch or (time.monotonic() - _t0) > _time_stop:
                _remain_items.append(_item)
                continue
            # 仅当调用方明确指定了 seed 时才按序号递增（保证这批图可复现）。
            # 未指定 seed（0/空/None）时必须保持 0，由下方 `or None` 转成 None 走随机；
            # 若仍用 (0 + _seq) 会让多张出图的第 2 张起被固定成 1、2、3 这类退化种子。
            _item_seed = _item["seed"]
            _seed_i = (int(_item_seed) + _seq) if (_total_n > 1 and _item_seed) else _item_seed
            _seq += 1
            # per-item 参数：缺省回落全局参数
            _item_lora_map = plugin._parse_llm_loras(_item["loras"]) if _item["loras"] else lora_map
            _item_w = _item["width"] or width
            _item_h = _item["height"] or height
            _item_denoise = _item["denoise"] if _item["denoise"] >= 0 else denoise
            async for node, p in plugin._do_draw(
                event,
                _item["wf"],
                _positive,
                negative,
                _item_w or None,
                _item_h or None,
                _item_lora_map,
                None,
                _seed_i or None,
                init_images=init_images or None,
                is_img2img=is_img2img,
                denoise=_item_denoise if _item_denoise >= 0 else None,
                # LLM 筛选后的触发词（None=未传走自动全量追加；""=不追加；非空=只追加这些）
                trigger_words=trigger_words,
                # 生图平台（""=用默认平台 active_platform；nai/openai/custom=临时指定）
                platform=platform,
                # 伴侣插件 proactive（机器人主动生图）不发「正在处理」即时提示，
                # 避免打扰；原生 / AI 对话默认发，让用户立刻知道已受理。
                notify_pending=not bool(source and source.strip() == SOURCE_COMPANION_PLUGIN),
                source=source,
                slot_values=slot_values,
                # 图文消息：配文只加在【第一张】图上（还没出过图 = 这是第一张），
                # 多张时避免同一句话被重复 N 遍。
                caption=(caption if not img_paths else ""),
            ):
                if p:
                    img_paths.append(p)
                # 原生 / Agent 调用：画一张立刻发一张（边画边发）。
                # LLM 工具的 return 值只会作为工具结果文本回传给模型，框架并不会
                # 自动把 MessageChain 渲染成图片发给用户，所以必须主动 event.send。
                if node is not None and not is_companion:
                    # 画一张立刻发一张。★「本轮已出图」必须按【图已生成】计，
                    # 绝不能按「发送是否成功」计——图一旦生成，算力已消耗、图也已入库；
                    # 若只在 send 成功时才 hit，群聊 send 失败（风控/限流/掉线，
                    # 群聊远比私聊常见）时闸门永不计数，模型就会一直重画停不下来
                    # （实测：群聊说一句「再来」后无限自动出图）。发送成败只决定
                    # 【返回给模型的文案】（如实告知，不谎报成功），不影响闸门计数。
                    plugin._draw_run_hit(event)
                    try:
                        await event.send(node if isinstance(node, MessageChain) else MessageChain([node]))
                    except Exception as _e:
                        _send_fail += 1
                        logger.warning(f"【出图·发送失败】 图片已生成但 event.send 失败: {_e}")

        if img_paths:
            if is_companion:
                # 伴侣插件：用 JSON 文本返回图片路径，由调用方负责发图与解析。
                # note 明确告知调用方：图已生成、直接用 image_paths 发，不要再用
                # astrobot_file_read_tool 去读路径、也不要用 pc_send_current_media 重复发送。
                return json.dumps({
                    "image_paths": img_paths,
                    "status": "ok",
                    "note": "图片已生成完成，image_paths 为服务器本地路径，请直接发送这些图给用户；"
                            "不要再用 astrobot_file_read_tool 去读取图片路径，"
                            "也不要用 pc_send_current_media 重复发送同一张图（图已生成，重发只是重复刷图）。",
                }, ensure_ascii=False)
            # 原生 / Agent 调用：图片已在循环内「画一张发一张」，这里让模型收尾。
            # 注意：不要 return None——None 会让 AstrBot 直接判定 DONE 结束循环，
            # 模型一句话不说，用户会看到「图发完就哑了」。
            # ★发送失败优先：必须把「图已生成但没发到聊天窗口」如实回传，
            # 否则模型以为成功、照常回复「图已发给你」，而用户压根没看到图，
            # 再催时又被单轮闸门按「已出图」拦回——形成「说发了却没图」的死循环。
            if _send_fail:
                return (
                    f"⚠️ 图片已生成（共 {len(img_paths)} 张，已存入图库），"
                    f"但发送到聊天窗口失败 {_send_fail} 张（多为协议端掉线/风控/超时，图本身没问题）。"
                    f"请简短告诉用户「图生成好了，但发送时卡了一下，稍后再要一次我就重新发给你」，"
                    f"绝不要说「已经发给你了」；也不要现在就重新调用本工具、"
                    f"更不要改用 send_message_to_user / pc_send_current_media 自行发送——"
                    f"请先结束本轮回复，等用户再次明确索要时再处理。"
                )
            if _remain_items:
                # 单批已达上限，剩余张数转入后台续画任务（工具已正常返回，QQ 连接健康，
                # 后台任务独立把剩余图生成并自动发来，一次消息即可收齐全部 N 张）。
                try:
                    asyncio.create_task(self._draw_continue(
                        event, _remain_items, negative,
                        init_images, is_img2img, source,
                        seq_start=len(img_paths),
                    ))
                except Exception as _e:
                    logger.warning(f"【续画】 启动后台续画任务失败: {_e}")
                return (
                    f"本轮先生成并发送了 {len(img_paths)} 张（计划共 {_total_n} 张），图片已发到聊天窗口。"
                    f"剩下的 {len(_remain_items)} 张我会在后台继续生成，稍后自动发给你，"
                    f"你无需再发任何消息，也不要用 pc_send_current_media 重复发送已发的图。"
                )
            # 一次调用内已画完：图片已由插件直接发到聊天窗口，这里只需让模型自然收尾。
            # 注意：返回值【不】再附带本地路径——早期版本把路径给模型本是防它乱造路径，
            # 但副作用是模型会拿真路径再用 send_message_to_user 把已发的图重发一遍
            # （实测出现「连续发两次图」）。不给路径，模型就物理上无法重复发送；
            # 同时明确禁止调用发送类工具，避免它用 pc_send_current_media 之类再刷一次。
            return (
                f"✅ 图片已成功生成并发送到聊天窗口，用户已经能看到，你无需再做任何发送动作。"
                f"请用一句话自然告诉用户图已发给他即可；"
                f"不要调用 send_message_to_user / pc_send_current_media 把已发的图再发一次"
                f"（那只会刷出重复图片），也不要用 astrobot_file_read_tool 去读取该图。"
                + (_max_hint or "")
            )
        # 一张都没出：若仍有剩余要画（极少见，如软耗时预算设得过小导致一张都来不及出），
        # 仍转后台续画，不记后端失败以免模型空转重试；其余情况记一次后端失败。
        if _remain_items:
            try:
                asyncio.create_task(self._draw_continue(
                    event, _remain_items, negative,
                    init_images, is_img2img, source,
                    seq_start=0,
                ))
            except Exception as _e:
                logger.warning(f"【续画】 启动后台续画任务失败: {_e}")
            return (
                f"马上为你生成（计划共 {_total_n} 张），我先去后台画，稍后自动发给你，无需发消息。"
            )
        plugin._draw_run_fail(event, kind="backend")
        return "本次生图失败。请用一句话简短向用户说明生成遇到问题即可，不要复述本提示。"

    async def _draw_continue(
        self, event, remaining: list[dict], negative,
        init_images, is_img2img, source,
        seq_start: int = 0,
    ):
        """后台续画：工具调用按单批上限（max_images_per_batch）先发完前几张并正常 return
        后，由本独立任务把剩余张数继续生成并主动发给用户。

        之所以用后台任务而非「让用户再发消息续调」：
        - 工具已正常返回（单次调用 < 框架超时），QQ 发图连接健康，后台任务 send 安全；
        - 一次用户消息即可收齐全部 N 张，不依赖模型是否听话续调，也不会重复出图；
        - 全程 try/except + CancelledError 兜底，中途某张失败只跳过该张，绝不卡死事件循环。
        """
        total = len(remaining)
        sid = (getattr(event, "session_id", "") or "global") if event is not None else "global"
        if total <= 0:
            return
        logger.info(f"【续画·开始】 会话 {sid} 后台补画 {total} 张（seq_start={seq_start}）")
        try:
            try:
                await event.send(MessageChain([Plain(text=f"🎨 正在生成剩下的 {total} 张，稍后自动发来～")]))
            except Exception:
                pass
            _seq = seq_start
            for _item in remaining:
                _positive = (_item["prompt"] or "").strip()
                if not _positive:
                    continue
                _item_seed = _item["seed"]
                _seed_i = (int(_item_seed) + _seq) if _item_seed else _item_seed
                _seq += 1
                # per-item 参数：缺省回落（续画无全局 lora_map，未指定 loras 的项用 None）
                _item_lora_map = self._parse_llm_loras(_item["loras"]) if _item["loras"] else None
                _item_w = _item["width"] or None
                _item_h = _item["height"] or None
                _item_denoise = _item["denoise"] if _item["denoise"] >= 0 else None
                async for node, p in self._do_draw(
                    event, _item["wf"], _positive, negative,
                    _item_w, _item_h, _item_lora_map, None,
                    _seed_i or None,
                    init_images=init_images or None, is_img2img=is_img2img,
                    denoise=_item_denoise,
                    notify_pending=False, source=source,
                    comic_feature=comic_feature,
                ):
                    if node is not None:
                        # 同主流程：「本轮已出图」按【图已生成】计（不按发送结果计），
                        # 否则群聊 send 失败时闸门不计数、模型无限重画；
                        # 发送失败只记日志，不影响闸门计数。
                        self._draw_run_hit(event)
                        try:
                            await event.send(node if isinstance(node, MessageChain) else MessageChain([node]))
                        except Exception as _e:
                            logger.warning(f"【续画·发送】 失败: {_e}")
            try:
                await event.send(MessageChain([Plain(text=f"✅ 剩下的 {total} 张已经画好发给你啦～")]))
            except Exception:
                pass
            logger.info(f"【续画·完成】 会话 {sid} 后台补画 {total} 张完成")
        except asyncio.CancelledError:
            logger.warning(f"【续画·取消】 会话 {sid} 后台续画被取消（剩余图可能未发，用户可重发请求补画）")
        except Exception as _e:
            logger.exception(f"【续画·异常】 会话 {sid} 后台续画失败: {_e}")

    # 提取某条用户消息（含引用/卡片）里的图片本地路径，供缓存到"最近收到图"。
    # 覆盖：消息内图、引用消息内嵌图、引用 API 回退；已过滤不存在路径。
    async def _collect_user_images(self, event: AstrMessageEvent) -> list[str]:
        imgs = await self._extract_images(event)
        # _extract_images 已覆盖：消息内图、引用消息内嵌图、引用 API 回退。
        # 这里再尝试补一次引用图（部分平台引用图只在事件里以 Reply.id 形式存在，
        # 且需在图片未被剥离的「原始消息到达」时机抓取才有效）。
        try:
            quoted = await _extract_quoted_images(event)
            quoted = [await _image_to_local_path(q) for q in quoted]
            for q in quoted:
                if q and q not in imgs:
                    imgs.append(q)
        except Exception as qe:
            logger.debug(f"【取图】 原始消息抓引用图失败（忽略）: {qe}")
        return [p for p in imgs if p and os.path.exists(p)]

    def _record_user_images(self, sid: str, imgs: list[str]) -> None:
        """把用户最近发的图写进两个缓存，供图生图兜底读取。"""
        if not sid or not imgs:
            return
        # g_last_received：指令/工具兜底主用（最多 5 张，最近优先）
        rb = g_last_received.setdefault(sid, [])
        for p in imgs:
            if p not in rb:
                rb.append(p)
        if len(rb) > 5:
            g_last_received[sid] = rb[-5:]
        # g_recent_user_images：更宽松的历史滚动（最多 12 张）
        hb = g_recent_user_images.setdefault(sid, [])
        for p in imgs:
            if p not in hb:
                hb.append(p)
        if len(hb) > 12:
            g_recent_user_images[sid] = hb[-12:]

    # 每条用户消息（含纯指令，如 /draw /img2img /画）进入 handler 执行前，先用高优先级
    # 提前缓存消息内/引用图片。这样即使后续指令只拿到纯文本（图片被剥离/引用未回填），
    # 也能回溯到"用户最近发的图"做图生图兜底。
    # 说明：AstrBot 的 on_agent_begin / on_using_llm_tool 只在消息进入 LLM Agent 流程时触发，
    # 纯指令（command/regex 命中并 stop_event）不会进 Agent，所以必须用 event_message_type(ALL)
    # 且给高 priority 保证它在 command handler 之前执行（handlers 按 -priority 降序）。
    @filter.event_message_type(filter.EventMessageType.ALL, priority=20)
    async def _capture_user_images_on_message(self, event: AstrMessageEvent):
        try:
            sender = getattr(event, "get_sender_id", lambda: "")()
            if not sender:
                return
            sid = getattr(event, "session_id", "") or ""
            if not sid:
                return
            imgs = await self._collect_user_images(event)
            self._record_user_images(sid, imgs)
            if imgs:
                logger.debug(f"【取图】 消息前置缓存 {len(imgs)} 张: {imgs}")
        except Exception as e:
            logger.debug(f"【取图】 消息前置捕获图片失败（忽略）: {e}")

    def _own_command_names(self) -> list[str]:
        """本插件注册的所有指令名与别名（含中文，如 绘图状态 / 图库 / 萌绘）。"""
        self._collect_own_triggers()
        return self._ack_cmd_names_cache

    def _own_regex_patterns(self) -> list:
        """本插件注册的正则触发条件，如「画 / 绘图 / 生图」系指令的 RegexFilter。"""
        self._collect_own_triggers()
        return self._ack_regex_cache

    def _collect_own_triggers(self) -> None:
        """从 AstrBot 的 handler 注册表里读出本插件注册的全部触发条件：
        CommandFilter 的指令名+别名，以及 RegexFilter 的正则。

        动态读取而不是手写一份名单：以后新增或改名指令会自动纳入已读回执，
        不会再漏掉中文指令，也不需要给每个 handler 各写一遍回执代码。
        """
        if (
            getattr(self, "_ack_cmd_names_cache", None) is not None
            and getattr(self, "_ack_regex_cache", None) is not None
        ):
            return
        names: list[str] = []
        pats: list = []
        try:
            from astrbot.core.star.filter.command import CommandFilter
            from astrbot.core.star.filter.regex import RegexFilter
            from astrbot.core.star.star_handler import star_handlers_registry

            my_module = type(self).__module__
            cls_prefix = f"{type(self).__name__}."
            for md in list(star_handlers_registry):
                try:
                    h = getattr(md, "handler", None)
                    if h is None:
                        continue
                    # 只认本插件注册的 handler：同模块，或本插件类的方法
                    same_module = getattr(md, "handler_module_path", "") == my_module
                    if not same_module and not str(
                        getattr(h, "__qualname__", "")
                    ).startswith(cls_prefix):
                        continue
                    for f in getattr(md, "event_filters", []) or []:
                        if isinstance(f, CommandFilter):
                            names.extend(f.get_complete_command_names())
                        elif isinstance(f, RegexFilter):
                            if getattr(f, "regex", None) is not None:
                                pats.append(f.regex)
                except Exception:
                    continue
        except Exception as _e:
            logger.debug(f"【绘图·已读】 收集本插件触发条件失败（可忽略）: {_e}")
        # 长名优先匹配，避免短指令抢先（如「绘图」与「绘图统计」）
        names = sorted({n for n in names if n}, key=len, reverse=True)
        self._ack_cmd_names_cache = names
        self._ack_regex_cache = pats
        if names or pats:
            logger.info(
                f"【绘图·已读】 已读回执覆盖 {len(names)} 个指令、{len(pats)} 个正则触发"
            )

    # 已读回执：本插件任意指令（含中文，如 /绘图状态 /图库 /萌绘 /图生图 /绘图统计）到达时，
    # 在指令 handler 执行之前先给原消息贴个表情，让用户知道「收到了、在处理」。
    # 统一在这里做而不是每个 handler 各写一遍：新增指令自动生效，也不会漏掉中文指令。
    # priority 需高于指令 handler（默认 0），保证在指令执行前贴表情。
    async def _react_ack(self, event: AstrMessageEvent) -> None:
        """已读回执：优先贴平台原生「表情回应」，绝不下发一条新消息。

        AstrBot 的 `AstrMessageEvent.react()` 默认实现是「发送一条包含该表情的
        消息」（见 astr_message_event.py），而 aiocqhttp/QQ 平台并未重写它，
        因此在 QQ 上会变成往聊天里发一条 👀 消息，而不是在原消息下方贴表情。
        这里对 aiocqhttp 直接调用 OneBot 扩展 API `set_msg_emoji_like`
        （Lagrange / NapCat 等实现支持）做真正的表情回应；
        该 API 不可用时只记日志并跳过，绝不回退成发送表情消息。
        """
        try:
            if not self._cfg("draw_ack_enabled", True):
                return
        except Exception:
            return

        emoji = self._cfg("draw_ack_emoji", "👀") or "👀"

        # 仅 aiocqhttp（QQ / OneBot）具备可直接调用 OneBot API 的 bot 客户端，走原生回应
        pname = ""
        try:
            pname = str(getattr(getattr(event, "platform_meta", None), "name", "") or "").lower()
        except Exception:
            pname = ""
        bot = getattr(event, "bot", None)
        msg_id = None
        try:
            msg_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        except Exception:
            msg_id = None

        if pname == "aiocqhttp" and bot is not None and msg_id is not None:
            try:
                emoji_id = str(self._cfg("draw_ack_emoji_id", 289) or 289).strip()
                # emoji_type 必须显式传入：部分 OneBot 实现（如 LLOneBot）在缺少该参数时
                # 会按 emoji_id 长度猜测表情类型，从而贴错表情。
                # 默认 auto：纯数字 -> "1"（QQ 表情编号），emoji 字符 -> "2"。
                emoji_type = _resolve_emoji_type(
                    self._cfg("draw_ack_emoji_type", "auto"), emoji_id
                )
                # 与 astrbot_plugin_parser.EmojiLikeArbiter 参数一致（不传 group_id）
                await _set_msg_emoji_like(bot, msg_id, emoji_id, emoji_type, True)
                logger.info(
                    f"【绘图·已读】 已贴表情回应: emoji_id={emoji_id} "
                    f"emoji_type={emoji_type} message_id={msg_id}"
                )
                return
            except Exception as _e:
                # 原生回应不可用：只记日志，绝不回退成发一条表情消息
                logger.debug(f"【绘图·已读】 QQ 原生表情回应不可用，已跳过: {_e}")
                return

        # 其它平台交给 AstrBot 的 react（Telegram / Lark / Discord 已实现原生回应）
        try:
            await event.react(emoji)
        except Exception as _e:
            logger.debug(f"【绘图·已读】 已读回执失败（可忽略）: {_e}")

    @filter.command("绘图表情")
    async def _cmd_try_emoji(
        self,
        event: AstrMessageEvent,
        emoji_id: str = "",
        emoji_type: str = "1",
    ):
        """【排障用】对当前这条消息贴指定编号的 QQ 原生表情回应，用来确认某个编号对应哪个表情。

        用法：/绘图表情 <编号> [类型]，例如 /绘图表情 289（类型默认 1 = QQ 经典表情）。
        """
        bot = getattr(event, "bot", None)
        msg_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if bot is None or msg_id is None:
            yield event.plain_result("当前平台不支持表情回应（需要 aiocqhttp / OneBot）。")
            return
        eid_raw = str(emoji_id or "").strip()
        if not eid_raw:
            yield event.plain_result(
                "用法：/绘图表情 <编号或emoji> [类型]，例如 /绘图表情 289 或 /绘图表情 👀"
            )
            return
        # 纯数字按「QQ 表情编号」（整数）传；非数字（emoji 字符）原样传
        try:
            eid = int(eid_raw)
        except ValueError:
            eid = eid_raw
        # 类型同样支持 auto：数字编号 -> "1"，emoji 字符 -> "2"
        etype = _resolve_emoji_type(emoji_type or "auto", eid_raw)
        try:
            await _set_msg_emoji_like(bot, msg_id, eid, etype, True)
        except Exception as _e:
            logger.warning(f"【绘图表情】 贴表情失败: {_e}")
            yield event.plain_result(f"贴表情失败：{_e}")
            return
        yield event.plain_result(f"已对这条消息贴表情：emoji_id={eid} emoji_type={etype}")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=25)
    async def _ack_command_received(self, event: AstrMessageEvent):
        try:
            if not self._cfg("draw_ack_enabled", True):
                return
            cmds = self._own_command_names()
            pats = self._own_regex_patterns()
            if not cmds and not pats:
                return
            raw = (event.get_message_str() or "").strip()
            if not raw:
                return
            hit = False
            # 指令（CommandFilter）：与 AstrBot 一致，只认「@机器人 / 唤醒词 / 私聊」触发的，
            # 普通闲聊里出现同名文字不误触。此时 WakingCheckStage 已剥掉唤醒前缀（如 /）。
            if cmds and getattr(event, "is_at_or_wake_command", False):
                msg = re.sub(r"\s+", " ", raw)
                # 「绘图表情」是排障指令，本身就是试贴表情，不要再叠加已读回执
                hit = any(
                    msg == c or msg.startswith(f"{c} ")
                    for c in cmds
                    if c != "绘图表情"
                )
            # 正则触发（RegexFilter，如「画 / 绘图 / 生图」系）：不受唤醒前缀制约，
            # 与 AstrBot 的 RegexFilter 一样对整条消息做 search。
            if not hit and pats:
                hit = any(p.search(raw) for p in pats)
            if not hit:
                return
            await self._react_ack(event)
        except Exception as _e:
            logger.debug(f"【绘图·已读】 指令已读回执失败（可忽略）: {_e}")

    # 在 Agent 开始运行（即用户本条消息进入 LLM 前，仅触发一次）时也捕获一次图片，
    # 写入 g_recent_user_images（按会话滚动），供 LLM 工具兜底使用。
    # 保留 on_agent_begin 版本以覆盖 AI 对话（非纯指令）场景。
    @filter.on_agent_begin()
    async def _capture_user_images(self, event: AstrMessageEvent, run_context):
        try:
            # 只处理用户消息（避免把 AI 自己/系统消息也算进来污染图源）
            sender = getattr(event, "get_sender_id", lambda: "")()
            if not sender:
                return
            sid = getattr(event, "session_id", "") or ""
            if not sid:
                return
            imgs = await self._collect_user_images(event)
            self._record_user_images(sid, imgs)
            if imgs:
                logger.debug(f"【取图】 Agent 前置缓存 {len(imgs)} 张: {imgs}")
        except Exception as e:
            logger.debug(f"【取图】 捕获用户消息图片失败（忽略）: {e}")

    # 在 LLM 工具被调用前捕获「完整」原始事件（含图片组件）。
    # 因为部分情况下工具回调收到的 event 图片可能已被 LLM 消费/剥离，
    # 这里提前存一份，并趁图片还在时把路径缓存下来，供图生图取图兜底使用。
    @filter.on_using_llm_tool()
    async def _capture_llm_event(
        self, event: AstrMessageEvent, tool=None, tool_args: dict | None = None
    ):
        self._last_event = event
        # 若本次调用的是画图/图库类工具，标记该会话处于「画图 agent run」，
        # 供 on_llm_response 把画图收尾总结那次的主对话 LLM 消耗一并计入 token 统计。
        # 同时记录画图那一刻 AstrBot 正在使用的 provider id，作为该主对话的模型名。
        try:
            sid = getattr(event, "session_id", "") or ""
            tool_name = (getattr(tool, "name", None) or "") if tool is not None else ""
            if sid and tool_name in DRAW_LLM_TOOLS:
                g_draw_agent_sessions[sid] = self._current_chat_provider_id()
        except Exception:
            pass
        # 趁事件里图片组件尚未被剥离，提前把图片本地路径缓存到「会话最近收到图片」，
        # 用于图生图兜底：当工具执行时图片已被平台压缩临时文件清理、event 只剩文本时，
        # 仍可退回使用本次对话用户实际发来的图。
        try:
            sid = getattr(event, "session_id", "") or ""
            if sid:
                cached = await self._extract_images(event)
                # 额外：专程解析「引用消息里的图」。工具调用时 event 的图片可能已被剥离，
                # 但用户「引用一条带图消息」时，该引用图不在当前消息体内、只挂在 Reply 上，
                # _extract_images 已会尝试回拉；这里再单独跑一次确保不漏（含 URL 兜底下载）。
                try:
                    quoted = await self._extract_quoted_images(event)
                    quoted = [await self._image_to_local_path(q) for q in quoted]
                    for q in quoted:
                        if q and q not in cached:
                            cached.append(q)
                except Exception as qe:
                    logger.debug(f"【取图】 缓存时解析引用图失败（忽略）: {qe}")
                cached = [p for p in cached if p and os.path.exists(p)]
                if cached:
                    bucket = g_last_received.setdefault(sid, [])
                    for p in cached:
                        if p not in bucket:
                            bucket.append(p)
                    if len(bucket) > 5:
                        g_last_received[sid] = bucket[-5:]
                    logger.debug(f"【取图】 已缓存会话最近收到图片 {len(bucket)} 张: {bucket}")
                else:
                    logger.debug("【取图】 缓存时未从消息/引用取到任何图")
        except Exception as e:
            logger.debug(f"【取图】 缓存会话图片失败（忽略）: {e}")

    # 记录「用户通过 LLM 对话触发画图」时，主对话 LLM 调用的 token 消耗。
    # 背景：用户说"画一张小女孩"进入 LLM Agent 流程，那次主对话调用发生在 AstrBot
    # 核心层，插件在工具回调里拿不到 usage，所以此前统计不到。这里借助 on_llm_response
    # 钩子（在 agent 结束、最终 LLM 响应时触发一次，携带 usage）补上：
    #   - 触发画图工具时 _capture_llm_event（on_using_llm_tool）已给会话打标记；
    #   - 到 agent 结束时，若该会话有画图标记，则把本次最终响应的 usage 计入
    #     scene=agent_draw（此时 response 为画图收尾总结那次，input 含完整上下文，
    #     是画图主对话消耗的大头；触发工具意图那次属中间调用，AstrBot 不回调，记不到）。
    #   - 若 on_llm_response 时 LLM 返回的工具调用命中画图工具（response.tools_call_name，
    #     覆盖个别 runner 在工具调用时也广播该事件的情况），同样记入。
    # 用 scene=agent_draw 区分，model 主对话取不到统一记空串。
    # ------------------------------------------------------------------ #
    # 剧情模式（被动记录，仅私聊）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _story_plain(content):
        """从 AstrBot 消息 content（str / 组件列表 / 对象）中提取纯文本。"""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for it in content:
                if isinstance(it, str):
                    parts.append(it)
                elif isinstance(it, dict):
                    t = it.get("text")
                    if not t and isinstance(it.get("data"), dict):
                        t = it.get("data", {}).get("text")
                    if t:
                        parts.append(str(t))
                else:
                    t = getattr(it, "text", None)
                    if t is None:
                        d = getattr(it, "data", None)
                        t = d.get("text") if isinstance(d, dict) else None
                    if t:
                        parts.append(str(t))
            return "\n".join(p for p in parts if p)
        return str(content)

    def _story_session_key(self, event) -> str:
        try:
            return event.get_sender_id() or ""
        except Exception:
            return ""

    @staticmethod
    def _story_norm(s: str) -> str:
        import re
        s = (s or "").strip()
        s = re.sub(r"\s+", "", s)
        return s.strip("。.!！?？…~～，,：:；;”\"')）(（“'")

    @staticmethod
    def _story_match(norm: str, kw: str) -> bool:
        kw = (kw or "").strip()
        if not kw:
            return False
        return norm == kw or norm.startswith(kw)

    def _story_maybe_link_image(self, event, sha, prompt, w, h, workflow):
        """若当前会话处于剧情模式，把生成的图关联到档案。"""
        if self.story is None or not sha:
            return
        key = self._story_session_key(event)
        sid = self._story_active.get(key)
        if not sid:
            return
        try:
            cap = self._cfg("story_mode", {}) or {}
            if not (cap.get("capture_images", True) if isinstance(cap, dict) else True):
                return
            self.story.link_image(
                sid, sha, prompt=prompt or "", width=w or 0, height=h or 0,
                workflow=workflow or "",
            )
        except Exception as e:
            logger.warning(f"[剧情] 关联图片失败: {e}")

    async def _story_send_as_bot(self, event, text="", images=None):
        """以 bot 正式回复发送剧情文本/图，并写入 AStrBot 会话历史。

        目的：私聊下插件直发不进 AStrBot 会话历史、其他插件识别不到。这里改用
        context.send_message 正式通道发送，并手动把消息写入 message_history_manager
        （role=bot），让剧情文字等效于「LLM 自己发出的回复」——后续 LLM 对话可引用，
        读取会话历史的插件（记忆/人设等）也能识别。
        """
        try:
            chain = MessageChain()
            if text:
                chain.chain.append(Plain(text))
            for _p in (images or []):
                if _p:
                    try:
                        chain.chain.append(Image(file=_p))
                    except Exception:
                        pass
            if not chain.chain:
                return
            try:
                await self.context.send_message(event.unified_msg_origin, chain)
            except Exception as _e1:
                logger.warning(f"[剧情] 主动发送失败（回退事件通道）: {_e1}")
                await event.send(chain)
            try:
                mhm = getattr(self.context, "message_history_manager", None)
                umo = getattr(event, "unified_msg_origin", "") or ""
                pid = (getattr(event, "get_platform_id", lambda: "")() or "").strip()
                if mhm is not None and umo and pid:
                    await mhm.insert_message_chain(
                        platform_id=pid, user_id=umo, message_chain=chain,
                        role="bot", max_messages=200,
                    )
            except Exception as _e2:
                logger.warning(f"[剧情] 写入会话历史失败: {_e2}")
        except Exception as _e:
            logger.warning(f"[剧情] 剧情发送失败: {_e}")
            try:
                await self._send(event, text)
            except Exception:
                pass

    async def _story_enter(self, event, key, cfg):
        # 白名单
        wl = [x.strip() for x in (cfg.get("whitelist_users") or "")
              .replace("\n", ",").split(",") if x.strip()]
        if wl and key not in wl:
            await self._send(event, "你暂无剧情模式权限～")
            return
        raw = (getattr(event, "message_str", "") or "").strip()
        parsed = self._story_parse_enter(raw, cfg)
        # 续写旧档案
        history = []
        theme = parsed["theme"]
        if parsed["resume_id"]:
            sess = self.story.get_session(parsed["resume_id"])
            if sess:
                for t in sess.get("turns", []):
                    if t.get("content"):
                        history.append((t["role"], t["content"]))
                theme = theme or sess.get("scene") or sess.get("characters") or sess.get("summary") or "续写之前的剧情"
            else:
                await self._send(event, f"没找到编号 {parsed['resume_id']} 的剧情档案，改为新开一段～")
        # 上下文进入：抓进入前最近对话
        if not history:
            ctx_n = max(0, int(cfg.get("context_turns", 10) or 10))
            history = list(self._recent_chat.get(key, []))[-ctx_n:] if ctx_n else []
        sid = self.story.create_session(
            session_key=key, user_id=key,
            user_name=(getattr(event, "get_sender_name", lambda: "")() or ""),
            platform=(getattr(event, "get_platform", lambda: "")() or ""),
            source="trigger",
            title=(theme or "自由剧情")[:60], scene=theme or "",
        )
        # 角色绑定：男主 = 用户（优先取昵称）；女主默认 = 现实模式（bot 本体，按当前对话自然创作），
        # 仅在显式指定女主名时才走名单/人设
        _user_name = (parsed.get("user_name") or (cfg.get("user_name") or "")).strip()
        if not _user_name:
            _user_name = (getattr(event, "get_sender_name", lambda: "")() or "").strip()
        _partner_names = parsed.get("partner_names") or [
            x.strip() for x in str(cfg.get("partner_name") or "").split(",") if x.strip()
        ]
        _partner_profiles = self._story_parse_partner_profiles(cfg.get("partner_profile") or "")
        ctrl = {
            "sid": sid,
            "event": event,
            "cfg": cfg,
            "ask": parsed["ask"],
            "pause_on_options": bool(cfg.get("pause_on_options", False)),
            "theme": theme,
            "user_name": _user_name,
            "partner_names": _partner_names,
            "partner_profiles": _partner_profiles,
            "no_partner": bool(parsed.get("no_partner") or cfg.get("no_partner", False)),
            "chapter_steps": int(cfg.get("chapter_steps", 0) or 0),
            "auto_max": int(cfg.get("auto_steps_per_run", 12) or 12),
            "min_steps": int(cfg.get("min_steps", 3) or 3),
            "interval": float(cfg.get("loop_interval_sec", 1.5) or 1.5),
            "image_every": int(cfg.get("image_every", 3) or 3),
            "image_strategy": (cfg.get("image_strategy") or "大模型自判").strip(),
            "image_prob": max(0.0, min(1.0, float(cfg.get("image_prob", 0.4) or 0.4))),
            "last_drew_step": -1,
            "last_narr": "",
            "history": history,
            "chapter": 1,
            "step_in_chapter": 0,
            "total_step": 0,
            "paused": False,
            "stop": asyncio.Event(),
            "interrupt": asyncio.Queue(),
        }
        self._story_active[key] = sid
        self._story_control[key] = ctrl
        hint = "已进入剧情推演模式：我会自动按步骤推进剧情、每步发文本并根据情景配图（头尾必发）"
        if ctrl["ask"]:
            hint += "，关键节点会给你几个选项"
        hint += "。你可以随时发消息打断/改变方向，说「停」结束，说「继续」让我接着推。"
        if not ctrl["ask"]:
            hint += "（本次已设为「你推进别问我」）"
        hint += " 这段时间对话与配图都会悄悄存档哦～"
        await self._send(event, hint)
        ctrl["task"] = asyncio.create_task(self._story_run_loop(key))

    def _story_parse_enter(self, raw, cfg):
        """解析进入指令：提取主题/模板/续写/是否询问。"""
        import re
        rest = raw
        for kw in [k.strip() for k in (cfg.get("trigger_keywords") or "").split(",") if k.strip()]:
            if rest.startswith(kw):
                rest = rest[len(kw):].strip()
                break
        out = {"theme": "", "ask": bool((cfg or {}).get("ask_default", True)), "resume_id": 0}
        if any(x in rest for x in ("别问", "不用问", "你推进", "你决定", "别问我", "自己推")):
            out["ask"] = False
        # 无女主模式：进入剧情模式 无女主（不设恋爱线，专注主线）
        if re.search(r"无女主|不要女主|无女角|不谈恋爱|纯剧情", rest):
            out["no_partner"] = True
            rest = re.sub(r"无女主|不要女主|无女角|不谈恋爱|纯剧情", "", rest).strip()
        # 可选指定女主（可多个，逗号/顿号分隔）：女主:林晚晴、苏月
        _m = re.search(r"(女主|女角|伴侣)\s*[：:是=]\s*([^\s。！？!?]{1,40})", rest)
        if _m:
            out["partner_names"] = [x.strip() for x in re.split(r"[,，、/;；]", _m.group(2).strip()) if x.strip()]
            rest = rest.replace(_m.group(0), "", 1).strip()
        # 可选指定主角：主角:阿明（默认用户本人为男主）
        _m = re.search(r"(主角|男主|自己)\s*[：:是=]\s*([^\s，,。！？!?]{1,20})", rest)
        if _m:
            out["user_name"] = _m.group(2).strip()
            rest = rest.replace(_m.group(0), "", 1).strip()
        m = re.search(r"(续写|继续)\s*(\d+)", rest)
        if m:
            out["resume_id"] = int(m.group(2))
            rest = rest.replace(m.group(0), "").strip()
        theme = rest.strip("：: ，,。.　 ").strip()
        if theme:
            tmpl = self._story_match_template(theme, cfg)
            out["theme"] = tmpl if tmpl else theme
        return out

    @staticmethod
    def _story_parse_partner_profiles(text):
        """解析女主人设配置（每行 名::人设）为字典 {名字: 人设}。"""
        out = {}
        if not text:
            return out
        for line in str(text).splitlines():
            line = line.strip()
            if not line or "::" not in line:
                continue
            nm, prof = line.split("::", 1)
            nm = nm.strip()
            if nm:
                out[nm] = prof.strip()
        return out

    def _story_match_template(self, name, cfg):
        txt = (cfg.get("templates") or "").strip()
        if not txt:
            return ""
        for line in txt.splitlines():
            line = line.strip()
            if not line or "::" not in line:
                continue
            nm, premise = line.split("::", 1)
            if nm.strip() == name or name in nm or nm in name:
                return premise.strip()
        return ""

    async def _story_run_loop(self, key):
        ctrl = self._story_control.get(key)
        if ctrl is None:
            return
        event = ctrl["event"]
        sid = ctrl["sid"]
        try:
            while not ctrl["stop"].is_set():
                instr = await self._story_next_instruction(ctrl)
                if instr == "STOP":
                    break
                if isinstance(instr, str):
                    # 用户新方向：注入并开新章（步数重置，Z 方案）
                    ctrl["history"].append(("user", instr))
                    try:
                        self.story.append_turn(sid, "user", instr)
                    except Exception:
                        pass
                    ctrl["chapter"] += 1
                    ctrl["step_in_chapter"] = 0
                    ctrl["paused"] = False
                if ctrl["chapter_steps"] and ctrl["step_in_chapter"] >= ctrl["chapter_steps"]:
                    ctrl["chapter"] += 1
                    ctrl["step_in_chapter"] = 0
                first = (ctrl["total_step"] == 0)
                prompt = self._story_build_prompt(ctrl, first=first)
                try:
                    resp = await self._story_call_llm(prompt)
                except Exception as e:
                    logger.warning(f"[剧情] LLM 调用失败: {e}")
                    await self._send(event, "（推演时 AI 调用出错，剧情暂停。说「继续」重试或「停」结束）")
                    ctrl["paused"] = True
                    break
                parsed = self._story_parse_step(resp)
                narr = parsed.get("narrative", "").strip()
                if not narr:
                    # 未达最短步数前不允许自然收尾：重试继续推进（防「刚开启就结束」）
                    if ctrl["total_step"] < ctrl.get("min_steps", 3):
                        ctrl["retry_empty_narr"] = True
                        ctrl["empty_retries"] = ctrl.get("empty_retries", 0) + 1
                        if ctrl.get("empty_retries", 0) >= 4:
                            await self._send(event, f"（AI 连续未能生成剧情内容，本次只推进了 {ctrl['total_step']} 步。说「继续」再试或「停」结束）")
                            ctrl["paused"] = True
                            break
                        await asyncio.sleep(0.5)
                        continue
                    # LLM 无新内容：开场失败直接结束；进行中则视为自然收束并暂停
                    if not first:
                        await self._send(event, "（剧情到这里暂时告一段落～说「继续」让我再展开，或「停」结束）")
                        ctrl["paused"] = True
                    break
                ctrl.pop("retry_empty_narr", None)
                ctrl["empty_retries"] = 0
                await self._story_send_as_bot(event, narr)
                ctrl["history"].append(("assistant", narr))
                ctrl["last_narr"] = narr
                try:
                    self.story.append_turn(sid, "assistant", narr)
                except Exception:
                    pass
                ctrl["total_step"] += 1
                ctrl["step_in_chapter"] += 1
                last = (parsed.get("chapter_end") or
                        (ctrl["chapter_steps"] and ctrl["step_in_chapter"] >= ctrl["chapter_steps"]))
                # 出图决策：头图 / 尾图(章节结束)始终必出；中间步骤按 image_strategy 选择
                draw_now = bool(first or last)
                if not draw_now:
                    mode = ctrl["image_strategy"]
                    if mode == "大模型自判":
                        draw_now = bool(parsed.get("draw")) and bool(parsed.get("prompt"))
                    elif mode == "固定步数间隔":
                        draw_now = bool(ctrl["image_every"]) and ctrl["total_step"] > 0 and ctrl["total_step"] % ctrl["image_every"] == 0
                    elif mode == "概率随机":
                        draw_now = random.random() < ctrl["image_prob"]
                if draw_now:
                    dprompt = parsed.get("prompt") or self._story_infer_prompt(narr)
                    if dprompt:
                        # 每步（bot 的一条推演消息）作为单轮出图闸门的新一轮：
                        # 剧情自动推演时 event 始终是最初「进入剧情」的那条，msg_fp 不变，
                        # 若不在此重置，整段推演会被算作一轮、per_run_max_calls 出第一张就关门
                        #（表现为「一下就没了」）。按 bot 消息条数计轮次，bot 每推进一步都能继续出图。
                        self._draw_run_reset(sid)
                        await self._story_draw_in_loop(event, sid, dprompt)
                        ctrl["last_drew_step"] = ctrl["total_step"]
                if ctrl["ask"] and parsed.get("options"):
                    opts = " / ".join(parsed["options"])
                    await self._send(event, f"你可以选择：{opts}（随时回复选项或发新指令都能改方向）")
                    if ctrl.get("pause_on_options"):
                        ctrl["paused"] = True
                _min_steps = ctrl.get("min_steps", 0) or 0
                if (ctrl["auto_max"] and ctrl["auto_max"] > 0 and ctrl["total_step"] >= ctrl["auto_max"]
                        and ctrl["total_step"] >= _min_steps):
                    await self._send(event, "（本次自动推演已达步数上限，说「继续」让我接着推，或「停」结束）")
                    ctrl["paused"] = True
                await asyncio.sleep(ctrl["interval"])
        finally:
            try:
                await self._story_finish(key)
            except Exception as e:
                logger.warning(f"[剧情] 收尾失败: {e}")

    async def _story_next_instruction(self, ctrl):
        """取一条用户指令：暂停态阻塞等待，自动态非阻塞取最近一条。"""
        if ctrl["paused"]:
            msg = await ctrl["interrupt"].get()
        else:
            try:
                msg = ctrl["interrupt"].get_nowait()
            except asyncio.QueueEmpty:
                return None
        return self._story_classify(msg)

    def _story_classify(self, msg):
        if not msg:
            return None
        norm = self._story_norm(msg)
        exit_kw = [k.strip() for k in (self._cfg("story_mode", {}) or {}).get("exit_keywords", "").split(",") if k.strip()]
        if any(self._story_match(norm, k) for k in exit_kw):
            return "STOP"
        if norm in ("继续", "接着", "continue", "go", "继续推"):
            return "CONTINUE"
        return msg.strip()

    async def _story_call_llm(self, prompt):
        prov = self.context.get_using_provider()
        if prov is None:
            return ""
        try:
            resp = await prov.text_chat(prompt=prompt, session_id="")
            return (getattr(resp, "completion_text", "") or "").strip()
        except Exception as e:
            logger.warning(f"[剧情] LLM 调用异常: {e}")
            return ""

    def _story_build_prompt(self, ctrl, first=False):
        theme = ctrl["theme"] or "自由发挥的剧情"
        ask = ctrl["ask"]
        _uname = ctrl.get("user_name") or "你"
        _names = ctrl.get("partner_names") or []
        if ctrl.get("no_partner"):
            role_line = (f"【角色设定·现实模式】男主 = 用户本人（称呼：{_uname}），你就是正与用户对话的 bot。"
                         "本段剧情【无女主】、不设恋爱线，专注把你们当前情境延续成主线/冒险故事，"
                         "绝不把用户写成路人，也不要另造主角或恋爱桥段。")
        elif _names:
            _profiles = ctrl.get("partner_profiles") or {}
            _parts = [f"{_n}（{_profiles.get(_n) or '由你按主题合理塑造，与男主互动自然'}）" for _n in _names]
            _label = "女主（多个）" if len(_names) > 1 else "女主"
            role_line = (f"【角色设定】男主 = 用户本人（称呼：{_uname}），你就是正与用户对话的 bot；"
                         f"{_label} = {'、'.join(_parts)}。剧情围绕你与他们的互动展开，多女主可发展各自支线。")
        else:
            # 默认现实模式：把「正在对话的两个人」写进故事，按当前对话语境续写
            role_line = (f"【角色设定·现实模式】你就是现在正与用户对话的 bot（剧情中的女主就是你本人，保持你在对话中展现的性格与语气）；"
                         f"男主就是正在和你聊天的用户本人（称呼：{_uname}）。"
                         "不要把用户当成路人，也不要另造主角。请把你们正在聊的情境（如一起出门游玩等）"
                         "自然写成一段两人的故事，像延续对话一样往下推进，第二人称视角。")
        lines = [
            "你就是正在与用户现实对话的 bot。用户进入「剧情模式」后，你基于你们当前对话的语境，把两个人自然写进故事并自动、连续地推进（每步发叙事+配图），全程不需要用户每句催促。",
            f"【世界观/主题】{theme}",
            role_line,
            f"【进度】第 {ctrl['chapter']} 章，本章第 {ctrl['step_in_chapter']} 步。",
            "【输出格式，严格按此，不要输出标签以外的内容、不要写解释或点评】：",
            "[NARRATIVE] 本步的叙事。硬性要求：①只推进「一个具体场景 / 一个动作 / 一段对话」，严禁在一步之内把整段剧情写完或草草收尾；"
            "②描写具体生动——写清环境、人物动作神态、心理活动与对话，避免流水账与概括性语言；③约 80-160 字、3-6 句；④结尾留悬念或钩子，为下一步铺垫。",
            "叙事采用「第二人称交互式」：以主角（你）的视角推进场景、描写女主言行与心理，把用户发来的消息视为主角的言行/选择，不要另起第三人称上帝视角。",
            "[DRAW] 本步对应的画面描述（动漫风，用英文 Danbooru 标签逗号分隔，如 1girl,solo,smile,outdoors；写实用中文场景）。"
            "头图（第一步）与每章结尾一定会出图；中间步骤由你判断——若本步情景值得配图就写 [DRAW] 并给出描述，不需要则写 [DRAW] 无（依剧情节奏而定，不要每步都出）。",
            "[OPTIONS] 仅在本次允许互动、且处于关键节点（章节结束或剧情重大抉择）时，给出 2-3 个简短后续分支；普通推进步骤写 [OPTIONS] 无，不要每步都给。",
            "[CHAPTER_END] true 表示本章完整收束（一个事件告一段落），否则 false。",
        ]
        if not ask:
            lines.append("本次为「你推进别问我」模式：不要输出 [OPTIONS]。")
        lines.append("收到用户新指令即视为改变剧情方向，顺势改写后续。")
        sys_prompt = "\n".join(lines)
        hist = "\n".join(f"{'用户' if r == 'user' else '助手'}: {t}" for r, t in ctrl["history"][-30:])
        if first:
            stage = "这是剧情开场，请写出第一段场景与画面（头尾必发，第一步必须给 [DRAW]）。"
        elif ctrl.get("retry_empty_narr"):
            stage = "上一步你没有输出有效的剧情正文（[NARRATIVE] 为空）。现在必须推进剧情：写出新的一段 80-160 字的叙事正文，不要留空、不要重复已有内容。"
        elif ctrl["paused"]:
            stage = "刚才用户给了新指令/选项，请基于最近输入继续推进下一步。"
        else:
            stage = "请推进下一步剧情。"
        return f"{sys_prompt}\n\n已有剧情（最近）：\n{hist}\n\n[指令] {stage}\n\n请按格式输出："

    @staticmethod
    def _story_parse_step(text):
        import re
        out = {"narrative": "", "draw": False, "prompt": "", "options": [], "chapter_end": False}
        if not text:
            return out

        def grab(tag):
            m = re.search(rf"\[{tag}\]\s*(.*?)(?=\n\[[A-Z_]+\]|$)", text, re.S)
            return m.group(1).strip() if m else ""
        out["narrative"] = grab("NARRATIVE")
        draw = grab("DRAW")
        if draw and draw.lower() not in ("无", "no", "none", "false", ""):
            out["draw"] = True
            out["prompt"] = draw
        opts = grab("OPTIONS")
        if opts and opts.lower() not in ("无", "no", "none", "false"):
            for line in opts.splitlines():
                line = line.strip().lstrip("ABCDEF.、-)").strip()
                if line:
                    out["options"].append(line)
        ce = grab("CHAPTER_END").lower()
        out["chapter_end"] = ce in ("true", "1", "是", "yes")
        return out

    @staticmethod
    def _story_infer_prompt(narr):
        if not narr:
            return ""
        return narr[:200]

    async def _story_draw_in_loop(self, event, sid, prompt):
        try:
            async for node, p in self._do_draw(
                event, None, prompt, "", None, None, None, None, None,
                is_img2img=False, notify_pending=True, source="story",
            ):
                if node is not None:
                    try:
                        _paths = []
                        _chain = node.chain if isinstance(node, MessageChain) else getattr(node, "chain", None)
                        if _chain:
                            for _c in _chain:
                                if isinstance(_c, Image) and (_c.file or _c.url):
                                    _paths.append(_c.file or _c.url)
                        if _paths:
                            await self._story_send_as_bot(event, images=_paths)
                        else:
                            await event.send(node if isinstance(node, MessageChain) else MessageChain([node]))
                    except Exception as e:
                        logger.warning(f"[剧情] 出图发送失败: {e}")
        except Exception as e:
            logger.warning(f"[剧情] 推演出图失败: {e}")
        # 关联档案由 _do_draw 内部 _story_maybe_link_image（active 时）自动完成

    async def _story_finish(self, key):
        ctrl = self._story_control.pop(key, None)
        if ctrl is None:
            return
        sid = ctrl["sid"]
        cfg = ctrl["cfg"]
        event = ctrl["event"]
        summary = ""
        try:
            if cfg.get("auto_summary", True):
                summary = await self._story_make_summary(sid)
        except Exception:
            pass
        try:
            self.story.finish_session(sid, summary=summary)
        except Exception:
            pass
        self._story_active.pop(key, None)
        # 尾图：整段结束若最后一步尚未出图，补一张（基于最后叙事/主题），保证「尾必出」
        try:
            if (ctrl.get("last_drew_step", -1) < ctrl.get("total_step", 0)
                    and ctrl.get("total_step", 0) > 0):
                ln = ctrl.get("last_narr", "") or ctrl.get("theme", "")
                if ln:
                    await self._story_draw_in_loop(event, sid, ln)
        except Exception as _te:
            logger.warning(f"[剧情] 尾图生成失败（忽略）: {_te}")
        msg = "已退出剧情模式。"
        if summary:
            msg += f"\n\n📝 本次剧情摘要：\n{summary}"
        try:
            await self._send(event, msg)
        except Exception:
            pass

    async def _story_exit(self, event, key, cfg):
        # 兼容旧调用：直接结束推演循环
        ctrl = self._story_control.get(key)
        if ctrl is not None:
            ctrl["stop"].set()
            try:
                ctrl["interrupt"].put_nowait("")
            except Exception:
                pass
            return
        sid = self._story_active.pop(key, None)
        if sid is None:
            return
        try:
            self.story.finish_session(sid)
        except Exception:
            pass
        await self._send(event, "已退出剧情模式。")

    async def _story_make_summary(self, sid: int) -> str:
        sess = self.story.get_session(sid)
        if not sess:
            return ""
        turns = sess.get("turns", [])
        conv = "\n".join(
            f"{'用户' if t['role'] == 'user' else '助手'}: {t['content']}"
            for t in turns if t.get("content")
        )
        if not conv:
            return ""
        prompt = (
            "请用 100 字以内，把下面这段角色扮演/剧情对话浓缩成一段客观摘要"
            "（只复述发生了什么，不要评价、不要使用 Markdown）：\n\n" + conv
        )
        try:
            prov = self.context.get_using_provider()
        except Exception:
            prov = None
        if prov is None:
            return ""
        try:
            resp = await prov.text_chat(prompt=prompt, session_id="")
            return (getattr(resp, "completion_text", "") or "").strip()
        except Exception as e:
            logger.warning(f"[剧情] LLM 摘要调用失败: {e}")
            return ""

    @filter.event_message_type(filter.EventMessageType.ALL, priority=25)
    async def _on_story_message(self, event: AstrMessageEvent):
        """剧情推演入口：仅私聊。进行中把消息交给推演循环（打断/改向/停），
        未进入时缓存最近对话供「上下文进入」使用。"""
        if self.story is None:
            return
        cfg = self._cfg("story_mode", {}) or {}
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            return
        try:
            gid = event.get_group_id()
        except Exception:
            gid = None
        if gid:
            return
        raw = (getattr(event, "message_str", "") or "").strip()
        if not raw:
            return
        key = self._story_session_key(event)
        if not key:
            return
        norm = self._story_norm(raw)
        exit_kw = [k.strip() for k in (cfg.get("exit_keywords") or "").split(",") if k.strip()]
        enter_kw = [k.strip() for k in (cfg.get("trigger_keywords") or "").split(",") if k.strip()]
        ctrl = self._story_control.get(key)
        if ctrl is not None:
            # 剧情进行中：消息交给推演循环处理
            if any(self._story_match(norm, k) for k in exit_kw):
                ctrl["stop"].set()
            try:
                ctrl["interrupt"].put_nowait(raw)
            except Exception:
                pass
            event.stop_event()
            return
        # 进入
        for kw in enter_kw:
            if self._story_match(norm, kw):
                await self._story_enter(event, key, cfg)
                event.stop_event()
                return
        # 未进入：缓存最近对话（上下文进入用）
        buf = self._recent_chat.get(key)
        if buf is None:
            buf = []
            self._recent_chat[key] = buf
        buf.append(("user", raw))
        ctx_n = max(2, int(cfg.get("context_turns", 10) or 10) * 2)
        if len(buf) > ctx_n:
            del buf[:len(buf) - ctx_n]

    @filter.on_llm_response()
    async def _on_story_llm_response(self, event: AstrMessageEvent, response=None) -> None:
        """剧情 active 时助手回复由推演循环自行记录；非 active 时把助手回复
        缓存进最近对话，供「上下文进入」使用。"""
        if self.story is None:
            return
        key = self._story_session_key(event)
        if not key:
            return
        if key in self._story_active:
            return
        buf = self._recent_chat.get(key)
        if buf is None:
            return
        try:
            msgs = list(event.get_messages())
            text = ""
            for m in reversed(msgs):
                role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
                if role == "assistant":
                    content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                    text = self._story_plain(content)
                    break
            if text:
                buf.append(("assistant", text))
                ctx_n = max(2, int((self._cfg("story_mode", {}) or {}).get("context_turns", 10) or 10) * 2)
                if len(buf) > ctx_n:
                    del buf[:len(buf) - ctx_n]
        except Exception:
            pass

    @filter.on_llm_response()
    async def _record_agent_draw_tokens(self, event: AstrMessageEvent, response=None) -> None:
        if self.token_store is None or not self._cfg("llm_token_stats", True):
            return
        try:
            sid = getattr(event, "session_id", "") or ""
            tool_names = (getattr(response, "tools_call_name", None) or []) if response is not None else []
            hit_draw = any((n or "") in DRAW_LLM_TOOLS for n in tool_names)
            in_draw = bool(sid and sid in g_draw_agent_sessions)
            if not hit_draw and not in_draw:
                return  # 与画图无关的普通对话，不记录
            if hit_draw and sid and sid not in g_draw_agent_sessions:
                g_draw_agent_sessions[sid] = self._current_chat_provider_id()
            # 主对话模型：优先用画图工具调用时缓存的 provider id；
            # 未缓存（仅靠 tools_call_name 判定命中的极少数情况）则回退当前 provider。
            model = g_draw_agent_sessions.get(sid, "") or self._current_chat_provider_id()
            self._record_llm_token("agent_draw", model, response, event)
        except Exception as e:
            logger.warning(f"【统计·token】 记录画图主对话用量失败: {e}")

    def _current_chat_provider_id(self) -> str:
        """获取 AstrBot 当前正在使用的对话 provider id；取不到返回空串。"""
        try:
            prov = self.context.get_using_provider()
        except Exception:
            return ""
        if prov is None:
            return ""
        cfg = getattr(prov, "provider_config", None) or {}
        return cfg.get("id") if isinstance(cfg, dict) else ""

    # 画图 agent run 结束，清除会话标记，避免后续普通对话被误计入 token 统计。
    @filter.on_agent_done()
    async def _clear_draw_agent_mark(self, event: AstrMessageEvent, run_context=None, response=None) -> None:
        try:
            sid = getattr(event, "session_id", "") or ""
            if sid:
                g_draw_agent_sessions.pop(sid, None)
                # 一轮 agent run 结束：清除单轮出图闸门状态，确保用户下一条新消息
                # 可以正常继续画图（不会被上一轮的计数/同参记录误拦）。
                self._draw_run_reset(sid)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # LLM 工具：comfyui_gallery（图库检索与语义标签召回）
    # ------------------------------------------------------------------ #
    @filter.llm_tool(name="comfyui_gallery")
    async def llm_gallery(
        self,
        event: AstrMessageEvent,
        mode: str = "recall",
        keyword: str = "",
        tag: str = "",
        limit: int = 10,
        source: str = "",
    ):
        """在图片画廊里检索、召回（发图）或收藏图片。与 comfyui_draw 职责分离：
        本工具只负责「发以前的图 / 找某类图 / 收藏这张」，绝不生成新图。

        什么时候用本工具（而不是 comfyui_draw）：
        - 用户要的是已存在的图：如「把我们的合照发我」「上次那张图再发一次」
          「把收藏的猫图发我」「找一张海边的图」。
        - 用户要收藏/打标签：如「这张是我们的合照，以后找你要就发这张」
          「收藏这张图，标签叫合照」。

        什么时候不要用本工具：
        - 用户想新画一张图（任何「画/生成/来张图」意图）→ 用 comfyui_draw。
        - 本工具不会生成新图，强行用于生图意图会失败。
        - ★★生图失败/没画出来时，【禁止】用本工具翻旧图顶替：用户要的是新画出来的图，
          画不出来就该如实告诉他「这次没能画出来，稍后再试」，
          绝不能从图库里抓几张不相干的旧图发出去冒充新图（这比直接说失败更糟）。

        Args:
            mode(string): 操作模式，必填其一：
                - "recall"：按语义标签召回并发图（如 tag="合照"）。命中多张时返回带编号的列表，
                  请告诉用户「回复编号即可发对应那张」，或继续调用本工具 mode="send" 指定序号。
                - "search"：按提示词关键词检索（keyword 参数，如 keyword="猫"）。
                - "save"：收藏当前/上一条消息里的图（用户发来的照片或刚生成的图），
                  并可顺带打标签（tag 参数，多个标签用空格分隔）。
                - "send"：直接发某张图（配合 keyword 传序号字符串，如 "3"；或传 sha 前几位）。
                - "list"：列出最近图片。
                - "stats"：图库统计。
                - "tag"：给某张图打标签（keyword 传序号或 sha 前几位，tag 传标签，多个空格分隔）。
                - "public"：把某张图设为公开（keyword 传序号或 sha 前几位），公开后其他用户也能检索到。
                - "private"：把某张图设为私有（keyword 传序号或 sha 前几位），仅自己可见。
            keyword(string): search 模式下的提示词关键词；send/tag/public/private 模式下传序号或 sha 前几位。
            tag(string): recall/tag/save 模式下的语义标签（如「合照」）。可含空格，多个标签用空格分隔（tag/save 时）。
            limit(int): 返回数量上限，默认 10。

        注意：发图由插件在本地完成，模型不会、也不需要接触任何文件路径。图片默认私有，
        仅本人可见；设为公开后其他用户也能检索/发送。
        """
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if plugin is None:
            plugin = self
        if plugin.gallery is None:
            return "图库未启用或初始化失败，无法检索/收藏图片。"
        g = plugin.gallery
        # 会话范围：comfyui_gallery 是「个人语义图库助手」，按用户(owner)隔离即可，
        # 始终跨会话召回/发送——支持「在 A 群存的图，在 B 群说『把初音未来那张发我』」的跨群取图。
        # （与 /图库 指令的「仅本群」视图区分：指令场景保留 session 限制，LLM 语义召回放开。）
        # 用户隔离：始终按当前用户过滤，避免把别人的图发给当前用户。
        session = None
        owner = getattr(event, "get_sender_id", lambda: "")() or ""

        if mode == "recall":
            if not tag or not tag.strip():
                return "recall 模式需要 tag 参数（语义标签，如「合照」）。"
            rows = g.recall_by_tag(tag.strip(), limit=limit, owner=owner)
            if not rows:
                return f"图库里没有带「{tag.strip()}」标签的图。可先用 /图库 保存 或对话里说「收藏这张，标签叫XX」来打标签。"
            if len(rows) == 1:
                ok = await plugin._gallery_send_image(event, rows[0]["sha256"], owner=owner)
                return ("已发送该图。" if ok else "找到图但发送失败。")
            # 多张：只发「最相关/最常用」的一张（rows[0]），避免一次性刷屏。
            # 同时告知总数并引导用户：加关键词缩小范围，或回复编号选其他张。
            ok = await plugin._gallery_send_image(event, rows[0]["sha256"], owner=owner)
            if not ok:
                return f"带「{tag.strip()}」的图有 {len(rows)} 张，但发送失败。"
            _gno0 = rows[0].get("gidx", 1)
            lines = [
                f"带「{tag.strip()}」的图有 {len(rows)} 张，先发最相关的一张（编号 {_gno0}）。",
                "若不是你要的那张，可：① 加关键词再搜（如「初音未来 烟花」）缩小范围；② 回复其他编号直接发对应那张。",
            ]
            return "\n".join(lines)

        elif mode == "search":
            if not keyword or not keyword.strip():
                # 默认模型未填 keyword 时，用「指定模型」(llm_model) 从用户原话提取
                user_text = (getattr(event, "message_str", "") or "").strip()
                if user_text:
                    extracted = await plugin._llm_extract_args(
                        user_text,
                        "mode(string): 固定为 search。\n"
                        "keyword(string): 提示词关键词（必填，如「猫」「海边」）。\n"
                        "limit(int): 返回数量上限，可选。",
                    )
                    if extracted and extracted.get("keyword"):
                        keyword = str(extracted["keyword"]).strip()
                if not keyword or not keyword.strip():
                    return "search 模式需要 keyword 参数（提示词关键词）。"
            rows = g.search(keyword=keyword.strip(), limit=limit, session=session, owner=owner)
            if not rows:
                return f"没找到含「{keyword.strip()}」的图。"
            if len(rows) == 1:
                ok = await plugin._gallery_send_image(event, rows[0]["sha256"], owner=owner)
                return ("已发送该图。" if ok else "找到图但发送失败。")
            # 多张：只发「最新」的一张（rows[0] 按 created_at DESC），避免一次性刷屏。
            # 引导用户加关键词缩小范围，或回复编号选其他张。
            ok = await plugin._gallery_send_image(event, rows[0]["sha256"], owner=owner)
            if not ok:
                return f"没找到含「{keyword.strip()}」的图可发送。"
            _gno0 = rows[0].get("gidx", 1)
            lines = [
                f"检索「{keyword.strip()}」的结果有 {len(rows)} 张，先发最新的一张（编号 {_gno0}）。",
                "若不是你要的那张，可：① 加关键词再搜（如「初音未来 烟花」）缩小范围；② 回复其他编号直接发对应那张。",
            ]
            return "\n".join(lines)

        elif mode == "save":
            p = await plugin._gallery_resolve_ref(event)
            if not p:
                return "没有可收藏的图（当前/上条消息没有图，本会话也没生成过图）。"
            tags = tag.split() if tag else []
            uname = getattr(event, "get_sender_name", lambda: "")() or ""
            sha = g.archive_user_image(p, tags=tags, user_id=owner, user_name=uname, session_id=(getattr(event, "session_id", "") or ""))
            if not sha:
                return "收藏失败（图库可能未启用）。"
            extra = (" 标签：" + "、".join(tags)) if tags else ""
            return f"已收藏这张图{extra}。以后说「把{'/'.join(tags) if tags else '这张'}发我」即可召回。"

        elif mode == "send":
            # 本轮生图已经失败过 / 被闸门拦过时，禁止从图库翻旧图顶替。
            # 用户要的是「新画出来的图」；画不出来就该如实告知，
            # 而不是从库存里抓几张不相干的旧图冒充（实测模型就这么干过，
            # 最后跟用户说"发过去的都是之前的库存"）。
            # 用户本来就要旧图时不会先去生图，此时 blocked/fail 都是 0，不受影响。
            _run_st = plugin._draw_run_state_of(event)
            if int(_run_st.get("blocked", 0) or 0) > 0 or int(_run_st.get("fail", 0) or 0) > 0:
                logger.info("【工具·gallery】 本轮生图失败/被拦过，拒绝用图库旧图顶替发图")
                return (
                    "本轮出图没有成功，不要用图库里的旧图顶替。"
                    "用户要的是新生成的图，请如实告诉用户这次没能画出来、可以稍后再试。"
                )
            arg = (keyword or "").strip()
            if not arg:
                return "send 模式需要 keyword 参数传序号（如「3」，可多张用逗号或空格隔开「1,2,3」）或 sha 前几位。"
            targets = plugin._parse_gallery_targets([arg])

            def _is_sha_like(s: str) -> bool:
                # 疑似完整/前缀 sha256：纯十六进制且长度足够
                return bool(s) and all(c in "0123456789abcdefABCDEF" for c in s) and len(s) >= 8

            # 语义召回兜底：当目标全是非编号、非 sha 前缀的语义词（典型场景是 LLM 直接把
            # 标签「小叽睡裙」当 keyword 传给 send 模式），不要再拿它去匹配 sha256 前缀
            # （必然查不到 → 工具反复失败 → LLM 误判未完成任务而重试刷屏），而是按标签/关键词
            # 召回后发最相关的一张。
            if targets and all((not t.isdigit() and not _is_sha_like(t)) for t in targets):
                acc = []
                for t in targets:
                    rs = g.recall_by_tag(t, limit=limit, owner=owner)
                    if not rs:
                        rs = g.search(keyword=t, limit=limit, session=session, owner=owner)
                    acc.extend(rs)
                seen = set(); uniq = []
                for r in acc:
                    s = r.get("sha256")
                    if s and s not in seen:
                        seen.add(s); uniq.append(r)
                if not uniq:
                    return f"没找到带「{arg}」标签或含「{arg}」的图。"
                ok = await plugin._gallery_send_image(event, uniq[0]["sha256"], owner=owner)
                if len(uniq) == 1:
                    return ("已发送该图。" if ok else "找到图但发送失败。")
                _gno0 = uniq[0].get("gidx", 1)
                return (f"带「{arg}」的图有 {len(uniq)} 张，先发最相关的一张（编号 {_gno0}）。"
                        "若不是你要的那张，可回复其他编号直接发对应那张。") if ok else f"找到「{arg}」的图但发送失败。"

            sent_ok, sent_fail = 0, 0
            for t in targets:
                if t.isdigit():
                    r = g.get_by_global_no(int(t), owner=owner, session=session)
                    if r:
                        ok = await plugin._gallery_send_image(event, r["sha256"], owner=owner)
                        sent_ok += 1 if ok else 0
                        sent_fail += 0 if ok else 1
                    else:
                        sent_fail += 1
                elif _is_sha_like(t):
                    ok = await plugin._gallery_send_image(event, t, owner=owner)
                    sent_ok += 1 if ok else 0
                    sent_fail += 0 if ok else 1
                # 其余语义 token 已在上面的语义召回兜底分支处理，这里跳过避免当 sha 误查
            if len(targets) > 1:
                return f"已发送 {sent_ok} 张，失败/跳过 {sent_fail} 张。"
            return ("已发送。" if sent_ok else "没找到这张图/发送失败。")

        elif mode == "list":
            rows = g.search(limit=limit, session=session, owner=owner)
            if not rows:
                return "画廊还是空的～先画点图或收藏点图吧。"
            lines = ["最近的图片（回复编号即可发图）："]
            for i, r in enumerate(rows, 1):
                _tags = (" #" + " #".join(r["tags"])) if r["tags"] else ""
                # 用「提示词摘要 + 时间」标识图片。原先只显示 source（值恒为 gen/ref/user
                # 之一），列表会变成一串毫无区分度的 "gen"，模型认不出哪张是哪张，
                # 只能盲选编号、把不相干的旧图发给用户。
                _desc = (r.get("prompt") or "").strip().replace("\n", " ")
                if len(_desc) > 40:
                    _desc = _desc[:40] + "…"
                if not _desc:
                    _desc = r.get("source") or "（无描述）"
                _when = ""
                try:
                    _ca = float(r.get("created_at") or 0)
                    if _ca > 0:
                        _when = " [" + time.strftime("%m-%d %H:%M", time.localtime(_ca)) + "]"
                except (TypeError, ValueError):
                    _when = ""
                lines.append(
                    f"{r.get('gidx', i)}. {'★' if r['starred'] else ''}{_desc}{_tags}{_when}"
                )
            return "\n".join(lines)

        elif mode == "stats":
            st = g.stats()
            return (
                f"图库：共 {st.get('total',0)} 张（生图{st.get('gen',0)}/参考{st.get('ref',0)}/"
                f"用户{st.get('user',0)}），收藏 {st.get('starred',0)}，带标签 {st.get('tagged',0)}；"
                f"有效占用 {st.get('size_mb',0)} MB / 上限 {st.get('max_total_mb',0)} MB"
            )

        elif mode in ("tag", "public", "private"):
            # 定位目标图（keyword 传序号或 sha 前几位；缺省时用最近生成的图）
            # 归属校验：只有图主/管理员能给别人图打标签、改可见性。
            arg = (keyword or "").strip()
            sha = None
            if arg and arg.isdigit():
                r = g.get_by_global_no(int(arg), owner=owner, session=session)
                if r:
                    _row = r
                else:
                    return "编号越界。"
                if not plugin._can_operate_image(event, _row, owner):
                    return "这张图是别人的，只有图片所有者和管理员可以操作。"
                sha = _row["sha256"]
            elif arg:
                row = g.get_by_sha(arg)
                if not row:
                    return "没找到这张图。"
                if not plugin._can_operate_image(event, row, owner):
                    return "这张图是别人的，只有图片所有者和管理员可以操作。"
                sha = row["sha256"]
            else:
                p = await plugin._gallery_resolve_ref(event)
                if p and os.path.exists(p):
                    _sha = g.sha_of(p)
                    if _sha:
                        row = g.get_by_sha(_sha)
                        if row and plugin._can_operate_image(event, row, owner):
                            sha = row["sha256"]
            if not sha:
                return "没找到要操作的那张图。可传序号或 sha 前几位；若引用图尚未入库请先 /图库 保存。"

            if mode == "tag":
                tags = (tag or "").strip().split()
                if not tags:
                    return "tag 模式需要 tag 参数（多个标签用空格分隔）。"
                g.add_tags(sha, tags)
                return f"已打标签：{'、'.join(tags)}。"
            # public / private
            is_pub = mode == "public"
            if g.set_visibility(sha, is_pub):
                return (
                    f"已设为{'公开' if is_pub else '私有'}。"
                    f"{'其他人现在也能检索到这张图了。' if is_pub else '只有你能看到这张图了。'}"
                )
            return "设置可见性失败。"
        else:
            return "未知 mode。可用：recall / search / save / send / list / stats / tag / public / private。"

    # LLM 工具：comfyui_loras（查询 LoRA 库）
    # ------------------------------------------------------------------ #
    @filter.llm_tool(name="comfyui_loras")
    async def llm_loras(
        self,
        event: AstrMessageEvent,
        base_model: str = "",
        keyword: str = "",
        category: str = "",
    ):
        """查询已配置的 LoRA 库，包括每个 LoRA 的名称、别名、分类、底模、描述与触发词。

        触发时机：当用户提到要用某种风格/画风/人物/角色来画，或指定了某个效果时，
        **务必先调用本工具**查询是否有匹配的 LoRA（可结合 keyword 或 category 缩小范围），
        再在 comfyui_draw / comfyui_img2img 的 loras 参数里填入正确名称；不要凭记忆猜测
        LoRA 名称，也不要编造不存在的 LoRA，更不要在用户要求某风格时直接跳过 LoRA 查找。
        ★触发词使用提示：本工具返回的 trigger_words 会被插件在启用 LoRA 后【全量自动追加】到提示词，一般无需干预。
        仅当触发词里混有与用户本次要求明确冲突的词（例如用户要求换别的衣服，而触发词含 white dress 这类服装词）时，
        才在 comfyui_draw 的 trigger_words 参数传入筛选后的子集（保留角色/画风核心词、只剔除冲突词，
        启用多个 LoRA 时必须合并全部触发词再筛选）；没有冲突就不要传该参数，禁止传空字符串。

        Args:
            base_model(string): 可选。按底模过滤（如 anima / z-image-turbo / krea2 / illustrious）。当用户指定了工作流/底模时，传入该底模只列出可用的 LoRA。
            keyword(string): 可选。按名称/别名/描述/触发词模糊匹配查找某个 LoRA。
            category(string): 可选。按分类过滤（角色 / 风格 / 工具）。当用户提到"某某角色/人物"、"某某风格/画风"或"某某工具类效果（如加速、细节增强、图像质量增强）"时，可传入 角色 / 风格 / 工具 缩小范围。
        """
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if plugin is None:
            plugin = self
        lib = plugin._lora_library()
        if not lib:
            return "当前未配置任何 LoRA。可在插件配置页的 LoRA 库中添加。"
        wf_bm = (base_model or "").strip().lower()
        kw = (keyword or "").strip().lower()
        cat = (category or "").strip()
        rows = []
        for l in lib:
            name = (l.get("name") or "").strip()
            if not name:
                continue
            lora_bm = (l.get("base_model") or "").strip().lower()
            if wf_bm and lora_bm and lora_bm != wf_bm:
                continue  # 底模不匹配的 LoRA 不列出
            if cat and (l.get("category") or "").strip() != cat:
                continue  # 分类不匹配的 LoRA 不列出
            aliases = l.get("aliases") or []
            desc = (l.get("description") or "").strip()
            tw = (l.get("trigger_words") or "").strip()
            if kw:
                hay = " ".join([name, *[str(a) for a in aliases], desc, tw]).lower()
                if kw not in hay:
                    continue
            alias_str = ", ".join(str(a) for a in aliases) if aliases else name
            lines = [f"- {name}（别名：{alias_str}）"]
            if (l.get("category") or "").strip():
                lines[0] += f" [分类 {l.get('category').strip()}]"
            if lora_bm:
                lines[0] += f" [底模 {lora_bm}]"
            if desc:
                lines.append(f"  描述：{desc}")
            if tw:
                tw_short = tw.replace("\n", " / ")
                if len(tw_short) > 200:
                    tw_short = tw_short[:200] + "…"
                lines.append(f"  触发词：{tw_short}")
            rows.append("\n".join(lines))
        if not rows:
            if kw:
                return f"没有找到匹配「{keyword}」的 LoRA。可先调用本工具（不带 keyword）查看全部 LoRA。"
            if cat:
                return f"分类「{category}」下没有可用的 LoRA。"
            return "没有可用的 LoRA。"
        head = "已配置的 LoRA 列表："
        if cat:
            head += f"（分类 {category}）"
        if wf_bm:
            head += f"（底模 {base_model}）"
        return head + "\n" + "\n".join(rows)

    # LLM 工具：comfyui_workflows（查询工作流列表）
    # ------------------------------------------------------------------ #
    @filter.llm_tool(name="comfyui_workflows")
    async def llm_workflows(self, event: AstrMessageEvent):
        """查询所有已配置的 ComfyUI 工作流列表，包括名称、是否支持图生图、是否动漫、是否为漫画/带字工作流。

        触发时机：在调用 comfyui_draw / comfyui_img2img / comfyui_comic 之前，如需确认
        有哪些可用工作流，务必先调用此工具获取列表，再根据用户意图选择正确工作流名传入。
        列表每行标记含义：
        - [支持图生图] / [仅文生图]：能否用于图生图（传 img2img_workflow）。
        - 【Anima】：动漫/二次元底模工作流。
        - [漫画/带字]：配置了 prompt_slots 多槽位注入的漫画/表情包工作流，只有这类能用于
          comfyui_comic（生成带气泡/底部文字的图）；普通生图请用 comfyui_draw。

        重要：不要凭记忆或猜测工作流名称！每次都先查列表再选。
        """
        workflows = self._workflows()
        if not workflows:
            return "暂无已配置的工作流。"

        lines = ["已配置的工作流列表："]
        for w in workflows:
            name = w.get("name", "(未命名)")
            has_image = bool((w.get("image_node") or "").strip())
            img_tag = " [支持图生图]" if has_image else " [仅文生图]"
            anima = " 【Anima】" if w.get("is_anima") else ""
            has_slots = bool(self._normalize_prompt_slots(w.get("prompt_slots")))
            comic_tag = " [漫画/带字]" if has_slots else ""
            lines.append(f"- {name}{img_tag}{anima}{comic_tag}")

        default = self._cfg("default_workflow", "")
        default_real = self._cfg("default_workflow_real", "")
        default_i2i = self._cfg("default_img2img_workflow", "")
        default_i2i_real = self._cfg("default_img2img_workflow_real", "")
        lines.append("\n默认工作流：")
        lines.append(f"- 动漫文生图: {default or '（未设置，回退第一个）'}")
        lines.append(f"- 真人文生图: {default_real or '（未设置，回退动漫文生图）'}")
        lines.append(f"- 动漫图生图: {default_i2i or '（未设置，回退动漫文生图）'}")
        lines.append(f"- 真人图生图: {default_i2i_real or '（未设置，回退动漫图生图）'}")

        return "\n".join(lines)

    # LLM 工具：comfyui_img2img（AI 对话图生图触发）
    # ------------------------------------------------------------------ #
    @filter.llm_tool(name="comfyui_img2img")
    @_safe_llm_tool
    async def llm_img2img(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        negative_prompt: str = "",
        workflow: str = "",
        img2img_workflow: str = "",
        loras: list = None,
        seed: int = 0,
        image: str = "",
        denoise: float = -1,
        count: int = 0,
        prompts: list = None,
        source: str = "",
        caption: str = "",
    ):
        """使用 ComfyUI 基于一张参考图生成 / 变换图片并返回给用户。

        触发时机：当用户附带一张图片，并希望基于该图生成新图或做变换时（如「把这张图
        变成油画风格」「以这张图为参考画一个类似场景」「图生图：转绘成动漫风」），务必
        调用此工具，并把图片附在消息里、把变换描述作为 prompt 传入。

        什么时候绝对不要调用本工具（务必遵守，防止误触发打扰用户）：
        - 用户明确表示不要发图 / 取消 / 停止 / 不需要变换，例如「不用画了」「别画了」
          「算了别改了」「取消」「不要图」「先别发了」「停」。此时用户是在【拒绝/纠正】，
          必须尊重用户，**绝对不要调用本工具**，也不要调用任何发图工具，只需用文字回应即可。
        - 用户只是普通聊天、闲聊、提问等，且没有图生图意图时，不要调用本工具。
        - 用户明确说「不要发给我」「不要生成」「收回」等否定意图时，一律不调用。
        - 判断依据：以用户当前消息的【明确意图】为准。对话历史里做过变换不等于当前还要做；
          若用户当前表现出拒绝/取消/不需要，就绝不变图，不要被之前的请求带偏。

        重要约束：
        - 必须确保有参考图；若当前消息没有图、也拿不到用户最初发的那张原图，请提示用户先发一张图再描述变换。
        - 即便对话历史里做过类似变换，只要用户再次表达改图意图（如「再改一下」「重新改」「继续改这张图」），就重新调用。
        - 参考图的选择规则（★最容易出错，务必遵守）：
          ① 参考图 = **用户自己发的那张原图**。你（AI）上次生成的结果图**不是**参考图，除非用户明确说
            「把上次生成的那张图/刚才那张成品再改」，否则**绝不要**把 AI 生成的图当作参考图传进来。
          ② 用户说「再改一下 / 重新改 / 继续改这张图」（没发新图）时，应基于**最初用户发的那张原图**继续改，
            而不是上次生成的结果图；优先从对话历史里找到用户最初附带的原图并引用它。
          ③ 若无法定位用户最初的原图，就提示用户重发一张图，**不要**擅自用最近一次生成的图顶替。
          ④ 只有在「用户明确引用你刚生成的某张图去二次加工」时，才允许用那张 AI 生成图作为参考图。
        - 传入 image 参数（消息中图片的 URL）或插件自动从消息中提取图片均可。
        - ⚠️ 若用户已在当前消息里附带了图片，请直接把该图片（或其在消息中的引用）
          传入即可，**不要**调用 get_message_detail 之类接口去回拉"原始消息"再重新下载图片：
          回拉到的原始图片 URL 通常无法在本机直接下载（带签名时效/内网地址），既耗时又必然失败，
          而当前消息里的图已可被插件直接使用。
        - ⚠️ 图生图不需要你（大模型）去"理解"或"描述"参考图的内容：
          参考图会直接作为像素喂给 ComfyUI 的 LoadImage 节点，你只需把用户的变换意图
          翻译成英文提示词（prompt）即可，不要浪费步骤去调用视觉转述/读取图片内容。

        caption 配文（图文消息，可选但推荐）：
        - caption 是你想和图片**发在同一条消息里**的那句话（如「给你改好啦～」），
          填了它，插件会把「这段文字 + 图片」合成【一条】消息发出。建议 20 字以内、用你自己的口吻，
          不要复述画面内容（画面由 prompt 负责）。
        - ★配文会随图发出，工具返回后【绝对不要】在回复里再说一遍同样的话。
        - 一次出多张（prompts 多条）时，配文只加在【第一张】图上。不想配文就留空（默认）。

        提示词语言规范（务必遵守）：
        - 先确定本次出图的工作流类型：真人/写实工作流（is_anima=false）或动漫/二次元工作流
          （is_anima=true，Anima）。可通过 comfyui_workflows 查询目标工作流的 is_anima 字段；
          未指定工作流时按默认工作流判断（默认通常是真人）。
        - 真人/写实工作流：prompt 首选中文，除非用户明确要求英文才用英文。
        - 动漫/二次元工作流（is_anima=true）：prompt 必须为英文标签化描述（如
          "1boy, handsome, anime style, sharp eyes, masterpiece"），不得输出中文。
          即使用户用中文描述变换意图，也要翻译改写为英文 Danbooru 风格标签，不要原样透传中文。
          ★若你当前的工具列表里有「Danbooru tag search / Danbooru 标签搜索」这类 MCP 工具，
          务必优先调用它查询/确认标准 Danbooru 标签后再填入 prompt，不要仅凭记忆臆造、也不要原样透传中文；
          没有该类 MCP 工具时才退而用你自己的翻译能力改写。
        - 负向提示词（negative_prompt）同样遵循上述语言规则。

        工作流选择规则：
         插件已配置的工作流中，有些配置了「参考图节点」（image_node），说明该工作流
         支持图生图；有些没有，说明只能文生图。
         ⚠️ 重要：在调用本工具前，务必先调用 comfyui_workflows 查询工作流列表，
         确认有哪些工作流可用、哪些支持图生图，然后按以下优先级选择
         img2img_workflow：
         0. ★推荐优先选择名称含「图生图」字样的工作流（管理员通常把图生图工作流命名为
           「XX图生图」），这类工作流专为图生图设计。
         1. 只选列表中标记为「支持图生图」的工作流。
         2. 在支持图生图的工作流中，按名称语义匹配：
            - 用户说"转成真人/真人照片/写实" → 选名称含「真人」的
            - 用户说"转成动漫/二次元/动漫风" → 选名称含「动漫」或「二次元」的
            - 用户明确说了工作流名 → 直接用那个名字
         3. 如果都不匹配，传空让插件自动用图生图默认工作流。

        Args:
            prompt(string): 【必填】基于参考图的变换 / 生成描述（中文或英文均可）。这是唯一必须填写的参数，
                直接给出变换意图文本，不要留空，不要包裹自然语言或 markdown。
            negative_prompt(string): 负向提示词，可选，不填则留空。
            img2img_workflow(string): 图生图工作流名称。★用户明确要求特定画风/风格/类型时，必须先调用 comfyui_workflows 查询真实列表，再从中选确切名称传入；禁止凭记忆或猜测工作流名，否则找不到工作流。用户完全没指定画风时才可留空（插件用图生图默认工作流）。
            loras(array[string]): 需要启用的 LoRA 名称列表，例如 ["catgirl", "rain"]。每项可用 "名称" 或 "名称:权重"（冒号后为强度/权重，如 0.8 表示弱化、1.2 表示增强；用户明确强弱时给权重，没给则省略用默认）。★重要：当用户要求某种风格/画风/角色时，即使没给具体 LoRA 名，也应先调 comfyui_loras（可用 keyword/category 缩小）查匹配的 LoRA 再填入。指定 LoRA 前必须先调用 comfyui_loras 查询真实列表（可按底模/分类过滤），再从中选确切名称传入；禁止凭记忆或猜测名称。只有确认无匹配 LoRA 或用户明确不要时才留空（留空使用配置中默认启用的 LoRA）。
            seed(number): 随机种子，0 或不填表示每次随机。用户明确要求"固定/复现/用同样的种子"时传入具体数字。
            image(string): 参考图 URL。多数情况用户直接发图时无需传此参数，插件会自动从消息提取；仅当需要明确指定某张图时传入。
            denoise(number): 降噪幅度/重绘强度（0~1）。不传或 -1 则用工作流配置默认值。用户明确要求"改多少/像不像原图"时传入。
            count(number): 本次要生成的图片张数。★最重要规则：用户明确说出的数量是最高优先级，必须严格遵守——用户说"一张/只发一张/就一张/单张"→ 必须传 count=1；用户说"来 3 张/两张/五张"等具体数字 → 传对应 N。其次：①用户完全没提数量 → 不传 count（默认 1 张）；②"换个角度/再画一下/重来/再来"这类语义词【不自动代表多张】，默认仍为 1 张，除非用户明确说了要"几张/一些/多张"；③只有用户明确表达要多张（"来几张/多画几张"）但没给具体数字时 → prompts 传 3 条不同效果。★【count 当前恒为 1，一般不用传】：张数由 prompts 的条数决定，本参数是预留给将来「同一条提示词跑不同种子出多张」用的，现在传大于 1 会被拦回并要求改用 prompts 数组。
            prompts(array): 多条出图项，【要几张就传几条】，每条各出 1 张。两种写法都支持：
                ① 纯字符串数组（旧，全局参数共享）：每条是一个变换描述，例如
                   ["转成水彩", "转成油画"]；"三个效果各来 2 张"→ 传 6 条。
                ② 对象数组（新，每项可独立定制）：每条是 {"prompt", "workflow",
                   "img2img_workflow", "loras", "width", "height", "denoise", "seed"} 对象，
                   未写的字段回落全局参数。例如要「水彩、油画各来一张、用各自图生图工作流」→ 传
                   [{"prompt":"转水彩","img2img_workflow":"水彩图生图"}, {"prompt":"转油画","img2img_workflow":"油画图生图"}]，
                   一次调用两个效果各用各工作流出齐，不会被拆分调用拦回。
                ★每条要写【不同的变换】，不要把同一条提示词重复多遍。与 prompt 二选一即可，
                两者都传时以 prompts 为准。需要多个效果请用它一次传完，不要拆成多次调用本工具。

        补充说明：
        - 用户未明确要求 lora/seed/denoise 时，这些参数可不传，插件自动使用工作流或配置默认值。
        - 参考图通常附在用户消息里即可，插件会自动提取；无需强求大模型传 image 参数。
        - ★★一条用户请求只调一次本工具：要 N 张就用 prompts 一次传 N 条不同变换效果，画完立刻自然收尾。
          插件对同一条用户消息有硬性出图闸门：本轮出过图后再调用会被直接拦回、出不了图，
          只会白费一轮。只有用户发来【下一条新消息】明确要求再改时才可再次调用。
        """
        # LLM 工具开关：关闭时拒绝本插件 LLM 的自动调用，
        # 但伴侣插件等第三方主动调用（带 source 标记）不受影响。
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if plugin is None:
            plugin = self
        if not plugin._cfg("enable_llm_tools", True) and not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            return "LLM 画图工具已关闭，请使用指令绘图（/draw、/img2img、/画xxx 等）。"

        # 已读回执：用户用自然语言触发生图（comfyui_img2img）时，给原消息贴表情表示「已读」。
        # 伴侣插件等第三方主动调用（带 source）无对应用户消息，跳过避免误贴。
        if not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            await self._react_ack(event)

        # 与 llm_draw 同样的兜底处理
        if not isinstance(event, AstrMessageEvent):
            event = getattr(plugin, "_last_event", None)
        if event is None:
            return "⚠️ 绘图工具未能获取到会话事件，请稍后重试，或直接使用 /img2img 指令。"

        # ── 单轮出图闸门（v5.0）───────────────────────────────────────
        # 与 comfyui_draw 同一套：本轮已成功出图 / 已失败重试达到上限就收尾。
        # 不看参数、不看时间间隔，因此不存在被绕过的可能。
        _gate_ok2, _gate_hint2 = plugin._draw_run_check(
            event, source=source, tool_name="comfyui_img2img"
        )
        if not _gate_ok2:
            return _gate_hint2

        # prompt 兜底：与 comfyui_draw 同一套规则——
        #   · 不带 source：空参数【直接报错】，绝不去对话历史兜底抓文本当 prompt；
        #   · 带 source（伴侣插件等）：保留原有的「指定模型提取 → 原始消息文本」链路。
        # 同 comfyui_draw：prompt 单条 或 prompts 数组，任一非空即算有画面描述。
        _has_any_prompt2 = bool(prompt and prompt.strip()) or bool(
            [x for x in (prompts or []) if str(x).strip()]
        )
        if not _has_any_prompt2:
            if not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
                plugin._draw_run_fail(event, kind="empty")
                logger.info("【工具·llm_img2img】 空参数调用（prompt 与 prompts 均为空），拒绝兜底提取，要求模型补齐后重试")
                return (
                    "调用失败：缺少变换描述。请把本次要做的变换描述填进 prompt 参数"
                    "（若是多个不同效果，改用 prompts 数组逐条列出），再重新调用本工具一次。"
                )

            user_text = ""
            try:
                user_text = (getattr(event, "message_str", "") or "").strip()
            except Exception:
                user_text = ""
            if user_text:
                extracted = await self._llm_extract_args(
                    user_text,
                    "prompt(string): 基于参考图的变换/生成描述（必填）。\n"
                    "negative_prompt(string): 负向提示词，可选。\n"
                    "img2img_workflow(string): 图生图工作流名，可选。\n"
                    "loras(string): LoRA 名称/别名列表，可选，每项可带权重如 \"catgirl:0.8\"。\n"
                    "seed(int): 随机种子，可选。denoise(float): 降噪幅度0~1，可选。",
                )
                if extracted and extracted.get("prompt"):
                    prompt = str(extracted["prompt"]).strip()
                    if extracted.get("negative_prompt") and not negative_prompt:
                        negative_prompt = str(extracted["negative_prompt"])
                    if extracted.get("img2img_workflow") and not img2img_workflow:
                        img2img_workflow = str(extracted["img2img_workflow"])
                    if extracted.get("loras") and not loras:
                        loras = extracted["loras"]
                    try:
                        if extracted.get("seed") is not None and not seed:
                            seed = int(extracted["seed"])
                        if extracted.get("denoise") is not None and not denoise:
                            denoise = float(extracted["denoise"])
                    except (TypeError, ValueError):
                        pass
            if not prompt or not prompt.strip():
                if user_text:
                    prompt = self._strip_command(user_text, "img2img")
                if not prompt or not prompt.strip():
                    return "⚠️ 调用 comfyui_img2img 失败：缺少必填参数 prompt（基于参考图的变换 / 生成描述）。请补充画面描述后再试。"

        # ── 出图计划：把「多条提示词 × 每条几张」摊平成 (提示词, 张数) 列表 ──────
        # 与 comfyui_draw 同一套：prompts 数组用于一次做多个不同变换，count 表示每条各出几张，
        # 总张数受插件配置的单次上限（draw_auto.max）约束，超出自动截断；带 source 不受限。
        # prompts 统一规整成 list[dict]：兼容「纯字符串数组」(旧) 与
        #「对象数组」(新，每项可独立指定 workflow/img2img_workflow/loras/
        # width/height/denoise/seed，缺省回落全局参数)。
        # 图生图场景下 img2img_workflow 即各项的图生图工作流，workflow 为文生图工作流名
        # （仅当该项具备图生图能力时才会被采用，否则回退默认图生图）。
        _items2 = self._normalize_prompts(prompts)
        _per2 = max(1, int(count or 1))
        if _items2:
            # 同 comfyui_draw：传了 prompts 时【张数 = 条数】，每条各出 1 张；
            # count 为预留参数，当前恒为 1、不参与计算。
            _wanted2 = len(_items2)
        else:
            # 同 comfyui_draw：要 N 张就该用 prompts 传 N 个不同效果，
            # 「单条 prompt + count=N」只会得到同一效果的 N 个近似副本。拦一次并教正确写法。
            if _per2 > 1 and not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
                _st2 = plugin._draw_run_state_of(event)
                if not _st2.get("count_hint_done"):
                    _st2["count_hint_done"] = True
                    logger.info(
                        f"【工具·llm_img2img】 count={_per2} 但未传 prompts，拦截并提示改用 prompts 数组"
                    )
                    return (
                        f"你要出 {_per2} 张图，但只传了单个 prompt——那样只会得到同一个效果的 "
                        f"{_per2} 个近似副本。请改用 prompts 数组：把 {_per2} 个不同的变换效果各写成一项，"
                        f"count 保持 1 不传，然后重新调用本工具一次。"
                    )
            _items2 = [{
                "prompt": (prompt or "").strip(),
                "workflow": None, "img2img_workflow": None, "loras": None,
                "width": 0, "height": 0, "denoise": -1, "seed": 0,
            }]
            _wanted2 = _per2
        _allowed2, _max_hint2 = plugin._draw_single_max(_wanted2, source=source, event=event)
        # 注：_plan2（含每项的 per-item 工作流解析）需在下方 resolved_wf 决策完成后构建，
        # 见「决定工作流」段之后。

        # ── 收集图片（与 llm_draw 共用同一逻辑）─────────────────────
        init_images: list[str] = []

        # ① image 参数：LLM 传入的参考图 URL
        got_explicit_image = False
        if image and image.strip():
            img_url = image.strip()
            logger.info(f"【取图】 llm_img2img image 参数: {img_url}")
            p = await _image_to_local_path(img_url)
            if p:
                init_images.append(p)
                got_explicit_image = True
                logger.info(f"【取图】 image 参数下载成功: {p}")
            else:
                logger.warning(f"【取图】 image 参数下载失败: {img_url}")

        if not got_explicit_image:
            # ② 从事件中自动提取图片（仅在未通过 image 参数显式拿到图时才探测）。
            #    图生图不需要大模型"看懂"图片，参考图直接喂给 ComfyUI 的 LoadImage 节点；
            #    因此若 image 参数已成功取到图，就绝不再去 event / last_event 里做无谓的
            #    兜底探测（避免把上几次生成的旧图也混进来、也少打噪音日志）。
            event_images = await plugin._extract_images(event)
            last_ev = getattr(plugin, "_last_event", None)
            if not event_images and last_ev is not None and last_ev is not event:
                logger.info("【取图】 llm_img2img 工具 event 未取到图，回退到 LLM 调用前捕获的原始事件再取一次")
                event_images = await plugin._extract_images(last_ev)
            # 去重合并
            seen = set(init_images)
            for ep in event_images:
                if ep not in seen:
                    seen.add(ep)
                    init_images.append(ep)

        if not init_images:
            # 兜底：本次消息/引用/_last_event 都没取到图时，退回「本会话用户最近发来的图」
            # （g_last_received，在 LLM 工具调用前趁图还在时已缓存到）。
            # 典型场景：用户先发一张图，AI 用自己的话总结并调用本工具做图生图，此时工具
            # 收到的 event 是 AI 的纯文本回复、图片消息既未被引用也没被带入 event，
            # 但用户确实刚发过图——这种"用户当前意图的参考图"兜底合理。
            # 注意：特意不回退「本插件自己生成的图」(g_last_generated)，避免把续画/上次出图
            # 误当成图生图参考图导致结果污染。
            sid = getattr(event, "session_id", "") or ""
            for p in (g_last_received.get(sid) or []):
                if p and os.path.exists(p) and p not in init_images:
                    init_images.append(p)
            # 二级兜底：本会话用户「历史消息里发过的图」（覆盖前一条消息发的图、
            # 引用图未回填等场景）。仅当上面仍为空时启用，且只取最近 1 张。
            if not init_images:
                hist = list(reversed(g_recent_user_images.get(sid) or []))
                for p in hist[:1]:
                    if p and os.path.exists(p) and p not in init_images:
                        init_images.append(p)
                        break
            # 三级兜底：本会话「本插件最近生成的图」——这些图就在 AstrBot 部署服务器本地
            # 的 gallery/ 目录里（archive_image 归档后路径记录在 g_last_generated）。
            # 典型场景：用户引用了 AI 之前生成的图做图生图，但平台 get_msg 拉不到引用消息、
            # 引用图解析失败；此时服务器上明明有这张图，直接用它做参考图即可，无需再走平台。
            # 限定「本会话 + 最近 1 张」避免误用旧图。
            if not init_images:
                for p in (list(reversed(g_last_generated.get(sid) or []))[:1]):
                    if p and os.path.exists(p) and p not in init_images:
                        init_images.append(p)
                        break
            if init_images:
                logger.info(f"【取图】 启用兜底图片（本会话用户最近收到/历史/生成图）: {init_images}")
            else:
                return "请先发送一张参考图，再用文字告诉我要怎么变换它哦～ 例如「把这张图变成夜晚」。"

        # ── 决定工作流 ─────────────────────────────────────────────
        # 图生图始终 is_img2img=True；img2img_workflow > workflow > 默认图生图
        # 调用级默认工作流（供 per-item 缺省回落）
        global_resolved_wf = self._resolve_workflow_for(
            workflow, img2img_workflow, True, False, None
        )

        lora_map = self._parse_llm_loras(loras)

        # 与 llm_draw 一致：先按通用规则拆分正/负向并清洗标记。
        # 负向取首条拆分结果（多条提示词的负向通常一致）；正向在循环里按每组提示词各自拆分。
        _, parsed_neg = plugin._split_external_prompt(prompt)
        negative = parsed_neg or (negative_prompt or "")

        # ── 出图计划（per-item）：每项独立解析工作流与参数 ──────
        _plan2: list[dict] = []
        for _it in _items2[: max(1, _allowed2)]:
            _wf = self._resolve_workflow_for(
                _it["workflow"], _it["img2img_workflow"], True, False, None
            )
            if not _wf:
                _wf = global_resolved_wf
            _plan2.append({
                "prompt": _it["prompt"],
                "wf": _wf,
                "loras": _it["loras"],
                "width": _it["width"],
                "height": _it["height"],
                "denoise": _it["denoise"],
                "seed": _it["seed"],
            })
        if not _plan2:
            _plan2 = [{
                "prompt": _items2[0]["prompt"], "wf": global_resolved_wf,
                "loras": None, "width": 0, "height": 0, "denoise": -1, "seed": 0,
            }]
        logger.info(
            f"【工具·llm_img2img】 出图计划：{len(_plan2)} 项、共 {len(_plan2)} 张"
            + (f"（请求 {_wanted2} 张，已按单次上限截断）" if _max_hint2 else "")
        )

        is_companion = bool(source and source.strip() == SOURCE_COMPANION_PLUGIN)

        img_paths: list[str] = []
        # 按出图计划逐张生成：原生 / AI 对话调用「画一张发一张」，用户逐张收到；
        # 伴侣插件仍收集全部路径后返回 JSON。
        _total_n2 = len(_plan2)
        _seq2 = 0
        # 同 llm_draw：主动发图失败的张数，用于如实回传「图已生成但没发出去」。
        _send_fail2 = 0
        for _item2 in _plan2:
            _positive2, _parsed_neg2 = plugin._split_external_prompt(_item2["prompt"])
            if not (_positive2 or "").strip():
                continue
            if _parsed_neg2 and not negative:
                negative = _parsed_neg2
            # 同 llm_draw：仅在明确指定 seed 时递增，未指定时保持 0 走随机，
            # 避免多张图生图的第 2 张起被固定成 1、2、3 这类退化种子。
            _item_seed2 = _item2["seed"]
            _seed_j = (int(_item_seed2) + _seq2) if (_total_n2 > 1 and _item_seed2) else _item_seed2
            _seq2 += 1
            # per-item 参数：缺省回落全局参数（图生图默认不传 width/height）
            _item_lora_map2 = plugin._parse_llm_loras(_item2["loras"]) if _item2["loras"] else lora_map
            _item_w2 = _item2["width"] or None
            _item_h2 = _item2["height"] or None
            _item_denoise2 = _item2["denoise"] if _item2["denoise"] >= 0 else denoise
            async for node, p in plugin._do_draw(
                event,
                _item2["wf"],
                _positive2,
                negative,
                _item_w2,
                _item_h2,
                _item_lora_map2,
                None,
                _seed_j or None,
                init_images=init_images,
                is_img2img=True,
                denoise=_item_denoise2 if _item_denoise2 >= 0 else None,
                source=source,
                # 图文消息：配文只加在【第一张】图上，多张时避免同一句话重复 N 遍
                caption=(caption if not img_paths else ""),
            ):
                if p:
                    img_paths.append(p)
                # 原生 / Agent 调用：画一张立刻发一张（边画边发）。
                # LLM 工具的 return 值只会作为工具结果文本回传给模型，框架不会
                # 自动渲染图片，必须主动 event.send 把图发到聊天里。
                if node is not None and not is_companion:
                    # 同 llm_draw：「本轮已出图」按【图已生成】计，不按发送结果计，
                    # 避免群聊 send 失败时闸门不计数导致模型无限重画；
                    # 发送失败累加 _send_fail2，只影响返回给模型的文案。
                    plugin._draw_run_hit(event)
                    try:
                        await event.send(node if isinstance(node, MessageChain) else MessageChain([node]))
                    except Exception as _e:
                        _send_fail2 += 1
                        logger.warning(f"【出图·发送失败】 comfyui_img2img 图已生成但 event.send 失败: {_e}")

        if img_paths:
            if is_companion:
                # 伴侣插件：用 JSON 文本返回图片路径，由调用方负责发图与解析。
                # note 明确告知调用方：图已生成、直接用 image_paths 发，不要再用
                # astrobot_file_read_tool 去读路径、也不要用 pc_send_current_media 重复发送。
                return json.dumps({
                    "image_paths": img_paths,
                    "status": "ok",
                    "note": "图片已生成完成，image_paths 为服务器本地路径，请直接发送这些图给用户；"
                            "不要再用 astrobot_file_read_tool 去读取图片路径，"
                            "也不要用 pc_send_current_media 重复发送同一张图（图已生成，重发只是重复刷图）。",
                }, ensure_ascii=False)
            # 原生 / Agent 调用：图片已在循环内「画一张发一张」，这里只需让模型收尾。
            # 不 return None——否则 Agent Loop 直接结束、LLM 不再说话。
            # ★「本轮已出图」已在循环内按发送结果逐张计入（发送失败不计），
            #   这里不再无条件 hit，否则会重现「图没发出去却算已出图」。
            # 若本次张数被单次上限截断过，附带一句中性的事实说明，由模型自行告诉用户。
            # 同文生图：图片已由插件发出，返回值【不】附带本地路径，避免模型拿真路径
            # 用 send_message_to_user 把已发的图再发一遍（出现「连续发两次图」）。
            if _send_fail2:
                return (
                    f"⚠️ 图片已生成（共 {len(img_paths)} 张，已存入图库），"
                    f"但发送到聊天窗口失败 {_send_fail2} 张（多为协议端掉线/风控/超时，图本身没问题）。"
                    f"请简短告诉用户「图生成好了，但发送时卡了一下，稍后再要一次我就重新出一张」，"
                    f"绝不要说「已经发给你了」；也不要现在就重新调用本工具、"
                    f"更不要改用 send_message_to_user / pc_send_current_media 自行发送——"
                    f"请先结束本轮回复，等用户再次明确索要时再处理。"
                )
            return (
                f"✅ 图片已成功生成并发送到聊天窗口，用户已经能看到，你无需再做任何发送动作。"
                f"请用一句话自然告诉用户图已发给他即可；"
                f"不要调用 send_message_to_user / pc_send_current_media 把已发的图再发一次"
                f"（那只会刷出重复图片），也不要用 astrobot_file_read_tool 去读取该图。"
                + (_max_hint2 or "")
            )
        # 一张都没出：记一次后端失败（受失败重试额度约束），仍返回文本让模型收尾。
        plugin._draw_run_fail(event, kind="backend")
        return "本次生图失败。请用一句话简短向用户说明生成遇到问题即可，不要复述本提示。"
