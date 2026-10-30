"""MIDI files: takes (the piano alone) and sessions (piano and strings, a channel per part, as the iPad saves them).
Events are plain data, read from a file and replayed in virtual time."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import mido

TICKS_PER_BEAT = 480
TEMPO = 500_000  # 120 bpm -> 960 ticks per second; recordings are stored in wall-clock time
TICKS_PER_SECOND = TICKS_PER_BEAT * 1_000_000 / TEMPO
CHANNELS = {"Violin": 0, "Viola": 1, "Violoncello": 2, "Piano": 3}  # in a session file
PROGRAMS = {"Violin": 40, "Viola": 41, "Violoncello": 42, "Piano": 0}  # General MIDI


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
