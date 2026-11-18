<template>
  <div class="rg-panel">
    <div class="rg-head">
      <span class="rg-title">🖼️ 最近成品</span>
      <n-button size="tiny" quaternary :loading="loading" @click="emit('refresh')">刷新</n-button>
    </div>
    <div v-if="!items.length" class="rg-empty">
      {{ loading ? "加载中…" : "图库还没有成品——出一张图试试" }}
    </div>
    <div v-else class="rg-grid">
      <div
        v-for="(it, i) in items"
        :key="it.sha || i"
        class="rg-item"
        :title="titleOf(it)"
        @click="emit('pick', it.sha)"
      >
        <img :src="urlMap[it.sha] || ''" class="rg-thumb" loading="lazy" />
        <div class="rg-hover" @click.stop>
          <button class="rg-act" title="看大图" @click="emit('view', it.sha)">🔍</button>
          <button class="rg-act" title="用作图生图参考" @click="emit('useAsRef', it.sha)">🖼️</button>
        </div>
      </div>
    </div>
    <div v-if="total > items.length" class="rg-more">
      图库里还有更多（共 {{ total }} 张）——到<a class="rg-link" href="#/gallery">图库页</a>看全部
    </div>
  </div>
</template>

<script setup lang="ts">
import type { StudioGalleryItem } from "@/api/studio";

const props = defineProps<{
  items: StudioGalleryItem[];
  total?: number;
  loading?: boolean;
  /** sha → 缩略图 URL（父组件统一预生成） */
  urlMap: Record<string, string>;
}>();

const emit = defineEmits<{
  (e: "view", sha: string): void;
  (e: "useAsRef", sha: string): void;
  (e: "pick", sha: string): void;
  (e: "refresh"): void;
}>();

function titleOf(it: StudioGalleryItem): string {
  const parts: string[] = [];
  if (it.workflow) parts.push(String(it.workflow));
  if (it.user_name) parts.push(String(it.user_name));
  const p = (it.prompt || "").trim();
  if (p) parts.push(p.slice(0, 60));
  return parts.join(" · ") || (it.sha || "").slice(0, 12);
}
</script>

<style scoped>
.rg-panel {
  flex: 0 0 auto;
  background: var(--bg-panel);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 10px 12px;
}
.rg-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.rg-title { font-size: 12px; font-weight: 600; color: var(--text-main); }
.rg-empty { font-size: 11px; color: var(--text-sub); line-height: 1.6; }
.rg-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}
.rg-item {
  position: relative;
  aspect-ratio: 1;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--border-color);
  cursor: pointer;
  background: color-mix(in srgb, var(--accent) 6%, var(--bg-body));
}
.rg-thumb { width: 100%; height: 100%; object-fit: cover; display: block; }
.rg-hover {
  position: absolute;
  inset: auto 0 0 0;
  display: none;
  justify-content: center;
  gap: 6px;
  padding: 4px 0;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.55));
}
.rg-item:hover .rg-hover { display: flex; }
.rg-act {
  border: none;
  background: rgba(255, 255, 255, 0.85);
  border-radius: 6px;
  width: 24px;
  height: 22px;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
}
.rg-act:hover { background: var(--accent); }
.rg-more { margin-top: 8px; font-size: 11px; color: var(--text-sub); }
.rg-link { color: var(--accent); text-decoration: none; }
.rg-link:hover { text-decoration: underline; }
</style>
