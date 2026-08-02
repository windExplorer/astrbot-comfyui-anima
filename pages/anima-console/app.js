(function () {
  "use strict";

  // bridge 在 start() 中通过 getBridge() 异步获取，不要在此处提前固定取值。
  let bridge = null;

  const state = {
    config: {},
    configDirty: false,
    logs: [],
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
  // AstrBot 的 bridge 会自动拼接 /api/plugins/extensions/<plugin_name>/ 前缀，
  // 后端路由注册在 /<plugin_name>/page/... 下，因此 endpoint 需包含 "page/" 来匹配
  // 完整路径，即最终请求为
  // /api/plugins/extensions/<plugin_name>/page/<endpoint>。
  // bridge 对 {status:"ok",data} 自动解包为 data；
  // AstrBot bridge 的 apiGet/apiPost 会把后端 json_response(value) 的响应体原样
  // resolve 出来。成功时 value 就是数据本身（config 对象 / 数组 / {lines,...} 等，
  // 没有 status 字段）；失败时响应体为 {status:"error", message:...}。这里统一
  // 把 error 形态的返回值转成 throw，使调用处能走 catch 分支。
  var API_PREFIX = "page/";

  function _unwrap(res) {
    if (res && typeof res === "object" && res.status === "error") {
      throw new Error(res.message || "请求失败");
    }
    return res;
  }

  // 给桥接请求加超时，避免接口 hang 时前端永远停在「正在读取…」的空壳状态。
  async function withTimeout(promise, ms, label) {
    let timer = null;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(label + " 超时（" + ms / 1000 + "s 无响应，可能后端路由未注册或插件未重载）")), ms);
    });
    try {
      return await Promise.race([promise, timeout]);
    } finally {
      clearTimeout(timer);
    }
  }

  async function apiGet(endpoint, params) {
    const p = bridge.apiGet(API_PREFIX + endpoint, params || {});
    return _unwrap(await withTimeout(p, 10000, "GET " + endpoint));
  }

  async function apiPost(endpoint, body) {
    const p = bridge.apiPost(API_PREFIX + endpoint, body || {});
    return _unwrap(await withTimeout(p, 10000, "POST " + endpoint));
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
    if (name === "gallery" && !state.galResults.length) galSearch();
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

  // 渲染单个基础字段（bool / string / int / float / text / 带 slider）
  function renderField(path, field, value) {
    var type = field.type || "string";
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

  // ====== LOGS ======
  async function loadLogs() {
    try {
      var data = await apiGet("logs");
      // 后端 apiGet 已被桥接解包为 data 本身：{ lines:[...], total:n }
      state.logs = (data && Array.isArray(data.lines)) ? data.lines : (Array.isArray(data) ? data : []);
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
      if (q) params.keyword = q;
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
      var thumb = img.thumb || "";
      return '<div class="gal-card">' +
        '<img src="' + escapeHtml(thumb) + '" data-sha="' + escapeHtml(sha) + '" alt="' + escapeHtml(prompt.slice(0, 80)) + '" loading="lazy" />' +
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
      try { await loadGalStats(); } catch (e) { failures.push("图库统计"); }
      try { await galSearch(); } catch (e) { failures.push("图库搜索"); }
      if (failures.length) showGlobalError(failures);
      setButtonBusy(els.refreshBtn, false, "刷新中…", "刷新数据");
    });
    $("retryAllBtn").addEventListener("click", function () { els.refreshBtn.click(); });
  }

  // ====== START ======
  // AstrBot 的桥接对象由宿主在页面加载后异步注入，可能晚于本脚本执行，
  // 因此不要在一开始就固定取值，而是轮询等待（最多约 8 秒）。
  let bridge = null;

  async function getBridge() {
    for (let i = 0; i < 80; i += 1) {
      const candidate = window.AstrBotPluginPage;
      if (candidate && typeof candidate.apiGet === "function") {
        try {
          if (candidate.ready) {
            // 防止 ready() 在某些环境下永不 resolve 导致一直"正在连接…"
            await Promise.race([
              candidate.ready(),
              new Promise((resolve) => setTimeout(resolve, 3000)),
            ]);
          }
        } catch (e) { /* already ready */ }
        return candidate;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return null;
  }

  async function start() {
    bridge = await getBridge();
    if (!bridge) {
      els.cfgContent.innerHTML = '<div class="empty error">AstrBot 页面桥接不可用，请在 AstrBot 内置环境中打开此页面。</div>';
      els.globalError.hidden = false;
      els.globalErrorMessage.textContent = "未能获取 AstrBot 页面桥接，请确认在 AstrBot Dashboard 的插件 WebUI 中打开。";
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
