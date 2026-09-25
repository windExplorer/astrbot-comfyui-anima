<template>
  <div class="workflows-view">
    <div class="view-head">
      <div>
        <h2>{{ legacy ? "旧版工作流" : "工作流" }}</h2>
        <p v-if="legacy">
          旧版工作流（未引用基础工作流）：点卡片上的「转新版」即可按<b>工作流文件名</b>匹配基础工作流完成迁移
          （匹配到才允许转换，封面会保留；原本没封面则取基础工作流的封面），也可以用右上「批量转新版」一次处理。
        </p>
        <p v-else>
          新版工作流：选择基础工作流后节点自动定位（无需手填节点 ID），并按需拨动语义开关
          （采样器 / 放大 / 清理显存 / 保存格式 / LLM 说明）。旧版条目在「旧版工作流」页。
        </p>
      </div>
      <Teleport to="#mobile-filter-slot" :disabled="!isMobile">
        <div class="view-actions">
          <n-button :loading="loading" @click="load">刷新</n-button>
          <n-button v-if="!legacy" type="primary" @click="addWorkflow">＋ 新增工作流</n-button>
          <template v-else>
            <n-button type="primary" :loading="converting" @click="convertAllLegacy">
              批量转新版
            </n-button>
            <n-button @click="gotoNew">去新版新建</n-button>
          </template>
        </div>
      </Teleport>
    </div>

    <!-- 星标筛选 + 排序（v6.2.0）：工作流多了以后按需挑常用的 -->
    <div class="filter-bar">
      <n-checkbox v-model:checked="starOnly" size="small">★ 仅看星标</n-checkbox>
      <span class="filter-hint">星标 {{ starredCount }} / {{ scopedWorkflows.length }}</span>
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
        匹配 {{ filteredWorkflows.length }} / {{ scopedWorkflows.length }} 条
      </span>
    </div>

    <div class="wf-scroll">
    <n-spin :show="loading">
      <n-empty
        v-if="!loading && !filteredWorkflows.length"
        :description="searchText.trim()
          ? `没有匹配「${searchText.trim()}」的工作流。`
          : (legacy ? '没有旧版工作流（都已迁移到新版）。' : '还没有新版工作流：点「＋ 新增工作流」并选择基础工作流即可。')"
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
            <div v-else class="cover-empty">
              <div>无封面</div>
              <div class="cover-empty-tip">点击查看详情</div>
            </div>
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
            <n-tag v-if="wfIsImg2Img(w)" size="small" type="success" :bordered="false">图生图</n-tag>
            <n-tag v-else size="small" type="default" :bordered="false">文生图</n-tag>
            <n-tag v-if="wfIsImg2Img(w) && baseOf(w)?.roles?.multi_image" size="small" type="success"
                   :bordered="false" round>多参</n-tag>
            <n-tag v-if="(w.base_id || '').trim()" size="small" type="warning" :bordered="false">v7</n-tag>
            <n-tag v-else size="small" type="default" :bordered="false">旧版</n-tag>
          </div>
          <div class="card-alias">
            {{ legacy ? `别名：${aliasStr(w.aliases)}` : `描述：${(w.desc || "").trim() || "—"}` }}
          </div>
          <div class="card-meta">
            <n-tag size="tiny" :bordered="false">
              {{ legacy ? (w.base_model?.trim() || "不限底模") : (baseOf(w)?.basemodel_name || "底模未关联") }}
            </n-tag>
            <span class="meta-item">{{ serverLabel(w) }}</span>
            <span v-if="legacy && w.workflow_name" class="meta-item">{{ w.workflow_name }}</span>
            <span v-if="!legacy && baseOf(w)" class="meta-item">{{ baseOf(w)?.name }}</span>
            <a
              v-if="legacy ? w.civitai_url : baseOf(w)?.civitai_url"
              :href="legacy ? w.civitai_url : baseOf(w)?.civitai_url"
              target="_blank"
              rel="noopener noreferrer"
              class="civ-link"
            >C站 ↗</a>
          </div>
          <div class="card-loracfg">{{ (w.loras_text || "").trim() ? "已配默认 LoRA" : "未配默认 LoRA" }}</div>
          <div class="card-actions">
            <n-button size="tiny" @click="editWorkflow(i)">编辑</n-button>
            <n-button size="tiny" @click="toggleEnabled(i)">{{ w.enabled === false ? "启用" : "停用" }}</n-button>
            <n-button size="tiny" @click="copyWorkflow(i)">复制</n-button>
            <n-button v-if="legacy" size="tiny" type="primary" :loading="converting" @click="convertLegacy(i)">
              转新版
            </n-button>
            <n-button size="tiny" @click="legacy ? fetchCover(i) : useBaseCover(i)">
              {{ legacy ? "抓封面" : "默认封面" }}
            </n-button>
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
    <n-modal
      v-model:show="editShow"
      preset="card"
      :title="editTitle"
      class="wf-modal"
      :bordered="false"
      :style="{ width: '960px', maxWidth: '96vw' }"
    >
      <n-form label-placement="top" :label-width="0" class="edit-form">
        <n-alert v-if="legacy" type="warning" style="margin-bottom: 10px">
          旧版工作流（未引用基础工作流）：仅可调整基础字段，节点/采样器等实现细节已收敛到新版。
          建议在「工作流」页用基础工作流重建后再删除本条。
        </n-alert>
        <!-- v7.0.9：基础工作流是最高优先级——先选它，其余配置项才出现 -->
        <template v-if="!legacy">
          <n-form-item label="基础工作流（第一步：不选无法继续）">
            <n-select
              v-model:value="editForm.base_id"
              :options="baseWfOptions"
              filterable
              placeholder="选择基础工作流（上传/管理见「基础工作流」页）"
            />
          </n-form-item>
          <n-alert v-if="!hasBase" type="info" style="margin-bottom: 10px">
            请先选择基础工作流——节点定位、类型、底模、采样器/放大/保存等默认值都由它解析得出。
          </n-alert>
        </template>

        <!-- ===== 旧版工作流：线性表单（冻结，不再演进） ===== -->
        <template v-if="legacy">
          <div class="form-grid">
            <n-form-item label="名称"><n-input v-model:value="editForm.name" placeholder="如 sd" /></n-form-item>
            <n-form-item label="绑定服务器">
              <n-select
                v-model:value="editForm.server_key"
                :options="serverOptions"
                clearable
                placeholder="默认服务器"
              />
            </n-form-item>
          </div>
          <n-form-item label="别名（逗号/换行分隔）"><n-input v-model:value="editForm.aliases" type="textarea" :rows="2" /></n-form-item>
          <div class="form-grid">
            <n-form-item label="底模">
              <n-select v-model:value="editForm.base_model" :options="baseModelOptions" />
            </n-form-item>
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
            <span class="form-hint">开启后该工作流无需提示词即可出图；用户传了提示词也会被忽略</span>
          </n-form-item>
          <div class="form-grid">
            <n-form-item label="固定正向提示词（可选）"><n-input v-model:value="editForm.default_positive" type="textarea" :rows="2" placeholder="锁定提示词时用此提示词覆盖工作流 JSON；未锁定时用户不传词也会兜底；不走翻译/改写" /></n-form-item>
            <n-form-item label="固定负向提示词（可选）"><n-input v-model:value="editForm.default_negative" type="textarea" :rows="2" placeholder="用户未传负向提示词时，用此覆盖工作流 JSON 内的负向提示词" /></n-form-item>
          </div>
          <div class="form-grid">
            <n-form-item label="C 站链接（抓封面）">
              <n-input v-model:value="editForm.civitai_url" placeholder="https://civitai.com/models/xxx" />
            </n-form-item>
            <n-form-item label="封面图文件名"><n-input v-model:value="editForm.image" placeholder="可上传/抓取" /></n-form-item>
          </div>
          <n-divider style="margin:8px 0">── 宽高 ──</n-divider>
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
          <n-divider style="margin:8px 0">── 节点配置（旧版） ──</n-divider>
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
          <n-form-item label="参考图上限（多参图生图）">
            <n-input-number v-model:value="editForm.max_refs" :min="0" :max="8" style="width:100%"
                            placeholder="0 = 沿用全局（默认 3）" />
            <span class="form-hint">多参图生图工作流单次最多取几张参考图（按顺序对应 image_1~N）。0 或留空 = 沿用全局「出图行为 → 图生图参考图上限」。仅多参工作流生效，单图工作流恒为 1。</span>
          </n-form-item>
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
          <template v-if="builtinLoras.length">
            <n-form-item label="基础工作流内置 LoRA（不可删除，可禁用）">
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
        </template>

        <!-- ===== 新版工作流：按配置类型分页 ===== -->
        <template v-else-if="hasBase">
          <!-- v7.0.14：页签内容区固定高度（内部滚动），避免切换页签时弹窗高度跳动 -->
          <n-tabs
            v-model:value="formTab"
            type="line"
            animated
            pane-style="padding-top:12px; padding-right:8px; height:clamp(320px, 50vh, 560px); overflow-y:auto; overflow-x:hidden"
          >

            <n-tab-pane name="basic" tab="基础信息">
              <div class="form-grid">
                <n-form-item label="名称"><n-input v-model:value="editForm.name" placeholder="如 动漫日常" /></n-form-item>
                <n-form-item label="绑定服务器">
                  <n-select v-model:value="editForm.server_key" :options="serverOptions" clearable placeholder="默认服务器" />
                </n-form-item>
              </div>
              <n-form-item label="描述（仅备注展示，不参与名称匹配）">
                <n-input v-model:value="editForm.desc" type="textarea" :rows="2" placeholder="如：日常出图用；或 头像专用，只出头像构图" />
              </n-form-item>
              <n-form-item label="底模 / 类型（来自基础工作流，只读）">
                <n-space :size="6" align="center">
                  <n-tag size="small" :bordered="false">{{ selectedBaseKindLabel }}</n-tag>
                  <n-tag size="small" type="info" :bordered="false">底模：{{ selectedBaseBasemodel }}</n-tag>
                  <n-tag v-if="selectedBaseCivitai" size="small" :bordered="false">
                    <a :href="selectedBaseCivitai" target="_blank" rel="noopener noreferrer" class="civ-link">C站链接 ↗</a>
                  </n-tag>
                  <span v-else class="form-hint">C 站链接在「基础工作流」里配置</span>
                </n-space>
              </n-form-item>
              <n-form-item label="启用该工作流">
                <n-switch v-model:value="editForm.enabled" />
                <span class="form-hint">关闭后不可使用：显式指定会提示「已停用」，自动选择默认工作流也会跳过它</span>
              </n-form-item>
              <n-form-item label="参考图上限（多参图生图）">
                <n-input-number v-model:value="editForm.max_refs" :min="0" :max="8" style="width:100%"
                                placeholder="0 = 沿用全局（默认 3）" />
                <span class="form-hint">多参图生图工作流单次最多取几张参考图（按顺序对应 image_1~N，提示词「图N」即第 N 张）。0 或留空 = 沿用全局「出图行为 → 图生图参考图上限」。单图工作流恒为 1。</span>
              </n-form-item>
              <n-form-item label="封面图文件名"><n-input v-model:value="editForm.image" placeholder="可上传/抓取；沿用基础工作流封面亦可自行更换" /></n-form-item>
            </n-tab-pane>

            <n-tab-pane name="prompt" tab="提示词">
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
              <n-form-item label="LLM 注入说明（用本工作流出图时告知 LLM 的用法；留空不注入）">
                <n-input v-model:value="editForm.llm_notes" type="textarea" :rows="2" placeholder="如：本工作流专画头像，用户要头像时优先选用" />
              </n-form-item>
            </n-tab-pane>

            <n-tab-pane name="size" tab="尺寸">
              <!-- v7.0.17：把「配置项 → 尺寸比例预设」接进表单，点一下即填默认宽高 -->
              <n-form-item label="尺寸预设（来自配置页「尺寸比例预设」）">
                <n-space :size="6" align="center" style="width:100%">
                  <n-select
                    :value="null"
                    :options="sizePresetOptions"
                    placeholder="选择后自动填入下方默认宽高（也可手填）"
                    filterable
                    style="min-width:260px; flex:1"
                    @update:value="applySizePreset"
                  />
                  <n-button size="tiny" quaternary @click="goConfig">编辑预设（配置页）↗</n-button>
                </n-space>
                <span class="form-hint">
                  基准值为 **1K**（SDXL 原生尺寸，如 832×1216 = 1K 竖版 2:3）；
                  用户话里的比例词（竖版/方形/16:9）会按此触发。
                </span>
              </n-form-item>
              <n-form-item label="最高支持尺寸档位">
                <n-space :size="6" align="center" style="width:100%">
                  <n-select
                    v-model:value="editForm.max_size_tier"
                    :options="tierOptions"
                    style="min-width:220px"
                  />
                  <n-button size="tiny" quaternary @click="openTierTable">档位 × 比例对照表</n-button>
                </n-space>
                <span class="form-hint">
                  按总像素预算：1K≈1.0MP｜1.5K≈2.3MP｜2K≈4.2MP（默认）｜4K≈8.3MP；
                  超出该档的尺寸会等比降级（不会放大）。想彻底锁死用「禁止改变默认宽高」。
                </span>
              </n-form-item>
              <div class="form-grid">
                <n-form-item label="默认宽度">
                  <n-input-number
                    v-model:value="editForm.default_width"
                    style="width:100%"
                    @update:value="markSizeTouched"
                  />
                  <span class="form-hint">{{ baseLatentHint }}（选基础工作流时自动带入）</span>
                </n-form-item>
                <n-form-item label="默认高度">
                  <n-input-number
                    v-model:value="editForm.default_height"
                    style="width:100%"
                    @update:value="markSizeTouched"
                  />
                  <span class="form-hint">{{ baseLatentHint }}（选基础工作流时自动带入）</span>
                </n-form-item>
              </div>
              <div class="form-grid">
                <n-form-item label="允许的最大宽度（留空=不限制）">
                  <n-input v-model:value="editForm.max_width" placeholder="如 1536；超限按比例缩小" />
                  <span class="form-hint">基础工作流：无此限制（非节点参数，仅插件侧裁剪）</span>
                </n-form-item>
                <n-form-item label="允许的最大高度（留空=不限制）">
                  <n-input v-model:value="editForm.max_height" placeholder="如 2048；超限按比例缩小" />
                  <span class="form-hint">基础工作流：无此限制（非节点参数，仅插件侧裁剪）</span>
                </n-form-item>
              </div>
              <n-form-item label="禁止改变默认宽高">
                <n-switch v-model:value="editForm.lock_size" />
                <span class="form-hint">开启后忽略用户传参与比例关键词，恒用默认宽高（图生图不适用）</span>
              </n-form-item>
              <n-button size="tiny" quaternary @click="resetSize">↺ 恢复默认（宽高回填基础工作流值并清空限制）</n-button>
            </n-tab-pane>

            <n-tab-pane name="sampler" tab="采样器">
          <div class="form-grid">
            <n-form-item label="固定种子（留空 = 每次随机）">
              <n-input v-model:value="editForm.fixed_seed" placeholder="数字；用户显式指定种子时以用户为准" />
              <span class="form-hint">{{ fmtBase("seed", "基础工作流：随机") }}</span>
            </n-form-item>
            <n-form-item label="步数">
              <n-input v-model:value="editForm.ov_steps" placeholder="留空 = 跟随基础工作流" />
              <span class="form-hint">{{ fmtBase("steps", "基础工作流：未解析到") }}</span>
            </n-form-item>
            <n-form-item label="CFG">
              <n-input v-model:value="editForm.ov_cfg" placeholder="留空 = 跟随基础工作流" />
              <span class="form-hint">{{ fmtBase("cfg", "基础工作流：未解析到") }}</span>
            </n-form-item>
            <n-form-item label="采样器">
              <n-select
                v-model:value="editForm.ov_sampler"
                :options="samplerOptions"
                filterable
                tag
                clearable
                placeholder="留空 = 跟随基础工作流"
              />
              <span class="form-hint">{{ fmtBase("sampler_name", "基础工作流：未解析到") }}</span>
            </n-form-item>
            <n-form-item label="调度器">
              <n-select
                v-model:value="editForm.ov_scheduler"
                :options="schedulerOptions"
                filterable
                tag
                clearable
                placeholder="留空 = 跟随基础工作流"
              />
              <span class="form-hint">{{ fmtBase("scheduler", "基础工作流：未解析到") }}</span>
            </n-form-item>
            <n-form-item label="噪点 denoise">
              <n-input
                v-model:value="editForm.ov_denoise"
                :disabled="selectedBaseKind !== 'img2img'"
                placeholder="留空 = 跟随基础工作流"
              />
              <span class="form-hint">
                {{ selectedBaseKind !== "img2img" ? "文生图固定为 1（只读）" : fmtBase("denoise", "基础工作流：未解析到") }}
              </span>
            </n-form-item>
          </div>
          <n-button size="tiny" quaternary @click="resetSampler">↺ 恢复默认（清空采样器覆盖）</n-button>
          </n-tab-pane>

            <n-tab-pane name="post" tab="放大与清理">
              <div class="form-grid">
                <n-form-item label="放大模式">
                  <n-select v-model:value="editForm.upscale_mode" :options="upscaleModeOptions" />
                  <span class="form-hint">{{ baseUpscaleHint }}</span>
                </n-form-item>
                <n-form-item label="放大模型">
                  <n-select
                    v-model:value="editForm.upscale_model_name"
                    :options="upscaleModelOptions"
                    filterable
                    tag
                    clearable
                    placeholder="留空 = 跟随基础工作流"
                  />
                  <span class="form-hint">{{ baseUpscaleModelHint }}</span>
                </n-form-item>
              </div>
              <n-form-item label="清理显存">
                <n-select v-model:value="editForm.cleanup_mode" :options="cleanupModeOptions" />
                <span class="form-hint">{{ baseCleanupHint }}</span>
              </n-form-item>
              <n-form-item label="放大模型可选列表（数据源）">
                <n-space :size="6" align="center">
                  <span class="form-hint">在「配置项」页维护（kind=upscale_model），当前可用 {{ upscaleOptions.length }} 个</span>
                  <n-button size="tiny" quaternary @click="goOptions">前往配置项 ↗</n-button>
                </n-space>
              </n-form-item>
              <n-space :size="8">
                <n-button size="tiny" quaternary @click="resetUpscale">↺ 恢复默认（清空放大覆盖）</n-button>
                <n-button size="tiny" quaternary @click="resetCleanup">↺ 恢复默认（清空清理覆盖）</n-button>
              </n-space>
            </n-tab-pane>

            <n-tab-pane name="save" tab="保存">
              <div class="form-grid">
                <n-form-item label="保存格式">
                  <n-select
                    v-model:value="editForm.save_format"
                    :options="saveFormatOptions"
                    placeholder="留空 = 跟随基础工作流"
                  />
                  <span class="form-hint">
                    {{ baseDefaults.save?.has_output_ext === false ? "基础工作流：保存节点不支持格式（配置后将替换为 SaveImageExtended）" : (baseDefaults.save?.output_ext ? `基础工作流：${baseDefaults.save.output_ext}` : "基础工作流：未解析到") }}
                  </span>
                </n-form-item>
                <n-form-item label="保存质量 %">
                  <n-input v-model:value="editForm.save_quality" placeholder="留空 = 跟随基础工作流" />
                  <span class="form-hint">
                    {{ baseDefaults.save?.has_quality === false ? "基础工作流：保存节点无质量字段（配置后将替换为 SaveImageExtended）" : (baseDefaults.save?.quality != null ? `基础工作流：${baseDefaults.save.quality}` : "基础工作流：未解析到") }}
                  </span>
                </n-form-item>
              </div>
              <n-button size="tiny" quaternary @click="resetSave">↺ 恢复默认（清空保存覆盖）</n-button>
            </n-tab-pane>

            <n-tab-pane name="lora" tab="LoRA">
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
              <div v-else class="form-hint">基础工作流未内置 LoRA。</div>
              <n-form-item label="附加 LoRA（追加注入，不影响内置）">
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
                  <n-space style="margin-top: 6px" align="center">
                    <n-button size="tiny" @click="addLoraRow">＋ 添加 LoRA</n-button>
                    <n-button size="tiny" @click="refreshLoras">↻ 刷新 LoRA 列表</n-button>
                    <n-checkbox v-model:checked="loraFilterByBase" size="small">
                      只显示匹配底模的 LoRA
                    </n-checkbox>
                  </n-space>
                  <div class="form-hint">
                    {{ loraFilterHint }}；从全局 LoRA 库下拉选择（可搜索），保存后写回 loras_text（名称|权重|0/1）
                  </div>
                </div>
              </n-form-item>
            </n-tab-pane>
          </n-tabs>
        </template>

      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editShow = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveEdit">保存</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- v7.0.18：尺寸档位 × 比例对照表 -->
    <n-modal
      v-model:show="tierModalShow"
      preset="card"
      title="尺寸档位 × 比例对照表"
      :bordered="false"
      :style="{ width: '820px', maxWidth: '96vw' }"
    >
      <div class="form-hint" style="margin-top:0">
        档位按**总像素预算**定义（不是长边）：1K≈1.0MP（SDXL 原生）、1.5K≈2.3MP、2K≈4.2MP、4K≈8.3MP（＝UHD 像素量）。
        比例定长宽比、档位定像素量；用户话里同时出现两者时组合生效（如「2K 竖版」）。
        实际出图再按工作流「最高支持尺寸档位」降级、按「最大宽/高」裁剪。
      </div>
      <n-data-table
        :columns="tierColumns"
        :data="tierRows"
        size="small"
        :bordered="false"
        :single-line="false"
        :max-height="420"
      />
      <template #footer>
        <n-space justify="space-between" align="center">
          <span class="form-hint">
            比例基准尺寸在「配置页 → 尺寸比例预设」里维护（1K 基准）；
            这里点「编辑预设」会直接展开并定位到那个分组。
          </span>
          <n-space :size="8">
            <n-button size="small" @click="goConfig">编辑预设（配置页）↗</n-button>
            <n-button @click="tierModalShow = false">关闭</n-button>
          </n-space>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useMessage, useDialog, NButton, NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NSwitch, NTag, NSpace, NDivider, NEmpty, NSpin, NCheckbox, NRadioGroup, NRadioButton, NAlert, NTabs, NTabPane, NDataTable } from "naive-ui";
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
// v7.0.4：本组件同时服务两个页面——新版（默认，引用基础工作流）与旧版（未引用，只读维护）
const props = withDefaults(defineProps<{ legacy?: boolean }>(), { legacy: false });
const legacy = computed(() => !!props.legacy);
const router = useRouter();
const isLegacyEntry = (w: any) => !String(w?.base_id || "").trim();
function gotoNew() { router.push({ name: "workflows" }); }

