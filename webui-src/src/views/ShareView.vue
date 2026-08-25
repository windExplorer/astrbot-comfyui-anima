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
      <header class="share-header">
        <div class="sh-brand">🎨 萌绘图库</div>
        <div class="sh-user" v-if="me">@{{ me.user_name || me.user_id }} · 有效期至 {{ expireText }}</div>
      </header>

      <nav class="share-tabs">
        <button :class="['stab', tab === 'world' && 'on']" @click="switchTab('world')">🌍 世界</button>
        <button :class="['stab', tab === 'gallery' && 'on']" @click="switchTab('gallery')">🖼️ 图库</button>
        <button :class="['stab', tab === 'favorites' && 'on']" @click="switchTab('favorites')">⭐ 收藏</button>
        <button :class="['stab', tab === 'profile' && 'on']" @click="switchTab('profile')">👤 个人中心</button>
      </nav>

      <div class="share-body">
        <!-- 世界 -->
        <section v-if="tab === 'world'" class="grid">
          <div v-for="m in world.list" :key="m.sha256" class="card" @click="openViewer(m)">
            <div class="cimg" :class="{ blur: m.nsfw }">
              <img :src="imgUrl(m.sha256, true, 300)" loading="lazy" />
            </div>
            <div class="cmeta">
              <span v-if="m.user_id !== me?.user_id" class="who">{{ m.user_name || m.user_id }}</span>
              <span class="when">{{ fmt(m.created_at) }}</span>
              <span class="sz">{{ m.w }}×{{ m.h }}</span>
            </div>
            <div class="cacts" @click.stop>
              <button class="like" :class="{ on: m.liked }" @click="toggleLike(m)">❤ {{ m.like_count }}</button>
              <button class="fav" :class="{ on: m.favorited }" @click="toggleFav(m)">★ {{ m.favorite_count }}</button>
            </div>
          </div>
          <div v-if="!world.loading && world.list.length === 0" class="empty">还没有公开作品~</div>
          <div v-if="world.hasMore" class="more"><button @click="loadWorld">加载更多</button></div>
        </section>

        <!-- 图库 -->
        <section v-else-if="tab === 'gallery'" class="grid">
          <div class="gfilter">
            <button :class="['gf', galleryVis === 'all' && 'on']" @click="setVis('all')">全部</button>
            <button :class="['gf', galleryVis === 'public' && 'on']" @click="setVis('public')">公开</button>
            <button :class="['gf', galleryVis === 'private' && 'on']" @click="setVis('private')">私有</button>
          </div>
          <div v-for="m in gallery.list" :key="m.sha256" class="card">
            <div class="cimg" :class="{ blur: m.nsfw }">
              <img :src="imgUrl(m.sha256, true, 300)" loading="lazy" />
              <button v-if="!m.is_public" class="badge fav" title="收藏" @click.stop="toggleFav(m)">★</button>
              <button v-if="!m.is_public" class="badge pub" title="设为公开" @click.stop="setPublic(m, true)">🌐</button>
              <button v-if="m.is_public" class="badge unpub" title="取消公开" @click.stop="setPublic(m, false)">🔓</button>
              <button class="badge del" title="删除" @click.stop="delImg(m)">🗑️</button>
            </div>
            <div class="cmeta">
              <span class="when">{{ fmt(m.created_at) }}</span>
              <span class="sz">{{ m.w }}×{{ m.h }}</span>
            </div>
            <div class="cacts" @click.stop>
              <button v-if="m.is_public" class="like" :class="{ on: m.liked }" @click="toggleLike(m)">❤ {{ m.like_count }}</button>
              <button class="fav" :class="{ on: m.favorited }" @click="toggleFav(m)">★ {{ m.favorite_count }}</button>
            </div>
          </div>
          <div v-if="!gallery.loading && gallery.list.length === 0" class="empty">还没有作品~</div>
        </section>

        <!-- 收藏 -->
        <section v-else-if="tab === 'favorites'">
          <h3 class="fsec">自己的收藏</h3>
          <div class="grid">
            <div v-for="m in fav.mine" :key="m.sha256" class="card" @click="openViewer(m)">
              <div class="cimg" :class="{ blur: m.nsfw }"><img :src="imgUrl(m.sha256, true, 300)" loading="lazy" /></div>
              <div class="cmeta"><span class="when">{{ fmt(m.created_at) }}</span><span class="sz">{{ m.w }}×{{ m.h }}</span></div>
              <div class="cacts" @click.stop><button class="fav on" @click="toggleFav(m)">★ {{ m.favorite_count }}</button></div>
            </div>
            <div v-if="fav.mine.length === 0" class="empty">暂无</div>
          </div>
          <h3 class="fsec">其他人的收藏</h3>
          <div class="grid">
            <div v-for="m in fav.others" :key="m.sha256" class="card" @click="openViewer(m)">
              <div class="cimg" :class="{ blur: m.nsfw }"><img :src="imgUrl(m.sha256, true, 300)" loading="lazy" /></div>
              <div class="cmeta"><span class="who">{{ m.user_name || m.user_id }}</span><span class="when">{{ fmt(m.created_at) }}</span><span class="sz">{{ m.w }}×{{ m.h }}</span></div>
              <div class="cacts" @click.stop><button class="fav on" @click="toggleFav(m)">★ {{ m.favorite_count }}</button></div>
            </div>
            <div v-if="fav.others.length === 0" class="empty">暂无</div>
          </div>
        </section>

        <!-- 个人中心 -->
        <section v-else-if="tab === 'profile'" class="profile">
          <div class="user-card" v-if="stats && stats.user">
            <div class="uc-avatar">👤</div>
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
            <div class="stat"><b>{{ stats.favorites }}</b><span>我的收藏</span></div>
            <div class="stat"><b>{{ stats.likes_given }}</b><span>点赞发出</span></div>
            <div class="stat"><b>{{ stats.likes_received }}</b><span>获赞</span></div>
            <div class="stat"><b>{{ stats.recycle }}</b><span>回收站</span></div>
          </div>
          <h3 class="fsec">回收站</h3>
          <div class="grid">
            <div v-for="m in recycle" :key="m.sha256" class="card">
              <div class="cimg" :class="{ blur: m.nsfw }"><img :src="imgUrl(m.sha256, true, 300)" loading="lazy" /></div>
              <div class="cmeta"><span class="when">{{ fmt(m.created_at) }}</span><span class="sz">{{ m.w }}×{{ m.h }}</span></div>
              <div class="cacts"><button class="restore" @click="restoreImg(m.sha256)">恢复</button></div>
            </div>
            <div v-if="recycle.length === 0" class="empty">回收站是空的</div>
          </div>
        </section>
      </div>
    </template>

    <!-- 大图查看器 -->
    <div v-if="viewer" class="viewer-mask" @click.self="closeViewer">
      <div class="viewer">
        <button class="vclose" @click="closeViewer">✕</button>
        <div class="vimg">
          <img :src="viewerSrc" @load="onViewerLoad" />
          <div v-if="viewerLoading" class="vloading">加载中…</div>
          <button v-if="viewerM && viewerM.is_img2img && viewerM.ref_sha256" class="vswap" @click="swapRef">
            {{ showRef ? "查看结果图" : "查看参考图" }}
          </button>
          <button class="vorig" @click="viewOriginal">查看原图</button>
        </div>
        <div class="vmeta" v-if="viewerM">
          <span>{{ fmt(viewerM.created_at) }}</span>
          <span>{{ viewerM.w }}×{{ viewerM.h }}</span>
          <button class="like" :class="{ on: viewerM.liked }" @click="toggleLike(viewerM)">❤ {{ viewerM.like_count }}</button>
          <button class="fav" :class="{ on: viewerM.favorited }" @click="toggleFav(viewerM)">★ {{ viewerM.favorite_count }}</button>
        </div>
      </div>
    </div>

    <div v-if="toastMsg" class="toast">{{ toastMsg }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, reactive } from "vue";
