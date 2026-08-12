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
  };

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
    recPrevBtn: $("recPrevBtn"),
    recNextBtn: $("recNextBtn"),
    recPageInfo: $("recPageInfo"),
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
    galPrevBtn: $("galPrevBtn"),
    galNextBtn: $("galNextBtn"),
    galPageInfo: $("galPageInfo"),
    // stats
    statsRefreshBtn: $("statsRefreshBtn"),
    statsMergeBtn: $("statsMergeBtn"),
    statsRanking: $("statsRanking"),
    statsTrendChart: $("statsTrendChart"),
    statsTrendInfo: $("statsTrendInfo"),
    // dialogs
    confirmDialog: $("confirmDialog"),
    dialogTitle: $("dialogTitle"),
    dialogMessage: $("dialogMessage"),
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

  async function bridgeRequest(br, path, method, body) {
    var url = new URL(path, "https://astrbot-plugin-page.local/");
    var routePath = url.pathname.replace(/^\/+/, "");
    var candidates = bridgeEndpointCandidates(routePath);
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
          var p = await withTimeout(br.apiGet(candidates[i].endpoint, Object.keys(params).length ? params : undefined), 6000, "GET " + candidates[i].endpoint);
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
        var r = await withTimeout(br.apiPost(candidates[j].endpoint, payload), 6000, "POST " + candidates[j].endpoint);
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
      payload = await bridgeRequest(br, path, method, options.body);
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
    var html = '<div class="cfg-sections">';
    keys.forEach(function (key) {
      var field = schema[key];
      var val = state.config ? state.config[key] : undefined;
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
    });
    html += '</div>';
    els.cfgContent.innerHTML = html;
    els.cfgSaveBtn.disabled = true;
    els.cfgSaveMsg.textContent = "";

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

  // 更新出图记录翻页控件。
  function updateRecPager() {
    if (!els.recPager) return;
    var totalPages = state.recTotal ? Math.ceil(state.recTotal / state.recPageSize) : 1;
    var hasPrev = state.recPage > 1;
    var hasNext = state.recPage < totalPages;
    els.recPager.hidden = state.recTotal === 0;
    if (els.recPrevBtn) els.recPrevBtn.disabled = !hasPrev;
    if (els.recNextBtn) els.recNextBtn.disabled = !hasNext;
    if (els.recPageInfo) {
      els.recPageInfo.textContent = "第 " + state.recPage + " / " + totalPages + " 页";
    }
  }

  function renderRecords() {
    var q = els.recSearch.value.trim().toLowerCase();
    var rows = state.records.filter(function (r) {
      if (!q) return true;
      return [r.user_name, r.trigger_msg, r.prompt, r.prompt_raw]
        .join(" ").toLowerCase().indexOf(q) >= 0;
    });
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
      html += '<tr><td class="rank">' + r.rank + '</td>'
        + '<td class="user">' + escapeHtml(r.user_name) + '</td>'
        + '<td class="uid">' + escapeHtml(r.user_id || "—") + '</td>'
        + '<td class="count">' + r.count + '</td>'
        + '<td class="bar-cell"><div class="bar"><i style="width:' + pct + '%"></i></div><span>' + pct + '%</span></td></tr>';
    });
    html += '</tbody></table>';
    holder.innerHTML = html;
  }

  async function loadStatsTrend() {
    var holder = els.statsTrendChart;
    if (!holder) return;
    holder.innerHTML = '<div class="empty">正在加载趋势…</div>';
    try {
      var data = await apiGet("stats/trend", { days: 1 });
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

  els.logLevel.addEventListener("change", renderLogs);
  els.logSearch.addEventListener("input", renderLogs);
  els.logRefreshBtn.addEventListener("click", function () {
    if (logTabState === "records") loadRecords(); else loadLogs();
  });
  els.recSearch.addEventListener("input", renderRecords);
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

  // 更新图库翻页控件（上一页/页码/下一页）。
  function updateGalPager() {
    if (!els.galPager) return;
    var totalPages = state.galTotal ? Math.ceil(state.galTotal / state.galPageSize) : 1;
    var hasPrev = state.galPage > 1;
    var hasNext = state.galPage < totalPages;
    els.galPager.hidden = state.galTotal === 0;
    if (els.galPrevBtn) els.galPrevBtn.disabled = !hasPrev;
    if (els.galNextBtn) els.galNextBtn.disabled = !hasNext;
    if (els.galPageInfo) {
      els.galPageInfo.textContent = "第 " + state.galPage + " / " + totalPages + " 页";
    }
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
