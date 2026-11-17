# -*- coding: utf-8 -*-
"""search / count_search 口径一致性守卫（v7.8.10）。

背景：两处曾逐行复制十余个过滤条件，只共用 type_filter_sql。
v6.0.0 只给其中一处补了 is_global=1 →「/图库 取图 <编号>」取错图。
现在统一走 build_search_where，本测试从两个角度兜底：

1. 参考实现：把重构前的条件拼装原样留在这里，逐字比对生成的 SQL 片段
   与参数列表（含参数顺序），确保抽取过程没改掉任何语义；
2. 行为对拍：真 SQLite 里造覆盖各维度的数据，逐组合断言
   count_search(...) == len(search(...))，根治计数与列表漂移。
"""
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import image_store  # noqa: E402


def _legacy_where(
    *, keyword="", type=None, starred_only=False, trash=False, owner="",
    session=None, user_filter="", nsfw="", tag="", platform="",
):
    """v7.8.9 及之前两处共用的那段逐行复制逻辑，原样保留用于对拍。"""
    sql = ""
    args: list = []
    if trash:
        sql += " AND deleted=?"
        args.append(1)
    else:
        sql += " AND deleted=0"
    sql += " AND status=0"
    if owner:
        sql += " AND (is_public=1 OR is_global=1 OR user_id=?)"
        args.append(owner)
    if session:
        sql += " AND (session_id=? OR is_public=1 OR is_global=1)"
        args.append(session)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        sql += (
            " AND (prompt LIKE ? OR prompt_raw LIKE ? OR workflow LIKE ?"
            " OR sha256 IN (SELECT sha256 FROM image_tags WHERE tag LIKE ?))"
        )
        args += [kw, kw, kw, kw]
    if tag and tag.strip():
        sql += " AND EXISTS (SELECT 1 FROM image_tags t WHERE t.sha256=images.sha256 AND t.tag=?)"
        args.append(tag.strip())
    _tfsql, _tfargs = image_store.type_filter_sql(type)
    sql += _tfsql
    args += _tfargs
    if starred_only:
        sql += " AND starred=1"
    if user_filter and user_filter.strip():
        uf = f"%{user_filter.strip()}%"
        sql += " AND (user_id LIKE ? OR user_name LIKE ?)"
        args += [uf, uf]
    nsfw_f = str(nsfw or "").strip()
    if nsfw_f == "0":
        sql += " AND nsfw=0"
    elif nsfw_f == "1":
        sql += " AND nsfw=1"
    if platform and platform.strip():
        sql += " AND platform=?"
        args.append(platform.strip())
    return sql, args


# 覆盖每个过滤维度 + 关键字空白 + 全条件组合
CASES = [
    {},
    {"owner": "u1"},
    {"owner": "u1", "session": "s1"},
    {"keyword": "猫"},
    {"keyword": "  猫  "},
    {"type": "gen"},
    {"type": "img2img"},
    {"type": "ref"},
    {"type": "user"},
    {"starred_only": True},
    {"trash": True},
    {"nsfw": "0"},
    {"nsfw": "1"},
    {"tag": "可爱"},
    {"platform": "nai"},
    {"user_filter": "u"},
    {
        "owner": "u1", "session": "s1", "keyword": "猫", "type": "gen",
        "tag": "可爱", "starred_only": True, "nsfw": "0",
        "platform": "comfyui", "user_filter": "u",
    },
]

# sha, source, is_img2img, starred, deleted, status, is_public, is_global,
# user_id, user_name, session_id, nsfw, platform, prompt
ROWS = [
    ("a", "gen", 0, 0, 0, 0, 1, 0, "u1", "小明", "s1", 0, "comfyui", "一只白猫"),
    ("b", "gen", 1, 0, 0, 0, 1, 0, "u1", "小明", "s1", 0, "comfyui", "黑猫图生图"),
    ("c", "user", 0, 1, 0, 0, 0, 0, "u1", "小明", "s1", 0, "comfyui", "参考猫"),
    ("d", "gen", 0, 0, 0, 0, 0, 0, "u2", "小红", "s2", 0, "nai", "猫 nai"),
    ("e", "gen", 0, 0, 0, 0, 0, 1, "u2", "小红", "s2", 1, "comfyui", "全局猫"),
    ("f", "gen", 0, 0, 1, 0, 1, 0, "u1", "小明", "s1", 0, "comfyui", "回收站猫"),
    ("g", "gen", 0, 0, 0, 1, 1, 0, "u1", "小明", "s1", 0, "comfyui", "失败猫"),
]

COLS = (
    "sha256, ext, month, source, is_img2img, starred, deleted, status, "
    "is_public, is_global, user_id, user_name, session_id, nsfw, platform, "
    "prompt, created_at"
)


def test_search_count_parity():
    tmp = tempfile.mkdtemp(prefix="anima_search_parity_")
    try:
        store = image_store.ImageStore(Path(tmp), cfg={"enabled": True})
        conn = store._conn_get()
        conn.executemany(
            f"INSERT OR REPLACE INTO images ({COLS}) VALUES ({','.join(['?'] * len(COLS.split(', ')))})",
            [
                (
                    c * 64, "png", "2026-11", src, i2i, star, dele, stat,
                    pub, glob, uid, uname, sid, nsfw, plat, prompt,
                    1000.0 + i,
                )
                for i, (c, src, i2i, star, dele, stat, pub, glob,
                        uid, uname, sid, nsfw, plat, prompt) in enumerate(ROWS)
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO image_tags (sha256, tag) VALUES (?,?)",
            [("a" * 64, "可爱"), ("c" * 64, "可爱")],
        )
        conn.commit()

        for kw in CASES:
            # 1) SQL 片段与参数逐字对齐重构前的实现
            got = image_store.build_search_where(**kw)
            want = _legacy_where(**kw)
            assert got == want, f"WHERE 漂移: {kw}\n got={got}\nwant={want}"

            # 2) 真库行为对拍：计数与列表必须命中同一批图
            n = store.count_search(**kw)
            rows = store.search(**kw, limit=1000)
            assert n == len(rows), f"count={n} 与 search={len(rows)} 不一致: {kw}"
            print(f"PASS {kw or '{}'}: {n} 条")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_search_count_parity()
    print(f"search/count parity OK: {len(CASES)} 组条件全部一致")
