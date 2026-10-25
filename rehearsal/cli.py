"""Command line entry points. Run with: uv run python -m rehearsal.cli <command>"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import midi_io
from .score import DEFAULT_SCORE, Score, pitch_name


def cmd_ports(args) -> int:
    names = midi_io.list_inputs()
    print("\n".join(names) if names else "No MIDI inputs found. Is the FP-30 on and plugged in by USB?")
    return 0 if names else 1


def _stream(args):
    if args.replay:
        return midi_io.replay(midi_io.read_take(args.replay), realtime=True)
    return midi_io.live_events(args.port)


def cmd_monitor(args) -> int:
    """Print incoming notes with timestamps (the milestone 1 scale test)."""
    try:
        stream = _stream(args)
        print("Listening. Play something; Ctrl-C to stop.")
        for ev in stream:
            if ev.kind == "pedal":
                print(f"{ev.time:8.3f}s  pedal {'down' if ev.value >= 64 else 'up'} ({ev.value})")
            elif ev.kind == "note_on":
                print(f"{ev.time:8.3f}s  on   {pitch_name(ev.note):<4} vel {ev.value}")
            else:
                print(f"{ev.time:8.3f}s  off  {pitch_name(ev.note):<4}")
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


def cmd_record(args) -> int:
    events: list[midi_io.MidiEvent] = []
    try:
        print(f"Recording to {args.out}. Play; Ctrl-C to stop and save.")
        for ev in midi_io.live_events(args.port):
            events.append(ev)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    if events:
        # Start the take at the first event so replays begin immediately.
        t0 = events[0].time
        midi_io.write_take([midi_io.MidiEvent(e.time - t0, e.kind, e.note, e.value) for e in events], args.out)
        print(f"\nSaved {len(events)} events to {args.out}")
    else:
        print("\nNothing recorded.")
    return 0


def cmd_bars(args) -> int:
    score = Score(args.score)
    names = [args.part] if args.part else score.part_names
    for name in names:
        print(f"\n== {name} bars {args.first}-{args.last} ==")
        for bar, notes in score.bars(name, args.first, args.last).items():
            body = "  ".join(f"{n.beat_in_bar:g}:{pitch_name(n.pitch)}" for n in notes) or "(rest)"
            print(f"bar {bar:>3}  {body}")
    return 0


def cmd_follow(args) -> int:
    """Follow the piano part and show the current bar and beat (the milestone 2 live test)."""
    from .follower import MatchmakerFollower

    score = Score(args.score)
    port = None
    if not args.replay:
        port = args.port or midi_io.find_piano_input()
        if port is None:
            print(f"FP-30 not found. MIDI inputs: {midi_io.list_inputs() or 'none'}", file=sys.stderr)
            return 1
    follower = MatchmakerFollower(score, args.replay, method=args.method, port=port)
    try:
        for pos in follower.positions():
            print(f"bar {pos.bar:>3}  beat {pos.beat_in_bar:4.1f}   (score beat {pos.beat:.1f})", flush=True)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_evaluate(args) -> int:
    from . import evaluate

    score = Score(args.score)
    log = None if args.no_log else Path("build/eval.jsonl")
    if args.takes:
        rows = evaluate.run_recorded(score, Path(args.takes), args.first, args.last, args.method, log=log)
    else:
        rows = evaluate.run_scenarios(score, args.first, args.last, args.method, log=log)
    print(f"method={args.method} bars {args.first}-{args.last}")
    print(f"{'scenario':<14}{'seed':>4}{'<=.25b':>8}{'<=1b':>7}{'median':>8}{'max':>7}{'final':>7}{'lost':>6}{'secs':>6}")
    for r in rows:
        print(f"{r['scenario']:<14}{r['seed']:>4}{r['within_0.25_beat']:>8}{r['within_1_beat']:>7}"
              f"{r['median_abs_err_beats']:>8}{r['max_abs_err_beats']:>7}{r['final_err_beats']:>7}{r['no_estimate']:>6}{r['wall_s']:>6}")
    return 0


def cmd_accompany(args) -> int:
    """Strings follow the piano (the milestone 3 test). Live, or offline from a recorded take."""
    score = Score(args.score)
    if args.replay:
        from . import simulate
        from .sound import render_wav

        events = midi_io.read_take(args.replay)
        res = simulate.run(score, events, args.first, args.last)
        for k, v in simulate.summary(res).items():
            print(f"{k:<26}{v}")
        stem = Path(args.replay).stem
        mid = simulate.write_midi(events, res.outs, Path("build") / f"{stem}-with-strings.mid")
        wav = render_wav(mid, mid.with_suffix(".wav"))
        print(f"wrote {mid} and {wav}")
        if args.listen:
            import subprocess
            subprocess.run(["afplay", str(wav)])
        return 0
    from . import live

    port = args.port or midi_io.find_piano_input()
    if port is None:
        print(f"FP-30 not found. MIDI inputs: {midi_io.list_inputs() or 'none'}", file=sys.stderr)
        return 1
    saved = live.run(score, port, args.first, args.last)
    if saved:
        print(f"\nSession saved to {saved}")
    return 0


def cmd_session(args) -> int:
    """Timing of a saved live session: strings against the piano where both play."""
    import numpy as np

    from . import session

    score = Score(args.score)
    r = session.analyze(score, Path(args.path), args.first, args.last)
    a = np.array([x for _, _, x in r.rows]) * 1000
    print(f"piano chords matched {r.piano_matched}/{r.piano_groups}, extra {r.piano_extra}")
    print(f"string notes with a piano chord: {len(a)}; string minus piano: median {np.median(a):.0f} ms, "
          f"within 30 ms {np.mean(np.abs(a) <= 30):.0%}, within 60 ms {np.mean(np.abs(a) <= 60):.0%}, "
          f"late >60 ms {(a > 60).sum()}, early >60 ms {(a < -60).sum()}")
    worst = sorted(r.rows, key=lambda x: -abs(x[2]))[:8]
    for beat, part, x in worst:
        bar, bib = score.bar_at(beat)
        print(f"  bar {bar:>3} beat {bib:4.1f}  {part:<12}{x * 1000:+7.0f} ms")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rehearsal")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="list MIDI inputs").set_defaults(fn=cmd_ports)

    m = sub.add_parser("monitor", help="print incoming notes with timestamps")
    m.add_argument("--port")
    m.add_argument("--replay", help="replay a recorded take instead of live input")
    m.set_defaults(fn=cmd_monitor)

    r = sub.add_parser("record", help="record a take from the piano to a .mid file")
    r.add_argument("out")
    r.add_argument("--port")
    r.set_defaults(fn=cmd_record)

    b = sub.add_parser("bars", help="print score bars for each part")
    b.add_argument("--score", default=str(DEFAULT_SCORE))
    b.add_argument("--part")
    b.add_argument("--first", type=int, default=1)
    b.add_argument("--last", type=int, default=8)
    b.set_defaults(fn=cmd_bars)

    f = sub.add_parser("follow", help="follow the piano part live (or from a take) and print bar and beat")
    f.add_argument("--score", default=str(DEFAULT_SCORE))
    f.add_argument("--method", default="outerhmm")
    f.add_argument("--port")
    f.add_argument("--replay", help="a recorded take (.mid) instead of live input")
    f.set_defaults(fn=cmd_follow)

    e = sub.add_parser("evaluate", help="measure follower accuracy on synthetic takes, or on recorded takes with --takes")
    e.add_argument("--score", default=str(DEFAULT_SCORE))
    e.add_argument("--method", default="outerhmm")
    e.add_argument("--first", type=int, default=1)
    e.add_argument("--last", type=int, default=16)
    e.add_argument("--takes", help="folder of recorded .mid takes to score instead of synthetic ones")
    e.add_argument("--no-log", action="store_true")
    e.set_defaults(fn=cmd_evaluate)

    a = sub.add_parser("accompany", help="strings follow the piano, live (or offline from a take with --replay)")
    a.add_argument("--score", default=str(DEFAULT_SCORE))
    a.add_argument("--port")
    a.add_argument("--first", type=int, default=1)
    a.add_argument("--last", type=int, default=16)
    a.add_argument("--replay", help="a recorded take (.mid): simulate offline, print timing, render audio")
    a.add_argument("--listen", action="store_true", help="with --replay, play the rendered audio")
    a.set_defaults(fn=cmd_accompany)

    se = sub.add_parser("session", help="timing of a saved live session (build/sessions/*.mid)")
    se.add_argument("path")
    se.add_argument("--score", default=str(DEFAULT_SCORE))
    se.add_argument("--first", type=int, default=1)
    se.add_argument("--last", type=int, default=16)
    se.set_defaults(fn=cmd_session)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
