"""基础工作流「编辑保存」的字段透传契约（v7.7.2）。

真实事故：在「基础工作流」页编辑弹窗里选了底模（`basemodel_id`）并保存，列表仍显示
「未关联」。原因**不在存储层**——`WorkflowStore.update_meta` 一直支持 basemodel_id，
而是 `webui_api.baseworkflows_meta` 这个中转层只白名单转发了
`name/civitai_url/image/description` 四个字段，`basemodel_id` 在 handler 里就被丢掉了，
存储层根本没收到，所以「保存成功」但底模没写进去。

这个测试盯两件事：
1) 存储层：五个元数据字段（含 basemodel_id）都能真的写进去、读得出来、能清空；
2) 契约层：handler 的白名单必须覆盖 UI 能改的字段 —— 用 AST 读出白名单元组，
   再和 `BaseWorkflowsView.vue` 里实际发送的 payload 比对（少一个就报错）。

跑法：python tests/test_baseworkflow_meta.py
"""
import ast
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from workflow_store import WorkflowStore      # noqa: E402

VOSR2 = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
    "2": {"class_type": "TESpeedVOSR2Image",
          "inputs": {"scale": ["6", 0], "seed": 6666, "model": ["3", 0],
                     "images": ["1", 0], "settings": ["4", 0]}},
    "3": {"class_type": "TESpeedVOSR2Loader", "inputs": {"model_bundle": "VOSR2"}},
    "4": {"class_type": "TESpeedVOSR2Settings", "inputs": {"quality_profile": "speed"}},
    "6": {"class_type": "easy int", "inputs": {"value": 3}},
    "9": {"class_type": "SaveImageExtended", "inputs": {"images": ["2", 0]}},
}


def test_store_meta_fields():
    """存储层：name / civitai_url / image / description / basemodel_id 都能写、读、清空。"""
    st = WorkflowStore(Path(tempfile.mkdtemp()))
    wf_id, _roles, err = st.import_json("放大图", json.dumps(VOSR2), "vosr2-api.json")
    assert err is None and wf_id, (wf_id, err)
    assert st.get(wf_id)["basemodel_id"] == 0        # 初始未关联

    err = st.update_meta(wf_id, {
        "name": "改名了", "civitai_url": "https://civitai.com/models/1",
        "image": "cover.png", "description": "描述", "basemodel_id": 7,
    })
    assert err is None, err
    rec = st.get(wf_id)
    assert rec["basemodel_id"] == 7, rec
    assert rec["name"] == "改名了" and rec["civitai_url"].endswith("/models/1")
    assert rec["image"] == "cover.png" and rec["description"] == "描述"

    # 底模可以再改回「未关联」（UI 的「未关联」选项 value=0）
    assert st.update_meta(wf_id, {"basemodel_id": 0}) is None
    assert st.get(wf_id)["basemodel_id"] == 0
    # 只传列表里没有的字段 → 明确报错（不是静默成功）
    assert st.update_meta(wf_id, {"不存在的字段": 1}) == "没有可更新的字段"
    print("== 1. 存储层元数据字段（含底模关联的写入/清空） OK")


def test_meta_handler_whitelist():
    """契约层：baseworkflows_meta 的白名单必须覆盖 UI 发送的字段。"""
    src = (ROOT / "webui_api.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    # 按名字全局找（handler 挂在哪个类/层级都行，避免依赖类嵌套结构）；
    # 注意：webui_api 的 handler 都是 `async def` → ast.AsyncFunctionDef
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "baseworkflows_meta")
    # handler 里转发给 update_meta 的字段白名单（字符串元组）
    keys = {
        e.value
        for node in ast.walk(fn)
        if isinstance(node, ast.Tuple)
        for e in node.elts
        if isinstance(e, ast.Constant) and isinstance(e.value, str)
    }
    need = {"name", "civitai_url", "image", "description", "basemodel_id"}
    assert need <= keys, f"handler 白名单漏字段：{need - keys}（现有 {sorted(keys)}）"

    # UI 的编辑弹窗确实会发 basemodel_id（另一头也没漏）
    vue = (ROOT / "webui-src" / "src" / "views" / "BaseWorkflowsView.vue").read_text(
        encoding="utf-8"
    )
    i = vue.index('apiPost("baseworkflows/meta"')
    assert "basemodel_id" in vue[i:i + 400], "编辑弹窗的保存 payload 里没有 basemodel_id"

    # 列表接口要把底模 id 翻成名字（不然前端只能显示「未关联」）
    api = (ROOT / "webui_api.py").read_text(encoding="utf-8-sig")
    assert 'it["basemodel_name"]' in api, "列表接口没有回填 basemodel_name"
    print("== 2. handler 白名单覆盖 UI 字段 + 列表回填底模名 OK")


if __name__ == "__main__":
    test_store_meta_fields()
    test_meta_handler_whitelist()
    print("基础工作流元数据契约（底模关联）全部通过")
