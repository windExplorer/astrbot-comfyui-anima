<template>
  <div class="token-view">
    <div class="view-head">
      <div>
        <h2>Token 用量</h2>
        <p>统计插件自发起的辅助 LLM 调用（翻译 / 动漫改写 / 写实清理 / 参数提取）消耗的 token。</p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="load">刷新</n-button>
        <n-button type="error" ghost @click="resetAll">重置统计</n-button>
      </div>
    </div>

    <div class="token-scroll">
    <div class="scope-toolbar">
      <n-radio-group v-model:value="scope" size="small" @update:value="load">
        <n-radio-button value="today">今天</n-radio-button>
        <n-radio-button value="1">近 1 天</n-radio-button>
        <n-radio-button value="3">近 3 天</n-radio-button>
        <n-radio-button value="7">近 7 天</n-radio-button>
        <n-radio-button value="30">近 30 天</n-radio-button>
        <n-radio-button value="90">近 90 天</n-radio-button>
        <n-radio-button value="all">全部</n-radio-button>
      </n-radio-group>
      <n-checkbox v-model:checked="merge" size="small" @update:checked="load">合并插件记录</n-checkbox>
    </div>

    <n-spin :show="loading">
      <!-- 汇总卡片 -->
      <div v-if="summary" class="token-cards">
        <n-card size="small" class="token-card"><div class="card-num">{{ num(summary.total) }}</div><div class="card-label">合计 tokens</div></n-card>
        <n-card size="small" class="token-card"><div class="card-num">{{ num(summary.input_other) }}</div><div class="card-label">非缓存输入</div></n-card>
        <n-card size="small" class="token-card"><div class="card-num">{{ num(summary.output) }}</div><div class="card-label">输出 tokens</div></n-card>
        <n-card size="small" class="token-card"><div class="card-num">{{ num(summary.input_cached) }}</div><div class="card-label">缓存命中</div></n-card>
        <n-card size="small" class="token-card"><div class="card-num">{{ num(summary.call_count) }}</div><div class="card-label">调用次数</div></n-card>
      </div>

      <!-- 每日趋势 -->
      <div class="panel">
        <div class="panel-title"><h3>每日 Token 消耗趋势</h3></div>
        <div class="chart-wrap">
          <VChart v-if="trendSpec" :option="trendSpec" :style="{ height: '260px', width: '100%' }" />
          <div v-else class="empty">暂无数据</div>
        </div>
      </div>

      <!-- 按调用场景 -->
      <div class="panel">
        <div class="panel-title"><h3>按调用场景</h3><span class="count">翻译 / 动漫改写 / 写实清理 / 参数提取</span></div>
        <n-data-table :columns="sceneColumns" :data="scenes" :bordered="false" size="small" />
      </div>

      <!-- 按 LLM 模型 -->
      <div class="panel">
        <div class="panel-title"><h3>按 LLM 模型</h3></div>
        <n-data-table :columns="modelColumns" :data="models" :bordered="false" size="small" />
      </div>

      <!-- 用户排行 -->
      <div class="panel">
        <div class="panel-title"><h3>用户 Token 排行</h3></div>
        <n-data-table :columns="userColumns" :data="users" :bordered="false" size="small" />
      </div>

      <!-- 明细 -->
      <div class="panel">
        <div class="panel-title"><h3>明细</h3></div>
        <n-data-table :columns="detailColumns" :data="detail" :bordered="false" size="small" />
      </div>
    </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useMessage, useDialog, NButton, NRadioGroup, NRadioButton, NCheckbox, NCard, NDataTable, NSpin, NTag, type DataTableColumns } from "naive-ui";
import VChart from "@/components/VChart.vue";
import { apiGet, apiPost } from "@/api/bridge";
import { fmtDateTime } from "@/utils/format";
import { useTheme } from "@/composables/useTheme";
import { useRefresh } from "@/composables/useRefresh";

const message = useMessage();
const dialog = useDialog();
const { isDark } = useTheme();
const loading = ref(false);
const scope = ref("1");
const merge = ref(false);

const summary = ref<any>(null);
const scenes = ref<any[]>([]);
const models = ref<any[]>([]);
const users = ref<any[]>([]);
const detail = ref<any[]>([]);
const daily = ref<any[]>([]);
const hourly = ref<any[]>([]);

function num(v: number | null | undefined): string {
  if (v == null) return "0";
  return Number(v).toLocaleString();
}

const SCOPE_DAYS: Record<string, string> = { today: "1", "1": "1", "3": "3", "7": "7", "30": "30", "90": "90", all: "0" };

async function load() {
  loading.value = true;
  try {
    const days = SCOPE_DAYS[scope.value] || "30";
    const data = await apiGet("token/summary", { days, scope: scope.value, merge: merge.value ? 1 : 0 });
    summary.value = data && data.summary ? data.summary : null;
    scenes.value = (data && Array.isArray(data.scenes)) ? data.scenes : [];
    models.value = (data && Array.isArray(data.models)) ? data.models : [];
    users.value = (data && Array.isArray(data.users)) ? data.users : [];
    detail.value = (data && Array.isArray(data.detail)) ? data.detail : [];
    daily.value = (data && Array.isArray(data.daily)) ? data.daily : [];
    hourly.value = (data && Array.isArray(data.hourly)) ? data.hourly : [];
  } catch (e: any) {
    message.error(e.message || "读取 token 统计失败");
  } finally {
    loading.value = false;
  }
}

