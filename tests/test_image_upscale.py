"""图片放大（v7.7.1 起，v7.7.5 改为「更多功能」多条条目，v7.7.12 收编 SeedVR2）。

覆盖：
1) `workflow_parser._parse_upscale` 的**两种放大形态**：
   · 无采样器型（VOSR2）：`图 → 放大 → 保存`；
   · 单步采样器型（SeedVR2 3B，v7.7.12）：`图 → 缩放 → VAE编码 → KSampler(steps=1)
     → VAE解码 → 后处理 → 保存`，回溯要能**穿过采样器**，倍率在带点号的
     `resize_type.multiplier` 上；
   同时「丢了采样器的出图工作流」「连线断掉的放大工作流」都必须被拒；
2) 「图片放大」指令参数解析：`3x` / `x3` / `3倍` / `--倍率 3` / 纯数字；
3) 倍率规则：允许列表校验 + 不在范围内回落默认；倍率模式守卫（非 multiplier 不认）；
4) prompt 注入：倍率写进独立整数节点（`easy int.value` / `resize_type.multiplier`）、
   种子按 random/fixed 策略；
5) **功能条目**（v7.7.5）：`features` 列表解析（含旧版单条 image_upscale 兼容）、
   按名字/ID 点名、停用与全停用的提示、条目绑定的基础工作流解析（绑错类型/找不到要报错）；
6) 真实入库链路：上传 → 解析通过 → 条目绑定 → 三处注入（图 / 倍率 / 种子）；
7) **文本写入点安全网**（v7.7.12）：`unet_name` 这类资源名**绝不当**提示词写入点——
   旧逻辑会把提示词写进模型名（SeedVR2 实测把 unet_name 写成 "lowres"），现在必须明确报错。

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
    "_upscale_entries", "_upscale_entry_enabled", "_upscale_param",
    "_parse_scale_list", "_resolve_upscale_scale", "_parse_upscale_args",
    "_upscale_base_rows", "_upscale_base_of", "_resolve_upscale_entry",
    "_load_upscale_base", "_apply_upscale_scale", "_apply_upscale_seed",
    "_strip_command",
}
NS = _load_helpers(NAMES)
_strip_command = NS["_strip_command"]

def _load_module_funcs(want: set[str]) -> dict:
    """摘 main.py 的**模块级**函数（尺寸护栏那批是模块级纯函数）与它们依赖的常量。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    ns: dict = {"re": re}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {getattr(t, "id", "") for t in tgts}
        if names & {"_UPSCALE_LIMIT_PRESETS", "_UPSCALE_LIMIT_ALIASES",
                    "_UPSCALE_SCALE_ARG_RE", "_UPSCALE_SCALE_FLAGS"}:
            exec(ast.get_source_segment(src, node) or "", ns)  # noqa: S102
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in want:
            exec(textwrap.dedent(ast.get_source_segment(src, node) or ""), ns)  # noqa: S102
    missing = want - set(ns)
    assert not missing, f"没摘到这些模块级函数: {missing}"
    return ns


_LIM = _load_module_funcs({
    "_upscale_limits_of", "_upscale_input_verdict", "_upscale_fit_scale", "_upscale_limits_desc",
})
_upscale_limits_of = _LIM["_upscale_limits_of"]
_upscale_input_verdict = _LIM["_upscale_input_verdict"]
_upscale_fit_scale = _LIM["_upscale_fit_scale"]
_upscale_limits_desc = _LIM["_upscale_limits_desc"]


_upscale_entries = NS["_upscale_entries"]
_upscale_entry_enabled = NS["_upscale_entry_enabled"]
_upscale_param = NS["_upscale_param"]
_parse_scale_list = NS["_parse_scale_list"]
_resolve_upscale_scale = NS["_resolve_upscale_scale"]
_parse_upscale_args = NS["_parse_upscale_args"]
_upscale_base_of = NS["_upscale_base_of"]
_resolve_upscale_entry = NS["_resolve_upscale_entry"]
_load_upscale_base = NS["_load_upscale_base"]
_apply_upscale_scale = NS["_apply_upscale_scale"]
_apply_upscale_seed = NS["_apply_upscale_seed"]


