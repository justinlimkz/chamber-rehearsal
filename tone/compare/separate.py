"""Split an excerpt into piano and "the rest" (here: the string trio) with Demucs htdemucs_6s, which has a piano stem.

The same separation is run on the real recording and on our synthetic mix, so its artefacts are on both sides; on ours
the true strings are known (synth/<name>/strings.wav), which measures how good the separation is.

Run: uv run --no-project --python 3.12 --with demucs --with soundfile --with pyloudnorm python -I tone/compare/separate.py real-Theme ours-Theme ...
  real-<name>: build/tone/compare/synth/<name>/real.wav if it exists, else the raw excerpt window (for the balance)
  ours-<name>: build/tone/compare/synth/<name>/mix.wav
Output: build/tone/compare/sep/<tag>/{piano,strings}.wav, balance.json (piano minus strings loudness, LU)
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build/tone/compare"
WINDOWS = {"Theme": (0.0, 44.0), "Chorale": (2.0, 46.0)}


def lufs(x, sr):
    import pyloudnorm
    return pyloudnorm.Meter(sr).integrated_loudness(x)


def main():
    model = get_model("htdemucs_6s"); model.eval()
    for tag in sys.argv[1:]:
        side, name = tag.split("-", 1)
        if side == "ours":
            src = OUT / "synth" / name / "mix.wav"
        else:
            src = OUT / "synth" / name / "real.wav"
            if not src.exists():
                al = json.loads((OUT / "align" / f"{name}.json").read_text())
                src = ROOT / al["file"]
        x, sr = sf.read(str(src), dtype="float32", always_2d=True)
        if side == "real" and not (OUT / "synth" / name / "real.wav").exists():
            w0, w1 = WINDOWS[name]; x = x[int(w0 * sr):int(w1 * sr)]
        assert sr == model.samplerate == 44100 or sr == 48000
        # Demucs works at 44.1 kHz
        import librosa
        y = librosa.resample(x.T, orig_sr=sr, target_sr=model.samplerate)
        with torch.no_grad():
            est = apply_model(model, torch.tensor(y)[None], device="cpu", shifts=1, split=True, overlap=0.25)[0].numpy()
        pi = model.sources.index("piano")
        piano = est[pi]
        rest = y - piano  # everything that is not piano: the strings (plus whatever Demucs leaves)
        piano48 = librosa.resample(piano, orig_sr=model.samplerate, target_sr=48000).T
        rest48 = librosa.resample(rest, orig_sr=model.samplerate, target_sr=48000).T
        d = OUT / "sep" / tag
        d.mkdir(parents=True, exist_ok=True)
        sf.write(str(d / "piano.wav"), piano48, 48000, subtype="FLOAT")
        sf.write(str(d / "strings.wav"), rest48, 48000, subtype="FLOAT")
        bal = dict(src=str(src.relative_to(ROOT)), piano_lufs=round(lufs(piano48, 48000), 2),
                   rest_lufs=round(lufs(rest48, 48000), 2))
        bal["piano_minus_rest_lu"] = round(bal["piano_lufs"] - bal["rest_lufs"], 2)
        if side == "ours":  # separation quality against the true strings
            true = sf.read(str(OUT / "synth" / name / "strings.wav"), dtype="float64", always_2d=True)[0]
            n = min(len(true), len(rest48)); e = rest48[:n] - true[:n]
            bal["strings_sdr_db"] = round(float(10 * np.log10((true[:n] ** 2).sum() / (e ** 2).sum())), 2)
        (d / "balance.json").write_text(json.dumps(bal, indent=1))
        print(tag, bal, flush=True)


if __name__ == "__main__":
    main()
