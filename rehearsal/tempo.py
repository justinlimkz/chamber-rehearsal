"""Tempo and position model: a beat clock that predicts when each score beat will sound.

The clock is corrected at each piano onset with linear phase and period correction, as in the
ensemble-timing literature: phase moves a fraction `alpha` of the way to the pianist, period a fraction
`beta`. With alpha near 1 the strings follow tightly; smaller alpha would keep more of their own timing.

The score's tempo marks bend the clock: `spb` is the period at the piece's opening tempo, and a mark that says
twice as fast halves the time each beat takes from there on. So at the Kreutzer's Presto (quarter 55 to 270) the
strings play about five times faster at once, at the pace you took the Adagio, and your playing corrects it from
there. The clamps hold for the tempo at the beat the pianist played.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BeatClock:
    spb: float  # seconds per beat (quarter note)
    alpha: float = 0.7  # phase correction, 0..1
    beta: float = 0.3  # period correction, 0..1
    min_spb: float = 0.2
    max_spb: float = 2.0
    max_period_step: float = 0.08  # largest relative tempo change per onset
    t0: float = 0.0  # anchor: beat b0 sounds at time t0
    b0: float = 0.0
    marks: list[tuple[float, float]] = field(default_factory=list)  # (beat, period from there / at the opening)

    def factor(self, beat: float) -> float:
        """The period at `beat` over the period at the opening."""
        f = 1.0
        for b, k in self.marks:
            if b > beat + 1e-9:
                break
            f = k
        return f

    def units(self, a: float, b: float) -> float:
        """The beats from `a` to `b` in beats of the opening tempo (negative if b is before a)."""
        if b < a:
            return -self.units(b, a)
        u, x, f = 0.0, a, self.factor(a)
        for m, k in self.marks:
            if m <= x + 1e-9:
                continue
            if m >= b:
                break
            u, x, f = u + (m - x) * f, m, k
        return u + (b - x) * f

    def spb_at(self, beat: float) -> float:
        """Seconds per beat at `beat`."""
        return self.spb * self.factor(beat)

    def anchor(self, t: float, beat: float) -> None:
        self.t0, self.b0 = t, beat

    def beat_at(self, t: float) -> float:
        u, x = (t - self.t0) / self.spb, self.b0
        if u >= 0:
            f = self.factor(x)
            for m, k in self.marks:
                if m <= x + 1e-9:
                    continue
                if (m - x) * f >= u:
                    break
                u, x, f = u - (m - x) * f, m, k
            return x + u / f
        for i in range(len(self.marks) - 1, -1, -1):  # backwards, through the marks before b0
            m = self.marks[i][0]
            if m >= x - 1e-9:
                continue
            f = self.factor(m)
            if (x - m) * f >= -u:
                return x + u / f
            u, x = u + (x - m) * f, m
        return x + u  # before the first mark: the opening tempo

    def time_of(self, beat: float) -> float:
        return self.t0 + self.units(self.b0, beat) * self.spb

    def correct(self, t: float, beat: float, prev_beat: float | None) -> float:
        """The pianist played `beat` at time `t`. Returns the asynchrony (seconds, positive = pianist late)."""
        predicted = self.time_of(beat)
        asyn = t - predicted
        # Re-anchor at this beat: phase moves alpha of the way towards the pianist.
        self.anchor(predicted + self.alpha * asyn, beat)
        if prev_beat is not None and beat > prev_beat:
            # Period correction, per beat of the interval just played.
            step = self.beta * asyn / self.units(prev_beat, beat)
            limit = self.max_period_step * self.spb
            f = self.factor(beat)
            self.spb = min(self.max_spb / f, max(self.min_spb / f, self.spb + min(limit, max(-limit, step))))
        return asyn


def tempo_marks(tempos: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """A score's tempo marks, (beat, quarter notes a minute), as the clock's marks: (beat, the period there over
    the period at the first mark), held within 8 times either way."""
    if not tempos:
        return []
    first = tempos[0][1]
    return sorted(((b, min(8.0, max(1 / 8, first / q))) for b, q in tempos), key=lambda m: m[0])  # the last at a beat wins
