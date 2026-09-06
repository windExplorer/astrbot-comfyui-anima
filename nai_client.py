"""多平台生图客户端（NAI / OpenAI 兼容 / 自定义 HTTP）。

方案见 docs/multi-platform-image-plan.md。统一入口：
    data = await generate(platform_cfg, prompt=..., negative=..., width=..., height=..., seed=..., count=...)
返回图片字节（单张；count>1 时多次调用由调用方循环或本函数内循环聚合为列表）。

实现要点：
- NAI 官方：POST {base_url}/ai/generate-image（Bearer 持久 token），响应为 zip（内含 png），
  内存解包取第一张图。
- NAI 中转站（via_middle_station=true）：GET {base_url}/generate?prompt=...&model=...（nai.sta1n.cn 风格），
  响应为图片二进制或 JSON（b64/二进制自动识别）。移植自参考插件 astrbot_plugin_nai_image。
- OpenAI 兼容：POST {base_url}/v1/images/generations，支持 b64_json / url 两种响应，
  高级参数放 parameters 对象（对齐 NAI 中转 OpenAI 兼容接口）。
- custom：模板渲染（platform_store.render_custom_request）+ 响应提取。

超时/重试：瞬时错误（408/429/5xx、超时、连接错误）按 2/4/8 秒退避重试，共 3 次重试机会。
"""

import asyncio
import base64
import io
import json
import logging
import zipfile
from pathlib import Path

import aiohttp

logger = logging.getLogger("astrbot")

_NAI_DEFAULT_BASE = "https://image.novelai.net"

# 瞬时错误重试间隔（秒）
_RETRY_DELAYS = (2, 4, 8)
_RETRY_STATUS = (408, 429, 502, 503, 504)

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=15)


class PlatformError(RuntimeError):
    """平台生图失败（文案可直接给用户/LLM）。"""


# ---------------------------------------------------------------------- #
# 统一入口
# ---------------------------------------------------------------------- #

async def generate(
    p: dict,
    *,
    prompt: str,
    negative: str = "",
    width: int = 832,
    height: int = 1216,
    seed=None,
    count: int = 1,
    artist: str = "",
) -> list[bytes]:
    """按平台类型分发。返回 bytes 列表（每张一项）。"""
    ptype = (p.get("type") or "").strip().lower()
    if ptype == "nai":
        return await _gen_nai(p, prompt=prompt, negative=negative, width=width,
                              height=height, seed=seed, count=count, artist=artist)
    if ptype == "openai":
        return await _gen_openai(p, prompt=prompt, negative=negative, width=width,
                                 height=height, seed=seed, count=count)
    if ptype == "custom":
        return await _gen_custom(p, prompt=prompt, negative=negative, width=width,
                                 height=height, seed=seed, count=count)
    raise PlatformError(f"不支持的平台类型: {ptype!r}")


# ---------------------------------------------------------------------- #
# 通用 HTTP 帮助
# ---------------------------------------------------------------------- #

async def _request_with_retry(method: str, url: str, *, headers: dict = None,
                              params: dict = None, json_body: dict = None,
                              data=None, timeout=_DEFAULT_TIMEOUT) -> aiohttp.ClientResponse:
    """带瞬时错误退避重试的请求。失败抛 PlatformError。"""
    last_err = ""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method, url, headers=headers or {}, params=params,
                    json=json_body, data=data,
                ) as resp:
                    if resp.status < 400:
                        return resp
                    body_text = (await resp.text())[:500]
                    last_err = f"HTTP {resp.status}: {body_text}"
                    if resp.status not in _RETRY_STATUS:
                        raise PlatformError(f"平台请求失败 {last_err}")
        except PlatformError:
            raise
        except asyncio.TimeoutError:
            last_err = "请求超时"
        except aiohttp.ClientError as e:
            last_err = f"连接错误: {e}"
        if attempt < len(_RETRY_DELAYS):
            delay = _RETRY_DELAYS[attempt]
            logger.warning(f"[平台] 请求失败（{last_err}），{delay}s 后重试 {attempt + 1}/{len(_RETRY_DELAYS)}")
            await asyncio.sleep(delay)
    raise PlatformError(f"平台请求失败（已重试 {len(_RETRY_DELAYS)} 次）: {last_err}")


def _resp_to_bytes(raw: bytes, content_type: str) -> bytes:
    """响应字节 → 图片字节：JSON 里带 b64 则解；zip 解包；否则原样。"""
    ct = (content_type or "").lower()
    if "json" in ct:
        try:
            data = json.loads(raw.decode("utf-8", "ignore"))
        except Exception:
            return raw
        candidates = data if isinstance(data, list) else _iter_values(data)
        for v in candidates:
            if isinstance(v, str) and len(v) > 256:
                try:
                    return base64.b64decode(v)
                except Exception:
                    continue
        return raw
    if "zip" in ct or raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for name in zf.namelist():
                    if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        return zf.read(name)
        except Exception:
            pass
    return raw


def _iter_values(obj):
    """递归展开 dict/list 的所有叶子值（用于 JSON 里找 b64 字段）。"""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_values(v)
    else:
        yield obj


