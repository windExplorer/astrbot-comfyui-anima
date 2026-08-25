<template>
  <div class="share-root">
    <!-- 过期 / 无效 404 -->
    <div v-if="expired" class="share-404">
      <div class="s404-card">
        <div class="s404-emoji">🔗⏰</div>
        <div class="s404-title">链接已失效</div>
        <div class="s404-sub">该分享链接已过期或不存在。<br />请重新发送 /萌绘 获取新的临时链接。</div>
        <div class="s404-diag" v-if="diag">
          <div><b>URL:</b> {{ diag.url }}</div>
          <div><b>token:</b> {{ diag.token }}</div>
          <div v-if="diag.error"><b>error:</b> {{ diag.error }}</div>
        </div>
      </div>
    </div>

    <template v-else>
      <!-- 顶部：品牌 + 当前用户 -->
      <header class="sh-header">
        <div class="sh-brand">🎨 萌绘图库</div>
        <div class="sh-user" v-if="me" @click="switchTab('profile')">
          <img :src="avatarSrc" class="sh-avatar" @error="avatarFallback" />
          <div class="sh-user-info">
            <div class="sh-user-name">@{{ me.user_name || me.user_id }}</div>
            <div class="sh-user-exp">有效期至 {{ expireText }}</div>
          </div>
        </div>
      </header>

      <!-- 内容区 -->
      <main class="sh-main" ref="mainRef" @scroll.passive="onMainScroll">
        <!-- 世界 -->
        <section v-if="tab === 'world'" class="sh-pane">
          <div class="waterfall">
            <div v-for="m in world.list" :key="m.sha256" class="wf-item" @click="openViewer(m, world.list)">
              <div class="wf-img" :class="{ 'nsfw-blur': isNsfwBlurred(m) }">
                <img :src="imgUrl(m.sha256, true, 600)" loading="lazy" :data-sha="m.sha256" />
              </div>
              <div v-if="isNsfwBlurred(m)" class="nsfw-mask"><span>🔞</span><span class="nsfw-tip">点击查看</span></div>
              <div class="wf-meta">
                <span v-if="m.user_id !== me?.user_id" class="who">{{ m.user_name || m.user_id }}</span>
                <span class="when">{{ fmtShort(m.created_at) }}</span>
              </div>
              <div class="wf-acts">
                <button class="like" :class="{ on: m.liked }" @click.stop="toggleLike(m)">❤ {{ m.like_count }}</button>
                <button class="fav" :class="{ on: m.favorited }" @click.stop="toggleFav(m)">★ {{ m.favorite_count }}</button>
              </div>
            </div>
          </div>
          <n-empty v-if="!world.loading && world.list.length === 0" description="还没有公开作品~" class="sh-empty" />
          <div v-if="world.hasMore" class="sh-more">
            <n-button size="small" quaternary :loading="world.loading" @click="loadWorld">加载更多</n-button>
          </div>
        </section>

        <!-- 图库 -->
        <section v-else-if="tab === 'gallery'" class="sh-pane">
          <div class="gfilter">
            <n-radio-group v-model:value="galleryVis" size="small" @update:value="onVisChange">
              <n-radio-button value="all">全部</n-radio-button>
              <n-radio-button value="public">公开</n-radio-button>
              <n-radio-button value="private">私有</n-radio-button>
            </n-radio-group>
          </div>
          <div class="waterfall">
            <div v-for="m in gallery.list" :key="m.sha256" class="wf-item" @click="openViewer(m, gallery.list)">
              <div class="wf-img" :class="{ 'nsfw-blur': isNsfwBlurred(m) }">
                <img :src="imgUrl(m.sha256, true, 600)" loading="lazy" />
              </div>
              <div v-if="isNsfwBlurred(m)" class="nsfw-mask"><span>🔞</span><span class="nsfw-tip">点击查看</span></div>
              <div class="wf-badges">
                <button v-if="!m.is_public" class="bpub" title="设为公开" @click.stop="setPublic(m, true)">🌐公开</button>
                <button v-else class="bunpub" title="取消公开" @click.stop="setPublic(m, false)">🔓私有</button>
                <button class="bdel" title="删除" @click.stop="delImg(m)">🗑️</button>
              </div>
              <div class="wf-meta"><span class="when">{{ fmtShort(m.created_at) }}</span></div>
              <div class="wf-acts">
                <button class="like" :class="{ on: m.liked }" @click.stop="toggleLike(m)">❤ {{ m.like_count }}</button>
                <button class="fav" :class="{ on: m.favorited }" @click.stop="toggleFav(m)">★ {{ m.favorite_count }}</button>
              </div>
            </div>
          </div>
          <n-empty v-if="!gallery.loading && gallery.list.length === 0" description="还没有作品~" class="sh-empty" />
          <div v-if="gallery.hasMore" class="sh-more">
            <n-button size="small" quaternary :loading="gallery.loading" @click="loadGallery">加载更多</n-button>
          </div>
        </section>

        <!-- 收藏 -->
        <section v-else-if="tab === 'favorites'" class="sh-pane">
          <h3 class="fsec">自己的收藏</h3>
          <div class="waterfall">
            <div v-for="m in fav.mine" :key="m.sha256" class="wf-item" @click="openViewer(m, fav.mine)">
              <div class="wf-img" :class="{ 'nsfw-blur': isNsfwBlurred(m) }"><img :src="imgUrl(m.sha256, true, 600)" loading="lazy" /></div>
              <div v-if="isNsfwBlurred(m)" class="nsfw-mask"><span>🔞</span><span class="nsfw-tip">点击查看</span></div>
              <div class="wf-meta"><span class="when">{{ fmtShort(m.created_at) }}</span></div>
            </div>
          </div>
          <h3 class="fsec">其他人的收藏</h3>
          <div class="waterfall">
            <div v-for="m in fav.others" :key="m.sha256" class="wf-item" @click="openViewer(m, fav.others)">
              <div class="wf-img" :class="{ 'nsfw-blur': isNsfwBlurred(m) }"><img :src="imgUrl(m.sha256, true, 600)" loading="lazy" /></div>
              <div v-if="isNsfwBlurred(m)" class="nsfw-mask"><span>🔞</span><span class="nsfw-tip">点击查看</span></div>
              <div class="wf-meta"><span class="who">{{ m.user_name || m.user_id }}</span><span class="when">{{ fmtShort(m.created_at) }}</span></div>
            </div>
          </div>
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
            <div class="waterfall">
              <div v-for="m in recycle" :key="m.sha256" class="wf-item" @click="openViewer(m, recycle)">
                <div class="wf-img" :class="{ 'nsfw-blur': isNsfwBlurred(m) }"><img :src="imgUrl(m.sha256, true, 600)" loading="lazy" /></div>
                <div class="wf-acts"><button class="restore" @click.stop="restoreImg(m.sha256)">恢复</button></div>
              </div>
            </div>
            <n-empty v-if="recycle.length === 0" description="回收站是空的" class="sh-empty" />
          </template>
        </section>
      </main>

      <!-- 底部悬浮导航（移动端优先） -->
      <nav class="sh-tabbar">
        <button v-for="t in tabs" :key="t.key" :class="['tabbar-item', tab === t.key && 'on']" @click="switchTab(t.key)">
          <span class="tabbar-icon">{{ t.icon }}</span>
          <span class="tabbar-label">{{ t.label }}</span>
        </button>
      </nav>
    </template>

    <!-- 大图查看器 -->
    <n-modal v-model:show="viewer" :mask-closable="true" class="viewer-modal" :style="{ '--tw': 'min(94vw, 900px)' }">
      <div class="viewer" @click.self="closeViewer">
        <div class="vimg">
          <img :src="viewerSrc" @load="onViewerLoad" @click="viewerFull = !viewerFull" />
          <div v-if="viewerLoading" class="vloading">加载中…</div>
          <div class="vtools">
            <button v-if="viewerM && viewerM.is_img2img && viewerM.ref_sha256" class="vtool" @click="swapRef">{{ showRef ? "查看结果图" : "查看参考图" }}</button>
            <button class="vtool" @click="loadOriginal">原图</button>
            <button class="vtool" @click="closeViewer">✕</button>
          </div>
        </div>
        <div class="vmeta" v-if="viewerM">
          <span>{{ fmt(viewerM.created_at) }}</span>
          <span>{{ viewerM.w }}×{{ viewerM.h }}</span>
          <button class="like" :class="{ on: viewerM.liked }" @click="toggleLike(viewerM)">❤ {{ viewerM.like_count }}</button>
          <button class="fav" :class="{ on: viewerM.favorited }" @click="toggleFav(viewerM)">★ {{ viewerM.favorite_count }}</button>
        </div>
      </div>
    </n-modal>

    <!-- 全屏查看（NSFW 图点击后取消模糊查看） -->
    <n-modal v-model:show="viewerFull" :mask-closable="true" class="viewer-full-modal" @update:show="v => (viewerFull = v)">
      <div class="viewer-full" @click.self="viewerFull = false">
        <img :src="viewerSrc" />
        <button class="vfull-close" @click="viewerFull = false">✕</button>
      </div>
    </n-modal>

    <!-- NSFW 全局开关 -->
    <div v-if="nsfwBlurGlobal" class="nsfw-toggle" @click="nsfwBlurGlobal = false" title="关闭 NSFW 模糊">🔞 模糊</div>
    <div v-else class="nsfw-toggle off" @click="nsfwBlurGlobal = true" title="开启 NSFW 模糊">🔞 原图</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, reactive, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useMessage, useDialog, NModal, NEmpty, NButton, NRadioGroup, NRadioButton } from "naive-ui";
