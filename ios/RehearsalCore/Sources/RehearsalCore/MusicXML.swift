import Foundation

// A MusicXML score read on the iPad, so a piece can be added without the Mac: what rehearsal/musicxml.py reads on
// the Mac (each part's notes in quarter-note beats with ties joined and repeats played out, the bars in playing
// order) and the dynamics rehearsal/dynamics.py reads (marked levels, wedges, "cresc." and "dim." in words).
// Checked against the Mac's own reading of the same files: `swift run --package-path ios/RehearsalCore import-check`.
//
// As on the Mac (and as partitura, which the Mac used before): a direction sits where it is written among the notes (its <offset> is ignored); a
// grace note starts with the note it ornaments and lasts nothing; a short first bar is a pickup, so the first
// full bar starts at beat 0; every repeat is taken, with first and second endings (D.C. and D.S. are not followed).

/// An XML element, enough of one to walk a score.
final class XMLNode {
    let name: String
    let attrs: [String: String]
    var children: [XMLNode] = []
    var text = ""
    init(_ name: String, _ attrs: [String: String]) { self.name = name; self.attrs = attrs }

    func child(_ n: String) -> XMLNode? { children.first { $0.name == n } }
    func all(_ n: String) -> [XMLNode] { children.filter { $0.name == n } }
    func has(_ n: String) -> Bool { child(n) != nil }
    /// The trimmed text of a child, e.g. `value("duration")`.
    func value(_ n: String) -> String? { child(n).map { $0.text.trimmingCharacters(in: .whitespacesAndNewlines) } }

    static func parse(_ data: Data) throws -> XMLNode {
        let b = Builder()
        let p = XMLParser(data: data)
        p.shouldResolveExternalEntities = false
        p.delegate = b
        guard p.parse(), let root = b.root else {
            throw MusicXMLError.unreadable(p.parserError?.localizedDescription ?? "not XML")
        }
        return root
    }

    private final class Builder: NSObject, XMLParserDelegate {
        var stack: [XMLNode] = []
        var root: XMLNode?
        func parser(_ p: XMLParser, didStartElement e: String, namespaceURI: String?, qualifiedName: String?,
                    attributes: [String: String] = [:]) {
            let n = XMLNode(e, attributes)
            stack.last?.children.append(n)
            if root == nil { root = n }
            stack.append(n)
        }
        func parser(_ p: XMLParser, didEndElement e: String, namespaceURI: String?, qualifiedName: String?) { stack.removeLast() }
        func parser(_ p: XMLParser, foundCharacters s: String) { stack.last?.text += s }
    }
}

public enum MusicXMLError: LocalizedError, Equatable {
    case unreadable(String)
    case notPartwise
    case noParts
    case noPiano
    case twoParts(String)

    public var errorDescription: String? {
        switch self {
        case .unreadable(let why): "The file couldn't be read as MusicXML (\(why))."
        case .notPartwise: "This MusicXML is laid out time-wise; export it again from your notation program (the usual “MusicXML” setting is part-wise)."
        case .noParts: "This MusicXML has no parts the app can play."
        case .noPiano: "The app follows a piano part, and this score has none (or it isn't named “Piano”)."
        case .twoParts(let what): "This score has two \(what) parts; the app plays one of each string instrument."
        }
    }
}

/// One part, played out: its notes and loudness markings in quarter-note beats from the first full bar.
public struct MusicXMLPart: Sendable {
    public let id: String
    public let name: String  // as written: part-name, else part-abbreviation, else instrument-name
    public let names: [String]  // every name the score gives it, for telling the instrument
    public var notes: [ScoreNote] = []  // ties joined; grace notes last 0; sorted by (onset, pitch)
    public var bars: [(beat: Double, number: Int)] = []  // every bar in playing order, by its printed number
    public var marks: [(beat: Double, text: String)] = []  // dynamics (pp ... ff and any others), lower case
    public var hairpins: [(start: Double, end: Double?, sign: Int)] = []  // end nil: "cresc." with no span
}

