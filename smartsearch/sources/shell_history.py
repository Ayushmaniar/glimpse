"""Commands I ran: PowerShell (PSReadLine) history. Commands are only ever copied, never run."""
from __future__ import annotations

import hashlib
import logging
import os
import re

from ..embedder import Embedder, doc
from ..store import Item
from .base import Progress, Result, Source

log = logging.getLogger(__name__)

LINES_KEY, HEAD_KEY = "shell_history:lines", "shell_history:head"
SECRET = re.compile(r"password|passwd|secret|token|apikey|api_key|authorization|bearer|-p\s|--password|"
                    r"connectionstring", re.IGNORECASE)
BLOB = re.compile(r"[A-Za-z0-9+/=_-]{32,}")  # long base64 / hex strings: keys, hashes, tokens
MAX_TITLE = 140
CHUNK = 64


def _default_path() -> str:
    appdata = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "PowerShell", "PSReadLine", "ConsoleHost_history.txt")


def _keep(cmd: str) -> bool:
    return len(cmd) >= 4 and not SECRET.search(cmd) and not BLOB.search(cmd)


def _latest_unique(lines: list[str]) -> list[str]:
    """Unique commands, ordered by their latest occurrence."""
    seen: dict[str, None] = {}
    for line in lines:
        cmd = line.strip()
        if _keep(cmd):
            seen.pop(cmd, None)
            seen[cmd] = None
    return list(seen)


def _head(lines: list[str], n: int) -> str:
    """Fingerprint of the first lines. PSReadLine trims old lines, which changes the head."""
    return hashlib.sha1("\n".join(lines[: min(3, n)]).encode("utf-8", "replace")).hexdigest()


def _read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


class ShellHistorySource(Source):
    name = "shell_history"
    label = "Commands I ran"
    panel = "list"
    intent = ("A terminal or PowerShell command I typed before, like how I installed a package, "
              "restarted a service or ran a script")
    indexed = True
    supports_image_query = False
    floor = 0.66
    strong = 0.79

    def __init__(self, store, history_path: str | None = None):
        super().__init__(store)
        self.path = history_path or _default_path()

    def index(self, emb: Embedder, progress: Progress) -> None:
        try:
            st = os.stat(self.path)
        except OSError:  # no history file (or deleted): drop what was indexed from it
            self.store.remove_path(self.name, self.path)
            return
        if self.store.file_state(self.name).get(self.path) == (st.st_mtime, st.st_size):
            return
        lines = _read_lines(self.path)
        prev_n = int(self.store.kv_get(LINES_KEY) or 0)
        if len(lines) >= prev_n and _head(lines, prev_n) == self.store.kv_get(HEAD_KEY):
            start = prev_n  # the file only grew: embed the new lines
        else:  # trimmed or rewritten: start over
            self.store.remove_path(self.name, self.path, commit=False)
            start = 0
        cmds = _latest_unique(lines[start:])
        for i in range(0, len(cmds), CHUNK):
            part = cmds[i:i + CHUNK]
            vecs = emb.texts([doc(c, "PowerShell command") for c in part])
            for cmd, vec in zip(part, vecs):
                try:
                    key = "shell:" + hashlib.sha1(cmd.encode("utf-8", "replace")).hexdigest()
                    self.store.add(self.name, key, vec, path=self.path, title=cmd[:MAX_TITLE],
                                   subtitle="PowerShell", meta={"command": cmd}, commit=False, ts=0)
                except Exception:
                    log.exception("could not index a shell command")
            progress("indexing commands", min(i + CHUNK, len(cmds)), len(cmds))
        self.store.kv_set(LINES_KEY, str(len(lines)))
        self.store.kv_set(HEAD_KEY, _head(lines, len(lines)))
        self.store.mark_file(self.name, self.path, st.st_mtime, st.st_size)
        self.store.commit()

    def to_result(self, it: Item, score: float) -> Result:
        cmd = it.meta.get("command", it.title)
        return Result(id=f"{self.name}:{it.id}", source=self.name, title=cmd[:MAX_TITLE],
                      subtitle="PowerShell", score=score, icon="❯", kind="shell",
                      actions=[{"label": "Copy", "op": "copy"}])

    def run(self, result_id: str, op: str = "open") -> dict:
        it = self.store.get(int(result_id.split(":", 1)[1]))
        if it is None:
            return {"ok": False, "error": "item no longer exists"}
        return {"ok": True, "copy": it.meta.get("command", ""), "toast": "Command copied"}
