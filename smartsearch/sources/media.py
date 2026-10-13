"""ffmpeg helpers shared by the audio and video sources, and 'play from timestamp'."""
from __future__ import annotations

import functools
import glob
import os
import re
import shutil
import subprocess
import tempfile

NO_WINDOW = subprocess.CREATE_NO_WINDOW


@functools.lru_cache(maxsize=1)
def ffmpeg() -> str:
    """ffmpeg on PATH if the user has one, else the copy bundled with the imageio-ffmpeg package."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"  # fails per call and is logged there; the rest of the app keeps working


def _probe(path: str) -> str:
    """ffmpeg's description of a file (it prints it and exits non-zero when given no output); no ffprobe needed."""
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, timeout=30,
                       creationflags=NO_WINDOW)
    return r.stderr.decode("utf-8", "replace")


def duration(path: str) -> float:
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", _probe(path))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def audio_window(path: str, start: float, length: float) -> bytes:
    """16 kHz mono WAV bytes of [start, start+length)."""
    r = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", path,
                        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", "-"],
                       capture_output=True, timeout=120, creationflags=NO_WINDOW)
    return r.stdout if r.returncode == 0 else b""


def has_audio(path: str) -> bool:
    return re.search(r"Stream #\S+.*?: Audio:", _probe(path)) is not None


def frames(path: str, every_s: float, px: int = 512) -> tuple[str, list[str]]:
    """Extract one frame every `every_s` seconds into a temp dir. Returns (dir, sorted frame paths)."""
    d = tempfile.mkdtemp(prefix="ss_frames_")
    subprocess.run([ffmpeg(), "-v", "error", "-i", path, "-vf", f"fps=1/{every_s:.3f},scale={px}:-2",
                    "-q:v", "4", os.path.join(d, "f_%06d.jpg")], timeout=3600, creationflags=NO_WINDOW)
    return d, sorted(glob.glob(os.path.join(d, "f_*.jpg")))


def cleanup(d: str) -> None:
    shutil.rmtree(d, ignore_errors=True)


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def _vlc() -> str | None:
    for p in (shutil.which("vlc"), r"C:\Program Files\VideoLAN\VLC\vlc.exe",
              r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"):
        if p and os.path.exists(p):
            return p
    return None


def play_at(path: str, start: float) -> str:
    """Open the file at a timestamp: VLC if installed, else ffplay, else the default app (from the start)."""
    start = max(0.0, start - 1.0)
    vlc = _vlc()
    if vlc:
        subprocess.Popen([vlc, f"--start-time={start:.0f}", path])
        return "VLC"
    if shutil.which("ffplay"):
        subprocess.Popen(["ffplay", "-ss", f"{start:.0f}", "-autoexit", "-window_title",
                          os.path.basename(path), path], creationflags=NO_WINDOW)
        return "ffplay"
    os.startfile(path)  # noqa: S606 — user clicked this result
    return "default player"
