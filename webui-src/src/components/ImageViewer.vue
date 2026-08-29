<template>
  <teleport to="body">
    <div v-if="show" class="iviewer" @click.self="onClose">
      <button class="iv-close" @click="onClose" aria-label="关闭">✕</button>
      <div class="iv-body" @click.self="onClose">
        <button v-if="canNav" class="iv-nav iv-nav-prev" :disabled="navPrevDisabled" @click="onNav(-1)" aria-label="上一张">‹</button>
        <div v-if="canNav" class="iv-counter">{{ navIndex + 1 }} / {{ navTotal }}</div>
        <!-- 图片区：点击图片之外的空白（含图四周留白）关闭 -->
        <div class="iv-imgs" :data-pair="isPair ? '1' : '0'" @click="onClose">
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
              <img v-if="mainSrc" :src="mainSrc" alt="图片" :class="{ 'iv-nsfw-blur': mainBlurred }" />
              <div v-else class="iv-loading">加载中…</div>
              <button v-if="mainBlurred" class="iv-nsfw-reveal" @click.stop="revealMain">🔞 点击查看</button>
            </div>
            <figcaption class="iv-cap">{{ isPair ? "结果图" : typeText }}</figcaption>
          </figure>
        </div>
        <button v-if="canNav" class="iv-nav iv-nav-next" :disabled="navNextDisabled" @click="onNav(1)" aria-label="下一张">›</button>

        <!-- 信息面板 -->
        <aside class="iv-info">
          <template v-if="item">
            <div class="iv-actions">
              <button v-if="!isTrash" class="iv-star" :class="{ on: item.starred }" @click="onStar(item)">★ {{ item.starred ? "已收藏" : "收藏" }}</button>
              <button v-if="isNsfw && !isTrash" class="iv-blur" :class="{ on: mainBlurred }" @click="onToggleBlur">{{ blurBtnLabel }}</button>
              <n-button v-if="!isTrash" size="small" type="error" ghost @click="onDelete(item)">删除</n-button>
              <n-button v-if="isTrash" size="small" type="success" @click="onRestore(item)">恢复</n-button>
              <n-button v-if="isTrash" size="small" type="error" ghost @click="onPurge(item)">彻底删除</n-button>
            </div>
            <div class="iv-row iv-row-sha"><span class="k">SHA</span><span class="v">
              <code class="iv-sha" :title="fullSha ? '点击复制' : ''" @click="copySha">{{ fullSha || "—" }}</code>
            </span></div>
            <div class="iv-row"><span class="k">类型</span><span class="v">{{ typeText }}</span></div>
            <div v-if="item.nsfw != null" class="iv-row"><span class="k">NSFW</span><span class="v">
              {{ item.nsfw ? "是" : "否" }}<template v-if="item.nsfw_score != null && item.nsfw_score > 0">（{{ (item.nsfw_score * 100).toFixed(1) }}%）</template>
              <button class="iv-check" :disabled="checking" @click="onCheckNsfw">{{ item.nsfw_checked ? "重新检测" : "检测" }}</button>
              <button v-if="!item.nsfw" class="iv-check" :disabled="settingNsfw" @click="onSetNsfw(true)">标记为 NSFW</button>
              <button v-else class="iv-check" :disabled="settingNsfw" @click="onSetNsfw(false)">取消 NSFW</button>
            </span></div>
            <div v-if="item.nsfw == null" class="iv-row"><span class="k">NSFW</span><span class="v">未检测
              <button class="iv-check" :disabled="checking" @click="onCheckNsfw">{{ checking ? "检测中…" : "检测" }}</button>
              <button class="iv-check" :disabled="settingNsfw" @click="onSetNsfw(true)">标记为 NSFW</button>
            </span></div>
            <div v-if="!isTrash" class="iv-row iv-row-tags"><span class="k">标签</span><span class="v iv-tags">
              <template v-if="item.tags && item.tags.length">
                <n-tag v-for="t in item.tags" :key="t" size="small" :color="tagColor(t)" closable @close="removeTag(t)" class="iv-tag">{{ t }}</n-tag>
              </template>
              <span v-else class="iv-tag-empty">无</span>
              <span class="iv-tag-add">
                <n-input v-model:value="newTag" size="small" placeholder="加标签后回车" style="width:140px" @keyup.enter="addTag" />
              </span>
            </span></div>
            <div v-if="item.workflow" class="iv-row"><span class="k">工作流</span><span class="v">{{ item.workflow }}</span></div>
            <div v-if="item.trigger_msg" class="iv-row"><span class="k">触发消息</span><span class="v">{{ item.trigger_msg }}</span></div>
            <div v-if="item.w && item.h" class="iv-row"><span class="k">尺寸</span><span class="v">{{ item.w }} × {{ item.h }}</span></div>
            <div v-if="item.size_bytes != null" class="iv-row"><span class="k">大小</span><span class="v">{{ fmtBytes(item.size_bytes) }}</span></div>
            <div v-if="item.cost_sec != null" class="iv-row"><span class="k">耗时</span><span class="v">{{ fmtDuration(item.cost_sec) }}</span></div>
            <div v-if="item.created_at" class="iv-row"><span class="k">出图时间</span><span class="v">{{ fmtDateTime(item.created_at) }}</span></div>
            <div v-if="item.user_name" class="iv-row"><span class="k">用户名</span><span class="v">{{ item.user_name }}</span></div>
            <div v-if="item.user_id" class="iv-row"><span class="k">用户ID</span><span class="v">{{ item.user_id }}</span></div>
            <div v-if="item.group_id" class="iv-row"><span class="k">群号</span><span class="v">{{ item.group_id }}</span></div>
            <div v-if="item.group_name" class="iv-row"><span class="k">群名</span><span class="v">{{ item.group_name }}</span></div>
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
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
import { NButton, NInput, NSpace, NTag, useDialog, useMessage } from "naive-ui";
import { apiGet, apiPost, isStandaloneMode, standaloneImgUrl } from "@/api/bridge";
import { fmtBytes, fmtDuration, fmtDateTime } from "@/utils/format";

