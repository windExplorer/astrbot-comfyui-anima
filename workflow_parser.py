"""基础工作流解析器（v7.0.0）。

把 ComfyUI API 格式（prompt dict）解析成「角色注记」：
正/负向编码节点+输入框名、宽高节点、LoRA 链、放大链、清理显存节点、
保存节点（类型/格式/质量能力）、采样器默认值、图生图能力、底模源。

解析即校验：不满足「单采样器、单主模、四类节点可唯一定位」直接拒绝入库
（返回 errors 非空），绝不静默带病入库。

设计约定（与用户确认的 v7.0.0 方案一致）：
- 只收 API/prompt 格式（UI 格式 nodes/links 拒绝并提示用「导出(API)」）；
- 锚点/角色判定全部从采样器与保存节点的连线反推，与节点类名尽量解耦；
- 解析产物（roles）存库，出图运行时按注记定点改写，不做零件级重建。
"""

from __future__ import annotations

import re


# 文本编码节点可写字段（按优先级探测第一个字符串型输入框）
_TEXT_FIELD_PREFS = ("text", "prompt", "positive", "instruction", "caption")
# 清理显存节点特征（透传，仅副作用）：Easy-Use 的 easy cleanGpuUsed
_CLEANUP_HINT = "cleangpu"
# 图加载节点特征（图生图）
_IMAGE_LOADER_HINTS = (
    "loadimage", "loadimagefrompath", "loadimagev2", "loadimagewithresize",
    "loadimagemasked", "imageloader",
)


def _ct(node: dict) -> str:
    return (node.get("class_type") or "").strip().lower()


def _link(v) -> tuple[str, int] | None:
    """输入值是否为节点连线 [id, slot]，是则返回 (id_str, slot)。"""
    if isinstance(v, list) and len(v) == 2 and not isinstance(v[0], (dict, list)):
        try:
            return str(v[0]), int(v[1])
        except (TypeError, ValueError):
            return None
    return None


def _find_text_field(node: dict, exclude: tuple = (), prefer_neg: bool = False) -> str | None:
    """在文本编码节点里找可写入的输入框名。

    prefer_neg：该节点同时充当负向编码（如 TextEncodeQwenImage21 同一节点出
    正/负两个输出）时，优先选名字含 neg 的字段，避免正负向写到同一个框。
    """
    inputs = node.get("inputs") or {}
    str_fields = [
        f for f, v in inputs.items()
        if isinstance(v, str) and f not in exclude and f not in ("class_type", "_meta")
    ]
    if prefer_neg:
        negs = [f for f in str_fields if "neg" in f.lower()]
        if negs:
            return negs[0]
    else:
        # 正向方向：negative_* 字段绝不当正向写入口（Qwen 一体编码器实测踩坑）
        str_fields = [f for f in str_fields if "neg" not in f.lower()]
    for pref in _TEXT_FIELD_PREFS:
        if pref in str_fields:
            return pref
    return str_fields[0] if str_fields else None


def _resolve_text_write_node(nodes: dict, link: tuple[str, int] | None,
                             prefer_neg: bool = False, exclude: tuple = ()) -> tuple[str, str] | None:
    """从采样器的 positive/negative 连线出发，定位「可写入的文本节点 + 输入框名」。

    大多数工作流连线直接指向 CLIPTextEncode（有 text 字面量输入框），一步命中；
    Qwen v1 这类「easy positive → 编码器 prompt 输入」的结构，编码器本身没有可写的
    字符串输入框，需要沿线上溯到文本源节点（easy positive 的 positive 输入框）。
    """
    cur = link
    seen: set = set()
    while cur and cur[0] in nodes and cur[0] not in seen:
        seen.add(cur[0])
        node = nodes[cur[0]]
        field = _find_text_field(node, exclude=exclude, prefer_neg=prefer_neg)
        if field:
            return cur[0], field
        # 无字符串输入框：沿偏好顺序找第一条上游连线继续走
        inputs = node.get("inputs") or {}
        nxt = None
        for pref in ("text", "prompt", "positive", "string", "instruction", "caption"):
            if pref in inputs:
                nxt = _link(inputs[pref])
                if nxt:
                    break
        if not nxt:
            for _f, v in inputs.items():
                nxt = _link(v)
                if nxt:
                    break
        cur = nxt
    return None