/** 取工作流引用的基础工作流记录（新版卡片展示底模/C站用） */
function baseOf(w: any) {
  const id = String(w?.base_id || "").trim();
  if (!id) return null;
  return baseWfs.value.find((b: any) => String(b.id) === id) || null;
}

/** 是否图生图（v7.7.22 修复：新版条目没有 image_node 字段，之前全被标成「文生图」）。
 * 旧版：节点配置里配了「参考图节点」；新版：看绑定的基础工作流解析出的 kind。 */
function wfIsImg2Img(w: any): boolean {
  if (String(w?.image_node || "").trim()) return true;
  return baseOf(w)?.roles?.kind === "img2img";
}
const loading = ref(false);
const saving = ref(false);
const workflows = ref<any[]>([]);
const loras = ref<any[]>([]);

// 底模下拉：v7.0.5 起改为「底模库（配置项页）」动态提供，
// 并合并旧工作流里已存在的历史值（保证老条目原值可选、不被清空）。
const basemodelNames = ref<string[]>([]);
async function loadBasemodelNames() {
  try {
    const d = await apiGet("basemodels");
    basemodelNames.value = (Array.isArray(d?.items) ? d.items : [])
      .map((b: any) => String(b?.name || "").trim())
      .filter(Boolean);
  } catch {
    basemodelNames.value = [];
  }
}
const baseModelOptions = computed(() => {
  const legacyVals = Array.from(
    new Set(
      workflows.value
        .map((w) => String(w?.base_model || "").trim())
        .filter(Boolean)
    )
  );
  const all = Array.from(new Set([...basemodelNames.value, ...legacyVals]));
  return [
    { label: "（通用 / 不限底模）", value: "" },
    ...all.map((n) => ({ label: n, value: n })),
  ];
});

