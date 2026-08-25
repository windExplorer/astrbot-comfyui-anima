<template>
  <div class="share-root">
    <!-- 过期 / 无效 404 -->
    <div v-if="expired" class="share-404">
      <div class="s404-card">
        <div class="s404-emoji">🔗⏰</div>
        <div class="s404-title">链接已失效</div>
        <div class="s404-sub">该分享链接已过期或不存在。<br />请重新发送 /萌绘 获取新的临时链接。</div>
      </div>
    </div>

    <template v-else>
      <!-- 顶部：品牌 + 主题/版本 + 当前用户 -->
      <header class="sh-header">
        <div class="sh-brand">
          🎨 萌绘图库
          <span class="sh-ver" title="插件版本号">{{ PLUGIN_VERSION }}</span>
        </div>
        <div class="sh-header-right">
          <button class="sh-theme" @click="toggleDark" :title="isDark ? '切换到浅色' : '切换到深色'">
            {{ isDark ? "🌙" : "☀️" }}
          </button>
          <div class="sh-user" v-if="me" @click="switchTab('profile')">
            <img :src="avatarSrc" class="sh-avatar" @error="avatarFallback" />
            <div class="sh-user-info">
              <div class="sh-user-name">@{{ me.user_name || me.user_id }}</div>
              <div class="sh-user-exp">有效期至 {{ expireText }}</div>
            </div>
          </div>
        </div>
      </header>

      <!-- 内容区（每个 tab 内部虚拟滚动） -->
      <main class="sh-main">
        <!-- 世界 -->
        <section v-if="tab === 'world'" class="sh-pane">
          <VirtualWaterfall
            :items="world.list"
            :img-src="m => imgUrl(m.sha256, true, 600)"
            :has-more="world.hasMore"
            :load-more="loadWorld"
            :loading="world.loading"
            :nsfw="nsfwBlurGlobal"
            :refresh="refreshWorld"
            @item-click="m => openViewer(m, world.list, 'world')"
          >
            <template #info="{ item: m }">
              <span class="who">{{ m.user_id === me?.user_id ? "我" : (m.user_name || m.user_id) }}</span>
              <span class="when">{{ fmtShort(m.created_at) }}</span>
            </template>
          </VirtualWaterfall>
          <n-empty v-if="!world.loading && world.list.length === 0" description="还没有公开作品~" class="sh-empty" />
        </section>

        <!-- 图库 -->
        <section v-else-if="tab === 'gallery'" class="sh-pane">
          <FloatFilter
            :options="galleryVisOpts"
            :model-value="galleryVis"
            :top="64"
            :right="12"
            @update:model-value="onVisChange"
          />
          <VirtualWaterfall
            :items="gallery.list"
            :img-src="m => imgUrl(m.sha256, true, 600)"
            :has-more="gallery.hasMore"
            :load-more="loadGallery"
            :loading="gallery.loading"
            :nsfw="nsfwBlurGlobal"
            :refresh="refreshGallery"
            @item-click="m => openViewer(m, gallery.list, 'gallery')"
          >
            <template #badges="{ item: m }">
              <div class="wf-badges">
                <span class="bstate" :class="m.is_public ? 'bpub' : 'bpriv'">{{ m.is_public ? "公开" : "私有" }}</span>
              </div>
            </template>
            <template #info="{ item: m }">
              <span class="when">{{ fmtShort(m.created_at) }}</span>
            </template>
          </VirtualWaterfall>
          <n-empty v-if="!gallery.loading && gallery.list.length === 0" description="还没有作品~" class="sh-empty" />
        </section>

        <!-- 收藏 -->
        <section v-else-if="tab === 'favorites'" class="sh-pane">
          <FloatFilter
            :options="favVisOpts"
            :model-value="favVis"
            :top="64"
            :right="12"
            @update:model-value="onFavVisChange"
          />
          <VirtualWaterfall
            :items="favList"
            :img-src="m => imgUrl(m.sha256, true, 600)"
            :nsfw="nsfwBlurGlobal"
            :refresh="loadFav"
            @item-click="m => openViewer(m, fav.list, 'fav')"
          >
            <template #info="{ item: m }">
              <span class="who">{{ m.user_id === me?.user_id ? "我" : (m.user_name || m.user_id) }}</span>
              <span class="when">{{ fmtShort(m.created_at) }}</span>
            </template>
          </VirtualWaterfall>
          <n-empty v-if="!fav.loading && favList.length === 0" description="还没有收藏~" class="sh-empty" />
        </section>

        <!-- 个人中心 -->
        <section v-else-if="tab === 'profile'" class="sh-pane">
          <template v-if="!showRecycle">
            <div class="user-card" v-if="stats && stats.user">
              <img :src="avatarSrc" class="uc-avatar" @error="avatarFallback" />
              <div class="uc-info">
                <div class="uc-name">@{{ stats.user.user_name || stats.user.user_id }}</div>
                <div class="uc-sub">UID：{{ stats.user.user_id }}</div>
                <div class="uc-sub" v-if="stats.user.first_seen">首次使用：{{ fmt(stats.user.first_seen) }}</div>
                <div class="uc-sub" v-if="stats.user.last_seen">最近活跃：{{ fmt(stats.user.last_seen) }}</div>
              </div>
            </div>
            <div class="stats" v-if="stats">
              <div class="stat"><b>{{ stats.total }}</b><span>总作品</span></div>
              <div class="stat"><b>{{ stats.public }}</b><span>公开</span></div>
              <div class="stat"><b>{{ stats.private }}</b><span>私有</span></div>
              <div class="stat"><b>{{ stats.favorites }}</b><span>收藏</span></div>
              <div class="stat"><b>{{ stats.likes_received }}</b><span>获赞</span></div>
              <div class="stat"><b>{{ stats.recycle }}</b><span>回收站</span></div>
            </div>
            <n-button block size="small" secondary @click="showRecycle = true">
              🗑️ 回收站（{{ stats?.recycle ?? recycle.length }}）
            </n-button>
          </template>

          <!-- 回收站二级页 -->
          <template v-else>
            <div class="sub-head">
              <n-button size="small" quaternary @click="showRecycle = false">← 返回</n-button>
              <span class="sub-title">回收站</span>
            </div>
            <div class="glist">
              <VirtualWaterfall
                :items="recycle"
                :img-src="m => imgUrl(m.sha256, true, 600)"
                :nsfw="nsfwBlurGlobal"
                :refresh="refreshRecycle"
                @item-click="m => openViewer(m, recycle, 'recycle')"
              >
                <template #info="{ item: m }">
                  <span class="when">{{ fmtShort(m.created_at) }}</span>
                </template>
              </VirtualWaterfall>
            </div>
            <n-empty v-if="recycle.length === 0" description="回收站是空的" class="sh-empty" />
          </template>
        </section>
      </main>

      <!-- 底部悬浮导航 -->
      <nav class="sh-tabbar">
        <button v-for="t in tabs" :key="t.key" :class="['tabbar-item', tab === t.key && 'on']" @click="switchTab(t.key)">
          <span class="tabbar-icon">{{ t.icon }}</span>
          <span class="tabbar-label">{{ t.label }}</span>
        </button>
      </nav>
    </template>

    <!-- 大图查看器（全屏覆盖 + 底部拖拽抽屉） -->
    <n-modal v-model:show="viewer" :mask-closable="true" class="viewer-full-modal">
      <div class="vfull" @click.self="onVfullClick">
        <div class="vimg" @click.self="onVfullClick">
          <img :src="viewerSrc" @load="onViewerLoad" :class="imgFitClass" @click="onImgClick" />
          <div v-if="viewerLoading" class="vloading"><span class="vf-spin"></span>加载中…</div>
          <div class="vtools">
            <button v-if="viewerM && viewerM.is_img2img && viewerM.ref_sha256" class="vtool" @click="swapRef">{{ showRef ? "查看结果图" : "查看参考图" }}</button>
            <button class="vtool" @click="closeViewer">✕</button>
          </div>
          <button v-if="viewerList.length > 1" class="vnav left" @click="prevImg">‹</button>
          <button v-if="viewerList.length > 1" class="vnav right" @click="nextImg">›</button>
          <span v-if="viewerList.length > 1" class="vcount">{{ viewerIndex + 1 }}/{{ viewerList.length }}</span>
        </div>

        <!-- 底部半透明抽屉：上拉覆盖图片、下拉收起，跟手 -->
        <div
          v-if="viewerM"
          ref="drawerEl"
          class="vdrawer"
          :class="{ open: drawerOpen }"
          :style="drawerStyle"
          @pointerdown="onDrawerStart"
          @pointermove="onDrawerMove"
          @pointerup="onDrawerEnd"
          @pointercancel="onDrawerEnd"
        >
          <div class="vdrawer-grip"></div>
          <div ref="drawerBodyEl" class="vdrawer-body">
            <!-- 发布人 -->
            <div class="vd-user">
              <img :src="viewerAvatar" class="vd-avatar" @error="viewerAvatarFb = true" />
              <div class="vd-user-text">
                <div class="vd-name">{{ viewerM.user_name || viewerM.user_id }} <span v-if="viewerM.user_id === me?.user_id" class="vd-me">我的</span></div>
                <div class="vd-time">{{ fmt(viewerM.created_at) }}</div>
              </div>
            </div>
            <!-- 统计 + 公开私有 -->
            <div class="vd-stats">
              <button class="vstat" :class="{ on: viewerM.liked }" @click="toggleLike(viewerM)">❤ <b>{{ viewerM.like_count }}</b></button>
              <button class="vstat" :class="{ on: viewerM.favorited }" @click="toggleFav(viewerM)">★ <b>{{ viewerM.favorite_count }}</b></button>
              <span class="vstat-badge" :class="viewerM.is_public ? 'pub' : 'priv'">{{ viewerM.is_public ? "🌐 公开" : "🔒 私有" }}</span>
            </div>
            <!-- 信息行 -->
            <div class="vd-rows">
              <div class="vd-row">
                <span class="vlabel">尺寸</span>
                <span class="vvalue">{{ viewerM.w }}×{{ viewerM.h }}</span>
              </div>
              <div class="vd-row">
                <span class="vlabel">工作流</span>
                <span class="vvalue">{{ viewerM.is_img2img ? "🖼️ 图生图" : "✨ 文生图" }}</span>
              </div>
              <div class="vd-row">
                <span class="vlabel">NSFW 评分</span>
                <span class="vvalue" :class="nsfwLevelClass">{{ nsfwScoreText }}</span>
              </div>
            </div>
            <!-- 标签 -->
            <div class="vd-tags">
              <span class="vlabel">标签</span>
              <div class="vtag-list">
                <n-tag v-for="t in viewerTags" :key="t" size="small" round :bordered="false" class="vtag">{{ t }}</n-tag>
                <span v-if="viewerTags.length === 0" class="vtag-empty">暂无标签</span>
              </div>
            </div>
            <!-- 上下文操作 -->
            <div v-if="viewerOps.length" class="vd-ops">
              <button v-for="op in viewerOps" :key="op.key" class="vop" :class="op.cls" @click="op.run()">{{ op.label }}</button>
            </div>
          </div>
        </div>
      </div>
    </n-modal>

    <!-- NSFW 全局开关 -->
    <div v-if="nsfwBlurGlobal" class="nsfw-toggle" @click="nsfwBlurGlobal = false" title="关闭 NSFW 模糊">🔞 模糊</div>
    <div v-else class="nsfw-toggle off" @click="nsfwBlurGlobal = true" title="开启 NSFW 模糊">🔞 原图</div>

    <div v-if="toastMsg" class="toast">{{ toastMsg }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, reactive, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useMessage, useDialog, NModal, NEmpty, NButton, NTag } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";
