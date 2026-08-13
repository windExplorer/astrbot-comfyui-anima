"""通用 HTTP 翻译接口客户端：把中文动漫描述翻译为英文 Danbooru 风格标签。

与 Danbooru 标签服务器不同，本客户端面向任意支持 HTTP 请求的通用翻译服务
（如 DeepL、百度翻译、自定义中转接口），通过可配置的请求/响应字段映射，
把用户的提示词提交给外部服务并取回英文标签结果。
"""

import aiohttp


class TranslateApiClient:
    def __init__(
        self,
        url: str,
        method: str = "POST",
        headers: dict | None = None,
        timeout: int = 60,
        # 请求体字段映射：把当前提示词填入哪个 key
        text_field: str = "text",
        # 额外固定参数（dict）。POST 时并入 json/表单 body；GET 时并入 query。
        # 值若含 "{text}" 会被替换为本次原文（用于把原文塞进指定字段）。
        extra_params: dict | None = None,
        # 请求体是否用 JSON 编码（否则用表单 x-www-form-urlencoded）
        json_body: bool = True,
        # 响应字段映射：翻译结果在返回 JSON 里的路径（点分隔，如 "data.translated"）
        result_field: str = "translated",
        # 可选：是否把原始中文追加到结果后面
        append_original: bool = False,
    ) -> None:
        self.url = url.rstrip("/")
        self.method = (method or "POST").upper()
        self.headers = headers or {}
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.text_field = text_field
        self.extra_params = dict(extra_params or {})
        self.json_body = json_body
        self.result_field = result_field
        self.append_original = append_original

    @staticmethod
    def _resolve_field(data: dict, path: str) -> str:
        """按点分隔路径从嵌套 dict 取值，如 "data.translated"。"""
        cur = data
        for part in (path or "").split("."):
            if not part:
                return ""
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return ""
        return cur if isinstance(cur, str) else (str(cur) if cur is not None else "")

    @staticmethod
    def _fill(text: str, value) -> str:
        """把值里的 "{text}" 占位符替换为原文，其余转字符串。"""
        return str(value).replace("{text}", text)

    def _build_params(self, text: str) -> dict:
        """构造完整请求参数：text_field 放原文 + 额外固定参数（含 {text} 占位符替换）。"""
        params = {self.text_field: text}
        for k, v in self.extra_params.items():
            params[k] = self._fill(text, v)
        return params

    async def translate(self, text: str) -> str:
        """返回英文标签串（逗号分隔）。失败时抛异常，由调用方决定是否回退。"""
        if not text or not text.strip():
            return ""
        kwargs: dict = {"headers": self.headers}
        params = self._build_params(text)
        if self.method == "GET":
            from urllib.parse import urlencode
            url = f"{self.url}?{urlencode(params)}"
        else:
            url = self.url
            if self.json_body:
                kwargs["json"] = params
            else:
                kwargs["data"] = params
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.request(
                    self.method, url, **kwargs
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as e:
            raise RuntimeError(f"翻译接口请求失败: {e}") from e

        result = self._resolve_field(data, self.result_field) or ""
        result = result.strip()
        if self.append_original and result and text.strip() not in result:
            result = f"{text.strip()}, {result}"
        return result
