import AVFoundation

// The app's audio session: the strings out through a small buffer (5 ms) so they answer the piano
// quickly, and the microphone in for voice cues. Set up once, before the strings start.

enum AudioSetup {
    static let bufferS = 0.005

    static func configure() throws {
        let audio = AVAudioSession.sharedInstance()
        try audio.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetoothA2DP])
        try audio.setPreferredIOBufferDuration(bufferS)
        try audio.setActive(true)
    }
}