import VirtualWaterfall from "@/components/VirtualWaterfall.vue";
import FloatFilter from "@/components/FloatFilter.vue";
import { useTheme } from "@/composables/useTheme";
import { PLUGIN_VERSION } from "@/version";

const { isDark, toggleDark } = useTheme();

const route = useRoute();
const router = useRouter();
const message = useMessage();
const dialog = useDialog();

// 分享令牌解析：share_t（当前）→ 路径参数 → token（旧）→ location.search
function _extractToken(): string {
  const fromShareT = route.query.share_t as string | undefined;
  if (fromShareT) return fromShareT;
  const fromParam = (route.params.token as string) || "";
  if (fromParam) return fromParam;
  const fromHash = route.query.token as string | undefined;
  if (fromHash) return fromHash;
  try {
    const hash = window.location.hash || "";
    const qi = hash.indexOf("?");
    if (qi >= 0) {
      const qs = new URLSearchParams(hash.slice(qi + 1));
      const t = qs.get("share_t") || qs.get("token");
      if (t) return t;
    }
  } catch { /* ignore */ }
  const ss = new URLSearchParams(window.location.search);
  return ss.get("share_t") || ss.get("token") || "";
}
const token = computed(_extractToken);
let sharedToken = "";
try { sharedToken = _extractToken(); } catch { /* ignore */ }
if (sharedToken) {
  try {
    document.cookie = "anima_share_token=" + encodeURIComponent(sharedToken) + "; path=/; max-age=86400; SameSite=Lax";
  } catch { /* ignore */ }
}

