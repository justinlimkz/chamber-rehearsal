import RehearsalCore
import SwiftUI

// The rehearse screen: the score, full screen, under one slim status strip (bar, state, tempo). While you
// play there is nothing to touch. When the strings stop, a Liquid Glass toolbar comes in at the bottom:
// back to the pieces, Again, From the top, Bar…, pause, the strings' mix and tempo, and recordings.

struct RehearseView: View {
    let session: Session
    let onClose: () -> Void
    @State private var live: Live
    @State private var picking = false
    @State private var mixing = false
    @State private var listing = false
    @State private var look = ScoreLook.saved
    @State private var styling = false

    init(session: Session, onClose: @escaping () -> Void) {
        self.session = session
        self.onClose = onClose
        _live = State(initialValue: Live(session: session))
    }

    var body: some View {
        VStack(spacing: 0) {
            StatusStrip(live: live)
            ScoreView(session: session, look: look)
                .ignoresSafeArea(edges: .bottom)
        }
        .background(Paper.color)
        .overlay(alignment: .bottom) {
            if live.stopped {
                controls
                    .padding(.bottom, 18)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }
        }
        .animation(.easeInOut(duration: 0.35), value: live.stopped)
        .toolbar(.hidden, for: .navigationBar)
        .onDisappear { live.stop() }
        .task {  // Simulator screenshots: REHEARSAL_SHOW=bar|strings|recordings|score opens that panel
            guard Rig.silent else { return }
            try? await Task.sleep(for: .seconds(2))
            switch ProcessInfo.processInfo.environment["REHEARSAL_SHOW"] {
            case "bar": picking = true
            case "strings": mixing = true
            case "recordings": listing = true
            case "score": styling = true
            default: break
            }
        }
        .sheet(isPresented: $listing) { RecordingsView(piece: session.piece) }
    }

    private var controls: some View {
        VStack(spacing: 10) {
            if let pass = live.lastPass, live.state != "ready" {
                Text("Last pass  ·  " + pass.line)
                    .font(.callout).monospacedDigit()
                    .foregroundStyle(Paper.muted)
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .glassEffect(.regular, in: .capsule)
            }
            ViewThatFits(in: .horizontal) {  // on a smaller iPad, icons stand in for some of the words
                buttons(compact: false)
                buttons(compact: false, short: true)
                buttons(compact: true)
            }
        }
    }

    private func buttons(compact: Bool, short: Bool = false) -> some View {
        GlassEffectContainer(spacing: 12) {
            HStack(spacing: 12) {
                Button { onClose() } label: { Label("Pieces", systemImage: "chevron.backward") }
                    .buttonStyle(.glass)
                    .labelStyle(compact ? AnyLabelStyle(.iconOnly) : AnyLabelStyle(.titleAndIcon))
                Button { session.cue("again") } label: { Text("Again").frame(minWidth: 70) }
                    .buttonStyle(.glassProminent)
                Button { session.cue("top") } label: { Text(compact ? "Top" : "From the top") }
                    .buttonStyle(.glass)
                Button { picking = true } label: { Text("Bar…") }
                    .buttonStyle(.glass)
                    .popover(isPresented: $picking) { BarPicker(session: session, live: live) { picking = false } }
                Button { session.cue(live.state == "paused" ? "resume" : "pause") } label: {
                    Label(live.state == "paused" ? (short ? "Resume" : "Resume strings") : (short ? "Pause" : "Pause strings"),
                          systemImage: live.state == "paused" ? "play.fill" : "pause.fill")
                }
                .buttonStyle(.glass)
                .labelStyle(compact ? AnyLabelStyle(.iconOnly) : AnyLabelStyle(.titleAndIcon))
                .disabled(live.state == "finished")
                Button { styling = true } label: { Label("Score", systemImage: "music.note.list") }
                    .buttonStyle(.glass)
                    .labelStyle(compact ? AnyLabelStyle(.iconOnly) : AnyLabelStyle(.titleAndIcon))
                    .popover(isPresented: $styling) { ScorePanel(parts: session.score.partNames, look: $look) }
                Button { mixing = true } label: { Image(systemName: "slider.horizontal.3").accessibilityLabel("Strings and tempo") }
                    .buttonStyle(.glass)
                    .popover(isPresented: $mixing) { StringsPanel(session: session, live: live) }
                Button { listing = true } label: { Image(systemName: "waveform").accessibilityLabel("Recordings") }
                    .buttonStyle(.glass)
            }
            .lineLimit(1)
            .fixedSize()
            .controlSize(.extraLarge)
            .font(.title3.weight(.medium))
        }
    }
}

