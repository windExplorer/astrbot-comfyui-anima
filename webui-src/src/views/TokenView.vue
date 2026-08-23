<template>
  <div class="token-view">
    <div class="view-head">
      <div>
        <h2>Token 用量</h2>
        <p>统计插件自发起的辅助 LLM 调用（翻译 / 动漫改写 / 写实清理 / 参数提取）消耗的 token。</p>
      </div>
      <!-- 刷新 / 日期单选 / 合并开关 移动端收进底部操作弹窗面板 -->
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="view-actions">
          <n-button :loading="loading" @click="load">刷新</n-button>
        </div>
      </Teleport>
    </div>

    <div class="token-scroll">
    <div class="scope-toolbar">
      <!-- 日期单选 + 合并开关：PC 端留在原位；移动端收进底部操作弹窗面板（面板内可换行） -->
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="scope-mobile-block">
          <n-radio-group v-model:value="scope" size="small" @update:value="onScopeChange" class="scope-radios">
            <n-radio-button value="today">今天</n-radio-button>
            <n-radio-button value="yesterday">昨天</n-radio-button>
            <n-radio-button value="1">近 1 天</n-radio-button>
            <n-radio-button value="3">近 3 天</n-radio-button>
            <n-radio-button value="7">近 7 天</n-radio-button>
            <n-radio-button value="30">近 30 天</n-radio-button>
            <n-radio-button value="90">近 90 天</n-radio-button>
            <n-radio-button value="all">全部</n-radio-button>
          </n-radio-group>
          <n-checkbox v-model:checked="merge" size="small" @update:checked="onScopeChange">合并插件记录</n-checkbox>
        </div>
      </Teleport>
    </div>

    <n-spin :show="loading">
      <!-- 汇总卡片 -->
      <div v-if="summary" class="token-cards">
        <n-card size="small" class="token-card"><div class="card-num">{{ fmtToken(summary.total) }}</div><div class="card-label">合计 tokens</div></n-card>
        <n-card size="small" class="token-card"><div class="card-num">{{ fmtToken(summary.input_other) }}</div><div class="card-label">非缓存输入</div></n-card>
        <n-card size="small" class="token-card"><div class="card-num">{{ fmtToken(summary.output) }}</div><div class="card-label">输出 tokens</div></n-card>
        <n-card size="small" class="token-card"><div class="card-num">{{ fmtToken(summary.input_cached) }}</div><div class="card-label">缓存命中</div></n-card>
        <n-card size="small" class="token-card"><div class="card-num">{{ fmtToken(summary.call_count) }}</div><div class="card-label">调用次数</div></n-card>
      </div>

      <!-- 每日趋势 -->
      <div class="panel">
        <div class="panel-title"><h3>每日 Token 消耗趋势</h3></div>
        <div class="chart-wrap">
          <AreaChart v-if="trendData.length" :data="trendData" :format-y="fmtToken" />
          <div v-else class="empty">该时段无 token 消耗记录，请切换范围或等待使用后刷新</div>
        </div>
      </div>

      <!-- 按调用场景 -->
      <div class="panel">
        <div class="panel-title"><h3>按调用场景</h3><span class="count">翻译 / 动漫改写 / 写实清理 / 参数提取</span></div>
        <div class="table-scroll-wrap">
          <n-data-table :columns="sceneColumns" :data="scenes" :bordered="false" size="small" :scroll-x="700" />
        </div>
      </div>

      <!-- 按 LLM 模型 -->
      <div class="panel">
        <div class="panel-title"><h3>按 LLM 模型</h3></div>
        <div class="table-scroll-wrap">
          <n-data-table :columns="modelColumns" :data="models" :bordered="false" size="small" :scroll-x="760" />
        </div>
      </div>

      <!-- 用户排行 -->
      <div class="panel">
        <div class="panel-title"><h3>用户 Token 排行</h3></div>
        <div class="table-scroll-wrap">
          <n-data-table :columns="userColumns" :data="users" :bordered="false" size="small" :scroll-x="700" />
        </div>
      </div>

      <!-- 明细 -->
      <div class="panel">
        <div class="panel-title"><h3>明细</h3></div>
        <div class="table-scroll-wrap">
          <n-data-table :columns="detailColumns" :data="detail" :bordered="false" size="small" :scroll-x="1000" />
        </div>
        <Pager :page="page" :page-size="pageSize" :total="detailTotal" @update:page="onPage" @update:page-size="onPageSize" />
      </div>
    </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useMessage, NRadioGroup, NRadioButton, NCheckbox, NCard, NDataTable, NSpin, NTag, type DataTableColumns } from "naive-ui";