class _FakeStore:
    """最小可用的基础工作流库（list_all / get）。"""

    def __init__(self, rows):
        self._rows = [dict(r) for r in (rows or [])]

    def list_all(self):
        return [dict(r) for r in self._rows]

    def get(self, wf_id, with_json: bool = False):
        for r in self._rows:
            if int(r.get("id") or 0) == int(wf_id):
                return dict(r)
        return None


class _FakePlugin:
    """只实现被摘方法依赖的几处：配置读取、工作流库。"""

    def __init__(self, rows=None, cfg=None):
        self.workflow_store = _FakeStore(rows or [])
        self._cfg_all = dict(cfg or {})

    def _cfg(self, key, default=None):
        v = self._cfg_all.get(key, default)
        return v if v is not None else default


# `_upscale_entries` / `_upscale_base_of` / `_resolve_upscale_entry` 内部互相调用与
# 依赖 `self._upscale_base_rows()` → 摘出来的那几个都绑给假 self
_FakePlugin._upscale_base_rows = NS["_upscale_base_rows"]  # type: ignore[attr-defined]
_FakePlugin._upscale_entries = NS["_upscale_entries"]      # type: ignore[attr-defined]
_FakePlugin._upscale_entry_enabled = staticmethod(NS["_upscale_entry_enabled"])  # type: ignore[attr-defined]


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

# ── 与 logs/seedvr2-3b-int8.json 等价的 SeedVR2 3B 放大（内联） ──────────────
# v7.7.12 新收的形态：**有 KSampler**（单步扩散），但条件来自 SeedVR2Conditioning，
# 链上没有任何文本编码器；倍率在 ResizeImageMaskNode.resize_type.multiplier 上。
# 节点 ID 带 `:` 是 ComfyUI 子图展开的正常现象。
SEEDVR2 = {
    "73": {"class_type": "SaveImage",
           "inputs": {"filename_prefix": "seedvr2_after/image", "images": ["74:59", 0]}},
    "75": {"class_type": "LoadImage", "inputs": {"image": "hardcoded.jpg"}},
    "74:48": {"class_type": "VAEEncodeTiled",
              "inputs": {"tile_size": 512, "overlap": 128, "temporal_size": 4096,
                         "temporal_overlap": 8, "pixels": ["74:58", 0], "vae": ["74:51", 0]}},
    "74:50": {"class_type": "JoinImageWithAlpha",
              "inputs": {"image": ["75", 0], "alpha": ["75", 1]}},
    "74:51": {"class_type": "VAELoader",
              "inputs": {"vae_name": "seedvr2_ema_vae_fp16.safetensors"}},
    "74:52": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "seedvr2_3b_int8_convrot.safetensors",
                         "weight_dtype": "default"}},
    "74:54": {"class_type": "KSampler",
              "inputs": {"seed": 219920970385576, "steps": 1, "cfg": 1,
                         "sampler_name": "euler", "scheduler": "simple", "denoise": 1,
                         "model": ["74:52", 0], "positive": ["74:61", 0],
                         "negative": ["74:61", 1], "latent_image": ["74:48", 0]}},
    "74:55": {"class_type": "VAEDecodeTiled",
              "inputs": {"tile_size": 512, "overlap": 128, "temporal_size": 4096,
                         "temporal_overlap": 8, "samples": ["74:54", 0], "vae": ["74:51", 0]}},
    "74:57": {"class_type": "ResizeImageMaskNode",
              "inputs": {"resize_type": "scale by multiplier",
                         "resize_type.multiplier": 2, "scale_method": "lanczos",
                         "input": ["74:50", 0]}},
    "74:58": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["74:57", 0]}},
    "74:59": {"class_type": "SeedVR2PostProcessing",
              "inputs": {"color_correction_method": "none", "images": ["74:55", 0],
                         "original_resized_images": ["74:57", 0]}},
    "74:61": {"class_type": "SeedVR2Conditioning",
              "inputs": {"model": ["74:52", 0], "vae_conditioning": ["74:48", 0]}},
}

