import Foundation
import RehearsalCore
import SwiftUI

// The rehearse screen's view of the session, read ten times a second: what the status strip, the controls
// and the last-pass line show. (The score page gets the same state through ScoreView.)

@MainActor @Observable
final class Live {
    let session: Session
    private(set) var state = "ready"  // ready | playing | trailing | waiting | paused | finished
    private(set) var keyboard: String?
    private(set) var listening = false
    private(set) var heard: String?
    private(set) var lastCue: String?
    private(set) var bpm = 63.0
    private(set) var bar: Int?  // where the strings are (playing), or where they'll restart (stopped)
    private(set) var beat = 0  // beat in the bar while playing, 1-based
    private(set) var beatsInBar = 4
    private(set) var lastPass: PassSummary?
    private(set) var furthestBar: Int?
    @ObservationIgnored private var timer: Timer?
    @ObservationIgnored private var bars: [(start: Double, n: Int)] = []

    init(session: Session) {
        self.session = session
        bars = session.score.barStarts.map { ($0.0, $0.1) }
        poll()
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.poll() }
        }
    }

    func stop() { timer?.invalidate() }

    /// The controls are out while the strings aren't playing with you.
    var stopped: Bool { state != "playing" && state != "trailing" }
    var restarting: Bool { lastCue != nil }

    private func poll() {
        let s = session.state()
        state = s["state"] as? String ?? "ready"
        keyboard = s["keyboard"] as? String
        listening = s["mic"] as? String == "listening"
        heard = s["heard"] as? String
        lastCue = s["lastCue"] as? String
        bpm = s["bpm"] as? Double ?? bpm
        if let p = s["lastPass"] as? [String: Any] { lastPass = PassSummary(p) }
        furthestBar = (s["furthestBeat"] as? Double).map { session.score.barAt($0).bar }
        if !stopped, let c = s["clock"] as? [String: Double], let now = s["now"] as? Double,
           let t0 = c["t0"], let b0 = c["b0"], let spb = c["spb"], let end = s["endBeat"] as? Double {
            let b = min(b0 + (now - t0) / spb, end - 1e-3)
            let i = max(0, (bars.lastIndex { $0.start <= b + 1e-9 }) ?? 0)
            bar = bars[i].n
            beat = Int(b - bars[i].start) + 1
            beatsInBar = i + 1 < bars.count ? Int((bars[i + 1].start - bars[i].start).rounded()) : 4
        } else if let r = s["restartBeat"] as? Double {
            bar = session.score.barAt(r).bar
        } else if let l = s["lastBeat"] as? Double {
            bar = session.score.barAt(l).bar
        }
    }
}

/// The last pass in one line: bars, how the tempo moved, how often the strings were with you.
struct PassSummary: Equatable {
    var firstBar: Int, lastBar: Int, startBpm: Double, endBpm: Double, chords: Int, together: Int

    init?(_ d: [String: Any]) {
        guard let f = d["firstBar"] as? Int, let l = d["lastBar"] as? Int, let a = d["startBpm"] as? Double,
              let b = d["endBpm"] as? Double, let c = d["chords"] as? Int, let t = d["together"] as? Int else { return nil }
        firstBar = f; lastBar = l; startBpm = a; endBpm = b; chords = c; together = t
    }

    var bars: String { firstBar == lastBar ? "bar \(firstBar)" : "bars \(firstBar)–\(lastBar)" }
    var tempo: String {
        let a = Int(startBpm.rounded()), b = Int(endBpm.rounded())
        return a == b ? "♩ \(a)" : "♩ \(a) → \(b)"
    }
    var togetherText: String? { chords >= 4 ? "together \(Int((100 * Double(together) / Double(chords)).rounded()))%" : nil }
    var line: String { ([bars, tempo] + [togetherText].compactMap { $0 }).joined(separator: " · ") }
}

/// The page's colours (score.html), so the strip and the score read as one.
enum Paper {
    static let color = Color(red: 0xFC / 255, green: 0xFB / 255, blue: 0xF8 / 255)
    static let ink = Color(red: 0x1B / 255, green: 0x1F / 255, blue: 0x24 / 255)
    static let muted = Color(red: 0x6B / 255, green: 0x73 / 255, blue: 0x80 / 255)
    static let line = Color(red: 0xE2 / 255, green: 0xE0 / 255, blue: 0xDA / 255)
    static let blue = Color(red: 0x2F / 255, green: 0x6F / 255, blue: 0xB3 / 255)
    static let amber = Color(red: 0xB8 / 255, green: 0x74 / 255, blue: 0x11 / 255)
    static let warn = Color(red: 0xA2 / 255, green: 0x3B / 255, blue: 0x1E / 255)
}
