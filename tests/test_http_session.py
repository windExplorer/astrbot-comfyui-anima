# -*- coding: utf-8 -*-
"""共享 HTTP 会话池自检（v7.8.12）。

覆盖：
1. 按 trust_env 分桶，同桶复用、异桶隔离；
2. importlib.reload(http_session) 后池不丢（池挂在 aiohttp 模块上，
   这是热重载不泄漏旧连接池的关键）；
3. close_all 后全部关闭，再取得到新会话；
4. nai / danbooru / translate 三个客户端不再直接构造 ClientSession，
   且 main.terminate 统一关闭池。

跑法：uv run --no-project --python 3.12 --with aiohttp python tests/test_http_session.py
"""
import asyncio
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import http_session  # noqa: E402


async def _check_pool():
    s_true = http_session.get_session(True)
    assert http_session.get_session(True) is s_true, "同桶未复用连接"

    s_false = http_session.get_session(False)
    assert s_false is not s_true, "trust_env 不同却共用同一会话（配置会串味）"
    assert http_session.get_session(False) is s_false

    # 热重载：main.py 的初始化会 reload 本模块，池必须存活
    importlib.reload(http_session)
    assert http_session.get_session(True) is s_true, "reload 后会话池丢失（旧连接池会泄漏）"
    assert http_session.get_session(False) is s_false

    await http_session.close_all()
    assert s_true.closed and s_false.closed, "close_all 未关闭全部会话"
    again = http_session.get_session(True)
    assert again is not s_true and not again.closed
    await http_session.close_all()
    print("PASS 分桶复用 / reload 存活 / 统一关闭")


async def _check_new_loop():
    s = http_session.get_session(True)
    assert not s.closed
    assert http_session._pool()[True][0] is asyncio.get_running_loop(), (
        "跨 event loop 仍在复用旧会话（请求会抛 Event loop is closed）"
    )
    print("PASS 跨 event loop 换会话")


def test_http_session_pool():
    asyncio.run(_check_pool())
    # 第二个 event loop：不能复用绑定在旧 loop 上的会话
    asyncio.run(_check_new_loop())
    asyncio.run(http_session.close_all())


def test_clients_use_shared_pool():
    for name in ("nai_client", "danbooru_client", "translate_client"):
        mod = importlib.import_module(name)
        src = Path(mod.__file__).read_text(encoding="utf-8-sig")
        assert "aiohttp.ClientSession(" not in src, f"{name} 仍在直接构造 ClientSession"
        assert "http_session.get_session(" in src, f"{name} 未接入共享池"

    main_src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    assert "await http_session.close_all()" in main_src, "terminate 未关闭共享会话池"
    assert '"http_session"' in main_src, "http_session 未加入热重载列表"
    assert "http_session.py" in (ROOT / "build_zip.ps1").read_text(encoding="utf-8-sig"), (
        "http_session.py 未加入打包清单"
    )
    print("PASS 客户端接入共享池 / 生命周期与清单齐全")


if __name__ == "__main__":
    test_http_session_pool()
    test_clients_use_shared_pool()
    print("http session OK")
