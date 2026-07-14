"""AstrBot ComfyUI 绘图插件（支持多服务器、多工作流、LoRA 管理、Anima 标签翻译）。"""

import os
import re
import time
import uuid
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.message_components import Image
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


@register(
    "astrbot_plugin_comfyui_anima",
    "astrbot-comfyui-anima",
    "通过指令或 AI 对话调用 ComfyUI 绘图，支持多服务器、多工作流、LoRA 管理与 Anima 标签翻译",
    "1.0.0",
)
class ComfyUIDrawPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        # 记录每个会话最近一次提交的任务，用于 /queuestatus
        self._last_prompt: dict[str, str] = {}

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
        )

    # ------------------------------------------------------------------ #
    # 核心：提交并等待出图（异步生成器，yield 消息）
    # ------------------------------------------------------------------ #
    async def _do_draw(
        self,
        event: AstrMessageEvent,
        workflow_name: str | None,
        positive: str,
        negative: str,
        width: int | None,
        height: int | None,
        lora_map: dict[str, float | None] | None,
    ):
        if not positive or not positive.strip():
            yield event.plain_result("请提供正向提示词，例如：/draw 一只白色水手服少女")
            return

        try:
            wf = self._resolve_workflow(workflow_name)
            server = self._resolve_server(wf.get("server_name") or None)
        except ValueError as e:
            yield event.plain_result(f"配置错误：{e}")
            return

        # 加载工作流 JSON
        self._cleanup_temp()
        try:
            prompt = workflow_builder.load_workflow(
                self._resolve_workflow_path(wf), wf.get("workflow_json")
            )
        except Exception as e:
            yield event.plain_result(f"工作流加载失败：{e}")
            return

        # Anima 工作流：中文提示词翻译为 Danbooru 标签
        danbooru = self._build_danbooru()
        if wf.get("is_anima") and danbooru is not None:
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

        # 注入提示词
        workflow_builder.set_text_node(
            prompt, wf.get("positive_node"), wf.get("positive_input", "text"), positive
        )
        if negative:
            workflow_builder.set_text_node(
                prompt,
                wf.get("negative_node"),
                wf.get("negative_input", "text"),
                negative,
            )

        # 注入宽高
        w = width or int(wf.get("default_width", 512) or 512)
        h = height or int(wf.get("default_height", 512) or 512)
        workflow_builder.set_number_node(
            prompt, wf.get("width_node"), wf.get("width_input", "width"), w
        )
        workflow_builder.set_number_node(
            prompt, wf.get("height_node"), wf.get("height_input", "height"), h
        )

        # 注入 LoRA（合并关键词自动匹配）
        loras_cfg = wf.get("loras", []) or []
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

        # 提交到 ComfyUI
        client = self._build_client(server)
        try:
            try:
                result = await client.queue_prompt(prompt)
                prompt_id = result.get("prompt_id")
            except Exception as e:
                yield event.plain_result(f"提交到 ComfyUI 失败：{e}")
                return

            if not prompt_id:
                yield event.plain_result("ComfyUI 未返回任务 ID，提交可能失败。")
                return

            # 记录最近任务，供 /queuestatus 使用
            try:
                self._last_prompt[event.session_id or "global"] = prompt_id
            except Exception:
                pass

            # 返回队列位置
            if self._cfg("return_queue_position", True):
                pos = await client.get_queue_position(prompt_id)
                if pos is None:
                    yield event.plain_result("任务已提交，正在排队（无法获取队列位置）。")
                elif pos == 0:
                    yield event.plain_result("任务已提交，正在生成中…")
                else:
                    yield event.plain_result(f"任务已提交，前面还有 {pos} 位在排队。")

            # 等待出图
            timeout = int(self._cfg("draw_timeout", 120))
            interval = max(1, int(self._cfg("queue_poll_interval", 2)))
            history = await client.wait_for_result(prompt_id, timeout, interval)
            if not history:
                yield event.plain_result(
                    f"出图超时（{timeout} 秒），请稍后在 ComfyUI 中查看结果。"
                )
                return

            images = comfyui_client.extract_images(history, wf.get("output_node"))
            if not images:
                yield event.plain_result("任务完成，但未找到输出图片节点。")
                return

            for img in images:
                try:
                    data = await client.get_image(
                        img["filename"],
                        img.get("subfolder", ""),
                        img.get("type", ""),
                    )
                except Exception as e:
                    yield event.plain_result(f"下载图片失败：{e}")
                    continue
                suffix = os.path.splitext(img["filename"])[1] or ".png"
                tmp_path = self.temp_dir / f"{uuid.uuid4().hex}{suffix}"
                with open(tmp_path, "wb") as f:
                    f.write(data)
                yield event.chain_result([Image.fromFileSystem(str(tmp_path))])
        finally:
            await client.close()

    # ------------------------------------------------------------------ #
    # 指令：/draw
    # ------------------------------------------------------------------ #
    @filter.command("draw")
    async def cmd_draw(self, event: AstrMessageEvent):
        """通过指令绘图。用法：/draw 提示词 [--wf 工作流名] [--lora 名称[:权重]] [--w 宽] [--h 高]"""
        args = self._strip_command(event.message_str, "draw")
        prompt, lora_map, width, height, wf_name = self._parse_draw_args(args or "")
        if not prompt.strip():
            yield event.plain_result(
                "用法：/draw 一只白色水手服少女 --wf sd --lora catgirl:0.8 --w 768 --h 768"
            )
            return
        async for m in self._do_draw(
            event, wf_name, prompt, "", width, height, lora_map
        ):
            yield m

    def _parse_draw_args(self, text: str):
        """解析绘图指令参数，返回 (prompt, lora_map, width, height, workflow)。"""
        lora_map: dict[str, float | None] = {}
        width = None
        height = None
        wf_name = None

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

        prompt = text.strip()
        return prompt, (lora_map or None), width, height, wf_name

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
            yield event.plain_result(str(e))
            return
        loras = wf.get("loras", []) or []
        if not loras:
            yield event.plain_result(f"工作流「{wf.get('name')}」未配置任何 LoRA。")
            return
        lines = [f"工作流「{wf.get('name')}」的 LoRA 列表："]
        for l in loras:
            state = "启用" if l.get("enabled") else "禁用"
            raw = l.get("keywords") or []
            if isinstance(raw, str):
                kw_str = ", ".join(k.strip() for k in raw.split(",") if k.strip())
            else:
                kw_str = ", ".join(str(k).strip() for k in raw)
            lines.append(
                f"- {l.get('name')}（{state}，权重 {l.get('weight', 1.0)}）"
                + (f"，关键词：{kw_str}" if kw_str else "")
            )
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ #
    # 指令：/loraon /loraoff 持久化启用/禁用某个 LoRA
    # ------------------------------------------------------------------ #
    @filter.command("loraon")
    async def cmd_loraon(self, event: AstrMessageEvent):
        """启用某个 LoRA（持久化）。用法：/loraon 名称 [--wf 工作流名]"""
        args = self._strip_command(event.message_str, "loraon")
        await self._set_lora_enabled(args, True, event)

    @filter.command("loraoff")
    async def cmd_loraoff(self, event: AstrMessageEvent):
        """禁用某个 LoRA（持久化）。用法：/loraoff 名称 [--wf 工作流名]"""
        args = self._strip_command(event.message_str, "loraoff")
        await self._set_lora_enabled(args, False, event)

    async def _set_lora_enabled(self, args: str, enabled: bool, event):
        m = re.search(r"--wf\s+(\S+)", args or "")
        wf_name = m.group(1) if m else None
        name = (args or "").split("--wf")[0].strip()
        if not name:
            yield event.plain_result("请指定 LoRA 名称，例如：/loraon catgirl")
            return
        try:
            wf = self._resolve_workflow(wf_name)
            workflows = self._workflows()
            wf_index = workflows.index(wf)
            loras = wf.get("loras", []) or []
            target = None
            for i, l in enumerate(loras):
                if (l.get("name") or "").strip() == name:
                    target = i
                    break
            if target is None:
                yield event.plain_result(f"工作流「{wf.get('name')}」中找不到 LoRA「{name}」。")
                return
            loras[target]["enabled"] = enabled
            workflows[wf_index]["loras"] = loras
            self.config["workflows"] = workflows
            self.config.save_config()
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        except Exception as e:
            yield event.plain_result(f"操作失败：{e}")
            return
        state = "启用" if enabled else "禁用"
        yield event.plain_result(f"已将 LoRA「{name}」{state}（已保存）。")

    # ------------------------------------------------------------------ #
    # 指令：/queuestatus 查询队列
    # ------------------------------------------------------------------ #
    @filter.command("queuestatus")
    async def cmd_queuestatus(self, event: AstrMessageEvent):
        """查询 ComfyUI 队列状态，以及你最近一次任务前面还有多少位。可用 --wf 指定服务器所在工作流。"""
        args = self._strip_command(event.message_str, "queuestatus")
        m = re.search(r"--wf\s+(\S+)", args or "")
        wf_name = m.group(1) if m else None
        try:
            wf = self._resolve_workflow(wf_name)
            server = self._resolve_server(wf.get("server_name") or None)
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        client = self._build_client(server)
        try:
            running, pending = await client.get_queue_counts()
            lines = [f"ComfyUI「{server.get('name')}」队列：", f"生成中：{running} 个", f"排队中：{pending} 个"]
            pid = self._last_prompt.get(event.session_id or "global")
            if pid:
                pos = await client.get_queue_position(pid)
                if pos is None:
                    lines.append("你的最近一次任务已不在队列中（可能已完成）。")
                elif pos == 0:
                    lines.append("你的最近一次任务正在生成中。")
                else:
                    lines.append(f"你的最近一次任务前面还有 {pos} 位。")
            yield event.plain_result("\n".join(lines))
        finally:
            await client.close()

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
                yield event.plain_result(str(e))
                return
            self.config["default_workflow"] = name
            self.config.save_config()
            yield event.plain_result(f"已将默认工作流设为「{name}」。")
            return
        workflows = self._workflows()
        default = self._cfg("default_workflow", "")
        if not workflows:
            yield event.plain_result("尚未配置任何工作流。")
            return
        lines = ["已配置的工作流："]
        for w in workflows:
            tag = "（默认）" if w.get("name") == default else ""
            anima = " [Anima]" if w.get("is_anima") else ""
            lines.append(f"- {w.get('name')}{anima}{tag}")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ #
    # 指令：/drawhelp 帮助
    # ------------------------------------------------------------------ #
    @filter.command("drawhelp")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示绘图插件帮助。"""
        text = (
            "ComfyUI 绘图插件使用帮助：\n"
            "/draw 提示词 [--wf 工作流] [--lora 名称[:权重]] [--w 宽] [--h 高]  绘图\n"
            "/loralist [--wf 工作流]   列出 LoRA\n"
            "/loraon 名称 [--wf 工作流]  启用 LoRA\n"
            "/loraoff 名称 [--wf 工作流] 禁用 LoRA\n"
            "/queuestatus [--wf 工作流]  查看队列与排队位置\n"
            "/workflows [set 名称]   列出/设置默认工作流\n"
            "也可直接对 AI 说“画一只猫，使用 xxx lora”，由 AI 自动调用绘图工具。"
        )
        yield event.plain_result(text)

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
    ):
        """使用 ComfyUI 生成图片。

        Args:
            prompt(string): 图像的正向提示词描述（中文或英文均可）。
            negative_prompt(string): 负向提示词，可选，不填则留空。
            workflow(string): 要使用的工作流名称，留空使用默认工作流。
            width(number): 图片宽度，0 表示使用工作流默认宽度。
            height(number): 图片高度，0 表示使用工作流默认高度。
            loras(array[string]): 需要启用的 LoRA 名称列表，例如 ["catgirl", "rain"]。留空则使用配置中默认启用的 LoRA。
        """
        lora_map = None
        if loras:
            lora_map = {str(n).strip(): None for n in loras if str(n).strip()}
        async for m in self._do_draw(
            event,
            workflow or None,
            prompt,
            negative_prompt or "",
            width or None,
            height or None,
            lora_map,
        ):
            yield m
