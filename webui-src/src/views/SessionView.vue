<template>
  <div class="session-view">
    <div class="view-head">
      <div>
        <h2>会话管理</h2>
        <p>按会话（群聊 / 私聊）管理固定工作流、限额、NSFW、信息卡片与启停。所有覆盖项默认为「跟随全局」。</p>
      </div>
      <div class="head-actions">
        <n-button :loading="refreshingAvatars" @click="refreshAvatars">刷新头像</n-button>
        <n-button :loading="loading" @click="load">刷新</n-button>
        <n-button type="primary" :loading="syncing" @click="syncContacts">同步联系人</n-button>
      </div>
    </div>

    <div class="summary-row">
      <div class="sum-card"><div class="sum-label">全部会话</div><div class="sum-value">{{ counts.all }}</div></div>
      <div class="sum-card"><div class="sum-label">私聊（好友）</div><div class="sum-value">{{ counts.private }}</div></div>
      <div class="sum-card"><div class="sum-label">群聊</div><div class="sum-value">{{ counts.group }}</div></div>
      <div class="sum-card"><div class="sum-label">非好友私聊</div><div class="sum-value">{{ counts.stranger }}</div></div>
      <div class="sum-card"><div class="sum-label">插件来源</div><div class="sum-value">{{ counts.plugin }}</div></div>
      <div class="sum-card"><div class="sum-label">已配置覆盖</div><div class="sum-value">{{ counts.configured }}</div></div>
      <div class="sum-card"><div class="sum-label">已禁用</div><div class="sum-value">{{ counts.disabled }}</div></div>
    </div>

    <div class="panel">
      <div class="contacts-line">
        <span v-if="contacts.synced_at">
          联系人快照：{{ contacts.groups }} 个群 · {{ contacts.friends }} 位好友 · 同步于 {{ contacts.synced_at }}
        </span>
        <span v-else>尚未同步联系人，点「同步联系人」从机器人拉取全部群和好友（需 OneBot 在线）。</span>
        <span v-if="contacts.error" class="contacts-error">{{ contacts.error }}</span>
      </div>

      <div class="toolbar">
        <div class="tabs">
          <button class="tab" :class="{ active: tab === 'all' }" @click="tab = 'all'">
            全部 <span class="tab-count">{{ counts.all }}</span>
          </button>
          <button class="tab" :class="{ active: tab === 'private' }" @click="tab = 'private'">
            私聊 <span class="tab-count">{{ counts.private }}</span>
          </button>
          <button class="tab" :class="{ active: tab === 'group' }" @click="tab = 'group'">
            群聊 <span class="tab-count">{{ counts.group }}</span>
          </button>
          <button class="tab" :class="{ active: tab === 'stranger' }" @click="tab = 'stranger'">
            非好友 <span class="tab-count">{{ counts.stranger }}</span>
          </button>
          <button class="tab" :class="{ active: tab === 'plugin' }" @click="tab = 'plugin'">
            插件 <span class="tab-count">{{ counts.plugin }}</span>
          </button>
        </div>
        <div class="toolbar-right">
          <n-checkbox v-model:checked="onlyConfigured" size="small">只看已配置</n-checkbox>
          <n-input v-model:value="keyword" size="small" placeholder="搜索群名 / 昵称 / 会话…" clearable style="width:220px" />
        </div>
      </div>

      <div v-if="loading" class="loading-line">正在加载会话…</div>
      <div v-else-if="!filtered.length" class="empty-line">
        {{ contacts.synced_at || sessions.length ? '没有匹配的会话' : '点「同步联系人」拉取机器人的全部群和好友，或先让机器人出一次图。' }}
      </div>
      <div v-else class="session-list">
        <template v-for="grp in grouped" :key="grp.key">
        <div v-if="grp.label" class="group-heading">
          <span class="group-heading-bar" :class="grp.key"></span>
          <strong>{{ grp.label }}</strong>
          <span class="group-heading-count">{{ grp.items.length }}</span>
        </div>
        <div v-for="s in grp.items" :key="s.origin" class="session-item" :class="{ disabled: s.enabled === 0 }">
          <div class="avatar" :class="{ group: s.is_group, plugin: s.is_plugin }">
            <img v-if="avatarSrc(s)" :src="avatarSrc(s)" :alt="displayName(s)" />
            <span v-else>{{ s.is_plugin ? '插' : initial(s) }}</span>
          </div>

          <div class="session-main">
            <div class="line1">
              <strong class="name">{{ displayName(s) }}</strong>
              <n-tag size="small" :type="s.is_plugin ? 'warning' : (s.is_group ? 'info' : 'success')" round>
                {{ s.is_plugin ? '插件' : (s.is_group ? '群聊' : '私聊') }}
              </n-tag>
              <n-tag v-if="isStranger(s)" size="small" type="warning" round>非好友</n-tag>
              <n-tag v-if="s.enabled === 0" size="small" type="error" round>已禁用</n-tag>
              <n-tag v-if="s.has_override" size="small" round>已配置</n-tag>
              <span class="sub-id">{{ subName(s) }}</span>
            </div>
            <div class="line2">
              <span class="wf-chip" :class="{ pinned: s.wf_now_t2i.pinned }">
                文生图 · 上次：{{ s.wf_now_t2i.last_used || '—' }}
                <em v-if="s.wf_now_t2i.pinned">固定</em>
              </span>
              <span class="wf-chip" :class="{ pinned: s.wf_now_i2i.pinned }">
                图生图 · 上次：{{ s.wf_now_i2i.last_used || '—' }}
                <em v-if="s.wf_now_i2i.pinned">固定</em>
              </span>
              <span class="wf-next">
                后续：文生图「{{ s.wf_now_t2i.name || '—' }}」{{ s.wf_now_t2i.source }} ·
                图生图「{{ s.wf_now_i2i.name || '—' }}」{{ s.wf_now_i2i.source }}
              </span>
            </div>
            <div class="line3">
              <span>出图 {{ s.draw_total }}</span>
              <span>今日 {{ s.draw_today }}</span>
              <span>会话额度用量 {{ s.usage.total_used }} / {{ s.usage.hour_used }}时 / {{ s.usage.day_used }}天</span>
              <span v-if="s.note" class="note">备注：{{ s.note }}</span>
            </div>
          </div>

          <div class="session-actions">
            <div class="active-time">{{ activeText(s.last_active || s.last_draw) }}</div>
            <n-switch v-if="!s.is_plugin" :value="s.enabled !== 0" size="small"
                      @update:value="(v: boolean) => toggleEnabled(s, v)" />
            <n-button v-if="!s.is_plugin" size="tiny" @click="openEdit(s)">配置</n-button>
            <n-button v-else size="tiny" @click="openEdit(s)">查看</n-button>
          </div>
        </div>
        </template>
      </div>
    </div>

    <n-modal v-model:show="editing" preset="card" :title="modalTitle" style="width: min(720px, 94vw)" :bordered="false">
      <div v-if="draft" class="edit-form">
        <div class="form-hint">会话标识：{{ draft.origin }}</div>

        <div class="form-section">
          <div class="section-title">基本信息</div>
          <div class="field-row">
            <div class="field-label">备注</div>
            <n-input v-model:value="draft.note" size="small" placeholder="给这个会话写点说明（仅控制台可见）" :disabled="draft.is_plugin" />
          </div>
        </div>

        <div v-if="!draft.is_plugin" class="form-section">
          <div class="section-title">工作流</div>
          <div class="form-tip" style="margin-top:-4px">
            选「跟随全局 / 留空」即让本会话跟随全局默认、并允许 AI 对话动态切换；选具体工作流即手动指定后续使用，
            AI 对话仍可动态改写（除非打开下方的「固定」）。
          </div>
          <div class="wf-grid">
            <div class="wf-box">
              <div class="wf-head">文生图 · 后续使用</div>
              <n-select v-model:value="draft.wf_t2i" size="small" :options="wfOptions" clearable placeholder="跟随全局 / 对话设置" />
              <div class="wf-fix">
                <span>固定（固定后对话无法更改）</span>
                <n-switch v-model:value="draft.pin_t2i" size="small" />
              </div>
              <div class="wf-last">上次实际使用：{{ draft.wf_now_t2i.last_used || '—' }}</div>
            </div>
            <div class="wf-box">
              <div class="wf-head">图生图 · 后续使用</div>
              <n-select v-model:value="draft.wf_i2i" size="small" :options="wfOptions" clearable placeholder="跟随全局" />
              <div class="wf-fix">
                <span>固定（固定后对话无法更改）</span>
                <n-switch v-model:value="draft.pin_i2i" size="small" />
              </div>
              <div class="wf-last">上次实际使用：{{ draft.wf_now_i2i.last_used || '—' }}</div>
            </div>
          </div>
          <div class="form-tip">后续将用：文生图「{{ draft.wf_now_t2i.name || '—' }}」（{{ draft.wf_now_t2i.source }}） · 图生图「{{ draft.wf_now_i2i.name || '—' }}」（{{ draft.wf_now_i2i.source }}）</div>
        </div>

        <div v-if="!draft.is_plugin" class="form-section">
          <div class="section-title">会话覆盖（留空 = 跟随全局）</div>
          <div class="quota-grid">
            <div class="quota-field"><span>总次数上限</span><n-input-number v-model:value="draft.max_total" size="small" placeholder="跟随全局" :min="-1" style="width:120px" /></div>
            <div class="quota-field"><span>每小时上限</span><n-input-number v-model:value="draft.max_hour" size="small" placeholder="跟随全局" :min="-1" style="width:120px" /></div>
            <div class="quota-field"><span>每天上限</span><n-input-number v-model:value="draft.max_day" size="small" placeholder="跟随全局" :min="-1" style="width:120px" /></div>
          </div>
          <div class="form-tip">-1 = 不限；留空 = 跟随全局。会话限额与用户限额并存，分别计数。</div>
          <div class="opt-grid">
            <div class="opt-field">
              <span>允许 NSFW</span>
              <n-select v-model:value="draft.nsfw" size="small" :options="triOptions" style="width:140px" />
            </div>
            <div class="opt-field">
              <span>发送信息卡片</span>
              <n-select v-model:value="draft.card" size="small" :options="triOptions" style="width:140px" />
            </div>
            <div class="opt-field">
              <span>会话状态</span>
              <n-select v-model:value="draft.enabled" size="small" :options="enabledOptions" style="width:140px" />
            </div>
          </div>
        </div>

        <div v-else class="form-section">
          <div class="section-title">插件来源统计（只读）</div>
          <div class="form-tip">最近使用工作流：{{ draft.recent_workflow || '—' }} · 累计出图 {{ draft.draw_total }} · 今日 {{ draft.draw_today }}</div>
          <div class="form-tip">插件来源由出图归属「plugin:来源」自动识别，不提供固定工作流与限额设置。</div>
        </div>

        <div class="modal-footer">
          <n-button v-if="!draft.is_plugin" size="small" @click="resetConfig">恢复跟随全局</n-button>
          <n-button v-if="!draft.is_plugin" size="small" @click="resetUsage">清零会话用量</n-button>
          <span class="grow"></span>
          <n-button size="small" @click="editing = false">取消</n-button>
          <n-button size="small" type="primary" :loading="saving" @click="saveEdit">保存</n-button>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { apiGet, apiPost } from "@/api/bridge";
