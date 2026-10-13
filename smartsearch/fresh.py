"""New and changed files become searchable seconds after they are saved, while the app runs.

The periodic scan (core.Engine._index_loop) walks every folder: the first one takes hours, later
ones still stat ~100k files. Relying on it alone, a new screenshot or download stays unfindable
until the next pass. This watches the folders instead (ReadDirectoryChangesW, via watchdog):

  created / modified   queued; indexed once the file has been quiet SETTLE_S (downloads and saves
                       finish first) and at most once per COOLDOWN_S (a log or notebook being edited)
  moved / renamed      vectors kept, only the path changes, so renaming a folder of 5,000 photos
                       costs no GPU time; moved out of the indexed folders counts as deleted
  deleted              gone from results at once (a deleted folder is a single event: its subtree goes)

New files go ahead of the backlog on the GPU (embedder.FRESH) but stay behind searches. They are
indexed on battery too (a few files are cheap); only a manual pause stops them. Events missed while
the app was closed, on a buffer overflow or on a network drive are caught by the periodic scan,
which stays as the safety net.
"""
from __future__ import annotations

import collections
import logging
import os
import re
import threading
import time

from . import config, embedder
from .sources import walk

log = logging.getLogger(__name__)

SETTLE_S = 3.0      # a file must be quiet this long before it is read
COOLDOWN_S = 120.0  # a file that keeps changing is re-indexed at most this often
# Partial downloads, editor lock/swap files and Office's temp copies; the final name arrives as a rename.
TEMP = re.compile(r"(?i)^~\$|^\.~lock|\.(tmp|temp|part|partial|crdownload|download|opdownload|!ut|swp)$")


class Watcher:
    def __init__(self, engine):
        self.engine = engine
        self.store = engine.store
        self._pending: dict[str, float] = {}   # path -> time of its last event
        self._done: dict[str, float] = {}      # path -> when it was last indexed (for the cooldown)
        self._ops: collections.deque = collections.deque()  # ("gone", path, is_folder) | ("move", src, dest)
        self._lock = threading.Lock()
        self._observer = None
        self._roots: list[str] = []
        self._sources: list = []
        self._handlers: dict = {}
        self._exts: set[str] = set()
        self.stats = {"indexed": 0, "moved": 0, "removed": 0, "last": 0.0}

    # ---- setup -------------------------------------------------------------------------
    def start(self) -> None:
        self.refresh()
        threading.Thread(target=self._run, name="fresh", daemon=True).start()

    def refresh(self) -> None:
        """(Re)read which sources are on and which folders to watch, e.g. after settings change."""
        self._sources = [s for s in self.engine.enabled() if getattr(s, "exts", None) and hasattr(s, "handler")]
        self._handlers = {s.name: s.handler(self.engine.emb) for s in self._sources}
        self._exts = set().union(*(s.exts for s in self._sources)) if self._sources else set()
        roots = [r for r in config.get("folders") if os.path.isdir(r)]
        if roots == self._roots and self._observer is not None:
            return
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        watcher = self

        class Events(FileSystemEventHandler):
            def on_any_event(self, ev) -> None:
                try:
                    watcher._event(ev.event_type, os.fsdecode(ev.src_path), os.fsdecode(ev.dest_path or ""),
                                   ev.is_directory)
                except Exception:  # never let one odd event kill the watch thread
                    log.debug("watch event failed", exc_info=True)

        if self._observer is not None:
            self._observer.stop()
        obs = Observer()
        for r in roots:
            try:
                obs.schedule(Events(), r, recursive=True)
            except OSError as e:
                log.warning("cannot watch %s: %s", r, e)
        obs.daemon = True
        obs.start()
        self._observer, self._roots = obs, roots
        log.info("watching %d folders for new files", len(roots))

    # ---- events (watchdog's thread: keep it cheap) ---------------------------------------
    def _interesting(self, path: str) -> bool:
        name = os.path.basename(path)
        return (walk.ext_of(name) in self._exts and not TEMP.search(name)
                and walk.wanted(path))

    def _event(self, kind: str, src: str, dest: str, is_dir: bool) -> None:
        if kind == "moved":
            if not is_dir:  # a moved folder also arrives as one "moved" event per file inside it
                with self._lock:
                    self._ops.append(("move", src, dest))
        elif kind == "deleted":  # a deleted folder arrives only as itself: no extension = treat as a folder
            ext = walk.ext_of(src)
            if (ext in self._exts or not ext) and walk.wanted(src):
                with self._lock:
                    self._ops.append(("gone", src, not ext))
                    self._pending.pop(src, None)
        elif kind in ("created", "modified") and not is_dir and self._interesting(src):
            with self._lock:
                self._pending[src] = time.time()

    # ---- worker ------------------------------------------------------------------------
    def _run(self) -> None:
        while True:
            time.sleep(1.0)
            try:
                self._apply_ops()
                if not self.engine.ready.is_set() or walk._paused.is_set():
                    continue
                now = time.time()
                with self._lock:
                    due = sorted((p for p, t in self._pending.items()
                                  if now - t >= SETTLE_S and now - self._done.get(p, 0.0) >= COOLDOWN_S),
                                 key=lambda p: -self._pending[p])
                for p in due:
                    with self._lock:
                        t = self._pending.get(p)
                        if t is None or time.time() - t < SETTLE_S:  # changed again meanwhile
                            continue
                        del self._pending[p]
                    self._index(p)
                    self._apply_ops()  # a delete or rename that arrived meanwhile wins over the queue
                if due:
                    cutoff = time.time() - COOLDOWN_S
                    with self._lock:
                        self._done = {p: t for p, t in self._done.items() if t > cutoff}
            except Exception:
                log.exception("fresh indexing failed")

    def _index(self, path: str) -> None:
        did = False
        with embedder.priority(embedder.FRESH):
            ext = walk.ext_of(path)
            for s in self._sources:
                if ext in s.exts and s.name in self._handlers:
                    status = walk.index_one(self.store, s.name, path, self._handlers[s.name], s.min_size)
                    did |= status is not None
        if did:
            self._done[path] = time.time()
            self.stats["indexed"] += 1
            self.stats["last"] = time.time()
            log.info("indexed a new or changed file within seconds (%d so far)", self.stats["indexed"])

    def _apply_ops(self) -> None:
        while True:
            with self._lock:
                if not self._ops:
                    return
                op = self._ops.popleft()
            self._done.pop(op[1], None)  # a file recreated under this name is new, not a repeat edit
            if op[0] == "gone":
                self.stats["removed"] += bool(self.store.remove_under(op[1], folder=op[2]))
                continue
            _, src, dest = op
            ext = walk.ext_of(src)
            was = ext in self._exts and walk.wanted(src)
            now = self._interesting(dest)
            if was and now and ext == walk.ext_of(dest) and self.store.move_path(src, dest):
                self.stats["moved"] += 1
                continue
            # Not indexed under the old name (a finished download, an editor's save-by-rename), changed
            # type, or moved out of the indexed folders.
            if was and self.store.remove_under(src, folder=False):
                self.stats["removed"] += 1
            if now:
                with self._lock:
                    self._pending[dest] = time.time()

    def status(self) -> str:
        if self._observer is None:
            return ""
        last = self.stats["last"]
        ago = "" if not last else f" · last new file {_ago(time.time() - last)}"
        return f"watching {len(self._roots)} folders{ago}"


def _ago(s: float) -> str:
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s / 60:.0f} min ago"
    if s < 86400:
        return f"{s / 3600:.0f} h ago"
    return f"{s / 86400:.0f} d ago"