const message = useMessage();
const dialog = useDialog();

interface ViewerImage {
  sha?: string;
  sha256?: string;
  ref_sha256?: string;
  source?: string;
  is_img2img?: boolean;
  prompt?: string;
  prompt_raw?: string;
  trigger_msg?: string;
  workflow?: string;
  w?: number;
  h?: number;
  size_bytes?: number;
  cost_sec?: number;
  created_at?: number | string;
  user_name?: string;
  user_id?: string;
  group_id?: string;
  group_name?: string;
  seed?: number | string;
  denoise?: number;
  use_count?: number;
  starred?: boolean;
  status?: number;
  nsfw?: boolean;
  nsfw_score?: number;
  nsfw_blur?: number | null;
  nsfw_checked?: boolean;
  tags?: string[];
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
  /** 全局 NSFW 模糊开关（图库页的「一键模糊」），默认开启。关闭后大图不模糊（单图强制模糊除外） */
  blurGlobal?: boolean;
  /** 导航列表（同一批图片，用于上一张/下一张）。提供后显示左右箭头与计数 */
  images?: Array<{ sha?: string; item?: ViewerImage | null; refSha?: string }>;
  /** 当前在 images 中的索引 */
  index?: number;
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "star", item: any): void;
  (e: "delete", item: any): void;
  (e: "restore", item: any): void;
  (e: "purge", item: any): void;
  (e: "nav", delta: number): void;
}>();

const mainSrc = ref("");
const refSrc = ref("");
const item = ref<ViewerImage | null>(null);

const isPair = computed(() => {
  const rs = props.refSha || (item.value && item.value.ref_sha256);
  return Boolean(rs && mainSrc.value && refSrc.value);
});

