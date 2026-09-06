#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nai-codex 检索脚本(兼容 DSH / AstrBot 等任意可执行 Python 的环境)

用法:
  python search.py "关键词"                    # 全册检索
  python search.py "关键词" sfw                # 只查常规册
  python search.py "关键词" nsfw-a             # 只查色色册·上
  python search.py "关键词" nsfw-b             # 只查色色册·下
  python search.py "关键词" all --full         # 打印完整 tag 串(不截断)
  python search.py "关键词" all --json         # JSON 输出,便于 Agent 解析
  python search.py "关键词" all --limit 10     # 限制输出条数(默认 20)

匹配规则:条目名优先匹配,其次匹配 tag 正文;不区分大小写;
多个词用空格分隔时按「全部包含」匹配(AND)。
"""
import os
import sys
import json

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

FILES = {
    "all": ["codex-sfw.tsv", "codex-nsfw-a.tsv", "codex-nsfw-b.tsv"],
    "sfw": ["codex-sfw.tsv"],
    "nsfw-a": ["codex-nsfw-a.tsv"],
    "a": ["codex-nsfw-a.tsv"],
    "nsfw-b": ["codex-nsfw-b.tsv"],
    "b": ["codex-nsfw-b.tsv"],
}


def search(keyword, scope="all", full=False, limit=20):
    """检索法典，返回命中条目列表（list[dict]）。

    每条 dict 含：file / chapter / item / tags。
    tag 串中的 TSV 换行转义（\\n）已还原为真实换行。
    scope: sfw / nsfw-a / nsfw-b / all（其它值回落 all）。
    供插件 main.py 以 importlib 方式加载后直接调用，无需走命令行。
    """
    files = FILES.get(scope, FILES["all"])
    words = [w.lower() for w in keyword.split()]

    results = []
    for fn in files:
        path = os.path.join(DATA_DIR, fn)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3:
                    continue
                chapter, item, tags = parts
                tags = tags.replace("\\n", "\n")  # 还原 TSV 中的换行转义
                hay_item = item.lower()
                hay_tags = tags.lower()
                if all(w in hay_item or w in hay_tags for w in words):
                    results.append({
                        "file": fn,
                        "chapter": chapter,
                        "item": item,
                        "tags": tags,
                    })
                    if len(results) >= limit:
                        break
    return results


def format_results(results, keyword, full=False):
    """把 search() 的命中列表格式化为可读文本（与人类直接跑脚本的输出一致）。"""
    if not results:
        return f"未命中: {keyword!r}(试试其他关键词,如角色原名/英文 tag)"
    lines = [f"命中 {len(results)} 条(关键词: {keyword!r}):"]
    for r in results:
        tags = r["tags"] if full else r["tags"][:300]
        if not full and len(r["tags"]) > 300:
            tags += "...(加 full=true 查看完整)"
        lines.append(f"\n[{r['chapter']}] {r['item']}  ({r['file']})")
        lines.append(f"  {tags}")
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    keyword = args[0]
    scope = "all"
    full = False
    as_json = False
    limit = 20

    rest = args[1:]
    if rest and not rest[0].startswith("--"):
        scope = rest[0]
        rest = rest[1:]

    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--full":
            full = True
        elif a == "--json":
            as_json = True
        elif a == "--limit" and i + 1 < len(rest):
            limit = int(rest[i + 1])
            i += 1
        i += 1

    results = search(keyword, scope=scope, full=full, limit=limit)
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    print(format_results(results, keyword, full=full))
    return 1 if not results else 0


if __name__ == "__main__":
    sys.exit(main())
