// The finale (TL.ACT.finale): the explosion of the photos toward the camera, the app icon rising out of the
// light, the wordmark, the tagline, the install line with the Alt + Space keycaps, the GitHub URL, then a slow
// push-in and the fade to black. Every frame is a pure function of t: the burst uses closed-form drag.
(function () {
  const TF = TL.FINALE;
  const T0 = TF.boom;                 // the gathered light explodes
  const A5 = TL.FINALE.boom;   // times below are offsets from this beat
  const CX = 960, CY = 540;
  const F = 1000;                     // burst camera focal length: screen scale = F / depth
  const Z0 = 6000;                    // photos start far away (tiny, at the core) and rush toward the lens
  const Z_ICON = 1000;                // the icon sits on this depth plane: nearer photos pass in front of it
  const ICON_Y = 320, ICON_PX = 220;
  const N_PILE = 360, N_EMBER = 36;

  // decorrelated hash per (photo, channel)
  const R = (n, k) => hash(n * 12.9898 + k * 78.233 + 1.7);

  let iconC = null, wrap = null, blackEl = null, letters = [], tagEl = null, instEl = null, keysEl = null,
      keyEls = [], urlEl = null, sweepEl = null, featEls = [];
  // the features other launchers don't have, one chip each, landing on 16ths after the URL
  const FEATURES = [["&#128193;", "Search this folder"], ["&#128444;&#65039;", "Search by image"], ["&#128197;", "\u201cphotos from last summer\u201d"],
                    ["&#128196;", "Scanned pages"], ["&#127897;&#65039;", "Voice search"], ["&#128273;", "API keys masked"]];
  let burst = [], embers = [], sparks = [], streaks = [];

  // one photo: a random 3D direction, a drag-decayed burst, then a slow terminal drift
  function makePhoto(src, n, ember) {
    const im = img(src);
    if (!im || !im.naturalWidth) return null;
    const ar = im.naturalWidth / im.naturalHeight;
    const L = ember ? lerp(260, 340, R(n, 1)) : lerp(300, 540, R(n, 1));          // long side, world px
    const w = ar >= 1 ? L : L * ar, h = ar >= 1 ? L / ar : L;
    const th = R(n, 2) * Math.PI * 2;
    let zc;                                                                        // +1 = straight at the camera
    if (ember) zc = lerp(-0.15, 0.15, R(n, 4));
    else {
      const r = R(n, 3);
      zc = r < 0.45 ? lerp(0.35, 0.8, R(n, 4)) : lerp(-0.1, 0.35, R(n, 4));   // none recede into the centre
    }
    const sp = Math.sqrt(1 - zc * zc);
    return {
      im, src, w, h, ember,
      dx: sp * Math.cos(th), dy: sp * Math.sin(th), dz: zc,
      Dinf: ember ? lerp(2300, 3600, R(n, 5)) : lerp(4000, 9000, R(n, 5)),          // distance covered by the drag
      tau: ember ? lerp(0.7, 1.2, R(n, 6)) : lerp(0.25, 0.5, R(n, 6)),             // drag time constant
      VT: ember ? lerp(60, 160, R(n, 7)) : lerp(300, 1200, R(n, 7)),               // terminal drift speed
      delay: ember ? 0 : R(n, 8) * 0.1,
      rot0: (R(n, 9) - 0.5) * 0.7, spin: (R(n, 10) - 0.5) * 6,
      Z0: ember ? lerp(3200, 4200, R(n, 11)) : Z0,
      wob: R(n, 12),
    };
  }

  // screen state of a photo at time u (seconds after the boom), or null before it starts / after it passes the lens
  function place(p, u) {
    const up = u - p.delay;
    if (up <= 0) return null;
    const k = 1 - Math.exp(-up / p.tau);
    const D = p.Dinf * k + p.VT * up;
    let X = p.dx * D, Y = p.dy * D;
    const Zc = p.Z0 - p.dz * D;
    if (Zc < 200) return null;
    if (p.ember) { Y *= 0.62; X += 30 * Math.sin(u * 0.5 + p.wob * 6.3); Y += 22 * Math.cos(u * 0.37 + p.wob * 9.1); }
    const s = F / Zc;
    const alpha = p.ember ? 0.42 * seg(u, 0, 1.4)
      : seg(up, 0, 0.05) * smooth(seg(Zc, 200, 600)) * (1 - smooth(seg(u, 1.3, 2.3)));   // burst clears by ~55.6 s
    const x = CX + X * s, y = CY + Y * s;
    // keep the centre column clear for the logo and wordmark: photos still near it fade out quickly
    const clear = p.ember ? 1 : 1 - (1 - smooth(seg(Math.hypot(x - CX, (y - CY) * 1.4), 140, 480))) * smooth(seg(u, 0.25, 0.6));
    return { x, y, s, Zc, a: p.rot0 + p.spin * p.tau * k, alpha: alpha * clear };
  }

  // one photo print (white border, rounded, cover-cropped), centred at (x, y) in the current transform
  function print(c, im, x, y, w, h, a, alpha, o) {
    if (alpha < 0.01 || w < 2) return;
    c.save();
    c.globalAlpha = alpha;
    c.translate(x, y); c.rotate(a);
    if (o.blur) c.filter = `blur(${o.blur.toFixed(1)}px)`;
    const r = Math.max(2, Math.min(w, h) * 0.035);
    if (o.shadow) {
      c.fillStyle = "rgba(0,0,0,.28)";
      roundRect(c, -w / 2 + w * 0.02, -h / 2 + h * 0.06, w, h, r); c.fill();
    }
    c.fillStyle = "#f4f1ea";
    roundRect(c, -w / 2, -h / 2, w, h, r); c.fill();
    const bw = Math.max(1.2, Math.min(w, h) * 0.025);
    const iw = w - 2 * bw, ih = h - 2 * bw;
    roundRect(c, -w / 2 + bw, -h / 2 + bw, iw, ih, r * 0.6); c.clip();
    const nw = im.naturalWidth, nh = im.naturalHeight, ar = iw / ih;
    let sx = 0, sy = 0, sw = nw, sh = nh;
    if (nw / nh > ar) { sw = nh * ar; sx = (nw - sw) / 2; } else { sh = nw / ar; sy = (nh - sh) / 2; }
    c.drawImage(im, sx, sy, sw, sh, -w / 2 + bw, -h / 2 + bw, iw, ih);
    c.restore();
  }

  // ---- DOM styles ------------------------------------------------------------------------------------
  const CSS = `
    .fin-letter { display: inline-block; }
    .fin-key { height: 64px; box-sizing: border-box; display: flex; align-items: center; justify-content: center;
      border-radius: 14px; border: 1px solid rgba(255,255,255,.08);
      background: linear-gradient(180deg, #2c3140 0%, #1b1f29 100%);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.16), 0 6px 0 #090b10, 0 18px 30px rgba(0,0,0,.5);
      color: #eef0f6; font-family: var(--font); font-weight: 600; font-size: 26px; letter-spacing: .01em; }
    .fin-pill { position: relative; overflow: hidden; height: 72px; box-sizing: border-box; padding: 0 40px;
      display: flex; align-items: center; gap: 14px; border-radius: 999px;
      background: linear-gradient(180deg, rgba(255,255,255,.13), rgba(255,255,255,.04));
      border: 1px solid rgba(255,255,255,.18);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 0 70px rgba(52,102,246,.28);
      backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
      font-family: var(--mono); font-size: 34px; color: #d9def0; white-space: nowrap; }
    .fin-sweep { position: absolute; top: 0; bottom: 0; width: 34%; left: -34%; display: none; pointer-events: none;
      background: linear-gradient(100deg, rgba(255,255,255,0) 0%, rgba(255,255,255,.34) 50%, rgba(255,255,255,0) 100%); }
  `;

  CH.add({
    name: "finale", z: 40,

    async init() {
      const st = document.createElement("style");
      st.textContent = CSS;
      document.head.appendChild(st);

      // the app icon, downscaled once to 440 px for a clean 220 px draw
      const im = new Image();
      im.src = "../../glimpse/assets/icon-1024.png";
      try {
        await im.decode();
        iconC = document.createElement("canvas"); iconC.width = iconC.height = 440;
        const g = iconC.getContext("2d");
        g.imageSmoothingQuality = "high";
        g.drawImage(im, 0, 0, 440, 440);
      } catch (e) { console.error("finale: icon missing " + im.src); iconC = null; }

      // the real photos: every search card, plus a hash-shuffled set of pile photos (the first 36 drift as embers)
      const order = MEDIA.pile.map((_, i) => i).sort((a, b) => hash(a * 2.71 + 3) - hash(b * 2.71 + 3));
      const cards = [];
      MEDIA.queries.forEach((q) => q.images.forEach((m) => cards.push(m.card)));
      const burstSrc = cards.concat(order.slice(N_EMBER, N_EMBER + N_PILE).map((i) => MEDIA.pile[i].src));
      burstSrc.forEach((src, n) => { const p = makePhoto(src, n + 100, false); if (p) burst.push(p); });
      order.slice(0, N_EMBER).forEach((i, n) => { const p = makePhoto(MEDIA.pile[i].src, n + 900, true); if (p) embers.push(p); });

      for (let n = 0; n < 150; n++) sparks.push({
        th: R(n, 20) * Math.PI * 2, D: lerp(900, 3200, R(n, 21)), tau: lerp(0.25, 0.6, R(n, 22)),
        delay: R(n, 23) * 0.06, r: lerp(1.2, 3.2, R(n, 24)), life: lerp(0.9, 1.6, R(n, 25)),
      });
      for (let n = 0; n < 64; n++) streaks.push({
        th: (n / 64) * Math.PI * 2 + (R(n, 30) - 0.5) * 0.08, len: lerp(220, 520, R(n, 31)), w: lerp(1.2, 3, R(n, 32)),
      });

      // DOM end card: one wrapper (scaled by the push-in), with its text layers
      wrap = ui("", "left:0;top:0;width:1920px;height:1080px;transform-origin:960px 540px;display:none;");
      wrap.innerHTML = `
        <div class="fin-title" style="position:absolute;left:0;right:0;top:452px;text-align:center;font-family:var(--display);font-weight:700;font-size:130px;line-height:1;letter-spacing:-0.04em;color:#fff;white-space:nowrap;text-shadow:0 0 80px rgba(120,160,255,.35)"></div>
        <div class="fin-tag" style="position:absolute;left:0;right:0;top:614px;text-align:center;font-family:var(--font);font-weight:500;font-size:46px;line-height:1.2;letter-spacing:-0.01em;color:#c9cfdb;white-space:nowrap">Find the <span class="grad">moment</span>, not the file.</div>
        <div class="fin-inst" style="position:absolute;left:0;right:0;top:700px;text-align:center;font-family:var(--font);font-size:30px;line-height:1.2;color:#9aa3b5;white-space:nowrap">Free &amp; open source &middot; any Windows 10 or 11 PC &middot; one-line install</div>
        <div class="fin-keys" style="position:absolute;left:0;right:0;top:764px;">
          <div style="display:flex;justify-content:center;align-items:center;gap:16px;white-space:nowrap">
            <div class="fin-key" style="width:132px">Alt</div>
            <span style="font-family:var(--font);font-size:34px;color:#6b7386;font-weight:300">+</span>
            <div class="fin-key" style="width:360px">Space</div>
            <span style="font-family:var(--font);font-size:30px;color:#9aa3b5;margin-left:14px">to search</span>
          </div>
        </div>
        <div class="fin-urlrow" style="position:absolute;left:0;right:0;top:884px;">
          <div style="display:flex;justify-content:center">
            <div class="fin-pill"><span>github.com/Ayushmaniar/glimpse</span><span style="color:#84a9ff;font-family:var(--font)">&#8599;</span><div class="fin-sweep"></div></div>
          </div>
        </div>`;
      const title = wrap.querySelector(".fin-title");
      "Glimpse".split("").forEach((ch) => {
        const s = document.createElement("span");
        s.className = "fin-letter"; s.textContent = ch;
        title.appendChild(s); letters.push(s);
      });
      tagEl = wrap.querySelector(".fin-tag");
      instEl = wrap.querySelector(".fin-inst");
      keysEl = wrap.querySelector(".fin-keys");
      keyEls = [...wrap.querySelectorAll(".fin-key")];
      urlEl = wrap.querySelector(".fin-urlrow");
      const feat = document.createElement("div");
      feat.style.cssText = "position:absolute;left:0;right:0;top:972px;display:flex;justify-content:center;gap:14px;white-space:nowrap";
      FEATURES.forEach(([ic, label]) => {
        const c = document.createElement("div");
        c.style.cssText = "display:flex;align-items:center;gap:9px;padding:9px 18px;border-radius:999px;background:rgba(255,255,255,.06);" +
          "box-shadow:inset 0 0 0 1px rgba(255,255,255,.12);font:500 21px var(--font);color:#c9cfdb";
        c.innerHTML = `<span style="font-size:20px">${ic}</span><span>${label}</span>`;
        feat.appendChild(c);
        featEls.push(c);
      });
      wrap.appendChild(feat);
      sweepEl = wrap.querySelector(".fin-sweep");

      // full-screen black for the end (not scaled, sits above the card)
      blackEl = ui("", "left:0;top:0;width:1920px;height:1080px;background:#000;opacity:0;display:none;pointer-events:none;");
    },

    draw(t, ctx) {
      const u = t - T0;
      if (!wrap) return;
      if (u < 0) { wrap.style.display = "none"; blackEl.style.display = "none"; return; }
      wrap.style.display = "";

      // camera: a slow drift all along, shake on the boom, and the push-in over the last 2.4 s
      const k = 1 + 0.03 * smooth(seg(t, TF.fade, TL.DURATION));
      const sh = pulse(t, T0, 0.14) * 18 + pulse(t, T0, 0.4) * 6;
      const cx = 6 * noise(t * 0.21, 11) + sh * noise(t * 31, 12);
      const cy = 5 * noise(t * 0.18, 13) + sh * noise(t * 29, 14);
      wrap.style.transform = `translate(${cx.toFixed(1)}px,${cy.toFixed(1)}px) scale(${k.toFixed(4)})`;

      ctx.translate(CX + cx, CY + cy); ctx.scale(k, k); ctx.translate(-CX, -CY);

      // the icon's position and scale (it rises out of the core)
      const icon = {
        x: CX,
        y: lerp(CY, ICON_Y, ease.out(seg(u, 0, 1.0))),
        sc: 0.2 + 0.8 * ease.backOut(seg(u, 0, 1.2)),
        rot: lerp(-0.22, 0, ease.out(seg(u, 0, 1.3))) + 0.02 * Math.sin(t * 1.2) * smooth(seg(u, 1.3, 2.5)),
      };
      const gi = ease.out(seg(u, 0.05, 1.3));
      const breathe = 0.85 + 0.15 * beatPulse(t, T0, TL.BEAT, 0.12);

      // 1) the core blooms white, then the streaks and sparks leave it
      ctx.globalCompositeOperation = "lighter";
      const cb = Math.exp(-u / 0.32);
      if (cb > 0.003) {
        const g = ctx.createRadialGradient(CX, CY, 0, CX, CY, 640);
        g.addColorStop(0, `rgba(240,246,255,${cb.toFixed(3)})`);
        g.addColorStop(0.25, `rgba(150,185,255,${(cb * 0.6).toFixed(3)})`);
        g.addColorStop(1, "rgba(60,90,220,0)");
        ctx.fillStyle = g; ctx.fillRect(CX - 640, CY - 640, 1280, 1280);
      }
      ctx.lineCap = "round";
      for (const s of streaks) {
        const e = seg(u, 0, 0.7);
        if (e >= 1) continue;
        const a = (1 - seg(u, 0, 0.8)) * 0.55;
        if (a < 0.01) continue;
        const r1 = 90 + 1400 * ease.out(e), r0 = Math.max(0, r1 - s.len * (1 - seg(u, 0, 0.9)));
        ctx.strokeStyle = `rgba(190,210,255,${a.toFixed(3)})`; ctx.lineWidth = s.w;
        ctx.beginPath();
        ctx.moveTo(CX + Math.cos(s.th) * r0, CY + Math.sin(s.th) * r0);
        ctx.lineTo(CX + Math.cos(s.th) * r1, CY + Math.sin(s.th) * r1);
        ctx.stroke();
      }
      for (const s of sparks) {
        const up = u - s.delay;
        if (up <= 0 || up > s.life) continue;
        const D = s.D * (1 - Math.exp(-up / s.tau));
        const a = (1 - seg(up, 0, s.life)) * 0.85;
        ctx.fillStyle = `rgba(214,228,255,${a.toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(CX + Math.cos(s.th) * D, CY + Math.sin(s.th) * D * 0.9, s.r, 0, Math.PI * 2);
        ctx.fill();
      }

      // 2) the shockwave rings
      for (let j = 0; j < 2; j++) {
        const uj = u - j * 0.1;
        if (uj <= 0) continue;
        const e = seg(uj, 0, 1.0);
        const a = (1 - e) * (j ? 0.45 : 0.9);
        if (a < 0.01) continue;
        const r = 120 + 1500 * ease.out(e);
        ctx.strokeStyle = `rgba(170,200,255,${(a * 0.35).toFixed(3)})`; ctx.lineWidth = 26 * (1 - e) + 6;
        ctx.beginPath(); ctx.arc(CX, CY, r, 0, Math.PI * 2); ctx.stroke();
        ctx.strokeStyle = `rgba(220,232,255,${a.toFixed(3)})`; ctx.lineWidth = 4 * (1 - e) + 1.5;
        ctx.beginPath(); ctx.arc(CX, CY, r, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.globalCompositeOperation = "source-over";

      // 3) the slow embers, dim and soft, behind everything
      for (const p of embers) {
        const s = place(p, u);
        if (!s || s.alpha < 0.01) continue;
        const hw = Math.max(p.w, p.h) * s.s * 0.75;
        if (s.x + hw < 0 || s.x - hw > 1920 || s.y + hw < 0 || s.y - hw > 1080) continue;
        print(ctx, p.im, s.x, s.y, p.w * s.s, p.h * s.s, s.a, s.alpha, { blur: 2.5 });
      }

      // 4) the burst: sorted far to near, the nearest 25 that are large get depth-of-field blur
      const shots = [];
      for (const p of burst) {
        const s = place(p, u);
        if (!s || s.alpha < 0.005) continue;
        const hw = Math.max(p.w, p.h) * s.s * 0.75;
        if (s.x + hw < -40 || s.x - hw > 1960 || s.y + hw < -40 || s.y - hw > 1120) continue;
        shots.push({ p, s, blur: 0 });
      }
      shots.sort((a, b) => b.s.Zc - a.s.Zc);
      let nb = 0;
      for (let i = shots.length - 1; i >= 0 && nb < 16; i--) {
        if (shots[i].s.s <= 1.6) break;
        shots[i].blur = Math.min(9, 3 + (shots[i].s.s - 1.6) * 3); nb++;
      }

      const drawShot = (q) => {
        const { p, s } = q;
        // motion streaks: fast photos leave 3 fading ghosts along their path
        if (!p.ember && s.s > 0.35) {
          const pv = place(p, u - 0.03);
          const v = pv ? Math.hypot(s.x - pv.x, s.y - pv.y) / 0.03 : 0;
          if (v > 380) {
            for (let j = 3; j >= 1; j--) {
              const g = place(p, u - 0.025 * j);
              if (g) print(ctx, p.im, g.x, g.y, p.w * g.s, p.h * g.s, g.a, s.alpha * [0.1, 0.2, 0.36][j - 1], {});
            }
          }
        }
        print(ctx, p.im, s.x, s.y, p.w * s.s, p.h * s.s, s.a, s.alpha, { blur: q.blur, shadow: true });
      };
      let i = 0;
      for (; i < shots.length && shots[i].s.Zc > Z_ICON; i++) drawShot(shots[i]);

      // 5) the glow behind the icon, breathing on the beat
      const gr = 520 * (0.55 + 0.45 * gi);
      if (gi > 0.001) {
        ctx.globalCompositeOperation = "lighter";
        const g = ctx.createRadialGradient(icon.x, icon.y, 0, icon.x, icon.y, gr);
        g.addColorStop(0, `rgba(96,140,255,${(0.7 * gi * breathe).toFixed(3)})`);
        g.addColorStop(0.35, `rgba(120,100,255,${(0.32 * gi * breathe).toFixed(3)})`);
        g.addColorStop(1, "rgba(139,92,246,0)");
        ctx.fillStyle = g; ctx.fillRect(icon.x - gr, icon.y - gr, gr * 2, gr * 2);
        ctx.globalCompositeOperation = "source-over";
      }

      // 6) the app icon (over the far photos, under the near ones)
      if (iconC) {
        const ia = seg(u, 0, 0.12);
        ctx.save();
        ctx.translate(icon.x, icon.y); ctx.rotate(icon.rot); ctx.scale(icon.sc, icon.sc);
        ctx.globalAlpha = ia;
        ctx.shadowColor = "rgba(70,120,255,.65)"; ctx.shadowBlur = 90;
        ctx.drawImage(iconC, -ICON_PX / 2, -ICON_PX / 2, ICON_PX, ICON_PX);
        ctx.restore();
      }

      // 7) the near photos fly past the icon, toward the lens
      for (; i < shots.length; i++) drawShot(shots[i]);

      // 8) the soft glow under the install and URL lines
      const ug = seg(t, TF.url, TF.url + 1.0) * (1 - seg(t, (A5 + 10.60), (A5 + 11.80)));
      if (ug > 0.001) {
        ctx.globalCompositeOperation = "lighter";
        const g = ctx.createRadialGradient(CX, 920, 0, CX, 920, 420);
        g.addColorStop(0, `rgba(80,120,255,${(0.2 * ug).toFixed(3)})`);
        g.addColorStop(1, "rgba(80,120,255,0)");
        ctx.fillStyle = g; ctx.fillRect(CX - 420, 500, 840, 840);
        ctx.globalCompositeOperation = "source-over";
      }

      // 9) the flash: blinding at 52.8, a long soft bloom after
      const fl = Math.min(1, Math.exp(-u / 0.12) * 0.95 + Math.exp(-u / 0.4) * 0.14);
      if (fl > 0.004) {
        ctx.globalCompositeOperation = "lighter";
        ctx.fillStyle = `rgba(226,236,255,${fl.toFixed(3)})`;
        ctx.fillRect(-200, -200, 2320, 1480);
        ctx.globalCompositeOperation = "source-over";
      }

      // DOM: text, keycaps, pill, sweep, black
      letters.forEach((el, n) => textFx(el, t, TF.title + n * 0.025, null, { rise: 30 }));
      textFx(tagEl, t, TF.tagline, null);
      textFx(instEl, t, TF.install, null);
      textFx(keysEl, t, TF.install, null);
      const tp = TF.install + TL.b(2);
      const press = Math.min(seg(t, tp, tp + 0.07), 1 - seg(t, tp + 0.07, tp + 0.24));
      keyEls.forEach((el) => {
        el.style.transform = `translateY(${(5 * press).toFixed(2)}px)`;
        el.style.boxShadow = `inset 0 1px 0 rgba(255,255,255,.16), 0 ${(6 * (1 - press)).toFixed(2)}px 0 #090b10, 0 ${(18 - 6 * press).toFixed(1)}px 30px rgba(0,0,0,.5)`;
      });
      textFx(urlEl, t, TF.url, null);
      featEls.forEach((el, n) => textFx(el, t, TF.url + TL.b(1) + n * TL.BEAT / 4, null, { rise: 16 }));
      const sw = t >= TF.url + 0.4 && t <= TF.url + 1.5;
      sweepEl.style.display = sw ? "" : "none";
      if (sw) sweepEl.style.left = (-34 + 168 * ease.inOut(seg(t, TF.url + 0.4, TF.url + 1.5))).toFixed(2) + "%";

      blackEl.style.display = t >= (A5 + 10.60) ? "" : "none";
      blackEl.style.opacity = smooth(seg(t, (A5 + 10.60), (A5 + 11.80))).toFixed(3);
    },
  });
})();
