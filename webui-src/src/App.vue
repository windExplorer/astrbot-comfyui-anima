<template>
  <n-config-provider :theme="isDark ? darkTheme : null" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-dialog-provider>
        <n-loading-bar-provider>
          <!-- 登录页：仅渲染路由内容（不含侧边栏/顶栏），避免登录页看到控制台内容 -->
          <div v-if="route.name === 'login'" class="app-login-outlet">
            <router-view />
          </div>
          <!-- 分享站：公开独立页面，不含控制台布局 -->
          <div v-else-if="route.meta && route.meta.public" class="app-share-outlet">
            <router-view />
          </div>
          <!-- 移动端：按 UA 渲染独立外壳（轻量顶部栏 + 抽屉导航 + 内容区 + 悬浮按钮） -->
          <MobileShell v-else-if="isMobile">
            <router-view />
          </MobileShell>
          <!-- PC 端：控制台布局（侧边栏 + 顶栏 + 内容区） -->
          <div v-else class="app-shell">
            <div class="app-sider" :class="{ collapsed: siderCollapsed }">
              <div class="brand">
                <img :src="LOGO_DATA_URL" alt="logo" class="brand-logo" />
                <div v-if="!siderCollapsed" class="brand-text">
                  <div class="brand-title">萌绘控制台</div>
                  <div class="brand-sub">ComfyUI / 萌绘</div>
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
                  <n-switch v-model:value="isDark" size="small">
                    <template #checked-icon>🌙</template>
                    <template #unchecked-icon>☀️</template>
                  </n-switch>
                  <n-button size="small" quaternary @click="refreshAll">
                    <template #icon><n-icon><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg></n-icon></template>
                    刷新数据
                  </n-button>
                  <n-tooltip trigger="hover">
                    <template #trigger>
                      <n-tag size="small" round :bordered="false" type="info">{{ PLUGIN_VERSION }}</n-tag>
                    </template>
                    插件版本号
                  </n-tooltip>
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
import { computed, h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter, RouterLink } from "vue-router";
import {
  darkTheme,
  zhCN,
  dateZhCN,
  NConfigProvider,
  NTooltip,
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
import { useTheme, initThemeBridge } from "@/composables/useTheme";
import { useDevice } from "@/composables/useDevice";
import MobileShell from "@/components/MobileShell.vue";
import { LOGO_DATA_URL } from "@/assets/logo";
import { PLUGIN_VERSION } from "@/version";

// 注意：App.vue 自身是 <n-message-provider>/<n-dialog-provider> 的祖先组件，
// 不能在 App 的 setup 里调用 useMessage()/useDialog()（provider 尚未挂载会抛错）。
// 消息提示只能在各 View（provider 的后代）里用。
const { isDark } = useTheme();
const { isMobile } = useDevice();
const route = useRoute();
const router = useRouter();
const siderCollapsed = ref(false);

// 品牌名（浏览器标题 / 控制台品牌标题共用，站点统一为「萌绘控制台」）
const APP_NAME = "萌绘控制台";

// 与 AstrBot 主题联动：监听 html[data-theme] 与 bridge.onContext。
// 独立模式认证已迁移到路由守卫（router/index.ts）与 LoginView，App 只负责控制台布局。
onMounted(() => {
  initThemeBridge();
});

// 网页标题：路由切换时同步（浏览器标签页标题 = 「页面名 - 萌绘控制台」）
watch(
  () => route.meta.title,
  (t) => {
    document.title = t ? `${t} - ${APP_NAME}` : APP_NAME;
  },
  { immediate: true }
);

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
  { label: () => h(RouterLink, { to: "/share-manage" }, { default: () => "分享管理" }), key: "share-manage", icon: () => iconSvg("🔗") },
];

function iconSvg(text: string) {
  return h("span", { style: "font-size:16px" }, text);
}

const activeKey = computed(() => route.name as string);
const currentTitle = computed(() => String(route.meta.title || APP_NAME));

function onMenuSelect(key: string) {
  router.push({ name: key });
}
</script>

<style scoped>
/* 纯 flex 布局外壳：100vh 撑满，滚动完全由各页面内部管理 */
.app-shell {
  display: flex;
  width: 100%;
  height: 100vh;
  height: 100dvh;
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
/* 移动端：侧边栏改为顶部横向导航条，主区全宽（见底部 @media） */
.app-sider .sider-trigger { display: flex; }
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
  object-fit: cover;
  flex: 0 0 auto;
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
/* 独立服务登录页（未认证时整页显示） */
.auth-login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: linear-gradient(135deg, #fff0f6 0%, #ffe9f2 50%, #ffe0eb 100%);
}
.auth-login-card {
  width: 380px;
  max-width: 92vw;
  background: var(--bg-panel, #fff);
  border-radius: 16px;
  padding: 40px 32px 32px;
  box-shadow: 0 8px 30px rgba(255, 143, 179, 0.18);
  display: flex;
  flex-direction: column;
  gap: 14px;
  text-align: center;
}
.auth-login-logo {
  width: 64px;
  height: 64px;
  margin: 0 auto;
  border-radius: 18px;
  background: linear-gradient(135deg, #ff8fb3, #ff6b9d);
  color: #fff;
  font-size: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 16px rgba(255, 107, 157, 0.35);
}
.auth-login-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-main, #3a2a33);
}
.auth-login-sub {
  font-size: 13px;
  color: var(--text-sub, #9a7a88);
  line-height: 1.5;
  margin-bottom: 6px;
}
.auth-login-error {
  font-size: 13px;
  color: #e74c3c;
  margin-top: 4px;
}
/* 兼容两种深色触发：AstrBot 维护的 [data-theme=dark] 与本地手动切换的 html.dark */
html[data-theme="dark"],
html.dark {
  --bg-body: #1a1418;
  --bg-panel: #241b21;
  --text-main: #f2e3ea;
  --text-sub: #b3909f;
  --border-color: #3a2a33;
  --accent: #ff9dc4;
}
html[data-theme="dark"] .auth-login-page,
html.dark .auth-login-page {
  background: linear-gradient(135deg, #241b21 0%, #1f171c 50%, #1a1418 100%);
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
/* 让 Naive UI 的 dialog/弹窗始终显示在最上层（高于大图查看器的 z-index:9999），
   避免大图内点「检测」等弹窗被大图覆盖。 */
.n-dialog-container,
.n-dialog-mask {
  z-index: 10001 !important;
}

/* 下拉/选择项文本过长时在移动端换行（默认 nowrap 会导致选项溢出或截断） */
.n-base-select-option__content,
.n-base-select-menu__option-wrapper .n-base-select-option__content {
  white-space: normal !important;
  word-break: break-word !important;
  overflow-wrap: anywhere !important;
  line-height: 1.4 !important;
}
/* 选择框内已选文本同样允许换行 */
.n-base-selection__input {
  white-space: normal !important;
  word-break: break-word !important;
  overflow-wrap: anywhere !important;
}

/* 弹窗宽度：n-modal 经 teleport 渲染到 <body>，组件 scoped 样式无法命中，
   因此统一放在全局样式里控制。桌面固定宽度，移动端限宽 92vw 防占满屏幕。 */
.lora-modal { width: 680px; max-width: 92vw; }
.lora-modal.narrow { width: 520px; }
.wf-modal { width: 720px; max-width: 92vw; }
@media (max-width: 768px) {
  .lora-modal,
  .lora-modal.narrow,
  .wf-modal { width: 92vw; }
}
</style>
