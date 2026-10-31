# Can a model read a score page?

Before writing any music-recognition (OMR) software for scores that have no MusicXML (the Mozart K. 493), we test
whether frontier models can already transcribe a page well enough on their own. If they can, the "software" is a
prompt; if they nearly can, a thin harness of tools around them; only if they can't is it worth more. The benchmark
is the part meant to last: re-run it when new models come out.

- `pages/`: scanned pages with a known answer. `schumann-op47-iii-p24.png`: Schumann, Piano Quartet Op. 47, III,
  bars 1-26, from Robert Schumann's Werke (ed. Clara Schumann, Breitkopf & Härtel, 1880s; IMSLP #05770, public
  domain), 150 dpi. Same publisher and era as the Mozart scan.
- The answer: `scores/schumann-op47-iii.mxl` (the CC0 transcription). It may differ from this edition here and there,
  so a model's "error" is checked against the scan before it counts against it.
- `score.py`: compares a transcription with the answer bar by bar. A part-bar is right when its notes (pitch,
  onset in the bar, duration) match exactly; rests, grace notes, dynamics and spelling (G♭ = F♯) don't count.
  `python3 omr/score.py scores/schumann-op47-iii.mxl <transcription> 1 26 [--show]`
- `prompt-A.md`: approach A, the page and a prompt, nothing else. `runs/`: what each produced.

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

Next: repeat Opus on page 24 and on harder pages (25-29), then a Mozart page (no answer key: `rehearsal check` and
spot checks), and the cost per page against proofreading time.
