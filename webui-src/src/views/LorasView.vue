<template>
  <div class="loras-view">
    <div class="view-head">
      <div>
        <h2>LoRA 库</h2>
        <p>卡片式查看 LoRA：封面图、别名、底模、触发词、描述；可编辑、上传封面或从 C 站链接抓取。</p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="load">刷新</n-button>
        <n-button type="primary" @click="addLora">＋ 新增 LoRA</n-button>
      </div>
    </div>

    <n-spin :show="loading">
      <n-empty v-if="!loading && !loras.length" description="尚未配置任何 LoRA，点「新增 LoRA」添加。" style="padding:60px" />
      <div v-else class="card-grid">
        <div v-for="(l, idx) in loras" :key="idx" class="lora-card">
          <div class="card-cover" @click="openImage(l.image, l.name)">
            <img v-if="l.image && coverCache[l.image]" :src="coverCache[l.image]" alt="" loading="lazy" />
            <div v-else class="cover-empty">无封面</div>
          </div>
          <div class="card-body">
            <div class="card-title">{{ l.name || "(未命名)" }}</div>
            <div class="card-alias">别名：{{ aliasFirst(l.keywords) }}</div>
            <div class="card-meta">
              <n-tag size="tiny" :bordered="false">{{ l.base_model?.trim() || "通用" }}</n-tag>
              <a v-if="l.civitai_url" :href="l.civitai_url" target="_blank" rel="noopener noreferrer" class="civ-link">C站 ↗</a>
            </div>
            <div class="card-actions">
              <n-button size="tiny" @click="showDetail(idx)">详情</n-button>
              <n-button size="tiny" @click="editLora(idx)">编辑</n-button>
              <n-button size="tiny" @click="fetchLora(idx)">抓取</n-button>
              <n-button size="tiny" @click="uploadCover(idx)">上传封面</n-button>
              <n-button size="tiny" type="error" @click="removeLora(idx)">删除</n-button>
            </div>
          </div>
        </div>
      </div>
    </n-spin>

    <!-- 详情弹窗 -->
    <n-modal v-model:show="detailShow" preset="card" title="LoRA 详情" style="width:520px" :bordered="false">
      <div v-if="detailItem" class="detail">
        <div class="detail-row"><b>名称：</b>{{ detailItem.name }}</div>
        <div class="detail-row"><b>底模：</b>{{ detailItem.base_model?.trim() || "通用" }}</div>
        <div class="detail-row"><b>别名：</b>{{ detailItem.keywords || "—" }}</div>
        <div class="detail-row"><b>模型文件：</b>{{ detailItem.model_name || "—" }}</div>
        <div class="detail-row"><b>触发词：</b><pre>{{ detailItem.trigger_words || "—" }}</pre></div>
        <div class="detail-row"><b>描述：</b><pre>{{ detailItem.description || "—" }}</pre></div>
        <div class="detail-row"><b>提示词预设：</b><pre>{{ detailItem.presets || "—" }}</pre></div>
      </div>
    </n-modal>

    <!-- 大图预览 -->
    <ImagePreview v-model:show="previewShow" :src="previewSrc" :title="previewTitle" />

    <!-- 编辑弹窗 -->
    <n-modal v-model:show="editShow" preset="card" :title="editTitle" style="width:680px" :bordered="false">
      <n-form label-placement="top" class="edit-form">
        <div class="form-grid">
          <n-form-item label="名称（引用键）"><n-input v-model:value="editForm.name" placeholder="如 安魂曲" /></n-form-item>
          <n-form-item label="底模">
            <n-select v-model:value="editForm.base_model" :options="baseModelOptions" />
          </n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="模型文件名"><n-input v-model:value="editForm.model_name" placeholder="xxx.safetensors" /></n-form-item>
          <n-form-item label="默认权重"><n-input-number v-model:value="editForm.weight" style="width:100%" /></n-form-item>
        </div>
        <n-form-item label="别名（每行一个，供 LLM 区分）"><n-input v-model:value="editForm.keywords" type="textarea" :rows="3" /></n-form-item>
        <n-form-item label="触发词（每行一个）"><n-input v-model:value="editForm.trigger_words" type="textarea" :rows="3" /></n-form-item>
        <n-form-item label="描述（供 LLM 理解）"><n-input v-model:value="editForm.description" type="textarea" :rows="3" /></n-form-item>
        <div class="form-grid">
          <n-form-item label="C 站链接"><n-input v-model:value="editForm.civitai_url" placeholder="https://civitai.com/models/xxx" /></n-form-item>
          <n-form-item label="封面图文件名"><n-input v-model:value="editForm.image" placeholder="存于 lora_assets/，可上传" /></n-form-item>
        </div>
        <n-form-item label="提示词预设（每套 [预设名|提示词]，可多套）"><n-input v-model:value="editForm.presets" type="textarea" :rows="3" /></n-form-item>
        <n-form-item label="仅模型节点">
          <n-switch v-model:value="editForm.model_only" />
          <span class="form-hint">开启时只叠加 MODEL（兼容性最好）；关闭则同时影响 CLIP</span>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editShow = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveEdit">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useMessage, useDialog, NButton, NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NSwitch, NTag, NSpace, NEmpty, NSpin } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import { parseAliases } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import ImagePreview from "@/components/ImagePreview.vue";