import { useMessage, useDialog } from "naive-ui";

interface WfNow { name: string; pinned: boolean; source: string; last_used: string }
interface SessionItem {
  origin: string;
  name: string;
  is_group: number;
  is_plugin: boolean;
  plugin_name: string;
  in_contacts: boolean;
  group_id: string;
  user_id: string;
  avatar_id: string;
  avatar_kind: "group" | "user";
  note: string;
  wf_t2i: string;
  wf_i2i: string;
  pin_t2i: number;
  pin_i2i: number;
  max_total: number | null;
  max_hour: number | null;
  max_day: number | null;
  nsfw: number | null;
  card: number | null;
  enabled: number | null;
  draw_total: number;
  draw_today: number;
  last_active: number;
  last_draw: number;
  recent_workflow: string;
  usage: { total_used: number; hour_used: number; day_used: number };
  wf_now_t2i: WfNow;
  wf_now_i2i: WfNow;
  has_override: boolean;
}

const message = useMessage();
const dialog = useDialog();
const sessions = ref<SessionItem[]>([]);
const workflows = ref<{ name: string; enabled: boolean }[]>([]);
const contacts = ref<{ synced_at?: string; groups?: number; friends?: number; error?: string }>({});
const loading = ref(false);
const syncing = ref(false);
const saving = ref(false);
const refreshingAvatars = ref(false);
const keyword = ref("");
const tab = ref<"all" | "private" | "group" | "stranger" | "plugin">("all");
const onlyConfigured = ref(false);
const editing = ref(false);
const draft = ref<SessionItem | null>(null);
const avatars = ref<Record<string, string>>({});

