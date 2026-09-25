<template>
  <div class="features-view">
    <div class="view-head">
      <div>
        <h2>更多功能</h2>
        <p>
          插件里「不在出图主链路」的独立功能，可添加多条、各自配置、单独启用/禁用（用法同「工作流」页）。
          目前支持：图片放大（超分）、抠图（去背景）。
        </p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="load">刷新</n-button>
        <n-button type="primary" @click="openAdd">＋ 添加功能</n-button>
      </div>
    </div>

    <div class="feat-scroll">
      <n-alert v-if="msg" :type="msgType" closable style="margin-bottom: 16px" @close="msg = ''">
        {{ msg }}
      </n-alert>

      <n-alert v-if="legacyHint" type="info" closable style="margin-bottom: 16px" @close="legacyHint = false">
        检测到旧版「图片放大」单条配置（{{ legacyName }}），已折算为下面这条；点「保存」即完成迁移。
      </n-alert>

      <n-alert v-if="!upscaleBases.length" type="warning" style="margin-bottom: 16px" :show-icon="false">
        基础工作流库里还没有「放大类」工作流。请先到
        <router-link to="/baseworkflows">基础工作流</router-link>
        页上传一个纯放大工作流（如 TE-Speed VOSR2：LoadImage → 放大 → SaveImage，
        无采样器、无提示词），解析通过后它的类型会显示为「放大」。
      </n-alert>

      <!-- 尺寸护栏（全局）：防「图太大」与「放大后爆显存」 -->
      <n-card class="feat-card guard-card" size="small">
        <template #header>
          <div class="card-head">
            <span class="feat-icon">🛡️</span>
            <span class="feat-title">尺寸护栏（全局 · 防爆显存）</span>
            <span class="feat-desc">对所有放大功能生效：输入太大直接拦，倍率按输入尺寸自动降档</span>
          </div>
        </template>
        <template #header-extra>
          <n-button size="tiny" type="primary" ghost :loading="savingLimits" :disabled="!limitsDirty" @click="saveLimits">
            保存
          </n-button>
        </template>

        <n-form label-placement="top" size="small">
          <n-form-item label="档位">
            <n-select v-model:value="limits.preset" :options="presetOptions" style="max-width: 340px" />
            <div class="hint">{{ limitMeta("preset") }}</div>
          </n-form-item>
          <n-grid v-if="limits.preset === 'custom'" cols="1 640:2" :x-gap="16">
            <n-form-item-gi label="自定义 · 输入长边上限（px）">
              <n-input-number v-model:value="limits.custom_max_input_side" :min="0" :max="20000" :step="256" style="width: 100%" />
            </n-form-item-gi>
            <n-form-item-gi label="自定义 · 输入总像素上限（MP）">
              <n-input-number v-model:value="limits.custom_max_input_mp" :min="0" :max="500" :step="0.5" style="width: 100%" />
            </n-form-item-gi>
            <n-form-item-gi label="自定义 · 输出长边上限（px）">
              <n-input-number v-model:value="limits.custom_max_output_side" :min="0" :max="20000" :step="256" style="width: 100%" />
            </n-form-item-gi>
            <n-form-item-gi label="自定义 · 输出总像素上限（MP）">
              <n-input-number v-model:value="limits.custom_max_output_mp" :min="0" :max="500" :step="0.5" style="width: 100%" />
            </n-form-item-gi>
          </n-grid>
          <div v-if="limits.preset === 'custom'" class="hint">
            0 = 该项不限制；总像素比长边更能反映扩散式放大（VOSR2 / SeedVR2）的显存占用。
          </div>
        </n-form>

        <table class="guard-table">
          <thead>
            <tr><th>档位</th><th>输入上限（超过直接拒绝）</th><th>输出上限（决定自适应倍率）</th></tr>
          </thead>
          <tbody>
            <tr v-for="p in PRESET_TABLE" :key="p.key" :class="{ cur: limits.preset === p.key }">
              <td>{{ p.label }}</td>
              <td>{{ p.inTxt }}</td>
              <td>{{ p.outTxt }}</td>
            </tr>
          </tbody>
        </table>
        <div class="hint">
          命中「输入上限」会发失败卡说明原因与当前档位；倍率命中「输出上限」会自动降档并在
          处理中卡片 / 结果卡上写明（例如输入 1024×1536 请求 3× → 自动用 2×）。
        </div>
      </n-card>

      <!-- 抠图（去背景）：单条配置，指令 /抠图，不用传参数 -->
      <n-card class="feat-card" size="small" :class="{ off: !matting.enabled }">
        <template #header>
          <div class="card-head">
            <span class="feat-icon">✂️</span>
            <span class="feat-title">抠图（去背景）</span>
            <n-tag size="tiny" :bordered="false" :type="mattingBaseTagType">{{ mattingBaseLabel }}</n-tag>
            <span class="feat-desc">
              用户带图发 <code>/抠图</code> 即可按提示词抠图/去背景（不用传参数）
            </span>
          </div>
        </template>
        <template #header-extra>
          <n-button size="tiny" type="primary" ghost :loading="savingMatting" :disabled="!mattingDirty" @click="saveMatting">
            保存
          </n-button>
        </template>

        <n-form label-placement="top" size="small">
          <n-form-item :label="mMeta('enabled', '启用抠图').label">
            <n-switch v-model:value="matting.enabled" />
            <div class="hint">{{ mMeta("enabled", "").hint }}</div>
          </n-form-item>

          <n-form-item :label="mMeta('base_id', '绑定的抠图工作流').label">
            <n-select
              v-model:value="matting.base_id"
              :options="mattingBaseOptions"
              filterable
              clearable
              placeholder="自动（库里只有一个可用时）"
              @update:value="onMattingBaseChange"
            />
            <div class="hint">{{ mMeta("base_id", "").hint }}</div>
            <div v-if="mattingBase" class="hint ok">
              已绑定：{{ mattingBase.name }}（类型 {{ kindLabel(mattingBase.roles?.kind) }}；模型
              {{ mattingBase.roles?.model_file || "未识别" }}）
            </div>
          </n-form-item>

          <n-grid cols="1 640:2" :x-gap="16">
            <n-form-item-gi :label="mMeta('steps', '采样步数').label">
              <n-input-number
                v-model:value="matting.steps"
                :min="0"
                :max="200"
                :placeholder="mattingBaseSteps != null ? String(mattingBaseSteps) : '工作流默认'"
                style="width: 100%"
              />
              <div class="hint">
                {{ mMeta("steps", "").hint }}
                <template v-if="mattingBaseSteps != null">工作流当前：{{ mattingBaseSteps }} 步。</template>
              </div>
            </n-form-item-gi>
            <n-form-item-gi :label="mMeta('timeout', '等待超时（秒）').label">
              <n-input-number v-model:value="matting.timeout" :min="30" :max="3600" :step="30" style="width: 100%" />
              <div class="hint">{{ mMeta("timeout", "").hint }}</div>
            </n-form-item-gi>
          </n-grid>

          <n-form-item :label="mMeta('prompt', '提示词').label">
            <n-input
              v-model:value="matting.prompt"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 4 }"
              :placeholder="mattingBasePrompt || '留空 = 用工作流里的提示词'"
            />
            <div class="hint">{{ mMeta("prompt", "").hint }}</div>
            <div v-if="mattingBasePrompt" class="hint">
              工作流里的提示词：<code>{{ mattingBasePrompt }}</code>
            </div>
          </n-form-item>

          <n-space :size="8" align="center" style="margin-bottom: 4px">
            <n-button size="tiny" :disabled="!mattingBase" @click="fillFromMattingBase">
              从工作流填充步数与提示词
            </n-button>
            <n-button size="tiny" quaternary @click="clearMattingForm">清空（用工作流原值）</n-button>
          </n-space>
        </n-form>

        <div class="usage">
          <div class="usage-title">指令用法（另有 <code>/抠像</code>、<code>/去背景</code>、<code>/去背</code>）</div>
          <ul>
            <li><code>/抠图</code> — 用绑定的抠图工作流（图片跟指令一起发，或引用一条带图的消息），不用传参数</li>
            <li><code>/抠图 工作流名</code> — 临时指定另一套抠图工作流（可选，写错了会自动回到绑定的那套）</li>
          </ul>
          <div class="usage-note">
            步数与提示词留空即沿用工作流原值（表单里的「工作流里的提示词 / 工作流当前 N 步」就是当前值）；
            抠图结果计入生图限额与图库（与出图同口径），透明通道保留。
          </div>
        </div>
      </n-card>

      <n-spin :show="loading">
        <div v-if="!entries.length" class="empty">
          还没有添加「图片放大」功能。点右上角「＋ 添加功能」新增一条，绑定放大工作流即可使用
          <code>/放大</code>（同 <code>/图片放大</code>）。
        </div>

        <n-card
          v-for="(e, idx) in entries"
          :key="e.__template_key || idx"
          class="feat-card"
          size="small"
          :class="{ off: e.enabled === false }"
        >
          <template #header>
            <div class="card-head">
              <span class="feat-icon">🔍</span>
              <span class="feat-title">{{ e.name || "(未命名)" }}</span>
              <n-tag size="tiny" :bordered="false">图片放大</n-tag>
              <n-tag size="tiny" :bordered="false" :type="baseTagType(e)">
                {{ baseLabel(e) }}
              </n-tag>
              <n-tag v-if="e.enabled === false" size="tiny" type="warning" :bordered="false">已停用</n-tag>
            </div>
          </template>
          <template #header-extra>
            <n-space align="center" :size="8">
              <span class="sw-label">{{ e.enabled === false ? "未启用" : "已启用" }}</span>
              <n-switch :value="e.enabled !== false" size="small" @update:value="(v: boolean) => toggleEnabled(idx, v)" />
            </n-space>
          </template>

          <div class="meta">
            <span class="kv">默认倍率 <b>{{ num(e.default_scale, 3) }}×</b></span>
            <span class="kv">允许倍率 <b>{{ String(e.allowed_scales || "").trim() || "不限" }}</b></span>
            <span class="kv">种子 <b>{{ seedLabel(e) }}</b></span>
            <span class="kv">超时 <b>{{ num(e.timeout, 300) }}s</b></span>
          </div>

          <template #action>
            <n-space :size="6">
              <n-button size="tiny" @click="move(idx, -1)" :disabled="idx === 0">↑</n-button>
              <n-button size="tiny" @click="move(idx, 1)" :disabled="idx === entries.length - 1">↓</n-button>
              <n-button size="tiny" type="primary" ghost @click="openForm(idx)">编辑</n-button>
              <n-button size="tiny" type="error" ghost @click="remove(idx)">删除</n-button>
            </n-space>
          </template>
        </n-card>

        <div class="usage">
          <div class="usage-title">指令用法（<code>/放大</code> 是 <code>/图片放大</code> 的简称，另有 <code>/超分</code>）</div>
          <ul>
            <li><code>/放大</code> — 用**第一个启用**的功能 + 它的默认倍率（图片跟指令一起发，或引用一条带图的消息）</li>
            <li><code>/放大 3x</code> — 指定倍率（也支持 <code>x3</code>、<code>3倍</code>、<code>--倍率 3</code>）</li>
            <li><code>/放大 {{ entries[0]?.name || "功能名" }} 2x</code> — 点名功能 + 倍率（也可用绑定的放大工作流名）</li>
          </ul>
          <div class="usage-note">
            倍率不在该功能的「允许倍率」里时自动改用它的默认倍率；停用的功能不参与默认选择、也不能被点名；
            放大结果计入生图限额与图库（与出图同口径）。
          </div>
        </div>
      </n-spin>
    </div>

    <!-- 添加功能：功能选择面板（目前只有一项，图片放大） -->
    <n-modal v-model:show="pickShow" preset="card" title="添加功能" class="feat-modal pick" :bordered="false">
      <div class="pick-list">
        <div v-for="f in FEATURE_KINDS" :key="f.kind" class="pick-item" @click="pickKind(f.kind)">
          <span class="pick-icon">{{ f.icon }}</span>
          <div class="pick-body">
            <div class="pick-name">{{ f.name }}</div>
            <div class="pick-desc">{{ f.desc }}</div>
          </div>
          <span class="pick-go">选择 →</span>
        </div>
      </div>
      <div class="hint">选定后进入配置弹窗：绑定放大工作流、设置倍率与种子。后续新增的功能类型也会出现在这里。</div>
    </n-modal>

    <!-- 编辑弹窗 -->
    <n-modal v-model:show="formShow" preset="card" class="feat-modal" :bordered="false"
             :title="formIndex < 0 ? '添加功能（图片放大）' : '编辑功能'">
      <n-form label-placement="top" size="small">
        <n-form-item :label="meta('name', '功能名称（引用键）').label">
          <n-input v-model:value="form.name" placeholder="vosr2" />
          <div class="hint">{{ meta('name', "").hint }}</div>
        </n-form-item>

        <n-form-item label="启用">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>

        <n-form-item :label="meta('base_id', '绑定的放大基础工作流').label">
          <n-select v-model:value="form.base_id" :options="baseOptions" filterable clearable placeholder="自动（库里只有一个时）" />
          <div class="hint">{{ meta('base_id', "").hint }}</div>
          <div v-if="formBase" class="hint ok">
            已绑定：{{ formBase.name }}（类型 {{ kindLabel(formBase.roles?.kind) }}；模型
            {{ formBase.roles?.model_file || "未识别" }}）
          </div>
        </n-form-item>

        <n-grid cols="1 640:2" :x-gap="16">
          <n-form-item-gi :label="meta('default_scale', '默认放大倍率').label">
            <n-input-number v-model:value="form.default_scale" :min="1" :max="8" style="width: 100%" />
            <div class="hint">{{ meta('default_scale', "").hint }}</div>
          </n-form-item-gi>
          <n-form-item-gi :label="meta('allowed_scales', '允许的放大倍率').label">
            <n-input v-model:value="form.allowed_scales" placeholder="2,3,4" />
            <div class="hint">
              {{ meta('allowed_scales', "").hint }}
              <template v-if="formAllowed.length">当前允许：{{ formAllowed.join("×、") }}×</template>
              <template v-else>当前为「不校验」。</template>
            </div>
          </n-form-item-gi>
        </n-grid>

        <n-grid cols="1 640:2" :x-gap="16">
          <n-form-item-gi :label="meta('seed_mode', '种子策略').label">
            <n-select v-model:value="form.seed_mode" :options="[
              { label: 'random（每次随机，推荐）', value: 'random' },
              { label: 'fixed（固定种子，结果可复现）', value: 'fixed' },
            ]" />
          </n-form-item-gi>
          <n-form-item-gi :label="meta('seed_value', '固定种子值').label">
            <n-input-number v-model:value="form.seed_value" :disabled="form.seed_mode !== 'fixed'" style="width: 100%" />
          </n-form-item-gi>
        </n-grid>
        <div class="hint">{{ meta('seed_mode', "").hint }}</div>

        <n-form-item :label="meta('timeout', '放大等待超时（秒）').label">
          <n-input-number v-model:value="form.timeout" :min="30" :max="3600" :step="30" style="width: 220px" />
          <div class="hint">{{ meta('timeout', "").hint }}</div>
        </n-form-item>
      </n-form>

      <template #footer>
        <n-space justify="end">
          <n-button @click="formShow = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveForm">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useMessage } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";

