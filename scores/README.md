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
