"""The contract every search source implements.

A source owns one kind of thing (settings actions, files, photos, open windows, ...).
It can index things ahead of time into the shared Store, and answer a query vector
with Results. The UI renders each source as its own section using `panel`.

Panels the UI knows how to draw:
  cards    big clickable cards (settings / fixes), may carry a live `widget`
  list     rows with title, subtitle and snippet (files, history, commands)
  grid     image thumbnails (photos, screenshots, video frames)
  emoji    big glyph tiles, click copies to clipboard
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np

from ..embedder import Embedder
from ..store import Item, Store

Progress = Callable[[str, int, int], None]  # (message, done, total)


@dataclass
class Result:
    id: str                     # "<source>:<store item id or other stable id>"
    source: str
    title: str
    subtitle: str = ""
    snippet: str = ""
    score: float = 0.0
    thumb: str | None = None    # URL served by the app, e.g. "/api/thumb/123"
    icon: str | None = None     # short glyph shown when there is no thumb
    badge: str | None = None    # e.g. "Settings", "Tool", "Command", "PDF", "00:42"
    actions: list[dict] = field(default_factory=list)  # [{"label": "Open folder", "op": "reveal"}]
    # Modality, so the UI can draw the right thumbnail: photo, video, audio, pdf, scan, doc, slides, code,
    # text, data, notebook, setting, tool, command, window, emoji, web, shell.
    kind: str | None = None
    meta: dict = field(default_factory=dict)  # t, dur (s) · page, pages · ext, lang · w, h, taken

    def to_json(self) -> dict:
        return asdict(self)


class Source:
    name: str = ""          # stable id, also the settings key under "sources"
    label: str = ""         # section heading in the UI
    panel: str = "list"     # cards | list | grid | emoji
    # One or two sentences describing what someone searching this source wants.
    # Used to decide which section to show first for a query.
    intent: str = ""
    indexed: bool = True    # False for live sources (open windows) that search on the fly
    supports_image_query: bool = False  # True if an image query makes sense (photos, video frames, docs)
    # The best *unrelated* match gets higher as an index grows (more items, more near-misses), so floors
    # are measured at `floor_ref_n` items and rise by `floor_per_decade` per 10x more items.
    floor_ref_n: int = 0          # 0: fixed floors (catalogs that don't grow: settings, emoji)
    floor_per_decade: float = 0.0

    def __init__(self, store: Store):
        self.store = store

    # Called on a background thread. Must be incremental: skip unchanged items.
    def index(self, emb: Embedder, progress: Progress) -> None:
        return None

    # Rank against a query vector. `text` is the raw query (None for image queries).
    def search(self, emb: Embedder, qvec: np.ndarray, text: str | None, k: int = 8) -> list[Result]:
        return [self.to_result(it, s) for it, s in self.store.search(self.name, qvec, k)]

    def to_result(self, it: Item, score: float) -> Result:
        return Result(id=f"{self.name}:{it.id}", source=self.name, title=it.title,
                      subtitle=it.subtitle, snippet=it.snippet, score=score)

    # Perform an action on a result. op is "open" unless the UI sent another op from Result.actions.
    def run(self, result_id: str, op: str = "open") -> dict:
        it = self.store.get(int(result_id.split(":", 1)[1]))
        if it is None:
            return {"ok": False, "error": "item no longer exists"}
        if op == "reveal" and it.path:
            reveal(it.path)
        elif it.path:
            open_path(it.path)
        return {"ok": True}

    # Optional live status card (network, battery, storage). Returns None or a dict:
    # {"title": str, "rows": [[label, value, tone]], "tone": "ok|warn|bad"}  tone per row optional.
    def widget(self, result_ids: list[str]) -> dict | None:
        return None


# ---- helpers shared by sources --------------------------------------------------------

_HOME = os.path.expanduser("~")


def pretty_dir(path: str, width: int = 70) -> str:
    """Show the home folder as ~ and middle-truncate long paths to `width` characters."""
    if os.path.normcase(path).startswith(os.path.normcase(_HOME)):
        path = "~" + path[len(_HOME):]
    if len(path) > width:
        path = path[: width // 3] + "…" + path[-(width * 2 // 3 - 1):]
    return path

def open_path(target: str) -> None:
    """Open a file, folder, URL or ms-settings: URI with its default handler."""
    os.startfile(target)  # noqa: S606 — user clicked this result


BROWSERS = {"firefox.exe", "msedge.exe", "chrome.exe", "brave.exe", "vivaldi.exe", "opera.exe"}


def open_pdf(path: str, page: int | None) -> None:
    """Open a PDF at a page when the default PDF app is a browser (they all honour #page=N); else just open it."""
    exe = _pdf_handler() if page and page > 1 else None
    if exe:
        from pathlib import Path
        subprocess.Popen([exe, Path(path).as_uri() + f"#page={page}"])
    else:
        open_path(path)


def _pdf_handler() -> str | None:
    import shlex
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice") as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog + r"\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
        exe = shlex.split(cmd, posix=False)[0].strip('"')
    except (OSError, IndexError, ValueError):
        return None
    return exe if os.path.basename(exe).lower() in BROWSERS and os.path.isfile(exe) else None


def reveal(path: str) -> None:
    subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])


def run_command(cmdline: str) -> None:
    """Open a new console window running a command (user-initiated)."""
    subprocess.Popen(cmdline, creationflags=subprocess.CREATE_NEW_CONSOLE, shell=False)