# ---------------------------------------------------------------------- #
# NAI（官方 / 中转）
# ---------------------------------------------------------------------- #

async def _gen_nai(p: dict, *, prompt: str, negative: str, width: int, height: int,
                   seed, count: int, artist: str = "") -> list[bytes]:
    base = (p.get("base_url") or _NAI_DEFAULT_BASE).rstrip("/")
    api_key = (p.get("api_key") or "").strip()
    if not api_key:
        raise PlatformError("NAI 平台未配置 Token（api_key）")
    model = (p.get("model") or "nai-diffusion-4-5-full").strip()
    via_middle = bool(p.get("via_middle_station"))

    # 画师串追加到正向提示词前部（NAI 惯例：画师串在最前）
    full_prompt = f"{artist}, {prompt}" if artist else prompt
    if not negative:
        negative = "{}, lowres, bad anatomy, bad hands, worst quality, low quality".format(
            "username" if "4-full" in model or "5-full" in model else ""
        ).strip("{} ,")

    results: list[bytes] = []
    # 自定义请求头（条目式）：支持 {{api_key}} 等占位符
    try:
        try:
            from .platform_store import normalize_headers
        except ImportError:
            from platform_store import normalize_headers
        extra_headers = normalize_headers(p, {"api_key": api_key, "model": model})
    except Exception:
        extra_headers = {}
    for i in range(max(1, count)):
        _seed = (int(seed) + i) if seed is not None else -1
        if via_middle:
            # 中转站 GET（nai.sta1n.cn /generate 风格）
            params = {
                "prompt": full_prompt,
                "negative_prompt": negative or "",
                "model": model,
                "width": width,
                "height": height,
                "steps": int(p.get("defaults", {}).get("steps", 28)),
                "scale": float(p.get("defaults", {}).get("scale", 6)),
                "seed": _seed,
            }
            resp = await _request_with_retry(
                "GET", f"{base}/generate",
                headers={"Authorization": f"Bearer {api_key}", **extra_headers},
                params=params,
            )
            raw = await resp.read()
            img = _resp_to_bytes(raw, resp.headers.get("Content-Type", ""))
        else:
            # NAI 官方 /ai/generate-image（zip 响应）
            payload = {
                "input": full_prompt,
                "model": model,
                "action": "generate",
                "parameters": {
                    "width": width,
                    "height": height,
                    "scale": float(p.get("defaults", {}).get("scale", 6)),
                    "sampler": p.get("defaults", {}).get("sampler", "k_dpmpp_2m_sde"),
                    "steps": int(p.get("defaults", {}).get("steps", 28)),
                    "n_samples": 1,
                    "ucPreset": 0,
                    "qualityToggle": True,
                    "sm": False, "sm_dyn": False,
                    "dynamic_thresholding": False,
                    "controlnet_strength": 1,
                    "legacy": False,
                    "add_original_image": True,
                    "cfg_rescale": float(p.get("defaults", {}).get("cfg_rescale", 0.3)),
                    "noise_schedule": p.get("defaults", {}).get("noise_schedule", "karras"),
                    "seed": _seed,
                    "negative_prompt": negative or "",
                },
            }
            resp = await _request_with_retry(
                "POST", f"{base}/ai/generate-image",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **extra_headers},
                json_body=payload,
            )
            raw = await resp.read()
            img = _resp_to_bytes(raw, resp.headers.get("Content-Type", "application/zip"))
        if not img:
            raise PlatformError("NAI 平台返回了空图片")
        results.append(img)
    return results


# ---------------------------------------------------------------------- #
# OpenAI 兼容（/v1/images/generations）
# ---------------------------------------------------------------------- #

