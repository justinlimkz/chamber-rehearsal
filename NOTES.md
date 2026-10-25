# NOTES

Running log of decisions and tuning. See `HANDOFF.md` for the vision and milestones.

## Status (2026-10-25)

- **Milestone 1:** **passed at the piano (2026-10-25).** FP-30 found over USB as "Roland Digital Piano"; C major scale printed cleanly with timestamps, velocities and note-offs; `bars 1-8` prints all four parts. One stray C#6 at velocity 1 appeared at the start of the first run only (probably a brushed key); watch for it in takes.
- **Milestone 2:** **passed (2026-10-25).** Live `follow` on bars 1–16 tracked every half beat from bar 1 to bar 16 with no wrong jumps. On the three recorded takes (`evaluate --takes takes`, outerhmm), share of score onsets within 0.25 beat / 1 beat: clean 0.85 / 1.0, rubato 0.89 / 1.0, mistakes 0.83 / 0.99. Median error 0 on all three; worst 1.5 beats, at the very end of the mistakes take.
- **Milestone 3:** **passed at the piano (2026-10-25).** Three live runs of bars 1–16 (steady, rubato, with a stop), sessions in `build/sessions/` (`155158`, `155601`, `155725`). Every piano chord matched in order; strings never more than 60 ms late on any run. String minus piano where both play, bars 4–16: median −1 to +3 ms, 85–90% within 60 ms. Your tempo ranged ♩ = 57–75 in the rubato run. The outliers are the expected ones: the entrance at bar 4 after the strings' bars 2–3 (strings 0.2–1 s ahead: strings-lead, M5), strings carrying on ~2 beats into your stop before pausing (designed), and downbeats where you lean ~30 ms late. Your verdict: "worked pretty well".
- Milestones 4+ not started.

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
| `evaluate --takes takes [--first --last]` | accuracy on recorded takes against a reference alignment (`rehearsal/reference.py`) |
| `accompany [--first 1 --last 16]` | strings follow the piano live (M3 test); prints each matched chord, tempo and your timing vs the strings; saves the session (piano + strings) to `build/sessions/` |
| `session build/sessions/X.mid` | timing of a saved live session: strings vs piano where both play, worst notes |
| `accompany --replay take.mid [--listen]` | the same offline on a recorded take: timing stats, plus `build/<take>-with-strings.mid/.wav` |

Tests: `uv run pytest`.

## Layout

- `rehearsal/score.py`: partitura wrapper. Notes with bar and beat-in-bar, repeats unfolded, `bar_at(beat)`, `export_part()`.
- `rehearsal/midi_io.py`: `MidiEvent`, live input (timestamps taken in the rtmidi callback), take read/write, replay.
- `rehearsal/follower.py`: `Follower` protocol and `MatchmakerFollower`. Matchmaker is only imported here.
- `rehearsal/synth.py`: synthetic piano takes from the score with ground truth (rubato, jitter, wrong/dropped/extra notes).
- `rehearsal/evaluate.py`: scores the follower's alignment path against the ground truth.
- `rehearsal/reference.py`: offline ground truth for real takes: performed notes grouped into chords (50 ms gap), chord sequence aligned to score onset groups by edit distance on pitch sets.
- `rehearsal/tempo.py`: `BeatClock`, the tempo and position model: linear phase (`alpha`) and period (`beta`) correction at each piano chord.
- `rehearsal/accompanist.py`: `Accompanist`, event-driven and clock-agnostic (same code live and offline). Matches piano chords to the score, corrects the clock, schedules string notes ahead of predicted beats, pauses when the pianist stops.
- `rehearsal/sound.py`: `SoundEngine` protocol, `FluidSynthEngine` (Core Audio, 64 frames x 8 periods ≈ 10.7 ms), `render_wav()` via the fluidsynth CLI.
- `rehearsal/simulate.py`: replays a take through follower + accompanist in 1 ms virtual steps; timing vs the reference alignment; writes piano + strings MIDI.
- `rehearsal/session.py`: analysis of saved live sessions (piano aligned with `reference.align`; each string note assigned to the nearest same-pitch score note in expected time).
- `rehearsal/live.py`: the live loop: MIDI callback, follower thread, 1 ms scheduler poll.
- `takes/`: recorded takes from the FP-30 (bars 1–16: clean, rubato, mistakes).
- `scores/`: the four score files copied from Downloads. `build/` is scratch (piano-only MusicXML, eval log).

## Decisions

- **Project tooling:** `uv`, Python 3.12. Libraries: partitura 1.9.0, pymatchmaker 0.3.0, mido + python-rtmidi (MIDI I/O; mido is simple and rtmidi is what Matchmaker uses). Sound (M3): pyfluidsynth, now a direct dependency, needs the FluidSynth library: `brew install fluid-synth` (2.6.1). Speech recognition not chosen yet (M4).
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
- **Piano port name:** over USB the FP-30 shows up as "Roland Digital Piano", not "FP-30". `find_piano_input()` matches either (2026-10-25).
- Bar 1 is a full 3/4 bar with the first sound on beat 2, so bars 1–3 are the three-bar introduction and the cello starts at bar 4 (D3, C4, B♭3).

