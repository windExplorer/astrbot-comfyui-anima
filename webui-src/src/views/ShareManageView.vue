<template>
  <div class="share-manage-view">
    <div class="view-head">
      <div>
        <h2>分享管理</h2>
        <p>查看所有 /萌绘 临时分享链接（含首次访问绑定的客户端 IP），可作废无效链接。</p>
      </div>
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="view-actions">
          <n-button :loading="loading" @click="load">刷新</n-button>
        </div>
      </Teleport>
    </div>

    <div class="sm-scroll">
      <div class="sm-summary" v-if="tokens.length">
        <span class="sm-total">共 {{ tokens.length }} 条</span>
        <span class="sm-bind" :class="boundCount === tokens.length ? 'ok' : 'warn'">
          已绑定 IP：{{ boundCount }}/{{ tokens.length }}
        </span>
      </div>

      <n-spin :show="loading">
        <div v-if="!loading && tokens.length === 0" class="empty">暂无分享链接记录。用户发送 /萌绘 生成链接后这里会显示。</div>
        <div v-else class="table-scroll-wrap">
          <n-data-table :columns="columns" :data="tokens" :bordered="false" size="small" :scroll-x="860" />
        </div>
      </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useMessage, useDialog, NButton, NDataTable, NSpin, NTag, type DataTableColumns } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import { fmtDateTime } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import { useDevice } from "@/composables/useDevice";

const message = useMessage();
const dialog = useDialog();
const { isMobile } = useDevice();
const loading = ref(false);
const tokens = ref<any[]>([]);

const boundCount = computed(() => tokens.value.filter((t) => t.bound_ip).length);

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
      render: (row) =>
        h(
          NButton,
          { size: "tiny", quaternary: true, type: "error", onClick: () => onInvalidate(row) },
          { default: () => "作废" }
        ),
    },
  ];
}
const columns = makeColumns();

async function load() {
  loading.value = true;
  try {
    const data = await apiGet("share/tokens", { limit: 500 });
    tokens.value = (data && Array.isArray(data.tokens)) ? data.tokens : [];
  } catch (e: any) {
    message.error(e.message || "读取分享链接失败");
  } finally {
    loading.value = false;
  }
}

function onInvalidate(row: any) {
  dialog.warning({
    title: "作废分享链接",
    content: `确定作废 ${row.user_name || row.user_id} 的分享链接？作废后该链接立即失效，用户需重新 /萌绘 获取。`,
    positiveText: "作废",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("share/token/invalidate", { token: row.token });
        tokens.value = tokens.value.filter((t) => t.token !== row.token);
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
.sm-summary { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; font-size: 13px; }
.sm-total { color: var(--text-sub); }
.sm-bind { font-weight: 600; }
.sm-bind.ok { color: #2e9e5b; }
.sm-bind.warn { color: #e6a23c; }
.table-scroll-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.empty { color: var(--text-sub); text-align: center; padding: 50px 0; }

@media (max-width: 768px) {
  .share-manage-view { padding: 0; }
  .view-head { flex-direction: column; align-items: stretch; gap: 10px; }
  .view-actions { flex-wrap: wrap; }
  .view-actions :deep(.n-button) { flex: 1 1 auto; }
}
</style>
