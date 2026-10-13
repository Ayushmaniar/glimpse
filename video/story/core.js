// Shared engine for the launch film. Every frame is a pure function of t (seconds): frame(t) paints it all.
//
// Globals provided here (read-only for chapters):
//   TL                 timeline.js (beats, acts, queries, events)
//   SIM                physics: SIM.bodies[i] = {img, w, h, role, size, q, j, spawnV, targetV, pickV, pickPose}
//   MEDIA              video/private/media/manifest.json: {counts, queries[k]: {q, ms, images[], video}, pile[]}
//   img(src)           a decoded HTMLImageElement for a media-relative path ("pile/0001.jpg", "q/paneer/hero0.jpg")
//   sprite(src, w, h)  that photo as a "print" (rounded corners, thin white border) at w x h px, cached
//   pose(i, t)         physics pose of body i at video time t: {x, y, a} in world px, or null if not in the world
//   camera(t)          {cx, cy, zoom, rot} for the pile world;  toScreen(cam, x, y) -> [sx, sy]
//   LAUNCHER           geometry of the Glimpse window (screen px), shared by intro.js (keycaps morph) and ask.js
//   helpers            clamp lerp seg ease.* kf hash noise pulse beatPulse smooth
//   drawPhoto(ctx, src, x, y, w, h, angle, o)   o: {alpha, shadow (0..1), scale}
//   textFx(el, t, tIn, tOut, o)                 rise + unblur in at tIn, out at tOut (DOM text)
//   ui(html)           create a DOM element inside #ui (absolute positioned by the chapter)
//
// Chapters register with  CH.add({ name, z, draw(t, ctx) }) . draw is called EVERY frame in z order;
// a chapter must hide its DOM and return quickly when t is outside its own time range.
(function () {
  const W = TL.W, H = TL.H;

  // ---- math ---------------------------------------------------------------------------------------
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, k) => a + (b - a) * k;
  const seg = (t, a, b) => clamp((t - a) / (b - a));
  const smooth = (x) => { x = clamp(x); return x * x * (3 - 2 * x); };
  const ease = {
    linear: (x) => clamp(x),
    inOut: (x) => { x = clamp(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; },
    out: (x) => 1 - Math.pow(1 - clamp(x), 3),
    in: (x) => Math.pow(clamp(x), 3),
    expoOut: (x) => (x = clamp(x)) === 1 ? 1 : 1 - Math.pow(2, -10 * x),
    expoIn: (x) => (x = clamp(x)) === 0 ? 0 : Math.pow(2, 10 * x - 10),
    backOut: (x, s = 1.70158) => { x = clamp(x) - 1; return x * x * ((s + 1) * x + s) + 1; },
    elasticOut: (x) => { x = clamp(x); return x === 0 || x === 1 ? x : Math.pow(2, -10 * x) * Math.sin((x * 10 - 0.75) * (2 * Math.PI) / 3) + 1; },
    smooth,
  };
  // keyframes: kf(t, [[t0, v0], [t1, v1], ...], easeFn) ; values may be numbers or arrays
  function kf(t, keys, e = ease.inOut) {
    if (t <= keys[0][0]) return keys[0][1];
    for (let i = 1; i < keys.length; i++) {
      if (t <= keys[i][0]) {
        const [ta, va] = keys[i - 1], [tb, vb] = keys[i];
        const k = e((t - ta) / (tb - ta));
        return Array.isArray(va) ? va.map((v, j) => lerp(v, vb[j], k)) : lerp(va, vb, k);
      }
    }
    return keys[keys.length - 1][1];
  }
  const hash = (i) => { const x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
  // smooth 1D value noise in [-1, 1]
  function noise(t, seed = 0) {
    const i = Math.floor(t), f = t - i, u = f * f * (3 - 2 * f);
    return lerp(hash(i + seed * 101.3) * 2 - 1, hash(i + 1 + seed * 101.3) * 2 - 1, u);
  }
  // 1 at time `at`, decaying with time constant `decay` afterwards (0 before)
  const pulse = (t, at, decay = 0.25) => (t < at ? 0 : Math.exp(-(t - at) / decay));
  // pulse on every beat (or subdivision) since `from`
  function beatPulse(t, from = 0, every = TL.BEAT, decay = 0.12) {
    if (t < from) return 0;
    const ph = (t - from) % every;
    return Math.exp(-ph / decay);
  }

  // ---- assets ---------------------------------------------------------------------------------------
  const MEDIA_ROOT = "../private/media/";
  const OUT_ROOT = "../out/story/";
  const images = new Map();
  function load(src) {
    if (images.has(src)) return images.get(src).p;
    const im = new Image();
    const p = new Promise((res) => { im.onload = () => im.decode().then(res, res); im.onerror = () => { console.error("missing " + src); res(); }; });
    im.src = MEDIA_ROOT + src;
    images.set(src, { im, p });
    return p;
  }
  const img = (src) => images.get(src)?.im;

  const sprites = new Map();
  function sprite(src, w, h) {
    w = Math.max(8, Math.round(w)); h = Math.max(8, Math.round(h));
    const key = src + "@" + w + "x" + h;
    let c = sprites.get(key);
    if (c) return c;
    const im = img(src);
    c = document.createElement("canvas");
    c.width = w; c.height = h;
    const g = c.getContext("2d");
    const r = Math.max(3, Math.min(w, h) * 0.035), bw = Math.max(2, Math.min(w, h) * 0.025);
    g.fillStyle = "#f4f1ea";
    roundRect(g, 0, 0, w, h, r); g.fill();
    if (im) {
      g.save(); roundRect(g, bw, bw, w - 2 * bw, h - 2 * bw, r * 0.6); g.clip();
      // cover-fit
      const s = Math.max((w - 2 * bw) / im.naturalWidth, (h - 2 * bw) / im.naturalHeight);
      const dw = im.naturalWidth * s, dh = im.naturalHeight * s;
      g.drawImage(im, bw + (w - 2 * bw - dw) / 2, bw + (h - 2 * bw - dh) / 2, dw, dh);
      // a whisper of sheen from the top-left
      const gr = g.createLinearGradient(0, 0, w, h);
      gr.addColorStop(0, "rgba(255,255,255,.10)"); gr.addColorStop(0.45, "rgba(255,255,255,0)");
      g.fillStyle = gr; g.fillRect(0, 0, w, h);
      g.restore();
    }
    sprites.set(key, c);
    return c;
  }
  function roundRect(g, x, y, w, h, r) {
    g.beginPath();
    g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }
  // soft shadow: one blurred rounded rect, stretched to any card
  let shadowSprite = null;
  function shadowImg() {
    if (shadowSprite) return shadowSprite;
    const c = document.createElement("canvas"); c.width = 160; c.height = 160;
    const g = c.getContext("2d");
    g.filter = "blur(14px)"; g.fillStyle = "rgba(0,0,0,1)"; roundRect(g, 40, 40, 80, 80, 8); g.fill();
    shadowSprite = c;
    return c;
  }
  // a photo print centred at (x, y), rotated by angle, in the current transform
  function drawPhoto(ctx, src, x, y, w, h, angle = 0, o = {}) {
    const alpha = o.alpha ?? 1;
    if (alpha <= 0.002 || w < 1 || h < 1) return;
    const res = o.res ?? 1;   // sprite resolution multiplier (e.g. camera zoom, to avoid giant sprites when zoomed out)
    ctx.save();
    ctx.translate(x, y); ctx.rotate(angle);
    ctx.globalAlpha = alpha;
    const sh = o.shadow ?? 0.55;
    if (sh > 0) {
      ctx.globalAlpha = alpha * sh;
      const off = Math.min(w, h) * 0.06;
      ctx.drawImage(shadowImg(), -w / 2 - w * 0.25 + off * 0.3, -h / 2 - h * 0.25 + off, w * 1.5, h * 1.5);
      ctx.globalAlpha = alpha;
    }
    const sw = Math.min(w * res, o.maxRes ?? 2000), sh2 = sw * h / w;
    ctx.drawImage(sprite(src, sw, sh2), -w / 2, -h / 2, w, h);
    ctx.restore();
  }

  // ---- physics playback --------------------------------------------------------------------------
  let SIMPOSE = null;
  function pose(i, t) {
    const s = TL.simTime(t) * SIM.simHz;
    if (s < 0) return null;
    const f0 = Math.min(SIM.frames - 1, Math.floor(s)), f1 = Math.min(SIM.frames - 1, f0 + 1), k = s - f0;
    const a = (f0 * SIM.n + i) * 3, b = (f1 * SIM.n + i) * 3;
    const x0 = SIMPOSE[a];
    if (Number.isNaN(x0)) return null;
    const x1 = SIMPOSE[b];
    if (Number.isNaN(x1)) return { x: x0, y: SIMPOSE[a + 1], a: SIMPOSE[a + 2] };
    let da = SIMPOSE[b + 2] - SIMPOSE[a + 2];
    return { x: lerp(x0, x1, k), y: lerp(SIMPOSE[a + 1], SIMPOSE[b + 1], k), a: SIMPOSE[a + 2] + da * k };
  }
  // last recorded pose (end of the simulation, start of act 4)
  // (null for bodies that were pulled out as search results)
  function finalPose(i) {
    const a = ((SIM.frames - 1) * SIM.n + i) * 3;
    return Number.isNaN(SIMPOSE[a]) ? null : { x: SIMPOSE[a], y: SIMPOSE[a + 1], a: SIMPOSE[a + 2] };
  }

  // ---- camera ---------------------------------------------------------------------------------------
  // act 1 (must match sim.js cam()): floor near the bottom, zooming out as the pile grows
  function camAct1(t) {
    const k1 = smooth((t - TL.bar(3)) / TL.BAR), k2 = smooth((t - TL.bar(4)) / (TL.BAR * 1.2));
    const zoom = 1.0 * Math.pow(0.62, k1) * Math.pow(0.36 / 0.62, k2);
    return { cx: 0, cy: -(1010 - H / 2) / zoom, zoom, rot: 0 };
  }
  // act 3: the pile top sits at screen y ~ 800, under the launcher
  function camAsk(t) {
    const zoom = 0.40 + 0.02 * seg(t, TL.ACT.ask[0], TL.ACT.ask[1]);
    const top = SIM.topAfterTurn;
    return { cx: 120 * noise(t * 0.15, 3), cy: top - (800 - H / 2) / zoom, zoom, rot: 0 };
  }
  function camera(t) {
    let c;
    if (t < TL.HIT.turn) c = camAct1(t);
    else {
      const a = camAct1(TL.HIT.turn), b = camAsk(t);
      const k = ease.inOut(seg(t, TL.HIT.turn + 0.15, TL.ACT.ask[0]));
      c = { cx: lerp(a.cx, b.cx, k), cy: lerp(a.cy, b.cy, k), zoom: lerp(a.zoom, b.zoom, k), rot: 0 };
    }
    // shake on the two big hits
    const sh = pulse(t, TL.HIT.turn, 0.28) * 34 + pulse(t, TL.HIT.finale, 0.35) * 40;
    if (sh > 0.1) {
      c.cx += noise(t * 31, 1) * sh / c.zoom; c.cy += noise(t * 29, 2) * sh / c.zoom;
      c.rot = noise(t * 23, 5) * sh * 0.0009;
    }
    // slow handheld drift in act 1
    if (t < TL.HIT.turn) { c.cx += noise(t * 0.25, 7) * 14 / c.zoom; c.cy += noise(t * 0.21, 8) * 10 / c.zoom; }
    return c;
  }
  function applyCamera(ctx, c) {
    ctx.translate(W / 2, H / 2); ctx.rotate(c.rot || 0); ctx.scale(c.zoom, c.zoom); ctx.translate(-c.cx, -c.cy);
  }
  const toScreen = (c, x, y) => {
    const dx = (x - c.cx) * c.zoom, dy = (y - c.cy) * c.zoom, r = c.rot || 0;
    return [W / 2 + dx * Math.cos(r) - dy * Math.sin(r), H / 2 + dx * Math.sin(r) + dy * Math.cos(r)];
  };

  // the Glimpse window (screen px)
  const LAUNCHER = { x: 340, y: 96, w: 1240, bar: { x: 362, y: 118, w: 1196, h: 92 }, radius: 26,
                     results: { x: 362, y: 236, w: 1196 } };

  // ---- DOM text ----------------------------------------------------------------------------------------
  const uiRoot = () => document.getElementById("ui");
  function ui(html, style = "") {
    const d = document.createElement("div");
    d.innerHTML = html; d.style.cssText = "position:absolute;" + style;
    uiRoot().appendChild(d);
    return d;
  }
  // rise + unblur in at tIn (dur 0.35 s), mirror out at tOut. Returns visibility 0..1.
  function textFx(el, t, tIn, tOut, o = {}) {
    const d = o.dur ?? 0.38, rise = o.rise ?? 24;
    const kin = ease.out(seg(t, tIn, tIn + d)), kout = tOut == null ? 0 : ease.in(seg(t, tOut, tOut + d * 0.8));
    const v = kin * (1 - kout);
    if (v <= 0.001) { el.style.display = "none"; return 0; }
    el.style.display = "";
    el.style.opacity = v.toFixed(3);
    const y = (1 - kin) * rise - kout * rise;
    el.style.transform = `translateY(${y.toFixed(1)}px)` + (o.scale ? ` scale(${lerp(o.scale, 1, kin)})` : "");
    el.style.filter = v < 0.999 ? `blur(${((1 - v) * 10).toFixed(1)}px)` : "none";
    return v;
  }

  // ---- background -------------------------------------------------------------------------------------
  function background(ctx, t) {
    ctx.fillStyle = "#07080c"; ctx.fillRect(0, 0, W, H);
    // slow light blooms; brighter after the turn
    const lit = 0.25 + 0.75 * ease.out(seg(t, TL.HIT.turn - 0.1, TL.HIT.turn + 1.2));
    const blobs = [[0.28, 0.35, 900, "52,102,246"], [0.74, 0.62, 820, "139,92,246"], [0.52, 0.95, 700, "15,138,168"]];
    blobs.forEach(([bx, by, r, col], i) => {
      const x = W * bx + 140 * noise(t * 0.09, 20 + i), y = H * by + 100 * noise(t * 0.08, 30 + i);
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(${col},${(0.20 * lit).toFixed(3)})`); g.addColorStop(1, `rgba(${col},0)`);
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    });
  }

  // ---- chapters + frame -------------------------------------------------------------------------------
  const chapters = [];
  window.CH = { add: (c) => { chapters.push(c); chapters.sort((a, b) => (a.z ?? 0) - (b.z ?? 0)); }, list: chapters };

  let ctx = null;
  window.frame = function (t) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1; ctx.filter = "none"; ctx.globalCompositeOperation = "source-over";
    background(ctx, t);
    for (const c of chapters) {
      ctx.save();
      try { c.draw(t, ctx); } catch (e) { console.error(c.name + ": " + e.stack); }
      ctx.restore();
    }
    document.getElementById("grain").style.transform = `translate(${(hash(Math.floor(t * 30)) * 40 - 20).toFixed(0)}px,${(hash(Math.floor(t * 30) + 7) * 40 - 20).toFixed(0)}px)`;
  };

  async function loadBinary(url) {
    const r = await fetch(url);
    return new Float32Array(await r.arrayBuffer());
  }
  window.ready = (async function () {
    const [manifest, sim, bin] = await Promise.all([
      fetch(MEDIA_ROOT + "manifest.json").then((r) => r.json()),
      fetch(OUT_ROOT + "sim.json").then((r) => r.json()),
      loadBinary(OUT_ROOT + "sim.bin"),
    ]);
    window.MEDIA = manifest; window.SIM = sim; SIMPOSE = bin;
    const srcs = new Set(sim.bodies.map((b) => b.img));
    manifest.pile.forEach((p) => srcs.add(p.src));
    manifest.queries.forEach((q) => {
      q.images.forEach((im) => { srcs.add(im.card); srcs.add(im.hero); });
      if (q.video) for (let f = 1; f <= q.video.frames; f++) srcs.add(`${q.video.dir}/${String(f).padStart(3, "0")}.jpg`);
    });
    await Promise.all([...srcs].map(load));
    await document.fonts.ready;
    const cv = document.getElementById("c");
    ctx = cv.getContext("2d");
    for (const c of chapters) if (c.init) await c.init();
    return true;
  })();

  Object.assign(window, { clamp, lerp, seg, smooth, ease, kf, hash, noise, pulse, beatPulse, img, sprite, drawPhoto,
                          roundRect, pose, finalPose, camera, applyCamera, toScreen, LAUNCHER, ui, textFx, W, H,
                          MEDIA_ROOT, load });
})();