import AreaChart from "@/components/AreaChart.vue";
import Pager from "@/components/Pager.vue";
import { apiGet, apiPost } from "@/api/bridge";
import { lsGet, lsSet } from "@/api/storage";
import { fmtDateTime } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import { useDevice } from "@/composables/useDevice";

const message = useMessage();
const { isMobile } = useDevice();
const loading = ref(false);
const scope = ref("1");
const merge = ref(false);
const page = ref(1);
const pageSize = ref(Number(lsGet("anima_token_page_size") || "") || 30);
const detailTotal = ref(0);

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

// token 数值友好化：K/M/B（千/百万/十亿）token 计量单位，避免长数字撑爆布局
function fmtToken(v: number | null | undefined): string {
  if (v == null) return "0";
  const n = Number(v);
  const abs = Math.abs(n);
  if (abs >= 1e9) return trimZero((n / 1e9).toFixed(2)) + "B";
  if (abs >= 1e6) return trimZero((n / 1e6).toFixed(2)) + "M";
  if (abs >= 1e3) return trimZero((n / 1e3).toFixed(1)) + "K";
  return n.toLocaleString();
}
function trimZero(s: string): string {
  return s.replace(/\.?0+$/, "");
}

const SCOPE_DAYS: Record<string, string> = { today: "1", yesterday: "2", "1": "1", "3": "3", "7": "7", "30": "30", "90": "90", all: "0" };

async function load() {
  loading.value = true;
  try {
    const days = SCOPE_DAYS[scope.value] || "30";
    const data = await apiGet("token/summary", {
      days,
      scope: scope.value,
      merge: merge.value ? 1 : 0,
      page: page.value,
      page_size: pageSize.value,
    });
    summary.value = data && data.summary ? data.summary : null;
    scenes.value = (data && Array.isArray(data.scenes)) ? data.scenes : [];
    models.value = (data && Array.isArray(data.models)) ? data.models : [];
    users.value = (data && Array.isArray(data.users)) ? data.users : [];
    detail.value = (data && Array.isArray(data.detail)) ? data.detail : [];
    detailTotal.value = (data && typeof data.detail_total === "number") ? data.detail_total : detail.value.length;
    daily.value = (data && Array.isArray(data.daily)) ? data.daily : [];
    hourly.value = (data && Array.isArray(data.hourly)) ? data.hourly : [];
  } catch (e: any) {
    message.error(e.message || "读取 token 统计失败");
  } finally {
    loading.value = false;
  }
}

// 切换时间范围 / 合并插件记录时，明细范围变化，页码重置到第 1 页
function onScopeChange() {
  page.value = 1;
  load();
}

function onPage(p: number) {
  page.value = p;
  load();
}
function onPageSize(s: number) {
  pageSize.value = s;
  lsSet("anima_token_page_size", String(s));
  page.value = 1;
  load();
}

const trendData = computed(() => {
  const useHourly = scope.value === "today" || scope.value === "yesterday" || scope.value === "1";
  const buckets = useHourly ? hourly.value : daily.value;
  if (!buckets || !buckets.length) return [];
  return buckets.map((b) => ({
    x: fmtBucketLabel(b),
    y: Number(b.total ?? b.tokens ?? b.total_tokens ?? 0),
  }));
});

// 每日趋势用后端 day_bucket（YYYY-MM-DD）；小时趋势 hour 已是 "HH:00" 直接使用；
// 若 hour 为纯数字则补零格式化为 HH:00，避免重复拼出秒数
function fmtBucketLabel(b: any): string {
  if (b.day_bucket) return String(b.day_bucket);
  if (b.hour != null) {
    const h = String(b.hour);
    return h.includes(":") ? h : `${h.padStart(2, "0")}:00`;
  }
  return String(b.label || b.bucket || "");
}

