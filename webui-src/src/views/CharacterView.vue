<template>
  <div class="char-page">
    <!-- 概览 -->
    <div class="stat-row">
      <div class="stat-card"><div class="stat-num">{{ characters.length }}</div><div class="stat-label">角色卡片</div></div>
      <div class="stat-card"><div class="stat-num">{{ anchorTotal }}</div><div class="stat-label">锚点总数</div></div>
      <div class="stat-card"><div class="stat-num">{{ boundCount }}</div><div class="stat-label">已绑定人格</div></div>
      <div class="stat-card"><div class="stat-num">{{ loraCount }}</div><div class="stat-label">关联 LoRA</div></div>
    </div>

    <!-- 工具栏 -->
    <n-card class="tool-card" :bordered="false">
      <div class="tool-grid">
        <n-input
          v-model:value="keyword"
          placeholder="搜索角色名 / 别名 / 作品 / 人格"
          clearable
          @keyup.enter="reload"
        />
        <div class="tool-actions">
          <n-radio-group v-model:value="viewMode" size="small">
            <n-radio-button value="card">卡片</n-radio-button>
            <n-radio-button value="table">表格</n-radio-button>
          </n-radio-group>
          <n-popover v-model:show="actorOpen" trigger="click" placement="bottom-end" style="width: 280px">
            <template #trigger>
              <n-button size="small" :type="myActor ? 'default' : 'warning'" ghost>
                👤 {{ myActor || '点我设置身份' }}
              </n-button>
            </template>
            <div class="actor-pop">
              <div class="dim">
                面板只有访问口令、没有登录用户，所以「谁建的」由你自己报：填了之后在本面板建的卡、锚点、上传的图都会记成你。
              </div>
              <n-input v-model:value="actorName" size="small" placeholder="昵称（如 云端之风）" style="margin-top: 8px" />
              <n-input v-model:value="actorQq" size="small" placeholder="QQ 号（如 1479221500）" style="margin-top: 6px" />
              <n-space justify="end" style="margin-top: 10px">
                <n-button size="tiny" @click="actorOpen = false">取消</n-button>
                <n-button size="tiny" type="primary" @click="saveActor">保存</n-button>
              </n-space>
            </div>
          </n-popover>
          <n-tooltip trigger="hover">
            <template #trigger>
              <span class="blur-switch">
                NSFW 打码<n-switch v-model:value="blurGlobal" size="small" style="margin-left: 6px" />
              </span>
            </template>
            打码口径与图库一致（当前阈值 {{ nsfwThreshold }}）；点开放大后右下角可临时解除
          </n-tooltip>
          <n-button type="primary" @click="reload">查询</n-button>
          <n-button type="primary" ghost @click="openCreate">＋ 新建角色</n-button>
          <n-button @click="doExport">导出 JSON</n-button>
          <n-button @click="importOpen = true">导入 JSON</n-button>
        </div>
      </div>
      <div class="tip">
        角色卡片是绘图时的<strong>最高优先级来源</strong>：命中后插件自动注入锚点标签，多人时自动生成
        <code>2girls</code> 计数标签与每个角色一个 <code>(标签:1.2)</code> 分组。
        也可以用对话设定（说「记住小叽的样子是…」）或 <code>/角色</code> 指令管理。
        <br />
        <strong>参考图</strong>：出图只要命中了某个锚点，成品图就会<strong>自动挂到那个锚点下</strong>
        （引用图库原文件，不额外占盘），也可点「详情 → 参考图 → 上传图片」拖拽上传；
        卡片、锚点、图片都记录<strong>创建者与创建方式</strong>，老数据未记的显示「未记录」。
        <strong>补全标签</strong>会调用 danbooru 标签服务给候选，人工确认后再落库。
      </div>
    </n-card>

    <!-- 列表 -->
    <n-card class="list-card" :bordered="false">
      <!-- 卡片视图（默认）：带封面的画廊式展示 -->
      <div v-if="viewMode === 'card'" class="char-grid">
        <div
          v-for="c in characters"
          :key="c.id"
          class="char-card"
          :class="{ 'char-off': !c.enabled }"
          @click="openDetail(c)"
        >
          <div class="char-cover">
            <img v-if="coverUrls[c.id]" :src="coverUrls[c.id]" :alt="c.name" loading="lazy" />
            <div v-else class="char-cover-ph">{{ (c.name || '?').slice(0, 2) }}</div>
            <n-tag v-if="c.persona_name" class="char-badge" size="tiny" type="success" :bordered="false">人格</n-tag>
            <n-tag v-if="!c.enabled" class="char-badge char-badge-right" size="tiny" :bordered="false">停用</n-tag>
          </div>
          <div class="char-body">
            <div class="char-title">
              <span class="char-name">{{ c.name }}</span>
              <n-tag v-if="c.aliases?.length" size="tiny" :bordered="false">{{ c.aliases[0] }}</n-tag>
            </div>
            <div class="char-meta">
              <span v-if="c.work" :title="c.work">📖 {{ c.work }}</span>
              <span>🎯 {{ (c.anchors || []).length }} 锚点</span>
              <span>🖼 {{ (c.refs || []).length }} 图</span>
            </div>
            <div class="char-anchor-names">{{ anchorNames(c) || '（无锚点）' }}</div>
            <div v-if="c.lora_name" class="char-lora" :title="c.lora_name">LoRA：{{ c.lora_name }}</div>
            <div class="char-by" :title="`创建方式：${srcLabel(c.source)}`">
              👤 {{ c.created_by || NO_REC }} · {{ fmtTime(c.created_at) }}
            </div>
            <div class="char-ops" @click.stop>
              <n-button size="tiny" type="primary" ghost @click="openDetail(c)">详情</n-button>
              <n-popconfirm @positive-click="removeCardById(c)">
                <template #trigger><n-button size="tiny" type="error" quaternary>删除</n-button></template>
                删除「{{ c.name }}」及其全部锚点与图片？此操作不可恢复。
              </n-popconfirm>
            </div>
          </div>
        </div>
      </div>
      <!-- 表格视图 -->
      <n-data-table
        v-else
        :columns="columns"
        :data="characters"
        :loading="loading"
        :row-key="(row: any) => row.id"
        size="small"
        :bordered="false"
      />
      <div v-if="!loading && !characters.length" class="empty">
        暂无角色卡片。点「＋ 新建角色」，或对 AI 说「记住XX的样子是…」。
      </div>
    </n-card>

    <!-- 详情抽屉（v6.3.0 改版：封面 + 元信息条 + 分区标签页） -->
    <n-drawer v-model:show="detailOpen" :width="720" placement="right">
      <n-drawer-content :title="detail ? `角色卡：${detail.name}` : '角色卡'" closable>
        <template v-if="detail">
          <div class="dt-head">
            <div class="dt-cover" title="点击查看大图" @click.stop="openCoverViewer">
              <img
                v-if="dtCoverUrl"
                :src="dtCoverUrl"
                :alt="detail.name"
                :class="{ 'nsfw-blur': isBlurred(dtCoverRef) }"
              />
              <div v-else class="dt-cover-ph">{{ (detail.name || '?').slice(0, 2) }}</div>
              <div v-if="isBlurred(dtCoverRef)" class="nsfw-mask"><span>🔞</span></div>
              <span v-if="dtCoverAuto" class="dt-cover-tag">自动</span>
            </div>
            <div class="dt-ident">
              <div class="dt-name">
                <span class="dt-name-txt">{{ detail.name }}</span>
                <n-tag v-for="al in (detail.aliases || []).slice(0, 3)" :key="al" size="tiny" :bordered="false">
                  {{ al }}
                </n-tag>
                <n-tag v-if="!detail.enabled" size="tiny" type="warning" :bordered="false">已停用</n-tag>
              </div>
              <div class="dt-chips">
                <n-tag v-if="detail.persona_name" size="small" type="success" :bordered="false">
                  人格 {{ detail.persona_name }}
                </n-tag>
                <n-tag v-if="detail.work" size="small" :bordered="false">作品 {{ detail.work }}</n-tag>
                <n-tag v-if="detail.lora_name" size="small" type="info" :bordered="false">
                  LoRA {{ detail.lora_name }}
                </n-tag>
              </div>
              <n-descriptions :column="2" label-placement="left" size="small" bordered class="dt-meta">
                <n-descriptions-item label="创建者">{{ detail.created_by || NO_REC }}</n-descriptions-item>
                <n-descriptions-item label="创建方式">{{ srcLabel(detail.source) }}</n-descriptions-item>
                <n-descriptions-item label="创建时间">{{ fmtTime(detail.created_at) }}</n-descriptions-item>
                <n-descriptions-item label="最近更新">{{ fmtTime(detail.updated_at) }}</n-descriptions-item>
                <n-descriptions-item label="主锚点">{{ primaryAnchorName }}</n-descriptions-item>
                <n-descriptions-item label="锚点 / 图片">
                  {{ (detail.anchors || []).length }} 个 / {{ (detail.refs || []).length }} 张
                </n-descriptions-item>
              </n-descriptions>
            </div>
          </div>

          <n-tabs type="line" size="small" class="dt-tabs">
            <!-- ---- 锚点：服装 / 形象提示词 ---- -->
            <n-tab-pane name="anchor" :tab="`锚点 · ${(detail.anchors || []).length}`">
              <div class="pane-bar">
                <n-button size="small" type="primary" ghost @click="openAnchorCreate">＋ 新增锚点</n-button>
                <n-button size="small" :loading="suggesting" @click="suggestTags">🔎 从标签服务补全</n-button>
                <span class="dim">锚点 = 这个角色的一套外观/服装提示词，出图命中谁就注入谁</span>
              </div>
              <div v-for="a in detail.anchors || []" :key="a.id" class="anchor-item">
                <div class="anchor-top">
                  <span class="anchor-name">{{ a.name }}</span>
                  <n-tag v-if="isPrimary(a)" size="small" type="success" :bordered="false">主锚点</n-tag>
                  <n-tag size="small" :bordered="false">{{ kindLabel(a.kind) }}</n-tag>
                  <n-tag size="small" :bordered="false">权重 {{ a.weight }}</n-tag>
                  <n-tag v-if="a.lora_name" size="small" type="info" :bordered="false">LoRA {{ a.lora_name }}</n-tag>
                  <span class="anchor-spacer"></span>
                  <n-button size="tiny" @click="openAnchorEdit(a)">编辑</n-button>
                  <n-button v-if="!isPrimary(a)" size="tiny" quaternary @click="setPrimary(a)">设为主锚点</n-button>
                  <n-button size="tiny" quaternary @click="openUpload(Number(a.id))">传图</n-button>
                  <n-popconfirm @positive-click="removeAnchor(a)">
                    <template #trigger><n-button size="tiny" type="error" quaternary>删除</n-button></template>
                    删除锚点「{{ a.name }}」？（其图片会保留，降级为角色级）
                  </n-popconfirm>
                </div>
                <div class="anchor-pos">{{ a.positive }}</div>
                <div v-if="a.negative" class="anchor-neg">负向：{{ a.negative }}</div>
                <div v-if="a.note" class="anchor-neg">备注：{{ a.note }}</div>
                <div class="rec-meta">
                  <span>👤 {{ a.created_by || NO_REC }}</span>
                  <span>{{ fmtTime(a.created_at) }}</span>
                  <n-tag size="tiny" :bordered="false">{{ srcLabel(a.source) }}</n-tag>
                  <span v-if="anchorRefs(a.id).length" class="dim">挂图 {{ anchorRefs(a.id).length }} 张</span>
                </div>
                <div v-if="anchorRefs(a.id).length" class="anchor-refs">
                  <div
                    v-for="r in anchorRefs(a.id)"
                    :key="r.id"
                    class="anchor-ref"
                    :class="{ 'ref-missing': r.exists === false }"
                    :title="`id=${r.id} ${originLabel(r.origin)} ${r.created_by || NO_REC} ${fmtTime(r.created_at)}`"
                  >
                    <img
                      v-if="refUrls[r.id]"
                      :src="refUrls[r.id]"
                      :alt="'ref ' + r.id"
                      loading="lazy"
                      class="ref-clickable"
                      :class="{ 'nsfw-blur': isBlurred(r) }"
                      @click.stop="openRefViewer(r)"
                    />
                    <div v-else class="anchor-ref-ph">{{ r.exists === false ? '缺' : '…' }}</div>
                    <div v-if="isBlurred(r)" class="nsfw-mask"><span>🔞</span></div>
                    <span v-if="r.is_cover" class="anchor-ref-cover">★</span>
                    <!-- 就地删除：锚点条目里看图时不必再切到「参考图」分区找按钮 -->
                    <button class="anchor-ref-del" title="删除这张图" @click.stop="removeRef(r)">✕</button>
                  </div>
                </div>
              </div>
              <div v-if="!(detail.anchors || []).length" class="empty">
                还没有锚点，点「＋ 新增锚点」添加（至少一个才能注入）。
              </div>
            </n-tab-pane>

            <!-- ---- 参考图 ---- -->
            <n-tab-pane name="ref" :tab="`参考图 · ${(detail.refs || []).length}`">
              <div class="pane-bar">
                <n-button size="small" type="primary" ghost @click="openUpload(0)">⬆ 上传图片</n-button>
                <span class="dim">
                  支持拖拽；出图命中锚点时插件会自动把成品图挂到对应锚点下
                  <template v-if="autoLink">
                    （当前：每锚点保留最近 {{ autoLink.keep || '不限' }} 张）
                  </template>
                </span>
              </div>
              <div v-for="g in refGroups" :key="g.key" class="ref-group">
                <div class="ref-group-head">
                  <span class="ref-group-name">{{ g.name }}</span>
                  <n-button size="tiny" quaternary @click="openUpload(g.anchorId)">＋ 上传到此处</n-button>
                </div>
                <div class="ref-grid">
                  <div
                    v-for="r in g.items"
                    :key="r.id"
                    class="ref-item"
                    :class="{ 'ref-is-cover': r.is_cover, 'ref-missing': r.exists === false }"
                  >
                    <img
                      v-if="refUrls[r.id]"
                      :src="refUrls[r.id]"
                      :alt="'ref ' + r.id"
                      loading="lazy"
                      class="ref-clickable"
                      :class="{ 'nsfw-blur': isBlurred(r) }"
                      title="点击查看原图"
                      @click.stop="openRefViewer(r)"
                    />
                    <div v-else class="ref-ph">{{ r.exists === false ? '文件已不在' : '加载中…' }}</div>
                    <div v-if="isBlurred(r)" class="nsfw-mask">
                      <span>🔞</span>
                      <span class="nsfw-mask-tip">点击查看</span>
                    </div>
                    <div class="ref-meta">
                      <span>#{{ r.id }}</span>
                      <n-tag size="tiny" :bordered="false" :type="originType(r.origin)">
                        {{ originLabel(r.origin) }}
                      </n-tag>
                      <n-tag v-if="r.is_cover" size="tiny" type="warning" :bordered="false">★封面</n-tag>
                      <n-tag v-else-if="r.is_cover_auto" size="tiny" :bordered="false">自动封面</n-tag>
                      <n-tag v-if="nsfwText(r)" size="tiny" :type="nsfwType(r)" :bordered="false">
                        {{ nsfwText(r) }}
                      </n-tag>
                    </div>
                    <div class="ref-who">
                      <span class="ref-who-name">{{ r.created_by || NO_REC }}</span>
                      <span class="ref-who-time">{{ fmtTime(r.created_at) }}</span>
                    </div>
                    <div class="ref-ops">
                      <n-button v-if="!r.is_cover" size="tiny" quaternary @click="setCover(r)">设为封面</n-button>
                      <n-button size="tiny" type="error" quaternary @click="removeRef(r)">删除</n-button>
                    </div>
                  </div>
                </div>
              </div>
              <div v-if="!(detail.refs || []).length" class="empty">
                还没有参考图。出图命中锚点后会自动挂在这里，也可以点「⬆ 上传图片」拖拽上传。
              </div>
            </n-tab-pane>

            <!-- ---- 身份与设置 ---- -->
            <n-tab-pane name="info" tab="身份与设置">
              <n-form label-placement="left" label-width="86" size="small">
                <n-form-item label="角色名"><n-input v-model:value="form.name" /></n-form-item>
                <n-form-item label="别名">
                  <n-input v-model:value="form.aliasesText" placeholder="逗号分隔，如：小叽酱,叽叽" />
                </n-form-item>
                <n-form-item label="绑定人格">
                  <n-select
                    v-model:value="form.persona_name"
                    :options="mergedPersonaOptions"
                    filterable
                    tag
                    clearable
                    placeholder="AstrBot 人格名（用户说「画你」时命中）"
                  />
                </n-form-item>
                <n-form-item label="作品"><n-input v-model:value="form.work" placeholder="如 Neverness to Everness" /></n-form-item>
                <n-form-item label="关联 LoRA">
                  <n-select
                    v-model:value="form.lora_name"
                    :options="mergedLoraOptions"
                    filterable
                    tag
                    clearable
                    placeholder="只列「角色」分类的 LoRA；也可直接输入名称"
                  />
                </n-form-item>
                <n-form-item label="备注"><n-input v-model:value="form.note" type="textarea" :rows="2" /></n-form-item>
                <n-form-item label="启用"><n-switch v-model:value="form.enabled" /></n-form-item>
                <n-form-item label=" ">
                  <n-space>
                    <n-button type="primary" :loading="saving" @click="saveCard">保存卡片</n-button>
                    <n-popconfirm @positive-click="removeCard">
                      <template #trigger><n-button type="error" ghost>删除角色</n-button></template>
                      删除「{{ detail.name }}」及其全部锚点？此操作不可恢复。
                    </n-popconfirm>
                  </n-space>
                </n-form-item>
              </n-form>
            </n-tab-pane>
          </n-tabs>
        </template>
      </n-drawer-content>
    </n-drawer>

    <!-- 上传参考图弹窗（v6.3.0：拖拽 + 多选 + 预览，替代原「点一下直接开系统文件框」） -->
    <n-modal v-model:show="uploadOpen" preset="card" :title="`上传参考图 · ${detail?.name || ''}`" style="max-width: 640px">
      <n-form label-placement="top" size="small">
        <n-form-item label="挂到哪个锚点（决定这张图属于哪套服装/形象）">
          <n-select v-model:value="uploadAnchorId" :options="uploadTargetOptions" />
        </n-form-item>
      </n-form>
      <n-upload
        multiple
        accept="image/*"
        :default-open="uploadOpen"
        :custom-request="onUploadRequest"
        :show-file-list="true"
        @clear="onUploadClear"
      >
        <n-upload-dragger>
          <div class="drop-title">把图片拖进来，或点击选择</div>
          <div class="drop-sub">
            可一次多张；PNG / JPG / WebP / GIF，单张上限 12MB，单次最多 {{ UPLOAD_MAX }} 张
          </div>
        </n-upload-dragger>
      </n-upload>
      <div class="up-status">
        <span v-if="uploading">上传中… 成功 {{ upOk }} 张，重复 {{ upDup }} 张，失败 {{ upErr }} 张</span>
        <span v-else-if="upDone" class="dim">
          本次已上传 {{ upOk }} 张<template v-if="upDup">（{{ upDup }} 张已存在，未重复入库）</template><template v-if="upErr">，失败 {{ upErr }} 张</template>
        </span>
        <span v-else class="dim">上传完成后会自动刷新角色卡；关闭弹窗即结束。</span>
      </div>
      <n-divider style="margin: 10px 0">从图库导入（按 sha，已有成品图不必重复上传）</n-divider>
      <div class="tool-grid">
        <n-input v-model:value="refSha" size="small" placeholder="图库图片的 sha（可在图库页复制）" />
        <n-button size="small" :disabled="!refSha.trim()" @click="importRefFromGallery">导入</n-button>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="closeUpload">完成</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 新建角色 -->
    <n-modal v-model:show="createOpen" preset="card" title="新建角色卡" style="max-width: 520px">
      <n-form label-placement="left" label-width="86" size="small">
        <n-form-item label="角色名"><n-input v-model:value="createForm.name" placeholder="如 薄荷" /></n-form-item>
        <n-form-item label="别名"><n-input v-model:value="createForm.aliasesText" placeholder="逗号分隔（可留空）" /></n-form-item>
        <n-form-item label="绑定人格">
          <n-select
            v-model:value="createForm.persona_name"
            :options="mergedPersonaOptions"
            filterable
            tag
            clearable
            placeholder="AstrBot 人格名（可留空）"
          />
        </n-form-item>
        <n-form-item label="作品"><n-input v-model:value="createForm.work" placeholder="可留空" /></n-form-item>
        <n-form-item label="关联 LoRA">
          <n-select
            v-model:value="createForm.lora_name"
            :options="mergedLoraOptions"
            filterable
            tag
            clearable
            placeholder="只列「角色」分类的 LoRA（可留空，也可直接输入）"
          />
        </n-form-item>
        <div v-if="!loraOptions.length" class="hint tip-inline">
          未取到「角色」分类的 LoRA：可去「LoRA」页把分类改为角色，或直接输入名称。
        </div>
        <n-divider style="margin: 6px 0 12px">首个锚点（必填）</n-divider>
        <n-form-item label="锚点名"><n-input v-model:value="createForm.anchor_name" placeholder="默认装" /></n-form-item>
        <n-form-item label="标签串">
          <n-input v-model:value="createForm.positive" type="textarea" :rows="4"
                   placeholder="英文 danbooru 标签，逗号分隔。例：mint_\\(nte\\), teal hair, long hair, red eyes, cat ears" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="createOpen = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="createCard">创建</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 锚点编辑 -->
    <n-modal v-model:show="anchorOpen" preset="card"
             :title="anchorForm.id ? '编辑锚点' : '新增锚点'" style="max-width: 560px">
      <n-form label-placement="left" label-width="96" size="small">
        <n-form-item label="锚点名"><n-input v-model:value="anchorForm.name" placeholder="默认装 / 泳装 / 校服" /></n-form-item>
        <n-form-item label="类型">
          <n-select v-model:value="anchorForm.kind" :options="kindOptions" />
        </n-form-item>
        <n-form-item label="标签串">
          <n-input v-model:value="anchorForm.positive" type="textarea" :rows="4"
                   placeholder="英文 danbooru 标签，逗号分隔（不要写自然语言长句、不要写权重）" />
        </n-form-item>
        <n-form-item label="负向标签">
          <n-input v-model:value="anchorForm.negative" type="textarea" :rows="2" placeholder="可留空" />
        </n-form-item>
        <n-form-item label="分组权重">
          <n-input-number v-model:value="anchorForm.weight" :min="1" :max="1.5" :step="0.05" style="width: 160px" />
          <span class="hint">多人分组时用，建议 1.1~1.3（不超 1.5）</span>
        </n-form-item>
        <n-form-item label="覆盖 LoRA">
          <n-select
            v-model:value="anchorForm.lora_name"
            :options="mergedLoraOptions"
            filterable
            tag
            clearable
            placeholder="可留空（优先于角色级 LoRA）"
          />
        </n-form-item>
        <n-form-item label="抑制触发词">
          <n-switch v-model:value="anchorForm.skip_trigger_words" />
          <span class="hint">开启时该 LoRA 触发词不再全局追加（锚点里已写就不重复）</span>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="anchorOpen = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveAnchor">保存</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 补全候选标签（需人工确认） -->
    <n-modal v-model:show="suggestOpen" preset="card" title="候选标签（需人工确认）" style="max-width: 620px">
      <div class="tip">
        来源：danbooru 标签服务（查询词「{{ suggestQuery }}」）。
        ⚠️ 自动结果可能含不适用的服装/场景词，请删掉后再落库；落库只是新增一个锚点，不会自动出图。
      </div>
      <n-input v-model:value="suggestText" type="textarea" :rows="5" style="margin-top: 8px" />
      <template #footer>
        <n-space justify="end">
          <n-button @click="suggestOpen = false">取消</n-button>
          <n-button type="primary" @click="useSuggestAsAnchor">用它新建锚点</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 导入 -->
    <ItemViewer
      v-model:show="viewerShow"
      :src="viewerSrc"
      :title="viewerTitle"
      :nsfw="viewerNsfw"
      :blur-global="blurGlobal"
    />
    <n-modal v-model:show="importOpen" preset="card" title="导入角色卡片 JSON" style="max-width: 620px">
      <n-input v-model:value="importText" type="textarea" :rows="10" placeholder='把导出的 JSON 粘贴到这里（{"version":1,"characters":[…] }）' />
      <template #footer>
        <n-space justify="end">
          <n-button @click="importOpen = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="doImport">导入</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from "vue";
