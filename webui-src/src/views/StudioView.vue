<template>
  <div class="studio-view">
    <n-alert v-if="loadErr" type="error" style="margin-bottom: 14px" closable>
      {{ loadErr }}
      <n-button size="tiny" style="margin-left: 8px" @click="loadAll">重试</n-button>
    </n-alert>

    <!-- v7.9.2：外壳 .app-shell/.app-content 都是 overflow:hidden，滚动必须由页面
         内部管理（同 ConfigView 的 .cfg-scroll 模式），否则长页面滚不动 -->
    <div class="studio-scroll">
      <n-spin :show="loading && !overview">
      <div class="studio-grid">
        <!-- 左：生成配置 -->
        <div class="col col-left">
          <div class="panel">
            <div class="mode-tabs">
              <n-radio-group :value="mode" size="small" @update:value="onModeChange">
                <n-radio-button value="t2i">✏️ 文生图</n-radio-button>
                <n-radio-button value="img2img">🖼️ 图生图</n-radio-button>
              </n-radio-group>
            </div>

            <template v-if="mode !== 't2i'">
              <div class="sec-label">参考图（{{ refs.length }}/{{ maxRefs }}）</div>
              <RefUploadPanel
                ref="refPanel"
                v-model="refs"
                :max-refs="maxRefs"
              />
              <!-- v7.9.1：多参考并入图生图——参考图可放 1..N 张，是否走多参考注入
                   由工作流能力自动决定（带 TextEncodeQwenImage21 类节点且 >1 张时） -->
              <div class="sec-hint">
                可放多张：工作流支持多参考时会按「图1」「图2」逐张注入（顺序即列表顺序），
                提示词可直接引用；单图工作流取第一张。
              </div>
            </template>

            <div class="sec-label">工作流</div>
            <WorkflowPicker v-model="selectedWf" :workflows="ovWorkflows" :mode="mode" />

            <div class="sec-label">LoRA（{{ loraSel.length }}）</div>
            <LoraPicker v-model="loraSel" :loras="ovLoras" />

            <div class="sec-label">生成参数</div>
            <ParamsPanel
              :params="p"
              :mode="mode"
              :workflow="selectedWorkflow"
              :tiers="ovTiers"
              :ratios="ovRatios"
            />

            <div class="gen-row">
              <n-button
                type="primary"
                size="large"
                block
                :loading="submitting"
                :disabled="genDisabled"
                @click="submit"
              >
                🎨 开始生图
              </n-button>
            </div>
            <div v-if="ovServers.length" class="server-line">
              后端：<span v-for="s in ovServers" :key="s.name" class="chip dim">{{ s.name }}</span>
            </div>
          </div>
        </div>

        <!-- 中：画布 + 成果条 -->
        <div class="col col-center">
          <ResultPanel
            :current-sha="currentSha"
            :current-url="bigUrl"
            :busy="activeCount > 0"
            :active-hint="activeHint"
            :active-elapsed="activeJob?.elapsed ?? null"
            :results="sessionResults"
            :url-map="thumbMap"
            @view="openViewer"
            @rerun="rerun"
            @use-as-ref="useAsRef"
            @pick="onPick"
          />
        </div>

        <!-- 右：任务 -->
        <div class="col col-right">
          <JobPanel
            :jobs="jobs"
            :url-map="thumbMap"
            @discard="discardJob"
            @interrupt="interruptAll"
            @reuse="reuseParams"
            @view="openViewer"
          />
        </div>
      </div>

      <!-- 底：提示词区（全宽） -->
      <div class="prompt-band">
        <PromptPanel
          :positive="p.positive"
          :negative="p.negative"
          :llm-ready="overview?.llm_ready ?? false"
          :mode="mode"
          :ref-count="refs.length"
          :workflow="selectedWorkflow"
          :quick-templates="quickTemplates"
          :selected-lora-names="loraSel.map((s) => s.name)"
          @update:positive="(v: string) => (p.positive = v)"
          @update:negative="(v: string) => (p.negative = v)"
          @open-templates="tplShow = true"
        />
      </div>
      </n-spin>
    </div>

    <TemplateDrawer
      v-model:show="tplShow"
      :templates="overview?.templates || []"
      @use="applyTemplate"
      @changed="reloadTemplates"
    />

    <ImageViewer
      v-model:show="viewerShow"
      :sha="viewerSha"
      :images="viewerImages"
      :index="viewerIndex"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useMessage, useDialog } from "naive-ui";
