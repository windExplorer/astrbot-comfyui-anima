<template>
  <div class="config-view">
    <div class="view-head">
      <div>
        <h2>插件配置</h2>
        <p>修改配置后点击保存，插件将立即生效（部分改动需重启 ComfyUI 连接）。</p>
      </div>
      <div class="view-actions">
        <n-button :loading="loading" @click="load">刷新</n-button>
        <n-button type="primary" :disabled="!dirty" :loading="saving" @click="save">保存配置</n-button>
      </div>
    </div>

    <!-- 配置查询：按中文名 / 字段名 / 说明实时过滤，自动展开命中分组 -->
    <div class="cfg-search">
      <n-input
        v-model:value="searchQuery"
        placeholder="搜索配置项：如「超时」「回执」「NSFW」「lora」，支持多个词（空格分隔）"
        clearable
        size="medium"
      >
        <template #prefix><span class="search-icon">🔍</span></template>
      </n-input>
      <span v-if="searchQuery" class="search-count">找到 {{ matchedCount }} 项</span>
      <n-button v-if="searchQuery" size="tiny" quaternary @click="searchQuery = ''">清空</n-button>
    </div>

    <div class="cfg-scroll">
      <n-alert v-if="saveMsg" :type="saveMsgType" closable @close="saveMsg = ''" style="margin-bottom:16px">
        {{ saveMsg }}
      </n-alert>

      <n-spin :show="loading" style="min-height:200px">
        <div v-if="!loading && !schema" class="empty">未获取到配置结构（schema 为空）</div>
        <div v-else-if="!loading && searchQuery && matchedCount === 0" class="empty search-empty">
          没有匹配「{{ searchQuery }}」的配置项，换个关键词试试。
        </div>

        <n-collapse v-else v-model:expanded-names="expanded" class="cfg-collapse">
          <n-collapse-item v-for="grp in visibleGroups" :key="grp.name" :name="grp.name">
            <template #header>
              <div class="grp-head">
                <span class="grp-icon">{{ grp.icon }}</span>
                <span class="grp-title">{{ grp.name }}</span>
                <span v-if="searchQuery" class="grp-count">{{ grp.keys.length }} 项</span>
                <span class="grp-desc">{{ grp.description }}</span>
              </div>
            </template>
            <div class="grp-body">
              <ConfigSection
                v-for="key in grp.keys.filter((k: string) => schema && schema[k] && !hiddenKeys.has(k))"
                :key="key"
                :field-key="key"
                :schema="schema![key]"
                :value="config[key]"
                @change="onFieldChange"
                @update-scalar="onUpdateScalar"
              />
            </div>
          </n-collapse-item>
        </n-collapse>
      </n-spin>

      <!-- 翻译调试 -->
      <div class="panel translate-debug">
        <div class="panel-title">
          <h3>翻译调试</h3>
          <span>测试 Anima 翻译模式（danbooru / llm / api）是否连通。</span>
        </div>
        <div class="tran-debug-row">
          <n-select v-model:value="tranMode" :options="tranOptions" size="small" style="width:220px" />
          <n-input v-model:value="tranText" size="small" style="width:320px" placeholder="中文描述" />
          <n-button size="small" :loading="tranLoading" @click="runTranslate">测试翻译</n-button>
        </div>
        <div v-if="tranResult" class="tran-result" :class="tranResult.ok ? 'ok' : 'err'">
          <div v-if="tranResult.ok">
            <b>✓ 成功 · {{ tranResult.elapsed_ms }}ms</b>
            <pre>{{ tranResult.result }}</pre>
          </div>
          <div v-else>
            <b>✗ 失败 · {{ tranResult.elapsed_ms }}ms</b>
            <pre>{{ tranResult.error }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch, h } from "vue";
import { useRoute } from "vue-router";
import { useMessage, NButton } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import ConfigSection from "@/components/ConfigSection.vue";
import { useRefresh } from "@/composables/useRefresh";

const message = useMessage();
const loading = ref(false);
const saving = ref(false);
const dirty = ref(false);
const saveMsg = ref("");
const saveMsgType = ref<"success" | "error">("success");
const schema = ref<any>(null);
const config = reactive<Record<string, any>>({});
const expanded = ref<string[]>([]); // 默认全部收起
const baseConfig: Record<string, any> = {};

// 由「更多功能」页（/features）自己渲染的配置键：本页不重复展示（也不进兜底分区）。
// image_upscale 是 v7.7.1~v7.7.4 的旧键（保留在此仅为不显示，运行时仍按兼容模式读取）。
// 由「更多功能」页管理的配置键，不在本页展示。
// image_upscale / matting 已在 v7.0.0 移除，HIDDEN_KEYS 里不再保留这两个幽灵键。
const HIDDEN_KEYS = ["features", "upscale_limits"];

