<template>
  <div class="workflows-view">
    <div class="view-head">
      <div>
        <h2>工作流</h2>
        <p>卡片式查看工作流：名称、别名、底模、服务器、是否 Anima；可编辑、查看可用 LoRA。</p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="load">刷新</n-button>
        <n-button type="primary" @click="addWorkflow">＋ 新增工作流</n-button>
      </div>
    </div>

    <div class="wf-scroll">
    <n-spin :show="loading">
      <n-empty v-if="!loading && !workflows.length" description="尚未配置任何工作流，点「新增工作流」添加。" style="padding:60px" />
      <div v-else class="card-grid">
        <div v-for="(w, idx) in workflows" :key="idx" class="wf-card">
          <div class="card-cover" @click="openImage(w.image, w.name)">
            <img v-if="w.image" v-cover-lazy="w.image" alt="" loading="lazy" />
            <div v-else class="cover-empty">无封面</div>
          </div>
          <div class="card-head">
            <span class="card-title">{{ w.name || "(未命名)" }}</span>
            <n-tag v-if="w.is_anima" size="small" type="info" :bordered="false">Anima</n-tag>
          </div>
          <div class="card-alias">别名：{{ aliasStr(w.aliases) }}</div>
          <div class="card-meta">
            <n-tag size="tiny" :bordered="false">{{ w.base_model?.trim() || "不限底模" }}</n-tag>
            <span class="meta-item">{{ w.server_name?.trim() || "默认服务器" }}</span>
            <span v-if="w.workflow_name" class="meta-item">{{ w.workflow_name }}</span>
            <a v-if="w.civitai_url" :href="w.civitai_url" target="_blank" rel="noopener noreferrer" class="civ-link">C站 ↗</a>
          </div>
          <div class="card-loracfg">{{ (w.loras_text || "").trim() ? "已配默认 LoRA" : "未配默认 LoRA" }}</div>
          <div class="card-avail">可用 LoRA：{{ availLoras(w).join("、") || "无匹配 LoRA" }}</div>
          <div class="card-actions">
            <n-button size="tiny" @click="editWorkflow(idx)">编辑</n-button>
            <n-button size="tiny" @click="copyWorkflow(idx)">复制</n-button>
            <n-button size="tiny" @click="fetchCover(idx)">抓封面</n-button>
            <n-button size="tiny" @click="uploadCover(idx)">传封面</n-button>
            <n-button size="tiny" type="error" @click="removeWorkflow(idx)">删除</n-button>
          </div>
        </div>
      </div>
    </n-spin>
    </div>

    <!-- 大图预览 -->
    <!-- 大图详情（全屏：左侧封面，右侧字段信息） -->
    <ItemViewer v-model:show="previewShow" :src="previewSrc" :title="previewTitle" :fields="detailFields" />
    <!-- 抓取封面选择（多张候选时弹出） -->
    <CoverPicker v-model:show="coverPickShow" :covers="coverPickCovers" :title="coverPickTitle" @pick="onCoverPick" />

    <!-- 编辑弹窗 -->
    <n-modal v-model:show="editShow" preset="card" :title="editTitle" class="wf-modal" :bordered="false">
      <n-form label-placement="top" :label-width="0" class="edit-form">
        <div class="form-grid">
          <n-form-item label="名称"><n-input v-model:value="editForm.name" placeholder="如 sd" /></n-form-item>
          <n-form-item label="底模">
            <n-select v-model:value="editForm.base_model" :options="baseModelOptions" />
          </n-form-item>
        </div>
        <n-form-item label="别名（逗号/换行分隔）"><n-input v-model:value="editForm.aliases" type="textarea" :rows="2" /></n-form-item>
        <div class="form-grid">
          <n-form-item label="绑定服务器"><n-input v-model:value="editForm.server_name" placeholder="如 server1" /></n-form-item>
          <n-form-item label="工作流文件名"><n-input v-model:value="editForm.workflow_name" placeholder="如 sd.json" /></n-form-item>
        </div>
        <n-form-item label="Anima 工作流">
          <n-switch v-model:value="editForm.is_anima" />
          <span class="form-hint">开启后中文提示词会先翻译为 Danbooru 标签</span>
        </n-form-item>
        <div class="form-grid">
          <n-form-item label="C 站链接（抓封面）"><n-input v-model:value="editForm.civitai_url" placeholder="https://civitai.com/models/xxx" /></n-form-item>
          <n-form-item label="封面图文件名"><n-input v-model:value="editForm.image" placeholder="存于 lora_assets/，可抓取或上传" /></n-form-item>
        </div>

        <n-divider style="margin:8px 0">── 节点配置 ──</n-divider>
        <div class="form-grid">
          <n-form-item label="正提示词节点"><n-input v-model:value="editForm.positive_node" placeholder="如 6" /></n-form-item>
          <n-form-item label="负提示词节点"><n-input v-model:value="editForm.negative_node" placeholder="如 7" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="分辨率节点"><n-input v-model:value="editForm.resolution_node" placeholder="EmptyLatentImage，可留空自动探测" /></n-form-item>
          <n-form-item label="输出节点"><n-input v-model:value="editForm.output_node" placeholder="出图节点（可选）" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="宽度字段"><n-input v-model:value="editForm.resolution_width_field" placeholder="width" /></n-form-item>
          <n-form-item label="高度字段"><n-input v-model:value="editForm.resolution_height_field" placeholder="height" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="默认宽度"><n-input-number v-model:value="editForm.default_width" style="width:100%" /></n-form-item>
          <n-form-item label="默认高度"><n-input-number v-model:value="editForm.default_height" style="width:100%" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="参考图节点"><n-input v-model:value="editForm.image_node" placeholder="图生图 LoadImage（可选）" /></n-form-item>
          <n-form-item label="LoRA 主模锚点"><n-input v-model:value="editForm.lora_anchor" placeholder="底模节点键名，留空自动探测" /></n-form-item>
        </div>
        <n-form-item label="工作流 JSON（可直接粘贴）"><n-input v-model:value="editForm.workflow_json" type="textarea" :rows="3" /></n-form-item>
        <n-form-item label="默认 LoRA（每行 名称|权重|启用）"><n-input v-model:value="editForm.loras_text" type="textarea" :rows="3" /></n-form-item>
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
import { useMessage, useDialog, NButton, NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NSwitch, NTag, NSpace, NDivider, NEmpty, NSpin } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import { parseAliases, truncate } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import ItemViewer, { type ItemViewerField } from "@/components/ItemViewer.vue";
import CoverPicker from "@/components/CoverPicker.vue";

