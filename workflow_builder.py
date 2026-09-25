"""工作流 JSON 的加载与节点字段注入（提示词 / 宽高 / LoRA / 种子）。"""

import json
import logging
import os
import random
import re

_logger = logging.getLogger(__name__)



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


def set_image_node(
    prompt: dict, node_id, image_tuple: list, input_name: str = "image"
) -> bool:
    """把上传到 ComfyUI 后的图片引用（[filename, subfolder, type]）注入到
    LoadImage 节点的 image 输入，用于图生图（img2img）。"""
    node = _get_node(prompt, node_id)
    if node is None:
        return False
    node.setdefault("inputs", {})[input_name] = image_tuple
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


def set_upscale_model(prompt: dict, node_id, model_name: str) -> str | None:
    """把放大模型加载节点（如 UpscaleModelLoader）里原本的模型名替换为指定值。

    返回被替换掉的旧模型名（用于日志）；节点不存在或找不到可替换的模型名字段时返回 None。
    仅替换工作流里【已存在】的放大模型节点，不注入新节点。
    """
    if not model_name or not str(model_name).strip():
        return None
    node = _get_node(prompt, node_id)
    if node is None:
        return None
    inputs = node.setdefault("inputs", {})
    # 放大模型 Load 节点（UpscaleModelLoader）的字段名固定为 model_name；
    # 若节点 inputs 里没有 model_name，则兜底取第一个字符串类型的字段（兼容非标准放大节点）。
    old = None
    if "model_name" in inputs:
        old = inputs.get("model_name")
        inputs["model_name"] = str(model_name).strip()
    else:
        for k, v in inputs.items():
            if isinstance(v, str) and v.strip():
                old = v
                inputs[k] = str(model_name).strip()
                break
    if old is None:
        return None
    return old


def find_node_by_class(prompt: dict, class_type: str):
    """返回工作流中第一个匹配 class_type 的节点 ID（找不到返回 None）。"""
    for nid, node in prompt.items():
        if isinstance(node, dict) and (node.get("class_type") or "") == class_type:
            return nid
    return None


def find_all_nodes_by_class(prompt: dict, class_type: str) -> list:
    """返回工作流中**所有**匹配 class_type 的节点 ID（按字典顺序，找不到返回空列表）。

    用于多同类节点需要统一改写的场景：例如多 latent 串联工作流里存在两个
    EmptyLatentImage，宽高必须同步设置，否则前后阶段尺寸不一致会导致构图被拉伸。
    """
    return [
        nid
        for nid, node in prompt.items()
        if isinstance(node, dict) and (node.get("class_type") or "") == class_type
    ]


# 图加载类节点的 class_type 前缀/全称（兼容非标准 LoadImage，如 LoadImageFromPath
# / LoadImageV2 / ImageLoader 等）。图生图时用于自动定位参考图要注入的节点。
IMAGE_LOADER_HINTS = (
    "LoadImage",
    "LoadImageFromPath",
    "LoadImageV2",
    "LoadImageWithResize",
    "LoadImageMasked",
    "ImageLoader",
    "LoadImageAndResize",
    "LoadImageFromPathString",
)


def find_image_loader_node(prompt: dict, prefer: str = "LoadImage"):
    """定位工作流里用于图生图的图加载节点 ID。

    策略：
      1. 优先标准 LoadImage；
      2. 否则按 IMAGE_LOADER_HINTS 前缀/全称匹配（兼容大量自定义图加载节点）；
      3. 仍找不到返回 None（调用方应提示用户在 image_node 手动指定）。
    """
    n = find_node_by_class(prompt, prefer)
    if n is not None:
        return n
    for nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type") or ""
        if any(ct == h or ct.startswith(h) for h in IMAGE_LOADER_HINTS):
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


def set_denoise(prompt: dict, denoise: float) -> bool:
    """为工作流中所有采样器节点设置 denoise（降噪幅度/重绘强度）。

    只修改 inputs 中已存在 denoise 字段的采样器节点（如 KSampler）；
    若节点没有 denoise 字段（如某些定制采样器），则跳过不报错。
    返回是否至少修改了一个节点。
    """
    return set_sampler_params(prompt, denoise=denoise)


def get_sampler_defaults(prompt: dict) -> dict:
    """提取工作流中采样器节点的默认参数（steps / cfg / denoise / sampler_name / scheduler）。

    遍历所有采样器类节点（class_type 含 "sampler"），取第一个含对应字段的值。
    返回 {"steps": int|None, "cfg": float|None, "denoise": float|None,
          "sampler_name": str|None, "scheduler": str|None}；
    某个参数在所有采样器节点都缺失时为 None。
    v7.4.8：补上 sampler_name / scheduler —— 出图卡片的「采样器 / 调度器」胶囊
    一直靠它取值，此前漏提导致这两枚胶囊永远不显示。
    """
    out: dict = {
        "steps": None, "cfg": None, "denoise": None,
        "sampler_name": None, "scheduler": None,
    }
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        cls = (node.get("class_type") or "").lower()
        if "sampler" not in cls:
            continue
        inputs = node.get("inputs") or {}
        if out["steps"] is None and "steps" in inputs:
            try:
                out["steps"] = int(inputs["steps"])
            except (ValueError, TypeError):
                pass
        if out["cfg"] is None and "cfg" in inputs:
            try:
                out["cfg"] = float(inputs["cfg"])
            except (ValueError, TypeError):
                pass
        if out["denoise"] is None and "denoise" in inputs:
            try:
                out["denoise"] = float(inputs["denoise"])
            except (ValueError, TypeError):
                pass
        if out["sampler_name"] is None and "sampler_name" in inputs:
            _sn = str(inputs.get("sampler_name") or "").strip()
            if _sn:
                out["sampler_name"] = _sn
        if out["scheduler"] is None and "scheduler" in inputs:
            _sc = str(inputs.get("scheduler") or "").strip()
            if _sc:
                out["scheduler"] = _sc
        if all(out[k] is not None for k in
               ("steps", "cfg", "denoise", "sampler_name", "scheduler")):
            break
    return out


