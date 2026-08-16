<template>
  <div class="gallery-view">
    <div class="view-head">
      <div>
        <h2>图库</h2>
        <p>点击任意图片查看大图与详情；删除会先移入回收站，回收站内彻底删除才是真删。</p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="loadStats">刷新</n-button>
        <n-button @click="backupDb">备份数据库</n-button>
      </div>
    </div>

    <div v-if="stats" class="gal-stats">
      <n-statistic label="图片总数" :value="stats.total_count ?? 0" />
      <n-statistic label="收藏数" :value="stats.starred_count ?? 0" />
      <n-statistic label="总大小" :value="fmtBytes(stats.total_size_bytes)" />
      <n-statistic label="使用次数" :value="stats.total_use ?? 0" />
    </div>

    <div class="toolbar">
      <n-tabs v-model:value="activeTab" type="segment" size="small" @update:value="onTabChange">
        <n-tab-pane name="normal" tab="图库" />
        <n-tab-pane name="trash" tab="回收站" />
      </n-tabs>
      <n-input v-model:value="search" size="small" placeholder="搜索关键词、prompt…" style="width:260px" clearable @keyup.enter="doSearch(1)" />
      <n-select
        v-model:value="type"
        size="small"
        style="width:120px"
        :options="typeOptions"
        @update:value="doSearch(1)"
      />
      <n-checkbox v-model:checked="starred" size="small" @update:checked="doSearch(1)">仅收藏</n-checkbox>
      <n-button size="small" type="primary" @click="doSearch(1)">搜索</n-button>
      <span class="count">{{ total ? total + " 条" : "" }}</span>
    </div>

    <div class="gal-scroll">
      <n-spin :show="searching">
        <div class="gal-grid">
          <n-empty v-if="!searching && !images.length" :description="activeTab === 'trash' ? '回收站为空' : '请输入关键词搜索或直接浏览图库'" style="padding:60px" />
          <div v-for="img in images" :key="img.sha || img.sha256" class="gal-item" @click="openDetail(img)">
            <img :src="thumbCache[img.sha || img.sha256] || placeholder" :alt="truncate(img.prompt, 20)" loading="lazy" />
            <div class="gal-item-overlay">
              <n-tag v-if="img.starred" size="tiny" type="warning" :bordered="false">★</n-tag>
              <n-tag v-if="img.source" size="tiny" :bordered="false">{{ typeLabel(img.source) }}</n-tag>
            </div>
          </div>
        </div>
      </n-spin>
    </div>

    <n-pagination
      v-if="total > pageSize"
      v-model:page="page"
      :page-size="pageSize"
      :item-count="total"
      style="justify-content:flex-end;flex:0 0 auto;padding-top:12px"
      @update:page="doSearch"
    />

    <!-- 图片详情 -->
    <n-modal v-model:show="detailShow" style="width:min(900px, 92vw)" :bordered="false" :show-mask="true">
      <div class="img-detail">
        <div class="img-detail-imgs">
          <img v-if="detailItem" :src="detailDataUrl" alt="" style="max-width:100%;border-radius:8px" />
        </div>
        <div class="img-detail-info" v-if="detailItem">
          <n-space vertical>
            <n-space>
              <n-tag :type="detailItem.starred ? 'warning' : 'default'" size="small" :bordered="false">{{ detailItem.starred ? "已收藏" : "未收藏" }}</n-tag>
              <n-tag size="small" :bordered="false">{{ typeLabel(detailItem.source) }}</n-tag>
              <n-tag v-if="detailItem.is_img2img" size="small" type="info" :bordered="false">图生图</n-tag>
            </n-space>
            <div class="info-row"><b>提示词：</b><pre>{{ detailItem.prompt || "—" }}</pre></div>
            <div class="info-row"><b>原始提示词：</b><pre>{{ detailItem.prompt_raw || "—" }}</pre></div>
            <div class="info-row"><b>工作流：</b>{{ detailItem.workflow || "—" }}</div>
            <div class="info-row"><b>LoRA：</b>{{ loraStr(detailItem.loras) }}</div>
            <div class="info-row"><b>尺寸：</b>{{ detailItem.w && detailItem.h ? `${detailItem.w}×${detailItem.h}` : "—" }}</div>
            <div class="info-row"><b>大小：</b>{{ fmtBytes(detailItem.size_bytes) }}</div>
            <div class="info-row"><b>seed：</b>{{ detailItem.seed ?? "—" }}</div>
            <div class="info-row"><b>denoise：</b>{{ detailItem.denoise ?? "—" }}</div>
            <div class="info-row"><b>用户：</b>{{ detailItem.user_name || detailItem.user_id || "—" }}</div>
            <div class="info-row"><b>时间：</b>{{ fmtDateTime(detailItem.created_at) }}</div>
            <div class="info-row"><b>使用次数：</b>{{ detailItem.use_count ?? 0 }}</div>
            <n-space style="margin-top:12px">
              <n-button v-if="activeTab !== 'trash'" size="small" @click="toggleStar(detailItem)">{{ detailItem.starred ? "取消收藏" : "收藏" }}</n-button>
              <n-button v-if="activeTab !== 'trash'" size="small" type="error" @click="deleteImage(detailItem)">删除</n-button>
              <n-button v-else size="small" type="primary" @click="restoreImage(detailItem)">恢复</n-button>
              <n-button v-if="activeTab === 'trash'" size="small" type="error" @click="purgeImage(detailItem)">彻底删除</n-button>
            </n-space>
          </n-space>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useMessage, useDialog, NButton, NModal, NInput, NSelect, NCheckbox, NTag, NSpace, NSpin, NEmpty, NPagination, NStatistic, NTabs, NTabPane } from "naive-ui";
