"""Real vs ours, measured the same way on both sides (tone/compare/synth.py + separate.py must have run).

Sources per excerpt: the mixes (synth/<name>/real.wav, mix.wav), Demucs' strings stems of both (sep/real-<name>,
sep/ours-<name>) and, as a check on what the separation does, our true strings (synth/<name>/strings.wav).
Note times on both sides come from the score through the real recording's beat map (align/<name>.json); ours follows
the same map through the engine, so the windows line up (the engine's own lag is measured separately).

Per held string note, harmonics are tracked in an 8192-point STFT (10 ms hop) around h * f0, skipping any harmonic
within 60 cents of a harmonic of another string note sounding at the time; from them: level, brightness (harmonic
centroid), pitch (cents) -> envelope shape, vibrato, intonation, brightness-vs-level within the note.

Run: uv run --no-project --with numpy --with scipy --with soundfile --with librosa python -I tone/compare/measure.py
Output: build/tone/compare/measure.json and a printed table.
"""
import json
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build/tone/compare"
SCORE = json.loads((ROOT / "ios/Bundles/schumann-op47-iii/score.json").read_text())
STRINGS = ("Violin", "Viola", "Violoncello")
SR, NFFT, HOP = 48000, 8192, 480
FPS = SR / HOP
WINDOWS = {"Theme": (0.0, 44.0), "Chorale": (2.0, 46.0)}


def load(p, mono=True):
    x, sr = sf.read(str(p), dtype="float64", always_2d=True)
    assert sr == SR
    return x.mean(1) if mono else x


def tmap_of(name):
    al = json.loads((OUT / "align" / f"{name}.json").read_text())
    b = np.array([x[0] for x in al["beats"]]); t = np.array([x[1] for x in al["beats"]])
    k = 0
    while k + 1 < len(t) and t[k + 1] - t[k] < 0.4:
        k += 1
    b, t = b[k:], t[k:]
    w0, w1 = WINDOWS[name]
    keep = t <= w1 - 0.5  # beats inside the window
    return (lambda beat: np.interp(beat, b, t) - w0), (b[0], b[keep][-1])


def score_notes(name):
    tm, (b0, b1) = tmap_of(name)
    rows = []
    for part in STRINGS:
        for on, dur, p in SCORE["parts"][part]:
            if dur > 0 and b0 <= on and on + dur <= b1:
                rows.append(dict(part=part, pitch=p, beat=on, dur=dur, t0=float(tm(on)), t1=float(tm(on + dur))))
    return rows, tm, (b0, b1)


def hz(p, tune=0.0):
    return 440 * 2 ** ((p - 69 + tune / 100) / 12)


class Spec:
    def __init__(self, y):
        self.S = np.abs(librosa.stft(y, n_fft=NFFT, hop_length=HOP, window="hann")) + 1e-12
        self.f = librosa.fft_frequencies(sr=SR, n_fft=NFFT)

    def peak(self, fc, cents, a, b):
        """Per frame a..b: (freq, amplitude) of the largest peak within +-cents of fc (parabolic interpolation)."""
        lo, hi = np.searchsorted(self.f, [fc * 2 ** (-cents / 1200), fc * 2 ** (cents / 1200)])
        lo, hi = max(lo, 1), min(hi + 1, len(self.f) - 1)
        if hi - lo < 1:
            return None
        M = np.log(self.S[lo - 1:hi + 1, a:b])
        k = np.argmax(M[1:-1], axis=0) + 1
        c = np.arange(M.shape[1])
        al, be, ga = M[k - 1, c], M[k, c], M[k + 1, c]
        d = 0.5 * (al - ga) / np.where(al - 2 * be + ga == 0, -1e-9, al - 2 * be + ga)
        d = np.clip(d, -0.5, 0.5)
        freq = (lo - 1 + k + d) * SR / NFFT
        amp = np.exp(be - 0.25 * (al - ga) * d)
        return freq, amp


def clean_harmonics(n, notes, hmax=10):
    others = [m for m in notes if m is not n and m["t0"] < n["t1"] and m["t1"] > n["t0"]]
    out = []
    for h in range(1, hmax + 1):
        f = h * hz(n["pitch"])
        if f > 9000:
            break
        bad = any(abs(1200 * np.log2(f / (k * hz(m["pitch"])))) < 60 for m in others for k in range(1, 16))
        if not bad:
            out.append(h)
    return out


