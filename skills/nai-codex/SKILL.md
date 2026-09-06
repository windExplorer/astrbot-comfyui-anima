---
name: nai-codex
description: 当用户通过 NAI（NovelAI）类第三方平台生图、并希望微调出图风格时使用本技能。它教你在调用 comfyui_draw（带 platform=nai / platform=NAI 或会话已切到 NAI）时，如何正确、自主地决定 cfg（引导系数）、steps（步数）、sampler（采样器）、noise_schedule（噪声调度）等平台专属参数——包括它们的含义、取值范围、对画风/速度的影响，以及何时该传、何时该留给插件用默认值。负向提示词不需要你拼：不传 negative_prompt 时插件会自动套用管理员启用的负向模板。适用于你不确定 NAI 这些参数怎么填、或想主动让画面更精致/更快/更贴合提示词时。
---

# NAI（NovelAI）生图参数手册（astrbot_plugin_comfyui_anima）

当你通过 `comfyui_draw` 走 **NAI 类平台**出图时（即 `platform` 参数传了 `nai` / `NAI` / 该平台显示名，或用户本会话已切到 NAI 平台），除了 `prompt` 之外，还可以用 4 个**可选**参数微调画面：

- `cfg`（引导系数，NAI 官方字段名 `scale`）
- `steps`（采样步数）
- `sampler`（采样器）
- `noise_schedule`（噪声调度）

这 4 个参数**只作用于 nai / openai 类第三方平台**，对 ComfyUI 生图完全无效（会被忽略）。**不传或传 0/空 = 用该平台在配置里设的默认值**，绝大多数情况下默认值就已经很好，不要无脑覆盖。

## 核心原则：默认别传，想调风格才传

插件的平台配置里已经为 NAI 设好了合理的默认值（cfg≈6、steps≈28、sampler≈k_dpmpp_2m_sde、noise_schedule≈karras）。所以：

- ✅ 用户只说"画个 XX" → **不要传这 4 个参数**，让插件用默认。
- ✅ 用户明确想要特定质感（"更精致一点""再细腻些""快一点别太慢""用 euler 那种风格"）→ 才针对性传 1~2 个。
- ❌ 不要一次性把 4 个全填上，尤其不要凭记忆乱填 sampler / noise_schedule 的具体字符串——填错会直接报错。

## 各参数含义与取值建议

### cfg（引导系数 / scale）
控制画面**有多严格地遵循提示词**。

- 值越大 → 越贴合提示词、对比与饱和度越高，但过大易过饱和、毁细节、出怪形。
- 值越小 → 越自由、更有"艺术感"，但容易跑题、不守提示词。
- NAI 常用区间 **4 ~ 12**，默认约 **6**。
- 经验：
  - 想要"严格还原角色/构图" → 偏上限（7~9）。
  - 想要"梦幻/随性/艺术感" → 偏下限（4~5）。
  - 用户说"太艳/太假/糊了" → 适度调低；"不够像/跑题" → 适度调高。

### steps（采样步数）
去噪迭代次数，决定细节打磨程度与耗时（步数越多越慢）。

- NAI 常用 **20 ~ 32**，默认约 **28**。
- 步数过低 → 细节缺失、结构松散；过高 → 收益递减且明显变慢，超过 ~40 几乎无提升还可能劣化。
- 用户要"快一点" → 可降到 20~24；要"更精细" → 28~32，一般不必超过 32。

### sampler（采样器）
决定去噪的"路径算法"，**对画风/质感影响最直观**，不同采样器味道差别很大。

- 常用值（NAI 系）：`k_dpmpp_2m_sde`（默认，均衡稳定）、`k_dpmpp_2m`、`k_euler`、`k_euler_a`、`k_dpmpp_sde`、`k_heun`、`k_dpm_2`、`k_lms`、`ddim`。
- 不确定具体字符串时**不要传**——插件会用平台默认，传错字符串会请求失败。
- 仅在用户明确点名某个采样器（"用 euler 那种""换个 sampler"）或你很有把握时才传。

### noise_schedule（噪声调度）
NAI 专属，控制噪声随时间衰减的曲线。

- 常用值：`karras`（默认，最常用）、`native`、`exponential`、`polyexponential`、`ddim_uniform`。
- 默认 `karras` 对绝大多数画面都合适；一般**不需要传**。
- 仅当用户明确要某种调度、或你确认平台支持该值时才传。

## 负向提示词：交给插件，不要自己拼

NAI 的负面词你**不需要**在 `negative_prompt` 里写——不传时，插件会自动套用管理员在平台配置里**勾选启用的负向模板**（如 lowres / bad anatomy / worst quality 等通用负面），效果比手拼更稳。

- 用户**没有**特别要求负面词 → 不传 `negative_prompt`。
- 用户**明确**要追加特定负面（"不要出现文字""别太暗"）→ 才在 `negative_prompt` 里写那几句；平台启用的模板仍会自动合并进去。

## 调用示例

只画一张、用默认参数（最推荐）：

```
comfyui_draw(prompt="1girl, solo, white dress, masterpiece", platform="nai")
```

想让画面更精致、更贴合提示词，只覆盖 cfg 与 steps：

```
comfyui_draw(prompt="1girl, solo, white dress, masterpiece",
             platform="nai", cfg=8, steps=30)
```

用户点名要某个采样器：

```
comfyui_draw(prompt="1girl, cat ears, masterpiece",
             platform="nai", sampler="k_euler_a")
```

## 不要做的事

- 不要把 `cfg` 设得很高（>12）还觉得"更准"——通常只会过饱和毁图。
- 不要对 ComfyUI 工作流出图传这 4 个参数（无效）。
- 不要臆造 sampler / noise_schedule 字符串；不确定就留空用默认。
- 不要在 `negative_prompt` 里重复写平台已启用的通用负面模板词条。
