"""本地测试：无需真实 ComfyUI / Danbooru 即可验证插件核心逻辑。

运行：
    cd astrbot-comfyui-anima
    pip install aiohttp
    python tests/test_logic.py
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import workflow_builder
import comfyui_client
import danbooru_client
import translate_client
from mock_comfyui import start_mock

WF_PATH = os.path.join(HERE, "sample_workflow.json")

LORAS = [
    {
        "name": "catgirl",
        "enabled": True,
        "weight": 1.0,
        "load_node": "10",
        "model_name": "catgirl.safetensors",
        "keywords": "猫娘,catgirl",
    },
    {
        "name": "anime",
        "enabled": False,
        "weight": 0.6,
        "load_node": "11",
        "model_name": "anime.safetensors",
        "keywords": "",
    },
]


def test_workflow_logic():
    print("== 1. 工作流注入逻辑 ==")
    prompt = workflow_builder.load_workflow(path=WF_PATH)
    assert workflow_builder.set_text_node(prompt, "6", "text", "1girl, cat ears")
    assert workflow_builder.set_text_node(prompt, "7", "text", "lowres, bad")
    assert workflow_builder.set_number_node(prompt, "8", "width", 768)
    assert workflow_builder.set_number_node(prompt, "8", "height", 768)

    enabled = workflow_builder.apply_loras(prompt, LORAS)
    print("   默认启用:", enabled)
    assert enabled == ["catgirl"]
    assert prompt["10"]["inputs"]["strength_model"] == 1.0
    # anime 默认禁用：节点被真删除（不再存在于工作流图中）
    assert "11" not in prompt

    prompt2 = workflow_builder.load_workflow(path=WF_PATH)
    enabled2 = workflow_builder.apply_loras(
        prompt2, LORAS, active_map={"catgirl": 0.8, "anime": None}
    )
    print("   指定启用:", enabled2)
    assert set(enabled2) == {"catgirl", "anime"}
    assert prompt2["10"]["inputs"]["strength_model"] == 0.8
    assert prompt2["11"]["inputs"]["strength_model"] == 0.6  # 用默认权重

    kw = workflow_builder.collect_keyword_loras(LORAS, "画一只猫娘少女")
    print("   关键词匹配:", kw)
    assert kw == {"catgirl"}
    print("   OK")


def test_true_disable_relink():
    print("== 1b. 真禁用：删除节点并重接上下游 ==")
    prompt = workflow_builder.load_workflow(path=WF_PATH)
    # 让 save 节点直接消费 LoRA 链末端 node 11，验证删除后重接到上游
    prompt["save"]["inputs"]["images"] = ["11", 0]
    # 全部禁用：node 10、11 都应被删除，链重接到底模 node 4
    workflow_builder.apply_loras(
        prompt, LORAS, active_map={}  # 空 map = 全部禁用
    )
    assert "10" not in prompt
    assert "11" not in prompt
    # save 原来消费 [11,0]，穿过 11->10->4 后应重接到 [4,0]
    assert prompt["save"]["inputs"]["images"] == ["4", 0]
    print("   重接结果:", prompt["save"]["inputs"]["images"])
    print("   OK")


def test_lora_inject():
    print("== 1c. 无 LoraLoader 时按配置注入 ==")
    prompt = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "base.safetensors"},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "1girl", "clip": ["4", 1]},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {"model": ["4", 0], "positive": ["6", 0]},
        },
    }
    loras = [
        {"name": "catgirl", "model_name": "catgirl.safetensors", "weight": 0.8,
         "enabled": True, "load_node": ""},
        {"name": "rain", "model_name": "rain.safetensors", "weight": 1.0,
         "enabled": False, "load_node": ""},  # 禁用：不注入
    ]
    enabled = workflow_builder.apply_loras(prompt, loras)
    print("   注入启用:", enabled)
    assert enabled == ["catgirl"]
    # 默认 model_only=True -> 注入 LoraLoaderModelOnly（无 clip 输入）
    new_ids = [nid for nid, n in prompt.items() if "LoraLoader" in n["class_type"]]
    assert len(new_ids) == 1  # 只注入启用的 catgirl，禁用的 rain 不注入
    nid = new_ids[0]
    assert prompt[nid]["class_type"] == "LoraLoaderModelOnly"
    assert prompt[nid]["inputs"]["lora_name"] == "catgirl.safetensors"
    assert prompt[nid]["inputs"]["strength_model"] == 0.8
    # 新节点上游接锚点底模；仅模型不改 clip 路（CLIP 编码仍直连底模）
    assert prompt[nid]["inputs"]["model"] == ["4", 0]
    assert "clip" not in prompt[nid]["inputs"]
    assert prompt["3"]["inputs"]["model"] == [nid, 0]
    assert prompt["6"]["inputs"]["clip"] == ["4", 1]
    print("   OK")


def test_lora_inject_separated():
    print("== 1d. 分离式底模（UNETLoader + CLIPLoader）按配置注入（完整模式）==")
    prompt = {
        "4": {"class_type": "UNETLoader", "inputs": {"ckpt_name": "base.safetensors"}},
        "5": {"class_type": "CLIPLoader", "inputs": {"ckpt_name": "base.safetensors"}},
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "1girl", "clip": ["5", 1]},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {"model": ["4", 0], "positive": ["6", 0]},
        },
    }
    loras = [
        {"name": "catgirl", "model_name": "catgirl.safetensors", "weight": 0.8,
         "enabled": True, "load_node": ""},
    ]
    # 显式完整模式（model_only=False）以校验 clip 路接线
    enabled = workflow_builder.apply_loras(prompt, loras, model_only=False)
    print("   注入启用:", enabled)
    assert enabled == ["catgirl"]
    new_ids = [nid for nid, n in prompt.items() if n["class_type"] == "LoraLoader"]
    assert len(new_ids) == 1
    nid = new_ids[0]
    # 新节点 model 接 UNETLoader、clip 接 CLIPLoader（两路来源不同）
    assert prompt[nid]["inputs"]["model"] == ["4", 0]
    assert prompt[nid]["inputs"]["clip"] == ["5", 1]
    # 下游采样器 / CLIP 编码已改接到新节点
    assert prompt["3"]["inputs"]["model"] == [nid, 0]
    assert prompt["6"]["inputs"]["clip"] == [nid, 1]
    print("   OK")


def test_lora_inject_no_anchor():
    print("== 1e. 无 LoraLoader 且探测不到锚点：告警且不注入 ==")
    # 既没有底模加载节点、也没有采样器/CLIP 编码引用任何上游 -> 无法定位锚点
    prompt = {
        "1": {"class_type": "Note", "inputs": {}},
    }
    loras = [
        {"name": "catgirl", "model_name": "catgirl.safetensors", "weight": 0.8,
         "enabled": True, "load_node": ""},
    ]
    warns = []
    enabled = workflow_builder.apply_loras(
        prompt, loras, on_warning=lambda m: warns.append(m)
    )
    print("   告警数:", len(warns), " 启用:", enabled)
    assert enabled == []                       # 未注入任何 LoRA
    assert len(warns) == 1                    # 且产生了告警
    assert "未生效" in warns[0]
    assert "LoraLoader" not in str(
        [n.get("class_type") for n in prompt.values()]
    )
    print("   OK")


async def test_integration():
    print("== 2. ComfyUI 全链路（mock）==")
    server, base_url = start_mock()
    client = comfyui_client.ComfyUIClient(base_url)
    prompt = workflow_builder.load_workflow(path=WF_PATH)
    workflow_builder.set_text_node(prompt, "6", "text", "1girl")
    res = await client.queue_prompt(prompt)
    pid = res["prompt_id"]
    print("   提交 prompt_id:", pid)

    # 注意：队列位置提示已改为插件内本地队列（见 main.ComfyUIDrawPlugin 的
    # _server_pending / _local_queue_*），不再依赖 ComfyUI 的 /queue 接口，
    # 因此这里只验证“提交 -> 等待完成 -> 取图 -> 下载”的全链路。
    print("   已提交，等待出图（本地队列位置由插件统计，此处不再查询 /queue）")

    entry = await client.wait_for_result(pid, timeout=5, interval=0.3)
    assert entry is not None
    images = comfyui_client.extract_images(entry, output_node="save")
    print("   输出图片:", images)
    assert images
    data = await client.get_image(
        images[0]["filename"], images[0]["subfolder"], images[0]["type"]
    )
    assert data[:4] == b"\x89PNG"
    print("   下载图片大小:", len(data), "bytes")
    await client.close()
    server.shutdown()
    print("   OK")


async def test_danbooru():
    print("== 3. Danbooru 标签翻译（mock）==")
    server, base_url = start_mock()
    client = danbooru_client.DanbooruClient(base_url, api_path="/api/search")
    tags = await client.search("一只猫娘水手服少女")
    print("   翻译标签:", tags)
    assert "cat_ears" in tags
    server.shutdown()
    print("   OK")


async def test_translate_api():
    print("== 4. 通用 HTTP 翻译接口（mock）==")
    server, base_url = start_mock()
    # 默认：POST + json 请求体 + 结果字段 data.translated
    client = translate_client.TranslateApiClient(
        base_url + "/api/translate",
        method="POST",
        text_field="text",
        json_body=True,
        result_field="data.translated",
    )
    out = await client.translate("一只猫娘水手服少女")
    print("   翻译结果:", out)
    assert "cat_ears" in out

    # append_original：结果末尾追加原文
    client2 = translate_client.TranslateApiClient(
        base_url + "/api/translate",
        method="POST",
        text_field="text",
        json_body=True,
        result_field="data.translated",
        append_original=True,
    )
    out2 = await client2.translate("猫娘")
    print("   追加原文结果:", out2)
    # append_original：原文在前，英文结果在后（与 danbooru 的 append_original 语义一致）
    assert out2.startswith("猫娘")
    assert "cat_ears" in out2

    # 请求失败（接口不存在）应抛 RuntimeError 而非静默
    bad = translate_client.TranslateApiClient(
        base_url + "/no-such-endpoint",
        method="POST",
        text_field="text",
        json_body=True,
        result_field="data.translated",
    )
    try:
        await bad.translate("测试")
        raise AssertionError("应抛异常")
    except RuntimeError as e:
        print("   失败路径 OK:", str(e)[:30], "...")
    server.shutdown()
    print("   OK")


if __name__ == "__main__":
    test_workflow_logic()
    test_true_disable_relink()
    test_lora_inject()
    test_lora_inject_separated()
    test_lora_inject_no_anchor()
    asyncio.run(test_integration())
    asyncio.run(test_danbooru())
    asyncio.run(test_translate_api())
    print("\n全部测试通过 ✅")