const expired = ref(false);
const diag = ref<{ url: string; token: string; error?: string } | null>(null);
const me = ref<any>(null);
const toastMsg = ref("");
let toastTimer: any = null;
function toast(m: string) {
  toastMsg.value = m;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toastMsg.value = ""), 1800);
}

const tabs = [
  { key: "world", icon: "🌍", label: "世界" },
  { key: "gallery", icon: "🖼️", label: "图库" },
  { key: "favorites", icon: "⭐", label: "收藏" },
  { key: "profile", icon: "👤", label: "我的" },
];
const tab = ref<string>("world");
try {
  const q = route.query.tab as string | undefined;
  if (q && tabs.some((t) => t.key === q)) tab.value = q;
} catch { /* ignore */ }
function switchTab(t: string) {
  tab.value = t;
  try {
    router.replace({ path: "/share", query: { ...route.query, tab: t } });
  } catch { /* ignore */ }
  if (t === "world" && world.list.length === 0) loadWorld();
  if (t === "gallery" && gallery.list.length === 0) loadGallery();
  if (t === "favorites") loadFav();
  if (t === "profile") loadProfile();
}

function imgUrl(sha: string, thumb = true, size = 0) {
  const tok = token.value || sharedToken || "";
  let u = `/share/img/${sha}${thumb ? "/thumb" : ""}?share_t=${encodeURIComponent(tok)}`;
  if (thumb && size) u += `&size=${size}`;
  // 缩略图格式版本：内容格式变更（如 PNG→WebP）时递增，强制浏览器刷新旧缓存
  if (thumb) u += "&v=2";
  return u;
}

async function getJ(path: string, params: any = {}): Promise<any> {
  const tok = token.value || sharedToken;
  if (!tok) throw new Error("缺少分享令牌");
  return await apiGet("share/" + path, { share_t: tok, ...params }, { share_t: tok });
}
async function postJ(path: string, body: any = {}): Promise<any> {
  const tok = token.value || sharedToken;
  if (!tok) throw new Error("缺少分享令牌");
  return await apiPost("share/" + path, { share_t: tok, ...body }, { share_t: tok });
}

function fmt(ts: number) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fmtShort(ts: number) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
const expireText = computed(() => (me.value ? fmt(me.value.expire_at) : ""));

