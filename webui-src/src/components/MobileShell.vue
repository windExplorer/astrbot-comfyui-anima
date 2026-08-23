<template>
  <div class="mobile-shell">
    <!-- 顶部轻量栏：logo + 标题 + 菜单开关 -->
    <header class="ms-topbar">
      <div class="ms-logo-wrap">
        <img :src="LOGO_DATA_URL" alt="logo" class="ms-logo" />
      </div>
      <span class="ms-title">萌绘控制台</span>
      <button class="ms-menu-btn" @click="navOpen = true" aria-label="菜单">
        <span></span><span></span><span></span>
      </button>
    </header>

    <!-- 内容区：正常文档流滚动 -->
    <main class="ms-content">
      <slot />
    </main>

    <!-- 右下角可拖动悬浮按钮：打开筛选/操作面板 -->
    <FloatingActionButton @click="panelOpen = true" />

    <!-- 回到顶部 / 去底部：移动端页面级滚动（整个文档），不传 scrollTarget -->
    <ScrollJumpButton />

    <!-- 遮罩（菜单/面板共用；打开时锁定背景滚动） -->
    <div
      v-show="navOpen || panelOpen"
      class="ms-mask"
      @click="navOpen = false; panelOpen = false"
    ></div>

    <!-- 左侧抽屉导航：自绘常驻 DOM（v-show 控制显隐）。
         不用 naive n-drawer（其内部 VLazyTeleport 在关闭时不渲染内容，
         会导致 #mobile-filter-slot 等 Teleport 目标从无到有，触发
         Teleport fallback 原位与组件复用/卸载竞态崩溃）。 -->
    <aside class="ms-nav-panel" :class="{ open: navOpen }">
      <div class="ms-nav-panel-title">导航</div>
      <div class="ms-nav">
        <RouterLink
          v-for="item in NAV_ITEMS"
          :key="item.path"
          :to="item.path"
          class="ms-nav-item"
          :class="{ active: route.path === item.path }"
          @click="navOpen = false"
        >
          <span class="ms-nav-ico">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </RouterLink>
      </div>
    </aside>

    <!-- 底部抽屉面板：承载各 View 的筛选/操作栏（Teleport 目标）。
         #mobile-filter-slot 常驻 DOM，View 的 Teleport 首帧即可解析。 -->
    <div class="ms-panel" :class="{ open: panelOpen }">
      <div class="ms-panel-title">筛选 / 操作</div>
      <div id="mobile-filter-slot" class="ms-filter-slot"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from "vue";
import { useRoute, RouterLink } from "vue-router";
import FloatingActionButton from "@/components/FloatingActionButton.vue";
import ScrollJumpButton from "@/components/ScrollJumpButton.vue";
import { NAV_ITEMS } from "@/router/nav";
import { LOGO_DATA_URL } from "@/assets/logo";

const route = useRoute();

const navOpen = ref(false);
const panelOpen = ref(false);

// 面板打开时禁止背景滚动（模拟 native drawer 的 blockScroll）
watch(
  () => navOpen.value || panelOpen.value,
  (v) => {
    document.documentElement.style.overflow = v ? "hidden" : "";
  }
);

// 组件卸载（路由切到登录页/切 PC 分支）时清理滚动锁定，避免泄漏
onBeforeUnmount(() => {
  document.documentElement.style.overflow = "";
});
</script>

<style scoped>
.mobile-shell {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
  background: #f7f8fa;
}
.ms-topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 52px;
  padding: 0 14px;
  background: linear-gradient(135deg, #ff7eb3, #ff758c);
  color: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}
.ms-logo-wrap {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  flex: 0 0 auto;
}
.ms-logo {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.ms-title {
  font-weight: 700;
  font-size: 16px;
  flex: 1;
}
.ms-menu-btn {
  width: 36px;
  height: 36px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 5px;
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 6px;
}
.ms-menu-btn span {
  display: block;
  height: 2px;
  background: #fff;
  border-radius: 2px;
}
.ms-content {
  flex: 1;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  padding: 12px;
}

/* ---------- 遮罩 ---------- */
.ms-mask {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(0, 0, 0, 0.45);
}

/* ---------- 左侧导航抽屉 ---------- */
.ms-nav-panel {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  z-index: 1000;
  width: 240px;
  max-width: 80vw;
  background: #fff;
  box-shadow: 2px 0 12px rgba(0, 0, 0, 0.15);
  transform: translateX(-100%);
  transition: transform 0.25s ease;
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}
.ms-nav-panel.open {
  transform: translateX(0);
}
.ms-nav-panel-title {
  font-size: 15px;
  font-weight: 700;
  color: #333;
  padding: 4px 10px 12px;
  border-bottom: 1px solid #f2e3ea;
  margin-bottom: 8px;
}
.ms-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ms-nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 10px;
  font-size: 15px;
  color: #333;
  cursor: pointer;
  text-decoration: none;
}
.ms-nav-item.active {
  background: #fff0f5;
  color: #ff5c8a;
  font-weight: 600;
}
.ms-nav-ico {
  font-size: 18px;
}

/* ---------- 底部筛选/操作面板 ---------- */
.ms-panel {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1000;
  max-height: 60vh;
  background: #fff;
  border-radius: 16px 16px 0 0;
  box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.15);
  transform: translateY(100%);
  transition: transform 0.25s ease;
  display: flex;
  flex-direction: column;
  padding: 14px 16px calc(14px + env(safe-area-inset-bottom));
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}
.ms-panel.open {
  transform: translateY(0);
}
.ms-panel-title {
  font-size: 15px;
  font-weight: 700;
  color: #333;
  text-align: center;
  padding-bottom: 10px;
  margin-bottom: 8px;
  border-bottom: 1px solid #f2e3ea;
  flex: 0 0 auto;
}
.ms-filter-slot {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
</style>