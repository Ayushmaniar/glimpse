// Act 3: ASK. The launcher (DOM glass) with eight real searches. Photo searches type, Enter, fly their real results out of
// the physics pile into a grid, and open the best one full screen. Text searches type, Enter, and list real rows (or
// settings cards) that slide in, then open a Quick Look: the PDF page, the code around the hit, or the settings card.
// Everything is a pure function of t. Panel, rows, text and captions are DOM; the flights, the photo heroes and the page
// are drawn on one overlay canvas above the glass (inside our own root element).
// Beats come from TL (q.keys, q.words, q.listen, q.enter, q.hero, TL.arrive, TL.b). No search index is hardcoded.
(function () {
  // one step per search; a rapid slot expands into its quick searches, which share one caption and have no hero
  const Q = [];
  TL.QUERIES.forEach((q) => {
    const M = MEDIA.queries[q.k];
    if (!q.subs) { Q.push({ ...q, qk: q.k, M }); return; }
    q.subs.forEach((sb) => Q.push({ ...sb, kind: "text", qk: q.k, sub: sb.i, rapid: true, M: M.subs[sb.i], words: [], listen: null,
                                    hero: Infinity, caption: sb.i === 0 ? q.caption : null, capTo: q.end - TL.b(0.5) }));
  });
  const N = Q.length, BEAT = TL.BEAT, END = TL.ACT.brain[0];
  const MQ = Q.map((s) => s.M);
  const arrive = (k, j) => (Q[k].rapid ? TL.arrive(Q[k].qk, j, Q[k].sub) : TL.arrive(Q[k].qk, j));
  // a quick search that follows another of the same slot: the panel stays open and the rows swap on Enter
  const cont = (k) => k > 0 && !!Q[k].rapid && !!Q[k - 1].rapid;
  const S = Q.map((q) => q.start), E = Q.map((q) => q.enter), HR = Q.map((q) => q.hero);
  const VK = Q.findIndex((q) => q.voice);                      // the voice search
  const LISTEN = VK >= 0 ? Q[VK].listen : [1e9, 1e9];
  const isText = (k) => Q[k].kind === "text";
  const LX = LAUNCHER.x, LY = LAUNCHER.y, LW = LAUNCHER.w, BAR = LAUNCHER.bar;
  const MAXR = TL.MAX_RESULTS;
  const GRID = { x: LAUNCHER.results.x, w: LAUNCHER.results.w, gap: 12 };
  const TILE_W = (GRID.w - 3 * GRID.gap) / 4, TILE_H = 200;
  const VIDEO_W = 2 * TILE_W + GRID.gap, VIDEO_H = 320;
  const ROW_H = 120, ROW_GAP = 10, CARD_H = 124, EMO_W = 150, EMO_H = 112;
  const HEAD_Y = 264, HEAD_GAP = 36, BASE_H = 136, CHIP_H = 40, STATUS_Y = 226;
  // animation durations (not beats)
  const GROW = TL.b(0.75), EXPAND = 0.5, COLLAPSE = 0.3, CAP_IN = 0.15, CLEAR = TL.b(0.25), FALL = 0.5;
  const VIDEO_FROM = { x: 960, y: 980, w: 150, h: 84, a: 0 };   // the video moment rises from the bottom centre of the pile
  const HL = "background:rgba(132,169,255,.22);color:#d4e3ff;border-radius:4px;padding:0 2px";
  const QHL = "color:#9dbcff;font-weight:600;text-shadow:0 0 18px rgba(132,169,255,.55);border-bottom:2px solid #84a9ff";

  let root, panel, cv, g, shade, wave, bars = [], qSpan, qPh, caret, enterKey, mic, rings = [], chipL, chipT;
  const LAY = [], BY = [], capEls = [], statusEls = [], headEls = [], rowEls = [], pillEls = {}, progEls = {},
        quoteEls = {}, codeEls = {}, bigEls = {};

  const rx = (x) => x - LX, ry = (y) => y - LY;
  const kS = (t) => { let k = -1; for (let i = 0; i < N; i++) if (t >= S[i]) k = i; return k; };
  const kE = (t) => { let k = -1; for (let i = 0; i < N; i++) if (t >= E[i]) k = i; return k; };
  const nextS = (k) => (k < N - 1 ? S[k + 1] : null);
  const capEnd = (k) => (nextS(k) !== null ? nextS(k) - TL.b(0.5) : END - TL.b(1));
  // when a search's rows leave: at the next search, or (inside a rapid slot) when the next quick search hits Enter
  const leave = (k) => (k < N - 1 && cont(k + 1) ? E[k + 1] : nextS(k));
  const headEnd = (k) => leave(k);

  const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // escaped text with the query terms wrapped in a span (case-insensitive, whole words)
  function hiText(text, terms, style) {
    const e = esc(text);
    const ts = (terms || []).filter((x) => x.length > 1).sort((a, b) => b.length - a.length);
    if (!ts.length) return e;
    const re = new RegExp("\\b(" + ts.map(reEsc).join("|") + ")\\b", "gi");
    return e.replace(re, (m) => `<span style="${style}">${m}</span>`);
  }
  function el(tag, style, parent, html) {
    const d = document.createElement(tag);
    if (style) d.style.cssText = style;
    if (html != null) d.innerHTML = html;
    if (parent) parent.appendChild(d);
    return d;
  }

  // ---- layout ------------------------------------------------------------------------------------------
  function layoutFor(k) {
    const M = MQ[k];
    const hasVideo = !!M.video;
    const nPh = Math.min(M.images.length, MAXR - (hasVideo ? 1 : 0));
    const kinds = [];
    if (hasVideo) kinds.push("video");
    if (nPh > 0) kinds.push("photo");
    const items = [], heads = [];
    let y = HEAD_Y, bottom = HEAD_Y, slot = 0;
    kinds.forEach((kind) => {
      heads.push({ text: kind === "video" ? "Video moments" : "Photos & screenshots", y });
      const gy = y + HEAD_GAP;
      if (kind === "video") {
        items.push({ kind, slot: slot++, body: 0, src: `${M.video.dir}/001.jpg`, badge: M.video.badge,
                     x: GRID.x, y: gy, w: VIDEO_W, h: VIDEO_H });
        y = gy + VIDEO_H;
      } else {
        for (let i = 0; i < nPh; i++) {
          const r = Math.floor(i / 4), c = i % 4;
          items.push({ kind, slot: slot++, body: i, src: M.images[i].card,
                       x: GRID.x + c * (TILE_W + GRID.gap), y: gy + r * (TILE_H + GRID.gap), w: TILE_W, h: TILE_H });
        }
        const rows = Math.ceil(nPh / 4);
        y = gy + rows * TILE_H + (rows - 1) * GRID.gap;
      }
      bottom = y;
      y += 18;
    });
    const total = Object.values(M.sections).reduce((a, b) => a + b, 0);
    return { items, heads, panelH: bottom + 24 - LY, total };
  }

  // text results: rows (list), cards (settings) and emoji tiles, grouped by section as the app shows them
  function layoutText(k) {
    const rows = MQ[k].rows || [];
    const groups = [];
    rows.forEach((r) => {
      let g0 = groups.find((x) => x.label === r.label && x.panel === r.panel);
      if (!g0) groups.push((g0 = { label: r.label, panel: r.panel, rows: [] }));
      g0.rows.push(r);
    });
    const items = [], heads = [];
    let y = HEAD_Y, bottom = HEAD_Y, slot = 0;
    groups.forEach((gr) => {
      heads.push({ text: gr.label, y });
      const gy = y + HEAD_GAP;
      let gb = gy;
      gr.rows.forEach((r, i) => {
        let x, yy, w, h, kind;
        if (gr.panel === "cards") {
          const cw = (GRID.w - 2 * GRID.gap) / 3;
          kind = "card"; x = GRID.x + (i % 3) * (cw + GRID.gap); yy = gy + Math.floor(i / 3) * (CARD_H + GRID.gap); w = cw; h = CARD_H;
        } else if (gr.panel === "emoji") {
          kind = "emoji"; x = GRID.x + i * (EMO_W + GRID.gap); yy = gy; w = EMO_W; h = EMO_H;
        } else {
          kind = "row"; x = GRID.x; yy = gy + i * (ROW_H + ROW_GAP); w = GRID.w; h = ROW_H;
        }
        items.push({ kind, slot: slot++, row: r, x, y: yy, w, h });
        gb = Math.max(gb, yy + h);
      });
      bottom = gb;
      y = bottom + 18;
    });
    const total = Object.values(MQ[k].sections).reduce((a, b) => a + b, 0);
    return { items, heads, panelH: bottom + 24 - LY, total };
  }

  // ---- photo start points: where each result left the pile (screen px at the moment of Enter) --------
  function startOf(k, j) {
    const b = BY[k][j];
    const pp = b && b.pickPose;
    if (!pp) return { x: 960, y: 900, w: 70, h: 45, a: 0 };
    const c = camera(E[k]);
    const [sx, sy] = toScreen(c, pp[0], pp[1]);
    return { x: sx, y: sy, w: b.w * c.zoom, h: b.h * c.zoom, a: pp[2] };
  }

  // ---- hero: which full-screen (or Quick Look) is up; opens at hero, held until the next search, collapses -----
  function heroOf(t) {
    for (let k = N - 1; k >= 0; k--) {
      if (t < HR[k]) continue;
      const nx = nextS(k);
      if (nx !== null && t >= nx + COLLAPSE) return null;
      const u = ease.inOut(seg(t, HR[k], HR[k] + EXPAND));
      const c = nx !== null ? ease.out(seg(t, nx, nx + COLLAPSE)) : 0;
      const a = nx !== null ? 1 - seg(t, nx + COLLAPSE * 0.5, nx + COLLAPSE) : 1;
      return { k, m: u * (1 - c), a };
    }
    return null;
  }

  // the launcher panel grows to fit the results and shrinks back after the grid has fallen
  function panelH(t) {
    const k = kE(t);
    const chipV = VK >= 0 ? ease.out(seg(t, LISTEN[0], LISTEN[1])) * (1 - seg(t, E[VK], E[VK] + GROW)) : 0;
    let h = BASE_H + CHIP_H * chipV;
    if (k >= 0) {
      const grow = cont(k) ? 1 : ease.out(seg(t, E[k], E[k] + GROW));
      const nx = leave(k) === nextS(k) ? nextS(k) : null;
      const sh = nx !== null ? ease.inOut(seg(t, nx + COLLAPSE, nx + COLLAPSE + GROW)) : 0;
      h = Math.max(h, BASE_H + (LAY[k].panelH - BASE_H) * grow * (1 - sh));
    }
    return h;
  }

  // ---- canvas drawing: photo results -----------------------------------------------------------------------
  function videoOverlay(w, h, badge) {
    g.beginPath(); g.arc(0, 0, 26, 0, Math.PI * 2); g.fillStyle = "rgba(0,0,0,.42)"; g.fill();
    g.lineWidth = 2; g.strokeStyle = "rgba(255,255,255,.3)"; g.stroke();
    g.beginPath(); g.moveTo(-6, -11); g.lineTo(12, 0); g.lineTo(-6, 11); g.closePath();
    g.fillStyle = "rgba(255,255,255,.94)"; g.fill();
    const bw = 66, bh = 28, bx = w / 2 - 14 - bw, by = h / 2 - 14 - bh;
    roundRect(g, bx, by, bw, bh, 8); g.fillStyle = "rgba(0,0,0,.62)"; g.fill();
    g.fillStyle = "#fff"; g.font = '600 16px "Cascadia Mono", Consolas, monospace';
    g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(badge, bx + bw / 2, by + bh / 2 + 1);
  }

  // a photo print or a grid tile, centred on (cx, cy), rotated by a
  function drawCard(src, cx, cy, w, h, a, o) {
    const r = o.r ?? 18, bw = o.bw ?? 0, al = o.alpha ?? 1;
    if (al <= 0.003 || w < 2 || h < 2) return;
    g.save();
    g.translate(cx, cy); g.rotate(a); g.globalAlpha = al;
    if (o.shadow > 0) {
      g.shadowColor = `rgba(0,0,0,${(0.5 * o.shadow).toFixed(3)})`;
      g.shadowBlur = o.blur ?? 26; g.shadowOffsetY = o.dy ?? 10;
    }
    g.fillStyle = bw > 0 ? "#f4f1ea" : "#171a22";
    roundRect(g, -w / 2, -h / 2, w, h, r); g.fill();
    g.shadowColor = "rgba(0,0,0,0)"; g.shadowBlur = 0; g.shadowOffsetY = 0;
    const im = img(src);
    if (im && im.naturalWidth) {
      const iw = w - 2 * bw, ih = h - 2 * bw;
      const s = Math.max(iw / im.naturalWidth, ih / im.naturalHeight);
      const dw = im.naturalWidth * s, dh = im.naturalHeight * s;
      g.save(); roundRect(g, -iw / 2, -ih / 2, iw, ih, Math.max(1, r - bw)); g.clip();
      g.drawImage(im, -dw / 2, -dh / 2, dw, dh);
      g.restore();
    }
    if (o.badge) videoOverlay(w, h, o.badge);
    roundRect(g, -w / 2 + 0.5, -h / 2 + 0.5, w - 1, h - 1, r);
    g.strokeStyle = "rgba(255,255,255,.12)"; g.lineWidth = 1; g.stroke();
    g.restore();
  }

  // the arc of one result: cubic up and across from P0 to its slot; photos overshoot on landing
  function flightAt(P0, it, k, p) {
    const P3x = it.x + it.w / 2, P3y = it.y + it.h / 2;
    const dx = P3x - P0.x, dy = P3y - P0.y, dist = Math.hypot(dx, dy);
    const lift = 150 + 0.32 * dist;
    const sway = (hash(k * 41 + it.slot * 7 + 3) - 0.5) * 240;
    const C1x = P0.x + dx * 0.12 + sway, C1y = P0.y - lift;
    const C2x = P3x - dx * 0.22 + sway * 0.35, C2y = P3y - lift * 0.7;
    const u = ease.inOut(p), v = 1 - u;
    const x = v * v * v * P0.x + 3 * v * v * u * C1x + 3 * v * u * u * C2x + u * u * u * P3x;
    const y = v * v * v * P0.y + 3 * v * v * u * C1y + 3 * v * u * u * C2y + u * u * u * P3y;
    const sz = it.kind === "video" ? ease.inOut(p) : ease.backOut(p, 1.15);
    const w = lerp(P0.w, it.w, sz), h = lerp(P0.h, it.h, sz);
    const a = lerp(P0.a, 0, ease.out(p)) + 0.5 * Math.sin(Math.PI * p) * (it.slot % 2 ? -1 : 1);
    return { x, y, w, h, a, air: Math.sin(Math.PI * p), ep: ease.out(p) };
  }

  function drawFlight(t, k, it) {
    const arr = arrive(k, it.slot);
    const p = seg(t, E[k], arr);
    const isVideo = it.kind === "video";
    const P0 = isVideo ? VIDEO_FROM : startOf(k, it.body);
    if (isVideo) {
      // blue halo trail behind the video moment (positions re-derived from the same arc)
      for (let n = 6; n >= 1; n--) {
        const q = p - n * 0.08;
        if (q <= 0) continue;
        const f = flightAt(P0, it, k, q);
        const al = 0.55 * (1 - n / 7) * (1 - seg(p, 0.7, 1));
        const R = 90 + 40 * f.air;
        const gr = g.createRadialGradient(f.x, f.y, 0, f.x, f.y, R);
        gr.addColorStop(0, `rgba(120,170,255,${al.toFixed(3)})`); gr.addColorStop(1, "rgba(120,170,255,0)");
        g.fillStyle = gr; g.fillRect(f.x - R, f.y - R, 2 * R, 2 * R);
      }
    }
    const f = flightAt(P0, it, k, p);
    drawCard(it.src, f.x, f.y, f.w, f.h, f.a, {
      shadow: 0.35 + 0.65 * f.air, blur: 12 + 36 * f.air, dy: 4 + 20 * f.air,
      r: lerp(5, 18, f.ep), bw: isVideo ? 0 : (1 - f.ep) * 0.04 * Math.min(f.w, f.h),
      badge: isVideo ? it.badge : null,
    });
  }

  function drawResults(t, lo) {
    for (let k = 0; k < N; k++) {
      if (t < E[k] || isText(k)) continue;
      const nx = nextS(k);
      for (const it of LAY[k].items) {
        const arr = arrive(k, it.slot);
        if (t < arr) { drawFlight(t, k, it); continue; }
        const fallT = nx === null ? Infinity : nx + COLLAPSE + it.slot * BEAT / 4;
        let dy = 0, alpha = lo, ang = 0;
        if (t >= fallT) {
          const q = seg(t, fallT, fallT + FALL);
          if (q >= 1) continue;
          dy = ease.in(q) * 680; ang = q * 0.25 * (it.slot % 2 ? -1 : 1); alpha = lo * (1 - q);
        }
        const cx = it.x + it.w / 2, cy = it.y + it.h / 2 + dy;
        const landing = t < fallT && t < arr + TL.b(0.8);
        if (landing) {
          const p = seg(t, arr, arr + TL.b(0.8)), ex = 10 * ease.out(p);
          g.save();
          roundRect(g, it.x - ex, it.y - ex, it.w + 2 * ex, it.h + 2 * ex, 18 + ex);
          g.strokeStyle = `rgba(190,210,255,${(0.7 * (1 - p) * lo).toFixed(3)})`; g.lineWidth = 2; g.stroke();
          g.restore();
        }
        drawCard(it.src, cx, cy, it.w, it.h, ang, {
          alpha, shadow: 0.9, blur: 26, dy: 10, badge: it.kind === "video" ? it.badge : null,
        });
        if (landing) {
          const fl = pulse(t, arr, 0.08) * 0.38 * lo;
          if (fl > 0.01) {
            g.save(); roundRect(g, it.x, it.y, it.w, it.h, 18);
            g.fillStyle = `rgba(255,255,255,${fl.toFixed(3)})`; g.fill(); g.restore();
          }
        }
      }
    }
  }

  // the best photo or video opens full screen; the video plays the real clip at real speed from the hero
  function frameSrc(t, k) {
    const V = MQ[k].video;
    const f = clamp(1 + Math.floor((t - HR[k]) * 30), 1, V.frames);
    return `${V.dir}/${String(f).padStart(3, "0")}.jpg`;
  }
  function drawHero(t, h) {
    const k = h.k, m = h.m, a = h.a;
    const slot = LAY[k].items[0];
    const R = { x: lerp(slot.x, 0, m), y: lerp(slot.y, 0, m), w: lerp(slot.w, 1920, m), h: lerp(slot.h, 1080, m) };
    const rr = lerp(18, 0, m);
    const holdEnd = nextS(k) ?? (END - 0.4);
    const kbT = smooth(seg(t, HR[k] + EXPAND, holdEnd));
    const kb = lerp(1, 1 + 0.08 * kbT, m), pan = 24 * kbT * m * (k % 2 ? -1 : 1);
    const src = MQ[k].video ? frameSrc(t, k) : MQ[k].images[0].hero;
    const im = img(src);
    if (!im || !im.naturalWidth) return;
    g.save();
    g.globalAlpha = a;
    roundRect(g, R.x, R.y, R.w, R.h, rr); g.clip();
    const s = Math.max(R.w / im.naturalWidth, R.h / im.naturalHeight) * kb;
    const dw = im.naturalWidth * s, dh = im.naturalHeight * s;
    const cx = R.x + R.w / 2 + pan, cy = R.y + R.h / 2;
    g.drawImage(im, cx - dw / 2, cy - dh / 2, dw, dh);
    g.restore();
  }

  // the PDF page Quick Look: a large white page on the right half, clear of the caption
  function drawPage(t, h) {
    const k = h.k, m = h.m, a = h.a;
    const pg = MQ[k].page, im = img(pg.src);
    if (!im || !im.naturalWidth) return;
    const FX = 950, FY = 140, FW = 900, FH = 675;   // right of the caption, which ends near x 870
    const sc = lerp(0.9, 1, m);
    const cx = FX + FW / 2, cy = FY + FH / 2, w = FW * sc, hh = FH * sc;
    const holdEnd = nextS(k) ?? (END - 0.4);
    const kb = 1 + 0.03 * smooth(seg(t, HR[k] + EXPAND, holdEnd));
    g.save();
    g.globalAlpha = a;
    g.shadowColor = `rgba(0,0,0,${(0.6 * m).toFixed(3)})`; g.shadowBlur = 70 * m; g.shadowOffsetY = 26 * m;
    g.fillStyle = "#fff"; roundRect(g, cx - w / 2, cy - hh / 2, w, hh, 6); g.fill();
    g.shadowColor = "rgba(0,0,0,0)"; g.shadowBlur = 0; g.shadowOffsetY = 0;
    g.save(); roundRect(g, cx - w / 2, cy - hh / 2, w, hh, 6); g.clip();
    const s = Math.max(w / im.naturalWidth, hh / im.naturalHeight) * kb;
    const ix = cx - im.naturalWidth * s / 2, iy = cy - im.naturalHeight * s / 2, iw = im.naturalWidth * s, ih = im.naturalHeight * s;
    g.drawImage(im, ix, iy, iw, ih);
    // a highlighter sweeps across the real sentence on the real page (boxes from the PDF's text layer), line by line
    const boxes = pg.hl || [];
    g.globalCompositeOperation = "multiply";
    boxes.forEach((bx, i) => {
      const t0 = HR[k] + TL.b(1) + i * BEAT / 4;
      const q = ease.inOut(seg(t, t0, t0 + BEAT / 4));
      if (q <= 0) return;
      const pad = 0.004 * ih;
      const x0 = ix + bx[0] * iw - pad, y0 = iy + bx[1] * ih - pad, x1 = ix + bx[2] * iw + pad, y1 = iy + bx[3] * ih + pad;
      g.fillStyle = "rgba(132,169,255,0.42)";
      roundRect(g, x0, y0, (x1 - x0) * q, y1 - y0, 4); g.fill();
    });
    g.globalCompositeOperation = "source-over";
    g.restore(); g.restore();
  }

  // ---- DOM: result rows, cards and emoji tiles -------------------------------------------------------------
  function rowInner(r, terms) {
    const snip = (r.snippet || "").replace(/\s+/g, " ").trim();
    return `<div style="position:absolute;left:16px;top:${(ROW_H - 56) / 2}px;width:56px;height:56px;border-radius:14px;background:rgba(255,255,255,.07);display:grid;place-items:center;font-size:28px;line-height:1">${esc(r.icon)}</div>` +
      `<div style="position:absolute;left:92px;right:200px;top:16px">` +
        `<div style="font:600 21px/28px var(--font);color:#eef0f6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.title)}</div>` +
        `<div style="font:400 15px/22px var(--font);color:#8d96a8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.subtitle)}</div>` +
        (snip ? `<div style="margin-top:4px;font:400 16px/24px var(--font);color:#c3c9d6;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${hiText(snip, terms, HL)}</div>` : "") +
      `</div>` +
      (r.badge ? `<div style="position:absolute;right:20px;top:18px;padding:6px 12px;border-radius:999px;background:rgba(255,255,255,.07);font:500 14px/1.2 var(--font);color:#9aa3b5;white-space:nowrap">${esc(r.badge)}</div>` : "");
  }
  function cardInner(r) {
    return `<div style="position:absolute;left:18px;top:20px;width:52px;height:52px;border-radius:14px;background:rgba(255,255,255,.07);display:grid;place-items:center;font-size:26px;line-height:1">${esc(r.icon)}</div>` +
      `<div style="position:absolute;left:86px;right:18px;top:18px;font:600 20px/26px var(--font);color:#eef0f6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.title)}</div>` +
      `<div style="position:absolute;left:86px;right:18px;top:48px;font:400 15px/21px var(--font);color:#8d96a8;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(r.subtitle)}</div>` +
      (r.badge ? `<div style="position:absolute;right:16px;bottom:14px;padding:4px 10px;border-radius:999px;background:rgba(255,255,255,.07);font:500 13px/1.2 var(--font);color:#9aa3b5">${esc(r.badge)}</div>` : "");
  }
  function emojiInner(r) {
    return `<div style="position:absolute;left:0;right:0;top:14px;text-align:center;font-size:44px;line-height:1">${esc(r.icon)}</div>` +
      `<div style="position:absolute;left:8px;right:8px;bottom:14px;text-align:center;font:400 15px/20px var(--font);color:#9aa3b5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.title)}</div>`;
  }
  const BOX = {
    row: "border-radius:16px;background:rgba(255,255,255,.035);box-shadow:inset 0 0 0 1px rgba(255,255,255,.07);box-sizing:border-box",
    card: "border-radius:18px;background:rgba(255,255,255,.045);box-shadow:inset 0 0 0 1px rgba(255,255,255,.08);box-sizing:border-box",
    emoji: "border-radius:16px;background:rgba(255,255,255,.035);box-shadow:inset 0 0 0 1px rgba(255,255,255,.07);box-sizing:border-box",
  };

  // ---- code Quick Look (k with context) ----------------------------------------------------------------------
  const KW = new Set("def return if elif else for in import from with as while not and or None True False class try except finally lambda pass break continue is yield raise assert global nonlocal del".split(" "));
  function codeHtml(line) {
    let out = "", i = 0;
    const sp = (s, c) => `<span style="color:${c}">${esc(s)}</span>`;
    while (i < line.length) {
      const c = line[i];
      if (c === "#") { out += sp(line.slice(i), "#6b7385"); break; }
      if (c === '"' || c === "'") {
        let j = i + 1;
        while (j < line.length && line[j] !== c) { if (line[j] === "\\") j++; j++; }
        j = Math.min(j + 1, line.length);
        out += sp(line.slice(i, j), "#a6e3a1"); i = j; continue;
      }
      if (/[A-Za-z_]/.test(c)) {
        let j = i; while (j < line.length && /[A-Za-z0-9_]/.test(line[j])) j++;
        const w = line.slice(i, j);
        out += KW.has(w) ? sp(w, "#b79bff") : esc(w); i = j; continue;
      }
      if (/[0-9]/.test(c)) {
        let j = i; while (j < line.length && /[0-9.]/.test(line[j])) j++;
        out += sp(line.slice(i, j), "#f2b86b"); i = j; continue;
      }
      out += esc(c); i++;
    }
    return out;
  }
  const CODE_COLS = 56;
  const crop = (s) => { s = s.replace(/\t/g, "    "); return s.length > CODE_COLS ? s.slice(0, CODE_COLS - 1) + "…" : s; };

  // the search quote: ~140 characters around the longest query term, cut on word boundaries, with the page marker removed
  function quoteOf(snip, terms) {
    const s = snip.replace(/\s+/g, " ").replace(/\s+\d+ \/ \d+\s*$/, "").trim();
    const low = s.toLowerCase();
    const byLen = [...terms].filter((x) => x.length > 2).sort((a, b) => b.length - a.length);
    let idx = 0;
    for (const term of byLen) { const i = low.indexOf(term.toLowerCase()); if (i >= 0) { idx = i; break; } }
    let start = Math.max(0, idx - 30);
    if (start > 0) { const sp = s.indexOf(" ", start); if (sp >= 0 && sp < idx) start = sp + 1; }
    let out = s.slice(start, start + 140);
    if (start + 140 < s.length) out = out.replace(/\s+\S*$/, "") + "…";
    if (start > 0) out = "…" + out;
    return out;
  }

  // ---- DOM text state ----------------------------------------------------------------------------------------
  function barText(t, k) {
    let text = "", sel = false, showPh = false, caretOn = false, listening = false;
    if (k < 0) {
      showPh = true;
    } else {
      const q = Q[k];
      listening = !!q.listen && t >= q.listen[0] && t < q.listen[1];
      const clearing = k > 0 && t < S[k] + (q.rapid ? TL.b(0.15) : CLEAR);
      if (clearing) { text = Q[k - 1].q; sel = true; }
      else if (listening) { /* the waveform is shown instead */ }
      else if (q.voice) {
        const n = q.words.filter((w) => w.t <= t).length;
        text = q.words.slice(0, n).map((w) => w.w).join(" ");
      } else {
        const n = q.keys.filter((x) => x <= t).length;
        text = q.q.slice(0, n);
      }
      showPh = !clearing && !listening && !text;
      caretOn = !clearing && !listening && ((t / BEAT) % 1) < 0.5;
    }
    qSpan.textContent = text;
    qSpan.style.background = sel ? "rgba(52,102,246,.45)" : "transparent";
    qPh.style.opacity = showPh ? "0.85" : "0";
    caret.style.opacity = caretOn ? "1" : "0";
    return listening;
  }

  // rows, cards and emoji tiles: slide in from y+24 with a slight overshoot at TL.arrive(k, j), fall away at the next search
  function updateRows(t) {
    for (let k = 0; k < N; k++) {
      if (!isText(k)) continue;
      const nx = nextS(k);
      for (const r of rowEls[k]) {
        const it = r.it, arr = arrive(k, it.slot);
        let op = seg(t, arr, arr + TL.b(0.4));
        let y = (1 - ease.backOut(seg(t, arr, arr + TL.b(1.2)), 1.3)) * 24;
        if (nx !== null && leave(k) !== nx && t >= leave(k)) {
          op *= 1 - seg(t, leave(k), leave(k) + 0.08);
        } else if (nx !== null && t >= nx) {
          const fallT = nx + COLLAPSE + it.slot * BEAT / 4;
          const q = seg(t, fallT, fallT + FALL);
          op *= 1 - q; y += ease.in(q) * 40;
        }
        r.el.style.display = op > 0.001 ? "" : "none";
        r.el.style.opacity = op.toFixed(3);
        r.el.style.transform = `translateY(${y.toFixed(1)}px)`;
      }
    }
  }

  // the text Quick Looks: quote card (PDF), code panel, settings card
  function updateTextHeroes(t, h) {
    for (let k = 0; k < N; k++) {
      if (!isText(k)) continue;
      const M = MQ[k];
      const hk = h && h.k === k ? h : null;
      const vis = hk ? hk.m * hk.a : 0;
      if (quoteEls[k]) textFx(quoteEls[k], t, HR[k] + TL.b(0.5), capEnd(k));
      if (codeEls[k]) {
        const c = codeEls[k];
        c.root.style.display = vis > 0.001 ? "" : "none";
        c.root.style.opacity = vis.toFixed(3);
        c.root.style.transform = `scale(${lerp(0.94, 1, hk ? hk.m : 0).toFixed(4)})`;
        // all lines are in by the end of the expand (32nd-note stagger); the matching line is swept on beat 1
        if (c.hl) c.hl.style.width = (100 * ease.inOut(seg(t, HR[k] + TL.b(1), HR[k] + TL.b(1.6)))).toFixed(1) + "%";
        c.lines.forEach((ln) => {
          const t0 = HR[k] + 0.12 + ln.i * BEAT / 32;
          const v = ease.out(seg(t, t0, t0 + TL.b(0.3)));
          ln.el.style.opacity = (ln.base * v).toFixed(3);
          ln.el.style.transform = `translateX(${((1 - v) * -10).toFixed(1)}px)`;
        });
      }
      if (bigEls[k]) {
        const b = bigEls[k], it = LAY[k].items[0];
        const m = hk ? hk.m : 0;
        const sx = (it.x + it.w / 2) - 960, sy = (it.y + it.h / 2) - 540;
        const s = lerp(it.w / 900, 1, m);
        const tx = lerp(sx, 0, m), ty = lerp(sy, 0, m);
        const P = HR[k] + TL.b(1);
        const down = seg(t, P, P + TL.b(0.2)), up = seg(t, P + TL.b(0.2), P + TL.b(0.6));
        const press = 1 - 0.06 * (down - up);
        b.root.style.display = vis > 0.001 ? "" : "none";
        b.root.style.opacity = vis.toFixed(3);
        b.root.style.transform = `translate(${tx.toFixed(1)}px,${ty.toFixed(1)}px) scale(${s.toFixed(4)})`;
        b.btn.style.transform = `scale(${press.toFixed(4)})`;
        b.btn.style.filter = `brightness(${(1 + 0.35 * pulse(t, P, 0.12)).toFixed(3)})`;
      }
    }
  }

  // ---- chapter ----------------------------------------------------------------------------------------------
  CH.add({
    name: "ask", z: 20,
    async init() {
      // Quick Look images are not in the photo preload list
      await Promise.all(MQ.map((M) => (M.page ? load(M.page.src) : null)));

      root = el("div", "position:absolute;left:0;top:0;width:1920px;height:1080px;pointer-events:none;display:none", document.getElementById("ui"));

      // the launcher window: glass panel with the search bar inside
      panel = el("div", `position:absolute;left:${LX}px;top:${LY}px;width:${LW}px;height:${BASE_H}px;box-sizing:border-box;` +
        `border-radius:${LAUNCHER.radius}px;background:rgba(19,22,30,.86);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);` +
        "box-shadow:inset 0 0 0 1px rgba(255,255,255,.10),0 60px 140px rgba(0,0,0,.55),0 16px 46px rgba(0,0,0,.36);", root);
      el("div", `position:absolute;left:0;top:0;width:100%;height:80px;border-radius:${LAUNCHER.radius}px ${LAUNCHER.radius}px 0 0;` +
        "background:linear-gradient(180deg,rgba(255,255,255,.075),rgba(255,255,255,0));pointer-events:none", panel);
      const bar = el("div", `position:absolute;left:${rx(BAR.x)}px;top:${ry(BAR.y)}px;width:${BAR.w}px;height:${BAR.h}px;box-sizing:border-box;` +
        "border-radius:20px;background:rgba(255,255,255,.045);box-shadow:inset 0 0 0 1px rgba(255,255,255,.09),0 0 0 4px rgba(52,102,246,.20);", panel);
      bar.innerHTML =
        '<svg viewBox="0 0 24 24" style="position:absolute;left:26px;top:33px;width:26px;height:26px;color:#9aa3b5">' +
          '<circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="2"/>' +
          '<path d="M16 16l4.5 4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' +
        '<div class="ss-ph" style="position:absolute;left:70px;top:0;height:92px;line-height:92px;font:400 38px/92px var(--font);color:#9aa3b5;white-space:nowrap;opacity:.85">Search your PC&#8230;</div>' +
        '<div class="ss-q" style="position:absolute;left:70px;top:0;height:92px;line-height:92px;font:400 38px/92px var(--font);color:#eef0f6;white-space:pre;letter-spacing:-.005em">' +
          '<span style="border-radius:6px"></span><span class="ss-caret" style="display:inline-block;width:3px;height:44px;margin-left:3px;vertical-align:-9px;background:#84a9ff;border-radius:2px"></span></div>' +
        '<div class="ss-wave" style="position:absolute;left:70px;top:0;width:900px;height:92px;display:flex;align-items:center;gap:10px;opacity:0"></div>' +
        '<div class="ss-enter" style="position:absolute;left:1010px;top:29px;width:110px;height:34px;box-sizing:border-box;border-radius:9px;' +
          'background:rgba(255,255,255,.08);box-shadow:inset 0 0 0 1px rgba(255,255,255,.2);font:600 15px/32px var(--font);color:#eef0f6;text-align:center;opacity:0">Enter &#8629;</div>' +
        '<button class="ss-mic" type="button" tabindex="-1" style="position:absolute;left:1130px;top:24px;width:44px;height:44px;box-sizing:border-box;padding:0;border:0;' +
          'border-radius:14px;background:rgba(255,255,255,.06);color:#9aa3b5;display:grid;place-items:center">' +
          '<i class="ss-ring" style="position:absolute;left:0;top:0;width:44px;height:44px;border-radius:14px;border:2px solid #e5484d;opacity:0"></i>' +
          '<i class="ss-ring" style="position:absolute;left:0;top:0;width:44px;height:44px;border-radius:14px;border:2px solid #e5484d;opacity:0"></i>' +
          '<svg viewBox="0 0 24 24" style="position:relative;width:22px;height:22px"><rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor"/>' +
          '<path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></button>';
      qPh = bar.querySelector(".ss-ph");
      qSpan = bar.querySelector(".ss-q span");
      caret = bar.querySelector(".ss-caret");
      wave = bar.querySelector(".ss-wave");
      enterKey = bar.querySelector(".ss-enter");
      mic = bar.querySelector(".ss-mic");
      rings = [...mic.querySelectorAll(".ss-ring")];
      for (let i = 0; i < 60; i++) bars.push(el("i", "display:block;width:5px;height:10px;border-radius:3px;background:linear-gradient(180deg,#84a9ff,#b79bff)", wave));

      // listening / transcribed chips under the bar (voice search)
      const chipCss = `position:absolute;left:${rx(BAR.x)}px;top:${STATUS_Y - LY + 4}px;display:none;align-items:center;gap:10px;height:30px;box-sizing:border-box;` +
        "padding:0 14px;border-radius:999px;background:rgba(255,255,255,.06);box-shadow:inset 0 0 0 1px rgba(255,255,255,.08);font:500 16px var(--font);color:#c9cfdb;white-space:nowrap";
      chipL = el("div", chipCss, panel, '<i style="display:block;width:8px;height:8px;border-radius:50%;background:#e5484d"></i>Listening&#8230;');
      chipT = el("div", chipCss, panel, '<span style="color:#84a9ff;font-weight:700">&#10003;</span>Transcribed on your PC');

      // per search: layout, bodies, status line, section headers, and the rows / cards of text searches
      for (let k = 0; k < N; k++) {
        LAY[k] = isText(k) ? layoutText(k) : layoutFor(k);
        BY[k] = SIM.bodies.filter((b) => b.role === "result" && b.q === Q[k].qk).sort((a, b) => a.j - b.j);
        const M = MQ[k];
        const label = M.video ? "Video moments &amp; photos" : esc(LAY[k].heads[0].text);
        statusEls[k] = el("div", `position:absolute;left:${rx(BAR.x)}px;top:${STATUS_Y - LY}px;font:400 18px/24px var(--font);color:#9aa3b5;white-space:nowrap;display:none`, panel,
          `${label} &middot; ${LAY[k].total} results &middot; <span style="font-family:var(--mono);color:#c9cfdb">${M.ms} ms</span>`);
        headEls[k] = LAY[k].heads.map((hd) => el("div", `position:absolute;left:${rx(BAR.x)}px;top:${ry(hd.y)}px;font:600 22px/28px var(--font);color:#9aa3b5;white-space:nowrap;display:none`, panel, esc(hd.text)));
        rowEls[k] = [];
        if (isText(k)) LAY[k].items.forEach((it) => {
          const inner = it.kind === "row" ? rowInner(it.row, M.terms) : it.kind === "card" ? cardInner(it.row) : emojiInner(it.row);
          const node = el("div", `position:absolute;left:${rx(it.x)}px;top:${ry(it.y)}px;width:${it.w}px;height:${it.h}px;display:none;${BOX[it.kind]}`, panel, inner);
          rowEls[k].push({ it, el: node });
        });
      }

      // overlay canvas: photo results, flights and the photo heroes (above the glass, below the text)
      cv = document.createElement("canvas");
      cv.width = 1920; cv.height = 1080;
      cv.style.cssText = "position:absolute;left:0;top:0;width:1920px;height:1080px;pointer-events:none";
      root.appendChild(cv);
      g = cv.getContext("2d");

      shade = el("div", "position:absolute;left:0;top:0;width:1920px;height:1080px;pointer-events:none;opacity:0;" +
        "background:radial-gradient(1100px 760px at 0% 100%,rgba(4,5,9,.88),rgba(4,5,9,0) 72%),linear-gradient(0deg,rgba(4,5,9,.5),rgba(4,5,9,0) 42%)", root);

      // Quick Looks
      for (let k = 0; k < N; k++) {
        const M = MQ[k];
        if (M.video) {
          pillEls[k] = el("div", "position:absolute;left:120px;top:96px;display:none", root,
            '<div style="display:inline-flex;align-items:center;gap:12px;padding:10px 22px 10px 18px;border-radius:999px;background:rgba(19,22,30,.72);' +
            'backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);box-shadow:inset 0 0 0 1px rgba(255,255,255,.14);' +
            'font:600 24px/1 var(--font);color:#eef0f6;white-space:nowrap">' +
            `<span style="color:#84a9ff">&#9654;</span><span>Video moment</span><span style="font-family:var(--mono);font-size:21px;color:#c9cfdb">&middot; ${esc(M.video.badge)}</span></div>`);
          const po = el("div", "position:absolute;left:0;bottom:0;width:1920px;height:5px;background:rgba(255,255,255,.14);display:none", root);
          const pb = el("div", "position:absolute;left:0;top:0;height:100%;width:0;background:linear-gradient(90deg,#84a9ff,#b79bff)", po);
          progEls[k] = { outer: po, bar: pb };
        }
        if (M.page && !(M.page.hl && M.page.hl.length)) {
          const row = M.rows[0];
          const clean = M.rows.find((r) => !/-[0-9a-f]{16,}/.test(r.title)) || row;
          quoteEls[k] = el("div", "position:absolute;left:910px;top:600px;width:880px;box-sizing:border-box;padding:26px 30px 24px;border-radius:22px;display:none;" +
            "background:rgba(19,22,30,.97);box-shadow:inset 0 0 0 1px rgba(255,255,255,.12),0 30px 80px rgba(0,0,0,.5)", root,
            `<div style="font:400 30px/1.42 var(--font);color:#e6e9f1">${hiText(quoteOf(row.snippet || "", M.terms), M.terms, QHL)}</div>` +
            `<div style="margin-top:18px;display:inline-flex;align-items:center;gap:10px;padding:8px 14px;border-radius:999px;background:rgba(132,169,255,.14);color:#cfe0ff;font:500 18px/1.2 var(--font)">` +
            `${esc(clean.icon)} ${esc(clean.title)} &middot; page ${M.page.n}</div>`);
        }
        if (M.context) {
          // a window of the real file around the line that best matches the query; that line is highlighted
          const all = [...M.context.before.split("\n").slice(-6), ...M.context.text.split("\n"),
                       ...(M.context.after || "").split("\n").slice(0, 4)];
          const stems = (M.terms || []).filter((w) => w.length > 2).map((w) => (w.length > 3 ? w.replace(/s$/, "") : w));
          const score = (ln) => stems.reduce((n, st) => n + (ln.toLowerCase().includes(st) ? 1 : 0), 0);
          let best = 0;
          all.forEach((ln, i) => { if (score(ln) > score(all[best])) best = i; });
          let w0 = Math.max(0, best - 3);
          while (w0 < best && !all[w0].trim()) w0++;   // no blank lines at the top
          const win = all.slice(w0, w0 + 11);
          const lead = win.filter((x) => x.trim().length).map((x) => x.match(/^[ \t]*/)[0].length);
          const ind = lead.length ? Math.min(...lead) : 0;
          const lines = win.map((x) => (ind ? x.slice(Math.min(ind, x.match(/^[ \t]*/)[0].length)) : x));
          const hitAt = best - w0;
          const body = lines.map((ln, i) => {
            const hit = i === hitAt;
            return `<div style="position:relative;display:flex;align-items:flex-start;${hit ? "min-height:38px;padding:2px 0" : "height:38px"}">` +
              (hit ? `<div class="ask-hl" style="position:absolute;left:0;top:0;bottom:0;width:0;background:rgba(132,169,255,.16);box-shadow:inset 4px 0 0 #84a9ff"></div>` : "") +
              `<span style="position:relative;flex:none;width:56px;text-align:right;padding-right:18px;font:400 20px/38px var(--mono);color:#4b5263">${w0 + i + 1}</span>` +
              `<span style="position:relative;font:400 26px/38px var(--mono);${hit ? "white-space:pre-wrap;padding-left:4ch;text-indent:-4ch;max-width:880px" : "white-space:pre"};color:#d7dce6">${codeHtml(hit ? ln : crop(ln))}</span></div>`;
          });
          const codeRoot = el("div", "position:absolute;left:780px;top:150px;width:1000px;box-sizing:border-box;display:none;border-radius:18px;background:#0d1017;" +
            "box-shadow:inset 0 0 0 1px rgba(255,255,255,.08),0 40px 100px rgba(0,0,0,.6);overflow:hidden;padding-bottom:14px;transform-origin:50% 50%", root,
            `<div style="display:flex;align-items:center;gap:12px;height:54px;padding:0 20px;box-sizing:border-box;border-bottom:1px solid rgba(255,255,255,.07)">` +
            `<span style="font:600 19px/1 var(--font);color:#eef0f6">${esc(M.rows[0].title)}</span>` +
            `<span style="margin-left:auto;font:500 14px/1 var(--font);color:#9aa3b5;padding:6px 10px;border-radius:999px;background:rgba(255,255,255,.07)">Python</span></div>` +
            `<div style="padding-top:12px">${body.join("")}</div>`);
          const rowsEls = [...codeRoot.querySelectorAll(":scope > div:last-child > div")];
          codeEls[k] = { root: codeRoot, hl: codeRoot.querySelector(".ask-hl"),
                         lines: rowsEls.map((el2, i) => ({ el: el2, i, base: i === hitAt ? 1 : 0.62 })) };
        }
        if (M.rows && M.rows[0] && M.rows[0].panel === "cards" && !M.page && !M.context && !Q[k].rapid) {
          const r0 = M.rows[0];
          const btn = el("button", "position:absolute;left:56px;bottom:52px;height:66px;width:330px;border:0;border-radius:18px;background:#3466f6;color:#fff;" +
            "font:600 24px/1 var(--font);box-shadow:0 14px 40px rgba(52,102,246,.45);cursor:default;transform-origin:50% 50%", null, "Open settings &#8629;");
          const card = el("div", "position:absolute;left:510px;top:340px;width:900px;height:400px;box-sizing:border-box;display:none;border-radius:32px;background:rgba(24,27,36,.95);" +
            "box-shadow:inset 0 0 0 1px rgba(255,255,255,.12),0 60px 140px rgba(0,0,0,.6)", root,
            `<div style="position:absolute;left:56px;top:56px;width:120px;height:120px;border-radius:30px;background:rgba(255,255,255,.08);display:grid;place-items:center;font-size:64px;line-height:1">${esc(r0.icon)}</div>` +
            `<div style="position:absolute;left:206px;right:40px;top:58px;font:700 54px/1.1 var(--display);letter-spacing:-.02em;color:#fff">${esc(r0.title)}</div>` +
            `<div style="position:absolute;left:206px;right:40px;top:136px;font:400 26px/1.35 var(--font);color:#aab2c3">${esc(r0.subtitle)}</div>`);
          card.appendChild(btn);
          bigEls[k] = { root: card, btn };
        }
      }

      // captions, lower-left
      for (let k = 0; k < N; k++) {
        const c = Q[k].caption;
        if (!c) continue;
        capEls[k] = el("div", "position:absolute;left:120px;bottom:150px;width:1300px;display:none", root,
          `<div style="font:700 68px/1.02 var(--display);letter-spacing:-.03em;color:#fff;text-shadow:0 2px 30px rgba(0,0,0,.35)">${c[0]}</div>` +
          `<div style="margin-top:18px;font:400 30px/1.3 var(--font);color:#c9cfdb">${c[1]}</div>`);
      }
    },

    draw(t) {
      if (t < 16.2 || t >= END) { if (root) root.style.display = "none"; return; }
      root.style.display = "";
      root.style.opacity = (1 - ease.inOut(seg(t, END - 0.4, END))).toFixed(3);

      const h = heroOf(t);
      const hm = h ? h.m * h.a : 0;
      // intro.js fades its keycap group out over [16.5, S[0]]; the glass fades in over the same window on the same rect
      const lo = t < S[0] ? seg(t, 16.5, S[0]) : 1 - hm;
      panel.style.opacity = lo.toFixed(3);
      panel.style.height = panelH(t).toFixed(1) + "px";

      // search bar
      const k = kS(t);
      const listening = barText(t, k);
      const voiceNow = k >= 0 && !!Q[k].voice;
      const rec = voiceNow && listening;
      mic.style.background = rec ? "#e5484d" : "rgba(255,255,255,.06)";
      mic.style.color = rec ? "#fff" : "#9aa3b5";
      rings.forEach((r, i) => {
        const p = (((t - LISTEN[0]) / TL.b(2)) + i * 0.5) % 1;
        r.style.opacity = rec ? ((1 - p) * 0.8).toFixed(3) : "0";
        r.style.transform = `scale(${(1 + 0.7 * p).toFixed(3)})`;
      });
      const wv = voiceNow ? ease.out(seg(t, LISTEN[0], LISTEN[0] + TL.b(0.2))) * (1 - seg(t, LISTEN[1], LISTEN[1] + TL.b(0.25))) : 0;
      wave.style.opacity = wv.toFixed(3);
      if (wv > 0) bars.forEach((b, i) => {
        const n = noise(t * 4.5 + i * 0.31, 17);
        b.style.height = (6 + 62 * Math.pow((n + 1) / 2, 1.2)).toFixed(1) + "px";
      });
      if (VK >= 0) {
        textFx(chipL, t, LISTEN[0], LISTEN[1]);
        textFx(chipT, t, LISTEN[1], E[VK]);
      }

      // Enter key flash, right of the bar
      const ke = kE(t);
      let eA = 0, eS = 1, eF = 0;
      if (ke >= 0 && t - E[ke] < TL.b(1)) {
        const d = t - E[ke];
        eA = 1 - seg(d, TL.b(0.6), TL.b(1));
        eS = 0.9 + 0.1 * ease.backOut(seg(d, 0, TL.b(0.25)));
        eF = pulse(t, E[ke], 0.1);
      }
      enterKey.style.opacity = eA.toFixed(3);
      enterKey.style.transform = `scale(${eS.toFixed(3)})`;
      enterKey.style.background = `rgba(132,169,255,${(0.08 + 0.4 * eF).toFixed(3)})`;

      // status line and section headers: appear with the first arrival, leave with the next search
      for (let i = 0; i < N; i++) {
        const tIn = arrive(i, 0);
        if (Q[i].rapid) {
          // quick swaps: the status line changes in place; the section header stays up across the slot
          textFx(statusEls[i], t, tIn, headEnd(i) - 0.1, { dur: 0.1, rise: 6 });
          let last = i; while (last + 1 < N && cont(last + 1)) last++;
          headEls[i].forEach((e) => (cont(i) ? (e.style.display = "none") : textFx(e, t, tIn, headEnd(last))));
          continue;
        }
        textFx(statusEls[i], t, tIn, headEnd(i));
        headEls[i].forEach((e) => textFx(e, t, tIn, headEnd(i)));
      }

      updateRows(t);
      updateTextHeroes(t, h);

      // hero, captions, video pills and progress
      shade.style.opacity = hm.toFixed(3);
      for (let i = 0; i < N; i++) if (capEls[i]) textFx(capEls[i], t, Q[i].rapid ? E[i] + TL.b(0.5) : HR[i] + CAP_IN, Q[i].rapid ? Q[i].capTo : capEnd(i));
      for (const key of Object.keys(pillEls)) { const kk = Number(key); textFx(pillEls[kk], t, HR[kk] + CAP_IN, capEnd(kk)); }
      for (const key of Object.keys(progEls)) {
        const kk = Number(key);
        const V = MQ[kk].video;
        if (h && h.k === kk) {
          const f = clamp(1 + Math.floor((t - HR[kk]) * 30), 1, V.frames);
          progEls[kk].outer.style.display = "";
          progEls[kk].outer.style.opacity = hm.toFixed(3);
          progEls[kk].bar.style.width = (100 * f / V.frames).toFixed(2) + "%";
        } else {
          progEls[kk].outer.style.display = "none";
        }
      }

      // overlay canvas: photo results, flights and the photo / PDF heroes
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.globalAlpha = 1;
      g.clearRect(0, 0, 1920, 1080);
      drawResults(t, lo);
      if (h) {
        const M = MQ[h.k];
        if (M.page) drawPage(t, h);
        else if (!isText(h.k)) drawHero(t, h);
      }
    },
  });
})();