import {
  NButton, NCard, NDataTable, NDivider, NDrawer, NDrawerContent, NDescriptions,
  NDescriptionsItem, NForm, NFormItem, NInput, NInputNumber, NModal, NPopconfirm,
  NPopover, NSelect, NSpace, NSwitch, NTabPane, NTabs, NTag, NTooltip, NUpload,
  NUploadDragger, useDialog, useMessage,
  type UploadCustomRequestOptions,
} from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import { lsGet, lsSet } from "@/api/storage";
import ItemViewer from "@/components/ItemViewer.vue";
import { fmtTime } from "@/utils/format";

const message = useMessage();
const dialog = useDialog();

/** 老数据没记创建者（v6.3.0 才有该字段），统一显示成这个，别用空白糊弄过去。 */
const NO_REC = "未记录";
/** 单次上传张数上限（与后端 5 张/批的宽松限制解耦， purely 前端体验约束）。 */
const UPLOAD_MAX = 10;

/** `source`（角色卡/锚点的创建渠道）→ 中文。未知值原样显示，便于发现新渠道没映射。 */
function srcLabel(src: string | undefined | null): string {
  const k = (src || "").trim().toLowerCase();
  const m: Record<string, string> = {
    webui: "WebUI 创建",
    command: "指令创建",
    llm: "AI 对话创建",
    import: "导入",
    user: NO_REC,
  };
  return m[k] || (k ? src! : NO_REC);
}

