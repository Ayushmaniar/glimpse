// Physics for the launch video, computed once, ahead of rendering (planck.js, a Box2D port).
//
//   node story/sim.js
//
// Photos rain into a pile (the first 32 land exactly on beats), jump on the Alt+Space hit, and the search
// results are pulled out of the pile when each search runs. Writes to out/story/:
//   sim.bin          Float32 [frame][body][x, y, angle] at SIM_HZ of *sim* time (x, y in px; NaN = not in the world)
//   sim.json         bodies (image, size, spawn, role) + layout, read by the page
//   collisions.json  every impact (video time, impulse, position, size), read by score.py
//   events.json      timeline.events(): keystrokes, Enter, card arrivals, hits, read by score.py
//   timeline.json    acts, hits and search times, read by score.py
const fs = require("fs");
const path = require("path");
const pl = require("planck");
const TL = require("./timeline.js");

const HERE = __dirname;
const OUT = path.join(HERE, "..", "out", "story");
const MEDIA = path.join(HERE, "..", "private", "media");
const manifest = JSON.parse(fs.readFileSync(path.join(MEDIA, "manifest.json"), "utf8"));

const PX = 100;                 // px per metre
const G = 30;                   // m/s^2: snappier than real paper, reads as weighty cards
const STEP = 1 / 120;           // physics step (sim seconds)
const SIM_HZ = 60;              // recorded poses per sim second
const HALF_W = 2700;            // basin half-width (px), just outside the widest shot
const T_FALL = 0.45;            // beat-timed drops take this long from spawn to first touch

