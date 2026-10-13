// Act 1 on-screen text (memories), the counter strip, the big question before the turn, and the Act 2
// keycap turn: Alt + Space slam down on TL.HIT.turn (shockwave, bloom), then morph into the launcher's search bar.
(function () {
  const HIT = TL.HIT.turn;                 // 14.4 s
  const FALL0 = HIT - 0.35;                // keycaps start falling
  const MORPH0 = HIT + 0.9, MORPH1 = 16.5; // keycaps morph into the search bar
  const END = 16.9;
  const CX = W / 2, CY = 540;              // the hit, screen px

  let l1, l2, strip, l3, kc, keys, labels, plus, counters;

  const TXT = "font-family:var(--display);font-weight:650;letter-spacing:-0.03em;color:#eef0f6;" +
              "text-shadow:0 4px 40px rgba(0,0,0,.6);white-space:nowrap;text-align:center;pointer-events:none;";

  const CSS = `
    .intro-k { position:absolute; top:0; height:190px; box-sizing:border-box; border-radius:34px;
      background:linear-gradient(#2a2f3c,#1b1f29); border:1px solid rgba(255,255,255,.12);
      display:flex; align-items:center; justify-content:center; will-change:transform;
      font-family:var(--display); font-weight:600; font-size:60px; color:#eef0f6; }
    .intro-plus { position:absolute; left:230px; top:0; width:60px; height:190px; line-height:190px; text-align:center;
      font-family:var(--display); font-weight:500; font-size:84px; color:#9aa3b5; }
    .intro-pill { position:absolute; left:50%; top:950px; transform:translate(-50%,-50%); display:flex; align-items:stretch;
      padding:22px 0; border-radius:22px; background:rgba(10,12,18,.72); backdrop-filter:blur(12px);
      -webkit-backdrop-filter:blur(12px); border:1px solid rgba(255,255,255,.08); box-shadow:0 20px 60px rgba(0,0,0,.35); }
    .intro-cell { padding:0 46px; text-align:center; min-width:250px; }
    .intro-div { width:1px; background:rgba(255,255,255,.12); margin:6px 0; }
    .intro-num { font-family:var(--display); font-weight:700; font-size:64px; line-height:1; color:#ffffff;
      font-variant-numeric:tabular-nums; letter-spacing:-0.02em; }
    .intro-lab { font-family:var(--font); font-size:26px; color:#9aa3b5; margin-top:10px; white-space:nowrap; }
  `;

  // rest geometry of the keycap group (screen px, before the hover offset)
  const REST = { x: 540, y: 445, w: 840, h: 190, r: 34 };
  const BAR = { x: 362, y: 118, w: 1196, h: 92, r: 18 };   // LAUNCHER.bar

  const COUNTS = [
    { n: () => MEDIA.counts.images, label: "photos", start: TL.bar(4) },
    { n: () => MEDIA.counts.video, label: "video moments", start: TL.bar(4) + TL.b(1) },
    { n: () => MEDIA.counts.files, label: "documents", start: TL.bar(4) + TL.b(2) },
  ];
  const fmt = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

  // press amount of the keycaps: 1 = pressed down, dips below 0 as the spring overshoots back up
  function press(t) {
    if (t < HIT) return 0;
    if (t < HIT + 0.06) return 1;
    return 1 - ease.elasticOut(seg(t, HIT + 0.06, HIT + 0.32));
  }
  // morph of the keycap group into the search bar: 0 -> 1 with a tiny overshoot at the end
  const morph = (t) => kf(t, [[MORPH0, 0], [16.25, 1.02], [MORPH1, 1]], ease.inOut);

  // line 3: rises, held through the slow motion (drifts up, gains a blue glow), blasted away at the hit
  function line3(t) {
    const kin = ease.out(seg(t, 12.0, 12.4));
    const kout = ease.in(seg(t, HIT - 0.05, HIT + 0.1));
    const v = kin * (1 - kout);
    if (v <= 0.001) { l3.style.display = "none"; return; }
    l3.style.display = "";
    const drift = -14 * ease.inOut(seg(t, TL.SLOW.t0, HIT));
    const glow = seg(t, TL.SLOW.t0, HIT - 0.05);
    l3.style.opacity = v.toFixed(3);
    l3.style.transform = `translateY(${((1 - kin) * 24 + drift).toFixed(1)}px) scale(${(1 + 0.6 * kout).toFixed(3)})`;
    l3.style.filter = `blur(${((1 - kin) * 10 + kout * 26).toFixed(1)}px)`;
    l3.style.textShadow = `0 4px 40px rgba(0,0,0,.6), 0 0 ${(30 + 30 * glow).toFixed(0)}px rgba(132,169,255,${(0.55 * glow).toFixed(3)})`;
  }

  function counterStrip(t) {
    textFx(strip, t, TL.bar(4), 13.0);
    COUNTS.forEach((c, i) => {
      const k = ease.expoOut((t - c.start) / 1.6);
      counters[i].textContent = fmt(Math.round(c.n() * k));
    });
  }

  function keycaps(t) {
    if (t < FALL0 || t >= END) { kc.style.display = "none"; return; }
    kc.style.display = "";
    const m = morph(t), mc = clamp(m);
    const pa = press(t);
    const fall = seg(t, FALL0, HIT);
    const sFall = t < HIT ? lerp(1.15, 1, ease.in(fall)) : 1;
    const yFall = t < HIT ? lerp(-120, -10, ease.in(fall)) : -10;
    const bob = seg(t, 14.75, 15.0) * Math.sin((t - 14.75) * 3.2) * 1.6;
    const ty = (t < HIT ? yFall : -10 + bob) * (1 - mc);

    kc.style.opacity = (ease.out(seg(t, FALL0, FALL0 + 0.07)) * (1 - seg(t, MORPH1, MORPH1 + 0.3))).toFixed(3);
    kc.style.transform = `translateY(${ty.toFixed(2)}px) scale(${(sFall * (1 + 0.03 * pa)).toFixed(4)}, ${(sFall * (1 - 0.06 * pa)).toFixed(4)})`;
    kc.style.left = lerp(REST.x, BAR.x, mc).toFixed(1) + "px";
    kc.style.top = lerp(REST.y, BAR.y, mc).toFixed(1) + "px";
    kc.style.width = lerp(REST.w, BAR.w, mc).toFixed(1) + "px";
    kc.style.height = lerp(REST.h, BAR.h, mc).toFixed(1) + "px";
    kc.style.borderRadius = lerp(REST.r, BAR.r, mc).toFixed(1) + "px";
    kc.style.background = `rgba(29,33,41,${mc.toFixed(3)})`;
    kc.style.boxShadow = mc <= 0.001 ? "none" :
      `inset 0 0 0 1.5px rgba(132,169,255,${(0.55 * mc).toFixed(3)}), 0 0 0 ${(5 * mc).toFixed(2)}px rgba(132,169,255,${(0.12 * mc).toFixed(3)})`;

    const keyOp = (1 - ease.inOut(seg(mc, 0.2, 0.75))).toFixed(3);
    keys.forEach((el) => {
      el.style.transform = `translateY(${(12 * pa).toFixed(2)}px) scale(${(1 - 0.25 * mc).toFixed(3)})`;
      el.style.opacity = keyOp;
      const off = 14 - 12 * pa;
      el.style.boxShadow = `0 ${off.toFixed(2)}px 0 #0c0e14, 0 ${(off + 18).toFixed(1)}px 36px rgba(0,0,0,.55), inset 0 2px 0 rgba(255,255,255,.12)`;
    });
    plus.style.opacity = keyOp;
    const lblOp = (1 - seg(mc, 0, 0.4)).toFixed(3);
    labels.forEach((el) => { el.style.opacity = lblOp; });
  }

  // canvas: bloom behind the keys, shockwave rings, and a light flash, all centred on the keys
  function ringFx(ctx, t, t0, dur, r1, w0, w1, a0) {
    const u = seg(t, t0, t0 + dur);
    if (u <= 0 || u >= 1) return;
    ctx.beginPath();
    ctx.arc(CX, CY, r1 * ease.out(u), 0, Math.PI * 2);
    ctx.lineWidth = lerp(w0, w1, u);
    ctx.strokeStyle = `rgba(132,169,255,${(a0 * (1 - u)).toFixed(3)})`;
    ctx.stroke();
  }
  function fx(t, ctx) {
    const pre = 0.28 * ease.in(seg(t, FALL0, HIT)) * (1 - seg(t, HIT, HIT + 0.08));
    const pk = pulse(t, HIT, 0.3) * 0.3;
    const a = (pre + pk) * (1 - seg(t, 15.0, 15.6));
    if (a > 0.003) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      const g = ctx.createRadialGradient(CX, CY, 0, CX, CY, 820);
      g.addColorStop(0, `rgba(132,169,255,${a.toFixed(3)})`);
      g.addColorStop(0.45, `rgba(132,169,255,${(a * 0.4).toFixed(3)})`);
      g.addColorStop(1, "rgba(132,169,255,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      ctx.restore();
    }
    if (t >= HIT - 0.01 && t < HIT + 1.6) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      ringFx(ctx, t, HIT, 0.7, 1400, 30, 2, 0.8);
      ringFx(ctx, t, HIT + 0.03, 0.45, 1800, 8, 1, 0.6);
      const fl = pulse(t, HIT, 0.1) * 0.1;
      if (fl > 0.003) { ctx.fillStyle = `rgba(200,215,255,${fl.toFixed(3)})`; ctx.fillRect(0, 0, W, H); }
      ctx.restore();
    }
  }

  CH.add({
    name: "intro", z: 10,
    init() {
      const st = document.createElement("style");
      st.textContent = CSS; document.head.appendChild(st);

      l1 = ui("Every moment you’ve ever saved…", TXT + "left:0;top:300px;width:1920px;font-size:84px;line-height:1.1;");
      l2 = ui("…is somewhere <span class=\"grad\" style=\"text-shadow:none;filter:drop-shadow(0 4px 30px rgba(0,0,0,.5));\">on your PC</span>.",
              TXT + "left:0;top:300px;width:1920px;font-size:84px;line-height:1.1;");
      strip = ui("", "left:0;top:0;width:1920px;height:0;");
      strip.innerHTML =
        `<div class="intro-pill">` +
        COUNTS.map((c, i) => (i ? `<div class="intro-div"></div>` : "") +
          `<div class="intro-cell"><div class="intro-num">0</div><div class="intro-lab">${c.label}</div></div>`).join("") +
        `</div>`;
      counters = [...strip.querySelectorAll(".intro-num")];

      l3 = ui("So how do you find<br>the one you want?", TXT + "left:0;top:394px;width:1920px;font-size:104px;line-height:1.12;");

      kc = ui("", `left:${REST.x}px;top:${REST.y}px;width:${REST.w}px;height:${REST.h}px;box-sizing:border-box;`);
      kc.innerHTML =
        `<div class="intro-k" style="left:0;width:200px;"><span>Alt</span></div>` +
        `<div class="intro-plus">+</div>` +
        `<div class="intro-k" style="left:320px;width:520px;"><span>Space</span></div>`;
      keys = [...kc.querySelectorAll(".intro-k")];
      labels = [...kc.querySelectorAll(".intro-k span")];
      plus = kc.querySelector(".intro-plus");
    },
    draw(t, ctx) {
      textFx(l1, t, TL.b(1), TL.bar(1) + TL.b(2));
      textFx(l2, t, TL.bar(3), TL.bar(4) + TL.b(1));
      counterStrip(t);
      line3(t);
      keycaps(t);
      fx(t, ctx);
    },
  });
})();
