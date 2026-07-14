"""工作流 JSON 的加载与节点字段注入（提示词 / 宽高 / LoRA / 种子）。"""

import json
import os
import random


def load_workflow(path: str | None = None, json_text: str | None = None) -> dict:
    """从文件或文本加载 ComfyUI API 格式工作流（即 prompt 字典）。"""
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    if json_text and json_text.strip():
        return json.loads(json_text)
    raise ValueError("未配置工作流（workflow_name 或 workflow_json 均为空）")


def _get_node(prompt: dict, node_id) -> dict | None:
    """按节点 ID 获取节点（兼容字符串 / 整数键）。"""
    if node_id in (None, ""):
        return None
    node = prompt.get(node_id)
    if node is not None:
        return node
    node = prompt.get(str(node_id))
    if node is not None:
        return node
    try:
        return prompt.get(int(node_id))
    except (ValueError, TypeError):
        return None


def set_text_node(
    prompt: dict, node_id, input_name: str, value: str
) -> bool:
    """在指定节点的输入框写入文本（正向 / 负向提示词）。"""
    node = _get_node(prompt, node_id)
    if node is None:
        return False
    node.setdefault("inputs", {})[input_name] = value
    return True


def set_number_node(
    prompt: dict, node_id, input_name: str, value: int
) -> bool:
    """在指定节点的输入框写入数值（宽 / 高）。"""
    node = _get_node(prompt, node_id)
    if node is None:
        return False
    node.setdefault("inputs", {})[input_name] = value
    return True


def find_node_by_class(prompt: dict, class_type: str):
    """返回工作流中第一个匹配 class_type 的节点 ID（找不到返回 None）。"""
    for nid, node in prompt.items():
        if isinstance(node, dict) and (node.get("class_type") or "") == class_type:
            return nid
    return None


def randomize_seed(prompt: dict, seed: int | None = None) -> list[int]:
    """为工作流中所有采样器节点随机化（或固定）种子。

    默认 ComfyUI 工作流会写死一个固定 seed，导致同一提示词每次出图完全一致。
    本函数在每次提交前随机设置 seed（或按传入的 seed 固定），覆盖 KSampler 的
    `seed` 与 KSamplerAdvanced 的 `noise_seed` 输入。返回实际被设置的种子列表。
    """
    seeds: list[int] = []
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        cls = (node.get("class_type") or "").lower()
        if "sampler" not in cls:
            continue
        inputs = node.setdefault("inputs", {})
        # 每个采样器节点各自取一个种子（固定时统一使用传入值）
        s = seed if seed is not None else random.randint(0, 2**63 - 1)
        for field in ("seed", "noise_seed"):
            if field in inputs:
                inputs[field] = s
                seeds.append(s)
    return seeds


def apply_loras(
    prompt: dict,
    loras_config: list[dict],
    active_map: dict[str, float | None] | None = None,
) -> list[str]:
    """注入 LoRA。

    active_map 为 None 时按各 LoRA 的 enabled 默认值决定是否启用；
    active_map 不为 None 时，键为要启用的 LoRA 名称，值为权重
    （None 表示使用配置中的默认权重），未列出的 LoRA 一律禁用。
    返回实际启用的 LoRA 名称列表。
    """
    enabled_names: list[str] = []
    # 预收集工作流中的 LoraLoader 节点（按字典顺序），load_node 为空时按顺序自动分配
    loader_nodes = [
        nid
        for nid, node in prompt.items()
        if isinstance(node, dict) and (node.get("class_type") or "").endswith("LoraLoader")
    ]
    auto_idx = 0
    for lora in loras_config or []:
        name = (lora.get("name") or "").strip()
        load_node = lora.get("load_node")
        if not name:
            continue
        if not load_node:
            # 文本配置未指定 load_node：自动分配一个 LoraLoader 节点
            if auto_idx < len(loader_nodes):
                load_node = loader_nodes[auto_idx]
                auto_idx += 1
            else:
                continue
        node = _get_node(prompt, load_node)
        if node is None:
            continue
        inputs = node.setdefault("inputs", {})
        model_name = (lora.get("model_name") or "").strip()
        model_input = lora.get("model_input", "lora_name")
        if model_name:
            inputs[model_input] = model_name

        if active_map is None:
            active = bool(lora.get("enabled", False))
            weight = float(lora.get("weight", 1.0))
        else:
            if name not in active_map:
                active = False
                weight = 0.0
            else:
                active = True
                w = active_map[name]
                weight = float(lora.get("weight", 1.0)) if w is None else float(w)

        s_model = lora.get("strength_model_input", "strength_model")
        s_clip = lora.get("strength_clip_input", "strength_clip")
        if active:
            inputs[s_model] = weight
            inputs[s_clip] = weight
            enabled_names.append(name)
        else:
            # 强度置 0 实现“禁用”（仍保留 lora_name 以免节点报错）
            inputs[s_model] = 0.0
            inputs[s_clip] = 0.0
    return enabled_names


def collect_keyword_loras(
    loras_config: list[dict], text: str
) -> set[str]:
    """根据提示词中的关键词，自动收集应启用的 LoRA 名称。"""
    matched: set[str] = set()
    lower = (text or "").lower()
    for lora in loras_config or []:
        raw = lora.get("keywords") or []
        if isinstance(raw, str):
            kws = [k.strip() for k in raw.split(",") if k.strip()]
        else:
            kws = [str(k).strip() for k in raw]
        name = (lora.get("name") or "").strip()
        if not name or not kws:
            continue
        for kw in kws:
            kw = kw.lower()
            if kw and kw in lower:
                matched.add(name)
                break
    return matched
