<template>
  <div class="logs-view">
    <div class="view-head">
      <div>
        <h2>出图记录 / 日志</h2>
        <p>出图记录：谁、发了什么消息、出的图、尺寸/大小/耗时/成败。运行日志：插件运行流水。</p>
      </div>
      <n-button :loading="loading" @click="refresh">刷新</n-button>
    </div>

    <n-tabs v-model:value="activeTab" type="line">
      <!-- 出图记录 -->
      <n-tab-pane name="records" tab="出图记录">
        <div class="toolbar">
          <n-input v-model:value="recSearch" size="small" placeholder="搜索用户 / 消息 / 提示词…" style="width:280px" clearable @keyup.enter="loadRecords(1)" />
          <n-checkbox v-model:checked="recFailedOnly" size="small" @update:checked="loadRecords(1)">仅看失败</n-checkbox>
          <n-button size="small" @click="loadRecords(1)">搜索</n-button>
          <span class="count">{{ recTotal ? recTotal + " 条" : recRows.length + " 条" }}</span>
        </div>

        <n-data-table
          :columns="recColumns"
          :data="recRows"
          :pagination="recPagination"
          :bordered="false"
          :loading="recLoading"
          remote
          :max-height="recTableHeight"
          :scroll-x="1100"
          :row-key="(row: any) => row.sha || row.id"
        />
      </n-tab-pane>

      <!-- 运行日志 -->
      <n-tab-pane name="runlog" tab="运行日志">
        <div class="toolbar">
          <n-select
            v-model:value="logLevel"
            size="small"
            style="width:140px"
            :options="logLevelOptions"
            @update:value="filterLogs"
          />
          <n-input v-model:value="logSearch" size="small" placeholder="搜索日志关键词…" style="width:280px" clearable @update:value="filterLogs" />
          <span class="count">{{ filteredLogs.length }} 条</span>
        </div>
        <div class="log-viewer">
          <div v-for="(line, i) in filteredLogs" :key="i" class="log-line" :class="logLineClass(line)">
            <pre>{{ line }}</pre>
          </div>
          <div v-if="!filteredLogs.length" class="empty">暂无日志</div>
        </div>
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from "vue";
import { NButton, NDataTable, NInput, NCheckbox, NSelect, NTabs, NTabPane, NTag, NImage, useMessage, type DataTableColumns } from "naive-ui";
import { apiGet } from "@/api/bridge";
import { fetchThumb } from "@/api/bridge";
import { fmtBytes, fmtDuration, fmtDateTime, truncate } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";

const message = useMessage();
const activeTab = ref("records");
const loading = ref(false);

// 表格可视高度：让出图记录表格内部滚动，标题、工具栏、分页器固定可见。
// 用固定视口高度减去标题栏/工具栏/分页器的估算高度，保证不撑破页面产生整体滚动。
const recTableHeight = computed(() => `calc(100vh - ${activeTab.value === "records" ? 300 : 0}px)`);

// 出图记录
const recRows = ref<any[]>([]);
const recTotal = ref(0);
const recLoading = ref(false);
const recSearch = ref("");
const recFailedOnly = ref(false);
const recPage = ref(1);
const recPageSize = 40;
const recPageCache = new Map<string, string>();

async function loadRecords(page: number) {
  recLoading.value = true;
  try {
    const data = await apiGet("records", {
      failed: recFailedOnly.value ? 1 : 0,
      keyword: recSearch.value.trim(),
      page,
      size: recPageSize,
    });
    const rows = Array.isArray(data) ? data : (data && Array.isArray(data.records) ? data.records : []);
    recRows.value = rows;
    recTotal.value = data && data.total != null ? Number(data.total) : 0;
    recPage.value = page;
  } catch (e: any) {
    message.error(e.message || "读取出图记录失败");
  } finally {
    recLoading.value = false;
  }
}

// 缩略图懒加载
function thumbFor(row: any): string {
  const sha = row.sha || row.sha256;
  if (!sha) return "";
  if (recPageCache.has(sha)) return recPageCache.get(sha)!;
  fetchThumb(sha, 200).then((url) => {
    if (url) recPageCache.set(sha, url);
  });
  return "";
}