const message = useMessage();
const dialog = useDialog();
const loading = ref(false);
const saving = ref(false);
const loras = ref<any[]>([]);
const coverCache = reactive<Record<string, string>>({});

const baseModelOptions = ["", "anima", "z-image-turbo", "krea2", "illustrious"].map((o) => ({ label: o || "（通用）", value: o }));

async function load() {
  loading.value = true;
  try {
    const cfg = await apiGet("config");
    loras.value = Array.isArray(cfg.loras) ? cfg.loras : [];
    loras.value.forEach((l) => loadCover(l.image));
  } catch (e: any) {
    message.error(e.message || "加载 LoRA 失败");
  } finally {
    loading.value = false;
  }
}

function loadCover(fname: string) {
  if (!fname || coverCache[fname]) return;
  apiGet("lora/image", { name: fname }).then((d) => {
    if (d && d.url) coverCache[fname] = d.url;
  }).catch(() => {});
}

function aliasFirst(raw: string): string {
  const a = parseAliases(raw);
  return a.length ? a[0] : "—";
}

// 大图预览（用弹窗显示 data URL，避免沙箱 iframe 下 window.open 被拦截）
const previewShow = ref(false);
const previewSrc = ref("");
const previewTitle = ref("");

function openImage(fname: string, name: string) {
  if (!fname) { message.info("该 LoRA 没有封面图，可先上传或抓取封面"); return; }
  if (coverCache[fname]) {
    previewSrc.value = coverCache[fname];
    previewTitle.value = name || fname;
    previewShow.value = true;
  } else {
    message.info("封面加载中，请稍后再试");
  }
}

// 详情
const detailShow = ref(false);
const detailItem = ref<any>(null);
function showDetail(idx: number) {
  detailItem.value = loras.value[idx] || null;
  detailShow.value = true;
}

// 编辑
const editShow = ref(false);
const editTitle = ref("编辑 LoRA");
const editIndex = ref(-1);
const editForm = reactive<Record<string, any>>({});

function openForm(idx: number, prefill?: any) {
  const isNew = idx < 0 || idx >= loras.value.length;
  editTitle.value = (isNew ? "新增" : "编辑") + " LoRA";
  editIndex.value = idx;
  const l = prefill ? prefill : (isNew ? { weight: 1 } : (loras.value[idx] || {}));
  Object.keys(editForm).forEach((k) => delete editForm[k]);
  Object.assign(editForm, JSON.parse(JSON.stringify({
    name: l.name || "",
    base_model: l.base_model || "",
    model_name: l.model_name || "",
    weight: l.weight ?? 1,
    keywords: l.keywords || "",
    trigger_words: l.trigger_words || "",
    description: l.description || "",
    civitai_url: l.civitai_url || "",
    image: l.image || "",
    presets: l.presets || "",
    model_only: l.model_only !== false,
  })));
  editShow.value = true;
}

