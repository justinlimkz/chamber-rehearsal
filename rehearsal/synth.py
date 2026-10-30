"""Synthetic piano takes from the score, for testing the accompanist without playing.

A take has a known ground truth: for each performed onset, the score beat it came from. That lets us
measure the accompanist offline, including with deliberate wrong, missing and extra notes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .midi_io import MidiEvent
from .score import Score


@dataclass
class SynthTake:
    events: list[MidiEvent]
    truth: list[tuple[float, float]] = field(default_factory=list)  # (performance seconds, score beat), per onset group


def make_take(
    score: Score,
    first_bar: int = 1,
    last_bar: int = 16,
    bpm: float = 63.0,
    rubato: float = 0.08,  # fractional tempo wobble, slow sine
    jitter_ms: float = 12.0,
    wrong_rate: float = 0.0,  # fraction of onsets replaced by a neighbouring semitone
    drop_rate: float = 0.0,  # fraction of onsets left out
    extra_rate: float = 0.0,  # fraction of onsets followed by a stray extra note
    seed: int = 0,
) -> SynthTake:
    rng = np.random.default_rng(seed)
    notes = [n for n in score.notes("Piano") if first_bar <= n.bar <= last_bar]
    t_beat0 = min(n.onset_beat for n in notes)

    def beat_to_sec(b: float) -> float:
        x = b - t_beat0
        # integrate a slowly varying tempo: spb = (60/bpm) * (1 + rubato*sin(2*pi*x/24))
        return (60.0 / bpm) * (x - rubato * 24 / (2 * np.pi) * (np.cos(2 * np.pi * x / 24) - 1))

    events: list[MidiEvent] = []
    truth: dict[float, float] = {}
    # Timing error belongs to the chord (a player's hands land together); notes within it spread by a few ms.
    group_jitter = {b: rng.normal(0, jitter_ms / 1000) for b in {round(n.onset_beat, 4) for n in notes}}
    for n in notes:
        t = beat_to_sec(n.onset_beat) + group_jitter[round(n.onset_beat, 4)] + rng.normal(0, 0.004)
        t = max(t, 0.0)
        off = beat_to_sec(n.onset_beat + max(n.duration_beat, 0.25) * 0.95)
        if rng.random() < drop_rate:
            continue
        pitch = n.pitch + int(rng.choice([-1, 1])) if rng.random() < wrong_rate else n.pitch
        vel = int(np.clip(rng.normal(70, 8), 30, 110))
        events.append(MidiEvent(t, "note_on", pitch, vel))
        events.append(MidiEvent(max(off, t + 0.05), "note_off", pitch, 0))
        key = round(n.onset_beat, 4)
        truth[key] = min(t, truth.get(key, t))
        if rng.random() < extra_rate:
            events.append(MidiEvent(t + 0.03, "note_on", pitch + 2, 50))
            events.append(MidiEvent(t + 0.25, "note_off", pitch + 2, 0))
    events.sort(key=lambda e: e.time)
    return SynthTake(events, sorted((t, b) for b, t in truth.items()))
