// Port of rehearsal/accompanist.py (with voice.Cue).
//
// Accompaniment scheduler: plays the string parts ahead of the predicted beats.
//
// Event-driven and clock-agnostic, so the same object runs live (wall clock) and offline (take time):
// feed it piano notes, follower positions and voice cues, and ask `due(now)` for what to play.
//
// Where the pianist is comes from two sources:
// - Each piano chord is matched to a chord of the piano part near the beat clock's prediction, by pitch
//   set, preferring the next chord in the score. This keeps the clock steady in bars of repeated
//   identical chords, where the follower runs an eighth ahead, and the distinct bass notes on downbeats
//   pull it back if it slips.
// - The follower corrects gross drift: if it disagrees with the matched chord by more than a beat several
//   chords running, the position jumps to the follower.
//
// Who leads: by default the piano always leads and the clock follows it closely. Given a leadership
// annotation, each piano chord is coupled by its passage's setting instead: the clock moves `coupling`
// of the way to the pianist, waits a little more when the pianist is late than it hurries when they are
// early, and where the strings lead it is pulled back towards the strings' own tempo (`homeSpb`: the
// starting tempo, then whatever tempo the last piano-led passage ended in).
//
// Stopping and starting again:
// - When the pianist stops, the strings carry on for a beat and a half, then trail off: notes that end
//   soon finish, longer ones fade out. Then they wait.
// - While waiting, playing on (or going back a couple of chords) restarts them where the notes fit.
//   Going back further needs a voice cue ("from bar 9", "from the top", "again"): after a cue they wait
//   for the pianist's first notes there.

import Foundation

public let STRING_PARTS = ["Violin", "Viola", "Violoncello"]
let CHORD_GAP_S = 0.04  // a piano chord is complete this long after its last note (or as soon as all its notes are in)
let ROLLED_FRACTION = 0.3  // a pitch of the last chord this soon (fraction of the time to the next chord) is a late chord note
let GRACE_S = 0.07  // grace notes (zero duration in the score) sound this long, just before the beat
let LEGATO = 0.95  // string notes stop at this fraction of their written length (min 50 ms)
let DRIFT_BEATS = 1.0  // follower/matched-chord disagreement that counts as drift
let DRIFT_COUNT = 3  // consecutive drifting chords before the position jumps to the follower

let CARRY_BEATS = 1.5  // past an expected piano chord with no piano, the strings carry on this long...
let TRAIL_S = 0.8  // ...then trail off over this long: notes ending sooner finish, the rest fade out
let FADE_STEP = 8  // expression (CC11) change per fade message
let RESTART_BACK_BEATS = 1.5  // without a cue, a restart may go back this far from the last chord played
let CUE_BACK_BEATS = 0.5  // after a cue, slack before its first piano chord
let RESTART_MIN_SIM = 0.6  // a restart needs a chord this similar (Jaccard) to a score chord in the window
// (0.5 let bar 6's G-D-Eb pass for bar 8's G-C-Eb; 0.6 still allows one wrong note in a four-note chord)
let PROBATION_CHORDS = 2  // after a restart, chords that must also fit, or the strings stop again
let PROBATION_MIN_SIM = 0.34

// Chord matching: score = pitch similarity - penalties. Candidates are the next expected chord and any
// from 1 beat before the last match to 2 beats after it.
let TIME_PENALTY = 0.5  // per beat between the candidate and the clock's prediction
let SKIP_PENALTY = 0.4  // per score chord skipped past the next expected one
let BACK_PENALTY = 0.6  // per score chord stepped back (to undo an earlier wrong match)
let MIN_SCORE = 0.0  // below this the chord is treated as stray notes

// Coupling by passage (only with a leadership annotation). A coupling of COUPLING["piano"] is the
// clock's own alpha and beta, so piano-led passages behave exactly as without an annotation.
let BETA_PER_COUPLING = 0.3 / 0.7  // period correction scales with the coupling
let WAIT_EXTRA = 0.15  // extra phase correction when the pianist is late: the strings wait a little
let HOME_PULL = 0.3  // per chord, at coupling 0, the period moves this fraction back to the strings' tempo