const wfOptions = computed(() => [
  { label: "（跟随全局）", value: "" },
  ...workflows.value.map((w) => ({ label: w.name + (w.enabled ? "" : "（已停用）"), value: w.name })),
]);
const triOptions = [
  { label: "跟随全局", value: null },
  { label: "允许 / 发送", value: 1 },
  { label: "禁止 / 不发送", value: 0 },
];
const enabledOptions = [
  { label: "跟随全局（允许）", value: null },
  { label: "启用", value: 1 },
  { label: "禁用（黑名单）", value: 0 },
];

// v7.11.3：插件身份以 origin 前缀为准（后端已保证一致），前端再兜一层防御，
// 保证「用某插件出过图的群」绝不会被算进插件页。
const isPlugin = (s: SessionItem) => !!s.is_plugin || String(s.origin || "").startsWith("plugin:");
// v7.11.4：私聊里区分「好友（在联系人快照内）」与「非好友（出过图但不在好友列表）」。
const isPrivate = (s: SessionItem) => !s.is_group && !isPlugin(s);
const isStranger = (s: SessionItem) => isPrivate(s) && s.in_contacts === false;

const counts = computed(() => ({
  all: sessions.value.length,
  private: sessions.value.filter((s) => isPrivate(s) && !isStranger(s)).length,
  group: sessions.value.filter((s) => s.is_group && !isPlugin(s)).length,
  stranger: sessions.value.filter(isStranger).length,
  plugin: sessions.value.filter(isPlugin).length,
  configured: sessions.value.filter((s) => s.has_override).length,
  disabled: sessions.value.filter((s) => s.enabled === 0).length,
}));

