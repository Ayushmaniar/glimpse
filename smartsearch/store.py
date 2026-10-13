"""Local vector store: SQLite for metadata + vectors, numpy matrices in RAM for search.

On disk, vectors are float16 (2 bytes/dim) or int8 with a per-vector float32 scale
(1 byte/dim + 4 bytes). In RAM they are decoded once to float32, because converting int8
on every query costs ~225 ms per million vectors while a float32 matmul takes ~18 ms.
RAM cost: 1 KB per 256-d vector (1 GB per million). Search is brute-force per source.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,  -- ids are never reused, the RAM index relies on it
  source TEXT NOT NULL,
  key TEXT NOT NULL UNIQUE,      -- unique per item, e.g. 'files:C:\\a.pdf#3'
  path TEXT,                     -- file the item came from (for incremental re-indexing)
  title TEXT, subtitle TEXT, snippet TEXT,
  meta TEXT,                     -- JSON, source-specific
  vec BLOB NOT NULL,
  scale REAL,                    -- int8 only
  ts REAL                        -- when the thing happened: photo taken / file modified / page visited (unix s)
);
CREATE INDEX IF NOT EXISTS items_source ON items(source);
CREATE INDEX IF NOT EXISTS items_path ON items(path);
CREATE TABLE IF NOT EXISTS files (
  path TEXT NOT NULL, source TEXT NOT NULL, mtime REAL, size INTEGER, status TEXT,
  PRIMARY KEY (path, source)
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

# Keyword index (BM25) over document text, next to the vectors: embeddings miss exact words, names, IDs and
# phrases ("Contoso Award", "INV-20391"), which BM25 finds by construction. Index only (contentless: the text
# lives in items.snippet), kept in step with `items` by triggers, so every add/remove/move path updates it.
LEXICAL_SOURCES = ("files",)
_IN = ", ".join(f"'{s}'" for s in LEXICAL_SOURCES)
FTS_SCHEMA = f"""
CREATE VIRTUAL TABLE fts USING fts5(title, body, content='', contentless_delete=1,
                                    tokenize='porter unicode61 remove_diacritics 2');
CREATE TRIGGER fts_ai AFTER INSERT ON items WHEN new.source IN ({_IN}) BEGIN
  INSERT INTO fts(rowid, title, body) VALUES (new.id, ss_name_words(new.title), new.snippet);
END;
CREATE TRIGGER fts_ad AFTER DELETE ON items WHEN old.source IN ({_IN}) BEGIN
  DELETE FROM fts WHERE rowid = old.id;
END;
CREATE TRIGGER fts_au AFTER UPDATE OF title, snippet ON items WHEN new.source IN ({_IN}) BEGIN
  DELETE FROM fts WHERE rowid = old.id;
  INSERT INTO fts(rowid, title, body) VALUES (new.id, ss_name_words(new.title), new.snippet);