function addLora() { openForm(-1); }
function editLora(idx: number) { openForm(idx); }

async function saveEdit() {
  if (!editForm.name || !editForm.name.trim()) { message.warning("名称必填"); return; }
  editForm.name = editForm.name.trim();
  saving.value = true;
  try {
    const tplKey = (loras.value[editIndex.value] && loras.value[editIndex.value].__template_key) || "default";
    const v = { ...editForm, __template_key: tplKey };
    if (editIndex.value < 0 || editIndex.value >= loras.value.length) {
      loras.value.unshift(v);
    } else {
      loras.value[editIndex.value] = { ...loras.value[editIndex.value], ...v };
    }
    await apiPost("config", { config: { loras: loras.value } });
    message.success("LoRA 已保存");
    editShow.value = false;
    loadCover(v.image);
  } catch (e: any) {
    message.error(e.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

function removeLora(idx: number) {
  const l = loras.value[idx] || {};
  dialog.warning({
    title: "删除 LoRA",
    content: `确定要删除 LoRA「${l.name || ""}」吗？此操作不可恢复！`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      loras.value.splice(idx, 1);
      try {
        await apiPost("config", { config: { loras: loras.value } });
        message.success("LoRA 已删除");
      } catch (e: any) {
        message.error(e.message || "删除失败");
      }
    },
  });
}

function fetchLora(idx: number) {
  const l = loras.value[idx];
  if (!l) return;
  if (!l.civitai_url) { message.warning("请先填写 C 站链接"); return; }
  message.loading("正在抓取…", { duration: 15000 });
  apiPost("lora/fetch", { url: l.civitai_url }).then((d) => {
    if (!d || !d.fetched) throw new Error("未抓取到数据");
    const updates: Record<string, any> = {};
    if (d.trigger_words) updates.trigger_words = d.trigger_words;
    if (d.description) updates.description = d.description;
    if (d.base_model) updates.base_model = d.base_model;
    if (d.images && d.images.length) updates.image = d.images[0];
    loras.value[idx] = { ...l, ...updates };
    loras.value = [...loras.value];
    return apiPost("config", { config: { loras: loras.value } });
  }).then(() => {
    message.success("抓取完成并已保存");
    loadCover(loras.value[idx].image);
  }).catch((e: any) => message.error(e.message || "抓取失败"));
}

function uploadCover(idx: number) {
  const l = loras.value[idx];
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.onchange = () => {
    const file = input.files?.[0];
    if (!file) return;
    message.loading("上传中…", { duration: 10000 });
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = async () => {
      try {
        const b64 = String(reader.result || "").split(",")[1] || "";
        const d = await apiPost("lora/upload_image", { filename: file.name, data: b64 });
        l.image = d.name;
        loras.value = [...loras.value];
        await apiPost("config", { config: { loras: loras.value } });
        message.success("封面已上传");
        loadCover(l.image);
      } catch (e: any) {
        message.error(e.message || "上传失败");
      }
    };
  };
  input.click();
}

useRefresh(load);
onMounted(load);
</script>

<style scoped>
.loras-view { max-width: 1100px; }
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.view-actions { display: flex; gap: 8px; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
.lora-card {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-panel);
  overflow: hidden;
}
.card-cover { aspect-ratio: 3 / 4; cursor: zoom-in; background: var(--bg-body); display: flex; align-items: center; justify-content: center; overflow: hidden; }
.card-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cover-empty { color: var(--text-sub); font-size: 12px; }
.card-body { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.card-title { font-weight: 600; font-size: 15px; }
.card-alias { color: var(--text-sub); font-size: 12px; }
.card-meta { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.civ-link { color: var(--accent); text-decoration: none; font-size: 12px; }
.card-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.edit-form { max-height: 65vh; overflow: auto; padding-right: 4px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-hint { color: var(--text-sub); font-size: 12px; margin-left: 8px; }
.detail { display: flex; flex-direction: column; gap: 10px; }
.detail-row { font-size: 13px; }
.detail-row pre { margin: 4px 0 0; white-space: pre-wrap; word-break: break-all; font-family: inherit; color: var(--text-sub); }
</style>
