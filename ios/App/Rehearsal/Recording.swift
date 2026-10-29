import Foundation
import RehearsalCore

// Each session saved for listening back (live.py's session files): the piano, the strings and the
// notices as markers, in one MIDI file laid out as `simulate.write_midi` lays it out (a channel per part,
// wall-clock time at 120 bpm), so the Mac's `session.py` reads it as it reads the Mac's own.
// Saved in the app's Documents/Sessions folder, which the Files app shows (On My iPad › Rehearsal), with a
// small JSON file alongside (`<name>.json`: the piece and its passes) for the app's recordings list.

struct Recording {
    static let channels = ["Violin": 0, "Viola": 1, "Violoncello": 2, "Piano": 3]
    static let programs = ["Violin": 40, "Viola": 41, "Violoncello": 42, "Piano": 0]
    static let ticksPerSecond = 960.0  // 480 ticks per beat at 120 bpm (midi_io.TICKS_PER_SECOND)

    let start: Double  // session time of the recording's zero
    let name: String
    let piece: String
    private(set) var piano: [(t: Double, e: PianoEvent)] = []
    private(set) var strings: [Out] = []
    private(set) var notices: [Notice] = []

    init(start: Double, piece: String) {
        self.start = start
        self.piece = piece
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        name = f.string(from: Date()) + ".mid"
    }

    var isEmpty: Bool { piano.isEmpty }

    mutating func piano(_ t: Double, _ e: PianoEvent) { piano.append((t - start, e)) }
    mutating func played(_ t: Double, _ o: Out) {
        strings.append(Out(t - start, o.kind, o.part, pitch: o.pitch, velocity: o.velocity, beat: o.beat))
    }
    mutating func notice(_ n: Notice) { notices.append(Notice(t: max(0, n.t - start), text: n.text)) }

    static var folder: URL {
        URL.documentsDirectory.appendingPathComponent("Sessions", isDirectory: true)
    }

    /// Write (or rewrite) the file and its summary; returns where.
    func save(summary: Data) throws -> URL {
        try FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        let url = Self.folder.appendingPathComponent(name)
        try midi().write(to: url, options: .atomic)
        try summary.write(to: url.deletingPathExtension().appendingPathExtension("json"), options: .atomic)
        return url
    }

    /// A type-0 Standard MIDI File.
    func midi() -> Data {
        var events: [(t: Double, order: Int, bytes: [UInt8])] = []
        for n in notices {
            let text = Array(n.text.utf8)
            events.append((n.t, events.count, [0xFF, 0x06] + varLen(text.count) + text))
        }
        for (part, ch) in Self.channels.sorted(by: { $0.value < $1.value }) {
            events.append((0, events.count, [0xC0 | UInt8(ch), UInt8(Self.programs[part]!)]))
        }
        let pc = UInt8(Self.channels["Piano"]!)
        for (t, e) in piano {
            switch e.kind {
            case .noteOn: events.append((t, events.count, [0x90 | pc, UInt8(e.note), UInt8(e.value)]))
            case .noteOff: events.append((t, events.count, [0x80 | pc, UInt8(e.note), 0]))
            case .pedal: events.append((t, events.count, [0xB0 | pc, 64, UInt8(e.value)]))
            }
        }
        for o in strings {
            let ch = UInt8(Self.channels[o.part]!)
            switch o.kind {
            case "on": events.append((o.time, events.count, [0x90 | ch, UInt8(o.pitch), UInt8(o.velocity)]))
            case "off": events.append((o.time, events.count, [0x80 | ch, UInt8(o.pitch), 0]))
            default: events.append((o.time, events.count, [0xB0 | ch, 11, UInt8(o.velocity)]))
            }
        }
        events.sort { $0.t != $1.t ? $0.t < $1.t : $0.order < $1.order }
        var track: [UInt8] = [0x00, 0xFF, 0x51, 0x03, 0x07, 0xA1, 0x20]  // tempo 500000 µs per beat
        var last = 0
        for e in events {
            let tick = Int((max(e.t, 0) * Self.ticksPerSecond).rounded())
            track += varLen(tick - last) + e.bytes
            last = tick
        }
        track += [0x00, 0xFF, 0x2F, 0x00]
        var data: [UInt8] = Array("MThd".utf8) + be32(6) + [0, 0, 0, 1, 0x01, 0xE0]  // format 0, 1 track, 480 tpb
        data += Array("MTrk".utf8) + be32(track.count) + track
        return Data(data)
    }

    private func varLen(_ n: Int) -> [UInt8] {
        var n = n, out = [UInt8(n & 0x7F)]
        n >>= 7
        while n > 0 {
            out.insert(UInt8(n & 0x7F) | 0x80, at: 0)
            n >>= 7
        }
        return out
    }

    private func be32(_ n: Int) -> [UInt8] { [24, 16, 8, 0].map { UInt8((n >> $0) & 0xFF) } }
}
