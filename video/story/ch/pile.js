// The pile: every photo that falls in act 1 (physics from sim.js), the far "rain" of thousands more,
// dust motes, and the blurred pile under the launcher in acts 2-3. Search results that have been pulled
// out of the pile (t >= pickV) are NOT drawn here: ask.js flies them into the launcher.
(function () {
  const END = TL.ACT.brain[0];                 // brain.js takes the photos from here
  const RAIN_T0 = TL.FLOOD.t0 - 0.4, RAIN_T1 = TL.FLOOD.t1;
  const RAIN_N = 900;
  const ZOOM_LEVELS = [1, 0.7, 0.5, 0.36, 0.25];
  const resFor = (z) => ZOOM_LEVELS.reduce((a, b) => (Math.abs(b - z) < Math.abs(a - z) ? b : a));

  let off = null, octx = null;
  let rain = [];

  CH.add({
    name: "pile", z: 0,
    init() {
      off = document.createElement("canvas"); off.width = W; off.height = H; octx = off.getContext("2d");
      // rain: far photos, denser toward the end of the flood
      for (let n = 0; n < RAIN_N; n++) {
        const u = Math.pow(hash(n * 3 + 1), 0.55);               // more late spawns
        const p = MEDIA.pile[(n * 7 + 13) % MEDIA.pile.length];
        const d = 0.22 + 0.55 * hash(n * 3 + 2);                 // depth: 0.22 far .. 0.77 nearer
        const long = 70 + 90 * d;
        const s = long / Math.max(p.w, p.h);
        rain.push({ src: p.src, t0: RAIN_T0 + u * (RAIN_T1 - RAIN_T0), x: hash(n * 3 + 3) * W, d,
                    w: p.w * s, h: p.h * s, a0: (hash(n + 77) - 0.5) * 0.8, spin: (hash(n + 99) - 0.5) * 1.6 });
      }
      rain.sort((a, b) => a.d - b.d);
    },
    draw(t, ctx) {
      if (t >= END) return;
      const cam = camera(t);
      dust(ctx, t);
      drawRain(ctx, t);

      // the physics pile, through the camera
      const after = t >= TL.HIT.turn;
      const g = after ? octx : ctx;
      if (after) { octx.setTransform(1, 0, 0, 1, 0, 0); octx.clearRect(0, 0, W, H); }
      g.save();
      applyCamera(g, cam);
      floor(g, cam, t);
      const res = resFor(cam.zoom);
      const glowK = seg(t, TL.SLOW.t0 - 0.3, TL.SLOW.t0 + 0.4) * (1 - seg(t, TL.HIT.turn, TL.HIT.turn + 0.8));
      for (let i = 0; i < SIM.n; i++) {
        const b = SIM.bodies[i];
        if (t < b.spawnV - 0.02) continue;
        if (b.role === "result" && t >= b.pickV) continue;
        const p = pose(i, t);
        if (!p) continue;
        // motion blur ghosts for fast fallers
        const q = pose(i, t - 1 / 45);
        if (q) {
          const v = Math.hypot(p.x - q.x, p.y - q.y) * cam.zoom;
          if (v > 9) {
            for (let k = 2; k >= 1; k--) {
              const r = pose(i, t - k / 90);
              if (r) drawPhoto(g, b.img, r.x, r.y, b.w, b.h, r.a, { alpha: 0.18 * k, shadow: 0, res });
            }
          }
        }
        if (b.role === "result" && glowK > 0) glow(g, p, b, glowK, t);
        drawPhoto(g, b.img, p.x, p.y, b.w, b.h, p.a, { shadow: 0.55, res });
      }
      g.restore();

      if (after) {
        // under the launcher: blurred, darker; it still settles and shifts with real physics
        const k = ease.inOut(seg(t, TL.HIT.turn + 0.2, TL.ACT.ask[0]));
        ctx.save();
        ctx.filter = `blur(${(k * 5).toFixed(1)}px) brightness(${(1 - 0.48 * k).toFixed(3)}) saturate(${(1 - 0.25 * k).toFixed(3)})`;
        ctx.drawImage(off, 0, 0);
        ctx.restore();
        // the pile fades down as the brain act approaches (brain.js lifts the photos out)
      }
      leak(ctx, t);
    },
  });

  function glow(g, p, b, k, t) {
    g.save();
    g.translate(p.x, p.y); g.rotate(p.a);
    const pul = 0.75 + 0.25 * Math.sin(t * 9 + b.j);
    g.shadowColor = `rgba(132,169,255,${(0.9 * k * pul).toFixed(3)})`;
    g.shadowBlur = 40;
    g.fillStyle = `rgba(132,169,255,${(0.6 * k).toFixed(3)})`;
    g.fillRect(-b.w / 2 - 3, -b.h / 2 - 3, b.w + 6, b.h + 6);
    g.restore();
  }

  function floor(g, cam, t) {
    // a faint horizon of light where the pile rests
    const half = W / cam.zoom;
    const gr = g.createLinearGradient(0, -40 / cam.zoom, 0, 120 / cam.zoom);
    gr.addColorStop(0, "rgba(132,169,255,0)"); gr.addColorStop(0.35, "rgba(132,169,255,.10)"); gr.addColorStop(1, "rgba(10,12,20,0)");
    g.fillStyle = gr; g.fillRect(cam.cx - half, -40 / cam.zoom, half * 2, 160 / cam.zoom);
  }

  function dust(ctx, t) {
    const v = 1 - seg(t, 9, 11.5);
    if (v <= 0) return;
    const fin = seg(t, 0, 1.2);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (let i = 0; i < 140; i++) {
      const d = 0.3 + 0.7 * hash(i * 5 + 1);
      const x = (hash(i * 5 + 2) * W + t * 12 * d + 40 * noise(t * 0.3 + i, i)) % W;
      const y = (hash(i * 5 + 3) * H - t * 9 * d + 30 * noise(t * 0.25 + i * 2, i + 9) + H) % H;
      const tw = 0.55 + 0.45 * Math.sin(t * (1 + 2 * hash(i)) + i);
      const r = 1 + 2.4 * d;
      const a = 0.35 * d * tw * v * fin;
      const gr = ctx.createRadialGradient(x, y, 0, x, y, r * 4);
      gr.addColorStop(0, `rgba(255,236,205,${a.toFixed(3)})`); gr.addColorStop(1, "rgba(255,236,205,0)");
      ctx.fillStyle = gr; ctx.fillRect(x - r * 4, y - r * 4, r * 8, r * 8);
    }
    ctx.restore();
  }

  function drawRain(ctx, t) {
    if (t < RAIN_T0 || t > TL.ACT.ask[0] + 2) return;
    const st = TL.simTime(t);
    ctx.save();
    for (const r of rain) {
      if (t < r.t0) continue;
      const dt = st - TL.simTime(r.t0);
      const y = -120 + dt * (900 + 1600 * r.d) + 0.5 * 900 * dt * dt * r.d;
      if (y > H + 150) continue;
      const x = r.x + 30 * noise(dt * 0.8, r.x);
      const fade = 1 - seg(t, TL.HIT.turn, TL.HIT.turn + 1.2);
      drawPhoto(ctx, r.src, x, y, r.w, r.h, r.a0 + r.spin * dt, { alpha: (0.25 + 0.6 * r.d) * fade, shadow: 0.25, res: 1 });
    }
    // depth haze over the far rain
    ctx.restore();
  }

  // blue light leaking from below during the slow motion: something is about to change
  function leak(ctx, t) {
    const k = seg(t, TL.SLOW.t0, TL.HIT.turn) * (1 - seg(t, TL.HIT.turn, TL.HIT.turn + 0.6));
    const flash = pulse(t, TL.HIT.turn, 0.18);
    if (k <= 0 && flash <= 0.01) return;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    const gr = ctx.createRadialGradient(W / 2, H + 200, 0, W / 2, H + 200, 900 + 500 * k);
    gr.addColorStop(0, `rgba(110,150,255,${(0.55 * k).toFixed(3)})`); gr.addColorStop(1, "rgba(110,150,255,0)");
    ctx.fillStyle = gr; ctx.fillRect(0, 0, W, H);
    if (flash > 0.01) { ctx.fillStyle = `rgba(190,210,255,${(0.55 * flash).toFixed(3)})`; ctx.fillRect(0, 0, W, H); }
    ctx.restore();
  }
})();
