"""多平台生图配置管理（自管 JSON，不走 AstrBot 插件配置）。

方案见 docs/multi-platform-image-plan.md。存储于 data_dir/image_platforms.json：
- active_platform: 当前使用平台（"comfyui" 或 platforms 内某条目的 id）
- platforms: 平台实例列表（type = nai | openai | custom）
- artist_presets / negative_presets: 跨平台共享预设

设计要点：
- 启用规则与 comfyui_servers 一致：同一 type 同一时间只启用一个；
  生图取启用的那个；未启用则用第一个；多个启用按顺序取第一个并告警。
- 本模块只做纯逻辑（读写/校验/模板渲染），不涉及 aiohttp / AstrBot 运行时。
"""

import json
import logging
import time
import uuid
from pathlib import Path

logger = logging.getLogger("astrbot")

# 支持的平台类型
PLATFORM_TYPES = ("nai", "openai", "minimax", "custom")

# NAI 默认请求参数（参考 astrbot_plugin_nai_image）
NAI_SAMPLERS = (
    "k_dpmpp_2m_sde", "k_dpmpp_2m", "k_dpmpp_sde", "k_dpmpp_2s_ancestral",
    "k_euler_ancestral", "k_euler", "ddim",
)
NAI_NOISE_SCHEDULES = ("karras", "native", "exponential", "polyexponential")

# NAI 尺寸档位 → (宽, 高)
NAI_SIZES: dict[str, tuple[int, int]] = {
    "portrait": (832, 1216),
    "landscape": (1216, 832),
    "square": (1024, 1024),
    "2Kportrait": (1536, 2304),
    "2Klandscape": (2304, 1536),
    "2Ksquare": (2048, 2048),
    "4Kportrait": (2048, 3072),
    "4Klandscape": (3072, 2048),
    "4Ksquare": (3072, 3072),
}


def resolve_nai_size(size_key: str) -> tuple[int, int] | None:
    """把尺寸档位（portrait / 2K竖图 / 自由 '宽x高'）解析为 (宽, 高)。"""
    key = (size_key or "").strip()
    if not key:
        return None
    # 中文别名归一化
    aliases = {
        "竖图": "portrait", "横图": "landscape", "方图": "square",
        "2K竖图": "2Kportrait", "2K横图": "2Klandscape", "2K方图": "2Ksquare",
        "4K竖图": "4Kportrait", "4K横图": "4Klandscape", "4K方图": "4Ksquare",
    }
    key = aliases.get(key, key)
    if key in NAI_SIZES:
        return NAI_SIZES[key]
    # 自由格式 "宽x高" / "宽*高"
    for sep in ("x", "X", "*", "×"):
        if sep in key:
            try:
                w, h = key.split(sep, 1)
                w_i, h_i = int(w.strip()), int(h.strip())
                if w_i > 0 and h_i > 0:
                    return (w_i, h_i)
            except (ValueError, TypeError):
                pass
    return None


def render_template(template: str, variables: dict) -> str:
    """渲染 custom 平台的 {{xxx}} 占位符模板（纯文本级）。"""
    out = template or ""
    for key, val in (variables or {}).items():
        out = out.replace("{{" + key + "}}", str(val if val is not None else ""))
    return out


def build_render_variables(*, prompt: str = "", negative: str = "", width: int = 0,
                           height: int = 0, seed=None, model: str = "", api_key: str = "",
                           artist: str = "") -> dict:
    """占位符渲染变量（custom 模板/请求头/额外参数共用）。
    额外提供 prompt_encoded（URL 编码后的提示词，供 GET 直链类平台拼 URL）。"""
    try:
        from urllib.parse import quote as _quote
        prompt_encoded = _quote(str(prompt or ""), safe="")
    except Exception:
        prompt_encoded = str(prompt or "")
    return {
        "prompt": prompt or "",
        "prompt_encoded": prompt_encoded,
        "negative": negative or "",
        "width": width,
        "height": height,
        "seed": seed if seed is not None else -1,
        "model": model or "",
        "api_key": api_key or "",
        "artist": artist or "",
    }


def normalize_headers(p: dict, variables: dict | None = None) -> dict:
    """把平台条目的自定义请求头归一为 dict。

    兼容两种存储：
    - 新格式：列表 [{"key": "X-Foo", "value": "bar"}, ...]（WebUI 条目式编辑）
    - 旧格式：dict {"X-Foo": "bar"}（v5.8.0 custom 条目）
    value 支持 {{api_key}} 等占位符渲染。
    """
    raw = p.get("headers")
    out: dict = {}
    items: list = []
    if isinstance(raw, dict):
        items = [{"key": k, "value": v} for k, v in raw.items()]
    elif isinstance(raw, list):
        items = [x for x in raw if isinstance(x, dict)]
    for it in items:
        k = str(it.get("key") or "").strip()
        if not k:
            continue
        v = str(it.get("value") or "")
        if variables:
            v = render_template(v, variables)
        out[k] = v
    return out


