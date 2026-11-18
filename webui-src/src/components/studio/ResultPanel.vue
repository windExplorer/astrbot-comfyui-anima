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
    </div>

    <!-- 会话成果条 -->
    <div v-if="results.length" class="strip-wrap">
      <div class="strip-label">本次会话（{{ results.length }} 张）</div>
      <div class="strip">
        <div
          v-for="(r, i) in results"
          :key="r.sha + i"
          class="strip-item"
          :class="{ active: r.sha === currentSha }"
          @click="emit('pick', r)"
        >
          <img :src="urlMap[r.sha] || ''" class="strip-thumb" loading="lazy" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { downloadStudioImage } from "@/api/studio";
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
}>();

const message = useMessage();
const downloading = ref(false);
const refing = ref(false);

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
.strip-label { font-size: 12px; color: var(--text-sub); margin-bottom: 6px; }
.strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.strip-item {
  flex: none;
  width: 74px;
  height: 74px;
  border-radius: 10px;
  overflow: hidden;
  border: 2px solid var(--border-color);
  cursor: pointer;
  transition: border-color 0.15s;
}
.strip-item.active, .strip-item:hover { border-color: var(--accent); }
.strip-thumb { width: 100%; height: 100%; object-fit: cover; display: block; }
</style>
