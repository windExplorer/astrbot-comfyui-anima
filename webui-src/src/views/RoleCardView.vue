<template>
  <div class="wr-view">
    <div class="view-head">
      <div>
        <h2>角色卡</h2>
        <p>对接「萌萌今日老婆」插件的角色库：用户消息提到角色名时，自动用该角色的主参考图走图生图工作流出图。这里只读展示，角色资料请到老婆插件维护。</p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="load(true)">刷新</n-button>
      </div>
    </div>

    <n-spin :show="loading" style="min-height:200px">
      <n-alert v-if="!installed && !loading" type="warning" style="margin-bottom:14px">
        未读取到老婆插件角色库：请确认已安装「萌萌今日老婆」插件（v0.71.12+）并已在配置页启用「角色卡链路」。
      </n-alert>

      <div v-if="installed" class="wr-cards">
        <n-card size="small"><div class="num">{{ total }}</div><div class="label">角色总数</div></n-card>
        <n-card size="small"><div class="num ok">{{ withRef }}</div><div class="label">有主参考图</div></n-card>
        <n-card size="small"><div class="num warn">{{ total - withRef }}</div><div class="label">缺主参考图</div></n-card>
      </div>

      <!-- 角色卡工作流：命中角色时强制使用的图生图工作流 -->
      <div class="panel wf-panel">
        <div class="panel-title">
          <h3>角色卡工作流</h3>
          <span>命中角色时强制使用（优先级高于会话固定工作流），通常是 Qwen Image 2.1 图生图</span>
        </div>
        <div class="wf-row">
          <n-select
            v-model:value="wfValue"
            :options="wfOptions"
            filterable
            clearable
            placeholder="留空 = 不强制工作流（只用主参考图走当前图生图工作流）"
            style="max-width:520px"
          />
          <n-button type="primary" size="small" :loading="savingWf" :disabled="!wfDirty" @click="saveWorkflow">保存</n-button>
        </div>
        <div class="form-hint">也可在「配置 → 角色卡链路」里设置；两处同步。</div>
      </div>

      <n-alert v-if="missing.length" type="info" style="margin-bottom:14px">
        以下 {{ missing.length }} 个角色没有主参考图，不会参与角色卡图生图链路（仍可按 LoRA / 文字正常出图）：{{ missingText }}
      </n-alert>

      <div class="wr-toolbar">
        <n-input v-model:value="q" placeholder="搜索角色名 / 别名 / 作品 / 标签" clearable style="max-width:420px" />
        <span v-if="q" class="count">找到 {{ filtered.length }} 个</span>
      </div>

      <n-data-table
        v-if="filtered.length"
        :columns="columns"
        :data="filtered"
        :bordered="false"
        size="small"
        :scroll-x="720"
        :pagination="{ pageSize: 20 }"
      />
      <div v-else-if="installed" class="empty">没有匹配的角色</div>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { NTag, useMessage } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";

const message = useMessage();
const loading = ref(false);
const installed = ref(false);
const roles = ref<any[]>([]);
const missing = ref<string[]>([]);
const total = ref(0);
const withRef = ref(0);
const q = ref("");

// 角色卡工作流（命中角色时强制使用）
const fullCfg = ref<Record<string, any>>({});
const wfValue = ref<string | null>(null);
const wfSaved = ref<string | null>(null);
const wfOptions = ref<Array<{ label: string; value: string }>>([]);
const savingWf = ref(false);
const wfDirty = computed(() => (wfValue.value || "") !== (wfSaved.value || ""));

const columns = [
  { title: "角色", key: "name", width: 150, render: (r: any) => h("b", r.name) },
  { title: "别名", key: "aliases", width: 160, render: (r: any) => (r.aliases || []).join(" / ") || "—" },
  { title: "作品", key: "series", width: 140, render: (r: any) => r.series || "—" },
  { title: "标签", key: "tags_en", render: (r: any) => h("span", { class: "wr-tags" }, r.tags_en || "—") },
  {
    title: "主参考图", key: "has_ref", width: 110,
    render: (r: any) => r.has_ref
      ? h(NTag, { type: "success", size: "small", bordered: false }, { default: () => "已绑定" })
      : h(NTag, { type: "warning", size: "small", bordered: false }, { default: () => "缺失" }),
  },
];