// ---- 基础工作流（v7.0.0） ----
const baseWfs = ref<any[]>([]);
// 弹窗分页（v7.0.12：整个表单按配置类型分 tab）
const formTab = ref("basic");
/** 用户手动改过默认宽高（改过之后不再被基础工作流值覆盖） */
function markSizeTouched() {
  sizeTouched.value = true;
}
/** 跳到「配置项」页（放大模型数据源） */
function goOptions() {
  router.push("/options");
}
/** 用户是否手动改过默认宽高（改过之后不再用基础工作流的值覆盖） */
const sizeTouched = ref(false);

/** 用基础工作流解析出的宽高回填「默认宽高」（未手动改过时才覆盖，含历史 512 兜底值） */
function applyBaseSize() {
  if (sizeTouched.value) return;
  const lat = selectedBase.value?.roles?.latent || {};
  if (!lat.default_width || !lat.default_height) return;
  const curW = Number(editForm.default_width || 0);
  const curH = Number(editForm.default_height || 0);
  // 512 是历史兜底值（不是用户意图），一并纠正为基础工作流的真实尺寸
  if (curW !== lat.default_width) editForm.default_width = lat.default_width;
  if (curH !== lat.default_height) editForm.default_height = lat.default_height;
}

// 放大模型下拉数据源：配置项页 kind=upscale_model（v7.0.9）
const upscaleOptions = ref<any[]>([]);
async function loadUpscaleOptions() {
  try {
    const d = await apiGet("options/list", { kind: "upscale_model" });
    upscaleOptions.value = (Array.isArray(d?.items) ? d.items : []).filter((o: any) => o.enabled !== false);
  } catch {
    upscaleOptions.value = [];
  }
}
const baseWfOptions = computed(() => [
  { label: "不使用（旧版模式）", value: "" },
  ...baseWfs.value.map((b) => ({ label: b.name || b.file_name || `#${b.id}`, value: String(b.id) })),
]);
const selectedBase = computed(() =>
  baseWfs.value.find((b) => String(b.id) === String(editForm.base_id || "")) || null
);
const selectedBaseKind = computed(() => selectedBase.value?.roles?.kind || "");
/** 是否已选定基础工作流（新版必须选定才能配置其余项） */
const hasBase = computed(() => !!String(editForm.base_id || "").trim());
/** 基础工作流的解析默认值（用于表单下方「跟随值」提示与恢复默认） */
const baseDefaults = computed<any>(() => selectedBase.value?.roles || {});
/** 单个采样参数的「基础工作流值是 X」提示（v7.0.9 改为逐项展示，不再顶部汇总） */
function fmtBase(key: string, fallback: string): string {
  const v = (baseDefaults.value?.sampler_defaults || {})[key];
  if (v === undefined || v === null || v === "") return fallback;
  return `基础工作流：${v}`;
}
const baseUpscaleHint = computed(() => {
  const up = baseDefaults.value?.upscale || {};
  if (!up.apply && !up.model_name) return "基础工作流：无放大链（启用后将运行时注入放大节点）";
  return `基础工作流：跟随（已内置放大链${up.model_name ? `，当前模型 ${up.model_name}` : ""}）`;
});
const baseUpscaleModelHint = computed(() => {
  const up = baseDefaults.value?.upscale || {};
  if (up.model_name) return `基础工作流：${up.model_name}`;
  return up.apply ? "基础工作流：已内置放大链（未解析到模型名）" : "基础工作流：无放大链";
});
const baseCleanupHint = computed(() => {
  const has = (baseDefaults.value?.cleanup_nodes || []).length > 0;
  return has ? "基础工作流：跟随（已有清理显存节点）" : "基础工作流：无清理节点（启用后将运行时注入）";
});
const baseLatentHint = computed(() => {
  const lat = baseDefaults.value?.latent || {};
  if (lat.default_width && lat.default_height) {
    return `基础工作流：${lat.default_width}×${lat.default_height}`;
  }
  return lat.node ? "基础工作流：宽高由连线控制（未固化数值）" : "基础工作流：未解析到宽高节点";
});
// 放大模型下拉（配置项页 kind=upscale_model）+ 基础图当前模型兜底
const upscaleModelOptions = computed(() => {
  const opts = (upscaleOptions.value || []).map((o: any) => ({
    label: o.note ? `${o.name}（${o.note}）` : o.name,
    value: o.name,
  }));
  const cur = String(baseDefaults.value?.upscale?.model_name || "").trim();
  if (cur && !opts.some((o: any) => o.value === cur)) {
    opts.unshift({ label: `${cur}（基础工作流当前）`, value: cur });
  }
  return opts;
});