# 三条可用条目（第 2 条停用）+ 一条 base_id 指向非放大工作流
_ROWS = [
    {"id": 3, "name": "VOSR2 极速放大", "file_name": "vosr2-api.json",
     "parse_ok": True, "roles": {"kind": "upscale"}},
    {"id": 5, "name": "Anime Sharp", "file_name": "anime-sharp.json",
     "parse_ok": True, "roles": {"kind": "upscale"}},
    {"id": 7, "name": "出图工作流", "parse_ok": True, "roles": {"kind": "t2i"}},
    {"id": 8, "name": "坏掉的放大图", "parse_ok": False, "roles": {"kind": "upscale"}},
]
_ENTRIES = [
    {"__template_key": "k_vosr", "kind": "upscale", "name": "vosr2",
     "enabled": True, "base_id": "3", "default_scale": 3, "allowed_scales": "2,3,4",
     "seed_mode": "random", "timeout": 300},
    {"__template_key": "k_sharp", "kind": "upscale", "name": "sharp",
     "enabled": False, "base_id": "5", "default_scale": 2},
]


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
                              "field_name": "scale",
                              "node_class": "TESpeedVOSR2Image"}, roles["scale"]
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

    broken = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
        "2": {"class_type": "TESpeedVOSR2Image", "inputs": {"images": "in.png"}},
        "9": {"class_type": "SaveImageExtended", "inputs": {"images": ["2", 0]}},
    }
    roles, errors = wp.parse_workflow(broken)
    assert roles is None and any("连回图像输入节点" in e for e in errors), errors

    noop = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
        "9": {"class_type": "SaveImageExtended", "inputs": {"images": ["1", 0]}},
    }
    roles, errors = wp.parse_workflow(noop)
    assert roles is None and any("处理节点" in e for e in errors), errors

    no_save = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
        "2": {"class_type": "TESpeedVOSR2Image", "inputs": {"images": ["1", 0]}},
    }
    roles, errors = wp.parse_workflow(no_save)
    assert roles is None and any("采样器" in e for e in errors), errors
    print("== 2. 防误判（丢采样器的出图图 / 断链 / 无处理节点 / 无保存节点） OK")


def test_parse_args():
    """指令参数：功能名 + 倍率（3x / x3 / 3倍 / --倍率 3 / 纯数字）。"""
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
        ("超分放大 --scale 3 二号功能", ("超分放大 二号功能", 3)),
        ("vosr2 --倍率", ("vosr2", None)),
        ("VOSR 2.0", ("VOSR 2.0", None)),
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

    fixed = _apply_upscale_seed(prompt, roles, {"seed_mode": "fixed", "seed_value": 6688})
    assert fixed == [6688] and prompt["2"]["inputs"]["seed"] == 6688, prompt["2"]
    rnd = _apply_upscale_seed(prompt, roles, {"seed_mode": "random"})
    assert len(rnd) == 1 and 1 <= rnd[0] < 2 ** 31, rnd
    assert prompt["2"]["inputs"]["seed"] == rnd[0]

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


def test_entries_and_pick():
    """功能条目（v7.7.5）：列表解析、旧版单条兼容、点名、停用与全停用提示。"""
    me = _FakePlugin(_ROWS, {"features": _ENTRIES})
    rows = _upscale_entries(me)
    assert [e["name"] for e in rows] == ["vosr2", "sharp"], rows
    assert _upscale_entry_enabled(rows[0]) is True and _upscale_entry_enabled(rows[1]) is False
    assert _upscale_param(rows[1], "default_scale", 3) == 2        # 条目自己的值
    assert _upscale_param(rows[0], "seed_mode", "random") == "random"
    assert _upscale_param({"allowed_scales": "  "}, "allowed_scales", "2,3,4") == "2,3,4"
    assert _upscale_param({}, "default_scale", 3) == 3

    # 不写名字 → 第一个启用的（sharp 停用，故选 vosr2）
    assert _resolve_upscale_entry(me, "")["name"] == "vosr2"
    # 点名：功能名 / 条目标识 / 绑定的基础工作流 ID 或名字（含文件名包含）
    assert _resolve_upscale_entry(me, "vosr2")["name"] == "vosr2"
    assert _resolve_upscale_entry(me, "k_vosr")["name"] == "vosr2"
    assert _resolve_upscale_entry(me, "3")["name"] == "vosr2"
    assert _resolve_upscale_entry(me, "VOSR2 极速放大")["name"] == "vosr2"
    assert _resolve_upscale_entry(me, "vosr2-api")["name"] == "vosr2"
    # 点名到「已停用」的条目 → 明确说停用（不是「找不到」）
    for spec in ("sharp", "k_sharp", "5", "Anime Sharp"):
        try:
            _resolve_upscale_entry(me, spec)
            raise AssertionError(f"{spec}: 停用的条目不该被点名选中")
        except ValueError as e:
            assert "已停用" in str(e), (spec, e)
    try:
        _resolve_upscale_entry(me, "不存在的")
        raise AssertionError("点不存在的名字应报错")
    except ValueError as e:
        assert "没找到叫" in str(e), e

    # 只有停用条目 → 明确提示
    me_off = _FakePlugin(_ROWS, {"features": [_ENTRIES[1]]})
    try:
        _resolve_upscale_entry(me_off, "")
        raise AssertionError("全停用时应报错")
    except ValueError as e:
        assert "都被停用" in str(e), e

    # 一条都没配 → 引导去「更多功能」添加
    me_none = _FakePlugin(_ROWS, {})
    try:
        _resolve_upscale_entry(me_none, "")
        raise AssertionError("没条目时应报错")
    except ValueError as e:
        assert "还没有添加图片放大" in str(e), e

    # 旧版单条配置（image_upscale 对象）→ 折算成一条，spec 走 base_name 匹配
    me_legacy = _FakePlugin(_ROWS, {"image_upscale": {
        "enabled": True, "workflow": "VOSR2 极速放大", "default_scale": 4,
        "allowed_scales": "2,4", "seed_mode": "fixed", "seed_value": 7,
    }})
    lg = _upscale_entries(me_legacy)
    assert len(lg) == 1 and lg[0]["name"] == "VOSR2 极速放大", lg
    assert lg[0]["default_scale"] == 4 and lg[0]["seed_mode"] == "fixed"
    assert _upscale_base_of(me_legacy, lg[0])["id"] == 3
    print("== 6. 功能条目（列表/兼容/点名/停用/未配置） OK")


