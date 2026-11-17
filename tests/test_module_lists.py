# -*- coding: utf-8 -*-
"""模块清单三方一致性守卫（v7.8.9）。

顶层 .py 模块必须同时出现在两处清单里：
1. build_zip.ps1 的 $includeList —— 漏了就打成残包，用户侧 import 失败
   （历史事故：v7.6.0 的 prompt_guard.py 没进包）；
2. main.py 初始化时的 importlib.reload 列表 —— 漏了热更新后跑旧代码
   （历史事故：v7.7.67 的 prompt_guard.py、v7.8.9 的 workflow_parser.py）。

本测试从磁盘真实文件反推，任何一侧漏项/残留都直接 fail，
把「靠注释记着」变成跑一遍就能发现的机制。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# main.py 是重载的执行者，不重载自己；但它必须在打包清单里
RELOAD_EXEMPT = {"main.py"}


def _disk_modules() -> set[str]:
    """仓库根下所有非临时顶层 .py（与 build_zip.ps1 兜底校验口径一致）。"""
    return {p.name for p in ROOT.glob("*.py") if not p.name.startswith("_")}


def _reload_modules() -> set[str]:
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    m = re.search(r"for _dep_name in \((.*?)\)\s*:", src, re.S)
    assert m, "main.py 里找不到依赖模块重载列表（for _dep_name in (...)）"
    # 与磁盘 / 打包清单统一口径：都带 .py 后缀
    return {f"{name}.py" for name in re.findall(r"[\"']([A-Za-z_][A-Za-z_0-9]*)[\"']", m.group(1))}


def _packed_modules() -> set[str]:
    # build_zip.ps1 可能带 BOM / 中文注释，只按字节解出 ASCII 部分即可
    src = (ROOT / "build_zip.ps1").read_bytes().decode("utf-8-sig", errors="replace")
    m = re.search(r"\$includeList\s*=\s*@\((.*?)\)", src, re.S)
    assert m, "build_zip.ps1 里找不到 $includeList"
    return set(re.findall(r"[\"']([A-Za-z_0-9.]+\.py)[\"']", m.group(1)))


def test_reload_list_covers_all_modules():
    missing = sorted(_disk_modules() - RELOAD_EXEMPT - _reload_modules())
    assert not missing, f"热重载列表漏了模块（热更新后跑旧代码）: {missing}"


def test_pack_list_covers_all_modules():
    missing = sorted(_disk_modules() - _packed_modules())
    assert not missing, f"打包清单漏了模块（zip 残包）: {missing}"


def test_no_stale_entries():
    disk = _disk_modules()
    stale_reload = sorted(_reload_modules() - disk)
    stale_pack = sorted(_packed_modules() - disk)
    assert not stale_reload, f"热重载列表有磁盘上已不存在的模块: {stale_reload}"
    assert not stale_pack, f"打包清单有磁盘上已不存在的模块: {stale_pack}"


if __name__ == "__main__":
    test_reload_list_covers_all_modules()
    test_pack_list_covers_all_modules()
    test_no_stale_entries()
    print("module lists OK: 磁盘 / 热重载 / 打包清单三方一致")
