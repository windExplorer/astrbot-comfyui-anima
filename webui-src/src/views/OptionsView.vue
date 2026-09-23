<template>
  <div class="opt-view">
    <div class="view-head">
      <div>
        <h2>配置项</h2>
        <p>
          动态配置集合。当前包含「底模」——即开源绘图模型族（anima / krea2 / z-image-turbo /
          qwen image 2.1 / boogu 等，此前写死在代码里），可增删改查并配置语种与提示词风格；
          基础工作流按匹配关键字自动关联底模。后续新增的动态项（采样器、调度器等）也会放在本页。
        </p>
      </div>
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="view-actions">
          <n-button :loading="loading" @click="load">刷新</n-button>
          <n-button type="primary" @click="addModel">＋ 新增底模</n-button>
        </div>
      </Teleport>
    </div>

    <n-divider style="margin: 4px 0 10px">── 底模（模型族） ──</n-divider>

    <div class="filter-bar">
      <n-input
        v-model:value="searchText"
        size="small"
        clearable
        style="width: 260px"
        placeholder="搜索名称 / 匹配关键字 / 描述…"
      />
      <span v-if="searchText.trim()" class="filter-hint">匹配 {{ filtered.length }} / {{ items.length }} 条</span>
    </div>

    <n-spin :show="loading">
      <n-empty v-if="!loading && !filtered.length" description="底模库为空，点「新增底模」添加（如 anima / krea2 / qwen image 2.1）。" style="padding:60px" />
      <div v-else class="card-grid">
        <div v-for="m in filtered" :key="m.id" class="bm-card">
          <div class="card-cover">
            <img v-if="m.image" v-cover-lazy="'bm:' + m.image" alt="" loading="lazy" />
            <div v-else class="cover-empty">无封面</div>
          </div>
          <div class="card-body">
            <div class="card-title">{{ m.name || "(未命名)" }}</div>
            <div class="card-meta">
              <n-tag size="tiny" :type="m.prompt_style === 'danbooru' ? 'warning' : 'info'" :bordered="false">
                {{ m.prompt_style === "danbooru" ? "danbooru 标签" : "自然语言" }}
              </n-tag>
              <n-tag v-if="m.danbooru_ready" size="tiny" type="success" :bordered="false">danbooru 适配</n-tag>
            </div>
            <div class="card-sub">匹配关键字：{{ m.keywords || "—" }}</div>
            <div class="card-sub">语言：{{ (m.languages || []).join(" / ") }}｜优先：{{ m.priority_lang || "—" }}</div>
            <div class="card-actions">
              <n-button size="tiny" @click="editModel(m)">编辑</n-button>
              <n-button size="tiny" @click="openCoverEditor(m)">上传封面</n-button>
              <n-button size="tiny" type="error" @click="removeModel(m)">删除</n-button>
            </div>
          </div>
        </div>
      </div>
    </n-spin>

    <CoverEditor v-model:show="coverShow" title="设置底模封面" scope="bm" @confirm="onCoverConfirm" />

    <!-- 编辑弹窗 -->
    <n-modal v-model:show="editShow" preset="card" :title="editTitle" class="bm-modal" :bordered="false">
      <n-form label-placement="top" class="edit-form">
        <div class="form-grid">
          <n-form-item label="名称"><n-input v-model:value="editForm.name" placeholder="如 Qwen Image 2.1 / anima / boogu" /></n-form-item>
          <n-form-item label="匹配关键字（用于自动关联基础工作流）">
            <n-input v-model:value="editForm.keywords" placeholder="如 qwen、qwen_image；逗号/顿号分隔" />
          </n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="提示词风格">
            <n-select v-model:value="editForm.prompt_style" :options="styleOptions" />
          </n-form-item>
          <n-form-item label="danbooru 适配">
            <n-switch v-model:value="editForm.danbooru_ready" />
            <span class="form-hint">开启后标签翻译/补全按该模型可识别 danbooru 标签处理</span>
          </n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="支持语言（多选）">
            <n-select v-model:value="editForm.languages" multiple :options="langOptions" />
          </n-form-item>
          <n-form-item label="优先语种">
            <n-select v-model:value="editForm.priority_lang" :options="priorityOptions" />
          </n-form-item>
        </div>
        <n-form-item label="描述"><n-input v-model:value="editForm.description" type="textarea" :rows="3" /></n-form-item>
      </n-form>
      <template #footer>
        <div class="modal-footer">
          <n-button @click="editShow = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveEdit">保存</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import {
  useMessage, useDialog, NButton, NSpin, NForm, NFormItem, NInput,
  NModal, NSelect, NSwitch, NTag, NEmpty, NDivider,
} from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import { useDevice } from "@/composables/useDevice";
import CoverEditor from "@/components/CoverEditor.vue";

