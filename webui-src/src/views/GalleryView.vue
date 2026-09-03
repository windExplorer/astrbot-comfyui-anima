<template>
  <div class="gallery-view">
    <div class="view-head">
      <div>
        <h2>图库</h2>
        <p>点击任意图片查看大图与详情；删除会先移入回收站，回收站内彻底删除才是真删。</p>
      </div>
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="view-actions">
          <n-button :loading="loading" @click="loadStats">刷新</n-button>
          <n-button @click="backupDb">备份数据库</n-button>
        </div>
      </Teleport>
    </div>

    <div v-if="stats" class="gal-stats">
      <div class="stat-card sc-pink">
        <div class="stat-icon">🖼️</div>
        <div class="stat-body">
          <div class="stat-num">{{ (stats.total ?? 0).toLocaleString() }}</div>
          <div class="stat-label">图片总数</div>
        </div>
      </div>
      <div class="stat-card sc-gold">
        <div class="stat-icon">⭐</div>
        <div class="stat-body">
          <div class="stat-num">{{ (stats.starred ?? 0).toLocaleString() }}</div>
          <div class="stat-label">收藏数</div>
        </div>
      </div>
      <div class="stat-card sc-blue">
        <div class="stat-icon">💾</div>
        <div class="stat-body">
          <div class="stat-num">{{ fmtBytes((stats.size_mb ?? 0) * 1024 * 1024) }}</div>
          <div class="stat-label">总大小</div>
        </div>
      </div>
      <div class="stat-card sc-purple">
        <div class="stat-icon">🗑️</div>
        <div class="stat-body">
          <div class="stat-num">{{ (stats.trash_count ?? 0).toLocaleString() }}</div>
          <div class="stat-label">回收站</div>
        </div>
      </div>
      <div class="stat-card sc-red">
        <div class="stat-icon">🔞</div>
        <div class="stat-body">
          <div class="stat-num">{{ (stats.nsfw_count ?? 0).toLocaleString() }}</div>
          <div class="stat-label">NSFW<template v-if="(stats.nsfw_unchecked ?? 0) > 0">（未检测 {{ stats.nsfw_unchecked }}）</template></div>
        </div>
      </div>
    </div>

    <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
      <div class="toolbar">
        <n-tabs v-model:value="activeTab" type="segment" size="small" @update:value="onTabChange">
          <n-tab-pane name="normal" tab="图库" />
          <n-tab-pane name="trash" tab="回收站" />
        </n-tabs>
        <n-input v-model:value="search" size="small" placeholder="搜索关键词、prompt…" style="width:220px" clearable @keyup.enter="doSearch(1)" />
        <n-input v-model:value="tagFilter" size="small" placeholder="按标签筛选（精确匹配）…" style="width:180px" clearable @keyup.enter="doSearch(1)" />
        <n-input v-model:value="userSearch" size="small" placeholder="筛选用户昵称 / QQ…" style="width:200px" clearable @keyup.enter="doSearch(1)" />
        <n-select
          v-model:value="type"
          size="small"
          style="width:110px"
          :options="typeOptions"
          @update:value="doSearch(1)"
        />
        <n-radio-group v-model:value="catFilter" size="small" @update:value="onCatChange">
          <n-radio-button value="all">全部</n-radio-button>
          <n-radio-button value="表情包">表情包</n-radio-button>
          <n-radio-button value="漫画">漫画</n-radio-button>
        </n-radio-group>
        <n-checkbox v-model:checked="starred" size="small" @update:checked="doSearch(1)">仅收藏</n-checkbox>
        <n-select
          v-model:value="nsfwFilter"
          size="small"
          style="width:120px"
          :options="nsfwOptions"
          @update:value="doSearch(1)"
        />
        <n-button size="small" type="primary" @click="doSearch(1)">搜索</n-button>
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button size="small" :loading="retagging" @click="retag">补标表情包/漫画</n-button>
          </template>
          给「自动打标功能上线前」生成的存量图，按工作流类型批量补打「表情包/漫画」标签，之后分类按钮与按标签搜索即可命中
        </n-tooltip>
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-switch v-model:value="nsfwBlurGlobal" size="small" @update:value="onBlurGlobalChange">
              <template #checked>NSFW 模糊：开</template>
              <template #unchecked>NSFW 模糊：关</template>
            </n-switch>
          </template>
          一键开关所有 NSFW 图的模糊显示
        </n-tooltip>
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button size="small" @click="startScan" :disabled="scanState.running" :loading="scanState.running">
              {{ scanState.running ? "检测中…" : "一键检测" }}
            </n-button>
          </template>
          检测图库中所有未检测的图
        </n-tooltip>
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button size="small" @click="startRescan" :disabled="scanState.running" :loading="scanState.running">
              {{ scanState.running ? "重扫中…" : "重新检测" }}
            </n-button>
          </template>
          调整 NSFW 阈值后，用新阈值全量重新检测所有图片
        </n-tooltip>
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button size="small" quaternary @click="refreshScanProgress">↻</n-button>
          </template>
          刷新检测进度
        </n-tooltip>
        <span v-if="scanState.running" class="scan-progress">检测中 {{ scanState.done }}/{{ scanState.total || "?" }}</span>
        <span v-else-if="scanState.finished_at" class="scan-progress scan-done">上次检测 {{ scanState.done }} 张，NSFW {{ scanState.nsfw }}</span>
        <span class="count">{{ total ? total + " 条" : "" }}</span>
      </div>
    </Teleport>

    <div class="gal-scroll">
      <n-spin :show="searching">
        <div class="gal-grid">
          <n-empty v-if="!searching && !images.length" :description="activeTab === 'trash' ? '回收站为空' : '请输入关键词搜索或直接浏览图库'" style="padding:60px" />
          <div v-for="img in images" :key="img.sha || img.sha256" class="gal-item" @click="openDetail(img)">
            <img :src="thumbCache[img.sha || img.sha256] || placeholder" :alt="truncate(img.prompt, 20)" loading="lazy" :class="{ 'nsfw-blur': isNsfwBlurred(img) }" @error="onThumbError(img)" />
            <div v-if="isNsfwBlurred(img)" class="gal-nsfw-mask">
              <span>🔞</span>
              <span class="gal-nsfw-tip">点击查看</span>
            </div>
            <div class="gal-item-overlay">
              <span v-if="img.starred" class="gal-star">★</span>
              <span class="gal-type" :class="'t-' + typeKey(img)">{{ typeLabel(img) }}</span>
            </div>
            <span v-if="img.user_name" class="gal-user" :title="img.user_name">{{ cutName(img.user_name) }}</span>
            <!-- 右上角操作：收藏 / 删除 -->
            <div class="gal-actions">
              <button
                class="gal-act gal-star-btn"
                :class="{ on: img.starred }"
                :title="img.starred ? '取消收藏' : '收藏'"
                @click.stop="onStar(img)"
              >★</button>
              <n-popconfirm
                :show-icon="false"
                positive-text="删除"
                negative-text="取消"
                @positive-click="deleteImage(img)"
              >
                <template #trigger>
                  <button class="gal-act gal-del-btn" title="删除" @click.stop.prevent>🗑</button>
                </template>
                确定删除这张图片吗？将移入回收站。
              </n-popconfirm>
            </div>
            <!-- 封面未加载/加载失败时，hover 显示重载按钮 -->
            <button
              v-if="!thumbCache[img.sha || img.sha256]"
              class="gal-reload"
              :title="thumbFailed[img.sha || img.sha256] ? '封面加载失败，点击重新加载' : '点击重新加载封面'"
              :disabled="reloading[img.sha || img.sha256]"
              @click.stop="reloadThumb(img)"
            >{{ reloading[img.sha || img.sha256] ? "…" : "↻ 重载封面" }}</button>
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
      :blur-global="nsfwBlurGlobal"
      :images="viewerImages"
      :index="viewerIndex"
      @star="onStar"
      @delete="onDelete"
      @restore="onRestore"
      @purge="onPurge"
      @nav="onViewerNav"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref, computed } from "vue";
