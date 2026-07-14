"""ComfyUI HTTP 客户端：提交工作流、查询队列与历史、下载图片。"""

import asyncio
import uuid

import aiohttp


class ComfyUIClient:
    def __init__(
        self,
        base_url: str,
        client_id: str | None = None,
        timeout: int = 120,
    ) -> None:
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

    async def queue_prompt(self, prompt: dict, client_id: str | None = None) -> dict:
        """提交工作流，返回 {"prompt_id": "...", ...}。"""
        payload = {"prompt": prompt, "client_id": client_id or self.client_id}
        return await self._post("/prompt", payload)

    async def get_queue(self) -> dict:
        return await self._get("/queue")

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

    async def get_queue_position(self, prompt_id: str) -> int | None:
        """返回指定任务前面还有多少位在排队；正在生成返回 0；找不到返回 None。"""
        try:
            queue = await self.get_queue()
        except Exception:
            return None
        running = queue.get("queue_running", [])
        pending = queue.get("queue_pending", [])
        for item in running:
            if _item_prompt_id(item) == prompt_id:
                return 0
        ahead = len(running)
        for i, item in enumerate(pending):
            if _item_prompt_id(item) == prompt_id:
                return ahead + i
        return None

    async def get_queue_counts(self) -> tuple[int, int]:
        """返回 (正在生成数量, 排队中数量)。"""
        try:
            queue = await self.get_queue()
        except Exception:
            return (0, 0)
        return (len(queue.get("queue_running", [])), len(queue.get("queue_pending", [])))

    async def wait_for_result(
        self, prompt_id: str, timeout: int, interval: int
    ) -> dict | None:
        """轮询历史记录，直到该任务完成或超时。返回该 prompt_id 对应的历史条目。"""
        elapsed = 0
        while elapsed < timeout:
            try:
                history = await self.get_history(prompt_id)
            except Exception:
                history = {}
            if prompt_id in history:
                return history[prompt_id]
            await asyncio.sleep(interval)
            elapsed += interval
        return None


def _item_prompt_id(item) -> str | None:
    """queue 列表项结构为 [序号, {prompt_id:...}, [extra]]。"""
    try:
        return item[1].get("prompt_id")
    except Exception:
        return None


def extract_images(history_entry: dict, output_node: str | None = None) -> list[dict]:
    """从任务历史条目中提取输出图片列表。"""
    if not history_entry:
        return []
    outputs = history_entry.get("outputs", {})
    if output_node and output_node in outputs:
        images = outputs[output_node].get("images", [])
        if images:
            return images
    for _node_id, out in outputs.items():
        if isinstance(out, dict) and out.get("images"):
            return out["images"]
    return []