- **Ground truth for real takes: own chord aligner, not parangonar.** parangonar 3.4.0's `AutomaticNoteMatcher` was tried first. It needs unique score note ids (partitura gives all `None` here) and needed `tqdm` it doesn't declare. Even then it assigned single notes to the neighbouring identical chord in the repeated eighth-note chords (3 order violations in the mistakes take). Matching whole chords in order avoids that. Tested on synthetic takes: every match correct, at most one badly mangled chord unmatched.
- **Where the follower errs on real takes:** almost all errors are the follower running **one eighth ahead** inside bars of repeated identical chords (bars 4, 5, 8, 11), then snapping back on the next bar's bass note. The chords carry no pitch information to tell them apart, so the HMM advances on timing alone. Never behind. Matters for M3: smooth with the tempo model rather than trusting each estimate.

- **SoundFont:** MuseScore Studio's `MS Basic.sf3` (MIT), from the app bundle (`brew install --cask musescore`); GM programs 40/41/42. Loading it takes ~7 s at startup (sf3 samples are decompressed). Side effect noticed: once pyfluidsynth is importable, partitura downloads `MuseScore_General.sf3` (40 MB, MIT) into `.venv/.../partitura/assets/` on first import. One-off; not used by us.
- **M3 position tracking: chord matching, not the follower's raw estimate.** Piano notes within 40 ms form a chord (finished early once all expected notes are in). Each chord is matched to the piano part by pitch-set similarity minus penalties: 0.5 per beat from the clock's prediction, 0.4 per chord skipped, 0.6 per chord stepped back; the next expected chord is always a candidate, however long the rest before it. Late notes of the last chord (up to 30% of the way to the next) are ignored. The follower only corrects gross drift: 3 chords in a row more than 1 beat from it, and the position jumps to it. First tries snapped to the nearest onset by time alone and locked one eighth off after a rest or in rubato.
- **Audio buffer: 64 frames x 8 periods (≈ 10.7 ms).** With 2 or 4 periods FluidSynth ran (voices active, no error) but nothing came out of the MacBook Pro speakers; 8 plays (tested by ear, 2026-10-25). FluidSynth's default is 64 x 16 (≈ 21 ms).
- **Sandbox:** audio from commands run in Claude Code's sandbox is silent. Run `accompany` in a normal terminal.
- **M3 tempo: `alpha` 0.7, `beta` 0.3**, tempo change capped at 8% per chord, starting from the score's ♩ = 63. You play the opening at about ♩ = 82–85; the clock gets there within a bar of eighths.
- **M3 timing rules:** strings play at the predicted time; if the piano chord for that beat arrives first, they come in at once. Grace notes sound 70 ms before the beat. Notes stop at 95% of their length. Starting: strings at the first beat come in on your first note. Stopping (placeholder for M4): 2 beats past an expected chord with no piano, the strings stop and wait. On your next note they restart where its pitch fits best, from a beat and a half back to a beat on (preferring the next chord), and replay from there. Before this fix the steady run restarted an eighth late after you went back to bar 4's downbeat.

## Caveats

- The method comparison table is on **synthetic** takes only; outerhmm has been measured on real takes (above), the other methods not.
- "Lost" counts in the eval (onsets with no estimate in the 150 ms window) are 0–7 of 80; not yet examined.
- Matchmaker's file mode runs faster than real time; its live mode opens its own MIDI port (CoreMIDI lets us open it too). Its stream thread is not a daemon: `MatchmakerFollower.stop()` must be called or the process hangs on exit.
- Matchmaker's file mode hangs on a MIDI file with note-ons but no note-offs; always pass complete takes to `simulate.run`.
- **Offline M3 numbers are on solo takes**, recorded without strings. Bars 2–3 are the strings' alone (piano silent), so their timing there is the clock's own, and you could not have followed them; the bar-2 downbeat shows 0.4–1.1 s "early" for that reason. In the mistakes take the hesitations after wrong notes make the strings carry on 1–2 beats ahead before pausing, as designed. Real numbers need a live test.
- Bars 25 and 31 of the two transcriptions disagree in the piano part; settle against IMSLP before tuning on them (handoff item).

## Next

1. Milestone 4: stops and voice restarts. Stop behaviour (carry on 1–2 beats, then trail off rather than cut), local speech recognition for "from bar 9", "from the top", "again".
2. Worth revisiting in M5: the strings' bars 2–3 and the bar-4 entrance (strings lead; you came in 0.2–1 s after them), and the ~30 ms downbeat lean.
