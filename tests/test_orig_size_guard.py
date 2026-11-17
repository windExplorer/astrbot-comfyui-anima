# -*- coding: utf-8 -*-
"""原图 base64 体积守卫（v7.8.13）。

背景：
1. `_orig_data_url_sync` 原默认上限 24MB，base64 放大 1.33x → 约 32MB 字符串；
2. `gallery_image?raw=1`（内嵌页下载原图）此前**没有任何体积上限**；
3. 两条通道的 `/gallery/trash` 把最多 200 张**原图**逐张转 base64 当缩略图
   （3MB/张 → 600MB 读盘 + 800MB JSON），是本轮最大的内存/带宽风险。

本测试用 AST 抠出纯函数真跑（不依赖 astrbot 运行时），并用静态守卫
保证两条通道的回收站都走缓存缩略图、原图下载有上限。
"""
import ast
import base64
import mimetypes
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBUI_SRC = (ROOT / "webui_api.py").read_text(encoding="utf-8-sig")
STANDALONE_SRC = (ROOT / "standalone_webui.py").read_text(encoding="utf-8-sig")


def _num(node):
    """常量表达式求值（支持 8 * 1024 * 1024 这类乘法）。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _num(node.left) * _num(node.right)
    raise AssertionError(f"无法求值的常量表达式: {ast.dump(node)}")


def _const(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return _num(node.value)
    raise AssertionError(f"找不到常量 {name}")


def _method_src(src: str, cls: str, name: str) -> str:
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == name:
                    return ast.get_source_segment(src, sub) or ""
    raise AssertionError(f"找不到 {cls}.{name}")


def test_orig_size_guard():
    tree = ast.parse(WEBUI_SRC)
    limit = _const(tree, "_ORIG_B64_MAX")
    assert limit == 8 * 1024 * 1024, f"原图 base64 上限应为 8MB，实际 {limit}"

    # 抠出静态方法真跑：小图能编码，超过上限返回 None，显式放宽上限仍可读
    node = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_orig_data_url_sync":
            node = n
            break
    assert node is not None
    node = ast.parse(ast.unparse(node)).body[0]
    node.decorator_list = []
    ns = {"base64": base64, "mimetypes": mimetypes, "Path": Path, "_ORIG_B64_MAX": limit}
    exec(ast.unparse(ast.Module(body=[node], type_ignores=[])), ns)
    fn = ns["_orig_data_url_sync"]

    tmp = Path(tempfile.mkdtemp(prefix="anima_orig_"))
    try:
        small = tmp / "small.png"
        small.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 128)
        out = fn(small)
        assert out and out[0].startswith("data:image/png;base64,"), out

        big = tmp / "big.png"
        with big.open("wb") as fh:
            fh.seek(limit + 1)
            fh.write(b"\0")
        assert fn(big) is None, "超过 8MB 仍被编码成 data URL"
        assert fn(big, limit * 3) is not None, "显式放宽上限后应仍可读"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS 8MB 上限 / 小图正常编码 / 显式放宽可读")


def test_no_fullsize_base64_in_trash():
    # 内嵌通道：gallery_trash 不再逐张 read_bytes
    body = _method_src(WEBUI_SRC, "WebUIApi", "gallery_trash")
    assert "read_bytes()" not in body, "内嵌通道回收站仍在读原图转 base64"
    assert "_thumb_cached" in body, "内嵌通道回收站未改用缓存缩略图"

    # 独立通道（v7.8.14）：前端缩略图走 /img/{sha}/thumb 直链、不读 thumb 字段，
    # 后端连缩略图都不该生成，而不是"改用缓存缩略图"。
    start = STANDALONE_SRC.index('if path == "/gallery/trash"')
    end = STANDALONE_SRC.index('if path == "/gallery/scan_nsfw"')
    seg = STANDALONE_SRC[start:end]
    for token in ("read_bytes()", "base64", "_thumb_cached", 'r["thumb"]'):
        assert token not in seg, f"独立通道回收站仍在生成无用数据: {token}"
    print("PASS 内嵌用缓存缩略图 / 独立完全不生成 thumb")


def test_raw_download_has_limit():
    body = _method_src(WEBUI_SRC, "WebUIApi", "gallery_image")
    assert "_ORIG_B64_MAX" in body, "gallery_image?raw=1 仍无体积上限"
    assert "status_code=413" in body, "超限未返回 413"
    print("PASS 内嵌原图下载有 8MB 上限")


if __name__ == "__main__":
    test_orig_size_guard()
    test_no_fullsize_base64_in_trash()
    test_raw_download_has_limit()
    print("orig size guard OK")
