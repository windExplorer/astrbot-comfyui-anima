<template>
  <div class="workflows-view">
    <div class="view-head">
      <div>
        <h2>工作流</h2>
        <p>卡片式查看工作流：名称、别名、底模、服务器、是否 Anima；可编辑、查看封面与 LoRA 详情。</p>
      </div>
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="view-actions">
          <n-button :loading="loading" @click="load">刷新</n-button>
          <n-button type="primary" @click="addWorkflow">＋ 新增工作流</n-button>
        </div>
      </Teleport>
    </div>

    <!-- 星标筛选 + 排序（v6.2.0）：工作流多了以后按需挑常用的 -->
    <div class="filter-bar">
      <n-checkbox v-model:checked="starOnly" size="small">★ 仅看星标</n-checkbox>
      <span class="filter-hint">星标 {{ starredCount }} / {{ workflows.length }}</span>
      <n-select
        v-model:value="sortField"
        size="small"
        style="width: 150px"
        :options="sortFieldOptions"
      />
      <n-select
        v-model:value="sortOrder"
        size="small"
        style="width: 110px"
        :options="sortOrderOptions"
      />
      <n-button size="small" quaternary @click="resetSort">重置排序</n-button>
      <span class="filter-hint">创建时间＝列表顺序（新加的在最前）；「更新时间」按最后一次编辑</span>
    </div>

    <!-- 关键词搜索：名称 / 别名 / 底模 / 服务器 / 工作流文件名 / 默认 LoRA；前端即时过滤，与类型筛选叠加生效 -->
    <div class="filter-bar">
      <n-input
        v-model:value="searchText"
        size="small"
        clearable
        class="filter-search"
        placeholder="搜索名称 / 别名 / 底模 / 服务器 / 工作流文件…"
      />
      <span v-if="searchText.trim()" class="filter-hint">
        匹配 {{ filteredWorkflows.length }} / {{ workflows.length }} 条
      </span>
    </div>

    <div class="wf-scroll">
    <n-spin :show="loading">
      <n-empty
        v-if="!loading && !filteredWorkflows.length"
        :description="searchText.trim() ? `没有匹配「${searchText.trim()}」的工作流。` : '没有符合筛选条件的工作流。'"
        style="padding:60px"
      />
      <div v-else class="card-grid">
        <div
          v-for="({ w, i }, _) in filteredWorkflows"
          :key="i"
          class="wf-card"
          :class="{ 'is-star': w.starred === true }"
        >
          <div
            class="card-cover"
            :class="{ 'is-drag': coverDragIdx === i }"
            @click="openImage(i)"
            @dragover.prevent="coverDragIdx = i"
            @dragleave.prevent="coverDragIdx = -1"
            @drop.prevent="onDropCover(i, $event)"
          >
            <img v-if="w.image" v-cover-lazy="w.image" alt="" loading="lazy" />
            <div v-else class="cover-empty">无封面</div>
            <span class="cover-drop-tip">松开设置封面</span>
          </div>
          <div class="card-head">
            <span
              class="card-star"
              :class="{ on: w.starred === true }"
              :title="w.starred === true ? '取消星标' : '加星标（可筛选）'"
              @click.stop="toggleStar(i)"
            >{{ w.starred === true ? "★" : "☆" }}</span>
            <span class="card-title">{{ w.name || "(未命名)" }}</span>
            <n-tag v-if="w.enabled === false" size="small" type="error" :bordered="false">已停用</n-tag>
            <n-tag v-if="w.is_anima" size="small" type="info" :bordered="false">Anima</n-tag>
            <n-tag v-if="(w.image_node || '').trim()" size="small" type="success" :bordered="false">图生图</n-tag>
            <n-tag v-else size="small" type="default" :bordered="false">文生图</n-tag>
            <n-tag v-if="(w.base_id || '').trim()" size="small" type="warning" :bordered="false">v7</n-tag>
            <n-tag v-else size="small" type="default" :bordered="false">旧版</n-tag>
          </div>
          <div class="card-alias">别名：{{ aliasStr(w.aliases) }}</div>
          <div class="card-meta">
            <n-tag size="tiny" :bordered="false">{{ w.base_model?.trim() || "不限底模" }}</n-tag>
            <span class="meta-item">{{ w.server_name?.trim() || "默认服务器" }}</span>
            <span v-if="w.workflow_name" class="meta-item">{{ w.workflow_name }}</span>
            <a v-if="w.civitai_url" :href="w.civitai_url" target="_blank" rel="noopener noreferrer" class="civ-link">C站 ↗</a>
          </div>
          <div class="card-loracfg">{{ (w.loras_text || "").trim() ? "已配默认 LoRA" : "未配默认 LoRA" }}</div>
          <div class="card-actions">
            <n-button size="tiny" @click="editWorkflow(i)">编辑</n-button>
            <n-button size="tiny" @click="toggleEnabled(i)">{{ w.enabled === false ? "启用" : "停用" }}</n-button>
            <n-button size="tiny" @click="copyWorkflow(i)">复制</n-button>
            <n-button size="tiny" @click="fetchCover(i)">抓封面</n-button>
            <n-button size="tiny" @click="openCoverEditor(i)">传封面</n-button>
            <n-button size="tiny" type="error" @click="removeWorkflow(i)">删除</n-button>
          </div>
        </div>
      </div>
    </n-spin>
    </div>

    <!-- 大图预览 -->
    <!-- 大图详情（全屏：左侧封面，右侧字段信息） -->
    <ItemViewer v-model:show="previewShow" :images="coverImages" :index="coverIndex" @nav="onCoverNav" />
    <!-- 抓取封面选择（多张候选时弹出） -->
    <CoverPicker v-model:show="coverPickShow" :covers="coverPickCovers" :title="coverPickTitle" @pick="onCoverPick" />
    <CoverEditor v-model:show="coverEditorShow" :title="coverEditorTitle" @confirm="onCoverConfirm" />

    <!-- 编辑弹窗 -->
    <n-modal v-model:show="editShow" preset="card" :title="editTitle" class="wf-modal" :bordered="false">
      <n-form label-placement="top" :label-width="0" class="edit-form">
        <div class="form-grid">
          <n-form-item label="名称"><n-input v-model:value="editForm.name" placeholder="如 sd" /></n-form-item>
          <n-form-item label="底模">
            <n-select v-model:value="editForm.base_model" :options="baseModelOptions" />
          </n-form-item>
        </div>
        <n-form-item label="别名（逗号/换行分隔）"><n-input v-model:value="editForm.aliases" type="textarea" :rows="2" /></n-form-item>
        <div class="form-grid">
          <n-form-item label="绑定服务器"><n-input v-model:value="editForm.server_name" placeholder="如 server1" /></n-form-item>
          <n-form-item label="工作流文件名"><n-input v-model:value="editForm.workflow_name" placeholder="如 sd.json" /></n-form-item>
        </div>
        <n-form-item label="Anima 工作流">
          <n-switch v-model:value="editForm.is_anima" />
          <span class="form-hint">开启后中文提示词会先翻译为 Danbooru 标签</span>
        </n-form-item>
        <n-form-item label="工作流类型">
          <span class="form-hint">普通生图 / 图生图（旧版表情包/漫画功能已在 v7.0.0 移除）</span>
        </n-form-item>
        <n-form-item label="启用该工作流">
          <n-switch v-model:value="editForm.enabled" />
          <span class="form-hint">关闭后不可使用：显式指定会提示「已停用」，自动选择默认工作流也会跳过它</span>
        </n-form-item>
        <n-form-item label="锁定提示词（无需用户传词）">
          <n-switch
            v-model:value="editForm.require_prompt"
            :checked-value="false"
            :unchecked-value="true"
          />
          <span class="form-hint">开启后该工作流无需提示词即可出图（如「动漫转真人」，只需传图/引用图）；用户传了提示词也会被忽略</span>
        </n-form-item>
        <div class="form-grid">
          <n-form-item label="固定正向提示词（可选）"><n-input v-model:value="editForm.default_positive" type="textarea" :rows="2" placeholder="锁定提示词时用此提示词覆盖工作流 JSON；未锁定时用户不传词也会兜底；不走翻译/改写" /></n-form-item>
          <n-form-item label="固定负向提示词（可选）"><n-input v-model:value="editForm.default_negative" type="textarea" :rows="2" placeholder="用户未传负向提示词时，用此覆盖工作流 JSON 内的负向提示词" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="C 站链接（抓封面）"><n-input v-model:value="editForm.civitai_url" placeholder="https://civitai.com/models/xxx" /></n-form-item>
          <n-form-item label="封面图文件名"><n-input v-model:value="editForm.image" placeholder="存于 lora_assets/，可抓取或上传" /></n-form-item>
        </div>

        <n-divider style="margin:8px 0">── 基础工作流 (v7.0.0) ──</n-divider>
        <n-form-item label="基础工作流">
          <n-select
            v-model:value="editForm.base_id"
            :options="baseWfOptions"
            filterable
            clearable
            placeholder="不使用（旧版：按下方节点配置 + 工作流文件运行）"
          />
        </n-form-item>
        <template v-if="(editForm.base_id || '').trim()">
          <div class="form-hint" style="margin: 0 0 8px; margin-left: 0">
            本模式下节点全部按解析自动定位，下方「节点配置」自动忽略（保留仅作参考）。
          </div>
          <n-form-item label="LLM 注入说明（用本工作流出图时告知 LLM 的用法；留空不注入）">
            <n-input v-model:value="editForm.llm_notes" type="textarea" :rows="2" placeholder="如：本工作流专画头像，用户要头像时优先选用" />
          </n-form-item>
          <div class="form-grid">
            <n-form-item label="固定种子（留空=随机）">
              <n-input v-model:value="editForm.fixed_seed" placeholder="数字；用户显式指定种子时以用户为准" />
            </n-form-item>
            <n-form-item label="保存格式（留空=跟随基础工作流）">
              <n-select v-model:value="editForm.save_format" :options="saveFormatOptions" />
            </n-form-item>
          </div>
          <div class="form-grid">
            <n-form-item label="保存质量 %（留空=跟随）">
              <n-input v-model:value="editForm.save_quality" placeholder="1-100，仅 webp/jpg 有意义" />
            </n-form-item>
            <n-form-item label="放大模式">
              <n-select v-model:value="editForm.upscale_mode" :options="upscaleModeOptions" />
            </n-form-item>
          </div>
          <div class="form-grid" v-if="editForm.upscale_mode === 'inject'">
            <n-form-item label="注入放大模型名">
              <n-input v-model:value="editForm.upscale_model_name" placeholder="如 4x-UltraSharp.pth" />
            </n-form-item>
            <n-form-item label="清理显存">
              <n-select v-model:value="editForm.cleanup_mode" :options="cleanupModeOptions" />
            </n-form-item>
          </div>
          <div class="form-grid">
            <n-form-item label="步数（留空=跟随）"><n-input v-model:value="editForm.ov_steps" /></n-form-item>
            <n-form-item label="CFG（留空=跟随）"><n-input v-model:value="editForm.ov_cfg" /></n-form-item>
            <n-form-item label="采样器（可搜索/可手输）">
              <n-select v-model:value="editForm.ov_sampler" :options="samplerOptions" filterable tag clearable placeholder="留空=跟随基础工作流" />
            </n-form-item>
            <n-form-item label="调度器（可搜索/可手输）">
              <n-select v-model:value="editForm.ov_scheduler" :options="schedulerOptions" filterable tag clearable placeholder="留空=跟随基础工作流" />
            </n-form-item>
            <n-form-item v-if="selectedBaseKind === 'img2img'" label="噪点 denoise（留空=跟随）">
              <n-input v-model:value="editForm.ov_denoise" />
            </n-form-item>
          </div>
          <n-form-item v-if="builtinLoras.length" label="基础工作流内置 LoRA（不可删除，可禁用）">
            <div style="width:100%; display:flex; flex-direction:column; gap:6px">
              <div v-for="b in builtinLoras" :key="b.node" style="display:flex; align-items:center; gap:8px">
                <n-tag size="small" :bordered="false">{{ b.name || b.node }}</n-tag>
                <n-switch
                  size="small"
                  :value="!isBuiltinDisabled(b)"
                  @update:value="(v: boolean) => toggleBuiltin(b, !v)"
                />
                <span class="form-hint">{{ isBuiltinDisabled(b) ? "已禁用（强度 0）" : "启用" }}</span>
              </div>
            </div>
          </n-form-item>
        </template>

        <n-divider style="margin:8px 0">── 节点配置 ──</n-divider>
        <div class="form-grid">
          <n-form-item label="正提示词节点"><n-input v-model:value="editForm.positive_node" placeholder="如 6" /></n-form-item>
          <n-form-item label="负提示词节点"><n-input v-model:value="editForm.negative_node" placeholder="如 7" /></n-form-item>
          <n-form-item label="正向输入框名"><n-input v-model:value="editForm.positive_field" placeholder="留空默认 text；Qwen 系填 prompt" /></n-form-item>
          <n-form-item label="负向输入框名"><n-input v-model:value="editForm.negative_field" placeholder="留空默认 text；Qwen 系填 negative_prompt" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="分辨率节点"><n-input v-model:value="editForm.resolution_node" placeholder="EmptyLatentImage，可留空自动探测" /></n-form-item>
          <n-form-item label="输出节点"><n-input v-model:value="editForm.output_node" placeholder="出图节点（可选）" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="宽度字段"><n-input v-model:value="editForm.resolution_width_field" placeholder="width" /></n-form-item>
          <n-form-item label="高度字段"><n-input v-model:value="editForm.resolution_height_field" placeholder="height" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="默认宽度"><n-input-number v-model:value="editForm.default_width" style="width:100%" /></n-form-item>
          <n-form-item label="默认高度"><n-input-number v-model:value="editForm.default_height" style="width:100%" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="允许的最大宽度（留空=不限制）">
            <n-input v-model:value="editForm.max_width" placeholder="如 1536；超限按比例缩小" />
          </n-form-item>
          <n-form-item label="允许的最大高度（留空=不限制）">
            <n-input v-model:value="editForm.max_height" placeholder="如 2048；超限按比例缩小" />
          </n-form-item>
        </div>
        <n-form-item label="禁止改变默认宽高">
          <n-switch v-model:value="editForm.lock_size" />
          <span class="form-hint">开启后忽略用户传参与比例关键词，恒用默认宽高（图生图不适用）</span>
        </n-form-item>
        <n-form-item label="宽高注入范围">
          <n-space vertical :size="4" style="width:100%">
            <n-select v-model:value="editForm.resolution_mode" :options="resolutionModeOptions" style="width:100%" />
            <span class="form-hint">single=只改上方「分辨率节点」（留空则自动探测第一个 EmptyLatentImage），与旧行为一致；all=改<b>所有</b> EmptyLatentImage —— 多 latent 串联工作流必须选它，否则前后 latent 尺寸不一致、构图被拉伸；none=完全不改，沿用工作流 JSON 原始尺寸（也可用来避开默认宽高的兜底值）</span>
          </n-space>
        </n-form-item>
        <div class="form-grid">
          <n-form-item label="参考图节点"><n-input v-model:value="editForm.image_node" placeholder="图生图 LoadImage（可选）" /></n-form-item>
          <n-form-item label="主模节点（lora_anchor）"><n-input v-model:value="editForm.lora_anchor" placeholder="CheckpointLoader/UNETLoader 键名，留空自动探测" /></n-form-item>
        </div>
        <div class="form-grid">
          <n-form-item label="放大模型节点"><n-input v-model:value="editForm.upscale_node_id" placeholder="放大模型加载节点键名（如 14）" /></n-form-item>
          <n-form-item label="放大模型名称"><n-input v-model:value="editForm.upscale_model_name" placeholder="替换成的放大模型文件名（如 4x-UltraSharp.pth）" /></n-form-item>
        </div>
        <n-form-item label="CLIP 节点（lora_clip）"><n-input v-model:value="editForm.lora_clip" placeholder="完整模式用，CLIPLoader 键名，留空自动探测" /></n-form-item>

        <n-divider style="margin:8px 0">── 采样器参数（steps / cfg / denoise）──</n-divider>
        <n-form-item label="从工作流文件读取">
          <n-space vertical :size="6" style="width:100%">
            <n-button size="tiny" :loading="samplerLoading" @click="fetchSamplerParams">↻ 读取文件中的采样器参数</n-button>
            <span v-if="samplerHint" class="form-hint" style="color:#c2255c">{{ samplerHint }}</span>
            <span v-else class="form-hint">根据上方「工作流文件名」读取文件采样器节点的默认 steps / cfg / denoise，自动填入下方字段</span>
          </n-space>
        </n-form-item>
        <div class="form-grid">
          <n-form-item label="默认 steps">
            <n-space vertical :size="4" style="width:100%">
              <n-input-number v-model:value="editForm.default_steps" :min="0" :max="200" :step="1" :disabled="editForm.steps_off" style="width:100%" />
              <n-checkbox v-model:checked="editForm.steps_off">不注入（沿用工作流原值）</n-checkbox>
            </n-space>
          </n-form-item>
          <n-form-item label="默认 CFG">
            <n-space vertical :size="4" style="width:100%">
              <n-input-number v-model:value="editForm.default_cfg" :min="0" :max="30" :step="0.5" :precision="2" :disabled="editForm.cfg_off" style="width:100%" />
              <n-checkbox v-model:checked="editForm.cfg_off">不注入（沿用工作流原值）</n-checkbox>
            </n-space>
          </n-form-item>
        </div>
        <n-form-item label="默认 denoise">
          <n-space vertical :size="4" style="width:100%">
            <n-input-number v-model:value="editForm.default_denoise" :min="-1" :max="1" :step="0.05" :precision="2" :disabled="editForm.denoise_off" style="width:100%" />
            <n-checkbox v-model:checked="editForm.denoise_off">不注入（-1，沿用工作流原始值）</n-checkbox>
          </n-space>
        </n-form-item>
        <n-form-item label="工作流 JSON（可直接粘贴）"><n-input v-model:value="editForm.workflow_json" type="textarea" :rows="3" /></n-form-item>
        <n-form-item label="默认 LoRA">
          <div class="lora-list">
            <div v-for="(row, ri) in (editForm.loraList || [])" :key="ri" class="lora-row">
              <n-select
                v-model:value="row.name"
                :options="loraOptions"
                filterable
                clearable
                placeholder="选择 LoRA（可搜索）"
                style="flex: 1; min-width: 120px"
              />
              <n-input-number
                v-model:value="row.weight"
                :min="0"
                :max="2"
                :step="0.05"
                :precision="2"
                placeholder="权重"
                style="width: 110px"
              />
              <n-switch v-model:value="row.enabled" size="small">
                <template #checked>启用</template>
                <template #unchecked>停用</template>
              </n-switch>
              <n-button size="tiny" quaternary type="error" @click="removeLoraRow(ri)">删除</n-button>
            </div>
            <n-space style="margin-top: 6px">
              <n-button size="tiny" @click="addLoraRow">＋ 添加 LoRA</n-button>
              <n-button size="tiny" @click="refreshLoras">↻ 刷新 LoRA 列表</n-button>
            </n-space>
            <div class="form-hint">从全局 LoRA 库下拉选择（可搜索）；保存后写回 loras_text（名称|权重|0/1）</div>
          </div>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editShow = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveEdit">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useMessage, useDialog, NButton, NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NSwitch, NTag, NSpace, NDivider, NEmpty, NSpin, NCheckbox, NRadioGroup, NRadioButton } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import { lsGet, lsSet } from "@/api/storage";
