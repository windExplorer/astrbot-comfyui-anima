/**
 * AstrBot 插件 Page 桥接封装。
 * 移植自旧版 app.js 已验证可用的 bridge 逻辑：
 *   1) 既查 window.AstrBotPluginPage 也查 window.parent.AstrBotPluginPage（iframe 嵌入）
 *   2) 对同一 endpoint 尝试多种路径风格自动重试（含/不含插件名）
 *   3) normalizeResponse 统一成 {success, data, error}
 */

const PAGE_PLUGIN_NAME = "astrbot_plugin_comfyui_anima";

interface Bridge {
  apiGet(endpoint: string, params?: Record<string, any>): Promise<any>;
  apiPost(endpoint: string, body?: Record<string, any>): Promise<any>;
}

let cachedBridge: Bridge | null = null;
let cachedEndpointStyle = "";
let probePromise: Promise<Bridge | null> | null = null;

function getBridge(): Bridge | null {
  const w = window as any;
  if (w.AstrBotPluginPage) return w.AstrBotPluginPage;
  try {
    if (w.parent && w.parent !== w && w.parent.AstrBotPluginPage) {
      return w.parent.AstrBotPluginPage;
    }
  } catch (e) {
    return null;
  }
  return null;
}

function isUsable(b: Bridge | null | undefined): b is Bridge {
  return Boolean(b && typeof b.apiGet === "function" && typeof b.apiPost === "function");
}

function waitForBridge(timeoutMs: number): Promise<Bridge | null> {
  return new Promise((resolve) => {
    const start = Date.now();
    const timer = setInterval(() => {
      const b = getBridge();
      if (isUsable(b)) {
        clearInterval(timer);
        resolve(b);
      } else if (Date.now() - start > timeoutMs) {
        clearInterval(timer);
        resolve(null);
      }
    }, 100);
  });
}

async function getPageBridge(timeoutMs = 2500): Promise<Bridge> {
  if (isUsable(cachedBridge)) return cachedBridge;
  const b = getBridge();
  if (isUsable(b)) {
    cachedBridge = b;
    return b;
  }
  if (!probePromise) {
    probePromise = waitForBridge(timeoutMs)
      .then((resolved) => {
        if (isUsable(resolved)) {
          cachedBridge = resolved;
          return cachedBridge;
        }
        throw new Error("未检测到 AstrBot 官方插件 Page 桥接，请从 AstrBot 后台的插件拓展页打开");
      })
      .catch((e) => {
        probePromise = null;
        throw e;
      });
  }
  return probePromise;
}

function endpointForStyle(style: string, routePath: string): string {
  const clean = routePath.replace(/^\/+/, "");
  switch (style) {
    case "bare": return clean;
    case "slash": return "/" + clean;
    case "full": return PAGE_PLUGIN_NAME + "/" + clean;
    case "fullSlash": return "/" + PAGE_PLUGIN_NAME + "/" + clean;
    default: return "";
  }
}

interface Candidate { style: string; endpoint: string; }

function bridgeEndpointCandidates(routePath: string): Candidate[] {
  const clean = routePath.replace(/^\/+/, "");
  const byStyle = {
    cached: cachedEndpointStyle ? endpointForStyle(cachedEndpointStyle, clean) : "",
    bare: clean,
    slash: "/" + clean,
    full: PAGE_PLUGIN_NAME + "/" + clean,
    fullSlash: "/" + PAGE_PLUGIN_NAME + "/" + clean,
  };
  const ordered = [
    ["cached", byStyle.cached],
    ["full", byStyle.full],
    ["fullSlash", byStyle.fullSlash],
    ["bare", byStyle.bare],
    ["slash", byStyle.slash],
  ] as [string, string][];
  const seen = new Set<string>();
  return ordered
    .map(([style, ep]) => ({
      style: style === "cached" ? cachedEndpointStyle : style,
      endpoint: String(ep || "").replace(/\/+/g, "/"),
    }))
    .filter((it) => it.style && it.endpoint && !seen.has(it.endpoint) && (seen.add(it.endpoint), true));
}

