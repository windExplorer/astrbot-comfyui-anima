<template>
  <div class="cfg-field">
    <label class="field-label">
      <span class="field-name">{{ fieldName }}</span>
      <span v-if="field?.hint && !field?.obvious_hint" class="field-hint" :title="field.hint">ⓘ</span>
    </label>

    <!-- 嵌套对象：递归渲染子字段（如 gallery.nsfw 里的 threshold） -->
    <div v-if="field?.type === 'object'" class="obj-block">
      <div class="obj-block-title">
        <span>{{ fieldName }}</span>
        <span v-if="field?.hint" class="obj-block-hint">{{ field.hint }}</span>
      </div>
      <div class="obj-block-fields">
        <ConfigField
          v-for="(f, fk) in field.items || {}"
          :key="fk"
          :field-key="fk"
          :field="f"
          :model-value="modelValue && modelValue[fk]"
          @update:model-value="onSubUpdate(fk, $event)"
        />
      </div>
    </div>

    <!-- 布尔 -->
    <div v-else-if="field?.type === 'bool'" class="bool-row">
      <n-switch :value="!!modelValue" size="small" @update:value="emit('update:modelValue', $event)" />
      <span class="field-hint-text">{{ field.hint }}</span>
    </div>

    <!-- 键值行列表（如剧情模板 名::设定），可增删 -->
    <div v-else-if="field?.editor === 'kvlines'" class="kvlines">
      <div v-for="(row, idx) in kvRows" :key="idx" class="kv-row">
        <n-input v-model:value="row.key" size="small" placeholder="模板名" @update:value="emitKv" />
        <span class="kv-sep">::</span>
        <n-input v-model:value="row.val" size="small" placeholder="世界观 / 开场设定" @update:value="emitKv" />
        <n-button size="tiny" tertiary type="error" @click="removeKv(idx)">✕</n-button>
      </div>
      <n-button size="small" dashed block @click="addKv">+ 添加模板</n-button>
    </div>

    <!-- 多行文本 -->
    <n-input
      v-else-if="field?.type === 'text'"
      type="textarea"
      :value="String(modelValue ?? '')"
      :rows="3"
      placeholder=""
      @update:value="emit('update:modelValue', $event)"
    />

    <!-- 数字 + 滑块 -->
    <template v-else-if="field?.type === 'int' || field?.type === 'float' || field?.type === 'number'">
      <div class="num-row">
        <n-input-number
          :value="toNumber(modelValue)"
          :min="field?.slider?.min"
          :max="field?.slider?.max"
          :step="field?.slider?.step"
          :precision="field?.type === 'float' ? 2 : 0"
          size="small"
          style="width:140px"
          @update:value="emit('update:modelValue', $event)"
        />
        <n-slider
          v-if="field?.slider"
          :value="toNumber(modelValue)"
          :min="field.slider.min"
          :max="field.slider.max"
          :step="field.slider.step"
          style="flex:1"
          @update:value="emit('update:modelValue', $event)"
        />
      </div>
    </template>

    <!-- 带选项的下拉 -->
    <n-select
      v-else-if="field?.options?.length"
      :value="String(modelValue ?? '')"
      :options="(field.options || []).map((o: any) => ({ label: o === '' ? '（默认）' : String(o), value: String(o) }))"
      size="small"
      @update:value="emit('update:modelValue', $event)"
    />

    <!-- 通用字符串 -->
    <n-input
      v-else
      :value="String(modelValue ?? '')"
      size="small"
      placeholder=""
      @update:value="emit('update:modelValue', $event)"
    />

    <div v-if="field?.hint" class="field-hint-text">{{ field.hint }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NSwitch, NInput, NInputNumber, NSlider, NSelect, NButton } from "naive-ui";

const props = defineProps<{
  fieldKey: string;
  field: any;
  modelValue: any;
}>();

// 字段名：优先 label，其次 description，最后回退英文字段名
const fieldName = computed(() => props.field?.label || props.field?.description || props.fieldKey);

const emit = defineEmits<{
  (e: "update:modelValue", value: any): void;
}>();

function toNumber(v: any): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return isNaN(n) ? null : n;
}

// kvlines 编辑器：把 "key::val\nkey::val" 文本拆成可增删的列表
const kvRows = ref<{ key: string; val: string }[]>([]);
watch(
  () => props.modelValue,
  (v) => {
    const cur = kvRows.value
      .map((r) => r.key + "::" + r.val)
      .filter((s) => s.trim() !== "" && s !== "::")
      .join("\n");
    if (v === cur) return; // 自己编辑产生的回写,无需重建,避免输入框失焦
    const text = typeof v === "string" ? v : "";
    kvRows.value = text.split("\n").map((line) => {
      const i = line.indexOf("::");
      if (i >= 0) return { key: line.slice(0, i), val: line.slice(i + 2) };
      return { key: line, val: "" };
    });
  },
  { immediate: true }
);
function emitKv() {
  const text = kvRows.value
    .map((r) => r.key + "::" + r.val)
    .filter((s) => s.trim() !== "" && s !== "::")
    .join("\n");
  emit("update:modelValue", text);
}
function addKv() {
  kvRows.value.push({ key: "", val: "" });
  emitKv();
}
function removeKv(i: number) {
  kvRows.value.splice(i, 1);
  emitKv();
}

// object 类型：子字段变化时，拼成新对象向上 emit
function onSubUpdate(fk: string, v: any) {
  const cur: Record<string, any> = { ...(props.modelValue && typeof props.modelValue === "object" ? props.modelValue : {}) };
  cur[fk] = v;
  emit("update:modelValue", cur);
}
</script>

<style scoped>
.cfg-field { display: flex; flex-direction: column; gap: 4px; }
.obj-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--bg-body);
}
.obj-block-title { font-size: 13px; font-weight: 600; color: var(--text-main); display: flex; flex-direction: column; gap: 2px; }
.obj-block-hint { font-size: 11px; color: var(--text-sub); font-weight: 400; line-height: 1.4; }
.obj-block-fields { display: flex; flex-direction: column; gap: 10px; }
.field-label { display: flex; align-items: center; gap: 4px; font-size: 13px; font-weight: 500; }
.field-name { color: var(--text-main); }
.field-hint { cursor: help; color: var(--accent); }
.field-hint-text { color: var(--text-sub); font-size: 11px; line-height: 1.4; }
.field-desc { color: var(--text-sub); font-size: 12px; }
.kvlines { display: flex; flex-direction: column; gap: 8px; }
.kv-row { display: flex; align-items: center; gap: 6px; }
.kv-sep { color: var(--text-sub); font-family: ui-monospace, Consolas, monospace; flex: 0 0 auto; }
.kv-row :deep(.n-input:first-child) { flex: 0 0 160px; }
.kv-row :deep(.n-input) { flex: 1 1 auto; }
.num-row { display: flex; gap: 12px; align-items: center; }
.bool-row { display: flex; align-items: center; gap: 8px; }
@media (max-width: 768px) {
  .num-row { flex-direction: column; align-items: stretch; gap: 8px; }
  .num-row :deep(.n-input-number) { width: 100% !important; }
}
</style>
