<template>
  <n-modal
    :show="show"
    :mask-closable="true"
    preset="dialog"
    :title="title || '选择封面'"
    :style="{ width: 'min(680px, 92vw)' }"
    :bordered="false"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <template #default>
      <p class="cp-tip">抓取到 {{ covers.length }} 张候选图，点击一张作为封面：</p>
      <div class="cp-grid">
        <button
          v-for="name in covers"
          :key="name"
          type="button"
          class="cp-item"
          :title="name"
          @click="onPick(name)"
        >
          <img v-cover-lazy="name" alt="" loading="lazy" />
          <span class="cp-tag">选用</span>
        </button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { NModal } from "naive-ui";

defineProps<{
  show: boolean;
  covers: string[];
  title?: string;
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "pick", name: string): void;
}>();

function onPick(name: string) {
  emit("pick", name);
  emit("update:show", false);
}
</script>

<style scoped>
.cp-tip { margin: 0 0 12px; font-size: 13px; color: var(--text-sub, #888); }
.cp-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
  max-height: 56vh;
  overflow: auto;
}
.cp-item {
  position: relative;
  display: block;
  border: none;
  background: #16181f;
  border-radius: 10px;
  overflow: hidden;
  cursor: pointer;
  padding: 0;
  aspect-ratio: 1 / 1;
  transition: transform 0.15s, box-shadow 0.15s;
}
.cp-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.cp-item:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(0,0,0,0.4); }
.cp-tag {
  position: absolute;
  right: 6px;
  bottom: 6px;
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 999px;
  background: rgba(0, 122, 255, 0.9);
  color: #fff;
}
</style>
