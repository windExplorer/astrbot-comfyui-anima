<template>
  <div class="review-view">
    <div class="view-head">
      <div>
        <h2>图片复核</h2>
        <p>处理远程判定（nsfw-check）卡「待人工复核」的图：放行 / 标记 NSFW / 退回。关联了检测记录的图，结论会同步写通 nsfw-check。</p>
      </div>
      <!-- 刷新 移动端收进底部操作弹窗面板 -->
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="view-actions">
          <n-button :loading="loading" @click="loadAll">刷新</n-button>
        </div>
      </Teleport>
    </div>

    <div class="rv-scroll">
      <n-spin :show="loading">
        <!-- 汇总卡（TODO §7.2） -->
        <div class="rv-cards">
          <n-card size="small" class="rv-card"><div class="card-num warn">{{ stats.pending }}</div><div class="card-label">待复核</div></n-card>
          <n-card size="small" class="rv-card"><div class="card-num ok">{{ stats.passed }}</div><div class="card-label">已放行</div></n-card>
          <n-card size="small" class="rv-card"><div class="card-num err">{{ stats.blocked }}</div><div class="card-label">已拦截</div></n-card>
          <n-card size="small" class="rv-card"><div class="card-num sub">{{ stats.diverge }}</div><div class="card-label">分歧图</div></n-card>
        </div>

        <!-- 筛选工具条 -->
        <div class="rv-toolbar">
          <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
            <div class="rv-filter-block">
              <n-radio-group v-model:value="fStatus" size="small" @update:value="onFilterChange">
                <n-radio-button value="0">待复核</n-radio-button>
                <n-radio-button value="1">已放行</n-radio-button>
                <n-radio-button value="2">已 NSFW</n-radio-button>
                <n-radio-button value="auto">已自动审</n-radio-button>
                <n-radio-button value="manual">已人工审</n-radio-button>
                <n-radio-button value="all">全部</n-radio-button>
              </n-radio-group>
              <n-checkbox v-model:checked="fDiverge" @update:checked="onFilterChange">仅看分歧</n-checkbox>
              <n-input
                v-model:value="fKeyword"
                size="small"
                placeholder="搜索 sha / 用户 / 提示词"
                clearable
                class="rv-search"
                @update:value="onFilterChange"
              />
            </div>
          </Teleport>
        </div>

        <!-- 批量操作条 -->
        <div v-if="selected.length" class="rv-batchbar">
          <span class="rv-batch-count">已选 {{ selected.length }} 张</span>
          <n-button size="small" type="success" :loading="batchBusy" @click="batchReview('pass')">批量放行</n-button>
          <n-button size="small" type="error" :loading="batchBusy" @click="batchReview('nsfw')">批量标记 NSFW</n-button>
          <n-button size="small" quaternary @click="clearPicks">取消选择</n-button>
        </div>

        <!-- 队列列表 -->
        <div class="panel">
          <div class="panel-title">
            <h3>复核队列</h3>
            <span class="count">{{ total }} 张</span>
          </div>
          <div v-if="!rows.length" class="empty">{{ emptyText }}</div>
          <div v-else class="rv-grid">
            <div
              v-for="r in rows"
              :key="r.sha"
              class="rv-item"
              :class="{ picked: selected.includes(r.sha) }"
            >
              <div class="rv-thumb-wrap" @click="openPreview(r)">
                <img v-if="thumbs[r.sha]" :src="thumbs[r.sha]" loading="lazy" />
                <div v-else class="rv-thumb-ph">加载中…</div>
                <n-checkbox
                  class="rv-pick"
                  :checked="selected.includes(r.sha)"
                  @click.stop
                  @update:checked="() => togglePick(r)"
                />
                <div v-if="r.diverge" class="rv-badge diverge">分歧</div>
                <div class="rv-badge level" :class="levelClass(r)">{{ levelText(r) }}</div>
              </div>
              <div class="rv-meta">
                <div class="rv-score">远程分 {{ fmtScore(r.nsfw_remote_score) }}<template v-if="r.nsfw_remote_level"> · {{ r.nsfw_remote_level }}</template></div>
                <div v-if="(r.labels || []).length" class="rv-labels" :title="labelsZh(r.labels)">{{ labelsZh(r.labels) }}</div>
                <div class="rv-status">{{ statusText(r) }}</div>
                <div class="rv-actions">
                  <n-button size="tiny" type="success" @click.stop="review(r, 'pass')">放行</n-button>
                  <n-button size="tiny" type="error" @click.stop="review(r, 'nsfw')">NSFW</n-button>
                  <n-button v-if="r.review_status === 0" size="tiny" quaternary @click.stop="review(r, 'pending')">退回</n-button>
                </div>
              </div>
            </div>
          </div>
          <Pager :page="page" :page-size="pageSize" :total="total" @update:page="onPage" @update:page-size="onPageSize" />
        </div>
      </n-spin>
    </div>

    <!-- 大图预览（TODO §7.2：侧栏显示双分/level/分歧 + 操作） -->
    <!-- 宽度用内联 style：n-modal 传送到 body 外渲染，scoped 的 class 够不到卡片本体 -->
    <n-modal v-model:show="showPreview" preset="card" title="复核详情" style="width: min(960px, 94vw)">
      <div v-if="previewRow" class="rv-modal-body">
        <div class="rv-modal-img">
          <img v-if="previewSrc" :src="previewSrc" />
          <div v-else class="rv-thumb-ph tall">加载中…</div>
        </div>
        <div class="rv-modal-side">
          <div class="rv-kv"><span>sha</span><b :title="previewRow.sha256">{{ shortSha }}</b></div>
          <div class="rv-kv"><span>远程分</span><b>{{ fmtScore(previewRow.nsfw_remote_score) }}</b></div>
          <div class="rv-kv"><span>远程 level</span><b>{{ previewRow.nsfw_remote_level || "—" }}</b></div>
          <div class="rv-kv"><span>本地 NSFW 位</span><b>{{ previewRow.nsfw ? "NSFW" : "常规" }}</b></div>
          <div class="rv-kv"><span>分歧</span><b>{{ previewRow.diverge ? "是（主/副模型不一致）" : "否" }}</b></div>
          <div class="rv-kv"><span>当前状态</span><b>{{ statusText(previewRow) }}</b></div>
          <div class="rv-kv" v-if="previewRow.nsfw_record_id"><span>检测记录</span><b :title="previewRow.nsfw_record_id">{{ previewRow.nsfw_record_id }}</b></div>
          <div class="rv-kv" v-if="(previewRow.labels || []).length"><span>触发部位</span><b>{{ labelsZh(previewRow.labels) }}</b></div>
          <div class="rv-kv" v-if="previewRow.prompt"><span>提示词</span><b class="rv-prompt" :title="previewRow.prompt">{{ previewRow.prompt }}</b></div>
          <div class="rv-actions rv-modal-actions">
            <n-button type="success" @click="review(previewRow, 'pass')">放行</n-button>
            <n-button type="error" @click="review(previewRow, 'nsfw')">标记 NSFW</n-button>
            <n-button quaternary @click="review(previewRow, 'pending')">退回待复核</n-button>
          </div>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  useMessage, useDialog, NButton, NCard, NCheckbox, NInput,
  NModal, NRadioButton, NRadioGroup, NSpin,
} from "naive-ui";
import Pager from "@/components/Pager.vue";
import { apiGet, apiPost, fetchThumb } from "@/api/bridge";
import { lsGet, lsSet } from "@/api/storage";
import { useRefresh } from "@/composables/useRefresh";
import { useDevice } from "@/composables/useDevice";

