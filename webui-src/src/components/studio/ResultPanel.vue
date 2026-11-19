<template>
  <div class="result-panel">
    <div class="canvas" :class="{ busy }">
      <!-- 成图（忙时保留上一次的图，加载层覆盖在上） -->
      <img
        v-if="currentSha && currentUrl"
        :src="currentUrl"
        class="canvas-img"
        @click="!busy && emit('view', currentSha)"
      />
      <!-- 加载层（v7.9.3 重做：毛玻璃 + 自旋环 + 不确定进度条 + 分级文案） -->
      <div v-if="busy" class="canvas-loading">
        <div class="ring"></div>
        <div class="ld-title">{{ queued ? "排队中" : "正在生成" }}</div>
        <div class="ld-sub">
          {{ queued ? "前面的任务画完就轮到这张" : "ComfyUI 正在绘制" }}
          <template v-if="activeElapsed !== null"> · 已用时 {{ activeElapsed }}s</template>
        </div>
        <div class="ld-bar"><div class="ld-bar-fill"></div></div>
      </div>
      <!-- 空态 -->
      <div v-else-if="!currentSha" class="canvas-empty">
        <div class="empty-icon">🎨</div>
        <div class="empty-title">在这里看到你的图</div>
        <div class="empty-sub">左侧配置好工作流与提示词后，点「开始生图」</div>
      </div>
    </div>

    <!-- 操作条 -->
    <div v-if="currentSha && !busy" class="canvas-actions">
      <n-button size="small" type="primary" ghost @click="emit('view', currentSha)">🔍 大图</n-button>
      <n-button size="small" :loading="downloading" @click="download">⬇ 下载</n-button>
      <n-button size="small" @click="emit('rerun')">🎲 换种子重跑</n-button>
      <n-button size="small" :loading="refing" @click="useAsRef">🖼 用作图生图</n-button>
      <n-button size="small" quaternary @click="copyPrompt">📋 复制提示词</n-button>
      <n-button size="small" quaternary :loading="transLoading" @click="openTranslation">🇨🇳 看译文</n-button>
    </div>

    <!-- 会话成果条（v7.9.5：支持单张移除与新建会话，仅页面级——成品仍在图库） -->
    <div class="strip-wrap">
      <div class="strip-label">
        <span>本次会话（{{ results.length }} 张）</span>
        <n-popconfirm @positive-click="emit('newSession')">
          <template #trigger>
            <n-button size="tiny" quaternary type="primary">🗑 新建会话</n-button>
          </template>
          清空本条列表并从头开始？（成品图仍保留在图库）
        </n-popconfirm>
      </div>
      <div v-if="results.length" class="strip">
        <div
          v-for="(r, i) in results"
          :key="r.sha + i"
          class="strip-item"
          :class="{ active: r.sha === currentSha }"
          @click="emit('pick', r)"
        >
          <img :src="urlMap[r.sha] || ''" class="strip-thumb" loading="lazy" />
          <button class="strip-remove" title="从本条移除（不影响图库）" @click.stop="emit('remove', i)">×</button>
        </div>
      </div>
      <div v-else class="strip-empty">还没有成果——出一张图，或从右侧「最近成品」挑一张接着改</div>
    </div>

    <!-- 提示词中文译文（v7.10.1：点「看译文」才请求，英文→中文，按需触发） -->
    <n-modal
      :show="transShow"
      preset="card"
      title="提示词中文译文"
      :bordered="false"
      style="width: min(680px, 92vw)"
      :mask-closable="true"
      @update:show="(v: boolean) => (transShow = v)"
    >
      <div v-if="transLoading" class="trans-loading">
        <span class="trans-spin"></span> 正在翻译，请稍候…
      </div>
      <template v-else>
        <p class="trans-hint">下面是这张图所用英文提示词的中文意思（按需翻译，仅供参考，不影响实际出图）：</p>
        <div class="trans-label">原英文提示词</div>
        <div class="trans-en">{{ transSource }}</div>
        <div class="trans-label trans-label-zh">中文译文</div>
        <div v-if="transError" class="trans-error">{{ transError }}</div>
        <div v-else class="trans-zh">{{ transText }}</div>
        <div class="trans-foot">翻译模型复用配置里的 translate_llm_model（留空走默认对话模型）</div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { downloadStudioImage, studioApi } from "@/api/studio";
