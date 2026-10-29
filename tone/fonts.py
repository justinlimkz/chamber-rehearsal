"""SF2 fonts for the tone tests from the downloaded sample sets (tone/fetch.py), for AVAudioUnitSampler.

Each instrument is GM program 40/41/42 (what Strings.swift loads), one dynamic layer (the engine plays every note at
velocity 80, so a second layer would never sound), mono, every sampled note looped as long as it allows
(tone/samples.py). Run: uv run --with numpy --with soundfile python -I tone/fonts.py [name ...]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from samples import key_of, load_mono, octave_check, spread, zone  # noqa: E402
from sf2 import Instrument, Vibrato, write_font  # noqa: E402

DL = Path(__file__).resolve().parents[1] / "build/tone"
FONTS = DL / "fonts"
RANGE = {"Violin": (52, 103), "Viola": (46, 88), "Violoncello": (34, 76)}  # a little past each instrument's compass
PROGRAM = {"Violin": 40, "Viola": 41, "Violoncello": 42}
VIB = Vibrato(rate=5.5, delay=0.3)  # for non-vibrato recordings; Strings sends CC1 (the depth) per bowed note


def files(d: str, pattern: str) -> list[tuple[Path, int]]:
    """Files in build/tone/dl-<d>/files matching `pattern`, whose group 1 is the note name; with their keys moved
    by the octave most of the set's audio says (VSCO's sections and bigcat name notes an octave low; one file's own
    check can be fooled by a weak fundamental)."""
    from collections import Counter
    found = []
    for f in sorted((DL / f"dl-{d}" / "files").iterdir()):
        m = re.fullmatch(pattern, f.name)
        if m:
            k = key_of(m[1])
            found.append((f, k, octave_check(*load_mono(f), k) - k))
    shift = Counter(s for *_, s in found).most_common(1)[0][0]
    return [(f, k + shift) for f, k, _ in found]


def instrument(part: str, found: list[tuple[Path, int]], log: list, vibrato: bool = False, **kw) -> Instrument:
    found = sorted(found, key=lambda p: p[1])
    keys = [k for _, k in found]
    assert len(set(keys)) == len(keys), f"{part}: two files for one key {keys}"
    zones = []
    for (f, _), (k, lo, hi) in zip(found, spread(keys, *RANGE[part])):
        z = zone(f, k, lo, hi, name=f"{part[:3]}{k}")
        zones.append(z)
        log.append({"part": part, "file": f.name, "key": k, "loop_s": round((z.loop[1] - z.loop[0]) / z.rate, 2),
                    "len_s": round(len(z.pcm) / z.rate, 2), "cents": z.cents})
    return Instrument(part, PROGRAM[part], zones, vibrato=VIB if vibrato else None, **kw)


def vsco_violin(log):  # solo violin with vibrato, f: the p layer takes 0.7 s (median) to reach 70% of its level
    return instrument("Violin", files("vsco2", r"Strings__Solo Violin__Arco Vib__LLVln_ArcoVib_(\w+)_f\.wav"), log)


def vsco_viola(log):  # section; the louder layer (v2), as the soft one swells in slowly
    return instrument("Viola", files("vsco2", r"Strings__Viola Section__susvib__ViolaEns_susvib_(\w+)_v2_1\.wav"), log)


def vsco_cello(log):  # section; louder layer (v3), as for the viola
    return instrument("Violoncello", files("vsco2", r"Strings__Cello Section__susvib__susvib_(\w+)_v3_1\.wav"), log)


def iowa(part, log):  # solo, ff only, almost no vibrato (sigma 2-4 cents): the font's LFO adds it
    name = {"Violoncello": "Cello"}.get(part, part)
    return instrument(part, files("iowa", rf"{name}\.arco\.ff\.sul\w\.(\w+)\.stereo\.aif"), log, vibrato=True)


def bigcat_cello(log):  # solo cello without vibrato: the font's LFO adds it
    return instrument("Violoncello", files("bigcat", r"Samples__sus__(\w+)_mf_d\.wav"), log, vibrato=True)


CREDITS = {
    "vsco2": "VSCO 2 CE, Versilian Studios / Sam Gossner (CC0 1.0)",
    "iowa": "University of Iowa Electronic Music Studios MIS (used without restrictions)",
    "bigcat": "Karoryfer x bigcat Cello, Kamila Borowiak (CC0 1.0)",
}
FONT_SPECS = {
    "vsco": ([vsco_violin, vsco_viola, vsco_cello], ["vsco2"]),
    "iowa": ([lambda l: iowa("Violin", l), lambda l: iowa("Viola", l), lambda l: iowa("Violoncello", l)], ["iowa"]),
    "solo-mix": ([vsco_violin, lambda l: iowa("Viola", l), bigcat_cello], ["vsco2", "iowa", "bigcat"]),
}


def build(name: str) -> Path:
    out = FONTS / f"{name}.sf2"
    makers, sources = FONT_SPECS[name]
    log: list = []
    stats = write_font([m(log) for m in makers], out, f"Tone test: {name}", "; ".join(CREDITS[s] for s in sources))
    (FONTS / f"{name}.json").write_text(json.dumps({"stats": stats, "zones": log}, indent=1))
    return out


if __name__ == "__main__":
    for n in sys.argv[1:] or list(FONT_SPECS):
        p = build(n)
        print(p, f"{p.stat().st_size / 1e6:.1f} MB")
