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

    <!-- 独立服务访问口令弹窗：standalone 模式需要 token 校验时弹出 -->
    <n-modal
      v-model:show="authVisible"
      :mask-closable="false"
      :close-on-esc="false"
      :show-close="false"
      preset="card"
      style="width: 360px; max-width: 92vw"
      :title="'访问控制台'"
    >
      <div class="auth-panel">
        <p class="auth-tip">该控制台设置了访问口令，请输入后进入：</p>
        <n-input
          v-model:value="authInput"
          type="password"
          size="large"
          placeholder="请输入访问口令"
          show-password-on="click"
          @keyup.enter="submitAuth"
        />
        <div class="auth-actions">
          <n-button type="primary" size="large" block :loading="authLoading" @click="submitAuth">确认进入</n-button>
        </div>
      </div>
    </n-modal>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from "vue";
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
  NModal,
  NInput,
  type MenuOption,
  type GlobalThemeOverrides,
} from "naive-ui";
import { useTheme, initThemeBridge } from "@/composables/useTheme";
import { standaloneAuthState, setStandaloneToken, isStandaloneMode, apiGet } from "@/api/bridge";

// 注意：App.vue 自身是 <n-message-provider>/<n-dialog-provider> 的祖先组件，
// 不能在 App 的 setup 里调用 useMessage()/useDialog()（provider 尚未挂载会抛错）。
// 消息提示只能在各 View（provider 的后代）里用。
const { isDark, toggleDark } = useTheme();
const route = useRoute();
const router = useRouter();
const siderCollapsed = ref(false);

// 独立服务访问口令弹窗状态
const authVisible = ref(false);
const authInput = ref("");
const authLoading = ref(false);
let authUnsub: (() => void) | null = null;

async function submitAuth() {
  const token = (authInput.value || "").trim();
  if (!token) return;
  authLoading.value = true;
  try {
    setStandaloneToken(token);
    // 用 ping 验证口令是否正确
    await apiGet("ping", {}, { timeout: 5000 });
    authVisible.value = false;
    authInput.value = "";
    // 校验成功后重载页面，让所有请求带上新 token
    window.location.reload();
  } catch (e: any) {
    if (e && e.authRequired) {
      // token 仍不对，继续停留弹窗
      authInput.value = "";
    } else {
      authVisible.value = false;
      window.location.reload();
    }
  } finally {
    authLoading.value = false;
  }
}

// 与 AstrBot 主题联动：监听 html[data-theme] 与 bridge.onContext
onMounted(() => {
  initThemeBridge();
  // 独立服务模式：探测是否需要认证，订阅认证状态触发弹窗
  if (isStandaloneMode()) {
    authUnsub = standaloneAuthState.on((needed) => {
      authVisible.value = needed;
    });
    // 主动探测一次：后端无 token 配置则 ping 成功，不弹窗；有 token 则 401 弹窗
    apiGet("ping", {}, { timeout: 5000 }).catch((e) => {
      if (!(e && e.authRequired)) standaloneAuthState.set(false);
    });
  }
});

onUnmounted(() => {
  if (authUnsub) {
    authUnsub();
    authUnsub = null;
  }
});

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

/* ===================== 移动端适配（≤768px 时侧边栏转顶部横向导航） ===================== */
@media (max-width: 768px) {
  .app-shell {
    flex-direction: column;
    height: 100vh;
    height: 100dvh; /* 动态视口高度，规避移动端地址栏高度抖动 */
  }
  .app-sider {
    width: 100% !important;
    flex: 0 0 auto;
    flex-direction: row;
    align-items: center;
    border-right: none;
    border-bottom: 1px solid var(--border-color);
    padding: 0 8px;
    gap: 4px;
    overflow-x: auto;
    overflow-y: hidden;
    -webkit-overflow-scrolling: touch;
  }
  .app-sider.collapsed { width: 100% !important; }
  .brand {
    flex: 0 0 auto;
    padding: 10px 8px 10px 4px;
  }
  .brand-logo { width: 28px; height: 28px; font-size: 16px; }
  .brand-text { display: none; } /* 移动端只留 logo，节省横向空间 */
  .app-sider :deep(.n-menu) {
    flex: 1 1 auto;
    display: flex;
    flex-direction: row;
    align-items: center;
    overflow-x: auto;
    overflow-y: hidden;
    white-space: nowrap;
    min-height: 0;
  }
  /* Naive Menu 在横向模式下需要覆盖默认竖向内边距/宽度 */
  .app-sider :deep(.n-menu-item) { flex: 0 0 auto; }
  .app-sider :deep(.n-submenu) { flex: 0 0 auto; }
  .sider-trigger {
    display: none !important; /* 移动端横向导航不需要折叠按钮 */
  }
  .app-header {
    padding: 0 12px;
    height: 46px;
  }
  .header-title { font-size: 14px; }
  .header-right { gap: 8px; }
  .header-right :deep(.n-button) { font-size: 12px; }
  .app-content {
    padding: 10px 10px;
    overflow: hidden; /* 移动端仍交由各视图内部 flex + overflow:auto 管理滚动，避免破坏 height:100% 链导致表格/图库高度塌缩 */
  }
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
/* 独立服务访问口令弹窗 */
.auth-panel {
  padding: 4px 0 0;
}
.auth-tip {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--text-sub);
  line-height: 1.5;
}
.auth-actions {
  margin-top: 16px;
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
