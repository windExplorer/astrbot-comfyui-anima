<template>
  <div class="wf-picker">
    <!-- v7.9.2：由卡片网格改为下拉选择——卡片占屏太大，且左栏高度吃紧 -->
    <n-select
      v-if="options.length"
      :value="modelValue"
      :options="options"
      filterable
      size="small"
      placeholder="选择工作流"
      :render-label="renderWfLabel"
      @update:value="(v: string) => emit('update:modelValue', v)"
    />
    <div v-else class="wf-empty">{{ emptyHint }}</div>

    <!-- 选中工作流的一行简介（类型/底模/服务器/多参考能力 + 描述） -->
    <div v-if="selected" class="wf-brief">
      <span class="kind-badge" :class="selected.kind || 'unknown'">{{ kindLabel(selected.kind) }}</span>
      <span v-if="selected.basemodel_name" class="chip">{{ selected.basemodel_name }}</span>
      <span v-if="selected.server_name" class="chip dim">🛰 {{ selected.server_name }}</span>
      <span v-if="selected.multi_image" class="chip accent">多参考</span>
      <span v-if="!selected.enabled" class="chip warn">已停用</span>
    </div>
    <div v-if="selected?.description" class="wf-desc">{{ selected.description }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, h } from "vue";
import type { StudioWorkflow } from "@/api/studio";

const props = defineProps<{
  workflows: StudioWorkflow[];
  /** 当前选中工作流名 */
  modelValue: string;
  /** 当前模式：决定类型过滤口径 */
  mode: "t2i" | "img2img" | "multi_ref";
}>();

const emit = defineEmits<{ (e: "update:modelValue", v: string): void }>();

// 图生图模式只要带图加载节点的（upscale 不进生图页）；解析失败(null)的旧版条目
// 只在文生图模式兜底展示。已停用的工作流不进下拉（出图会被拒）。
const filtered = computed(() => {
  const wantImg = props.mode !== "t2i";
  return props.workflows.filter((w) => {
    if (!w.enabled) return false;
    if (wantImg) return w.kind === "img2img";
    return w.kind === "t2i" || w.kind === null;
  });
});

const options = computed(() =>
  filtered.value.map((w) => ({ label: w.name, value: w.name, wf: w }))
);

const selected = computed(() =>
  props.workflows.find((w) => w.name === props.modelValue) || null
);

const emptyHint = computed(() =>
  props.mode === "t2i"
    ? "还没有可用的文生图工作流——请到「基础工作流」页上传并解析通过后，在「配置 → 工作流」里建一条启用条目。"
    : "还没有可用的图生图工作流——需要一个带图加载节点（LoadImage）且已启用的基础工作流条目。"
);

function kindLabel(kind: string | null): string {
  if (kind === "t2i") return "文生图";
  if (kind === "img2img") return "图生图";
  if (kind === "upscale") return "放大";
  return "未知";
}

/** 下拉选项：名称 + 底模 + 多参考标（v7.10.10 去掉文生图/图生图前缀——模式已过滤类型，前缀多余） */
function renderWfLabel(o: any) {
  const w = o?.wf as StudioWorkflow | undefined;
  if (!w) return String(o?.label ?? "");
  const kids: any[] = [
    h("span", { class: "wf-opt-name" }, w.name),
  ];
  if (w.basemodel_name) kids.push(h("span", { class: "wf-opt-meta" }, w.basemodel_name));
  if (w.multi_image) kids.push(h("span", { class: "wf-opt-multi" }, "多参考"));
  if (w.server_name) kids.push(h("span", { class: "wf-opt-meta" }, "🛰 " + w.server_name));
  return h("span", { class: "wf-opt" }, kids);
}
</script>

<style scoped>
.wf-picker { width: 100%; display: flex; flex-direction: column; gap: 6px; }
.wf-empty {
  padding: 12px;
  font-size: 12px;
  color: var(--text-sub);
  background: var(--bg-panel);
  border: 1px dashed var(--border-color);
  border-radius: 10px;
  line-height: 1.6;
}
.wf-brief { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.kind-badge {
  flex: none;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 999px;
  color: #fff;
  background: #b8c0cc;
}
.kind-badge.t2i { background: #7ea4e0; }
.kind-badge.img2img { background: #e08fbc; }
.kind-badge.upscale { background: #8fd0b0; }
.chip {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--text-main);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chip.dim { background: color-mix(in srgb, var(--text-sub) 14%, transparent); color: var(--text-sub); }
.chip.accent { background: var(--accent); color: #fff; }
.chip.warn { background: #e0a07e; color: #fff; }
.wf-desc {
  font-size: 11px;
  color: var(--text-sub);
  line-height: 1.5;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
/* 下拉选项（renderLabel 渲染，scoped 属性随 VNode 带进 portal） */
.wf-opt { display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
.wf-opt-name { font-size: 13px; color: var(--text-main); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wf-opt-meta { font-size: 10px; color: var(--text-sub); background: color-mix(in srgb, var(--text-sub) 12%, transparent); border-radius: 999px; padding: 0 6px; }
.wf-opt-multi { font-size: 10px; color: #fff; background: var(--accent); border-radius: 999px; padding: 0 6px; }
</style>
