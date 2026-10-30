import RehearsalCore
import SwiftUI
import UniformTypeIdentifiers

// The first screen: the piano's connection, a Continue card for where you stopped last time, and the pieces
// as works with their movements under them, set like a concert programme. A piece is added from a MusicXML score
// (exported from MuseScore, Sibelius, Dorico and the like) or a folder made by the Mac's `rehearsal bundle`, from
// Files or AirDrop.

struct LibraryView: View {
    let library: Library
    let rig: Rig
    let open: (Piece, Int?) -> Void  // the piece, and the bar to start from (nil: the top)
    @Binding var adding: Piece?
    @State private var importing = false
    @State private var settings = false
    @State private var recordings = false
    @State private var problem: String?
    @State private var keyboard: String?
    @State private var counts: [String: Int] = [:]
    @State private var colors = WorkColors()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                HStack(spacing: 8) {
                    Circle().fill(keyboard == nil ? Paper.warn : .green).frame(width: 10, height: 10)
                    Text(keyboard.map { "\($0) · connected by USB" } ?? "Plug in the piano (USB)")
                        .foregroundStyle(keyboard == nil ? Paper.warn : Paper.muted)
                }
                .font(.callout)

                if let last = library.last, let piece = library.piece(last.piece) {
                    continueCard(piece, last)
                }

