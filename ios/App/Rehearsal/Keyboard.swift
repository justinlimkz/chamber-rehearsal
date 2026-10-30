import CoreMIDI
import Foundation

// The piano's MIDI (live.KeyboardLink on the iPad): listens to every MIDI source there is (the FP-30 on
// USB is normally the only one) and connects again whenever one is plugged in or out.

final class Keyboard: @unchecked Sendable {
    var onEvent: (Double, PianoEvent) -> Void = { _, _ in }  // (host time in seconds, event): notes and the pedal
    var onChange: (String?) -> Void = { _ in }  // the keyboard's name, or nil when there is none
    private(set) var name: String?
    private var announced = false

    private var client = MIDIClientRef()
    private var port = MIDIPortRef()

    func start() {
        MIDIClientCreateWithBlock("Rehearsal" as CFString, &client) { [weak self] note in
            if note.pointee.messageID == .msgSetupChanged {
                DispatchQueue.main.async { self?.connect() }
            }
        }
        MIDIInputPortCreateWithProtocol(client, "Piano" as CFString, ._1_0, &port) { [weak self] list, _ in
            self?.receive(list)
        }
        connect()
    }

    private func connect() {
        var names: [String] = []
        for i in 0..<MIDIGetNumberOfSources() {
            let src = MIDIGetSource(i)
            MIDIPortDisconnectSource(port, src)
            MIDIPortConnectSource(port, src, nil)
            var cf: Unmanaged<CFString>?
            if MIDIObjectGetStringProperty(src, kMIDIPropertyDisplayName, &cf) == noErr, let cf {
                names.append(cf.takeRetainedValue() as String)
            }
        }
        // Simulator screenshots: REHEARSAL_KEYBOARD=<name> stands in for a piano
        let now = names.first ?? (Rig.silent ? ProcessInfo.processInfo.environment["REHEARSAL_KEYBOARD"] : nil)
        if now != name || !announced {
            name = now
            announced = true
            onChange(now)
        }
    }

    private func receive(_ list: UnsafePointer<MIDIEventList>) {
        for packet in list.unsafeSequence() {
            let t = packet.pointee.timeStamp != 0 ? HostTime.seconds(packet.pointee.timeStamp) : HostTime.now()
            for word in packet.words() where word >> 28 == 0x2 {  // MIDI 1.0 channel voice message
                let status = (word >> 16) & 0xF0, data1 = Int((word >> 8) & 0x7F), data2 = Int(word & 0x7F)
                switch status {
                case 0x90 where data2 > 0: onEvent(t, PianoEvent(kind: .noteOn, note: data1, value: data2))
                case 0x80, 0x90: onEvent(t, PianoEvent(kind: .noteOff, note: data1, value: 0))
                case 0xB0 where data1 == 64: onEvent(t, PianoEvent(kind: .pedal, note: 0, value: data2))
                default: break
                }
            }
        }
    }
}

/// A piano event as recorded (midi_io.MidiEvent): a note on or off, or the sustain pedal (`value` 0-127).
struct PianoEvent {
    enum Kind { case noteOn, noteOff, pedal }
    var kind: Kind
    var note: Int
    var value: Int
}
