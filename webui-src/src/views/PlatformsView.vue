<template>
  <div class="platforms-view">
    <n-space vertical :size="16">
      <!-- 当前使用平台 -->
      <n-card title="当前使用平台" size="small">
        <n-space align="center" :size="12">
          <n-select
            v-model:value="cfg.active_platform"
            :options="activeOptions"
            style="width: 280px"
            @update:value="markDirty"
          />
          <n-button type="primary" :disabled="!dirty" :loading="saving" @click="saveAll">保存</n-button>
          <span class="hint">
            ComfyUI 服务器在「配置」页管理；切换并保存后，新出图即走所选平台（第三方平台不支持 LoRA/工作流/图生图）。
          </span>
        </n-space>
      </n-card>

      <!-- 平台列表 -->
      <n-card title="生图平台" size="small">
        <template #header-extra>
          <n-button size="small" type="primary" ghost @click="openAdd">+ 添加平台</n-button>
        </template>
        <n-empty v-if="!cfg.platforms.length" description="还没有第三方平台。点击右上角「添加平台」接入 NAI / OpenAI 兼容 / 自定义生图服务。" style="padding: 24px 0" />
        <n-space vertical :size="10">
          <div v-for="p in cfg.platforms" :key="p.id" class="plat-card">
            <div class="plat-head">
              <n-tag size="small" :type="typeTag(p.type)">{{ typeLabel(p.type) }}</n-tag>
              <b>{{ p.name || "（未命名）" }}</b>
              <span class="hint">{{ p.base_url || p.url || "" }}</span>
              <span class="spacer" />
              <n-switch v-model:value="p.enabled" size="small" @update:value="markDirty" />
              <span class="hint">{{ p.enabled ? "启用" : "停用" }}</span>
              <n-button size="tiny" @click="openEdit(p)">编辑</n-button>
              <n-button size="tiny" type="error" ghost @click="removePlatform(p)">删除</n-button>
            </div>
            <div class="plat-meta">
              模型：{{ p.model || "（未配置）" }}
              <template v-if="p.type === 'nai'"> · 中转模式：{{ p.via_middle_station ? "是" : "否" }}</template>
            </div>
          </div>
        </n-space>
        <div class="hint" style="margin-top: 8px">
          同一类型建议只启用一个：生图时优先用启用的那个；多个启用时取第一个。停用全部该类型则无法走该类型平台。
        </div>
      </n-card>

      <!-- 画师串预设 -->
      <n-card title="画师串预设（NAI 等标签类平台）" size="small">
        <template #header-extra>
          <n-button size="small" @click="openPresetAdd('artist')">+ 添加</n-button>
        </template>
        <n-empty v-if="!cfg.artist_presets.length" description="暂无画师串预设。启用中的第一个预设会自动追加到 NAI 提示词前部。" style="padding: 12px 0" />
        <n-space vertical :size="8">
          <div v-for="pr in cfg.artist_presets" :key="pr.id" class="preset-row">
            <n-tag size="small" :type="pr.enabled ? 'success' : 'default'">{{ pr.enabled ? "启用" : "停用" }}</n-tag>
            <b>{{ pr.name }}</b>
            <span class="preset-content">{{ pr.content }}</span>
            <span class="spacer" />
            <n-button size="tiny" @click="openPresetEdit('artist', pr)">编辑</n-button>
            <n-button size="tiny" type="error" ghost @click="removePreset('artist', pr)">删除</n-button>
          </div>
        </n-space>
      </n-card>

      <!-- 负面词模板 -->
      <n-card title="负面词模板（跨平台共享）" size="small">
        <template #header-extra>
          <n-button size="small" @click="openPresetAdd('negative')">+ 添加</n-button>
        </template>
        <n-empty v-if="!cfg.negative_presets.length" description="暂无负面词模板。提示词未带负面词时，自动合并所有启用模板。" style="padding: 12px 0" />
        <n-space vertical :size="8">
          <div v-for="pr in cfg.negative_presets" :key="pr.id" class="preset-row">
            <n-tag size="small" :type="pr.enabled ? 'success' : 'default'">{{ pr.enabled ? "启用" : "停用" }}</n-tag>
            <b>{{ pr.name }}</b>
            <span class="preset-content">{{ pr.content }}</span>
            <span class="spacer" />
            <n-button size="tiny" @click="openPresetEdit('negative', pr)">编辑</n-button>
            <n-button size="tiny" type="error" ghost @click="removePreset('negative', pr)">删除</n-button>
          </div>
        </n-space>
      </n-card>
    </n-space>

    <!-- 平台编辑弹窗 -->
    <n-modal v-model:show="showEdit" preset="card" :title="editIsNew ? '添加平台' : '编辑平台'" style="width: 620px">
      <n-form label-placement="left" label-width="120" size="small">
        <n-form-item label="平台类型">
          <n-select v-model:value="editing.type" :options="typeOptions" :disabled="!editIsNew" @update:value="onTypeChange" />
        </n-form-item>
        <n-form-item label="显示名">
          <n-input v-model:value="editing.name" placeholder="如：NAI 官方 / newapi 中转" />
        </n-form-item>
        <n-form-item label="接口地址">
          <n-input v-model:value="editing.base_url" :placeholder="baseUrlPlaceholder" />
        </n-form-item>
        <n-form-item label="API Key / Token">
          <n-input v-model:value="editing.api_key" type="password" show-password-on="click" placeholder="必填" />
        </n-form-item>
        <n-form-item label="模型">
          <n-input v-model:value="editing.model" :placeholder="modelPlaceholder" />
        </n-form-item>
        <n-form-item label="可用用户">
          <n-input v-model:value="editing.allowed_users_text" placeholder="逗号分隔的 QQ 号；留空 = 仅管理员可用" />
        </n-form-item>
        <template v-if="editing.type === 'nai'">
          <n-form-item label="走中转站">
            <n-space align="center">
              <n-switch v-model:value="editing.via_middle_station" size="small" />
              <span class="hint">开启后按中转站 GET /generate 调用；关闭走 NAI 官方 /ai/generate-image</span>
            </n-space>
          </n-form-item>
          <n-form-item label="默认尺寸">
            <n-select v-model:value="editing.defaults.size" :options="naiSizeOptions" />
          </n-form-item>
          <n-form-item label="步数 steps">
            <n-input-number v-model:value="editing.defaults.steps" :min="1" :max="50" style="width: 160px" />
          </n-form-item>
          <n-form-item label="引导强度 scale">
            <n-input-number v-model:value="editing.defaults.scale" :min="0" :max="20" :step="0.5" style="width: 160px" />
          </n-form-item>
          <n-form-item label="CFG Rescale">
            <n-input-number v-model:value="editing.defaults.cfg_rescale" :min="0" :max="1" :step="0.1" style="width: 160px" />
          </n-form-item>
          <n-form-item label="采样器">
            <n-select v-model:value="editing.defaults.sampler" :options="samplerOptions" />
          </n-form-item>
          <n-form-item label="噪声调度">
            <n-select v-model:value="editing.defaults.noise_schedule" :options="noiseOptions" />
          </n-form-item>
          <n-form-item label="负面词">
            <n-input v-model:value="editing.defaults.negative" type="textarea" :rows="2" placeholder="留空则使用下方启用的负面词模板" />
          </n-form-item>
        </template>
        <template v-if="editing.type === 'openai'">
          <n-form-item label="尺寸">
            <n-select v-model:value="editing.size" :options="openaiSizeOptions" tag filterable />
          </n-form-item>
          <n-form-item label="质量 quality">
            <n-input v-model:value="editing.quality" placeholder="可选，如 high / standard（不填省略）" />
          </n-form-item>
          <n-form-item label="负面词">
            <n-input v-model:value="editing.negative" type="textarea" :rows="2" placeholder="部分模型不支持，会被上游忽略" />
          </n-form-item>
        </template>
        <template v-if="editing.type === 'custom'">
          <n-form-item label="请求方法">
            <n-select v-model:value="editing.method" :options="[{ label: 'POST', value: 'POST' }, { label: 'GET', value: 'GET' }]" />
          </n-form-item>
          <n-form-item label="请求体模板">
            <n-input v-model:value="editing.body_template" type="textarea" :rows="5" placeholder='{"prompt":"{{prompt}}","width":{{width}},"height":{{height}}}' />
          </n-form-item>
          <n-form-item label="响应类型">
            <n-select v-model:value="editing.resp_type" :options="[{ label: 'JSON 内 base64', value: 'b64_json' }, { label: 'JSON 内图片 URL', value: 'url' }, { label: '直接返回图片二进制', value: 'binary' }]" />
          </n-form-item>
          <n-form-item label="提取路径">
            <n-input v-model:value="editing.resp_path" placeholder="如 data.0.b64_json（留空自动探测）" />
          </n-form-item>
          <n-form-item label="额外 Headers">
            <n-input v-model:value="editing.headers_text" type="textarea" :rows="2" placeholder='{"Authorization":"Bearer {{api_key}}"}（JSON，可空）' />
          </n-form-item>
        </template>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showEdit = false">取消</n-button>
          <n-button type="primary" @click="applyEdit">确定</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 预设编辑弹窗 -->
    <n-modal v-model:show="showPreset" preset="card" :title="presetIsNew ? '添加预设' : '编辑预设'" style="width: 560px">
      <n-form label-placement="left" label-width="90" size="small">
        <n-form-item label="名称">
          <n-input v-model:value="editingPreset.name" placeholder="如：韩漫小清新 / 通用低质" />
        </n-form-item>
        <n-form-item :label="presetKind === 'artist' ? '画师串' : '负面词'">
          <n-input v-model:value="editingPreset.content" type="textarea" :rows="4" placeholder="逗号分隔的标签/短语" />
        </n-form-item>
        <n-form-item label="启用">
          <n-switch v-model:value="editingPreset.enabled" size="small" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showPreset = false">取消</n-button>
          <n-button type="primary" @click="applyPreset">确定</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from "vue";
