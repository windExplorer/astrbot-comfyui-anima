"""抠图（v7.7.13）：解析 / 挑工作流 / 注入（步数 · 提示词）/ 真实入库。

覆盖：
1) `parse_workflow` 对抠图工作流（Qwen Image 2.1 Edit 去背景）的注记：`kind=img2img`、
   图输入 / 采样器（含默认步数）/ 正向·负向写入点，以及**工作流当前提示词**
   （`positive.default_text`，前端表单预填就靠它）；
2) `_resolve_matting_base`：配置绑定（ID / 名字 / 包含）→ 库里唯一一个 → 各类失败文案
   （库里没有 / 绑到放大类 / 绑到没有图输入的工作流）；
3) 注入：步数写进采样器、提示词写进正向节点；**留空就不写**（= 沿用工作流原值）；
4) 真实入库链路：上传 → parse_ok → default_text 可用于预填 → 挑中 → 注入，
   并确认模型 / CLIP / VAE 等资源节点没被动过；
5) 预处理缩放 + 「不放大」（v7.7.14）：识别 `ImageScaleToTotalPixels`（按总像素归一化，
   小图会被插值放大），并验证「只缩不放」的改写规则（小图保持原尺寸、输入≥目标时不碰节点、
   关掉不碰、px 单位换算回写为整数）。

main.py 里的方法依赖 astrbot 运行时（本地装不了），沿用 tests/test_size_helpers.py 的
做法：用 ast 把源码摘出来单独执行。

跑法：python tests/test_matting.py
"""
import ast
import json
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
        "re": re, "json": json, "logger": _Log(), "workflow_builder": wb,
    }
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
    "_matting_cfg", "_matting_base_rows", "_resolve_matting_base",
    "_load_matting_base", "_apply_matting_steps", "_apply_matting_prompt",
    "_apply_matting_pixel_target", "_strip_command",
}
NS = _load_helpers(NAMES)
_strip_command = NS["_strip_command"]
_load_matting_base = NS["_load_matting_base"]
_apply_matting_steps = NS["_apply_matting_steps"]
_apply_matting_prompt = NS["_apply_matting_prompt"]
_apply_matting_pixel_target = NS["_apply_matting_pixel_target"]
_resolve_matting_base = NS["_resolve_matting_base"]


class _FakeStore:
    """最小工作流库：只实现 resolve 用到的 list_all / get。"""

    def __init__(self, rows):
        self._rows = list(rows)

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


# `_resolve_matting_base` 内部会调这两个（摘出来的）→ 绑给假 self
_FakePlugin._matting_cfg = NS["_matting_cfg"]            # type: ignore[attr-defined]
_FakePlugin._matting_base_rows = NS["_matting_base_rows"]  # type: ignore[attr-defined]


