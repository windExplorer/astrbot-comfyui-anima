<template>
  <div class="rc-page">
    <!-- 顶部：标题 + 操作 -->
    <div class="rc-head">
      <div class="rc-head-title">
        <h2>🎭 角色卡</h2>
        <p>对接「萌萌今日老婆」插件的角色库：消息提到角色名时，自动用主参考图走图生图出图。角色资料请到老婆插件维护。</p>
      </div>
      <div class="rc-head-actions">
        <n-button :loading="loading" @click="load(true)">刷新</n-button>
      </div>
    </div>

    <div class="rc-scroll">
      <n-alert v-if="!installed && !loading" type="warning" style="margin-bottom:14px">
        未读取到角色库：请确认已安装「萌萌今日老婆」插件（v0.71.13+）并已在配置页启用「角色卡链路」。
      </n-alert>

      <!-- 统计 -->
      <div v-if="installed" class="rc-stats">
        <div class="stat-item"><span class="stat-num">{{ total }}</span><span class="stat-label">角色</span></div>
        <div class="stat-item ok"><span class="stat-num">{{ withRef }}</span><span class="stat-label">有主参考图</span></div>
        <div class="stat-item warn"><span class="stat-num">{{ total - withRef }}</span><span class="stat-label">缺主参考图</span></div>
        <div class="stat-item"><span class="stat-num">{{ withAvatar }}</span><span class="stat-label">有头像</span></div>
      </div>

      <!-- 角色卡工作流 -->
      <div v-if="installed" class="rc-panel">
        <div class="rc-panel-head">
          <span class="rc-panel-title">⚙️ 角色卡工作流</span>
          <span class="rc-panel-sub">命中角色时强制使用（高于会话固定工作流），通常是 Qwen Image 2.1 图生图</span>
        </div>
        <div class="rc-wf-row">
          <n-select v-model:value="wfValue" :options="wfOptions" filterable clearable
            placeholder="留空 = 不强制工作流（只用主参考图走当前图生图工作流）" style="max-width:560px" />
          <n-button type="primary" size="small" :loading="savingWf" :disabled="!wfDirty" @click="saveWorkflow">保存</n-button>
        </div>
      </div>

      <n-alert v-if="missing.length" type="info" style="margin-bottom:14px">
        以下 {{ missing.length }} 个角色没有主参考图，不参与角色卡生图（仍可按 LoRA / 文字出图）：{{ missingText }}
      </n-alert>

      <!-- 工具栏：搜索 + 筛选 + 视图切换 -->
      <div class="rc-toolbar">
        <n-input v-model:value="q" placeholder="搜索角色名 / 别名 / 作品 / 标签" clearable style="max-width:340px" />
        <n-select v-model:value="refFilter" :options="refFilterOptions" size="small" style="width:150px" />
        <div class="rc-view-switch">
          <n-button size="small" :type="viewMode === 'grid' ? 'primary' : 'default'" @click="viewMode = 'grid'">卡片</n-button>
          <n-button size="small" :type="viewMode === 'list' ? 'primary' : 'default'" @click="viewMode = 'list'">列表</n-button>
        </div>
        <span class="rc-count">{{ filtered.length }} / {{ total }}</span>
      </div>

      <!-- 卡片视图 -->
      <div v-if="viewMode === 'grid'" class="rc-grid">
        <div v-for="r in filtered" :key="r.char_id" class="rc-card" :class="{ 'no-ref': !r.has_ref }">
          <div class="rc-card-cover">
            <img v-if="r.portrait_thumb" :src="r.portrait_thumb" :alt="r.name" loading="lazy" />
            <div v-else class="rc-cover-ph">{{ (r.name || '?').slice(0, 1) }}</div>
            <img v-if="r.avatar_thumb" class="rc-card-avatar" :src="r.avatar_thumb" :alt="r.name + '头像'" loading="lazy" />
            <span class="rc-card-badge" :class="r.has_ref ? 'ok' : 'warn'">{{ r.has_ref ? '✓ 主参考' : '缺主参考' }}</span>
          </div>
          <div class="rc-card-body">
            <div class="rc-card-name">{{ r.name }}</div>
            <div class="rc-card-series">{{ r.series || '—' }}</div>
            <div class="rc-card-tags">{{ r.tags_en || '无标签' }}</div>
          </div>
        </div>
        <div v-if="!filtered.length" class="rc-empty">没有匹配的角色</div>
      </div>

      <!-- 列表视图（分页，不截断） -->
      <div v-else class="rc-list-wrap">
        <n-data-table :columns="columns" :data="filtered" :bordered="false" size="small"
          :scroll-x="780" :pagination="{ pageSize: 20, showSizePicker: true, pageSizes: [10, 20, 50] }" />
        <div v-if="!filtered.length" class="rc-empty">没有匹配的角色</div>
      </div>
    </div>
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
const refFilter = ref("all");
const viewMode = ref<"grid" | "list">("grid");

const refFilterOptions = [
  { label: "全部角色", value: "all" },
  { label: "有主参考图", value: "yes" },
  { label: "无主参考图", value: "no" },
];

// 工作流配置
const fullCfg = ref<Record<string, any>>({});
const wfValue = ref<string | null>(null);
const wfSaved = ref<string | null>(null);
const wfOptions = ref<Array<{ label: string; value: string }>>([]);
const savingWf = ref(false);
const wfDirty = computed(() => (wfValue.value || "") !== (wfSaved.value || ""));

const withAvatar = computed(() => roles.value.filter((r: any) => !!r.avatar_thumb).length);
const missingText = computed(() => missing.value.slice(0, 15).join("、") + (missing.value.length > 15 ? "…" : ""));

