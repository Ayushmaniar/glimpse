# The launch video, as code

`docs/launch.mp4` is rendered from these files, with no editor and no stock assets:

| File | What it is |
|---|---|
| `timeline.json` | Scene times, the six demo queries, transition and impact times. Picture and sound both read it |
| `scenes.html` | Every frame as a pure function of time, `frame(t)`: motion graphics, the launcher replica, illustrations |
| `render.js` | Drives Microsoft Edge (Playwright) frame by frame and pipes PNGs into ffmpeg |
| `music.py` | The soundtrack, synthesised from scratch: pads, bass, arpeggios, drums, risers, UI sounds, reverb |

Build it (Windows, Node 18+, ffmpeg on PATH, [uv](https://docs.astral.sh/uv/)):

```powershell
cd video
npm install
powershell -ExecutionPolicy Bypass -File build.ps1
```

`node render.js stills 7.5 13.5` writes single frames to `out/` for quick checks; open `scenes.html?t=13.5`
through any local web server to scrub it in a browser.
