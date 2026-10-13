"""Find scenic photos and video moments in the local Glimpse index for the launch video.

Uses the app's own model: every photo / video clip is scored against scenic themes, and against "people" and
"text/screen" prompts so portraits, screenshots and documents are left out. Writes video/private/candidates.json
and contact sheets (video/private/sheet_<theme>.jpg) to choose from. Nothing leaves the PC; private/ is gitignored.

  .venv\Scripts\python.exe video\pick_media.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from glimpse import config, embedder  # noqa: E402
from glimpse.sources.images import thumb_name  # noqa: E402

OUT = os.path.join(ROOT, "video", "private")
THEMES = {
    "sunset": "a golden sunset over the sea",
    "mountains": "snowy mountains and a lake, landscape photo",
    "beach": "a tropical beach with turquoise water",
    "city": "a city skyline at night with lights",
    "forest": "a green forest path with sunlight",
    "sky": "a dramatic sky with clouds",
    "snow": "snow covered landscape in winter",
    "flowers": "colorful flowers in a garden",
    "waterfall": "a waterfall in nature",
    "road": "an open road trip through beautiful scenery",
    "food": "a beautiful plate of food at a restaurant",
    "fireworks": "fireworks in the night sky",
    "stars": "the night sky full of stars",
    "travel": "a famous landmark while travelling",
}
AVOID = {"people": ["a photo of a person", "a selfie", "a group of people posing", "a face close up",
                    "people at a party", "a child"],
         "text": ["a screenshot of a computer screen", "a document with text", "a receipt or bill",
                  "code in an editor", "a presentation slide", "a chat conversation on a phone"]}


def load(db, source):
    rows = db.execute("SELECT id, path, meta, vec, scale FROM items WHERE source=?", (source,)).fetchall()
    ids, paths, metas, vecs = [], [], [], []
    for i, p, m, v, s in rows:
        x = np.frombuffer(v, np.int8).astype(np.float32) * (s or 1.0) if v else None
        if x is None or x.shape[0] != config.get("embedding_dim"):
            continue
        ids.append(i); paths.append(p); metas.append(json.loads(m or "{}")); vecs.append(x)
    V = np.stack(vecs)
    return ids, paths, metas, V / np.linalg.norm(V, axis=1, keepdims=True)


def main():
    db = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    emb = embedder.get()
    q = lambda s: (lambda v: v / np.linalg.norm(v))(emb.query(s))
    out = {}
    for source in ("images", "video"):
        ids, paths, metas, V = load(db, source)
        avoid = {k: V @ np.stack([q(s) for s in v]).T for k, v in AVOID.items()}
        bad = (avoid["people"].max(1) > np.percentile(avoid["people"].max(1), 60)) | \
              (avoid["text"].max(1) > np.percentile(avoid["text"].max(1), 50))
        for theme, prompt in THEMES.items():
            s = V @ q(prompt)
            s[bad] = -1
            order = np.argsort(-s)
            seen, pick = set(), []
            for j in order:
                key = paths[j] if source == "images" else (paths[j], int(metas[j].get("t", 0)) // 30)
                if s[j] < 0 or key in seen or not os.path.exists(paths[j]):
                    continue
                seen.add(key)
                pick.append({"id": ids[j], "path": paths[j], "score": round(float(s[j]), 3), "meta": metas[j]})
                if len(pick) >= 12:
                    break
            out.setdefault(theme, {})[source] = pick
            if source == "images":
                sheet(theme, pick)
        print(source, len(ids), "items scored")
    json.dump(out, open(os.path.join(OUT, "candidates.json"), "w", encoding="utf-8"), indent=1)
    # a wall of "memories": many scenic, people-free, text-free thumbnails
    ids, paths, metas, V = load(db, "images")
    avoid_p = V @ np.stack([q(s) for s in AVOID["people"]]).T
    avoid_t = V @ np.stack([q(s) for s in AVOID["text"]]).T
    nice = V @ np.stack([q(p) for p in THEMES.values()]).T
    score = nice.max(1) - 0.6 * avoid_p.max(1) - 0.8 * avoid_t.max(1)
    wall = [paths[j] for j in np.argsort(-score)[:1400]]
    json.dump(wall, open(os.path.join(OUT, "wall.json"), "w", encoding="utf-8"))
    print("wall", len(wall))


def sheet(theme, pick):
    tiles = []
    for k, c in enumerate(pick):
        t = os.path.join(config.THUMB_DIR, thumb_name(c["path"]))
        try:
            im = Image.open(t if os.path.exists(t) else c["path"]).convert("RGB")
        except Exception:
            continue
        im.thumbnail((320, 320))
        tile = Image.new("RGB", (330, 350), (20, 20, 24))
        tile.paste(im, ((330 - im.width) // 2, 5))
        ImageDraw.Draw(tile).text((8, 330), f"{theme}#{k}  {c['score']}", fill=(220, 220, 220))
        tiles.append(tile)
    if not tiles:
        return
    W = Image.new("RGB", (330 * 6, 350 * ((len(tiles) + 5) // 6)), (0, 0, 0))
    for k, t in enumerate(tiles):
        W.paste(t, ((k % 6) * 330, (k // 6) * 350))
    W.save(os.path.join(OUT, f"sheet_{theme}.jpg"), quality=85)


if __name__ == "__main__":
    main()