/** `origin`（参考图入库渠道）→ 中文。 */
function originLabel(o: string | undefined | null): string {
  const k = (o || "").trim().toLowerCase();
  const m: Record<string, string> = {
    upload: "上传",
    command: "指令录入",
    tool: "AI 录入",
    gallery: "图库导入",
    auto: "出图自动关联",
    web: "联网抓取",
    import: "导入",
  };
  return m[k] || (k || "上传");
}

function originType(o: string | undefined | null): "default" | "info" | "success" | "warning" {
  const k = (o || "").trim().toLowerCase();
  if (k === "auto") return "info";
  if (k === "upload") return "success";
  if (k === "web" || k === "import") return "warning";
  return "default";
}

function kindLabel(k: string | undefined | null): string {
  return ({ full: "外观+服装", appearance: "仅外观", outfit: "仅服装" } as Record<string, string>)[
    (k || "full") as string
  ] || "外观+服装";
}

const loading = ref(false);
const saving = ref(false);
const keyword = ref("");
const characters = ref<any[]>([]);

const detailOpen = ref(false);
const detail = ref<any>(null);
const createOpen = ref(false);
const anchorOpen = ref(false);
const importOpen = ref(false);
const importText = ref("");
/** 后端 character_card 的自动关联配置（列表接口带回来，只用于页面上的说明文案）。 */
const autoLink = ref<{ enabled: boolean; keep: number; cover: boolean } | null>(null);

