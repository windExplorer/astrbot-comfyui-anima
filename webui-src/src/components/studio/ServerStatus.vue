<template>
  <div class="srv-panel">
    <div class="srv-head"><span class="srv-title">🖥️ 服务器</span></div>
    <div v-if="!servers.length" class="srv-empty">未配置 ComfyUI 服务器</div>
    <div v-else class="srv-list">
      <div v-for="s in servers" :key="s.name" class="srv-row">
        <span class="srv-dot" :class="s.ok ? (busy(s) ? 'busy' : 'idle') : 'down'" />
        <span class="srv-name" :title="s.name">{{ s.name }}</span>
        <span class="srv-stat">
          <template v-if="s.ok">
            <span v-if="busy(s)" class="stat-busy">运行 {{ s.running }} · 排队 {{ s.pending }}</span>
            <span v-else class="stat-idle">空闲</span>
          </template>
          <span v-else class="stat-down">查询失败</span>
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { StudioQueueServer } from "@/api/studio";

defineProps<{
  servers: StudioQueueServer[];
}>();

function busy(s: StudioQueueServer): boolean {
  return (s.running ?? 0) > 0 || (s.pending ?? 0) > 0;
}
</script>

<style scoped>
.srv-panel {
  flex: 0 0 auto;
  background: var(--bg-panel);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 10px 12px;
}
.srv-head { margin-bottom: 6px; }
.srv-title { font-size: 12px; font-weight: 600; color: var(--text-main); }
.srv-empty { font-size: 11px; color: var(--text-sub); }
.srv-list { display: flex; flex-direction: column; gap: 6px; }
.srv-row { display: flex; align-items: center; gap: 7px; min-width: 0; }
.srv-dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #b8c0cc;
}
.srv-dot.idle { background: #8fd0b0; }
.srv-dot.busy { background: var(--accent); animation: srv-pulse 1.2s infinite; }
.srv-dot.down { background: #d05050; }
@keyframes srv-pulse { 50% { opacity: 0.35; } }
.srv-name {
  font-size: 12px;
  color: var(--text-main);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.srv-stat { margin-left: auto; flex: none; font-size: 11px; color: var(--text-sub); }
.stat-busy { color: var(--accent); }
.stat-down { color: #d05050; }
</style>
