(function () {
  "use strict";

  const bridge = window.AstrBotPluginPage;

  const state = {
    config: {},
    configDirty: false,
    logs: [],
    servers: [],
    galStats: {},
    galResults: [],
    galSearching: false,
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
    // logs
    logContent: $("logContent"),
    logLevel: $("logLevel"),
    logSearch: $("logSearch"),
    logRefreshBtn: $("logRefreshBtn"),
    logCount: $("logCount"),
    // debug
    serverList: $("serverList"),
    // gallery
    galStats: $("galStats"),
    galGrid: $("galGrid"),
    galSearch: $("galSearch"),
    galType: $("galType"),
    galStarred: $("galStarred"),
    galSearchBtn: $("galSearchBtn"),
    galCount: $("galCount"),
    // dialogs
    confirmDialog: $("confirmDialog"),
    dialogTitle: $("dialogTitle"),
    dialogMessage: $("dialogMessage"),
    imageDialog: $("imageDialog"),
    imageDialogImg: $("imageDialogImg"),
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
  // webui_api.py 注册的路由前缀是 /astrbot_plugin_comfyui_anima/page，
  // bridge 自动拼 /api/plugins/extensions/<plugin_name>/ 前缀，
  // 因此 endpoint 需包含 "page/" 来匹配完整路径。
  // bridge 对 {status:"ok",data} 自动解包为 data；
  // 对 {status:"error"} 或 HTTP 失败自动 reject。
  var API_PREFIX = "page/";

  async function apiGet(endpoint, params) {
    return await bridge.apiGet(API_PREFIX + endpoint, params || {});
  }

  async function apiPost(endpoint, body) {
    return await bridge.apiPost(API_PREFIX + endpoint, body || {});
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
    if (name === "logs" && !state.logs.length) loadLogs();
    if (name === "debug" && !state.servers.length) loadServers();
  }

  // ====== CONFIG ======
  async function loadConfig() {
    try {
      var data = await apiGet("config");
      state.config = data || {};
      state.configDirty = false;
      renderConfig();
      setStatus("配置已加载");
    } catch (e) {
      els.cfgContent.innerHTML = '<div class="empty error">读取配置失败：' + escapeHtml(e.message) + '</div>';
      setStatus("配置加载失败", true);
    }
  }

  function renderConfig() {
    var cfg = state.config;
    // flatten nested dict to simple key-value
    var fields = [];
    function walk(obj, prefix) {
      Object.keys(obj || {}).forEach(function (k) {
        var val = obj[k];
        var key = prefix ? prefix + "." + k : k;
        if (val && typeof val === "object" && !Array.isArray(val)) {
          walk(val, key);
        } else {
          fields.push({ key: key, val: val });
        }
      });
    }
    walk(cfg, "");
    if (!fields.length) {
      els.cfgContent.innerHTML = '<div class="empty">配置为空</div>';
      els.cfgSaveBtn.disabled = true;
      return;
    }
    els.cfgContent.innerHTML = fields.map(function (f) {
      var type = typeof f.val === "boolean" ? "bool" : typeof f.val === "number" ? "num" : "str";
      var inputHtml = "";
      if (type === "bool") {
        inputHtml = '<input type="checkbox" data-key="' + escapeHtml(f.key) + '" ' + (f.val ? "checked" : "") + ' />';
      } else if (type === "num") {
        inputHtml = '<input type="number" data-key="' + escapeHtml(f.key) + '" value="' + f.val + '" step="any" />';
      } else {
        var valStr = f.val === null || f.val === undefined ? "" : String(f.val);
        if (valStr.length > 80) {
          inputHtml = '<textarea data-key="' + escapeHtml(f.key) + '" rows="3">' + escapeHtml(valStr) + '</textarea>';
        } else {
          inputHtml = '<input type="text" data-key="' + escapeHtml(f.key) + '" value="' + escapeHtml(valStr) + '" />';
        }
      }
      return '<div class="cfg-field"><label>' + escapeHtml(f.key) + '</label>' + inputHtml + '</div>';
    }).join("");
    els.cfgSaveBtn.disabled = true;
    els.cfgSaveMsg.textContent = "";

    // mark dirty on change
    els.cfgContent.querySelectorAll("input,textarea").forEach(function (el) {
      el.addEventListener("input", function () {
        state.configDirty = true;
        els.cfgSaveBtn.disabled = false;
        els.cfgSaveMsg.textContent = "";
      });
      el.addEventListener("change", function () {
        state.configDirty = true;
        els.cfgSaveBtn.disabled = false;
        els.cfgSaveMsg.textContent = "";
      });
    });
  }

  function readConfigForm() {
    var cfg = JSON.parse(JSON.stringify(state.config));
    els.cfgContent.querySelectorAll("[data-key]").forEach(function (el) {
      var key = el.dataset.key;
      var val = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
      // set nested key
      var parts = key.split(".");
      var obj = cfg;
      for (var i = 0; i < parts.length - 1; i++) {
        if (!obj[parts[i]] || typeof obj[parts[i]] !== "object") obj[parts[i]] = {};
        obj = obj[parts[i]];
      }
      obj[parts[parts.length - 1]] = val;
    });
    return cfg;
  }

  els.cfgSaveBtn.addEventListener("click", async function () {
    if (!state.configDirty) return;
    els.cfgSaveMsg.textContent = "";
    setButtonBusy(els.cfgSaveBtn, true, "保存中…", "保存配置");
    try {
      var cfg = readConfigForm();
      await apiPost("config", cfg);
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

  // ====== LOGS ======
  async function loadLogs() {
    try {
      var data = await apiGet("logs");
      state.logs = Array.isArray(data) ? data : (data.logs || []);
      renderLogs();
      setStatus("日志已加载");
    } catch (e) {
      els.logContent.innerHTML = '<div class="empty error">读取日志失败：' + escapeHtml(e.message) + '</div>';
    }
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

  els.logLevel.addEventListener("change", renderLogs);
  els.logSearch.addEventListener("input", renderLogs);
  els.logRefreshBtn.addEventListener("click", loadLogs);

  // ====== DEBUG ======
  async function loadServers() {
    try {
      var data = await apiGet("servers");
      state.servers = Array.isArray(data) ? data : (data.servers || []);
      renderServers();
      setStatus("服务器列表已加载");
    } catch (e) {
      els.serverList.innerHTML = '<div class="empty error">读取服务器列表失败：' + escapeHtml(e.message) + '</div>';
    }
  }

  function renderServers() {
    if (!state.servers.length) {
      els.serverList.innerHTML = '<div class="empty">未配置 ComfyUI 服务器</div>';
      return;
    }
    els.serverList.innerHTML = state.servers.map(function (srv, idx) {
      var status = srv.status || "unknown";
      var statusLabel = status === "online" ? "在线" : status === "offline" ? "离线" : "未检测";
      return '<div class="server-card">' +
        '<div class="server-header">' +
          '<h3>' + escapeHtml(srv.label || srv.url || ("服务器 " + (idx + 1))) + '</h3>' +
          '<span class="server-status ' + status + '">' + statusLabel + '</span>' +
        '</div>' +
        '<div class="server-info">' +
          '<div>地址：' + escapeHtml(srv.url || "-") + '</div>' +
          (srv.gpu ? '<div>GPU：' + escapeHtml(srv.gpu) + '</div>' : '') +
          (srv.queue != null ? '<div>队列：' + srv.queue + '</div>' : '') +
        '</div>' +
        '<div class="server-actions">' +
          '<button data-test="' + idx + '">测试连接</button>' +
        '</div>' +
      '</div>';
    }).join("");
    els.serverList.querySelectorAll("[data-test]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var idx = Number(btn.dataset.test);
        var srv = state.servers[idx];
        if (!srv || !srv.url) return;
        setButtonBusy(btn, true, "检测中…", "测试连接");
        try {
          var result = await apiGet("test_server?url=" + encodeURIComponent(srv.url));
          srv.status = result && result.ok ? "online" : "offline";
          if (result) { srv.gpu = result.gpu; srv.queue = result.queue; }
          showToast("连接成功");
        } catch (e) {
          srv.status = "offline";
          showToast(e.message || "连接失败", "error");
        } finally {
          setButtonBusy(btn, false, "检测中…", "测试连接");
          renderServers();
        }
      });
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
    ].join("");
  }

  async function galSearch() {
    if (state.galSearching) return;
    state.galSearching = true;
    els.galGrid.innerHTML = '<div class="empty">搜索中…</div>';
    try {
      var params = {};
      var q = els.galSearch.value.trim();
      if (q) params.q = q;
      if (els.galType.value) params.type = els.galType.value;
      if (els.galStarred.checked) params.starred = "1";
      var data = await apiGet("gallery/search", params);
      state.galResults = Array.isArray(data) ? data : (data.results || data.images || []);
      renderGalResults();
      els.galCount.textContent = state.galResults.length + " 张";
    } catch (e) {
      els.galGrid.innerHTML = '<div class="empty error">搜索失败：' + escapeHtml(e.message) + '</div>';
      els.galCount.textContent = "";
    } finally {
      state.galSearching = false;
    }
  }

  function renderGalResults() {
    if (!state.galResults.length) {
      els.galGrid.innerHTML = '<div class="empty">没有找到匹配的图片</div>';
      return;
    }
    els.galGrid.innerHTML = state.galResults.map(function (img) {
      var sha = img.sha256 || "";
      var prompt = img.prompt || img.prompt_raw || "";
      var w = img.w || img.width || "";
      var h = img.h || img.height || "";
      var size = w && h ? w + "x" + h : "";
      var starred = img.starred;
      return '<div class="gal-card">' +
        '<img src="" data-sha="' + escapeHtml(sha) + '" alt="' + escapeHtml(prompt.slice(0, 80)) + '" loading="lazy" />' +
        '<div class="gal-meta">' +
          '<div class="gal-prompt">' + escapeHtml(prompt.slice(0, 60) || "(无描述)") + '</div>' +
          '<div>' + escapeHtml(size) + (img.is_img2img ? " · 图生图" : "") + '</div>' +
        '</div>' +
        '<div class="gal-actions">' +
          '<button data-star="' + escapeHtml(sha) + '" class="' + (starred ? "starred" : "") + '">' + (starred ? "★" : "☆") + '</button>' +
          '<button data-zoom="' + escapeHtml(sha) + '">放大</button>' +
          '<button data-del="' + escapeHtml(sha) + '" class="danger">删除</button>' +
        '</div>' +
      '</div>';
    }).join("");
    // load thumbnails
    state.galResults.forEach(function (img) {
      var sha = img.sha256 || "";
      var imgEl = els.galGrid.querySelector('img[data-sha="' + sha.replace(/"/g, '\\"') + '"]');
      if (!imgEl) return;
      apiGet("gallery/image?sha=" + encodeURIComponent(sha)).then(function (data) {
        if (data && data.data_url) imgEl.src = data.data_url;
      }).catch(function () {});
    });
    // events
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
    els.galGrid.querySelectorAll("[data-zoom]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var sha = btn.dataset.zoom;
        apiGet("gallery/image?sha=" + encodeURIComponent(sha)).then(function (data) {
          if (data && data.data_url) {
            els.imageDialogImg.src = data.data_url;
            els.imageDialogInfo.textContent = "SHA: " + sha.slice(0, 16) + "…";
            els.imageDialog.showModal();
          }
        }).catch(function () {});
      });
    });
    els.galGrid.querySelectorAll("[data-del]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var sha = btn.dataset.del;
        if (!await confirmAction("删除图片", "确定要删除该图片吗？此操作不可恢复。")) return;
        try {
          await apiPost("gallery/delete", { sha: sha });
          showToast("已删除");
          galSearch();
          loadGalStats();
        } catch (e) { showToast(e.message || "删除失败", "error"); }
      });
    });
  }

  els.galSearchBtn.addEventListener("click", galSearch);
  els.galSearch.addEventListener("keydown", function (e) { if (e.key === "Enter") galSearch(); });

  // ====== BIND EVENTS ======
  function bindEvents() {
    // nav
    document.querySelectorAll(".workspace-nav [data-view]").forEach(function (btn) {
      btn.addEventListener("click", function () { switchView(btn.dataset.view); });
    });
    els.refreshBtn.addEventListener("click", async function () {
      setButtonBusy(els.refreshBtn, true, "刷新中…", "刷新数据");
      hideGlobalError();
      var failures = [];
      try { await loadConfig(); } catch (e) { failures.push("配置"); }
      try { await loadLogs(); } catch (e) { failures.push("日志"); }
      try { await loadServers(); } catch (e) { failures.push("调试"); }
      try { await loadGalStats(); } catch (e) { failures.push("图库统计"); }
      try { await galSearch(); } catch (e) { failures.push("图库搜索"); }
      if (failures.length) showGlobalError(failures);
      setButtonBusy(els.refreshBtn, false, "刷新中…", "刷新数据");
    });
    $("retryAllBtn").addEventListener("click", function () { els.refreshBtn.click(); });
  }

  // ====== START ======
  async function start() {
    if (!bridge || typeof bridge.apiGet !== "function") {
      els.cfgContent.innerHTML = '<div class="empty error">AstrBot 页面桥接不可用，请在 AstrBot 内置环境中打开此页面。</div>';
      return;
    }
    try { await bridge.ready(); } catch (e) { /* bridge may already be ready */ }
    bindEvents();
    var initialView = (location.hash || "#config").slice(1);
    switchView(initialView);
    try { await loadConfig(); setStatus("已就绪"); } catch (e) { setStatus("初始化失败", true); }
    try { await loadGalStats(); } catch (e) { /* non-critical */ }
  }

  start();
})();
