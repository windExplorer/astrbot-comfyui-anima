import { ref } from "vue";

/**
 * 深色模式：优先读取 AstrBot 桥接注入的初始 context（window.AstrBotPluginPage 上可能带
 * context / getContext），否则回退到系统 prefers-color-scheme，并支持手动切换。
 */
const isDark = ref<boolean>(detectInitialDark());

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
