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

# 进程级共享会话：复用连接池（TCP/TLS keep-alive），避免每次请求都重新握手。
# 此前每个请求（含每次重试）都新建 ClientSession，无连接复用——高延迟网络下
# 每张图要多花 1~3 秒在 TCP+TLS 握手上。trust_env=True 使 HTTP(S)_PROXY/
# NO_PROXY 等系统代理环境变量生效（aiohttp 默认忽略，走代理环境的部署会直连
# 超时→退避重试，表现为「莫名慢」）。
_SHARED_SESSION: "aiohttp.ClientSession | None" = None


def _shared_session() -> aiohttp.ClientSession:
    global _SHARED_SESSION
    s = _SHARED_SESSION
    if s is not None and not s.closed:
        return s
    _SHARED_SESSION = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=8, ttl_dns_cache=300, enable_cleanup_closed=True),
        trust_env=True,
    )
    return _SHARED_SESSION


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
    # 调用方覆盖：LLM 工具（comfyui_draw）可临时指定 NAI/OpenAI 类平台的生图参数，
    # 传 None 时回落平台配置 defaults。cfg 在 NAI 里即引导系数（官方叫 scale）。
    cfg: float | None = None,
    steps: int | None = None,
    sampler: str | None = None,
    noise_schedule: str | None = None,
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
                              cfg=cfg, steps=steps, sampler=sampler,
                              noise_schedule=noise_schedule,
                              capture=capture, timeout=_ct)
    if ptype == "openai":
        return await _gen_openai(p, prompt=prompt, negative=negative, width=width,
                                 height=height, seed=seed, count=count,
                                 cfg=cfg, steps=steps, sampler=sampler,
                                 noise_schedule=noise_schedule,
                                 capture=capture, timeout=_ct)
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
            # 共享会话（连接复用）；超时按次请求传入，互不影响
            session = _shared_session()
            async with session.request(
                method, _target, headers=headers or {}, params=params,
                json=json_body, data=data, timeout=timeout,
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
    """把像素宽高映射为 NAI 中转站（Nai2API / nai.sta1n.cn 系）识别的尺寸键。

    ★中转站只认【中文】尺寸键：竖图/横图/方图/2K竖图/2K横图/2K方图/4K竖图/
    4K横图/4K方图（Nai2API 服务端 sizeMap 键即中文；astrbot_plugin_nai_image
    v2.1.x 修复记录同证：传英文值会导致尺寸被误解、回落站内默认尺寸）。
    不认像素宽高，实际像素由中转站按键映射（2K=1088x1600/1600x1088/1344x1344，
    4K=1344x1984/1984x1344/1728x1728，与官方档位一致）。

    方向由长宽比定；档位按最长边分：1K 最大 1216，2K 为 1344/1600，
    4K 为 1728/1984 → 阈值 1300 分 1K/2K，1650 分 2K/4K
    （1344x1344 的 2K 方图按旧 1400 阈值会误判成 1K，已修）。"""
    if height > width:
        orient = "竖图"
    elif width > height:
        orient = "横图"
    else:
        orient = "方图"
    mx = max(int(width), int(height))
    tier = ""
    if mx >= 1650:
        tier = "4K"
    elif mx >= 1300:
        tier = "2K"
    return f"{tier}{orient}"


# ---------------------------------------------------------------------- #
# NAI（官方 / 中转）
# ---------------------------------------------------------------------- #

async def _gen_nai(p: dict, *, prompt: str, negative: str, width: int, height: int,
                   seed, count: int, artist: str = "",
                   cfg: float | None = None, steps: int | None = None,
                   sampler: str | None = None, noise_schedule: str | None = None,
                   capture: dict | None = None,
                   timeout: float | None = None) -> list[bytes]:
    base = (p.get("base_url") or _NAI_DEFAULT_BASE).rstrip("/")
    api_key = (p.get("api_key") or "").strip()
    if not api_key:
        raise PlatformError("NAI 平台未配置 Token（api_key）")
    model = (p.get("model") or "nai-diffusion-4-5-full").strip()
    via_middle = bool(p.get("via_middle_station"))
    # v4/v5 模型必须用 V4 参数结构（v4_prompt/v4_negative_prompt 等），
    # 否则负面词不生效、部分参数被忽略（对齐 NAI 官方网页端，参考 Nai2API 实测实现）
    _is_v45 = bool(re.match(r"nai-diffusion-[45]", model))
    # 官方 API 尺寸/步数上限：宽高 128~2048，steps 1~50
    width = max(128, min(2048, int(width or 832)))
    height = max(128, min(2048, int(height or 1216)))

    # 画师串：官方 NAI 拼到 input 前部（官方网页端用换行分隔 artist 与 tag）；
    # 中转站走独立 artist 查询参数（见各分支）。
    base_prompt = prompt
    official_prompt = f"{artist}\n{base_prompt}" if artist else base_prompt
    if not negative:
        negative = "{}, lowres, bad anatomy, bad hands, worst quality, low quality".format(
            "username" if "4-full" in model or "5-full" in model else ""
        ).strip("{} ,")

    # 实际生效的生图参数：调用方覆盖（LLM 传入）优先，否则回落平台 defaults。
    # cfg 即 NAI 引导系数（官方 API 字段名 scale；中转站同时认 cfg/scale）。
    _d = p.get("defaults", {}) or {}
    try:
        _steps = int(steps) if steps is not None else int(_d.get("steps", 28))
    except (TypeError, ValueError):
        _steps = 28
    _steps = max(1, min(50, _steps))  # 官方 steps 上限 50（对齐 Nai2API MAX_STEPS）
    try:
        _scale = float(cfg) if cfg is not None else float(_d.get("scale", 6))
    except (TypeError, ValueError):
        _scale = 6.0
    _scale = max(1.0, min(20.0, _scale))
    try:
        _cfg = float(cfg) if cfg is not None else float(_d.get("cfg", 7.0))
    except (TypeError, ValueError):
        _cfg = 7.0
    _cfg = max(0.0, min(1.0, _cfg))  # cfg_rescale 官方范围 0~1
    _sampler = (sampler or _d.get("sampler") or "k_dpmpp_2m_sde")
    # v4/v5 官方支持的采样器白名单（不在列表的值上游会 400，回落官方网页端默认）
    if _is_v45:
        _v4_samplers = (
            "k_euler", "k_euler_ancestral", "k_dpm_2", "k_dpm_fast",
            "k_dpmpp_2m", "k_dpmpp_2m_sde", "k_dpmpp_3m_sde",
            "k_dpmpp_sde", "k_dpmpp_2s_ancestral",
        )
        if _sampler not in _v4_samplers:
            _sampler = "k_euler_ancestral"
    _noise = (noise_schedule or _d.get("noise_schedule") or "karras")

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
                "steps": _steps,
                "scale": _scale,
                "cfg": _cfg,
                "sampler": _sampler,
                "negative": negative or "",
                "nocache": 1,
                "noise_schedule": _noise,
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
            # v4/v5 模型必须用 V4 结构化参数（v4_prompt/v4_negative_prompt 的 caption
            # 结构），负面词才真正生效；v3 及以下继续用扁平参数。
            # 字段清单对齐 NAI 官方网页端（参考 Nai2API 实测实现）。
            _neg = negative or ""
            if _is_v45:
                parameters = {
                    "params_version": 3,
                    "width": width,
                    "height": height,
                    "scale": _scale,
                    "steps": _steps,
                    "uncond_scale": 0.00001,
                    "cfg_rescale": float(_d.get("cfg_rescale", 0.0)),
                    "seed": _seed,
                    "n_samples": 1,
                    "noise_schedule": _noise,
                    "legacy_v3_extend": False,
                    "reference_image_multiple": [],
                    "reference_information_extracted_multiple": [],
                    "reference_strength_multiple": [],
                    "v4_prompt": {
                        "caption": {"base_caption": official_prompt, "char_captions": []},
                        "use_coords": False,
                        "use_order": True,
                        "legacy_uc": False,
                    },
                    "v4_negative_prompt": {
                        "caption": {"base_caption": _neg, "char_captions": []},
                        "use_coords": False,
                        "use_order": False,
                        "legacy_uc": False,
                    },
                    "negative_prompt": _neg,
                    "uc": _neg,
                    "sampler": _sampler,
                    "controlnet_strength": 1,
                    "controlnet_model": None,
                    "dynamic_thresholding": False,
                    "dynamic_thresholding_percentile": 0.999,
                    "dynamic_thresholding_mimic_scale": 10,
                    "sm": False,
                    "sm_dyn": False,
                    "skip_cfg_above_sigma": None,
                    "skip_cfg_below_sigma": 0,
                    "lora_unet_weights": None,
                    "lora_clip_weights": None,
                    "deliberate_euler_ancestral_bug": False,
                    "prefer_brownian": True,
                    "cfg_sched_eligibility": "enable_for_post_summer_samplers",
                    "explike_fine_detail": False,
                    "minimize_sigma_inf": False,
                    "uncond_per_vibe": True,
                    "wonky_vibe_correlation": True,
                    "image_format": "png",
                    "version": 1,
                }
            else:
                parameters = {
                    "width": width,
                    "height": height,
                    "scale": _scale,
                    "sampler": _sampler,
                    "steps": _steps,
                    "n_samples": 1,
                    "ucPreset": 0,
                    "qualityToggle": True,
                    "sm": False, "sm_dyn": False,
                    "dynamic_thresholding": False,
                    "controlnet_strength": 1,
                    "legacy": False,
                    "add_original_image": True,
                    "cfg_rescale": float(_d.get("cfg_rescale", 0.3)),
                    "noise_schedule": _noise,
                    "seed": _seed,
                    "negative_prompt": _neg,
                }
            payload = {
                "input": official_prompt,
                "model": model,
                "action": "generate",
                "parameters": parameters,
            }
            _st, _hd, raw = await _request_with_retry(
                "POST", f"{base}/ai/generate-image",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "accept": "application/x-zip-compressed,image/png,application/json",
                    "origin": "https://novelai.net",
                    "referer": "https://novelai.net/",
                    **extra_headers,
                },
                json_body=payload, capture=capture,
                timeout=timeout or _DEFAULT_TIMEOUT,
            )
            img = _resp_to_bytes(raw, _hd.get("Content-Type", "application/zip"))
        if not img:
            raise PlatformError("NAI 平台返回了空图片")
        results.append(img)
    return results


