<template>
  <div class="features-view">
    <div class="view-head">
      <div>
        <h2>功能配置</h2>
        <p>声明「表情包 / 图生表情包 / 漫画」三类带字功能，每项绑定一个已配置 prompt_slots 的漫画工作流。指令 /表情包、/图生表情包 与 AI 工具 comfyui_comic / comfyui_meme_img 会按功能 key 找到对应配置并出图。</p>
      </div>
      <n-button type="primary" :loading="saving" @click="save">保存</n-button>
    </div>

    <n-spin :show="loading">
      <div class="feat-grid">
        <div v-for="f in features" :key="f.key" class="feat-card">
          <div class="feat-card-head">
            <span class="feat-key">{{ keyLabel(f.key) }}</span>
            <n-switch v-model:value="f.enabled" />
          </div>
          <n-form label-placement="top" class="feat-form">
            <n-form-item label="显示名">
              <n-input v-model:value="f.name" placeholder="如 表情生成" />
            </n-form-item>
            <n-form-item label="绑定工作流">
              <n-select
                v-model:value="f.workflow"
                :options="workflowOptions"
                filterable
                clearable
                placeholder="选择已配 prompt_slots 的漫画工作流"
              />
              <span class="form-hint">{{ f.key === 'meme_img' ? '需配 image_node（图生表情包）' : '需配 prompt_slots（表情/漫画）' }}</span>
            </n-form-item>
            <n-form-item label="默认 LoRA（可选，每行一个，名称|权重|0/1）">
              <n-input
                v-model:value="f.default_lora"
                type="textarea"
                :rows="3"
                placeholder="鲸鱼娘_v1|0.8|1"
              />
            </n-form-item>
            <n-form-item label="默认负向提示词（可选）">
              <n-input v-model:value="f.default_negative" type="textarea" :rows="2" placeholder="留空则沿用工作流默认" />
            </n-form-item>
          </n-form>
        </div>
      </div>
    </n-spin>

    <p class="foot-hint">
      提示：旧版「默认表情包工作流(default_comic_workflow)」会在未配置任何功能时自动迁移出一条「表情生成(meme_text)」。
      想完全自定义请在上方三张卡里填好并保存；保存后 special_features 优先于旧配置。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useMessage, NButton, NSpin, NForm, NFormItem, NInput, NSelect, NSwitch } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";

const message = useMessage();
const loading = ref(false);
const saving = ref(false);
const workflows = ref<any[]>([]);

const DEFAULT_FEATURES: Record<string, { name: string; hint: string }> = {
  meme_text: { name: "表情生成", hint: "文生表情包" },
  meme_img: { name: "图生表情包", hint: "图生表情包（需附图）" },
  comic: { name: "漫画生成", hint: "文生漫画（多格）" },
};

// 功能列表：固定三个 key，UI 用同一份顺序渲染
const features = reactive<any[]>([
  { key: "meme_text", name: "", workflow: "", default_lora: "", default_negative: "", enabled: true },
  { key: "meme_img", name: "", workflow: "", default_lora: "", default_negative: "", enabled: true },
  { key: "comic", name: "", workflow: "", default_lora: "", default_negative: "", enabled: true },
]);

const workflowOptions = computed(() =>
  workflows.value.map((w) => ({ label: w.name || "(未命名)", value: w.name || "" }))
);

function keyLabel(k: string): string {
  const m: Record<string, string> = { meme_text: "表情包 (meme_text)", meme_img: "图生表情包 (meme_img)", comic: "漫画 (comic)" };
  return m[k] || k;
}

async function load() {
  loading.value = true;
  try {
    const cfg = await apiGet("config");
    workflows.value = Array.isArray(cfg.workflows) ? cfg.workflows : [];
    const raw = Array.isArray(cfg.special_features) ? cfg.special_features : [];
    // 以 raw 覆盖默认（按 key 对齐），保留三个卡结构
    const byKey: Record<string, any> = {};
    for (const r of raw) {
      const k = (r && r.key) || "";
      if (k) byKey[k] = r;
    }
    for (const f of features) {
      const r = byKey[f.key] || {};
      f.name = r.name || DEFAULT_FEATURES[f.key]?.name || "";
      f.workflow = r.workflow || "";
      f.default_lora = r.default_lora || "";
      f.default_negative = r.default_negative || "";
      f.enabled = r.enabled !== false;
    }
  } catch (e: any) {
    message.error(e.message || "加载功能配置失败");
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  try {
    const payload = features.map((f) => ({
      __template_key: "default",
      key: f.key,
      name: f.name || DEFAULT_FEATURES[f.key]?.name || f.key,
      workflow: (f.workflow || "").trim(),
      default_lora: (f.default_lora || "").trim(),
      default_negative: (f.default_negative || "").trim(),
      enabled: f.enabled !== false,
    }));
    await apiPost("config", { config: { special_features: payload } });
    message.success("功能配置已保存");
  } catch (e: any) {
    message.error(e.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.features-view { height: 100%; overflow: auto; padding: 0 4px; }
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; max-width: 70%; }
.feat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
.feat-card { border: 1px solid var(--border-color); border-radius: 10px; background: var(--bg-panel); padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.feat-card-head { display: flex; align-items: center; justify-content: space-between; }
.feat-key { font-weight: 600; font-size: 15px; }
.feat-form { width: 100%; }
.form-hint { color: var(--text-sub); font-size: 12px; margin-left: 8px; }
.foot-hint { color: var(--text-sub); font-size: 12px; margin-top: 16px; }

@media (max-width: 768px) {
  .view-head p { max-width: 100%; }
  .feat-grid { grid-template-columns: 1fr; }
}
</style>
