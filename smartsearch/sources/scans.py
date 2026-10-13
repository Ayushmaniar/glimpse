"""Scanned PDF pages, searchable by what is on the page.

The documents source only sees a PDF's text layer, so scans, phone photos of paper and
image-only exports are invisible to it (21% of the PDFs on the dev laptop). Here every page
without a real text layer is rendered and embedded as an image: the model reads printed text,
forms and handwriting, and the page doubles as the thumbnail. ~0.2 s per scanned page.
"""
from __future__ import annotations

import io
import os

from .. import config
from .base import Result, Source, open_pdf, pretty_dir
from .images import MODEL_PX, THUMB_PX, thumb_name
from .pdfpages import scanned_pages
from .walk import sync


class ScansSource(Source):
    name = "scans"
    label = "Scanned pages"
    panel = "grid"
    exts, min_size = {".pdf"}, 1000
    intent = ("A scanned document or image-only PDF page: a form, letter, certificate, ID, receipt, statement or "
              "handwritten note, found by what is written or shown on the page.")
    supports_image_query = True
    # Same image<->text scale as photos; measured on synthetic scans (see tests/test_scans.py).
    floor, strong = 0.68, 0.79
    image_floor, image_strong = 0.72, 0.90
    floor_ref_n, floor_per_decade = 10, 0.027  # same image<->text behaviour as photos
    keep_within = 0.12

    def index(self, emb, progress) -> None:
        sync(self.store, self.name, self.exts, self.handler(emb), progress, min_size=self.min_size, label="PDFs checked for scans")

    def handler(self, emb):
        """handle(path) -> status: index one file (its old items are already removed)."""
        os.makedirs(config.THUMB_DIR, exist_ok=True)
        limit = config.get("max_scanned_pages")

        def handle(path: str) -> str:
            n_found = 0
            for page, pages, img in scanned_pages(path, MODEL_PX, limit):
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=88)
                th = thumb_name(path, f"#p{page}")
                t = img.copy()
                t.thumbnail((THUMB_PX, THUMB_PX))
                t.save(os.path.join(config.THUMB_DIR, th), "JPEG", quality=82)
                v = emb.image(buf.getvalue())
                self.store.add(self.name, f"scans:{path}#p{page}", v, path=path, title=os.path.basename(path),
                               subtitle=os.path.dirname(path), meta={"page": page, "pages": pages, "thumb": th},
                               commit=False)
                n_found += 1
            return "ok" if n_found else "empty"  # "empty" = born-digital PDF, nothing to do

        return handle

    def search(self, emb, qvec, text, k=8) -> list[Result]:
        out, per_file = [], {}
        for it, s in self.store.search(self.name, qvec, k * 4):
            if per_file.get(it.path, 0) >= 2:  # at most two pages of the same file
                continue
            per_file[it.path] = per_file.get(it.path, 0) + 1
            out.append(self.to_result(it, s))
            if len(out) >= k:
                break
        return out

    def to_result(self, it, score) -> Result:
        m = it.meta
        th = m.get("thumb")
        return Result(id=f"{self.name}:{it.id}", source=self.name, title=it.title, subtitle=pretty_dir(it.subtitle),
                      score=score, thumb=f"/api/thumb/{th}" if th and os.path.exists(os.path.join(config.THUMB_DIR, th)) else None,
                      icon="📠", badge=f"p. {m.get('page')}", kind="scan",
                      meta={"page": m.get("page"), "pages": m.get("pages"), "ext": ".pdf"},
                      actions=[{"label": "Show in folder", "op": "reveal"}])

    def run(self, result_id: str, op: str = "open") -> dict:
        it = self.store.get(int(result_id.split(":", 1)[1]))
        if it is not None and op == "open" and it.path:
            open_pdf(it.path, it.meta.get("page"))  # opens at the scanned page
            return {"ok": True}
        return super().run(result_id, op)