def test_base_binding():
    """条目绑定的基础工作流解析：base_id / 名字 / 唯一一个 / 绑错类型与失效的报错。"""
    me = _FakePlugin(_ROWS, {"features": _ENTRIES})
    assert _upscale_base_of(me, {"base_id": "3"})["id"] == 3
    assert _upscale_base_of(me, {"base_name": "Anime Sharp"})["id"] == 5
    # base_id 指向不存在的记录：没有名字可回退、库里有多个 → 提示去绑一个
    try:
        _upscale_base_of(_FakePlugin(_ROWS, {}), {"base_id": "999"})
        raise AssertionError("base_id 失效且库里有多个放大工作流时应报错")
    except ValueError as e:
        assert "没有绑定放大工作流" in str(e), e
    # 库里只有一个放大工作流 → 直接用它
    assert _upscale_base_of(_FakePlugin([_ROWS[0]], {}), {"base_id": "999"})["id"] == 3
    assert _upscale_base_of(_FakePlugin([_ROWS[0]], {}), {})["id"] == 3

    # 绑到非放大类工作流 → 明确报错（提示改绑）
    try:
        _upscale_base_of(me, {"base_id": "7", "name": "x"})
        raise AssertionError("绑非放大类应报错")
    except ValueError as e:
        assert "不是放大类工作流" in str(e), e
    # 绑到解析未通过的 → 报错
    try:
        _upscale_base_of(me, {"base_id": "8"})
        raise AssertionError("绑解析未通过应报错")
    except ValueError as e:
        assert "解析未通过" in str(e), e
    # 名字绑了个不存在的 → 报错
    try:
        _upscale_base_of(me, {"base_name": "没这个"})
        raise AssertionError("绑不存在的名字应报错")
    except ValueError as e:
        assert "不存在或未通过解析" in str(e), e
    # 库里一个放大类都没有 → 引导去基础工作流页
    only_t2i = _FakePlugin([_ROWS[2]], {})
    try:
        _upscale_base_of(only_t2i, {})
        raise AssertionError("没有放大工作流时应报错")
    except ValueError as e:
        assert "还没有可用的放大工作流" in str(e), e
    print("== 7. 绑定解析（ID/名字/唯一/绑错类型/失效/无可用） OK")