const message = useMessage();
const loading = ref(false);
const saving = ref(false);
const msg = ref("");
const msgType = ref<"success" | "error">("success");
const schema = ref<any>(null);
const bases = ref<any[]>([]);
const entries = ref<any[]>([]);
const legacyHint = ref(false);
const legacyName = ref("");

const DEFAULTS: Record<string, any> = {
  kind: "upscale",
  name: "vosr2",
  enabled: true,
  base_id: "",
  default_scale: 3,
  allowed_scales: "2,3,4",
  seed_mode: "random",
  seed_value: 6666,
  timeout: 300,
};

// ---- 尺寸护栏（全局，防爆显存）----
// 对照表数值需与后端 main.py 的 _UPSCALE_LIMIT_PRESETS 保持一致（这里仅用于展示/选择）
const LIMIT_DEFAULTS: Record<string, any> = {
  preset: "8g",
  custom_max_input_side: 3072,
  custom_max_input_mp: 9,
  custom_max_output_side: 4096,
  custom_max_output_mp: 12,
};
const limits = reactive({ ...LIMIT_DEFAULTS });
const limitsDirty = ref(false);
const savingLimits = ref(false);
const PRESET_TABLE = [
  { key: "8g", label: "8G 显存（默认）", inTxt: "长边 ≤2048px、≤4MP", outTxt: "长边 ≤3072px、≤8MP" },
  { key: "12g", label: "12G 显存", inTxt: "长边 ≤3072px、≤9MP", outTxt: "长边 ≤4096px、≤12MP" },
  { key: "16g", label: "16G 显存以上", inTxt: "长边 ≤4096px、≤16MP", outTxt: "长边 ≤6144px、≤24MP" },
  { key: "off", label: "不限制", inTxt: "不限", outTxt: "不限（自行承担爆显存风险）" },
  { key: "custom", label: "自定义", inTxt: "见上方四个数值", outTxt: "见上方四个数值" },
];
const presetOptions = [
  { label: "8G 显存（默认）", value: "8g" },
  { label: "12G 显存", value: "12g" },
  { label: "16G 显存以上", value: "16g" },
  { label: "不限制（自行承担爆显存风险）", value: "off" },
  { label: "自定义", value: "custom" },
];
watch(limits, () => { limitsDirty.value = true; }, { deep: true });

