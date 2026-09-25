"""多参考图图生图（v7.7.35）。

覆盖：
1) 解析器：TextEncodeQwenImage21 的 images.image_N 动态输入 → roles.multi_image 标识；
2) workflow_builder.prepare_multi_image_refs：单图保持 / 多图扩节点（复制 LoadImage+缩放链、
   接线 images.image_N、缩放参数原样拷贝）；
3) find_multi_image_node；
4) 选择器（_apply_ref_selector）与 /device 硬件名精简（_short_hw_name）单测。

跑法：python tests/test_multi_ref.py
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import workflow_builder as wb  # noqa: E402
import workflow_parser as wp  # noqa: E402


def _load_main_funcs(want: set[str]) -> dict:
    """从 main.py 摘类方法（静态）单独执行（main 依赖 astrbot 运行时装不了）。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    ns = {"re": re}
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in want:
            exec(compile(ast.get_source_segment(src, node), "<x>", "exec"), ns)
    return ns


# 与 logs/qwen2.1图生图.json 等价的最小结构（多参图生图：条件节点 images.image_N）
QWEN_MULTI = {
    "461": {"class_type": "SaveImageAdvanced",
            "inputs": {"filename_prefix": "Qwen_image_2.1", "images": ["459:457", 0]}},
    "470": {"class_type": "LoadImage", "inputs": {"image": "ref.jpg"}},
    "477": {"class_type": "ImageScaleToTotalPixels",
            "inputs": {"upscale_method": "lanczos", "megapixels": 1.25,
                       "resolution_steps": 32, "image": ["470", 0]}},
    "459:451": {"class_type": "UNETLoader",
                "inputs": {"unet_name": "qwen_image_2.1_int8_convrot.safetensors",
                           "weight_dtype": "default"}},
    "459:453": {"class_type": "CLIPLoader",
                "inputs": {"clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                           "type": "qwen_image", "device": "default"}},
    "459:454": {"class_type": "VAELoader",
                "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
    "459:456": {"class_type": "EmptyLatentImage",
                "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "459:457": {"class_type": "VAEDecode",
                "inputs": {"samples": ["459:458", 0], "vae": ["459:454", 0]}},
    "459:458": {"class_type": "KSampler",
                "inputs": {"seed": 208267376819620, "steps": 25, "cfg": 1,
                           "sampler_name": "euler", "scheduler": "simple", "denoise": 1,
                           "model": ["459:469", 0], "positive": ["459:474", 0],
                           "negative": ["459:474", 1], "latent_image": ["459:468", 0]}},
    "459:468": {"class_type": "ComfySwitchNode",
                "inputs": {"switch": False, "on_false": ["459:474", 2],
                           "on_true": ["459:456", 0]}},
    "459:469": {"class_type": "QwenImage21Cache",
                "inputs": {"device": "auto", "dtype": "default", "model": ["459:478", 0]}},
    "459:474": {"class_type": "TextEncodeQwenImage21",
                "inputs": {"prompt": "将图1转成动漫风格", "negative_prompt": "",
                           "resolution": 0, "clip": ["459:453", 0],
                           "images.image_1": ["477", 0], "vae": ["459:454", 0]}},
    "459:478": {"class_type": "TESpeedQwenImage21",
                "inputs": {"attention": "kitchen_int8", "step_cache": "te_predictor",
                           "reuse_threshold": 0.06, "start_percent": 0, "end_percent": 0,
                           "predictor_error_limit": 0.08, "verbose": True,
                           "model": ["459:451", 0]}},
}


def test_parse_multi_image():
    """解析器：识别多参考图工作流（multi_image 标识），kind 不变。"""
    real = ROOT / "logs" / "qwen2.1图生图.json"
    src = json.loads(real.read_text(encoding="utf-8")) if real.is_file() else QWEN_MULTI
    roles, errors = wp.parse_workflow(src)
    assert not errors, errors
    assert roles["kind"] == "img2img", roles.get("kind")
    assert roles["image_node"] == "470", roles["image_node"]
    mi = roles.get("multi_image")
    assert mi and mi["node"] == "459:474" and mi["count"] == 1, mi
    assert mi["inputs"] == ["images.image_1"], mi
    assert roles.get("pre_scale", {}).get("node") == "477", roles.get("pre_scale")
    print("== 1. 解析器 multi_image 标识（qwen2.1 图生图） OK")


def test_prepare_multi_image_refs():
    """扩节点：单图不动 / 三图复制两路（LoadImage+缩放链）并接线。"""
    roles, _ = wp.parse_workflow(json.loads(json.dumps(QWEN_MULTI)))
    # ---- 单图：直接写现有 LoadImage，不新增节点 ----
    prompt = json.loads(json.dumps(QWEN_MULTI))
    ids = wb.prepare_multi_image_refs(prompt, roles, ["a.png"])
    assert ids == ["470"], ids
    assert prompt["470"]["inputs"]["image"] == "a.png"
    assert "images.image_2" not in prompt["459:474"]["inputs"]

    # ---- 三图：复制 470/477 两次，接 images.image_2/3，缩放参数原样 ----
    prompt = json.loads(json.dumps(QWEN_MULTI))
    ids = wb.prepare_multi_image_refs(prompt, roles, ["a.png", "b.png", "c.webp"])
    assert len(ids) == 3 and all(ids), ids
    assert prompt["470"]["inputs"]["image"] == "a.png"
    cond = prompt["459:474"]["inputs"]
    assert cond["images.image_1"] == ["477", 0], cond
    assert "images.image_2" in cond and "images.image_3" in cond, cond
    # 新路链：image_2 → 缩放副本（megapixels=1.25）→ LoadImage 副本(b.png)
    s2 = cond["images.image_2"][0]
    assert prompt[s2]["class_type"] == "ImageScaleToTotalPixels"
    assert prompt[s2]["inputs"]["megapixels"] == 1.25
    assert prompt[s2]["inputs"]["upscale_method"] == "lanczos"
    l2 = prompt[s2]["inputs"]["image"][0]
    assert prompt[l2]["class_type"] == "LoadImage"
    assert prompt[l2]["inputs"]["image"] == "b.png"
    assert ids[1] == l2
    # 第三路同构
    s3 = cond["images.image_3"][0]
    l3 = prompt[s3]["inputs"]["image"][0]
    assert prompt[l3]["inputs"]["image"] == "c.webp" and ids[2] == l3
    # 新节点 ID 不与现有冲突，且原节点没被改坏
    assert s2 != s3 and l2 != l3 and s2 not in QWEN_MULTI and l2 not in QWEN_MULTI
    assert prompt["477"]["inputs"]["megapixels"] == 1.25
    assert prompt["470"]["inputs"]["image"] == "a.png"
    # find_multi_image_node 复核：条件节点现有 3 路
    mi = wb.find_multi_image_node(prompt)
    assert mi and mi["node"] == "459:474" and mi["count"] == 3, mi
    print("== 2. 多参考图扩节点（复制链/接线/参数拷贝） OK")


def test_find_multi_image_node_none():
    """普通工作流（无 images.image_N）→ None。"""
    assert wb.find_multi_image_node({"1": {"class_type": "KSampler", "inputs": {}}}) is None
    roles, _ = wp.parse_workflow({"1": {"class_type": "KSampler", "inputs": {}}})
    assert (roles or {}).get("multi_image") is None
    print("== 3. find_multi_image_node（无多图工作流） OK")


def test_ref_selector():
    """--图 选择器：单张/多张/范围/倒数/越界忽略/去重保序。"""
    ns = _load_main_funcs({"_apply_ref_selector"})
    f = lambda *a: ns["_apply_ref_selector"](None, *a)  # 实例方法，self 传 None
    imgs = ["a", "b", "c", "d", "e"]
    assert f(imgs, "2") == ["b"]
    assert f(imgs, "2,4") == ["b", "d"]
    assert f(imgs, "2-4") == ["b", "c", "d"]
    assert f(imgs, "-1") == ["e"]
    assert f(imgs, "-2") == ["d"]
    assert f(imgs, "2,2") == ["b"]                      # 去重
    assert f(imgs, "9") == imgs                         # 全越界 → 原样
    assert f(imgs, None) == imgs                        # 未传
    assert f(imgs, "2-9") == ["b", "c", "d", "e"]       # 范围超尾截到末尾
    print("== 4. 参考图选择器 OK")


def test_short_hw_name():
    """硬件名精简：去厂商前缀/垃圾尾巴。"""
    ns = _load_main_funcs({"_short_hw_name"})
    f = ns["_short_hw_name"]
    assert f("12th Gen Intel(R) Core(TM) i7-12700F", "cpu") == "i7-12700F"
    assert f("AMD Ryzen 9 7950X 16-Core Processor", "cpu") == "Ryzen 9 7950X"
    assert f("NVIDIA GeForce RTX 4090D", "gpu") == "RTX 4090D"
    assert f("AMD Radeon RX 7900 XTX", "gpu") == "RX 7900 XTX"
    print("== 5. 硬件名精简 OK")


if __name__ == "__main__":
    test_parse_multi_image()
    test_prepare_multi_image_refs()
    test_find_multi_image_node_none()
    test_ref_selector()
    test_short_hw_name()
    print("多参考图图生图全部通过")
