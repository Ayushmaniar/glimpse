"""Files by name: the one thing embeddings are bad at ("IMG_4021", "invoice_2024", "resume.pdf").

Live lookup over every file path the indexers have seen (documents, photos, recordings, videos,
including sampled frames). Every query word must appear in the file name; exact and prefix
matches first, then newer files. Long natural-language queries are left to the semantic sources.

Searching one folder (opened from File Explorer) also walks that folder live, so any file type
(zip, exe, installers, files outside the indexed folders) is found by name, the way Explorer does.
"""
from __future__ import annotations

import os
import re
import threading
import time

from .. import config
from ..store import _like_prefix
from . import walk
from .base import Result, Source, open_path, pretty_dir, reveal
from .images import thumb_name

STOP = {"the", "and", "for", "with", "from", "that", "this", "file", "files", "my", "a", "an", "of", "in", "on",
        "to", "find", "open", "show", "where", "is", "named", "called"}
KIND = {".pdf": "pdf", ".docx": "doc", ".pptx": "slides", ".ipynb": "notebook", ".mp4": "video", ".mkv": "video",
        ".mov": "video", ".avi": "video", ".webm": "video", ".mp3": "audio", ".m4a": "audio", ".wav": "audio",
        ".ogg": "audio", ".flac": "audio", ".aac": "audio", ".opus": "audio"}
MAX_WORDS = 4
LIVE_BUDGET_S = 0.35  # live folder walk before the full listing is ready


class NamesSource(Source):
    name = "names"
    label = "Files by name"
    panel = "list"
    intent = "A file I remember the name of: a filename, part of a filename, or a file extension."
    indexed = False
    floor, strong = 0.60, 0.80

    def __init__(self, store):
        super().__init__(store)
        self._live: dict[str, str] = {}  # id -> path of live-walk hits, so run() only opens what was shown
        self._live_lock = threading.Lock()

    def search(self, emb, qvec, text, k=8) -> list[Result]:
        if not text:
            return []
        words = [w for w in re.findall(r"[\w\-.]+", text.lower()) if len(w) >= 2 and w not in STOP]
        if not words or len(words) > MAX_WORDS or not any(len(w) >= 3 for w in words):
            return []
        under = self.store.scope_folder()
        like = " AND ".join("lower(path) LIKE ? ESCAPE '\\'" for _ in words)
        esc = [w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for w in words]  # "IMG_" is literal
        args = [f"%{w}%" for w in esc]
        if under:
            like += " AND lower(path) LIKE ? ESCAPE '\\'"
            args.append(_like_prefix(under.lower()))
        # the store's lock, always: an insert runs a Python function inside SQLite (the fts triggers) while holding
        # the connection's mutex, so an unlocked read from another thread here deadlocks against it on the GIL
        with self.store._lock:
            rows = self.store._db.execute(
                f"SELECT rowid, path, MAX(mtime) FROM files WHERE ({like}) AND status NOT LIKE 'error%' "
                f"GROUP BY path ORDER BY MAX(mtime) DESC LIMIT 400", args).fetchall()
        if under:
            known = {os.path.normcase(p) for _, p, _ in rows}
            rows += [(None, p, m) for p, m in _walk_live(under, words) if os.path.normcase(p) not in known]
        scored = []
        joined = " ".join(words)
        for rowid, path, mtime in rows:
            base = os.path.basename(path).lower()
            stem = os.path.splitext(base)[0]
            if not all(w in base for w in words):  # the words matched a folder, not the name
                continue
            if stem == joined.replace(" ", "") or base == joined or stem == joined:
                s = 0.95
            elif stem.startswith(words[0]):
                s = 0.88
            else:
                s = 0.82
            scored.append((s, mtime or 0.0, rowid, path))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        out = []
        for s, mtime, rowid, path in scored[:k]:
            rid = str(rowid) if rowid is not None else self._remember(path)
            ext = os.path.splitext(path)[1].lower()
            th = thumb_name(path)
            thumb = f"/api/thumb/{th}" if os.path.exists(os.path.join(config.THUMB_DIR, th)) else None
            kind = KIND.get(ext) or ("photo" if thumb else "text")
            out.append(Result(id=f"{self.name}:{rid}", source=self.name, title=os.path.basename(path),
                              subtitle=pretty_dir(os.path.dirname(path)), score=s, thumb=thumb, icon="📄",
                              badge=ext[1:].upper() or None, kind=kind, meta={"ext": ext, "ts": mtime},
                              actions=[{"label": "Show in folder", "op": "reveal"}]))
        return out

    def _remember(self, path: str) -> str:
        rid = f"live{abs(hash(os.path.normcase(path))) % 10**12}"
        with self._live_lock:
            if len(self._live) > 2000:
                self._live.clear()
            self._live[rid] = path
        return rid

    def run(self, result_id: str, op: str = "open") -> dict:
        rid = result_id.split(":", 1)[1]
        if rid.startswith("live"):
            with self._live_lock:
                path = self._live.get(rid)
        else:
            with self.store._lock:
                row = self.store._db.execute("SELECT path FROM files WHERE rowid=?", (int(rid),)).fetchone()
            path = row[0] if row else None
        if not path or not os.path.exists(path):
            return {"ok": False, "error": "file no longer exists"}
        reveal(path) if op == "reveal" else open_path(path)
        return {"ok": True}


