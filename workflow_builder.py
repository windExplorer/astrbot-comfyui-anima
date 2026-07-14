"""工作流 JSON 的加载与节点字段注入（提示词 / 宽高 / LoRA）。"""

import json
import os


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
    for lora in loras_config or []:
        name = (lora.get("name") or "").strip()
        load_node = lora.get("load_node")
        if not name or not load_node:
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
