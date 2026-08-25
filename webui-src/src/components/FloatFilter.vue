<template>
  <div class="ff" :style="{ top: top + 'px', right: right + 'px' }" ref="rootRef">
    <button class="ff-btn" @click.stop="toggle">
      <span class="ff-label">{{ current?.label || "筛选" }}</span>
      <span class="ff-caret" :class="{ up: open }">▾</span>
    </button>
    <div v-if="open" class="ff-menu">
      <button
        v-for="o in options"
        :key="o.value"
        class="ff-item"
        :class="{ on: o.value === modelValue }"
        @click="pick(o)"
      >
        {{ o.label }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";

const props = defineProps<{
  options: { label: string; value: string }[];
  modelValue: string;
  top?: number;
  right?: number;
}>();
const emit = defineEmits<{ (e: "update:modelValue", v: string): void }>();

const open = ref(false);
const rootRef = ref<HTMLElement | null>(null);
const current = computed(() => props.options.find((o) => o.value === props.modelValue));

function toggle() {
  open.value = !open.value;
}
function pick(o: { label: string; value: string }) {
  if (o.value !== props.modelValue) emit("update:modelValue", o.value);
  open.value = false;
}
function onDoc(e: PointerEvent) {
  if (rootRef.value && !rootRef.value.contains(e.target as Node)) open.value = false;
}
onMounted(() => document.addEventListener("pointerdown", onDoc, true));
onUnmounted(() => document.removeEventListener("pointerdown", onDoc, true));
</script>

<style scoped>
.ff { position: fixed; z-index: 40; }
.ff-btn {
  display: flex; align-items: center; gap: 6px;
  border: 1px solid var(--border-color, #ffe3ec); cursor: pointer;
  background: var(--bg-panel, #fff); color: var(--text-main, #3a2a33);
  border-radius: 999px; padding: 7px 14px; font-size: 13px; font-weight: 600;
  box-shadow: 0 2px 10px rgba(255, 143, 179, 0.18);
  backdrop-filter: blur(10px);
}
.ff-caret { font-size: 10px; opacity: 0.7; transition: transform 0.2s; }
.ff-caret.up { transform: rotate(180deg); }
.ff-menu {
  position: absolute; top: calc(100% + 6px); right: 0;
  background: var(--bg-panel, #fff); border: 1px solid var(--border-color, #ffe3ec);
  border-radius: 12px; padding: 4px; min-width: 120px;
  box-shadow: 0 6px 20px rgba(255, 143, 179, 0.2);
  display: flex; flex-direction: column; gap: 2px;
}
.ff-item {
  border: none; background: none; cursor: pointer; text-align: left;
  padding: 8px 12px; font-size: 13px; border-radius: 8px; color: var(--text-main, #3a2a33);
}
.ff-item:hover { background: var(--bg-body, #fff1f4); }
.ff-item.on { color: #ff6b9d; font-weight: 700; background: rgba(255, 143, 179, 0.1); }
</style>
