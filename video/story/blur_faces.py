"""Blur faces in the film's media, after prep.py and before sim.js.

    .venv\\Scripts\\python.exe video\\story\\blur_faces.py            apply the blurs
    .venv\\Scripts\\python.exe video\\story\\blur_faces.py --mark      print signatures of the photos in story.json's
                                                                      "blur_faces" queries, to fill the file below

The face boxes live in video/private/faces.json (never committed): a list of
{"sig": [256 floats], "boxes": [[x0, y0, x1, y1], ...]} with boxes as fractions of the photo. A photo is found by
its signature (a 16 x 16 grey thumbnail), so the same blur reaches every copy of it: the result card, the full-screen
hero and the pile, whatever prep.py named them this time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
MEDIA = HERE.parent / "private" / "media"
FACES = HERE.parent / "private" / "faces.json"
MATCH = 0.95


def sig(im: Image.Image) -> np.ndarray:
    a = np.asarray(im.convert("L").resize((16, 16), Image.BILINEAR), np.float32).ravel()
    a -= a.mean()
    return a / (np.linalg.norm(a) + 1e-6)


def blur(im: Image.Image, boxes) -> Image.Image:
    W, H = im.size
    out = im.copy()
    for x0, y0, x1, y1 in boxes:
        # pad the box a little and feather it, so the blur has no hard edge
        w, h = (x1 - x0) * W, (y1 - y0) * H
        l, t = int(x0 * W - 0.25 * w), int(y0 * H - 0.25 * h)
        r, b = int(x1 * W + 0.25 * w), int(y1 * H + 0.25 * h)
        l, t, r, b = max(0, l), max(0, t), min(W, r), min(H, b)
        if r - l < 2 or b - t < 2:
            continue
        region = im.crop((l, t, r, b))
        radius = max(3.0, 0.35 * max(r - l, b - t))
        soft = region.filter(ImageFilter.GaussianBlur(radius)).filter(ImageFilter.GaussianBlur(radius))
        mask = Image.new("L", region.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, region.size[0] - 1, region.size[1] - 1), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(max(1.0, 0.08 * min(region.size))))
        out.paste(soft, (l, t), mask)
    return out


def main() -> None:
    if "--mark" in sys.argv:
        story = json.loads((HERE / "story.json").read_text(encoding="utf-8"))
        manifest = json.loads((MEDIA / "manifest.json").read_text(encoding="utf-8"))
        for spec, q in zip(story["queries"], manifest["queries"]):
            if spec.get("blur_faces"):
                for im in q["images"]:
                    print(im["hero"], json.dumps([round(float(v), 4) for v in sig(Image.open(MEDIA / im["hero"]))]))
        return
    entries = json.loads(FACES.read_text(encoding="utf-8")) if FACES.exists() else []
    if not entries:
        print("no faces.json: nothing to blur")
        return
    S = np.stack([np.asarray(e["sig"], np.float32) for e in entries])
    done = set()
    for p in sorted(MEDIA.rglob("*.jpg")):
        if p.parent.name == "v":          # video frames
            continue
        im = Image.open(p).convert("RGB")
        s = S @ sig(im)
        j = int(np.argmax(s))
        if s[j] < MATCH:
            continue
        blur(im, entries[j]["boxes"]).save(p, quality=90)
        done.add(j)
        print(f"blurred {p.relative_to(MEDIA)} ({len(entries[j]['boxes'])} faces, match {s[j]:.3f})")
    missing = [i for i in range(len(entries)) if i not in done]
    if missing:
        print(f"note: {len(missing)} marked photos are not in the media this time")


if __name__ == "__main__":
    main()