public struct MusicXMLScore: Sendable {
    public var title: String  // the movement's title, else the work's, else the first title credit
    public var work: String?
    public var movement: String?
    public var composer: String
    public var tempo: Double?  // quarter notes a minute at the start, from the first part's first <sound tempo>
    public var parts: [MusicXMLPart]

    /// The MusicXML text of `data`: a .musicxml/.xml file as it is, or the score inside a compressed .mxl.
    public static func text(_ data: Data) throws -> Data {
        guard data.starts(with: [0x50, 0x4B, 0x03, 0x04]) else { return data }  // "PK": a zip
        let zip = try Zip(data)
        if let c = zip.file("META-INF/container.xml"), let root = try? XMLNode.parse(c),
           let path = root.child("rootfiles")?.child("rootfile")?.attrs["full-path"], let d = zip.file(path) {
            return d
        }
        guard let name = zip.names.first(where: { !$0.hasPrefix("META-INF") && ($0.hasSuffix(".xml") || $0.hasSuffix(".musicxml")) }),
              let d = zip.file(name) else { throw MusicXMLError.unreadable("no score in the .mxl") }
        return d
    }

    static func firstTempo(_ node: XMLNode) -> Double? {
        if node.name == "sound", let t = node.attrs["tempo"].flatMap(Double.init), t > 0 { return t }
        for c in node.children { if let t = firstTempo(c) { return t } }
        return nil
    }

    public init(data: Data) throws {
        let root = try XMLNode.parse(try Self.text(data))
        guard root.name == "score-partwise" else { throw MusicXMLError.notPartwise }
        let workTitle = root.child("work")?.value("work-title").flatMap { $0.isEmpty ? nil : $0 }
        let movementTitle = root.value("movement-title").flatMap { $0.isEmpty ? nil : $0 }
        let credit = root.all("credit").first { $0.value("credit-type") == "title" }?.value("credit-words")
        work = workTitle
        movement = movementTitle
        title = movementTitle ?? workTitle ?? credit ?? ""
        composer = root.child("identification")?.all("creator").first { $0.attrs["type"] == "composer" }?
            .text.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        var info: [String: [String]] = [:]
        for sp in root.child("part-list")?.all("score-part") ?? [] {
            guard let id = sp.attrs["id"] else { continue }
            let names: [String?] = [sp.value("part-name"), sp.value("part-abbreviation")]
                + sp.all("score-instrument").map { $0.value("instrument-name") }
            info[id] = names.compactMap { $0 }.filter { !$0.isEmpty }
        }
        let partNodes = root.all("part")
        guard !partNodes.isEmpty else { throw MusicXMLError.noParts }
        tempo = Self.firstTempo(partNodes[0])
        let order = Self.playingOrder(partNodes)
        parts = partNodes.map { node in
            let id = node.attrs["id"] ?? ""
            let names = info[id] ?? []
            var part = MusicXMLPart(id: id, name: names.first ?? id, names: names)
            Self.read(node, order: order, into: &part)
            return part
        }
    }

    // MARK: repeats