import { apiGet, apiPost } from "@/api/bridge";

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
// tab 与 URL 同步（路由化：刷新保持当前页）
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

// 头像：优先后端 /share/avatar/{uid}，加载失败回退 QQ 头像直链
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

// ---- 图库 ----
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
function onVisChange(v: string) {
  galleryVis.value = v;
  gallery.list = [];
  gallery.offset = 0;
  gallery.hasMore = true;
  loadGallery();
}

// ---- 收藏 ----
const fav = reactive({ mine: [] as any[], others: [] as any[], loading: false });
async function loadFav() {
  fav.loading = true;
  try {
    const r = await getJ("favorites", { limit: 100, offset: 0 });
    const items = r.images || [];
    fav.mine = items.filter((x: any) => x.owner_is_me);
    fav.others = items.filter((x: any) => !x.owner_is_me);
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
        toast("已恢复");
      } catch (e: any) { toast("操作失败: " + e.message); }
    },
  });
}

// ---- NSFW 模糊（参考 WebUI）----
const nsfwBlurGlobal = ref(true);
try {
  const v = localStorage.getItem("anima_share_nsfw_blur");
  if (v != null) nsfwBlurGlobal.value = v === "1";
} catch { /* ignore */ }
watch(nsfwBlurGlobal, (val) => {
  try { localStorage.setItem("anima_share_nsfw_blur", val ? "1" : "0"); } catch { /* ignore */ }
});
function isNsfwBlurred(img: any): boolean {
  if (!img || !img.nsfw) return false;
  if (!nsfwBlurGlobal.value) return false;
  if (img.nsfw_blur === 0) return false;
  return true;
}

