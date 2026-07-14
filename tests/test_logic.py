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
    assert prompt["11"]["inputs"]["strength_model"] == 0.0  # anime 默认禁用

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


if __name__ == "__main__":
    test_workflow_logic()
    asyncio.run(test_integration())
    asyncio.run(test_danbooru())
    print("\n全部测试通过 ✅")
