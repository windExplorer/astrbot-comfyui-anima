import { ref, watch } from "vue";

/**
 * 深色模式，与 AstrBot 主题联动。
 *
 * AstrBot 机制：插件页面的 `<html>` 由 Dashboard 维护 `data-theme="light|dark"` 属性，
 * 且 bridge 提供 `getContext()?.isDark` / `onContext(handler)`。
 * 因此：
 *  1) 初始主题：优先读 AstrBot context.isDark，其次读 `html[data-theme]`，再回退系统偏好。
 *  2) 跟随切换：监听 `html[data-theme]` 属性变化 + bridge.onContext，AstrBot 切主题时插件自动跟随。
 *  3) 手动切换：toggleDark 同步改 `html[data-theme]` 与 `dark` class（CSS 变量随之切换）。
 *     若 AstrBot 之后推送主题，onContext 会再次同步，以 AstrBot 为准。
 */
const isDark = ref<boolean>(detectInitialDark());

function getPage(): any {
  const w = window as any;
  if (w.AstrBotPluginPage) return w.AstrBotPluginPage;
  try {
    if (w.parent && w.parent !== w && w.parent.AstrBotPluginPage) return w.parent.AstrBotPluginPage;
  } catch (e) {
    // ignore
  }
  return null;
}

function ctxIsDark(page: any): boolean | null {
  if (!page) return null;
  try {
    const ctx = page.context || (typeof page.getContext === "function" ? page.getContext() : null);
    if (ctx && typeof ctx.isDark === "boolean") return ctx.isDark;
    if (ctx && typeof ctx.isDarkMode === "boolean") return ctx.isDarkMode;
  } catch (e) {
    // ignore
  }
  return null;
}

function detectInitialDark(): boolean {
  // 1) AstrBot context
  const fromCtx = ctxIsDark(getPage());
  if (fromCtx != null) return fromCtx;
  // 2) html data-theme（AstrBot 服务端预注入，避免首屏闪烁）
  try {
    const th = document.documentElement.getAttribute("data-theme");
    if (th === "dark") return true;
    if (th === "light") return false;
  } catch (e) {
    // ignore
  }
  // 3) 系统偏好
  try {
    if (typeof window.matchMedia === "function") {
      return window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
  } catch (e) {
    // ignore
  }
  return false;
}

// 同步 <html> 的 data-theme 属性与 dark class，驱动 CSS 变量（[data-theme=dark] / html.dark）
function applyDark(dark: boolean) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.setAttribute("data-theme", dark ? "dark" : "light");
  root.classList.toggle("dark", dark);
}

applyDark(isDark.value);
watch(isDark, (v) => applyDark(v));

// 由 AstrBot/系统驱动的主题回调：读 context 优先，回退 data-theme
let syncing = false;
function onExternalTheme() {
  if (syncing) return;
  syncing = true;
  try {
    const fromCtx = ctxIsDark(getPage());
    if (fromCtx != null) {
      isDark.value = fromCtx;
    } else {
      const th = document.documentElement.getAttribute("data-theme");
      if (th === "dark") isDark.value = true;
      else if (th === "light") isDark.value = false;
    }
  } finally {
    syncing = false;
  }
}

// 手动切换时，先更新 isDark（watch 会同步到 html），随后 MutationObserver/onContext 再次同步，
// 值一致不会产生副作用。
function toggleDark() {
  isDark.value = !isDark.value;
}

let bridgeInited = false;
let offContext: (() => void) | null = null;
let mo: MutationObserver | null = null;

/** 在 App 挂载后调用：建立与 AstrBot 主题的双向联动监听。 */
export function initThemeBridge() {
  if (bridgeInited) return;
  bridgeInited = true;
  // 方式一：bridge.onContext（AstrBot 主题/语言等上下文变化）
  const page = getPage();
  if (page && typeof page.onContext === "function") {
    try {
      offContext = page.onContext(onExternalTheme);
    } catch (e) {
      offContext = null;
    }
  }
  // 方式二：监听 html data-theme 属性（Dashboard 维护，最通用）
  try {
    mo = new MutationObserver(onExternalTheme);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  } catch (e) {
    mo = null;
  }
  // 立即同步一次
  onExternalTheme();
}

/** 组件卸载时调用，解除监听（一般无需手动，但保留清理入口）。 */
export function disposeThemeBridge() {
  if (offContext) { try { offContext(); } catch (e) { /* ignore */ } offContext = null; }
  if (mo) { mo.disconnect(); mo = null; }
  bridgeInited = false;
}

export function useTheme() {
  return { isDark, toggleDark };
}
