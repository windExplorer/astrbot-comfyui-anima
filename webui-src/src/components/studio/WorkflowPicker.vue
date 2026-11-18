<template>
  <div class="wf-picker">
    <div v-if="!filtered.length" class="wf-empty">
      {{ emptyHint }}
    </div>
    <div v-else class="wf-grid">
      <div
        v-for="w in filtered"
        :key="w.name"
        class="wf-card"
        :class="{ active: w.name === modelValue, disabled: !w.enabled }"
        @click="pick(w)"
      >
        <div class="wf-name">
          <span class="kind-badge" :class="w.kind || 'unknown'">{{ kindLabel(w.kind) }}</span>
          <span class="wf-title">{{ w.name }}</span>
        </div>
        <div class="wf-meta">
          <span v-if="w.basemodel_name" class="chip">{{ w.basemodel_name }}</span>
          <span v-if="w.server_name" class="chip dim">🛰 {{ w.server_name }}</span>
          <span v-if="w.multi_image" class="chip accent">多参考</span>
          <span v-if="!w.enabled" class="chip warn">已停用</span>
        </div>
        <div v-if="w.description" class="wf-desc">{{ w.description }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { StudioWorkflow } from "@/api/studio";

const props = defineProps<{
  workflows: StudioWorkflow[];
  /** 当前选中工作流名 */
  modelValue: string;
  /** 当前模式：决定类型过滤口径 */
  mode: "t2i" | "img2img" | "multi_ref";
}>();

const emit = defineEmits<{ (e: "update:modelValue", v: string): void }>();

const filtered = computed(() => {
  const wantImg = props.mode !== "t2i";
  return props.workflows.filter((w) => {
    if (wantImg) {
      // 图生图模式：只要带图加载节点的（upscale 不进生图页）；解析失败(null)的旧版条目不展示
      return w.kind === "img2img";
    }
    return w.kind === "t2i" || w.kind === null;
  });
});

const emptyHint = computed(() =>
  props.mode === "t2i"
    ? "还没有可用的文生图工作流——请到「基础工作流」页上传并解析通过后，在「配置 → 工作流」里建一条启用条目。"
    : "还没有可用的图生图工作流——需要一个带图加载节点（LoadImage）的基础工作流。"
);

function kindLabel(kind: string | null): string {
  if (kind === "t2i") return "文生图";
  if (kind === "img2img") return "图生图";
  if (kind === "upscale") return "放大";
  return "未知";
}

function pick(w: StudioWorkflow) {
  emit("update:modelValue", w.name);
}
</script>

<style scoped>
.wf-picker { width: 100%; }
.wf-empty {
  padding: 14px 12px;
  font-size: 12px;
  color: var(--text-sub);
  background: var(--bg-panel);
  border: 1px dashed var(--border-color);
  border-radius: 10px;
  line-height: 1.6;
}
.wf-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 8px;
}
.wf-card {
  border: 1.5px solid var(--border-color);
  border-radius: 10px;
  padding: 8px 10px;
  cursor: pointer;
  background: var(--bg-panel);
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.1s;
  min-width: 0;
}
.wf-card:hover { border-color: var(--accent); transform: translateY(-1px); }
.wf-card.active {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 25%, transparent);
  background: color-mix(in srgb, var(--accent) 8%, var(--bg-panel));
}
.wf-card.disabled { opacity: 0.55; }
.wf-name { display: flex; align-items: center; gap: 6px; min-width: 0; }
.wf-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
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
.wf-meta { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
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
  margin-top: 5px;
  font-size: 11px;
  color: var(--text-sub);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
