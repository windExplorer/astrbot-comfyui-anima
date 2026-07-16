"""AstrBot ComfyUI 绘图插件（支持多服务器、多工作流、LoRA 管理、Anima 标签翻译）。"""

import os
import json
import random
import re
import time
import uuid
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register

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

# 可爱随机话术：提交绘图后提示用，避免每次都相同
_QUEUE_HINTS_GENERATING = [
    "好嘞~ 小画家已经开始动笔啦，请稍候✨",
    "收到！正在为你努力出图中，马上就好🎨",
    "嗯嗯，已经在画啦，乖乖等一下下就好~",
    "任务已提交，图图正在生成中，马上飞到你面前🥰",
    "已收到！正在悄悄为你画图，稍等片刻哦🌸",
    "好~ 正在生成中，喝口水的功夫就出来啦🍵",
]

_QUEUE_HINTS_QUEUED = [
    "任务已提交，前面还有 {n} 位在排队呢，先喝口水等等吧🍵",
    "排上号啦~ 前面还有 {n} 位，马上就轮到你😊",
    "小本本记上了，前面有 {n} 位在排队，稍安勿躁~",
    "当前前面还有 {n} 位哦，图图不会跑的，等等嘛🌸",
    "已经排好队啦，前面 {n} 位，马上给你画上🎀",
    "收到！前面还有 {n} 位在排队，稍等一下下就好💕",
]

