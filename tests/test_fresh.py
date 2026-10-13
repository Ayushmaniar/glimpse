"""Folder watcher: new, edited, renamed, moved and deleted files on a temp folder (runs the real model).

  .venv\\Scripts\\python.exe -m tests.test_fresh
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time

TMP = tempfile.mkdtemp(prefix="ss_fresh_")
os.environ["SMARTSEARCH_DATA"] = os.path.join(TMP, "data")
HOME = os.path.join(TMP, "home")
os.makedirs(HOME)

from PIL import Image, ImageDraw  # noqa: E402

from smartsearch import config, embedder, fresh  # noqa: E402
from smartsearch.sources.files import FilesSource  # noqa: E402
from smartsearch.sources.images import ImagesSource  # noqa: E402
from smartsearch.store import Store  # noqa: E402
from smartsearch.sources import walk as _walk  # noqa: E402
_walk.on_battery = lambda: False  # index even when the laptop runs on battery

config.update({"folders": [HOME]})
fresh.SETTLE_S, fresh.COOLDOWN_S = 1.0, 4.0


class Engine:  # the parts of core.Engine the watcher uses
    def __init__(self, store, emb):
        self.store, self.emb = store, emb
        self.ready = threading.Event()
        self.ready.set()
        self.srcs = [FilesSource(store), ImagesSource(store)]

    def enabled(self):
        return self.srcs


def paths(store, source):
    return sorted(os.path.relpath(p, HOME) for p, in store._db.execute(
        "SELECT DISTINCT path FROM items WHERE source=?", (source,)))


def ids(store, rel):
    return sorted(i for i, in store._db.execute("SELECT id FROM items WHERE path=?", (os.path.join(HOME, rel),)))


def wait(cond, timeout=30.0):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return time.time() - t
        time.sleep(0.2)
    return None


def write(rel, text):
    p = os.path.join(HOME, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def main() -> None:
    store = Store()
    emb = embedder.get()
    w = fresh.Watcher(Engine(store, emb))
    w.start()
    time.sleep(0.5)
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")

    # 1. a new note is searchable within seconds
    write("notes/trip.md", "Packing list for the Yosemite trip: tent, headlamp, bear canister, rain jacket.\n" * 3)
    t = wait(lambda: "notes\\trip.md" in paths(store, "files"))
    check("new file indexed", t is not None, f"{t:.1f}s" if t else "")
    q = emb.query("what to pack for camping")
    top = store.search("files", q, 1)
    check("and found by meaning", top and top[0][0].path.endswith("trip.md"))

    # 2. rename keeps the vectors (same item ids, no new embedding)
    before, n0 = ids(store, "notes\\trip.md"), w.stats["indexed"]
    os.rename(os.path.join(HOME, "notes", "trip.md"), os.path.join(HOME, "notes", "yosemite.md"))
    wait(lambda: "notes\\yosemite.md" in paths(store, "files"), 10)
    time.sleep(fresh.SETTLE_S + 1.5)
    check("rename moves vectors", ids(store, "notes\\yosemite.md") == before and not ids(store, "notes\\trip.md"))
    check("rename costs no embedding", w.stats["indexed"] == n0, f"indexed {w.stats['indexed'] - n0}")
    title = store._db.execute("SELECT title FROM items WHERE id=?", (before[0],)).fetchone()[0]
    check("title follows the rename", title == "yosemite.md")

    # 3. renaming the folder moves everything inside
    os.rename(os.path.join(HOME, "notes"), os.path.join(HOME, "travel"))
    t = wait(lambda: paths(store, "files") == ["travel\\yosemite.md"], 10)
    check("folder rename moves its files", t is not None, str(paths(store, "files")))

    # 4. a download in progress is ignored until it gets its real name
    part = write("dl/report.pdf.crdownload", "partial")
    time.sleep(fresh.SETTLE_S + 1.5)
    check("partial download ignored", not any(p.startswith("dl") for p in paths(store, "files")))
    os.remove(part)
    write("dl/tmp_123.txt.crdownload", "Quarterly revenue grew 14 percent driven by cloud subscriptions.\n" * 3)
    os.rename(os.path.join(HOME, "dl", "tmp_123.txt.crdownload"), os.path.join(HOME, "dl", "q3_report.txt"))
    t = wait(lambda: "dl\\q3_report.txt" in paths(store, "files"))
    check("finished download indexed", t is not None)

    # 5. rapid edits: indexed once after they settle, then at most once per cooldown
    n0 = w.stats["indexed"]
    for i in range(5):
        write("log/run.txt", f"training step {i} loss {1 / (i + 1):.3f}\n" * 4)
        time.sleep(0.3)
    wait(lambda: w.stats["indexed"] > n0, 15)
    time.sleep(fresh.SETTLE_S + 0.5)
    check("burst of saves = one index", w.stats["indexed"] - n0 == 1, f"{w.stats['indexed'] - n0}")
    write("log/run.txt", "training finished, final loss 0.012\n" * 4)
    time.sleep(fresh.SETTLE_S + 1.0)
    check("cooldown holds the next one", w.stats["indexed"] - n0 == 1)
    t = wait(lambda: w.stats["indexed"] - n0 == 2, fresh.COOLDOWN_S + 10)
    check("then re-indexed", t is not None)
    snip = store._db.execute("SELECT snippet FROM items WHERE path=?", (os.path.join(HOME, "log", "run.txt"),)).fetchone()
    check("with the latest content", snip and "finished" in snip[0])

    # 6. skipped folders stay skipped
    write("proj/node_modules/pkg/readme.md", "A package readme that should never be indexed.\n" * 3)
    write("proj/.git/notes.txt", "git internals\n" * 10)
    time.sleep(fresh.SETTLE_S + 1.5)
    check("excluded folders ignored", not any("node_modules" in p or ".git" in p for p in paths(store, "files")))

    # 7. images
    img = Image.effect_noise((640, 480), 40).convert("RGB")  # photo-like: big enough to pass the icon filter
    ImageDraw.Draw(img).ellipse((200, 120, 440, 360), fill=(250, 210, 40))
    img.save(os.path.join(HOME, "travel", "sun.jpg"), quality=90)
    t = wait(lambda: "travel\\sun.jpg" in paths(store, "images"))
    check("new image indexed", t is not None, f"{t:.1f}s" if t else "")

    # 8. moving a file out of the indexed folders = deleted; deleting a folder drops its subtree
    out = os.path.join(TMP, "outside")
    os.makedirs(out)
    shutil.move(os.path.join(HOME, "dl", "q3_report.txt"), os.path.join(out, "q3_report.txt"))
    t = wait(lambda: "dl\\q3_report.txt" not in paths(store, "files"), 10)
    check("moved out = removed", t is not None)
    shutil.rmtree(os.path.join(HOME, "travel"))
    t = wait(lambda: not any(p.startswith("travel") for p in paths(store, "files") + paths(store, "images")), 10)
    check("deleted folder = subtree removed", t is not None, str(paths(store, "files") + paths(store, "images")))
    files_rows = store._db.execute("SELECT COUNT(*) FROM files WHERE path LIKE ?", (os.path.join(HOME, "travel") + "%",)).fetchone()[0]
    check("and its bookkeeping", files_rows == 0)

    print("ALL PASS" if ok else "SOME FAILED")
    w._observer.stop()
    store.close() if hasattr(store, "close") else None
    shutil.rmtree(TMP, ignore_errors=True)
    os._exit(0 if ok else 1)


if __name__ == "__main__":
    main()