/// Either label style, chosen at run time.
struct AnyLabelStyle: LabelStyle {
    private let make: (Configuration) -> AnyView
    init<S: LabelStyle>(_ style: S) { make = { AnyView(style.makeBody(configuration: $0)) } }
    func makeBody(configuration: Configuration) -> some View { make(configuration) }
}

/// Bar on the left, the state in the middle, the tempo on the right, at a size readable from the bench.
struct StatusStrip: View {
    let live: Live

    var body: some View {
        HStack(alignment: .center, spacing: 20) {
            VStack(alignment: .leading, spacing: 2) {
                Text(barCaption).font(.subheadline.weight(.semibold)).foregroundStyle(barColor)
                Text(live.bar.map(String.init) ?? "–")
                    .font(.system(size: 48, weight: .bold)).monospacedDigit()
                    .foregroundStyle(barColor)
                    .contentTransition(.numericText())
                if !live.stopped {
                    HStack(spacing: 6) {
                        ForEach(1...max(1, live.beatsInBar), id: \.self) { b in
                            Circle().fill(b == live.beat ? Paper.blue : Paper.line).frame(width: 9, height: 9)
                        }
                    }
                }
            }
            .frame(minWidth: 140, alignment: .leading)
            Spacer(minLength: 0)
            VStack(spacing: 4) {
                HStack(spacing: 8) {
                    Circle().fill(color).frame(width: 12, height: 12)
                    Text(word).font(.title2.weight(.semibold)).foregroundStyle(color)
                }
                if let sub {
                    Text(sub).font(.callout).foregroundStyle(Paper.muted).lineLimit(1)
                }
            }
            Spacer(minLength: 0)
            Text("♩ \(Int(live.bpm.rounded()))")
                .font(.system(size: 30, weight: .semibold)).monospacedDigit()
                .foregroundStyle(Paper.ink)
                .frame(minWidth: 140, alignment: .trailing)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 10)
        .background(Paper.color)
        .overlay(alignment: .bottom) { Paper.line.frame(height: 1) }
    }

    private var waitingLike: Bool { ["waiting", "ready"].contains(live.state) }
    private var cued: Bool { live.stopped && live.restarting && live.state != "paused" }  // paused: you're there, alone
    private var barCaption: String { cued ? "From bar" : live.state == "ready" ? "Start at bar" : "Bar" }
    private var barColor: Color { cued ? Paper.amber : Paper.ink }

    private var word: String {
        if live.keyboard == nil { return "No piano" }
        switch live.state {
        case "playing": return "With you"
        case "trailing": return "You stopped"
        case "paused": return "Paused"
        case "finished": return "Finished"
        case "ready": return live.listening ? "Ready · listening" : "Ready"
        default: return live.listening ? "Listening" : "Waiting"
        }
    }

    private var color: Color {
        if live.keyboard == nil { return Paper.warn }
        switch live.state {
        case "playing": return Paper.blue
        case "trailing", "waiting": return Paper.amber
        default: return Paper.muted
        }
    }

    private var sub: String? {
        if live.keyboard == nil { return "Plug in the piano (USB)" }
        if live.stopped, let heard = live.heard, live.restarting { return "Heard “\(heard)”" }
        switch live.state {
        case "ready": return "Play to begin, or say a bar"
        case "waiting": return live.restarting ? "Play from there" : "Say where to start, or play on"
        case "paused": return "On your own · say “resume” or a bar"
        case "finished": return "“Again” or “from the top” starts a new run"
        default: return nil
        }
    }
}

