# -*- coding: utf-8 -*-
"""图库批量操作自检（v7.8.8）。

用临时目录建真 ImageStore，验证 batch_op 的 delete/restore/purge：
- 批量删除移入回收站；收藏图拒绝删除（计入失败）
- 批量恢复
- 批量彻底删除（删记录）
- 去重、空 shas、未知 action
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import image_store  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def add_row(store, sha, starred=0, deleted=0):
    conn = store._conn_get()
    conn.execute(
        "INSERT OR REPLACE INTO images (sha256, ext, month, source, starred, deleted, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (sha, "png", "2026-11", "gen", starred, deleted, 1000.0),
    )
    conn.commit()


def get_row(store, sha):
    return store._conn_get().execute("SELECT * FROM images WHERE sha256=?", (sha,)).fetchone()


def main():
    tmp = tempfile.mkdtemp(prefix="anima_batch_")
    try:
        store = image_store.ImageStore(Path(tmp), cfg={"enabled": True})
        # 造 4 张：a/b 普通，c 收藏，d 普通
        for sha, st in [("a" * 64, 0), ("b" * 64, 0), ("c" * 64, 1), ("d" * 64, 0)]:
            add_row(store, sha, starred=st)

        # 1. 批量删除：a/b/d 成功，c 收藏拒绝
        res = store.batch_op("delete", ["a" * 64, "b" * 64, "c" * 64, "d" * 64])
        check("批量删除成功3失败1", res["ok"] == 3 and res["failed"] == 1, str(res))
        check("失败包含收藏图c", "c" * 64 in res["failed_shas"], str(res["failed_shas"]))
        check("a已入回收站", get_row(store, "a" * 64)["deleted"] == 1)
        check("c收藏图未删除", get_row(store, "c" * 64)["deleted"] == 0)

        # 2. 批量恢复 a/b
        res2 = store.batch_op("restore", ["a" * 64, "b" * 64])
        check("批量恢复2张", res2["ok"] == 2 and res2["failed"] == 0, str(res2))
        check("a已恢复", get_row(store, "a" * 64)["deleted"] == 0)

        # 3. 去重 + 空值：同一 sha 重复只算一次
        res3 = store.batch_op("delete", ["b" * 64, "b" * 64, "", None])
        check("去重后只处理1张", res3["ok"] == 1 and res3["total"] == 1, str(res3))

        # 4. 批量彻底删除：d（已在回收站）
        res4 = store.batch_op("purge", ["d" * 64])
        check("批量purge成功", res4["ok"] == 1 and get_row(store, "d" * 64) is None, str(res4))

        # 5. 未知 action / 空 shas
        r5 = store.batch_op("xxx", ["a" * 64])
        check("未知action返回error", "error" in r5 and r5["ok"] == 0, str(r5))
        r6 = store.batch_op("delete", [])
        check("空shas返回0", r6["total"] == 0 and r6["ok"] == 0, str(r6))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [n for n, ok, _ in results if not ok]
    if bad:
        print("FAILED:", ", ".join(bad))
        sys.exit(1)
    print("ALL PASS", len(results))


if __name__ == "__main__":
    main()
