<template>
  <div class="char-page">
    <!-- 概览 -->
    <div class="stat-row">
      <div class="stat-card"><div class="stat-num">{{ characters.length }}</div><div class="stat-label">角色卡片</div></div>
      <div class="stat-card"><div class="stat-num">{{ anchorTotal }}</div><div class="stat-label">锚点总数</div></div>
      <div class="stat-card"><div class="stat-num">{{ boundCount }}</div><div class="stat-label">已绑定人格</div></div>
      <div class="stat-card"><div class="stat-num">{{ loraCount }}</div><div class="stat-label">关联 LoRA</div></div>
    </div>

    <!-- 工具栏 -->
    <n-card class="tool-card" :bordered="false">
      <div class="tool-grid">
        <n-input
          v-model:value="keyword"
          placeholder="搜索角色名 / 别名 / 作品 / 人格"
          clearable
          @keyup.enter="reload"
        />
        <div class="tool-actions">
          <n-button type="primary" @click="reload">查询</n-button>
          <n-button type="primary" ghost @click="openCreate">＋ 新建角色</n-button>
          <n-button @click="doExport">导出 JSON</n-button>
          <n-button @click="importOpen = true">导入 JSON</n-button>
        </div>
      </div>
      <div class="tip">
        角色卡片是绘图时的<strong>最高优先级来源</strong>：命中后插件自动注入锚点标签，多人时自动生成
        <code>2girls</code> 计数标签与每个角色一个 <code>(标签:1.2)</code> 分组。
        也可以用对话设定（说「记住小叽的样子是…」）或 <code>/角色</code> 指令管理。
      </div>
    </n-card>

    <!-- 列表 -->
    <n-card class="list-card" :bordered="false">
      <n-data-table
        :columns="columns"
        :data="characters"
        :loading="loading"
        :row-key="(row: any) => row.id"
        size="small"
        :bordered="false"
      />
      <div v-if="!loading && !characters.length" class="empty">
        暂无角色卡片。点「＋ 新建角色」，或对 AI 说「记住XX的样子是…」。
      </div>
    </n-card>

    <!-- 详情抽屉 -->
    <n-drawer v-model:show="detailOpen" :width="560" placement="right">
      <n-drawer-content :title="detail ? `角色卡：${detail.name}` : '角色卡'" closable>
        <template v-if="detail">
          <n-form label-placement="left" label-width="86" size="small">
            <n-form-item label="角色名"><n-input v-model:value="form.name" /></n-form-item>
            <n-form-item label="别名">
              <n-input v-model:value="form.aliasesText" placeholder="逗号分隔，如：小叽酱,叽叽" />
            </n-form-item>
            <n-form-item label="绑定人格">
              <n-input v-model:value="form.persona_name" placeholder="AstrBot 人格名，如 小叽V4（用户说「画你」时命中）" />
            </n-form-item>
            <n-form-item label="作品"><n-input v-model:value="form.work" placeholder="如 Neverness to Everness" /></n-form-item>
            <n-form-item label="关联 LoRA">
              <n-input v-model:value="form.lora_name" placeholder="LoRA 库里的规范名；命中卡片时自动启用" />
            </n-form-item>
            <n-form-item label="备注"><n-input v-model:value="form.note" type="textarea" :rows="2" /></n-form-item>
            <n-form-item label="启用"><n-switch v-model:value="form.enabled" /></n-form-item>
            <n-form-item label=" ">
              <n-space>
                <n-button type="primary" :loading="saving" @click="saveCard">保存卡片</n-button>
                <n-popconfirm @positive-click="removeCard">
                  <template #trigger><n-button type="error" ghost>删除角色</n-button></template>
                  删除「{{ detail.name }}」及其全部锚点？此操作不可恢复。
                </n-popconfirm>
              </n-space>
            </n-form-item>
          </n-form>

          <n-divider>锚点（{{ detail.anchors?.length || 0 }} 个）</n-divider>
          <div class="anchor-head">
            <n-button size="small" type="primary" ghost @click="openAnchorCreate">＋ 新增锚点</n-button>
          </div>
          <div v-for="a in detail.anchors || []" :key="a.id" class="anchor-item">
            <div class="anchor-top">
              <span class="anchor-name">{{ a.name }}</span>
              <n-tag v-if="isPrimary(a)" size="small" type="success" :bordered="false">主锚点</n-tag>
              <n-tag size="small" :bordered="false">权重 {{ a.weight }}</n-tag>
              <n-tag v-if="a.lora_name" size="small" type="info" :bordered="false">LoRA {{ a.lora_name }}</n-tag>
            </div>
            <div class="anchor-pos">{{ a.positive }}</div>
            <div v-if="a.negative" class="anchor-neg">负向：{{ a.negative }}</div>
            <div class="anchor-ops">
              <n-button size="tiny" @click="openAnchorEdit(a)">编辑</n-button>
              <n-button size="tiny" quaternary @click="setPrimary(a)">设为主锚点</n-button>
              <n-popconfirm @positive-click="removeAnchor(a)">
                <template #trigger><n-button size="tiny" type="error" quaternary>删除</n-button></template>
                删除锚点「{{ a.name }}」？
              </n-popconfirm>
            </div>
          </div>
          <div v-if="!(detail.anchors || []).length" class="empty">还没有锚点，点「＋ 新增锚点」添加（至少一个才能注入）。</div>
        </template>
      </n-drawer-content>
    </n-drawer>

    <!-- 新建角色 -->
    <n-modal v-model:show="createOpen" preset="card" title="新建角色卡" style="max-width: 520px">
      <n-form label-placement="left" label-width="86" size="small">
        <n-form-item label="角色名"><n-input v-model:value="createForm.name" placeholder="如 薄荷" /></n-form-item>
        <n-form-item label="别名"><n-input v-model:value="createForm.aliasesText" placeholder="逗号分隔（可留空）" /></n-form-item>
        <n-form-item label="绑定人格"><n-input v-model:value="createForm.persona_name" placeholder="AstrBot 人格名（可留空）" /></n-form-item>
        <n-form-item label="作品"><n-input v-model:value="createForm.work" placeholder="可留空" /></n-form-item>
        <n-form-item label="关联 LoRA"><n-input v-model:value="createForm.lora_name" placeholder="可留空" /></n-form-item>
        <n-divider style="margin: 6px 0 12px">首个锚点（必填）</n-divider>
        <n-form-item label="锚点名"><n-input v-model:value="createForm.anchor_name" placeholder="默认装" /></n-form-item>
        <n-form-item label="标签串">
          <n-input v-model:value="createForm.positive" type="textarea" :rows="4"
                   placeholder="英文 danbooru 标签，逗号分隔。例：mint_\\(nte\\), teal hair, long hair, red eyes, cat ears" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="createOpen = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="createCard">创建</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 锚点编辑 -->
    <n-modal v-model:show="anchorOpen" preset="card"
             :title="anchorForm.id ? '编辑锚点' : '新增锚点'" style="max-width: 560px">
      <n-form label-placement="left" label-width="96" size="small">
        <n-form-item label="锚点名"><n-input v-model:value="anchorForm.name" placeholder="默认装 / 泳装 / 校服" /></n-form-item>
        <n-form-item label="类型">
          <n-select v-model:value="anchorForm.kind" :options="kindOptions" />
        </n-form-item>
        <n-form-item label="标签串">
          <n-input v-model:value="anchorForm.positive" type="textarea" :rows="4"
                   placeholder="英文 danbooru 标签，逗号分隔（不要写自然语言长句、不要写权重）" />
        </n-form-item>
        <n-form-item label="负向标签">
          <n-input v-model:value="anchorForm.negative" type="textarea" :rows="2" placeholder="可留空" />
        </n-form-item>
        <n-form-item label="分组权重">
          <n-input-number v-model:value="anchorForm.weight" :min="1" :max="1.5" :step="0.05" style="width: 160px" />
          <span class="hint">多人分组时用，建议 1.1~1.3（不超 1.5）</span>
        </n-form-item>
        <n-form-item label="覆盖 LoRA"><n-input v-model:value="anchorForm.lora_name" placeholder="可留空（优先于角色级 LoRA）" /></n-form-item>
        <n-form-item label="抑制触发词">
          <n-switch v-model:value="anchorForm.skip_trigger_words" />
          <span class="hint">开启时该 LoRA 触发词不再全局追加（锚点里已写就不重复）</span>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="anchorOpen = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveAnchor">保存</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 导入 -->
    <n-modal v-model:show="importOpen" preset="card" title="导入角色卡片 JSON" style="max-width: 620px">
      <n-input v-model:value="importText" type="textarea" :rows="10" placeholder='把导出的 JSON 粘贴到这里（{"version":1,"characters":[…] }）' />
      <template #footer>
        <n-space justify="end">
          <n-button @click="importOpen = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="doImport">导入</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from "vue";
