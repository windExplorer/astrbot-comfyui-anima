# -*- coding: utf-8 -*-
"""共享 aiohttp Session 池（v7.8.12）。

统一此前分散在 nai_client / danbooru_client / translate_client 的会话策略：

- 按 `trust_env` 分桶。aiohttp 默认忽略 HTTP(S)_PROXY / NO_PROXY 环境变量，
  走系统代理部署的 NAI 链路需要 trust_env=True；本地 Danbooru / 翻译服务
  走直连用 False。两桶互不干扰。
- **headers / timeout / proxy 一律走请求级参数**，不放进 session 构造。
  这些值随用户、端点、配置变化，放进去会串味（典型事故：Bearer token 被
  上一个请求带到下一个不同用户的请求上）。
- 会话绑定创建它的 event loop。loop 变了（测试里多个 asyncio.run、或
  AstrBot 重建 loop）必须换新的，否则请求会在已关闭的 loop 上抛
  `Event loop is closed`——这是实测踩过的坑，不是理论风险。
- 池挂在 aiohttp 模块对象上，而不是本模块的全局变量：本模块在 main.py 的
  热重载列表里，`importlib.reload` 会重建模块级变量；挂在 aiohttp 上则
  跨 reload 保留同一批连接，既复用连接也不泄漏旧池。插件卸载时由
  `main.terminate()` 调用 `close_all()` 统一关闭。
"""
from __future__ import annotations

import asyncio

import aiohttp

_POOL_ATTR = "_astrbot_anima_http_sessions"


def _pool() -> dict:
    """值形如 {(trust_env): (loop, session)}。"""
    pool = getattr(aiohttp, _POOL_ATTR, None)
    if pool is None:
        pool = {}
        setattr(aiohttp, _POOL_ATTR, pool)
    return pool


def get_session(trust_env: bool = True) -> aiohttp.ClientSession:
    """取共享会话（不存在 / 已关闭 / loop 变了就新建）。需在 async 上下文中调用。"""
    loop = asyncio.get_running_loop()
    key = bool(trust_env)
    pool = _pool()
    entry = pool.get(key)
    if entry is not None:
        old_loop, s = entry
        if old_loop is loop and not s.closed:
            return s
        # loop 变了：旧会话不能在其它 loop 上使用。安排在当前 loop 上异步
        # 关闭——直接调 s.close() 会得到未 await 的 coroutine（实测 RuntimeWarning）。
        # 旧 connector 绑定的是已关闭的 loop，关闭时可能抛异常，用 done callback
        # 消费掉，避免 "Task exception was never retrieved"。
        try:
            if not s.closed:
                task = loop.create_task(s.close())
                task.add_done_callback(
                    lambda t: t.exception() if not t.cancelled() else None
                )
        except Exception:
            pass
    s = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            limit=8, ttl_dns_cache=300, enable_cleanup_closed=True
        ),
        trust_env=key,
    )
    pool[key] = (loop, s)
    return s


async def close_all() -> None:
    """关闭池内所有会话（插件卸载时调用）。重复调用安全。"""
    pool = _pool()
    entries = list(pool.values())
    pool.clear()
    for _, s in entries:
        try:
            if not s.closed:
                await s.close()
        except Exception:
            pass
