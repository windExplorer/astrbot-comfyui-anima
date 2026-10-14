"""提示词护栏（v7.6.0）：外观维度识别 / 冲突剥除 / 扩写校验 / 质量前缀分档。

跑法：python tests/test_prompt_guard.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import prompt_guard  # noqa: E402

from prompt_guard import (  # noqa: E402
    LORA_ECHO_DIMS, LORA_ECHO_DIMS_WITH_OUTFIT, MULTI_FUSION_NEGATIVE,
    appearance_change_intent, appearance_dims, build_hybrid_multi_person,
    classify_common_tags, clean_multi_count_conflicts, differentiation_intent,
    ensure_quality_prefix, free_dim_coverage,
    has_character_tag, identity_anchor_tags, is_multi_person, is_short_prompt,
    keeps_original_tags, merge_multi_negative, multi_person_hints,
    multi_shared_appearance, outfit_intent, prompt_style_of, quality_prefix_for,
    strip_conflicting_appearance, strip_lora_echo_appearance, strip_new_appearance,
    visual_label_for,
)

ANCHOR = "long hair, green hair, green eyes, cat ears, hair ribbon"


def test_appearance_dims():
    d = appearance_dims("white hair, blue eyes, twintails, fox ears, petite")
    assert d["hair_color"] == {"white"}, d
    assert d["eye_color"] == {"blue"}, d
    assert d["hair_style"] == {"twintails"}, d
    assert d["animal"] and "fox ears" in d["animal"], d
    assert d["body"] == {"petite"}, d
    # 中文也认
    zh = appearance_dims("白发, 蓝瞳, 双马尾, 猫耳")
    assert zh["hair_color"] == {"白"}, zh
    assert zh["eye_color"] == {"蓝"}, zh
    # 不含外观的标签不该误报
    plain = appearance_dims("1girl, solo, night, depth of field, standing, smile")
    assert not plain, plain
    # 服装走 outfit 维度（不参与锚点剥除）
    assert appearance_dims("white dress, red boots")["outfit"], "服装应被识别"
    print("== 1. 外观维度识别（中英/兽化/体型/服装） OK")


def test_has_character_tag():
    assert has_character_tag("hatsune_miku \\(vocaloid\\), solo")
    assert has_character_tag("belle (zenless zone zero), 1girl")
    assert not has_character_tag("1girl, solo, white hair")
    assert has_character_tag("来一张星野的图", ("星野",))          # 命中角色卡名单
    assert not has_character_tag("1girl, night", ("星野",))
    print("== 2. 角色/作品 tag 判定（括号形态 + 角色卡名单） OK")


def test_strip_conflicting_appearance():
    # 经典事故：锚点是绿发，模型补了 white hair → 必须剥掉模型那份
    txt = "1girl, white hair, smile, night, green eyes"
    out, dropped = strip_conflicting_appearance(txt, ANCHOR)
    assert "white hair" not in out and "smile" in out and "night" in out, out
    assert dropped == ["white hair"], dropped
    # 同维度同值 → 保留（没冲突）
    out2, dropped2 = strip_conflicting_appearance("1girl, green hair, smile", ANCHOR)
    assert out2 == "1girl, green hair, smile" and not dropped2, (out2, dropped2)
    # 服装冲突不管（本图换装允许）
    out3, dropped3 = strip_conflicting_appearance("1girl, white dress, blue eyes", ANCHOR)
    assert "white dress" in out3, out3
    assert dropped3 == ["blue eyes"], dropped3
    # 多人分组：整组（括号）不被拆，组外的冲突标签照剥（权威值只对其中一个角色成立）
    multi = "2girls, (cat ears, green hair:1.20), (fox ears, white hair:1.20), night"
    out4, dropped4 = strip_conflicting_appearance(multi, ANCHOR)
    assert "(cat ears, green hair:1.20)" in out4 and "(fox ears, white hair:1.20)" in out4, out4
    assert "night" in out4 and not dropped4, (out4, dropped4)
    # 单角色场景：连括号分组一起判（模型自己写的冲突分组也剥掉）
    out4b, dropped4b = strip_conflicting_appearance(
        "1girl, (white hair:1.2), night", ANCHOR, include_groups=True)
    assert "white hair" not in out4b and "night" in out4b, out4b
    assert dropped4b == ["(white hair:1.2)"], dropped4b
    # 没有权威外观 → 原样返回
    out5, dropped5 = strip_conflicting_appearance("1girl, white hair", "")
    assert out5 == "1girl, white hair" and not dropped5
    print("== 3. 与锚点冲突的外观标签剥除（含多人分组保护） OK")


def test_strip_new_appearance():
    orig = "1girl, night, smile"
    # 只加自由维度 → 全保留
    enriched = "1girl, night, smile, depth of field, backlighting, petals falling"
    out, dropped = strip_new_appearance(orig, enriched)
    assert out == enriched and not dropped, (out, dropped)
    # 新增外观 → 剥掉；改写外观 → 剥掉
    enriched2 = "1girl, night, smile, white hair, depth of field"
    out2, dropped2 = strip_new_appearance(orig, enriched2)
    assert "white hair" not in out2 and "depth of field" in out2, out2
    assert dropped2 == ["white hair"], dropped2
    enriched3 = "1girl, night, smile, black hair, white dress, rim light"
    out3, dropped3 = strip_new_appearance("1girl, night, smile, black hair", enriched3)
    assert "black hair" in out3 and "white dress" not in out3 and "rim light" in out3, out3
    assert dropped3 == ["white dress"], dropped3
    print("== 4. 扩写护栏（外观只能原样保留，不能新增/改写） OK")


def test_keeps_original_tags():
    assert keeps_original_tags("1girl, night, smile", "1girl, night, smile, bokeh")
    assert not keeps_original_tags("1girl, night, smile", "1girl, night, bokeh")
    assert keeps_original_tags("", "anything")
    print("== 5. 扩写结果必须保留原文全部标签 OK")


def test_free_dim_coverage_and_short():
    cov = free_dim_coverage("1girl, close-up, cinematic lighting, night, detailed background")
    assert {"镜头/视角", "光影", "氛围/天气", "场景细节"} <= cov, cov
    assert is_short_prompt("1girl, smile", 8)
    assert not is_short_prompt(", ".join(f"tag{i}" for i in range(12)), 8)
    print("== 6. 自由维度覆盖统计 + 短描述判定 OK")


def test_quality_prefix():
    # 分档
    assert prompt_style_of(is_anima=True) == "tags"
    assert prompt_style_of(base_name="animaPencilXL_v500") == "tags"
    assert prompt_style_of(base_name="Pony Diffusion V6 XL") == "pony"
    assert prompt_style_of(base_name="Flux.1-dev") == "natural"
    assert prompt_style_of(base_name="Z-Image Turbo") == "natural"
    assert prompt_style_of(base_name="Qwen Image 2.1") == "natural"
    assert prompt_style_of(base_name="随便一个底模", prompt_style="自然语言") == "natural"
    assert prompt_style_of(base_name="随便一个底模", prompt_style="danbooru") == "tags"
    assert quality_prefix_for("natural") == ""
    assert "score_9" in quality_prefix_for("pony")
    # 缺失才加
    out, added = ensure_quality_prefix("1girl, night", "tags")
    assert added and out.endswith("1girl, night"), out
    # 已有序言 → 不加
    out2, added2 = ensure_quality_prefix("masterpiece, 1girl", "tags")
    assert added2 == "" and out2 == "masterpiece, 1girl", (out2, added2)
    # 自然语言系不加（加了会被当文字渲染）
    out3, added3 = ensure_quality_prefix("a girl at night", "natural")
    assert added3 == "" and out3 == "a girl at night"
    # 自定义前缀优先
    out4, added4 = ensure_quality_prefix("1girl", "tags", custom="best quality, my style")
    assert added4 == "best quality, my style" and out4.startswith("best quality"), out4
    # 空文本不加
    assert ensure_quality_prefix("", "tags") == ("", "")
    print("== 7. 画质前缀分档与「缺失才加」 OK")


def test_multi_person_detection():
    assert is_multi_person("masterpiece, 2girls, side_by_side")
    assert is_multi_person("1boy 1girl, smiling")
    assert is_multi_person("三个女孩在樱花树下")          # 中文人数
    assert is_multi_person("这两个女孩合照")               # 中文人数
    assert not is_multi_person("1girl, solo, white dress")
    assert not is_multi_person("两个苹果放在桌上")          # 不是人，不该触发
    assert not is_multi_person("")
    print("== 8. 多人判定（英文计数标签 + 中文人数表述） OK")


def test_multi_person_hints():
    # 贴身 + 大头贴裁切 → 两个风险都命中
    multi, crops, contact = multi_person_hints(
        "2girls, close-up, hugging, cheek to cheek")
    assert multi and "close-up" in crops and "hugging" in contact, (crops, contact)
    # 带部位的接触写法不该告警（是推荐形态）
    multi2, crops2, contact2 = multi_person_hints(
        "2girls, cowboy shot, hugging from behind, cheek to cheek")
    assert multi2 and crops2 == [] and contact2 == [], (crops2, contact2)
    # 中文近景 + 中文笼统接触
    multi3, crops3, contact3 = multi_person_hints("两个女孩，特写，拥抱")
    assert multi3 and crops3 and contact3, (crops3, contact3)
    # 单人：完全不触发
    assert multi_person_hints("1girl, solo, close-up, hugging") == (False, [], [])
    print("== 9. 多人风险词告警（贴身裁切 + 笼统接触，带部位写法不告警） OK")


def test_merge_multi_negative():
    # 负向为空 → 整串畸形词补上
    neg, added = merge_multi_negative("")
    assert neg and added == list(MULTI_FUSION_NEGATIVE), added
    # 已有负向：只增不覆盖，原词保持在前
    neg2, added2 = merge_multi_negative("lowres, bad hands")
    assert neg2.startswith("lowres, bad hands, ") and "conjoined" in neg2, neg2
    assert "lowres" not in added2 and "bad hands" not in added2, added2
    # 已有畸形词 → 去重不重复追加（给全量词表，预期零追加）
    neg3, added3 = merge_multi_negative(", ".join(MULTI_FUSION_NEGATIVE) + ", lowres")
    assert added3 == [] and neg3.endswith(", lowres"), (neg3, added3)
    # 绝不能把人数词写进负向（会把 2 人压回 1 人）
    for w in ("solo", "1girl", "2girls", "1boy"):
        assert w not in MULTI_FUSION_NEGATIVE, w
    # 自定义词表替换默认
    neg4, added4 = merge_multi_negative("", custom="bad anatomy, 粘连")
    assert added4 == ["bad anatomy", "粘连"], added4
    print("== 10. 畸形负向并入（只增不覆盖 + 去重 + 绝不含人数词） OK")


def test_strip_lora_echo_appearance():
    # 用户实测原样：两个角色 LoRA + LLM 复述了完整外形（衣服是用户没提的）
    txt = ("masterpiece, best quality, safe, 2girls, "
           "(suomingwuwa, red hair, twin hair buns with gold bells, long grey-tipped twintails, "
           "red eyes, black strapless dress with detached sleeves and black coat on shoulders:1.25), "
           "(xinyuehu, long white hair with black tips, single hair bun, fox ears, orange eyes, "
           "red eyeliner, black strapless dress with puffy detached sleeves:1.25), "
           "standing side by side, night")
    out, dropped = strip_lora_echo_appearance(txt)
    # 外形（发色/发型/瞳色/兽耳）全部剥掉
    for t in ("red hair", "red eyes", "fox ears", "orange eyes", "single hair bun"):
        assert t in dropped, (t, dropped)
    # 角色触发词、用户可能明说的衣服、分组权重、场景标签必须保留
    assert "suomingwuwa" in out and "xinyuehu" in out, out
    assert "black strapless dress with detached sleeves and black coat on shoulders" in out, out
    assert "black strapless dress with puffy detached sleeves" in out, out
    assert ":1.25" in out and "standing side by side" in out and "night" in out, out
    # 服装维度绝不参与剥除（outfit 不在 LORA_ECHO_DIMS）
    assert "outfit" not in LORA_ECHO_DIMS, LORA_ECHO_DIMS
    # 剥空了的组整组消失（只含外形的组）
    out2, dropped2 = strip_lora_echo_appearance("2girls, (red hair, blue eyes:1.2), night")
    assert "red hair" not in out2 and "blue eyes" not in out2, out2
    assert "night" in out2, out2
    # 没有外形标签 → 原样返回
    same = "2girls, (suomingwuwa:1.2), (xinyuehu:1.2), night, moonlit pavilion"
    out3, dropped3 = strip_lora_echo_appearance(same)
    assert out3 == same and not dropped3, (out3, dropped3)
    print("== 11. 角色 LoRA 形象复述剥除（剥外形、留服装与触发词） OK")


def test_appearance_change_intent():
    assert appearance_change_intent("把头发染成粉色")
    assert appearance_change_intent("剪短一点")
    assert appearance_change_intent("给她换成红裙子")          # 换装也算用户干预 → 不剥
    assert appearance_change_intent("加上猫耳")
    assert appearance_change_intent("去掉角")
    assert appearance_change_intent("change her hair to blue")
    assert not appearance_change_intent("画两个角色并加上她们的 LoRA")
    assert not appearance_change_intent("两个女孩合照")
    print("== 12. 用户改外形意图判定（有则整体放弃剥除） OK")


def test_strip_lora_echo_appearance_with_outfit():
    """v7.7.69：用户没提服装时，服装配饰也归 LoRA 自带，一并剥除。"""
    txt = ("2girls, (suomingwuwa, black strapless dress, detached sleeves, black coat on shoulders, "
           "diamond choker, gold bells hair ornaments:1.2), "
           "(xinyuehu, black strapless dress, black choker, detached sleeves, puffy sleeves, "
           "side slit, hair ornament, tassel:1.2), side_by_side, cowboy shot, night")
    out, dropped = strip_lora_echo_appearance(txt, LORA_ECHO_DIMS_WITH_OUTFIT)
    for t in ("black strapless dress", "detached sleeves", "diamond choker",
              "gold bells hair ornaments", "tassel"):
        assert t in dropped, (t, dropped)
    # 触发词 / 场景 / 权重保留
    assert "suomingwuwa" in out and "xinyuehu" in out and ":1.2" in out, out
    assert "side_by_side" in out and "cowboy shot" in out and "night" in out, out
    # 只剩触发词的两个分组仍在，且两组内容不再相同（撞脸根因消除）
    assert out.count("black strapless dress") == 0, out
    # 服装维度确认在 dims 里
    assert "outfit" in LORA_ECHO_DIMS_WITH_OUTFIT, LORA_ECHO_DIMS_WITH_OUTFIT
    print("== 14. 角色 LoRA 复述剥除（含服装维度） OK")


def test_outfit_intent():
    # 用户明说换装 → 保留服装
    assert outfit_intent("给她换成红裙子")
    assert outfit_intent("让她穿上校服")
    assert outfit_intent("戴个choker")
    assert outfit_intent("脱掉外套")
    assert outfit_intent("换一身和服")
    assert outfit_intent("穿件黑色风衣")
    assert outfit_intent("undress her")
    # 用户没提服装 → 剥
    assert not outfit_intent("画两个角色并加上她们的 LoRA")
    assert not outfit_intent("两个女孩在樱花树下合照")
    assert not outfit_intent("t 两个角色")
    print("== 15. 用户服装干预意图判定 OK")


def test_clean_multi_count_conflicts():
    # 用户实测原样：左右站位写成自然语言，各带一个 1girl，与 2girls 打架
    txt = ("2girls, 1girl standing on the left and 1girl standing on the right, "
           "(suomingwuwa, armor:1.2), (xinyuehu, hanfu:1.2), side by side, night")
    out, dropped = clean_multi_count_conflicts(txt)
    assert "1girl" not in out, out
    assert "standing on the left" not in out, out
    assert dropped == ["1girl standing on the left and 1girl standing on the right"], dropped
    assert "2girls" in out and "(suomingwuwa, armor:1.2)" in out, out
    # 分组内的裸计数标签也要清（此前只在 character.py 归一时清过）
    out2, dropped2 = clean_multi_count_conflicts(
        "2girls, (1girl, red hair:1.2), (1boy, blue hair:1.2), night")
    assert "1girl" not in out2 and "1boy" not in out2, out2
    assert "red hair" in out2 and "blue hair" in out2, out2
    # 单人场景不动（1girl / solo 是正确计数）
    same = "1girl, solo, white dress"
    assert clean_multi_count_conflicts(same) == (same, [])
    print("== 16. 多人计数冲突清理（裸计数标签 + 含计数的自然语言句） OK")


def _load_main_methods(names: tuple[str, ...], extra_ns: dict | None = None):
    """从 main.py 原样抠出指定方法 exec（带最小 stub），测磁盘上的真代码。

    本机 import 不了 astrbot 运行时，所以走 AST 抠函数这条路——测的是磁盘上的真代码。
    """
    import ast
    import logging
    # main.py 带 BOM，必须用 utf-8-sig 读，否则 ast.parse 报 U+FEFF
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(
        (n for n in tree.body
         if isinstance(n, ast.ClassDef) and n.name == "ComfyUIDrawPlugin"), None)
    assert cls is not None, "未找到 ComfyUIDrawPlugin 类"
    ns: dict = {"re": re, "logger": logging.getLogger("test"), "prompt_guard": prompt_guard}
    ns.update(extra_ns or {})
    got: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            exec(compile(ast.Module([node], []), "main.py", "exec"), ns)
            got.add(node.name)
    assert got == set(names), f"抠取失败: {set(names) - got}"
    return ns


class _FakeLoraSelf:
    """给 _align_character_lora_triggers / _char_lora_triggers 用的最小替身。"""

    def __init__(self, lib: dict, wf_loras=None):
        self._lib = lib
        self._wf_loras = wf_loras or []

    def _lora_lib_index(self):
        return dict(self._lib)

    def _loras_of(self, wf):
        return list(self._wf_loras)


def test_align_character_lora_triggers():
    import logging
    ns = _load_main_methods(
        ("_char_lora_triggers", "_align_character_lora_triggers"),
        {"logger": logging.getLogger("test")},
    )

    class S(_FakeLoraSelf):
        _text_has_word = staticmethod(
            lambda word, hay_low: (
                bool(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", hay_low))
                if word.isascii() else word.lower() in hay_low
            )
        )

    S._char_lora_triggers = ns["_char_lora_triggers"]
    S._align_character_lora_triggers = ns["_align_character_lora_triggers"]

    lib = {
        "锁暝": {"name": "锁暝", "category": "角色", "trigger_words": "lockming"},
        "心月狐": {"name": "心月狐", "category": "角色", "trigger_words": "heartmoon"},
    }
    s = S(lib)
    lora_map = {"锁暝": None, "心月狐": None}
    # 用户实测：提示词里写的是上一轮对话里的另两个人 → 按分组顺序替换成本次真正的角色
    txt = "2girls, (suomingwuwa, armor:1.2), (xinyuehu, hanfu:1.2), side by side, night"
    out, note = s._align_character_lora_triggers(txt, lora_map, {})
    assert "(lockming, armor:1.2)" in out and "(heartmoon, hanfu:1.2)" in out, out
    assert "suomingwuwa" not in out and "xinyuehu" not in out, out
    assert "suomingwuwa → lockming" in note, note
    # 提示词里已经点名了本次角色 → 不插手
    ok = "2girls, (lockming:1.2), (heartmoon:1.2), night"
    out2, note2 = s._align_character_lora_triggers(ok, lora_map, {})
    assert out2 == ok, out2
    # 只有一个分组 → 保守不动
    one = "1girl, (suomingwuwa:1.2), night"
    out3, note3 = s._align_character_lora_triggers(one, {"锁暝": None}, {})
    assert out3 == one, out3
    # 组内没有可识别的角色标识（只有衣服）→ 不动并说明
    none_role = "2girls, (armor:1.2), (hanfu:1.2), night"
    out4, note4 = s._align_character_lora_triggers(none_role, lora_map, {})
    assert out4 == none_role, out4
    assert "不一致" in note4 and "保留原样" in note4, note4
    # 没有角色 LoRA → 不动
    plain = S({"天光02": {"name": "天光02", "category": "风格", "trigger_words": ""}})
    out5, _ = plain._align_character_lora_triggers(txt, {"天光02": 0.3}, {})
    assert out5 == txt, out5
    print("== 17. 触发词与实际角色 LoRA 对齐（错写角色名自动纠正） OK")


def test_identity_anchor_tags():
    # Danbooru 返回：角色 tag + 标准外观（参考流程：先校正角色 tag，再取身份锚点）
    tags = ("hatsune_miku \\(vocaloid\\), 1girl, solo, long hair, blue hair, blue eyes, "
            "ahoge, headset, white shirt, detached sleeves, smile")
    anchors = identity_anchor_tags(tags)
    assert "blue hair" in anchors and "blue eyes" in anchors, anchors
    assert not any("hatsune_miku" in a for a in anchors), anchors      # 角色 tag 本身不作锚点
    assert "1girl" not in anchors and "solo" not in anchors, anchors
    assert "white shirt" in anchors or "detached sleeves" in anchors, anchors   # 服装可作锚点
    assert "smile" not in anchors, anchors                              # 表情/动作不算身份
    # 空输入不炸
    assert identity_anchor_tags("") == []
    print("== 18. 身份锚点提取（跳过角色 tag / 人数词，按维度取） OK")


def test_visual_label_for():
    # 参考 multi_person_prompt.py:221：visual_label 必须唯一、不含名字与 left/right
    assert visual_label_for(["red hair", "fox ears"]) == "red-haired fox"
    assert visual_label_for(["long white hair", "orange eyes"]) == "white-haired"
    assert visual_label_for(["orange eyes"]) == "orange-eyed"
    assert visual_label_for(["cat ears"]) == "cat"
    assert visual_label_for([], fallback="xinyuehu") == "xinyuehu"
    print("== 19. visual_label 生成（唯一可见特征，不含名字/方位） OK")


def test_build_hybrid_multi_person():
    # 用户实测形态：两个分组只剩触发词（v7.7.70 剥除后的结果）
    positive = ("masterpiece, best quality, 2girls, (suomingwuwa:1.2), (xinyuehu:1.2), "
                "side_by_side, looking_at_each_other, night")
    per_char = [{"anchors": ["red hair", "fox ears"]},
                {"anchors": ["long white hair", "orange eyes"]}]
    out, notes = build_hybrid_multi_person(positive, per_char)
    # 参考实现形态：公共 tag + 每角色一个自然语言块（标签: 标签, 标签.）
    assert "red-haired fox: " in out, out
    assert "white-haired: " in out, out
    # 身份由 label + 锚点表达，裸触发词与权重括号都该消失
    assert "suomingwuwa" not in out and "xinyuehu" not in out, out
    assert ":1.2" not in out, out
    # 公共部分（人数/关系/场景/画质）保持
    for keep in ("masterpiece", "2girls", "side_by_side", "looking_at_each_other", "night"):
        assert keep in out, (keep, out)
    assert len(notes) == 2, notes
    # 缺锚点 → 放弃重构（宁可不改，也不能写成两个没区分的人）
    same, notes2 = build_hybrid_multi_person(
        positive, [{"anchors": ["red hair"]}, {"anchors": []}])
    assert same == positive and not notes2, (same, notes2)
    # 分组数与角色数不一致 → 放弃
    same3, notes3 = build_hybrid_multi_person(positive, [{"anchors": ["red hair"]}])
    assert same3 == positive and not notes3, (same3, notes3)
    # 无分组 → 放弃
    same4, _ = build_hybrid_multi_person("2girls, suomingwuwa, xinyuehu, night", per_char)
    assert same4 == "2girls, suomingwuwa, xinyuehu, night"
    print("== 20. 参考混合式渲染（公共 tag + 自然语言块 + 权威锚点） OK")


def test_end_to_end_multi_identity():
    """端到端复现用户 v7.7.70 的事故现场：剥完只剩触发词 → 必须补回权威锚点并混合式渲染。"""
    # 用户实际日志里的提示词（外形已被剥除，两个分组只剩触发词 + standing）
    positive = ("masterpiece, best quality, very aesthetic, absurdres, 2girls, "
                "(suomingwuwa, standing:1.2), (xinyuehu, standing:1.2), "
                "side_by_side, looking_at_each_other, simple background, soft lighting")
    # 模拟 Danbooru 校正返回（参考流程：角色 tag → 标准外观标签）
    a1 = identity_anchor_tags(
        "suomingwuwa, 1girl, solo, red hair, twin hair buns, red eyes, black coat")
    a2 = identity_anchor_tags(
        "xinyuehu, 1girl, solo, long white hair, fox ears, orange eyes, white and red hanfu")
    assert a1 and a2, (a1, a2)
    out, notes = build_hybrid_multi_person(positive, [{"anchors": a1}, {"anchors": a2}])
    assert notes and len(notes) == 2, notes
    # 两个角色必须落在**不同**的视觉标签上（这是治同脸的关键）
    labels = [n.split(" ←")[0] for n in notes]
    assert len(set(labels)) == 2, labels
    assert "red-haired" in out and "white-haired" in out, out
    assert "2girls" in out and "side_by_side" in out, out
    # 参考负向词必须含治同脸的几个
    for w in ("twins", "duplicate characters", "cloned character", "fused characters"):
        assert w in MULTI_FUSION_NEGATIVE, w
    print("== 21. 端到端：剥除 → 补权威锚点 → 参考混合式（两人标签不同） OK")


def test_classify_common_tags():
    # 用户实测：说"各自有不同的体型服饰动作"，模型却把专属项全丢在公共区
    txt = ("2girls, side_by_side, looking_at_each_other, night, full body, soft lighting, "
           "petite, tall, black dress, red dress, sitting, standing, blush, "
           "simple background, depth of field")
    common, personal = classify_common_tags(txt)
    # 角色专属：体型/服饰/动作/表情
    for t in ("petite", "tall", "black dress", "red dress", "sitting", "standing", "blush"):
        assert t in personal, (t, common, personal)
    # 公共：人数/关系/场景/镜头/画质
    for t in ("2girls", "side_by_side", "looking_at_each_other", "night", "full body",
              "soft lighting", "simple background", "depth of field"):
        assert t in common, (t, common, personal)
    # 关系类动作不进个人专属（两人共有才对）
    _c2, _p2 = classify_common_tags("2girls, holding hands, hugging, smile")
    assert "holding hands" not in _p2 and "hugging" not in _p2, _p2
    assert "smile" in _p2, _p2
    print("== 22. 公共 / 角色专属标签分类（体型服饰动作表情 vs 关系场景镜头） OK")


def test_differentiation_intent():
    assert differentiation_intent("她们各自有不同的体型服饰和动作")
    assert differentiation_intent("两个角色要不一样的画风")
    assert differentiation_intent("画两个风格迥异的人")
    assert differentiation_intent("two characters with different outfits")
    assert not differentiation_intent("画两个角色并加上她们的 LoRA")
    assert not differentiation_intent("两个女孩合照")
    print("== 23. 用户「要求差异化」意图判定 OK")


def test_assign_personal_to_blocks():
    """公共区的角色专属标签要归位到各角色块，而不是留在公共区让两人共用。"""
    positive = ("masterpiece, 2girls, (suomingwuwa:1.2), (xinyuehu:1.2), side_by_side, "
                "night, petite, tall, black dress, red dress, sitting, standing, blush")
    per_char = [{"anchors": ["red hair", "fox ears"]},
                {"anchors": ["long white hair", "orange eyes"]}]
    out, notes = build_hybrid_multi_person(positive, per_char, assign_personal=True)
    # 四项专属标签应被拆到两个块里，且不再出现在公共段
    head = out.split("red-haired")[0]
    for t in ("petite", "tall", "black dress", "red dress", "sitting", "standing", "blush"):
        assert t not in head, (t, out)
    a_block = out.split("red-haired")[1].split(".")[0]
    b_block = out.split("white-haired")[1]
    assert "petite" in a_block and "black dress" in a_block and "sitting" in a_block, out
    assert "tall" in b_block and "red dress" in b_block and "standing" in b_block, out
    # 公共区保留真正的公共项
    assert "side_by_side" in head and "night" in head, out
    # 不启用归位时，公共专属标签留在公共区（维持用户自己的选择）
    out2, _ = build_hybrid_multi_person(positive, per_char, assign_personal=False)
    assert "petite" in out2.split("red-haired")[0], out2
    print("== 24. 公共区角色专属标签归位到各角色块 OK")


def test_lora_description_as_anchor_source():
    """LoRA 库的 description 可直接当身份锚点（原创角色 Danbooru 查不到时的救命档）。"""
    desc = ("红发，双丸子头缀金铃，灰色渐变双马尾，红色瞳孔，黑色无肩带连衣裙，黑色外套披肩")
    anchors = identity_anchor_tags(desc)
    assert anchors, anchors
    joined = " ".join(anchors)
    assert "红发" in joined or "红" in joined, anchors
    assert "黑" in joined, anchors
    # 描述里没有的维度不会凭空出现
    assert "fox" not in joined.lower() and "cat" not in joined.lower(), anchors
    print("== 25. LoRA 描述可作身份锚点来源 OK")


def test_multi_shared_appearance():
    # 用户实测原样：两人共用的 black strapless dress / detached sleeves
    txt = ("2girls, (suomingwuwa, red hair, black strapless dress, detached sleeves:1.25), "
           "(xinyuehu, long white hair, black strapless dress, detached sleeves:1.25), night")
    is_multi, shared = multi_shared_appearance(txt)
    assert is_multi, shared
    assert "black strapless dress" in shared and "detached sleeves" in shared, shared
    assert "red hair" not in shared and "long white hair" not in shared, shared
    # 两人服装不同 → 不报
    txt2 = ("2girls, (suomingwuwa, black dress:1.2), (xinyuehu, white hanfu:1.2), night")
    _, shared2 = multi_shared_appearance(txt2)
    assert shared2 == [], shared2
    # 单人不报
    assert multi_shared_appearance("1girl, black dress, night") == (False, [])
    print("== 13. 多人共有外观/服装检测（撞脸信号） OK")


if __name__ == "__main__":
    test_appearance_dims()
    test_has_character_tag()
    test_strip_conflicting_appearance()
    test_strip_new_appearance()
    test_keeps_original_tags()
    test_free_dim_coverage_and_short()
    test_quality_prefix()
    test_multi_person_detection()
    test_multi_person_hints()
    test_merge_multi_negative()
    test_strip_lora_echo_appearance()
    test_appearance_change_intent()
    test_multi_shared_appearance()
    test_clean_multi_count_conflicts()
    test_align_character_lora_triggers()
    test_strip_lora_echo_appearance_with_outfit()
    test_outfit_intent()
    test_identity_anchor_tags()
    test_visual_label_for()
    test_build_hybrid_multi_person()
    test_end_to_end_multi_identity()
    test_classify_common_tags()
    test_differentiation_intent()
    test_assign_personal_to_blocks()
    test_lora_description_as_anchor_source()
    print("提示词护栏全部通过")
