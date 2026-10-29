"""Collect the A/B listening set: real excerpt vs ours (same bars, same tempo map), all at -23 LUFS integrated.

Run (after synth.py, separate.py, variant.py):
    uv run --no-project --with numpy --with soundfile --with pyloudnorm python -I tone/compare/listen.py
Output: build/tone/compare/listen/*.wav (48 kHz 24-bit stereo) and *.mp4 (AAC 192k, via macOS afconvert).
"""
import subprocess
from pathlib import Path

import numpy as np
import pyloudnorm
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
C = ROOT / "build/tone/compare"
L = C / "listen"
SETS = {
    "theme-a-real": C / "synth/Theme/real.wav", "theme-b-ours": C / "synth/Theme/mix.wav",
    "theme-c-ours-tweaked": C / "synth/Theme/mix-tweaked.wav",
    "theme-strings-a-real-demucs": C / "sep/real-Theme/strings.wav", "theme-strings-b-ours-demucs": C / "sep/ours-Theme/strings.wav",
    "chorale-a-real": C / "synth/Chorale/real.wav", "chorale-b-ours": C / "synth/Chorale/mix.wav",
    "chorale-c-ours-tweaked": C / "synth/Chorale/mix-tweaked.wav",
    "chorale-strings-a-real-demucs": C / "sep/real-Chorale/strings.wav", "chorale-strings-b-ours-demucs": C / "sep/ours-Chorale/strings.wav",
}
L.mkdir(parents=True, exist_ok=True)
for name, src in SETS.items():
    x, sr = sf.read(str(src), dtype="float64", always_2d=True)
    x = x * 10 ** ((-23.0 - pyloudnorm.Meter(sr).integrated_loudness(x)) / 20)
    pk = np.abs(x).max()
    if pk > 0.98:
        x *= 0.977 / pk
    sf.write(str(L / f"{name}.wav"), x, sr, subtype="PCM_24")
    subprocess.run(["afconvert", "-f", "mp4f", "-d", "aac", "-b", "192000", str(L / f"{name}.wav"), str(L / f"{name}.mp4")],
                   check=True)
    print(name, f"{len(x) / sr:.1f} s")
