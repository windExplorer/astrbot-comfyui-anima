<template>
  <div class="area-chart" ref="wrapEl">
    <svg
      v-if="pts.length >= 2"
      class="area-svg"
      :viewBox="`0 0 ${W} ${H}`"
      preserveAspectRatio="none"
      role="img"
    >
      <defs>
        <linearGradient id="acFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" :stop-color="accent" stop-opacity="0.55" />
          <stop offset="55%" :stop-color="accent" stop-opacity="0.18" />
          <stop offset="100%" :stop-color="accent" stop-opacity="0.03" />
        </linearGradient>
        <filter id="acGlow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="2.2" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <!-- 水平网格线 + Y 轴刻度 -->
      <g class="ac-grid">
        <template v-for="g in gridLines" :key="'g' + g.y">
          <line :x1="pad.l" :x2="W - pad.r" :y1="g.y" :y2="g.y" class="ac-grid-line" />
          <text :x="pad.l - 8" :y="g.y + 3.5" class="ac-axis-tick" text-anchor="end">{{ g.label }}</text>
        </template>
      </g>

      <!-- 渐变面积 -->
      <path :d="areaPath" fill="url(#acFill)" class="ac-area" />

      <!-- 平滑曲线（带光晕） -->
      <path :d="linePath" class="ac-line-glow" />
      <path :d="linePath" class="ac-line" />

      <!-- X 轴刻度 -->
      <g class="ac-xaxis">
        <template v-for="t in xTicks" :key="'x' + t.i">
          <line :x1="t.x" :x2="t.x" :y1="H - pad.b" :y2="H - pad.b + 4" class="ac-axis-tickline" />
          <text :x="t.x" :y="H - pad.b + 18" class="ac-axis-tick" text-anchor="middle">{{ t.label }}</text>
        </template>
      </g>

      <!-- 峰值标签 -->
      <g class="ac-peaks">
        <template v-for="p in peaks" :key="'p' + p.i">
          <line :x1="p.x" :x2="p.x" :y1="p.y - 4" :y2="p.y - 16" class="ac-peak-line" />
          <rect
            :x="p.tagX"
            :y="p.y - 30"
            :width="p.tagW"
            height="18"
            rx="9"
            class="ac-peak-tag"
          />
          <text :x="p.tagX + p.tagW / 2" :y="p.y - 16" class="ac-peak-text" text-anchor="middle">{{ p.label }}</text>
        </template>
      </g>

      <!-- 数据点 -->
      <g class="ac-dots">
        <template v-for="(p, i) in pts" :key="'d' + i">
          <circle :cx="p.x" :cy="p.y" r="6" class="ac-dot-halo" opacity="0" />
          <circle :cx="p.x" :cy="p.y" r="3.2" class="ac-dot" />
        </template>
      </g>

      <!-- hover 指示条 -->
      <g v-if="hover != null && hovered" class="ac-hover">
        <line :x1="hover.x" :x2="hover.x" :y1="pad.t" :y2="H - pad.b" class="ac-hover-line" />
        <circle :cx="hover.x" :cy="hover.y" r="9" class="ac-hover-halo" />
        <circle :cx="hover.x" :cy="hover.y" r="4.5" class="ac-hover-dot" />
        <g :transform="`translate(${tipX}, ${tipY})`" class="ac-tip">
          <rect :width="tipW" height="36" rx="7" class="ac-tip-bg" />
          <text :x="tipW / 2" :y="14" class="ac-tip-label" text-anchor="middle">{{ hover.xLabel }}</text>
          <text :x="tipW / 2" :y="29" class="ac-tip-value" text-anchor="middle">{{ hover.yLabel }}</text>
        </g>
      </g>
    </svg>

    <div v-else class="ac-empty">暂无数据</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

export interface AreaPoint {
  x: string;
  y: number;
}

const props = withDefaults(
  defineProps<{
    data: AreaPoint[];
    height?: number;
    formatY?: (v: number) => string;
  }>(),
  {
    height: 260,
    formatY: (v: number) => String(v),
  }
);

const wrapEl = ref<HTMLElement | null>(null);

// ---- 几何常量 ----
// W 为响应式：跟随容器实际宽度，保证图表占满宽度并在窗口/容器尺寸变化时重新布局
const W = ref(800);
const H = 300;
// 顶部 padding 需容纳峰值标签(hover 最高到 y-48)与曲线光晕，防止被裁切
const pad = { l: 44, r: 14, t: 40, b: 32 };

