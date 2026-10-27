"""Tempo and position model: a beat clock that predicts when each score beat will sound.

The clock is corrected at each piano onset with linear phase and period correction, as in the
ensemble-timing literature: phase moves a fraction `alpha` of the way to the pianist, period a fraction
`beta`. With alpha near 1 the strings follow tightly (piano leads); smaller alpha keeps more of their
own timing (milestone 5's strings-lead coupling). Where the strings lead, the period is also pulled a
fraction `pull` back towards their own tempo (`home_spb`), so they keep it rather than drift to the
pianist's.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BeatClock:
    spb: float  # seconds per beat (quarter note)
    alpha: float = 0.7  # phase correction, 0..1
    beta: float = 0.3  # period correction, 0..1
    min_spb: float = 0.35
    max_spb: float = 2.0
    max_period_step: float = 0.08  # largest relative tempo change per onset
    t0: float = 0.0  # anchor: beat b0 sounds at time t0
    b0: float = 0.0

    def anchor(self, t: float, beat: float) -> None:
        self.t0, self.b0 = t, beat

    def beat_at(self, t: float) -> float:
        return self.b0 + (t - self.t0) / self.spb

    def time_of(self, beat: float) -> float:
        return self.t0 + (beat - self.b0) * self.spb

    def correct(self, t: float, beat: float, prev_beat: float | None, alpha: float | None = None,
                beta: float | None = None, home_spb: float | None = None, pull: float = 0.0) -> float:
        """The pianist played `beat` at time `t`. Returns the asynchrony (seconds, positive = pianist late).
        `alpha` and `beta` override the clock's own for this correction."""
        alpha = self.alpha if alpha is None else alpha
        beta = self.beta if beta is None else beta
        predicted = self.time_of(beat)
        asyn = t - predicted
        # Re-anchor at this beat: phase moves alpha of the way towards the pianist.
        self.anchor(predicted + alpha * asyn, beat)
        if prev_beat is not None and beat > prev_beat:
            # Period correction, per beat of the interval just played, plus the pull home.
            step = beta * asyn / (beat - prev_beat)
            if home_spb is not None:
                step += pull * (home_spb - self.spb)
            limit = self.max_period_step * self.spb
            self.spb = min(self.max_spb, max(self.min_spb, self.spb + min(limit, max(-limit, step))))
        return asyn
