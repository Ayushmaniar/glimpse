"""PDF page rendering and text probing with PDFium (pypdfium2), shared by scans and previews.

PDFium is not thread-safe, so every call goes through one lock.
"""
from __future__ import annotations

import threading
from typing import Iterator

from PIL import Image

_lock = threading.Lock()

# A page with less extractable text than this is treated as an image (scan, photo of paper, slide export).
MIN_TEXT_CHARS = 40


def _render(page, px: int) -> Image.Image:
    w, h = page.get_size()
    return page.render(scale=px / max(w, h, 1)).to_pil().convert("RGB")


def scanned_pages(path: str, px: int, limit: int, max_pages: int = 300) -> Iterator[tuple[int, int, Image.Image]]:
    """Yield (page number from 1, total pages, image) for pages that have no real text layer."""
    import pypdfium2 as pdfium

    found = 0
    with _lock:
        pdf = pdfium.PdfDocument(path)
    try:
        n = len(pdf)
        for i in range(min(n, max_pages)):
            with _lock:
                page = pdf[i]
                try:
                    tp = page.get_textpage()
                    text = tp.get_text_range()
                    tp.close()
                    img = _render(page, px) if len(text.strip()) < MIN_TEXT_CHARS else None
                finally:
                    page.close()
            if img is not None:
                yield i + 1, n, img
                found += 1
                if found >= limit:
                    return
    finally:
        with _lock:
            pdf.close()


def render_page(path: str, page_no: int, px: int) -> tuple[Image.Image | None, int]:
    """Render one page (numbered from 1) so its longest side is `px` pixels. Returns (image, page count)."""
    import pypdfium2 as pdfium

    with _lock:
        pdf = pdfium.PdfDocument(path)
        try:
            n = len(pdf)
            if not 1 <= page_no <= n:
                return None, n
            page = pdf[page_no - 1]
            try:
                return _render(page, px), n
            finally:
                page.close()
        finally:
            pdf.close()
