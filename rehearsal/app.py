"""The rehearsal window: a small local web page, served by the live process and shown in its own window.

The page (`rehearsal/web/index.html`) gets the score layout once (`/score`), then the state as a stream
of server-sent events (`/events`), sent only when something shown changes. Between events it moves the
playhead itself from the beat clock (anchor time, anchor beat, seconds per beat), so the display is
smooth without a message per frame. Clicks come back as cues (`/cue`), a request to look for the keyboard again (`/scan`) or the end of
the session (`/end`).

The iPad view (`/ipad`, `rehearsal/web/ipad.html`) is the score itself, engraved by Verovio from the
same MusicXML (`/engraving.svg`, with where each bar's notes start in `/anchors`; `engrave.py`), following the same state stream: the current bar
highlighted, the page scrolled a system at a time, tap a bar to start there.

Only the standard library: `http.server`, on 127.0.0.1 unless asked to listen on the network (`host`
"0.0.0.0", for the iPad). The window is Chrome in app mode (no tabs or address bar) when Chrome is
installed, else the default browser.
"""
from __future__ import annotations

import gzip
import json
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .accompanist import STRING_PARTS, Accompanist
from .score import Score

WEB = Path(__file__).resolve().parent / "web"
PAGE = WEB / "index.html"
IPAD_PAGE = WEB / "ipad.html"
CHROME = Path("/Applications/Google Chrome.app")
EVENT_INTERVAL_S = 0.04  # how often the state is checked for changes
LANES = (*STRING_PARTS, "Piano")


def score_layout(score: Score, acc: Accompanist) -> dict:
    """Bars of the session in playing order (repeats unfolded), each with its notes per lane, for the
    score strip: positions are fractions of the bar, heights fractions of the lane's pitch range."""
    starts = [(b, bar) for b, bar in acc.bar_starts if acc.start_beat <= b < acc.end_beat]
    ends = [b for b, _ in starts[1:]] + [min(acc.end_beat, starts[-1][0] + 3)]
    notes = [(n.part, n.onset_beat, n.duration_beat, n.pitch) for n in acc.notes]
    notes += [(n.part, n.onset_beat, n.duration_beat, n.pitch) for n in score.notes("Piano")
              if acc.start_beat <= n.onset_beat < acc.end_beat]
    ranges = {}
    for lane in LANES:
        ps = [p for part, _, _, p in notes if part.lower().startswith(lane.lower())]
        ranges[lane] = (min(ps, default=60), max(ps, default=72))
    bars = []
    for (start, number), end in zip(starts, ends):
        length = end - start
        cells = []
        for part, onset, dur, pitch in notes:
            if start <= onset < end:
                lane = next(name for name in LANES if part.lower().startswith(name.lower()))
                lo, hi = ranges[lane]
                cells.append({"lane": LANES.index(lane), "x": (onset - start) / length,
                              "w": max(dur, 0.1) / length, "y": 1 - (pitch - lo) / max(hi - lo, 1)})
        bars.append({"n": number, "start": start, "beats": length, "notes": cells})
    return {"title": f"{score.composer.split()[-1]} · {score.title}" if score.composer else score.title,
            "lanes": ["Violin", "Viola", "Cello", "Piano"], "bars": bars,
            "firstBar": acc.first_bar, "lastBar": acc.last_bar}


class AppServer:
    def __init__(self, layout: dict, state: Callable[[], dict], on_cue_text: Callable[[str], None],
                 on_end: Callable[[], None], on_scan: Callable[[], object] = lambda: None, port: int = 8765,
                 host: str = "127.0.0.1", score_path: Path | None = None):
        self.layout, self.state, self.on_cue_text, self.on_end = layout, state, on_cue_text, on_end
        self.on_scan = on_scan
        self.score_path = score_path
        self._engraving: tuple[bytes, bytes] | None = None  # gzipped SVG and anchors, made on first request
        self._engraving_lock = threading.Lock()
        self.stopping = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # keep the terminal for the session's own output
                pass

            def _send(self, body: bytes, kind: str, status: int = 200, gzipped: bool = False) -> None:
                if gzipped and "gzip" not in (self.headers.get("Accept-Encoding") or ""):
                    body, gzipped = gzip.decompress(body), False
                self.send_response(status)
                self.send_header("Content-Type", kind)
                if gzipped:
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path == "/":
                    self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
                elif self.path == "/ipad":
                    self._send(IPAD_PAGE.read_bytes(), "text/html; charset=utf-8")
                elif self.path == "/engraving.svg" and server.score_path is not None:
                    self._send(server.engraving()[0], "image/svg+xml", gzipped=True)
                elif self.path == "/anchors" and server.score_path is not None:
                    self._send(server.engraving()[1], "application/json")
                elif self.path == "/score":
                    self._send(json.dumps(server.layout).encode(), "application/json")
                elif self.path == "/events":
                    self._events()
                else:
                    self._send(b"not found", "text/plain", 404)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/cue" and isinstance(body.get("text"), str):
                    server.on_cue_text(body["text"][:40])
                elif self.path == "/end":
                    server.on_end()
                elif self.path == "/scan":
                    server.on_scan()
                else:
                    self._send(b"not found", "text/plain", 404)
                    return
                self._send(b"{}", "application/json")

            def _events(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                last, last_sent = None, 0.0
                try:
                    while not server.stopping.is_set():
                        state = server.state()
                        key = json.dumps({k: v for k, v in state.items() if k != "now"}, sort_keys=True)
                        if key != last or time.monotonic() - last_sent > 5:  # changes, plus a heartbeat
                            self.wfile.write(f"data: {json.dumps(state)}\n\n".encode())
                            self.wfile.flush()
                            last, last_sent = key, time.monotonic()
                        time.sleep(EVENT_INTERVAL_S)
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the window was closed or reloaded

        try:
            self.httpd = ThreadingHTTPServer((host, port), Handler)
        except OSError:  # the usual port is taken (another session?): any free one
            self.httpd = ThreadingHTTPServer((host, 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"

    def engraving(self) -> tuple[bytes, bytes]:
        with self._engraving_lock:
            if self._engraving is None:
                from . import engrave

                svg, anchors = engrave.engrave(self.score_path)
                self._engraving = gzip.compress(svg.encode(), compresslevel=6), json.dumps(anchors).encode()
            return self._engraving

    def network_urls(self, page: str = "ipad") -> list[str]:
        """Addresses another device on the same network can open: the Mac's .local name, then its IP."""
        urls = []
        try:
            name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                                  timeout=2).stdout.strip()
            if name:
                urls.append(f"http://{name}.local:{self.port}/{page}")
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))  # no packet is sent; this just picks the outgoing interface
                urls.append(f"http://{s.getsockname()[0]}:{self.port}/{page}")
        except OSError:
            pass
        return urls

    def start(self) -> str:
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.url

    def stop(self) -> None:
        self.stopping.set()
        time.sleep(2 * EVENT_INTERVAL_S)  # let open pages hear that the session ended
        self.httpd.shutdown()


def open_window(url: str) -> None:
    """Chrome in app mode (a plain window, no tabs) if installed, else the default browser."""
    if CHROME.exists() and shutil.which("open"):
        subprocess.run(["open", "-na", str(CHROME), "--args", f"--app={url}", "--window-size=1280,860"], check=False)
    else:
        webbrowser.open(url)