const filtered = computed(() => {
  let list = sessions.value;
  if (tab.value === "private") list = list.filter((s) => isPrivate(s) && !isStranger(s));
  else if (tab.value === "group") list = list.filter((s) => s.is_group && !isPlugin(s));
  else if (tab.value === "stranger") list = list.filter(isStranger);
  else if (tab.value === "plugin") list = list.filter(isPlugin);
  if (onlyConfigured.value) list = list.filter((s) => s.has_override);
  const k = keyword.value.trim().toLowerCase();
  if (k) list = list.filter((s) =>
    (s.name || "").toLowerCase().includes(k) ||
    s.origin.toLowerCase().includes(k) ||
    (s.plugin_name || "").toLowerCase().includes(k));
  return list;
});

// 「全部」标签下按类型分组展示（群聊 / 私聊 / 插件），避免混在一起分不清；
// 其它标签已经是单一类型，不再分组。
const grouped = computed<{ key: string; label: string; items: SessionItem[] }[]>(() => {
  const list = filtered.value;
  if (tab.value !== "all") return [{ key: tab.value, label: "", items: list }];
  return [
    { key: "group", label: "群聊", items: list.filter((s) => s.is_group && !isPlugin(s)) },
    { key: "private", label: "私聊（好友）", items: list.filter((s) => isPrivate(s) && !isStranger(s)) },
    { key: "stranger", label: "非好友私聊", items: list.filter(isStranger) },
    { key: "plugin", label: "插件来源", items: list.filter(isPlugin) },
  ].filter((g) => g.items.length > 0);
});