END;
"""
FTS_VERSION = "2"  # 2: file names indexed with their camelCase / letter-digit parts split out
_NAME_PARTS = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")


def query_terms(text: str) -> list[str]:
    """The words of a query as the keyword index sees them (lower case, up to 8, no repeats)."""
    return list(dict.fromkeys(re.findall(r"\w+", text.lower())))[:8]


def name_words(title: str | None) -> str | None:
    """A file name as people type it: "NorthwindGamesOffer2024.pdf" also as "Northwind Games Offer 2024 .pdf". The tokenizer
    already splits on spaces, _ - . and other punctuation; camelCase and letter-digit joins it keeps whole. The
    original stays too, so "northwindgamesoffer" still matches."""
    if not title:
        return title
    split = _NAME_PARTS.sub(" ", title)
    return title if split == title else f"{title} {split}"


@dataclass
class Item:
    id: int
    source: str
    key: str
    path: str | None
    title: str
    subtitle: str
    snippet: str
    meta: dict


class _Matrix:
    """In-RAM float32 vectors for one source (decoded from the on-disk format)."""

    def __init__(self, dim: int):
        # Capacity-doubling buffers: appending during indexing must not copy a 1 GB matrix per search.
        self.n = 0
        self._ids = np.zeros(1024, np.int64)
        self._ts = np.zeros(1024, np.float64)
        self._vecs = np.zeros((1024, dim), np.float32)
        self.pending: list[tuple[int, np.ndarray, float]] = []
        self.removed: set[int] = set()

    @property
    def ids(self) -> np.ndarray:
        return self._ids[: self.n]

    @property
    def vecs(self) -> np.ndarray:
        return self._vecs[: self.n]

    @property
    def ts(self) -> np.ndarray:
        return self._ts[: self.n]

    def set(self, ids: np.ndarray, vecs: np.ndarray, ts: np.ndarray) -> None:
        self.n = len(ids)
        cap = max(1024, self.n * 2)
        self._ids = np.zeros(cap, np.int64)
        self._ts = np.zeros(cap, np.float64)
        self._vecs = np.zeros((cap, vecs.shape[1] if self.n else self._vecs.shape[1]), np.float32)
        self._ids[: self.n], self._vecs[: self.n], self._ts[: self.n] = ids, vecs, ts

    def _flush(self) -> None:
        if self.removed:
            keep = ~np.isin(self.ids, list(self.removed))
            k = int(keep.sum())
            self._ids[:k], self._vecs[:k], self._ts[:k] = self.ids[keep], self.vecs[keep], self.ts[keep]
            self.n = k
            # An item can be added and replaced again before a flush; drop those too.
            self.pending = [p for p in self.pending if p[0] not in self.removed]
            self.removed.clear()
        if self.pending:
            m = self.n + len(self.pending)
            if m > len(self._ids):
                cap = max(m, len(self._ids) * 2)
                self._ids = np.resize(self._ids, cap)
                self._ts = np.resize(self._ts, cap)
                grown = np.zeros((cap, self._vecs.shape[1]), np.float32)
                grown[: self.n] = self.vecs
                self._vecs = grown
            ids, vecs, ts = zip(*self.pending)
            self._ids[self.n:m] = ids
            self._ts[self.n:m] = ts
            self._vecs[self.n:m] = np.vstack(vecs)
            self.n = m
            self.pending.clear()

    def scores(self, q: np.ndarray, window: tuple[float, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
        self._flush()
        if not self.n:
            return self.ids, np.zeros(0, np.float32)
        s = self.vecs @ q
        if window is not None and self.ts.any():  # sources without dates (settings, emoji) are never filtered
            ts = self.ts
            s = np.where((ts >= window[0]) & (ts < window[1]), s, s - 2.0)
        return self.ids, s


def decode(raw: bytes, scale: float | None, dtype: str) -> np.ndarray:
    if dtype == "float16":
        return np.frombuffer(raw, np.float16).astype(np.float32)
    return np.frombuffer(raw, np.int8).astype(np.float32) * (scale or 1.0)


def encode(v: np.ndarray, dtype: str) -> tuple[np.ndarray, float]:
    if dtype == "float16":
        return v.astype(np.float16), 1.0
    scale = float(np.abs(v).max()) / 127.0 or 1.0
    return np.clip(np.round(v / scale), -127, 127).astype(np.int8), scale


def _like_prefix(folder: str) -> str:
    """LIKE pattern (ESCAPE '\\') matching every path inside `folder`."""
    esc = folder.rstrip("\\/").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc + "\\\\%"


class Store:
    def __init__(self, path: str | None = None):
        path = path or config.DB_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.dim = config.get("embedding_dim")
        self.dtype = config.get("vector_dtype")
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.create_function("ss_name_words", 1, name_words, deterministic=True)  # used by the fts triggers
        self._db.executescript(SCHEMA)
        self._lock = threading.RLock()
        self._mats: dict[str, _Matrix] = {}
        self._local = threading.local()  # per-request date window and folder scope (see `window`, `scope`)
        self._under_cache: dict[tuple[str, str], tuple[int, np.ndarray]] = {}
        self._stat_cache: tuple[str, float] = ("", 0.0)
        self._df_cache: dict[str, tuple[float, int]] = {}  # term -> (when, documents containing it)
        self._check_format()
        self._migrate()
        self._migrate_fts()
        self._load()

    # ---- setup ---------------------------------------------------------------------
    def _check_format(self) -> None:
        """If dim/dtype changed since the index was built, start the index over."""
        fmt = f"{self.dim}/{self.dtype}"
        row = self._db.execute("SELECT v FROM kv WHERE k='format'").fetchone()
        if row and row[0] != fmt:
            self._db.execute("DELETE FROM items")
            self._db.execute("DELETE FROM files")
        self._db.execute("INSERT OR REPLACE INTO kv VALUES ('format', ?)", (fmt,))
        self._db.commit()

    def _migrate(self) -> None:
        """Add the ts column to older indexes and fill it from what is already known."""
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(items)")}
        if "ts" in cols:
            return
        self._db.execute("ALTER TABLE items ADD COLUMN ts REAL")
        self._db.execute("""UPDATE items SET ts = (SELECT f.mtime FROM files f
                            WHERE f.path = items.path AND f.source = items.source)""")
        try:  # needs SQLite's JSON functions (present in Python's bundled SQLite)
            self._db.execute("""UPDATE items SET ts = CAST(strftime('%s', json_extract(meta, '$.taken')) AS REAL)
                                WHERE source = 'images' AND json_extract(meta, '$.taken') IS NOT NULL""")
            self._db.execute("""UPDATE items SET ts = CAST(strftime('%s', json_extract(meta, '$.last_visit')) AS REAL)
                                WHERE source = 'browser_history'""")
        except sqlite3.OperationalError:
            pass
        self._db.execute("UPDATE items SET ts = NULL WHERE source IN ('shell_history', 'actions', 'emoji')")
        self._db.commit()

    def _migrate_fts(self) -> None:
        """Create the keyword index once and fill it from the items already stored (~1 min for 240k chunks)."""
        if (self._db.execute("SELECT 1 FROM sqlite_master WHERE name='fts'").fetchone()
                and self.kv_get("fts_version") == FTS_VERSION):
            return
        t = time.perf_counter()
        self._db.executescript("DROP TRIGGER IF EXISTS fts_ai; DROP TRIGGER IF EXISTS fts_ad; "
                               "DROP TRIGGER IF EXISTS fts_au; DROP TABLE IF EXISTS fts;")
        self._db.executescript(FTS_SCHEMA)
        self._db.execute(f"INSERT INTO fts(rowid, title, body) SELECT id, ss_name_words(title), snippet FROM items "
                         f"WHERE source IN ({_IN})")
        self._db.commit()
        self.kv_set("fts_version", FTS_VERSION)
        import logging
        logging.getLogger(__name__).info("keyword index built in %.0f s", time.perf_counter() - t)

    def lexical(self, text: str, limit: int = 200) -> dict[int, tuple[int, float, float]]:
        """BM25 over document text: {item id: (rank, coverage, matched_idf)} for the best `limit` items.

        coverage     share of the query's information (sum of its words' IDF) that the item contains: rare
                     words count, words in most documents ("the", "notes" in a notes folder) barely do
        matched_idf  that information in nats, so an item that only shares very common words can be told apart
        """
        terms = query_terms(text)
        if not terms:
            return {}
        match = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
        with self._lock:
            try:
                rows = self._db.execute("SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY bm25(fts, 2.0, 1.0) "
                                        "LIMIT ?", (match, limit)).fetchall()
            except sqlite3.OperationalError:  # e.g. a query of only punctuation
                return {}
            if not rows:
                return {}
            ids = [r[0] for r in rows]
            idf = self.term_idf(text)
            total = sum(idf.values()) or 1.0
            got = {i: 0.0 for i in ids}
            marks = ",".join("?" * len(ids))
            for t in terms:
                for (i,) in self._db.execute(f"SELECT rowid FROM fts WHERE fts MATCH ? AND rowid IN ({marks})",
                                             ('"' + t.replace('"', "") + '"', *ids)):
                    got[i] += idf[t]
        return {i: (rank, got[i] / total, got[i]) for rank, i in enumerate(ids)}

    def term_idf(self, text: str) -> dict[str, float]:
        """Each query word's information in nats, ln(1 + (N - df + 0.5) / (df + 0.5)) over document chunks:
        ~0.1 for "the", ~9 for a rare name. Used to weigh keyword matches, pick snippets and highlight words."""
        terms = query_terms(text)
        if not terms:
            return {}
        with self._lock:
            n = self._fts_df("")
            return {t: float(np.log1p((n - (df := self._fts_df(t)) + 0.5) / (df + 0.5))) for t in terms}

    def _fts_df(self, term: str) -> int:
        """How many indexed chunks contain `term` ("" = all of them), cached for 10 minutes."""
        hit = self._df_cache.get(term)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        if term:
            n = self._db.execute("SELECT count(*) FROM fts WHERE fts MATCH ?",
                                 ('"' + term.replace('"', "") + '"',)).fetchone()[0]
        else:
            n = self._db.execute(f"SELECT count(*) FROM items WHERE source IN ({_IN})").fetchone()[0]
        if len(self._df_cache) > 5000:
            self._df_cache.clear()
        self._df_cache[term] = (time.time(), n)
        return n

    def _mat(self, source: str) -> _Matrix:
        if source not in self._mats:
            self._mats[source] = _Matrix(self.dim)
        return self._mats[source]

    def _load(self) -> None:
        for source, in self._db.execute("SELECT DISTINCT source FROM items").fetchall():
            rows = self._db.execute("SELECT id, vec, scale, ts FROM items WHERE source=?", (source,)).fetchall()
            if rows:
                self._mat(source).set(np.array([r[0] for r in rows], np.int64),
                                      np.vstack([decode(r[1], r[2], self.dtype) for r in rows]),
                                      np.array([r[3] or 0.0 for r in rows], np.float64))

    # ---- writes --------------------------------------------------------------------
    def add(self, source: str, key: str, vec: np.ndarray, *, path: str | None = None, title: str = "",
            subtitle: str = "", snippet: str = "", meta: dict | None = None, commit: bool = True,
            ts: float | None = None) -> int:
        """`ts` is when the thing happened; it defaults to the file's modified time when `path` is given.
        Pass ts=0 for items that have no meaningful date."""
        enc, scale = encode(vec[: self.dim], self.dtype)
        if ts is None and path:
            ts = self._mtime(path)
        with self._lock:
            old = self._db.execute("SELECT id FROM items WHERE key=?", (key,)).fetchone()
            if old:
                self._db.execute("DELETE FROM items WHERE id=?", (old[0],))
                self._mat(source).removed.add(old[0])
            cur = self._db.execute(
                "INSERT INTO items (source,key,path,title,subtitle,snippet,meta,vec,scale,ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (source, key, path, title, subtitle, snippet, json.dumps(meta or {}), enc.tobytes(), scale, ts or None))
            self._mat(source).pending.append((cur.lastrowid, decode(enc.tobytes(), scale, self.dtype), ts or 0.0))
            if commit:
                self._db.commit()
            return cur.lastrowid

    def _mtime(self, path: str) -> float:
        if self._stat_cache[0] != path:  # chunks of one file arrive together: stat once
            try:
                self._stat_cache = (path, os.path.getmtime(path))
            except OSError:
                self._stat_cache = (path, 0.0)
        return self._stat_cache[1]

    def remove_path(self, source: str, path: str, commit: bool = True) -> None:
        with self._lock:
            ids = [r[0] for r in self._db.execute(
                "SELECT id FROM items WHERE source=? AND path=?", (source, path))]
            self._db.execute("DELETE FROM items WHERE source=? AND path=?", (source, path))
            self._db.execute("DELETE FROM files WHERE source=? AND path=?", (source, path))
            self._mat(source).removed.update(ids)
            if commit:
                self._db.commit()

    def remove_under(self, path: str, folder: bool = True) -> int:
        """Forget a deleted file, or (`folder`) everything under a deleted folder, in every source.
        Returns items dropped. The folder form scans every row, so callers use it only when needed."""
        with self._lock:
            if folder:
                where, args = "path=? OR path LIKE ? ESCAPE '\\'", (path, _like_prefix(path))
            else:
                where, args = "path=?", (path,)
            rows = self._db.execute(f"SELECT id, source FROM items WHERE {where}", args).fetchall()
            self._db.execute(f"DELETE FROM items WHERE {where}", args)
            self._db.execute(f"DELETE FROM files WHERE {where}", args)
            for i, src in rows:
                self._mat(src).removed.add(i)
            self._db.commit()
            return len(rows)

    def move_path(self, old: str, new: str) -> int:
        """A file was renamed or moved: keep its vectors, just point them at the new path.
        Returns how many items moved (0 when the old path was never indexed)."""
        with self._lock:
            rows = self._db.execute("SELECT id, key FROM items WHERE path=?", (old,)).fetchall()
            if not rows and not self._db.execute("SELECT 1 FROM files WHERE path=?", (old,)).fetchone():
                return 0
            gone = self._db.execute("SELECT id, source FROM items WHERE path=?", (new,)).fetchall()
            self._db.execute("DELETE FROM items WHERE path=?", (new,))  # moved over an existing file
            self._db.execute("DELETE FROM files WHERE path=?", (new,))
            for i, src in gone:
                self._mat(src).removed.add(i)
            title, subtitle = os.path.basename(new), os.path.dirname(new)
            self._db.executemany("UPDATE items SET path=?, key=?, title=?, subtitle=? WHERE id=?",
                                 [(new, key.replace(old, new, 1), title, subtitle, i) for i, key in rows])
            self._db.execute("UPDATE files SET path=? WHERE path=?", (new, old))
            self._db.commit()
            return len(rows)

    def set_snippets(self, rows: list[tuple[str, str]]) -> None:
        """[(text, item key)]: replace stored text (the keyword index follows through its trigger)."""
        with self._lock:
            # unchanged text is skipped, so the index isn't rewritten for chunks that were already whole
            self._db.executemany("UPDATE items SET snippet=? WHERE key=? AND snippet IS NOT ?",
                                 [(text, key, text) for text, key in rows])
            self._db.commit()

    def file_row(self, source: str, path: str) -> tuple[float, int] | None:
        """(mtime, size) recorded for one file, or None if it was never indexed."""
        with self._lock:
            r = self._db.execute("SELECT mtime, size FROM files WHERE source=? AND path=?", (source, path)).fetchone()
            return (r[0], r[1]) if r else None

    def clear_source(self, source: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM items WHERE source=?", (source,))
            self._db.execute("DELETE FROM files WHERE source=?", (source,))
            self._db.commit()
            self._mats.pop(source, None)

    def commit(self) -> None:
        with self._lock:
            self._db.commit()

    # ---- file bookkeeping for incremental indexing ------------------------------------
    def file_state(self, source: str) -> dict[str, tuple[float, int]]:
        with self._lock:
            return {p: (m, s) for p, m, s in self._db.execute(
                "SELECT path, mtime, size FROM files WHERE source=?", (source,))}

    def file_status(self, source: str) -> dict[str, str]:
        with self._lock:
            return dict(self._db.execute("SELECT path, status FROM files WHERE source=?", (source,)))

    def mark_file(self, source: str, path: str, mtime: float, size: int, status: str = "ok") -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)", (path, source, mtime, size, status))

    # ---- reads ---------------------------------------------------------------------
    @contextmanager
    def window(self, start: float | None, end: float | None, exts: frozenset = frozenset(), by_date: float = 0.0):
        """Within this block (on this thread) every source's scores are shaped for a dated query:
          - dated items outside [start, end) score 2 lower, so floors drop them while the top-k is
            still taken from inside the window;
          - with `exts`, file items of other types score 2 lower (undated sources are untouched);
          - `by_date` adds up to that much for being newer within the window, so the top-k of a
            browse query ("pdfs from last week") is the newest items, not the most pdf-sounding ones."""
        win = (start or 0.0, end or time.time() + 86400) if (start or end or by_date) else None
        self._local.opts = (win, exts, by_date)
        try:
            yield
        finally:
            self._local.opts = None

    @contextmanager
    def scope(self, folder: str | None):
        """Within this block (on this thread) only items from files inside `folder` score normally;
        everything else scores 2 lower, so floors drop it ("search this folder" from File Explorer)."""
        self._local.under = os.path.normpath(folder) if folder else None
        try:
            yield
        finally:
            self._local.under = None

    def scope_folder(self) -> str | None:
        return getattr(self._local, "under", None)

    def ids_under(self, source: str, folder: str) -> np.ndarray:
        """Item ids of `source` whose file is inside `folder` (cached until the source changes)."""
        with self._lock:
            mat = self._mat(source)
            version = mat.n + len(mat.pending) + len(mat.removed) * 1_000_003
            hit = self._under_cache.get((source, folder))
            if hit and hit[0] == version:
                return hit[1]
            ids = np.array([r[0] for r in self._db.execute(
                "SELECT id FROM items WHERE source=? AND path LIKE ? ESCAPE '\\'", (source, _like_prefix(folder)))],
                np.int64)
            if len(self._under_cache) > 64:
                self._under_cache.clear()
            self._under_cache[(source, folder)] = (version, ids)
            return ids

    def size_in_scope(self, source: str) -> int:
        """Items the current request can see: the folder's when scoped, else the whole source."""
        under = self.scope_folder()
        return len(self.ids_under(source, under)) if under else self.size(source)

    def scores(self, source: str, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """All (ids, scores) for a source. q must be a normalised float32 vector of length dim."""
        opts = getattr(self._local, "opts", None)
        under = self.scope_folder()
        with self._lock:
            mat = self._mat(source)
            ids, s = mat.scores(q[: self.dim].astype(np.float32), opts[0] if opts else None)
            if under and len(ids):
                s = np.where(np.isin(ids, self.ids_under(source, under)), s, s - 2.0)
            if not opts or not len(ids):
                return ids, s
            win, exts, by_date = opts
            if exts and mat.ts.any():
                s = np.where(self._has_ext(source, ids, exts), s, s - 2.0)
            if by_date and win is not None and mat.ts.any():
                s = s + self._bonus(mat, win, by_date, mat.ts)
            return ids, s

    @staticmethod
    def _bonus(mat: _Matrix, win: tuple[float, float], by_date: float, ts) -> np.ndarray:
        lo = max(win[0], float(mat.ts[mat.ts > 0].min()))  # "since forever" starts at the oldest item
        return by_date * np.clip((np.asarray(ts, np.float64) - lo) / max(win[1] - lo, 1.0), 0.0, 1.0)

    def date_bonus(self, source: str, ts: float) -> float:
        """What `scores` added to an item with this timestamp in the current `window` block (0 if none)."""
        opts = getattr(self._local, "opts", None)
        if not opts or not opts[2] or opts[0] is None or not ts:
            return 0.0
        with self._lock:
            mat = self._mat(source)
            return float(self._bonus(mat, opts[0], opts[2], ts)) if mat.ts.any() else 0.0

    def _has_ext(self, source: str, ids: np.ndarray, exts: frozenset) -> np.ndarray:
        like = " OR ".join("lower(path) LIKE ?" for _ in exts)
        ok = [r[0] for r in self._db.execute(f"SELECT id FROM items WHERE source=? AND ({like})",
                                             (source, *[f"%{e}" for e in sorted(exts)]))]
        return np.isin(ids, np.array(ok, np.int64))

    def timestamps(self, ids: list[int]) -> dict[int, float]:
        if not ids:
            return {}
        with self._lock:
            q = f"SELECT id, ts FROM items WHERE ts IS NOT NULL AND id IN ({','.join('?' * len(ids))})"
            return dict(self._db.execute(q, ids).fetchall())

    def search(self, source: str, q: np.ndarray, k: int = 20) -> list[tuple[Item, float]]:
        ids, s = self.scores(source, q)
        if not len(ids):
            return []
        top = np.argsort(-s)[:k]
        return [(it, float(s[i])) for i in top if (it := self.get(int(ids[i]))) is not None]

    def get(self, item_id: int) -> Item | None:
        with self._lock:
            r = self._db.execute(
                "SELECT id,source,key,path,title,subtitle,snippet,meta FROM items WHERE id=?", (item_id,)).fetchone()
        if not r:
            return None
        return Item(r[0], r[1], r[2], r[3], r[4] or "", r[5] or "", r[6] or "", json.loads(r[7] or "{}"))

    def vector(self, item_id: int) -> np.ndarray | None:
        """Decoded float32 vector for an item (used for 'find similar')."""
        with self._lock:
            r = self._db.execute("SELECT vec, scale FROM items WHERE id=?", (item_id,)).fetchone()
        if not r:
            return None
        v = decode(r[0], r[1], self.dtype)
        return v / (np.linalg.norm(v) or 1.0)

    def size(self, source: str) -> int:
        with self._lock:
            m = self._mats.get(source)
            return (m.n + len(m.pending) - len(m.removed)) if m else 0

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._db.execute("SELECT source, COUNT(*) FROM items GROUP BY source").fetchall())

    def kv_get(self, k: str) -> str | None:
        with self._lock:
            r = self._db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return r[0] if r else None

    def kv_set(self, k: str, v: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, v))
            self._db.commit()