import { apiGet, apiPost, fetchThumb, fetchImageMeta } from "@/api/bridge";
import { fmtBytes, fmtDateTime, truncate } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";

const message = useMessage();
const dialog = useDialog();
const loading = ref(false);
const searching = ref(false);
const activeTab = ref("normal");
const search = ref("");
const type = ref("");
const starred = ref(false);
const images = ref<any[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 40;
const stats = ref<any>(null);
const thumbCache = reactive<Record<string, string>>({});

const placeholder = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
const typeOptions = [
  { label: "全部类型", value: "" },
  { label: "文生图", value: "gen" },
  { label: "图生图", value: "img2img" },
  { label: "参考图", value: "ref" },
];

function typeLabel(src: string): string {
  if (src === "gen") return "文生图";
  if (src === "img2img") return "图生图";
  if (src === "ref") return "参考图";
  return src || "图片";
}

function loraStr(raw: any): string {
  if (!raw) return "—";
  try {
    const arr = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (Array.isArray(arr)) return arr.map((l) => (typeof l === "string" ? l : l.name || "")).filter(Boolean).join("、") || "—";
    return String(raw);
  } catch {
    return String(raw);
  }
}

async function loadStats() {
  try {
    stats.value = await apiGet("gallery/stats");
  } catch (e: any) {
    // stats 可能未启用，不阻塞
  }
}

async function doSearch(p: number) {
  searching.value = true;
  page.value = p;
  try {
    const data = await apiGet("gallery/search", {
      keyword: search.value.trim(),
      type: type.value || undefined,
      starred: starred.value ? 1 : 0,
      trash: activeTab.value === "trash" ? 1 : 0,
      page: p,
      size: pageSize,
    });
    images.value = (data && Array.isArray(data.images)) ? data.images : [];
    total.value = data && data.total != null ? Number(data.total) : 0;
    // 懒加载缩略图
    images.value.forEach((img) => {
      const sha = img.sha || img.sha256;
      if (sha && !thumbCache[sha]) {
        fetchThumb(sha, 300).then((url) => { if (url) thumbCache[sha] = url; }).catch(() => {});
      }
    });
  } catch (e: any) {
    message.error(e.message || "检索失败");
  } finally {
    searching.value = false;
  }
}

function onTabChange() {
  page.value = 1;
  doSearch(1);
}

function backupDb() {
  message.loading("正在备份数据库…", { duration: 15000 });
  apiGet("gallery/backup").then((d) => {
    if (!d || !d.data_url) throw new Error("备份数据为空");
    const mimeMatch = String(d.data_url).match(/^data:([^;,]+)/);
    const mime = mimeMatch ? mimeMatch[1] : "application/octet-stream";
    const b64 = String(d.data_url).split(",", 2)[1] || "";
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const blob = new Blob([bytes], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = d.filename || "gallery_backup.db";
    a.click();
    URL.revokeObjectURL(url);
    message.success("数据库已下载");
  }).catch((e: any) => message.error(e.message || "备份失败"));
}

// 图片详情
const detailShow = ref(false);
const detailItem = ref<any>(null);
const detailDataUrl = ref("");
const detailSha = ref("");

async function openDetail(img: any) {
  const sha = img.sha || img.sha256;
  if (!sha) return;
  detailItem.value = img;
  detailSha.value = sha;
  detailShow.value = true;
  detailDataUrl.value = img.data_url || thumbCache[sha] || "";
  if (!detailDataUrl.value) {
    try {
      const meta = await fetchImageMeta(sha);
      if (meta && meta.data_url) detailDataUrl.value = meta.data_url;
    } catch {}
  }
  // 刷新元数据
  try {
    const meta = await fetchImageMeta(sha);
    if (meta && meta.meta) detailItem.value = { ...img, ...meta.meta, sha: meta.meta.sha256 || sha };
  } catch {}
}

async function toggleStar(img: any) {
  const sha = img.sha || img.sha256;
  try {
    await apiPost("gallery/star", { sha, on: !img.starred });
    img.starred = !img.starred;
    detailItem.value = { ...img };
    message.success(img.starred ? "已收藏" : "已取消收藏");
  } catch (e: any) {
    message.error(e.message || "操作失败");
  }
}

function deleteImage(img: any) {
  const sha = img.sha || img.sha256;
  dialog.warning({
    title: "删除图片",
    content: "确定要删除这张图片吗？将移入回收站，可在回收站内彻底删除。",
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("gallery/delete", { sha });
        message.success("已移入回收站");
        detailShow.value = false;
        doSearch(page.value);
        loadStats();
      } catch (e: any) {
        message.error(e.message || "删除失败");
      }
    },
  });
}

