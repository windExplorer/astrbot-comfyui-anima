<template>
  <div class="scroll-jump">
    <!-- 回到顶部 -->
    <button class="sj-btn" title="回到顶部" @click.prevent="scrollTop">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 15l-6-6-6 6"/></svg>
    </button>
    <!-- 去底部 -->
    <button class="sj-btn" title="去底部" @click.prevent="scrollBottom">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
    </button>
  </div>
</template>

<script setup lang="ts">
/**
 * 回到顶部 / 去底部 悬浮按钮组。
 * 通过 prop 传入需要滚动的容器；不传时滚动 window（页面级滚动）。
 */
const props = withDefaults(
  defineProps<{ scrollTarget?: HTMLElement | null; offset?: number }>(),
  { scrollTarget: null, offset: 0 }
);

function getScroller(): HTMLElement | Window {
  if (props.scrollTarget) return props.scrollTarget;
  // 未指定滚动容器时滚动整个页面（移动端 MobileShell 为页面级滚动）
  return window;
}

function scrollTop() {
  const s = getScroller();
  if (s instanceof Window) s.scrollTo({ top: 0, behavior: "smooth" });
  else s.scrollTo({ top: props.offset, behavior: "smooth" });
}

function scrollBottom() {
  const s = getScroller();
  if (s instanceof Window) s.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
  else s.scrollTo({ top: s.scrollHeight, behavior: "smooth" });
}
</script>

<style scoped>
.scroll-jump {
  position: fixed;
  right: 16px;
  bottom: 84px;
  z-index: 1800;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sj-btn {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  border: none;
  background: linear-gradient(135deg, #ffb3d1, #ff8fb3);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(255, 143, 179, 0.4);
  padding: 0;
}
.sj-btn svg {
  width: 18px;
  height: 18px;
}
.sj-btn:active {
  transform: scale(0.94);
}
</style>