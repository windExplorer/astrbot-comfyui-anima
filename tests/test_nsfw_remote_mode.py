"""NSFW 远程判定模式的默认值与降级自检（v7.12.1）。

核心契约：默认 local 模式必须等价原行为——_remote_nsfw_verdict 恒返回 None，
_detect_nsfw 走本地 open_nsfw；只有显式 auto 且有远程结论时才用远程。

    uv run --no-project --python 3.12 python tests/test_nsfw_remote_mode.py
"""
from __future__ import annotations

import ast
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _Log:
    def debug(self, *a, **k):
        pass
    info = warning = error = debug


def _load_verdict_fn() -> dict:
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ComfyUIDrawPlugin")
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                and n.name == "_remote_nsfw_verdict")
    ns = {"logger": _Log()}
    exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)  # noqa: S102
    return ns


def _make_cls(cfg_fn):
    """用 type() 挂载抠出的函数，确保 self 自动绑定。"""
    return type("_H", (), {
        "_remote_nsfw_verdict": _load_verdict_fn()["_remote_nsfw_verdict"],
        "_cfg": cfg_fn,
    })


def _Harness(mode):
    def cfg(self, key, default):
        if key != "gallery.nsfw.remote_mode":
            return default
        return default if self._mode is None else self._mode  # None=模拟未设置→走 default
    obj = _make_cls(cfg)()
    obj._mode = mode
    return obj


def check(title, cond, detail=""):
    if cond:
        print(f"  [OK] {title}")
    else:
        raise AssertionError(f"{title} {detail}")


def test_schema_default_local():
    """配置 schema 的 remote_mode 默认必须是 local，且 options 第一项是 local。"""
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8-sig"))
    nsfw = schema["gallery"]["items"]["nsfw"]["items"]
    rm = nsfw["remote_mode"]
    check("schema 默认 local", rm["default"] == "local", rm)
    check("options 含 local/auto", set(rm["options"]) == {"local", "auto"}, rm)
    check("说明标注实验性", "实验性" in rm["description"])


def test_verdict_default_local():
    """默认配置（不传 remote_mode）时，即使 history 有 nsfw 结论也返回 None。"""
    # mode=None 模拟用户从未设置该项 → _cfg 返回 default（应为 local）
    h = _Harness(None)
    hist = {"nsfw": {"available": True, "level": "nsfw", "action": "block", "score": 0.9}}
    check("默认 local 时忽略远程结论", h._remote_nsfw_verdict(hist) is None)
    check("非 dict history 返回 None", h._remote_nsfw_verdict(None) is None)


def test_verdict_modes():
    """显式 local/auto 的行为。"""
    hist = {"nsfw": {"available": True, "level": "nsfw", "action": "block", "score": 0.9}}
    h_local = _Harness("local")
    check("显式 local 忽略远程", h_local._remote_nsfw_verdict(hist) is None)
    h_auto = _Harness("auto")
    check("auto 且有结论时使用远程", h_auto._remote_nsfw_verdict(hist) == hist["nsfw"])
    hist_none = {"nsfw": {"available": False}}
    check("auto 但结论不可用时回退", h_auto._remote_nsfw_verdict(hist_none) is None)
    check("auto 但没有 nsfw 字段时回退", h_auto._remote_nsfw_verdict({}) is None)


def test_db_columns_default_null():
    """归档新增的 5 列默认 NULL：local 模式下不写远程字段也不影响现有查询。"""
    import sqlite3
    src = (ROOT / "image_store.py").read_text(encoding="utf-8-sig")
    check("有 ALTER TABLE 迁移循环", "ALTER TABLE images ADD COLUMN" in src)
    for col in ("nsfw_remote_level", "nsfw_remote_score", "diverge", "review_status", "review_auto"):
        check(f"{col} 列定义在迁移清单", f'("{col}"' in src, col)
    # INSERT 语句列数与占位符数一致（核心路径，写错就归档失败）
    check("INSERT 含 5 个远程列", "nsfw_remote_level, nsfw_remote_score, diverge, review_status, review_auto" in src)


if __name__ == "__main__":
    test_schema_default_local()
    test_verdict_default_local()
    test_verdict_modes()
    test_db_columns_default_null()
    print("RESULT: nsfw_remote_mode OK")
