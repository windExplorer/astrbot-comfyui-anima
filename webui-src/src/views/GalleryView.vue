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
      <n-statistic label="图片总数" :value="stats.total ?? 0" />
      <n-statistic label="收藏数" :value="stats.starred ?? 0" />
      <n-statistic label="总大小" :value="fmtBytes((stats.size_mb ?? 0) * 1024 * 1024)" />
      <n-statistic label="回收站" :value="stats.trash_count ?? 0" />
    </div>

    <div class="toolbar">
      <n-tabs v-model:value="activeTab" type="segment" size="small" @update:value="onTabChange">
        <n-tab-pane name="normal" tab="图库" />
        <n-tab-pane name="trash" tab="回收站" />
      </n-tabs>
      <n-input v-model:value="search" size="small" placeholder="搜索关键词、prompt…" style="width:220px" clearable @keyup.enter="doSearch(1)" />
      <n-input v-model:value="userSearch" size="small" placeholder="筛选用户昵称 / QQ…" style="width:200px" clearable @keyup.enter="doSearch(1)" />
      <n-select
        v-model:value="type"
        size="small"
        style="width:110px"
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
              <span v-if="img.starred" class="gal-star">★</span>
              <span class="gal-type" :class="'t-' + typeKey(img)">{{ typeLabel(img) }}</span>
            </div>
          </div>
        </div>
      </n-spin>
    </div>

    <Pager
      v-if="total > pageSize"
      :page="page"
      :page-size="pageSize"
      :total="total"
      @update:page="doSearch"
      @update:page-size="onPageSize"
    />

    <!-- 大图查看器 -->
    <ImageViewer
      v-model:show="viewerShow"
      :sha="viewerSha"
      :item="viewerItem"
      :ref-sha="viewerRefSha"
      :is-trash="activeTab === 'trash'"
      @star="onStar"
      @delete="onDelete"
      @restore="onRestore"
      @purge="onPurge"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useMessage, useDialog, NButton, NInput, NSelect, NCheckbox, NTag, NSpin, NEmpty, NStatistic, NTabs, NTabPane } from "naive-ui";
import { apiGet, apiPost, fetchThumb } from "@/api/bridge";
import { fmtBytes, truncate } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import ImageViewer from "@/components/ImageViewer.vue";
import Pager from "@/components/Pager.vue";

const message = useMessage();
const dialog = useDialog();
const loading = ref(false);
const searching = ref(false);
const activeTab = ref("normal");
const search = ref("");
const userSearch = ref("");
const type = ref("");
const starred = ref(false);
const images = ref<any[]>([]);
const total = ref(0);
const page = ref(1);
// 每页缩略图数量：与出图记录一致，避免一页展示太多
let pageSize = 20;
const stats = ref<any>(null);
const thumbCache = reactive<Record<string, string>>({});

const placeholder = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
const typeOptions = [
  { label: "全部类型", value: "" },
  { label: "文生图", value: "gen" },
  { label: "图生图", value: "img2img" },
  { label: "参考图", value: "ref" },
];

function typeKey(img: any): string {
  if (img?.is_img2img || img?.ref_sha256) return "img2img";
  if (img?.source === "ref") return "ref";
  if (img?.source === "user") return "user";
  return "gen";
}
function typeLabel(img: any): string {
  const k = typeKey(img);
  return { gen: "文生图", img2img: "图生图", ref: "参考图", user: "收藏" }[k] || "图片";
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
      user: userSearch.value.trim(),
      type: type.value || undefined,
      starred: starred.value ? 1 : 0,
      trash: activeTab.value === "trash" ? 1 : 0,
      page: p,
      size: pageSize,
    });
    images.value = (data && Array.isArray(data.images)) ? data.images : [];
    total.value = data && data.total != null ? Number(data.total) : 0;
    // 并发预取缩略图（小尺寸，提升加载速度）
    images.value.forEach((img) => {
      const sha = img.sha || img.sha256;
      if (sha && !thumbCache[sha]) {
        fetchThumb(sha, 240).then((url) => { if (url) thumbCache[sha] = url; }).catch(() => {});
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

function onPageSize(s: number) {
  pageSize = s;
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

// 大图查看器：点击缩略图打开原图
const viewerShow = ref(false);
const viewerSha = ref("");
const viewerItem = ref<any>(null);
const viewerRefSha = ref("");

function openDetail(img: any) {
  const sha = img.sha || img.sha256;
  if (!sha) return;
  viewerSha.value = sha;
  viewerItem.value = { ...img };
  viewerRefSha.value = img.ref_sha256 || "";
  viewerShow.value = true;
}

function onStar(img: any) {
  const sha = img.sha || img.sha256;
  apiPost("gallery/star", { sha, on: !img.starred }).then(() => {
    img.starred = !img.starred;
    message.success(img.starred ? "已收藏" : "已取消收藏");
    doSearch(page.value);
    loadStats();
  }).catch((e: any) => message.error(e.message || "操作失败"));
}

function onDelete(img: any) {
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
        viewerShow.value = false;
        doSearch(page.value);
        loadStats();
      } catch (e: any) {
        message.error(e.message || "删除失败");
      }
    },
  });
}

function onRestore(img: any) {
  const sha = img.sha || img.sha256;
  apiPost("gallery/restore", { sha }).then(() => {
    message.success("已恢复");
    viewerShow.value = false;
    doSearch(page.value);
    loadStats();
  }).catch((e: any) => message.error(e.message || "恢复失败"));
}

function onPurge(img: any) {
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
        viewerShow.value = false;
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
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 12px;
}
.gal-item {
  position: relative;
  border-radius: 10px;
  overflow: hidden;
  cursor: pointer;
  aspect-ratio: 3 / 4;
  background: var(--bg-body);
  border: 1px solid var(--border-color);
  box-shadow: 0 2px 8px rgba(255, 143, 179, 0.08);
  transition: transform 0.18s, box-shadow 0.18s;
}
.gal-item:hover { transform: translateY(-3px); box-shadow: 0 6px 18px rgba(255, 143, 179, 0.18); }
.gal-item img { width: 100%; height: 100%; object-fit: cover; transition: transform 0.2s; }
.gal-item:hover img { transform: scale(1.05); }
.gal-item-overlay {
  position: absolute;
  top: 8px;
  left: 8px;
  display: flex;
  gap: 6px;
  align-items: center;
}
.gal-star {
  font-size: 15px;
  color: #ffd257;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.5);
}
.gal-type {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 20px;
  color: #fff;
  backdrop-filter: blur(3px);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25);
}
.gal-type.t-gen { background: linear-gradient(135deg, #ffb3d1, #ff8fb3); }
.gal-type.t-img2img { background: linear-gradient(135deg, #ff9ecb, #ff7ea8); }
.gal-type.t-ref { background: linear-gradient(135deg, #a8d8ff, #7eb6ff); }
.gal-type.t-user { background: linear-gradient(135deg, #ffd98a, #ffb347); }
</style>
