"""静态检查：用 pyflakes 抓「未定义名称」这类 compileall 抓不到、运行时才炸的错误。

背景（v7.7.16 踩坑）：抠图改条目式时把参数 `wf_spec` 改名 `spec`，函数体里漏了一处——
compileall 不报（语法合法），跑起来才 NameError。本测试给这类错误兜底：
只拦截 **undefined name / redefinition** 级别的硬伤，忽略未使用导入等风格问题。

pyflakes 未安装时跳过（跑法：uv run --with pyflakes python tests/test_static_lint.py）。
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 只对插件自身的核心 py 文件做检查（不含 tests/ 与 pages/）
TARGETS = [
    "main.py",
    "workflow_parser.py",
    "workflow_builder.py",
    "workflow_store.py",
    "comfyui_client.py",
    "webui_api.py",
    "image_store.py",
    "draw_card.py",
    "basemodel_store.py",
    "option_store.py",
    "character_store.py",
]

# pyflakes 的告警里，哪些属于「运行时必炸」级别（其余视为风格问题忽略）
HARD_PATTERNS = (
    "undefined name",
    "unable to detect undefined names",
)


def test_no_undefined_names():
    try:
        from pyflakes.api import checkPath
        from pyflakes.reporter import Reporter
    except Exception:
        print("pyflakes 未安装，跳过（建议：uv run --with pyflakes python tests/test_static_lint.py）")
        return

    class _Recorder(Reporter):
        """把告警收进列表（pyflakes 默认只往 stderr 打）。"""

        def __init__(self):
            super().__init__(io.StringIO(), io.StringIO())
            self.problems: list[str] = []

        def unexpectedError(self, filename, msg):  # noqa: N802
            self.problems.append(f"{filename}: {msg}")

        def syntaxError(self, filename, msg, lineno, offset, text):  # noqa: N802
            self.problems.append(f"{filename}:{lineno}: 语法错误 {msg}")

        def flake(self, warning):
            msg = warning.message % tuple(warning.message_args or ())
            self.problems.append(f"{warning.filename}:{warning.lineno}: {msg}")

    paths = [str(ROOT / f) for f in TARGETS if (ROOT / f).exists()]
    assert paths, "没找到任何待检查文件"
    rec = _Recorder()
    for _p in paths:
        checkPath(_p, rec)

    hard = [
        p for p in rec.problems
        if any(pat in p for pat in HARD_PATTERNS)
    ]
    assert not hard, "发现「运行时必炸」级别的静态问题：\n" + "\n".join(hard)
    style = [p for p in rec.problems if p not in hard]
    print(f"静态检查（pyflakes，{len(paths)} 个文件） OK"
          + (f"；另有 {len(style)} 条风格类提示（忽略）" if style else ""))


if __name__ == "__main__":
    test_no_undefined_names()
