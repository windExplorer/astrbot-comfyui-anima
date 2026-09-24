"""等待出图轮询的超时行为（v7.5.4）。

**现象**：排队/后端一慢，用户「再也不提示超时」。
**根因**（两个都在 `ComfyUIClient.wait_for_result`）：
1. 原实现用 `elapsed += interval` 估算已等时长 —— 只数 sleep，**请求本身的耗时完全不计**，
   后端每次 /history 慢几百毫秒至数秒时，真实等待能远超设定值；
2. 单次请求走 client 全局超时（draw_timeout + 30 ≈ 150s），一个挂死的响应就能把整轮等待
   拖过 AstrBot 的工具调用超时 → 协程被硬取消，超时提示根本没机会发出。

现在的约定：按**真实时间**判超时；单次轮询请求单独限时（interval×3，5~30s）。

跑法：python tests/test_wait_for_result.py
"""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfyui_client import ComfyUIClient  # noqa: E402


class _FakeClient(ComfyUIClient):
    """假客户端：不起服务器，用可控延时/报错/命中次数模拟后端。"""

    def __init__(self, *, delay: float = 0.0, done_after: int | None = None,
                 raise_always: bool = False, entry: dict | None = None):
        super().__init__("http://127.0.0.1:9", timeout=150)
        self.delay = delay
        self.done_after = done_after
        self.raise_always = raise_always
        self.entry = entry or {"status": {"completed": True}}
        self.calls: list[float | None] = []

    async def get_history(self, prompt_id=None, *, timeout=None):  # type: ignore[override]
        self.calls.append(timeout)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_always:
            raise RuntimeError("502 Bad Gateway")
        if self.done_after is not None and len(self.calls) >= self.done_after:
            return {prompt_id: self.entry}
        return {}


def _run(coro):
    return asyncio.run(coro)


def test_timeout_bounded_by_real_time():
    """慢后端（每次请求 1.5s）：真实等待≈timeout，而不是被请求耗时按轮次乘上去。

    旧实现只把 sleep 计入 elapsed，真实时长 ≈ (timeout/interval) × (interval + 请求耗时)
    —— 本例约 12.5s；新实现按真实时间判超时，约 timeout + 两次请求 ≈ 8s。
    """
    cli = _FakeClient(delay=1.5)
    t0 = time.monotonic()
    out = _run(cli.wait_for_result("p1", timeout=5, interval=1))
    elapsed = time.monotonic() - t0
    assert out is None, out
    assert elapsed <= 9.5, f"真实等待 {elapsed:.1f}s 太长（旧实现在这里约 12.5s）"
    assert elapsed >= 5.0, f"还没到 timeout 就返回了：{elapsed:.1f}s"
    print(f"== 1. 慢后端按真实时间超时 OK（timeout=5s，实测 {elapsed:.1f}s）")


def test_each_poll_request_is_bounded():
    """每次 /history 请求都带一个较短的上限（不是 150s 的全局超时）。"""
    cli = _FakeClient(delay=0.05)
    _run(cli.wait_for_result("p1", timeout=1, interval=2))
    assert cli.calls, "没发起过轮询"
    assert all(c is not None for c in cli.calls), f"有请求没带超时: {cli.calls}"
    assert all(5 <= c <= 30 for c in cli.calls), f"请求超时不在 5~30s: {cli.calls}"
    assert cli.calls[0] == 6, f"interval=2 时应为 6s（interval×3）：{cli.calls[0]}"
    print(f"== 2. 单次轮询请求单独限时 OK（interval=2 → {cli.calls[0]:.0f}s）")


def test_returns_when_finished():
    cli = _FakeClient(delay=0.05, done_after=3)
    out = _run(cli.wait_for_result("p1", timeout=5, interval=1))
    assert out == cli.entry, out
    assert len(cli.calls) == 3, len(cli.calls)
    print("== 3. 第 3 次轮询命中即返回 OK")


def test_backend_errors_still_timeout():
    """后端一直报错：不能卡死、不能抛出，到点返回 None（由调用方发超时提示）。"""
    cli = _FakeClient(raise_always=True, delay=0.05)
    t0 = time.monotonic()
    out = _run(cli.wait_for_result("p1", timeout=2, interval=1))
    elapsed = time.monotonic() - t0
    assert out is None and elapsed <= 3.5, (out, elapsed)
    print(f"== 4. 后端持续报错仍按时超时 OK（{elapsed:.1f}s）")


def test_instant_result_no_wait():
    cli = _FakeClient(done_after=1)
    t0 = time.monotonic()
    assert _run(cli.wait_for_result("p1", timeout=30, interval=2)) == cli.entry
    assert time.monotonic() - t0 < 1.0, "已有历史时不该先睡一轮"
    print("== 5. 结果已在历史里 → 立即返回不空等 OK")


def test_wait_budget_helper():
    """main.py 的 `_wait_timeout_with_budget`：等待超时收进整次请求预算（v7.5.4）。

    它住在 main.py（依赖 astrbot），沿用 tests/test_size_helpers.py 的 ast 摘取做法。
    """
    import ast
    import textwrap

    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")   # main.py 带 BOM
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    node = next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "_wait_timeout_with_budget")
    seg = ast.get_source_segment(src, node) or ""
    body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
    # 默认参数引用了模块级常量（预留/下限），一并摘出来 → 用的是真实取值
    ns: dict = {}
    for const in ("_WAIT_SEND_RESERVE", "_WAIT_FLOOR"):
        cnode = next(n for n in tree.body if isinstance(n, ast.Assign)
                     and any(getattr(t, "id", "") == const for t in n.targets))
        ns[const] = ast.literal_eval(cnode.value)
    exec(textwrap.dedent(body), ns)  # noqa: S102
    fn = ns["_wait_timeout_with_budget"]
    R, FLOOR = ns["_WAIT_SEND_RESERVE"], ns["_WAIT_FLOOR"]
    assert R > 0 and FLOOR > 0, (R, FLOOR)

    # 不限制（纯指令场景）：原样返回动态值
    assert fn(600, 0, 0) == (600, False)
    assert fn(600, 0, 120) == (600, False)
    # 预算 100：从头开始也只等 100-reserve（预留几秒发超时提示）——「提示发不出去」的修法
    assert fn(100, 100, 0) == (100 - R, True)
    # 动态值本来就更小 → 用动态值，不算被预算收紧
    assert fn(60, 100, 0) == (60, False)
    # 前置耗时（LLM 改写/翻译/上传）吃预算
    assert fn(100, 100, 40) == (100 - 40 - R, True)
    # 预算被吃光 → 仍至少等 floor（保证有反馈，不被框架静默取消）
    assert fn(200, 100, 95) == (FLOOR, True)
    # 硬上限比动态值大 → 动态值生效
    assert fn(100, 290, 30) == (100, False)
    # 自定义预留/下限
    assert fn(100, 100, 0, reserve=20, floor=15) == (80, True)
    assert fn(100, 100, 99, reserve=20, floor=15) == (15, True)
    print(f"== 6. 等待超时的预算收紧 OK（预留 {R}s / 下限 {FLOOR}s）")


if __name__ == "__main__":
    test_timeout_bounded_by_real_time()
    test_each_poll_request_is_bounded()
    test_returns_when_finished()
    test_backend_errors_still_timeout()
    test_instant_result_no_wait()
    test_wait_budget_helper()
    print("等待轮询超时行为全部通过")
