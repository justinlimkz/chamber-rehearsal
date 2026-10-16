# NOTES

Running log of decisions and tuning. See `HANDOFF.md` for the vision and milestones.

## Status (2026-10-16)

- **Milestone 1:** built and passing offline tests. **Not yet tested at the piano**: the FP-30 did not show up as a MIDI input (`uv run python -m rehearsal.cli ports` printed none), so check that it is switched on and connected by USB.
- **Milestone 2:** built. Matchmaker follows the piano part offline on synthetic takes. **Not yet tested live or on real recordings.**
- Milestones 3+ not started, as instructed.

## Commands

All via `uv run python -m rehearsal.cli <cmd>`:

| Command | What it does |
| --- | --- |
| `ports` | list MIDI inputs |
| `monitor` | print incoming notes with timestamps (M1 scale test); `--replay take.mid` to replay |
| `record out.mid` | record a take from the piano (Ctrl-C to save) |
| `bars --first 1 --last 8 [--part Piano]` | print score bars per part (M1 score test) |
| `follow` | follow the piano part live, print bar and beat (M2 live test); `--replay take.mid` for a file |
| `evaluate --first 1 --last 16 [--method M]` | accuracy on synthetic takes; appends to `build/eval.jsonl` |

Tests: `uv run pytest`.

## Layout

- `rehearsal/score.py`: partitura wrapper. Notes with bar and beat-in-bar, repeats unfolded, `bar_at(beat)`, `export_part()`.
- `rehearsal/midi_io.py`: `MidiEvent`, live input (timestamps taken in the rtmidi callback), take read/write, replay.
- `rehearsal/follower.py`: `Follower` protocol and `MatchmakerFollower`. Matchmaker is only imported here.
- `rehearsal/synth.py`: synthetic piano takes from the score with ground truth (rubato, jitter, wrong/dropped/extra notes).
- `rehearsal/evaluate.py`: scores the follower's alignment path against the ground truth.
- `scores/`: the four score files copied from Downloads. `build/` is scratch (piano-only MusicXML, eval log).

## Decisions

- **Project tooling:** `uv`, Python 3.12. Libraries: partitura 1.9.0, pymatchmaker 0.3.0, mido + python-rtmidi (MIDI I/O; mido is simple and rtmidi is what Matchmaker uses). Added later, for M3: pyfluidsynth came in as a Matchmaker dependency and is the likely sound route. Speech recognition not chosen yet (M4).
- **Matchmaker takes a score file and merges all parts**, so the follower is given a piano-only MusicXML exported from the written part (`build/piano.musicxml`). Matchmaker unfolds repeats itself; our `Score` unfolds its own copy the same way so bar lookups agree.
- **Follower method: `outerhmm`.** On synthetic takes of bars 1–16 (3 seeds each) share of onsets within 0.25 beat of truth:

  | Method | clean | messy (wrong+dropped+extra notes, rubato, jitter) |
  | --- | --- | --- |
  | outerhmm | 0.92–0.98 | 0.78–0.85 |
  | pthmm (Matchmaker default) | 0.46–0.52 | 0.51–0.69 |
  | hmm | 0.46–0.53 | 0.53–0.69 |
  | dixon | 0.25–0.51 | 0.31–0.55 |
  | arzt | 0.35–0.39 | 0.37–0.47 |

  Within 1 beat is ~100% for outerhmm, pthmm and hmm. The other methods are consistently half a beat off about half the time, on eighth-note chords. Cause not investigated; we just don't use them.
- **Repeat structure:** unfolding gives bars 64–70, then 64–68, then 71 onward (first and second endings). The handoff said "bars 64–70 are repeated"; the first-ending detail is slightly different.
- Bar 1 is a full 3/4 bar with the first sound on beat 2, so bars 1–3 are the three-bar introduction and the cello starts at bar 4 (D3, C4, B♭3).

## Caveats

- All accuracy numbers are on **synthetic** takes. The synthetic player is metronome-like with a gentle sine rubato; real playing will be harder. Re-measure on recordings from the FP-30 once we have them (`record`, then drop the takes in `takes/` and extend `evaluate`).
- "Lost" counts in the eval (onsets with no estimate in the 150 ms window) are 0–7 of 80; not yet examined.
- Matchmaker's file mode runs faster than real time; its live mode opens its own MIDI port.
- Bars 25 and 31 of the two transcriptions disagree in the piano part; settle against IMSLP before tuning on them (handoff item).

## Next

1. At the piano: `ports`, `monitor` with a C major scale, `bars`, then `record` a take of bars 1–16 and `follow` live, including a few deliberate wrong notes.
2. Add real takes to the evaluation. Check how outerhmm copes with the sparse opening played with real rubato.
3. Then milestone 3.