/// A voice cue (voice.Cue).
public struct Cue: Equatable, Sendable, CustomStringConvertible {
    public var kind: String  // "bar" | "top" | "again"
    public var bar: Int = 0
    public var pickup: Bool = false  // "from the pickup to bar N"

    public init(kind: String, bar: Int = 0, pickup: Bool = false) {
        self.kind = kind; self.bar = bar; self.pickup = pickup
    }

    public var description: String {
        if kind == "bar" {
            return pickup ? "from the pickup to bar \(bar)" : "from bar \(bar)"
        }
        return kind == "top" ? "from the top" : "again"
    }
}

public struct StringNote: Equatable, Sendable {
    public var part: String
    public var pitch: Int
    public var onsetBeat: Double
    public var durationBeat: Double
    public var velocity: Int

    public var isGrace: Bool { durationBeat <= 0 }
}

/// A string event the sound engine should play. `time` is when it was meant to sound.
public struct Out: Equatable, Sendable {
    public var time: Double
    public var kind: String  // "on" | "off" | "expr" (expression, CC11, value in `velocity`)
    public var part: String
    public var pitch: Int = 0
    public var velocity: Int = 0
    public var beat: Double = 0.0

    public init(_ time: Double, _ kind: String, _ part: String, pitch: Int = 0, velocity: Int = 0, beat: Double = 0.0) {
        self.time = time; self.kind = kind; self.part = part
        self.pitch = pitch; self.velocity = velocity; self.beat = beat
    }
}

public struct LogEntry: Equatable, Sendable {
    public var t: Double, beat: Double, asyn: Double
    public init(t: Double, beat: Double, asyn: Double) { self.t = t; self.beat = beat; self.asyn = asyn }
}

public struct Notice: Equatable, Sendable {
    public var t: Double
    public var text: String
    public init(t: Double, text: String) { self.t = t; self.text = text }
}

public final class Accompanist {
    public let score: ScoreData
    public let firstBar: Int
    public let lastBar: Int
    public var clock: BeatClock
    public let velocity: Int
    public let leadership: [Passage]?  // nil: the piano always leads

    let barStarts: [(Double, Int)]
    let barStart: [Int: Double]
    let startBeat: Double
    let endBeat: Double
    let pianoOnsets: [Double]
    let pianoPitches: [Double: Set<Int>]
    let notes: [StringNote]

    public private(set) var started = false  // the clock has been set at least once
    public private(set) var waiting = true  // strings silent, waiting for the pianist to (re)start
    public private(set) var finished = false
    var trailStart: Double? = nil  // when the strings began trailing off
    // current expression of parts being faded (Python dict: insertion order matters)
    var faded: [(part: String, level: Int)] = []
    var restartFrom: Double
    var restartBack = 0.0
    var lastStart: Double  // for "again"
    var lastBeat: Double? = nil  // score beat of the last piano chord matched
    var lastChordT = -1.0
    var lastNoteT = -1.0  // last note that went into a chord
    var pending: (first: Double, last: Double, pitches: Set<Int>)? = nil  // chord being collected
    var nextNote = 0  // index into notes of the next note to start
    var sounding: [(end: Double, note: StringNote)] = []
    var queued: [Out] = []  // produced by inputs, emitted by the next `due`
    var drift = 0
    var followerBeat: Double? = nil  // the follower's latest estimate
    var followerTrusted = false  // set once the follower agrees with us; cleared on every restart
    var probation = 0  // chords left that must fit after a restart
    public private(set) var log: [LogEntry] = []  // (time, matched beat, asynchrony) per piano chord
    public private(set) var notices: [Notice] = []  // state changes, for the live display
    var homeSpb: Double  // the strings' own tempo, where they lead
    var passage: Passage? = nil  // the leadership passage of the last chord matched

