import AVFoundation
import RehearsalCore

// The strings' sound (rehearsal/sound.py on the iPad): one sampler per part, playing the violin, viola
// and cello of MuseScore's SoundFont (cut down to `strings.sf2` by rehearsal/sf2strings.py), through a
// small audio buffer so they answer the piano quickly.

final class Strings: @unchecked Sendable {
    static let programs = ["Violin": 40, "Viola": 41, "Violoncello": 42]  // General MIDI
    static let bufferS = 0.005

    private let engine = AVAudioEngine()
    private var samplers: [String: AVAudioUnitSampler] = [:]

    init() throws {
        let audio = AVAudioSession.sharedInstance()
        try audio.setCategory(.playback, mode: .default)
        try audio.setPreferredIOBufferDuration(Self.bufferS)
        try audio.setActive(true)
        guard let font = Bundle.main.url(forResource: "strings", withExtension: "sf2") else {
            throw NSError(domain: "Rehearsal", code: 1, userInfo: [NSLocalizedDescriptionKey: "strings.sf2 missing from the app"])
        }
        for (part, program) in Self.programs {
            let s = AVAudioUnitSampler()
            engine.attach(s)
            engine.connect(s, to: engine.mainMixerNode, format: nil)
            try s.loadSoundBankInstrument(at: font, program: UInt8(program),
                                          bankMSB: UInt8(kAUSampler_DefaultMelodicBankMSB), bankLSB: 0)
            samplers[part] = s
        }
        engine.mainMixerNode.outputVolume = 0.6
        try engine.start()
    }

    func play(_ o: Out) {
        guard let s = samplers[o.part] else { return }
        switch o.kind {
        case "on": s.startNote(UInt8(o.pitch), withVelocity: UInt8(o.velocity), onChannel: 0)
        case "off": s.stopNote(UInt8(o.pitch), onChannel: 0)
        default: s.sendController(11, withValue: UInt8(o.velocity), onChannel: 0)  // expression
        }
    }

    /// After an interruption (a call, Siri) or a route change, start the engine again.
    func resume() {
        if !engine.isRunning { try? engine.start() }
    }
}
