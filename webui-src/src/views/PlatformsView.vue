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
              <n-switch v-model:value="p.enabled" size="small" @update:value="saveAll" />
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
        <n-form-item v-if="editIsNew" label="快速预设">
          <n-select v-model:value="presetKey" :options="presetOptions" placeholder="选一个预设模板，字段自动填好，只需填 Key" @update:value="applyPresetTemplate" />
        </n-form-item>
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
        <n-form-item label="自定义请求头">
          <div class="kv-editor">
            <div v-for="(h, hi) in editing.headers_list" :key="hi" class="kv-row">
              <n-input v-model:value="h.key" size="small" placeholder="Header 名，如 X-Region" style="width: 180px" />
              <n-input v-model:value="h.value" size="small" placeholder="值，可用 api_key 等占位符" style="flex: 1" />
              <n-button size="tiny" type="error" ghost @click="editing.headers_list.splice(hi, 1)">删</n-button>
            </div>
            <n-button size="tiny" dashed @click="editing.headers_list.push({ key: '', value: '' })">+ 添加请求头</n-button>
            <div class="hint">追加到生图请求的 HTTP 头，可用于鉴权、地区路由等；每条一个头。</div>
          </div>
        </n-form-item>
        <n-form-item v-if="editing.type !== 'nai'" label="额外参数">
          <div class="kv-editor">
            <div v-for="(ep, ei) in editing.extra_params" :key="ei" class="kv-row">
              <n-input v-model:value="ep.key" size="small" placeholder="参数名" style="width: 160px" />
              <n-input v-model:value="ep.value" size="small" :placeholder="editing.type === 'custom' ? '值，支持 prompt 等占位符' : '值'" style="flex: 1" />
              <n-select v-model:value="ep.vtype" size="small" :options="vtypeOptions" style="width: 96px" />
              <n-button size="tiny" type="error" ghost @click="editing.extra_params.splice(ei, 1)">删</n-button>
            </div>
            <n-button size="tiny" dashed @click="editing.extra_params.push({ key: '', value: '', vtype: 'text' })">+ 添加参数</n-button>
            <div class="hint">
              {{ editing.type === 'openai' ? '并入请求体顶层（中转站/模型专属字段，如 response_format）' : '逐条拼装请求体，无需手写 JSON；value 支持 prompt / negative / width / height / seed 等占位符' }}
            </div>
          </div>
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
          <n-form-item label="parameters 包装">
            <n-space align="center">
              <n-switch v-model:value="editing.use_parameters_wrapper" size="small" @update:value="markDirty" />
              <span class="hint">仅 NAI 中转等私有扩展端点需要开启（negative/steps 等打包进 parameters 对象）；标准 OpenAI 兼容端点保持关闭，否则可能报 400「parameters is not supported」</span>
            </n-space>
          </n-form-item>
        </template>
        <template v-if="editing.type === 'custom'">
          <n-form-item label="请求方法">
            <n-select v-model:value="editing.method" :options="[{ label: 'POST', value: 'POST' }, { label: 'GET', value: 'GET' }]" />
          </n-form-item>
          <n-form-item label="响应类型">
            <n-select v-model:value="editing.resp_type" :options="[{ label: 'JSON 内 base64', value: 'b64_json' }, { label: 'JSON 内图片 URL', value: 'url' }, { label: '直接返回图片二进制', value: 'binary' }]" />
          </n-form-item>
          <n-form-item label="提取路径">
            <n-input v-model:value="editing.resp_path" placeholder="如 data.0.b64_json（留空自动探测）" />
          </n-form-item>
          <n-form-item label="高级模板">
            <n-input v-model:value="editing.body_template" type="textarea" :rows="3" placeholder="留空即可——请求体由上方「额外参数」条目拼装。仅在需要完整 JSON 模板控制时填写，填写后优先于条目。" />
          </n-form-item>
        </template>
      </n-form>
      <!-- 连通性测试：用表单当前值真实生图一张并入库，不必先保存 -->
      <n-divider style="margin: 8px 0" />
      <div class="test-area">
        <n-space align="center" :size="8">
          <b>连通性测试</b>
          <span class="hint">用上方当前填写配置真实生图一张（会实际消耗平台额度），成功后自动入库并打「平台测试」标签。</span>
        </n-space>
        <n-space align="center" :size="8" style="margin-top: 8px">
          <n-input v-model:value="testPrompt" size="small" style="flex: 1" placeholder="测试提示词（建议英文标签）" />
          <n-button size="small" type="info" :loading="testing" @click="runTest">测试生图</n-button>
        </n-space>
        <div v-if="testError" class="test-error">{{ testError }}</div>
        <div v-if="testResult" class="test-result">
          <img :src="testResult.data_url" class="test-img" alt="测试图" />
          <div class="test-meta">
            <div>✅ 生成成功，耗时 {{ testResult.cost_sec }}s</div>
            <div>尺寸 {{ testResult.w }} × {{ testResult.h }} · Seed {{ testResult.seed }}</div>
            <div v-if="testResult.archived">已入库（SHA {{ String(testResult.sha).slice(0, 16) }}，标签「平台测试」，可在图库查看）</div>
            <div v-else class="hint">图库未启用，图片未入库</div>
          </div>
        </div>
        <n-space align="center" style="margin-top: 8px">
          <n-button size="tiny" :disabled="!lastDebug" @click="showDebugModal = true">📋 查看请求详情</n-button>
          <span class="hint">完整请求流程（含重试）与响应数据；Authorization 已脱敏。成功/失败均可查看。</span>
        </n-space>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showEdit = false">取消</n-button>
          <n-button type="primary" @click="applyEdit">确定</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 请求详情弹窗 -->
    <n-modal v-model:show="showDebugModal" preset="card" title="请求详情（完整流程与响应，Authorization 已脱敏）" style="width: 780px">
      <pre class="debug-pre">{{ lastDebug ? formatDebug(lastDebug) : "" }}</pre>
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
  return ({ nai: "NAI", openai: "OpenAI 兼容", minimax: "MiniMax", custom: "自定义" } as any)[t] || t || "未知";
}
function typeTag(t: string): any {
  return ({ nai: "success", openai: "info", minimax: "error", custom: "warning" } as any)[t] || "default";
}

