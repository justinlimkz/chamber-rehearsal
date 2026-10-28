"""The rehearsal window: a small local web page, served by the live process and shown in its own window.

The page (`rehearsal/web/index.html`) gets the score layout once (`/score`), then the state as a stream
of server-sent events (`/events`), sent only when something shown changes. Between events it moves the
playhead itself from the beat clock (anchor time, anchor beat, seconds per beat), so the display is
smooth without a message per frame. Clicks come back as cues (`/cue`), a request to look for the keyboard again (`/scan`) or the end of
the session (`/end`).

Only the standard library: `http.server` on 127.0.0.1. The window is Chrome in app mode (no tabs or
address bar) when Chrome is installed, else the default browser.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .accompanist import STRING_PARTS, Accompanist
from .score import Score

PAGE = Path(__file__).resolve().parent / "web" / "index.html"
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
                 on_end: Callable[[], None], on_scan: Callable[[], object] = lambda: None, port: int = 8765):
        self.layout, self.state, self.on_cue_text, self.on_end = layout, state, on_cue_text, on_end
        self.on_scan = on_scan
        self.stopping = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # keep the terminal for the session's own output
                pass

            def _send(self, body: bytes, kind: str, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path == "/":
                    self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
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
            self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:  # the usual port is taken (another session?): any free one
            self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"

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
