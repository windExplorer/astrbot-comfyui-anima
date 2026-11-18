<template>
  <div class="prompt-panel">
    <!-- 快捷标签行（单行横向滚动，聊天输入框上沿风格） -->
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

    <n-input
      :value="positive"
      type="textarea"
      :rows="3"
      :placeholder="positivePlaceholder"
      @update:value="(v: string) => emit('update:positive', v)"
    />

    <!-- 工具条：左侧 AI/模板/清空/计数，右侧润色开关+开始生图（聊天窗口式） -->
    <div class="pp-toolbar">
      <div class="pp-left">
        <n-dropdown
          trigger="click"
          :options="aiOptions"
          @select="runAi"
          placement="top-start"
        >
          <n-button size="small" type="primary" ghost :loading="aiLoading" :disabled="!llmReady || !positive.trim()">
            ✨ AI 生成
          </n-button>
        </n-dropdown>
        <n-button size="small" @click="emit('openTemplates')">📚 模板库</n-button>
        <n-button size="small" quaternary @click="emit('update:positive', '')" :disabled="!positive">清空</n-button>
        <span class="pp-count">{{ tagCount }} 个标签</span>
      </div>
      <div class="pp-right">
        <!-- v7.9.6：提交时润色改为提交前的主动开关（默认关）——在线生图的提示词
             都是用户亲手确认的，被动润色反而是多余加工 -->
        <n-tooltip trigger="hover" placement="bottom-end">
          <template #trigger>
            <n-button
              size="small"
              :type="llmRefine ? 'primary' : 'default'"
              :ghost="llmRefine"
              class="pp-refine"
              :class="{ 'pp-refine-off': !llmRefine }"
              @click="emit('update:llmRefine', !llmRefine)"
            >
              🪄 润色
            </n-button>
          </template>
          {{ llmRefine
            ? "润色已开：提交后插件会按工作流风格自动翻译/改写（与聊天侧一致）"
            : "润色已关：直接用输入框里的原文出图。点开则提交后由插件 LLM 再润色一轮" }}
        </n-tooltip>
        <n-button
          type="primary"
          size="large"
          class="pp-submit"
          :disabled="submitDisabled"
          :loading="submitting"
          @click="emit('submit')"
        >
          🎨 开始生图
        </n-button>
      </div>
    </div>

    <div v-if="aiError" class="pp-error">{{ aiError }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { studioApi, type StudioTemplate, type StudioWorkflow } from "@/api/studio";
import { useMessage } from "naive-ui";

const props = defineProps<{
  positive: string;
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
  /** 开始生图禁用态（父组件 genDisabled） */
  submitDisabled: boolean;
  submitting: boolean;
  /** v7.9.6：提交时润色开关（默认关；提交前的主动决定） */
  llmRefine: boolean;
}>();

const emit = defineEmits<{
  (e: "update:positive", v: string): void;
  (e: "openTemplates"): void;
  (e: "submit"): void;
  (e: "update:llmRefine", v: boolean): void;
}>();

const message = useMessage();
const aiLoading = ref(false);
const aiError = ref("");

const tagCount = computed(() =>
  props.positive.split(/[，,]/).map((s) => s.trim()).filter(Boolean).length
);

const positivePlaceholder = computed(() => {
  if (props.mode === "img2img") {
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
.pp-chips {
  display: flex;
  flex-wrap: nowrap;
  gap: 6px;
  align-items: center;
  overflow-x: auto;
  padding-bottom: 2px;
}
.chips-label { font-size: 11px; color: var(--text-sub); flex: none; }
.pp-chip { cursor: pointer; font-size: 11px; flex: none; }
.pp-chip:hover { background: var(--accent); color: #fff; }
.pp-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
}
.pp-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.pp-right { display: flex; align-items: center; gap: 10px; flex: none; }
.pp-refine-off { color: var(--text-sub); }
.pp-count { font-size: 11px; color: var(--text-sub); }
.pp-submit { flex: none; min-width: 140px; }
.pp-error {
  font-size: 12px;
  color: #d05050;
  background: color-mix(in srgb, #d05050 8%, transparent);
  border-radius: 8px;
  padding: 6px 10px;
}
</style>
