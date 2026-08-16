<template>
  <n-modal
    :show="show"
    style="width:max-content;max-width:94vw"
    :bordered="false"
    :mask-closable="true"
    @update:show="onClose"
  >
    <div class="preview-body">
      <div class="preview-toolbar">
        <span v-if="title" class="preview-title">{{ title }}</span>
        <button class="preview-close" @click="onClose">✕</button>
      </div>
      <div class="preview-imgwrap">
        <img v-if="src" :src="src" alt="" />
      </div>
      <div v-if="!src && !loading" class="preview-empty">图片加载中…</div>
    </div>
  </n-modal>
</template>

<script setup lang="ts">
import { NModal } from "naive-ui";

defineProps<{
  show: boolean;
  src?: string;
  title?: string;
  loading?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
}>();

function onClose() {
  emit("update:show", false);
}
</script>

<style scoped>
.preview-body {
  background: var(--bg-panel, #1c1c27);
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid var(--border-color, #2c2c3a);
  max-width: 94vw;
}
.preview-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: rgba(0, 0, 0, 0.3);
}
.preview-title {
  font-size: 13px;
  color: #fff;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 70vw;
}
.preview-close {
  background: rgba(255, 255, 255, 0.15);
  border: none;
  color: #fff;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  font-size: 14px;
  cursor: pointer;
  line-height: 1;
}
.preview-close:hover { background: rgba(255, 255, 255, 0.3); }
.preview-imgwrap {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #0c0d11;
  max-height: 86vh;
}
.preview-imgwrap img {
  max-width: 92vw;
  max-height: 86vh;
  object-fit: contain;
  display: block;
}
.preview-empty {
  padding: 40px;
  color: rgba(255, 255, 255, 0.6);
}
</style>
