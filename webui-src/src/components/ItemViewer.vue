<template>
  <teleport to="body">
    <div v-if="show" class="iviewer">
      <button class="iv-close" @click="onClose" aria-label="关闭">✕</button>
      <div class="iv-body" @click.self="onClose">
        <button v-if="canNav" class="iv-nav iv-nav-prev" :disabled="navPrevDisabled" @click="onNav(-1)" aria-label="上一张">‹</button>
        <div v-if="canNav" class="iv-counter">{{ navIndex + 1 }} / {{ navTotal }}</div>
        <!-- 左侧：封面大图 -->
        <div class="iv-imgs" @click="onClose">
          <figure class="iv-fig">
            <div class="iv-imgwrap">
              <img v-if="resolvedSrc" :src="resolvedSrc" alt="" />
              <div v-else class="iv-loading">封面加载中…</div>
            </div>
            <figcaption class="iv-cap">{{ resolvedTitle }}</figcaption>
          </figure>
        </div>
        <!-- 右侧：字段信息 -->
        <aside class="iv-info" @click.stop>
          <div class="iv-title">{{ resolvedTitle }}</div>
          <div class="iv-rows">
            <div v-for="f in resolvedFields" :key="f.key" class="iv-row">
              <span class="k">{{ f.key }}</span>
              <span class="v">
                <a v-if="f.href" :href="f.href" target="_blank" rel="noopener noreferrer">{{ f.value || f.href }} ↗</a>
                <span v-else-if="f.html" v-html="sanitizeHtml(String(f.value || ''))"></span>
                <template v-else>{{ f.value || "—" }}</template>
              </span>
            </div>
          </div>
        </aside>
        <button v-if="canNav" class="iv-nav iv-nav-next" :disabled="navNextDisabled" @click="onNav(1)" aria-label="下一张">›</button>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
import { apiGet } from "@/api/bridge";
import { sanitizeHtml } from "@/utils/sanitizeHtml";

export interface ItemViewerField {
  key: string;
  value?: string | number | null;
  /** 存在时渲染为可点击外链 */
  href?: string;
  /** 为 true 时 value 按净化后的 HTML 渲染（用于富文本描述） */
  html?: boolean;
}

const props = defineProps<{
  show: boolean;
  /** 直接给已解析的 src（不使用 images 导航时） */
  src?: string;
  title?: string;
  fields?: ItemViewerField[];
  /** 导航列表：每项含封面文件名 fname，组件自动解析为 src */
  images?: Array<{ fname: string; title?: string; fields?: ItemViewerField[] }>;
  /** 当前在 images 中的索引 */
  index?: number;
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "nav", delta: number): void;
}>();

// 解析后的当前项（导航模式从 images[index] 取，非导航模式直接透传 props）
const resolvedSrc = ref<string>("");
const resolvedTitle = ref<string>("");
const resolvedFields = ref<ItemViewerField[]>([]);

const canNav = computed(() => Array.isArray(props.images) && props.images.length > 1);
const navIndex = computed(() => props.index ?? 0);
const navTotal = computed(() => (Array.isArray(props.images) ? props.images.length : 0));
const navPrevDisabled = computed(() => !canNav.value || navIndex.value <= 0);
const navNextDisabled = computed(() => !canNav.value || navIndex.value >= navTotal.value - 1);

function applyItem(fname?: string, title?: string, fields?: ItemViewerField[]) {
  resolvedTitle.value = title ?? "";
  resolvedFields.value = fields ?? [];
  if (fname) {
    resolvedSrc.value = "";
    apiGet("lora/image", { name: fname })
      .then((d: any) => { resolvedSrc.value = d?.url || ""; })
      .catch(() => { resolvedSrc.value = ""; });
  } else {
    resolvedSrc.value = "";
  }
}

// show 打开或 index 导航时重新解析当前项
watch(
  () => [props.show, props.index] as const,
  ([v, idx]) => {
    if (Array.isArray(props.images) && props.images.length) {
      const cur = props.images[Math.max(0, Math.min(props.images.length - 1, idx ?? 0))] || {};
      applyItem(cur.fname, cur.title, cur.fields);
    } else {
      resolvedSrc.value = props.src || "";
      resolvedTitle.value = props.title || "";
      resolvedFields.value = props.fields || [];
    }
  },
  { immediate: true }
);

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
</script>

<style scoped>
.iviewer {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(8, 8, 12, 0.9);
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
  align-items: stretch;   /* 让 figure 撑满左侧可用高度 */
  justify-content: center;
  padding: 28px;
  min-width: 0;
  min-height: 0;
  cursor: pointer;        /* 提示左侧可点击关闭 */
}
.iv-fig {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  width: 100%;
  max-width: 100%;
  align-items: center;
}
.iv-cap {
  flex: 0 0 auto;
  font-size: 0.74rem;
  font-weight: 700;
  padding: 3px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.1);
  color: #cfd2dc;
  letter-spacing: 0.3px;
  user-select: none;
}
.iv-imgwrap {
  flex: 1 1 auto;          /* 撑满剩余高度 */
  width: 100%;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
}
.iv-imgwrap img {
  width: 100%;            /* 铺满容器宽度 */
  height: 100%;           /* 铺满容器高度 */
  object-fit: contain;    /* 保持比例，撑满左侧（宽度或高度取较小约束） */
  display: block;
}
.iv-loading {
  color: rgba(255, 255, 255, 0.6);
  padding: 60px;
  font-size: 13px;
}
.iv-info {
  width: 380px;
  flex: 0 0 380px;
  height: 100%;
  overflow: hidden;
  padding: 20px 18px;
  border-left: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(20, 20, 28, 0.6);
  font-size: 0.84rem;
  line-height: 1.7;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}
.iv-title {
  flex: 0 0 auto;
  font-size: 1.05rem;
  font-weight: 700;
  color: #fff;
  padding-bottom: 12px;
  margin-bottom: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  word-break: break-word;
}
.iv-rows {
  flex: 1 1 auto;
  overflow: auto;
  padding-right: 4px;
}
.iv-row {
  display: flex;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}
.iv-row .k { flex: 0 0 72px; color: rgba(255, 255, 255, 0.5); }
.iv-row .v { flex: 1; color: #e6e6f0; word-break: break-word; white-space: pre-wrap; }
.iv-row .v a { color: #7aa2ff; text-decoration: none; }
.iv-row .v a:hover { text-decoration: underline; }
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
@media (max-width: 760px) {
  .iv-body { flex-direction: column; }
  .iv-info { width: 100%; flex: 0 0 auto; height: auto; max-height: 40vh; overflow: auto; border-left: none; border-top: 1px solid rgba(255,255,255,0.08); }
  .iv-imgwrap img { max-width: 92vw; }
}
</style>
