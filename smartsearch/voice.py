"""Speech-to-text for voice queries (faster-whisper, local).

The UI records with MediaRecorder (webm/opus) and posts the bytes here; ffmpeg decodes them to
16 kHz PCM. GPU (float16) when the CUDA libraries load, else CPU int8.


"""
from __future__ import annotations

import glob
import logging
import os
import sys
import threading
import time

from . import config
from .sources import media

log = logging.getLogger(__name__)
_model = None
_device = None
_lock = threading.Lock()


def _add_cuda_dlls() -> None:
    """pip's nvidia-cublas/cudnn wheels put DLLs in site-packages/nvidia/*/bin; make them findable."""
    for base in sys.path:
        for d in glob.glob(os.path.join(base, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            except OSError:
                pass


# "auto": the large turbo model on an NVIDIA GPU (~0.3 s per query); "base" on CPU (~1.7 s for a short query on a
# 16-thread laptop CPU, vs ~5 s for "small" and far more for the large one, which is also a 1.6 GB download).
AUTO_MODEL = {"cuda": "large-v3-turbo", "cpu": "base"}


def load(download: bool = True):
    """The speech model, loaded once. `download=False` (start-up warm-up) only uses a model already on disk,
    so nobody downloads one just by starting the app; the first press of the mic fetches it."""
    global _model, _device
    with _lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel
        _add_cuda_dlls()
        setting = config.get("whisper_model")
        for device, ctype in (("cuda", "float16"), ("cpu", "int8")):
            name = AUTO_MODEL[device] if setting == "auto" else setting
            try:
                try:  # stay offline once the model is cached; download only on first use
                    m = WhisperModel(name, device=device, compute_type=ctype, local_files_only=True)
                except Exception:
                    if not download:
                        raise
                    log.info("downloading speech model %s (first use)", name)
                    m = WhisperModel(name, device=device, compute_type=ctype)
                # A tiny decode proves the CUDA libraries actually load (errors surface on first use).
                import numpy as np
                list(m.transcribe(np.zeros(16000, np.float32), language="en")[0])
                _model, _device = m, device
                log.info("whisper %s ready on %s", name, device)
                return _model
            except Exception as e:
                log.warning("whisper on %s failed: %s", device, e)
        raise RuntimeError("Whisper could not be loaded")


def _decode(audio: bytes):
    """Any container/codec the browser records (webm/opus, ogg, wav) -> 16 kHz mono float32 via ffmpeg."""
    import subprocess
    import numpy as np
    r = subprocess.run([media.ffmpeg(), "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
                       input=audio, capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("could not decode audio: " + r.stderr.decode(errors="ignore")[:200])
    return np.frombuffer(r.stdout, np.float32)


def transcribe(audio: bytes) -> dict:
    t = time.perf_counter()
    m = load()
    # Language detection is a second pass (it doubles CPU time); "voice_language" (e.g. "en") skips it.
    segs, info = m.transcribe(_decode(audio), beam_size=1, vad_filter=True, condition_on_previous_text=False,
                              language=config.get("voice_language") or None)
    text = " ".join(s.text.strip() for s in segs).strip()
    return {"text": text, "language": info.language, "device": _device,
            "ms": round((time.perf_counter() - t) * 1000)}


def warm_up_in_background() -> None:
    threading.Thread(target=lambda: _safe(lambda: load(download=False)), name="whisper-warmup", daemon=True).start()


def _safe(fn):
    try:
        fn()
    except Exception as e:  # usually: not downloaded yet (it will be on the first voice query)
        log.info("speech model not warmed up: %s", str(e).splitlines()[0][:200] if str(e) else type(e).__name__)