def set_sampler_params(prompt: dict, steps=None, cfg=None, denoise=None) -> bool:
    """为工作流中所有采样器节点设置采样参数（steps / cfg / denoise）。

    只修改 inputs 中**已存在**对应字段的采样器节点（如 KSampler）；
    节点缺该字段（如某些定制采样器）则跳过不报错。参数传 None 表示不修改。
    返回是否至少修改了一个节点。
    """
    changed = False
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        cls = (node.get("class_type") or "").lower()
        if "sampler" not in cls:
            continue
        inputs = node.setdefault("inputs", {})
        if steps is not None and "steps" in inputs:
            inputs["steps"] = int(steps)
            changed = True
        if cfg is not None and "cfg" in inputs:
            inputs["cfg"] = float(cfg)
            changed = True
        if denoise is not None and "denoise" in inputs:
            inputs["denoise"] = float(denoise)
            changed = True
    return changed


def _next_free_id(prompt: dict) -> str:
    """返回一个未被占用的整数字符串节点 ID（当前最大整数键 + 1）。"""
    mx = 0
    for k in prompt.keys():
        try:
            mx = max(mx, int(k))
        except (ValueError, TypeError):
            pass
    return str(mx + 1)


def _prefer_model_anchor(prompt: dict, model_srcs: set) -> str | None:
    """从多个候选主模里挑 LoRA 注入锚点：优先「非 GGUF」的主模。

    单管线（只有一个候选）时原样返回，行为不变；多管线工作流（如漫画：GGUF 编辑
    模型 + 标准 anima 生图模型）里，GGUF 通常无法叠加标准 LoRA、且往往是编辑/细化
    管线而非基础生图管线，所以把 LoRA 锚到能正常生效的基础生图主模上。找不到候选时
    返回 None。
    """
    if not model_srcs:
        return None
    # 排序后筛选，保证同候选集时结果确定（set 迭代序受哈希随机化影响）
    ordered = sorted(model_srcs)
    non_gguf = [
        s for s in ordered
        if "gguf" not in ((prompt.get(s) or {}).get("class_type") or "").lower()
    ]
    # 优先 class_type 含 loader 的节点（CheckpointLoader/UNETLoader 等真正的模型加载器）
    loaders = [
        s for s in non_gguf
        if "loader" in ((prompt.get(s) or {}).get("class_type") or "").lower()
    ]
    candidates = loaders or non_gguf or ordered
    return candidates[0]


def _clip_for_model_anchor(prompt: dict, model_anchor: str | None) -> str | None:
    """找与给定 model 锚点同属一条生图管线的 CLIP 加载节点（用于完整模式 LoRA）。

    顺着「使用该主模的 sampler → 它消费的 text-encode 节点 → 其 clip 上游」回溯；
    找不到则返回 None（调用方回退到自动探测到的第一个 clip 源）。
    """
    if not model_anchor:
        return None
    for _yid, ynode in prompt.items():
        if not isinstance(ynode, dict):
            continue
        ct = (ynode.get("class_type") or "").lower()
        if "sampler" not in ct:
            continue
        m = (ynode.get("inputs") or {}).get("model")
        if not (isinstance(m, list) and len(m) == 2 and str(m[0]) == str(model_anchor)):
            continue
        for _f, v in (ynode.get("inputs") or {}).items():
            if isinstance(v, list) and len(v) == 2:
                src = str(v[0])
                snode = prompt.get(src)
                if not isinstance(snode, dict):
                    continue
                sct = (snode.get("class_type") or "").lower()
                if "clip" in sct and ("encode" in sct or "text" in sct):
                    cv = (snode.get("inputs") or {}).get("clip")
                    if isinstance(cv, list) and len(cv) == 2:
                        return str(cv[0])
    return None


