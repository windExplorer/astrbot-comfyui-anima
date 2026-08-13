# -*- coding: utf-8 -*-
"""临时验证 translate_client（用完即删）。"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import translate_client
from mock_comfyui import start_mock

async def main():
    server, base_url = start_mock()
    client = translate_client.TranslateApiClient(
        base_url + "/api/translate",
        method="POST",
        text_field="text",
        json_body=True,
        result_field="data.translated",
    )
    src = "一只猫娘水手服少女"
    out = await client.translate(src)
    print("result:", out)
    assert "cat_ears" in out

    client2 = translate_client.TranslateApiClient(
        base_url + "/api/translate",
        method="POST",
        text_field="text",
        json_body=True,
        result_field="data.translated",
        append_original=True,
    )
    out2 = await client2.translate("猫娘")
    print("append_original result:", out2)
    assert out2.startswith("猫娘")
    assert "cat_ears" in out2

    bad = translate_client.TranslateApiClient(
        base_url + "/no-such-endpoint",
        method="POST",
        text_field="text",
        json_body=True,
        result_field="data.translated",
    )
    try:
        await bad.translate("测试")
        raise SystemExit("should raise")
    except RuntimeError as e:
        print("error path ok:", str(e)[:40])

    server.shutdown()
    print("ALL TRANSLATE TESTS PASSED")

asyncio.run(main())
