// Renders scenes.html frame by frame with Microsoft Edge (Playwright) and pipes the frames to ffmpeg.
//
//   node render.js stills 3 7.5 12.8        PNG stills at those times (out/still_<t>.png) for review
//   node render.js video                    out/silent.mp4 (1920x1080, 30 fps)
//   node render.js gif 11 19                out/preview.gif of that time range (README preview)
//
// Then `python music.py` writes out/music.wav and `ffmpeg` muxes them (see build.ps1).
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { chromium } = require("playwright-core");

const HERE = __dirname;
const OUT = path.join(HERE, "out");
const TL = JSON.parse(fs.readFileSync(path.join(HERE, "timeline.json"), "utf8"));
const EDGE = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
].find((p) => fs.existsSync(p));

function built() {
  const src = fs.readFileSync(path.join(HERE, "scenes.html"), "utf8");
  const out = path.join(HERE, "_built.html");  // next to scenes.html so ../smartsearch/assets resolves
  fs.writeFileSync(out, src.replace("/*TIMELINE*/null", JSON.stringify(TL)));
  return "file:///" + out.replace(/\\/g, "/");
}

async function page() {
  const browser = await chromium.launch({ executablePath: EDGE, headless: true, args: ["--force-color-profile=srgb", "--hide-scrollbars"] });
  const pg = await browser.newPage({ viewport: { width: TL.width, height: TL.height }, deviceScaleFactor: 1 });
  pg.on("console", (m) => { if (m.type() === "error") console.error("page:", m.text()); });
  pg.on("pageerror", (e) => console.error("page error:", e.message));
  await pg.goto(built());
  await pg.evaluate(() => document.fonts.ready);
  return { browser, pg };
}

async function shot(pg, t) {
  await pg.evaluate((tt) => window.frame(tt), t);
  return pg.screenshot({ type: "png", clip: { x: 0, y: 0, width: TL.width, height: TL.height } });
}

function ffmpeg(args) {
  const p = spawn("ffmpeg", args, { stdio: ["pipe", "inherit", "inherit"] });
  return p;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const [mode, ...rest] = process.argv.slice(2);
  const { browser, pg } = await page();
  try {
    if (mode === "stills") {
      for (const s of rest) {
        const buf = await shot(pg, parseFloat(s));
        fs.writeFileSync(path.join(OUT, `still_${s}.png`), buf);
        console.log("still", s);
      }
    } else if (mode === "video" || mode === "gif") {
      const fps = mode === "gif" ? 15 : TL.fps;
      const t0 = mode === "gif" ? parseFloat(rest[0]) : 0;
      const t1 = mode === "gif" ? parseFloat(rest[1]) : TL.duration;
      const n = Math.round((t1 - t0) * fps);
      const target = mode === "gif" ? path.join(OUT, "frames.mp4") : path.join(OUT, "silent.mp4");
      const ff = ffmpeg(["-y", "-v", "error", "-f", "image2pipe", "-framerate", String(fps), "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-tune", "animation", target]);
      const started = Date.now();
      for (let i = 0; i < n; i++) {
        const buf = await shot(pg, t0 + i / fps);
        if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once("drain", r));
        if (i % 60 === 0) console.log(`frame ${i}/${n}  ${((Date.now() - started) / 1000).toFixed(0)} s`);
      }
      ff.stdin.end();
      await new Promise((r) => ff.on("close", r));
      if (mode === "gif") {
        // two-pass palette GIF, 960 px wide
        const pal = path.join(OUT, "palette.png");
        await new Promise((r) => spawn("ffmpeg", ["-y", "-v", "error", "-i", target, "-vf", "scale=960:-1:flags=lanczos,palettegen=stats_mode=diff", pal], { stdio: "inherit" }).on("close", r));
        await new Promise((r) => spawn("ffmpeg", ["-y", "-v", "error", "-i", target, "-i", pal, "-lavfi",
          "scale=960:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", path.join(OUT, "preview.gif")], { stdio: "inherit" }).on("close", r));
      }
      console.log("done", target);
    } else {
      console.log("usage: node render.js stills <t...> | video | gif <t0> <t1>");
    }
  } finally {
    await browser.close();
  }
})();
