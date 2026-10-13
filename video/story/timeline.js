// The launch video's clock. Shared by the page (browser), the physics precompute (sim.js, Node) and the
// music (score.py reads the events this file dumps), so picture, physics and sound agree to the sample.
//
// 100 BPM, 4/4: a beat is 0.6 s, a bar is 2.4 s. Everything important lands on a beat.
(function (root) {
  const BPM = 100, BEAT = 60 / BPM, BAR = 4 * BEAT;
  const W = 1920, H = 1080, FPS = 30;
  const b = (n) => n * BEAT;          // time of beat n
  const bar = (n) => n * BAR;         // time of bar n
  const DURATION = bar(33);           // 79.2 s

  // ---- acts (seconds) --------------------------------------------------------------------------
  const ACT = {
    memories: [0, bar(6)],            // photos rain into a pile, faster and faster
    turn: [bar(6), bar(7)],           // Alt+Space hit, the launcher appears over the pile
    ask: [bar(7), bar(25)],           // nine real search slots, 2 bars each: 4 photo/video, 3 text, 1 rapid-fire, 1 spoken
    brain: [bar(25), bar(28)],        // EmbeddingGemma 2: one meaning space, on your PC, private
    finale: [bar(28), DURATION],      // explosion, logo, tagline, install
  };
  const HIT = { turn: bar(6), finale: bar(28) };

  // ---- act 1: the drops that land on beats ------------------------------------------------------
  // [time the card first touches the pile, size class]. 8 big on quarter notes, 8 medium on 8ths, 16 small on 16ths.
  const DROPS = [];
  for (let i = 0; i < 8; i++) DROPS.push({ t: bar(1) + i * BEAT, size: "big" });
  for (let i = 0; i < 8; i++) DROPS.push({ t: bar(3) + i * BEAT / 2, size: "medium" });
  for (let i = 0; i < 16; i++) DROPS.push({ t: bar(4) + i * BEAT / 4, size: "small" });
  // then a flood with no per-card timing: rate ramps from 8 to 90 cards per beat
  const FLOOD = { t0: bar(4) + b(2), t1: bar(5) + b(3.5) };

  // slow motion right before the turn: video seconds in [SLOW.t0, SLOW.t1) advance physics by SLOW.rate
  const SLOW = { t0: bar(5) + b(1.5), t1: HIT.turn, rate: 0.22 };
  function simTime(t) {
    if (t <= SLOW.t0) return t;
    if (t <= SLOW.t1) return SLOW.t0 + (t - SLOW.t0) * SLOW.rate;
    return SLOW.t0 + (SLOW.t1 - SLOW.t0) * SLOW.rate + (t - SLOW.t1);
  }
  function videoTime(s) {
    const s0 = SLOW.t0, s1 = simTime(SLOW.t1);
    if (s <= s0) return s;
    if (s <= s1) return s0 + (s - s0) / SLOW.rate;
    return SLOW.t1 + (s - s1);
  }

  // ---- act 3: searches ---------------------------------------------------------------------------
  // Each search is 2 bars (8 beats) from its start s:
  //   beats 0-2.5  type the query (keystrokes on 16ths, 32nds or faster if it is long) / or speak it
  //   beat 2.5     Enter (on the "and" of 3): the matching photos fly out of the pile into the grid (arrivals on 32nds)
  //   beat 4       the best result opens full screen with a caption, held until the next search starts (2.4 s)
  // A "rapid" slot is four quick searches of 2 beats each: type on the "and", Enter on the beat, the top rows stay
  // up while the next one is typed. It has no hero; its caption runs across the whole slot.
  const QUERY_TEXT = [
    { q: "paneer", caption: ["Type a word.", "Every photo of it, instantly."] },
    { q: "birthday", caption: ["Even moments inside videos.", "It opens the clip right where it happens."] },
    { q: "glowing blue waves at night", caption: ["Describe it like you would to a friend.", "It understands what's in the picture."] },
    { q: "mountains and a lake", caption: ["No tags. No albums. No filenames.", "It just knows what you mean."] },
    { q: "what is a bayesian network", kind: "text", caption: ["Documents, by meaning.", "The exact page. The exact sentence."] },
    { q: "code that finds duplicate photos", kind: "text", caption: ["Code, by what it does.", "Even when you forgot what you named it."] },
    { q: "my screen is too dim", kind: "text", caption: ["Not just your files. Your whole PC.", "Say what's wrong. Fix it right there."] },
    { q: "make text bigger", kind: "rapid", subs: ["make text bigger", "eyes hurt at night", "no sound from speakers", "uninstall an app"],
      caption: ["Settings. Apps. Fixes.", "One search box for all of Windows."] },
    { q: "sunset at the beach", voice: true, caption: ["Or just say it.", "Voice search runs on your PC too."] },
  ];
  const QUERIES = QUERY_TEXT.map((spec, k) => {
    const s = bar(7 + 2 * k);
    const n = spec.q.length;
    const step = [BEAT / 4, BEAT / 8, BEAT / 12, BEAT / 16].find((st) => n * st <= b(2.2)) || BEAT / 16;
    const keys = [];
    if (!spec.voice) for (let i = 0; i < n; i++) keys.push(s + b(0.25) + i * step);
    const words = [];
    if (spec.voice) spec.q.split(" ").forEach((w, i) => words.push({ w, t: s + b(1.5) + i * BEAT / 4 }));
    if (spec.kind === "rapid") {
      const subs = spec.subs.map((sq, i) => {
        const s0 = s + i * b(2);
        const st = Math.min(BEAT / 8, b(0.75) / sq.length);
        return { q: sq, i, start: s0, keys: [...sq].map((_, c) => s0 + b(0.2) + c * st), enter: s0 + b(1) };
      });
      return { ...spec, k, start: s, end: s + bar(2), keys: subs.flatMap((x) => x.keys), words: [], listen: null, subs,
               enter: subs[0].enter, hero: s + bar(2) };
    }
    return { kind: "photo", ...spec, k, start: s, end: s + bar(2), keys, words,
             listen: spec.voice ? [s, s + b(1.5)] : null,
             enter: s + b(2.5), hero: s + b(4) };
  });
  // results fly in on 32nds after Enter: arrival time of result j of query k (or of quick search i of a rapid slot)
  const arrive = (k, j, i) => (i != null ? QUERIES[k].subs[i].enter + b(0.125) + j * BEAT / 8
                                         : QUERIES[k].enter + b(0.5) + j * BEAT / 8);
  const MAX_RESULTS = 8;

  // ---- act 4 / 5 beats ------------------------------------------------------------------------------
  const B0 = ACT.brain[0], F0 = ACT.finale[0];
  const BRAIN = {
    gather: B0,                 // photos lift off the pile into a 3D "meaning space"
    model: B0 + b(2),           // "Powered by EmbeddingGemma 2"
    space: B0 + bar(1),         // text, photos, video, speech: one shared space; a query vector finds neighbours
    local: B0 + bar(2),         // "Runs on your PC. Nothing is uploaded."
    implode: B0 + bar(2) + b(2),// everything is pulled to the centre (riser)
  };
  const FINALE = {
    boom: HIT.finale,           // explosion, logo
    title: F0 + b(2),           // wordmark
    tagline: F0 + bar(1),       // "Find the moment, not the file."
    install: F0 + bar(2),       // free, open source, Windows; Alt+Space
    url: F0 + bar(3),           // github URL
    fade: F0 + bar(4),          // fade to black by DURATION
  };

  // ---- sound events the music must hit exactly (score.py reads these) ---------------------------
  function events() {
    const ev = [];
    const add = (t, type, extra) => ev.push({ t: +t.toFixed(4), type, ...(extra || {}) });
    QUERIES.forEach((q) => {
      if (q.subs) {
        q.subs.forEach((sb) => {
          sb.keys.forEach((t, i) => add(t, "key", { i, fast: true }));
          add(sb.enter, "enter", { k: q.k, sub: sb.i });
          for (let j = 0; j < 3; j++) add(arrive(q.k, j, sb.i), "row", { k: q.k, j: j + sb.i });
        });
        return;
      }
      q.keys.forEach((t, i) => add(t, "key", { i }));
      if (q.listen) add(q.listen[0], "mic_on");
      q.words.forEach((w) => add(w.t, "word"));
      add(q.enter, "enter", { k: q.k });
      const n = q.kind === "text" ? 4 : MAX_RESULTS;
      for (let j = 0; j < n; j++) add(arrive(q.k, j), q.kind === "text" ? "row" : "card", { k: q.k, j });
      add(q.hero, "hero", { k: q.k });
      add(q.hero - b(0.5), "whoosh");
    });
    add(HIT.turn, "hit", { big: true });
    add(HIT.turn - b(0.5), "whoosh");
    add(HIT.finale, "hit", { big: true });
    Object.entries(BRAIN).forEach(([k, t]) => add(t, "brain_" + k));
    Object.entries(FINALE).forEach(([k, t]) => add(t, "finale_" + k));
    add(SLOW.t0, "slowmo_in");
    return ev.sort((a, c) => a.t - c.t);
  }

  const TL = { BPM, BEAT, BAR, W, H, FPS, DURATION, ACT, HIT, DROPS, FLOOD, SLOW, QUERIES, MAX_RESULTS, BRAIN, FINALE,
               b, bar, simTime, videoTime, arrive, events };
  if (typeof module !== "undefined") module.exports = TL; else root.TL = TL;
})(this);
