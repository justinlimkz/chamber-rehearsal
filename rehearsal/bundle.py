"""A piece packed for the iPad app: everything the app needs, made on the Mac with the Python tools.

`<out>/score.json`: title, composer, the bars (unfolded, in playing order) and every note of every part
in quarter-note beats, and who leads where (the `.lead.yaml`, if there is one). `<out>/engraving.svg` and
`<out>/anchors.json`: the score as drawn for reading (`engrave.py`).

Golden fixtures for the app's engine (`fixtures`): recorded takes run through the Python accompanist,
with what it played. The Swift port must play the same notes at the same times. The follower
(Matchmaker) is left out: on the takes it changes nothing (2026-10-28), and the app has no Matchmaker.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import annotate, engrave, midi_io, reference, session, simulate
from .accompanist import STRING_PARTS
from .score import Score
from .tempo import BeatClock
from .voice import Cue

PARTS = (*STRING_PARTS, "Piano")


def export(score: Score, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    lead_path = annotate.annotation_path(score.path)
    leadership = annotate.load(lead_path) if lead_path.exists() else None
    data = {
        "title": score.title,
        "composer": score.composer,
        "barStarts": [[beat, bar] for beat, bar in score.bar_starts()],
        "parts": {name: [[n.onset_beat, n.duration_beat, n.pitch] for n in score.notes(name)] for name in PARTS},
        "leadership": [{"firstBar": p.first_bar, "lastBar": p.last_bar, "lead": p.lead, "coupling": p.coupling}
                       for p in leadership] if leadership else None,
    }
    (out / "score.json").write_text(json.dumps(data))
    svg, anchors = engrave.engrave(score.path)
    (out / "engraving.svg").write_text(svg)
    (out / "anchors.json").write_text(json.dumps(anchors))
    return out


def fixtures(score: Score, out: Path, takes: list[Path], sessions: list[Path]) -> list[Path]:
    """One JSON file per case: the inputs (piano onsets, cues), the settings, and what the strings played."""
    out.mkdir(parents=True, exist_ok=True)
    lead_path = annotate.annotation_path(score.path)
    cases = [(p.stem, midi_io.read_take(p), 1, 16, False, []) for p in takes]
    if lead_path.exists() and takes:
        cases.append((takes[-1].stem + "-lead", midi_io.read_take(takes[-1]), 1, 16, True, []))
    if takes:
        events, cues = _cue_take(score, midi_io.read_take(takes[0]))
        cases.append(("cues", events, 1, 16, False, cues))
    for p in sessions:
        piano = session.read(p)[0]
        cases.append(("session-" + p.stem, piano, 1, 132, False, []))
    written = []
    for name, events, first, last, lead, cues in cases:
        tempo = 63.0
        res = simulate.run(score, events, first, last, clock=BeatClock(spb=60 / tempo), follow=False, cues=cues,
                           leadership=annotate.load(lead_path) if lead else None)
        acc = res.accompanist
        case = {
            "name": name, "firstBar": first, "lastBar": last, "tempo": tempo, "leadership": lead,
            "onsets": [[e.time, e.note] for e in events if e.is_onset],
            "cues": [[t, c.kind, c.bar, c.pickup] for t, c in cues],
            "outs": [[o.time, o.kind, o.part, o.pitch, o.velocity, o.beat] for o in res.outs],
            "log": [list(x) for x in acc.log],
            "notices": [[t, text] for t, text in acc.notices],
        }
        path = out / f"{name}.json"
        path.write_text(json.dumps(case))
        written.append(path)
    return written


def _cue_take(score: Score, events: list[midi_io.MidiEvent]) -> tuple[list[midi_io.MidiEvent], list[tuple[float, Cue]]]:
    """A rehearsal made from a clean take: bars 1-8, stop; "from bar 5", bars 5-8, stop; "again", bars
    5-8; "from the pickup to bar 9" (no piano follows); "from the top", bars 1-4."""
    truth = reference.align(score, events, 1, 16).truth
    bar_start = {bar: b for b, bar in reversed(score.bar_starts())}
    at = lambda bar: min(t for t, b in truth if b >= bar_start[bar])  # when the take reached the bar

    def span(first: int, end: int, shift: float) -> list[midi_io.MidiEvent]:
        t0, t1 = at(first) - 0.05, at(end) - 0.05
        return [midi_io.MidiEvent(e.time - t0 + shift, e.kind, e.note, e.value) for e in events if t0 <= e.time < t1]

    out, cues, t = [], [], 0.0
    for cue, first, end in [(None, 1, 9), (Cue("bar", 5), 5, 9), (Cue("again"), 5, 9),
                            (Cue("bar", 9, pickup=True), None, None), (Cue("top"), 1, 5)]:
        if cue is not None:
            cues.append((t + 3.0, cue))
            t += 5.0
        if first is not None:
            part = span(first, end, t)
            out += part
            t = max(e.time for e in part)
    return out, cues