def note_tracks(spec, n, notes, tune):
    a, b = int((n["t0"] + 0.12) * FPS), min(int((n["t1"] - 0.08) * FPS), spec.S.shape[1])
    if a < 0 or b - a < 10:
        return None
    hs = clean_harmonics(n, notes)
    if len(hs) < 2:
        return None
    amps, freqs = {}, {}
    for h in hs:
        r = spec.peak(h * hz(n["pitch"], tune), 70, a, b)
        if r is None:
            continue
        freqs[h], amps[h] = r
    if len(amps) < 2:
        return None
    A = np.array([amps[h] ** 2 for h in amps]); H = np.array(list(amps))
    level = 10 * np.log10(A.sum(0) + 1e-20)
    bright = (H[:, None] * A).sum(0) / A.sum(0)
    lowh = [h for h in amps if h <= 6] or list(amps)
    hbest = max(lowh, key=lambda h: amps[h].mean())
    cents = 1200 * np.log2(freqs[hbest] / (hbest * hz(n["pitch"], tune)))
    return dict(level=level, bright=bright, cents=cents, t=(np.arange(a, b)) / FPS)


def vibrato(c):
    """(rate Hz, peak-to-peak extent cents, regular?) of a cents track at FPS."""
    if len(c) < int(0.7 * FPS):
        return None
    c = c - np.convolve(c, np.ones(31) / 31, mode="same")
    c = c[15:-15]
    if len(c) < 40:
        return None
    sos = butter(2, [3, 10], btype="band", fs=FPS, output="sos")
    v = sosfiltfilt(sos, c)
    P = np.abs(np.fft.rfft(c * np.hanning(len(c)), 4096)) ** 2
    fr = np.fft.rfftfreq(4096, 1 / FPS)
    band = (fr >= 3) & (fr <= 10)
    rate = fr[band][np.argmax(P[band])]
    frac = P[(fr >= rate - 1) & (fr <= rate + 1)].sum() / P[(fr >= 1) & (fr <= 20)].sum()
    ext = 2 * np.sqrt(2) * v.std()
    return float(rate), float(ext), bool(ext >= 15 and frac > 0.35)


def frame_db(y, win=0.05):
    h = int(win * SR)
    n = len(y) // h
    return 10 * np.log10(np.mean(y[:n * h].reshape(n, h) ** 2, 1) + 1e-12)


def per_beat_levels(y, tm, beats):
    out = []
    for b in beats:
        a, e = int(tm(b) * SR), int(tm(b + 1) * SR)
        if 0 <= a < e <= len(y):
            out.append(10 * np.log10(np.mean(y[a:e] ** 2) + 1e-12))
        else:
            out.append(np.nan)
    return np.array(out)


def string_onset(spec, n, notes, tune):
    """Half-rise time of the note's clean harmonics within +-0.25 s of the expected onset (None if unclear)."""
    hs = clean_harmonics(n, notes)[:6]
    if len(hs) < 2:
        return None
    a, b = int((n["t0"] - 0.25) * FPS), int((n["t0"] + 0.3) * FPS)
    if a < 0 or b >= spec.S.shape[1]:
        return None
    L = sum(spec.peak(h * hz(n["pitch"], tune), 40, a, b)[1] ** 2 for h in hs)
    L = 10 * np.log10(L + 1e-20)
    L = np.convolve(L, np.ones(3) / 3, mode="same")
    c = int(0.25 * FPS)
    m = int(np.argmin(L[: c + 5]))  # the trough before (or at) the expected onset
    top = L[m:].max()
    if top - L[m] < 6:
        return None  # no clear rise: legato from an overlapping note, or masked
    i = m + int(np.argmax(L[m:] > L[m] + 0.5 * (top - L[m])))
    return (a + i) / FPS


