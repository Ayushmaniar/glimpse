"""Photos and screenshots, searchable by what is in them (including text inside them).

Indexing speed on an RTX 4070 laptop (benchmarks in README):
  full-resolution file -> model          ~310 ms/image
  pre-resized to 768 px JPEG -> model    ~160 ms/image, cosine 0.994 to full-res vectors
Decoding/resizing runs in a thread pool so it overlaps with embedding. The 256 px
thumbnail for the UI comes out of the same decode.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PIL import Image, ImageOps

from .. import config
from .base import Result, Source, pretty_dir
from . import walk as walk_mod
from .walk import walk

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MODEL_PX = 768
THUMB_PX = 256
MIN_BYTES = 8_000    # icons are filtered by MIN_SIDE; real screenshots can be ~10-30 KB
MIN_SIDE = 200

Image.MAX_IMAGE_PIXELS = 200_000_000

# Numbered frame dumps (frame_0001.png, f_12.jpg, step_3.png, 000123.png): datasets, renders, game
# captures. On the dev laptop they were 67% of all images. Every Nth frame keeps the sequence findable
# at a fraction of the cost. Camera names (IMG_, DSC_, PXL_, Screenshot ...) never match.
FRAME_NAME = re.compile(r"(?i)^(?:frames?|f|step|s|t|ep|episode|render|out|output|sample|obs|rgb|image|shot)?"
                        r"[_\-. ]?\d+(?:[_\-.]\d+)*$")
MIN_SEQUENCE = 50


def frame_skips(files: list[tuple[str, float, int]], every: int) -> set[str]:
    """Paths to leave out: all but every Nth image of each folder's numbered frame sequence."""
    if every <= 1:
        return set()
    seqs: dict[str, list[str]] = defaultdict(list)
    for p, _, _ in files:
        if FRAME_NAME.match(os.path.splitext(os.path.basename(p))[0]):
            seqs[os.path.dirname(p)].append(p)
    skip = set()
    for paths in seqs.values():
        if len(paths) < MIN_SEQUENCE:
            continue
        paths.sort(key=lambda p: [int(n) for n in re.findall(r"\d+", os.path.basename(p))])
        skip.update(p for i, p in enumerate(paths) if i % every and i != len(paths) - 1)
    return skip


def thumb_name(path: str, suffix: str = "") -> str:
    return hashlib.sha1((os.path.normcase(path) + suffix).encode()).hexdigest()[:20] + ".jpg"


def prepare(path: str, thumb: str) -> tuple[bytes, dict] | None:
    """Decode once, return (model-ready JPEG bytes, meta) and write the thumbnail."""
    im = Image.open(path)
    w, h = im.size
    if min(w, h) < MIN_SIDE:
        return None
    taken = None
    try:
        exif = im.getexif()
        raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)  # DateTimeOriginal / DateTime
        if raw:
            taken = datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S").date().isoformat()
    except Exception:
        pass
    if im.format == "JPEG":
        im.draft("RGB", (MODEL_PX, MODEL_PX))  # DCT downscale while decoding: much faster for big photos
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((MODEL_PX, MODEL_PX))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    t = im.copy()
    t.thumbnail((THUMB_PX, THUMB_PX))
    t.save(os.path.join(config.THUMB_DIR, thumb), "JPEG", quality=82)
    return buf.getvalue(), {"w": w, "h": h, "taken": taken, "thumb": thumb}