def _find_injection_anchors(prompt: dict):
    """探测 LoRA 注入所需的 (model_src, clip_src) 锚点（找不到返回 (None, None)）。

    返回的两个 ID 是「采样器最终使用的 model 上游」与「CLIP 文本编码最终使用的
    clip 上游」。尽可能与节点类名解耦：

    1) 优先：类名含 "checkpointloader" 的节点（一个节点同时出 model+clip），
       此时 model_src == clip_src。
    2) 退而求其次：某个节点同时被采样器的 model 输入（slot0）和 CLIP 编码的
       clip 输入（slot1）引用——即底模加载节点，同样 model_src == clip_src。
    3) 最后兜底：分离式工作流（如 UNETLoader 出 model + CLIPLoader 出 clip），
       则 model_src / clip_src 分别取两者的上游节点。

    这样无论底模节点叫 CheckpointLoader / UNETLoader / 自定义名，都能正确定位锚点。
    """
    # 1) 显式 CheckpointLoader：同时提供 model 与 clip
    for nid, node in prompt.items():
        if isinstance(node, dict) and "checkpointloader" in (
            node.get("class_type") or ""
        ).lower():
            return (str(nid), str(nid))

    # 收集：哪些节点被当作 采样器.model(slot0) / CLIP编码.clip(slot1) 的上游
    model_srcs: set = set()
    clip_srcs: set = set()
    for yid, ynode in prompt.items():
        if not isinstance(ynode, dict):
            continue
        ct = (ynode.get("class_type") or "").lower()
        is_sampler = "sampler" in ct
        is_clipenc = "clip" in ct and ("encode" in ct or "text" in ct)
        for field, val in (ynode.get("inputs") or {}).items():
            if isinstance(val, list) and len(val) == 2:
                src = str(val[0])
                # ★只认语义匹配的输入框：采样器的 model 输入才可能是 model 源，
                # 否则 positive/latent_image（同为 slot0）会被误收进候选，
                # _prefer_model_anchor 再从 set 里随机取一个 → 锚点选择变成
                # 随机的（字符串哈希随机化），LoRA 偶尔被注到正向编码节点上。
                if is_sampler and val[1] == 0 and "model" in field.lower():
                    model_srcs.add(src)
                if is_clipenc and "clip" in field.lower():
                    # 不限制 slot：CLIP 编码节点的 clip 输入可能接在 slot0
                    # （如 CLIPLoader）或 slot1（如 CheckpointLoader），以实际为准
                    clip_srcs.add(src)

    # 2) 同一个节点同时供给 model 与 clip（覆盖 CheckpointLoader 未被 1 命中时）
    common = model_srcs & clip_srcs
    if common:
        nid = next(iter(common))
        return (nid, nid)

    # 3) 分离式：model 来自一个节点、clip 来自另一个
    # 多管线工作流优先把 LoRA 锚到非 GGUF 的基础生图主模；clip 锚点尽量对齐到同一条管线。
    m = _prefer_model_anchor(prompt, model_srcs)
    c = _clip_for_model_anchor(prompt, m) if m is not None else None
    if c is None:
        c = next(iter(clip_srcs)) if clip_srcs else None
    return (m, c)



# --------------------------------------------------------------------------- #
# 基础工作流运行时手术（v7.0.0）：放大绕过/注入、清理注入、保存节点替换、采样器覆盖
# --------------------------------------------------------------------------- #
def _save_images_link(prompt: dict, save_node) -> tuple[str, int] | None:
    """输出节点 images 输入当前接的 (上游节点ID, slot)。"""
    node = _get_node(prompt, save_node)
    if not node:
        return None
    return _link((node.get("inputs") or {}).get("images"))


def _link(v):
    """输入值是否为节点连线 [id, slot]（本模块此前没有这个助手，v7.0.0 补上）。"""
    if isinstance(v, list) and len(v) == 2 and not isinstance(v[0], (dict, list)):
        try:
            return str(v[0]), int(v[1])
        except (TypeError, ValueError):
            return None
    return None


def bypass_upscale(
    prompt: dict, save_node, chain_nodes=None, image_source=None, upscale_loader=None,
) -> bool:
    """绕过放大链（v7.0.1）：保存节点改接图像源，并删除整条放大/alpha 处理链。

    chain_nodes / image_source 来自解析注记（upscale.chain_nodes / image_source）。
    alpha 工作流（Split→放大→Join）里放大节点被 Join 消费，只删放大两节点会让
    Join 输入悬空导致 ComfyUI 校验失败——所以必须整链删除并改接回图像源
    （如 VAEDecode 输出，带 alpha）。

    安全阀：若链上有节点被链外其它节点消费（删除会悬空），则放弃删除、返回 False。
    """
    save = _get_node(prompt, save_node)
    if not save or not image_source:
        return False
    src_id = str(image_source)
    if src_id not in prompt:
        return False
    chain = {str(c) for c in (chain_nodes or [])} - {src_id}
    if upscale_loader:
        chain.add(str(upscale_loader))
    # 外部消费者检查：链外节点（除保存节点）不得引用链上/被删节点
    for nid, node in prompt.items():
        if str(nid) == str(save_node) or str(nid) in chain or str(nid) == src_id:
            continue
        if not isinstance(node, dict):
            continue
        for val in (node.get("inputs") or {}).values():
            l = _link(val)
            if l and l[0] in chain:
                return False
    save.setdefault("inputs", {})["images"] = [src_id, 0]
    for c in chain:
        prompt.pop(str(c), None)
    return True


def inject_upscale(prompt: dict, save_node, model_name: str) -> str | None:
    """注入放大链：UpscaleModelLoader + ImageUpscaleWithModel 插在保存节点上游。

    返回新保存节点 images 所指的放大节点 ID（失败返回 None）。
    """
    save = _get_node(prompt, save_node)
    if not save or not (model_name or "").strip():
        return None
    src = _link((save.get("inputs") or {}).get("images"))
    if not src:
        return None
    loader_id = _next_free_id(prompt)
    prompt[loader_id] = {
        "class_type": "UpscaleModelLoader",
        "inputs": {"model_name": model_name.strip()},
        "_meta": {"title": "注入放大模型 (anima)"},
    }
    # ★先插入 loader 再取下一个空闲 ID（两 ID 一起先取会撞号）
    apply_id = _next_free_id(prompt)
    prompt[apply_id] = {
        "class_type": "ImageUpscaleWithModel",
        "inputs": {"upscale_model": [loader_id, 0], "image": [src[0], src[1]]},
        "_meta": {"title": "注入放大 (anima)"},
    }
    save["inputs"]["images"] = [apply_id, 0]
    return apply_id


def inject_cleanup(prompt: dict, save_node) -> str | None:
    """注入清理显存节点（easy cleanGpuUsed，透传）插在保存节点上游。"""
    save = _get_node(prompt, save_node)
    if not save:
        return None
    src = _link((save.get("inputs") or {}).get("images"))
    if not src:
        return None
    new_id = _next_free_id(prompt)
    prompt[new_id] = {
        "class_type": "easy cleanGpuUsed",
        "inputs": {"anything": [src[0], src[1]]},
        "_meta": {"title": "注入清理显存 (anima)"},
    }
    save["inputs"]["images"] = [new_id, 0]
    return new_id


