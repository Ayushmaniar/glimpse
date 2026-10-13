"""Gather the real photos and video moments the launch video is cut from.

    .venv\\Scripts\\python.exe video\\story\\prep.py

Reads video/story/story.json (the queries) and the local index, and writes video/private/media/:
  pile/NNNN.jpg      ~900 varied photos (no screenshots or documents) that fall into the pile
  q/<slug>/*.jpg     each query's real results: cards (480 px) and heroes (1920 px)
  q/<slug>/vNN/      frames of a matched video moment
  manifest.json      what the page loads (relative paths, sizes, result order, timings)
Everything here is the user's own media, so it stays in video/private/ (gitignored).
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from glimpse import config, embedder  # noqa: E402
from glimpse.sources import media  # noqa: E402
from glimpse.sources.images import thumb_name  # noqa: E402
from eval.run_cutoff_eval import server  # noqa: E402

OUT = ROOT / "video" / "private" / "media"
STORY = json.loads((HERE / "story.json").read_text(encoding="utf-8"))
# queries whose photos must never appear (a JSON list); kept with the private media, not in the repo
EXCLUDE_PATH = ROOT / "video" / "private" / "exclude.json"
PILE_N = 900


def slug(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")


def save(src: str, dst: Path, size: int) -> tuple[int, int] | None:
    try:
        im = Image.open(src)
        im.draft("RGB", (size, size))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    im.thumbnail((size, size), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, quality=88)
    return im.size


def vectors(db, source: str):
    dim = config.get("embedding_dim")
    rows = [r for r in db.execute("SELECT id, path, vec, scale FROM items WHERE source=? AND vec IS NOT NULL",
                                  (source,)) if len(r[2]) == dim and r[1] and os.path.isfile(r[1])]
    ids = np.array([r[0] for r in rows])
    paths = [r[1] for r in rows]
    m = np.frombuffer(b"".join(r[2] for r in rows), np.int8).reshape(len(rows), dim).astype(np.float32)
    m *= np.array([r[3] or 1.0 for r in rows], np.float32)[:, None]
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    return ids, paths, m


def excluded(db, emb, m, ids) -> np.ndarray:
    """Photos that must not appear: everything the app itself returns for each query in video/private/exclude.json,
    plus anything visually close to those hits (other shots of the same moment) or scoring very high on the query."""
    out = np.zeros(len(ids), bool)
    qs = json.loads(EXCLUDE_PATH.read_text(encoding="utf-8")) if EXCLUDE_PATH.exists() else []
    if not qs:
        return out
    port, token = server()
    row = {int(i): k for k, i in enumerate(ids)}
    hits = []
    for q in qs:
        for s in ask(port, token, q)["sections"]:
            if s["source"] == "images":
                hits += [row[n] for r in s["results"] if (n := int(r["id"].split(":")[1])) in row]
        v = emb.query(q)
        sc = m @ (v / np.linalg.norm(v))
        out |= sc >= np.quantile(sc, 0.997)
    if hits:
        out[hits] = True
        near = (m @ m[hits].T).max(axis=1)
        out |= near > 0.86
    print(f"excluded {int(out.sum())} photos for {qs}")
    return out


def pile(db, emb) -> list[dict]:
    ids, paths, m = vectors(db, "images")
    banned = excluded(db, emb, m, ids)
    def score(prompt):
        v = emb.query(prompt)
        return m @ (v / np.linalg.norm(v))

    def best(prompts):
        return np.max(np.stack([score(p) for p in prompts]), axis=0)
    photo = best(["a candid photo of people", "a selfie", "a group photo of friends and family", "a photo of food on a plate",
                  "a landscape photo of nature", "a photo of a city street or building", "a photo of a pet",
                  "a photo at a party, wedding or festival", "a travel photo at a famous place"])
    doc = best(["a document page with printed text", "a screenshot of an app or website", "math equations or handwritten notes",
                "a chart, plot or diagram", "a book cover or poster", "a cartoon, drawing or illustration", "a logo or icon",
                "a product photo on a white background", "a greeting card with text", "a texture or abstract pattern",
                "a meme with a text caption", "a screenshot of a social media post", "a collage or grid of many photos"])
    bad = best(["a person in underwear or a swimsuit", "a wound, injury or blood", "a passport, ID card or bank card",
                "a hospital or medical scan"])
    margin = photo - doc
    good = margin + 0.3 * score("a beautiful happy moment")
    keep = (margin > np.quantile(margin, 0.5)) & (bad < np.quantile(bad, 0.92)) & ~banned
    order = [i for i in np.argsort(-good) if keep[i]][:6000]
    random.Random(7).shuffle(order)
    chosen, mats = [], []
    for i in order:
        if mats and float(np.max(np.stack(mats) @ m[i])) > 0.9:  # near-duplicate burst shots
            continue
        chosen.append(i)
        mats.append(m[i])
        if len(chosen) >= PILE_N:
            break
    out = []
    for k, i in enumerate(chosen):
        src = os.path.join(config.THUMB_DIR, thumb_name(paths[i]))
        src = src if os.path.isfile(src) else paths[i]
        wh = save(src, OUT / "pile" / f"{k:04d}.jpg", 320)
        if wh:
            out.append({"src": f"pile/{k:04d}.jpg", "w": wh[0], "h": wh[1], "_v": m[i]})
    return out


def ask(port, token, q):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/search", json.dumps({"q": q}).encode(),
                                 {"X-Token": token, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=180))


def video_frames(path: str, t: float, dst: Path, secs: float, fps: int = 30) -> int:
    dst.mkdir(parents=True, exist_ok=True)
    for f in dst.glob("*.jpg"):
        f.unlink()
    start = max(0.0, t - 0.4)
    subprocess.run([media.ffmpeg(), "-v", "error", "-y", "-ss", f"{start:.2f}", "-i", path, "-t", f"{secs:.2f}",
                    "-vf", f"fps={fps},scale='min(1600,iw)':-2", "-q:v", "3", str(dst / "%03d.jpg")], check=True)
    return len(list(dst.glob("*.jpg")))


def queries(db, port, token) -> list[dict]:
    ids, paths, m = vectors(db, "images")
    row = {int(i): k for k, i in enumerate(ids)}
    out = []
    for spec in STORY["queries"]:
        q = spec["q"]
        d = ask(port, token, q)
        folder = OUT / "q" / slug(q)
        if folder.exists():
            shutil.rmtree(folder)
        entry = {"q": q, "ms": d["ms"], "sections": {s["source"]: len(s["results"]) for s in d["sections"]},
                 "images": [], "video": None, "kind": spec.get("kind", "photo"), "terms": d.get("terms", [])}
        sec = {s["source"]: s["results"] for s in d["sections"]}
        if spec.get("kind") == "rapid":
            # several quick searches in one slot: each keeps only its rows (no Quick Look)
            entry["subs"] = []
            for sq in spec["subs"]:
                sd = ask(port, token, sq)
                sub = {"q": sq, "ms": sd["ms"], "sections": {s["source"]: len(s["results"]) for s in sd["sections"]},
                       "terms": sd.get("terms", [])}
                text_results(sub, sd, {"show": spec["show"]}, folder, port, token)
                entry["subs"].append(sub)
                print(f"  {sq!r}: {[r['title'] for r in sub['rows']]} {sd['ms']} ms")
            out.append(entry)
            continue
        if spec.get("kind") == "text":
            text_results(entry, d, spec, folder, port, token)
            out.append(entry)
            print(f"{q!r}: {len(entry['rows'])} rows, preview={'page' if entry.get('page') else 'context' if entry.get('context') else 'none'}")
            continue
        seen = []
        for r in sec.get("images", []):
            n = int(r["id"].split(":")[1])
            k = row.get(n)
            if k is None:
                continue
            if seen and float(np.max(np.stack(seen) @ m[k])) > 0.985:
                continue
            seen.append(m[k])
            j = len(entry["images"])
            card = save(paths[k], folder / f"card{j}.jpg", 480)
            hero = save(paths[k], folder / f"hero{j}.jpg", 1920)
            if card and hero:
                entry["images"].append({"card": f"q/{slug(q)}/card{j}.jpg", "hero": f"q/{slug(q)}/hero{j}.jpg",
                                        "w": hero[0], "h": hero[1], "score": r["score"], "_v": m[k]})
        if spec.get("video") is not None and sec.get("video"):
            r = sec["video"][min(spec["video"], len(sec["video"]) - 1)]
            n = int(r["id"].split(":")[1])
            p, meta = db.execute("SELECT path, meta FROM items WHERE id=?", (n,)).fetchone()
            meta = json.loads(meta or "{}")
            t = float(meta.get("t", 0))
            nf = video_frames(p, t, folder / "v", spec.get("video_secs", 4.0))
            first = Image.open(folder / "v" / "001.jpg")
            entry["video"] = {"dir": f"q/{slug(q)}/v", "frames": nf, "fps": 30, "w": first.width, "h": first.height,
                              "badge": f"{int(t) // 60}:{int(t) % 60:02d}", "t": t}
        out.append(entry)
        print(f"{q!r}: {len(entry['images'])} unique photos, video={'yes' if entry['video'] else 'no'}, {d['ms']} ms")
    return out


def layout(manifest: dict, clusters: int = 7) -> None:
    """The real "meaning space": each photo's EmbeddingGemma 2 vector, projected to 3D (PCA) and clustered
    (k-means), stored as "e": [x, y, z] (unit-ish scale) and "c": cluster id. brain.js draws the cloud from it."""
    items = manifest["pile"] + [im for q in manifest["queries"] for im in q["images"]]
    X = np.stack([it["_v"] for it in items]).astype(np.float64)
    X -= X.mean(0)
    _, _, vt = np.linalg.svd(X, full_matrices=False)
    P = X @ vt[:3].T
    P /= P.std(0) + 1e-9
    rng = np.random.default_rng(3)
    cent = X[rng.choice(len(X), clusters, replace=False)]
    for _ in range(40):
        lab = np.argmax(X @ cent.T / (np.linalg.norm(cent, axis=1) + 1e-9), axis=1)
        cent = np.stack([X[lab == c].mean(0) if np.any(lab == c) else cent[c] for c in range(clusters)])
    for it, p, c in zip(items, P, lab):
        it["e"] = [round(float(v), 3) for v in p]
        it["c"] = int(c)
        del it["_v"]


def sentence_boxes(item_id: int, page: int, snippet: str, terms: list[str]) -> list[list[float]]:
    """Where the matching sentence sits on the PDF page: line boxes as fractions of the page [x0, y0, x1, y1],
    found in the PDF's own text layer, so the video can highlight the real sentence on the real page."""
    import pypdfium2 as pdfium
    db = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=120)
    path = db.execute("SELECT path FROM items WHERE id=?", (item_id,)).fetchone()[0]
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", snippet)) if x.strip()]
    best = max(sents, key=lambda x: sum(t.lower() in x.lower() for t in terms) + len(x) / 1000) if sents else ""
    doc = pdfium.PdfDocument(path)
    pg = doc[page - 1]
    W, H = pg.get_size()
    tp = pg.get_textpage()
    words = best.split()
    # slides put titles and diagram labels ("A B J M E") before the prose: start after the last lone capital letter
    lone = [i for i, w in enumerate(words[:-5]) if len(w) == 1 and w.isupper()]
    if lone:
        words = words[lone[-1] + 1:]
    for n in (len(words), 12, 8, 5, 3):   # the longest run of the sentence that the text layer matches
        frag = " ".join(words[:n])
        if not frag:
            break
        found = tp.search(frag, match_case=False).get_next()
        if found:
            start, count = found
            # extend to the whole sentence length if only a prefix matched
            count = max(count, min(len(" ".join(words)), tp.count_chars() - start))
            boxes = []
            for i in range(tp.count_rects(start, count)):
                l, b, r, t = tp.get_rect(i)
                boxes.append([round(l / W, 4), round(1 - t / H, 4), round(r / W, 4), round(1 - b / H, 4)])
            return boxes
    return []


