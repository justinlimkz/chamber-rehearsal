import Foundation
import RehearsalCore

// Reads each MusicXML file the Mac read for ios/ImportFixtures (`rehearsal bundle --import-fixtures`) the iPad's way
// (ScoreImport) and compares: the composer, the starting tempo, the bars, every part's notes and the string parts' loudness curves.
// Usage (from the repo root): swift run --package-path ios/RehearsalCore import-check [fixtures-dir]

let dir = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "ios/ImportFixtures")
let files = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
    .filter { $0.pathExtension == "json" }.sorted { $0.path < $1.path }

func rows(_ x: Any?) -> [[Double]] { ((x as? [[Any]]) ?? []).map { $0.map { ($0 as? NSNumber)?.doubleValue ?? .nan } } }

/// Where two lists of rows first differ (rows sorted first when `sort`), or nil.
func diff(_ a: [[Double]], _ b: [[Double]], sort: Bool = false) -> String? {
    if sort && ProcessInfo.processInfo.environment["IMPORT_ALL"] != nil {  // every note that differs
        let key = { (r: [Double]) in r.map { String(format: "%.6f", $0) }.joined(separator: " ") }
        var count: [String: Int] = [:]
        for r in a { count[key(r), default: 0] += 1 }
        for r in b { count[key(r), default: 0] -= 1 }
        let d = count.filter { $0.value != 0 }.sorted { $0.key < $1.key }
        return d.isEmpty ? nil : d.map { "\($0.value > 0 ? "missing" : "extra") \($0.key)" }.joined(separator: "\n      ")
    }
    let order = { (x: [Double], y: [Double]) in x.lexicographicallyPrecedes(y) }
    let (a, b) = sort ? (a.sorted(by: order), b.sorted(by: order)) : (a, b)
    for (i, (x, y)) in zip(a, b).enumerated() where x.count != y.count || zip(x, y).contains(where: { abs($0 - $1) > 1e-9 }) {
        return "row \(i): expected \(x), got \(y)"
    }
    return a.count == b.count ? nil : "expected \(a.count) rows, got \(b.count)"
}

var failed = 0
for f in files {
    let want = try JSONSerialization.jsonObject(with: Data(contentsOf: f)) as! [String: Any]
    let name = f.deletingPathExtension().lastPathComponent
    var problems: [String] = []
    do {
        let imp = try ScoreImport(data: Data(contentsOf: URL(fileURLWithPath: want["source"] as! String)))
        let got = imp.json()
        if let debug = ProcessInfo.processInfo.environment["IMPORT_DEBUG"], let p = imp.parts[debug] {  // what was read
            print("\(name) \(debug) marks: \(p.marks.prefix(40))\n  hairpins: \(p.hairpins.prefix(40))")
        }
        do {  // the app reads what it writes
            _ = try JSONDecoder().decode(ScoreData.self, from: JSONSerialization.data(withJSONObject: got))
        } catch {
            problems.append("score.json doesn't load: \(error)")
        }
        if (want["composer"] as? String) != (got["composer"] as? String) {
            problems.append("composer: expected \(want["composer"] ?? ""), got \(got["composer"] ?? "")")
        }
        if (want["tempo"] as? Double) != (got["tempo"] as? Double) {
            problems.append("tempo: expected \(want["tempo"] ?? "none"), got \(got["tempo"] ?? "none")")
        }
        if let d = diff(rows(want["barStarts"]), rows(got["barStarts"])) { problems.append("bars: \(d)") }
        let wp = want["parts"] as! [String: Any], gp = got["parts"] as! [String: Any]
        if Set(wp.keys) != Set(gp.keys) { problems.append("parts: expected \(wp.keys.sorted()), got \(gp.keys.sorted())") }
        for k in wp.keys.sorted() {
            if let d = diff(rows(wp[k]), rows(gp[k]), sort: true) { problems.append("\(k) notes: \(d)") }
        }
        let wd = want["dynamics"] as! [String: Any], gd = got["dynamics"] as! [String: Any]
        for k in wd.keys.sorted() {
            if let d = diff(rows(wd[k]), rows(gd[k])) { problems.append("\(k) dynamics: \(d)") }
        }
    } catch {
        problems.append("couldn't import: \(error.localizedDescription)")
    }
    if problems.isEmpty {
        print("PASS \(name)")
    } else {
        failed += 1
        print("FAIL \(name)")
        for p in problems { print("    \(p)") }
    }
}
print(failed == 0 ? "all \(files.count) imports match" : "\(failed) of \(files.count) imports differ")

// Files the app must refuse, each with its reason (tests/musicxml/refused).
let refused: [String: MusicXMLError] = [
    "empty": .unreadable("the file is empty"),
    "not-a-score": .unreadable("it isn't valid XML"),
    "no-piano": .noPiano,
    "piano-only": .noStrings,
    "timewise": .notPartwise,
]
var wrong = 0
for (name, want) in refused.sorted(by: { $0.key < $1.key }) {
    let url = URL(fileURLWithPath: "tests/musicxml/refused/\(name).musicxml")
    do {
        _ = try ScoreImport(data: Data(contentsOf: url))
        wrong += 1
        print("FAIL refused/\(name): imported")
    } catch let e as MusicXMLError where e == want {
        print("PASS refused/\(name)")
    } catch {
        wrong += 1
        print("FAIL refused/\(name): \(error)")
    }
}
print(wrong == 0 ? "all \(refused.count) bad files refused" : "\(wrong) of \(refused.count) bad files not refused as they should be")
exit(failed == 0 && wrong == 0 ? 0 : 1)
