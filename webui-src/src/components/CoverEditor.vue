<template>
  <n-modal
    :show="show"
    :mask-closable="true"
    preset="card"
    :title="title || '设置封面'"
    class="cover-editor-modal"
    :bordered="false"
    style="width: min(560px, 92vw)"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <div
      class="drop-zone"
      :class="{ 'is-drag': dragging }"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
      @click="pickFile"
    >
      <div class="dz-inner">
        <div class="dz-icon">🖼️</div>
        <p>把图片拖到这里，或<span class="dz-link">点击选择本地图片</span></p>
        <p class="dz-sub">支持 png / jpg / webp / gif</p>
      </div>
      <input ref="fileInput" type="file" accept="image/*" hidden @change="onFileChange" />
    </div>

    <n-divider>或粘贴图片直链</n-divider>

    <n-input-group>
      <n-input
        v-model:value="url"
        placeholder="https://…（C站 / 魔搭 / HuggingFace 等任意图片地址）"
        @keydown.enter="onFetch"
      />
      <n-button type="primary" :loading="urlLoading" @click="onFetch">下载</n-button>
    </n-input-group>
    <p class="dz-hint">直链需为图片本身地址（以 .png/.jpg/.webp 等结尾），不是模型详情页。</p>
  </n-modal>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { NModal, NInput, NInputGroup, NButton, NDivider } from "naive-ui";
import { useCover } from "@/composables/useCover";

const props = defineProps<{ show: boolean; title?: string }>();
const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "confirm", name: string): void;
}>();

const { uploadFile, fetchUrl } = useCover();
const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
const url = ref("");
const urlLoading = ref(false);

function pickFile() {
  fileInput.value?.click();
}

async function handleFile(file: File | undefined) {
  if (!file) return;
  const name = await uploadFile(file);
  if (name) {
    emit("confirm", name);
    emit("update:show", false);
  }
}

function onFileChange(ev: Event) {
  const target = ev.target as HTMLInputElement;
  handleFile(target.files?.[0]);
  target.value = "";
}

async function onDrop(ev: DragEvent) {
  dragging.value = false;
  const file = Array.from(ev.dataTransfer?.files || []).find((f) => f.type.startsWith("image/"));
  if (!file) return;
  await handleFile(file);
}

async function onFetch() {
  const u = url.value.trim();
  if (!u) return;
  urlLoading.value = true;
  const name = await fetchUrl(u);
  urlLoading.value = false;
  if (name) {
    url.value = "";
    emit("confirm", name);
    emit("update:show", false);
  }
}
</script>

<style scoped>
.drop-zone {
  border: 2px dashed var(--border-color, #3a3f4b);
  border-radius: 10px;
  padding: 26px 16px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
  color: var(--text-sub, #888);
}
.drop-zone.is-drag {
  border-color: var(--accent, #007aff);
  background: rgba(0, 122, 255, 0.08);
}
.dz-inner { pointer-events: none; }
.dz-icon { font-size: 28px; margin-bottom: 6px; }
.dz-link { color: var(--accent, #007aff); text-decoration: underline; }
.dz-sub { font-size: 12px; margin: 4px 0 0; }
.dz-hint { font-size: 12px; color: var(--text-sub, #888); margin: 8px 0 0; }
</style>