def test_store_import_and_pick():
    """真实入库链路：VOSR2 上传 → 条目绑定 → 挑中 → 三处注入（图 / 倍率 / 种子）。"""
    import tempfile

    from workflow_store import WorkflowStore

    st = WorkflowStore(Path(tempfile.mkdtemp()))
    wf_id, roles, err = st.import_json("VOSR2 极速放大", json.dumps(VOSR2), "vosr2-api.json")
    assert err is None and wf_id, (wf_id, err)
    assert roles["kind"] == "upscale", roles
    rows = st.list_all()
    assert rows and rows[0]["parse_ok"] is True, rows
    assert rows[0]["roles"]["kind"] == "upscale" and rows[0]["name"] == "VOSR2 极速放大"

    # 真实 store + 一条绑定它的条目
    me = _FakePlugin()
    me.workflow_store = st
    me._cfg_all = {"features": [{
        "__template_key": "k1", "kind": "upscale", "name": "vosr2", "enabled": True,
        "base_id": str(wf_id), "default_scale": 3, "allowed_scales": "2,3,4",
    }]}
    entry = _resolve_upscale_entry(me, "")
    assert entry["name"] == "vosr2"
    rec = _upscale_base_of(me, entry)
    assert rec["id"] == wf_id
    # ★v7.7.8 回归：绑定的记录**必须带工作流 JSON**——`WorkflowStore.get()` 默认会把它摘掉，
    #   只拿 roles 的话出图时 prompt 是空字典，报「图片输入节点写入失败」（线上踩的就是这个）。
    assert rec.get("wf_json"), "取回的记录丢了 wf_json（应为 with_json=True 取）"
    _, _p = _load_upscale_base(rec)
    assert _p and rec["roles"]["image_node"] in _p, "拼出来的 prompt 里没有图片节点"

    # 取完整记录 → 注入「输入图 + 倍率 + 种子」三处
    full = st.get(wf_id, with_json=True)
    _, prompt = _load_upscale_base(full)
    assert wb.set_image_node(prompt, full["roles"]["image_node"], "uploaded.png")
    assert prompt["1"]["inputs"]["image"] == "uploaded.png"
    assert _apply_upscale_scale(prompt, full["roles"], 2) == 2
    assert prompt["6"]["inputs"]["value"] == 2
    assert _apply_upscale_seed(prompt, full["roles"], {"seed_mode": "fixed", "seed_value": 42}) == [42]
    assert prompt["2"]["inputs"]["seed"] == 42

    # 顺带回归：坏工作流（既没采样器也没图/保存）仍按原规则拒绝入库
    bad_id, bad_roles, bad_err = st.import_json(
        "坏图", json.dumps({"1": {"class_type": "LoadImage", "inputs": {}}}), "x.json"
    )
    assert bad_id is None and bad_err, (bad_id, bad_err)

    # v7.7.12：SeedVR2（单步采样器型）同样入库成放大类，并能被条目绑定挑中
    sid, sroles, serr = st.import_json("SeedVR2 3B", json.dumps(SEEDVR2), "seedvr2-3b-int8.json")
    assert serr is None and sid and sroles["kind"] == "upscale", (sid, serr)
    me2 = _FakePlugin()
    me2.workflow_store = st
    me2._cfg_all = {"features": [{
        "__template_key": "k2", "kind": "upscale", "name": "seedvr2", "enabled": True,
        "base_id": str(sid), "default_scale": 2, "allowed_scales": "2,3,4",
        "seed_mode": "random",
    }]}
    entry2 = _resolve_upscale_entry(me2, "")
    assert entry2["name"] == "seedvr2", entry2
    base2 = _upscale_base_of(me2, entry2)
    assert base2["id"] == sid and base2.get("wf_json"), base2
    _, p2s = _load_upscale_base(st.get(sid, with_json=True))
    assert wb.set_image_node(p2s, base2["roles"]["image_node"], "up.png")
    assert p2s["75"]["inputs"]["image"] == "up.png"
    assert _apply_upscale_scale(p2s, base2["roles"], 3) == 3
    assert p2s["74:57"]["inputs"]["resize_type.multiplier"] == 3
    assert _apply_upscale_seed(p2s, base2["roles"],
                               {"seed_mode": "fixed", "seed_value": 7}) == [7]
    assert p2s["74:54"]["inputs"]["seed"] == 7
    # ★关键回归：工作流里**不该**被写入提示词 / 凭空造宽高（旧逻辑会把 unet_name 写坏）
    assert p2s["74:52"]["inputs"]["unet_name"] == "seedvr2_3b_int8_convrot.safetensors"
    assert "width" not in p2s["74:48"]["inputs"] and "height" not in p2s["74:48"]["inputs"]
    print("== 8. 入库链路（VOSR2/SeedVR2 解析通过 + 绑定挑中 + 三处注入 + 坏图仍被拒） OK")


