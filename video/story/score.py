#!/usr/bin/env python3
"""Music and sound design for the Glimpse launch film.

Every section time comes from out/story/timeline.json (written by sim.js); every search, row,
card, keystroke, hero and cue time comes from out/story/events.json; impacts come from
out/story/collisions.json. Renders out/story/music.wav: 48 kHz stereo float32, duration + 1.5 s
(reverb tail). Everything is synthesized with numpy/scipy.

Run:
  cd video && uv run --no-project --with numpy --with scipy python story/score.py
"""
import json
import math
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile

T_START = time.time()

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out" / "story"
EVENTS_PATH = OUT_DIR / "events.json"
COLLISIONS_PATH = OUT_DIR / "collisions.json"
TIMELINE_PATH = OUT_DIR / "timeline.json"
WAV_PATH = OUT_DIR / "music.wav"

# ---------------------------------------------------------------------------------------------
# Clock: everything below is read from timeline.json (no section time is hardcoded).
# ---------------------------------------------------------------------------------------------
TL = json.loads(TIMELINE_PATH.read_text(encoding="utf-8"))
SR = 48000
BPM = float(TL["bpm"])
BEAT = float(TL["beat"])                 # 0.6 s
BAR = float(TL["bar"])                   # 2.4 s
SIXTEENTH = BEAT / 4
DUR = float(TL["duration"])              # film length (74.4 s)
TAIL = 1.5
TOTAL = DUR + TAIL
N = int(round(TOTAL * SR))


def bar(n):
    """Time of bar n."""
    return n * BAR


def bt(n):
    return n * BEAT


def bar_index(t):
    return int(round(t / BAR))


ACTS = {k: (float(a), float(b)) for k, (a, b) in TL["acts"].items()}
MEM0, MEM_END = ACTS["memories"]
TURN0, TURN_END = ACTS["turn"]
ASK0, ASK1 = ACTS["ask"]
BRAIN0, BRAIN1 = ACTS["brain"]
FIN0, FIN1 = ACTS["finale"]

HIT_TURN = float(TL["hits"]["turn"])
HIT_FINALE = float(TL["hits"]["finale"])
SLOW_T0 = float(TL["slow"]["t0"])
FLOOD = (float(TL["flood"]["t0"]), float(TL["flood"]["t1"]))
BRAIN_T = {k: float(v) for k, v in TL["brain"].items()}   # gather, model, space, local, implode
QUERIES = sorted(TL["queries"], key=lambda q: q["k"])
SEARCH = {int(q["k"]): q for q in QUERIES}
TEXT_K = [k for k in SEARCH if SEARCH[k]["kind"] in ("text", "rapid")]

# silences: the eighth note before each hit (0.3 s), as in the turn
SILENCE = (HIT_TURN - BEAT / 2, HIT_TURN)
FINALE_SILENCE = (HIT_FINALE - BEAT / 2, HIT_FINALE)
SILENCES = [SILENCE, FINALE_SILENCE]
RISER1 = (HIT_TURN - BAR, SILENCE[0])           # the memories riser: one bar before the turn hit
RISER2 = (BRAIN_T["local"], FINALE_SILENCE[0])  # the brain riser: local -> silence before the finale
IMPLODE = BRAIN_T["implode"]

MEM_BARS = bar_index(MEM_END)                   # bars 0..5 are the memories act
TURN_BAR = bar_index(TURN0)
ASK_BAR0 = bar_index(ASK0)
BRAIN_BAR0 = bar_index(BRAIN0)
FIN_BAR0 = bar_index(FIN0)
LAST_BAR = bar_index(DUR) - 1                   # the final bar holds the Dadd9 sustain

# Level plan (dB, applied as a ride before compression; search act = 0 dB)
ACT1_TRIM_DB = -6.2      # memories act about 4 dB under the search act (about -20 dBFS)
TURN_DB = -1.5           # the turn section
BRAIN_DB = -2.0          # the breakdown
FIRST_BEAT_DB = 2.0      # finale hit ride: its first beat sits about 9 dB over the search act (loudest moment)
FINALE_DB = 1.0          # final chorus ride: about 2 dB over the first chorus
LOUD_TARGET_DB = -17.0   # search act RMS before the final peak normalize (lands near -18)
CLATTER_GAIN = 0.07      # after-turn impact clatter sits about 24 dB under the music

# ---------------------------------------------------------------------------------------------
# Harmony and melody
# ---------------------------------------------------------------------------------------------
VOICING = {                       # MIDI notes, pad voicings
    "D": [50, 57, 62, 66],
    "A/C#": [49, 52, 57, 64],
    "Bm": [47, 54, 59, 62],
    "G": [43, 50, 55, 59],
    "Asus4": [45, 50, 52, 57],
    "A": [45, 52, 57, 61],
    "Dadd9": [50, 54, 57, 62, 64],
}
BASS_ROOT = {"D": 38, "A/C#": 37, "Bm": 35, "G": 31, "Asus4": 33, "A": 33, "Dadd9": 38}
PROG_ACT1 = ["D", "A/C#", "Bm", "G", "D", "Asus4"]
PROG_LOOP = ["D", "A/C#", "Bm", "G"]
PENT = [0, 2, 4, 7, 9]            # D major pentatonic: D E F# A B
PILE_STEPS = [1, -1, 2, -2, 1]    # melodic walk over the chord-tone pool for the pile notes

# Hook phrases (2 bars each = 8 quarter notes). First one is the storyboard hook F#5 A5 B5 A5 | F#5 E5 D5.
HOOKS = [
    [78, 81, 83, 81, 78, 76, 74, 74],
    [74, 78, 81, 83, 81, 78, 76, 74],
    [83, 81, 78, 76, 74, 78, 81, 78],
    [81, 83, 86, 83, 81, 78, 76, 74],
]
HOOK_OF_SEARCH = [0, 1, 2, 0, 3, 1, 2, 1, 3]
CARD_START = [3, 2, 0, 3, 2, 0, 3, 0, 2] # pentatonic index a search's cards start from (chord tones)


def chord_of_bar(i):
    if i < MEM_BARS:
        return PROG_ACT1[i]
    if i >= LAST_BAR:
        return "Dadd9"
    return PROG_LOOP[(i - TURN_BAR) % 4]


def chord_of_half(i, h):
    if i == MEM_BARS - 1 and h == 1:
        return "A"                  # the sus4 resolves to A in the second half of the last memories bar
    return chord_of_bar(i)


def chord_pool(ch):
    """Chord tones (pitch classes of the voicing) across two octaves, D4 to A5 for D major."""
    pcs = sorted({m % 12 for m in VOICING[ch]})
    return sorted([60 + pc for pc in pcs] + [72 + pc for pc in pcs])


