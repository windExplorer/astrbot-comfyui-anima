"""图生图误判回归：引用取图与历史兜底（v7.7.59）。

背景（2026-10-10 线上日志，logs/20261010-1.log）：星语抽签调用 `comfyui_draw` 画塔罗牌，
结果走了「动漫图生图」工作流、还把用户早先发的 GIF 首帧当参考图注入——用户完全没提图。
根因是 `_extract_images` 的两条兜底：
  ① 消息里只要带 `Reply`（哪怕引用的是**纯文字**消息）就回退到「本会话最近发过的图」；
  ② 回退时把最近 5 张全部塞进去，多图工作流于是把不相干的旧图当参考图。

本测试锁死修复后的行为：兜底必须「引用里确有图」且**只取最近 1 张**；同时守住
「外部插件调用不自动取图」这条守卫（两个工具入口都要有）。

跑法：python tests/test_extract_images.py
"""

import ast
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"FAIL: {msg}")


class _Log:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


# --- AstrBot 组件替身（真身是 astrbot 的 Image / Reply 等，本机装不了运行时）---
class _Image:
    def __init__(self, resolved=None):
        self.type = "Image"
        self.resolved = resolved


class _Reply:
    def __init__(self, chain):
        self.type = "Reply"
        self.id = 1
        self.chain = list(chain)


class _Plain:
    def __init__(self):
        self.type = "Plain"


class _Event:
    def __init__(self, comps, sid="s1"):
        self._comps = list(comps)
        self.session_id = sid

    def get_messages(self):
        return list(self._comps)


def _load_extract_images(ns_extra: dict):
    """从 main.py 原样抠出 `_extract_images` 定义再 exec —— 测的是磁盘上的真代码。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    node = next(
        n for n in cls.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_extract_images"
    )
    ns = {"logger": _Log(), "Image": _Image, "Reply": _Reply, "CardImage": None, "os": os,
          "AstrMessageEvent": object}
    ns.update(ns_extra)
    exec(compile(ast.get_source_segment(src, node), "<x>", "exec"), ns)  # noqa: S102
    return ns["_extract_images"]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="anima_extract_"))
    hist = []
    for i in range(5):
        p = tmp / f"h{i}.jpg"
        p.write_bytes(b"x")
        hist.append(str(p))
    newest = hist[-1]

    async def fake_quoted(event, reply_component=None):
        return []

    async def fake_local(item):
        return getattr(item, "resolved", None)

    def load(store, recent=None):
        return _load_extract_images({
            "g_last_received": store,
            "g_recent_user_images": recent if recent is not None else {},
            "_extract_quoted_images": fake_quoted,
            "_image_to_local_path": fake_local,
        })

    # ① 无引用 + 历史有图 → 绝不兜底（纯文生图不能被历史图污染成图生图）
    fn_hist = load({"s1": list(hist)})
    got = asyncio.run(fn_hist(_Event([_Plain()])))
    check(got == [], f"无引用时不得兜底历史图，实际 {got}")

    # ② 引用一条**纯文字**消息 + 历史有图 → 不得兜底（v7.7.59 修复点）
    got = asyncio.run(fn_hist(_Event([_Plain(), _Reply([_Plain()])])))
    check(got == [], f"引用纯文字消息不得兜底历史图，实际 {got}")

    # ③ 引用里确有图、但路径解析失败 → 兜底「最近 1 张」，不是最近 5 张
    got = asyncio.run(fn_hist(_Event([_Reply([_Image()])])))
    check(got == [newest], f"引用图解析失败应只兜底最近 1 张，实际 {got}")

    # ④ 消息内直接带图 → 用它，且不触发任何兜底
    own = _Image(str(tmp / "own.jpg"))
    got = asyncio.run(fn_hist(_Event([_Plain(), own])))
    check(got == [str(tmp / "own.jpg")], f"消息内图片应正常取出，实际 {got}")

    # ⑤ 引用图能解析 → 引用优先（v7.7.35：引用是主体、新发的是辅助）
    ref = _Image(str(tmp / "ref.jpg"))
    own2 = _Image(str(tmp / "own2.jpg"))
    got = asyncio.run(fn_hist(_Event([own2, _Reply([ref])])))
    check(got == [str(tmp / "ref.jpg"), str(tmp / "own2.jpg")],
          f"引用图应排在新发图之前，实际 {got}")

    # ⑥ 没有会话标识时不猜（空键桶是所有会话共用的，捞了就串味）
    got = asyncio.run(fn_hist(_Event([_Reply([_Image()])], sid="")))
    check(got == [], f"无 sid 时不得兜底历史图，实际 {got}")

    # ⑦ 外部插件调用的守卫：两个工具入口都必须「不自动取图」
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    marker = "外部插件调用（source/external）：跳过从事件自动取图"
    check(src.count(marker) == 2,
          f"comfyui_draw 与 comfyui_img2img 都要有「外部调用不自动取图」守卫，当前 {src.count(marker)} 处")

    if FAILED:
        print(f"\n{len(FAILED)} 项失败")
        return 1
    print("\n图生图取图回归（引用优先 / 兜底收紧 / 外部调用守卫）全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