// 头像
const avatarFb = ref(false);
const avatarSrc = computed(() => {
  if (!me.value) return "";
  const uid = me.value.user_id || "";
  const tok = encodeURIComponent(token.value || sharedToken || "");
  if (avatarFb.value) {
    return `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(uid)}&s=100`;
  }
  return `/share/avatar/${encodeURIComponent(uid)}?share_t=${tok}`;
});
function avatarFallback() {
  if (!avatarFb.value && me.value) avatarFb.value = true;
}

// ---- 世界 ----
const world = reactive({ list: [] as any[], offset: 0, loading: false, hasMore: true });
async function loadWorld() {
  if (world.loading || !world.hasMore) return;
  world.loading = true;
  try {
    const r = await getJ("world", { limit: 30, offset: world.offset });
    const items = r.images || [];
    world.list.push(...items);
    world.offset += items.length;
    world.hasMore = world.offset < (r.total || 0);
  } catch (e: any) {
    console.warn("[ShareView] world 加载失败", e.message);
    toast("世界加载失败: " + e.message);
  } finally {
    world.loading = false;
  }
}
async function refreshWorld() {
  world.list = [];
  world.offset = 0;
  world.hasMore = true;
  await loadWorld();
  toast("已刷新");
}

// ---- 图库 ----
const galleryVisOpts = [
  { label: "全部", value: "all" },
  { label: "公开", value: "public" },
  { label: "私有", value: "private" },
];
const galleryVis = ref("all");
const gallery = reactive({ list: [] as any[], offset: 0, loading: false, hasMore: true });
async function loadGallery() {
  if (gallery.loading || !gallery.hasMore) return;
  gallery.loading = true;
  try {
    const r = await getJ("gallery", { vis: galleryVis.value, limit: 30, offset: gallery.offset });
    const items = r.images || [];
    gallery.list.push(...items);
    gallery.offset += items.length;
    gallery.hasMore = gallery.offset < (r.total || 0);
  } catch (e: any) {
    toast("加载失败: " + e.message);
  } finally {
    gallery.loading = false;
  }
}
async function refreshGallery() {
  gallery.list = [];
  gallery.offset = 0;
  gallery.hasMore = true;
  await loadGallery();
  toast("已刷新");
}
function onVisChange(v: string) {
  galleryVis.value = v;
  gallery.list = [];
  gallery.offset = 0;
  gallery.hasMore = true;
  loadGallery();
}

// ---- 收藏 ----
const favVisOpts = [
  { label: "全部", value: "all" },
  { label: "我的", value: "mine" },
  { label: "他人", value: "others" },
];
const favVis = ref<"all" | "mine" | "others">("all");
const fav = reactive({ list: [] as any[], loading: false });
const favList = computed(() => {
  if (favVis.value === "mine") return fav.list.filter((x: any) => x.owner_is_me);
  if (favVis.value === "others") return fav.list.filter((x: any) => !x.owner_is_me);
  return fav.list;
});
function onFavVisChange(v: string) {
  favVis.value = v as "all" | "mine" | "others";
}
async function loadFav() {
  fav.loading = true;
  try {
    const r = await getJ("favorites", { limit: 100, offset: 0 });
    fav.list = r.images || [];
  } catch (e: any) {
    toast("加载失败: " + e.message);
  } finally {
    fav.loading = false;
  }
}

// ---- 个人中心 ----
const stats = ref<any>(null);
const recycle = ref<any[]>([]);
const showRecycle = ref(false);
async function loadProfile() {
  try {
    stats.value = await getJ("profile");
  } catch (e: any) {
    toast("加载失败: " + e.message);
  }
  try {
    const r = await getJ("recycle");
    recycle.value = r.images || [];
  } catch { /* ignore */ }
}
async function refreshRecycle() {
  try {
    const r = await getJ("recycle");
    recycle.value = r.images || [];
    toast("已刷新");
  } catch (e: any) {
    toast("刷新失败: " + e.message);
  }
}

// ---- 操作 ----
async function toggleLike(m: any) {
  try {
    const r = await postJ("like", { sha: m.sha256, on: !m.liked });
    m.liked = r.liked;
    m.like_count = r.like_count;
  } catch (e: any) { toast("操作失败: " + e.message); }
}
async function toggleFav(m: any) {
  try {
    const r = await postJ("favorite", { sha: m.sha256, on: !m.favorited });
    m.favorited = r.favorited;
    m.favorite_count = r.favorite_count;
  } catch (e: any) { toast("操作失败: " + e.message); }
}
async function setPublic(m: any, on: boolean) {
  try {
    const r = await postJ("set_public", { sha: m.sha256, on });
    m.is_public = r.is_public;
    toast(on ? "已设为公开" : "已取消公开");
  } catch (e: any) { toast("操作失败: " + e.message); }
}
function delImg(m: any) {
  dialog.warning({
    title: "删除图片",
    content: "确定删除该图？将移入回收站（可在个人中心恢复）。",
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await postJ("delete", { sha: m.sha256 });
        gallery.list = gallery.list.filter((x) => x.sha256 !== m.sha256);
        viewer.value = false;
        toast("已移入回收站");
      } catch (e: any) { toast("操作失败: " + e.message); }
    },
  });
}
function restoreImg(sha: string) {
  dialog.warning({
    title: "恢复图片",
    content: "确定恢复该图？",
    positiveText: "恢复",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await postJ("restore", { sha });
        recycle.value = recycle.value.filter((x) => x.sha256 !== sha);
        viewer.value = false;
        toast("已恢复");
      } catch (e: any) { toast("操作失败: " + e.message); }
    },
  });
}

