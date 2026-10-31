import AVFoundation
import RehearsalCore
import Speech

// Voice cues (rehearsal/voice.py on the iPad): "from bar nine", "from the pickup to bar nine",
// "from the top", "again", and "pause"/"stop" (the strings sit out while you practise alone), "resume"/
// "continue"/"come back". Apple's on-device speech recognition, listened to only while the strings are
// waiting, so the piano and the strings don't trigger it.
//
// Recognition is open dictation (not a fixed grammar), so the text is searched for the last cue
// in it. Results arrive word by word; a bar number may still grow ("from bar one" ... "nineteen"), so a
// bar cue waits for a short pause in the results before it is acted on. "again" and "from the top" go at once.

final class Voice: @unchecked Sendable {
    static let settle = 0.6  // seconds without a new result before a bar cue counts

    var onCue: (Cue, String) -> Void = { _, _ in }  // the cue and the words heard
    // "off" (not started yet) | "denied" (microphone or speech recognition refused) | "unsupported" (no on-device
    // recognition here) | "asleep" | "listening"
    private(set) var state = "off"

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var pending: DispatchWorkItem?
    private var fired = false  // this utterance already gave a cue
    private var generation = 0  // which recognition task is current (a cancelled one still calls back)
    private let queue = DispatchQueue(label: "voice")
    private var wanted = false  // listen as soon as the microphone is ready

    /// Ask for the microphone and speech recognition, then start the microphone (it only feeds the
    /// recogniser while `listen(true)`).
    func start() {
        SFSpeechRecognizer.requestAuthorization { status in
            guard status == .authorized else { return self.queue.async { self.state = "denied" } }
            AVAudioApplication.requestRecordPermission { granted in
                self.queue.async { if granted { self.startMicrophone() } else { self.state = "denied" } }
            }
        }
    }

    private func startMicrophone() {
        guard let recognizer, recognizer.supportsOnDeviceRecognition else { state = "unsupported"; return }
        let input = engine.inputNode
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { [weak self] buffer, _ in
            self?.request?.append(buffer)
        }
        do { try engine.start() } catch { state = "unsupported"; return }
        state = "asleep"
        if wanted { begin() }
    }

    /// Listen (the strings are waiting) or not.
    func listen(_ on: Bool) {
        queue.async {
            self.wanted = on
            guard self.state == "asleep" || self.state == "listening" else { return }
            if on && self.task == nil { self.begin() }
            if !on && self.task != nil { self.end() }
        }
    }

    private func begin() {
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        request.taskHint = .confirmation
        request.contextualStrings = ["from the top", "again", "from bar", "from the pickup to bar", "from measure"]
        self.request = request
        fired = false
        generation += 1
        let gen = generation
        task = recognizer?.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            let text = result?.bestTranscription.formattedString, final = result?.isFinal ?? false, failed = error != nil
            self.queue.async {
                guard gen == self.generation, self.task != nil else { return }
                if let text { self.heard(text, final: final) }
                if failed || final, gen == self.generation, self.task != nil {
                    self.end(); self.begin()  // recognition stops after a pause or a minute: go again
                }
            }
        }
        state = "listening"
    }

    private func end() {
        pending?.cancel(); pending = nil
        request?.endAudio(); task?.cancel()
        request = nil; task = nil
        state = "asleep"
    }

    private func heard(_ text: String, final: Bool) {
        guard !fired, let cue = Self.parse(text) else { return }
        pending?.cancel()
        let go = DispatchWorkItem { [weak self] in
            guard let self, !self.fired else { return }
            self.fired = true
            self.onCue(cue, text)
            self.end(); self.begin()  // a fresh utterance for the next cue
        }
        pending = go
        if cue.kind != "bar" || final {
            queue.async(execute: go)
        } else {
            queue.asyncAfter(deadline: .now() + Self.settle, execute: go)
        }
    }

    // MARK: parsing (voice.parse, for dictated text: digits or words, any case, punctuation)

    static let ones = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                       "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    static let tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

    /// The last cue in the text, if any.
    static func parse(_ text: String) -> Cue? {
        let words = text.lowercased().replacingOccurrences(of: "-", with: " ")
            .components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }
        let keys: Set<String> = ["from", "again", "pause", "stop", "resume", "continue"]
        guard let i = words.indices.last(where: { keys.contains(words[$0]) || (words[$0] == "back" && $0 > 0 && words[$0 - 1] == "come") })
        else { return nil }
        switch words[i] {
        case "again": return Cue(kind: "again")
        case "pause", "stop": return Cue(kind: "pause")
        case "resume", "continue", "back": return Cue(kind: "resume")
        default: break
        }
        var rest = Array(words[(i + 1)...])
        if rest.starts(with: ["the", "top"]) || rest.starts(with: ["the", "beginning"]) || rest == ["top"] {
            return Cue(kind: "top")
        }
        let pickup = rest.starts(with: ["the", "pickup", "to"])
        if pickup { rest.removeFirst(3) }
        guard let w = rest.first, ["bar", "bars", "measure"].contains(w) else { return nil }
        guard let n = number(Array(rest.dropFirst())), n > 0 else { return nil }
        return Cue(kind: "bar", bar: n, pickup: pickup)
    }

    /// "19", "nineteen", "a hundred and five".
    static let soundsLike = ["to": 2, "too": 2, "for": 4, "won": 1, "ate": 8]  // dictation's guesses for a lone number

    static func number(_ words: [String]) -> Int? {
        if let w = words.first, let n = Int(w) ?? (words.count == 1 ? soundsLike[w] : nil) { return n }
        var current = 0, any = false
        for w in words {
            if w == "and" { continue }
            if let k = ones.firstIndex(of: w) { current += k }
            else if let k = tens.firstIndex(of: w), k >= 2 { current += 10 * k }
            else if w == "hundred" { current = max(current, 1) * 100 }
            else if w == "a" { current += 1 }
            else { break }
            any = true
        }
        return any ? current : nil
    }
}
