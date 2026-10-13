"""Scanned-page source and on-demand previews, on synthetic fixtures (runs the real model).

  .venv\\Scripts\\python.exe -m tests.test_scans
"""
from __future__ import annotations

import os
import shutil
import tempfile
import wave

import numpy as np

TMP = tempfile.mkdtemp(prefix="ss_scans_")
os.environ["SMARTSEARCH_DATA"] = os.path.join(TMP, "data")

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

from smartsearch import config, embedder, previews  # noqa: E402
from smartsearch.sources.scans import ScansSource  # noqa: E402
from smartsearch.store import Store  # noqa: E402
from smartsearch.sources import walk as _walk  # noqa: E402
_walk.on_battery = lambda: False  # index even when the laptop runs on battery

PAGES = {
    "lease_scan.pdf": ["RESIDENTIAL LEASE AGREEMENT", "", "Landlord: Maple Properties LLC", "Tenant: A. Maniar",
                       "Premises: 4102 Nobel Drive, Apt 12, San Diego, CA", "Monthly rent: $1,850 due on the 1st",
                       "Security deposit: $2,000", "Term: 12 months from September 1"],
    "vaccine_scan.pdf": ["CERTIFICATE OF VACCINATION", "", "Name: Alex Doe", "Vaccine: Hepatitis B, dose 2 of 3",
                         "Date administered: 14 March 2024", "Clinic: Student Health Services", "Lot no. HB-2231"],
    "note_scan.pdf": ["things to pack for the trek", "", "- rain jacket", "- headlamp + batteries",
                      "- water purification tablets", "- trekking poles", "- first aid kit"],
    "statement_scan.pdf": ["MONTHLY BANK STATEMENT", "", "Account ending 4417", "Opening balance   3,214.55",
                           "Grocery store        -82.10", "Electricity bill     -64.00", "Salary credit     +4,100.00"],
}
QUERIES = {
    "how much rent do I pay": "lease_scan.pdf",
    "my hepatitis vaccine certificate": "vaccine_scan.pdf",
    "packing list for hiking": "note_scan.pdf",
    "bank statement with electricity bill payment": "statement_scan.pdf",
}
UNRELATED = ["recipe for chocolate cake", "python error traceback", "photo of a cat on a sofa"]


def font(size: int):
    for f in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def scan(lines: list[str], hand: bool) -> Image.Image:
    """A page that looks scanned: off-white, slightly rotated, blurred, noisy."""
    img = Image.new("RGB", (1240, 1754), (246, 244, 238))
    d = ImageDraw.Draw(img)
    f = font(46 if hand else 38)
    for i, line in enumerate(lines):
        d.text((120, 160 + i * (90 if hand else 70)), line, fill=(40, 40, 60), font=f)
    img = img.rotate(-1.2, fillcolor=(230, 230, 225)).filter(ImageFilter.GaussianBlur(0.8))
    noise = np.random.default_rng(0).normal(0, 6, (img.height, img.width, 1))
    return Image.fromarray(np.clip(np.asarray(img, np.float32) + noise, 0, 255).astype(np.uint8))


def main() -> None:
    docs = os.path.join(TMP, "docs")
    os.makedirs(docs)
    for name, lines in PAGES.items():
        scan(lines, hand=name == "note_scan.pdf").save(os.path.join(docs, name), "PDF", resolution=150)
    # Born-digital PDF with a real text layer: must be skipped by the scans source.
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, StreamObject, DictionaryObject
    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    stream = StreamObject()
    stream.set_data(b"BT /F1 14 Tf 72 700 Td (" + b"This invoice has a real text layer. " * 40 + b") Tj ET")
    page[NameObject("/Contents")] = w._add_object(stream)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                             NameObject("/Subtype"): NameObject("/Type1"),
                                             NameObject("/BaseFont"): NameObject("/Helvetica")})})})
    with open(os.path.join(docs, "digital.pdf"), "wb") as f:
        w.write(f)

    config.update({"folders": [docs]})
    store = Store()
    src = ScansSource(store)
    emb = embedder.get()
    src.index(emb, lambda *a: None)
    titles = sorted({t for t, in store._db.execute("SELECT title FROM items WHERE source='scans'")})
    status = dict(store._db.execute("SELECT path, status FROM files WHERE source='scans'").fetchall())
    assert status.get(os.path.join(docs, "digital.pdf")) == "empty", status  # text layer -> not a scan
    print("indexed scans:", titles)
    assert "digital.pdf" not in titles and len(titles) == 4, titles

    ok = 0
    for q, want in QUERIES.items():
        res = src.search(emb, emb.query(q), q, k=4)
        top = res[0]
        print(f"  {q!r:52} -> {top.title:20} {top.score:.3f}  (2nd {res[1].score:.3f})")
        ok += top.title == want
    worst = []
    for q in UNRELATED:
        res = src.search(emb, emb.query(q), q, k=1)
        worst.append(res[0].score)
        print(f"  unrelated {q!r:42} -> best {res[0].score:.3f}")
    print(f"top-1 {ok}/{len(QUERIES)}; unrelated max {max(worst):.3f} (floor {src.floor})")
    assert ok == len(QUERIES)
    assert max(worst) < src.floor, "noise above floor"
    th = src.search(emb, emb.query("lease"), "lease", k=1)[0]
    assert th.thumb and th.kind == "scan" and th.meta["page"] == 1

    # On-demand previews: a waveform for audio.
    wav = os.path.join(docs, "tone.wav")
    with wave.open(wav, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
        t = np.arange(16000 * 4) / 16000
        wf.writeframes((np.sin(2 * np.pi * 220 * t) * np.linspace(0, 1, len(t)) * 12000).astype(np.int16).tobytes())
    iid = store.add("audio", "audio:tone", np.ones(store.dim, np.float32) / np.sqrt(store.dim), path=wav, title="tone.wav")
    data, ctype = previews.preview(store, iid)
    assert ctype == "image/png" and data[:4] == b"\x89PNG", ctype
    print("waveform preview:", len(data), "bytes")
    # ...and the matching page for a text-PDF hit.
    iid = store.add("files", "files:digital#0", np.ones(store.dim, np.float32) / np.sqrt(store.dim),
                    path=os.path.join(docs, "digital.pdf"), title="digital.pdf", meta={"loc": "p. 1"})
    data, ctype = previews.preview(store, iid)
    assert ctype == "image/jpeg" and data[:2] == b"\xff\xd8", ctype
    print("pdf page preview:", len(data), "bytes")
    emb.close()
    print("OK")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