def replace_save_node(
    prompt: dict, save_node, output_ext: str = "", quality=None,
) -> str | None:
    """把保存节点替换成标准 SaveImageExtended（保存格式/质量可配）。

    原保存节点从图中删除（避免双份落盘），其 images 上游改接新节点。
    返回新节点 ID（失败 None）。
    """
    old = _get_node(prompt, save_node)
    if not old:
        return None
    src = _link((old.get("inputs") or {}).get("images"))
    if not src:
        return None
    old_inputs = old.get("inputs") or {}
    new_id = _next_free_id(prompt)
    inputs = {
        "filename_prefix": old_inputs.get("filename_prefix") or "anima",
        "images": [src[0], src[1]],
        "output_ext": (output_ext or "").strip() or ".png",
    }
    try:
        q = int(quality)
        if q > 0:
            inputs["quality"] = q
    except (TypeError, ValueError):
        pass
    prompt[new_id] = {
        "class_type": "SaveImageExtended",
        "inputs": inputs,
        "_meta": {"title": "💾 Save Image Extended (anima)"},
    }
    prompt.pop(str(save_node), None)
    return new_id


def set_sampler_node(
    prompt: dict, sampler_node, sampler_name: str = "", scheduler: str = "",
) -> bool:
    """给采样器节点写 sampler_name / scheduler（字段存在才写）。"""
    node = _get_node(prompt, sampler_node)
    if not node:
        return False
    inputs = node.setdefault("inputs", {})
    changed = False
    if (sampler_name or "").strip() and "sampler_name" in inputs:
        inputs["sampler_name"] = sampler_name.strip()
        changed = True
    if (scheduler or "").strip() and "scheduler" in inputs:
        inputs["scheduler"] = scheduler.strip()
        changed = True
    return changed


def _lora_chain_tail(prompt: dict, loaders: list):
    """在给定 LoraLoader 节点集合中，返回其 model 链的末端节点 ID。

    末端 = 其 model 输出没有被集合内其它 LoraLoader 当作输入消费的节点。
    """
    if not loaders:
        return None
    consumed = set()
    for nid in loaders:
        node = _get_node(prompt, nid)
        m = (node or {}).get("inputs", {}).get("model")
        if isinstance(m, list) and len(m) == 2:
            consumed.add(str(m[0]))
    tails = [nid for nid in loaders if str(nid) not in consumed]
    return tails[-1] if tails else loaders[-1]


def _bypass_and_delete(prompt: dict, node_ids) -> None:
    """从工作流图中删除指定节点，并把下游对它的 model/clip 引用改接到其上游。

    这是“真禁用”LoRA 的核心：删掉 LoraLoader 后，采样器 / 提示词编码器会直接
    拿到未叠加该 LoRA 的 model/clip，节点不再被执行、对应 LoRA 文件也不会被加载
    （因此不会因文件缺失而报错，也不占用显存）。支持链式 LoRA（A->B->C），会
    跳过所有被删的中间节点，解析到最终未删的上游。
    """
    disabled = {str(n) for n in node_ids if n not in (None, "")}
    if not disabled:
        return
    # 删除前快照每个被删节点的原始上游 (model, clip)，避免删除后解析丢失
    src: dict[str, tuple] = {}
    for nid in disabled:
        node = _get_node(prompt, nid)
        inputs = (node or {}).get("inputs", {})
        src[nid] = (inputs.get("model"), inputs.get("clip"))

    def resolve(start, slot):
        cur, s = str(start), slot
        seen: set[str] = set()
        while cur in disabled and cur not in seen:
            seen.add(cur)
            link = src[cur][0] if s == 0 else src[cur][1]
            if not (isinstance(link, list) and len(link) == 2):
                return None
            cur, s = str(link[0]), link[1]
        return [cur, s]

    for yid, ynode in prompt.items():
        if str(yid) in disabled or not isinstance(ynode, dict):
            continue
        inputs = ynode.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for field, val in list(inputs.items()):
            if isinstance(val, list) and len(val) == 2 and str(val[0]) in disabled:
                resolved = resolve(val[0], val[1])
                if resolved is not None:
                    inputs[field] = resolved

    for nid in list(disabled):
        for key in (nid, str(nid)):
            if key in prompt:
                prompt.pop(key, None)


