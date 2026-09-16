"""角色卡片（character_store / character）回归测试。

真实跑存储层与渲染逻辑（两者都不依赖 AstrBot，可直接执行）：

    uv run --no-project --python 3.12 python tests/test_character.py

覆盖：
- 建卡/锚点、主锚点自动设置、名字/别名/人格匹配；
- 文本命中顺序、用户点名锚点优先；
- 「你」→ 当前会话人格绑定卡（v5.16.1 修正过「你和」语序，这里是防回归点）；
- 注入渲染：单角色追加、多角色计数标签 + 每角色权重分组、剥掉模型自带分组、
  画质前缀置顶、组内不带计数标签、负向合并、关联 LoRA 收集；
- 开关行为（enabled / auto_bind_persona / auto_group_multi）；
- 更新/切换主锚点/删除（连带锚点）/导出导入。
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import character  # noqa: E402
from character_store import CharacterStore  # noqa: E402

_ok = 0
_fail = 0


def check(title: str, cond: bool, detail: str = ""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  ✓ {title}")
    else:
        _fail += 1
        print(f"  ✗ {title}  {detail}")


class DummyPlugin:
    """只提供 character.py 用到的两个接口。"""

    def __init__(self, store, cfg):
        self.character = store
        self._c = cfg

    def _cfg(self, key, default=None):
        if key == "character_card":
            return self._c
        return default


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="charcard_test_"))
    store = CharacterStore(tmp)
    print(f"临时库: {store.db_path}")

    cfg = {"enabled": True, "auto_bind_persona": True, "auto_inject": True,
           "auto_group_multi": True, "default_weight": 1.2}
    plugin = DummyPlugin(store, cfg)

    print("\n[1] 建卡 + 锚点")
    ji = store.create_character("小叽", aliases=["小叽酱", "叽叽"], persona_name="小叽V4")
    store.add_anchor(ji["id"], "默认装",
                     "1girl, white hair, heterochromia, cat ears, white knit sweater",
                     weight=1.3)
    store.add_anchor(ji["id"], "泳装",
                     "1girl, white hair, heterochromia, white one-piece swimsuit",
                     weight=1.2)
    mint = store.create_character("薄荷", lora_name="薄荷")
    store.add_anchor(mint["id"], "默认装",
                     "mint_\\(nte\\), 1girl, teal hair, long hair, red eyes, cat ears, black corset",
                     weight=1.2, negative="bad hands")
    check("角色数=2", len(store.list_characters()) == 2)
    check("首个锚点自动设为主锚点", int(store.get_character(ji["id"])["primary_anchor_id"]) > 0)
    check("锚点数=2", len(store.list_anchors(ji["id"])) == 2)

    print("\n[2] 名字 / 别名 / 模糊匹配")
    check("按名命中", (store.get_character("小叽") or {}).get("id") == ji["id"])
    check("按别名命中", (store.get_character("叽叽") or {}).get("id") == ji["id"])
    check("按人格命中", (store.find_by_persona("小叽V4") or {}).get("id") == ji["id"])
    check("大小写不敏感", (store.get_character("小叽V4") or {}).get("id") == ji["id"])

    print("\n[3] 文本命中（match_characters）")
    hits = store.match_characters("画一张你和薄荷站一起的图")
    check("命中薄荷", [c["name"] for c in hits] == ["薄荷"], str([c["name"] for c in hits]))
    hits2 = store.match_characters("小叽和薄荷的合照")
    check("按出现顺序命中", [c["name"] for c in hits2] == ["小叽", "薄荷"], str([c["name"] for c in hits2]))

    print("\n[4] 锚点选择（用户点名锚点优先）")
    pair = character.collect_hits(plugin, "小叽", prefer_anchor_text="小叽穿泳装")
    check("点名泳装", pair and pair[0][1]["name"] == "泳装", str(pair and pair[0][1]["name"]))
    pair2 = character.collect_hits(plugin, "小叽", prefer_anchor_text="画小叽")
    check("未点名→主锚点", pair2 and pair2[0][1]["name"] == "默认装", str(pair2 and pair2[0][1]["name"]))

    print("\n[5] resolve_hits：自称「你」→ 当前人格绑定卡")

    class DummyEvent:
        message_str = "画一张你和薄荷的合照"
        unified_msg_origin = "mumu:FriendMessage:1"

        def get_platform_name(self):
            return "mumu"

    class DummyPersonaMgr:
        async def get_default_persona_v3(self, umo=None):
            return {"name": "小叽V4"}

    class DummyContext:
        persona_manager = DummyPersonaMgr()

    plugin.context = DummyContext()
    plugin._last_event = None
    # ★防回归：「你和」语序（v5.16.0 误写成「和你」，导致最典型的请求命中不了人格卡）
    hits3 = await character.resolve_hits(plugin, DummyEvent(), DummyEvent.message_str, "masterpiece, 2girls, hugging, beach")
    check("命中 2 个角色（人格+薄荷）",
          [c["name"] for c, _a in hits3] == ["小叽", "薄荷"], str([c["name"] for c, _a in hits3]))

    print("\n[6] 注入：单人 / 双人 / 计数标签 / 画质前缀 / 剥掉模型分组")
    r1 = character.inject(plugin, "masterpiece, best quality, sitting by the window", [[ji, store.get_anchor(ji["id"], "默认装")]], cfg)
    check("单角色追加", "white hair" in r1["prompt"] and r1["prompt"].startswith("masterpiece"), r1["prompt"])

    r2 = character.inject(
        plugin,
        "masterpiece, best quality, 2girls, (zero_nte, grey hair:1.2), standing side by side, city street",
        [(ji, store.get_anchor(ji["id"], "默认装")), (mint, store.get_anchor(mint["id"], "默认装"))],
        cfg,
    )
    p2 = r2["prompt"]
    check("多角色模式", r2["mode"] == "multi")
    check("画质前缀置顶", p2.startswith("masterpiece, best quality"), p2)
    check("计数标签 2girls 且在分组前", p2.index("2girls") < p2.index("("), p2)
    check("两个分组", p2.count(":1.30)") == 1 and p2.count(":1.20)") == 1, p2)
    check("模型自带分组被剥掉", "(zero_nte, grey hair:1.2)" not in p2 and "grey hair" not in p2, p2)
    check("分组内无计数标签", "(1girl," not in p2 and "cat ears, white knit sweater:1.30)" in p2, p2)
    check("保留场景标签", "standing side by side" in p2 and "city street" in p2, p2)
    check("负向合并", r2["negative"] == "bad hands", r2["negative"])
    check("关联 LoRA 收集", r2["loras"] == ["薄荷"], str(r2["loras"]))

    print("\n[6b] 一个角色多个锚点 + 锚点选择")
    check("一个角色可有多个锚点", len(store.list_anchors(ji["id"])) == 2,
          str([a["name"] for a in store.list_anchors(ji["id"])]))
    check("按名取锚点", (store.get_anchor(ji["id"], "泳装") or {}).get("name") == "泳装")
    check("不传名取主锚点", (store.get_anchor(ji["id"]) or {}).get("name") == "默认装",
          str((store.get_anchor(ji["id"]) or {}).get("name")))
    _h = character.collect_hits(plugin, "画一张小叽泳装", prefer_anchor_text="画一张小叽泳装")
    check("用户提锚点名 → 选中该锚点", bool(_h) and _h[0][1]["name"] == "泳装",
          str([(c["name"], a["name"]) for c, a in _h]))
    _h2 = character.collect_hits(plugin, "画一张小叽", prefer_anchor_text="画一张小叽")
    check("未提锚点名 → 用主锚点", bool(_h2) and _h2[0][1]["name"] == "默认装",
          str([(c["name"], a["name"]) for c, a in _h2]))
    _h3 = character.collect_hits(plugin, "画一张小叽泳装", prefer_anchor_text="画一张小叽")
    check("锚点名只在提示词里也算数（用户原话优先）",
          bool(_h3) and _h3[0][1]["name"] == "默认装",
          str([(c["name"], a["name"]) for c, a in _h3]))

    print("\n[7] 一男一女 / 三女 计数推断")
    boy = store.create_character("阿明")
    store.add_anchor(boy["id"], "默认装", "1boy, black hair, school uniform")
    r3 = character.inject(plugin, "masterpiece, holding hands", [(boy, store.get_anchor(boy["id"])), (mint, store.get_anchor(mint["id"]))], cfg)
    check("1girl 1boy", r3["prompt"].startswith("masterpiece, 1girl 1boy"), r3["prompt"])
    r4 = character.inject(plugin, "masterpiece, stage", [
        (ji, store.get_anchor(ji["id"])), (mint, store.get_anchor(mint["id"])), (boy, store.get_anchor(boy["id"]))
    ], cfg)
    check("2girls 1boy", "2girls 1boy" in r4["prompt"], r4["prompt"])

    print("\n[8] 关闭开关的行为")
    r5 = character.inject(plugin, "masterpiece, 2girls, hugging", [
        (ji, store.get_anchor(ji["id"])), (mint, store.get_anchor(mint["id"]))
    ], {**cfg, "auto_group_multi": False})
    check("auto_group_multi=false 退化平铺", r5["mode"] == "multi" and ":1.2" not in r5["prompt"], r5["prompt"])
    off = await character.resolve_hits(plugin, DummyEvent(), "画你", "1girl")
    check("「画你」命中人格卡", [c["name"] for c, _a in off] == ["小叽"], str([c["name"] for c, _a in off]))
    plugin._c = {**cfg, "enabled": False}
    off2 = await character.resolve_hits(plugin, DummyEvent(), DummyEvent.message_str, "1girl")
    check("enabled=false 不命中", off2 == [], str(off2))
    plugin._c = {**cfg, "auto_bind_persona": False}
    off3 = await character.resolve_hits(plugin, DummyEvent(), DummyEvent.message_str, "1girl")
    check("auto_bind_persona=false 只按名字命中", [c["name"] for c, _a in off3] == ["薄荷"], str([c["name"] for c, _a in off3]))
    plugin._c = cfg

    print("\n[9] 更新 / 主锚点 / 删除 / 导出导入")
    store.update_character(mint["id"], lora_name="薄荷2")
    check("更新字段", store.get_character(mint["id"])["lora_name"] == "薄荷2")
    store.set_primary_anchor(ji["id"], "泳装")
    check("主锚点切换", store.get_anchor(ji["id"])["name"] == "泳装")
    export = store.export_all()
    check("导出含锚点", len(export["characters"][0]["anchors"]) >= 1)
    tmp2 = Path(tempfile.mkdtemp(prefix="charcard_import_"))
    store2 = CharacterStore(tmp2)
    n = store2.import_all(export)
    check("导入角色数一致", n == len(export["characters"]), f"{n} vs {len(export['characters'])}")
    check("导入锚点保留", len(store2.list_anchors(store2.get_character("小叽")["id"])) == 2)
    store.delete_anchor(store.get_anchor(ji["id"], "泳装")["id"])
    check("删锚点后主锚点顺延", store.get_anchor(ji["id"]) is not None)
    store.delete_character(boy["id"])
    check("删角色连带锚点", store.get_character("阿明") is None and store.list_anchors(boy["id"]) == [])

    print("\n[10] 参考图落地（M3）")
    _img = b"\x89PNG\r\n\x1a\n" + b"x" * 64          # 内容不重要，store 只做内容寻址
    _img2 = b"\x89PNG\r\n\x1a\n" + b"z" * 80
    r1 = store.store_ref_bytes(ji["id"], _img, ext=".png", note="测试")
    check("落地写文件", bool(r1) and Path(r1["path"]).exists(), str(r1 and r1.get("path")))
    check("目录名含角色名与 id",
          bool(r1) and f"_{ji['id']}" in Path(r1["path"]).parent.name,
          str(r1 and Path(r1["path"]).parent.name))
    check("sha256 已记录", bool(r1) and len(r1["sha256"]) == 64)
    r1b = store.store_ref_bytes(ji["id"], _img, ext=".png")
    check("同图去重（同记录）", bool(r1b) and r1b["id"] == r1["id"] and r1b.get("dedup") is True, str(r1b))
    r2 = store.store_ref_bytes(ji["id"], _img2, ext=".png")
    check("不同图新记录", bool(r2) and r2["id"] != r1["id"])
    _refs = store.list_refs(ji["id"])
    check("列表返回 2 张且文件存在", len(_refs) == 2 and all(x["exists"] for x in _refs), str(_refs))
    _landed = await character.land_ref(plugin, ji["id"], _img2, filename="dup.png")
    check("land_ref 复用已有（去重）", bool(_landed) and _landed.get("dedup") is True, str(_landed))
    # 联网来源受 allow_web_fetch 约束
    try:
        await character.land_ref(plugin, ji["id"], _img, filename="w.png", url="https://example.com/a.png")
        check("联网来源被拒（allow_web_fetch=false）", False, "未抛异常")
    except ValueError:
        check("联网来源被拒（allow_web_fetch=false）", True)
    # 打开 allow_web_fetch 后，联网来源允许落地并记录 url
    # （注意用**新图**：同 sha 会命中内容寻址去重、返回既有记录，url 自然为空）
    _img3 = b"\x89PNG\r\n\x1a\n" + b"w" * 96
    plugin._c = {**cfg, "allow_web_fetch": True}
    _w2 = await character.land_ref(
        plugin, ji["id"], _img3, filename="w2.png", url="https://example.com/b.png"
    )
    check("允许联网时记录来源 url", bool(_w2) and _w2.get("url") == "https://example.com/b.png", str(_w2))
    plugin._c = cfg
    # 删除：记录与文件都清理
    _path1 = Path(r1["path"])
    store.delete_ref(int(r1["id"]))
    check("删参考图（记录+文件）", store.get_ref(int(r1["id"])) is None and not _path1.exists())
    # 删角色连带参考图目录
    _t = store.create_character("临时角色X")
    store.add_anchor(_t["id"], "默认装", "1girl, red hair")
    _rt = store.store_ref_bytes(_t["id"], _img, ext=".png")
    _tdir = Path(_rt["path"]).parent
    store.delete_character(_t["id"])
    check("删角色连带参考图目录", not _tdir.exists(), str(_tdir))

    print(f"\n结果：{_ok} 通过 / {_fail} 失败")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
