"""LoRA 按底模筛选（v7.5.3）。

背景：新版工作流不再使用 `base_model` 字段（走 `base_id` 关联基础工作流），
原先「LoRA 是否适用于该工作流」只读 `wf["base_model"]` → 恒为空 → 过滤形同虚设，
WebUI 下拉与 `/绘图lora` 都把全库 LoRA 列了出来。

这里测两件事：
1) 底模匹配规则（与前端 `baseModelFits` 同一套）：空=通用/不限，小写原文相等，
   去标点后相等 / 前缀 / 包含（「Z-Image Turbo」↔「z-image-turbo」）；
2) 新版工作流的底模解析：base_id → 基础工作流 → 底模库 name，取不到再退回旧版字段。

这几个方法住在 main.py（依赖 astrbot 运行时，本地装不了），沿用 tests/test_size_helpers.py
的做法：用 ast 摘出源码单独执行。

跑法：python tests/test_lora_base_filter.py
"""
import ast
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Log:
    """顶替 astrbot 的 logger（摘出来的方法只用 debug）。"""

    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _load_helpers(want: set[str]) -> dict:
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")   # main.py 带 BOM
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ComfyUIDrawPlugin")
    ns: dict = {"re": re, "logger": _Log()}
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in want:
            continue
        seg = ast.get_source_segment(src, node) or ""
        body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
        exec(textwrap.dedent(body), ns)  # noqa: S102
    missing = want - set(ns)
    assert not missing, f"没摘到这些方法: {missing}"
    return ns


NAMES = {"_bm_key", "_bm_fits", "_wf_base_model", "_lora_matches_wf"}
NS = _load_helpers(NAMES)

# 摘出来的都是普通函数：_bm_key(静态) 直接调；其余把假 self 当第一个参数传
_bm_key = NS["_bm_key"]
_bm_fits = NS["_bm_fits"]
_wf_base_model = NS["_wf_base_model"]
_lora_matches_wf = NS["_lora_matches_wf"]


class _FakePlugin:
    """只实现 _wf_base_model 依赖的 `_basemodel_of_workflow`（底模库记录）。"""

    def __init__(self, basemodels: dict):
        self._bm = basemodels       # {basemodel_id: record}

    def _basemodel_of_workflow(self, w: dict):
        """真实实现会做 base_id → 基础工作流 → basemodel_id → 底模库；
        这里直接按工作流上挂的 basemodel_id 取（等价结果，去掉存储依赖）。"""
        _bid = int((w or {}).get("basemodel_id") or 0)
        return self._bm.get(_bid) if _bid else None


# 这几个方法内部互相调用（`self._bm_key` / `self._bm_fits`）→ 全部绑给假 self
_FakePlugin._bm_key = staticmethod(_bm_key)  # type: ignore[attr-defined]
_FakePlugin._bm_fits = _bm_fits              # type: ignore[attr-defined]
_FakePlugin._wf_base_model = _wf_base_model  # type: ignore[attr-defined]


def test_normalize():
    assert _bm_key("Z-Image Turbo") == "zimageturbo"
    assert _bm_key(" z-image_turbo. ") == "zimageturbo"
    assert _bm_key("") == ""
    assert _bm_key(None) == ""
    print("== 1. 底模名归一化（去空格/连字符/下划线/点） OK")


def test_bm_fits():
    me = _FakePlugin({})
    cases = [
        # (LoRA 底模, 工作流底模, 期望)
        ("anima", "Anima Pencil XL v5", True),
        ("noobai", "NoobAI XL v-pred", True),
        ("z-image-turbo", "Z-Image Turbo", True),
        ("krea2", "Krea 2", True),
        ("qwen", "Qwen Image 2.1", True),
        ("illustrious", "Illustrious XL 1.0", True),
        ("illustrious", "NoobAI XL", False),
        ("krea2", "Anima Pencil XL v5", False),
        ("", "Anima Pencil XL", True),      # LoRA 通用 → 任何底模都可用
        (None, "NoobAI XL", True),
        ("anima", "", True),                # 工作流不限底模 → 任何 LoRA 都可用
        ("anima", None, True),
    ]
    for lora_bm, wf_bm, exp in cases:
        got = _bm_fits(me, lora_bm, wf_bm)
        assert got is exp, f"{lora_bm!r} vs {wf_bm!r}: 期望 {exp}，实际 {got}"
        assert me._bm_fits(lora_bm, wf_bm) is exp   # 走 self 调用（与线上一致）
    print(f"== 2. 底模匹配规则（{len(cases)} 组用例） OK")


def test_wf_base_model_resolve():
    me = _FakePlugin({1: {"name": "Anima Pencil XL v5"}, 2: {"name": "NoobAI XL"}})
    # 新版：base_id + basemodel_id → 底模库 name
    assert _wf_base_model(me, {"base_id": "7", "basemodel_id": 2}) == "NoobAI XL"
    # 取不到底模记录 → 退回旧版 base_model 字段
    assert _wf_base_model(me, {"base_id": "7", "base_model": "anima"}) == "anima"
    # 都没有 → 空（= 不限底模）
    assert _wf_base_model(me, {"name": "wf"}) == ""
    assert _wf_base_model(me, {}) == ""
    print("== 3. 工作流底模解析（新版走基础工作流 / 旧版回退字段） OK")