import { parseAliases, truncate } from "@/utils/format";
import { useRefresh } from "@/composables/useRefresh";
import { useDevice } from "@/composables/useDevice";
import ItemViewer, { type ItemViewerField } from "@/components/ItemViewer.vue";
import CoverPicker from "@/components/CoverPicker.vue";
import CoverEditor from "@/components/CoverEditor.vue";
import { useCover } from "@/composables/useCover";

const message = useMessage();
const dialog = useDialog();
const { isMobile } = useDevice();
const loading = ref(false);
const saving = ref(false);
const workflows = ref<any[]>([]);
const loras = ref<any[]>([]);

const baseModelOptions = ["", "anima", "z-image-turbo", "krea2", "illustrious"].map((o) => ({ label: o || "（通用）", value: o }));

// ---- 基础工作流（v7.0.0） ----
const baseWfs = ref<any[]>([]);
const baseWfOptions = computed(() => [
  { label: "不使用（旧版模式）", value: "" },
  ...baseWfs.value.map((b) => ({ label: b.name || b.file_name || `#${b.id}`, value: String(b.id) })),
]);
const selectedBase = computed(() =>
  baseWfs.value.find((b) => String(b.id) === String(editForm.base_id || "")) || null
);
const selectedBaseKind = computed(() => selectedBase.value?.roles?.kind || "");
const builtinLoras = computed<any[]>(() => selectedBase.value?.roles?.builtin_loras || []);
const saveFormatOptions = [
  { label: "跟随基础工作流", value: "" },
  { label: ".webp", value: ".webp" },
  { label: ".png", value: ".png" },
  { label: ".jpg", value: ".jpg" },
];
const upscaleModeOptions = [
  { label: "跟随基础工作流（有内置=沿用，可换模型）", value: "" },
  { label: "绕过内置放大链", value: "bypass" },
  { label: "注入放大链（基础图无放大链时）", value: "inject" },
];
const cleanupModeOptions = [
  { label: "跟随基础工作流", value: "" },
  { label: "注入清理显存节点", value: "inject" },
];
// ComfyUI 最新版采样器/调度器清单（下拉可搜索、可手输自定义值）
const SAMPLER_OPTIONS = [
  "euler", "euler_cfg_pp", "euler_ancestral", "euler_ancestral_cfg_pp", "heun", "heunpp2",
  "dpm_2", "dpm_2_ancestral", "lms", "dpm_fast", "dpm_adaptive",
  "dpmpp_2s_ancestral", "dpmpp_sde", "dpmpp_sde_gpu", "dpmpp_2m", "dpmpp_2m_sde",
  "dpmpp_2m_sde_gpu", "dpmpp_3m_sde", "dpmpp_3m_sde_gpu", "ddpm", "lcm",
  "ipndm", "ipndm_v", "deis", "ddim", "uni_pc", "uni_pc_bh2",
];
const SCHEDULER_OPTIONS = [
  "normal", "karras", "exponential", "sgm_uniform", "simple",
  "ddim_uniform", "beta", "linear_quadratic", "kl_optimal",
];
const samplerOptions = SAMPLER_OPTIONS.map((s) => ({ label: s, value: s }));
const schedulerOptions = SCHEDULER_OPTIONS.map((s) => ({ label: s, value: s }));