                VStack(alignment: .leading, spacing: 14) {
                    Text("All pieces").font(.title3.weight(.semibold))
                    ForEach(library.works, id: \.work) { w in
                        work(w.composer, w.work, w.movements)
                    }
                    Button { importing = true } label: {
                        HStack(alignment: .top, spacing: 14) {
                            Image(systemName: "plus.circle.fill").font(.title2).foregroundStyle(Paper.blue)
                            VStack(alignment: .leading, spacing: 3) {
                                Text("Add a piece").font(.body.weight(.semibold)).foregroundStyle(Paper.ink)
                                Text("Open a MusicXML score (.musicxml or .mxl, exported from MuseScore or another notation program) from Files, or AirDrop one. The strings play the violin, viola and cello parts; you play the piano.")
                                    .font(.callout).foregroundStyle(Paper.muted).multilineTextAlignment(.leading)
                            }
                            Spacer()
                        }
                        .padding(18)
                        .background(RoundedRectangle(cornerRadius: 16).strokeBorder(Paper.line, style: StrokeStyle(lineWidth: 1.5, dash: [6, 5])))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Paper.color)
        .navigationTitle("Pieces")
        .onAppear { colors.assign(library.works.map { ($0.composer, $0.work) }) }
        .onChange(of: library.pieces) { colors.assign(library.works.map { ($0.composer, $0.work) }) }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { recordings = true } label: { Label("All recordings", systemImage: "waveform") }
                Button { settings = true } label: { Label("Settings", systemImage: "gearshape") }
                Button { importing = true } label: { Label("Add a piece", systemImage: "plus") }
            }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: Piece.musicXML + [.xml, Piece.type, .folder]) { result in
            switch result {
            case .success(let url): adding = Self.preview(url, problem: &problem)
            case .failure(let e): problem = e.localizedDescription
            }
        }
        .sheet(isPresented: $settings) { SettingsView(rig: rig) }
        .sheet(isPresented: $recordings) { RecordingsView(piece: nil) }
        .sheet(item: $adding) { piece in
            AddPieceView(piece: piece) {
                do { try library.add(piece); adding = nil } catch { problem = error.localizedDescription }
            }
        }
        .alert("Couldn't add the piece", isPresented: .constant(problem != nil)) {
            Button("OK") { problem = nil }
        } message: { Text(problem ?? "") }
        .task {
            while !Task.isCancelled {  // the piano's status, and recordings made since
                keyboard = rig.keyboard.name
                counts = Dictionary(grouping: RecordingItem.all().compactMap(\.piece), by: { $0 }).mapValues(\.count)
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    /// A score or folder picked in Files (or opened from AirDrop), made into a piece in a temporary folder to show
    /// what would be added.
    static func preview(_ url: URL, problem: inout String?) -> Piece? {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        // Copy it out first: the security-scoped access ends here, and the sheet reads it later.
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let tmp = dir.appendingPathComponent(url.deletingPathExtension().lastPathComponent)
        if ["musicxml", "mxl", "xml"].contains(url.pathExtension.lowercased()) {
            do {
                try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                return try Piece.fromMusicXML(url, in: dir)
            } catch {
                problem = error.localizedDescription
                return nil
            }
        }
        do {
            try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
            for f in Piece.files where FileManager.default.fileExists(atPath: url.appendingPathComponent(f).path) {
                try FileManager.default.copyItem(at: url.appendingPathComponent(f), to: tmp.appendingPathComponent(f))
            }
            return try Piece(url: tmp, builtIn: false)
        } catch {
            problem = error.localizedDescription
            return nil
        }
    }

    private func continueCard(_ piece: Piece, _ last: LastVisit) -> some View {
        ViewThatFits(in: .horizontal) {  // the bars beside the words where there's room, above them where not
            HStack(alignment: .top, spacing: 24) {
                continuePreview(piece, last).frame(width: 340)
                continueText(piece, last).frame(minWidth: 380, alignment: .leading)
            }
            VStack(alignment: .leading, spacing: 18) {
                continuePreview(piece, last)
                continueText(piece, last)
            }
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workCard(colors.color(piece.composer, piece.work), tinted: true)
    }

    /// The bars you'll continue from, the first of them shaded.
    private func continuePreview(_ piece: Piece, _ last: LastVisit) -> some View {
        ScorePreview(piece: piece, first: last.bar, mark: last.bar)
            .padding(8)
            .background(RoundedRectangle(cornerRadius: 12).fill(.white))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Paper.line))
    }

    private func continueText(_ piece: Piece, _ last: LastVisit) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Continue · \(piece.composer)".uppercased()).font(.caption.weight(.semibold)).tracking(1.5)
                .foregroundStyle(colors.color(piece.composer, piece.work))
            Text(piece.work).font(.system(.title2, design: .serif))
            if !piece.movement.isEmpty {
                Text(piece.movement).font(.system(.title3, design: .serif)).italic()
            }
            Text("Stopped at bar \(last.bar) · \(last.date.formatted(.relative(presentation: .named)))")
                .font(.callout).foregroundStyle(Paper.muted)
            HStack(spacing: 12) {
                Button { open(piece, last.bar) } label: { Text("Continue from bar \(last.bar)") }
                    .buttonStyle(.glassProminent)
                Button { open(piece, nil) } label: { Text("From the top") }
                    .buttonStyle(.glass)
            }
            .controlSize(.large)
            .padding(.top, 4)
        }
    }

    private func work(_ composer: String, _ work: String, _ movements: [Piece]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text(composer.uppercased()).font(.caption.weight(.semibold)).tracking(1.5).foregroundStyle(colors.color(composer, work))
                Text(work).font(.system(.title2, design: .serif))
                if let with = movements.first?.withInstruments, !with.isEmpty {
                    Text(with).font(.callout).foregroundStyle(Paper.muted)
                }
            }
            .padding(.bottom, 10)
            ForEach(movements) { m in
                Divider()
                Button { open(m, nil) } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(m.movement.isEmpty ? "Whole work" : m.movement)
                                .font(.system(.title3, design: .serif)).italic().foregroundStyle(Paper.ink)
                            Text(details(m)).font(.callout).foregroundStyle(Paper.muted)
                        }
                        Spacer()
                        Image(systemName: "chevron.right").foregroundStyle(Paper.muted)
                    }
                    .padding(.vertical, 12)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .contextMenu {
                    if !m.builtIn {
                        Button(role: .destructive) { try? library.remove(m) } label: { Label("Remove from the iPad", systemImage: "trash") }
                    }
                }
            }
        }
        .padding(22)
        .workCard(colors.color(composer, work), tinted: false)
        .contextMenu {
            Picker("Colour", selection: Binding(get: { colors.index(composer, work) }, set: { colors.set(composer, work, $0) })) {
                ForEach(WorkColors.palette.indices, id: \.self) { i in
                    Label(WorkColors.palette[i].name, systemImage: "circle.fill").tint(WorkColors.palette[i].color).tag(i)
                }
            }
            .pickerStyle(.menu)
        }
    }

    private func details(_ p: Piece) -> String {
        var parts = ["\(p.bars) bars"]
        if let n = counts[p.id], n > 0 { parts.append(n == 1 ? "1 recording" : "\(n) recordings") }
        if let last = library.last, last.piece == p.id { parts.append("stopped at bar \(last.bar)") }
        return parts.joined(separator: " · ")
    }
}

