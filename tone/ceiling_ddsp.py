"""ceiling-neural: the clips' violin lines through Google's pretrained DDSP solo-violin model (reference only, not for
the app). It takes an f0 and loudness curve, made here from the clips' engine events (build/tone/clips) with what a
player adds: a 40 ms glide into joined notes, delayed vibrato (5.5 Hz, ~20 cents, a little irregular), a swell on
long notes. The model only knows the violin's range: clips with cello are skipped; long-note plays the violin and
viola notes (G3 is a violin note too).

Needs ddsp 3.7 + TensorFlow 2.15 (Python 3.11; its `crepe` dependency won't build, and only a training loss uses it,
so an empty stand-in module is put on the path), and the checkpoint in build/tone/dl-ddsp/solo_violin_ckpt from
https://storage.googleapis.com/ddsp/models/timbre_transfer_colab/2021-07-08/solo_violin_ckpt/ (its
dataset_statistics.pkl is not loaded: a pickle can run code). Run: <that python> tone/ceiling_ddsp.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CKPT = ROOT / "build/tone/dl-ddsp/solo_violin_ckpt"
CLIPS = ROOT / "build/tone/clips"
OUT = ROOT / "build/tone/renders/ceiling-neural"
SR, FR = 16000, 250  # the model's audio rate and frame rate
PARTS = {"violin-1-4": {"Violin"}, "faster": {"Violin"}, "long-note": {"Violin", "Viola"}}
LUFS = -23.0
rng = np.random.default_rng(1)


def curves(ev: list, parts: set, ref_db: float) -> tuple[np.ndarray, np.ndarray, float]:
    """f0 (Hz) and loudness (dB) per frame for the notes of `parts` (one line: the model is monophonic)."""
    ev = [e for e in ev if e[2] in parts]
    notes, on = [], {}
    expr = {}
    for t, kind, part, pitch, vel in ev:
        if kind == "on":
            on[part, pitch] = t
        elif kind == "off" and (part, pitch) in on:
            notes.append([on.pop((part, pitch)), t, pitch])
        elif kind == "expr":
            expr.setdefault(part, []).append((t, vel))
    notes.sort()
    # monophonic: of notes starting together (double stops), keep the top one
    line = []
    for n in notes:
        if line and abs(n[0] - line[-1][0]) < 0.03:
            line[-1] = max(line[-1], n, key=lambda m: m[2])
        else:
            line.append(n)
    end = max(n[1] for n in line) + 2.0
    T = int(end * FR)
    t = np.arange(T) / FR
    midi = np.full(T, float(line[0][2]))
    loud = np.full(T, ref_db - 60)
    for i, (a, b, p) in enumerate(line):
        nxt = line[i + 1] if i + 1 < len(line) else None
        joined = nxt is not None and nxt[0] - b < 0.25
        stop = nxt[0] if joined else b + 0.05  # held through the engine's gap, as Strings does
        m = (t >= a) & (t < (nxt[0] if nxt else end))
        dt = t[m] - a
        glide = 0.04 if i and line[i - 1][1] > a - 0.25 else 0.0
        prev = line[i - 1][2] if glide else p
        base = p + (prev - p) * np.clip(1 - dt / glide, 0, 1) if glide else np.full(dt.shape, float(p))
        rate = 5.5 + rng.uniform(-0.3, 0.3)
        depth = rng.uniform(0.16, 0.24) * np.clip((dt - 0.2) / 0.3, 0, 1)  # semitones, delayed onset
        midi[m] = base + depth * np.sin(2 * np.pi * rate * dt + rng.uniform(0, 6.28))
        # loudness: quick bow attack (from the note before if joined), a swell on long notes, release at the stop
        level = ref_db + 2.5 * np.clip((dt - 0.4) / 1.2, 0, 1) - 2.5 * np.clip((dt - 3) / 3, 0, 1)
        attack = np.clip(dt / 0.06, 0, 1)
        floor = ref_db - 4 if glide else ref_db - 60
        rel = np.clip((t[m] - stop) / 0.15, 0, 1)
        loud[m] = floor + (level - floor) * attack
        loud[m] = loud[m] * (1 - rel) + (ref_db - 60) * rel
    for part, xs in expr.items():  # the engine's CC11 fades (long-note), as dB (CC11 acts about squared)
        for (ta, v), (tb, _) in zip(xs, xs[1:] + [(end, 127)]):
            m = (t >= ta) & (t < tb)
            loud[m] += 40 * np.log10(max(v, 1) / 127)
    return 440 * 2 ** ((midi - 69) / 12), loud, end


def main() -> None:
    import ddsp.spectral_ops
    import ddsp.training
    import gin
    import pyloudnorm
    import soundfile as sf
    from scipy.signal import resample_poly

    # loudness of a real sustained violin note (VSCO A4 f), in the model's own measure, as the playing level
    ref, sr = sf.read(next((ROOT / "build/tone/dl-vsco2/files").glob("*ArcoVib_A4_f.wav")), always_2d=True)
    ref = resample_poly(ref.mean(1), SR, sr)[: SR * 4]
    ref_db = float(np.median(ddsp.spectral_ops.compute_loudness(ref, sample_rate=SR, frame_rate=FR)[FR:3 * FR]))
    print(f"playing level {ref_db:.1f} dB")
    OUT.mkdir(parents=True, exist_ok=True)
    for clip, parts in PARTS.items():
        f0, loud, end = curves(json.loads((CLIPS / f"{clip}.json").read_text()), parts, ref_db)
        T = len(f0)
        with gin.unlock_config():
            gin.parse_config_file(str(CKPT / "operative_config-0.gin"), skip_unknown=True)
            gin.parse_config([f"Harmonic.n_samples = {T * SR // FR}", f"FilteredNoise.n_samples = {T * SR // FR}",
                              f"F0LoudnessPreprocessor.time_steps = {T}", "F0LoudnessPreprocessor.compute_loudness = False",
                              "oscillator_bank.use_angular_cumsum = True"])
        model = ddsp.training.models.Autoencoder()
        model.restore(str(CKPT / "ckpt-40000"))
        feats = {"f0_hz": f0[None].astype(np.float32), "loudness_db": loud[None].astype(np.float32),
                 "f0_confidence": np.ones((1, T), np.float32)}
        audio = np.asarray(model.get_audio_from_outputs(model(feats, training=False)))[0]
        x = resample_poly(audio, 3, 1)  # 16 -> 48 kHz
        x = np.stack([x, x], 1)
        x *= 10 ** ((LUFS - pyloudnorm.Meter(48000).integrated_loudness(x)) / 20)
        x *= min(1.0, 0.977 / np.abs(x).max())
        sf.write(OUT / f"{clip}.wav", x, 48000, subtype="PCM_24")
        print(clip, f"{len(x) / 48000:.1f} s")
    (OUT / "info.json").write_text(json.dumps({
        "description": "Reference ceiling, not for the app: Google Magenta DDSP pretrained solo-violin model (2021-07-08 timbre-transfer checkpoint) driven by f0/loudness curves made from the engine events (glides, delayed irregular vibrato, swells). Violin line only (violin-1-4, faster; long-note has the violin and viola notes, no cello); mono, with the model's learned room reverb.",
        "licence": "ddsp code Apache-2.0 (github.com/magenta/ddsp); checkpoint published by Google in the public gs://ddsp bucket with the timbre-transfer colab; training data MusOpen recordings; reference listening only",
        "app_size": "n/a (TensorFlow model, ~58 MB weights; not real-time on iPad as is)",
        "realtime": "no: offline only (a DDSP model can run in real time with work, but not with low latency on iPad as is)",
        "clips": sorted(PARTS),
    }, indent=2) + "\n")


if __name__ == "__main__":
    sys.exit(main())