const message = useMessage();
const dialog = useDialog();
const { isMobile } = useDevice();

const loading = ref(false);
const rows = ref<any[]>([]);
const total = ref(0);
const stats = ref({ pending: 0, passed: 0, blocked: 0, diverge: 0 });
const thumbs = ref<Record<string, string>>({});

// 筛选（服务端分页：待复核队列可能很大）
const fStatus = ref("0");
const fDiverge = ref(false);
const fKeyword = ref("");
const page = ref(1);
const pageSize = ref(Number(lsGet("anima_review_page_size") || "") || 40);

// 批量选择（普通数组保响应式简单）
const selected = ref<string[]>([]);
const batchBusy = ref(false);

// 预览（网格缩略图 300px 够列表用；大图预览按需拉 1200px，拿不到兜底用网格图）
const showPreview = ref(false);
const previewRow = ref<any>(null);
const bigThumbs = ref<Record<string, string>>({});
const previewSrc = computed(() => {
  const sha = previewRow.value?.sha;
  return (sha && (bigThumbs.value[sha] || thumbs.value[sha])) || "";
});
const shortSha = computed(() => (previewRow.value?.sha || "").slice(0, 12));

const emptyText = computed(() => {
  if (fStatus.value === "0" && !fDiverge.value && !fKeyword.value.trim()) {
    return "没有待复核的图片。远程判为 review / 检测服务不可用的图会出现在这里。";
  }
  return "没有符合筛选条件的图片。";
});

