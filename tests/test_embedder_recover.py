r"""The embedder survives losing its GPU: when every call starts failing, it starts a new engine and carries on;
when only one input fails (a corrupt image), the error is raised and the engine is kept. No model is loaded.

  .venv\Scripts\python.exe -m tests.test_embedder_recover
"""
from __future__ import annotations

import sys
import threading
import types

import numpy as np

from glimpse import embedder


class FakeEngine:
    def __init__(self, n):
        self.n, self.lost, self.closed = n, False, False

    def compute_embedding(self, item, opts):
        if self.lost or item == "corrupt":
            raise RuntimeError("Failed to compute embedding.")
        return types.SimpleNamespace(embedding=[float(self.n), 1.0, 0.0, 0.0])

    def compute_embedding_batch(self, items, opts):
        return [self.compute_embedding(i, opts) for i in items]

    def close(self):
        self.closed = True


def make() -> embedder.Embedder:
    e = object.__new__(embedder.Embedder)
    e.dim, e._want, e._opts, e._img_opts = 4, "auto", None, None
    e._gpu, e._turn, e._waiting = threading.Lock(), threading.Condition(), [0, 0, 0]
    started = []

    def start(backend):
        started.append(FakeEngine(len(started)))
        return started[-1], "gpu"
    e._start = start
    e.engine, e.backend = start("auto")
    return e, started


def main() -> None:
    fails = 0

    def check(name, ok):
        nonlocal fails
        fails += not ok
        print(("ok  " if ok else "FAIL"), name)

    e, started = make()
    check("first engine works", e.text("hello")[0] > -1)
    try:
        e.text("corrupt")
        check("a bad input raises", False)
    except RuntimeError:
        check("a bad input raises", True)
    check("a bad input keeps the engine", len(started) == 1 and not started[0].closed)

    started[0].lost = True                      # the GPU goes away under the running app
    v = e.query("photos of a temple")
    check("a lost engine is replaced", len(started) == 2 and started[0].closed)
    check("the call that hit it still answers", np.isclose(v[0] / v[1], 1.0))
    check("later calls use the new engine", e.texts(["a", "b"]).shape == (2, 4) and len(started) == 2)

    print("ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