def _is_sampler(node: dict) -> bool:
    """采样器判定：类名含 sampler 且同时有 model/positive 输入。"""
    inputs = node.get("inputs") or {}
    return "sampler" in _ct(node) and "model" in inputs and "positive" in inputs


def _is_lora(node: dict) -> bool:
    # LoraLoader / LoraLoaderModelOnly / LoraLoaderBlock 等都按「包含」判定
    # （endswith 会漏掉 LoraLoaderModelOnly——mmh1.6 内置 Turbo LoRA 实测踩坑）
    return "loraloader" in _ct(node)


def _is_base_model_loader(node: dict) -> bool:
    """主模加载节点：CheckpointLoader / UNETLoader / UnetLoaderGGUF 等。"""
    ct = _ct(node)
    return "loader" in ct and ("checkpoint" in ct or "unet" in ct)


def _is_image_loader(node: dict) -> bool:
    ct = _ct(node)
    return any(h in ct for h in _IMAGE_LOADER_HINTS)


def _is_cleanup(node: dict) -> bool:
    return _CLEANUP_HINT in _ct(node)


def _walk_up_model(prompt: dict, start: str) -> tuple[str | None, list[str]]:
    """从采样器 model 输入沿 LoRA 链向上回溯到主模源。返回 (主模节点ID, 途经LoRA节点列表)。"""
    loras: list[str] = []
    cur = start
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        node = prompt.get(cur)
        if not isinstance(node, dict):
            return None, loras
        if _is_lora(node):
            loras.append(cur)
            nxt = _link((node.get("inputs") or {}).get("model"))
            cur = nxt[0] if nxt else None
            continue
        return cur, loras
    return None, loras


