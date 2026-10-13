// Renders story/index.html frame by frame in headless Microsoft Edge and encodes with ffmpeg.
//
//   node story/render.js stills 3 7.5 12.8           out/story/still_<t>.png
//   node story/render.js sheet 14.0 14.4 14.8 ...    out/story/sheet_<first>-<last>.jpg (contact sheet, 3 columns, 640 px tiles)
//   node story/render.js video [t0 t1] [--workers 6] out/story/silent.mp4 (parallel workers, then joined)
//   add --draft to video for a quick half-resolution (960x540) pass
//   node story/render.js mux [draft]                 out/story/launch.mp4 = silent.mp4 + music.wav (draft: launch_draft.mp4)
//
// Needs: node_modules/playwright-core, ffmpeg on PATH, out/story/sim.* from sim.js, private/media from prep.py.
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { chromium } = require("playwright-core");
const TL = require("./timeline.js");

const HERE = __dirname;
const OUT = path.join(HERE, "..", "out", "story");
const EDGE = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe"]
  .find((p) => fs.existsSync(p));
const URL = "file:///" + path.join(HERE, "index.html").replace(/\\/g, "/");

const DRAFT = process.argv.includes("--draft");
async function open() {
  const browser = await chromium.launch({
    executablePath: EDGE, headless: true,
    args: ["--allow-file-access-from-files", "--force-color-profile=srgb", "--hide-scrollbars", "--disable-gpu-vsync",
           "--enable-gpu-rasterization", "--ignore-gpu-blocklist"],
  });
  const pg = await browser.newPage({ viewport: { width: TL.W, height: TL.H }, deviceScaleFactor: DRAFT ? 0.5 : 1 });
  pg.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") console.error("page:", m.text()); });
  pg.on("pageerror", (e) => console.error("page error:", e.message));
  await pg.goto(URL);
  await pg.evaluate(() => window.ready);
  return { browser, pg };
}
async function shot(pg, t, type = "png") {
  await pg.evaluate((tt) => window.frame(tt), t);
  return pg.screenshot({ type, quality: type === "jpeg" ? 92 : undefined, clip: { x: 0, y: 0, width: TL.W, height: TL.H } });
}
const run = (args) => new Promise((res, rej) => {
  const p = spawn("ffmpeg", args, { stdio: ["ignore", "inherit", "inherit"] });
  p.on("close", (c) => (c === 0 ? res() : rej(new Error("ffmpeg " + c))));
});

async function renderRange(f0, f1, file, label) {
  const { browser, pg } = await open();
  const ff = spawn("ffmpeg", ["-y", "-v", "error", "-f", "image2pipe", "-framerate", String(TL.FPS), "-i", "-",
    "-c:v", "libx264", "-preset", DRAFT ? "veryfast" : "medium", "-crf", DRAFT ? "22" : "15", "-pix_fmt", "yuv420p", "-g", "60", file], { stdio: ["pipe", "inherit", "inherit"] });
  const started = Date.now();
  for (let f = f0; f < f1; f++) {
    const buf = await shot(pg, f / TL.FPS);
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once("drain", r));
    if ((f - f0) % 90 === 0) console.log(`${label} frame ${f - f0}/${f1 - f0}  ${((Date.now() - started) / 1000).toFixed(0)} s`);
  }
  ff.stdin.end();
  await new Promise((r) => ff.on("close", r));
  await browser.close();
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const argv = process.argv.slice(2).filter((a) => a !== "--draft");
  const mode = argv.shift();
  const wi = argv.indexOf("--workers");
  const workers = wi >= 0 ? parseInt(argv.splice(wi, 2)[1], 10) : 6;
  const nums = argv.map(parseFloat).filter((x) => !Number.isNaN(x));

  if (mode === "stills" || mode === "sheet") {
    const { browser, pg } = await open();
    const files = [];
    for (const [k, t] of nums.entries()) {
      const t0 = Date.now();
      const buf = await shot(pg, t);
      const f = mode === "stills" ? path.join(OUT, `still_${t}.png`) : path.join(OUT, `_sheet${process.pid}_${String(k).padStart(3, "0")}.png`);
      fs.writeFileSync(f, buf); files.push(f);
      console.log(`t=${t}  ${Date.now() - t0} ms`);
    }
    await browser.close();
    if (mode === "sheet") {
      const sheetFile = path.join(OUT, `sheet_${nums[0]}-${nums[nums.length - 1]}.jpg`);
      const cols = 3, rows = Math.ceil(files.length / cols);
      await run(["-y", "-v", "error", "-i", path.join(OUT, `_sheet${process.pid}_%03d.png`), "-vf",
        `scale=640:-1,pad=648:368:4:4:color=0x111111,tile=${cols}x${rows}`, "-frames:v", "1", "-q:v", "3", sheetFile]);
      files.forEach((f) => fs.unlinkSync(f));
      console.log("sheet written to", sheetFile);
    }
  } else if (mode === "video") {
    const t0 = nums[0] ?? 0, t1 = nums[1] ?? TL.DURATION;
    const F0 = Math.round(t0 * TL.FPS), F1 = Math.round(t1 * TL.FPS);
    const per = Math.ceil((F1 - F0) / workers);
    const parts = [];
    const jobs = [];
    for (let w = 0; w < workers; w++) {
      const a = F0 + w * per, b = Math.min(F1, a + per);
      if (a >= b) break;
      const file = path.join(OUT, `_part${w}.mp4`);
      parts.push(file);
      jobs.push(renderRange(a, b, file, `w${w}`));
    }
    const started = Date.now();
    await Promise.all(jobs);
    fs.writeFileSync(path.join(OUT, "_parts.txt"), parts.map((p) => `file '${p.replace(/\\/g, "/")}'`).join("\n"));
    await run(["-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", path.join(OUT, "_parts.txt"), "-c", "copy", path.join(OUT, DRAFT ? "draft.mp4" : "silent.mp4")]);
    parts.forEach((p) => fs.unlinkSync(p));
    console.log(`done silent.mp4 in ${((Date.now() - started) / 1000).toFixed(0)} s`);
  } else if (mode === "mux") {
    const silent = path.join(OUT, argv.includes("draft") ? "draft.mp4" : "silent.mp4");
    await run(["-y", "-v", "error", "-i", silent, "-i", path.join(OUT, "music.wav"),
      "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-af", `afade=t=out:st=${TL.DURATION - 1.2}:d=1.2`,
      "-t", String(TL.DURATION), "-movflags", "+faststart", path.join(OUT, argv.includes("draft") ? "launch_draft.mp4" : "launch.mp4")]);
    console.log("done", path.join(OUT, "launch.mp4"));
  } else {
    console.log("usage: node story/render.js stills <t...> | sheet <t...> | video [t0 t1] [--workers N] | mux");
  }
})();
