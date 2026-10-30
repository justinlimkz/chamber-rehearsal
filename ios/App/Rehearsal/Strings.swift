import AVFoundation
import RehearsalCore

// The strings' sound (rehearsal/sound.py on the iPad): the violin, viola and cello of MuseScore's SoundFont
// (cut down to `strings.sf2` by rehearsal/sf2strings.py), through a small audio buffer (AudioSetup) so they
// answer the piano quickly, and a little reverb so they sound in a room rather than in your ear.
//
// Legato: every sample begins with a bow attack, and replaying it on each note of a slurred line makes the notes
// sound separate. So each part has two samplers: one with the full attack, for notes after a rest, and one with
// the font's legato copies (bank 1: they start past the attack and fade in), for notes that join on to the one
// before. Note-offs also wait a little, so the next note starts under the old one instead of after a gap; but
// only until it does: a player's old note ends as the new one begins, and holding it on past that (2026-10-30)
// made a semitone clash and then a dip at every change of chord.

final class Strings: @unchecked Sendable {
    static let programs = ["Violin": 40, "Viola": 41, "Violoncello": 42]  // General MIDI

    /// How the strings are played; the defaults are what the app uses (rendered offline in A/B tests).
    struct Style {
        var legato = true  // joined-on notes use the legato presets
        var joinS = 0.25  // a note starting this soon after its part's last note ended joins on to it
        var chordS = 0.03  // notes starting this close together are one chord, not one after the other
        // The engine stops notes at 0.95 of their length (LEGATO), a 25-200 ms gap here: hold each note through
        // that gap and this much beyond it, so the next note starts under the old one.
        var overlapS = 0.05
        var handoverS: Double? = 0.02  // a held-over note stops this long after the part's next note starts (nil: not early)
        var fillGap = true
        var reverbMix: Float = 22  // percent wet
        var reverb = AVAudioUnitReverbPreset.mediumHall
        // Player-side expression (off in the app until a listening test picks it; tone/ renders it). The engine
        // sends one velocity and no dynamics, and a looped sample sounds the same for as long as it is held.
        var swell = false  // CC11: a note held past ~0.4 s grows by about 2 dB, then settles back after 3 s
        var vibrato: ClosedRange<Int>? = nil  // CC1 per bowed (not joined-on) note, random in this range: the depth
                                              // of the font's vibrato LFO (fonts from tone/ set its rate and delay)
        var vibratoParts: Set<String>? = nil  // nil: every part (recordings with their own vibrato need none)
        // The players in a room (compared with a real recording, 2026-10-29: ours was nearly mono, brighter, and the
        // same colour at every dynamic). Each part has its own place left to right; its treble opens up as it
        // plays louder (a high shelf, `darkDB` down at the softest expression, flat at the loudest); and the viola
        // and cello come in a few milliseconds after the violin, as players never quite coincide.
        var seating: [String: Float] = ["Violin": -0.7, "Viola": 0.2, "Violoncello": 0.65]  // pan, -1 left ... 1 right
        var darkDB: Float = -6  // 0: no shelf
        var shelfHz: Float = 2000
        var lag: [String: Double] = ["Viola": 0.012, "Violoncello": 0.006]  // seconds
    }

    private struct Note { let sampler: AVAudioUnitSampler; let start: Double; let id: Int; var over = false }  // over: held past its note-off
    private final class Part {
        let attack: AVAudioUnitSampler, legato: AVAudioUnitSampler
        var sounding: [Int: Note] = [:]  // by pitch, including notes held past their engine note-off
        var lastOff = -Double.infinity  // engine time of the part's last note-off
        var expr = 127  // the engine's expression (it fades parts out when the pianist stops)
        var shelf: AVAudioUnitEQ?
        var held = 0.0, level = 0.88  // swell: seconds since the last note-on, and the swell's current gain
        var sent = 127  // CC11 last sent
        init(_ attack: AVAudioUnitSampler, _ legato: AVAudioUnitSampler) { self.attack = attack; self.legato = legato }
    }