function isRouteMissingPayload(payload: any): boolean {
  if (!payload || typeof payload !== "object") return false;
  const text = String((payload.error || "") + " " + (payload.message || "") + " " + (payload.detail || "")).toLowerCase();
  return /未找到.*路由|route.*not.*found|not.*found.*route|404/.test(text);
}

function isRouteMissingError(error: any): boolean {
  const text = String((error && error.message) || error || "").toLowerCase();
  return /未找到.*路由|route.*not.*found|not.*found.*route|http\s*404|\b404\b/.test(text);
}

interface Normalized { success: boolean; data: any; error?: string; }

function normalizeResponse(payload: any): Normalized {
  if (payload && typeof payload === "object" && Object.prototype.hasOwnProperty.call(payload, "success")) {
    return payload;
  }
  if (payload && typeof payload === "object") {
    const status = String(payload.status || "").trim().toLowerCase();
    if (["error", "fail", "failed"].includes(status) || payload.ok === false) {
      return { success: false, error: payload.message || payload.error || "请求失败", data: payload.data || {} };
    }
  }
  return { success: true, data: payload };
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error(label + " 超时（" + (ms / 1000) + "s 无响应，可能后端路由未注册或插件未重载）"));
    }, ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

async function bridgeRequest(br: Bridge, path: string, method: string, body: Record<string, any>, timeoutMs?: number): Promise<any> {
  const url = new URL(path, "https://astrbot-plugin-page.local/");
  const routePath = url.pathname.replace(/^\/+/, "");
  const candidates = bridgeEndpointCandidates(routePath);
  const tmo = (timeoutMs && timeoutMs > 0) ? timeoutMs : 6000;
  const errors: string[] = [];

  if (method === "GET") {
    const params = Object.fromEntries(url.searchParams.entries());
    for (const c of candidates) {
      try {
        const p = await withTimeout(
          br.apiGet(c.endpoint, Object.keys(params).length ? params : undefined),
          tmo,
          "GET " + c.endpoint
        );
        if (isRouteMissingPayload(p)) { errors.push(p.message || p.error || "未找到该路由"); continue; }
        cachedEndpointStyle = c.style;
        return p;
      } catch (error: any) {
        errors.push(error && error.message ? error.message : String(error));
        continue;
      }
    }
    throw new Error(errors[0] || "未找到可用的页面 API 路由");
  }

  let payload = body || {};
  if (typeof payload === "string") {
    try { payload = JSON.parse(payload); } catch (e) { payload = {}; }
  }
  // 关键：统一深拷贝，剥掉 Vue 响应式 Proxy。
  // postMessage 走结构化克隆，reactive proxy / ref 解包对象无法被克隆，
  // 会抛 "could not be cloned"。这里统一转成纯 JSON，任何调用方都不会再踩坑。
  try {
    payload = JSON.parse(JSON.stringify(payload));
  } catch (e) {
    // 若含 File/函数等无法 JSON 化的内容，保留原样交由下游处理。
  }
  for (const c of candidates) {
    try {
      const r = await withTimeout(br.apiPost(c.endpoint, payload), tmo, "POST " + c.endpoint);
      if (isRouteMissingPayload(r)) { errors.push(r.message || r.error || "未找到该路由"); continue; }
      cachedEndpointStyle = c.style;
      return r;
    } catch (error: any) {
      errors.push(error && error.message ? error.message : String(error));
      continue;
    }
  }
  throw new Error(errors[0] || "未找到可用的页面 API 路由");
}

// ------------------------------------------------------------------ //
// 独立服务（standalone）模式：当页面从独立 WebUI 服务打开时，没有 AstrBot
// 桥接（AstrBotPluginPage），此时所有 API 走同源 HTTP /api/<endpoint>，
// 并自动附带访问口令 token（从 URL ?token= 或 localStorage 读取）。
// ------------------------------------------------------------------ //
const STANDALONE_TOKEN_KEY = "anima_standalone_token";