def _inject_loras(
    prompt: dict, model_src, clip_src, entries: list[tuple], model_only: bool = False
) -> list[str]:
    """链式注入若干 LoRA 节点。

    每个 entry 为 ``(name, model_name, weight, mo)``，其中 ``mo`` 表示该 LoRA
    是否使用「仅模型」节点：

    - ``mo=True`` -> ``LoraLoaderModelOnly``：只输出 MODEL、不接 clip 路，
      只需 model 锚点即可（兼容很难探测 clip 源、或只影响去噪网络的 LoRA）。
    - ``mo=False`` -> 完整 ``LoraLoader``：同时改写 model 路（slot0）与
      clip 路（slot1），需要 model_src 与 clip_src 两个锚点。

    entry 里的 ``mo`` 优先；缺失时回退到函数参数 ``model_only``。

    多个 LoRA 会**链式串联**：第 n 个节点的 model/clip 输入接第 n-1 个节点的
    输出，整条链末端再接回原下游（采样器 / CLIP 编码）。

    model_src / clip_src 为注入起点的上游节点 ID。返回注入的名称列表。
    """
    injected: list[str] = []
    model_src = str(model_src)
    clip_src = str(clip_src) if clip_src is not None else None

    # 注入前记录：谁在消费 model_src 的 model(slot0)
    model_consumers: list[tuple] = []  # (node, field) -> 改接 [last, 0]
    model_consumer_nodes: set = set()  # 这些节点已被 model 路处理，clip 路需排除
    for ynode in prompt.values():
        if not isinstance(ynode, dict):
            continue
        for field, val in (ynode.get("inputs") or {}).items():
            if isinstance(val, list) and len(val) == 2:
                if str(val[0]) == model_src and val[1] == 0:
                    model_consumers.append((ynode, field))
                    model_consumer_nodes.add(id(ynode))

    # 仅当本批里存在“完整 LoRA”（需要 clip 路）时才记录并重接 clip 路
    # 记录 (node, field, clip_slot)：clip_slot 即「编码器引用 clip 源时用的是
    # 源节点的第几个输出」，用它来给 LoRA 的 clip 输入选对 slot
    # （CheckpointLoader 的 CLIP 在 slot1，CLIPLoader 的 CLIP 在 slot0）。
    # 注意：CheckpointLoader 同时是 model 与 clip 的源， model 路消费节点
    # （如 KSampler）也会被它引用 —— 必须排除已计入 model_consumers 的节点，
    # 否则 KSampler.model 会被 clip 改写覆盖成 slot1（旧版全模型模式的 bug）。
    need_clip = any(
        (not (e[3] if len(e) > 3 else model_only)) for e in entries
    )
    clip_consumers: list[tuple] = []   # (node, field, slot) -> 改接 [last_full, 1]
    if need_clip and clip_src is not None:
        for ynode in prompt.values():
            if not isinstance(ynode, dict) or id(ynode) in model_consumer_nodes:
                continue
            for field, val in (ynode.get("inputs") or {}).items():
                if isinstance(val, list) and len(val) == 2:
                    if str(val[0]) == clip_src:
                        clip_consumers.append((ynode, field, val[1]))
        _logger.info(
            f"[LoRA] 完整模式 clip 处理：clip源={clip_src}，"
            f"clip消费节点(将改接LoRA的clip出口)="
            f"{[(str(c[0]), c[1]) for c in clip_consumers]}"
        )

    # 推导 LoRA clip 输入应接 clip 源的第几个输出（无消费节点时默认 slot1）
    clip_slot = clip_consumers[0][2] if clip_consumers else 1

    last = None          # 链末端节点（最终 model 路改接到这里）
    last_full = None    # 链末端“完整 LoraLoader”（有 clip 出口）节点
    prev_clip = clip_src  # 下一个完整 LoRA 的 clip 输入来源（首节点用 clip_src）
    for name, model_name, weight, mo in entries:
        mo = bool(mo) if mo is not None else bool(model_only)
        new_id = _next_free_id(prompt)
        if mo:
            prompt[new_id] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {
                    "lora_name": model_name or name,
                    "strength_model": float(weight),
                    "model": [last if last is not None else model_src, 0],
                },
                "_meta": {"title": f"Load LoRA (Model Only): {name}"},
            }
        else:
            clip_in_slot = clip_slot if prev_clip == clip_src else 1
            prompt[new_id] = {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": model_name or name,
                    "strength_model": float(weight),
                    "strength_clip": float(weight),
                    "model": [last if last is not None else model_src, 0],
                    "clip": [prev_clip, clip_in_slot],
                },
                "_meta": {"title": f"Load LoRA: {name}"},
            }
            last_full = new_id
            prev_clip = new_id
        last = new_id
        injected.append(name)

    # 把原下游（model 路 / clip 路）分别改接到新链末端
    for ynode, field in model_consumers:
        ynode["inputs"][field] = [last, 0]
    # clip 路：仅当链中存在完整 LoraLoader 时，改接到最后一个完整节点的 clip 出口；
    # 若整条链都是“仅模型”，clip 路本就未被改动，保持原样不动。
    if last_full is not None:
        for ynode, field, _slot in clip_consumers:
            ynode["inputs"][field] = [last_full, 1]
    return injected



def _normalize_lora_name(name: str) -> str:
    """去掉 LoRA 名字末尾的版本 / 数字后缀，便于「安魂曲」「安魂曲-1」互相匹配。

    例：安魂曲-1 -> 安魂曲；安魂曲_v2 -> 安魂曲；安魂曲1 -> 安魂曲；安魂曲 V1.3 -> 安魂曲。
    """
    return re.sub(
        r"[-_ ]*(?:v)?\d+(?:\.\d+)*\)?$", "", (name or "").strip(), flags=re.IGNORECASE
    ).strip()


def _lora_name_matches(config_name: str, cmd_key: str) -> bool:
    """命令简写 cmd_key 是否命中配置名 config_name（大小写不敏感）。

    支持：
      - 精确相等（忽略大小写，如 Q版菲比 == q版菲比）；
      - 对称前缀（带分隔符 - _ 空格 （ ( 【 [），如「安魂曲」<->「安魂曲-1」双向；
      - 去掉末尾版本后缀后相等，覆盖「安魂曲」「安魂曲1」「安魂曲_v1」「安魂曲 v1」。
    """
    if not config_name or not cmd_key:
        return False
    a = config_name.strip().lower()
    b = cmd_key.strip().lower()
    if a == b:
        return True
    for sep in ("-", "_", " ", "（", "(", "【", "["):
        if a.startswith(b + sep):
            return True
        if b.startswith(a + sep):
            return True
    if _normalize_lora_name(a) == b:
        return True
    if _normalize_lora_name(b) == a:
        return True
    return False


