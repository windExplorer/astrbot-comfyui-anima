<template>
  <teleport to="body">
    <div v-if="show" class="iviewer" @click.self="onClose">
      <button class="iv-close" @click="onClose" aria-label="关闭">✕</button>
      <div class="iv-body">
        <!-- 图片区 -->
        <div class="iv-imgs" :data-pair="isPair ? '1' : '0'">
          <!-- 参考图（图生图源图） -->
          <figure v-if="isPair" class="iv-fig">
            <div class="iv-imgwrap">
              <img v-if="refSrc" :src="refSrc" alt="参考图" @click="swapPair" />
              <div v-else class="iv-loading">加载参考图…</div>
            </div>
            <figcaption class="iv-cap ref">参考图</figcaption>
          </figure>
          <!-- 结果图 -->
          <figure class="iv-fig">
            <div class="iv-imgwrap">
              <img v-if="mainSrc" :src="mainSrc" alt="图片" />
              <div v-else class="iv-loading">加载中…</div>
            </div>
            <figcaption class="iv-cap">{{ isPair ? "结果图" : typeText }}</figcaption>
          </figure>
        </div>

        <!-- 信息面板 -->
        <aside class="iv-info">
          <template v-if="item">
            <div class="iv-actions">
              <n-button v-if="!isTrash" size="small" :type="item.starred ? 'warning' : 'default'" @click="onStar(item)">★ {{ item.starred ? "已收藏" : "收藏" }}</n-button>
              <n-button v-if="!isTrash" size="small" type="error" ghost @click="onDelete(item)">删除</n-button>
              <n-button v-if="isTrash" size="small" type="success" @click="onRestore(item)">恢复</n-button>
              <n-button v-if="isTrash" size="small" type="error" ghost @click="onPurge(item)">彻底删除</n-button>
            </div>
            <div class="iv-row"><span class="k">SHA</span><span class="v">{{ shortSha }}</span></div>
            <div class="iv-row"><span class="k">类型</span><span class="v">{{ typeText }}</span></div>
            <div v-if="item.workflow" class="iv-row"><span class="k">工作流</span><span class="v">{{ item.workflow }}</span></div>
            <div v-if="item.w && item.h" class="iv-row"><span class="k">尺寸</span><span class="v">{{ item.w }} × {{ item.h }}</span></div>
            <div v-if="item.size_bytes != null" class="iv-row"><span class="k">大小</span><span class="v">{{ fmtBytes(item.size_bytes) }}</span></div>
            <div v-if="item.cost_sec != null" class="iv-row"><span class="k">耗时</span><span class="v">{{ fmtDuration(item.cost_sec) }}</span></div>
            <div v-if="item.created_at" class="iv-row"><span class="k">出图时间</span><span class="v">{{ fmtDateTime(item.created_at) }}</span></div>
            <div v-if="item.user_name" class="iv-row"><span class="k">用户名</span><span class="v">{{ item.user_name }}</span></div>
            <div v-if="item.user_id" class="iv-row"><span class="k">用户ID</span><span class="v">{{ item.user_id }}</span></div>
            <div v-if="item.seed != null" class="iv-row"><span class="k">Seed</span><span class="v">{{ item.seed }}</span></div>
            <div v-if="item.denoise != null" class="iv-row"><span class="k">Denoise</span><span class="v">{{ item.denoise }}</span></div>
            <div v-if="item.use_count != null" class="iv-row"><span class="k">使用次数</span><span class="v">{{ item.use_count }}</span></div>
            <div class="iv-row iv-prompt"><span class="k">提示词</span><span class="v">{{ item.prompt_raw || item.prompt || "（无）" }}</span></div>
          </template>
          <div v-else class="iv-loading">加载信息…</div>
        </aside>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NButton } from "naive-ui";
import { apiGet } from "@/api/bridge";
import { fmtBytes, fmtDuration, fmtDateTime } from "@/utils/format";

interface ViewerImage {
  sha?: string;
  sha256?: string;
  ref_sha256?: string;
  source?: string;
  is_img2img?: boolean;
  prompt?: string;
  prompt_raw?: string;
  workflow?: string;
  w?: number;
  h?: number;
  size_bytes?: number;
  cost_sec?: number;
  created_at?: number | string;
  user_name?: string;
  user_id?: string;
  seed?: number | string;
  denoise?: number;
  use_count?: number;
  starred?: boolean;
  status?: number;
}

const props = defineProps<{
  show: boolean;
  /** 主图 sha */
  sha?: string;
  /** 初始元数据（可选，打开后会自动拉取最新） */
  item?: ViewerImage | null;
  /** 图生图参考图 sha（可选） */
  refSha?: string;
  /** 是否回收站模式（显示恢复/彻底删除） */
  isTrash?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "star", item: any): void;
  (e: "delete", item: any): void;
  (e: "restore", item: any): void;
  (e: "purge", item: any): void;
}>();

const mainSrc = ref("");
const refSrc = ref("");
const item = ref<ViewerImage | null>(null);

const isPair = computed(() => {
  const rs = props.refSha || (item.value && item.value.ref_sha256);
  return Boolean(rs && mainSrc.value && refSrc.value);
});