import {
  NCard, NSpace, NButton, NSelect, NSwitch, NTag, NEmpty, NModal,
  NForm, NFormItem, NInput, NInputNumber, useMessage,
} from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";

const message = useMessage();

const cfg = reactive<any>({
  active_platform: "comfyui",
  platforms: [],
  artist_presets: [],
  negative_presets: [],
});
const dirty = ref(false);
const saving = ref(false);

function markDirty() { dirty.value = true; }

async function load() {
  try {
    const d = await apiGet("platforms");
    if (d && typeof d === "object") {
      cfg.active_platform = d.active_platform || "comfyui";
      cfg.platforms = Array.isArray(d.platforms) ? d.platforms : [];
      cfg.artist_presets = Array.isArray(d.artist_presets) ? d.artist_presets : [];
      cfg.negative_presets = Array.isArray(d.negative_presets) ? d.negative_presets : [];
    }
    dirty.value = false;
  } catch (e) {
    message.error(`读取平台配置失败：${(e as Error)?.message || e}`);
  }
}

async function saveAll() {
  saving.value = true;
  try {
    await apiPost("platforms/save", JSON.parse(JSON.stringify(cfg)));
    message.success("平台配置已保存");
    dirty.value = false;
  } catch (e) {
    message.error(`保存失败：${(e as Error)?.message || e}`);
  } finally {
    saving.value = false;
  }
}

