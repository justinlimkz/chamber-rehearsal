import SwiftUI

// Every visit to the rehearse screen is recorded (Recording.swift): piano, strings and cues as MIDI, with a
// summary of its passes alongside. This lists them by day, with Share to send one to the Mac (`rehearsal
// session` reads it there). Listening back in the app comes later.

struct RecordingItem: Identifiable {
    let url: URL
    let date: Date
    let piece: String?
    let title: String?
    let passes: [PassSummary]
    var id: URL { url }

    static func all() -> [RecordingItem] {
        let fm = FileManager.default
        guard let files = try? fm.contentsOfDirectory(at: Recording.folder, includingPropertiesForKeys: [.creationDateKey]) else { return [] }
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        return files.filter { $0.pathExtension == "mid" }.compactMap { url in
            let date = f.date(from: url.deletingPathExtension().lastPathComponent)
                ?? (try? url.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? .distantPast
            let side = url.deletingPathExtension().appendingPathExtension("json")
            let d = (try? Data(contentsOf: side)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            let passes = (d?["passes"] as? [[String: Any]] ?? []).compactMap(PassSummary.init)
            return RecordingItem(url: url, date: date, piece: d?["piece"] as? String, title: d?["title"] as? String, passes: passes)
        }.sorted { $0.date > $1.date }
    }

    static func count(_ piece: Piece) -> Int { all().filter { $0.piece == piece.id }.count }

    var bars: String? {
        guard let a = passes.map(\.firstBar).min(), let b = passes.map(\.lastBar).max() else { return nil }
        return a == b ? "bar \(a)" : "bars \(a)–\(b)"
    }

    var summary: String {
        var parts: [String] = []
        if passes.count > 1 { parts.append("\(passes.count) passes") }
        if let p = passes.last {
            parts.append(p.tempo)
            if let t = p.togetherText { parts.append(t) }
        }
        return parts.joined(separator: " · ")
    }
}

struct RecordingsView: View {
    let piece: Piece?  // nil: all recordings
    @Environment(\.dismiss) private var dismiss
    @State private var items: [RecordingItem] = []

    var body: some View {
        NavigationStack {
            List {
                if items.isEmpty {
                    Text("Nothing recorded yet. Every pass you play with the strings is kept here.")
                        .foregroundStyle(.secondary)
                }
                ForEach(days, id: \.0) { day, rows in
                    Section(day) {
                        ForEach(rows) { item in
                            HStack(alignment: .firstTextBaseline) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(item.date.formatted(date: .omitted, time: .shortened)
                                         + (item.bars.map { " · " + $0 } ?? ""))
                                        .font(.body.weight(.medium)).monospacedDigit()
                                    Text([piece == nil ? item.title : nil, item.summary.isEmpty ? nil : item.summary]
                                        .compactMap { $0 }.joined(separator: " · "))
                                        .font(.callout).foregroundStyle(.secondary)
                                }
                                Spacer()
                                ShareLink(item: item.url) { Image(systemName: "square.and.arrow.up") }
                                    .buttonStyle(.borderless)
                                    .accessibilityLabel("Share the MIDI file")
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }
            }
            .navigationTitle(piece.map { "Recordings · \($0.title)" } ?? "All recordings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .onAppear { items = RecordingItem.all().filter { piece == nil || $0.piece == piece!.id } }
        }
    }

    private var days: [(String, [RecordingItem])] {
        var out: [(String, [RecordingItem])] = []
        for item in items {
            let day = item.date.formatted(.dateTime.weekday(.wide).day().month(.wide))
            if out.last?.0 == day { out[out.count - 1].1.append(item) } else { out.append((day, [item])) }
        }
        return out
    }
}
