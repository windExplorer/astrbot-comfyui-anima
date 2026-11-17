# -*- coding: utf-8 -*-
"""_do_draw 等待超时决策抽取的行为等价对拍（v7.8.18）。

v7.8.18 把 `_do_draw` 里的等待超时计算抽成模块级纯函数
`_decide_wait_timeout`（squeeze_fn 传真静态方法 `_wait_timeout_with_budget`）。
本测试用 AST 抠出真实新函数与真 squeeze 方法，与抽取前的逻辑逐字对拍，
800 组固定种子随机参数比对全部返回值。
"""
import ast
import random
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Log:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _num(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_num(node.operand)
    raise AssertionError(f"无法求值: {ast.dump(node)}")


def _load():
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    ns = {"logger": _Log()}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("_WAIT_SEND_RESERVE", "_WAIT_FLOOR"):
                    ns[t.id] = _num(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in ("_WAIT_SEND_RESERVE", "_WAIT_FLOOR") and node.value is not None:
                ns[node.target.id] = _num(node.value)

    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "ComfyUIDrawPlugin")
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_wait_timeout_with_budget":
            seg = ast.get_source_segment(src, node) or ""
            # 去掉 @staticmethod 装饰行，直接 exec def
            body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
            exec(textwrap.dedent(body), ns)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_decide_wait_timeout":
            exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)

    assert "_decide_wait_timeout" in ns and "_wait_timeout_with_budget" in ns
    return ns, src


def legacy_wait(*, base_timeout, queue_extra_timeout, max_draw_timeout, hard_cap,
                ahead, elapsed_pre, poll_interval, squeeze_fn):
    """v7.8.18 抽取前 `_do_draw` 中等待超时计算的逐字返回值逻辑。"""
    per_extra = int(queue_extra_timeout or 0) or base_timeout
    max_timeout = int(max_draw_timeout or 0) or (base_timeout + 30 * base_timeout)
    _calc = min(max_timeout, base_timeout + ahead * per_extra)
    _ = min(_calc, hard_cap) if hard_cap > 0 else _calc  # 原代码此处被下一行覆盖
    timeout, _squeezed = squeeze_fn(_calc, hard_cap, elapsed_pre)
    interval = max(1, int(poll_interval or 2))
    return timeout, per_extra, max_timeout, hard_cap, _squeezed, interval


def test_wait_timeout_equivalence():
    ns, src = _load()
    assert "timeout, per_extra, max_timeout, _hard_cap, _squeezed, interval = (" in src, (
        "抽取后的调用点不存在"
    )
    new_fn = ns["_decide_wait_timeout"]
    squeeze = ns["_wait_timeout_with_budget"]

    rnd = random.Random(20261118)
    bases = [0, 10, 120, 300]
    extras = [0, 10, 120, 300]
    maxes = [0, 60, 600, 1200]
    caps = [0, 30, 100, 300, -1]
    aheads = [0, 1, 5, 20]
    elapseds = [0.0, 10.0, 60.0, 150.0, 300.0]
    polls = [0, 1, 2, 10, None]

    for i in range(800):
        kw = dict(
            base_timeout=rnd.choice(bases), queue_extra_timeout=rnd.choice(extras),
            max_draw_timeout=rnd.choice(maxes), hard_cap=rnd.choice(caps),
            ahead=rnd.choice(aheads), elapsed_pre=rnd.choice(elapseds),
            poll_interval=rnd.choice(polls), squeeze_fn=squeeze,
        )
        got = new_fn(**kw)
        want = legacy_wait(**kw)
        assert got == want, f"#{i} 等待超时决策漂移: {kw}\n got={got}\nwant={want}"
    print("PASS 800 组随机参数：_decide_wait_timeout 与抽取前逐字等价")


if __name__ == "__main__":
    test_wait_timeout_equivalence()