// ---- 大图查看器（默认加载原图）----
const viewer = ref(false);
const viewerFull = ref(false);
const viewerM = ref<any>(null);
const viewerList = ref<any[]>([]);
const viewerIndex = ref(0);
const showRef = ref(false);
const viewerSrc = ref("");
const viewerLoading = ref(false);
function openViewer(m: any, list: any[]) {
  viewerM.value = m;
  viewerList.value = list || [];
  viewerIndex.value = (list || []).findIndex((x) => x.sha256 === m.sha256) || 0;
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
  viewerFull.value = false;
}
function onViewerLoad() {
  viewerLoading.value = false;
}
function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") { closeViewer(); return; }
  if (!viewer.value) return;
  if (e.key === "ArrowRight") nextImg();
  if (e.key === "ArrowLeft") prevImg();
}
function nextImg() {
  const list = viewerList.value;
  if (!list.length) return;
  viewerIndex.value = (viewerIndex.value + 1) % list.length;
  viewerM.value = list[viewerIndex.value];
  showRef.value = false;
  loadOriginal();
}
function prevImg() {
  const list = viewerList.value;
  if (!list.length) return;
  viewerIndex.value = (viewerIndex.value - 1 + list.length) % list.length;
  viewerM.value = list[viewerIndex.value];
  showRef.value = false;
  loadOriginal();
}