const fullSha = computed(() => {
  return item.value?.sha256 || props.sha || "";
});

const typeText = computed(() => {
  const it = item.value;
  if (!it) return "图片";
  if (it.is_img2img) return "图生图";
  if (it.source === "ref") return "参考图";
  if (it.source === "user") return "用户图";
  return "文生图";
});

async function loadMain(sha: string) {
  mainSrc.value = "";
  try {
    // 元数据单独取（noimg=1：只回 meta，不生成大图 base64，避免大图被转码/缩小）
    try {
      const data = await apiGet("gallery/image", { sha, meta: 1, noimg: 1 });
      if (data && data.meta) item.value = { ...(item.value || {}), ...data.meta, sha: data.meta.sha256 || sha };
    } catch (e) { /* meta 获取失败不阻塞看图 */ }
    const meta = item.value;
    item.value = {
      ...item.value,
      prompt: meta?.prompt,
      prompt_raw: meta?.prompt_raw,
    };

    // 大图加载按模式区分：
    // - 独立服务：/img/{sha} 原图直链（自带 token，浏览器原生缓存，不缩放、不 base64）
    // - 内嵌页：AstrBot 的 page API 受登录鉴权保护，<img> 直链会 401，只能用 base64。
    //   故内嵌走 gallery/image?meta=1（后端返回原图 data_url），前端直接给 <img>。
    if (isStandaloneMode()) {
      mainSrc.value = standaloneImgUrl(sha);
    } else {
      try {
        const img = await apiGet("gallery/image", { sha, meta: 1 });
        if (img && img.data_url) mainSrc.value = img.data_url;
      } catch (e) { /* 加载失败不阻塞，mainSrc 保持空 */ }
    }
  } catch (e) {
    mainSrc.value = "";
  }
}

async function loadRef(rs: string) {
  refSrc.value = "";
  try {
    // 独立模式：用直链 URL
    if (isStandaloneMode()) {
      refSrc.value = standaloneImgUrl(rs, 900);
      return;
    }
    // 内嵌页：参考图展示尺寸较小，用更小的 size 进一步减小体积
    const data = await apiGet("gallery/image", { sha: rs, meta: 1, size: 900 });
    if (data && data.data_url) refSrc.value = data.data_url;
  } catch (e) {
    refSrc.value = "";
  }
}

// 图生图参考图 + 结果图并排；无参考图时纯单图。
// 主图与参考图并行加载，避免串行等待放大图加载时长。
watch(
  () => [props.show, props.sha, props.refSha] as const,
  async ([v, sha, rs]) => {
    if (!v || !sha) return;
    item.value = props.item || null;
    mainSrc.value = "";
    refSrc.value = "";
    const tasks: Promise<void>[] = [loadMain(sha)];
    const ref = rs || (item.value && item.value.ref_sha256);
    if (ref) tasks.push(loadRef(String(ref)));
    await Promise.all(tasks);
  },
  { immediate: true }
);

function swapPair() {
  // 点击参考图/结果图标签时可交换主次（参考图也可以作为主图查看）
  const m = mainSrc.value;
  mainSrc.value = refSrc.value;
  refSrc.value = m;
}