function limitMeta(key: string) {
  return schema.value?.upscale_limits?.items?.[key]?.hint || "";
}

async function saveLimits() {
  savingLimits.value = true;
  msg.value = "";
  try {
    const payload: Record<string, any> = {};
    Object.keys(LIMIT_DEFAULTS).forEach((k) => (payload[k] = (limits as any)[k]));
    await apiPost("config", { config: { upscale_limits: payload } });
    limitsDirty.value = false;
    msgType.value = "success";
    msg.value = `尺寸护栏已保存（${presetOptions.find((p) => p.value === limits.preset)?.label || limits.preset}）`;
    message.success("尺寸护栏已保存");
  } catch (e: any) {
    msgType.value = "error";
    msg.value = e?.message || "保存失败";
    message.error(msg.value);
  } finally {
    savingLimits.value = false;
  }
}

// ---- 抠图（去背景）：单条配置，指令 /抠图 ----
const MATTING_DEFAULTS: Record<string, any> = {
  enabled: false,
  base_id: "",
  steps: 0,
  prompt: "",
  timeout: 300,
};
const matting = reactive({ ...MATTING_DEFAULTS });
const mattingDirty = ref(false);
const savingMatting = ref(false);
watch(matting, () => { mattingDirty.value = true; }, { deep: true });