/// Start from any bar: a keypad (with "pickup", as in "from the pickup to bar 9"), or the movement's bars in
/// one strip, grey as far as you got.
struct BarPicker: View {
    let session: Session
    let live: Live
    let done: () -> Void
    @State private var digits = ""
    @State private var pickup = false

    private var bar: Int? { Int(digits) }
    private var valid: Bool { bar.map { (session.firstBar...session.lastBar).contains($0) } ?? false }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Start from").font(.headline)
            Text(pickup ? "The pickup to bar \(digits.isEmpty ? "…" : digits)" : "Bar \(digits.isEmpty ? "…" : digits)")
                .font(.system(size: 40, weight: .bold)).monospacedDigit()
                .foregroundStyle(valid || digits.isEmpty ? Paper.ink : Paper.warn)
            BarRibbon(first: session.firstBar, last: session.lastBar, furthest: live.furthestBar, selected: bar) {
                digits = String($0)
            }
            .frame(height: 34)
            Text(valid ? "The strings wait for you to play there." : digits.isEmpty ? "Type a bar, or tap one in the strip or on the score."
                 : "Bars \(session.firstBar)–\(session.lastBar) only.")
                .font(.callout).foregroundStyle(Paper.muted)
            Grid(horizontalSpacing: 10, verticalSpacing: 10) {
                ForEach([[1, 2, 3], [4, 5, 6], [7, 8, 9]], id: \.self) { row in
                    GridRow { ForEach(row, id: \.self) { n in key(String(n)) { type(n) } } }
                }
                GridRow {
                    key("pickup", on: pickup) { pickup.toggle() }
                    key("0") { type(0) }
                    key("⌫") { digits = String(digits.dropLast()) }
                }
            }
            Button {
                if let bar { session.cue(pickup ? "p\(bar)" : String(bar)) }
                done()
            } label: {
                Text(valid ? (pickup ? "Start at the pickup to bar \(digits)" : "Start at bar \(digits)") : "Start")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.glassProminent)
            .controlSize(.extraLarge)
            .disabled(!valid)
        }
        .padding(24)
        .frame(width: 380)
    }

    private func type(_ n: Int) {
        if digits.count < 3 { digits = digits == "0" ? String(n) : digits + String(n) }
    }

    private func key(_ text: String, on: Bool = false, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(text).font(text.count > 1 && text != "⌫" ? .body.weight(.medium) : .title2.weight(.medium))
                .frame(maxWidth: .infinity, minHeight: 56)
        }
        .buttonStyle(.bordered)
        .tint(on ? Paper.blue : Paper.ink)
    }
}

/// The movement's bars in one strip: grey up to the furthest bar played; tap to choose one.
struct BarRibbon: View {
    let first: Int, last: Int
    let furthest: Int?
    let selected: Int?
    let choose: (Int) -> Void

    var body: some View {
        GeometryReader { g in
            let n = last - first + 1, w = g.size.width / CGFloat(n)
            ZStack(alignment: .topLeading) {
                ForEach(first...last, id: \.self) { b in
                    Rectangle()
                        .fill(b == selected ? Paper.amber : furthest.map { b <= $0 } ?? false ? Paper.muted.opacity(0.35) : Paper.line)
                        .frame(width: max(1, w - 1), height: 18)
                        .offset(x: CGFloat(b - first) * w)
                }
                ForEach(Array(stride(from: first == 1 ? 10 : first, through: last, by: 10)) + [first], id: \.self) { b in
                    Text("\(b)").font(.caption2).foregroundStyle(Paper.muted).monospacedDigit()
                        .offset(x: CGFloat(b - first) * w, y: 20)
                }
            }
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0).onChanged { v in
                choose(min(last, max(first, first + Int(v.location.x / w))))
            })
        }
    }
}