import ImageViewer from "@/components/ImageViewer.vue";
import WorkflowPicker from "@/components/studio/WorkflowPicker.vue";
import RefUploadPanel from "@/components/studio/RefUploadPanel.vue";
import LoraPicker from "@/components/studio/LoraPicker.vue";
import PromptPanel from "@/components/studio/PromptPanel.vue";
import TemplateDrawer from "@/components/studio/TemplateDrawer.vue";
import ParamsPanel from "@/components/studio/ParamsPanel.vue";
import JobPanel from "@/components/studio/JobPanel.vue";
import ResultPanel from "@/components/studio/ResultPanel.vue";
import {
  studioApi,
  studioImg,
  type StudioGenerateParams,
  type StudioJob,
  type StudioLoraSel,
  type StudioOverview,
  type StudioRefItem,
  type StudioTemplate,
} from "@/api/studio";
import { lsGet, lsSet } from "@/api/storage";
import { useRefresh } from "@/composables/useRefresh";

const message = useMessage();
const dialog = useDialog();

// ── 状态 ────────────────────────────────────────────────────────── //
const loading = ref(false);
const loadErr = ref("");
const overview = ref<StudioOverview | null>(null);

const mode = ref<"t2i" | "img2img">("t2i");
const selectedWf = ref("");
const refs = ref<StudioRefItem[]>([]);
const loraSel = ref<StudioLoraSel[]>([]);
const submitting = ref(false);

const p = reactive<StudioGenerateParams>({
  mode: "t2i",
  workflow: "",
  positive: "",
  negative: "",
  width: null,
  height: null,
  seed: null,
  steps: null,
  cfg: null,
  denoise: null,
  sampler: "",
  noise_schedule: "",
  custom_size: null,
  llm_refine: true,
  loras: [],
  ref_ids: [],
});

const jobs = ref<StudioJob[]>([]);
const processed = new Set<string>(); // 已把成果并入会话条的任务 id
const sessionResults = ref<Array<{ sha: string; params: Partial<StudioGenerateParams> | undefined; jobId: string }>>([]);
const currentSha = ref("");
const thumbMap = reactive<Record<string, string>>({});
const bigMap = reactive<Record<string, string>>({});

const tplShow = ref(false);
const viewerShow = ref(false);
const viewerSha = ref("");
const viewerIndex = ref<number | undefined>(undefined);

const refPanel = ref<InstanceType<typeof RefUploadPanel> | null>(null);
let pollTimer: number | null = null;

const SESSION_KEY = "anima_studio_session_v1";

// ── 派生 ────────────────────────────────────────────────────────── //
const ovWorkflows = computed(() => overview.value?.workflows || []);
const ovLoras = computed(() => overview.value?.loras || []);
const ovTiers = computed(() => overview.value?.size_tiers?.tiers || []);
const ovRatios = computed(() => overview.value?.size_tiers?.ratios || []);
const ovServers = computed(() => overview.value?.servers || []);
const maxRefs = computed(() => {
  if (mode.value === "t2i") return 1;
  // v7.9.1：图生图 1..N 张（N=工作流条目 max_refs，缺省全局值）；
  // >1 张且工作流支持 multi_image 时自动走多参考注入
  if (selectedWorkflow.value?.max_refs) return Math.max(1, selectedWorkflow.value.max_refs);
  return Math.max(1, overview.value?.max_refs || 3);
});

const selectedWorkflow = computed(
  () => ovWorkflows.value.find((w) => w.name === selectedWf.value) || null
);

const quickTemplates = computed(() =>
  (overview.value?.templates || []).filter((t) => t.enabled && !t.is_negative).slice(0, 8)
);

const activeJob = computed(
  () => jobs.value.find((j) => j.state === "running" || j.state === "queued") || null
);
const activeCount = computed(() => (activeJob.value ? 1 : 0));
const activeHint = computed(() =>
  activeJob.value?.state === "queued" ? "排队中，等前面的任务画完…" : "正在生成，ComfyUI 忙碌中…"
);

const bigUrl = computed(() => (currentSha.value ? bigMap[currentSha.value] || "" : ""));

