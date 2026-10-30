# Tone tests

How the strings' sound was chosen: every change to how the strings sound (the SoundFont, legato, vibrato,
dynamics, EQ) is rendered through the app's own engine and sampler, then kept only if it wins a blind listening
round. The rounds and their results are in `docs/NOTES.md`. None of this is needed to build or use the app.

| Path | What |
|---|---|
| `render/` | A command-line renderer built from the app's own `Strings.swift` and `ios/RehearsalCore`, so a render is what the iPad plays. |
| `clips.py`, `candidates.py` | The fixed test clips, and the candidate sounds to render them with. |
| `listening/` | A blind listening page for a round: hidden versions of each clip, rated by ear. |
| `scorecard.py`, `reference.py` | An objective scorecard: renders measured against real string recordings. |
| `compare/` | Real recordings of Op. 47 III against ours, at the same bars and tempo. |
| `fetch.py`, `fonts.py`, `samples.py`, `sf2.py` | SoundFonts built from free sample libraries, to compare with MS Basic. |
| `dynamics_variants.py` | The piece's loudness curves with other settings. |
| `ceiling_ddsp.py` | A reference only: the violin through Google's DDSP model. |

Recordings and samples are downloaded when the scripts run, into `build/tone/` (not part of the repository).
Their sources and licences are listed at the top of `fetch.py` and `reference.py`. Most scripts run with their
own dependencies, given on their first lines (`uv run --no-project --with ...`).
