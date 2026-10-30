// Offline renders through the app's own Strings.swift (compiled in by build.sh), so a render is what the iPad plays.
//
//   render events <score.json> <firstBar> <lastBar> <out.json> [flat]
//       Runs the real engine (with `flat`, without the score's dynamics: every note at velocity 80) (RehearsalCore's Simulator) on a synthetic piano take that plays every piano onset of
//       the bars exactly in time at 63 bpm, and writes the string events it emits: [[time, kind, part, pitch, vel]].
//   render play <font.sf2> <events.json> <out.wav> [key=value ...]
//       Plays events (from `events`, or written by tone/clips.py) through Strings in manual rendering mode.
//       keys: legato=0|1 swell=0|1 vibrato=lo-hi (CC1 range) vibparts=Violin,... mix=<reverb %> seed=<n>
//             seat=0 (all parts centred) dark=<dB> (treble shelf at soft expression; 0: none) lag=0 (no part lag)
import AVFoundation
import RehearsalCore

let a = CommandLine.arguments
func die(_ s: String) -> Never { FileHandle.standardError.write((s + "\n").data(using: .utf8)!); exit(1) }
guard a.count >= 2 else { die("usage: render events|play ...") }

if a[1] == "events" {
    guard a.count == 6 || a.count == 7 else { die("usage: render events <score.json> <firstBar> <lastBar> <out.json> [flat]") }
    let score = try ScoreData.load(path: a[2])
    let first = Int(a[3])!, last = Int(a[4])!
    let spb = 60.0 / 63.0, lead = 1.0  // the take starts 1 s in
    let starts = Dictionary(score.barStarts.map { ($0.1, $0.0) }, uniquingKeysWith: { x, _ in x })
    let lo = starts[first]!, hi = starts[last + 1] ?? .infinity
    let onsets = score.notes("Piano").filter { lo <= $0.onsetBeat && $0.onsetBeat < hi }
        .map { (lead + ($0.onsetBeat - lo) * spb, $0.pitch) }
    let r = Simulator.run(score: score, onsets: onsets, firstBar: first, lastBar: last, clock: BeatClock(spb: spb),
                           dynamics: a.count == 6)
    let rows = r.outs.map { [$0.time, $0.kind, $0.part, $0.pitch, $0.velocity] as [Any] }
    try JSONSerialization.data(withJSONObject: rows).write(to: URL(fileURLWithPath: a[5]))
    exit(0)
}

guard a[1] == "play", a.count >= 5 else { die("usage: render play <font.sf2> <events.json> <out.wav> [key=value ...]") }
var opts: [String: String] = [:]
for kv in a.dropFirst(5) { let p = kv.split(separator: "=", maxSplits: 1); opts[String(p[0])] = String(p[1]) }
var style = Strings.Style()
if let v = opts["legato"] { style.legato = v == "1" }
if let v = opts["swell"] { style.swell = v == "1" }
if let v = opts["vibrato"] { let r = v.split(separator: "-").map { Int($0)! }; style.vibrato = r[0]...r[1] }
if let v = opts["vibparts"] { style.vibratoParts = Set(v.split(separator: ",").map(String.init)) }
if let v = opts["mix"] { style.reverbMix = Float(v)! }
if opts["seat"] == "0" { style.seating = [:] }
if let v = opts["dark"] { style.darkDB = Float(v)! }
if opts["lag"] == "0" { style.lag = [:] }
if let v = opts["color"] {  // Part:treble:body,...
    style.color = [:]
    for c in v.split(separator: ",") { let f = c.split(separator: ":"); style.color[String(f[0])] = (Float(f[1])!, Float(f[2])!) }
}
if let v = opts["handover"] { style.handoverS = v == "off" ? nil : Double(v)! }

let rows = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: a[3]))) as! [[Any]]
let events = rows.map { r in
    Out((r[0] as! NSNumber).doubleValue, r[1] as! String, r[2] as! String,
        pitch: (r[3] as! NSNumber).intValue, velocity: (r[4] as! NSNumber).intValue)
}.enumerated().sorted { $0.element.time != $1.element.time ? $0.element.time < $1.element.time : $0.offset < $1.offset }
.map { $0.element }

let sr = 48000.0
let format = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
let engine = AVAudioEngine()
var now = 0.0
var later: [(Double, () -> Void)] = []  // Strings' delayed calls, run in render time
let strings = try Strings(engine: engine, font: URL(fileURLWithPath: a[2]), style: style) { d, f in later.append((now + d, f)) }
try engine.enableManualRenderingMode(.offline, format: format, maximumFrameCount: 4096)
try engine.start()
let file = try AVAudioFile(forWriting: URL(fileURLWithPath: a[4]), settings: format.settings)
let block: AVAudioFrameCount = 48  // 1 ms: the engine's own step, so event timing is as on the iPad
let buf = AVAudioPCMBuffer(pcmFormat: engine.manualRenderingFormat, frameCapacity: block)!
let end = (events.last?.time ?? 0) + 3.0
var next = 0
while now < end {
    while true {  // everything due by now, in time order
        let e = next < events.count ? events[next].time : .infinity
        let l = later.indices.min { later[$0].0 < later[$1].0 }
        let lt = l.map { later[$0].0 } ?? .infinity
        if min(e, lt) > now { break }
        if lt < e { later.remove(at: l!).1() } else { strings.play(events[next]); next += 1 }
    }
    guard try engine.renderOffline(block, to: buf) == .success else { die("render failed") }
    try file.write(from: buf)
    now += Double(block) / sr
}
file.close()  // writes the header (a global is never deinitialised)
