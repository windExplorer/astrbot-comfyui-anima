# -*- coding: utf-8 -*-
"""MOSS-Image 提示词规范测试（v7.7.96）。

覆盖：
1) Layout 意图判定（意图词 / 同时点到 ≥2 个角色卡角色）；
2) Layout JSON 识别（决定后续追加/扩写全部跳过）；
3) bbox 缩放（1024 规划画布 → 实际尺寸，含越界钳制与坏输入容错）；
4) 规范文档存在且含必备章节（skills/moss-image/moss-image.md）；
5) _conf_schema.json 的 prompt_skill 选项含 moss-image；
6) 底模库默认项含 Moss-Image。

跑法：python tests/test_moss_prompt.py
"""

import ast
import json
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from workflow_parser import parse_workflow  # noqa: E402


def _load_static(name: str):
    """从 main.py 摘一个静态方法出来单独执行（main.py 依赖 astrbot，本地装不了）。

    方法体引用的模块级常量（如 _MOSS_LAYOUT_INTENT_RE）一并提取进同一命名空间。
    """
    src = Path(REPO, "main.py").read_text(encoding="utf-8-sig")   # main.py 带 BOM
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    seg = ast.get_source_segment(src, fn) or ""
    body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
    ns: dict = {"re": re, "json": json}
    # 模块级正则/常量（方法体引用的那些）先注入
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id.isupper():
                    try:
                        exec(compile(ast.Module(body=[node], type_ignores=[]),
                                     "<moss-const>", "exec"), ns)
                    except Exception:
                        pass
    exec(textwrap_dedent(body), ns)  # noqa: S102
    return ns[name]


def textwrap_dedent(s: str) -> str:
    import textwrap
    return textwrap.dedent(s)


# 最小 MOSS Layout JSON（双人格子，结构与官方/实测案例一致）
LAYOUT_SAMPLE = {
    "summary": {
        "canvas": "multi_panel",
        "meta": ["manga", "four_panel"],
        "caption": "Four-panel manga. Aemeath on the left, Denia on the right in every panel. "
                   "Keep each character's official hairstyle, eye color, accessories and "
                   "outfit exactly consistent across all panels.",
    },
    "items": [
        {"label": "character", "caption": "Left: Aemeath, long pink hair, amber eyes.",
         "bbox": [-494, -494, -58, -58]},
        {"label": "character", "caption": "Right: Denia, pale pink-lavender hair, blue eyes.",
         "bbox": [-494, 24, -58, 494]},
        {"label": "text", "caption": "Rounded balloon, tail to Aemeath.",
         "text": "最后一块，归我啦！", "bbox": [-480, -494, -350, -180]},
    ],
    "bbox_coord_space": "abs_centralized",
    "bbox_format": "yxyx",
}


def test_layout_intent():
    fn = _load_static("_is_moss_layout_request")
    # 意图词命中 → Layout
    for t in ("画一个四格漫画", "来个双人对话场景", "做张海报，标题写夏日特惠",
              "comic panel with two girls", "分镜：三格", "气泡对白"):
        assert fn(t), t
    # 同时点到两个角色卡角色（没说漫画也要 Layout——防串角色）
    assert fn("爱弥斯和达妮娅在咖啡馆抢蛋糕", ("爱弥斯", "达妮娅", "清宵"))
    assert fn("爱弥斯 达妮娅", ("爱弥斯", "达妮娅"))
    # 单角色 / 无意图词 → 不走 Layout
    assert not fn("画一个少女", ("爱弥斯",))
    assert not fn("1girl, solo, cat ears", ())
    assert not fn("", ())
    print("== 1. MOSS Layout 意图判定 OK")


def test_looks_moss_layout():
    fn = _load_static("_looks_moss_layout")
    assert fn(json.dumps(LAYOUT_SAMPLE, ensure_ascii=False))
    assert fn(json.dumps(LAYOUT_SAMPLE))                       # 无缩进也行
    # 非 Layout：普通文本 / 无 items / 无 bbox 的 JSON
    assert not fn("1girl, masterpiece")
    assert not fn(json.dumps({"caption": "hi"}))
    assert not fn(json.dumps({"items": [{"label": "text", "text": "x"}]}))
    assert not fn("")
    print("== 2. MOSS Layout JSON 识别 OK")


