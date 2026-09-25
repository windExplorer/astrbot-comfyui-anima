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

# --------------------------------------------------------------------------- #
# 纯处理 / 放大类工作流（v7.7.1，如 TE-Speed VOSR2 超分）
# --------------------------------------------------------------------------- #
# 「倍率」可写字段优先级（VOSR2 的 scale、UltraSharp 的 scale_factor、
# SeedVR2 那类 ResizeImageMaskNode 的 resize_type.multiplier 等）
_SCALE_FIELD_PREFS = (
    "scale", "scale_factor", "upscale_by", "magnification",
    "resize_scale", "scale_by", "factor", "upscale_factor",
    "resize_type.multiplier",       # v7.7.12：字段名带点号，是 ComfyUI 的真实键名
)
# 「种子」字段优先级
_SEED_FIELD_PREFS = ("seed", "noise_seed", "rand_seed", "seed_value")
# 独立数值节点（如 `easy int` / PrimitiveInt）里可写的数值字段
_NUM_FIELD_PREFS = ("value", "int", "number", "float", "value_int", "num", "i")
# 处理链上「图像输入」字段名（沿它向上回溯到图像输入节点）
# 前半是显式图像流；后半是**隐式图像流**（v7.7.12，SeedVR2 单步采样器型实测）：
# 链路形如 保存←后处理.images←VAEDecode.samples←KSampler.latent_image
#           ←VAEEncode.pixels←预处理.resized_images←缩放.input←图输入
_IMAGE_IN_FIELDS = (
    "image", "images", "image1", "input_image", "img", "pixels", "anything",
    "samples", "latent_image", "latent", "resized_images", "input",
)
# 「预处理缩放」节点的目标像素字段（v7.7.14）：这类节点按**总像素**归一化，
# 不分大小一律缩放（小图会被插值放大）。`megapixels` 的单位是 MP，其余是绝对像素。
_PIXEL_FIELD_PREFS = ("megapixels", "max_total_pixels", "total_pixels", "target_pixels")

# --------------------------------------------------------------------------- #
# 文本写入点合理性（v7.7.12 安全网）
# --------------------------------------------------------------------------- #
# 这些字段装的是**资源文件名**（模型 / VAE / LoRA / CLIP…），绝不能当提示词写入口：
# 写上去 = 把「模型名」改成提示词，ComfyUI 直接报「值不在列表里」。
# 背景：SeedVR2 这类工作流的 positive 连到 SeedVR2Conditioning（无字符串框），
# 旧逻辑沿「任意第一条连线」乱走，走到 UNETLoader 后兜底取 str_fields[0]，
# 于是把 `unet_name` 当成了正向提示词写入点（实测会把 unet_name 写成 "lowres"）。
_RESOURCE_FIELD_EXACT = {
    "unet_name", "ckpt_name", "vae_name", "lora_name", "clip_name", "clip_name1",
    "clip_name2", "clip_name3", "model_name", "gguf_name", "control_net_name",
    "style_model_name", "text_encoder_name", "diffusion_model_name",
    "embedding_name", "upscale_model_name", "sampler_name", "scheduler",
    "weight_dtype", "device", "dtype", "model_type", "format", "precision",
}
# 这类后缀基本是「文件名 / 路径」而非文本
_RESOURCE_FIELD_SUFFIX = ("_name", "_file", "_path", "_filename", "_dir", "_folder")
# 值看起来是模型/权重文件名
_RESOURCE_VALUE_SUFFIX = (
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".sft",
)


