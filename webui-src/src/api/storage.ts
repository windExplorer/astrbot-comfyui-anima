// 安全 localStorage 封装：AstrBot 插件页运行在 sandbox iframe 中（缺少
// allow-same-origin），直接访问 localStorage 会抛 SecurityError，导致整个 JS
// bundle 在初始化阶段崩溃（表现为“接口拿到数据但页面不渲染”）。这里统一兜底：
// 访问失败则降级到内存 Map，绝不向上抛错。

const memoryFallback = new Map<string, string>();
let storageAvailable: boolean | null = null;

function probe(): boolean {
  if (storageAvailable !== null) return storageAvailable;
  try {
    const k = "__anima_probe__";
    window.localStorage.setItem(k, "1");
    window.localStorage.removeItem(k);
    storageAvailable = true;
  } catch {
    storageAvailable = false;
  }
  return storageAvailable;
}

export function lsGet(key: string): string | null {
  if (probe()) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      /* 落到内存兜底 */
    }
  }
  return memoryFallback.has(key) ? (memoryFallback.get(key) as string) : null;
}

export function lsSet(key: string, value: string): void {
  if (probe()) {
    try {
      window.localStorage.setItem(key, value);
      return;
    } catch {
      /* 落到内存兜底 */
    }
  }
  memoryFallback.set(key, value);
}