def test_lora_matches_wf():
    me = _FakePlugin({1: {"name": "Anima Pencil XL v5"}})
    # 新版工作流：base_model 字段是空的，以前这里必然 True（全放行）——现在是按底模判
    wf_new = {"name": "动漫日常", "base_id": "7", "basemodel_id": 1, "base_model": ""}
    assert _lora_matches_wf(me, {"name": "A", "base_model": "anima"}, wf_new) is True
    assert _lora_matches_wf(me, {"name": "B", "base_model": "illustrious"}, wf_new) is False
    assert _lora_matches_wf(me, {"name": "C", "base_model": ""}, wf_new) is True
    # 旧版工作流：仍按自己的 base_model
    wf_old = {"name": "老工作流", "base_model": "NoobAI XL"}
    assert _lora_matches_wf(me, {"name": "D", "base_model": "noobai"}, wf_old) is True
    assert _lora_matches_wf(me, {"name": "E", "base_model": "anima"}, wf_old) is False
    # 不限定底模的工作流：全放行
    assert _lora_matches_wf(me, {"name": "F", "base_model": "krea2"}, {"base_model": ""}) is True
    print("== 4. LoRA 适用性判定（新版按基础工作流底模） OK")


def _load_module_fn(name: str):
    """从 main.py 顶层摘一个模块级纯函数（_lora_reality_hint 不在类里）。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == name
    )
    seg = ast.get_source_segment(src, node) or ""
    ns: dict = {}
    exec(textwrap.dedent(seg), ns)  # noqa: S102
    return ns[name]


def test_llm_loras_no_cross_basemodel_fallback():
    """v8.14.6：comfyui_loras 不得在底模没命中时列出全库——
    那会把其他底模的同名 LoRA 当成「找到了」，再由 LLM 谎报「用了 LoRA」。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "ComfyUIDrawPlugin"
    )
    fn = next(
        n for n in cls.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "llm_loras"
    )
    body = ast.get_source_segment(src, fn) or ""
    assert "check_bm=False" not in body, "仍有 check_bm=False 的全库放宽"
    assert "列出全部供参考" not in body, "仍有跨底模列全库的兜底"
    assert "_resolve_workflow" in body and "apply_session_pin=True" in body, \
        "没有按当前会话固定工作流解析底模"
    print("== 5. comfyui_loras 按固定工作流底模过滤、不跨底模放宽 OK")


def test_draw_basemodel_check_uses_effective_model():
    """v8.14.6：出图底模硬校验必须走 _wf_base_model（覆盖新版 base_id 工作流），
    不能只读 wf['base_model']（新版恒空 → 校验失效）。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "ComfyUIDrawPlugin"
    )
    fn = next(
        n for n in cls.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_do_draw"
    )
    body = ast.get_source_segment(src, fn) or ""
    assert 'self._bm_key(self._wf_base_model(wf or {}))' in body, \
        "底模硬校验未用生效底模"
    assert 'stats["lora_rejected"].append' in body, \
        "被拒 LoRA 未回传给工具层"
    print("== 6. 出图底模硬校验覆盖新版工作流 + 拒绝回传 OK")


def test_lora_reality_hint():
    """v8.14.6：工具成功返回带上真实启用/拒绝结果，杜绝 LLM 谎报用了 LoRA。"""
    fn = _load_module_fn("_lora_reality_hint")
    assert fn(None) == ""
    assert fn({}) == ""
    assert fn({"lora_enabled": [], "lora_rejected": []}) == ""
    # 只有启用
    out = fn({"lora_enabled": ["鲸鱼娘"], "lora_rejected": []})
    assert "实际生效：鲸鱼娘" in out and "没有启用任何" not in out
    # 只有拒绝（用户反馈的场景：LLM 以为用了，实际被底模校验拒绝）
    out = fn({"lora_enabled": [], "lora_rejected": ["鲸鱼娘（底模不匹配）"]})
    assert "没有启用任何 LoRA" in out
    assert "请求但未生效：鲸鱼娘（底模不匹配）" in out
    assert "绝不能说用了" in out
    # 同时有启用和拒绝；重名去重
    out = fn({
        "lora_enabled": ["A", "A"],
        "lora_rejected": ["B（底模不匹配）", "B（底模不匹配）"],
    })
    assert out.count("实际生效：A。") == 1
    assert out.count("请求但未生效：B（底模不匹配）。") == 1
    print("== 7. LoRA 实际结果回传提示（启用/拒绝/去重） OK")


if __name__ == "__main__":
    test_normalize()
    test_bm_fits()
    test_wf_base_model_resolve()
    test_lora_matches_wf()
    test_llm_loras_no_cross_basemodel_fallback()
    test_draw_basemodel_check_uses_effective_model()
    test_lora_reality_hint()
    print("LoRA 底模筛选全部通过")