const activeOptions = computed(() => [
  { label: "ComfyUI（本地/自建）", value: "comfyui" },
  ...cfg.platforms
    .filter((p: any) => p.enabled)
    .map((p: any) => ({ label: `${p.name || "未命名"}（${typeLabel(p.type)}）`, value: p.id })),
]);

function typeLabel(t: string): string {
  return ({ nai: "NAI", openai: "OpenAI 兼容", custom: "自定义" } as any)[t] || t || "未知";
}
function typeTag(t: string): any {
  return ({ nai: "success", openai: "info", custom: "warning" } as any)[t] || "default";
}

const typeOptions = [
  { label: "NAI（NovelAI / 中转站）", value: "nai" },
  { label: "OpenAI 兼容（官方 / newapi / 聚合）", value: "openai" },
  { label: "自定义 HTTP（实验）", value: "custom" },
];
const naiSizeOptions = [
  { label: "竖图 832x1216", value: "portrait" },
  { label: "横图 1216x832", value: "landscape" },
  { label: "方图 1024x1024", value: "square" },
  { label: "2K 竖图 1536x2304", value: "2Kportrait" },
  { label: "2K 横图 2304x1536", value: "2Klandscape" },
  { label: "4K 竖图 2048x3072", value: "4Kportrait" },
  { label: "4K 横图 3072x2048", value: "4Klandscape" },
];
const openaiSizeOptions = [
  { label: "1024x1024", value: "1024x1024" },
  { label: "1024x1536", value: "1024x1536" },
  { label: "1536x1024", value: "1536x1024" },
  { label: "1792x1024", value: "1792x1024" },
  { label: "1024x1792", value: "1024x1792" },
];
const samplerOptions = [
  "k_dpmpp_2m_sde", "k_dpmpp_2m", "k_dpmpp_sde", "k_dpmpp_2s_ancestral",
  "k_euler_ancestral", "k_euler", "ddim",
].map((s) => ({ label: s, value: s }));
const noiseOptions = ["karras", "native", "exponential", "polyexponential"].map((s) => ({ label: s, value: s }));