import { useMessage, useDialog, NButton, NInput, NSelect, NCheckbox, NTag, NSpin, NEmpty, NTabs, NTabPane, NSwitch, NTooltip, NRadioGroup, NRadioButton } from "naive-ui";
import { apiGet, apiPost, fetchThumb } from "@/api/bridge";
import { lsGet, lsSet } from "@/api/storage";
import { fmtBytes, truncate } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import { useDevice } from "@/composables/useDevice";
import ImageViewer from "@/components/ImageViewer.vue";
import Pager from "@/components/Pager.vue";

const message = useMessage();
const dialog = useDialog();
const { isMobile } = useDevice();
const loading = ref(false);
const searching = ref(false);
const activeTab = ref("normal");
const search = ref("");
const userSearch = ref("");
const tagFilter = ref("");
// 顶部「全部/表情包/漫画」分类筛选：映射到 tag 精确匹配（表情包/漫画 由出图自动打标）
const catFilter = computed<string>({
  get: () => (tagFilter.value === "表情包" || tagFilter.value === "漫画") ? tagFilter.value : "all",
  set: (v: string) => { tagFilter.value = (v === "all" ? "" : v); },
});
function onCatChange() { doSearch(1); }
const type = ref("");
const starred = ref(false);
const images = ref<any[]>([]);
const total = ref(0);
const page = ref(1);
// 每页缩略图数量：与出图记录一致，避免一页展示太多；用 localStorage 缓存，刷新不变
let pageSize = Number(lsGet("anima_gallery_page_size") || "") || 20;
const stats = ref<any>(null);
const thumbCache = reactive<Record<string, string>>({});
// 记录封面加载失败的 sha；reloading 记录正在重载中的 sha（用于按钮状态）
const thumbFailed = reactive<Record<string, boolean>>({});
const reloading = reactive<Record<string, boolean>>({});