    /// The bars in the order they're played: every repeat taken (twice, or as many times as it says), each
    /// ending on the pass its number names. Read from the barlines of every part (a repeat in any part counts).
    static func playingOrder(_ parts: [XMLNode]) -> [Int] {
        let n = parts.map { $0.all("measure").count }.max() ?? 0
        var forward = Array(repeating: false, count: n), backward = Array(repeating: 0, count: n)
        var endingStart: [[Int]?] = Array(repeating: nil, count: n), endingStop = Array(repeating: false, count: n)
        for part in parts {
            for (i, m) in part.all("measure").enumerated() {
                for b in m.all("barline") {
                    if let r = b.child("repeat") {
                        if r.attrs["direction"] == "forward" { forward[i] = true }
                        if r.attrs["direction"] == "backward" { backward[i] = max(backward[i], Int(r.attrs["times"] ?? "") ?? 2) }
                    }
                    if let e = b.child("ending") {
                        let nums = (e.attrs["number"] ?? "").split(whereSeparator: { ",; ".contains($0) }).compactMap { Int($0) }
                        if e.attrs["type"] == "start" { endingStart[i] = nums }
                        if e.attrs["type"] == "stop" || e.attrs["type"] == "discontinue" { endingStop[i] = true }
                    }
                }
            }
        }
        var ending: [[Int]?] = Array(repeating: nil, count: n)  // the ending each bar belongs to
        var current: [Int]?
        for i in 0..<n {
            if let s = endingStart[i] { current = s }
            ending[i] = current
            if endingStop[i] { current = nil }
        }
        var order: [Int] = []
        var i = 0, start = 0, pass = 1, jumped = false
        while i < n && order.count < 100_000 {
            if forward[i] && !(jumped && i == start) { start = i; pass = 1 }
            jumped = false
            if let e = ending[i], !e.contains(pass) { i += 1; continue }
            order.append(i)
            if backward[i] > 0 {
                if pass < backward[i] { pass += 1; i = start; jumped = true; continue }
                start = i + 1; pass = 1
            }
            i += 1
        }
        return order
    }

    // MARK: a part

    private struct Bar {
        var length = 0.0  // quarters
        var nominal = 0.0  // the time signature's length
        var number = 0
        var notes: [(on: Double, dur: Double, pitch: Int, tieStart: Bool, tieStop: Bool, grace: Bool)] = []
        var marks: [(Double, String)] = []
        var wedges: [(Double, String, String)] = []  // (position, type, number)
        var words: [(Double, Int)] = []  // "cresc." (+1) or "dim." (-1)
        var dashes: [(Double, String, String)] = []  // (position, type, number): the line after a "cresc."
    }

