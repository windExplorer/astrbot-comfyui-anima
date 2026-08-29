<template>
  <div class="logs-view">
    <div class="view-head">
      <div>
        <h2>出图记录 / 日志</h2>
        <p>出图记录：谁、发了什么消息、出的图、尺寸/大小/耗时/成败。运行日志：插件运行流水。</p>
      </div>
      <!-- 刷新 + 切换：PC 端留在原位；移动端收进底部操作弹窗面板 -->
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="mobile-actions">
          <n-button size="small" :loading="loading" @click="refresh">刷新</n-button>
          <div class="mobile-tabs">
            <button class="tab-btn" :class="{ active: activeTab === 'records' }" @click="activeTab = 'records'">出图记录</button>
            <button class="tab-btn" :class="{ active: activeTab === 'oplog' }" @click="switchOplog">操作日志</button>
            <button class="tab-btn" :class="{ active: activeTab === 'runlog' }" @click="activeTab = 'runlog'">运行日志</button>
          </div>
        </div>
      </Teleport>
    </div>

    <!-- 出图记录 -->
    <div v-show="activeTab === 'records'" class="pane-inner">
      <div class="toolbar">
        <n-input v-model:value="recSearch" size="small" placeholder="搜索用户 / 消息 / 提示词…" style="width:280px" clearable @keyup.enter="loadRecords(1)" />
        <n-checkbox v-model:checked="recFailedOnly" size="small" @update:checked="loadRecords(1)">仅看失败</n-checkbox>
        <n-button size="small" @click="loadRecords(1)">搜索</n-button>
        <span class="count">{{ recTotal ? recTotal + " 条" : recRows.length + " 条" }}</span>
      </div>

      <div class="table-scroll">
        <n-data-table
          :columns="recColumns"
          :data="recRows"
          :bordered="false"
          :loading="recLoading"
          remote
          flex-height
          :scroll-x="1100"
          :row-key="(row: any) => row.sha || row.id"
        />
      </div>
      <Pager
        v-if="recTotal > recPageSize"
        :page="recPage"
        :page-size="recPageSize"
        :total="recTotal"
        @update:page="loadRecords"
        @update:page-size="onRecPageSize"
      />
    </div>

    <!-- 操作日志 -->
    <div v-show="activeTab === 'oplog'" class="pane-inner">
      <div class="toolbar">
        <n-select
          v-model:value="opEvent"
          size="small"
          style="width:180px"
          :options="opEventOptions"
          clearable
          placeholder="事件类型"
          @update:value="loadOplog(1)"
        />
        <n-input v-model:value="opSearch" size="small" placeholder="搜索用户 / 摘要 / sha…" style="width:280px" clearable @keyup.enter="loadOplog(1)" />
        <n-button size="small" @click="loadOplog(1)">搜索</n-button>
        <span class="count">{{ opTotal ? opTotal + " 条" : opRows.length + " 条" }}</span>
      </div>
      <div class="table-scroll">
        <n-data-table
          :columns="opColumns"
          :data="opRows"
          :bordered="false"
          :loading="opLoading"
          remote
          flex-height
          :scroll-x="900"
          :row-key="(row: any) => row.id"
        />
      </div>
      <Pager
        v-if="opTotal > opPageSize"
        :page="opPage"
        :page-size="opPageSize"
        :total="opTotal"
        @update:page="loadOplog"
        @update:page-size="onOpPageSize"
      />
    </div>

    <!-- 运行日志 -->
    <div v-show="activeTab === 'runlog'" class="pane-inner">
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
    </div>

    <!-- 大图查看器 -->
    <ImageViewer
      v-model:show="viewerShow"
      :sha="viewerSha"
      :item="viewerItem"
      :ref-sha="viewerRefSha"
      :images="viewerImages"
      :index="viewerIndex"
      @star="onViewerStar"
      @delete="onViewerDelete"
      @nav="onViewerNav"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from "vue";
import { NButton, NDataTable, NInput, NCheckbox, NSelect, NTag, useMessage, type DataTableColumns } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import { fetchThumb } from "@/api/bridge";
import { lsGet, lsSet } from "@/api/storage";
import { fmtBytes, fmtDuration, fmtDateTime, truncate } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import { useDevice } from "@/composables/useDevice";
import ImageViewer from "@/components/ImageViewer.vue";
import Pager from "@/components/Pager.vue";

const message = useMessage();
const { isMobile } = useDevice();
const activeTab = ref("records");
const loading = ref(false);

// 出图记录
const recRows = ref<any[]>([]);
const recTotal = ref(0);
const recLoading = ref(false);
const recSearch = ref("");
const recFailedOnly = ref(false);
const recPage = ref(1);
let recPageSize = Number(lsGet("anima_logs_page_size") || "") || 10;
// 用响应式对象缓存缩略图：fetchThumb 完成后更新会触发表格重新渲染，图片才显示。
const recThumbCache = reactive<Record<string, string>>({});

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
    // 预取本页缩略图
    rows.forEach((r) => loadThumb(r));
  } catch (e: any) {
    message.error(e.message || "读取出图记录失败");
  } finally {
    recLoading.value = false;
  }
}

