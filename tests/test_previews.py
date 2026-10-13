"""Quick Look previews and text context on synthetic files (no model needed).

  .venv\\Scripts\\python.exe -m tests.test_previews
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import numpy as np

TMP = tempfile.mkdtemp(prefix="ss_prev_")
os.environ["SMARTSEARCH_DATA"] = os.path.join(TMP, "data")

from PIL import Image  # noqa: E402

from smartsearch import previews  # noqa: E402
from smartsearch.store import Store  # noqa: E402
from smartsearch.sources import walk as _walk  # noqa: E402
_walk.on_battery = lambda: False  # index even when the laptop runs on battery


def vec(store):
    return np.ones(store.dim, np.float32) / np.sqrt(store.dim)


def main() -> None:
    store = Store()
    d = os.path.join(TMP, "files")
    os.makedirs(d)

    # 3-page image PDF: any page can be previewed, page count comes back in headers.
    pdf = os.path.join(d, "pages.pdf")
    pages = [Image.new("RGB", (1240, 1754), c) for c in ("white", "lightyellow", "lightblue")]
    pages[0].save(pdf, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    iid = store.add("scans", "scans:pages#p2", vec(store), path=pdf, title="pages.pdf", meta={"page": 2})
    data, h = previews.large(store, iid)
    assert data[:2] == b"\xff\xd8" and h == {"X-Page": "2", "X-Pages": "3"}, h
    assert max(Image.open(__import__("io").BytesIO(data)).size) == previews.LARGE_PAGE_PX
    assert previews.large(store, iid, page=3)[1]["X-Page"] == "3"
    assert previews.large(store, iid, page=9) is None
    print("pdf pages: ok")

    # Photo: downscaled to 1280 px.
    jpg = os.path.join(d, "photo.jpg")
    Image.new("RGB", (4000, 3000), "green").save(jpg, quality=90)
    iid = store.add("images", "images:photo", vec(store), path=jpg, title="photo.jpg")
    data, _ = previews.large(store, iid)
    assert max(Image.open(__import__("io").BytesIO(data)).size) <= previews.LARGE_PHOTO_PX
    print("photo: ok", len(data) // 1024, "KB")

    # Video frame at the matching moment.
    mp4 = os.path.join(d, "clip.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=640x360:rate=10",
                    "-pix_fmt", "yuv420p", mp4], check=True)
    iid = store.add("video", "video:clip@3", vec(store), path=mp4, title="clip.mp4", meta={"t": 3})
    data, _ = previews.large(store, iid)
    assert data[:2] == b"\xff\xd8"
    print("video frame: ok")

    # Code context: indentation from the real file, the hit spans the right lines.
    py = os.path.join(d, "mod.py")
    src = "\n\n".join(f"def func_{i}(a, b):\n    '''Helper number {i} for the loader.'''\n"
                      f"    if a > b:\n        return a * {i}\n    return b + {i}" for i in range(80))
    open(py, "w").write(src)
    iid = store.add("files", f"files:{py}#2", vec(store), path=py, title="mod.py")
    ctx = previews.context(store, iid)
    assert ctx and "        return a *" in ctx["text"], ctx
    assert ctx["before"] and ctx["after"]
    print("code context: ok,", ctx["text"].count("\n") + 1, "lines in the hit")

    # Prose (docx-like path through chunks): falls back to neighbouring chunks.
    md = os.path.join(d, "notes.md")
    open(md, "w").write("\n\n".join(f"Paragraph {i}: notes about the budget review and launch plan." for i in range(200)))
    iid = store.add("files", f"files:{md}#3", vec(store), path=md, title="notes.md")
    ctx = previews.context(store, iid)
    assert ctx and "Paragraph" in ctx["text"] and ctx["before"] and ctx["after"]
    print("prose context: ok")
    print("OK")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
