"""Reference alignment of a real take to the score, so the follower can be scored on real playing.

Offline, with the whole take in hand: performed notes are grouped into chords, and the chord sequence is
aligned to the score's onset groups by edit distance on pitch sets. Unlike the live follower it sees the
future, so it serves as ground truth. The opening repeats identical chords, so matching whole chords in
order is more reliable here than matching single notes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .midi_io import MidiEvent
from .score import Score

CHORD_GAP_S = 0.05  # a note more than this after the previous one starts a new chord
SKIP_COST = 1.0  # cost of an extra performed chord or a skipped score chord


@dataclass
class Reference:
    truth: list[tuple[float, float]]  # (performance seconds, score beat), per matched score onset group
    score_groups: int
    skipped: list[float]  # score beats with no performed chord
    extra: list[float]  # performance seconds of chords that match nothing


def chords(events: list[MidiEvent]) -> list[tuple[float, frozenset[int]]]:
    """Group note-ons into chords: (time of first note, pitches)."""
    res: list[tuple[float, set[int]]] = []
    last = -np.inf
    for e in sorted((e for e in events if e.kind == "note_on"), key=lambda e: e.time):
        if e.time - last > CHORD_GAP_S:
            res.append((e.time, set()))
        res[-1][1].add(e.note)
        last = e.time
    return [(t, frozenset(p)) for t, p in res]


def score_groups(score: Score, first_bar: int, last_bar: int, part: str = "Piano") -> list[tuple[float, frozenset[int]]]:
    """Score onset groups in a bar range of the unfolded part: (beat, pitches)."""
    starts = [b for b, bar in score.bar_starts(part) if bar == first_bar]
    ends = [b for b, bar in score.bar_starts(part) if bar == last_bar + 1]
    lo, hi = starts[0], ends[0] if ends else np.inf
    groups: dict[float, set[int]] = {}
    for n in score.notes(part):
        if lo <= n.onset_beat < hi:
            groups.setdefault(n.onset_beat, set()).add(n.pitch)
    return [(b, frozenset(p)) for b, p in sorted(groups.items())]


def align(score: Score, events: list[MidiEvent], first_bar: int = 1, last_bar: int = 16) -> Reference:
    perf = chords(events)
    ref = score_groups(score, first_bar, last_bar)
    n, m = len(perf), len(ref)

    def cost(i: int, j: int) -> float:
        a, b = perf[i][1], ref[j][1]
        return 1.0 - len(a & b) / len(a | b)  # Jaccard distance; 1 means no pitch in common

    D = np.zeros((n + 1, m + 1))
    D[:, 0] = np.arange(n + 1) * SKIP_COST
    D[0, :] = np.arange(m + 1) * SKIP_COST
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i, j] = min(D[i - 1, j - 1] + 2 * cost(i - 1, j - 1), D[i - 1, j] + SKIP_COST, D[i, j - 1] + SKIP_COST)

    truth, skipped, extra = [], [], []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and np.isclose(D[i, j], D[i - 1, j - 1] + 2 * cost(i - 1, j - 1)) and cost(i - 1, j - 1) < 1.0:
            truth.append((perf[i - 1][0], ref[j - 1][0]))
            i, j = i - 1, j - 1
        elif i > 0 and np.isclose(D[i, j], D[i - 1, j] + SKIP_COST):
            extra.append(perf[i - 1][0])
            i -= 1
        else:
            skipped.append(ref[j - 1][0])
            j -= 1
    return Reference(sorted(truth), m, sorted(skipped), sorted(extra))
