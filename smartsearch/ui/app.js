/*
 * SmartSearch launcher UI. Plain JS, no dependencies, no network except the local API.
 *
 * Mock mode (canned data, no server) runs when the token placeholder is still present
 * (index.html opened from disk) or the URL has ?mock=1.
 */
(function () {
  "use strict";

  var TOKEN = String(window.SS_TOKEN || "");
  var MOCK = TOKEN === "" || TOKEN === "{{TOKEN}}" || /(^|[?&])mock=1(&|$)/.test(location.search);
  var DEBOUNCE_MS = 120;
  var MAX_IMAGE_BYTES = 24 * 1024 * 1024;
  var SIMILAR_SOURCES = { files: true, images: true, video: true };
  var PANELS = { cards: true, list: true, grid: true, emoji: true };

  var $ = function (id) { return document.getElementById(id); };
  var els = {
    field: $("field"), input: $("q"), modeSlot: $("mode-slot"), mic: $("mic"), hud: $("hud"),
    filters: $("filters"), results: $("results"), sections: $("sections"),
    backend: $("backend"), mockTag: $("mock-tag"), message: $("message"),
    progress: $("progress"), progressBar: $("progress-bar"),
    counts: $("counts"), latency: $("latency"), toast: $("toast"), drop: $("drop"),
    ql: $("ql"), qlTile: $("ql-tile"), qlTitle: $("ql-title"), qlSub: $("ql-sub"),
    qlOpen: $("ql-open"), qlReveal: $("ql-reveal"), qlClose: $("ql-close"),
    qlBody: $("ql-body"), qlLeft: $("ql-left"), qlHint: $("ql-hint"),
    top: $("top"), footer: $("footer"), note: $("note"), busyDot: $("busy-dot"),
    interp: $("interp"), sortBest: $("sort-best"), sortNew: $("sort-new"), scope: $("scope")
  };

  var state = { ready: false, status: null, sources: [], chips: [], chipLabel: {}, offline: false, selected: {} };
  var win = { h: 0 };                   // last window height requested from the backend
  var FIT_MAX = 600, FIT_MIN = 56, FADE_MS = 120;
  var fitTimer = 0;
  var mode = { type: "text" };          // {type:"similar", label} | {type:"image", url}
  var items = [];                       // flat list of rendered result items, global selection indexes this
  var sel = -1;
  var seq = 0;                          // request counter: responses with another value are stale
  var debTimer = 0, toastTimer = 0, pollTimer = 0, dragDepth = 0;
  var voice = { state: "idle", rec: null, discard: false, started: 0, session: 0, limitTimer: 0, tickTimer: 0 };
  var curTime = null;                   // "time" block of the current text response, or null
  var lit = false;                      // literal search: date and type words ignored (set by the chip's ✕)
  var sortExplicit = false;             // the user picked a sort for the current response
  var secRecs = [];                     // sections with results, in display order
  var dateNodes = [];                   // {node, ts}: relative dates, repainted every minute
  var SORT_KEY = "smartsearch.sort";
  var prefSort = loadSort();            // "best" (search order) or "new" (newest first), remembered
  var scope = null;                     // {folder, name, coverage, on}: opened from File Explorer
  var DEFAULT_PH = "";

  /* ---------- small helpers ---------- */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function pd(e) { e.preventDefault(); }
  function focusInput() { try { els.input.focus(); } catch (e) { /* ignore */ } }
  window.ssFocus = function () { focusInput(); try { els.input.select(); } catch (e) { /* ignore */ } };
  // The app calls this when the launcher opens: null, or the File Explorer folder you came from.
  window.ssScope = function (info) {
    var before = scopeFolder();
    scope = info && info.folder ? { folder: String(info.folder), name: String(info.name || info.folder),
                                    coverage: String(info.coverage || "full"), on: true } : null;
    renderScope();
    if (scopeFolder() !== before && els.input.value.trim()) runSearch();
  };
  function setBusy(on) { els.field.classList.toggle("busy", !!on); }
  function normPanel(p) { return PANELS[p] ? p : "list"; }
  function normTone(t) {
    t = String(t || "").toLowerCase();
    return (t === "ok" || t === "warn" || t === "bad") ? t : "";
  }
  function fmt(v) {
    if (v === undefined || v === null || v === "") return "-";
    if (typeof v === "number") return String(Math.round(v * 1000) / 1000);
    return String(v);
  }
  /* ---------- matched words: the informative query words (server picks them by IDF) in bold ---------- */

  var hlRe = null;
  function termsRegex(terms) {
    if (!Array.isArray(terms) || !terms.length) return null;
    var parts = terms.filter(function (t) { return typeof t === "string" && t.length >= 2; }).map(function (t) {
      var stem = t.length > 3 && /s$/.test(t) ? t.slice(0, -1) : t;  // "awards" also marks "award"
      return stem.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    });
    if (!parts.length) return null;
    try { return new RegExp("(?<![\\p{L}\\p{N}])(?:" + parts.join("|") + ")[\\p{L}\\p{N}]*", "giu"); } catch (e) { return null; }
  }
  // Text of `node` with the matches wrapped in <mark class="hl"> (built as nodes: titles are never HTML).
  function hl(node) {
    if (!hlRe) return node;
    var text = node.textContent, last = 0, m, frag = document.createDocumentFragment();
    hlRe.lastIndex = 0;
    while ((m = hlRe.exec(text)) !== null) {
      if (!m[0]) { hlRe.lastIndex++; continue; }
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      frag.appendChild(el("mark", "hl", m[0]));
      last = m.index + m[0].length;
    }
    if (!last) return node;
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    clear(node);
    node.appendChild(frag);
    return node;
  }

  // A snippet that only repeats the title (shell commands, window titles) is noise.
  function sameText(a, b) {
    var norm = function (s) { return String(s || "").replace(/\s+/g, " ").trim().toLowerCase(); };
    return !!a && norm(a) === norm(b);
  }
  function initial(r) {
    var s = String(r.title || r.badge || "?").trim();
    return s ? s.charAt(0).toUpperCase() : "?";
  }
  function fmtTime(ms) {
    var s = Math.floor(ms / 1000);
    var m = Math.floor(s / 60);
    s = s % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }
  function withToken(url) {
    if (!url) return null;
    if (/^(data:|blob:)/.test(url)) return url;
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);
  }
  function countTotal(c) {
    if (typeof c === "number") return c;
    if (c && typeof c === "object") {
      var t = 0;
      Object.keys(c).forEach(function (k) { if (typeof c[k] === "number") t += c[k]; });
      return t;
    }
    return null;
  }
  function breakdown(c) {
    if (!c || typeof c !== "object") return "";
    return Object.keys(c).map(function (k) { return k + " " + c[k]; }).join(" \u00b7 ");
  }
  function normSources(raw) {
    var out = [];
    if (Array.isArray(raw)) {
      raw.forEach(function (s) {
        if (typeof s === "string") out.push({ name: s, label: s });
        else if (s && typeof s === "object" && s.name && s.enabled !== false) {
          out.push({ name: String(s.name), label: String(s.label || s.name) });
        }
      });
    } else if (raw && typeof raw === "object") {
      Object.keys(raw).forEach(function (k) {
        var v = raw[k];
        var enabled = typeof v === "boolean" ? v : (v && typeof v === "object" ? v.enabled !== false : true);
        var label = v && typeof v === "object" && v.label ? String(v.label) : k;
        if (enabled) out.push({ name: k, label: label });
      });
    }
    return out;
  }

  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { els.toast.classList.remove("show"); }, 2000);
  }

  function copyText(text) {
    var fallback = function () {
      var t = document.createElement("textarea");
      t.value = text;
      t.setAttribute("readonly", "");
      t.style.position = "fixed";
      t.style.opacity = "0";
      document.body.appendChild(t);
      t.select();
      try { document.execCommand("copy"); } catch (e) { /* ignore */ }
      document.body.removeChild(t);
      focusInput();
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(fallback);
    } else {
      fallback();
    }
  }

  /* ---------- API ---------- */

  function api(path, opts) {
    opts = opts || {};
    if (MOCK) return mockApi(path, opts);
    var method = opts.method || (opts.json !== undefined || opts.body !== undefined ? "POST" : "GET");
    var headers = { "X-Token": TOKEN };
    var payload;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(opts.json);
    } else if (opts.body !== undefined) {
      headers["Content-Type"] = opts.type || "application/octet-stream";
      payload = opts.body;
    }
    return fetch(path, { method: method, headers: headers, body: payload, cache: "no-store" })
      .then(function (r) {
        return r.text().then(function (text) {
          var data = null;
          try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
          if (!r.ok) throw new Error((data && data.error) || ("Request failed (" + r.status + ")"));
          return data;
        });
      });
  }

  /* ---------- status polling ---------- */

  function poll() {
    clearTimeout(pollTimer);
    api("/api/status").then(function (s) {
      if (state.offline) { state.offline = false; setNote(""); }
      applyStatus(s);
      var busy = s && (s.state === "starting" || s.state === "indexing");
      pollTimer = setTimeout(poll, busy ? 1500 : 10000);
    }, function () {
      state.offline = true;
      els.message.textContent = "Backend unreachable";
      if (hasQuery()) setNote("Backend unreachable");
      pollTimer = setTimeout(poll, 3000);
    });
  }

  function applyStatus(s) {
    s = s || {};
    state.status = s;
    var wasReady = state.ready;
    state.ready = !!s.ready;

    els.backend.hidden = !s.backend;
    els.backend.textContent = s.backend ? String(s.backend) : "";
    els.message.textContent = s.message ? String(s.message) : "";

    var total = Number(s.total) || 0;
    var done = Number(s.done) || 0;
    els.progress.hidden = !(total > 0);
    if (total > 0) els.progressBar.style.width = Math.min(100, (done * 100) / total).toFixed(1) + "%";

    var n = countTotal(s.counts);
    els.counts.textContent = n === null ? "" : n.toLocaleString() + " items";
    els.counts.title = breakdown(s.counts);

    state.sources = normSources(s.sources);

    // A tiny indicator in the search bar while the model loads or indexing runs.
    var busy = !state.ready || s.state === "indexing";
    els.busyDot.hidden = !busy;
    els.busyDot.title = s.message ? String(s.message) : "Loading model…";

    if (state.ready !== wasReady) renderView();
    else if (!state.ready && hasQuery()) renderLoading();  // keep the download / loading message current
  }

  /* ---------- source chips: only for sections in the current results ---------- */

  function chipLabel(name) {
    var hit = state.sources.filter(function (x) { return x.name === name; })[0];
    return hit ? hit.label : (state.chipLabel[name] || name);
  }

  function renderFilters() {
    clear(els.filters);
    els.filters.hidden = els.results.hidden || state.chips.length === 0;
    var any = Object.keys(state.selected).length > 0;
    if (state.chips.length > 1 || any) {  // "All": the state with no filter, and the way back to it
      var all = el("span", "chip" + (any ? "" : " on"), "All");
      all.setAttribute("role", "button");
      all.setAttribute("aria-pressed", any ? "false" : "true");
      all.title = "Search everything";
      all.addEventListener("mousedown", pd);
      all.addEventListener("click", function () {
        if (!any) return focusInput();
        state.selected = {};
        renderFilters();
        if (mode.type === "text" && els.input.value.trim()) runSearch();
        focusInput();
      });
      els.filters.appendChild(all);
    }
    state.chips.forEach(function (name) {
      var on = !!state.selected[name];
      var label = chipLabel(name);
      var c = el("span", "chip" + (on ? " on" : ""), label);
      c.setAttribute("role", "button");
      c.setAttribute("aria-pressed", on ? "true" : "false");
      c.title = on ? "Stop limiting to " + label : "Limit search to " + label;
      c.addEventListener("mousedown", pd);
      c.addEventListener("click", function () {
        if (state.selected[name]) delete state.selected[name];
        else state.selected[name] = true;
        renderFilters();
        if (mode.type === "text" && els.input.value.trim()) runSearch();
        focusInput();
      });
      els.filters.appendChild(c);
    });
  }

  // While a filter is on, earlier chips stay so the other sources can still be picked.
  function updateChips(sections) {
    var names = [];
    sections.forEach(function (sec) {
      if (!sec || typeof sec !== "object" || typeof sec.source !== "string" || !sec.source) return;
      var filled = (Array.isArray(sec.results) && sec.results.length > 0) || !!sec.widget;
      if (!filled) return;
      if (names.indexOf(sec.source) < 0) names.push(sec.source);
      if (sec.label && !state.chipLabel[sec.source]) state.chipLabel[sec.source] = String(sec.label);
    });
    var out = Object.keys(state.selected).length ? state.chips.slice() : [];
    names.forEach(function (n) { if (out.indexOf(n) < 0) out.push(n); });
    state.chips = out;
  }

  /* ---------- views: bar only, or bar + results + footer ---------- */

  function resetList() {
    closeQL();
    clear(els.sections);
    items = [];
    sel = -1;
    secRecs = [];
    dateNodes = [];
    els.results.scrollTop = 0;
  }

  function renderView() {
    if (!state.ready) { renderLoading(); return; }
    if (mode.type === "text") {
      if (els.input.value.trim()) runSearch();
      else renderEmpty();
    }
  }

  function setNote(text) {
    els.note.textContent = text || "";
    els.note.hidden = !text;
    scheduleFit();
  }

  function renderLoading() {
    curTime = null;
    setBusy(false);
    resetList();
    els.latency.textContent = "";
    var why = state.status && state.status.message ? String(state.status.message) : "Loading model…";
    setNote(hasQuery() ? why : "");
    scheduleFit();
  }

  // Empty query: nothing but the search bar.
  function renderEmpty() {
    if (!state.ready) { renderLoading(); return; }
    curTime = null;
    setBusy(false);
    resetList();
    els.latency.textContent = "";
    setNote("");
    renderInterp();
    scheduleFit();
  }

  function showError(err) {
    curTime = null;
    setBusy(false);
    resetList();
    els.latency.textContent = "";
    setNote(err && err.message ? err.message : "Search failed");
    scheduleFit();
  }

  function showResponse(data, q) {
    setBusy(false);
    data = data || {};
    els.latency.textContent = typeof data.ms === "number" ? data.ms + " ms" : "";
    var emptyText = mode.type === "text" && q ? "No matches for “" + q + "”" : "No matches";
    var sections = Array.isArray(data.sections) ? data.sections : [];
    curTime = mode.type === "text" && data.time && typeof data.time === "object" ? data.time : null;
    sortExplicit = false;
    updateChips(sections);
    setNote("");
    hlRe = mode.type === "text" ? termsRegex(data.terms) : null;
    renderSections(sections, emptyText);
    renderFilters();
    renderInterp();
    renderSortSeg();
  }

  function renderWidget(w) {
    var box = el("div", "widget " + normTone(w.tone));
    var head = el("div", "widget-head");
    head.appendChild(el("span", "dot"));
    head.appendChild(el("span", "widget-title", w.title || "Status"));
    box.appendChild(head);
    var rows = el("div", "widget-rows");
    (Array.isArray(w.rows) ? w.rows : []).forEach(function (row) {
      if (!Array.isArray(row)) return;
      rows.appendChild(el("span", "w-label", row[0]));
      rows.appendChild(el("span", "w-value " + normTone(row[2]), row[1]));
    });
    box.appendChild(rows);
    return box;
  }

  function renderSections(sections, emptyText) {
    resetList();
    showBodyLayout();       // laid out (hidden by opacity) so rows can be measured
    setPending(true);
    var shown = 0;
    sections.forEach(function (sec) {
      if (!sec || typeof sec !== "object") return;
      var results = (Array.isArray(sec.results) ? sec.results : []).filter(function (r) {
        return r && typeof r === "object";
      });
      if (!results.length && !sec.widget) return;

      shown += 1;
      var panel = normPanel(sec.panel);
      var box = el("section", "section");
      var head = el("div", "sec-head");
      head.appendChild(el("span", "sec-label", sec.label || sec.source || "Results"));
      if (shown <= 9) head.appendChild(el("span", "sec-key", "Ctrl+" + shown));
      head.appendChild(el("span", "sec-dbg", "rank " + fmt(sec.rank) + " · intent " + fmt(sec.intent) +
        " · relevance " + fmt(sec.relevance)));
      box.appendChild(head);

      if (sec.widget) box.appendChild(renderWidget(sec.widget));

      if (results.length) {
        var body = el("div", "body " + panel);
        var rec = { items: [], origItems: [], panel: panel, box: box, body: body, more: null, collapsed: false };
        results.forEach(function (r) {
          var it = buildItem(r, panel, shown);
          it.secEl = box;
          it.rec = rec;
          rec.origItems.push(it);      // search order, kept so "Best match" can always go back to it
          body.appendChild(it.el);
        });
        box.appendChild(body);
        secRecs.push(rec);
      }
      els.sections.appendChild(box);
    });

    arrangeSections();
    if (!items.length && !els.sections.firstChild) els.sections.appendChild(noResults(emptyText));
    scheduleFit();
  }

  // Nothing found: say so, then offer whatever narrowed the search (folder, filters, a date it read).
  function noResults(text) {
    var box = el("div", "state");
    box.appendChild(el("div", "state-title", text));
    var ways = el("div", "state-ways");
    function way(label, fn) {
      var b = el("button", "way", label);
      b.type = "button";
      b.tabIndex = -1;
      b.addEventListener("mousedown", pd);
      b.addEventListener("click", fn);
      ways.appendChild(b);
    }
    if (mode.type === "text") {
      if (scope && scope.on) way("Search all of This PC (Tab)", toggleScope);
      if (Object.keys(state.selected).length) {
        way("Search everything, not just " + Object.keys(state.selected).map(chipLabel).join(", "), function () {
          state.selected = {};
          renderFilters();
          runSearch();
          focusInput();
        });
      }
      if (curTime && !lit && (curTime.mode !== "none" || (curTime.exts || []).length)) way("Ignore the date and type words", dismissInterp);
    }
    if (ways.firstChild) box.appendChild(ways);
    else box.appendChild(el("div", "state-sub", "Try other words; search goes by meaning, so describe what's in it."));
    return box;
  }

  // Puts every section in the current sort order, then applies the progressive disclosure.
  // Item objects are reused, so the selection and an open Quick Look survive a re-sort.
  function arrangeSections() {
    var prev = (sel >= 0 && items[sel]) ? items[sel] : null;
    if (prev) prev.el.classList.remove("sel");
    var keepTop = els.results.scrollTop;
    var newest = shownSort() === "new";
    items = [];
    sel = -1;
    secRecs.forEach(function (rec) {
      if (rec.more && rec.more.parentNode) rec.more.parentNode.removeChild(rec.more);
      rec.more = null;
      rec.collapsed = false;
      rec.items = newest
        ? sortNewest(rec.origItems, function (it) { return num(metaOf(it.res).ts); })
        : rec.origItems.slice();
      rec.items.forEach(function (it, j) {
        it.first = j === 0;
        it.shut = false;
        it.el.hidden = false;
        rec.body.appendChild(it.el);
        items.push(it);
      });
    });
    // Progressive disclosure: the best section shows all it has, the others show a few rows.
    secRecs.forEach(function (rec, i) { if (i > 0) limitSection(rec); });
    var idx = prev ? items.indexOf(prev) : -1;
    if (idx >= 0 && !prev.shut) setSel(idx, false);
    else if (items.length) setSel(0, false);
    els.results.scrollTop = keepTop;
    scheduleFit();
  }

  // Two rows for lists and cards, one row for grid tiles. Rows are measured, not guessed.
  function limitSection(rec) {
    var rowsWanted = (rec.panel === "grid" || rec.panel === "emoji") ? 1 : 2;
    var rows = [];
    var hiddenCount = 0;
    rec.items.forEach(function (it) {
      var top = it.el.offsetTop;
      var row = rows.indexOf(top);
      if (row < 0) { rows.push(top); row = rows.length - 1; }
      if (row >= rowsWanted) {
        it.shut = true;
        it.el.hidden = true;
        hiddenCount += 1;
      }
    });
    if (!hiddenCount) return;
    rec.collapsed = true;
    var more = el("button", "more", "Show " + hiddenCount + " more");
    more.type = "button";
    more.tabIndex = -1;
    more.addEventListener("mousedown", pd);
    more.addEventListener("click", function () { expandSection(rec); });
    rec.more = more;
    rec.box.appendChild(more);
  }

  function expandSection(rec) {
    if (!rec.collapsed) return;
    rec.collapsed = false;
    rec.items.forEach(function (it) { it.shut = false; it.el.hidden = false; });
    if (rec.more && rec.more.parentNode) rec.more.parentNode.removeChild(rec.more);
    rec.more = null;
    scheduleFit();
  }

  /* ---------- window height follows the visible content ---------- */

  function hasQuery() { return els.input.value.trim() !== "" || mode.type !== "text"; }
  function hasBody() { return els.sections.firstChild !== null; }

  function scheduleFit() {
    clearTimeout(fitTimer);
    fitTimer = setTimeout(fit, 30);
  }

  function setPending(on) {
    els.results.classList.toggle("pending", !!on);
    els.footer.classList.toggle("pending", !!on);
  }
  function revealBody() { setPending(false); }

  function showBodyLayout() {
    els.results.hidden = false;
    els.footer.hidden = false;
    renderFilters();
    renderInterp();
  }
  function hideBodyLayout() {
    els.results.hidden = true;
    els.footer.hidden = true;
    renderFilters();
    renderInterp();
  }

  // Window height in CSS px: the header, then (when shown) results and footer. The frame is 1px each side.
  function measureH(withBody) {
    var h = els.top.offsetHeight;
    if (withBody) {
      var foot = els.footer.offsetHeight;
      var body = els.sections.offsetHeight + 16;   // 8px top margin of the first section, 8px bottom padding
      h += Math.min(body, Math.max(0, FIT_MAX - h - foot - 2)) + foot;
    }
    return Math.min(FIT_MAX, Math.ceil(h + 2));
  }

  // POST /api/window {height}. Changes of 4px or less are skipped.
  function requestH(h) {
    h = Math.max(FIT_MIN, Math.min(640, Math.round(h)));
    if (Math.abs(h - win.h) <= 4) return Promise.resolve();
    win.h = h;
    if (MOCK) {
      console.log("[mock] window height " + h + "px");
      return Promise.resolve();
    }
    var call = api("/api/window", { json: { height: h } }).then(function () {}, function () {});
    // do not wait forever for the window before showing content
    return Promise.race([call, new Promise(function (res) { setTimeout(res, 250); })]);
  }

  function fit() {
    fitTimer = 0;
    if (QL.open) {                          // the Quick Look wants the full height
      revealBody();
      requestH(FIT_MAX).then(placeQL);
      return;
    }
    if (hasQuery() && hasBody()) {
      showBodyLayout();
      var hOpen = measureH(true);
      if (hOpen > win.h) requestH(hOpen).then(revealBody);   // growing: window first, then content
      else { revealBody(); requestH(hOpen); }                // shrinking or same: content first
      return;
    }
    if (!els.results.hidden) {
      // shrinking to the bar: fade the content out, hide it, then shrink the window
      setPending(true);
      setTimeout(function () {
        if (hasQuery() && hasBody()) { scheduleFit(); return; }
        hideBodyLayout();
        requestH(measureH(false));
      }, FADE_MS);
      return;
    }
    requestH(measureH(false));
  }

  /* ---------- dates: relative on rows and tiles, full in tooltips and Quick Look ---------- */

  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var MON_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December"];
  var DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var DAY_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  function two(n) { return (n < 10 ? "0" : "") + n; }
  function clockOf(d) { return two(d.getHours()) + ":" + two(d.getMinutes()); }
  // Whole calendar days from b to a, counted on local dates so daylight saving does not shift them.
  function dayGap(a, b) {
    return Math.round((Date.UTC(a.getFullYear(), a.getMonth(), a.getDate()) -
      Date.UTC(b.getFullYear(), b.getMonth(), b.getDate())) / 86400000);
  }
  function relDate(ts, now) {
    var d = new Date(ts * 1000);
    var secs = (now.getTime() - d.getTime()) / 1000;
    if (secs >= 0 && secs < 60) return "just now";
    if (secs >= 0 && secs < 3600) return Math.floor(secs / 60) + " min ago";
    var days = dayGap(now, d);
    if (days === 0) return "today " + clockOf(d);
    if (days === 1) return "yesterday " + clockOf(d);
    if (days > 1 && days < 7) return DAY[d.getDay()] + " " + clockOf(d);
    if (d.getFullYear() === now.getFullYear()) return d.getDate() + " " + MON[d.getMonth()];
    return d.getDate() + " " + MON[d.getMonth()] + " " + d.getFullYear();
  }
  function fullDate(ts) {
    var d = new Date(ts * 1000);
    return DAY_LONG[d.getDay()] + " " + d.getDate() + " " + MON_LONG[d.getMonth()] + " " + d.getFullYear() +
      ", " + clockOf(d);
  }
  // A date node that is repainted every minute; the full date is in its tooltip.
  function dateNode(ts, cls) {
    var n = el("span", cls);
    n.textContent = relDate(ts, new Date());
    n.title = fullDate(ts);
    dateNodes.push({ node: n, ts: ts });
    return n;
  }
  function refreshDates() {
    var now = new Date();
    dateNodes.forEach(function (x) { x.node.textContent = relDate(x.ts, now); });
  }
  // Quick Look header: the path, then the full date of the result.
  function fillQLSub(r) {
    clear(els.qlSub);
    var sub = r.subtitle ? String(r.subtitle) : "";
    var ts = num(metaOf(r).ts);
    if (sub) els.qlSub.appendChild(el("span", "ql-path", sub));
    if (ts !== null) els.qlSub.appendChild(el("span", "ql-when", fullDate(ts)));
    els.qlSub.hidden = !sub && ts === null;
  }

  /* ---------- interpretation chips: the date and type words the backend read from the query ---------- */

  // File type words become chips: [name, icon]. Unknown extensions show as they are.
  var EXT_NAME = {
    ".pdf": ["PDF", "📄"], ".docx": ["Word", "📝"], ".pptx": ["PowerPoint", "📊"],
    ".py": ["Python", "🐍"], ".ipynb": ["Notebook", "📓"], ".md": ["Markdown", "📝"],
    ".csv": ["CSV", "📄"], ".json": ["JSON", "📄"]
  };

  function itpChip(icon, text, note, onX) {
    var c = el("span", "itp");
    c.appendChild(el("span", "itp-ico", icon));
    c.appendChild(el("span", "itp-text", text));
    if (note) c.appendChild(el("span", "itp-note", note));
    if (onX) {
      var x = el("button", "itp-x", "✕");
      x.type = "button";
      x.tabIndex = -1;
      x.title = "Search without the date and type words (Ctrl+Backspace)";
      x.setAttribute("aria-label", "Ignore " + text + " and search literally");
      x.addEventListener("mousedown", pd);
      x.addEventListener("click", onX);
      c.appendChild(x);
    }
    return c;
  }

  // Row under the search bar. Shown only with results (or the empty result) for a text query.
  function renderInterp() {
    clear(els.interp);
    var on = mode.type === "text" && hasQuery() && !els.results.hidden;
    var chips = [];
    if (on && lit) {
      var lc = el("span", "itp lit");
      lc.appendChild(el("span", "itp-text", "searching literally"));
      var undo = el("button", "itp-link", "undo");
      undo.type = "button";
      undo.tabIndex = -1;
      undo.title = "Use the date and type words again";
      undo.addEventListener("mousedown", pd);
      undo.addEventListener("click", undoLiteral);
      lc.appendChild(undo);
      chips.push(lc);
    } else if (on && curTime) {
      var t = curTime;
      if (t.mode === "range" && t.label) {
        chips.push(itpChip("📅", String(t.label), t.browse ? "showing everything" : "", dismissInterp));
      } else if (t.mode === "latest") {
        chips.push(itpChip("🕑", "newest first", "", dismissInterp));
      }
      (Array.isArray(t.exts) ? t.exts : []).forEach(function (ext) {
        var hit = has(EXT_NAME, ext) ? EXT_NAME[ext] : null;
        chips.push(itpChip(hit ? hit[1] : "📄", hit ? hit[0] : String(ext), "", dismissInterp));
      });
    }
    els.interp.hidden = chips.length === 0;
    chips.forEach(function (c) { els.interp.appendChild(c); });
  }

  // The chip's ✕: the same query again, with the date and type words ignored.
  function dismissInterp() {
    if (lit) return;
    lit = true;
    renderInterp();
    runSearch();
  }
  function undoLiteral() {
    if (!lit) return;
    lit = false;
    renderInterp();
    runSearch();
    focusInput();
  }

  /* ---------- sort: search order, or newest first by date ---------- */

  function loadSort() {
    try { return localStorage.getItem(SORT_KEY) === "new" ? "new" : "best"; } catch (e) { return "best"; }
  }
  function saveSort(v) {
    try { localStorage.setItem(SORT_KEY, v); } catch (e) { /* ignore */ }
  }
  // "latest" and browse queries already come back newest first, so that is what the toggle shows.
  function forcedNewest() {
    return !!curTime && (curTime.mode === "latest" || curTime.browse === true);
  }
  function shownSort() {
    return forcedNewest() && !sortExplicit ? "new" : prefSort;
  }
  // Newest first by date. Entries without a date keep their order after the dated ones.
  function sortNewest(arr, tsOf) {
    var dated = [], rest = [];
    arr.forEach(function (x) { (tsOf(x) !== null ? dated : rest).push(x); });
    if (!dated.length) return arr.slice();
    dated.sort(function (a, b) { return tsOf(b) - tsOf(a); });
    return dated.concat(rest);
  }
  function pickSort(v) {
    prefSort = v === "new" ? "new" : "best";
    sortExplicit = true;
    saveSort(prefSort);
    if (secRecs.length) arrangeSections();
    renderSortSeg();
  }
  function toggleSort() {
    pickSort(shownSort() === "new" ? "best" : "new");
  }
  function renderSortSeg() {
    var s = shownSort();
    els.sortBest.setAttribute("aria-pressed", s === "best" ? "true" : "false");
    els.sortNew.setAttribute("aria-pressed", s === "new" ? "true" : "false");
    els.sortBest.title = "Best match order (Ctrl+Shift+N)";
    els.sortNew.title = forcedNewest() && !sortExplicit
      ? "This search already returns newest first"
      : "Newest first (Ctrl+Shift+N)";
  }

  /* ---------- result kinds: thumbnails, file tiles, badges ---------- */

  var KINDS = { photo: 1, video: 1, audio: 1, pdf: 1, scan: 1, doc: 1, slides: 1, code: 1, text: 1,
    data: 1, notebook: 1, setting: 1, tool: 1, command: 1, window: 1, emoji: 1, web: 1, shell: 1 };
  var PAPER = { pdf: "PDF", scan: "SCANNED" };
  var GLYPH = { setting: "⚙", tool: "⚒", command: ">_" };
  var KIND_EXT = { text: "TXT", data: "DATA", notebook: "IPYNB", code: "CODE" };
  var LANG_SHORT = { python: "PY", javascript: "JS", typescript: "TS", powershell: "PS", "c#": "C#",
    csharp: "C#", "c++": "C++", cpp: "C++", rust: "RS", go: "GO", golang: "GO", ruby: "RB", bash: "SH",
    shell: "SH", sql: "SQL", html: "HTML", css: "CSS", java: "JAVA", json: "JSON" };

  function has(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); }
  function kindOf(r) {
    var k = String(r.kind || "").toLowerCase();
    return has(KINDS, k) ? k : "";
  }
  function metaOf(r) { return r.meta && typeof r.meta === "object" ? r.meta : {}; }
  function num(v) {
    if (typeof v === "string" && v.trim() !== "") v = Number(v);
    return typeof v === "number" && isFinite(v) ? v : null;
  }
  function fmtSecs(s) {
    s = Math.max(0, Math.floor(s));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    var ss = (sec < 10 ? "0" : "") + sec;
    if (h > 0) return h + ":" + (m < 10 ? "0" : "") + m + ":" + ss;
    return m + ":" + ss;
  }
  function extOf(meta) { return String(meta.ext || "").replace(/^\./, "").toUpperCase().slice(0, 5); }
  function codeLabel(meta) {
    var lang = String(meta.lang || "").trim();
    if (lang) return LANG_SHORT[lang.toLowerCase()] || lang.slice(0, 4).toUpperCase();
    return extOf(meta) || KIND_EXT.code;
  }
  function timedKind(kind, meta) {
    return (kind === "video" || kind === "audio") && num(meta.t) !== null;
  }
  // Badge text: the playback position for timed media, "p. N" for paged files, else the badge as sent.
  function badgeText(r, kind, meta) {
    if (timedKind(kind, meta)) return fmtSecs(num(meta.t));
    var pg = num(meta.page);
    if (has(PAPER, kind) && pg !== null) return "p. " + Math.round(pg);
    return r.badge ? String(r.badge) : "";
  }
  function badgeCls(kind, meta) { return timedKind(kind, meta) ? " time" : ""; }
  function kindClass(node, kind) {
    if (kind) node.classList.add("k-" + kind);
    return node;
  }
  function hashStr(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  // Waveform strip as a data URI SVG (64 units tall). Same seed gives the same bars.
  function waveUri(seed, n, fill) {
    var h = hashStr(String(seed)) || 1;
    var W = n * 4;
    var bars = "";
    for (var i = 0; i < n; i++) {
      h = (Math.imul(h, 1664525) + 1013904223) >>> 0;
      var env = 0.45 + 0.55 * Math.sin(((i + 0.5) / n) * Math.PI);
      var amp = 0.25 + 0.75 * (h / 4294967296);
      var bh = Math.max(4, Math.round(amp * env * 56));
      bars += '<rect x="' + (i * 4 + 1) + '" y="' + ((64 - bh) / 2) + '" width="2" height="' + bh + '" rx="1"/>';
    }
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="64" viewBox="0 0 ' + W +
      ' 64"><g fill="' + fill + '">' + bars + '</g></svg>';
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  }

  function mediaNode(r, cls) {
    var kind = kindOf(r);
    var meta = metaOf(r);
    var inTile = cls === "tile-img";
    var wrap = el("div", cls + " media" + (kind ? " k-" + kind : ""));
    if (has(PAPER, kind)) wrap.classList.add("paperish");
    var base = mediaBase(r, kind, meta, inTile);
    if (base) wrap.appendChild(base);
    if (base && base.classList.contains("ava")) {  // letter tile: a stable hue per name
      wrap.classList.add("ava");
      wrap.style.setProperty("--h", String(hashStr(String(r.title || r.id || "")) % 360));
    }
    if (r.thumb) {
      var img = el("img");
      img.alt = "";
      img.className = kind === "audio" ? "thumb wave-img" : "thumb";
      img.loading = "lazy";
      img.decoding = "async";
      img.addEventListener("load", function () { wrap.classList.add("loaded"); });
      img.addEventListener("error", function () { wrap.classList.add("broken"); });
      img.src = withToken(String(r.thumb));
      wrap.appendChild(img);
    }
    mediaOverlay(r, kind, meta, inTile).forEach(function (n) { wrap.appendChild(n); });
    return wrap;
  }

  // Placeholder drawn under the thumb; hidden once a thumb has loaded (.media.loaded > .fb).
  function mediaBase(r, kind, meta, inTile) {
    if (kind === "video") return el("div", "fb vframe");
    if (kind === "audio") {
      var wave = el("img", "fb wave-img pseudo");
      wave.alt = "";
      wave.loading = "lazy";
      wave.decoding = "async";
      wave.src = waveUri(r.id || r.title || "", inTile ? 40 : 30, "#7f8aa0");
      return wave;
    }
    if (has(PAPER, kind)) return paperBase();
    if (kind === "doc" || kind === "slides") return fileTile(kind, meta);
    if (kind === "code") return codeTile(meta);
    if (kind === "text" || kind === "data" || kind === "notebook") {
      var ext = extOf(meta);
      var badge = r.badge && /^[A-Za-z]{1,5}$/.test(String(r.badge)) ? String(r.badge).toUpperCase() : "";
      return el("div", "fb etile", ext || badge || KIND_EXT[kind]);
    }
    if (r.icon || GLYPH[kind]) return el("span", "ph", r.icon || GLYPH[kind]);
    return el("span", "ph ava", initial(r));
  }

  function paperBase() {
    var p = el("div", "fb paper");
    var lines = el("div", "pl");
    [100, 92, 97, 60, 100, 88, 74].forEach(function (w, i) {
      var b = el("span", i === 0 ? "head" : "");
      b.style.width = w + "%";
      lines.appendChild(b);
    });
    p.appendChild(lines);
    return p;
  }

  function fileTile(kind, meta) {
    var ext = extOf(meta);
    var ppt = kind === "slides" || /^(PPTX?|ODP|KEY)$/.test(ext);
    var f = el("div", "fb ftile " + (ppt ? "ppt" : "word"));
    f.appendChild(el("span", "flet", ppt ? "P" : "W"));
    if (ext) f.appendChild(el("span", "fext", ext));
    return f;
  }

  function codeTile(meta) {
    var f = el("div", "fb ctile");
    f.appendChild(el("span", "clang", codeLabel(meta)));
    var lines = el("div", "clines");
    for (var i = 0; i < 3; i++) lines.appendChild(el("span"));
    f.appendChild(lines);
    return f;
  }

  // Overlays sit above the thumb: play glyph and progress for video, marker for audio,
  // corner tag for paper, hover details for photo tiles, category glyph for settings/tools/commands.
  function mediaOverlay(r, kind, meta, inTile) {
    var out = [];
    var t = num(meta.t), dur = num(meta.dur);
    var frac = (t !== null && dur && dur > 0) ? Math.min(1, Math.max(0, t / dur)) : null;
    if (kind === "video") {
      out.push(el("span", "play"));
      if (frac !== null) {
        var bar = el("span", "vprog");
        bar.appendChild(el("span"));
        bar.firstChild.style.width = (frac * 100).toFixed(1) + "%";
        out.push(bar);
      }
    } else if (kind === "audio") {
      out.push(el("span", "aglyph", "♪"));
      if (frac !== null) {
        var a = Math.max(0, frac - 0.04), b = Math.min(1, frac + 0.04);
        var win = el("span", "wwin");
        win.style.left = (a * 100).toFixed(2) + "%";
        win.style.width = ((b - a) * 100).toFixed(2) + "%";
        var mark = el("span", "wmark");
        mark.style.left = (frac * 100).toFixed(2) + "%";
        out.push(win, mark);
      }
    } else if (has(PAPER, kind)) {
      out.push(el("span", "ctag" + (kind === "scan" ? " scan" : ""), PAPER[kind]));
    } else if (kind === "photo" && inTile) {
      var hov = photoHover(meta);
      if (hov) out.push(el("span", "hov", hov));
    }
    if (has(GLYPH, kind) && r.icon) out.push(el("span", "kg", GLYPH[kind]));
    return out;
  }

  function photoHover(meta) {
    var parts = [];
    var w = num(meta.w), h = num(meta.h);
    if (w && h) parts.push(Math.round(w) + "×" + Math.round(h));
    if (meta.taken) parts.push(String(meta.taken));
    return parts.join(" · ");
  }

  function buildActions(it, acts, extraCls) {
    var box = el("div", "actions" + (extraCls ? " " + extraCls : ""));
    acts.forEach(function (a) {
      var b = el("button", "act", a.label);
      b.type = "button";
      b.tabIndex = -1;
      b.addEventListener("mousedown", pd);
      b.addEventListener("click", function (e) {
        e.stopPropagation();
        e.preventDefault();
        if (a.op === "preview") openQL(it);
        else runOp(it, a.op);
      });
      box.appendChild(b);
    });
    return box;
  }

  function buildCard(it, r, acts, dbg) {
    var kind = kindOf(r), meta = metaOf(r);
    var n = kindClass(el("div", "item card"), kind);
    n.appendChild(mediaNode(r, "card-ico"));
    var txt = el("div", "card-txt");
    txt.appendChild(hl(el("div", "title", r.title || r.id)));
    if (r.subtitle) txt.appendChild(el("div", "sub", r.subtitle));
    if (acts.length) txt.appendChild(buildActions(it, acts, ""));
    txt.appendChild(el("span", "dbg", dbg));
    n.appendChild(txt);
    var badge = badgeText(r, kind, meta);
    if (badge) n.appendChild(el("span", "badge" + badgeCls(kind, meta), badge));
    return n;
  }

  function buildRow(it, r, acts, dbg) {
    var kind = kindOf(r), meta = metaOf(r);
    var n = kindClass(el("div", "item row"), kind);
    n.appendChild(mediaNode(r, "row-ico"));
    var main = el("div", "row-main");
    main.appendChild(hl(el("div", "title", r.title || r.id)));
    if (r.subtitle) main.appendChild(el("div", "sub", r.subtitle));
    if (r.snippet && !sameText(r.snippet, r.title) && !sameText(r.snippet, r.subtitle)) {
      main.appendChild(hl(el("div", kind === "code" ? "snip code" : "snip", r.snippet)));
    }
    if (acts.length) main.appendChild(buildActions(it, acts, ""));
    main.appendChild(el("span", "dbg", dbg));
    n.appendChild(main);
    var end = el("div", "row-end");
    var ts = num(meta.ts);
    if (ts !== null) end.appendChild(dateNode(ts, "when"));
    var badge = badgeText(r, kind, meta);
    if (badge) end.appendChild(el("span", "badge" + badgeCls(kind, meta), badge));
    if (end.firstChild) n.appendChild(end);
    return n;
  }

  function buildTile(it, r, acts, dbg) {
    var kind = kindOf(r), meta = metaOf(r);
    var n = kindClass(el("div", "item tile"), kind);
    n.title = r.title || "";
    var im = mediaNode(r, "tile-img");
    var badge = badgeText(r, kind, meta);
    if (badge) im.appendChild(el("span", "badge overlay" + badgeCls(kind, meta), badge));
    if (acts.length) im.appendChild(buildActions(it, acts, "overlay"));
    n.appendChild(im);
    n.appendChild(hl(el("div", "tile-title", r.title || r.id)));
    var tts = num(meta.ts);
    if (tts !== null) {
      n.classList.add("dated");
      n.appendChild(dateNode(tts, "tile-when"));
    }
    n.appendChild(el("span", "dbg", dbg));
    return n;
  }

  function buildEmoji(it, r, acts, dbg) {
    var n = el("div", "item emo");
    n.title = r.title || "";
    n.appendChild(el("div", "glyph", r.icon || r.title || "?"));
    n.appendChild(el("div", "emo-name", r.title || r.id));
    if (acts.length) n.appendChild(buildActions(it, acts, ""));
    n.appendChild(el("span", "dbg", dbg));
    return n;
  }

  function buildItem(r, panel, secNum) {
    var acts = (Array.isArray(r.actions) ? r.actions : []).filter(function (a) {
      return a && a.label && a.op;
    });
    var hasSimilar = acts.some(function (a) { return a.op === "similar"; });
    if (!hasSimilar && (panel === "grid" || (panel === "list" && SIMILAR_SOURCES[r.source]))) {
      acts.push({ label: "\u2248 Similar", op: "similar" });
    }
    if (previewable(r)) acts.unshift({ label: PREVIEW_LABEL, op: "preview" });
    var it = { res: r, panel: panel, section: secNum, first: false, secEl: null, el: null };
    var dbg = "score " + fmt(r.score);
    var node;
    if (panel === "grid") node = buildTile(it, r, acts, dbg);
    else if (panel === "emoji") node = buildEmoji(it, r, acts, dbg);
    else if (panel === "cards") node = buildCard(it, r, acts, dbg);
    else node = buildRow(it, r, acts, dbg);
    node.addEventListener("mousedown", pd);
    node.addEventListener("click", function () { activate(it); });
    it.el = node;
    return it;
  }

  function activate(it) {
    var idx = items.indexOf(it);
    if (idx >= 0) setSel(idx, false);
    runOp(it, "open");
  }

  function setSel(idx, scroll) {
    if (sel >= 0 && items[sel]) items[sel].el.classList.remove("sel");
    sel = (idx >= 0 && idx < items.length) ? idx : -1;
    if (sel < 0) return;
    var it = items[sel];
    it.el.classList.add("sel");
    if (scroll !== false) (it.first ? it.secEl : it.el).scrollIntoView({ block: "nearest" });
  }

  function moveVertical(dir) {
    if (!items.length) return;
    if (sel < 0) { setSel(0); return; }
    var cur = items[sel].el.getBoundingClientRect();
    var cx = cur.left + cur.width / 2;
    var cands = [];
    for (var i = 0; i < items.length; i++) {
      if (i === sel || items[i].shut) continue;
      var r = items[i].el.getBoundingClientRect();
      if (dir > 0 ? r.top > cur.top + 4 : r.top < cur.top - 4) cands.push({ i: i, r: r });
    }
    // ArrowDown past the last visible row of a collapsed section opens the rest of it first
    var secRec = items[sel].rec;
    if (dir > 0 && secRec && secRec.collapsed && !cands.some(function (c) { return items[c.i].rec === secRec; })) {
      var firstShut = items.filter(function (x) { return x.rec === secRec && x.shut; })[0];
      expandSection(secRec);
      if (firstShut) setSel(items.indexOf(firstShut));
      return;
    }
    if (!cands.length) return;
    // nearest row in that direction, then the column closest to the current item
    var rowTop = cands[0].r.top;
    for (var j = 1; j < cands.length; j++) {
      if (dir > 0 ? cands[j].r.top < rowTop : cands[j].r.top > rowTop) rowTop = cands[j].r.top;
    }
    var best = -1, bestDx = Infinity;
    for (var k = 0; k < cands.length; k++) {
      if (Math.abs(cands[k].r.top - rowTop) > 4) continue;
      var dx = Math.abs(cands[k].r.left + cands[k].r.width / 2 - cx);
      if (dx < bestDx) { bestDx = dx; best = cands[k].i; }
    }
    if (best >= 0) setSel(best);
  }

  function moveHorizontal(dir) {
    if (sel < 0) return false;
    var cur = items[sel];
    if (cur.panel !== "grid" && cur.panel !== "emoji") return false;
    var j = sel + dir;
    if (j < 0 || j >= items.length || items[j].section !== cur.section || items[j].shut) return false;
    setSel(j);
    return true;
  }

  function caretAtEdge(dir) {
    var inp = els.input;
    var n = inp.value.length;
    if (n === 0) return true;
    if (inp.selectionStart !== inp.selectionEnd) return false;
    return dir > 0 ? inp.selectionStart === n : inp.selectionStart === 0;
  }

  function jumpSection(n) {
    for (var i = 0; i < items.length; i++) {
      if (items[i].section === n && items[i].first) { setSel(i); return; }
    }
  }

  /* ---------- running actions ---------- */

  function runOp(it, op) {
    if (!it) return;
    if (op === "similar") { findSimilar(it.res); return; }
    var panel = it.panel;
    api("/api/run", { json: { id: it.res.id, op: op } }).then(function (data) {
      data = data || {};
      if (data.ok === false || data.error) {
        toast(String(data.error || "Action failed"));
        return;
      }
      var msg = "";
      if (typeof data.copy === "string") copyText(data.copy);
      if (data.toast) msg = String(data.toast);
      else if (panel === "emoji") msg = "Copied \uD83C\uDF89";
      else if (typeof data.copy === "string") msg = "Copied";
      if (msg) toast(msg);
    }, function (err) {
      toast(err && err.message ? err.message : "Action failed");
    });
  }

  function revealSelected() {
    var it = items[sel];
    if (!it) return;
    var has = (it.res.actions || []).some(function (a) { return a && a.op === "reveal"; });
    if (!has) { toast("No reveal action for this result"); return; }
    runOp(it, "reveal");
  }

  function hideLauncher() {
    if (MOCK) { toast("Hide requested (mock mode)"); return; }
    api("/api/hide", { json: {} }).catch(function () { /* ignore */ });
  }

  function toggleDebug() {
    document.body.classList.toggle("debug");
    toast("Debug scores " + (document.body.classList.contains("debug") ? "on" : "off"));
  }

  /* ---------- quick look: large preview over the results (Shift+Space, Ctrl+P, or the Preview action) ---------- */

  var PREVIEW_KINDS = { photo: 1, video: 1, audio: 1, pdf: 1, scan: 1, doc: 1, slides: 1, code: 1, text: 1,
    data: 1, notebook: 1 };
  var PREVIEW_IMAGE = { photo: 1, video: 1, pdf: 1, scan: 1 };   // large image from /api/preview
  var PREVIEW_PAGED = { pdf: 1, scan: 1 };                       // page flipping, X-Page / X-Pages
  var PREVIEW_MONO = { code: 1, data: 1, notebook: 1 };          // monospace context
  var PREVIEW_LABEL = "👁 Preview";
  // Overlay state. cache maps a page key to {entry, promise}; entry.url is an object URL to revoke.
  var QL = { open: false, it: null, kind: "", itemId: null, session: 0, page: 1, shown: 1, pages: null, metaPages: null,
    cache: {}, stage: null, img: null, pager: null };

  function previewable(r) { return has(PREVIEW_KINDS, kindOf(r)); }
  // "files:123" -> "123". Ids without a numeric part have no preview endpoint.
  function previewId(r) {
    var s = String(r.id || "");
    var i = s.indexOf(":");
    var n = i >= 0 ? s.slice(i + 1) : s;
    return /^\d+$/.test(n) ? n : null;
  }
  function revokeUrl(u) {
    if (u && /^blob:/.test(u)) { try { URL.revokeObjectURL(u); } catch (e) { /* ignore */ } }
  }

  // GET /api/preview/<id>?size=large[&page=N] as an object URL, plus the X-Page / X-Pages headers.
  function previewFetch(itemId, kind, page) {
    if (MOCK) return mockPreviewPage(itemId, kind, page);
    var path = "/api/preview/" + itemId + "?size=large" + (page ? "&page=" + page : "");
    return fetch(withToken(path), { headers: { "X-Token": TOKEN }, cache: "no-store" }).then(function (r) {
      if (!r.ok) {
        var err = new Error("Request failed (" + r.status + ")");
        err.status = r.status;
        throw err;
      }
      var pg = num(r.headers.get("X-Page"));
      var pgs = num(r.headers.get("X-Pages"));
      return r.blob().then(function (b) {
        return { url: URL.createObjectURL(b), page: pg, pages: pgs };
      });
    });
  }

  // GET /api/context/<id> -> {text, before, after}
  function contextFetch(itemId, kind) {
    if (MOCK) return mockContext(kind);
    return api("/api/context/" + itemId);
  }

  function resetQL() {
    QL.session++;
    pruneQL([]);
    QL.stage = null;
    QL.img = null;
    QL.pager = null;
  }

  function closeQL() {
    if (!QL.open) return;
    resetQL();
    QL.open = false;
    QL.it = null;
    clear(els.qlBody);
    clear(els.qlLeft);
    els.ql.hidden = true;
    focusInput();
    scheduleFit();          // back to the normal height
  }

  // The overlay covers the results box: same top and height, inside the window frame.
  function placeQL() {
    if (!QL.open) return;
    els.ql.style.top = els.results.offsetTop + "px";
    els.ql.style.height = els.results.offsetHeight + "px";
  }

  function openQL(it) {
    var r = it && it.res;
    if (!r || !previewable(r)) return false;
    var idx = items.indexOf(it);
    if (idx >= 0 && idx !== sel) setSel(idx, false);
    resetQL();
    var kind = kindOf(r), meta = metaOf(r);
    QL.open = true;
    QL.it = it;
    QL.kind = kind;
    QL.itemId = previewId(r);
    QL.pages = null;
    QL.metaPages = has(PREVIEW_PAGED, kind) ? num(meta.pages) : null;   // provisional until X-Pages arrives
    QL.page = has(PREVIEW_PAGED, kind) ? Math.max(1, Math.round(num(meta.page) || 1)) : 1;
    fillQLHead(r, kind);
    els.ql.hidden = false;
    placeQL();
    renderQLBody(r, kind, meta);
    scheduleFit();          // asks the window for its full height
    return true;
  }

  function togglePreview() {
    if (QL.open) { closeQL(); return; }
    if (sel >= 0 && items[sel]) openQL(items[sel]);
  }

  // Up/Down while the overlay is open: previous or next previewable result.
  function qlStep(dir) {
    var j = sel < 0 ? (dir > 0 ? 0 : items.length - 1) : sel + dir;
    for (; j >= 0 && j < items.length; j += dir) {
      if (!items[j].shut && previewable(items[j].res)) { setSel(j); openQL(items[j]); return; }
    }
  }

  function fillQLHead(r, kind) {
    els.qlTile.className = "ql-tile media" + (kind ? " k-" + kind : "") + (has(PAPER, kind) ? " paperish" : "");
    clear(els.qlTile);
    var base = mediaBase(r, kind, metaOf(r), false);
    if (base) els.qlTile.appendChild(base);
    els.qlTitle.textContent = String(r.title || r.id || "");
    fillQLSub(r);
    var canReveal = (Array.isArray(r.actions) ? r.actions : []).some(function (a) { return a && a.op === "reveal"; });
    els.qlReveal.hidden = !canReveal;
    els.qlHint.textContent = has(PREVIEW_PAGED, kind)
      ? "← → pages · ↑ ↓ items · Esc close"
      : "↑ ↓ items · Esc close";
  }

  function qlBtn(label, title, cls, fn) {
    var b = el("button", cls, label);
    b.type = "button";
    b.tabIndex = -1;
    b.title = title;
    b.addEventListener("mousedown", pd);
    b.addEventListener("click", fn);
    return b;
  }

  function renderQLBody(r, kind, meta) {
    clear(els.qlBody);
    clear(els.qlLeft);
    QL.pager = null;
    if (kind === "audio") { renderQLAudio(r, meta); return; }
    if (has(PREVIEW_IMAGE, kind)) {
      if (has(PREVIEW_PAGED, kind)) buildQLPager();
      buildQLStage(r, kind, meta);
      if (kind === "video") addQLPlay(meta);
      goQLPage(QL.page, true);
      return;
    }
    loadQLText(r, kind);
  }

  // Waveform at a large size, with the matching-moment marker from the shared audio helpers.
  function renderQLAudio(r, meta) {
    var box = el("div", "ql-av");
    var wave = mediaNode(r, "tile-img");
    wave.classList.add("ql-wave");
    box.appendChild(wave);
    if (r.snippet) box.appendChild(el("div", "ql-snip", String(r.snippet)));
    els.qlBody.appendChild(box);
    addQLPlay(meta);
  }

  function addQLPlay(meta) {
    var t = num(meta.t);
    var label = t !== null ? "▶ Play from " + fmtSecs(t) : "▶ Play";
    els.qlLeft.appendChild(qlBtn(label, "Play in the default player (Enter)", "ql-btn primary", function () {
      if (QL.it) runOp(QL.it, "open");
    }));
  }

  function buildQLPager() {
    var box = el("div", "ql-pager");
    var prev = qlBtn("◀", "Previous page (←)", "ql-pg", function () { goQLPage(QL.page - 1, false); });
    var info = el("span", "ql-pginfo");
    var next = qlBtn("▶", "Next page (→)", "ql-pg", function () { goQLPage(QL.page + 1, false); });
    box.appendChild(prev);
    box.appendChild(info);
    box.appendChild(next);
    els.qlLeft.appendChild(box);
    QL.pager = { prev: prev, next: next, info: info };
  }

  // One page count everywhere: X-Pages once the server has answered, the result's meta until then.
  function updateQLPager() {
    var P = QL.pager;
    if (!P) return;
    var total = QL.pages || QL.metaPages;
    P.info.textContent = total ? "Page " + QL.page + " of " + total : "Page " + QL.page;
    P.prev.disabled = QL.page <= 1;
    P.next.disabled = !!total && QL.page >= total;
  }

  // Image stage: the small thumb (if any) blurred underneath until the large preview has loaded.
  function buildQLStage(r, kind, meta) {
    var stage = el("div", "ql-stage loading");
    if (r.thumb) {
      var ph = el("img", "ql-ph");
      ph.alt = "";
      ph.decoding = "async";
      ph.addEventListener("error", function () { ph.hidden = true; });
      ph.src = withToken(String(r.thumb));
      stage.appendChild(ph);
    }
    var img = el("img", "ql-img");
    img.alt = "";
    img.decoding = "async";
    img.addEventListener("load", function () { if (QL.img === img) qlImgDone(); });
    img.addEventListener("error", function () { if (QL.img === img) qlNoPreview(); });
    stage.appendChild(img);
    stage.appendChild(el("span", "spinner"));
    if (kind === "video" && num(meta.t) !== null) stage.appendChild(el("span", "ql-pill", fmtSecs(num(meta.t))));
    els.qlBody.appendChild(stage);
    QL.stage = stage;
    QL.img = img;
  }

  function qlImgDone() {
    if (!QL.stage) return;
    QL.stage.classList.remove("loading");
    QL.stage.classList.add("ready");
    prefetchQLNext();
  }

  // One page slot per key. Requests are shared; a slot that is pruned or from an old session is revoked.
  function qlPageSlot(p) {
    var key = String(p);
    var slot = QL.cache[key];
    if (slot) return slot;
    var sess = QL.session;
    var itemId = QL.itemId, kind = QL.kind;
    slot = { entry: null, promise: null };
    QL.cache[key] = slot;
    slot.promise = previewFetch(itemId, kind, has(PREVIEW_PAGED, kind) ? p : null).then(function (e) {
      if (sess !== QL.session || QL.cache[key] !== slot) {
        revokeUrl(e.url);
        throw { stale: true };
      }
      slot.entry = e;
      return e;
    }, function (err) {
      if (QL.cache[key] === slot) delete QL.cache[key];
      throw err;
    });
    return slot;
  }

  // Keep only the listed pages (current and next) and revoke the object URLs of the rest.
  function pruneQL(keep) {
    Object.keys(QL.cache).forEach(function (k) {
      if (keep.indexOf(Number(k)) >= 0) return;
      var slot = QL.cache[k];
      if (slot && slot.entry) revokeUrl(slot.entry.url);
      delete QL.cache[k];
    });
  }

  function showQLEntry(e, p) {
    QL.shown = p;
    if (!QL.img || !QL.stage) return;
    if (QL.img.getAttribute("src") === e.url) { qlImgDone(); return; }
    QL.img.src = e.url;
  }

  function goQLPage(p, initial) {
    if (!QL.it) return;
    if (!QL.itemId) { qlNoPreview(); return; }
    if (p < 1 || (QL.pages && p > QL.pages)) return;
    var sess = QL.session;
    QL.page = p;
    updateQLPager();
    if (QL.stage) QL.stage.classList.add("loading");
    qlPageSlot(p).promise.then(function (e) {
      if (sess !== QL.session || QL.page !== p) return;
      if (e.pages) QL.pages = e.pages;
      if (e.page && e.page !== p) QL.page = e.page;   // X-Page: the page the server actually sent
      pruneQL([QL.page, QL.page + 1]);
      showQLEntry(e, QL.page);
      updateQLPager();
    }, function (err) {
      if (!err || err.stale || sess !== QL.session || QL.page !== p) return;
      if (err.status === 404 && p > 1) {
        // First page missing: start at 1. A flip past the end: the previous page is the last one.
        if (initial) { goQLPage(1, false); return; }
        QL.pages = p - 1;
        goQLPage(p - 1, false);
        return;
      }
      if (initial) { qlNoPreview(); return; }
      QL.page = QL.shown;
      updateQLPager();
      if (QL.stage) QL.stage.classList.remove("loading");
      toast("Could not load page " + p);
    });
  }

  // Warm the next page once the current one is on screen (only one page ahead).
  function prefetchQLNext() {
    if (!has(PREVIEW_PAGED, QL.kind) || !QL.itemId) return;
    var next = QL.page + 1;
    if ((QL.pages && next > QL.pages) || QL.cache[String(next)]) return;
    var sess = QL.session;
    qlPageSlot(next).promise.then(null, function (err) {
      if (!err || err.stale || sess !== QL.session) return;
      if (err.status === 404 && (!QL.pages || QL.pages > QL.page)) {
        QL.pages = QL.page;
        updateQLPager();
      }
    });
  }

  function qlNoPreview() {
    if (!QL.open || !QL.it) return;
    QL.stage = null;
    QL.img = null;
    clear(els.qlBody);
    var r = QL.it.res;
    var box = el("div", "ql-empty");
    var big = el("div", "ql-tile big media" + (QL.kind ? " k-" + QL.kind : "") + (has(PAPER, QL.kind) ? " paperish" : ""));
    var base = mediaBase(r, QL.kind, metaOf(r), false);
    if (base) big.appendChild(base);
    box.appendChild(big);
    box.appendChild(el("div", "ql-empty-title", "No preview available"));
    els.qlBody.appendChild(box);
  }

  // Text kinds: context around the match, the match highlighted and scrolled into view.
  function loadQLText(r, kind) {
    var sess = QL.session;
    var wait = el("div", "ql-state");
    wait.appendChild(el("div", "spinner"));
    els.qlBody.appendChild(wait);
    var done = function (ctx) {
      if (sess !== QL.session) return;
      showQLText(r, kind, ctx);
    };
    if (!QL.itemId) { done(null); return; }
    contextFetch(QL.itemId, kind).then(done, function () { done(null); });
  }

  function showQLText(r, kind, ctx) {
    var text = ctx && typeof ctx.text === "string" ? ctx.text : "";
    var before = ctx && typeof ctx.before === "string" ? ctx.before : "";
    var after = ctx && typeof ctx.after === "string" ? ctx.after : "";
    if (!text.trim()) {
      if (!r.snippet) { qlNoPreview(); return; }
      text = String(r.snippet);
      before = "";
      after = "";
    }
    clear(els.qlBody);
    QL.stage = null;
    QL.img = null;
    var mono = has(PREVIEW_MONO, kind);
    var scroller = el("div", "ql-text");
    var root = mono ? el("pre", "ql-code") : el("div", "ql-prose");
    scroller.appendChild(root);
    els.qlBody.appendChild(scroller);
    var st = { root: root, prose: !mono, p: mono ? root : null, first: null };
    addQLSeg(st, before, false, true);
    addQLSeg(st, text, true, false);
    addQLSeg(st, after, false, true);
    if (st.first) centerQL(scroller, st.first);
  }

  // Prose splits on blank lines into paragraphs; code keeps its whitespace in one <pre>.
  // All text goes through textContent, never innerHTML.
  function addQLSeg(st, seg, hit, dim) {
    if (!seg) return;
    var parts = st.prose ? seg.split(/\n[ \t]*\n\s*/) : [seg];
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i];
      if (st.prose && i > 0) st.p = null;
      if (!part) continue;
      if (!st.p) { st.p = el("p"); st.root.appendChild(st.p); }
      var node;
      if (hit) {
        node = el("mark", "ql-hit", part);
        if (!st.first) st.first = node;
      } else {
        node = dim ? el("span", "ql-ctx", part) : document.createTextNode(part);
      }
      st.p.appendChild(node);
    }
  }

  function centerQL(box, node) {
    box.scrollTop = Math.max(0, node.offsetTop - box.clientHeight / 2 + node.offsetHeight / 2);
  }

  /* ---------- quick look mock (no server): canned pages and context ---------- */

  var MOCK_PROSE_CTX = {
    before: "Lease agreement, section 4: notices.\n\nThe tenant must keep the flat clean and report any damage within seven days.\n\n",
    text: "Tenant shall give 60 days notice before ending the lease.",
    after: "\n\nIf notice is not given, the lease renews for another twelve months.\n\nSigned on the first day of the month."
  };
  var MOCK_CODE_CTX = {
    before: "import time\n\n\ndef run(cmd):\n    print(cmd)\n\n\n",
    text: "def reconnect(adapter):\n    run('netsh interface set interface ' + adapter + ' disable')\n" +
      "    time.sleep(3)\n    run('netsh interface set interface ' + adapter + ' enable')\n    return True",
    after: "\n\n\nif __name__ == '__main__':\n    reconnect('Wi-Fi')\n"
  };
  var MOCK_DATA_CTX = {
    before: "{\n  \"rent\": 1800,\n  ",
    text: "\"groceries\": 420",
    after: ",\n  \"utilities\": 130\n}"
  };

  function mockContext(kind) {
    var s = has(PREVIEW_MONO, kind) ? (kind === "data" ? MOCK_DATA_CTX : MOCK_CODE_CTX) : MOCK_PROSE_CTX;
    return new Promise(function (resolve) {
      setTimeout(function () {
        resolve({ text: s.text, before: s.before, after: s.after });
      }, 160);
    });
  }

  // A portrait page with a heading, text lines and the page number. Scanned pages get a grey tint.
  function mockPageUri(p, pages, scanned) {
    var rows = "";
    for (var i = 0; i < 22; i++) {
      var lw = (i % 6 === 5) ? 260 : 420 + ((i * 53) % 90);
      rows += '<rect x="64" y="' + (210 + i * 24) + '" width="' + lw + '" height="8" rx="4" fill="#2a2f3a" opacity=".5"/>';
    }
    var body = '<rect width="612" height="792" fill="#ffffff"/>' +
      '<rect x="64" y="64" width="240" height="36" rx="5" fill="hsl(' + (200 + p * 20) + ',60%,50%)"/>' +
      '<text x="64" y="170" font-family="Segoe UI, Arial, sans-serif" font-size="30" fill="#2a2f3a">Page ' +
      p + ' of ' + pages + '</text>' + rows +
      (scanned ? '<rect width="612" height="792" fill="rgba(120,140,170,.08)"/>' : "");
    return svgUri(body, 612, 792);
  }

  // Paged kinds report 5 pages here (X-Pages semantics); page 6 answers 404 like the server past the end.
  // X-Pages per kind:id. The Lease agreement's meta says 10 pages, the server says 12: the pager follows the server.
  var MOCK_PAGE_TOTAL = { "pdf:1": 12, "pdf:2": 9, "scan:1": 1, "scan:2": 6 };
  function mockPreviewPage(itemId, kind, page) {
    return new Promise(function (resolve, reject) {
      setTimeout(function () {
        var p = page || 1;
        if (has(PREVIEW_PAGED, kind)) {
          var total = MOCK_PAGE_TOTAL[kind + ":" + itemId] || 5;
          if (p > total) { reject({ status: 404 }); return; }
          resolve({ url: mockPageUri(p, total, kind === "scan"), page: p, pages: total });
          return;
        }
        if (kind === "video") resolve({ url: svgThumb("Video frame (mock)", 150, 960, 540), page: null, pages: null });
        else resolve({ url: svgThumb("Photo preview (mock)", 200, 960, 640), page: null, pages: null });
      }, 180);
    });
  }

  /* ---------- search modes ---------- */

  function renderMode() {
    clear(els.modeSlot);
    if (mode.type === "text") return;
    var chip = el("span", "mode-chip");
    if (mode.type === "image") {
      var img = el("img");
      img.alt = "Query image";
      img.decoding = "async";
      img.src = mode.url;
      chip.appendChild(img);
      chip.appendChild(el("span", "mode-text", "Image search"));
    } else {
      chip.appendChild(el("span", "mode-ico", "\u2248"));
      chip.appendChild(el("span", "mode-text", mode.label));
    }
    var x = el("button", "mode-x", "\u00d7");
    x.type = "button";
    x.tabIndex = -1;
    x.title = "Exit";
    x.addEventListener("mousedown", pd);
    x.addEventListener("click", resetToEmpty);
    chip.appendChild(x);
    els.modeSlot.appendChild(chip);
  }

  function exitMode() {
    if (mode.type === "text") return;
    if (mode.type === "image" && mode.url) {
      try { URL.revokeObjectURL(mode.url); } catch (e) { /* ignore */ }
    }
    mode = { type: "text" };
    renderMode();
  }

  function resetToEmpty() {
    seq++;
    clearTimeout(debTimer);
    exitMode();
    lit = false;
    els.input.value = "";
    renderEmpty();
    focusInput();
  }

  /* ---------- search this folder (from File Explorer) ---------- */

  function scopeFolder() { return scope && scope.on ? scope.folder : null; }

  function renderScope() {
    var b = els.scope;
    if (!DEFAULT_PH) DEFAULT_PH = els.input.getAttribute("placeholder") || "";
    clear(b);
    if (!scope) {
      b.hidden = true;
      els.input.setAttribute("placeholder", DEFAULT_PH);
      return;
    }
    b.hidden = false;
    b.classList.toggle("on", scope.on);
    b.appendChild(el("span", "scope-ico", scope.on ? "\uD83D\uDCC1" : "\uD83D\uDDA5\uFE0F"));
    b.appendChild(el("span", "scope-name", scope.on ? scope.name : "This PC"));
    b.appendChild(el("span", "scope-key", "Tab"));
    var names = scope.coverage === "none";
    b.title = scope.on
      ? scope.folder + (names ? "\nNot indexed by SmartSearch: file names only" : "") + "\nTab: search This PC instead"
      : "Searching everything. Tab: search only " + scope.name;
    els.input.setAttribute("placeholder", scope.on
      ? (names ? "Search file names in " : "Search in ") + scope.name + "\u2026"
      : DEFAULT_PH);
  }

  function toggleScope() {
    if (!scope) return false;
    scope.on = !scope.on;
    renderScope();
    if (mode.type === "text" && els.input.value.trim()) runSearch();
    focusInput();
    return true;
  }

  function runSearch() {
    clearTimeout(debTimer);
    if (!state.ready) { renderLoading(); return; }
    if (mode.type !== "text") return;
    var q = els.input.value.trim();
    var my = ++seq;
    if (!q) { renderEmpty(); return; }
    var body = { q: q };
    var names = Object.keys(state.selected);
    if (names.length) body.sources = names;
    if (lit) body.literal = true;
    if (scopeFolder()) body.scope = scopeFolder();
    setBusy(true);
    api("/api/search", { json: body }).then(function (data) {
      if (my !== seq) return;
      showResponse(data, q);
    }, function (err) {
      if (my !== seq) return;
      showError(err);
    });
  }

  function findSimilar(res) {
    var my = ++seq;
    clearTimeout(debTimer);
    exitMode();
    mode = { type: "similar", label: "Similar to " + (res.title || res.id) };
    renderMode();
    els.input.value = "";
    setBusy(true);
    api("/api/similar", { json: { id: res.id } }).then(function (data) {
      if (my !== seq || mode.type !== "similar") return;
      if (data && data.query) mode.label = String(data.query);
      renderMode();
      showResponse(data, "");
    }, function (err) {
      if (my !== seq) return;
      showError(err);
    });
  }

  function searchImage(blob) {
    if (!blob || !blob.size) return;
    if (blob.size > MAX_IMAGE_BYTES) { toast("Image is too large"); return; }
    var my = ++seq;
    clearTimeout(debTimer);
    exitMode();
    mode = { type: "image", url: URL.createObjectURL(blob) };
    renderMode();
    els.input.value = "";
    setBusy(true);
    var url = "/api/search_image" + (scopeFolder() ? "?scope=" + encodeURIComponent(scopeFolder()) : "");
    api(url, { body: blob, type: blob.type || "image/png" }).then(function (data) {
      if (my !== seq || mode.type !== "image") return;
      showResponse(data, "");
    }, function (err) {
      if (my !== seq) return;
      showError(err);
    });
  }

  /* ---------- voice ---------- */

  function updateVoiceUi() {
    var s = voice.state;
    els.mic.classList.toggle("rec", s === "recording" || s === "starting");
    els.mic.classList.toggle("busy", s === "busy" || s === "stopping");
    els.mic.setAttribute("aria-pressed", s === "recording" ? "true" : "false");
    if (s === "recording") {
      els.hud.hidden = false;
      els.hud.classList.add("rec");
      els.hud.textContent = "\u25cf " + fmtTime(Date.now() - voice.started);
    } else if (s === "busy") {
      els.hud.hidden = false;
      els.hud.classList.remove("rec");
      els.hud.textContent = "Transcribing\u2026";
    } else {
      els.hud.hidden = true;
      els.hud.classList.remove("rec");
      els.hud.textContent = "";
    }
  }

  function toggleVoice() {
    if (voice.state === "recording") { stopVoice(false); return; }
    if (voice.state !== "idle") return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof window.MediaRecorder !== "function") {
      toast("Voice input is not available here");
      return;
    }
    var session = ++voice.session;
    voice.state = "starting";
    updateVoiceUi();
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      if (session !== voice.session) {
        stream.getTracks().forEach(function (t) { t.stop(); });
        return;
      }
      startRecording(stream);
    }, function (err) {
      if (session !== voice.session) return;
      voice.state = "idle";
      updateVoiceUi();
      var denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
      toast(denied ? "Microphone permission denied" : "No microphone available");
    });
  }

  function startRecording(stream) {
    var mime = "";
    var cands = ["audio/webm;codecs=opus", "audio/webm"];
    for (var i = 0; i < cands.length; i++) {
      if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(cands[i])) { mime = cands[i]; break; }
    }
    var rec;
    try {
      rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch (e) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      voice.state = "idle";
      updateVoiceUi();
      toast("Could not start recording");
      return;
    }
    var chunks = [];
    rec.addEventListener("dataavailable", function (e) {
      if (e.data && e.data.size) chunks.push(e.data);
    });
    rec.addEventListener("stop", function () {
      stream.getTracks().forEach(function (t) { t.stop(); });
      var discard = voice.discard;
      var blob = new Blob(chunks, { type: rec.mimeType || mime || "audio/webm" });
      voice.rec = null;
      if (discard) { voice.state = "idle"; updateVoiceUi(); return; }
      voice.state = "busy";
      updateVoiceUi();
      transcribe(blob);
    });
    voice.rec = rec;
    voice.discard = false;
    voice.started = Date.now();
    voice.state = "recording";
    rec.start(500);
    voice.limitTimer = setTimeout(function () { stopVoice(false); }, 15000);
    voice.tickTimer = setInterval(updateVoiceUi, 250);
    updateVoiceUi();
  }

  function stopVoice(discard) {
    if (voice.state !== "recording" || !voice.rec) return;
    clearTimeout(voice.limitTimer);
    clearInterval(voice.tickTimer);
    voice.discard = !!discard;
    voice.state = "stopping";
    updateVoiceUi();
    try { voice.rec.stop(); } catch (e) { voice.state = "idle"; updateVoiceUi(); }
  }

  function cancelVoice() {
    if (voice.state === "recording") { stopVoice(true); return; }
    if (voice.state === "starting") { voice.session++; voice.state = "idle"; updateVoiceUi(); }
  }

  function transcribe(blob) {
    if (!blob.size) {
      voice.state = "idle";
      updateVoiceUi();
      toast("Nothing was recorded");
      return;
    }
    api("/api/transcribe", { body: blob, type: blob.type || "audio/webm" }).then(function (data) {
      voice.state = "idle";
      updateVoiceUi();
      var text = data && data.text ? String(data.text).trim() : "";
      if (!text) { toast("Didn't catch that"); return; }
      exitMode();
      lit = false;
      els.input.value = text;
      focusInput();
      try { els.input.setSelectionRange(text.length, text.length); } catch (e) { /* ignore */ }
      runSearch();
    }, function (err) {
      voice.state = "idle";
      updateVoiceUi();
      toast(err && err.message ? err.message : "Transcription failed");
    });
  }

  /* ---------- keyboard ---------- */

  function onEscape() {
    if (voice.state === "recording" || voice.state === "starting") { cancelVoice(); return; }
    if (mode.type !== "text" || els.input.value !== "") { resetToEmpty(); return; }
    hideLauncher();
  }

  function onKey(e) {
    if (e.isComposing) return;
    var ctrl = e.ctrlKey || e.metaKey;
    var k = e.key;

    if (ctrl && !e.altKey) {
      if (k === "d" || k === "D") { e.preventDefault(); toggleDebug(); return; }
      if (k === "m" || k === "M") { e.preventDefault(); toggleVoice(); return; }
      if ((k === "n" || k === "N") && e.shiftKey) { e.preventDefault(); toggleSort(); return; }
      // Ctrl+Backspace drops the date and type reading of the query (only while such chips are shown)
      if (k === "Backspace" && !lit && !els.interp.hidden) { e.preventDefault(); dismissInterp(); return; }
      if (k === "Enter") { e.preventDefault(); revealSelected(); return; }
      if (k === "p" || k === "P") { e.preventDefault(); togglePreview(); return; }
      if (/^[1-9]$/.test(k)) { e.preventDefault(); jumpSection(parseInt(k, 10)); return; }
      return;
    }
    if (e.altKey) return;

    // Shift+Space: quick look. With a result selected it never types a space into the box.
    if (k === " " && e.shiftKey) {
      if (sel >= 0 || QL.open) e.preventDefault();
      togglePreview();
      return;
    }
    if (QL.open) {
      if (k === "Escape") { e.preventDefault(); closeQL(); return; }
      if (k === "ArrowDown" || k === "ArrowUp") { e.preventDefault(); qlStep(k === "ArrowDown" ? 1 : -1); return; }
      if (k === "ArrowLeft" || k === "ArrowRight") {
        if (has(PREVIEW_PAGED, QL.kind)) {
          e.preventDefault();
          goQLPage(QL.page + (k === "ArrowRight" ? 1 : -1), false);
        }
        return;
      }
    }

    var inInput = document.activeElement === els.input;
    if (k === "Tab" && !e.shiftKey && scope) { e.preventDefault(); toggleScope(); return; }
    switch (k) {
      case "ArrowDown":
        e.preventDefault();
        moveVertical(1);
        break;
      case "ArrowUp":
        e.preventDefault();
        moveVertical(-1);
        break;
      case "ArrowRight":
      case "ArrowLeft": {
        var dir = k === "ArrowRight" ? 1 : -1;
        if (inInput && !caretAtEdge(dir)) return;
        if (moveHorizontal(dir)) e.preventDefault();
        break;
      }
      case "Enter":
        if (sel >= 0) { e.preventDefault(); runOp(items[sel], "open"); }
        break;
      case "Escape":
        e.preventDefault();
        onEscape();
        break;
      default:
        break;
    }
  }

  /* ---------- paste and drop of images ---------- */

  function onPaste(e) {
    var clip = e.clipboardData;
    if (!clip || !clip.items) return;
    for (var i = 0; i < clip.items.length; i++) {
      var entry = clip.items[i];
      if (entry.kind === "file" && /^image\//.test(entry.type)) {
        var f = entry.getAsFile();
        if (f) { e.preventDefault(); searchImage(f); return; }
      }
    }
  }

  function hasFiles(e) {
    var t = e.dataTransfer && e.dataTransfer.types;
    return !!t && Array.prototype.indexOf.call(t, "Files") !== -1;
  }
  function onDragEnter(e) {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth++;
    els.drop.classList.add("on");
  }
  function onDragOver(e) {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }
  function onDragLeave(e) {
    if (!hasFiles(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) els.drop.classList.remove("on");
  }
  function onDrop(e) {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    els.drop.classList.remove("on");
    var f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    if (!/^image\//.test(f.type)) { toast("Only images can be dropped here"); return; }
    searchImage(f);
  }

  /* ---------- mock mode (no server): canned responses for UI checks ---------- */

  var mockStarted = Date.now();
  var MOCK_SOURCES = {
    settings: { label: "Settings", enabled: true },
    photos: { label: "Photos", enabled: true },
    scans: { label: "Scanned pages", enabled: true },
    audio: { label: "Voice notes", enabled: true },
    files: { label: "Files", enabled: true },
    commands: { label: "Commands", enabled: true },
    emoji: { label: "Emoji", enabled: true },
    windows: { label: "Windows", enabled: false }
  };
  var MOCK_EMOJI = [
    ["party popper", "\uD83C\uDF89"], ["confetti ball", "\uD83C\uDF8A"], ["clinking glasses", "\uD83E\uDD42"],
    ["sparkles", "\u2728"], ["fireworks", "\uD83C\uDF86"], ["trophy", "\uD83C\uDFC6"],
    ["balloon", "\uD83C\uDF88"], ["birthday cake", "\uD83C\uDF82"]
  ];

  function svgUri(body, w, h) {
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + w + '" height="' + h +
      '" viewBox="0 0 ' + w + ' ' + h + '">' + body + '</svg>';
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  }

  // Photo or video-frame thumb (w x h), with a gradient, hills and a caption.
  function svgThumb(text, hue, w, h) {
    w = w || 320;
    h = h || 240;
    var safe = String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    var body = '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="hsl(' + hue + ',70%,62%)"/>' +
      '<stop offset="1" stop-color="hsl(' + (hue + 40) + ',60%,36%)"/></linearGradient></defs>' +
      '<rect width="' + w + '" height="' + h + '" fill="url(#g)"/>' +
      '<circle cx="' + Math.round(w * 0.78) + '" cy="' + Math.round(h * 0.27) + '" r="' + Math.round(h * 0.11) +
      '" fill="rgba(255,255,255,.35)"/>' +
      '<path d="M0 ' + Math.round(h * 0.83) + ' L' + Math.round(w * 0.28) + ' ' + Math.round(h * 0.5) +
      ' L' + Math.round(w * 0.5) + ' ' + Math.round(h * 0.77) + ' L' + Math.round(w * 0.69) + ' ' +
      Math.round(h * 0.58) + ' L' + w + ' ' + Math.round(h * 0.88) + ' L' + w + ' ' + h + ' L0 ' + h +
      'Z" fill="rgba(0,0,0,.25)"/>' +
      '<text x="14" y="28" font-family="Segoe UI, Arial, sans-serif" font-size="19" fill="#fff">' + safe + '</text>';
    return svgUri(body, w, h);
  }

  // Portrait page (1 : 1.3) with a letterhead block and text lines; scanned pages get a grey tint.
  function paperThumb(hue, scanned) {
    var rows = "";
    for (var i = 0; i < 14; i++) {
      var lw = (i % 5 === 4) ? 110 : 168 + ((i * 37) % 40);
      rows += '<rect x="22" y="' + (62 + i * 16) + '" width="' + lw + '" height="6" rx="3" fill="#2a2f3a" opacity=".55"/>';
    }
    var body = '<rect width="240" height="312" fill="#ffffff"/>' +
      '<rect x="22" y="22" width="120" height="18" rx="3" fill="hsl(' + hue + ',60%,50%)"/>' +
      rows + (scanned ? '<rect width="240" height="312" fill="rgba(120,140,170,.08)"/>' : "");
    return svgUri(body, 240, 312);
  }

  // Mock dates by result id: seconds before now, or an absolute unix time (meta.ts).
  // Settings, emoji, windows and commands have no date.
  var MOCK_D = 86400, MOCK_H = 3600, MOCK_M = 60;
  var MOCK_WHEN = {
    "photos:1": { ago: 58 * MOCK_D }, "photos:2": { ago: 3 * MOCK_D }, "photos:3": { ago: 12 * MOCK_M },
    "photos:4": { ago: 3 * MOCK_H }, "photos:5": { ago: 730 * MOCK_D },
    "scans:1": { ago: 26 * MOCK_H }, "scans:2": { abs: Math.floor(new Date(2024, 2, 5, 9, 30).getTime() / 1000) },
    "audio:1": { ago: 3 * MOCK_D }, "audio:2": { ago: 58 * MOCK_D }, "audio:3": { ago: 2 * MOCK_M },
    "files:1": { ago: 26 * MOCK_H }, "files:2": { abs: Math.floor(new Date(2024, 3, 15, 16, 20).getTime() / 1000) },
    "files:3": { ago: 58 * MOCK_D }, "files:4": { ago: 4 * MOCK_H }, "files:5": { ago: 3 * MOCK_D },
    "files:6": { ago: 5 * MOCK_D }, "files:7": { ago: 26 * MOCK_H },
    "files:8": { abs: Math.floor(new Date(2024, 0, 20, 9, 0).getTime() / 1000) },
    "files:9": { ago: 4 * MOCK_H }, "windows:2": { ago: 40 * MOCK_M }
  };

  function mockItem(source, id, title, o) {
    var key = source + ":" + id;
    var meta = Object.assign({}, o.meta || {});
    var when = MOCK_WHEN[key];
    if (when) meta.ts = when.abs !== undefined ? when.abs : Math.floor(Date.now() / 1000) - when.ago;
    return {
      id: key, source: source, title: title, subtitle: o.sub || "", snippet: o.snip || "",
      score: o.score, thumb: o.thumb || null, icon: o.icon || null, badge: o.badge || null,
      kind: o.kind || null, meta: meta,
      actions: o.actions || [{ label: "Reveal", op: "reveal" }]
    };
  }

  function mockPhotos() {
    return [
      mockItem("photos", 1, "IMG_4021.jpg", { sub: "Mountains at sunset", score: 0.8, kind: "photo",
        thumb: svgThumb("Mountains", 200), meta: { w: 4032, h: 3024, taken: "Aug 14, 2026" } }),
      mockItem("photos", 2, "Screenshot - python error", { sub: "Screenshot", score: 0.75, kind: "photo",
        thumb: svgThumb("python error", 210), meta: { w: 1920, h: 1080, taken: "Oct 2, 2026" } }),
      mockItem("photos", 3, "Lake walk clip", { sub: "Video frame", score: 0.7, kind: "video",
        thumb: svgThumb("Lake walk", 150, 320, 180), meta: { t: 161, dur: 300, ext: ".mp4" } }),
      mockItem("photos", 4, "Standup recording", { sub: "Video moment (no frame yet)", score: 0.65, kind: "video",
        thumb: null, meta: { t: 42, dur: 1800, ext: ".mp4" } }),
      mockItem("photos", 5, "Desk setup", { sub: "Photo", score: 0.6, kind: "photo",
        thumb: svgThumb("Desk setup", 260), meta: { w: 3000, h: 2000 } })
    ];
  }

  function mockScans() {
    return [
      mockItem("scans", 1, "Grocery receipt", { sub: "Scan \u00b7 page 1 of 1", score: 0.62, kind: "scan",
        thumb: paperThumb(150, true), meta: { page: 1, pages: 1 } }),
      // thumb path 404s on purpose: exercises the paper fallback
      mockItem("scans", 2, "Lease agreement scan", { sub: "Scan \u00b7 page 4 of 6", score: 0.5, kind: "scan",
        thumb: "mock-missing/lease-p4.png", meta: { page: 4, pages: 6 } })
    ];
  }

  function mockAudio() {
    return [
      mockItem("audio", 1, "Voice note - party planning", { sub: "Voice note \u00b7 3:34", score: 0.7,
        snip: "bring speakers, order the cake, check the playlist", kind: "audio",
        thumb: waveUri("party planning", 80, "#9aa0a6"), meta: { t: 73, dur: 214, ext: ".m4a" },
        actions: [{ label: "Open", op: "open" }] }),
      mockItem("audio", 2, "Voice memo - standup", { sub: "Voice memo \u00b7 no waveform image", score: 0.55,
        kind: "audio", thumb: null, meta: { t: 0, dur: 95, ext: ".wav" }, actions: [{ label: "Open", op: "open" }] }),
      // thumb path 404s on purpose: falls back to the drawn waveform
      mockItem("audio", 3, "Voice note - grocery list", { sub: "Voice note", score: 0.5, kind: "audio",
        thumb: "mock-missing/grocery-voice.png", meta: { t: 128, dur: 160, ext: ".m4a" },
        actions: [{ label: "Open", op: "open" }] })
    ];
  }

  function mockDocs() {
    return [
      mockItem("files", 1, "Lease agreement 2026.pdf", { sub: "C:\\Users\\me\\Documents\\Legal",
        snip: "Tenant shall give 60 days notice before ending the lease.", score: 0.68, kind: "pdf",
        thumb: paperThumb(200, false), meta: { page: 2, pages: 10, ext: ".pdf" } }),
      // thumb path 404s on purpose: exercises the paper fallback for a PDF
      mockItem("files", 2, "Tax summary 2025.pdf", { sub: "C:\\Users\\me\\Documents\\Finance",
        snip: "Total deductions 4,210.00", score: 0.55, kind: "pdf",
        thumb: "mock-missing/tax-summary-p5.png", meta: { page: 5, pages: 9, ext: ".pdf" } }),
      mockItem("files", 3, "Project proposal.docx", { sub: "C:\\Users\\me\\Documents\\Work",
        snip: "Scope, milestones and budget for the Q4 launch.", score: 0.5, kind: "doc",
        meta: { ext: ".docx" } }),
      mockItem("files", 4, "Launch deck.pptx", { sub: "C:\\Users\\me\\Documents\\Work", score: 0.45,
        kind: "slides", badge: "slide 4", meta: { ext: ".pptx" } })
    ];
  }

  function mockCode() {
    return [
      mockItem("files", 5, "wifi_reconnect.py", { sub: "C:\\Users\\me\\Documents\\scripts", score: 0.6,
        snip: "def reconnect(adapter):\n    run('netsh interface set interface ' + adapter + ' disable')\n" +
          "    time.sleep(3)\n    run('netsh interface set interface ' + adapter + ' enable')\n    return True",
        kind: "code", meta: { lang: "Python", ext: ".py" } }),
      mockItem("files", 6, "traceback-2026-10-02.txt", { sub: "C:\\Users\\me\\Documents\\Logs", score: 0.52,
        snip: 'Traceback (most recent call last): File "app.py", line 42, in main', kind: "text",
        meta: { ext: ".txt" } }),
      mockItem("files", 7, "party planning notes.md", { sub: "C:\\Users\\me\\Documents\\Notes", score: 0.5,
        snip: "Voice note transcript: bring speakers, order the cake, check the playlist.", kind: "text",
        meta: { ext: ".md" } }),
      mockItem("files", 8, "budget-2026.json", { sub: "C:\\Users\\me\\Documents\\Finance", score: 0.45,
        snip: '{"rent": 1800, "groceries": 420}', kind: "data", meta: { ext: ".json" } }),
      mockItem("files", 9, "analysis.ipynb", { sub: "C:\\Users\\me\\Documents\\Work", score: 0.4,
        snip: "Cell 3: df.groupby('month').sum()", kind: "notebook", meta: { ext: ".ipynb" } }),
      // no kind at all: renders with the existing row look
      mockItem("files", 10, "legacy-result.log", { sub: "C:\\Users\\me\\AppData\\Local\\Temp", score: 0.35,
        kind: null, badge: "LOG" })
    ];
  }

  function mockWindows() {
    return [
      mockItem("windows", 1, "Draft - Re: invoice - Mail", { sub: "Window \u00b7 Mail", score: 0.42,
        kind: "window", badge: "Window", actions: [{ label: "Switch to", op: "open" }] }),
      mockItem("windows", 2, "Wi-Fi troubleshooting - Support", { sub: "Browser history \u00b7 Edge", score: 0.38,
        kind: "web", badge: "Web", actions: [{ label: "Open", op: "open" }] })
    ];
  }

  function mockCommands() {
    return [
      mockItem("commands", 1, "docker compose restart web", { sub: "History \u00b7 PowerShell",
        snip: "docker compose restart web", score: 0.72, kind: "command", badge: "Command",
        actions: [{ label: "Run in terminal", op: "open" }] }),
      mockItem("commands", 2, "Get-NetAdapter | Restart-NetAdapter", { sub: "History \u00b7 PowerShell",
        snip: "Get-NetAdapter | Restart-NetAdapter", score: 0.4, kind: "shell", badge: "Shell",
        actions: [{ label: "Run in terminal", op: "open" }] })
    ];
  }

  function mockEmojiItems() {
    return MOCK_EMOJI.map(function (e) {
      return {
        id: "emoji:" + e[0].replace(/ /g, "-"), source: "emoji", title: e[0], subtitle: "", snippet: "",
        score: 0.5, thumb: null, icon: e[1], badge: null, kind: "emoji", meta: {}, actions: []
      };
    });
  }

  function mockSections() {
    return [
      {
        source: "settings", label: "Settings & fixes", panel: "cards", rank: 1,
        intent: "system settings, network and quick fixes", relevance: 0.91,
        widget: { title: "Network", tone: "warn", rows: [
          ["Wi-Fi", "HomeNet-5G \u00b7 38% signal", "warn"],
          ["Internet", "Reachable", "ok"],
          ["Adapter", "Intel Wi-Fi 6E AX211"]
        ] },
        results: [
          mockItem("settings", "wifi", "Wi-Fi settings", { sub: "Networks, saved profiles, airplane mode",
            score: 0.91, kind: "setting", icon: "\u2301", badge: "Settings", actions: [{ label: "Open", op: "open" }] }),
          mockItem("settings", "fix-wifi-reset", "Reset network adapter", { sub: "Release and renew the IP address",
            score: 0.84, kind: "tool", icon: "\u2699", badge: "Fix", actions: [{ label: "Run", op: "open" }] }),
          mockItem("settings", "power", "Power & sleep", { sub: "Screen timeout and sleep mode",
            score: 0.58, kind: "setting", icon: "\u23FB", badge: "Settings", actions: [{ label: "Open", op: "open" }] })
        ]
      },
      { source: "photos", label: "Photos & video", panel: "grid", rank: 2,
        intent: "pictures and video moments", relevance: 0.8, results: mockPhotos() },
      { source: "scans", label: "Scanned pages", panel: "grid", rank: 3,
        intent: "scanned paper, receipts and letters", relevance: 0.7, results: mockScans() },
      { source: "audio", label: "Voice notes", panel: "list", rank: 4,
        intent: "spoken notes and memos", relevance: 0.66, results: mockAudio() },
      { source: "files", label: "Documents", panel: "list", rank: 5,
        intent: "PDFs, Word files and slides", relevance: 0.63, results: mockDocs() },
      { source: "files", label: "Code, notes & data", panel: "list", rank: 6,
        intent: "code, notes, data and notebooks", relevance: 0.6, results: mockCode() },
      { source: "windows", label: "Windows & web", panel: "list", rank: 7,
        intent: "open windows and pages", relevance: 0.45, results: mockWindows() },
      { source: "commands", label: "Commands", panel: "list", rank: 8,
        intent: "shell commands you ran before", relevance: 0.6, results: mockCommands() },
      { source: "emoji", label: "Emoji", panel: "emoji", rank: 9,
        intent: "emoji by name or mood", relevance: 0.4, results: mockEmojiItems() }
    ];
  }

  // A small stand-in for the backend's query reading: date words give a range or "latest",
  // file words give extensions. Returns the "time" block, or null when there is nothing to read.
  var MOCK_STOP = { from: 1, in: 1, the: 1, my: 1, all: 1, of: 1, a: 1, an: 1, and: 1, show: 1, me: 1,
    files: 1, file: 1, docs: 1, on: 1, since: 1, during: 1 };
  var MOCK_EXT_WORD = { pdf: ".pdf", docx: ".docx", word: ".docx", pptx: ".pptx", slides: ".pptx",
    powerpoint: ".pptx", py: ".py", python: ".py", ipynb: ".ipynb", notebook: ".ipynb", md: ".md",
    markdown: ".md", csv: ".csv", json: ".json" };
  function mockParseTime(raw, nowS) {
    var D = 86400;
    var now = new Date(nowS * 1000);
    var midnight = function (off) {
      return Math.floor(new Date(now.getFullYear(), now.getMonth(), now.getDate() + off).getTime() / 1000);
    };
    var q = " " + String(raw || "").toLowerCase().replace(/\s+/g, " ").trim() + " ";
    var t = { mode: "none", label: "", start: null, end: null, browse: false, exts: [] };
    var rest = q;
    var ranges = [
      { re: / last week /, label: "last week", start: nowS - 7 * D, end: nowS },
      { re: / last month /, label: "last month", start: nowS - 30 * D, end: nowS },
      { re: / yesterday /, label: "yesterday", start: midnight(-1), end: midnight(0) },
      { re: / today /, label: "today", start: midnight(0), end: nowS }
    ];
    var year = q.match(/ ((?:19|20)\d{2}) /);
    var hit = null;
    ranges.forEach(function (r) { if (!hit && r.re.test(q)) hit = r; });
    if (!hit && year) {
      var y = Number(year[1]);
      hit = {
        re: new RegExp(" " + year[1] + " "), label: year[1],
        start: Math.floor(new Date(y, 0, 1).getTime() / 1000),
        end: Math.floor(new Date(y + 1, 0, 1).getTime() / 1000)
      };
    }
    if (hit) {
      t.mode = "range";
      t.label = hit.label;
      t.start = hit.start;
      t.end = hit.end;
      rest = rest.replace(hit.re, " ");
    }
    if (/ (latest|newest) /.test(q)) {
      rest = rest.replace(/ (latest|newest) /, " ");
      if (!hit) { t.mode = "latest"; t.label = "latest"; }
    }
    rest.trim().split(" ").forEach(function (w) {
      if (w && has(MOCK_EXT_WORD, w) && t.exts.indexOf(MOCK_EXT_WORD[w]) < 0) t.exts.push(MOCK_EXT_WORD[w]);
    });
    var words = rest.trim().split(" ").filter(function (w) {
      return w && !has(MOCK_STOP, w) && !has(MOCK_EXT_WORD, w);
    });
    t.browse = t.mode === "range" && words.length === 0;
    if (t.mode === "none" && !t.exts.length) return null;
    return t;
  }
  // Keeps the items the reading asks for; sections left empty are dropped.
  function mockApplyTime(list, t) {
    var out = [];
    list.forEach(function (sec) {
      var keep = sec.results.filter(function (r) {
        var m = metaOf(r);
        if (t.exts.length && t.exts.indexOf(String(m.ext || "").toLowerCase()) < 0) return false;
        if (t.mode === "range") {
          var ts = num(m.ts);
          if (ts === null || ts < t.start || ts > t.end) return false;
        }
        return true;
      });
      if (keep.length) out.push(Object.assign({}, sec, { results: keep }));
    });
    return out;
  }

  function mockSearch(q, sources, literal) {
    if (/^zzz/i.test(q)) return { query: q, ms: 9, sections: [], time: null };
    var nowS = Math.floor(Date.now() / 1000);
    var t = literal ? null : mockParseTime(q, nowS);
    var list = mockSections().filter(function (s) {
      return !sources || !sources.length || sources.indexOf(s.source) !== -1;
    });
    if (t) list = mockApplyTime(list, t);
    if (t && (t.mode === "latest" || t.browse)) {
      list = list.map(function (sec) {
        return Object.assign({}, sec, {
          results: sortNewest(sec.results, function (r) { return num(metaOf(r).ts); })
        });
      });
    }
    console.log("[mock] /api/search literal=" + (literal ? "true" : "false") + " q=" + JSON.stringify(q) +
      " time=" + (t ? t.mode : "null"));
    var terms = (q.toLowerCase().match(/[a-z0-9]{3,}/g) || []).filter(function (w) { return !/^(the|and|for|from)$/.test(w); });
    return { query: q, ms: 38 + Math.round(Math.random() * 30), sections: list, time: t, terms: terms };
  }

  function mockSimilar(id) {
    var base = id.split(":").pop() || "item";
    return {
      query: "Similar to " + base, ms: 41, sections: [{
        source: "photos", label: "Similar photos", panel: "grid", rank: 1,
        intent: "visually similar images", relevance: 0.86, results: mockPhotos().slice(1, 5)
      }]
    };
  }

  function mockRun(id, op) {
    if (/missing/.test(id)) return { ok: false, error: "item no longer exists" };
    if (id.indexOf("emoji:") === 0) {
      var hit = mockEmojiItems().filter(function (e) { return e.id === id; })[0];
      return { ok: true, copy: hit ? hit.icon : "" };
    }
    return { ok: true, toast: (op === "reveal" ? "Showing in Explorer" : "Opened") + " (mock)" };
  }

  function mockStatus() {
    var t = Date.now() - mockStarted;
    var common = {
      backend: "GPU \u00b7 CUDA",
      counts: { files: 12840, photos: 3120, settings: 42, commands: 640, emoji: 3800 },
      sources: MOCK_SOURCES
    };
    if (t < 1500) {
      return Object.assign({ state: "starting", message: "Loading EmbeddingGemma 2\u2026", ready: false, done: 0, total: 0 }, common);
    }
    if (t < 6000) {
      var total = 400;
      var done = Math.min(total, Math.round((t - 1500) / 4500 * total));
      return Object.assign({ state: "indexing", message: "Photos: indexing " + done + " of " + total,
        ready: true, done: done, total: total }, common);
    }
    return Object.assign({ state: "idle", message: "Index up to date", ready: true, done: 0, total: 0 }, common);
  }

  function mockRoute(path, opts) {
    var json = opts.json || {};
    if (path === "/api/status") return mockStatus();
    if (path === "/api/search") return mockSearch(String(json.q || ""), json.sources, json.literal === true);
    if (path === "/api/search_image") return mockSearch("", null);
    if (path === "/api/similar") return mockSimilar(String(json.id || ""));
    if (path === "/api/run") return mockRun(String(json.id || ""), json.op || "open");
    if (path === "/api/transcribe") return { text: "wifi keeps disconnecting", ms: 812 };
    if (path === "/api/hide") return { ok: true };
    throw new Error("mock: no route " + path);
  }

  function mockApi(path, opts) {
    path = path.split("?")[0];
    var delay = path === "/api/transcribe" ? 900 : 90 + Math.random() * 110;
    return new Promise(function (resolve, reject) {
      setTimeout(function () {
        try { resolve(mockRoute(path, opts)); } catch (e) { reject(e); }
      }, delay);
    });
  }

  /* ---------- wiring ---------- */

  function init() {
    if (MOCK) els.mockTag.hidden = false;

    els.input.addEventListener("input", function () {
      closeQL();
      lit = false;            // a typed query always reads its date and type words again
      renderInterp();
      if (mode.type !== "text") exitMode();
      clearTimeout(debTimer);
      if (!els.input.value.trim() && mode.type === "text") {
        seq++;                 // a search still in flight must not bring results back
        renderEmpty();
        return;
      }
      debTimer = setTimeout(runSearch, DEBOUNCE_MS);
    });

    els.mic.addEventListener("mousedown", pd);
    els.mic.addEventListener("click", toggleVoice);
    els.results.addEventListener("mousedown", pd);

    // Quick look buttons keep focus in the search box, like the result actions do.
    [els.qlOpen, els.qlReveal, els.qlClose].forEach(function (b) { b.addEventListener("mousedown", pd); });
    els.qlOpen.addEventListener("click", function () { if (QL.it) runOp(QL.it, "open"); });
    els.qlReveal.addEventListener("click", function () { if (QL.it) runOp(QL.it, "reveal"); });
    els.qlClose.addEventListener("click", closeQL);
    window.addEventListener("resize", placeQL);

    document.addEventListener("keydown", onKey);
    document.addEventListener("paste", onPaste);
    document.addEventListener("dragenter", onDragEnter);
    document.addEventListener("dragover", onDragOver);
    document.addEventListener("dragleave", onDragLeave);
    document.addEventListener("drop", onDrop);
    window.addEventListener("focus", focusInput);

    els.scope.addEventListener("mousedown", pd);
    els.scope.addEventListener("click", toggleScope);
    els.sortBest.addEventListener("mousedown", pd);
    els.sortNew.addEventListener("mousedown", pd);
    els.sortBest.addEventListener("click", function () { pickSort("best"); focusInput(); });
    els.sortNew.addEventListener("click", function () { pickSort("new"); focusInput(); });
    // relative dates stay current while the window is on screen
    setInterval(function () { if (!document.hidden) refreshDates(); }, 60000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refreshDates(); });

    initDrag();
    renderLoading();
    renderSortSeg();
    focusInput();
    poll();
  }

  // pywebview moves the window while a drag region is held; we only tell the app where it ended up so the
  // next Alt+Space opens it there. Double-click a drag region to go back to the centre.
  function initDrag() {
    var start = null;
    function isRegion(t) { return t && t.classList && t.classList.contains("pywebview-drag-region"); }
    document.addEventListener("mousedown", function (e) {
      start = e.button === 0 && isRegion(e.target) ? { x: e.screenX, y: e.screenY } : null;
      if (start) e.preventDefault();          // no text selection or focus change while dragging
    }, true);
    window.addEventListener("mouseup", function (e) {
      if (!start) return;
      var far = Math.abs(e.screenX - start.x) + Math.abs(e.screenY - start.y) > 3;
      start = null;
      if (far && !MOCK) api("/api/window", { json: { moved: true } }).then(function () {}, function () {});
      focusInput();
    });
    document.addEventListener("dblclick", function (e) {
      if (!isRegion(e.target) || MOCK) return;
      api("/api/window", { json: { recenter: true } }).then(function () {}, function () {});
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