// 可绑的抠图工作流：解析通过 + **带图片输入**（放大类归「图片放大」管，不在这里出现）
const mattingBases = computed<any[]>(() =>
  (bases.value || []).filter(
    (w: any) => w?.parse_ok && w?.roles?.image_node && w?.roles?.kind !== "upscale"
  )
);
const mattingBaseOptions = computed(() => [
  { label: "自动（库里只有一个可用时用它）", value: "" },
  ...mattingBases.value.map((w: any) => ({
    label: `${w.name || w.id}${w.roles?.model_file ? ` · ${w.roles.model_file}` : ""}`,
    value: String(w.id),
  })),
]);
const mattingBase = computed<any>(() => {
  const k = String(matting.base_id || "").trim();
  if (k) return baseById(k);
  return mattingBases.value.length === 1 ? mattingBases.value[0] : null;
});
// 工作流里的当前值：用于「填充到表单」与占位提示（roles 由后端解析器产出）
const mattingBaseSteps = computed<number | null>(() => {
  const s = mattingBase.value?.roles?.sampler_defaults?.steps;
  return typeof s === "number" ? s : null;
});
const mattingBasePrompt = computed<string>(() =>
  String(mattingBase.value?.roles?.positive?.default_text || "")
);
function fillFromMattingBase() {
  if (!mattingBase.value) return;
  if (mattingBaseSteps.value != null) matting.steps = mattingBaseSteps.value;
  if (mattingBasePrompt.value) matting.prompt = mattingBasePrompt.value;
}
function onMattingBaseChange() {
  // 选定工作流后**自动把工作流里的步数/提示词填进表单**（默认值取工作流的）
  fillFromMattingBase();
}
function clearMattingForm() {
  matting.steps = 0;
  matting.prompt = "";
}
async function saveMatting() {
  savingMatting.value = true;
  msg.value = "";
  try {
    const payload: Record<string, any> = {};
    Object.keys(MATTING_DEFAULTS).forEach((k) => (payload[k] = (matting as any)[k]));
    payload.steps = Number(payload.steps) > 0 ? Number(payload.steps) : 0;
    payload.timeout = Number(payload.timeout) > 0 ? Number(payload.timeout) : 300;
    payload.prompt = String(payload.prompt || "");
    await apiPost("config", { config: { matting: payload } });
    mattingDirty.value = false;
    msgType.value = "success";
    msg.value = "抠图配置已保存";
    message.success("抠图配置已保存");
  } catch (e: any) {
    msgType.value = "error";
    msg.value = e?.message || "保存失败";
    message.error(msg.value);
  } finally {
    savingMatting.value = false;
  }
}
function mMeta(key: string, fallbackLabel: string) {
  const it = schema.value?.matting?.items?.[key] || {};
  return { label: it.description || fallbackLabel, hint: it.hint || "" };
}
const mattingBaseLabel = computed<string>(() => {
  const k = String(matting.base_id || "").trim();
  if (!k) {
    return mattingBases.value.length === 1 ? `自动：${mattingBases.value[0].name}` : "未绑定工作流";
  }
  const w = baseById(k);
  if (!w) return `绑定的工作流已删除（ID ${k}）`;
  if (w.roles?.kind === "upscale") return `${w.name}（放大类，不适用）`;
  if (!w.roles?.image_node) return `${w.name}（没有图片输入）`;
  if (!w.parse_ok) return `${w.name}（解析未通过）`;
  return w.name;
});
const mattingBaseTagType = computed<any>(() => {
  const k = String(matting.base_id || "").trim();
  if (!k) return mattingBases.value.length === 1 ? "info" : "warning";
  const w = baseById(k);
  if (!w || !w.parse_ok || !w.roles?.image_node || w.roles?.kind === "upscale") return "error";
  return "info";
});