    private static func read(_ node: XMLNode, order: [Int], into part: inout MusicXMLPart) {
        var divisions = 1.0, timeLength = 4.0
        var bars: [Bar] = []
        for (index, m) in node.all("measure").enumerated() {
            var bar = Bar()
            let label = (m.attrs["number"] ?? "").trimmingCharacters(in: .whitespaces)
            bar.number = Int(label) ?? (index + 1)
            var pos = 0.0, last = 0.0, end = 0.0
            for e in m.children {
                switch e.name {
                case "attributes":
                    if let d = e.value("divisions").flatMap(Double.init), d > 0 { divisions = d }
                    if let t = e.child("time"), let b = t.value("beats"), let bt = t.value("beat-type").flatMap(Double.init) {
                        let beats = b.split(separator: "+").compactMap { Double($0) }.reduce(0, +)
                        if beats > 0 && bt > 0 { timeLength = beats * 4 / bt }
                    }
                case "backup":
                    pos -= (e.value("duration").flatMap(Double.init) ?? 0) / divisions
                case "forward":
                    pos += (e.value("duration").flatMap(Double.init) ?? 0) / divisions
                case "note":
                    let grace = e.has("grace")
                    let dur = grace ? 0 : (e.value("duration").flatMap(Double.init) ?? 0) / divisions
                    let on = e.has("chord") ? last : pos
                    if !e.has("chord") { last = pos; pos += dur }
                    if let p = e.child("pitch"), !e.has("cue"),
                       let step = p.value("step"), let octave = p.value("octave").flatMap(Int.init),
                       let s = ["C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11][step] {
                        let alter = Int((p.value("alter").flatMap(Double.init) ?? 0).rounded())
                        let ties = e.all("tie").map { $0.attrs["type"] ?? "" }
                        bar.notes.append((on, dur, (octave + 1) * 12 + s + alter, ties.contains("start"), ties.contains("stop"), grace))
                    }
                case "direction":
                    for dt in e.all("direction-type") {
                        if let d = dt.child("dynamics") {
                            for k in d.children {
                                let text = k.name == "other-dynamics" ? k.text : k.name
                                bar.marks.append((pos, text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()))
                            }
                        }
                        if let w = dt.child("wedge") {
                            bar.wedges.append((pos, w.attrs["type"] ?? "", w.attrs["number"] ?? "1"))
                        }
                        for w in dt.all("words") {
                            if let sign = Self.hairpinWord(w.text) { bar.words.append((pos, sign)) }
                        }
                        for d in dt.all("dashes") { bar.dashes.append((pos, d.attrs["type"] ?? "", d.attrs["number"] ?? "1")) }
                    }
                default:
                    break
                }
                end = max(end, pos)
            }
            bar.length = end
            bar.nominal = timeLength
            bars.append(bar)
        }
        guard !bars.isEmpty else { return }
        // a short first bar is a pickup: the first full bar starts at beat 0
        var t = bars[0].length < bars[0].nominal - 1e-9 ? -bars[0].length : 0
        var open: [Int: Int] = [:]  // pitch -> index in part.notes of a note whose tie is still open
        var wedges: [String: (Double, Int)] = [:]  // number -> (start, sign)
        var words: [String: (Double, Int)] = [:]  // a "cresc." waiting for the end of its dashes
        for i in order where i < bars.count {
            let bar = bars[i]
            part.bars.append((t, bar.number))
            for (_, n) in bar.notes.enumerated().sorted(by: { ($0.element.on, $0.offset) < ($1.element.on, $1.offset) }) {
                let on = t + n.on
                if n.tieStop, let k = open[n.pitch], abs(part.notes[k].onsetBeat + part.notes[k].durationBeat - on) < 1e-6 {
                    part.notes[k].durationBeat += n.dur
                    if !n.tieStart { open[n.pitch] = nil }
                    continue
                }
                part.notes.append(ScoreNote(onsetBeat: on, durationBeat: n.dur, pitch: n.pitch))
                if n.tieStart { open[n.pitch] = part.notes.count - 1 } else { open[n.pitch] = nil }
            }
            for (p, text) in bar.marks { part.marks.append((t + p, text)) }
            // "cresc." with dashes ends where they do; without, it has no span
            let dashed = bar.dashes.filter { $0.1 == "start" }
            for (p, sign) in bar.words {
                if let d = dashed.first(where: { $0.0 == p }) { words[d.2] = (t + p, sign) } else { part.hairpins.append((t + p, nil, sign)) }
            }
            for (p, type, number) in bar.dashes where type == "stop" {
                if let (s, sign) = words.removeValue(forKey: number) { part.hairpins.append((s, t + p, sign)) }
            }
            for (p, type, number) in bar.wedges {
                if type == "crescendo" || type == "diminuendo" {
                    wedges[number] = (t + p, type == "crescendo" ? 1 : -1)
                } else if type == "stop", let (s, sign) = wedges.removeValue(forKey: number) {
                    part.hairpins.append((s, t + p, sign))
                }
            }
            t += bar.length
        }
        for (s, sign) in Array(wedges.values) + Array(words.values) { part.hairpins.append((s, nil, sign)) }  // never stopped
        part.notes.sort { ($0.onsetBeat, $0.pitch) < ($1.onsetBeat, $1.pitch) }
        part.hairpins.sort { ($0.start, $0.sign) < ($1.start, $1.sign) }
    }

    /// +1 for "cresc." (and "crescendo", "poco a poco cresc." ...), -1 for "dim.", "decresc." and the like.
    static func hairpinWord(_ text: String) -> Int? {
        let words = text.lowercased().split(whereSeparator: { !$0.isLetter }).map(String.init)
        if words.contains(where: { $0.hasPrefix("cresc") }) { return 1 }
        if words.contains(where: { $0.hasPrefix("dim") || $0.hasPrefix("decresc") }) { return -1 }
        return nil
    }
}
