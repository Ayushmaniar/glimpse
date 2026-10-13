"""Search orchestration: sources, background indexing, and section ranking.

How a query becomes sections:
  1. The query is embedded once (EmbeddingGemma 2, "search result" prefix).
  2. Every enabled source ranks its own items against that vector.
  3. Each section gets two signals:
       intent     how well the query matches the source's description
                  ("photos of ..." -> Photos, "wifi broken" -> Settings & fixes)
       relevance  how far its best hit clears that source's noise floor.
                  Image-text, audio-text and text-text similarities live on different
                  scales, so each source declares its own `floor` and `strong` scores.
  4. Sections with nothing above the floor are hidden; the rest are ordered by
     0.5 * intent + 0.5 * relevance.

Time: date words ("last week", "in March", "latest") are taken out of the query before it is
embedded (timeq.py) and become a window on item timestamps. Inside a section, results that clear
the floor are ordered by similarity with a small recency tiebreak; "latest" and browse queries
("photos from yesterday") are ordered newest first. Floors always apply to the raw similarity, so
recency can reorder results but never lift noise into view.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import threading
import time
from typing import Iterable

import numpy as np

from . import config, embedder, timeq
from .sources.base import Result, Source
from .store import Store

log = logging.getLogger(__name__)

# Sources whose items have dates (photo taken / file modified / page visited).
TIMED = {"files", "scans", "images", "audio", "video", "browser_history"}
IN_FOLDERS = {"files", "scans", "images", "audio", "video", "names"}  # sources whose results are files

# Looking for a saved key: "gemini api key", "my aws secret", "database password". A file result whose snippet
# holds a detected secret (meta.secret, shown masked) is then the answer even if its text scores a little lower,
# and the documents section leads. Naming a provider limits this to that provider's keys.
KEY_WORDS = re.compile(r"(?i)\b(?:api[\s_-]?keys?|tokens?|secrets?|passwords?|passwd|credentials?|creds)\b")
BARE_KEY = re.compile(r"(?i)\bkeys?\b")  # "openai key" counts, "key points" doesn't: needs a provider word
KEY_PROVIDERS = {
    "gemini": ("gemini", "google", "aiza"), "google": ("gemini", "google", "aiza"),
    "openai": ("openai", "sk-"), "chatgpt": ("openai", "sk-"), "anthropic": ("anthropic", "claude", "sk-ant"),
    "claude": ("anthropic", "claude", "sk-ant"), "aws": ("aws", "akia", "asia"), "amazon": ("aws", "akia"),
    "github": ("github", "ghp_", "gho_", "github_pat"), "huggingface": ("hf_", "hugging"), "hf": ("hf_", "hugging"),
    "stripe": ("stripe", "sk_live", "sk_test", "rk_"), "slack": ("slack", "xox"),
}
KEY_SLACK = 0.08  # how far under the floor a matching key file may score
KEY_REL = 0.8     # relevance given to the documents section when it holds the key asked for


# Keyword evidence (BM25 index, see Store.lexical): a document that contains the query's information is shown even
# when its embedding score is under the floor. The floor rises with index size because nonsense queries find
# higher near-misses in a bigger index; it was cutting real matches (a two-word query whose exact document scored 0.795
# under a 0.807 floor). Nonsense and absent topics have no documents containing their rare words, so they still
# show nothing. Information is IDF-weighted, so no stop-word lists: "the" carries ~0.1 nats, "contoso" ~9.
LEX_COVER = 0.8   # share of the query's information the document must contain
LEX_INFO = 2.3    # ...and at least this much of it, in nats (ln 10: words rarer than 1 document in 10 together)
HIGHLIGHT_IDF = 1.5  # mark a query word in results if it is in fewer than ~1 in 5 document chunks


def _lexical_ok(r) -> bool:
    return getattr(r, "lexical", 0.0) >= LEX_COVER and getattr(r, "lexical_info", 0.0) >= LEX_INFO


def _key_match(text: str | None, source: str):
    """Predicate over results: is this the saved key the query asks for? (always False for other queries)"""
    if source != "files" or not text:
        return lambda r: False
    hints = [h for w in re.findall(r"[a-z]+", text.lower()) for h in KEY_PROVIDERS.get(w, ())]
    if not KEY_WORDS.search(text) and not (hints and BARE_KEY.search(text)):
        return lambda r: False
    return lambda r: bool(r.meta.get("secret")) and (not hints or any(h in (r.snippet or "").lower() for h in hints))
DAY = 86400.0
TIEBREAK, TIEBREAK_HALF_LIFE = 0.015, 180 * DAY  # a near-tie goes to the newer item
BROWSE_SLACK = 0.08  # "photos from last week": show what's there, not only strong matches
BROWSE_BY_DATE = 0.10  # ...newer first, but a screenshot still beats a photo for "screenshots"


def _load_sources(store: Store) -> list[Source]:
    """Import every known source; a source that fails to import is skipped, not fatal."""
    import importlib

    out = []
    for mod, cls in [
        ("actions", "ActionsSource"), ("files", "FilesSource"), ("scans", "ScansSource"), ("images", "ImagesSource"),
        ("audio", "AudioSource"), ("video", "VideoSource"), ("windows", "WindowsSource"),
        ("emoji", "EmojiSource"), ("names", "NamesSource"), ("shell_history", "ShellHistorySource"),
        ("browser_history", "BrowserHistorySource"),
    ]:
        try:
            m = importlib.import_module(f".sources.{mod}", __package__)
            out.append(getattr(m, cls)(store))
        except Exception as e:  # missing optional source or dependency
            log.warning("source %s unavailable: %s", mod, e)
    return out


class Engine:
    def __init__(self):
        self.store = Store()
        self.sources: dict[str, Source] = {s.name: s for s in _load_sources(self.store)}
        self.emb: embedder.Embedder | None = None
        self.ready = threading.Event()
        self.status = {"state": "starting", "message": "Loading EmbeddingGemma 2…", "done": 0, "total": 0}
        self._intent_vecs: dict[str, np.ndarray] = {}
        self._index_lock = threading.Lock()
        self._reindex = threading.Event()
        self.watcher = None  # fresh.Watcher once the model is up
        self._diag = threading.local()

    # ---- lifecycle -----------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._boot, name="boot", daemon=True).start()

    def _ensure_model(self) -> None:
        """First start: fetch the embedding model (~485 MB, Apache-2.0, no account needed) into models/."""
        if os.path.exists(config.MODEL_PATH):
            return
        from huggingface_hub import hf_hub_download
        repo, name = config.MODEL_REPO, os.path.basename(config.MODEL_PATH)
        self.status = {"state": "loading", "message": "Downloading the search model (485 MB, first start only)…",
                       "done": 0, "total": 0}
        log.info("downloading %s from %s", name, repo)
        hf_hub_download(repo, name, local_dir=os.path.dirname(config.MODEL_PATH))

    def _boot(self) -> None:
        try:
            self._ensure_model()
            self.emb = embedder.get()
        except Exception as e:
            log.exception("embedder failed")
            self.status = {"state": "error", "message": f"Model failed to load: {e}", "done": 0, "total": 0}
            return
        self._intent_vecs = {
            n: self.emb.text(embedder.doc(s.intent, s.label)) for n, s in self.sources.items() if s.intent}
        self.ready.set()
        try:  # new files searchable in seconds, even while the first full index is still running
            from .fresh import Watcher
            self.watcher = Watcher(self)
            self.watcher.start()
        except Exception:
            log.exception("folder watcher unavailable; new files wait for the periodic scan")
            self.watcher = None
        self._index_loop()

    def _index_loop(self) -> None:
        while True:
            self.index_all()
            # With the watcher this scan is only a safety net (events missed while the app was closed,
            # buffer overflows, network drives); without it, it is how new files are found.
            minutes = config.get("rescan_minutes") if self.watcher else 15
            self._reindex.wait(timeout=minutes * 60)
            self._reindex.clear()

    def request_reindex(self) -> None:
        if self.watcher:
            try:
                self.watcher.refresh()  # folders or sources may have changed
            except Exception:
                log.exception("watcher refresh failed")
        self._reindex.set()

    def idle_message(self) -> str:
        w = self.watcher.status() if self.watcher else ""
        return "Index up to date" + (f" · {w}" if w else "")

    def index_all(self) -> None:
        """Two passes: files changed in the last `recent_first_days` across every source, then the rest.

        A first full index takes about a day on this laptop; the first pass makes recent
        documents, photos, recordings and videos searchable within the first hour or so.
        """
        from .sources import walk
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        with self._index_lock:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)  # no sleep mid-index
            try:
                days = config.get("recent_first_days")
                passes = [time.time() - days * 86400, None] if days else [None]
                for n, since in enumerate(passes):
                    walk.RECENT_SINCE = since
                    tag = f"recent {days} days · " if since else ("older files · " if n else "")
                    # Small catalogs (settings, emoji, history) first: minutes, not hours of files ahead of them.
                    for s in sorted(self.enabled(), key=lambda s: s.name in TIMED and s.name != "browser_history"):
                        if not s.indexed:
                            continue
                        def progress(msg: str, done: int, total: int, _s=s, _t=tag) -> None:
                            self.status = {"state": "indexing", "message": f"{_t}{_s.label}: {msg}",
                                           "done": done, "total": total}
                        try:
                            progress("checking…", 0, 0)
                            s.index(self.emb, progress)
                            self.store.commit()
                        except Exception:
                            log.exception("indexing %s failed", s.name)
            finally:
                walk.RECENT_SINCE = None
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            self.status = {"state": "idle", "message": "Index up to date", "done": 0, "total": 0}

    def warm(self) -> None:
        """Prefetch slow live data (status widgets) in the background, e.g. when the window opens."""
        for s in self.enabled():
            if hasattr(s, "warm"):
                threading.Thread(target=s.warm, name=f"warm-{s.name}", daemon=True).start()

    def enabled(self) -> list[Source]:
        return [s for s in self.sources.values() if config.source_enabled(s.name)]

    # ---- search --------------------------------------------------------------------
    def search_text(self, q: str, only: Iterable[str] | None = None, k: int = 8, literal: bool = False,
                    scope: str | None = None, debug: bool = False) -> dict:
        """`literal`: the user dismissed the date/type reading of their words ("march madness").
        `scope`: a folder (from File Explorer); only files inside it are searched."""
        self._diag.full = debug  # also report each source's whole score distribution for this query
        with self.store.scope(scope):
            return self._search_text(q, only, k, literal)

    def _search_text(self, q: str, only, k: int, literal: bool) -> dict:
        self.ready.wait()
        t = time.perf_counter()
        tq = timeq.TimeQuery(q.strip()) if literal else timeq.parse(q)
        text = tq.text or q
        qvec = self.emb.query(text)
        if tq.mode == "none" and not tq.exts:
            sections = self._run(qvec, text, only, k, use_intent=True)
        else:
            now = time.time()
            # "latest ...": look in the last month first, widen if nothing there.
            windows = [(tq.start, tq.end)] if tq.mode == "range" else \
                [(now - 30 * DAY, None), (now - 365 * DAY, None), (None, None)] if tq.mode == "latest" else [(None, None)]
            # Browse: rank the window by date (all of it if nothing but time/type words were said).
            by_date = (10.0 if not tq.text or tq.exts else BROWSE_BY_DATE) if tq.browse or tq.mode == "latest" else 0.0
            for start, end in windows:
                with self.store.window(start, end, tq.exts, by_date):
                    sections = self._run(qvec, text, only, k, use_intent=True, tq=tq)
                if sections:
                    break
        return {"query": q, "ms": round((time.perf_counter() - t) * 1000), "sections": sections,
                "time": tq.to_json(), "terms": self._highlight_terms(text),
                "diag": getattr(self._diag, "rows", [])}

    def _highlight_terms(self, text: str) -> list[str]:
        """Query words worth marking in results: the informative ones (no stop-word list; "the" and "of" are in
        most documents, so they carry almost no information and stay unmarked)."""
        try:
            idf = self.store.term_idf(text)
        except Exception:
            return []
        return [w for w, v in idf.items() if v >= HIGHLIGHT_IDF and len(w) >= 2]

    def search_vector(self, qvec: np.ndarray, label: str, only: Iterable[str] | None = None, k: int = 12,
                      exclude: str | None = None) -> dict:
        """Image query or 'find similar': no intent, only sources that make sense for images."""
        self.ready.wait()
        t = time.perf_counter()
        srcs = [n for n, s in self.sources.items() if s.supports_image_query]
        if only:
            srcs = [n for n in srcs if n in set(only)]
        sections = self._run(qvec, None, srcs, k, use_intent=False, exclude=exclude)
        return {"query": label, "ms": round((time.perf_counter() - t) * 1000), "sections": sections}

    def search_image(self, data: bytes, only: Iterable[str] | None = None, scope: str | None = None) -> dict:
        self.ready.wait()
        t = time.perf_counter()
        with self.store.scope(scope):
            res = self.search_vector(self.emb.image(data, urgent=True), "📷 image", only)
        res["ms"] = round((time.perf_counter() - t) * 1000)
        return res

    def similar(self, result_id: str) -> dict:
        self.ready.wait()
        src, _, item_id = result_id.partition(":")
        v = self.store.vector(int(item_id))
        if v is None:
            return {"query": "similar", "ms": 0, "sections": []}
        it = self.store.get(int(item_id))
        return self.search_vector(v, f"≈ {it.title if it else 'similar'}", exclude=result_id)

    def _run(self, qvec: np.ndarray, text: str | None, only, k: int, use_intent: bool,
             exclude: str | None = None, tq: timeq.TimeQuery | None = None) -> list[dict]:
        names = [s.name for s in self.enabled() if not only or s.name in set(only)]
        if self.store.scope_folder():  # searching one folder: only things that are files
            names = [n for n in names if n in IN_FOLDERS]
        timed = tq is not None and (tq.mode != "none" or tq.browse)
        if timed:  # settings, emoji and windows have no dates
            names = [n for n in names if n in TIMED]
        # "latest pdf", "python scripts from last week": only documents. ("pdf printer not working" keeps
        # Settings: there the type only filters the documents section.)
        if timed and tq.exts:
            names = [n for n in names if n == "files" or (n == "scans" and ".pdf" in tq.exts)]
        browse = timed and tq.browse
        intent = self._intent(qvec, names) if use_intent and text and not (browse and not tq.text) else {}
        sections = []
        self._diag.rows = []  # per source: floor used, best score, kept/cut (Ctrl+D in the UI)
        for n in names:
            s = self.sources[n]
            try:
                results: list[Result] = s.search(self.emb, qvec, text, k)
            except Exception:
                log.exception("search in %s failed", n)
                continue
            if exclude:
                results = [r for r in results if r.id != exclude]
            floor, strong = getattr(s, "floor", 0.5), getattr(s, "strong", 0.75)
            if not text:  # image queries score lower across the board
                floor, strong = getattr(s, "image_floor", floor - 0.05), getattr(s, "image_strong", strong - 0.05)
            if s.floor_ref_n:  # bigger index, higher best-unrelated score: lift the band with log(size)
                # (searching one folder: its own item count, so a small folder isn't held to a 50k-item bar)
                lift = s.floor_per_decade * max(0.0, np.log10(max(self.store.size_in_scope(n), 1) / s.floor_ref_n))
                floor, strong = floor + lift, strong + lift
            if n in TIMED:
                self._attach_dates(n, results)  # also takes the store's date bonus back out of the scores
            # Keep results above the floor and close to the section's best hit, so a strong
            # match isn't padded with noise.
            top = max((r.score for r in results), default=0.0)
            keep = getattr(s, "keep_within", 0.10)
            if browse:
                if intent and intent.get(n, 0.0) < 0.15:  # "screenshots yesterday": just the photo section
                    continue
                # Nothing but time/type words ("recent", "pdfs from yesterday"): everything that fits qualifies.
                floor, keep = (floor - BROWSE_SLACK if tq.text and not tq.exts else -1.0), 9.0
            wants_key = _key_match(text, n)  # "gemini api key": a file with a (masked) Gemini key is the answer
            pre = [[r.id, round(r.score, 4), round(getattr(r, "lexical", 0.0), 3), round(getattr(r, "lexical_info", 0.0), 2)]
                   for r in results]
            n_before = len(results)
            results = [r for r in results if r.score >= max(floor, top - keep)
                       or (wants_key(r) and r.score >= floor - KEY_SLACK)
                       or (text and not browse and _lexical_ok(r))]
            self._diag.rows.append({"source": n, "floor": round(floor, 3), "strong": round(strong, 3),
                                    "top": round(top, 3), "keep": keep, "kept": len(results),
                                    "cut": n_before - len(results),
                                    "in_scope": self.store.size_in_scope(n) if s.floor_ref_n else None})
            if getattr(self._diag, "full", False):
                self._diag.rows[-1]["pre"] = pre
            if getattr(self._diag, "full", False) and s.indexed:
                all_ids, all_s = self.store.scores(n, qvec)
                inside = all_s > -1.0  # inside the scope / time window
                all_ids, all_s = all_ids[inside], all_s[inside]
                if len(all_s):
                    q_ = np.quantile(all_s, [0.5, 0.99, 0.999]) if len(all_s) > 20 else [np.nan] * 3
                    best = np.argsort(-all_s)[:200]
                    self._diag.rows[-1]["dist"] = {
                        "n": int(len(all_s)), "mean": round(float(all_s.mean()), 4), "std": round(float(all_s.std()), 4),
                        "p50": round(float(q_[0]), 4), "p99": round(float(q_[1]), 4), "p999": round(float(q_[2]), 4),
                        "top5": [round(float(x), 4) for x in np.sort(all_s)[-5:][::-1]],
                        "cands": [[int(all_ids[i]), round(float(all_s[i]), 4)] for i in best]}
            if not results:
                continue
            if n in TIMED:
                results = self._order(results, tq if timed else None)
            rel = float(np.clip((max(r.score for r in results) - floor) / max(strong - floor, 1e-6), 0, 1))
            if text and not browse and any(_lexical_ok(r) for r in results):  # as relevant as the words it contains
                rel = max(rel, max(r.lexical for r in results if _lexical_ok(r)))
            if any(wants_key(r) for r in results):
                results = sorted(results, key=lambda r: not wants_key(r))  # stable: keeps the order otherwise
                rel = max(rel, KEY_REL)
            rank = 0.5 * intent.get(n, 0.0) + 0.5 * rel if intent else rel
            widget = None
            if text and rel >= 0.5:  # status cards only for confident matches
                try:
                    widget = s.widget([r.id for r in results[:3]])
                except Exception:
                    log.exception("widget %s failed", n)
            sections.append({
                "source": n, "label": s.label, "panel": s.panel, "rank": round(rank, 3),
                "intent": round(intent.get(n, 0.0), 3), "relevance": round(rel, 3),
                "results": [r.to_json() for r in results], "widget": widget,
            })
        sections.sort(key=lambda x: -x["rank"])
        return sections

    def _attach_dates(self, source: str, results: list[Result]) -> None:
        """Put each result's date in meta.ts and turn its score back into the raw similarity."""
        ids = {r.id: int(r.id.split(":", 1)[1]) for r in results if r.id.split(":", 1)[1].isdigit()}
        ts = self.store.timestamps(list(ids.values()))
        for r in results:
            t = ts.get(ids.get(r.id))
            if t:
                r.meta = {**r.meta, "ts": t}
            r.score = round(r.score - self.store.date_bonus(source, t or 0.0), 4)

    def _order(self, results: list[Result], tq: timeq.TimeQuery | None) -> list[Result]:
        """Newest first for "latest ..." and browse queries; otherwise similarity with a recency tiebreak."""
        when = lambda r: r.meta.get("ts") or 0.0  # noqa: E731
        if tq is not None and (tq.mode == "latest" or tq.browse):
            return sorted(results, key=lambda r: -when(r))
        if all(hasattr(r, "fused") for r in results):  # hybrid sources already ranked meaning + keywords together
            return sorted(results, key=lambda r: -r.fused)
        now = time.time()
        return sorted(results, key=lambda r: -(r.score + (TIEBREAK * np.exp(-max(0.0, now - when(r)) / TIEBREAK_HALF_LIFE)
                                                          if when(r) else 0.0)))

    def _intent(self, qvec: np.ndarray, names: list[str]) -> dict[str, float]:
        names = [n for n in names if n in self._intent_vecs]
        if not names:
            return {}
        sims = np.array([float(self._intent_vecs[n] @ qvec) for n in names])
        p = np.exp((sims - sims.max()) / 0.03)  # similarities differ by a few hundredths, so a sharp softmax
        p /= p.sum()
        return dict(zip(names, p.tolist()))

    # ---- actions -------------------------------------------------------------------
    def run(self, result_id: str, op: str = "open") -> dict:
        src = result_id.split(":", 1)[0]
        if src not in self.sources:
            return {"ok": False, "error": f"unknown source {src}"}
        return self.sources[src].run(result_id, op)

    def status_json(self) -> dict:
        st = dict(self.status)
        if st.get("state") == "idle" and st.get("message") == "Index up to date":
            st["message"] = self.idle_message()
        return {**st, "backend": self.emb.backend if self.emb else None,
                "counts": self.store.counts(), "ready": self.ready.is_set(),
                "sources": {n: {"label": s.label, "enabled": config.source_enabled(n)}
                            for n, s in self.sources.items()}}