// ---------------- 操作者身份（v6.3.1）----------------
// 面板只有口令鉴权、没有「用户」概念，所以创建者由使用者自报：存本地、随写入上报，
// 后端只做规范化（限长/去控制字符），空则回落「WebUI 控制台」。
const ACTOR_NAME_KEY = "anima_actor_name";
const ACTOR_QQ_KEY = "anima_actor_qq";
const actorName = ref(lsGet(ACTOR_NAME_KEY) || "");
const actorQq = ref(lsGet(ACTOR_QQ_KEY) || "");
const actorOpen = ref(false);
const myActor = computed(() => {
  const n = actorName.value.trim();
  const q = actorQq.value.trim();
  if (n && q) return `${n}(${q})`;
  if (n) return n;
  return q ? `QQ:${q}` : "";
});

function saveActor() {
  lsSet(ACTOR_NAME_KEY, actorName.value.trim());
  lsSet(ACTOR_QQ_KEY, actorQq.value.trim());
  actorOpen.value = false;
  message.success(
    myActor.value ? `以后在面板创建的内容会记为「${myActor.value}」` : "已清除身份，将记为「WebUI 控制台」"
  );
}

// ---------------- NSFW 打码（v6.3.1，口径与图库一致）----------------
const BLUR_KEY = "anima_char_nsfw_blur";
const nsfwThreshold = ref(0.5);
const blurGlobal = ref(lsGet(BLUR_KEY) == null ? true : lsGet(BLUR_KEY) === "1");
watch(blurGlobal, (v) => lsSet(BLUR_KEY, v ? "1" : "0"));

/** 分数未知（-1，未跑过检测）不打码；只有明确 ≥ 阈值才打码。 */
function isNsfw(r: any): boolean {
  const s = Number(r?.nsfw_score ?? -1);
  return s >= 0 && s >= nsfwThreshold.value;
}

function isBlurred(r: any): boolean {
  return blurGlobal.value && isNsfw(r);
}

// v6.1.2：下拉框的「空」必须是 null，不能是空字符串——
// `n-select` 只要 value 非 null 就认为「有值」，会显示清空按钮（看着空、其实得手点清空）。
/** 下拉值统一判空：null/undefined → 空串（提交给后端前用）。 */
const nv = (v: unknown) => String(v ?? "").trim();
type NullableStr = string | null;

const form = reactive({
  name: "", aliasesText: "", persona_name: null as NullableStr, work: "",
  lora_name: null as NullableStr, note: "", enabled: true,
});
const createForm = reactive({
  name: "", aliasesText: "", persona_name: null as NullableStr, work: "",
  lora_name: null as NullableStr,
  anchor_name: "默认装", positive: "",
});
const anchorForm = reactive({
  id: 0, name: "默认装", kind: "full", positive: "", negative: "",
  weight: 1.2, lora_name: null as NullableStr, skip_trigger_words: true,
});

const kindOptions = [
  { label: "外观 + 服装（推荐）", value: "full" },
  { label: "仅外观", value: "appearance" },
  { label: "仅服装/造型", value: "outfit" },
];

const anchorTotal = computed(() => characters.value.reduce((n, c) => n + (c.anchors?.length || 0), 0));
const boundCount = computed(() => characters.value.filter((c) => (c.persona_name || "").trim()).length);
const loraCount = computed(() => characters.value.filter((c) => (c.lora_name || "").trim()).length);