def test_cmd_alias_and_strip():
    """指令别名（含简称「放大」）与参数剥离（v7.7.8）。"""
    # 注册别名必须含「放大」（并保留原有几个）
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "cmd_image_upscale")
    alias: set[str] = set()
    for dec in fn.decorator_list:
        for kw in getattr(dec, "keywords", []) or []:
            if kw.arg == "alias" and isinstance(kw.value, ast.Set):
                alias = {e.value for e in kw.value.elts if isinstance(e, ast.Constant)}
    assert "放大" in alias, f"别名里没有「放大」：{sorted(alias)}"
    assert {"图片超分", "超分", "放大图片"} <= alias, sorted(alias)

    # 参数剥离：主名与各别名都要能剥掉（首 token 命中后返回剩余参数）
    ALIASES = ("放大", "放大图片", "图片超分", "超分")
    cases = [
        ("/图片放大 3x", "3x"),
        ("/放大 3x", "3x"),
        ("/放大", ""),
        ("/放大 vosr2 --倍率 4", "vosr2 --倍率 4"),
        ("/放大图片 2倍", "2倍"),
        ("/超分 vosr2 2x", "vosr2 2x"),
    ]
    for raw, exp in cases:
        got = _strip_command(raw, "图片放大", ALIASES)
        assert got == exp, f"{raw!r}: 期望 {exp!r}，实际 {got!r}"
    print(f"== 9. 指令别名（含简称「放大」）+ 参数剥离（{len(cases)} 组） OK")