// ---- NSFW 模糊 ----
const nsfwBlurGlobal = ref(true);
try {
  const v = localStorage.getItem("anima_share_nsfw_blur");
  if (v != null) nsfwBlurGlobal.value = v === "1";
} catch { /* ignore */ }
watch(nsfwBlurGlobal, (val) => {
  try { localStorage.setItem("anima_share_nsfw_blur", val ? "1" : "0"); } catch { /* ignore */ }
});

// ---- 大图查看器（全屏覆盖 + 底部拖拽抽屉）----
const viewer = ref(false);
const viewerM = ref<any>(null);
const viewerList = ref<any[]>([]);
const viewerIndex = ref(0);
const viewerCtx = ref<"world" | "gallery" | "fav" | "recycle">("world");
const showRef = ref(false);
const viewerSrc = ref("");
const viewerLoading = ref(false);
const viewerAvatarFb = ref(false);
const imgFit = ref<"contain" | "cover">("contain");
const imgFitClass = computed(() => (imgFit.value === "cover" ? "img-cover" : "img-contain"));

// ---- 底部抽屉（覆盖式拖拽，跟手）----
const DRAWER_HEIGHT_VH = 62; // 抽屉固定高度（vh）
const drawerCollapsedPx = ref(0); // 收起位移 = 抽屉高 - grip 高
const drawerOffset = ref(0); // 0=展开；=collapsedPx=收起
const drawerOpen = computed(() => drawerOffset.value < drawerCollapsedPx.value / 2);
const drawerStyle = computed(() => ({ transform: `translateY(${drawerOffset.value}px)` }));
const drawerEl = ref<HTMLElement | null>(null);
const drawerBodyEl = ref<HTMLElement | null>(null);
let dragStartY = 0;
let dragStartOffset = 0;
let dragging = false;

function measureDrawer() {
  const vh = window.innerHeight || document.documentElement.clientHeight || 800;
  drawerCollapsedPx.value = Math.max(90, Math.round((DRAWER_HEIGHT_VH / 100) * vh - 34));
}

function onDrawerStart(e: PointerEvent) {
  dragging = true;
  dragStartY = e.clientY;
  dragStartOffset = drawerOffset.value;
  if (drawerEl.value) drawerEl.value.style.transition = "none";
  try { (e.target as HTMLElement).setPointerCapture?.(e.pointerId); } catch { /* ignore */ }
}
function onDrawerMove(e: PointerEvent) {
  if (!dragging) return;
  const dy = dragStartY - e.clientY; // 上拉 dy>0
  const body = drawerBodyEl.value;
  // 完全展开时向下拉：先让内容滚动，滚到顶再收抽屉
  if (drawerOffset.value <= 0 && dy < 0 && body && body.scrollTop > 0) return;
  drawerOffset.value = Math.max(0, Math.min(drawerCollapsedPx.value, dragStartOffset - dy));
}
function onDrawerEnd() {
  if (!dragging) return;
  dragging = false;
  if (drawerEl.value) drawerEl.value.style.transition = "";
  drawerOffset.value = drawerOffset.value < drawerCollapsedPx.value / 2 ? 0 : drawerCollapsedPx.value;
}

// 点击大图空白：抽屉展开时先收起，收起后再点才关闭查看器
function onVfullClick() {
  if (drawerOpen.value) {
    drawerOffset.value = drawerCollapsedPx.value;
  } else {
    closeViewer();
  }
}
// 点击图片：抽屉展开时先收起，收起后再点切换 contain/cover
function onImgClick() {
  if (drawerOpen.value) {
    drawerOffset.value = drawerCollapsedPx.value;
    return;
  }
  imgFit.value = imgFit.value === "contain" ? "cover" : "contain";
}

const viewerAvatar = computed(() => {
  const m = viewerM.value;
  if (!m) return "";
  const uid = m.user_id || "";
  const tok = encodeURIComponent(token.value || sharedToken || "");
  if (viewerAvatarFb.value) {
    return `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(uid)}&s=100`;
  }
  return `/share/avatar/${encodeURIComponent(uid)}?share_t=${tok}`;
});

const viewerTags = computed(() => {
  const m = viewerM.value;
  if (!m || !Array.isArray(m.tags)) return [];
  return m.tags.slice(0, 12);
});

const nsfwScoreText = computed(() => {
  const m = viewerM.value;
  if (!m || m.nsfw_score == null) return "未检测";
  const pct = Math.max(0, Math.min(100, Math.round((m.nsfw_score as number) * 100)));
  return `${pct}%`;
});
const nsfwLevelClass = computed(() => {
  const m = viewerM.value;
  if (!m || m.nsfw_score == null) return "";
  const s = m.nsfw_score as number;
  if (s >= 0.6) return "lv-high";
  if (s >= 0.3) return "lv-mid";
  return "lv-low";
});

