import Foundation
import RehearsalCore
import UniformTypeIdentifiers

// The pieces the app can rehearse: folders holding score.json (the strings' notes and dynamics) and
// score.musicxml (the page to engrave), made here from a MusicXML file (`fromMusicXML`) or on the Mac by
// `rehearsal bundle` (older bundles have the Mac's engraving.svg and anchors.json instead). The ones that come with
// the app live in its bundle; added ones are copied into
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
    var notice: String? = nil  // shown when adding it, e.g. parts the app won't play

    static let files = ["score.json", "score.musicxml", "engraving.svg", "anchors.json"]  // the ones there are
    static let type = UTType(exportedAs: "kz.justinlim.rehearsal.piece")  // a bundle folder named *.rehearsal
    static let musicXML = [UTType(importedAs: "org.musicxml.musicxml"), UTType(importedAs: "org.musicxml.compressed")]

    /// A piece made from a MusicXML score (.musicxml, .mxl, or .xml), in a new folder under `dir`.
    static func fromMusicXML(_ url: URL, in dir: URL) throws -> Piece {
        let data = try Data(contentsOf: url)
        let imp = try ScoreImport(data: data)
        let title = [imp.score.composer.split(separator: " ").last.map(String.init) ?? "", imp.score.work ?? "", imp.score.movement ?? ""]
            .joined(separator: " ")
        let slug = slugged(title.trimmingCharacters(in: .whitespaces).isEmpty ? url.deletingPathExtension().lastPathComponent : title)
        let folder = dir.appendingPathComponent(slug, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try JSONSerialization.data(withJSONObject: imp.json()).write(to: folder.appendingPathComponent("score.json"))
        let xml = String(decoding: try MusicXMLScore.text(data), as: UTF8.self)
        try Data(imp.musicxml(xml).utf8).write(to: folder.appendingPathComponent("score.musicxml"))
        var piece = try Piece(url: folder, builtIn: false)
        if !imp.ignored.isEmpty {
            piece.notice = "Not played (the app plays violin, viola and cello): " + ListFormatter.localizedString(byJoining: imp.ignored) + "."
        }
        return piece
    }

    /// "Schumann Piano Quartet, Op. 47" -> "schumann-piano-quartet-op-47"
    static func slugged(_ s: String) -> String {
        let words = s.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: nil).lowercased()
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
        return words.isEmpty ? "piece" : words.joined(separator: "-")
    }

    /// Read a bundle folder's description (not its notes: the session loads those).
    init(url: URL, builtIn: Bool) throws {
        let has = { FileManager.default.fileExists(atPath: url.appendingPathComponent($0).path) }
        if !has("score.json") { throw PieceError.missing("score.json") }
        if !has("score.musicxml") && !(has("engraving.svg") && has("anchors.json")) { throw PieceError.missing("score.musicxml") }
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

    /// The score engraved on the iPad (any parts, any size), not the Mac's fixed engraving.
    var engravable: Bool { FileManager.default.fileExists(atPath: url.appendingPathComponent("score.musicxml").path) }

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
        case .missing(let f): "This isn't a piece the app can open: it has no \(f). Add a MusicXML score (.musicxml or .mxl), or a piece made by the Mac's “rehearsal bundle”."
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
        for f in Piece.files where fm.fileExists(atPath: piece.url.appendingPathComponent(f).path) {
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
