r"""Soundtrack for the launch video, synthesised from scratch (no samples, no licences): out/music.wav.

  uv run --no-project --with numpy --with scipy python music.py

120 BPM, one bar = 2 s, so every scene cut in timeline.json lands on the grid. A warm IV-V-iii-vi loop in C
(Fmaj7, G6, Em7, Am9) carries the piece; drums come in exactly when Alt+Space is pressed, drop out for the
privacy scene and come back for the feature montage. UI sounds (key ticks per typed character, result pops,
the mic) are placed from the same timeline as the visuals, so picture and sound stay in sync by construction.
"""
from __future__ import annotations

import json
import os
import wave

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
TL = json.load(open(os.path.join(HERE, "timeline.json"), encoding="utf-8"))
SR = 48000
DUR = TL["duration"] + 0.5
N = int(DUR * SR)
BEAT = 60 / TL["bpm"]
BAR = 4 * BEAT
rng = np.random.default_rng(7)

L = np.zeros(N, np.float64)
R = np.zeros(N, np.float64)
buses = {name: (np.zeros(N), np.zeros(N)) for name in ("reverb", "delay")}


def mtof(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def place(sig, t0, gain=1.0, pan=0.0, send_rev=0.0, send_dly=0.0, out=None):
    """Add a mono signal at time t0 with equal-power pan and effect sends."""
    i = int(t0 * SR)
    if i >= N:
        return
    sig = sig[: N - i] * gain
    a = (pan + 1) * np.pi / 4
    gl, gr = np.cos(a), np.sin(a)
    tl, tr = out if out else (L, R)
    tl[i:i + len(sig)] += sig * gl
    tr[i:i + len(sig)] += sig * gr
    if send_rev:
        buses["reverb"][0][i:i + len(sig)] += sig * gl * send_rev
        buses["reverb"][1][i:i + len(sig)] += sig * gr * send_rev
    if send_dly:
        buses["delay"][0][i:i + len(sig)] += sig * gl * send_dly
        buses["delay"][1][i:i + len(sig)] += sig * gr * send_dly


def env(n, a, d, s, r, hold):
    """ADSR in samples: attack a, decay d to sustain s, hold, release r (all seconds)."""
    A, D, H, Rl = int(a * SR), int(d * SR), int(hold * SR), int(r * SR)
    e = np.concatenate([np.linspace(0, 1, max(A, 1)), np.linspace(1, s, max(D, 1)),
                        np.full(max(H - A - D, 0), s), np.linspace(s, 0, max(Rl, 1))])
    return e[:n] if len(e) >= n else np.pad(e, (0, n - len(e)))


def saw(f, n, phase=0.0):
    t = np.arange(n) / SR
    return 2 * ((f * t + phase) % 1.0) - 1


def lp(x, fc, order=2):
    sos = signal.butter(order, min(fc, SR * 0.45), "low", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def hp(x, fc, order=2):
    sos = signal.butter(order, fc, "high", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def bp(x, lo, hi, order=2):
    sos = signal.butter(order, [lo, hi], "band", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


# ---------------------------------------------------------------- harmony
CHORDS = [  # (bass root midi, voicing midi) — Fmaj7, G6, Em7, Am9
    (41, [53, 57, 60, 64, 69]),
    (43, [55, 59, 62, 64, 71]),
    (40, [52, 55, 59, 62, 67]),
    (45, [57, 60, 64, 67, 71]),
]
FINAL = (36, [48, 55, 60, 64, 67, 71, 74])  # Cmaj9 to end on


def chord_at(t):
    return CHORDS[int(t // BAR) % 4]


def sec(name):
    return TL["scenes"][name]


# ---------------------------------------------------------------- instruments
def pad(notes, t0, dur, gain, cutoff):
    n = int((dur + 1.2) * SR)
    out = np.zeros(n)
    for m in notes:
        f = mtof(m)
        for det in (-0.09, 0.0, 0.11):
            out += saw(f * 2 ** (det / 12), n, rng.random())
    out = hp(lp(out, cutoff, 2), 170) * env(n, 0.45, 0.4, 0.8, 1.1, dur)  # leave the low end to the bass
    return out * gain / len(notes)


def bass_note(m, dur, gain):
    n = int((dur + 0.08) * SR)
    t = np.arange(n) / SR
    f = mtof(m)
    x = np.sin(2 * np.pi * f * t) + 0.35 * np.tanh(2.5 * np.sin(2 * np.pi * f * t))
    x = lp(x + 0.25 * saw(f, n), 900) * env(n, 0.005, 0.12, 0.7, 0.06, dur)
    return x * gain


def pluck(m, gain, decay=0.28):
    n = int(0.9 * SR)
    t = np.arange(n) / SR
    f = mtof(m)
    x = saw(f, n, rng.random()) * 0.6 + np.sin(2 * np.pi * f * t)
    # brightness falls with the note: a one-pole sweep approximated by two filtered copies
    bright, dark = lp(x, 5200), lp(x, 900)
    k = np.exp(-t / 0.07)
    return (bright * k + dark * (1 - k)) * np.exp(-t / decay) * gain


def bell(m, gain, decay=1.4):
    n = int(2.5 * SR)
    t = np.arange(n) / SR
    f = mtof(m)
    mod = np.sin(2 * np.pi * f * 3.5 * t) * 2.2 * np.exp(-t / 0.4)
    return np.sin(2 * np.pi * f * t + mod) * np.exp(-t / decay) * gain * env(n, 0.004, 0.05, 1, 0.1, 2.3)


def kick(gain=1.0):
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    f = 46 + 110 * np.exp(-t * 28)
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = np.sin(ph) * np.exp(-t * 6.5)
    x[: int(0.004 * SR)] += rng.standard_normal(int(0.004 * SR)) * 0.25
    return np.tanh(1.6 * x) * gain


def clap(gain=1.0):
    n = int(0.35 * SR)
    t = np.arange(n) / SR
    noise = bp(rng.standard_normal(n), 900, 4200)
    e = np.zeros(n)
    for k, off in enumerate((0, 0.011, 0.022)):
        i = int(off * SR)
        e[i:] += np.exp(-(t[: n - i]) / (0.012 if k < 2 else 0.12))
    return noise * e * gain * 0.6


def hat(open_=False, gain=1.0):
    n = int((0.25 if open_ else 0.06) * SR)
    t = np.arange(n) / SR
    return hp(rng.standard_normal(n), 7500) * np.exp(-t / (0.09 if open_ else 0.018)) * gain


def riser(length, gain):
    n = int(length * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    out = np.zeros(n)
    blk = int(0.02 * SR)
    for i in range(0, n, blk):  # band sweeps up
        k = i / n
        c = 300 * (1 + 40 * k ** 2)
        out[i:i + blk] = bp(noise[max(0, i - 2048):i + blk], c, min(c * 2.2, 20000))[-len(out[i:i + blk]):]
    return out * (t / length) ** 2.2 * gain


def impact(gain):
    n = int(3.0 * SR)
    t = np.arange(n) / SR
    f = 52 * np.exp(-t * 0.6)
    boom = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.45)
    crash = lp(rng.standard_normal(n), 6000) * np.exp(-t / 0.5) * 0.35
    return (np.tanh(1.8 * boom) + crash) * gain


def tick(gain):
    n = int(0.03 * SR)
    t = np.arange(n) / SR
    x = hp(rng.standard_normal(n), 2500) * np.exp(-t / 0.004) + np.sin(2 * np.pi * 3100 * t) * np.exp(-t / 0.006) * 0.4
    return x * gain


def thock(gain):
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    return (lp(rng.standard_normal(n), 1500) * np.exp(-t / 0.012) * 0.8 + np.sin(2 * np.pi * 170 * t) * np.exp(-t / 0.03)) * gain


def blip(m, gain, length=0.18):
    n = int(length * SR)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * mtof(m) * t) * np.exp(-t / (length / 4)) * env(n, 0.003, 0.01, 1, 0.02, length) * gain


# ---------------------------------------------------------------- arrangement
hook, logo, demo, privacy, montage, cta = (sec(k) for k in ("hook", "logo", "demo", "privacy", "montage", "cta"))
drums_on = [(demo[0], privacy[0]), (montage[0], cta[0])]
kicks = []

# pads: every bar, cutoff opens through the intro, darker in the breakdown
for b in range(int(TL["duration"] // BAR)):
    t0 = b * BAR
    if t0 >= cta[0]:
        break
    root, notes = chord_at(t0)
    cut = 500 + 2200 * min(1, t0 / hook[1]) if t0 < hook[1] else (1400 if privacy[0] <= t0 < montage[0] else 2600)
    soft = 0.7 if privacy[0] <= t0 < montage[0] else 1.0
    place(pad(notes, t0, BAR, 0.5 * soft, cut), t0, pan=0.0, send_rev=0.45)

# final section: Fmaj7 (bar 24), G6 (bar 25), then a long Cmaj9 to the end
for i, (t0, (root, notes), dur) in enumerate([(cta[0], CHORDS[0], BAR), (cta[0] + BAR, CHORDS[1], BAR),
                                              (cta[0] + 2 * BAR, FINAL, TL["duration"] - cta[0] - 2 * BAR)]):
    place(pad(notes, t0, dur, 0.6, 3000), t0, send_rev=0.55)
    place(bass_note(root if root > 38 else root + 12, dur, 0.45), t0, send_rev=0.05)

# drums, bass and arp in the grooves
for a, b in drums_on:
    t = a
    step = 0
    while t < b - 1e-6:
        beat_in_bar = step % 16
        root, notes = chord_at(t)
        if beat_in_bar % 4 == 0:
            place(kick(1.0), t)
            kicks.append(t)
        if beat_in_bar in (4, 12):
            place(clap(0.65), t, send_rev=0.25)
        if beat_in_bar % 4 == 2:
            place(hat(True, 0.18), t, pan=0.25, send_rev=0.05)
        place(hat(False, 0.08 if step % 2 else 0.13), t, pan=-0.2)
        # bass: 8ths with an octave pop on the off-beats
        if step % 2 == 0:
            m = root + (12 if beat_in_bar in (6, 14) else 0)
            place(bass_note(m, BEAT / 2 * 0.9, 0.42), t)
        # arp 16ths over the chord, two octaves, ping-ponged
        seq = [0, 2, 4, 1, 3, 4, 2, 1]
        m = notes[seq[step % 8] % len(notes)] + 12
        bright = 1.0 if a >= montage[0] else 0.8
        place(pluck(m + (12 if a >= montage[0] and step % 8 == 4 else 0), 0.16 * bright), t,
              pan=-0.45 if step % 2 else 0.45, send_dly=0.35, send_rev=0.15)
        step += 1
        t = a + step * BEAT / 4
        if t >= b:
            break

# breakdown: soft arp only, filtered
t = privacy[0]
step = 0
while t < montage[0] - 1e-6:
    root, notes = chord_at(t)
    if step % 2 == 0:
        m = notes[[0, 2, 4, 3][(step // 2) % 4] % len(notes)] + 12
        place(lp(pluck(m, 0.12, 0.4), 2500), t, pan=0.3 if step % 4 else -0.3, send_dly=0.4, send_rev=0.4)
    step += 1
    t = privacy[0] + step * BEAT / 4

# bell motif in the montage and the end card
motif = [(0, 76), (0.75, 79), (1.5, 81), (2.0, 84), (3.0, 83), (3.5, 79)]
for base in (montage[0], montage[0] + 2 * BAR):
    for dt, m in motif:
        place(bell(m, 0.10), base + dt * BEAT, pan=0.1, send_rev=0.4, send_dly=0.25)
for dt, m in [(0, 72), (1, 76), (2, 79), (3, 84)]:
    place(bell(m, 0.09, 2.2), cta[0] + 2 * BAR + dt * BEAT * 0.5, send_rev=0.6, send_dly=0.3)

# transitions
for tw in TL["whoosh"]:
    place(riser(1.6, 0.16), tw + 0.25 - 1.6, pan=0.0, send_rev=0.3)
for ti in TL["impact"]:
    place(impact(0.42), ti, send_rev=0.5)
    kicks.append(ti)

# UI sounds from the same timeline as the picture
for tk in TL["keys"]:
    place(thock(0.45), tk, send_rev=0.1)
place(blip(84, 0.10, 0.3), 10.75, send_rev=0.4)            # launcher opens
cps = TL["typing_cps"]
for q in TL["queries"]:
    if q.get("voice"):
        place(blip(84, 0.12), q["start"], send_rev=0.3)
        place(blip(91, 0.12), q["start"] + 0.09, send_rev=0.3)
        place(blip(88, 0.10), q["start"] + 1.5, send_rev=0.3)
        appear = q["start"] + 1.5 + 0.14 * len(q["text"].split()) + 0.25
    else:
        for i in range(1, len(q["text"]) + 1):
            place(tick(0.10 + 0.04 * rng.random()), q["start"] + i / cps, pan=rng.uniform(-0.3, 0.3))
        appear = q["start"] + len(q["text"]) / cps + 0.25
    place(blip(88, 0.09, 0.25), appear, send_rev=0.35, send_dly=0.2)
for i in range(8):  # montage tiles
    place(blip([72, 74, 76, 79, 81, 84, 86, 88][i], 0.07, 0.2), montage[0] + 0.5 + i * 0.14, pan=-0.6 + i * 0.17, send_rev=0.3)

# ---------------------------------------------------------------- effects and mix
# sidechain: everything but drums ducks under each kick
duck = np.ones(N)
t = np.arange(int(0.4 * SR)) / SR
shape = 1 - 0.45 * np.exp(-t / 0.11)
for tk in kicks:
    i = int(tk * SR)
    j = min(N, i + len(shape))
    duck[i:j] = np.minimum(duck[i:j], shape[: j - i])

# ping-pong delay, dotted eighth, 3 repeats
d = int(BEAT * 0.75 * SR)
dl, dr = buses["delay"]
for k in range(1, 4):
    g = 0.45 ** k
    src = dl if k % 2 else dr
    if k % 2:
        R[d * k:] += lp(src[: N - d * k], 4000) * g
    else:
        L[d * k:] += lp(src[: N - d * k], 4000) * g

# convolution reverb: decorrelated decaying noise, darkened
ir_n = int(2.8 * SR)
ti = np.arange(ir_n) / SR
irL = lp(rng.standard_normal(ir_n), 5000) * np.exp(-ti / 0.7)
irR = lp(rng.standard_normal(ir_n), 5000) * np.exp(-ti / 0.7)
irL[: int(0.02 * SR)] = 0
irR[: int(0.02 * SR)] = 0
rl, rr = buses["reverb"]
L += signal.fftconvolve(rl, irL)[:N] * 0.09
R += signal.fftconvolve(rr, irR)[:N] * 0.09

L *= duck
R *= duck

# master: low cut, gentle glue, soft clip, fades, normalise
L, R = hp(L, 28), hp(R, 28)
fade_in = np.clip(np.arange(N) / (0.05 * SR), 0, 1)
end = TL["duration"]
fade_out = np.clip((end - np.arange(N) / SR) / 1.6, 0, 1) ** 1.5
for ch in (L, R):
    ch *= fade_in * fade_out
peak = max(np.abs(L).max(), np.abs(R).max())
L, R = L / peak * 1.25, R / peak * 1.25
L, R = np.tanh(L) / np.tanh(1.25), np.tanh(R) / np.tanh(1.25)
L, R = L * 0.89, R * 0.89  # -1 dBFS
rms = np.sqrt(np.mean(np.concatenate([L, R]) ** 2))
print(f"rms {20 * np.log10(rms):.1f} dBFS, peak {20 * np.log10(max(np.abs(L).max(), np.abs(R).max())):.1f} dBFS")

os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
pcm = (np.stack([L, R], 1) * 32767).astype("<i2")
with wave.open(os.path.join(HERE, "out", "music.wav"), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
print("wrote out/music.wav", round(N / SR, 1), "s")
