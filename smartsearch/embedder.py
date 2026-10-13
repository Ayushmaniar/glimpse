"""EmbeddingGemma 2 wrapper: one engine, thread-safe, with the model card's task prefixes.

Every vector this module returns is L2-normalised float32 of length `dim`, so cosine
similarity is a plain dot product. Matryoshka truncation (768 -> 512/256/128) happens
here, and the truncated vector is re-normalised as the model card requires.
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from typing import Iterable, Sequence

import numpy as np

from . import config

log = logging.getLogger(__name__)

# Prefixes from the google/embeddinggemma-2 model card.
QUERY = "task: search result | query: {}"
QA = "task: question answering | query: {}"
CODE_QUERY = "task: code retrieval | query: {}"
CLASSIFY = "task: classification | query: {}"
SIMILARITY = "task: sentence similarity | query: {}"


def doc(text: str, title: str | None = None) -> str:
    return f"title: {title or 'none'} | text: {text}"


BULK, FRESH, SEARCH = 0, 1, 2
_tls = threading.local()


@contextlib.contextmanager
def priority(level: int):
    """Embedding calls made by this thread inside the block run at `level` (default BULK)."""
    old = getattr(_tls, "level", BULK)
    _tls.level = level
    try:
        yield
    finally:
        _tls.level = old


class Embedder:
    """Owns the LiteRT-LM EmbeddingEngine. All calls are serialised by a lock."""

    def __init__(self, dim: int | None = None, backend: str | None = None):
        import litert_lm as lm  # imported lazily so the UI can start without it

        self._lm = lm
        self.dim = dim or config.get("embedding_dim")
        backend = backend or config.get("backend")
        os.makedirs(config.MODEL_CACHE_DIR, exist_ok=True)
        if hasattr(lm, "LogSeverity"):
            lm.set_min_log_severity(lm.LogSeverity.ERROR)
        self._gpu = threading.Lock()
        self._turn = threading.Condition()
        self._waiting = [0, 0, 0]  # callers waiting per priority (BULK, FRESH, SEARCH); lower levels yield
        t = time.perf_counter()
        self.engine, self.backend = None, None
        for name in ([backend] if backend != "auto" else ["gpu", "cpu"]):
            try:
                b = lm.Backend.GPU() if name == "gpu" else lm.Backend.CPU()
                # Audio on GPU is ~3x faster than CPU here (421 vs 1263 ms per 9.4 s clip).
                self.engine = lm.EmbeddingEngine(
                    config.MODEL_PATH, backend=b, vision_backend=b, audio_backend=b,
                    cache_dir=config.MODEL_CACHE_DIR,
                )
                self.backend = name
                break
            except Exception as e:  # GPU can fail on machines without WebGPU support
                log.warning("embedding backend %s failed: %s", name, e)
        if self.engine is None:
            raise RuntimeError("Could not start EmbeddingGemma 2 on GPU or CPU")
        self._opts = lm.EmbeddingOptions(normalize=True)
        self._img_opts = lm.EmbeddingOptions(
            normalize=True, vision_tokens_per_image=config.get("vision_tokens_per_image"))
        # First call compiles kernels; do it now so the first real query is fast.
        self.text("warm up")
        log.info("embedder ready on %s in %.1fs (dim=%d)", self.backend, time.perf_counter() - t, self.dim)

    # ---- helpers -------------------------------------------------------------------
    @contextlib.contextmanager
    def _lock_for(self, urgent: bool):
        """The model runs one call at a time. A search waits for at most one in-flight item, never
        behind a queue of indexing work; a just-saved file (FRESH) likewise jumps the backlog (BULK).
        `urgent` means SEARCH; otherwise the calling thread's `priority()` applies."""
        level = SEARCH if urgent else getattr(_tls, "level", BULK)
        with self._turn:
            self._waiting[level] += 1
            self._turn.wait_for(lambda: not any(self._waiting[level + 1:]))
        try:
            self._gpu.acquire()
        finally:
            with self._turn:
                self._waiting[level] -= 1
                self._turn.notify_all()
        try:
            yield
        finally:
            self._gpu.release()

    @property
    def _lock(self):
        return self._lock_for(False)

    def _finish(self, v: Sequence[float]) -> np.ndarray:
        a = np.asarray(v, dtype=np.float32)[: self.dim]
        n = np.linalg.norm(a)
        return a / n if n else a

    # ---- public API ----------------------------------------------------------------
    def text(self, s: str, urgent: bool = False) -> np.ndarray:
        """Embed one already-formatted string (use QUERY.format(...) or doc(...))."""
        with self._lock_for(urgent):
            return self._finish(self.engine.compute_embedding(s, self._opts).embedding)

    def query(self, q: str, template: str = QUERY) -> np.ndarray:
        return self.text(template.format(q), urgent=True)

    def texts(self, items: Sequence[str], batch: int = 1, urgent: bool = False) -> np.ndarray:
        """Embed many formatted strings. Batching doesn't speed up the GPU here, so batches stay
        tiny and a search never waits behind more than a few chunks."""
        out = []
        for i in range(0, len(items), batch):
            chunk = list(items[i:i + batch])
            with self._lock_for(urgent):
                rs = self.engine.compute_embedding_batch(chunk, self._opts)
            out.extend(self._finish(r.embedding) for r in rs)
        return np.vstack(out) if out else np.zeros((0, self.dim), np.float32)

    def image(self, path_or_bytes: str | bytes, urgent: bool = False) -> np.ndarray:
        from litert_lm._messages import ImageBytes, ImageFile
        item = ImageBytes(path_or_bytes) if isinstance(path_or_bytes, bytes) else ImageFile(path_or_bytes)
        with self._lock_for(urgent):
            return self._finish(self.engine.compute_embedding(item, self._img_opts).embedding)

    def multi_image(self, items: list) -> np.ndarray:
        """One vector for several images (e.g. frames of a video clip), mean-pooled by the model."""
        with self._lock:
            return self._finish(self.engine.compute_embedding(items, self._img_opts).embedding)

    def audio(self, path_or_bytes: str | bytes) -> np.ndarray:
        from litert_lm._messages import AudioBytes, AudioFile
        item = AudioBytes(path_or_bytes) if isinstance(path_or_bytes, bytes) else AudioFile(path_or_bytes)
        with self._lock:
            return self._finish(self.engine.compute_embedding(item, self._opts).embedding)

    def close(self) -> None:
        with self._lock:
            if self.engine is not None:
                self.engine.close()
                self.engine = None


_instance: Embedder | None = None
_instance_lock = threading.Lock()


def get() -> Embedder:
    """Process-wide embedder, created on first use."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Embedder()
        return _instance