async def _gen_openai(p: dict, *, prompt: str, negative: str, width: int, height: int,
                      seed, count: int) -> list[bytes]:
    base = (p.get("base_url") or "").rstrip("/")
    api_key = (p.get("api_key") or "").strip()
    if not base:
        raise PlatformError("OpenAI 兼容平台未配置接口地址（base_url）")
    if not api_key:
        raise PlatformError("OpenAI 兼容平台未配置密钥（api_key）")
    model = (p.get("model") or "").strip()
    if not model:
        raise PlatformError("OpenAI 兼容平台未配置模型名（model）")
    size = f"{width}x{height}"

    # 高级参数进 parameters 对象（对齐 NAI 中转 OpenAI 兼容文档）；
    # 标准 OpenAI 平台会忽略未知字段（gpt-image-1 等不接受 seed/parameters 时由上游裁剪）。
    params_obj = {}
    if negative:
        params_obj["negative_prompt"] = negative
    if seed is not None:
        params_obj["seed"] = int(seed)
    for k in ("steps", "scale", "sampler", "noise_schedule"):
        v = p.get("defaults", {}).get(k)
        if v is not None:
            params_obj[k] = v
    body: dict = {
        "model": model,
        "prompt": prompt,
        "n": max(1, min(4, int(count))),
        "size": size,
    }
    if params_obj:
        body["parameters"] = params_obj
    quality = (p.get("quality") or "").strip()
    if quality:
        body["quality"] = quality

    # 额外参数条目：合并进 body 顶层（中转站/模型专属字段，如 response_format、style 等）
    try:
        try:
            from .platform_store import render_extra_params, normalize_headers
        except ImportError:
            from platform_store import render_extra_params, normalize_headers
        _vars = {"prompt": prompt, "negative": negative, "model": model,
                 "width": width, "height": height, "seed": seed, "api_key": api_key}
        body.update(render_extra_params(p.get("extra_params"), _vars))
        extra_headers = normalize_headers(p, _vars)
    except Exception as _ep:
        logger.warning(f"[平台] 额外参数/自定义头渲染失败（忽略）: {_ep}")
        extra_headers = {}

    resp = await _request_with_retry(
        "POST", f"{base}/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **extra_headers},
        json_body=body,
    )
    raw = await resp.read()
    try:
        data = json.loads(raw.decode("utf-8", "ignore"))
    except Exception as e:
        raise PlatformError(f"OpenAI 兼容平台响应解析失败: {e}")

    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise PlatformError(f"OpenAI 兼容平台未返回图片: {str(data)[:300]}")

    results: list[bytes] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json")
        if b64:
            try:
                results.append(base64.b64decode(b64))
                continue
            except Exception:
                pass
        url = item.get("url")
        if url:
            dl = await _request_with_retry("GET", url)
            results.append(await dl.read())
    if not results:
        raise PlatformError("OpenAI 兼容平台响应里没有可用的图片数据")
    return results


# ---------------------------------------------------------------------- #
# 自定义 HTTP（模板渲染 + 响应提取）
# ---------------------------------------------------------------------- #

async def _gen_custom(p: dict, *, prompt: str, negative: str, width: int, height: int,
                      seed, count: int) -> list[bytes]:
    # 延迟导入避免循环依赖
    try:
        from .platform_store import render_custom_request, extract_path, normalize_headers
    except ImportError:
        from platform_store import render_custom_request, extract_path, normalize_headers

    results: list[bytes] = []
    resp_type = (p.get("resp_type") or "b64_json").strip()
    resp_path = (p.get("resp_path") or "").strip()
    # 请求体来源：extra_params 条目式（推荐，零 JSON）；body_template 为旧版/高级模式兼容
    body_template = str(p.get("body_template") or "").strip()
    body_params = p.get("extra_params") if isinstance(p.get("extra_params"), list) else []

    for i in range(max(1, count)):
        _seed = (int(seed) + i) if seed is not None else -1
        if body_template:
            method, url, headers, body_text = render_custom_request(
                p, prompt=prompt, negative=negative, width=width, height=height,
                seed=_seed,
            )
            headers = {**normalize_headers(p, {"prompt": prompt, "api_key": p.get("api_key") or ""}), **headers}
        else:
            # 条目式：body_params 每条 {key, value, vtype}，value 支持 {{prompt}} 等占位符
            method = (p.get("method") or "POST").upper()
            url = str(p.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                raise PlatformError(f"custom 平台 URL 不合法: {url!r}")
            try:
                from .platform_store import render_extra_params
            except ImportError:
                from platform_store import render_extra_params
            _vars = {"prompt": prompt, "negative": negative, "model": p.get("model") or "",
                     "width": width, "height": height, "seed": _seed,
                     "api_key": p.get("api_key") or "", "artist": ""}
            body_obj = render_extra_params(body_params, _vars)
            body_text = json.dumps(body_obj, ensure_ascii=False)
            headers = normalize_headers(p, _vars)
        resp = await _request_with_retry(
            method, url, headers=headers, data=body_text.encode("utf-8"),
        )
        raw = await resp.read()
        ct = resp.headers.get("Content-Type", "")

        if resp_type == "binary" or "image/" in ct:
            results.append(_resp_to_bytes(raw, ct))
            continue

        try:
            data = json.loads(raw.decode("utf-8", "ignore"))
        except Exception as e:
            raise PlatformError(f"custom 平台响应不是 JSON: {e}")

        target = extract_path(data, resp_path) if resp_path else None
        if target is None:
            # 未配路径则自动在 JSON 里找第一个 b64 大字段
            for v in _iter_values(data):
                if isinstance(v, str) and len(v) > 256:
                    target = v
                    break
        if target is None and resp_type == "url":
            # 自动找第一个 http(s) 图片 URL
            for v in _iter_values(data):
                if isinstance(v, str) and v.startswith(("http://", "https://")):
                    target = v
                    break
        if isinstance(target, str) and target.startswith(("http://", "https://")):
            dl = await _request_with_retry("GET", target)
            results.append(await dl.read())
        elif isinstance(target, str) and target:
            try:
                results.append(base64.b64decode(target))
            except Exception as e:
                raise PlatformError(f"custom 平台图片 base64 解码失败: {e}")
        else:
            raise PlatformError(f"custom 平台响应里找不到图片数据（resp_path={resp_path!r}）")
    return results