import {
  NButton, NCard, NDataTable, NDivider, NDrawer, NDrawerContent, NForm, NFormItem,
  NInput, NInputNumber, NModal, NPopconfirm, NSelect, NSpace, NSwitch, NTag,
  useDialog, useMessage,
} from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";

const message = useMessage();
const dialog = useDialog();

const loading = ref(false);
const saving = ref(false);
const keyword = ref("");
const characters = ref<any[]>([]);

const detailOpen = ref(false);
const detail = ref<any>(null);
const createOpen = ref(false);
const anchorOpen = ref(false);
const importOpen = ref(false);
const importText = ref("");

const form = reactive({
  name: "", aliasesText: "", persona_name: "", work: "", lora_name: "", note: "", enabled: true,
});
const createForm = reactive({
  name: "", aliasesText: "", persona_name: "", work: "", lora_name: "",
  anchor_name: "默认装", positive: "",
});
const anchorForm = reactive({
  id: 0, name: "默认装", kind: "full", positive: "", negative: "",
  weight: 1.2, lora_name: "", skip_trigger_words: true,
});

const kindOptions = [
  { label: "外观 + 服装（推荐）", value: "full" },
  { label: "仅外观", value: "appearance" },
  { label: "仅服装/造型", value: "outfit" },
];

const anchorTotal = computed(() => characters.value.reduce((n, c) => n + (c.anchors?.length || 0), 0));
const boundCount = computed(() => characters.value.filter((c) => (c.persona_name || "").trim()).length);
const loraCount = computed(() => characters.value.filter((c) => (c.lora_name || "").trim()).length);

