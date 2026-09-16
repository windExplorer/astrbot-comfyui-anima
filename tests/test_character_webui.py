"""角色卡片 WebUI 接口回归测试（不依赖 AstrBot 运行时）。

用桩模块顶掉 `astrbot.api.web`，直接实例化 WebUIApi 并调用角色卡 handler，
验证「前端 → 路由 → handler → store」这一层真的通（内嵌页与独立 WebUI 共用同一批 handler）。

    uv run --no-project --python 3.12 python tests/test_character_webui.py
"""

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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


class _FakeRequest:
    """最小 aiohttp 风格 request 桩：query + json()。"""

    def __init__(self, query=None, body=None):
        self.query = query or {}
        self._body = body or {}

    async def json(self, default=None):
        return self._body


def _install_stub():
    """把 astrbot.api.web 换成桩，返回模块以便测试里替换 request/json_response。"""
    mod = types.ModuleType("astrbot.api.web")
    mod.request = _FakeRequest()

    def json_response(payload):
        return {"__kind__": "json", "payload": payload}

    def error_response(msg):
        return {"__kind__": "error", "message": str(msg)}

    def file_response(*a, **k):  # pragma: no cover - 角色卡用不到
        return {"__kind__": "file"}

    mod.json_response = json_response
    mod.error_response = error_response
    mod.file_response = file_response

    pkg_astrbot = types.ModuleType("astrbot")
    pkg_api = types.ModuleType("astrbot.api")
    pkg_astrbot.api = pkg_api
    pkg_api.web = mod
    sys.modules.setdefault("astrbot", pkg_astrbot)
    sys.modules.setdefault("astrbot.api", pkg_api)
    sys.modules["astrbot.api.web"] = mod
    return mod


def _payload(res):
    """从桩响应里取数据；错误响应直接抛，便于测试失败定位。"""
    if isinstance(res, dict) and res.get("__kind__") == "error":
        raise AssertionError("handler 返回错误: " + str(res.get("message")))
    return res.get("payload") if isinstance(res, dict) else res