    private let engine: AVAudioEngine
    private let style: Style
    private let after: (Double, @escaping () -> Void) -> Void  // run later (delayed note-offs)
    private var parts: [String: Part] = [:]
    private var mixers: [String: AVAudioMixerNode] = [:]  // one per part: its volume (and mute) in the mix
    private var nextID = 0
    private let lock = NSLock()
    private var ticking = false
    private var rng = SystemRandomNumberGenerator()

    /// `silent`: build the samplers but don't start the audio (screenshots in the Simulator, whose audio can hang).
    convenience init(silent: Bool = false) throws {
        guard let font = Bundle.main.url(forResource: "strings", withExtension: "sf2") else {
            throw NSError(domain: "Rehearsal", code: 1, userInfo: [NSLocalizedDescriptionKey: "strings.sf2 missing from the app"])
        }
        let offs = DispatchQueue(label: "strings.offs", qos: .userInteractive)
        try self.init(engine: AVAudioEngine(), font: font) { offs.asyncAfter(deadline: .now() + $0, execute: $1) }
        if !silent { try engine.start() }
    }

    /// The sound graph on `engine`, not started (the offline renderer drives it in manual rendering mode).
    init(engine: AVAudioEngine, font: URL, style: Style = Style(), after: @escaping (Double, @escaping () -> Void) -> Void) throws {
        self.engine = engine; self.style = style; self.after = after
        let bus = AVAudioMixerNode(), reverb = AVAudioUnitReverb()
        reverb.loadFactoryPreset(style.reverb)
        reverb.wetDryMix = style.reverbMix
        engine.attach(bus); engine.attach(reverb)
        engine.connect(bus, to: reverb, format: nil)
        engine.connect(reverb, to: engine.mainMixerNode, format: nil)
        for (part, program) in Self.programs {
            let mix = AVAudioMixerNode()
            engine.attach(mix)
            var shelf: AVAudioUnitEQ? = nil
            if style.darkDB != 0 {  // samplers -> part mixer -> shelf -> bus
                let eq = AVAudioUnitEQ(numberOfBands: 1)
                eq.bands[0].filterType = .highShelf
                eq.bands[0].frequency = style.shelfHz
                eq.bands[0].bypass = false
                eq.bands[0].gain = style.darkDB
                engine.attach(eq)
                engine.connect(mix, to: eq, format: nil)
                engine.connect(eq, to: bus, format: nil)
                shelf = eq
            } else {
                engine.connect(mix, to: bus, format: nil)
            }
            mixers[part] = mix
            let pair = try [0, 1].map { bank in  // bank 1: the legato copies
                let s = AVAudioUnitSampler()
                engine.attach(s)
                engine.connect(s, to: mix, format: nil)
                s.pan = style.seating[part] ?? 0
                try s.loadSoundBankInstrument(at: font, program: UInt8(program),
                                              bankMSB: UInt8(kAUSampler_DefaultMelodicBankMSB), bankLSB: UInt8(bank))
                return s
            }
            parts[part] = Part(pair[0], pair[1])
            parts[part]!.shelf = shelf
        }
        engine.mainMixerNode.outputVolume = Self.fullVolume
    }

    static let fullVolume: Float = 0.6  // the strings at "volume 1": balanced against the FP-30 at a normal level

    /// All three parts together, 0...1 (Settings).
    var volume: Float {
        get { engine.mainMixerNode.outputVolume / Self.fullVolume }
        set { engine.mainMixerNode.outputVolume = Self.fullVolume * max(0, min(1, newValue)) }
    }

    /// One part's level in the mix, 0...1; 0 mutes it (the strings popover).
    func setVolume(_ part: String, _ v: Float) { mixers[part]?.outputVolume = max(0, min(1, v)) }

    func play(_ o: Out) {
        if let lag = style.lag[o.part], lag > 0, o.kind != "expr" {
            after(lag) { [self] in playNow(o) }
        } else {
            playNow(o)
        }
    }