import { useRoute } from "vue-router";
import { apiGet, apiPost } from "@/api/bridge";

const route = useRoute();
// hash 路由下 route.query 只解析 hash 内的 query（#/share?token=xxx）。
// 兼容旧版链接格式 ?token=xxx#/share（token 在 hash 外，路由解析不到），从 location.search 兜底取。
const token = computed(() => {
  const fromRoute = (route.query.token as string) || "";
  if (fromRoute) return fromRoute;
  return new URLSearchParams(window.location.search).get("token") || "";
});
const expired = ref(false);
const me = ref<any>(null);
const tab = ref("world");
const toastMsg = ref("");
let toastTimer: any = null;

function toast(m: string) {
  toastMsg.value = m;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toastMsg.value = ""), 1800);
}

function imgUrl(sha: string, thumb = true, size = 0) {
  let u = `/share/img/${sha}${thumb ? "/thumb" : ""}?token=${encodeURIComponent(token.value)}`;
  if (thumb && size) u += `&size=${size}`;
  return u;
}

async function getJ(path: string, params: any = {}): Promise<any> {
  const r: any = await apiGet("share/" + path, { token: token.value, ...params });
  if (!r || !r.ok) throw new Error((r && r.error) || "请求失败");
  return r.data;
}
async function postJ(path: string, body: any = {}): Promise<any> {
  const r: any = await apiPost("share/" + path, { token: token.value, ...body });
  if (!r || !r.ok) throw new Error((r && r.error) || "请求失败");
  return r.data;
}

