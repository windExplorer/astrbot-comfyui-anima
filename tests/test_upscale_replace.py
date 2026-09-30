"""放大模型替换决策测试（v7.7.56）。

背景（回归用例）：新版工作流（base_id）的 `upscale_node_id` 由 `_load_from_base`
从基础工作流解析注记里**自动**注入，用户根本没配放大。旧代码拿它去套旧版那句
「只填一项 = 配置不完整」，导致「跟随基础工作流 + 什么都没配」的默认状态也被告警：

    【放大模型】 配置不完整：需同时填写「放大模型节点」和「放大模型名称」才会生效…

`_upscale_replace_plan` 就是把这个判定从出图主链路里抠出来的纯函数——测试用 ast
把它的源码从 main.py 摘出来单独执行（main.py 依赖 astrbot 运行时，本机装不了）。

跑法：python tests/test_upscale_replace.py
"""
import ast
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_plan():
    # main.py 带 UTF-8 BOM，用 utf-8-sig 读掉，否则 ast.parse 会报非法字符
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    ns: dict = {}
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "_upscale_replace_plan":
            continue
        seg = ast.get_source_segment(src, node) or ""
        body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
        exec(textwrap.dedent(body), ns)  # noqa: S102
    assert "_upscale_replace_plan" in ns, "没摘到 _upscale_replace_plan"
    return ns["_upscale_replace_plan"]


plan = _load_plan()


def test_legacy_workflow():
    """旧版工作流：两个字段都是用户手填的，语义与历史一致。"""
    # 都填 → 替换
    assert plan({"upscale_node_id": "14", "upscale_model_name": "4x-UltraSharp.pth"}) == (
        "14", "4x-UltraSharp.pth", "replace", False)
    # 只填一项 → 告警「配置不完整」
    assert plan({"upscale_node_id": "14", "upscale_model_name": ""})[2] == "legacy"
    assert plan({"upscale_model_name": "4x-UltraSharp.pth"})[2] == "legacy"
    # 都不填 → 什么都不做（沿用工作流默认放大模型）
    assert plan({}) == ("", "", "none", False)
    assert plan({"upscale_node_id": "", "upscale_model_name": None}) == ("", "", "none", False)
    print("== 1. 旧版工作流（手填两项 / 缺项告警 / 空配置静默） OK")


def test_new_workflow_follow_no_config():
    """★本 bug 的核心回归：新版跟随基础工作流、没配任何放大项 → 必须静默。

    `upscale_node_id` 由 `_load_from_base` 从基础图解析注记注入（这里非空），
    `upscale_model_name` 用户留空 —— 不能判成「配置不完整」。
    """
    assert plan({"base_id": "3", "upscale_node_id": "11", "upscale_model_name": ""}) == (
        "", "", "none", True)
    # 基础图无放大链时，注入的节点就是空串，同样静默
    assert plan({"base_id": "3", "upscale_node_id": "", "upscale_model_name": ""}) == (
        "", "", "none", True)
    assert plan({"base_id": "3"}) [2] == "none"
    print("== 2. 新版跟随 + 未配放大（不再误报告警） OK")


def test_new_workflow_configured():
    """新版：配了放大模型名时按模式分流。"""
    # 基础图有放大链 + 填了模型名 → 替换该节点模型
    assert plan({"base_id": "3", "upscale_node_id": "11",
                 "upscale_model_name": "RealESRGAN_x2plus.pth"}) == (
        "11", "RealESRGAN_x2plus.pth", "replace", True)
    # 跟随模式 + 基础图无放大链（节点空）+ 填了模型名 → 可行动提示（改选注入）
    assert plan({"base_id": "3", "upscale_model_name": "4x-UltraSharp.pth", "upscale_mode": ""}) == (
        "", "4x-UltraSharp.pth", "hint", True)
    # 绕过模式 → 故意不用放大，静默
    assert plan({"base_id": "3", "upscale_model_name": "4x-UltraSharp.pth", "upscale_mode": "bypass"})[2] == "none"
    # 注入模式且已注入（_upscale_apply 有值）→ 静默（注入链路已带模型）
    assert plan({"base_id": "3", "upscale_model_name": "4x-UltraSharp.pth",
                 "upscale_mode": "inject", "_upscale_apply": "90"})[2] == "none"
    # 注入模式但没注入成功 → 由 `_apply_base_overrides` 报原因，这里不重复提示
    assert plan({"base_id": "3", "upscale_model_name": "4x-UltraSharp.pth",
                 "upscale_mode": "inject"})[2] == "none"
    print("== 3. 新版配了放大（替换 / 提示 / 绕过 / 已注入 分流） OK")


if __name__ == "__main__":
    test_legacy_workflow()
    test_new_workflow_follow_no_config()
    test_new_workflow_configured()
    print("放大模型替换决策测试通过")
