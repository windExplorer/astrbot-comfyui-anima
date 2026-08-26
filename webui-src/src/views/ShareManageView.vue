<template>
  <div class="share-manage-view">
    <div class="view-head">
      <div>
        <h2>分享管理</h2>
        <p>查看所有 /萌绘 临时分享链接（含首次访问绑定的客户端 IP），可作废无效链接。</p>
      </div>
      <!-- 刷新 移动端收进底部操作弹窗面板 -->
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="view-actions">
          <n-button :loading="loading" @click="load">刷新</n-button>
        </div>
      </Teleport>
    </div>

    <div class="sm-scroll">
      <n-spin :show="loading">
        <!-- 汇总卡片 -->
        <div v-if="all.length" class="sm-cards">
          <n-card size="small" class="sm-card"><div class="card-num">{{ all.length }}</div><div class="card-label">分享链接总数</div></n-card>
          <n-card size="small" class="sm-card"><div class="card-num ok">{{ validCount }}</div><div class="card-label">有效</div></n-card>
          <n-card size="small" class="sm-card"><div class="card-num sub">{{ expiredCount }}</div><div class="card-label">已过期</div></n-card>
          <n-card size="small" class="sm-card"><div class="card-num ok">{{ boundCount }}</div><div class="card-label">已绑定 IP</div></n-card>
          <n-card size="small" class="sm-card"><div class="card-num warn">{{ unboundCount }}</div><div class="card-label">未绑定 IP</div></n-card>
        </div>

        <!-- 筛选工具条 -->
        <div class="sm-toolbar">
          <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
            <div class="sm-filter-block">
              <n-radio-group v-model:value="fStatus" size="small" @update:value="onFilterChange" class="sm-radios">
                <n-radio-button value="all">全部状态</n-radio-button>
                <n-radio-button value="valid">有效</n-radio-button>
                <n-radio-button value="expired">已过期</n-radio-button>
              </n-radio-group>
              <n-radio-group v-model:value="fBind" size="small" @update:value="onFilterChange" class="sm-radios">
                <n-radio-button value="all">IP 不限</n-radio-button>
                <n-radio-button value="bound">已绑定</n-radio-button>
                <n-radio-button value="unbound">未绑定</n-radio-button>
              </n-radio-group>
              <n-input
                v-model:value="fKeyword"
                size="small"
                placeholder="搜索 用户 / ID / IP / 令牌"
                clearable
                class="sm-search"
                @update:value="onFilterChange"
              />
            </div>
          </Teleport>
        </div>

        <!-- 链接列表 -->
        <div class="panel">
          <div class="panel-title">
            <h3>分享链接列表</h3>
            <span class="count">{{ filtered.length }} 条{{ filtered.length !== all.length ? ` / 共 ${all.length}` : "" }}</span>
          </div>
          <div v-if="!all.length" class="empty">暂无分享链接记录。用户发送 /萌绘 生成链接后这里会显示。</div>
          <div v-else-if="!filtered.length" class="empty">没有符合筛选条件的链接。</div>
          <template v-else>
            <div class="table-scroll-wrap">
              <n-data-table :columns="columns" :data="pageRows" :bordered="false" size="small" :scroll-x="900" />
            </div>
            <Pager :page="page" :page-size="pageSize" :total="filtered.length" @update:page="onPage" @update:page-size="onPageSize" />
          </template>
        </div>
      </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import {
  useMessage, useDialog, NButton, NDataTable, NSpin, NTag, NCard,
  NRadioGroup, NRadioButton, NInput, type DataTableColumns,
} from "naive-ui";
import Pager from "@/components/Pager.vue";
import { apiGet, apiPost } from "@/api/bridge";
import { lsGet, lsSet } from "@/api/storage";
import { fmtDateTime } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import { useDevice } from "@/composables/useDevice";

const message = useMessage();
const dialog = useDialog();
const { isMobile } = useDevice();
const loading = ref(false);
const all = ref<any[]>([]);

// 分页（客户端切片；分享链接数量有限，一次拉取后分页，保证汇总卡统计真实）
const page = ref(1);
const pageSize = ref(Number(lsGet("anima_share_page_size") || "") || 20);

// 筛选
const fStatus = ref<"all" | "valid" | "expired">("all");
const fBind = ref<"all" | "bound" | "unbound">("all");
const fKeyword = ref("");

const validCount = computed(() => all.value.filter((t) => statusOf(t) === "valid").length);
const expiredCount = computed(() => all.value.length - validCount.value);
const boundCount = computed(() => all.value.filter((t) => t.bound_ip).length);
const unboundCount = computed(() => all.value.length - boundCount.value);

const filtered = computed(() => {
  const kw = fKeyword.value.trim().toLowerCase();
  return all.value.filter((t) => {
    if (fStatus.value === "valid" && statusOf(t) !== "valid") return false;
    if (fStatus.value === "expired" && statusOf(t) !== "expired") return false;
    if (fBind.value === "bound" && !t.bound_ip) return false;
    if (fBind.value === "unbound" && t.bound_ip) return false;
    if (kw) {
      const hay = `${t.user_name || ""} ${t.user_id || ""} ${t.bound_ip || ""} ${t.token || ""} ${t.token_short || ""}`.toLowerCase();
      if (!hay.includes(kw)) return false;
    }
    return true;
  });
});

