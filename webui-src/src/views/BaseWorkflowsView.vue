<template>
  <div class="bw-view">
    <div class="view-head">
      <div>
        <h2>基础工作流</h2>
        <p>上传 ComfyUI「导出(API)」格式 JSON，入库并自动解析节点角色。出图工作流从这里引用，替代手动拷文件到 workflow 目录。</p>
      </div>
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="view-actions">
          <n-button :loading="loading" @click="load">刷新</n-button>
          <n-button type="primary" @click="openUpload">＋ 上传工作流</n-button>
        </div>
      </Teleport>
    </div>

    <n-spin :show="loading">
      <n-empty v-if="!loading && !items.length" description="基础工作流库为空，点「上传工作流」添加。" style="padding:60px" />
      <div v-else class="card-grid">
        <div v-for="w in items" :key="w.id" class="bw-card">
          <div class="card-cover">
            <img v-if="w.image" v-cover-lazy="'bm:' + w.image" alt="" loading="lazy" />
            <div v-else class="cover-empty">无封面</div>
          </div>
          <div class="card-body">
            <div class="card-title">
              {{ w.name || "(未命名)" }}
              <n-tag size="tiny" :type="w.parse_ok ? 'success' : 'error'" :bordered="false">
                {{ w.parse_ok ? "解析通过" : "解析失败" }}
              </n-tag>
            </div>
            <div class="card-meta">
              <n-tag size="tiny" :bordered="false">{{ typeLabel(w.roles?.kind) }}</n-tag>
              <n-tag size="tiny" type="info" :bordered="false">底模：{{ w.basemodel_name || "未关联" }}</n-tag>
              <n-tag size="tiny" :bordered="false">{{ w.roles?.model_file || w.roles?.model_class || "模型未识别" }}</n-tag>
              <n-tag v-if="(w.roles?.upscale)" size="tiny" type="warning" :bordered="false">内置放大链</n-tag>
              <n-tag v-if="(w.roles?.cleanup_nodes || []).length" size="tiny" :bordered="false">清理显存</n-tag>
              <n-tag v-if="w.ref_count" size="tiny" type="success" :bordered="false">被引用 {{ w.ref_count }}</n-tag>
              <a v-if="w.civitai_url" :href="w.civitai_url" target="_blank" rel="noopener noreferrer" class="civ-link">C站 ↗</a>
            </div>
            <div class="card-sub" v-if="!w.parse_ok">{{ w.parse_msg }}</div>
            <div class="card-sub">文件：{{ w.file_name || "—" }}｜sha {{ w.sha256 || "—" }}</div>
            <div class="card-actions">
              <n-button size="tiny" @click="openDetail(w)">解析详情</n-button>
              <n-button size="tiny" @click="openEdit(w)">编辑</n-button>
              <n-button size="tiny" @click="openCover(w)">上传封面</n-button>
              <n-button size="tiny" :loading="reparsingId === w.id" @click="reparse(w)">重解析</n-button>
              <n-button size="tiny" type="error" @click="removeWf(w)">删除</n-button>
            </div>
          </div>
        </div>
      </div>
    </n-spin>

    <CoverEditor v-model:show="coverShow" title="设置基础工作流封面" scope="bm" @confirm="onCoverConfirm" />

    <!-- 上传弹窗 -->
    <n-modal v-model:show="uploadShow" preset="card" title="上传基础工作流" class="bw-modal" :bordered="false">
      <div
        class="drop-zone"
        :class="{ 'is-drag': dragging }"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
        @click="pickFile"
      >
        <div class="dz-inner">
          <div class="dz-icon">🗂️</div>
          <p>把工作流 JSON 拖到这里，或<span class="dz-link">点击选择文件</span></p>
          <p class="dz-sub">必须是 ComfyUI「导出（API）」格式；解析不通过会被拒绝并说明原因</p>
        </div>
        <input ref="fileInput" type="file" accept=".json" hidden @change="onFileChange" />
      </div>
      <n-form label-placement="top" style="margin-top: 12px">
        <n-form-item label="名称（默认取文件名去扩展名）">
          <n-input v-model:value="uploadName" placeholder="如 mmh1.6_turbo" />
        </n-form-item>
        <n-form-item label="或直接粘贴 JSON 文本">
          <n-input v-model:value="uploadText" type="textarea" :rows="5" placeholder='{ "3": { "class_type": "KSampler", ... } }' />
        </n-form-item>
      </n-form>
      <n-alert v-if="uploadError" type="error" style="margin-top: 8px">{{ uploadError }}</n-alert>
      <n-alert v-if="uploadOk" type="success" style="margin-top: 8px">
        解析通过：{{ uploadOkSummary }}
      </n-alert>
      <template #footer>
        <div class="modal-footer">
          <n-button @click="uploadShow = false">关闭</n-button>
          <n-button type="primary" :loading="uploading" @click="doUpload">解析并入库</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 编辑弹窗（名称/别名/C站/封面） -->
    <n-modal v-model:show="editShow" preset="card" title="编辑基础工作流" class="bw-modal" :bordered="false">
      <n-form label-placement="top">
        <n-form-item label="名称"><n-input v-model:value="editForm.name" /></n-form-item>
        <n-form-item label="底模（模型族，来自「配置项」页）">
          <n-select
            v-model:value="editForm.basemodel_id"
            :options="basemodelOptions"
            filterable
            clearable
            placeholder="未关联（可手动选择；上传时按匹配关键字自动关联）"
          />
        </n-form-item>
        <n-form-item label="C 站链接">
          <n-input-group>
            <n-input v-model:value="editForm.civitai_url" placeholder="https://civitai.com/models/12345" />
            <n-button :loading="fetching" @click="fetchInfo">抓取</n-button>
          </n-input-group>
        </n-form-item>
        <n-form-item label="描述（来自抓取或手填，可选）">
          <n-input v-model:value="editForm.description" type="textarea" :rows="3" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="modal-footer">
          <n-button @click="editShow = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveEdit">保存</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 解析详情 -->
    <n-modal v-model:show="detailShow" preset="card" title="解析详情" class="bw-modal" :bordered="false">
      <pre v-if="detailRoles" class="roles-pre">{{ JSON.stringify(detailRoles, null, 2) }}</pre>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  useMessage, useDialog, NButton, NSpin, NForm, NFormItem, NInput, NInputGroup,
  NModal, NTag, NEmpty, NAlert,
} from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import CoverEditor from "@/components/CoverEditor.vue";

