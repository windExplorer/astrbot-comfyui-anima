"""AstrBot ComfyUI 绘图插件（支持多服务器、多工作流、LoRA 管理、Anima 标签翻译）。"""

import os
import json
import random
import re
import time
import traceback
import uuid
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

try:
    from PIL import Image as _PILImage
except ImportError:  # pragma: no cover - 环境无 Pillow 时降级（不读像素尺寸）
    _PILImage = None

try:
    from astrbot.api.star import StarTools
except ImportError:
    StarTools = None

try:
    from . import comfyui_client, danbooru_client, translate_client, workflow_builder
except ImportError:
    # 兼容非包环境（如本地测试直接运行本模块）
    import comfyui_client
    import danbooru_client
    import translate_client
    import workflow_builder

try:
    from . import quota_store
except ImportError:
    import quota_store

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

# 「我会永远陪着你」伴侣插件的来源标识：llm_draw 的 source 参数命中此值时，
# 对整段提示词做专属的格式化与过滤（拆分正/负向、过滤时间/日程/位置/情绪等无关
# 事实与元指令、清除 [section compacted] 等标记）。
SOURCE_COMPANION_PLUGIN = "我会永远陪着你"


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
        logger.debug(f"[取图] 引用消息API回退异常（忽略）: {e}")
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
                        f"[取图] 引用图兜底下载失败: HTTP {resp.status} {url[:80]}"
                    )
                    return None
                data = await resp.read()
        if not data or len(data) < 64:
            logger.warning(f"[取图] 引用图兜底下载内容异常（空/过小）: {url[:80]}")
            return None
        with open(out, "wb") as f:
            f.write(data)
        logger.info(f"[取图] 引用图兜底下载成功: {url[:80]}... -> {out}")
        return out
    except Exception as e:
        logger.warning(f"[取图] 引用图兜底下载异常（忽略）: {e} | {url[:80]}")
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
        logger.debug(f"[取图] 构造图片组件失败: {e}")
        return None
    p = None
    try:
        p = await comp.convert_to_file_path()
    except Exception as e:
        logger.debug(f"[取图] convert_to_file_path 失败: {e}")
    # 兜底：convert_to_file_path 失败时，若原始是 http(s) URL（如引用消息里的带签名图
    # 床地址），尝试自带 UA/Referer 下载到本地 temp，避免"引用消息图片读不到"。
    if not p:
        raw_url = None
        if isinstance(item, str) and item.startswith("http"):
            raw_url = item
        elif getattr(comp, "url", None):
            raw_url = comp.url
        if raw_url:
            logger.debug(f"[取图] convert_to_file_path 失败，尝试 URL 兜底下载: {raw_url[:80]}")
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
            f"[取图] 解析出的路径不存在（可能是平台文件名而非本地路径）: {p!r}"
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
                f"[取图] 解析出的文件无效（size={sz}, ext_ok={ok_ext}, magic_ok={magic}），"
                f"视为下载/解析失败: {p!r}"
            )
            p = None
    if not p:
        logger.warning(
            f"[取图] 无法解析为本地路径: "
            f"url={getattr(comp, 'url', None)!r} "
            f"file={getattr(comp, 'file', None)!r} "
            f"path={getattr(comp, 'path', None)!r}"
        )
    return p


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

            self.gallery = ImageStore(self.data_dir, self.config.get("gallery", {}))
            logger.info(
                f"[init] 图库已就绪: {self.data_dir} "
                f"(gallery={self.gallery.gallery_dir}, refs={self.gallery.refs_dir}, "
                f"db={self.gallery.db_path})"
            )
        except Exception as e:
            logger.warning(f"[init] 图库初始化失败（功能不可用）: {e}", exc_info=True)

        # 生图次数限制（配额）：独立 SQLite 维护每个用户的总/小时生图计数与单独配置
        self.quota = None
        try:
            self.quota = quota_store.QuotaStore(self.data_dir)
            logger.info(f"[init] 生图限额已就绪: {self.quota.db_path}")
        except Exception as e:
            logger.warning(f"[init] 生图限额初始化失败（功能不可用）: {e}", exc_info=True)

        # WebUI 控制台：把本插件日志镜像进内存环形缓冲，供页面读取
        try:
            try:
                from .webui_api import LOG_BUFFER
            except ImportError:
                from webui_api import LOG_BUFFER

            self._webui_log_buffer = LOG_BUFFER
            self._install_webui_log_handler()
        except Exception as e:
            logger.warning(f"[init] WebUI 日志缓冲初始化失败（可忽略）: {e}")

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
        # 挂到 root logger，捕获插件及依赖（ComfyUI client 等）全部日志
        logging.root.addHandler(h)
        self._webui_log_handler = h

        # 同时落盘，方便排查（文件保留最近 1MB 滚动）
        try:
            from logging.handlers import RotatingFileHandler

            fh = RotatingFileHandler(
                self.data_dir / "webui.log", maxBytes=1024 * 1024, backupCount=1, encoding="utf-8"
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            logging.root.addHandler(fh)
            self._webui_file_handler = fh
        except Exception:
            self._webui_file_handler = None

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
                logger.info(f"[init] 已为工具补充 required: {patched}")
            else:
                logger.warning("[init] 未找到本插件工具，补充 required 跳过（工具可能尚未注册）")
        except Exception as e:  # 框架内部结构变动时不致命
            logger.warning(f"[init] 补充工具 required 失败（可忽略）: {e}")

        # 注册 WebUI 控制台路由（/api/<插件名>/page/...）
        try:
            try:
                from .webui_api import register_web_api
            except ImportError:
                from webui_api import register_web_api

            register_web_api(self)
        except Exception as e:
            logger.warning(f"[init] 注册 WebUI 路由失败（控制台不可用）: {e}")

    async def terminate(self) -> None:
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
            llm_resp = await self.context.llm_generate(chat_provider_id=model, prompt=prompt)
            text = getattr(llm_resp, "completion_text", "") or ""
        except Exception as e:
            logger.warning(f"[llm_model] 指定模型({model}) 参数提取失败，退回默认逻辑: {e}")
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
                logger.debug(f"[图库] LRU 清理异常（忽略）: {_e}")

    def _servers(self) -> list[dict]:
        return self._cfg("comfyui_servers", []) or []

    def _workflows(self) -> list[dict]:
        return self._cfg("workflows", []) or []

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
            for a in re.split(r"[,，\n\r]+", kws):
                a = a.strip()
                if a and a not in aliases:
                    aliases.append(a)
            item["aliases"] = aliases
            out.append(item)
        return out

    def _loras_of(self, wf: dict) -> list[dict]:
        """解析本工作流实际生效的 LoRA 列表。

        工作流里的 ``loras_text`` 只写「名称|权重|是否启用」（默认启用/权重），
        真正的文件名、是否「仅模型」、关键词、预设提示词都来自全局 LoRA 库
        （按名称匹配）。组装时把两者合并成完整配置；若某名称在库里找不到，
        则仅用工作流里的有限信息（model_name 空，注入时会告警）。
        """
        lib = {(l.get("name") or "").strip(): l for l in self._lora_library()}
        text = (wf.get("loras_text") or "").strip()
        base = self._parse_loras_text(text) if text else (wf.get("loras", []) or [])
        merged: list[dict] = []
        for l in base:
            name = (l.get("name") or "").strip()
            if not name:
                continue
            lib_l = lib.get(name)
            if lib_l:
                merged.append(
                    {
                        "name": name,
                        "model_name": (lib_l.get("model_name") or "").strip(),
                        "model_only": bool(lib_l.get("model_only", True)),
                        "base_model": (lib_l.get("base_model") or "").strip() or (l.get("base_model") or "").strip(),
                        "weight": float(l.get("weight", 1.0)),
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
                        "weight": float(l.get("weight", 1.0)),
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
                logger.warning(f"[LoRA] 预设引用：库里找不到 LoRA「{lora_name}」，跳过预设")
                continue
            found = None
            for p in self._parse_presets(l.get("presets")):
                if (p.get("name") or "").strip() == (preset_name or "").strip():
                    found = p
                    break
            if not found:
                logger.warning(
                    f"[LoRA] 预设引用：LoRA「{lora_name}」下找不到预设「{preset_name}」，跳过"
                )
                continue
            pr = (found.get("prompt") or "").strip()
            if pr:
                pos_parts.append(pr)
            logger.info(
                f"[LoRA] 应用预设：{lora_name}-{preset_name}"
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
    def _serialize_loras_text(loras: list[dict]) -> str:
        """将 LoRA 列表序列化回 名称|权重|0/1|底模 文本（用于 loraon/loraoff 持久化）。"""
        lines = []
        for l in loras:
            name = (l.get("name") or "").strip()
            if not name:
                continue
            weight = l.get("weight", 1.0)
            wstr = str(int(weight)) if float(weight) == int(weight) else str(weight)
            enabled = 1 if l.get("enabled", False) else 0
            bm = (l.get("base_model") or "").strip()
            if bm:
                lines.append(f"{name}|{wstr}|{enabled}|{bm}")
            else:
                lines.append(f"{name}|{wstr}|{enabled}")
        return "\n".join(lines)

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
    def _strip_command(message_str: str, cmd: str) -> str:
        """从消息文本中去掉命令触发词（如 /draw），返回剩余参数文本。"""
        text = (message_str or "").strip()
        parts = text.split(None, 1)
        if not parts:
            return ""
        first = parts[0]
        if first.lower().endswith(cmd.lower()) or first.lower() == cmd.lower():
            return parts[1].strip() if len(parts) > 1 else ""
        return text

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
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id, prompt=prompt
            )
            out = getattr(llm_resp, "completion_text", "") or ""
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
            "你是动漫（Anima）生图提示词专家。用户会给你一段对画面的描述"
            "（可能是中文、英文或中英混杂，也可能是结构化文本），请你理解其含义，"
            "改写为一张可以直接交给 Anima 动漫模型的英文提示词。要求：\n"
            "1. 全部用英文，输出 Danbooru 风格标签（如 1girl, solo, white dress, "
            "long hair, blue eyes, masterpiece, best quality），用英文逗号分隔；\n"
            "2. 忠实反映描述里的人物、外观、服装、场景、动作、表情等核心信息，"
            "不要臆造描述里没有的内容；\n"
            "3. 如果是已经很合适的英文标签，直接精简整理后输出，不要啰嗦重复；\n"
            "4. 只输出提示词本身，不要任何解释、不要序号、不要代码块、不要中文。\n\n"
            f"画面描述：\n{text}\n\n"
            "改写后的英文 Anima 提示词："
        )
        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id, prompt=prompt
            )
            out = getattr(llm_resp, "completion_text", "") or ""
        except Exception as e:
            raise RuntimeError(f"LLM 改写失败: {e}") from e
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
        logger.info(f"[翻译] Anima 工作流，翻译模式={mode}，仅翻译中文片段")
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
                    logger.warning(f"[翻译] 片段「{seg}」翻译失败，保留原文: {e}")
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
                            f"[绘图] 工作流别名命中：{alias!r} → {wf_name or '(未命名)'}"
                        )
                        return wf_name or name
        return name

    def _resolve_workflow(
        self,
        name: str | None = None,
        is_img2img: bool = False,
        fallback_on_missing: bool = False,
        positive: str = "",
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
            # 未指定工作流时，先按提示词语义判断「真人/动漫」，命中则用对应默认工作流；
            # 语义不明才按「风格优先级 + 文生图/图生图」选全局默认。
            _sem = self._detect_style_from_prompt("" if positive is None else str(positive))
            if _sem == "real":
                _cand = (
                    self._cfg("default_img2img_workflow_real", "")
                    if is_img2img
                    else self._cfg("default_workflow_real", "")
                ) or self._cfg("default_workflow_real", "")
                if _cand:
                    name = _cand
                    logger.info(f"[绘图] 提示词含「真人/写实」语义，选用真人工流={name}")
            elif _sem == "anime":
                _cand = (
                    self._cfg("default_img2img_workflow", "")
                    if is_img2img
                    else self._cfg("default_workflow", "")
                ) or self._cfg("default_workflow", "")
                if _cand:
                    name = _cand
                    logger.info(f"[绘图] 提示词含「动漫/二次元」语义，选用动漫工作流={name}")
            if not name:
                name = self._pick_default_workflow_name(is_img2img)
                logger.info(
                    f"[绘图] 未指定工作流，按风格优先级={self._cfg('default_style_priority', 'anime')} "
                    f"{'图生图' if is_img2img else '文生图'}选定默认工作流={name or '（均无配置，回退第一个）'}"
                )
        if name:
            # 1) 精确匹配工作流名称
            for w in workflows:
                if w.get("name") == name:
                    return w
            # 2) 大小写不敏感 + 去首尾空格匹配名称（AI 常把 Default 写成 default 等）
            name_trim = name.strip()
            name_lower = name_trim.lower()
            for w in workflows:
                n = (w.get("name") or "").strip()
                if n.lower() == name_lower:
                    return w
            # 3) 回退：按文件名匹配（解决 LLM 把文件名当工作流名的问题）
            for w in workflows:
                fn = (w.get("workflow_name") or "").strip().lower()
                if not fn:
                    continue
                # 精确文件名（如 "sd.json"）
                if fn == name_lower:
                    return w
                # 去掉 .json 后缀匹配（如 "sd" 匹配 "sd.json"）
                if fn.endswith(".json") and fn[:-5] == name_lower:
                    return w
                # 加上 .json 后缀匹配（如 "sd.json" 匹配 "sd"）
                if not fn.endswith(".json") and fn + ".json" == name_lower:
                    return w
            # 全部失败
            avail = "、".join((w.get("name") or "(未命名)") for w in workflows)
            if not fallback_on_missing:
                raise ValueError(f"找不到名为「{name}」的工作流。可用工作流：{avail}。")
            # 容错回退：按「风格优先级 + 文生图/图生图」默认工作流，未配置则第一个
            fallback = self._pick_default_workflow_name(is_img2img)
            if fallback:
                for w in workflows:
                    if (
                        w.get("name") == fallback
                        or (w.get("workflow_name") or "").strip().lower() == fallback.strip().lower()
                    ):
                        logger.warning(
                            f"[绘图] 找不到工作流「{name}」（可用：{avail}），"
                            f"容错回退到默认工作流「{w.get('name') or fallback}」"
                        )
                        return w
                logger.warning(
                    f"[绘图] 找不到工作流「{name}」，且默认工作流「{fallback}」也未匹配，回退第一个"
                )
                return workflows[0]
            logger.warning(
                f"[绘图] 找不到工作流「{name}」且未配置默认工作流，回退第一个（可用：{avail}）"
            )
            return workflows[0]
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
            f"[取图] 开始：消息组件共 {len(comps)} 个 -> "
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
                    f"[取图] 发现引用消息 Reply(id={getattr(comp, 'id', None)})，"
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
                logger.info(f"[取图] 成功 [{src}] -> {p}")
            elif p:
                logger.info(f"[取图] 跳过重复 [{src}] -> {p}")
            else:
                logger.warning(f"[取图] 失败 [{src}] 无法解析为本地路径")

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
                logger.info(f"[取图] 引用图解析失败，兜底最近用户发的图: {paths}")

        if paths:
            logger.info(f"[取图] 完成：共取得 {len(paths)} 张图片")
        else:
            logger.info("[取图] 消息/引用/卡片内均未取到图片（本方法不兜底历史生成图）")
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
                lines.append(f"[拆prompt][DBG] {tag}段{i // 400}: {seg}")
            return lines

        logger.debug(
            f"[拆prompt][DBG] 输入长度={len(text)} "
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
            logger.debug("[拆prompt][DBG] === 走分支1(有Negative标记) 过滤后正向提示词 ===")
            for ln in _dbg_block("过滤后", positive):
                logger.debug(ln)
            # 负面直接删除（不保留，回退到调用方自行提供的 negative_prompt）
            return positive, ""

        # 2) 无 'Negative prompt:' 标记：兜底处理方括号标题 + 内联负向软信号。
        #    未命中软信号则原样返回（不误伤常规 /draw 与 AI 对话的自然语言描述）。
        positive = _cut_inline_negative(text)
        positive = ComfyUIDrawPlugin._clean_prompt_markers(positive)
        logger.debug("[拆prompt][DBG] === 走分支2(无Negative标记) 过滤后正向提示词 ===")
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

    @staticmethod
    def _format_companion_prompt(raw: str) -> tuple[str, str]:
        """针对「我会永远陪着你」伴侣插件的生图提示词做专属格式化与过滤。

        伴侣传来的整段含 Positive/Negative 分段、分节标题、'[section compacted]' 占位符，
        以及大量与出图无关的事实描述（时间/日程/位置/情绪等）和元指令。这里只抽取对
        出图真正有用的部分，采用「白名单段保留」策略：保留那些承载 SD 风格标签的节，
        丢弃纯事实 / 元指令段。

        保留的节（标题大小写不敏感，方括号可有可无）：
        - user request / [User image request]：用户原始出图诉求（首行）
        - additional visual recognition notes：角色外观识别要点（狐娘人设等）
        - additional outfit preference：穿搭偏好（daily_outfit_photo_prompt 落在此处）
        - visual continuity reference：跨轮视觉连续性参考
        - [Composition and continuity]：构图连续性标准 SD 标签
        负向段（Negative prompt:）单独保留，去掉其中的 'Do not ...' 元指令与占位符。

        其余噪声（场景事实、分节标题、元指令、截断占位符）一律丢弃。
        """
        if not raw:
            return "", ""
        # 1) 先按 Negative prompt: 切分正/负原始段
        m = re.search(r"negative\s*prompt\s*[:：]", raw, re.IGNORECASE)
        pos_raw = raw[: m.start()].strip() if m else raw.strip()
        neg_raw = raw[m.end():].strip() if m else ""

        # 2) 正向：白名单段抽取
        # 各保留节的标题正则（允许带或不带方括号）
        keep_sections = [
            r"user\s*image\s*request",
            r"user\s*request",
            r"additional\s*visual\s*recognition\s*notes",
            r"additional\s*outfit\s*preference",
            r"visual\s*continuity\s*reference",
            r"composition\s*and\s*continuity",
        ]
        # 用统一正则把正向段按标题切成 (标题, 内容) 块
        split_pat = re.compile(
            r"(?:^|\n)\s*\[?\s*("
            + "|".join(keep_sections)
            + r")\s*\]?\s*[:：]?\s*\n",
            re.IGNORECASE,
        )
        # 先把正向段按标题切分；没有命中任何白名单标题的零散首行视为 user request 首行
        chunks: list[str] = []

        # 提取所有白名单块
        matches = list(split_pat.finditer(pos_raw))
        if matches:
            for i, mt in enumerate(matches):
                start = mt.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(pos_raw)
                content = pos_raw[start:end].strip()
                content = re.sub(r"\[\s*section\s*compacted\s*\]", " ", content, flags=re.IGNORECASE)
                content = re.sub(r"\s+", " ", content).strip()
                if content:
                    chunks.append(content)
            # 首个白名单块之前、且不以白名单标题开头的文本，当作 user request 首行补上
            head = pos_raw[: matches[0].start()].strip()
            if head:
                head = re.sub(r"\[\s*section\s*compacted\s*\]", " ", head, flags=re.IGNORECASE)
                head = re.sub(r"\s+", " ", head).strip()
                # 仅取首行，避免把分节标题后残留的噪声带进来
                head_first = head.split("\n")[0].strip()
                if head_first and not re.search(r"user\s*request", head_first, re.IGNORECASE):
                    chunks.insert(0, head_first)
        else:
            # 没有任何白名单标题：退回旧逻辑，取首行作为 user request
            um = re.search(r"user\s*request\s*[:：]\s*(.+)", pos_raw, re.IGNORECASE)
            if um:
                chunk = um.group(1).split("\n")[0].strip()
            else:
                chunk = pos_raw.split("\n")[0].strip()
            chunk = re.sub(r"\[\s*section\s*compacted\s*\]", " ", chunk, flags=re.IGNORECASE)
            chunk = re.sub(r"\s+", " ", chunk).strip()
            if chunk:
                chunks.append(chunk)

        positive = ", ".join(p for p in chunks if p)

        # 2.5) 中文保护：伴侣 prompt 里用户的**中文描图**往往不在白名单英文标题段内
        # （例如裸中文段落、[Scene, style and final preset] 等非白名单段里的中文）。
        # 白名单策略只保留「带英文标题」的段，会把这些中文整体丢弃——这是 bug。
        # 这里兜底：扫描所有未被白名单块覆盖、含中文且非方括号标题行的内容，追加保留。
        # 原则：**中文是用户出图意图核心，绝不丢；纯英文事实段仍按白名单丢弃**。
        if re.search(r"[\u4e00-\u9fff]", pos_raw):
            # 先把已收集 chunk 拼成一段用于「去重判断」（避免重复追加同一行）
            used_blob = "\n".join(chunks)
            for raw_line in pos_raw.split("\n"):
                line = raw_line.strip()
                if not line:
                    continue
                if not re.search(r"[\u4e00-\u9fff]", line):
                    continue  # 纯英文/数字行：按白名单策略，不兜底保留
                if line.startswith("["):
                    continue  # 方括号标题行（含中文标题）本身不是描图，跳过
                # 已作为某 chunk 一部分存在则跳过（允许子串，避免重复）
                if line in used_blob:
                    continue
                chunks.append(line)
            positive = ", ".join(p for p in chunks if p)

        # 3) 负向：取 Negative prompt 区块，去掉元指令与占位符
        neg = neg_raw
        neg = re.sub(r"\[\s*section\s*compacted\s*\]", " ", neg, flags=re.IGNORECASE)
        neg = re.sub(r"do\s+not\s+[^\n;；]*", " ", neg, flags=re.IGNORECASE)
        neg = re.sub(r"\bdup\b", " ", neg, flags=re.IGNORECASE)
        neg = re.sub(r"\[[^\]]*?\s.+?\]", " ", neg)
        negative = re.sub(r"\s+", " ", neg).strip()

        return positive, negative

    # ------------------------------------------------------------------ #
    # 核心：提交并等待出图（异步生成器，yield 消息）
    # ------------------------------------------------------------------ #
    async def _send(self, event: AstrMessageEvent, text: str) -> None:
        """主动发送一条文本消息（不占用 yield，避免命令 pipeline 在首个
        yield 后中断；同时标记 _has_send_oper，防止触发后续 LLM 阶段）。"""
        await event.send(MessageChain([Plain(str(text))]))

    async def _send_display(self, event: AstrMessageEvent, text: str) -> None:
        """按图库配置的展示方式发送展示内容。

        gallery.display_mode == "render" 时，优先用 AstrBot 自带的文本转图片服务
        (text_to_image，官方 HTML 模板渲染美观、清晰)；若该服务不可用 / 返回空 /
        发送异常，再用本插件内置的 Pillow 渲染做兜底（仅防止完全无图可发），再失败回退文字。
        其他值（默认 text）直接发送文字。
        """
        if str(self._cfg("gallery", {}).get("display_mode", "text")).strip().lower() == "render":
            # 1) 优先：AstrBot text_to_image 官方渲染服务（模板漂亮、清晰）
            try:
                url = await self.text_to_image(text)
                if url:
                    # AstrBot 的 Image 组件第一个必填参数是 file（不是 url）。
                    # text_to_image 返回的是本地路径，用 fromFileSystem；http(s) 才用 fromURL。
                    if url.startswith("http://") or url.startswith("https://"):
                        img_comp = Image.fromURL(url)
                    else:
                        img_comp = Image.fromFileSystem(url)
                    await event.send(MessageChain([img_comp]))
                    return
                self.logger.warning("[图库] AstrBot 渲染服务返回空 URL，改用 Pillow 兜底")
            except Exception as _e:
                try:
                    self.logger.warning(f"[图库] AstrBot 渲染失败，改用 Pillow 兜底: {_e}")
                except Exception:
                    pass
            # 2) 兜底：本插件内置 Pillow 渲染（仅在 AstrBot 服务不可用时）
            render_path = self._render_gallery_text_pillow(text)
            if render_path:
                try:
                    await event.send(MessageChain([Image.fromFileSystem(render_path)]))
                    return
                except Exception as _e:
                    self.logger.warning(f"[图库] Pillow 兜底渲染图发送失败，回退文字: {_e}")
            # 3) 最终回退：文字
            await self._send(
                event,
                text + "\n\n⚠ 渲染成图片失败（AstrBot 文本转图片服务不可用，Pillow 兜底也未成功），已回退文字。请确认 AstrBot「文本转图片」服务已启用并选择了激活模板。",
            )
            return
        await self._send(event, text)

    def _render_gallery_text_pillow(self, text: str, font_size: int = 22) -> str | None:
        """用 Pillow 把图库展示文字绘制成高清图片（解决 AstrBot 默认 t2i 字小发虚）。

        做法：2x 超采样（先在大尺寸画布上用大字号绘制，再缩放回目标尺寸）得到抗锯齿清晰字；
        白底深灰字，按字符宽度自动换行，兼容中英文；输出 PNG 到 data_dir 下的临时渲染目录。
        返回图片路径；Pillow 不可用 / 绘制失败 / 字体缺失时返回 None（调用方回退）。
        """
        if _PILImage is None:
            return None
        try:
            from PIL import ImageDraw, ImageFont

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
                    font = ImageFont.truetype(_cand, font_size)
                    break
                except Exception:
                    continue
            if font is None:
                try:
                    font = ImageFont.load_default()
                except Exception:
                    return None

            scale = 2  # 超采样倍数
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
                self.logger.warning(f"[图库] Pillow 渲染失败: {_e}")
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
        self, event, positive, wf, is_img2img, ref_sha256, draw_start, reason
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
            )
        except Exception as e:
            logger.warning(
                f"[图库] 写入失败记录出错（忽略）: {e} | "
                f"wf={type(wf).__name__} event={type(event).__name__}",
                exc_info=True,
            )

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
            f"[绘图失败]{tag} {type(exc).__name__}: {exc}", exc_info=True
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
        notify_pending: bool = True,
        source: str = "",
    ):
        # 记录最近一次事件，供 LLM 工具在 event 异常时为兜底使用
        self._last_event = event
        # 出图计时起点（用于生成完成后的耗时报告）
        _draw_start = time.time()
        # 图生图参考图的 sha256（归档成品图时回填到 ref_sha256 字段）
        ref_sha256 = None
        # 用户标识（成品图归档用）：用 get_sender_id() 取真实用户ID，避免归档成"无主图"
        user_id = (getattr(event, "get_sender_id", lambda: "")() or "") if event is not None else ""
        user_name_fn = getattr(event, "get_sender_name", None) if event is not None else None
        user_name = (user_name_fn() if callable(user_name_fn) else "") or ""
        # 发图白名单：allow_draw_users 非空时，非白名单用户直接拒绝，不进入生图流程。
        # 空名单 = 所有用户都允许（含未识别到 user_id 的情况）。
        if not self._is_draw_allowed(user_id):
            logger.info(f"[绘图] 用户 {user_id or '(unknown)'} 不在发图白名单，拒绝绘图")
            await self._send(event, "抱歉，你没有发图权限哦～ 如需使用绘图功能请联系管理员。")
            return
        # 生图次数限制：全局/按用户配额校验（管理员可豁免）
        _ok, _reason = self._check_draw_limit(event)
        if not _ok:
            logger.info(f"[绘图] 用户 {user_id or '(unknown)'} 触发生图限额，拒绝：{_reason}")
            await self._send(event, _reason)
            return
        if not positive or not positive.strip():
            await self._send(event, "请提供正向提示词，例如：/draw 一只白色水手服少女")
            return

        try:
            # fallback_on_missing=True：绘图真正入口可能收到伴侣/LLM 传入的无效工作流名
            # （如 "ComfyUI default"），此时不报错中断，容错回退到配置的默认工作流。
            wf = self._resolve_workflow(workflow_name, is_img2img=is_img2img, fallback_on_missing=True, positive=positive)
            logger.info(
                f"[绘图] 解析工作流：请求名={workflow_name!r}, is_img2img={is_img2img}, "
                f"实际选用工作流={wf.get('name')!r}（server={wf.get('server_name')!r}）"
            )
            server = self._resolve_server(wf.get("server_name") or None)
        except ValueError as e:
            # 配置类问题：原因是插件自己给出的可读文案，直接说明
            logger.warning(f"[绘图失败][配置] {e}")
            await self._send(event, f"绘图配置有误：{e} 请联系管理员调整。")
            return

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
                        logger.info(f"[图库] 已归档参考图: {_ri} -> {_final}")
                except Exception as _re:
                    logger.warning(f"[图库] 参考图归档失败（不影响出图）: {_re}")

        # Anima 工作流提示词处理（source 非空 = 第三方插件调用，如伴侣插件）：
        # - 第三方插件调用：用 LLM 把传入的描述（可能中英混杂/结构化文本）改写为
        #   纯英文 Anima 提示词格式，避免被 api/danbooru 翻译破坏结构。
        # - 原生调用（source 为空）：仅当提示词含中文时按翻译模式处理中文片段。
        if wf.get("is_anima"):
            if source:
                try:
                    rewritten = await self._rewrite_to_anima_llm(positive)
                    if rewritten and rewritten.strip():
                        positive = rewritten
                        logger.info(f"[Anima] 第三方插件调用，LLM 改写为 Anima 提示词: {positive}")
                except Exception as e:
                    logger.warning(f"[Anima] LLM 改写失败，保留原提示词: {e}")
            elif self._has_chinese(positive):
                translated = await self._translate_prompt(wf, positive)
                if translated:
                    positive = translated
                    logger.info(f"Anima 提示词翻译结果: {positive}")

        # 注入 LoRA 预设提示词（--名称/预设名）：追加到正/负向提示词
        if lora_presets:
            positive, negative = self._apply_lora_presets(lora_presets, positive, negative)

        # 注入提示词（正/负下输入框名固定为 text，无需配置）
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
        w = width or int(wf.get("default_width", 512) or 512)
        h = height or int(wf.get("default_height", 512) or 512)
        if init_images:
            res_node = None
        else:
            res_node = wf.get("resolution_node") or ""
            if not res_node:
                # 未配置宽高节点时自动探测 EmptyLatentImage
                res_node = workflow_builder.find_node_by_class(
                    prompt, "EmptyLatentImage"
                )
        width_field = wf.get("resolution_width_field", "width") or "width"
        height_field = wf.get("resolution_height_field", "height") or "height"
        if res_node:
            workflow_builder.set_number_node(prompt, res_node, width_field, w)
            workflow_builder.set_number_node(prompt, res_node, height_field, h)

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
            active_map = lora_map
        logger.info(f"LoRA active_map（本次实际请求启用）: {active_map}")

        # 补全：--名称 临时请求的 LoRA，若工作流未预引用（loras_config 里没有该项），
        # 则从全局 LoRA 库里取完整配置（含真实 model_name）补进 loras_cfg。否则
        # apply_loras 因无配置项可遍历而不会注入任何节点，表现为「LoraLoader 节点: 无 /
        # 本次最终启用: 无」——这正是「/draw --安魂曲 没加上」的根因（工作流没引用安魂曲）。
        if active_map:
            lib = {(l.get("name") or "").strip(): l for l in self._lora_library()}
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
                                float(lib_l.get("weight", 1.0))
                                if w is None
                                else float(w)
                            ),
                            "enabled": True,
                            "load_node": "",
                        }
                    ]
                    logger.info(
                        f"[LoRA] 从全局 LoRA 库补全临时启用的「{cmd_name}」"
                        f"（工作流未预引用；文件={lib_l.get('model_name')}）"
                    )
                else:
                    logger.warning(
                        f"【LoRA 提示】本次请求启用「{cmd_name}」，但工作流未引用且全局 LoRA 库"
                        f"里也找不到该名称。请先在全局「LoRA 库」配置「{cmd_name}」并填好"
                        f"model_name（真实 .safetensors 文件名），否则无法注入。"
                    )

        # 常驻预设：启用的 LoRA 若配置了名为「0」的预设，则无论用户是否指定其它
        # 预设（--名称/预设名）都自动带上。先排除用户已显式指定「0」的，避免重复。
        if active_map:
            always_pre: dict[str, str] = {}
            lib_pre = {(l.get("name") or "").strip(): l for l in self._lora_library()}
            for lora_name in active_map:
                ln = (lora_name or "").strip()
                l = lib_pre.get(ln)
                if not l:
                    continue
                for p in self._parse_presets(l.get("presets")):
                    if (p.get("name") or "").strip() == "0":
                        if not (lora_presets and (lora_presets.get(ln) or "").strip() == "0"):
                            always_pre[ln] = "0"
                        break
            if always_pre:
                positive, negative = self._apply_lora_presets(
                    always_pre, positive, negative
                )
                logger.info(f"[LoRA] 已追加常驻预设（名为0）：{list(always_pre.keys())}")
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
                    logger.info(f"[LoRA] 启用 {nm} → {mn}")
                else:
                    logger.warning(
                        f"[LoRA] 启用 {nm} → 未配置 model_name，节点沿用工作流默认文件（可能不是该 LoRA）"
                    )

        # 随机化种子（未指定 --seed 时），避免每次出图完全相同
        seeds_used = workflow_builder.randomize_seed(prompt, seed)
        if seeds_used:
            logger.info(f"本次种子: {seeds_used}")

        # 注入 denoise（降噪幅度/重绘强度）
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

        # 调试用：打印最终提交给 ComfyUI 的工作流（拼接结果），便于核对 LoRA 注入/禁用是否正确
        logger.info(
            "最终工作流（提交给 ComfyUI）:\n"
            + json.dumps(prompt, ensure_ascii=False, indent=2)
        )

        # 提交到 ComfyUI（client 已在工作流加载时创建）
        srv_key = self._server_key(server)
        try:
            try:
                result = await client.queue_prompt(prompt)
                prompt_id = result.get("prompt_id")
            except Exception as e:
                await self._send(event, self._friendly_error(e, "提交任务"))
                return

            if not prompt_id:
                logger.warning("[绘图失败][提交] ComfyUI 未返回 prompt_id")
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
                logger.info(f"[队列] 中转站 X-Queue-Position={ahead}（来自响应头）")
            else:
                ahead = self._local_queue_ahead(srv_key)
                logger.info(f"[队列] 无中转站 X-Queue-Position 响应头，回退本地队列 ahead={ahead}")
            try:
                self._local_queue_add(srv_key, prompt_id)
                # 提交后统一发一条提示：无队列（ahead<=0）→「稍等，马上来」；
                # 有队列（ahead>0）→「前面排着 N 个」。只发这一条，避免与提交前
                # 提示重复。伴侣 proactive（notify_pending=False）不发。
                if self._cfg("return_queue_position", True) and notify_pending:
                    await self._send(event, self._queue_hint(ahead))

                # 等待出图：动态超时 = 基础超时 + 前面排队任务累加预估耗时。
                # 排得越靠后，前面任务越多，等待就越久，故按 ahead 逐任务累加，
                # 避免排在长队后面的任务因固定超时过早被误判为失败。
                base_timeout = int(self._cfg("draw_timeout", 120))
                # 每个前面排队任务额外累加的秒数；默认按"每个任务都要完整基础超时"保守估算
                per_extra = int(self._cfg("queue_extra_timeout", 0)) or base_timeout
                max_timeout = int(self._cfg("max_draw_timeout", 0)) or (base_timeout + 30 * base_timeout)
                timeout = min(max_timeout, base_timeout + ahead * per_extra)
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
                    logger.warning(f"[绘图失败][超时] 等待 {timeout} 秒仍无结果，prompt_id={prompt_id}")
                    await self._send(event, self._cute("timeout"))
                    self._record_failed(
                        event, positive, wf, is_img2img, ref_sha256,
                        _draw_start, f"等待 {timeout} 秒仍无结果（超时）",
                    )
                    return

                images = comfyui_client.extract_images(history, wf.get("output_node"))
                if not images:
                    logger.warning("[绘图失败][无图] 任务完成但未找到输出图片节点")
                    await self._send(event, self._cute("no_image"))
                    self._record_failed(
                        event, positive, wf, is_img2img, ref_sha256,
                        _draw_start, "任务完成但未找到输出图片节点（无图）",
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
                            _final = self.gallery.archive_image(
                                img_path,
                                source=SRC_GEN,
                                prompt=positive,
                                prompt_raw=positive,
                                workflow=(wf.get("name") or ""),
                                loras=enabled,
                                seed=(seeds_used[0] if seeds_used else None),
                                w=_real_w,
                                h=_real_h,
                                denoise=(denoise if is_img2img else None),
                                is_img2img=bool(is_img2img),
                                ref_sha256=(ref_sha256 or ""),
                                size_bytes=(os.path.getsize(img_path) if os.path.exists(img_path) else None),
                                cost_sec=(time.time() - _draw_start),
                                user_id=user_id,
                                user_name=user_name,
                                session_id=(getattr(event, "session_id", "") or ""),
                                trigger_msg=(getattr(event, "message_str", "") or ""),
                                status=0,
                            )
                            # archive_image 会把文件从 temp/ 移动到 gallery/，必须用
                            # 返回的最终路径继续发送/上报，否则会指向已不存在的临时文件。
                            if _final:
                                img_path = _final
                        except Exception as _ge:
                            logger.warning(f"[图库] 归档失败（不影响出图）: {_ge}")

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
                                logger.info(f"[出图] webp 已转 png 发送副本: {_send_img_path}")
                        except Exception as _e:
                            logger.warning(f"[出图] webp 转 png 发送副本失败（用原图发送）: {_e}")
                    # LLM 工具 llm_draw 额外用本地路径拼 JSON 返回（供伴侣插件解析为图片）。
                    yield event.image_result(_send_img_path), _send_img_path

                    # 生图成功：记录配额（总次数 + 当前小时次数）
                    self._record_draw_used(event)

                    # 出图完成后的贴心小报告：文件时间、尺寸、耗时（随机萌文案）。
                    # 受配置 show_draw_report 控制（默认关闭，关闭则不输出文件信息）。
                    if self._cfg("show_draw_report", False):
                        try:
                            _st = os.stat(img_path)
                            _ftime = time.strftime(
                                "%m-%d %H:%M:%S", time.localtime(_st.st_mtime)
                            )
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
                            _cost = time.time() - _draw_start
                            await self._send(
                                event,
                                random.choice(_DRAW_DONE_HINTS).format(
                                    ftime=_ftime, wh=_wh, size=_size,
                                    cost=f"{_cost:.1f}",
                                ),
                            )
                        except Exception as _e:
                            logger.warning(f"[出图报告] 发送小报告失败（不影响出图）: {_e}")
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
        args = self._strip_command(event.message_str, "draw")
        prompt, lora_map, lora_presets, width, height, wf_name, seed, denoise = self._parse_draw_args(args or "")
        if not prompt.strip():
            await self._send(event, 
                "用法：/draw 一只白色水手服少女 --wf sd --lora catgirl:0.8 --w 768 --h 768 [--seed 12345]"
            )
            return
        # 若消息或引用(回复)里带了图片，则按图生图处理
        images = await self._extract_images(event)
        async for m, _p in self._do_draw(
            event, wf_name, prompt, "", width, height, lora_map, lora_presets, seed,
            init_images=images,
            is_img2img=bool(images),
            denoise=denoise,
        ):
            yield m
        # 收尾时再终止事件：避免开头 stop_event 导致 pipeline 在第一个 yield
        # 后中断 _do_draw 的协程（等待/下载图片的代码不再执行，temp 无图）。
        event.stop_event()

    @filter.command("img2img")
    async def cmd_img2img(self, event: AstrMessageEvent):
        """图生图：用附带的一张图片作为参考图重绘。用法：/img2img 描述 [--wf 工作流] [...]"""
        args = self._strip_command(event.message_str, "img2img")
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
            logger.info(f"[取图] /img2img 启用兜底图片: {images}")
        if not images:
            await self._send(
                event,
                "图生图需要附带一张参考图哦～ 请在消息里发一张图片，再加上你的描述，例如：/img2img 把背景换成星空",
            )
            event.stop_event()
            return
        async for m, _p in self._do_draw(
            event, wf_name, prompt, "", width, height, lora_map, lora_presets, seed,
            init_images=images,
            is_img2img=True,
            denoise=denoise,
        ):
            yield m
        event.stop_event()

    # 「画」系绘图指令（独立新增指令，非 /draw 别名）：
    #   /画 [工作流名] 提示词   用指定/默认工作流（如 /画 真人 一个女孩）
    #   /绘图 /绘画 /生图 /画图 /作画 /画画 提示词   均用默认工作流
    # 语法约定：触发词后必须跟空格再写内容（触发词紧贴其它字不视为指令，
    # 例如「画风成熟点」不会触发），以规避把闲聊误判为绘图指令。
    # 工作流名是可选的，且必须以空格与提示词分隔；若指定的工作流不存在，
    # 直接回复「xx 工作流不存在」并列出可用工作流，不再静默回退默认。
    # 与 /draw 并存，互不冲突。
    _DRAW_TRIGGER_PATTERN = r"^[/／]?(?:画|绘图|绘画|生图|画图|作画|画画)(?:\s+(.+))?$"

    @filter.regex(_DRAW_TRIGGER_PATTERN)
    async def cmd_draw_wf(self, event: AstrMessageEvent):
        """「画」系绘图指令（新增指令，非 /draw 别名）。

        用法：
          /画 提示词 [...]                      用默认工作流（如 /画 一个女孩）
          /画 工作流名 提示词 [...]             用指定工作流（如 /画 真人 一个女孩）
          /绘图|/绘画|/生图|/画图|/作画|/画画 提示词 [...]   用默认工作流
        工作流名可选、必须以空格与提示词分隔；首 token 若长度超过 10 字则视为
        提示词（用默认工作流），若长度 ≤10 但不是已知工作流会回复该工作流不存在并
        列出可用工作流。其余参数（--lora / --w / --h / --seed / --wf 等）与 /draw 完全一致。"""
        text = (event.message_str or "").strip()
        m = re.match(self._DRAW_TRIGGER_PATTERN, text, re.S)
        rest = (m.group(1) or "").strip() if m else ""
        if not rest:
            await self._send(event, random.choice(_WF_HINTS["no_arg"]).format(wf="默认"))
            event.stop_event()
            return
        # 自然语言帮助：触发词后跟「帮助/说明/怎么用/咋用/help」等，复用 /drawhelp 输出
        if re.match(r"^(?:帮助|说明|怎么用|咋用|help)$", rest.strip().lower()):
            await self.cmd_help(event)
            event.stop_event()
            return
        # 尝试把 rest 首 token 当作可选工作流名。规则：
        #  - 首 token 长度 > 10（多半是用户直接写提示词，只是恰好开头像工作流名）
        #    → 不解析为工作流，整句当作提示词用默认工作流。
        #  - 首 token 长度 ≤ 10，但确为已知工作流 → 拆出作为指定工作流名。
        #  - 首 token 长度 ≤ 10，且不是已知工作流 → 直接回复「没有该工作流」并列出可用，
        #    不再静默回退当提示词（例如「画 真人 一个女孩」中真人拼错就明确报错）。
        MAX_WF_NAME_LEN = 10
        wf_specified = None
        parts = rest.split(None, 1)
        first_tok = parts[0]
        if len(first_tok) > MAX_WF_NAME_LEN:
            rest_for_parse = rest
        else:
            try:
                self._resolve_workflow(first_tok)
                wf_specified = first_tok
                rest_for_parse = parts[1] if len(parts) > 1 else ""
            except ValueError as e:
                await self._send(event, str(e))
                event.stop_event()
                return
        prompt, lora_map, lora_presets, width, height, wf_arg, seed, denoise = self._parse_draw_args(rest_for_parse)
        # 工作流优先级：显式 --wf > 首 token 推断的工作流名 > 默认
        wf_name = wf_arg or wf_specified
        if not prompt.strip():
            await self._send(event, random.choice(_WF_HINTS["no_arg"]).format(wf=wf_name or "默认"))
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
    @filter.command("loralist")
    async def cmd_loralist(self, event: AstrMessageEvent):
        """列出当前工作流可配置的 LoRA 及其启用状态。可用 --wf 指定工作流。"""
        args = self._strip_command(event.message_str, "loralist")
        wf_name = None
        m = re.search(r"--wf\s+(\S+)", args or "")
        if m:
            wf_name = m.group(1)
        try:
            wf = self._resolve_workflow(wf_name)
        except ValueError as e:
            await self._send(event, str(e))
            return
        loras = self._loras_of(wf)
        if not loras:
            await self._send(event, f"工作流「{wf.get('name')}」未配置任何 LoRA。")
            return
        wf_bm = (wf.get("base_model") or "").strip()
        lines = [f"工作流「{wf.get('name')}」的 LoRA 列表："]
        if wf_bm:
            lines[0] += f"（底模：{wf_bm}）"
        for l in loras:
            state = "启用" if l.get("enabled") else "禁用"
            model = l.get("model_name") or ""
            mo = l.get("model_only", True)
            presets = l.get("presets") or []
            preset_names = [ (p.get("name") or "").strip() for p in presets if (p.get("name") or "").strip() ]
            lora_bm = (l.get("base_model") or "").strip()
            match_tag = ""
            if wf_bm and lora_bm and lora_bm != wf_bm:
                match_tag = f"，⚠底模不匹配（LoRA={lora_bm}）"
            elif lora_bm:
                match_tag = f"，底模 {lora_bm}"
            lines.append(
                f"- {l.get('name')}（{state}，权重 {l.get('weight', 1.0)}"
                + (f"，仅模型" if mo else "，模型+CLIP")
                + (f"，文件 {model}" if model else "，⚠未配置文件名")
                + match_tag
                + "）"
                + (f"\n    预设：{', '.join(preset_names)}" if preset_names else "")
            )
        await self._send(event, "\n".join(lines))
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
                lib = {(x.get("name") or "").strip(): x for x in self._lora_library()}
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
            logger.error(f"[LoRA 开关] 操作失败: {type(e).__name__}: {e}", exc_info=True)
            await self._send(event, "保存 LoRA 设置时出错，请稍后再试或联系管理员。")
            return
        state = "启用" if enabled else "禁用"
        await self._send(event, f"已将 LoRA「{name}」{state}（已保存）。")

    # ------------------------------------------------------------------ #
    # 指令：/queuestatus 查询队列
    # ------------------------------------------------------------------ #
    @filter.command("queuestatus")
    async def cmd_queuestatus(self, event: AstrMessageEvent):
        """查询本地队列状态，以及你最近一次任务前面还有多少位。可用 --wf 指定服务器所在工作流。"""
        args = self._strip_command(event.message_str, "queuestatus")
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
    # 指令：/workflows 列出/选择默认工作流
    # ------------------------------------------------------------------ #
    @filter.command("workflows")
    async def cmd_workflows(self, event: AstrMessageEvent):
        """列出工作流，或设置默认工作流：/workflows set 动漫文生图 | set_real 真人文生图 | set_img2img 动漫图生图 | set_img2img_real 真人图生图"""
        args = self._strip_command(event.message_str, "workflows")
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
            anima = " [Anima]" if w.get("is_anima") else ""
            lines.append(f"- {wname}{anima}{tag}")
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
            "/绘图 | /绘画 | /生图 | /画图 | /作画 | /画画 提示词 [...]   以上均用默认工作流作画（如 /绘图 一个女孩）\n"
            "  · 以上任意中文触发词后跟「帮助/说明/怎么用」（如「画画帮助」「作图帮助」「绘图帮助」）也会显示本帮助。\n"
            "/loralist [--wf 工作流]   列出 LoRA（含预设）\n"
            "/loraon 名称 [--wf 工作流]  启用 LoRA（持久化到工作流默认列表）\n"
            "/loraoff 名称 [--wf 工作流] 禁用 LoRA（持久化）\n"
            "/queuestatus [--wf 工作流]  查看队列与排队位置\n"
            "/workflows [set 名称 | set_img2img 名称]   列出/设置默认工作流（set 设置文生图默认，set_img2img 设置图生图默认）\n"
            '也可直接对 AI 说"画一只猫，使用 xxx lora"，由 AI 自动调用绘图工具。'
        )
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
            logger.warning(f"[图库] 提取当前消息图片失败: {_e}")
        # 2) / 3) 本会话生成 / 收到（ImageStore.resolve_ref 处理）
        return self.gallery.resolve_ref(event, event.session_id or "")

    def _is_admin(self, event) -> bool:
        return bool(getattr(event, "is_admin", lambda: False)())

    def _is_draw_allowed(self, user_id: str) -> bool:
        """发图白名单校验：allow_draw_users 配置非空时，仅列表内的用户可绘图/发图。

        配置为逗号或换行分隔的用户 ID 列表，留空表示所有用户都允许
        （包括未识别到 user_id 的情况，避免误伤）。
        """
        if not user_id:
            return True
        whitelist = (self._cfg("allow_draw_users", "") or "").strip()
        if not whitelist:
            return True
        allowed = {
            x.strip()
            for x in re.split(r"[,，\n\r]+", whitelist)
            if x.strip()
        }
        return user_id in allowed

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
        path = self.gallery.path_of(sha)
        if not path:
            await self._send(event, f"没找到这张图（sha={sha[:16]}），可能已被清理或从未入库。")
            return False
        try:
            # 必须包成 MessageChain 再 send：AstrBot 新版 event.send 期望消息链，
            # 直接传裸 Image 组件在 comfyui_gallery 工具场景会报
            # "'Image' object has no attribute 'chain'"。
            await event.send(MessageChain([Image(file=path)]))
            self.gallery.send(sha)
            return True
        except Exception as _e:
            logger.warning(f"[图库] 发图失败: {_e}")
            await self._send(event, "这张图文件丢失了，可能已被 LRU 清理。")
            return False

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
            "帮助": "help", "怎么用": "help", "说明": "help",
            "全部": "all", "全库": "all", "所有": "all",
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

        # 跨会话范围（关闭 cross_session 时仅当前会话）
        session_scope = None if self._cfg("gallery", {}).get("cross_session") else (event.session_id or "")
        # 用户隔离标识：始终按当前用户过滤，避免群聊里不同用户互相看到对方的图。
        # owner 为空（如事件拿不到发送者）时不隔离，仅作兜底。
        owner = getattr(event, "get_sender_id", lambda: "")() or ""

        if sub in ("help", "帮助"):
            await self._send(
                event,
                "📚 图库指令说明（用 /图库 或 /gallery 均可）：\n"
                "· 列表 [页码]　查看图库（每页 5 条，显示总数/总页数）\n"
                "· 搜索 <关键词>　按画面描述检索\n"
                "· 打标签 [图] <标签...>　给图加标签（可用 /图库 打标签 或 /图库 标签）\n"
                "· 找标签 <标签>　按标签取图\n"
                "· 取图 <序号/sha>　发某张图（序号指列表里的编号；可多张，逗号或空格隔开）\n"
                "· 收藏 <序号/sha> / 取消收藏 <序号/sha>　收藏或取消收藏（可多张）\n"
                "· 收藏列表 [页码]　查看自己收藏的图（★）\n"
                "· 公开 <序号/sha> / 私有 <序号/sha>　设置图片可见性（公开后他人可检索）\n"
                "· 保存 [标签...]　收藏当前这张图\n"
                # "· 删除 <sha>　移入回收站；恢复 <sha> 从回收站找回；清空 <sha> 彻底删除\n"
                # "· 回收站　查看回收站\n"
                "· 统计　查看图库统计信息\n"
                "· 全部 列表/搜索（管理员）　查看所有用户的图片（带 sid/用户名）\n\n"
                "多张用法：取图/收藏/取消收藏 都支持一次性多张，序号用逗号或空格隔开。\n"
                "示例：/图库 列表 2　/图库 取图 1,2,3　/图库 收藏 1 2 5　/图库 取图 1,2,4 7",
            )
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
                # 是否管理员（管理员额外展示所属会话 sid；全库模式展示 user_id/user_name）
                is_admin = bool(getattr(event, "is_admin", lambda: False)())
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
                    if is_admin and (_sid or all_view):
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
            # 收藏是用户级资产，跨会话可见，不因 session 过滤而丢图（否则「收藏两张只显示一张」）。
            total = self.gallery.count_search(starred_only=True, owner=eff_owner)
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, total_pages)
            rows = self.gallery.search(
                starred_only=True, limit=page_size, offset=(page - 1) * page_size,
                owner=eff_owner,
            )
            if not rows:
                await self._send(event, "你还没收藏任何图。收藏后可用 /图库 收藏列表 查看。")
            else:
                is_admin = bool(getattr(event, "is_admin", lambda: False)())
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
                    if is_admin and (_sid or all_view):
                        line += f" | 👤 {_uname or _uid or '匿名'}"
                    lines.append(line)
                lines.append(f"\n翻页：/图库 收藏列表 <页码>（共 {total_pages} 页）")
                lines.append("发图用：/图库 取图 <序号>（上方「N.」左侧的数字）")
                await self._send_display(event, "\n".join(lines))

        elif sub == "search":
            kw = " ".join(rest).strip()
            if not kw:
                await self._send(event, "用法：/图库 搜索 <关键词>")
            else:
                eff_owner = "" if all_view else owner
                rows = self.gallery.search(keyword=kw, limit=20, session=session_scope, owner=eff_owner)
                if not rows:
                    await self._send(event, f"没找到含「{kw}」的图。")
                else:
                    is_admin = bool(getattr(event, "is_admin", lambda: False)())
                    _head = "全库检索" if all_view else "检索"
                    lines = [f"{_head}「{kw}」的结果："]
                    for i, r in enumerate(rows, 1):
                        _gno = r.get("gidx", i)  # 图库唯一编号，可直接取图
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
                        tag_line = f" | {tags.strip()}" if tags.strip() else ""
                        line = f"{_gno}.{desc}{tag_line} | {_wf} | {_tm}"
                        if is_admin and (_sid or all_view):
                            line += f" | 👤 {_uname or _uid or '匿名'}"
                        lines.append(line)
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
                    await self._send(event, f"已给 [{sha[:16]}] 打标签：{'、'.join(tags)}")

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
                        lines.append(f"{_gno}. {star} {r['source']} {self._gallery_desc(r, 40)}")
                    lines.append("发图用：/图库 取图 <序号>（上方「N.」左侧的数字）")
                    await self._send_display(event, "\n".join(lines))

        elif sub == "send":
            if not rest:
                await self._send(event, "用法：/图库 取图 <序号>（可多张，用逗号或空格隔开，如「/图库 取图 1,2,3」）")
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
                    await self._send(event, f"已收藏这张图 [{sha[:16]}]{extra}")
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
                        await self._send(event, f"已把 [{sha[:16]}] 设为{'公开' if is_pub else '私有'}。{'其他人现在也能检索到这张图了。' if is_pub else '只有你能看到这张图了。'}")
                    else:
                        await self._send(event, "设置可见性失败。")
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
                "· public/公开 <序号/sha>　private/私有 <序号/sha>　stats/统计",
            )
        event.stop_event()

    # ------------------------------------------------------------------ #
    # LLM 工具：comfyui_draw（AI 对话触发）
    # ------------------------------------------------------------------ #
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
        source: str = "",
        image: str = "",
        denoise: float = -1,
    ):
        """使用 ComfyUI 根据文本提示词生成图片并返回给用户。同时支持文生图与图生图。

        什么时候不要用本工具（改用 comfyui_gallery）：
        - 用户要的是「以前画过的图 / 收藏的图 / 之前发过的某张照片」，而不是要新画一张。
          例如「把我们的合照发我」「上次那张猫的图再发一次」「把收藏的图发我」——
          这些一律调用 comfyui_gallery（mode=recall / mode=search），本工具永远只出【新】图，
          绝不从图库复用旧图。
        - 本工具与 comfyui_gallery 职责严格分离：生图归 draw，发旧图归 gallery。

        触发时机：当用户表达任何想要绘制/生成/画一张图片的意图时，务必调用此工具。
        详细的操作细则（数量、图生图判定、LoRA/工作流查询时机、提示词语言等）见可用技能「comfyui-draw」（若你有技能读取能力，先读它再操作）。
        ★直接调用，不要只说不动：用户让我画图/生成图时，**必须立即调用本工具**，并同时把画面描述完整填进 prompt 参数。绝不允许只回复"好/马上/快了"而不调用工具——不调用工具=没有真的画。
        
        什么时候调用：
        - 用户说了任何画图意图（画/生成/来张图/画个/出张图/拍照/再来一张/换个姿势重画等），一律调用。
        - 用户在催"你咋不画/图呢/怎么没看到图"同理，立即调用。
        - 用户提到"用某个 LoRA/某个风格"，把可用的名称/别名填进 loras 参数（用户给了名字直接用；没给具体名但要求某类效果时，可先调 comfyui_loras 查列表再选）。请勿只让用户"给你确切名字"而不动手——能查到就自己查。
        
        什么时候不要调用：
        - 用户明确说不要/取消/别发/不需要图。
        - 与画图无关的普通闲聊。
        
        数量：默认只出 1 张。用户明确说了"几张/多张/各来一版/所有姿势"才按数量多画；否则一次一张就停。
        
        图生图判定（重要）：只有当用户**当前消息里附带了参考图**（或明确说"把这张图/参考这张图/这张照片变成XX"）时，才按图生图处理（传 image 或依赖插件自动提取）。**普通文字请求一律文生图**，不要因为群里/历史里有图就当作图生图。
        
        prompt 语言：动漫/二次元风格（Anima 工作流）用英文 Danbooru 风格标签（如 1girl, solo, white dress, masterpiece）；真人/写实用中文即可。
        ★动漫标签翻译（重要）：当用户用中文描述动漫/二次元画面，需要把中文翻译/改写为英文 Danbooru 标签时，若你当前的工具列表里有「Danbooru tag search / Danbooru 标签搜索」这类 MCP 工具，**务必优先调用它**去查询/确认标准 Danbooru 标签，再把准确的英文标签填进 prompt，不要仅凭记忆臆造标签、也不要原样透传中文。只有当没有该类 MCP 工具时才退而直接用你自己的翻译能力改写为英文标签。
        不确定工作流时留空 workflow，插件会用默认；只有用户明确要某种画风且你有把握时才传 workflow 名称（不确定可先调 comfyui_workflows）。
        
        重要：不要依赖历史记忆复用旧图。用户再次要图就重新生成。画完就自然收尾，不要不停追问或重复画。
        
        Args:
            prompt(string): 【必填】图像的正向提示词描述（中文或英文均可）。这是唯一必须填写的参数，
                不要留空，也不要用自然语言包裹，直接给出画面描述文本。
            negative_prompt(string): 负向提示词，可选，不填则留空。
            workflow(string): 文生图工作流名称，可选。用户明确要某画风且你知道对应名称时传入；否则留空用默认。不确定可用名称时可先调 comfyui_workflows 查。
            img2img_workflow(string): 图生图工作流名称，可选。仅在本次消息附了参考图时使用；否则留空用默认图生图工作流。
            width(number): 图片宽度，0 或不填表示使用工作流默认宽度。用户明确要求宽高时传入（如"1024x1024"、"宽512"）。
            height(number): 图片高度，0 或不填表示使用工作流默认高度。用户明确要求宽高时传入。
            loras(array[string]): 需要启用的 LoRA 名称/别名列表，例如 ["catgirl"]。用户提到某个 LoRA（用名称或别名）就填进来；不确定有哪些可先调 comfyui_loras 查。用户没提 or 不需要时留空。
            seed(number): 随机种子，0 或不填表示每次随机。用户明确要求"固定/复现/用同样的种子"时传入具体数字。
            image(string): 图生图参考图的 URL。仅当用户在消息里明确带图并要变换时传；多数情况插件自动从消息提取，无需传此参数。
            denoise(number): 降噪幅度/重绘强度（0~1），仅图生图有效。不传或 -1 则用工作流配置默认值。用户明确要求"改多少/像不像原图"时传入。

        补充说明：
        - 用户未明确要求宽高/lora/seed/denoise 时，这些参数可不传，插件自动使用工作流或配置默认值。
        - 图生图只认「本次消息附带的参考图」；历史消息/群聊里的旧图不算。仅在用户明确针对某张图时按图生图走。
        """
        # LLM 工具开关：关闭时拒绝本插件 LLM 的自动调用，
        # 但伴侣插件等第三方主动调用（带 source 标记）不受影响。
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if plugin is None:
            plugin = self
        if not plugin._cfg("enable_llm_tools", True) and not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            return "LLM 画图工具已关闭，请使用指令绘图（/draw、/img2img、/画xxx 等）。"

        # 单张保护：同一会话短时间内的重复调用视为模型死循环/误触发，
        # 直接收尾不重复生图（除非带 source 的第三方插件主动调用）。
        try:
            _now = time.time()
            _sid_key = (getattr(event, "session_id", "") or "global") if event is not None else "global"
            _ts_map = getattr(plugin, "_last_llm_draw_ts", None)
            if not isinstance(_ts_map, dict):
                _ts_map = {}
                plugin._last_llm_draw_ts = _ts_map
            _prev = _ts_map.get(_sid_key, 0.0)
            _ts_map[_sid_key] = _now
            _is_companion_call = bool(source and source.strip() == SOURCE_COMPANION_PLUGIN)
            if not _is_companion_call and _prev and (_now - _prev) < 4.0:
                logger.info(f"[llm_draw] 会话 {_sid_key} 4 秒内重复调用画图工具，已拦截至一张（防止连发多张）")
                return "图片已生成并发送给用户。请用一句话简短、自然地收尾即可；用户没有明确要求多张，不要再重复调用画图工具。"
        except Exception:
            pass

        # 部分 AstrBot 版本下 self/event 绑定可能异常（self 为 None 或 event 为 None），
        # 这里用全局实例与最近事件兜底，避免 'NoneType' object has no attribute '_do_draw'。
        if not isinstance(event, AstrMessageEvent):
            event = getattr(plugin, "_last_event", None)
        if event is None:
            return "⚠️ 绘图工具未能获取到会话事件，请稍后重试，或直接使用 /draw 指令绘图。"

        # prompt 兜底：LLM 有时不会把描述填进 tool 参数（参数空洞/空 JSON），
        # 此时优先用「指定模型」(llm_model) 重新从用户原话提取参数；再退回从原始消息文本取描述，
        # 避免「空参数→报错→重试→空参数」死循环。
        if not prompt or not prompt.strip():
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
                    "loras(string): LoRA 名称列表，可选。\n"
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

        lora_map = None
        if loras:
            lora_map = {str(n).strip(): None for n in loras if str(n).strip()}

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
            logger.info(f"[取图] llm_draw image 参数: {img_url}")
            p = await _image_to_local_path(img_url)
            if p:
                init_images.append(p)
                got_explicit_image = True
                logger.info(f"[取图] image 参数下载成功: {p}")
            else:
                logger.warning(
                    f"[取图] image 参数下载/解析失败，无法作为参考图: {img_url!r}"
                    f" —— 该路径在本机不存在（调用方/伴侣插件传来的可能是另一容器或已清理的 temp 路径）。"
                    f" 若本应走图生图，请让调用方传入当前服务器上真实可用的图片路径或 URL。"
                )

        # ② 从事件中自动提取图片（本次消息/引用里的图，是"用户确实发了图"的最可靠信号）
        event_images: list[str] = []
        last_ev = getattr(plugin, "_last_event", None)
        if not got_explicit_image:
            event_images = await plugin._extract_images(event)
            if not event_images and last_ev is not None and last_ev is not event:
                logger.info("[取图] llm_draw 工具 event 未取到图，回退到 LLM 调用前捕获的原始事件再取一次")
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
            for p in (g_last_received.get(sid) or []):
                if p and os.path.exists(p) and p not in init_images:
                    init_images.append(p)
            if not init_images:
                hist = list(reversed(g_recent_user_images.get(sid) or []))
                for p in hist[:1]:
                    if p and os.path.exists(p) and p not in init_images:
                        init_images.append(p)
                        break
            if not init_images:
                for p in (list(reversed(g_last_generated.get(sid) or []))[:1]):
                    if p and os.path.exists(p) and p not in init_images:
                        init_images.append(p)
                        break
            if init_images:
                logger.info(f"[取图] llm_draw 图生图补图兜底（历史/会话/生成图）: {init_images}")
            else:
                logger.info("[取图] llm_draw 已判定图生图但兜底仍未取到参考图，将提示用户重发图")

        if init_images:
            logger.info(f"[取图] llm_draw 最终取得参考图 {len(init_images)} 张 -> {init_images}")
        elif is_img2img:
            logger.info(
                f"[取图] llm_draw 意图为图生图但无参考图可用"
                f"（用户/调用方指定的图生图工作流={img2img_workflow or workflow or '默认'}），"
                f"将不下发，提示用户重发图"
            )
        else:
            logger.info("[取图] llm_draw 文生图模式（未取图，无图生图意图）")

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
                    f"[取图] llm_draw 仅指定 img2img_workflow（={_req_i2i or '默认'}）但无参考图，"
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
                    f"[取图] llm_draw 弱信号回退文生图：原图生图工作流={_req_i2i or '默认'}, "
                    f"回退到文生图工作流={fallback_wf or '（均无配置，走默认）'}"
                )
            elif img2img_fallback == "txt2img":
                logger.warning(
                    f"[取图] llm_draw 已判定为图生图（期望工作流={img2img_workflow or workflow or '默认'}）"
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
                    f"[取图] llm_draw 回退文生图：原图生图工作流={_req_i2i or '默认'}, "
                    f"回退到文生图工作流={fallback_wf or '（均无配置，走默认）'}"
                )
            else:
                logger.warning(
                    f"[取图] llm_draw 已判定为图生图（期望工作流={img2img_workflow or workflow or '默认'}）"
                    f"但取不到任何参考图，终止并提示用户重发图（img2img_fallback=prompt，不降级为文生图）"
                )
                return "图生图需要一张参考图，但没能从本次消息/引用/历史里取到图片。请先发送一张图片（或引用一张图）再说明要怎么变换它，例如「把这张图变成夜晚」。"
        else:
            was_img2img = False

        if is_img2img and img2img_workflow and img2img_workflow.strip():
            resolved_wf = img2img_workflow.strip()
        elif is_img2img and workflow and workflow.strip():
            resolved_wf = workflow.strip()
        else:
            resolved_wf = (workflow or "").strip() or None
        # 回退为文生图时：不沿用原图生图工作流名（那可能是图生图工作流、有 LoadImage
        # 但无图注入会报错），改用工步计算好的对应风格文生图工作流；未配置则 None（走默认）。
        if was_img2img and not is_img2img:
            resolved_wf = fallback_wf or None
        logger.info(
            f"[llm_draw] 工作流决策：is_img2img={is_img2img}, "
            f"指定 img2img_workflow={img2img_workflow!r}, 指定 workflow={workflow!r}, "
            f"最终选用工作流={resolved_wf or '默认文生图'}"
        )

        # 提示词过滤总开关（默认关闭）：
        # - 关闭（默认）：无论原生调用还是伴侣插件调用，都**完全不做任何提示词改写**，
        #   原始提示词原样透传给 ComfyUI（连通用拆分/清洗都不做）。
        # - 开启：仅当调用方为伴侣插件（source == SOURCE_COMPANION_PLUGIN，
        #   即 astrbot_plugin_private_companion）时，自动做完整过滤——先按通用规则
        #   拆分正/负向并清洗方括号分节标记，再做专属过滤（抽取用户诉求与构图连续性，
        #   过滤时间/日程/位置/情绪等无关事实与元指令、Avoid/Do not 负面约束）。
        #   原生调用（/draw、AI 对话、Agent 等）即使开启开关也归属同一过滤功能，
        #   由本总开关统一控制，这里同走过滤分支即可。
        _filter_on = plugin._cfg("filter_companion_prompt", False)
        is_companion_src = bool(source and source.strip() == SOURCE_COMPANION_PLUGIN)
        logger.info(
            f"[llm_draw] 提示词过滤开关={_filter_on}, 来源={source!r}, 是否伴侣插件={is_companion_src}"
        )
        if _filter_on:
            positive, parsed_neg = plugin._split_external_prompt(prompt)
            cpos, cneg = plugin._format_companion_prompt(prompt)
            if cpos:
                positive = cpos
            if cneg:
                parsed_neg = cneg
            positive = plugin._strip_inline_negative(positive)
        else:
            positive, parsed_neg = prompt.strip(), ""
        negative = parsed_neg or (negative_prompt or "")

        # 改为普通协程（不再用 yield），以兼容用 `await` 调用本工具的第三方插件
        # （如 astrbot_plugin_private_companion 主动生图）。_do_draw 现以
        # (图片节点, 本地路径) 元组产出。注意：LLM 工具的 return 值只会作为工具
        # 结果文本回传给模型，框架不会自动渲染图片，所以原生对话下必须在这里主动
        # event.send 把图发到聊天里。
        # - 带 source（伴侣插件 proactive 管道）时，return JSON 文本，由伴侣解析
        #   image_path 后自己发图，本函数不重复发图；
        # - 不带 source（原生对话 / 伴侣 Agent 自主 tool_call）时，主动 event.send
        #   图片，再 return 简短文本告知模型已处理。
        img_path = ""
        img_node = None
        async for node, p in plugin._do_draw(
            event,
            resolved_wf,
            positive,
            negative,
            width or None,
            height or None,
            lora_map,
            None,
            seed or None,
            init_images=init_images or None,
            is_img2img=is_img2img,
            denoise=denoise if denoise >= 0 else None,
            # 伴侣插件 proactive（机器人主动生图）不发「正在处理」即时提示，
            # 避免打扰；原生 / AI 对话默认发，让用户立刻知道已受理。
            notify_pending=not bool(source and source.strip() == SOURCE_COMPANION_PLUGIN),
            source=source,
        ):
            if not img_node:
                img_node = node
            if not img_path:
                img_path = p

        is_companion = bool(source and source.strip() == SOURCE_COMPANION_PLUGIN)
        if img_path:
            if is_companion:
                # 伴侣插件：用 JSON 文本返回图片路径，由调用方负责发图与解析
                return json.dumps({"image_path": img_path, "status": "ok"}, ensure_ascii=False)
            # 原生 / Agent 调用：LLM 工具的 return 值只会作为工具结果文本回传给
            # 模型，框架并不会自动把 MessageChain 渲染成图片发给用户。因此这里必须
            # 主动 event.send 把图真正发出去，再 return 一句简短文本让模型知道已处理。
            try:
                await event.send(img_node if isinstance(img_node, MessageChain) else MessageChain([img_node]))
            except Exception as _e:
                logger.warning(f"[出图] comfyui_draw 主动发送图片失败: {_e}")
            # 图片已由插件主动 event.send 发到聊天里。返回给模型的文本**绝不提及任何
            # 文件信息（路径/文件名/尺寸/大小/耗时/时间/格式等）**，避免模型把这些
            # 技术元数据复述给用户；只做极简收尾指示即可。
            return "图片已发送给用户。请用一句话简短、自然地收尾即可；不要描述图片的文件名、尺寸、大小、耗时、格式或任何技术细节。"
        return "本次生图失败。请用一句话简短向用户说明生成遇到问题即可，不要复述本提示。"

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
            logger.debug(f"[取图] 原始消息抓引用图失败（忽略）: {qe}")
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
                logger.debug(f"[取图] 消息前置缓存 {len(imgs)} 张: {imgs}")
        except Exception as e:
            logger.debug(f"[取图] 消息前置捕获图片失败（忽略）: {e}")

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
                logger.debug(f"[取图] Agent 前置缓存 {len(imgs)} 张: {imgs}")
        except Exception as e:
            logger.debug(f"[取图] 捕获用户消息图片失败（忽略）: {e}")

    # 在 LLM 工具被调用前捕获「完整」原始事件（含图片组件）。
    # 因为部分情况下工具回调收到的 event 图片可能已被 LLM 消费/剥离，
    # 这里提前存一份，并趁图片还在时把路径缓存下来，供图生图取图兜底使用。
    @filter.on_using_llm_tool()
    async def _capture_llm_event(
        self, event: AstrMessageEvent, tool=None, tool_args: dict | None = None
    ):
        self._last_event = event
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
                    logger.debug(f"[取图] 缓存时解析引用图失败（忽略）: {qe}")
                cached = [p for p in cached if p and os.path.exists(p)]
                if cached:
                    bucket = g_last_received.setdefault(sid, [])
                    for p in cached:
                        if p not in bucket:
                            bucket.append(p)
                    if len(bucket) > 5:
                        g_last_received[sid] = bucket[-5:]
                    logger.debug(f"[取图] 已缓存会话最近收到图片 {len(bucket)} 张: {bucket}")
                else:
                    logger.debug("[取图] 缓存时未从消息/引用取到任何图")
        except Exception as e:
            logger.debug(f"[取图] 缓存会话图片失败（忽略）: {e}")

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
        cross = bool(plugin._cfg("gallery", {}).get("cross_session"))
        session = None if cross else (getattr(event, "session_id", "") or "")
        # 用户隔离：始终按当前用户过滤，避免把别人的图发给当前用户。
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
            # 多张：列出让用户选（按确认口径）。编号为图库唯一编号，可直接用于 send 定位。
            lines = [f"带「{tag.strip()}」的图有 {len(rows)} 张，回复编号即可发对应那张："]
            for i, r in enumerate(rows, 1):
                _gno = r.get("gidx", i)  # 图库唯一编号，send 传它即可定位到同一张
                star = "★" if r["starred"] else ""
                lines.append(f"{_gno}. {star} {plugin._gallery_desc(r, 40)}")
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
            lines = [f"检索「{keyword.strip()}」的结果："]
            for i, r in enumerate(rows, 1):
                _gno = r.get("gidx", i)  # 图库唯一编号，send 传它即可定位到同一张
                lines.append(f"{_gno}. {plugin._gallery_desc(r, 40)}")
            lines.append("回复编号即可发对应那张。")
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
            return f"已收藏这张图 [{sha[:16]}]{extra}。以后说「把{'/'.join(tags) if tags else '这张'}发我」即可召回。"

        elif mode == "send":
            arg = (keyword or "").strip()
            if not arg:
                return "send 模式需要 keyword 参数传序号（如「3」，可多张用逗号或空格隔开「1,2,3」）或 sha 前几位。"
            targets = plugin._parse_gallery_targets([arg])
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
                else:
                    ok = await plugin._gallery_send_image(event, t, owner=owner)
                    sent_ok += 1 if ok else 0
                    sent_fail += 0 if ok else 1
            if len(targets) > 1:
                return f"已发送 {sent_ok} 张，失败/跳过 {sent_fail} 张。"
            return ("已发送。" if sent_ok else "没找到这张图/发送失败。")

        elif mode == "list":
            rows = g.search(limit=limit, session=session, owner=owner)
            if not rows:
                return "画廊还是空的～先画点图或收藏点图吧。"
            lines = ["最近的图片（回复编号即可发图）："]
            for i, r in enumerate(rows, 1):
                t = (" #" + " #".join(r["tags"])) if r["tags"] else ""
                lines.append(f"{r.get('gidx', i)}. {'★' if r['starred'] else ''} {r['source']}{t}")
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
                return f"已给 [{sha[:16]}] 打标签：{'、'.join(tags)}。"
            # public / private
            is_pub = mode == "public"
            if g.set_visibility(sha, is_pub):
                return (
                    f"已把 [{sha[:16]}] 设为{'公开' if is_pub else '私有'}。"
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
    ):
        """查询已配置的 LoRA 库，包括每个 LoRA 的名称、别名、底模、描述与触发词。

        触发时机：在调用 comfyui_draw / comfyui_img2img 并需要指定 LoRA 之前，
        可先调用本工具获取真实 LoRA 列表，再从中选择正确的名称传入 loras 参数。
        不要凭记忆猜测 LoRA 名称，也不要编造不存在的 LoRA。

        Args:
            base_model(string): 可选。按底模过滤（如 anima / z-image-turbo / krea2 / illustrious）。当用户指定了工作流/底模时，传入该底模只列出可用的 LoRA。
            keyword(string): 可选。按名称/别名模糊匹配查找某个 LoRA。
        """
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if plugin is None:
            plugin = self
        lib = plugin._lora_library()
        if not lib:
            return "当前未配置任何 LoRA。可在插件配置页的 LoRA 库中添加。"
        wf_bm = (base_model or "").strip().lower()
        kw = (keyword or "").strip().lower()
        rows = []
        for l in lib:
            name = (l.get("name") or "").strip()
            if not name:
                continue
            lora_bm = (l.get("base_model") or "").strip().lower()
            if wf_bm and lora_bm and lora_bm != wf_bm:
                continue  # 底模不匹配的 LoRA 不列出
            aliases = l.get("aliases") or []
            desc = (l.get("description") or "").strip()
            tw = (l.get("trigger_words") or "").strip()
            if kw:
                hay = " ".join([name, *[str(a) for a in aliases], desc, tw]).lower()
                if kw not in hay:
                    continue
            alias_str = ", ".join(str(a) for a in aliases) if aliases else name
            lines = [f"- {name}（别名：{alias_str}）"]
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
            return "没有可用的 LoRA。"
        head = "已配置的 LoRA 列表："
        if wf_bm:
            head += f"（底模 {base_model}）"
        return head + "\n" + "\n".join(rows)

    # LLM 工具：comfyui_workflows（查询工作流列表）
    # ------------------------------------------------------------------ #
    @filter.llm_tool(name="comfyui_workflows")
    async def llm_workflows(self, event: AstrMessageEvent):
        """查询所有已配置的 ComfyUI 工作流列表，包括名称和是否支持图生图。

        触发时机：在调用 comfyui_draw 或 comfyui_img2img 之前，如果需要确认
        有哪些可用工作流、哪些支持图生图（配置了 image_node），务必先调用此工具
        获取列表，再根据用户意图选择正确的工作流名称传入 img2img_workflow 或
        workflow 参数。

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
            anima = " [Anima]" if w.get("is_anima") else ""
            lines.append(f"- {name}{img_tag}{anima}")

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
        source: str = "",
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
        - 必须确保用户消息里附带了参考图；若没有图，请提示用户先发一张图再描述变换。
        - 即便对话历史里做过类似变换，只要用户再次附带图片并表达意图，就重新调用。
        - 传入 image 参数（消息中图片的 URL）或插件自动从消息中提取图片均可。
        - ⚠️ 若用户已在当前消息里附带了图片，请直接把该图片（或其在消息中的引用）
          传入即可，**不要**调用 get_message_detail 之类接口去回拉"原始消息"再重新下载图片：
          回拉到的原始图片 URL 通常无法在本机直接下载（带签名时效/内网地址），既耗时又必然失败，
          而当前消息里的图已可被插件直接使用。
        - ⚠️ 图生图不需要你（大模型）去"理解"或"描述"参考图的内容：
          参考图会直接作为像素喂给 ComfyUI 的 LoadImage 节点，你只需把用户的变换意图
          翻译成英文提示词（prompt）即可，不要浪费步骤去调用视觉转述/读取图片内容。

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
            loras(array[string]): 需要启用的 LoRA 名称列表，例如 ["catgirl", "rain"]。留空则使用配置中默认启用的 LoRA。指定 LoRA 前必须先调用 comfyui_loras 查询真实列表（可按底模过滤），再从中选确切名称传入；禁止凭记忆或猜测名称。
            seed(number): 随机种子，0 或不填表示每次随机。用户明确要求"固定/复现/用同样的种子"时传入具体数字。
            image(string): 参考图 URL。多数情况用户直接发图时无需传此参数，插件会自动从消息提取；仅当需要明确指定某张图时传入。
            denoise(number): 降噪幅度/重绘强度（0~1）。不传或 -1 则用工作流配置默认值。用户明确要求"改多少/像不像原图"时传入。

        补充说明：
        - 用户未明确要求 lora/seed/denoise 时，这些参数可不传，插件自动使用工作流或配置默认值。
        - 参考图通常附在用户消息里即可，插件会自动提取；无需强求大模型传 image 参数。
        """
        # LLM 工具开关：关闭时拒绝本插件 LLM 的自动调用，
        # 但伴侣插件等第三方主动调用（带 source 标记）不受影响。
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if plugin is None:
            plugin = self
        if not plugin._cfg("enable_llm_tools", True) and not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            return "LLM 画图工具已关闭，请使用指令绘图（/draw、/img2img、/画xxx 等）。"

        # 单张保护：同 llm_draw，同一会话短时间重复调用（模型死循环）直接收尾
        try:
            _now2 = time.time()
            _sid_key2 = (getattr(event, "session_id", "") or "global") if event is not None else "global"
            _ts_map2 = getattr(plugin, "_last_llm_draw_ts", None)
            if not isinstance(_ts_map2, dict):
                _ts_map2 = {}
                plugin._last_llm_draw_ts = _ts_map2
            _prev2 = _ts_map2.get(_sid_key2, 0.0)
            _ts_map2[_sid_key2] = _now2
            _is_companion_call2 = bool(source and source.strip() == SOURCE_COMPANION_PLUGIN)
            if not _is_companion_call2 and _prev2 and (_now2 - _prev2) < 4.0:
                logger.info(f"[llm_img2img] 会话 {_sid_key2} 4 秒内重复调用，已拦截（防止连发多张）")
                return "图片已生成并发送给用户。请用一句话简短、自然地收尾即可；用户没有明确要求多张，不要再重复调用画图工具。"
        except Exception:
            pass

        # 与 llm_draw 同样的兜底处理
        if not isinstance(event, AstrMessageEvent):
            event = getattr(plugin, "_last_event", None)
        if event is None:
            return "⚠️ 绘图工具未能获取到会话事件，请稍后重试，或直接使用 /img2img 指令。"

        # prompt 兜底：LLM 有时不会把描述填进 tool 参数（参数空洞/空 JSON），
        # 优先用「指定模型」(llm_model) 重新提取；再退回原始消息文本，避免死循环。
        if not prompt or not prompt.strip():
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
                    "loras(string): LoRA 名称列表，可选。\n"
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

        # ── 收集图片（与 llm_draw 共用同一逻辑）─────────────────────
        init_images: list[str] = []

        # ① image 参数：LLM 传入的参考图 URL
        got_explicit_image = False
        if image and image.strip():
            img_url = image.strip()
            logger.info(f"[取图] llm_img2img image 参数: {img_url}")
            p = await _image_to_local_path(img_url)
            if p:
                init_images.append(p)
                got_explicit_image = True
                logger.info(f"[取图] image 参数下载成功: {p}")
            else:
                logger.warning(f"[取图] image 参数下载失败: {img_url}")

        if not got_explicit_image:
            # ② 从事件中自动提取图片（仅在未通过 image 参数显式拿到图时才探测）。
            #    图生图不需要大模型"看懂"图片，参考图直接喂给 ComfyUI 的 LoadImage 节点；
            #    因此若 image 参数已成功取到图，就绝不再去 event / last_event 里做无谓的
            #    兜底探测（避免把上几次生成的旧图也混进来、也少打噪音日志）。
            event_images = await plugin._extract_images(event)
            last_ev = getattr(plugin, "_last_event", None)
            if not event_images and last_ev is not None and last_ev is not event:
                logger.info("[取图] llm_img2img 工具 event 未取到图，回退到 LLM 调用前捕获的原始事件再取一次")
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
                logger.info(f"[取图] 启用兜底图片（本会话用户最近收到/历史/生成图）: {init_images}")
            else:
                return "请先发送一张参考图，再用文字告诉我要怎么变换它哦～ 例如「把这张图变成夜晚」。"

        # ── 决定工作流 ─────────────────────────────────────────────
        # 图生图始终 is_img2img=True；img2img_workflow > workflow > 默认图生图
        if img2img_workflow and img2img_workflow.strip():
            resolved_wf = img2img_workflow.strip()
        elif workflow and workflow.strip():
            resolved_wf = workflow.strip()
        else:
            resolved_wf = None

        lora_map = None
        if loras:
            lora_map = {str(n).strip(): None for n in loras if str(n).strip()}

        # 与 llm_draw 一致：先按通用规则拆分正/负向并清洗标记
        positive, parsed_neg = plugin._split_external_prompt(prompt)
        negative = parsed_neg or (negative_prompt or "")

        img_path = ""
        img_node = None
        async for node, p in plugin._do_draw(
            event,
            resolved_wf,
            positive,
            negative,
            None,
            None,
            lora_map,
            None,
            seed or None,
            init_images=init_images,
            is_img2img=True,
            denoise=denoise if denoise >= 0 else None,
            source=source,
        ):
            # 本插件只负责生图与返回，不再主动 event.send（避免与调用方重复发图）：
            # - 带 source（伴侣插件 proactive 管道）时，return JSON 文本，由伴侣解析
            #   image_path 后自己发图；
            # - 不带 source（原生对话 / 伴侣 Agent 自主 tool_call）时，直接 return
            #   图片节点，由 AstrBot 框架把工具结果里的图片渲染给用户。
            if not img_node:
                img_node = node
            if not img_path:
                img_path = p

        is_companion = bool(source and source.strip() == SOURCE_COMPANION_PLUGIN)
        if img_path:
            if is_companion:
                # 伴侣插件：用 JSON 文本返回图片路径，由调用方负责发图与解析
                return json.dumps({"image_path": img_path, "status": "ok"}, ensure_ascii=False)
            # 原生 / Agent 调用：LLM 工具的 return 值只会作为工具结果文本回传给模型，
            # 框架不会自动渲染图片，必须主动 event.send 把图发到聊天里。
            try:
                await event.send(img_node if isinstance(img_node, MessageChain) else MessageChain([img_node]))
            except Exception as _e:
                logger.warning(f"[出图] comfyui_img2img 主动发送图片失败: {_e}")
            # 图片已由插件主动 event.send 发到聊天里。返回给模型的文本**绝不提及任何
            # 文件信息（路径/文件名/尺寸/大小/耗时/时间/格式等）**，避免模型把这些
            # 技术元数据复述给用户；只做极简收尾指示即可。
            return "图片已发送给用户。请用一句话简短、自然地收尾即可；不要描述图片的文件名、尺寸、大小、耗时、格式或任何技术细节。"
        return "本次生图失败。请用一句话简短向用户说明生成遇到问题即可，不要复述本提示。"