def _is_resource_field(field: str, node: dict) -> bool:
    """该字符串字段是否为「资源文件名」类，不能当提示词写入点（v7.7.12）。"""
    f = str(field or "").strip().lower()
    if not f:
        return False
    if f in _RESOURCE_FIELD_EXACT:
        return True
    if f.endswith(_RESOURCE_FIELD_SUFFIX):
        return True
    # 兜底：字段名不是文本语义、但值是权重文件名 → 也算资源字段
    val = ((node.get("inputs") or {}).get(field))
    if isinstance(val, str) and val.strip().lower().endswith(_RESOURCE_VALUE_SUFFIX):
        if not any(p in f for p in _TEXT_FIELD_PREFS):
            return True
    return False


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

    v7.7.12：**资源文件名类字段一律不算可写入的文本框**（`_is_resource_field`）——
    否则没有文本编码器的工作流会把提示词写进 `unet_name` 这类模型名里。
    """
    inputs = node.get("inputs") or {}
    str_fields = [
        f for f, v in inputs.items()
        if isinstance(v, str) and f not in exclude and f not in ("class_type", "_meta")
        and not _is_resource_field(f, node)
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


def _find_pre_scale(nodes: dict, image_node: str | None) -> dict | None:
    """从图输入节点**顺流**找出第一个「预处理缩放」节点（v7.7.14）。

    典型：`LoadImage → ImageScaleToTotalPixels(megapixels=1.25) → 文本/VAE 编码`。
    这类节点按**总像素**归一化，不分大小一律缩放——小图会被插值放大到目标像素
    （输出比输入大、细节并不会变多）。把它的可写目标记下来，出图时可改写成
    「只缩不放」（小图给它的实际像素，大图仍按原目标缩小）。

    判定：类名像缩放（含 scale / resize）+ 确实带目标像素字段；顺流 BFS（离图输入
    最近的优先，那才是「预处理」那一步）。找不到返回 None。
    """
    if not image_node or image_node not in nodes:
        return None
    down: dict[str, list[str]] = {}
    for nid, n in nodes.items():
        for v in (n.get("inputs") or {}).values():
            lk = _link(v)
            if lk and lk[0] in nodes and lk[0] != nid:
                down.setdefault(lk[0], []).append(nid)
    seen = {str(image_node)}
    queue = list(down.get(str(image_node), []))
    while queue:
        nid = queue.pop(0)                       # BFS：按离图输入的远近逐层看
        if nid in seen:
            continue
        seen.add(nid)
        node = nodes.get(nid)
        if not isinstance(node, dict):
            continue
        ct = _ct(node)
        if "scale" in ct or "resize" in ct:
            _in = node.get("inputs") or {}
            for f in _PIXEL_FIELD_PREFS:
                v = _in.get(f)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                    _unit = "mp" if f == "megapixels" else "px"
                    out = {
                        "node": nid, "field": f, "default": float(v),
                        "class_type": node.get("class_type"), "unit": _unit,
                        # 统一换算成 MP，调用方只跟 MP 打交道
                        "default_mp": float(v) if _unit == "mp" else float(v) / 1_000_000,
                    }
                    if isinstance(_in.get("resolution_steps"), (int, float)):
                        out["steps_field"] = "resolution_steps"
                        out["steps_default"] = int(_in["resolution_steps"])
                    return out
        queue.extend(down.get(nid, []))
    return None


def _walk_up_model(prompt: dict, start: str) -> tuple[str | None, list[str], list[str]]:
    """从采样器的 model 输入沿 MODEL 连线向上回溯到主模源。

    返回 (主模节点ID, 途经 LoRA 节点列表, 途经「模型修饰节点」列表)。

    v7.7.3：此前是「遇到第一个非 LoRA 节点就停」，于是
    `UNETLoader → ModelSamplingAuraFlow → KSampler` 这类工作流会把
    ModelSamplingAuraFlow（采样偏移 / 模型补丁节点）当成主模 —— 表现为
    「模型识别错、显示 ModelSamplingAuraFlow、底模关联不上」，而且该修饰节点
    **上游的 LoRA 也全被漏收**（内置 LoRA 保护随之失效）。
    现在改为：只要节点还带着 `model` 上游连线就继续穿透，直到真正的加载器，
    或再往上没有 `model` 连线（那它就是链路源头）。
    """
    loras: list[str] = []
    patches: list[str] = []
    cur = start
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        node = prompt.get(cur)
        if not isinstance(node, dict):
            return None, loras, patches
        if _is_base_model_loader(node):
            return cur, loras, patches
        nxt = _link((node.get("inputs") or {}).get("model"))
        if not nxt:
            # 链路源头：自定义加载器类名可能不含 unet / checkpoint（识别不到就以此兜底）
            return cur, loras, patches
        (loras if _is_lora(node) else patches).append(cur)
        cur = nxt[0]
    return None, loras, patches


def list_nodes(prompt: dict) -> list[dict]:
    """把工作流摊平成节点清单（供「详情」表格展示）。

    返回 [{id, title, class_type, values}]：
    - title：节点自带的 _meta.title（ComfyUI 界面上的中文名），无则空；
    - values：输入项的可读概览（连线写作 `→ 节点ID.slot`，字面量截断到 60 字）。
    """
    out: list[dict] = []
    if not isinstance(prompt, dict):
        return out
    for nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        parts: list[str] = []
        for k, v in inputs.items():
            l = _link(v)
            if l:
                parts.append(f"{k} → {l[0]}[{l[1]}]")
            else:
                sv = str(v)
                if len(sv) > 60:
                    sv = sv[:60] + "…"
                parts.append(f"{k} = {sv}")
        out.append({
            "id": str(nid),
            "title": ((node.get("_meta") or {}).get("title") or ""),
            "class_type": node.get("class_type") or "",
            "values": "；".join(parts),
        })
    return out


def _text_encoder_nodes(nodes: dict) -> list[str]:
    """文本编码节点（CLIPTextEncode 等）。

    纯放大/处理工作流**不该**有它们——用来把「丢了采样器但本该是出图工作流」
    和「真的是纯处理工作流」区分开（防误判成放大类）。
    """
    out = []
    for nid, n in nodes.items():
        ct = _ct(n)
        if "encode" in ct and ("clip" in ct or "text" in ct or "condition" in ct):
            out.append(nid)
    return out


def _numeric_target(nodes: dict, node_id: str, field: str) -> dict | None:
    """把一个输入框定位成「可写数值目标」。

    - 字面量数字 → 就地可写（node=本节点、field=该字段）；
    - 连线到独立数值节点（如 `easy int.value`）→ 写那个节点的数值字段
      （VOSR2 的倍率就是这样：`scale` 连到 `easy int` 的 `value`）；
    - 定位不到返回 None。
    """
    node = nodes.get(node_id)
    if not isinstance(node, dict):
        return None
    val = (node.get("inputs") or {}).get(field)
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return {"node": node_id, "field": field, "default": int(val)}
    lk = _link(val)
    if not lk or lk[0] not in nodes:
        return None
    src = nodes[lk[0]]
    if not isinstance(src, dict):
        return None
    sin = src.get("inputs") or {}
    for f in _NUM_FIELD_PREFS:
        v = sin.get(f)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return {"node": lk[0], "field": f, "default": int(v)}
    for f, v in sin.items():          # 兜底：任意数值型输入
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return {"node": lk[0], "field": f, "default": int(v)}
    return None


def _parse_upscale(nodes: dict) -> tuple[dict | None, list[str]]:
    """尝试按「纯处理 / 放大」工作流解析（v7.7.1，v7.7.12 扩到单步采样器型）。

    适用对象：**没有提示词可写** 的处理/超分工作流，链路形如
    `LoadImage → 处理链 → SaveImage(Extended)`。两种形态都收：

      · 无采样器型（TE-Speed VOSR2）：`LoadImage → VOSR2放大 → 保存`；
      · 单步采样器型（SeedVR2 3B）：`LoadImage → 缩放 → VAE编码 → KSampler(steps=1)
        → VAE解码 → 后处理 → 保存`——有 KSampler，但条件来自 SeedVR2Conditioning，
        整条链上没有任何文本编码器。

    与出图工作流的入库标准完全不同，因此走单独旁路，返回三种语义：
      - `(roles, [])`     解析通过（`kind="upscale"`）；
      - `(None, [错误…])` 看起来是处理类工作流、但没配全/连错线（给针对性提示）；
      - `(None, [])`      不是处理类工作流（调用方按出图工作流的原逻辑报错）。
    """
    save_nodes = [nid for nid, n in nodes.items() if "saveimage" in _ct(n)]
    image_loaders = [nid for nid, n in nodes.items() if _is_image_loader(n)]
    # 「像是处理类工作流」的前提：有图输入 + 有保存节点 + 完全没有文本编码器
    if not save_nodes or not image_loaders or _text_encoder_nodes(nodes):
        return None, []
    # v7.7.12 反向前提：采样器的正/负向若**确实能定位到可写文本框**，说明它是出图工作流
    # （只是类名没命中 `_text_encoder_nodes` 的特征），该按出图工作流的原逻辑去报错，
    # 不能当处理类收编。这条同时兜住了 `_text_encoder_nodes` 漏判的风险。
    samplers = [nid for nid, n in nodes.items() if _is_sampler(n)]
    if len(samplers) > 1:                 # 多阶段：交回原逻辑报「多阶段暂不支持」
        return None, []
    for _sid in samplers:
        _s_in = nodes[_sid].get("inputs") or {}
        for _k in ("positive", "negative"):
            if _resolve_text_write_node(nodes, _link(_s_in.get(_k))):
                return None, []

    errors: list[str] = []
    save_id = next(
        (nid for nid in save_nodes if "extended" in _ct(nodes[nid])), save_nodes[0]
    )
    image_id = image_loaders[0]

    # 从保存节点沿图像连线向上回溯，直到图像输入节点（沿途即处理链）
    chain: list[str] = []
    seen: set = set()
    apply_id = ""
    _save_in = nodes[save_id].get("inputs") or {}
    cur = _link(_save_in.get("images")) or _link(_save_in.get("image"))
    while cur and cur[0] in nodes and cur[0] not in seen:
        seen.add(cur[0])
        if cur[0] == image_id:
            break
        chain.append(cur[0])
        if not apply_id:
            apply_id = cur[0]              # 离保存节点最近的 = 处理/放大执行节点
        _sin = nodes[cur[0]].get("inputs") or {}
        nxt = None
        for _f in _IMAGE_IN_FIELDS:
            nxt = _link(_sin.get(_f))
            if nxt:
                break
        cur = nxt
    if not chain or not apply_id:
        errors.append(
            "未在「图像输入 → 保存节点」之间找到处理节点（放大/超分节点），"
            "请检查工作流连线是否断开。"
        )
        return None, errors
    if not cur or cur[0] != image_id:
        errors.append("处理链没有连回图像输入节点（LoadImage 等），请检查工作流连线。")
        return None, errors

    # 有采样器（单步扩散式放大，如 SeedVR2）时它才是「放大执行体」：
    # 展示、倍率/种子搜索都以它为中心（种子就在 KSampler.seed 上）。
    sampler_id = samplers[0] if (samplers and samplers[0] in chain) else ""
    if sampler_id:
        apply_id = sampler_id
    apply = nodes[apply_id]
    a_in = apply.get("inputs") or {}

    # 处理节点旁的「模型/设置」等资源节点（供展示与日志；模型名尤其有用）
    loader_id, loader_class, aux_nodes = "", "", []
    for _v in a_in.values():
        lk = _link(_v)
        if not lk or lk[0] not in nodes or lk[0] == image_id or lk[0] in chain:
            continue
        _c = nodes[lk[0]].get("class_type") or ""
        if "loader" in _c.lower() and not loader_id:
            loader_id, loader_class = lk[0], _c
        else:
            aux_nodes.append(lk[0])
    model_file = ""
    if loader_id:
        _li = nodes[loader_id].get("inputs") or {}
        model_file = next(
            (
                str(_li[k]).strip()
                for k in ("model_bundle", "model_name", "ckpt_name", "unet_name",
                          "gguf_name", "model")
                if isinstance(_li.get(k), str) and str(_li[k]).strip()
            ),
            "",
        )

    # 倍率：处理链上第一个可写数值目标（字面量就地写 / 连线写独立数值节点）。
    # v7.7.12：搜索范围从「离保存最近的节点」扩到**整条处理链**——SeedVR2 的倍率在
    # ResizeImageMaskNode 的 `resize_type.multiplier` 上，位置在链偏上游。
    scale = None
    for _nid in [apply_id, *chain]:
        _in = (nodes.get(_nid) or {}).get("inputs") or {}
        for f in _SCALE_FIELD_PREFS:
            if f not in _in:
                continue
            if f == "resize_type.multiplier":
                # 只有「按倍率缩放」模式下这个字段才生效，别的模式（改边长/目标尺寸）不认
                _mode = str(_in.get("resize_type") or "").strip().lower()
                if "multiplier" not in _mode:
                    continue
            scale = _numeric_target(nodes, _nid, f)
            if scale:
                scale["field_name"] = f
                scale["node_class"] = nodes[_nid].get("class_type") or ""
                break
        if scale:
            break

    # 种子：采样器（单步扩散式放大）优先，其次处理节点与整条处理链
    seed = None
    for nid in ([sampler_id] if sampler_id else []) + [apply_id, *chain]:
        _in = (nodes.get(nid) or {}).get("inputs") or {}
        for f in _SEED_FIELD_PREFS:
            v = _in.get(f)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                seed = {"node": nid, "field": f, "default": int(v)}
                break
        if seed:
            break

    save_in = _save_in
    roles = {
        "kind": "upscale",
        # 单步采样器型（SeedVR2）才有采样器；无采样器型（VOSR2）为 ""
        "sampler": sampler_id,
        "positive": None,
        "negative": None,
        "latent": None,
        "image_node": image_id,
        "save": {
            "node": save_id,
            "class_type": nodes[save_id].get("class_type"),
            "has_quality": "quality" in save_in,
            "has_output_ext": "output_ext" in save_in,
            "quality": save_in.get("quality") if isinstance(save_in.get("quality"), (int, float)) else None,
            "output_ext": save_in.get("output_ext") if isinstance(save_in.get("output_ext"), str) else None,
        },
        "apply": {"node": apply_id, "class_type": apply.get("class_type")},
        "loader": {"node": loader_id, "class_type": loader_class},
        "model_file": model_file,
        "chain_nodes": chain,
        "aux_nodes": aux_nodes,
        "scale": scale,          # {"node","field","default","field_name","node_class"} 或 None
        "seed": seed,            # {"node","field","default"} 或 None
        "cleanup_nodes": [nid for nid, n in nodes.items() if _is_cleanup(n)],
        # 纯放大工作流没有「出图工作流内部那条放大链」，显式置空：
        # 避免被 bypass / inject 那套（针对出图工作流的）逻辑误用。
        "upscale": None,
        "subgraph_ids": [k for k in nodes if ":" in str(k)],
    }
    return roles, []


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
    # v7.7.1：采样器不是 1 个时，先试「纯放大 / 纯处理」工作流；
    # v7.7.12：把**单步采样器型**也纳入（SeedVR2 3B 这类：有 KSampler(steps=1)，
    # 但条件来自 SeedVR2Conditioning，链上没有任何文本编码器）。
    # 判定很严 —— 必须有图输入 + 保存节点 + 完全没有文本编码器 + 采样器的正/负向
    # 确实定位不到可写文本框，所以不会把「丢了采样器的出图工作流」误收成放大类。
    if len(samplers) <= 1:
        _up_roles, _up_errors = _parse_upscale(nodes)
        if _up_roles is not None:
            return _up_roles, []
        if _up_errors:
            return None, _up_errors
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
            errors.append(
                "无法定位正向提示词的可写入文本节点（沿 positive 连线及其上游都没有文本输入框；"
                "模型名/VAE 名这类资源字段不算）。若这是**纯放大/超分工作流**（本来就不吃提示词），"
                "请检查它是否有采样器、或条件是否来自 SeedVR2Conditioning 这类非文本节点——"
                "这类工作流请按「放大工作流」上传（在「更多功能 → 图片放大」里绑定）。"
            )
        else:
            pnode = nodes[found[0]]
            roles["positive"] = {"node": found[0], "field": found[1], "class_type": pnode.get("class_type")}
            # v7.7.13：记下工作流里的**当前提示词**（供「更多功能」表单预填，如抠图）
            _pt = (pnode.get("inputs") or {}).get(found[1])
            if isinstance(_pt, str):
                roles["positive"]["default_text"] = _pt
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
            errors.append(
                "无法定位负向提示词的可写入文本节点（资源名字段不算文本框）。"
                "若这是纯放大/超分工作流，属正常现象，请按「放大工作流」上传。"
            )
        else:
            nnode = nodes[found[0]]
            roles["negative"] = {"node": found[0], "field": found[1], "class_type": nnode.get("class_type")}
            _nt = (nnode.get("inputs") or {}).get(found[1])
            if isinstance(_nt, str):
                roles["negative"]["default_text"] = _nt
            # v7.7.4：负向与正向落在**同一个写入点**时标记出来。典型来源是工作流拿
            # `ConditioningZeroOut(正向编码)` 当负向（Z-Image / Qwen 系常见写法）——
            # 这种工作流**没有独立的负向输入**，出图时必须跳过负向注入，
            # 否则会把刚写好的正向提示词覆盖掉（画面完全不理会用户描述）。
            if (roles.get("positive")
                    and roles["positive"].get("node") == found[0]
                    and str(roles["positive"].get("field") or "") == str(found[1])):
                roles["negative"]["shared_with_positive"] = True

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

    # ---- 主模源：沿 model 链上溯（穿透 LoRA 与模型修饰节点），全图主模加载节点必须 ≤1 ----
    model_link = _link(s_inputs.get("model"))
    base_id, lora_chain, patch_chain = (None, [], [])
    if model_link:
        base_id, lora_chain, patch_chain = _walk_up_model(nodes, model_link[0])
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
    # 途经的「模型修饰节点」（ModelSampling* / 模型补丁等，v7.7.3）：主模已**穿透**它们
    # 回溯到真正的加载器，这里留档供展示与排查（出图时【底模】日志会打印）
    roles["model_patch_nodes"] = patch_chain
    roles["model_patch_class"] = (
        (nodes.get(patch_chain[0]) or {}).get("class_type") if patch_chain else None
    )
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
    # v7.7.14：预处理缩放节点（如 ImageScaleToTotalPixels）——按总像素归一化，
    # 小图也会被插值放大；抠图等场景据此改成「只缩不放」（见 main.py _apply_matting_pixel_target）
    _pre_scale = _find_pre_scale(nodes, roles["image_node"])
    if _pre_scale:
        roles["pre_scale"] = _pre_scale

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