const placeholder = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
const typeOptions = [
  { label: "全部类型", value: "" },
  { label: "文生图", value: "gen" },
  { label: "图生图", value: "img2img" },
  { label: "参考图", value: "ref" },
];
// NSFW 筛选：""=全部；"0"=仅常规；"1"=仅NSFW
const nsfwFilter = ref("");
const nsfwOptions = [
  { label: "全部（含 NSFW）", value: "" },
  { label: "仅常规", value: "0" },
  { label: "仅 NSFW", value: "1" },
];
// 全局 NSFW 模糊开关（localStorage 持久化，默认开启）
const nsfwBlurGlobal = ref(true);
try {
  const v = lsGet("anima_gal_nsfw_blur");
  if (v != null) nsfwBlurGlobal.value = v === "1";
} catch { /* ignore */ }
function onBlurGlobalChange() {
  try { lsSet("anima_gal_nsfw_blur", nsfwBlurGlobal.value ? "1" : "0"); } catch { /* ignore */ }
}
// ---- NSFW 一键检测 + 进度 ----
const scanState = ref<{ running: boolean; total: number; done: number; nsfw: number; finished_at: number | null }>({
  running: false, total: 0, done: 0, nsfw: 0, finished_at: null,
});
function startScan() {
  apiGet("gallery/scan_nsfw", { only: 1 })
    .then((res: any) => { scanState.value = { ...scanState.value, ...res }; })
    .catch((e: any) => {
      if (isNsfwUnavailable(e)) showNsfwInstallDialog();
      else message.error(e.message || "启动检测失败");
    });
}
function startRescan() {
  // 全量重新检测：调整阈值后用新阈值重扫所有图（only=0）
  dialog.warning({
    title: "全量重新检测",
    content: "将对图库中所有图片重新执行 NSFW 检测（耗时取决于图片数量，后台执行）。确认继续？",
    positiveText: "开始重扫",
    negativeText: "取消",
    onPositiveClick: () => {
      return new Promise((resolve, reject) => {
        apiGet("gallery/scan_nsfw", { only: 0 })
          .then((res: any) => {
            scanState.value = { ...scanState.value, ...res };
            message.success("已开始全量重新检测");
            resolve(true);
          })
          .catch((e: any) => {
            if (isNsfwUnavailable(e)) showNsfwInstallDialog();
            else message.error(e.message || "启动重扫失败");
            reject(e);
          });
      });
    },
  });
}
function isNsfwUnavailable(e: any): boolean {
  const m = String(e?.message || "");
  return /NSFW 检测不可用|onnxruntime|opennsfw/.test(m);
}
function showNsfwInstallDialog() {
  dialog.warning({
    title: "NSFW 检测不可用",
    content: "NSFW 检测需要两个依赖库。请在 AstrBot 日志页右上角的「安装 pip 库」入口依次填入以下库名并安装，完成后重启插件：\n\n· onnxruntime\n· opennsfw-onnx",
    positiveText: "知道了",
    closable: true,
  });
}
function refreshScanProgress() {
  apiGet("gallery/scan_nsfw_progress")
    .then((res: any) => {
      scanState.value = { ...scanState.value, ...res };
      if (res?.finished_at && !res?.running) {
        // 检测完成，刷新列表与统计
        doSearch(1);
      }
    })
    .catch((e: any) => message.error(e.message || "刷新进度失败"));
}
// 该图是否应模糊显示：NSFW 图 && 全局开关开 && 单图未强制取消（nsfw_blur=0 时单图不模糊，=1 时单图模糊）
function isNsfwBlurred(img: any): boolean {
  if (!img || !img.nsfw) return false;
  if (!nsfwBlurGlobal.value) return false;
  // 单图覆盖：nsfw_blur=0 强制不模糊；=1 强制模糊；null 跟随全局
  if (img.nsfw_blur === 0) return false;
  if (img.nsfw_blur === 1) return true;
  return true;
}

