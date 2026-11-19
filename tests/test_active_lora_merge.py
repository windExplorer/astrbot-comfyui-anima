# -*- coding: utf-8 -*-
"""_do_draw 的 LoRA 启用合并抽取的行为等价对拍（v7.8.19）。

v7.8.19 把「工作流默认 → 用户原话命中 → 显式指定覆盖」的合并逻辑抽成
模块级纯函数 `_merge_active_loras`。本测试用 AST 抠出真实新函数，与
抽取前的逻辑逐字对拍；重点覆盖「原话点名同系列时忽略 LLM 指定」规则。
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


def _load():
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "_merge_active_loras")
    ns = {"logger": _Log()}
    exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)
    return ns["_merge_active_loras"], src


def legacy_merge(*, loras_cfg, raw_hits, lora_map):
    """v7.8.19 抽取前的逐字返回值逻辑。"""
    merged: dict = {}
    for lora in loras_cfg:
        nm = (lora.get("name") or "").strip()
        if nm and lora.get("enabled"):
            merged[nm] = None
    merged.update(raw_hits)
    if lora_map is not None:
        for nm, w in lora_map.items():
            nm = (nm or "").strip()
            if not nm:
                continue
            if nm not in raw_hits and any(
                nm != h and (nm.lower() in h.lower() or h.lower() in nm.lower())
                for h in raw_hits
            ):
                continue
            merged[nm] = w
    return merged or None


def test_fixed_scenarios():
    fn, _ = _load()
    # 工作流默认启用 + 原话命中 + 显式覆盖
    got = fn(
        loras_cfg=[{"name": "默认", "enabled": True}, {"name": "关闭", "enabled": False}],
        raw_hits={"原话": 0.8}, lora_map={"默认": 1.2, "显式": 0.5},
    )
    assert got == {"默认": 1.2, "原话": 0.8, "显式": 0.5}, got

    # 同系列冲突：原话点名"娜娜莉" → 忽略 LLM 的"娜娜莉2"
    got = fn(loras_cfg=[], raw_hits={"娜娜莉": 1.0}, lora_map={"娜娜莉2": 0.7})
    assert got == {"娜娜莉": 1.0}, got
    # 反向也成立
    got = fn(loras_cfg=[], raw_hits={"娜娜莉2": 1.0}, lora_map={"娜娜莉": 0.7})
    assert got == {"娜娜莉2": 1.0}, got
    # 原话没点名同系列 → LLM 指定生效
    got = fn(loras_cfg=[], raw_hits={"别的": 1.0}, lora_map={"娜娜莉2": 0.7})
    assert got == {"别的": 1.0, "娜娜莉2": 0.7}, got

    # 空名称过滤 / lora_map=None / 全空
    assert fn(loras_cfg=[{"name": "  ", "enabled": True}], raw_hits={}, lora_map=None) is None
    assert fn(loras_cfg=[], raw_hits={}, lora_map={"  ": 1.0}) is None

    # v7.10.6：工作台取消勾选的自带 LoRA 被真排除（默认启用项也一样）
    got = fn(
        loras_cfg=[{"name": "自带A", "enabled": True}, {"name": "自带B", "enabled": True}],
        raw_hits={}, lora_map=None, disabled_loras=["自带A"],
    )
    assert got == {"自带B": None}, got
    # 显式指定的也会被取消（取消后 lora_map 里不该再有它）
    got = fn(
        loras_cfg=[{"name": "自带A", "enabled": True}],
        raw_hits={}, lora_map={"自带A": 0.6}, disabled_loras=["自带A"],
    )
    assert got is None, got
    # 大小写不敏感；名字不同的不误伤
    got = fn(
        loras_cfg=[{"name": "BoutiqueLora", "enabled": True}, {"name": "Lora乙", "enabled": True}],
        raw_hits={}, lora_map=None, disabled_loras=["boutiquelora"],
    )
    assert got == {"Lora乙": None}, got
    print("PASS 固定场景：默认/原话/显式优先级 + 同系列冲突 + 空值 + disabled 排除")


def test_random_equivalence():
    fn, src = _load()
    assert "active_map = _merge_active_loras(" in src, "抽取后的调用点不存在"
    names = ["猫娘", "猫娘2", "娜娜莉", "娜娜莉2", "a", "A", "  ", ""]
    rnd = random.Random(20261118)
    for i in range(800):
        loras_cfg = [
            {"name": rnd.choice(names), "enabled": rnd.choice([True, False])}
            for _ in range(rnd.randint(0, 4))
        ]
        raw_hits = {
            rnd.choice(names): rnd.choice([None, 0.5, 1.0, 1.2])
            for _ in range(rnd.randint(0, 3))
        }
        lora_map = (
            None if rnd.random() < 0.2 else
            {rnd.choice(names): rnd.choice([None, 0.3, 0.8, 1.5])
             for _ in range(rnd.randint(0, 3))}
        )
        kw = dict(loras_cfg=loras_cfg, raw_hits=raw_hits, lora_map=lora_map)
        got, want = fn(**kw), legacy_merge(**kw)
        assert got == want, f"#{i} LoRA 合并漂移: {kw}\n got={got}\nwant={want}"
    print("PASS 800 组随机参数：_merge_active_loras 与抽取前逐字等价")


if __name__ == "__main__":
    test_fixed_scenarios()
    test_random_equivalence()
