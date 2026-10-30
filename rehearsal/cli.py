"""Command line entry points. Run with: uv run rehearsal <command>"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import midi_io
from .score import DEFAULT_SCORE, Score, pitch_name


def cmd_bars(args) -> int:
    score = Score(args.score)
    names = [args.part] if args.part else score.part_names
    for name in names:
        print(f"\n== {name} bars {args.first}-{args.last} ==")
        for bar, notes in score.bars(name, args.first, args.last).items():
            body = "  ".join(f"{n.beat_in_bar:g}:{pitch_name(n.pitch)}" for n in notes) or "(rest)"
            print(f"bar {bar:>3}  {body}")
    return 0


def cmd_simulate(args) -> int:
    """The strings on a recorded take, offline, as the iPad would play them: timing, and a MIDI file to listen to."""
    from . import simulate
    from .tempo import BeatClock

    score = Score(args.score)
    events = midi_io.read_take(args.take)
    res = simulate.run(score, events, args.first, args.last, clock=BeatClock(spb=60 / args.tempo))
    for k, v in simulate.summary(res).items():
        print(f"{k:<26}{v}")
    out = Path(args.out) if args.out else Path("build") / f"{Path(args.take).stem}-with-strings.mid"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"wrote {simulate.write_midi(events, res.outs, out, res.accompanist.notices)}")
    return 0


def cmd_session(args) -> int:
    """Timing of a saved live session: strings against the piano where both play."""
    import numpy as np

    from . import session

    score = Score(args.score)
    r = session.analyze(score, Path(args.path), args.first, args.last)
    a = np.array([x for _, _, x in r.rows]) * 1000
    for p in r.passes:
        (b0, _), (b1, _) = score.bar_at(p.first_beat), score.bar_at(p.last_beat)
        pa = np.array([x for _, _, x in p.rows]) * 1000
        timing = f"median {np.median(pa):+4.0f} ms, within 60 ms {np.mean(np.abs(pa) <= 60):4.0%}" if len(pa) else "no strings"
        print(f"pass at {p.start:5.1f} s: bars {b0:>2}-{b1:<2}  piano chords {p.piano_matched:>3} (+{p.piano_extra} extra)  {timing}")
        for t, text in p.notices:
            print(f"    {t:6.1f} s  {text}")
    if not len(a):
        print("no string notes matched a piano chord")
        return 0
    print(f"string notes with a piano chord: {len(a)}; string minus piano: median {np.median(a):.0f} ms, "
          f"within 30 ms {np.mean(np.abs(a) <= 30):.0%}, within 60 ms {np.mean(np.abs(a) <= 60):.0%}, "
          f"late >60 ms {(a > 60).sum()}, early >60 ms {(a < -60).sum()}")
    worst = sorted(r.rows, key=lambda x: -abs(x[2]))[:8]
    for beat, part, x in worst:
        bar, bib = score.bar_at(beat)
        print(f"  bar {bar:>3} beat {bib:4.1f}  {part:<12}{x * 1000:+7.0f} ms")
    return 0


def cmd_check(args) -> int:
    """Suspicious bars in a MusicXML score: rhythms that don't add up, notes out of range, ties to nowhere."""
    from . import check

    problems = check.check(Path(args.score))
    for p in problems:
        print(p)
    bars = len({p.index for p in problems if p.bar})
    print(f"{len(problems)} problems in {bars} bars" if problems else "no problems found")
    return 1 if problems else 0


def cmd_bundle(args) -> int:
    """Pack a piece for the iPad app; with --fixtures, also the engine's golden test cases."""
    from . import bundle

    score = Score(args.score)
    out = bundle.export(score, Path(args.out))
    print(f"wrote {out}")
    if args.fixtures:
        takes = sorted(Path("takes").glob("*.mid"))
        sessions = [Path("build/sessions") / f"{s}.mid" for s in args.sessions]
        for path in bundle.fixtures(score, Path(args.fixtures), takes, sessions):
            print(f"wrote {path}")
    if args.import_fixtures:  # the test pieces and the scores, as the iPad's MusicXML import must read them
        sources = sorted(Path("tests/musicxml").glob("*.musicxml")) + sorted(Path("scores").glob("*.mxl"))
        for path in bundle.import_fixtures(sources, Path(args.import_fixtures)):
            print(f"wrote {path}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rehearsal")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bars", help="print score bars for each part")
    b.add_argument("--score", default=str(DEFAULT_SCORE))
    b.add_argument("--part")
    b.add_argument("--first", type=int, default=1)
    b.add_argument("--last", type=int, default=8)
    b.set_defaults(fn=cmd_bars)

    si = sub.add_parser("simulate", help="the strings on a recorded take (.mid), offline: timing and a MIDI file")
    si.add_argument("take")
    si.add_argument("--score", default=str(DEFAULT_SCORE))
    si.add_argument("--first", type=int, default=1)
    si.add_argument("--last", type=int, default=16)
    si.add_argument("--tempo", type=float, default=63.0, help="starting tempo (quarter notes per minute)")
    si.add_argument("--out", help="the MIDI file: piano and strings (default: build/<take>-with-strings.mid)")
    si.set_defaults(fn=cmd_simulate)

    se = sub.add_parser("session", help="timing of a saved session (a MIDI file from the iPad's Sessions folder)")
    se.add_argument("path")
    se.add_argument("--score", default=str(DEFAULT_SCORE))
    se.add_argument("--first", type=int, default=1)
    se.add_argument("--last", type=int, default=16)
    se.set_defaults(fn=cmd_session)

    ch = sub.add_parser("check", help="suspicious bars in a MusicXML score (after music recognition, say)")
    ch.add_argument("score")
    ch.set_defaults(fn=cmd_check)

    bu = sub.add_parser("bundle", help="pack a piece for the iPad app; also the test fixtures")
    bu.add_argument("--score", default=str(DEFAULT_SCORE))
    bu.add_argument("--out", default="ios/Bundles/schumann-op47-iii")
    bu.add_argument("--fixtures", help="also write the engine's golden test cases here (from takes/ and --sessions)")
    bu.add_argument("--sessions", nargs="*", default=["20261028-150330", "20261028-150451", "20261029-172728"],
                    help="saved live sessions (build/sessions/<name>.mid) to include as fixtures")
    bu.add_argument("--import-fixtures", help="also write what the iPad's MusicXML import must make, here")
    bu.set_defaults(fn=cmd_bundle)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