// 配置分区元数据（v7.15.0 全量重排）。
// ★新增顶层配置键时必须同步加进对应分区的 keys（或新建分区），
//   否则会掉进兜底的「其他」分区（v5.12.9 教训：permissions/recall 曾因此埋在「其他」里）；
//   若该键由「更多功能」页管理，则加进上面的 HIDDEN_KEYS。
//
// 重排原则（v7.15.0）：按「出图前的准备 → 出图过程 → 出图后」的心智顺序，
// 拆散原来塞了 18 项的「出图行为」大组，语义相近的键归到同组，一个键只出现一次。
const GROUP_META = [
  // ── 出图前的准备 ──────────────────────────────────────────────
  { name: "角色卡链路", description: "对接萌萌今日老婆插件：角色主参考图 + 强制图生图工作流 + 优先级", icon: "🎭", keys: ["role_card"] },
  { name: "服务器与工作流", description: "ComfyUI 服务器连接 + 工作流列表（出图的两大基础）", icon: "🖥️", keys: ["comfyui_servers", "workflows"] },
  { name: "LoRA 库", description: "LoRA 模型库：分类、默认启用、关键字匹配与换装过滤", icon: "🧩", keys: ["loras", "lora_keyword_auto", "lora_alias_lookup", "lora_outfit_filter"] },
  { name: "默认工作流选择", description: "用户没指定工作流时，按风格优先级选哪一个", icon: "🧭", keys: ["default_style_priority", "default_workflow", "default_workflow_real", "default_img2img_workflow", "default_img2img_workflow_real", "img2img_fallback", "img2img_from_quote"] },
  { name: "尺寸与比例", description: "尺寸比例预设、全局硬上限与图生图参考图上限", icon: "📐", keys: ["draw_ratio", "size_max_side", "size_max_mp", "img2img_max_refs"] },

  // ── 提示词与角色 ──────────────────────────────────────────────
  { name: "提示词翻译", description: "Anima 中文提示词翻译成英文标签：模式、兜底、标签服务器与 HTTP 接口", icon: "🌐", keys: ["translator_mode", "translate_fallback_mode", "translate_llm_model", "translate_api", "danbooru"] },
  { name: "提示词优化", description: "画质前缀、短描述扩写、外观防漂移与第三方调用时的 LLM 整理", icon: "✨", keys: ["prompt_boost", "third_party_llm_refine", "llm_rewrite_timeout"] },

  // ── 出图过程 ──────────────────────────────────────────────────
  { name: "AI 对话出图", description: "LLM 工具开关、指定模型、防连发与强制出图标记", icon: "🤖", keys: ["enable_llm_tools", "llm_model", "llm_token_stats", "draw_auto", "force_draw"] },
  { name: "超时与队列", description: "各类等待超时、轮询间隔与排队提示（防框架 120 秒中断）", icon: "⏱️", keys: ["draw_timeout", "platform_gen_timeout", "queue_extra_timeout", "draw_wait_hard_cap", "max_draw_timeout", "queue_poll_interval", "return_queue_position", "queue_hint_only_when_queued"] },
  { name: "消息与回执", description: "已读回执、图文消息、webp 转换、小报告与调试日志", icon: "💬", keys: ["draw_ack_enabled", "draw_ack_emoji", "draw_ack_emoji_id", "draw_ack_emoji_type", "image_caption", "convert_webp_to_png", "show_draw_report", "log_workflow_json"] },
  { name: "出图信息卡片", description: "入队 / 完成 / 失败三个时机发送的渐变信息卡", icon: "🎴", keys: ["pre_draw_card"] },

  // ── 权限与数据 ────────────────────────────────────────────────
  { name: "权限与限额", description: "发图白名单、黑名单、生图次数限制、撤回与 NSFW 群白名单", icon: "🔒", keys: ["permissions", "allow_draw_users", "blacklist", "draw_limit", "recall", "nsfw_group_whitelist"] },
  { name: "图片图库", description: "成品图 / 参考图的归档、检索与 NSFW 检测", icon: "🏞️", keys: ["gallery"] },

  // ── 系统与网络 ────────────────────────────────────────────────
  { name: "网络与 WebUI", description: "C 站抓取代理、独立 WebUI 服务与用户级分享站", icon: "🌍", keys: ["http_proxy", "civitai_api_key", "webui_standalone", "share_webui"] },
];

// 需要隐藏的键：由「更多功能」页管理的 + schema 标记 invisible 的内部/兼容键
// （如 _size_presets_v2 内部迁移标记、nsfw_group_whitelist 旧位置兼容项）。
// 统一在此算出，字段渲染与「其他」兜底分区都用它过滤。
const hiddenKeys = computed(() => {
  const s = schema.value || {};
  const invis = Object.keys(s).filter((k) => !!(s[k] as Record<string, unknown>)?.invisible);
  return new Set<string>([...HIDDEN_KEYS, ...invis]);
});

