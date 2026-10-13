"""取图回归：引用/兜底误判 与「按需取图」（v7.7.59 / v7.7.60 / v7.7.61 / v7.7.63 / v7.7.64）。

背景一（v7.7.59，logs/20261010-1.log）：星语抽签调用 `comfyui_draw` 画塔罗牌，结果走了
「动漫图生图」工作流、还把用户早先发的 GIF 首帧当参考图注入——用户完全没提图。根因是
`_extract_images` 的两条兜底：① 消息带 `Reply`（哪怕引用的是纯文字）就回退历史图；
② 回退时把最近 5 张全塞进去。

背景二（v7.7.60）：预取钩子把「引用(Reply)」也当"可能含图"，于是每条带引用的消息都会去打
平台 API + 下载整张图；`on_using_llm_tool` 更是每次 LLM 工具调用都完整取一遍图，还重复
解析一次引用图。

背景三（v7.7.61，最终形态）：**预取阶段零 IO** —— 只登记"图源 id"（消息自己的 message_id /
被引用消息的 Reply.id），不解析、不打接口、不下载；只有真要参考图时（画图指令/工具及其
兜底链）才用这些 id 调平台接口 `get_msg` 把图拉回来并下载。

背景四（v7.7.63，2026-10-13 用户日志）：用户发「照片发我看看」并**引用一条纯文字消息**
（Reply.id=1576887032），却仍被判成图生图。根因是 v7.7.61 的兜底①调
`_ensure_recent_user_images(event, limit=1)` 时没限定图源，它会**回溯本会话最近 8 条登记图源**，
于是当前引用取不到图后继续往回捞到更早的 `reply:-604553423`，把一张旧图当成本次参考图。
修复：兜底①改用 `sources=` 只查**本条消息自己的引用 id**（日志里 id 对不上就是铁证）。

背景五（v7.7.64，用户拍板）：**「引用(回复)里的图」不再单独构成图生图信号**。
用户要的是"没说要改图就别图生图"，而旧判定挂在"引用里那张图能不能抠出来"上——
能不能抠取决于平台当次给不给（日志实证引用图解析失败很常见），于是同一个"回复一张图
+ 说句别的"的动作会时而图生图时而不；而用户"回复某条消息"往往只是接话。改动：
  ① `_extract_images(split=True)` 分开返回「本条自带图」与「引用来源图」；
  ② 引用图只有过了闸门（用户原话命中 `_I2I_INTENT_RE`，或 LLM 显式传 img2img_workflow，
     或配置 img2img_from_quote=always）才参与判定与取参考图；
  ③ **删掉「会话缓存兜底」**（拿会话里最近一张旧图顶替本次引用图 → 图生图 + 参考图给错）。

跑法：python tests/test_extract_images.py
"""

import ast
import asyncio
import json
import os
import re
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
    def __init__(self, chain=(), rid="r1"):
        self.type = "Reply"
        self.id = rid
        self.chain = list(chain)


class _Plain:
    def __init__(self):
        self.type = "Plain"


class _MsgObj:
    def __init__(self, mid):
        self.message_id = mid


class _Event:
    def __init__(self, comps, sid="s1", mid="m1", text=""):
        self._comps = list(comps)
        self.session_id = sid
        self.message_obj = _MsgObj(mid)
        # 用户原话（图生图意图词判据只认它，见 _user_wants_img2img_ref）
        self.message_str = text

    def get_messages(self):
        return list(self._comps)


def _load_funcs(names: tuple, ns_extra: dict | None = None) -> dict:
    """从 main.py 原样抠出插件类里的方法定义再 exec —— 测的是磁盘上的真代码。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    ns = {"logger": _Log(), "Image": _Image, "Reply": _Reply, "CardImage": None, "os": os,
          "AstrMessageEvent": object}
    if ns_extra:
        ns.update(ns_extra)
    out = {}
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), "<x>", "exec"), ns)  # noqa: S102
            out[node.name] = ns[node.name]
    return out


def _load_module_defs(names: tuple, ns_extra: dict | None = None) -> dict:
    """从 main.py 抠出**模块级**函数/常量定义再 exec（意图词判据就在模块级）。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    ns = {"re": re, "logger": _Log(), "os": os}
    if ns_extra:
        ns.update(ns_extra)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), "<m>", "exec"), ns)  # noqa: S102
            out[node.name] = ns[node.name]
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in names:
                    exec(compile(ast.get_source_segment(src, node), "<m>", "exec"), ns)  # noqa: S102
                    out[tgt.id] = ns[tgt.id]
    return out


