import Foundation
import RehearsalCore

// The live session (rehearsal/live.py, without the follower): piano notes in from the keyboard, the
// accompanist deciding what the strings play, the strings out through the samplers.
//
// One `Accompanist` under a lock: the MIDI thread feeds piano onsets, the page feeds cues (taps,
// "Again", "From the top"), and a loop thread asks `due()` every millisecond and plays what comes out.
// Times are seconds since the session started, on the host clock (MIDI timestamps use the same clock).

final class Session: @unchecked Sendable {
    let score: ScoreData
    let firstBar: Int, lastBar: Int
    let tempo = 63.0
    let strings: Strings
    let keyboard: Keyboard

    private let lock = NSLock()
    private var acc: Accompanist
    private var view = ViewState()
    private var extraNotices: [Notice] = []  // the session's own (keyboard), shown with the accompanist's
    private let t0 = HostTime.now()
    private var loop: Thread?

    init(score: ScoreData, firstBar: Int = 1, lastBar: Int? = nil) throws {
        self.score = score
        self.firstBar = firstBar
        self.lastBar = lastBar ?? score.barStarts.map { $0.1 }.max()!
        acc = Accompanist(score: score, firstBar: firstBar, lastBar: self.lastBar, clock: BeatClock(spb: 60 / tempo))
        strings = try Strings()
        keyboard = Keyboard()
        keyboard.onNote = { [weak self] hostTime, pitch in self?.pianoOnset(hostTime, pitch) }
        keyboard.onChange = { [weak self] name in
            self?.notice(name.map { "keyboard connected: \($0)" } ?? "no keyboard: plug in the piano (USB)")
        }
    }

    func start() {
        keyboard.start()
        let thread = Thread { [weak self] in self?.run() }
        thread.qualityOfService = .userInteractive
        thread.name = "accompanist"
        loop = thread
        thread.start()
    }

    func now() -> Double { HostTime.now() - t0 }

    private func pianoOnset(_ hostTime: Double, _ pitch: Int) {
        lock.withLock { acc.onPianoOnset(hostTime - t0, pitch) }
    }

    private func notice(_ text: String) {
        let t = now()
        lock.withLock { extraNotices.append(Notice(t: t, text: text)) }
    }

    /// A cue from the page: "again", "top", or a bar number.
    func cue(_ text: String) {
        let cue: Cue
        switch text {
        case "again": cue = Cue(kind: "again")
        case "top": cue = Cue(kind: "top")
        default:
            guard let bar = Int(text) else { return }
            cue = Cue(kind: "bar", bar: bar)
        }
        let t = now()
        lock.withLock {
            if acc.finished {  // played to the end: a fresh run, with the tempo it ended in
                acc = Accompanist(score: score, firstBar: firstBar, lastBar: lastBar, clock: BeatClock(spb: acc.clock.spb))
            }
            view.lastCue = acc.onCue(t, cue)
        }
    }

    private func run() {
        var shownLog = 0
        while true {
            let now = self.now()
            let outs: [Out] = lock.withLock {
                let outs = acc.due(now)
                if acc.log.count < shownLog { shownLog = 0 }  // a fresh accompanist
                view.update(acc.log[shownLog...])
                shownLog = acc.log.count
                return outs
            }
            for o in outs { strings.play(o) }
            Thread.sleep(forTimeInterval: 0.001)
        }
    }

    /// What the score page shows (`ipad.html`), as JSON: the same fields the Mac's server sends.
    func state() -> [String: Any] {
        let now = self.now()
        return lock.withLock {
            let state = acc.finished ? "finished" : acc.trailing ? "trailing" : acc.waiting ? (acc.started ? "waiting" : "ready") : "playing"
            let restart: Double? = state == "waiting" || state == "ready" ? acc.restartFrom : nil
            let notices = (acc.notices + extraNotices).sorted { $0.t < $1.t }.suffix(5).map { [$0.t, $0.text] as [Any] }
            return [
                "state": state, "keyboard": keyboard.name as Any, "bpm": 60 / acc.clock.spb,
                "notices": notices, "lastCue": restart != nil ? view.lastCue as Any : NSNull(),
                "restartBeat": restart as Any, "lastBeat": acc.lastBeat as Any, "endBeat": min(acc.endBeat, 1e9),
                "clock": ["t0": acc.clock.t0, "b0": acc.clock.b0, "spb": acc.clock.spb], "now": now,
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
        let composer = score.composer.split(separator: " ").last.map(String.init)
        return ["title": composer.map { "\($0) · \(score.title)" } ?? score.title, "bars": bars,
                "firstBar": firstBar, "lastBar": lastBar]
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
