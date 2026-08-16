<template>
  <n-config-provider :theme="isDark ? darkTheme : null" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-dialog-provider>
        <n-loading-bar-provider>
          <n-layout has-sider class="app-shell" :style="{ height: '100vh' }">
            <n-layout-sider
              bordered
              collapse-mode="width"
              :collapsed-width="0"
              :width="200"
              :collapsed="siderCollapsed"
              show-trigger="bar"
              @collapse="siderCollapsed = true"
              @expand="siderCollapsed = false"
              class="app-sider"
            >
              <div class="brand">
                <div class="brand-logo">✦</div>
                <div v-if="!siderCollapsed" class="brand-text">
                  <div class="brand-title">Anima 控制台</div>
                  <div class="brand-sub">ComfyUI / Control</div>
                </div>
              </div>
              <n-menu
                :value="activeKey"
                :options="menuOptions"
                :collapsed="siderCollapsed"
                :collapsed-width="0"
                @update:value="onMenuSelect"
              />
            </n-layout-sider>

            <n-layout>
              <n-layout-header bordered class="app-header">
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
              </n-layout-header>

              <n-layout-content class="app-content" :native-scrollbar="false">
                <router-view />
              </n-layout-content>
            </n-layout>
          </n-layout>
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
  useMessage,
  type MenuOption,
  type GlobalThemeOverrides,
} from "naive-ui";
import { useTheme } from "@/composables/useTheme";

const { isDark, toggleDark } = useTheme();
const route = useRoute();
const router = useRouter();
const message = useMessage();
const siderCollapsed = ref(false);

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: "#7c4dff",
    primaryColorHover: "#9a73ff",
    primaryColorPressed: "#6236d6",
    primaryColorSuppl: "#7c4dff",
    borderRadius: "8px",
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
  message.success("已请求刷新数据");
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
  return () => h("span", { style: "font-size:16px" }, text);
}

const activeKey = computed(() => route.name as string);
const currentTitle = computed(() => String(route.meta.title || "Anima 控制台"));

function onMenuSelect(key: string) {
  router.push({ name: key });
}
</script>

<style scoped>
.app-shell {
  background: var(--bg-body);
}
.app-sider {
  background: var(--bg-panel);
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 16px 8px;
}
.brand-logo {
  font-size: 22px;
  color: #7c4dff;
}
.brand-title {
  font-weight: 700;
  font-size: 15px;
  line-height: 1.2;
}
.brand-sub {
  font-size: 11px;
  opacity: 0.55;
  letter-spacing: 0.05em;
}
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  height: 52px;
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
.app-content {
  padding: 20px;
}
</style>

<style>
:root {
  --bg-body: #f6f5fa;
  --bg-panel: #ffffff;
  --text-main: #1f1f2e;
  --text-sub: #6b6b80;
  --border-color: #e8e7f0;
  --accent: #7c4dff;
}
html.dark {
  --bg-body: #14141d;
  --bg-panel: #1c1c27;
  --text-main: #e6e6f0;
  --text-sub: #9a9ab0;
  --border-color: #2c2c3a;
  --accent: #9a73ff;
}
body {
  margin: 0;
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", Roboto, sans-serif;
  color: var(--text-main);
  background: var(--bg-body);
}
* { box-sizing: border-box; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: rgba(124, 77, 255, 0.3); border-radius: 4px; }
::-webkit-scrollbar-track { background: transparent; }
</style>