async def fetch_quota(p: dict, *, timeout=_DEFAULT_TIMEOUT) -> dict:
    """查询 NAI 剩余点数。

    入参 p 为平台配置 dict（取 base_url / api_key）。
    - NAI 中转站（via_middle_station）：GET {base}/api/me?token={api_key}
    - NAI 官方：GET {base}/user/data（Bearer token），解析订阅固定/购买 steps 之和
      （对齐 Nai2API fetchNovelAiAccountQuota）。
    返回 {"ok": True, "balance": int, "enabled": bool} 或 {"ok": False, "message": str}。"""
    if not isinstance(p, dict):
        return {"ok": False, "message": "平台配置无效"}
    if not bool(p.get("via_middle_station")):
        return await _fetch_quota_official(p, timeout=timeout)
    base = (p.get("base_url") or "").strip().rstrip("/")
    key = (p.get("api_key") or "").strip()
    if not base or not key:
        return {"ok": False, "message": "平台未配置 base_url 或 api_key"}
    url = f"{base}/api/me"
    try:
        _st, _hd, raw = await _request_with_retry(
            "GET", url,
            params={"token": key},
            timeout=timeout,
        )
    except PlatformError as e:
        return {"ok": False, "message": str(e)}
    try:
        data = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return {"ok": False, "message": "上游响应解析失败"}
    if not isinstance(data, dict):
        return {"ok": False, "message": "上游响应格式异常"}
    return {
        "ok": True,
        "balance": int(data.get("balance", 0) or 0),
        "enabled": bool(data.get("enabled", True)),
    }