    private func playNow(_ o: Out) {
        lock.lock(); defer { lock.unlock() }
        guard let p = parts[o.part] else { return }
        switch o.kind {
        case "on":
            if let old = p.sounding.removeValue(forKey: o.pitch) {  // re-struck while held over: let it go now
                old.sampler.stopNote(UInt8(o.pitch), onChannel: 0)
            }
            let joins = o.time - p.lastOff < style.joinS || p.sounding.values.contains { o.time - $0.start > style.chordS }
            let s = style.legato && joins ? p.legato : p.attack
            if let h = style.handoverS {  // the part's held-over notes end as this one begins
                for (pitch, n) in p.sounding where n.over {
                    let stop = { guard p.sounding[pitch]?.id == n.id else { return }; p.sounding[pitch] = nil
                                 n.sampler.stopNote(UInt8(pitch), onChannel: 0) }
                    if h <= 0 { stop() } else { after(h) { [self] in lock.lock(); defer { lock.unlock() }; stop() } }
                }
            }
            if let depth = style.vibrato, !joins, style.vibratoParts?.contains(o.part) ?? true {  // vary the vibrato from one bow to the next
                s.sendController(1, withValue: UInt8(Int.random(in: depth, using: &rng)), onChannel: 0)
            }
            if style.swell { p.held = 0; sendExpr(p); tick() }
            nextID += 1
            p.sounding[o.pitch] = Note(sampler: s, start: o.time, id: nextID)
            s.startNote(UInt8(o.pitch), withVelocity: UInt8(o.velocity), onChannel: 0)
        case "off":
            p.lastOff = o.time
            guard let n = p.sounding[o.pitch] else { return }
            p.sounding[o.pitch]!.over = true
            // the engine's gap is 1/19 of what it played (0.05 of the written length)
            let hold = (style.fillGap ? min((o.time - n.start) / 19, 0.25) : 0) + style.overlapS
            let stop = {
                guard p.sounding[o.pitch]?.id == n.id else { return }  // re-struck since: that note is not ours
                p.sounding[o.pitch] = nil
                n.sampler.stopNote(UInt8(o.pitch), onChannel: 0)
            }
            if hold <= 0 { stop(); return }
            after(hold) { [self] in lock.lock(); defer { lock.unlock() }; stop() }
        default:  // expression (CC11), on both samplers
            p.expr = o.velocity
            sendExpr(p)
        }
    }

    private static let tickS = 0.02

    /// Swell: the target gain rises from 0.88 to 1 (CC11 acts about squared: 2 dB) between 0.4 and 1.6 s into a
    /// note and falls back between 3 and 6 s; the gain follows it with a 0.25 s lag, so a line of short notes
    /// neither pumps nor swells much.
    private func tick() {
        guard !ticking else { return }
        ticking = true
        after(Self.tickS) { [self] in
            lock.lock(); defer { lock.unlock() }
            ticking = false
            var any = false
            for p in parts.values where !p.sounding.isEmpty || abs(p.level - 0.88) > 0.005 {
                p.held += Self.tickS
                let ss = { (x: Double) in let c = min(max(x, 0), 1); return c * c * (3 - 2 * c) }
                let target = 0.88 + 0.12 * (ss((p.held - 0.4) / 1.2) - ss((p.held - 3) / 3))
                p.level += (target - p.level) * Self.tickS / 0.25
                sendExpr(p)
                any = true
            }
            if any { tick() }
        }
    }

    private func sendExpr(_ p: Part) {
        let v = style.swell ? Int((Double(p.expr) * p.level).rounded()) : p.expr
        guard v != p.sent else { return }
        p.sent = v
        for s in [p.attack, p.legato] { s.sendController(11, withValue: UInt8(v), onChannel: 0) }
        if let eq = p.shelf {  // the treble opens with the expression: dark at CC11 40 and below, flat at 127
            let open = Float(min(max(Double(v - 40) / 87, 0), 1))
            eq.bands[0].gain = style.darkDB * (1 - open)
        }
    }

    /// After an interruption (a call, Siri) or a route change, start the engine again.
    func resume() {
        if !engine.isRunning { try? engine.start() }
    }
}