const shortSha = computed(() => {
  const s = item.value?.sha256 || props.sha || "";
  return s ? s.slice(0, 20) + "…" : "";
});

const typeText = computed(() => {
  const it = item.value;
  if (!it) return "图片";
  if (it.is_img2img) return "图生图";
  if (it.source === "ref") return "参考图";
  if (it.source === "user") return "用户收藏";
  return "文生图";
});

async function loadMain(sha: string) {
  mainSrc.value = "";
  try {
    const data = await apiGet("gallery/image", { sha, meta: 1 });
    if (data && data.data_url) mainSrc.value = data.data_url;
    if (data && data.meta) item.value = { ...(item.value || {}), ...data.meta, sha: data.meta.sha256 || sha };
    // 提示词取原始
    const meta = item.value;
    item.value = {
      ...item.value,
      prompt: meta?.prompt,
      prompt_raw: meta?.prompt_raw,
    };
  } catch (e) {
    mainSrc.value = "";
  }
}

async function loadRef(rs: string) {
  refSrc.value = "";
  try {
    const data = await apiGet("gallery/image", { sha: rs, meta: 1 });
    if (data && data.data_url) refSrc.value = data.data_url;
  } catch (e) {
    refSrc.value = "";
  }
}

// 图生图参考图 + 结果图并排；无参考图时纯单图
watch(
  () => props.show,
  async (v) => {
    if (!v || !props.sha) return;
    item.value = props.item || null;
    mainSrc.value = "";
    refSrc.value = "";
    await loadMain(props.sha);
    const rs = props.refSha || (item.value && item.value.ref_sha256);
    if (rs) await loadRef(String(rs));
  },
  { immediate: true }
);

function swapPair() {
  // 点击参考图/结果图标签时可交换主次（参考图也可以作为主图查看）
  const m = mainSrc.value;
  mainSrc.value = refSrc.value;
  refSrc.value = m;
}

function onClose() {
  emit("update:show", false);
}

function onStar(it: any) { emit("star", it); }
function onDelete(it: any) { emit("delete", it); }
function onRestore(it: any) { emit("restore", it); }
function onPurge(it: any) { emit("purge", it); }
</script>

<style scoped>
.iviewer {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(8, 8, 12, 0.88);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: stretch;
}
.iv-close {
  position: fixed;
  top: 14px;
  right: 16px;
  z-index: 10;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 1.3rem;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
}
.iv-close:hover { background: rgba(0, 0, 0, 0.8); }
.iv-body {
  display: flex;
  width: 100%;
  height: 100%;
  overflow: hidden;
}
.iv-imgs {
  flex: 1 1 auto;
  display: flex;
  gap: 18px;
  align-items: center;
  justify-content: center;
  padding: 28px;
  min-width: 0;
}
.iv-fig {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  max-width: 100%;
}
.iv-cap {
  align-self: center;
  font-size: 0.74rem;
  font-weight: 700;
  padding: 3px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.1);
  color: #cfd2dc;
  letter-spacing: 0.3px;
  cursor: pointer;
  user-select: none;
}
.iv-cap.ref { background: #5b3a8e; color: #fff; }
.iv-imgwrap {
  background: #0c0d11;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  border-radius: 10px;
  overflow: hidden;
}
.iv-imgwrap img {
  max-width: calc(100% - 40px);
  max-height: calc(100vh - 90px);
  width: auto;
  height: auto;
  display: block;
  object-fit: contain;
}
/* 图生图：结果图 + 参考图各占一半并排 */
.iv-imgs[data-pair="1"] .iv-fig { flex: 1 1 50%; min-width: 0; max-width: 50%; }
.iv-imgs[data-pair="1"] .iv-imgwrap { max-height: calc(100vh - 90px); }
.iv-imgs[data-pair="1"] .iv-imgwrap img { max-width: 100%; max-height: calc(100vh - 100px); }
.iv-loading {
  color: rgba(255, 255, 255, 0.6);
  padding: 40px;
  font-size: 13px;
}
.iv-info {
  width: 380px;
  flex: 0 0 380px;
  height: 100%;
  overflow: hidden;
  padding: 16px 18px;
  border-left: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(20, 20, 28, 0.6);
  font-size: 0.82rem;
  line-height: 1.7;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}
.iv-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  flex: 0 0 auto;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  margin-bottom: 8px;
}
.iv-row {
  display: flex;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  flex: 0 0 auto;
}
.iv-row .k { flex: 0 0 64px; color: rgba(255, 255, 255, 0.5); }
.iv-row .v { flex: 1; color: #e6e6f0; word-break: break-word; white-space: pre-wrap; }
.iv-prompt { flex: 1 1 auto; min-height: 60px; align-items: stretch; }
.iv-prompt .v { overflow: auto; max-height: 100%; padding-right: 4px; }
@media (max-width: 760px) {
  .iv-body { flex-direction: column; }
  .iv-info { width: 100%; flex: 0 0 auto; height: auto; max-height: 40vh; overflow: auto; border-left: none; border-top: 1px solid rgba(255,255,255,0.08); }
  .iv-imgwrap img { max-width: 92vw; }
}
</style>
