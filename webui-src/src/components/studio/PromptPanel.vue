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
        <!-- v7.9.10：🎲 AI 想画面——按当前工作流底模的提示词格式自由创作一幅画面；
             输入框有内容则作为灵感参考，留空则 AI 完全自由发挥 -->
        <n-tooltip trigger="hover" placement="bottom-end">
          <template #trigger>
            <n-button
              size="small"
              ghost
              class="pp-imagine"
              :loading="imagining"
              :disabled="!llmReady || aiLoading || refining"
              @click="runImagine"
            >
              🎲 想画面
            </n-button>
          </template>
          AI 想画面：按当前工作流底模的提示词格式自由创作一幅画面（纯风景 / 单人 /
          多角色 / 动漫或游戏角色 / 风景城市等等）。输入框有内容时作为灵感参考，
          留空则完全自由发挥
        </n-tooltip>
        <!-- v7.10.3：英文提示词才有译文（生成时一次 LLM 顺带产出），点击查看不额外调接口 -->
        <n-button
          v-if="canShowImagineTranslation"
          size="small"
          quaternary
          @click="imagineZhShow = true"
        >
          🇨🇳 看译文
        </n-button>
        <!-- v7.9.7：润色是即用即点的动作按钮——点一下就按当前工作流风格自动选方式
             （扩写/翻译/改写/编辑指令）就地改写输入框文本，无任何隐藏的提交后加工 -->
        <n-tooltip trigger="hover" placement="bottom-end">
          <template #trigger>
            <n-button
              size="small"
              type="primary"
              ghost
              class="pp-refine"
              :loading="refining"
              :disabled="!llmReady || !positive.trim() || aiLoading || imagining"
              @click="runAutoPolish"
            >
              🪄 润色
            </n-button>
          </template>
          一键润色：按当前工作流风格自动选择润色方式（扩写 / 翻译 / 改写 / 编辑指令），
          就地改写输入框文本
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

    <!-- v7.10.3：想画面译文（与提示词同一次 LLM 产出，点按钮查看，不再调翻译接口） -->
    <n-modal
      :show="imagineZhShow"
      preset="card"
      title="画面提示词中文译文"
      :bordered="false"
      style="width: min(640px, 92vw)"
      :mask-closable="true"
      @update:show="(v: boolean) => (imagineZhShow = v)"
    >
      <p class="pp-zh-hint">这是「🎲 想画面」生成英文提示词时一并产出的译文，查看不再消耗翻译额度：</p>
      <div class="pp-zh-label">原英文提示词</div>
      <div class="pp-zh-en">{{ imaginedPrompt }}</div>
      <div class="pp-zh-label pp-zh-label-zh">中文译文</div>
      <div class="pp-zh-text">{{ imaginedTranslation }}</div>
    </n-modal>
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
}>();

const emit = defineEmits<{
  (e: "update:positive", v: string): void;
  (e: "openTemplates"): void;
  (e: "submit"): void;
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

// v7.9.7：🪄 一键润色（即用即点）——按当前工作流风格自动选润色方式，就地改写输入框文本
const refining = ref(false);

// v7.9.10：🎲 AI 想画面——按当前工作流底模的提示词格式自由创作一幅画面
const imagining = ref(false);
// v7.10.3：同一次 LLM 顺带产出的译文；提示词被用户改动后按钮自动隐藏，避免展示过期译文
const imaginedPrompt = ref("");
const imaginedTranslation = ref("");
const imagineZhShow = ref(false);
const canShowImagineTranslation = computed(
  () => !!imaginedTranslation.value && imaginedPrompt.value === props.positive.trim()
);

async function runImagine() {
  if (imagining.value) return;
  imagining.value = true;
  aiError.value = "";
  try {
    const d = await studioApi.aiImagine(props.positive.trim(), props.workflow?.name || "");
    if (d && d.text) {
      emit("update:positive", d.text);
      imaginedPrompt.value = d.text.trim();
      imaginedTranslation.value = (d.translation || "").trim();
      message.success(
        imaginedTranslation.value
          ? "AI 已想好一个画面；点「看译文」可查看中文意思"
          : "AI 已想好一个画面，可继续修改或直接开始生图"
      );
    } else {
      aiError.value = "AI 没有返回内容";
    }
  } catch (e: any) {
    aiError.value = e?.message || String(e);
  } finally {
    imagining.value = false;
  }
}

function autoPolishMode(): string {
  // 多参考编辑类工作流 → 编辑指令；动漫/danbooru 底模 → 中文翻译为标签、英文标签扩写；
  // 其余（真人向/未知）→ 中文改写为自然语言、英文标签扩写
  if (props.mode === "img2img" && (props.refCount > 1 || props.workflow?.multi_image)) {
    return "qwen_edit";
  }
  const hasCJK = /[\u4e00-\u9fff]/.test(props.positive);
  if (props.workflow?.is_anima) {
    return hasCJK ? "translate" : "expand";
  }
  return hasCJK ? "natural" : "expand";
}

async function runAutoPolish() {
  const text = props.positive.trim();
  if (!text || refining.value) return;
  refining.value = true;
  aiError.value = "";
  const mode = autoPolishMode();
  try {
    const loraRef = props.selectedLoraNames.join(", ");
    const d = await studioApi.llmPrompt(mode, text, loraRef);
    if (d && d.text) {
      emit("update:positive", d.text);
      message.success(`已润色（${aiModeHint[mode] || "AI 润色"}）`);
    } else {
      aiError.value = "AI 没有返回内容";
    }
  } catch (e: any) {
    aiError.value = e?.message || String(e);
  } finally {
    refining.value = false;
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
.pp-count { font-size: 11px; color: var(--text-sub); }
.pp-submit { flex: none; min-width: 140px; }
.pp-error {
  font-size: 12px;
  color: #d05050;
  background: color-mix(in srgb, #d05050 8%, transparent);
  border-radius: 8px;
  padding: 6px 10px;
}
/* v7.10.3：想画面译文弹窗 */
.pp-zh-hint { margin: 0 0 14px; font-size: 12px; color: var(--text-sub); line-height: 1.6; }
.pp-zh-label { font-size: 12px; font-weight: 600; color: var(--text-sub); margin-bottom: 6px; }
.pp-zh-label-zh { margin-top: 16px; color: var(--accent); }
.pp-zh-en, .pp-zh-text {
  max-height: 220px; overflow: auto; padding: 12px 14px; border-radius: 10px;
  background: color-mix(in srgb, var(--accent) 5%, var(--bg-panel));
  border: 1px solid var(--border-color);
  font-size: 13px; line-height: 1.75; white-space: pre-wrap; word-break: break-word;
  user-select: text;
}
.pp-zh-en { color: var(--text-sub); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.pp-zh-text { color: var(--text-main); font-size: 14px; }
</style>
