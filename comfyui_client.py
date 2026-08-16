"""ComfyUI HTTP 客户端：提交工作流、查询队列与历史、下载图片。"""

import asyncio
import os
import time
import uuid

import aiohttp


class ComfyUIClient:
    def __init__(
        self,
        base_url: str,
        client_id: str | None = None,
        timeout: int = 120,
        probe_timeout: int = 15,
    ) -> None:
        self.probe_timeout = probe_timeout
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or f"astrbot-comfyui-{uuid.uuid4().hex[:8]}"
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _post(self, path: str, json_data: dict | None = None) -> dict:
        session = await self._session_get()
        async with session.post(self.base_url + path, json=json_data) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _get(self, path: str) -> dict:
        session = await self._session_get()
        async with session.get(self.base_url + path) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def probe(self) -> dict:
        """探测服务器连通性与延迟，返回 {"ok": bool, "elapsed_ms": int, "error": str}。

        访问根路径 /（任何 ComfyUI 均存在），只检查 HTTP 2xx，不解析内容，
        避免依赖 system_stats 等可能缺失/404 的端点。失败时返回 ok=False，
        由调用方展示不可达状态。

        测量口径：先发一次"预热"请求完成建连/DNS/TLS 握手（其耗时不算数），
        再用同一条连接内的第二次请求的耗时作为 HTTP 往返延迟（elapsed_ms），
        避免把握手/建连开销误报成高延迟。整个探测用较短的 probe_timeout，
        不可达时能更快返回，不会干等超时。
        """
        start = time.monotonic()
        try:
            session = await self._session_get()
            # 1) 预热请求：完成建连/DNS/TLS，只验连通，不纳入计时
            async with session.get(self.base_url + "/", timeout=aiohttp.ClientTimeout(total=self.probe_timeout)) as resp:
                resp.raise_for_status()
            # 2) 正式测量：复用同一连接池，得到贴近真实网络的往返耗时
            s2 = time.monotonic()
            async with session.get(self.base_url + "/", timeout=aiohttp.ClientTimeout(total=self.probe_timeout)) as resp2:
                resp2.raise_for_status()
            return {"ok": True, "elapsed_ms": int((time.monotonic() - s2) * 1000), "error": ""}
        except Exception as e:
            return {
                "ok": False,
                "elapsed_ms": int((time.monotonic() - start) * 1000),
                "error": f"{type(e).__name__}: {e}",
            }

    async def get_queue(self) -> dict:
        """查询 ComfyUI /queue 接口，返回 {"queue_running": [...], "queue_pending": [...]}。

        标准 ComfyUI 的 /queue 返回 {queue_running, queue_pending}，各为一个
        任务列表。此方法只做透传，由调用方计算正在执行与排队数量。
        """
        return await self._get("/queue")

    async def queue_prompt(self, prompt: dict, client_id: str | None = None) -> dict:
        """提交工作流，返回 {"prompt_id": "...", ...}。

        中转站（middle station）会在成功响应头里带 `X-Queue-Position`，
        表示「该任务入队那一刻，前方还有几个任务（含正在运行的）」。这里顺带
        解析并塞进返回 dict 的 `_queue_position` 字段（int，未提供时为 None），
        供调用方优先用于排队提示；直连 ComfyUI 时没有该头则为 None，调用方
        会回退到本地队列统计。
        """
        payload = {"prompt": prompt, "client_id": client_id or self.client_id}
        session = await self._session_get()
        async with session.post(self.base_url + "/prompt", json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
            # 兼容 dict 或其它可映射结构
            if isinstance(data, dict):
                pos = resp.headers.get("X-Queue-Position")
                if pos is not None and str(pos).strip() not in ("", "-1"):
                    try:
                        data["_queue_position"] = int(str(pos).strip())
                    except ValueError:
                        data["_queue_position"] = None
                else:
                    data["_queue_position"] = None
            return data

    async def upload_image(self, path: str, image_type: str = "input") -> dict:
        """上传一张本地图片到 ComfyUI 的 /upload/image，返回接口 JSON 中的图片引用信息。

        返回形如 {"name": "abc.png", "subfolder": "", "type": "input"}。
        注意：标准 LoadImage 节点在 /prompt API 下 image 输入应为**字符串文件名**（info["name"]），
        而非 [name, subfolder, type] 三元组（三元组是节点间连线引用格式，当单输入框值会 400）。
        上传已写到 type=input 目录，调用方取 info["name"] 作为 image 输入即可。
        """
        session = await self._session_get()
        try:
            with open(path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("image", f, filename=os.path.basename(path))
                data.add_field("type", image_type)
                async with session.post(
                    self.base_url + "/upload/image", data=data
                ) as resp:
                    resp.raise_for_status()
                    return await resp.json()
        except aiohttp.ClientResponseError as e:
            raise RuntimeError(f"上传图片到 ComfyUI 失败（HTTP {e.status}）") from e

    async def get_history(self, prompt_id: str | None = None) -> dict:
        if prompt_id:
            return await self._get(f"/history/{prompt_id}")
        return await self._get("/history")

    async def get_image(self, filename: str, subfolder: str, img_type: str) -> bytes:
        url = (
            f"{self.base_url}/view"
            f"?filename={filename}&subfolder={subfolder}&type={img_type}"
        )
        session = await self._session_get()
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()

    async def wait_for_result(
        self, prompt_id: str, timeout: int, interval: int
    ) -> dict | None:
        """轮询历史记录，直到该任务完成或超时。返回该 prompt_id 对应的历史条目。

        ComfyUI 的 /history 会持久保留已完成任务，因此即便任务在超时临界点附近
        才写入历史，这里在退出前也会再做最后一次查询，避免“刚好错过”导致收不到图。
        """
        elapsed = 0
        while True:
            try:
                history = await self.get_history(prompt_id)
            except Exception:
                history = {}
            if prompt_id in history:
                return history[prompt_id]
            if elapsed >= timeout:
                # 超时后兜底再查一次：历史已持久化，可能刚刚才写入
                try:
                    final = await self.get_history(prompt_id)
                except Exception:
                    final = {}
                if prompt_id in final:
                    return final[prompt_id]
                return None
            await asyncio.sleep(interval)
            elapsed += interval


def extract_images(history_entry: dict, output_node: str | None = None) -> list[dict]:
    """从任务历史条目中提取输出图片列表。

    兼容 outputs[...].images（SaveImage 等）与 outputs[...].gifs（VideoCombine /
    AnimateDiff 等动图节点），降低「任务完成但未找到输出图片节点」的误报。
    """

    def _imgs(out):
        if not isinstance(out, dict):
            return None
        imgs = out.get("images") or out.get("gifs") or []
        return imgs if imgs else None

    if not history_entry:
        return []
    outputs = history_entry.get("outputs", {})
    if output_node and output_node in outputs:
        imgs = _imgs(outputs[output_node])
        if imgs:
            return imgs
    for _node_id, out in outputs.items():
        imgs = _imgs(out)
        if imgs:
            return imgs
    return []