def test_size_guard():
    """尺寸护栏（v7.7.9 起；v7.7.10 档位按显存命名，默认 8g）：档位/输入拦截/自适应降倍率/文案。"""
    # 档位解析：默认 8g（面向 8G 卡）；非法档位回落 8g；off=不限；custom 读自定义数值
    g8 = _upscale_limits_of({})
    assert g8["preset"] == "8g" and g8["unlimited"] is False
    assert (g8["in_side"], g8["in_mp"], g8["out_side"], g8["out_mp"]) == (2048, 4, 3072, 8)
    assert _upscale_limits_of(None)["preset"] == "8g"
    assert _upscale_limits_of({"preset": "不存在的档位"})["preset"] == "8g"
    assert _upscale_limits_of({"preset": "OFF"})["unlimited"] is True
    # 旧档位名（v7.7.9 首版的 strict/standard/loose）自动映射到新档位，配置不跑偏
    assert _upscale_limits_of({"preset": "strict"})["preset"] == "8g"
    assert _upscale_limits_of({"preset": "standard"})["preset"] == "12g"
    assert _upscale_limits_of({"preset": "loose"})["preset"] == "16g"
    _cus = _upscale_limits_of({"preset": "custom", "custom_max_input_side": 1500,
                               "custom_max_input_mp": 2, "custom_max_output_side": 2400,
                               "custom_max_output_mp": 5})
    assert (_cus["in_side"], _cus["in_mp"], _cus["out_side"], _cus["out_mp"]) == (1500, 2.0, 2400, 5.0)
    # 容错：负数按 0（= 不限制该项）；非数字回落默认值
    assert _upscale_limits_of({"preset": "custom", "custom_max_input_side": -5})["in_side"] == 0
    assert _upscale_limits_of({"preset": "custom", "custom_max_input_side": "abc",
                               "custom_max_input_mp": 3})["in_mp"] == 3.0
    # 档位宽严有序（8g < 12g < 16g）
    g12, g16 = _upscale_limits_of({"preset": "12g"}), _upscale_limits_of({"preset": "16g"})
    assert g8["in_side"] < g12["in_side"] < g16["in_side"]
    assert g8["out_mp"] < g12["out_mp"] < g16["out_mp"]

    # 文案
    assert _upscale_limits_desc(g8, "in") == "长边 ≤2048px、总像素 ≤4MP"
    assert _upscale_limits_desc(g8, "out") == "长边 ≤3072px、总像素 ≤8MP"
    assert _upscale_limits_desc(g12, "in") == "长边 ≤3072px、总像素 ≤9MP"
    assert _upscale_limits_desc(_upscale_limits_of({"preset": "off"}), "out") == "不限"

    # 输入拦截：正常图放行；超长边 / 超像素分别拦；尺寸未知（0）不拦
    assert _upscale_input_verdict(832, 1216, g8) == (True, "")
    assert _upscale_input_verdict(0, 0, g8) == (True, "")
    ok, why = _upscale_input_verdict(6000, 4000, g8)
    assert ok is False and "长边 6000px" in why, why
    ok, why = _upscale_input_verdict(2048, 2000, g8)     # 长边刚好达标，但 4.1MP 超限
    assert ok is False and "总像素" in why, why
    assert _upscale_input_verdict(2000, 2000, g8)[0] is True    # 恰好 4MP（≤4MP）放行
    assert _upscale_input_verdict(3840, 2160, g16)[0] is True   # 16G 档放行 4K
    assert _upscale_input_verdict(3840, 2160, g8)[0] is False   # 8G 档拦 4K（长边 3840 > 2048）

    # 自适应倍率：放得下就不动；放不下降档；连 1× 都不行 → None；off/尺寸未知 → 不动
    assert _upscale_fit_scale(832, 1216, 3, g8)[0] == 2        # 8G 档：3× 到 2496×3648 超长边 → 2×
    assert _upscale_fit_scale(832, 1216, 3, g12) == (3, "")    # 12G 档不动
    s, note = _upscale_fit_scale(1024, 1536, 3, g8)            # 3072×4608 超长边 → 2×
    assert s == 2 and "已自动降为 2×" in note, (s, note)
    assert _upscale_fit_scale(1024, 1536, 4, g8)[0] == 2       # 4× 一路降到 2×
    s, note = _upscale_fit_scale(2496, 3648, 4, g12)      # 12G 档：降到 1×
    assert s == 1 and "降为 1×" in note, (s, note)
    # 8G 档：这个尺寸连 1× 都超（长边 3648 > 3072、9.1MP > 8MP）⇒ 直接拒绝
    #（注意：实际链路里这种输入更早已被「输入上限」拦下，这里是直接单测降档函数）
    assert _upscale_fit_scale(2496, 3648, 4, g8)[0] is None
    assert _upscale_fit_scale(3000, 3000, 2, g8)[0] is None    # 8G 档：1× 也超 8MP ⇒ 拒绝
    assert _upscale_fit_scale(3000, 3000, 2, g12)[0] == 1      # 12G 档：降到 1×（9MP ≤ 12MP）
    assert _upscale_fit_scale(5000, 5000, 2, g8)[0] is None    # 连 1× 都超输出长边 ⇒ 拒绝
    assert _upscale_fit_scale(832, 1216, 4, g16)[0] == 4       # 16G 档不动
    assert _upscale_fit_scale(832, 1216, 4, _upscale_limits_of({"preset": "off"})) == (4, "")
    assert _upscale_fit_scale(0, 0, 3, g8) == (3, "")

    # 与条目参数配合：允许列表回落默认 → 再按尺寸降档（两层规则不互相打架）
    scale, _ = _resolve_upscale_scale(8, [2, 3, 4], 3)          # 8 不在列表 → 回落 3
    assert scale == 3
    assert _upscale_fit_scale(1024, 1536, scale, g8)[0] == 2
    print("== 10. 尺寸护栏（档位 8g/12g/16g + 旧名映射 / 输入拦截 / 自适应降倍率 / 文案） OK")