# ── 与 logs/qwen2.1抠图.json 等价的抠图工作流（Qwen Image 2.1 Edit 去背景）──
MATTING = {
    "477": {"class_type": "LoadImage", "inputs": {"image": "hardcoded.png"}},
    "479": {"class_type": "SaveImageAdvanced",
            "inputs": {"filename_prefix": "Qwen_image_2.1", "format": "png",
                       "format.bit_depth": "8-bit", "format.input_color_space": "sRGB",
                       "images": ["478:457", 0]}},
    "478:451": {"class_type": "UNETLoader",
                "inputs": {"unet_name": "qwen_image_2.1_int8_convrot.safetensors",
                           "weight_dtype": "default"}},
    "478:453": {"class_type": "CLIPLoader",
                "inputs": {"clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                           "type": "qwen_image", "device": "default"}},
    "478:454": {"class_type": "VAELoader",
                "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
    "478:456": {"class_type": "EmptyLatentImage",
                "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "478:457": {"class_type": "VAEDecode",
                "inputs": {"samples": ["478:458", 0], "vae": ["478:454", 0]}},
    "478:458": {"class_type": "KSampler",
                "inputs": {"seed": 812501882643461, "steps": 25, "cfg": 1,
                           "sampler_name": "euler", "scheduler": "simple", "denoise": 1,
                           "model": ["478:469", 0], "positive": ["478:474", 0],
                           "negative": ["478:474", 1], "latent_image": ["478:468", 0]}},
    "478:468": {"class_type": "ComfySwitchNode",
                "inputs": {"switch": False, "on_false": ["478:474", 2],
                           "on_true": ["478:456", 0]}},
    "478:469": {"class_type": "QwenImage21Cache",
                "inputs": {"device": "auto", "dtype": "default", "model": ["478:451", 0]}},
    "478:474": {"class_type": "TextEncodeQwenImage21",
                "inputs": {"prompt": "Remove the background, and output a PNG image",
                           "negative_prompt": "", "resolution": 0,
                           "clip": ["478:453", 0], "images.image_1": ["477", 0],
                           "vae": ["478:454", 0]}},
}
PROMPT_TEXT = "Remove the background, and output a PNG image"

# ── 与 logs/qwen2.1抠图-预处理.json 等价（多了一步「缩放图像（像素）」）──────────
# ImageScaleToTotalPixels 按**总像素**归一化：megapixels=1.25 → 小图会被插值放大。
MATTING_SCALE = json.loads(json.dumps(MATTING))
MATTING_SCALE["484"] = {"class_type": "ImageScaleToTotalPixels",
                        "inputs": {"upscale_method": "lanczos", "megapixels": 1.25,
                                   "resolution_steps": 32, "image": ["477", 0]}}
MATTING_SCALE["478:474"]["inputs"]["images.image_1"] = ["484", 0]


def test_parse_matting_workflow():
    """抠图工作流：解析成 img2img，并给出「工作流当前提示词 / 步数」等注记。"""
    roles, errors = wp.parse_workflow(MATTING)
    assert not errors, errors
    assert roles and roles["kind"] == "img2img", roles
    assert roles["image_node"] == "477", roles["image_node"]
    assert roles["sampler"] == "478:458", roles["sampler"]
    # 正向 / 负向：同一个 TextEncodeQwenImage21 的两个字段（不是同一写入点）
    assert roles["positive"]["node"] == "478:474"
    assert roles["positive"]["field"] == "prompt"
    assert roles["positive"]["default_text"] == PROMPT_TEXT, roles["positive"]
    assert roles["negative"]["field"] == "negative_prompt"
    assert not roles["negative"].get("shared_with_positive"), roles["negative"]
    # 步数默认值（「更多功能 → 抠图」表单要填的就是它）
    assert roles["sampler_defaults"]["steps"] == 25, roles["sampler_defaults"]
    assert roles["model_file"] == "qwen_image_2.1_int8_convrot.safetensors", roles["model_file"]
    assert roles["model_patch_nodes"] == ["478:469"], roles["model_patch_nodes"]
    assert roles["save"]["node"] == "479", roles["save"]
    # 真实导出（logs/ 下有就跑）
    real = ROOT / "logs" / "qwen2.1抠图.json"
    if real.is_file():
        r2, e2 = wp.parse_workflow(json.loads(real.read_text(encoding="utf-8")))
        assert not e2 and r2["image_node"] == "477", (e2, r2)
        assert r2["positive"]["default_text"] == PROMPT_TEXT, r2["positive"]
        assert r2["sampler_defaults"]["steps"] == 25
    print("== 1. 抠图工作流解析（图输入 / 采样器 / 提示词与步数默认值） OK")


_ROWS = [
    {"id": 1, "name": "抠图 Qwen2.1", "file_name": "qwen2.1抠图.json", "parse_ok": True,
     "roles": {"kind": "img2img", "image_node": "477", "sampler": "478:458"}},
    {"id": 2, "name": "VOSR2 放大", "parse_ok": True,
     "roles": {"kind": "upscale", "image_node": "1"}},
    {"id": 3, "name": "纯文生图", "parse_ok": True, "roles": {"kind": "t2i"}},
    {"id": 4, "name": "解析失败的图", "parse_ok": False,
     "roles": {"kind": "img2img", "image_node": "477"}},
]


def test_resolve_matting_base():
    """挑工作流：ID / 名字 / 包含 / 唯一，以及各种绑错的文案。"""
    # ① 库里只有 1 个可用候选 → 不配 base_id 也能自动选中
    me = _FakePlugin([_ROWS[0]], {"matting": {}})
    assert _resolve_matting_base(me, "")["id"] == 1
    # ② 配置绑定 ID
    me = _FakePlugin(_ROWS, {"matting": {"base_id": "1"}})
    assert _resolve_matting_base(me, "")["id"] == 1
    # ③ 指令参数点名：名字 / 包含 / 文件名包含
    assert _resolve_matting_base(me, "抠图 Qwen2.1")["id"] == 1
    assert _resolve_matting_base(me, "qwen2.1抠图")["id"] == 1
    # ④ 放大类不可绑（即使显式按 ID 绑）
    me_up = _FakePlugin(_ROWS, {"matting": {"base_id": "2"}})
    try:
        _resolve_matting_base(me_up, "")
        raise AssertionError("绑到放大类应当报错")
    except ValueError as e:
        assert "放大类" in str(e), e
    # ⑤ 没有图输入的工作流不可绑
    me_t2i = _FakePlugin(_ROWS, {"matting": {"base_id": "3"}})
    try:
        _resolve_matting_base(me_t2i, "")
        raise AssertionError("绑到无图输入的工作流应当报错")
    except ValueError as e:
        assert "没有图片输入节点" in str(e), e
    # ⑥ 解析未通过 → 提示重新解析（且它不会出现在候选里）
    me_bad = _FakePlugin(_ROWS, {"matting": {"base_id": "4"}})
    try:
        _resolve_matting_base(me_bad, "")
        raise AssertionError("绑到解析失败的工作流应当报错")
    except ValueError as e:
        assert "解析未通过" in str(e), e
    # ⑦ 库里一个候选都没有 → 引导去基础工作流页
    only_t2i = _FakePlugin([_ROWS[2]], {"matting": {}})
    try:
        _resolve_matting_base(only_t2i, "")
        raise AssertionError("没有可用抠图工作流时应报错")
    except ValueError as e:
        assert "还没有可用的抠图工作流" in str(e), e
    # ⑧ 有多个候选 + 没绑 → 要求指定
    two = _FakePlugin([_ROWS[0], {"id": 5, "name": "抠图B", "parse_ok": True,
                                  "roles": {"kind": "img2img", "image_node": "1"}}],
                      {"matting": {}})
    try:
        _resolve_matting_base(two, "")
        raise AssertionError("多个候选且未绑定时应要求指定")
    except ValueError as e:
        assert "请到「更多功能 → 抠图」里指定" in str(e), e
    # ⑨ 点名不存在的工作流 → 列出可用的
    try:
        _resolve_matting_base(me, "不存在的抠图")
        raise AssertionError("点名不存在时应报错")
    except ValueError as e:
        assert "没找到叫「不存在的抠图」" in str(e), e
    print("== 2. 挑工作流（ID/名字/包含/唯一 + 放大类·无图输入·解析失败·无候选·多候选） OK")


def test_matting_injection():
    """注入：步数 → 采样器；提示词 → 正向节点；留空 → 不动（沿用工作流原值）。"""
    roles, _ = wp.parse_workflow(MATTING)
    rec = {"id": 1, "name": "抠图 Qwen2.1", "wf_json": json.dumps(MATTING), "roles": roles}
    wf, prompt = _load_matting_base(rec)
    assert wf["name"] == "抠图 Qwen2.1" and "_matting_roles" in wf

    # 不写 = 工作流原值（25 步 / 原提示词）
    assert prompt["478:458"]["inputs"]["steps"] == 25
    assert prompt["478:474"]["inputs"]["prompt"] == PROMPT_TEXT

    # 写步数（覆盖）+ 写提示词（覆盖）
    assert _apply_matting_steps(prompt, roles, 40) == 40
    assert prompt["478:458"]["inputs"]["steps"] == 40
    assert _apply_matting_prompt(prompt, roles, "去掉背景，输出 PNG（透明）") is True
    assert prompt["478:474"]["inputs"]["prompt"] == "去掉背景，输出 PNG（透明）"
    # 负向没被动（只写正向）
    assert prompt["478:474"]["inputs"]["negative_prompt"] == ""
    # 输入图可写（文件名形式，与出图链路同一约定）
    assert wb.set_image_node(prompt, roles["image_node"], "up.png")
    assert prompt["477"]["inputs"]["image"] == "up.png"

    # 工作流没暴露采样器 / 正向 → 不报错，返回 None / False（调用方沿用原值）
    assert _apply_matting_steps(prompt, {}, 30) is None
    assert _apply_matting_prompt(prompt, {}, "x") is False
    assert _apply_matting_steps(prompt, {"sampler": "不存在的节点"}, 30) is None
    print("== 3. 注入（步数 / 提示词 / 留空沿用原值 / 无注记不报错） OK")


def test_store_and_pick():
    """真实入库链路：上传 → 挑中 → 注入，并确认资源节点没被动过。"""
    import tempfile

    from workflow_store import WorkflowStore

    st = WorkflowStore(Path(tempfile.mkdtemp()))
    wf_id, roles, err = st.import_json("抠图 Qwen2.1", json.dumps(MATTING), "qwen2.1抠图.json")
    assert err is None and wf_id, (wf_id, err)
    assert roles["kind"] == "img2img" and roles["positive"]["default_text"] == PROMPT_TEXT
    rows = st.list_all()
    assert rows and rows[0]["parse_ok"] is True and rows[0]["roles"]["image_node"] == "477"

    # 真实 store 交给挑选逻辑；配置里绑它 + 填了步数与提示词
    me = _FakePlugin()
    me.workflow_store = st
    me._cfg_all = {"matting": {"base_id": str(wf_id), "steps": 40,
                               "prompt": "去掉背景，输出 PNG"}}
    rec = _resolve_matting_base(me, "")
    assert rec["id"] == wf_id and rec.get("wf_json"), rec
    # ★前端预填就靠这两个：工作流里的步数 / 提示词
    assert rec["roles"]["sampler_defaults"]["steps"] == 25
    assert rec["roles"]["positive"]["default_text"] == PROMPT_TEXT

    _, prompt = _load_matting_base(rec)
    assert _apply_matting_steps(prompt, rec["roles"], 40) == 40
    assert _apply_matting_prompt(prompt, rec["roles"], "去掉背景，输出 PNG") is True
    assert wb.set_image_node(prompt, rec["roles"]["image_node"], "uploaded.png")
    assert prompt["477"]["inputs"]["image"] == "uploaded.png"
    assert prompt["478:458"]["inputs"]["steps"] == 40
    assert prompt["478:474"]["inputs"]["prompt"] == "去掉背景，输出 PNG"
    # 资源节点（模型 / CLIP / VAE）不该被动过
    assert prompt["478:451"]["inputs"]["unet_name"] == "qwen_image_2.1_int8_convrot.safetensors"
    assert prompt["478:453"]["inputs"]["clip_name"] == "qwen3vl_8b_int8_convrot.safetensors"
    assert prompt["478:454"]["inputs"]["vae_name"] == "qwen_image_2.1_vae_bf16.safetensors"
    # 按名字点名也能挑中
    assert _resolve_matting_base(me, "抠图 Qwen2.1")["id"] == wf_id
    print("== 4. 真实入库 + 挑选 + 注入（step/prompt/图；资源节点未被动过） OK")


def test_pre_scale_no_upscale():
    """预处理缩放 + 「不放大」（v7.7.14）：小图保持原尺寸，大图照原目标缩小。"""
    roles, errors = wp.parse_workflow(MATTING_SCALE)
    assert not errors, errors
    # 注记：顺流找到的预处理缩放节点（带单位与换算后的 MP）
    ps = roles["pre_scale"]
    assert ps["node"] == "484" and ps["field"] == "megapixels", ps
    assert ps["class_type"] == "ImageScaleToTotalPixels"
    assert ps["unit"] == "mp" and ps["default_mp"] == 1.25
    assert ps["steps_field"] == "resolution_steps" and ps["steps_default"] == 32
    # 没有这类节点的工作流不该有这个注记
    r2, _ = wp.parse_workflow(MATTING)
    assert "pre_scale" not in r2, r2

    # ① 小图 + 不放大 → 目标 ≈ 输入像素（不再拉到 1.25MP）
    p = json.loads(json.dumps(MATTING_SCALE))
    mp, note = _apply_matting_pixel_target(p, roles, 512, 512, True)
    assert abs(mp - 0.262144) < 1e-6, (mp, note)
    assert "不放大" in note and "0.26MP" in note, note
    assert p["484"]["inputs"]["megapixels"] == round(0.262144, 4), p["484"]["inputs"]
    # ② 小图但略小于目标（800×1200 = 0.96MP）→ 写 input（不放大）
    p = json.loads(json.dumps(MATTING_SCALE))
    assert abs(_apply_matting_pixel_target(p, roles, 800, 1200, True)[0] - 0.96) < 1e-6
    assert p["484"]["inputs"]["megapixels"] == 0.96
    # ③ 大图 / 中等图（≥ 工作流原目标）→ 无需改写：工作流自己会缩（显存保护不变）
    for w, h in ((3000, 2000), (1024, 1536)):
        p = json.loads(json.dumps(MATTING_SCALE))
        assert _apply_matting_pixel_target(p, roles, w, h, True) == (None, ""), (w, h)
        assert p["484"]["inputs"]["megapixels"] == 1.25, p["484"]["inputs"]
    # ④ 关掉「不放大」→ 完全不碰（沿用工作流原值）
    p = json.loads(json.dumps(MATTING_SCALE))
    assert _apply_matting_pixel_target(p, roles, 512, 512, False) == (None, "")
    assert p["484"]["inputs"]["megapixels"] == 1.25
    # ⑤ 尺寸读不出来（无 Pillow / 读失败）→ 不写、不报错（照工作流原值跑）
    p = json.loads(json.dumps(MATTING_SCALE))
    assert _apply_matting_pixel_target(p, roles, 0, 0, True) == (None, "")
    # ⑥ 工作流没有该注记 → 不写、不报错
    r3, _ = wp.parse_workflow(MATTING)
    p = json.loads(json.dumps(MATTING))
    assert _apply_matting_pixel_target(p, r3, 512, 512, True) == (None, "")
    # ⑦ 像素单位字段（max_total_pixels=1048576 → 1.05MP）：换算与回写都要对（且写整数）
    px_roles = {"pre_scale": {"node": "9", "field": "max_total_pixels", "default": 1048576,
                              "unit": "px", "default_mp": 1.048576,
                              "class_type": "ImageScaleToTotalPixels"}}
    p = {"9": {"class_type": "ImageScaleToTotalPixels",
               "inputs": {"max_total_pixels": 1048576}}}
    mp, _ = _apply_matting_pixel_target(p, px_roles, 512, 512, True)
    assert abs(mp - 0.262144) < 1e-6, mp
    assert p["9"]["inputs"]["max_total_pixels"] == 262144, p["9"]["inputs"]
    assert isinstance(p["9"]["inputs"]["max_total_pixels"], int), p["9"]["inputs"]
    # 真实导出（logs/ 下有就跑）
    real = ROOT / "logs" / "qwen2.1抠图-预处理.json"
    if real.is_file():
        r4, e4 = wp.parse_workflow(json.loads(real.read_text(encoding="utf-8")))
        assert not e4 and r4["pre_scale"]["node"] == "484", (e4, r4.get("pre_scale"))
    print("== 5. 预处理缩放 + 不放大（小图保持 / 大图缩小 / 关掉不碰 / px 单位 / 无节点） OK")


def test_cmd_alias_and_strip():
    """指令别名（/抠图 及其别名）与参数剥离。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "cmd_matting")
    names: set[str] = set()
    for dec in fn.decorator_list:
        for arg in getattr(dec, "args", []) or []:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
        for kw in getattr(dec, "keywords", []) or []:
            if kw.arg == "alias" and isinstance(kw.value, ast.Set):
                names |= {e.value for e in kw.value.elts if isinstance(e, ast.Constant)}
    assert "抠图" in names, sorted(names)
    assert {"抠像", "去背景", "去背"} <= names, sorted(names)

    ALIASES = ("抠像", "去背景", "去背")
    cases = [
        ("/抠图", ""),
        ("/抠图 ", ""),
        ("/抠图 抠图 Qwen2.1", "抠图 Qwen2.1"),     # 可选：临时点名工作流
        ("/抠像", ""),
        ("/去背景", ""),
        ("/去背 去背景", "去背景"),
    ]
    for raw, exp in cases:
        got = _strip_command(raw, "抠图", ALIASES)
        assert got == exp, f"{raw!r}: 期望 {exp!r}，实际 {got!r}"
    print(f"== 6. 指令别名（抠图/抠像/去背景/去背）+ 参数剥离（{len(cases)} 组） OK")


if __name__ == "__main__":
    test_parse_matting_workflow()
    test_resolve_matting_base()
    test_matting_injection()
    test_store_and_pick()
    test_pre_scale_no_upscale()
    test_cmd_alias_and_strip()
    print("抠图（解析 / 挑工作流 / 注入 / 不放大 / 入库 / 指令）全部通过")