const trendSpec = computed(() => {
  const buckets = scope.value === "today" || scope.value === "1" ? hourly.value : daily.value;
  if (!buckets || !buckets.length) return null;
  const axisColor = isDark.value ? "#9a9ab0" : "#6b6b80";
  const data = buckets.map((b) => ({
    label: b.date || (b.hour != null ? `${b.hour}:00` : String(b.label || b.bucket || "")),
    value: Number(b.tokens ?? b.total_tokens ?? 0),
  }));
  // VChart 声明式 area：极简 spec + series 数组，避免顶层 area/line/point 配置导致 init 失败
  return {
    type: "area",
    data: [{ id: "area", values: data }],
    xField: "label",
    yField: "value",
    series: [
      {
        type: "area",
        xField: "label",
        yField: "value",
        area: { style: { fillOpacity: 0.35, fill: "#ff8fb3", curveType: "monotone" } },
        line: { style: { stroke: "#ff8fb3", lineWidth: 2, curveType: "monotone" } },
        point: { visible: false },
      },
    ],
    axes: [
      { orient: "bottom", label: { style: { fill: axisColor, fontSize: 11 } }, grid: { visible: false } },
      { orient: "left", label: { style: { fill: axisColor, fontSize: 11 } }, grid: { visible: true } },
    ],
    tooltip: { visible: true },
  };
});

function makeSceneColumns(): DataTableColumns {
  return [
    { title: "场景", key: "scene", render: (row) => row.scene || "-" },
    { title: "非缓存输入", key: "input", width: 110, render: (row) => num(row.input_other) },
    { title: "缓存命中", key: "cached_input", width: 100, render: (row) => num(row.input_cached) },
    { title: "输出", key: "output", width: 100, render: (row) => num(row.output) },
    { title: "合计", key: "total", width: 100, render: (row) => num(row.total) },
    { title: "调用次数", key: "call_count", width: 90, render: (row) => num(row.call_count) },
  ];
}
const sceneColumns = makeSceneColumns();

function makeModelColumns(): DataTableColumns {
  return [
    { title: "模型", key: "model", render: (row) => row.model || "-" },
    { title: "非缓存输入", key: "input", width: 110, render: (row) => num(row.input_other) },
    { title: "缓存命中", key: "cached_input", width: 100, render: (row) => num(row.input_cached) },
    { title: "输出", key: "output", width: 100, render: (row) => num(row.output) },
    { title: "合计", key: "total", width: 100, render: (row) => num(row.total) },
    { title: "调用次数", key: "call_count", width: 90, render: (row) => num(row.call_count) },
  ];
}
const modelColumns = makeModelColumns();

function makeUserColumns(): DataTableColumns {
  return [
    { title: "用户 / ID", key: "name", render: (row) => `${row.user_name || ""}${row.user_id ? ` (${row.user_id})` : ""}` || "-" },
    { title: "非缓存输入", key: "input", width: 110, render: (row) => num(row.input_other) },
    { title: "缓存命中", key: "cached_input", width: 100, render: (row) => num(row.input_cached) },
    { title: "输出", key: "output", width: 100, render: (row) => num(row.output) },
    { title: "合计", key: "total", width: 100, render: (row) => num(row.total) },
    { title: "调用次数", key: "call_count", width: 90, render: (row) => num(row.call_count) },
  ];
}
const userColumns = makeUserColumns();

function makeDetailColumns(): DataTableColumns {
  return [
    { title: "日期", key: "day_bucket", width: 150, render: (row) => row.day_bucket || fmtDateTime(row.created_at) },
    { title: "用户ID", key: "user_id", width: 110, render: (row) => row.user_id || "-" },
    { title: "场景", key: "scene", width: 120, render: (row) => row.scene || "-" },
    { title: "模型", key: "model", ellipsis: { tooltip: true }, render: (row) => row.model || "-" },
    { title: "非缓存输入", key: "input_other", width: 100, render: (row) => num(row.input_other) },
    { title: "缓存命中", key: "input_cached", width: 90, render: (row) => num(row.input_cached) },
    { title: "输出", key: "output", width: 90, render: (row) => num(row.output) },
    { title: "合计", key: "total", width: 90, render: (row) => num(row.total) },
    { title: "调用次数", key: "call_count", width: 80, render: (row) => num(row.call_count) },
  ];
}
const detailColumns = makeDetailColumns();

function resetAll() {
  dialog.warning({
    title: "重置统计",
    content: "确定要重置全部 LLM token 统计吗？此操作不可恢复！",
    positiveText: "重置全部",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("token/reset", { all: true });
        message.success("已重置");
        load();
      } catch (e: any) {
        message.error(e.message || "重置失败");
      }
    },
  });
}

useRefresh(load);
onMounted(load);
</script>

<style scoped>
.token-view { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; flex: 0 0 auto; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.view-actions { display: flex; gap: 8px; }
.token-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }
.scope-toolbar { display: flex; gap: 16px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
.token-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }
.token-card { text-align: center; }
.card-num { font-size: 22px; font-weight: 700; color: var(--accent); }
.card-label { font-size: 12px; color: var(--text-sub); margin-top: 4px; }
.panel { background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.panel-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.panel-title h3 { margin: 0; }
.count { color: var(--text-sub); font-size: 12px; }
.chart-wrap { margin-top: 8px; }
.empty { color: var(--text-sub); text-align: center; padding: 30px; }
</style>
