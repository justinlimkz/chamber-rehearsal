"""Turning recorded sustained notes into SF2 zones: tuning, long crossfaded loops, level matching.

Recordings of single notes (VSCO, Iowa, ...) are unlooped and a few seconds long. A sampler holds a note by looping;
MuseScore's loops are ~0.2 s, which repeats one vibrato cycle and buzzes. Here each note gets the longest loop its
steady part allows (up to MAX_LOOP_S), its end crossfaded into the audio before its start so the jump is seamless.
"""
from __future__ import annotations

import numpy as np

from sf2 import Zone

MAX_LOOP_S = 3.0  # longer adds size, and past a few seconds nobody hears the repeat
MIN_LOOP_S = 0.6
XFADE_S = 0.25  # a vibrato cycle or more, so the crossfade blends whole wobbles
LEVEL_DBFS = -12.0  # steady-part RMS of every zone, so neighbouring notes match


def load_mono(path) -> tuple[np.ndarray, int]:
    import soundfile as sf
    x, sr = sf.read(str(path), dtype="float64", always_2d=True)
    return x.mean(1), sr


def f0_cents(x: np.ndarray, sr: int, key: int) -> float:
    """Median deviation (cents) of the fundamental from `key`'s equal-tempered pitch, over the given audio."""
    ref = 440 * 2 ** ((key - 69) / 12)
    n = 1 << int(np.ceil(np.log2(sr * 0.2)))
    w, devs = np.hanning(n), []
    for i in range(0, len(x) - n, n // 2):
        X = np.abs(np.fft.rfft(x[i:i + n] * w, 4 * n))
        f = np.fft.rfftfreq(4 * n, 1 / sr)
        m = (f > ref * 2 ** (-1 / 12 * 0.6)) & (f < ref * 2 ** (1 / 12 * 0.6))
        if not m.any():
            continue
        k = np.argmax(X * m)
        a, b, c = np.log(X[k - 1:k + 2] + 1e-12)
        d = 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c else 0
        devs.append(1200 * np.log2((k + d) * sr / (4 * n) / ref))
    return float(np.median(devs)) if devs else 0.0


def envelope(x: np.ndarray, sr: int, win_s: float = 0.05) -> np.ndarray:
    h = max(1, int(win_s * sr))
    c = np.concatenate([[0.0], np.cumsum(x * x)])  # moving average by cumulative sum: long files, long windows
    i = np.arange(len(x))
    lo, hi = np.clip(i - h // 2, 0, len(x)), np.clip(i + h - h // 2, 0, len(x))
    return np.sqrt(np.maximum(c[hi] - c[lo], 0) / h) + 1e-9


def steady(x: np.ndarray, sr: int) -> tuple[int, int]:
    """Frames [a, b) of the note's sustained part: after the attack has settled, before it fades or stops."""
    e = envelope(x, sr, 0.1)
    peak_at = int(np.argmax(e[: int(len(e) * 0.6)]))
    ref = np.median(e[peak_at:]) if peak_at < len(e) else e.max()
    on = np.nonzero(e > 0.5 * e.max())[0]
    a = max(int(0.25 * sr), on[0] + int(0.15 * sr)) if len(on) else int(0.25 * sr)
    above = np.nonzero(e[a:] > 0.6 * ref)[0]
    b = a + above[-1] if len(above) else len(x)
    return a, min(b - int(0.1 * sr), len(x))


def make_loop(x: np.ndarray, sr: int) -> tuple[np.ndarray, int, int]:
    """Loop points in the steady part, the end crossfaded into what precedes the start; returns (audio, s, e)."""
    a, b = steady(x, sr)
    xf = min(int(XFADE_S * sr), max(1, (b - a) // 3))  # a short recording gets a shorter crossfade
    # the loop starts soon after the attack and the rest is cut off: a 3 s loop is plenty, and every second kept is
    # app size; the start may move 0.4 s to find a good match
    lo = a + xf
    e = min(b, lo + int((MAX_LOOP_S + 0.4) * sr))
    hi = max(lo + 1, min(lo + int(0.4 * sr), e - int(MIN_LOOP_S * sr)))
    # start where the audio before it best resembles the audio before the end (the crossfade blends the two)
    win = int(0.03 * sr)
    tail = x[e - win:e]
    best, s = -2.0, lo
    for c in range(lo, hi, max(1, int(0.002 * sr))):
        seg = x[c - win:c]
        r = float(np.dot(seg, tail) / (np.linalg.norm(seg) * np.linalg.norm(tail) + 1e-12))
        if r > best:
            best, s = r, c
    y = x[:e].copy()
    # even out the level across the loop so the jump doesn't step (a slow swell inside it is lost; fine for a hold)
    env = envelope(y, sr, 0.3)
    lvl = np.median(env[s:e])
    g = np.ones(len(y))
    g[s - xf:e] = np.clip(lvl / env[s - xf:e], 0.5, 2.0)
    ramp = np.linspace(1, 0, xf)
    g[s - 2 * xf:s - xf] = ramp + (1 - ramp) * g[s - xf] if s - 2 * xf >= 0 else g[s - 2 * xf:s - xf]
    y *= g
    t = np.linspace(0, 1, xf)
    fade_in, fade_out = (t, 1 - t) if best > 0.7 else (np.sin(t * np.pi / 2), np.cos(t * np.pi / 2))
    y[e - xf:e] = y[e - xf:e] * fade_out + y[s - xf:s] * fade_in
    return y, s, e


def zone(path, key: int, lo: int, hi: int, name: str = "", vel=(0, 127), retune: bool = True) -> Zone:
    """A looped, level-matched, tuned zone from a recording of `key`, played for keys lo..hi."""
    x, sr = load_mono(path)
    # start 10 ms before the note reaches -20 dB of its sustain: some recordings (VSCO's p violin) have a quiet
    # lead-in that would make every note late
    a, b = steady(x, sr)
    env = envelope(x, sr, 0.02)
    ref = np.median(env[a:b])
    x = x[max(0, int(np.argmax(env > 0.1 * ref)) - int(0.01 * sr)):]
    y, s, e = make_loop(x, sr)
    a, _ = steady(x, sr)
    # a joined-on note starts where the bow has the note going (70% of its sustain level), staying before the loop
    env = envelope(y, sr, 0.02)
    skip = min(int(np.argmax(env > 0.7 * np.median(env[a:e]))), s - int(0.05 * sr))
    rms = np.sqrt(np.mean(y[a:e] ** 2))
    y *= 10 ** (LEVEL_DBFS / 20) / rms
    peak = np.abs(y).max()
    if peak > 0.99:  # a spiky attack: the zone comes out a little quieter rather than clipped
        y *= 0.99 / peak
    pcm = np.clip(np.round(y * 32767), -32768, 32767).astype(np.int16)
    cents = -round(f0_cents(x[s:e], sr, key)) if retune else 0
    return Zone(pcm=pcm, rate=sr, root=key, lo=lo, hi=hi, loop=(s, e), cents=max(-50, min(50, cents)), vel=vel,
                name=name, legato_skip=max(skip, int(0.03 * sr)))


def spread(keys: list[int], low: int, high: int) -> list[tuple[int, int, int]]:
    """(key, lo, hi) for sampled keys, each covering up to halfway to its neighbours; the ends reach low/high."""
    keys = sorted(keys)
    out = []
    for i, k in enumerate(keys):
        lo = low if i == 0 else (keys[i - 1] + k) // 2 + 1
        hi = high if i == len(keys) - 1 else (k + keys[i + 1]) // 2
        out.append((k, lo, hi))
    return out


NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def key_of(name: str) -> int:
    """MIDI key of a note name like 'A4', 'Bb3', 'C#5', 'Gs2' (middle C = C4 = 60)."""
    import re
    m = re.fullmatch(r"([A-G])(#|s|b)?(-?\d)", name)
    assert m, name
    acc = {"#": 1, "s": 1, "b": -1}.get(m[2] or "", 0)
    return 12 * (int(m[3]) + 1) + NOTE[m[1]] + acc


def octave_check(x: np.ndarray, sr: int, key: int) -> int:
    """`key` moved by octaves to where the recording's fundamental is (some sets name notes an octave off)."""
    a, b = steady(x, sr)
    seg = x[a:b][: sr * 2]
    n = 1 << int(np.ceil(np.log2(len(seg))))
    X = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n))
    f = np.fft.rfftfreq(n, 1 / sr)
    peak = X.max()

    def strength(k):
        hz = 440 * 2 ** ((k - 69) / 12)
        m = (f > hz * 2 ** (-0.5 / 12)) & (f < hz * 2 ** (0.5 / 12))
        return X[m].max() / peak if m.any() else 0

    # the lowest octave with a clear partial; a real fundamental also has its octave above
    for k in (key - 24, key - 12, key, key + 12, key + 24):
        if k > 20 and strength(k) > 0.03 and strength(k + 12) > 0.03:
            return k
    return key
