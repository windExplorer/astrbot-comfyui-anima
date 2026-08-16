import { onBeforeUnmount, onMounted } from "vue";

/**
 * 订阅全局「刷新数据」事件（App 顶栏的刷新按钮触发）。
 * 各视图在 onMounted 时调用 refresh 首次加载，并订阅全局刷新事件。
 */
export function useRefresh(fn: () => void) {
  onMounted(() => {
    window.addEventListener("anima:refresh", fn);
  });
  onBeforeUnmount(() => {
    window.removeEventListener("anima:refresh", fn);
  });
}