// ---- 标签（展示 + 增删） ----
const newTag = ref("");
// 标签固定调色板：同一标签稳定映射到同一颜色，不同标签随机配色。
// 色板取中深饱和色，配白字保证对比度；返回 NTag 的 color 对象。
const TAG_COLORS = [
  "#7c4dff", "#0ea5e9", "#db2777", "#d97706", "#16a34a",
  "#9333ea", "#c026d3", "#0284c7", "#ea580c", "#059669",
  "#be123c", "#0d9488", "#4f46e5", "#b91c1c", "#2563eb",
];
function _tagHash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}
function tagColor(s: string) {
  return { color: TAG_COLORS[_tagHash(s) % TAG_COLORS.length], textColor: "#fff", borderColor: "transparent" };
}
async function _applyTags(tags: string[], action: "add" | "del") {
  const it = item.value;
  const sha = it?.sha || it?.sha256;
  if (!sha || !tags.length) return;
  try {
    await apiPost("gallery/tags", { sha, tags, action });
    // 本地同步 tags，并广播给图库列表刷新
    const cur = Array.isArray(it.tags) ? it.tags : [];
    it.tags = action === "add"
      ? Array.from(new Set([...cur, ...tags]))
      : cur.filter((t: string) => !tags.includes(t));
    window.dispatchEvent(new CustomEvent("anima:tags-updated", { detail: { sha, tags: it.tags } }));
    message.success(action === "add" ? "标签已添加" : "标签已删除");
  } catch (e: any) {
    message.error(e?.message || (action === "add" ? "添加标签失败" : "删除标签失败"));
  }
}
async function addTag() {
  const t = newTag.value.trim();
  if (!t) return;
  newTag.value = "";
  await _applyTags([t], "add");
}
async function removeTag(t: string) {
  await _applyTags([t], "del");
}

// ---- NSFW 模糊 ----
const isNsfw = computed(() => Boolean(item.value && item.value.nsfw));
// 结果图是否应模糊：
//  - 非 NSFW → 不模糊
//  - 单图 nsfw_blur=0 强制不模糊（覆盖全局）；=1 强制模糊（覆盖全局）
//  - nsfw_blur 未设置 → 跟随全局开关 blurGlobal（默认开）：开→模糊，关→不模糊
const mainBlurred = ref(false);
watch(
  () => [item.value?.nsfw, item.value?.nsfw_blur, props.blurGlobal] as const,
  () => {
    const it = item.value;
    if (!it || !it.nsfw) { mainBlurred.value = false; return; }
    if (it.nsfw_blur === 0) mainBlurred.value = false;
    else if (it.nsfw_blur === 1) mainBlurred.value = true;
    else mainBlurred.value = props.blurGlobal !== false;
  },
  { immediate: true }
);
const blurBtnLabel = computed(() => {
  // 反映当前临时模糊状态
  return mainBlurred.value ? "取消模糊" : "设为模糊";
});
function revealMain() {
  // 临时查看：仅取消本次查看的模糊，不写库（关闭再开后恢复）
  mainBlurred.value = false;
}
function onToggleBlur() {
  // 纯前端临时切换模糊/清晰，便于查看；不请求接口、不改数据库，关闭大图后恢复默认
  mainBlurred.value = !mainBlurred.value;
}

// ---- 单图 NSFW 检测 ----
const checking = ref(false);
function onCheckNsfw() {
  const it = item.value;
  const sha = it?.sha || it?.sha256;
  if (!sha || checking.value) return;
  checking.value = true;
  apiGet("gallery/check_nsfw", { sha })
    .then((res: any) => {
      it.nsfw = !!res?.nsfw;
      it.nsfw_score = res?.nsfw_score ?? null;
      it.nsfw_checked = true;
      message.success(res?.msg || "检测完成");
      // 通知图库列表等页面本地同步该图的 NSFW 状态（无需重新请求接口）
      const fullSha = it?.sha256 || sha;
      window.dispatchEvent(new CustomEvent("anima:nsfw-updated", {
        detail: { sha: fullSha, nsfw: !!res?.nsfw, nsfw_score: res?.nsfw_score ?? null },
      }));
    })
    .catch((e: any) => {
      if (isNsfwUnavailable(e)) showNsfwInstallDialog();
      else message.error(e.message || "检测失败");
    })
    .finally(() => { checking.value = false; });
}

