import SwiftUI
import WebKit

// The engraved score that follows you: the same page the Mac serves to Safari (rehearsal/web/ipad.html),
// in a web view. Its files come from the app bundle through the `rehearsal:` scheme; the session's state
// goes in ten times a second (`receive`); taps and buttons come back as cues.

struct ScoreView: UIViewRepresentable {
    let session: Session
    static let piece = "schumann-op47-iii"

    func makeCoordinator() -> Coordinator { Coordinator(session) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.setURLSchemeHandler(context.coordinator, forURLScheme: "rehearsal")
        config.userContentController.add(context.coordinator, name: "rehearsal")
        let web = WKWebView(frame: .zero, configuration: config)
        web.isInspectable = true  // Safari's Develop menu, from the Mac
        web.scrollView.bounces = false
        web.load(URLRequest(url: URL(string: "rehearsal://app/ipad.html")!))
        context.coordinator.start(web)
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKURLSchemeHandler, WKScriptMessageHandler {
        let session: Session
        private var timer: Timer?

        init(_ session: Session) { self.session = session }

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
            let piece = Bundle.main.url(forResource: ScoreView.piece, withExtension: nil)!
            let body: Data?, type: String
            switch path {
            case "/ipad.html":
                body = try? Data(contentsOf: Bundle.main.url(forResource: "ipad", withExtension: "html")!)
                type = "text/html"
            case "/score":
                body = try? JSONSerialization.data(withJSONObject: session.layout())
                type = "application/json"
            case "/engraving.svg":
                body = try? Data(contentsOf: piece.appendingPathComponent("engraving.svg"))
                type = "image/svg+xml"
            case "/anchors":
                body = try? Data(contentsOf: piece.appendingPathComponent("anchors.json"))
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
