r"""One shared SQLite connection, many threads: a filename search while the indexer inserts must not deadlock.

An insert runs a Python function inside SQLite (the fts triggers call ss_name_words) while holding the connection's
mutex; a read from another thread that skips the store's lock holds the GIL and waits for that mutex. Both stop,
and with the GIL held so does the whole process: a regression shows up as this test hanging, not failing.

  .venv\Scripts\python.exe -m tests.test_store_threads
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

import numpy as np

from glimpse.sources.names import NamesSource
from glimpse.store import Store

SECONDS = 6.0


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="glimpse-threads-")
    store = Store(os.path.join(tmp, "t.sqlite"))
    names = NamesSource(store)
    for i in range(300):
        store.mark_file("files", os.path.join(tmp, f"invoice_{i}.pdf"), 1.0 + i, 10)
    store.commit()
    stop = time.time() + SECONDS
    done = {"adds": 0, "searches": 0}

    def writer():
        rng = np.random.default_rng(0)
        while time.time() < stop:
            v = rng.standard_normal(store.dim).astype(np.float32)
            store.add("files", f"k{done['adds']}", v / np.linalg.norm(v), path=os.path.join(tmp, f"note_{done['adds']}.txt"),
                      title=f"note_{done['adds']}.txt", snippet="some words about invoices", ts=0)
            done["adds"] += 1

    def reader():
        while time.time() < stop:
            names.search(None, None, "invoice")
            done["searches"] += 1

    ts = [threading.Thread(target=writer, daemon=True)] + [threading.Thread(target=reader, daemon=True) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(max(0.0, stop + 10 - time.time()))
    hung = any(t.is_alive() for t in ts)
    print(f"{done['adds']} inserts, {done['searches']} searches in {SECONDS:.0f}s")
    print("FAIL: deadlocked" if hung else "ALL PASS")
    sys.stdout.flush()
    os._exit(1 if hung else 0)  # a deadlocked thread can't be joined


if __name__ == "__main__":
    main()
