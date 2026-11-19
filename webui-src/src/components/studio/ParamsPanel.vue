<template>
  <div class="params-panel">
    <!-- 尺寸 -->
    <div class="pp-head"><span class="pp-label">尺寸</span></div>
    <div class="size-row">
      <n-select
        v-model:value="ratioSel"
        size="small"
        :options="ratioOptions"
        placeholder="跟随工作流默认"
        style="flex: 1.2"
        @update:value="applyRatio"
      />
      <n-select
        v-if="ratioSel"
        v-model:value="tierSel"
        size="small"
        :options="tierOptions"
        style="flex: 1"
        @update:value="applyRatio"
      />
    </div>
    <div class="size-row">
      <n-input-number
        :value="params.width"
        size="small"
        :min="64"
        :max="4096"
        :step="64"
        :placeholder="wfSizeLabel.w"
        style="flex: 1"
        @update:value="(v: number | null) => (params.width = v)"
      />
      <span class="size-x">×</span>
      <n-input-number
        :value="params.height"
        size="small"
        :min="64"
        :max="4096"
        :step="64"
        :placeholder="wfSizeLabel.h"
        style="flex: 1"
        @update:value="(v: number | null) => (params.height = v)"
      />
    </div>
    <div v-if="wfSizeLabel.text" class="ctrl-hint">{{ wfSizeLabel.text }}</div>
    <!-- v7.10.8：自动尺寸开关——关闭时提示词里的 portrait/9:16/高清 不会偷偷改尺寸 -->
    <div class="size-mode">
      <span class="ctrl-label">自动尺寸</span>
      <n-tooltip trigger="hover" placement="bottom-start">
        <template #trigger>
          <n-switch
            :value="params.size_from_prompt"
            size="small"
            @update:value="onAutoSizeChange"
          />
        </template>
        开启后，提示词里的比例词（如 9:16 / portrait / landscape）与档位词（高清 / 2K）
        会自动决定出图尺寸；关闭时严格跟随上方所选尺寸或工作流默认。
      </n-tooltip>
      <span class="auto-size-note">{{ params.size_from_prompt ? "提示词可改尺寸" : "跟随所选 / 工作流默认" }}</span>
    </div>
    <div v-if="mode !== 't2i'" class="size-mode">
      <span class="ctrl-label">尺寸来源</span>
      <n-radio-group :value="params.custom_size" size="tiny" @update:value="(v: boolean | null) => (params.custom_size = v)">
        <n-radio-button :value="null">自动</n-radio-button>
        <n-radio-button :value="true">自定义</n-radio-button>
        <n-radio-button :value="false">跟随参考图</n-radio-button>
      </n-radio-group>
    </div>

    <!-- 采样 -->
    <div class="pp-head"><span class="pp-label">采样参数</span></div>
    <div class="ctrl-row">
      <span class="ctrl-label">步数</span>
      <n-slider :value="dispSteps" :min="1" :max="60" :step="1" style="flex: 1" @update:value="(v: number) => (params.steps = v)" />
      <n-input-number :value="dispSteps" size="tiny" :min="1" :max="60" style="width: 76px" @update:value="(v: number | null) => (params.steps = v)" />
    </div>
    <div class="ctrl-row">
      <span class="ctrl-label">CFG</span>
      <n-slider :value="dispCfg" :min="0.5" :max="15" :step="0.5" style="flex: 1" @update:value="(v: number) => (params.cfg = v)" />
      <n-input-number :value="dispCfg" size="tiny" :min="0.5" :max="15" :step="0.5" style="width: 76px" @update:value="(v: number | null) => (params.cfg = v)" />
    </div>
    <div v-if="mode !== 't2i'" class="ctrl-row">
      <span class="ctrl-label">降噪</span>
      <n-slider :value="dispDenoise" :min="0.1" :max="1" :step="0.05" style="flex: 1" @update:value="(v: number) => (params.denoise = v)" />
      <n-input-number :value="dispDenoise" size="tiny" :min="0.1" :max="1" :step="0.05" style="width: 76px" @update:value="(v: number | null) => (params.denoise = v)" />
    </div>
    <div class="ctrl-hint">
      {{ defaultsText }}
    </div>

    <!-- 种子 -->
    <div class="ctrl-row">
      <span class="ctrl-label">种子</span>
      <n-input-number
        :value="params.seed"
        size="small"
        :min="0"
        :max="4294967295"
        placeholder="留空随机"
        style="flex: 1"
        @update:value="(v: number | null) => (params.seed = v === 0 ? null : v)"
      />
      <n-button size="small" title="随机种子" @click="params.seed = null">🎲</n-button>
    </div>

    <!-- 高级 -->
    <n-collapse class="adv">
      <n-collapse-item title="高级（采样器覆盖）" name="adv">
        <div class="ctrl-row adv-row">
          <span class="ctrl-label">采样器</span>
          <n-select
            :value="params.sampler || null"
            size="small"
            filterable
            tag
            clearable
            :options="samplerSelectOptions"
            placeholder="留空用工作流默认"
            style="flex: 1"
            @update:value="(v: string | null) => (params.sampler = v || '')"
          />
        </div>
        <div class="ctrl-row adv-row">
          <span class="ctrl-label">调度器</span>
          <n-select
            :value="params.noise_schedule || null"
            size="small"
            filterable
            tag
            clearable
            :options="schedulerSelectOptions"
            placeholder="留空用工作流默认"
            style="flex: 1"
            @update:value="(v: string | null) => (params.noise_schedule = v || '')"
          />
        </div>
        <div class="ctrl-hint">
          {{ samplerHint }}
        </div>
      </n-collapse-item>
    </n-collapse>

    <!-- v7.9.6：LLM 润色开关移到提示词区工具条（🪄 图标钮，用户提交前主动决定） -->
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { lsSet } from "@/api/storage";
import type { StudioGenerateParams, StudioSizeRatio, StudioSizeTier, StudioWorkflow } from "@/api/studio";