const recColumns: DataTableColumns = [
  { title: "预览", key: "thumb", width: 70, render: (row) => {
    const sha = row.sha || row.sha256;
    return h(NImage, {
      width: 56,
      height: 56,
      objectFit: "cover",
      src: thumbFor(row),
      fallbackSrc: "",
      style: "border-radius:6px;background:#00000010",
      previewDisabled: false,
    });
  }},
  { title: "出图时间", key: "created_at", width: 150, render: (row) => fmtDateTime(row.created_at) },
  { title: "用户", key: "user_name", width: 100, render: (row) => row.user_name || row.user_id || "-" },
  { title: "触发消息", key: "trigger_msg", width: 180, ellipsis: { tooltip: true }, render: (row) => truncate(row.trigger_msg, 30) },
  { title: "工作流", key: "workflow", width: 120, ellipsis: { tooltip: true }, render: (row) => row.workflow || "-" },
  { title: "尺寸", key: "size", width: 90, render: (row) => (row.w && row.h) ? `${row.w}×${row.h}` : "-" },
  { title: "大小", key: "size_bytes", width: 80, render: (row) => fmtBytes(row.size_bytes) },
  { title: "耗时", key: "cost_sec", width: 70, render: (row) => fmtDuration(row.cost_sec) },
  { title: "状态", key: "status", width: 90, render: (row) => {
    const ok = row.status !== "failed" && row.status !== "error";
    return h(NTag, { type: ok ? "success" : "error", size: "small" }, { default: () => ok ? "成功" : "失败" });
  }},
  { title: "提示词", key: "prompt", ellipsis: { tooltip: true }, render: (row) => truncate(row.prompt || row.prompt_raw, 50) },
];

const recPagination = computed(() => ({
  page: recPage.value,
  pageSize: recPageSize,
  itemCount: recTotal.value,
  onChange: (page: number) => loadRecords(page),
  showSizePicker: false,
}));

// 运行日志
const logLines = ref<string[]>([]);
const logLevel = ref("all");
const logSearch = ref("");
const logLevelOptions = [
  { label: "全部级别", value: "all" },
  { label: "INFO", value: "INFO" },
  { label: "WARN", value: "WARN" },
  { label: "ERROR", value: "ERROR" },
];

async function loadLogs() {
  try {
    const data = await apiGet("logs", { n: 2000 });
    logLines.value = Array.isArray(data) ? data : (data && Array.isArray(data.lines) ? data.lines : []);
  } catch (e: any) {
    message.error(e.message || "读取日志失败");
  }
}

const filteredLogs = computed(() => {
  let lines = logLines.value;
  const kw = logSearch.value.trim().toLowerCase();
  if (logLevel.value !== "all") {
    lines = lines.filter((l) => l.includes(logLevel.value));
  }
  if (kw) {
    lines = lines.filter((l) => l.toLowerCase().includes(kw));
  }
  return lines;
});

function logLineClass(line: string): string {
  if (/\[ERROR\]|ERROR/i.test(line)) return "err";
  if (/\[WARN\]|WARN/i.test(line)) return "warn";
  return "";
}

function filterLogs() { /* computed handles it */ }

function refresh() {
  if (activeTab.value === "records") loadRecords(1);
  else loadLogs();
}

useRefresh(refresh);
onMounted(() => {
  loadRecords(1);
  loadLogs();
});
</script>

<style scoped>
.logs-view { max-width: 1280px; }
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.toolbar { display: flex; gap: 8px; align-items: center; margin: 8px 0 12px; flex-wrap: wrap; }
.count { color: var(--text-sub); font-size: 12px; }
.log-viewer {
  background: var(--bg-body);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 8px;
  height: calc(100vh - 280px);
  min-height: 200px;
  overflow: auto;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
}
.log-line { padding: 2px 8px; border-radius: 3px; }
.log-line pre { margin: 0; white-space: pre-wrap; word-break: break-all; }
.log-line.err pre { color: #ff453a; }
.log-line.warn pre { color: #ff9f0a; }
.empty { color: var(--text-sub); text-align: center; padding: 30px; }
</style>