function restoreImage(img: any) {
  const sha = img.sha || img.sha256;
  apiPost("gallery/restore", { sha }).then(() => {
    message.success("已恢复");
    detailShow.value = false;
    doSearch(page.value);
    loadStats();
  }).catch((e: any) => message.error(e.message || "恢复失败"));
}

function purgeImage(img: any) {
  const sha = img.sha || img.sha256;
  dialog.warning({
    title: "彻底删除",
    content: "确定要彻底删除这张图片吗？此操作不可恢复！",
    positiveText: "彻底删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("gallery/purge", { sha });
        message.success("已彻底删除");
        detailShow.value = false;
        doSearch(page.value);
        loadStats();
      } catch (e: any) {
        message.error(e.message || "彻底删除失败");
      }
    },
  });
}

function refresh() {
  loadStats();
  doSearch(page.value);
}

useRefresh(refresh);
onMounted(() => {
  loadStats();
  doSearch(1);
});
</script>

<style scoped>
.gallery-view {
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
.gal-stats { display: flex; gap: 32px; margin-bottom: 12px; flex-wrap: wrap; flex: 0 0 auto; }
.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; flex: 0 0 auto; }
.count { color: var(--text-sub); font-size: 12px; }
/* 图片网格滚动区：占满剩余空间，内部滚动，分页器固定底部 */
.gal-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }
.gal-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}
.gal-item {
  position: relative;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  aspect-ratio: 1;
  background: var(--bg-body);
  border: 1px solid var(--border-color);
}
.gal-item img { width: 100%; height: 100%; object-fit: cover; transition: transform 0.2s; }
.gal-item:hover img { transform: scale(1.05); }
.gal-item-overlay {
  position: absolute;
  top: 6px;
  left: 6px;
  display: flex;
  gap: 4px;
}
.img-detail { display: flex; gap: 16px; padding: 16px; }
.img-detail-imgs { flex: 1; display: flex; align-items: flex-start; }
.img-detail-info { width: 320px; max-height: 70vh; overflow: auto; }
.info-row { font-size: 13px; }
.info-row pre { margin: 4px 0 0; white-space: pre-wrap; word-break: break-all; font-family: inherit; color: var(--text-sub); }
</style>
