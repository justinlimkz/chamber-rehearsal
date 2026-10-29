"""Find which bars of Op. 47 III each real recording covers, and its tempo map, without listening.

Chroma DTW (librosa chroma_cqt of the recording against a piano-roll chroma of the whole score, subsequence DTW so the
excerpt can start anywhere), then each score onset is snapped to a detected onset within 0.15 s of where DTW put it,
and beat times are interpolated through those anchors (kept monotone). Per-bar tempo = 3 beats / bar duration.

Run: uv run --no-project --with numpy --with scipy --with soundfile --with librosa python -I tone/compare/align.py
Output: build/tone/compare/align/<name>.json  {first_bar, last_bar, beats: [[score_beat, time_s]], bars: [...]}
"""
import json
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
SCORE = ROOT / "ios/Bundles/schumann-op47-iii/score.json"
RAW = ROOT / "build/tone/ref/raw"
OUT = ROOT / "build/tone/compare/align"
SR, HOP = 22050, 512
NOMINAL_SPB = 60 / 60.0
EXCERPTS = {n: RAW / f"commons-Schumann-Piano-Quartet-III-{n}" / f"Schumann_-_Piano_Quartet_-_III._{n}.opus"
            for n in ("Theme", "Var2", "Var4", "Chorale")}


def load_score():
    s = json.loads(SCORE.read_text())
    bars = s["barStarts"]  # [[beat, written bar]] unfolded
    notes = [(on, max(d, 0.25), p, part) for part, ns in s["parts"].items() for on, d, p in ns if d > 0]
    return s, bars, notes


def score_chroma(notes, total_beats):
    fps = SR / HOP / NOMINAL_SPB  # frames per beat at the nominal tempo
    n = int(total_beats * fps) + 1
    C = np.zeros((12, n))
    for on, d, p, part in notes:
        a, b = int(on * fps), int((on + d) * fps) + 1
        w = np.ones(b - a)
        if part == "Piano":  # piano notes decay
            w = np.exp(-np.arange(b - a) / (fps * 1.5))
        C[p % 12, a:b] += w * (1.3 if p < 60 else 1.0)
    C = C + 1e-3
    C = C / np.linalg.norm(C, axis=0, keepdims=True)
    return C, fps


SMOOTH_S = 0.06  # seconds: how far the tempo map may miss an anchor (onsets picked from a full mix are this noisy)


def smooth_times(beats, anc):
    """Beat times from the onset anchors, smoothed: a cubic smoothing spline through the anchors, allowed to miss each
    by about SMOOTH_S. Joining the anchors with straight lines (as before) made the piano stutter: a misplaced
    anchor squeezed two quavers together (bar 4 of the Theme, 2026-10-29). Real rubato changes over beats, not
    within a quaver, so the spline keeps the players' tempo curve and drops the picking noise."""
    from scipy.interpolate import UnivariateSpline
    spl = UnivariateSpline(anc[:, 0], anc[:, 1], k=3, s=len(anc) * SMOOTH_S ** 2)
    t = spl(beats)
    return np.maximum.accumulate(t + 1e-6 * np.arange(len(t)))  # never backwards


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    s, bars, notes = load_score()
    total = bars[-1][0] + 3
    Cs, fps = score_chroma(notes, total)
    onset_beats = np.array(sorted({round(n[0], 3) for n in notes}))
    names = sys.argv[1:] or list(EXCERPTS)
    for name in names:
        x, sr = sf.read(str(EXCERPTS[name]), dtype="float32", always_2d=True)
        y = librosa.resample(x.mean(1), orig_sr=sr, target_sr=SR)
        Cr = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP)
        Cr = Cr + 1e-3
        Cr = Cr / np.linalg.norm(Cr, axis=0, keepdims=True)
        D, wp = librosa.sequence.dtw(Cr, Cs, subseq=True, metric="cosine")
        wp = wp[::-1]
        cost = D[-1, wp[-1, 1]] / len(wp)
        # beat -> time from the path (median real frame per score frame), then onset snapping
        sf_, rf = wp[:, 1], wp[:, 0]
        b0, b1 = sf_.min() / fps, sf_.max() / fps
        t_path = lambda beat: np.interp(beat * fps, sf_, rf) * HOP / SR
        on_env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
        det = librosa.onset.onset_detect(onset_envelope=on_env, sr=SR, hop_length=HOP, units="time", backtrack=False)
        anchors = []
        for ob in onset_beats[(onset_beats >= b0) & (onset_beats <= b1)]:
            t = t_path(ob)
            if len(det):
                k = np.argmin(abs(det - t))
                if abs(det[k] - t) < 0.15:
                    anchors.append((ob, det[k]))
        anchors = np.array(anchors)
        # keep anchors monotone (drop any that go backwards), else fall back to the path
        keep, last = [], -1
        for ob, t in anchors:
            if t > last + 0.05:
                keep.append((ob, t)); last = t
        anc = np.array(keep)
        beats = np.arange(np.ceil(b0), np.floor(b1) + 1)
        times = smooth_times(beats, anc) if len(anc) > 4 else t_path(beats)
        bar_rows = []
        for i, (bb, bar) in enumerate(bars):
            nb = bars[i + 1][0] if i + 1 < len(bars) else bb + 3
            if bb >= beats[0] - 1e-6 and nb <= beats[-1] + 1e-6:
                ta, tb = np.interp([bb, nb], beats, times)
                n_anc = int(((anc[:, 0] >= bb) & (anc[:, 0] < nb)).sum()) if len(anc) else 0
                bar_rows.append(dict(bar=bar, beat=bb, start=round(float(ta), 2), bpm=round(60 * (nb - bb) / (tb - ta), 1),
                                     anchors=n_anc))
        res = dict(name=name, file=str(EXCERPTS[name].relative_to(ROOT)), seconds=round(len(y) / SR, 1),
                   score_beats=[round(b0, 2), round(b1, 2)], path_cost=round(float(cost), 4),
                   first_bar=bar_rows[0]["bar"] if bar_rows else None, last_bar=bar_rows[-1]["bar"] if bar_rows else None,
                   beats=[[float(b), round(float(t), 3)] for b, t in zip(beats, times)],
                   anchors=[[float(b), round(float(t), 3)] for b, t in anc], bars=bar_rows)
        (OUT / f"{name}.json").write_text(json.dumps(res, indent=1))
        print(f"{name}: beats {b0:.1f}-{b1:.1f}  bars {res['first_bar']}-{res['last_bar']}  cost {cost:.3f}  "
              f"anchors {len(anc)}/{len(onset_beats[(onset_beats >= b0) & (onset_beats <= b1)])}")
        print("   bpm/bar:", " ".join(f"{r['bar']}:{r['bpm']:.0f}" for r in bar_rows))


if __name__ == "__main__":
    main()