def test_rescale_bbox():
    fn = _load_static("_rescale_moss_layout")
    # 1024 规划画布 → 816×1216：x×0.797、y×1.188
    out = fn(json.dumps(LAYOUT_SAMPLE, ensure_ascii=False), 816, 1216)
    d = json.loads(out)
    bbs = [it["bbox"] for it in d["items"]]
    assert bbs[0] == [-587, -394, -69, -46], bbs[0]     # -494*(1216/1024)=-586.6→-587；-494*0.797=-394
    # 全部在 [-608,608]×[-408,408] 内且 min<max
    for bb in bbs:
        y0, x0, y1, x1 = bb
        assert -608 <= y0 < y1 <= 608 and -408 <= x0 < x1 <= 408, bb
    # 同尺寸 → 原样返回（不折腾）
    same = fn(json.dumps(LAYOUT_SAMPLE), 1024, 1024)
    assert json.loads(same) == LAYOUT_SAMPLE
    # 坏输入容错：非 JSON / 非 Layout / bbox 缺项 → 原样
    assert fn("not json", 800, 800) == "not json"
    assert fn(json.dumps({"a": 1}), 800, 800) == json.dumps({"a": 1})
    bad = json.dumps({"items": [{"bbox": [1, 2]}]})
    assert fn(bad, 800, 800) == bad
    # 非数值 bbox 不动
    bad2 = json.dumps({"items": [{"bbox": ["a", "b", "c", "d"]}]})
    assert fn(bad2, 800, 800) == bad2
    print("== 3. bbox 缩放（1024 → 实际尺寸 / 钳制 / 容错） OK")


def test_skill_doc():
    p = Path(REPO, "skills", "moss-image", "moss-image.md")
    assert p.is_file(), "规范文档缺失：skills/moss-image/moss-image.md"
    txt = p.read_text(encoding="utf-8")
    for must in ("bbox_format", "abs_centralized", "yxyx", "multi_panel",
                 "一致性", "character", "speech", "Euler", "shift",
                 "nsfw, worst quality, bad anatomy, watermark, jpeg artifact"):
        assert must in txt, f"规范文档缺少要素: {must}"
    # 示例 JSON 必须能被解析（规范里的范本不能本身是错的）。
    # 结构模板块含 y0/x0 占位符（非合法 JSON）属预期，跳过；其余块必须全部可解析。
    blocks = txt.split("```json")[1:]
    parsed = 0
    for b in blocks:
        seg = b.split("```", 1)[0]
        try:
            d = json.loads(seg)
        except Exception:
            if "y0" in seg:
                continue        # 结构模板块（占位符），允许
            raise AssertionError(f"规范文档里的 JSON 示例不合法: {seg[:120]}")
        parsed += 1
        assert d["bbox_coord_space"] == "abs_centralized" and d["bbox_format"] == "yxyx"
        assert d["summary"]["canvas"] == "multi_panel"
        for it in d["items"]:
            bb = it["bbox"]
            assert len(bb) == 4 and bb[0] < bb[2] and bb[1] < bb[3], bb
            if it["label"] == "text":
                assert it.get("text"), "text 项必须带 text"
    assert parsed >= 1, "规范文档里没有可解析的完整示例"
    print("== 4. 规范文档存在且示例 JSON 合法 OK")


def test_schema_and_basemodel():
    schema = json.loads(Path(REPO, "_conf_schema.json").read_text(encoding="utf-8"))

    def _find_key(obj, key):
        """递归查找含指定键的节点（schema 层级深，别写死路径）。"""
        if isinstance(obj, dict):
            if key in obj and isinstance(obj[key], dict):
                return obj[key]
            for v in obj.values():
                hit = _find_key(v, key)
                if hit is not None:
                    return hit
        return None

    ps = _find_key(schema, "prompt_skill")
    assert ps and "moss-image" in (ps.get("options") or []), ps
    from basemodel_store import DEFAULT_BASEMODELS
    names = {b["name"] for b in DEFAULT_BASEMODELS}
    assert "Moss-Image" in names, names
    moss = next(b for b in DEFAULT_BASEMODELS if b["name"] == "Moss-Image")
    assert "moss" in moss["keywords"]
    assert moss["prompt_style"] == "natural" and moss["priority_lang"] == "英文"
    print("== 5. schema 选项 + 底模默认项（Moss-Image） OK")


def test_parse_still_ok():
    """经典/自定义链解析不受本次改动影响（回归）。"""
    from workflow_store import WorkflowStore
    import tempfile
    # 拿 WF_STD 同款最小样例：KSampler + CLIPTextEncode
    wf = {
        "9": {"class_type": "UNETLoader", "inputs": {"unet_name": "m.safetensors"}},
        "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "q.safetensors"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "pos", "clip": ["1", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "neg", "clip": ["1", 0]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 816, "height": 1216}},
        "6": {"class_type": "KSampler", "inputs": {
            "seed": 1, "steps": 10, "cfg": 1, "sampler_name": "euler",
            "scheduler": "simple", "denoise": 1, "model": ["9", 0],
            "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x", "images": ["7", 0]}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["9", 0]}},
    }
    roles, errs = parse_workflow(wf)
    assert not errs, errs
    assert roles["positive"]["node"] == "3" and roles["kind"] == "t2i"
    print("== 6. 解析器回归（经典结构不受影响） OK")


if __name__ == "__main__":
    test_layout_intent()
    test_looks_moss_layout()
    test_rescale_bbox()
    test_skill_doc()
    test_schema_and_basemodel()
    test_parse_still_ok()
    print("\nMOSS-Image 提示词规范测试全部通过（6 组）")
