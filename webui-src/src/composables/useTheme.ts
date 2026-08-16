import { ref, watch } from "vue";

/**
 * 深色模式：优先读取 AstrBot 桥接注入的初始 context（window.AstrBotPluginPage 上可能带
 * context / getContext），否则回退到系统 prefers-color-scheme，并支持手动切换。
 */
const isDark = ref<boolean>(detectInitialDark());

// 同步 <html> 的 dark class：驱动 :root / html.dark 下的 CSS 变量深色主题。
// 注意：Naive UI 组件深色由 n-config-provider:theme 驱动，而页面背景、文字、
// 及依赖 var(--*) 的自定义样式（面板、图表等）靠 html.dark 生效，二者缺一不可。
function applyDark(dark: boolean) {
  try {
    document.documentElement.classList.toggle("dark", dark);
  } catch (e) {
    // ignore
  }
}

// 初始应用一次，保证刷新/首屏即正确；后续响应式同步切换。
applyDark(isDark.value);
watch(isDark, (v) => applyDark(v));

function detectInitialDark(): boolean {
  try {
    const w = window as any;
    const ctx = (w.AstrBotPluginPage && w.AstrBotPluginPage.context) ||
      (w.parent && w.parent.AstrBotPluginPage && w.parent.AstrBotPluginPage.context);
    if (ctx && typeof ctx === "object" && typeof ctx.isDark === "boolean") {
      return ctx.isDark;
    }
    if (ctx && typeof ctx.isDarkMode === "boolean") {
      return ctx.isDarkMode;
    }
  } catch (e) {
    // ignore
  }
  try {
    if (typeof window.matchMedia === "function") {
      return window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
  } catch (e) {
    // ignore
  }
  return false;
}

export function useTheme() {
  function toggleDark() {
    isDark.value = !isDark.value;
  }
  return { isDark, toggleDark };
}