const columns = [
  { title: "角色名", key: "name", width: 140 },
  {
    title: "别名", key: "aliases", width: 160,
    render: (row: any) => (row.aliases || []).join("、") || "—",
  },
  {
    title: "绑定人格", key: "persona_name", width: 130,
    render: (row: any) => row.persona_name || "—",
  },
  { title: "作品", key: "work", width: 150, render: (row: any) => row.work || "—" },
  { title: "关联 LoRA", key: "lora_name", width: 120, render: (row: any) => row.lora_name || "—" },
  {
    title: "锚点", key: "anchor_count", width: 150,
    render: (row: any) => {
      const names = (row.anchors || []).map((a: any) => a.name).join("、");
      return `${row.anchors?.length || 0} 个${names ? "：" + names : ""}`;
    },
  },
  {
    title: "操作", key: "ops", width: 130,
    render: (row: any) =>
      h(NSpace, { size: 4 }, {
        default: () => [
          h(NButton, { size: "tiny", onClick: () => openDetail(row) }, { default: () => "查看/编辑" }),
        ],
      }),
  },
];

function isPrimary(a: any) {
  return detail.value && Number(detail.value.primary_anchor_id) === Number(a.id);
}

async function reload() {
  loading.value = true;
  try {
    const data = await apiGet("character/list", { keyword: keyword.value.trim() });
    characters.value = data?.characters || [];
  } catch (e: any) {
    message.error(`读取角色卡片失败：${e?.message || e}`);
  } finally {
    loading.value = false;
  }
}