// deterministic "random"
const hash = (i) => { let x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

// camera during act 1 (the page uses the same function): floor sits near the bottom of the frame
function cam(t) {
  const k1 = smooth((t - TL.bar(3)) / TL.BAR), k2 = smooth((t - TL.bar(4)) / (TL.BAR * 1.2));
  const zoom = 1.0 * Math.pow(0.62, k1) * Math.pow(0.36 / 0.62, k2);
  return { cx: 0, cy: -(1010 - TL.H / 2) / zoom, zoom };
}
function smooth(x) { x = Math.min(1, Math.max(0, x)); return x * x * (3 - 2 * x); }
function view(t) {   // visible world rect (px) at video time t
  const c = cam(t);
  return { x0: c.cx - TL.W / 2 / c.zoom, x1: c.cx + TL.W / 2 / c.zoom, y0: c.cy - TL.H / 2 / c.zoom, y1: c.cy + TL.H / 2 / c.zoom };
}

// ---- who falls, when ----------------------------------------------------------------------------
const bodies = [];   // {img, w, h, role, spawnV (video t), targetV?, q?, j?, pickV?}
const pile = manifest.pile;
let pi = 0;
const nextPile = (pred) => {
  for (let tries = 0; tries < pile.length; tries++) {
    const p = pile[pi++ % pile.length];
    if (!pred || pred(p)) return p;
  }
  return pile[pi++ % pile.length];
};
const fit = (p, long) => {
  const s = long / Math.max(p.w, p.h);
  return { w: Math.round(p.w * s), h: Math.round(p.h * s) };
};
const SIZE = { big: 440, medium: 300, small: 210 };
TL.DROPS.forEach((d, i) => {
  const p = nextPile(d.size === "big" ? (q) => q.w >= q.h * 0.7 : null);
  const v = view(d.t);
  // big ones are composed by hand so the first bars read like a still life; the rest scatter
  const BIG_X = [-80, 470, -560, 190, -330, 680, -720, 360];
  const span = d.size === "medium" ? 0.75 : 0.85;
  const x = d.size === "big" ? BIG_X[i] : (hash(i + 1) - 0.5) * 2 * span * (v.x1 - v.x0) / 2;
  bodies.push({ img: p.src, ...fit(p, SIZE[d.size]), role: "drop", size: d.size, targetV: d.t, x });
});
// flood: rate ramps from 8 to 160 cards per beat
{
  let t = TL.FLOOD.t0, i = 0;
  const span = TL.FLOOD.t1 - TL.FLOOD.t0;
  while (t < TL.FLOOD.t1) {
    const rate = 8 * Math.pow(160 / 8, (t - TL.FLOOD.t0) / span);
    const p = nextPile();
    const v = view(t);
    const x = v.x0 + (0.03 + 0.94 * hash(1000 + i)) * (v.x1 - v.x0);
    bodies.push({ img: p.src, ...fit(p, 120 + 70 * hash(2000 + i)), role: "flood", spawnV: t, x, y: v.y0 - 150 - 500 * hash(3000 + i) });
    t += TL.BEAT / rate; i++;
  }
}
// search results: they fall late in the flood, near the middle, so they sit near the top of the pile
TL.QUERIES.forEach((q, k) => {
  const imgs = manifest.queries[k].images.slice(0, TL.MAX_RESULTS);
  imgs.forEach((im, j) => {
    const n = k * TL.MAX_RESULTS + j;
    const t = TL.FLOOD.t1 - 1.1 + 1.0 * hash(4000 + n);
    const v = view(t);
    const x = (hash(5000 + n) - 0.5) * 0.7 * (v.x1 - v.x0);
    bodies.push({ img: im.card, ...fit(im, 175), role: "result", q: k, j, spawnV: t, x, y: v.y0 - 200 - 400 * hash(6000 + n),
                  pickV: q.enter });
  });
});

// ---- world --------------------------------------------------------------------------------------
const world = new pl.World({ gravity: pl.Vec2(0, G) });
const ground = world.createBody();
ground.createFixture(pl.Edge(pl.Vec2(-HALF_W / PX, 0), pl.Vec2(HALF_W / PX, 0)), { friction: 0.7 });
ground.createFixture(pl.Edge(pl.Vec2(-HALF_W / PX, 0), pl.Vec2(-HALF_W / PX, -60)), { friction: 0.3 });
ground.createFixture(pl.Edge(pl.Vec2(HALF_W / PX, 0), pl.Vec2(HALF_W / PX, -60)), { friction: 0.3 });
ground.setUserData(-1);

function surfaceY(xpx) {   // top of the pile (px, y down) under x
  let best = 0;
  world.rayCast(pl.Vec2(xpx / PX, -80), pl.Vec2(xpx / PX, 0.01), (fixture, point, normal, fraction) => {
    best = point.y * PX; return fraction;
  });
  return best;
}

// spawn schedule in sim time
bodies.forEach((bd, i) => {
  bd.i = i;
  if (bd.role === "drop") bd.spawnS = TL.simTime(bd.targetV) - T_FALL;
  else bd.spawnS = TL.simTime(bd.spawnV);
});
const order = bodies.map((b) => b.i).sort((a, c) => bodies[a].spawnS - bodies[c].spawnS);
const live = new Array(bodies.length).fill(null);

function spawn(bd) {
  const angle = (hash(7000 + bd.i) - 0.5) * (bd.role === "drop" ? 0.35 : 1.2);
  let y, vy = 0;
  // lowest point of the (rotated) card: that is what touches first
  const hh = (bd.w * Math.abs(Math.sin(angle)) + bd.h * Math.abs(Math.cos(angle))) / 2;
  if (bd.role === "drop") {
    // land at targetV: fall T_FALL from spawn; start above the frame, with a push if the frame is far above
    const surf = surfaceY(bd.x);
    const free = 0.5 * G * PX * T_FALL * T_FALL;
    const top = view(bd.targetV - T_FALL).y0 - Math.max(bd.w, bd.h);
    y = surf - hh - free;
    if (y > top) { y = top; vy = ((surf - hh - top) - free) / T_FALL; }
  } else y = bd.y;
  const body = world.createDynamicBody({
    position: pl.Vec2(bd.x / PX, y / PX), angle,
    linearVelocity: pl.Vec2(0, vy / PX), angularVelocity: bd.role === "drop" ? 0 : (hash(8000 + bd.i) - 0.5) * 1.5,
    bullet: false,
  });
  body.createFixture(pl.Box(bd.w / 2 / PX, bd.h / 2 / PX), { density: 1, friction: 0.55, restitution: 0.08 });
  body.setUserData(bd.i);
  live[bd.i] = body;
  bd.spawnedS = world_t;
}

// ---- collisions ------------------------------------------------------------------------------------
const fresh = new Set();
const touched = new Set();
const hits = [];
world.on("begin-contact", (c) => fresh.add(c));
world.on("post-solve", (c, imp) => {
  if (!fresh.has(c)) return;
  fresh.delete(c);
  const a = c.getFixtureA().getBody().getUserData(), b = c.getFixtureB().getBody().getUserData();
  const j = Math.max(...imp.normalImpulses.slice(0, c.getManifold().pointCount));
  if (!(j > 0.02)) return;
  const wm = c.getWorldManifold(null);
  const p = wm && wm.points[0] ? wm.points[0] : (a >= 0 ? live[a] : live[b]).getPosition();
  const first = [a, b].filter((x) => x >= 0 && !touched.has(x));
  first.forEach((x) => touched.add(x));
  const size = Math.max(...[a, b].filter((x) => x >= 0).map((x) => Math.max(bodies[x].w, bodies[x].h)));
  hits.push({ t: +TL.videoTime(world_t).toFixed(4), imp: +j.toFixed(3), x: Math.round(p.x * PX), y: Math.round(p.y * PX),
              size, a, b, first: first.length ? first : undefined,
              beat: first.length && first.some((x) => bodies[x].role === "drop") ? bodies[first.find((x) => bodies[x].role === "drop")].targetV : undefined });
});

// ---- run ---------------------------------------------------------------------------------------------
const END_V = TL.ACT.brain[0];
const END_S = TL.simTime(END_V);
const nFrames = Math.ceil(END_S * SIM_HZ) + 1;
const N = bodies.length;
const poses = new Float32Array(nFrames * N * 3).fill(NaN);
let world_t = 0, next = 0, frame = 0;
const kickS = TL.simTime(TL.HIT.turn);
let kicked = false;
const picks = TL.QUERIES.map((q) => ({ k: q.k, s: TL.simTime(q.enter), done: false }));
const steps = Math.ceil(END_S / STEP);
const t0 = Date.now();
for (let s = 0; s <= steps; s++) {
  world_t = s * STEP;
  while (next < order.length && bodies[order[next]].spawnS <= world_t + 1e-9) spawn(bodies[order[next++]]);
  if (!kicked && world_t >= kickS) {
    kicked = true;
    live.forEach((b, i) => {
      if (!b) return;
      const m = b.getMass();
      b.setAwake(true);
      b.applyLinearImpulse(pl.Vec2((hash(9000 + i) - 0.5) * 5 * m, -(5 + 9 * hash(9100 + i)) * m), b.getWorldCenter());
      b.applyAngularImpulse((hash(9200 + i) - 0.5) * 6 * b.getInertia());
    });
  }
  picks.forEach((p) => {
    if (p.done || world_t < p.s) return;
    p.done = true;
    bodies.forEach((bd) => {
      if (bd.role !== "result" || bd.q !== p.k || !live[bd.i]) return;
      const b = live[bd.i];
      bd.pickPose = [b.getPosition().x * PX, b.getPosition().y * PX, b.getAngle()];
      world.destroyBody(b);
      live[bd.i] = null;
      // the pile shifts into the gap
      world.getBodyList && (() => { for (let o = world.getBodyList(); o; o = o.getNext()) o.setAwake(true); })();
    });
  });
  // record at SIM_HZ
  while (frame < nFrames && frame / SIM_HZ <= world_t + 1e-9) {
    const base = frame * N * 3;
    for (let i = 0; i < N; i++) {
      const b = live[i];
      if (!b) continue;
      const p = b.getPosition();
      poses[base + i * 3] = p.x * PX; poses[base + i * 3 + 1] = p.y * PX; poses[base + i * 3 + 2] = b.getAngle();
    }
    frame++;
  }
  world.step(STEP, 10, 4);
}
// pile top after the turn (where the camera should frame it in act 3) and at the end
function pileTop(f) {
  const ys = [];
  for (let i = 0; i < N; i++) { const y = poses[f * N * 3 + i * 3 + 1]; if (!Number.isNaN(y)) ys.push(y - bodies[i].h / 2); }
  ys.sort((a, b) => a - b);
  return ys.length ? ys[Math.floor(ys.length * 0.04)] : 0;   // ignore a few outliers on top
}
fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(path.join(OUT, "sim.bin"), Buffer.from(poses.buffer));
fs.writeFileSync(path.join(OUT, "sim.json"), JSON.stringify({
  simHz: SIM_HZ, frames: nFrames, n: N, px: PX, halfW: HALF_W,
  topAfterTurn: pileTop(Math.min(nFrames - 1, Math.round(TL.simTime(TL.ACT.ask[0]) * SIM_HZ))),
  topAtEnd: pileTop(nFrames - 1),
  bodies: bodies.map((b) => ({ img: b.img, w: b.w, h: b.h, role: b.role, size: b.size, q: b.q, j: b.j,
                                spawnV: +TL.videoTime(b.spawnS).toFixed(4), targetV: b.targetV, pickV: b.pickV, pickPose: b.pickPose })),
}));
fs.writeFileSync(path.join(OUT, "collisions.json"), JSON.stringify(hits));
fs.writeFileSync(path.join(OUT, "events.json"), JSON.stringify(TL.events()));
// the clock itself, for score.py: section times are read from here, never hardcoded
fs.writeFileSync(path.join(OUT, "timeline.json"), JSON.stringify({
  bpm: TL.BPM, beat: TL.BEAT, bar: TL.BAR, duration: TL.DURATION, acts: TL.ACT, hits: TL.HIT, slow: TL.SLOW,
  flood: TL.FLOOD, brain: TL.BRAIN, finale: TL.FINALE,
  queries: TL.QUERIES.map((q) => ({ k: q.k, q: q.q, kind: q.kind, voice: !!q.voice, start: q.start, end: q.end, enter: q.enter, hero: q.hero })),
}, null, 1));
const firsts = hits.filter((h) => h.beat !== undefined).map((h) => +(h.t - h.beat).toFixed(3));
console.log(`bodies ${N}, frames ${nFrames}, hits ${hits.length}, ${((Date.now() - t0) / 1000).toFixed(1)} s`);
console.log("drop landing error vs beat (s):", firsts.slice(0, 32).join(" "));