const filtered = computed(() => {
  const s = (q.value || "").trim().toLowerCase();
  return roles.value.filter((r: any) => {
    if (refFilter.value === "yes" && !r.has_ref) return false;
    if (refFilter.value === "no" && r.has_ref) return false;
    if (!s) return true;
    return [r.name, r.char_id, r.series, r.tags_en, ...(r.aliases || [])]
      .join(" ").toLowerCase().includes(s);
  });
});

const columns = [
  {
    title: "头像", key: "avatar", width: 62,
    render: (r: any) => r.avatar_thumb
      ? h("img", { src: r.avatar_thumb, alt: r.name, class: "rc-list-avatar" })
      : h("span", { class: "rc-list-noavatar" }, "—"),
  },
  { title: "角色", key: "name", width: 140, render: (r: any) => h("b", r.name) },
  { title: "别名", key: "aliases", width: 150, render: (r: any) => (r.aliases || []).join(" / ") || "—" },
  { title: "作品", key: "series", width: 130, render: (r: any) => r.series || "—" },
  { title: "标签", key: "tags_en", render: (r: any) => h("span", { class: "rc-tags" }, r.tags_en || "—") },
  {
    title: "主参考图", key: "has_ref", width: 100,
    render: (r: any) => r.has_ref
      ? h(NTag, { type: "success", size: "small", bordered: false }, { default: () => "已绑定" })
      : h(NTag, { type: "warning", size: "small", bordered: false }, { default: () => "缺失" }),
  },
];

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
    const wfs = (ov?.workflows || []).filter((w: any) => w.enabled !== false);
    wfs.sort((a: any, b: any) => {
      const sa = (a.kind === "img2img" || a.multi_image) ? 0 : 1;
      const sb = (b.kind === "img2img" || b.multi_image) ? 0 : 1;
      return sa - sb;
    });
    wfOptions.value = [
      { label: "（不强制，留空）", value: "" },
      ...wfs.map((w: any) => ({
        label: `${w.name}${w.kind === "img2img" || w.multi_image ? " · 图生图" : ""}${w.base_name ? `（${w.base_name}）` : ""}`,
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
.rc-page { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.rc-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 12px; flex: 0 0 auto; }
.rc-head h2 { margin: 0 0 4px; font-size: 20px; }
.rc-head p { margin: 0; color: var(--text-sub); font-size: 12px; line-height: 1.5; }
.rc-head-actions { display: flex; gap: 8px; flex: 0 0 auto; }
.rc-scroll { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding-right: 6px; }

.rc-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
.stat-item {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  padding: 12px 8px; border-radius: 10px;
  background: var(--bg-panel); border: 1px solid var(--border-color);
}
.stat-num { font-size: 22px; font-weight: 700; line-height: 1.1; }
.stat-label { font-size: 12px; color: var(--text-sub); }
.stat-item.ok .stat-num { color: #18a058; }
.stat-item.warn .stat-num { color: #d03050; }

.rc-panel { background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
.rc-panel-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.rc-panel-title { font-weight: 600; font-size: 14px; }
.rc-panel-sub { color: var(--text-sub); font-size: 12px; }
.rc-wf-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

.rc-toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
.rc-view-switch { display: flex; gap: 4px; }
.rc-count { margin-left: auto; color: var(--text-sub); font-size: 13px; }

.rc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; }
.rc-card {
  border: 1px solid var(--border-color); border-radius: 12px; overflow: hidden;
  background: var(--bg-panel); display: flex; flex-direction: column;
  transition: transform .15s, box-shadow .15s;
}
.rc-card:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(0,0,0,.18); }
.rc-card.no-ref { opacity: .8; }
.rc-card-cover { position: relative; aspect-ratio: 3/4; background: var(--bg-body); overflow: hidden; }
.rc-card-cover > img:first-child { width: 100%; height: 100%; object-fit: cover; display: block; }
.rc-cover-ph {
  width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
  font-size: 54px; font-weight: 700; color: var(--text-sub); opacity: .5;
}
.rc-card-avatar {
  position: absolute; left: 8px; bottom: 8px; width: 44px; height: 44px;
  border-radius: 50%; object-fit: cover; border: 2px solid #fff;
  box-shadow: 0 2px 8px rgba(0,0,0,.35);
}
.rc-card-badge {
  position: absolute; right: 6px; top: 6px; font-size: 11px; padding: 2px 7px;
  border-radius: 10px; color: #fff; white-space: nowrap;
}
.rc-card-badge.ok { background: rgba(24,160,88,.9); }
.rc-card-badge.warn { background: rgba(208,48,80,.9); }
.rc-card-body { padding: 8px 10px 10px; }
.rc-card-name { font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rc-card-series { color: var(--text-sub); font-size: 11px; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rc-card-tags {
  color: var(--text-sub); font-size: 11px; margin-top: 6px; line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.rc-list-wrap { background: var(--bg-panel); border-radius: 10px; padding: 6px; }
.rc-list-avatar { width: 36px; height: 36px; border-radius: 50%; object-fit: cover; }
.rc-list-noavatar { color: var(--text-sub); }
.rc-tags { color: var(--text-sub); font-size: 12px; word-break: break-all; }
.rc-empty { color: var(--text-sub); text-align: center; padding: 48px 12px; }

@media (max-width: 768px) {
  .rc-head { flex-direction: column; }
  .rc-stats { grid-template-columns: repeat(2, 1fr); }
  .rc-grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
  .rc-count { margin-left: 0; }
}
</style>
