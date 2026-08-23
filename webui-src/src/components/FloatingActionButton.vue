<template>
  <div
    ref="fab"
    class="fab"
    :class="{ dragging }"
    :style="{ left: x + 'px', top: y + 'px' }"
    @pointerdown="onDown"
    @click="onNativeClick"
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

// 点击与拖动的位移判定阈值（px）。真实手机 tap 时手指常有 5~10px 抖动，
// 阈值过小会把点击误判为拖动导致「点了没反应」。
const CLICK_SLOP = 10;
const SIZE = 56;

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
  x.value = clamp(w - SIZE - 16, 0, w - SIZE);
  y.value = clamp(h - SIZE - 16, 0, h - SIZE);
}

function onDown(e: PointerEvent) {
  dragging.value = true;
  moved = false;
  startX = e.clientX;
  startY = e.clientY;
  origX = x.value;
  origY = y.value;
  // 捕获后续 pointer 事件，保证拖动跟手；老环境不支持时静默降级
  try {
    (fab.value as HTMLElement).setPointerCapture(e.pointerId);
  } catch {
    /* ignore */
  }
}

function onMove(e: PointerEvent) {
  if (!dragging.value) return;
  const dx = e.clientX - startX;
  const dy = e.clientY - startY;
  if (Math.abs(dx) > CLICK_SLOP || Math.abs(dy) > CLICK_SLOP) moved = true;
  x.value = clamp(origX + dx, 0, window.innerWidth - SIZE);
  y.value = clamp(origY + dy, 0, window.innerHeight - SIZE);
}

function onUp(e: PointerEvent) {
  if (!dragging.value) return;
  // 抬起时最终位移再判定一次（防止过程中未超阈值、抬起瞬间拉动）
  const dx = e.clientX - startX;
  const dy = e.clientY - startY;
  if (Math.abs(dx) > CLICK_SLOP || Math.abs(dy) > CLICK_SLOP) moved = true;
  dragging.value = false;
  try {
    (fab.value as HTMLElement).releasePointerCapture(e.pointerId);
  } catch {
    /* ignore */
  }
  // 注意：这里不 emit。点击统一交给浏览器合成的原生 click（onNativeClick）触发，
  // 保证不支持 PointerEvent / pointer 流程异常时点击依然可用。
}

// 核心点击入口：鼠标/触摸/笔在无拖动时都会由浏览器合成 click。
// 拖动（moved=true）或拖动后的合成 click 会被跳过，不会误触发。
function onNativeClick() {
  if (moved) {
    moved = false;
    return;
  }
  moved = false;
  emit("click");
}

onMounted(() => {
  placeDefault();
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  window.addEventListener("pointercancel", onUp);
});

onBeforeUnmount(() => {
  window.removeEventListener("pointermove", onMove);
  window.removeEventListener("pointerup", onUp);
  window.removeEventListener("pointercancel", onUp);
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