/// The strings' mix (a volume and a mute for each instrument) and the tempo of their first notes.
struct StringsPanel: View {
    let session: Session
    let live: Live
    @State private var volumes: [String: Float] = [:]
    @State private var tempo = 63.0

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Strings").font(.headline)
            ForEach(session.piece.strings, id: \.self) { part in
                HStack(spacing: 14) {
                    Text(part == "Violoncello" ? "Cello" : part).frame(width: 60, alignment: .leading)
                    Slider(value: binding(part), in: 0...1)
                    Button { volumes[part] = (volumes[part] ?? 1) > 0 ? 0 : 1; apply(part) } label: {
                        Image(systemName: (volumes[part] ?? 1) > 0 ? "speaker.wave.2.fill" : "speaker.slash.fill")
                            .frame(width: 28)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel((volumes[part] ?? 1) > 0 ? "Mute" : "Unmute")
                }
            }
            Divider()
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("First notes at ♩ \(Int(tempo.rounded()))").monospacedDigit()
                    Spacer()
                    Stepper("", value: $tempo, in: 30...160, step: 2).labelsHidden()
                        .onChange(of: tempo) { _, v in if abs(v - live.bpm) >= 1 { session.setTempo(v) } }
                }
                Text("Only for where the strings come in first. After that they follow you.")
                    .font(.footnote).foregroundStyle(Paper.muted)
            }
            .disabled(!live.stopped || live.state == "trailing")
        }
        .padding(24)
        .frame(width: 400)
        .onAppear {
            for p in session.piece.strings { volumes[p] = Settings.partVolume(p) }
            tempo = live.bpm.rounded()
        }
    }

    private func binding(_ part: String) -> Binding<Float> {
        Binding(get: { volumes[part] ?? 1 }, set: { volumes[part] = $0; apply(part) })
    }

    private func apply(_ part: String) {
        let v = volumes[part] ?? 1
        session.rig.strings.setVolume(part, v)
        Settings.setPartVolume(part, v)
    }
}

/// What the score shows: the parts (the piano part alone, for rehearsing from your own part, or any of them)
/// and the size of the music. The page engraves it again to fit, keeping the full score's line breaks.
struct ScorePanel: View {
    let parts: [String]  // the score's parts, sorted
    @Binding var look: ScoreLook

    private var ordered: [String] { STRING_PARTS.filter(parts.contains) + parts.filter { !STRING_PARTS.contains($0) } }
    private var shown: Set<String> { Set(look.parts ?? ordered) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Score").font(.headline)
            HStack(spacing: 10) {
                Button("Full score") { set(nil) }
                    .buttonStyle(.bordered).tint(look.parts == nil ? Paper.blue : Paper.ink)
                Button("Piano part") { set(["Piano"]) }
                    .buttonStyle(.bordered).tint(look.parts == ["Piano"] ? Paper.blue : Paper.ink)
            }
            .controlSize(.large)
            ForEach(ordered, id: \.self) { part in
                Toggle(part == "Violoncello" ? "Cello" : part, isOn: Binding(
                    get: { shown.contains(part) },
                    set: { on in
                        var s = shown
                        if on { s.insert(part) } else if s.count > 1 { s.remove(part) }
                        set(s.count == ordered.count ? nil : ordered.filter(s.contains))
                    }))
            }
            Divider()
            HStack {
                Text("Size \(Int((look.size * 100).rounded()))%").monospacedDigit()
                Spacer()
                Stepper("", value: Binding(get: { look.size }, set: { look.size = $0; look.save() }), in: 0.6...1.8, step: 0.1)
                    .labelsHidden()
            }
            Text("The music is engraved again to fit; a part on its own keeps the full score's lines.")
                .font(.footnote).foregroundStyle(Paper.muted)
        }
        .padding(24)
        .frame(width: 340)
    }

    private func set(_ p: [String]?) { look.parts = p; look.save() }
}
