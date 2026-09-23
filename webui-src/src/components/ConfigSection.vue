<template>
  <div class="cfg-section">
    <div class="cfg-section-title">
      <h4>{{ sectionTitle }}</h4>
      <span v-if="schema?.hint && schema?.type !== 'template_list'">{{ schema.hint }}</span>
    </div>
    <div class="cfg-section-body">
      <!-- object：嵌套字段 -->
      <template v-if="schema?.type === 'object'">
        <div class="obj-fields">
          <ConfigField
            v-for="(f, fk) in schema.items"
            :key="fk"
            :field-key="fk"
            :field="f"
            :model-value="value?.[fk]"
            @update:model-value="updateNested(fk, $event)"
          />
        </div>
      </template>

      <!-- template_list：服务器 / LoRA / 工作流等可增删列表 -->
      <template v-else-if="schema?.type === 'template_list'">
        <div class="tmpl-list" :data-list="fieldKey">
          <div class="tmpl-toolbar">
            <span class="tmpl-hint">{{ schema.hint }}</span>
            <n-button size="small" type="primary" ghost @click="addItem">＋ 添加</n-button>
          </div>
          <n-empty v-if="!arrValue || !arrValue.length" description="暂无条目，点击「添加」新增" style="padding:16px" />
          <n-collapse v-else class="tmpl-items">
            <n-collapse-item
              v-for="(item, idx) in arrValue"
              :key="idx"
              :name="String(idx)"
            >
              <template #header>
                <div class="tmpl-item-head">
                  <span class="tmpl-item-name">{{ displayName(item) }}</span>
                  <n-button size="tiny" quaternary type="error" @click.stop="removeItem(idx)">删除</n-button>
                </div>
              </template>
              <div class="tmpl-item-body">
                <div class="obj-fields">
                  <ConfigField
                    v-for="row in itemFields"
                    :key="row.k"
                    :field-key="row.k"
                    :field="row.f"
                    :model-value="item[row.k]"
                    @update:model-value="updateItem(idx, row.k, $event)"
                  />
                  <!-- v7.1.0：唯一 key 只读展示（它同时是「服务器绑定值」，
                       手改会断链；空/重复由前后端自动补成唯一值） -->
                  <div v-if="schema.templates?.default?.items?.__template_key" class="tmpl-key-row">
                    <span class="k">唯一 key</span>
                    <code class="v">{{ item.__template_key || "（保存时自动生成）" }}</code>
                    <span class="tip">只读：工作流绑定服务器用的就是它，改名不影响</span>
                  </div>
                </div>
              </div>
            </n-collapse-item>
          </n-collapse>
        </div>
      </template>

      <!-- 标量字段 -->
      <template v-else>
        <ConfigField :field-key="fieldKey" :field="schema" :model-value="value" @update:model-value="emitScalar($event)" />
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NButton, NCollapse, NCollapseItem, NEmpty } from "naive-ui";
import ConfigField from "@/components/ConfigField.vue";

const props = defineProps<{
  schema: any;
  value: any;
  fieldKey: string;
}>();

const emit = defineEmits<{
  (e: "change"): void;
  (e: "update-scalar", key: string, value: any): void;
}>();

const arrValue = computed<any[]>(() => (Array.isArray(props.value) ? props.value : []));

/** template_list 条目的可编辑字段：v7.1.0 起把 __template_key 摘出来单独只读展示
 *  （它同时是服务器/工作流的绑定值，手改会断链；v-for 内用 v-if 取不到循环变量，
 *   所以用 computed 过滤） */
const itemFields = computed<Array<{ k: string; f: any }>>(() => {
  const items = props.schema?.templates?.default?.items || {};
  return Object.keys(items)
    .filter((k) => k !== "__template_key")
    .map((k) => ({ k, f: items[k] }));
});

// 标题：优先 label，其次 description，最后回退英文字段名
const sectionTitle = computed(() => props.schema?.label || props.schema?.description || props.fieldKey);

function displayName(item: any): string {
  const disp = props.schema?.templates?.default?.display_item;
  if (disp && item && item[disp] != null && item[disp] !== "") return String(item[disp]);
  return "(未命名)";
}

// 标量字段（bool/string/number）：props.value 是标量（非对象），不能在其上写属性，
// 必须把 (key, value) 向上 emit，由父级 ConfigView 更新 config[key]。
// 否则对布尔 false 等标量执行 props.value[key]=v 会抛
// "Cannot create property ... on boolean 'false'"（旧版 bug）。
function emitScalar(v: any) {
  emit("update-scalar", props.fieldKey, v);
  emit("change");
}

function updateNested(fk: string, v: any) {
  const cur = props.value && typeof props.value === "object" ? props.value : {};
  if (!props.value) (props as any).value = cur;
  cur[fk] = v;
  emit("change");
}

// v7.1.0：新增条目的 __template_key 必须**唯一**（此前写死 "default"，
// 多条目会撞成同一个 key —— 服务器/工作流的绑定值就是它，会指向错误对象）
function genTemplateKey(): string {
  try {
    const c: any = (globalThis as any).crypto;
    if (c?.randomUUID) return c.randomUUID().replace(/-/g, "").slice(0, 24);
  } catch { /* 忽略，走下面的兜底 */ }
  return `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

function addItem() {
  const itemsSchema = props.schema?.templates?.default?.items || {};
  const empty: Record<string, any> = {};
  Object.keys(itemsSchema).forEach((k) => {
    if (k === "__template_key") return;
    if ("default" in itemsSchema[k]) empty[k] = itemsSchema[k].default;
  });
  empty.__template_key = genTemplateKey();
  if (!Array.isArray(props.value)) (props as any).value = [];
  (props.value as any[]).push(empty);
  emit("change");
}

function removeItem(idx: number) {
  if (Array.isArray(props.value)) {
    (props.value as any[]).splice(idx, 1);
    emit("change");
  }
}

function updateItem(idx: number, fk: string, v: any) {
  if (Array.isArray(props.value) && props.value[idx]) {
    (props.value as any)[idx][fk] = v;
    emit("change");
  }
}
</script>

<style scoped>
.cfg-section {
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 12px;
  background: var(--bg-body);
}
.cfg-section-title h4 { margin: 0 0 4px; font-size: 14px; }
.cfg-section-title span { color: var(--text-sub); font-size: 12px; }
.cfg-section-body { margin-top: 8px; }
.obj-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 768px) { .obj-fields { grid-template-columns: 1fr; } }
.tmpl-list { display: flex; flex-direction: column; gap: 8px; }
.tmpl-toolbar { display: flex; justify-content: space-between; align-items: center; }
.tmpl-hint { color: var(--text-sub); font-size: 12px; }
.tmpl-item-head { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.tmpl-item-name { font-weight: 600; }
/* v7.1.0：唯一 key 只读展示行 */
.tmpl-key-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 4px 0;
  font-size: 12px;
  color: var(--text-sub, #8a8f99);
}
.tmpl-key-row .k { flex: 0 0 auto; }
.tmpl-key-row .v {
  font-family: Consolas, Menlo, monospace;
  background: rgba(127, 127, 127, 0.12);
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--text-main, inherit);
}
.tmpl-key-row .tip { flex: 1 1 200px; }

.tmpl-item-body { padding: 8px 0; }
@media (max-width: 768px) {
  .tmpl-toolbar { flex-direction: column; align-items: stretch; gap: 8px; }
  .tmpl-item-head { flex-wrap: wrap; gap: 6px; }
}
</style>
