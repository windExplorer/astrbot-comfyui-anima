"""出图卡片渲染（v7.3.0）。

一张 PNG 讲清一次出图：顶部渐变 header（底模 / 状态 / 工作流或平台 / 队列·设备·今日统计）、
中部内容区（失败原因 / LoRA / 参数 / 提示词）、底部 footer（时间 + 品牌）。

设计要点：
- **9 套主题**可配置（默认 teal），失败等特殊情况可指定专属主题（failed → crimson）；
- **状态自适应**：排队中 / 绘制中 / 已完成 / 绘制失败，标题与右上首行随状态变化；
- **平台自适应**：第三方平台（NAI / OpenAI 兼容 / 自定义）出图时，底模位置显示模型名、
  工作流行显示平台名、无 LoRA 区、参数换成平台参数；
- 字体随包（assets/fonts/ResourceHanRoundedCN-Medium.woff2），缺失再回退插件 fonts/ 与系统字体；
- 纯函数实现，不依赖 AstrBot 运行时，便于单测。

本模块只负责「画」，不管发；发送与降级（渲染失败退化成文字）由 main.py 决定。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

logger = logging.getLogger("astrbot")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - 依赖缺失时调用方会拿到 None
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]

# 超采样倍数：先 2 倍画再缩回，字边缘才平滑
S = 2

BRAND = "ComfyUI萌绘"

# --------------------------------------------------------------------------- #
# 主题（结构对齐 user_gateway：top/bottom=header 渐变两端，ink/sub=header 文字，
# accent=强调色，soft=高亮底，footer=脚底色，border=描边）
# --------------------------------------------------------------------------- #
THEMES: dict[str, dict] = {
    "indigo": dict(top=(226, 236, 255), bottom=(147, 178, 248), ink=(23, 45, 96),
                   sub=(56, 88, 152), accent=(37, 99, 235), soft=(235, 243, 255),
                   footer=(242, 247, 255), border=(198, 216, 245)),
    "violet": dict(top=(232, 234, 255), bottom=(160, 170, 248), ink=(42, 38, 94),
                   sub=(78, 74, 138), accent=(79, 70, 229), soft=(238, 240, 255),
                   footer=(243, 244, 255), border=(206, 210, 243)),
    "teal": dict(top=(222, 246, 240), bottom=(142, 214, 199), ink=(18, 70, 64),
                 sub=(46, 110, 102), accent=(13, 128, 118), soft=(233, 247, 244),
                 footer=(238, 250, 247), border=(192, 227, 218)),
    "amber": dict(top=(255, 243, 222), bottom=(246, 205, 137), ink=(92, 58, 12),
                  sub=(140, 96, 30), accent=(186, 96, 12), soft=(253, 243, 229),
                  footer=(254, 247, 237), border=(240, 213, 174)),
    "sunset": dict(top=(255, 236, 214), bottom=(250, 194, 164), ink=(94, 40, 20),
                   sub=(146, 76, 46), accent=(234, 88, 12), soft=(255, 243, 235),
                   footer=(255, 247, 241), border=(248, 214, 190)),
    "crimson": dict(top=(255, 231, 234), bottom=(243, 168, 178), ink=(92, 20, 32),
                    sub=(152, 52, 66), accent=(198, 40, 56), soft=(255, 239, 241),
                    footer=(255, 245, 246), border=(246, 200, 208)),
    "rose": dict(top=(255, 229, 241), bottom=(249, 164, 202), ink=(108, 30, 64),
                 sub=(162, 70, 108), accent=(193, 24, 93), soft=(253, 238, 245),
                 footer=(255, 244, 249), border=(243, 202, 222)),
    "graphite": dict(top=(238, 240, 245), bottom=(203, 208, 222), ink=(38, 42, 54),
                     sub=(88, 94, 110), accent=(71, 85, 105), soft=(241, 243, 247),
                     footer=(245, 246, 250), border=(214, 219, 229)),
    # 深色（本站新增：user_gateway 那 8 套全是浅色，夜间看白卡偏亮）
    "night": dict(top=(21, 64, 58), bottom=(13, 40, 48), ink=(236, 250, 246),
                  sub=(150, 196, 184), accent=(53, 224, 200), soft=(40, 52, 50),
                  footer=(30, 34, 40), border=(56, 66, 72), dark=True),
}

THEME_ORDER = ("teal", "night", "graphite", "indigo", "violet", "amber", "sunset", "rose", "crimson")

THEME_LABELS = {
    "indigo": "靛蓝", "violet": "紫罗兰", "teal": "青碧", "amber": "琥珀橙",
    "sunset": "落日橙", "crimson": "朱红", "rose": "樱粉", "graphite": "石墨灰",
    "night": "夜幕（深色）",
}

DEFAULT_THEME = "teal"

# 状态 → (标题, 强制主题 or None)
STATES: dict[str, tuple[str, str | None]] = {
    "queued": ("排队中", None),
    "drawing": ("绘制中", None),
    "done": ("已完成", None),
    "failed": ("绘制失败", "crimson"),
    "blocked": ("已被拦截", "crimson"),
}

# 画布与版式常量（输出像素；绘制时统一 ×S）
W, PAD, RADIUS = 840, 34, 18
HEAD_H, FOOT_H = 128, 54

# 字体查找顺序（v7.4.12，参考 astrbot_plugin_model_panel 的投放目录策略）：
#   配置指定 → 用户投放目录（data_dir/fonts/ → AstrBot 共享 data/fonts/）→
#   插件自带 fonts/ → 随包 woff2 → 系统字体
BUNDLED_FONT = Path(__file__).resolve().parent / "assets" / "fonts" / "ResourceHanRoundedCN-Medium.woff2"
_SYSTEM_FONTS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)
# Pillow/FreeType 认的字体容器（woff2 需要 brotli 支持，坏文件靠「实际加载验证」跳过）
_FONT_EXTS = (".ttc", ".otf", ".ttf", ".woff2", ".woff")
# 一个目录里多个字体时的挑选提示：圆体/文楷配这套浅色卡片最协调，
# 不认识的名字排最后，不会挡路
_FONT_NAME_HINTS = ("lxgw", "wenkai", "霞鹜", "hanrounded", "rounded", "圆",
                    "noto", "sourcehan", "source-han", "pingfang", "msyh")
# 探测结果缓存：key = (配置的字体名, 数据目录)；None = 探测过且不可用
_FONT_RESOLVED: dict[tuple, "str | None"] = {}


def _mix(c1, c2, t: float):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def norm_theme(name: str, state: str = "") -> str:
    """解析主题名：状态专属主题 > 配置主题 > 默认。"""
    forced = STATES.get(state, (None, None))[1]
    if forced:
        return forced
    n = str(name or "").strip().lower()
    return n if n in THEMES else DEFAULT_THEME


def _font_dirs(data_dir=None) -> list[str]:
    """用户可自己投放字体的目录（优先级从高到低）。

    - ``data_dir/fonts/``：插件数据目录下的 fonts（Docker 里挂载卷持久化，重装插件不丢）；
    - AstrBot 共享 ``data/fonts/``：所有插件共用的投放位（同 model_panel 的做法）；
    - 插件包内 ``fonts/``：老文档位置（霞鹜文楷 ttf，gitignore 不入库）。
    """
    dirs: list[str] = []
    if data_dir:
        dirs.append(str(Path(str(data_dir)) / "fonts"))
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        _root = str(get_astrbot_data_path() or "")
    except Exception:  # 老版本没有该助手或核心结构变化：只少一个候选目录，不影响功能
        _root = ""
    if _root:
        dirs.append(os.path.join(_root, "fonts"))
    dirs.append(str(Path(__file__).resolve().parent / "fonts"))
    return dirs


def _fonts_in(dir_path: str) -> list[str]:
    """列出一个字体目录里的候选文件，按名字提示排序（lxgw/文楷/圆体优先）。

    目录不存在返回空列表。
    """
    try:
        names = [n for n in os.listdir(dir_path) if n.lower().endswith(_FONT_EXTS)]
    except Exception:
        return []

    def _rank(name: str) -> tuple:
        low = name.lower()
        for i, hint in enumerate(_FONT_NAME_HINTS):
            if hint in low:
                return (0, i, low)
        return (1, 0, low)

    return [os.path.join(dir_path, n) for n in sorted(names, key=_rank)]


def find_font(cfg: dict | None = None, data_dir=None, force: bool = False) -> "str | None":
    """探测一个**实际可加载**的中文字体路径，结果按 (配置, 数据目录) 缓存。

    查找顺序：
      ① 配置指定 font_file（绝对路径，或插件目录/数据目录下的相对路径）；
      ② 用户投放目录：data_dir/fonts/ → AstrBot 共享 data/fonts/（Docker 往挂载卷
         丢一个字体文件即可换字体，不用重装插件；目录里多个字体按名字提示排序）；
      ③ 插件自带 fonts/（老文档位置）；
      ④ 随包 assets/fonts/ResourceHanRoundedCN-Medium.woff2（开箱默认）；
      ⑤ 系统字体。
    每个候选都用 PIL 实际加载验证——坏文件 / 缺 brotli 导致读不了的 woff2 直接跳过；
    全部失败返回 None（调用方降级，绝不影响出图）。
    """
    if ImageFont is None:
        return None
    cfg = cfg or {}
    configured = str(cfg.get("font_file") or "").strip()
    key = (configured, str(data_dir or ""))
    if not force and key in _FONT_RESOLVED:
        return _FONT_RESOLVED[key]

    candidates: list[str] = []
    if configured:
        _p = Path(configured)
        if _p.is_absolute():
            candidates.append(configured)
        else:
            # 相对名：依次在插件目录、数据目录、各投放目录里找同名文件
            candidates.append(str(Path(__file__).resolve().parent / configured))
            if data_dir:
                candidates.append(str(Path(str(data_dir)) / configured))
    for _d in _font_dirs(data_dir):
        candidates += _fonts_in(_d)
    try:
        if BUNDLED_FONT.is_file():
            candidates.append(str(BUNDLED_FONT))
    except Exception:
        pass
    candidates += [c for c in _SYSTEM_FONTS]

    picked: "str | None" = None
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            ImageFont.truetype(path, 20)  # 实际加载验证（woff2 缺 brotli / 文件损坏 → 跳过）
        except Exception:
            continue
        picked = path
        break
    _FONT_RESOLVED[key] = picked
    if picked:
        logger.info(f"【出图卡片】 字体: {picked}")
    else:
        logger.warning(
            "【出图卡片】 未找到可用中文字体（候选：配置 font_file、data/fonts/、"
            "插件 fonts/、随包 woff2、系统字体均不可加载）——卡片将渲染失败并降级"
        )
    return picked


def _wrap(text: str, font, draw, max_w: int, max_lines: int = 0) -> list[str]:
    """按像素宽度折行：ASCII 词不拆、CJK 逐字断。

    max_lines <= 0 = **不限行数**（提示词走这条：内容一个字都不能少，卡片高度随内容长）；
    max_lines > 0 才在超出时给末行加省略号（仅用于「失败原因」这类超长兜底）。
    """
    text = re.sub(r"\s+", " ", str(text or "").strip())
    if not text:
        return []
    # 传进来的是**输出像素**宽度，而字体是超采样后的（字号 ×S），textlength 也按超采样算，
    # 所以这里必须先换算，否则实际只用了 max_w/S 的宽度（曾导致提示词只占左边一半）。
    _mw = max(0, int(max_w)) * S
    tokens = re.findall(r"[A-Za-z0-9_\-'’\.]+|\s+|[^\s]", text)
    lines: list[str] = []
    cur = ""
    truncated = False
    for tk in tokens:
        cand = cur + tk
        if draw.textlength(cand.strip(), font=font) <= _mw or not cur.strip():
            cur = cand
        else:
            lines.append(cur.strip())
            cur = "" if tk.isspace() else tk
            if max_lines > 0 and len(lines) >= max_lines:
                truncated = True
                break
    if cur.strip() and not truncated:
        lines.append(cur.strip())
    if truncated and lines:
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > _mw:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _chips(draw, items: list[str], font, max_w: int, h: int = 32, gap: int = 10,
           pad_x: int = 16, line_gap: int = 10) -> tuple[list[tuple[int, int, int, str]], int]:
    """排布胶囊：返回 [(x, y, w, text)] 与占用的总高度（未加外边距）。"""
    out: list[tuple[int, int, int, str]] = []
    x, y, row_h = 0, 0, h
    for text in items:
        tw = int(draw.textlength(text, font=font) / S)
        cw = tw + pad_x * 2
        if cw > max_w:
            cw = max_w
        if x and x + cw > max_w:
            x, y = 0, y + row_h + line_gap
        out.append((x, y, cw, text))
        x += cw + gap
    return out, y + row_h


def render(info: dict, *, state: str = "drawing", theme: str = "", cfg: dict | None = None,
           data_dir=None):
    """渲染卡片，返回 PIL.Image（RGBA）；依赖缺失/失败返回 None。"""
    if Image is None:
        return None
    cfg = cfg or {}
    key = norm_theme(theme or cfg.get("theme") or DEFAULT_THEME, state)
    c = THEMES[key]
    dark = bool(c.get("dark"))
    body_bg = (26, 28, 34, 255) if dark else (255, 255, 255, 255)
    text_main = (238, 240, 248, 255) if dark else (30, 33, 44, 255)
    text_muted = (152, 158, 180, 255) if dark else (126, 133, 152, 255)
    chip_bg = (38, 41, 50, 255) if dark else (255, 255, 255, 255)
    chip_border = c["border"] + (255,) if not dark else (66, 76, 82, 255)

    fpath = find_font(cfg, data_dir=data_dir)
    if not fpath:
        return None
    try:
        f = lambda size: ImageFont.truetype(fpath, int(size * S))  # noqa: E731
    except Exception as e:
        logger.warning(f"【出图卡片】 字体加载失败 {fpath}: {e}")
        return None

    f_kicker, f_title, f_status = f(14), f(30), f(16)
    f_time, f_pill, f_label = f(14), f(15), f(15)
    f_chip, f_body, f_foot, f_brand, f_mark = f(15), f(17), f(14), f(14), f(12)

    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    loras = [str(x) for x in (info.get("loras") or []) if str(x).strip()]
    params = [str(x) for x in (info.get("params") or []) if str(x).strip()]
    prompt = str(info.get("prompt") or "").strip()
    reason = str(info.get("reason") or "").strip()

    head_h = HEAD_H
    y = head_h + 18
    body_top = y
    reason_lines: list[str] = []
    reason_h = 0
    if reason:
        # 失败原因：最多 8 行（够放完整报错，极端超长才省略号）
        reason_lines = _wrap(reason, f_chip, probe, (W - PAD * 2) - 28, max_lines=8)
        reason_h = 14 + 20 * max(1, len(reason_lines))
        y += 26 + reason_h + 18
    chips_max_w = W - PAD * 2
    # 版式（与样张一致）：标签文字画在 label_y+8；胶囊首行顶在 label_y+44，
    # 一块内容占 44 + 胶囊总高，块间再留 24。
    lora_label_y, lora_geo = y, []
    if loras:
        lora_geo, _lh = _chips(probe, loras, f_chip, chips_max_w)
        y += 44 + _lh + 24
    param_label_y, param_geo = y, []
    param_geo, _ph = _chips(probe, params, f_chip, chips_max_w)
    y += 44 + _ph + 24
    prompt_label_y, prompt_lines = y, []
    if prompt:
        # v7.4.2：提示词**不再省略**——按宽度折满所有行，一个字都不少
        prompt_lines = _wrap(prompt, f_body, probe, chips_max_w, max_lines=0)
        y += 30 + 28 * len(prompt_lines) + 14
    H = int(y + 6 + FOOT_H)

    img = Image.new("RGBA", (W * S, H * S), body_bg)

    def _ink(text: str, font):
        """文字墨迹 bbox（超采样坐标）：advance 带字形侧边留白，对齐/居中都得看墨迹。"""
        p = Image.new("L", (900 * S, 90 * S), 0)
        ImageDraw.Draw(p).text((0, 0), text, font=font, fill=255)
        return p.getbbox() or (0, 0, 0, 0)

    # ---- header ----
    gs = 64
    small = Image.new("RGB", (gs, gs))
    px = small.load()
    for yy in range(gs):
        for xx in range(gs):
            t = (xx * 0.30 + yy * 0.70) / (gs - 1)
            px[xx, yy] = _mix(c["top"], c["bottom"], t)
    img.alpha_composite(small.resize((W * S, head_h * S), Image.BICUBIC).convert("RGBA"), (0, 0))
    d = ImageDraw.Draw(img)
    d.text((PAD * S, 24 * S), str(info.get("kicker") or ""), font=f_kicker, fill=c["sub"] + (255,))
    _title = str(info.get("title") or STATES.get(state, ("绘制中", None))[0])
    d.text((PAD * S, 44 * S), _title, font=f_title, fill=c["ink"] + (255,))
    _wf = str(info.get("workflow") or "")
    if _wf:
        _wb = _ink(_wf, f_status)
        _cyw = 90 * S + (_wb[1] + _wb[3]) / 2
        d.ellipse([PAD * S, _cyw - 4.5 * S, (PAD + 9) * S, _cyw + 4.5 * S], fill=c["accent"] + (255,))
        d.text(((PAD + 17) * S, 90 * S), _wf, font=f_status, fill=c["ink"] + (255,))
    # 右上三行：状态胶囊 / 设备 / 今日出图统计
    # v7.4.6：状态行从裸文字升级为胶囊徽章（淡强调底 + 描边），「排队 0」也显示；
    # 胶囊里的数字用强调色并稍大，比一整串同色文字好看。
    _right = str(info.get("right_top") or "")
    if _right:
        _m = re.search(r"\d[\d.]*", _right)
        _f_num = f(18)
        _segs: list[tuple[str, object, tuple]] = []
        if _m:
            _pre, _num, _post = _right[:_m.start()], _m.group(0), _right[_m.end():]
            if _pre:
                _segs.append((_pre, f_pill, c["sub"]))
            _segs.append((_num, _f_num, c["accent"]))
            if _post:
                _segs.append((_post, f_pill, c["sub"]))
        else:
            _segs.append((_right, f_pill, c["accent"]))
        _pw = sum(d.textlength(_t, font=_ft) for _t, _ft, _ in _segs) + 28 * S
        _ph = 32 * S
        _px = (W - PAD) * S - _pw
        _py = 20 * S
        # 半透明底必须走「独立图层 + alpha_composite」：ImageDraw 直接往不透明底图上
        # 写半透明像素是不混合的（v7.2.0 踩过的坑），深色客户端下会变成一坨深色。
        _hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(_hl).rounded_rectangle(
            [_px, _py, _px + _pw, _py + _ph], radius=16 * S,
            fill=c["accent"] + (30,), outline=c["accent"] + (100,), width=S,
        )
        img.alpha_composite(_hl)
        _cx = _px + 14 * S
        for _t, _ft, _col in _segs:
            _b = _ink(_t, _ft)
            d.text((_cx - _b[0], _py + _ph / 2 - (_b[1] + _b[3]) / 2), _t,
                   font=_ft, fill=_col + (255,))
            _cx += d.textlength(_t, font=_ft)
        _ry_dev, _ry_stats = 58, 80
    else:
        _ry_dev, _ry_stats = 26, 50
    _dev = str(info.get("device") or "")
    if _dev:
        _dw = d.textlength(_dev, font=f_time)
        d.text(((W - PAD) * S - _dw, _ry_dev * S), _dev, font=f_time, fill=c["sub"] + (255,))
    _today = info.get("today")
    if _today is not None and cfg.get("today_count", True):
        _s1, _s3, _num = "今日已出图 ", " 张", str(int(_today))
        _tw = (d.textlength(_s1, font=f_time) + d.textlength(_num, font=f_status)
               + d.textlength(_s3, font=f_time))
        _sx, _sy = (W - PAD) * S - _tw, _ry_stats * S
        d.text((_sx, _sy), _s1, font=f_time, fill=c["sub"] + (255,))
        _sx += d.textlength(_s1, font=f_time)
        d.text((_sx, _sy - 2 * S), _num, font=f_status, fill=c["accent"] + (255,))
        _sx += d.textlength(_num, font=f_status)
        d.text((_sx, _sy), _s3, font=f_time, fill=c["sub"] + (255,))

    # ---- body ----
    def _label(text: str, yy: int):
        d.text((PAD * S, (yy + 8) * S), text, font=f_label, fill=text_muted)

    def _draw_chips(geo, base_y: int):
        """胶囊坐标是「相对本块首行」的，这里加上块起始位置（base_y+44）。"""
        for x, yy, cw, text in geo:
            _cy = (base_y + 44 + yy) * S
            d.rounded_rectangle([x * S + PAD * S, _cy,
                                 (x + cw) * S + PAD * S, _cy + 32 * S],
                                radius=16 * S, fill=chip_bg, outline=chip_border, width=S)
            d.text((x * S + PAD * S + 16 * S, _cy + 6 * S), text, font=f_chip, fill=text_main)

    if reason:
        _label("失败原因", body_top)
        _py = (body_top + 26) * S
        # 同上：半透明底走独立图层（浅色/深色客户端观感一致）
        _pl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(_pl).rounded_rectangle(
            [PAD * S, _py, (W - PAD) * S, _py + reason_h * S], radius=10 * S,
            fill=c["accent"] + (26,), outline=c["accent"] + (80,), width=S,
        )
        img.alpha_composite(_pl)
        for _i, _ln in enumerate(reason_lines or [reason[:42]]):
            d.text(((PAD + 14) * S, _py + (7 + _i * 20) * S), _ln,
                   font=f_chip, fill=c["accent"] + (255,))
    if loras:
        _label("LoRA", lora_label_y)
        _draw_chips(lora_geo, lora_label_y)
    if params:
        _label("参 数", param_label_y)
        _draw_chips(param_geo, param_label_y)
    if prompt_lines:
        _label("提示词", prompt_label_y)
        for i, ln in enumerate(prompt_lines):
            d.text((PAD * S, (prompt_label_y + 30 + i * 28) * S), ln, font=f_body, fill=text_main)

    # ---- footer ----
    _fy = (H - FOOT_H) * S
    d.rectangle([0, _fy, W * S, H * S], fill=c["footer"] + (255,))
    d.line([(0, _fy), (W * S, _fy)], fill=(c["border"] if not dark else (56, 66, 72)) + (255,), width=S)
    _cyr = _fy + 27 * S
    _left = f"{cfg.get('foot_left') or '参数以提交时刻为准'} · {info.get('time_text') or time.strftime('%Y-%m-%d %H:%M:%S')}"
    _lb = _ink(_left, f_foot)
    d.ellipse([PAD * S, _cyr - 4.5 * S, (PAD + 9) * S, _cyr + 4.5 * S], fill=c["accent"] + (255,))
    d.text(((PAD + 16) * S - _lb[0], _cyr - (_lb[1] + _lb[3]) / 2), _left, font=f_foot, fill=text_muted)
    _brand = str(info.get("brand") or cfg.get("brand") or BRAND)
    _bb = _ink(_brand, f_brand)
    _box, _gap = 22 * S, 8 * S
    _tx = (W - PAD) * S - _bb[2]
    _x = _tx + _bb[0] - _gap - _box
    d.rounded_rectangle([_x, _cyr - _box / 2, _x + _box, _cyr + _box / 2], radius=6 * S,
                        fill=c["accent"] + (255,))
    _mb = _ink("萌", f_mark)
    d.text((_x + _box / 2 - (_mb[0] + _mb[2]) / 2, _cyr - (_mb[1] + _mb[3]) / 2), "萌",
           font=f_mark, fill=(255, 255, 255, 255))
    d.text((_tx, _cyr - (_bb[1] + _bb[3]) / 2), _brand, font=f_brand, fill=c["ink"] + (255,))

    # ---- 圆角裁切 ----
    mask = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W * S - 1, H * S - 1], radius=RADIUS * S, fill=255)
    out = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out.resize((W, H), Image.LANCZOS)


def render_report(info: dict, *, theme: str = "", cfg: dict | None = None,
                  data_dir=None, foot_left: str = "") -> Image.Image | None:
    """统计 / 状态类报表卡（/绘图统计、/绘图状态 用，v7.5.1）。

    info 字段（全部可缺省，缺啥跳啥）：
      kicker    左上小字（如「ComfyUI萌绘 · 绘图统计」）
      title     大标题（「绘图统计」/「绘图状态」）
      right_top 右上胶囊文字（统计范围，如「今天」）
      tiles     [(label, value, unit)] 大数字块，最多 4 个等宽一排
      sections  [{"label": "热门工作流", "rows": [(左文, 右值, 状态?)]}, ...]
                状态: "ok"=强调色圆点、""=无点、其他=红点（不可达等）
    版式与出图卡同源：同一套主题/字体/footer（左说明+时间、右品牌）。
    """
    if Image is None:
        return None
    cfg = cfg or {}
    c = THEMES[norm_theme(theme or cfg.get("theme") or DEFAULT_THEME, "")]
    dark = bool(c.get("dark"))
    body_bg = (26, 28, 34, 255) if dark else (255, 255, 255, 255)
    text_main = (238, 240, 248, 255) if dark else (30, 33, 44, 255)
    text_muted = (152, 158, 180, 255) if dark else (126, 133, 152, 255)
    tile_bg = (38, 41, 50, 255) if dark else c["soft"] + (255,)
    tile_border = (66, 76, 82, 255) if dark else c["border"] + (255,)
    _bad = (224, 82, 82)

    fpath = find_font(cfg, data_dir=data_dir)
    if not fpath:
        return None
    try:
        f = lambda size: ImageFont.truetype(fpath, int(size * S))  # noqa: E731
    except Exception as e:
        logger.warning(f"【出图卡片】 字体加载失败 {fpath}: {e}")
        return None
    f_kicker, f_title = f(14), f(30)
    f_pill = f(15)
    f_tlabel, f_tval, f_tunit = f(14), f(30), f(13)
    f_row_l, f_row_v = f(16), f(15)
    f_label = f(15)
    f_foot, f_brand, f_mark = f(14), f(14), f(12)

    def _ink(text: str, font):
        p = Image.new("L", (900 * S, 90 * S), 0)
        ImageDraw.Draw(p).text((0, 0), text, font=font, fill=255)
        return p.getbbox() or (0, 0, 0, 0)

    tiles = [t for t in (info.get("tiles") or []) if isinstance(t, (tuple, list)) and len(t) >= 2][:4]
    sections = [s for s in (info.get("sections") or [])
                if isinstance(s, dict) and (s.get("rows") or [])]

    # ---- 版式预算 ----
    HEAD_H, FOOT_H = 108, 54
    y = HEAD_H + 20
    tile_geo = []
    if tiles:
        n = len(tiles)
        _gap = 14
        _tw = (W - PAD * 2 - _gap * (n - 1)) // n
        tile_geo = [(PAD + i * (_tw + _gap), _tw) for i in range(n)]
        y += 96 + 18
    sec_geo = []
    for sec in sections:
        sec_geo.append((y, sec))
        y += 30 + len(sec["rows"]) * 40 + 16
    H = int(y + 4 + FOOT_H)

    img = Image.new("RGBA", (W * S, H * S), body_bg)

    # ---- header 渐变 ----
    gs = 64
    small = Image.new("RGB", (gs, gs))
    px = small.load()
    for yy in range(gs):
        for xx in range(gs):
            t = (xx * 0.30 + yy * 0.70) / (gs - 1)
            px[xx, yy] = _mix(c["top"], c["bottom"], t)
    img.alpha_composite(small.resize((W * S, HEAD_H * S), Image.BICUBIC).convert("RGBA"), (0, 0))
    d = ImageDraw.Draw(img)
    d.text((PAD * S, 24 * S), str(info.get("kicker") or ""), font=f_kicker, fill=c["sub"] + (255,))
    d.text((PAD * S, 44 * S), str(info.get("title") or "统计"), font=f_title, fill=c["ink"] + (255,))

    def _ink(text: str, font):
        p = Image.new("L", (900 * S, 90 * S), 0)
        ImageDraw.Draw(p).text((0, 0), text, font=font, fill=255)
        return p.getbbox() or (0, 0, 0, 0)

    # 右上胶囊（范围）：淡强调底 + 描边，文字居中（与出图卡同语言）
    _rt = str(info.get("right_top") or "")
    if _rt:
        _rb = _ink(_rt, f_pill)
        _pw = (_rb[2] - _rb[0]) + 28 * S
        _ph = 30 * S
        _px = (W - PAD) * S - _pw
        _py = 24 * S
        _hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(_hl).rounded_rectangle(
            [_px, _py, _px + _pw, _py + _ph], radius=15 * S,
            fill=c["accent"] + (30,), outline=c["accent"] + (100,), width=S)
        img.alpha_composite(_hl)
        d.text((_px + _pw / 2 - (_rb[0] + _rb[2]) / 2, _py + _ph / 2 - (_rb[1] + _rb[3]) / 2),
               _rt, font=f_pill, fill=c["accent"] + (255,))

    # ---- tiles ----
    _tiles_y = HEAD_H + 20
    for (tx, tw), (label, value, unit) in zip(tile_geo, tiles):
        _x0, _y0 = tx * S, _tiles_y * S
        _x1, _y1 = (tx + tw) * S, (_tiles_y + 96) * S
        d.rounded_rectangle([_x0, _y0, _x1, _y1], radius=14 * S,
                            fill=tile_bg, outline=tile_border, width=S)
        # label 居中
        _lb = _ink(str(label), f_tlabel)
        d.text(((_x0 + _x1) / 2 - (_lb[0] + _lb[2]) / 2, _y0 + 14 * S), str(label),
               font=f_tlabel, fill=text_muted)
        # value + unit 居中一行
        _val = str(value)
        _un = str(unit or "")
        _vb = _ink(_val, f_tval)
        _uw = d.textlength(_un, font=f_tunit) if _un else 0
        _total_w = (_vb[2] - _vb[0]) + (6 * S + _uw if _un else 0)
        _vx = (_x0 + _x1) / 2 - _total_w / 2
        d.text((_vx - _vb[0], _y0 + 42 * S), _val, font=f_tval, fill=c["accent"] + (255,))
        if _un:
            d.text((_vx + (_vb[2] - _vb[0]) + 6 * S, _y0 + 56 * S), _un,
                   font=f_tunit, fill=text_muted)

    # ---- sections ----
    for _sy, sec in sec_geo:
        d.text((PAD * S, (_sy + 8) * S), str(sec.get("label") or ""), font=f_label, fill=text_muted)
        for _i, row in enumerate(sec["rows"]):
            _left = str(row[0]) if len(row) > 0 else ""
            _val = str(row[1]) if len(row) > 1 else ""
            _state = str(row[2]) if len(row) > 2 else ""
            _ry = (_sy + 30 + _i * 40) * S
            _cy = _ry + 20 * S
            if _state == "ok":
                d.ellipse([PAD * S, _cy - 4.5 * S, (PAD + 9) * S, _cy + 4.5 * S],
                          fill=c["accent"] + (255,))
            elif _state and _state != "":
                d.ellipse([PAD * S, _cy - 4.5 * S, (PAD + 9) * S, _cy + 4.5 * S], fill=_bad + (255,))
            d.text(((PAD + 16) * S, _ry + 4 * S), _left, font=f_row_l, fill=text_main)
            if _val:
                _vb = _ink(_val, f_row_v)
                d.text(((W - PAD) * S - _vb[2], _ry + 7 * S), _val, font=f_row_v,
                       fill=(_bad if (_state and _state != "" and _state != "ok") else text_muted))

    # ---- footer（与出图卡同款：左说明+时间，右品牌） ----
    _fy = (H - FOOT_H) * S
    d.rectangle([0, _fy, W * S, H * S], fill=c["footer"] + (255,))
    d.line([(0, _fy), (W * S, _fy)],
           fill=((56, 66, 72) if dark else c["border"]) + (255,), width=S)
    _cyr = _fy + 27 * S
    _left = f"{foot_left or '实时数据'} · {info.get('time_text') or time.strftime('%Y-%m-%d %H:%M:%S')}"
    _lb = _ink(_left, f_foot)
    d.ellipse([PAD * S, _cyr - 4.5 * S, (PAD + 9) * S, _cyr + 4.5 * S], fill=c["accent"] + (255,))
    d.text(((PAD + 16) * S - _lb[0], _cyr - (_lb[1] + _lb[3]) / 2), _left, font=f_foot, fill=text_muted)
    _brand = str(info.get("brand") or cfg.get("brand") or BRAND)
    _bb = _ink(_brand, f_brand)
    _box, _gap = 22 * S, 8 * S
    _tx = (W - PAD) * S - _bb[2]
    _x = _tx + _bb[0] - _gap - _box
    d.rounded_rectangle([_x, _cyr - _box / 2, _x + _box, _cyr + _box / 2], radius=6 * S,
                        fill=c["accent"] + (255,))
    _mb = _ink("萌", f_mark)
    d.text((_x + _box / 2 - (_mb[0] + _mb[2]) / 2, _cyr - (_mb[1] + _mb[3]) / 2), "萌",
           font=f_mark, fill=(255, 255, 255, 255))
    d.text((_tx, _cyr - (_bb[1] + _bb[3]) / 2), _brand, font=f_brand, fill=c["ink"] + (255,))

    # ---- 圆角裁切 ----
    mask = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W * S - 1, H * S - 1], radius=RADIUS * S, fill=255)
    out = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out.resize((W, H), Image.LANCZOS)


def save_report(info: dict, *, theme: str = "", cfg: dict | None = None,
                data_dir="data", foot_left: str = "") -> str | None:
    """渲染报表卡并落盘，返回 PNG 路径；任何失败都返回 None（调用方降级成文字）。"""
    try:
        im = render_report(info, theme=theme, cfg=cfg, data_dir=data_dir, foot_left=foot_left)
    except Exception as e:
        logger.warning(f"【报表卡片】 渲染失败: {e}")
        return None
    if im is None:
        return None
    try:
        rd = Path(str(data_dir)) / "card_render"
        rd.mkdir(parents=True, exist_ok=True)
        p = rd / f"report_{uuid.uuid4().hex}.png"
        im.save(p, "PNG")
        return str(p)
    except Exception as e:
        logger.warning(f"【报表卡片】 落盘失败: {e}")
        return None


def save(info: dict, *, state: str = "drawing", theme: str = "", cfg: dict | None = None,
         data_dir="data") -> str | None:
    """渲染并落盘，返回 PNG 路径；任何失败都返回 None（调用方据此降级）。"""
    try:
        im = render(info, state=state, theme=theme, cfg=cfg, data_dir=data_dir)
        if im is None:
            return None
        rd = os.path.join(str(data_dir), "card_render")
        os.makedirs(rd, exist_ok=True)
        path = os.path.join(rd, f"card_{state}_{int(time.time() * 1000)}.png")
        im.save(path, "PNG")
        return path
    except Exception as e:
        logger.warning(f"【出图卡片】 渲染失败（不影响出图）: {e}")
        return None


# --------------------------------------------------------------------------- #
# 今日出图统计（自管 JSON，避免依赖图库查询）
# --------------------------------------------------------------------------- #

def _stats_path(data_dir) -> Path:
    return Path(str(data_dir)) / "draw_stats.json"


def _load_stats(data_dir) -> dict:
    today = time.strftime("%Y-%m-%d")
    try:
        p = _stats_path(data_dir)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("date") == today:
                return {"date": today, "ok": int(data.get("ok") or 0),
                        "fail": int(data.get("fail") or 0)}
    except Exception as e:
        logger.debug(f"【出图卡片】 读取统计失败（按 0 计）: {e}")
    return {"date": today, "ok": 0, "fail": 0}


def bump_today(data_dir, ok: bool = True) -> dict:
    """给今日计数 +1，返回最新统计（跨天自动归零）。失败只记日志，不影响出图。"""
    try:
        st = _load_stats(data_dir)
        st["ok" if ok else "fail"] = int(st.get("ok" if ok else "fail") or 0) + 1
        p = _stats_path(data_dir)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return st
    except Exception as e:
        logger.debug(f"【出图卡片】 写入统计失败（忽略）: {e}")
        return _load_stats(data_dir)


def today_count(data_dir) -> int:
    """今日成功出图数（跨天归零）。"""
    return int(_load_stats(data_dir).get("ok") or 0)
