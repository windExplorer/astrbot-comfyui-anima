"""图片放大（v7.7.1）：放大类工作流解析 + 指令参数 + 倍率规则 + prompt 注入。

覆盖：
1) `workflow_parser._parse_upscale`：VOSR2 这类「图 → 放大 → 保存」的无采样器工作流
   能被解析成 `kind="upscale"`（图节点 / 处理节点 / 保存节点 / 倍率 / 种子注记）；
   同时「丢了采样器的出图工作流」「连线断掉的放大工作流」都必须被拒；
2) 「图片放大」指令参数解析：`3x` / `x3` / `3倍` / `--倍率 3` / 纯数字；
3) 倍率规则：允许列表校验 + 不在范围内回落默认；
4) prompt 注入：倍率写进独立整数节点（`easy int.value`）、种子按 random/fixed 策略；
5) 放大工作流的挑选：ID / 名字 / 包含匹配 / 唯一一个 / 配置绑定 / 找不到报错。

main.py 里的方法依赖 astrbot 运行时（本地装不了），沿用 tests/test_size_helpers.py 的
做法：用 ast 把源码摘出来单独执行。

跑法：python tests/test_image_upscale.py
"""
import ast
import json
import random
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import workflow_parser as wp            # noqa: E402
import workflow_builder as wb           # noqa: E402


class _Log:
    """顶替 astrbot 的 logger（摘出来的方法只用 info/warning/debug）。"""

    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _load_helpers(want: set[str]) -> dict:
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")   # main.py 带 BOM
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    ns: dict = {
        "re": re, "json": json, "random": random,
        "logger": _Log(), "workflow_builder": wb,
    }
    # 模块级常量（正则/flag 表）：`_parse_upscale_args` 依赖它们
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = {getattr(t, "id", "") for t in node.targets}
        if names & {"_UPSCALE_SCALE_ARG_RE", "_UPSCALE_SCALE_FLAGS"}:
            exec(ast.get_source_segment(src, node) or "", ns)  # noqa: S102
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in want:
            continue
        seg = ast.get_source_segment(src, node) or ""
        body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
        exec(textwrap.dedent(body), ns)  # noqa: S102
    missing = want - set(ns)
    assert not missing, f"没摘到这些方法: {missing}"
    return ns


NAMES = {
    "_upscale_cfg", "_parse_scale_list", "_resolve_upscale_scale",
    "_parse_upscale_args", "_upscale_base_rows", "_resolve_upscale_base",
    "_load_upscale_base", "_apply_upscale_scale", "_apply_upscale_seed",
}
NS = _load_helpers(NAMES)

_parse_scale_list = NS["_parse_scale_list"]
_resolve_upscale_scale = NS["_resolve_upscale_scale"]
_parse_upscale_args = NS["_parse_upscale_args"]
_resolve_upscale_base = NS["_resolve_upscale_base"]
_load_upscale_base = NS["_load_upscale_base"]
_apply_upscale_scale = NS["_apply_upscale_scale"]
_apply_upscale_seed = NS["_apply_upscale_seed"]


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def list_all(self):
        return list(self._rows)


class _FakePlugin:
    """只实现被摘方法依赖的两处：工作流库与 image_upscale 配置。"""

    def __init__(self, rows=None, cfg=None):
        self.workflow_store = _FakeStore(rows or [])
        self._all_cfg = {"image_upscale": dict(cfg or {})}

    def _upscale_cfg(self):
        return dict(self._all_cfg.get("image_upscale") or {})


# `_resolve_upscale_base` 内部会调 `self._upscale_base_rows()` → 摘出来的那个绑给假 self
_FakePlugin._upscale_base_rows = NS["_upscale_base_rows"]  # type: ignore[attr-defined]


# ── 与 logs/vosr2-api.json 等价的 VOSR2 工作流（内联，避免依赖 logs 目录）─────
VOSR2 = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
    "2": {"class_type": "TESpeedVOSR2Image",
          "inputs": {"scale": ["6", 0], "seed": 6666, "model": ["3", 0],
                     "images": ["1", 0], "settings": ["4", 0]}},
    "3": {"class_type": "TESpeedVOSR2Loader",
          "inputs": {"model_bundle": "VOSR2", "precision": "auto"}},
    "4": {"class_type": "TESpeedVOSR2Settings",
          "inputs": {"quality_profile": "speed", "tile_size": 512}},
    "6": {"class_type": "easy int", "inputs": {"value": 3}},
    "9": {"class_type": "SaveImageExtended",
          "inputs": {"filename_prefix": "vosr2", "images": ["2", 0]}},
}


