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

On top of the markings, one player's reading of each phrase (`Expression`; compared with a real recording of this
movement, 2026-10-29, our strings varied half as much from beat to beat and their held notes didn't swell). The
reading is a consistent one, not an average of many: the marked levels spread a little wider; each phrase (notes
up to a rest) rises to its highest note and falls away after it, ending a little softer than it began; and a held note
swells into its middle and tapers off. Same score, same shape, every time.

Velocity picks the sample layer (the cello has soft, medium and loud ones): `layer_velocity` from the level where
a phrase starts, kept while the part plays on, and the expression makes up the difference (`expression`).
"""
from __future__ import annotations

import bisect
import math
import warnings
from dataclasses import dataclass

import numpy as np
import partitura as pt

LEVELS_DB = {"pp": -12.0, "p": -7.0, "mp": -3.5, "mf": 0.0, "f": 4.0, "ff": 7.0}
DEFAULT_DB = 0.0  # before the part's first marking: mf
STEP_DB = 4.0  # a hairpin with no dynamic at its end
EASE = 0  # pieces of the S-curve each ramp follows (0: straight ramps)
UNSPANNED_BEATS = 8.0  # "cresc." with no end: two bars of 4/4
END_SLACK = 1.0  # a dynamic this many beats either side of a hairpin's end is where it goes
MF_VELOCITY = 80
TOP_VELOCITY = 127  # without layers, notes play at this; expression brings them down to their level
# Sample layers: velocities inside the cello's soft (0-79), medium (80-102) and loud (103-127) layers, and the
# highest level (dB from mf) each is used from: at least 4 dB below where the expression would run out, so a
# phrase can still grow.
LAYERS = ((-5.0, 79), (0.0, 102), (math.inf, 127))


@dataclass(frozen=True)
class Expression:
    range: float = 1.4  # the marked levels and hairpins, spread this much wider around mf
    arch_db: float = 4.0  # a phrase starts 0.4x this below, rises to this above at its highest note, ends 0.6x below
    swell_db: float = 3.0  # a held note swells this much into its middle
    swell_beats: float = 1.5  # notes at least this long swell
    phrase_gap: float = 0.5  # a rest this long (beats) ends a phrase
    grid: float = 0.25  # beats between the curve's points under the notes


EXPRESSION = Expression()
SUBTLE = Expression(range=1.15, arch_db=1.5, swell_db=1.25)


def velocity(db: float) -> int:
    """The velocity, or with notes at TOP_VELOCITY the expression (CC11, the same curve), for a level."""
    return int(min(127, max(1, int(MF_VELOCITY * 10 ** (db / 40) + 0.5))))


def layer_velocity(db: float) -> int:
    """The velocity for a note starting a phrase at this level: it picks the sample layer."""
    return next(v for top, v in LAYERS if db <= top)


def expression(db: float, note_velocity: int) -> int:
    """CC11 that brings notes of `note_velocity` to the level `db` (velocity and expression add in dB)."""
    return velocity(db - 40 * math.log10(note_velocity / TOP_VELOCITY))


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


def phrases(notes) -> list[list]:
    """A part's notes (score.ScoreNote, in onset order) split where a rest of at least `phrase_gap` beats falls."""
    out, end = [], -math.inf
    for n in notes:
        if not out or n.onset_beat - end >= EXPRESSION.phrase_gap:
            out.append([])
        out[-1].append(n)
        end = max(end, n.onset_beat + n.duration_beat) if out[-1][:-1] else n.onset_beat + n.duration_beat
    return out


def shaped(points: list[tuple[float, float]], notes, ex: Expression) -> list[tuple[float, float]]:
    """The marked curve widened by `ex.range`, plus a phrase arch and note swells, as points every `ex.grid`
    beats under the notes (and at every marked point, so steps stay steps)."""
    arch: list[tuple[float, float]] = []  # (beat, dB) breakpoints, linear between, 0 outside phrases
    swells: list[tuple[float, float, float]] = []  # (start, end, peak dB)
    for ph in phrases(notes):
        start = ph[0].onset_beat
        end = max(n.onset_beat + n.duration_beat for n in ph)
        top = max(ph, key=lambda n: (n.pitch, n.duration_beat, -n.onset_beat))  # the highest note; the longest of those
        peak = top.onset_beat
        if peak > start:
            arch += [(start, -0.4 * ex.arch_db), (peak, ex.arch_db)]
        else:
            arch += [(start, ex.arch_db)]
        arch += [(end, -0.6 * ex.arch_db)] if end > peak else []
        arch.append((end + 1e-6, 0.0))
        swells += [(n.onset_beat, n.onset_beat + n.duration_beat, ex.swell_db) for n in ph
                   if n.duration_beat >= ex.swell_beats]

    def arch_at(b: float) -> float:
        i = bisect.bisect_right([p[0] for p in arch], b)
        if i == 0 or i == len(arch):
            return 0.0
        (b0, v0), (b1, v1) = arch[i - 1], arch[i]
        return v0 + (v1 - v0) * (b - b0) / (b1 - b0) if b1 > b0 else v0

    def swell_at(b: float) -> float:
        best = 0.0
        for s, e, d in swells:
            if s <= b <= e:
                x = (b - s) / (e - s)  # -1 dB at the start, `d` at 55%, -1.5 dB at the end
                best = max(best, -1 + (d + 1) * x / 0.55 if x < 0.55 else d - (d + 1.5) * (x - 0.55) / 0.45)
        return best if best else 0.0

    beats = {p[0] for p in points if p[0] != -math.inf}
    for n in notes:
        k = 0
        while n.onset_beat + k * ex.grid < n.onset_beat + n.duration_beat:
            beats.add(round(n.onset_beat + k * ex.grid, 6))
            k += 1
        beats.add(n.onset_beat + n.duration_beat)
    out = []
    marked = {}
    for b, v in points:  # a step: both of its values, in order
        marked.setdefault(b, []).append(v)
    for b in sorted(beats):
        add = arch_at(b) + swell_at(b)
        for v in marked.get(b, [level_at(points, b)]):
            out.append((b, v * ex.range + add))
    return out


def curves(score, names, step_db: float = STEP_DB, ease: int = EASE,
           ex: Expression | None = EXPRESSION) -> dict[str, list[tuple[float, float]]]:
    """Each part's loudness curve, without the leading point at -inf (the first point's level holds before
    it), shaped as `ex` reads it (None: the markings alone). Kept on the score: every restart makes a new
    accompanist."""
    cache = score.__dict__.setdefault("_dynamics", {})
    for name in names:
        key = (name, step_db, ease, ex)
        if key not in cache:
            pts = curve(score.part(name), step_db, ease)
            cache[key] = shaped(pts, score.notes(name), ex) if ex else pts[1:]
    return {name: cache[name, step_db, ease, ex] for name in names}