import { useMessage } from "naive-ui";
import type { StudioGenerateParams, StudioJob } from "@/api/studio";

const props = defineProps<{
  currentSha: string;
  currentUrl: string;
  busy: boolean;
  activeHint: string;
  activeElapsed: number | null;
  /** 会话成果（新→旧） */
  results: Array<{ sha: string; params: Partial<StudioGenerateParams> | undefined; jobId: string }>;
  urlMap: Record<string, string>;
}>();

const emit = defineEmits<{
  (e: "view", sha: string): void;
  (e: "rerun"): void;
  (e: "useAsRef", sha: string): void;
  (e: "pick", r: { sha: string; params: Partial<StudioGenerateParams> | undefined; jobId: string }): void;
  (e: "remove", index: number): void;
  (e: "newSession"): void;
}>();

const message = useMessage();
const downloading = ref(false);
const refing = ref(false);

// 提示词译文：点「看译文」才请求（按需触发），按提示词文本缓存避免重复消耗 LLM
const transShow = ref(false);
const transLoading = ref(false);
const transText = ref("");
const transError = ref("");
const transSource = ref("");
const transCache = new Map<string, string>();

/** 当前忙态是排队还是绘制中（activeHint 由父组件按 activeJob.state 生成） */
const queued = computed(() => props.activeHint.includes("排队"));

async function download() {
  if (!props.currentSha) return;
  downloading.value = true;
  try {
    await downloadStudioImage(props.currentSha);
    message.success("已下载");
  } catch (e: any) {
    message.error(e?.message || "下载失败");
  } finally {
    downloading.value = false;
  }
}

async function useAsRef() {
  if (!props.currentSha) return;
  refing.value = true;
  emit("useAsRef", props.currentSha);
  refing.value = false;
}

async function copyPrompt() {
  const cur = props.results.find((r) => r.sha === props.currentSha);
  const text = cur?.params?.positive || "";
  if (!text) {
    message.warning("这张图没有提示词信息");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    message.success("提示词已复制");
  } catch {
    message.error("复制失败（浏览器剪贴板不可用）");
  }
}

function currentPrompt(): string {
  const cur = props.results.find((r) => r.sha === props.currentSha);
  return cur?.params?.positive || "";
}

async function openTranslation() {
  if (transLoading.value) return;
  const text = currentPrompt();
  if (!text) {
    message.warning("这张图没有提示词信息");
    return;
  }
  transSource.value = text;
  transError.value = "";
  transShow.value = true;
  const cached = transCache.get(text);
  if (cached !== undefined) {
    transText.value = cached;
    return;
  }
  transText.value = "";
  transLoading.value = true;
  try {
    const d = await studioApi.translatePrompt(text);
    const out = (d?.text || "").trim();
    if (!out) throw new Error("译文为空");
    transText.value = out;
    transCache.set(text, out);
  } catch (e: any) {
    transText.value = "";
    transError.value = e?.message || "翻译失败，请稍后重试";
  } finally {
    transLoading.value = false;
  }
}
</script>

