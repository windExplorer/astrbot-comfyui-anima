# -*- coding: utf-8 -*-
"""v8.14.3：工作台 / LLM 工具显式采样参数在 ComfyUI 链路生效的回归测试。

从 main.py 原样抠出 `_apply_explicit_sampler_overrides`（纯函数）执行，
避免为本机跑不了 astrbot 运行时而重写一遍逻辑。直接 python 本文件即可。
"""
import ast
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

_MAIN = (Path(REPO) / "main.py").read_text(encoding="utf-8")
_TREE = ast.parse(_MAIN)
_FN = next(
    n for n in _TREE.body
    if isinstance(n, ast.FunctionDef) and n.name == "_apply_explicit_sampler_overrides"
)
_NS: dict = {}
exec(compile(ast.Module(body=[_FN], type_ignores=[]), "main.py", "exec"), _NS)
apply_ov = _NS["_apply_explicit_sampler_overrides"]


def test_explicit_steps_cfg():
    wf = {"default_steps": 8, "steps_off": True,
          "default_cfg": 1.0, "cfg_off": True}
    apply_ov(wf, steps=20, cfg=5.5)
    assert wf["default_steps"] == 20 and wf["steps_off"] is False
    assert wf["default_cfg"] == 5.5 and wf["cfg_off"] is False
    print("== 1. 显式 steps/cfg 覆盖工作流默认 OK")


def test_string_numbers_from_webui():
    wf: dict = {}
    apply_ov(wf, steps="24", cfg="7.5")
    assert wf["default_steps"] == 24 and wf["default_cfg"] == 7.5
    assert wf["steps_off"] is False and wf["cfg_off"] is False
    print("== 2. 工作台字符串数字转换 OK")


def test_empty_keeps_workflow_config():
    wf = {"default_steps": 8, "steps_off": False, "ov_sampler": "euler"}
    apply_ov(wf, steps=None, cfg=0, sampler="", noise_schedule=None)
    assert wf["default_steps"] == 8 and wf["steps_off"] is False
    assert wf["ov_sampler"] == "euler"
    assert "default_cfg" not in wf and "ov_scheduler" not in wf
    print("== 3. 未显式指定（None/0/空串）不覆盖 OK")


def test_sampler_scheduler():
    wf: dict = {}
    apply_ov(wf, sampler="er_sde", noise_schedule="normal")
    assert wf["ov_sampler"] == "er_sde" and wf["ov_scheduler"] == "normal"
    print("== 4. 显式采样器/调度器写入 ov_* OK")


def test_invalid_values_no_crash():
    wf = {"default_steps": 8, "ov_sampler": "euler"}
    apply_ov(wf, steps="abc", cfg="xyz", sampler=123)
    assert wf["default_steps"] == 8 and wf["ov_sampler"] == "euler"
    print("== 5. 非法值安全跳过 OK")


def test_call_site_before_apply_overrides():
    """覆盖必须发生在 _apply_base_overrides 之前，否则 ov_sampler 读不到新值。"""
    call = "_apply_explicit_sampler_overrides(wf, steps, cfg, sampler, noise_schedule)"
    assert call in _MAIN
    i_call = _MAIN.index(call)
    i_override = _MAIN.index("self._apply_base_overrides(wf, prompt)", i_call)
    assert i_call < i_override
    print("== 6. 调用时序守卫 OK（覆盖早于 _apply_base_overrides）")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
    print(f"RESULT: {len(tests)} tests passed")