const genDisabled = computed(() => {
  if (submitting.value) return true;
  if (!selectedWf.value) return true;
  if (!p.positive.trim()) return true;
  if (mode.value === "img2img" && refs.value.length < 1) return true;
  return false;
});

const viewerImages = computed(() =>
  sessionResults.value.map((r) => ({ sha: r.sha }))
);

// ── 数据加载 ────────────────────────────────────────────────────── //
async function loadAll() {
  loading.value = true;
  loadErr.value = "";
  try {
    overview.value = await studioApi.overview();
    ensureWorkflowSelected();
    ensureUrlsForVisible();
  } catch (e: any) {
    loadErr.value = e?.message || "加载失败";
  } finally {
    loading.value = false;
  }
}

function compatibleWorkflows(m: string) {
  // v7.9.2：只认已启用的（下拉不再列出停用条目，自动选中也不落在停用项上）
  if (m === "t2i") {
    return ovWorkflows.value.filter((w) => w.enabled && (w.kind === "t2i" || w.kind === null));
  }
  return ovWorkflows.value.filter((w) => w.enabled && w.kind === "img2img");
}

function ensureWorkflowSelected() {
  const list = compatibleWorkflows(mode.value);
  if (selectedWf.value && list.some((w) => w.name === selectedWf.value)) return;
  const saved = lsGet(`anima_studio_wf_${mode.value}`) || "";
  const hit = saved && list.find((w) => w.name === saved);
  selectedWf.value = (hit || list[0])?.name || "";
  applyWorkflowDefaults(true);
}

function applyWorkflowDefaults(force = false) {
  const wf = selectedWorkflow.value;
  if (!wf) return;
  if (force || !p.positive.trim()) {
    if (wf.default_positive) p.positive = wf.default_positive;
  }
  if (force || !p.negative.trim()) {
    if (wf.default_negative) p.negative = wf.default_negative;
  }
}

watch(selectedWf, (v, old) => {
  if (v && v !== old) {
    lsSet(`anima_studio_wf_${mode.value}`, v);
    applyWorkflowDefaults(false);
  }
});

function onModeChange(m: any) {
  // v7.9.1：multi_ref 并入 img2img（复用旧会话存档里的历史字面量时归一化）
  if (m === "multi_ref") m = "img2img";
  mode.value = m;
  p.mode = m;
  if (m === "t2i") {
    refs.value = [];
  }
  ensureWorkflowSelected();
}

// ── 提交 ────────────────────────────────────────────────────────── //
async function submit() {
  if (genDisabled.value) return;
  submitting.value = true;
  try {
    p.mode = mode.value;
    p.workflow = selectedWf.value;
    p.loras = loraSel.value.map((s) => ({ ...s }));
    p.ref_ids = refs.value.map((r) => r.ref_id);
    await studioApi.generate({ ...p });
    message.success("任务已提交，出图后自动进入成果条");
    await refreshJobs();
  } catch (e: any) {
    message.error(e?.message || "提交失败");
  } finally {
    submitting.value = false;
  }
}

// ── 任务轮询与成果 ──────────────────────────────────────────────── //
async function refreshJobs() {
  try {
    const d = await studioApi.jobs(30);
    jobs.value = d.jobs || [];
    collectDone();
  } catch {
    /* 轮询失败静默（下次 tick 再试） */
  }
}

async function tick() {
  if (activeCount.value > 0 || jobs.value.some((j) => j.state === "running" || j.state === "queued")) {
    await refreshJobs();
  }
}

async function collectDone() {
  let changed = false;
  for (const j of jobs.value) {
    if (j.state === "done" && !processed.has(j.id)) {
      processed.add(j.id);
      for (const img of j.images.slice().reverse()) {
        if (!img.sha) continue;
        sessionResults.value.unshift({ sha: img.sha, params: j.params, jobId: j.id });
        changed = true;
      }
      if (j.images[0]?.sha) setCurrent(j.images[0].sha);
      if (j.params?.workflow) message.success(`「${j.params.workflow}」出图完成`);
    }
  }
  if (sessionResults.value.length > 40) sessionResults.value = sessionResults.value.slice(0, 40);
  if (changed) persistSession();
}

function setCurrent(sha: string) {
  currentSha.value = sha;
  ensureUrl(sha, 1024, bigMap);
  ensureUrl(sha, 400, thumbMap);
}