const message = useMessage();
const dialog = useDialog();
const loading = ref(false);
const saving = ref(false);
const fetching = ref(false);
const uploading = ref(false);
const reparsingId = ref(-1);
const items = ref<any[]>([]);

function typeLabel(kind?: string) {
  if (kind === "img2img") return "图生图";
  if (kind === "t2i") return "文生图";
  return "类型未知";
}

async function load() {
  loading.value = true;
  try {
    const d = await apiGet("baseworkflows");
    items.value = Array.isArray(d?.items) ? d.items : [];
  } catch (e: any) {
    message.error(e?.message || "读取基础工作流库失败");
  } finally {
    loading.value = false;
  }
}

// ---- 上传 ----
const uploadShow = ref(false);
const dragging = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const uploadName = ref("");
const uploadText = ref("");
const uploadError = ref("");
const uploadOk = ref(false);
const uploadOkSummary = ref("");

function openUpload() {
  uploadName.value = "";
  uploadText.value = "";
  uploadError.value = "";
  uploadOk.value = false;
  uploadShow.value = true;
}
function pickFile() { fileInput.value?.click(); }

function onDrop(ev: DragEvent) {
  dragging.value = false;
  const file = Array.from(ev.dataTransfer?.files || []).find((f) => f.name.toLowerCase().endsWith(".json"));
  if (file) readFile(file);
}
function onFileChange(ev: Event) {
  const target = ev.target as HTMLInputElement;
  const file = target.files?.[0];
  if (file) readFile(file);
  target.value = "";
}
function readFile(file: File) {
  if (!uploadName.value.trim()) {
    uploadName.value = file.name.replace(/\.json$/i, "");
  }
  const reader = new FileReader();
  reader.onload = () => { uploadText.value = String(reader.result || ""); };
  reader.readAsText(file, "utf-8");
}

function summarizeRoles(roles: any): string {
  if (!roles) return "";
  const parts: string[] = [];
  parts.push(typeLabel(roles.kind));
  if (roles.model_class) parts.push(`底模 ${roles.model_class}`);
  if (roles.positive) parts.push(`正向 ${roles.positive.node}.${roles.positive.field}`);
  if (roles.negative) parts.push(`负向 ${roles.negative.node}.${roles.negative.field}`);
  if (roles.latent) parts.push(`宽高 ${roles.latent.node}`);
  if (roles.save) parts.push(`保存 ${roles.save.node}`);
  if (roles.upscale) parts.push(`内置放大 ${roles.upscale.loader || ""}`);
  if ((roles.cleanup_nodes || []).length) parts.push("清理显存");
  if ((roles.subgraph_ids || []).length) parts.push("子图格式");
  return parts.join("｜");
}

async function doUpload() {
  const content = uploadText.value.trim();
  if (!content) { message.warning("请先选择文件或粘贴 JSON"); return; }
  uploading.value = true;
  uploadError.value = "";
  uploadOk.value = false;
  try {
    const d = await apiPost("baseworkflows/upload", {
      name: uploadName.value.trim(),
      content,
      filename: "",
    });
    uploadOk.value = true;
    uploadOkSummary.value = summarizeRoles(d.roles);
    message.success("入库成功");
    await load();
  } catch (e: any) {
    uploadError.value = e?.message || "上传失败";
  } finally {
    uploading.value = false;
  }
}

