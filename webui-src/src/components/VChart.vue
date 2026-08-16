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

function ensureChart(): VChartCore | null {
  if (chart) return chart;
  if (!chartEl.value || !props.option) return null;
  try {
    chart = new VChartCore(props.option, { dom: chartEl.value });
    chart.renderAsync();
    return chart;
  } catch (e) {
    chart = null;
    return null;
  }
}

async function render() {
  if (destroyed) return;
  if (!props.option) return;
  // 等待容器有实际尺寸（避免在 v-show 隐藏或布局未就绪时宽高为 0 渲染失败）
  await nextTick();
  if (destroyed) return;
  if (chart) {
    chart.updateSpec(props.option);
  } else {
    ensureChart();
  }
}

watch(
  () => props.option,
  (val) => {
    if (val) render();
  },
  { deep: true }
);

onMounted(async () => {
  await nextTick();
  render();
});

onBeforeUnmount(() => {
  destroyed = true;
  if (chart) {
    chart.release();
    chart = null;
  }
});
</script>
