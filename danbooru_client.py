"""Danbooru 标签语义搜索客户端：把中文画面描述翻译为英文 Danbooru 标签。"""

import aiohttp


class DanbooruClient:
    def __init__(
        self,
        base_url: str,
        api_path: str = "/api/search",
        limit: int = 20,
        show_nsfw: bool = False,
        use_segmentation: bool = True,
        popularity: float = 0.85,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_path = api_path
        self.limit = limit
        self.show_nsfw = show_nsfw
        self.use_segmentation = use_segmentation
        self.popularity = popularity
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def search(self, query: str) -> str:
        """返回逗号分隔的标签串（优先 SFW 集合）。失败时返回空串。"""
        if not query.strip():
            return ""
        payload = {
            "query": query,
            "limit": self.limit,
            "show_nsfw": self.show_nsfw,
            "use_segmentation": self.use_segmentation,
            "popularity": self.popularity,
        }
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    self.base_url + self.api_path, json=payload
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as e:
            raise RuntimeError(f"Danbooru 标签服务器请求失败: {e}") from e

        tags = data.get("tags_sfw") or data.get("tags_all") or ""
        return tags.strip()