const filtered = computed(() => {
  const s = (q.value || "").trim().toLowerCase();
  if (!s) return roles.value;
  return roles.value.filter((r: any) =>
    [r.name, r.char_id, r.series, r.tags_en, ...(r.aliases || [])]
      .join(" ").toLowerCase().includes(s)
  );
});

const missingText = computed(() => missing.value.slice(0, 20).join("、") + (missing.value.length > 20 ? "…" : ""));

async function load(force = false) {
  loading.value = true;
  try {
    const [res, cfg, ov] = await Promise.all([
      apiGet("role-card/roles", force ? { refresh: "1" } : undefined),
      apiGet("config"),
      apiGet("studio/overview", undefined, { timeout: 30000 }).catch(() => null),
    ]);
    fullCfg.value = cfg || {};
    installed.value = !!res.installed;
    roles.value = res.roles || [];
    missing.value = res.missing || [];
    total.value = res.total || 0;
    withRef.value = res.with_ref || 0;
    // 工作流选项：图生图 / 多参考图的排前面（角色卡链路要用图生图）
    const wfs = (ov?.workflows || []).filter((w: any) => w.enabled !== false);
    wfs.sort((a: any, b: any) => {
      const sa = (a.kind === "img2img" || a.multi_image) ? 0 : 1;
      const sb = (b.kind === "img2img" || b.multi_image) ? 0 : 1;
      return sa - sb;
    });
    wfOptions.value = [
      { label: "（不强制，留空）", value: "" },
      ...wfs.map((w: any) => ({
        label: `${w.name}${w.kind === "img2img" || w.multi_image ? "  ·  图生图" : ""}${w.base_name ? `（${w.base_name}）` : ""}`,
        value: String(w.name || ""),
      })),
    ];
    const cur = String((cfg?.role_card || {}).workflow || "");
    wfValue.value = cur || null;
    wfSaved.value = cur || null;
  } finally {
    loading.value = false;
  }
}

async function saveWorkflow() {
  if (!wfDirty.value) return;
  savingWf.value = true;
  try {
    const next = String(wfValue.value || "");
    // 后端 save_config 是浅覆盖：必须提交完整 role_card 块，否则其它字段被默认值冲掉
    const rc = { ...(fullCfg.value.role_card || {}), workflow: next };
    await apiPost("config", { config: { ...fullCfg.value, role_card: rc } });
    wfSaved.value = next || null;
    message.success(next ? `角色卡工作流已设为「${next}」` : "已清空角色卡工作流");
  } catch (e: any) {
    message.error(e?.message || "保存失败");
  } finally {
    savingWf.value = false;
  }
}

onMounted(() => load());
</script>

<style scoped>
.wr-view { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; gap: 12px; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; line-height: 1.6; }
.view-actions { display: flex; gap: 8px; flex: 0 0 auto; }
.wr-cards { display: flex; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.wr-cards .n-card { min-width: 130px; }
.num { font-size: 22px; font-weight: 600; }
.num.ok { color: #18a058; }
.num.warn { color: #d03050; }
.label { color: var(--text-sub); font-size: 12px; margin-top: 2px; }
.wr-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.count { color: var(--text-sub); font-size: 13px; }
.wr-tags { color: var(--text-sub); font-size: 12px; word-break: break-all; }
.wf-panel { margin-bottom: 14px; }
.wf-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.form-hint { color: var(--text-sub); font-size: 12px; margin-top: 8px; }
.empty { color: var(--text-sub); padding: 40px; text-align: center; }
@media (max-width: 768px) {
  .view-head { flex-direction: column; }
  .view-actions { width: 100%; }
}
</style>
