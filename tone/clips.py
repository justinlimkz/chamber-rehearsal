"""The fixed test clips of the tone tests, rendered for each candidate sound (tone/candidates.py).

Score clips run the real engine (tone/render, `render events`) on a piano take that plays exactly in time at 63 bpm,
so their timing is what the app would emit. Output: build/tone/renders/<candidate>/<clip>.wav, 48 kHz stereo 24-bit,
at -23 LUFS integrated, plus info.json; each clip's events in build/tone/clips/<clip>.json.

Run: tone/render/build.sh, then
    uv run --with numpy --with soundfile --with pyloudnorm python -I tone/clips.py [candidate ...]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tone"))
import candidates  # noqa: E402

SCORE = ROOT / "ios/Bundles/schumann-op47-iii/score.json"
RENDER = ROOT / "build/tone/bin/render"
OUT = ROOT / "build/tone"
LUFS = -23.0
LEAD_S, TAIL_S = 0.3, 2.2  # silence kept before the first note, and room for the release and reverb after the last

# name: (first bar, last bar, parts) for score clips. Bars 1-8 take 23 s at 63 bpm, so the opening is bars 1-4.
SCORE_CLIPS = {
    "violin-1-4": (1, 4, ["Violin"]),  # joins: slurred crotchets, the leap from a grace note, a quaver run
    "trio-1-4": (1, 4, ["Violin", "Viola", "Violoncello"]),  # balance
    "cello-6-8": (6, 8, ["Violoncello"]),  # repeated E-flats, crotchet after crotchet (6.2 to 7.2)
    "faster": (75, 78, ["Violin", "Viola", "Violoncello"]),  # the violin's semiquaver runs (bars 75-89), the quickest
    "trio-48-51": (48, 51, ["Violin", "Viola", "Violoncello"]),  # p, with paired crescendo/diminuendo hairpins
    "trio-66-69": (66, 69, ["Violin", "Viola", "Violoncello"]),  # crescendo into the f of bar 67, then hairpins
    "theme-4-11": (4, 11, ["Violin", "Viola", "Violoncello"]),  # the cello's theme (compared with real players)
}
# long-note: each instrument's longest-held pitch in the movement (violin D4 bar 112, viola G3 bar 107, cello B-flat2
# bar 120), 4 s each, faded out over the last 1.5 s with CC11 the way the engine fades (steps of 8)
LONG = [("Violin", 62), ("Viola", 55), ("Violoncello", 46)]
LONG_S, FADE_S, GAP_S = 4.0, 1.5, 0.6


def long_note() -> list[list]:
    ev, t = [], LEAD_S
    for part, pitch in LONG:
        ev.append([t, "on", part, pitch, 80])
        steps = list(range(127 - 8, 30, -8))
        for i, v in enumerate(steps):
            ev.append([t + LONG_S - FADE_S + FADE_S * (i + 1) / len(steps), "expr", part, 0, v])
        ev.append([t + LONG_S, "off", part, pitch, 0])
        ev.append([t + LONG_S + 0.5, "expr", part, 0, 127])  # as the engine resets a faded part
        t += LONG_S + GAP_S
    return ev


def clip_events(flat: bool = False, variant: str | None = None) -> dict[str, list[list]]:
    """Every clip's events, starting LEAD_S before its first note. `flat`: without the score's dynamics;
    `variant`: with the dynamics of build/tone/scores/<variant>.json (tone/dynamics_variants.py)."""
    out = {}
    tmp = OUT / "clips"
    tmp.mkdir(parents=True, exist_ok=True)
    tag = ".flat" if flat else f".{variant}" if variant else ""
    score = OUT / "scores" / f"{variant}.json" if variant else SCORE
    for name, (first, last, parts) in SCORE_CLIPS.items():
        raw = tmp / f"{name}{tag}.engine.json"
        subprocess.run([RENDER, "events", score, str(first), str(last), raw, *(["flat"] if flat else [])], check=True)
        ev = [e for e in json.loads(raw.read_text()) if e[2] in parts]
        t0 = min(e[0] for e in ev) - LEAD_S
        out[name] = [[round(e[0] - t0, 4), *e[1:]] for e in ev]
    out["long-note"] = long_note()
    for name, ev in out.items():
        (tmp / f"{name}{tag}.json").write_text(json.dumps(ev))
    return out


def render(cand: str, clips: dict[str, list[list]]) -> None:
    import numpy as np
    import pyloudnorm
    import soundfile as sf

    spec = candidates.CANDIDATES[cand]
    font = spec["font"]()
    d = OUT / "renders" / cand
    d.mkdir(parents=True, exist_ok=True)
    for name, ev in clips.items():
        raw = OUT / "tmp" / f"{cand}-{name}.wav"
        raw.parent.mkdir(parents=True, exist_ok=True)
        opts = [f"{k}={v}" for k, v in spec.get("style", {}).items()]
        events = OUT / "clips" / f"{name}{event_tag(spec)}.json"
        subprocess.run([RENDER, "play", font, events, raw, *opts], check=True, stderr=subprocess.DEVNULL)
        x, sr = sf.read(raw, dtype="float64")
        end = max(e[0] for e in ev) + TAIL_S
        x = x[: int(end * sr)]
        gain = LUFS - pyloudnorm.Meter(sr).integrated_loudness(x)
        x = x * 10 ** (gain / 20)
        peak = np.abs(x).max()
        if peak > 0.98:  # never clip: a quieter clip is better than a distorted one (reported)
            print(f"  {cand}/{name}: peak {peak:.2f} after normalising, limited to -0.2 dBFS")
            x *= 0.977 / peak
        sf.write(d / f"{name}.wav", x, sr, subtype="PCM_24")
        print(f"  {cand}/{name}: {len(x) / sr:.1f} s, gain {gain:+.1f} dB")
    info = {k: spec[k] for k in ("description", "licence", "app_size", "realtime")}
    info["font"] = str(Path(font).relative_to(ROOT)) if Path(font).is_relative_to(ROOT) else str(font)
    info["style"] = spec.get("style", {})
    (d / "info.json").write_text(json.dumps(info, indent=2) + "\n")


def main() -> None:
    names = sys.argv[1:] or list(candidates.CANDIDATES)
    clips = {}
    for c in names:
        spec = candidates.CANDIDATES[c]
        tag = event_tag(spec)
        if tag not in clips and spec.get("events"):  # made beforehand, e.g. by an earlier engine
            clips[tag] = {f.name.removesuffix(f"{tag}.json"): json.loads(f.read_text()) for f in (OUT / "clips").glob(f"*{tag}.json")}
        if tag not in clips:
            clips[tag] = clip_events(bool(spec.get("flat")), spec.get("variant"))
        print(c)
        render(c, clips[tag])


def event_tag(spec: dict) -> str:
    if spec.get("events"):
        return f".{spec['events']}"
    return ".flat" if spec.get("flat") else f".{spec['variant']}" if spec.get("variant") else ""


if __name__ == "__main__":
    main()
