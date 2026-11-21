# -*- coding: utf-8 -*-
"""底层逻辑守卫：防止几处已修复的 bug 在后续改动中复发。

- H2：_resolve_workflow 抛 ValueError 时 except 里的失败卡引用 wf，
      必须在 try 前初始化 wf = None，否则 UnboundLocalError。
- M1：平台链路 _ok_sent 必须在成功发图后置位，否则结果卡 / 今日计数永不可达。
- M2：平台链路成功出图后必须 _record_draw_used，否则平台图不占配额、限额页失真。
- L1：_decide_wait_timeout 里那段注释自认无用的死计算不得复活。
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "main.py").read_text(encoding="utf-8")
LINES = SRC.splitlines()


def test_resolve_workflow_wf_initialized():
    """wf 必须在 _resolve_workflow 的 try 前初始化，供 except 里的失败卡安全使用。"""
    lines = LINES
    try_at = next(i for i, ln in enumerate(lines) if "wf = self._resolve_workflow(" in ln)
    # 往前找最近的 wf = None 初始化，必须在 try 之前且不早于函数开头太多（紧邻 try 块）
    init_idxs = [i for i in range(max(0, try_at - 8), try_at)
                 if lines[i].strip().startswith("wf = None")]
    assert init_idxs, "wf 未在 _resolve_workflow 的 try 前初始化（失败卡会 UnboundLocalError）"


def test_platform_ok_sent_is_set():
    """平台链路成功发图后必须置 _ok_sent = True，结果卡逻辑才可达。"""
    assigns = [ln.strip() for ln in LINES if re.match(r"^\s*_ok_sent\s*=", ln)]
    assert "_ok_sent = False" in assigns, "缺少 _ok_sent 初始化"
    assert "_ok_sent = True" in assigns, (
        "_ok_sent 从不置 True——平台链路的结果卡 / bump_today 永久不可达"
    )


def test_platform_draw_records_quota():
    """平台链路成功出图后必须记账，且位置在 NSFW 拦截之前。"""
    # _do_draw_nai_style 里无图 return 之后、_ok_sent 初始化之前应有 _record_draw_used。
    no_img = next(i for i, ln in enumerate(LINES) if "平台未返回图片（无图）" in ln)
    ok_sent = next(i for i, ln in enumerate(LINES)
                   if i > no_img and ln.strip() == "_ok_sent = False")
    seg = LINES[no_img:ok_sent]
    assert any("_record_draw_used(event)" in ln for ln in seg), (
        "平台链路成功出图后没调用 _record_draw_used——平台图不占配额、限额页失真"
    )


def test_no_dead_hardcap_computation():
    """_decide_wait_timeout 里 min(_calc, hard_cap) 的死计算不得复活。"""
    assert "_ = min(_calc, hard_cap)" not in SRC, "死计算 _ = min(_calc, hard_cap) 复活了"


if __name__ == "__main__":
    test_resolve_workflow_wf_initialized()
    test_platform_ok_sent_is_set()
    test_platform_draw_records_quota()
    test_no_dead_hardcap_computation()
    print("logic guards OK: wf init / _ok_sent set / platform quota / no dead calc")
