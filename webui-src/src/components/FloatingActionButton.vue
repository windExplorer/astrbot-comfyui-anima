<template>
  <div
    ref="fab"
    class="fab"
    :class="{ dragging }"
    :style="{ left: x + 'px', top: y + 'px' }"
    @pointerdown="onDown"
  >
    <slot>
      <span class="fab-icon">⚙</span>
    </slot>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from "vue";

const emit = defineEmits<{ (e: "click"): void }>();

const fab = ref<HTMLElement | null>(null);
const x = ref(0);
const y = ref(0);
const dragging = ref(false);

let startX = 0;
let startY = 0;
let origX = 0;
let origY = 0;
let moved = false;

function clamp(v: number, min: number, max: number) {
  return Math.max(min, Math.min(max, v));
}

function placeDefault() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const size = 56;
  x.value = clamp(w - size - 16, 0, w - size);
  y.value = clamp(h - size - 16, 0, h - size);
}

function onDown(e: PointerEvent) {
  dragging.value = true;
  moved = false;
  startX = e.clientX;
  startY = e.clientY;
  origX = x.value;
  origY = y.value;
  (fab.value as HTMLElement).setPointerCapture(e.pointerId);
}

function onMove(e: PointerEvent) {
  if (!dragging.value) return;
  const dx = e.clientX - startX;
  const dy = e.clientY - startY;
  if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
  const size = 56;
  x.value = clamp(origX + dx, 0, window.innerWidth - size);
  y.value = clamp(origY + dy, 0, window.innerHeight - size);
}

function onUp(e: PointerEvent) {
  if (!dragging.value) return;
  dragging.value = false;
  try {
    (fab.value as HTMLElement).releasePointerCapture(e.pointerId);
  } catch {
    /* ignore */
  }
  // 拖动距离很小才视为点击
  if (!moved) emit("click");
}

onMounted(() => {
  placeDefault();
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
});

onBeforeUnmount(() => {
  window.removeEventListener("pointermove", onMove);
  window.removeEventListener("pointerup", onUp);
});
</script>

<style scoped>
.fab {
  position: fixed;
  z-index: 2000;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: linear-gradient(135deg, #ff7eb3, #ff758c);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 20px rgba(255, 117, 140, 0.5);
  cursor: grab;
  user-select: none;
  touch-action: none;
  font-size: 24px;
}
.fab:active {
  cursor: grabbing;
}
.fab.dragging {
  opacity: 0.85;
  box-shadow: 0 10px 28px rgba(255, 117, 140, 0.6);
}
.fab-icon {
  pointer-events: none;
}
</style>