const modalTitle = computed(() => {
  if (!draft.value) return "会话配置";
  return draft.value.is_plugin
    ? `插件来源：${draft.value.plugin_name || draft.value.origin}`
    : `会话配置：${displayName(draft.value)}`;
});

async function load() {
  loading.value = true;
  try {
    const data = await apiGet("sessions");
    sessions.value = (data?.sessions || []).map((s: any) => ({
      ...s,
      has_override: Boolean(
        s.wf_t2i || s.wf_i2i || s.pin_t2i || s.pin_i2i ||
        s.max_total !== null || s.max_hour !== null || s.max_day !== null ||
        s.nsfw !== null || s.card !== null || s.enabled !== null || s.note
      ),
      usage: s.usage || { total_used: 0, hour_used: 0, day_used: 0 },
      in_contacts: s.in_contacts !== false,
      wf_now_t2i: s.wf_now_t2i || { name: "", pinned: false, source: "" },
      wf_now_i2i: s.wf_now_i2i || { name: "", pinned: false, source: "" },
    }));
    workflows.value = data?.workflows || [];
    contacts.value = data?.contacts || {};
    await loadAvatars(false);
  } catch (e: any) {
    message.error(e?.message || "加载会话列表失败");
  } finally {
    loading.value = false;
  }
}
onMounted(load);

async function loadAvatars(force: boolean) {
  const byKind: Record<string, string[]> = { user: [], group: [] };
  for (const s of sessions.value) {
    const kind = s.avatar_kind === "group" ? "group" : "user";
    if (s.avatar_id && !byKind[kind].includes(s.avatar_id)) byKind[kind].push(s.avatar_id);
  }
  const merged: Record<string, string> = {};
  for (const kind of ["user", "group"]) {
    if (!byKind[kind].length) continue;
    try {
      const r = await apiGet("avatars",
        { type: kind, ids: byKind[kind].join(","), force: force ? 1 : 0 },
        { timeout: 25000 });
      Object.assign(merged, r?.items || {});
    } catch { /* 头像失败不影响会话列表 */ }
  }
  avatars.value = merged;
}
async function refreshAvatars() {
  refreshingAvatars.value = true;
  try {
    await loadAvatars(true);
    message.success("头像已刷新");
  } catch (e: any) {
    message.error(e?.message || "刷新头像失败");
  } finally {
    refreshingAvatars.value = false;
  }
}
function avatarSrc(s: SessionItem): string { return avatars.value[s.avatar_id] || ""; }

async function syncContacts() {
  syncing.value = true;
  try {
    const r = await apiPost("sessions/sync", {});
    if (r?.ok) message.success(`已同步 ${r.groups || 0} 个群、${r.friends || 0} 位好友`);
    else message.warning(r?.error || "同步失败（可能未连接 OneBot）");
    await load();
  } catch (e: any) {
    message.error(e?.message || "同步失败");
  } finally {
    syncing.value = false;
  }
}

function displayName(s: SessionItem): string {
  return s.name || (s.is_group ? `群 ${s.group_id}` : `用户 ${s.user_id || s.origin.split(":").pop()}`);
}
function subName(s: SessionItem): string {
  if (s.is_plugin) return `来源 plugin:${s.plugin_name || s.user_id}`;
  if (s.is_group) return `群号 ${s.group_id}`;
  const sid = s.user_id || s.origin.split(":").pop() || "";
  return `QQ ${sid}`;
}
function initial(s: SessionItem): string {
  return (s.is_group ? "群" : "私");
}
function activeText(ts: number): string {
  if (!ts) return "—";
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "刚刚";
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`;
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`;
  if (d < 86400 * 7) return `${Math.floor(d / 86400)} 天前`;
  const dt = new Date(ts * 1000);
  return `${dt.getMonth() + 1}/${dt.getDate()}`;
}

