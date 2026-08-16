<template>
  <div ref="chartEl" :style="style"></div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { VChart as VChartCore } from "@visactor/vchart";

const props = defineProps<{
  option: any;
  style?: any;
}>();

const chartEl = ref<HTMLElement | null>(null);
let chart: VChartCore | null = null;
let destroyed = false;
let ro: ResizeObserver | null = null;

let initFailed = false;

function ensureChart(): boolean {
  if (chart) return true;
  if (initFailed) return false;
  if (!chartEl.value || !props.option) return false;
  const el = chartEl.value;
  if (el.clientWidth === 0 && el.clientHeight === 0) return false; // 容器未就绪
  try {
    chart = new VChartCore(props.option, { dom: el });
    // renderAsync 返回 promise，必须捕获异步 init 错误，避免 unhandled rejection
    Promise.resolve(chart.renderAsync()).catch(() => {
      try { chart?.release(); } catch (e2) { /* ignore */ }
      chart = null;
      initFailed = true;
    });
    return true;
  } catch (e) {
    chart = null;
    initFailed = true;
    return false;
  }
}

async function render() {
  if (destroyed || !props.option) return;
  await nextTick();
  if (destroyed) return;
  // 首次 init 已失败则不再重试（避免反复 init 报错刷屏）
  if (initFailed) {
    ensureChart();
    return;
  }
  if (chart) {
    try {
      // updateSpec 前确保内部 chart 有效，避免 "Cannot read properties of undefined (reading 'updateSpec')"
      chart.updateSpec(props.option);
      chart.resize();
      return;
    } catch (e) {
      try { chart.release(); } catch (e2) { /* ignore */ }
      chart = null;
    }
  }
  ensureChart();
}

watch(
  () => props.option,
  (val) => {
    if (!val) return;
    // spec 变化时重置失败标记，允许重新 init
    initFailed = false;
    render();
  },
  { deep: true }
);

onMounted(async () => {
  await nextTick();
  render();
  // 容器尺寸变化（flex 布局/侧栏折叠/窗口缩放）时重建或 resize，确保图表可见
  if (chartEl.value && typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver(() => {
      if (destroyed) return;
      if (!chart && props.option) {
        ensureChart();
      } else if (chart) {
        try { chart.resize(); } catch (e) { /* ignore */ }
      }
    });
    ro.observe(chartEl.value);
  }
});

onBeforeUnmount(() => {
  destroyed = true;
  if (ro) { ro.disconnect(); ro = null; }
  if (chart) { chart.release(); chart = null; }
});
</script>