const columns = [
  { title: "角色名", key: "name", width: 140 },
  {
    title: "封面", key: "cover", width: 74,
    render: (row: any) =>
      coverUrls[row.id]
        ? h("img", {
            src: coverUrls[row.id],
            alt: row.name,
            style: "width:48px;height:48px;object-fit:cover;border-radius:6px;display:block",
          })
        : h("span", { style: "opacity:.4" }, "—"),
  },
  {
    title: "别名", key: "aliases", width: 160,
    render: (row: any) => (row.aliases || []).join("、") || "—",
  },
  {
    title: "绑定人格", key: "persona_name", width: 130,
    render: (row: any) => row.persona_name || "—",
  },
  { title: "作品", key: "work", width: 150, render: (row: any) => row.work || "—" },
  { title: "关联 LoRA", key: "lora_name", width: 120, render: (row: any) => row.lora_name || "—" },
  {
    title: "创建者", key: "created_by", width: 130,
    render: (row: any) => row.created_by || NO_REC,
  },
  {
    title: "创建方式", key: "source", width: 96,
    render: (row: any) => srcLabel(row.source),
  },
  {
    title: "创建时间", key: "created_at", width: 130,
    render: (row: any) => fmtTime(row.created_at),
  },
  {
    title: "锚点", key: "anchor_count", width: 150,
    render: (row: any) => {
      const names = (row.anchors || []).map((a: any) => a.name).join("、");
      return `${row.anchors?.length || 0} 个${names ? "：" + names : ""}`;
    },
  },
  {
    title: "参考图", key: "refs", width: 90,
    render: (row: any) => `${(row.refs || []).length} 张`,
  },
  {
    title: "操作", key: "ops", width: 130,
    render: (row: any) =>
      h(NSpace, { size: 4 }, {
        default: () => [
          h(NButton, { size: "tiny", onClick: () => openDetail(row) }, { default: () => "查看/编辑" }),
        ],
      }),
  },
];

function isPrimary(a: any) {
  return detail.value && Number(detail.value.primary_anchor_id) === Number(a.id);
}

async function reload() {
  loading.value = true;
  try {
    const data = await apiGet("character/list", { keyword: keyword.value.trim() });
    characters.value = data?.characters || [];
    autoLink.value = data?.auto_link || null;
    if (data?.nsfw?.threshold != null) nsfwThreshold.value = Number(data.nsfw.threshold);
    // 用户没手动拧过开关时，跟随插件配置的图库 NSFW 默认值
    if (lsGet(BLUR_KEY) == null && data?.nsfw) blurGlobal.value = data.nsfw.blur_default !== false;
    loadCovers();
  } catch (e: any) {
    message.error(`读取角色卡片失败：${e?.message || e}`);
  } finally {
    loading.value = false;
  }
}

// ---------------- 视图模式（默认卡片）----------------
const VIEW_KEY = "anima_char_view";
const viewMode = ref<"card" | "table">(
  (() => {
    try {
      return localStorage.getItem(VIEW_KEY) === "table" ? "table" : "card";
    } catch {
      return "card";
    }
  })(),
);
watch(viewMode, (v) => {
  try {
    localStorage.setItem(VIEW_KEY, v);
  } catch {
    /* 忽略隐私模式等 */
  }
  loadCovers();
});

// ---------------- 封面（v6.1.0）----------------
const coverUrls = reactive<Record<number, string>>({});

function coverRef(c: any) {
  const refs: any[] = c?.refs || [];
  return refs.find((r) => r.is_cover) || refs.find((r) => r.is_cover_auto) || null;
}

function anchorNames(c: any) {
  return (c?.anchors || []).map((a: any) => a.name).join("、");
}

/** 拉角色封面缩略图（限 4 并发，避免一次几十个请求打满）。 */
async function loadCovers() {
  const todo = characters.value
    .map((c: any) => ({ cid: Number(c.id), ref: coverRef(c) }))
    .filter((x) => x.ref && !coverUrls[x.cid]);
  if (!todo.length) return;
  let idx = 0;
  const worker = async () => {
    while (idx < todo.length) {
      const it = todo[idx++];
      try {
        const d = await apiGet("character/ref/image", { id: it.ref.id, size: 320 });
        if (d?.url) coverUrls[it.cid] = d.url;
      } catch {
        /* 单张失败不影响其他 */
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(4, todo.length) }, worker));
}

async function removeCardById(c: any) {
  try {
    await apiPost("character/delete", { id: c.id });
    delete coverUrls[c.id];
    message.success("已删除");
    await reload();
  } catch (e: any) {
    message.error(`删除失败：${e?.message || e}`);
  }
}

function fillForm(c: any) {
  form.name = c?.name || "";
  form.aliasesText = (c?.aliases || []).join(",");
  // 下拉字段：空 → null（不是空串，否则 n-select 会显示清空按钮）
  form.persona_name = (c?.persona_name || "").trim() || null;
  form.work = c?.work || "";
  form.lora_name = (c?.lora_name || "").trim() || null;
  form.note = c?.note || "";
  form.enabled = c?.enabled !== false;
  // 历史自定义值不在候选里时补进选项，避免下拉显示成空
  rememberExtra(nv(form.lora_name), nv(form.persona_name));
}

async function openDetail(row: any) {
  try {
    const c = await apiGet("character/detail", { id: row.id });
    detail.value = c;
    fillForm(c);
    detailOpen.value = true;
    uploadAnchorId.value = 0; // 上传目标默认回到「角色级」，避免误挂到上次选的锚点
    loadRefUrls(c?.refs || []);
  } catch (e: any) {
    message.error(`读取详情失败：${e?.message || e}`);
  }
}

function openCreate() {
  createForm.name = "";
  createForm.aliasesText = "";
  createForm.persona_name = null; // 下拉空值 = null（空串会被 n-select 当成有值）
  createForm.work = "";
  createForm.lora_name = null;
  createForm.anchor_name = "默认装";
  createForm.positive = "";
  createOpen.value = true;
}

function splitAliases(text: string) {
  return (text || "")
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

async function createCard() {
  if (!createForm.name.trim()) return message.warning("请填角色名");
  if (!createForm.positive.trim()) return message.warning("请填首个锚点的标签串");
  saving.value = true;
  try {
    const r = await apiPost("character/save", {
      name: createForm.name.trim(),
      aliases: splitAliases(createForm.aliasesText),
      persona_name: nv(createForm.persona_name),
      work: createForm.work.trim(),
      lora_name: nv(createForm.lora_name),
      created_by: myActor.value,
    });
    await apiPost("character/anchor/save", {
      character_id: r.id,
      anchor_name: createForm.anchor_name.trim() || "默认装",
      positive: createForm.positive.trim(),
      kind: "full",
      weight: 1.2,
      created_by: myActor.value,
    });
    createOpen.value = false;
    message.success(`已创建「${createForm.name.trim()}」`);
    await reload();
  } catch (e: any) {
    message.error(`创建失败：${e?.message || e}`);
  } finally {
    saving.value = false;
  }
}

async function saveCard() {
  if (!detail.value) return;
  saving.value = true;
  try {
    await apiPost("character/save", {
      id: detail.value.id,
      name: form.name.trim(),
      aliases: splitAliases(form.aliasesText),
      persona_name: nv(form.persona_name),
      work: form.work.trim(),
      lora_name: nv(form.lora_name),
      note: form.note,
      enabled: form.enabled,
    });
    message.success("已保存");
    await openDetail({ id: detail.value.id });
    await reload();
  } catch (e: any) {
    message.error(`保存失败：${e?.message || e}`);
  } finally {
    saving.value = false;
  }
}

function removeCard() {
  if (!detail.value) return;
  dialog.warning({
    title: "删除角色卡",
    content: `确认删除「${detail.value.name}」及其全部锚点？`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("character/delete", { id: detail.value.id });
        detailOpen.value = false;
        message.success("已删除");
        await reload();
      } catch (e: any) {
        message.error(`删除失败：${e?.message || e}`);
      }
    },
  });
}