const typeOptions = [
  { label: "NAI（NovelAI / 中转站）", value: "nai" },
  { label: "OpenAI 兼容（官方 / newapi / 聚合）", value: "openai" },
  { label: "MiniMax（海螺 image-01）", value: "minimax" },
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
const vtypeOptions = [
  { label: "文本", value: "text" },
  { label: "数字", value: "number" },
  { label: "布尔", value: "bool" },
  { label: "JSON", value: "json" },
];

// ---- 快速预设模板（添加平台时一键预填，参考常见生图平台） ----
const NAI_DEFAULTS = {
  size: "portrait", steps: 28, scale: 6, cfg_rescale: 0.3,
  sampler: "k_dpmpp_2m_sde", noise_schedule: "karras", negative: "",
};
const PLATFORM_PRESETS: Record<string, { label: string; fields: any }> = {
  blank: { label: "空白（手动填写）", fields: null },
  nai_official: {
    label: "NAI 官方（NovelAI 订阅 Token）",
    fields: {
      type: "nai", base_url: "https://image.novelai.net",
      model: "nai-diffusion-4-5-full", via_middle_station: false,
      defaults: { ...NAI_DEFAULTS },
    },
  },
  nai_middle: {
    label: "NAI 中转站（nai.sta1n.cn 等）",
    fields: {
      type: "nai", base_url: "https://nai.sta1n.cn",
      model: "nai-diffusion-4-5-full", via_middle_station: true,
      defaults: { ...NAI_DEFAULTS },
    },
  },
  openai_official: {
    label: "OpenAI 官方（gpt-image-1 / dall-e-3）",
    fields: { type: "openai", base_url: "https://api.openai.com", model: "gpt-image-1", size: "1024x1024", quality: "", negative: "" },
  },
  newapi: {
    label: "newapi / one-api 中转（OpenAI 兼容）",
    fields: { type: "openai", base_url: "", model: "", size: "1024x1024", quality: "", negative: "" },
  },
  siliconflow: {
    label: "硅基流动（SiliconFlow）",
    fields: { type: "openai", base_url: "https://api.siliconflow.cn", model: "Kwai-Kolors/Kolors", size: "1024x1024", quality: "", negative: "" },
  },
  doubao: {
    label: "豆包（火山方舟 Seedream）",
    fields: { type: "openai", base_url: "https://ark.cn-beijing.volces.com", model: "doubao-seedream-3-0-t2i-250415", size: "1024x1024", quality: "", negative: "" },
  },
  together: {
    label: "Together AI（FLUX 系）",
    fields: { type: "openai", base_url: "https://api.together.xyz", model: "black-forest-labs/FLUX.1-schnell-Free", size: "1024x1024", quality: "", negative: "" },
  },
  zhipu: {
    label: "智谱 CogView（cogview-3-flash 免费）",
    fields: { type: "openai", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "cogview-3-flash", size: "1024x1024", quality: "", negative: "" },
  },
  xai: {
    label: "xAI（Grok 生图）",
    fields: { type: "openai", base_url: "https://api.x.ai", model: "grok-2-image", size: "1024x1024", quality: "", negative: "" },
  },
  minimax: {
    label: "MiniMax（海螺 image-01 / image-01-live）",
    fields: {
      type: "minimax", base_url: "https://api.minimaxi.com",
      model: "image-01",
    },
  },
  pollinations: {
    label: "Pollinations（免费，无需 Key）",
    fields: {
      type: "custom", url: "https://image.pollinations.ai/prompt/{{prompt_encoded}}?width={{width}}&height={{height}}&seed={{seed}}&nologo=true",
      method: "GET", resp_type: "binary", resp_path: "", body_template: "",
      model: "flux", extra_params: [],
    },
  },
  agnes: {
    label: "Agnes Image（2.0/2.1/2.5-flash，当前免费）",
    fields: {
      type: "openai", base_url: "https://apihub.agnes-ai.com",
      model: "agnes-image-2.5-flash", size: "1024x1024", quality: "", negative: "",
    },
  },
  sensenova: {
    label: "SenseNova 日日新（U1 Fast，信息图强）",
    fields: {
      type: "openai", base_url: "https://token.sensenova.cn",
      model: "sensenova-u1-fast", size: "2048x2048", quality: "", negative: "",
      extra_params: [
        { key: "watermark", value: "false", vtype: "bool" },
      ],
    },
  },
};
const presetKey = ref("blank");
const presetOptions = Object.entries(PLATFORM_PRESETS).map(([k, v]) => ({ label: v.label, value: k }));

function applyPresetTemplate(key: string) {
  const preset = PLATFORM_PRESETS[key];
  if (!preset || !preset.fields) return; // blank 不动
  const fields = JSON.parse(JSON.stringify(preset.fields));
  // 保留用户已填内容：Key、名称、白名单、自定义头/参数
  const keep = {
    id: editing.id,
    api_key: editing.api_key,
    name: editing.name,
    allowed_users_text: editing.allowed_users_text,
    headers_list: editing.headers_list,
    extra_params: editing.extra_params,
    enabled: editing.enabled,
  };
  const fresh = emptyPlatform(fields.type);
  Object.assign(fresh, fields, keep);
  if (!String(fresh.name || "").trim()) fresh.name = preset.label.split("（")[0];
  Object.assign(editing, fresh);
}

const baseUrlPlaceholder = computed(() => {
  if (editing.type === "nai") return "https://image.novelai.net 或中转站地址";
  if (editing.type === "openai") return "https://api.openai.com（填域名即可，自动补全 /v1/images/generations）";
  return "完整接口地址（含 http(s)://，URL 支持 prompt 等占位符）";
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
    headers_list: [],
    extra_params: [],
  };
  if (type === "nai") {
    base.via_middle_station = false;
    base.defaults = { size: "portrait", steps: 28, scale: 6, cfg_rescale: 0.3, sampler: "k_dpmpp_2m_sde", noise_schedule: "karras", negative: "" };
  }
  if (type === "openai") {
    base.size = "1024x1024";
    base.quality = "";
    base.negative = "";
    base.use_parameters_wrapper = false;
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
  presetKey.value = "blank";
  showEdit.value = true;
}

function openEdit(p: any) {
  const copy = emptyPlatform(p.type || "openai");
  Object.assign(copy, JSON.parse(JSON.stringify(p)));
  if (!copy.defaults) copy.defaults = emptyPlatform("nai").defaults;
  // 自定义请求头：兼容旧 dict（v5.8.0 custom）与新列表 [{key,value}]
  if (Array.isArray(p.headers)) {
    copy.headers_list = p.headers.map((h: any) => ({ key: h.key || "", value: h.value || "" }));
  } else if (p.headers && typeof p.headers === "object") {
    copy.headers_list = Object.entries(p.headers).map(([k, v]) => ({ key: k, value: String(v) }));
  } else {
    copy.headers_list = [];
  }
  copy.extra_params = Array.isArray(p.extra_params)
    ? p.extra_params.map((e: any) => ({ key: e.key || "", value: e.value ?? "", vtype: e.vtype || "text" }))
    : [];
  if (p.type === "custom") {
    // 旧版 JSON 模板字段保留（高级兼容），条目式为默认使用方式
    copy.headers_text = "";
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

function buildPlatformFromEditing(): any {
  // 表单 → 平台条目（保存与测试共用）；校验失败抛错
  if (!editing.name.trim()) throw new Error("请填写显示名");
  if (editing.type !== "custom" && !String(editing.base_url || "").trim()) {
    throw new Error("请填写接口地址");
  }
  if (editing.type !== "custom" && !String(editing.api_key || "").trim()) {
    throw new Error("请填写 API Key / Token");
  }
  // custom 类型允许无 Key（如 Pollinations 免费直链）
  const item = JSON.parse(JSON.stringify(editing));
  if (!item.id) item.id = `p_${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  item.allowed_users = String(item.allowed_users_text || "")
    .split(/[,，;；\s]+/)
    .map((s: string) => s.trim())
    .filter(Boolean);
  delete item.allowed_users_text;
  // 自定义请求头：条目 → dict（后端兼容列表与 dict 两种）
  item.headers = (item.headers_list || [])
    .filter((h: any) => String(h.key || "").trim())
    .reduce((acc: any, h: any) => { acc[String(h.key).trim()] = h.value ?? ""; return acc; }, {} as any);
  delete item.headers_list;
  // 额外参数：剔除空 key 行
  item.extra_params = (item.extra_params || []).filter((e: any) => String(e.key || "").trim());
  if (item.type === "custom") {
    if (!String(item.body_template || "").trim() && !(item.extra_params || []).length) {
      throw new Error("请至少添加一条请求体参数（或改用高级 JSON 模板）");
    }
  }
  return item;
}

async function applyEdit() {
  let item: any;
  try {
    item = buildPlatformFromEditing();
  } catch (e) {
    message.error((e as Error).message);
    return;
  }
  const idx = cfg.platforms.findIndex((p: any) => p.id === item.id);
  if (idx >= 0) cfg.platforms[idx] = item;
  else cfg.platforms.push(item);
  showEdit.value = false;
  markDirty();
  // 确定即自动落盘，避免「加了平台没点顶部保存」导致刷新丢失
  await saveAll();
}

// ---- 连通性测试 ----
const testing = ref(false);
const testPrompt = ref("1girl, solo, simple background, upper body, looking at viewer, masterpiece");
const testResult = ref<any>(null);
const testError = ref("");
const lastDebug = ref<any>(null);
const showDebugModal = ref(false);

function formatDebug(d: any): string {
  try {
    return JSON.stringify(d, null, 2);
  } catch {
    return String(d);
  }
}

async function runTest() {
  if (testing.value) return;
  testResult.value = null;
  testError.value = "";
  lastDebug.value = null;
  let item: any;
  try {
    item = buildPlatformFromEditing();
  } catch (e) {
    testError.value = (e as Error).message;
    return;
  }
  testing.value = true;
  try {
    const d = await apiPost("platforms/test", { platform: item, prompt: testPrompt.value });
    lastDebug.value = (d && d.debug) || null;
    if (d && d.ok) {
      testResult.value = d;
      message.success("测试成功，图片已生成并入库");
    } else {
      testError.value = `测试失败：${(d && d.error) || "未知错误"}`;
    }
  } catch (e) {
    testError.value = `测试失败：${(e as Error)?.message || e}`;
  } finally {
    testing.value = false;
  }
}

async function removePlatform(p: any) {
  cfg.platforms = cfg.platforms.filter((x: any) => x.id !== p.id);
  if (cfg.active_platform === p.id) cfg.active_platform = "comfyui";
  markDirty();
  await saveAll();
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

async function applyPreset() {
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
  // 确定即自动落盘
  await saveAll();
}

async function removePreset(kind: "artist" | "negative", pr: any) {
  if (kind === "artist") cfg.artist_presets = cfg.artist_presets.filter((x: any) => x.id !== pr.id);
  else cfg.negative_presets = cfg.negative_presets.filter((x: any) => x.id !== pr.id);
  markDirty();
  await saveAll();
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
.kv-editor {
  width: 100%;
}
.test-area {
  border-top: 1px dashed rgba(255, 255, 255, 0.12);
  padding-top: 10px;
}
.test-error {
  margin-top: 8px;
  color: #ff8080;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.test-result {
  margin-top: 10px;
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.test-img {
  width: 220px;
  max-width: 45%;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.12);
}
.test-meta {
  font-size: 12px;
  line-height: 1.9;
  opacity: 0.85;
}
.debug-pre {
  margin-top: 6px;
  max-width: 520px;
  max-height: 260px;
  overflow: auto;
  background: rgba(0, 0, 0, 0.35);
  border-radius: 6px;
  padding: 8px;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-all;
}
.kv-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.hint {
  font-size: 12px;
  opacity: 0.65;
}
</style>