const message = useMessage();
const dialog = useDialog();
const loading = ref(false);
const saving = ref(false);
const workflows = ref<any[]>([]);
const loras = ref<any[]>([]);

const baseModelOptions = ["", "anima", "z-image-turbo", "krea2", "illustrious"].map((o) => ({ label: o || "（通用）", value: o }));

async function load() {
  loading.value = true;
  try {
    const cfg = await apiGet("config");
    workflows.value = Array.isArray(cfg.workflows) ? cfg.workflows : [];
    loras.value = Array.isArray(cfg.loras) ? cfg.loras : [];
  } catch (e: any) {
    message.error(e.message || "加载工作流失败");
  } finally {
    loading.value = false;
  }
}

function aliasStr(raw: string): string {
  const a = parseAliases(raw);
  return a.length ? a.join(" / ") : "—";
}

function availLoras(w: any): string[] {
  const wbm = (w.base_model || "").trim().toLowerCase();
  return loras.value
    .filter((l) => {
      const lbm = (l.base_model || "").trim().toLowerCase();
      return !wbm || !lbm || wbm === lbm;
    })
    .map((l) => l.name || "")
    .filter(Boolean);
}

// 大图详情（全屏：左侧封面，右侧字段信息）
const previewShow = ref(false);
const previewSrc = ref("");
const previewTitle = ref("");
const detailFields = ref<ItemViewerField[]>([]);