const message = useMessage();
const dialog = useDialog();
const { isMobile } = useDevice();
const loading = ref(false);
const saving = ref(false);
const items = ref<any[]>([]);
const searchText = ref("");

const styleOptions = [
  { label: "自然语言（可掺杂标签）", value: "natural" },
  { label: "danbooru 标签", value: "danbooru" },
];
const langOptions = ["中文", "英文"].map((l) => ({ label: l, value: l }));
const priorityOptions = computed(() =>
  (editForm.languages || []).map((l: string) => ({ label: l, value: l }))
);

const filtered = computed(() => {
  const kw = searchText.value.trim().toLowerCase();
  if (!kw) return items.value;
  return items.value.filter((m) =>
    [m.name, m.keywords, m.description].some((s: string) => String(s || "").toLowerCase().includes(kw))
  );
});

async function load() {
  loading.value = true;
  try {
    const d = await apiGet("basemodels");
    items.value = Array.isArray(d?.items) ? d.items : [];
  } catch (e: any) {
    message.error(e?.message || "读取底模库失败");
  } finally {
    loading.value = false;
  }
}

const editShow = ref(false);
const editTitle = ref("新增底模");
const editForm = reactive<Record<string, any>>({});

function openForm(m: any | null) {
  editTitle.value = m ? "编辑底模" : "新增底模";
  Object.keys(editForm).forEach((k) => delete editForm[k]);
  Object.assign(editForm, {
    id: m?.id ?? null,
    name: m?.name || "",
    keywords: m?.keywords || "",
    image: m?.image || "",
    prompt_style: m?.prompt_style || "natural",
    languages: Array.isArray(m?.languages) ? [...m.languages] : ["中文", "英文"],
    priority_lang: m?.priority_lang || "中文",
    danbooru_ready: !!m?.danbooru_ready,
    description: m?.description || "",
  });
  editShow.value = true;
}
function addModel() { openForm(null); }
function editModel(m: any) { openForm(m); }

async function saveEdit() {
  if (!String(editForm.name || "").trim()) { message.warning("名称必填"); return; }
  saving.value = true;
  try {
    await apiPost("basemodels/save", { ...editForm, name: String(editForm.name).trim() });
    message.success("已保存");
    editShow.value = false;
    await load();
  } catch (e: any) {
    message.error(e?.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

function removeModel(m: any) {
  dialog.warning({
    title: "删除底模",
    content: `确定要删除底模「${m.name || ""}」吗？此操作不可恢复！`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: () => {
      apiPost("basemodels/delete", { id: m.id })
        .then(() => { message.success("已删除"); load(); })
        .catch((e: any) => message.error(e?.message || "删除失败"));
    },
  });
}

const coverShow = ref(false);
let coverTarget: any = null;
function openCoverEditor(m: any) { coverTarget = m; coverShow.value = true; }
async function onCoverConfirm(name: string) {
  if (!coverTarget || !name) return;
  try {
    await apiPost("basemodels/save", { ...coverTarget, image: name });
    message.success("封面已更新");
    await load();
  } catch (e: any) {
    message.error(e?.message || "封面保存失败");
  }
}

onMounted(load);
</script>

<style scoped>
.opt-view { height: 100%; overflow: auto; padding: 0 4px; }
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; max-width: 72%; }
.view-actions { display: flex; gap: 8px; }
.filter-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.filter-hint { color: var(--text-sub); font-size: 12px; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
.bm-card { border: 1px solid var(--border-color); border-radius: 10px; background: var(--bg-panel); overflow: hidden; display: flex; flex-direction: column; }
.card-cover { height: 150px; background: var(--bg-body, #f5f5f5); display: flex; align-items: center; justify-content: center; overflow: hidden; }
.card-cover img { width: 100%; height: 100%; object-fit: cover; }
.cover-empty { color: var(--text-sub); font-size: 12px; }
.card-body { padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; flex: 1; }
.card-title { font-weight: 600; }
.card-meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.card-sub { color: var(--text-sub); font-size: 12px; word-break: break-all; }
.card-actions { display: flex; gap: 6px; margin-top: auto; flex-wrap: wrap; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; }
.form-hint { color: var(--text-sub); font-size: 12px; margin-left: 8px; }
.edit-form { max-height: 62vh; overflow: auto; padding-right: 4px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
@media (max-width: 640px) {
  .form-grid { grid-template-columns: 1fr; }
  .view-head p { max-width: 100%; }
}
</style>
