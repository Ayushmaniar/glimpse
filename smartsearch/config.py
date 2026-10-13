"""Paths and user settings. Settings live in .data/settings.json next to the project."""
from __future__ import annotations

import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("SMARTSEARCH_DATA") or os.path.join(ROOT, ".data")
MODEL_REPO = "litert-community/embeddinggemma-2-740m-litert-lm"  # Hugging Face, Apache-2.0, not gated
MODEL_PATH = os.path.join(ROOT, "models", "embeddinggemma-2-740m.litertlm")
MODEL_CACHE_DIR = os.path.join(ROOT, "models", "cache")
DB_PATH = os.path.join(DATA_DIR, "index.sqlite")
THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
CATALOG_PATH = os.path.join(ROOT, "smartsearch", "catalog", "actions.toml")
UI_DIR = os.path.join(ROOT, "smartsearch", "ui")

_HOME = os.path.expanduser("~")

DEFAULTS: dict = {
    # Embedding hyperparameters (see README "Choosing the numbers").
    "embedding_dim": 256,            # Matryoshka: 768 | 512 | 256 | 128 (256 ≈ lossless here, 3x smaller)
    "vector_dtype": "int8",          # int8 | float16 (int8: 1 byte/dim + 4-byte scale, no measurable loss)
    "backend": "auto",               # auto | gpu | cpu
    "vision_tokens_per_image": None,  # None = model default; fewer tokens = faster image indexing
    # What to index. Folders are scanned recursively.
    "folders": [os.path.join(_HOME, d) for d in ("Documents", "Desktop", "Downloads", "Pictures", "Videos", "Music")],
    "exclude_dirs": ["node_modules", ".git", ".venv", "venv", "__pycache__", "site-packages", ".data", "AppData"],
    "sources": {
        "actions": True, "files": True, "scans": True, "images": True, "audio": True, "video": True,
        "windows": True, "emoji": True, "names": True,
        # Personal history: off until the user turns it on.
        "shell_history": False, "browser_history": False,
    },
    # Chunking for text documents.
    "chunk_chars": 1200,
    "chunk_overlap": 150,
    "max_chunks_per_file": 200, "max_chunks_code": 60, "max_chunks_data": 2, "max_scanned_pages": 60, "frame_sample_every": 20, "pause_on_battery": True, "window_pos": None, "rescan_minutes": 60,
    # First indexing pass covers files modified in the last N days (0 = single pass).
    "recent_first_days": 90,
    # Video: one frame every N seconds (plus scene changes).
    "video_frame_every_s": 10,
    # Audio: split into windows of N seconds.
    "audio_window_s": 30,
    "hotkey": ["alt+space", "ctrl+shift+space", "alt+shift+space"],  # first free one wins
    "whisper_model": "auto",  # auto: large-v3-turbo on an NVIDIA GPU, small on CPU; or any faster-whisper name
}

_lock = threading.Lock()
_settings: dict | None = None


def _load() -> dict:
    s = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(s.get(k), dict):
                s[k].update(v)
            else:
                s[k] = v
    return s


def get(key: str):
    global _settings
    with _lock:
        if _settings is None:
            _settings = _load()
        return _settings[key]


def all_settings() -> dict:
    get("hotkey")
    with _lock:
        return json.loads(json.dumps(_settings))


def update(changes: dict) -> None:
    """Merge changes into settings.json (only keys that differ from defaults are stored)."""
    global _settings
    with _lock:
        cur = _load()
        for k, v in changes.items():
            if isinstance(v, dict) and isinstance(cur.get(k), dict):
                cur[k].update(v)
            else:
                cur[k] = v
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(_diff(cur), f, indent=2)
        _settings = cur


def _diff(s: dict) -> dict:
    """Only what differs from DEFAULTS, so improved defaults reach existing installs."""
    out = {}
    for k, v in s.items():
        d = DEFAULTS.get(k)
        if isinstance(v, dict) and isinstance(d, dict):
            sub = {kk: vv for kk, vv in v.items() if d.get(kk) != vv}
            if sub:
                out[k] = sub
        elif k not in DEFAULTS or v != d:
            out[k] = v
    return out


def source_enabled(name: str) -> bool:
    return bool(get("sources").get(name, False))