function fmt(ts: number) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
const expireText = computed(() => (me.value ? fmt(me.value.expire_at) : ""));

// 世界
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
    if (/401|过期|无效/.test(String(e.message))) expired.value = true;
    else toast("加载失败: " + e.message);
  } finally {
    world.loading = false;
  }
}

// 图库
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
function setVis(v: string) {
  if (galleryVis.value === v) return;
  galleryVis.value = v;
  gallery.list = [];
  gallery.offset = 0;
  gallery.hasMore = true;
  loadGallery();
}

// 收藏
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

// 个人中心
const stats = ref<any>(null);
const recycle = ref<any[]>([]);
async function loadProfile() {
  try {
    stats.value = await getJ("profile");
  } catch (e: any) {
    toast("加载失败: " + e.message);
  }
  try {
    const r = await getJ("recycle");
    recycle.value = r.images || [];
  } catch {
    /* ignore */
  }
}

function switchTab(t: string) {
  tab.value = t;
  if (t === "world" && world.list.length === 0) loadWorld();
  if (t === "gallery") setVis(galleryVis.value);
  if (t === "favorites") loadFav();
  if (t === "profile") loadProfile();
}

async function toggleLike(m: any) {
  try {
    const r = await postJ("like", { sha: m.sha256, on: !m.liked });
    m.liked = r.liked;
    m.like_count = r.like_count;
  } catch (e: any) {
    toast("操作失败: " + e.message);
  }
}
async function toggleFav(m: any) {
  try {
    const r = await postJ("favorite", { sha: m.sha256, on: !m.favorited });
    m.favorited = r.favorited;
    m.favorite_count = r.favorite_count;
  } catch (e: any) {
    toast("操作失败: " + e.message);
  }
}
async function setPublic(m: any, on: boolean) {
  if (!confirm(on ? "确定将该图设为公开？公开后所有人可在「世界」看到。" : "确定取消公开？")) return;
  try {
    const r = await postJ("set_public", { sha: m.sha256, on });
    m.is_public = r.is_public;
    toast(on ? "已设为公开" : "已取消公开");
  } catch (e: any) {
    toast("操作失败: " + e.message);
  }
}
async function delImg(m: any) {
  if (!confirm("确定删除该图？将移入回收站（可在个人中心恢复）。")) return;
  try {
    await postJ("delete", { sha: m.sha256 });
    gallery.list = gallery.list.filter((x) => x.sha256 !== m.sha256);
    toast("已移入回收站");
  } catch (e: any) {
    toast("操作失败: " + e.message);
  }
}
async function restoreImg(sha: string) {
  if (!confirm("确定恢复该图？")) return;
  try {
    await postJ("restore", { sha });
    recycle.value = recycle.value.filter((x) => x.sha256 !== sha);
    toast("已恢复");
  } catch (e: any) {
    toast("操作失败: " + e.message);
  }
}