function fmtScore(v: any): string {
  const n = Number(v);
  if (v === null || v === undefined || Number.isNaN(n)) return "—";
  return n.toFixed(3);
}

// 部位标签通俗中文映射（与后端 /涩图检测 话术同源，未收录的标签原样展示）
const NSFW_LABEL_ZH: Record<string, string> = {
  FEMALE_FACE: "女性面部", MALE_FACE: "男性面部", EYE: "眼部",
  FEMALE_BREAST_COVERED: "女性胸部(有遮挡)", MALE_BREAST_COVERED: "男性胸部(有遮挡)",
  FEMALE_BREAST_EXPOSED: "女性胸部暴露", MALE_BREAST_EXPOSED: "男性胸部暴露",
  NIPPLE_EXPOSED: "露点",
  FEMALE_GENITALIA_COVERED: "女性私处(有遮挡)", MALE_GENITALIA_COVERED: "男性私处(有遮挡)",
  FEMALE_GENITALIA_EXPOSED: "女性私处暴露", MALE_GENITALIA_EXPOSED: "男性私处暴露",
  VAGINA_EXPOSED: "私处暴露", PENIS_EXPOSED: "男性私处暴露",
  BUTTOCKS_COVERED: "臀部(有遮挡)", BUTTOCKS_EXPOSED: "臀部暴露",
  ANUS_COVERED: "肛部(有遮挡)", ANUS_EXPOSED: "肛部暴露",
  ARMPITS_EXPOSED: "腋下暴露", BELLY_EXPOSED: "腹部暴露",
  FEET_COVERED: "足部(有遮挡)", FEET_EXPOSED: "足部暴露",
};

function labelsZh(list: any): string {
  const arr = (Array.isArray(list) ? list : [])
    .map((x: any) => String(x).trim())
    .filter(Boolean);
  if (!arr.length) return "";
  return arr.map((x: string) => NSFW_LABEL_ZH[x.toUpperCase()] || x).join(" / ");
}

function levelText(r: any): string {
  const lv = String(r.nsfw_remote_level || "");
  if (lv === "nsfw") return "NSFW";
  if (lv === "safe") return "安全";
  if (lv === "review") return "中间带";
  if (lv === "unavailable") return "不可用";
  return "未检";
}

function levelClass(r: any): string {
  const lv = String(r.nsfw_remote_level || "");
  if (lv === "nsfw") return "nsfw";
  if (lv === "safe") return "safe";
  return "mid";
}