function openImage(fname: string, name: string) {
  const w = workflows.value.find((x) => x.image === fname) || {};
  const realName = name || w.name || fname;
  previewSrc.value = ""; // 重置后组件显示"封面加载中…"
  detailFields.value = [
    { key: "名称", value: realName },
    { key: "别名", value: aliasStr(w.aliases || "") },
    { key: "底模", value: w.base_model?.trim() || "通用" },
    { key: "服务器", value: w.server_name?.trim() || "默认" },
    { key: "工作流文件", value: w.workflow_name?.trim() || "—" },
    { key: "Anima 模式", value: w.is_anima ? "是" : "否" },
    { key: "默认尺寸", value: w.default_width && w.default_height ? `${w.default_width} × ${w.default_height}` : "—" },
    { key: "预设 LoRA", value: w.loras_text?.trim() || "—" },
    { key: "封面文件", value: fname },
  ];
  if (w.civitai_url) detailFields.value.push({ key: "C 站", value: w.civitai_url, href: w.civitai_url });
  previewTitle.value = realName;
  previewShow.value = true;
  if (fname) {
    apiGet("lora/image", { name: fname }).then((d) => {
      if (d && d.url) previewSrc.value = d.url;
    }).catch(() => {});
  }
}

// 编辑
const editShow = ref(false);
const editTitle = ref("编辑工作流");
const editIndex = ref(-1);
const editForm = reactive<Record<string, any>>({});

function openForm(idx: number, prefill?: any) {
  const isNew = idx < 0 || idx >= workflows.value.length;
  editTitle.value = (isNew ? "新增" : "编辑") + " 工作流";
  editIndex.value = idx;
  const w = prefill ? prefill : (isNew ? {} : (workflows.value[idx] || {}));
  Object.keys(editForm).forEach((k) => delete editForm[k]);
  Object.assign(editForm, JSON.parse(JSON.stringify({
    name: w.name || "",
    base_model: w.base_model || "",
    aliases: w.aliases || "",
    server_name: w.server_name || "",
    workflow_name: w.workflow_name || "",
    is_anima: !!w.is_anima,
    civitai_url: w.civitai_url || "",
    image: w.image || "",
    positive_node: w.positive_node || "",
    negative_node: w.negative_node || "",
    resolution_node: w.resolution_node || "",
    output_node: w.output_node || "",
    resolution_width_field: w.resolution_width_field || "width",
    resolution_height_field: w.resolution_height_field || "height",
    default_width: w.default_width ?? 512,
    default_height: w.default_height ?? 512,
    image_node: w.image_node || "",
    lora_anchor: w.lora_anchor || "",
    workflow_json: w.workflow_json || "",
    loras_text: w.loras_text || "",
  })));
  editShow.value = true;
}

function addWorkflow() { openForm(-1); }
function editWorkflow(idx: number) { openForm(idx); }
function copyWorkflow(idx: number) {
  const src = workflows.value[idx];
  if (!src) return;
  const copy = JSON.parse(JSON.stringify(src));
  copy.name = "";
  openForm(-1, copy);
}

