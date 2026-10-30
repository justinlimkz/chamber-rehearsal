import Foundation
import RehearsalCore

// Runs the Simulator on every golden fixture and compares with what the Python engine emitted.
// Usage (from the repo root): swift run --package-path ios/RehearsalCore fixture-check [score.json] [fixtures-dir]

// Decoded with JSONDecoder, not JSONSerialization: the latter rounds some doubles one ulp off
// (e.g. "0.049613095238097316"), which shows up as bogus last-digit mismatches in the log.
struct Fixture: Decodable {
    var name: String
    var firstBar: Int, lastBar: Int
    var tempo: Double
    var onsets: [(Double, Int)]
    var cues: [(Double, Cue)]
    var outs: [Out]
    var log: [LogEntry]
    var notices: [Notice]

    private enum Keys: String, CodingKey { case name, firstBar, lastBar, tempo, onsets, cues, outs, log, notices }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        name = try c.decode(String.self, forKey: .name)
        firstBar = try c.decode(Int.self, forKey: .firstBar)
        lastBar = try c.decode(Int.self, forKey: .lastBar)
        tempo = try c.decode(Double.self, forKey: .tempo)
        onsets = []
        var u = try c.nestedUnkeyedContainer(forKey: .onsets)
        while !u.isAtEnd {
            var r = try u.nestedUnkeyedContainer()
            onsets.append((try r.decode(Double.self), try r.decode(Int.self)))
        }
        cues = []
        if c.contains(.cues) {
            u = try c.nestedUnkeyedContainer(forKey: .cues)
            while !u.isAtEnd {
                var r = try u.nestedUnkeyedContainer()
                let t = try r.decode(Double.self), kind = try r.decode(String.self)
                cues.append((t, Cue(kind: kind, bar: try r.decode(Int.self), pickup: try r.decode(Bool.self))))
            }
        }
        outs = []
        u = try c.nestedUnkeyedContainer(forKey: .outs)
        while !u.isAtEnd {
            var r = try u.nestedUnkeyedContainer()
            let t = try r.decode(Double.self), kind = try r.decode(String.self), part = try r.decode(String.self)
            let pitch = try r.decode(Int.self), vel = try r.decode(Int.self), beat = try r.decode(Double.self)
            outs.append(Out(t, kind, part, pitch: pitch, velocity: vel, beat: beat))
        }
        log = []
        u = try c.nestedUnkeyedContainer(forKey: .log)
        while !u.isAtEnd {
            var r = try u.nestedUnkeyedContainer()
            log.append(LogEntry(t: try r.decode(Double.self), beat: try r.decode(Double.self), asyn: try r.decode(Double.self)))
        }
        notices = []
        u = try c.nestedUnkeyedContainer(forKey: .notices)
        while !u.isAtEnd {
            var r = try u.nestedUnkeyedContainer()
            notices.append(Notice(t: try r.decode(Double.self), text: try r.decode(String.self)))
        }
    }

    init(path: String) throws {
        self = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
    }
}

func show(_ o: Out) -> String { "t=\(o.time) \(o.kind) \(o.part) pitch=\(o.pitch) vel=\(o.velocity) beat=\(o.beat)" }

func outMismatch(_ e: Out, _ g: Out) -> Bool {
    e.kind != g.kind || e.part != g.part || e.pitch != g.pitch || e.velocity != g.velocity
        || abs(e.beat - g.beat) > 1e-9 || abs(e.time - g.time) > 1e-6
}

/// First mismatch of two sequences, with a few surrounding entries; nil if they agree.
func compare<T>(_ what: String, _ exp: [T], _ got: [T], differ: (T, T) -> Bool, show: (T) -> String) -> String? {
    var idx: Int? = nil
    for k in 0..<min(exp.count, got.count) where differ(exp[k], got[k]) { idx = k; break }
    if idx == nil && exp.count != got.count { idx = min(exp.count, got.count) }
    guard let k = idx else { return nil }
    var s = "\(what): count expected \(exp.count), got \(got.count); first mismatch at index \(k)\n"
    s += "    expected: \(k < exp.count ? show(exp[k]) : "(none)")\n"
    s += "    got:      \(k < got.count ? show(got[k]) : "(none)")\n"
    for j in max(0, k - 2)...(k + 2) {
        let e = j < exp.count ? show(exp[j]) : "(none)", g = j < got.count ? show(got[j]) : "(none)"
        s += "    \(j == k ? ">" : " ") [\(j)] exp \(e)\n          [\(j)] got \(g)\n"
    }
    return s
}

let args = Array(CommandLine.arguments.dropFirst())
let scorePath = args.count > 0 ? args[0] : "ios/Bundles/schumann-op47-iii/score.json"
let fixturesDir = args.count > 1 ? args[1] : "ios/Fixtures"

do {
    let score = try ScoreData.load(path: scorePath)
    let files = try FileManager.default.contentsOfDirectory(atPath: fixturesDir)
        .filter { $0.hasSuffix(".json") }.sorted()
    if files.isEmpty { print("no fixtures in \(fixturesDir)"); exit(2) }
    var failed = 0
    for f in files {
        let fx = try Fixture(path: fixturesDir + "/" + f)
        let res = Simulator.run(score: score, onsets: fx.onsets, firstBar: fx.firstBar, lastBar: fx.lastBar,
                                clock: BeatClock(spb: 60 / fx.tempo), cues: fx.cues)
        var problems: [String] = []
        if let p = compare("outs", fx.outs, res.outs, differ: outMismatch, show: show) { problems.append(p) }
        if let p = compare("log", fx.log, res.log, differ: { $0 != $1 },
                           show: { "t=\($0.t) beat=\($0.beat) asyn=\($0.asyn)" }) { problems.append(p) }
        if let p = compare("notices", fx.notices, res.notices, differ: { $0 != $1 },
                           show: { "t=\($0.t) \"\($0.text)\"" }) { problems.append(p) }
        if problems.isEmpty {
            print("PASS \(fx.name) (\(res.outs.count) outs, \(res.log.count) log, \(res.notices.count) notices)")
        } else {
            failed += 1
            print("FAIL \(fx.name)")
            problems.forEach { print("  " + $0) }
        }
    }
    print(failed == 0 ? "all \(files.count) fixtures pass" : "\(failed) of \(files.count) fixtures FAILED")
    exit(failed == 0 ? 0 : 1)
} catch {
    print("error: \(error)")
    exit(2)
}
