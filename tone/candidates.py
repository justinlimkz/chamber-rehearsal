"""Candidate string sounds for the tone tests: a font (built on demand into build/tone/fonts) and Strings.Style keys.

`style` keys are tone/render's: legato, swell, vibrato (CC1 range per bowed note), mix. `variant`: the engine on
build/tone/scores/<variant>.json (tone/dynamics_variants.py). `flat`: the engine without the score's dynamics,
every note at velocity 80 (the clips' events carry them since 2026-10-29). The app plays `smooth`.
"""
from __future__ import annotations

from pathlib import Path

import fonts
from sf2 import Vibrato, add_vibrato

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "build/tone/fonts"
APP_FONT = ROOT / "ios/Sounds/strings.sf2"
# the expression of the "-plus" candidates: a swell on held notes, and vibrato whose depth varies bow to bow (CC1
# 30-50 is 12-20 cents through SF2's default CC1 modulator; the samples carry some vibrato of their own)
PLUS = {"swell": 1, "vibrato": "30-50"}
VIB = Vibrato(rate=5.5, delay=0.25)
REALTIME_SAMPLER = "yes: AVAudioUnitSampler as now, no added latency"


def current_plus_font() -> Path:
    out = FONTS / "current-plus.sf2"
    add_vibrato(APP_FONT, out, VIB)
    return out