def analyse(name):
    notes, tm, (b0, b1) = score_notes(name)
    src = {"real": OUT / "sep" / f"real-{name}" / "strings.wav", "ours": OUT / "sep" / f"ours-{name}" / "strings.wav",
           "ours-true": OUT / "synth" / name / "strings.wav"}
    mixes = {"real": OUT / "synth" / name / "real.wav", "ours": OUT / "synth" / name / "mix.wav"}
    pianos = {"real": OUT / "sep" / f"real-{name}" / "piano.wav", "ours": OUT / "sep" / f"ours-{name}" / "piano.wav",
              "ours-true": OUT / "sep" / f"ours-{name}" / "piano.wav"}
    beats = np.arange(np.ceil(b0), np.floor(b1))
    sounding = np.array([any(m["beat"] <= b < m["beat"] + m["dur"] for m in notes) for b in beats])
    res = {}
    for side, p in src.items():
        y = load(p)
        tune = 100 * librosa.estimate_tuning(y=librosa.resample(load(pianos[side]), orig_sr=SR, target_sr=22050), sr=22050)
        spec = Spec(y)
        r = dict(tuning_cents=round(float(tune), 1))
        # 1. dynamics: per-beat level of the strings where they play
        lv = per_beat_levels(y, tm, beats)[sounding]
        lv = lv[np.isfinite(lv)]
        r["beat_level_p90_p10_db"] = round(float(np.percentile(lv, 90) - np.percentile(lv, 10)), 1)
        r["beat_levels"] = [round(float(v), 1) for v in per_beat_levels(y, tm, beats)]
        # 2. brightness: over all frames (centroid vs level), and within held notes (pitch fixed)
        fdb = frame_db(y, 0.05)
        cen = librosa.feature.spectral_centroid(y=y, sr=SR, n_fft=2400, hop_length=2400)[0][:len(fdb)]
        ok = fdb > fdb.max() - 30
        r["centroid_hz_median"] = round(float(np.median(cen[ok])), 0)
        r["frames_oct_per_10db"] = round(float(np.polyfit(fdb[ok], np.log2(cen[ok]), 1)[0] * 10), 3)
        cands = [(n, note_tracks(spec, n, notes, tune)) for n in notes if n["t1"] - n["t0"] >= 0.6]
        cands = [(n, tr) for n, tr in cands if tr is not None]
        top = max(tr["level"].max() for _, tr in cands)
        acr = []  # (note level, pitch, harmonic centroid) of each note, for brightness across notes
        tracks, vib, swell, decay, peakpos, within, cents_med, am = [], [], [], [], [], [], [], []
        for n, tr in cands:
            L, B, C = tr["level"], tr["bright"], tr["cents"]
            if L.max() < top - 30:
                continue  # too quiet to trust
            Ls = np.convolve(L, np.ones(9) / 9, mode="same")
            cents_med.append(float(np.median(C)))
            acr.append((float(np.percentile(L, 80)), n["pitch"], float(np.median(B))))
            if len(L) >= int(0.9 * FPS):
                v = vibrato(C)
                if v:
                    vib.append(v)
                q = len(Ls) // 4
                swell.append(float(Ls[4:].max() - Ls[q]))  # rise after the first quarter
                decay.append(float(Ls[-6:].mean() - Ls[4:].max()))  # end vs peak
                peakpos.append(float((np.argmax(Ls[4:-4]) + 4) / len(Ls)))
                if Ls.max() - Ls.min() > 3:
                    within.append(float(np.polyfit(Ls, B, 1)[0] * 10))  # harmonic centroid per 10 dB
                d = L - np.convolve(L, np.ones(31) / 31, mode="same")
                am.append(float(d[15:-15].std()))
            tracks.append(n)
        vr = [v for v in vib if v[2]]
        if len(acr) >= 6:
            X = np.array([[a_[0], a_[1], 1.0] for a_ in acr]); Y = np.array([a_[2] for a_ in acr])
            r["across_notes_hcentroid_per_10db"] = round(float(np.linalg.lstsq(X, Y, rcond=None)[0][0] * 10), 3)
        r.update(notes_measured=len(tracks), long_notes=len(swell),
                 vibrato_share=round(len(vr) / max(1, len(vib)), 2),
                 vibrato_rate_hz=round(float(np.median([v[0] for v in vr])), 1) if vr else None,
                 vibrato_rate_sd=round(float(np.std([v[0] for v in vr])), 2) if len(vr) > 2 else None,
                 vibrato_extent_cents=round(float(np.median([v[1] for v in vr])), 0) if vr else None,
                 pitch_wobble_cents=round(float(np.median([v[1] for v in vib])), 0) if vib else None,
                 swell_db=round(float(np.median(swell)), 1), end_vs_peak_db=round(float(np.median(decay)), 1),
                 peak_position=round(float(np.median(peakpos)), 2),
                 level_ripple_db=round(float(np.median(am)), 2),
                 within_note_hcentroid_per_10db=round(float(np.median(within)), 3) if within else None,
                 within_n=len(within),
                 intonation_sd_cents=round(float(np.std(cents_med)), 1),
                 intonation_mean_cents=round(float(np.mean(cents_med)), 1))
        # joins: the strings' level dip where a part moves to its next note and nothing else starts
        e20 = frame_db(y, 0.02)
        onsets_all = [m["t0"] for m in notes]
        dips = []
        for n in notes:
            nxt = [m for m in notes if m["part"] == n["part"] and abs(m["beat"] - (n["beat"] + n["dur"])) < 1e-3
                   and m["pitch"] != n["pitch"]]
            if not nxt:
                continue
            t = n["t1"]
            if sum(abs(o - t) < 0.3 for o in onsets_all) > 1 or t < 0.5 or t > len(y) / SR - 0.5:
                continue
            k = int(t / 0.02)
            mid = e20[k - 5:k + 6].min()
            around = np.concatenate([e20[k - 17:k - 7], e20[k + 7:k + 17]]).mean()
            dips.append(float(mid - around))
        r["join_dip_db"] = round(float(np.median(dips)), 1) if dips else None
        r["join_n"] = len(dips)
        # 3. timing: string onsets vs the piano's at the same beat; spread between string parts starting together
        pspec_y = load(pianos[side])
        pon = librosa.onset.onset_detect(y=pspec_y, sr=SR, hop_length=480, units="time", backtrack=False)
        piano_beats = {round(n_[0], 3) for n_ in SCORE["parts"]["Piano"] if n_[1] > 0}
        diffs, spreads, by_beat = [], [], {}
        for n in notes:
            if round(n["beat"], 3) not in piano_beats:
                continue
            so = string_onset(spec, n, notes, tune)
            if so is None:
                continue
            k = np.argmin(abs(pon - n["t0"])) if len(pon) else None
            if k is not None and abs(pon[k] - n["t0"]) < 0.12:
                diffs.append(so - pon[k])
                by_beat.setdefault(n["beat"], {})[n["part"]] = so
        for b, d in by_beat.items():
            if len(d) >= 2:
                spreads.append(float(np.ptp(list(d.values()))))
        if diffs:
            r.update(string_minus_piano_ms_median=round(1000 * float(np.median(diffs)), 0),
                     string_minus_piano_ms_iqr=round(1000 * float(np.subtract(*np.percentile(diffs, [75, 25]))), 0),
                     timing_n=len(diffs),
                     string_spread_ms_median=round(1000 * float(np.median(spreads)), 0) if spreads else None)
        # 4. space: stereo of the strings stem
        ys = load(p, mono=False)
        m, s = ys.mean(1), (ys[:, 0] - ys[:, 1]) / 2
        r["side_minus_mid_db"] = round(float(10 * np.log10((s ** 2).sum() / (m ** 2).sum())), 1)
        r["lr_correlation"] = round(float(np.corrcoef(ys[:, 0], ys[:, 1])[0, 1]), 2)
        for band, (lo, hi) in {"low": (80, 300), "high": (700, 3000)}.items():
            sos = butter(4, [lo, hi], btype="band", fs=SR, output="sos")
            L_, R_ = sosfiltfilt(sos, ys[:, 0]), sosfiltfilt(sos, ys[:, 1])
            r[f"pan_{band}"] = round(float(((L_ ** 2).sum() - (R_ ** 2).sum()) / ((L_ ** 2).sum() + (R_ ** 2).sum())), 2)
        # 5. long-term spectrum, 1/3-octave-ish bands, relative to total
        P = spec.S.mean(1) ** 2
        edges = [100, 200, 400, 800, 1600, 3200, 6400, 12800]
        tot = P[(spec.f >= 100) & (spec.f < 12800)].sum()
        r["ltas_db"] = {f"{a}-{b}": round(float(10 * np.log10(P[(spec.f >= a) & (spec.f < b)].sum() / tot)), 1)
                        for a, b in zip(edges, edges[1:])}
        res[side] = r
    # the mixes: per-bar loudness contour (no separation involved)
    bars = [(bb, bar) for bb, bar in SCORE["barStarts"] if b0 <= bb]
    for side, p in mixes.items():
        y = load(p)
        lvb = []
        for (bb, bar), nxt in zip(bars, bars[1:]):
            if nxt[0] > b1:
                break
            a, e = int(tm(bb) * SR), int(tm(nxt[0]) * SR)
            lvb.append((bar, round(float(10 * np.log10(np.mean(y[max(a, 0):e] ** 2) + 1e-12)), 1)))
        res[side]["mix_bar_db"] = lvb
        lv = per_beat_levels(y, tm, beats)
        res[side]["mix_beat_p90_p10_db"] = round(float(np.nanpercentile(lv, 90) - np.nanpercentile(lv, 10)), 1)
    rb = np.array([v for _, v in res["real"]["mix_bar_db"]]); ob = np.array([v for _, v in res["ours"]["mix_bar_db"]])
    res["mix_bar_contour_corr"] = round(float(np.corrcoef(rb, ob)[0, 1]), 2)
    rl, ol = np.array(res["real"]["beat_levels"]), np.array(res["ours"]["beat_levels"])
    ok = np.isfinite(rl) & np.isfinite(ol) & sounding
    res["strings_beat_contour_corr"] = round(float(np.corrcoef(rl[ok], ol[ok])[0, 1]), 2)
    # engine truth for ours: string note-on minus the piano take's onset at the same beat
    ev = json.loads((OUT / "synth" / name / "events.json").read_text())
    info = json.loads((OUT / "synth" / name / "info.json").read_text())
    ons = json.loads((OUT / "synth" / name / "onsets.json").read_text())
    pt = np.array(sorted({o[0] for o in ons})) - info["offset_s"] - info["window"][0]
    d = [e[0] - pt[np.argmin(abs(pt - e[0]))] for e in ev if e[1] == "on"]
    d = [x for x in d if abs(x) < 0.15]
    res["ours_engine_string_minus_piano_ms"] = dict(median=round(1000 * float(np.median(d)), 0),
                                                    iqr=round(1000 * float(np.subtract(*np.percentile(d, [75, 25]))), 0))
    return res


