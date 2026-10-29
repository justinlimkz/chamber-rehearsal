import RehearsalCore
import SwiftUI

// Chamber rehearsal on the iPad: plug the piano in, pick a piece, play, and the string trio follows
// (rehearsal/live.py and the iPad score view, without the Mac). Two places: the library, and the rehearse
// screen with the score; everything else is a sheet or a popover over them.

@main
struct RehearsalApp: App {
    @State private var rig: Result<Rig, Error> = Result {
        let r = try Rig()
        r.start()
        return r
    }
    @State private var library = Library()
    @State private var session: Session?
    @State private var adding: Piece?
    @State private var problem: String?
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            switch rig {
            case .success(let rig):
                NavigationStack {
                    LibraryView(library: library, rig: rig, open: { open($0, from: $1, rig: rig) }, adding: $adding)
                        .navigationDestination(isPresented: Binding(get: { session != nil }, set: { if !$0 { close() } })) {
                            if let session {
                                RehearseView(session: session) { close() }
                            }
                        }
                }
                .tint(Paper.blue)
                .preferredColorScheme(.light)  // the score is printed paper; the whole app follows it, even in dark mode
                .onAppear {
                    UIApplication.shared.isIdleTimerDisabled = Settings.keepAwakeOn  // the score stays on screen
                    // Simulator screenshots: REHEARSAL_OPEN=<piece>[@<bar>] opens it straight away.
                    if Rig.silent, let arg = ProcessInfo.processInfo.environment["REHEARSAL_OPEN"] {
                        let parts = arg.split(separator: "@").map(String.init)
                        if let p = library.piece(parts[0]) { open(p, from: parts.count > 1 ? Int(parts[1]) : nil, rig: rig) }
                    }
                }
                .onChange(of: phase) { _, p in
                    if p == .active && !Rig.silent { rig.strings.resume() }
                    if p == .background { session?.save(renew: true) }  // coming back starts a new recording
                }
                .onOpenURL { url in  // a piece from AirDrop, or "Open in" from Files
                    adding = LibraryView.preview(url, problem: &problem)
                }
                .alert("Couldn't open the piece", isPresented: .constant(problem != nil)) {
                    Button("OK") { problem = nil }
                } message: { Text(problem ?? "") }
            case .failure(let e):
                Text("Couldn't start: \(e.localizedDescription)").padding()
            }
        }
    }

    private func open(_ piece: Piece, from bar: Int?, rig: Rig) {
        do {
            let s = try Session(piece: piece, rig: rig)
            s.start()
            if let bar { s.cue(String(bar)) }
            session = s
        } catch {
            problem = error.localizedDescription
        }
    }

    /// Back to the library: the strings stop, the recording is kept, and "Continue" remembers where you were.
    private func close() {
        guard let s = session else { return }
        let st = s.state()
        let beat = st["restartBeat"] as? Double ?? st["lastBeat"] as? Double
        library.remember(s.piece, bar: beat.map { s.score.barAt($0).bar } ?? s.firstBar)
        s.close()
        session = nil
    }
}