function fillForm(c: any) {
  form.name = c?.name || "";
  form.aliasesText = (c?.aliases || []).join(",");
  form.persona_name = c?.persona_name || "";
  form.work = c?.work || "";
  form.lora_name = c?.lora_name || "";
  form.note = c?.note || "";
  form.enabled = c?.enabled !== false;
}

async function openDetail(row: any) {
  try {
    const c = await apiGet("character/detail", { id: row.id });
    detail.value = c;
    fillForm(c);
    detailOpen.value = true;
  } catch (e: any) {
    message.error(`读取详情失败：${e?.message || e}`);
  }
}

function openCreate() {
  createForm.name = "";
  createForm.aliasesText = "";
  createForm.persona_name = "";
  createForm.work = "";
  createForm.lora_name = "";
  createForm.anchor_name = "默认装";
  createForm.positive = "";
  createOpen.value = true;
}

function splitAliases(text: string) {
  return (text || "")
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

async function createCard() {
  if (!createForm.name.trim()) return message.warning("请填角色名");
  if (!createForm.positive.trim()) return message.warning("请填首个锚点的标签串");
  saving.value = true;
  try {
    const r = await apiPost("character/save", {
      name: createForm.name.trim(),
      aliases: splitAliases(createForm.aliasesText),
      persona_name: createForm.persona_name.trim(),
      work: createForm.work.trim(),
      lora_name: createForm.lora_name.trim(),
    });
    await apiPost("character/anchor/save", {
      character_id: r.id,
      anchor_name: createForm.anchor_name.trim() || "默认装",
      positive: createForm.positive.trim(),
      kind: "full",
      weight: 1.2,
    });
    createOpen.value = false;
    message.success(`已创建「${createForm.name.trim()}」`);
    await reload();
  } catch (e: any) {
    message.error(`创建失败：${e?.message || e}`);
  } finally {
    saving.value = false;
  }
}

async function saveCard() {
  if (!detail.value) return;
  saving.value = true;
  try {
    await apiPost("character/save", {
      id: detail.value.id,
      name: form.name.trim(),
      aliases: splitAliases(form.aliasesText),
      persona_name: form.persona_name.trim(),
      work: form.work.trim(),
      lora_name: form.lora_name.trim(),
      note: form.note,
      enabled: form.enabled,
    });
    message.success("已保存");
    await openDetail({ id: detail.value.id });
    await reload();
  } catch (e: any) {
    message.error(`保存失败：${e?.message || e}`);
  } finally {
    saving.value = false;
  }
}

function removeCard() {
  if (!detail.value) return;
  dialog.warning({
    title: "删除角色卡",
    content: `确认删除「${detail.value.name}」及其全部锚点？`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("character/delete", { id: detail.value.id });
        detailOpen.value = false;
        message.success("已删除");
        await reload();
      } catch (e: any) {
        message.error(`删除失败：${e?.message || e}`);
      }
    },
  });
}

function openAnchorCreate() {
  anchorForm.id = 0;
  anchorForm.name = "";
  anchorForm.kind = "full";
  anchorForm.positive = "";
  anchorForm.negative = "";
  anchorForm.weight = 1.2;
  anchorForm.lora_name = "";
  anchorForm.skip_trigger_words = true;
  anchorOpen.value = true;
}

function openAnchorEdit(a: any) {
  anchorForm.id = Number(a.id);
  anchorForm.name = a.name || "";
  anchorForm.kind = a.kind || "full";
  anchorForm.positive = a.positive || "";
  anchorForm.negative = a.negative || "";
  anchorForm.weight = Number(a.weight || 1.2);
  anchorForm.lora_name = a.lora_name || "";
  anchorForm.skip_trigger_words = a.skip_trigger_words !== false;
  anchorOpen.value = true;
}

