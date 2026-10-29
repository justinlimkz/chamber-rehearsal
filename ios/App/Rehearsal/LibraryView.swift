import RehearsalCore
import SwiftUI
import UniformTypeIdentifiers

// The first screen: the piano's connection, a Continue card for where you stopped last time, and the pieces
// as works with their movements under them, set like a concert programme. Pieces come from the Mac's
// `rehearsal bundle`, added from Files (or opened from AirDrop).

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
                                Text("Open a piece folder made by the Mac (“rehearsal bundle”) from Files, or AirDrop one.")
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
            .frame(maxWidth: 820, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Paper.color)
        .navigationTitle("Pieces")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { recordings = true } label: { Label("All recordings", systemImage: "waveform") }
                Button { settings = true } label: { Label("Settings", systemImage: "gearshape") }
                Button { importing = true } label: { Label("Add a piece", systemImage: "plus") }
            }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [Piece.type, .folder]) { result in
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

    /// A folder picked in Files (or opened from AirDrop), read in place to show what would be added.
    static func preview(_ url: URL, problem: inout String?) -> Piece? {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        // Copy it out first: the security-scoped access ends here, and the sheet reads it later.
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            .appendingPathComponent(url.deletingPathExtension().lastPathComponent)
        do {
            try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
            for f in Piece.files + Piece.pianoFiles where FileManager.default.fileExists(atPath: url.appendingPathComponent(f).path) {
                try FileManager.default.copyItem(at: url.appendingPathComponent(f), to: tmp.appendingPathComponent(f))
            }
            return try Piece(url: tmp, builtIn: false)
        } catch {
            problem = error.localizedDescription
            return nil
        }
    }

    private func continueCard(_ piece: Piece, _ last: LastVisit) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Continue · \(piece.composer)").font(.subheadline.weight(.semibold)).foregroundStyle(Paper.blue)
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
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 18).fill(.white))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Paper.line))
    }

    private func work(_ composer: String, _ work: String, _ movements: [Piece]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text(composer.uppercased()).font(.caption.weight(.semibold)).tracking(1.5).foregroundStyle(Paper.muted)
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
        .background(RoundedRectangle(cornerRadius: 18).fill(.white))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Paper.line))
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
                Section {
                    LabeledContent("You play", value: "Piano")
                    LabeledContent("The strings play", value: piece.strings.map { $0 == "Violoncello" ? "Cello" : $0 }.joined(separator: ", "))
                } footer: {
                    Text("The strings follow you. Their dynamics come from the score.")
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
