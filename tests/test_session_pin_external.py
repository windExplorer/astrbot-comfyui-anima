"""会话固定工作流「越界覆盖外部插件工作流」回归守卫（v8.13.1）。

报障：在会话管理里给某个群固定了工作流之后，其他插件（星语 / 萌萌日程 /
今日老婆 / Studio 等）在同一群用【合成 event】调 comfyui_draw 出图时，
也被强制用这个固定工作流，没有用插件在自己配置里显式指定的 workflow。

根因：_do_draw 调用 _resolve_workflow 时写死 apply_session_pin=not bool(_wr_force_wf)，
对所有出图调用都套上「会话固定工作流」。外部插件的合成 event 经 _session_umo_of
归一到同一群 UMO，于是 pinned_workflow() 命中，覆盖了调用方传入的 workflow 名。
固定工作流的语义本应是「本群【聊天出图】的偏好」，不该管辖其他插件主动发图。

修复：外部调用（_is_external_call(source, external) 为真）一并绕过会话 pin，
即 apply_session_pin=not bool(_wr_force_wf) and not _ext_call。

跑法：python tests/test_session_pin_external.py
"""
from __future__ import annotations

import ast
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import session_store as ss_mod  # noqa: E402


class _Log:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _load_main_parts():
    """用 AST 从 main.py 原样抠出 _is_external_call / _resolve_workflow 及依赖常量。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    ns: dict = {"logger": _Log(), "session_store": ss_mod, "time": time}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SOURCE_COMPANION_PLUGIN":
                    exec(ast.get_source_segment(src, node), ns)  # noqa: S102
        if isinstance(node, ast.FunctionDef) and node.name == "_is_external_call":
            exec(ast.get_source_segment(src, node), ns)  # noqa: S102
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ComfyUIDrawPlugin")
    want = {"_resolve_workflow", "_alias_workflow_name", "_wf_usable"}
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in want:
            seg = ast.get_source_segment(src, node) or ""
            body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
            exec(textwrap.dedent(body), ns)  # noqa: S102
    for name in want | {"_is_external_call", "SOURCE_COMPANION_PLUGIN"}:
        assert name in ns, f"没抠到: {name}"
    return src, ns


class _FakeSessions:
    def __init__(self, pinned: str):
        self.pinned = pinned

    def pinned_workflow(self, sk, is_img2img):
        return self.pinned

    def session_workflow(self, sk, is_img2img):
        return ""


class _FakePlugin:
    """最小插件替身：会话 pin 固定为「群固定工作流」，全局/兜底默认「插件默认工作流」。"""

    def __init__(self, ns, pinned="群固定工作流"):
        self.sessions = _FakeSessions(pinned)
        self._resolve_workflow = ns["_resolve_workflow"].__get__(self, _FakePlugin)
        self._alias_workflow_name = ns["_alias_workflow_name"].__get__(self, _FakePlugin)
        self._wf_usable = ns["_wf_usable"].__get__(self, _FakePlugin)

    def _workflows(self):
        return [
            {"name": "插件默认工作流", "enabled": True},
            {"name": "群固定工作流", "enabled": True},
        ]

    def _cfg(self, key, default=""):
        return default

    def _pick_default_workflow_name(self, is_img2img):
        return "插件默认工作流"

    def _get_session_workflow(self, sid):
        return ""


def test_pin_scope_in_resolver() -> None:
    """_resolve_workflow 行为：apply_session_pin 开→固定覆盖，关→用调用方指定。"""
    _, ns = _load_main_parts()
    p = _FakePlugin(ns)
    umo = "aiocqhttp:GroupMessage:123456"

    # 原生聊天出图：apply_session_pin=True → 群固定工作流生效（原有语义保留）
    wf = p._resolve_workflow("插件默认工作流", apply_session_pin=True, session_umo=umo)
    assert wf["name"] == "群固定工作流", wf

    # 外部插件出图：主路径传 apply_session_pin=False → 不会被群固定覆盖
    wf = p._resolve_workflow("插件默认工作流", apply_session_pin=False, session_umo=umo)
    assert wf["name"] == "插件默认工作流", wf
    print("== 1. 会话固定仅在 apply_session_pin=True 时覆盖工作流 OK")


def test_external_call_detection() -> None:
    """判定入口：external=True 或历史 companion source 都算外部调用。"""
    _, ns = _load_main_parts()
    is_ext = ns["_is_external_call"]
    assert is_ext(external=True) is True
    assert is_ext(source=ns["SOURCE_COMPANION_PLUGIN"]) is True
    assert is_ext(source="webui_studio") is False  # Studio 靠 external=True 声明
    assert is_ext(external=True, source="webui_studio") is True
    assert is_ext() is False  # 原生聊天：什么都不传
    print("== 2. 外部调用判定（external / companion source）OK")


def test_callsite_guard() -> None:
    """守卫 _do_draw 调用点：会话 pin 必须同时被角色卡与外部调用两道开关拦下。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    assert "_ext_call = _is_external_call(source, external)" in src, "缺外部调用判定"
    assert (
        "apply_session_pin=not bool(_wr_force_wf) and not _ext_call" in src
    ), "会话固定工作流未对外部调用关闭"
    print("== 3. _do_draw 调用点对外部调用关闭会话 pin OK")


if __name__ == "__main__":
    test_pin_scope_in_resolver()
    test_external_call_detection()
    test_callsite_guard()
    print("RESULT: session_pin_external OK")
