<template>
  <div class="story-page">
    <!-- 概览统计 -->
    <div class="stat-row" v-if="stats">
      <div class="stat-card"><div class="stat-num">{{ stats.total }}</div><div class="stat-label">总档案</div></div>
      <div class="stat-card"><div class="stat-num">{{ stats.active }}</div><div class="stat-label">进行中</div></div>
      <div class="stat-card"><div class="stat-num">{{ stats.finished }}</div><div class="stat-label">已结束</div></div>
      <div class="stat-card"><div class="stat-num">{{ stats.turns }}</div><div class="stat-label">对话轮次</div></div>
      <div class="stat-card"><div class="stat-num">{{ stats.images }}</div><div class="stat-label">关联图片</div></div>
    </div>

    <!-- 筛选栏 -->
    <n-card class="filter-card" :bordered="false">
      <div class="filter-grid">
        <n-input v-model:value="filters.keyword" placeholder="搜索标题/摘要/标签/角色/场景" clearable @keyup.enter="reload" />
        <n-input v-model:value="filters.user_id" placeholder="用户 ID" clearable @keyup.enter="reload" />
        <n-select v-model:value="filters.status" :options="statusOptions" placeholder="状态" />
        <n-input v-model:value="filters.date_from" placeholder="起始日期 YYYY-MM-DD" clearable />
        <n-input v-model:value="filters.date_to" placeholder="结束日期 YYYY-MM-DD" clearable />
        <div class="filter-actions">
          <n-button type="primary" @click="reload">查询</n-button>
          <n-button @click="resetFilters">重置</n-button>
          <n-popconfirm @positive-click="bulkDelete">
            <template #trigger>
              <n-button type="error" :disabled="!checkedRowKeys.length">批量删除 ({{ checkedRowKeys.length }})</n-button>
            </template>
            确认删除选中的 {{ checkedRowKeys.length }} 条档案？此操作不可恢复。
          </n-popconfirm>
        </div>
      </div>
    </n-card>

    <!-- 列表 -->
    <n-card class="list-card" :bordered="false">
      <n-data-table
        remote
        :columns="columns"
        :data="rows"
        :loading="loading"
        :row-key="(row: any) => row.id"
        :checked-row-keys="checkedRowKeys"
        @update:checked-row-keys="(keys: any) => (checkedRowKeys = keys)"
        :pagination="pagination"
        :row-props="rowProps"
        size="small"
      />
    </n-card>

    <!-- 详情抽屉 -->
    <n-drawer v-model:show="detailShow" :width="640" placement="right">
      <n-drawer-content :title="'剧情档案 #' + (current.id || '')" closable>
        <n-scrollbar style="max-height: calc(100vh - 64px)">
          <n-empty v-if="!current.id" description="加载中…" />

          <template v-else>
            <!-- 字段详情 -->
            <n-descriptions title="档案信息" label-placement="left" bordered :column="1" size="small">
              <n-descriptions-item label="状态">
                <n-tag :type="statusType(current.status)" size="small">{{ statusLabel(current.status) }}</n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="用户">{{ current.user_name || "—" }} <span class="sub">({{ current.user_id }})</span></n-descriptions-item>
              <n-descriptions-item label="平台">{{ current.platform || "—" }}</n-descriptions-item>
              <n-descriptions-item label="来源">{{ current.source || "—" }}</n-descriptions-item>
              <n-descriptions-item label="开始">{{ current.started_at || "—" }}</n-descriptions-item>
              <n-descriptions-item label="结束">{{ current.ended_at || "—" }}</n-descriptions-item>
              <n-descriptions-item label="对话轮次">{{ current.turn_count }}（消息 {{ current.message_count }}）</n-descriptions-item>
              <n-descriptions-item label="图片数">{{ current.image_count }}</n-descriptions-item>
              <n-descriptions-item label="评分">
                <n-rate :value="Number(current.rating) || 0" readonly />
              </n-descriptions-item>
              <n-descriptions-item label="标题">{{ current.title || "—" }}</n-descriptions-item>
              <n-descriptions-item label="情绪/基调">{{ current.mood || "—" }}</n-descriptions-item>
              <n-descriptions-item label="场景设定">{{ current.scene || "—" }}</n-descriptions-item>
              <n-descriptions-item label="角色">{{ current.characters || "—" }}</n-descriptions-item>
              <n-descriptions-item label="标签">
                <n-tag v-for="t in splitTags(current.tags)" :key="t" size="small" :bordered="false" type="info" style="margin-right:6px">{{ t }}</n-tag>
                <span v-if="!splitTags(current.tags).length">—</span>
              </n-descriptions-item>
              <n-descriptions-item label="摘要">{{ current.summary || "（无）" }}</n-descriptions-item>
              <n-descriptions-item label="备注">{{ current.notes || "—" }}</n-descriptions-item>
            </n-descriptions>

            <n-divider>对话记录</n-divider>
            <div class="chat">
              <div
                v-for="t in current.turns"
                :key="t.id"
                class="chat-row"
                :class="t.role"
              >
                <div class="chat-bubble">
                  <div class="chat-role">{{ t.role === "user" ? "用户" : "助手" }}</div>
                  <div class="chat-text">{{ t.content }}</div>
                  <img v-if="t.image_sha && thumbUrl(t.image_sha)" :src="thumbUrl(t.image_sha)" class="chat-img" />
                </div>
              </div>
              <n-empty v-if="!current.turns || !current.turns.length" description="暂无对话记录" />
            </div>

            <n-divider>关联图片 ({{ (current.images || []).length }})</n-divider>
            <div class="img-grid">
              <div v-for="img in current.images" :key="img.id" class="img-cell">
                <img v-if="thumbUrl(img.sha)" :src="thumbUrl(img.sha)" class="img-thumb" />
                <div v-else class="img-missing">无缩略图<br /><span class="sub">{{ img.sha }}</span></div>
                <div class="img-meta">{{ img.workflow || "—" }}<br />{{ img.width }}×{{ img.height }}</div>
              </div>
              <n-empty v-if="!current.images || !current.images.length" description="本档案暂无关联图片" />
            </div>

            <n-divider>编辑</n-divider>
            <n-form label-placement="top" size="small">
              <n-form-item label="标题">
                <n-input v-model:value="editForm.title" placeholder="给这段剧情起个名字" />
              </n-form-item>
              <n-form-item label="情绪 / 基调">
                <n-input v-model:value="editForm.mood" placeholder="如：甜蜜 / 紧张 / 悲壮" />
              </n-form-item>
              <n-form-item label="场景设定">
                <n-input v-model:value="editForm.scene" type="textarea" :autosize="{ minRows: 2 }" />
              </n-form-item>
              <n-form-item label="角色">
                <n-input v-model:value="editForm.characters" type="textarea" :autosize="{ minRows: 2 }" />
              </n-form-item>
              <n-form-item label="标签（逗号分隔）">
                <n-input v-model:value="editForm.tags" placeholder="tag1, tag2" />
              </n-form-item>
              <n-form-item label="摘要">
                <n-input v-model:value="editForm.summary" type="textarea" :autosize="{ minRows: 3 }" />
              </n-form-item>
              <n-form-item label="备注">
                <n-input v-model:value="editForm.notes" type="textarea" :autosize="{ minRows: 2 }" />
              </n-form-item>
              <n-grid :cols="2" :x-gap="12">
                <n-grid-item>
                  <n-form-item label="评分">
                    <n-rate v-model:value="editForm.rating" />
                  </n-form-item>
                </n-grid-item>
                <n-grid-item>
                  <n-form-item label="状态">
                    <n-select v-model:value="editForm.status" :options="statusOptions.filter((o: any) => o.value)" />
                  </n-form-item>
                </n-grid-item>
              </n-grid>
              <n-space>
                <n-button type="primary" :loading="saving" @click="saveEdit">保存修改</n-button>
                <n-popconfirm @positive-click="() => deleteOne(current.id)">
                  <template #trigger>
                    <n-button type="error">删除此档案</n-button>
                  </template>
                  确认删除该档案？不可恢复。
                </n-popconfirm>
              </n-space>
            </n-form>
          </template>
        </n-scrollbar>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { h, reactive, ref, onMounted } from "vue";
