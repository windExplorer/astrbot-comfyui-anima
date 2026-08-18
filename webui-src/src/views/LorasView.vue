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

    <div class="filter-bar">
      <span class="filter-label">底模：</span>
      <n-radio-group v-model:value="filterModel" size="small">
        <n-radio-button value="all">全部 ({{ loras.length }})</n-radio-button>
        <n-radio-button value="anima">anima ({{ countByModel("anima") }})</n-radio-button>
        <n-radio-button value="z-image-turbo">z-image-turbo ({{ countByModel("z-image-turbo") }})</n-radio-button>
        <n-radio-button value="krea2">krea2 ({{ countByModel("krea2") }})</n-radio-button>
        <n-radio-button value="illustrious">illustrious ({{ countByModel("illustrious") }})</n-radio-button>
        <n-radio-button value="__none__">通用 ({{ countByModel("__none__") }})</n-radio-button>
      </n-radio-group>
    </div>
    <div class="filter-bar">
      <span class="filter-label">分类：</span>
      <n-radio-group v-model:value="filterCategory" size="small">
        <n-radio-button value="all">全部 ({{ loras.length }})</n-radio-button>
        <n-radio-button value="角色">角色 ({{ countByCategory("角色") }})</n-radio-button>
        <n-radio-button value="风格">风格 ({{ countByCategory("风格") }})</n-radio-button>
        <n-radio-button value="__none__">未分类 ({{ countByCategory("__none__") }})</n-radio-button>
      </n-radio-group>
    </div>
    <div class="lora-scroll">
    <n-spin :show="loading">
      <n-empty v-if="!loading && !loras.length" description="尚未配置任何 LoRA，点「新增 LoRA」添加。" style="padding:60px" />
      <n-empty v-else-if="!loading && !filteredIndexes.length" description="当前底模分类下暂无 LoRA。" style="padding:60px" />
      <div v-else class="card-grid">
        <div v-for="idx in filteredIndexes" :key="idx" class="lora-card">
          <div class="card-cover" @click="openImage(loras[idx].image, loras[idx].name)">
            <img v-if="loras[idx].image" v-cover-lazy="loras[idx].image" alt="" loading="lazy" />
            <div v-else class="cover-empty">无封面</div>
          </div>
          <div class="card-body">
            <div class="card-title">{{ loras[idx].name || "(未命名)" }}</div>
            <div class="card-alias">别名：{{ aliasFirst(loras[idx].keywords) }}</div>
            <div class="card-meta">
              <n-tag size="tiny" :bordered="false">{{ loras[idx].base_model?.trim() || "通用" }}</n-tag>
              <n-tag v-if="loras[idx].category" size="tiny" type="info" :bordered="false">{{ loras[idx].category }}</n-tag>
              <a v-if="loras[idx].civitai_url" :href="loras[idx].civitai_url" target="_blank" rel="noopener noreferrer" class="civ-link">C站 ↗</a>
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
    </div>

    <!-- 详情弹窗 -->
    <n-modal v-model:show="detailShow" preset="card" title="LoRA 详情" style="width:520px" :bordered="false">
      <div v-if="detailItem" class="detail">
        <div class="detail-row"><b>名称：</b>{{ detailItem.name }}</div>
        <div class="detail-row"><b>分类：</b>{{ detailItem.category || "未分类" }}</div>
        <div class="detail-row"><b>底模：</b>{{ detailItem.base_model?.trim() || "通用" }}</div>
        <div class="detail-row"><b>别名：</b>{{ detailItem.keywords || "—" }}</div>
        <div class="detail-row"><b>模型文件：</b>{{ detailItem.model_name || "—" }}</div>
        <div class="detail-row"><b>触发词：</b><pre>{{ detailItem.trigger_words || "—" }}</pre></div>
        <div class="detail-row"><b>描述：</b><pre>{{ detailItem.description || "—" }}</pre></div>
        <div class="detail-row"><b>提示词预设：</b><pre>{{ detailItem.presets || "—" }}</pre></div>
      </div>
    </n-modal>

    <!-- 大图详情（全屏：左侧封面，右侧字段信息） -->
    <ItemViewer v-model:show="previewShow" :src="previewSrc" :title="previewTitle" :fields="detailFields" />
    <!-- 抓取封面选择（多张候选时弹出） -->
    <CoverPicker v-model:show="coverPickShow" :covers="coverPickCovers" :title="coverPickTitle" @pick="onCoverPick" />

    <!-- 编辑弹窗 -->
    <n-modal v-model:show="editShow" preset="card" :title="editTitle" style="width:680px" :bordered="false">
      <n-form label-placement="top" class="edit-form">
        <div class="form-grid">
          <n-form-item label="名称（引用键）"><n-input v-model:value="editForm.name" placeholder="如 安魂曲" /></n-form-item>
          <n-form-item label="分类">
            <n-select v-model:value="editForm.category" :options="categoryOptions" />
          </n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="底模">
            <n-select v-model:value="editForm.base_model" :options="baseModelOptions" />
          </n-form-item>
          <n-form-item label="模型文件名"><n-input v-model:value="editForm.model_name" placeholder="xxx.safetensors" /></n-form-item>
        </div>
        <div class="form-grid">
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
import { computed, onMounted, reactive, ref } from "vue";
import { useMessage, useDialog, NButton, NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NSwitch, NTag, NSpace, NEmpty, NSpin, NRadioGroup, NRadioButton } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import { parseAliases } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import ItemViewer, { type ItemViewerField } from "@/components/ItemViewer.vue";
import CoverPicker from "@/components/CoverPicker.vue";

