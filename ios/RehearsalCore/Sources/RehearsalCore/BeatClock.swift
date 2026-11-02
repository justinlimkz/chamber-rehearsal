// Port of rehearsal/tempo.py.
//
// Tempo and position model: a beat clock that predicts when each score beat will sound.
//
// The clock is corrected at each piano onset with linear phase and period correction, as in the
// ensemble-timing literature: phase moves a fraction `alpha` of the way to the pianist, period a fraction
// `beta`. With alpha near 1 the strings follow tightly; smaller alpha would keep more of their own timing.
//
// The score's tempo marks bend the clock: `spb` is the period at the piece's opening tempo, and a mark that says
// twice as fast halves the time each beat takes from there on. So at the Kreutzer's Presto (quarter 55 to 270) the
// strings play about five times faster at once, at the pace you took the Adagio, and your playing corrects it from
// there. The clamps hold for the tempo at the beat the pianist played.

public struct BeatClock: Sendable {
    public var spb: Double  // seconds per beat (quarter note) at the opening tempo
    public var alpha: Double = 0.7  // phase correction, 0..1
    public var beta: Double = 0.3  // period correction, 0..1
    public var minSpb: Double = 0.2
    public var maxSpb: Double = 2.0
    public var maxPeriodStep: Double = 0.08  // largest relative tempo change per onset
    public var t0: Double = 0.0  // anchor: beat b0 sounds at time t0
    public var b0: Double = 0.0
    public var marks: [(beat: Double, factor: Double)] = []  // (beat, period from there / at the opening)

    public init(spb: Double, alpha: Double = 0.7, beta: Double = 0.3, minSpb: Double = 0.2,
                maxSpb: Double = 2.0, maxPeriodStep: Double = 0.08, t0: Double = 0.0, b0: Double = 0.0) {
        self.spb = spb; self.alpha = alpha; self.beta = beta; self.minSpb = minSpb
        self.maxSpb = maxSpb; self.maxPeriodStep = maxPeriodStep; self.t0 = t0; self.b0 = b0
    }

    public mutating func anchor(_ t: Double, _ beat: Double) {
        t0 = t
        b0 = beat
    }

    /// The period at `beat` over the period at the opening.
    public func factor(_ beat: Double) -> Double {
        var f = 1.0
        for m in marks {
            if m.beat > beat + 1e-9 { break }
            f = m.factor
        }
        return f
    }

    /// The beats from `a` to `b` in beats of the opening tempo (negative if b is before a).
    public func units(_ a: Double, _ b: Double) -> Double {
        if b < a { return -units(b, a) }
        var u = 0.0, x = a, f = factor(a)
        for m in marks {
            if m.beat <= x + 1e-9 { continue }
            if m.beat >= b { break }
            u += (m.beat - x) * f; x = m.beat; f = m.factor
        }
        return u + (b - x) * f
    }

    /// Seconds per beat at `beat`.
    public func spbAt(_ beat: Double) -> Double { spb * factor(beat) }

    public func beatAt(_ t: Double) -> Double {
        var u = (t - t0) / spb, x = b0
        if u >= 0 {
            var f = factor(x)
            for m in marks {
                if m.beat <= x + 1e-9 { continue }
                if (m.beat - x) * f >= u { break }
                u -= (m.beat - x) * f; x = m.beat; f = m.factor
            }
            return x + u / f
        }
        for m in marks.reversed() {  // backwards, through the marks before b0
            if m.beat >= x - 1e-9 { continue }
            let f = factor(m.beat)
            if (x - m.beat) * f >= -u { return x + u / f }
            u += (x - m.beat) * f; x = m.beat
        }
        return x + u  // before the first mark: the opening tempo
    }

    public func timeOf(_ beat: Double) -> Double { t0 + units(b0, beat) * spb }

    /// The pianist played `beat` at time `t`. Returns the asynchrony (seconds, positive = pianist late).
    @discardableResult
    public mutating func correct(_ t: Double, _ beat: Double, prevBeat: Double?) -> Double {
        let predicted = timeOf(beat)
        let asyn = t - predicted
        // Re-anchor at this beat: phase moves alpha of the way towards the pianist.
        anchor(predicted + alpha * asyn, beat)
        if let prev = prevBeat, beat > prev {
            // Period correction, per beat of the interval just played.
            let step = beta * asyn / units(prev, beat)
            let limit = maxPeriodStep * spb
            let f = factor(beat)
            spb = min(maxSpb / f, max(minSpb / f, spb + min(limit, max(-limit, step))))
        }
        return asyn
    }
}

/// A score's tempo marks, (beat, quarter notes a minute), as the clock's marks: (beat, the period there over the
/// period at the first mark), held within 8 times either way. (tempo.tempo_marks)
public func tempoMarks(_ tempos: [(Double, Double)]) -> [(beat: Double, factor: Double)] {
    guard let first = tempos.first?.1 else { return [] }
    return tempos.map { (beat: $0.0, factor: min(8.0, max(1.0 / 8, first / $0.1))) }
        .enumerated().sorted { ($0.element.beat, $0.offset) < ($1.element.beat, $1.offset) }.map { $0.element }
}
