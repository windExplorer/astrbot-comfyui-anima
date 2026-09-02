"""Danbooru 标签语义搜索客户端：把中文画面描述翻译为英文 Danbooru 标签。

重要约定（与 ComfyUI/CLIP 提示词语法相关）：
- 返回的标签串里，**括号 `(` `)` 会被反转义成 `\(` `\)`**。原因：在 ComfyUI/CLIP 的
  提示词里，`( )` 是「注意力权重」符号（如 `(cat:1.2)`），danbooru 角色/作品标签常带括号
  （如 `belle (zenless zone zero)`），不转义会被当成权重调节、把标签拆坏。转义后
  `belle \(zenless zone zero\)` 才会被当作字面标签整体送进模型。
- 角色 / 作品 tag 已包含该角色完整外观设定（发色、瞳色、服装、体型等），调用方拿到后
  **不要**再叠加 `blue_hair` / `long_hair` / `white_dress` / `blue_eyes` 之类的通用外观标签，
  否则会与角色原设定冲突、覆盖角色形象；只有用户明确要改某外观时才追加。
"""

import aiohttp
import re

# 只转义「未转义」的括号，避免重复转义时已存在的 \( \) 被破坏。
_PAREN_RE = re.compile(r"(?<!\\)([()])")


def _escape_parens(tag_str: str) -> str:
    """把标签串里未转义的 `(` `)` 反转义成 `\(` `\)`，适配 ComfyUI/CLIP 提示词语法。"""
    if not tag_str:
        return ""
    return _PAREN_RE.sub(lambda m: "\\" + m.group(1), tag_str)


class DanbooruClient:
    def __init__(
        self,
        base_url: str,
        api_path: str = "/api/search",
        limit: int = 20,
        show_nsfw: bool = False,
        use_segmentation: bool = True,
        popularity: float = 0.15,
        top_k: int = 20,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_path = api_path
        self.limit = limit
        self.show_nsfw = show_nsfw
        self.use_segmentation = use_segmentation
        self.popularity = popularity
        self.top_k = top_k
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def search(self, query: str) -> str:
        """返回逗号分隔的标签串（优先 SFW 集合）。

        返回的标签里括号已反转义（`(` `)` → `\(` `\)`），可直接安全送进 ComfyUI/CLIP
        提示词（不转义会被当成注意力权重符号，拆坏 danbooru 角色/作品标签）。
        失败时返回空串。
        """
        if not query.strip():
            return ""
        payload = {
            "query": query,
            "limit": self.limit,
            "show_nsfw": self.show_nsfw,
            "use_segmentation": self.use_segmentation,
            "popularity": self.popularity,
            "top_k": self.top_k,
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
        return _escape_parens(tags.strip())
