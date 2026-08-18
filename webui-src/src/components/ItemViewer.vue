<template>
  <teleport to="body">
    <div v-if="show" class="iviewer">
      <button class="iv-close" @click="onClose" aria-label="关闭">✕</button>
      <div class="iv-body">
        <!-- 左侧：封面大图 -->
        <div class="iv-imgs" @click.self="onClose">
          <figure class="iv-fig">
            <div class="iv-imgwrap">
              <img v-if="src" :src="src" alt="" />
              <div v-else class="iv-loading">封面加载中…</div>
            </div>
            <figcaption class="iv-cap">{{ title }}</figcaption>
          </figure>
        </div>
        <!-- 右侧：字段信息 -->
        <aside class="iv-info">
          <div class="iv-title">{{ title }}</div>
          <div class="iv-rows">
            <div v-for="f in fields" :key="f.key" class="iv-row">
              <span class="k">{{ f.key }}</span>
              <span class="v">
                <a v-if="f.href" :href="f.href" target="_blank" rel="noopener noreferrer">{{ f.value || f.href }} ↗</a>
                <span v-else-if="f.html" v-html="sanitizeHtml(String(f.value || ''))"></span>
                <template v-else>{{ f.value || "—" }}</template>
              </span>
            </div>
          </div>
        </aside>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { sanitizeHtml } from "@/utils/sanitizeHtml";

export interface ItemViewerField {
  key: string;
  value?: string | number | null;
  /** 存在时渲染为可点击外链 */
  href?: string;
  /** 为 true 时 value 按净化后的 HTML 渲染（用于富文本描述） */
  html?: boolean;
}

defineProps<{
  show: boolean;
  src?: string;
  title?: string;
  fields?: ItemViewerField[];
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
}>();

function onClose() {
  emit("update:show", false);
}
</script>

<style scoped>
.iviewer {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(8, 8, 12, 0.9);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: stretch;
}
.iv-close {
  position: fixed;
  top: 14px;
  right: 16px;
  z-index: 10;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 1.3rem;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
}
.iv-close:hover { background: rgba(0, 0, 0, 0.8); }
.iv-body {
  display: flex;
  width: 100%;
  height: 100%;
  overflow: hidden;
}
.iv-imgs {
  flex: 1 1 auto;
  display: flex;
  align-items: stretch;   /* 让 figure 撑满左侧可用高度 */
  justify-content: center;
  padding: 28px;
  min-width: 0;
  min-height: 0;
}
.iv-fig {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  width: 100%;
  max-width: 100%;
  align-items: center;
}
.iv-cap {
  flex: 0 0 auto;
  font-size: 0.74rem;
  font-weight: 700;
  padding: 3px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.1);
  color: #cfd2dc;
  letter-spacing: 0.3px;
  user-select: none;
}
.iv-imgwrap {
  flex: 1 1 auto;          /* 撑满剩余高度 */
  width: 100%;
  min-height: 0;
  background: #0c0d11;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  border-radius: 10px;
  overflow: hidden;
}
.iv-imgwrap img {
  width: 100%;            /* 铺满容器宽度 */
  height: 100%;           /* 铺满容器高度 */
  object-fit: contain;    /* 保持比例，撑满左侧（宽度或高度取较小约束） */
  display: block;
}
.iv-loading {
  color: rgba(255, 255, 255, 0.6);
  padding: 60px;
  font-size: 13px;
}
.iv-info {
  width: 380px;
  flex: 0 0 380px;
  height: 100%;
  overflow: hidden;
  padding: 20px 18px;
  border-left: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(20, 20, 28, 0.6);
  font-size: 0.84rem;
  line-height: 1.7;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}
.iv-title {
  flex: 0 0 auto;
  font-size: 1.05rem;
  font-weight: 700;
  color: #fff;
  padding-bottom: 12px;
  margin-bottom: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  word-break: break-word;
}
.iv-rows {
  flex: 1 1 auto;
  overflow: auto;
  padding-right: 4px;
}
.iv-row {
  display: flex;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}
.iv-row .k { flex: 0 0 72px; color: rgba(255, 255, 255, 0.5); }
.iv-row .v { flex: 1; color: #e6e6f0; word-break: break-word; white-space: pre-wrap; }
.iv-row .v a { color: #7aa2ff; text-decoration: none; }
.iv-row .v a:hover { text-decoration: underline; }
@media (max-width: 760px) {
  .iv-body { flex-direction: column; }
  .iv-info { width: 100%; flex: 0 0 auto; height: auto; max-height: 40vh; overflow: auto; border-left: none; border-top: 1px solid rgba(255,255,255,0.08); }
  .iv-imgwrap img { max-width: 92vw; }
}
</style>
