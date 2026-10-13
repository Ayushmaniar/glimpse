// Act 4, THE BRAIN (TL.ACT.brain): the pile's photos lift off and turn into a slowly rotating "meaning space".
// Every photo sits where its real EmbeddingGemma 2 vector puts it (MEDIA e, pulled toward its cluster c). Glyph cards
// (PDF pages, voice-note waveforms, video frames, settings, code) float among them. A query travels in, its
// neighbours light up, a laptop draws itself around the cloud, the link to the cloud is cut, and the whole cloud
// implodes into a core that finale.js explodes from.
(function () {
  // ---- beats (timeline.js; the music is built on these) -------------------------------------------
  const T0 = TL.BRAIN.gather;        // photos lift off the pile
  const TM = TL.BRAIN.model;         // "Powered by EmbeddingGemma 2"
  const TS = TL.BRAIN.space;         // the query travels in
  const TLO = TL.BRAIN.local;        // "It runs on your PC."
  const TI = TL.BRAIN.implode;       // implosion
  const TE = TL.ACT.brain[1];        // end of act 4
  const T1 = TE + 0.1;               // nothing is drawn after this
  const TCUT = TLO + TL.b(1);        // the link to the cloud is cut
  const A4 = TL.BRAIN.gather;   // times below are offsets from this beat
  const LIFT = 1.4, STAGGER = 0.6;   // lift duration, and the spread of start times (s)

  // ---- look -----------------------------------------------------------------------------------------
  const SKY = "132,169,255";
  const FOC = 1000, DCAM = 1250, TILT = -0.22, YAW0 = 0.35;
  const OMEGA = 25 * Math.PI / 180;  // slow turn, 25 degrees per second
  const BOOST = 16;                  // the turn speeds up this much during the implosion (expoIn)
  const CENTER = { x: 960, y: 540 };
  const CLOUD_TOP = { x: 960, y: 640 }, CLOUD_PULL = { x: 960, y: 570 }, S_PULL = 0.66;
  // cluster centres in cloud units (z positive = further away)
  let CENT = [];   // cluster centroids in cloud units, computed in init from the embeddings
  const BLOB = [SKY, "139,92,246", "52,102,246", "95,130,240", "150,110,255", "70,150,230", "120,140,255"];
  const GLYPHS = [["pdf", 5], ["pdf", 5], ["pdf", 5], ["pdf", 5], ["pdf", 5],
                  ["wave", 3], ["wave", 3], ["wave", 3], ["wave", 3],
                  ["film", 1], ["film", 1], ["film", 1], ["film", 1],
                  ["gear", 5], ["gear", 5], ["code", 5], ["code", 5], ["code", 5]];
  const L_PHOTO = 80, W_GLYPH = 56, H_GLYPH = 70;
  const PILL_R = { x: 570, y: 595 };               // right-middle of the query pill (screen px)
  const EXTRA_N = 120;
  const SC = 225, PULL = 0.4, SPREAD = 1.35;   // SPREAD pushes the cluster centres apart so the blobs read   // embedding units -> cloud units; pull toward the cluster centroid

  let LAY = null, lctx = null, glyphImg = {}, glowImg = null;
  const softCache = new Map();
  const pts = [];
  const starts = [];                                // screen starts of bodies that are in the pile at T0
  const posOf = new Map();
  const cardMap = new Map();
  const EL = {};

  // ---- helpers --------------------------------------------------------------------------------------
  function yaw(t) {
    // the turn rate is OMEGA * (1 + BOOST * expoIn(x)) for x = progress through the implosion
    const x = seg(t, TI, TE);
    const I = x > 0 ? (Math.pow(2, 10 * x - 10) - Math.pow(2, -10)) / (10 * Math.LN2) : 0;   // integral of expoIn
    return YAW0 + OMEGA * (t - T0) + OMEGA * BOOST * (TE - TI) * I;
  }
  function makeG(t) {
    const th = yaw(t), k = ease.inOut(seg(t, (A4 + 4.20), (A4 + 5.20)));
    return { c: Math.cos(th), s: Math.sin(th), cf: Math.cos(TILT), sf: Math.sin(TILT),
             S: lerp(1, S_PULL, k), cx: 960, cy: lerp(CLOUD_TOP.y, CLOUD_PULL.y, k) };
  }
  // rotate around Y, tilt around X, perspective. Returns screen x, y, scale and depth.
  function proj(X, Y, Z, G) {
    Y *= 0.58;                       // a wide, flat cloud: room for the title above and the footnote below
    const x1 = X * G.c + Z * G.s, z1 = -X * G.s + Z * G.c;
    const y2 = Y * G.cf - z1 * G.sf, z2 = Y * G.sf + z1 * G.cf;
    const sc = FOC / (z2 + DCAM) * G.S;
    return { x: G.cx + x1 * sc, y: G.cy + y2 * sc, sc, z: z2 };
  }
  function ball(seed, R) {           // deterministic point in a flattened ball
    const r = R * Math.cbrt(hash(seed * 3 + 1));
    const th = 2 * Math.PI * hash(seed * 3 + 2);
    const c = 2 * hash(seed * 3 + 3) - 1, sn = Math.sqrt(1 - c * c);
    return [r * sn * Math.cos(th), 0.8 * r * sn * Math.sin(th), r * c];
  }
  function entryOf(src) {
    const k = posOf.get(src);
    return k !== undefined ? MEDIA.pile[k] : (cardMap.get(src) || { e: [0, 0, 0], c: 0 });
  }
  function cloudOf(ent, seed) {
    const C = CENT[ent.c], j = ball(seed, 14);
    return [lerp(ent.e[0] * SC, C[0], PULL) + j[0], lerp(ent.e[1] * SC, C[1], PULL) + j[1], lerp(ent.e[2] * SC, C[2], PULL) + j[2]];
  }
  function mk(o) {
    if (o.pos) { o.cx = o.pos[0]; o.cy = o.pos[1]; o.cz = o.pos[2]; }
    else { const c = CENT[o.cl]; o.cx = c[0] + o.off[0]; o.cy = c[1] + o.off[1]; o.cz = c[2] + o.off[2]; }
    o.jit = 0.85 + 0.3 * hash(o.idx * 13 + 5);
    o.ph = 6.283 * hash(o.idx * 17 + 9);
    o.s0 = 0.4 * hash(o.idx * 19 + 11);
    o.dir = hash(o.idx * 23 + 2) < 0.5 ? -1 : 1;
    o.arc = hash(o.idx * 29 + 4) < 0.5 ? -1 : 1;
    o.spin = (hash(o.idx * 31 + 6) - 0.5) * 1.6;
    if (o.kind === "photo") {
      const r = o.ratio;
      if (r <= 1) { o.w0 = L_PHOTO; } else { o.w0 = L_PHOTO / r; }
    } else { o.w0 = W_GLYPH; }
    return o;
  }

  // ---- sprites --------------------------------------------------------------------------------------
  function glowCanvas() {
    const c = document.createElement("canvas"); c.width = c.height = 128;
    const g = c.getContext("2d");
    const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    gr.addColorStop(0, `rgba(${SKY},0.95)`); gr.addColorStop(0.35, `rgba(${SKY},0.35)`); gr.addColorStop(1, `rgba(${SKY},0)`);
    g.fillStyle = gr; g.fillRect(0, 0, 128, 128);
    return c;
  }
  function glyphCanvas(kind) {
    const c = document.createElement("canvas"); c.width = 256; c.height = 318;
    const g = c.getContext("2d");
    const bg = g.createLinearGradient(0, 0, 0, 318);
    bg.addColorStop(0, "#1f2638"); bg.addColorStop(1, "#0e1119");
    roundRect(g, 3, 3, 250, 312, 28); g.fillStyle = bg; g.fill();
    g.lineWidth = 4; g.strokeStyle = "rgba(255,255,255,.16)"; g.stroke();
    if (kind === "pdf") {
      g.beginPath(); g.moveTo(80, 64); g.lineTo(150, 64); g.lineTo(178, 92); g.lineTo(178, 254); g.lineTo(80, 254); g.closePath();
      g.fillStyle = "#f4f1ea"; g.fill();
      g.fillStyle = "rgba(20,24,34,.32)";
      for (let k = 0; k < 7; k++) g.fillRect(98, 116 + k * 17, k === 6 ? 44 : 60, 6);
      g.fillStyle = `rgb(${SKY})`; g.fillRect(98, 222, 56, 14);
    } else if (kind === "wave") {
      for (let i = 0; i < 16; i++) {
        const h = 26 + 96 * Math.abs(Math.sin(i * 0.8 + 0.4)) * (0.55 + 0.45 * Math.cos(i * 1.7));
        const gr = g.createLinearGradient(0, 160 - h / 2, 0, 160 + h / 2);
        gr.addColorStop(0, `rgb(${SKY})`); gr.addColorStop(1, "rgb(139,92,246)");
        g.fillStyle = gr; g.fillRect(58 + i * 9.2, 160 - h / 2, 5.5, h);
      }
    } else if (kind === "film") {
      g.fillStyle = "#0a0c12"; g.fillRect(62, 44, 132, 232);
      g.fillStyle = "#1f2638";
      for (let y = 56; y < 268; y += 34) { g.fillRect(70, y, 12, 18); g.fillRect(172, y, 12, 18); }
      const gr = g.createLinearGradient(88, 96, 168, 192);
      gr.addColorStop(0, "rgba(52,102,246,.85)"); gr.addColorStop(1, "rgba(139,92,246,.6)");
      g.fillStyle = gr; g.fillRect(88, 96, 80, 96);
      g.fillStyle = "rgba(240,244,255,.95)";
      g.beginPath(); g.moveTo(116, 124); g.lineTo(150, 144); g.lineTo(116, 164); g.closePath(); g.fill();
    } else if (kind === "gear") {
      g.beginPath();
      for (let k = 0; k < 48; k++) {
        const a = k / 48 * 2 * Math.PI, r = (k % 6 < 3) ? 64 : 52;
        const x = 128 + r * Math.cos(a), y = 160 + r * Math.sin(a);
        k ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      g.closePath(); g.fillStyle = "rgba(226,232,246,.92)"; g.fill();
      g.beginPath(); g.arc(128, 160, 20, 0, 2 * Math.PI); g.fillStyle = "#121622"; g.fill();
    } else if (kind === "code") {
      g.lineWidth = 12; g.lineCap = "round"; g.lineJoin = "round"; g.strokeStyle = `rgb(${SKY})`;
      g.beginPath(); g.moveTo(104, 112); g.lineTo(70, 160); g.lineTo(104, 208); g.stroke();
      g.beginPath(); g.moveTo(152, 112); g.lineTo(186, 160); g.lineTo(152, 208); g.stroke();
      g.beginPath(); g.moveTo(138, 100); g.lineTo(118, 220); g.stroke();
    }
    return c;
  }
  // soft (defocused) version of a photo print, for depth of field
  function softOf(src, bucket, ratio) {
    const key = src + "|" + bucket;
    let v = softCache.get(key);
    if (v) return v;
    const hh = Math.round(bucket * ratio), P = 12;
    const base = sprite(src, bucket, hh);
    const c = document.createElement("canvas"); c.width = bucket + 2 * P; c.height = hh + 2 * P;
    const g = c.getContext("2d");
    g.filter = "blur(2.6px)"; g.drawImage(base, P, P);
    v = { c };
    softCache.set(key, v);
    return v;
  }

  // ---- state of every point at time t ----------------------------------------------------------------
  // pass 1: where the point is in the cloud, and where it is while lifting off the pile
  function baseState(pt, t, G) {
    const bob = 4 * Math.sin(t * 0.7 + pt.ph);
    const cp = proj(pt.cx, pt.cy + bob, pt.cz, G);
    const s = { x: cp.x, y: cp.y, w: pt.w0 * cp.sc * pt.jit, ang: 0, alpha: 1, sh: 0, soft: 0, z: cp.z, E: 1, glow: 0, Q: 0 };
    if (pt.kind === "glyph") {
      const a = smooth(seg(t, pt.t0, pt.t0 + 0.5));
      s.alpha = a; s.w *= lerp(0.5, 1, ease.backOut(seg(t, pt.t0, pt.t0 + 0.9))); s.E = a;
      return s;
    }
    const d0 = T0 + pt.d, e = ease.inOut(seg(t, d0, d0 + LIFT));
    const dx = cp.x - pt.sx, dy = cp.y - pt.sy, len = Math.hypot(dx, dy) || 1;
    const arc = Math.min(140, len * 0.3) * Math.sin(Math.PI * e) * pt.arc;
    s.x = pt.sx + dx * e + (-dy / len) * arc;
    s.y = pt.sy + dy * e + (dx / len) * arc;
    s.w = lerp(pt.sw, s.w, e);
    s.ang = pt.sa * (1 - e) + pt.spin * Math.sin(Math.PI * e);   // the spin unwinds
    s.sh = pt.hasStart ? 0.55 * (1 - e) : 0;
    s.E = e;
    const fog = lerp(0.4, 1, smooth(clamp((300 - cp.z) / 420)));
    s.soft = smooth(clamp((cp.z - 40) / 240)) * e;
    s.alpha = (pt.hasStart ? 1 : smooth(seg(t, d0, d0 + 0.5))) * lerp(1, fog, e);
    return s;
  }
  // implosion: spiral into the centre and shrink
  function applyImplode(s, pt, t) {
    const Q = Math.pow(seg(t, TI + pt.s0, TE), 1.8);
    if (Q <= 0) return;
    const dx = s.x - CENTER.x, dy = s.y - CENTER.y;
    const a = 2.4 * Q * pt.dir, c = Math.cos(a), sn = Math.sin(a), f = Math.pow(1 - Q, 1.2);
    s.x = CENTER.x + (dx * c - dy * sn) * f;
    s.y = CENTER.y + (dx * sn + dy * c) * f;
    s.w *= 1 - 0.95 * Q; s.alpha *= 1 - 0.5 * Q; s.Q = Q;
  }

  // ---- drawing ------------------------------------------------------------------------------------------
  function drawBlobs(g, G, t) {
    const kb = smooth(seg(t, (A4 + 0.60), (A4 + 2.00))) * (1 - smooth(seg(t, TI, TI + 0.9))) * (1 + 0.1 * Math.sin(t * 0.9));
    if (kb <= 0.01) return;
    g.save(); g.globalCompositeOperation = "lighter";
    for (let c = 0; c < CENT.length; c++) {
      const p = proj(CENT[c][0], CENT[c][1], CENT[c][2], G), r = 250 * p.sc;
      const gr = g.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
      gr.addColorStop(0, `rgba(${BLOB[c]},${(0.16 * kb).toFixed(3)})`);
      gr.addColorStop(0.5, `rgba(${BLOB[c]},${(0.06 * kb).toFixed(3)})`);
      gr.addColorStop(1, `rgba(${BLOB[c]},0)`);
      g.fillStyle = gr; g.fillRect(p.x - r, p.y - r, 2 * r, 2 * r);
    }
    g.restore();
  }
  function drawSoft(g, pt, s, bucket) {
    const sf = softOf(pt.src, bucket, pt.ratio), k = s.w / bucket;
    const dw = sf.c.width * k, dh = sf.c.height * k;
    g.save(); g.globalAlpha = s.alpha * s.soft;
    g.translate(s.x, s.y); g.rotate(s.ang);
    g.drawImage(sf.c, -dw / 2, -dh / 2, dw, dh);
    g.restore();
  }
  function drawPic(g, pt, s) {
    if (s.w < 0.8 || s.alpha < 0.004) return;
    const bucket = s.w <= 128 ? 128 : 256, h = s.w * pt.ratio;
    let sharpA = s.alpha;
    if (bucket === 128 && s.soft > 0.02) { drawSoft(g, pt, s, bucket); sharpA = s.alpha * (1 - s.soft); }
    drawPhoto(g, pt.src, s.x, s.y, s.w, h, s.ang, { alpha: sharpA, shadow: s.sh, res: bucket / s.w });
  }
  function drawGlyph(g, pt, s) {
    if (s.alpha < 0.004 || s.w < 1) return;
    const img = glyphImg[pt.gk], h = s.w * H_GLYPH / W_GLYPH;
    g.save(); g.globalAlpha = s.alpha; g.translate(s.x, s.y); g.rotate(s.ang);
    g.drawImage(img, -s.w / 2, -h / 2, s.w, h);
    g.restore();
  }
  function drawPoint(g, pt, s) {
    if (s.glow > 0.01) {        // a highlighted neighbour: glow behind it
      const gs = s.w * 1.7;
      g.save(); g.globalCompositeOperation = "lighter"; g.globalAlpha = 0.3 * s.glow * s.alpha;
      g.drawImage(glowImg, s.x - gs / 2, s.y - gs / 2, gs, gs); g.restore();
    }
    if (pt.kind === "glyph") drawGlyph(g, pt, s); else drawPic(g, pt, s);
    if (s.glow > 0.01) {        // and a ring
      const h = s.w * pt.ratio;
      g.save(); g.strokeStyle = `rgba(${SKY},${(s.glow * s.alpha).toFixed(3)})`; g.lineWidth = 3;
      g.translate(s.x, s.y); g.rotate(s.ang); roundRect(g, -s.w / 2 - 4, -h / 2 - 4, s.w + 8, h + 8, 8); g.stroke(); g.restore();
    }
  }
  function drawStreak(g, pr, cu, Q, alpha) {
    g.save(); g.globalCompositeOperation = "lighter";
    g.strokeStyle = `rgba(190,212,255,${(0.55 * Q * alpha).toFixed(3)})`;
    g.lineWidth = 1.2 + 3.2 * Q; g.lineCap = "round";
    g.beginPath(); g.moveTo(pr.x, pr.y); g.lineTo(cu.x, cu.y); g.stroke();
    g.restore();
  }

  // the query: a glowing vector with a comet head, from the pill into the cloud
  function drawQuery(g, t, tip) {
    const lf = 1 - smooth(seg(t, (A4 + 3.80), (A4 + 4.20)));
    if (t < TS || lf <= 0) return;
    const ph = ease.inOut(seg(t, TS + 0.1, TS + 0.9));
    const C = { x: (PILL_R.x + tip.x) / 2, y: Math.min(PILL_R.y, tip.y) - 150 };
    const bez = (u) => {
      const a = (1 - u) * (1 - u), b = 2 * (1 - u) * u, c = u * u;
      return { x: a * PILL_R.x + b * C.x + c * tip.x, y: a * PILL_R.y + b * C.y + c * tip.y };
    };
    g.save(); g.globalCompositeOperation = "lighter";
    g.strokeStyle = `rgba(${SKY},${(0.9 * lf).toFixed(3)})`; g.lineWidth = 2.5; g.lineCap = "round";
    g.shadowColor = `rgba(${SKY},${(0.9 * lf).toFixed(3)})`; g.shadowBlur = 16;
    g.beginPath();
    for (let k = 0; k <= 32; k++) {
      const p = bez(ph * k / 32);
      k ? g.lineTo(p.x, p.y) : g.moveTo(p.x, p.y);
    }
    g.stroke();
    g.shadowBlur = 0;
    for (let k = 0; k < 14; k++) {       // trailing particles
      const p = bez(Math.max(0, ph - k * 0.03));
      const f = 1 - k / 14;
      g.fillStyle = `rgba(190,210,255,${(0.9 * f * lf).toFixed(3)})`;
      g.beginPath();
      g.arc(p.x + (hash(k * 2 + 1) - 0.5) * 10 * (1 - f), p.y + (hash(k * 2 + 2) - 0.5) * 10 * (1 - f), 1.2 + 2.4 * f, 0, 2 * Math.PI);
      g.fill();
    }
    const h = bez(ph);                    // the comet head
    const gr = g.createRadialGradient(h.x, h.y, 0, h.x, h.y, 34);
    gr.addColorStop(0, `rgba(255,255,255,${lf.toFixed(3)})`);
    gr.addColorStop(0.25, `rgba(${SKY},${(0.9 * lf).toFixed(3)})`);
    gr.addColorStop(1, `rgba(${SKY},0)`);
    g.fillStyle = gr; g.fillRect(h.x - 34, h.y - 34, 68, 68);
    g.restore();
  }

  // the laptop draws itself around the cloud; a GPU chip glows under it; a cloud is outside with its link cut
  function cloudPath(g) {
    g.beginPath();
    g.moveTo(1660, 560); g.arc(1632, 560, 28, 0, 2 * Math.PI);
    g.moveTo(1734, 528); g.arc(1690, 528, 44, 0, 2 * Math.PI);
    g.moveTo(1780, 562); g.arc(1750, 562, 30, 0, 2 * Math.PI);
    g.rect(1594, 556, 186, 34);
  }
  function drawLaptop(g, t) {
    const la = 1 - smooth(seg(t, (A4 + 5.40), (A4 + 6.00)));
    if (t < (A4 + 4.20) || la <= 0) return;
    const p1 = ease.out(seg(t, (A4 + 4.20), (A4 + 4.90))), p2 = ease.out(seg(t, (A4 + 4.60), (A4 + 5.20)));
    const chip = smooth(seg(t, (A4 + 4.90), (A4 + 5.40))), pulse = 0.6 + 0.4 * Math.sin(t * 6);
    g.save(); g.globalAlpha = la;
    g.lineJoin = "round"; g.lineCap = "round";
    if (p1 > 0) { roundRect(g, 600, 290, 720, 500, 22); g.fillStyle = `rgba(8,12,22,${(0.28 * p1).toFixed(3)})`; g.fill(); }
    g.strokeStyle = "rgba(255,255,255,0.7)"; g.lineWidth = 3;
    const L1 = 2440, L2 = 1832;
    g.setLineDash([L1, 9999]); g.lineDashOffset = L1 * (1 - p1);
    roundRect(g, 600, 290, 720, 500, 22); g.stroke();
    g.setLineDash([L2, 9999]); g.lineDashOffset = L2 * (1 - p2);
    roundRect(g, 520, 790, 880, 36, 12); g.stroke();
    g.setLineDash([]);
    if (chip > 0) {
      g.globalAlpha = la * chip;
      g.shadowColor = `rgba(${SKY},${(0.9 * pulse).toFixed(3)})`; g.shadowBlur = 28;
      roundRect(g, 930, 856, 60, 44, 8); g.fillStyle = "rgba(14,20,36,0.95)"; g.fill();
      g.shadowBlur = 0;
      g.strokeStyle = `rgb(${SKY})`; g.lineWidth = 2; g.stroke();
      g.beginPath();
      for (let k = 0; k < 4; k++) {
        const x = 938 + k * 14;
        g.moveTo(x, 848); g.lineTo(x, 856); g.moveTo(x, 900); g.lineTo(x, 908);
      }
      g.stroke();
      g.fillStyle = `rgb(${SKY})`; g.font = "600 15px Consolas, monospace";
      g.textAlign = "center"; g.textBaseline = "middle"; g.fillText("GPU", 960, 878);
    }
    g.restore();

    // outside: a cloud, and the dashed link to the laptop
    const ci = smooth(seg(t, (A4 + 4.60), (A4 + 5.00))) * la;
    const dim = t >= TCUT ? lerp(1, 0.25, smooth(seg(t, TCUT, TCUT + 0.3))) : 1;
    g.save(); g.globalAlpha = ci * dim;
    g.strokeStyle = "rgba(255,255,255,0.75)"; g.lineWidth = 5;
    cloudPath(g); g.stroke();
    g.fillStyle = "rgb(9,11,17)"; cloudPath(g); g.fill();
    g.restore();

    const la2 = smooth(seg(t, (A4 + 4.80), (A4 + 5.20))) * la * 0.7;
    if (la2 <= 0.01) return;
    g.save(); g.lineWidth = 2.5; g.strokeStyle = "rgba(255,255,255,1)";
    if (t < TCUT) {
      g.globalAlpha = la2;
      g.setLineDash([10, 8]); g.lineDashOffset = -t * 60;
      g.beginPath(); g.moveTo(1340, 540); g.lineTo(1612, 540); g.stroke();
    } else {
      const u = seg(t, TCUT, TCUT + 0.8), fa = la2 * (1 - smooth(u));
      g.globalAlpha = fa;
      g.beginPath(); g.moveTo(-26 * u + 1340, 46 * u + 540); g.lineTo(-26 * u + 1476, 46 * u + 540); g.stroke();
      g.beginPath(); g.moveTo(26 * u + 1476, 46 * u + 540); g.lineTo(26 * u + 1612, 46 * u + 540); g.stroke();
      const sp = seg(t, TCUT, TCUT + 0.45);   // a small spark at the cut
      if (sp > 0 && sp < 1) {
        g.globalCompositeOperation = "lighter"; g.globalAlpha = 1 - sp;
        for (let k = 0; k < 8; k++) {
          const a = k * Math.PI / 4 + 0.3;
          g.beginPath();
          g.moveTo(1476 + Math.cos(a) * (4 + 10 * sp), 540 + Math.sin(a) * (4 + 10 * sp));
          g.lineTo(1476 + Math.cos(a) * (8 + 38 * sp), 540 + Math.sin(a) * (8 + 38 * sp));
          g.stroke();
        }
      }
    }
    g.restore();
  }

  // the implosion: a core at the centre that grows into a near-white bloom
  function drawCore(g, t) {
    if (t < TI) return;
    const k = ease.in(seg(t, TI, TE));
    g.save(); g.globalCompositeOperation = "lighter";
    const R2 = 70 + 150 * k;
    let gr = g.createRadialGradient(CENTER.x, CENTER.y, 0, CENTER.x, CENTER.y, R2);
    gr.addColorStop(0, `rgba(200,215,255,${(0.35 * k).toFixed(3)})`);
    gr.addColorStop(0.35, `rgba(52,102,246,${(0.22 * k).toFixed(3)})`);
    gr.addColorStop(1, "rgba(52,102,246,0)");
    g.fillStyle = gr; g.fillRect(CENTER.x - R2, CENTER.y - R2, 2 * R2, 2 * R2);
    const R1 = 14 + 34 * k;
    gr = g.createRadialGradient(CENTER.x, CENTER.y, 0, CENTER.x, CENTER.y, R1);
    gr.addColorStop(0, `rgba(255,255,255,${k.toFixed(3)})`);
    gr.addColorStop(0.4, `rgba(214,226,255,${(0.85 * k).toFixed(3)})`);
    gr.addColorStop(1, `rgba(${SKY},0)`);
    g.fillStyle = gr; g.fillRect(CENTER.x - R1, CENTER.y - R1, 2 * R1, 2 * R1);
    g.restore();
  }

  // the query pill and the label: DOM
  function drawLabel(t, tip) {
    if (EL.label.style.display === "none") return;
    EL.label.style.left = (tip.x - 110).toFixed(0) + "px";
    EL.label.style.top = (tip.y - 236).toFixed(0) + "px";
  }

  // ---- chapter -------------------------------------------------------------------------------------------
  CH.add({
    name: "brain", z: 5,
    init() {
      const st = document.createElement("style");
      st.textContent =
        ".brn-h{font:700 76px/1.1 var(--display);letter-spacing:-0.035em;color:#eef0f6;white-space:nowrap}" +
        ".brn-s{font:400 28px/1.4 var(--font);color:var(--muted);margin-top:20px;white-space:nowrap}" +
        ".brn-spec{font:500 19px/1.2 var(--mono);color:var(--muted);letter-spacing:.04em;white-space:nowrap}" +
        ".brn-pill{font:600 34px/1.1 var(--font);color:#eef0f6;width:420px;box-sizing:border-box;text-align:center;" +
        "padding:20px 30px;border-radius:999px;background:rgba(19,22,30,.86);border:1px solid rgba(255,255,255,.14);" +
        "box-shadow:0 0 0 1px rgba(132,169,255,.10),0 0 46px rgba(52,102,246,.45);backdrop-filter:blur(20px);white-space:nowrap}" +
        ".brn-lab{font:600 24px/1 var(--font);color:#84a9ff;white-space:nowrap;letter-spacing:.02em}" +
        ".brn-lab::before{content:'';display:inline-block;width:10px;height:10px;border-radius:50%;background:#84a9ff;" +
        "box-shadow:0 0 14px #84a9ff;margin-right:12px;vertical-align:middle}";
      document.head.appendChild(st);

      EL.model = ui('<div class="brn-h">Powered by <span class="grad">EmbeddingGemma 2</span></div>' +
                    '<div class="brn-s">Google\'s open AI model sees text, photos, video and speech in one shared space.</div>',
                    "left:0;top:132px;width:1920px;text-align:center;");
      EL.spec = ui('<div class="brn-spec">740M parameters &middot; Apache-2.0 &middot; runs on LiteRT-LM</div>',
                   "left:0;top:1002px;width:1920px;text-align:center;");
      EL.local = ui('<div class="brn-h">It runs on your PC.</div>' +
                    '<div class="brn-s">Nothing is uploaded. Ever. Works offline.</div>',
                    "left:0;top:132px;width:1920px;text-align:center;");
      EL.localFoot = ui('<div class="brn-spec">DirectX 12 GPU (NVIDIA &middot; AMD &middot; Intel) or CPU</div>',
                        "left:0;top:1002px;width:1920px;text-align:center;");
      EL.pill = ui('<div class="brn-pill">mountains and a lake</div>', "left:" + (PILL_R.x - 420) + "px;top:556px;");
      EL.label = ui('<div class="brn-lab">nearest by meaning</div>', "left:0;top:0;");
      for (const k in EL) EL[k].style.display = "none";

      LAY = document.createElement("canvas"); LAY.width = W; LAY.height = H;
      lctx = LAY.getContext("2d");
      ["pdf", "wave", "film", "gear", "code"].forEach((k) => { glyphImg[k] = glyphCanvas(k); });
      glowImg = glowCanvas();

      MEDIA.pile.forEach((p, k) => posOf.set(p.src, k));
      MEDIA.queries.forEach((q) => q.images.forEach((im) => { if (im.e) cardMap.set(im.card, im); }));
      const acc = Array.from({ length: 7 }, () => [0, 0, 0, 0]);
      const addAcc = (ent) => { const a = acc[ent.c]; a[0] += ent.e[0] * SC; a[1] += ent.e[1] * SC; a[2] += ent.e[2] * SC; a[3]++; };
      MEDIA.pile.forEach(addAcc); cardMap.forEach(addAcc);
      CENT = acc.map((a) => (a[3] ? [SPREAD * a[0] / a[3], SPREAD * a[1] / a[3], SPREAD * a[2] / a[3]] : [0, 0, 0]));
      const cam0 = camera(T0);
      const used = new Set();

      // every SIM body: the pile's photos (in the pile at T0), and the search results (pulled out before T0)
      for (let i = 0; i < SIM.n; i++) {
        const b = SIM.bodies[i];
        const isRes = b.role === "result";
        const ent = entryOf(b.img), cl = ent.c, pos = cloudOf(ent, i);
        if (!isRes) used.add(b.img);
        let hasStart = false, sx, sy, sw, sa = 0;
        const p = pose(i, T0);
        if (p) {
          hasStart = true; [sx, sy] = toScreen(cam0, p.x, p.y); sw = b.w * cam0.zoom; sa = p.a;
        } else if (isRes && b.pickPose) {
          [sx, sy] = toScreen(cam0, b.pickPose[0], b.pickPose[1]); sw = b.w * cam0.zoom; sa = b.pickPose[2];
        } else continue;
        const pt = mk({ kind: "photo", idx: i, src: b.img, ratio: b.h / b.w, cl, pos, hasStart, sx, sy, sw, sa,
                        hl: isRes && b.q === 3, d: isRes ? 0.3 + 0.4 * hash(i + 3) : STAGGER * hash(i * 7 + 3) });
        pts.push(pt);
        if (hasStart) starts.push({ sx, sy, k: pts.length - 1 });
      }

      // extra pile photos (not in the physics): they rise from somewhere in the pile
      let cnt = 0, extra = 0;
      for (let pi = 0; pi < MEDIA.pile.length && extra < EXTRA_N; pi++) {
        const p = MEDIA.pile[pi];
        if (used.has(p.src)) continue;
        if (cnt++ % 3) continue;
        const k = extra++;
        const s0 = starts[Math.floor(hash(5000 + k) * starts.length)];
        const L = 220 * cam0.zoom;
        const sw = p.w >= p.h ? L : L * p.w / p.h;
        pts.push(mk({ kind: "photo", idx: 400 + k, src: p.src, ratio: p.h / p.w, cl: p.c, pos: cloudOf(p, 1000 + k),
                      hasStart: false, sx: s0.sx + (hash(k * 2 + 1) - 0.5) * 40, sy: s0.sy + (hash(k * 2 + 2) - 0.5) * 40,
                      sw, sa: 0, d: 0.2 + 0.7 * hash(7000 + k) }));
      }

      let hr = 0;
      pts.forEach((pt) => { if (pt.hl) pt.hlRank = hr++; });
      // glyph cards: kinds of things that are not photos
      GLYPHS.forEach((g, k) => {
        pts.push(mk({ kind: "glyph", gk: g[0], idx: 500 + k, cl: g[1], off: ball(2000 + k, 140),
                      t0: (A4 + 0.60) + 1.6 * hash(2100 + k) }));
      });
    },

    draw(t, ctx) {
      if (t < T0 || t >= T1) { for (const k in EL) EL[k].style.display = "none"; return; }

      // DOM text
      textFx(EL.model, t, TM, TLO);
      textFx(EL.spec, t, TM + 0.6, TLO);
      textFx(EL.local, t, TLO + 0.2, TI);
      textFx(EL.localFoot, t, TLO + 0.4, TI);
      textFx(EL.pill, t, TS, (A4 + 4.00));
      textFx(EL.label, t, (A4 + 3.30), (A4 + 4.00));

      const G = makeG(t), G2 = makeG(t - 0.05);
      const n = pts.length;
      const st = new Array(n);
      for (let i = 0; i < n; i++) st[i] = baseState(pts[i], t, G);

      // the query's neighbours (the "mountains and a lake" results): their centre is the tip of the query
      let tx = 0, ty = 0, nh = 0;
      for (let i = 0; i < n; i++) if (pts[i].hl) { tx += st[i].x; ty += st[i].y; nh++; }
      const tip0 = nh ? { x: tx / nh, y: ty / nh } : CENTER;
      const tip = { x: clamp(tip0.x, 980, 1320), y: clamp(tip0.y, 560, 700) };
      const kHL = ease.out(seg(t, (A4 + 2.90), (A4 + 3.40))) * (1 - smooth(seg(t, (A4 + 4.00), (A4 + 4.60))));
      const kD = smooth(seg(t, (A4 + 2.90), (A4 + 3.40))) * (1 - smooth(seg(t, (A4 + 4.00), (A4 + 4.60))));

      // pass 2: highlight, dim, implode; streaks use the position 0.05 s earlier
      const streaks = [];
      for (let i = 0; i < n; i++) {
        const pt = pts[i], s = st[i];
        if (pt.hl) {
          // the nearest neighbours fan out around the tip of the query: big, sharp, upright
          const a = -Math.PI * 0.95 + (pt.hlRank / Math.max(1, nh - 1)) * Math.PI * 1.9;
          const fx = tip.x + 205 * Math.cos(a), fy = tip.y + 150 * Math.sin(a);
          s.x = lerp(s.x, fx, kHL); s.y = lerp(s.y, fy, kHL);
          s.w = lerp(s.w, 150, kHL); s.ang *= 1 - kHL; s.soft *= 1 - kHL; s.alpha = lerp(s.alpha, 1, kHL);
          s.glow = kHL;
        } else s.alpha *= 1 - 0.72 * kD;
        if (t > TI) {
          applyImplode(s, pt, t);
          if (s.Q > 0.03) {
            const pr = baseState(pt, t - 0.05, G2);
            applyImplode(pr, pt, t - 0.05);
            streaks.push([pr, s]);
          }
        }
      }

      // draw order: far to near while in the cloud, index order while still in the pile
      const order = st.map((_, i) => i).sort((a, b) => st[b].z - st[a].z);
      const rank = new Array(n);
      order.forEach((id, k) => { rank[id] = k; });
      const draw = st.map((_, i) => i).sort((a, b) => (st[a].E * rank[a] + (1 - st[a].E) * pts[a].idx) -
                                                      (st[b].E * rank[b] + (1 - st[b].E) * pts[b].idx));
      // the query's neighbours come to the front while they are lit
      if (kHL > 0.01) draw.sort((a, b) => (pts[a].hl ? 1 : 0) - (pts[b].hl ? 1 : 0));

      // the photos are blurred and dimmed like the pile they came from, then sharpen as they rise
      const gk = smooth(seg(t, T0, (A4 + 1.30)));
      const bl = 5 * (1 - gk), br = lerp(0.52, 1, gk), sa = lerp(0.75, 1, gk);
      const g = gk < 1 ? lctx : ctx;
      if (gk < 1) { lctx.setTransform(1, 0, 0, 1, 0, 0); lctx.clearRect(0, 0, W, H); }

      drawBlobs(g, G, t);
      for (const i of draw) drawPoint(g, pts[i], st[i]);
      for (const [pr, cu] of streaks) drawStreak(g, pr, cu, cu.Q, cu.alpha);

      if (gk < 1) {
        ctx.save();
        ctx.filter = `blur(${bl.toFixed(2)}px) brightness(${br.toFixed(3)}) saturate(${sa.toFixed(3)})`;
        ctx.drawImage(LAY, 0, 0);
        ctx.restore();
      }

      drawQuery(ctx, t, tip);
      drawLabel(t, tip);
      drawLaptop(ctx, t);
      drawCore(ctx, t);
    },
  });
})();