def apply_loras(
    prompt: dict,
    loras_config: list[dict],
    active_map: dict[str, float | None] | None = None,
    anchor=None,
    clip_anchor=None,
    true_disable: bool = True,
    on_warning=None,
    on_info=None,
    model_only: bool = True,
    protected_nodes=None,
) -> list[str]:
    """注入 / 启用 / 禁用 LoRA。

    active_map 为 None 时按各 LoRA 的 enabled 默认值决定是否启用；
    active_map 不为 None 时，键为要启用的 LoRA 名称，值为权重
    （None 表示使用配置中的默认权重），未列出的 LoRA 一律禁用。

    anchor：显式注入锚点节点 ID（model 源，如 CheckpointLoader / UNETLoader）。
        留空则自动探测（与节点类名无关，从采样器 model 输入反推上游）。
    clip_anchor：显式 CLIP 源节点 ID（如 CLIPLoader / CheckpointLoader 的 CLIP
        一侧）。仅在完整模式（model_only=False）生效；留空则自动探测（从 CLIP
        编码节点的 clip 输入反推上游）。与 anchor 分离后，可分别指定 model 源与
        clip 源，兼容「正向/负向提示词用不同 CLIPLoader」等分离式工作流。


    on_warning：可选回调，签名 (msg: str) -> None。当配置了启用 LoRA、但工作流里
        没有现成 LoraLoader 节点、又探测不到注入锚点时调用，用于向用户告警
        （例如把消息打到插件日志）。

    行为要点：
    - 已有的 LoraLoader 节点：按 load_node 或顺序自动分配后改写强度 / 文件名。
    - 启用但工作流里没有可用 LoraLoader 节点：在锚点（anchor 或自动探测的
      底模加载节点）之后链式“注入”新的 LoraLoader，实现“纯靠配置组装”。
    - 禁用且 true_disable=True：把对应 LoraLoader 从图中删除并重接上下游，
      实现真正禁用（不加载文件、不报错、不占显存）；true_disable=False 时退回
      到把强度置 0 的旧行为。
    - 工作流里存在、但未被任何配置项分配到的 LoraLoader 节点：保持原样不动。

    返回实际启用的 LoRA 名称列表。
    """

    def _report(msg: str) -> None:
        """把决策过程同时打到模块 logger 与外部回调（如插件日志）。"""
        _logger.info(msg)
        if on_info:
            try:
                on_info(msg)
            except Exception:
                pass

    enabled_names: list[str] = []
    # 预收集工作流中的 LoraLoader 节点（按字典顺序），load_node 为空时按顺序自动分配
    # protected_nodes：基础工作流的内置 LoRA 节点（v7.0.0）——不参与自动分配，
    # 避免用户配置的 LoRA 覆写内置模型名（如 mmh1.6 的 Turbo-ANIMA）。
    _protected = {str(x) for x in (protected_nodes or [])}
    loader_nodes = [
        nid
        for nid, node in prompt.items()
        if isinstance(node, dict)
        and (node.get("class_type") or "").endswith("LoraLoader")
        and str(nid) not in _protected
    ]
    _report(
        f"[LoRA] 工作流现有 LoraLoader 节点: {loader_nodes or '无'}；"
        f"配置项 {len(loras_config or [])} 个；"
        f"active_map={'(按 enabled 默认)' if active_map is None else active_map}"
    )
    auto_idx = 0
    to_disable: list = []          # 需删除的已有节点 ID
    to_inject: list[tuple] = []    # 需注入的 (name, model_name, weight)

    for lora in loras_config or []:
        name = (lora.get("name") or "").strip()
        if not name:
            continue

        if active_map is None:
            active = bool(lora.get("enabled", False))
            weight = float(lora.get("weight", 1.0))
        else:
            matched = None
            for k in active_map:
                if _lora_name_matches(name, k):
                    matched = k
                    break
            if matched is None:
                active = False
                weight = 0.0
            else:
                active = True
                w = active_map[matched]
                weight = float(lora.get("weight", 1.0)) if w is None else float(w)

        model_name = (lora.get("model_name") or "").strip()
        model_input = lora.get("model_input", "lora_name")
        s_model = lora.get("strength_model_input", "strength_model")
        s_clip = lora.get("strength_clip_input", "strength_clip")

        # 分配节点：优先用显式 load_node，否则按顺序自动分配现有 LoraLoader
        load_node = lora.get("load_node")
        node = None
        node_id = None
        if load_node:
            node = _get_node(prompt, load_node)
            node_id = load_node
        elif auto_idx < len(loader_nodes):
            node_id = loader_nodes[auto_idx]
            node = _get_node(prompt, node_id)
            auto_idx += 1

        if node is not None:
            inputs = node.setdefault("inputs", {})
            if active:
                if model_name:
                    inputs[model_input] = model_name
                inputs[s_model] = weight
                inputs[s_clip] = weight
                enabled_names.append(name)
                _report(
                    f"[LoRA] 「{name}」→ 改写已有节点 {node_id}"
                    f"（文件={inputs.get(model_input)}, 权重={weight}）"
                    + ("" if model_name else "；⚠ 未填别名(文件名)，仅改权重、未改文件名")
                )
                if not model_name and on_warning:
                    on_warning(
                        f"【LoRA 提示】「{name}」已启用，但配置里没填 model_name（真实 "
                        f".safetensors 文件名），节点 {node_id} 只改了权重、文件名仍是工作流"
                        f"默认值（{inputs.get(model_input)}）。若最终出图不是该 LoRA，请在配置"
                        f"里补上 model_name。"
                    )
            elif true_disable:
                to_disable.append(node_id)
                _report(f"[LoRA] 「{name}」→ 禁用，待删除节点 {node_id}")
            else:
                # 退回旧行为：强度置 0（仍保留 lora_name 以免节点报错）
                if model_name:
                    inputs[model_input] = model_name
                inputs[s_model] = 0.0
                inputs[s_clip] = 0.0
                _report(f"[LoRA] 「{name}」→ 禁用，节点 {node_id} 强度置 0")
        else:
                # 工作流里没有现成节点：启用则注入，禁用则忽略（本就不存在）
                if active:
                    mo = lora.get("model_only", model_only)
                    to_inject.append((name, model_name, weight, mo))
                    _report(
                        f"[LoRA] 「{name}」→ 工作流无现成节点，待注入"
                        f"（文件={model_name or name}, 权重={weight}）"
                        f"{'（仅模型）' if mo else '（模型+CLIP）'}"
                    )
                else:
                    _report(f"[LoRA] 「{name}」→ 禁用且工作流无对应节点，跳过")

    # 先注入需要新增的 LoRA（接在现有启用链末端或锚点之后）
    if to_inject:
        if anchor or clip_anchor:
            # 显式锚点优先；某一侧未填则自动探测补齐
            auto_model, auto_clip = (None, None)
            if not anchor or not clip_anchor:
                auto_model, auto_clip = _find_injection_anchors(prompt)
            model_src = str(anchor) if anchor else auto_model
            clip_src = (
                str(clip_anchor)
                if clip_anchor
                else (str(anchor) if anchor else auto_clip)
            )
        else:
            model_src, clip_src = _find_injection_anchors(prompt)
        # 是否完整模式（任一注入项需要 clip 路）决定是否需要 clip 锚点
        need_clip = any(
            (not (e[3] if len(e) > 3 else model_only)) for e in to_inject
        )
        # 仅模型模式只需 model 源；完整模式需 model+clip 两个源
        if model_src is not None and (not need_clip or clip_src is not None):
            _report(
                f"[LoRA] 注入准备：模式={'仅模型' if model_only else '模型+CLIP'}，"
                f"model源={model_src}，clip源={clip_src}"
            )
            disabled_set = {str(x) for x in to_disable}
            active_loaders = [n for n in loader_nodes if str(n) not in disabled_set]
            tail_model, tail_clip = model_src, (clip_src if clip_src is not None else model_src)
            if active_loaders:
                # 已有启用链时，接在链末端（model 路接链尾；clip 路以链尾为锚）
                tail_node = _lora_chain_tail(prompt, active_loaders)
                if tail_node is not None:
                    tail_model = tail_clip = tail_node
            _before_ids = set(prompt.keys())
            enabled_names.extend(
                _inject_loras(prompt, tail_model, tail_clip, to_inject, model_only=model_only)
            )
            _new_ids = [k for k in prompt.keys() if k not in _before_ids]
            _report(
                f"[LoRA] 注入完成（{'仅模型' if model_only else '模型+CLIP'}）："
                f"新建节点 {_new_ids}（锚点 model 源={tail_model}）"
            )
        else:
            # 列出工作流节点清单，便于用户挑选正确的键名填入 lora_anchor
            inventory = [
                f"{nid}（class_type={node.get('class_type') or '?'}）"
                for nid, node in prompt.items()
                if isinstance(node, dict)
            ]
            names = ", ".join(e[0] for e in to_inject)
            msg = (
                "【LoRA 未生效】已配置启用的 LoRA，但工作流中没有 LoraLoader 节点、"
                "且无法自动探测到注入锚点（底模加载节点）。本次这些 LoRA 被跳过："
                f"{names}。\n"
                "解决办法（二选一）：\n"
                "  1) 在工作流配置里填 lora_anchor = 底模加载节点的键名"
                "（从下方清单选一个同时出 model 与 clip 的节点，如 CheckpointLoader）；\n"
                "  2) 或直接在 ComfyUI 工作流里加一个 LoraLoader 节点"
                "（放在底模与采样器之间），本插件会自动改写它。\n"
                "当前工作流所有节点（把对应键名填进 lora_anchor）：\n  "
                + "\n  ".join(inventory)
            )
            _logger.warning(msg)
            if on_warning:
                on_warning(msg)


    # 再真删除被禁用的已有节点（重接上下游）
    if to_disable:
        _bypass_and_delete(prompt, to_disable)
        _report(f"[LoRA] 真删除禁用节点 {[str(x) for x in to_disable]} 并已重接上下游")

    _report(f"[LoRA] 本次最终启用: {enabled_names or '无'}")
    return enabled_names