// ---- 触底加载更多 ----
const mainRef = ref<any>(null);
function onMainScroll() {
  const el = mainRef.value;
  if (!el) return;
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 120) {
    if (tab.value === "world") loadWorld();
    if (tab.value === "gallery") loadGallery();
  }
}

// ---- 生命周期 ----
onMounted(async () => {
  window.addEventListener("keydown", onKey);
  const cur = token.value || sharedToken || "";
  if (!cur) {
    diag.value = { url: window.location.href, token: "(空)" };
    expired.value = true;
    return;
  }
  try {
    me.value = await getJ("me");
    // 按当前 tab 加载（刷新后保持内容）
    if (tab.value === "world") loadWorld();
    else if (tab.value === "gallery") loadGallery();
    else if (tab.value === "favorites") loadFav();
    else if (tab.value === "profile") loadProfile();
  } catch (e: any) {
    diag.value = { url: window.location.href, token: cur, error: (e && e.message) || String(e) };
    expired.value = true;
  }
});
onUnmounted(() => window.removeEventListener("keydown", onKey));
</script>

<style scoped>
.share-root {
  min-height: 100vh;
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
.sh-brand { font-size: 16px; font-weight: 800; color: #e86f9c; }
.sh-user { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.sh-avatar { width: 34px; height: 34px; border-radius: 50%; object-fit: cover; border: 2px solid #ffb3d1; }
.sh-user-name { font-size: 13px; font-weight: 700; }
.sh-user-exp { font-size: 11px; color: var(--text-sub, #9a7a88); }

/* ---- 内容区 ---- */
.sh-main {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 12px 12px 76px; /* 底部导航高度留白 */
}
.sh-pane { min-height: 100%; }
.sh-empty { padding: 50px 0; }
.sh-more { text-align: center; padding: 14px 0; }
.fsec { font-size: 13px; color: var(--text-sub, #9a7a88); margin: 16px 0 8px; }

/* ---- 瀑布流（CSS 自适应列数）---- */
.waterfall { columns: 2; column-gap: 10px; }
@media (min-width: 700px) { .waterfall { columns: 3; } }
@media (min-width: 1000px) { .waterfall { columns: 4; } }
@media (min-width: 1400px) { .waterfall { columns: 5; } }
.wf-item {
  break-inside: avoid;
  margin-bottom: 10px;
  background: #fff;
  border: 1px solid var(--border-color, #ffe3ec);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(255, 143, 179, 0.1);
}
.wf-img { position: relative; width: 100%; }
.wf-img img { display: block; width: 100%; height: auto; }
.wf-meta { display: flex; align-items: center; justify-content: space-between; padding: 6px 10px 0; font-size: 11px; color: var(--text-sub, #9a7a88); }
.wf-acts { display: flex; gap: 6px; padding: 4px 8px 8px; }
.wf-acts button, .wf-badges button {
  border: none; background: #fff1f4; color: #b05c7a; border-radius: 999px;
  font-size: 11px; padding: 3px 10px; cursor: pointer;
}
.wf-acts button.on { background: #ff8fb3; color: #fff; }
.wf-badges { display: flex; gap: 6px; padding: 6px 10px 0; }
.wf-badges button { background: #fff1f4; }
.wf-badges .bdel { color: #d64541; }

/* ---- NSFW 模糊 ---- */
.nsfw-blur { filter: blur(14px); transform: scale(1.08); }
.nsfw-mask {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 4px; color: #fff;
  background: rgba(0, 0, 0, 0.15); font-size: 22px;
}
.nsfw-tip { font-size: 11px; opacity: 0.9; }
.nsfw-toggle {
  position: fixed; right: 14px; bottom: 84px; z-index: 60;
  background: rgba(255, 143, 179, 0.9); color: #fff; border-radius: 999px;
  padding: 6px 12px; font-size: 12px; cursor: pointer; box-shadow: 0 2px 10px rgba(255, 143, 179, 0.4);
}
.nsfw-toggle.off { background: rgba(120, 120, 120, 0.85); }

/* ---- 图库筛选 ---- */
.gfilter { display: flex; justify-content: center; margin-bottom: 12px; }

/* ---- 个人中心 ---- */
.user-card { display: flex; align-items: center; gap: 12px; background: #fff; border: 1px solid var(--border-color, #ffe3ec); border-radius: 12px; padding: 14px 16px; margin-bottom: 12px; }
.uc-avatar { width: 48px; height: 48px; border-radius: 50%; object-fit: cover; border: 2px solid #ffb3d1; flex: 0 0 auto; }
.uc-name { font-size: 15px; font-weight: 700; }
.uc-sub { font-size: 12px; color: var(--text-sub, #9a7a88); margin-top: 2px; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 12px; }
.stat { background: #fff; border: 1px solid var(--border-color, #ffe3ec); border-radius: 12px; padding: 12px 8px; text-align: center; }
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

/* ---- 大图查看器 ---- */
.viewer-modal { width: var(--tw); max-width: 94vw; }
.viewer { background: #000; border-radius: 12px; overflow: hidden; }
.vimg { position: relative; display: flex; align-items: center; justify-content: center; max-height: 78vh; }
.vimg img { max-width: 100%; max-height: 78vh; display: block; cursor: zoom-in; }
.vloading { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #fff; background: rgba(0,0,0,0.4); }
.vtools { position: absolute; bottom: 10px; right: 10px; display: flex; gap: 8px; }
.vtool { border: none; background: rgba(0,0,0,0.6); color: #fff; border-radius: 999px; padding: 5px 12px; font-size: 12px; cursor: pointer; }
.vmeta { display: flex; align-items: center; gap: 10px; padding: 10px 14px; color: #fff; background: #111; font-size: 12px; }
.vmeta button { border: none; background: rgba(255,255,255,0.15); color: #fff; border-radius: 999px; padding: 4px 10px; cursor: pointer; font-size: 12px; }
.vmeta button.on { background: #ff6b9d; }

/* 全屏查看 */
.viewer-full-modal { width: 100vw; height: 100vh; }
.viewer-full { width: 100vw; height: 100vh; display: flex; align-items: center; justify-content: center; background: #000; position: relative; }
.viewer-full img { max-width: 100%; max-height: 100%; object-fit: contain; }
.vfull-close { position: fixed; top: 14px; right: 14px; z-index: 70; border: none; background: rgba(0,0,0,0.6); color: #fff; width: 36px; height: 36px; border-radius: 50%; font-size: 16px; cursor: pointer; }

/* ---- 失效页 ---- */
.share-404 { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; }
.s404-card { width: 380px; max-width: 92vw; background: #fff; border-radius: 16px; padding: 40px 32px; box-shadow: 0 8px 30px rgba(255, 143, 179, 0.18); text-align: center; }
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
html[data-theme="dark"] .wf-item, html.dark .wf-item,
html[data-theme="dark"] .user-card, html.dark .user-card,
html[data-theme="dark"] .stat, html.dark .stat { background: #241b21; }
html[data-theme="dark"] .sh-tabbar, html.dark .sh-tabbar { background: rgba(36,27,33,0.95); }
</style>
