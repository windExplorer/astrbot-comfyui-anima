"""用**生产渲染模块** draw_card 出一组样张（自查版式；不进发布包）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

from draw_card import render  # noqa: E402

LORA = ["薄荷发型 0.8", "婚纱服饰 默认", "柔光氛围 0.6"]
PARAMS = ["尺寸 1664 × 2432", "采样步数 25", "CFG 1.0", "采样器 euler",
          "调度器 normal", "噪点 1.0", "放大 4×", "种子 1234567890"]
PROMPT = "1girl, 夜景, 满月, 月光, 星空, 花海, 户外, 繁花绽放, 酒壶, 影子, 长白发, 猫耳, 暖光"


def info(**kw) -> dict:
    d = dict(kicker="animaPencilXL_v500", workflow="动漫日常 · 文生图",
             right_top="排队 0", device="RTX 4090D 24G", today=128,
             loras=LORA, params=PARAMS, prompt=PROMPT, time_text="2026-09-24 00:12:35")
    d.update(kw)
    return d


shots = [
    ("real_task_teal.png", info(), {"state": "drawing", "theme": "teal"}),
    ("real_task_night.png", info(), {"state": "drawing", "theme": "night"}),
    ("real_done_teal.png",
     info(params=PARAMS + ["大小 2.4 MB", "耗时 8.4 秒"], right_top="耗时 8.4 秒"),
     {"state": "done", "theme": "teal"}),
    ("real_fail_crimson.png",
     info(reason="ComfyUI 连接失败：服务器无响应（已重试 1 次）", right_top="耗时 1.2 秒"),
     {"state": "failed", "theme": "teal"}),   # 配置 teal，但失败强制朱红
    ("real_longprompt_teal.png",
     info(prompt="1girl, 夜景, 满月, 月光, 星空, 花海, 户外, 繁花绽放, 酒壶, 影子, 长白发, 猫耳, 暖光, "
                 "精致的面部, 细腻的皮肤纹理, 晶莹剔透的眼睛, 银白色长发, 随风飘动, 花瓣飞舞, "
                 "电影级光影, 逆光轮廓, master piece, best quality, ultra detailed, 8k, "
                 "wide shot, depth of field, bokeh, cinematic lighting"),
     {"state": "drawing", "theme": "teal"}),
    ("real_platform_night.png",
     info(kicker="nai-diffusion-4-5-full", workflow="NovelAI · 文生图",
          device="云端 NovelAI", loras=[],
          params=["尺寸 832 × 1216", "步数 28", "引导 5.0", "采样器 k_euler_ancestral",
                  "噪声调度 karras", "画师串 已启用", "种子 1234567890", "耗时 6.1 秒"]),
     {"state": "done", "theme": "night"}),
]

for name, inf, kw in shots:
    im = render(inf, **kw)
    assert im is not None, name
    im.save(OUT / name, "PNG")
    print(f"  {name} {im.size}")
print("done ->", OUT)