# --------------------------------------------------------------------------- #
# 多参考图图生图（v7.7.35，如 TextEncodeQwenImage21 的 images.image_N 动态输入）
# --------------------------------------------------------------------------- #
def _is_load_image_node(node: dict) -> bool:
    ct = str((node or {}).get("class_type") or "")
    return any(ct == h or ct.startswith(h) for h in IMAGE_LOADER_HINTS)


def _ref_chain_to_loader(prompt: dict, link) -> list[str]:
    """从一条连线引用回溯到 LoadImage 的节点链（返回顺序：源在前、LoadImage 在后）。"""
    chain: list[str] = []
    cur = link
    seen: set = set()
    while isinstance(cur, list) and len(cur) >= 2 and str(cur[0]) in prompt:
        nid = str(cur[0])
        if nid in seen:
            break
        seen.add(nid)
        chain.append(nid)
        node = prompt[nid]
        if _is_load_image_node(node):
            return chain
        nxt = None
        for v in (node.get("inputs") or {}).values():
            if isinstance(v, list) and len(v) >= 2 and str(v[0]) in prompt:
                nxt = v
                break
        if nxt is None:
            break
        cur = nxt
    return chain


def find_multi_image_node(prompt: dict) -> "dict | None":
    """找条件节点上的 `images.image_N` 动态输入（多参考图，如 TextEncodeQwenImage21）。

    返回 {"node": 节点ID, "count": 现有路数, "inputs": [按N排序的输入名]}；没有返回 None。
    """
    best: dict | None = None
    for nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        ks = [k for k in (node.get("inputs") or {}) if str(k).startswith("images.image")]
        if not ks:
            continue
        ks.sort(key=lambda k: int(str(k).rsplit("_", 1)[-1])
                if str(k).rsplit("_", 1)[-1].isdigit() else 0)
        if best is None or len(ks) > best["count"]:
            best = {"node": str(nid), "count": len(ks), "inputs": ks}
    return best


