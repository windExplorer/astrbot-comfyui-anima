"""出图卡片渲染测试（v7.3.0）。

覆盖：9 套主题可用、四状态渲染、状态专属主题（失败→朱红）、
平台卡（无 LoRA 更矮）、渲染落盘、今日统计跨天归零。

跑法：python tests/test_draw_card.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from draw_card import (  # noqa: E402
    DEFAULT_THEME, THEMES, THEME_ORDER, bump_today, norm_theme, render, save,
    today_count,
)

LORA = ["薄荷发型 0.8", "婚纱服饰 默认", "柔光氛围 0.6"]
PARAMS = ["尺寸 1664 × 2432", "采样步数 25", "CFG 1.0", "采样器 euler",
          "调度器 normal", "噪点 1.0", "放大 4×", "种子 1234567890"]
PROMPT = "1girl, 夜景, 满月, 月光, 星空, 花海, 户外, 繁花绽放, 酒壶, 影子, 长白发, 猫耳, 暖光"


def _draw_probe():
    from PIL import Image as _I, ImageDraw as _D

    return _D.Draw(_I.new("RGBA", (8, 8)))


def _info(**kw) -> dict:
    base = dict(kicker="底模 animaPencilXL_v500", workflow="动漫日常 · 文生图",
                right_top="排队 0", device="RTX 4090D 24G", today=128,
                loras=LORA, params=PARAMS, prompt=PROMPT,
                time_text="2026-09-24 00:12:35")
    base.update(kw)
    return base


def test_theme_table():
    assert DEFAULT_THEME in THEMES
    assert len(THEMES) == 9 and set(THEME_ORDER) == set(THEMES)
    for k, t in THEMES.items():
        for field in ("top", "bottom", "ink", "sub", "accent", "footer", "border"):
            v = t.get(field)
            assert isinstance(v, tuple) and len(v) == 3, (k, field)
    print("== 1. 主题表（9 套，字段完整） OK")


def test_norm_theme():
    assert norm_theme("", "drawing") == DEFAULT_THEME
    assert norm_theme("night", "drawing") == "night"
    assert norm_theme("不存在的主题", "done") == DEFAULT_THEME
    # 状态专属主题：失败/拦截 → 朱红（忽略配置主题）
    assert norm_theme("night", "failed") == "crimson"
    assert norm_theme("teal", "blocked") == "crimson"
    print("== 2. 主题解析（默认/指定/未知回退/失败强制朱红） OK")


def test_render_all_themes_and_states():
    heights = {}
    for k in THEME_ORDER:
        im = render(_info(), state="drawing", theme=k)
        assert im is not None, k
        assert im.size[0] == 840, im.size
        heights[k] = im.size[1]
    # 同一内容不同主题：高度应完全一致（主题只换配色）
    assert len(set(heights.values())) == 1, heights
    im_q = render(_info(right_top="排队 3"), state="queued")
    im_d = render(_info(params=PARAMS + ["大小 2.4 MB", "耗时 8.4 秒"]), state="done")
    assert im_q is not None and im_d is not None
    im_f = render(_info(reason="ComfyUI 连接失败：服务器无响应（已重试 1 次）"), state="failed")
    assert im_f is not None and im_f.size[1] > im_q.size[1], (im_f.size, im_q.size)
    # 平台卡：无 LoRA 区 + 无提示词 → 明显更矮
    p = render(_info(kicker="模型 nai-diffusion-4-5-full", workflow="NovelAI · 文生图",
                     device="云端 NovelAI", loras=[], params=PARAMS), state="done")
    assert p is not None and p.size[1] < im_d.size[1], (p.size, im_d.size)
    print("== 3. 渲染（9 主题等高 / 四状态 / 失败更高 / 平台更矮） OK")


def test_save_and_stats():
    with tempfile.TemporaryDirectory() as td:
        path = save(_info(), state="done", data_dir=td)
        assert path and Path(path).is_file(), path
        assert Path(path).suffix == ".png"
        assert today_count(td) == 0
        st = bump_today(td, ok=True)
        assert st["ok"] == 1 and today_count(td) == 1
        bump_today(td, ok=True)
        st = bump_today(td, ok=False)
        assert st["ok"] == 2 and st["fail"] == 1
        # 伪造「昨天」的记录 → 读取时归零
        import json
        (Path(td) / "draw_stats.json").write_text(
            json.dumps({"date": "2000-01-01", "ok": 999, "fail": 9}), encoding="utf-8")
        assert today_count(td) == 0
        # 渲染失败也不能抛（字体路径不存在时回退随包/系统字体或返回 None）
        save(_info(), state="drawing", cfg={"font_file": "不存在.ttf"}, data_dir=td)
    print("== 4. 落盘 / 今日统计（自增、失败计数、跨天归零） OK")


def test_prompt_not_truncated():
    """提示词完整显示，不省略（v7.4.2）；失败原因板随行数变高。"""
    short = render(_info(prompt="1girl"), state="drawing")
    long_prompt = ("1girl, extremely detailed face, " * 12).strip()
    long_im = render(_info(prompt=long_prompt), state="drawing")
    assert short is not None and long_im is not None
    assert long_im.size[1] > short.size[1], (long_im.size, short.size)

    # 折行函数：max_lines=0 = 不限行、且不出现省略号，内容一字不少
    from PIL import ImageFont as _F
    from draw_card import _wrap, find_font

    _fp = find_font()
    assert _fp, "找不到可用字体"
    _f = _F.truetype(_fp, 34)
    _probe = _draw_probe()
    lines = _wrap(long_prompt, _f, _probe, 700, max_lines=0)
    assert len(lines) >= 3, lines
    assert not any(ln.endswith("…") for ln in lines), lines
    assert "".join(lines).replace(" ", "") == long_prompt.replace(" ", ""), lines
    # 折行必须**用满宽度**：最长一行要接近 max_w（曾因单位没换算只用了半宽）
    _widest = max(_probe.textlength(ln, font=_f) for ln in lines) / 2  # 字体是 2x 超采样
    assert _widest <= 700, _widest
    assert _widest > 700 * 0.6, f"折行只用了 {_widest:.0f}/700 宽度（应是接近满宽）"
    # 指定行数时才会省略（仅失败原因这种兜底用）
    capped = _wrap(long_prompt, _f, _draw_probe(), 700, max_lines=2)
    assert len(capped) == 2 and capped[-1].endswith("…"), capped

    # 失败原因：多行 → 卡片更高；单行短原因仍是单行板
    r1 = render(_info(reason="连接超时"), state="failed")
    r2 = render(_info(reason="ComfyUI 连接失败：" + "服务器无响应，" * 10), state="failed")
    assert r1 is not None and r2 is not None
    assert r2.size[1] > r1.size[1], (r2.size, r1.size)
    print("== 5. 提示词完整显示（不省略）+ 失败原因多行 OK")


if __name__ == "__main__":
    test_theme_table()
    test_norm_theme()
    test_render_all_themes_and_states()
    test_save_and_stats()
    test_prompt_not_truncated()
    print("draw_card 全部通过")