function statusText(r: any): string {
  const st = Number(r.review_status);
  const manual = !r.review_auto;
  if (st === 0) return "待人工复核";
  if (st === 1) return manual ? "人工放行" : "自动放行";
  if (st === 2) return manual ? "人工标记 NSFW" : "自动拦截";
  return "未参与复核";
}

async function loadStats() {
  try {
    const s = await apiGet("gallery/review_stats");
    if (s && typeof s === "object") stats.value = { pending: 0, passed: 0, blocked: 0, diverge: 0, ...s };
  } catch { /* 汇总失败不阻塞列表 */ }
}

async function loadList() {
  loading.value = true;
  try {
    const data = await apiGet("gallery/review_list", {
      status: fStatus.value,
      diverge: fDiverge.value ? "1" : "",
      kw: fKeyword.value.trim(),
      page: page.value,
      size: pageSize.value,
    }, { timeout: 60000 });
    rows.value = (data && Array.isArray(data.images)) ? data.images : [];
    total.value = Number(data?.total || 0);
    loadThumbs();
  } catch (e: any) {
    message.error(e.message || "读取复核队列失败");
  } finally {
    loading.value = false;
  }
}

function loadAll() {
  loadStats();
  loadList();
}

async function loadThumbs() {
  for (const r of rows.value) {
    const sha = r.sha;
    if (!sha || thumbs.value[sha]) continue;
    try {
      const url = await fetchThumb(sha, 300);
      if (url) thumbs.value = { ...thumbs.value, [sha]: url };
    } catch { /* 单张缩略图失败不阻塞 */ }
  }
}

function onFilterChange() {
  page.value = 1;
  loadList();
}

function onPage(p: number) {
  page.value = p;
  loadList();
}

function onPageSize(s: number) {
  pageSize.value = s;
  lsSet("anima_review_page_size", String(s));
  page.value = 1;
  loadList();
}

function togglePick(r: any) {
  const i = selected.value.indexOf(r.sha);
  if (i >= 0) selected.value = selected.value.filter((s) => s !== r.sha);
  else selected.value = [...selected.value, r.sha];
}

function clearPicks() {
  selected.value = [];
}

function openPreview(r: any) {
  previewRow.value = r;
  showPreview.value = true;
  const sha = r.sha;
  if (sha && !bigThumbs.value[sha]) {
    fetchThumb(sha, 1200)
      .then((url) => {
        if (url) bigThumbs.value = { ...bigThumbs.value, [sha]: url };
      })
      .catch(() => { /* 拿不到大图就兜底用网格缩略图 */ });
  }
}

async function review(r: any, decision: "pass" | "nsfw" | "pending") {
  try {
    const res = await apiPost("gallery/review", { sha: r.sha, decision }, { timeout: 30000 });
    message.success(res?.msg || "已提交");
    if (res?.synced === false && r.nsfw_record_id) {
      message.warning("nsfw-check 同步失败（本地结论已保存）");
    }
    loadAll();
    if (previewRow.value && previewRow.value.sha === r.sha) {
      showPreview.value = false;
    }
  } catch (e: any) {
    message.error(e.message || "复核操作失败");
  }
}

function batchReview(decision: "pass" | "nsfw") {
  const shas = [...selected.value];
  if (!shas.length) return;
  dialog.warning({
    title: decision === "pass" ? "批量放行" : "批量标记 NSFW",
    content: `确定将 ${shas.length} 张图标记为「${decision === "pass" ? "放行" : "NSFW"}」？结论会同步到 nsfw-check（如有记录关联）。`,
    positiveText: "确定",
    negativeText: "取消",
    onPositiveClick: async () => {
      batchBusy.value = true;
      try {
        const res = await apiPost("gallery/review_batch", { shas, decision }, { timeout: 120000 });
        message.success(res?.msg || "批量复核完成");
        selected.value = [];
        loadAll();
      } catch (e: any) {
        message.error(e.message || "批量复核失败");
      } finally {
        batchBusy.value = false;
      }
    },
  });
}

useRefresh(loadAll);
onMounted(loadAll);
</script>