// 按打开上下文展示操作按钮
const viewerOps = computed(() => {
  const m = viewerM.value;
  if (!m) return [];
  switch (viewerCtx.value) {
    case "gallery":
      return [
        { key: "pub", label: m.is_public ? "设为私有" : "设为公开", cls: "op-pub", run: () => setPublic(m, !m.is_public) },
        { key: "del", label: "删除", cls: "op-del", run: () => delImg(m) },
      ];
    case "recycle":
      return [{ key: "restore", label: "恢复图片", cls: "op-restore", run: () => restoreImg(m.sha256) }];
    default:
      return [];
  }
});

function openViewer(m: any, list: any[], ctx: "world" | "gallery" | "fav" | "recycle" = "world") {
  viewerM.value = m;
  viewerList.value = list || [];
  const idx = (list || []).findIndex((x) => x.sha256 === m.sha256);
  viewerIndex.value = idx >= 0 ? idx : 0;
  viewerCtx.value = ctx;
  measureDrawer();
  drawerOffset.value = drawerCollapsedPx.value; // 默认收起，只露小白条
  viewerAvatarFb.value = false;
  imgFit.value = "contain";
  viewer.value = true;
  showRef.value = false;
  loadOriginal();
}
function loadOriginal() {
  const m = viewerM.value;
  if (!m) return;
  viewerLoading.value = true;
  const sha = showRef.value && m.ref_sha256 ? m.ref_sha256 : m.sha256;
  viewerSrc.value = imgUrl(sha, false);
}
function swapRef() {
  showRef.value = !showRef.value;
  loadOriginal();
}
function closeViewer() {
  viewer.value = false;
}
function onViewerLoad() {
  viewerLoading.value = false;
}
function nextImg() {
  const list = viewerList.value;
  if (list.length <= 1) return;
  viewerIndex.value = (viewerIndex.value + 1) % list.length;
  viewerM.value = list[viewerIndex.value];
  viewerAvatarFb.value = false;
  showRef.value = false;
  imgFit.value = "contain";
  loadOriginal();
}
function prevImg() {
  const list = viewerList.value;
  if (list.length <= 1) return;
  viewerIndex.value = (viewerIndex.value - 1 + list.length) % list.length;
  viewerM.value = list[viewerIndex.value];
  viewerAvatarFb.value = false;
  showRef.value = false;
  imgFit.value = "contain";
  loadOriginal();
}
function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") { closeViewer(); return; }
  if (!viewer.value) return;
  if (e.key === "ArrowRight") nextImg();
  if (e.key === "ArrowLeft") prevImg();
}

// ---- 生命周期 ----
onMounted(async () => {
  window.addEventListener("keydown", onKey);
  window.addEventListener("resize", measureDrawer);
  measureDrawer();
  const cur = token.value || sharedToken || "";
  if (!cur) {
    diag.value = { url: window.location.href, token: "(空)" };
    expired.value = true;
    return;
  }
  try {
    me.value = await getJ("me");
    if (tab.value === "world") loadWorld();
    else if (tab.value === "gallery") loadGallery();
    else if (tab.value === "favorites") loadFav();
    else if (tab.value === "profile") loadProfile();
  } catch (e: any) {
    diag.value = { url: window.location.href, token: cur, error: (e && e.message) || String(e) };
    expired.value = true;
  }
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  window.removeEventListener("resize", measureDrawer);
});
</script>

