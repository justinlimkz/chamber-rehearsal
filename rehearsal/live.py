"""Live accompaniment: the FP-30 in, strings out through FluidSynth, voice cues from the microphone.

Threads share one `Accompanist` under a lock: the MIDI callback feeds piano notes, a follower thread
feeds Matchmaker's estimates, the voice listener (and typed cues, as a backup) feed restart cues, and
the main thread polls `due()` every millisecond and plays what comes out. The microphone is only
listened to while the strings are waiting. Each session is saved (piano plus strings) to
`build/sessions/` for listening back.

The keyboard needn't be plugged in at the start: `KeyboardLink` looks for it every couple of seconds (or
at once on "scan"), connects when it appears, and notices if it is unplugged.

The state is shown in a window (`app.py`), or with `plain` printed line by line.
"""
from __future__ import annotations

import bisect
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import mido

from . import midi_io, simulate, voice
from .accompanist import Accompanist, Out
from .annotate import Passage
from .follower import MatchmakerFollower
from .score import Score
from .sound import FluidSynthEngine
from .tempo import BeatClock

POLL_S = 0.001
SCAN_S = 2.0  # how often to look for the keyboard (to connect it, or to notice it was unplugged)


@dataclass
class Snapshot:
    """What the window shows, copied out under the lock."""
    title: str
    first_bar: int
    last_bar: int
    mode: str  # e.g. "strings follow you"
    state: str  # "ready" | "playing" | "trailing" | "waiting" | "finished"
    mic: str  # "asleep" | "listening" | "off"
    bar: int | None = None
    beat: int | None = None  # 1-based beat in bar
    beats_in_bar: int = 3
    bpm: float | None = None
    tempo_history: list[float] = field(default_factory=list)  # bpm per bar played
    timing_ms: list[float] = field(default_factory=list)  # strings minus you, recent chords
    played_bars: set[int] = field(default_factory=set)
    restart_bar: int | None = None  # where the strings will come in, while waiting
    notices: list[tuple[float, str]] = field(default_factory=list)
    keyboard: str | None = "connected"  # the keyboard's input name; None: not plugged in


def typed_cue(line: str) -> voice.Cue | None:
    """A typed cue: "9", "bar 9", "pickup 9", "top" or "again"."""
    words = line.strip().lower().split()
    if words and words[-1].isdigit():
        words = ["from", "the", "pickup", "to", "bar"] + words[-1:] if "pickup" in words else ["from", "bar"] + words[-1:]
        words[-1] = voice.number_words(int(words[-1]))
    elif words in (["top"], ["beginning"]):
        words = ["from", "the", "top"]
    return voice.parse(" ".join(words))


