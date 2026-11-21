<template>
  <div class="wr-view">
    <div class="view-head">
      <div>
        <h2>老婆角色</h2>
        <p>对接「萌萌今日老婆」插件的角色库：用户消息提到角色名时，自动用该角色的主参考图走图生图工作流出图。这里只读展示，角色资料请到老婆插件维护。</p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="load(true)">刷新</n-button>
      </div>
    </div>

    <n-spin :show="loading" style="min-height:200px">
      <n-alert v-if="!installed && !loading" type="warning" style="margin-bottom:14px">
        未读取到老婆插件角色库：请确认已安装「萌萌今日老婆」插件（v0.71.12+）并已在配置页启用「老婆角色链路」。
      </n-alert>

      <div v-if="installed" class="wr-cards">
        <n-card size="small"><div class="num">{{ total }}</div><div class="label">角色总数</div></n-card>
        <n-card size="small"><div class="num ok">{{ withRef }}</div><div class="label">有主参考图</div></n-card>
        <n-card size="small"><div class="num warn">{{ total - withRef }}</div><div class="label">缺主参考图</div></n-card>
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
import { NTag } from "naive-ui";
import { apiGet } from "@/api/bridge";

const loading = ref(false);
const installed = ref(false);
const roles = ref<any[]>([]);
const missing = ref<string[]>([]);
const total = ref(0);
const withRef = ref(0);
const q = ref("");

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
    const res = await apiGet("waifu/roles", force ? { refresh: "1" } : undefined);
    installed.value = !!res.installed;
    roles.value = res.roles || [];
    missing.value = res.missing || [];
    total.value = res.total || 0;
    withRef.value = res.with_ref || 0;
  } finally {
    loading.value = false;
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
.empty { color: var(--text-sub); padding: 40px; text-align: center; }
@media (max-width: 768px) {
  .view-head { flex-direction: column; }
  .view-actions { width: 100%; }
}
</style>
