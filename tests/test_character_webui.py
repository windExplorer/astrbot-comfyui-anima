"""角色卡片 WebUI 接口回归测试（不依赖 AstrBot 运行时）。

用桩模块顶掉 `astrbot.api.web`，直接实例化 WebUIApi 并调用角色卡 handler，
验证「前端 → 路由 → handler → store」这一层真的通（内嵌页与独立 WebUI 共用同一批 handler）。

    uv run --no-project --python 3.12 python tests/test_character_webui.py
"""

import asyncio
import base64
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
    """最小 aiohttp 风格 request 桩：query + json() + body() + headers。

    与 standalone_webui 的 `_AioReqAdapter` 暴露的能力保持一致，确保两条通道
    走同一批 handler 时行为等价。
    """

    def __init__(self, query=None, body=None, raw: bytes = b"", headers=None):
        self.query = query or {}
        self._body = body or {}
        self._raw = raw or b""
        self.headers = headers or {}

    async def json(self, default=None):
        return self._body

    async def body(self):
        return self._raw


def _install_stub():
    """把 astrbot.api.web 换成桩，返回模块以便测试里替换 request/json_response。"""
    mod = types.ModuleType("astrbot.api.web")
    mod.request = _FakeRequest()

    def json_response(payload):
        return {"__kind__": "json", "payload": payload}

    def error_response(msg, **kwargs):  # status_code 等关键字参数由 handler 传入，桩需宽容
        return {"__kind__": "error", "message": str(msg), "kwargs": kwargs}

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
    import standalone_webui  # noqa: E402
    from character_store import CharacterStore  # noqa: E402

    StandaloneWebUI = standalone_webui.StandaloneWebUI

    tmp = Path(tempfile.mkdtemp(prefix="charweb_test_"))
    store = CharacterStore(tmp)

    # 图库桩：ref/from_gallery 需要 path_of(sha)（M3）
    _gal_img = tmp / "gallery_img.png"
    _gal_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"g" * 64)

    class _FakeGallery:
        def path_of(self, sha):
            return str(_gal_img)

        def _nsfw_threshold(self):
            return 0.5

    class _FakeDanbooru:
        async def search(self, query):
            return "mint_\\(nte\\), teal hair, long hair, red eyes, cat ears"

    class FakePlugin:
        character = store
        gallery = _FakeGallery()
        config = {"character_card": {"enabled": True, "auto_inject": True, "allow_web_fetch": False}}

        def _cfg(self, key, default=None):
            return self.config.get(key, default)

        def _build_danbooru(self):
            return _FakeDanbooru()

    api = webui_api.WebUIApi(FakePlugin())
    print("WebUIApi 实例化成功，开始跑角色卡接口\n")

    def set_req(query=None, body=None, raw: bytes = b"", headers=None):
        # ★必须替换 webui_api 模块属性（handler 读的是它自己的模块级 request），
        # 与 standalone_webui 的适配器做法一致；只改桩模块的属性不会生效。
        webui_api.request = _FakeRequest(query, body, raw, headers)

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

    print("\n[10] 参考图接口（M3）")
    set_req(body={"name": "薄荷", "aliases": [], "persona_name": "", "work": "", "lora_name": ""})
    rc = _payload(await api.character_save())
    cid = int(rc["id"])
    _img = b"\x89PNG\r\n\x1a\n" + b"r" * 64
    _b64 = base64.b64encode(_img).decode()
    # ★注意：handler 读的是 `await request.body()` 后自行解析 JSON（与 lora_upload_image 同构），
    #   所以这里把 JSON 序列化进 raw，而不是走桩的 json()。
    set_req(raw=json.dumps({
        "character_id": cid, "filename": "ref.png",
        "data": "data:image/png;base64," + _b64,
    }).encode(), headers={"content-type": "application/json"})
    up = _payload(await api.character_ref_upload())
    check("上传（JSON base64 + dataURL 前缀）", bool(up.get("id")) and up.get("dedup") is False, str(up))
    # 缺 content-type 时靠「body 以 { 开头」兜底识别为 JSON
    _img3 = b"\x89PNG\r\n\x1a\n" + b"t" * 90
    set_req(raw=json.dumps({
        "character_id": cid, "filename": "noct.png",
        "data": base64.b64encode(_img3).decode(),
    }).encode())
    up3 = _payload(await api.character_ref_upload())
    check("缺 content-type 仍能识别 JSON", bool(up3.get("id")), str(up3))
    _img2 = b"\x89PNG\r\n\x1a\n" + b"s" * 80
    set_req(raw=_img2, headers={"x-character-id": str(cid), "x-filename": "raw.png"})
    up2 = _payload(await api.character_ref_upload())
    check("上传（raw + 头，独立通道形态）", bool(up2.get("id")) and up2["id"] != up["id"], str(up2))
    set_req(body={"filename": "x.png", "data": _b64})
    _e = await api.character_ref_upload()
    check("缺 character_id 报错", isinstance(_e, dict) and _e.get("__kind__") == "error", str(_e))
    set_req(query={"id": str(up["id"])})
    im = _payload(await api.character_ref_image())
    check("缩略图返回 data URL", str(im.get("url", "")).startswith("data:image"), str(im)[:70])
    set_req(query={"id": str(cid)})
    det = _payload(await api.character_detail())
    check("详情含 3 张参考图", len(det.get("refs") or []) == 3, str(len(det.get("refs") or [])))
    set_req(body={"character_id": cid, "sha": "deadbeef"})
    fg = _payload(await api.character_ref_from_gallery())
    check("从图库导入参考图", bool(fg.get("id")), str(fg))
    set_req(body={"id": up["id"]})
    dl = _payload(await api.character_ref_delete())
    check("删除参考图", dl.get("ok") is True, str(dl))
    set_req(query={"id": str(cid)})
    det = _payload(await api.character_detail())
    check("删除后剩 3 张", len(det.get("refs") or []) == 3, str(len(det.get("refs") or [])))

    print("\n[11] 联网补全接口（M3）")
    set_req(body={"name": "薄荷", "work": "NTE"})
    sug = _payload(await api.character_suggest())
    check("补全返回候选标签", "mint" in (sug.get("tags") or ""), str(sug))
    check("补全标注来源", sug.get("source") == "danbooru", str(sug.get("source")))
    set_req(body={})
    _e2 = await api.character_suggest()
    check("缺 name 报错", isinstance(_e2, dict) and _e2.get("__kind__") == "error", str(_e2))

    print("\n[13] 锚点图片与封面接口（v6.1.0）")
    set_req(body={"name": "封面测试", "aliases": [], "persona_name": "", "work": "", "lora_name": ""})
    _rcc = _payload(await api.character_save())
    _cid2 = int(_rcc["id"])
    set_req(body={"character_id": _cid2, "anchor_name": "默认装",
                  "positive": "1girl, red hair", "kind": "full", "weight": 1.2})
    _aid1 = int(_payload(await api.character_anchor_save())["id"])
    set_req(body={"character_id": _cid2, "anchor_name": "泳装",
                  "positive": "1girl, blue swimsuit", "kind": "full", "weight": 1.2})
    _aid2 = int(_payload(await api.character_anchor_save())["id"])
    # 上传时用 JSON 字段指定锚点
    set_req(
        raw=json.dumps({
            "character_id": _cid2, "filename": "a.png", "anchor_id": _aid1,
            "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"1" * 60).decode(),
        }).encode(),
        headers={"content-type": "application/json"},
    )
    _u1 = _payload(await api.character_ref_upload())
    check("上传可指定锚点", int(_u1.get("anchor_id") or 0) == _aid1, str(_u1))
    # raw 二进制 + x-anchor-id 头（独立通道形态）
    set_req(
        raw=b"\x89PNG\r\n\x1a\n" + b"2" * 70,
        headers={"x-character-id": str(_cid2), "x-anchor-id": str(_aid2), "x-filename": "b.png"},
    )
    _u2 = _payload(await api.character_ref_upload())
    check("raw 上传走 x-anchor-id", int(_u2.get("anchor_id") or 0) == _aid2, str(_u2))
    set_req(query={"id": str(_cid2)})
    _det2 = _payload(await api.character_detail())
    _byid = {int(r["id"]): int(r.get("anchor_id") or 0) for r in (_det2.get("refs") or [])}
    check("详情图片带锚点归属",
          _byid.get(int(_u1["id"])) == _aid1 and _byid.get(int(_u2["id"])) == _aid2, str(_byid))
    check("未设封面时首图标记自动封面",
          any(r.get("is_cover_auto") for r in (_det2.get("refs") or [])),
          str([(r["id"], r.get("is_cover_auto")) for r in (_det2.get("refs") or [])]))
    set_req(body={"character_id": _cid2, "ref_id": _u2["id"]})
    _cs = _payload(await api.character_cover_set())
    check("设置封面", _cs.get("ok") is True and int(_cs.get("ref_id") or 0) == int(_u2["id"]), str(_cs))
    set_req(query={"id": str(_cid2)})
    _det3 = _payload(await api.character_detail())
    check("详情标注 is_cover",
          [int(r["id"]) for r in (_det3.get("refs") or []) if r.get("is_cover")] == [int(_u2["id"])],
          str([(r["id"], r.get("is_cover")) for r in (_det3.get("refs") or [])]))
    set_req(body={"character_id": _cid2, "ref_id": 0})
    _cc = _payload(await api.character_cover_set())
    check("清除封面回落自动", _cc.get("ok") is True and int(_cc.get("ref_id") or 0) == 0, str(_cc))
    set_req(body={"id": _u1["id"], "anchor_id": 0})
    _ra = _payload(await api.character_ref_anchor())
    check("改归属为角色级", int(_ra.get("anchor_id") or 0) == 0, str(_ra))
    set_req(body={"character_id": cid, "ref_id": _u1["id"]})
    _eb = await api.character_cover_set()
    check("跨角色设封面报错", isinstance(_eb, dict) and _eb.get("__kind__") == "error", str(_eb))

    print("\n[14] 下拉候选接口（v6.1.1：角色类 LoRA + AstrBot 人格）")
    # 桩：LoRA 库（含非角色分类）+ persona_manager
    FakePlugin._lora_library = lambda self: [
        {"name": "薄荷", "category": "角色", "base_model": "anima", "trigger_words": "mint_(nte)"},
        {"name": "鉴定师", "category": "角色", "base_model": "anima", "trigger_words": "zero_nte"},
        {"name": "鬼猫风格", "category": "风格", "base_model": "anima"},
        {"name": "无分类项"},
    ]
    FakePlugin.context = types.SimpleNamespace(
        persona_manager=types.SimpleNamespace(
            personas_v3=[
                {"name": "小叽V4", "prompt": "你是小叽……"},
                {"name": "霜岛绫V4", "prompt": "你是霜岛绫……"},
                {"name": "小叽V4", "prompt": "重复项应被去重"},
            ],
            selected_default_persona_v3={"name": "霜岛绫V4"},
        )
    )
    set_req()
    _opt = _payload(await api.character_options())
    _lnames = [x["name"] for x in (_opt.get("loras") or [])]
    # 排序按名称 Unicode（薄 U+8584 < 鉴 U+9274），不是拼音
    check("只返回角色分类 LoRA", _lnames == ["薄荷", "鉴定师"], str(_lnames))
    check("返回库内总数用于提示",
          int(_opt.get("lora_total") or 0) == 4 and int(_opt.get("lora_role_total") or 0) == 2,
          f"{_opt.get('lora_total')}/{_opt.get('lora_role_total')}")
    _pnames = [x["name"] for x in (_opt.get("personas") or [])]
    check("人格列表来自 AstrBot 且去重", _pnames == ["小叽V4", "霜岛绫V4"], str(_pnames))
    check("返回默认人格", _opt.get("default_persona") == "霜岛绫V4", str(_opt.get("default_persona")))
    # persona_manager 不可用时不报错、回空列表
    FakePlugin.context = types.SimpleNamespace()
    set_req()
    _opt2 = _payload(await api.character_options())
    check("取不到人格列表时不报错", _opt2.get("personas") == [], str(_opt2.get("personas")))
    check("LoRA 候选仍可用", len(_opt2.get("loras") or []) == 2, str(_opt2.get("loras")))

    print("\n[15] 下拉空值语义（v6.1.2：n-select 绑 null，不能绑空串）")
    _cv = (ROOT / "webui-src" / "src" / "views" / "CharacterView.vue").read_text(encoding="utf-8")
    check("表单下拉字段声明为 null", "persona_name: null as NullableStr" in _cv)
    check("新建弹窗清空下拉用 null", "createForm.persona_name = null" in _cv)
    check("填充表单空值转 null", 'form.persona_name = (c?.persona_name || "").trim() || null' in _cv)
    check("提交前 null 转空串", "persona_name: nv(form.persona_name)" in _cv)
    check("回归守卫：不得再把下拉清成空串",
          'createForm.persona_name = "' not in _cv and 'anchorForm.lora_name = "' not in _cv,
          "发现把 n-select 绑成空串的写法（会显示幽灵清空按钮）")
    _fv = (ROOT / "webui-src" / "src" / "views" / "FeaturesView.vue").read_text(encoding="utf-8")
    check("FeaturesView 工作流下拉同样用 null", "workflow: null as string | null" in _fv)

    print("\n[16] 原图查看 size=orig（v6.1.3）")
    _raw_png = b"\x89PNG\r\n\x1a\n" + b"o" * 88
    set_req(raw=_raw_png, headers={"x-character-id": str(_cid2), "x-filename": "orig.png"})
    _uo = _payload(await api.character_ref_upload())
    set_req(query={"id": str(_uo["id"]), "size": "orig"})
    _im2 = _payload(await api.character_ref_image())
    _dec1 = base64.b64decode(str(_im2.get("url", "")).split(",", 1)[1]) if "," in str(_im2.get("url", "")) else b""
    check("角色参考图 size=orig 返回原图字节", _dec1 == _raw_png, f"{len(_dec1)} vs {len(_raw_png)}")
    set_req(query={"id": str(_uo["id"])})
    _im3 = _payload(await api.character_ref_image())
    check("角色参考图缺省仍是缩略图 data URL",
          str(_im3.get("url", "")).startswith("data:image"), str(_im3.get("url", ""))[:40])
    # lora_image 同样支持 size=orig（工作流/LoRA 封面存于 lora_assets/）
    FakePlugin.lora_assets_dir = tmp / "lora_assets"
    FakePlugin.lora_assets_dir.mkdir(exist_ok=True)
    (FakePlugin.lora_assets_dir / "cover_test.png").write_bytes(_raw_png)
    set_req(query={"name": "cover_test.png", "size": "orig"})
    _li = _payload(await api.lora_image())
    _dec2 = base64.b64decode(str(_li.get("url", "")).split(",", 1)[1]) if "," in str(_li.get("url", "")) else b""
    check("LoRA/工作流封面 size=orig 返回原图", _dec2 == _raw_png, f"{len(_dec2)} vs {len(_raw_png)}")
    set_req(query={"name": "cover_test.png"})
    _li2 = _payload(await api.lora_image())
    check("LoRA/工作流封面缺省仍是缩略图",
          str(_li2.get("url", "")).startswith("data:image"), str(_li2.get("url", ""))[:40])

    print("\n[9] 路由已注册（内嵌页）")
    src = (ROOT / "webui_api.py").read_text(encoding="utf-8")
    for _ep in ("character/list", "character/detail", "character/save", "character/delete",
                "character/anchor/save", "character/anchor/delete", "character/anchor/primary",
                "character/ref/upload", "character/ref/image", "character/ref/delete",
                "character/ref/from_gallery", "character/ref/anchor", "character/cover/set",
                "character/options", "character/suggest", "character/export", "character/import"):
        check(f"路由 {_ep} 已注册", f'/character/' in src and _ep.split("/", 1)[1] in src, _ep)
    ssrc = (ROOT / "standalone_webui.py").read_text(encoding="utf-8")
    check("独立 WebUI 已分派 /character/", 'startswith("/character/")' in ssrc)
    check("独立 WebUI 适配器存在", "async def _api_character(" in ssrc)
    check("独立 WebUI 已分派 ref/anchor", 'sub == "ref/anchor"' in ssrc)
    check("独立 WebUI 已分派 cover/set", 'sub == "cover/set"' in ssrc)
    check("独立 WebUI 已分派 options", 'sub == "options"' in ssrc)

    print("\n[12] 双通道方法校验（v6.0.0：独立通道 GET 不再能触发 POST 端点）")
    check("模块级方法表已声明", isinstance(getattr(webui_api, "ROUTE_METHODS", None), dict))
    # ★v6.0.1 回归：同名不同方法的条目必须**归并**。
    #   `/config` 在 routes 里是两条（GET 读 + POST 写），早期实现用 dict[key]=methods
    #   互相覆盖 → 表里只剩 POST → GET /api/config 全 405（实测故障）。
    _tbl = webui_api.build_route_methods([
        ("/p/config", object(), ["GET"], "读"),
        ("/p/config", object(), ["POST"], "写"),
        ("/p/skip", None, ["GET"], "handler 缺失应跳过"),
        ("/p/story/session", object(), ["GET"], "详情"),
        ("/p/story/session", object(), ["POST"], "更新"),
    ])
    check("同名条目归并方法（/config = GET+POST）",
          set(_tbl.get("/p/config") or []) == {"GET", "POST"}, str(_tbl.get("/p/config")))
    check("handler 为 None 的条目跳过", "/p/skip" not in _tbl)
    check("同名条目归并方法（/story/session = GET+POST）",
          set(_tbl.get("/p/story/session") or []) == {"GET", "POST"})
    # 精确匹配优先：/quota/config 不该被 /config 抢走
    webui_api.ROUTE_METHODS.clear()
    webui_api.ROUTE_METHODS.update(webui_api.build_route_methods([
        (f"/{webui_api.PLUGIN_NAME}/config", object(), ["GET"], ""),
        (f"/{webui_api.PLUGIN_NAME}/quota/config", object(), ["POST"], ""),
        (f"/{webui_api.PLUGIN_NAME}/quota/reset", object(), ["POST"], ""),
    ]))
    check("精确匹配 /config = GET", StandaloneWebUI._route_methods("/config") == {"GET"},
          str(StandaloneWebUI._route_methods("/config")))
    check("精确匹配 /quota/config = POST",
          StandaloneWebUI._route_methods("/quota/config") == {"POST"},
          str(StandaloneWebUI._route_methods("/quota/config")))
    check("独立通道方法表可解析",
          StandaloneWebUI._route_methods("/quota/reset") == {"POST"},
          str(StandaloneWebUI._route_methods("/quota/reset")))
    check("未知路径不拦截（返回 None）",
          StandaloneWebUI._route_methods("/not/registered") is None)
    check("独立通道已放宽 body 上限", "_MAX_UPLOAD_BYTES" in ssrc)

    print(f"\n结果：{_ok} 通过 / {_fail} 失败")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