def render_extra_params(items, variables: dict | None = None) -> dict:
    """把「额外参数」条目列表渲染为 dict（并入请求 body）。

    每条：{"key": 参数名, "value": 值, "vtype": text|number|bool|json}
    - text（默认）：原样字符串（可含 {{prompt}} 等占位符）
    - number：转 int/float，失败原样字符串
    - bool：true/false/1/0/yes/no（不区分大小写），其余原样
    - json：值本身是 JSON 字符串，解析后并入（失败原样字符串）
    """
    out: dict = {}
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        k = str(it.get("key") or "").strip()
        if not k:
            continue
        raw = it.get("value")
        vtype = str(it.get("vtype") or "text").strip().lower()
        val = render_template(str(raw if raw is not None else ""), variables) if variables else str(raw if raw is not None else "")
        if vtype == "number":
            try:
                val = int(val)
            except (TypeError, ValueError):
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    pass
        elif vtype == "bool":
            low = str(val).strip().lower()
            if low in ("true", "1", "yes", "on"):
                val = True
            elif low in ("false", "0", "no", "off", ""):
                val = False
        elif vtype == "json":
            try:
                val = json.loads(val)
            except Exception:
                pass
        out[k] = val
    return out


def extract_path(data, path: str):
    """按 'a.b.0.c' 形式的点路径从嵌套结构提取值；失败返回 None。"""
    cur = data
    for part in (path or "").split("."):
        part = part.strip()
        if not part:
            continue
        try:
            if isinstance(cur, list):
                cur = cur[int(part)]
            elif isinstance(cur, dict):
                cur = cur[part]
            else:
                return None
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return cur