async function saveEdit() {
  if (!editForm.name || !editForm.name.trim()) { message.warning("名称必填"); return; }
  editForm.name = editForm.name.trim();
  saving.value = true;
  try {
    const tplKey = (workflows.value[editIndex.value] && workflows.value[editIndex.value].__template_key) || "default";
    const v = { ...editForm, __template_key: tplKey };
    if (editIndex.value < 0 || editIndex.value >= workflows.value.length) {
      workflows.value.unshift(v);
    } else {
      workflows.value[editIndex.value] = { ...workflows.value[editIndex.value], ...v };
    }
    await apiPost("config", { config: { workflows: workflows.value } });
    message.success("工作流已保存");
    editShow.value = false;
  } catch (e: any) {
    message.error(e.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

function removeWorkflow(idx: number) {
  const w = workflows.value[idx] || {};
  dialog.warning({
    title: "删除工作流",
    content: `确定要删除工作流「${w.name || ""}」吗？此操作不可恢复！`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      workflows.value.splice(idx, 1);
      try {
        await apiPost("config", { config: { workflows: workflows.value } });
        message.success("工作流已删除");
      } catch (e: any) {
        message.error(e.message || "删除失败");
      }
    },
  });
}

// 抓取封面选择（多张候选时弹出）
const coverPickShow = ref(false);
const coverPickCovers = ref<string[]>([]);
const coverPickTitle = ref("");
let coverPickOnPick: ((name: string) => void) | null = null;

function onCoverPick(name: string) {
  if (coverPickOnPick) coverPickOnPick(name);
  coverPickOnPick = null;
}

// 应用封面并保存
function applyCoverFetch(idx: number, w: any, chosenName: string) {
  w.image = chosenName;
  workflows.value = [...workflows.value];
  apiPost("config", { config: { workflows: workflows.value } }).then(() => {
    message.success("封面已保存");
  }).catch((e: any) => message.error(e.message || "保存失败"));
}

function fetchCover(idx: number) {
  const w = workflows.value[idx];
  if (!w) return;
  if (!w.civitai_url) { message.warning("请先填写 C 站链接"); return; }
  message.loading("正在抓取封面…", { duration: 10000 });
  apiPost("lora/fetch", { url: w.civitai_url }).then((d) => {
    const covers = (Array.isArray(d.images) && d.images.length) ? d.images : (d.image ? [d.image] : []);
    if (!covers.length) throw new Error("未抓取到封面图");
    if (covers.length > 1) {
      // 多张候选 → 弹封面选择
      coverPickCovers.value = covers;
      coverPickTitle.value = `为「${w.name || "工作流"}」选择封面`;
      coverPickOnPick = (chosen) => applyCoverFetch(idx, w, chosen);
      coverPickShow.value = true;
      return;
    }
    applyCoverFetch(idx, w, covers[0]);
  }).catch((e: any) => message.error(e.message || "抓取失败"));
}

function uploadCover(idx: number) {
  const w = workflows.value[idx];
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.onchange = async () => {
    const file = input.files?.[0];
    if (!file) return;
    message.loading("上传中…", { duration: 10000 });
    try {
      const reader = new FileReader();
      reader.readAsDataURL(file);
      reader.onload = async () => {
        const b64 = String(reader.result || "").split(",")[1] || "";
        const d = await apiPost("lora/upload_image", { filename: file.name, data: b64 });
        w.image = d.name;
        workflows.value = [...workflows.value];
        await apiPost("config", { config: { workflows: workflows.value } });
        message.success("封面已上传");
      };
    } catch (e: any) {
      message.error(e.message || "上传失败");
    }
  };
  input.click();
}

useRefresh(load);
onMounted(load);
</script>

<style scoped>
.workflows-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; flex: 0 0 auto; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.view-actions { display: flex; gap: 8px; }
.wf-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
.wf-card {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-panel);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.card-cover { aspect-ratio: 3 / 4; border-radius: 8px; overflow: hidden; cursor: zoom-in; background: var(--bg-body); display: flex; align-items: center; justify-content: center; }
.card-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cover-empty { color: var(--text-sub); font-size: 12px; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.card-title { font-weight: 600; font-size: 15px; }
.card-alias { color: var(--text-sub); font-size: 12px; }
.card-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.meta-item { color: var(--text-sub); }
.civ-link { color: var(--accent); text-decoration: none; font-size: 12px; }
.card-loracfg { font-size: 12px; color: var(--text-sub); }
.card-avail { font-size: 12px; color: var(--text-main); opacity: 0.85; }
.card-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.edit-form { max-height: 65vh; overflow: auto; padding-right: 4px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-hint { color: var(--text-sub); font-size: 12px; margin-left: 8px; }

@media (max-width: 768px) {
  .workflows-view { padding: 0; }
  .view-head { flex-direction: column; align-items: stretch; gap: 10px; }
  .view-actions { flex-wrap: wrap; }
  .view-actions :deep(.n-button) { flex: 1 1 auto; }
  .card-grid { grid-template-columns: 1fr; }
  .form-grid { grid-template-columns: 1fr; }
}
</style>
