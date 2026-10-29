"""A piece packed for the iPad app: everything the app needs, made on the Mac with the Python tools.

`<out>/score.json`: title, composer, work and movement (from `<score>.piece.yaml`, if there is one), the
bars (unfolded, in playing order) and every note of every part in quarter-note beats, each string part's
loudness curve from the score's dynamics (`dynamics.py`: [beat, dB from mf] points, linear between them),
and who leads where (the `.lead.yaml`, if there is one). `<out>/engraving.svg` and `<out>/anchors.json`:
the score as drawn for reading (`engrave.py`), and `engraving-piano.svg`, `anchors-piano.json`: the piano
part alone. Nothing needs annotating by hand: the leadership file and the piece file are optional.

Golden fixtures for the app's engine (`fixtures`): recorded takes run through the Python accompanist,
with what it played. The Swift port must play the same notes at the same times. The follower
(Matchmaker) is left out: on the takes it changes nothing (2026-10-28), and the app has no Matchmaker.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from . import annotate, dynamics, engrave, midi_io, reference, session, simulate
from .accompanist import STRING_PARTS
from .score import Score
from .tempo import BeatClock
from .voice import Cue

PARTS = (*STRING_PARTS, "Piano")


def export(score: Score, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    lead_path = annotate.annotation_path(score.path)
    leadership = annotate.load(lead_path) if lead_path.exists() else None
    meta_path = score.path.with_suffix(".piece.yaml")
    meta = yaml.safe_load(meta_path.read_text()) if meta_path.exists() else {}
    data = {
        "title": score.title,
        "composer": score.composer,
        "work": meta.get("work", score.title),  # for the library: works, with their movements under them
        "movement": meta.get("movement", ""),
        "barStarts": [[beat, bar] for beat, bar in score.bar_starts()],
        "parts": {name: [[n.onset_beat, n.duration_beat, n.pitch] for n in score.notes(name)] for name in PARTS},
        "dynamics": {name: [list(p) for p in pts] for name, pts in dynamics.curves(score, STRING_PARTS).items()},
        "leadership": [{"firstBar": p.first_bar, "lastBar": p.last_bar, "lead": p.lead, "coupling": p.coupling}
                       for p in leadership] if leadership else None,
    }
    (out / "score.json").write_text(json.dumps(data))
    svg, anchors = engrave.engrave(score.path)
    (out / "engraving.svg").write_text(svg)
    (out / "anchors.json").write_text(json.dumps(anchors))
    # the piano part alone, larger, its lines breaking where the full score's do
    svg, anchors = engrave.engrave(score.path, only="Piano", breaks=engrave.systems(svg))
    (out / "engraving-piano.svg").write_text(svg)
    (out / "anchors-piano.json").write_text(json.dumps(anchors))
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
        events, cues = _pause_take(score, midi_io.read_take(takes[0]))
        cases.append(("pause", events, 1, 16, False, cues))
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
    return _rehearsal(score, events, [(None, 1, 9), (Cue("bar", 5), 5, 9), (Cue("again"), 5, 9),
                                      (Cue("bar", 9, pickup=True), None, None), (Cue("top"), 1, 5)])


def _pause_take(score: Score, events: list[midi_io.MidiEvent]) -> tuple[list[midi_io.MidiEvent], list[tuple[float, Cue]]]:
    """Practising alone: bars 1-4, "pause" while playing, bars 5-8 alone (ignored), "resume", bars 5-8
    with the strings; "pause" while waiting, bars 9-12 alone, "from bar 3", bars 3-6."""
    return _rehearsal(score, events, [(None, 1, 5), (Cue("pause"), 5, 9, 0.0), (Cue("resume"), 5, 9),
                                      (Cue("pause"), 9, 13), (Cue("bar", 3), 3, 7)])


def _rehearsal(score: Score, events: list[midi_io.MidiEvent], steps: list[tuple]) -> tuple[list[midi_io.MidiEvent], list[tuple[float, Cue]]]:
    """Spans of a take played one after another: (cue said first or None, first bar, bar to stop before or
    None for no playing[, 0.0: the cue comes while the span before is still playing, with no pause]). A cue
    otherwise comes 3 s after the playing stops, and the next span 2 s after it."""
    truth = reference.align(score, events, 1, 16).truth
    bar_start = {bar: b for b, bar in reversed(score.bar_starts())}
    at = lambda bar: min(t for t, b in truth if b >= bar_start[bar])  # when the take reached the bar

    def span(first: int, end: int, shift: float) -> list[midi_io.MidiEvent]:
        t0, t1 = at(first) - 0.05, at(end) - 0.05
        return [midi_io.MidiEvent(e.time - t0 + shift, e.kind, e.note, e.value) for e in events if t0 <= e.time < t1]

    out, cues, t = [], [], 0.0
    for cue, first, end, *gap in steps:
        if cue is not None:
            if gap and gap[0] == 0.0:  # just before the last note
                cues.append((t - 0.3, cue))
            else:
                cues.append((t + 3.0, cue))
                t += 5.0
        if first is not None:
            part = span(first, end, t)
            out += part
            t = max(e.time for e in part)
    return out, cues
