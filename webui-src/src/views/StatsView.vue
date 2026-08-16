<template>
  <div class="stats-view">
    <div class="view-head">
      <div>
        <h2>生图统计</h2>
        <p>按用户统计生图数量排行，以及近一天的生图数量趋势。</p>
      </div>
      <n-button :loading="loading" @click="load">刷新</n-button>
    </div>

    <div class="stats-scroll">
    <div class="panel">
      <div class="stats-toolbar">
        <n-radio-group v-model:value="scope" size="small" @update:value="load">
          <n-radio-button value="today">今天</n-radio-button>
          <n-radio-button value="3">近 3 天</n-radio-button>
          <n-radio-button value="7">近 7 天</n-radio-button>
          <n-radio-button value="all">全部</n-radio-button>
        </n-radio-group>
        <n-checkbox v-model:checked="merge" size="small" @update:checked="load">合并插件记录</n-checkbox>
      </div>

      <n-empty v-if="!ranking.rows?.length" description="暂无生图记录" style="padding:40px" />
      <div v-else class="ranking">
        <div v-for="(r, i) in ranking.rows" :key="i" class="rank-row">
          <span class="rank-no" :class="rankClass(i)">{{ i + 1 }}</span>
          <span class="rank-name">{{ r.user_name || r.user_id || "未知" }}</span>
          <span class="rank-count">{{ r.count }} 张</span>
          <n-progress
            type="line"
            :percentage="rankPct(r.count)"
            :show-indicator="false"
            :height="8"
            :color="rankColor(i)"
            style="flex:1"
          />
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-title">
        <h3>近一天生图数量</h3>
        <span class="count">{{ trendInfo }}</span>
      </div>
      <div class="chart-wrap">
        <VChart v-if="trendSpec" :option="trendSpec" :style="{ height: '260px', width: '100%' }" />
        <div v-else class="empty">正在加载趋势…</div>
      </div>
    </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { NButton, NRadioGroup, NRadioButton, NCheckbox, NEmpty, NProgress, useMessage } from "naive-ui";
import VChart from "@/components/VChart.vue";
import { apiGet } from "@/api/bridge";
import { useTheme } from "@/composables/useTheme";
import { useRefresh } from "@/composables/useRefresh";

const message = useMessage();
const { isDark } = useTheme();
const loading = ref(false);
const scope = ref("today");
const merge = ref(false);
const ranking = ref<{ rows: any[]; total: number }>({ rows: [], total: 0 });
const trend = ref<{ buckets: any[] }>({ buckets: [] });

const SCOPE_MAP: Record<string, string> = { today: "today", "3": "3", "7": "7", all: "all" };

async function load() {
  loading.value = true;
  try {
    const days = SCOPE_MAP[scope.value] || "all";
    const [rank, tr] = await Promise.all([
      apiGet("stats/ranking", { days, merge: merge.value ? 1 : 0 }),
      apiGet("stats/trend", { hours: 24 }),
    ]);
    ranking.value = rank || { rows: [], total: 0 };
    trend.value = tr || { buckets: [] };
  } catch (e: any) {
    message.error(e.message || "加载统计失败");
  } finally {
    loading.value = false;
  }
}

const trendInfo = computed(() => {
  const buckets = trend.value?.buckets || [];
  if (!buckets.length) return "";
  const total = buckets.reduce((s, b) => s + (Number(b.count) || 0), 0);
  return `共 ${total} 张`;
});

const trendSpec = computed(() => {
  const buckets = trend.value?.buckets || [];
  if (!buckets.length) return null;
  const axisColor = isDark.value ? "#9a9ab0" : "#6b6b80";
  const data = buckets.map((b) => ({
    x: b.hour != null ? `${b.hour}:00` : String(b.label || b.bucket || ""),
    y: Number(b.count) || 0,
  }));
  // VChart 官方最简 area spec：只声明类型 + 数据 + 字段，样式走默认，确保 init 稳定。
  // 用 SVG 渲染（renderMode: 'svg'），避免沙箱 iframe 下 Canvas 渲染受限导致图表空白。
  return {
    type: "area",
    renderMode: "svg",
    data: [{ id: "data0", values: data }],
    xField: "x",
    yField: "y",
    point: { visible: false },
    axes: [
      { orient: "bottom", label: { style: { fill: axisColor, fontSize: 11 } }, grid: { visible: false } },
      { orient: "left", label: { style: { fill: axisColor, fontSize: 11 } }, grid: { visible: true } },
    ],
  };
});

function rankPct(count: number): number {
  const max = ranking.value?.rows?.[0]?.count || 1;
  return Math.max(4, Math.round((count / max) * 100));
}
function rankColor(i: number): string {
  const colors = ["#ff8fb3", "#ffb3d1", "#ffd0e2", "#f8d8e4", "#c0a8b4"];
  return colors[i] || colors[colors.length - 1];
}
function rankClass(i: number): string {
  return i < 3 ? `top${i + 1}` : "";
}

useRefresh(load);
onMounted(load);
</script>

<style scoped>
.stats-view { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; flex: 0 0 auto; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.stats-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }
.panel { background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.stats-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
.ranking { display: flex; flex-direction: column; gap: 8px; }
.rank-row { display: flex; align-items: center; gap: 12px; }
.rank-no { width: 24px; height: 24px; border-radius: 50%; background: var(--bg-body); display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; }
.rank-no.top1 { background: #ffd70033; color: #ffd700; }
.rank-no.top2 { background: #c0c0c033; color: #c0c0c0; }
.rank-no.top3 { background: #cd7f3233; color: #cd7f32; }
.rank-name { width: 160px; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rank-count { width: 60px; font-size: 12px; color: var(--text-sub); }
.panel-title { display: flex; justify-content: space-between; align-items: center; }
.panel-title h3 { margin: 0; }
.chart-wrap { margin-top: 12px; }
.count { color: var(--text-sub); font-size: 12px; }
.empty { color: var(--text-sub); text-align: center; padding: 30px; }
</style>
