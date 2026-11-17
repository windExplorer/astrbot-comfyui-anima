# -*- coding: utf-8 -*-
"""_do_draw 尺寸决策抽取的行为等价对拍（v7.8.16）。

v7.8.16 把 `_do_draw` 里 79 行的四层尺寸决策抽成模块级纯函数
`_decide_draw_size`（无 self / 无 await / 无 IO）。本测试：

1. 用 AST 从 main.py 抠出**真实的新函数**及其依赖的常量/工具函数执行
   （不导入 main，避免 astrbot 依赖）；
2. 把抽取前的逻辑逐字保留为 `legacy_decide()`；
3. 用固定种子的随机组合对拍 `(w, h, _wh_src)`，确保抽取过程零语义漂移。
"""
import ast
import random
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import workflow_builder  # noqa: E402

FUNCS = {
    "_align16", "_tier_key_norm", "_tier_factor", "_tier_label",
    "_scale_size_to_tier", "_cap_size_to_tier", "_budget_ratio",
    "_decide_draw_size",
}
CONSTS = {"_SIZE_ALIGN", "_SIZE_TIERS", "_SIZE_TIER_DEFAULT", "_SIZE_BASE_PX"}


class _Log:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _num(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _num(node.left) * _num(node.right)
    if isinstance(node, (ast.Tuple, ast.List)):
        seq = [_num(x) for x in node.elts]
        return tuple(seq) if isinstance(node, ast.Tuple) else seq
    if isinstance(node, ast.Dict):
        return {_num(k): _num(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_num(node.operand)
    raise AssertionError(f"无法求值: {ast.dump(node)}")


def _load_new():
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    ns = {"workflow_builder": workflow_builder, "logger": _Log()}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in CONSTS:
                    ns[t.id] = _num(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in CONSTS and node.value is not None:
                ns[node.target.id] = _num(node.value)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCS:
            exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)
    assert FUNCS <= set(ns), f"没摘到函数: {FUNCS - set(ns)}"
    assert CONSTS <= set(ns), f"没摘到常量: {CONSTS - set(ns)}"
    return ns["_decide_draw_size"], src


def legacy_decide(
    ns, *, ratio_wh, tier_req, width, height, default_w, default_h,
    lock_size, init_images, custom_size, max_size_tier, ratio_src, positive,
    max_width, max_height, max_side, max_mp,
):
    """v7.8.16 抽取前 `_do_draw` 中 8707-8785 的返回值逻辑（日志省略，不影响返回值）。"""
    _tier_key_norm = ns["_tier_key_norm"]
    _SIZE_TIER_DEFAULT = ns["_SIZE_TIER_DEFAULT"]
    _tier_label = ns["_tier_label"]
    _scale_size_to_tier = ns["_scale_size_to_tier"]
    _cap_size_to_tier = ns["_cap_size_to_tier"]
    _dw, _dh = default_w, default_h
    _max_tier = _tier_key_norm(max_size_tier) or _SIZE_TIER_DEFAULT
    if ratio_wh is not None:
        w, h = ratio_wh
        _wh_src = "比例预设（用户原话命中，覆盖工作流默认）"
    elif width or height:
        w = width or _dw
        h = height or _dh
        _wh_src = "用户显式传参"
    else:
        w, h = _dw, _dh
        _wh_src = "工作流默认（default_width/height，未配置则兜底 512）"
    if tier_req:
        _w0, _h0 = w, h
        w, h = _scale_size_to_tier(w, h, tier_req)
        _wh_src += f" + 档位 {_tier_label(tier_req)}（{_w0}x{_h0}→{w}x{h}）"
    if lock_size and not init_images:
        w, h = _dw, _dh
        _wh_src = "锁定工作流默认尺寸（lock_size，忽略用户/比例/档位）"
    _cw, _ch, _capped = _cap_size_to_tier(w, h, _max_tier)
    if _capped:
        w, h = _cw, _ch
        _wh_src += f" + 档位上限降级({_tier_label(_max_tier)})"
    if (not init_images or custom_size) and (w and h):
        def _wh_limit(v):
            s = str(v or "").strip()
            return int(s) if s.isdigit() and int(s) > 0 else None
        _maxw, _maxh = _wh_limit(max_width), _wh_limit(max_height)
        if _maxw or _maxh:
            _factor = min(
                (_maxw / w) if _maxw else 1.0,
                (_maxh / h) if _maxh else 1.0,
                1.0,
            )
            if _factor < 1.0:
                w = max(64, int(round(w * _factor / 8)) * 8)
                h = max(64, int(round(h * _factor / 8)) * 8)
    if w and h:
        _gw, _gh, _gwhy = workflow_builder.clamp_size(
            w, h, max_side, max_mp, ns["_SIZE_ALIGN"]
        )
        if _gwhy:
            w, h = _gw, _gh
    return w, h, _wh_src


def test_size_decision_equivalence():
    new_fn, src = _load_new()
    assert "w, h, _wh_src = _decide_draw_size(" in src, "抽取后的调用点不存在"
    # 直接复用抠出来的命名空间（含依赖函数与常量）
    ns = new_fn.__globals__

    rnd = random.Random(20261117)
    ratios = [None, (832, 1216), (1216, 832), (1024, 1024)]
    tiers = ["", "1k", "2k", "4k", "乱写"]
    params = [(0, 0), (768, 768), (0, 1024), (1920, 1080)]
    defaults = [(512, 512), (1024, 1024)]
    max_tiers = ["", "1k", "2k", "4k"]
    limits = ["", "768", "1024", "2048"]
    maxes = [(0, 0.0), (1024, 0.0), (0, 2.0), (2048, 4.0)]

    for i in range(800):
        kw = dict(
            ratio_wh=rnd.choice(ratios), tier_req=rnd.choice(tiers),
            width=rnd.choice(params)[0], height=rnd.choice(params)[1],
            default_w=rnd.choice(defaults)[0], default_h=rnd.choice(defaults)[1],
            lock_size=rnd.choice([False, True]), init_images=rnd.choice([False, True]),
            custom_size=rnd.choice([False, True]), max_size_tier=rnd.choice(max_tiers),
            ratio_src="用户原话 竖版", positive="画面描述",
            max_width=rnd.choice(limits), max_height=rnd.choice(limits),
            max_side=rnd.choice(maxes)[0], max_mp=rnd.choice(maxes)[1],
        )
        got = new_fn(**kw)
        want = legacy_decide(ns, **kw)
        assert got == want, f"#{i} 尺寸决策漂移: {kw}\n got={got}\nwant={want}"
    print("PASS 800 组随机参数：_decide_draw_size 与抽取前逐字等价")


if __name__ == "__main__":
    test_size_decision_equivalence()