function onPick(r: { sha: string }) {
  setCurrent(r.sha);
}

function ensureUrl(sha: string, size: number, map: Record<string, string>) {
  if (!sha || map[sha]) return;
  studioImg(sha, size)
    .then((u) => {
      map[sha] = u;
    })
    .catch(() => {});
}

function ensureUrlsForVisible() {
  for (const r of sessionResults.value) ensureUrl(r.sha, 400, thumbMap);
  if (currentSha.value) {
    ensureUrl(currentSha.value, 400, thumbMap);
    ensureUrl(currentSha.value, 1024, bigMap);
  }
}

function persistSession() {
  try {
    const data = {
      results: sessionResults.value.slice(0, 15),
      processed: Array.from(processed).slice(-60),
    };
    lsSet(SESSION_KEY, JSON.stringify(data));
  } catch {
    /* 内存兜底由 storage 层处理 */
  }
}

function restoreSession() {
  try {
    const raw = lsGet(SESSION_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (Array.isArray(data?.results)) {
      sessionResults.value = data.results.filter((r: any) => r && r.sha);
      for (const id of data.processed || []) processed.add(id);
      if (sessionResults.value.length) setCurrent(sessionResults.value[0].sha);
    }
  } catch {
    /* 忽略损坏的存档 */
  }
}

// ── 操作 ────────────────────────────────────────────────────────── //
async function discardJob(id: string) {
  try {
    await studioApi.discard(id);
    message.info("已放弃等待（ComfyUI 侧任务可能仍会跑完并入图库）");
    await refreshJobs();
  } catch (e: any) {
    message.error(e?.message || "操作失败");
  }
}

function interruptAll() {
  dialog.warning({
    title: "中断确认",
    content: "中断是全局操作：会打断 ComfyUI 上正在生成的任何任务（包括聊天侧出图）。确定中断吗？",
    positiveText: "中断",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        const d = await studioApi.interrupt();
        const parts = Object.entries(d.results || {}).map(([k, v]) => `${k}:${v ? "✓" : "✗"}`);
        message.info(`已发送中断请求（${parts.join("，") || "无后端"}）`);
        await refreshJobs();
      } catch (e: any) {
        message.error(e?.message || "中断失败");
      }
    },
  });
}