    public init(score: ScoreData, firstBar: Int = 1, lastBar: Int = 16,
                clock: BeatClock = BeatClock(spb: 60.0 / 63.0), velocity: Int = 80,
                leadership: [Passage]? = nil) {
        self.score = score; self.firstBar = firstBar; self.lastBar = lastBar
        self.clock = clock; self.velocity = velocity; self.leadership = leadership
        let starts = score.barStarts
        barStarts = starts
        var bs: [Int: Double] = [:]
        for (b, bar) in starts.reversed() { bs[bar] = b }  // first occurrence if repeats unfold
        barStart = bs
        startBeat = bs[firstBar]!
        endBeat = bs[lastBar + 1] ?? Double.infinity
        let lo = startBeat, hi = endBeat
        var piano: [Double: Set<Int>] = [:]
        for n in score.notes("Piano") where lo <= n.onsetBeat && n.onsetBeat < hi {
            piano[n.onsetBeat, default: []].insert(n.pitch)
        }
        pianoOnsets = piano.keys.sorted()
        pianoPitches = piano
        // sorted by (onset, part, pitch); Python's sort is stable, so keep the input index as last key
        var all: [(Int, StringNote)] = []
        for name in STRING_PARTS {
            for n in score.notes(name) where lo <= n.onsetBeat && n.onsetBeat < hi {
                all.append((all.count, StringNote(part: name, pitch: n.pitch, onsetBeat: n.onsetBeat,
                                                  durationBeat: n.durationBeat, velocity: velocity)))
            }
        }
        all.sort { a, b in
            if a.1.onsetBeat != b.1.onsetBeat { return a.1.onsetBeat < b.1.onsetBeat }
            if a.1.part != b.1.part { return a.1.part < b.1.part }
            if a.1.pitch != b.1.pitch { return a.1.pitch < b.1.pitch }
            return a.0 < b.0
        }
        notes = all.map { $0.1 }
        restartFrom = pianoOnsets[0]  // piano chord a restart is expected at...
        lastStart = startBeat
        homeSpb = clock.spb
    }

    // MARK: inputs

    public func onPianoOnset(_ t: Double, _ pitch: Int) {
        if finished { return }
        if let p = pending, t - p.last <= CHORD_GAP_S {
            pending!.pitches.insert(pitch)
            pending!.last = t
            lastNoteT = t
        } else {
            finishChord()
            if t - lastNoteT <= CHORD_GAP_S {
                lastNoteT = t
                return  // the rest of a chord already matched (it was complete enough to act on early)
            }
            if !restarting && lateChordNote(t, pitch) { return }
            pending = (t, t, [pitch])
            lastNoteT = t
        }
        if restarting {
            // Restart on the whole chord (it is the only evidence of where the pianist is), but don't wait
            // out the gap once a candidate chord is complete, or (four notes or more) all but one in and fitting.
            let got = pending!.pitches
            if restartCandidates().contains(where: { b in
                let ref = pianoPitches[b]!
                return ref.isSubset(of: got)
                    || (got.count >= 3 && got.count >= ref.count - 1 && sim(got, ref) >= RESTART_MIN_SIM)
            }) {
                finishChord()
            }
            return
        }
        // Don't wait out the gap once every note of the expected chord is in.
        if let nxt = nextOnset(), pianoPitches[nxt]!.isSubset(of: pending!.pitches) {
            finishChord()
        }
    }

    public func onFollower(_ t: Double, _ beat: Double) {
        followerBeat = beat
    }

    /// A voice cue. Returns (and posts as a notice) what the strings will do.
    @discardableResult
    public func onCue(_ t: Double, _ cue: Cue) -> String {
        let msg = cueMessage(t, cue)
        notices.append(Notice(t: t, text: msg))
        return msg
    }

    private func cueMessage(_ t: Double, _ cue: Cue) -> String {
        if !(waiting || trailing) {
            return "\"\(cue)\" ignored: only while stopped"
        }
        let target: Double
        if cue.kind == "again" {
            target = lastStart
        } else if cue.kind == "top" {
            target = startBeat
        } else if let b = barStart[cue.bar], startBeat <= b, b < endBeat {
            target = b - (cue.pickup ? 1.0 : 0.0)
        } else {
            return "\"\(cue)\" ignored: bars \(firstBar)-\(lastBar) only"
        }
        guard let first = pianoOnsets.first(where: { $0 >= target }) else {
            return "\"\(cue)\" ignored: no piano after that point"
        }
        stopSounding(t)
        waiting = true; trailStart = nil
        restartFrom = first; restartBack = CUE_BACK_BEATS
        lastStart = max(target, startBeat)
        lastBeat = nil
        let (bar, bib) = score.barAt(first)
        return "\(cue): strings come in with you at bar \(bar) beat \(fmtG(bib))"
    }