def mtof(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def pent(idx, root=62):
    o, d = divmod(idx, 5)
    return root + 12 * o + PENT[d]


# ---------------------------------------------------------------------------------------------
# DSP helpers
# ---------------------------------------------------------------------------------------------
RNG = np.random.default_rng(20261013)
IR_RNG = np.random.default_rng(99)


@lru_cache(maxsize=None)
def lp_sos(fc, order=2):
    return signal.butter(order, float(fc), "low", fs=SR, output="sos")


@lru_cache(maxsize=None)
def hp_sos(fc, order=2):
    return signal.butter(order, float(fc), "high", fs=SR, output="sos")


@lru_cache(maxsize=None)
def bp_sos(lo, hi, order=2):
    return signal.butter(order, [float(lo), float(hi)], "band", fs=SR, output="sos")


def tv(n):
    return np.arange(n) / SR


def shape(x, fin=0.004, fout=0.02):
    """Short cosine fades at both ends so no voice can click."""
    n = len(x)
    a = min(int(fin * SR), n // 2)
    b = min(int(fout * SR), n // 2)
    if a > 0:
        x[:a] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    if b > 0:
        x[n - b:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(b) / b)
    return x


def saw(f, n, nh=10):
    """Band-limited sawtooth by additive harmonics."""
    t = tv(n)
    out = np.zeros(n)
    hmax = max(1, min(nh, int(0.45 * SR / f)))
    th = 2.0 * np.pi * f * t
    for h in range(1, hmax + 1):
        out += np.sin(h * th) / h
    return out * (2.0 / np.pi)


def chorus_st(x, base=0.016, depth=0.0045, rate=0.23):
    """Two slowly modulated short delays, one per channel, for a wide chorus."""
    n = len(x)
    t = tv(n)
    idx = np.arange(n)
    outs = []
    for ph in (0.0, np.pi):
        d = (base + depth * np.sin(2.0 * np.pi * rate * t + ph)) * SR
        y = np.interp(idx - d, idx, x, left=0.0)
        outs.append(0.6 * x + 0.6 * y)
    return outs


# --- stereo bus ------------------------------------------------------------------------------
BUS = {}


def new_buses():
    for name in ("dry", "rev", "dly", "post", "dkd", "dkr"):
        BUS[name] = np.zeros((N, 2), dtype=np.float32)


KICKS = []   # (time, vel) for sidechain ducking of pad and bass


def _add(name, i0, sl, sr):
    n = len(sl)
    arr = BUS[name]
    arr[i0:i0 + n, 0] += sl
    arr[i0:i0 + n, 1] += sr


def place(t, sig, gain=1.0, pan=0.0, rev=0.0, dly=0.0, duck=False, post=False):
    """Add a mono signal at time t with equal-power pan and optional reverb/delay sends.
    post=True bypasses the silence gates (used only for whooshes that rise into a silence)."""
    i0 = int(round(t * SR))
    if i0 < 0 or i0 >= N:
        return
    n = min(len(sig), N - i0)
    if n <= 0:
        return
    s = sig[:n] * gain
    ang = (pan + 1.0) * np.pi / 4.0
    gl, gr = math.cos(ang), math.sin(ang)
    main = "post" if post else ("dkd" if duck else "dry")
    _add(main, i0, s * gl, s * gr)
    if rev > 0:
        _add("dkr" if duck else "rev", i0, s * rev * gl, s * rev * gr)
    if dly > 0:
        _add("dly", i0, s * dly * gl, s * dly * gr)


def place2(t, L, R, gain=1.0, rev=0.0, duck=False):
    """Add an already-stereo signal (pad, choir) at time t."""
    i0 = int(round(t * SR))
    if i0 < 0 or i0 >= N:
        return
    n = min(len(L), N - i0)
    _add("dkd" if duck else "dry", i0, L[:n] * gain, R[:n] * gain)
    if rev > 0:
        _add("dkr" if duck else "rev", i0, L[:n] * gain * rev, R[:n] * gain * rev)


def kick_at(t, vel=1.0, **kw):
    KICKS.append((t, vel))
    place(t, kick(vel, **kw))


def duck_envelope():
    d = np.zeros(N, dtype=np.float64)
    L = int(0.35 * SR)
    dec = np.exp(-np.arange(L) / (0.11 * SR))
    for t, v in KICKS:
        i0 = int(round(t * SR))
        if i0 >= N:
            continue
        m = min(L, N - i0)
        d[i0:i0 + m] = np.maximum(d[i0:i0 + m], v * dec[:m])
    return 1.0 - 0.55 * np.clip(d, 0.0, 1.0)


# ---------------------------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------------------------
def felt_piano(f0, dur, vel=0.4, cents=0.0, sustain=1.0):
    """Additive felt piano: slightly inharmonic partials, per-partial decay, hammer thump.
    sustain stretches every partial's decay (pedal-like ring)."""
    f0 = f0 * 2.0 ** (cents / 1200.0)
    n = int(dur * SR)
    t = tv(n)
    out = np.zeros(n)
    for h in range(1, 11):
        fh = h * f0 * math.sqrt(1.0 + 0.00035 * h * h)
        if fh > 0.42 * SR:
            break
        tau = sustain * 2.6 / (1.0 + 0.9 * h)
        out += (1.0 / h ** 1.35) * np.exp(-t / tau) * np.sin(2.0 * np.pi * fh * t)
    m = int(0.03 * SR)
    hm = signal.sosfilt(bp_sos(1200, 5500), RNG.standard_normal(m)) * np.exp(-np.arange(m) / (0.004 * SR))
    hm = hm / (np.max(np.abs(hm)) + 1e-9) * 0.35
    out[:m] += hm
    out = signal.sosfilt(lp_sos(6000), out)
    return shape(out * vel, 0.002, 0.04)


def piano_pair(t, m, vel, rev=0.35, dur=2.4, sustain=1.0):
    """A piano note as two slightly detuned voices, panned for width."""
    place(t, felt_piano(mtof(m), dur, vel, cents=-1.2, sustain=sustain), pan=-0.3, rev=rev)
    place(t, felt_piano(mtof(m), dur, vel, cents=+1.2, sustain=sustain), pan=+0.3, rev=rev)


def mallet(f, vel=0.5, dur=1.5):
    """Kalimba / mallet voice used for the pile's impact notes."""
    n = int(dur * SR)
    t = tv(n)
    out = (np.exp(-t / 0.9) * np.sin(2.0 * np.pi * f * t)
           + 0.22 * np.exp(-t / 0.22) * np.sin(2.0 * np.pi * f * 2.76 * t)
           + 0.07 * np.exp(-t / 0.08) * np.sin(2.0 * np.pi * f * 5.4 * t))
    return shape(out * vel, 0.0015, 0.06)


def fm_bell(f, vel=0.3, dur=2.4, tau=0.9, index=2.2, ratio=3.5):
    """FM glockenspiel-like bell."""
    n = int(dur * SR)
    t = tv(n)
    idx = index * np.exp(-t / 0.3) + 0.15
    car = np.sin(2.0 * np.pi * f * t + idx * np.sin(2.0 * np.pi * ratio * f * t))
    out = (car + 0.18 * np.exp(-t / 0.3) * np.sin(4.0 * np.pi * f * t)) * np.exp(-t / tau)
    return shape(out * vel, 0.002, 0.1)


def marimba(f, vel=0.07, dur=0.5):
    """Soft marimba tick for the text-search result rows."""
    n = int(dur * SR)
    t = tv(n)
    out = np.sin(2.0 * np.pi * f * t) * np.exp(-t / 0.12) + 0.25 * np.sin(2.0 * np.pi * 4.0 * f * t) * np.exp(-t / 0.03)
    return shape(out * vel, 0.001, 0.05)


def pluck(f, vel=0.07, dur=1.0):
    """Pizzicato-style plucked string: a decaying tone with a short bright noise attack."""
    n = int(dur * SR)
    t = tv(n)
    body = (np.sin(2.0 * np.pi * f * t) + 0.35 * np.sin(4.0 * np.pi * f * t) * np.exp(-t / 0.12)) * np.exp(-t / 0.35)
    attack = signal.sosfilt(bp_sos(2000, 6000), RNG.standard_normal(n)) * np.exp(-t / 0.008) * 0.3
    return shape((body + attack) * vel, 0.001, 0.05)


def saw_lead(f, vel=0.1, dur=0.8):
    n = int(dur * SR)
    t = tv(n)
    x = signal.sosfilt(lp_sos(1800), saw(f, n, nh=12))
    env = np.minimum(t / 0.015, 1.0) * np.exp(-t / 0.7)
    return shape(x * env * vel, 0.0, 0.05)


def bass(f, vel=0.4, dur=0.34):
    n = int(dur * SR)
    t = tv(n)
    sub = np.sin(2.0 * np.pi * f * t)
    s = signal.sosfilt(lp_sos(520), saw(f, n, nh=8))
    env = np.minimum(t / 0.004, 1.0) * np.exp(-t / 0.2)
    out = (0.8 * sub + 0.35 * s) * env
    out = signal.sosfilt(hp_sos(40), out)
    return shape(out * vel, 0.0, 0.03)


def kick(vel=1.0, top=150.0, bottom=45.0, tau_p=0.035, tau_a=0.2, dur=0.6):
    """Punchy sine kick with a pitch drop from top to bottom Hz."""
    n = int(dur * SR)
    t = tv(n)
    ph = 2.0 * np.pi * (bottom * t + (top - bottom) * tau_p * (1.0 - np.exp(-t / tau_p)))
    body = np.sin(ph) * np.exp(-t / tau_a)
    click = signal.sosfilt(bp_sos(1500, 5000), RNG.standard_normal(n)) * np.exp(-t / 0.003)
    out = (np.tanh(1.5 * body) / np.tanh(1.5) + 0.12 * click) * vel
    return shape(out, 0.0005, 0.05)


def snare(vel=1.0, dur=0.3, tone=190.0):
    n = int(dur * SR)
    t = tv(n)
    noise = signal.sosfilt(bp_sos(1700, 8000), RNG.standard_normal(n))
    ph = 2.0 * np.pi * (tone * t + tone * 0.2 * 0.02 * (1.0 - np.exp(-t / 0.02)))
    body = np.sin(ph) * np.exp(-t / 0.06)
    out = (0.8 * noise * np.exp(-t / 0.14) + 0.45 * body) * vel
    return shape(out, 0.0005, 0.02)


def clap(vel=0.8, dur=0.3):
    n = int(dur * SR)
    t = tv(n)
    x = signal.sosfilt(bp_sos(900, 3200), RNG.standard_normal(n))
    env = np.exp(-t / 0.12)
    for d in (0.0, 0.011, 0.023):
        env = env + np.where(t >= d, 1.2 * np.exp(-(t - d) / 0.006), 0.0)
    return shape(x * env * vel, 0.0002, 0.02)


def _mk_hat(length=0.09, decay=0.012):
    n = int(length * SR)
    t = tv(n)
    x = signal.sosfilt(bp_sos(7000, 11000), RNG.standard_normal(n)) * np.exp(-t / decay)
    return x / np.max(np.abs(x))


HATS = [_mk_hat() for _ in range(3)]


def _mk_tick(seed):
    n = int(0.03 * SR)
    t = tv(n)
    x = signal.sosfilt(bp_sos(5000, 9000), RNG.standard_normal(n)) * np.exp(-t / 0.0045)
    x = x + 0.35 * np.sin(2.0 * np.pi * (3000 + 400 * seed) * t) * np.exp(-t / 0.003)
    return x / np.max(np.abs(x))


TICKS = [_mk_tick(i) for i in range(4)]


def _mk_clatter(i):
    n = int(0.06 * SR)
    t = tv(n)
    x = signal.sosfilt(bp_sos(1200 + 300 * i, 4200), RNG.standard_normal(n)) * np.exp(-t / 0.009)
    x = x + 0.5 * np.sin(2.0 * np.pi * (650 + 120 * i) * t) * np.exp(-t / 0.018)
    return x / np.max(np.abs(x))


CLATTER = [_mk_clatter(i) for i in range(4)]


def blip(f=1100.0, vel=0.06, dur=0.12):
    n = int(dur * SR)
    t = tv(n)
    return shape(np.sin(2.0 * np.pi * f * t) * np.exp(-t / 0.03) * vel, 0.001, 0.04)


def whoosh(dur=0.9, peak=0.3, vel=0.5):
    """Filtered-noise whoosh that rises to a peak at `peak` seconds after its start."""
    n = int(dur * SR)
    t = tv(n)
    noise = RNG.standard_normal(n)
    lo = signal.sosfilt(bp_sos(350, 1100), noise)
    hi = signal.sosfilt(bp_sos(2600, 9000), noise)
    w = np.clip(t / peak, 0.0, 1.0)
    env = np.where(t < peak, (t / peak) ** 2, np.exp(-(t - peak) / 0.22))
    return shape((lo * (1.0 - w) + hi * w) * env * vel, 0.0, 0.04)


def rev_swell(dur=1.2, vel=0.3):
    """Noise swell that builds to its last sample (reverse-cymbal feel)."""
    n = int(dur * SR)
    t = tv(n)
    x = signal.sosfilt(bp_sos(500, 9000), RNG.standard_normal(n))
    return shape(x * (t / dur) ** 3 * vel, 0.0, 0.03)


def swell(tp=1.2, tail=1.8, vel=0.22):
    """Cymbal-like swell that peaks at tp seconds."""
    n = int((tp + tail) * SR)
    t = tv(n)
    x = signal.sosfilt(bp_sos(4500, 12000), RNG.standard_normal(n))
    env = np.where(t < tp, (t / tp) ** 2, np.exp(-(t - tp) / 0.5))
    return shape(x * env * vel, 0.0, 0.1)


def boom(vel=1.0, dur=3.0):
    """Sub sine sweep 70 to 30 Hz, distorted low tom and a noise burst."""
    n = int(dur * SR)
    t = tv(n)
    sub = np.sin(2.0 * np.pi * (30.0 * t + 40.0 * 0.25 * (1.0 - np.exp(-t / 0.25)))) * np.exp(-t / 1.5)
    tom = np.tanh(3.0 * np.sin(2.0 * np.pi * (55.0 * t + 35.0 * 0.05 * (1.0 - np.exp(-t / 0.05))))) / np.tanh(3.0)
    tom = tom * np.exp(-t / 0.45)
    nb = signal.sosfilt(bp_sos(150, 3000), RNG.standard_normal(n)) * np.exp(-t / 0.25)
    return shape((1.0 * sub + 0.35 * tom + 0.3 * nb) * vel, 0.002, 0.2)


def crash(vel=1.0, dur=3.6):
    n = int(dur * SR)
    t = tv(n)
    x = signal.sosfilt(bp_sos(2800, 12000), RNG.standard_normal(n))
    env = (1.0 - np.exp(-t / 0.01)) * np.exp(-t / 1.3)
    return shape(x * env * vel, 0.0, 0.5)


def big_hit(t, boom_gain=1.0, crash_gain=0.55, rev=0.5):
    KICKS.append((t, 1.2))
    place(t, boom(boom_gain), rev=0.2)
    place(t, crash(1.0), gain=crash_gain, rev=rev)


def riser(dur, vel=0.5, f0=180.0, f1=1200.0):
    """Swept noise plus an exponential pitch rise that ends abruptly."""
    n = int(dur * SR)
    t = tv(n)
    p = t / dur
    noise = RNG.standard_normal(n)
    bands = 12
    centers = np.geomspace(300.0, 9000.0, bands)
    bank = np.stack([signal.sosfilt(bp_sos(c / 1.25, c * 1.25), noise) for c in centers])
    pos = p * (bands - 1)
    i0 = np.clip(np.floor(pos).astype(int), 0, bands - 2)
    fr = pos - i0
    idx = np.arange(n)
    sweep = bank[i0, idx] * (1.0 - fr) + bank[i0 + 1, idx] * fr
    k = math.log(f1 / f0)
    phase = 2.0 * np.pi * f0 * dur / k * (np.exp(k * p) - 1.0)
    x = sweep * 0.6 * p ** 1.5 + 0.25 * np.sin(phase) * p ** 2
    return shape(x * vel, 0.01, 0.005)


def glide(f0, f1, dur, vel):
    n = int(dur * SR)
    t = tv(n)
    p = t / dur
    k = math.log(f1 / f0)
    phase = 2.0 * np.pi * f0 * dur / k * (np.exp(k * p) - 1.0)
    env = np.minimum(p * 8.0, 1.0) * (1.0 - p) ** 1.5
    return shape(np.sin(phase) * env * vel, 0.002, 0.05)


def pad_voice(midis, dur, cut=2000.0, nh=8, detune=(-7.0, 7.0)):
    """Detuned band-limited saws, high-passed above the mud (150 Hz), low-passed, with a stereo chorus."""
    n = int(dur * SR)
    x = np.zeros(n)
    for m in midis:
        f = mtof(m)
        for d in detune:
            x += saw(f * 2.0 ** (d / 1200.0), n, nh=nh)
    x /= len(midis) * len(detune)
    x = signal.sosfilt(hp_sos(150, 4), x)
    x = signal.sosfilt(lp_sos(cut), x)
    return chorus_st(x)


def choir_voice(midis, dur):
    """Choir-like 'aah': saws through three formant bands. Returns (L, R)."""
    n = int(dur * SR)
    x = np.zeros(n)
    for m in midis:
        f = mtof(m)
        for d in (-5.0, 5.0):
            x += saw(f * 2.0 ** (d / 1200.0), n, nh=12)
    x /= len(midis) * 2
    y = np.zeros(n)
    for fc, bw, w in ((700.0, 160.0, 1.0), (1150.0, 200.0, 0.6), (2450.0, 320.0, 0.3)):
        y += w * signal.sosfilt(bp_sos(fc - bw / 2, fc + bw / 2), x)
    return chorus_st(y)


# ---------------------------------------------------------------------------------------------
# Musical building blocks
# ---------------------------------------------------------------------------------------------
def pad_bar_cfg(i):
    """(filter cutoff, level) for the pad in bar i, or None when the pad is silent."""
    if 1 <= i < MEM_BARS:
        return 1700.0, {1: 0.14, 2: 0.20, 3: 0.28}.get(i, 0.34)
    if MEM_BARS <= i < BRAIN_BAR0:
        return 2600.0, 0.36
    if BRAIN_BAR0 <= i < FIN_BAR0:
        return [800.0, 2000.0, 4000.0][min(i - BRAIN_BAR0, 2)], 0.36   # breakdown: filter opens up
    if FIN_BAR0 <= i < LAST_BAR:
        return 3400.0, 0.36
    return None


def pad_bar(i, cut, lvl, rev=0.5, dur=BAR + 0.4):
    L, R = pad_voice(VOICING[chord_of_bar(i)], dur, cut=cut)
    shape(L, 0.4, 0.4)
    shape(R, 0.4, 0.4)
    place2(bar(i), L, R, gain=lvl, rev=rev, duck=True)


def choir_bar(i, lvl):
    midis = [m + 12 for m in VOICING[chord_of_bar(i)]]
    L, R = choir_voice(midis, BAR + 0.4)
    shape(L, 0.4, 0.4)
    shape(R, 0.4, 0.4)
    place2(bar(i), L, R, gain=lvl, rev=0.6, duck=True)


CHOIR_LVL = 0.25


def drums_bar(i, level=1.0, halftime=False):
    """Groove for one bar. halftime: a half-time feel (kick on the 1, snare on the 3 only), for the text block."""
    b0 = bar(i)
    kick_at(b0, 1.0 * level)
    if halftime:
        place(b0 + bt(2), snare(0.9 * level), rev=0.35)
        accent = [0.45, 0.2, 0.3, 0.2]
    else:
        kick_at(b0 + bt(2), 0.9 * level)
        kick_at(b0 + bt(3.5), 0.55 * level)          # 8th pickup into the next bar
        place(b0 + bt(1), snare(0.75 * level), rev=0.35)
        place(b0 + bt(3), snare(0.85 * level), rev=0.35)
        accent = [0.55, 0.22, 0.4, 0.25]
    for s in range(16):
        v = accent[s % 4] * level
        place(b0 + s * SIXTEENTH, HATS[s % 3], gain=0.10 * v, pan=0.3 if s % 2 else -0.3)


def pluck_arp(i, level):
    """Pizzicato eighths over the bar's chord tones, the text block's textural layer."""
    tones = [m + 12 for m in VOICING[chord_of_bar(i)]]
    pat = [0, 2, 1, 3, 2, 1, 3, 0]
    for e in range(8):
        place(bar(i) + e * BEAT / 2, pluck(mtof(tones[pat[e]]), vel=0.07 * level),
              pan=0.5 if e % 2 else -0.5, rev=0.35)


def bass_bar(i, level=1.0):
    root = BASS_ROOT[chord_of_bar(i)]
    for e in range(8):
        m = root if e % 2 == 0 else root + 12
        place(bar(i) + e * BEAT / 2, bass(mtof(m), vel=0.30 * level), duck=True)


def hook(t0, notes, oct_=0, vel=0.2, lead=False, counter=False):
    for i, m in enumerate(notes):
        t = t0 + i * BEAT
        place(t, fm_bell(mtof(m + oct_), vel=vel, dur=2.0, tau=0.8),
              pan=-0.25 + 0.5 * (i % 2), rev=0.45, dly=0.4)
        if lead:
            place(t, saw_lead(mtof(m + oct_ - 12), vel=0.08), rev=0.2, duck=True)
        if counter:
            place(t, fm_bell(mtof(m + oct_ + 12), vel=0.07, dur=1.6, tau=0.6),
                  pan=0.35, rev=0.4)


def card_cue(t, k, j):
    """A photo result landing: a bright, clearly audible bell with a sparkle partial above it."""
    midi = pent(CARD_START[k] + j)
    pan = -0.5 + j / 7.0
    place(t, fm_bell(mtof(midi), vel=0.20, dur=1.8, tau=0.6, index=4.0), pan=pan, rev=0.3, dly=0.3)
    place(t, fm_bell(mtof(midi + 12), vel=0.07, dur=0.9, tau=0.25, index=1.5), pan=pan, rev=0.2)


def row_cue(t, k, j):
    """A text result row landing: a soft, short marimba tick, quieter than a photo card."""
    place(t, marimba(mtof(pent((j + k) % 6, root=69)), vel=0.07), pan=-0.4 + 0.8 * ((j % 4) / 3.0), rev=0.25)


def key_spacings(events):
    """Distance to the nearest neighbouring keystroke, per key time (fast rolls get softer ticks)."""
    ts = sorted(float(e["t"]) for e in events if e["type"] == "key")
    sp = {}
    for idx, t in enumerate(ts):
        gaps = []
        if idx > 0:
            gaps.append(t - ts[idx - 1])
        if idx + 1 < len(ts):
            gaps.append(ts[idx + 1] - t)
        sp[round(t, 6)] = min(gaps) if gaps else 1.0
    return sp


def key_cue(t, i, spacing):
    v = 0.085 * (0.85 + 0.3 * ((i * 37 + int(t * 100)) % 7) / 6.0)
    tpl = TICKS[(i + int(t * 1000)) % 4]
    if spacing < 0.08:
        # fast roll (long queries): softer, shorter ticks so they read as a tight hat roll, not a buzz
        v *= max(0.3, spacing / 0.08) ** 0.5
        tpl = tpl[:int(0.012 * SR)]
    place(t, tpl * v, pan=0.35 * math.sin(i * 1.7))


def hero_cue(t):
    place(t - 1.2, swell(1.2, 1.8, vel=0.4), rev=0.5)
    for j, m in enumerate((62, 66, 69, 74)):
        place(t + 0.03 * j, fm_bell(mtof(m), vel=0.2, dur=3.0, tau=1.5),
              pan=-0.4 + 0.27 * j, rev=0.6)


def enter_cue(t):
    place(t, clap(0.6), rev=0.35)
    place(t, snare(0.55), rev=0.3)
    place(t - 0.4, whoosh(dur=0.7, peak=0.4, vel=0.22), rev=0.1)


def vel_of(imp):
    return float(np.clip((math.log10(max(imp, 1e-3)) + 1.7) / 3.4, 0.05, 1.0))


def pan_of(x):
    return float(np.clip(x / 2700.0, -1.0, 1.0)) * 0.75


# ---------------------------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------------------------
def act1_music(stats):
    # room tone: a very quiet low bed with the dust motes
    n = int(HIT_TURN * SR)
    room = signal.sosfilt(lp_sos(380), RNG.standard_normal(n)) * 0.0015
    place(0.0, shape(room, 1.0, 0.5), rev=0.2)

    # felt piano: 8th-note arpeggios over the chords, long pedal-like decays,
    # a low octave root on each bar downbeat, doubled an octave up from the 4th memories bar
    arp = [0, 1, 2, 1, 3, 2, 1, 2]
    for i in range(MEM_BARS):
        ch0 = chord_of_half(i, 0)
        place(bar(i), felt_piano(mtof(min(VOICING[ch0])), 3.2, vel=0.10, sustain=2.0), rev=0.4)
        for e in range(8):
            ch = chord_of_half(i, e // 4)
            tones = [m + 12 for m in VOICING[ch]]
            m = tones[arp[e]]
            t = bar(i) + e * BEAT / 2
            vel = 0.075 if i < MEM_BARS - 2 else 0.065
            piano_pair(t, m, vel, dur=3.0, sustain=1.6)
            stats["piano_notes"] += 2
            if i >= MEM_BARS - 2:
                piano_pair(t, m + 12, 0.03, dur=3.0, sustain=1.6)

    # riser into the turn plus a reverse swell peaking at the silence, then sudden near silence
    place(RISER1[0], riser(RISER1[1] - RISER1[0], vel=0.5), rev=0.2)
    place(SLOW_T0, rev_swell(HIT_TURN - BEAT / 2 - SLOW_T0, vel=0.22), rev=0.5)


def collisions(coll, stats):
    """Physics impacts become notes (act 1), a shimmer bed (flood), and quiet clatter (after the turn)."""
    beats, act1, clat = [], [], []
    for c in coll:
        if "beat" in c:
            beats.append(c)
        elif c["t"] < HIT_TURN:
            act1.append(c)
        else:
            clat.append(c)

    # throttled pile notes: at most one per 50 ms, keeping the loudest; onset snapped to the 16th grid
    # when within 40 ms of it
    kept = {}
    for c in act1:
        t = c["t"]
        if SILENCE[0] <= t < SILENCE[1]:
            continue
        key = int(t // 0.05)
        cur = kept.get(key)
        if cur is None or c["imp"] > cur["imp"]:
            kept[key] = c
    notes = []
    for c in kept.values():
        t = c["t"]
        tq = round(t / SIXTEENTH) * SIXTEENTH
        onset = tq if abs(tq - t) <= 0.04 else t
        notes.append((onset, "pile", c))
    for c in beats:
        notes.append((c["beat"], "beat", c))
    notes.sort(key=lambda z: z[0])

    # pitches come from the current chord's tones over two octaves, walked melodically
    walk = 3
    for onset, kind, c in notes:
        i = min(int(onset // BAR), MEM_BARS - 1)
        h_in = min(1, int((onset - bar(i)) // (BAR / 2)))
        pool = chord_pool(chord_of_half(i, h_in))
        h = (int(c["a"]) * 7919 + int(c["b"]) * 104729 + 13) & 0xFFFF
        walk = (walk + PILE_STEPS[h % 5]) % len(pool)
        m = pool[walk]
        if c["size"] > 300:            # big impacts sound an octave lower
            m -= 12
        v = vel_of(c["imp"])
        first = 1.12 if c.get("first") else 1.0
        pan = pan_of(c["x"])
        if kind == "beat":
            # the 32 beat landings: a felt-piano and bell layer, louder than the pile notes
            g = 1.9 * first
            place(onset, felt_piano(mtof(m), 2.6, vel=(0.12 + 0.08 * v) * g, sustain=1.4), pan=pan, rev=0.3)
            place(onset, fm_bell(mtof(m + 12), vel=(0.05 + 0.05 * v) * g, dur=2.2, tau=0.9), pan=pan, rev=0.35)
        else:
            place(onset, mallet(mtof(m), vel=(0.02 + 0.10 * v ** 1.3) * first), pan=pan, rev=0.25)
    stats["beat_notes"] = len(beats)
    stats["coll_notes"] = len(kept)

    # shimmer bed: bandpassed noise plus high sines, following the impact density
    dt = 0.05
    edges = np.arange(0.0, HIT_TURN + dt, dt)
    times = np.array([c["t"] for c in act1])
    cnt, _ = np.histogram(times, edges)
    rate = np.convolve(cnt / dt, np.ones(5) / 5.0, mode="same")
    centers = edges[:-1] + dt / 2
    fl = (centers >= FLOOD[0]) & (centers < FLOOD[1])
    ref = max(float(np.percentile(rate[fl], 90)) if fl.any() else 1.0, 1.0)
    lvl = np.clip(rate / ref, 0.0, 1.0) ** 1.2
    n = int(HIT_TURN * SR)
    lvl_t = np.interp(tv(n), centers, lvl)
    noise = signal.sosfilt(bp_sos(2800, 9000), RNG.standard_normal(n))
    tt = tv(n)
    sines = np.zeros(n)
    for m in (86, 90, 93, 98):
        sines += np.sin(2.0 * np.pi * mtof(m) * tt) * (0.6 + 0.4 * np.sin(2.0 * np.pi * (2.3 + 0.37 * (m % 5)) * tt))
    shim = (0.05 * noise + 0.02 * sines) * lvl_t
    place(0.0, shape(shim, 0.05, 0.05), rev=0.35)
    stats["density_peak"] = float(ref)

    # after the turn: quiet dry clatter (wood/paper), panned by x, throttled to 15 ms bins
    kept2 = {}
    for c in clat:
        if "beat" in c:
            continue
        key = int(c["t"] // 0.015)
        cur = kept2.get(key)
        if cur is None or c["imp"] > cur["imp"]:
            kept2[key] = c
    for c in kept2.values():
        v = vel_of(c["imp"])
        h = int(c["a"]) * 31 + int(c["b"]) * 17
        place(c["t"], CLATTER[h % 4] * (0.02 + 0.07 * v) * CLATTER_GAIN, pan=pan_of(c["x"]), rev=0.06)
    stats["clatter_ticks"] = len(kept2)
    stats["act1_impacts"] = len(act1)
    stats["after_turn_impacts"] = len(clat)


def ask_music():
    # groove from the turn bar through the end of the search act, rising in energy across the eight searches
    for i in range(TURN_BAR, BRAIN_BAR0):
        k = max(0, (i - ASK_BAR0) // 2)
        level = 0.85 + 0.035 * k if i >= ASK_BAR0 else 0.85
        text = i >= ASK_BAR0 and k in TEXT_K
        drums_bar(i, level, halftime=text)
        bass_bar(i, level)
        if text:
            pluck_arp(i, level)
    # the hook for each search (2 bars), starting at the search's start from timeline.json.
    # photo searches 3 and 7 add a saw an octave below and an octave-up counter; text searches stay bell-led
    for k in range(len(QUERIES)):
        q = SEARCH[k]
        lead = k >= 3 and q["kind"] == "photo"
        hook(q["start"], HOOKS[HOOK_OF_SEARCH[k]], vel=0.19 if k < 3 else 0.21, lead=lead, counter=lead)


def brain_music():
    # heartbeat: soft sub kick on the 1 and the "and" of 1, drums are out
    for i in range(BRAIN_BAR0, FIN_BAR0):
        kick_at(bar(i), 0.55, top=75.0, bottom=50.0, tau_a=0.25)
        kick_at(bar(i) + bt(0.5), 0.40, top=75.0, bottom=50.0, tau_a=0.2)
    # the piano returns with the hook slowed to 1.2 s per note
    for j, m in enumerate([78, 81, 83, 81, 78, 76]):
        piano_pair(BRAIN0 + j * bt(2), m, vel=0.3, dur=2.6)
    # shimmering high arps from the 'model' cue
    arp16 = [0, 1, 2, 3, 2, 1]
    for i in range(BRAIN_BAR0, FIN_BAR0):
        tones = [m + 24 for m in VOICING[chord_of_bar(i)]]
        for s in range(16):
            t = bar(i) + s * SIXTEENTH
            if t < BRAIN_T["model"]:
                continue
            place(t, fm_bell(mtof(tones[arp16[s % 6]]), vel=0.035, dur=1.6, tau=0.6),
                  pan=-0.5 + (s % 4) / 1.5, rev=0.5, dly=0.25)
    # riser from 'local' to the silence before the finale, with a snare roll that crescendos into it
    place(RISER2[0], riser(RISER2[1] - RISER2[0], vel=0.9, f0=250.0, f1=2600.0), rev=0.3)
    for j in range(8):
        place(RISER2[0] + j * SIXTEENTH, snare(0.25 + 0.35 * j / 7.0, dur=0.15), rev=0.25)
    for j in range(12):
        place(IMPLODE + j * BEAT / 8, snare(0.35 + 0.65 * j / 11.0, dur=0.12), rev=0.25)
    # the implosion swell rises into the silence (it is gated there)
    place(IMPLODE, rev_swell(1.2, vel=0.12), rev=0.3)


def finale_music():
    for i in range(FIN_BAR0, LAST_BAR):
        drums_bar(i, 1.1)
        bass_bar(i, 1.1)
        choir_bar(i, CHOIR_LVL)
    # two choruses, the hook up an octave
    hook(bar(FIN_BAR0), HOOKS[0], oct_=12, vel=0.23, counter=True)
    hook(bar(FIN_BAR0 + 2), HOOKS[3], oct_=12, vel=0.24, lead=True, counter=True)
    # the hit: a whoosh rising into the silence, peaking on the hit
    place(FINALE_SILENCE[0], whoosh(dur=0.9, peak=0.3, vel=0.09), post=True)
    # the full Dadd9 chord under the hit: wide strings and choir
    L, R = pad_voice(VOICING["Dadd9"], 5.0, cut=4500.0, nh=10)
    shape(L, 0.005, 0.8)
    shape(R, 0.005, 0.8)
    place2(HIT_FINALE, L, R, gain=0.9, rev=0.5)
    L, R = choir_voice([m + 12 for m in VOICING["Dadd9"]], 4.5)
    shape(L, 0.005, 0.8)
    shape(R, 0.005, 0.8)
    place2(HIT_FINALE, L, R, gain=0.45, rev=0.6)
    # the final sustained Dadd9 with a piano tail that decays before the end
    L, R = pad_voice(VOICING["Dadd9"], 4.3, cut=3000.0, nh=8)
    shape(L, 0.8, 0.0)
    shape(R, 0.8, 0.0)
    last = bar(LAST_BAR)
    place2(last, L, R, gain=0.36, rev=0.6)
    for j, m in enumerate((62, 66, 69, 74, 76)):
        piano_pair(last + j * 0.035, m, vel=0.26, dur=4.0)
    for j, m in enumerate((81, 86, 78, 74, 62)):
        piano_pair(last + bt(1) + j * bt(0.5), m, vel=0.14, dur=3.0)


def event_cues(events, stats):
    counts = {}
    spacing = key_spacings(events)
    for e in events:
        typ, t = e["type"], float(e["t"])
        counts[typ] = counts.get(typ, 0) + 1
        if typ == "key":
            key_cue(t, int(e.get("i", 0)), spacing.get(round(t, 6), 1.0))
        elif typ == "mic_on":
            place(t, blip(1000.0, 0.05, 0.1))
            place(t + 0.09, blip(1500.0, 0.05, 0.1))
        elif typ == "word":
            place(t, blip(1100.0 + 60.0 * (int(t * 100) % 3), 0.06, 0.12), pan=0.2)
        elif typ == "enter":
            enter_cue(t)
        elif typ == "card":
            card_cue(t, int(e["k"]), int(e["j"]))
        elif typ == "row":
            row_cue(t, int(e["k"]), int(e["j"]))
        elif typ == "hero":
            hero_cue(t)
        elif typ == "whoosh":
            if abs(t - SILENCE[0]) < 0.01:
                # the turn whoosh starts inside the near-silence window, so it stays very low until its peak
                place(t, whoosh(dur=0.9, peak=0.3, vel=0.09), post=True)
            else:
                place(t, whoosh(dur=0.9, peak=0.3, vel=0.4), rev=0.2)
        elif typ == "hit":
            if abs(t - HIT_FINALE) < 0.01:
                big_hit(t, boom_gain=1.2, crash_gain=1.0, rev=0.6)
            else:
                big_hit(t)
        elif typ == "brain_gather":
            place(t, whoosh(dur=1.0, peak=0.6, vel=0.3), rev=0.25)
        elif typ == "brain_model":
            for j, m in enumerate((74, 78, 81, 86)):
                place(t + 0.06 * j, fm_bell(mtof(m), vel=0.1, dur=3.0, tau=1.4), pan=-0.3 + 0.2 * j, rev=0.55)
        elif typ == "brain_space":
            place(t, glide(300.0, 1800.0, 0.6, 0.22), rev=0.4)
            place(t + 0.5, fm_bell(mtof(86), vel=0.08, dur=2.0), rev=0.5)
        elif typ == "brain_local":
            place(t, fm_bell(mtof(81), vel=0.13, dur=2.8, tau=1.2), rev=0.5)
            place(t + 0.1, fm_bell(mtof(88), vel=0.09, dur=2.8, tau=1.2), rev=0.5)
        elif typ == "brain_implode":
            pass   # the implosion swell is placed with the breakdown
        elif typ == "finale_title":
            for j, m in enumerate((62, 69, 74, 78, 86)):
                place(t + 0.04 * j, fm_bell(mtof(m), vel=0.13, dur=3.2, tau=1.6), pan=-0.3 + 0.15 * j, rev=0.55)
        elif typ == "finale_tagline":
            piano_pair(t, 74, vel=0.22, dur=3.0)
            piano_pair(t + 0.04, 81, vel=0.18, dur=3.0)
        elif typ == "finale_install":
            place(t, fm_bell(mtof(74), vel=0.12, dur=2.6), rev=0.5)
            place(t + 0.15, fm_bell(mtof(81), vel=0.09, dur=2.6), rev=0.5)
        elif typ == "finale_url":
            place(t, fm_bell(mtof(78), vel=0.10, dur=2.6), rev=0.5)
            place(t + 0.12, fm_bell(mtof(86), vel=0.07, dur=2.6), rev=0.5)
        # slowmo_in, finale_boom (the hit is the "hit" event), finale_fade: no separate cue
    stats["events"] = counts


# ---------------------------------------------------------------------------------------------
# Master: level ride, silences, filter build, reverb, delay, compression, normalize
# ---------------------------------------------------------------------------------------------
def make_ir(seconds=2.2, pre=0.02):
    n = int(seconds * SR)
    t = tv(n)
    p = int(pre * SR)
    ir = np.zeros((p + n, 2))
    env = np.exp(-t * (math.log(1000.0) / seconds))
    for c in range(2):
        noise = signal.sosfilt(lp_sos(6000), IR_RNG.standard_normal(n))
        ir[p:, c] = noise * env
    ir /= np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True))
    return ir


def ping_pong(src, dly_s=0.45, fb=0.5, taps=7):
    """Dotted-eighth ping-pong: repeats alternate right and left, each one quieter."""
    m = signal.sosfilt(lp_sos(3200), (src[:, 0].astype(np.float64) + src[:, 1]) * 0.5)
    out = np.zeros((N, 2))
    for k in range(1, taps + 1):
        s = int(round(k * dly_s * SR))
        if s >= N:
            break
        tap = np.zeros(N)
        tap[s:] = m[:N - s] * fb ** k
        out[:, 1 if k % 2 else 0] += tap
    return out


def compress(x, thr_db=-16.0, ratio=2.0, att_s=0.12, rel_s=0.4):
    """Gentle RMS bus compressor: 2:1 with a slow detector so transients breathe."""
    ms = np.mean(x.astype(np.float64) ** 2, axis=1)
    a_env = math.exp(-1.0 / (att_s * SR))
    env = np.sqrt(signal.lfilter([1.0 - a_env], [1.0, -a_env], ms) + 1e-12)
    lvl = 20.0 * np.log10(env + 1e-9)
    gr = np.maximum(lvl - thr_db, 0.0) * (1.0 - 1.0 / ratio)
    a_rel = math.exp(-1.0 / (rel_s * SR))
    gr_s = signal.lfilter([1.0 - a_rel], [1.0, -a_rel], gr)
    g = 10.0 ** (-gr_s / 20.0)
    return x * g[:, None]


def soft_clip(x, knee=0.8):
    a = np.abs(x)
    y = np.where(a < knee, a, knee + (1.0 - knee) * np.tanh((a - knee) / (1.0 - knee)))
    return np.sign(x) * y


def rms_db(x):
    return 10.0 * math.log10(float(np.mean(x.astype(np.float64) ** 2)) + 1e-16)


def ride_db():
    """Section level plan in dB, interpolated per sample (search act = 0 dB), from the timeline."""
    pts = [(0.0, ACT1_TRIM_DB), (MEM_END - 0.1, ACT1_TRIM_DB), (MEM_END + 0.1, TURN_DB),
           (TURN_END - 0.1, TURN_DB), (TURN_END + 0.2, 0.0), (ASK1 - 0.2, 0.0), (ASK1 + 0.2, BRAIN_DB),
           (RISER2[0] - 0.1, BRAIN_DB), (RISER2[1], 0.0), (HIT_FINALE, FIRST_BEAT_DB),
           (HIT_FINALE + 0.6, FINALE_DB), (DUR, FINALE_DB), (TOTAL, FINALE_DB)]
    xs, ys = zip(*pts)
    return np.interp(tv(N), xs, ys)


def finalize(stats):
    dk = duck_envelope().astype(np.float32)[:, None]
    dry = BUS["dry"] + BUS["dkd"] * dk
    rev_src = BUS["rev"] + BUS["dkr"] * dk
    BUS["dkd"] = None
    BUS["dkr"] = None

    ride = (10.0 ** (ride_db() / 20.0)).astype(np.float32)[:, None]
    dry *= ride
    rev_src *= ride

    # the build into the finale: everything filters up (high-pass sweep) across the riser
    a0, a1 = int(RISER2[0] * SR), int(RISER2[1] * SR)
    seg = dry[a0:a1].astype(np.float64)
    hp = signal.sosfilt(hp_sos(2500), seg, axis=0)
    wv = np.linspace(0.0, 0.85, a1 - a0)[:, None]
    dry[a0:a1] = (seg * (1.0 - wv) + hp * wv).astype(np.float32)

    # near silences before each hit: music gated, reverb send cut beforehand, faint tail only
    gate = np.ones(N, dtype=np.float32)
    rev_cut = np.ones(N, dtype=np.float32)
    for s0, s1 in SILENCES:
        i1, i2 = int(round(s0 * SR)), int(round(s1 * SR))
        r = int(0.012 * SR)
        gate[i1:i2] = 0.02
        gate[i1 - r:i1] = np.linspace(1.0, 0.02, r)
        gate[i2:i2 + r] = np.linspace(0.02, 1.0, r)
        a_cut = int((s0 - 1.1) * SR)
        rev_cut[a_cut:i1] = np.power(10.0, np.linspace(0.0, -40.0, i1 - a_cut) / 20.0)
        rev_cut[i1:i2] = 10.0 ** (-40.0 / 20.0)
    dry *= gate[:, None]
    rev_src *= (gate * rev_cut)[:, None]

    # slow motion: the whole music is low-passed from the slow-mo start, and the filter opens back up after the silence
    i2 = int(round(SILENCE[1] * SR))
    w = np.zeros(N, dtype=np.float32)
    a0s, a1s = int(SLOW_T0 * SR), int((SLOW_T0 + 0.7) * SR)
    w[a0s:a1s] = 0.5 - 0.5 * np.cos(np.pi * np.linspace(0.0, 1.0, a1s - a0s))
    w[a1s:i2] = 1.0
    r2 = int(0.2 * SR)
    w[i2:i2 + r2] = 0.5 + 0.5 * np.cos(np.pi * np.linspace(0.0, 1.0, r2))
    dry_lp = signal.sosfilt(lp_sos(900), dry, axis=0)
    mix = dry * (1.0 - w)[:, None] + dry_lp * w[:, None]
    del dry_lp

    # convolution reverb on the send bus
    IR = make_ir()
    wet = np.zeros((N, 2))
    for c in range(2):
        wet[:, c] = signal.fftconvolve(rev_src[:, c].astype(np.float64), IR[:, c])[:N]
    mix += wet * 0.45
    del wet
    mix += ping_pong(BUS["dly"]) * 0.8
    mix += BUS["post"]

    # bus compression, then remove sub rumble below the kick and bass with a steep 30 Hz high-pass
    mix = compress(mix)
    mix = signal.sosfilt(hp_sos(30, 4), mix, axis=0)

    # loudness: the search act around LOUD_TARGET_DB RMS, then soft clip
    loud = mix[int(ASK0 * SR):int(ASK1 * SR)]
    g = 10.0 ** ((LOUD_TARGET_DB - rms_db(loud)) / 20.0)
    mix = mix * g
    mix = soft_clip(mix)

    # final fade over the tail, then peak-normalize to -1 dBFS
    i_end = int(DUR * SR)
    fade = np.ones(N)
    fade[i_end:] = 0.5 + 0.5 * np.cos(np.pi * np.linspace(0.0, 1.0, N - i_end))
    mix *= fade[:, None]
    peak = float(np.max(np.abs(mix)))
    mix *= 10.0 ** (-1.0 / 20.0) / peak
    stats["loud_gain_db"] = 20.0 * math.log10(g)
    return mix.astype(np.float32)


def report(out, stats):
    acts = [("memories", MEM0, MEM_END), ("turn", TURN0, TURN_END), ("ask", ASK0, ASK1),
            ("brain", BRAIN0, BRAIN1), ("finale", FIN0, FIN1), ("tail", DUR, TOTAL)]
    print(f"duration: {out.shape[0] / SR:.3f} s  channels: {out.shape[1]}  sample rate: {SR}")
    pk = np.max(np.abs(out), axis=1)
    print(f"peak: {20 * math.log10(np.max(pk)):.2f} dBFS at {int(np.argmax(pk)) / SR:.3f} s")
    for name, a, b in acts:
        seg = out[int(a * SR):int(b * SR)]
        print(f"  RMS {name:10s} {a:5.1f}-{b:5.1f} s  {rms_db(seg):7.2f} dBFS  "
              f"peak {20 * math.log10(np.max(np.abs(seg)) + 1e-12):7.2f} dBFS")
    ask = out[int(ASK0 * SR):int(ASK1 * SR)]
    first = out[int(HIT_FINALE * SR):int((HIT_FINALE + 0.6) * SR)]
    chorus = out[int(HIT_FINALE * SR):int((bar(LAST_BAR)) * SR)]
    print(f"finale first beat vs search act: {rms_db(first) - rms_db(ask):+.2f} dB")
    print(f"finale chorus vs search act: {rms_db(chorus) - rms_db(ask):+.2f} dB")
    sil = out[int(SILENCE[0] * SR):int(SILENCE[1] * SR)]
    sil2 = out[int(FINALE_SILENCE[0] * SR):int(FINALE_SILENCE[1] * SR)]
    print(f"near silence {SILENCE[0]:.1f}-{SILENCE[1]:.1f} RMS: {rms_db(sil):.2f} dBFS   "
          f"{FINALE_SILENCE[0]:.1f}-{FINALE_SILENCE[1]:.1f} RMS: {rms_db(sil2):.2f} dBFS")
    print(f"DC offset: L {float(np.mean(out[:, 0])):.6f}  R {float(np.mean(out[:, 1])):.6f}")
    print(f"notes: piano {stats['piano_notes']}, beat-locked impacts {stats['beat_notes']}, "
          f"pile notes kept {stats['coll_notes']} (of {stats['act1_impacts']} in act 1), "
          f"after-turn clatter ticks {stats['clatter_ticks']} (of {stats['after_turn_impacts']})")
    print(f"events: {stats['events']}")
    print(f"loudness gain applied: {stats['loud_gain_db']:+.2f} dB")


def main():
    events = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    coll = json.loads(COLLISIONS_PATH.read_text(encoding="utf-8"))
    print(f"loaded {len(events)} events, {len(coll)} collisions, timeline {DUR:.1f} s")
    print("searches (start / enter / hero, from timeline and events): " +
          "; ".join(f"{k}:{SEARCH[k]['kind'][0]} {SEARCH[k]['start']:.1f}/{SEARCH[k]['enter']:.1f}/{SEARCH[k]['hero']:.1f}"
                    for k in sorted(SEARCH)))

    new_buses()
    stats = {"piano_notes": 0, "beat_notes": 0, "coll_notes": 0, "clatter_ticks": 0,
             "act1_impacts": 0, "after_turn_impacts": 0, "events": {}}
    act1_music(stats)
    collisions(coll, stats)
    ask_music()
    brain_music()
    finale_music()
    for i in range(1, LAST_BAR):
        cfg = pad_bar_cfg(i)
        if cfg is not None:
            pad_bar(i, cfg[0], cfg[1])
    event_cues(events, stats)
    print(f"composed in {time.time() - T_START:.1f} s")

    out = finalize(stats)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(WAV_PATH), SR, out)
    print(f"wrote {WAV_PATH}")
    report(out, stats)
    print(f"total time {time.time() - T_START:.1f} s")


if __name__ == "__main__":
    main()