async function toggleEnabled(s: SessionItem, v: boolean) {
  try {
    await apiPost("sessions/set", { origin: s.origin, enabled: v ? 1 : 0 });
    s.enabled = v ? 1 : 0;
    message.success(v ? "已启用该会话" : "已禁用该会话（等同黑名单）");
  } catch (e: any) {
    message.error(e?.message || "操作失败");
    await load();
  }
}

function openEdit(s: SessionItem) {
  draft.value = reactive({ ...s, usage: { ...s.usage } });
  editing.value = true;
}

async function saveEdit() {
  const d = draft.value;
  if (!d) return;
  saving.value = true;
  try {
    if (!d.is_plugin) {
      await apiPost("sessions/set", {
        origin: d.origin,
        note: d.note || "",
        wf_t2i: d.wf_t2i || "",
        wf_i2i: d.wf_i2i || "",
        pin_t2i: d.pin_t2i ? 1 : 0,
        pin_i2i: d.pin_i2i ? 1 : 0,
        max_total: d.max_total === null || d.max_total === undefined ? null : d.max_total,
        max_hour: d.max_hour === null || d.max_hour === undefined ? null : d.max_hour,
        max_day: d.max_day === null || d.max_day === undefined ? null : d.max_day,
        nsfw: d.nsfw === null || d.nsfw === undefined ? null : d.nsfw,
        card: d.card === null || d.card === undefined ? null : d.card,
        enabled: d.enabled === null || d.enabled === undefined ? null : d.enabled,
      });
    } else {
      await apiPost("sessions/set", { origin: d.origin, note: d.note || "" });
    }
    editing.value = false;
    message.success("已保存");
    await load();
  } catch (e: any) {
    message.error(e?.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

async function resetConfig() {
  const d = draft.value;
  if (!d) return;
  dialog.warning({
    title: "恢复跟随全局",
    content: `确定清空「${displayName(d)}」的全部会话覆盖配置吗？`,
    positiveText: "确定", negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("sessions/reset", { origin: d.origin });
        editing.value = false;
        message.success("已恢复跟随全局");
        await load();
      } catch (e: any) { message.error(e?.message || "重置失败"); }
    },
  });
}

async function resetUsage() {
  const d = draft.value;
  if (!d) return;
  dialog.warning({
    title: "清零会话用量",
    content: `确定清零「${displayName(d)}」的会话限额计数吗？用户限额不受影响。`,
    positiveText: "确定", negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("sessions/reset_usage", { origin: d.origin });
        message.success("会话用量已清零");
        await load();
      } catch (e: any) { message.error(e?.message || "清零失败"); }
    },
  });
}
</script>

