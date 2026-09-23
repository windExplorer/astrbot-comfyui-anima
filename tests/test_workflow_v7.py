# -*- coding: utf-8 -*-
"""v7.0.0 基础工作流体系回归测试：解析器 / 存储 / 运行时节点手术。

直接 `python tests/test_workflow_v7.py` 运行，退出码即结论。
依赖 tests/ 同级的真实工作流样例（仓库 logs/ 下不属于测试资产，这里自带精简样例）。
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


wb = _load_mod("wb_v7", os.path.join(REPO, "workflow_builder.py"))
from workflow_parser import parse_workflow  # noqa: E402
from workflow_store import WorkflowStore  # noqa: E402

# ------------------------------------------------------------------ #
# 精简样例（API 格式）
# ------------------------------------------------------------------ #
WF_STD = {  # 标准单阶段：CLIPTextEncode + 内置放大链 + 清理节点（结构同 mmh1.6_turbo）
    "9": {"class_type": "UNETLoader", "inputs": {"unet_name": "m.safetensors"}},
    "13": {"class_type": "LoraLoaderModelOnly",
           "inputs": {"lora_name": "Turbo-ANIMA-v2.9.safetensors", "strength_model": 1, "model": ["9", 0]}},
    "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen.safetensors", "type": "qwen_image"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "pos", "clip": ["1", 0]}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "neg", "clip": ["1", 0]}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 816, "height": 1216, "batch_size": 1}},
    "6": {"class_type": "KSampler", "inputs": {
        "seed": 1, "steps": 10, "cfg": 1, "sampler_name": "euler", "scheduler": "simple",
        "denoise": 1, "model": ["13", 0], "positive": ["3", 0], "negative": ["4", 0],
        "latent_image": ["5", 0]}},
    "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["2", 0]}},
    "2": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_vae.safetensors"}},
    "11": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["12", 0], "image": ["7", 0]}},
    "12": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4x-ClearRealityV1.pth"}},
    "10": {"class_type": "easy cleanGpuUsed", "inputs": {"anything": ["11", 0]}},
    "8": {"class_type": "SaveImageExtended", "inputs": {
        "filename_prefix": "mmh", "images": ["10", 0], "output_ext": ".webp", "quality": 99}},
}

WF_MULTI_SAMPLER = {  # 双采样器 → 必须拒绝
    **WF_STD,
    "18": {"class_type": "KSampler", "inputs": {
        "seed": 2, "steps": 4, "cfg": 1, "sampler_name": "euler", "scheduler": "simple",
        "denoise": 1, "model": ["9", 0], "positive": ["3", 0], "negative": ["4", 0],
        "latent_image": ["5", 0]}},
}

WF_QWEN_SUBGRAPH = {  # 子图冒号 ID + 一体编码器（正/负同节点）
    "473": {"class_type": "SaveImageExtended", "inputs": {"filename_prefix": "q", "images": ["471:457", 0],
            "output_ext": ".webp", "quality": 99}},
    "471:451": {"class_type": "UNETLoader", "inputs": {"unet_name": "q.safetensors"}},
    "471:452": {"class_type": "TextEncodeQwenImage21",
                "inputs": {"prompt": "正", "negative_prompt": "", "resolution": 1024, "clip": ["471:453", 0]}},
    "471:453": {"class_type": "CLIPLoader", "inputs": {"clip_name": "q3.safetensors", "type": "qwen_image"}},
    "471:454": {"class_type": "VAELoader", "inputs": {"vae_name": "vae.safetensors"}},
    "471:456": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
    "471:457": {"class_type": "VAEDecode", "inputs": {"samples": ["471:458", 0], "vae": ["471:454", 0]}},
    "471:458": {"class_type": "KSampler", "inputs": {
        "seed": 3, "steps": 25, "cfg": 1, "sampler_name": "euler", "scheduler": "simple", "denoise": 1,
        "model": ["471:451", 0], "positive": ["471:452", 0], "negative": ["471:452", 1],
        "latent_image": ["471:456", 0]}},
}

WF_NO_UPSCALE = {k: v for k, v in WF_QWEN_SUBGRAPH.items() if k != "473"} | {
    "473": {"class_type": "SaveImage", "inputs": {"filename_prefix": "q", "images": ["471:457", 0]}},
}


def test_parse_std():
    roles, errs = parse_workflow(WF_STD)
    assert not errs, errs
    assert roles["positive"] == {"node": "3", "field": "text", "class_type": "CLIPTextEncode"}
    assert roles["negative"]["node"] == "4" and roles["negative"]["field"] == "text"
    assert roles["latent"]["node"] == "5" and roles["latent"]["default_width"] == 816
    assert roles["save"]["node"] == "8" and roles["save"]["has_quality"] and roles["save"]["has_output_ext"]
    assert roles["upscale"]["loader"] == "12" and roles["upscale"]["apply"] == "11"
    assert roles["upscale"]["model_name"] == "4x-ClearRealityV1.pth"
    assert roles["cleanup_nodes"] == ["10"]
    assert roles["model_src"] == "9" and roles["lora_nodes"] == ["13"]
    assert roles["kind"] == "t2i"
    assert any(b["name"] == "Turbo-ANIMA-v2.9.safetensors" for b in roles["builtin_loras"])
    print("== 1. 标准工作流解析 OK")


def test_parse_reject_multi_sampler():
    roles, errs = parse_workflow(WF_MULTI_SAMPLER)
    assert errs and any("采样器" in e for e in errs), errs
    print("== 2. 双采样器拒绝 OK ->", errs[0][:40])


def test_parse_qwen_subgraph():
    roles, errs = parse_workflow(WF_QWEN_SUBGRAPH)
    assert not errs, errs
    assert roles["positive"] == {"node": "471:452", "field": "prompt", "class_type": "TextEncodeQwenImage21"}
    assert roles["negative"]["node"] == "471:452" and roles["negative"]["field"] == "negative_prompt"
    assert roles["model_src"] == "471:451" and roles["subgraph_ids"]
    print("== 3. 子图冒号 ID + 一体编码器解析 OK")


def test_store():
    tmp = Path(tempfile.mkdtemp())
    store = WorkflowStore(tmp)
    wf_id, roles, err = store.import_json("std", json.dumps(WF_STD), "std.json")
    assert err is None and wf_id and roles, err
    rec = store.get(wf_id)
    assert rec["parse_ok"] is True
    assert (tmp / "workflow_uploads" / rec["stored_file"]).exists()  # 文件关联
    # 同名覆盖
    wf_id2, _, err2 = store.import_json("std", json.dumps(WF_STD), "std.json")
    assert wf_id2 == wf_id and len(store.list_all()) == 1
    # 拒绝入库
    _, _, err3 = store.import_json("bad", json.dumps(WF_MULTI_SAMPLER), "bad.json")
    assert err3 and "采样器" in err3 and len(store.list_all()) == 1
    # 删除保护 + 删除
    assert "被以下出图工作流引用" in (store.delete(wf_id, referenced_names=["日常"]) or "")
    assert store.delete(wf_id) is None
    # 元数据更新
    wf_id3, _, _ = store.import_json("m", json.dumps(WF_QWEN_SUBGRAPH), "q.json")
    assert store.update_meta(wf_id3, {"civitai_url": "https://civitai.com/models/1", "image": "c.png"}) is None
    assert store.get(wf_id3)["civitai_url"] == "https://civitai.com/models/1"
    print("== 4. WorkflowStore 入库/文件关联/同名覆盖/拒绝/删除保护/元数据 OK")


def test_surgery():
    # 绕过内置放大链（标准链：保存←清理←放大←解码）
    p = json.loads(json.dumps(WF_STD))
    roles, _ = parse_workflow(p)
    up = roles["upscale"]
    assert wb.bypass_upscale(
        p, roles["save"]["node"], chain_nodes=up["chain_nodes"],
        image_source=up["image_source"], upscale_loader=up["loader"],
    )
    assert p["8"]["inputs"]["images"] == ["7", 0]
    assert "11" not in p and "12" not in p and "10" not in p
    # 注入放大链（无放大链工作流）
    p = json.loads(json.dumps(WF_NO_UPSCALE))
    roles, _ = parse_workflow(p)
    assert roles["upscale"] is None
    apply_id = wb.inject_upscale(p, roles["save"]["node"], "4x-UltraSharp.pth")
    assert apply_id and p[roles["save"]["node"]]["inputs"]["images"] == [apply_id, 0]
    loader_id = p[apply_id]["inputs"]["upscale_model"][0]
    assert p[loader_id]["inputs"]["model_name"] == "4x-UltraSharp.pth"
    # 注入清理显存
    cid = wb.inject_cleanup(p, roles["save"]["node"])
    assert cid and p[roles["save"]["node"]]["inputs"]["images"] == [cid, 0]
    # 保存节点替换
    p = json.loads(json.dumps(WF_QWEN_SUBGRAPH))
    roles, _ = parse_workflow(p)
    old = roles["save"]["node"]
    src = p[old]["inputs"]["images"]
    new_id = wb.replace_save_node(p, old, ".webp", 95)
    assert new_id and old not in p and p[new_id]["inputs"]["images"] == src
    assert p[new_id]["inputs"]["output_ext"] == ".webp" and p[new_id]["inputs"]["quality"] == 95
    # 采样器覆盖
    p = json.loads(json.dumps(WF_STD))
    roles, _ = parse_workflow(p)
    assert wb.set_sampler_node(p, roles["sampler"], "dpmpp_2m", "karras")
    assert p[roles["sampler"]]["inputs"]["sampler_name"] == "dpmpp_2m"
    print("== 5. 运行时手术（绕过/注入放大、清理、保存替换、采样器覆盖） OK")


def test_bypass_alpha_chain():
    """alpha 工作流（Split→放大→Join→清理→保存）：绕过须整链删除并接回 VAEDecode。"""
    p = json.loads(json.dumps(WF_STD))
    # 改造成 alpha 链：7(VAEDecode) → 485 Split → 484 放大 → 486 Join → 10 清理 → 8 保存
    p["485"] = {"class_type": "SplitImageWithAlpha", "inputs": {"image": ["7", 0]}}
    p["11"]["inputs"]["image"] = ["485", 0]
    p["486"] = {"class_type": "JoinImageWithAlpha",
                "inputs": {"image": ["11", 0], "alpha": ["485", 1]}}
    p["10"]["inputs"]["anything"] = ["486", 0]
    roles, errs = parse_workflow(p)
    assert not errs, errs
    up = roles["upscale"]
    assert up["apply"] == "11" and up["loader"] == "12"
    assert up["image_source"] == "7"
    assert set(up["chain_nodes"]) == {"10", "486", "11", "485", "7"}
    assert wb.bypass_upscale(
        p, roles["save"]["node"], chain_nodes=up["chain_nodes"],
        image_source=up["image_source"], upscale_loader=up["loader"],
    )
    assert p["8"]["inputs"]["images"] == ["7", 0], "须改接回 VAEDecode（保留 alpha）"
    for gone in ("10", "486", "11", "485", "12"):
        assert gone not in p, f"{gone} 应被整链删除"
    print("== 6. alpha 工作流绕过（整链删除+接回 VAEDecode） OK")


if __name__ == "__main__":
    test_parse_std()
    test_parse_reject_multi_sampler()
    test_parse_qwen_subgraph()
    test_store()
    test_surgery()
    test_bypass_alpha_chain()
    print("\nv7.0.0 基础工作流体系测试全部通过")


if __name__ == "__main__":
    test_parse_std()
    test_parse_reject_multi_sampler()
    test_parse_qwen_subgraph()
    test_store()
    test_surgery()
    print("\nv7.0.0 基础工作流体系测试全部通过")
