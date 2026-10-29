import Foundation
import RehearsalCore

// What stays put while pieces come and go: the audio session, the strings' samplers, the piano's MIDI and
// the microphone. The piece open on screen (a Session) borrows them; the rig sends it the piano's notes and
// the voice cues.

final class Rig: @unchecked Sendable {
    let strings: Strings
    let keyboard = Keyboard()
    let voice = Voice()

    private let lock = NSLock()
    private weak var session: Session?
    private var lastNote: (note: Int, velocity: Int)?

    /// REHEARSAL_SILENT=1 in the environment: no audio and no microphone (Simulator screenshots).
    static let silent = ProcessInfo.processInfo.environment["REHEARSAL_SILENT"] == "1"

    init() throws {
        if !Self.silent { try AudioSetup.configure() }
        strings = try Strings(silent: Self.silent)
        let d = UserDefaults.standard
        if d.object(forKey: Settings.volume) != nil { strings.volume = d.float(forKey: Settings.volume) }
        for part in STRING_PARTS { strings.setVolume(part, Settings.partVolume(part)) }
        keyboard.onEvent = { [weak self] t, e in
            guard let self else { return }
            let s: Session? = self.lock.withLock {
                if e.kind == .noteOn { self.lastNote = (e.note, e.value) }
                return self.session
            }
            s?.piano(t, e)
        }
        keyboard.onChange = { [weak self] name in
            self?.lock.withLock { self?.session }?.notice(name.map { "keyboard connected: \($0)" } ?? "no keyboard: plug in the piano (USB)")
        }
        voice.onCue = { [weak self] cue, words in self?.lock.withLock { self?.session }?.cue(cue, heard: words) }
    }

    func start() {
        keyboard.start()
        if !Self.silent { voice.start() }
    }

    func attach(_ s: Session?) { lock.withLock { session = s } }

    /// The last key pressed, for Settings' “Last note heard”.
    var lastKey: (note: Int, velocity: Int)? { lock.withLock { lastNote } }
}

/// Settings kept between launches.
enum Settings {
    static let volume = "stringsVolume"
    static let voice = "voiceCues"
    static let keepAwake = "keepAwake"

    static func partVolume(_ part: String) -> Float {
        let d = UserDefaults.standard
        return d.object(forKey: "volume." + part) == nil ? 1 : d.float(forKey: "volume." + part)
    }

    static func setPartVolume(_ part: String, _ v: Float) { UserDefaults.standard.set(v, forKey: "volume." + part) }

    static var voiceOn: Bool { UserDefaults.standard.object(forKey: voice) as? Bool ?? true }
    static var keepAwakeOn: Bool { UserDefaults.standard.object(forKey: keepAwake) as? Bool ?? true }
}
