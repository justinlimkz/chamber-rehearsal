"""A fair synthetic counterpart of a real Op. 47 III excerpt: same bars, same tempo map.

The piano take is the score's piano part played at the real recording's beat times (from tone/compare/align.py), so the
real engine (build/tone/bin/compare-events, RehearsalCore unmodified, score dynamics on) follows the real tempo; its
string events are played through the app's Strings.swift (build/tone/bin/render play, app font, app style). The piano is
the same notes through MuseScore's MS Basic grand piano in FluidSynth (velocity 50, reverb on). The piano is mixed
against the strings at the piano/strings loudness ratio Demucs finds in the real excerpt (tone/compare/separate.py
must have run on the real excerpt first; otherwise 0 dB). Everything is cut to the excerpt's window.

Run: uv run --no-project --with numpy --with soundfile --with pyloudnorm --with mido python -I tone/compare/synth.py [name ...]
Output: build/tone/compare/synth/<name>/{real,mix,strings,piano}.wav (48 kHz stereo, unnormalised except real/mix at
-23 LUFS), events.json (string events, times relative to the window), onsets.json, info.json.
"""
import json
import subprocess
import sys
from pathlib import Path

import mido
import numpy as np
import pyloudnorm
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
SCORE = ROOT / "ios/Bundles/schumann-op47-iii/score.json"
FONT = ROOT / "ios/Sounds/strings.sf2"
PIANO_FONT = Path("/Applications/MuseScore 4.app/Contents/Resources/sound/MS Basic.sf3")
BIN = ROOT / "build/tone/bin"
OUT = ROOT / "build/tone/compare"
SR, LUFS, LEAD = 48000, -23.0, 1.0
PIANO_VEL = 50
# name: (engine first bar, engine last bar, window in the real file [s, s])
JOBS = {"Theme": (1, 17, (0.0, 44.0)), "Chorale": (47, 63, (2.0, 46.0))}


def beat_map(al):
    b = np.array([x[0] for x in al["beats"]]); t = np.array([x[1] for x in al["beats"]])
    k = 0  # DTW can pin the first beats to one frame: start where beats are at least 0.4 s apart
    while k + 1 < len(t) and t[k + 1] - t[k] < 0.4:
        k += 1
    b, t = b[k:], t[k:]
    s0 = float(np.median(np.diff(t[:7]))); s1 = float(np.median(np.diff(t[-7:])))

    def f(beat):
        beat = np.asarray(beat, float)
        return np.where(beat < b[0], t[0] + (beat - b[0]) * s0,
                        np.where(beat > b[-1], t[-1] + (beat - b[-1]) * s1, np.interp(beat, b, t)))
    return f, s0


def main():
    score = json.loads(SCORE.read_text())
    bars = {}
    for beat, bar in score["barStarts"]:
        bars.setdefault(bar, beat)
    names = sys.argv[1:] or list(JOBS)
    meter = pyloudnorm.Meter(SR)
    for name in names:
        first, last, (w0, w1) = JOBS[name]
        al = json.loads((OUT / "align" / f"{name}.json").read_text())
        tmap, spb0 = beat_map(al)
        lo, hi = bars[first], bars.get(last + 1, bars[last] + 3)
        d = OUT / "synth" / name
        d.mkdir(parents=True, exist_ok=True)
        piano = [n for n in score["parts"]["Piano"] if lo <= n[0] < hi and n[1] > 0]
        off = LEAD - float(tmap(min(n[0] for n in piano)))  # our time = real time + off
        onsets = sorted([[round(float(tmap(n[0])) + off, 4), n[2]] for n in piano])
        (d / "onsets.json").write_text(json.dumps(onsets))
        ev_path = d / "events.engine.json"
        subprocess.run([BIN / "compare-events", SCORE, d / "onsets.json", str(first), str(last), f"{spb0:.4f}", ev_path],
                       check=True)
        events = json.loads(ev_path.read_text())
        # strings through the app's sampler
        subprocess.run([BIN / "render", "play", FONT, ev_path, d / "strings.full.wav"], check=True, stderr=subprocess.DEVNULL)
        # piano through FluidSynth
        mid = mido.MidiFile(ticks_per_beat=960)
        tr = mido.MidiTrack(); mid.tracks.append(tr)
        tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000))  # 1 beat = 1 s: ticks are milliseconds-ish
        msgs = []
        for on, dur, p in piano:
            a = float(tmap(on)) + off
            b = float(tmap(on + dur)) + off + 0.08  # slight overlap, as with a little pedal
            msgs += [(a, 1, mido.Message("note_on", note=p, velocity=PIANO_VEL)), (b, 0, mido.Message("note_off", note=p))]
        msgs.sort(key=lambda m: (m[0], m[1]))
        now = 0
        for t, _, m in msgs:
            tick = int(round(t * 960)); m.time = tick - now; now = tick; tr.append(m)
        mid.save(d / "piano.mid")
        subprocess.run(["fluidsynth", "-ni", "-q", "-R", "1", "-C", "0", "-g", "0.5", "-r", str(SR),
                        "-F", str(d / "piano.full.wav"), str(PIANO_FONT), str(d / "piano.mid")],
                       check=True, capture_output=True)
        # cut everything to the real window
        real, rsr = sf.read(str(ROOT / al["file"]), dtype="float64", always_2d=True)
        assert rsr == SR
        real = real[int(w0 * SR):int(w1 * SR)]
        n = len(real)
        cut = lambda x: np.pad(x, ((0, max(0, int((w0 + off) * SR) + n - len(x))), (0, 0)))[int((w0 + off) * SR):][:n]
        st = cut(sf.read(str(d / "strings.full.wav"), dtype="float64", always_2d=True)[0])
        pf, psr = sf.read(str(d / "piano.full.wav"), dtype="float64", always_2d=True)
        assert psr == SR
        pn = cut(pf)
        # balance: piano minus strings loudness as Demucs measured it on the real excerpt
        bal_path = OUT / "sep" / f"real-{name}" / "balance.json"
        target = json.loads(bal_path.read_text())["piano_minus_rest_lu"] if bal_path.exists() else 0.0
        cur = meter.integrated_loudness(pn) - meter.integrated_loudness(st)
        pn *= 10 ** ((target - cur) / 20)
        mix = st + pn
        g = 10 ** ((LUFS - meter.integrated_loudness(mix)) / 20)
        rg = 10 ** ((LUFS - meter.integrated_loudness(real)) / 20)
        for fn, x in (("real", real * rg), ("mix", mix * g), ("strings", st * g), ("piano", pn * g)):
            sf.write(str(d / f"{fn}.wav"), x, SR, subtype="PCM_24")
        win = [[round(e[0] - off - w0, 4), *e[1:]] for e in events if w0 <= e[0] - off <= w1 + 3]
        (d / "events.json").write_text(json.dumps(win))
        info = dict(name=name, engine_bars=[first, last], window=[w0, w1], offset_s=round(off, 3), spb0=round(spb0, 4),
                    piano_minus_strings_lu=round(target, 2), mix_gain_db=round(20 * np.log10(g), 2),
                    real_gain_db=round(20 * np.log10(rg), 2), piano_velocity=PIANO_VEL)
        (d / "info.json").write_text(json.dumps(info, indent=1))
        print(name, info)


if __name__ == "__main__":
    main()
