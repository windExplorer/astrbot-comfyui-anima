# -*- coding: utf-8 -*-
"""老婆角色链路守卫：契约降级 / 文本命中 / tags 注入。

main.py 依赖 astrbot 运行时装不了，waifu_role.py 是纯模块可直接 import。
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import waifu_role

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
waifu_role.clear_cache()
data, missing = asyncio.run(waifu_role.load_ref_map(_FakeCtx()))
check("契约映射读到", set(data) == {"miku", "asuna"}, str(list(data)))
check("missing 透传", missing == ["asuna"], str(missing))

# 2) 缓存命中（第二次用坏 context 也应返回缓存）
data2, _ = asyncio.run(waifu_role.load_ref_map(SimpleNamespace(get_registered_star=lambda n: None)))
check("缓存命中", set(data2) == {"miku", "asuna"})

# 3) 契约缺失降级
waifu_role.clear_cache()
d3, m3 = asyncio.run(waifu_role.load_ref_map(SimpleNamespace(get_registered_star=lambda n: None)))
check("无老婆插件 → 空映射降级", d3 == {} and m3 == [])

# 4) 版本不符降级
waifu_role.clear_cache()
class _BadApi(_FakeApi):
    def fingerprint(self): return "other.v9:1"
class _BadStar:
    star_cls = SimpleNamespace(api=_BadApi())
d4, _ = asyncio.run(waifu_role.load_ref_map(SimpleNamespace(get_registered_star=lambda n: _BadStar())))
check("版本不符 → 空映射降级", d4 == {})

# 5) 文本命中（长名优先 + 别名）
cid, e = waifu_role.match_role("画一张初音未来的图", REF_MAP)
check("中文名命中", cid == "miku", str(cid))
cid, e = waifu_role.match_role("draw miku please", REF_MAP)
check("英文别名命中", cid == "miku", str(cid))
cid, e = waifu_role.match_role("随便画个少女", REF_MAP)
check("无命中返回 None", cid is None)

# 6) tags_en 注入（追加不覆盖）
out = waifu_role.build_role_prompt(REF_MAP["miku"], "1girl, smile")
check("tags 追加", out.startswith("1girl, smile") and "twintails" in out, out)
out2 = waifu_role.build_role_prompt(REF_MAP["miku"], REF_MAP["miku"]["tags_en"])
check("已有 tags 不重复", out2 == REF_MAP["miku"]["tags_en"], out2)

# 7) 静态守卫：main.py 接入点存在
src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
check("main 接入 load_ref_map", "await waifu_role.load_ref_map(self.context)" in src)
check("main 接入 match_role", "waifu_role.match_role(" in src)
check("强制工作流绕过 session pin", "apply_session_pin=not bool(_wr_force_wf)" in src)
check("参考图注入 init_images", "init_images.insert(0, _wr_ref)" in src)

if FAILURES:
    print("FAILURES:", FAILURES)
    sys.exit(1)
print("waifu role OK")