const props = defineProps<{
  /** 直接传父组件的 reactive 参数对象，子组件就地改字段（Vue 3 嵌套修改合法） */
  params: StudioGenerateParams;
  mode: "t2i" | "img2img" | "multi_ref";
  workflow: StudioWorkflow | null;
  tiers: StudioSizeTier[];
  ratios: StudioSizeRatio[];
  /** 采样器枚举（ComfyUI /object_info 提取；空数组=未拿到，下拉可手输） */
  samplerOptions?: string[];
  /** 调度器枚举（同上） */
  schedulerOptions?: string[];
}>();

const ratioSel = ref("");
const tierSel = ref("");

const samplerSelectOptions = computed(() =>
  (props.samplerOptions || []).map((s) => ({ label: s, value: s }))
);
const schedulerSelectOptions = computed(() =>
  (props.schedulerOptions || []).map((s) => ({ label: s, value: s }))
);
const samplerHint = computed(() => {
  const n = (props.samplerOptions || []).length;
  return n
    ? `枚举来自 ComfyUI /object_info（${n} 个采样器），也可手动输入自定义值；留空用工作流默认。`
    : "暂未拿到 ComfyUI 枚举（服务器未连上），可手动输入采样器/调度器名；留空用工作流默认。";
});

// v7.9.3：工作流实际参数（基础工作流解析注记的采样默认值），让「跟随默认」看得见
const wfD = computed(() => props.workflow?.sampler_defaults || null);

const dispSteps = computed(() => {
  const v = props.params.steps ?? wfD.value?.steps;
  return v != null ? Math.round(v) : 20;
});
const dispCfg = computed(() => {
  const v = props.params.cfg ?? wfD.value?.cfg;
  return v != null ? v : 5;
});
const dispDenoise = computed(() => {
  const v = props.params.denoise ?? wfD.value?.denoise;
  return v != null ? v : 0.75;
});

const defaultsText = computed(() => {
  const d = wfD.value;
  if (!d || (d.steps == null && d.cfg == null && d.denoise == null)) {
    return "未解析到工作流采样默认值，将用工作流节点当前值；改动只对本次生图生效。";
  }
  const parts: string[] = [];
  if (d.steps != null) parts.push(`${Math.round(d.steps)} 步`);
  if (d.cfg != null) parts.push(`CFG ${d.cfg}`);
  if (d.denoise != null && props.mode !== "t2i") parts.push(`降噪 ${d.denoise}`);
  if (d.sampler_name) parts.push(String(d.sampler_name));
  return `工作流默认：${parts.join(" · ")}（不调即用；改动只对本次生图生效）`;
});

