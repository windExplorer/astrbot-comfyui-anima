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
    from . import comfyui_client, danbooru_client, workflow_builder
except ImportError:
    # 兼容非包环境（如本地测试直接运行本模块）
    import comfyui_client
    import danbooru_client
    import workflow_builder

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
    "已收到，正在处理中，请稍候。",
    "任务已提交，正在生成中，稍等片刻。",
    "好的，已开始处理，马上就好。",
    "已提交，正在出图中，请稍等一下。",
    "收到，正在处理你的请求，稍候片刻。",
    "已开始，正在生成，请稍等。",
]

_QUEUE_HINTS_QUEUED = [
    "已收到，前面还有 {n} 个任务在排队，请稍候。",
    "已排队，当前前面还有 {n} 个任务，请等待。",
    "任务已入队，前面还有 {n} 个，稍等一下下。",
    "正在排队，前面还有 {n} 个任务，请稍候。",
    "已入队，前面 {n} 个任务在等待，请稍等片刻。",
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
    "好了，这张 {wh}、{size}，耗时 {cost} 秒，文件时间 {ftime}。",
    "搞定~ {wh}、{size}，用时 {cost} 秒，保存于 {ftime}。",
    "这张图 {wh}、{size}，耗时 {cost} 秒，生成时间 {ftime}。",
    "给你：{wh}、{size}，耗时 {cost} 秒，文件时间 {ftime}。",
    "已保存：{wh}、{size}，耗时 {cost} 秒，落盘于 {ftime}。",
    "好了，{wh}、{size}，跑了 {cost} 秒，时间 {ftime}。要调整随时说。",
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

        # 插件数据目录：temp/ 存出图，workflow/ 存工作流文件
        self.data_dir = self._get_data_dir()
        self.temp_dir = self.data_dir / "temp"
        self.workflow_dir = self.data_dir / "workflow"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_dir.mkdir(parents=True, exist_ok=True)

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
        """全局 LoRA 库（配置顶层 loras）。"""
        return self._cfg("loras", []) or []

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
                        "weight": float(l.get("weight", 1.0)),
                        "enabled": bool(l.get("enabled", False)),
                        "load_node": "",
                        "model_input": "lora_name",
                        "strength_model_input": "strength_model",
                        "strength_clip_input": "strength_clip",
                        "keywords": (lib_l.get("keywords") or ""),
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
                        "weight": float(l.get("weight", 1.0)),
                        "enabled": bool(l.get("enabled", False)),
                        "load_node": "",
                        "model_input": "lora_name",
                        "strength_model_input": "strength_model",
                        "strength_clip_input": "strength_clip",
                        "keywords": (l.get("keywords") or ""),
                        "presets": [],
                    }
                )
        return merged

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
        """解析多行 LoRA 文本为配置列表。每行：名称|权重|0/1（0=禁用）。"""
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
            out.append(
                {
                    "name": name,
                    "weight": weight,
                    "enabled": enabled,
                }
            )
        return out

    @staticmethod
    def _serialize_loras_text(loras: list[dict]) -> str:
        """将 LoRA 列表序列化回 名称|权重|0/1 文本（用于 loraon/loraoff 持久化）。"""
        lines = []
        for l in loras:
            name = (l.get("name") or "").strip()
            if not name:
                continue
            weight = l.get("weight", 1.0)
            wstr = str(int(weight)) if float(weight) == int(weight) else str(weight)
            enabled = 1 if l.get("enabled", False) else 0
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

    def _resolve_workflow(self, name: str | None = None, is_img2img: bool = False) -> dict:
        """解析工作流配置。is_img2img=True 时优先用图生图默认工作流。

        匹配优先级：
          1) 精确匹配工作流名称（name 字段）
          2) 回退：按文件名匹配（workflow_name 字段，兼容带/不带 .json 后缀）
          3) 上述都失败 → 找不到报错
        """
        workflows = self._workflows()
        if not workflows:
            raise ValueError("未配置任何工作流，请先在插件配置中添加。")
        if not name:
            if is_img2img:
                name = self._cfg("default_img2img_workflow", "") or self._cfg("default_workflow", "")
            else:
                name = self._cfg("default_workflow", "")
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
            # 全部失败：报错并列出可用工作流名，方便用户/AI 校正
            avail = "、".join((w.get("name") or "(未命名)") for w in workflows)
            raise ValueError(f"找不到名为「{name}」的工作流。可用工作流：{avail}。")
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

    @staticmethod
    def _has_chinese(text: str) -> bool:
        """判断文本是否包含中文字符。用于决定是否调用 Danbooru 翻译。"""
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
    def _split_external_prompt(text: str) -> tuple[str, str]:
        """把可能混合「正向/负向」与外部结构化标记的文本拆成 (正向, 负向)。

        用于兼容 astrbot_plugin_private_companion 等调用方：它们把整段提示词
        （含 'Negative prompt:' 段落、'[section compacted]' 占位符、'[User image
        request]' 等分节方括号标题）塞进单个 prompt 参数。若不含有 'Negative
        prompt:' 标记，则视为普通提示词原样返回（不影响常规 /draw 与 AI 对话调用）。
        """
        if not text:
            return "", ""
        # 1) 按 'Negative prompt:' 拆分正/负（大小写与冒号差异均兼容）
        m = re.search(r"negative\s*prompt\s*[:：]", text, re.IGNORECASE)
        if not m:
            return text.strip(), ""
        positive = text[: m.start()].strip()
        negative = text[m.end():].strip()
        # 2) 去掉开头的 'Positive prompt:' 标签
        positive = re.sub(
            r"^\s*positive\s*prompt\s*[:：]\s*", "", positive, flags=re.IGNORECASE
        ).strip()
        # 3) 清理伴侣插件注入的占位符与分节方括号标题（含空格的 [...]）
        positive = re.sub(r"\[\s*section\s*compacted\s*\]", " ", positive, flags=re.IGNORECASE)
        positive = re.sub(r"\[[^\]]*?\s.+?\]", " ", positive)
        positive = re.sub(r"\s+", " ", positive).strip()
        negative = re.sub(r"\[\s*section\s*compacted\s*\]", " ", negative, flags=re.IGNORECASE)
        negative = re.sub(r"\[[^\]]*?\s.+?\]", " ", negative)
        negative = re.sub(r"\s+", " ", negative).strip()
        return positive, negative

    @staticmethod
    def _format_companion_prompt(raw: str) -> tuple[str, str]:
        """针对「我会永远陪着你」伴侣插件的生图提示词做专属格式化与过滤。

        伴侣传来的整段含 Positive/Negative 分段、分节标题、'[section compacted]' 占位符，
        以及大量与出图无关的事实描述（时间/日程/位置/情绪等）和元指令。这里只抽取对
        出图真正有用的部分：
        - 用户原始诉求（紧接 'user request:' 的内容）
        - 构图连续性段落（[Composition and continuity] 区块，标准 SD 风格标签）
        - 负向段落（Negative prompt: 区块，去掉其中的 'Do not ...' 元指令）
        其余噪声（场景事实、分节标题、元指令、截断占位符）一律丢弃。
        """
        if not raw:
            return "", ""
        # 1) 先按 Negative prompt: 切分正/负原始段
        m = re.search(r"negative\s*prompt\s*[:：]", raw, re.IGNORECASE)
        pos_raw = raw[: m.start()].strip() if m else raw.strip()
        neg_raw = raw[m.end():].strip() if m else ""

        # 2) 正向：抽取 user request 与 Composition and continuity 两块
        parts: list[str] = []
        um = re.search(r"user\s*request\s*[:：]\s*(.+)", pos_raw, re.IGNORECASE)
        if um:
            chunk = um.group(1).split("\n")[0].strip()
            chunk = re.sub(r"\[\s*section\s*compacted\s*\]", " ", chunk, flags=re.IGNORECASE)
            chunk = re.sub(r"\s+", " ", chunk).strip()
            if chunk:
                parts.append(chunk)
        cm = re.search(
            r"\[Composition and continuity\](.+?)(?:\n\[|$)",
            pos_raw,
            re.IGNORECASE | re.DOTALL,
        )
        if cm:
            block = cm.group(1).strip()
            block = re.sub(r"\[\s*section\s*compacted\s*\]", " ", block, flags=re.IGNORECASE)
            block = re.sub(r"\s+", " ", block).strip()
            if block:
                parts.append(block)
        positive = ", ".join(p for p in parts if p)

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
            user_id = getattr(event, "user_id", "") or "" if event is not None else ""
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
    ):
        # 记录最近一次事件，供 LLM 工具在 event 异常时为兜底使用
        self._last_event = event
        # 出图计时起点（用于生成完成后的耗时报告）
        _draw_start = time.time()
        # 图生图参考图的 sha256（归档成品图时回填到 ref_sha256 字段）
        ref_sha256 = None
        if not positive or not positive.strip():
            await self._send(event, "请提供正向提示词，例如：/draw 一只白色水手服少女")
            return

        try:
            wf = self._resolve_workflow(workflow_name, is_img2img=is_img2img)
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
                # 没找到图加载节点：列出现有「可能」是图加载的节点，方便用户去
                # _conf_schema 的 image_node 手动指定，避免"明明有图却没填充"的困惑。
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
                await self._send(
                    event,
                    "该工作流没有 LoadImage 类的图加载节点，无法做图生图。"
                    f"请在插件配置的 image_node 里手动填参考图节点的键名（如 39）。{hint}",
                )
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
                        # 参考图优先按 user（用户发来的合照等）归档，便于「合照」类召回
                        _final = self.gallery.archive_image(_ri, source=SRC_USER)
                        # archive_image 现返回归档后路径，反算 sha 作为 ref_sha256（入库/回填用）
                        _sha = _sha256_of(_final) if _final else None
                        if _sha and ref_sha256 is None:
                            ref_sha256 = _sha
                        logger.info(f"[图库] 已归档参考图: {_ri} -> {_final}")
                except Exception as _re:
                    logger.warning(f"[图库] 参考图归档失败（不影响出图）: {_re}")

        # Anima 工作流：中文提示词翻译为 Danbooru 标签
        # 仅当提示词包含中文时才调用（纯英文/无中文时直接作为标签使用，跳过翻译）
        danbooru = self._build_danbooru()
        if wf.get("is_anima") and danbooru is not None and self._has_chinese(positive):
            try:
                tags = await danbooru.search(positive)
            except Exception as e:
                logger.warning(f"Danbooru 翻译失败，将直接使用原始提示词: {e}")
                tags = ""
            if tags:
                if self._danbooru_cfg().get("append_original"):
                    positive = f"{positive}, {tags}"
                else:
                    positive = tags
                logger.info(f"Danbooru 翻译结果: {positive}")

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

            # 本地队列：本次提交之前已排队的任务数即为"前面还有几位"（只提示一次，
            # 不调用 ComfyUI 的 /queue 接口）。
            ahead = self._local_queue_ahead(srv_key)
            try:
                self._local_queue_add(srv_key, prompt_id)
                if self._cfg("return_queue_position", True):
                    await self._send(event, self._queue_hint(ahead))

                # 等待出图
                timeout = int(self._cfg("draw_timeout", 300))
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
                                user_id=(getattr(event, "user_id", "") or ""),
                                user_name=(getattr(event, "get_sender_name", lambda: "")() or ""),
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
                    # LLM 工具 llm_draw 额外用本地路径拼 JSON 返回（供伴侣插件解析为图片）。
                    yield event.image_result(img_path), img_path

                    # 出图完成后的贴心小报告：文件时间、尺寸、耗时（随机萌文案）。
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
        lines = [f"工作流「{wf.get('name')}」的 LoRA 列表："]
        for l in loras:
            state = "启用" if l.get("enabled") else "禁用"
            model = l.get("model_name") or ""
            mo = l.get("model_only", True)
            presets = l.get("presets") or []
            preset_names = [ (p.get("name") or "").strip() for p in presets if (p.get("name") or "").strip() ]
            lines.append(
                f"- {l.get('name')}（{state}，权重 {l.get('weight', 1.0)}"
                + (f"，仅模型" if mo else "，模型+CLIP")
                + (f"，文件 {model}" if model else "，⚠未配置文件名")
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
        """列出工作流，或设置默认工作流：/workflows set 名称 | /workflows set_img2img 名称"""
        args = self._strip_command(event.message_str, "workflows")
        # set_img2img 优先匹配（防止被 set 正则吞掉后缀）
        m_i2i = re.match(r"set_img2img\s+(\S+)", (args or "").strip())
        m = re.match(r"set\s+(\S+)", (args or "").strip())
        if m_i2i:
            name = m_i2i.group(1)
            try:
                self._resolve_workflow(name)
            except ValueError as e:
                await self._send(event, str(e))
                return
            self.config["default_img2img_workflow"] = name
            self.config.save_config()
            await self._send(event, f"已将图生图默认工作流设为「{name}」。")
            event.stop_event()
            return
        if m and not m_i2i:
            name = m.group(1)
            try:
                self._resolve_workflow(name)
            except ValueError as e:
                await self._send(event, str(e))
                return
            self.config["default_workflow"] = name
            self.config.save_config()
            await self._send(event, f"已将文生图默认工作流设为「{name}」。")
            event.stop_event()
            return
        workflows = self._workflows()
        default = self._cfg("default_workflow", "")
        default_i2i = self._cfg("default_img2img_workflow", "")
        if not workflows:
            await self._send(event, "尚未配置任何工作流。")
            event.stop_event()
            return
        lines = ["已配置的工作流："]
        for w in workflows:
            wname = w.get("name")
            tags = []
            if wname == default:
                tags.append("文生图默认")
            if wname == default_i2i:
                tags.append("图生图默认")
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

    async def _gallery_send_image(self, event: AstrMessageEvent, sha: str) -> bool:
        """根据 sha256/前缀拼路径，并发图。返回是否成功。"""
        path = self.gallery.path_of(sha)
        if not path:
            await self._send(event, f"没找到这张图（sha={sha[:16]}），可能已被清理或从未入库。")
            return False
        try:
            await event.send(Image(file=path))
            self.gallery.send(sha)
            return True
        except Exception as _e:
            logger.warning(f"[图库] 发图失败: {_e}")
            await self._send(event, "这张图文件丢失了，可能已被 LRU 清理。")
            return False

    @filter.command("gallery")
    async def cmd_gallery(self, event: AstrMessageEvent):
        """图片画廊与语义标签召回。用法见子命令。"""
        if self.gallery is None:
            await self._send(event, "图库未启用或初始化失败，请检查配置。")
            event.stop_event()
            return
        args = self._strip_command(event.message_str, "gallery") or ""
        parts = args.split()
        sub = (parts[0] or "").lower() if parts else "list"
        rest = parts[1:]

        # 跨会话范围（关闭 cross_session 时仅当前会话）
        session_scope = None if self._cfg("gallery", {}).get("cross_session") else (event.session_id or "")

        if sub == "list":
            n = 10
            if rest:
                try:
                    n = max(1, min(int(rest[0]), 50))
                except ValueError:
                    pass
            rows = self.gallery.search(limit=n, session=session_scope)
            if not rows:
                await self._send(event, "画廊还是空的～先画点图或收藏点图吧。")
            else:
                lines = ["最近的图片："]
                for i, r in enumerate(rows, 1):
                    tags = (" #" + " #".join(r["tags"])) if r["tags"] else ""
                    star = "★" if r["starred"] else ""
                    lines.append(
                        f"{i}. [{r['sha16']}]{star} {r['source']} "
                        f"{r['w']}×{r['h']} 用{r['use_count']}次{tags}\n"
                        f"   {r['prompt'][:60]}"
                    )
                lines.append("\n发图用：/gallery send <序号或sha前几位>")
                await self._send(event, "\n".join(lines))

        elif sub == "search":
            kw = " ".join(rest).strip()
            if not kw:
                await self._send(event, "用法：/gallery search <关键词>")
            else:
                rows = self.gallery.search(keyword=kw, limit=20, session=session_scope)
                if not rows:
                    await self._send(event, f"没找到含「{kw}」的图。")
                else:
                    lines = [f"检索「{kw}」的结果："]
                    for i, r in enumerate(rows, 1):
                        lines.append(f"{i}. [{r['sha16']}] {r['prompt'][:60]}")
                    lines.append("\n发图用：/gallery send <序号或sha前几位>")
                    await self._send(event, "\n".join(lines))

        elif sub == "tag":
            # /gallery tag [图标识] <标签...>
            if not rest:
                await self._send(event, "用法：/gallery tag [序号或sha前几位] <标签1> <标签2> ...")
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
                    await self._send(event, "请至少给一个标签，如：/gallery tag 合照 我们的合照")
                    event.stop_event()
                    return
                # 解析 target 到 sha
                sha = None
                if target is None:
                    # 指代消解：默认指向"这张图"
                    p = await self._gallery_resolve_ref(event)
                    if p:
                        # 用文件反查 sha：遍历 images 找相同绝对路径
                        for r in self.gallery.search(limit=1000):
                            if self.gallery.path_of(r["sha256"]) == p:
                                sha = r["sha256"]
                                break
                else:
                    if isinstance(target, int):
                        rows = self.gallery.search(limit=1000, session=session_scope)
                        if 1 <= target <= len(rows):
                            sha = rows[target - 1]["sha256"]
                    else:
                        sha = target
                if not sha:
                    await self._send(event, "没找到这张图（先发图、或指定 /gallery tag <序号> <标签>）")
                else:
                    self.gallery.add_tags(sha, tags)
                    await self._send(event, f"已给 [{sha[:16]}] 打标签：{'、'.join(tags)}")

        elif sub in ("findbytag", "bytag"):
            tag = " ".join(rest).strip()
            if not tag:
                await self._send(event, "用法：/gallery findByTag <标签>")
            else:
                rows = self.gallery.recall_by_tag(tag, limit=20)
                if not rows:
                    await self._send(event, f"没有带「{tag}」标签的图。")
                else:
                    lines = [f"带「{tag}」的图（共 {len(rows)} 张）："]
                    for i, r in enumerate(rows, 1):
                        star = "★" if r["starred"] else ""
                        lines.append(f"{i}. [{r['sha16']}]{star} {r['source']} {r['prompt'][:40]}")
                    lines.append("\n发图用：/gallery send <序号或sha前几位>")
                    await self._send(event, "\n".join(lines))

        elif sub == "send":
            if not rest:
                await self._send(event, "用法：/gallery send <序号或sha前几位>")
            else:
                arg = rest[0]
                if arg.isdigit():
                    rows = self.gallery.search(limit=1000, session=session_scope)
                    if 1 <= int(arg) <= len(rows):
                        await self._gallery_send_image(event, rows[int(arg) - 1]["sha256"])
                    else:
                        await self._send(event, "序号越界了。")
                else:
                    await self._gallery_send_image(event, arg)

        elif sub == "star":
            if not rest:
                await self._send(event, "用法：/gallery star <sha前几位>")
            else:
                ok = self.gallery.star(rest[0], 1)
                await self._send(event, "已收藏 ★（永不淘汰）。" if ok else "没找到这张图。")

        elif sub == "unstar":
            if rest:
                self.gallery.star(rest[0], 0)
                await self._send(event, "已取消收藏。")

        elif sub == "del":
            if not rest:
                await self._send(event, "用法：/gallery del <sha前几位>  （移入回收站，可在 /gallery trash 查看，purge 才真删）")
            else:
                ok = self.gallery.delete(rest[0])
                await self._send(event, "已移入回收站（用 /gallery purge 彻底删除）。" if ok else "删除失败（已收藏的图不可删，或不存在）。")

        elif sub == "trash":
            rows = self.gallery.search(trash=True, limit=100)
            if not rows:
                await self._send(event, "回收站是空的。")
            else:
                lines = [f"回收站（{len(rows)} 张，purge 才真删）："]
                for i, r in enumerate(rows, 1):
                    lines.append(f"{i}. {r['sha256'][:16]} 「{(r.get('prompt') or '')[:24]}」")
                await self._send(event, "\n".join(lines))

        elif sub == "restore":
            if not rest:
                await self._send(event, "用法：/gallery restore <sha前几位>")
            else:
                ok = self.gallery.restore(rest[0])
                await self._send(event, "已恢复。" if ok else "恢复失败（不在回收站或不存在）。")

        elif sub == "purge":
            if not rest:
                await self._send(event, "用法：/gallery purge <sha前几位>  （彻底删除，不可恢复）")
            else:
                ok = self.gallery.purge(rest[0])
                await self._send(event, "已彻底删除。" if ok else "删除失败（不在回收站或不存在）。")

        elif sub == "save":
            # /gallery save [标签...]：收藏当前/上一条消息的图（方案B）
            p = await self._gallery_resolve_ref(event)
            if not p:
                await self._send(event, "没找到要收藏的图（当前/上条消息没有图，本会话也没生成过图）。")
            else:
                tags = rest
                sha = self.gallery.archive_user_image(p, tags=tags)
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
                f"· 有效占用：{st.get('size_mb', 0)} MB（回收站 {st.get('trash_size_mb', 0)} MB）/ 上限 {st.get('max_total_mb', 0)} MB",
            ]
            await self._send(event, "\n".join(lines))

        else:
            await self._send(
                event,
                "未知子命令。可用：list [n] | search <关键词> | tag [图] <标签...> | "
                "findByTag <标签> | send <序号/sha> | star <sha> | unstar <sha> | "
                "del <sha> | save [标签...] | stats",
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
        以下所有情况都必须调用，禁止只用文字描述画面而不调用工具：
        1. 显式动词：「画一只猫」「生成一张风景图」「来张图：穿和服的少女」「画/生成/
           做/出一张图」等。
        2. 延续前文画图的请求（即使没有「画/图」这类动词，也必须调用）：
           - 「再来点」「再来几张」「续上」「继续」「出几张」「再来几张同款」「多来几张」
           - 「换个姿势」「换几个姿势」「沙发靠/窗边回眸/床边侧坐这种」「刚才那张再改改」
           - 「把腿露出来那版补上」「之前那张重跑一次」「同个姬发式再来几张」
           凡是用户基于「刚刚/之前画过的图」提出再来、续上、换姿势、重跑、补一张等
           延续性请求，一律视为新的画图意图，必须重新调用本工具生成全新图片。
        3. 用户吐槽/提醒你没画图（如「你咋忘了怎么画图了」「你又没画」「你怎么不画了」）：
           这本身就是催促画图，必须立即调用本工具，不要只用文字承诺会画图。
        把用户的画面描述作为 prompt 传入。即使描述比较口语化也应调用。
        核心原则：只要用户想要【新】的一张（或几张）图，无论他说得多隐晦，都调本工具，
        绝不能用文字复述「我会画 XX」来替代真实调用。

        重要约束（务必遵守，不要因为对话记忆而违反）：
        - 不要依赖历史记忆复用结果。即便本次对话里已经画过类似的图，只要用户再次
          表达画图意图，就必须重新调用本工具生成一张全新的图，绝不能以「之前画过」
          为由拒绝调用或直接复述旧结果。
        - 为让同一句描述也能产生不同的画面，请在 prompt 中自然地加入一些随机变化
          （如不同的姿势、光影、构图、背景细节、服饰点缀等），避免每次都生成雷同的图。
        - 本工具自动从消息中提取图片进行图生图，无需切换到其他工具。
        - 若用户明确提到"根据这张图/参考这张图/把这张图变成…"，必须传入 image 参数
          （消息中的图片URL）。

        工作流必查规则（务必遵守，防止"忘记工作流"）：
        - 不要凭记忆或猜测工作流名称！你记不住配置里有哪些工作流、哪些是默认、哪些
          支持图生图，工作流随时可能被管理员增删改。**每次需要指定工作流时，先调用
          comfyui_workflows 查询当前真实列表**，再从中选择正确的名称。
        - 用户明确要某类型/画风时（如"真人""写实""动漫""二次元""这个工作流"），
          必须先查列表，按名称语义匹配选对应工作流，再传入 workflow 或 img2img_workflow。
        - 只有用户完全没提画风、也不需要特定类型时，才可省略 workflow 参数交给默认工作流。
        - 若查询后仍无法确定，宁可不传 workflow（用默认）也不要瞎填一个不存在的名字。

        提示词语言规范（务必遵守）：
        - 先确定本次出图的工作流类型：真人/写实工作流（is_anima=false）或动漫/二次元工作流
          （is_anima=true，Anima）。可通过 comfyui_workflows 查询目标工作流的 is_anima 字段；
          未指定工作流时按默认工作流判断（默认通常是真人）。
        - 真人/写实工作流：prompt 首选中文，除非用户明确要求英文才用英文。
        - 动漫/二次元工作流（is_anima=true）：prompt 必须为英文标签化描述（如
          "1girl, solo, white dress, beach, backlight, masterpiece"），不得输出中文。
          即使用户用中文描述，也要翻译改写为英文 Danbooru 风格标签，不要原样透传中文。
        - 负向提示词（negative_prompt）同样遵循上述语言规则。
        - 判断依据：is_anima=true 时插件会把含中文的 prompt 发送给 Danbooru 翻译成英文标签，
          结果不可控；主动写英文标签才能获得稳定精确的出图效果。

        工作流选择规则（图生图）：
          插件已配置的工作流中，有些配置了「参考图节点」（image_node），说明该工作流
          支持图生图；有些没有，说明只能文生图。
          ⚠️ 重要：在调用本工具前，务必先调用 comfyui_workflows 查询工作流列表，
          确认有哪些工作流可用、哪些支持图生图，然后按以下优先级选择
          img2img_workflow：
          1. 只选列表中标记为「支持图生图」的工作流。
          2. 在支持图生图的工作流中，按名称语义匹配：
             - 用户说"转成真人/真人照片/写实" → 选名称含「真人」的
             - 用户说"转成动漫/二次元/动漫风" → 选名称含「动漫」或「二次元」的
             - 用户明确说了工作流名 → 直接用那个名字
          3. 如果都不匹配，传空让插件自动用图生图默认工作流。

        Args:
            prompt(string): 【必填】图像的正向提示词描述（中文或英文均可）。这是唯一必须填写的参数，
                不要留空，也不要用自然语言包裹，直接给出画面描述文本。
            negative_prompt(string): 负向提示词，可选，不填则留空。
            workflow(string): 文生图工作流名称。★用户明确要求特定画风/风格/类型时，必须先调用 comfyui_workflows 查询真实列表，再从中选确切名称传入；禁止凭记忆或猜测工作流名。用户完全没指定画风时才可留空（插件用默认工作流）。
            img2img_workflow(string): 图生图工作流名称。仅在消息附带参考图时生效。同样：除非用户明确要求特定风格，否则留空让插件用图生图默认工作流；确需指定时必须用 comfyui_workflows 查询到的确切名称。
            width(number): 图片宽度，0 或不填表示使用工作流默认宽度。用户明确要求宽高时传入（如"1024x1024"、"宽512"）。
            height(number): 图片高度，0 或不填表示使用工作流默认高度。用户明确要求宽高时传入。
            loras(array[string]): 需要启用的 LoRA 名称列表，例如 ["catgirl", "rain"]。留空则使用配置中默认启用的 LoRA。
            seed(number): 随机种子，0 或不填表示每次随机。用户明确要求"固定/复现/用同样的种子"时传入具体数字。
            image(string): 图生图参考图的 URL。仅当用户在消息里明确附带了图片且需要按该图变换时传入；多数情况用户直接发图时无需传此参数，插件会自动从消息/会话提取图片。
            denoise(number): 降噪幅度/重绘强度（0~1），仅图生图有效。不传或 -1 则用工作流配置默认值。用户明确要求"改多少/像不像原图"时传入。

        补充说明：
        - 用户未明确要求宽高/lora/seed/denoise 时，这些参数可不传，插件自动使用工作流或配置默认值。
        - 图生图：即使不传 image，只要用户消息/会话里有图，插件也会自动提取做参考图。
        """
        # LLM 工具开关：关闭时拒绝本插件 LLM 的自动调用，
        # 但伴侣插件等第三方主动调用（带 source 标记）不受影响。
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if plugin is None:
            plugin = self
        if not plugin._cfg("enable_llm_tools", True) and not (source and source.strip() == SOURCE_COMPANION_PLUGIN):
            return "LLM 画图工具已关闭，请使用指令绘图（/draw、/img2img、/画xxx 等）。"

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
        if image and image.strip():
            img_url = image.strip()
            logger.info(f"[取图] llm_draw image 参数: {img_url}")
            p = await _image_to_local_path(img_url)
            if p:
                init_images.append(p)
                got_explicit_image = True
                logger.info(f"[取图] image 参数下载成功: {p}")
            else:
                logger.warning(f"[取图] image 参数下载失败: {img_url}")

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

        # ③ 判定图生图：LLM 显式传图 OR 本次消息/引用里有图
        is_img2img = got_explicit_image or bool(event_images)

        # ④ 已判定图生图、但参考图还没拿到（图没进 event，如引用图解析失败）时，
        #    才用历史/会话/生成图兜底补一张参考图。纯文生图绝不进入这里。
        if is_img2img and not init_images:
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
                logger.info("[取图] llm_draw 已判定图生图但未取到参考图，尝试无图提交")

        if init_images:
            logger.info(f"[取图] llm_draw 最终取得参考图 {len(init_images)} 张 -> {init_images}")
        elif is_img2img:
            logger.info("[取图] llm_draw 图生图模式但无参考图可用")
        else:
            logger.info("[取图] llm_draw 文生图模式（未取图）")

        # ── 决定工作流与模式 ─────────────────────────────────────────
        # 优先级：
        #   image + img2img_workflow → 用 img2img_workflow
        #   image + workflow          → 用 workflow（语义匹配）
        #   image + 都没传            → 默认图生图工作流
        #   无 image                  → workflow 或默认文生图工作流
        # is_img2img 已在取图段判定（LLM 显式传 image OR 本次消息/引用有图）。
        # 若判定为图生图但最终没拿到任何参考图，降级为文生图，避免无图还走图生图工作流
        # （导致去找 LoadImage 节点而报错）。
        if is_img2img and not init_images:
            logger.warning("[取图] llm_draw 图生图模式但无参考图，降级为文生图提交")
            is_img2img = False
        if is_img2img and img2img_workflow and img2img_workflow.strip():
            resolved_wf = img2img_workflow.strip()
        elif is_img2img and workflow and workflow.strip():
            resolved_wf = workflow.strip()
        else:
            resolved_wf = (workflow or "").strip() or None

        # 与 llm_draw 一致：先按通用规则拆分正/负向并清洗标记
        positive, parsed_neg = plugin._split_external_prompt(prompt)
        if source and source.strip() == SOURCE_COMPANION_PLUGIN:
            cpos, cneg = plugin._format_companion_prompt(prompt)
            if cpos:
                positive = cpos
            if cneg:
                parsed_neg = cneg
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
            # 图片与固定小报告（尺寸/大小/耗时/时间）已由插件主动 event.send 发出，
            # 模型无需也不应再补述文件信息。只回一句极简事实，明确指示简短收尾即可。
            return "图片已发送给用户（含文件详情）。请用一句话简短收尾即可，不要重复文件信息。"
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
            keyword(string): search 模式下的提示词关键词；或 send 模式下传序号/sha 前几位。
            tag(string): recall/save 模式下的语义标签（如「合照」）。可含空格，多个标签用空格分隔（save 时）。
            limit(int): 返回数量上限，默认 10。

        注意：发图由插件在本地完成，模型不会、也不需要接触任何文件路径。
        """
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if plugin is None:
            plugin = self
        if plugin.gallery is None:
            return "图库未启用或初始化失败，无法检索/收藏图片。"
        g = plugin.gallery
        cross = bool(plugin._cfg("gallery", {}).get("cross_session"))
        session = None if cross else (getattr(event, "session_id", "") or "")

        if mode == "recall":
            if not tag or not tag.strip():
                return "recall 模式需要 tag 参数（语义标签，如「合照」）。"
            rows = g.recall_by_tag(tag.strip(), limit=limit)
            if not rows:
                return f"图库里没有带「{tag.strip()}」标签的图。可先用 /gallery save 或对话里说「收藏这张，标签叫XX」来打标签。"
            if len(rows) == 1:
                ok = await plugin._gallery_send_image(event, rows[0]["sha256"])
                return ("已发送该图。" if ok else "找到图但发送失败。")
            # 多张：列出让用户选（按确认口径）
            lines = [f"带「{tag.strip()}」的图有 {len(rows)} 张，回复编号即可发对应那张："]
            for i, r in enumerate(rows, 1):
                star = "★" if r["starred"] else ""
                lines.append(f"{i}. [{r['sha16']}]{star} {r['prompt'][:40]}")
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
            rows = g.search(keyword=keyword.strip(), limit=limit, session=session)
            if not rows:
                return f"没找到含「{keyword.strip()}」的图。"
            if len(rows) == 1:
                ok = await plugin._gallery_send_image(event, rows[0]["sha256"])
                return ("已发送该图。" if ok else "找到图但发送失败。")
            lines = [f"检索「{keyword.strip()}」的结果："]
            for i, r in enumerate(rows, 1):
                lines.append(f"{i}. [{r['sha16']}] {r['prompt'][:40]}")
            lines.append("回复编号即可发对应那张。")
            return "\n".join(lines)

        elif mode == "save":
            p = await plugin._gallery_resolve_ref(event)
            if not p:
                return "没有可收藏的图（当前/上条消息没有图，本会话也没生成过图）。"
            tags = tag.split() if tag else []
            sha = g.archive_user_image(p, tags=tags)
            if not sha:
                return "收藏失败（图库可能未启用）。"
            extra = (" 标签：" + "、".join(tags)) if tags else ""
            return f"已收藏这张图 [{sha[:16]}]{extra}。以后说「把{'/'.join(tags) if tags else '这张'}发我」即可召回。"

        elif mode == "send":
            arg = (keyword or "").strip()
            if not arg:
                return "send 模式需要 keyword 参数传序号（如「3」）或 sha 前几位。"
            if arg.isdigit():
                rows = g.search(limit=1000, session=session)
                if 1 <= int(arg) <= len(rows):
                    ok = await plugin._gallery_send_image(event, rows[int(arg) - 1]["sha256"])
                    return ("已发送。" if ok else "发送失败。")
                return "序号越界。"
            ok = await plugin._gallery_send_image(event, arg)
            return ("已发送。" if ok else "没找到这张图。")

        elif mode == "list":
            rows = g.search(limit=limit, session=session)
            if not rows:
                return "画廊还是空的～先画点图或收藏点图吧。"
            lines = ["最近的图片："]
            for i, r in enumerate(rows, 1):
                t = (" #" + " #".join(r["tags"])) if r["tags"] else ""
                lines.append(f"{i}. [{r['sha16']}]{'★' if r['starred'] else ''} {r['source']}{t}")
            return "\n".join(lines)

        elif mode == "stats":
            st = g.stats()
            return (
                f"图库：共 {st.get('total',0)} 张（生图{st.get('gen',0)}/参考{st.get('ref',0)}/"
                f"用户{st.get('user',0)}），收藏 {st.get('starred',0)}，带标签 {st.get('tagged',0)}；"
                f"有效占用 {st.get('size_mb',0)} MB（回收站 {st.get('trash_size_mb',0)} MB）"
                f"/ 上限 {st.get('max_total_mb',0)} MB"
            )
        else:
            return "未知 mode。可用：recall / search / save / send / list / stats。"

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
        default_i2i = self._cfg("default_img2img_workflow", "")
        if default:
            lines.append(f"\n文生图默认工作流: {default}")
        if default_i2i:
            lines.append(f"图生图默认工作流: {default_i2i}")

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
        - 负向提示词（negative_prompt）同样遵循上述语言规则。

        工作流选择规则：
          插件已配置的工作流中，有些配置了「参考图节点」（image_node），说明该工作流
          支持图生图；有些没有，说明只能文生图。
          ⚠️ 重要：在调用本工具前，务必先调用 comfyui_workflows 查询工作流列表，
          确认有哪些工作流可用、哪些支持图生图，然后按以下优先级选择
          img2img_workflow：
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
            loras(array[string]): 需要启用的 LoRA 名称列表，例如 ["catgirl", "rain"]。留空则使用配置中默认启用的 LoRA。
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
            # 图片与固定小报告（尺寸/大小/耗时/时间）已由插件主动 event.send 发出，
            # 模型无需也不应再补述文件信息。只回一句极简事实，明确指示简短收尾即可。
            return "图片已发送给用户（含文件详情）。请用一句话简短收尾即可，不要重复文件信息。"
        return "本次生图失败。请用一句话简短向用户说明生成遇到问题即可，不要复述本提示。"
