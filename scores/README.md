# Scores

Schumann, Piano Quartet in E-flat major, Op. 47: a MuseScore transcription of the whole quartet by MuseScore
user 37403731, https://musescore.com/user/37403731/scores/10195096, released under the Creative Commons
copyright waiver (CC0).

| File | What |
|---|---|
| `piano-quartet-op47-robert-schumann.mscz`, `.mxl` | The transcription as published (all four movements). |
| `schumann-op47-iii.mxl` | Movement III, Andante cantabile (132 bars), cut from it: the app's built-in piece and the tests' score. |
| `schumann-op47-iii.piece.yaml` | How the library lists it. |
| `cut.py` | Cuts a movement out of a whole work. |

Movement III was made with:

```sh
uv run python scores/cut.py scores/piano-quartet-op47-robert-schumann.mxl 578 709 scores/schumann-op47-iii.mxl \
    "Piano Quartet in E-flat major, Op. 47" "III. Andante cantabile" --dim P1:1
```

One correction: in bar 1 the transcription has a crescendo under the violin's chord, where the viola, cello and
piano have the diminuendo from f to p; the violin gets the diminuendo too. The transcription's hidden tempo
changes are left in: the app ignores them.

## Mozart, Piano Quartet in E-flat major, K. 493, I. Allegro

`mozart-k493-i.mxl` (252 bars): transcribed from the scan of Mozarts Werke, Serie XVII No. 3 (Breitkopf & Härtel,
1879, public domain; IMSLP #364331, pages 4-15) by Claude (Opus) reading the page images, with no music-recognition
software: each page read twice independently, every bar where the two readings differed (32 of 1008 part-bars)
settled by looking at the scan again, then joined with `omr/join.py` (which adds printed accidentals and beams).
How well this works was measured first on Schumann pages with a known answer (`omr/README.md`).

Notes and rhythms only: no dynamics, slurs, articulations, ornaments or grace notes yet (so the strings play at one
level until dynamics are added). Not yet proofread by a person. Where to look first (bars as numbered in the file):

| Bar | Part | What |
|---|---|---|
| 86, 88 | Piano RH, beat 4 | F E♭ C: three eighths, or a dotted rhythm? A dot and a possible sixteenth stub in the print. |
| 137 | Cello | Second half note: printed over a damaged spot with a double flat (read A𝄫2). |
| 175 | Piano RH, beat 3 | B♮4 D5 F5 A♭5: both readings missed the natural; fixed by eye. Agreement isn't proof. |
| 64, 72 | Violin / piano | Quarter then eighths (a stray dot near the quarter). |
| 244 | Piano LH | B♭ E♭ G (the natural cancels a G♭). |
| throughout | | Ties vs slurs between equal notes: read as ties. |
