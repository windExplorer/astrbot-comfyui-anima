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


def test_external_call_without_event_is_allowed():
    """外部插件后台调用没有会话事件时，不许直接回「未能获取到会话事件」。"""
    assert "class _HeadlessEvent" in SRC, "缺少无头事件桩"
    assert SRC.count("event = _HeadlessEvent()") >= 2, (
        "comfyui_draw / comfyui_img2img 两个入口都要兜底"
    )
    # 兜底必须只在外部调用时启用：原生链路仍要求真实事件。
    # 以两条报错文案为锚点，往前看 400 字符内有没有 `if _ext:` 分支。
    for tail in ("直接使用 /draw 指令绘图", "直接使用 /img2img 指令"):
        assert tail in SRC, tail
        seg = SRC.split(tail, 1)[0][-400:]
        assert "if _ext:" in seg, f"event 为空时应先判断外部调用：{tail}"


def test_headless_event_not_remembered():
    assert 'getattr(event, "_is_headless", False)' in SRC, (
        "无头桩不能写进 _last_event，否则会污染原生调用的兜底"
    )


def test_headless_event_has_chain_api():
    """桩必须能应答「构造回复链」的接口。

    v7.8.5 实测：缺 chain_result 会直接 AttributeError，整张图打挂
    （`'_HeadlessEvent' object has no attribute 'chain_result'`）。
    """
    assert "def chain_result(" in SRC
    assert "def set_result(" in SRC
    assert "def plain_result(" in SRC


def test_external_skips_send_chain():
    """外部调用不该走到发送链：ComfyUI 与第三方平台两条链路都要旁路。"""
    assert SRC.count("if external:") >= 2, "两条出图链路都要有 external 分支"
    assert SRC.count("yield None, _send_img_path") >= 2, (
        "旁路分支要把路径 yield 出去（Node 传 None，外层只取路径不发图）"
    )