import {
  NCard, NDataTable, NButton, NInput, NSelect, NTag, NDrawer, NDrawerContent,
  NDescriptions, NDescriptionsItem, NScrollbar, NSpace, NPopconfirm, NEmpty,
  NImage, NForm, NFormItem, NRate, NDivider, NGrid, NGridItem, useMessage, useDialog,
} from "naive-ui";
import { apiGet, apiPost, fetchThumb } from "@/api/bridge";

const message = useMessage();
const dialog = useDialog();

const loading = ref(false);
const rows = ref<any[]>([]);
const stats = ref<any>(null);
const checkedRowKeys = ref<number[]>([]);

const filters = reactive({
  keyword: "",
  user_id: "",
  status: "",
  date_from: "",
  date_to: "",
});

const statusOptions = [
  { label: "全部", value: "" },
  { label: "进行中", value: "active" },
  { label: "已结束", value: "finished" },
  { label: "已废弃", value: "abandoned" },
];

const pagination = reactive({
  page: 1,
  pageSize: 20,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [10, 20, 50, 100],
  onUpdatePage: (p: number) => {
    pagination.page = p;
    reload();
  },
  onUpdatePageSize: (s: number) => {
    pagination.pageSize = s;
    pagination.page = 1;
    reload();
  },
});

function statusType(s: string) {
  if (s === "active") return "warning";
  if (s === "finished") return "success";
  return "default";
}
function statusLabel(s: string) {
  return statusOptions.find((o) => o.value === s)?.label || s || "—";
}
function splitTags(t: string) {
  if (!t) return [];
  return String(t).split(",").map((x) => x.trim()).filter(Boolean);
}
const thumbs = reactive<Record<string, string>>({});
function thumbUrl(sha: string) {
  return sha ? (thumbs[sha] || "") : "";
}
async function preloadThumbs(list: string[]) {
  const uniq = Array.from(new Set(list.filter(Boolean)));
  await Promise.all(
    uniq.map(async (sha) => {
      if (thumbs[sha] !== undefined) return;
      try {
        const u = await fetchThumb(sha);
        if (u) thumbs[sha] = u;
      } catch {
        /* 无缩略图则忽略 */
      }
    })
  );
}

