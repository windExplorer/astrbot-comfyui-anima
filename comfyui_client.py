"""ComfyUI HTTP 客户端：提交工作流、查询队列与历史、下载图片。"""

import asyncio
import os
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

    async def upload_image(self, path: str, image_type: str = "input") -> dict:
        """上传一张本地图片到 ComfyUI 的 /upload/image，返回接口 JSON 中的图片引用信息。

        返回形如 {"name": "abc.png", "subfolder": "", "type": "input"}，可直接作为
        LoadImage 节点的 image 输入（[name, subfolder, type]），用于图生图（img2img）。
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