const message = useMessage();
const dialog = useDialog();
const loading = ref(false);
const saving = ref(false);
const loras = ref<any[]>([]);

const baseModelOptions = ["", "anima", "z-image-turbo", "krea2", "illustrious"].map((o) => ({ label: o || "（通用）", value: o }));
const categoryOptions = ["", "角色", "风格"].map((o) => ({ label: o || "（未分类）", value: o }));

// 底模分类筛选：all=全部；__none__=通用（base_model 为空）；其余=对应底模
const filterModel = ref("all");
// 分类筛选：all=全部；__none__=未分类；其余=对应分类
const filterCategory = ref("all");

// 筛选后命中的 LoRA 在 loras 全量数组中的下标（操作按钮沿用全量 idx）；底模 + 分类双重过滤
const filteredIndexes = computed<number[]>(() => {
  const m = filterModel.value;
  const c = filterCategory.value;
  return loras.value
    .map((l, i) => ({ l, i }))
    .filter(({ l }) => {
      // 底模匹配
      const bm = (l.base_model || "").trim();
      let okModel = true;
      if (m === "__none__") okModel = bm === "";
      else if (m !== "all") okModel = bm === m;
      if (!okModel) return false;
      // 分类匹配
      const cat = (l.category || "").trim();
      if (c === "__none__") return cat === "";
      if (c === "all") return true;
      return cat === c;
    })
    .map(({ i }) => i);
});

// 底模归一化：转小写并与白名单（baseModelOptions 非空项）匹配，不在白名单返回空（通用）
function normalizeBaseModel(raw: string): string {
  const bm = (raw || "").trim().toLowerCase();
  if (!bm) return "";
  for (const opt of baseModelOptions) {
    if (opt.value && bm === opt.value) return opt.value;
  }
  // 容错前缀匹配（如 "anima v1" -> anima）
  for (const opt of baseModelOptions) {
    if (opt.value && bm.startsWith(opt.value)) return opt.value;
  }
  return "";
}

// 各底模分类的数量（用于筛选栏计数）
function countByModel(m: string): number {
  if (m === "all") return loras.value.length;
  if (m === "__none__") return loras.value.filter((l) => !((l.base_model || "").trim())).length;
  return loras.value.filter((l) => ((l.base_model || "").trim()) === m).length;
}

// 各分类的数量（用于筛选栏计数）
function countByCategory(c: string): number {
  if (c === "all") return loras.value.length;
  if (c === "__none__") return loras.value.filter((l) => !((l.category || "").trim())).length;
  return loras.value.filter((l) => ((l.category || "").trim()) === c).length;
}

