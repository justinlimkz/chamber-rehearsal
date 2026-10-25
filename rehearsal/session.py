"""Analysis of a saved live session (piano plus strings, as `live.run` writes it).

The piano is aligned to the score with `reference.align`. Each string note is assigned to the score note
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


@dataclass
class SessionTiming:
    rows: list[tuple[float, str, float]]  # (score beat, part, string minus piano in seconds)
    piano_matched: int
    piano_groups: int
    piano_extra: int


def read(path: Path) -> tuple[list[midi_io.MidiEvent], list[tuple[float, str, int]]]:
    """Piano events, and string onsets as (time, part, pitch)."""
    piano, strings, t = [], [], 0.0
    for msg in mido.MidiFile(str(path)):
        t += msg.time
        if msg.is_meta or msg.type not in ("note_on", "note_off", "control_change"):
            continue
        part = PART_OF[msg.channel]
        if part == "Piano":
            ev = midi_io.from_message(msg, t)
            if ev is not None:
                piano.append(ev)
        elif msg.type == "note_on" and msg.velocity > 0:
            strings.append((t, part, msg.note))
    return piano, strings


def analyze(score: Score, path: Path, first_bar: int = 1, last_bar: int = 16) -> SessionTiming:
    piano, strings = read(path)
    ref = reference.align(score, piano, first_bar, last_bar)
    beats = np.array([b for _, b in ref.truth])
    times = np.array([t for t, _ in ref.truth])
    when = dict(zip(beats.tolist(), times.tolist()))
    notes = Accompanist(score, first_bar, last_bar).notes
    rows = []
    for t, part, pitch in strings:
        cands = [n for n in notes if n.part == part and n.pitch == pitch]
        if not cands:
            continue
        n = min(cands, key=lambda n: abs(np.interp(n.onset_beat, beats, times) - t))
        if n.onset_beat in when and not n.is_grace:  # grace notes sound before the beat on purpose
            rows.append((n.onset_beat, part, t - when[n.onset_beat]))
    return SessionTiming(rows, len(ref.truth), ref.score_groups, len(ref.extra))