def find_size_switch(prompt: dict) -> "dict | None":
    """找图生图的「尺寸来源开关」（v7.7.39，如 Qwen 2.1 Edit 的 ComfySwitchNode）。

    判定：`switch` 输入是布尔，且 `on_true`/`on_false` 两路连线**恰好一路**指向
    EmptyLatentImage 类节点（自定义尺寸侧）、另一路非 latent 节点（参考图侧）。
    返回 {"node", "field", "empty_side"}（empty_side=指向 EmptyLatentImage 的那路）；
    没有返回 None。
    """
    for nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        ins = node.get("inputs") or {}
        sw = ins.get("switch")
        if not isinstance(sw, bool):
            continue
        sides: dict = {}
        ok = True
        for k in ("on_true", "on_false"):
            v = ins.get(k)
            if not (isinstance(v, list) and len(v) >= 2 and str(v[0]) in prompt):
                ok = False
                break
            ct = str((prompt[str(v[0])] or {}).get("class_type") or "").lower()
            sides[k] = "emptylatentimage" in ct
        if not ok or sides["on_true"] == sides["on_false"]:
            continue
        return {
            "node": str(nid),
            "field": "switch",
            "empty_side": "on_true" if sides["on_true"] else "on_false",
        }
    return None


def set_size_switch(prompt: dict, sw: dict, enable: bool) -> bool:
    """拨动尺寸来源开关：enable=True → 用 EmptyLatentImage（自定义尺寸生效）；
    enable=False → 用参考图（on_* 另一路）。写入布尔值（ComfySwitchNode 期待 bool）。"""
    node = _get_node(prompt, (sw or {}).get("node"))
    if node is None:
        return False
    node.setdefault("inputs", {})[str((sw or {}).get("field") or "switch")] = bool(enable)
    return True


def prepare_multi_image_refs(
    prompt: dict, roles: dict, image_names: list[str]
) -> "list[str | None] | None":
    """多参考图图生图（v7.7.35）：把 M 张参考图接进条件节点的 `images.image_N` 动态输入。

    工作流里已有 N 路输入（`images.image_1..N`，roles["multi_image"] 记录了节点与数量）：
      · 前 min(M, N) 路：回溯各路到自己的 LoadImage，写入上传后的文件名；
      · M > N：**复制第一路的节点链**（LoadImage → 缩放/预处理 …，缩放参数原样拷贝）
        作为新路，依次接线 `images.image_(N+1)`…；
      · image_names 与路一一对应（调用方保证顺序=用户图顺序，提示词「图N」即第 N 路）。

    返回与 image_names 对齐的 LoadImage 节点 ID 列表（调用方已无需再写文件名——
    本函数内已写好）；工作流没有多图节点时返回 None（调用方走单图旧路径）。
    """
    if not isinstance(prompt, dict) or not prompt or not image_names:
        return None
    multi = (roles or {}).get("multi_image") or {}
    tgt = str(multi.get("node") or "")
    if not tgt or tgt not in prompt:
        return None
    tgt_node = prompt[tgt]
    tgt_inputs = tgt_node.setdefault("inputs", {})

    def _img_keys() -> list[str]:
        ks = [k for k in tgt_inputs if str(k).startswith("images.image")]
        ks.sort(key=lambda k: int(str(k).rsplit("_", 1)[-1])
                if str(k).rsplit("_", 1)[-1].isdigit() else 0)
        return ks

    keys = _img_keys()
    if not keys:
        return None

    first_link = tgt_inputs.get(keys[0])
    chain = _ref_chain_to_loader(prompt, first_link)
    if not chain or not _is_load_image_node(prompt[chain[-1]]):
        return None
    load1 = chain[-1]

    load_ids: "list[str | None]" = []
    for i, name in enumerate(image_names):
        if i < len(keys):
            # 已有的一路：回溯到该路自己的 LoadImage 写文件名
            ch = _ref_chain_to_loader(prompt, tgt_inputs.get(keys[i]))
            if ch and _is_load_image_node(prompt[ch[-1]]):
                lid = ch[-1]
            else:
                lid = load1  # 兜底（该路回溯失败时沿用第一路的 LoadImage）
            set_image_node(prompt, lid, name)
            load_ids.append(lid)
        else:
            # 扩展新路：复制第一路链（LoadImage → 处理节点 …）
            copies: dict[str, str] = {}
            new_load: str | None = None
            for nid in reversed(chain):        # LoadImage 在前 → 源在后
                src = prompt[nid]
                new_id = _next_free_id(prompt)
                new_node = json.loads(json.dumps(src))
                for k, v in (new_node.get("inputs") or {}).items():
                    if isinstance(v, list) and len(v) >= 2 and str(v[0]) in chain:
                        v[0] = copies[str(v[0])]
                prompt[new_id] = new_node
                copies[str(nid)] = new_id
                if _is_load_image_node(src):
                    set_image_node(prompt, new_id, name)
                    new_load = new_id
            # 新路接到条件节点（chain[0] 是 image_1 的直接源，其副本同构）
            tgt_inputs[f"images.image_{i + 1}"] = [copies[chain[0]], 0]
            load_ids.append(new_load)
    return load_ids
