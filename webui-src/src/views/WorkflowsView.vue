<template>
  <div class="workflows-view">
    <div class="view-head">
      <div>
        <h2>工作流</h2>
        <p>卡片式查看工作流：名称、别名、底模、服务器、是否 Anima；可编辑、查看可用 LoRA。</p>
      </div>
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="view-actions">
          <n-button :loading="loading" @click="load">刷新</n-button>
          <n-button type="primary" @click="addWorkflow">＋ 新增工作流</n-button>
        </div>
      </Teleport>
    </div>

    <!-- 工作流类型筛选：文生图 / 图生图（按是否配置参考图节点 image_node 判定） -->
    <div class="filter-bar">
      <n-radio-group v-model:value="filterType" size="small" class="filter-radios">
        <n-radio-button value="all">全部</n-radio-button>
        <n-radio-button value="txt2img">文生图</n-radio-button>
        <n-radio-button value="img2img">图生图</n-radio-button>
      </n-radio-group>
      <span class="filter-hint">类型按「参考图节点」是否配置自动判定</span>
    </div>

    <div class="wf-scroll">
    <n-spin :show="loading">
      <n-empty v-if="!loading && !filteredWorkflows.length" description="没有符合筛选条件的工作流。" style="padding:60px" />
      <div v-else class="card-grid">
        <div v-for="({ w, i }, _) in filteredWorkflows" :key="i" class="wf-card">
          <div class="card-cover" @click="openImage(w.image, w.name)">
            <img v-if="w.image" v-cover-lazy="w.image" alt="" loading="lazy" />
            <div v-else class="cover-empty">无封面</div>
          </div>
          <div class="card-head">
            <span class="card-title">{{ w.name || "(未命名)" }}</span>
            <n-tag v-if="w.enabled === false" size="small" type="error" :bordered="false">已停用</n-tag>
            <n-tag v-if="w.is_anima" size="small" type="info" :bordered="false">Anima</n-tag>
            <n-tag v-if="(w.image_node || '').trim()" size="small" type="success" :bordered="false">图生图</n-tag>
            <n-tag v-else size="small" type="default" :bordered="false">文生图</n-tag>
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
            <n-button size="tiny" @click="editWorkflow(i)">编辑</n-button>
            <n-button size="tiny" @click="toggleEnabled(i)">{{ w.enabled === false ? "启用" : "停用" }}</n-button>
            <n-button size="tiny" @click="copyWorkflow(i)">复制</n-button>
            <n-button size="tiny" @click="fetchCover(i)">抓封面</n-button>
            <n-button size="tiny" @click="uploadCover(i)">传封面</n-button>
            <n-button size="tiny" type="error" @click="removeWorkflow(i)">删除</n-button>
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
        <n-form-item label="启用该工作流">
          <n-switch v-model:value="editForm.enabled" />
          <span class="form-hint">关闭后不可使用：显式指定会提示「已停用」，自动选择默认工作流也会跳过它</span>
        </n-form-item>
        <n-form-item label="锁定提示词（无需用户传词）">
          <n-switch
            v-model:value="editForm.require_prompt"
            :checked-value="false"
            :unchecked-value="true"
          />
          <span class="form-hint">开启后该工作流无需提示词即可出图（如「动漫转真人」，只需传图/引用图）；用户传了提示词也会被忽略</span>
        </n-form-item>
        <div class="form-grid">
          <n-form-item label="固定正向提示词（可选）"><n-input v-model:value="editForm.default_positive" type="textarea" :rows="2" placeholder="锁定提示词时用此提示词覆盖工作流 JSON；未锁定时用户不传词也会兜底；不走翻译/改写" /></n-form-item>
          <n-form-item label="固定负向提示词（可选）"><n-input v-model:value="editForm.default_negative" type="textarea" :rows="2" placeholder="用户未传负向提示词时，用此覆盖工作流 JSON 内的负向提示词" /></n-form-item>
        </div>
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
          <n-form-item label="主模节点（lora_anchor）"><n-input v-model:value="editForm.lora_anchor" placeholder="CheckpointLoader/UNETLoader 键名，留空自动探测" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="放大模型节点"><n-input v-model:value="editForm.upscale_node_id" placeholder="放大模型加载节点键名（如 14）" /></n-form-item>
          <n-form-item label="放大模型名称"><n-input v-model:value="editForm.upscale_model_name" placeholder="替换成的放大模型文件名（如 4x-UltraSharp.pth）" /></n-form-item>
        </div>
        <n-form-item label="CLIP 节点（lora_clip）"><n-input v-model:value="editForm.lora_clip" placeholder="完整模式用，CLIPLoader 键名，留空自动探测" /></n-form-item>

        <n-divider style="margin:8px 0">── 采样器参数（steps / cfg / denoise）──</n-divider>
        <n-form-item label="从工作流文件读取">
          <n-space vertical :size="6" style="width:100%">
            <n-button size="tiny" :loading="samplerLoading" @click="fetchSamplerParams">↻ 读取文件中的采样器参数</n-button>
            <span v-if="samplerHint" class="form-hint" style="color:#c2255c">{{ samplerHint }}</span>
            <span v-else class="form-hint">根据上方「工作流文件名」读取文件采样器节点的默认 steps / cfg / denoise，自动填入下方字段</span>
          </n-space>
        </n-form-item>
        <div class="form-grid">
          <n-form-item label="默认 steps">
            <n-space vertical :size="4" style="width:100%">
              <n-input-number v-model:value="editForm.default_steps" :min="0" :max="200" :step="1" :disabled="editForm.steps_off" style="width:100%" />
              <n-checkbox v-model:checked="editForm.steps_off">不注入（沿用工作流原值）</n-checkbox>
            </n-space>
          </n-form-item>
          <n-form-item label="默认 CFG">
            <n-space vertical :size="4" style="width:100%">
              <n-input-number v-model:value="editForm.default_cfg" :min="0" :max="30" :step="0.5" :precision="2" :disabled="editForm.cfg_off" style="width:100%" />
              <n-checkbox v-model:checked="editForm.cfg_off">不注入（沿用工作流原值）</n-checkbox>
            </n-space>
          </n-form-item>
        </div>
        <n-form-item label="默认 denoise">
          <n-space vertical :size="4" style="width:100%">
            <n-input-number v-model:value="editForm.default_denoise" :min="-1" :max="1" :step="0.05" :precision="2" :disabled="editForm.denoise_off" style="width:100%" />
            <n-checkbox v-model:checked="editForm.denoise_off">不注入（-1，沿用工作流原始值）</n-checkbox>
          </n-space>
        </n-form-item>
        <n-form-item label="工作流 JSON（可直接粘贴）"><n-input v-model:value="editForm.workflow_json" type="textarea" :rows="3" /></n-form-item>
        <n-form-item label="默认 LoRA">
          <div class="lora-list">
            <div v-for="(row, ri) in (editForm.loraList || [])" :key="ri" class="lora-row">
              <n-select
                v-model:value="row.name"
                :options="loraOptions"
                filterable
                clearable
                placeholder="选择 LoRA（可搜索）"
                style="flex: 1; min-width: 120px"
              />
              <n-input-number
                v-model:value="row.weight"
                :min="0"
                :max="2"
                :step="0.05"
                :precision="2"
                placeholder="权重"
                style="width: 110px"
              />
              <n-switch v-model:value="row.enabled" size="small">
                <template #checked>启用</template>
                <template #unchecked>停用</template>
              </n-switch>
              <n-button size="tiny" quaternary type="error" @click="removeLoraRow(ri)">删除</n-button>
            </div>
            <n-space style="margin-top: 6px">
              <n-button size="tiny" @click="addLoraRow">＋ 添加 LoRA</n-button>
              <n-button size="tiny" @click="refreshLoras">↻ 刷新 LoRA 列表</n-button>
            </n-space>
            <div class="form-hint">从全局 LoRA 库下拉选择（可搜索）；保存后写回 loras_text（名称|权重|0/1）</div>
          </div>
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
import { useMessage, useDialog, NButton, NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NSwitch, NTag, NSpace, NDivider, NEmpty, NSpin, NCheckbox, NRadioGroup, NRadioButton } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import { parseAliases, truncate } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import { useDevice } from "@/composables/useDevice";
import ItemViewer, { type ItemViewerField } from "@/components/ItemViewer.vue";
import CoverPicker from "@/components/CoverPicker.vue";

