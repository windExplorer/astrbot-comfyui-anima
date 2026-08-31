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

    <div class="cfg-scroll">
      <n-alert v-if="saveMsg" :type="saveMsgType" closable @close="saveMsg = ''" style="margin-bottom:16px">
        {{ saveMsg }}
      </n-alert>

      <n-spin :show="loading" style="min-height:200px">
        <div v-if="!loading && !schema" class="empty">未获取到配置结构（schema 为空）</div>

        <n-collapse v-else v-model:expanded-names="expanded" class="cfg-collapse">
          <n-collapse-item v-for="grp in groups" :key="grp.name" :name="grp.name">
            <template #header>
              <div class="grp-head">
                <span class="grp-icon">{{ grp.icon }}</span>
                <span class="grp-title">{{ grp.name }}</span>
                <span class="grp-desc">{{ grp.description }}</span>
              </div>
            </template>
            <div class="grp-body">
              <ConfigSection
                v-for="key in grp.keys.filter((k: string) => schema && schema[k])"
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
import { computed, onMounted, reactive, ref, h } from "vue";
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

// 配置分区元数据（服务器/工作流/LoRA 为同级独立分区）
const GROUP_META = [
  { name: "服务器与模型", description: "ComfyUI 服务器连接配置", icon: "🖥️", keys: ["comfyui_servers"] },
  { name: "工作流列表", description: "各工作流的启用与参数（含封面/底模等）", icon: "🗂️", keys: ["workflows"] },
  { name: "LoRA 列表", description: "LoRA 库的启用与分类", icon: "🧩", keys: ["loras"] },
  { name: "默认工作流", description: "未指定工作流时的默认选择与风格优先级", icon: "🧭", keys: ["default_style_priority", "default_workflow", "default_workflow_real", "default_img2img_workflow", "default_img2img_workflow_real", "img2img_fallback"] },
  { name: "AI 对话与 LLM", description: "AI 对话调用的 LLM 工具开关与专用模型", icon: "🤖", keys: ["enable_llm_tools", "llm_model"] },
  { name: "Anima 翻译", description: "Anima 工作流中文提示词翻译模式与接口", icon: "🌐", keys: ["translator_mode", "translate_llm_model", "translate_api", "danbooru"] },
  { name: "出图行为", description: "出图等待、轮询、webp 转换与小报告等行为", icon: "🖼️", keys: ["draw_timeout", "queue_extra_timeout", "max_draw_timeout", "queue_poll_interval", "return_queue_position", "convert_webp_to_png", "show_draw_report"] },
  { name: "网络与代理", description: "外部网络访问（如 C 站抓取）的代理设置", icon: "🌍", keys: ["http_proxy", "civitai_api_key"] },
  { name: "权限与图库", description: "发图白名单、绘图黑名单、生图次数限制与图片画廊归档", icon: "🔒", keys: ["allow_draw_users", "blacklist", "draw_limit", "gallery"] },
  { name: "分享 WebUI", description: "用户级独立分享 WebUI（/萌绘 指令）", icon: "🔗", keys: ["share_webui"] },
  { name: "剧情模式", description: "剧情模式（仅私聊被动记录）的开关、触发词、白名单与摘要设置", icon: "🎬", keys: ["story_mode"] },
];

const groups = computed(() => {
  const sk = Object.keys(schema.value || {});
  const gkeys: Record<string, boolean> = {};
  GROUP_META.forEach((g) => g.keys.forEach((k) => (gkeys[k] = true)));
  const list = GROUP_META.filter((g) => g.keys.some((k) => sk.includes(k)));
  const leftover = sk.filter((k) => !gkeys[k]);
  if (leftover.length) {
    list.push({ name: "其他", description: "未分区的配置项", icon: "📦", keys: leftover });
  }
  return list;
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
.cfg-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }
.cfg-collapse { background: var(--bg-panel); border-radius: 8px; padding: 8px; }
.grp-head { display: flex; align-items: center; gap: 8px; }
.grp-icon { font-size: 16px; }
.grp-title { font-weight: 600; }
.grp-desc { color: var(--text-sub); font-size: 12px; }
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
  .cfg-scroll { padding-right: 0; }
  .panel { padding: 12px; margin-top: 12px; }
  .tran-debug-row { flex-direction: column; align-items: stretch; gap: 8px; }
  .tran-debug-row :deep(.n-input),
  .tran-debug-row :deep(.n-select) { width: 100% !important; }
}
</style>