// 缩略图：响应式缓存 + 懒加载，拉取成功自动触发渲染
async function loadThumb(row: any): Promise<void> {
  const sha = row?.sha || row?.sha256;
  if (!sha || recThumbCache[sha]) return;
  try {
    const url = await fetchThumb(sha, 200);
    if (url) recThumbCache[sha] = url;
  } catch (e) {
    // 忽略单张缩略图失败
  }
}
function thumbFor(row: any): string {
  const sha = row?.sha || row?.sha256;
  return sha ? (recThumbCache[sha] || "") : "";
}

// NSFW 模糊（与图库封面一致）：NSFW 图 && 全局开关开 && 单图未强制取消
const nsfwBlurGlobal = ref(true);
try {
  const v = lsGet("anima_gal_nsfw_blur");
  if (v != null) nsfwBlurGlobal.value = v === "1";
} catch { /* ignore */ }
function isNsfwBlurred(img: any): boolean {
  if (!img || !img.nsfw) return false;
  if (!nsfwBlurGlobal.value) return false;
  if (img.nsfw_blur === 0) return false;
  if (img.nsfw_blur === 1) return true;
  return true;
}

// 大图查看器：点击缩略图打开，支持图生图（参考图 + 结果图并排）；支持左右箭头导航
const viewerShow = ref(false);
const viewerSha = ref("");
const viewerItem = ref<any>(null);
const viewerRefSha = ref("");
const viewerIndex = ref(0);
const viewerImages = computed(() =>
  (recRows.value || [])
    .filter((r: any) => r.sha || r.sha256)
    .map((r: any) => ({ sha: r.sha || r.sha256, item: r, refSha: r.ref_sha256 || "" }))
);

function openViewer(row: any) {
  const sha = row.sha || row.sha256;
  if (!sha) return;
  const idx = (viewerImages.value || []).findIndex((it: any) => it.sha === sha);
  viewerIndex.value = idx >= 0 ? idx : 0;
  viewerSha.value = sha;
  viewerItem.value = { ...row };
  viewerRefSha.value = row.ref_sha256 || "";
  viewerShow.value = true;
}

// 导航：左右切换（边界由 ImageViewer 禁用箭头 + 此处 clamp 双重保护）
function onViewerNav(delta: number) {
  const ni = viewerIndex.value + delta;
  if (ni < 0 || ni >= viewerImages.value.length) return;
  const it = viewerImages.value[ni];
  if (!it || !it.sha) return;
  viewerIndex.value = ni;
  openViewer(it.item);
}

function onViewerStar(img: any) {
  const sha = img.sha || img.sha256;
  apiPost("gallery/star", { sha, on: !img.starred })
    .then(() => {
      img.starred = !img.starred;
      message.success(img.starred ? "已收藏" : "已取消收藏");
    })
    .catch((e: any) => message.error(e.message || "操作失败"));
}

function onViewerDelete(img: any) {
  message.warning("出图记录不支持删除，请到「图库」中操作");
}