// ---- 主题取色（含 fallback，保证沙箱内 CSS 变量不可用时仍可显示）----
const accent = computed(() => cssVar("--accent", "#ff8fb3"));
const textSub = computed(() => cssVar("--text-sub", "#9a7a88"));
const borderColor = computed(() => cssVar("--border-color", "#ffe3ec"));
const textMain = computed(() => cssVar("--text-main", "#3a2a33"));
const bgPanel = computed(() => cssVar("--bg-panel", "#ffffff"));

function cssVar(name: string, fallback: string): string {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch (e) {
    return fallback;
  }
}

// ---- 容器尺寸测量 + 监听（ResizeObserver 为主，window.resize 兜底）----
let ro: ResizeObserver | null = null;
let rafId = 0;

function measure() {
  const el = wrapEl.value;
  if (!el) return;
  const w = el.clientWidth;
  if (w > 0 && Math.abs(w - W.value) > 0.5) {
    W.value = w;
  }
}

function scheduleMeasure() {
  if (rafId) return;
  rafId = window.requestAnimationFrame(() => {
    rafId = 0;
    measure();
  });
}

// ---- 数据点映射（依赖响应式 W，尺寸变化自动重算）----
const pts = computed(() => {
  const d = props.data || [];
  if (!d.length) return [];
  const max = Math.max(...d.map((p) => p.y), 1);
  const ih = H - pad.t - pad.b;
  const step = (W.value - pad.l - pad.r) / Math.max(d.length - 1, 1);
  return d.map((p, i) => ({
    x: pad.l + i * step,
    y: pad.t + ih - (p.y / max) * ih,
    xLabel: p.x,
    yLabel: props.formatY(p.y),
    raw: p.y,
  }));
});

// ---- 坐标轴 ----
const Y_STEPS = [0, 0.25, 0.5, 0.75, 1];
const gridLines = computed(() => {
  const max = Math.max(...(props.data || []).map((p) => p.y), 1);
  const ih = H - pad.t - pad.b;
  return Y_STEPS.map((r) => ({
    y: pad.t + ih - r * ih,
    label: props.formatY(Math.round(r * max)),
  }));
});

const xTicks = computed(() => {
  const n = pts.value.length;
  if (!n) return [];
  const maxCount = 8;
  const step = Math.max(1, Math.ceil(n / maxCount));
  const arr: { i: number; x: number; label: string }[] = [];
  for (let i = 0; i < n; i += step) {
    arr.push({ i, x: pts.value[i].x, label: pts.value[i].xLabel });
  }
  if (arr[arr.length - 1]?.i !== n - 1) {
    arr.push({ i: n - 1, x: pts.value[n - 1].x, label: pts.value[n - 1].xLabel });
  }
  return arr;
});

