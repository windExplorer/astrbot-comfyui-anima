<template>
  <div class="features-view">
    <div class="view-head">
      <div>
        <h2>更多功能</h2>
        <p>插件里「不在出图主链路」的独立功能，各自带开关与参数。改完点右侧保存即生效。</p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="load">刷新</n-button>
        <n-button type="primary" :disabled="!dirty" :loading="saving" @click="save">保存配置</n-button>
      </div>
    </div>

    <div class="feat-scroll">
      <n-alert v-if="msg" :type="msgType" closable style="margin-bottom: 16px" @close="msg = ''">
        {{ msg }}
      </n-alert>

      <n-spin :show="loading">
        <n-card class="feat-card" size="small">
          <template #header>
            <div class="card-head">
              <span class="feat-icon">🔍</span>
              <span class="feat-title">图片放大（超分）</span>
              <span class="feat-desc">用户带图 + <code>/图片放大 [放大工作流] [倍率]</code>，把图送进纯放大工作流放大后发回</span>
            </div>
          </template>
          <template #header-extra>
            <n-space align="center" :size="8">
              <span class="sw-label">{{ form.enabled ? "已启用" : "未启用" }}</span>
              <n-switch v-model:value="form.enabled" />
            </n-space>
          </template>

          <n-form label-placement="top" size="small" class="feat-form">
            <n-form-item :label="meta('workflow', '默认放大工作流').label">
              <n-select
                v-model:value="form.workflow"
                :options="workflowOptions"
                :disabled="!form.enabled"
                placeholder="自动"
              />
              <div class="hint">{{ meta('workflow', '').hint }}</div>
              <n-alert
                v-if="!upscaleWorkflows.length"
                type="warning"
                style="margin-top: 8px"
                :show-icon="false"
              >
                基础工作流库里还没有「放大类」工作流。请先到
                <router-link to="/baseworkflows">基础工作流</router-link>
                页上传一个纯放大工作流（如 TE-Speed VOSR2：LoadImage → 放大 → SaveImage，
                没有采样器、没有提示词），解析通过后它的类型会显示为「放大」。
              </n-alert>
              <div v-else class="hint">
                可用放大工作流 {{ upscaleWorkflows.length }} 个：
                {{ upscaleWorkflows.map((w: any) => w.name).join("、") }}
              </div>
            </n-form-item>

            <n-grid cols="1 640:2" :x-gap="16">
              <n-form-item-gi :label="meta('default_scale', '默认放大倍率').label">
                <n-input-number
                  v-model:value="form.default_scale"
                  :min="1"
                  :max="8"
                  :disabled="!form.enabled"
                  style="width: 100%"
                />
                <div class="hint">{{ meta('default_scale', '').hint }}</div>
              </n-form-item-gi>

              <n-form-item-gi :label="meta('allowed_scales', '允许的放大倍率').label">
                <n-input
                  v-model:value="form.allowed_scales"
                  :disabled="!form.enabled"
                  placeholder="2,3,4"
                />
                <div class="hint">
                  {{ meta('allowed_scales', '').hint }}
                  <template v-if="allowedParsed.length">
                    当前允许：{{ allowedParsed.join("×、") }}×
                  </template>
                  <template v-else>当前为「不校验」。</template>
                </div>
              </n-form-item-gi>
            </n-grid>

            <n-grid cols="1 640:2" :x-gap="16">
              <n-form-item-gi :label="meta('seed_mode', '种子策略').label">
                <n-select
                  v-model:value="form.seed_mode"
                  :disabled="!form.enabled"
                  :options="[
                    { label: 'random（每次随机，推荐）', value: 'random' },
                    { label: 'fixed（固定种子，结果可复现）', value: 'fixed' },
                  ]"
                />
              </n-form-item-gi>

              <n-form-item-gi :label="meta('seed_value', '固定种子值').label">
                <n-input-number
                  v-model:value="form.seed_value"
                  :disabled="!form.enabled || form.seed_mode !== 'fixed'"
                  style="width: 100%"
                />
              </n-form-item-gi>
            </n-grid>
            <div class="hint">{{ meta('seed_mode', '').hint }}</div>

            <n-form-item :label="meta('timeout', '放大等待超时（秒）').label">
              <n-input-number
                v-model:value="form.timeout"
                :min="30"
                :max="3600"
                :step="30"
                :disabled="!form.enabled"
                style="width: 220px"
              />
              <div class="hint">{{ meta('timeout', '').hint }}</div>
            </n-form-item>
          </n-form>

          <div class="usage">
            <div class="usage-title">指令用法</div>
            <ul>
              <li><code>/图片放大</code> — 用默认工作流 + 默认倍率（图片跟指令一起发，或引用一条带图的消息）</li>
              <li><code>/图片放大 3x</code> — 指定倍率（也支持 <code>x3</code>、<code>3倍</code>、<code>--倍率 3</code>）</li>
              <li><code>/图片放大 vosr2 2x</code> — 指定放大工作流 + 倍率</li>
            </ul>
            <div class="usage-note">
              倍率不在「允许的倍率」里时自动改用默认倍率；图片同时计入生图限额与图库（与出图同口径）。
            </div>
          </div>
        </n-card>
      </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useMessage } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";

