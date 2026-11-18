<template>
  <div class="job-panel">
    <div class="job-head">
      <span class="job-title">
        任务
        <n-badge v-if="activeCount" :value="activeCount" type="info" style="margin-left: 4px" />
      </span>
      <n-popconfirm @positive-click="emit('interrupt')">
        <template #trigger>
          <n-button size="tiny" type="error" ghost :disabled="!runningCount">中断当前</n-button>
        </template>
        中断是全局操作，会打断 ComfyUI 上正在生成的任何任务（含聊天侧出图）。确定？
      </n-popconfirm>
    </div>

    <div v-if="!jobs.length" class="job-empty">
      还没有任务。左侧选工作流、写提示词后点「开始生图」。
      <div class="job-empty-sub">任务历史仅保留在本页（重启清空）；成品图已自动存入图库。</div>
    </div>

    <div v-else class="job-list">
      <div v-for="j in jobs" :key="j.id" class="job-item" :class="'st-' + j.state">
        <div class="job-row1">
          <span class="st-dot" :class="j.state" />
          <span class="job-state">{{ stateLabel(j) }}</span>
          <span class="job-wf">{{ j.params?.workflow || "?" }}</span>
          <span class="job-mode">{{ modeLabel(j.params?.mode) }}</span>
          <span v-if="j.elapsed" class="job-elapsed">{{ j.elapsed }}s</span>
          <div class="job-actions">
            <n-button v-if="j.state === 'queued' || j.state === 'running'" size="tiny" quaternary @click="emit('discard', j.id)">
              放弃
            </n-button>
            <n-button v-if="j.state === 'done' && j.images.length" size="tiny" quaternary @click="emit('view', j.images[0].sha)">
              看图
            </n-button>
            <n-button size="tiny" quaternary @click="emit('reuse', j)">复用参数</n-button>
          </div>
        </div>
        <div v-if="j.state === 'error'" class="job-error">{{ j.error || "未知错误" }}</div>
        <div v-if="j.state === 'done' && j.images.length" class="job-thumbs">
          <img
            v-for="img in j.images.slice(0, 6)"
            :key="img.sha"
            :src="urlMap[img.sha] || ''"
            class="job-thumb"
            loading="lazy"
            @click="emit('view', img.sha)"
          />
        </div>
        <div v-if="j.params?.positive" class="job-prompt" :title="j.params.positive">{{ j.params.positive }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { StudioJob } from "@/api/studio";

const props = defineProps<{
  jobs: StudioJob[];
  /** sha → 展示 URL（父组件预生成的缩略地址） */
  urlMap: Record<string, string>;
}>();

const emit = defineEmits<{
  (e: "discard", id: string): void;
  (e: "interrupt"): void;
  (e: "reuse", job: StudioJob): void;
  (e: "view", sha: string): void;
}>();

const activeCount = computed(
  () => props.jobs.filter((j) => j.state === "queued" || j.state === "running").length
);
const runningCount = computed(() => props.jobs.filter((j) => j.state === "running").length);

function stateLabel(j: StudioJob): string {
  switch (j.state) {
    case "queued": return "排队中";
    case "running": return "生成中";
    case "done": return `完成 ×${j.images.length}`;
    case "error": return "失败";
    case "discarded": return "已放弃";
    default: return j.state;
  }
}

function modeLabel(mode?: string): string {
  if (mode === "img2img") return "图生图";
  if (mode === "multi_ref") return "多参考";
  return "文生图";
}
</script>

<style scoped>
.job-panel { display: flex; flex-direction: column; gap: 8px; min-width: 0; min-height: 0; }
.job-head { display: flex; align-items: center; justify-content: space-between; }
.job-title { font-size: 13px; font-weight: 600; color: var(--text-main); display: flex; align-items: center; }
.job-empty {
  font-size: 12px;
  color: var(--text-sub);
  background: var(--bg-panel);
  border: 1px dashed var(--border-color);
  border-radius: 10px;
  padding: 12px;
  line-height: 1.6;
}
.job-empty-sub { margin-top: 4px; font-size: 11px; opacity: 0.8; }
.job-list { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; gap: 8px; overflow-y: auto; padding-right: 2px; }
.job-item {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-panel);
  padding: 8px 10px;
  min-width: 0;
}
.job-item.st-error { border-left: 3px solid #d05050; }
.job-item.st-done { border-left: 3px solid #8fd0b0; }
.job-row1 { display: flex; align-items: center; gap: 6px; min-width: 0; }
.st-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: #b8c0cc; }
.st-dot.queued { background: #7ea4e0; animation: pulse 1.2s infinite; }
.st-dot.running { background: var(--accent); animation: pulse 1.2s infinite; }
.st-dot.done { background: #8fd0b0; }
.st-dot.error { background: #d05050; }
.st-dot.discarded { background: #b8c0cc; }
@keyframes pulse { 50% { opacity: 0.35; } }
.job-state { font-size: 12px; font-weight: 600; color: var(--text-main); flex: none; }
.job-wf {
  font-size: 11px;
  color: var(--text-sub);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.job-mode { flex: none; font-size: 10px; padding: 0 6px; border-radius: 999px; background: color-mix(in srgb, var(--accent) 12%, transparent); color: var(--text-main); }
.job-elapsed { flex: none; font-size: 11px; color: var(--text-sub); }
.job-actions { margin-left: auto; flex: none; display: flex; gap: 2px; }
.job-error {
  margin-top: 6px;
  font-size: 12px;
  color: #d05050;
  background: color-mix(in srgb, #d05050 8%, transparent);
  border-radius: 8px;
  padding: 5px 8px;
  word-break: break-all;
}
.job-thumbs { margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }
.job-thumb {
  width: 56px;
  height: 56px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  cursor: pointer;
}
.job-thumb:hover { border-color: var(--accent); }
.job-prompt {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-sub);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
