# -*- coding: utf-8 -*-
"""v8.14.1：ComfyUI 画布直接保存的 UI 格式自动转 API 回归测试。

直接 `python tests/test_ui_format.py` 运行，退出码即结论。
样例按 Z-Image Turbo 官方模板（UNETLoader + CLIPLoader lumina2 +
ModelSamplingAuraFlow + EmptySD3LatentImage + KSampler）精简构造。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from workflow_parser import is_ui_format, parse_workflow, ui_to_api  # noqa: E402
from workflow_store import WorkflowStore  # noqa: E402


def _ui_workflow(with_named: bool = True) -> dict:
    def node(nid, typ, links_in=(), widgets=(), named=None):
        n = {"id": nid, "type": typ, "mode": 0, "inputs": [], "outputs": [],
             "widgets_values": list(widgets)}
        if with_named and named is not None:
            n["widgets_values_named"] = named
        for name, lid in links_in:
            n["inputs"].append({"name": name, "type": "*", "link": lid})
        return n

    nodes = [
        node(9, "SaveImage", [("images", 80)], ["ComfyUI"], {"filename_prefix": "ComfyUI"}),
        node(62, "CLIPLoader", (), ["clip_l.safetensors", "lumina2", "default"],
             {"clip_name": "clip_l.safetensors", "type": "lumina2", "device": "default"}),
        node(66, "UNETLoader", (), ["z_image_turbo_bf16.safetensors", "default"],
             {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"}),
        node(67, "CLIPTextEncode", [("clip", 79)], ["POS"], {"text": "POS"}),
        node(71, "CLIPTextEncode", [("clip", 81)], ["NEG"], {"text": "NEG"}),
        node(68, "EmptySD3LatentImage", (), [1024, 1024, 1],
             {"width": 1024, "height": 1024, "batch_size": 1}),
        node(69, "ModelSamplingAuraFlow", [("model", 72)], [3, "flow"],
             {"shift": 3, "sampling": "flow"}),
        node(70, "KSampler",
             [("model", 75), ("positive", 76), ("negative", 82), ("latent_image", 78)],
             [42, "fixed", 8, 1, "res_multistep", "simple", 1],
             {"seed": 42, "control_after_generate": "fixed", "steps": 8, "cfg": 1,
              "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1}),
        node(63, "VAELoader", (), ["taesd"], {"vae_name": "taesd"}),
        node(65, "VAEDecode", [("samples", 73), ("vae", 74)], []),
    ]
    links = [
        [72, 66, 0, 69, 0, "MODEL"], [75, 69, 0, 70, 0, "MODEL"],
        [76, 67, 0, 70, 1, "CONDITIONING"], [82, 71, 0, 70, 2, "CONDITIONING"],
        [78, 68, 0, 70, 3, "LATENT"], [73, 70, 0, 65, 0, "LATENT"],
        [74, 63, 0, 65, 1, "VAE"], [80, 65, 0, 9, 0, "IMAGE"],
        [79, 62, 0, 67, 0, "CLIP"], [81, 62, 0, 71, 0, "CLIP"],
    ]
    return {"id": "fake", "version": 0.4, "nodes": nodes, "links": links, "groups": []}


def test_format_detection():
    ui = _ui_workflow()
    assert is_ui_format(ui) is True
    api = ui_to_api(ui)
    assert is_ui_format(api) is False
    assert is_ui_format({"foo": 1}) is False
    assert is_ui_format({"nodes": ["not-a-node"]}) is False
    print("== 1. UI/API 格式识别 OK")


def test_convert_named():
    api = ui_to_api(_ui_workflow())
    assert set(api) == {"9", "62", "63", "65", "66", "67", "68", "69", "70", "71"}
    assert api["70"]["inputs"] == {
        "seed": 42, "control_after_generate": "fixed", "steps": 8, "cfg": 1,
        "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1,
        "model": ["69", 0], "positive": ["67", 0], "negative": ["71", 0],
        "latent_image": ["68", 0],
    }
    assert api["67"]["inputs"]["text"] == "POS"
    assert api["66"]["inputs"]["unet_name"] == "z_image_turbo_bf16.safetensors"
    assert api["9"]["inputs"]["images"] == ["65", 0]
    print("== 2. widgets_values_named 无损转换 OK")


def test_convert_positional():
    api = ui_to_api(_ui_workflow(with_named=False))
    assert api["70"]["inputs"]["sampler_name"] == "res_multistep"
    assert api["68"]["inputs"]["width"] == 1024
    assert api["62"]["inputs"]["clip_name"] == "clip_l.safetensors"
    assert api["69"]["inputs"]["sampling"] == "flow"
    roles, errs = parse_workflow(api)
    assert not errs, errs
    print("== 3. 老版 widgets_values 位置表兜底 OK")


def test_parse_ui_direct():
    ui = _ui_workflow()
    roles, errs = parse_workflow(ui)
    assert not errs, errs
    assert roles["sampler"] == "70"
    assert roles["positive"] == {"node": "67", "field": "text",
                                 "class_type": "CLIPTextEncode", "default_text": "POS"}
    assert roles["negative"]["node"] == "71" and roles["negative"]["default_text"] == "NEG"
    assert roles["latent"]["node"] == "68" and roles["latent"]["default_width"] == 1024
    assert roles["model_src"] == "66" and roles["model_file"] == "z_image_turbo_bf16.safetensors"
    assert roles["model_patch_nodes"] == ["69"]
    assert roles["clip_src"] == "62"
    assert roles["kind"] == "t2i" and roles["save"]["node"] == "9"
    assert roles["sampler_defaults"]["sampler_name"] == "res_multistep"
    # 直接传 UI 与先手动转 API 的结果必须一致
    roles2, errs2 = parse_workflow(ui_to_api(ui))
    assert not errs2 and roles2 == roles
    print("== 4. parse_workflow 直接吃 UI 格式 OK")


def test_bad_format_error():
    roles, errs = parse_workflow({"foo": 1})
    assert roles is None and errs and "既不是" in errs[0], errs
    print("== 5. 非法格式给出清晰错误（不再误报 0 个采样器）OK")


def test_import_normalizes_to_api():
    ui = _ui_workflow()
    with tempfile.TemporaryDirectory() as td:
        store = WorkflowStore(Path(td))
        try:
            wf_id, roles, err, info = store.import_json_ex(
                "z_image_turbo", json.dumps(ui, ensure_ascii=False), "Unsaved Workflow.json"
            )
            assert err is None, err
            assert roles["sampler"] == "70"
            assert info.get("converted_ui") is True
            rec = store.get(wf_id, with_json=True)
            assert rec["file_name"] == "Unsaved Workflow.json"
            # ★核心守卫：入库/落盘的必须是 API 格式，否则运行时 json.loads 后节点手术全废
            stored = json.loads(rec["wf_json"])
            assert is_ui_format(stored) is False
            assert stored["70"]["inputs"]["positive"] == ["67", 0]
            assert json.loads((store.uploads_dir / rec["stored_file"]).read_text(encoding="utf-8")) == stored
            print("== 6. 入库规范化为 API 格式（运行时可用）OK")
        finally:
            if store._conn is not None:
                store._conn.close()


def _moss_ui_latent(missing: bool = False) -> dict:
    """MOSS 系工作流：EmptyFlux2LatentImage + 无 widgets_values_named + link 源 ID 是字符串。"""
    nodes = [
        {"id": 1, "type": "UNETLoader", "inputs": [], "widgets_values": ["moss.safetensors", "default"]},
        {"id": 2, "type": "CLIPLoader", "inputs": [], "widgets_values": ["qwen.safetensors", "moss_image", "default"]},
        {"id": 3, "type": "VAELoader", "inputs": [], "widgets_values": ["ae.safetensors"]},
        {"id": 5, "type": "CLIPTextEncode", "inputs": [{"name": "clip", "link": 2}], "widgets_values": ["POS"]},
        {"id": 6, "type": "CLIPTextEncode", "inputs": [{"name": "clip", "link": 3}], "widgets_values": ["NEG"]},
        {"id": 7, "type": "EmptyFlux2LatentImage", "inputs": [],
         "widgets_values": [] if missing else [1024, 1536, 1]},
        {"id": 8, "type": "KSampler", "inputs": [
            {"name": "model", "link": 1}, {"name": "positive", "link": 4},
            {"name": "negative", "link": 5}, {"name": "latent_image", "link": 6}],
         "widgets_values": [42, "randomize", 32, 3.0, "er_sde", "normal", 1.0]},
        {"id": 9, "type": "VAEDecode", "inputs": [
            {"name": "samples", "link": 7}, {"name": "vae", "link": 8}], "widgets_values": []},
        {"id": 10, "type": "SaveImage", "inputs": [{"name": "images", "link": 9}], "widgets_values": ["moss_ui"]},
    ]
    links = [
        [1, "1", 0, "8", 0, "MODEL"], [2, "2", 0, "5", 0, "CLIP"], [3, "2", 0, "6", 0, "CLIP"],
        [4, "5", 0, "8", 1, "CONDITIONING"], [5, "6", 0, "8", 2, "CONDITIONING"],
        [6, "7", 0, "8", 3, "LATENT"], [7, "8", 0, "9", 0, "LATENT"],
        [8, "3", 0, "9", 1, "VAE"], [9, "9", 0, "10", 0, "IMAGE"],
    ]
    return {"nodes": nodes, "links": links, "version": 0.4}


def test_flux2_latent_pattern_fallback():
    """v8.14.2：EmptyFlux2LatentImage 未收录时会整节点丢 widget，出图报 batch_size 缺失。"""
    ui = _moss_ui_latent()
    api = ui_to_api(ui)
    assert api["7"]["inputs"] == {"width": 1024, "height": 1536, "batch_size": 1}
    roles, errs = parse_workflow(ui)
    assert not errs, errs
    assert roles["latent"]["default_width"] == 1024 and roles["latent"]["default_height"] == 1536
    assert roles["model_file"] == "moss.safetensors"
    # 守卫：widget 全丢时直接拒绝入库，不能"解析通过"骗到出图阶段才炸
    roles2, errs2 = parse_workflow(_moss_ui_latent(missing=True))
    assert roles2 is None and errs2 and "width/height" in "".join(errs2), errs2
    print("== 7. EmptyFlux2LatentImage 模式兜底 + 带病入库守卫 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
    print(f"RESULT: {len(tests)} tests passed")
