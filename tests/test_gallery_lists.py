# -*- coding: utf-8 -*-
"""三个图库列表语义守卫（v7.8.11）。

背景：gallery_list / favorites_list 旧版在 SELECT 里各跑两个相关子查询算
lc/fc，但它们既不参与排序、_row_to_dict 也不读；紧随其后的 _share_enrich
已经用 IN (...) GROUP BY 批量算过 like_count/favorite_count。
v7.8.11 删掉这两处冗余子查询（3000 张图 A/B：gallery_list 快约 22%）。

本测试保证：
1. world_list 的热度排序仍依赖 lc/fc，相关子查询不能被顺手删掉；
2. 删掉子查询后，计数仍由 _share_enrich 正确填充（前端字段不变）；
3. 静态守卫：gallery_list / favorites_list 不得再出现逐行计数子查询。

注：曾试过给 images / image_favorites 加复合索引、把 world_list 改成
聚合 LEFT JOIN，EXPLAIN 更漂亮但 A/B 实测分别慢 4.7x / 8.7x / 1.16x，
已全部撤销；不要看到 TEMP B-TREE 就加索引。
"""
import inspect
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import image_store  # noqa: E402

COLS = (
    "sha256, ext, month, source, starred, deleted, status, is_public, "
    "user_id, user_name, created_at"
)

# sha, is_public, user_id, created_at
IMAGES = [
    ("a", 1, "u1", 3000.0),
    ("b", 1, "u1", 2000.0),
    ("c", 1, "u2", 1000.0),
    ("d", 0, "u1", 4000.0),  # 私有：不进 world
    ("e", 1, "u1", 5000.0),  # 回收站：不进任何列表
]

# (image_sha, user_id)
LIKES = [("a", "x1"), ("a", "x2"), ("b", "x1"), ("c", "x1"), ("c", "x2"), ("c", "x3")]
FAVS = [("a", "u1"), ("c", "u1"), ("c", "u2")]


def test_gallery_lists():
    tmp = tempfile.mkdtemp(prefix="anima_lists_")
    try:
        st = image_store.ImageStore(Path(tmp), cfg={"enabled": True})
        conn = st._conn_get()
        conn.executemany(
            f"INSERT INTO images ({COLS}) VALUES ({','.join(['?'] * len(COLS.split(', ')))})",
            [
                (c * 64, "png", "2026-11", "gen", 0, 1 if c == "e" else 0, 0,
                 pub, uid, f"user-{uid}", ts)
                for c, pub, uid, ts in IMAGES
            ],
        )
        conn.executemany(
            "INSERT INTO image_likes (sha256, user_id, created_at) VALUES (?,?,?)",
            [(s * 64, u, 1.0) for s, u in LIKES],
        )
        conn.executemany(
            "INSERT INTO image_favorites (sha256, user_id, created_at) VALUES (?,?,?)",
            [(s * 64, u, 2.0 + i) for i, (s, u) in enumerate(FAVS)],
        )
        conn.commit()

        # 1. world：热度 = 点赞+收藏；c(5) > a(3) > b(1)，私有 d 与回收站 e 不出现
        world = st.world_list("u1", limit=40)
        assert [d["sha16"] for d in world["images"][:3]] == ["c" * 16, "a" * 16, "b" * 16], world
        assert world["total"] == 3, world
        a = next(d for d in world["images"] if d["sha16"] == "a" * 16)
        assert (a["like_count"], a["favorite_count"]) == (2, 1), a

        # 2. gallery：本人未删的 a/b/d；私有图仍在自己图库里；计数由 _share_enrich 填充
        gal = st.gallery_list("u1", limit=40)
        assert {d["sha16"] for d in gal["images"]} == {"a" * 16, "b" * 16, "d" * 16}, gal
        assert gal["total"] == 3, gal
        b = next(d for d in gal["images"] if d["sha16"] == "b" * 16)
        assert (b["like_count"], b["favorite_count"]) == (1, 0), b

        # 3. favorites：u1 收藏了 a（自己）与 c（他人公开）；owner_is_me 区分归属
        fav = st.favorites_list("u1", limit=40)
        assert {d["sha16"] for d in fav["images"]} == {"a" * 16, "c" * 16}, fav
        assert fav["total"] == 2, fav
        c = next(d for d in fav["images"] if d["sha16"] == "c" * 16)
        assert c["owner_is_me"] is False and c["like_count"] == 3, c

        # 4. 静态守卫：冗余子查询只在 world（热度排序需要）里保留
        src_w = inspect.getsource(image_store.ImageStore.world_list)
        assert "SELECT COUNT(*) FROM image_likes" in src_w
        for name in ("gallery_list", "favorites_list"):
            src = inspect.getsource(getattr(image_store.ImageStore, name))
            assert "SELECT COUNT(*) FROM image_likes" not in src, f"{name} 又加回了逐行计数子查询"

        print("gallery lists OK: 热度排序 / 计数填充 / 静态守卫均通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_gallery_lists()
