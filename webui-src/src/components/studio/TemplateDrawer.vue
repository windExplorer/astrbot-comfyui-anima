<template>
  <n-drawer :show="show" :width="drawerWidth" placement="right" @update:show="(v: boolean) => emit('update:show', v)">
    <n-drawer-content title="提示词模板库" closable>
      <template #header-extra>
        <div class="tpl-head-actions">
          <n-button size="tiny" @click="manage = !manage">{{ manage ? "退出管理" : "管理" }}</n-button>
          <n-button size="tiny" :loading="reseeding" @click="reseed">补齐内置</n-button>
        </div>
      </template>

      <n-tabs v-model:value="activeCat" type="line" size="small" animated>
        <n-tab v-for="cat in categories" :key="cat" :name="cat" :tab="cat">
          <div class="tpl-list">
            <div v-for="t in byCategory(cat)" :key="t.id" class="tpl-item" :class="{ neg: t.is_negative }">
              <div class="tpl-row1">
                <span class="tpl-name">{{ t.name }}</span>
                <span v-if="t.is_negative" class="chip warn">负向</span>
                <span v-if="!t.enabled" class="chip dim">停用</span>
              </div>
              <div class="tpl-content">{{ t.content }}</div>
              <div v-if="!manage" class="tpl-actions">
                <n-button size="tiny" type="primary" ghost @click="emit('use', t, t.is_negative ? 'append-neg' : 'append-pos')">
                  {{ t.is_negative ? "加入负向" : "追加正向" }}
                </n-button>
                <n-button size="tiny" @click="emit('use', t, t.is_negative ? 'replace-neg' : 'replace-pos')">
                  {{ t.is_negative ? "设为负向" : "替换正向" }}
                </n-button>
              </div>
              <div v-else class="tpl-actions">
                <n-button size="tiny" @click="startEdit(t)">编辑</n-button>
                <n-popconfirm @positive-click="del(t)">
                  <template #trigger>
                    <n-button size="tiny" type="error" ghost>删除</n-button>
                  </template>
                  确定删除「{{ t.name }}」？
                </n-popconfirm>
              </div>
            </div>
          </div>
        </n-tab>
      </n-tabs>

      <!-- 管理：新增/编辑表单 -->
      <div v-if="manage" class="tpl-form">
        <div class="tpl-form-title">{{ form.id ? "编辑模板" : "新增模板" }}</div>
        <n-form size="small" label-placement="top">
          <n-form-item label="名称">
            <n-input v-model:value="form.name" placeholder="如：黄昏逆光" />
          </n-form-item>
          <n-form-item label="分类">
            <n-input v-model:value="form.category" placeholder="如：光影氛围" />
          </n-form-item>
          <n-form-item label="内容（提示词标签）">
            <n-input v-model:value="form.content" type="textarea" :rows="3" placeholder="sunset, backlighting, ..." />
          </n-form-item>
          <n-form-item label="类型">
            <n-radio-group v-model:value="formNegative">
              <n-radio :value="false">正向</n-radio>
              <n-radio :value="true">负向</n-radio>
            </n-radio-group>
          </n-form-item>
          <n-form-item label="启用">
            <n-switch v-model:value="formEnabled" />
          </n-form-item>
          <div class="tpl-form-actions">
            <n-button size="small" type="primary" :loading="saving" :disabled="!form.name.trim() || !form.content.trim()" @click="save">
              保存
            </n-button>
            <n-button size="small" quaternary @click="resetForm">取消</n-button>
          </div>
        </n-form>
      </div>
      <n-button v-if="manage && !form.id" size="small" block type="primary" ghost @click="resetForm(); formOpenForce = true" style="margin-bottom: 12px">
        ＋ 新增模板
      </n-button>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { studioApi, type StudioTemplate } from "@/api/studio";
import { useMessage } from "naive-ui";

const props = defineProps<{
  show: boolean;
  templates: StudioTemplate[];
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "use", t: StudioTemplate, target: "append-pos" | "replace-pos" | "append-neg" | "replace-neg"): void;
  (e: "changed"): void;
}>();

const message = useMessage();
const manage = ref(false);
const reseeding = ref(false);
const saving = ref(false);
const activeCat = ref("");
const formOpenForce = ref(false);

const form = reactive<{ id: number | null; name: string; category: string; content: string }>({
  id: null,
  name: "",
  category: "",
  content: "",
});
const formNegative = ref(false);
const formEnabled = ref(true);

const drawerWidth = computed(() => (window.innerWidth < 640 ? "100%" : 420));

const categories = computed(() => {
  const set: string[] = [];
  for (const t of props.templates) {
    if (!set.includes(t.category)) set.push(t.category);
  }
  return set.length ? set : ["未分类"];
});

watch(
  categories,
  (cats) => {
    if (!cats.includes(activeCat.value)) activeCat.value = cats[0] || "";
  },
  { immediate: true }
);

function byCategory(cat: string): StudioTemplate[] {
  return props.templates.filter((t) => t.category === cat);
}

function startEdit(t: StudioTemplate) {
  form.id = t.id;
  form.name = t.name;
  form.category = t.category;
  form.content = t.content;
  formNegative.value = t.is_negative;
  formEnabled.value = t.enabled;
}

function resetForm() {
  form.id = null;
  form.name = "";
  form.category = activeCat.value === "未分类" ? "" : activeCat.value;
  form.content = "";
  formNegative.value = false;
  formEnabled.value = true;
}

async function save() {
  saving.value = true;
  try {
    await studioApi.saveTemplate({
      id: form.id ?? undefined,
      name: form.name.trim(),
      category: form.category.trim() || "未分类",
      content: form.content.trim(),
      is_negative: formNegative.value,
      enabled: formEnabled.value,
    } as any);
    message.success("模板已保存");
    resetForm();
    emit("changed");
  } catch (e: any) {
    message.error(e?.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

async function del(t: StudioTemplate) {
  try {
    await studioApi.deleteTemplate(t.id);
    message.success("模板已删除");
    emit("changed");
  } catch (e: any) {
    message.error(e?.message || "删除失败");
  }
}

async function reseed() {
  reseeding.value = true;
  try {
    const d = await studioApi.reseedTemplates();
    message.success(`已补齐 ${d.added} 条内置模板`);
    emit("changed");
  } catch (e: any) {
    message.error(e?.message || "补齐失败");
  } finally {
    reseeding.value = false;
  }
}
</script>

<style scoped>
.tpl-head-actions { display: flex; gap: 6px; }
.tpl-list { display: flex; flex-direction: column; gap: 10px; padding-top: 4px; }
.tpl-item {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-panel);
  padding: 10px 12px;
}
.tpl-item.neg { border-left: 3px solid #e0a07e; }
.tpl-row1 { display: flex; align-items: center; gap: 6px; }
.tpl-name { font-size: 13px; font-weight: 600; color: var(--text-main); }
.chip {
  font-size: 10px;
  padding: 0 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--text-main);
}
.chip.warn { background: #e0a07e; color: #fff; }
.chip.dim { background: color-mix(in srgb, var(--text-sub) 14%, transparent); color: var(--text-sub); }
.tpl-content {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-sub);
  line-height: 1.5;
  word-break: break-all;
}
.tpl-actions { margin-top: 8px; display: flex; gap: 6px; }
.tpl-form {
  margin-top: 16px;
  border-top: 1px dashed var(--border-color);
  padding-top: 12px;
}
.tpl-form-title { font-size: 13px; font-weight: 600; color: var(--text-main); margin-bottom: 8px; }
.tpl-form-actions { display: flex; gap: 8px; }
</style>