class PlatformStore:
    """image_platforms.json 的读写与查询。"""

    def __init__(self, data_dir, cfg_provider=None):
        self.data_dir = Path(data_dir)
        self.json_path = self.data_dir / "image_platforms.json"
        self._cfg_provider = cfg_provider if callable(cfg_provider) else None
        self._cache: dict | None = None
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ---- 配置读写 ----

    def _cfg(self) -> dict:
        """实时配置（cfg_provider 提供热更新；否则用缓存/落盘值）。"""
        if self._cfg_provider is not None:
            try:
                cfg = self._cfg_provider()
                if isinstance(cfg, dict) and cfg:
                    return cfg
            except Exception:
                pass
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    def _load(self) -> dict:
        try:
            if self.json_path.exists():
                data = json.loads(self.json_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning(f"[平台] 读取 image_platforms.json 失败（用默认空配置）: {e}")
        return {"active_platform": "comfyui", "platforms": [], "artist_presets": [], "negative_presets": []}

    def save(self, data: dict) -> None:
        """整包保存（WebUI 整包读写）。非法结构直接拒绝。"""
        if not isinstance(data, dict):
            raise ValueError("平台配置必须是对象")
        platforms = data.get("platforms", [])
        if not isinstance(platforms, list):
            raise ValueError("platforms 必须是列表")
        for p in platforms:
            if not isinstance(p, dict):
                raise ValueError("platforms 条目必须是对象")
            t = (p.get("type") or "").strip()
            if t not in PLATFORM_TYPES:
                raise ValueError(f"不支持的平台类型: {t!r}（允许 {PLATFORM_TYPES}）")
            if not (p.get("id") or "").strip():
                p["id"] = uuid.uuid4().hex
        active = (data.get("active_platform") or "comfyui").strip() or "comfyui"
        if active != "comfyui" and not any(p.get("id") == active for p in platforms):
            raise ValueError(f"当前平台指向不存在的条目: {active!r}")
        data["active_platform"] = active
        data.setdefault("artist_presets", [])
        data.setdefault("negative_presets", [])
        self._cache = data
        tmp = self.json_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.json_path)
        logger.info(f"[平台] 配置已保存（active={active}, platforms={len(platforms)}）")

    # ---- 查询 ----

    def active_platform(self) -> str:
        return (self._cfg().get("active_platform") or "comfyui").strip() or "comfyui"

    def set_active_platform(self, pid: str) -> None:
        cfg = self._cfg()
        pid = (pid or "comfyui").strip() or "comfyui"
        cfg["active_platform"] = pid
        self.save(cfg)

    def all_platforms(self) -> list[dict]:
        return [p for p in (self._cfg().get("platforms") or []) if isinstance(p, dict)]

    def get_platform(self, pid: str) -> dict | None:
        """按 id 或名称查找平台条目。"""
        pid = (pid or "").strip()
        if not pid:
            return None
        for p in self.all_platforms():
            if p.get("id") == pid or (p.get("name") or "").strip() == pid:
                return p
        return None

    def pick_platform(self, pid: str = "", user_id: str = "", is_admin: bool = False) -> dict | None:
        """选出本次生图用的平台：
        1) 显式指定 pid（id/名称）且存在 → 用它；
        2) active_platform 非 comfyui 且存在 → 用它；
        3) 否则 None（走 ComfyUI）。
        权限：第三方平台受 allowed_users 限制——空列表 = 仅管理员；非空 = 管理员 + 名单内
        用户。无权限时静默回退 ComfyUI（普通用户无感）。"""
        cfg = self._cfg()
        target = (pid or "").strip() or self.active_platform()
        if target == "comfyui":
            return None
        p = self.get_platform(target)
        if p is None:
            logger.warning(f"[平台] 指定平台 {target!r} 不存在，回退 ComfyUI")
            return None
        if not p.get("enabled", True):
            logger.warning(f"[平台] 平台 {p.get('name')!r} 已停用，回退 ComfyUI")
            return None
        # 平台使用权限：allowed_users 空 = 仅管理员；非空 = 管理员 + 名单内用户
        if not is_admin:
            allowed = [str(u).strip() for u in (p.get("allowed_users") or []) if str(u).strip()]
            if not user_id or user_id not in allowed:
                logger.info(
                    f"[平台] 用户 {user_id or '(unknown)'} 无权使用平台 {p.get('name')!r}"
                    f"（仅管理员/白名单用户），回退 ComfyUI"
                )
                return None
        return p

    def artist_presets(self, enabled_only: bool = False) -> list[dict]:
        out = self._cfg().get("artist_presets") or []
        if enabled_only:
            out = [p for p in out if p.get("enabled", True)]
        return out

    def negative_presets(self, enabled_only: bool = False) -> list[dict]:
        out = self._cfg().get("negative_presets") or []
        if enabled_only:
            out = [p for p in out if p.get("enabled", True)]
        return out

    def enabled_negative_text(self) -> str:
        """所有启用的负面词模板合并（逗号连接，去重保序）。"""
        seen: list[str] = []
        for p in self.negative_presets(enabled_only=True):
            for seg in (p.get("content") or "").replace("\n", ",").split(","):
                seg = seg.strip()
                if seg and seg not in seen:
                    seen.append(seg)
        return ", ".join(seen)

    # ---- custom 平台模板渲染 ----

    def render_custom_request(self, p: dict, *, prompt: str, negative: str,
                              width: int, height: int, seed, model: str = "") -> tuple[str, str, dict, str]:
        """渲染 custom 平台请求。返回 (method, url, headers, body_json_text)。
        渲染或 JSON 校验失败抛 ValueError。"""
        variables = build_render_variables(
            prompt=prompt, negative=negative, width=width, height=height,
            seed=seed, model=model or (p.get("model") or ""), api_key=p.get("api_key") or "",
        )
        body_text = render_template(p.get("body_template") or "", variables)
        try:
            json.loads(body_text)
        except Exception as e:
            raise ValueError(f"custom 平台请求体模板渲染后不是合法 JSON: {e}")
        headers = {}
        for k, v in (p.get("headers") or {}).items():
            headers[str(k)] = render_template(str(v), variables)
        url = render_template(p.get("url") or "", variables)
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"custom 平台 URL 不合法: {url!r}")
        method = (p.get("method") or "POST").upper()
        return method, url, headers, body_text

    # ---- 摘要（供日志/状态查询） ----

    def summary_full(self) -> dict:
        """全量配置（WebUI 整包读写用）。"""
        return self._cfg()

    def summary(self) -> dict:
        cfg = self._cfg()
        return {
            "active_platform": self.active_platform(),
            "platforms": [
                {"id": p.get("id"), "name": p.get("name"), "type": p.get("type"),
                 "enabled": bool(p.get("enabled", True))}
                for p in self.all_platforms()
            ],
            "artist_presets": len(self.artist_presets()),
            "negative_presets": len(self.negative_presets()),
            "ts": time.time(),
        }
