# Can a model read a score page?

Before writing any music-recognition (OMR) software for scores that have no MusicXML (the Mozart K. 493), we test
whether frontier models can already transcribe a page well enough on their own. If they can, the "software" is a
prompt; if they nearly can, a thin harness of tools around them; only if they can't is it worth more. The benchmark
is the part meant to last: re-run it when new models come out.

- `pages/`: scanned pages with a known answer: Schumann, Piano Quartet Op. 47, III, page 24 (bars 1-26, `1 26` for
  score.py), 25 (bars 27-47, `27 21`), 26 (bars 48-74, `48 27`: six flats, 4/4, voices, a repeat with endings), from Robert Schumann's Werke (ed. Clara Schumann, Breitkopf & Härtel, 1880s; IMSLP #05770, public
  domain), 150 dpi. Same publisher and era as the Mozart scan.
- The answer: `scores/schumann-op47-iii.mxl` (the CC0 transcription). It may differ from this edition here and there,
  so a model's "error" is checked against the scan before it counts against it.
- `score.py`: compares a transcription with the answer bar by bar. A part-bar is right when its notes (pitch,
  onset in the bar, duration) match exactly; rests, grace notes, dynamics and spelling (G♭ = F♯) don't count.
  `python3 omr/score.py scores/schumann-op47-iii.mxl <transcription> 1 26 [--show]`
- `prompt-A.md`: approach A, the page and a prompt, nothing else. `runs/`: what each produced.
- `agree.py`: where two readings of a page differ. `join.py`: pages into one score (with `accidentals.py` and
  `beams.py`, since the readings carry only pitches and durations).

## Results, page 24 (2026-10-30)

| | Part-bars right | Bars right in all 4 parts | Notes F1 | Time | Tokens |
|---|---|---|---|---|---|
| Audiveris 5.11 | 17% | 0/26 | 69% | 1 min | – |
| Sonnet (A) | 21% | 0/26 | 33% | 1.5 min | 52k |
| Fable (A) | 66% | 3/26 | 70% | 108 min | 398k |
| **Opus (A)** | **99%** | **25/26** | **99.9%** | 19 min | 247k |

One run each, one page: a first look, not a measurement of variance. Opus's one miss: cello bar 22, G4 for A4. Opus
and Fable both wrote their own zoom and pixel-measuring scripts unprompted (the page is ~5.5 px between string
staff lines after the image is downscaled), so "approach A" already turned into a model building its own tools.
Sonnet looked once and wrote placeholder chords for the piano.

### Repeats and harder pages (2026-10-30, Opus only)

| Page | Run | Part-bars right | Bars right in all 4 | Notes F1 | Time | Tokens |
|---|---|---|---|---|---|---|
| 24 | 1 | 99% (103/104) | 25/26 | 99.9% | 19 min | 247k |
| 24 | 2 | 93% (97/104) | 19/26 | 93.8% | 17 min | 212k |
| 24 | 3 | 96% (100/104) | 22/26 | 95.3% | 16 min | 223k |
| 25 | 1 | 85% (71/84) | 11/21 | 95.0% | 34 min | 318k |
| 26 | 1 | 86% (93/108) | 15/27 | 96.7% | 34 min | 401k |

- Nearly every miss is one or two notes a step off, mostly inner notes of the piano's close chords, sometimes a viola
  note or a dotted rhythm; bar structure, key, time, voices, the repeat and both endings were all right.
- Three runs of page 24 voted bar by bar: the 94 part-bars all three agreed on were all right; the majority reading
  is right in 102/104. So disagreement between runs finds the doubtful bars, and the models' own "unsure" lists
  named most of their misses too.
- Not yet adjudicated: whether some misses on pages 25-26 are the reference's errors (one checked: p. 25 bar 46,
  violin, the scan has the B♭3+D4 double stop the reference has; the model wrote C4).
- The page images are 150 dpi; the models say the close chords are where resolution runs out (they zoom in
  themselves, but only on what they're shown).

### 300 dpi (2026-10-31)

Page 24 at 300 dpi, one Opus run: 99% of part-bars (103/104), 99.6% of notes, against 93-99% at 150 dpi.

## The Mozart (2026-10-31)

K. 493, I, all 12 pages (252 bars), 300 dpi: two independent Opus readings per page (8-18 min, 125-270k tokens
each), `agree.py` on each pair, then an Opus pass (or me) settling each disagreement from the scan. The pairs
agreed on 976 of 1008 part-bars (97%); two pages agreed completely. Of the 32 disputes, about half went each way;
one bar both readings had wrong (a missed natural) was caught by eye. `rehearsal check` finds nothing in the result.
About 5M tokens and 1.5 hours in all, nearly all of it parallel. Result: `scores/mozart-k493-i.mxl`, with the bars
to proofread first in `scores/README.md`.

What we learnt: a frontier model reading the page is already far better than Audiveris here (17% of part-bars right
on the Schumann page vs 93-99%), and two readings plus a look at their disagreements gets most of the rest. The
"software" is three small scripts (agree, join, and the scorer) and a prompt; better models will make it cheaper,
not obsolete. Dynamics were added that way (2026-10-31): one Opus pass per page reading only the markings into JSON
(`directions.py` writes them in at their beats), about 1-5 minutes and 60-70k tokens a page. Still missing: slurs and
ornaments.