const message = useMessage();
const dialog = useDialog();
const { isMobile } = useDevice();
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

// ── LoRA 图形化：与后端 _parse_loras_text / _serialize_loras_text 格式保持一致 ──
type LoraRow = { name: string; weight: number; enabled: boolean };

function parseLorasText(text: string): LoraRow[] {
  const out: LoraRow[] = [];
  for (const line of (text || "").split("\n")) {
    const s = line.trim();
    if (!s || s.startsWith("#")) continue;
    const parts = s.split("|").map((p) => p.trim());
    const name = parts[0] || "";
    if (!name) continue;
    const rawW = parseFloat(parts[1] || "");
    const rawE = (parts[2] || "").toLowerCase();
    const enabled = !(rawE && ["0", "0.0", "false", "禁用", "关"].includes(rawE));
    out.push({ name, weight: isNaN(rawW) ? 1.0 : rawW, enabled });
  }
  return out;
}

function serializeLorasText(list: LoraRow[] | undefined | null): string {
  return (list || [])
    .filter((l) => l && (l.name || "").trim())
    .map((l) => {
      const w = Number(l.weight);
      return `${(l.name || "").trim()}|${!isNaN(w) ? w : 1.0}|${l.enabled ? 1 : 0}`;
    })
    .join("\n");
}

