# -*- coding: utf-8 -*-
"""角色卡链路守卫：契约降级 / 文本命中 / tags 注入。

main.py 依赖 astrbot 运行时装不了，role_card.py 是纯模块可直接 import。
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import role_card

FAILURES = []


def check(name, cond, detail=""):
    if not cond:
        FAILURES.append(name)
        print(f"FAIL: {name} {detail}")
    else:
        print(f"ok: {name}")


REF_MAP = {
    "miku": {"name": "初音未来", "aliases": ["miku", "初音"], "tags_en": "1girl, twintails, teal hair",
             "ref": "/data/refs/miku.png"},
    "asuna": {"name": "明日奈", "aliases": ["asuna", "亚丝娜"], "tags_en": "1girl, brown hair",
              "ref": ""},  # 无主参考
}


class _FakeApi:
    def fingerprint(self):
        return "waifu.v1:1"

    async def main_ref_map(self):
        return {"status": "ok", "data": REF_MAP, "missing": ["asuna"]}


class _FakeStar:
    star_cls = SimpleNamespace(api=_FakeApi())


class _FakeCtx:
    def get_registered_star(self, name):
        return _FakeStar() if name == "astrbot_plugin_moe_waifu" else None


# 1) 契约读取成功
role_card.clear_cache()
data, missing = asyncio.run(role_card.load_ref_map(_FakeCtx()))
check("契约映射读到", set(data) == {"miku", "asuna"}, str(list(data)))
check("missing 透传", missing == ["asuna"], str(missing))

# 2) 缓存命中（第二次用坏 context 也应返回缓存）
data2, _ = asyncio.run(role_card.load_ref_map(SimpleNamespace(get_registered_star=lambda n: None)))
check("缓存命中", set(data2) == {"miku", "asuna"})

# 3) 契约缺失降级
role_card.clear_cache()
d3, m3 = asyncio.run(role_card.load_ref_map(SimpleNamespace(get_registered_star=lambda n: None)))
check("无老婆插件 → 空映射降级", d3 == {} and m3 == [])

# 4) 版本不符降级
role_card.clear_cache()
class _BadApi(_FakeApi):
    def fingerprint(self): return "other.v9:1"
class _BadStar:
    star_cls = SimpleNamespace(api=_BadApi())
d4, _ = asyncio.run(role_card.load_ref_map(SimpleNamespace(get_registered_star=lambda n: _BadStar())))
check("版本不符 → 空映射降级", d4 == {})

# 5) 文本命中（长名优先 + 别名）
cid, e = role_card.match_role("画一张初音未来的图", REF_MAP)
check("中文名命中", cid == "miku", str(cid))
cid, e = role_card.match_role("draw miku please", REF_MAP)
check("英文别名命中", cid == "miku", str(cid))
cid, e = role_card.match_role("随便画个少女", REF_MAP)
check("无命中返回 None", cid is None)

# 6) tags_en 注入（追加不覆盖）
out = role_card.build_role_prompt(REF_MAP["miku"], "1girl, smile")
check("tags 追加", out.startswith("1girl, smile") and "twintails" in out, out)
out2 = role_card.build_role_prompt(REF_MAP["miku"], REF_MAP["miku"]["tags_en"])
check("已有 tags 不重复", out2 == REF_MAP["miku"]["tags_en"], out2)

# 7) search_roles（LLM 工具 comfyui_role 用）
rows = role_card.search_roles("", REF_MAP)
check("空关键词列出全部", len(rows) == 2, str(rows))
rows = role_card.search_roles("miku", REF_MAP)
check("按别名查到", len(rows) == 1 and rows[0]["char_id"] == "miku", str(rows))
rows = role_card.search_roles("twintails", REF_MAP)
check("按 tags_en 查到", len(rows) == 1 and rows[0]["char_id"] == "miku", str(rows))
check("不暴露本地路径", all("ref" not in r for r in rows), str(rows))
rows = role_card.search_roles("", REF_MAP, limit=1)
check("limit 生效", len(rows) == 1, str(rows))

# 8) 静态守卫：main.py 接入点存在
src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
check("main 接入 load_ref_map", "await role_card.load_ref_map(self.context)" in src)
check("main 接入 match_role", "role_card.match_role(" in src)
check("main 接入 search_roles", "role_card.search_roles(" in src)
check("注册 comfyui_role 工具", '@filter.llm_tool(name="comfyui_role")' in src)
check("出图 docstring 引导查角色库", "先查角色库" in src)
check("强制工作流绕过 session pin", "apply_session_pin=not bool(_wr_force_wf)" in src)
check("参考图注入 init_images", "init_images.insert(0, _wr_ref)" in src)

if FAILURES:
    print("FAILURES:", FAILURES)
    sys.exit(1)
print("role card OK")