<style scoped>
.review-view { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; flex: 0 0 auto; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.view-actions { display: flex; gap: 8px; }
.rv-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }

/* 汇总卡：与 ShareManageView 一致 */
.rv-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }
.rv-card { text-align: center; }
.card-num { font-size: 22px; font-weight: 700; color: var(--accent); }
.card-num.ok { color: #2e9e5b; }
.card-num.warn { color: #e6a23c; }
.card-num.err { color: #d03050; }
.card-num.sub { color: var(--text-sub); }
.card-label { font-size: 12px; color: var(--text-sub); margin-top: 4px; }

.rv-toolbar { display: flex; gap: 16px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.rv-filter-block { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.rv-search { width: 220px; }

.rv-batchbar { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; padding: 8px 12px;
  background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: 8px; flex-wrap: wrap; }
.rv-batch-count { color: var(--text-sub); font-size: 13px; }

.panel { background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.panel-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.panel-title h3 { margin: 0; }
.count { color: var(--text-sub); font-size: 12px; }
.empty { color: var(--text-sub); text-align: center; padding: 30px; }

/* 缩略图网格 */
.rv-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
.rv-item { border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; background: var(--bg-panel); }
.rv-item.picked { outline: 2px solid var(--accent); }
.rv-thumb-wrap { position: relative; aspect-ratio: 1 / 1; cursor: pointer; background: rgba(128, 128, 128, 0.08); }
.rv-thumb-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
.rv-thumb-ph { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: var(--text-sub); font-size: 12px; }
.rv-thumb-ph.tall { position: static; height: 320px; }
.rv-pick { position: absolute; top: 6px; left: 6px; z-index: 2; }
.rv-badge { position: absolute; top: 6px; right: 6px; padding: 1px 7px; border-radius: 10px;
  font-size: 11px; color: #fff; background: rgba(90, 90, 90, 0.8); }
.rv-badge.diverge { top: 30px; background: #e6a23c; }
.rv-badge.level.safe { background: rgba(46, 158, 91, 0.85); }
.rv-badge.level.nsfw { background: rgba(208, 48, 80, 0.85); }
.rv-badge.level.mid { background: rgba(230, 162, 60, 0.85); }

.rv-meta { padding: 8px 10px 10px; font-size: 12px; color: var(--text-sub); }
.rv-score { color: var(--text-main); }
.rv-labels { margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rv-status { margin-top: 2px; }
.rv-actions { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }

/* 预览弹窗（宽度走 n-modal 内联 style，这里不再管卡片尺寸） */
.rv-modal-body { display: flex; gap: 16px; }
.rv-modal-img { flex: 1 1 auto; min-width: 0; }
.rv-modal-img img { max-width: 100%; max-height: 70vh; border-radius: 8px; display: block; }
.rv-modal-side { flex: 0 0 240px; display: flex; flex-direction: column; gap: 8px; overflow: auto; }
.rv-kv { display: flex; gap: 8px; font-size: 13px; }
.rv-kv span { color: var(--text-sub); flex: 0 0 70px; }
.rv-kv b { word-break: break-all; font-weight: 600; }
.rv-prompt { font-weight: 400 !important; max-height: 90px; overflow: auto; }
.rv-modal-actions { margin-top: 12px; }

@media (max-width: 768px) {
  .review-view { padding: 0; }
  .view-head { flex-direction: column; align-items: stretch; gap: 10px; }
  .view-actions { flex-wrap: wrap; }
  .view-actions :deep(.n-button) { flex: 1 1 auto; }
  .rv-cards { grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); }
  .rv-toolbar { flex-direction: column; align-items: stretch; gap: 10px; }
  .rv-filter-block { flex-direction: column; align-items: stretch; gap: 10px; }
  .rv-search { width: 100%; }
  .rv-grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
  .rv-modal-body { flex-direction: column; }
  .rv-modal-side { flex: 1 1 auto; }
}
</style>