const groups = computed(() => {
  const hidden = hiddenKeys.value;
  const sk = Object.keys(schema.value || {}).filter((k) => !hidden.has(k));
  const gkeys: Record<string, boolean> = {};
  GROUP_META.forEach((g) => g.keys.forEach((k) => (gkeys[k] = true)));
  const list = GROUP_META.filter((g) => g.keys.some((k) => sk.includes(k)));
  const leftover = sk.filter((k) => !gkeys[k] && !hidden.has(k));
  if (leftover.length) {
    list.push({ name: "其他", description: "未分区的配置项", icon: "📦", keys: leftover });
  }
  return list;
});

// ── 配置查询（v7.15.0）──────────────────────────────────────────
const searchQuery = ref("");

// 一个配置项的可搜索文本：字段名 + 中文标题 + 说明 + hint 全文。
// 用 schema 的原文拼，新增键无需改这里。
function keySearchText(key: string): string {
  const s = (schema.value || {})[key] || {};
  return [key, s.label || "", s.description || "", s.hint || ""].join(" ").toLowerCase();
}

// 多词 AND：每个词都要命中（在标题或说明里），与直觉一致。
function keyMatches(key: string, words: string[]): boolean {
  if (!words.length) return true;
  const text = keySearchText(key);
  return words.every((w) => text.includes(w));
}

// n-input 的 clearable 会把值清成 null，统一归一化，避免 null.trim() 崩溃。
const searchText = computed(() => (searchQuery.value || "").trim());

const visibleGroups = computed(() => {
  const q = searchText.value.toLowerCase();
  if (!q) return groups.value;
  const words = q.split(/\s+/).filter(Boolean);
  return groups.value
    .map((g: any) => ({ ...g, keys: g.keys.filter((k: string) => keyMatches(k, words)) }))
    .filter((g: any) => g.keys.length > 0);
});

const matchedCount = computed(() =>
  visibleGroups.value.reduce((n: number, g: any) => n + g.keys.length, 0)
);

// 搜索时自动展开所有命中分组；清空后恢复默认（全部收起）。
// 不用 watch visibleGroups（深链展开也会被覆盖），只跟搜索词走。
watch(searchText, (q) => {
  expanded.value = q ? visibleGroups.value.map((g: any) => g.name) : [];
});

async function load() {
  loading.value = true;
  try {
    const [sch, cfg] = await Promise.all([apiGet("schema"), apiGet("config")]);
    schema.value = sch;
    // 深拷贝进入 reactive config
    const plain = JSON.parse(JSON.stringify(cfg || {}));
    Object.keys(plain).forEach((k) => { (config as any)[k] = plain[k]; });
    Object.keys(plain).forEach((k) => { baseConfig[k] = JSON.parse(JSON.stringify(plain[k])); });
    dirty.value = false;
  } catch (e: any) {
    message.error(e.message || "读取配置失败");
  } finally {
    loading.value = false;
  }
}

function onFieldChange() {
  dirty.value = true;
  saveMsg.value = "";
}

// 标量字段（bool/string/number）更新：由 ConfigSection emit 上来的 (key, value)，
// 直接写入 reactive config[key]（config 是对象可写；若在标量值上写会报错）。
function onUpdateScalar(key: string, value: any) {
  (config as any)[key] = value;
  dirty.value = true;
  saveMsg.value = "";
}