const wfSizeLabel = computed(() => {
  const w = props.workflow?.default_width;
  const h = props.workflow?.default_height;
  if (props.params.width != null && props.params.height != null) {
    return { w: "宽", h: "高", text: "" };
  }
  if (w && h) {
    return { w: String(w), h: String(h), text: `跟随工作流默认 ${w}×${h}` };
  }
  return { w: "宽", h: "高", text: "" };
});

const ratioOptions = computed(() => [
  { label: "跟随工作流默认", value: "" },
  ...props.ratios.filter((r) => r.enabled !== false).map((r) => ({
    label: `${r.name}（${r.base}）`,
    value: r.name,
  })),
]);

const tierOptions = computed(() =>
  props.tiers.map((t) => ({ label: `${t.label}（约 ${t.budget_mp}MP）`, value: t.key }))
);

watch(
  () => props.ratios,
  (list) => {
    if (!list.length) return;
    if (!tierSel.value && props.tiers.length) {
      // 默认取后端给的最大档（通常第二档）
      const defKey = props.tiers.length > 1 ? props.tiers[1].key : props.tiers[0].key;
      tierSel.value = defKey;
    }
  },
  { immediate: true }
);

function onAutoSizeChange(v: boolean) {
  props.params.size_from_prompt = v;
  lsSet("anima_studio_auto_size", v ? "1" : "0");
}

function applyRatio() {
  if (!ratioSel.value) {
    // 清空 → 跟随工作流默认
    props.params.width = null;
    props.params.height = null;
    return;
  }
  const r = props.ratios.find((x) => x.name === ratioSel.value);
  if (!r) return;
  const key = tierSel.value || (props.tiers.length > 1 ? props.tiers[1].key : props.tiers[0]?.key);
  const size = (r.sizes || {})[key] || "";
  const m = String(size).match(/(\d+)\s*[×x*]\s*(\d+)/);
  if (m) {
    props.params.width = parseInt(m[1], 10);
    props.params.height = parseInt(m[2], 10);
  }
}

// 外部把宽高改掉（如复用参数）时，让比例选择回显
watch(
  () => [props.params.width, props.params.height] as const,
  ([w, h]) => {
    if (w == null || h == null) {
      ratioSel.value = "";
      return;
    }
    const hit = props.ratios.find((r) =>
      Object.values(r.sizes || {}).some((s) => {
        const m = String(s).match(/(\d+)\s*[×x*]\s*(\d+)/);
        return m && parseInt(m[1], 10) === w && parseInt(m[2], 10) === h;
      })
    );
    ratioSel.value = hit ? hit.name : "custom-hidden";
    if (!hit) {
      // 手动尺寸不在比例表里：选择器显示为空但不触发清空
      ratioSel.value = "";
    }
  }
);
</script>

<style scoped>
.params-panel { display: flex; flex-direction: column; gap: 8px; }
.pp-head { margin-top: 2px; }
.pp-label { font-size: 13px; font-weight: 600; color: var(--text-main); }
.size-row { display: flex; gap: 8px; align-items: center; }
.size-x { color: var(--text-sub); }
.size-mode { display: flex; align-items: center; gap: 8px; }
.auto-size-note { font-size: 11px; color: var(--text-sub); white-space: nowrap; flex: none; }
.ctrl-row { display: flex; align-items: center; gap: 8px; }
.ctrl-label { font-size: 12px; color: var(--text-sub); flex: none; width: 52px; white-space: nowrap; }
.ctrl-hint { font-size: 11px; color: var(--text-sub); opacity: 0.85; }
.adv :deep(.n-collapse-item__header) { font-size: 12px; padding-top: 2px; padding-bottom: 2px; }
.adv :deep(.n-collapse-item__content) { padding-top: 4px; }
/* v7.9.5：高级区两行下拉之间留出间距 */
.adv-row { margin-bottom: 10px; }
.adv-row:last-of-type { margin-bottom: 8px; }
</style>