function openAnchorCreate() {
  anchorForm.id = 0;
  anchorForm.name = "";
  anchorForm.kind = "full";
  anchorForm.positive = "";
  anchorForm.negative = "";
  anchorForm.weight = 1.2;
  anchorForm.lora_name = null; // 下拉空值 = null（空串会被 n-select 当成有值）
  anchorForm.skip_trigger_words = true;
  anchorOpen.value = true;
}

function openAnchorEdit(a: any) {
  anchorForm.id = Number(a.id);
  anchorForm.name = a.name || "";
  anchorForm.kind = a.kind || "full";
  anchorForm.positive = a.positive || "";
  anchorForm.negative = a.negative || "";
  anchorForm.weight = Number(a.weight || 1.2);
  anchorForm.lora_name = a.lora_name || null; // 空值用 null，避免下拉出现「待清空」
  anchorForm.skip_trigger_words = a.skip_trigger_words !== false;
  rememberExtra(a.lora_name || "", ""); // 锚点级自定义 LoRA 名也要能显示
  anchorOpen.value = true;
}

async function saveAnchor() {
  if (!detail.value) return;
  if (!anchorForm.positive.trim()) return message.warning("标签串不能为空");
  saving.value = true;
  try {
    await apiPost("character/anchor/save", {
      character_id: detail.value.id,
      id: anchorForm.id || undefined,
      anchor_name: anchorForm.name.trim() || "默认装",
      kind: anchorForm.kind,
      positive: anchorForm.positive.trim(),
      negative: anchorForm.negative.trim(),
      weight: Number(anchorForm.weight || 1.2),
      lora_name: nv(anchorForm.lora_name),
      skip_trigger_words: anchorForm.skip_trigger_words,
      created_by: myActor.value,
    });
    anchorOpen.value = false;
    message.success("锚点已保存");
    await openDetail({ id: detail.value.id });
    await reload();
  } catch (e: any) {
    message.error(`保存锚点失败：${e?.message || e}`);
  } finally {
    saving.value = false;
  }
}

function removeAnchor(a: any) {
  dialog.warning({
    title: "删除锚点",
    content: `确认删除锚点「${a.name}」？`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("character/anchor/delete", { id: a.id });
        message.success("已删除");
        await openDetail({ id: detail.value.id });
        await reload();
      } catch (e: any) {
        message.error(`删除失败：${e?.message || e}`);
      }
    },
  });
}

async function setPrimary(a: any) {
  try {
    await apiPost("character/anchor/primary", { character_id: detail.value.id, anchor_id: a.id });
    message.success(`主锚点 → ${a.name}`);
    await openDetail({ id: detail.value.id });
    await reload();
  } catch (e: any) {
    message.error(`设置失败：${e?.message || e}`);
  }
}

// ---------------- 下拉候选：角色类 LoRA / AstrBot 人格（v6.1.1）----------------
const loraOptions = ref<{ label: string; value: string }[]>([]);
const personaOptions = ref<{ label: string; value: string }[]>([]);
/** 已保存但不在候选里的值（如历史自定义名）也要能显示 → 补进选项。 */
const extraValues = reactive<{ lora: string[]; persona: string[] }>({ lora: [], persona: [] });

function mergeOptions(
  base: { label: string; value: string }[],
  extras: string[],
  suffix = "",
): { label: string; value: string }[] {
  const seen = new Set(base.map((o) => o.value));
  const out = [...base];
  for (const v of extras) {
    const _v = (v || "").trim();
    if (_v && !seen.has(_v)) {
      seen.add(_v);
      out.push({ label: `${_v}${suffix}`, value: _v });
    }
  }
  return out;
}

const mergedLoraOptions = computed(() => mergeOptions(loraOptions.value, extraValues.lora));
const mergedPersonaOptions = computed(() =>
  mergeOptions(
    personaOptions.value,
    extraValues.persona,
    extraValues.persona.length ? "" : "",
  ),
);

async function loadOptions() {
  try {
    const d = await apiGet("character/options");
    loraOptions.value = (d?.loras || []).map((l: any) => ({
      label: l.base_model ? `${l.name}（${l.base_model}）` : l.name,
      value: l.name,
    }));
    const _dflt = String(d?.default_persona || "");
    personaOptions.value = (d?.personas || []).map((p: any) => ({
      label: `${p.name}${_dflt && p.name === _dflt ? "（默认人格）" : ""}`,
      value: p.name,
    }));
  } catch (e: any) {
    // 候选拿不到不阻塞页面：下拉仍可手输
    console.warn("[角色卡] 读取下拉候选失败:", e?.message || e);
  }
}

function rememberExtra(lora: string, persona: string) {
  for (const [k, v] of [["lora", lora], ["persona", persona]] as const) {
    const _v = (v || "").trim();
    if (_v && !extraValues[k].includes(_v)) extraValues[k].push(_v);
  }
}

// ---------------- 参考图（M3）----------------
const refUrls = reactive<Record<number, string>>({});
const refSha = ref("");
const suggesting = ref(false);
const suggestOpen = ref(false);
const suggestText = ref("");
const suggestQuery = ref("");

// ---------------- 锚点级图片（v6.1.0）+ 上传弹窗（v6.3.0）----------------
/** 上传目标：0 = 角色级（不绑定锚点），其余 = 锚点 id。 */
const uploadAnchorId = ref<number>(0);
const uploadOpen = ref(false);
const uploading = ref(false);
const upDone = ref(false);
const upOk = ref(0);
const upDup = ref(0);
const upErr = ref(0);

const uploadTargetOptions = computed(() => [
  { label: "角色级（不绑定）", value: 0 },
  ...((detail.value?.anchors || []).map((a: any) => ({
    label: `锚点：${a.name}`,
    value: Number(a.id),
  })) as { label: string; value: number }[]),
]);

function anchorRefs(aid: number) {
  return (detail.value?.refs || []).filter((r: any) => Number(r.anchor_id || 0) === Number(aid));
}

/** 参考图按锚点分组（角色级单独一组），详情页「参考图」分区用它展示。 */
const refGroups = computed(() => {
  const refs = detail.value?.refs || [];
  const groups: { key: string; name: string; anchorId: number; items: any[] }[] = [];
  for (const a of detail.value?.anchors || []) {
    const items = refs.filter((r: any) => Number(r.anchor_id || 0) === Number(a.id));
    if (items.length) groups.push({ key: `a${a.id}`, name: `锚点：${a.name}`, anchorId: Number(a.id), items });
  }
  const loose = refs.filter((r: any) => !Number(r.anchor_id || 0));
  if (loose.length) groups.push({ key: "loose", name: "角色级（未绑定锚点）", anchorId: 0, items: loose });
  return groups;
});

/** 打开上传弹窗；anchorId 非 0 表示从某个锚点的「传图」进来，预选好目标。 */
function openUpload(anchorId = 0) {
  uploadAnchorId.value = Number(anchorId) || 0;
  upOk.value = 0;
  upDup.value = 0;
  upErr.value = 0;
  upDone.value = false;
  uploading.value = false;
  uploadOpen.value = true;
}

function closeUpload() {
  uploadOpen.value = false;
}

function onUploadClear() {
  upOk.value = 0;
  upDup.value = 0;
  upErr.value = 0;
  upDone.value = false;
}

/**
 * n-upload 的自定义上传：页面跑在 sandbox iframe 里，不能自己 fetch，
 * 必须走 bridge（apiPost）；所以这里只负责「读文件 → 交给后端 → 报成败」。
 */