    // MARK: output

    var trailing: Bool { trailStart != nil }

    /// String events whose time has come, in time order.
    public func due(_ now: Double) -> [Out] {
        if let p = pending, now - p.last > CHORD_GAP_S {
            finishChord()
        }
        var out = queued
        queued = []
        if waiting || finished { return out }
        endNotes(now, &out)
        if trailing {
            trail(now, &out)
            return out
        }
        if pianistStopped(now) {
            trailStart = now
            // A restart may go back RESTART_BACK_BEATS from the last chord played.
            let nxt = nextOnset()!
            restartFrom = nxt
            restartBack = nxt - lastBeat! + RESTART_BACK_BEATS
            notices.append(Notice(t: now, text: "you stopped: strings trailing off"))
            trail(now, &out)
            return out
        }
        while nextNote < notes.count {
            let n = notes[nextNote]
            var tOn = clock.timeOf(n.onsetBeat) - (n.isGrace ? GRACE_S : 0.0)
            if let lb = lastBeat, n.onsetBeat <= lb {
                tOn = min(tOn, now)  // the pianist already played this beat, earlier than predicted: come in now
            }
            if tOn > now { break }
            out.append(Out(tOn, "on", n.part, pitch: n.pitch, velocity: n.velocity, beat: n.onsetBeat))
            sounding.append((n.onsetBeat + n.durationBeat, n))
            nextNote += 1
        }
        if nextNote >= notes.count && sounding.isEmpty {
            finished = true
        }
        // out.sort(key=time): stable
        out = out.enumerated().sorted { a, b in
            a.element.time != b.element.time ? a.element.time < b.element.time : a.offset < b.offset
        }.map { $0.element }
        return out
    }

    /// Stop everything (end of run).
    public func flush(_ now: Double) -> [Out] {
        let out = queued + sounding.map { Out(now, "off", $0.note.part, pitch: $0.note.pitch, beat: $0.note.onsetBeat) }
        queued = []; sounding = []
        finished = true
        return out
    }

    // MARK: internals

    private var restarting: Bool { waiting || trailing }

    private func endNotes(_ now: Double, _ out: inout [Out]) {
        var keep: [(end: Double, note: StringNote)] = []
        for s in sounding {
            let tOff = offTime(s.note)
            if tOff <= now {
                out.append(Out(tOff, "off", s.note.part, pitch: s.note.pitch, beat: s.end))
            } else {
                keep.append(s)
            }
        }
        sounding = keep
    }

    /// Let notes that end within the trail finish; fade the rest; then wait.
    private func trail(_ now: Double, _ out: inout [Out]) {
        let ts = trailStart!
        let elapsed = now - ts
        if elapsed >= TRAIL_S {
            stopSounding(now)
            out += queued
            queued = []
            waiting = true; trailStart = nil
            notices.append(Notice(t: now, text: "waiting: play on, or say where to start"))
            return
        }
        let level = Int(127 * (1 - elapsed / TRAIL_S))
        for s in sounding {
            let n = s.note
            let cur = faded.first(where: { $0.part == n.part })?.level ?? 127
            if offTime(n) > ts + TRAIL_S && cur - level >= FADE_STEP {
                if let i = faded.firstIndex(where: { $0.part == n.part }) {
                    faded[i].level = level
                } else {
                    faded.append((n.part, level))
                }
                out.append(Out(now, "expr", n.part, velocity: level))
            }
        }
    }

    /// Queue note-offs for everything sounding and reset any faded expression.
    private func stopSounding(_ now: Double) {
        queued += sounding.map { Out(now, "off", $0.note.part, pitch: $0.note.pitch, beat: $0.note.onsetBeat) }
        queued += faded.map { Out(now, "expr", $0.part, velocity: 127) }
        sounding = []; faded = []
    }