function isBuiltinDisabled(b: any): boolean {
  const list: string[] = Array.isArray(editForm.disable_builtin_loras) ? editForm.disable_builtin_loras : [];
  return list.includes(String(b.node));
}
function toggleBuiltin(b: any, disabled: boolean) {
  const list: string[] = Array.isArray(editForm.disable_builtin_loras) ? [...editForm.disable_builtin_loras] : [];
  const key = String(b.node);
  const idx = list.indexOf(key);
  if (disabled && idx < 0) list.push(key);
  if (!disabled && idx >= 0) list.splice(idx, 1);
  editForm.disable_builtin_loras = list;
}

async function loadBaseWfs() {
  try {
    const d = await apiGet("baseworkflows");
    baseWfs.value = Array.isArray(d?.items) ? d.items : [];
  } catch {
    baseWfs.value = [];
  }
}

async function load() {
  loading.value = true;
  try {
    const cfg = await apiGet("config");
    workflows.value = Array.isArray(cfg.workflows) ? cfg.workflows : [];
    loras.value = Array.isArray(cfg.loras) ? cfg.loras : [];
  } catch (e: any) {
    message.error(e.message || "加载工作流失败");
  } finally {
    loading.value = false;
  }
  loadBaseWfs();
}

function aliasStr(raw: string): string {
  const a = parseAliases(raw);
  return a.length ? a.join(" / ") : "—";
}