// 只有解析通过、类型为「放大」的基础工作流才能绑定
const upscaleBases = computed<any[]>(() =>
  (bases.value || []).filter((w: any) => w?.roles?.kind === "upscale" && w?.parse_ok)
);
const baseOptions = computed(() => [
  { label: "自动（库里只有一个放大工作流时用它）", value: "" },
  ...upscaleBases.value.map((w: any) => ({
    label: `${w.name || w.id}${w.roles?.model_file ? ` · ${w.roles.model_file}` : ""}`,
    value: String(w.id),
  })),
]);

function baseById(id: any) {
  const k = String(id ?? "").trim();
  if (!k) return null;
  return (bases.value || []).find((w: any) => String(w.id) === k) || null;
}
function baseLabel(e: any): string {
  const k = String(e?.base_id ?? "").trim();
  if (!k) {
    return upscaleBases.value.length === 1
      ? `自动：${upscaleBases.value[0].name}`
      : "未绑定放大工作流";
  }
  const w = baseById(k);
  if (!w) return `绑定的工作流已删除（ID ${k}）`;
  if (w.roles?.kind !== "upscale") return `${w.name}（不是放大类）`;
  if (!w.parse_ok) return `${w.name}（解析未通过）`;
  return w.name;
}
function baseTagType(e: any): any {
  const k = String(e?.base_id ?? "").trim();
  if (!k) return upscaleBases.value.length === 1 ? "info" : "warning";
  const w = baseById(k);
  if (!w || w.roles?.kind !== "upscale" || !w.parse_ok) return "error";
  return "info";
}
function kindLabel(kind?: string) {
  if (kind === "img2img") return "图生图";
  if (kind === "t2i") return "文生图";
  if (kind === "upscale") return "放大";
  return "未知";
}
const num = (v: any, dft: number) => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : dft;
};
function seedLabel(e: any): string {
  return String(e?.seed_mode || "random") === "fixed"
    ? `固定 ${num(e.seed_value, 6666)}`
    : "随机";
}

