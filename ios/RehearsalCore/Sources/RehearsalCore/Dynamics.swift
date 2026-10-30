import Foundation

// rehearsal/dynamics.py, the part the engine needs: the loudness curves come in the bundle (score.json),
// worked out from the score on the Mac.

let MF_VELOCITY = 80
let TOP_VELOCITY = 127  // notes play at this; expression brings them down to their level

/// The velocity, or with notes at TOP_VELOCITY the expression (CC11, the same curve), for a level in dB from mf.
func dynamicsVelocity(_ db: Double) -> Int {
    min(127, max(1, Int(Double(MF_VELOCITY) * pow(10, db / 40) + 0.5)))
}

/// Sample layers (dynamics.LAYERS): the velocity for a phrase starting at a level, inside the cello's soft, medium
/// and loud layers.
let LAYERS: [(top: Double, velocity: Int)] = [(-5.0, 79), (0.0, 102), (.infinity, 127)]

let PHRASE_GAP = 0.5  // a rest this long (beats) ends a phrase (dynamics.Expression.phrase_gap)

func layerVelocity(_ db: Double) -> Int { LAYERS.first { db <= $0.top }!.velocity }

/// CC11 that brings notes of `noteVelocity` to the level `db` (dynamics.expression).
func expressionFor(_ db: Double, _ noteVelocity: Int) -> Int {
    dynamicsVelocity(db - 40 * log10(Double(noteVelocity) / Double(TOP_VELOCITY)))
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
