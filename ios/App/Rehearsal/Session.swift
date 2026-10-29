import Foundation
import RehearsalCore

// The live session (rehearsal/live.py, without the follower): piano notes in from the keyboard, the
// accompanist deciding what the strings play, the strings out through the samplers, voice cues from the
// microphone, and everything recorded to a MIDI file.
//
// One `Accompanist` under a lock: the MIDI thread feeds piano onsets, the page (taps, "Again", "From the
// top") and the voice feed cues, and a loop thread asks `due()` every millisecond and plays what comes
// out. The microphone is only listened to while the strings wait. The recording is saved whenever the
// strings stop and when the app goes to the background; coming back starts a new one.
// Times are seconds since the session started, on the host clock (MIDI timestamps use the same clock).

final class Session: @unchecked Sendable {
    let score: ScoreData
    let firstBar: Int, lastBar: Int
    let tempo = 63.0
    let strings: Strings
    let keyboard: Keyboard
    let voice = Voice()

    private let lock = NSLock()
    private var acc: Accompanist
    private var view = ViewState()
    private var extraNotices: [Notice] = []  // the session's own (keyboard), shown with the accompanist's
    private var recording: Recording
    private var heard: String?  // the words of the last voice cue
    private let saving = DispatchQueue(label: "saving", qos: .utility)
    private let t0 = HostTime.now()
    private var loop: Thread?

    init(score: ScoreData, firstBar: Int = 1, lastBar: Int? = nil) throws {
        self.score = score
        self.firstBar = firstBar
        self.lastBar = lastBar ?? score.barStarts.map { $0.1 }.max()!
        acc = Accompanist(score: score, firstBar: firstBar, lastBar: self.lastBar, clock: BeatClock(spb: 60 / tempo))
        recording = Recording(start: 0)
        try AudioSetup.configure()
        strings = try Strings()
        keyboard = Keyboard()
        keyboard.onEvent = { [weak self] hostTime, e in self?.piano(hostTime, e) }
        voice.onCue = { [weak self] cue, words in self?.cue(cue, heard: words) }
        keyboard.onChange = { [weak self] name in
            self?.notice(name.map { "keyboard connected: \($0)" } ?? "no keyboard: plug in the piano (USB)")
        }
    }

    func start() {
        keyboard.start()
        voice.start()
        let thread = Thread { [weak self] in self?.run() }
        thread.qualityOfService = .userInteractive
        thread.name = "accompanist"
        loop = thread
        thread.start()
    }

    func now() -> Double { HostTime.now() - t0 }

    private func piano(_ hostTime: Double, _ e: PianoEvent) {
        let t = hostTime - t0
        lock.withLock {
            recording.piano(t, e)
            if e.kind == .noteOn { acc.onPianoOnset(t, e.note) }
        }
    }

    private func notice(_ text: String) {
        let n = Notice(t: now(), text: text)
        lock.withLock { extraNotices.append(n); recording.notice(n) }
    }

    /// A cue from the page: "again", "top", "pause", "resume", or a bar number.
    func cue(_ text: String) {
        switch text {
        case "again", "top", "pause", "resume": cue(Cue(kind: text))
        default: if let bar = Int(text) { cue(Cue(kind: "bar", bar: bar)) }
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

    /// Save the recording (if anything was played) and, with `renew`, start a new one.
    func save(renew: Bool = false) {
        let rec: Recording? = lock.withLock {
            let rec = recording
            if renew && !rec.isEmpty { recording = Recording(start: now()) }
            return rec.isEmpty ? nil : rec
        }
        guard let rec else { return }
        saving.async { _ = try? rec.save() }
    }

    private func run() {
        var shownLog = 0, shownNotices = 0, wasWaiting = false  // so the first pass sets the microphone listening
        while true {
            let now = self.now()
            let (outs, waiting): ([Out], Bool) = lock.withLock {
                let outs = acc.due(now)
                if acc.log.count < shownLog || acc.notices.count < shownNotices { shownLog = 0; shownNotices = 0 }  // a fresh accompanist
                view.update(acc.log[shownLog...])
                shownLog = acc.log.count
                for n in acc.notices[shownNotices...] { recording.notice(n) }
                shownNotices = acc.notices.count
                for o in outs { recording.played(now, o) }
                return (outs, acc.waiting || acc.finished)
            }
            for o in outs { strings.play(o) }
            if waiting != wasWaiting {
                wasWaiting = waiting
                voice.listen(waiting)
                if waiting { save() }  // the strings stopped: keep what was played so far
            }
            Thread.sleep(forTimeInterval: 0.001)
        }
    }

    /// What the score page shows (`ipad.html`), as JSON: the same fields the Mac's server sends.
    func state() -> [String: Any] {
        let now = self.now()
        return lock.withLock {
            let state = acc.finished ? "finished" : acc.trailing ? "trailing" : acc.paused ? "paused"
                : acc.waiting ? (acc.started ? "waiting" : "ready") : "playing"
            let restart: Double? = ["waiting", "ready", "paused"].contains(state) ? acc.restartFrom : nil
            let notices = (acc.notices + extraNotices).sorted { $0.t < $1.t }.suffix(5).map { [$0.t, $0.text] as [Any] }
            return [
                "state": state, "keyboard": keyboard.name as Any, "bpm": 60 / acc.clock.spb,
                "mic": voice.state, "heard": restart != nil ? heard as Any : NSNull(),
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