// ── LoRA 图形化：与后端 _parse_loras_text / _serialize_loras_text 格式保持一致 ──
type LoraRow = { name: string; weight: number; enabled: boolean };

function parseLorasText(text: string): LoraRow[] {
  const out: LoraRow[] = [];
  for (const line of (text || "").split("\n")) {
    const s = line.trim();
    if (!s || s.startsWith("#")) continue;
    const parts = s.split("|").map((p) => p.trim());
    const name = parts[0] || "";
    if (!name) continue;
    const rawW = parseFloat(parts[1] || "");
    const rawE = (parts[2] || "").toLowerCase();
    const enabled = !(rawE && ["0", "0.0", "false", "禁用", "关"].includes(rawE));
    out.push({ name, weight: isNaN(rawW) ? 1.0 : rawW, enabled });
  }
  return out;
}

function serializeLorasText(list: LoraRow[] | undefined | null): string {
  return (list || [])
    .filter((l) => l && (l.name || "").trim())
    .map((l) => {
      const w = Number(l.weight);
      return `${(l.name || "").trim()}|${!isNaN(w) ? w : 1.0}|${l.enabled ? 1 : 0}`;
    })
    .join("\n");
}

// 关键词搜索（工作流越来越多，靠翻页找太慢）：匹配名称、别名、底模、绑定服务器、
// 工作流文件名、默认 LoRA 文本。前端即时过滤，与类型筛选叠加生效。
const searchText = ref("");
// 单条工作流是否命中搜索词（空词视为全命中）
function wfMatches(w: any, kw: string): boolean {
  if (!kw) return true;
  const hay = [w.name, w.aliases, w.base_model, w.server_name, w.workflow_name, w.loras_text]
    .map((v) => (Array.isArray(v) ? v.join(" ") : String(v ?? "")))
    .join(" ")
    .toLowerCase();
  return hay.includes(kw);
}
// ── 星标与排序（v6.2.0）────────────────────────────────────────────
// 星标存在工作流条目的 starred 字段；「更新时间」用 updated_at（毫秒），保存编辑时打点。
// 「创建时间」不存字段：数组顺序即创建顺序（新建是 unshift 到最前 → 索引越小越新），
// 这样老配置无需迁移就能按创建排序。
const wxLs = { star: "anima_wf_star_only", field: "anima_wf_sort_field", order: "anima_wf_sort_order" };
const starOnly = ref(lsGet(wxLs.star) === "1");
const sortField = ref<"created" | "updated" | "name">(
  (["created", "updated", "name"] as const).includes(lsGet(wxLs.field) as any)
    ? (lsGet(wxLs.field) as "created" | "updated" | "name")
    : "created",
);
const sortOrder = ref<"desc" | "asc">(lsGet(wxLs.order) === "asc" ? "asc" : "desc"); // 默认倒序
watch([starOnly, sortField, sortOrder], () => {
  lsSet(wxLs.star, starOnly.value ? "1" : "0");
  lsSet(wxLs.field, sortField.value);
  lsSet(wxLs.order, sortOrder.value);
});