def text_results(entry: dict, d: dict, spec: dict, folder: Path, port: int, token: str) -> None:
    """Rows of a text search exactly as the app returned them, limited to the sections and counts in story.json
    ("show": {"files": 2, ...}), plus the Quick Look of the top row: the PDF page image, or the code around the hit."""
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in d["sections"]:
        n = spec.get("show", {}).get(s["source"], 0)
        for r in s["results"][:n]:
            rows.append({"source": s["source"], "label": s["label"], "panel": s["panel"], "title": r["title"],
                         "subtitle": r.get("subtitle") or "", "snippet": r.get("snippet") or "", "badge": r.get("badge"),
                         "icon": r.get("icon"), "kind": r.get("kind"), "page": (r.get("meta") or {}).get("page"),
                         "_id": r["id"]})
    # live controls the app shows with settings results (brightness, volume, ...): only their names and values,
    # never the status rows (those can hold a Wi-Fi name)
    for s in d["sections"]:
        ctl = (s.get("widget") or {}).get("controls")
        if ctl:
            entry["controls"] = [{k: c[k] for k in ("id", "kind", "label", "icon", "value", "unit") if k in c} for c in ctl]
            break
    order = list(spec.get("show", {}).keys())
    rows.sort(key=lambda r: order.index(r["source"]))
    entry["rows"] = rows
    top = rows[0] if rows else None
    if top and spec.get("preview") and top["source"] == spec["preview"]:
        num = top["_id"].split(":")[1]
        def get(path):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"X-Token": token})
            return urllib.request.urlopen(req, timeout=180).read()
        if top["kind"] == "pdf":
            (folder / "page.jpg").write_bytes(get(f"/api/preview/{num}?size=large&page={top['page'] or 1}"))
            im = Image.open(folder / "page.jpg")
            entry["page"] = {"src": f"q/{slug(entry['q'])}/page.jpg", "w": im.width, "h": im.height, "n": top["page"],
                             "hl": sentence_boxes(int(num), top["page"] or 1, top["snippet"], entry["terms"])}
        else:
            entry["context"] = json.loads(get(f"/api/context/{num}"))
    for r in rows:
        del r["_id"]


def counts(db) -> dict:
    return dict(db.execute("SELECT source, COUNT(*) FROM items GROUP BY source").fetchall())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=120)
    port, token = server()
    manifest = {"counts": counts(db), "queries": queries(db, port, token)}
    if "--no-pile" not in sys.argv or not (OUT / "manifest.json").exists():
        emb = embedder.get()
        manifest["pile"] = pile(db, emb)
    else:
        manifest["pile"] = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))["pile"]
    if any("_v" in p for p in manifest["pile"]):
        layout(manifest)
    else:
        for q in manifest["queries"]:
            for im in q["images"]:
                im.pop("_v", None)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print("pile", len(manifest["pile"]), "counts", manifest["counts"])