const recColumns: DataTableColumns = [
  { title: "预览", key: "thumb", width: 90, render: (row) => {
    const sha = row.sha || row.sha256;
    const isI2i = !!(row.is_img2img || row.ref_sha256);
    const blurred = isNsfwBlurred(row);
    const src = thumbFor(row);
    return h("div", { class: "rec-thumb", style: "position:relative;cursor:zoom-in;display:inline-block", onClick: () => openViewer(row) }, [
      src
        ? h("img", { src, style: `width:60px;height:60px;object-fit:cover;border-radius:6px;display:block;background:#00000010${blurred ? ";filter:blur(8px)" : ""}` })
        : h("div", { style: "width:60px;height:60px;border-radius:6px;background:#00000010;display:flex;align-items:center;justify-content:center;color:#999;font-size:11px" }, "加载…"),
      isI2i
        ? h("span", { class: "rec-i2i-badge", style: "position:absolute;left:2px;bottom:2px;background:linear-gradient(135deg,#ffb3d1,#ff8fb3);color:#fff;font-size:10px;padding:1px 6px;border-radius:8px" }, "图生图")
        : null,
      blurred
        ? h("span", { style: "position:absolute;top:2px;right:2px;font-size:11px;text-shadow:0 1px 3px rgba(0,0,0,0.6)" }, "🔞")
        : null,
    ]);
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

function onRecPageSize(s: number) {
  recPageSize = s;
  lsSet("anima_logs_page_size", String(s));
  recPage.value = 1;
  loadRecords(1);
}

// 操作日志（独立 oplog）
const opRows = ref<any[]>([]);
const opTotal = ref(0);
const opLoading = ref(false);
const opSearch = ref("");
const opEvent = ref<string | null>(null);
const opPage = ref(1);
let opPageSize = Number(lsGet("anima_oplog_page_size") || "") || 20;

const OP_EVENT_LABELS: Record<string, string> = {
  draw_success: "生图成功",
  draw_fail: "生图失败",
  gallery_dedup: "图库去重",
  gallery_new: "图库新增",
  quota_inc: "限额扣减",
  quota_reset: "限额重置",
  config_save: "配置保存",
  gallery_delete: "图库删除",
  gallery_restore: "图库恢复",
  gallery_purge: "图库彻底删除",
  gallery_star: "图库收藏",
  gallery_tags: "图库打标签",
};
const opEventOptions = Object.entries(OP_EVENT_LABELS).map(([value, label]) => ({ value, label }));

async function loadOplog(page: number) {
  opLoading.value = true;
  try {
    const data = await apiGet("oplog", {
      page,
      size: opPageSize,
      keyword: opSearch.value.trim(),
      event: opEvent.value || "",
    });
    opRows.value = data && Array.isArray(data.records) ? data.records : [];
    opTotal.value = data && data.total != null ? Number(data.total) : 0;
    opPage.value = page;
  } catch (e: any) {
    message.error(e.message || "读取操作日志失败");
  } finally {
    opLoading.value = false;
  }
}
function onOpPageSize(s: number) {
  opPageSize = s;
  lsSet("anima_oplog_page_size", String(s));
  opPage.value = 1;
  loadOplog(1);
}
function switchOplog() {
  activeTab.value = "oplog";
  loadOplog(1);
}
function opEventLabel(ev: string): string {
  return OP_EVENT_LABELS[ev] || ev;
}
function opTime(ts: number): string {
  if (!ts) return "-";
  return fmtDateTime(ts * 1000);
}
function opExtraText(extra: any): string {
  if (!extra) return "";
  try { return JSON.stringify(extra); } catch { return ""; }
}
const opColumns: DataTableColumns = [
  { title: "时间", key: "ts", width: 150, render: (row) => opTime(row.ts) },
  { title: "类型", key: "event", width: 110, render: (row) => {
    return h(NTag, { type: opTagType(row.event), size: "small" }, { default: () => opEventLabel(row.event) });
  }},
  { title: "用户", key: "user_name", width: 100, render: (row) => row.user_name || row.user_id || "-" },
  { title: "摘要", key: "summary", ellipsis: { tooltip: true } },
  { title: "详情", key: "detail", width: 220, ellipsis: { tooltip: true }, render: (row) => {
    const d = row.detail || opExtraText(row.extra);
    return truncate(d, 40);
  }},
];
function opTagType(ev: string): "success" | "warning" | "error" | "info" {
  if (ev === "draw_success" || ev === "gallery_new") return "success";
  if (ev === "quota_inc" || ev === "gallery_dedup") return "warning";
  if (ev === "draw_fail" || ev === "gallery_purge") return "error";
  return "info";
}

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
  else if (activeTab.value === "oplog") loadOplog(1);
  else loadLogs();
}

useRefresh(refresh);
onMounted(() => {
  loadRecords(1);
  loadLogs();
});
</script>

<style scoped>
.logs-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; flex: 0 0 auto; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
/* tab 切换条 */
.tab-bar { display: flex; gap: 8px; margin-bottom: 12px; flex: 0 0 auto; }
.tab-btn {
  padding: 6px 16px;
  border: 1px solid var(--border-color);
  background: var(--bg-panel);
  color: var(--text-sub);
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.15s;
}
.tab-btn:hover { color: var(--accent); border-color: var(--accent); }
.tab-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

/* 内容面板：flex 填满剩余空间，内部滚动；工具栏/分页器固定 */
.pane-inner { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.toolbar { display: flex; gap: 8px; align-items: center; margin: 0 0 12px; flex-wrap: wrap; flex: 0 0 auto; }
.count { color: var(--text-sub); font-size: 12px; }
/* 表格区域：flex 占满剩余空间，flex-height 使表格内部滚动、分页器固定底部可见 */
.table-scroll { flex: 1 1 auto; min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
.table-scroll :deep(.n-data-table) { flex: 1 1 auto; min-height: 0; }
.log-viewer {
  background: var(--bg-body);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 8px;
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
}
.log-line { padding: 2px 8px; border-radius: 3px; }
.log-line pre { margin: 0; white-space: pre-wrap; word-break: break-all; }
.log-line.err pre { color: #ff453a; }
.log-line.warn pre { color: #ff9f0a; }
.empty { color: var(--text-sub); text-align: center; padding: 30px; }

@media (max-width: 768px) {
  .logs-view { padding: 0; }
  .view-head { flex-direction: column; align-items: stretch; gap: 10px; }
  .view-actions { flex-wrap: wrap; }
  .view-actions :deep(.n-button) { flex: 1 1 auto; }
  /* 工具栏固定宽度 input 在窄屏改为全宽换行；内联 width 用 !important 覆盖 */
  .toolbar { flex-wrap: wrap; }
  .toolbar :deep(.n-input) { width: 100% !important; flex: 1 1 100%; }
  .toolbar :deep(.n-select) { width: 100% !important; flex: 1 1 100%; }
  .tab-bar :deep(.n-button) { flex: 1 1 auto; }
  /* 短屏手机：上方 tabs/工具栏堆叠会挤占空间，给表格加高兜底，一屏能看到更多条记录 */
  .table-scroll { min-height: 520px; }
}

/* 移动端操作弹窗面板内的刷新 + tab 切换 */
.mobile-actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}
.mobile-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