const message = useMessage();
const loading = ref(false);
const saving = ref(false);
const dirty = ref(false);
const msg = ref("");
const msgType = ref<"success" | "error">("success");
const schema = ref<any>(null);
const bases = ref<any[]>([]);

const DEFAULTS: Record<string, any> = {
  enabled: false,
  workflow: "",
  default_scale: 3,
  allowed_scales: "2,3,4",
  seed_mode: "random",
  seed_value: 6666,
  timeout: 300,
};
const form = reactive({ ...DEFAULTS });
let filling = false;

// 放大类基础工作流：kind=upscale 且解析通过（解析没过的不能用）
const upscaleWorkflows = computed<any[]>(() =>
  (bases.value || []).filter((w: any) => w?.roles?.kind === "upscale" && w?.parse_ok)
);
const workflowOptions = computed(() => [
  { label: "自动（库里只有一个放大工作流时用它）", value: "" },
  ...upscaleWorkflows.value.map((w: any) => ({
    label: `${w.name || w.id}${w.roles?.model_file ? ` · ${w.roles.model_file}` : ""}`,
    value: String(w.name || w.id),
  })),
]);
const allowedParsed = computed(() =>
  String(form.allowed_scales || "")
    .split(/[,，、;；\s]+/)
    .map((s) => s.trim().replace(/[xX倍]$/, ""))
    .filter((s) => /^\d+$/.test(s) && +s >= 1 && +s <= 8)
    .map((s) => +s)
);

// 文案以 _conf_schema.json 为准（避免两处漂移）
function meta(key: string, fallbackLabel: string) {
  const it = schema.value?.image_upscale?.items?.[key] || {};
  return { label: it.description || fallbackLabel, hint: it.hint || "" };
}

watch(
  form,
  () => {
    if (!filling) dirty.value = true;
  },
  { deep: true }
);

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
    const cur = (cfg && cfg.image_upscale) || {};
    filling = true;
    Object.keys(DEFAULTS).forEach((k) => {
      (form as any)[k] = cur[k] === undefined || cur[k] === null ? DEFAULTS[k] : cur[k];
    });
    filling = false;
    dirty.value = false;
  } catch (e: any) {
    msgType.value = "error";
    msg.value = e?.message || "读取配置失败";
    message.error(msg.value);
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  msg.value = "";
  try {
    const payload: Record<string, any> = {};
    Object.keys(DEFAULTS).forEach((k) => (payload[k] = (form as any)[k]));
    await apiPost("config", { config: { image_upscale: payload } });
    dirty.value = false;
    msgType.value = "success";
    msg.value = form.enabled
      ? "已保存，图片放大功能已开启。"
      : "已保存（图片放大当前为未启用状态）。";
    message.success("配置已保存");
  } catch (e: any) {
    msgType.value = "error";
    msg.value = e?.message || "保存失败";
    message.error(msg.value);
  } finally {
    saving.value = false;
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
  margin-bottom: 16px;
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
.feat-desc {
  color: var(--text-sub);
  font-size: 12px;
  font-weight: 400;
}
.sw-label {
  font-size: 12px;
  color: var(--text-sub);
}
.feat-form :deep(.n-form-item) {
  margin-bottom: 8px;
}
.hint {
  width: 100%;
  margin-top: 4px;
  color: var(--text-sub);
  font-size: 12px;
  line-height: 1.6;
}
.hint code,
.usage code {
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
</style>