def main():
    names = sys.argv[1:] or list(WINDOWS)
    allres = {n: analyse(n) for n in names}
    (OUT / "measure.json").write_text(json.dumps(allres, indent=1))
    keys = ["tuning_cents", "mix_beat_p90_p10_db", "beat_level_p90_p10_db", "centroid_hz_median", "frames_oct_per_10db",
            "notes_measured", "long_notes", "vibrato_share", "vibrato_rate_hz", "vibrato_extent_cents", "pitch_wobble_cents",
            "swell_db", "end_vs_peak_db", "peak_position", "level_ripple_db", "within_note_hcentroid_per_10db", "within_n", "across_notes_hcentroid_per_10db", "vibrato_rate_sd", "join_dip_db", "join_n",
            "intonation_sd_cents", "intonation_mean_cents", "string_minus_piano_ms_median", "string_minus_piano_ms_iqr",
            "timing_n", "string_spread_ms_median", "side_minus_mid_db", "lr_correlation", "pan_low", "pan_high", "ltas_db"]
    for n, res in allres.items():
        print(f"\n== {n}   mix bar-contour corr {res['mix_bar_contour_corr']}, strings beat-contour corr "
              f"{res['strings_beat_contour_corr']}, engine strings-piano {res['ours_engine_string_minus_piano_ms']}")
        print(f"{'':34}{'real':>14}{'ours':>14}{'ours-true':>14}")
        for k in keys:
            vals = [res[s].get(k) for s in ("real", "ours", "ours-true")]
            if k == "ltas_db":
                for band in vals[0]:
                    print(f"  ltas {band:28}" + "".join(f"{(v or {}).get(band, ''):>14}" for v in vals))
            else:
                print(f"  {k:32}" + "".join(f"{str(v):>14}" for v in vals))
        print("  mix bar dB real:", res["real"]["mix_bar_db"])
        print("  mix bar dB ours:", res["ours"]["mix_bar_db"])


if __name__ == "__main__":
    main()
