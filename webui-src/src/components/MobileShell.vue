<template>
  <div class="mobile-shell">
    <!-- 顶部轻量栏：logo + 标题 + 菜单开关 -->
    <header class="ms-topbar">
      <div class="ms-logo">✦</div>
      <span class="ms-title">Anima 控制台</span>
      <button class="ms-menu-btn" @click="navOpen = true" aria-label="菜单">
        <span></span><span></span><span></span>
      </button>
    </header>

    <!-- 内容区：正常文档流滚动，不再 100vh 死高度 -->
    <main class="ms-content">
      <slot />
    </main>

    <!-- 右下角可拖动悬浮按钮：打开筛选/操作面板 -->
    <FloatingActionButton @click="panelOpen = true" />

    <!-- 侧边抽屉导航 -->
    <n-drawer v-model:show="navOpen" placement="left" :width="240">
      <n-drawer-content title="导航" :native-scrollbar="false">
        <div class="ms-nav">
          <div
            v-for="item in navItems"
            :key="item.path"
            class="ms-nav-item"
            :class="{ active: route.path === item.path }"
            @click="go(item.path)"
          >
            <span class="ms-nav-ico">{{ item.icon }}</span>
            <span>{{ item.label }}</span>
          </div>
        </div>
      </n-drawer-content>
    </n-drawer>

    <!-- 底部抽屉面板：承载各 View 的筛选/操作栏（Teleport 目标） -->
    <n-drawer v-model:show="panelOpen" placement="bottom" :height="420">
      <n-drawer-content title="筛选 / 操作" :native-scrollbar="false">
        <div id="mobile-filter-slot" class="ms-filter-slot"></div>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NDrawer, NDrawerContent } from "naive-ui";
import FloatingActionButton from "@/components/FloatingActionButton.vue";

const route = useRoute();
const router = useRouter();

const navOpen = ref(false);
const panelOpen = ref(false);

const navItems = [
  { path: "/gallery", label: "图库", icon: "🖼" },
  { path: "/loras", label: "LoRA 库", icon: "🎨" },
  { path: "/workflows", label: "工作流", icon: "🧩" },
  { path: "/records", label: "出图记录", icon: "📜" },
  { path: "/monitor", label: "监控", icon: "📡" },
  { path: "/settings", label: "设置", icon: "⚙" },
];

function go(path: string) {
  navOpen.value = false;
  if (route.path !== path) router.push(path);
}
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
.ms-logo {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: #fff;
  object-fit: contain;
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
}
.ms-nav-item.active {
  background: #fff0f5;
  color: #ff5c8a;
  font-weight: 600;
}
.ms-nav-ico {
  font-size: 18px;
}
.ms-filter-slot {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
</style>
