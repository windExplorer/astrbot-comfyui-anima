# -*- coding: utf-8 -*-
"""配置页分组守卫：schema 顶层键必须全部有归宿，且不出现幽灵键。

ConfigView.vue 的 GROUP_META 硬编码了每个配置键所属分区，没进表的键会掉进
兜底「其他」分区。历史事故：
- v5.12.9：permissions / recall 漏进「其他」，用户找不到；
- v7.13.x：内部迁移标记 _size_presets_v2、旧兼容键 nsfw_group_whitelist
  因前端不读 invisible 而暴露给用户。

本测试复刻前端过滤口径（HIDDEN_KEYS ∪ 分组 keys ∪ schema.invisible），
保证：① 每个 schema 键要么被分组覆盖、要么在 HIDDEN_KEYS、要么 invisible；
② HIDDEN_KEYS / 分组 keys 里没有指向已删除键的幽灵项。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _schema_keys():
    data = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    invisible = {k for k, v in data.items() if isinstance(v, dict) and v.get("invisible")}
    return set(data), invisible


def _vue_config():
    src = (ROOT / "webui-src/src/views/ConfigView.vue").read_text(encoding="utf-8")
    m = re.search(r"const HIDDEN_KEYS = \[(.*?)\];", src, re.S)
    assert m, "找不到 HIDDEN_KEYS"
    hidden = set(re.findall(r'"([^"]+)"', m.group(1)))
    m = re.search(r"const GROUP_META = \[(.*?)\n\];", src, re.S)
    assert m, "找不到 GROUP_META"
    grouped = set(re.findall(r'"([a-z0-9_]+)"', m.group(1)))
    return hidden, grouped, src


def _group_names_and_aliases(src):
    """分组名（中文）与深链别名映射，用于守卫深链目标有效。"""
    m = re.search(r"const GROUP_META = \[(.*?)\n\];", src, re.S)
    names = set(re.findall(r'name:\s*"([^"]+)"', m.group(1)))
    m = re.search(r"const GROUP_ALIASES: Record<string, string> = \{(.*?)\n\};", src, re.S)
    aliases = {}
    if m:
        aliases = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', m.group(1)))
    return names, aliases


def test_all_schema_keys_have_home():
    keys, invisible = _schema_keys()
    hidden, grouped, _ = _vue_config()
    # 前端过滤口径：hiddenKeys = HIDDEN_KEYS ∪ invisible；leftover 再排除 grouped
    orphans = sorted(k for k in keys if k not in hidden and k not in invisible and k not in grouped)
    assert not orphans, f"这些配置键会掉进 WebUI「其他」分区: {orphans}"


def test_no_ghost_keys():
    keys, _ = _schema_keys()
    hidden, grouped, _ = _vue_config()
    ghosts = sorted((hidden | grouped) - keys)
    # upscale_limits / features 是「更多功能」页管理的真实键，在 schema 里；
    # v7.0.0 移除的 image_upscale / matting 已不该再出现。
    assert not ghosts, f"HIDDEN_KEYS/GROUP_META 引用了已不存在的键: {ghosts}"


def test_no_duplicate_key_across_groups():
    """v7.15.0 重排分组后，一个配置键只能出现在一个分组里。"""
    src = (ROOT / "webui-src/src/views/ConfigView.vue").read_text(encoding="utf-8")
    m = re.search(r"const GROUP_META = \[(.*?)\n\];", src, re.S)
    keys = re.findall(r'"([a-z0-9_]+)"', m.group(1))
    dups = sorted({k for k in keys if keys.count(k) > 1})
    assert not dups, f"这些配置键出现在多个分组（界面重复展示）: {dups}"


def test_deeplink_aliases_resolve_to_real_groups():
    """深链别名（含调用方实际传的旧名）必须指向真实分组名，否则跳转失效。"""
    _, _, src = _vue_config()
    names, aliases = _group_names_and_aliases(src)
    bad = {k: v for k, v in aliases.items() if v not in names}
    assert not bad, f"深链别名指向不存在的分组: {bad}"
    # 工作流页「编辑预设 ↗」实际传的名字必须能解析
    wf = (ROOT / "webui-src/src/views/WorkflowsView.vue").read_text(encoding="utf-8")
    sent = set(re.findall(r'group:\s*"([^"]+)"', wf))
    for name in sent:
        assert name in names or name in aliases, f"工作流页深链传了无法解析的分组名: {name}"


def test_internal_keys_marked_invisible_or_hidden():
    keys, invisible = _schema_keys()
    hidden, _, _ = _vue_config()
    for k in ("_size_presets_v2", "nsfw_group_whitelist"):
        if k in keys:
            assert k in invisible or k in hidden, f"内部/兼容键 {k} 未隐藏，会暴露给用户"


if __name__ == "__main__":
    test_all_schema_keys_have_home()
    test_no_ghost_keys()
    test_no_duplicate_key_across_groups()
    test_deeplink_aliases_resolve_to_real_groups()
    test_internal_keys_marked_invisible_or_hidden()
    print("config groups OK: keys grouped once, no ghosts, deeplinks valid, internal keys hidden")
