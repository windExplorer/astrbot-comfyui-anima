<template>
  <n-config-provider :theme="isDark ? darkTheme : null" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-dialog-provider>
        <n-loading-bar-provider>
          <div class="app-shell">
            <div class="app-sider" :class="{ collapsed: siderCollapsed }">
              <div class="brand">
                <div class="brand-logo">✦</div>
                <div v-if="!siderCollapsed" class="brand-text">
                  <div class="brand-title">萌绘控制台</div>
                  <div class="brand-sub">ComfyUI / 萌图工厂</div>
                </div>
              </div>
              <n-menu
                :value="activeKey"
                :options="menuOptions"
                :collapsed="siderCollapsed"
                :collapsed-width="0"
                :indent="18"
                @update:value="onMenuSelect"
              />
              <div class="sider-trigger" @click="siderCollapsed = !siderCollapsed">«</div>
            </div>

            <div class="app-main">
              <div class="app-header">
                <div class="header-left">
                  <span class="header-title">{{ currentTitle }}</span>
                </div>
                <div class="header-right">
                  <n-switch v-model:value="isDark" size="small" @update:value="toggleDark">
                    <template #checked-icon>🌙</template>
                    <template #unchecked-icon>☀️</template>
                  </n-switch>
                  <n-button size="small" quaternary @click="refreshAll">
                    <template #icon><n-icon><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg></n-icon></template>
                    刷新数据
                  </n-button>
                </div>
              </div>

              <div class="app-content">
                <router-view />
              </div>
            </div>
          </div>
        </n-loading-bar-provider>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, h, ref } from "vue";
import { useRoute, useRouter, RouterLink } from "vue-router";
import {
  darkTheme,
  zhCN,
  dateZhCN,
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  NLoadingBarProvider,
  NLayout,
  NLayoutSider,
  NLayoutHeader,
  NLayoutContent,
  NMenu,
  NSwitch,
  NButton,
  NIcon,
  type MenuOption,
  type GlobalThemeOverrides,
} from "naive-ui";
import { useTheme } from "@/composables/useTheme";

// 注意：App.vue 自身是 <n-message-provider>/<n-dialog-provider> 的祖先组件，
// 不能在 App 的 setup 里调用 useMessage()/useDialog()（provider 尚未挂载会抛错）。
// 消息提示只能在各 View（provider 的后代）里用。
const { isDark, toggleDark } = useTheme();
const route = useRoute();
const router = useRouter();
const siderCollapsed = ref(false);

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: "#ff8fb3",
    primaryColorHover: "#ffa8c8",
    primaryColorPressed: "#e86f9c",
    primaryColorSuppl: "#ff8fb3",
    borderRadius: "10px",
    borderRadiusSmall: "8px",
    fontFamily: '-apple-system, "PingFang SC", "Microsoft YaHei", "HarmonyOS Sans SC", "Segoe UI", Roboto, sans-serif',
  },
};

interface AppEvent { detail?: unknown }
window.addEventListener("anima:refresh" as any, () => {
  refreshAll();
});

function emitRefresh() {
  window.dispatchEvent(new CustomEvent("anima:refresh"));
}

function refreshAll() {
  emitRefresh();
}

const menuOptions: MenuOption[] = [
  { label: () => h(RouterLink, { to: "/config" }, { default: () => "配置" }), key: "config", icon: () => iconSvg("⚙️") },
  { label: () => h(RouterLink, { to: "/logs" }, { default: () => "日志" }), key: "logs", icon: () => iconSvg("📋") },
  { label: () => h(RouterLink, { to: "/stats" }, { default: () => "统计" }), key: "stats", icon: () => iconSvg("📊") },
  { label: () => h(RouterLink, { to: "/workflows" }, { default: () => "工作流" }), key: "workflows", icon: () => iconSvg("🗂️") },
  { label: () => h(RouterLink, { to: "/loras" }, { default: () => "LoRA" }), key: "loras", icon: () => iconSvg("🎨") },
  { label: () => h(RouterLink, { to: "/gallery" }, { default: () => "图库" }), key: "gallery", icon: () => iconSvg("🖼️") },
  { label: () => h(RouterLink, { to: "/quota" }, { default: () => "限额" }), key: "quota", icon: () => iconSvg("🚦") },
  { label: () => h(RouterLink, { to: "/token" }, { default: () => "Token" }), key: "token", icon: () => iconSvg("🔑") },
];

function iconSvg(text: string) {
  return h("span", { style: "font-size:16px" }, text);
}

const activeKey = computed(() => route.name as string);
const currentTitle = computed(() => String(route.meta.title || "Anima 控制台"));

function onMenuSelect(key: string) {
  router.push({ name: key });
}
</script>

<style scoped>
/* 纯 flex 布局外壳：100vh 撑满，滚动完全由各页面内部管理 */
.app-shell {
  display: flex;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: var(--bg-body);
}
.app-sider {
  width: 200px;
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  background: var(--bg-panel);
  border-right: 1px solid var(--border-color);
  transition: width 0.2s;
  overflow: hidden;
}
.app-sider.collapsed {
  width: 48px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 16px 8px;
  white-space: nowrap;
  overflow: hidden;
  flex: 0 0 auto;
}
.brand-logo {
  font-size: 20px;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: linear-gradient(135deg, #ffb3d1, #ff8fb3);
  color: #fff;
  box-shadow: 0 3px 8px rgba(255, 143, 179, 0.35);
}
.brand-title {
  font-weight: 800;
  font-size: 15px;
  line-height: 1.2;
  background: linear-gradient(90deg, #ff8fb3, #ffb3d1);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.brand-sub {
  font-size: 11px;
  opacity: 0.55;
  letter-spacing: 0.05em;
}
.app-sider :deep(.n-menu) {
  flex: 1 1 auto;
  overflow: auto;
  min-height: 0;
}
.sider-trigger {
  flex: 0 0 auto;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--text-sub);
  border-top: 1px solid var(--border-color);
  user-select: none;
}
.sider-trigger:hover { color: var(--accent); }
.app-main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.app-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  height: 52px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-panel);
}
.header-title {
  font-size: 16px;
  font-weight: 600;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
/* 内容区：flex 填满 header 以下全部空间，不滚动；滚动由各页面内部管理，
   从而保证顶部标题（header）与底部有分页器的页面底部固定可见。 */
.app-content {
  flex: 1 1 auto;
  min-height: 0;
  padding: 16px 20px;
  overflow: hidden;
}
.app-content > * {
  height: 100%;
}
</style>

<style>
:root {
  --bg-body: #fff6f9;
  --bg-panel: #ffffff;
  --text-main: #3a2a33;
  --text-sub: #9a7a88;
  --border-color: #ffe3ec;
  --accent: #ff8fb3;
}
html.dark {
  --bg-body: #1a1418;
  --bg-panel: #241b21;
  --text-main: #f2e3ea;
  --text-sub: #b3909f;
  --border-color: #3a2a33;
  --accent: #ff9dc4;
}
body {
  margin: 0;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "HarmonyOS Sans SC", "Segoe UI", Roboto, sans-serif;
  color: var(--text-main);
  background: var(--bg-body);
}
* { box-sizing: border-box; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: rgba(255, 143, 179, 0.35); border-radius: 4px; }
::-webkit-scrollbar-track { background: transparent; }
</style>
