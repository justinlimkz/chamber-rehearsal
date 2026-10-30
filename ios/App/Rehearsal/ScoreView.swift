import SwiftUI
import WebKit

// The engraved score that follows you: a page (ios/Web/score.html) in a web view; the app draws the status strip
// and the controls around it. The page engraves the piece's MusicXML itself, with Verovio, to the screen's width
// and the parts and size chosen here (`setView`).
// Its files come from the app and the piece's folder through the `rehearsal:` scheme; the session's state goes
// in ten times a second (`receive`); taps on bars come back as cues.

/// Which parts the score shows (nil: all) and how large (1 = a 2100-unit page on a 13-inch iPad in portrait).
struct ScoreLook: Codable, Equatable {
    var parts: [String]? = nil
    var size: Double = 1

    static let key = "scoreLook"
    static var saved: ScoreLook {
        (UserDefaults.standard.data(forKey: key)).flatMap { try? JSONDecoder().decode(ScoreLook.self, from: $0) } ?? ScoreLook()
    }
    func save() { UserDefaults.standard.set(try? JSONEncoder().encode(self), forKey: Self.key) }
    var json: String { String(decoding: (try? JSONEncoder().encode(self)) ?? Data("{}".utf8), as: UTF8.self) }
}

struct ScoreView: UIViewRepresentable {
    let session: Session
    var look = ScoreLook()

    func makeCoordinator() -> Coordinator { Coordinator(session, look: look) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.setURLSchemeHandler(context.coordinator, forURLScheme: "rehearsal")
        config.userContentController.add(context.coordinator, name: "rehearsal")
        let web = WKWebView(frame: .zero, configuration: config)
        web.isInspectable = true  // Safari's Develop menu, from the Mac
        web.scrollView.bounces = false
        web.isOpaque = false
        web.backgroundColor = UIColor(Paper.color)
        web.load(URLRequest(url: URL(string: "rehearsal://app/score.html")!))
        context.coordinator.start(web)
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {
        if context.coordinator.look != look {
            context.coordinator.look = look
            web.evaluateJavaScript("window.setView && setView(\(look.json))")
        }
    }

    static func dismantleUIView(_ web: WKWebView, coordinator: Coordinator) {
        coordinator.stop()
        web.configuration.userContentController.removeScriptMessageHandler(forName: "rehearsal")
    }

    final class Coordinator: NSObject, WKURLSchemeHandler, WKScriptMessageHandler {
        let session: Session
        var look: ScoreLook
        private var timer: Timer?

        init(_ session: Session, look: ScoreLook) { self.session = session; self.look = look }

        func stop() { timer?.invalidate() }

        func start(_ web: WKWebView) {
            timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self, weak web] _ in
                MainActor.assumeIsolated {  // scheduled on the main run loop
                    guard let self, let web,
                          let json = try? JSONSerialization.data(withJSONObject: self.session.state()) else { return }
                    web.evaluateJavaScript("window.receive && receive(\(String(decoding: json, as: UTF8.self)))")
                }
            }
        }

        func webView(_ web: WKWebView, start task: WKURLSchemeTask) {
            let path = task.request.url?.path ?? ""
            let piece = session.piece.url
            let body: Data?, type: String
            switch path {
            case "/score.html":
                body = try? Data(contentsOf: Bundle.main.url(forResource: "score", withExtension: "html")!)
                type = "text/html"
            case "/score":
                body = try? JSONSerialization.data(withJSONObject: session.layout())
                type = "application/json"
            case "/verovio.js":
                body = try? Data(contentsOf: Bundle.main.url(forResource: "verovio-toolkit-wasm", withExtension: "js")!)
                type = "text/javascript"
            case "/musicxml":
                body = try? Data(contentsOf: piece.appendingPathComponent("score.musicxml"))
                type = "application/xml"
            case "/view":
                body = Data(look.json.utf8)
                type = "application/json"
            default:
                body = nil; type = "text/plain"
            }
            let response = HTTPURLResponse(url: task.request.url!, statusCode: body == nil ? 404 : 200, httpVersion: nil,
                                           headerFields: ["Content-Type": type + "; charset=utf-8"])!
            task.didReceive(response)
            task.didReceive(body ?? Data())
            task.didFinish()
        }

        func webView(_ web: WKWebView, stop task: WKURLSchemeTask) {}

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let m = message.body as? [String: Any], m["path"] as? String == "/cue",
                  let text = (m["body"] as? [String: Any])?["text"] as? String else { return }
            session.cue(text)
        }
    }
}
