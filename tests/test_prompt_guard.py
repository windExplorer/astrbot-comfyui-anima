"""提示词护栏（v7.6.0）：外观维度识别 / 冲突剥除 / 扩写校验 / 质量前缀分档。

跑法：python tests/test_prompt_guard.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prompt_guard import (  # noqa: E402
    appearance_dims, ensure_quality_prefix, free_dim_coverage, has_character_tag,
    is_short_prompt, keeps_original_tags, prompt_style_of, quality_prefix_for,
    strip_conflicting_appearance, strip_new_appearance,
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


if __name__ == "__main__":
    test_appearance_dims()
    test_has_character_tag()
    test_strip_conflicting_appearance()
    test_strip_new_appearance()
    test_keeps_original_tags()
    test_free_dim_coverage_and_short()
    test_quality_prefix()
    print("提示词护栏全部通过")
