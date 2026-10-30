# Chamber Music Rehearsal Tool — Handoff

Oct 16, 2026 · @Justin

## Vision

A rehearsal partner, not a backing track: I play the piano part on my digital piano, and a virtual violin, viola and cello play the other three parts with me in real time.

It should feel like rehearsing with real players:

- They follow my rubato, hesitations and tempo changes.
- They tolerate wrong notes without losing their place.
- They sometimes **lead**, so I practise following them, not just being followed.
- When I stop, they carry on briefly, like players who haven't noticed yet, then trail off.
- When I want to restart, I say where from ("from bar 9"), as I would in a real rehearsal.

The project is for personal use and personal interest. Musical feel matters more than feature count.

## Setup and constraints

| Item | Decision |
| --- | --- |
| Computer | Mac (macOS) |
| Instrument | Roland FP-30 (original, not FP-30X), connected by USB. It shows up as a class-compliant MIDI device. Bluetooth MIDI also exists but adds latency; prefer USB. |
| Input | MIDI only: note on/off, velocity, damper pedal (CC64). No audio analysis needed. |
| Piano sound | The FP-30's own speakers play my part. Software produces only the strings. |
| Language | Python. I am a very experienced Python programmer. |
| Budget | Free only, for now. No paid libraries, plugins or sample sets. |
| Latency target | Strings audio under about 20 ms from scheduling to sound; MIDI input handling well under that. |

## Starting repertoire and score source

First piece: Schumann, Piano Quartet in E-flat major, Op. 47, III. Andante cantabile (B-flat major), starting with its opening minute or two. The cello carries the opening melody while the piano supports, so the very first passage exercises the **strings-lead** mode.

Later candidates: Mozart Piano Quartet K. 493, II. Larghetto; Brahms Piano Quartet No. 3, Op. 60, III. Andante (stretch goal).

Score plan:

1. **Primary score:** `piano-quartet-op47-3rd-mov-robert-schumann.mxl` (movement III only, MuseScore 3.6.2 export), from [this transcription](https://musescore.com/user/1958691/scores/7191924). The `.mscz` source sits beside it for corrections in MuseScore Studio.
2. **Checked so far:** 132 bars; parts Violin, Viola, Violoncello, Piano (two staves); 3/4, 4/4 from bar 48, 3/4 from bar 73; B-flat major, G-flat major middle section from bar 48; one tempo mark, quarter = 63. Loads cleanly in partitura. Three-bar introduction, then the cello's rising-seventh melody from bar 4. Cello retuning note at bar 76, with the cello resting bars 76–90.
3. **Second transcription:** `piano-quartet-op47-robert-schumann.mxl` (all four movements, from [this transcription](https://musescore.com/user/37403731/scores/10195096)). Use it only for cross-checking: its bar numbers restart at each movement, and movement III carries about 20 hidden tempo changes that fake ritardandos on playback.
4. **Known disagreements:** the string parts match in all but 4 bars. The piano differs in 16 bars, most of them single notes or rhythms (e.g. bar 49 G♯ vs F♯, bar 63 B vs C, bar 81 E♭ vs D, bar 100 a run shifted by a sixteenth, bars 120–123). In the opening section only **bars 25 and 31** disagree. Settle those against the public-domain scan on [IMSLP](<https://imslp.org/wiki/Piano_Quartet,_Op.47_(Schumann,_Robert)>) before tuning the follower on them; the rest can wait until we reach those bars.
5. **Repeat:** bars 64–70 are repeated. partitura doesn't unfold repeats by default, so unfold them before score following.
6. **Scordatura:** the cello retunes its low C string down to B-flat near the end. Don't assume standard string ranges in the score model.

## Prior art and what we reuse

No existing open-source project does this exactly, but score following and score parsing are solved and reusable. We build on them and spend our effort on what is new.

| Project | What it is | How we use it |
| --- | --- | --- |
| [Matchmaker](https://pypi.org/project/pymatchmaker/) (JKU Linz + KAIST) | Python library of real-time score followers; reports score position in beats; has a MIDI mode | **Our score follower.** Try it first; write our own only if it fails on this piece |
| [partitura](https://github.com/CPJKU/partitura) (JKU Linz) | Python library for MusicXML/MIDI parsing and note arrays | **Our score model.** Matchmaker already uses it |
| [ACCompanion](https://github.com/CPJKU/accompanion) (JKU Linz) | Open-source expressive MIDI accompanist for piano duets; the human always leads | **Reference design** for prediction, scheduling and expressive output. Read, don't fork: its piano-duet assumptions run deep |
| Adaptive metronome / ARME project (Birmingham, MMU) | Research on virtual string players with mutual timing adaptation via a linear phase correction model | **Model for the leadership coupling.** No released code found; the published model is simple to implement |
| Antescofo / Metronaut (IRCAM) | Mature score follower for Max; Metronaut is a proprietary app built on it | Not used: Max-based, not open Python |

What is new here: the human plays piano with a virtual string trio, leadership shifts between parts, restarts are voice-cued, and players carry on briefly when I stop.

## Musical behavior spec

The strings track my position and tempo continuously, but how much they defer to me depends on who is leading at that moment.

**Leadership.** Each passage in the score carries a leadership setting, authored in a small annotation file alongside the MusicXML:

| Setting | Strings' tempo comes from | Coupling to me |
| --- | --- | --- |
| Piano leads | My playing | Tight: they follow within a beat |
| Strings lead | Their own expressive tempo plan | Loose: they wait slightly if I fall behind, but keep their phrase shape |
| Shared | A blend of both | Mutual: each side corrects toward the other |

The coupling strength should be a continuous parameter, not three hard modes, so passages can be tuned and transitions are smooth. Implement it as a linear phase correction model, as in the ensemble-timing research (see Prior art): each side corrects its next note time by a weighted fraction of its asynchrony with the other.

**Strings' expressive tempo plan.** Version 1: hand-authored tempo curve (ritardandi, breaths at phrase ends). Later: extracted by aligning a professional recording to the score, so the virtual cellist phrases like a real one.

**Anticipation.** The strings must predict my next beat, not react to it. Schedule string notes ahead using the tempo estimate; a purely reactive system always sounds late.

**Mistakes.** Wrong, missing and extra notes must not derail the position estimate. The follower matches probabilistically against the expected piano part.

**When I stop.** If no expected piano onsets arrive, the strings keep going for roughly one to two beats (tunable), then trail off by finishing or tapering their current notes, never cutting off abruptly. They then wait silently.

**Restarts by voice.** While stopped, I say where to start, in rehearsal language:

- "From bar 9" / "from measure 9"
- "From the pickup to bar 9"
- "From the top" / "from the beginning"
- "Again" (restart from the last start point)
- Later: rehearsal letters, "from the cello entrance", named spots I define

After a cue, the system relocates to that point and waits for my first notes (or, in a strings-lead passage, gives a short visual or audible count-in, to be decided). Speech recognition runs locally and offline. Voice is only listened for while stopped, so the piano sound doesn't trigger it.

**Fallback.** If I simply start playing elsewhere without a cue, the follower should try to find me by searching the score for my recent notes. It must not jump on weak evidence.

**On-screen view.** A simple display of the current bar and beat, the follower's confidence, and the current leadership setting. Clicking a bar is a quiet backup to voice cues.

## Architecture

Seven components, each testable on its own against recorded MIDI takes. The follower is Matchmaker and the score model is partitura; the rest is ours.

```
Roland FP-30 ──MIDI──▶ Score follower (Matchmaker) ──position──▶ Tempo & position model
 (USB MIDI: notes,        "Where am I in the score?"               current tempo, next beat;
  velocity, pedal)        tolerates wrong/missed/extra notes       blends my timing with the
                                   ▲                               strings' plan by coupling
                                   │ expected piano part                      │
Voice cues ──relocate to bar──┐    │                                          ▼
 (local speech recognition;   └──▶ Score & annotations (partitura)  ──▶ Accompaniment scheduler
  "from bar 9", "again";            MusicXML: all four parts;            plays string parts ahead
  listens only while stopped)       leadership per passage;              of predicted beats;
                                    strings' tempo plans                 carries on, then trails off
                                                                                  │
                                                                                  ▼
                                                                  Sound engine (swappable)
                                                                   SoundFont now; samples,
                                                                   modeling or neural later
```

The follower answers "where am I"; the tempo model answers "when is the next beat"; the scheduler turns that prediction into string notes slightly ahead of time. Voice cues bypass the follower's search and tell it directly where to restart.

## Milestones

Each milestone ends with a test I can run at the piano. Do not start the next until the current one passes.

1. **MIDI in and score loaded.** Read the FP-30 over USB; load the MusicXML with partitura (notes, onsets in beats, bar numbers, parts). Record and replay my playing as MIDI files for offline testing.
   - Test: playing a C major scale prints notes with timestamps; the score model prints bars 1–8 of each part correctly.
2. **Matchmaker follows me, no sound.** Run Matchmaker in MIDI mode on the piano part of the opening, with a live display of bar and beat.
   - Test: on clean playing, position stays correct; with a few deliberate wrong notes, it does not lose me; offline accuracy on recorded takes is measured and logged.
   - If Matchmaker struggles with the sparse opening, try its other alignment methods before writing our own follower.
3. **Strings follow me.** Strings play their parts in a piano-led stretch, with tempo prediction and basic sounds. Use ACCompanion's prediction and scheduling code as a reference.
   - Test: when I slow down and speed up, they stay together with me and never sound consistently late.
4. **Stops and voice restarts.** Stop behavior (carry on briefly, trail off) and voice cues.
   - Test: stop mid-phrase, say "from bar 5", start again; strings come in with me. "From the top" and "again" work.
5. **Strings lead.** Opening cello melody with an authored tempo plan and the coupling model (linear phase correction).
   - Test: the cello shapes its phrase on its own; if I lag, it waits a little but doesn't collapse to my tempo.
6. **Sound and polish.** Better instruments, dynamics from the score, natural note endings, recording-derived tempo plans.

A headless test harness that replays recorded MIDI takes through the follower is part of milestone 1, not an afterthought. Most tuning will happen against recordings, not live.

## Sound engine options

The sound engine is a swappable component behind one interface: it receives note events plus continuous expression (dynamics, vibrato depth) per instrument. The musical logic never knows which engine is in use.

| Option | What it is | When | Trade-off |
| --- | --- | --- | --- |
| SoundFont via FluidSynth | Free General MIDI or orchestral SoundFonts, driven from Python | Milestones 3–5 | Fastest to set up; strings sound stiff |
| Free sample libraries (SFZ) | e.g. VSCO 2 Community Edition, Virtual Playing Orchestra, via an open-source SFZ player such as sfizz | Milestone 6 | Better tone; solo legato still limited |
| Physical modeling | A simulated bowed string (digital waveguide; open-source references such as STK's bowed-string model) | Experiment | Natural continuous expression; sounding good is a research project |
| Neural synthesis | Pitch-and-loudness-to-audio models such as Google Magenta's DDSP | Experiment | Most promising "AI builds the instrument" route; real-time usability must be verified |

Verify current availability and licences of each before relying on it. Paid options (e.g. physically modeled SWAM strings) are out of scope while the free-only constraint holds.

## Open questions and risks

- [ ] Count-in after a voice restart in a strings-lead passage: visual cue, a sniff/breath sound, or one silent beat?
- [ ] How the annotation file for leadership and tempo plans is written (YAML by bar range is the default proposal).
- [ ] Which speech-recognition model runs locally on the Mac with acceptable delay for short phrases.
- [ ] Whether the MuseScore transcription is accurate enough, or the opening needs correcting by hand.

Risks:

- **Sparse piano part in the opening.** Supporting chords give the follower fewer distinctive notes than a melody. Mitigation: lean on the tempo model's expected timing between onsets.
- **Python timing jitter.** Garbage collection and the GIL can delay scheduled notes. Mitigation: schedule ahead with timestamps, keep the audio callback minimal, or move the audio thread to a compiled synth.
- **False relocations.** Jumping to the wrong bar is worse than staying lost. Require strong evidence before any automatic jump; voice cues are the primary path.
- **Sound quality ceiling.** Free solo strings may sound synthetic on the exposed cello line. Accept for now; the engine is swappable.

## Instructions for the Claude Code session

Paste this doc into a new Claude Code session in an empty project folder, then:

1. Read the whole doc before writing code. Ask me about anything ambiguous, but don't re-litigate settled decisions (Mac, FP-30 over USB MIDI, Python, free only, voice restarts, the Schumann opening, building on Matchmaker and partitura).
2. Install Matchmaker and partitura and confirm they run on macOS with Python 3.12. Clone ACCompanion read-only as a reference. Propose the remaining libraries for MIDI I/O, audio output and local speech recognition, one line of justification each.
3. Build milestones 1 and 2 only, including the record-and-replay test harness. Stop and let me test at the piano before milestone 3.
4. Keep the follower, tempo model, scheduler and sound engine as separate modules with clear interfaces, so each can be tested offline from recorded takes. Wrap Matchmaker behind our own follower interface so it can be swapped.
5. Keep a running `NOTES.md` in the repo with decisions made and parameters tuned, so future sessions can pick up where we left off.

I will provide: both MusicXML files and the `.mscz` sources (currently in my Downloads folder; copy them into the repo's `scores/` folder), and access to the FP-30 plugged into the Mac.