// ---- 编辑 / 删除 / 重解析 ----
const editShow = ref(false);
const editForm = ref<Record<string, any>>({ name: "", civitai_url: "", description: "", basemodel_id: 0 });
let editTarget: any = null;
// 底模（模型族）下拉：来自「配置项」页
const basemodels = ref<any[]>([]);
const basemodelOptions = computed(() => [
  { label: "未关联", value: 0 },
  ...basemodels.value.map((b) => ({ label: b.name || `#${b.id}`, value: b.id })),
]);

async function loadBasemodels() {
  try {
    const d = await apiGet("basemodels");
    basemodels.value = Array.isArray(d?.items) ? d.items : [];
  } catch {
    basemodels.value = [];
  }
}

function openEdit(w: any) {
  editTarget = w;
  editForm.name = w.name || "";
  editForm.civitai_url = w.civitai_url || "";
  editForm.description = w.description || "";
  editForm.basemodel_id = Number(w.basemodel_id || 0);
  editShow.value = true;
}

async function saveEdit() {
  if (!editTarget) return;
  if (!String(editForm.name || "").trim()) { message.warning("名称必填"); return; }
  saving.value = true;
  try {
    await apiPost("baseworkflows/meta", {
      id: editTarget.id,
      name: String(editForm.name).trim(),
      civitai_url: editForm.civitai_url,
      description: editForm.description,
      basemodel_id: Number(editForm.basemodel_id || 0),
    });
    message.success("已保存");
    editShow.value = false;
    await load();
  } catch (e: any) {
    message.error(e?.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

async function fetchInfo() {
  if (!editTarget) return;
  const url = String(editForm.civitai_url || "").trim();
  if (!url) { message.warning("请先填写 C 站链接"); return; }
  fetching.value = true;
  try {
    const d = await apiPost("baseworkflows/fetch", { url }, { timeout: 60000 });
    if (d.title) editForm.name = d.title;
    if (d.description) editForm.description = d.description;
    const updates: Record<string, any> = { civitai_url: url };
    if (d.image) updates.image = d.image;
    await apiPost("baseworkflows/meta", { id: editTarget.id, ...updates });
    message.success("抓取完成（封面已保存）");
    await load();
  } catch (e: any) {
    message.error(e?.message || "抓取失败");
  } finally {
    fetching.value = false;
  }
}

function removeWf(w: any) {
  dialog.warning({
    title: "删除基础工作流",
    content: `确定删除「${w.name || ""}」吗？被出图工作流引用时会被拒绝。`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: () => {
      apiPost("baseworkflows/delete", { id: w.id })
        .then(() => { message.success("已删除"); load(); })
        .catch((e: any) => message.error(e?.message || "删除失败"));
    },
  });
}

async function reparse(w: any) {
  reparsingId.value = w.id;
  try {
    await apiPost("baseworkflows/reparse", { id: w.id });
    message.success("重解析完成");
    await load();
  } catch (e: any) {
    message.error(e?.message || "重解析失败");
  } finally {
    reparsingId.value = -1;
  }
}

// ---- 详情 / 封面 ----
const detailShow = ref(false);
const detailRoles = ref<any>(null);
function openDetail(w: any) {
  detailRoles.value = w.roles || null;
  detailShow.value = true;
}
const coverShow = ref(false);
let coverTarget: any = null;
function openCover(w: any) { coverTarget = w; coverShow.value = true; }
async function onCoverConfirm(name: string) {
  if (!coverTarget || !name) return;
  try {
    await apiPost("baseworkflows/meta", { id: coverTarget.id, image: name });
    message.success("封面已更新");
    await load();
  } catch (e: any) {
    message.error(e?.message || "封面保存失败");
  }
}

onMounted(() => {
  load();
  loadBasemodels();
});
</script>

<style scoped>
.bw-view { height: 100%; overflow: auto; padding: 0 4px; }
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; max-width: 72%; }
.view-actions { display: flex; gap: 8px; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.bw-card { border: 1px solid var(--border-color); border-radius: 10px; background: var(--bg-panel); overflow: hidden; display: flex; flex-direction: column; }
.card-cover { height: 140px; background: var(--bg-body, #f5f5f5); display: flex; align-items: center; justify-content: center; overflow: hidden; }
.card-cover img { width: 100%; height: 100%; object-fit: cover; }
.cover-empty { color: var(--text-sub); font-size: 12px; }
.card-body { padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; flex: 1; }
.card-title { font-weight: 600; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.card-meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.card-sub { color: var(--text-sub); font-size: 12px; word-break: break-all; }
.card-actions { display: flex; gap: 6px; margin-top: auto; flex-wrap: wrap; }
.civ-link { font-size: 12px; }
.roles-pre { font-size: 12px; max-height: 55vh; overflow: auto; white-space: pre-wrap; word-break: break-all; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; }
.drop-zone { border: 2px dashed var(--border-color); border-radius: 10px; padding: 26px; text-align: center; cursor: pointer; }
.drop-zone.is-drag { border-color: var(--primary, #18a058); background: rgba(24, 160, 88, 0.05); }
.dz-icon { font-size: 30px; }
.dz-link { color: var(--primary, #18a058); }
.dz-sub { color: var(--text-sub); font-size: 12px; }
</style>
