import SwiftUI
import WebKit

// A few bars of a piece, engraved, for the library: where you stopped on the Continue card, the opening when a
// piece is added. The same engraver as the rehearse screen (Verovio, in a small web view that can't be touched),
// with the parts you chose to see there. Each engraving is kept for as long as the app runs, so coming back to
// the library shows it at once.

struct ScorePreview: View {
    let piece: Piece
    let first: Int
    var count = 4
    var mark: Int? = nil  // a bar to shade, as the rehearse screen shades the bar the strings come back at

    @State private var ratio: CGFloat?

    var body: some View {
        PreviewWeb(job: job, ratio: $ratio)
            .aspectRatio(ratio ?? 2, contentMode: .fit)
            .opacity(ratio == nil ? 0 : 1)
            .animation(.easeOut(duration: 0.2), value: ratio)
            .accessibilityLabel("Bars \(job.first) to \(job.last) of the score")
    }

    private var job: PreviewWeb.Job {
        let last = max(first, min(first + count - 1, piece.bars))
        return .init(piece: piece.url, id: piece.id, first: first, last: last, mark: mark, parts: ScoreLook.saved.parts)
    }
}

struct PreviewWeb: UIViewRepresentable {
    struct Job: Hashable {
        let piece: URL, id: String, first: Int, last: Int, mark: Int?, parts: [String]?
        var json: String {
            let o: [String: Any] = ["first": first, "last": last, "mark": mark ?? -1, "parts": parts ?? NSNull()]
            return String(decoding: try! JSONSerialization.data(withJSONObject: o), as: UTF8.self)
        }
    }

    let job: Job
    @Binding var ratio: CGFloat?

    @MainActor static var engraved: [Job: (svg: String, ratio: CGFloat)] = [:]

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.setURLSchemeHandler(context.coordinator, forURLScheme: "rehearsal")
        config.userContentController.add(context.coordinator, name: "preview")
        let web = WKWebView(frame: .zero, configuration: config)
        web.isOpaque = false
        web.backgroundColor = .clear
        web.scrollView.backgroundColor = .clear
        web.scrollView.isScrollEnabled = false
        web.isUserInteractionEnabled = false
        web.navigationDelegate = context.coordinator
        context.coordinator.show(job, in: web)
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {
        context.coordinator.parent = self
        if context.coordinator.job != job { context.coordinator.show(job, in: web) }
    }

    static func dismantleUIView(_ web: WKWebView, coordinator: Coordinator) {
        web.configuration.userContentController.removeScriptMessageHandler(forName: "preview")
    }

    final class Coordinator: NSObject, WKURLSchemeHandler, WKScriptMessageHandler, WKNavigationDelegate {
        var parent: PreviewWeb
        var job: Job?

        init(_ parent: PreviewWeb) { self.parent = parent }

        @MainActor func show(_ job: Job, in web: WKWebView) {
            self.job = job
            if let done = PreviewWeb.engraved[job] {
                web.loadHTMLString(Self.page(svg: done.svg), baseURL: nil)
                parent.ratio = done.ratio
            } else {
                parent.ratio = nil
                web.load(URLRequest(url: URL(string: "rehearsal://app/preview.html")!))
            }
        }

        func webView(_ web: WKWebView, didFinish navigation: WKNavigation!) {
            guard let job, web.url?.scheme == "rehearsal" else { return }
            web.evaluateJavaScript("preview(\(job.json))")
        }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let m = message.body as? [String: Any], let job,
                  let svg = m["svg"] as? String, let r = m["ratio"] as? Double, r > 0 else { return }
            MainActor.assumeIsolated {
                PreviewWeb.engraved[job] = (svg, CGFloat(r))
                parent.ratio = CGFloat(r)
            }
        }

        func webView(_ web: WKWebView, start task: WKURLSchemeTask) {
            let body: Data?, type: String
            switch task.request.url?.path ?? "" {
            case "/preview.html":
                body = Data(Self.page(svg: nil).utf8); type = "text/html"
            case "/verovio.js":
                body = try? Data(contentsOf: Bundle.main.url(forResource: "verovio-toolkit-wasm", withExtension: "js")!)
                type = "text/javascript"
            case "/musicxml":
                body = job.flatMap { try? Data(contentsOf: $0.piece.appendingPathComponent("score.musicxml")) }
                type = "application/xml"
            default:
                body = nil; type = "text/plain"
            }
            task.didReceive(HTTPURLResponse(url: task.request.url!, statusCode: body == nil ? 404 : 200, httpVersion: nil,
                                            headerFields: ["Content-Type": type + "; charset=utf-8"])!)
            task.didReceive(body ?? Data())
            task.didFinish()
        }

        func webView(_ web: WKWebView, stop task: WKURLSchemeTask) {}

        /// The page: the engraving if there is one, or the script that makes it (`preview`, which posts it back).
        static func page(svg: String?) -> String {
            """
            <!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
            <style>html,body{margin:0;background:transparent}svg{display:block;width:100%;height:auto}</style>
            <body><div id="s">\(svg ?? "")</div>
            <script>
            function loadVerovio() {
              return new Promise((resolve, reject) => {
                const sc = document.createElement("script");
                sc.src = "/verovio.js";
                sc.onload = () => verovio.module.onRuntimeInitialized = () => resolve(new verovio.toolkit());
                sc.onerror = reject;
                document.head.appendChild(sc);
              });
            }
            // As ipad.html's partsOnly, without the line breaks: only the chosen parts
            function partsOnly(xml, parts) {
              const doc = new DOMParser().parseFromString(xml, "application/xml"), keep = new Set();
              for (const sp of [...doc.querySelectorAll("part-list > score-part")]) {
                const name = (sp.querySelector("part-name")?.textContent || "").trim();
                if (parts.some((p) => name.toLowerCase().startsWith(p.toLowerCase()))) keep.add(sp.id); else sp.remove();
              }
              for (const g of [...doc.querySelectorAll("part-list > part-group")]) g.remove();
              for (const part of [...doc.querySelectorAll("score-partwise > part")]) if (!keep.has(part.id)) part.remove();
              return new XMLSerializer().serializeToString(doc);
            }
            async function preview(o) {
              const tk = await loadVerovio();
              let xml = await (await fetch("/musicxml")).text();
              if (o.parts) xml = partsOnly(xml, o.parts);
              tk.setOptions({ pageWidth: 2100, pageHeight: 60000, adjustPageHeight: true, scale: 38, breaks: "none",
                header: "none", footer: "none", svgViewBox: true, svgAdditionalAttribute: ["measure@n"],
                pageMarginTop: 20, pageMarginBottom: 20, pageMarginLeft: 20, pageMarginRight: 20 });
              if (!tk.loadData(xml)) return;
              tk.select({ measureRange: o.first + "-" + o.last });
              tk.redoLayout();
              const box = document.getElementById("s");
              box.innerHTML = tk.renderToSVG(1);
              const m = box.querySelector('g.measure[data-n="' + o.mark + '"]');
              if (m) {  // shaded in the rehearse screen's blue, behind the notes
                const b = m.getBBox(), r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
                r.setAttribute("x", b.x); r.setAttribute("y", b.y); r.setAttribute("width", b.width); r.setAttribute("height", b.height);
                r.setAttribute("fill", "rgba(47,111,179,.14)");
                m.parentNode.insertBefore(r, m);
              }
              const vb = box.querySelector("svg").viewBox.baseVal;
              webkit.messageHandlers.preview.postMessage({ svg: box.innerHTML, ratio: vb.width / vb.height });
            }
            </script>
            """
        }
    }
}
