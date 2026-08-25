<template>
  <div
    ref="boxRef"
    class="vf-box"
    @scroll.passive="onScroll"
    @touchstart.passive="onTouchStart"
    @touchend="onTouchEnd"
    @touchcancel="onTouchEnd"
  >
    <div class="vf-pull" :style="pullStyle">
      <template v-if="pullState === 'refreshing'">↻ 刷新中…</template>
      <template v-else-if="pullState === 'release'">⇅ 松手刷新</template>
      <template v-else>⇃ 下拉刷新</template>
    </div>
    <div ref="canvasEl" class="vf-canvas" :style="canvasStyle">
      <div v-for="v in visible" :key="v.m.sha256 || v.m.sha" class="vf-item" :style="itemStyle(v)">
        <div class="wf-card" :style="{ height: v.cardH + 'px' }">
          <div class="wf-img" :style="{ height: v.h + 'px' }" @click="onClick(v.m)">
            <img :src="imgSrc(v.m)" loading="lazy" :class="{ 'nsfw-blur': nsfw && isNsfw(v.m) }" />
            <div v-if="nsfw && isNsfw(v.m)" class="nsfw-mask">
              <span>🔞</span><span class="nsfw-tip">点击查看</span>
            </div>
            <slot name="badges" :item="v.m"></slot>
            <div class="wf-info">
              <slot name="info" :item="v.m"></slot>
            </div>
          </div>
        </div>
      </div>
    </div>
    <!-- 触底哨兵：进入视口触发加载下一页 -->
    <div ref="tailRef" class="vf-tail"></div>
    <div v-if="loading" class="vf-loading"><span class="vf-spin"></span>加载中…</div>
    <div v-else-if="hasMore === false && items.length > 0" class="vf-end">— 已经到底啦 —</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from "vue";

const props = defineProps<{
  items: any[];
  imgSrc: (item: any) => string;
  gap?: number;
  loadMore?: () => void | Promise<void>;
  hasMore?: boolean;
  loading?: boolean;
  nsfw?: boolean;
  refresh?: () => void | Promise<void>;
}>();

const emit = defineEmits<{ (e: "item-click", item: any): void }>();

// ---- 下拉刷新 ----
const PULL_THRESHOLD = 70;
const pullState = ref<"idle" | "pull" | "release" | "refreshing">("idle");
const pullDist = ref(0);
const refreshing = ref(false);
let touchStartY = 0;
let touchActive = false;
let pullActive = false;
let pullTimer: any = null;

function onTouchStart(e: TouchEvent) {
  const t = e.touches[0];
  if (!t) return;
  touchStartY = t.clientY;
  touchActive = true;
  pullActive = false;
}
function onTouchMove(e: TouchEvent) {
  if (!touchActive) return;
  const t = e.touches[0];
  if (!t) return;
  const el = boxRef.value;
  const dy = t.clientY - touchStartY;
  // 仅当在顶部且向下拉时启用下拉刷新（避免与上滑滚动冲突）
  if (el && el.scrollTop <= 0 && dy > 0) {
    if (refreshing.value) {
      pullDist.value = 0;
      return;
    }
    pullActive = true;
    // 阻止页面级橡皮筋/下拉刷新，让手势归本组件处理
    if (e.cancelable) e.preventDefault();
    // 跟手时禁用过渡，松手时恢复
    if (canvasEl.value) canvasEl.value.style.transition = "none";
    // 阻尼：越拉越费力
    const damp = Math.min(1, dy / 140);
    pullDist.value = Math.min(120, dy * damp);
    pullState.value = pullDist.value >= PULL_THRESHOLD ? "release" : "pull";
  } else if (!pullActive) {
    // 正常滚动，不干预
  }
}
async function onTouchEnd() {
  touchActive = false;
  if (!pullActive) return;
  pullActive = false;
  const dist = pullDist.value;
  pullDist.value = 0;
  if (canvasEl.value) canvasEl.value.style.transition = "";
  if (dist >= PULL_THRESHOLD && !refreshing.value) {
    pullState.value = "refreshing";
    refreshing.value = true;
    try {
      if (props.refresh) await props.refresh();
    } finally {
      refreshing.value = false;
      pullState.value = "idle";
      pullTimer = setTimeout(() => {
        pullState.value = "idle";
        pullDist.value = 0;
      }, 0);
    }
  } else {
    pullState.value = "idle";
  }
}

