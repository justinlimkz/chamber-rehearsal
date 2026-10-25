"""Live accompaniment: the FP-30 in, strings out through FluidSynth.

Three threads share one `Accompanist` under a lock: the MIDI callback feeds piano notes, a follower
thread feeds Matchmaker's estimates, and the main thread polls `due()` every millisecond and plays what
comes out. Each session is saved (piano plus strings) to `build/sessions/` for listening back.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import mido

from . import midi_io, simulate
from .accompanist import Accompanist, Out
from .follower import MatchmakerFollower
from .score import Score
from .sound import FluidSynthEngine

POLL_S = 0.001


def run(score: Score, port: str, first_bar: int = 1, last_bar: int = 16, out_dir: Path = Path("build/sessions")) -> Path | None:
    engine = FluidSynthEngine()
    acc = Accompanist(score, first_bar, last_bar)
    lock = threading.Lock()
    t0 = time.perf_counter()
    piano: list[midi_io.MidiEvent] = []
    played: list[Out] = []

    def on_msg(msg: mido.Message) -> None:
        ev = midi_io.from_message(msg, time.perf_counter() - t0)
        if ev is None:
            return
        piano.append(ev)
        if ev.is_onset:
            with lock:
                acc.on_piano_onset(ev.time, ev.note)

    follower = MatchmakerFollower(score, port=port)

    def follow() -> None:
        for pos in follower.positions():
            with lock:
                acc.on_follower(pos.wall_time - t0, pos.beat)

    threading.Thread(target=follow, daemon=True).start()
    shown = 0
    print(f"Ready: play from bar {first_bar}; strings stop after bar {last_bar}. Ctrl-C to quit.")
    try:
        with mido.open_input(port, callback=on_msg):
            while True:
                now = time.perf_counter() - t0
                with lock:
                    outs = acc.due(now)
                    new_log = acc.log[shown:]
                    shown = len(acc.log)
                    state = "paused" if acc.paused else "finished" if acc.finished else ""
                for o in outs:
                    if o.kind == "on":
                        engine.note_on(o.part, o.pitch, o.velocity)
                    else:
                        engine.note_off(o.part, o.pitch)
                    played.append(Out(now, o.kind, o.part, o.pitch, o.velocity, o.beat))
                for t, beat, asyn in new_log:
                    bar, bib = score.bar_at(beat)
                    print(f"bar {bar:>3} beat {bib:4.1f}   {60 / acc.clock.spb:5.1f} bpm   you {asyn * 1000:+5.0f} ms vs strings")
                if state and new_log:
                    print(f"({state})")
                time.sleep(POLL_S)
    except KeyboardInterrupt:
        pass
    finally:
        with lock:
            outs = acc.flush(time.perf_counter() - t0)
        for o in outs:
            engine.note_off(o.part, o.pitch)
        engine.close()
        follower.stop()
    if not piano:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / time.strftime("%Y%m%d-%H%M%S.mid")
    return simulate.write_midi(piano, played, path)