def test_parse_upscale_workflow():
    """VOSR2：解析成放大类，并给出倍率 / 种子 / 图节点 / 保存节点注记。"""
    roles, errors = wp.parse_workflow(VOSR2)
    assert not errors, errors
    assert roles and roles["kind"] == "upscale", roles
    assert roles["image_node"] == "1", roles["image_node"]
    assert roles["save"]["node"] == "9" and roles["save"]["class_type"] == "SaveImageExtended"
    assert roles["apply"] == {"node": "2", "class_type": "TESpeedVOSR2Image"}, roles["apply"]
    assert roles["loader"]["node"] == "3" and roles["loader"]["class_type"] == "TESpeedVOSR2Loader"
    assert roles["model_file"] == "VOSR2", roles["model_file"]
    assert roles["chain_nodes"] == ["2"], roles["chain_nodes"]
    # 倍率连线到独立整数节点（easy int.value），必须记到那个节点上
    assert roles["scale"] == {"node": "6", "field": "value", "default": 3,
                              "field_name": "scale"}, roles["scale"]
    assert roles["seed"] == {"node": "2", "field": "seed", "default": 6666}, roles["seed"]
    # 纯放大工作流不该带「出图工作流内部的放大链」注记（免得被 bypass/inject 误用）
    assert roles["upscale"] is None
    # 真实的 VOSR2 导出（logs/ 下）也过一遍（有的话）
    real = ROOT / "logs" / "vosr2-api.json"
    if real.is_file():
        roles2, err2 = wp.parse_workflow(json.loads(real.read_text(encoding="utf-8")))
        assert not err2 and roles2["kind"] == "upscale", (err2, roles2)
        assert roles2["scale"]["node"] == "6" and roles2["seed"]["default"] == 6666
    print("== 1. 放大类工作流解析（VOSR2：图/处理/保存/倍率/种子） OK")


def test_reject_cases():
    """防误判：丢采样器的出图工作流、连线断掉的放大工作流都要被拒。"""
    # ① 有文本编码器 → 按「出图工作流」标准拒（不能收成放大类）
    t2i = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "a.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "x", "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "4": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
    }
    roles, errors = wp.parse_workflow(t2i)
    assert roles is None and errors, (roles, errors)
    assert any("采样器" in e for e in errors), errors

    # ② 放大工作流但处理链没连回图像节点 → 针对性报错
    broken = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
        "2": {"class_type": "TESpeedVOSR2Image", "inputs": {"images": "in.png"}},
        "9": {"class_type": "SaveImageExtended", "inputs": {"images": ["2", 0]}},
    }
    roles, errors = wp.parse_workflow(broken)
    assert roles is None and any("连回图像输入节点" in e for e in errors), errors

    # ③ 保存节点直接接图像输入（中间没有处理节点）→ 报「没找到处理节点」
    noop = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
        "9": {"class_type": "SaveImageExtended", "inputs": {"images": ["1", 0]}},
    }
    roles, errors = wp.parse_workflow(noop)
    assert roles is None and any("处理节点" in e for e in errors), errors

    # ④ 没有保存节点 → 不认成放大类，按出图工作流的采样器报错
    no_save = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
        "2": {"class_type": "TESpeedVOSR2Image", "inputs": {"images": ["1", 0]}},
    }
    roles, errors = wp.parse_workflow(no_save)
    assert roles is None and any("采样器" in e for e in errors), errors
    print("== 2. 防误判（丢采样器的出图图 / 断链 / 无处理节点 / 无保存节点） OK")


def test_parse_args():
    """指令参数：工作流名 + 倍率（3x / x3 / 3倍 / --倍率 3 / 纯数字）。"""
    cases = [
        ("", ("", None)),
        ("vosr2", ("vosr2", None)),
        ("vosr2 3x", ("vosr2", 3)),
        ("vosr2 4X", ("vosr2", 4)),
        ("3x", ("", 3)),
        ("x4", ("", 4)),
        ("2倍", ("", 2)),
        ("vosr2 --倍率 4", ("vosr2", 4)),
        ("--倍数 2 vosr2", ("vosr2", 2)),
        ("vosr2 --倍 5", ("vosr2", 5)),
        ("vosr2 2", ("vosr2", 2)),
        ("超分放大 --scale 3 二号工作流", ("超分放大 二号工作流", 3)),
        ("vosr2 --倍率", ("vosr2", None)),          # flag 后面没值 → 用默认
        ("VOSR 2.0", ("VOSR 2.0", None)),           # 带点的不算倍率，是名字的一部分
        ("anime-upscale-v2", ("anime-upscale-v2", None)),
    ]
    for raw, exp in cases:
        got = _parse_upscale_args(raw)
        assert got == exp, f"{raw!r}: 期望 {exp}，实际 {got}"
    print(f"== 3. 指令参数解析（{len(cases)} 组写法） OK")