def run(score: Score, port: str | None, first_bar: int = 1, last_bar: int = 16, use_voice: bool = True,
        out_dir: Path = Path("build/sessions"), leadership: list[Passage] | None = None,
        tempo: float = 63.0, ui: str = "app", window: bool = True, ipad: bool = False) -> Path | None:
    """`port`: the keyboard's MIDI input, or None for the first that looks like the FP-30. `ui`: "app"
    (a window: the live view and the music-stand view) or "plain" (lines printed as things happen).
    `ipad`: also serve the score view to other devices on the network (with "app")."""
    engine = FluidSynthEngine()
    acc = Accompanist(score, first_bar, last_bar, clock=BeatClock(spb=60 / tempo), leadership=leadership)
    acc.notices.append((0.0, "lead: by the score's annotation" if leadership else "lead: piano throughout"))
    view = ViewState(score, acc, first_bar, last_bar, "strings lead by passage" if leadership else "strings follow you")
    lock = threading.Lock()
    t0 = time.perf_counter()
    piano: list[midi_io.MidiEvent] = []
    played: list[Out] = []
    ended = threading.Event()  # the app's "End session"

    def on_msg(msg: mido.Message) -> None:
        ev = midi_io.from_message(msg, time.perf_counter() - t0)
        if ev is None:
            return
        piano.append(ev)
        if ev.is_onset:
            with lock:
                acc.on_piano_onset(ev.time, ev.note)

    def on_cue(cue: voice.Cue) -> None:
        with lock:
            view.last_cue = acc.on_cue(time.perf_counter() - t0, cue)  # also shown with the other notices

    def on_line(line: str) -> None:
        # Backup to the microphone: type "9", "bar 9", "pickup 9", "top" or "again" and press Enter.
        if line.strip().lower() == "scan":
            keyboard.scan()
            return
        cue = typed_cue(line)
        if cue is not None:
            on_cue(cue)
        else:
            with lock:
                acc.notices.append((time.perf_counter() - t0, f'"{line.strip()}"? cues: 9, pickup 9, top, again'))

    def typed_lines() -> None:
        for line in sys.stdin:
            on_line(line)

    def on_position(t: float, beat: float) -> None:
        with lock:
            acc.on_follower(t - t0, beat)

    def notice(text: str) -> None:
        with lock:
            acc.notices.append((time.perf_counter() - t0, text))

    keyboard = KeyboardLink(score, port, on_msg, on_position, notice)
    keyboard.start()

    listener = None
    if use_voice:
        try:
            listener = voice.Listener(on_cue, max_bar=last_bar)
            listener.start()
        except Exception as e:  # no model, no microphone permission, ...
            print(f"Voice cues off ({e}). Type cues instead.", file=sys.stderr)
            listener = None

    server = None
    threading.Thread(target=typed_lines, daemon=True).start()
    if ui == "app":
        from . import app

        def state() -> dict:
            with lock:
                return view.app_state(time.perf_counter() - t0, listener, keyboard.name)

        server = app.AppServer(app.score_layout(score, acc), state, on_line, ended.set, keyboard.scan,
                               host="0.0.0.0" if ipad else "127.0.0.1", score_path=score.path)
        url = server.start()
        if window:
            app.open_window(url)
        print(f"Rehearsal window: {url}  (S for the music-stand view)")
        if ipad:
            threading.Thread(target=server.engraving, daemon=True).start()  # ready before the iPad asks
            print("On the iPad (same Wi-Fi), open in Safari: " + "  or  ".join(server.network_urls()))
    print(f"Ready: bars {first_bar}-{last_bar}. Play, or say where to start (\"from bar 9\"). Ctrl-C to quit.")
    shown_log = shown_notes = 0
    try:
        while not ended.is_set():
            now = time.perf_counter() - t0
            with lock:
                outs = acc.due(now)
                new_log, shown_log = acc.log[shown_log:], len(acc.log)
                new_notes, shown_notes = acc.notices[shown_notes:], len(acc.notices)
                waiting = acc.waiting
                view.update(new_log)
            if listener is not None:
                (listener.active.set if waiting else listener.active.clear)()
            keyboard.poll()
            for o in outs:
                if o.kind == "on":
                    engine.note_on(o.part, o.pitch, o.velocity)
                elif o.kind == "off":
                    engine.note_off(o.part, o.pitch)
                else:
                    engine.expression(o.part, o.velocity)
                played.append(Out(now, o.kind, o.part, o.pitch, o.velocity, o.beat))
            for t, beat, asyn in new_log:
                bar, bib = score.bar_at(beat)
                print(f"bar {bar:>3} beat {bib:4.1f}   {60 / acc.clock.spb:5.1f} bpm   you {asyn * 1000:+5.0f} ms vs strings")
            for _, text in new_notes:
                print(f"  ({text})", flush=True)
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.stop()
        with lock:
            outs = acc.flush(time.perf_counter() - t0)
        for o in outs:
            if o.kind == "off":
                engine.note_off(o.part, o.pitch)
        engine.close()
        keyboard.stop()
        if listener is not None:
            listener.stop()
    if not piano:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / time.strftime("%Y%m%d-%H%M%S.mid")
    return simulate.write_midi(piano, played, path, acc.notices)