function makeSceneColumns(): DataTableColumns {
  return [
    { title: "场景", key: "scene", width: 160, render: (row) => row.scene || "-" },
    { title: "非缓存输入", key: "input", width: 110, render: (row) => fmtToken(row.input_other) },
    { title: "缓存命中", key: "cached_input", width: 100, render: (row) => fmtToken(row.input_cached) },
    { title: "输出", key: "output", width: 100, render: (row) => fmtToken(row.output) },
    { title: "合计", key: "total", width: 100, render: (row) => fmtToken(row.total) },
    { title: "调用次数", key: "call_count", width: 90, render: (row) => num(row.call_count) },
  ];
}
const sceneColumns = makeSceneColumns();

function makeModelColumns(): DataTableColumns {
  return [
    { title: "模型", key: "model", width: 240, ellipsis: { tooltip: true }, render: (row) => row.model || "-" },
    { title: "非缓存输入", key: "input", width: 110, render: (row) => fmtToken(row.input_other) },
    { title: "缓存命中", key: "cached_input", width: 100, render: (row) => fmtToken(row.input_cached) },
    { title: "输出", key: "output", width: 100, render: (row) => fmtToken(row.output) },
    { title: "合计", key: "total", width: 100, render: (row) => fmtToken(row.total) },
    { title: "调用次数", key: "call_count", width: 90, render: (row) => num(row.call_count) },
  ];
}
const modelColumns = makeModelColumns();

function makeUserColumns(): DataTableColumns {
  return [
    { title: "用户 / ID", key: "name", width: 180, ellipsis: { tooltip: true }, render: (row) => `${row.user_name || ""}${row.user_id ? ` (${row.user_id})` : ""}` || "-" },
    { title: "非缓存输入", key: "input", width: 110, render: (row) => fmtToken(row.input_other) },
    { title: "缓存命中", key: "cached_input", width: 100, render: (row) => fmtToken(row.input_cached) },
    { title: "输出", key: "output", width: 100, render: (row) => fmtToken(row.output) },
    { title: "合计", key: "total", width: 100, render: (row) => fmtToken(row.total) },
    { title: "调用次数", key: "call_count", width: 90, render: (row) => num(row.call_count) },
  ];
}
const userColumns = makeUserColumns();

function makeDetailColumns(): DataTableColumns {
  return [
    { title: "时间", key: "hour_bucket", width: 160, render: (row) => row.hour_bucket || row.day_bucket || fmtDateTime(row.created_at) },
    { title: "用户ID", key: "user_id", width: 140, ellipsis: { tooltip: true }, render: (row) => row.user_id || "-" },
    { title: "场景", key: "scene", width: 140, render: (row) => row.scene || "-" },
    { title: "模型", key: "model", width: 200, ellipsis: { tooltip: true }, render: (row) => row.model || "-" },
    { title: "非缓存输入", key: "input_other", width: 100, render: (row) => fmtToken(row.input_other) },
    { title: "缓存命中", key: "input_cached", width: 90, render: (row) => fmtToken(row.input_cached) },
    { title: "输出", key: "output", width: 90, render: (row) => fmtToken(row.output) },
    { title: "合计", key: "total", width: 90, render: (row) => fmtToken(row.total) },
    { title: "调用次数", key: "call_count", width: 80, render: (row) => num(row.call_count) },
  ];
}
const detailColumns = makeDetailColumns();

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
/* 表格外层容器：窄屏横向滚动，防止超出界面 */
.table-scroll-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.table-scroll-wrap :deep(.n-data-table) { min-width: 640px; }
/* 移动端面板内：日期单选可换行、勾选与单选分段 */
.scope-mobile-block { display: flex; flex-direction: column; gap: 10px; width: 100%; }
.scope-radios { display: flex; flex-wrap: wrap; gap: 4px; }
.scope-radios :deep(.n-radio-group) { flex-wrap: wrap; gap: 4px; }
.scope-radios :deep(.n-radio-button) { flex: 0 0 auto; }

@media (max-width: 768px) {
  .token-view { padding: 0; }
  .view-head { flex-direction: column; align-items: stretch; gap: 10px; }
  .view-actions { flex-wrap: wrap; }
  .view-actions :deep(.n-button) { flex: 1 1 auto; }
  .scope-toolbar { flex-direction: column; align-items: stretch; gap: 10px; }
  .token-cards { grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); }
}
</style>