const columns = [
  { title: "ID", key: "id", width: 64 },
  {
    title: "标题 / 摘要",
    key: "title",
    render: (row: any) =>
      h("div", {}, [
        h("div", { style: "font-weight:600" }, row.title || "（未命名）"),
        h("div", { style: "color:#9a7a88;font-size:12px;max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, row.summary || "—"),
      ]),
  },
  { title: "用户", key: "user_name", width: 110, render: (row: any) => h("span", {}, [row.user_name || "—", h("div", { style: "color:#9a7a88;font-size:11px" }, row.user_id || "")] ) },
  {
    title: "状态", key: "status", width: 90,
    render: (row: any) => h(NTag, { size: "small", type: statusType(row.status) }, { default: () => statusLabel(row.status) }),
  },
  { title: "轮次", key: "turn_count", width: 64 },
  { title: "图", key: "image_count", width: 56 },
  { title: "评分", key: "rating", width: 100, render: (row: any) => h(NRate, { value: Number(row.rating) || 0, readonly: true, size: "small" }) },
  { title: "开始时间", key: "started_at", width: 150 },
  {
    title: "操作", key: "op", width: 90,
    render: (row: any) =>
      h(NSpace, { size: 4 }, {
        default: () => [
          h(NButton, { size: "small", tertiary: true, onClick: () => openDetail(row.id) }, { default: () => "查看" }),
          h(NPopconfirm, { onPositiveClick: () => deleteOne(row.id) }, {
            default: () => "确认删除？",
            trigger: () => h(NButton, { size: "small", type: "error", tertiary: true }, { default: () => "删" }),
          }),
        ],
      }),
  },
];

function rowProps(row: any) {
  return {
    style: "cursor:pointer",
    onClick: () => openDetail(row.id),
  };
}

async function loadStats() {
  try {
    stats.value = await apiGet("story/stats");
  } catch (e: any) {
    stats.value = null;
  }
}

async function reload() {
  loading.value = true;
  try {
    const data = await apiGet("story/sessions", {
      page: pagination.page,
      size: pagination.pageSize,
      keyword: filters.keyword,
      user_id: filters.user_id,
      status: filters.status,
      date_from: filters.date_from,
      date_to: filters.date_to,
    });
    rows.value = data.sessions || [];
    pagination.itemCount = data.total || 0;
  } catch (e: any) {
    message.error("加载剧情档案失败：" + (e?.message || e));
  } finally {
    loading.value = false;
  }
}

function resetFilters() {
  filters.keyword = "";
  filters.user_id = "";
  filters.status = "";
  filters.date_from = "";
  filters.date_to = "";
  pagination.page = 1;
  reload();
}

// ---------- 详情 ----------
const detailShow = ref(false);
const current = ref<any>({});
const saving = ref(false);
const editForm = reactive({
  title: "", mood: "", scene: "", characters: "", tags: "",
  summary: "", notes: "", rating: 0, status: "",
});

async function openDetail(id: number) {
  detailShow.value = true;
  current.value = {};
  try {
    const data = await apiGet("story/session", { id });
    current.value = data || {};
    await preloadThumbs([
      ...(data.turns || []).filter((t: any) => t.image_sha).map((t: any) => t.image_sha),
      ...(data.images || []).map((i: any) => i.sha),
    ]);
    editForm.title = data.title || "";
    editForm.mood = data.mood || "";
    editForm.scene = data.scene || "";
    editForm.characters = data.characters || "";
    editForm.tags = data.tags || "";
    editForm.summary = data.summary || "";
    editForm.notes = data.notes || "";
    editForm.rating = Number(data.rating) || 0;
    editForm.status = data.status || "";
  } catch (e: any) {
    message.error("加载详情失败：" + (e?.message || e));
  }
}

async function saveEdit() {
  if (!current.value.id) return;
  saving.value = true;
  try {
    await apiPost("story/session/update", {
      id: current.value.id,
      title: editForm.title,
      mood: editForm.mood,
      scene: editForm.scene,
      characters: editForm.characters,
      tags: editForm.tags,
      summary: editForm.summary,
      notes: editForm.notes,
      rating: editForm.rating,
      status: editForm.status,
    });
    message.success("已保存");
    await openDetail(current.value.id);
    await reload();
  } catch (e: any) {
    message.error("保存失败：" + (e?.message || e));
  } finally {
    saving.value = false;
  }
}

async function deleteOne(id: number) {
  try {
    await apiPost("story/session/delete", { ids: [id] });
    message.success("已删除");
    detailShow.value = false;
    await reload();
    await loadStats();
  } catch (e: any) {
    message.error("删除失败：" + (e?.message || e));
  }
}

async function bulkDelete() {
  if (!checkedRowKeys.value.length) return;
  try {
    await apiPost("story/session/delete", { ids: checkedRowKeys.value });
    message.success("已删除 " + checkedRowKeys.value.length + " 条");
    checkedRowKeys.value = [];
    await reload();
    await loadStats();
  } catch (e: any) {
    message.error("删除失败：" + (e?.message || e));
  }
}

onMounted(() => {
  reload();
  loadStats();
});
</script>

<style scoped>
.story-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
}
.stat-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.stat-card {
  flex: 1 1 0;
  min-width: 90px;
  background: var(--bg-panel);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 12px 16px;
  text-align: center;
}
.stat-num {
  font-size: 22px;
  font-weight: 800;
  color: var(--accent);
}
.stat-label {
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 2px;
}
.filter-grid {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr 1fr 1fr auto;
  gap: 8px;
  align-items: center;
}
.filter-actions {
  display: flex;
  gap: 8px;
}
.list-card {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}
.sub {
  color: var(--text-sub);
  font-size: 11px;
}
.chat {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.chat-row {
  display: flex;
}
.chat-row.user {
  justify-content: flex-end;
}
.chat-bubble {
  max-width: 80%;
  padding: 8px 12px;
  border-radius: 12px;
  background: var(--bg-panel);
  border: 1px solid var(--border-color);
}
.chat-row.user .chat-bubble {
  background: linear-gradient(135deg, #ffd9e7, #ffc2da);
  border-color: transparent;
}
.chat-row.assistant .chat-bubble {
  background: #f3eaff;
  border-color: #e6dbff;
}
.chat-role {
  font-size: 11px;
  color: var(--text-sub);
  margin-bottom: 2px;
}
.chat-text {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
}
.chat-img {
  max-width: 160px;
  border-radius: 8px;
  margin-top: 6px;
  display: block;
}
.img-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
}
.img-cell {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  overflow: hidden;
  background: var(--bg-panel);
}
.img-thumb {
  width: 100%;
  display: block;
  aspect-ratio: 1 / 1;
  object-fit: cover;
}
.img-missing {
  width: 100%;
  aspect-ratio: 1 / 1;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  font-size: 11px;
  color: var(--text-sub);
  padding: 6px;
  word-break: break-all;
}
.img-meta {
  font-size: 11px;
  color: var(--text-sub);
  padding: 4px 6px;
  line-height: 1.3;
}
</style>
