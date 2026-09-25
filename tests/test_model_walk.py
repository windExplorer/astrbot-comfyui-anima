"""主模回溯：穿透「模型修饰节点」，别把 ModelSamplingAuraFlow 当成底模（v7.7.3）。

**现象**：基础工作流解析出来的模型是 `ModelSamplingAuraFlow`，模型文件空 → 底模关联不上、
「模型未识别」。

**根因**：`_walk_up_model` 以前是「遇到第一个非 LoRA 节点就停」，而
`UNETLoader → ModelSamplingAuraFlow → KSampler` 里采样器的 model 输入直接来自修饰节点，
于是修饰节点被当成了主模；顺带它**上游的 LoRA 也全被漏收**（内置 LoRA 保护失效）。

**修法**：只要节点还带着 `model` 上游连线就继续穿透，直到真正的加载器（`_is_base_model_loader`）
或再往上没有 `model` 连线（那它就是链路源头）。途经的非 LoRA 节点记进
`roles["model_patch_nodes"]`（`model_patch_class` 给第一个）留档。

跑法：python tests/test_model_walk.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import workflow_parser as wp        # noqa: E402


def _wf(model_nodes: dict, model_tail: str, clip_src: str = "30") -> dict:
    """拼一个最小可解析的文生图工作流：模型链末端由采样器 model 输入指向 model_tail。"""
    p = {
        "20": {"class_type": "CLIPTextEncode",
               "inputs": {"text": "1girl", "clip": [clip_src, 1]}},
        "21": {"class_type": "CLIPTextEncode",
               "inputs": {"text": "bad", "clip": [clip_src, 1]}},
        "22": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024}},
        "23": {"class_type": "KSampler", "inputs": {
            "seed": 1, "steps": 20, "cfg": 3.5, "sampler_name": "euler",
            "scheduler": "normal", "denoise": 1.0,
            "model": [model_tail, 0], "positive": ["20", 0],
            "negative": ["21", 0], "latent_image": ["22", 0]}},
        "29": {"class_type": "SaveImage",
               "inputs": {"filename_prefix": "x", "images": ["23", 0]}},
        "30": {"class_type": "CLIPLoader",
               "inputs": {"clip_name": "t5xxl.safetensors", "type": "flux"}},
    }
    p.update(model_nodes)
    return p


def test_auraflow_patch_not_treated_as_base():
    """本 bug 原始形态：UNETLoader → ModelSamplingAuraFlow → KSampler。"""
    nodes = {
        "10": {"class_type": "UNETLoader",
               "inputs": {"unet_name": "flux1-dev.safetensors", "weight_dtype": "default"}},
        "11": {"class_type": "ModelSamplingAuraFlow",
               "inputs": {"shift": 1.73, "model": ["10", 0]}},
    }
    roles, errs = wp.parse_workflow(_wf(nodes, "11"))
    assert not errs, errs
    assert roles["model_src"] == "10", roles["model_src"]        # 必须是加载器，不是修饰节点
    assert roles["model_class"] == "UNETLoader", roles["model_class"]
    assert roles["model_file"] == "flux1-dev.safetensors", roles["model_file"]
    assert roles["model_patch_nodes"] == ["11"], roles["model_patch_nodes"]
    assert roles["model_patch_class"] == "ModelSamplingAuraFlow"
    print("== 1. ModelSamplingAuraFlow 不再被当成底模（模型文件可识别） OK")


def test_patch_and_lora_both_traversed():
    """Checkpoint → Lora → ModelSamplingFlux → 采样器：两者都要穿透，LoRA 也要收到。"""
    nodes = {
        "10": {"class_type": "CheckpointLoaderSimple",
               "inputs": {"ckpt_name": "anima-pencil-xl.safetensors"}},
        "11": {"class_type": "LoraLoader",
               "inputs": {"lora_name": "turbo.safetensors", "strength_model": 0.8,
                          "strength_clip": 0.8, "model": ["10", 0], "clip": ["10", 1]}},
        "12": {"class_type": "ModelSamplingFlux",
               "inputs": {"max_shift": 1.15, "base_shift": 0.5,
                          "width": 1024, "height": 1024, "model": ["11", 0]}},
    }
    roles, errs = wp.parse_workflow(_wf(nodes, "12"))
    assert not errs, errs
    assert roles["model_src"] == "10" and roles["model_class"] == "CheckpointLoaderSimple"
    assert roles["model_file"] == "anima-pencil-xl.safetensors"
    assert roles["lora_nodes"] == ["11"], roles["lora_nodes"]        # 修饰节点上游的 LoRA 也收到
    assert roles["model_patch_nodes"] == ["12"], roles["model_patch_nodes"]
    assert {b["node"] for b in roles["builtin_loras"]} == {"11"}, roles["builtin_loras"]
    # 锚点仍是主模加载节点（LoRA 注入从它之后开始，顺序正确）
    assert roles["model_src"] == "10"
    print("== 2. 修饰节点 + 上游 LoRA 都穿透（内置 LoRA 不再漏收） OK")


def test_plain_and_lora_only_chains():
    """回归：直连加载器 / 只有 LoRA 链的老形态结果不变。"""
    plain = {"10": {"class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": "a.safetensors"}}}
    roles, errs = wp.parse_workflow(_wf(plain, "10"))
    assert not errs and roles["model_src"] == "10" and roles["model_file"] == "a.safetensors"
    assert roles["model_patch_nodes"] == [] and roles["model_patch_class"] is None

    lora_only = {
        "10": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "b.safetensors"}},
        "11": {"class_type": "LoraLoaderModelOnly",
               "inputs": {"lora_name": "l.safetensors", "strength_model": 1.0,
                          "model": ["10", 0]}},
    }
    roles2, errs2 = wp.parse_workflow(_wf(lora_only, "11"))
    assert not errs2 and roles2["model_src"] == "10"
    assert roles2["lora_nodes"] == ["11"] and roles2["model_patch_nodes"] == []
    print("== 3. 直连加载器 / 仅 LoRA 链（老形态）行为不变 OK")


def test_custom_loader_and_broken_chain():
    """自定义加载器（类名不含 unet/checkpoint）作源头可用；断环要报错而不是死循环。"""
    custom = {
        "10": {"class_type": "DiffusionModelLoader",
               "inputs": {"model_name": "custom-model.safetensors"}},
    }
    roles, errs = wp.parse_workflow(_wf(custom, "10"))
    assert not errs, errs
    assert roles["model_src"] == "10" and roles["model_file"] == "custom-model.safetensors"

    # 自环：model 指向自己 → 不能死循环，且应报「无法回溯」
    cyc = {"10": {"class_type": "UNETLoader", "inputs": {"unet_name": "x.safetensors"}},
           "11": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["11", 0]}}}
    roles2, errs2 = wp.parse_workflow(_wf(cyc, "11"))
    assert roles2 is None and any("回溯" in e for e in errs2), errs2
    print("== 4. 自定义加载器兜底 + 自环不死循环 OK")


def test_reparse_salvages_legacy_records():
    """存量记录修复路径：旧解析结果（主模=修饰节点）重解析后应恢复正确模型。

    用户库里已入库的记录，roles_json 是**修复前**写进去的（model_class=ModelSamplingAuraFlow、
    model_file 空），所以光改解析器不够 —— 需要在「基础工作流」页点一次「重解析」
    （handler 还会顺带补一次底模自动关联）。这里模拟这条修复路径。
    """
    import json
    import sqlite3
    import tempfile

    from workflow_store import WorkflowStore

    st = WorkflowStore(Path(tempfile.mkdtemp()))
    nodes = {
        "10": {"class_type": "UNETLoader",
               "inputs": {"unet_name": "flux1-dev.safetensors", "weight_dtype": "default"}},
        "11": {"class_type": "ModelSamplingAuraFlow",
               "inputs": {"shift": 1.73, "model": ["10", 0]}},
    }
    wf_id, _roles, err = st.import_json("flux 图", json.dumps(_wf(nodes, "11")), "flux.json")
    assert err is None and wf_id, (wf_id, err)

    # 把 roles_json 改回「修复前」的错误结果（模拟存量记录）
    legacy = {"kind": "t2i", "model_src": "11", "model_class": "ModelSamplingAuraFlow",
              "model_file": "", "lora_nodes": [], "extra_lora_nodes": [], "builtin_loras": []}
    conn = sqlite3.connect(str(st.db_path))
    conn.execute("UPDATE base_workflows SET roles_json=? WHERE id=?",
                 (json.dumps(legacy, ensure_ascii=False), int(wf_id)))
    conn.commit()
    conn.close()
    assert st.get(wf_id)["roles"]["model_class"] == "ModelSamplingAuraFlow"   # 旧数据就长这样
    assert st.get(wf_id)["basemodel_id"] == 0

    # 重解析 → 模型识别修正（底模自动关联由 webui handler 补，这里只验证解析结果）
    roles, err2 = st.reparse(wf_id)
    assert err2 is None, err2
    assert roles["model_class"] == "UNETLoader" and roles["model_src"] == "10"
    assert roles["model_file"] == "flux1-dev.safetensors"
    assert st.get(wf_id)["roles"]["model_patch_class"] == "ModelSamplingAuraFlow"
    print("== 5. 存量记录重解析后可恢复正确模型（修复路径） OK")


if __name__ == "__main__":
    test_auraflow_patch_not_treated_as_base()
    test_patch_and_lora_both_traversed()
    test_plain_and_lora_only_chains()
    test_custom_loader_and_broken_chain()
    test_reparse_salvages_legacy_records()
    print("主模回溯（穿透模型修饰节点）全部通过")
