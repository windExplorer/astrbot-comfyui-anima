# -*- coding: utf-8 -*-
"""守卫：main.py 里 self.xxx(...) 调用的插件方法必须都有定义。

历史事故：v8.0.0 删自建角色卡时误删了 _looks_moss_layout / _rescale_moss_layout，
但 _do_draw 里 8 处仍在调用 → 运行时 AttributeError，pyflakes 查不到实例属性。
本测试用 AST 对比「self.方法( 调用」与「类里 def 方法」，跑一遍即发现悬空调用。

跑法：uv run --no-project --python 3.12 python tests/test_self_methods_defined.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
tree = ast.parse(src)

cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "ComfyUIDrawPlugin")
defined = {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

called = set()
for node in ast.walk(cls):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        v = node.func.value
        if isinstance(v, ast.Name) and v.id == "self":
            called.add(node.func.attr)

# 只检查本类「看起来是方法」的调用：排除明显的属性字段（配置/store/常量），
# 用启发式：名字以 _ 开头、或调用出现在 await 表达式里 → 视为方法。属性字段极少带下划线前缀调用。
candidate = {n for n in called if n.startswith("_")}
missing = sorted(candidate - defined)

# 白名单：确实是从 mixin / 动态挂载 / 属性回调来的名字（如确有必要在此登记并注明原因）
WHITELIST = set()

missing = [m for m in missing if m not in WHITELIST]
if missing:
    print("FAIL self.xxx(...) 调用但类里没有 def：", missing)
    sys.exit(1)
print(f"OK: {len(candidate)} 个 self._xxx(...) 调用全部有定义（类内共 {len(defined)} 方法）")