async function onUploadRequest({ file, onFinish, onError }: UploadCustomRequestOptions) {
  if (!detail.value) return onError();
  if (Number(upOk.value + upDup.value + upErr.value) >= UPLOAD_MAX) {
    message.warning(`单次最多上传 ${UPLOAD_MAX} 张`);
    return onError();
  }
  uploading.value = true;
  try {
    const raw = file.file as File | undefined;
    if (!raw) throw new Error("没读到文件内容");
    const dataUrl = await fileToDataUrl(raw);
    const r = await apiPost(
      "character/ref/upload",
      {
        character_id: detail.value.id,
        filename: raw.name || file.name,
        data: dataUrl,
        anchor_id: Number(uploadAnchorId.value) || 0,
        created_by: myActor.value,
      },
      { timeout: 30000 },
    );
    if (r?.dedup) upDup.value += 1;
    else upOk.value += 1;
    onFinish();
    await refreshDetail();
  } catch (e: any) {
    upErr.value += 1;
    message.error(`「${file.name}」上传失败：${e?.message || e}`);
    onError();
  } finally {
    uploading.value = false;
    upDone.value = true;
  }
}

async function setCover(r: any) {
  if (!detail.value) return;
  try {
    await apiPost("character/cover/set", { character_id: detail.value.id, ref_id: r.id });
    message.success("已设为封面");
    await openDetail({ id: detail.value.id });
    await reload();
  } catch (e: any) {
    message.error(`设置封面失败：${e?.message || e}`);
  }
}

// ---------------- 查看原图（v6.1.3）----------------
const viewerShow = ref(false);
const viewerSrc = ref("");
const viewerTitle = ref("");
/** 当前查看的那张是否 NSFW（传给查看器决定初始打码与切换按钮）。 */
const viewerNsfw = ref(false);

/** 详情抽屉顶部那张封面：显式封面 > 自动封面（第一张图）。 */
const dtCoverRef = computed(() => {
  const refs: any[] = detail.value?.refs || [];
  return refs.find((r) => r.is_cover) || refs.find((r) => r.is_cover_auto) || null;
});
const dtCoverUrl = computed(() => {
  const r = dtCoverRef.value;
  return r ? refUrls[r.id] || "" : "";
});
const dtCoverAuto = computed(() => !!(dtCoverRef.value && !dtCoverRef.value.is_cover));
const primaryAnchorName = computed(() => {
  const pid = Number(detail.value?.primary_anchor_id || 0);
  const a = (detail.value?.anchors || []).find((x: any) => Number(x.id) === pid);
  return a ? a.name : "—";
});

/** 点封面 → 打开大图查看器（没有封面时不响应）。 */
function openCoverViewer() {
  const r = dtCoverRef.value;
  if (r) openRefViewer(r);
}

/** 点缩略图 → 拉**原图**进大图查看器（列表里的都是缩略图，放大才不发糊）。 */
async function openRefViewer(r: any) {
  viewerNsfw.value = isNsfw(r);
  viewerTitle.value = detail.value
    ? `${detail.value.name} · #${r.id}${r.is_cover ? "（封面）" : ""}`
    : `图片 #${r.id}`;
  viewerShow.value = true;
  viewerSrc.value = refUrls[r.id] || ""; // 先用缩略图占位，原图到了即刻替换
  try {
    const d = await apiGet("character/ref/image", { id: r.id, size: "orig" });
    if (d?.url) viewerSrc.value = d.url;
  } catch {
    /* 拉不到原图就保留缩略图展示 */
  }
}

function nsfwText(r: any) {
  const n = Number(r?.nsfw_score ?? -1);
  return n < 0 ? "" : `NSFW ${n.toFixed(2)}`;
}

function nsfwType(r: any) {
  const n = Number(r?.nsfw_score ?? -1);
  if (n >= 0.7) return "error";
  if (n >= 0.4) return "warning";
  return "success";
}

async function loadRefUrls(refs: any[]) {
  for (const r of refs || []) {
    if (refUrls[r.id]) continue;
    try {
      const d = await apiGet("character/ref/image", { id: r.id });
      if (d?.url) refUrls[r.id] = d.url;
    } catch {
      /* 单张失败不阻塞其余 */
    }
  }
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result || ""));
    fr.onerror = () => reject(new Error("读取文件失败"));
    fr.readAsDataURL(file);
  });
}

function removeRef(r: any) {
  dialog.warning({
    title: "删除参考图",
    // 引用式记录（出图自动关联 / 图库导入）删掉只是解除归属，gallery 里的原图不动 —— 说清楚，
    // 否则用户会以为点删除会把成品图一起清掉。
    content: `确认删除参考图 #${r.id}？`
      + (r.external ? "（这条是引用图库原图，只删记录，图库里的图不会被删）" : ""),
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await apiPost("character/ref/delete", { id: r.id });
        delete refUrls[r.id];
        message.success("已删除");
        await openDetail({ id: detail.value.id });
        await reload();
      } catch (e: any) {
        message.error(`删除失败：${e?.message || e}`);
      }
    },
  });
}

/** 重新拉当前角色详情（上传图片后立刻看到；列表也同步刷新）。 */
async function refreshDetail() {
  if (!detail.value) return;
  await openDetail({ id: detail.value.id });
  await reload();
}

async function importRefFromGallery() {
  const sha = (refSha.value || "").trim();
  if (!sha) return message.warning("请填图库图片的 sha");
  if (!detail.value) return;
  try {
    await apiPost("character/ref/from_gallery", {
      character_id: detail.value.id,
      sha,
      anchor_id: Number(uploadAnchorId.value) || 0,
      created_by: myActor.value,
    });
    refSha.value = "";
    message.success("已从图库导入");
    await openDetail({ id: detail.value.id });
    await reload();
  } catch (e: any) {
    message.error(`导入失败：${e?.message || e}`);
  }
}

async function suggestTags() {
  if (!detail.value) return;
  suggesting.value = true;
  try {
    const r = await apiPost("character/suggest", {
      name: detail.value.name,
      work: detail.value.work || "",
    });
    suggestText.value = r?.tags || "";
    suggestQuery.value = r?.query || "";
    suggestOpen.value = true;
  } catch (e: any) {
    message.error(`补全失败：${e?.message || e}`);
  } finally {
    suggesting.value = false;
  }
}

function useSuggestAsAnchor() {
  suggestOpen.value = false;
  openAnchorCreate();
  anchorForm.name = "补全候选";
  anchorForm.positive = suggestText.value;
}