<style scoped>
.session-view { height: 100%; min-height: 0; display: flex; flex-direction: column; gap: 16px; padding: 18px; }
.view-head { flex: 0 0 auto; display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.view-head h2 { margin: 0 0 6px; font-size: 20px; color: var(--text-main); }
.view-head p { margin: 0; font-size: 12px; color: var(--text-sub); line-height: 1.6; }
.head-actions { display: flex; gap: 8px; }
.summary-row { flex: 0 0 auto; display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }
.sum-card { background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: 12px; padding: 12px 14px; }
.sum-label { font-size: 11px; color: var(--text-sub); }
.sum-value { font-size: 20px; font-weight: 700; color: var(--text-main); margin-top: 4px; }
.panel { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: 14px; padding: 16px; }
.contacts-line { flex: 0 0 auto; font-size: 12px; color: var(--text-sub); padding-bottom: 12px; border-bottom: 1px solid var(--border-color); display: flex; gap: 10px; flex-wrap: wrap; }
.contacts-error { color: #e74c3c; }
.toolbar { flex: 0 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 0; flex-wrap: wrap; }
.tabs { display: inline-flex; gap: 4px; padding: 4px; border-radius: 11px; background: var(--bg-body); border: 1px solid var(--border-color); }
.tab { border: 0; background: transparent; color: var(--text-sub); font-size: 13px; font-family: inherit; padding: 7px 13px; border-radius: 8px; cursor: pointer; }
.tab.active { background: var(--bg-panel); color: var(--text-main); font-weight: 650; box-shadow: 0 2px 8px rgba(255,143,179,.15); }
.tab-count { font-size: 11px; color: var(--text-sub); margin-left: 4px; }
.toolbar-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.loading-line, .empty-line { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 40px 0; text-align: center; color: var(--text-sub); font-size: 13px; }
.session-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding-right: 6px; display: flex; flex-direction: column; gap: 9px; }
.group-heading { display: flex; align-items: center; gap: 8px; margin: 14px 0 2px; font-size: 13px; color: var(--text-main); }
.group-heading:first-child { margin-top: 2px; }
.group-heading-bar { width: 4px; height: 15px; border-radius: 3px; background: var(--accent); }
.group-heading-bar.group { background: #5a8dee; }
.group-heading-bar.private { background: #ff6b9d; }
.group-heading-bar.plugin { background: #f08a24; }
.group-heading-count { font-size: 11px; color: var(--text-sub); background: var(--bg-body); border: 1px solid var(--border-color); border-radius: 999px; padding: 0 7px; }
.session-item { display: flex; align-items: center; gap: 12px; padding: 12px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-body); }
.session-item.disabled { opacity: .65; }
.avatar { width: 42px; height: 42px; flex: 0 0 42px; border-radius: 12px; display: grid; place-items: center; color: #fff; font-weight: 700; font-size: 14px; background: linear-gradient(135deg, #ff8fb3, #ff6b9d); overflow: hidden; }
.avatar.group { background: linear-gradient(135deg, #7bc0ff, #5a8dee); }
.avatar.plugin { background: linear-gradient(135deg, #ffc26e, #f08a24); }
.avatar img { width: 100%; height: 100%; object-fit: cover; }
.session-main { flex: 1; min-width: 0; }
.line1 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.name { font-size: 14px; color: var(--text-main); }
.sub-id { font-size: 11px; color: var(--text-sub); }
.line2 { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 7px; }
.wf-chip { font-size: 11px; padding: 3px 8px; border-radius: 7px; background: var(--bg-panel); border: 1px solid var(--border-color); color: var(--text-sub); }
.wf-chip.pinned { border-color: var(--accent); color: var(--text-main); }
.wf-chip em { font-style: normal; color: #e74c3c; margin-left: 4px; }
.wf-next { font-size: 11px; color: var(--text-sub); }
.wf-last { font-size: 11px; color: var(--accent); margin-top: 2px; }
.line3 { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 7px; font-size: 11px; color: var(--text-sub); }
.line3 .note { color: var(--accent); }
.session-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.active-time { font-size: 11px; color: var(--text-sub); min-width: 48px; text-align: right; }
.edit-form { display: flex; flex-direction: column; gap: 14px; }
.form-hint { font-size: 11px; color: var(--text-sub); word-break: break-all; }
.form-section { border: 1px solid var(--border-color); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.section-title { font-size: 13px; font-weight: 650; color: var(--text-main); }
.field-row { display: flex; flex-direction: column; gap: 6px; }
.field-label { font-size: 12px; color: var(--text-sub); }
.wf-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.wf-box { display: flex; flex-direction: column; gap: 8px; }
.wf-head { font-size: 12px; color: var(--text-sub); }
.wf-fix { display: flex; align-items: center; justify-content: space-between; font-size: 11px; color: var(--text-sub); }
.form-tip { font-size: 11px; color: var(--text-sub); line-height: 1.6; }
.quota-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.quota-field { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--text-sub); }
.opt-grid { display: flex; gap: 16px; flex-wrap: wrap; }
.opt-field { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--text-sub); }
.modal-footer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.grow { flex: 1; }
@media (max-width: 720px) {
  .wf-grid, .quota-grid { grid-template-columns: 1fr; }
  .session-item { align-items: flex-start; flex-wrap: wrap; }
  .session-actions { width: 100%; justify-content: flex-end; }
}
</style>
