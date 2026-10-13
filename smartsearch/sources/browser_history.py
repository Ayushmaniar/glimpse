"""Pages I visited: Chrome, Edge and Brave history, read from a copy of each profile's History DB."""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from ..embedder import Embedder, doc
from ..store import Item
from .base import Progress, Result, Source, open_path

log = logging.getLogger(__name__)

EPOCH = dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)  # Chrome times are microseconds since this
CHUNK = 64
ROW_LIMIT = 20000
SENSITIVE_PATH = re.compile(r"(?<![a-z])(login|signin|sign-in|oauth|auth|callback|checkout|bank)(?![a-z])")
SENSITIVE_PARAMS = {"token", "code", "session", "key", "sig"}
SKIP_HOSTS = {"localhost", "127.0.0.1", "::1", "mail.google.com"}


def _default_roots() -> dict[str, str]:
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    return {
        "Chrome": os.path.join(local, "Google", "Chrome", "User Data"),
        "Edge": os.path.join(local, "Microsoft", "Edge", "User Data"),
        "Brave": os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data"),
    }


def _profiles(root: str) -> list[tuple[str, str]]:
    """(profile name, History path) for Default and Profile N folders."""
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, "History")
        if (name == "Default" or name.startswith("Profile ")) and os.path.isfile(path):
            out.append((name, path))
    return out


def _host_if_ok(url: str) -> str | None:
    """The host for a page worth remembering, or None for login pages, local and private URLs."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    host = parts.hostname or ""
    if not host or host in SKIP_HOSTS or host.endswith(".localhost"):
        return None
    if SENSITIVE_PATH.search(parts.path.lower()):
        return None
    if {k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)} & SENSITIVE_PARAMS:
        return None
    return host.removeprefix("www.")


def _read_rows(db_path: str, watermark: int) -> list[tuple]:
    """Rows newer than the watermark, newest first. The browser may hold the original open, so copy it."""
    tmp = tempfile.mkdtemp(prefix="smartsearch-history-")
    try:
        copy = os.path.join(tmp, "History")
        shutil.copy2(db_path, copy)
        con = sqlite3.connect(Path(copy).as_uri() + "?mode=ro", uri=True)
        try:
            return con.execute(
                "SELECT url, title, visit_count, last_visit_time FROM urls "
                "WHERE title IS NOT NULL AND title <> '' AND last_visit_time > ? "
                "ORDER BY last_visit_time DESC LIMIT ?", (watermark, ROW_LIMIT)).fetchall()
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _visit_time(last_visit_time: int) -> dt.datetime:
    return EPOCH + dt.timedelta(microseconds=last_visit_time)


def _ago(iso: str | None) -> str:
    if not iso:
        return ""
    then = dt.datetime.fromisoformat(iso)
    days = (dt.datetime.now(dt.timezone.utc) - then).days
    if days <= 0:
        return "visited today"
    if days == 1:
        return "visited yesterday"
    if days < 60:
        return f"visited {days} days ago"
    return f"visited on {then:%d %b %Y}"


class BrowserHistorySource(Source):
    name = "browser_history"
    label = "Pages I visited"
    panel = "list"
    intent = "A web page, article or video I visited earlier in my browser"
    indexed = True
    supports_image_query = False
    floor = 0.65
    strong = 0.86

    def __init__(self, store, roots: dict[str, str] | None = None):
        super().__init__(store)
        self.roots = _default_roots() if roots is None else roots  # browser name -> "User Data" folder

    def index(self, emb: Embedder, progress: Progress) -> None:
        for browser, root in self.roots.items():
            for profile, path in _profiles(root):
                try:
                    self._index_profile(emb, progress, browser, profile, path)
                except Exception:  # a locked or odd database must not stop the others
                    log.exception("could not read %s history (%s)", browser, profile)
        self.store.commit()

    def _index_profile(self, emb: Embedder, progress: Progress, browser: str, profile: str, path: str) -> None:
        wm_key = f"browser_watermark:{browser}:{profile}"
        progress(f"{browser} {profile}: reading history", 0, 0)
        rows = _read_rows(path, int(self.store.kv_get(wm_key) or 0))
        if not rows:
            return
        picked, seen = [], set()
        for url, title, _visits, last in rows:
            host = _host_if_ok(url)
            key = (title.strip(), host)
            if host is None or key in seen:
                continue
            seen.add(key)
            picked.append((url, title.strip(), host, _visit_time(last)))
        texts = [doc(f"{title} — {host}", None) for _, title, host, _ in picked]
        for i in range(0, len(picked), CHUNK):
            vecs = emb.texts(texts[i:i + CHUNK])
            for (url, title, host, when), vec in zip(picked[i:i + CHUNK], vecs):
                try:
                    self.store.add(self.name, f"browser:{browser}:{url}", vec, title=title,
                                   subtitle=f"{host} · {browser}",
                                   meta={"url": url, "browser": browser, "host": host,
                                         "last_visit": when.isoformat(timespec="seconds")}, ts=when.timestamp(),
                                   commit=False)
                except Exception:
                    log.exception("could not index a page")
            progress(f"{browser} {profile}: indexing pages", min(i + CHUNK, len(picked)), len(picked))
        self.store.kv_set(wm_key, str(rows[0][3]))  # rows are newest first

    def to_result(self, it: Item, score: float) -> Result:
        return Result(id=f"{self.name}:{it.id}", source=self.name, title=it.title, subtitle=it.subtitle,
                      snippet=_ago(it.meta.get("last_visit")), score=score, icon="🌐", kind="web",
                      actions=[{"label": "Copy link", "op": "copy"}])

    def run(self, result_id: str, op: str = "open") -> dict:
        it = self.store.get(int(result_id.split(":", 1)[1]))
        if it is None:
            return {"ok": False, "error": "item no longer exists"}
        url = it.meta.get("url", "")
        if op == "copy":
            return {"ok": True, "copy": url}
        open_path(url)
        return {"ok": True}