const sortFieldOptions = [
  { label: "按创建时间", value: "created" },
  { label: "按更新时间", value: "updated" },
  { label: "按名称", value: "name" },
];
const sortOrderOptions = [
  { label: "倒序（新→旧）", value: "desc" },
  { label: "正序（旧→新）", value: "asc" },
];
const starredCount = computed(() => workflows.value.filter((w) => w.starred === true).length);

function resetSort() {
  sortField.value = "created";
  sortOrder.value = "desc";
}

/** 星标开关：直接写回 config（与启用/停用同一套保存方式）。 */
async function toggleStar(idx: number) {
  const w = workflows.value[idx];
  if (!w) return;
  const next = w.starred !== true;
  w.starred = next;
  try {
    await apiPost("config", { config: { workflows: workflows.value } });
  } catch (e: any) {
    w.starred = !next; // 失败回滚
    message.error(e.message || "保存星标失败");
  }
}

const filteredWorkflows = computed(() => {
  const kw = searchText.value.trim().toLowerCase();
  const rows = workflows.value
    .map((w, i) => ({ w, i }))
    .filter(({ w }) => {
      // 星标筛选
      if (starOnly.value && w.starred !== true) return false;
      // 关键词搜索
      return wfMatches(w, kw);
    });
  // 排序：dir=-1 表示倒序（默认）。同值一律回落到「创建顺序（新的在前）」保证稳定。
  const dir = sortOrder.value === "asc" ? 1 : -1;
  rows.sort((x, y) => {
    let d = 0;
    if (sortField.value === "name") {
      d = String(x.w.name || "").localeCompare(String(y.w.name || ""), "zh-Hans-CN");
    } else if (sortField.value === "updated") {
      d = Number(x.w.updated_at || 0) - Number(y.w.updated_at || 0);
    } else {
      d = y.i - x.i; // 创建：索引越小越新 → 「越新越大」
    }
    if (d === 0) d = y.i - x.i;
    return dir * d;
  });
  return rows;
});