const pullStyle = computed(() => {
  const h = pullState.value === "refreshing" ? 44 : pullDist.value;
  return {
    height: h + "px",
    opacity: Math.min(1, (pullState.value === "refreshing" ? 1 : pullDist.value) / PULL_THRESHOLD),
  };
});
const canvasStyle = computed(() => {
  const dy = pullState.value === "refreshing" ? 44 : pullDist.value;
  return { height: totalHeight.value + "px", transform: `translateY(${dy}px)` };
});

// 卡片附加高度：信息叠在图片上，无额外行
const CARD_EXTRA = 0;

const boxRef = ref<HTMLElement | null>(null);
const canvasEl = ref<HTMLElement | null>(null);
const tailRef = ref<HTMLElement | null>(null);
const scrollTop = ref(0);
const viewH = ref(600);
const colW = ref(0);
const colCount = ref(2);

let measureTries = 0;
function measure() {
  const el = boxRef.value;
  if (!el) return false;
  const w = el.clientWidth;
  if (w <= 0) return false;
  viewH.value = el.clientHeight;
  const cols = w >= 1400 ? 5 : w >= 1000 ? 4 : w >= 700 ? 3 : 2;
  colCount.value = cols;
  const gap = props.gap ?? 10;
  colW.value = (w - gap * (cols - 1)) / cols;
  return true;
}

// 首屏测量容错：容器宽度可能尚未就绪（布局未稳定），此时 rebuild 会因 colW=0
// 直接 return 导致瀑布流空白（但底部"到底了"却显示了）。用 rAF 重试直到成功。
function ensureLayout() {
  if (measure()) {
    rebuild();
    nextTick(() => maybeLoad());
    return;
  }
  measureTries++;
  if (measureTries > 10) {
    measureTries = 0;
    return;
  }
  requestAnimationFrame(ensureLayout);
}

interface VItem { m: any; col: number; top: number; h: number; cardH: number }
const layout = ref<VItem[]>([]);
const totalHeight = ref(0);

function rebuild() {
  const gap = props.gap ?? 10;
  const n = colCount.value;
  const cw = colW.value;
  if (!cw || n <= 0) return;
  const colH = new Array(n).fill(0);
  const arr: VItem[] = [];
  for (const m of props.items || []) {
    let ci = 0;
    for (let k = 1; k < n; k++) if (colH[k] < colH[ci]) ci = k;
    let h = 0;
    if (m.w && m.h && m.w > 0) h = Math.max(60, Math.round((cw * m.h) / m.w));
    else h = Math.round(cw * 1.35);
    const cardH = h + CARD_EXTRA;
    arr.push({ m, col: ci, top: colH[ci], h, cardH });
    colH[ci] += cardH + gap;
  }
  layout.value = arr;
  totalHeight.value = Math.max(1, ...colH) + 10;
}

// 虚拟化：只渲染可视区（含缓冲）附近的卡片
const visible = computed(() => {
  const top = scrollTop.value - 500;
  const bottom = scrollTop.value + viewH.value + 500;
  return layout.value.filter((v) => v.top + v.cardH >= top && v.top <= bottom);
});

function itemStyle(v: VItem) {
  const gap = props.gap ?? 10;
  return {
    transform: `translate(${v.col * (colW.value + gap)}px, ${v.top}px)`,
    width: colW.value + "px",
  };
}

function onClick(m: any) {
  emit("item-click", m);
}

function isNsfw(m: any): boolean {
  if (!m || !m.nsfw) return false;
  if (m.nsfw_blur === 0) return false;
  return true;
}

function onScroll() {
  scrollTop.value = boxRef.value?.scrollTop || 0;
  maybeLoad();
}

