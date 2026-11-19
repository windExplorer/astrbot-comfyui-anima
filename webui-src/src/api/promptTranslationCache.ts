/**
 * 提示词中文译文的本地缓存（v7.10.7）。
 *
 * 两处共用同一份缓存：
 *  - ResultPanel「查看译文」按需调用 LLM 翻译后的结果；
 *  - PromptPanel「🎲 想画面」由 LLM 在同一次输出里顺带产出的译文。
 * 这样同一段提示词无论从哪条路径看过译文，之后都不用再翻译一遍。
 *
 * key 用文本 hash（而不是整段提示词当 key）——详细模式提示词可达上千字，
 * 直接当 localStorage key 既浪费又容易撞上限；同时存长度做二次校验，
 * 避免极端 hash 碰撞时误用别的提示词的译文。
 */
import { lsGet, lsSet } from "@/api/storage";

const CACHE_KEY = "anima_prompt_translation_cache_v1";
const MAX_ENTRIES = 200;

type Entry = { zh: string; len: number; ts: number };
type CacheMap = Record<string, Entry>;

let mem: CacheMap | null = null;

function load(): CacheMap {
  if (mem) return mem;
  try {
    const raw = lsGet(CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    mem = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as CacheMap) : {};
  } catch {
    mem = {};
  }
  return mem as CacheMap;
}

function flush(): void {
  try {
    lsSet(CACHE_KEY, JSON.stringify(mem || {}));
  } catch {
    /* storage.ts 内部已有内存兜底，绝不向上抛 */
  }
}

function hashText(text: string): string {
  let h = 5381;
  const s = String(text || "").trim();
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h + s.charCodeAt(i)) | 0; // djb2
  }
  return (h >>> 0).toString(36);
}

export function getCachedTranslation(text: string): string {
  const s = String(text || "").trim();
  if (!s) return "";
  const e = load()[hashText(s)];
  if (e && typeof e.zh === "string" && e.len === s.length) return e.zh;
  return "";
}

export function setCachedTranslation(text: string, zh: string): void {
  const s = String(text || "").trim();
  const clean = String(zh || "").trim();
  if (!s || !clean) return;
  const map = load();
  map[hashText(s)] = { zh: clean, len: s.length, ts: Date.now() };
  const keys = Object.keys(map);
  if (keys.length > MAX_ENTRIES) {
    keys.sort((a, b) => (map[a].ts || 0) - (map[b].ts || 0));
    for (const k of keys.slice(0, keys.length - MAX_ENTRIES)) delete map[k];
  }
  flush();
}