// LoRA 下拉选项：按工作流底模筛选（与 availLoras 逻辑一致：底模为空则全部，LoRA 底模为空则通用）
// + 已选但库中不存在/底模不匹配的名称（保留老配置可编辑，标记未知）
const loraOptions = computed(() => {
  const known = new Set<string>();
  const wbm = ((editForm.base_model || "") as string).trim().toLowerCase();
  const opts = loras.value
    .filter((l) => {
      const n = (l.name || "").trim();
      if (!n) return false;
      const lbm = (l.base_model || "").trim().toLowerCase();
      return !wbm || !lbm || wbm === lbm;
    })
    .map((l) => {
      const n = (l.name || "").trim();
      known.add(n);
      return { label: n, value: n };
    });
  for (const row of editForm.loraList || []) {
    const n = (row.name || "").trim();
    if (n && !known.has(n)) {
      known.add(n);
      opts.push({ label: `${n}（库中不存在或不匹配当前底模）`, value: n });
    }
  }
  return opts;
});

function addLoraRow() {
  if (!editForm.loraList) editForm.loraList = [];
  editForm.loraList.push({ name: "", weight: 1.0, enabled: true });
}
function removeLoraRow(i: number) {
  editForm.loraList.splice(i, 1);
}
function refreshLoras() {
  message.loading("正在刷新 LoRA 列表…", { duration: 3000 });
  load();
}

function availLoras(w: any): string[] {
  const wbm = (w.base_model || "").trim().toLowerCase();
  return loras.value
    .filter((l) => {
      const lbm = (l.base_model || "").trim().toLowerCase();
      return !wbm || !lbm || wbm === lbm;
    })
    .map((l) => l.name || "")
    .filter(Boolean);
}

// 大图详情（全屏：左侧封面，右侧字段信息）；封面导航列表
const previewShow = ref(false);
const coverImages = ref<{ fname: string; title: string; fields: ItemViewerField[] }[]>([]);
const coverIndex = ref(0);

// 由工作流对象构造封面查看项（导航用）
function buildCover(w: any): { fname: string; title: string; fields: ItemViewerField[] } {
  const fields: ItemViewerField[] = [
    { key: "名称", value: w.name },
    { key: "别名", value: aliasStr(w.aliases || "") },
    { key: "底模", value: w.base_model?.trim() || "通用" },
    { key: "服务器", value: w.server_name?.trim() || "默认" },
    { key: "工作流文件", value: w.workflow_name?.trim() || "—" },
    { key: "Anima 模式", value: w.is_anima ? "是" : "否" },
    { key: "默认尺寸", value: w.default_width && w.default_height ? `${w.default_width} × ${w.default_height}` : "—" },
    { key: "可用 LoRA", value: availLoras(w).join("、") || "无匹配 LoRA" },
    { key: "预设 LoRA", value: w.loras_text?.trim() || "—" },
    { key: "封面文件", value: w.image || "—" },
  ];
  if (w.civitai_url) fields.push({ key: "C 站", value: w.civitai_url, href: w.civitai_url });
  return { fname: w.image || "", title: w.name || "", fields };
}

// 打开大图（支持左右箭头在封面列表间导航）
function openImage(idx: number) {
  const w = workflows.value[idx];
  if (!w) return;
  if (!w.image) { message.warning("该工作流暂无封面"); return; }
  coverImages.value = workflows.value.map(buildCover);
  coverIndex.value = idx;
  previewShow.value = true;
}

// 导航：左右切换（边界由 ItemViewer 禁用箭头 + 此处 clamp 双重保护）
function onCoverNav(delta: number) {
  const ni = coverIndex.value + delta;
  if (ni < 0 || ni >= coverImages.value.length) return;
  coverIndex.value = ni;
}

// 编辑
const editShow = ref(false);
const editTitle = ref("编辑工作流");
const editIndex = ref(-1);
// 宽高注入范围（resolution_mode）下拉选项
const resolutionModeOptions = [
  { label: "single（默认）只改分辨率节点 / 第一个 EmptyLatentImage", value: "single" },
  { label: "all：改所有 EmptyLatentImage（两阶段串联工作流必选）", value: "all" },
  { label: "none：完全不改，沿用工作流 JSON 原尺寸", value: "none" },
];

const editForm = reactive<Record<string, any>>({});

function openForm(idx: number, prefill?: any) {
  const isNew = idx < 0 || idx >= workflows.value.length;
  editTitle.value = (isNew ? "新增" : "编辑") + " 工作流";
  editIndex.value = idx;
  // 重置采样器文件默认值提示（避免打开新弹窗残留上一个工作流的读取结果）
  samplerHint.value = "";
  samplerLoading.value = false;
  const w = prefill ? prefill : (isNew ? {} : (workflows.value[idx] || {}));
  Object.keys(editForm).forEach((k) => delete editForm[k]);
  Object.assign(editForm, JSON.parse(JSON.stringify({
    name: w.name || "",
    base_model: w.base_model || "",
    aliases: w.aliases || "",
    server_name: w.server_name || "",
    workflow_name: w.workflow_name || "",
    is_anima: !!w.is_anima,
    kind: "draw",
    civitai_url: w.civitai_url || "",
    image: w.image || "",
    positive_node: w.positive_node || "",
    negative_node: w.negative_node || "",
    positive_field: w.positive_field || "",
    negative_field: w.negative_field || "",
    base_id: w.base_id || "",
    llm_notes: w.llm_notes || "",
    fixed_seed: w.fixed_seed || "",
    ov_steps: w.ov_steps || "",
    ov_cfg: w.ov_cfg || "",
    ov_sampler: w.ov_sampler || "",
    ov_scheduler: w.ov_scheduler || "",
    ov_denoise: w.ov_denoise || "",
    upscale_mode: w.upscale_mode || "",
    cleanup_mode: w.cleanup_mode || "",
    save_format: w.save_format || "",
    save_quality: w.save_quality || "",
    disable_builtin_loras: Array.isArray(w.disable_builtin_loras) ? [...w.disable_builtin_loras] : [],
    resolution_node: w.resolution_node || "",
    output_node: w.output_node || "",
    resolution_width_field: w.resolution_width_field || "width",
    resolution_height_field: w.resolution_height_field || "height",
    resolution_mode: w.resolution_mode || "single",
    default_width: w.default_width ?? 512,
    default_height: w.default_height ?? 512,
    max_width: w.max_width || "",
    max_height: w.max_height || "",
    lock_size: !!w.lock_size,
    image_node: w.image_node || "",
    lora_anchor: w.lora_anchor || "",
    lora_clip: w.lora_clip || "",
    upscale_node_id: w.upscale_node_id || "",
    upscale_model_name: w.upscale_model_name || "",
    default_steps: w.default_steps ?? 0,
    steps_off: !!w.steps_off,
    default_cfg: w.default_cfg ?? 0,
    cfg_off: !!w.cfg_off,
    default_denoise: (w.default_denoise ?? -1),
    denoise_off: (w.default_denoise ?? -1) <= -1,
    workflow_json: w.workflow_json || "",
    enabled: w.enabled !== false,
    require_prompt: w.require_prompt !== false,
    default_positive: w.default_positive || "",
    default_negative: w.default_negative || "",
    loras_text: w.loras_text || "",
    loraList: parseLorasText(w.loras_text || ""),
  })));
  editShow.value = true;
}