async def _no_lazy_fetch(event, *, limit: int = 1, sources=None):
    """按需拉取的桩：本机没有 astrbot 运行时（_onebot_client_for 必然拿不到客户端），
    真实实现也会返回空；这里显式桩掉，保证"结果缓存兜底"的断言测的是缓存逻辑本身。

    注意签名**不带 self**：桩是挂在假 self 的**实例属性**上的普通函数，不做绑定，
    调用形态就是 `_extract_images` 里写的 `self._ensure_recent_user_images(event, ...)`。
    """
    return []


def _self_stub(extra: dict | None = None):
    """给实例方法用的假 self：本测试只用到 _ensure_recent_user_images 一个实例方法。"""
    stub = type("_S", (), {})()
    stub._ensure_recent_user_images = _no_lazy_fetch
    for k, v in (extra or {}).items():
        setattr(stub, k, v)
    return stub


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="anima_extract_"))
    hist = []
    for i in range(5):
        p = tmp / f"h{i}.jpg"
        p.write_bytes(b"x")
        hist.append(str(p))

    async def fake_quoted(event, reply_component=None):
        return []

    async def fake_local(item):
        return getattr(item, "resolved", None)

    def fn_extract(store, recent=None):
        return _load_funcs(("_extract_images",), {
            "g_last_received": store,
            "g_recent_user_images": recent if recent is not None else {},
            "_extract_quoted_images": fake_quoted,
            "_image_to_local_path": fake_local,
        })["_extract_images"]

    me = _self_stub()

    # ① 无引用 + 历史有图 → 绝不兜底（纯文生图不能被历史图污染成图生图）
    fn_hist = fn_extract({"s1": list(hist)})
    got = asyncio.run(fn_hist(me, _Event([_Plain()])))
    check(got == [], f"无引用时不得兜底历史图，实际 {got}")

    # ② 引用一条**纯文字**消息 + 历史有图 → 不得兜底（v7.7.59 修复点）
    got = asyncio.run(fn_hist(me, _Event([_Plain(), _Reply([_Plain()])])))
    check(got == [], f"引用纯文字消息不得兜底历史图，实际 {got}")

    # ③ 引用里确有图、但路径解析失败 → v7.7.64 起**不再用会话缓存兜底**。
    #    旧行为（v7.7.59 的兜底②）会把会话里最近 1 张用户图顶上，而那多半不是本次引用的
    #    那张 —— "文生图被整成图生图"与"参考图给错"就是它同时造成的。现在取不到就是取不到。
    got = asyncio.run(fn_hist(me, _Event([_Reply([_Image()])])))
    check(got == [], f"引用图解析失败不得再用会话缓存兜底（会拿错参考图），实际 {got}")

    # ③c v7.7.64：split=True 分开返回「本条自带图」与「引用来源图」——
    #     判定闸门（引用图是否参与图生图）就靠这个区分。
    fn_split = _load_funcs(("_extract_images",), {
        "g_last_received": {"s1": list(hist)}, "g_recent_user_images": {},
        "_extract_quoted_images": fake_quoted,
        "_image_to_local_path": fake_local,
    })["_extract_images"]
    own_s = _Image(str(tmp / "own_split.jpg"))
    ref_s = _Image(str(tmp / "ref_split.jpg"))
    got = asyncio.run(
        fn_split(me, _Event([_Plain(), own_s, _Reply([ref_s])]), split=True)
    )
    check(got == ([str(tmp / "own_split.jpg")], [str(tmp / "ref_split.jpg")]),
          f"split 应分开返回 (自带图, 引用图)，实际 {got}")
    got = asyncio.run(fn_split(me, _Event([_Plain(), own_s]), split=True))
    check(got == ([str(tmp / "own_split.jpg")], []),
          f"只有自带图时引用桶应为空，实际 {got}")

    # ③b v7.7.63：按需取图只认「本条消息自己的引用 id」，绝不回溯会话历史登记的图源。
    #     2026-10-13 线上日志：用户发「照片发我看看」+ 引用一条纯文字消息（Reply.id=1576887032），
    #     兜底①却从会话里更早登记的 reply:-604553423 拉回一张旧图 → 文生图被判成图生图
    #     → 会话沿用的文生图工作流没有 LoadImage 节点 → 出图直接失败。
    asked: list = []

    async def _spy_lazy(event, *, limit: int = 1, sources=None):
        asked.append(sources)
        return []

    spy_self = _self_stub({"_ensure_recent_user_images": _spy_lazy})
    fn_spy = _load_funcs(("_extract_images",), {
        "g_last_received": {}, "g_recent_user_images": {},
        "_extract_quoted_images": fake_quoted,
        "_image_to_local_path": fake_local,
    })["_extract_images"]
    got = asyncio.run(fn_spy(spy_self, _Event([_Plain(), _Reply([_Plain()], rid="1576887032")])))
    check(got == [], f"引用纯文字消息不得拉到图，实际 {got}")
    check(asked == [[("reply", "1576887032")]],
          f"按需取图只应查本条消息的引用 id（不得回溯历史图源），实际 {asked}")

    # ④ 消息内直接带图 → 用它，且不触发任何兜底
    own = _Image(str(tmp / "own.jpg"))
    got = asyncio.run(fn_hist(me, _Event([_Plain(), own])))
    check(got == [str(tmp / "own.jpg")], f"消息内图片应正常取出，实际 {got}")

    # ⑤ 引用图能解析 → 引用优先（v7.7.35：引用是主体、新发的是辅助）
    ref = _Image(str(tmp / "ref.jpg"))
    own2 = _Image(str(tmp / "own2.jpg"))
    got = asyncio.run(fn_hist(me, _Event([own2, _Reply([ref])])))
    check(got == [str(tmp / "ref.jpg"), str(tmp / "own2.jpg")],
          f"引用图应排在新发图之前，实际 {got}")

    # ⑥ 没有会话标识时不猜（空键桶是所有会话共用的，捞了就串味）
    got = asyncio.run(fn_hist(me, _Event([_Reply([_Image()])], sid="")))
    check(got == [], f"无 sid 时不得兜底历史图，实际 {got}")

    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")

    # ⑦ 外部插件调用的守卫：两个工具入口都必须「不自动取图」
    marker = "外部插件调用（source/external）：跳过从事件自动取图"
    check(src.count(marker) == 2,
          f"comfyui_draw 与 comfyui_img2img 都要有「外部调用不自动取图」守卫，当前 {src.count(marker)} 处")

    # ⑧ 组件判定：预取钩子只认「消息自带图片/卡片」，引用不算
    guard = _load_funcs(("_msg_has_image_comp",), {})["_msg_has_image_comp"]
    check(guard(_Image()) is True, "图片组件应判为「自带图」")
    check(guard(_Plain()) is False, "纯文本不算")
    check(guard(_Reply([_Plain()])) is True, "默认含引用：引用组件判为「可能含图」")
    check(guard(_Reply([_Plain()]), include_reply=False) is False,
          "登记/预取：仅引用的消息不得算作「自带图」")

    # ⑨ v7.7.61 图源登记：只记 id，零 IO（不解析图片、不打接口、不下载）
    bucket: dict = {}

    def _remember_with(store):
        f = _load_funcs(
            ("_msg_has_image_comp", "_reply_id_of", "_remember_image_sources"),
            {"g_image_sources": store},
        )
        s = _self_stub({
            "_msg_has_image_comp": f["_msg_has_image_comp"],
            "_reply_id_of": f["_reply_id_of"],
        })
        return f["_remember_image_sources"], s

    f, s = _remember_with(bucket)
    f(s, _Event([_Plain()], mid="m10"))
    check(bucket == {}, f"纯文本消息不得登记图源，实际 {bucket}")
    f(s, _Event([_Image()], mid="m11"))
    check(bucket.get("s1") == [("msg", "m11")], f"自带图消息应登记自己的 message_id，实际 {bucket}")
    f(s, _Event([_Reply([_Plain()], rid="r9")], mid="m12"))
    check(("reply", "r9") in (bucket.get("s1") or []),
          f"引用消息应登记被引用消息 id，实际 {bucket}")
    f(s, _Event([_Image()], mid="m11"))
    check((bucket.get("s1") or []).count(("msg", "m11")) == 1,
          f"同一图源重复登记应去重，实际 {bucket}")

    # ⑩ 接线守卫：预取钩子只登记（旧预取代码已删）、按需取图入口齐全
    check("_collect_user_images" not in src, "旧的预取取图（_collect_user_images）应已删除")
    check(src.count("_remember_image_sources(") == 4,
          f"三个预取钩子都要登记图源（+1 处定义），当前 {src.count('_remember_image_sources(')} 处")
    check(src.count("_ensure_recent_user_images(") >= 6,
          f"按需取图入口应覆盖全部兜底链（+1 处定义），当前 {src.count('_ensure_recent_user_images(')} 处")
    check("g_image_sources" in src, "缺少图源登记表 g_image_sources")

    # ⑪ v7.7.64 意图词判据：只认用户原话里「针对那张图」的说法
    wants = _load_module_defs(("_I2I_INTENT_RE", "_user_wants_img2img_ref"))
    wants_fn = wants["_user_wants_img2img_ref"]

    def _intent(text):
        return wants_fn(_Event([_Plain()], text=text))

    # 关键反例：用户这次的原话（2026-10-13 日志）—— 引用了一条消息但只想看新图
    check(_intent("照片发我看看") is False, "「照片发我看看」不得算图生图意图")
    check(_intent("画一张雪景") is False, "纯文生图描述不得算图生图意图")
    check(_intent("画一个变成人鱼的少女") is False,
          "「变成…」这类泛化动词不得算图生图意图（文生图提示词里太常见）")
    check(_intent("把头发改成粉色") is False,
          "「把头发改成粉色」是文生图的外观要求，不得算图生图意图")
    check(_intent("") is False, "拿不到原话时一律按没有意图（保守）")
    # 正例：明确针对那张图
    check(_intent("把这张改成水彩") is True, "「这张 + 改成」应算图生图意图")
    check(_intent("参考这张图的画风再画一张") is True, "「参考这张」应算图生图意图")
    check(_intent("再改一下") is True, "「再改一下」应算图生图意图（多轮改图续作）")
    check(_intent("照着上图重画") is True, "「照着…图」应算图生图意图")
    check(_intent("based on this image") is True, "英文指代也应算图生图意图")

    # ⑫ v7.7.64 接线守卫：判定段必须用 split 取图 + 闸门；旧「会话缓存兜底」必须已删
    check(src.count("_extract_images(event, split=True)") == 1,
          "llm_draw 判定段必须用 _extract_images(..., split=True) 分开自带图/引用图")
    check(src.count("_extract_images(last_ev, split=True)") == 1,
          "last_ev 兜底取图同样要 split=True")
    check("_user_wants_img2img_ref(event)" in src, "判定段必须接入用户原话意图判据")
    check("用本会话最近 1 张用户图兜底" not in src,
          "v7.7.59 的「会话缓存兜底②」（会拿错参考图）应已删除")
    check("图生图判定（最终）" in src,
          "判定日志要打一行最终结论（否则弱信号回退前那行 is_img2img=True 会误导排查）")

    # ⑬ 新配置键必须三处齐（schema + 前端分区 + 代码默认值）
    sch = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8-sig"))
    node = sch.get("img2img_from_quote") or {}
    check(node.get("default") == "intent",
          f"img2img_from_quote 默认应为 intent，实际 {node.get('default')!r}")
    check(set(node.get("options") or []) == {"intent", "always", "never"},
          f"img2img_from_quote 选项应为 intent/always/never，实际 {node.get('options')}")
    cfg_view = (ROOT / "webui-src" / "src" / "views" / "ConfigView.vue").read_text(encoding="utf-8")
    check('"img2img_from_quote"' in cfg_view,
          "新配置键必须加进 ConfigView.vue 的 GROUP_META（否则在 WebUI 掉进「其他」分区）")
    check('plugin._cfg("img2img_from_quote", "intent")' in src,
          "判定段必须读配置 img2img_from_quote（默认 intent）")

    if FAILED:
        print(f"\n{len(FAILED)} 项失败")
        return 1
    print("\n取图回归（按需取图 / 自带图与引用图拆分 / 引用图意图闸门 / 外部调用守卫）全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