    private func finishChord() {
        guard let p = pending else { return }
        let t = p.first, pitches = p.pitches
        pending = nil
        if restarting {
            restart(t, pitches)
            return
        }
        let beat = match(t, pitches)
        if probation != 0, let beat = beat, pitches.count >= 2 {
            if sim(pitches, pianoPitches[beat]!) < PROBATION_MIN_SIM {
                lost(t)
                return
            }
            probation -= 1
        }
        guard let beat = beat else { return }  // stray notes that fit nothing nearby
        let asyn: Double
        if let lb = lastBeat, beat <= lb {
            clock.anchor(t, beat)  // stepping back to fix an earlier wrong match: phase only
            asyn = 0.0
        } else {
            asyn = correct(t, beat)
        }
        matched(t, beat, asyn)
        checkDrift(t)
    }

    private func restart(_ t: Double, _ pitches: Set<Int>) {
        guard let beat = restartBeat(pitches) else {
            if notices.isEmpty || !notices[notices.count - 1].text.hasPrefix("not sure") {
                notices.append(Notice(t: t, text: "not sure where you are: play on from where you stopped, or say where to start"))
            }
            return
        }
        stopSounding(t)  // anything still trailing belongs to where we were
        clock.anchor(t, beat)  // tempo carries over from before the stop
        started = true; waiting = false; trailStart = nil
        nextNote = notes.firstIndex(where: { $0.onsetBeat >= beat }) ?? notes.count
        followerTrusted = false
        drift = 0
        probation = PROBATION_CHORDS
        let (bar, bib) = score.barAt(beat)
        notices.append(Notice(t: t, text: "with you at bar \(bar) beat \(fmtG(bib))"))
        matched(t, beat, 0.0)
    }

    /// A restart turned out wrong: stop the strings and wait again, with the same restart window.
    private func lost(_ t: Double) {
        stopSounding(t)
        waiting = true; probation = 0
        notices.append(Notice(t: t, text: "lost you: play on from where you stopped, or say where to start"))
    }

    private func restartCandidates() -> [Double] {
        let nxt = restartFrom
        return pianoOnsets.filter { nxt - restartBack <= $0 && $0 <= nxt + 1.0 }
    }

    private func sim(_ a: Set<Int>, _ b: Set<Int>) -> Double {
        Double(a.intersection(b).count) / Double(a.union(b).count)
    }

    private func indexOfOnset(_ b: Double) -> Int { pianoOnsets.firstIndex(of: b)! }

    /// Where the pianist (re)starts: a chord whose pitches fit well, from `restartBack` beats before
    /// the expected chord to a beat after it, preferring the expected one. nil if nothing fits.
    private func restartBeat(_ pitches: Set<Int>) -> Double? {
        let iNext = indexOfOnset(restartFrom)
        let scored = restartCandidates().map { b in
            (sim(pitches, pianoPitches[b]!) - 0.05 * Double(abs(indexOfOnset(b) - iNext)), b)
        }
        // Python max() over (score, beat) tuples: highest score, then highest beat. (Python raises on an
        // empty list; the candidates always include restartFrom itself, so it is never empty.)
        var best = scored[0]
        for s in scored.dropFirst() where s.0 > best.0 || (s.0 == best.0 && s.1 > best.1) { best = s }
        return sim(pitches, pianoPitches[best.1]!) >= RESTART_MIN_SIM ? best.1 : nil
    }

    private func checkDrift(_ t: Double) {
        guard let f = followerBeat else { return }
        let close = abs(f - lastBeat!) <= DRIFT_BEATS
        if !followerTrusted {
            // After a restart the follower may still be elsewhere (it cannot jump back); ignore it until
            // it agrees with us again.
            followerTrusted = close
            return
        }
        if close {
            drift = 0
            return
        }
        drift += 1
        if drift >= DRIFT_COUNT {
            // Trust the follower: jump to the piano chord at or before its estimate, keeping the tempo.
            let beat = pianoOnsets.filter { $0 <= f }.max() ?? pianoOnsets[0]
            clock.anchor(t, beat)
            lastBeat = beat
            drift = 0
        }
    }