async function load() {
  loading.value = true;
  try {
    const cfg = await apiGet("config");
    loras.value = Array.isArray(cfg.loras) ? cfg.loras : [];
    // 归一化存量底模（修正早期抓取存下的大写/非法值，如 "Anima"）
    loras.value = loras.value.map((l) => {
      const bm = normalizeBaseModel(l.base_model || "");
      if (bm !== (l.base_model || "").trim()) return { ...l, base_model: bm };
      return l;
    });
  } catch (e: any) {
    message.error(e.message || "加载 LoRA 失败");
  } finally {
    loading.value = false;
  }
}

function aliasFirst(raw: string): string {
  const a = parseAliases(raw);
  return a.length ? a[0] : "—";
}

// 大图详情（全屏：左侧封面，右侧字段信息）
const previewShow = ref(false);
const previewSrc = ref("");
const previewTitle = ref("");
const detailFields = ref<ItemViewerField[]>([]);

function openImage(fname: string, name: string) {
  const l = loras.value.find((x) => x.image === fname) || {};
  const realName = name || l.name || fname;
  previewSrc.value = ""; // 重置后组件显示"封面加载中…"
  detailFields.value = [
    { key: "名称", value: realName },
    { key: "分类", value: l.category?.trim() || "未分类" },
    { key: "别名", value: parseAliases(l.keywords || "").join(" / ") || "—" },
    { key: "底模", value: l.base_model?.trim() || "通用" },
    { key: "模型", value: l.model_name?.trim() || "—" },
    { key: "默认权重", value: l.weight ?? 1 },
    { key: "触发词", value: l.trigger_words?.trim() || "—" },
    { key: "提示词预设", value: l.presets?.trim() || "—" },
    { key: "描述", value: l.description?.trim() || "—" },
    { key: "封面文件", value: fname },
  ];
  if (l.civitai_url) detailFields.value.push({ key: "C 站", value: l.civitai_url, href: l.civitai_url });
  previewTitle.value = realName;
  previewShow.value = true;
  if (fname) {
    apiGet("lora/image", { name: fname }).then((d) => {
      if (d && d.url) previewSrc.value = d.url;
    }).catch(() => {});
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
    category: l.category || "",
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

// 抓取封面选择（多张候选时弹出）
const coverPickShow = ref(false);
const coverPickCovers = ref<string[]>([]);
const coverPickTitle = ref("");
let coverPickOnPick: ((name: string) => void) | null = null;

function onCoverPick(name: string) {
  if (coverPickOnPick) coverPickOnPick(name);
  coverPickOnPick = null;
}

// 应用抓取结果并保存
function applyLoraFetch(idx: number, l: any, updates: Record<string, any>) {
  loras.value[idx] = { ...l, ...updates };
  loras.value = [...loras.value];
  apiPost("config", { config: { loras: loras.value } }).then(() => {
    message.success("抓取完成并已保存");
  }).catch((e: any) => message.error(e.message || "保存失败"));
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
    // C 站标题并入别名（若不存在）：别名可能为换行/逗号分隔，避免重复
    if (d.title) {
      const oldKw = String(l.keywords || "").trim();
      const kwList = oldKw ? oldKw.split(/[,，\n\r]+/).map((s) => s.trim()).filter(Boolean) : [];
      if (!kwList.includes(d.title)) {
        updates.keywords = oldKw ? oldKw + "\n" + d.title : d.title;
      }
    }
    const covers = (Array.isArray(d.images) && d.images.length) ? d.images : (d.image ? [d.image] : []);
    // 底模兜底归一化：C 站可能返回 "Anima" 等大写，需转小写并与白名单匹配，
    // 否则与编辑下拉 / 底模筛选的小写选项对不上。
    if (updates.base_model) updates.base_model = normalizeBaseModel(updates.base_model);
    if (covers.length > 1) {
      // 多张候选 → 弹封面选择
      coverPickCovers.value = covers;
      coverPickTitle.value = `为「${l.name || "LoRA"}」选择封面`;
      coverPickOnPick = (chosen) => applyLoraFetch(idx, l, { ...updates, image: chosen });
      coverPickShow.value = true;
      return;
    }
    if (covers.length === 1) updates.image = covers[0];
    applyLoraFetch(idx, l, updates);
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
.loras-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; flex: 0 0 auto; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.view-actions { display: flex; gap: 8px; }
.filter-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex: 0 0 auto; flex-wrap: wrap; }
.filter-label { color: var(--text-sub); font-size: 13px; }
.lora-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }
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
