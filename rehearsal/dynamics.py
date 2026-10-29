"""Loudness of each string note from the score's dynamics: the marked level (pp..ff) and hairpins.

A marked dynamic sets the level from where it is written. A crescendo or diminuendo ramps the level
across its span: to the dynamic marked where it ends, if there is one going the same way, otherwise by
one step (STEP_DB), and the new level holds until the next marking. Hairpins with no dynamic at their
end stay within a step of the last marked dynamic (and at most ff), so a run of crescendos doesn't
climb. A "cresc." written without a span ramps over UNSPANNED_BEATS. The level is in dB from mf and
is played as expression (CC11) on notes at velocity 127: in Apple's sampler (and FluidSynth) with our
SoundFont, loudness follows 40 * log10(velocity / 80) dB (measured 2026-10-29) and expression the same
curve, so velocity 127 with expression 80 is mf, as loud as every note used to be (velocity 80). The
accompanist updates the expression as the beat moves, so a hairpin shapes notes already sounding;
velocities alone made a step at each new note (heard in round 3).

Accents and sforzandi are not read: this score has none (only staccato dots).
"""
from __future__ import annotations

import bisect
import warnings

import numpy as np
import partitura as pt

LEVELS_DB = {"pp": -12.0, "p": -7.0, "mp": -3.5, "mf": 0.0, "f": 4.0, "ff": 7.0}
DEFAULT_DB = 0.0  # before the part's first marking: mf
STEP_DB = 4.0  # a hairpin with no dynamic at its end
EASE = 0  # pieces of the S-curve each ramp follows (0: straight ramps)
UNSPANNED_BEATS = 8.0  # "cresc." with no end: two bars of 4/4
END_SLACK = 1.0  # a dynamic this many beats either side of a hairpin's end is where it goes
MF_VELOCITY = 80
TOP_VELOCITY = 127  # notes play at this; expression brings them down to their level


def velocity(db: float) -> int:
    """The velocity, or with notes at TOP_VELOCITY the expression (CC11, the same curve), for a level."""
    return int(min(127, max(1, int(MF_VELOCITY * 10 ** (db / 40) + 0.5))))


def curve(part, step_db: float = STEP_DB, ease: int = EASE) -> list[tuple[float, float]]:
    """Breakpoints (beat, dB) of the part's loudness, linear between them; a step is two points at one beat.
    `ease` > 0: each ramp follows an S-curve (smoothstep) drawn with that many pieces, so a hairpin starts and
    ends gently and a crescendo turning into a diminuendo rounds off instead of peaking."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        q = part.quarter_map
        marks = sorted((float(q(d.start.t)), LEVELS_DB[d.text.strip().lower()])
                       for d in part.iter_all(pt.score.ConstantLoudnessDirection)
                       if (d.text or "").strip().lower() in LEVELS_DB)
        pins = []
        for cls, sign in ((pt.score.IncreasingLoudnessDirection, 1), (pt.score.DecreasingLoudnessDirection, -1)):
            for d in part.iter_all(cls):
                s = float(q(d.start.t))
                e = float(q(d.end.t)) if d.end is not None else s
                pins.append((s, e if e > s else s + UNSPANNED_BEATS, sign))
    pins.sort()
    # Marks first at equal times: a hairpin starting with a dynamic starts from it.
    events = sorted([(b, 0, db, None) for b, db in marks] + [(s, 1, e, sign) for s, e, sign in pins],
                    key=lambda x: (x[0], x[1]))
    points = [(-np.inf, DEFAULT_DB)]
    marked = DEFAULT_DB  # the last marked dynamic

    for beat, kind, x, sign in events:
        cur = level_at(points, beat)
        points = [p for p in points if p[0] <= beat]  # a new event cuts short a ramp still running
        if points[-1][0] < beat:
            points.append((beat, cur))
        if kind == 0:
            points.append((beat, x))
            marked = x
            continue
        end = x
        near = [db for b, db in marks if abs(b - end) <= END_SLACK and (db - cur) * sign > 0]
        target = near[0] if near else min(marked + step_db, LEVELS_DB["ff"], max(marked - step_db, cur + sign * step_db))
        points.append((end, target))
    return _eased(points, ease) if ease else points


def _eased(points: list[tuple[float, float]], pieces: int) -> list[tuple[float, float]]:
    res = points[:1]
    for (b0, v0), (b1, v1) in zip(points, points[1:]):
        if b0 < b1 and v0 != v1 and b0 != -np.inf:
            for k in range(1, pieces):
                x = k / pieces
                res.append((b0 + (b1 - b0) * x, v0 + (v1 - v0) * x * x * (3 - 2 * x)))
        res.append((b1, v1))
    return res


def level_at(points: list[tuple[float, float]], beat: float) -> float:
    i = bisect.bisect_right([p[0] for p in points], beat)  # points[i-1] is the last at or before `beat`
    b0, v0 = points[i - 1]
    if i == 0:
        return points[0][1] if points else DEFAULT_DB
    if i == len(points) or b0 == -np.inf:
        return v0
    b1, v1 = points[i]
    return v0 if b1 == b0 else v0 + (v1 - v0) * (beat - b0) / (b1 - b0)


def curves(score, names, step_db: float = STEP_DB, ease: int = EASE) -> dict[str, list[tuple[float, float]]]:
    """Each part's loudness curve, without the leading point at -inf (the first point's level holds before
    it). Kept on the score: every restart makes a new accompanist."""
    cache = score.__dict__.setdefault("_dynamics", {})
    for name in names:
        if (name, step_db, ease) not in cache:
            cache[name, step_db, ease] = curve(score.part(name), step_db, ease)[1:]
    return {name: cache[name, step_db, ease] for name in names}
