"""图库「输入尺寸 / 放大倍率」取值辅助测试（v7.4.4）。

这三个辅助（`_latent_size_of` / `_images_size_of` / `_upscale_measured_note`）是纯函数，
但住在 main.py 里（依赖 astrbot 运行时，本地装不了）。这里用 ast 把它们的源码摘出来
单独执行，从而在没有 astrbot 的环境下也能测到真实逻辑。

跑法：python tests/test_size_helpers.py
"""
import ast
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

import workflow_builder  # noqa: E402


class _Log:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _load_helpers() -> dict:
    # main.py 带 UTF-8 BOM，用 utf-8-sig 读掉，否则 ast.parse 会报非法字符
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    want = {"_latent_size_of", "_images_size_of", "_upscale_measured_note"}
    ns = {"workflow_builder": workflow_builder, "logger": _Log(), "_PILImage": Image}
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in want:
            continue
        seg = ast.get_source_segment(src, node) or ""
        # 去掉 @staticmethod 装饰行（直接在命名空间里 exec def 即可）
        body = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("@"))
        exec(textwrap.dedent(body), ns)  # noqa: S102
    missing = want - set(ns)
    assert not missing, f"没摘到这些方法: {missing}"
    return ns


H = _load_helpers()


def test_latent_size_of():
    f = H["_latent_size_of"]
    # 1) 标准 EmptyLatentImage
    assert f({"5": {"class_type": "EmptyLatentImage",
                    "inputs": {"width": 816, "height": 1216, "batch_size": 1}}}) == (816, 1216)
    # 2) 其它 latent 节点类名
    assert f({"1": {"class_type": "EmptySD3LatentImage",
                    "inputs": {"width": 1024, "height": 1024}}}) == (1024, 1024)
    # 3) 自定义类名兜底（名字里有 latent 且有 width/height）
    assert f({"9": {"class_type": "MyLatentThing",
                    "inputs": {"width": 640, "height": 480}}}) == (640, 480)
    # 4) 字符串数值也要能读
    assert f({"5": {"class_type": "EmptyLatentImage",
                    "inputs": {"width": "832", "height": "1216"}}}) == (832, 1216)
    # 5) 读不到 → (None, None)（UI 不显示假值）
    assert f({}) == (None, None)
    assert f(None) == (None, None)
    assert f({"6": {"class_type": "KSampler", "inputs": {"seed": 1}}}) == (None, None)
    # 6) 宽高为 0 也当读不到
    assert f({"5": {"class_type": "EmptyLatentImage",
                    "inputs": {"width": 0, "height": 0}}}) == (None, None)
    print("== 1. latent 尺寸读取（标准/别名/兜底/字符串/读不到） OK")


def test_images_size_of():
    f = H["_images_size_of"]
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "ref.png"
    Image.new("RGB", (512, 768), (10, 20, 30)).save(p, "PNG")
    assert f([str(p)]) == (512, 768)
    # 第一张读不了 → 顺延下一张；全读不了 → (None, None)
    assert f([str(tmp / "missing.png"), str(p)]) == (512, 768)
    assert f([str(tmp / "missing.png")]) == (None, None)
    assert f([]) == (None, None)
    assert f(None) == (None, None)
    print("== 2. 参考图尺寸读取（顺延/失败留空） OK")


def test_upscale_measured_note():
    f = H["_upscale_measured_note"]
    # 2 倍放大
    assert f(832, 1216, 1664, 2432) == "约 2.00×（按出图/输入尺寸推算）"
    # 没放大（尺寸相同）→ 空
    assert f(832, 1216, 832, 1216) == ""
    # 2% 以内不算放大（容差）
    assert f(832, 1216, 845, 1235) == ""
    # 缺值 → 空
    assert f(None, None, 1024, 1024) == ""
    assert f(832, 1216, None, None) == ""
    assert f(0, 0, 0, 0) == ""
    print("== 3. 放大倍率推算（旧版工作流无解析注记时用） OK")


if __name__ == "__main__":
    test_latent_size_of()
    test_images_size_of()
    test_upscale_measured_note()
    print("尺寸辅助测试通过")