/** 恢复默认 = 清空覆盖项，回到「跟随基础工作流」 */
function resetSampler() {
  editForm.ov_steps = "";
  editForm.ov_cfg = "";
  // 下拉用 null 表示「未选」（空串会显示空白却带清除叉叉）
  editForm.ov_sampler = null;
  editForm.ov_scheduler = null;
  editForm.ov_denoise = "";
  editForm.fixed_seed = "";
}
function resetUpscale() {
  editForm.upscale_mode = "";
  editForm.upscale_model_name = null;
}
function resetCleanup() {
  editForm.cleanup_mode = "";
}
function resetSave() {
  editForm.save_format = "";
  editForm.save_quality = "";
}
function resetSize() {
  editForm.max_width = "";
  editForm.max_height = "";
  editForm.lock_size = false;
  sizeTouched.value = false;  // 恢复默认 = 重新允许基础工作流值覆盖
  const lat = baseDefaults.value?.latent || {};
  if (lat.default_width) editForm.default_width = lat.default_width;
  if (lat.default_height) editForm.default_height = lat.default_height;
}
// 新版只读展示：底模 / 类型 / C站链接 都来自所选基础工作流
const selectedBaseKindLabel = computed(() => {
  const k = selectedBaseKind.value;
  return k === "img2img" ? "图生图" : (k === "t2i" ? "文生图" : "类型未知（未选基础工作流）");
});
const selectedBaseBasemodel = computed(
  () => selectedBase.value?.basemodel_name || "未关联（去「配置项」页补底模并在基础工作流里选择）"
);
const selectedBaseCivitai = computed(() => String(selectedBase.value?.civitai_url || "").trim());

