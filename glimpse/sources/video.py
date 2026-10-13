"""Videos: jump to the moment that matches a description — what is shown OR what is said.

Visual: one clip per `video_frame_every_s` (default 10 s) window, embedded as 2 frames
(start + middle, 512 px) in a single input, so the vector describes the clip rather than one
still. Long videos widen the window so a file never exceeds MAX_CLIPS clips.
Speech: the soundtrack is embedded in 30 s windows like the audio source.
Results point at a timestamp; opening plays from there (VLC or ffplay).
"""
from __future__ import annotations

import os

from PIL import Image

from .. import config
from . import media
from .base import Result, Source, pretty_dir
from .images import THUMB_PX, thumb_name
from .walk import sync

EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv"}
MIN_BYTES = 300_000
MAX_CLIPS = 360         # ~1 hour at 10 s; longer videos get wider windows
MAX_SPEECH_S = 2 * 3600


class VideoSource(Source):
    name = "video"
    label = "Video moments"
    panel = "grid"
    exts, min_size = EXTS, MIN_BYTES
    intent = "A moment in a video, movie or screen recording, found by what happens on screen or what is said."
    supports_image_query = True
    floor, strong = 0.68, 0.79
    image_floor, image_strong = 0.72, 0.90
    floor_ref_n, floor_per_decade = 50, 0.027  # frames behave like photos

    def index(self, emb, progress) -> None:
        sync(self.store, self.name, self.exts, self.handler(emb), progress, min_size=self.min_size, label="videos")

    def handler(self, emb):
        """handle(path) -> status: index one file (its old items are already removed)."""
        from litert_lm._messages import ImageFile
        os.makedirs(config.THUMB_DIR, exist_ok=True)
        every = float(config.get("video_frame_every_s"))
        win_a = float(config.get("audio_window_s"))

        def handle(path: str) -> str:
            dur = media.duration(path)
            if dur < 1:
                return "skipped"
            window = max(every, dur / MAX_CLIPS)
            tmp, frames = media.frames(path, window / 2)
            n = 0
            try:
                for c in range(0, len(frames), 2):
                    pair = frames[c:c + 2]
                    start = c / 2 * window
                    v = emb.multi_image([ImageFile(f) for f in pair])
                    th = thumb_name(path, f"@{start:.0f}")
                    im = Image.open(pair[0])
                    im.thumbnail((THUMB_PX, THUMB_PX))
                    im.convert("RGB").save(os.path.join(config.THUMB_DIR, th), "JPEG", quality=80)
                    self.store.add(self.name, f"video:{path}@{start:.0f}", v, path=path,
                                   title=os.path.basename(path), subtitle=os.path.dirname(path),
                                   meta={"t": start, "dur": dur, "thumb": th, "kind": "visual"}, commit=False)
                    n += 1
            finally:
                media.cleanup(tmp)
            if media.has_audio(path):
                start = 0.0
                while start < min(dur, MAX_SPEECH_S):
                    wav = media.audio_window(path, start, win_a)
                    if len(wav) > 16000:
                        v = emb.audio(wav)
                        th = thumb_name(path, f"@{int(start // window * window):.0f}")
                        self.store.add(self.name, f"video:{path}@{start:.0f}:speech", v, path=path,
                                       title=os.path.basename(path), subtitle=os.path.dirname(path),
                                       meta={"t": start, "dur": dur, "thumb": th, "kind": "speech"}, commit=False)
                        n += 1
                    start += win_a
            return "ok" if n else "empty"

        return handle

    def search(self, emb, qvec, text, k=8) -> list[Result]:
        out, per_file = [], {}
        for it, s in self.store.search(self.name, qvec, k * 6):
            if per_file.get(it.path, 0) >= 2:  # at most two moments per video
                continue
            per_file[it.path] = per_file.get(it.path, 0) + 1
            m = it.meta
            th = os.path.join(config.THUMB_DIR, m.get("thumb", ""))
            out.append(Result(
                id=f"video:{it.id}", source=self.name, title=it.title, subtitle=pretty_dir(it.subtitle), score=s,
                thumb=f"/api/thumb/{m['thumb']}" if m.get("thumb") and os.path.exists(th) else None,
                icon="🎬", kind="video", meta={"t": m.get("t", 0), "dur": m.get("dur", 0)},
                badge=("🗣 " if m.get("kind") == "speech" else "") + media.fmt_ts(m.get("t", 0)),
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