CANDIDATES: dict[str, dict] = {
    "current": {
        "font": lambda: APP_FONT,
        "description": "What the app plays today: MuseScore MS Basic violin/viola/cello (rehearsal/sf2strings.py), legato bank, held note-offs, 22% medium hall.",
        "licence": "MIT (MuseScore_General / FluidR3Mono), see ios/Sounds/LICENSE-MS-Basic.md",
        "app_size": "4.2 MB (already in the app)",
        "realtime": REALTIME_SAMPLER,
    },
    "flat": {
        "font": lambda: APP_FONT,
        "flat": True,
        "description": "Today's sound with every note at velocity 80 (the app before it read the score's dynamics).",
        "licence": "MIT (as current)",
        "app_size": "4.2 MB (no change)",
        "realtime": REALTIME_SAMPLER,
    },
    "dynamics": {
        "font": lambda: APP_FONT,
        "description": "Today's sound with each note's velocity from the score's dynamics (round 3, superseded by `smooth`): pp..ff, hairpins as steps from note to note.",
        "licence": "MIT (as current)",
        "app_size": "4.2 MB (no change)",
        "realtime": REALTIME_SAMPLER,
    },
    "smooth": {
        "font": lambda: APP_FONT,
        "description": "Today's sound with the score's dynamics as expression (CC11) that follows the beat, so hairpins shape held notes too (rehearsal/dynamics.py); notes at velocity 127.",
        "licence": "MIT (as current)",
        "app_size": "4.2 MB (no change)",
        "realtime": REALTIME_SAMPLER,
    },
    "gentle": {
        "font": lambda: APP_FONT,
        "variant": "gentle",
        "description": "As `smooth`, with hairpins that have no dynamic at their end moving 3 dB (not 4), and every ramp an S-curve (starts and ends gently; a crescendo turning into a diminuendo rounds off).",
        "licence": "MIT (as current)",
        "app_size": "4.2 MB (no change)",
        "realtime": REALTIME_SAMPLER,
    },
    "gentler": {
        "font": lambda: APP_FONT,
        "variant": "gentler",
        "description": "As `gentle`, with those hairpins moving 2 dB.",
        "licence": "MIT (as current)",
        "app_size": "4.2 MB (no change)",
        "realtime": REALTIME_SAMPLER,
    },
    "expressive": {
        "font": lambda: APP_FONT,
        "description": "One player's reading on top of the score's dynamics (rehearsal/dynamics.py Expression: marked levels 1.4x wider, phrase arches of 4 dB to the highest note, 3 dB swells on held notes), velocity picking the cello's soft/medium/loud layers, parts seated left to right, a treble shelf (-6 dB at soft expression, flat at loud), viola and cello 12/6 ms behind.",
        "licence": "MIT (as current)",
        "app_size": "4.2 MB (no change)",
        "realtime": REALTIME_SAMPLER,
    },
    "expressive-subtle": {
        "font": lambda: APP_FONT,
        "variant": "subtle",
        "style": {"dark": -3},
        "description": "As `expressive` at under half strength: levels 1.15x, arches 1.5 dB, swells 1.25 dB, shelf -3 dB (same seating and lag).",
        "licence": "MIT (as current)",
        "app_size": "4.2 MB (no change)",
        "realtime": REALTIME_SAMPLER,
    },
    "current-plus": {
        "font": current_plus_font,
        "style": PLUS,
        "description": "Same samples plus player-side expression: CC11 swell (~2 dB) on notes held past 0.4 s, and a 5.5 Hz vibrato LFO (0.25 s delay, 12-20 cents, depth varied per bow) on top of the samples' own.",
        "licence": "MIT (as current)",
        "app_size": "4.2 MB (no change)",
        "realtime": REALTIME_SAMPLER + "; a 50 Hz CC11 ticker while notes sound",
    },
    "current-swell": {
        "font": lambda: APP_FONT,
        "style": {"swell": 1},
        "description": "Today's samples with only the CC11 swell (~2 dB) on notes held past 0.4 s; no added vibrato (round 2: is the swell what the ears liked in current-plus?).",
        "licence": "MIT (as current)",
        "app_size": "4.2 MB (no change)",
        "realtime": REALTIME_SAMPLER + "; a 50 Hz CC11 ticker while notes sound",
    },
    "vsco-plus": {
        "font": lambda: fonts.build("vsco"),
        "style": {"swell": 1},
        "description": "VSCO 2 CE: solo violin (Arco Vib, f layer) with its own vibrato, viola and cello SECTIONS (susvib, louder layer: the soft layers swell in over 0.3-0.7 s); ~3 s crossfade loops; swell. No added vibrato.",
        "licence": "CC0 1.0 Universal (github.com/sgossner/VSCO-2-CE/blob/master/LICENSE); readme asks for credit to Versilian Studios/Sam Gossner",
        "app_size": "14.2 MB (41 mono 44.1 kHz zones, ~3.5-5 s each)",
        "realtime": REALTIME_SAMPLER,
    },
    "iowa-plus": {
        "font": lambda: fonts.build("iowa"),
        "style": {"swell": 1, "vibrato": "40-62"},
        "description": "University of Iowa MIS 2012 solo violin, viola, cello (arco ff, the only dynamic; nearly no vibrato, so the font LFO adds 15-25 cents at 5.5 Hz); short recordings, loops 0.1-3 s; swell.",
        "licence": "theremin.music.uiowa.edu/MIS.html: \"may be downloaded and used for any projects, without restrictions\" (informal; no licence file)",
        "app_size": "9.5 MB (40 mono zones)",
        "realtime": REALTIME_SAMPLER,
    },
    "solo-mix-plus": {
        "font": lambda: fonts.build("solo-mix"),
        "style": {"swell": 1, "vibrato": "40-62", "vibparts": "Viola,Violoncello"},
        "description": "All solo players: VSCO 2 CE solo violin (f, own vibrato), Iowa solo viola (ff, LFO vibrato), Karoryfer x bigcat solo cello (mf, non-vibrato, LFO vibrato); ~3 s loops (viola shorter); swell.",
        "licence": "Violin CC0 (VSCO 2 CE); viola Iowa MIS 'used for any projects, without restrictions'; cello CC0 1.0 (github.com/sfzinstruments/karoryfer-bigcat.cello/blob/HEAD/LICENSE)",
        "app_size": "14.1 MB (45 mono zones)",
        "realtime": REALTIME_SAMPLER,
    },
}
