"""多平台生图客户端（NAI / OpenAI 兼容 / 自定义 HTTP）。

方案见 docs/multi-platform-image-plan.md。统一入口：
    data = await generate(platform_cfg, prompt=..., negative=..., width=..., height=..., seed=..., count=...)
返回图片字节（单张；count>1 时多次调用由调用方循环或本函数内循环聚合为列表）。

实现要点：
- NAI 官方：POST {base_url}/ai/generate-image（Bearer 持久 token），响应为 zip（内含 png），
  内存解包取第一张图。
- NAI 中转站（via_middle_station=true）：GET {base_url}/generate?tag=...&token=...&size=...&model=...&negative=...&steps=&scale=&cfg=&sampler=&noise_schedule=&nocache=1（nai.sta1n.cn 风格：密钥走 token 查询参数、尺寸用中文键，提示词用 tag=、负向用 negative=），响应为图片二进制；失败返回 SVG 错误页（自动识别并报错）。对齐参考插件 astrbot_plugin_nai_image。
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
import re
import zipfile
from pathlib import Path

import aiohttp

try:
    from yarl import URL as _YarlURL
except ImportError:  # pragma: no cover
    _YarlURL = None

logger = logging.getLogger("astrbot")

_NAI_DEFAULT_BASE = "https://image.novelai.net"

# 瞬时错误重试间隔（秒）
_RETRY_DELAYS = (2, 4, 8)
_RETRY_STATUS = (408, 429, 502, 503, 504)

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=60)


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
    capture: dict | None = None,
    timeout: float | None = None,
) -> list[bytes]:
    """按平台类型分发。返回 bytes 列表（每张一项）。

    capture: 可选 dict，用于回填实际请求/响应调试信息（测试功能「查看详情」）。
    timeout: 可选总超时（秒）；为 None 时用默认 _DEFAULT_TIMEOUT（total=120, connect=60）。"""
    # 构造 ClientTimeout：给定数值时 total=timeout，connect 取 min(60, timeout)（连接不应久等）；
    # 否则沿用默认 _DEFAULT_TIMEOUT（total=120, connect=60）。
    _ct = _DEFAULT_TIMEOUT
    if timeout not in (None, "", 0):
        try:
            _t = float(timeout)
            _ct = aiohttp.ClientTimeout(total=_t, connect=min(60.0, _t))
        except (TypeError, ValueError):
            _ct = _DEFAULT_TIMEOUT
    ptype = (p.get("type") or "").strip().lower()
    if ptype == "nai":
        return await _gen_nai(p, prompt=prompt, negative=negative, width=width,
                              height=height, seed=seed, count=count, artist=artist,
                              capture=capture, timeout=_ct)
    if ptype == "openai":
        return await _gen_openai(p, prompt=prompt, negative=negative, width=width,
                                 height=height, seed=seed, count=count, capture=capture,
                                 timeout=_ct)
    if ptype == "minimax":
        return await _gen_minimax(p, prompt=prompt, negative=negative, width=width,
                                  height=height, seed=seed, count=count, capture=capture,
                                  timeout=_ct)
    if ptype == "custom":
        return await _gen_custom(p, prompt=prompt, negative=negative, width=width,
                                 height=height, seed=seed, count=count, capture=capture,
                                 timeout=_ct)
    raise PlatformError(f"不支持的平台类型: {ptype!r}")


# ---------------------------------------------------------------------- #
# 通用 HTTP 帮助
# ---------------------------------------------------------------------- #

def _mask_headers(headers: dict) -> dict:
    """请求头脱敏（Authorization 等只保留前 12 字符），供调试信息展示。"""
    out = {}
    for k, v in (headers or {}).items():
        sv = str(v)
        if k.lower() in ("authorization", "x-api-key", "api-key") and len(sv) > 12:
            sv = sv[:12] + "..."
        out[k] = sv
    return out


async def _request_with_retry(method: str, url: str, *, headers: dict = None,
                              params: dict = None, json_body: dict = None,
                              data=None, timeout=_DEFAULT_TIMEOUT,
                              capture: dict | None = None) -> tuple[int, dict, bytes]:
    """带瞬时错误退避重试的请求。成功返回 (status, headers, body_bytes)；失败抛 PlatformError。

    ★响应体在会话关闭前已完整读取，调用方直接用返回的 bytes，不要再对响应对象 read()。

    capture: 可选 dict，回填每次尝试的实际请求/响应（headers 脱敏、响应截断 2000 字符）。"""
    last_err = ""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        cap_entry: dict = {
            "attempt": attempt + 1,
            "method": method,
            "url": url,
            "headers": _mask_headers(headers or {}),
        }
        if params:
            cap_entry["query_params"] = params
        if json_body is not None:
            cap_entry["body"] = json_body
        elif data is not None:
            cap_entry["body"] = str(data)[:4000]
        try:
            # ★URL 必须按「已编码」处理：带签名参数（如 X-Amz-Credential 含 %2F）的临时
            # 图片链接若被 aiohttp 二次编码会变成 %252F，导致上游 403 SignatureDoesNotMatch。
            _target = url
            if _YarlURL is not None and not isinstance(_target, _YarlURL):
                try:
                    _target = _YarlURL(str(_target), encoded=True)
                except Exception:
                    _target = url
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method, _target, headers=headers or {}, params=params,
                    json=json_body, data=data,
                ) as resp:
                    # ★必须在会话关闭前把响应体读出来：此前在 async with 内 return resp、
                    # 调用方在会话关闭后才 read()，大响应体（图片）会 "Connection closed"。
                    try:
                        body = await resp.read()
                    except Exception:
                        body = b""
                    status = resp.status
                    hdrs = {str(k): str(v) for k, v in resp.headers.items()}
                    cap_entry["status"] = status
                    cap_entry["response"] = body.decode("utf-8", "ignore")[:2000]
                    if capture is not None:
                        capture.setdefault("attempts", []).append(cap_entry)
                        capture["last"] = cap_entry
                    if status < 400:
                        return status, hdrs, body
                    last_err = f"HTTP {status}: {cap_entry['response'][:500]}"
                    if status not in _RETRY_STATUS:
                        raise PlatformError(f"平台请求失败 {last_err}")
        except PlatformError:
            raise
        except asyncio.TimeoutError:
            last_err = "请求超时"
            cap_entry["error"] = last_err
        except aiohttp.ClientError as e:
            last_err = f"连接错误: {e}"
            cap_entry["error"] = last_err
        if capture is not None:
            capture.setdefault("attempts", []).append(cap_entry)
            capture["last"] = cap_entry
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


def _nai_relay_size(width: int, height: int) -> str:
    """把像素宽高映射为 nai.sta1n.cn 中继站识别的中文尺寸键。

    中继站只认中文尺寸值（竖图/横图/方图/2K竖图/2K横图/2K方图/4K...），不认像素宽高。
    方向由长宽比定，档位由最长边定：<1400=1K，1400~2500=2K，>=2500=4K。"""
    if height > width:
        orient = "竖图"
    elif width > height:
        orient = "横图"
    else:
        orient = "方图"
    mx = max(int(width), int(height))
    tier = ""
    if mx >= 2500:
        tier = "4K"
    elif mx >= 1400:
        tier = "2K"
    return f"{tier}{orient}"


# ---------------------------------------------------------------------- #
# NAI（官方 / 中转）
# ---------------------------------------------------------------------- #

async def _gen_nai(p: dict, *, prompt: str, negative: str, width: int, height: int,
                   seed, count: int, artist: str = "",
                   capture: dict | None = None,
                   timeout: float | None = None) -> list[bytes]:
    base = (p.get("base_url") or _NAI_DEFAULT_BASE).rstrip("/")
    api_key = (p.get("api_key") or "").strip()
    if not api_key:
        raise PlatformError("NAI 平台未配置 Token（api_key）")
    model = (p.get("model") or "nai-diffusion-4-5-full").strip()
    via_middle = bool(p.get("via_middle_station"))

    # 画师串：官方 NAI 拼到 input 前部；中转站走独立 artist 查询参数（见各分支）。
    base_prompt = prompt
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
            # 中转站 GET（nai.sta1n.cn /generate 风格）：鉴权走 token= 查询参数，
            # 尺寸用中文尺寸键（竖图/横图/方图/2K...），提示词用 tag=、负向用 negative=。
            # 与参考插件 astrbot_plugin_nai_image 严格对齐，否则中继站会回「密钥无效或已被禁用」。
            params = {
                "tag": base_prompt,
                "token": api_key,
                "model": model,
                "artist": artist or "",
                "size": _nai_relay_size(width, height),
                "steps": int(p.get("defaults", {}).get("steps", 28)),
                "scale": float(p.get("defaults", {}).get("scale", 6)),
                "cfg": float(p.get("defaults", {}).get("cfg", 7.0)),
                "sampler": (p.get("defaults", {}).get("sampler") or "k_dpmpp_2m_sde"),
                "negative": negative or "",
                "nocache": 1,
                "noise_schedule": (p.get("defaults", {}).get("noise_schedule") or "karras"),
            }
            if _seed >= 0:
                params["seed"] = _seed
            _st, _hd, raw = await _request_with_retry(
                "GET", f"{base}/generate",
                headers={**extra_headers},
                params=params, capture=capture,
                timeout=timeout or _DEFAULT_TIMEOUT,
            )
            _ct = _hd.get("Content-Type", "")
            # 中继站失败会返回 SVG 错误页（如「密钥无效或已被禁用」），不是图片，
            # 必须识别并报错，否则会把 SVG 当成品图发给用户。
            if raw.lstrip().startswith(b"<?xml") or "svg" in _ct:
                _txt = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", "ignore"))
                _txt = re.sub(r"\s+", " ", _txt).strip()
                raise PlatformError(f"NAI 中转站返回错误：{_txt[:200]}")
            img = _resp_to_bytes(raw, _ct)
        else:
            # NAI 官方 /ai/generate-image（zip 响应）
            payload = {
                "input": f"{artist}, {base_prompt}" if artist else base_prompt,
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
            _st, _hd, raw = await _request_with_retry(
                "POST", f"{base}/ai/generate-image",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **extra_headers},
                json_body=payload, capture=capture,
                timeout=timeout or _DEFAULT_TIMEOUT,
            )
            img = _resp_to_bytes(raw, _hd.get("Content-Type", "application/zip"))
        if not img:
            raise PlatformError("NAI 平台返回了空图片")
        results.append(img)
    return results


# ---------------------------------------------------------------------- #
# OpenAI 兼容（/v1/images/generations）
# ---------------------------------------------------------------------- #

async def _gen_openai(p: dict, *, prompt: str, negative: str, width: int, height: int,
                      seed, count: int, capture: dict | None = None,
                      timeout: float | None = None) -> list[bytes]:
    base = (p.get("base_url") or "").strip().rstrip("/")
    # 端点归一：裸域名 / 带 /v1 / 误填完整端点，统一为 <root>/v1/images/generations
    base = re.sub(r"/v1/(?:images/(?:generations|edits))?/?$", "", base, flags=re.I)
    base = re.sub(r"/images/(?:generations|edits)/?$", "", base, flags=re.I)
    if not base.endswith("/v1"):
        base += "/v1"
    api_key = (p.get("api_key") or "").strip()
    if not base:
        raise PlatformError("OpenAI 兼容平台未配置接口地址（base_url）")
    if not api_key:
        raise PlatformError("OpenAI 兼容平台未配置密钥（api_key）")
    model = (p.get("model") or "").strip()
    if not model:
        raise PlatformError("OpenAI 兼容平台未配置模型名（model）")
    # 尺寸原样透传：支持 "1024x1024" 精确值，也支持 "2K" 等档位写法（上游自行标准化）
    size = str(p.get("size") or "").strip() or f"{width}x{height}"

    # 参数风格：use_parameters_wrapper=true（NAI 中转等私有扩展）→ negative/seed/steps 等
    # 打包进 parameters 对象；默认（标准 OpenAI 兼容：官方/Agnes/SenseNova/newapi 等）不发送
    # parameters —— 多数严格端点对未知字段直接 400（如 "parameters is not supported"）。
    use_wrapper = bool(p.get("use_parameters_wrapper"))
    params_obj: dict = {}
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
    if use_wrapper and params_obj:
        body["parameters"] = params_obj
    elif params_obj:
        # 标准风格：负面词放顶层（支持的端点生效，不支持的一般忽略）；seed/steps 等不自动发送
        if negative:
            body["negative_prompt"] = negative
    quality = (p.get("quality") or "").strip()
    if quality:
        body["quality"] = quality

    # 额外参数条目：合并进 body 顶层（wrapper 模式则并入 parameters；
    # 中转站/模型专属字段，如 response_format、ratio、style 等）
    try:
        try:
            from .platform_store import render_extra_params, normalize_headers
        except ImportError:
            from platform_store import render_extra_params, normalize_headers
        _vars = {"prompt": prompt, "negative": negative, "model": model,
                 "width": width, "height": height, "seed": seed, "api_key": api_key}
        extra_kv = render_extra_params(p.get("extra_params"), _vars)
        if use_wrapper:
            params_obj.update(extra_kv)
            if params_obj:
                body["parameters"] = params_obj
        else:
            body.update(extra_kv)
        extra_headers = normalize_headers(p, _vars)
    except Exception as _ep:
        logger.warning(f"[平台] 额外参数/自定义头渲染失败（忽略）: {_ep}")
        extra_headers = {}

    _st, _hd, raw = await _request_with_retry(
        "POST", f"{base}/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **extra_headers},
        json_body=body, capture=capture,
        timeout=timeout or _DEFAULT_TIMEOUT,
    )
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
            _s2, _h2, dlr = await _request_with_retry("GET", url, timeout=timeout or _DEFAULT_TIMEOUT)
            results.append(dlr)
    if not results:
        raise PlatformError("OpenAI 兼容平台响应里没有可用的图片数据")
    return results


# ---------------------------------------------------------------------- #
# MiniMax（海螺 image-01 / image-01-live，同步 JSON）
# ---------------------------------------------------------------------- #

_MINIMAX_ENDPOINT_TAIL_RE = re.compile(
    r"/v1/(?:image_generation|image/generation|images/generations|images/edits)/?$", re.I,
)


def _minimax_endpoint(base_url: str) -> str:
    """MiniMax 端点归一：填根域名 / 带 /v1 / 带端点路径，统一为 <root>/v1/image_generation。"""
    base = (base_url or "").strip().rstrip("/")
    base = _MINIMAX_ENDPOINT_TAIL_RE.sub("", base)
    if not base.startswith(("http://", "https://")):
        raise PlatformError(f"MiniMax 平台地址不合法: {base_url!r}")
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/image_generation"


def _aspect_ratio_of(w: int, h: int) -> str:
    """宽高比映射到 MiniMax 支持的最接近档位（image-01-live 只认比例不认像素）。"""
    ratio = (w / h) if h else 1.0
    candidates = {"21:9": 21 / 9, "16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0, "3:4": 3 / 4, "9:16": 9 / 16}
    return min(candidates.items(), key=lambda kv: abs(kv[1] - ratio))[0]


async def _gen_minimax(p: dict, *, prompt: str, negative: str, width: int, height: int,
                       seed, count: int, capture: dict | None = None,
                       timeout: float | None = None) -> list[bytes]:
    base = (p.get("base_url") or "https://api.minimaxi.com").strip()
    endpoint = _minimax_endpoint(base)
    api_key = (p.get("api_key") or "").strip()
    if not api_key:
        raise PlatformError("MiniMax 平台未配置密钥（api_key）")
    model = (p.get("model") or "image-01").strip()

    try:
        try:
            from .platform_store import render_extra_params, normalize_headers
        except ImportError:
            from platform_store import render_extra_params, normalize_headers
        _vars = {"prompt": prompt, "negative": negative, "model": model,
                 "width": width, "height": height, "seed": seed, "api_key": api_key}
        extra_headers = normalize_headers(p, _vars)
    except Exception:
        extra_headers = {}

    results: list[bytes] = []
    for i in range(max(1, count)):
        body: dict = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "response_format": "base64",
        }
        # image-01-live 只认比例；image-01 接受宽高像素
        if "live" in model.lower():
            body["aspect_ratio"] = _aspect_ratio_of(width, height)
        else:
            body["width"] = int(width)
            body["height"] = int(height)
        if negative:
            body["negative_prompt"] = negative
        try:
            body.update(render_extra_params(p.get("extra_params"), _vars))
        except Exception:
            pass

        _st, _hd, raw = await _request_with_retry(
            "POST", endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **extra_headers},
            json_body=body, capture=capture,
            timeout=timeout or _DEFAULT_TIMEOUT,
        )
        try:
            resp_data = json.loads(raw.decode("utf-8", "ignore"))
        except Exception as e:
            raise PlatformError(f"MiniMax 响应解析失败: {e}")
        base_resp = resp_data.get("base_resp") or {}
        if isinstance(base_resp, dict) and int(base_resp.get("status_code", 0) or 0) != 0:
            raise PlatformError(f"MiniMax 返回错误 {base_resp.get('status_code')}: {base_resp.get('status_msg', '未知')}")
        data = resp_data.get("data") or {}
        b64_list = data.get("image_base64") or []
        if isinstance(b64_list, list) and b64_list:
            try:
                results.append(base64.b64decode(b64_list[0]))
                continue
            except Exception:
                pass
        url_list = data.get("image_urls") or []
        if isinstance(url_list, list) and url_list:
            _s2, _h2, dlr = await _request_with_retry("GET", url_list[0], timeout=timeout or _DEFAULT_TIMEOUT)
            results.append(dlr)
            continue
        raise PlatformError(f"MiniMax 响应里没有图片数据: {str(resp_data)[:300]}")
    return results


# ---------------------------------------------------------------------- #
# 自定义 HTTP（模板渲染 + 响应提取）
# ---------------------------------------------------------------------- #

async def _gen_custom(p: dict, *, prompt: str, negative: str, width: int, height: int,
                      seed, count: int, capture: dict | None = None,
                      timeout: float | None = None) -> list[bytes]:
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
            # 条目式：extra_params 每条 {key, value, vtype}，value 支持 prompt 等占位符
            method = (p.get("method") or "POST").upper()
            url = str(p.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                raise PlatformError(f"custom 平台 URL 不合法: {url!r}")
            try:
                from .platform_store import render_extra_params, build_render_variables
            except ImportError:
                from platform_store import render_extra_params, build_render_variables
            _vars = build_render_variables(
                prompt=prompt, negative=negative, width=width, height=height,
                seed=_seed, model=p.get("model") or "", api_key=p.get("api_key") or "",
            )
            body_obj = render_extra_params(body_params, _vars)
            body_text = json.dumps(body_obj, ensure_ascii=False) if body_obj else ""
            headers = normalize_headers(p, _vars)
        # GET 且无请求体时绝不携带 body（直链类平台，如 Pollinations）
        _data = body_text.encode("utf-8") if (method == "POST" and body_text) else None
        _st, _hd, raw = await _request_with_retry(
            method, url, headers=headers, data=_data, capture=capture,
            timeout=timeout or _DEFAULT_TIMEOUT,
        )
        ct = _hd.get("Content-Type", "")

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
            _s2, _h2, dlr = await _request_with_retry("GET", target, timeout=timeout or _DEFAULT_TIMEOUT)
            results.append(dlr)
        elif isinstance(target, str) and target:
            try:
                results.append(base64.b64decode(target))
            except Exception as e:
                raise PlatformError(f"custom 平台图片 base64 解码失败: {e}")
        else:
            raise PlatformError(f"custom 平台响应里找不到图片数据（resp_path={resp_path!r}）")
    return results
