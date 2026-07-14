"""AstrBot ComfyUI 绘图插件（支持多服务器、多工作流、LoRA 管理、Anima 标签翻译）。"""

import os
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

    def _loras_of(self, wf: dict) -> list[dict]:
        """从工作流配置解析 LoRA 列表。

        优先使用新版文本格式 loras_text（每行 名称|别名|权重|0/1）；
        若为空则兼容旧版结构化 loras 列表。
        """
        text = (wf.get("loras_text") or "").strip()
        if text:
            return self._parse_loras_text(text)
        return wf.get("loras", []) or []

    @staticmethod
    def _parse_loras_text(text: str) -> list[dict]:
        """解析多行 LoRA 文本为配置列表。每行：名称|别名(文件名)|权重|0/1。"""
        out: list[dict] = []
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if not parts[0]:
                continue
            name = parts[0]
            model_name = parts[1] if len(parts) > 1 else ""
            try:
                weight = float(parts[2]) if len(parts) > 2 and parts[2] != "" else 1.0
            except ValueError:
                weight = 1.0
            enabled = True
            if len(parts) > 3 and parts[3] != "":
                enabled = parts[3] not in ("0", "0.0", "false", "False", "禁用", "关")
            out.append(
                {
                    "name": name,
                    "model_name": model_name,
                    "weight": weight,
                    "enabled": enabled,
                    # load_node 留空，apply_loras 时自动探测工作流里的 LoraLoader 节点
                    "load_node": "",
                    "model_input": "lora_name",
                    "strength_model_input": "strength_model",
                    "strength_clip_input": "strength_clip",
                    "keywords": [],
                }
            )
        return out

    @staticmethod
    def _serialize_loras_text(loras: list[dict]) -> str:
        """将 LoRA 列表序列化回 名称|别名|权重|0/1 文本（用于 loraon/loraoff 持久化）。"""
        lines = []
        for l in loras:
            name = (l.get("name") or "").strip()
            if not name:
                continue
            model = (l.get("model_name") or "").strip()
            weight = l.get("weight", 1.0)
            wstr = str(int(weight)) if float(weight) == int(weight) else str(weight)
            enabled = 1 if l.get("enabled", False) else 0
            lines.append(f"{name}|{model}|{wstr}|{enabled}")
        return "\n".join(lines)

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
            float(cfg.get("popularity", 0.85)),
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
            await self._send(event, f"配置错误：{e}")
            return

        # 加载工作流 JSON
        self._cleanup_temp()
        try:
            prompt = workflow_builder.load_workflow(
                self._resolve_workflow_path(wf), wf.get("workflow_json")
            )
        except Exception as e:
            await self._send(event, f"工作流加载失败：{e}")
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

        # 注入提示词（正/负下输入框名固定为 text，无需配置）
        workflow_builder.set_text_node(
            prompt, wf.get("positive_node"), "text", positive
        )
        if negative:
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
        enabled = workflow_builder.apply_loras(prompt, loras_cfg, active_map)
        if enabled:
            logger.info(f"本次启用的 LoRA: {enabled}")

        # 随机化种子（未指定 --seed 时），避免每次出图完全相同
        seeds_used = workflow_builder.randomize_seed(prompt, seed)
        if seeds_used:
            logger.info(f"本次种子: {seeds_used}")

        # 提交到 ComfyUI
        client = self._build_client(server)
        srv_key = self._server_key(server)
        try:
            try:
                result = await client.queue_prompt(prompt)
                prompt_id = result.get("prompt_id")
            except Exception as e:
                await self._send(event, f"提交到 ComfyUI 失败：{e}")
                return

            if not prompt_id:
                await self._send(event, "ComfyUI 未返回任务 ID，提交可能失败。")
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
                    await self._send(event, 
                        f"出图超时（{timeout} 秒），但 ComfyUI 可能仍在生成，请稍后在 ComfyUI 中确认结果。"
                    )
                    return

                images = comfyui_client.extract_images(history, wf.get("output_node"))
                if not images:
                    await self._send(event, "任务完成，但未找到输出图片节点。")
                    return

                for img in images:
                    try:
                        data = await client.get_image(
                            img["filename"],
                            img.get("subfolder", ""),
                            img.get("type", ""),
                        )
                    except Exception as e:
                        await self._send(event, f"下载图片失败：{e}")
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
        """通过指令绘图。用法：/draw 提示词 [--wf 工作流名] [--lora 名称[:权重]] [--w 宽] [--h 高]"""
        args = self._strip_command(event.message_str, "draw")
        prompt, lora_map, width, height, wf_name, seed = self._parse_draw_args(args or "")
        if not prompt.strip():
            await self._send(event, 
                "用法：/draw 一只白色水手服少女 --wf sd --lora catgirl:0.8 --w 768 --h 768 [--seed 12345]"
            )
            return
        async for m in self._do_draw(
            event, wf_name, prompt, "", width, height, lora_map, seed
        ):
            yield m
        # 收尾时再终止事件：避免开头 stop_event 导致 pipeline 在第一个 yield
        # 后中断 _do_draw 的协程（等待/下载图片的代码不再执行，temp 无图）。
        event.stop_event()

    def _parse_draw_args(self, text: str):
        """解析绘图指令参数，返回 (prompt, lora_map, width, height, workflow, seed)。"""
        lora_map: dict[str, float | None] = {}
        width = None
        height = None
        wf_name = None
        seed = None

        def consume(pattern):
            out = []
            for m in re.finditer(pattern, text):
                out.append(m)
            return out

        for m in consume(r"--lora\s+(\S+?(?::\d+(?:\.\d+)?)?)"):
            token = m.group(1)
            if ":" in token:
                nm, wt = token.split(":", 1)
                try:
                    lora_map[nm.strip()] = float(wt)
                except ValueError:
                    lora_map[nm.strip()] = None
            else:
                lora_map[token.strip()] = None
            text = text.replace(m.group(0), " ")

        for m in consume(r"--wf\s+(\S+)"):
            wf_name = m.group(1)
            text = text.replace(m.group(0), " ")

        for m in consume(r"--w\s+(\d+)"):
            width = int(m.group(1))
            text = text.replace(m.group(0), " ")

        for m in consume(r"--h\s+(\d+)"):
            height = int(m.group(1))
            text = text.replace(m.group(0), " ")

        for m in consume(r"--seed\s+(\d+)"):
            try:
                seed = int(m.group(1))
            except ValueError:
                seed = None
            text = text.replace(m.group(0), " ")

        prompt = text.strip()
        return prompt, (lora_map or None), width, height, wf_name, seed

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
            raw = l.get("keywords") or []
            if isinstance(raw, str):
                kw_str = ", ".join(k.strip() for k in raw.split(",") if k.strip())
            else:
                kw_str = ", ".join(str(k).strip() for k in raw)
            model = l.get("model_name") or ""
            lines.append(
                f"- {l.get('name')}（{state}，权重 {l.get('weight', 1.0)}"
                + (f"，文件 {model}" if model else "")
                + (f"，关键词：{kw_str}" if kw_str else "")
                + "）"
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
            loras = self._loras_of(wf)
            target = None
            for i, l in enumerate(loras):
                if (l.get("name") or "").strip() == name:
                    target = i
                    break
            if target is None:
                await self._send(event, f"工作流「{wf.get('name')}」中找不到 LoRA「{name}」。")
                return
            loras[target]["enabled"] = enabled
            workflows[wf_index]["loras_text"] = self._serialize_loras_text(loras)
            workflows[wf_index].pop("loras", None)
            self.config["workflows"] = workflows
            self.config.save_config()
        except ValueError as e:
            await self._send(event, str(e))
            return
        except Exception as e:
            await self._send(event, f"操作失败：{e}")
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
            "/draw 提示词 [--wf 工作流] [--lora 名称[:权重]] [--w 宽] [--h 高] [--seed 数字]  绘图\n"
            "/loralist [--wf 工作流]   列出 LoRA\n"
            "/loraon 名称 [--wf 工作流]  启用 LoRA\n"
            "/loraoff 名称 [--wf 工作流] 禁用 LoRA\n"
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
            seed or None,
        ):
            yield m