def test_seedvr2_workflow():
    """SeedVR2（单步采样器型，v7.7.12）：收成放大类，倍率/种子落到正确节点。"""
    roles, errors = wp.parse_workflow(SEEDVR2)
    assert not errors, errors
    assert roles and roles["kind"] == "upscale", roles
    # 有采样器，但它是「放大执行体」（不是出图链路）
    assert roles["sampler"] == "74:54", roles["sampler"]
    assert roles["image_node"] == "75" and roles["save"]["node"] == "73", roles
    assert roles["apply"] == {"node": "74:54", "class_type": "KSampler"}, roles["apply"]
    assert roles["loader"] == {"node": "74:52", "class_type": "UNETLoader"}, roles["loader"]
    assert roles["model_file"] == "seedvr2_3b_int8_convrot.safetensors", roles["model_file"]
    # 回溯必须**穿过采样器**：解码 / 采样 / 编码 / 预处理 / 缩放都要在链上
    for nid in ("74:59", "74:55", "74:54", "74:48", "74:58", "74:57"):
        assert nid in roles["chain_nodes"], (nid, roles["chain_nodes"])
    # 倍率：ResizeImageMaskNode 的**带点号**字段
    assert roles["scale"]["node"] == "74:57", roles["scale"]
    assert roles["scale"]["field"] == "resize_type.multiplier", roles["scale"]
    assert roles["scale"]["default"] == 2, roles["scale"]
    assert roles["seed"] == {"node": "74:54", "field": "seed",
                             "default": 219920970385576}, roles["seed"]
    # 关键：正向/负向/宽高必须为空——绝不能把 unet_name 当文本框、给 VAEEncodeTiled 造 width
    assert roles["positive"] is None and roles["negative"] is None, roles
    assert roles["latent"] is None, roles["latent"]
    # 真实导出（logs/seedvr2-3b-int8.json）也过一遍
    real = ROOT / "logs" / "seedvr2-3b-int8.json"
    if real.is_file():
        r2, e2 = wp.parse_workflow(json.loads(real.read_text(encoding="utf-8")))
        assert not e2 and r2["kind"] == "upscale", (e2, r2)
        assert r2["scale"]["field"] == "resize_type.multiplier", r2["scale"]
        assert r2["seed"]["node"] == "74:54" and r2["image_node"] == "75"
    print("== 11. 放大类工作流解析（SeedVR2 单步采样器型：穿采样器回溯 + 带点号倍率） OK")


def test_scale_mode_guard():
    """倍率模式守卫：`resize_type` 不是「按倍率」时，multiplier 字段不算可写倍率。"""
    wf = json.loads(json.dumps(SEEDVR2))
    wf["74:57"]["inputs"]["resize_type"] = "scale by side length"
    roles, errors = wp.parse_workflow(wf)
    assert not errors and roles["kind"] == "upscale", (errors, roles)
    assert roles["scale"] is None, roles["scale"]
    print("== 12. 倍率模式守卫（非 multiplier 模式不认 resize_type.multiplier） OK")


def test_text_field_safety_net():
    """安全网（v7.7.12）：资源名字段（unet_name 等）绝不当提示词写入点。"""
    # ① 有采样器、无文本编码点、也没图输入 → 明确报错，且提示「按放大工作流上传」
    bad = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "a.safetensors",
                                                     "weight_dtype": "default"}},
        "2": {"class_type": "SeedVR2Conditioning", "inputs": {"model": ["1", 0]}},
        "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024}},
        "4": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 1, "cfg": 1,
                                                   "model": ["1", 0], "positive": ["2", 0],
                                                   "negative": ["2", 1],
                                                   "latent_image": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
    }
    roles, errors = wp.parse_workflow(bad)
    assert roles is None and errors, (roles, errors)
    assert any("可写入文本节点" in e for e in errors), errors
    assert any("放大工作流" in e for e in errors), errors
    # ② 走到 UNETLoader 时，定位必须返回 None（旧逻辑会返回 ("1", "unet_name")）
    assert wp._resolve_text_write_node(bad, ("2", 0)) is None
    # ③ 正常文本节点不受影响
    ok = {"2": {"class_type": "CLIPTextEncode", "inputs": {"text": "1girl", "clip": ["1", 1]}}}
    assert wp._resolve_text_write_node(ok, ("2", 0)) == ("2", "text")
    # ④ 资源名判定本身
    assert wp._is_resource_field("unet_name", bad["1"]) is True
    assert wp._is_resource_field("lora_name", {"inputs": {"lora_name": "x.safetensors"}}) is True
    assert wp._is_resource_field("text", {"inputs": {"text": "1girl"}}) is False
    assert wp._is_resource_field("positive", {"inputs": {"positive": "1girl"}}) is False
    print("== 13. 文本写入点安全网（资源名判失败 + 明确报错 + 正常文本框不受影响） OK")


if __name__ == "__main__":
    test_parse_upscale_workflow()
    test_reject_cases()
    test_parse_args()
    test_scale_rules()
    test_prompt_injection()
    test_entries_and_pick()
    test_base_binding()
    test_store_import_and_pick()
    test_cmd_alias_and_strip()
    test_size_guard()
    test_seedvr2_workflow()
    test_scale_mode_guard()
    test_text_field_safety_net()
    print("图片放大（解析/参数/倍率/注入/条目/绑定/入库/别名/尺寸护栏/SeedVR2/安全网）全部通过")
