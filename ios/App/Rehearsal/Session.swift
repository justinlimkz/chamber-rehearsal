import Foundation
import RehearsalCore

// Rehearsing one piece: piano notes in from the keyboard, the
// accompanist deciding what the strings play, the strings out through the samplers, voice cues from the
// microphone, and everything recorded to a MIDI file. The keyboard, samplers and microphone belong to the
// Rig and are lent to the session open on screen.
//
// One `Accompanist` under a lock: the MIDI thread feeds piano onsets, the screen (taps, "Again", "From the
// top") and the voice feed cues, and a loop thread asks `due()` every millisecond and plays what comes
// out. The microphone is only listened to while the strings wait. The recording is saved whenever the
// strings stop, when the app goes to the background (coming back starts a new one) and when the piece is
// closed. Times are seconds since the session started, on the host clock (MIDI timestamps use the same clock).

final class Session: @unchecked Sendable {
    let piece: Piece
    let score: ScoreData
    let firstBar: Int, lastBar: Int
    let rig: Rig

    private let lock = NSLock()
    private var acc: Accompanist
    private var view = ViewState()
    private var extraNotices: [Notice] = []  // the session's own (keyboard), shown with the accompanist's
    private var recording: Recording
    private var heard: String?  // the words of the last voice cue
    private var passes: [Pass] = []  // this recording's passes, for the last-pass line and the recordings list
    private var furthest: Double?  // the furthest beat played with the strings, for the bar picker
    private let saving = DispatchQueue(label: "saving", qos: .utility)
    private let t0 = HostTime.now()
    private var running = true

    /// `tempo`: the strings' first notes, in quarter notes a minute; the piece's own (its score.json) unless given,
    /// else 63.
    init(piece: Piece, rig: Rig, tempo: Double? = nil) throws {
        self.piece = piece
        self.rig = rig
        score = try ScoreData.load(path: piece.url.appendingPathComponent("score.json").path)
        firstBar = score.barStarts.map { $0.1 }.min()!
        lastBar = score.barStarts.map { $0.1 }.max()!
        acc = Accompanist(score: score, firstBar: firstBar, lastBar: lastBar, clock: BeatClock(spb: 60 / min(160, max(30, tempo ?? score.tempo ?? 63))))
        recording = Recording(start: 0, piece: piece.id)
    }

    func start() {
        rig.attach(self)
        let thread = Thread { [weak self] in self?.run() }
        thread.qualityOfService = .userInteractive
        thread.name = "accompanist"
        thread.start()
    }

    /// Leave the piece: the strings stop, the recording is saved, the rig is free for the next piece.
    func close() {
        let outs: [Out] = lock.withLock {
            running = false
            let outs = acc.flush(now())
            for o in outs { recording.played(now(), o) }
            return outs
        }
        for o in outs { rig.strings.play(o) }
        rig.attach(nil)
        rig.voice.listen(false)
        save()
    }

    func now() -> Double { HostTime.now() - t0 }

    func piano(_ hostTime: Double, _ e: PianoEvent) {
        let t = hostTime - t0
        lock.withLock {
            recording.piano(t, e)
            if e.kind == .noteOn { acc.onPianoOnset(t, e.note) }
        }
    }

    func notice(_ text: String) {
        let n = Notice(t: now(), text: text)
        lock.withLock { extraNotices.append(n); recording.notice(n) }
    }

    /// A cue from the screen: "again", "top", "pause", "resume", or a bar number ("p9": from the pickup to bar 9).
    func cue(_ text: String) {
        switch text {
        case "again", "top", "pause", "resume": cue(Cue(kind: text))
        default:
            if let bar = Int(text) { cue(Cue(kind: "bar", bar: bar)) }
            else if text.hasPrefix("p"), let bar = Int(text.dropFirst()) { cue(Cue(kind: "bar", bar: bar, pickup: true)) }
        }
    }

    func cue(_ cue: Cue, heard words: String? = nil) {
        let t = now()
        lock.withLock {
            if acc.finished {  // played to the end: a fresh run, with the tempo it ended in
                acc = Accompanist(score: score, firstBar: firstBar, lastBar: lastBar, clock: BeatClock(spb: acc.clock.spb))
            }
            view.lastCue = acc.onCue(t, cue)
            heard = words
            if let words { recording.notice(Notice(t: t, text: "heard \"\(words)\"")) }
        }
    }

    /// The strings' tempo for their first notes after a restart (they follow you after that).
    func setTempo(_ bpm: Double) {
        lock.withLock { if acc.waiting { acc.clock.spb = 60 / bpm } }
    }

    /// Save the recording (if anything was played) and, with `renew`, start a new one.
    func save(renew: Bool = false) {
        let (rec, summary): (Recording?, Data) = lock.withLock {
            let rec = recording
            let summary = (try? JSONSerialization.data(withJSONObject: summaryJSON())) ?? Data()
            if renew && !rec.isEmpty { recording = Recording(start: now(), piece: piece.id); passes = [] }
            return (rec.isEmpty ? nil : rec, summary)
        }
        guard let rec else { return }
        saving.async { _ = try? rec.save(summary: summary) }
    }

