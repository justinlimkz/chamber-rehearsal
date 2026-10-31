Approach A, the prompt each model got (run as a Claude Code subagent; `<dir>` was a directory holding only page.png).

> You are a test subject in a music-recognition experiment: how accurately can you transcribe a printed score page by looking at it?
>
> The page: `<dir>/page.png` — one page of a piano quartet (violin, viola, cello, piano), 19th-century engraving. Look at it with the Read tool (you may view it as often as you like).
>
> Task: transcribe every bar on the page into one MusicXML file (score-partwise) with four parts in this order: Violin, Viola, Violoncello, Piano (piano as one part with two staves). Number the bars from 1 (the first bar on the page is bar 1). What gets scored: every note's pitch (including accidentals and key signature), its onset within the bar and its duration, in each part, bar by bar. Rests must be present so the timing is right, but dynamics, slurs, articulations and text are not scored; skip them. Grace notes are not scored. Ties: write a tied note as a note in each bar.
>
> Write the result to `<dir>/out.musicxml`. Writing MusicXML by hand is long: you may write and run a Python script (standard library only, python3) in that same directory that generates it from a compact notation of your own; keep the script there too. Make sure the file parses as XML.
>
> Rules (this is a blind test): read ONLY page.png and files you create yourself in that directory. Do not read, search or list any other file or directory on this computer (in particular nothing in /Users/justin/work), do not use the web, and do not run any music-recognition software. Use only your own eyes on the image.
>
> When done, reply with: how many bars you found, and any bars or passages you were unsure about.