    func passageAt(_ beat: Double) -> Passage? {
        guard let leadership = leadership else { return nil }
        let i = max(0, upperBound(barStarts.map { $0.0 }, beat) - 1)
        let bar = barStarts[i].1
        return leadership.first(where: { $0.firstBar <= bar && bar <= $0.lastBar })
    }

    /// Correct the clock for a piano chord, coupled as the passage says (if there is an annotation).
    private func correct(_ t: Double, _ beat: Double) -> Double {
        guard let p = passageAt(beat) else {
            return clock.correct(t, beat, prevBeat: lastBeat)
        }
        let c = p.coupling
        let late = t > clock.timeOf(beat)
        let free = max(0.0, 1 - c / COUPLING["piano"]!)  // 0 where the piano leads, 1 at coupling 0
        let asyn = clock.correct(t, beat, prevBeat: lastBeat, alpha: min(1.0, c + WAIT_EXTRA * (late ? 1.0 : 0.0) * free),
                                 beta: BETA_PER_COUPLING * c, homeSpb: homeSpb, pull: HOME_PULL * free)
        if free == 0.0 {
            homeSpb = clock.spb  // the strings take over at the tempo the pianist set
        }
        return asyn
    }

    private func matched(_ t: Double, _ beat: Double, _ asyn: Double) {
        if let p = passageAt(beat), p != passage {
            passage = p
            let who = ["piano": "piano leads", "strings": "strings lead", "shared": "shared"][p.lead]!
            notices.append(Notice(t: t, text: "bars \(p.firstBar)-\(p.lastBar): \(who) (coupling \(fmtG(p.coupling)))"))
        }
        lastBeat = beat
        lastChordT = t
        log.append(LogEntry(t: t, beat: beat, asyn: asyn))
    }

    private func nextOnset() -> Double? {
        let lo = lastBeat ?? -Double.infinity
        return pianoOnsets.first(where: { $0 > lo })
    }

    /// Is this a note of the chord just played, arriving a little late (a rolled or uneven chord)?
    private func lateChordNote(_ t: Double, _ pitch: Int) -> Bool {
        let nxt = nextOnset()
        guard let lb = lastBeat, let nx = nxt, pianoPitches[lb]!.contains(pitch) else { return false }
        return t - lastChordT <= ROLLED_FRACTION * (nx - lb) * clock.spb
    }

    /// The score chord this piano chord most plausibly is, or nil for stray notes.
    private func match(_ t: Double, _ pitches: Set<Int>) -> Double? {
        let predicted = clock.beatAt(t)
        let last = lastBeat ?? -Double.infinity
        guard let nxt = nextOnset() else { return nil }
        // The next expected chord is always a candidate, however long the rest before it.
        let cands = pianoOnsets.filter { $0 == nxt || (last - 1.0 <= $0 && $0 <= last + 2.0) }  // already sorted, unique
        let iNext = indexOfOnset(nxt)

        func score(_ b: Double) -> Double {
            let ref = pianoPitches[b]!
            let s = Double(pitches.intersection(ref).count) / Double(pitches.union(ref).count)
            let steps = indexOfOnset(b) - iNext
            var penalty = SKIP_PENALTY * Double(max(0, steps)) + BACK_PENALTY * Double(max(0, -steps))
            // After a rest the clock may have run far from the pianist; time says little then.
            if b != nxt {
                penalty += TIME_PENALTY * abs(b - predicted)
            }
            return s - penalty
        }

        // max(key=): the first of equal maxima
        var best = cands[0], bestScore = score(best)
        for b in cands.dropFirst() {
            let s = score(b)
            if s > bestScore { best = b; bestScore = s }
        }
        return bestScore > MIN_SCORE ? best : nil
    }

    private func offTime(_ n: StringNote) -> Double {
        if n.isGrace {
            return clock.timeOf(n.onsetBeat)
        }
        let on = clock.timeOf(n.onsetBeat), end = clock.timeOf(n.onsetBeat + n.durationBeat)
        return on + max(0.05, (end - on) * LEGATO)
    }

    private func pianistStopped(_ now: Double) -> Bool {
        guard let expected = nextOnset() else { return false }
        return clock.beatAt(now) > expected + CARRY_BEATS
    }
}
