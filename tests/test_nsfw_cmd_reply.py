"""v8.13.5：/涩图检测 走 TaskHub 队列 + 判定回复完整化的静态/函数级自检。

核心契约：
1. cmd_detect_timeout 配置存在，默认 240s（对齐 TaskHub queue_wait+infer_timeout）；
2. _nsfw_detect_remote 用该配置做超时、读 X-Queue-Position 头、回填 _via/_elapsed；
3. cmd_nsfw 的回复话术含「来源」「命中部位」，本地回退如实说明无部位定位；
4. 部位中文映射表覆盖 nsfw-check nudenet 主要类目。

    uv run --no-project --python 3.12 python tests/test_nsfw_cmd_reply.py
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(title, cond, detail=""):
    if cond:
        print(f"  [OK] {title}")
    else:
        raise AssertionError(f"{title} {detail}")


def test_schema():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8-sig"))
    nsfw = schema["gallery"]["items"]["nsfw"]["items"]
    ct = nsfw.get("cmd_detect_timeout")
    check("有 cmd_detect_timeout 配置项", ct is not None)
    check("默认 240s", ct["default"] == 240, ct)
    check("类型是 int", ct["type"] == "int", ct)
    check("hint 说明队列排队语义", "队列" in ct["hint"] and "240" in ct["hint"], ct["hint"])


def test_remote_timeout_and_queue_header():
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    check("读取 cmd_detect_timeout 配置", 'self._cfg("gallery.nsfw.cmd_detect_timeout", 240)' in src)
    check("超时上限钳制 900", "max(30, min(_timeout, 900))" in src)
    check("读 X-Queue-Position 响应头", 'resp.headers.get("X-Queue-Position")' in src)
    check("回填 _via 来源", 'body["_via"] = via' in src)
    check("回填 _elapsed 耗时", 'body["_elapsed"]' in src)
    check("回填 _queue_pos", 'body["_queue_pos"]' in src)
    check("client timeout 用配置值", "ClientTimeout(total=_timeout, sock_read=_timeout)" in src)
    # 请求仍带 plugin-cmd 来源标识（nsfw-check 审计用）
    check("保留 source=plugin-cmd", 'form.add_field("source", "plugin-cmd")' in src)


def test_reply_wording():
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    check("回复含来源字段", 'f"来源：{_source_of(r)}"' in src)
    check("有 _source_of 来源函数", "def _source_of(r: dict) -> str:" in src)
    check("云端来源标注（对用户不说内部链路名）", '"云端检测"' in src)
    check("本地来源标注无部位定位", "仅整体概率，无部位定位" in src)
    check("命中部位标注", '"命中部位："' in src)
    check("无人命中信号兜底", "无明确部位信号" in src)
    check("分歧提示", "主/副模型有分歧" in src)
    check("记录号展示", 'f"记录号 {rid}"' in src)
    check("检测中提示简洁（不堆队列细节）", 'await self._send(event, "⏳ 检测中，请稍候…")' in src)
    # 远程 action=review 也应进擦边档（不只是 level=review）
    check("action=review 并入擦边档", 'elif lv == "review" or action == "review":' in src)
    check("action=block 并入涩涩档", 'elif lv == "nsfw" or action == "block":' in src)


def test_no_internal_terms_in_user_text():
    """发给用户的字符串不得含内部链路名/依赖名/配置路径（注释与日志不受此限）。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "ComfyUIDrawPlugin")
    fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "cmd_nsfw")
    banned = ("taskhub", "nsfw-check", "nsfw_check", "open_nsfw", "gallery.nsfw", "p(nsfw)")
    offenders = []
    # 豁免：docstring（不是对用户说的）与 self._cfg(...) 的配置 key（代码内部读配置用）
    doc_node = fn.body[0].value if (fn.body and isinstance(fn.body[0], ast.Expr)
                                    and isinstance(fn.body[0].value, ast.Constant)) else None
    cfg_args = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_cfg":
            for arg in node.args:
                cfg_args.update(id(x) for x in ast.walk(arg))
    for node in ast.walk(fn):
        if (node is doc_node or id(node) in cfg_args
                or not isinstance(node, ast.Constant) or not isinstance(node.value, str)):
            continue
        low = node.value.lower()
        for b in banned:
            if b in low:
                offenders.append((node.lineno, b, node.value[:50]))
    check("用户文案无内部技术名词", not offenders, offenders)


def test_label_map():
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    for label, zh in (
        ("FEMALE_BREAST_EXPOSED", "女性胸部暴露"),
        ("NIPPLE_EXPOSED", "露点"),
        ("FEMALE_GENITALIA_EXPOSED", "女性私处暴露"),
        ("VAGINA_EXPOSED", "私处暴露"),
        ("BUTTOCKS_EXPOSED", "臀部暴露"),
    ):
        check(f"部位映射 {label}", f'"{label}": "{zh}"' in src)


if __name__ == "__main__":
    test_schema()
    test_remote_timeout_and_queue_header()
    test_reply_wording()
    test_no_internal_terms_in_user_text()
    test_label_map()
    print("RESULT: nsfw_cmd_reply OK")
