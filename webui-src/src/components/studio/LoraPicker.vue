<template>
  <div class="lora-picker">
    <div class="lora-head">
      <n-input v-model:value="keyword" size="small" placeholder="搜索 LoRA 名称 / 关键字" clearable />
      <n-button size="small" :type="modelValue.length ? 'default' : 'primary'" ghost @click="clearAll" :disabled="!modelValue.length">
        清空已选
      </n-button>
    </div>

    <div v-if="!filtered.length" class="lora-empty">
      {{ allowedSet !== null ? "当前工作流没有兼容的 LoRA（按底模筛选）" : "没有匹配的 LoRA（库为空或已被过滤）" }}
    </div>

    <div class="lora-list">
      <div
        v-for="l in filtered"
        :key="l.name"
        class="lora-row"
        :class="{ selected: isSel(l.name) }"
      >
        <div class="lora-main" @click="toggle(l)">
          <span class="sel-dot" :class="{ on: isSel(l.name) }">{{ isSel(l.name) ? "✓" : "" }}</span>
          <div class="lora-info">
            <div class="lora-name">
              {{ l.name }}
              <span v-if="l.starred" class="star">★</span>
              <span v-if="l.category" class="chip">{{ l.category }}</span>
              <span v-if="l.base_model" class="chip dim">{{ l.base_model }}</span>
            </div>
            <div v-if="l.trigger_words" class="lora-triggers" :title="l.trigger_words">触发词：{{ l.trigger_words }}</div>
          </div>
        </div>
        <div v-if="isSel(l.name)" class="lora-ctrl">
          <div class="ctrl-item">
            <span class="ctrl-label">权重</span>
            <n-slider
              :value="selWeight(l.name)"
              :min="0"
              :max="1.5"
              :step="0.05"
              style="flex: 1"
              @update:value="(v: number) => setWeight(l.name, v)"
            />
            <n-input-number
              :value="selWeight(l.name)"
              size="tiny"
              :min="0"
              :max="1.5"
              :step="0.05"
              style="width: 82px"
              @update:value="(v: number | null) => setWeight(l.name, v ?? 0)"
            />
          </div>
          <div v-if="l.presets.length" class="ctrl-item">
            <span class="ctrl-label">预设</span>
            <n-select
              size="tiny"
              clearable
              :value="selPreset(l.name)"
              :options="presetOptions(l)"
              placeholder="不使用预设"
              style="flex: 1"
              @update:value="(v: string | null) => setPreset(l.name, v || '')"
            />
          </div>
        </div>
      </div>
    </div>
    <div v-if="modelValue.length || availableCount" class="lora-selected-hint">
      已选 {{ modelValue.length }} 个 · 可用 {{ availableCount }} 个<template v-if="allowedSet !== null">（已按工作流底模筛选）</template>
      <template v-if="modelValue.length">；触发词会自动追加到提示词</template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import type { StudioLora, StudioLoraSel } from "@/api/studio";

const props = defineProps<{
  loras: StudioLora[];
  modelValue: StudioLoraSel[];
  /** v7.9.3：当前工作流底模兼容的 LoRA 名单（后端按聊天侧同规则预筛）。
   *  null = 未选工作流（不限制）；非空 = 只展示名单内的。 */
  allowedNames?: string[] | null;
}>();

const emit = defineEmits<{ (e: "update:modelValue", v: StudioLoraSel[]): void }>();

const keyword = ref("");

const allowedSet = computed(() => {
  if (props.allowedNames === null || props.allowedNames === undefined) return null;
  return new Set(props.allowedNames);
});

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  const aset = allowedSet.value;
  return props.loras.filter((l) => {
    if (!l.enabled) return false;
    if (aset && !aset.has(l.name)) return false;
    if (!kw) return true;
    return (
      l.name.toLowerCase().includes(kw) ||
      (l.trigger_words || "").toLowerCase().includes(kw) ||
      (l.category || "").toLowerCase().includes(kw) ||
      (l.base_model || "").toLowerCase().includes(kw)
    );
  });
});

const availableCount = computed(() => filtered.value.length);

function isSel(name: string): boolean {
  return props.modelValue.some((s) => s.name === name);
}
function selEntry(name: string): StudioLoraSel | undefined {
  return props.modelValue.find((s) => s.name === name);
}
function selWeight(name: string): number {
  const e = selEntry(name);
  return e && e.weight !== null && e.weight !== undefined ? e.weight : 0.8;
}
function selPreset(name: string): string {
  return selEntry(name)?.preset || "";
}

function toggle(l: StudioLora) {
  if (isSel(l.name)) {
    emit("update:modelValue", props.modelValue.filter((s) => s.name !== l.name));
  } else {
    const w = l.weight !== null && l.weight !== undefined ? l.weight : 0.8;
    emit("update:modelValue", [...props.modelValue, { name: l.name, weight: w, preset: "" }]);
  }
}

function clearAll() {
  emit("update:modelValue", []);
}

function setWeight(name: string, v: number) {
  emit(
    "update:modelValue",
    props.modelValue.map((s) => (s.name === name ? { ...s, weight: v } : s))
  );
}

function setPreset(name: string, v: string) {
  emit(
    "update:modelValue",
    props.modelValue.map((s) => (s.name === name ? { ...s, preset: v } : s))
  );
}

function presetOptions(l: StudioLora) {
  return l.presets.map((p) => ({ label: p.name, value: p.name }));
}
</script>

<style scoped>
.lora-picker { width: 100%; }
.lora-head { display: flex; gap: 8px; align-items: center; }
.lora-empty {
  padding: 12px;
  font-size: 12px;
  color: var(--text-sub);
  background: var(--bg-panel);
  border: 1px dashed var(--border-color);
  border-radius: 10px;
  margin-top: 8px;
}
.lora-list {
  margin-top: 8px;
  max-height: 300px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-right: 2px;
}
.lora-row {
  border: 1.5px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-panel);
  padding: 7px 9px;
}
.lora-row.selected { border-color: var(--accent); }
.lora-main { display: flex; align-items: flex-start; gap: 8px; cursor: pointer; min-width: 0; }
.sel-dot {
  flex: none;
  width: 16px;
  height: 16px;
  margin-top: 1px;
  border-radius: 50%;
  border: 1.5px solid var(--border-color);
  font-size: 11px;
  line-height: 13px;
  text-align: center;
  color: #fff;
}
.sel-dot.on { background: var(--accent); border-color: var(--accent); }
.lora-info { min-width: 0; flex: 1; }
.lora-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}
.star { color: #f0b24a; }
.chip {
  font-size: 10px;
  padding: 0 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--text-main);
}
.chip.dim { background: color-mix(in srgb, var(--text-sub) 14%, transparent); color: var(--text-sub); }
.lora-triggers {
  margin-top: 3px;
  font-size: 11px;
  color: var(--text-sub);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lora-ctrl { margin-top: 7px; padding-top: 7px; border-top: 1px dashed var(--border-color); display: flex; flex-direction: column; gap: 6px; }
.ctrl-item { display: flex; align-items: center; gap: 8px; }
.ctrl-label { font-size: 11px; color: var(--text-sub); flex: none; width: 28px; }
.lora-selected-hint { margin-top: 6px; font-size: 11px; color: var(--text-sub); }
</style>
