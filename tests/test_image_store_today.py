"""图库「今日出图数」口径测试（v7.4.1）。

卡片右上角「今日已出图 N 张」= 当天**全部用户**成功发出去的成品图，
口径与「绘图统计」一致：source='gen' 且 status=0 且未删除。
覆盖：空库 / 参考图不计入 / 内容寻址去重不重复计数 / 跨天不计入。

跑法：python tests/test_image_store_today.py
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from image_store import SRC_GEN, SRC_REF, ImageStore  # noqa: E402


def _png(dirpath: Path, name: str, color) -> str:
    p = dirpath / name
    Image.new("RGB", (64, 64), color).save(p, "PNG")
    return str(p)


def test_today_count():
    tmp = Path(tempfile.mkdtemp())
    st = ImageStore(tmp, cfg={"enabled": True})
    assert st.count_today_generated() == 0

    st.archive_image(_png(tmp, "a.png", (200, 30, 60)), source=SRC_GEN, prompt="p1", w=64, h=64)
    st.archive_image(_png(tmp, "b.png", (30, 200, 90)), source=SRC_GEN, prompt="p2", w=64, h=64)
    # 参考图与用户收藏图不计入出图数
    st.archive_image(_png(tmp, "c.png", (30, 90, 200)), source=SRC_REF, prompt="ref", w=64, h=64)
    assert st.count_today_generated() == 2, st.count_today_generated()

    # 同一张图重复生成 → 内容寻址去重，不重复计数（与绘图统计同口径）
    st.archive_image(_png(tmp, "a2.png", (200, 30, 60)), source=SRC_GEN, prompt="p1")
    assert st.count_today_generated() == 2, st.count_today_generated()

    # 把一张成品图挪到前天 → 今日数减 1（跨天不计入）
    conn = st._conn_get()
    conn.execute(
        "UPDATE images SET created_at=? "
        "WHERE rowid=(SELECT rowid FROM images WHERE source='gen' ORDER BY rowid LIMIT 1)",
        (time.time() - 86400 * 2,),
    )
    conn.commit()
    assert st.count_today_generated() == 1, st.count_today_generated()
    print("== 今日出图数（全部用户 / 参考图不计 / 去重 / 跨天） OK")


def test_keep_src():
    """v7.8.2：keep_src=True 时归档只复制，不搬走调用方的源文件。

    背景：萌萌今日老婆把 data/refs/ 里的参考图传进来做图生图，旧实现
    archive_image 用 os.replace「移动转正」，生成完它的参考图就全没了。
    """
    tmp = Path(tempfile.mkdtemp())
    st = ImageStore(tmp, cfg={"enabled": True})
    keep = _png(tmp, "outer_ref.png", (10, 120, 200))
    out = st.archive_image(keep, source=SRC_REF, prompt="ref", keep_src=True)
    assert out and Path(keep).is_file(), "keep_src=True 不应移动源文件"
    # 默认仍是移动转正（萌绘自己 temp/ 里的图）
    own = _png(tmp, "own_temp.png", (10, 200, 120))
    out2 = st.archive_image(own, source=SRC_GEN, prompt="p")
    assert out2 and not Path(own).is_file(), "默认应移动转正"
    print("== 参考图归档 keep_src（只复制、不搬走源文件） OK")


if __name__ == "__main__":
    test_today_count()
    test_keep_src()
    print("今日出图数测试通过")
