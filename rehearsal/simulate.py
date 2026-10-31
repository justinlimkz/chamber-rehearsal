"""Offline run of the accompanist on a recorded take: what the strings would have played, and how in time.

The take is replayed in virtual time, 1 ms per step, feeding piano onsets (and voice cues) to the
`Accompanist`, as the iPad app does live (ios/RehearsalCore's Simulator is the same in Swift). Timing is
measured against the reference alignment.

Caveat: the take was recorded without strings, so where the strings lead (bars 2-3, the cello's
entries) the pianist could not have followed them. Asynchrony there reflects that, not the scheduler.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido
import numpy as np

from . import midi_io, reference
from .accompanist import Accompanist, Out
from .score import Score
from .midi_io import CHANNELS, PROGRAMS
from .tempo import BeatClock
from .voice import Cue

STEP_S = 0.001


@dataclass
class SimResult:
    outs: list[Out]  # with `time` set to when the event was actually emitted
    asyn: list[tuple[float, float]]  # (score beat, string onset minus piano onset in seconds) where both play
    accompanist: Accompanist


def run(score: Score, events: list[midi_io.MidiEvent], first_bar: int = 1, last_bar: int = 16,
        clock: BeatClock | None = None, cues: list[tuple[float, Cue]] = ()) -> SimResult:
    inputs = [(e.time, 0, e.note) for e in events if e.is_onset]
    inputs += [(t, 1, c) for t, c in cues]
    inputs.sort(key=lambda x: x[:2])

    acc = Accompanist(score, first_bar, last_bar, clock=clock or BeatClock(spb=60 / 63))
    outs: list[Out] = []
    i, now, end = 0, 0.0, max(t for t, _, _ in inputs) + 5.0
    while now < end and not acc.finished:
        while i < len(inputs) and inputs[i][0] <= now:
            t, kind, x = inputs[i]
            if kind == 0:
                acc.on_piano_onset(t, int(x))
            else:
                acc.on_cue(t, x)
            i += 1
        outs += [Out(now, o.kind, o.part, o.pitch, o.velocity, o.beat) for o in acc.due(now)]
        now += STEP_S
    outs += acc.flush(now)

    ref = {b: t for t, b in reference.align(score, events, first_bar, last_bar).truth}
    asyn = [(o.beat, o.time - ref[o.beat]) for o in outs if o.kind == "on" and o.beat in ref]
    return SimResult(outs, asyn, acc)


def summary(res: SimResult) -> dict:
    a = np.array([x for _, x in res.asyn]) * 1000
    if len(a) == 0:
        return {"string_onsets_with_piano": 0}
    return {
        "string_onsets_with_piano": len(a),
        "median_ms": round(float(np.median(a)), 1),
        "mean_ms": round(float(np.mean(a)), 1),
        "within_30ms": round(float(np.mean(np.abs(a) <= 30)), 3),
        "within_60ms": round(float(np.mean(np.abs(a) <= 60)), 3),
        "late_over_60ms": int(np.sum(a > 60)),
        "early_over_60ms": int(np.sum(a < -60)),
        "final_spb": round(res.accompanist.clock.spb, 3),
    }


def write_midi(events: list[midi_io.MidiEvent], outs: list[Out], path: Path,
               notices: list[tuple[float, str]] = ()) -> Path:
    """Piano take plus the simulated strings, one channel each, for listening; notices become markers."""
    msgs: list[tuple[float, mido.Message]] = [(t, mido.MetaMessage("marker", text=text)) for t, text in notices]
    for part, ch in CHANNELS.items():
        msgs.append((0.0, mido.Message("program_change", channel=ch, program=PROGRAMS[part])))
    piano = CHANNELS["Piano"]
    for e in events:
        if e.kind == "pedal":
            msgs.append((e.time, mido.Message("control_change", channel=piano, control=64, value=e.value)))
        elif e.kind == "note_on":
            msgs.append((e.time, mido.Message("note_on", channel=piano, note=e.note, velocity=e.value)))
        else:
            msgs.append((e.time, mido.Message("note_off", channel=piano, note=e.note)))
    for o in outs:
        if o.kind == "click":  # the count-in: a woodblock on the drum channel
            msgs.append((o.time, mido.Message("note_on", channel=9, note=76 if o.velocity == 100 else 77, velocity=o.velocity)))
            msgs.append((o.time + 0.05, mido.Message("note_off", channel=9, note=76 if o.velocity == 100 else 77)))
            continue
        ch = CHANNELS[o.part]
        if o.kind == "expr":
            msgs.append((o.time, mido.Message("control_change", channel=ch, control=11, value=o.velocity)))
        else:
            kind = "note_on" if o.kind == "on" else "note_off"
            msgs.append((o.time, mido.Message(kind, channel=ch, note=o.pitch, velocity=o.velocity)))
    mid = mido.MidiFile(ticks_per_beat=midi_io.TICKS_PER_BEAT)
    track = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=midi_io.TEMPO, time=0)])
    last = 0
    for t, msg in sorted(msgs, key=lambda x: x[0]):
        tick = round(max(t, 0.0) * midi_io.TICKS_PER_SECOND)
        msg.time = tick - last
        last = tick
        track.append(msg)
    mid.tracks.append(track)
    mid.save(str(path))
    return path
