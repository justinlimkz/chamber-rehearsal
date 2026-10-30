// Port of rehearsal/tempo.py.
//
// Tempo and position model: a beat clock that predicts when each score beat will sound.
//
// The clock is corrected at each piano onset with linear phase and period correction, as in the
// ensemble-timing literature: phase moves a fraction `alpha` of the way to the pianist, period a fraction
// `beta`. With alpha near 1 the strings follow tightly; smaller alpha would keep more of their own timing.

public struct BeatClock: Sendable {
    public var spb: Double  // seconds per beat (quarter note)
    public var alpha: Double = 0.7  // phase correction, 0..1
    public var beta: Double = 0.3  // period correction, 0..1
    public var minSpb: Double = 0.35
    public var maxSpb: Double = 2.0
    public var maxPeriodStep: Double = 0.08  // largest relative tempo change per onset
    public var t0: Double = 0.0  // anchor: beat b0 sounds at time t0
    public var b0: Double = 0.0

    public init(spb: Double, alpha: Double = 0.7, beta: Double = 0.3, minSpb: Double = 0.35,
                maxSpb: Double = 2.0, maxPeriodStep: Double = 0.08, t0: Double = 0.0, b0: Double = 0.0) {
        self.spb = spb; self.alpha = alpha; self.beta = beta; self.minSpb = minSpb
        self.maxSpb = maxSpb; self.maxPeriodStep = maxPeriodStep; self.t0 = t0; self.b0 = b0
    }

    public mutating func anchor(_ t: Double, _ beat: Double) {
        t0 = t
        b0 = beat
    }

    public func beatAt(_ t: Double) -> Double { b0 + (t - t0) / spb }

    public func timeOf(_ beat: Double) -> Double { t0 + (beat - b0) * spb }

    /// The pianist played `beat` at time `t`. Returns the asynchrony (seconds, positive = pianist late).
    @discardableResult
    public mutating func correct(_ t: Double, _ beat: Double, prevBeat: Double?) -> Double {
        let predicted = timeOf(beat)
        let asyn = t - predicted
        // Re-anchor at this beat: phase moves alpha of the way towards the pianist.
        anchor(predicted + alpha * asyn, beat)
        if let prev = prevBeat, beat > prev {
            // Period correction, per beat of the interval just played.
            let step = beta * asyn / (beat - prev)
            let limit = maxPeriodStep * spb
            spb = min(maxSpb, max(minSpb, spb + min(limit, max(-limit, step))))
        }
        return asyn
    }
}