// 大图查看器
const viewer = ref(false);
const viewerM = ref<any>(null);
const showRef = ref(false);
const viewerSrc = ref("");
const viewerLoading = ref(false);
function openViewer(m: any) {
  viewerM.value = m;
  viewer.value = true;
  showRef.value = false;
  showViewer(m);
}
function showViewer(m: any) {
  viewerLoading.value = true;
  const sha = showRef.value && m.ref_sha256 ? m.ref_sha256 : m.sha256;
  viewerSrc.value = imgUrl(sha, true, 640);
}
function viewOriginal() {
  const m = viewerM.value;
  if (!m) return;
  viewerLoading.value = true;
  const sha = showRef.value && m.ref_sha256 ? m.ref_sha256 : m.sha256;
  viewerSrc.value = imgUrl(sha, false);
}
function swapRef() {
  showRef.value = !showRef.value;
  showViewer(viewerM.value);
}
function closeViewer() {
  viewer.value = false;
}
function onViewerLoad() {
  viewerLoading.value = false;
}
function onKey(e: KeyboardEvent) {
  if (e.key === "Escape" && viewer.value) closeViewer();
}

onMounted(async () => {
  window.addEventListener("keydown", onKey);
  if (!token.value) {
    expired.value = true;
    return;
  }
  try {
    me.value = await getJ("me");
    loadWorld();
  } catch (e: any) {
    expired.value = true;
  }
});
onUnmounted(() => window.removeEventListener("keydown", onKey));
</script>