// 绑定服务器下拉：来自插件配置的服务器列表（comfyui_servers）
// v7.1.0：**label = 服务器名字（+设备），value = 唯一 key（__template_key）**——
// 服务器改名不再让工作流绑定断链；旧数据里存的名字仍能按名字匹配（见后端 _resolve_server）。
const servers = ref<any[]>([]);
const serverOptions = computed(() => [
  { label: "默认服务器（未绑定）", value: "" },
  ...servers.value
    .filter((s: any) => String(s?.__template_key || "").trim())
    .map((s: any) => {
      const nm = String(s?.name || "").trim() || "(未命名服务器)";
      const dev = String(s?.device || "").trim();
      const def = s?.enabled ? "（默认）" : "";
      return { label: `${nm}${def}${dev ? `｜${dev}` : ""}`, value: String(s.__template_key) };
    }),
]);

/** 展示用：工作流绑定的是哪台服务器（key → 名字；老数据的名字也能显示并标注） */
function serverLabel(w: any): string {
  const key = String(w?.server_key || "").trim();
  if (key) {
    const s = servers.value.find((x: any) => String(x?.__template_key || "") === key);
    return s ? (String(s.name || "").trim() || key) : `未知服务器（key ${key.slice(0, 6)}…）`;
  }
  const legacy = String(w?.server_name || "").trim();
  if (legacy) {
    const hit = servers.value.find((x: any) => String(x?.name || "").trim() === legacy);
    return hit ? `${legacy}（旧版绑定）` : `${legacy}（旧版绑定·可能已改名）`;
  }
  return "默认服务器";
}
async function loadServers() {
  try {
    const cfg = await apiGet("config");
    servers.value = Array.isArray(cfg.comfyui_servers) ? cfg.comfyui_servers : [];
    ratioPresets.value = Array.isArray(cfg.draw_ratio) ? cfg.draw_ratio : [];
  } catch {
    servers.value = [];
    ratioPresets.value = [];
  }
}
// 尺寸档位 × 比例对照表（v7.0.18）：档位倍率由后端内置，比例基准来自 draw_ratio
const tierTable = ref<any>({ tiers: [], ratios: [] });
const tierModalShow = ref(false);
const FALLBACK_TIERS = [
  { key: "1k", label: "1K", budget_mp: 1.05 },
  { key: "1.5k", label: "1.5K", budget_mp: 2.36 },
  { key: "2k", label: "2K", budget_mp: 4.19 },
  { key: "4k", label: "4K", budget_mp: 8.81 },
];
const tierOptions = computed(() => {
  const ts = tierTable.value?.tiers?.length ? tierTable.value.tiers : FALLBACK_TIERS;
  return ts.map((t: any) => ({ label: `${t.label}（≈${t.budget_mp}MP）`, value: t.key }));
});
const tierColumns = computed(() => {
  const ts = tierTable.value?.tiers?.length ? tierTable.value.tiers : FALLBACK_TIERS;
  return [
    { title: "比例", key: "name", width: 120 },
    { title: "触发关键词", key: "keywords", ellipsis: { tooltip: true } },
    ...ts.map((t: any) => ({
      title: `${t.label}（≈${t.budget_mp}MP）`,
      key: `tier_${t.key}`,
      width: 125,
      render: (r: any) => (r.enabled === false ? "（已停用）" : (r.sizes?.[t.key] || "—")),
    })),
  ];
});
const tierRows = computed(() =>
  (tierTable.value?.ratios || []).map((r: any, i: number) => ({ key: i, ...r }))
);
async function loadTierTable() {
  try {
    tierTable.value = await apiGet("size_tiers");
  } catch {
    tierTable.value = { tiers: [], ratios: [] };
  }
}
function openTierTable() {
  if (!tierTable.value?.tiers?.length) loadTierTable();
  tierModalShow.value = true;
}

