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
    "miku": {"name": "初音未来", "aliases": ["miku", "初音"], "series": "vocaloid",
             "tags_en": "1girl, twintails, teal hair", "ref": "/data/refs/miku.png"},
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
# 多词 AND：LLM 常把「作品名 角色名」一起传（eva 的 miku：series=vocaloid）
rows = role_card.search_roles("vocaloid miku", REF_MAP)
check("作品+角色组合查到", len(rows) == 1 and rows[0]["char_id"] == "miku", str(rows))
rows = role_card.search_roles("miku vocaloid", REF_MAP)
check("组合词顺序无关", len(rows) == 1 and rows[0]["char_id"] == "miku", str(rows))
rows = role_card.search_roles("vocaloid 不存在", REF_MAP)
check("多词 AND 有一词不匹配→空", not rows, str(rows))
rows = role_card.search_roles("vocaloid 不存在", REF_MAP, any_word=True)
check("OR 回退能捞回", len(rows) == 1 and rows[0]["char_id"] == "miku", str(rows))
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
# v8.5.2：图生图靠参考图锁形象，不能再引导 LLM 把 tags_en/角色名写进 prompt
check("不引导填 tags_en 进 prompt", "把 tags_en 填进 prompt" not in src and "命中就把 tags_en" not in src)
check("明确禁止写角色 tag", "不要把角色名 / 作品名 / 角色标签写进 prompt" in src
      or "不要写角色 tag" in src or "不要写角色名" in src)
