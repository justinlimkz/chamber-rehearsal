import Foundation
import RehearsalCore
import UniformTypeIdentifiers

// The pieces the app can rehearse: folders made on the Mac by `rehearsal bundle` (score.json, engraving.svg,
// anchors.json). The ones that come with the app live in its bundle; added ones are copied into
// Documents/Pieces (Files › On My iPad › Rehearsal › Pieces). The library lists works with their movements
// under them, from each bundle's composer, work and movement.

struct Piece: Identifiable, Hashable, Sendable {
    let id: String  // the folder's name
    let url: URL
    let composer: String
    let work: String
    let movement: String  // "" for a whole work
    let bars: Int
    let strings: [String]  // the parts the strings play, in score order
    let builtIn: Bool

    static let files = ["score.json", "engraving.svg", "anchors.json"]
    static let type = UTType(exportedAs: "kz.justinlim.rehearsal.piece")  // a bundle folder named *.rehearsal

    /// Read a bundle folder's description (not its notes: the session loads those).
    init(url: URL, builtIn: Bool) throws {
        for f in Self.files where !FileManager.default.fileExists(atPath: url.appendingPathComponent(f).path) {
            throw PieceError.missing(f)
        }
        let score = try ScoreData.load(path: url.appendingPathComponent("score.json").path)
        self.id = url.deletingPathExtension().lastPathComponent
        self.url = url
        self.builtIn = builtIn
        composer = score.composer
        work = score.work
        movement = score.movement
        bars = Set(score.barStarts.map { $0.1 }).count
        strings = STRING_PARTS.filter { score.partNames.contains($0) }
    }

    /// "Robert Schumann" -> "Schumann"
    var composerSurname: String { composer.split(separator: " ").last.map(String.init) ?? composer }
    var title: String { movement.isEmpty ? work : movement }
    var withInstruments: String {
        let names = strings.map { ["Violin": "violin", "Viola": "viola", "Violoncello": "cello"][$0] ?? $0.lowercased() }
        return names.isEmpty ? "" : "with " + ListFormatter.localizedString(byJoining: names)
    }
}

enum PieceError: LocalizedError {
    case missing(String)
    var errorDescription: String? {
        switch self {
        case .missing(let f): "This isn't a piece made by the Mac's “rehearsal bundle”: it has no \(f)."
        }
    }
}

/// Where the strings stopped last time in a piece, for “Continue”.
struct LastVisit: Codable, Equatable {
    var piece: String
    var bar: Int
    var date: Date
}

@MainActor @Observable
final class Library {
    private(set) var pieces: [Piece] = []
    private(set) var last: LastVisit?

    static let folder = URL.documentsDirectory.appendingPathComponent("Pieces", isDirectory: true)
    private static let lastKey = "lastVisit"

    init() {
        if let d = UserDefaults.standard.data(forKey: Self.lastKey) { last = try? JSONDecoder().decode(LastVisit.self, from: d) }
        reload()
    }

    func reload() {
        var found: [Piece] = []
        let fm = FileManager.default
        if let res = Bundle.main.resourceURL, let items = try? fm.contentsOfDirectory(at: res, includingPropertiesForKeys: nil) {
            found += items.compactMap { try? Piece(url: $0, builtIn: true) }
        }
        if let items = try? fm.contentsOfDirectory(at: Self.folder, includingPropertiesForKeys: nil) {
            found += items.compactMap { try? Piece(url: $0, builtIn: false) }
        }
        var seen = Set<String>()  // an added copy of a built-in piece replaces it
        pieces = found.reversed().filter { seen.insert($0.id).inserted }.sorted {
            ($0.composerSurname, $0.work, $0.movement) < ($1.composerSurname, $1.work, $1.movement)
        }
    }

    /// Works in the order listed, each with its movements.
    var works: [(composer: String, work: String, movements: [Piece])] {
        var out: [(composer: String, work: String, movements: [Piece])] = []
        for p in pieces {
            if let i = out.firstIndex(where: { $0.composer == p.composer && $0.work == p.work }) {
                out[i].movements.append(p)
            } else {
                out.append((p.composer, p.work, [p]))
            }
        }
        return out
    }

    func piece(_ id: String) -> Piece? { pieces.first { $0.id == id } }

    func remember(_ piece: Piece, bar: Int) {
        last = LastVisit(piece: piece.id, bar: bar, date: Date())
        UserDefaults.standard.set(try? JSONEncoder().encode(last), forKey: Self.lastKey)
    }

    /// Copy a bundle folder into Documents/Pieces (replacing an earlier copy of the same piece).
    func add(_ piece: Piece) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        let dest = Self.folder.appendingPathComponent(piece.id, isDirectory: true)
        if fm.fileExists(atPath: dest.path) { try fm.removeItem(at: dest) }
        try fm.createDirectory(at: dest, withIntermediateDirectories: true)
        for f in Piece.files {
            try fm.copyItem(at: piece.url.appendingPathComponent(f), to: dest.appendingPathComponent(f))
        }
        reload()
    }

    func remove(_ piece: Piece) throws {
        guard !piece.builtIn else { return }
        try FileManager.default.removeItem(at: piece.url)
        reload()
    }
}
