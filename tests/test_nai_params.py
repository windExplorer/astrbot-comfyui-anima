"""NAI 生图参数归一的自测（不需网络 / AstrBot 运行环境）。

覆盖 v6.2.1 新增的三件事：
  1) 采样器人类写法 → NAI 内部名（含采样器名里带的 Karras 调度词）；
  2) 噪声调度写法归一（大小写 / 装饰词）；
  3) cfg 自适应：0~1 → CFG Rescale（缩放引导值），>1 → 引导强度 scale。

运行：
    uv run --no-project --with aiohttp python tests/test_nai_params.py
（nai_client 顶层 import aiohttp，故需 --with aiohttp）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import nai_client  # noqa: E402


def test_sampler_aliases():
    print("== 1. 采样器归一 ==")
    cases = [
        # 用户/站点写法 → NAI 内部名
        ("dpm++2msde", "k_dpmpp_2m_sde", ""),
        ("DPM++ 2M SDE", "k_dpmpp_2m_sde", ""),
        ("DPM++ 2M SDE Karras", "k_dpmpp_2m_sde", "karras"),
        ("dpm++ 2m sde karras", "k_dpmpp_2m_sde", "karras"),
        ("DPM++ 2M", "k_dpmpp_2m", ""),
        ("DPM++ 2M Karras", "k_dpmpp_2m", "karras"),
        ("DPM++ SDE", "k_dpmpp_sde", ""),
        ("DPM++ 2S a", "k_dpmpp_2s_ancestral", ""),
        ("DPM++ 3M SDE", "k_dpmpp_3m_sde", ""),
        ("Euler a", "k_euler_ancestral", ""),
        ("Euler", "k_euler", ""),
        ("DPM2", "k_dpm_2", ""),
        ("DPM fast", "k_dpm_fast", ""),
        # NAI 内部名幂等
        ("k_dpmpp_2m_sde", "k_dpmpp_2m_sde", ""),
        ("k_euler_ancestral", "k_euler_ancestral", ""),
        # 认不出：第一项空（调用方兜底），但调度词仍可识别
        ("UniPC", "", ""),
        ("PLMS exponential", "", "exponential"),
        ("", "", ""),
    ]
    for raw, want_name, want_noise in cases:
        got = nai_client.normalize_nai_sampler(raw)
        assert got == (want_name, want_noise), f"{raw!r} → {got}（期望 {(want_name, want_noise)}）"
        print(f"   ✓ {raw!r:<24} → {got}")
    # v4/v5 白名单里的名字必须都是内部名（防别名表写错）
    for name in nai_client._NAI_V4_SAMPLERS:
        assert nai_client.normalize_nai_sampler(name)[0] == name, f"白名单项未自映射: {name}"
    print("   ✓ v4/v5 白名单 9 项均自映射")


def test_noise_aliases():
    print("== 2. 噪声调度归一 ==")
    cases = [
        ("karras", "karras"),
        ("Karras", "karras"),
        ("KARRAS", "karras"),
        ("karras schedule", "karras"),
        ("native", "native"),
        ("exponential", "exponential"),
        ("polyexponential", "polyexponential"),
        ("PolyExponential", "polyexponential"),
        ("", ""),
        ("bogus", ""),
    ]
    for raw, want in cases:
        got = nai_client.normalize_nai_noise(raw)
        assert got == want, f"{raw!r} → {got!r}（期望 {want!r}）"
        print(f"   ✓ {raw!r:<20} → {got!r}")


def test_cfg_split():
    print("== 3. cfg 自适应拆分（引导强度 / CFG Rescale） ==")
    # 平台默认刻意取「引导 6 / 重缩放 0.1」这两个不同的值：
    # 这样任一分支走错（拿默认值顶替或两个字段串位）都会立刻被断言抓到。
    defaults = {"scale": 6, "cfg_rescale": 0.1}
    cases = [
        # (传入 cfg, 期望 (引导强度, CFG Rescale), 说明)
        (None, (6.0, 0.1), "未传 → 两项都回落平台默认"),
        (6, (6.0, 0.1), "6 → 引导强度，重缩放保持默认"),
        (8.5, (8.5, 0.1), "8.5 → 引导强度"),
        (0.3, (6.0, 0.3), "0.3 → 重缩放，引导强度保持默认"),
        ("0.7", (6.0, 0.7), "字符串 0.7 → 重缩放"),
        (99, (20.0, 0.1), "99 → 引导强度按官方上限 20 clamp"),
        (0.0, (6.0, 0.0), "0 → 重缩放（最保守取值，不误当引导强度）"),
        ("6", (6.0, 0.1), "字符串 6 → 引导强度"),
        ("abc", (6.0, 0.1), "非法值 → 全回落默认"),
        ("", (6.0, 0.1), "空串 → 全回落默认"),
    ]
    for raw, want, note in cases:
        got = nai_client.resolve_nai_cfg(raw, defaults)
        assert got == want, f"cfg={raw!r} → {got}（期望 {want}；{note}）"
        print(f"   ✓ cfg={str(raw):<6} → {str(got):<14} {note}")
    # defaults 里没值时按官方兜底：v4/v5 重缩放 0，旧模型 0.3
    assert nai_client.resolve_nai_cfg(None, {}, "nai-diffusion-4-5-full") == (6.0, 0.0)
    assert nai_client.resolve_nai_cfg(None, {}, "nai-diffusion-5-full") == (6.0, 0.0)
    assert nai_client.resolve_nai_cfg(None, {}, "nai-diffusion-3") == (6.0, 0.3)
    print("   ✓ v4/v5 重缩放兜底 0、旧模型兜底 0.3（共 13 项）")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    test_sampler_aliases()
    test_noise_aliases()
    test_cfg_split()
    print("\n全部通过：NAI 采样器 / 噪声调度 / cfg 归一逻辑符合预期。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