async function saveAnchor() {
  if (!detail.value) return;
  if (!anchorForm.positive.trim()) return message.warning("标签串不能为空");
  saving.value = true;
  try {
    await apiPost("character/anchor/save", {
      character_id: detail.value.id,
      id: anchorForm.id || undefined,
      anchor_name: anchorForm.name.trim() || "默认装",
      kind: anchorForm.kind,
      positive: anchorForm.positive.trim(),
      negative: anchorForm.negative.trim(),
      weight: Number(anchorForm.weight || 1.2),
      lora_name: anchorForm.lora_name.trim(),
      skip_trigger_words: anchorForm.skip_trigger_words,
    });
    anchorOpen.value = false;
    message.success("锚点已保存");
    await openDetail({ id: detail.value.id });
    await reload();
  } catch (e: any) {
    message.error(`保存锚点失败：${e?.message || e}`);
  } finally {
    saving.value = false;
  }
}

function removeAnchor(a: any) {
  dialog.warning({
    title: "删除锚点",
    content: `确认删除锚点「${a.name}」？`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("character/anchor/delete", { id: a.id });
        message.success("已删除");
        await openDetail({ id: detail.value.id });
        await reload();
      } catch (e: any) {
        message.error(`删除失败：${e?.message || e}`);
      }
    },
  });
}

async function setPrimary(a: any) {
  try {
    await apiPost("character/anchor/primary", { character_id: detail.value.id, anchor_id: a.id });
    message.success(`主锚点 → ${a.name}`);
    await openDetail({ id: detail.value.id });
    await reload();
  } catch (e: any) {
    message.error(`设置失败：${e?.message || e}`);
  }
}

async function doExport() {
  try {
    const data = await apiGet("character/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `character_cards_${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 3000);
    message.success("已导出 JSON");
  } catch (e: any) {
    message.error(`导出失败：${e?.message || e}`);
  }
}

async function doImport() {
  let parsed: any = null;
  try {
    parsed = JSON.parse(importText.value || "{}");
  } catch {
    return message.error("JSON 解析失败，请检查粘贴内容");
  }
  saving.value = true;
  try {
    const r = await apiPost("character/import", { data: parsed });
    importOpen.value = false;
    importText.value = "";
    message.success(`已导入 ${r?.imported ?? 0} 个角色（同名角色会被合并）`);
    await reload();
  } catch (e: any) {
    message.error(`导入失败：${e?.message || e}`);
  } finally {
    saving.value = false;
  }
}

onMounted(reload);
</script>

<style scoped>
.char-page { display: flex; flex-direction: column; gap: 12px; }
.stat-row { display: flex; gap: 12px; flex-wrap: wrap; }
.stat-card {
  flex: 1 1 120px; background: rgba(128, 128, 128, 0.08); border-radius: 10px;
  padding: 10px 14px; text-align: center;
}
.stat-num { font-size: 22px; font-weight: 600; }
.stat-label { font-size: 12px; opacity: 0.7; }
.tool-grid { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.tool-grid > :first-child { flex: 1 1 240px; }
.tool-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.tip { margin-top: 10px; font-size: 12px; line-height: 1.7; opacity: 0.75; }
.tip code {
  background: rgba(128, 128, 128, 0.16); padding: 1px 4px; border-radius: 4px;
}
.empty { text-align: center; opacity: 0.6; padding: 18px 0; font-size: 13px; }
.anchor-head { display: flex; justify-content: flex-end; margin-bottom: 8px; }
.anchor-item {
  border: 1px solid rgba(128, 128, 128, 0.2); border-radius: 8px;
  padding: 8px 10px; margin-bottom: 8px;
}
.anchor-top { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.anchor-name { font-weight: 600; }
.anchor-pos { font-size: 12px; line-height: 1.6; margin-top: 6px; word-break: break-word; }
.anchor-neg { font-size: 12px; opacity: 0.7; margin-top: 4px; word-break: break-word; }
.anchor-ops { display: flex; gap: 6px; margin-top: 8px; }
.hint { font-size: 12px; opacity: 0.6; margin-left: 8px; }
</style>