async def main() -> int:
    web = _install_stub()
    import webui_api  # noqa: E402
    from character_store import CharacterStore  # noqa: E402

    tmp = Path(tempfile.mkdtemp(prefix="charweb_test_"))
    store = CharacterStore(tmp)

    class FakePlugin:
        character = store
        config = {"character_card": {"enabled": True, "auto_inject": True}}

        def _cfg(self, key, default=None):
            return self.config.get(key, default)

    api = webui_api.WebUIApi(FakePlugin())
    print("WebUIApi 实例化成功，开始跑角色卡接口\n")

    def set_req(query=None, body=None):
        # ★必须替换 webui_api 模块属性（handler 读的是它自己的模块级 request），
        # 与 standalone_webui 的适配器做法一致；只改桩模块的属性不会生效。
        webui_api.request = _FakeRequest(query, body)

    print("[1] 列表（空）")
    set_req()
    res = _payload(await api.character_list())
    check("空库返回 0 条", res["total"] == 0 and res["characters"] == [], str(res))
    check("返回 can_edit", res.get("can_edit") is True)

    print("\n[2] 新建角色 + 首个锚点（前端「新建角色」两次调用）")
    set_req(body={"name": "薄荷", "aliases": ["薄荷酱"], "persona_name": "薄荷V4", "lora_name": "薄荷"})
    r1 = _payload(await api.character_save())
    check("创建返回 id", bool(r1.get("id")) and r1.get("created") is True, str(r1))
    set_req(body={"character_id": r1["id"], "anchor_name": "默认装",
                  "positive": "mint_\\(nte\\), teal hair, red eyes, cat ears", "kind": "full", "weight": 1.2})
    r2 = _payload(await api.character_anchor_save())
    check("锚点创建", r2.get("created") is True and bool(r2.get("id")), str(r2))

    print("\n[3] 列表 / 详情（带锚点）")
    set_req(query={"keyword": "薄荷"})
    res = _payload(await api.character_list())
    check("关键字命中 1 条", res["total"] == 1, str(res["total"]))
    check("列表带锚点", len(res["characters"][0]["anchors"]) == 1, json.dumps(res["characters"][0], ensure_ascii=False))
    set_req(query={"id": str(r1["id"])})
    det = _payload(await api.character_detail())
    check("详情含锚点与主锚点", len(det["anchors"]) == 1 and det["primary_anchor_id"] == r2["id"], str(det)[:200])

    print("\n[4] 编辑角色（改名/别名串/停用）")
    set_req(body={"id": r1["id"], "name": "薄荷", "aliases": "薄荷酱,猫薄荷", "persona_name": "薄荷V4",
                  "work": "NTE", "lora_name": "薄荷2", "enabled": False})
    r3 = _payload(await api.character_save())
    check("更新而非新建", r3.get("created") is False, str(r3))
    set_req(query={"id": str(r1["id"])})
    det = _payload(await api.character_detail())
    check("别名已更新", det["aliases"] == ["薄荷酱", "猫薄荷"], str(det["aliases"]))
    check("停用已写入", det["enabled"] is False, str(det["enabled"]))

    print("\n[5] 新增第二个锚点 + 编辑 + 设主锚点")
    set_req(body={"character_id": r1["id"], "anchor_name": "泳装",
                  "positive": "mint_\\(nte\\), teal hair, white one-piece swimsuit"})
    a2 = _payload(await api.character_anchor_save())
    set_req(body={"id": a2["id"], "anchor_name": "泳装", "positive": "mint_\\(nte\\), teal hair, white bikini",
                  "weight": 1.35})
    _payload(await api.character_anchor_save())
    set_req(body={"character_id": r1["id"], "anchor_id": a2["id"]})
    prim = _payload(await api.character_anchor_primary())
    check("主锚点已切换", prim.get("primary") == "泳装", str(prim))
    set_req(query={"id": str(r1["id"])})
    det = _payload(await api.character_detail())
    sw = next(a for a in det["anchors"] if a["name"] == "泳装")
    check("锚点编辑生效", "white bikini" in sw["positive"] and abs(float(sw["weight"]) - 1.35) < 1e-6, str(sw))
    check("主锚点 id 已更新", int(det["primary_anchor_id"]) == int(a2["id"]), str(det["primary_anchor_id"]))

    print("\n[6] 删除锚点（主锚点顺延）")
    set_req(body={"id": a2["id"]})
    _payload(await api.character_anchor_delete())
    set_req(query={"id": str(r1["id"])})
    det = _payload(await api.character_detail())
    check("锚点剩 1 个", len(det["anchors"]) == 1, str(len(det["anchors"])))
    check("主锚点顺延到剩余锚点", int(det["primary_anchor_id"]) == int(det["anchors"][0]["id"]), str(det["primary_anchor_id"]))

    print("\n[7] 导出 / 导入")
    set_req()
    exp = _payload(await api.character_export())
    check("导出含角色与锚点", len(exp.get("characters") or []) == 1 and exp["characters"][0]["anchors"], str(exp)[:160])
    set_req(body={"data": exp})
    imp = _payload(await api.character_import())
    check("导入不重复（同名跳过）", imp.get("ok") is True, str(imp))
    set_req(body={"data": "not-a-dict"})
    bad = await api.character_import()
    check("非法导入返回错误", isinstance(bad, dict) and bad.get("__kind__") == "error", str(bad))

    print("\n[8] 删除角色（连带锚点）")
    set_req(body={"id": r1["id"]})
    _payload(await api.character_delete())
    set_req()
    res = _payload(await api.character_list())
    check("列表已空", res["total"] == 0, str(res["total"]))

    print("\n[9] 路由已注册（内嵌页）")
    src = (ROOT / "webui_api.py").read_text(encoding="utf-8")
    for _ep in ("character/list", "character/detail", "character/save", "character/delete",
                "character/anchor/save", "character/anchor/delete", "character/anchor/primary",
                "character/export", "character/import"):
        check(f"路由 {_ep} 已注册", f'/character/' in src and _ep.split("/", 1)[1] in src, _ep)
    ssrc = (ROOT / "standalone_webui.py").read_text(encoding="utf-8")
    check("独立 WebUI 已分派 /character/", 'startswith("/character/")' in ssrc)
    check("独立 WebUI 适配器存在", "async def _api_character(" in ssrc)

    print(f"\n结果：{_ok} 通过 / {_fail} 失败")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