// 尺寸比例预设（全局配置 draw_ratio）：v7.0.17 起在「尺寸」页可直接套用
const ratioPresets = ref<any[]>([]);
const sizePresetOptions = computed(() =>
  ratioPresets.value
    .filter((p: any) => p && p.enabled !== false && p.width && p.height)
    .map((p: any) => ({
      label: `${p.name || "未命名"}（${p.width}×${p.height}）`,
      value: `${p.width}x${p.height}`,
    }))
);
function applySizePreset(v: string | null) {
  const [w, h] = String(v || "").split("x").map((x) => Number(x));
  if (!w || !h) return;
  editForm.default_width = w;
  editForm.default_height = h;
  markSizeTouched();
  message.success(`已套用尺寸预设 ${w}×${h}`);
}
/** 跳配置页并**自动展开/定位**到「尺寸比例预设」分组（v7.0.19，别再让用户自己找） */
function goConfig() {
  tierModalShow.value = false;
  router.push({ path: "/config", query: { group: "尺寸比例预设" } });
}
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
  loadBasemodelNames();
  loadServers();
  loadUpscaleOptions();
  loadTierTable();
}

// 选定基础工作流后回填「默认宽高」（业务参数，直接取值；采样器/放大/保存等
// 走「留空=跟随」语义，不做填充，只在表单里显示跟随值）
watch(
  () => editForm.base_id,
  (nv) => {
    if (!String(nv || "").trim()) return;
    applyBaseSize();
    // 若基础图保存节点没有 quality/output_ext 能力，清掉不可用的保存覆盖
    const save = selectedBase.value?.roles?.save || {};
    if (!save.has_quality) editForm.save_quality = "";
    if (!save.has_output_ext) editForm.save_format = "";
  }
);

// 基础工作流列表/详情是异步加载的：选定时可能还没拿到解析结果，
// 数据到位后再补一次宽高回填（否则会停在 512/空值）。
watch(selectedBase, () => applyBaseSize());

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
  const hay = [w.name, w.aliases, w.base_model, w.server_key, w.server_name, serverLabel(w), w.workflow_name, w.loras_text]
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
const starredCount = computed(
  () => scopedWorkflows.value.filter((w) => w.starred === true).length
);

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

/** 本页可见的工作流（新版页看 base_id 非空的，旧版页看其余的） */
const scopedWorkflows = computed(() =>
  workflows.value.filter((w) => (legacy.value ? isLegacyEntry(w) : !isLegacyEntry(w)))
);

