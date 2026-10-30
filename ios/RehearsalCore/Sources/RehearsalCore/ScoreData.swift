import Foundation

// Stand-in for rehearsal/score.py: only what the engine needs (bar starts, bar_at, notes per part),
// read from the JSON bundle (score.json) instead of MusicXML.

/// A note of the score (score.ScoreNote, reduced): onset and duration in quarter-note beats.
public struct ScoreNote: Equatable, Sendable {
    public var onsetBeat: Double
    public var durationBeat: Double
    public var pitch: Int
}

public struct ScoreData: Sendable, Decodable {
    public var title: String
    public var composer: String
    public var work: String  // for the library: the work, with its movements under it
    public var movement: String  // "" when the piece is a whole work, or not known
    /// (quarter-note position, written bar number) for every bar of the unfolded piano part, in order.
    public var barStarts: [(Double, Int)]
    /// Each string part's loudness from the score's dynamics (dynamics.curves): (beat, dB from mf) points.
    public var dynamics: [String: [(Double, Double)]]
    private var parts: [String: [ScoreNote]]

    private enum CodingKeys: String, CodingKey { case title, composer, work, movement, barStarts, parts, dynamics }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decode(String.self, forKey: .title)
        composer = try c.decode(String.self, forKey: .composer)
        work = try c.decodeIfPresent(String.self, forKey: .work) ?? title
        movement = try c.decodeIfPresent(String.self, forKey: .movement) ?? ""
        var bs = try c.nestedUnkeyedContainer(forKey: .barStarts)
        var starts: [(Double, Int)] = []
        while !bs.isAtEnd {
            var p = try bs.nestedUnkeyedContainer()
            starts.append((try p.decode(Double.self), try p.decode(Int.self)))
        }
        barStarts = starts
        let dyn = try c.decodeIfPresent([String: [[Double]]].self, forKey: .dynamics) ?? [:]
        dynamics = dyn.mapValues { rows in rows.map { ($0[0], $0[1]) } }
        let raw = try c.decode([String: [[Double]]].self, forKey: .parts)
        parts = raw.mapValues { rows in
            rows.map { ScoreNote(onsetBeat: $0[0], durationBeat: $0[1], pitch: Int($0[2])) }
        }
    }

    public static func load(path: String) throws -> ScoreData {
        try JSONDecoder().decode(ScoreData.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
    }

    /// Notes of a part, sorted by (onset, pitch) as in score.notes(name).
    public func notes(_ name: String) -> [ScoreNote] { parts[name] ?? [] }

    /// The parts in the score, sorted (string parts and the piano).
    public var partNames: [String] { parts.keys.sorted() }

    /// Map an absolute (unfolded) beat position to (written bar, 1-based beat in bar).
    public func barAt(_ beat: Double) -> (bar: Int, beatInBar: Double) {
        // searchsorted(side="right") - 1
        let i = max(0, upperBound(barStarts.map { $0.0 }, beat) - 1)
        return (barStarts[i].1, beat - barStarts[i].0 + 1)
    }
}

/// bisect.bisect_right: index after the last element <= x.
func upperBound(_ a: [Double], _ x: Double) -> Int {
    var lo = 0, hi = a.count
    while lo < hi {
        let mid = (lo + hi) / 2
        if x < a[mid] { hi = mid } else { lo = mid + 1 }
    }
    return lo
}

/// Python's format spec `:g` (6 significant digits, trailing zeros removed).
func fmtG(_ x: Double) -> String { String(format: "%g", x) }