const baseUrlPlaceholder = computed(() => {
  if (editing.type === "nai") return "https://image.novelai.net 或中转站地址";
  if (editing.type === "openai") return "https://api.openai.com 或 newapi 中转地址";
  return "完整接口地址（含 http(s)://）";
});
const modelPlaceholder = computed(() => {
  if (editing.type === "nai") return "nai-diffusion-4-5-full / nai-diffusion-5-full";
  if (editing.type === "openai") return "gpt-image-1 / dall-e-3 / flux / 中转站模型名";
  return "（可选，供 {{model}} 占位）";
});

// ---- 平台编辑 ----
const showEdit = ref(false);
const editIsNew = ref(false);
const editing = reactive<any>(emptyPlatform("openai"));

function emptyPlatform(type: string) {
  const base: any = {
    id: "",
    type,
    name: "",
    base_url: "",
    api_key: "",
    model: "",
    enabled: true,
    allowed_users_text: "",
  };
  if (type === "nai") {
    base.via_middle_station = false;
    base.defaults = { size: "portrait", steps: 28, scale: 6, cfg_rescale: 0.3, sampler: "k_dpmpp_2m_sde", noise_schedule: "karras", negative: "" };
  }
  if (type === "openai") {
    base.size = "1024x1024";
    base.quality = "";
    base.negative = "";
  }
  if (type === "custom") {
    base.method = "POST";
    base.body_template = "";
    base.resp_type = "b64_json";
    base.resp_path = "";
    base.headers_text = "";
  }
  return base;
}

function openAdd() {
  Object.assign(editing, emptyPlatform("openai"));
  editIsNew.value = true;
  showEdit.value = true;
}

function openEdit(p: any) {
  const copy = emptyPlatform(p.type || "openai");
  Object.assign(copy, JSON.parse(JSON.stringify(p)));
  if (!copy.defaults) copy.defaults = emptyPlatform("nai").defaults;
  if (p.type === "custom" && p.headers && typeof p.headers === "object") {
    copy.headers_text = JSON.stringify(p.headers);
  }
  copy.allowed_users_text = Array.isArray(p.allowed_users) ? p.allowed_users.join(",") : "";
  Object.assign(editing, copy);
  editIsNew.value = false;
  showEdit.value = true;
}