function addWorkflow() { openForm(-1); }
function editWorkflow(idx: number) { openForm(idx); }
function copyWorkflow(idx: number) {
  const src = workflows.value[idx];
  if (!src) return;
  const copy = JSON.parse(JSON.stringify(src));
  copy.name = "";
  openForm(-1, copy);
}

async function saveEdit() {
  if (!editForm.name || !editForm.name.trim()) { message.warning("名称必填"); return; }
  editForm.name = editForm.name.trim();
  saving.value = true;
  try {
    const tplKey = (workflows.value[editIndex.value] && workflows.value[editIndex.value].__template_key) || "default";
    const v = { ...editForm, __template_key: tplKey };
    // denoise 开关：勾选不注入时强制 -1（低于 min 的语义值，后端识别为「不注入」）
    if (v.denoise_off) v.default_denoise = -1;
    delete v.denoise_off;
    // v6.2.0：打「更新时间」戳（供列表按更新时间排序；创建时间用数组顺序表示）
    v.updated_at = Date.now();
    // LoRA 图形化列表 → 序列化为后端兼容的 loras_text（每行 名称|权重|0/1）
    v.loras_text = serializeLorasText(v.loraList);
    delete v.loraList;
    if (editIndex.value < 0 || editIndex.value >= workflows.value.length) {
      workflows.value.unshift(v);
    } else {
      workflows.value[editIndex.value] = { ...workflows.value[editIndex.value], ...v };
    }
    await apiPost("config", { config: { workflows: workflows.value } });
    message.success("工作流已保存");
    editShow.value = false;
  } catch (e: any) {
    message.error(e.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

// 从工作流文件读取采样器参数（steps / cfg / denoise）
const samplerLoading = ref(false);
const samplerHint = ref("");
async function fetchSamplerParams() {
  const name = (editForm.workflow_name || "").trim();
  if (!name) { message.warning("请先填写工作流文件名"); return; }
  samplerLoading.value = true;
  samplerHint.value = "";
  try {
    // 复用已长期可用的 /config 路由（POST body 传参，桥接传递可靠性优于 GET query）
    const d = await apiPost("config", { _read_sampler: true, workflow_name: name });
    const hasAny = !!d && typeof d === "object" && (d.steps != null || d.cfg != null || d.denoise != null);
    if (hasAny) {
      // 读到文件默认值：字段为空/未设置才自动填入（不覆盖用户已填值）；无论是否填入都在下方显示文件值
      const parts: string[] = [];
      if (d.steps != null) {
        parts.push(`steps ${d.steps}`);
        if (!editForm.steps_off && !editForm.default_steps) editForm.default_steps = d.steps;
      }
      if (d.cfg != null) {
        parts.push(`cfg ${d.cfg}`);
        if (!editForm.cfg_off && !editForm.default_cfg) editForm.default_cfg = d.cfg;
      }
      if (d.denoise != null) {
        parts.push(`denoise ${d.denoise}`);
        if (!editForm.denoise_off && editForm.default_denoise <= -1) editForm.default_denoise = d.denoise;
      }
      samplerHint.value = "文件默认值：" + parts.join("　");
      message.success("已读取工作流文件的采样器参数");
    } else {
      // 区分「返回了整个配置」vs「返回了 null」vs「报错」
      const isConfigLike = !!d && typeof d === "object"
        && (Array.isArray(d.workflows) || Array.isArray(d.lora_library) || "provider_settings" in d || "draw_limit" in d);
      if (isConfigLike) {
        message.warning("返回的是插件配置而非采样参数：请确认插件后端已更新到 v4.9.12+ 并重载插件后再试");
      } else {
        message.warning((d && d.error) || "未读取到采样器参数（文件里没有采样器节点？或插件后端未更新到 v4.9.12+）");
      }
    }
  } catch (e: any) {
    message.error(e.message || "读取失败");
  } finally {
    samplerLoading.value = false;
  }
}

// 快捷启用/停用工作流
async function toggleEnabled(idx: number) {
  const w = workflows.value[idx];
  if (!w) return;
  const next = w.enabled === false;
  w.enabled = next;
  try {
    await apiPost("config", { config: { workflows: workflows.value } });
    message.success(next ? `已启用「${w.name || ""}」` : `已停用「${w.name || ""}」`);
  } catch (e: any) {
    w.enabled = !next;
    message.error(e.message || "保存失败");
  }
}

function removeWorkflow(idx: number) {
  const w = workflows.value[idx] || {};
  dialog.warning({
    title: "删除工作流",
    content: `确定要删除工作流「${w.name || ""}」吗？此操作不可恢复！`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      workflows.value.splice(idx, 1);
      try {
        await apiPost("config", { config: { workflows: workflows.value } });
        message.success("工作流已删除");
      } catch (e: any) {
        message.error(e.message || "删除失败");
      }
    },
  });
}

// 抓取封面选择（多张候选时弹出）
const coverPickShow = ref(false);
const coverPickCovers = ref<string[]>([]);
const coverPickTitle = ref("");
let coverPickOnPick: ((name: string) => void) | null = null;

function onCoverPick(name: string) {
  if (coverPickOnPick) coverPickOnPick(name);
  coverPickOnPick = null;
}

// 应用封面并保存
function applyCoverFetch(idx: number, w: any, chosenName: string) {
  w.image = chosenName;
  workflows.value = [...workflows.value];
  apiPost("config", { config: { workflows: workflows.value } }).then(() => {
    message.success("封面已保存");
  }).catch((e: any) => message.error(e.message || "保存失败"));
}

function fetchCover(idx: number) {
  const w = workflows.value[idx];
  if (!w) return;
  if (!w.civitai_url) { message.warning("请先填写 C 站链接"); return; }
  message.loading("正在抓取封面…", { duration: 10000 });
  apiPost("lora/fetch", { url: w.civitai_url }, { timeout: 60000 }).then((d) => {
    const covers = (Array.isArray(d.images) && d.images.length) ? d.images : (d.image ? [d.image] : []);
    if (!covers.length) throw new Error("未抓取到封面图");
    if (covers.length > 1) {
      // 多张候选 → 弹封面选择
      coverPickCovers.value = covers;
      coverPickTitle.value = `为「${w.name || "工作流"}」选择封面`;
      coverPickOnPick = (chosen) => applyCoverFetch(idx, w, chosen);
      coverPickShow.value = true;
      return;
    }
    applyCoverFetch(idx, w, covers[0]);
  }).catch((e: any) => message.error(e.message || "抓取失败"));
}

// 封面设置：卡片拖拽 / 弹窗（本地文件或图片直链）统一走 applyCover
const coverEditorShow = ref(false);
const coverEditorTitle = ref("");
let coverEditorTarget = -1;
const coverDragIdx = ref(-1);
const { uploadFile } = useCover();

async function applyCover(idx: number, name: string) {
  const w = workflows.value[idx];
  if (!w) return;
  w.image = name;
  workflows.value = [...workflows.value];
  try {
    await apiPost("config", { config: { workflows: workflows.value } });
    message.success("封面已设置");
  } catch (e: any) {
    message.error(e.message || "保存失败");
  }
}

function openCoverEditor(idx: number) {
  const w = workflows.value[idx];
  if (!w) return;
  coverEditorTarget = idx;
  coverEditorTitle.value = `为「${w.name || "工作流"}」设置封面`;
  coverEditorShow.value = true;
}

async function onDropCover(idx: number, ev: DragEvent) {
  coverDragIdx.value = -1;
  const file = Array.from(ev.dataTransfer?.files || []).find((f: File) => f.type.startsWith("image/"));
  if (!file) {
    message.warning("请拖入图片文件");
    return;
  }
  const name = await uploadFile(file);
  if (name) await applyCover(idx, name);
}

async function onCoverConfirm(name: string) {
  if (coverEditorTarget >= 0) await applyCover(coverEditorTarget, name);
  coverEditorTarget = -1;
}

useRefresh(load);
onMounted(load);
</script>

<style scoped>
.workflows-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.view-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; flex: 0 0 auto; }
.view-head h2 { margin: 0 0 4px; }
.view-head p { margin: 0; color: var(--text-sub); font-size: 13px; }
.view-actions { display: flex; gap: 8px; }
.filter-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex: 0 0 auto; flex-wrap: wrap; }
.filter-hint { color: var(--text-sub); font-size: 12px; }
/* 搜索框：窄屏占满一行，宽屏固定宽度不挤压筛选按钮 */
.filter-search { width: 100%; max-width: 340px; flex: 1 1 220px; }
.filter-radios { display: flex; flex-wrap: wrap; gap: 4px; }
.filter-radios :deep(.n-radio-group) { flex-wrap: wrap; gap: 4px; }
.filter-radios :deep(.n-radio-button) { flex: 0 0 auto; }
.lora-list { width: 100%; }
.lora-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.wf-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; padding-right: 4px; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
.wf-card {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-panel);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
/* 星标（v6.2.0）：★ 高亮 + 卡片描边 */
.wf-card.is-star { border-color: rgba(250, 173, 20, 0.6); }
.card-star {
  cursor: pointer; font-size: 15px; line-height: 1; opacity: 0.45;
  color: var(--text-color, #888); user-select: none; flex: 0 0 auto;
}
.card-star:hover { opacity: 0.85; }
.card-star.on { opacity: 1; color: #faad14; }
.card-cover { position: relative; aspect-ratio: 3 / 4; border-radius: 8px; overflow: hidden; cursor: zoom-in; background: var(--bg-body); display: flex; align-items: center; justify-content: center; }
.card-cover.is-drag { outline: 2px dashed var(--accent); outline-offset: -2px; background: rgba(0, 122, 255, 0.08); }
.card-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cover-empty { color: var(--text-sub); font-size: 12px; }
.cover-drop-tip { position: absolute; top: 8px; left: 50%; transform: translateX(-50%); font-size: 12px; color: var(--accent); background: var(--bg-panel); padding: 2px 8px; border-radius: 6px; opacity: 0; transition: opacity 0.15s; pointer-events: none; }
.card-cover.is-drag .cover-drop-tip { opacity: 1; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.card-title { font-weight: 600; font-size: 15px; }
.card-alias { color: var(--text-sub); font-size: 12px; }
.card-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.meta-item { color: var(--text-sub); }
.civ-link { color: var(--accent); text-decoration: none; font-size: 12px; }
.card-loracfg { font-size: 12px; color: var(--text-sub); }
.card-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.edit-form { max-height: 65vh; overflow: auto; padding-right: 4px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-hint { color: var(--text-sub); font-size: 12px; margin-left: 8px; }

@media (max-width: 768px) {
  .workflows-view { padding: 0; }
  .view-head { flex-direction: column; align-items: stretch; gap: 10px; }
  .view-actions { flex-wrap: wrap; }
  .view-actions :deep(.n-button) { flex: 1 1 auto; }
  .card-grid { grid-template-columns: 1fr; }
  .form-grid { grid-template-columns: 1fr; }
}
</style>
