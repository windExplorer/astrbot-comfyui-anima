(function () {
  "use strict";

  const state = {
    config: {},
    configDirty: false,
    logs: [],
    records: [],
    recPage: 1,
    recTotal: 0,
    recPageSize: 40,
    recSearching: false,
    galStats: {},
    galResults: [],
    galSearching: false,
    galPage: 1,
    galTotal: 0,
    galPageSize: 40,
    // stats
    statsScope: "today",
    statsRanking: { rows: [] },
    statsTrend: { buckets: [] },
    // quota（生图限额）
    quota: { global: {}, users: [] },
    // token（LLM 用量统计）
    token: { summary: {}, scenes: [], users: [], models: [], daily: [], detail: [], days: 30 },
  };

  // 封面图 URL 缓存：fname -> url，避免保存/重渲染时反复请求 lora/image
  var coverCache = {};

  const $ = function (id) { return document.getElementById(id); };

  const els = {
    statusText: $("statusText"),
    globalError: $("globalError"),
    globalErrorMessage: $("globalErrorMessage"),
    refreshBtn: $("refreshBtn"),
    toast: $("toast"),
    // config
    cfgContent: $("cfgContent"),
    cfgSaveBtn: $("cfgSaveBtn"),
    cfgSaveMsg: $("cfgSaveMsg"),
    // logs / 出图记录
    logContent: $("logContent"),
    logLevel: $("logLevel"),
    logSearch: $("logSearch"),
    logRefreshBtn: $("logRefreshBtn"),
    logCount: $("logCount"),
    logTabs: $("logTabs"),
    logTabRecords: $("logTabRecords"),
    logTabRunlog: $("logTabRunlog"),
    recBody: $("recBody"),
    recEmpty: $("recEmpty"),
    recSearch: $("recSearch"),
    recFailedOnly: $("recFailedOnly"),
    recCount: $("recCount"),
    recPager: $("recPager"),
    recFirstBtn: $("recFirstBtn"),
    recPrevBtn: $("recPrevBtn"),
    recPageBtns: $("recPageBtns"),
    recNextBtn: $("recNextBtn"),
    recLastBtn: $("recLastBtn"),
    recPageInfo: $("recPageInfo"),
    recJumpInput: $("recJumpInput"),
    recJumpBtn: $("recJumpBtn"),
    // gallery
    galStats: $("galStats"),
    backupDbBtn: $("backupDbBtn"),
    galGrid: $("galGrid"),
    galSearch: $("galSearch"),
    galType: $("galType"),
    galStarred: $("galStarred"),
    galSearchBtn: $("galSearchBtn"),
    galCount: $("galCount"),
    galPager: $("galPager"),
    galFirstBtn: $("galFirstBtn"),
    galPrevBtn: $("galPrevBtn"),
    galPageBtns: $("galPageBtns"),
    galNextBtn: $("galNextBtn"),
    galLastBtn: $("galLastBtn"),
    galPageInfo: $("galPageInfo"),
    galJumpInput: $("galJumpInput"),
    galJumpBtn: $("galJumpBtn"),
    // stats
    statsRefreshBtn: $("statsRefreshBtn"),
    statsMergeBtn: $("statsMergeBtn"),
    statsRanking: $("statsRanking"),
    statsTrendChart: $("statsTrendChart"),
    statsTrendInfo: $("statsTrendInfo"),
    // loras
    lorasRefreshBtn: $("lorasRefreshBtn"),
    lorasGrid: $("lorasGrid"),
    // workflows
    workflowsRefreshBtn: $("workflowsRefreshBtn"),
    workflowsGrid: $("workflowsGrid"),
    // quota（生图限额）
    quotaRefreshBtn: $("quotaRefreshBtn"),
    quotaResetAllBtn: $("quotaResetAllBtn"),
    quotaGlobal: $("quotaGlobal"),
    quotaBody: $("quotaBody"),
    quotaEmpty: $("quotaEmpty"),
    quotaCount: $("quotaCount"),
    // token（LLM 用量统计）
    tokenScope: $("tokenScope"),
    tokenRefreshBtn: $("tokenRefreshBtn"),
    tokenResetAllBtn: $("tokenResetAllBtn"),
    tokenCards: $("tokenCards"),
    tokenTrendChart: $("tokenTrendChart"),
    tokenTrendInfo: $("tokenTrendInfo"),
    tokenSceneBody: $("tokenSceneBody"),
    tokenSceneEmpty: $("tokenSceneEmpty"),
    tokenModelBody: $("tokenModelBody"),
    tokenModelEmpty: $("tokenModelEmpty"),
    tokenModelCount: $("tokenModelCount"),
    tokenUserBody: $("tokenUserBody"),
    tokenUserEmpty: $("tokenUserEmpty"),
    tokenUserCount: $("tokenUserCount"),
    tokenDetailBody: $("tokenDetailBody"),
    tokenDetailEmpty: $("tokenDetailEmpty"),
    tokenDetailCount: $("tokenDetailCount"),
    // dialogs
    confirmDialog: $("confirmDialog"),
    dialogTitle: $("dialogTitle"),
    dialogMessage: $("dialogMessage"),
    qqListDialog: $("qqListDialog"),
    qqListTitle: $("qqListTitle"),
    qqListBody: $("qqListBody"),
    editDialog: $("editDialog"),
    editKicker: $("editKicker"),
    editTitle: $("editTitle"),
    editBody: $("editBody"),
    editMsg: $("editMsg"),
    editSaveBtn: $("editSaveBtn"),
    editCancelBtn: $("editCancelBtn"),
    loraImgDialog: $("loraImgDialog"),
    loraImgFull: $("loraImgFull"),
    imageDialog: $("imageDialog"),
    imageDialogImgs: $("imageDialogImgs"),
    imageDialogImg: $("imageDialogImg"),
    imageDialogResultFig: $("imageDialogResultFig"),
    imageDialogRefImg: $("imageDialogRefImg"),
    imageDialogRefFig: $("imageDialogRefFig"),
    imageDialogCaption1: $("imageDialogCaption1"),
    imageDialogInfo: $("imageDialogInfo"),
  };

  // ---- utils ----
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  function showToast(message, tone) {
    els.toast.textContent = message;
    els.toast.className = "toast show" + (tone === "error" ? " error" : "");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(function () { els.toast.className = "toast"; }, 3200);
  }

  function showGlobalError(messages) {
    var unique = [];
    messages.forEach(function (m) { if (m && unique.indexOf(m) < 0) unique.push(m); });
    els.globalErrorMessage.textContent = unique.length
      ? unique.join("；") : "部分数据加载失败。";
    els.globalError.hidden = false;
  }

  function hideGlobalError() {
    els.globalError.hidden = true;
    els.globalErrorMessage.textContent = "";
  }

  function setButtonBusy(button, busy, busyLabel, idleLabel) {
    button.disabled = busy;
    button.textContent = busy ? busyLabel : idleLabel;
  }

  function errorMessage(reason, fallback) {
    return reason instanceof Error && reason.message ? reason.message : fallback;
  }

  function setStatus(text, isErr) {
    els.statusText.textContent = text;
    var pulse = els.statusText.parentElement.querySelector(".pulse");
    if (pulse) pulse.className = "pulse" + (isErr ? " err" : "");
  }

  // ---- bridge API ----
  // 移植自伴侣插件 astrbot_plugin_private_companion（已被验证可正常展示配置/日志/画廊）。
  // 关键差异（之前空壳的根因）：
  //   1) getBridge 既查 window.AstrBotPluginPage 也查 window.parent.AstrBotPluginPage，
  //      因为插件页面在 AstrBot 后台以 iframe 嵌入，bridge 挂在 parent 上。
  //   2) bridgeRequest 对同一个 endpoint 尝试多种路径风格（含/不含插件名）自动重试，
  //      命中 404/未找到路由则换下一个候选，不再依赖单一前缀写法。
  //   3) normalizeResponse 把后端返回统一成 {success, data, error} 形态，前端只取 data。
  var PAGE_PLUGIN_NAME = "astrbot_plugin_comfyui_anima";
  var PAGE_ENDPOINT_PREFIX = ""; // 对齐 AstrBot 官方约定：bridge endpoint 不带 /page 前缀
  var HTTP_API = "/" + PAGE_PLUGIN_NAME; // fetch 兜底用，含插件名

  let cachedPageBridge = null;
  let cachedPageEndpointStyle = "";
  let pageBridgeProbePromise = null;

  function getBridge() {
    if (window.AstrBotPluginPage) return window.AstrBotPluginPage;
    try {
      if (window.parent && window.parent !== window && window.parent.AstrBotPluginPage) {
        return window.parent.AstrBotPluginPage;
      }
    } catch (error) {
      return null;
    }
    return null;
  }

  function isUsableBridge(b) {
    return Boolean(b && typeof b.apiGet === "function" && typeof b.apiPost === "function");
  }

  async function getPageBridge(timeoutMs) {
    timeoutMs = timeoutMs || 2500;
    if (isUsableBridge(cachedPageBridge)) return cachedPageBridge;
    var b = getBridge();
    if (isUsableBridge(b)) {
      cachedPageBridge = b;
      return cachedPageBridge;
    }
    if (!pageBridgeProbePromise) {
      pageBridgeProbePromise = waitForBridge(timeoutMs)
        .then(function (resolved) {
          if (isUsableBridge(resolved)) {
            cachedPageBridge = resolved;
            return cachedPageBridge;
          }
          throw new Error("未检测到 AstrBot 官方插件 Page 桥接，请从 AstrBot 后台的插件拓展页打开");
        })
        .catch(function (e) { pageBridgeProbePromise = null; throw e; });
    }
    return pageBridgeProbePromise;
  }

  function waitForBridge(timeoutMs) {
    timeoutMs = timeoutMs || 2500;
    return new Promise(function (resolve) {
      var start = Date.now();
      var timer = setInterval(function () {
        var b = getBridge();
        if (isUsableBridge(b)) {
          clearInterval(timer);
          resolve(b);
        } else if (Date.now() - start > timeoutMs) {
          clearInterval(timer);
          resolve(null);
        }
      }, 100);
    });
  }

  function bridgeEndpointCandidates(routePath) {
    var cleanRoute = String(routePath || "").replace(/^\/+/, "");
    var byStyle = {
      cached: cachedPageEndpointStyle ? endpointForStyle(cachedPageEndpointStyle, cleanRoute) : "",
      bare: cleanRoute,
      slash: "/" + cleanRoute,
      full: PAGE_PLUGIN_NAME + "/" + cleanRoute,
      fullSlash: "/" + PAGE_PLUGIN_NAME + "/" + cleanRoute,
    };
    var ordered = [
      ["cached", byStyle.cached],
      ["full", byStyle.full],
      ["fullSlash", byStyle.fullSlash],
      ["bare", byStyle.bare],
      ["slash", byStyle.slash],
    ];
    var seen = new Set();
    return ordered
      .map(function (it) {
        return {
          style: it[0] === "cached" ? cachedPageEndpointStyle : it[0],
          endpoint: String(it[1] || "").replace(/\/+/g, "/"),
        };
      })
      .filter(function (item) {
        return item.style && item.endpoint && !seen.has(item.endpoint) && seen.add(item.endpoint);
      });
  }

  function endpointForStyle(style, routePath) {
    var cleanRoute = String(routePath || "").replace(/^\/+/, "");
    switch (style) {
      case "bare": return cleanRoute;
      case "slash": return "/" + cleanRoute;
      case "full": return PAGE_PLUGIN_NAME + "/" + cleanRoute;
      case "fullSlash": return "/" + PAGE_PLUGIN_NAME + "/" + cleanRoute;
      default: return "";
    }
  }

  function isRouteMissingPayload(payload) {
    if (!payload || typeof payload !== "object") return false;
    var text = String((payload.error || "") + " " + (payload.message || "") + " " + (payload.detail || "")).toLowerCase();
    return /未找到.*路由|route.*not.*found|not.*found.*route|404/.test(text);
  }

  function isRouteMissingError(error) {
    var text = String((error && error.message) || error || "").toLowerCase();
    return /未找到.*路由|route.*not.*found|not.*found.*route|http\s*404|\b404\b/.test(text);
  }

  function normalizeResponse(payload) {
    if (payload && typeof payload === "object" && Object.prototype.hasOwnProperty.call(payload, "success")) {
      return payload;
    }
    if (payload && typeof payload === "object") {
      var status = String(payload.status || "").trim().toLowerCase();
      if (["error", "fail", "failed"].includes(status) || payload.ok === false) {
        return { success: false, error: payload.message || payload.error || "请求失败", data: payload.data || {} };
      }
    }
    return { success: true, data: payload };
  }

  // 带超时包装，避免接口 hang 时永远停在「正在读取…」
  function withTimeout(promise, ms, label) {
    var timer = null;
    var timeout = new Promise(function (_, reject) {
      timer = setTimeout(function () {
        reject(new Error(label + " 超时（" + (ms / 1000) + "s 无响应，可能后端路由未注册或插件未重载）"));
      }, ms);
    });
    return Promise.race([promise, timeout]).finally(function () { clearTimeout(timer); });
  }

  async function bridgeRequest(br, path, method, body, timeoutMs) {
    var url = new URL(path, "https://astrbot-plugin-page.local/");
    var routePath = url.pathname.replace(/^\/+/, "");
    var candidates = bridgeEndpointCandidates(routePath);
    var tmo = (timeoutMs && timeoutMs > 0) ? timeoutMs : 6000;
    console.log("[anima-console] bridgeRequest 开始:", method, path, "候选路径(", candidates.length, "):", candidates.map(function (c) { return c.style + "=" + c.endpoint; }));
    var errors = [];
    var attempts = [];
    function recordAttempt(style, endpoint, ms, note) {
      attempts.push(style + "=" + endpoint + " (" + ms.toFixed(0) + "ms, " + note + ")");
      console.log("[anima-console]   尝试 " + style + "=" + endpoint + " 耗时 " + ms.toFixed(0) + "ms -> " + note);
    }
    if (method === "GET") {
      var params = Object.fromEntries(url.searchParams.entries());
      for (var i = 0; i < candidates.length; i++) {
        var t0 = performance.now();
        try {
          var p = await withTimeout(br.apiGet(candidates[i].endpoint, Object.keys(params).length ? params : undefined), tmo, "GET " + candidates[i].endpoint);
          recordAttempt(candidates[i].style, candidates[i].endpoint, performance.now() - t0, "命中");
          if (isRouteMissingPayload(p)) { errors.push((p.message || p.error || "未找到该路由")); continue; }
          cachedPageEndpointStyle = candidates[i].style;
          console.log("[anima-console] bridgeRequest 成功，使用风格:", candidates[i].style);
          return p;
        } catch (error) {
          var isMissing = isRouteMissingError(error);
          recordAttempt(candidates[i].style, candidates[i].endpoint, performance.now() - t0, isMissing ? "路由不存在(404)" : "异常:" + (error && error.message ? error.message : error));
          // 路由不存在(404)或超时/网络异常都视为该候选失败，继续尝试后续候选
          errors.push((error && error.message ? error.message : String(error)));
          continue;
        }
      }
      console.error("[anima-console] bridgeRequest 全部候选路径失败。尝试记录:", attempts);
      throw new Error(errors[0] || "未找到可用的页面 API 路由（已尝试: " + attempts.join("; ") + "）");
    }
    var payload = body || {};
    if (typeof payload === "string") {
      try { payload = JSON.parse(payload); } catch (e) { payload = {}; }
    }
    for (var j = 0; j < candidates.length; j++) {
      var t1 = performance.now();
      try {
        var r = await withTimeout(br.apiPost(candidates[j].endpoint, payload), tmo, "POST " + candidates[j].endpoint);
        recordAttempt(candidates[j].style, candidates[j].endpoint, performance.now() - t1, "命中");
        if (isRouteMissingPayload(r)) { errors.push((r.message || r.error || "未找到该路由")); continue; }
        cachedPageEndpointStyle = candidates[j].style;
        console.log("[anima-console] bridgeRequest 成功，使用风格:", candidates[j].style);
        return r;
      } catch (error) {
        var isMissing2 = isRouteMissingError(error);
        recordAttempt(candidates[j].style, candidates[j].endpoint, performance.now() - t1, isMissing2 ? "路由不存在(404)" : "异常:" + (error && error.message ? error.message : error));
        errors.push((error && error.message ? error.message : String(error)));
        continue;
      }
    }
    console.error("[anima-console] bridgeRequest 全部候选路径失败。尝试记录:", attempts);
    throw new Error(errors[0] || "未找到可用的页面 API 路由（已尝试: " + attempts.join("; ") + "）");
  }

  async function apiRaw(path, options) {
    options = options || {};
    var br = await getPageBridge();
    var method = (options.method || "GET").toUpperCase();
    var payload;
    if (br && isUsableBridge(br)) {
      payload = await bridgeRequest(br, path, method, options.body, options.timeout);
    } else {
      // fetch 兜底（仅 debug_http=1 时伴侣插件才走；这里直接抛，引导用后台打开）
      throw new Error("未检测到 AstrBot 官方插件 Page 桥接，请从 AstrBot 后台的插件拓展页打开");
    }
    payload = normalizeResponse(payload);
    if (!payload.success) throw new Error(payload.error || "请求失败");
    return payload.data;
  }

  function imageUrl(sha) {
    return "/" + PAGE_PLUGIN_NAME + "/gallery/image?sha=" + encodeURIComponent(sha || "");
  }

  async function apiGet(endpoint, params) {
    var path = endpoint;
    if (params && Object.keys(params).length) {
      var qs = new URLSearchParams();
      Object.keys(params).forEach(function (k) { qs.set(k, params[k]); });
      path = endpoint + "?" + qs.toString();
    }
    return apiRaw(path, { method: "GET" });
  }

  async function apiPost(endpoint, body) {
    return apiRaw(endpoint, { method: "POST", body: body || {} });
  }

  // ---- 图库缩略图懒加载（参照 astrbot_plugin_stealer）----
  // 列表接口只返回元数据；缩略图在图片进入视口时经 bridge 逐个拉取单张 data URL，
  // 配 LRU 缓存。这样既不走 AstrBot 裸路径（404/401），也不会一次内联几十张图超时。
  var THUMB_PLACEHOLDER = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
  var THUMB_LRU = new Map();
  var THUMB_LRU_MAX = 200;
  var thumbObserver = null;
  var thumbLoading = new Set();

  function thumbCacheGet(sha) {
    if (!THUMB_LRU.has(sha)) return null;
    var v = THUMB_LRU.get(sha);
    THUMB_LRU.delete(sha);
    THUMB_LRU.set(sha, v); // 刷新 LRU
    return v;
  }
  function thumbCacheSet(sha, url) {
    if (THUMB_LRU.has(sha)) THUMB_LRU.delete(sha);
    THUMB_LRU.set(sha, url);
    if (THUMB_LRU.size > THUMB_LRU_MAX) {
      var oldest = THUMB_LRU.keys().next().value;
      if (oldest != null) THUMB_LRU.delete(oldest);
    }
  }

  async function loadThumb(sha) {
    if (!sha || thumbLoading.has(sha)) return;
    var cached = thumbCacheGet(sha);
    if (cached) { applyThumb(sha, cached); return; }
    thumbLoading.add(sha);
    try {
      var data = await apiGet("gallery/thumb", { sha: sha, size: 300 });
      var url = data && (data.url || data.data_url);
      if (url) {
        thumbCacheSet(sha, url);
        applyThumb(sha, url);
      }
    } catch (e) {
      console.error("[anima-console] 缩略图加载失败:", sha, e);
    } finally {
      thumbLoading.delete(sha);
    }
  }

  function applyThumb(sha, url) {
    document.querySelectorAll('[data-sha="' + cssEscape(sha) + '"]').forEach(function (img) {
      if (img && img.src !== url) img.src = url;
    });
  }

  function cssEscape(value) {
    return String(value).replace(/([^a-zA-Z0-9_-])/g, "\\$1");
  }

  function ensureThumbObserver() {
    if (thumbObserver) return;
    if (typeof IntersectionObserver !== "function") {
      // 不支持 IntersectionObserver 时退化为立即加载
      thumbObserver = { observe: function (el) { loadThumb(el.dataset.sha); } };
      return;
    }
    thumbObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var el = entry.target;
          var sha = el && el.dataset.sha;
          if (sha) loadThumb(sha);
          thumbObserver.unobserve(el);
        }
      });
    }, { rootMargin: "200px" });
  }

  function observeThumbs() {
    ensureThumbObserver();
    document.querySelectorAll("[data-sha]").forEach(function (el) {
      if (el.dataset.observed) return;
      el.dataset.observed = "1";
      thumbObserver.observe(el);
    });
  }

  // ---- confirm dialog ----
  function confirmAction(title, message) {
    var triggerEl = document.activeElement;
    els.dialogTitle.textContent = title;
    els.dialogMessage.textContent = message;
    els.confirmDialog.showModal();
    return new Promise(function (resolve) {
      var onClose = function () {
        els.confirmDialog.removeEventListener("close", onClose);
        if (triggerEl && typeof triggerEl.focus === "function") triggerEl.focus();
        resolve(els.confirmDialog.returnValue === "confirm");
      };
      els.confirmDialog.addEventListener("close", onClose);
    });
  }

  // ---- view switching ----
  function switchView(name) {
    document.querySelectorAll(".workspace-nav [data-view]").forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.view === name);
    });
    document.querySelectorAll(".workspace").forEach(function (view) {
      view.classList.toggle("active", view.id === name + "View");
    });
    history.replaceState(null, "", "#" + name);
    // lazy load
    if (name === "logs") {
      setLogTab("records"); // 默认展示出图记录
      if (!state.records.length) loadRecords();
      if (!state.logs.length) loadLogs();
    }
    if (name === "gallery") {
      if (galTabState !== "normal") {
        galTabState = "normal";
        if (els.galTabs) els.galTabs.querySelectorAll(".tab").forEach(function (x) {
          x.classList.toggle("active", x.dataset.galtab === "normal");
        });
      }
      if (!state.galResults.length) galSearch();
    }
    if (name === "stats") {
      if (!state.statsRanking.rows.length) loadStatsRanking();
      if (!state.statsTrend.buckets.length) loadStatsTrend();
    }
    if (name === "loras") {
      if (!state.config || !state.config.loras) loadConfig();
      renderLoras();
    }
    if (name === "workflows") {
      if (!state.config || !state.config.workflows) loadConfig();
      renderWorkflows();
    }
    if (name === "quota") {
      loadQuota();
    }
    if (name === "token") {
      loadToken();
    }
  }

  // ====== CONFIG ======
  // 基于插件 _conf_schema.json 结构化渲染配置编辑器（而非把 config 当黑盒拍平）。
  async function loadConfig() {
    try {
      var results = await Promise.all([apiGet("config"), apiGet("schema")]);
      state.config = results[0] || {};
      state.schema = results[1] || {};
      state.configDirty = false;
      renderConfig();
      setStatus("配置已加载");
    } catch (e) {
      els.cfgContent.innerHTML = '<div class="empty error">读取配置失败：' + escapeHtml(e.message) + '</div>';
      setStatus("配置加载失败", true);
    }
  }

  // 按 data-path（点分路径，数组用数字段）读取/写入配置值
  function getPath(obj, path) {
    var parts = String(path).split(".");
    var cur = obj;
    for (var i = 0; i < parts.length; i += 1) {
      if (cur == null) return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  }
  function setPath(obj, path, val) {
    var parts = String(path).split(".");
    var cur = obj;
    for (var i = 0; i < parts.length - 1; i += 1) {
      var k = parts[i];
      if (cur[k] == null || typeof cur[k] !== "object") cur[k] = /^\d+$/.test(parts[i + 1]) ? [] : {};
      cur = cur[k];
    }
    cur[parts[parts.length - 1]] = val;
  }

  function fieldHintHtml(field) {
    var h = "";
    if (field.description) h += '<p class="fh-desc">' + escapeHtml(field.description) + '</p>';
    if (field.hint) h += '<p class="fh-hint">' + escapeHtml(field.hint) + '</p>';
    return h;
  }

  // 需要从「已配置工作流名」动态生成下拉的默认工作流配置项
  var DYNAMIC_WORKFLOW_KEYS = [
    "default_workflow",
    "default_workflow_real",
    "default_img2img_workflow",
    "default_img2img_workflow_real"
  ];

  // 取当前已配置的工作流名列表（用于默认工作流下拉）
  function getWorkflowNames() {
    var wfs = (state.config && Array.isArray(state.config.workflows)) ? state.config.workflows : [];
    var names = [];
    wfs.forEach(function (w) {
      if (w && w.name) names.push(String(w.name));
    });
    return names;
  }

  // 渲染单个基础字段（bool / string / int / float / text / 带 slider）
  function renderField(path, field, value) {
    var type = field.type || "string";
    // 默认工作流类配置项：从已配置工作流名动态生成下拉（避免手敲出错）
    if (DYNAMIC_WORKFLOW_KEYS.indexOf(path) >= 0 && type === "string") {
      var names = getWorkflowNames();
      var opts = '<option value="">（未设置，回退第一个工作流）</option>';
      names.forEach(function (n) {
        opts += '<option value="' + escapeHtml(n) + '"' + (n === String(value || "") ? " selected" : "") + '>' + escapeHtml(n) + '</option>';
      });
      var inner = '<select data-path="' + escapeHtml(path) + '">' + opts + '</select>';
      return '<div class="cfg-field"><div class="cfg-field-head">' + inner + '</div>' + fieldHintHtml(field) + '</div>';
    }
    var inner = "";
    if (type === "bool") {
      inner = '<label class="switch"><input type="checkbox" data-path="' + escapeHtml(path) + '" ' +
        (value ? "checked" : "") + ' /><span class="slider-ui"></span></label>';
    } else if (type === "text") {
      inner = '<textarea data-path="' + escapeHtml(path) + '" rows="3">' +
        escapeHtml(value == null ? "" : String(value)) + '</textarea>';
    } else if (type === "int" || type === "float") {
      var numVal = value == null ? "" : String(value);
      if (field.slider) {
        var min = field.slider.min, max = field.slider.max, step = field.slider.step || 1;
        inner = '<div class="num-slider">' +
          '<input type="range" data-path="' + escapeHtml(path) + '" data-num="1" min="' + min + '" max="' + max +
          '" step="' + step + '" value="' + (value == null ? min : value) + '" />' +
          '<input type="number" data-path="' + escapeHtml(path) + '" data-num="1" value="' + numVal +
          '" step="' + (type === "float" ? "any" : step) + '" />' +
          '</div>';
      } else {
        inner = '<input type="number" data-path="' + escapeHtml(path) + '" value="' + numVal +
          '" step="' + (type === "float" ? "any" : "1") + '" />';
      }
    } else if (Array.isArray(field.options) && field.options.length) {
      // 带可选项的 string 配置（如 default_style_priority / img2img_fallback）：渲染下拉
      var cur = value == null ? "" : String(value);
      var opts = "";
      field.options.forEach(function (o) {
        var label = (typeof o === "object" && o.label != null) ? String(o.label) : String(o);
        var val = (typeof o === "object" && o.value != null) ? String(o.value) : String(o);
        opts += '<option value="' + escapeHtml(val) + '"' + (val === cur ? " selected" : "") + '>' + escapeHtml(label) + '</option>';
      });
      inner = '<select data-path="' + escapeHtml(path) + '">' + opts + '</select>';
    } else if (field.multiline) {
      // 多行 string 配置（如 workflow_aliases 别名映射）：textarea，支持换行输入
      inner = '<textarea data-path="' + escapeHtml(path) + '" rows="4">' +
        escapeHtml(value == null ? "" : String(value)) + '</textarea>';
    } else {
      inner = '<input type="text" data-path="' + escapeHtml(path) + '" value="' +
        escapeHtml(value == null ? "" : String(value)) + '" />';
    }
    return '<div class="cfg-field"><div class="cfg-field-head">' + inner + '</div>' + fieldHintHtml(field) + '</div>';
  }

  // 渲染嵌套 object
  function renderObject(path, schemaObj, valueObj) {
    var html = '<div class="cfg-group">';
    Object.keys(schemaObj.items || {}).forEach(function (k) {
      var child = schemaObj.items[k];
      var v = valueObj ? valueObj[k] : undefined;
      var childPath = path ? path + "." + k : k;
      html += '<div class="cfg-sub"><h4>' + escapeHtml(k) + '</h4>' + renderFieldByType(childPath, child, v) + '</div>';
    });
    html += '</div>';
    return html;
  }

  // 渲染 template_list（如服务器列表 / LoRA 库 / 工作流列表）
  function renderTemplateList(path, field, listArr) {
    var itemsSchema = (field.templates && field.templates.default && field.templates.default.items) || {};
    var displayKey = (field.templates && field.templates.default && field.templates.default.display_item) || "name";
    var arr = Array.isArray(listArr) ? listArr : [];
    var html = '<div class="tmpl-list" data-list="' + escapeHtml(path) + '">';
    arr.forEach(function (item, idx) {
      var title = item && item[displayKey] ? item[displayKey] : (field.templates.default.name + " " + (idx + 1));
      html += '<div class="tmpl-item" data-index="' + idx + '">';
      html += '<div class="tmpl-head"><span>' + escapeHtml(String(title)) + '</span>' +
        '<button type="button" class="tmpl-del" data-del="' + idx + '">删除</button></div>';
      html += '<div class="tmpl-body">';
      Object.keys(itemsSchema).forEach(function (k) {
        var child = itemsSchema[k];
        var childPath = path + "." + idx + "." + k;
        html += '<div class="cfg-sub"><h4>' + escapeHtml(k) + '</h4>' + renderFieldByType(childPath, child, item ? item[k] : undefined) + '</div>';
      });
      html += '</div></div>';
    });
    html += '</div>';
    html += '<button type="button" class="tmpl-add" data-add="' + escapeHtml(path) + '">+ 添加' +
      (field.templates && field.templates.default ? escapeHtml(field.templates.default.name) : "条目") + '</button>';
    return html;
  }

  function renderFieldByType(path, field, value) {
    var type = field.type || "string";
    if (type === "object") return renderObject(path, field, value);
    if (type === "template_list") return renderTemplateList(path, field, value);
    return renderField(path, field, value);
  }

  function renderConfig() {
    var schema = state.schema || {};
    var keys = Object.keys(schema);
    if (!keys.length) {
      els.cfgContent.innerHTML = '<div class="empty">未获取到配置结构（schema 为空）</div>';
      els.cfgSaveBtn.disabled = true;
      return;
    }
    // 分区元数据（前端硬编码，仅 WebUI 展示用；不放 schema 避免 AstrBot 当配置解析报 'type' 错误）
    var CFG_GROUPS = {
      "服务器与模型": { description: "ComfyUI 服务器、工作流与 LoRA 库", icon: "server", keys: ["comfyui_servers", "workflows", "loras"] },
      "默认工作流": { description: "未指定工作流时的默认选择与风格优先级", icon: "workflow", keys: ["default_style_priority", "default_workflow", "default_workflow_real", "default_img2img_workflow", "default_img2img_workflow_real", "img2img_fallback"] },
      "AI 对话与 LLM": { description: "AI 对话调用的 LLM 工具开关与专用模型", icon: "ai", keys: ["enable_llm_tools", "llm_model"] },
      "Anima 翻译": { description: "Anima 工作流中文提示词翻译模式与接口（danbooru / llm / api）", icon: "translate", keys: ["translator_mode", "translate_llm_model", "translate_api", "danbooru"] },
      "出图行为": { description: "出图等待、轮询、webp 转换与小报告等行为", icon: "image", keys: ["draw_timeout", "queue_extra_timeout", "max_draw_timeout", "queue_poll_interval", "return_queue_position", "convert_webp_to_png", "show_draw_report"] },
      "网络与代理": { description: "外部网络访问（如 C 站抓取）的代理设置与 C 站 API Key", icon: "network", keys: ["http_proxy", "civitai_api_key"] },
      "权限与图库": { description: "发图白名单、生图次数限制与图片画廊归档", icon: "lock", keys: ["allow_draw_users", "draw_limit", "gallery"] }
    };
    var groupsMeta = CFG_GROUPS || {};
    // 收集所有已分区 key
    var groupedKeys = {};
    Object.keys(groupsMeta).forEach(function (gname) {
      (groupsMeta[gname].keys || []).forEach(function (k) { groupedKeys[k] = true; });
    });
    function renderSection(key) {
      var field = schema[key];
      var val = state.config ? state.config[key] : undefined;
      var html = '';
      html += '<section class="cfg-section" data-key="' + escapeHtml(key) + '">';
      html += '<div class="cfg-section-title"><h3>' + escapeHtml(key) + '</h3>' +
        (field.description ? '<span>' + escapeHtml(field.description) + '</span>' : '') + '</div>';
      html += '<div class="cfg-section-body">';
      if (field.type === "object") {
        html += renderObject(key, field, val);
      } else if (field.type === "template_list") {
        html += renderTemplateList(key, field, val);
      } else {
        html += renderField(key, field, val);
      }
      html += '</div></section>';
      return html;
    }
    var html = '<div class="cfg-sections">';
    var groupNames = Object.keys(groupsMeta);
    if (groupNames.length) {
      // 分区折叠面板
      html += '<div class="cfg-groups">';
      groupNames.forEach(function (gname, gi) {
        var g = groupsMeta[gname] || {};
        var gkeys = (g.keys || []).filter(function (k) { return schema[k]; });
        if (!gkeys.length) return;
        var desc = g.description || "";
        var icon = g.icon || "folder";
        var open = gi === 0; // 默认展开第一组
        html += '<div class="cfg-group' + (open ? " open" : "") + '" data-group="' + escapeHtml(gname) + '">';
        html += '<button type="button" class="cfg-group-head" aria-expanded="' + (open ? "true" : "false") + '">'
          + '<span class="cfg-group-icon" aria-hidden="true">' + escapeHtml(icon) + '</span>'
          + '<span class="cfg-group-title">' + escapeHtml(gname) + '</span>'
          + (desc ? '<span class="cfg-group-desc">' + escapeHtml(desc) + '</span>' : '')
          + '<span class="cfg-group-arrow">▸</span>'
          + '</button>';
        html += '<div class="cfg-group-body"' + (open ? "" : " hidden") + '>';
        gkeys.forEach(function (k) { html += renderSection(k); });
        html += '</div></div>';
      });
      // 未分区的兜底
      var leftover = keys.filter(function (k) { return k !== "_groups" && !groupedKeys[k]; });
      if (leftover.length) {
        html += '<div class="cfg-group"><button type="button" class="cfg-group-head" aria-expanded="false">'
          + '<span class="cfg-group-icon">other</span><span class="cfg-group-title">其他</span><span class="cfg-group-arrow">▸</span>'
          + '</button><div class="cfg-group-body" hidden>';
        leftover.forEach(function (k) { html += renderSection(k); });
        html += '</div></div>';
      }
      html += '</div>';
    } else {
      // 无分区元数据：平铺（旧行为）
      keys.forEach(function (k) {
        if (k === "_groups") return;
        html += renderSection(k);
      });
    }
    html += '</div>';

    // ---- 翻译调试面板（验证三种翻译模式的连接与效果）----
    html += '<section class="panel cfg-section translate-debug" id="translateDebugPanel">'
      + '<div class="cfg-section-title"><h3>翻译调试</h3>'
      + '<span>测试 Anima 翻译模式（danbooru / llm / api）是否连通、返回效果如何。'
      + '仅做单次调用，不改动任何配置。</span></div>'
      + '<div class="cfg-section-body">'
      + '<div class="tran-debug-mode-row">'
      + '<label for="tranDebugMode">翻译模式</label>'
      + '<select id="tranDebugMode">'
      + '<option value="danbooru">danbooru（标签服务器）</option>'
      + '<option value="llm">llm（大模型翻译）</option>'
      + '<option value="api">api（通用 HTTP 翻译接口）</option>'
      + '</select>'
      + '<label for="tranDebugText">中文描述</label>'
      + '<input id="tranDebugText" type="text" value="帅气的少年, 水手服少女, 微笑" />'
      + '<button type="button" id="tranDebugBtn">测试翻译</button>'
      + '</div>'
      + '<div id="tranDebugResult" class="tran-debug-result" aria-live="polite"></div>'
      + '</div></section>';

    els.cfgContent.innerHTML = html;
    els.cfgSaveBtn.disabled = true;
    els.cfgSaveMsg.textContent = "";

    // 翻译调试事件
    var tranBtn = document.getElementById("tranDebugBtn");
    if (tranBtn) {
      tranBtn.addEventListener("click", async function () {
        var mode = document.getElementById("tranDebugMode").value;
        var text = document.getElementById("tranDebugText").value.trim();
        var resEl = document.getElementById("tranDebugResult");
        if (!resEl || !text) return;
        resEl.innerHTML = '<div class="empty">测试中…</div>';
        var btn = tranBtn;
        var oldText = btn.textContent;
        btn.disabled = true; btn.textContent = "测试中…";
        try {
          var r = await apiPost("translate/test", { mode: mode, text: text });
          if (!r) { resEl.innerHTML = '<div class="empty error">无响应</div>'; return; }
          if (r.ok) {
            resEl.innerHTML = '<div class="tran-debug-item ok"><span class="tran-debug-status">✓ 成功</span>'
              + '<div class="tran-debug-detail"><div><b>耗时：</b>' + escapeHtml(String(r.elapsed_ms)) + ' ms</div>'
              + '<div><b>结果：</b><code class="tran-debug-code">' + escapeHtml(r.result || "") + '</code></div></div></div>';
          } else {
            resEl.innerHTML = '<div class="tran-debug-item err"><span class="tran-debug-status">✗ 失败</span>'
              + '<div class="tran-debug-detail"><div><b>耗时：</b>' + escapeHtml(String(r.elapsed_ms)) + ' ms</div>'
              + '<div><b>错误：</b><code class="tran-debug-code">' + escapeHtml(r.error || "未知错误") + '</code></div></div></div>';
          }
        } catch (e) {
          resEl.innerHTML = '<div class="empty error">调用失败：' + escapeHtml(e && e.message ? e.message : String(e)) + '</div>';
        } finally {
          btn.disabled = false; btn.textContent = oldText;
        }
      });
    }

    // 分组折叠交互
    els.cfgContent.querySelectorAll(".cfg-group-head").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var group = btn.closest(".cfg-group");
        if (!group) return;
        var isOpen = group.classList.contains("open");
        var body = group.querySelector(".cfg-group-body");
        if (isOpen) {
          group.classList.remove("open");
          if (body) body.hidden = true;
          btn.setAttribute("aria-expanded", "false");
        } else {
          group.classList.add("open");
          if (body) body.hidden = false;
          btn.setAttribute("aria-expanded", "true");
        }
      });
    });

    // 标记 dirty + slider/number 联动
    var sliders = {};
    els.cfgContent.querySelectorAll("[data-path]").forEach(function (el) {
      var mark = function () { markDirty(); };
      el.addEventListener("input", mark);
      el.addEventListener("change", mark);
      if (el.type === "range") sliders[el.dataset.path] = el;
    });
    // range 与 number 双向联动（同 data-path 且 data-num=1 的两个控件）
    Object.keys(sliders).forEach(function (p) {
      var range = sliders[p];
      var num = els.cfgContent.querySelector('input[type="number"][data-path="' + cssEsc(p) + '"][data-num="1"]');
      if (num) {
        range.addEventListener("input", function () { num.value = range.value; });
        num.addEventListener("input", function () { range.value = num.value; });
      }
    });

    // 模板列表：添加 / 删除
    els.cfgContent.querySelectorAll(".tmpl-add").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var listPath = btn.dataset.add;
        var field = getPath(state.schema, listPath);
        var itemsSchema = (field && field.templates && field.templates.default && field.templates.default.items) || {};
        var empty = {};
        Object.keys(itemsSchema).forEach(function (k) {
          if ("default" in itemsSchema[k]) empty[k] = itemsSchema[k].default;
        });
        var arr = getPath(state.config, listPath);
        if (!Array.isArray(arr)) { arr = []; setPath(state.config, listPath, arr); }
        arr.push(empty);
        markDirty();
        renderConfig();
      });
    });
    els.cfgContent.querySelectorAll(".tmpl-del").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var listPath = btn.closest(".tmpl-list").dataset.list;
        var idx = Number(btn.dataset.del);
        var arr = getPath(state.config, listPath);
        if (Array.isArray(arr)) {
          arr.splice(idx, 1);
          markDirty();
          renderConfig();
        }
      });
    });
  }

  function cssEsc(s) {
    return String(s).replace(/["\\]/g, "\\$&");
  }

  function markDirty() {
    state.configDirty = true;
    els.cfgSaveBtn.disabled = false;
    els.cfgSaveMsg.textContent = "";
  }

  function readConfigForm() {
    var cfg = JSON.parse(JSON.stringify(state.config || {}));
    els.cfgContent.querySelectorAll("[data-path]").forEach(function (el) {
      var path = el.dataset.path;
      var raw = el.type === "checkbox" ? el.checked : el.value;
      var val;
      if (el.type === "checkbox") {
        val = el.checked;
      } else if (el.type === "number" || el.dataset.num === "1") {
        val = raw === "" ? null : Number(raw);
      } else {
        val = el.value;
      }
      setPath(cfg, path, val);
    });
    return cfg;
  }

  els.cfgSaveBtn.addEventListener("click", async function () {
    if (!state.configDirty) return;
    els.cfgSaveMsg.textContent = "";
    setButtonBusy(els.cfgSaveBtn, true, "保存中…", "保存配置");
    try {
      var cfg = readConfigForm();
      await apiPost("config", { config: cfg });
      state.config = cfg;
      state.configDirty = false;
      els.cfgSaveBtn.disabled = true;
      els.cfgSaveMsg.className = "form-msg ok";
      els.cfgSaveMsg.textContent = "配置已保存";
      showToast("配置已保存");
    } catch (e) {
      els.cfgSaveMsg.className = "form-msg err";
      els.cfgSaveMsg.textContent = e.message || "保存失败";
      showToast(e.message || "保存失败", "error");
    } finally {
      setButtonBusy(els.cfgSaveBtn, false, "保存中…", "保存配置");
    }
  });

  // ====== 出图记录 + 运行日志 ======
  var logTabState = "records"; // 默认显示「出图记录」

  async function loadRecords() {
    // 首次加载重置到第一页
    await loadRecordsPage(1, true);
  }

  // 翻页加载出图记录（替换式，不做累加）。
  async function loadRecordsPage(page, reset) {
    if (state.recSearching) return;
    state.recSearching = true;
    var data;
    try {
      data = await apiGet("records", {
        failed: els.recFailedOnly.checked ? 1 : 0,
        keyword: els.recSearch.value.trim(),
        page: page,
        size: state.recPageSize,
      });
    } catch (e) {
      els.recBody.innerHTML = '<tr><td colspan="9" class="empty error">读取出图记录失败：' + escapeHtml(e.message) + '</td></tr>';
      state.recSearching = false;
      throw e;
    }
    var rows = Array.isArray(data) ? data : (data && Array.isArray(data.records) ? data.records : []);
    state.recTotal = (data && data.total != null) ? Number(data.total) : 0;
    state.records = rows;
    state.recPage = page;
    if (!state.records.length) {
      els.recEmpty.hidden = false;
      els.recBody.innerHTML = "";
    } else {
      els.recEmpty.hidden = true;
    }
    els.recCount.textContent = state.recTotal ? state.recTotal + " 条" : state.records.length + " 条";
    renderRecords();
    updateRecPager();
    setStatus(reset ? "出图记录已加载" : "已翻页");
    state.recSearching = false;
  }

  // 通用分页器：生成「首页/上一页/页码(带省略)/下一页/末页」按钮。
  // cfg: { firstBtn, prevBtn, btnsEl, nextBtn, lastBtn, infoEl, jumpInput, jumpBtn,
  //        page, totalPages, onGo }
  function renderPager(cfg) {
    var page = cfg.page, totalPages = cfg.totalPages;
    var onGo = cfg.onGo;
    var hasPrev = page > 1;
    var hasNext = page < totalPages;
    if (cfg.firstBtn) cfg.firstBtn.disabled = !hasPrev;
    if (cfg.prevBtn) cfg.prevBtn.disabled = !hasPrev;
    if (cfg.nextBtn) cfg.nextBtn.disabled = !hasNext;
    if (cfg.lastBtn) cfg.lastBtn.disabled = !hasNext;
    if (cfg.infoEl) cfg.infoEl.textContent = "共 " + totalPages + " 页";
    // 生成页码按钮（含省略号）
    if (cfg.btnsEl) {
      var html = "";
      var shown = pageNums(page, totalPages);
      var prev = 0;
      shown.forEach(function (p) {
        if (prev && p - prev > 1) html += '<span class="pager-ellipsis">…</span>';
        html += '<button type="button" class="pager-num' + (p === page ? " active" : "") + '" data-page="' + p + '">' + p + '</button>';
        prev = p;
      });
      cfg.btnsEl.innerHTML = html;
      Array.prototype.forEach.call(cfg.btnsEl.querySelectorAll(".pager-num"), function (b) {
        b.addEventListener("click", function () { onGo(parseInt(b.dataset.page, 10)); });
      });
    }
    if (cfg.jumpInput) {
      cfg.jumpInput.value = page;
      cfg.jumpInput.max = totalPages;
    }
  }

  // 计算要显示的页码序列（最多 7 个，当前页居中，两端带省略号）。
  function pageNums(cur, total) {
    if (total <= 7) {
      var all = [];
      for (var i = 1; i <= total; i++) all.push(i);
      return all;
    }
    var nums = [1, total];
    var start = Math.max(2, cur - 2);
    var end = Math.min(total - 1, cur + 2);
    for (var p = start; p <= end; p++) nums.push(p);
    nums.sort(function (a, b) { return a - b; });
    return nums.filter(function (v, idx, arr) { return idx === 0 || v !== arr[idx - 1]; });
  }

  // 通用跳转绑定：回车或点击「跳转」跳到指定页。
  function bindPagerJump(jumpInput, jumpBtn, onGo, maxPages) {
    function doJump() {
      var v = parseInt(jumpInput.value, 10);
      if (isNaN(v) || v < 1) return;
      if (maxPages && v > maxPages) v = maxPages;
      jumpInput.value = v;
      onGo(v);
    }
    if (jumpBtn) jumpBtn.addEventListener("click", doJump);
    if (jumpInput) {
      jumpInput.addEventListener("keydown", function (e) { if (e.key === "Enter") doJump(); });
    }
  }

  // 更新出图记录翻页控件。
  function updateRecPager() {
    if (!els.recPager) return;
    var totalPages = state.recTotal ? Math.ceil(state.recTotal / state.recPageSize) : 1;
    if (state.recPage > totalPages) state.recPage = totalPages || 1;
    if (state.recPage < 1) state.recPage = 1;
    els.recPager.hidden = state.recTotal === 0;
    renderPager({
      firstBtn: els.recFirstBtn, prevBtn: els.recPrevBtn, btnsEl: els.recPageBtns,
      nextBtn: els.recNextBtn, lastBtn: els.recLastBtn, infoEl: els.recPageInfo,
      jumpInput: els.recJumpInput, jumpBtn: els.recJumpBtn,
      page: state.recPage, totalPages: totalPages,
      onGo: function (p) { loadRecordsPage(p, false); }
    });
  }

  function renderRecords() {
    var rows = state.records;
    if (!rows.length) {
      els.recBody.innerHTML = '<tr><td colspan="10" class="empty">没有匹配的出图记录</td></tr>';
      return;
    }
    els.recBody.innerHTML = rows.map(function (r) {
      var isFail = Number(r.status) === 1;
      var recSha = r.sha || r.sha256 || "";
      var canOpen = (recSha && r.ext !== "fail");
      var refSha = r.ref_sha256 || (r.ref && r.ref.sha256) || "";
      var i2iBadge = (r.is_img2img || refSha)
        ? '<span class="badge i2i" title="图生图">图生图</span>'
        : "";
      var refThumb = refSha
        ? '<img class="rec-thumb ref" src="' + THUMB_PLACEHOLDER + '" data-sha="' + escapeHtml(refSha) + '" data-open="' + escapeHtml(recSha) + '" data-refsha="' + escapeHtml(refSha) + '" alt="参考图" title="点击并排查看（结果 + 参考图）" loading="lazy" />'
        : "";
      var thumb = canOpen
        ? '<div class="rec-thumb-pair">' +
            '<img class="rec-thumb" src="' + THUMB_PLACEHOLDER + '" data-sha="' + escapeHtml(recSha) + '" data-open="' + escapeHtml(recSha) + '" alt="预览" title="点击查看大图" loading="lazy" />' +
            refThumb +
          '</div>' + i2iBadge
        : '<div class="rec-thumb empty-thumb">' + (isFail ? "失败" : "—") + '</div>';
      var t = (r.created_at ? new Date(Number(r.created_at) * 1000) : null);
      var time = t ? t.toLocaleString("zh-CN", { hour12: false }) : "—";
      var wh = (r.w && r.h) ? (r.w + "×" + r.h) : "—";
      var size = (r.size_bytes != null) ? fmtSize(r.size_bytes) : (isFail ? "—" : "—");
      var cost = (r.cost_sec != null) ? (Number(r.cost_sec).toFixed(1) + "s") : "—";
      var user = [r.user_name, r.user_id].filter(Boolean).join(" · ") || "—";
      var prompt = (r.prompt_raw || r.prompt || "").slice(0, 60);
      var status = isFail
        ? '<span class="badge fail">失败</span>'
        : (Number(r.deleted) === 1
            ? '<span class="badge warn">已删(回收站)</span>'
            : '<span class="badge ok">成功</span>');
      var msg = (r.trigger_msg || "").slice(0, 40);
      var wfName = (r.workflow || (r.wf && r.wf.name) || "").slice(0, 24);
      return '<tr>' +
        '<td>' + thumb + '</td>' +
        '<td>' + escapeHtml(time) + '</td>' +
        '<td>' + escapeHtml(user) + '</td>' +
        '<td>' + escapeHtml(msg) + '</td>' +
        '<td class="rec-wf">' + escapeHtml(wfName || "—") + '</td>' +
        '<td>' + escapeHtml(wh) + '</td>' +
        '<td>' + escapeHtml(size) + '</td>' +
        '<td>' + escapeHtml(cost) + '</td>' +
        '<td>' + status + '</td>' +
        '<td class="rec-prompt">' + escapeHtml(prompt) + '</td>' +
        '</tr>';
    }).join("");
    // 懒加载缩略图
    observeThumbs();
    Array.prototype.forEach.call(els.recBody.querySelectorAll("[data-open]"), function (img) {
      img.addEventListener("click", function () {
        var refSha = img.getAttribute("data-refsha");
        openImage(img.getAttribute("data-open"), refSha ? { refSha: refSha } : {});
      });
    });
  }

  function fmtSize(bytes) {
    bytes = Number(bytes) || 0;
    if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(2) + " MB";
    if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KB";
    return bytes + " B";
  }

  async function loadLogs() {
    var data;
    try {
      data = await apiGet("logs");
    } catch (e) {
      els.logContent.innerHTML = '<div class="empty error">读取日志失败：' + escapeHtml(e.message) + '</div>';
      throw e; // 让刷新逻辑能正确记录“日志”失败项
    }
    // 后端 apiGet 已被桥接解包为 data 本身：{ lines:[...], total:n }
    state.logs = (data && Array.isArray(data.lines)) ? data.lines : (Array.isArray(data) ? data : []);
    if (!state.logs.length) {
      els.logContent.innerHTML = '<div class="empty">暂无日志（产生绘图或其它运行日志后会自动出现）。</div>';
    }
    renderLogs();
    setStatus("日志已加载");
  }

  function renderLogs() {
    var level = els.logLevel.value;
    var query = els.logSearch.value.trim().toLowerCase();
    var filtered = state.logs.filter(function (line) {
      if (level === "WARN") return line.indexOf("WARN") >= 0 || line.indexOf("ERROR") >= 0;
      if (level === "ERROR") return line.indexOf("ERROR") >= 0;
      if (level === "INFO") return line.indexOf("INFO") >= 0;
      return true;
    });
    if (query) {
      filtered = filtered.filter(function (line) { return line.toLowerCase().indexOf(query) >= 0; });
    }
    els.logCount.textContent = filtered.length + " / " + state.logs.length + " 条";
    els.logContent.innerHTML = filtered.length
      ? filtered.map(function (line) {
          var cls = "log-line";
          if (line.indexOf("ERROR") >= 0 || line.indexOf("[ERROR]") >= 0) cls += " ERROR";
          else if (line.indexOf("WARN") >= 0 || line.indexOf("[WARN]") >= 0) cls += " WARN";
          else if (line.indexOf("INFO") >= 0 || line.indexOf("[INFO]") >= 0) cls += " INFO";
          return '<div class="' + cls + '">' + escapeHtml(line) + '</div>';
        }).join("")
      : '<div class="empty">没有匹配的日志</div>';
  }

  function setLogTab(name) {
    logTabState = name;
    var isRec = name === "records";
    els.logTabRecords.hidden = !isRec;
    els.logTabRunlog.hidden = isRec;
    document.querySelectorAll("#logTabs .tab").forEach(function (b) {
      b.classList.toggle("active", b.dataset.logtab === name);
    });
    if (isRec) {
      if (!state.records.length) loadRecords();
    } else {
      if (!state.logs.length) loadLogs();
    }
  }

  // ====== STATS ======
  var statsScope = "today";
  var statsMerge = false;

  function setStatsScope(scope) {
    statsScope = scope;
    document.querySelectorAll(".stats-scope .scope-tab").forEach(function (b) {
      b.classList.toggle("active", b.dataset.scope === scope);
    });
    loadStatsRanking();
  }

  async function loadStatsRanking() {
    var holder = els.statsRanking;
    if (!holder) return;
    holder.innerHTML = '<div class="empty">正在加载统计…</div>';
    try {
      var data = await apiGet("stats/ranking", { days: statsScope, merge: statsMerge ? "1" : "0" });
      state.statsRanking = data || { rows: [] };
      renderStatsRanking();
    } catch (e) {
      holder.innerHTML = '<div class="empty error">加载排行失败：' + escapeHtml(e.message) + '</div>';
    }
  }

  function renderStatsRanking() {
    var holder = els.statsRanking;
    if (!holder) return;
    var rows = (state.statsRanking.rows || []);
    var total = state.statsRanking.total || 0;
    if (!rows.length) {
      holder.innerHTML = '<div class="empty">暂无生图记录。生成图片后这里会出现排行。</div>';
      return;
    }
    var scopeLabel = { today: "今天", "3": "近 3 天", "7": "近 7 天", all: "全部" }[statsScope] || "全部";
    var maxCount = rows[0].count || 1;
    var html = '<div class="stats-meta">' + scopeLabel + ' 共生成 <b>' + total + '</b> 张图</div>';
    html += '<table class="stats-table"><thead><tr><th>排名</th><th>用户</th><th>QQ</th><th>数量</th><th>占比</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var pct = Math.round((r.count / maxCount) * 100);
      var ids = (Array.isArray(r.user_ids) && r.user_ids.length) ? r.user_ids : (r.user_id ? [r.user_id] : []);
      var uidHtml;
      if (!ids.length) {
        uidHtml = '<span class="uid">—</span>';
      } else if (ids.length <= 3) {
        uidHtml = '<span class="uid">' + ids.map(function (x) { return escapeHtml(x); }).join(", ") + '</span>';
      } else {
        var shown = ids.slice(0, 3).map(function (x) { return escapeHtml(x); }).join(", ");
        var countsJson = JSON.stringify(r.user_id_counts || {});
        uidHtml = '<span class="uid"><span class="uid-shown">' + shown + '</span>'
          + '<button type="button" class="uid-more" data-qids="' + escapeHtml(JSON.stringify(ids)) + '" data-qcounts="' + escapeHtml(countsJson) + '" data-qname="' + escapeHtml(r.user_name) + '">查看更多</button></span>';
      }
      html += '<tr><td class="rank">' + r.rank + '</td>'
        + '<td class="user">' + escapeHtml(r.user_name) + '</td>'
        + '<td class="uid-cell">' + uidHtml + '</td>'
        + '<td class="count">' + r.count + '</td>'
        + '<td class="bar-cell"><div class="bar"><i style="width:' + pct + '%"></i></div><span>' + pct + '%</span></td></tr>';
    });
    html += '</tbody></table>';
    holder.innerHTML = html;
    // 绑定「查看更多」按钮
    holder.querySelectorAll(".uid-more").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var ids = [];
        var counts = {};
        try { ids = JSON.parse(btn.dataset.qids); } catch (e) { ids = []; }
        try { counts = JSON.parse(btn.dataset.qcounts || "{}"); } catch (e) { counts = {}; }
        openQqList(btn.dataset.qname || "QQ 列表", ids, counts);
      });
    });
  }

  // 打开 QQ 列表弹窗
  function openQqList(title, ids, counts) {
    if (!els.qqListDialog) return;
    els.qqListTitle.textContent = title + " 的 QQ 列表";
    var rowsHtml = "";
    ids.forEach(function (id) {
      var c = (counts && Object.prototype.hasOwnProperty.call(counts, id)) ? counts[id] : "";
      rowsHtml += '<div class="qq-list-row"><span class="qq-list-id">' + escapeHtml(id) + '</span>'
        + '<span class="qq-list-count">' + (c === "" ? "" : c + " 张") + '</span></div>';
    });
    els.qqListBody.innerHTML = rowsHtml || '<div class="empty">无记录</div>';
    els.qqListDialog.showModal();
  }

  async function loadStatsTrend() {
    var holder = els.statsTrendChart;
    if (!holder) return;
    holder.innerHTML = '<div class="empty">正在加载趋势…</div>';
    try {
      var data = await apiGet("stats/trend", { hours: 24 });
      state.statsTrend = data || { buckets: [] };
      renderStatsTrend();
    } catch (e) {
      holder.innerHTML = '<div class="empty error">加载趋势失败：' + escapeHtml(e.message) + '</div>';
      if (els.statsTrendInfo) els.statsTrendInfo.textContent = "";
    }
  }

  function renderStatsTrend() {
    var holder = els.statsTrendChart;
    if (!holder) return;
    var buckets = state.statsTrend.buckets || [];
    if (!buckets.length) {
      holder.innerHTML = '<div class="empty">暂无趋势数据。</div>';
      if (els.statsTrendInfo) els.statsTrendInfo.textContent = "";
      return;
    }
    var maxCount = 1;
    var sum = 0;
    buckets.forEach(function (b) { if (b.count > maxCount) maxCount = b.count; sum += b.count; });
    if (els.statsTrendInfo) els.statsTrendInfo.textContent = "共 " + sum + " 张";
    // 面积图：用 SVG 折线 + 渐变填充
    var W = 860, H = 220, PAD = { l: 34, r: 10, t: 14, b: 26 };
    var n = buckets.length;
    var iw = W - PAD.l - PAD.r;
    var ih = H - PAD.t - PAD.b;
    var stepX = n > 1 ? iw / (n - 1) : iw;
    var pts = buckets.map(function (b, i) {
      var x = PAD.l + i * stepX;
      var y = PAD.t + ih - (b.count / maxCount) * ih;
      return { x: x, y: y, b: b };
    });
    var line = pts.map(function (p, i) { return (i ? " L" : "M") + p.x.toFixed(1) + " " + p.y.toFixed(1); }).join("");
    var area = line + " L" + (PAD.l + (n - 1) * stepX).toFixed(1) + " " + (PAD.t + ih) + " L" + PAD.l + " " + (PAD.t + ih) + " Z";
    // X 轴刻度：最多显示 12 个（hour 已是 HH:00，无日期）
    var tickEvery = Math.max(1, Math.ceil(n / 12));
    var ticks = "";
    for (var i = 0; i < n; i += tickEvery) {
      var tx = PAD.l + i * stepX;
      var ty = PAD.t + ih + 16;
      ticks += '<text x="' + tx.toFixed(1) + '" y="' + ty + '" text-anchor="middle">' + escapeHtml(buckets[i].hour) + '</text>';
    }
    // Y 轴刻度：0 / 中 / 最大
    var yticks = "";
    [0, 0.5, 1].forEach(function (f) {
      var yy = PAD.t + ih - f * ih;
      var val = Math.round(f * maxCount);
      yticks += '<text x="' + (PAD.l - 6) + '" y="' + (yy + 4).toFixed(1) + '" text-anchor="end">' + val + '</text>';
      yticks += '<line x1="' + PAD.l + '" y1="' + yy.toFixed(1) + '" x2="' + (W - PAD.r) + '" y2="' + yy.toFixed(1) + '" class="grid"/>';
    });
    // 数据点：全部带悬浮 title（HH:00 - N 张）；非 0 的点上方显示数量文本
    var dots = "";
    var labels = "";
    pts.forEach(function (p) {
      var t = '<title>' + escapeHtml(p.b.hour + " - " + p.b.count + " 张") + '</title>';
      dots += '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="3" class="dot">' + t + '</circle>';
      if (p.b.count > 0) {
        labels += '<text x="' + p.x.toFixed(1) + '" y="' + (p.y - 8).toFixed(1) + '" text-anchor="middle" class="dot-label">' + p.b.count + '</text>';
      }
    });
    var svg = '<svg class="trend-svg" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="近一天生图数量面积图">'
      + '<defs><linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">'
      + '<stop offset="0%" stop-color="var(--accent, #8b5cf6)" stop-opacity="0.45"/>'
      + '<stop offset="100%" stop-color="var(--accent, #8b5cf6)" stop-opacity="0.05"/>'
      + '</linearGradient></defs>'
      + '<g class="y-axis">' + yticks + '</g>'
      + '<path d="' + area + '" fill="url(#trendFill)"/>'
      + '<path d="' + line + '" fill="none" stroke="var(--accent, #8b5cf6)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
      + '<g class="x-axis">' + ticks + '</g>'
      + '<g class="dots">' + dots + '</g>'
      + '<g class="dot-labels">' + labels + '</g>'
      + '</svg>';
    holder.innerHTML = svg;
  }

  if (els.statsRefreshBtn) {
    els.statsRefreshBtn.addEventListener("click", function () {
      setButtonBusy(els.statsRefreshBtn, true, "刷新中…", "刷新");
      Promise.all([loadStatsRanking(), loadStatsTrend()])
        .catch(function () {})
        .then(function () {
          setButtonBusy(els.statsRefreshBtn, false, "刷新中…", "刷新");
        });
    });
  }
  document.querySelectorAll(".stats-scope .scope-tab").forEach(function (b) {
    b.addEventListener("click", function () { setStatsScope(b.dataset.scope); });
  });
  if (els.statsMergeBtn) {
    statsMerge = els.statsMergeBtn.checked;
    els.statsMergeBtn.addEventListener("change", function () {
      statsMerge = els.statsMergeBtn.checked;
      loadStatsRanking();
    });
  }

  // ====== 工作流卡片视图 ======
  function renderWorkflows() {
    var holder = els.workflowsGrid;
    if (!holder) return;
    var wfs = (state.config && Array.isArray(state.config.workflows)) ? state.config.workflows : null;
    if (!wfs) {
      holder.innerHTML = '<div class="empty">正在加载工作流…</div>';
      return;
    }
    if (!wfs.length) {
      holder.innerHTML = '<div class="workflows-toolbar"><button id="workflowsAddBtn" type="button">+ 新增工作流</button></div><div class="empty">尚未配置任何工作流，点「新增工作流」添加。</div>';
      return;
    }
    var loras = (state.config && Array.isArray(state.config.loras)) ? state.config.loras : [];
    var html = '<div class="workflows-toolbar"><button id="workflowsAddBtn" type="button">+ 新增工作流</button></div><div class="workflows-card-grid">';
    wfs.forEach(function (w, idx) {
      var name = (w.name || "");
      var aliases = (w.aliases || "").split(/[,，\n]/).map(function (s) { return s.trim(); }).filter(Boolean).join(" / ") || "—";
      var bm = (w.base_model || "").trim() || "不限底模";
      var srv = (w.server_name || "").trim() || "默认服务器";
      var isAnima = !!w.is_anima;
      var wfName = (w.workflow_name || "").trim() || "";
      // 可用 LoRA：底模匹配的（复用与后端一致的口径：工作流/ LoRA 底模任一为空=通用）
      var avail = loras.filter(function (l) {
        var wbm = (w.base_model || "").trim().toLowerCase();
        var lbm = (l.base_model || "").trim().toLowerCase();
        return !wbm || !lbm || wbm === lbm;
      }).map(function (l) { return (l.name || ""); }).filter(Boolean);
      var availStr = avail.length ? avail.slice(0, 6).join("、") + (avail.length > 6 ? " …" : "") : "无匹配 LoRA";
      var loraCfg = (w.loras_text || "").trim() ? "已配默认 LoRA" : "未配默认 LoRA";
      var wImg = (w.image || "").trim() || "";
      var wCiv = (w.civitai_url || "").trim() || "";
      html += '<div class="wf-card" data-idx="' + idx + '">'
        + '<div class="lora-cover-wrap"><button type="button" class="lora-cover-btn wf-cover-btn" data-img="' + escapeHtml(wImg) + '" data-idx="' + idx + '" title="点击查看大图">' + loraCoverHtml(name, wImg) + '</button></div>'
        + '<div class="wf-card-head"><span class="wf-card-title">' + escapeHtml(name) + '</span>'
        + (isAnima ? '<span class="wf-badge anima">Anima</span>' : '')
        + '</div>'
        + '<div class="wf-card-alias">别名：' + escapeHtml(aliases) + '</div>'
        + '<div class="wf-card-meta">'
        + '<span class="lora-badge">' + escapeHtml(bm) + '</span>'
        + '<span class="wf-srv">' + escapeHtml(srv) + '</span>'
        + (wfName ? '<span class="wf-file">' + escapeHtml(wfName) + '</span>' : '')
        + (wCiv ? '<a class="lora-civ-link" href="' + escapeHtml(wCiv) + '" target="_blank" rel="noopener noreferrer">C站 ↗</a>' : '')
        + '</div>'
        + '<div class="wf-card-loracfg">' + escapeHtml(loraCfg) + '</div>'
        + '<div class="wf-card-avail">可用 LoRA：' + escapeHtml(availStr) + '</div>'
        + '<div class="wf-card-actions">'
        + '<button type="button" class="wf-edit" data-idx="' + idx + '">编辑</button>'
        + '<button type="button" class="wf-copy" data-idx="' + idx + '" title="复制该工作流创建新工作流">复制</button>'
        + '<button type="button" class="wf-cover-fetch" data-idx="' + idx + '" title="从 C 站抓取封面">抓封面</button>'
        + '<button type="button" class="wf-cover-upload" data-idx="' + idx + '" title="上传封面图片">传封面</button>'
        + '<button type="button" class="wf-del danger" data-idx="' + idx + '">删除</button>'
        + '</div>'
        + '</div>';
    });
    html += '</div>';
    holder.innerHTML = html;
    holder.querySelectorAll(".wf-edit").forEach(function (btn) {
      btn.addEventListener("click", function () { openWorkflowEditor(+btn.dataset.idx); });
    });
    holder.querySelectorAll(".wf-copy").forEach(function (btn) {
      btn.addEventListener("click", function () { copyWorkflow(+btn.dataset.idx); });
    });
    holder.querySelectorAll(".wf-del").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var wfs = state.config.workflows;
        var w = wfs[+btn.dataset.idx] || {};
        var ok = await confirmAction("删除工作流", "确定要删除工作流「" + (w.name || "") + "」吗？此操作不可恢复！");
        if (!ok) return;
        wfs.splice(+btn.dataset.idx, 1);
        apiPost("config", { config: { workflows: wfs } }).then(function () {
          showToast("工作流已删除", "success");
          renderWorkflows();
        }).catch(function (e) { showToast(e.message || "删除失败", "error"); });
      });
    });
    // 工作流封面：加载图（带缓存）/ 看大图 / 抓封面 / 传封面
    holder.querySelectorAll("[data-lora-img]").forEach(function (img) {
      loadCover(img, img.dataset.loraImg);
    });
    holder.querySelectorAll(".wf-cover-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var fname = btn.dataset.img || "";
        if (!fname) { showToast("该工作流没有封面图，可先上传或抓取封面", "info"); return; }
        openLoraImage(fname);
      });
    });
    holder.querySelectorAll(".wf-cover-fetch").forEach(function (btn) {
      btn.addEventListener("click", function () { fetchWorkflowCover(btn); });
    });
    holder.querySelectorAll(".wf-cover-upload").forEach(function (btn) {
      btn.addEventListener("click", function () { uploadWorkflowCover(btn); });
    });
  }

  if (els.workflowsRefreshBtn) {
    els.workflowsRefreshBtn.addEventListener("click", function () {
      loadConfig().then(function () { renderWorkflows(); });
    });
  }

  // ====== LoRA 卡片视图 ======
  function loraCoverHtml(name, img) {
    if (img) {
      return '<div class="lora-cover"><img data-lora-img="' + escapeHtml(img) + '" alt="" loading="lazy"></div>';
    }
    return '<div class="lora-cover lora-cover-empty"><span>无封面</span></div>';
  }

  // 加载封面：命中缓存直接用，否则请求后端并写入缓存。
  function loadCover(img, fname) {
    if (!img || !fname) return;
    if (coverCache[fname]) {
      img.src = coverCache[fname];
      return;
    }
    apiGet("lora/image", { name: fname }).then(function (d) {
      if (d && d.url) {
        coverCache[fname] = d.url;
        img.src = d.url;
      }
    }).catch(function () {});
  }

  function renderLoras() {
    var holder = els.lorasGrid;
    if (!holder) return;
    var loras = (state.config && Array.isArray(state.config.loras)) ? state.config.loras : null;
    if (!loras) {
      holder.innerHTML = '<div class="empty">正在加载 LoRA 库…</div>';
      return;
    }
    if (!loras.length) {
      holder.innerHTML = '<div class="loras-toolbar"><button id="lorasAddBtn" type="button">+ 新增 LoRA</button></div><div class="empty">尚未配置任何 LoRA，点「新增 LoRA」添加。</div>';
      return;
    }
    var html = '<div class="loras-toolbar"><button id="lorasAddBtn" type="button">+ 新增 LoRA</button></div><div class="loras-card-grid">';
    loras.forEach(function (l, idx) {
      var name = (l.name || "");
      var aliases = (l.keywords || "").split(/[,，\n\r]+/).map(function (s) { return s.trim(); }).filter(Boolean);
      var aliasFirst = aliases.length ? aliases[0] : "—";
      var bm = (l.base_model || "").trim() || "通用";
      var tw = (l.trigger_words || "").trim() || "";
      var desc = (l.description || "").trim() || "";
      var img = (l.image || "").trim() || "";
      var cUrl = (l.civitai_url || "").trim() || "";
      html += '<div class="lora-card" data-idx="' + idx + '">'
        + '<div class="lora-cover-wrap"><button type="button" class="lora-cover-btn" data-img="' + escapeHtml(img) + '" data-idx="' + idx + '" title="点击查看大图">' + loraCoverHtml(name, img) + '</button></div>'
        + '<div class="lora-card-body">'
        + '<div class="lora-card-title">' + escapeHtml(name) + '</div>'
        + '<div class="lora-card-alias">别名：' + escapeHtml(aliasFirst) + (aliases.length > 1 ? ' <span class="alias-more">+' + (aliases.length - 1) + '</span>' : '') + '</div>'
        + '<div class="lora-card-meta"><span class="lora-badge">' + escapeHtml(bm) + '</span>'
        + (cUrl ? '<a class="lora-civ-link" href="' + escapeHtml(cUrl) + '" target="_blank" rel="noopener noreferrer">C站 ↗</a>' : '')
        + '</div>'
        + '<div class="lora-card-actions">'
        + '<button type="button" class="lora-detail" data-idx="' + idx + '">详情</button>'
        + '<button type="button" class="lora-edit" data-idx="' + idx + '">编辑</button>'
        + '<button type="button" class="lora-fetch" data-idx="' + idx + '">抓取</button>'
        + '<button type="button" class="lora-upload" data-idx="' + idx + '">上传封面</button>'
        + '<button type="button" class="lora-del danger" data-idx="' + idx + '">删除</button>'
        + '</div></div></div>';
    });
    html += '</div>';
    holder.innerHTML = html;
    // 加载封面图（带缓存，避免保存/重渲染时反复请求）
    holder.querySelectorAll("[data-lora-img]").forEach(function (img) {
      loadCover(img, img.dataset.loraImg);
    });
    // 事件
    holder.querySelectorAll(".lora-cover-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var fname = btn.dataset.img || "";
        if (!fname) {
          showToast("该 LoRA 没有封面图，可先上传封面", "info");
          return;
        }
        openLoraImage(fname);
      });
    });
    holder.querySelectorAll(".lora-detail").forEach(function (btn) {
      btn.addEventListener("click", function () { openLoraDetail(+btn.dataset.idx); });
    });
    holder.querySelectorAll(".lora-edit").forEach(function (btn) {
      btn.addEventListener("click", function () { openLoraEditor(+btn.dataset.idx); });
    });
    holder.querySelectorAll(".lora-fetch").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var l = state.config.loras[+btn.dataset.idx] || {};
        var cur = (l.civitai_url || "").trim();
        if (!cur) {
          showToast("该 LoRA 尚未配置 C 站链接，请先在编辑中填写链接后再抓取", "info");
          return;
        }
        fetchLoraRemote(btn, cur);
      });
    });
    holder.querySelectorAll(".lora-upload").forEach(function (btn) {
      btn.addEventListener("click", function () { uploadLoraCover(btn); });
    });
    holder.querySelectorAll(".lora-del").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var loras = state.config.loras;
        var l = loras[+btn.dataset.idx] || {};
        var ok = await confirmAction("删除 LoRA", "确定要删除 LoRA「" + (l.name || "") + "」吗？此操作不可恢复！");
        if (!ok) return;
        loras.splice(+btn.dataset.idx, 1);
        saveLorasState().then(function () {
          showToast("LoRA 已删除", "success");
          renderLoras();
        }).catch(function (e) { showToast(e.message || "删除失败", "error"); });
      });
    });
  }

  // ====== 生图限额（配额） ======
  async function loadQuota() {
    try {
      var d = await apiGet("quota/users");
      state.quota = d || { global: {}, users: [] };
      renderQuotaGlobal();
      renderQuotaTable();
    } catch (e) {
      var holder = els.quotaGlobal;
      if (holder) holder.innerHTML = '<div class="empty error">读取限额数据失败：' + escapeHtml(e.message || e) + '</div>';
    }
  }

  function fmtQuota(val) {
    return (val === null || val === undefined) ? "—" : (val === -1 ? "不限" : String(val));
  }

  function renderQuotaGlobal() {
    var holder = els.quotaGlobal;
    if (!holder) return;
    var g = state.quota.global || {};
    var enabled = !!g.enabled;
    var html = '<div class="quota-global-card">'
      + '<div class="quota-global-title">全局默认限额</div>'
      + '<div class="quota-global-field"><label>总次数上限</label><input type="number" class="quota-input qg-input" id="qgMaxTotal" value="' + g.max_total + '" min="-1" title="-1 表示不限制" /></div>'
      + '<div class="quota-global-field"><label>每小时上限</label><input type="number" class="quota-input qg-input" id="qgMaxHour" value="' + g.max_hour + '" min="-1" title="-1 表示不限制" /></div>'
      + '<div class="quota-global-field"><label>每天上限</label><input type="number" class="quota-input qg-input" id="qgMaxDay" value="' + g.max_day + '" min="-1" title="-1 表示不限制" /></div>'
      + '<div class="quota-global-field"><label>管理员豁免</label><select id="qgAdminExempt" class="quota-input"><option value="1"' + (g.admin_exempt ? " selected" : "") + '>是</option><option value="0"' + (!g.admin_exempt ? " selected" : "") + '>否</option></select></div>'
      + '<div class="quota-global-field"><label>限制开关</label><select id="qgEnabled" class="quota-input"><option value="1"' + (enabled ? " selected" : "") + '>已启用</option><option value="0"' + (!enabled ? " selected" : "") + '>未启用</option></select></div>'
      + '<div class="quota-global-actions"><button type="button" id="qgSaveBtn" class="quota-save">保存全局限额</button>'
      + '<span class="quota-global-note">未单独配置的用户使用这里的全局值；每天次数在本地时区 0 点自动重置。限制开关未启用时仅记录用量、不拦截生图。</span></div>'
      + '</div>';
    holder.innerHTML = html;
    var saveBtn = document.getElementById("qgSaveBtn");
    if (saveBtn) saveBtn.addEventListener("click", saveQuotaGlobal);
  }

  async function saveQuotaGlobal() {
    function num(id) { var v = parseInt(document.getElementById(id).value, 10); return isNaN(v) ? -1 : v; }
    var payload = {
      max_total: num("qgMaxTotal"),
      max_hour: num("qgMaxHour"),
      max_day: num("qgMaxDay"),
      admin_exempt: document.getElementById("qgAdminExempt").value === "1",
      enabled: document.getElementById("qgEnabled").value === "1"
    };
    var btn = document.getElementById("qgSaveBtn");
    setButtonBusy(btn, true, "保存中…", "保存全局限额");
    try {
      var r = await apiPost("quota/save_global", payload);
      if (!r) throw new Error("无响应");
      if (r.error) throw new Error(r.error);
      showToast("已保存全局限额", "success");
      await loadQuota();
    } catch (e) {
      showToast("保存失败：" + (e.message || e), "error");
      setButtonBusy(btn, false, "保存中…", "保存全局限额");
    }
  }

  function renderQuotaTable() {
    var body = els.quotaBody;
    var empty = els.quotaEmpty;
    var count = els.quotaCount;
    if (!body) return;
    var users = (state.quota.users || []).filter(function (u) { return u && (u.user_id || ""); });
    if (count) count.textContent = users.length ? "共 " + users.length + " 个用户" : "";
    if (!users.length) {
      body.innerHTML = "";
      if (empty) empty.style.display = "block";
      return;
    }
    if (empty) empty.style.display = "none";
    var html = "";
    users.forEach(function (u) {
      var fromGlobal = (u.max_total === null || u.max_total === undefined)
        && (u.max_hour === null || u.max_hour === undefined)
        && (u.max_day === null || u.max_day === undefined);
      var mt = (u.max_total === null || u.max_total === undefined) ? state.quota.global.max_total : u.max_total;
      var mh = (u.max_hour === null || u.max_hour === undefined) ? state.quota.global.max_hour : u.max_hour;
      var md = (u.max_day === null || u.max_day === undefined) ? state.quota.global.max_day : u.max_day;
      html += '<tr>'
        + '<td>' + escapeHtml(u.user_name || "（未记录）") + '</td>'
        + '<td>' + escapeHtml(u.user_id || "—") + '</td>'
        + '<td>' + u.total_used + '</td>'
        + '<td>' + u.hour_used + '</td>'
        + '<td>' + u.day_used + '</td>'
        + '<td><input type="number" class="quota-input" data-uid="' + escapeHtml(u.user_id) + '" data-field="max_total" value="' + mt + '" min="-1" title="-1 表示不限制" /></td>'
        + '<td><input type="number" class="quota-input" data-uid="' + escapeHtml(u.user_id) + '" data-field="max_hour" value="' + mh + '" min="-1" title="-1 表示不限制" /></td>'
        + '<td><input type="number" class="quota-input" data-uid="' + escapeHtml(u.user_id) + '" data-field="max_day" value="' + md + '" min="-1" title="-1 表示不限制" /></td>'
        + '<td class="quota-actions">'
        + '<button type="button" class="quota-save" data-uid="' + escapeHtml(u.user_id) + '" data-global="' + (fromGlobal ? "1" : "0") + '">保存</button>'
        + '<button type="button" class="quota-reset danger-ghost" data-uid="' + escapeHtml(u.user_id) + '">重置次数</button>'
        + '</td>'
        + '</tr>';
    });
    body.innerHTML = html;
  }

  async function saveQuotaConfig(btn) {
    var uid = btn.dataset.uid || "";
    if (!uid) return;
    var maxTotal = -1, maxHour = -1, maxDay = -1;
    document.querySelectorAll(".quota-input[data-uid=\"" + CSS.escape(uid) + "\"]").forEach(function (inp) {
      var v = parseInt(inp.value, 10);
      if (isNaN(v)) v = -1;
      if (inp.dataset.field === "max_total") maxTotal = v;
      if (inp.dataset.field === "max_hour") maxHour = v;
      if (inp.dataset.field === "max_day") maxDay = v;
    });
    setButtonBusy(btn, true, "保存中…", "保存");
    try {
      var r = await apiPost("quota/config", { user_id: uid, max_total: maxTotal, max_hour: maxHour, max_day: maxDay });
      if (!r) throw new Error("无响应");
      if (r.error) throw new Error(r.error);
      showToast("已保存用户 " + uid + " 的限额", "success");
      await loadQuota();
    } catch (e) {
      showToast("保存失败：" + (e.message || e), "error");
    } finally {
      setButtonBusy(btn, false, "保存中…", "保存");
    }
  }

  async function resetQuotaUser(btn) {
    var uid = btn.dataset.uid || "";
    var name = (btn.closest("tr") && btn.closest("tr").cells[0]) ? btn.closest("tr").cells[0].textContent : uid;
    var ok = await confirmAction("重置生图次数", "确定要重置用户「" + name + "」的生图次数吗？总次数与当前小时次数都将清零。");
    if (!ok) return;
    setButtonBusy(btn, true, "重置中…", "重置次数");
    try {
      var r = await apiPost("quota/reset", { user_id: uid });
      if (!r) throw new Error("无响应");
      if (r.error) throw new Error(r.error);
      showToast("已重置用户 " + uid + " 的生图次数", "success");
      await loadQuota();
    } catch (e) {
      showToast("重置失败：" + (e.message || e), "error");
    } finally {
      setButtonBusy(btn, false, "重置中…", "重置次数");
    }
  }

  // ---- LLM token 用量统计 ----
  var tokenScope = "30"; // today / 1 / 3 / 7 / 30 / 90 / all

  function tokenScopeValue() {
    if (tokenScope === "today") return 1;
    if (tokenScope === "all") return -1; // 全部历史
    var n = parseInt(tokenScope, 10);
    return (n && n > 0) ? n : 30;
  }

  function setTokenScope(scope) {
    tokenScope = scope;
    if (els.tokenScope) {
      els.tokenScope.querySelectorAll(".scope-tab").forEach(function (b) {
        b.classList.toggle("active", b.dataset.tokenScope === scope);
      });
    }
    loadToken();
  }

  function tokenScopeLabel() {
    var map = { today: "今天", "1": "近 1 天", "3": "近 3 天", "7": "近 7 天", "30": "近 30 天", "90": "近 90 天", all: "全部" };
    return map[tokenScope] || "近 30 天";
  }

  function fmtTokenNumber(val) {
    if (val === null || val === undefined) return "—";
    var n = Number(val) || 0;
    if (n >= 1000000000) return (n / 1000000000).toFixed(2) + "B";
    if (n >= 1000000) return (n / 1000000).toFixed(2) + "M";
    if (n >= 10000) return (n / 1000).toFixed(1) + "k";
    return String(n);
  }

  function sceneLabel(scene) {
    var map = {
      translate: "翻译",
      rewrite_anima: "动漫改写",
      rewrite_real: "写实清理",
      extract_args: "参数提取",
    };
    return map[scene] || escapeHtml(scene);
  }

  async function loadToken() {
    var holder = els.tokenCards;
    if (holder) holder.innerHTML = '<div class="empty">正在加载 token 统计…</div>';
    if (els.tokenTrendChart) els.tokenTrendChart.innerHTML = '<div class="empty">正在加载趋势…</div>';
    try {
      var days = tokenScopeValue();
      var d = await apiGet("token/summary", { days: days });
      if (!d) throw new Error("无响应");
      state.token = d;
      state.token.days = days;
      renderTokenCards();
      renderTokenTrend();
      renderTokenScenes();
      renderTokenModels();
      renderTokenUsers();
      renderTokenDetail();
    } catch (e) {
      if (holder) holder.innerHTML = '<div class="empty error">读取 token 统计失败：' + escapeHtml(e.message || e) + '</div>';
    }
  }

  function renderTokenCards() {
    var holder = els.tokenCards;
    if (!holder) return;
    var s = state.token.summary || {};
    var cards = [
      { label: "非缓存输入", value: fmtTokenNumber(s.input_other), sub: s.input_other === 0 ? "" : String(s.input_other || 0) },
      { label: "缓存命中", value: fmtTokenNumber(s.input_cached), sub: s.input_cached === 0 ? "" : String(s.input_cached || 0) },
      { label: "输出", value: fmtTokenNumber(s.output), sub: s.output === 0 ? "" : String(s.output || 0) },
      { label: "Token 合计", value: fmtTokenNumber(s.total), sub: String(s.total || 0), strong: true },
      { label: "调用次数", value: fmtTokenNumber(s.call_count), sub: String(s.call_count || 0) },
      { label: "使用模型数", value: fmtTokenNumber(s.model_count), sub: tokenScopeLabel() },
    ];
    holder.innerHTML = cards.map(function (c) {
      return '<div class="stat-card' + (c.strong ? ' strong' : '') + '">'
        + '<span class="stat-value">' + escapeHtml(c.value) + '</span>'
        + '<span class="stat-label">' + escapeHtml(c.label) + '</span>'
        + (c.sub ? '<span class="stat-sub">' + escapeHtml(c.sub) + '</span>' : '')
        + '</div>';
    }).join("");
  }

  // 每日 token 消耗面积图（SVG 折线 + 渐变填充），参照统计页趋势图
  function renderTokenTrend() {
    var holder = els.tokenTrendChart;
    if (!holder) return;
    var buckets = state.token.daily || [];
    if (!buckets.length) {
      holder.innerHTML = '<div class="empty">暂无趋势数据。</div>';
      if (els.tokenTrendInfo) els.tokenTrendInfo.textContent = "";
      return;
    }
    var total = 0;
    var maxVal = 1;
    buckets.forEach(function (b) { if (b.total > maxVal) maxVal = b.total; total += b.total; });
    if (els.tokenTrendInfo) els.tokenTrendInfo.textContent = tokenScopeLabel() + " 共 " + fmtTokenNumber(total) + " tokens";
    var W = 860, H = 220, PAD = { l: 34, r: 10, t: 14, b: 26 };
    var n = buckets.length;
    var iw = W - PAD.l - PAD.r;
    var ih = H - PAD.t - PAD.b;
    var stepX = n > 1 ? iw / (n - 1) : iw;
    var pts = buckets.map(function (b, i) {
      var x = PAD.l + i * stepX;
      var y = PAD.t + ih - (b.total / maxVal) * ih;
      return { x: x, y: y, b: b };
    });
    var line = pts.map(function (p, i) { return (i ? " L" : "M") + p.x.toFixed(1) + " " + p.y.toFixed(1); }).join("");
    var area = line + " L" + (PAD.l + (n - 1) * stepX).toFixed(1) + " " + (PAD.t + ih) + " L" + PAD.l + " " + (PAD.t + ih) + " Z";
    var tickEvery = Math.max(1, Math.ceil(n / 12));
    var ticks = "";
    for (var i = 0; i < n; i += tickEvery) {
      var tx = PAD.l + i * stepX;
      ticks += '<text x="' + tx.toFixed(1) + '" y="' + (PAD.t + ih + 16) + '" text-anchor="middle">' + escapeHtml(buckets[i].day_bucket.slice(5)) + '</text>';
    }
    var yticks = "";
    [0, 0.5, 1].forEach(function (f) {
      var yy = PAD.t + ih - f * ih;
      var val = Math.round(f * maxVal);
      yticks += '<text x="' + (PAD.l - 6) + '" y="' + (yy + 4).toFixed(1) + '" text-anchor="end">' + fmtTokenNumber(val) + '</text>';
      yticks += '<line x1="' + PAD.l + '" y1="' + yy.toFixed(1) + '" x2="' + (W - PAD.r) + '" y2="' + yy.toFixed(1) + '" class="grid"/>';
    });
    var dots = "";
    var labels = "";
    pts.forEach(function (p) {
      var t = '<title>' + escapeHtml(p.b.day_bucket + " - " + fmtTokenNumber(p.b.total) + " tokens") + '</title>';
      dots += '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="3" class="dot">' + t + '</circle>';
      if (p.b.total > 0) {
        labels += '<text x="' + p.x.toFixed(1) + '" y="' + (p.y - 8).toFixed(1) + '" text-anchor="middle" class="dot-label">' + fmtTokenNumber(p.b.total) + '</text>';
      }
    });
    var svg = '<svg class="trend-svg" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="每日 token 消耗面积图">'
      + '<defs><linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">'
      + '<stop offset="0%" stop-color="var(--accent, #8b5cf6)" stop-opacity="0.45"/>'
      + '<stop offset="100%" stop-color="var(--accent, #8b5cf6)" stop-opacity="0.05"/>'
      + '</linearGradient></defs>'
      + '<g class="y-axis">' + yticks + '</g>'
      + '<path d="' + area + '" fill="url(#trendFill)"/>'
      + '<path d="' + line + '" fill="none" stroke="var(--accent, #8b5cf6)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
      + '<g class="x-axis">' + ticks + '</g>'
      + '<g class="dots">' + dots + '</g>'
      + '<g class="dot-labels">' + labels + '</g>'
      + '</svg>';
    holder.innerHTML = svg;
  }

  // 带进度条占比的通用渲染：给表格行加「占比」列（bar 宽度按 maxTotal 归一）
  function tokenBarRow(r, extraTds) {
    var pct = Math.round((r.total / (state.token._maxTotal || 1)) * 100);
    return '<td class="bar-cell"><div class="bar"><i style="width:' + pct + '%"></i></div><span>' + pct + '%</span></td>';
  }

  function renderTokenScenes() {
    var body = els.tokenSceneBody;
    var empty = els.tokenSceneEmpty;
    if (!body) return;
    var rows = (state.token.scenes || []).filter(function (r) { return r && r.scene; });
    state.token._maxTotal = Math.max.apply(null, rows.map(function (r) { return r.total || 0; }).concat([0])) || 1;
    if (empty) empty.style.display = rows.length ? "none" : "block";
    body.innerHTML = rows.map(function (r) {
      return '<tr>'
        + '<td>' + sceneLabel(r.scene) + '</td>'
        + '<td>' + fmtTokenNumber(r.input_other) + '</td>'
        + '<td>' + fmtTokenNumber(r.input_cached) + '</td>'
        + '<td>' + fmtTokenNumber(r.output) + '</td>'
        + '<td class="num-strong">' + fmtTokenNumber(r.total) + '</td>'
        + '<td>' + fmtTokenNumber(r.call_count) + '</td>'
        + tokenBarRow(r)
        + '</tr>';
    }).join("");
  }

  function renderTokenModels() {
    var body = els.tokenModelBody;
    var empty = els.tokenModelEmpty;
    var count = els.tokenModelCount;
    if (!body) return;
    var rows = (state.token.models || []).filter(function (r) { return r && r.model; });
    if (count) count.textContent = rows.length ? "共 " + rows.length + " 个模型" : "";
    if (empty) empty.style.display = rows.length ? "none" : "block";
    var max = Math.max.apply(null, rows.map(function (r) { return r.total || 0; }).concat([0])) || 1;
    body.innerHTML = rows.map(function (r) {
      var pct = Math.round((r.total / max) * 100);
      return '<tr>'
        + '<td>' + escapeHtml(r.model) + '</td>'
        + '<td>' + fmtTokenNumber(r.input_other) + '</td>'
        + '<td>' + fmtTokenNumber(r.input_cached) + '</td>'
        + '<td>' + fmtTokenNumber(r.output) + '</td>'
        + '<td class="num-strong">' + fmtTokenNumber(r.total) + '</td>'
        + '<td>' + fmtTokenNumber(r.call_count) + '</td>'
        + '<td class="bar-cell"><div class="bar"><i style="width:' + pct + '%"></i></div><span>' + pct + '%</span></td>'
        + '</tr>';
    }).join("");
  }

  function renderTokenUsers() {
    var body = els.tokenUserBody;
    var empty = els.tokenUserEmpty;
    var count = els.tokenUserCount;
    if (!body) return;
    var rows = (state.token.users || []).filter(function (r) { return r && r.user_id; });
    if (count) count.textContent = rows.length ? "共 " + rows.length + " 个用户" : "";
    if (empty) empty.style.display = rows.length ? "none" : "block";
    var max = Math.max.apply(null, rows.map(function (r) { return r.total || 0; }).concat([0])) || 1;
    body.innerHTML = rows.map(function (r) {
      var uid = escapeHtml(r.user_id);
      var pct = Math.round((r.total / max) * 100);
      return '<tr>'
        + '<td>' + uid + '</td>'
        + '<td>' + fmtTokenNumber(r.input_other) + '</td>'
        + '<td>' + fmtTokenNumber(r.input_cached) + '</td>'
        + '<td>' + fmtTokenNumber(r.output) + '</td>'
        + '<td class="num-strong">' + fmtTokenNumber(r.total) + '</td>'
        + '<td>' + fmtTokenNumber(r.call_count) + '</td>'
        + '<td class="bar-cell"><div class="bar"><i style="width:' + pct + '%"></i></div><span>' + pct + '%</span></td>'
        + '<td><button type="button" class="token-reset danger-ghost" data-uid="' + uid + '">重置</button></td>'
        + '</tr>';
    }).join("");
  }

  function renderTokenDetail() {
    var body = els.tokenDetailBody;
    var empty = els.tokenDetailEmpty;
    var count = els.tokenDetailCount;
    if (!body) return;
    var rows = state.token.detail || [];
    if (count) count.textContent = rows.length ? "共 " + rows.length + " 条" : "";
    if (empty) empty.style.display = rows.length ? "none" : "block";
    body.innerHTML = rows.map(function (r) {
      return '<tr>'
        + '<td>' + escapeHtml(r.day_bucket || "") + '</td>'
        + '<td>' + escapeHtml(r.user_id || "") + '</td>'
        + '<td>' + sceneLabel(r.scene) + '</td>'
        + '<td>' + escapeHtml(r.model || "—") + '</td>'
        + '<td>' + fmtTokenNumber(r.input_other) + '</td>'
        + '<td>' + fmtTokenNumber(r.input_cached) + '</td>'
        + '<td>' + fmtTokenNumber(r.output) + '</td>'
        + '<td class="num-strong">' + fmtTokenNumber(r.total) + '</td>'
        + '<td>' + fmtTokenNumber(r.call_count) + '</td>'
        + '</tr>';
    }).join("");
  }

  async function resetTokenUser(btn) {
    var uid = btn.dataset.uid || "";
    var ok = await confirmAction("重置 Token 用量", "确定要删除用户「" + uid + "」的全部 token 用量记录吗？该操作不可恢复。");
    if (!ok) return;
    setButtonBusy(btn, true, "重置中…", "重置");
    try {
      var r = await apiPost("token/reset", { user_id: uid });
      if (!r) throw new Error("无响应");
      if (r.error) throw new Error(r.error);
      showToast("已重置用户 " + uid + " 的 token 用量", "success");
      await loadToken();
    } catch (e) {
      showToast("重置失败：" + (e.message || e), "error");
    } finally {
      setButtonBusy(btn, false, "重置中…", "重置");
    }
  }

  async function resetAllToken() {
    var ok = await confirmAction("重置全部 Token 用量", "确定要清空全部 LLM token 用量记录吗？该操作不可恢复。");
    if (!ok) return;
    setButtonBusy(els.tokenResetAllBtn, true, "重置中…", "重置全部");
    try {
      var r = await apiPost("token/reset", {});
      if (!r) throw new Error("无响应");
      if (r.error) throw new Error(r.error);
      showToast("已清空全部 token 用量记录", "success");
      await loadToken();
    } catch (e) {
      showToast("重置失败：" + (e.message || e), "error");
    } finally {
      setButtonBusy(els.tokenResetAllBtn, false, "重置中…", "重置全部");
    }
  }

  // 抓取工作流封面：调 C 站接口（返回 image 文件名，仅存封面），写入工作流配置
  function fetchWorkflowCover(btn) {
    var idx = +btn.dataset.idx;
    var w = (state.config && state.config.workflows && state.config.workflows[idx]) || {};
    var cur = (w.civitai_url || "").trim();
    if (!cur) {
      showToast("该工作流尚未配置 C 站链接，请先在编辑中填写链接后再抓封面", "info");
      return;
    }
    setButtonBusy(btn, true, "抓取中…", "抓封面");
    apiRaw("lora/fetch", { method: "POST", body: { url: cur }, timeout: 60000 }).then(function (d) {
      setButtonBusy(btn, false, "抓取中…", "抓封面");
      if (!d) return;
      var covers = (Array.isArray(d.images) && d.images.length) ? d.images : (d.image ? [d.image] : []);
      if (!covers.length) {
        showToast("未获取到有效封面（该版本可能无图片预览、只有视频，或网络问题）", "info");
        return;
      }
      var applyCover = function (chosenName) {
        w.image = chosenName;
        apiPost("config", { config: { workflows: state.config.workflows } }).then(function () {
          showToast("封面已保存", "success");
          renderWorkflows();
        }).catch(function (e) { showToast(e.message || "保存失败", "error"); });
      };
      if (covers.length > 1) {
        openCoverPicker(covers, w.name || "工作流", applyCover);
      } else {
        applyCover(covers[0]);
      }
    }).catch(function (e) {
      setButtonBusy(btn, false, "抓取中…", "抓封面");
      showToast("抓取失败：" + (e && e.message ? e.message : "网络错误"), "error");
    });
  }

  // 上传工作流封面
  function uploadWorkflowCover(btn) {
    var input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.onchange = function () {
      var file = input.files && input.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        var b64 = String(reader.result).split(",")[1] || "";
        setButtonBusy(btn, true, "上传中…", "传封面");
        apiPost("lora/upload_image", { filename: file.name, data: b64 }).then(function (d) {
          setButtonBusy(btn, false, "上传中…", "传封面");
          if (!d || !d.name) return;
          var idx = +btn.dataset.idx;
          var w = state.config.workflows[idx] || {};
          w.image = d.name;
          apiPost("config", { config: { workflows: state.config.workflows } }).then(function () {
            showToast("封面已上传并保存", "success");
            renderWorkflows();
          }).catch(function (e) { showToast(e.message || "保存失败", "error"); });
        }).catch(function (e) {
          setButtonBusy(btn, false, "上传中…", "传封面");
          showToast("上传失败：" + (e && e.message ? e.message : "未知错误"), "error");
        });
      };
      reader.readAsDataURL(file);
    };
    input.click();
  }

  // 选择封面弹窗：展示候选缩略图，点选一张
  function openCoverPicker(covers, loraName, onPick) {
    if (!els.editDialog || !els.editBody) return;
    var grid = '<div class="cover-picker">';
    covers.forEach(function (name) {
      grid += '<button type="button" class="cover-pick-item" data-name="' + escapeHtml(name) + '" title="选这张">'
        + '<img data-lora-img="' + escapeHtml(name) + '" alt="" loading="lazy">'
        + '<span class="cover-pick-tag">选用</span></button>';
    });
    grid += '</div>';
    els.editKicker.textContent = "选择封面";
    els.editTitle.textContent = "为「" + escapeHtml(loraName || "LoRA") + "」选择封面";
    els.editBody.innerHTML = grid;
    els.editMsg.textContent = "抓取到 " + covers.length + " 张候选图，点击一张作为封面";
    els.editDialog._onSave = null;
    if (els.editSaveBtn) els.editSaveBtn.style.display = "none";
    if (els.editCancelBtn) els.editCancelBtn.textContent = "取消";
    els.editDialog.showModal();
    // 加载缩略图（带缓存）
    els.editBody.querySelectorAll("[data-lora-img]").forEach(function (img) {
      loadCover(img, img.dataset.loraImg);
    });
    // 点选
    els.editBody.querySelectorAll(".cover-pick-item").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (els.editDialog) els.editDialog.close();
        onPick(btn.dataset.name);
      });
    });
  }

  function fetchLoraRemote(btn, url) {
    setButtonBusy(btn, true, "抓取中…", "抓取");
    apiRaw("lora/fetch", { method: "POST", body: { url: url }, timeout: 60000 }).then(function (d) {
      setButtonBusy(btn, false, "抓取中…", "抓取");
      if (!d) return;
      var idx = +btn.dataset.idx;
      var l = state.config.loras[idx] || {};
      if (d.trigger_words) l.trigger_words = d.trigger_words;
      if (d.description) l.description = d.description;
      // C 站标题并入别名（若不存在）：别名现在是换行分隔（textarea），兼容旧逗号分隔
      if (d.title) {
        var oldKw = String(l.keywords || "").trim();
        var existed = false;
        (oldKw.split(/[,，\n\r]+/).map(function (s) { return s.trim(); }).filter(Boolean)).forEach(function (a) {
          if (a === d.title) existed = true;
        });
        if (!existed) {
          if (oldKw) l.keywords = oldKw + "\n" + d.title;
          else l.keywords = d.title;
        }
      }
      if (d.base_model) {
        var bm = String(d.base_model).toLowerCase();
        if (["anima", "z-image-turbo", "krea2", "illustrious"].indexOf(bm) >= 0) l.base_model = bm;
      }
      l.civitai_url = url;
      var covers = (Array.isArray(d.images) && d.images.length) ? d.images : (d.image ? [d.image] : []);
      if (covers.length > 1) {
        // 多张候选 → 弹选图弹窗，选后再保存
        openCoverPicker(covers, l.name || "LoRA", function (chosenName) {
          l.image = chosenName;
          saveLorasState().then(function () {
            showToast("已选择封面并保存", "success");
            renderLoras();
          }).catch(function (e) { showToast(e.message || "保存失败", "error"); });
        });
      } else {
        if (covers.length === 1) l.image = covers[0];
        saveLorasState().then(function () {
          showToast("抓取成功，已写入「" + (l.name || "LoRA") + "」" + (covers.length ? "" : "；未获取到有效封面"), covers.length ? "success" : "info");
          renderLoras();
        }).catch(function (e) { showToast(e.message || "保存失败", "error"); });
      }
    }).catch(function (e) {
      setButtonBusy(btn, false, "抓取中…", "抓取");
      showToast("抓取失败：" + (e && e.message ? e.message : "网络错误，请手动填写"), "error");
    });
  }

  function uploadLoraCover(btn) {
    var input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.onchange = function () {
      var file = input.files && input.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        var b64 = String(reader.result).split(",")[1] || "";
        setButtonBusy(btn, true, "上传中…", "上传封面");
        apiPost("lora/upload_image", { filename: file.name, data: b64 }).then(function (d) {
          setButtonBusy(btn, false, "上传中…", "上传封面");
          if (!d || !d.name) return;
          var idx = +btn.dataset.idx;
          var l = state.config.loras[idx] || {};
          l.image = d.name;
          saveLorasState().then(function () {
            showToast("封面已上传，请到配置页保存", "success");
            renderLoras();
          });
        }).catch(function (e) {
          setButtonBusy(btn, false, "上传中…", "上传封面");
          showToast("上传失败：" + (e && e.message ? e.message : "未知错误"), "error");
        });
      };
      reader.readAsDataURL(file);
    };
    input.click();
  }

  // 打开 LoRA 封面大图弹窗
  function openLoraImage(fname) {
    if (!els.loraImgDialog || !els.loraImgFull) return;
    els.loraImgFull.src = "";
    els.loraImgFull.classList.add("img-loading");
    els.loraImgDialog.showModal();
    apiGet("lora/image", { name: fname }).then(function (d) {
      els.loraImgFull.classList.remove("img-loading");
      if (d && d.url) {
        els.loraImgFull.src = d.url;
      } else {
        showToast("封面图不存在或已删除", "error");
        els.loraImgDialog.close();
      }
    }).catch(function () {
      els.loraImgFull.classList.remove("img-loading");
      showToast("加载封面大图失败", "error");
      els.loraImgDialog.close();
    });
  }
  // 关闭：点击图片 / 关闭按钮 / ESC（ESC 由 dialog 原生支持）；点击遮罩也关闭
  if (els.loraImgDialog) {
    if (els.loraImgFull) {
      els.loraImgFull.addEventListener("click", function () {
        if (!els.loraImgFull.classList.contains("img-loading")) els.loraImgDialog.close();
      });
    }
    els.loraImgDialog.addEventListener("click", function (e) {
      if (e.target === els.loraImgDialog) els.loraImgDialog.close();
    });
  }

  // 打开 LoRA 详情弹窗（触发词/描述）
  function openLoraDetail(idx) {
    var l = (state.config && state.config.loras && state.config.loras[idx]) || {};
    var tw = (l.trigger_words || "").trim();
    var desc = (l.description || "").trim();
    var alias = (l.keywords || "").trim();
    var bm = (l.base_model || "").trim() || "通用";
    var cUrl = (l.civitai_url || "").trim();
    var lines = [];
    lines.push('<div class="lora-detail-row"><span class="lora-detail-k">名称</span><span>' + escapeHtml(l.name || "—") + '</span></div>');
    lines.push('<div class="lora-detail-row"><span class="lora-detail-k">别名</span><span>' + (alias ? escapeHtml(alias).replace(/\n/g, "<br>") : "—") + '</span></div>');
    lines.push('<div class="lora-detail-row"><span class="lora-detail-k">底模</span><span>' + escapeHtml(bm) + '</span></div>');
    var twHtml = tw ? escapeHtml(tw).replace(/\n/g, "<br>") : "—";
    lines.push('<div class="lora-detail-row"><span class="lora-detail-k">触发词</span><span>' + twHtml + '</span></div>');
    var descHtml = desc ? escapeHtml(desc).replace(/\n/g, "<br>") : "—";
    lines.push('<div class="lora-detail-row"><span class="lora-detail-k">描述</span><span>' + descHtml + '</span></div>');
    if (cUrl) {
      lines.push('<div class="lora-detail-row"><span class="lora-detail-k">C 站</span><a href="' + escapeHtml(cUrl) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(cUrl) + ' ↗</a></div>');
    }
    openEditDialog("LoRA 详情", (l.name || "LoRA") + " 信息", '<div class="lora-detail-box">' + lines.join("") + '</div>', null);
    // 详情只读：隐藏保存按钮，仅保留关闭
    if (els.editSaveBtn) els.editSaveBtn.style.display = "none";
    if (els.editCancelBtn) els.editCancelBtn.textContent = "关闭";
  }

  function saveLorasState() {
    return apiPost("config", { config: { loras: state.config.loras } });
  }

  // ====== 通用编辑弹窗 ======
  function openEditDialog(kicker, title, bodyHtml, onSave) {
    if (!els.editDialog) return;
    els.editKicker.textContent = kicker;
    els.editTitle.textContent = title;
    els.editBody.innerHTML = bodyHtml;
    els.editMsg.textContent = "";
    els.editDialog._onSave = onSave;
    // onSave 为空 = 只读弹窗（如详情），隐藏保存按钮
    if (els.editSaveBtn) els.editSaveBtn.style.display = onSave ? "" : "none";
    els.editDialog.showModal();
  }

  var BM_OPTIONS = ["", "anima", "z-image-turbo", "krea2", "illustrious"];

  function bmSelectHtml(name, cur) {
    var opts = BM_OPTIONS.map(function (o) {
      return '<option value="' + escapeHtml(o) + '"' + (String(o) === String(cur || "") ? " selected" : "") + '>' +
        (o ? escapeHtml(o) : "（通用）") + '</option>';
    }).join("");
    return '<select data-f="' + name + '">' + opts + '</select>';
  }

  function fieldHtml(label, html) {
    return '<div class="edit-field"><label>' + escapeHtml(label) + '</label>' + html + '</div>';
  }
  function inputHtml(name, val, ph) {
    return '<input type="text" data-f="' + escapeHtml(name) + '" value="' + escapeHtml(val == null ? "" : String(val)) + '" placeholder="' + escapeHtml(ph || "") + '">';
  }
  function textareaHtml(name, val, rows) {
    return '<textarea data-f="' + escapeHtml(name) + '" rows="' + (rows || 3) + '">' + escapeHtml(val == null ? "" : String(val)) + '</textarea>';
  }

  function collectForm() {
    var out = {};
    els.editBody.querySelectorAll("[data-f]").forEach(function (el) {
      out[el.dataset.f] = el.tagName === "SELECT" ? el.value : el.value;
    });
    return out;
  }

  function openLoraEditor(idx) {
    if (els.editSaveBtn) els.editSaveBtn.style.display = "";
    if (els.editCancelBtn) els.editCancelBtn.textContent = "取消";
    var loras = (state.config && Array.isArray(state.config.loras)) ? state.config.loras : [];
    var isNew = idx < 0 || idx >= loras.length;
    // 新增 LoRA 时权重默认填 1，避免漏填导致空字符串报错
    var l = isNew ? { weight: 1 } : (loras[idx] || {});
    var body = fieldHtml("名称（引用键）", inputHtml("name", l.name, "如 安魂曲"))
      + fieldHtml("底模", bmSelectHtml("base_model", l.base_model))
      + fieldHtml("别名（每行一个，供 LLM 区分）", textareaHtml("keywords", l.keywords, 3))
      + fieldHtml("触发词（每行一个）", textareaHtml("trigger_words", l.trigger_words, 3))
      + fieldHtml("描述（供 LLM 理解）", textareaHtml("description", l.description, 3))
      + fieldHtml("C 站链接", inputHtml("civitai_url", l.civitai_url, "https://civitai.com/models/xxx"))
      + fieldHtml("封面图文件名", inputHtml("image", l.image, "存于 lora_assets/，可上传"))
      + fieldHtml("提示词预设（每套 [预设名|提示词]，可多套）", textareaHtml("presets", l.presets, 3))
      + fieldHtml("仅模型节点", '<label class="edit-toggle"><input type="checkbox" data-f="model_only_cb"' + (l.model_only !== false ? " checked" : "") + '><span class="toggle-slider"></span><span class="toggle-label">开启时只叠加 MODEL（兼容性最好）；关闭则同时影响 CLIP</span></label>')
      + fieldHtml("默认权重", inputHtml("weight", l.weight, "1.0"))
      + fieldHtml("模型文件名", inputHtml("model_name", l.model_name, "xxx.safetensors"));
    openEditDialog("LoRA", (isNew ? "新增" : "编辑") + " LoRA", body, function () {
      var v = collectForm();
      if (!v.name || !v.name.trim()) { els.editMsg.textContent = "名称必填"; return; }
      v.name = v.name.trim();
      // model_only 开关：checkbox 不在 collectForm 里（它是 data-f 但 checkbox 需特殊处理）
      var moCb = els.editBody.querySelector('[data-f="model_only_cb"]');
      v.model_only = moCb ? moCb.checked : true;
      delete v.model_only_cb;
      // AstrBot 配置校验：template_list 元素需带 __template_key（模板名，schema 里是 default）
      var tplKeyL = (loras[idx] && loras[idx].__template_key) || "default";
      v.__template_key = tplKeyL;
      if (isNew) {
        loras.unshift(v); // 新增的排在最前
      } else {
        loras[idx] = Object.assign({}, loras[idx], v);
      }
      state.config.loras = loras;
      saveLorasState().then(function () {
        els.editDialog.close();
        showToast("LoRA 已保存", "success");
        renderLoras();
      }).catch(function (e) {
        els.editMsg.textContent = "保存失败：" + (e && e.message ? e.message : "未知错误");
      });
    });
  }

  // 复制工作流：深拷贝一份（name 置空）追加到列表，打开新增编辑弹窗
  function copyWorkflow(idx) {
    var wfs = (state.config && Array.isArray(state.config.workflows)) ? state.config.workflows : [];
    var src = wfs[idx];
    if (!src) return;
    var copy = JSON.parse(JSON.stringify(src));
    copy.name = "";
    openWorkflowEditor(-1, copy);
  }

  function openWorkflowEditor(idx, prefill) {
    if (els.editSaveBtn) els.editSaveBtn.style.display = "";
    if (els.editCancelBtn) els.editCancelBtn.textContent = "取消";
    var wfs = (state.config && Array.isArray(state.config.workflows)) ? state.config.workflows : [];
    var isNew = idx < 0 || idx >= wfs.length;
    var w = prefill ? prefill : (isNew ? {} : (wfs[idx] || {}));
    var body = fieldHtml("名称", inputHtml("name", w.name, "如 sd"))
      + fieldHtml("底模", bmSelectHtml("base_model", w.base_model))
      + fieldHtml("别名（逗号/换行分隔）", textareaHtml("aliases", w.aliases, 2))
      + fieldHtml("绑定服务器", inputHtml("server_name", w.server_name, "如 server1"))
      + fieldHtml("工作流文件名", inputHtml("workflow_name", w.workflow_name, "如 sd.json"))
      + fieldHtml("Anima 工作流", '<label class="edit-toggle"><input type="checkbox" data-f="is_anima_cb"' + (w.is_anima ? " checked" : "") + '><span class="toggle-slider"></span><span class="toggle-label">开启后中文提示词会先翻译为 Danbooru 标签</span></label>')
      + fieldHtml("C 站链接（用于抓取封面）", inputHtml("civitai_url", w.civitai_url, "https://civitai.com/models/xxx"))
      + fieldHtml("封面图文件名", inputHtml("image", w.image, "存于 lora_assets/，可抓取或上传"))
      + '<div class="edit-field"><label>── 节点配置 ──</label></div>'
      + fieldHtml("正提示词节点", inputHtml("positive_node", w.positive_node, "如 6"))
      + fieldHtml("负提示词节点", inputHtml("negative_node", w.negative_node, "如 7"))
      + fieldHtml("分辨率节点", inputHtml("resolution_node", w.resolution_node, "EmptyLatentImage 节点，可留空自动探测"))
      + fieldHtml("宽度字段", inputHtml("resolution_width_field", w.resolution_width_field, "width"))
      + fieldHtml("高度字段", inputHtml("resolution_height_field", w.resolution_height_field, "height"))
      + fieldHtml("默认宽度", inputHtml("default_width", w.default_width, "如 512，默认出图宽度"))
      + fieldHtml("默认高度", inputHtml("default_height", w.default_height, "如 512，默认出图高度"))
      + fieldHtml("参考图节点", inputHtml("image_node", w.image_node, "图生图 LoadImage 节点（可选）"))
      + fieldHtml("输出节点", inputHtml("output_node", w.output_node, "出图节点（可选）"))
      + fieldHtml("LoRA 主模锚点", inputHtml("lora_anchor", w.lora_anchor, "底模节点键名，留空自动探测"))
      + fieldHtml("工作流 JSON（可直接粘贴）", textareaHtml("workflow_json", w.workflow_json, 3))
      + fieldHtml("默认 LoRA（每行 名称|权重|启用|底模）", textareaHtml("loras_text", w.loras_text, 3));
    openEditDialog("WORKFLOW", (isNew ? "新增" : "编辑") + " 工作流", body, function () {
      var v = collectForm();
      if (!v.name || !v.name.trim()) { els.editMsg.textContent = "名称必填"; return; }
      v.name = v.name.trim();
      var cb = els.editBody.querySelector('[data-f="is_anima_cb"]');
      v.is_anima = !!(cb && cb.checked);
      delete v.is_anima_cb;
      // AstrBot 配置校验：template_list 元素需带 __template_key（模板名，schema 里是 default）
      var tplKey = (wfs[idx] && wfs[idx].__template_key) || "default";
      v.__template_key = tplKey;
      if (isNew) {
        wfs.unshift(v); // 新增的排在最前
      } else {
        wfs[idx] = Object.assign({}, wfs[idx], v);
      }
      state.config.workflows = wfs;
      apiPost("config", { config: { workflows: state.config.workflows } }).then(function () {
        els.editDialog.close();
        showToast("工作流已保存", "success");
        renderWorkflows();
      }).catch(function (e) {
        els.editMsg.textContent = "保存失败：" + (e && e.message ? e.message : "未知错误");
      });
    });
  }

  if (els.editCancelBtn) {
    els.editCancelBtn.addEventListener("click", function () { if (els.editDialog) els.editDialog.close(); });
  }
  if (els.editSaveBtn) {
    els.editSaveBtn.addEventListener("click", function () {
      if (els.editDialog && typeof els.editDialog._onSave === "function") {
        els.editDialog._onSave();
      }
    });
  }
  // 新增按钮：LoRA / 工作流
  document.addEventListener("click", function (e) {
    if (e.target && e.target.id === "lorasAddBtn") { openLoraEditor(-1); }
    if (e.target && e.target.id === "workflowsAddBtn") { openWorkflowEditor(-1); }
  });

  if (els.lorasRefreshBtn) {
    els.lorasRefreshBtn.addEventListener("click", function () {
      loadConfig().then(function () { renderLoras(); });
    });
  }

  els.logLevel.addEventListener("change", renderLogs);
  els.logSearch.addEventListener("input", renderLogs);
  els.logRefreshBtn.addEventListener("click", function () {
    if (logTabState === "records") loadRecords(); else loadLogs();
  });
  els.recSearch.addEventListener("input", (function () {
    var timer = null;
    return function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        loadRecordsPage(1, true);
      }, 350);
    };
  })());
  els.recFailedOnly.addEventListener("change", loadRecords);
  if (els.recPrevBtn) {
    els.recPrevBtn.addEventListener("click", function () {
      if (state.recPage > 1) loadRecordsPage(state.recPage - 1, false);
    });
  }
  if (els.recNextBtn) {
    els.recNextBtn.addEventListener("click", function () {
      loadRecordsPage(state.recPage + 1, false);
    });
  }
  if (els.recFirstBtn) els.recFirstBtn.addEventListener("click", function () { loadRecordsPage(1, false); });
  if (els.recLastBtn) els.recLastBtn.addEventListener("click", function () {
    var tp = state.recTotal ? Math.ceil(state.recTotal / state.recPageSize) : 1;
    loadRecordsPage(tp, false);
  });
  bindPagerJump(els.recJumpInput, els.recJumpBtn, function (p) { loadRecordsPage(p, false); });
  if (els.logTabs) {
    els.logTabs.querySelectorAll(".tab").forEach(function (b) {
      b.addEventListener("click", function () { setLogTab(b.dataset.logtab); });
    });
  }

  // ====== GALLERY ======
  async function loadGalStats() {
    try {
      state.galStats = await apiGet("gallery/stats") || {};
      renderGalStats();
    } catch (e) { /* ignore */ }
  }

  function renderGalStats() {
    var s = state.galStats;
    els.galStats.innerHTML = [
      "<span><strong>" + (s.total || 0) + "</strong> 张图片</span>",
      "<span><strong>" + (s.starred || 0) + "</strong> 收藏</span>",
      s.size_mb != null ? "<span>占用 <strong>" + (typeof s.size_mb === "number" ? s.size_mb.toFixed(1) : s.size_mb) + "</strong> MB</span>" : "",
      (s.trash_count || 0) ? "<span class='trash-stat'>回收站 <strong>" + s.trash_count + "</strong> 张</span>" : "",
    ].join("");
    if (els.galTrashBadge) {
      if (s.trash_count) {
        els.galTrashBadge.textContent = s.trash_count;
        els.galTrashBadge.hidden = false;
      } else {
        els.galTrashBadge.hidden = true;
      }
    }
  }

  var galTabState = "normal"; // normal | trash

  async function galSearch() {
    await galSearchPage(1, true);
  }

  // 翻页加载图库（替换式，不做累加）。
  async function galSearchPage(page, reset) {
    if (state.galSearching) return;
    state.galSearching = true;
    els.galGrid.innerHTML = '<div class="empty">搜索中…</div>';
    try {
      var params = {};
      var q = els.galSearch.value.trim();
      if (q) params.keyword = q;
      if (els.galType.value) params.type = els.galType.value;
      if (els.galStarred.checked) params.starred = "1";
      if (galTabState === "trash") params.trash = "1";
      params.page = page;
      params.size = state.galPageSize;
      var data = await apiGet("gallery/search", params);
      var rows = Array.isArray(data) ? data : (data.rows || data.results || data.images || []);
      state.galTotal = (data && typeof data === "object" && data.total != null)
        ? Number(data.total) : 0;
      state.galResults = rows;
      state.galPage = page;
      renderGalResults();
      els.galCount.textContent = state.galTotal ? state.galTotal + " 张" : state.galResults.length + " 张";
    } catch (e) {
      if (reset) {
        els.galGrid.innerHTML = '<div class="empty error">搜索失败：' + escapeHtml(e.message) + '</div>';
        els.galCount.textContent = "";
      } else {
        showToast(e.message || "翻页失败", "error");
      }
    } finally {
      state.galSearching = false;
      updateGalPager();
    }
  }

  // 更新图库翻页控件（首页/上一页/页码/下一页/末页 + 跳转）。
  function updateGalPager() {
    if (!els.galPager) return;
    var totalPages = state.galTotal ? Math.ceil(state.galTotal / state.galPageSize) : 1;
    if (state.galPage > totalPages) state.galPage = totalPages || 1;
    if (state.galPage < 1) state.galPage = 1;
    els.galPager.hidden = state.galTotal === 0;
    renderPager({
      firstBtn: els.galFirstBtn, prevBtn: els.galPrevBtn, btnsEl: els.galPageBtns,
      nextBtn: els.galNextBtn, lastBtn: els.galLastBtn, infoEl: els.galPageInfo,
      jumpInput: els.galJumpInput, jumpBtn: els.galJumpBtn,
      page: state.galPage, totalPages: totalPages,
      onGo: function (p) { galSearchPage(p, false); }
    });
  }

  function renderGalResults() {
    if (!state.galResults.length) {
      if (galTabState === "trash") {
        els.galGrid.innerHTML = '<div class="empty">回收站是空的。</div>';
      } else {
        var filtering = !!(els.galSearch.value.trim() || els.galType.value || els.galStarred.checked);
        els.galGrid.innerHTML = filtering
          ? '<div class="empty">没有找到匹配的图片</div>'
          : '<div class="empty">图库里还没有图片。<br/>先在对话框里让插件出一张图，生成成功后会自动归档到这里。</div>';
      }
      return;
    }
    var isTrash = galTabState === "trash";
    els.galGrid.innerHTML = state.galResults.map(function (img) {
      var sha = img.sha || img.sha256 || "";
      var prompt = img.prompt || img.prompt_raw || "";
      var w = img.w || img.width || "";
      var h = img.h || img.height || "";
      var size = w && h ? w + "x" + h : "";
      var starred = img.starred;
      var actions = isTrash
        ? '<button data-restore="' + escapeHtml(sha) + '">恢复</button>' +
          '<button data-purge="' + escapeHtml(sha) + '" class="danger">彻底删除</button>'
        : '<button data-star="' + escapeHtml(sha) + '" class="' + (starred ? "starred" : "") + '">' + (starred ? "★" : "☆") + '</button>' +
          '<button data-del="' + escapeHtml(sha) + '" class="danger">移入回收站</button>';
      return '<div class="gal-card' + (isTrash ? " in-trash" : "") + '">' +
        '<img class="gal-img" src="' + THUMB_PLACEHOLDER + '" data-sha="' + escapeHtml(sha) + '" data-open="' + escapeHtml(sha) + '" alt="' + escapeHtml(prompt.slice(0, 80)) + '" loading="lazy" />' +
        '<div class="gal-meta">' +
          '<div class="gal-prompt">' + escapeHtml(prompt.slice(0, 60) || "(无描述)") + '</div>' +
          '<div>' + escapeHtml(size) + (img.is_img2img ? " · 图生图" : "") + (img.deleted ? " · 回收站" : "") + '</div>' +
        '</div>' +
        '<div class="gal-actions">' + actions + '</div>' +
      '</div>';
    }).join("");

    // 懒加载缩略图：图片进入视口时经 bridge 拉取单张 data URL
    observeThumbs();

    // 更新翻页控件
    updateGalPager();

    // 点击图片直接看大图
    els.galGrid.querySelectorAll("[data-open]").forEach(function (img) {
      img.addEventListener("click", function () { openImage(img.dataset.open); });
    });
    els.galGrid.querySelectorAll("[data-star]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var sha = btn.dataset.star;
        var cur = btn.classList.contains("starred");
        try {
          await apiPost("gallery/star", { sha: sha, on: !cur });
          btn.classList.toggle("starred", !cur);
          btn.textContent = cur ? "☆" : "★";
          showToast(cur ? "已取消收藏" : "已收藏");
          loadGalStats();
        } catch (e) { showToast(e.message || "操作失败", "error"); }
      });
    });
    els.galGrid.querySelectorAll("[data-del]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var sha = btn.dataset.del;
        if (!await confirmAction("移入回收站", "确定要把该图移入回收站吗？回收站内可恢复，彻底删除才不可逆。")) return;
        try {
          await apiPost("gallery/delete", { sha: sha });
          showToast("已移入回收站");
          galSearch();
          loadGalStats();
        } catch (e) { showToast(e.message || "操作失败", "error"); }
      });
    });
    els.galGrid.querySelectorAll("[data-restore]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var sha = btn.dataset.restore;
        try {
          await apiPost("gallery/restore", { sha: sha });
          showToast("已恢复");
          galSearch();
          loadGalStats();
        } catch (e) { showToast(e.message || "恢复失败", "error"); }
      });
    });
    els.galGrid.querySelectorAll("[data-purge]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var sha = btn.dataset.purge;
        if (!await confirmAction("彻底删除", "确定要永久删除该图吗？此操作不可恢复！")) return;
        try {
          await apiPost("gallery/purge", { sha: sha });
          showToast("已彻底删除");
          galSearch();
          loadGalStats();
        } catch (e) { showToast(e.message || "删除失败", "error"); }
      });
    });
  }

  async function openImage(sha, opts) {
    opts = opts || {};
    try {
      // 大图经 bridge 拉取 data_url（浏览器 <img> 直连裸路径会 404/401）。
      // gallery/image?meta=1 走 bridge 返回 { data_url, mime, meta }，其中 data_url
      // 是原图 base64，赋给 <img> src 即可显示，不走 AstrBot 插件路由。
      els.imageDialogImg.src = "";
      els.imageDialogImg.classList.add("img-loading");
      // 默认：单图模式（文生图），不显示任何标签
      if (els.imageDialogCaption1) els.imageDialogCaption1.hidden = true;
      els.imageDialogRefFig.hidden = true;
      els.imageDialogRefImg.src = "";
      if (els.imageDialogImgs) els.imageDialogImgs.dataset.pair = "0";
      els.imageDialogInfo.innerHTML = '<div class="empty">加载中…</div>';
      els.imageDialog.showModal();
      var data = await apiGet("gallery/image?sha=" + encodeURIComponent(sha) + "&meta=1");
      if (data && data.data_url) {
        els.imageDialogImg.src = data.data_url;
      } else {
        els.imageDialogImg.src = imageUrl(sha);
      }
      els.imageDialogImg.classList.remove("img-loading");
      // meta 元数据（meta=1 返回 JSON，含 data_url 兜底 + 元数据）
      var img = null;
      try {
        img = data && data.meta ? data.meta : (data || {});
        var info = [];
        var add = function (k, v) { if (v != null && v !== "") info.push("<div><span class='k'>" + escapeHtml(k) + "</span><span class='v'>" + escapeHtml(String(v)) + "</span></div>"); };
        add("SHA", (img.sha256 || sha).slice(0, 20) + "…");
        add("类型", img.is_img2img ? "图生图" : (img.source === "ref" ? "参考图" : (img.source === "user" ? "用户收藏" : "文生图")));
        if (img.workflow) add("工作流", img.workflow);
        if (img.w && img.h) add("尺寸", img.w + " × " + img.h);
        if (img.size_bytes != null) add("大小", fmtSize(img.size_bytes));
        if (img.cost_sec != null) add("耗时", Number(img.cost_sec).toFixed(1) + " 秒");
        if (img.created_at) {
          var t = new Date(Number(img.created_at) * 1000);
          add("出图时间", t.toLocaleString("zh-CN", { hour12: false }));
        }
        if (img.user_name) add("用户名", img.user_name);
        if (img.user_id) add("用户ID", img.user_id);
        if (img.session_id) add("会话ID", img.session_id);
        if (img.trigger_msg) add("触发消息", img.trigger_msg);
        if (img.status === 1) add("状态", "失败");
        else if (img.status === 0) add("状态", "成功");
        if (img.starred) add("收藏", "★ 已收藏");
        if (img.seed != null) add("Seed", img.seed);
        // 提示词单独一项，允许在面板内独立滚动（其他信息项固定显示，不随面板整体滚动）
        var promptV = img.prompt_raw || img.prompt || "（无）";
        info.push("<div class='info-prompt'><span class='k'>提示词</span><span class='v'>" + escapeHtml(String(promptV)) + "</span></div>");
        els.imageDialogInfo.innerHTML = info.join("");
      } catch (e) {
        els.imageDialogInfo.innerHTML = '<div class="empty error">信息加载失败：' + escapeHtml(e.message || "") + '</div>';
      }
      // 图生图：并排展示参考图（源图）与结果图。只有确实有参考图时才显示
      // "结果图/参考图"两个标签；文生图或参考图缺失时保持单图干净展示。
      var refSha = (img && img.ref_sha256) ? String(img.ref_sha256) : (opts.refSha || null);
      if (refSha) {
        els.imageDialogCaption1.textContent = "结果图";
        els.imageDialogCaption1.hidden = false;
        els.imageDialogRefFig.hidden = false;
        els.imageDialogRefImg.src = "";
        if (els.imageDialogImgs) els.imageDialogImgs.dataset.pair = "1";
        // 参考图先经 bridge 取 data_url（更小、直连免 token），失败再回退裸路径
        try {
          var rdata = await apiGet("gallery/image?sha=" + encodeURIComponent(refSha) + "&meta=1");
          if (rdata && rdata.data_url) {
            els.imageDialogRefImg.src = rdata.data_url;
          } else {
            els.imageDialogRefImg.src = imageUrl(refSha);
          }
        } catch (e) {
          els.imageDialogRefImg.src = imageUrl(refSha);
        }
      } else {
        els.imageDialogCaption1.hidden = true;
        els.imageDialogRefFig.hidden = true;
      }
    } catch (e) {
      showToast("打开图片失败：" + (e.message || ""), "error");
    }
  }

  // 备份图库数据库：从后端拉取 base64，前端构造 Blob 触发下载（走 bridge，规避裸路径需 token）。
  function downloadDataUrl(dataUrl, filename) {
    var mime = (dataUrl.split(",")[0] || "").split(":")[1] || "application/octet-stream";
    var b64 = dataUrl.split(",")[1] || dataUrl;
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    var blob = new Blob([bytes], { type: mime });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename || "gallery_backup.db";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  if (els.backupDbBtn) {
    els.backupDbBtn.addEventListener("click", async function () {
      setButtonBusy(els.backupDbBtn, true, "备份中…", "备份数据库");
      try {
        var data = await apiGet("gallery/backup");
        if (!data || !data.data_url) throw new Error("未获取到备份数据");
        downloadDataUrl(data.data_url, data.filename || "gallery_backup.db");
        showToast("已开始下载数据库备份");
      } catch (e) {
        showToast(e.message || "备份失败", "error");
      } finally {
        setButtonBusy(els.backupDbBtn, false, "备份中…", "备份数据库");
      }
    });
  }

  els.galSearchBtn.addEventListener("click", galSearch);
  els.galSearch.addEventListener("keydown", function (e) { if (e.key === "Enter") galSearch(); });
  if (els.galPrevBtn) {
    els.galPrevBtn.addEventListener("click", function () {
      if (state.galPage > 1) galSearchPage(state.galPage - 1, false);
    });
  }
  if (els.galNextBtn) {
    els.galNextBtn.addEventListener("click", function () {
      galSearchPage(state.galPage + 1, false);
    });
  }
  if (els.galFirstBtn) els.galFirstBtn.addEventListener("click", function () { galSearchPage(1, false); });
  if (els.galLastBtn) els.galLastBtn.addEventListener("click", function () {
    var tp = state.galTotal ? Math.ceil(state.galTotal / state.galPageSize) : 1;
    galSearchPage(tp, false);
  });
  bindPagerJump(els.galJumpInput, els.galJumpBtn, function (p) { galSearchPage(p, false); });
  if (els.galTabs) {
    els.galTabs.querySelectorAll(".tab").forEach(function (b) {
      b.addEventListener("click", function () {
        galTabState = b.dataset.galtab;
        els.galTabs.querySelectorAll(".tab").forEach(function (x) {
          x.classList.toggle("active", x === b);
        });
        galSearch();
      });
    });
  }

  // ====== BIND EVENTS ======
  function bindEvents() {
    // 大图弹窗：点击背景/空白区域（非图片、非信息面板、非关闭按钮）关闭
    els.imageDialog.addEventListener("click", function (e) {
      if (e.target.closest(".image-close")) return;
      if (e.target.closest(".image-dialog-imgwrap img, .image-info")) return;
      els.imageDialog.close();
    });

    // nav
    document.querySelectorAll(".workspace-nav [data-view]").forEach(function (btn) {
      btn.addEventListener("click", function () { switchView(btn.dataset.view); });
    });
    els.refreshBtn.addEventListener("click", async function () {
      setButtonBusy(els.refreshBtn, true, "刷新中…", "刷新数据");
      hideGlobalError();
      var failures = [];
      try { await loadConfig(); } catch (e) { failures.push("配置"); }
      try { await loadRecords(); } catch (e) { failures.push("出图记录"); }
      try { await loadLogs(); } catch (e) { failures.push("日志"); }
      try { await loadGalStats(); } catch (e) { failures.push("图库统计"); }
      try { await galSearch(); } catch (e) { failures.push("图库搜索"); }
      if (failures.length) showGlobalError(failures);
      setButtonBusy(els.refreshBtn, false, "刷新中…", "刷新数据");
    });

    // 生图限额页
    if (els.quotaRefreshBtn) {
      els.quotaRefreshBtn.addEventListener("click", function () { loadQuota(); });
    }
    if (els.quotaResetAllBtn) {
      els.quotaResetAllBtn.addEventListener("click", async function () {
        var ok = await confirmAction("重置全部生图次数", "确定要重置所有用户的总次数与当前小时次数吗？此操作不可撤销。");
        if (!ok) return;
        setButtonBusy(els.quotaResetAllBtn, true, "重置中…", "重置全部次数");
        try {
          var r = await apiPost("quota/reset", {});
          if (!r) throw new Error("无响应");
          if (r.error) throw new Error(r.error);
          showToast("已重置全部用户的生图次数", "success");
          await loadQuota();
        } catch (e) {
          showToast("重置失败：" + (e.message || e), "error");
        } finally {
          setButtonBusy(els.quotaResetAllBtn, false, "重置中…", "重置全部次数");
        }
      });
    }
    // 表格事件（委托）
    var quotaBody = els.quotaBody;
    if (quotaBody) {
      quotaBody.addEventListener("click", function (e) {
        var saveBtn = e.target.closest(".quota-save");
        if (saveBtn) { saveQuotaConfig(saveBtn); return; }
        var resetBtn = e.target.closest(".quota-reset");
        if (resetBtn) { resetQuotaUser(resetBtn); return; }
      });
    }

    // token（LLM 用量统计）页
    if (els.tokenScope) {
      // 初始 active 由 data-active 标记决定
      els.tokenScope.querySelectorAll(".scope-tab").forEach(function (b) {
        if (b.hasAttribute("data-active")) b.classList.add("active");
        b.addEventListener("click", function () { setTokenScope(b.dataset.tokenScope); });
      });
    }
    if (els.tokenRefreshBtn) {
      els.tokenRefreshBtn.addEventListener("click", function () { loadToken(); });
    }
    if (els.tokenResetAllBtn) {
      els.tokenResetAllBtn.addEventListener("click", resetAllToken);
    }
    if (els.tokenUserBody) {
      els.tokenUserBody.addEventListener("click", function (e) {
        var resetBtn = e.target.closest(".token-reset");
        if (resetBtn) { resetTokenUser(resetBtn); return; }
      });
    }

    $("retryAllBtn").addEventListener("click", function () { els.refreshBtn.click(); });
  }

  // ====== START ======
  // 桥接对象由宿主异步注入；在 AstrBot 后台以 iframe 嵌入时 bridge 挂在 parent 上
  // （见上面移植的 getBridge，会同时查找 window 与 window.parent）。用 getPageBridge
  // （含超时探测 + parent 回退）获取，不再依赖单一 window 上的对象。
  async function start() {
    var br = null;
    try {
      br = await getPageBridge(8000);
    } catch (e) {
      br = null;
    }
    if (!br) {
      els.cfgContent.innerHTML = '<div class="empty error">AstrBot 页面桥接不可用，请在 AstrBot 内置环境中（插件拓展页）打开此页面。</div>';
      els.globalError.hidden = false;
      els.globalErrorMessage.textContent = "未能获取 AstrBot 页面桥接：请在 AstrBot Dashboard 的插件拓展页打开 WebUI。";
      setStatus("桥接不可用", true);
      return;
    }
    bindEvents();
    setStatus("正在连接…");
    var initialView = (location.hash || "#config").slice(1);
    switchView(initialView);
    try { await loadConfig(); setStatus("已就绪"); } catch (e) { setStatus("初始化失败", true); }
    try { await loadGalStats(); } catch (e) { /* non-critical */ }
  }

  start();
})();