function onTypeChange(t: string) {
  // 切类型时重置该类型专有字段（保留公共字段）
  const common = { id: editing.id, type: t, name: editing.name, enabled: editing.enabled };
  Object.assign(editing, emptyPlatform(t), common);
}

function applyEdit() {
  if (!editing.name.trim()) { message.error("请填写显示名"); return; }
  if (editing.type !== "custom" && !String(editing.base_url || "").trim()) {
    message.error("请填写接口地址");
    return;
  }
  if (!String(editing.api_key || "").trim()) { message.error("请填写 API Key / Token"); return; }
  const item = JSON.parse(JSON.stringify(editing));
  if (!item.id) item.id = `p_${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  item.allowed_users = String(item.allowed_users_text || "")
    .split(/[,，;；\s]+/)
    .map((s: string) => s.trim())
    .filter(Boolean);
  delete item.allowed_users_text;
  if (item.type === "custom") {
    try {
      item.headers = item.headers_text ? JSON.parse(item.headers_text) : {};
    } catch {
      message.error("额外 Headers 不是合法 JSON");
      return;
    }
    delete item.headers_text;
    if (!String(item.body_template || "").trim()) { message.error("请填写请求体模板"); return; }
  }
  const idx = cfg.platforms.findIndex((p: any) => p.id === item.id);
  if (idx >= 0) cfg.platforms[idx] = item;
  else cfg.platforms.push(item);
  showEdit.value = false;
  markDirty();
  message.success("已加入列表，记得点「保存」提交");
}

function removePlatform(p: any) {
  cfg.platforms = cfg.platforms.filter((x: any) => x.id !== p.id);
  if (cfg.active_platform === p.id) cfg.active_platform = "comfyui";
  markDirty();
}

// ---- 预设编辑 ----
const showPreset = ref(false);
const presetIsNew = ref(false);
const presetKind = ref<"artist" | "negative">("artist");
const editingPreset = reactive<any>({ id: "", name: "", content: "", enabled: true });

function presetList() {
  return presetKind.value === "artist" ? cfg.artist_presets : cfg.negative_presets;
}

function openPresetAdd(kind: "artist" | "negative") {
  presetKind.value = kind;
  Object.assign(editingPreset, { id: "", name: "", content: "", enabled: true });
  presetIsNew.value = true;
  showPreset.value = true;
}

function openPresetEdit(kind: "artist" | "negative", pr: any) {
  presetKind.value = kind;
  Object.assign(editingPreset, JSON.parse(JSON.stringify(pr)));
  presetIsNew.value = false;
  showPreset.value = true;
}

function applyPreset() {
  if (!editingPreset.name.trim()) { message.error("请填写名称"); return; }
  const list = presetList();
  const idx = list.findIndex((p: any) => p.id === editingPreset.id);
  if (idx >= 0) list[idx] = JSON.parse(JSON.stringify(editingPreset));
  else {
    const item = JSON.parse(JSON.stringify(editingPreset));
    item.id = `ps_${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
    list.push(item);
  }
  showPreset.value = false;
  markDirty();
}

function removePreset(kind: "artist" | "negative", pr: any) {
  if (kind === "artist") cfg.artist_presets = cfg.artist_presets.filter((x: any) => x.id !== pr.id);
  else cfg.negative_presets = cfg.negative_presets.filter((x: any) => x.id !== pr.id);
  markDirty();
}

onMounted(load);
</script>

<style scoped>
.platforms-view {
  max-width: 980px;
}
.plat-card {
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 10px 12px;
}
.plat-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.plat-meta {
  margin-top: 6px;
  font-size: 12px;
  opacity: 0.75;
}
.preset-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.preset-content {
  font-size: 12px;
  opacity: 0.7;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 480px;
}
.spacer {
  flex: 1;
}
.hint {
  font-size: 12px;
  opacity: 0.65;
}
</style>
