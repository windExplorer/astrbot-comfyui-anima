<template>
  <div class="ref-panel">
    <div
      class="drop-zone"
      :class="{ dragging, disabled }"
      @click="pick"
      @dragover.prevent="dragging = true"
      @dragleave="dragging = false"
      @drop.prevent="onDrop"
    >
      <div class="dz-icon">🖼️</div>
      <div class="dz-text">
        {{ disabled ? "当前模式不需要参考图" : `点击 / 拖拽 / 粘贴图片（最多 ${maxRefs} 张）` }}
      </div>
      <input
        ref="fileInput"
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        multiple
        style="display: none"
        @change="onSelect"
      />
    </div>
    <div v-if="modelValue.length" class="ref-list">
      <div v-for="(r, i) in modelValue" :key="r.ref_id" class="ref-item">
        <img :src="r.preview" class="ref-thumb" loading="lazy" />
        <span class="ref-index">图{{ i + 1 }}</span>
        <button class="ref-remove" title="移除" @click="remove(i)">×</button>
      </div>
    </div>
    <div v-if="uploading" class="ref-uploading">上传中…（{{ uploadingCount }} 张）</div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { studioApi, fileToDataUrl, type StudioRefItem } from "@/api/studio";
import { useMessage } from "naive-ui";

const props = defineProps<{
  modelValue: StudioRefItem[];
  maxRefs: number;
  disabled?: boolean;
}>();

const emit = defineEmits<{ (e: "update:modelValue", v: StudioRefItem[]): void }>();

const message = useMessage();
const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
const uploading = ref(false);
const uploadingCount = ref(0);

function pick() {
  if (props.disabled) return;
  fileInput.value?.click();
}

async function addFiles(files: File[]) {
  if (props.disabled) return;
  const imgs = files.filter((f) => f.type.startsWith("image/"));
  if (!imgs.length) return;
  const room = props.maxRefs - props.modelValue.length;
  if (room <= 0) {
    message.warning(`参考图最多 ${props.maxRefs} 张`);
    return;
  }
  const batch = imgs.slice(0, room);
  if (imgs.length > room) message.warning(`参考图最多 ${props.maxRefs} 张，已只取前 ${room} 张`);
  uploading.value = true;
  uploadingCount.value = batch.length;
  const added: StudioRefItem[] = [];
  for (const f of batch) {
    try {
      const dataUrl = await fileToDataUrl(f);
      const d = await studioApi.uploadRef(dataUrl);
      added.push({ ref_id: d.ref_id, preview: dataUrl, name: f.name });
    } catch (e: any) {
      message.error(`「${f.name}」上传失败：${e?.message || e}`);
    }
  }
  uploading.value = false;
  uploadingCount.value = 0;
  if (added.length) {
    emit("update:modelValue", [...props.modelValue, ...added]);
    message.success(`已添加 ${added.length} 张参考图`);
  }
}

function onSelect(e: Event) {
  const input = e.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = "";
  addFiles(files);
}

function onDrop(e: DragEvent) {
  dragging.value = false;
  if (props.disabled) return;
  addFiles(Array.from(e.dataTransfer?.files || []));
}

/** 供父组件转发页面级粘贴事件 */
function addFromPaste(files: File[]) {
  addFiles(files);
}
defineExpose({ addFromPaste });

function remove(i: number) {
  const next = props.modelValue.slice();
  next.splice(i, 1);
  emit("update:modelValue", next);
}
</script>

<style scoped>
.ref-panel { width: 100%; }
.drop-zone {
  border: 1.5px dashed var(--border-color);
  border-radius: 10px;
  padding: 14px 10px;
  text-align: center;
  cursor: pointer;
  background: var(--bg-panel);
  transition: border-color 0.15s, background 0.15s;
}
.drop-zone:hover, .drop-zone.dragging { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 6%, var(--bg-panel)); }
.drop-zone.disabled { cursor: not-allowed; opacity: 0.5; }
.dz-icon { font-size: 22px; }
.dz-text { font-size: 12px; color: var(--text-sub); margin-top: 4px; }
.ref-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.ref-item {
  position: relative;
  width: 72px;
  height: 72px;
  border-radius: 8px;
  overflow: hidden;
  border: 1.5px solid var(--border-color);
}
.ref-thumb { width: 100%; height: 100%; object-fit: cover; display: block; }
.ref-index {
  position: absolute;
  left: 3px;
  bottom: 3px;
  font-size: 10px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  padding: 0 5px;
  border-radius: 999px;
}
.ref-remove {
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
}
.ref-remove:hover { background: #e86f9c; }
.ref-uploading { font-size: 12px; color: var(--text-sub); margin-top: 8px; }
</style>