function typeKey(img: any): string {
  if (img?.is_img2img || img?.ref_sha256) return "img2img";
  if (img?.source === "ref") return "ref";
  if (img?.source === "user") return "user";
  return "gen";
}
function typeLabel(img: any): string {
  const k = typeKey(img);
  return { gen: "文生图", img2img: "图生图", ref: "参考图", user: "用户图" }[k] || "图片";
}
// 出图人昵称最多显示 8 个字（超出省略号，完整名在 title 悬浮提示）
function cutName(name: string): string {
  return truncate(name, 8);
}

async function loadStats() {
  try {
    stats.value = await apiGet("gallery/stats");
  } catch (e: any) {
    // stats 可能未启用，不阻塞
  }
}

// 限并发预取缩略图：默认一次最多 4 个在途请求，避免首屏一次性并发 20 张压垮后端
// 造成批量超时（这是“部分图一直不加载”的主因）。失败的 sha 记进 thumbFailed，
// 网格中对应项会出现“重载封面”按钮供单独重试。
async function prefetchThumbs(list: any[]) {
  const CONCURRENCY = 4;
  const queue = list
    .map((img) => img.sha || img.sha256)
    .filter((sha) => sha && !thumbCache[sha]);
  let idx = 0;
  async function worker() {
    while (idx < queue.length) {
      const sha = queue[idx++];
      try {
        const url = await fetchThumb(sha, 480);
        if (url) {
          thumbCache[sha] = url;
          delete thumbFailed[sha];
        } else {
          thumbFailed[sha] = true;
        }
      } catch {
        thumbFailed[sha] = true;
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, () => worker()));
}

// 单图封面重载：清除失败标记后重新拉取，成功后写入缓存
async function reloadThumb(img: any) {
  const sha = img.sha || img.sha256;
  if (!sha || reloading[sha]) return;
  reloading[sha] = true;
  try {
    const url = await fetchThumb(sha, 480);
    if (url) {
      thumbCache[sha] = url;
      delete thumbFailed[sha];
    } else {
      thumbFailed[sha] = true;
      message.warning("封面仍无法加载，可能是原图已损坏或后端生成失败");
    }
  } catch (e: any) {
    thumbFailed[sha] = true;
    message.error(e?.message || "封面重载失败");
  } finally {
    reloading[sha] = false;
  }
}

// 封面 <img> 解码失败（如 data URL 损坏/超大）时的兜底：标记失败并显示重载按钮
function onThumbError(img: any) {
  const sha = img.sha || img.sha256;
  if (sha) thumbFailed[sha] = true;
}

const retagging = ref(false);
async function retag() {
  retagging.value = true;
  try {
    const d = await apiPost("gallery/retag");
    message.success(d?.msg || "补标完成");
    doSearch(1);
  } catch (e: any) {
    message.error(e?.message || "补标失败");
  } finally {
    retagging.value = false;
  }
}

async function doSearch(p: number) {
  searching.value = true;
  page.value = p;
  try {
    const data = await apiGet("gallery/search", {
      keyword: search.value.trim(),
      user: userSearch.value.trim(),
      tag: tagFilter.value.trim(),
      type: type.value || undefined,
      starred: starred.value ? 1 : 0,
      trash: activeTab.value === "trash" ? 1 : 0,
      nsfw: nsfwFilter.value || undefined,
      page: p,
      size: pageSize,
    });
    images.value = (data && Array.isArray(data.images)) ? data.images : [];
    total.value = data && data.total != null ? Number(data.total) : 0;
    // 预填充 thumbCache 的所有 key 为 undefined，确保 Vue 在首屏就对这些 key 建立
    // 响应式依赖；否则“动态新增 key”在 v-for + 异步赋值场景下可能不触发重渲染，
    // 表现为“接口已拿到 base64，但封面不渲染”。
    images.value.forEach((img) => {
      const s = img.sha || img.sha256;
      if (s && !(s in thumbCache)) thumbCache[s] = undefined as any;
    });
    // 预取缩略图：限并发避免一次性压垮后端导致批量超时（部分图因此“一直不加载”），
    // 失败的记录进 thumbFailed，网格中显示“重载封面”按钮可单独重试。
    prefetchThumbs(images.value);
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
  lsSet("anima_gallery_page_size", String(s));
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

// 大图查看器：点击缩略图打开原图（支持左右箭头在列表间导航）
const viewerShow = ref(false);
const viewerSha = ref("");
const viewerItem = ref<any>(null);
const viewerRefSha = ref("");
const viewerIndex = ref(0);
const viewerImages = computed(() => images.value.map((it: any) => ({
  sha: it.sha || it.sha256,
  item: it,
  refSha: it.ref_sha256 || "",
})));

function openDetail(img: any) {
  const sha = img.sha || img.sha256;
  if (!sha) return;
  const idx = images.value.findIndex((it: any) => (it.sha || it.sha256) === sha);
  viewerIndex.value = idx >= 0 ? idx : 0;
  viewerSha.value = sha;
  viewerItem.value = { ...img };
  viewerRefSha.value = img.ref_sha256 || "";
  viewerShow.value = true;
}

// 导航：左右切换（边界由 ImageViewer 禁用箭头 + 此处 clamp 双重保护）
function onViewerNav(delta: number) {
  const ni = viewerIndex.value + delta;
  if (ni < 0 || ni >= viewerImages.value.length) return;
  const it = viewerImages.value[ni];
  if (!it || !it.sha) return;
  viewerIndex.value = ni;
  openDetail(it.item);
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

// 真正执行删除（移入回收站）。卡片右上角的 popconfirm 已做二次确认，直接调用此函数。
async function deleteImage(img: any) {
  const sha = img.sha || img.sha256;
  try {
    await apiPost("gallery/delete", { sha });
    message.success("已移入回收站");
    if (viewerShow.value) viewerShow.value = false;
    doSearch(page.value);
    loadStats();
  } catch (e: any) {
    message.error(e.message || "删除失败");
  }
}

function onDelete(img: any) {
  const sha = img.sha || img.sha256;
  dialog.warning({
    title: "删除图片",
    content: "确定要删除这张图片吗？将移入回收站，可在回收站内彻底删除。",
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: () => deleteImage(img),
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
// 监听大图检测结果，本地同步图库列表对应图的 NSFW 状态（封面立即模糊，无需重新搜索）
function onNsfwUpdated(e: Event) {
  const detail = (e as CustomEvent)?.detail;
  if (!detail?.sha) return;
  const arr = images.value;
  for (let i = 0; i < arr.length; i++) {
    const it = arr[i];
    if ((it.sha || it.sha256) === detail.sha) {
      it.nsfw = !!detail.nsfw;
      it.nsfw_score = detail.nsfw_score ?? null;
      it.nsfw_checked = true;
      break;
    }
  }
}
onMounted(() => {
  loadStats();
  doSearch(1);
  window.addEventListener("anima:nsfw-updated", onNsfwUpdated);
});
// 组件卸载时清理监听，避免重复监听导致内存泄漏
onUnmounted(() => window.removeEventListener("anima:nsfw-updated", onNsfwUpdated));
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
.gal-stats { display: flex; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; flex: 0 0 auto; }
.stat-card {
  flex: 1 1 0;
  min-width: 150px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid var(--border-color);
  background: var(--bg-panel);
  box-shadow: 0 2px 10px rgba(255, 143, 179, 0.08);
}
.stat-icon {
  width: 42px;
  height: 42px;
  border-radius: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex: 0 0 auto;
}
.stat-body { display: flex; flex-direction: column; min-width: 0; }
.stat-num { font-size: 20px; font-weight: 800; color: var(--text-main); line-height: 1.2; }
.stat-label { font-size: 12px; color: var(--text-sub); margin-top: 2px; }
.sc-pink .stat-icon { background: rgba(255, 143, 179, 0.15); }
.sc-gold .stat-icon { background: rgba(255, 210, 87, 0.18); }
.sc-blue .stat-icon { background: rgba(126, 182, 255, 0.18); }
.sc-purple .stat-icon { background: rgba(181, 152, 255, 0.18); }
.sc-red .stat-icon { background: rgba(255, 107, 107, 0.18); }
/* NSFW 模糊 */
.nsfw-blur { filter: blur(14px); transform: scale(1.08); }
.gal-nsfw-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #fff;
  pointer-events: none;
  font-size: 26px;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.6);
}
.gal-nsfw-mask .gal-nsfw-tip { font-size: 12px; font-weight: 600; background: rgba(0,0,0,0.45); padding: 2px 10px; border-radius: 20px; }
.gal-user {
  position: absolute;
  left: 6px;
  bottom: 6px;
  z-index: 3;
  max-width: 70%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
  background: rgba(0, 0, 0, 0.45);
  padding: 1px 8px;
  border-radius: 12px;
  backdrop-filter: blur(3px);
}
/* 右上角操作区：收藏 / 删除 图标，hover 整卡时浮现 */
.gal-actions {
  position: absolute;
  top: 5px;
  right: 5px;
  z-index: 5;
  display: flex;
  gap: 5px;
  opacity: 0;
  transition: opacity 0.15s;
}
.gal-item:hover .gal-actions {
  opacity: 1;
}
.gal-act {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  color: #fff;
  background: rgba(0, 0, 0, 0.5);
  box-shadow: 0 1px 5px rgba(0, 0, 0, 0.3);
  transition: background 0.12s, transform 0.12s;
}
.gal-act:hover {
  transform: scale(1.08);
}
.gal-star-btn.on {
  color: #ffd357;
  background: rgba(255, 211, 87, 0.22);
}
.gal-del-btn:hover {
  background: rgba(255, 99, 107, 0.9);
}
/* 收藏状态：即便不 hover 也让星标常显，方便辨认 */
.gal-star-btn.on {
  opacity: 1;
}
.gal-actions:has(.gal-star-btn.on) {
  opacity: 1;
}
/* 封面未加载时的重载按钮：居中浮于图片上，hover 整卡时显示 */
.gal-reload {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: 4;
  border: none;
  border-radius: 20px;
  padding: 6px 14px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  background: rgba(255, 99, 146, 0.92);
  cursor: pointer;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.35);
  opacity: 0;
  transition: opacity 0.15s;
}
.gal-item:hover .gal-reload {
  opacity: 1;
}
.gal-reload:disabled {
  background: rgba(150, 150, 150, 0.85);
  cursor: default;
}
.scan-progress {
  font-size: 12px;
  color: #f0c060;
  margin-left: 4px;
}
.scan-progress.scan-done {
  color: #7fd0a0;
}
.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; flex: 0 0 auto; }
.count { color: var(--text-sub); font-size: 12px; }
/* 图片网格滚动区：占满剩余空间，内部滚动，分页器固定底部 */
.gal-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }
.gal-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  align-items: start; /* 防止 grid 默认 stretch 拉伸 item 导致 aspect-ratio 失效、封面高度异常 */
  gap: 12px;
}
.gal-item {
  position: relative;
  border-radius: 10px;
  overflow: hidden;
  cursor: pointer;
  aspect-ratio: 3 / 4;
  min-height: 200px; /* 兜底：即便父级高度链异常也能保证封面可见 */
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

/* ===================== 移动端适配 ===================== */
@media (max-width: 768px) {
  .gallery-view { padding: 10px; }
  /* view-head 标题与操作按钮堆叠，避免并排溢出 */
  .view-head { flex-direction: column; align-items: stretch; gap: 10px; }
  .view-actions { flex-wrap: wrap; }
  .view-actions :deep(.n-button) { flex: 1 1 auto; }
  /* 网格退化为单列（minmax(230px,1fr) 在窄屏本就会单列，这里再兜底更稳） */
  .gal-grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
  /* 移动端无 hover：收藏/删除/重载图标必须常显，否则无法操作 */
  .gal-actions { opacity: 1 !important; }
  .gal-reload { opacity: 1 !important; }
  .gal-act { width: 30px; height: 30px; font-size: 15px; }
  .gal-stats { gap: 8px; }
  .stat-card { padding: 8px 10px; }
  /* 工具栏固定宽度控件在窄屏改为全宽换行 */
  .toolbar { flex-wrap: wrap; }
  .toolbar :deep(.n-input),
  .toolbar :deep(.n-select) { width: 100% !important; flex: 1 1 100%; }
  /* 短屏手机：上方统计/工具栏堆叠会挤占空间，给网格滚动区兜底最小高度，保证封面可见 */
  .gal-scroll { min-height: 320px; }
}
</style>