class KeyboardLink:
    """The keyboard's MIDI input and the score follower listening to it, connected whenever the keyboard
    is there: looked for at start, every SCAN_S seconds after, and at once on request (`scan`).

    The list of inputs must be read on the main thread: CoreMIDI only shows a newly plugged-in device to
    the thread that runs its notifications (seen with a virtual port: a background thread never saw it
    appear). So the main loop calls `poll`, which costs about 0.2 ms; connecting (opening the input and
    building the follower, about a second) happens on a thread of its own.
    """

    def __init__(self, score: Score, wanted: str | None, on_msg, on_position, notice):
        self.score, self.wanted = score, wanted
        self.on_msg, self.on_position, self.notice = on_msg, on_position, notice
        self.name: str | None = None  # the connected input, None while there is none
        self._port = self._follower = None
        self._connecting = False
        self._requested = threading.Event()
        self._last_poll = -float("inf")
        self._lock = threading.Lock()

    def start(self) -> None:
        """Connect now if the keyboard is there (on the calling thread, which must be the main one)."""
        name = self._find()
        if name is not None:
            self._connect(name)
        else:
            self.notice("no keyboard found: plug in the piano (USB); looking every few seconds")
        self._last_poll = time.monotonic()

    def scan(self) -> None:
        """Ask for a look now (from any thread); the main loop's next `poll` does it."""
        self._requested.set()

    def poll(self) -> None:
        """From the main loop: every SCAN_S seconds, or when asked, check the keyboard is (still) there."""
        now = time.monotonic()
        if not self._requested.is_set() and now - self._last_poll < SCAN_S:
            return
        self._requested.clear()
        self._last_poll = now
        with self._lock:
            if self._connecting:
                return
            if self.name is not None:
                if self.name in midi_io.list_inputs():
                    return
                self.notice(f"keyboard unplugged ({self.name}): plug it back in")
                self._close()
            name = self._find()
            if name is None:
                return
            self._connecting = True
        threading.Thread(target=self._connect, args=(name,), daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            self._close()

    def _find(self) -> str | None:
        if self.wanted is not None:
            return self.wanted if self.wanted in midi_io.list_inputs() else None
        return midi_io.find_piano_input()

    def _connect(self, name: str) -> None:
        try:
            port = mido.open_input(name, callback=self.on_msg)
            follower = MatchmakerFollower(self.score, port=name)
        except Exception as e:  # unplugged again already, or not ready yet: the next poll retries
            self.notice(f"couldn't connect the keyboard ({e}); trying again")
            with self._lock:
                self._connecting = False
            return
        with self._lock:
            self._port, self._follower, self.name, self._connecting = port, follower, name, False
        threading.Thread(target=self._follow, args=(follower,), daemon=True).start()
        self.notice(f"keyboard connected: {name}")

    def _follow(self, follower: MatchmakerFollower) -> None:
        try:
            for pos in follower.positions():
                self.on_position(pos.wall_time, pos.beat)
        except Exception:
            pass  # the keyboard went away under the follower; a later poll reconnects

    def _close(self) -> None:
        if self._port is not None:
            self._port.close()
        if self._follower is not None:
            try:
                self._follower.stop()
            except Exception:
                pass
        self._port = self._follower = None
        self.name = None


class ViewState:
    """What the window shows beyond the accompanist's own state: history gathered as chords come in."""

    def __init__(self, score: Score, acc: Accompanist, first_bar: int, last_bar: int, mode: str):
        self.acc = acc
        self.first_bar, self.last_bar, self.mode = first_bar, last_bar, mode
        self.title = f"{score.composer.split()[-1]} · {score.title}" if score.composer else score.title
        self.starts = [b for b, _ in acc.bar_starts]
        self.bars = [bar for _, bar in acc.bar_starts]
        self.tempo_history: list[float] = []  # per bar
        self.tempo_bar: int | None = None
        self.timing_ms: list[float] = []
        self.played_bars: set[int] = set()
        self.last_cue: str | None = None  # what the last cue will do, shown until the strings come in

    def bar_at(self, beat: float) -> tuple[int, int, int]:
        """(written bar, 1-based beat in bar, beats in bar)."""
        i = max(0, bisect.bisect_right(self.starts, beat) - 1)
        length = round(self.starts[i + 1] - self.starts[i]) if i + 1 < len(self.starts) else 3
        return self.bars[i], int(beat - self.starts[i]) + 1, max(1, length)

    def update(self, new_log: list[tuple[float, float, float]]) -> None:
        for _, beat, asyn in new_log:
            bar = self.bar_at(beat)[0]
            self.played_bars.add(bar)
            if asyn == 0.0:  # a restart (logged with 0): the cue, if any, has been acted on
                self.last_cue = None
            else:
                # One tempo per bar (the latest), so the sparkline shows the phrase, not chord-to-chord jitter.
                if bar != self.tempo_bar:
                    self.tempo_history.append(0.0)
                    self.tempo_bar = bar
                self.tempo_history[-1] = 60 / self.acc.clock.spb
                self.timing_ms.append(-asyn * 1000)  # strings minus you
        del self.tempo_history[:-32], self.timing_ms[:-32]

    def app_state(self, now: float, listener, keyboard: str | None) -> dict:
        """The window's state (`app.py`): the snapshot, plus the beat clock so the page can move the
        playhead between updates."""
        s = self.snapshot(now, listener, keyboard)
        acc = self.acc
        restart = acc.restart_from if s.state in ("waiting", "ready") else None
        return {"state": s.state, "mic": s.mic, "keyboard": keyboard, "mode": s.mode, "bpm": s.bpm, "tempoHistory": s.tempo_history,
                "timingMs": s.timing_ms, "notices": s.notices, "lastCue": self.last_cue if restart is not None else None,
                "restartBeat": restart, "lastBeat": acc.last_beat, "endBeat": min(acc.end_beat, 1e9),
                "clock": {"t0": acc.clock.t0, "b0": acc.clock.b0, "spb": acc.clock.spb}, "now": now}

    def snapshot(self, now: float, listener, keyboard: str | None = None) -> Snapshot:
        acc = self.acc
        if acc.finished:
            state = "finished"
        elif acc.trailing:
            state = "trailing"
        elif acc.waiting:
            state = "waiting" if acc.started else "ready"
        else:
            state = "playing"
        mic = "off" if listener is None else "listening" if listener.active.is_set() else "asleep"
        s = Snapshot(self.title, self.first_bar, self.last_bar, self.mode, state, mic,
                     bpm=60 / acc.clock.spb, tempo_history=list(self.tempo_history), timing_ms=list(self.timing_ms),
                     played_bars=set(self.played_bars), notices=acc.notices[-5:], keyboard=keyboard)
        if state in ("playing", "trailing"):
            s.bar, s.beat, s.beats_in_bar = self.bar_at(min(acc.clock.beat_at(now), acc.end_beat - 1e-6))
        elif acc.last_beat is not None:
            s.bar, _, s.beats_in_bar = self.bar_at(acc.last_beat)
        if state in ("waiting", "ready") and acc.restart_from is not None:
            s.restart_bar = self.bar_at(acc.restart_from)[0]
        return s