def test_scale_rules():
    """允许倍率列表 + 「不在范围内回落默认」。"""
    assert _parse_scale_list("2,3,4") == [2, 3, 4]
    assert _parse_scale_list("2、3 4；4") == [2, 3, 4]      # 顿号/空格/重复
    assert _parse_scale_list("3x,4X") == [3, 4]
    assert _parse_scale_list("") == []
    assert _parse_scale_list("9,0,-1") == []               # 只收 1~8
    assert _resolve_upscale_scale(None, [2, 3, 4], 3) == (3, "未指定倍率，用默认")
    assert _resolve_upscale_scale(4, [2, 3, 4], 3) == (4, "")
    got, why = _resolve_upscale_scale(5, [2, 3, 4], 3)
    assert got == 3 and "不在允许" in why, (got, why)
    assert _resolve_upscale_scale(5, [], 3) == (5, "")        # 不校验
    assert _resolve_upscale_scale(None, [2, 4], 3)[0] == 2    # 默认不在允许里 → 取第一个
    assert _resolve_upscale_scale(7, [2, 4], 3)[0] == 2
    print("== 4. 倍率规则（允许列表校验 + 回落默认） OK")


def test_prompt_injection():
    """倍率写进独立整数节点、种子按 random / fixed 策略。"""
    roles, _ = wp.parse_workflow(VOSR2)
    wf, prompt = _load_upscale_base({"name": "vosr2", "wf_json": json.dumps(VOSR2),
                                     "roles": roles})
    assert wf["name"] == "vosr2" and "_upscale_roles" in wf

    assert _apply_upscale_scale(prompt, roles, 4) == 4
    assert prompt["6"]["inputs"]["value"] == 4            # 倍率写进 easy int
    assert prompt["1"]["inputs"]["image"] == "in.png"     # 没动别的

    # fixed → 用配置值；random → 落在 1..2^31-1
    fixed = _apply_upscale_seed(prompt, roles, {"seed_mode": "fixed", "seed_value": 6688})
    assert fixed == [6688] and prompt["2"]["inputs"]["seed"] == 6688, prompt["2"]
    rnd = _apply_upscale_seed(prompt, roles, {"seed_mode": "random"})
    assert len(rnd) == 1 and 1 <= rnd[0] < 2 ** 31, rnd
    assert prompt["2"]["inputs"]["seed"] == rnd[0]

    # 工作流没暴露倍率/种子 → 不报错、返回 None/[]
    assert _apply_upscale_scale(prompt, {}, 4) is None
    assert _apply_upscale_seed(prompt, {}, {}) == []

    # 字面量倍率：就地写在本节点（scale 是字面量而非连线）
    lit = {"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
           "2": {"class_type": "ImageUpscaleWithModel", "inputs": {"image": ["1", 0], "scale": 2}},
           "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}}}
    roles_l, err_l = wp.parse_workflow(lit)
    assert not err_l and roles_l["scale"]["node"] == "2" and roles_l["scale"]["default"] == 2
    _, p2 = _load_upscale_base({"name": "lit", "wf_json": json.dumps(lit), "roles": roles_l})
    assert _apply_upscale_scale(p2, roles_l, 3) == 3 and p2["2"]["inputs"]["scale"] == 3
    print("== 5. prompt 注入（倍率 / 种子策略 / 字面量倍率） OK")


def test_resolve_base():
    """放大工作流挑选：ID / 名字 / 包含 / 唯一一个 / 配置绑定 / 报错文案。"""
    rows = [
        {"id": 3, "name": "VOSR2 极速放大", "file_name": "vosr2-api.json",
         "parse_ok": True, "roles": {"kind": "upscale"}},
        {"id": 5, "name": "Anime Sharp", "file_name": "anime-sharp.json",
         "parse_ok": True, "roles": {"kind": "upscale"}},
        {"id": 7, "name": "出图工作流", "parse_ok": True, "roles": {"kind": "t2i"}},
        {"id": 8, "name": "坏掉的放大图", "parse_ok": False, "roles": {"kind": "upscale"}},
    ]
    me = _FakePlugin(rows)

    assert _resolve_upscale_base(me, "3")["id"] == 3          # 按 ID
    assert _resolve_upscale_base(me, "vosr2 极速放大")["id"] == 3
    assert _resolve_upscale_base(me, "VOSR2")["id"] == 3      # 名字包含（忽略大小写）
    assert _resolve_upscale_base(me, "anime-sharp")["id"] == 5  # 文件名包含

    # 没指定：库里多个 → 报错并给例子；配置绑定则用它
    try:
        _resolve_upscale_base(me, "")
        raise AssertionError("多个放大工作流时应报错")
    except ValueError as e:
        assert "有 2 个放大工作流" in str(e), e
    me2 = _FakePlugin(rows, {"workflow": "Anime Sharp"})
    assert _resolve_upscale_base(me2, "")["id"] == 5
    me3 = _FakePlugin(rows, {"workflow": "不存在的名字"})
    try:
        _resolve_upscale_base(me3, "")
        raise AssertionError("绑定到不存在的名字时应报错")
    except ValueError as e:
        assert "没找到叫" in str(e), e

    # 只有 1 个可用（解析未通过的不算）→ 直接用它
    only = _FakePlugin([rows[0], rows[2], rows[3]])
    assert _resolve_upscale_base(only, "")["id"] == 3

    # 一个都没有 → 提示去上传
    none = _FakePlugin([rows[2]])
    try:
        _resolve_upscale_base(none, "")
        raise AssertionError("没有放大工作流时应报错")
    except ValueError as e:
        assert "还没有可用的放大工作流" in str(e), e
    print("== 6. 放大工作流挑选（ID/名字/包含/唯一/绑定/报错） OK")


def test_store_import_and_pick():
    """真实入库链路：VOSR2 上传后 parse_ok=True、kind=upscale，能被放大链路挑中并用上。

    这一步覆盖用户实际操作：「基础工作流」页上传 → 解析入库 → 「更多功能」里绑定 → 出图时注入。
    """
    import tempfile

    from workflow_store import WorkflowStore

    st = WorkflowStore(Path(tempfile.mkdtemp()))
    wf_id, roles, err = st.import_json("VOSR2 极速放大", json.dumps(VOSR2), "vosr2-api.json")
    assert err is None and wf_id, (wf_id, err)
    assert roles["kind"] == "upscale", roles
    rows = st.list_all()
    assert rows and rows[0]["parse_ok"] is True, rows
    assert rows[0]["roles"]["kind"] == "upscale" and rows[0]["name"] == "VOSR2 极速放大"

    # 把真实 store 交给挑选逻辑（走 list_all 的真实字段）
    me = _FakePlugin()
    me.workflow_store = st
    assert _resolve_upscale_base(me, "")["id"] == wf_id
    assert _resolve_upscale_base(me, "vosr2 极速放大")["id"] == wf_id
    assert _resolve_upscale_base(me, "vosr2-api")["id"] == wf_id     # 文件名包含匹配

    # 取完整记录 → 注入「输入图 + 倍率 + 种子」三处
    rec = st.get(wf_id, with_json=True)
    _, prompt = _load_upscale_base(rec)
    assert wb.set_image_node(prompt, rec["roles"]["image_node"], "uploaded.png")
    assert prompt["1"]["inputs"]["image"] == "uploaded.png"
    assert _apply_upscale_scale(prompt, rec["roles"], 2) == 2
    assert prompt["6"]["inputs"]["value"] == 2
    assert _apply_upscale_seed(prompt, rec["roles"], {"seed_mode": "fixed", "seed_value": 42}) == [42]
    assert prompt["2"]["inputs"]["seed"] == 42

    # 顺带回归：坏工作流（既没采样器也没图/保存）仍按原规则拒绝入库
    bad_id, bad_roles, bad_err = st.import_json(
        "坏图", json.dumps({"1": {"class_type": "LoadImage", "inputs": {}}}), "x.json"
    )
    assert bad_id is None and bad_err, (bad_id, bad_err)
    print("== 7. 入库链路（解析通过 + 类型 + 挑选 + 三处注入 + 坏图仍被拒） OK")


if __name__ == "__main__":
    test_parse_upscale_workflow()
    test_reject_cases()
    test_parse_args()
    test_scale_rules()
    test_prompt_injection()
    test_resolve_base()
    test_store_import_and_pick()
    print("图片放大（解析/参数/倍率/注入/挑选/入库）全部通过")
