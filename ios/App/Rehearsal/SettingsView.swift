import AVFoundation
import SwiftUI

// The little there is to set: the piano (with a live test), the strings' volume and where the sound goes,
// voice cues, and keeping the screen on.

struct SettingsView: View {
    let rig: Rig
    @Environment(\.dismiss) private var dismiss
    @State private var keyboard: String?
    @State private var lastKey: (note: Int, velocity: Int)?
    @State private var volume: Float = 1
    @State private var output = ""
    @State private var voiceState = "off"
    @AppStorage(Settings.voice) private var voice = true
    @AppStorage(Settings.keepAwake) private var keepAwake = true

    var body: some View {
        NavigationStack {
            Form {
                Section("Piano") {
                    LabeledContent(keyboard ?? "No keyboard", value: keyboard == nil ? "Plug in the piano (USB)" : "Connected by USB")
                    LabeledContent("Last note heard", value: lastKey.map { "\(Self.name($0.note)), velocity \($0.velocity)" } ?? "Press a key")
                }
                Section("Sound") {
                    HStack {
                        Text("Strings volume")
                        Slider(value: $volume, in: 0...1)
                            .onChange(of: volume) { _, v in
                                rig.strings.volume = v
                                UserDefaults.standard.set(v, forKey: Settings.volume)
                            }
                    }
                    LabeledContent("Output", value: output)
                }
                Section {
                    Toggle("Restart by voice", isOn: $voice)
                    if voice && voiceState == "denied" {
                        Button("Allow the microphone and speech recognition") {
                            if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                        }
                    }
                } header: { Text("Voice") } footer: {
                    if voice && voiceState == "denied" {
                        Text("Voice cues need the microphone and speech recognition, which were turned off for Rehearsal. Turn both on in Settings, then open the app again. Until then, use the buttons.")
                    } else if voice && voiceState == "unsupported" {
                        Text("This iPad can't recognise speech on the device, so voice cues are off. Use the buttons.")
                    } else {
                        Text("Listens only while the strings are stopped: “from bar nine”, “again”, “from the top”, “pause”, “resume”. Works offline, on the iPad.")
                    }
                }
                Section("Screen") {
                    Toggle("Keep the screen on while rehearsing", isOn: $keepAwake)
                        .onChange(of: keepAwake) { _, on in UIApplication.shared.isIdleTimerDisabled = on }
                }
                Section {} footer: {
                    Text("Strings: MuseScore's MS Basic SoundFont (MIT).")
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .onAppear { volume = rig.strings.volume }
            .task {
                while !Task.isCancelled {
                    keyboard = rig.keyboard.name
                    lastKey = rig.lastKey
                    voiceState = rig.voice.state
                    output = AVAudioSession.sharedInstance().currentRoute.outputs.first?.portName ?? "–"
                    try? await Task.sleep(for: .milliseconds(200))
                }
            }
        }
    }

    static func name(_ midi: Int) -> String {
        let names = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]
        return names[midi % 12] + String(midi / 12 - 1)
    }
}