<style scoped>
/* v7.9.3：中间列弹性布局——画布占满除提示词区以外的全部高度 */
.result-panel {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}
.canvas {
  position: relative;
  flex: 1 1 auto;
  min-height: 160px;
  border-radius: 14px;
  border: 1px solid var(--border-color);
  background: color-mix(in srgb, var(--accent) 4%, var(--bg-panel));
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.canvas-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  cursor: zoom-in;
  display: block;
}
/* 加载层：毛玻璃 + 自旋环 + 不确定进度条（v7.9.3 重做） */
.canvas-loading {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: color-mix(in srgb, var(--bg-body) 45%, transparent);
  backdrop-filter: blur(10px) saturate(1.05);
  -webkit-backdrop-filter: blur(10px) saturate(1.05);
}
.ring {
  width: 46px;
  height: 46px;
  border-radius: 50%;
  border: 4px solid color-mix(in srgb, var(--accent) 18%, transparent);
  border-top-color: var(--accent);
  animation: ld-spin 0.9s linear infinite;
}
@keyframes ld-spin { to { transform: rotate(360deg); } }
.ld-title { font-size: 15px; font-weight: 600; color: var(--text-main); }
.ld-sub { font-size: 12px; color: var(--text-sub); }
.ld-bar {
  width: 180px;
  height: 4px;
  border-radius: 999px;
  overflow: hidden;
  background: color-mix(in srgb, var(--accent) 15%, transparent);
}
.ld-bar-fill {
  width: 38%;
  height: 100%;
  background: var(--accent);
  border-radius: 999px;
  animation: ld-slide 1.4s ease-in-out infinite;
}
@keyframes ld-slide {
  0% { transform: translateX(-110%); }
  100% { transform: translateX(370%); }
}
.canvas-empty { text-align: center; padding: 40px 20px; }
.empty-icon { font-size: 44px; }
.empty-title { margin-top: 8px; font-size: 15px; font-weight: 600; color: var(--text-main); }
.empty-sub { margin-top: 4px; font-size: 12px; color: var(--text-sub); }
.canvas-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.strip-wrap { min-width: 0; flex: 0 0 auto; }
.strip-label {
  font-size: 12px;
  color: var(--text-sub);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.strip-item {
  position: relative;
  flex: none;
  width: 74px;
  height: 74px;
  border-radius: 10px;
  overflow: hidden;
  border: 2px solid var(--border-color);
  cursor: pointer;
  transition: border-color 0.15s;
}
.strip-remove {
  position: absolute;
  right: 2px;
  top: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 12px;
  line-height: 16px;
  cursor: pointer;
  padding: 0;
  display: none;
}
.strip-item:hover .strip-remove { display: block; }
.strip-remove:hover { background: #e86f9c; }
.strip-empty { font-size: 11px; color: var(--text-sub); line-height: 1.6; padding: 4px 0; }
.strip-item.active, .strip-item:hover { border-color: var(--accent); }
.strip-thumb { width: 100%; height: 100%; object-fit: cover; display: block; }
/* 提示词译文弹窗（v7.10.1） */
.trans-loading {
  display: flex; align-items: center; gap: 10px;
  padding: 30px 4px; color: var(--text-sub); font-size: 13px;
}
.trans-spin {
  width: 16px; height: 16px; border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--accent) 25%, transparent);
  border-top-color: var(--accent);
  animation: trans-spin 0.8s linear infinite;
}
@keyframes trans-spin { to { transform: rotate(360deg); } }
.trans-hint { margin: 0 0 14px; font-size: 12px; color: var(--text-sub); line-height: 1.6; }
.trans-label { font-size: 12px; font-weight: 600; color: var(--text-sub); margin-bottom: 6px; }
.trans-label-zh { margin-top: 16px; color: var(--accent); }
.trans-en, .trans-zh {
  max-height: 240px; overflow: auto; padding: 12px 14px; border-radius: 10px;
  background: color-mix(in srgb, var(--accent) 5%, var(--bg-panel));
  border: 1px solid var(--border-color);
  font-size: 13px; line-height: 1.75; white-space: pre-wrap; word-break: break-word;
  user-select: text;
}
.trans-en { color: var(--text-sub); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.trans-zh { color: var(--text-main); font-size: 14px; }
.trans-error {
  padding: 12px 14px; border-radius: 10px; font-size: 13px; line-height: 1.6;
  color: #e86f9c; background: color-mix(in srgb, #e86f9c 10%, transparent);
}
.trans-foot { margin-top: 14px; font-size: 11px; color: var(--text-sub); opacity: 0.8; }
</style>