// ---- 平滑曲线（Catmull-Rom → 三次贝塞尔）----
function smoothPath(points: { x: number; y: number }[]): string {
  const n = points.length;
  if (n === 1) return `M${points[0].x},${points[0].y}`;
  if (n === 2) return `M${points[0].x},${points[0].y} L${points[1].x},${points[1].y}`;
  let d = `M${points[0].x},${points[0].y}`;
  for (let i = 0; i < n - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(n - 1, i + 2)];
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${p2.x.toFixed(2)},${p2.y.toFixed(2)}`;
  }
  return d;
}

const linePath = computed(() => smoothPath(pts.value));

const areaPath = computed(() => {
  const p = pts.value;
  if (!p.length) return "";
  const baseY = H - pad.b;
  return `${linePath.value} L${p[p.length - 1].x.toFixed(2)},${baseY} L${p[0].x.toFixed(2)},${baseY} Z`;
});

// ---- 峰值标签（只取最高的 2 个，且避免重叠）----
const peaks = computed(() => {
  const p = pts.value;
  if (p.length < 3) return [];
  const sorted = [...p].sort((a, b) => b.raw - a.raw);
  const top = sorted.slice(0, 2);
  const tagW = 64;
  return top.map((pt) => {
    let tagX = pt.x - tagW / 2;
    tagX = Math.max(pad.l, Math.min(W.value - pad.r - tagW, tagX));
    return { i: pt.x, x: pt.x, y: pt.y, tagX, tagW, label: pt.yLabel };
  });
});

// ---- hover 交互（用 mousemove 计算最近点，不依赖 Canvas 量字）----
const hover = ref<{ x: number; y: number; xLabel: string; yLabel: string } | null>(null);
const hovered = ref(false);
const tipW = 84;

const tipX = computed(() => {
  if (!hover.value) return 0;
  const w = tipW + 16;
  return Math.max(4, Math.min(W.value - w - 4, hover.value.x - tipW / 2));
});
const tipY = computed(() => Math.max(4, hover.value ? hover.value.y - 48 : 0));

function onMove(e: MouseEvent) {
  if (!wrapEl.value || !pts.value.length) return;
  const rect = wrapEl.value.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const relX = ((e.clientX - rect.left) / rect.width) * W.value;
  // 找到最近的数据点
  let best = 0;
  let bestDist = Infinity;
  pts.value.forEach((p, i) => {
    const d = Math.abs(p.x - relX);
    if (d < bestDist) {
      bestDist = d;
      best = i;
    }
  });
  const p = pts.value[best];
  hover.value = { x: p.x, y: p.y, xLabel: p.xLabel, yLabel: p.yLabel };
}

function onEnter() {
  hovered.value = true;
}
function onLeave() {
  hovered.value = false;
  hover.value = null;
}

onMounted(() => {
  const el = wrapEl.value;
  if (el) {
    el.addEventListener("mousemove", onMove);
    el.addEventListener("mouseenter", onEnter);
    el.addEventListener("mouseleave", onLeave);
    measure();
    // 优先 ResizeObserver（容器尺寸变化最灵敏）；否则退化为 window.resize
    if (typeof ResizeObserver !== "undefined") {
      try {
        ro = new ResizeObserver(scheduleMeasure);
        ro.observe(el);
      } catch (e) {
        ro = null;
      }
    }
    if (!ro) {
      window.addEventListener("resize", scheduleMeasure);
    }
  }
});
onBeforeUnmount(() => {
  const el = wrapEl.value;
  if (el) {
    el.removeEventListener("mousemove", onMove);
    el.removeEventListener("mouseenter", onEnter);
    el.removeEventListener("mouseleave", onLeave);
  }
  if (ro) { ro.disconnect(); ro = null; }
  if (!ro) {
    window.removeEventListener("resize", scheduleMeasure);
  }
  if (rafId) { window.cancelAnimationFrame(rafId); rafId = 0; }
});
</script>

<style scoped>
.area-chart {
  width: 100%;
  position: relative;
  overflow: hidden;
}
.area-svg {
  display: block;
  width: 100%;
  height: v-bind("props.height + 'px'");
}

/* 网格线 */
.ac-grid-line {
  stroke: v-bind(borderColor);
  stroke-width: 1;
  stroke-dasharray: 3 4;
}
.ac-axis-tick {
  font-size: 10.5px;
  fill: v-bind(textSub);
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
}
.ac-axis-tickline {
  stroke: v-bind(borderColor);
  stroke-width: 1;
}

/* 曲线 */
.ac-line {
  fill: none;
  stroke: v-bind(accent);
  stroke-width: 2.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.ac-line-glow {
  fill: none;
  stroke: v-bind(accent);
  stroke-width: 5;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 0.22;
  filter: url(#acGlow);
}
.ac-area {
  transition: opacity 0.2s;
}

/* 数据点 */
.ac-dot {
  fill: v-bind(bgPanel);
  stroke: v-bind(accent);
  stroke-width: 2;
  transition: r 0.15s;
}
.ac-dot-halo {
  fill: v-bind(accent);
  pointer-events: none;
}

/* 峰值标签 */
.ac-peak-line {
  stroke: v-bind(accent);
  stroke-width: 1;
  stroke-dasharray: 2 2;
  opacity: 0.6;
}
.ac-peak-tag {
  fill: v-bind(accent);
  opacity: 0.14;
}
.ac-peak-text {
  font-size: 10px;
  font-weight: 700;
  fill: v-bind(accent);
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
}

/* hover */
.ac-hover-line {
  stroke: v-bind(accent);
  stroke-width: 1;
  stroke-dasharray: 3 3;
  opacity: 0.55;
}
.ac-hover-halo {
  fill: v-bind(accent);
  opacity: 0.18;
}
.ac-hover-dot {
  fill: v-bind(accent);
  stroke: v-bind(bgPanel);
  stroke-width: 2;
}
.ac-tip-bg {
  fill: v-bind(textMain);
  opacity: 0.92;
}
.ac-tip-label {
  font-size: 10.5px;
  fill: v-bind(bgPanel);
  opacity: 0.85;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
}
.ac-tip-value {
  font-size: 13px;
  font-weight: 700;
  fill: v-bind(bgPanel);
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
}

.ac-empty {
  color: v-bind(textSub);
  text-align: center;
  padding: 30px;
  font-size: 13px;
}
</style>