function maybeLoad() {
  const el = boxRef.value;
  if (!el || !props.hasMore || !props.loadMore || props.loading) return;
  if (el.scrollTop + el.clientHeight >= totalHeight.value - 400) {
    props.loadMore();
  }
}

let ro: ResizeObserver | null = null;
let io: IntersectionObserver | null = null;
onMounted(() => {
  ensureLayout();
  ro = new ResizeObserver(() => {
    measure();
    rebuild();
  });
  if (boxRef.value) {
    ro.observe(boxRef.value);
    // touchmove 需非 passive 才能 preventDefault（禁掉橡皮筋/页面刷新）
    boxRef.value.addEventListener("touchmove", onTouchMove, { passive: false });
  }
  nextTick(() => {
    // 触底哨兵：进入可视区即加载下一页，比纯 scroll 阈值更可靠
    if (boxRef.value && tailRef.value && "IntersectionObserver" in window) {
      io = new IntersectionObserver(
        (entries) => {
          if (entries.some((en) => en.isIntersecting)) maybeLoad();
        },
        { root: boxRef.value, rootMargin: "400px" }
      );
      io.observe(tailRef.value);
    }
  });
});
onUnmounted(() => {
  ro?.disconnect();
  io?.disconnect();
  boxRef.value?.removeEventListener("touchmove", onTouchMove as any);
  clearTimeout(pullTimer);
});

// 关键：父组件用 list.push(...) 原地追加数据时 items 引用不变，
// 必须监听长度变化才能触发重建（否则首屏 layout 为空 → 空白，改宽度靠 ResizeObserver 才出图）。
watch(
  () => props.items?.length ?? 0,
  () => {
    measureTries = 0;
    ensureLayout();
  }
);
watch(
  () => props.items,
  () => {
    measureTries = 0;
    ensureLayout();
  }
);
watch([colW, colCount], () => rebuild());
</script>

<style scoped>
.vf-box {
  position: relative;
  width: 100%;
  height: 100%;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior: contain;
  touch-action: pan-y;
}
.vf-canvas { position: relative; width: 100%; transition: transform 0.25s ease; }
.vf-pull {
  position: absolute; top: 0; left: 0; right: 0; z-index: 2;
  display: flex; align-items: center; justify-content: center;
  color: var(--text-sub, #9a7a88); font-size: 12px;
  overflow: hidden; pointer-events: none;
}
.vf-item { position: absolute; top: 0; left: 0; }
.wf-card {
  border-radius: 12px;
  overflow: hidden;
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border-color, #ffe3ec);
  box-shadow: 0 2px 8px rgba(255, 143, 179, 0.1);
}
.wf-img { position: relative; width: 100%; overflow: hidden; }
.wf-img img { width: 100%; height: 100%; object-fit: cover; display: block; }
.nsfw-blur { filter: blur(14px); transform: scale(1.08); }
.nsfw-mask {
  position: absolute; inset: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  color: #fff; background: rgba(0, 0, 0, 0.15); font-size: 22px; gap: 4px;
  pointer-events: none;
}
.nsfw-tip { font-size: 11px; }
/* 图片底部渐变叠层：关键信息（发布人/时间）叠在图上，保持瀑布流干净 */
.wf-info {
  position: absolute; left: 0; right: 0; bottom: 0; z-index: 2;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 18px 10px 7px;
  font-size: 11px; color: #fff;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.6));
  pointer-events: none;
}
.wf-info .when { opacity: 0.9; text-shadow: 0 1px 3px rgba(0,0,0,0.6); }
.vf-tail { height: 1px; }
.vf-loading, .vf-end { text-align: center; color: var(--text-sub, #9a7a88); padding: 14px; font-size: 12px; }
.vf-spin {
  display: inline-block; width: 14px; height: 14px; vertical-align: -2px; margin-right: 6px;
  border: 2px solid rgba(255, 143, 179, 0.3); border-top-color: #ff8fb3;
  border-radius: 50%; animation: vf-spin 0.8s linear infinite;
}
@keyframes vf-spin { to { transform: rotate(360deg); } }
</style>