async function doExport() {
  try {
    const data = await apiGet("character/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `character_cards_${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 3000);
    message.success("已导出 JSON");
  } catch (e: any) {
    message.error(`导出失败：${e?.message || e}`);
  }
}

async function doImport() {
  let parsed: any = null;
  try {
    parsed = JSON.parse(importText.value || "{}");
  } catch {
    return message.error("JSON 解析失败，请检查粘贴内容");
  }
  saving.value = true;
  try {
    const r = await apiPost("character/import", { data: parsed });
    importOpen.value = false;
    importText.value = "";
    message.success(`已导入 ${r?.imported ?? 0} 个角色（同名角色会被合并）`);
    await reload();
  } catch (e: any) {
    message.error(`导入失败：${e?.message || e}`);
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  reload();
  loadOptions(); // 角色类 LoRA 与 AstrBot 人格候选（拿不到也不影响手输）
});
</script>

<style scoped>
.char-page { display: flex; flex-direction: column; gap: 12px; }
.stat-row { display: flex; gap: 12px; flex-wrap: wrap; }
.stat-card {
  flex: 1 1 120px; background: rgba(128, 128, 128, 0.08); border-radius: 10px;
  padding: 10px 14px; text-align: center;
}
.stat-num { font-size: 22px; font-weight: 600; }
.stat-label { font-size: 12px; opacity: 0.7; }
.tool-grid { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.tool-grid > :first-child { flex: 1 1 240px; }
.tool-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.tip { margin-top: 10px; font-size: 12px; line-height: 1.7; opacity: 0.75; }
.tip code {
  background: rgba(128, 128, 128, 0.16); padding: 1px 4px; border-radius: 4px;
}
.empty { text-align: center; opacity: 0.6; padding: 18px 0; font-size: 13px; }
.dim { font-size: 12px; opacity: 0.6; }

/* ---- 详情抽屉头部（v6.3.0）---- */
.dt-head { display: flex; gap: 14px; align-items: flex-start; }
.dt-cover {
  position: relative; flex: 0 0 108px; width: 108px; height: 144px;
  border-radius: 10px; overflow: hidden; cursor: zoom-in;
  border: 1px solid rgba(128, 128, 128, 0.25); background: rgba(128, 128, 128, 0.12);
}
.dt-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.dt-cover-ph {
  width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
  font-size: 26px; font-weight: 600; opacity: 0.35; letter-spacing: 2px;
}
.dt-cover-tag {
  position: absolute; right: 4px; bottom: 4px; font-size: 10px; padding: 0 5px;
  border-radius: 4px; background: rgba(0, 0, 0, 0.5); color: #fff;
}
.dt-ident { flex: 1 1 auto; min-width: 0; }
.dt-name { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.dt-name-txt { font-size: 17px; font-weight: 600; }
.dt-chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0 8px; }
.dt-meta { width: 100%; }
.dt-tabs { margin-top: 10px; }

/* ---- 分区工具条 ---- */
.pane-bar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;
}

/* ---- 锚点条目 ---- */
.anchor-item {
  border: 1px solid rgba(128, 128, 128, 0.2); border-radius: 8px;
  padding: 8px 10px; margin-bottom: 8px;
}
.anchor-top { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.anchor-spacer { flex: 1 1 auto; }
.anchor-name { font-weight: 600; }
.anchor-pos { font-size: 12px; line-height: 1.6; margin-top: 6px; word-break: break-word; }
.anchor-neg { font-size: 12px; opacity: 0.7; margin-top: 4px; word-break: break-word; }
.rec-meta {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-top: 6px; font-size: 11px; opacity: 0.72;
}
.hint { font-size: 12px; opacity: 0.6; margin-left: 8px; }
.tip-inline { display: block; margin: 0 0 10px 92px; }

/* ---- 参考图 ---- */
.ref-group { margin-bottom: 14px; }
.ref-group-head {
  display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
  border-left: 3px solid rgba(64, 128, 255, 0.5); padding-left: 6px;
}
.ref-group-name { font-size: 13px; font-weight: 600; }
.ref-grid { display: flex; flex-wrap: wrap; gap: 10px; }
.ref-item {
  width: 132px; border: 1px solid rgba(128, 128, 128, 0.2);
  border-radius: 8px; padding: 6px; text-align: center;
}
.ref-item img { width: 100%; height: 110px; object-fit: cover; border-radius: 6px; display: block; }
.ref-ph {
  height: 110px; display: flex; align-items: center; justify-content: center;
  font-size: 12px; opacity: 0.5; background: rgba(128, 128, 128, 0.12); border-radius: 6px;
}
.ref-meta {
  display: flex; align-items: center; justify-content: center; gap: 4px;
  font-size: 11px; opacity: 0.8; margin: 4px 0 2px; flex-wrap: wrap;
}
.ref-who {
  display: flex; flex-direction: column; font-size: 10px; opacity: 0.62;
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
}
.ref-who-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ref-missing { border-color: rgba(255, 77, 79, 0.6); }
.ref-clickable { cursor: zoom-in; }
.anchor-ref img.ref-clickable { cursor: zoom-in; }
.ref-is-cover { border-color: rgba(250, 173, 20, 0.75) !important; }
.ref-ops { display: flex; align-items: center; justify-content: center; gap: 2px; }

/* ---- NSFW 打码（与图库同一套观感）---- */
.ref-item { position: relative; }
.nsfw-blur { filter: blur(12px); transform: scale(1.06); }
.nsfw-mask {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 4px;
  color: #fff; font-size: 16px; border-radius: inherit; cursor: zoom-in;
  background: rgba(0, 0, 0, 0.18); pointer-events: none;
}
.nsfw-mask-tip { font-size: 10px; font-weight: 600; background: rgba(0, 0, 0, 0.5); padding: 1px 7px; border-radius: 20px; }
.blur-switch { display: inline-flex; align-items: center; font-size: 12px; opacity: 0.85; }
.actor-pop { max-width: 280px; }

/* ---- 锚点缩略图的就地删除 ---- */
.anchor-ref-del {
  position: absolute; top: -5px; left: -5px; width: 17px; height: 17px;
  border-radius: 50%; border: none; cursor: pointer; line-height: 1;
  font-size: 10px; color: #fff; background: rgba(0, 0, 0, 0.55);
  display: flex; align-items: center; justify-content: center; padding: 0;
}
.anchor-ref-del:hover { background: #d03050; }
.anchor-ref.ref-missing { border-radius: 6px; box-shadow: 0 0 0 2px rgba(255, 77, 79, 0.7); }

/* ---- 上传弹窗 ---- */
.drop-title { font-size: 15px; font-weight: 600; }
.drop-sub { font-size: 12px; opacity: 0.65; margin-top: 6px; line-height: 1.7; }
.up-status { margin-top: 10px; font-size: 12px; min-height: 18px; }

/* ---- 卡片视图（v6.1.0）---- */
.char-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 14px;
}
.char-card {
  border: 1px solid rgba(128, 128, 128, 0.2);
  border-radius: 10px;
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.12s ease, box-shadow 0.12s ease, border-color 0.12s ease;
  background: rgba(128, 128, 128, 0.04);
}
.char-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.16);
  border-color: rgba(64, 128, 255, 0.45);
}
.char-off { opacity: 0.55; }
.char-cover {
  position: relative;
  width: 100%;
  aspect-ratio: 3 / 4;
  background: rgba(128, 128, 128, 0.12);
  overflow: hidden;
}
.char-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.char-cover-ph {
  width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
  font-size: 30px; font-weight: 600; opacity: 0.35; letter-spacing: 2px;
}
.char-badge { position: absolute; top: 6px; left: 6px; }
.char-badge-right { left: auto; right: 6px; }
.char-body { padding: 8px 10px 10px; }
.char-title { display: flex; align-items: center; gap: 6px; }
.char-name { font-weight: 600; font-size: 14px; }
.char-meta {
  display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px;
  font-size: 11px; opacity: 0.72;
}
.char-anchor-names {
  margin-top: 4px; font-size: 11px; opacity: 0.6;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.char-lora {
  margin-top: 2px; font-size: 11px; opacity: 0.6;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.char-by {
  margin-top: 4px; font-size: 10px; opacity: 0.55;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.char-ops { display: flex; align-items: center; gap: 4px; margin-top: 8px; flex-wrap: wrap; }

/* ---- 锚点下的图片 ---- */
.anchor-refs { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.anchor-ref { position: relative; width: 56px; height: 56px; }
.anchor-ref img {
  width: 100%; height: 100%; object-fit: cover; border-radius: 6px; display: block;
  border: 1px solid rgba(128, 128, 128, 0.25);
}
.anchor-ref-ph {
  width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
  border-radius: 6px; background: rgba(128, 128, 128, 0.14); font-size: 11px; opacity: 0.6;
}
.anchor-ref-cover {
  position: absolute; top: -4px; right: -4px; font-size: 12px;
  color: #faad14; text-shadow: 0 0 3px rgba(0, 0, 0, 0.6);
}
</style>