// 人工标记/取消 NSFW（误判纠正，绕过自动检测模型）
const settingNsfw = ref(false);
function onSetNsfw(on: boolean) {
  const it = item.value;
  const sha = it?.sha || it?.sha256;
  if (!sha || settingNsfw.value) return;
  settingNsfw.value = true;
  apiPost("gallery/set_nsfw", { sha, on: on ? 1 : 0 })
    .then((res: any) => {
      it.nsfw = !!on;
      it.nsfw_checked = true;
      message.success(res?.msg || (on ? "已标记为 NSFW" : "已取消 NSFW"));
      // 通知图库列表等页面本地同步该图的 NSFW 状态（无需重新请求接口）
      const fullSha = it?.sha256 || sha;
      window.dispatchEvent(new CustomEvent("anima:nsfw-updated", {
        detail: { sha: fullSha, nsfw: !!on, nsfw_score: null },
      }));
    })
    .catch((e: any) => { message.error(e.message || "操作失败"); })
    .finally(() => { settingNsfw.value = false; });
}

function isNsfwUnavailable(e: any): boolean {
  const m = String(e?.message || "");
  return /NSFW 检测不可用|onnxruntime|opennsfw/.test(m);
}
function showNsfwInstallDialog() {
  dialog.warning({
    title: "NSFW 检测不可用",
    content: "NSFW 检测需要两个依赖库。请在 AstrBot 日志页右上角的「安装 pip 库」入口依次填入以下库名并安装，完成后重启插件：\n\n· onnxruntime\n· opennsfw-onnx",
    positiveText: "知道了",
    closable: true,
  });
}

function copySha() {
  const s = fullSha.value;
  if (!s) return;
  try {
    navigator.clipboard?.writeText(s);
    message.success("SHA 已复制");
  } catch {
    message.error("复制失败");
  }
}

