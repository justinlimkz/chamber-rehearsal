// Runs the real engine (RehearsalCore's Simulator, unmodified) on a given piano take instead of tone/render's
// perfectly-timed 63 bpm one, so the strings follow a real recording's tempo map.
//
//   compare-events <score.json> <onsets.json> <firstBar> <lastBar> <spb> <out.json> [flat]
//       onsets.json: [[time_s, pitch], ...] (the synthetic piano take); spb: the clock's starting seconds per beat.
//       Writes the string events the engine emits: [[time, kind, part, pitch, value]] (as `render events`).
import Foundation
import RehearsalCore

let a = CommandLine.arguments
guard a.count == 7 || a.count == 8 else {
    FileHandle.standardError.write("usage: compare-events <score.json> <onsets.json> <first> <last> <spb> <out.json> [flat]\n".data(using: .utf8)!)
    exit(1)
}
let score = try ScoreData.load(path: a[1])
let raw = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: a[2]))) as! [[NSNumber]]
let onsets = raw.map { ($0[0].doubleValue, $0[1].intValue) }
let spb = Double(a[5])!
let r = Simulator.run(score: score, onsets: onsets, firstBar: Int(a[3])!, lastBar: Int(a[4])!,
                      clock: BeatClock(spb: spb), dynamics: a.count == 7)
let rows = r.outs.map { [$0.time, $0.kind, $0.part, $0.pitch, $0.velocity] as [Any] }
try JSONSerialization.data(withJSONObject: rows).write(to: URL(fileURLWithPath: a[6]))
