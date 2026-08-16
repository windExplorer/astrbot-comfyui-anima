<template>
  <div class="quota-view">
    <div class="view-head">
      <div>
        <h2>生图限额</h2>
        <p>查看与配置每个用户的生图次数限制（总次数 / 每小时次数）。-1 表示不限制。</p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="load">刷新</n-button>
        <n-button type="error" ghost @click="resetAll">重置全部次数</n-button>
      </div>
    </div>

    <div class="panel">
      <div class="panel-title"><h3>全局配置</h3><n-button size="tiny" @click="saveGlobal">保存全局</n-button></div>
      <div class="global-form">
        <n-checkbox v-model:checked="global.enabled" size="small">启用生图次数限制</n-checkbox>
        <n-checkbox v-model:checked="global.admin_exempt" size="small">管理员不受限制</n-checkbox>
        <div class="num-fields">
          <n-form-item label="总次数上限" :show-feedback="false"><n-input-number v-model:value="global.max_total" size="small" style="width:140px" :min="-1" /></n-form-item>
          <n-form-item label="每小时上限" :show-feedback="false"><n-input-number v-model:value="global.max_hour" size="small" style="width:140px" :min="-1" /></n-form-item>
          <n-form-item label="每天上限" :show-feedback="false"><n-input-number v-model:value="global.max_day" size="small" style="width:140px" :min="-1" /></n-form-item>
        </div>
      </div>
    </div>

    <div class="panel panel-table">
      <div class="panel-title"><h3>用户生图用量与配置</h3><span class="count">{{ users.length }} 个用户</span></div>
      <div class="table-wrap">
        <n-data-table
          :columns="columns"
          :data="users"
          :bordered="false"
          :loading="loading"
          flex-height
          :scroll-x="900"
          :row-key="(row: any) => row.user_id"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { h, onMounted, reactive, ref } from "vue";
import { useMessage, useDialog, NButton, NDataTable, NCheckbox, NFormItem, NInputNumber, NInput, NSpace, NTag, type DataTableColumns } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import { useRefresh } from "@/composables/useRefresh";

const message = useMessage();
const dialog = useDialog();
const loading = ref(false);
const global = reactive({ enabled: false, max_total: -1, max_hour: -1, max_day: -1, admin_exempt: true });
const users = ref<any[]>([]);

async function load() {
  loading.value = true;
  try {
    const data = await apiGet("quota/users");
    const g = data && data.global ? data.global : {};
    global.enabled = !!g.enabled;
    global.max_total = g.max_total ?? -1;
    global.max_hour = g.max_hour ?? -1;
    global.max_day = g.max_day ?? -1;
    global.admin_exempt = g.admin_exempt !== false;
    users.value = (data && Array.isArray(data.users)) ? data.users : [];
  } catch (e: any) {
    message.error(e.message || "读取限额数据失败");
  } finally {
    loading.value = false;
  }
}

function fmtLimit(v: number): string {
  return v == null || v < 0 ? "不限" : String(v);
}

const columns: DataTableColumns = [
  { title: "用户名", key: "user_name", width: 130, render: (row) => row.user_name || row.user_id || "-" },
  { title: "QQ号", key: "user_id", width: 110, render: (row) => row.user_id || "-" },
  { title: "总生图数", key: "total_used", width: 100, render: (row) => row.total_used ?? 0 },
  { title: "当前小时", key: "hour_used", width: 90, render: (row) => row.hour_used ?? 0 },
  { title: "当天", key: "day_used", width: 80, render: (row) => row.day_used ?? 0 },
  { title: "总上限", key: "max_total", width: 100, render: (row) => fmtLimit(row.max_total) },
  { title: "每时上限", key: "max_hour", width: 100, render: (row) => fmtLimit(row.max_hour) },
  { title: "每天上限", key: "max_day", width: 100, render: (row) => fmtLimit(row.max_day) },
  { title: "操作", key: "actions", width: 220, render: (row) => h(NSpace, {}, {
    default: () => [
      h(NButton, { size: "tiny", onClick: () => editUser(row) }, { default: () => "配置" }),
      h(NButton, { size: "tiny", onClick: () => resetUser(row) }, { default: () => "重置" }),
    ],
  })},
];