function standaloneToken(): string {
  try {
    const q = new URLSearchParams(window.location.search).get("token");
    if (q) return q;
    return localStorage.getItem(STANDALONE_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

function isStandaloneMode(): boolean {
  // 独立服务页面从它自己的源加载，通常 window !== window.parent（iframe 内嵌时
  // parent 有 AstrBotPluginPage）。这里：无可用桥接即视为独立模式，走 HTTP。
  const w = window as any;
  try {
    if (w.AstrBotPluginPage) return false;
    if (w.parent && w.parent !== w && w.parent.AstrBotPluginPage) return false;
  } catch (e) {
    // 跨源访问 parent 时抛错，说明不是内嵌在 AstrBot 内 → 独立模式
  }
  return true;
}

async function standaloneRequest(path: string, method: string, body?: any, timeoutMs?: number): Promise<any> {
  const clean = path.replace(/^\//, "").replace(/\/+/g, "/");
  const tmo = (timeoutMs && timeoutMs > 0) ? timeoutMs : 15000;
  const url = "/api/" + clean;
  const headers: Record<string, string> = {};
  const token = standaloneToken();
  if (token) headers["Authorization"] = "Bearer " + token;
  if (method === "POST" && body !== undefined) headers["Content-Type"] = "application/json";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), tmo);
  try {
    const resp = await fetch(url, {
      method,
      headers,
      signal: ctrl.signal,
      body: method === "POST" && body !== undefined ? JSON.stringify(body) : undefined,
    });
    let payload: any = null;
    try { payload = await resp.json(); } catch { payload = { success: false, error: "响应非 JSON" }; }
    if (payload && typeof payload === "object" && Object.prototype.hasOwnProperty.call(payload, "success")) {
      if (!payload.success) throw new Error(payload.error || "请求失败");
      return payload.data;
    }
    // 兼容非包结构：直接返回 body
    if (!resp.ok) throw new Error("HTTP " + resp.status + (payload && payload.error ? ": " + payload.error : ""));
    return payload;
  } catch (e: any) {
    if (e && e.name === "AbortError") throw new Error("请求超时（" + (tmo / 1000) + "s 无响应）");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export async function apiRaw(path: string, options?: { method?: string; body?: any; timeout?: number }): Promise<any> {
  const opts = options || {};
  const method = (opts.method || "GET").toUpperCase();
  // 独立模式：无 AstrBot 桥接，直接走同源 HTTP API
  if (isStandaloneMode()) {
    return standaloneRequest(path, method, opts.body, opts.timeout);
  }
  const br = await getPageBridge();
  const payload = await bridgeRequest(br, path, method, opts.body, opts.timeout);
  const norm = normalizeResponse(payload);
  if (!norm.success) throw new Error(norm.error || "请求失败");
  return norm.data;
}

/** GET 请求，endpoint 形如 "config"、"gallery/search"（不带插件名、不带 /page）。 */
export function apiGet(endpoint: string, params?: Record<string, any>, options?: { timeout?: number }): Promise<any> {
  let path = endpoint;
  if (params && Object.keys(params).length) {
    const qs = new URLSearchParams();
    Object.keys(params).forEach((k) => {
      const v = params[k];
      // 跳过 undefined / null / 空字符串，避免把 undefined 序列化成字符串 "undefined"
      // 导致后端过滤条件错误（例如 type=undefined 匹配不到任何数据）。
      if (v === undefined || v === null || v === "") return;
      qs.set(k, String(v));
    });
    const q = qs.toString();
    if (q) path = endpoint + "?" + q;
  }
  const opts: { method: string; timeout?: number } = { method: "GET" };
  if (options && options.timeout) opts.timeout = options.timeout;
  return apiRaw(path, opts);
}

/** POST 请求，body 直接作为 JSON 负载发送。timeout 可选（毫秒），默认 6000。 */
export function apiPost(endpoint: string, body?: Record<string, any>, options?: { timeout?: number }): Promise<any> {
  const opts: { method: string; body?: any; timeout?: number } = { method: "POST", body: body || {} };
  if (options && options.timeout) opts.timeout = options.timeout;
  return apiRaw(endpoint, opts);
}

/** 缩略图/大图拉取封装：图库列表只返回 sha，前端按需取 data URL。 */
export async function fetchThumb(sha: string, size = 300, timeout = 15000): Promise<string> {
  const d = await apiGet("gallery/thumb", { sha, size }, { timeout });
  return (d && (d.url || d.data_url)) || "";
}

export async function fetchImageMeta(sha: string): Promise<any> {
  return apiGet("gallery/image", { sha, meta: 1 });
}