def parse_workflow(prompt: dict) -> tuple[dict | None, list[str]]:
    """解析工作流。返回 (roles, errors)：errors 非空即拒绝入库。"""
    errors: list[str] = []
    if not isinstance(prompt, dict) or not prompt:
        return None, ["JSON 不是 ComfyUI API 格式（应为 {节点ID: {class_type, inputs}} 对象）"]
    # UI 格式守卫
    sample = next(iter(prompt.values()))
    if isinstance(sample, dict) and ("class_type" not in sample) and ("type" in sample):
        return None, ["检测到 UI 格式（nodes/links）。请在 ComfyUI 用「导出（API）」另存后再上传。"]

    nodes = {k: v for k, v in prompt.items() if isinstance(v, dict)}

    # ---- 采样器：必须恰好 1 个 ----
    samplers = [nid for nid, n in nodes.items() if _is_sampler(n)]
    if len(samplers) != 1:
        errors.append(
            f"要求恰好 1 个采样器节点（含 model/positive 输入），实际 {len(samplers)} 个：{samplers or '无'}。"
            "多阶段工作流暂不支持，请拆分后再传。"
        )
        return None, errors
    sampler_id = samplers[0]
    sampler = nodes[sampler_id]
    s_inputs = sampler.get("inputs") or {}

    # ---- 正向 / 负向编码节点 + 字段名 ----
    roles: dict = {"sampler": sampler_id}
    pos_link = _link(s_inputs.get("positive"))
    neg_link = _link(s_inputs.get("negative"))
    if not pos_link:
        errors.append("无法从采样器的 positive 输入定位正向提示词节点。")
    else:
        found = _resolve_text_write_node(nodes, pos_link)
        if not found:
            errors.append("无法定位正向提示词的可写入文本节点（沿 positive 连线及其上游均无文本输入框）。")
        else:
            pnode = nodes[found[0]]
            roles["positive"] = {"node": found[0], "field": found[1], "class_type": pnode.get("class_type")}
    if not neg_link:
        errors.append("无法从采样器的 negative 输入定位负向提示词节点。")
    else:
        # 仅当正/负向落在同一节点（Qwen 系一体编码器）时，负向字段才排除正向字段
        same_first = bool(roles.get("positive", {}).get("node") == neg_link[0])
        found = _resolve_text_write_node(
            nodes, neg_link, prefer_neg=True,
            exclude=((roles["positive"]["field"],) if same_first else ()),
        )
        if not found:
            errors.append("无法定位负向提示词的可写入文本节点。")
        else:
            nnode = nodes[found[0]]
            roles["negative"] = {"node": found[0], "field": found[1], "class_type": nnode.get("class_type")}

    # ---- 宽高节点（latent_image 上游，最好为 EmptyLatentImage）----
    lat_link = _link(s_inputs.get("latent_image"))
    if not lat_link or lat_link[0] not in nodes:
        errors.append("无法从采样器的 latent_image 输入定位空 latent 节点（宽高注入位置）。")
    else:
        lnode = nodes[lat_link[0]]
        l_inputs = lnode.get("inputs") or {}
        wf_ = l_inputs.get("width")
        hf_ = l_inputs.get("height")
        roles["latent"] = {
            "node": lat_link[0],
            "class_type": lnode.get("class_type"),
            "width_field": "width" if isinstance(wf_, (int, float, str)) else "width",
            "height_field": "height" if isinstance(hf_, (int, float, str)) else "height",
            "size_locked": not (isinstance(wf_, (int, float)) or wf_ is None),
        }
        # 记录默认宽高（字面量时）
        try:
            roles["latent"]["default_width"] = int(wf_) if isinstance(wf_, (int, float)) else None
            roles["latent"]["default_height"] = int(hf_) if isinstance(hf_, (int, float)) else None
        except (TypeError, ValueError):
            roles["latent"]["default_width"] = None
            roles["latent"]["default_height"] = None

    # ---- 保存节点：优先 SaveImageExtended ----
    save_nodes = [nid for nid, n in nodes.items() if "saveimage" in _ct(n)]
    if not save_nodes:
        errors.append("未找到保存节点（SaveImage / SaveImageExtended / SaveImageAdvanced 等，需含 images 输入）。")
    else:
        save_id = next((nid for nid in save_nodes if "extended" in _ct(nodes[nid])), save_nodes[0])
        snode = nodes[save_id]
        s_in = snode.get("inputs") or {}
        roles["save"] = {
            "node": save_id,
            "class_type": snode.get("class_type"),
            "has_quality": "quality" in s_in,
            "has_output_ext": "output_ext" in s_in,
            "quality": s_in.get("quality") if isinstance(s_in.get("quality"), (int, float)) else None,
            "output_ext": s_in.get("output_ext") if isinstance(s_in.get("output_ext"), str) else None,
        }

    # ---- 主模源：沿 model 链上溯（穿透 LoRA），全图主模加载节点必须 ≤1 ----
    model_link = _link(s_inputs.get("model"))
    base_id, lora_chain = (None, [])
    if model_link:
        base_id, lora_chain = _walk_up_model(nodes, model_link[0])
    if not base_id or base_id not in nodes:
        errors.append("无法从采样器的 model 输入回溯到底模加载节点（UNETLoader/CheckpointLoader 等）。")
    else:
        roles["model_src"] = base_id
        roles["model_class"] = nodes[base_id].get("class_type")
        # 模型文件名（UNETLoader.unet_name / CheckpointLoader.ckpt_name / GGUF 的 unet_name 等）：
        # 供「底模（模型族）」自动关联用（v7.0.3）
        _min = nodes[base_id].get("inputs") or {}
        roles["model_file"] = next(
            (
                str(_min[k]).strip()
                for k in ("unet_name", "ckpt_name", "model_name", "gguf_name", "model")
                if isinstance(_min.get(k), str) and str(_min[k]).strip()
            ),
            "",
        )
    base_loaders = [nid for nid, n in nodes.items() if _is_base_model_loader(n)]
    if len(base_loaders) > 1:
        errors.append(f"检测到 {len(base_loaders)} 个主模加载节点（{base_loaders}）。要求单主模，多阶段/多管线工作流暂不支持。")
    roles["lora_nodes"] = lora_chain
    roles["extra_lora_nodes"] = [nid for nid, n in nodes.items() if _is_lora(n) and nid not in lora_chain]
    # 内置 LoRA 明细（节点 + 当前模型名）：供前端展示与运行时保护（不可删、可禁用）
    roles["builtin_loras"] = [
        {
            "node": nid,
            "name": ((nodes[nid].get("inputs") or {}).get("lora_name") or ""),
        }
        for nid in sorted(set(roles["lora_nodes"]) | set(roles["extra_lora_nodes"]))
    ]

    # ---- CLIP 源（LoRA 完整注入用）----
    clip_srcs: set = set()
    for nid, n in nodes.items():
        if "clip" in _ct(n) and ("encode" in _ct(n) or "text" in _ct(n)):
            cv = _link((n.get("inputs") or {}).get("clip"))
            if cv:
                clip_srcs.add(cv[0])
    roles["clip_src"] = sorted(clip_srcs)[0] if clip_srcs else None

    # ---- 图生图能力 ----
    image_loaders = [nid for nid, n in nodes.items() if _is_image_loader(n)]
    roles["image_node"] = image_loaders[0] if image_loaders else None
    roles["kind"] = "img2img" if image_loaders else "t2i"

    # ---- 放大链：从保存节点 images 向上溯源（穿过清理透传节点）----
    # v7.0.1：同时记录整条链路（chain_nodes，含 Split/Join alpha 处理节点）与
    # 图像源（image_source，如 VAEDecode），供「绕过放大」安全改接——
    # alpha 工作流里放大节点被 Join 消费，只删放大两节点会让 Join 输入悬空。
    roles["upscale"] = None
    if save_nodes:
        cur = _link((nodes[roles.get("save", {}).get("node", "")].get("inputs") or {}).get("images"))
        seen = set()
        loader_id = apply_id = None
        chain_nodes: list = []
        image_source = None
        while cur and cur[0] in nodes and cur[0] not in seen:
            seen.add(cur[0])
            n = nodes[cur[0]]
            ct = _ct(n)
            if "upscalemodelloader" in ct:
                loader_id = cur[0]
            elif "imageupscale" in ct:
                apply_id = cur[0]
                # UpscaleModelLoader 不在图像链上，而是放大节点的 upscale_model 输入
                um_link = _link((n.get("inputs") or {}).get("upscale_model"))
                if um_link and um_link[0] in nodes and "upscalemodelloader" in _ct(nodes[um_link[0]]):
                    loader_id = um_link[0]
            chain_nodes.append(cur[0])
            # 继续向上：放大节点走 image；清理透传走 anything/image；其它停止
            nxt = _link((n.get("inputs") or {}).get("image")) or _link((n.get("inputs") or {}).get("anything"))
            if not nxt:
                break
            cur = nxt
        if cur and cur[0] in nodes:
            image_source = cur[0]
        if loader_id or apply_id:
            um = nodes.get(loader_id or "", {})
            roles["upscale"] = {
                "loader": loader_id,
                "apply": apply_id,
                "model_name": (um.get("inputs") or {}).get("model_name") if isinstance(um, dict) else None,
                "chain_nodes": chain_nodes,
                "image_source": image_source,
            }
    # 清理显存节点清单
    roles["cleanup_nodes"] = [nid for nid, n in nodes.items() if _is_cleanup(n)]

    # ---- 采样器默认参数 ----
    steps = cfg = denoise = None
    s_in = s_inputs
    try:
        steps = int(s_in["steps"]) if "steps" in s_in else None
    except (TypeError, ValueError):
        pass
    try:
        cfg = float(s_in["cfg"]) if "cfg" in s_in else None
    except (TypeError, ValueError):
        pass
    try:
        denoise = float(s_in["denoise"]) if "denoise" in s_in else None
    except (TypeError, ValueError):
        pass
    roles["sampler_defaults"] = {
        "steps": steps,
        "cfg": cfg,
        "denoise": denoise,
        "sampler_name": s_in.get("sampler_name") if isinstance(s_in.get("sampler_name"), str) else None,
        "scheduler": s_in.get("scheduler") if isinstance(s_in.get("scheduler"), str) else None,
        "seed": s_in.get("seed") if isinstance(s_in.get("seed"), (int, float)) else None,
    }
    roles["subgraph_ids"] = [k for k in nodes if ":" in str(k)]

    # 汇总缺失
    for key, label in (("positive", "正向"), ("negative", "负向"), ("latent", "宽高"), ("save", "保存")):
        if key not in roles:
            errors.append(f"未能定位{label}节点。")
    if errors:
        return None, errors
    return roles, []