async def _fetch_quota_official(p: dict, *, timeout=_DEFAULT_TIMEOUT) -> dict:
    """NAI 官方订阅剩余点数：GET {base}/user/data（Bearer 持久 token）。

    点数 = subscription.trainingStepsLeft.fixedTrainingStepsLeft + purchasedTrainingSteps
    （字段兼容 snake_case）；查不到点数字段时返回失败，不猜 0。"""
    key = (p.get("api_key") or "").strip()
    if not key:
        return {"ok": False, "message": "NAI 平台未配置 Token（api_key）"}
    base = ((p.get("base_url") or "").strip() or _NAI_DEFAULT_BASE).rstrip("/")
    try:
        _st, _hd, raw = await _request_with_retry(
            "GET", f"{base}/user/data",
            headers={
                "Authorization": f"Bearer {key}",
                "accept": "application/json",
                "origin": "https://novelai.net",
                "referer": "https://novelai.net/",
            },
            timeout=timeout,
        )
    except PlatformError as e:
        return {"ok": False, "message": str(e)}
    try:
        data = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return {"ok": False, "message": "上游响应解析失败"}
    if not isinstance(data, dict):
        return {"ok": False, "message": "上游响应格式异常"}
    sub = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
    steps_left = sub.get("trainingStepsLeft") if isinstance(sub.get("trainingStepsLeft"), dict) else {}

    def _num(*vals):
        for v in vals:
            try:
                if v is not None:
                    return int(v)
            except (TypeError, ValueError):
                continue
        return None

    fixed = _num(steps_left.get("fixedTrainingStepsLeft"), steps_left.get("fixed_training_steps_left"),
                 sub.get("fixedTrainingStepsLeft"), sub.get("fixed_training_steps_left"))
    purchased = _num(steps_left.get("purchasedTrainingSteps"), steps_left.get("purchased_training_steps"),
                     sub.get("purchasedTrainingSteps"), sub.get("purchased_training_steps"))
    if fixed is None and purchased is None:
        return {"ok": False, "message": "响应里没有订阅点数字段"}
    return {
        "ok": True,
        "balance": (fixed or 0) + (purchased or 0),
        "enabled": True,
        "tier": sub.get("tier"),
    }


# ---------------------------------------------------------------------- #
# OpenAI 兼容（/v1/images/generations）
# ---------------------------------------------------------------------- #

async def _gen_openai(p: dict, *, prompt: str, negative: str, width: int, height: int,
                      seed, count: int,
                      cfg: float | None = None, steps: int | None = None,
                      sampler: str | None = None, noise_schedule: str | None = None,
                      capture: dict | None = None,
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
    for k, _ov in (("steps", steps), ("scale", cfg), ("sampler", sampler), ("noise_schedule", noise_schedule)):
        v = _ov if _ov is not None else p.get("defaults", {}).get(k)
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