<style scoped>
.share-root {
  width: 100%;
  min-height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  background: var(--bg-body, #fff6f9);
  color: var(--text-main, #3a2a33);
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "HarmonyOS Sans SC", "Segoe UI", Roboto, sans-serif;
}
.share-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  border-bottom: 1px solid var(--border-color, #ffe3ec);
  background: var(--bg-panel, #fff);
}
.sh-brand {
  font-size: 18px;
  font-weight: 800;
  background: linear-gradient(90deg, #ff8fb3, #ffb3d1);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.sh-user {
  font-size: 12px;
  opacity: 0.6;
}
.share-tabs {
  flex: 0 0 auto;
  display: flex;
  gap: 8px;
  padding: 10px 18px;
  flex-wrap: wrap;
}
.stab {
  border: 1px solid var(--border-color, #ffe3ec);
  background: var(--bg-panel, #fff);
  color: var(--text-sub, #9a7a88);
  padding: 7px 14px;
  border-radius: 999px;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.15s;
}
.stab.on {
  background: linear-gradient(135deg, #ff8fb3, #ff6b9d);
  color: #fff;
  border-color: transparent;
}
.share-body {
  flex: 1 1 auto;
  overflow: auto;
  padding: 12px 18px 28px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 14px;
}
.card {
  background: var(--bg-panel, #fff);
  border: 1px solid var(--border-color, #ffe3ec);
  border-radius: 14px;
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.12s, box-shadow 0.12s;
}
.card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(255, 143, 179, 0.18);
}
.cimg {
  position: relative;
  aspect-ratio: 1 / 1;
  background: #f3e6ec;
  overflow: hidden;
}
.cimg img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.cimg.blur img {
  filter: blur(14px);
}
.badge {
  position: absolute;
  top: 6px;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  border: none;
  background: rgba(255, 255, 255, 0.88);
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.12);
}
.badge.fav { right: 6px; color: #ff8fb3; }
.badge.pub { right: 40px; }
.badge.unpub { right: 40px; }
.badge.del { right: 6px; bottom: 6px; top: auto; color: #e74c3c; }
.cmeta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding: 7px 9px 2px;
  font-size: 11px;
  color: var(--text-sub, #9a7a88);
}
.cmeta .who {
  color: var(--accent, #ff8fb3);
  font-weight: 600;
}
.cacts {
  display: flex;
  gap: 6px;
  padding: 4px 9px 9px;
}
.cacts button {
  flex: 1;
  border: 1px solid var(--border-color, #ffe3ec);
  background: #fff;
  border-radius: 8px;
  padding: 5px 0;
  font-size: 12px;
  cursor: pointer;
  color: var(--text-sub, #9a7a88);
}
.cacts button.like.on { color: #ff5b8a; border-color: #ffb3c9; }
.cacts button.fav.on { color: #ffb000; border-color: #ffe08a; }
.gfilter {
  grid-column: 1 / -1;
  display: flex;
  gap: 8px;
  margin-bottom: 4px;
}
.gf {
  border: 1px solid var(--border-color, #ffe3ec);
  background: var(--bg-panel, #fff);
  color: var(--text-sub, #9a7a88);
  padding: 6px 14px;
  border-radius: 999px;
  cursor: pointer;
  font-size: 13px;
}
.gf.on {
  background: linear-gradient(135deg, #ff8fb3, #ff6b9d);
  color: #fff;
  border-color: transparent;
}
.fsec {
  margin: 16px 0 10px;
  font-size: 14px;
  color: var(--text-sub, #9a7a88);
}
.empty {
  grid-column: 1 / -1;
  text-align: center;
  padding: 40px 0;
  color: var(--text-sub, #9a7a88);
}
.more {
  grid-column: 1 / -1;
  text-align: center;
  padding: 14px 0;
}
.more button,
.restore {
  border: 1px solid var(--accent, #ff8fb3);
  background: #fff;
  color: var(--accent, #ff8fb3);
  padding: 7px 20px;
  border-radius: 999px;
  cursor: pointer;
}
.user-card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg-panel, #fff);
  border: 1px solid var(--border-color, #ffe3ec);
  border-radius: 12px;
  padding: 14px 16px;
  margin-bottom: 12px;
}
.uc-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: linear-gradient(135deg, #ffb3d1, #ff8fb3);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  flex: 0 0 auto;
}
.uc-name { font-size: 15px; font-weight: 700; }
.uc-sub { font-size: 12px; color: var(--text-sub, #9a7a88); margin-top: 2px; }
.profile .stats {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 12px;
  margin-bottom: 8px;
}
.stat {
  background: var(--bg-panel, #fff);
  border: 1px solid var(--border-color, #ffe3ec);
  border-radius: 12px;
  padding: 14px;
  text-align: center;
}
.stat b {
  display: block;
  font-size: 22px;
  color: var(--accent, #ff8fb3);
}
.stat span {
  font-size: 12px;
  color: var(--text-sub, #9a7a88);
}

/* 大图查看器 */
.viewer-mask {
  position: fixed;
  inset: 0;
  background: rgba(20, 12, 16, 0.82);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}
.viewer {
  position: relative;
  max-width: 94vw;
  max-height: 92vh;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.vimg {
  position: relative;
  max-width: 94vw;
  max-height: 78vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.vimg img {
  max-width: 94vw;
  max-height: 78vh;
  object-fit: contain;
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
}
.vloading {
  position: absolute;
  color: #fff;
  font-size: 13px;
  background: rgba(0, 0, 0, 0.4);
  padding: 6px 12px;
  border-radius: 8px;
}
.vswap {
  position: absolute;
  top: 10px;
  left: 10px;
  border: none;
  background: rgba(255, 255, 255, 0.92);
  color: #333;
  padding: 6px 12px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
}
.vorig {
  position: absolute;
  left: 10px;
  bottom: 10px;
  border: none;
  background: rgba(255, 255, 255, 0.92);
  color: #333;
  padding: 6px 12px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
}
.vclose {
  position: absolute;
  top: -38px;
  right: 0;
  border: none;
  background: rgba(255, 255, 255, 0.9);
  width: 32px;
  height: 32px;
  border-radius: 50%;
  cursor: pointer;
  font-size: 16px;
}
.vmeta {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-top: 10px;
  color: #fff;
  font-size: 13px;
  flex-wrap: wrap;
  justify-content: center;
}
.vmeta button {
  border: 1px solid rgba(255, 255, 255, 0.5);
  background: transparent;
  color: #fff;
  padding: 4px 12px;
  border-radius: 999px;
  cursor: pointer;
}
.vmeta button.like.on { color: #ff5b8a; border-color: #ff5b8a; }
.vmeta button.fav.on { color: #ffb000; border-color: #ffb000; }

/* 404 */
.share-404 {
  width: 100%;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #fff0f6, #ffe9f2);
}
.s404-card {
  text-align: center;
  background: #fff;
  border-radius: 18px;
  padding: 44px 36px;
  box-shadow: 0 10px 36px rgba(255, 143, 179, 0.2);
  max-width: 90vw;
}
.s404-emoji {
  font-size: 46px;
}
.s404-title {
  font-size: 22px;
  font-weight: 800;
  margin: 12px 0 8px;
  color: #e86f9c;
}
.s404-sub {
  font-size: 14px;
  color: #9a7a88;
  line-height: 1.7;
}
.toast {
  position: fixed;
  bottom: 28px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(40, 28, 34, 0.9);
  color: #fff;
  padding: 9px 18px;
  border-radius: 999px;
  font-size: 13px;
  z-index: 10000;
}
</style>
