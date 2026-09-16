"""校验所有 LLM 工具（@filter.llm_tool）的 docstring 能被正确解析成参数 schema。

为什么需要这个测试
------------------
AstrBot 的 `@filter.llm_tool` **只靠 docstring** 生成工具参数 schema
（`astrbot/core/star/register/star_handler.py` → `docstring_parser.parse(func_doc)`）。
它用的是 `DocstringStyle.AUTO`：会依次尝试 REST / GOOGLE / NUMPYDOC / EPYDOC，
把解析成功的结果里「meta 最多」的那个作为返回值，**解析失败的风格被静默跳过**。

于是当 Args 段写得不符合 Google 风格时，GOOGLE 解析抛 ParseError 被吞掉，
最终返回 0 个参数 —— 工具照常注册、照样能被模型看到，但**一个参数都没有**，
不报错、不打日志。历史事故：
  - `comfyui_draw` 自 v5.10.12 起 0 参数（裸行「★以上四个参数只作用于第三方平台生图…」）
  - `comfyui_comic` 自 v5.11.11 起 0 参数（裸行「image、denoise：…」，中文冒号）

Google 风格的硬性要求（Args 段内每一行）
----------------------------------------
  ✅ `name(type): 描述`            —— 参数行，缩进与首个参数行一致
  ✅ 缩进 **比参数行更深** 的续行   —— 会被并入上一个参数的描述
  ❌ 与参数行同缩进的裸说明行       —— 整段 Args 解析失败（尤其含中文冒号 `：`）
另外 `width、height(number): …` 这种「两个参数写一行」不会报错，但会生成一个名为
`width、height` 的假参数，LLM 永远填不对 —— 所以下面也校验参数名必须真实存在。

用法
----
    uv run --no-project --with "docstring-parser>=0.16" python tests/test_llm_tool_docstrings.py
（不需要 AstrBot 运行环境，纯 AST + docstring_parser）
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import docstring_parser
from docstring_parser import DocstringStyle

MAIN_PY = Path(__file__).resolve().parent.parent / "main.py"

# 这些工具必须有参数，且必须包含列出的参数名（缺了就是 schema 解析坏了）
REQUIRED = {
    "comfyui_draw": ["prompt", "prompts", "loras", "platform", "artist"],
    "comfyui_img2img": ["prompt", "prompts", "loras", "image"],
    "comfyui_comic": ["prompt"],
    "comfyui_meme_img": ["prompt", "image"],
    "comfyui_gallery": ["mode", "keyword", "tag", "limit"],
    "comfyui_loras": ["base_model", "keyword", "category"],
    "comfyui_character": ["action", "name", "positive", "anchor_name", "persona_name"],
    "nai_codex": ["keyword", "scope", "full", "limit"],
}

# 这两个工具按设计就没有参数（只读查询），无需补
NO_PARAM_BY_DESIGN = {"comfyui_workflows", "comfyui_platforms"}

# AstrBot 支持的 JSON schema 类型（func_tool_manager.SUPPORTED_TYPES）
SUPPORTED_TYPES = {
    "string", "number", "integer", "boolean", "object", "array",
    # PY_TO_JSON_TYPE 会先把这些 Python 名映射掉，这里兜底
    "str", "int", "float", "bool", "list", "dict",
}


def astrbot_type_names(type_name: str) -> list[str]:
    """复刻 AstrBot 对 docstring 里类型注解的处理。

    `array[string]` 会被拆成 `array`（外层）与 `string`（items 子类型），两者都要受支持。
    """
    if not type_name:
        return []
    m = re.match(r"(\w+)\[(\w+)\]", type_name)
    if m:
        return [m.group(1), m.group(2)]
    return [type_name]


def collect_tools(tree: ast.Module) -> list[tuple[str, ast.AsyncFunctionDef]]:
    """取出所有被 @filter.llm_tool(name=...) 装饰的函数。"""
    tools = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "llm_tool":
                for kw in dec.keywords:
                    if kw.arg == "name":
                        tools.append((ast.literal_eval(kw.value), node))
    return tools


def main() -> int:
    # Windows 控制台默认 GBK，中文/符号直接 print 会 UnicodeEncodeError
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    errors: list[str] = []
    tools = collect_tools(ast.parse(MAIN_PY.read_text(encoding="utf-8-sig")))

    print(f"docstring-parser: {Path(docstring_parser.__file__).parent.name}")
    print(f"扫描 {MAIN_PY.name}：发现 {len(tools)} 个 LLM 工具\n")

    for name, node in tools:
        raw = ast.get_docstring(node, clean=False) or ""
        sig_params = [
            a.arg
            for a in node.args.args
            if a.arg not in ("self", "event")  # AstrBot 自动注入，不进 schema
        ]

        # 1) Google 风格必须能解析（AstrBot 的 AUTO 会吞掉这里的异常）
        try:
            google = docstring_parser.parse(raw, style=DocstringStyle.GOOGLE)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: Google 风格解析失败 → {type(e).__name__}: {e}")
            print(f"✗ {name:<20} Google 解析异常")
            continue

        # 2) AUTO（AstrBot 实际用的）结果必须与 Google 一致，否则说明退化成了空 schema
        auto = docstring_parser.parse(raw)
        g_params = [(p.arg_name, p.type_name) for p in google.params]
        a_params = [(p.arg_name, p.type_name) for p in auto.params]

        # 3) 参数名必须是函数签名里真实存在的参数
        for arg_name, type_name in g_params:
            if arg_name not in sig_params:
                errors.append(
                    f"{name}: docstring 里的参数 {arg_name!r} 不在函数签名中"
                    f"（签名：{sig_params}）——多半是两个参数写成了一行"
                )
            for tn in astrbot_type_names(type_name):
                if tn not in SUPPORTED_TYPES:
                    errors.append(f"{name}: 参数 {arg_name} 的类型 {tn!r} 不受支持")

        # 4) 必需参数不得缺失
        need = REQUIRED.get(name, [])
        for r in need:
            if r not in dict(g_params):
                errors.append(f"{name}: 缺少必需参数 {r!r}")

        if not g_params and name not in NO_PARAM_BY_DESIGN:
            errors.append(f"{name}: 参数解析为空（Args 段格式不合法）")
        if a_params != g_params:
            errors.append(
                f"{name}: AstrBot 实际使用的 AUTO 解析结果与 Google 不一致\n"
                f"      AUTO  = {a_params}\n      GOOGLE= {g_params}"
            )

        mark = "✓" if (g_params or name in NO_PARAM_BY_DESIGN) else "✗"
        shown = ", ".join(f"{a}:{t}" for a, t in g_params) or "(无参数)"
        print(f"{mark} {name:<20} {len(g_params):2d} 项 | {shown}")

    print()
    if errors:
        print(f"发现 {len(errors)} 个问题：")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("全部通过：所有 LLM 工具的参数 schema 都能被 AstrBot 正确解析。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