check("强调画面要具体", "画面内容更要写足、写具体" in src or "越具体越好" in src)
check("不引导用户发参考图", "不要让用户发参考图" in src or "绝不要问用户要参考图" in src)
check("有角色库命中 hook", "def _inject_role_card_hint" in src)
# v8.5.4：不跟参考图尺寸 + 反三视图
check("角色卡强制自定义尺寸", "_role_card_force_size" in src and "custom_size = True" in src)
check("竖版兜底", "width, height = 832, 1216" in src)
check("反三视图 negative", "character sheet, multiple views, three views" in src)
check("无尺寸开关时提示", "工作流没有尺寸开关" in src)
check("schema 有 force_own_size/anti_sheet", '"force_own_size"' in (ROOT / "_conf_schema.json").read_text(encoding="utf-8")
      and '"anti_sheet"' in (ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
# v8.6.0：角色卡短描述自动详细创作
check("有详细创作方法", "async def _role_card_detail_prompt" in src)
check("身份靠参考图不写文字", "不要写角色名/作品名/角色 tag" in src)
check("创作后跳过二次扩写", "skip_llm=raw_prompt or _detail_done" in src)
check("排除用户自带图场景", "not _user_had_images" in src)
_sch = (ROOT / "_conf_schema.json").read_text(encoding="utf-8")
check("schema 有 auto_detail", '"auto_detail"' in _sch and '"auto_detail_max_chars"' in _sch)
# v8.7.0：会话内沿用上次角色（续画不串角色/工作流）
check("有会话角色记忆 dict", "g_session_role: dict[str, dict]" in src)
check("有 TTL", "G_SESSION_ROLE_TTL" in src)
check("读记忆回退", "g_session_role.get(_skey)" in src)
check("写记忆", 'g_session_role[_skey] = {"cid"' in src)
check("续画沿用", "续画沿用上次角色" in src)
# v8.9.2：用户点名的服装/画风必须照做；禁止「保持参考图不变」；画风当代不复古
check("点名服装画风照做", "用户没点名的就你替他设计" in src and "不锁服装/画风" in src)
check("禁止保持参考图不变", "原服装的款式与轮廓不要残留" in src)
check("画风默认当代", "绝不写复古/90年代" in src and "具体可画的视觉属性" in src)
check("禁止2026空话", "禁止写「2026年流行」" in src)
check("词表无Classic/复古", "Classic anime" not in src and "古典油画" not in src)
check("比基尼事故写进约束", "比基尼" in src)
# v8.10.0：照搬老婆插件的 Qwen 编辑指令形态 + 现代画风词表
check("编辑指令形态", "动作 + 目标 + 约束" in src and "操作动词开头" in src)
check("字数纪律380", "380 字" in src)
check("必须补全画面", "必须把画面写完整" in src and "和参考图一模一样" in src)
# v8.10.2：用户点名的具体元素不许被替换 + 程序化拼回兜底
check("用户点名元素硬事实", "必须逐字保留" in src and "厨房里冲热可可" in src)
check("用户原话强制拼回", "严格按用户要求执行" in src and "_raw_hint" in src)
# v8.10.3：创作 hint 用 LLM 的 positive（信息完整），不再只用用户短消息
check("创作hint用LLM画面", "_llm_hint = (positive" in src and "len(_llm_hint) > len(_hint)" in src)
check("长画面兜底措辞", "严格保留以下画面内容" in src)
# v8.10.4：原生聊天即使 raw_prompt=true 也要转英文tag→中文编辑指令
check("原生raw_prompt也转换", "(not raw_prompt or not source)" in src)
# v8.11.0：确定性中文编辑指令（不依赖LLM/auto_detail）
ins = role_card.build_edit_instruction("深夜阳台喝杯热的", "现代日式动画")
check("确定性指令含用户画面", "深夜阳台喝杯热的" in ins, ins)
check("确定性指令含画风", "将整体画风改为现代日式动画" in ins, ins)
check("确定性指令保身份", "只保留人物的面部特征" in ins and "不要照搬参考图的服装与姿势" in ins, ins)
check("确定性指令含近景", "半身近景" in ins, ins)
check("确定性指令重设计姿势", "姿势动作与表情按本次描述重新设计" in ins, ins)
_ins_outfit = role_card.build_edit_instruction("把衣服换成比基尼", "")
check("确定性指令换装完全替换", "新服装完全替换参考图原有服装" in _ins_outfit, _ins_outfit)
check("main接入确定性指令", "role_card.build_edit_instruction(" in src)
check("完全替换原服装", "完全替换参考图原有服装" in src and "轮廓不要残留" in src)
check("不锁参考图服装姿势", "不要照搬参考图的服装与姿势" in src and "只做上述改动" not in src)
check("现代画风词表", "现代日式动画" in src and "新海诚式电影动画" in src and "复古 90 年代动画" in src)
_vue = (ROOT / "webui-src/src/views/RoleCardView.vue").read_text(encoding="utf-8")
check("页面词表同步", "现代日式动画" in _vue)
check("schema 有 reuse_last_role", '"reuse_last_role"' in _sch)
# v8.8.0：默认近景、角色占比大（用户明确全身/远景时不强制）
check("详细创作有近景约束", "人物占画面主要面积" in src and "远景小人" in src)
check("有全身意图判定", "_fullbody_re" in src)
check("negative 压制远景小人", "tiny character, small figure" in src)
check("docstring 默认近景", "默认近景 / 半身" in src)
# v8.9.0→v8.10.0：画风可改 + 通用画风工具（编辑指令形态）
check("画风是点名改动", "把整体画风改为" in src)
check("画风写法现代", "具体可画的视觉属性" in src)
check("有 comfyui_style 工具", '@filter.llm_tool(name="comfyui_style")' in src)
check("画风词表多档", "danbooru 标签" in src and "qwen 中文" in src and "natural 英文" in src)
# v8.9.1：默认画风（页面配置）+ 续画判定放宽
check("默认画风写入 hint", "画成{_style_cfg}风格" in src)
check("对话画风覆盖默认", "_user_mentioned_style" in src)
check("schema 有 default_style", '"default_style"' in _sch)
_vue = (ROOT / "webui-src/src/views/RoleCardView.vue").read_text(encoding="utf-8")
check("页面有默认画风下拉", "styleOptions" in _vue and "default_style" in _vue)
check("续画不靠窄正则", "_continue_re" not in src and "not _wr_entry and _reuse_cfg" in src)
check("强制工作流绕过 session pin", "apply_session_pin=not bool(_wr_force_wf)" in src)
check("参考图注入 init_images", "init_images.insert(0, _wr_ref)" in src)
# v8.12.0：退出角色 / 显式工作流 opt-out / denoise 可配
check("退出词清会话记忆", "_ROLE_EXIT_RE.search(_u_msg)" in src and "g_session_role.pop(_skey, None)" in src)
check("续画意图收窄", "_ROLE_CONTINUE_RE.search(_u_msg)" in src)
check("跨插件跳过角色卡", "use_role_card: bool | None = None" in src
      and "use_role_card is True" in src and "use_role_card=use_role_card" in src)
check("外部来源默认不进角色卡", "_role_card_source_allowed" in src
      and "_ROLE_CARD_EXTERNAL_SOURCES" not in src
      and "return not _is_external_call(source, external)" in src)
check("raw_prompt不覆盖调用方prompt", "raw_prompt and _wr_orig_positive" in src)
check("denoise可配", '"denoise"' in _sch and '_wr_cfg.get("denoise", -1)' in src)

if FAILURES:
    print("FAILURES:", FAILURES)
    sys.exit(1)
print("role card OK")
