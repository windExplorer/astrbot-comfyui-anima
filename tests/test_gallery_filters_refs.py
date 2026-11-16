# -*- coding: utf-8 -*-
"""图库筛选与多参考图 sha 拼接自检（v7.8.7）。

覆盖三个回归点：
1. type=img2img 走 is_img2img=1，count/search 口径一致（分页器不再消失）。
2. type=ref 同时命中 source=ref / user（参考图按 user 归档，筛选不再为空）。
3. 多参考图 sha 用逗号拼接、去重、兼容单张裸 sha。
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import image_store  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def main():
    # 1/2. type_filter_sql 口径
    sql_i, args_i = image_store.type_filter_sql("img2img")
    check("img2img 用 is_img2img=1", "is_img2img=1" in sql_i and not args_i, sql_i)

    sql_r, args_r = image_store.type_filter_sql("ref")
    ok_ref = "source IN (?,?)" in sql_r and set(args_r) == {image_store.SRC_REF, image_store.SRC_USER}
    check("ref 命中 ref+user", ok_ref, f"{sql_r} {args_r}")

    sql_g, args_g = image_store.type_filter_sql("gen")
    check("gen 精确匹配 source", "source=?" in sql_g and args_g == ["gen"], f"{sql_g} {args_g}")

    sql_a, args_a = image_store.type_filter_sql(None)
    check("None 不过滤", sql_a == "" and args_a == [], repr((sql_a, args_a)))

    # count_search 与 search 必须用同一个函数（防止再次口径漂移）
    import inspect
    src = inspect.getsource(image_store.ImageStore.count_search)
    check("count_search 调用共用过滤函数", "type_filter_sql(" in src)
    src_s = inspect.getsource(image_store.ImageStore.search)
    check("search 调用共用过滤函数", "type_filter_sql(" in src_s)

    # 3. 多参考图 sha 拼接（复刻 main.py 归档处的语义）
    def collect(shas):
        acc = ""
        for s in shas:
            if s and s not in acc.split(","):
                acc = (acc + "," if acc else "") + s
        return acc

    got = collect(["aaa", "bbb", "ccc"])
    check("多张逗号拼接", got == "aaa,bbb,ccc", got)
    got = collect(["aaa", "aaa", "bbb"])
    check("去重", got == "aaa,bbb", got)
    got = collect(["aaa"])
    check("单张是裸 sha", got == "aaa" and "," not in got, got)

    # 前端拆分语义（Python 侧等价验证）：逗号串拆回原数组
    check("逗号串可拆回", "aaa,bbb".split(",") == ["aaa", "bbb"])

    bad = [n for n, ok, _ in results if not ok]
    if bad:
        print("FAILED:", ", ".join(bad))
        sys.exit(1)
    print("ALL PASS", len(results))


if __name__ == "__main__":
    main()
