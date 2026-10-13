"""Capture real search results from the running app for the launch video.

    .venv\\Scripts\\python.exe video\\capture.py "paneer" "birthday cake" ...

For each query this saves the response, the thumbnails and a large preview of the first photo and
video results to video/private/q/<slug>/, plus a contact sheet (sheet.jpg) to choose from.
Everything lands in video/private/, which is gitignored: these are your own files.
"""
from __future__ import annotations

import io
import json
import re
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "private" / "q"
sys.path.insert(0, str(ROOT.parent))
from eval.run_cutoff_eval import server  # noqa: E402

PORT, TOKEN = server()


def get(path: str, body: dict | None = None) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data,
                                 {"X-Token": TOKEN, "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=180).read()


def slug(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")


def capture(q: str, large: int = 8) -> dict:
    d = json.loads(get("/api/search", {"q": q}))
    folder = OUT / slug(q)
    folder.mkdir(parents=True, exist_ok=True)
    tiles = []
    for s in d["sections"]:
        for i, r in enumerate(s["results"]):
            r["_files"] = {}
            if r.get("thumb"):
                try:
                    p = folder / f"{s['source']}_{i}_thumb.jpg"
                    p.write_bytes(get(r["thumb"] if r["thumb"].startswith("/") else f"/api/thumb/{r['thumb']}"))
                    r["_files"]["thumb"] = p.name
                    tiles.append((f"{s['source'][:3]}{i}", p))
                except Exception as e:  # noqa: BLE001
                    r["_files"]["thumb_error"] = str(e)[:80]
            if s["source"] in ("images", "video", "scans", "files") and i < large:
                try:
                    p = folder / f"{s['source']}_{i}_large.jpg"
                    p.write_bytes(get(f"/api/preview/{r['id']}?size=large"))
                    r["_files"]["large"] = p.name
                except Exception as e:  # noqa: BLE001
                    r["_files"]["large_error"] = str(e)[:80]
    (folder / "response.json").write_text(json.dumps(d, indent=1), encoding="utf-8")
    sheet(tiles, folder / "sheet.jpg")
    counts = {s["source"]: len(s["results"]) for s in d["sections"]}
    return {"q": q, "ms": d["ms"], "counts": counts}


def sheet(tiles, path: Path, w: int = 220) -> None:
    if not tiles:
        return
    cols = 8
    rows = (len(tiles) + cols - 1) // cols
    img = Image.new("RGB", (cols * w, rows * (w + 18)), (20, 22, 27))
    dr = ImageDraw.Draw(img)
    for k, (label, p) in enumerate(tiles):
        try:
            t = Image.open(p).convert("RGB")
        except Exception:  # noqa: BLE001
            continue
        t.thumbnail((w - 6, w - 6))
        x, y = (k % cols) * w, (k // cols) * (w + 18)
        img.paste(t, (x + (w - t.width) // 2, y + (w - t.height) // 2))
        dr.text((x + 4, y + w + 2), label, fill=(200, 200, 200))
    img.save(path, quality=85)


if __name__ == "__main__":
    for q in sys.argv[1:]:
        print(json.dumps(capture(q)))