function reuseParams(job: StudioJob) {
  const q = job.params || {};
  if (q.mode && q.mode !== mode.value) onModeChange(q.mode);
  if (q.workflow && ovWorkflows.value.some((w) => w.name === q.workflow)) {
    selectedWf.value = q.workflow;
  }
  p.positive = q.positive || "";
  p.negative = q.negative || "";
  p.width = q.width ?? null;
  p.height = q.height ?? null;
  p.seed = q.seed ?? null;
  p.steps = q.steps ?? null;
  p.cfg = q.cfg ?? null;
  p.denoise = q.denoise ?? null;
  p.sampler = q.sampler || "";
  p.noise_schedule = q.noise_schedule || "";
  p.custom_size = q.custom_size ?? null;
  p.llm_refine = q.llm_refine ?? true;
  loraSel.value = (q.loras || []).map((s: any) => ({ name: s.name, weight: s.weight, preset: s.preset || "" }));
  if (q.mode && q.mode !== "t2i") {
    message.info("参数已复用；参考图是临时文件，需要重新上传");
  } else {
    message.success("参数已复用");
  }
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

function rerun() {
  const cur = sessionResults.value.find((r) => r.sha === currentSha.value);
  if (!cur?.params?.workflow) {
    message.warning("找不到本次参数，无法重跑");
    return;
  }
  reuseParams({ id: "", state: "done", elapsed: null, error: "", images: [], nsfw_blocked: 0, params: cur.params, created_at: 0 } as StudioJob);
  p.seed = null; // 换种子重跑
  submit();
}

async function useAsRef(sha: string) {
  try {
    const d = await studioApi.uploadRefFromGallery(sha);
    if (mode.value === "t2i") onModeChange("img2img");
    const preview = await studioImg(sha, 320).catch(() => "");
    refs.value = [
      ...refs.value,
      { ref_id: d.ref_id, preview: preview, name: `gallery_${sha.slice(0, 8)}` },
    ].slice(0, maxRefs.value);
    message.success("已取作参考图");
  } catch (e: any) {
    message.error(e?.message || "取用失败");
  }
}

function openViewer(sha: string) {
  viewerSha.value = sha;
  const idx = sessionResults.value.findIndex((r) => r.sha === sha);
  viewerIndex.value = idx >= 0 ? idx : undefined;
  viewerShow.value = true;
}

function applyTemplate(t: StudioTemplate, target: string) {
  const piece = t.content.trim();
  if (target === "append-pos" || target === "replace-pos") {
    const cur = p.positive.trimEnd();
    p.positive =
      target === "replace-pos"
        ? piece
        : (cur + (cur && !/[，,\s]$/.test(cur) ? ", " : cur ? " " : "") + piece).trim();
  } else {
    const cur = p.negative.trimEnd();
    p.negative =
      target === "replace-neg"
        ? piece
        : (cur + (cur && !/[，,\s]$/.test(cur) ? ", " : cur ? " " : "") + piece).trim();
  }
  message.success(`已应用模板「${t.name}」`);
}

async function reloadTemplates() {
  try {
    const d = await studioApi.templates();
    if (overview.value) overview.value.templates = d.items || [];
  } catch {
    /* 静默 */
  }
}

// ── 页面级粘贴 → 参考图 ─────────────────────────────────────────── //
function onPaste(e: ClipboardEvent) {
  if (mode.value === "t2i" || !refPanel.value) return;
  const files = Array.from(e.clipboardData?.files || []).filter((f) => f.type.startsWith("image/"));
  if (files.length) {
    e.preventDefault();
    refPanel.value.addFromPaste?.(files);
  }
}

function onVisibility() {
  if (document.visibilityState === "visible") refreshJobs();
}

// ── 生命周期 ────────────────────────────────────────────────────── //
useRefresh(() => {
  loadAll();
  refreshJobs();
});

onMounted(() => {
  restoreSession();
  loadAll();
  refreshJobs();
  window.addEventListener("paste", onPaste);
  // 标签页从后台切回时立即刷一次：隐藏页的 setInterval 会被浏览器节流，
  // 只靠 1.6s 轮询会出现「切回来还显示生成中」的迟到状态。
  window.addEventListener("visibilitychange", onVisibility);
  pollTimer = window.setInterval(tick, 1600);
});

onBeforeUnmount(() => {
  window.removeEventListener("paste", onPaste);
  window.removeEventListener("visibilitychange", onVisibility);
  if (pollTimer !== null) window.clearInterval(pollTimer);
});
</script>

<style scoped>
.studio-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
/* 外壳 overflow:hidden，页面必须自带内部滚动区（对齐 ConfigView .cfg-scroll） */
.studio-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 4px 4px 24px 2px;
}
.studio-grid {
  display: grid;
  grid-template-columns: minmax(330px, 400px) minmax(0, 1fr) minmax(240px, 300px);
  gap: 14px;
  align-items: start;
}
.col { min-width: 0; }
.panel {
  background: var(--bg-panel);
  border: 1px solid var(--border-color);
  border-radius: 14px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.mode-tabs { display: flex; }
.mode-tabs :deep(.n-radio-button) { flex: 1; }
.mode-tabs :deep(.n-radio-button__label) { width: 100%; text-align: center; }
.sec-label { font-size: 13px; font-weight: 600; color: var(--text-main); margin-top: 2px; }
.sec-hint { font-size: 11px; color: var(--text-sub); line-height: 1.5; margin-top: -6px; }
.gen-row { margin-top: 4px; }
.server-line {
  font-size: 11px;
  color: var(--text-sub);
  display: flex;
  gap: 5px;
  align-items: center;
  flex-wrap: wrap;
}
.chip {
  font-size: 10px;
  padding: 0 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--text-main);
}
.chip.dim { background: color-mix(in srgb, var(--text-sub) 14%, transparent); color: var(--text-sub); }
.prompt-band {
  margin-top: 14px;
  background: var(--bg-panel);
  border: 1px solid var(--border-color);
  border-radius: 14px;
  padding: 14px;
}

@media (max-width: 1080px) {
  .studio-grid {
    grid-template-columns: 1fr;
  }
  .col-center { order: 1; }
  .col-left { order: 0; }
  .col-right { order: 2; }
}
</style>
