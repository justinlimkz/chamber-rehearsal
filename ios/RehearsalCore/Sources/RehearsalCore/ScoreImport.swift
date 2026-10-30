import Foundation

// A piece made on the iPad from a MusicXML file: the same score.json the Mac's `rehearsal bundle` writes
// (rehearsal/bundle.py score_data), so a piece needs nothing but the score. The parts are told apart by their
// names in any of the usual spellings ("Vln.", "Violoncello", "Pianoforte"); the loudness curves are
// rehearsal/dynamics.py's, ported here (the Mac's tests and import-check keep the two the same).

public struct ScoreImport {
    public let score: MusicXMLScore
    public let parts: [String: MusicXMLPart]  // by the engine's names: Violin, Viola, Violoncello, Piano
    public let ignored: [String]  // parts the app doesn't play, by their names in the score

    public init(data: Data) throws {
        score = try MusicXMLScore(data: data)
        var parts: [String: MusicXMLPart] = [:], ignored: [String] = []
        for p in score.parts {
            guard let name = Self.instrument(p.names) else { ignored.append(p.name); continue }
            if parts[name] != nil {
                throw MusicXMLError.twoParts(name == "Violoncello" ? "cello" : name.lowercased())
            }
            parts[name] = p
        }
        guard parts["Piano"] != nil else { throw MusicXMLError.noPiano }
        self.parts = parts
        self.ignored = ignored
    }

    /// The engine's name for a part, from the names the score gives it.
    static func instrument(_ names: [String]) -> String? {
        for raw in names {
            let n = raw.lowercased().replacingOccurrences(of: ".", with: " ").trimmingCharacters(in: .whitespaces)
            let first = n.split(separator: " ").first.map(String.init) ?? n
            if n.contains("cello") || ["vc", "vlc", "vcl", "vcello"].contains(first) { return "Violoncello" }
            if n.hasPrefix("viola") || ["va", "vla", "br", "bratsche", "alto"].contains(first) { return "Viola" }
            if n.hasPrefix("violin") || n.hasPrefix("violino") || ["vn", "vln", "vl", "vni", "violon"].contains(first) { return "Violin" }
            if n.contains("piano") || n.hasPrefix("klavier") || ["pno", "pf", "pft", "kl"].contains(first) { return "Piano" }
        }
        return nil
    }

    /// score.json for the piece. `work`/`movement`: the score's own titles unless given.
    public func json(work: String? = nil, movement: String? = nil) -> [String: Any] {
        let strings = STRING_PARTS.filter { parts[$0] != nil }
        var dyn: [String: Any] = [:]
        for s in strings {
            let p = parts[s]!
            dyn[s] = shaped(curve(p), p.notes).map { [$0.0, $0.1] }
        }
        let w = work ?? score.work ?? score.title
        let m = movement ?? (score.work != nil && score.movement != score.work ? score.movement ?? "" : "")
        return [
            "title": score.title,
            "composer": score.composer,
            "work": w,
            "movement": m,
            "barStarts": parts["Piano"]!.bars.map { [$0.beat, $0.number] as [Any] },
            "parts": parts.mapValues { $0.notes.map { [$0.onsetBeat, $0.durationBeat, $0.pitch] as [Any] } },
            "dynamics": dyn,
            "leadership": NSNull(),
        ]
    }

    /// The score as the iPad engraves it: each part the app plays named as the app names it, so the Score
    /// panel's choice of parts finds it.
    public func musicxml(_ text: String) -> String {
        var out = text
        for (name, p) in parts {
            let id = NSRegularExpression.escapedPattern(for: p.id)
            guard let re = try? NSRegularExpression(
                pattern: "(<score-part[^>]*id=\"\(id)\"[^>]*>.*?<part-name[^>]*>)(.*?)(</part-name>)",
                options: [.dotMatchesLineSeparators]) else { continue }
            out = re.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out),
                                              withTemplate: "$1\(name)$3")
        }
        return out
    }
}

// MARK: rehearsal/dynamics.py

let LEVELS_DB: [String: Double] = ["pp": -12, "p": -7, "mp": -3.5, "mf": 0, "f": 4, "ff": 7]
let STEP_DB = 4.0, UNSPANNED_BEATS = 8.0, END_SLACK = 1.0
let ARCH_DB = 4.0, SWELL_DB = 3.0, SWELL_BEATS = 1.5, RANGE = 1.4, GRID = 0.25

