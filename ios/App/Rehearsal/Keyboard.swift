import CoreMIDI
import Foundation

// The piano's MIDI (live.KeyboardLink on the iPad): listens to every MIDI source there is (the FP-30 on
// USB is normally the only one) and connects again whenever one is plugged in or out.

final class Keyboard: @unchecked Sendable {
    var onNote: (Double, Int) -> Void = { _, _ in }  // (host time in seconds, pitch) for each note-on
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
        let now = names.first
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
                let status = (word >> 16) & 0xF0, pitch = Int((word >> 8) & 0x7F), velocity = word & 0x7F
                if status == 0x90 && velocity > 0 {
                    onNote(t, pitch)
                }
            }
        }
    }
}