function editUser(row: any) {
  // None/null（未单独配置）统一按 -1 展示（-1 表示不限制）
  const toNum = (v: any) => (v === undefined || v === null || v === "" || isNaN(Number(v)) ? -1 : Number(v));
  const local = {
    max_total: toNum(row.max_total),
    max_hour: toNum(row.max_hour),
    max_day: toNum(row.max_day),
  };
  // 用 default-value（非受控）保证弹窗每次打开都能正确展示该用户的限额初始值
  dialog.info({
    title: `配置「${row.user_name || row.user_id}」的限额`,
    content: () => h("div", { style: "display:flex;flex-direction:column;gap:10px" }, [
      h("div", {}, [
        "总次数上限（-1 不限）：",
        h(NInputNumber, { size: "small", style: "width:140px", min: -1, "default-value": local.max_total, "onUpdate:value": (v: number | null) => (local.max_total = v ?? -1) }),
      ]),
      h("div", {}, [
        "每小时上限（-1 不限）：",
        h(NInputNumber, { size: "small", style: "width:140px", min: -1, "default-value": local.max_hour, "onUpdate:value": (v: number | null) => (local.max_hour = v ?? -1) }),
      ]),
      h("div", {}, [
        "每天上限（-1 不限）：",
        h(NInputNumber, { size: "small", style: "width:140px", min: -1, "default-value": local.max_day, "onUpdate:value": (v: number | null) => (local.max_day = v ?? -1) }),
      ]),
    ]),
    positiveText: "保存",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("quota/config", { user_id: row.user_id, max_total: local.max_total, max_hour: local.max_hour, max_day: local.max_day });
        message.success("已保存");
        load();
      } catch (e: any) {
        message.error(e.message || "保存失败");
      }
    },
  });
}

function resetUser(row: any) {
  dialog.warning({
    title: "重置次数",
    content: `确定要重置用户「${row.user_name || row.user_id}」的生图次数吗？`,
    positiveText: "重置",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("quota/reset", { user_id: row.user_id });
        message.success("已重置");
        load();
      } catch (e: any) {
        message.error(e.message || "重置失败");
      }
    },
  });
}

function resetAll() {
  dialog.warning({
    title: "重置全部次数",
    content: "确定要重置所有用户的生图次数吗？",
    positiveText: "重置全部",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        const d = await apiPost("quota/reset", { all: true });
        message.success(`已重置 ${d.count || 0} 个用户`);
        load();
      } catch (e: any) {
        message.error(e.message || "重置失败");
      }
    },
  });
}

async function saveGlobal() {
  try {
    await apiPost("quota/save_global", { ...global });
    message.success("全局配置已保存");
  } catch (e: any) {
    message.error(e.message || "保存失败");
  }
}

useRefresh(load);
onMounted(load);
</script>

<style scoped>
.quota-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
  max-width: 1200px;
}
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; flex: 0 0 auto; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.view-actions { display: flex; gap: 8px; }
.panel { background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; margin-bottom: 16px; flex: 0 0 auto; }
.panel-table { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
.panel-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex: 0 0 auto; }
.panel-title h3 { margin: 0; }
.count { color: var(--text-sub); font-size: 12px; }
.global-form { display: flex; flex-direction: column; gap: 12px; }
.num-fields { display: flex; gap: 24px; flex-wrap: wrap; }
/* 表格区：占满剩余空间，flex-height 使表格内部滚动 */
.table-wrap { flex: 1 1 auto; min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
.table-wrap :deep(.n-data-table) { flex: 1 1 auto; min-height: 0; }
</style>