/// dynamics.curve: breakpoints (beat, dB) of the part's marked loudness, starting with (-inf, 0).
func curve(_ part: MusicXMLPart) -> [(Double, Double)] {
    let marks = part.marks.compactMap { m in LEVELS_DB[m.text].map { (m.beat, $0) } }.sorted { ($0.0, $0.1) < ($1.0, $1.1) }
    let pins = part.hairpins.map { h -> (Double, Double, Int) in
        let e = h.end ?? h.start
        return (h.start, e > h.start ? e : h.start + UNSPANNED_BEATS, h.sign)
    }.sorted { ($0.0, $0.1, $0.2) < ($1.0, $1.1, $1.2) }
    // marks first at equal times (a stable sort of marks, then pins, by (beat, kind))
    let events = (marks.map { ($0.0, 0, $0.1, 0) } + pins.map { ($0.0, 1, $0.1, $0.2) })
        .enumerated().sorted { ($0.element.0, $0.element.1, $0.offset) < ($1.element.0, $1.element.1, $1.offset) }.map(\.element)
    var points: [(Double, Double)] = [(-.infinity, 0)]
    var marked = 0.0
    for (beat, kind, x, sign) in events {
        let cur = levelAtPy(points, beat)
        points = points.filter { $0.0 <= beat }
        if points.last!.0 < beat { points.append((beat, cur)) }
        if kind == 0 { points.append((beat, x)); marked = x; continue }
        let end = x, s = Double(sign)
        let near = marks.filter { abs($0.0 - end) <= END_SLACK && ($0.1 - cur) * s > 0 }.map(\.1)
        let target = near.first ?? min(marked + STEP_DB, LEVELS_DB["ff"]!, max(marked - STEP_DB, cur + s * STEP_DB))
        points.append((end, target))
    }
    return points
}

/// dynamics.level_at, -inf first point and all.
func levelAtPy(_ points: [(Double, Double)], _ beat: Double) -> Double {
    let i = upperBound(points.map(\.0), beat)
    if i == 0 { return points.first?.1 ?? 0 }
    let (b0, v0) = points[i - 1]
    if i == points.count || b0 == -.infinity { return v0 }
    let (b1, v1) = points[i]
    return b1 == b0 ? v0 : v0 + (v1 - v0) * (beat - b0) / (b1 - b0)
}

/// dynamics.phrases: notes split where a rest of at least PHRASE_GAP beats falls.
func phrases(_ notes: [ScoreNote]) -> [[ScoreNote]] {
    var out: [[ScoreNote]] = [], end = -Double.infinity
    for n in notes {
        if out.isEmpty || n.onsetBeat - end >= PHRASE_GAP { out.append([]) }
        out[out.count - 1].append(n)
        end = out[out.count - 1].count > 1 ? max(end, n.onsetBeat + n.durationBeat) : n.onsetBeat + n.durationBeat
    }
    return out
}

/// dynamics.shaped with dynamics.EXPRESSION: the marked curve widened, a phrase arch and note swells, as points
/// every GRID beats under the notes and at every marked point.
func shaped(_ points: [(Double, Double)], _ notes: [ScoreNote]) -> [(Double, Double)] {
    var arch: [(Double, Double)] = [], swells: [(Double, Double, Double)] = []
    for ph in phrases(notes) {
        let start = ph[0].onsetBeat
        let end = ph.map { $0.onsetBeat + $0.durationBeat }.max()!
        var top = ph[0]
        for n in ph.dropFirst() where (n.pitch, n.durationBeat, -n.onsetBeat) > (top.pitch, top.durationBeat, -top.onsetBeat) { top = n }
        let peak = top.onsetBeat
        if peak > start { arch += [(start, -0.4 * ARCH_DB), (peak, ARCH_DB)] } else { arch.append((start, ARCH_DB)) }
        if end > peak { arch.append((end, -0.6 * ARCH_DB)) }
        arch.append((end + 1e-6, 0))
        swells += ph.filter { $0.durationBeat >= SWELL_BEATS }.map { ($0.onsetBeat, $0.onsetBeat + $0.durationBeat, SWELL_DB) }
    }
    let archBeats = arch.map(\.0)
    func archAt(_ b: Double) -> Double {
        let i = upperBound(archBeats, b)
        if i == 0 || i == arch.count { return 0 }
        let (b0, v0) = arch[i - 1], (b1, v1) = arch[i]
        return b1 > b0 ? v0 + (v1 - v0) * (b - b0) / (b1 - b0) : v0
    }
    func swellAt(_ b: Double) -> Double {
        var best = 0.0
        for (s, e, d) in swells where s <= b && b <= e {
            let x = (b - s) / (e - s)
            best = max(best, x < 0.55 ? -1 + (d + 1) * x / 0.55 : d - (d + 1.5) * (x - 0.55) / 0.45)
        }
        return best
    }
    var beats = Set(points.map(\.0).filter { $0 != -.infinity })
    for n in notes {
        var k = 0.0
        while n.onsetBeat + k * GRID < n.onsetBeat + n.durationBeat {
            beats.insert(roundPy(n.onsetBeat + k * GRID, 6))
            k += 1
        }
        beats.insert(n.onsetBeat + n.durationBeat)
    }
    var marked: [Double: [Double]] = [:]
    for (b, v) in points { marked[b, default: []].append(v) }
    var out: [(Double, Double)] = []
    for b in beats.sorted() {
        let add = archAt(b) + swellAt(b)
        for v in marked[b] ?? [levelAtPy(points, b)] { out.append((b, v * RANGE + add)) }
    }
    return out
}

/// Python's round(x, n): to the nearest, ties to even, on the decimal value.
func roundPy(_ x: Double, _ n: Int) -> Double {
    let s = String(format: "%.\(n)f", x)  // C rounds the exact binary value, as Python does
    return Double(s) ?? x
}
