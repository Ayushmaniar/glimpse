"""Incremental folder sync shared by the file, image, audio and video sources."""
from __future__ import annotations

import ctypes
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from .. import config
from ..store import Store

log = logging.getLogger(__name__)

# Set by the indexer for its first, quick pass: only files modified after this timestamp are
# considered, and nothing is treated as deleted (older files are simply not looked at yet).
RECENT_SINCE: float | None = None

# FILE_ATTRIBUTE_OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS: cloud-only (e.g. OneDrive Files On-Demand).
CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000

# ---- pausing -------------------------------------------------------------------------
# Indexers call gate() between files. It blocks while the user paused indexing (tray menu) or,
# with `pause_on_battery`, while the laptop runs on battery; searching keeps working meanwhile.
_paused = threading.Event()
_battery = {"t": 0.0, "on": False}


def set_paused(on: bool) -> None:
    _paused.set() if on else _paused.clear()


def on_battery() -> bool:
    if time.time() - _battery["t"] > 5:
        class SPS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        s = SPS()
        ok = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s))
        _battery.update(t=time.time(), on=bool(ok) and s.ACLineStatus == 0)
    return _battery["on"]


def pause_reason() -> str:
    if _paused.is_set():
        return "paused"
    if config.get("pause_on_battery") and on_battery():
        return "paused on battery (plug in to continue)"
    return ""


