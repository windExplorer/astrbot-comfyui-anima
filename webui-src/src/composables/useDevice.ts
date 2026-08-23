import { ref, onMounted, onUnmounted } from "vue";

/** 根据 UA 判断是否为移动端设备（非媒体查询，纯 UA 渲染分支）。 */
function detectMobile(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent || "";
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini|Mobile/i.test(ua);
}

const isMobile = ref(detectMobile());

// 响应式兜底：个别机型 UA 不含 Mobile 但宽度很小，resize 时也修正
function onResize() {
  if (window.innerWidth <= 768 && !isMobile.value) {
    // 仅当 UA 判断失败时兜底，避免桌面端误判
    if (/Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "")) {
      isMobile.value = true;
    }
  }
}

onMounted(() => window.addEventListener("resize", onResize));
onUnmounted(() => window.removeEventListener("resize", onResize));

export function useDevice() {
  return { isMobile };
}
