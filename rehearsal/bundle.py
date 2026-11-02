"""A piece packed for the iPad app on the Mac (the app makes the same from a MusicXML file itself).

`<out>/score.json`: title, composer, work and movement and the tempo to start at (from `<score>.piece.yaml`, if
there is one; the tempo else from the score's first metronome mark, if it has one), the
bars (unfolded, in playing order) and every note of every part in quarter-note beats, each string part's
loudness curve from the score's dynamics (`dynamics.py`: [beat, dB from mf] points, linear between them).
`<out>/score.musicxml`: the score itself, which the iPad engraves with Verovio to its screen, with the parts and
size chosen there. The piece file is optional.

Golden fixtures for the app's engine (`fixtures`): recorded takes run through the Python accompanist,
with what it played. The Swift port must play the same notes at the same times.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from . import dynamics, midi_io, musicxml, reference, session, simulate
from .accompanist import STRING_PARTS
from .score import Score
from .tempo import BeatClock
from .voice import Cue

PARTS = (*STRING_PARTS, "Piano")


def score_data(score: Score) -> dict:
    """What the engine reads from the score (the iPad makes the same from a MusicXML file itself, checked by
    `import_fixtures`): the bars, the notes of the parts it has, and the string parts' loudness curves."""
    found = {musicxml.instrument(p.names, p.program) for p in score.read_parts}
    have = [name for name in PARTS if name in found or any(p.lower().startswith(name.lower()) for p in score.part_names)]
    strings = [name for name in have if name in STRING_PARTS]
    data = {
        "title": score.title,
        "composer": score.composer,
        "barStarts": [[beat, bar] for beat, bar in score.bar_starts()],
        "parts": {name: [[n.onset_beat, n.duration_beat, n.pitch] for n in score.notes(name)] for name in have},
        "dynamics": {name: [list(p) for p in pts] for name, pts in dynamics.curves(score, strings).items()},
    }
    if score.tempo:
        data["tempo"] = score.tempo  # quarter notes a minute for the strings' first notes
    if len({q for _, q in score.tempos}) > 1:
        data["tempos"] = [[b, q] for b, q in score.tempos]  # the marks after it: the strings change pace where they say
    return data


def import_fixtures(paths: list[Path], out: Path) -> list[Path]:
    """For each MusicXML file, `<out>/<stem>.json`: the source's path and `score_data`, for the iPad's own
    MusicXML import to match (`swift run --package-path ios/RehearsalCore import-check`)."""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for path in paths:
        root = Path(__file__).resolve().parent.parent
        dest = out / (Path(path).name.split(".")[0] + ".json")
        dest.write_text(json.dumps({"source": str(Path(path).resolve().relative_to(root)), **score_data(Score(path))}))
        written.append(dest)
    return written


def export(score: Score, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    meta_path = score.path.with_suffix(".piece.yaml")
    meta = yaml.safe_load(meta_path.read_text()) if meta_path.exists() else {}
    data = {
        **score_data(score),
        "work": meta.get("work", score.title),  # for the library: works, with their movements under them
        "movement": meta.get("movement", ""),
    }
    if meta.get("tempo"):
        data["tempo"] = float(meta["tempo"])
    (out / "score.json").write_text(json.dumps(data))
    (out / "score.musicxml").write_bytes(musicxml.text(score.path))  # the iPad engraves it itself
    return out


def fixtures(score: Score, out: Path, takes: list[Path], sessions: list[Path]) -> list[Path]:
    """One JSON file per case: the inputs (piano onsets, cues), the settings, and what the strings played."""
    out.mkdir(parents=True, exist_ok=True)
    cases = [(p.stem, midi_io.read_take(p), 1, 16, []) for p in takes]
    if takes:
        events, cues = _cue_take(score, midi_io.read_take(takes[0]))
        cases.append(("cues", events, 1, 16, cues))
        events, cues = _pause_take(score, midi_io.read_take(takes[0]))
        cases.append(("pause", events, 1, 16, cues))
        events, cues = _count_in_take(score, midi_io.read_take(takes[0]))
        cases.append(("count-in", events, 1, 16, cues))
        events, cues = _count_in_take(score, midi_io.read_take(takes[0]), late_s=3.0)
        cases.append(("count-in-late", events, 1, 16, cues))
    for p in sessions:
        piano = session.read(p)[0]
        cases.append(("session-" + p.stem, piano, 1, 132, []))
    written = []
    for name, events, first, last, cues in cases:
        tempo = 63.0
        res = simulate.run(score, events, first, last, clock=BeatClock(spb=60 / tempo), cues=cues)
        acc = res.accompanist
        case = {
            "name": name, "firstBar": first, "lastBar": last, "tempo": tempo,
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


def _count_in_take(score: Score, events: list[midi_io.MidiEvent],
                   late_s: float = 0.02) -> tuple[list[midi_io.MidiEvent], list[tuple[float, Cue]]]:
    """Where the strings play first: bars 1-4, stop; "from bar 3" (three beats of strings before the piano), "go"
    2 s later, and bars 3-6 from the piano's first chord, coming in `late_s` after the strings expect it (3 s: they
    hold at your entry first)."""
    out, cues = _rehearsal(score, events, [(None, 1, 5), (Cue("bar", 3), None, None)])
    t_go = cues[-1][0] + 2.0
    cues.append((t_go, Cue("go")))
    probe = simulate.run(score, out, 1, 16, clock=BeatClock(spb=60 / 63.0), cues=cues).accompanist
    entry = probe.clock.time_of(probe.restart_from) + late_s
    truth = reference.align(score, events, 1, 16).truth
    bar_start = {bar: b for b, bar in reversed(score.bar_starts())}
    at = lambda bar: min(t for t, b in truth if b >= bar_start[bar])
    t0, t1 = at(3), at(7) - 0.05
    out += [midi_io.MidiEvent(e.time - t0 + entry, e.kind, e.note, e.value) for e in events if t0 - 0.05 <= e.time < t1]
    return out, cues


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
