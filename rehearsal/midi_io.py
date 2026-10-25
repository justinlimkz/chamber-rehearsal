"""MIDI input, recording and replay. Events are plain data so the same stream feeds live and offline paths."""
from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import mido

TICKS_PER_BEAT = 480
TEMPO = 500_000  # 120 bpm -> 960 ticks per second; recordings are stored in wall-clock time
TICKS_PER_SECOND = TICKS_PER_BEAT * 1_000_000 / TEMPO


@dataclass(frozen=True)
class MidiEvent:
    time: float  # seconds from the start of the take
    kind: str  # "note_on" | "note_off" | "pedal"
    note: int = 0  # MIDI pitch (unused for pedal)
    value: int = 0  # velocity for notes, CC64 value for pedal

    @property
    def is_onset(self) -> bool:
        return self.kind == "note_on" and self.value > 0


def from_message(msg: mido.Message, t: float) -> MidiEvent | None:
    if msg.type == "note_on" and msg.velocity > 0:
        return MidiEvent(t, "note_on", msg.note, msg.velocity)
    if msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
        return MidiEvent(t, "note_off", msg.note, getattr(msg, "velocity", 0))
    if msg.type == "control_change" and msg.control == 64:
        return MidiEvent(t, "pedal", 64, msg.value)
    return None


def to_message(ev: MidiEvent) -> mido.Message:
    if ev.kind == "note_on":
        return mido.Message("note_on", note=ev.note, velocity=ev.value)
    if ev.kind == "note_off":
        return mido.Message("note_off", note=ev.note, velocity=ev.value)
    return mido.Message("control_change", control=64, value=ev.value)


def list_inputs() -> list[str]:
    return mido.get_input_names()


# The FP-30 reports itself over USB as "Roland Digital Piano", not by model name.
PIANO_HINTS = ("FP-30", "Roland Digital Piano")


def find_piano_input(hints: Iterable[str] = PIANO_HINTS) -> str | None:
    names = list_inputs()
    for hint in hints:
        for n in names:
            if hint.lower() in n.lower():
                return n
    return None


def live_events(port_name: str | None = None) -> Iterator[MidiEvent]:
    """Blocking iterator of events from a live port; time is seconds since the port opens.

    The port is looked up immediately, so a missing piano raises here rather than on first read.
    Timestamps are taken in the rtmidi callback, not when the consumer gets round to reading.
    """
    name = port_name or find_piano_input()
    if name is None:
        raise RuntimeError(f"FP-30 not found. MIDI inputs: {list_inputs() or 'none'}")
    return _port_events(name)


def _port_events(name: str) -> Iterator[MidiEvent]:
    q: queue.SimpleQueue[MidiEvent] = queue.SimpleQueue()
    t0 = time.perf_counter()

    def on_msg(msg: mido.Message) -> None:
        ev = from_message(msg, time.perf_counter() - t0)
        if ev is not None:
            q.put(ev)

    with mido.open_input(name, callback=on_msg):
        while True:
            yield q.get()


def write_take(events: Iterable[MidiEvent], path: str | Path) -> None:
    mid = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    track = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=TEMPO, time=0)])
    last = 0
    for ev in sorted(events, key=lambda e: e.time):
        tick = round(ev.time * TICKS_PER_SECOND)
        msg = to_message(ev)
        msg.time = tick - last
        last = tick
        track.append(msg)
    mid.tracks.append(track)
    mid.save(str(path))


def read_take(path: str | Path) -> list[MidiEvent]:
    """Read a take written by `write_take`, or any MIDI file (tempo changes are honoured)."""
    events = []
    t = 0.0
    for msg in mido.MidiFile(str(path)):  # iterating yields delta times in seconds
        t += msg.time
        ev = from_message(msg, t) if not msg.is_meta else None
        if ev is not None:
            events.append(ev)
    return events


def replay(events: Iterable[MidiEvent], realtime: bool = False, speed: float = 1.0) -> Iterator[MidiEvent]:
    """Yield events in order. Offline (default) yields immediately; realtime sleeps to the original timing."""
    start = time.perf_counter()
    for ev in events:
        if realtime:
            delay = ev.time / speed - (time.perf_counter() - start)
            if delay > 0:
                time.sleep(delay)
        yield ev