    private func summaryJSON() -> [String: Any] {
        ["piece": piece.id, "title": piece.title, "passes": passes.map { $0.json(score) }]
    }

    private func run() {
        var shownLog = 0, shownNotices = 0, wasWaiting = false  // so the first pass sets the microphone listening
        while lock.withLock({ running }) {
            let now = self.now()
            let (outs, waiting): ([Out], Bool) = lock.withLock {
                let outs = acc.due(now)
                if acc.log.count < shownLog || acc.notices.count < shownNotices { shownLog = 0; shownNotices = 0 }  // a fresh accompanist
                let new = acc.log[shownLog...]
                view.update(new)
                for e in new { track(e) }
                shownLog = acc.log.count
                for n in acc.notices[shownNotices...] { recording.notice(n) }
                shownNotices = acc.notices.count
                for o in outs { recording.played(now, o) }
                return (outs, acc.waiting || acc.finished)
            }
            for o in outs { rig.strings.play(o) }
            if waiting != wasWaiting {
                wasWaiting = waiting
                rig.voice.listen(waiting && Settings.voiceOn)
                if waiting { save() }  // the strings stopped: keep what was played so far
            }
            Thread.sleep(forTimeInterval: 0.001)
        }
    }

    /// A piano chord the accompanist matched: a restart (logged with asynchrony 0) begins a pass.
    private func track(_ e: LogEntry) {
        let bpm = 60 / acc.clock.spb
        if e.asyn == 0.0 || passes.isEmpty {
            passes.append(Pass(firstBeat: e.beat, lastBeat: e.beat, startBpm: bpm, endBpm: bpm))
        } else {
            passes[passes.count - 1].add(e, bpm: bpm)
        }
        furthest = max(furthest ?? e.beat, e.beat)
    }

    /// What the score page (`score.html`) and the strip show, as JSON, with the last pass.
    func state() -> [String: Any] {
        let now = self.now()
        return lock.withLock {
            let state = acc.finished ? "finished" : acc.trailing ? "trailing" : acc.paused ? "paused"
                : acc.waiting ? (acc.started ? "waiting" : "ready") : "playing"
            let restart: Double? = ["waiting", "ready", "paused"].contains(state) ? acc.restartFrom : nil
            let notices = (acc.notices + extraNotices).sorted { $0.t < $1.t }.suffix(5).map { [$0.t, $0.text] as [Any] }
            return [
                "state": state, "keyboard": rig.keyboard.name as Any, "bpm": 60 / acc.clock.spb,
                "mic": rig.voice.state, "heard": restart != nil ? heard as Any : NSNull(),
                "notices": notices, "lastCue": restart != nil ? view.lastCue as Any : NSNull(),
                "restartBeat": restart as Any, "lastBeat": acc.lastBeat as Any, "endBeat": min(acc.endBeat, 1e9),
                "clock": ["t0": acc.clock.t0, "b0": acc.clock.b0, "spb": acc.clock.spb], "now": now,
                "lastPass": passes.last.map { $0.json(score) } as Any, "furthestBeat": furthest as Any,
            ]
        }
    }

    /// The bars of the session in playing order, for the page (`/score`; app.score_layout without the notes).
    func layout() -> [String: Any] {
        let starts = acc.barStarts.filter { acc.startBeat <= $0.0 && $0.0 < acc.endBeat }
        var bars: [[String: Any]] = []
        for (i, (start, n)) in starts.enumerated() {
            let end = i + 1 < starts.count ? starts[i + 1].0 : min(acc.endBeat, start + 3)
            bars.append(["n": n, "start": start, "beats": end - start])
        }
        return ["title": "\(piece.composerSurname) · \(piece.title)", "bars": bars, "firstBar": firstBar, "lastBar": lastBar]
    }
}

/// One pass: from a restart to where the strings stopped. "Together" counts chords where the strings were
/// within 60 ms of you.
struct Pass {
    var firstBeat: Double, lastBeat: Double
    var startBpm: Double, endBpm: Double
    var chords = 0, together = 0

    static let togetherS = 0.06

    mutating func add(_ e: LogEntry, bpm: Double) {
        lastBeat = max(lastBeat, e.beat)
        endBpm = bpm
        chords += 1
        if abs(e.asyn) <= Self.togetherS { together += 1 }
    }

    func json(_ score: ScoreData) -> [String: Any] {
        ["firstBar": score.barAt(firstBeat).bar, "lastBar": score.barAt(lastBeat).bar,
         "startBpm": startBpm, "endBpm": endBpm, "chords": chords, "together": together]
    }
}

/// live.ViewState, the part the score page needs: what the last cue will do, until the strings come in.
struct ViewState {
    var lastCue: String?

    mutating func update(_ newLog: ArraySlice<LogEntry>) {
        for e in newLog where e.asyn == 0.0 {  // a restart (logged with 0): the cue has been acted on
            lastCue = nil
        }
    }
}

enum HostTime {
    private static let scale: Double = {
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        return Double(info.numer) / Double(info.denom) / 1e9
    }()

    static func now() -> Double { seconds(mach_absolute_time()) }
    static func seconds(_ t: UInt64) -> Double { Double(t) * scale }
}
