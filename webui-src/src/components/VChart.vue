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

function ensureChart(): boolean {
  if (chart) return true;
  if (!chartEl.value || !props.option) return false;
  const el = chartEl.value;
  if (el.clientWidth === 0 && el.clientHeight === 0) return false; // 容器未就绪
  try {
    chart = new VChartCore(props.option, { dom: el });
    chart.renderAsync();
    return true;
  } catch (e) {
    chart = null;
    return false;
  }
}

async function render() {
  if (destroyed || !props.option) return;
  await nextTick();
  if (destroyed) return;
  if (chart) {
    try { chart.updateSpec(props.option); } catch (e) { /* ignore */ }
  } else {
    ensureChart();
  }
}

watch(
  () => props.option,
  (val) => { if (val) render(); },
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