def gate(progress=None) -> None:
    """Block while indexing is paused; report it once through `progress`."""
    reason = pause_reason()
    if not reason:
        return
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState(0x80000000)  # ES_CONTINUOUS: let the laptop sleep while paused
    while reason:
        if progress:
            progress(f"Indexing {reason}", 0, 0)
            progress = None
        time.sleep(2)
        reason = pause_reason()
    kernel32.SetThreadExecutionState(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED, as index_all set it


# ".env", ".env.local", ".env.production", "prod.env": where API keys usually live. splitext() sees no
# extension (or ".local"), so name them explicitly.
ENV_FILE = re.compile(r"(?i)^(?:\.env(?:\.[\w-]+)*|[\w.-]+\.env)$")


def ext_of(path: str) -> str:
    """Lower-case extension used to pick a source; ".env" for dotenv files whatever their suffix."""
    name = os.path.basename(path)
    return ".env" if ENV_FILE.match(name) else os.path.splitext(name)[1].lower()


# ---- one file at a time ---------------------------------------------------------------
# The folder watcher (fresh.py) and the periodic scan can reach the same file at once; whoever
# claims it first indexes it, the other skips it.
_claims: set[tuple[str, str]] = set()
_claims_lock = threading.Lock()


@contextmanager
def claim(source: str, path: str):
    key = (source, os.path.normcase(path))
    with _claims_lock:
        got = key not in _claims
        _claims.add(key)
    try:
        yield got
    finally:
        if got:
            with _claims_lock:
                _claims.discard(key)


def wanted(path: str) -> bool:
    """Is this path inside a configured folder and outside every skipped one (same rules as `walk`)?"""
    key = os.path.normcase(os.path.abspath(path))
    if key.startswith(os.path.normcase(config.ROOT) + os.sep):
        return False
    for root in config.get("folders"):
        r = os.path.normcase(os.path.abspath(root)).rstrip(os.sep) + os.sep
        if key.startswith(r):
            skip = {d.lower() for d in config.get("exclude_dirs")}
            dirs = key[len(r):].split(os.sep)[:-1]
            return not any(d in skip or d.startswith((".", "$")) for d in dirs)
    return False


def stat(path: str, min_size: int = 1) -> tuple[float, int] | None:
    """(mtime, size) of a local, big-enough file; None for missing, cloud-only or tiny files."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if getattr(st, "st_file_attributes", 0) & CLOUD_ONLY or st.st_size < min_size or not os.path.isfile(path):
        return None
    return st.st_mtime, st.st_size


def index_one(store: Store, source: str, path: str, handle: Callable[[str], str], min_size: int = 1) -> str | None:
    """Index one file now unless it is unchanged, gone, or being indexed by another thread.
    Returns the status recorded, or None if nothing was done."""
    st = stat(path, min_size)
    if st is None:
        return None
    with claim(source, path) as got:
        if not got:
            return None
        old = store.file_row(source, path)
        if old and abs(old[0] - st[0]) <= 1e-3 and old[1] == st[1]:
            return None
        store.remove_path(source, path, commit=False)
        try:
            status = handle(path)
        except Exception as e:  # one bad file must not stop indexing
            log.debug("index %s failed: %s", path, e)
            status = f"error: {e.__class__.__name__}"
        if status == "deferred":  # the source wants the periodic scan to decide (image sequences)
            store.commit()
            return None
        store.mark_file(source, path, st[0], st[1], status)
        store.commit()
        return status


def walk(exts: set[str], min_size: int = 1) -> Iterator[tuple[str, float, int]]:
    """Yield (path, mtime, size) for files under the configured folders with one of `exts`."""
    skip = {d.lower() for d in config.get("exclude_dirs")}
    own = os.path.normcase(config.ROOT)  # Glimpse's own catalog/eval files would match every settings query
    seen: set[str] = set()
    for root in config.get("folders"):
        if not os.path.isdir(root):
            continue
        for dp, dns, fns in os.walk(root, onerror=lambda e: None):
            dns[:] = [d for d in dns if d.lower() not in skip and not d.startswith((".", "$"))
                      and os.path.normcase(os.path.join(dp, d)) != own]
            for f in fns:
                if ext_of(f) not in exts:
                    continue
                p = os.path.join(dp, f)
                key = os.path.normcase(p)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if getattr(st, "st_file_attributes", 0) & CLOUD_ONLY:
                    continue  # OneDrive placeholder: reading it would download the file
                if st.st_size >= min_size and (RECENT_SINCE is None or st.st_mtime >= RECENT_SINCE):
                    yield p, st.st_mtime, st.st_size


def sync(store: Store, source: str, exts: set[str], handle: Callable[[str], str], progress,
         min_size: int = 1, label: str = "files") -> None:
    """Index new/changed files with `handle(path) -> status` and drop deleted ones.

    `handle` adds the file's items to the store (after its old items were removed) and
    returns a status string ("ok", "empty", "skipped", "error: ...") that is remembered so
    unchangeable failures are not retried every scan.
    """
    known = store.file_state(source)
    todo, present = [], set()
    for p, mtime, size in walk(exts, min_size):
        present.add(p)
        old = known.get(p)
        if not old or abs(old[0] - mtime) > 1e-3 or old[1] != size:
            todo.append((p, mtime, size))
    if RECENT_SINCE is None:
        for p in set(known) - present:
            store.remove_path(source, p, commit=False)
    store.commit()
    if not todo:
        return
    # Newest first: recent files are the ones people search for.
    todo.sort(key=lambda x: -x[1])
    t0 = time.time()
    for i, (p, mtime, size) in enumerate(todo):
        gate(progress)
        if i % 5 == 0:
            rate = i / max(time.time() - t0, 1e-6)
            eta = f" · ~{(len(todo) - i) / rate / 60:.0f} min left" if i > 20 and rate > 0 else ""
            progress(f"{i:,}/{len(todo):,} {label}{eta}", i, len(todo))
        with claim(source, p) as got:
            if not got or store.file_row(source, p) == (mtime, size):
                continue  # the folder watcher has it, or already indexed it since this scan started
            store.remove_path(source, p, commit=False)
            try:
                status = handle(p)
            except Exception as e:  # one bad file must not stop indexing
                log.debug("index %s failed: %s", p, e)
                status = f"error: {e.__class__.__name__}"
            store.mark_file(source, p, mtime, size, status)
        if i % 25 == 24:
            store.commit()
    store.commit()