function meta(key: string, fallbackLabel: string) {
  const it = schema.value?.features?.templates?.default?.items?.[key] || {};
  return { label: it.description || fallbackLabel, hint: it.hint || "" };
}

// ---- 表单 ----
const formShow = ref(false);
const formIndex = ref(-1);
const form = reactive<any>({ ...DEFAULTS });
const formBase = computed(() => baseById(form.base_id));
const formAllowed = computed(() =>
  String(form.allowed_scales || "")
    .split(/[,，、;；\s]+/)
    .map((s) => s.trim().replace(/[xX倍]$/, ""))
    .filter((s) => /^\d+$/.test(s) && +s >= 1 && +s <= 8)
    .map((s) => +s)
);

function genTemplateKey(): string {
  try {
    const c: any = (globalThis as any).crypto;
    if (c?.randomUUID) return c.randomUUID().replace(/-/g, "").slice(0, 24);
  } catch { /* 忽略，走下面的兜底 */ }
  return `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

function openForm(idx: number, kind = "upscale") {
  formIndex.value = idx;
  if (idx >= 0 && entries.value[idx]) {
    Object.keys(DEFAULTS).forEach((k) => (form[k] = (entries.value[idx] as any)[k] ?? DEFAULTS[k]));
  } else {
    Object.assign(form, DEFAULTS, { kind });
    // 省事一点：库里只有一个放大工作流时，新条目默认就绑它
    if (!form.base_id && upscaleBases.value.length === 1) form.base_id = String(upscaleBases.value[0].id);
  }
  formShow.value = true;
}

// ---- 添加功能：先选功能类型（目前只有「图片放大」）----
const pickShow = ref(false);
const FEATURE_KINDS = [
  {
    kind: "upscale",
    name: "图片放大（超分）",
    icon: "🔍",
    desc: "用户带图 + /放大 [功能名] [倍率]，把图送进纯放大工作流超分（如 TE-Speed VOSR2）",
  },
];

function openAdd() {
  if (!upscaleBases.value.length) {
    message.warning("基础工作流库里还没有「放大类」工作流，先到「基础工作流」页上传一个（也可以先添加、稍后再绑定）");
  }
  pickShow.value = true;
}

function pickKind(kind: string) {
  pickShow.value = false;
  openForm(-1, kind);
}

async function saveForm() {
  if (!String(form.name || "").trim()) {
    message.warning("功能名称必填（指令里用它点名）");
    return;
  }
  const v: any = { ...form, name: String(form.name).trim() };
  if (formIndex.value >= 0 && entries.value[formIndex.value]) {
    v.__template_key = entries.value[formIndex.value].__template_key || genTemplateKey();
    entries.value[formIndex.value] = { ...entries.value[formIndex.value], ...v };
  } else {
    v.__template_key = genTemplateKey();
    entries.value.push(v);
  }
  formShow.value = false;
  await saveAll("已保存");
}

async function saveAll(okText: string) {
  saving.value = true;
  msg.value = "";
  try {
    await apiPost("config", { config: { features: entries.value } });
    msgType.value = "success";
    msg.value = okText;
    message.success(okText);
    legacyHint.value = false;
  } catch (e: any) {
    msgType.value = "error";
    msg.value = e?.message || "保存失败";
    message.error(msg.value);
  } finally {
    saving.value = false;
  }
}

async function toggleEnabled(idx: number, val: boolean) {
  const e = entries.value[idx];
  if (!e) return;
  const prev = e.enabled !== false;
  e.enabled = val;
  try {
    await apiPost("config", { config: { features: entries.value } });
    message.success(val ? `已启用「${e.name || ""}」` : `已停用「${e.name || ""}」`);
    msgType.value = "success";
    msg.value = val ? `已启用「${e.name || ""}」` : `已停用「${e.name || ""}」`;
  } catch (err: any) {
    e.enabled = prev;
    message.error(err?.message || "保存失败");
  }
}

async function move(idx: number, delta: number) {
  const j = idx + delta;
  if (j < 0 || j >= entries.value.length) return;
  const arr = entries.value.slice();
  [arr[idx], arr[j]] = [arr[j], arr[idx]];
  entries.value = arr;
  await saveAll("顺序已保存");
}

async function remove(idx: number) {
  const e = entries.value[idx] || {};
  const arr = entries.value.slice();
  arr.splice(idx, 1);
  entries.value = arr;
  await saveAll(`已删除「${e.name || ""}」`);
}

async function load() {
  loading.value = true;
  msg.value = "";
  try {
    const [cfg, sch, bw] = await Promise.all([
      apiGet("config"),
      apiGet("schema").catch(() => null),
      apiGet("baseworkflows").catch(() => null),
    ]);
    schema.value = sch || null;
    bases.value = Array.isArray(bw?.items) ? bw.items : [];
    // 尺寸护栏（全局）
    const _lim: any = cfg?.upscale_limits;
    if (_lim && typeof _lim === "object") {
      Object.keys(LIMIT_DEFAULTS).forEach((k) => {
        (limits as any)[k] = _lim[k] ?? LIMIT_DEFAULTS[k];
      });
    }
    limitsDirty.value = false;
    // 抠图（单条配置）
    const _mt: any = cfg?.matting;
    if (_mt && typeof _mt === "object") {
      Object.keys(MATTING_DEFAULTS).forEach((k) => {
        (matting as any)[k] = _mt[k] ?? MATTING_DEFAULTS[k];
      });
    }
    mattingDirty.value = false;
    const list = Array.isArray(cfg?.features) ? cfg.features : [];
    entries.value = list
      .filter((x: any) => x && typeof x === "object")
      .map((x: any) => ({ ...DEFAULTS, ...x }));
    // 旧版单条配置（image_upscale 对象）→ 折算成一条，提示保存一次完成迁移
    const legacy = cfg?.image_upscale;
    legacyHint.value = false;
    if (!entries.value.length && legacy && typeof legacy === "object" && (legacy.workflow || legacy.enabled)) {
      entries.value = [{
        ...DEFAULTS,
        __template_key: genTemplateKey(),
        name: String(legacy.workflow || "默认放大").trim() || "默认放大",
        enabled: !!legacy.enabled,
        default_scale: legacy.default_scale ?? 3,
        allowed_scales: legacy.allowed_scales ?? "2,3,4",
        seed_mode: legacy.seed_mode ?? "random",
        seed_value: legacy.seed_value ?? 6666,
        timeout: legacy.timeout ?? 300,
      }];
      legacyName.value = String(legacy.workflow || "未命名");
      legacyHint.value = true;
    } else if (!entries.value.length) {
      msg.value = "";
    }
    // 给省事一点：库里只有一个放大工作流时，新建条目默认就绑它
  } catch (e: any) {
    msgType.value = "error";
    msg.value = e?.message || "读取配置失败";
    message.error(msg.value);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.features-view {
  height: 100%;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  padding: 0 4px;
}
.view-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.view-head h2 {
  margin: 0 0 4px;
}
.view-head p {
  margin: 0;
  color: var(--text-sub);
  font-size: 13px;
  max-width: 760px;
}
.view-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.feat-scroll {
  flex: 1;
  overflow: auto;
  padding-bottom: 24px;
}
.feat-card {
  margin-bottom: 12px;
}
.feat-card.off {
  opacity: 0.72;
}
.guard-card {
  border: 1px solid var(--border, rgba(128, 128, 128, 0.22));
}
.guard-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
  margin: 4px 0 2px;
}
.guard-table th,
.guard-table td {
  text-align: left;
  padding: 6px 8px;
  border-bottom: 1px solid rgba(128, 128, 128, 0.16);
}
.guard-table th {
  color: var(--text-sub);
  font-weight: 500;
}
.guard-table tr.cur td {
  background: rgba(32, 128, 240, 0.08);
  font-weight: 600;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.feat-icon {
  font-size: 18px;
}
.feat-title {
  font-size: 15px;
  font-weight: 600;
}
.sw-label {
  font-size: 12px;
  color: var(--text-sub);
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  color: var(--text-sub);
  font-size: 12.5px;
}
.meta b {
  color: var(--text-main, inherit);
  font-weight: 600;
}
.empty {
  padding: 28px 12px;
  color: var(--text-sub);
  font-size: 13px;
  text-align: center;
}
.hint {
  width: 100%;
  margin-top: 4px;
  color: var(--text-sub);
  font-size: 12px;
  line-height: 1.6;
}
.hint.ok {
  color: #18a058;
}
.hint code,
.usage code,
.empty code {
  background: rgba(128, 128, 128, 0.14);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 12px;
}
.usage {
  margin-top: 8px;
  padding: 12px;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.08);
}
.usage-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}
.usage ul {
  margin: 0;
  padding-left: 18px;
  line-height: 1.9;
  font-size: 13px;
}
.usage-note {
  margin-top: 8px;
  color: var(--text-sub);
  font-size: 12px;
  line-height: 1.6;
}
/* 功能选择面板：整行可点 */
.pick-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pick-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--border, rgba(128, 128, 128, 0.24));
  border-radius: 10px;
  cursor: pointer;
  transition: border-color 0.15s, background-color 0.15s;
}
.pick-item:hover {
  border-color: var(--primary, #2080f0);
  background: rgba(32, 128, 240, 0.06);
}
.pick-icon {
  font-size: 22px;
}
.pick-body {
  flex: 1;
  min-width: 0;
}
.pick-name {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 2px;
}
.pick-desc {
  color: var(--text-sub);
  font-size: 12px;
  line-height: 1.5;
}
.pick-go {
  color: var(--text-sub);
  font-size: 12px;
  flex-shrink: 0;
}
.pick-item:hover .pick-go {
  color: var(--primary, #2080f0);
}
</style>