def _walk_live(folder: str, words: list[str]) -> list[tuple[str, float]]:
    """Files anywhere under `folder` whose name contains every word.

    From the folder's full listing when it is ready (built in the background, see `prefetch`); until then a
    breadth-first walk within a small budget, so every subfolder's top levels are seen before deep ones."""
    with _tree_lock:
        ready = _tree["folder"] == folder and _tree["done"] and time.time() - _tree["at"] < TREE_TTL_S
        files = _tree["files"] if ready else None
    if files is not None:
        return [(path, mtime) for name, path, mtime in files if all(w in name for w in words)]
    prefetch(folder)
    out = []
    for name, path, mtime in _scan(folder, time.perf_counter() + LIVE_BUDGET_S):
        if all(w in name for w in words):
            out.append((path, mtime))
    return out


# ---- the scoped folder's full file list --------------------------------------------------
# Documents here: ~230k entries, ~1.3 s to list. Built once when the launcher opens on a folder, kept for
# TREE_TTL_S, so every keystroke matches names across all nested folders in milliseconds.
TREE_TTL_S = 120.0
_tree: dict = {"folder": None, "at": 0.0, "files": [], "done": False, "building": False}
_tree_lock = threading.Lock()


def prefetch(folder: str) -> None:
    """Start listing `folder` in the background unless a fresh listing exists or is being built."""
    folder = os.path.normpath(folder)
    with _tree_lock:
        fresh = _tree["folder"] == folder and (_tree["building"] or time.time() - _tree["at"] < TREE_TTL_S)
        if fresh:
            return
        _tree.update(folder=folder, at=time.time(), files=[], done=False, building=True)
    threading.Thread(target=_build, args=(folder,), name="names-tree", daemon=True).start()


def _build(folder: str) -> None:
    files = list(_scan(folder, None))
    with _tree_lock:
        if _tree["folder"] == folder:  # still the folder being searched
            _tree.update(at=time.time(), files=files, done=True, building=False)


def _scan(folder: str, deadline: float | None):
    """(lower-case name, path, mtime) of every local file under `folder`, breadth-first. DirEntry.stat()
    costs no extra disk access on Windows, so this is one directory listing per folder."""
    import collections
    skip = {d.lower() for d in config.get("exclude_dirs")}
    queue = collections.deque([folder])
    while queue:
        if deadline is not None and time.perf_counter() > deadline:
            return
        d = queue.popleft()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() not in skip and not e.name.startswith((".", "$")):
                        queue.append(e.path)
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if not getattr(st, "st_file_attributes", 0) & walk.CLOUD_ONLY:
                yield e.name.lower(), e.path, st.st_mtime