const filteredWorkflows = computed(() => {
  const kw = searchText.value.trim().toLowerCase();
  const rows = workflows.value
    .map((w, i) => ({ w, i }))
    .filter(({ w }) => {
      // 页面范围（新版 / 旧版）
      if (legacy.value ? !isLegacyEntry(w) : isLegacyEntry(w)) return false;
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

// LoRA 下拉选项：按工作流**底模**筛选（v7.5.3 修复）。
// 此前筛的是 editForm.base_model，而新版工作流不再用这个字段（走 base_id 关联基础工作流），
// 它恒为空 → 过滤条件恒真 → 下拉把全部 LoRA 都列了出来。
const curBaseModel = computed(() => {
  const viaBase = String(selectedBase.value?.basemodel_name || "").trim();
  if (viaBase) return viaBase;                       // 新版：基础工作流的底模
  return String((editForm as any).base_model || "").trim();  // 旧版：工作流自己的 base_model
});
/** 当前底模是否来自基础工作流（决定提示文案） */
const curBaseFromBase = computed(() => !!String(selectedBase.value?.basemodel_name || "").trim());

/** LoRA 底模是否匹配工作流底模：任一侧为空（通用 / 不限）→ 匹配；
 *  否则先比小写原文，再比「去标点」形式（空格/连字符/下划线/点都不算），
 *  最后容错前缀 / 包含：底模库名「Anima Pencil XL」↔ LoRA 里记的「anima」、
 *  「Z-Image Turbo」↔「z-image-turbo」、「Krea 2」↔「krea2」都能对上。 */
function baseModelFits(loraBm: any, wfBm: any): boolean {
  const low = (v: any) => String(v || "").trim().toLowerCase();
  const a0 = low(loraBm);
  const b0 = low(wfBm);
  if (!a0 || !b0) return true;          // 通用 / 不限底模 → 互相都算可用
  if (a0 === b0) return true;
  const flat = (v: any) => low(v).replace(/[^0-9a-z\u4e00-\u9fa5]/g, "");
  const a = flat(loraBm);
  const b = flat(wfBm);
  if (!a || !b) return false;
  return a === b || a.startsWith(b) || b.startsWith(a) || a.includes(b) || b.includes(a);
}

/** 底模筛选开关（新版 LoRA 页）；关掉可看全库，便于临时混搭 */
const loraFilterByBase = ref(true);

const loraOptions = computed(() => {
  const known = new Set<string>();
  const wbm = curBaseModel.value;
  const byBase = loras.value
    .filter((l) => String(l.name || "").trim() && baseModelFits(l.base_model, wbm))
    .map((l) => {
      const n = String(l.name || "").trim();
      known.add(n);
      return { label: n, value: n };
    });
  // 兜底：该底模下确实没有匹配的 LoRA（或用户关掉筛选）→ 退回全量，别让下拉是空的；
  // 全量时把每个 LoRA 的底模标在名字后面，方便一眼看出为什么它不在「匹配」里。
  const useAll = !loraFilterByBase.value || byBase.length === 0;
  const opts = useAll
    ? loras.value
        .filter((l) => String(l.name || "").trim())
        .map((l) => {
          const n = String(l.name || "").trim();
          const lbm = String(l.base_model || "").trim();
          known.add(n);
          return { label: lbm ? `${n}（${lbm}）` : n, value: n };
        })
    : byBase;
  // 已选但库中不存在 / 底模不匹配的名称（保留老配置可编辑，标记未知）
  for (const row of editForm.loraList || []) {
    const n = (row.name || "").trim();
    if (n && !known.has(n)) {
      known.add(n);
      opts.push({ label: `${n}（库中不存在或不匹配当前底模）`, value: n });
    }
  }
  return opts;
});

/** LoRA 下拉下方的筛选状态提示（让「少了一些 LoRA」是可见、可解释的） */
const loraFilterHint = computed(() => {
  const total = loras.value.filter((l) => String(l.name || "").trim()).length;
  if (!loraFilterByBase.value) return `已关闭底模筛选：显示全部 ${total} 个 LoRA`;
  if (!curBaseModel.value) return "当前工作流未关联底模：显示全部 LoRA（关联底模后会自动筛选）";
  const hit = loras.value.filter(
    (l) => String(l.name || "").trim() && baseModelFits(l.base_model, curBaseModel.value)
  ).length;
  if (!hit) return `底模「${curBaseModel.value}」下没有匹配的 LoRA：已显示全部 ${total} 个`;
  const from = curBaseFromBase.value ? "（来自基础工作流）" : "";
  return `已按底模「${curBaseModel.value}」${from}筛选：${hit} / ${total} 个可用`;
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
  // v7.5.3：新版工作流底模看基础工作流（base_id → basemodel_name），旧版才看 w.base_model
  const viaBase = String(baseOf(w)?.basemodel_name || "").trim();
  const wbm = viaBase || String(w.base_model || "").trim();
  return loras.value
    .filter((l) => baseModelFits(l.base_model, wbm))
    .map((l) => l.name || "")
    .filter(Boolean);
}

// 大图详情（全屏：左侧封面，右侧字段信息）；封面导航列表
const previewShow = ref(false);
const coverImages = ref<{ fname: string; title: string; fields: ItemViewerField[] }[]>([]);
const coverIndex = ref(0);

// 由工作流对象构造封面查看项（导航用）
function buildCover(w: any): { fname: string; title: string; fields: ItemViewerField[] } {
  const isNew = !!String(w.base_id || "").trim();
  const base = baseOf(w);
  const size = (w.default_width && w.default_height)
    ? `${w.default_width} × ${w.default_height}` : "—";
  const fields: ItemViewerField[] = isNew ? [
    {
      key: "类型",
      value: (wfIsImg2Img(w) ? "图生图" : "文生图")
        + (baseOf(w)?.roles?.multi_image ? "（多参）" : ""),
    },
    { key: "基础工作流", value: base?.name || `#${w.base_id}` },
    { key: "底模", value: base?.basemodel_name || "未关联底模" },
    { key: "服务器", value: serverLabel(w) },
    { key: "状态", value: w.enabled === false ? "已停用" : "启用" },
    { key: "描述", value: (w.desc || "").trim() || "—" },
    { key: "默认尺寸", value: size },
    { key: "预设 LoRA", value: w.loras_text?.trim() || "—" },
    { key: "封面文件", value: w.image || "（未设置，可点卡片上的「默认封面」取基础工作流封面）" },
  ] : [
    { key: "名称", value: w.name },
    { key: "别名", value: aliasStr(w.aliases || "") },
    { key: "底模", value: w.base_model?.trim() || "通用" },
    { key: "服务器", value: serverLabel(w) },
    { key: "工作流文件", value: w.workflow_name?.trim() || "—" },
    { key: "Anima 模式", value: w.is_anima ? "是" : "否" },
    { key: "默认尺寸", value: size },
    { key: "可用 LoRA", value: availLoras(w).join("、") || "无匹配 LoRA" },
    { key: "预设 LoRA", value: w.loras_text?.trim() || "—" },
    { key: "封面文件", value: w.image || "—" },
  ];
  const civ = base?.civitai_url || w.civitai_url;
  if (civ) fields.push({ key: "C 站", value: civ, href: civ });
  return { fname: w.image || "", title: w.name || "", fields };
}

// 打开详情（支持左右箭头在封面列表间导航）；v7.0.15：没有封面也允许打开看字段
function openImage(idx: number) {
  const w = workflows.value[idx];
  if (!w) return;
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
    desc: w.desc || "",
    base_model: w.base_model || "",
    aliases: w.aliases || "",
    // v7.1.0：绑定值改用服务器唯一 key（旧数据里是名字 → 打开时自动迁移成 key，
    // 保存后即完成升级；后端两种都认，所以没迁移也不会坏）
    server_key: (() => {
      const _k = String(w.server_key || "").trim();
      if (_k) return _k;
      const _old = String(w.server_name || "").trim();
      if (!_old) return "";
      const _hit = servers.value.find((x: any) => String(x?.name || "").trim() === _old);
      return String(_hit?.__template_key || "");
    })(),
    // 旧字段保持原值（只读保留，避免数据丢失）
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
    // v7.0.13：tag+clearable 的下拉「未选」必须用 null——空字符串会被 naive-ui
    // 当成一个已选值：输入框显示空白、悬浮却出现清除叉叉（用户实测反馈）。
    ov_sampler: w.ov_sampler || null,
    ov_scheduler: w.ov_scheduler || null,
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
    // v7.0.11：新版工作流默认宽高不再兜底 512（512 会被当成真实默认值注入），
    // 留空 = 跟随基础工作流解析出的宽高；旧版条目保持 512 兜底行为。
    default_width: (w.base_id || "").trim()
      ? (w.default_width || null)
      : (w.default_width ?? 512),
    default_height: (w.base_id || "").trim()
      ? (w.default_height || null)
      : (w.default_height ?? 512),
    max_width: w.max_width || "",
    max_height: w.max_height || "",
    // v7.0.18：最高支持尺寸档位（旧数据缺省=2k，与后端默认一致）
    max_size_tier: w.max_size_tier || "2k",
    lock_size: !!w.lock_size,
    image_node: w.image_node || "",
    max_refs: Number(w.max_refs) > 0 ? Number(w.max_refs) : 0,
    lora_anchor: w.lora_anchor || "",
    lora_clip: w.lora_clip || "",
    upscale_node_id: w.upscale_node_id || "",
    // 同上：放大模型是 tag+clearable 下拉，空值用 null 才是「未选」
    upscale_model_name: w.upscale_model_name || null,
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
  // 打开弹窗时重置「宽高手动改过」标记：已有显式宽高的记录视为已定，不覆盖
  sizeTouched.value = !!(w.default_width && w.default_height);
  formTab.value = "basic";
  editShow.value = true;
  // 基础工作流解析数据可能晚于弹窗打开（列表异步），再补一次回填
  applyBaseSize();
}

function addWorkflow() {
  if (legacy.value) {
    message.warning("旧版页面不支持新增，请到「工作流」页新建");
    gotoNew();
    return;
  }
  openForm(-1);
}
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
  // 新版工作流必须引用基础工作流（节点自动定位的前提）
  if (!legacy.value && !String(editForm.base_id || "").trim()) {
    message.warning("请选择「基础工作流」——新版工作流的节点由它自动定位");
    return;
  }
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

/**
 * 新版：取「基础工作流」的封面作为本工作流封面（v7.0.15）
 * 只复制文件名（同一张图，不重复占空间）；基础工作流换封面后不会自动同步，可再次点此按钮。
 */
function useBaseCover(idx: number) {
  const w = workflows.value[idx];
  if (!w) return;
  const base = baseOf(w);
  if (!base) { message.warning("该工作流未关联基础工作流，无法取默认封面"); return; }
  if (!base.image) {
    message.warning(`基础工作流「${base.name}」还没有封面：请先到「基础工作流」页设置它的封面`);
    return;
  }
  dialog.info({
    title: "使用默认封面",
    content: `将把基础工作流「${base.name}」的封面复制一份作为本工作流的封面。`
      + "（基础工作流以后换封面不会自动同步，可再点一次本按钮同步）",
    positiveText: "使用",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("workflows/use_base_cover", { name: w.name });
        message.success("封面已设置");
        await load();
      } catch (e: any) {
        message.error(e?.message || "设置封面失败");
      }
    },
  });
}

// ---------------- 旧版工作流 → 新版（v7.4.0） ----------------
const converting = ref(false);

function fmtConvertResult(d: any): { ok: string[]; bad: string[] } {
  const ok = (Array.isArray(d?.converted) ? d.converted : [])
    .map((x: any) => `${x.name} → 基础工作流「${x.base}」${x.image ? "（含封面）" : ""}`);
  const bad = (Array.isArray(d?.failed) ? d.failed : [])
    .map((x: any) => `${x.name}：${x.reason}`);
  return { ok, bad };
}

async function doConvert(payload: Record<string, any>) {
  converting.value = true;
  try {
    const d = await apiPost("legacyworkflows/convert", payload, { timeout: 120000 });
    const { ok, bad } = fmtConvertResult(d);
    if (ok.length && !bad.length) {
      message.success(`已转换 ${ok.length} 条：${ok.join("；")}`);
    } else if (ok.length && bad.length) {
      dialog.warning({
        title: `部分转换成功（成功 ${ok.length} / 未转换 ${bad.length}）`,
        content: `已转换：${ok.join("；")}。未转换：${bad.join("；")}`,
        positiveText: "知道了",
      });
    } else {
      dialog.error({
        title: "转换失败",
        content: bad.join("；") || "没有可转换的旧版工作流",
        positiveText: "知道了",
      });
    }
    await load();
  } catch (e: any) {
    dialog.error({ title: "转换失败", content: e?.message || "请求失败", positiveText: "知道了" });
  } finally {
    converting.value = false;
  }
}

function convertLegacy(idx: number) {
  const w = workflows.value[idx];
  if (!w) return;
  const file = String(w.workflow_name || "").trim();
  dialog.info({
    title: "转换为新版工作流",
    content: `将按「工作流文件名」${file ? `（${file}）` : "（未填写）"}匹配同名基础工作流；`
      + "匹配到才能转换。转换后该条目从「旧版工作流」页消失、出现在「工作流」页，"
      + "封面会保留（原本没封面则取基础工作流的封面）。",
    positiveText: "开始转换",
    negativeText: "取消",
    onPositiveClick: () => doConvert({ name: w.name }),
  });
}

function convertAllLegacy() {
  const n = scopedWorkflows.value.length;
  if (!n) { message.info("没有旧版工作流"); return; }
  dialog.info({
    title: "批量转换为新版",
    content: `将尝试转换本页全部 ${n} 条旧版工作流：能按文件名匹配到基础工作流的会转换，`
      + "其余保持原样并列出原因。",
    positiveText: "开始转换",
    negativeText: "取消",
    onPositiveClick: () => doConvert({ all: true }),
  });
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
.cover-empty { color: var(--text-sub); font-size: 12px; text-align: center; display: flex; flex-direction: column; gap: 4px; }
.cover-empty-tip { font-size: 11px; color: var(--accent); opacity: 0.85; }
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

/* v7.0.9：表单项内的「基础工作流：X」默认值提示另起一行（不挤在输入框右侧） */
.form-grid :deep(.n-form-item-blank) { flex-wrap: wrap; }
.form-grid .form-hint {
  flex-basis: 100%;
  margin-left: 0;
  margin-top: 2px;
  line-height: 1.5;
}

@media (max-width: 768px) {
  .workflows-view { padding: 0; }
  .view-head { flex-direction: column; align-items: stretch; gap: 10px; }
  .view-actions { flex-wrap: wrap; }
  .view-actions :deep(.n-button) { flex: 1 1 auto; }
  .card-grid { grid-template-columns: 1fr; }
  .form-grid { grid-template-columns: 1fr; }
}
</style>
