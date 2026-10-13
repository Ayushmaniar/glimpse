"""Voice notes, recordings, podcasts: searched by what is SAID, with no transcription step.

EmbeddingGemma 2's audio encoder maps speech straight into the shared space. Speech works
well (tested: "voice note about paying bills" finds the right recording); environmental
sound effects are weak (the reference notebook found the right clip first only 24% of the
time on ESC-50), so this source is pitched at speech.

Files are cut into 30 s windows (25 audio tokens/s -> 750 tokens, well inside the 8k
context). Runs at ~22x real time on the GPU.
"""
from __future__ import annotations

import os

from .. import config
from . import media
from .base import Result, Source, pretty_dir
from .walk import sync

EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma", ".amr", ".3gp"}
MIN_BYTES = 40_000      # skip UI blips and short effects
MAX_SECONDS = 3 * 3600  # index at most the first 3 hours of a file


class AudioSource(Source):
    name = "audio"
    label = "Voice notes & recordings"
    panel = "list"
    exts, min_size = EXTS, MIN_BYTES
    intent = "A voice note, recording, meeting, lecture or podcast, found by what someone says in it."
    floor, strong = 0.70, 0.80
    floor_ref_n, floor_per_decade = 20, 0.03  # not measured at scale yet: assumed like photos

    def index(self, emb, progress) -> None:
        sync(self.store, self.name, self.exts, self.handler(emb), progress, min_size=self.min_size, label="recordings")

    def handler(self, emb):
        """handle(path) -> status: index one file (its old items are already removed)."""
        win = float(config.get("audio_window_s"))

        def handle(path: str) -> str:
            dur = media.duration(path)
            if dur < 2:
                return "skipped"
            n = 0
            start = 0.0
            while start < min(dur, MAX_SECONDS):
                wav = media.audio_window(path, start, win)
                if len(wav) > 16000:  # > 0.5 s of audio
                    v = emb.audio(wav)
                    self.store.add(self.name, f"audio:{path}@{start:.0f}", v, path=path,
                                   title=os.path.basename(path), subtitle=os.path.dirname(path),
                                   meta={"t": start, "dur": dur}, commit=False)
                    n += 1
                start += win
            return "ok" if n else "empty"

        return handle

    def search(self, emb, qvec, text, k=8) -> list[Result]:
        out, seen = [], set()
        for it, s in self.store.search(self.name, qvec, k * 6):
            if it.path in seen:
                continue
            seen.add(it.path)
            t = it.meta.get("t", 0.0)
            out.append(Result(id=f"audio:{it.id}", source=self.name, title=it.title, subtitle=pretty_dir(it.subtitle), score=s,
                              icon="🎙️", kind="audio", meta={"t": t, "dur": it.meta.get("dur", 0)},
                              thumb=f"/api/preview/{it.id}",  # waveform, drawn on demand
                              badge=media.fmt_ts(t) if it.meta.get("dur", 0) > 40 else None,
                              snippet=f"Best match around {media.fmt_ts(t)} of {media.fmt_ts(it.meta.get('dur', 0))}",
                              actions=[{"label": "Show in folder", "op": "reveal"}]))
            if len(out) >= k:
                break
        return out

    def run(self, result_id: str, op: str = "open") -> dict:
        if op != "open":
            return super().run(result_id, op)
        it = self.store.get(int(result_id.split(":", 1)[1]))
        if not it:
            return {"ok": False, "error": "item no longer exists"}
        player = media.play_at(it.path, it.meta.get("t", 0.0))
        return {"ok": True, "toast": f"Playing from {media.fmt_ts(it.meta.get('t', 0))} in {player}"}