# 面向用户的可爱错误话术：真实报错只写进日志，用户只看到经过包装的萌系提示。
# 按错误类别分池，每类多条随机取一，避免每次都一样。
_ERR_HINTS = {
    # 连不上绘图服务器（连接被拒 / 掉线 / DNS 解析失败等）
    "connect": [
        "呜…绘图服务器好像联系不上了呢(´；ω；｀)，可能它正在打盹💤，麻烦联系管理员看看吧～",
        "咦？我怎么敲不开绘图服务器的小门啦，它八成是掉线睡着了，请通知管理员唤醒一下嘛～",
        "呜哇，绘图服务器一直没有回应我(>﹏<)，请联系管理员检查一下它还好不好哦！",
    ],
    # 超时：连上了但迟迟不出结果
    "timeout": [
        "呜…图图画了好久好久还没好，绘图服务器可能有点累啦，晚点再叫我画一次好不好？⏳",
        "等得我小脚都麻啦，图图迟迟没出现，可能服务器在忙，稍后再试试嘛～🥺",
        "哎呀，等太久超时啦，说不定服务器还在偷偷画，晚点再来找我看看嘛🌙",
    ],
    # 服务器返回了错误状态（HTTP 4xx/5xx 等）
    "server": [
        "绘图服务器闹小脾气了(>﹏<)，回了句我听不懂的话，麻烦管理员帮忙瞧瞧吧～",
        "呜…绘图服务器好像不太舒服，出了点小错，请联系管理员检查一下哦！",
        "咦，服务器那边返回了奇怪的回应呢，八成是它累坏啦，请找管理员看看嘛～",
    ],
    # 兜底：未归类的意外
    "generic": [
        "呜…出了一点点小意外，图图没能顺利画出来，请稍后再试或联系管理员嘛(´•̥ω•̥`)",
        "哎呀，遇到了一个小状况，图图暂时画不了啦，晚点再来找我玩好不好～",
        "唔…有个我也没料到的小问题冒出来啦，麻烦联系管理员帮忙看看哦(｡•́︿•̀｡)",
    ],
    # 任务完成但没找到输出图片（多半是工作流输出节点没配对）
    "no_image": [
        "咦…任务明明完成啦，可我怎么没找到图图呢？可能工作流的输出节点没配置对，麻烦联系管理员看看嘛～",
        "呜，画是画完了，但图图好像躲起来了找不到(・_・;)，请管理员检查下输出节点的配置哦！",
    ],
    # 工作流（图纸）加载失败
    "workflow": [
        "呜…绘图的小图纸（工作流）没能读出来，可能是文件放错地方或格式坏掉啦，请联系管理员检查一下哦(´•ω•`)",
        "咦，我翻不开这份绘图图纸呢，八成是文件名或内容有点问题，麻烦管理员瞧瞧嘛～",
    ],
    # 服务器没返回任务 ID
    "no_task_id": [
        "呜…绘图服务器收下了请求却没给我任务小票，可能它有点迷糊，请联系管理员看看吧～",
        "咦，提交出去了但没拿到任务编号呢，服务器好像走神啦，麻烦管理员检查一下哦！",
    ],
}


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
        # 仅用于提示“前面还有几位”。
        self._server_pending: dict[str, list] = {}

        # 插件数据目录：temp/ 存出图，workflow/ 存工作流文件
        self.data_dir = self._get_data_dir()
        self.temp_dir = self.data_dir / "temp"
        self.workflow_dir = self.data_dir / "workflow"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        pass

    async def terminate(self) -> None:
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

    def _cleanup_temp(self, max_age: float = 86400) -> None:
        """清理 temp/ 中超过 max_age 秒的旧图片，避免无限增长。"""
        try:
            now = time.time()
            for f in self.temp_dir.iterdir():
                if f.is_file() and now - f.stat().st_mtime > max_age:
                    f.unlink()
        except Exception:
            pass

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
        """把 --名称-预设名 引用的预设提示词追加到正向提示词。

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

    def _resolve_workflow(self, name: str | None = None) -> dict:
        workflows = self._workflows()
        if not workflows:
            raise ValueError("未配置任何工作流，请先在插件配置中添加。")
        if not name:
            name = self._cfg("default_workflow", "")
        if name:
            for w in workflows:
                if w.get("name") == name:
                    return w
            raise ValueError(f"找不到名为「{name}」的工作流。")
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

    # ------------------------------------------------------------------ #
    # 核心：提交并等待出图（异步生成器，yield 消息）
    # ------------------------------------------------------------------ #
    async def _send(self, event: AstrMessageEvent, text: str) -> None:
        """主动发送一条文本消息（不占用 yield，避免命令 pipeline 在首个
        yield 后中断；同时标记 _has_send_oper，防止触发后续 LLM 阶段）。"""
        await event.send(MessageChain([Plain(str(text))]))

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
        """当前服务器上、本次提交之前已排队的任务数量（即“前面还有几位”）。"""
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
    ):
        # 记录最近一次事件，供 LLM 工具在 event 异常时为兜底使用
        self._last_event = event
        if not positive or not positive.strip():
            await self._send(event, "请提供正向提示词，例如：/draw 一只白色水手服少女")
            return

        try:
            wf = self._resolve_workflow(workflow_name)
            server = self._resolve_server(wf.get("server_name") or None)
        except ValueError as e:
            # 配置类问题：原因是插件自己给出的可读文案，保留原因但用可爱口吻包裹
            logger.warning(f"[绘图失败][配置] {e}")
            await self._send(event, f"呜…绘图配置好像有点小问题：{e} 麻烦联系管理员调整一下吧～")
            return

        # 加载工作流 JSON
        self._cleanup_temp()
        try:
            prompt = workflow_builder.load_workflow(
                self._resolve_workflow_path(wf), wf.get("workflow_json")
            )
        except Exception as e:
            await self._send(event, self._friendly_error(e, "工作流加载", "workflow"))
            return

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

        # 注入 LoRA 预设提示词（--名称-预设名）：追加到正/负向提示词
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

        # 注入宽高（宽高同属一个节点）
        w = width or int(wf.get("default_width", 512) or 512)
        h = height or int(wf.get("default_height", 512) or 512)
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
        enabled = workflow_builder.apply_loras(
            prompt, loras_cfg, active_map, anchor=wf.get("lora_anchor") or None,
            clip_anchor=wf.get("lora_clip") or None,
            on_warning=lambda m: logger.warning(m),
            on_info=lambda m: logger.info(m),
            model_only=True,
        )
        if enabled:
            logger.info(f"本次启用的 LoRA: {enabled}")

        # 随机化种子（未指定 --seed 时），避免每次出图完全相同
        seeds_used = workflow_builder.randomize_seed(prompt, seed)
        if seeds_used:
            logger.info(f"本次种子: {seeds_used}")

        # 调试用：打印最终提交给 ComfyUI 的工作流（拼接结果），便于核对 LoRA 注入/禁用是否正确
        logger.info(
            "最终工作流（提交给 ComfyUI）:\n"
            + json.dumps(prompt, ensure_ascii=False, indent=2)
        )

        # 提交到 ComfyUI
        client = self._build_client(server)
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
                return

            # 记录最近任务，供 /queuestatus 使用
            try:
                self._last_prompt[event.session_id or "global"] = prompt_id
            except Exception:
                pass

            # 本地队列：本次提交之前已排队的任务数即为“前面还有几位”（只提示一次，
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
                    return

                images = comfyui_client.extract_images(history, wf.get("output_node"))
                if not images:
                    logger.warning("[绘图失败][无图] 任务完成但未找到输出图片节点")
                    await self._send(event, self._cute("no_image"))
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
                    yield event.image_result(str(tmp_path))
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
        prompt, lora_map, lora_presets, width, height, wf_name, seed = self._parse_draw_args(args or "")
        if not prompt.strip():
            await self._send(event, 
                "用法：/draw 一只白色水手服少女 --wf sd --lora catgirl:0.8 --w 768 --h 768 [--seed 12345]"
            )
            return
        async for m in self._do_draw(
            event, wf_name, prompt, "", width, height, lora_map, lora_presets, seed
        ):
            yield m
        # 收尾时再终止事件：避免开头 stop_event 导致 pipeline 在第一个 yield
        # 后中断 _do_draw 的协程（等待/下载图片的代码不再执行，temp 无图）。
        event.stop_event()

    def _parse_draw_args(self, text: str):
        """解析绘图指令参数，返回 (prompt, lora_map, lora_presets, width, height, workflow, seed)。

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
        权重缺省为 1.0。lora_map 为 {名称: 权重|None}，lora_presets 为 {名称: 预设名}。
        """
        # 已知“取值型”参数：后接一个值 token（--wf sd / --w 768 / --lora 名:权）
        VALUE_FLAGS = {"--lora", "--wf", "--w", "--h", "--seed"}
        lora_map: dict[str, float | None] = {}
        lora_presets: dict[str, str] = {}
        width = height = wf_name = seed = None

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
        return prompt, (lora_map or None), (lora_presets or None), width, height, wf_name, seed

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
            await self._send(event, "呜…保存 LoRA 设置时出了点小状况，请稍后再试或联系管理员嘛(´•ω•`)")
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
        """列出工作流，或设置默认工作流：/workflows set 名称"""
        args = self._strip_command(event.message_str, "workflows")
        m = re.match(r"set\s+(\S+)", (args or "").strip())
        if m:
            name = m.group(1)
            try:
                self._resolve_workflow(name)
            except ValueError as e:
                await self._send(event, str(e))
                return
            self.config["default_workflow"] = name
            self.config.save_config()
            await self._send(event, f"已将默认工作流设为「{name}」。")
            event.stop_event()
            return
        workflows = self._workflows()
        default = self._cfg("default_workflow", "")
        if not workflows:
            await self._send(event, "尚未配置任何工作流。")
            event.stop_event()
            return
        lines = ["已配置的工作流："]
        for w in workflows:
            tag = "（默认）" if w.get("name") == default else ""
            anima = " [Anima]" if w.get("is_anima") else ""
            lines.append(f"- {w.get('name')}{anima}{tag}")
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
            "/draw 提示词 [--wf 工作流] [--lora 名称[:权重] | --名称[:权重] | --名称-预设[:权重]] [--w 宽] [--h 高] [--seed 数字]  绘图\n"
            "  · LoRA 简写：--安魂曲 等价于 --lora 安魂曲:1；--安魂曲:0.5 等价于 --lora 安魂曲:0.5（冒号支持半角 : 与全角 ：）\n"
            "  · LoRA 预设：--安魂曲-预设1 表示用「安魂曲」的「预设1」提示词（在全局 LoRA 库里配置多套预设）。\n"
            "/loralist [--wf 工作流]   列出 LoRA（含预设）\n"
            "/loraon 名称 [--wf 工作流]  启用 LoRA（持久化到工作流默认列表）\n"
            "/loraoff 名称 [--wf 工作流] 禁用 LoRA（持久化）\n"
            "/queuestatus [--wf 工作流]  查看队列与排队位置\n"
            "/workflows [set 名称]   列出/设置默认工作流\n"
            "也可直接对 AI 说“画一只猫，使用 xxx lora”，由 AI 自动调用绘图工具。"
        )
        await self._send(event, text)
        event.stop_event()

    # ------------------------------------------------------------------ #
    # LLM 工具：comfyui_draw（AI 对话触发）
    # ------------------------------------------------------------------ #
    @filter.llm_tool(name="comfyui_draw")
    async def llm_draw(
        self,
        event: AstrMessageEvent,
        prompt: str,
        negative_prompt: str = "",
        workflow: str = "",
        width: int = 0,
        height: int = 0,
        loras: list = None,
        seed: int = 0,
    ):
        """使用 ComfyUI 根据文本提示词生成图片并返回给用户。

        触发时机：当用户表达任何想要绘制/生成/画一张图片的意图时（如「画一只猫」、
        「生成一张风景图」、「来张图：穿和服的少女」），务必调用此工具，并把用户的
        画面描述作为 prompt 传入。即使描述比较口语化也应调用。

        重要约束（务必遵守，不要因为对话记忆而违反）：
        - 不要依赖历史记忆复用结果。即便本次对话里已经画过类似的图，只要用户再次
          表达画图意图，就必须重新调用本工具生成一张全新的图，绝不能以「之前画过」
          为由拒绝调用或直接复述旧结果。
        - 为让同一句描述也能产生不同的画面，请在 prompt 中自然地加入一些随机变化
          （如不同的姿势、光影、构图、背景细节、服饰点缀等），避免每次都生成雷同的图。

        Args:
            prompt(string): 图像的正向提示词描述（中文或英文均可）。
            negative_prompt(string): 负向提示词，可选，不填则留空。
            workflow(string): 要使用的工作流名称，留空使用默认工作流。
            width(number): 图片宽度，0 表示使用工作流默认宽度。
            height(number): 图片高度，0 表示使用工作流默认高度。
            loras(array[string]): 需要启用的 LoRA 名称列表，例如 ["catgirl", "rain"]。留空则使用配置中默认启用的 LoRA。
            seed(number): 随机种子，0 或不填表示每次随机，填具体数字可复现同一张图。
        """
        # 部分 AstrBot 版本下 self/event 绑定可能异常（self 为 None 或 event 为 None），
        # 这里用全局实例与最近事件兜底，避免 'NoneType' object has no attribute '_do_draw'。
        plugin = self if isinstance(self, ComfyUIDrawPlugin) else _PLUGIN_INSTANCE
        if plugin is None:
            plugin = self
        if not isinstance(event, AstrMessageEvent):
            event = getattr(plugin, "_last_event", None)
        if event is None:
            yield "⚠️ 绘图工具未能获取到会话事件，请稍后重试，或直接使用 /draw 指令绘图。"
            return

        lora_map = None
        if loras:
            lora_map = {str(n).strip(): None for n in loras if str(n).strip()}
        async for m in plugin._do_draw(
            event,
            workflow or None,
            prompt,
            negative_prompt or "",
            width or None,
            height or None,
            lora_map,
            None,
            seed or None,
        ):
            yield m