class ImagesSource(Source):
    name = "images"
    label = "Photos & screenshots"
    panel = "grid"
    exts, min_size = EXTS, MIN_BYTES
    intent = ("A photo, picture, screenshot, scanned page, meme or diagram on my computer, described by the scene, "
              "people, objects or text in it.")
    supports_image_query = True
    floor, strong = 0.68, 0.79    # unrelated photos: 0.60-0.66
    image_floor, image_strong = 0.72, 0.90
    # Real index (10.5k photos): nonsense p50 0.706, p90 0.739; real queries 0.722-0.789.
    floor_ref_n, floor_per_decade = 40, 0.027
    keep_within = 0.12  # grids are browsed, show a few more

    def index(self, emb, progress) -> None:
        os.makedirs(config.THUMB_DIR, exist_ok=True)
        known = self.store.file_state(self.name)
        files = list(walk(EXTS, MIN_BYTES))
        present = {p for p, _, _ in files}
        if walk_mod.RECENT_SINCE is None:
            ok = walk_mod.prunable()
            for p in set(known) - present:
                if ok(p):
                    self.store.remove_path(self.name, p, commit=False)
        skip = frame_skips(files, config.get("frame_sample_every"))
        status = self.store.file_status(self.name) if skip else {}
        for p, m, s in files:
            if p in skip and status.get(p) != "sampled":
                self.store.remove_path(self.name, p, commit=False)  # drop frames indexed before sampling
                self.store.mark_file(self.name, p, m, s, "sampled")
        todo = [(p, m, s) for p, m, s in files if p not in skip and known.get(p) != (m, s)]
        todo.sort(key=lambda x: -x[1])
        if not todo:
            self.store.commit()
            return
        import time
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [(p, m, s, pool.submit(prepare, p, thumb_name(p))) for p, m, s in todo[:8]]
            nxt = 8
            for i in range(len(todo)):
                p, m, s, fut = futs[i]
                if nxt < len(todo):  # keep the decode pipeline 8 images ahead
                    q = todo[nxt]
                    futs.append((q[0], q[1], q[2], pool.submit(prepare, q[0], thumb_name(q[0]))))
                    nxt += 1
                walk_mod.gate(progress)
                if i % 10 == 0:
                    rate = i / max(time.time() - t0, 1e-6)
                    eta = f" · ~{(len(todo) - i) / rate / 60:.0f} min left" if i > 30 else ""
                    progress(f"{i:,}/{len(todo):,} images{eta}", i, len(todo))
                with walk_mod.claim(self.name, p) as got:
                    if got and self.store.file_row(self.name, p) != (m, s):  # else the folder watcher has it
                        self.store.remove_path(self.name, p, commit=False)
                        try:
                            status = self._add(emb, p, fut.result())
                        except Exception as e:
                            status = f"error: {e.__class__.__name__}"
                        self.store.mark_file(self.name, p, m, s, status)
                futs[i] = None  # free memory
                if i % 50 == 49:
                    self.store.commit()
        self.store.commit()

    def handler(self, emb):
        """handle(path) -> status for one new image (the folder watcher). Frames of an image sequence are
        left to the periodic scan, which samples them."""
        os.makedirs(config.THUMB_DIR, exist_ok=True)

        def handle(path: str) -> str:
            if FRAME_NAME.match(os.path.splitext(os.path.basename(path))[0]):
                return "deferred"
            return self._add(emb, path, prepare(path, thumb_name(path)))
        return handle

    def _add(self, emb, p: str, prepared) -> str:
        if prepared is None:
            return "skipped"
        data, meta = prepared
        v = emb.image(data)
        taken = meta.get("taken")  # EXIF date beats file time (copies, downloads, backups)
        ts = datetime.fromisoformat(taken).timestamp() + 12 * 3600 if taken else None
        self.store.add(self.name, f"images:{p}", v, path=p, title=os.path.basename(p),
                       subtitle=os.path.dirname(p), meta=meta, commit=False, ts=ts)
        return "ok"

    def to_result(self, it, score) -> Result:
        m = it.meta
        return Result(id=f"images:{it.id}", source=self.name, title=it.title, subtitle=pretty_dir(it.subtitle), score=score,
                      thumb=f"/api/thumb/{m.get('thumb')}" if m.get("thumb") else None, icon="🖼️",
                      badge=m.get("taken"), kind="photo",
                      meta={k: m[k] for k in ("w", "h", "taken") if m.get(k)}, actions=[{"label": "Show in folder", "op": "reveal"}])

    def search(self, emb, qvec, text, k=8) -> list[Result]:
        return [self.to_result(it, s) for it, s in self.store.search(self.name, qvec, max(k, 12))]