<style scoped>
/* 固定高度 + 锁定页面滚动：滚动只发生在瀑布流容器内，避免双滚动条 */
.share-root {
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: var(--bg-body, #fff6f9);
  display: flex;
  flex-direction: column;
}

/* ---- 顶部 ---- */
.sh-header {
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: rgba(255, 246, 249, 0.92);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border-color, #ffe3ec);
}
.sh-brand { font-size: 16px; font-weight: 800; color: #e86f9c; display: flex; align-items: center; gap: 6px; }
.sh-ver {
  font-size: 10px; font-weight: 600; color: var(--text-sub, #9a7a88);
  background: var(--bg-panel, #fff); border: 1px solid var(--border-color, #ffe3ec);
  border-radius: 999px; padding: 1px 8px;
}
.sh-header-right { display: flex; align-items: center; gap: 10px; }
.sh-theme {
  width: 32px; height: 32px; border-radius: 50%; cursor: pointer;
  border: 1px solid var(--border-color, #ffe3ec); background: var(--bg-panel, #fff);
  font-size: 15px; display: flex; align-items: center; justify-content: center;
}
.sh-user { display: flex; align-items: center; gap: 8px; cursor: pointer; min-width: 0; }
.sh-avatar { width: 34px; height: 34px; border-radius: 50%; object-fit: cover; border: 2px solid #ffb3d1; flex: 0 0 auto; }
.sh-user-name { font-size: 13px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 22vw; }
.sh-user-exp { font-size: 11px; color: var(--text-sub, #9a7a88); }
/* 窄屏：隐藏有效期文字，避免 header 拥挤 */
@media (max-width: 420px) {
  .sh-user-exp { display: none; }
  .sh-header { padding: 10px 10px; }
  .sh-header-right { gap: 8px; }
}

/* ---- 内容区 ---- */
.sh-main { flex: 1 1 auto; overflow: hidden; padding: 12px 12px 76px; }
.sh-pane { height: 100%; min-height: 0; }
.sh-empty { padding: 50px 0; }
.glist { height: calc(100% - 48px); }

/* ---- 瀑布流卡片叠层 ---- */
.who { color: #ffd3e3; font-weight: 700; text-shadow: 0 1px 3px rgba(0, 0, 0, 0.7); }
.when { opacity: 0.95; text-shadow: 0 1px 3px rgba(0, 0, 0, 0.7); }
.wf-badges { position: absolute; top: 6px; left: 6px; z-index: 3; display: flex; gap: 4px; }
.bstate {
  border-radius: 999px; padding: 2px 9px; font-size: 10px; font-weight: 600; color: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25);
}
.bstate.bpub { background: linear-gradient(135deg, #ff8fb3, #ff6b9d); }
.bstate.bpriv { background: rgba(60, 60, 60, 0.75); }

/* ---- 个人中心 ---- */
.user-card { display: flex; align-items: center; gap: 12px; background: var(--bg-panel, #fff); border: 1px solid var(--border-color, #ffe3ec); border-radius: 12px; padding: 14px 16px; margin-bottom: 12px; }
.uc-avatar { width: 48px; height: 48px; border-radius: 50%; object-fit: cover; border: 2px solid #ffb3d1; flex: 0 0 auto; }
.uc-name { font-size: 15px; font-weight: 700; }
.uc-sub { font-size: 12px; color: var(--text-sub, #9a7a88); margin-top: 2px; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 12px; }
.stat { background: var(--bg-panel, #fff); border: 1px solid var(--border-color, #ffe3ec); border-radius: 12px; padding: 12px 8px; text-align: center; }
.stat b { display: block; font-size: 20px; color: #e86f9c; }
.stat span { font-size: 11px; color: var(--text-sub, #9a7a88); }
.sub-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.sub-title { font-size: 15px; font-weight: 700; }

/* ---- 底部悬浮导航 ---- */
.sh-tabbar {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 55;
  display: flex; background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(12px);
  border-top: 1px solid var(--border-color, #ffe3ec);
  padding-bottom: env(safe-area-inset-bottom);
}
.tabbar-item {
  flex: 1; border: none; background: none; cursor: pointer;
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  padding: 8px 0 10px; color: var(--text-sub, #9a7a88); font-size: 11px;
}
.tabbar-item.on { color: #ff6b9d; }
.tabbar-icon { font-size: 20px; line-height: 1; }

/* ---- 大图查看器（全屏覆盖 + 底部拖拽抽屉） ---- */
.viewer-full-modal { width: 100vw; height: 100vh; }
.vfull {
  width: 100vw; height: 100vh; height: 100dvh;
  background: #000; position: relative; overflow: hidden;
}
.vimg { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
.img-contain { object-fit: contain; max-width: 100%; max-height: 100%; }
.img-cover { object-fit: cover; width: 100%; height: 100%; }
.vimg img { display: block; cursor: zoom-in; }
.vloading {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: #fff; background: rgba(0, 0, 0, 0.45); font-size: 13px; gap: 8px;
}
.vf-spin {
  display: inline-block; width: 16px; height: 16px;
  border: 2px solid rgba(255, 143, 179, 0.3); border-top-color: #ff8fb3;
  border-radius: 50%; animation: vf-spin 0.8s linear infinite;
}
@keyframes vf-spin { to { transform: rotate(360deg); } }

.vtools {
  position: absolute; top: calc(env(safe-area-inset-top) + 12px); right: 12px; z-index: 6;
  display: flex; gap: 8px;
}
.vtool {
  border: none; background: rgba(0, 0, 0, 0.55); color: #fff; border-radius: 999px;
  padding: 8px 16px; font-size: 13px; cursor: pointer; backdrop-filter: blur(8px);
}
.vnav {
  position: absolute; top: 50%; transform: translateY(-50%); z-index: 6;
  width: 44px; height: 44px; border-radius: 50%; border: none; cursor: pointer;
  background: rgba(0, 0, 0, 0.4); color: #fff; font-size: 26px; line-height: 1;
  backdrop-filter: blur(6px);
}
.vnav.left { left: 12px; }
.vnav.right { right: 12px; }
.vcount {
  position: absolute; top: calc(env(safe-area-inset-top) + 16px); left: 50%; transform: translateX(-50%); z-index: 6;
  color: rgba(255, 255, 255, 0.85); font-size: 12px;
  background: rgba(0, 0, 0, 0.45); padding: 4px 14px; border-radius: 999px;
}

/* ---- 底部拖拽抽屉：覆盖在图片上，不挤压图片 ---- */
.vdrawer {
  position: absolute; left: 0; right: 0; bottom: 0; z-index: 10;
  height: 62vh; max-height: 62vh;
  display: flex; flex-direction: column;
  color: #fff;
  background: rgba(20, 16, 18, 0.72);
  backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 18px 18px 0 0;
  transition: transform 0.28s cubic-bezier(0.25, 0.8, 0.35, 1);
  touch-action: pan-y;
  user-select: none;
}
.vdrawer-grip {
  width: 44px; height: 5px; border-radius: 3px; background: rgba(255, 255, 255, 0.35);
  margin: 10px auto 4px; flex: 0 0 auto; cursor: grab;
}
.vdrawer-body {
  flex: 1 1 auto; min-height: 0; overflow-y: auto;
  padding: 2px 16px 16px;
  -webkit-overflow-scrolling: touch;
}

.vd-user { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.vd-avatar { width: 44px; height: 44px; border-radius: 50%; object-fit: cover; border: 2px solid rgba(255, 143, 179, 0.6); flex: 0 0 auto; background: #333; }
.vd-name { font-size: 15px; font-weight: 700; }
.vd-me { font-size: 10px; color: #ff9dc4; border: 1px solid rgba(255, 157, 196, 0.5); border-radius: 999px; padding: 0 6px; margin-left: 4px; }
.vd-time { font-size: 12px; color: rgba(255, 255, 255, 0.55); margin-top: 2px; }

.vd-stats { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.vstat {
  border: none; cursor: pointer; border-radius: 12px; padding: 9px 18px; font-size: 13px;
  background: rgba(255, 255, 255, 0.1); color: #fff; display: inline-flex; align-items: center; gap: 6px;
  min-height: 40px;
}
.vstat.on { background: linear-gradient(135deg, #ff8fb3, #ff6b9d); }
.vstat b { font-weight: 700; }
.vstat-badge { border-radius: 12px; padding: 6px 12px; font-size: 12px; font-weight: 600; min-height: 40px; display: inline-flex; align-items: center; }
.vstat-badge.pub { background: rgba(46, 158, 91, 0.25); color: #7fe0a8; border: 1px solid rgba(46, 158, 91, 0.45); }
.vstat-badge.priv { background: rgba(255, 180, 90, 0.18); color: #ffc277; border: 1px solid rgba(255, 180, 90, 0.4); }

.vd-rows { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.vd-row { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.vlabel { color: rgba(255, 255, 255, 0.45); width: 88px; flex: 0 0 auto; white-space: nowrap; }
.vvalue { color: #fff; }
.vvalue.lv-low { color: #7fe0a8; }
.vvalue.lv-mid { color: #ffc277; }
.vvalue.lv-high { color: #ff7b7b; }

.vd-tags { margin-bottom: 14px; }
.vtag-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.vtag {
  --n-color: rgba(255, 143, 179, 0.16);
  --n-color-hover: rgba(255, 143, 179, 0.22);
  --n-text-color: #ffd3e3;
  --n-text-color-hover: #fff;
}
.vtag-empty { font-size: 12px; color: rgba(255, 255, 255, 0.4); }

.vd-ops { display: flex; gap: 10px; flex-wrap: wrap; }
.vop {
  border: none; cursor: pointer; border-radius: 12px; min-height: 46px;
  padding: 12px 22px; font-size: 14px; font-weight: 600;
  background: rgba(255, 255, 255, 0.12); color: #fff;
}
.vop.op-pub { background: rgba(255, 143, 179, 0.28); color: #ffd3e3; }
.vop.op-del { background: rgba(214, 69, 65, 0.35); color: #ffb3b3; }
.vop.op-restore { background: linear-gradient(135deg, #2e9e5b, #1e7a44); color: #d9f7e5; }

/* ---- NSFW 开关 ---- */
.nsfw-toggle {
  position: fixed; right: 14px; bottom: 84px; z-index: 60;
  background: rgba(255, 143, 179, 0.9); color: #fff; border-radius: 999px;
  padding: 6px 12px; font-size: 12px; cursor: pointer; box-shadow: 0 2px 10px rgba(255, 143, 179, 0.4);
}
.nsfw-toggle.off { background: rgba(120, 120, 120, 0.85); }

/* ---- 失效页 ---- */
.share-404 { height: 100%; overflow-y: auto; display: flex; align-items: center; justify-content: center; padding: 24px; }
.s404-card { width: 380px; max-width: 92vw; background: var(--bg-panel, #fff); border-radius: 16px; padding: 40px 32px; box-shadow: 0 8px 30px rgba(255, 143, 179, 0.18); text-align: center; }
.s404-emoji { font-size: 42px; }
.s404-title { font-size: 20px; font-weight: 700; margin: 12px 0 8px; }
.s404-sub { font-size: 14px; color: #9a7a88; line-height: 1.7; }
.s404-diag { margin-top: 14px; padding: 10px 12px; background: #fff1f4; border: 1px dashed #ffb3c9; border-radius: 8px; font-size: 11px; color: #7a5a68; text-align: left; word-break: break-all; line-height: 1.5; }

/* toast */
.toast {
  position: fixed; top: 60px; left: 50%; transform: translateX(-50%); z-index: 100;
  background: rgba(0,0,0,0.75); color: #fff; border-radius: 999px; padding: 8px 18px; font-size: 13px;
}

/* 暗色适配 */
html[data-theme="dark"] .share-root, html.dark .share-root { background: #1a1418; }
html[data-theme="dark"] .sh-header, html.dark .sh-header { background: rgba(26,20,24,0.92); }
html[data-theme="dark"] .sh-tabbar, html.dark .sh-tabbar { background: rgba(36,27,33,0.95); }
</style>
