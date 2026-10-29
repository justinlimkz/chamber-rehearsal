// Port of rehearsal/simulate.py `run(..., follow=False)`: offline run of the accompanist on a recorded take.
//
// The take is replayed in virtual time, 1 ms per step, feeding piano onsets (and cues) to the same
// `Accompanist` the live path uses. No follower, no reference alignment / summary.

public let STEP_S = 0.001

public struct SimResult {
    public var outs: [Out]  // with `time` set to when the event was actually emitted
    public var accompanist: Accompanist
    public var log: [LogEntry] { accompanist.log }
    public var notices: [Notice] { accompanist.notices }
}

public enum Simulator {
    /// `onsets`: (time, pitch) of the piano note-ons.
    public static func run(score: ScoreData, onsets: [(Double, Int)], firstBar: Int = 1, lastBar: Int = 16,
                           clock: BeatClock? = nil, cues: [(Double, Cue)] = [],
                           leadership: [Passage]? = nil, dynamics: Bool = true) -> SimResult {
        enum Input { case piano(Int), cue(Cue) }
        // (time, kind, payload); kind 0 = piano, 2 = cue. Sorted by (time, kind), stable.
        var inputs: [(t: Double, kind: Int, x: Input)] = onsets.map { ($0.0, 0, .piano($0.1)) }
        inputs += cues.map { ($0.0, 2, .cue($0.1)) }
        inputs = inputs.enumerated().sorted { a, b in
            if a.element.t != b.element.t { return a.element.t < b.element.t }
            if a.element.kind != b.element.kind { return a.element.kind < b.element.kind }
            return a.offset < b.offset
        }.map { $0.element }

        let acc = Accompanist(score: score, firstBar: firstBar, lastBar: lastBar,
                              clock: clock ?? BeatClock(spb: 60.0 / 63.0), leadership: leadership,
                              dynamics: dynamics)
        var outs: [Out] = []
        var i = 0, now = 0.0
        let end = inputs.map { $0.t }.max()! + 5.0
        while now < end && !acc.finished {
            while i < inputs.count && inputs[i].t <= now {
                switch inputs[i].x {
                case .piano(let p): acc.onPianoOnset(inputs[i].t, p)
                case .cue(let c): acc.onCue(inputs[i].t, c)
                }
                i += 1
            }
            for o in acc.due(now) {
                outs.append(Out(now, o.kind, o.part, pitch: o.pitch, velocity: o.velocity, beat: o.beat))
            }
            now += STEP_S
        }
        outs += acc.flush(now)
        return SimResult(outs: outs, accompanist: acc)
    }
}
