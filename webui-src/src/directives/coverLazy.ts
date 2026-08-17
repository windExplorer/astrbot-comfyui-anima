/**
 * v-cover-lazy：封面图懒加载指令（LoRA / 工作流封面）。
 *
 * 解决的问题：
 * 1) 旧实现一次性 `forEach` 并发发起所有封面请求，而 AstrBot 的 postMessage
 *    桥接是串行/有限并发处理。请求一次涌入会把桥接队列堵死，表现为"加载几张
 *    就停住，必须刷新页面才能恢复"。
 * 2) 所有封面（含视口外的）一次性加载，量大且慢。
 *
 * 方案：
 * - 视口懒加载：用 IntersectionObserver，封面进入视口附近才发起请求，大幅减少
 *   初始请求量。
 * - 受限并发：全局单例队列，同一时刻最多并发 CONCURRENCY 个请求，一个完成/失败
 *   才拉下一个，杜绝打爆桥接。
 * - 全局缓存：同一封面名只请求一次，跨视图、跨刷新复用。
 * - 失败兜底：请求失败/超时置空且不重试，不阻塞其他封面；下次重新挂载可再触发。
 */
import type { Directive } from "vue";
import { apiGet } from "@/api/bridge";

/** 同一时刻最多并发拉取封面的数量 */
const CONCURRENCY = 3;

/** 封面名 -> data URL（全局缓存） */
const cache = new Map<string, string>();
/** 进行中的请求（避免同名并发重复） */
const inflight = new Map<string, Promise<string>>();
/** 封面名 -> 引用该封面的 DOM 元素集合 */
const elements = new Map<string, Set<HTMLImageElement>>();
/** 受限并发队列 */
const queue: string[] = [];
let active = 0;

function pump() {
  while (active < CONCURRENCY && queue.length) {
    const name = queue.shift()!;
    active++;
    fetchCover(name).finally(() => {
      active--;
      pump();
    });
  }
}

async function fetchCover(name: string): Promise<string> {
  if (cache.has(name)) return cache.get(name)!;
  let p = inflight.get(name);
  if (!p) {
    p = apiGet("lora/image", { name })
      .then((d) => {
        const url = (d && (d.url || "")) || "";
        if (url) cache.set(name, url);
        return url;
      })
      .catch(() => "");
    inflight.set(name, p);
  }
  const url = await p;
  inflight.delete(name);
  // 通知所有引用该封面的元素更新
  const els = elements.get(name);
  if (els) {
    els.forEach((el) => applySrc(el, url));
  }
  return url;
}

function applySrc(el: HTMLImageElement, url: string) {
  if (!url) {
    el.removeAttribute("src");
    el.style.visibility = "hidden";
    return;
  }
  el.src = url;
  el.style.visibility = "visible";
}

function register(el: HTMLImageElement, name: string) {
  let els = elements.get(name);
  if (!els) {
    els = new Set();
    elements.set(name, els);
  }
  els.add(el);
}

function enqueue(name: string, el: HTMLImageElement) {
  if (!name) {
    el.removeAttribute("src");
    return;
  }
  register(el, name);
  if (cache.has(name)) {
    applySrc(el, cache.get(name)!);
    return;
  }
  // 已在队列/加载中则不重复排队（会由 fetchCover 完成回调统一刷新元素）
  if (queue.includes(name) || inflight.has(name)) return;
  queue.push(name);
  pump();
}

let observer: IntersectionObserver | null = null;

function getObserver(): IntersectionObserver {
  if (!observer) {
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const el = entry.target as HTMLImageElement;
            const name = (el as any).__coverName as string;
            if (name) enqueue(name, el);
            observer!.unobserve(el);
          }
        }
      },
      { rootMargin: "200px" }
    );
  }
  return observer;
}

export const vCoverLazy: Directive<HTMLImageElement, string> = {
  mounted(el, binding) {
    el.setAttribute("loading", "lazy");
    el.style.visibility = "hidden"; // 加载前隐藏，避免露出破图/撑版
    (el as any).__coverName = binding.value;
    getObserver().observe(el);
  },
  updated(el, binding) {
    const name = binding.value;
    (el as any).__coverName = name;
    if (!name) {
      el.removeAttribute("src");
      return;
    }
    if (cache.has(name)) {
      applySrc(el, cache.get(name)!);
    } else {
      el.style.visibility = "hidden";
      getObserver().observe(el);
    }
  },
  unmounted(el) {
    for (const [name, els] of elements) {
      if (els.delete(el) && els.size === 0) elements.delete(name);
    }
  },
};
