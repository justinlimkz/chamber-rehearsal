import RehearsalCore
import SwiftUI

// Chamber rehearsal on the iPad: plug the piano in, play, and the string trio follows (rehearsal/live.py
// and the iPad score view, without the Mac). One piece for now, packed on the Mac (rehearsal/bundle.py).

@main
struct RehearsalApp: App {
    @State private var session: Result<Session, Error> = Result {
        let url = Bundle.main.url(forResource: ScoreView.piece, withExtension: nil)!.appendingPathComponent("score.json")
        let s = try Session(score: ScoreData.load(path: url.path))
        s.start()
        return s
    }
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            switch session {
            case .success(let s):
                ScoreView(session: s)
                    .ignoresSafeArea(edges: .bottom)
                    .onAppear { UIApplication.shared.isIdleTimerDisabled = true }  // the score stays on screen
                    .onChange(of: phase) { _, p in
                        if p == .active { s.strings.resume() }
                        if p == .background { s.save(renew: true) }  // coming back starts a new recording
                    }
            case .failure(let e):
                Text("Couldn't start: \(e.localizedDescription)").padding()
            }
        }
    }
}
