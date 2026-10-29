import Foundation

// rehearsal/dynamics.py, the part the engine needs: the loudness curves come in the bundle (score.json),
// worked out from the score on the Mac.

let MF_VELOCITY = 80
let TOP_VELOCITY = 127  // notes play at this; expression brings them down to their level

/// The velocity, or with notes at TOP_VELOCITY the expression (CC11, the same curve), for a level in dB from mf.
func dynamicsVelocity(_ db: Double) -> Int {
    min(127, max(1, Int(Double(MF_VELOCITY) * pow(10, db / 40) + 0.5)))
}

/// The level at `beat` on (beat, dB) points, linear between them; a step is two points at one beat (the later wins).
func levelAt(_ points: [(Double, Double)], _ beat: Double) -> Double {
    if points.isEmpty { return 0 }
    let i = upperBound(points.map { $0.0 }, beat)
    if i == 0 { return points[0].1 }
    let (b0, v0) = points[i - 1]
    if i == points.count { return v0 }
    let (b1, v1) = points[i]
    return b1 == b0 ? v0 : v0 + (v1 - v0) * (beat - b0) / (b1 - b0)
}

/// Expression for a dynamics level faded to `fade`/127 (rounding half up).
func scaleExpression(_ dyn: Int, _ fade: Int) -> Int {
    fade == 127 ? dyn : Int(Double(dyn) * Double(fade) / 127 + 0.5)
}