// ---- 左右切换导航 ----
const canNav = computed(() => Array.isArray(props.images) && props.images.length > 1);
const navIndex = computed(() => props.index ?? 0);
const navTotal = computed(() => (Array.isArray(props.images) ? props.images.length : 0));
const navPrevDisabled = computed(() => !canNav.value || navIndex.value <= 0);
const navNextDisabled = computed(() => !canNav.value || navIndex.value >= navTotal.value - 1);
function onNav(delta: number) {
  if (!canNav.value) return;
  const ni = navIndex.value + delta;
  if (ni < 0 || ni >= navTotal.value) return;
  emit("nav", delta);
}
function onKeyNav(e: KeyboardEvent) {
  if (!props.show) return;
  const t = e.target as HTMLElement | null;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
  if (!canNav.value) return;
  if (e.key === "ArrowLeft") { e.preventDefault(); onNav(-1); }
  else if (e.key === "ArrowRight") { e.preventDefault(); onNav(1); }
}
onMounted(() => window.addEventListener("keydown", onKeyNav));
onUnmounted(() => window.removeEventListener("keydown", onKeyNav));

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
/* 左右切换箭头 + 计数器 */
.iv-nav {
  flex: 0 0 auto;
  align-self: center;
  width: 46px;
  height: 46px;
  border: none;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  font-size: 2rem;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, opacity 0.15s;
  z-index: 5;
}
.iv-nav:hover:not(:disabled) { background: rgba(255, 255, 255, 0.28); }
.iv-nav:disabled { opacity: 0.25; cursor: not-allowed; }
.iv-counter {
  position: absolute;
  top: 18px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  font-size: 0.8rem;
  font-weight: 700;
  padding: 4px 14px;
  border-radius: 14px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  letter-spacing: 0.5px;
  pointer-events: none;
}
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
.iv-cap.ref { background: linear-gradient(135deg, #ffb3d1, #ff8fb3); color: #fff; }
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
  align-items: center;
  flex: 0 0 auto;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  margin-bottom: 8px;
}
.iv-star {
  padding: 4px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid #ffd257;
  background: rgba(255, 210, 87, 0.15);
  color: #ffd257;
  transition: all 0.15s;
}
.iv-star:hover { background: rgba(255, 210, 87, 0.3); }
.iv-star.on { background: #ffd257; color: #1a1206; border-color: #ffd257; }
/* NSFW */
.iv-nsfw-blur { filter: blur(20px) !important; transform: scale(1.05); }
.iv-nsfw-reveal {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  padding: 10px 22px;
  border: none;
  border-radius: 30px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  backdrop-filter: blur(4px);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.4);
  z-index: 3;
}
.iv-nsfw-reveal:hover { background: rgba(0, 0, 0, 0.75); }
.iv-imgwrap { position: relative; }
.iv-blur {
  padding: 4px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid #ff6b6b;
  background: rgba(255, 107, 107, 0.15);
  color: #ff6b6b;
  transition: all 0.15s;
}
.iv-blur:hover { background: rgba(255, 107, 107, 0.3); }
.iv-blur.on { background: #ff6b6b; color: #fff; border-color: #ff6b6b; }
.iv-check {
  margin-left: 8px;
  padding: 1px 10px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid #8a8a8a;
  background: transparent;
  color: inherit;
  transition: all 0.15s;
}
.iv-check:hover { background: rgba(128, 128, 128, 0.15); }
.iv-check:disabled { opacity: 0.5; cursor: not-allowed; }
.iv-row-sha .v { display: flex; align-items: flex-start; min-width: 0; }
.iv-sha {
  font-family: var(--n-font-mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace);
  font-size: 11px;
  word-break: break-all;
  white-space: pre-wrap;
  color: #d3d3d3;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(128, 128, 128, 0.12);
  transition: background 0.15s;
}
.iv-sha:hover { background: rgba(128, 128, 128, 0.22); }
.iv-row {
  display: flex;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  flex: 0 0 auto;
}
.iv-row .k { flex: 0 0 64px; color: rgba(255, 255, 255, 0.5); }
.iv-row .v { flex: 1; color: #e6e6f0; word-break: break-word; white-space: pre-wrap; }
.iv-row-tags { align-items: flex-start; padding-top: 8px; }
.iv-tags { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.iv-tag {
  cursor: default;
  transition: transform 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease;
}
.iv-tag:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
}
.iv-tag :deep(.n-tag__close) {
  opacity: 0.55;
  transition: opacity 0.15s ease, background 0.15s ease;
}
.iv-tag:hover :deep(.n-tag__close) { opacity: 1; }
.iv-tag :deep(.n-tag__close:hover) {
  background: rgba(255, 77, 79, 0.25);
  color: #ff6b6b !important;
}
.iv-tag-empty { color: rgba(255, 255, 255, 0.35); font-size: 12px; font-style: italic; }
.iv-tag-add { display: inline-flex; align-items: center; }
.iv-tag-add :deep(.n-input) {
  border-radius: 12px;
  transition: box-shadow 0.15s ease, border-color 0.15s ease;
}
.iv-tag-add :deep(.n-input:focus-within) {
  border-color: var(--primary-color, #7c4dff);
  box-shadow: 0 0 0 2px rgba(124, 77, 255, 0.18);
}
.iv-prompt { flex: 1 1 auto; min-height: 60px; align-items: stretch; }
.iv-prompt .v { overflow: auto; max-height: 100%; padding-right: 4px; }
@media (max-width: 760px) {
  .iv-body { flex-direction: column; }
  .iv-info { width: 100%; flex: 0 0 auto; height: auto; max-height: 40vh; overflow: auto; border-left: none; border-top: 1px solid rgba(255,255,255,0.08); }
  .iv-imgwrap img { max-width: 92vw; max-height: calc(40vh - 40px); }
  .iv-imgs { padding: 16px 10px; gap: 12px; flex-direction: column; }
  /* 图生图并排改为纵向堆叠，避免窄屏挤压 */
  .iv-imgs[data-pair="1"] .iv-fig { flex: 0 0 auto; min-width: 0; max-width: 100%; }
  .iv-imgs[data-pair="1"] .iv-imgwrap img { max-height: calc(40vh - 40px); }
}
</style>
