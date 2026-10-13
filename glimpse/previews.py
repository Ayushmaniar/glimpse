"""Thumbnails made on demand when a result is shown, so they cost nothing at index time.

  PDF text hits   the matching page, rendered with PDFium (~30-80 ms, then cached)
  audio           a waveform of the whole recording; the UI marks the matching moment on it

Large previews (Quick Look, Shift+Space) are rendered only when opened and kept in a small RAM cache,
never on disk: any PDF page, a photo at 1280 px, the video frame at the matching moment.
Text context returns the matching chunk with the lines or chunks around it.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import threading
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageDraw

from . import config
from .sources import media
from .sources.images import thumb_name
from .store import Store

PAGE_PX = 320
LARGE_PAGE_PX, LARGE_PHOTO_PX, LARGE_FRAME_PX = 1400, 1280, 960
CONTEXT_LINES, MAX_CONTEXT = 14, 8000
WAVE_W, WAVE_H, BARS = 320, 64, 96
WAVE_RGB = (154, 160, 166)  # mid grey reads on both light and dark backgrounds


def preview(store: Store, item_id: int) -> tuple[bytes, str] | None:
    """(image bytes, content type) for an item, or None if it has no preview."""
    it = store.get(item_id)
    if it is None or not it.path or not os.path.isfile(it.path):
        return None
    stamp = f"{os.path.getmtime(it.path):.0f}"
    if it.source == "files" and it.path.lower().endswith(".pdf"):
        m = re.fullmatch(r"p\. (\d+)", it.meta.get("loc", ""))
        if not m:
            return None
        name = thumb_name(it.path, f"#pv{m.group(1)}@{stamp}")
        return _cached(name, "image/jpeg", lambda: _pdf_page(it.path, int(m.group(1))))
    if it.source == "audio":
        name = thumb_name(it.path, f"#wave@{stamp}")[:-4] + ".png"
        return _cached(name, "image/png", lambda: _waveform(it.path))
    return None


def _cached(name: str, ctype: str, make) -> tuple[bytes, str] | None:
    full = os.path.join(config.THUMB_DIR, name)
    if not os.path.isfile(full):
        data = make()
        if data is None:
            return None
        os.makedirs(config.THUMB_DIR, exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
    with open(full, "rb") as f:
        return f.read(), ctype


def _pdf_page(path: str, page: int) -> bytes | None:
    from .sources.pdfpages import render_page
    img, _ = render_page(path, page, PAGE_PX)
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _waveform(path: str) -> bytes | None:
    # 2 kHz mono is plenty for an envelope; cap at 3 h so a huge file can't stall the UI.
    r = subprocess.run([media.ffmpeg(), "-v", "error", "-t", "10800", "-i", path, "-ac", "1", "-ar", "2000",
                        "-f", "s16le", "pipe:1"], capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32)
    if len(x) < BARS:
        return None
    env = np.array([np.sqrt(np.mean(c ** 2)) for c in np.array_split(x, BARS)])
    env = env / (np.percentile(env, 98) or 1.0)
    img = Image.new("RGBA", (WAVE_W, WAVE_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    step = WAVE_W / BARS
    for i, v in enumerate(np.clip(env, 0.04, 1.0)):
        h = v * (WAVE_H - 4)
        x0 = i * step + 0.5
        d.rounded_rectangle([x0, (WAVE_H - h) / 2, x0 + step * 0.6, (WAVE_H + h) / 2], radius=1, fill=WAVE_RGB + (255,))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---- Quick Look ------------------------------------------------------------------------

_lru: OrderedDict = OrderedDict()
_lru_lock = threading.Lock()
LRU_ITEMS = 48  # ~150 KB each


def large(store: Store, item_id: int, page: int | None = None) -> tuple[bytes, dict] | None:
    """(JPEG bytes, extra headers) for the Quick Look view, or None if the item has no image preview."""
    it = store.get(item_id)
    if it is None or not it.path or not os.path.isfile(it.path):
        return None
    pdf = it.path.lower().endswith(".pdf") and it.source in ("files", "scans")
    if pdf and page is None:
        m = re.fullmatch(r"p\. (\d+)", it.meta.get("loc", ""))
        page = it.meta.get("page") or (int(m.group(1)) if m else 1)
    key = (it.path, os.path.getmtime(it.path), page if pdf else None, it.meta.get("t"))
    with _lru_lock:
        if key in _lru:
            _lru.move_to_end(key)
            return _lru[key]
    if pdf:
        from .sources.pdfpages import render_page
        img, n = render_page(it.path, page, LARGE_PAGE_PX)
        out = (_jpeg(img, 82), {"X-Page": str(page), "X-Pages": str(n)}) if img is not None else None
    elif it.source == "images":
        from PIL import ImageOps
        im = Image.open(it.path)
        im.draft("RGB", (LARGE_PHOTO_PX, LARGE_PHOTO_PX))  # JPEG: decode at reduced size, much faster
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((LARGE_PHOTO_PX, LARGE_PHOTO_PX))
        out = (_jpeg(im, 85), {})
    elif it.source == "video":
        r = subprocess.run([media.ffmpeg(), "-v", "error", "-ss", f"{float(it.meta.get('t', 0)) + 1:.2f}", "-i", it.path,
                            "-frames:v", "1", "-vf", f"scale={LARGE_FRAME_PX}:-2", "-f", "image2pipe",
                            "-vcodec", "mjpeg", "-q:v", "4", "pipe:1"], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout, {}) if r.stdout[:2] == b"\xff\xd8" else None  # JPEG magic
    else:
        return None
    if out is not None:
        with _lru_lock:
            _lru[key] = out
            while len(_lru) > LRU_ITEMS:
                _lru.popitem(last=False)
    return out


def _jpeg(img: Image.Image, q: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    return buf.getvalue()


def context(store: Store, item_id: int) -> dict | None:
    """{"before", "text", "after"} around a document/code hit, read fresh from the file."""
    it = store.get(item_id)
    if it is None or it.source != "files" or not it.path or not os.path.isfile(it.path):
        return None
    from .sources import files
    try:
        i = int(it.key.rsplit("#", 1)[1])
    except (IndexError, ValueError):
        i = 0
    ext = os.path.splitext(it.path)[1].lower()
    size, overlap = config.get("chunk_chars"), config.get("chunk_overlap")
    cs = files.chunks(files.extract(it.path), size, overlap, i + 2)
    if i >= len(cs):
        return {"before": "", "text": it.snippet, "after": ""}
    hit = cs[i][1]
    if ext not in files.DOCS and ext not in (".ipynb", ".html", ".htm", ".xml"):
        lines = _raw_lines(it.path, ext in files.DATA)
        span = _find_lines(lines, hit)
        if span:
            a, b = span
            return {"before": "\n".join(lines[max(0, a - CONTEXT_LINES):a])[-MAX_CONTEXT:],
                    "text": "\n".join(lines[a:b + 1])[:MAX_CONTEXT],
                    "after": "\n".join(lines[b + 1:b + 1 + CONTEXT_LINES])[:MAX_CONTEXT]}
    return {"before": cs[i - 1][1][-800:] if i > 0 else "", "text": hit,
            "after": cs[i + 1][1][:800] if i + 1 < len(cs) else ""}


def _raw_lines(path: str, head: bool) -> list[str]:
    from .sources.files import HEAD_BYTES
    with open(path, "rb") as f:
        return f.read(HEAD_BYTES if head else 8_000_000).decode("utf-8", "ignore").splitlines()


def _find_lines(lines: list[str], chunk: str) -> tuple[int, int] | None:
    """Map a whitespace-normalised chunk back to line numbers in the original file."""
    # Chunk lines are the file's non-blank lines with whitespace collapsed (the first and last may be
    # partial), so align them against non-blank file lines: try every exact match of the chunk's most
    # distinctive line as an anchor and keep the alignment where the most lines agree.
    nonblank = [k for k, l in enumerate(lines) if l.strip()]
    norm = [re.sub(r"\s+", " ", lines[k]).strip() for k in nonblank]
    want = [re.sub(r"\s+", " ", l).strip() for l in chunk.splitlines() if l.strip()]
    if not want or not norm:
        return None
    pos = {}
    for k, l in enumerate(norm):
        pos.setdefault(l, []).append(k)
    inner = want[1:-1] or want
    anchor = max(range(len(want)), key=lambda j: (want[j] in inner, len(want[j])))
    best, best_score = None, 0
    for k in pos.get(want[anchor], [])[:200]:
        start = k - anchor
        score = sum(1 for j, w in enumerate(want)
                    if 0 <= start + j < len(norm) and (norm[start + j] == w or w in norm[start + j]))
        if score > best_score:
            best, best_score = start, score
    if best is None or best_score < max(1, len(want) // 2):
        return None
    a = nonblank[max(0, best)]
    b = nonblank[min(len(nonblank) - 1, best + len(want) - 1)]
    return a, b
