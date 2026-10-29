"""A rough prototype of three playback-layer changes on the Theme counterpart (not blind-tested, not in the app):
  seating   each part rendered on its own and placed: violin left, viola slightly right, cello right
  dynamics  the engine's CC11 expression squared (about twice the dB range of the score dynamics, same shape)
  darker    a -5 dB high shelf above ~2 kHz on the strings (the real strings stem has 5-9 dB less 1.6-6.4 kHz energy)
Same engine events, tempo map, piano and window as tone/compare/synth.py; loudness matched at -23 LUFS.

Run: uv run --no-project --with numpy --with scipy --with soundfile --with pyloudnorm python -I tone/compare/variant.py
Output: build/tone/compare/synth/<name>/mix-tweaked.wav, strings-tweaked.wav
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyloudnorm
import soundfile as sf
from scipy.signal import lfilter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build/tone/compare"
BIN = ROOT / "build/tone/bin"
FONT = ROOT / "ios/Sounds/strings.sf2"
SR = 48000
PAN = {"Violin": -0.45, "Viola": 0.15, "Violoncello": 0.4}  # -1 left .. +1 right


def high_shelf(x, f0=2000.0, gain_db=-5.0, q=0.7):
    A = 10 ** (gain_db / 40); w = 2 * np.pi * f0 / SR; al = np.sin(w) / (2 * q); c = np.cos(w)
    b = [A * ((A + 1) + (A - 1) * c + 2 * np.sqrt(A) * al), -2 * A * ((A - 1) + (A + 1) * c),
         A * ((A + 1) + (A - 1) * c - 2 * np.sqrt(A) * al)]
    a = [(A + 1) - (A - 1) * c + 2 * np.sqrt(A) * al, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - 2 * np.sqrt(A) * al]
    return lfilter(np.array(b) / a[0], np.array(a) / a[0], x, axis=0)


def main():
    meter = pyloudnorm.Meter(SR)
    for name in sys.argv[1:] or ["Theme"]:
        d = OUT / "synth" / name
        info = json.loads((d / "info.json").read_text())
        ev = json.loads((d / "events.engine.json").read_text())
        ref = sf.read(str(d / "strings.wav"), dtype="float64", always_2d=True)[0]
        n = len(ref); start = int((info["window"][0] + info["offset_s"]) * SR)
        total = np.zeros((n, 2))
        for part, pan in PAN.items():
            pe = [[e[0], e[1], e[2], e[3], round(127 * (e[4] / 127) ** 2) if e[1] == "expr" else e[4]]
                  for e in ev if e[2] == part]
            p_ev, p_wav = d / f"events.{part}.json", d / f"strings.{part}.wav"
            p_ev.write_text(json.dumps(pe))
            subprocess.run([BIN / "render", "play", FONT, p_ev, p_wav], check=True, stderr=subprocess.DEVNULL)
            x = sf.read(str(p_wav), dtype="float64", always_2d=True)[0]
            x = np.pad(x, ((0, max(0, start + n - len(x))), (0, 0)))[start:start + n]
            th = (pan + 1) * np.pi / 4  # constant-power balance of the stereo render
            total += x * np.array([np.cos(th), np.sin(th)]) * np.sqrt(2)
        total = high_shelf(total)
        total *= 10 ** ((meter.integrated_loudness(ref) - meter.integrated_loudness(total)) / 20)
        mix = total + sf.read(str(d / "piano.wav"), dtype="float64", always_2d=True)[0]
        g = 10 ** ((-23.0 - meter.integrated_loudness(mix)) / 20)
        sf.write(str(d / "mix-tweaked.wav"), mix * g, SR, subtype="PCM_24")
        sf.write(str(d / "strings-tweaked.wav"), total * g, SR, subtype="PCM_24")
        print(name, "peak", round(float(np.abs(mix * g).max()), 3))


if __name__ == "__main__":
    main()
