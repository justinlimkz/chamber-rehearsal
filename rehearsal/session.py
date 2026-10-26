"""Analysis of a saved live session (piano plus strings, as `live.run` writes it).

A session can hold several passes: whenever the pianist stops, the strings fall silent and wait, and the
next pass may start anywhere. The session is split into passes wherever all the strings are silent for
PASS_GAP_S, and each pass is aligned to the score on its own with `reference.align`. Each string note is assigned to the score note
of the same part and pitch whose expected time (interpolated from the piano) is nearest, which copes with
notes dropped during a pause. Timing is string onset minus piano onset where both play the same beat.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido
import numpy as np

from . import midi_io, reference
from .accompanist import Accompanist
from .score import Score
from .sound import CHANNELS

PART_OF = {ch: part for part, ch in CHANNELS.items()}
PASS_GAP_S = 0.5  # all strings silent this long: they stopped and waited, so a new pass begins


@dataclass
class Pass:
    start: float  # seconds
    first_beat: float  # score beats the piano covered
    last_beat: float
    rows: list[tuple[float, str, float]]  # (score beat, part, string minus piano in seconds)
    piano_matched: int
    piano_extra: int
    notices: list[tuple[float, str]]  # what the strings said during the pass (newer sessions only)


@dataclass
class SessionTiming:
    passes: list[Pass]

    @property
    def rows(self) -> list[tuple[float, str, float]]:
        return [r for p in self.passes for r in p.rows]


def read(path: Path) -> tuple[list[midi_io.MidiEvent], list[tuple[float, str, int]], list[float], list[tuple[float, str]]]:
    """Piano events, string onsets as (time, part, pitch), the times at which new passes begin, and markers."""
    piano, strings, breaks, markers, t = [], [], [], [], 0.0
    sounding: set[tuple[int, int]] = set()
    silent_since = None
    for msg in mido.MidiFile(str(path)):
        t += msg.time
        if msg.type == "marker":
            markers.append((t, msg.text))
        if msg.is_meta or msg.type not in ("note_on", "note_off", "control_change"):
            continue
        part = PART_OF[msg.channel]
        if part == "Piano":
            ev = midi_io.from_message(msg, t)
            if ev is not None:
                piano.append(ev)
        elif msg.type == "note_on" and msg.velocity > 0:
            if silent_since is not None and t - silent_since >= PASS_GAP_S:
                breaks.append(silent_since)
            strings.append((t, part, msg.note))
            sounding.add((msg.channel, msg.note))
            silent_since = None
        elif msg.type in ("note_on", "note_off"):
            sounding.discard((msg.channel, msg.note))
            if not sounding and silent_since is None:
                silent_since = t
    return piano, strings, breaks, markers


def analyze(score: Score, path: Path, first_bar: int = 1, last_bar: int = 16) -> SessionTiming:
    piano, strings, breaks, markers = read(path)
    notes = Accompanist(score, first_bar, last_bar).notes
    edges = [-np.inf] + breaks + [np.inf]
    passes = []
    for lo, hi in zip(edges, edges[1:]):
        ref = reference.align(score, [e for e in piano if lo <= e.time < hi], first_bar, last_bar, local=True)
        if len(ref.truth) < 2:
            continue
        beats = np.array([b for _, b in ref.truth])
        times = np.array([t for t, _ in ref.truth])
        when = dict(zip(beats.tolist(), times.tolist()))
        rows = []
        for t, part, pitch in strings:
            if not lo <= t < hi:
                continue
            cands = [n for n in notes if n.part == part and n.pitch == pitch]
            if not cands:
                continue
            n = min(cands, key=lambda n: abs(np.interp(n.onset_beat, beats, times) - t))
            if n.onset_beat in when and not n.is_grace:  # grace notes sound before the beat on purpose
                rows.append((n.onset_beat, part, t - when[n.onset_beat]))
        passes.append(Pass(max(lo, 0.0), beats[0], beats[-1], rows, len(ref.truth), len(ref.extra),
                           [(t, text) for t, text in markers if lo <= t < hi]))
    return SessionTiming(passes)
