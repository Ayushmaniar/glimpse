# Writing a chapter of the launch film

These are the rules. Read `STORYBOARD.md` (the film), `timeline.js` (the clock) and `core.js` (the engine API, documented at its top) before writing anything. `ch/pile.js` is a complete, working example chapter.

## Contract

- **One file per chapter.** Edit only your own `story/ch/<name>.js`. The shared files are off-limits: timeline.js, core.js, index.html, render.js, sim.js, pile.js and the other chapters. If you need something from them, say so in your report.
- **Structure.** The file is an IIFE that calls `CH.add({ name, z, init?, draw(t, ctx) })`.
  - `init()` runs once after all media has loaded. Build your DOM there with `ui(html, style)`.
  - `draw(t, ctx)` is called on **every** frame. Outside your time range, hide your DOM (`el.style.display = "none"`) and return immediately.
- **Every frame is a pure function of `t`.**
  - No state carried between frames: frames are rendered out of order, by several parallel workers.
  - No `Math.random()`, `Date` or `performance.now()`. Use `hash(i)` and `noise(t, seed)`.
- **The canvas `ctx` is 1920×1080 in screen pixels** with an identity transform. Save and restore around transforms. The background is already painted.
- **Text and UI.** DOM is best for crisp text and glass UI. Use `textFx(el, t, tIn, tOut)` for the house text animation (rise and unblur).
  - Fonts: `var(--display)` (Segoe UI Variable Display) for headlines, `var(--font)` for UI, `var(--mono)` for code and URLs.
- **Media.**
  - `img(src)` returns a decoded image; `sprite(src, w, h)` returns it as a photo print.
  - `drawPhoto(ctx, src, x, y, w, h, angle, {alpha, shadow, res})` draws a print centred on (x, y).
  - `src` values come from `MEDIA` (manifest) and `SIM.bodies[i].img`.
  - Hero images: `MEDIA.queries[k].images[j].hero` (1920 px). Cards: `.card`.
  - Video frames: `${MEDIA.queries[k].video.dir}/${String(f).padStart(3,"0")}.jpg`, with f from 1 to `.frames`, at 30 fps.
- **Physics poses.** `pose(i, t)` gives the pile pose in world px. Use `camera(t)` and `toScreen(cam, x, y)` for screen px; multiply sizes by `cam.zoom`.
  - Search result bodies have `role: "result"`, `q` (search index), `j` (rank), `pickV` (time pulled out) and `pickPose` [x, y, angle] (world px at that moment).
- **Beats.** Put every important change on a beat: `TL.b(n)`, `TL.bar(n)`, `TL.QUERIES[k].enter`, `TL.arrive(k, j)` and so on.
  - The music is generated from the same timeline and hits exactly those times, so never invent your own times for things the music accents.
  - In-between motion is free.

## Craft

- **Something always moves.** Cameras drift, glows breathe, elements overshoot (`ease.backOut`) and settle.
  - Use anticipation before hits, squash on impact and follow-through after.
- **Style.** Premium and calm, like an Apple or Google keynote film. Generous spacing, one focal point per moment, no clutter. Every shot should read in under a second.
- **Palette.** Night blue-black; accents in sky `#84a9ff`, blue `#3466f6` and violet `#8b5cf6`; text `#eef0f6`, muted `#9aa3b5`. Gradient words use the `grad` class.
- **Performance.** Keep render time under 700 ms per frame: the sheet command prints ms per frame. Pre-render expensive static things in `init()` to offscreen canvases.

## Review loop (do it, several times)

```
cd video
node story/render.js sheet 16.8 17.4 18.0 19.2 19.8 21.0      # contact sheet -> out/story/sheet_16.8-21.jpg (path is printed)
node story/render.js stills 19.2                              # full-size PNG -> out/story/still_19.2.png
```

1. Open the sheet with the Read tool and look closely.
2. Check the first and last frame of your range, frames around each hit (every 0.1 s), and the handoff to the chapters before and after yours.
3. Fix overlaps, clipping, unreadable text, empty dead frames and stiff motion. Iterate until each moment is beautiful.
4. Page errors are printed as `page error:`. Fix every one of them.