// 分页作用于筛选后的结果
const pageRows = computed(() => {
  const start = (page.value - 1) * pageSize.value;
  return filtered.value.slice(start, start + pageSize.value);
});

function statusOf(row: any): "valid" | "expired" {
  const now = Date.now() / 1000;
  return (row.expire_at || 0) > now ? "valid" : "expired";
}

function makeColumns(): DataTableColumns {
  return [
    {
      title: "用户", key: "user", width: 170, ellipsis: { tooltip: true },
      render: (row) => `${row.user_name || ""}${row.user_id ? ` (${row.user_id})` : ""}` || "-",
    },
    {
      title: "令牌", key: "token_short", width: 140, ellipsis: { tooltip: true },
      render: (row) => row.token_short || row.token || "-",
    },
    {
      title: "创建时间", key: "created_at", width: 160,
      render: (row) => fmtDateTime(row.created_at),
    },
    {
      title: "过期时间", key: "expire_at", width: 160,
      render: (row) => fmtDateTime(row.expire_at),
    },
    {
      title: "绑定 IP", key: "bound_ip", width: 130,
      render: (row) =>
        row.bound_ip
          ? h(NTag, { size: "small", type: "success", bordered: false }, { default: () => row.bound_ip })
          : h(NTag, { size: "small", type: "error", bordered: false }, { default: () => "未绑定" }),
    },
    {
      title: "状态", key: "status", width: 90,
      render: (row) =>
        statusOf(row) === "valid"
          ? h(NTag, { size: "small", type: "success", bordered: false }, { default: () => "有效" })
          : h(NTag, { size: "small", type: "default", bordered: false }, { default: () => "已过期" }),
    },
    {
      title: "操作", key: "actions", width: 90,
      render: (row) => {
        const expired = statusOf(row) === "expired";
        return h(
          NButton,
          {
            size: "tiny",
            quaternary: true,
            type: "error",
            disabled: expired,
            title: expired ? "已过期，无需作废" : undefined,
            onClick: () => onInvalidate(row),
          },
          { default: () => (expired ? "已过期" : "作废") }
        );
      },
    },
  ];
}
const columns = makeColumns();

async function load() {
  loading.value = true;
  try {
    const data = await apiGet("share/tokens", { limit: 500 });
    all.value = (data && Array.isArray(data.tokens)) ? data.tokens : [];
    // 作废后总数变小，若当前页越界则回拉
    const maxPage = Math.max(1, Math.ceil(filtered.value.length / pageSize.value));
    if (page.value > maxPage) page.value = maxPage;
  } catch (e: any) {
    message.error(e.message || "读取分享链接失败");
  } finally {
    loading.value = false;
  }
}

// 任一筛选变化 → 回到第 1 页（总数变了）
function onFilterChange() {
  page.value = 1;
}

function onPage(p: number) {
  page.value = p;
}
function onPageSize(s: number) {
  pageSize.value = s;
  lsSet("anima_share_page_size", String(s));
  page.value = 1;
}

function onInvalidate(row: any) {
  // 过期链接不可作废（后端作废只是把 expire_at 置 0，对已过期无意义）
  if (statusOf(row) === "expired") return;
  dialog.warning({
    title: "作废分享链接",
    content: `确定作废 ${row.user_name || row.user_id} 的分享链接？作废后该链接立即失效，用户需重新 /萌绘 获取。`,
    positiveText: "作废",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("share/token/invalidate", { token: row.token });
        all.value = all.value.filter((t) => t.token !== row.token);
        message.success("已作废");
      } catch (e: any) {
        message.error(e.message || "作废失败");
      }
    },
  });
}

useRefresh(load);
onMounted(load);
</script>

<style scoped>
.share-manage-view { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; flex: 0 0 auto; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.view-actions { display: flex; gap: 8px; }
.sm-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }

/* 汇总卡片：与 Token 用量页一致 */
.sm-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }
.sm-card { text-align: center; }
.card-num { font-size: 22px; font-weight: 700; color: var(--accent); }
.card-num.ok { color: #2e9e5b; }
.card-num.warn { color: #e6a23c; }
.card-num.sub { color: var(--text-sub); }
.card-label { font-size: 12px; color: var(--text-sub); margin-top: 4px; }

/* 筛选工具条 */
.sm-toolbar { display: flex; gap: 16px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
.sm-filter-block { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.sm-radios { display: flex; flex-wrap: wrap; gap: 4px; }
.sm-search { width: 220px; }

/* 分区块面板：与 Token 用量页一致 */
.panel { background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.panel-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.panel-title h3 { margin: 0; }
.count { color: var(--text-sub); font-size: 12px; }
.empty { color: var(--text-sub); text-align: center; padding: 30px; }
.table-scroll-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.table-scroll-wrap :deep(.n-data-table) { min-width: 640px; }

@media (max-width: 768px) {
  .share-manage-view { padding: 0; }
  .view-head { flex-direction: column; align-items: stretch; gap: 10px; }
  .view-actions { flex-wrap: wrap; }
  .view-actions :deep(.n-button) { flex: 1 1 auto; }
  .sm-cards { grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); }
  .sm-toolbar { flex-direction: column; align-items: stretch; gap: 10px; }
  .sm-filter-block { flex-direction: column; align-items: stretch; gap: 10px; }
  .sm-search { width: 100%; }
}
</style>