// 工作流类型筛选：文生图 / 图生图（按是否配置参考图节点 image_node 判定，无新增字段）
const filterType = ref<"all" | "txt2img" | "img2img">("all");
const filteredWorkflows = computed(() => {
  const items = workflows.value.map((w, i) => ({ w, i }));
  if (filterType.value === "all") return items;
  return items.filter(({ w }) => {
    const isImg2img = !!((w.image_node || "").trim());
    return filterType.value === "img2img" ? isImg2img : !isImg2img;
  });
});

// LoRA 下拉选项：全局 LoRA 库 + 已选但库中不存在的名称（保留老配置可编辑，标记未知）
const loraOptions = computed(() => {
  const known = new Set<string>();
  const opts = loras.value
    .filter((l) => (l.name || "").trim())
    .map((l) => {
      const n = (l.name || "").trim();
      known.add(n);
      return { label: n, value: n };
    });
  for (const row of editForm.loraList || []) {
    const n = (row.name || "").trim();
    if (n && !known.has(n)) {
      known.add(n);
      opts.push({ label: `${n}（库中不存在）`, value: n });
    }
  }
  return opts;
});

function addLoraRow() {
  if (!editForm.loraList) editForm.loraList = [];
  editForm.loraList.push({ name: "", weight: 1.0, enabled: true });
}
function removeLoraRow(i: number) {
  editForm.loraList.splice(i, 1);
}
function refreshLoras() {
  message.loading("正在刷新 LoRA 列表…", { duration: 3000 });
  load();
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
    lora_clip: w.lora_clip || "",
    upscale_node_id: w.upscale_node_id || "",
    upscale_model_name: w.upscale_model_name || "",
    default_steps: w.default_steps ?? 0,
    steps_off: !!w.steps_off,
    default_cfg: w.default_cfg ?? 0,
    cfg_off: !!w.cfg_off,
    default_denoise: (w.default_denoise ?? -1),
    denoise_off: (w.default_denoise ?? -1) <= -1,
    workflow_json: w.workflow_json || "",
    enabled: w.enabled !== false,
    require_prompt: w.require_prompt !== false,
    default_positive: w.default_positive || "",
    default_negative: w.default_negative || "",
    loras_text: w.loras_text || "",
    loraList: parseLorasText(w.loras_text || ""),
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
    // denoise 开关：勾选不注入时强制 -1（低于 min 的语义值，后端识别为「不注入」）
    if (v.denoise_off) v.default_denoise = -1;
    delete v.denoise_off;
    // LoRA 图形化列表 → 序列化为后端兼容的 loras_text（每行 名称|权重|0/1）
    v.loras_text = serializeLorasText(v.loraList);
    delete v.loraList;
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

// 从工作流文件读取采样器参数（steps / cfg / denoise）
const samplerLoading = ref(false);
const samplerHint = ref("");
async function fetchSamplerParams() {
  const name = (editForm.workflow_name || "").trim();
  if (!name) { message.warning("请先填写工作流文件名"); return; }
  samplerLoading.value = true;
  samplerHint.value = "";
  try {
    // 复用已长期可用的 /config 路由（query 带 workflow_sampler 返回采样参数），
    // 规避新增路由在前端桥接候选路径上的兼容问题。
    const d = await apiGet("config", { workflow_sampler: name });
    const hasAny = !!d && typeof d === "object" && (d.steps != null || d.cfg != null || d.denoise != null);
    if (hasAny) {
      // 读到文件默认值：字段为空/未设置才自动填入（不覆盖用户已填值）；无论是否填入都在下方显示文件值
      const parts: string[] = [];
      if (d.steps != null) {
        parts.push(`steps ${d.steps}`);
        if (!editForm.steps_off && !editForm.default_steps) editForm.default_steps = d.steps;
      }
      if (d.cfg != null) {
        parts.push(`cfg ${d.cfg}`);
        if (!editForm.cfg_off && !editForm.default_cfg) editForm.default_cfg = d.cfg;
      }
      if (d.denoise != null) {
        parts.push(`denoise ${d.denoise}`);
        if (!editForm.denoise_off && editForm.default_denoise <= -1) editForm.default_denoise = d.denoise;
      }
      samplerHint.value = "文件默认值：" + parts.join("　");
      message.success("已读取工作流文件的采样器参数");
    } else {
      // 区分「返回了整个配置」vs「返回了 null」vs「报错」
      const isConfigLike = !!d && typeof d === "object"
        && (Array.isArray(d.workflows) || Array.isArray(d.lora_library) || "provider_settings" in d || "draw_limit" in d);
      if (isConfigLike) {
        message.warning("返回的是插件配置而非采样参数：请确认插件后端已更新到 v4.9.12+ 并重载插件后再试");
      } else {
        message.warning((d && d.error) || "未读取到采样器参数（文件里没有采样器节点？或插件后端未更新到 v4.9.12+）");
      }
    }
  } catch (e: any) {
    message.error(e.message || "读取失败");
  } finally {
    samplerLoading.value = false;
  }
}

// 快捷启用/停用工作流
async function toggleEnabled(idx: number) {
  const w = workflows.value[idx];
  if (!w) return;
  const next = w.enabled === false;
  w.enabled = next;
  try {
    await apiPost("config", { config: { workflows: workflows.value } });
    message.success(next ? `已启用「${w.name || ""}」` : `已停用「${w.name || ""}」`);
  } catch (e: any) {
    w.enabled = !next;
    message.error(e.message || "保存失败");
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
  apiPost("lora/fetch", { url: w.civitai_url }, { timeout: 60000 }).then((d) => {
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
.filter-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex: 0 0 auto; flex-wrap: wrap; }
.filter-hint { color: var(--text-sub); font-size: 12px; }
.filter-radios { display: flex; flex-wrap: wrap; gap: 4px; }
.filter-radios :deep(.n-radio-group) { flex-wrap: wrap; gap: 4px; }
.filter-radios :deep(.n-radio-button) { flex: 0 0 auto; }
.lora-list { width: 100%; }
.lora-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
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
