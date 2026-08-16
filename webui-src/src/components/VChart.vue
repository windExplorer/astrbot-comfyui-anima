<template>
  <div ref="chartEl" :style="style"></div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { VChart as VChartCore } from "@visactor/vchart";

const props = defineProps<{
  option: any;
  style?: any;
}>();

const chartEl = ref<HTMLElement | null>(null);
let chart: VChartCore | null = null;

onMounted(() => {
  if (!chartEl.value || !props.option) return;
  chart = new VChartCore(props.option, { dom: chartEl.value });
  chart.renderAsync();
});

watch(
  () => props.option,
  (val, old) => {
    if (!chart || !val) return;
    if (old) {
      chart.updateSpec(val);
    } else {
      chart.renderAsync();
    }
  },
  { deep: true }
);

watch(
  () => props.style,
  () => {
    if (chart) chart.resize();
  }
);

onBeforeUnmount(() => {
  if (chart) {
    chart.release();
    chart = null;
  }
});
</script>
