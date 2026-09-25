<template>
  <div class="features-view">
    <div class="view-head">
      <div>
        <h2>更多功能</h2>
        <p>
          插件里「不在出图主链路」的独立功能，可添加多条、各自配置、单独启用/禁用（用法同「工作流」页）。
          目前支持：图片放大（超分）。
        </p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="load">刷新</n-button>
        <n-button type="primary" :loading="saving" @click="openForm(-1)">＋ 添加功能</n-button>
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

      <n-spin :show="loading">
        <div v-if="!entries.length" class="empty">
          还没有添加功能。点右上角「＋ 添加功能」新增一条「图片放大」，绑定放大工作流即可使用
          <code>/图片放大</code>。
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
          <div class="usage-title">指令用法</div>
          <ul>
            <li><code>/图片放大</code> — 用**第一个启用**的功能 + 它的默认倍率（图片跟指令一起发，或引用一条带图的消息）</li>
            <li><code>/图片放大 3x</code> — 指定倍率（也支持 <code>x3</code>、<code>3倍</code>、<code>--倍率 3</code>）</li>
            <li><code>/图片放大 {{ entries[0]?.name || "功能名" }} 2x</code> — 点名功能 + 倍率（也可用绑定的放大工作流名）</li>
          </ul>
          <div class="usage-note">
            倍率不在该功能的「允许倍率」里时自动改用它的默认倍率；停用的功能不参与默认选择、也不能被点名；
            放大结果计入生图限额与图库（与出图同口径）。
          </div>
        </div>
      </n-spin>
    </div>

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
import { computed, onMounted, reactive, ref } from "vue";
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

function openForm(idx: number) {
  formIndex.value = idx;
  if (idx >= 0 && entries.value[idx]) {
    Object.keys(DEFAULTS).forEach((k) => (form[k] = (entries.value[idx] as any)[k] ?? DEFAULTS[k]));
  } else {
    Object.assign(form, DEFAULTS);
  }
  formShow.value = true;
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
.feat-modal {
  width: min(680px, 92vw);
}
</style>