/// Confirm before adding: what the piece is and who plays what. Nothing to fill in.
struct AddPieceView: View {
    let piece: Piece
    let add: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(piece.composer.uppercased()).font(.caption.weight(.semibold)).tracking(1.5).foregroundStyle(Paper.muted)
                        Text(piece.work).font(.system(.title2, design: .serif))
                        if !piece.movement.isEmpty {
                            Text(piece.movement).font(.system(.title3, design: .serif)).italic()
                        }
                        Text("\(piece.bars) bars").font(.callout).foregroundStyle(Paper.muted)
                    }
                    .padding(.vertical, 6)
                }
                Section { ScorePreview(piece: piece, first: 1).padding(.vertical, 6) }
                Section {
                    LabeledContent("You play", value: "Piano")
                    LabeledContent("The strings play", value: piece.strings.map { $0 == "Violoncello" ? "Cello" : $0 }.joined(separator: ", "))
                } footer: {
                    Text(["The strings follow you. Their dynamics come from the score.", piece.notice].compactMap { $0 }.joined(separator: " "))
                }
            }
            .navigationTitle("Add a piece")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("Add") { add() }.bold() }
            }
        }
    }
}

/// Each work's colour: the spine of its cards and its composer's name. Picked from a few deep, muted colours (none
/// near the blue that means "the strings are with you" or the amber of a restart) when a work first appears in
/// the library, the next one not yet used, and kept; a long press on the work changes it.
@MainActor @Observable
final class WorkColors {
    static let palette: [(name: String, color: Color)] = [
        ("Claret", Color(red: 0x7A / 255, green: 0x2E / 255, blue: 0x3B / 255)),
        ("Forest", Color(red: 0x2F / 255, green: 0x5A / 255, blue: 0x4A / 255)),
        ("Plum", Color(red: 0x4B / 255, green: 0x3A / 255, blue: 0x68 / 255)),
        ("Umber", Color(red: 0x6B / 255, green: 0x4A / 255, blue: 0x2E / 255)),
        ("Slate", Color(red: 0x3B / 255, green: 0x4F / 255, blue: 0x5C / 255)),
        ("Mulberry", Color(red: 0x6E / 255, green: 0x2F / 255, blue: 0x55 / 255)),
        ("Moss", Color(red: 0x4F / 255, green: 0x5B / 255, blue: 0x2A / 255)),
        ("Pine", Color(red: 0x23 / 255, green: 0x58 / 255, blue: 0x5A / 255)),
    ]
    private static let key = "workColors"
    private var chosen: [String: Int] = UserDefaults.standard.dictionary(forKey: key) as? [String: Int] ?? [:]

    private func id(_ composer: String, _ work: String) -> String { composer + "\u{1F}" + work }

    /// Give every work that has none the next colour (the least used, in palette order).
    func assign(_ works: [(String, String)]) {
        var changed = false
        for (c, w) in works where chosen[id(c, w)] == nil {
            let uses = Self.palette.indices.map { i in chosen.values.filter { $0 == i }.count }
            chosen[id(c, w)] = uses.indices.min { uses[$0] < uses[$1] }!
            changed = true
        }
        if changed { UserDefaults.standard.set(chosen, forKey: Self.key) }
    }

    func index(_ composer: String, _ work: String) -> Int { chosen[id(composer, work)] ?? 0 }
    func color(_ composer: String, _ work: String) -> Color { Self.palette[index(composer, work) % Self.palette.count].color }

    func set(_ composer: String, _ work: String, _ i: Int) {
        chosen[id(composer, work)] = i
        UserDefaults.standard.set(chosen, forKey: Self.key)
    }
}

extension View {
    /// A library card: white (or faintly tinted with the work's colour), with the colour as a spine down its left edge.
    func workCard(_ color: Color, tinted: Bool) -> some View {
        self
            .padding(.leading, 8)
            .background(alignment: .leading) {
                ZStack(alignment: .leading) {
                    Rectangle().fill(.white)
                    if tinted { Rectangle().fill(color.opacity(0.07)) }
                    Rectangle().fill(color).frame(width: 8)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Paper.line))
    }
}