async function save() {
  if (!dirty.value) return;
  saving.value = true;
  saveMsg.value = "";
  try {
    const payload = JSON.parse(JSON.stringify(config));
    await apiPost("config", { config: payload });
    dirty.value = false;
    saveMsgType.value = "success";
    saveMsg.value = "配置已保存";
    // 更新基准
    Object.keys(payload).forEach((k) => { baseConfig[k] = JSON.parse(JSON.stringify(payload[k])); });
    message.success("配置已保存");
  } catch (e: any) {
    saveMsgType.value = "error";
    saveMsg.value = e.message || "保存失败";
    message.error(e.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

// 翻译调试
const tranMode = ref("danbooru");
const tranText = ref("帅气的少年, 水手服少女, 微笑");
const tranLoading = ref(false);
const tranResult = ref<any>(null);
const tranOptions = [
  { label: "danbooru（标签服务器）", value: "danbooru" },
  { label: "llm（大模型翻译）", value: "llm" },
  { label: "api（通用 HTTP 翻译接口）", value: "api" },
];

async function runTranslate() {
  const text = tranText.value.trim();
  if (!text) return;
  tranLoading.value = true;
  tranResult.value = null;
  try {
    tranResult.value = await apiPost("translate/test", { mode: tranMode.value, text });
  } catch (e: any) {
    tranResult.value = { ok: false, error: e.message || "调用失败" };
  } finally {
    tranLoading.value = false;
  }
}

useRefresh(load);
onMounted(load);

// v7.0.19：支持从其它页面深链到指定配置分组（?group=尺寸比例预设），
// 打开后自动展开该分组并滚动到它——工作流「尺寸」页的「编辑预设 ↗」用的就是这个。
// v7.15.0：修一个从 v7.0.19 起就存在的 bug——调用方传的是「尺寸比例预设」，
// 而分组实际叫「尺寸比例预设与上限」，精确匹配永远失败。这里做别名归一，
// 并让深链与分组改名解耦（以后再改名只需维护这张表）。
const GROUP_ALIASES: Record<string, string> = {
  "尺寸比例预设": "尺寸与比例",
  "尺寸比例预设与上限": "尺寸与比例",
  "AI 对话与 LLM": "AI 对话出图",
  "AI 对话自动出图": "AI 对话出图",
  "出图行为": "超时与队列",
  "网络与代理": "网络与 WebUI",
  "HTTP 代理": "网络与 WebUI",
  "权限": "权限与限额",
  "撤回": "权限与限额",
  "图库": "图片图库",
  "WebUI 服务": "网络与 WebUI",
  "LoRA 列表": "LoRA 库",
  "工作流列表": "服务器与工作流",
  "服务器与模型": "服务器与工作流",
  "出图信息卡片": "出图信息卡片",
  "提示词丰富化": "提示词优化",
};
const route = useRoute();
async function applyDeepLink() {
  const raw = String(route.query.group || "").trim();
  if (!raw) return;
  const want = GROUP_ALIASES[raw] || raw;
  if (searchQuery.value) searchQuery.value = "";  // 清搜索，确保目标分组可见
  await nextTick();                               // 等 watch(searchQuery) 把 expanded 重置完
  const idx = visibleGroups.value.findIndex((g: any) => g.name === want);
  if (idx < 0) return;          // 分组不存在（配置项被清空等）→ 不折腾
  expanded.value = Array.from(new Set([...expanded.value, want]));
  await nextTick();
  setTimeout(() => {
    const nodes = document.querySelectorAll(".cfg-collapse .n-collapse-item");
    nodes[idx]?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 120);
}
watch(() => schema.value, () => { if (schema.value) applyDeepLink(); });
watch(() => route.query.group, () => applyDeepLink());
</script>

<style scoped>
.config-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.view-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
  flex: 0 0 auto;
}
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.view-actions { display: flex; gap: 8px; }
.cfg-search {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex: 0 0 auto;
}
.cfg-search :deep(.n-input) { max-width: 720px; }
.search-icon { font-size: 14px; }
.search-count { color: var(--text-sub); font-size: 13px; white-space: nowrap; }
.cfg-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }
.cfg-collapse { background: var(--bg-panel); border-radius: 8px; padding: 8px; }
.grp-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.grp-icon { font-size: 16px; }
.grp-title { font-weight: 600; }
.grp-count {
  color: var(--text-sub);
  font-size: 11px;
  background: rgba(127, 127, 127, 0.16);
  border-radius: 10px;
  padding: 1px 8px;
}
.grp-desc { color: var(--text-sub); font-size: 12px; }
.search-empty { padding: 48px 20px; line-height: 1.7; }
.grp-body { display: flex; flex-direction: column; gap: 12px; }
.panel {
  background: var(--bg-panel);
  border-radius: 8px;
  padding: 16px;
  margin-top: 16px;
  border: 1px solid var(--border-color);
}
.panel-title h3 { margin: 0 0 4px; }
.panel-title span { color: var(--text-sub); font-size: 12px; }
.tran-debug-row { display: flex; gap: 8px; margin-top: 8px; align-items: center; flex-wrap: wrap; }
.tran-result { margin-top: 12px; padding: 10px; border-radius: 6px; font-size: 13px; }
.tran-result.ok { background: rgba(52, 199, 89, 0.12); }
.tran-result.err { background: rgba(255, 69, 58, 0.12); }
.tran-result pre {
  margin: 6px 0 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: ui-monospace, Consolas, monospace;
}
.empty { color: var(--text-sub); padding: 40px; text-align: center; }

@media (max-width: 768px) {
  .view-head { flex-direction: column; align-items: stretch; gap: 10px; }
  .view-actions { flex-wrap: wrap; }
  .view-actions :deep(.n-button) { flex: 1 1 auto; }
  .cfg-search { flex-wrap: wrap; gap: 6px; }
  .cfg-search :deep(.n-input) { max-width: 100%; width: 100%; }
  .cfg-scroll { padding-right: 0; }
  .panel { padding: 12px; margin-top: 12px; }
  .tran-debug-row { flex-direction: column; align-items: stretch; gap: 8px; }
  .tran-debug-row :deep(.n-input),
  .tran-debug-row :deep(.n-select) { width: 100% !important; }
}
</style>
