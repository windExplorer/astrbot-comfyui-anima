<template>
  <div class="prompt-panel">
    <!-- 正向提示词 -->
    <div class="pp-head">
      <span class="pp-label">正向提示词</span>
      <span class="pp-count">{{ tagCount }} 个标签</span>
      <div class="pp-ai">
        <n-dropdown
          trigger="click"
          :options="aiOptions"
          @select="runAi"
          placement="bottom-end"
        >
          <n-button size="tiny" type="primary" ghost :loading="aiLoading" :disabled="!llmReady || !positive.trim()">
            ✨ AI 生成
          </n-button>
        </n-dropdown>
        <n-button size="tiny" @click="emit('openTemplates')">📚 模板库</n-button>
        <n-button size="tiny" quaternary @click="emit('update:positive', '')" :disabled="!positive">清空</n-button>
      </div>
    </div>
    <n-input
      :value="positive"
      type="textarea"
      :rows="4"
      :placeholder="positivePlaceholder"
      @update:value="(v: string) => emit('update:positive', v)"
    />

    <!-- 快捷标签（取启用的正向模板前 8 个） -->
    <div v-if="quickTemplates.length" class="pp-chips">
      <span class="chips-label">快捷：</span>
      <n-tag
        v-for="t in quickTemplates"
        :key="t.id"
        size="small"
        round
        :bordered="false"
        class="pp-chip"
        @click="appendTemplate(t)"
      >
        {{ t.name }}
      </n-tag>
    </div>

    <!-- 负向提示词 -->
    <div class="pp-head neg">
      <span class="pp-label">负向提示词</span>
      <n-button size="tiny" quaternary @click="emit('update:negative', '')" :disabled="!negative">清空</n-button>
    </div>
    <n-input
      :value="negative"
      type="textarea"
      :rows="2"
      placeholder="不想要的内容（留空用工作流默认负向）"
      @update:value="(v: string) => emit('update:negative', v)"
    />

    <div v-if="aiError" class="pp-error">{{ aiError }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { studioApi, type StudioTemplate, type StudioWorkflow } from "@/api/studio";
import { useMessage } from "naive-ui";

const props = defineProps<{
  positive: string;
  negative: string;
  llmReady: boolean;
  /** 当前模式（v7.9.1 起只有 t2i / img2img，多参考并入图生图） */
  mode: "t2i" | "img2img";
  /** 已上传的参考图数量（驱动多参考占位提示与 AI 菜单的编辑指令项） */
  refCount: number;
  workflow: StudioWorkflow | null;
  /** 启用中的正向模板（快捷标签） */
  quickTemplates: StudioTemplate[];
  /** 已选 LoRA 名（作为 AI 生成时的参考上下文） */
  selectedLoraNames: string[];
}>();

const emit = defineEmits<{
  (e: "update:positive", v: string): void;
  (e: "update:negative", v: string): void;
  (e: "openTemplates"): void;
}>();

const message = useMessage();
const aiLoading = ref(false);
const aiError = ref("");

const tagCount = computed(() =>
  props.positive.split(/[，,]/).map((s) => s.trim()).filter(Boolean).length
);

const positivePlaceholder = computed(() => {
  if (props.mode === "img2img") {
    // v7.9.1：多参考并入图生图——多张参考图时提示「图N」引用写法
    if (props.refCount > 1) {
      return "描述画面与编辑要求；可用「图1」「图2」指代参考图（如：把图1的裙子换成图2的款式）";
    }
    return "描述想要的改动或画面（参考图会注入工作流；再多放几张可走多参考）";
  }
  if (props.workflow && props.workflow.is_anima) {
    return "英文 Danbooru 风格标签，或用中文描述后点「AI：中文→标签」";
  }
  return "描述你要画的画面（中文英文均可，提交前可让 AI 帮你润色）";
});

const aiOptions = computed(() => {
  const opts: any[] = [
    { key: "expand", label: "扩写细节（保留原标签，补氛围/构图等）" },
    { key: "translate", label: "中文 → Danbooru 标签（整句改写）" },
    { key: "anima", label: "改写为动漫提示词" },
    { key: "natural", label: "改写为自然语言描述（真人向）" },
  ];
  // 编辑指令只对「多张参考图」或「带多参考节点的工作流」有意义
  if (props.mode === "img2img" && (props.refCount > 1 || props.workflow?.multi_image)) {
    opts.push({ key: "qwen_edit", label: "改写为图像编辑指令（多参考）", props: { style: "color: #e86f9c" } });
  }
  return opts;
});

const aiModeHint: Record<string, string> = {
  expand: "已扩写细节",
  translate: "已翻译为标签",
  anima: "已改写为动漫提示词",
  natural: "已改写为自然语言",
  qwen_edit: "已改写为编辑指令",
};

async function runAi(mode: string) {
  const text = props.positive.trim();
  if (!text || aiLoading.value) return;
  aiLoading.value = true;
  aiError.value = "";
  try {
    const loraRef = props.selectedLoraNames.join(", ");
    const d = await studioApi.llmPrompt(mode, text, loraRef);
    if (d && d.text) {
      emit("update:positive", d.text);
      message.success(aiModeHint[mode] || "AI 已生成提示词");
    } else {
      aiError.value = "AI 没有返回内容";
    }
  } catch (e: any) {
    aiError.value = e?.message || String(e);
  } finally {
    aiLoading.value = false;
  }
}

function appendTemplate(t: StudioTemplate) {
  const cur = props.positive.trimEnd();
  const sep = cur && !/[，,\s]$/.test(cur) ? ", " : cur ? " " : "";
  const piece = t.content.trim();
  emit("update:positive", (cur + sep + piece).trim());
}
</script>

<style scoped>
.prompt-panel { width: 100%; display: flex; flex-direction: column; gap: 8px; }
.pp-head { display: flex; align-items: center; gap: 8px; }
.pp-head.neg { margin-top: 2px; }
.pp-label { font-size: 13px; font-weight: 600; color: var(--text-main); }
.pp-count { font-size: 11px; color: var(--text-sub); }
.pp-ai { margin-left: auto; display: flex; gap: 6px; align-items: center; }
.pp-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.chips-label { font-size: 11px; color: var(--text-sub); }
.pp-chip { cursor: pointer; font-size: 11px; }
.pp-chip:hover { background: var(--accent); color: #fff; }
.pp-error {
  font-size: 12px;
  color: #d05050;
  background: color-mix(in srgb, #d05050 8%, transparent);
  border-radius: 8px;
  padding: 6px 10px;
}
</style>
