# -*- coding: utf-8 -*-
"""外部插件调用（external=True）的图库归属守卫（v7.8.3）。

背景：萌萌今日老婆等伴侣插件调 comfyui_draw / comfyui_img2img 时，传进来的
event 只是"凑巧触发了这条命令的用户消息"。旧实现无论内外一律用
`event.get_sender_id()` 当作者，结果插件批量补的立绘在图库里全挂在那个群友
名下，看不出是哪个插件产出的图。

守卫点：
① `_do_draw` 接受 `external` 参数；
② external 时作者记 `plugin:<source>`（插件名），不记 QQ；
③ 三个工具调用点（llm_draw 主循环 / 续画循环 / llm_img2img）都要把 `_ext`
   传下去，少一处就会漏。
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def test_do_draw_accepts_external():
    assert "external: bool = False,\n    ):" in SRC, "_do_draw 未声明 external 参数"


def test_external_uses_plugin_identity():
    assert "if external:" in SRC, "缺少 external 分支"
    assert 'user_id = f"plugin:' in SRC, "外部调用应记 plugin:<source>"
    # 外部分支里不能再退回去读 QQ 号
    seg = SRC.split("if external:", 1)[1].split("else:", 1)[0]
    assert "get_sender_id" not in seg, "外部分支仍在读发送者 QQ 号"


def test_all_tool_entrypoints_forward_external():
    assert SRC.count("external=_ext") >= 3, (
        "llm_draw 主循环 / 续画循环 / llm_img2img 三处都要传 external=_ext"
    )
