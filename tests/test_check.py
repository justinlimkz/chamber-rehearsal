from pathlib import Path

from rehearsal import check
from rehearsal.score import DEFAULT_SCORE

BASIC = Path(__file__).parent / "musicxml" / "basic.musicxml"


def test_good_scores_are_clean():
    assert check.check(BASIC) == []
    assert check.check(DEFAULT_SCORE) == []


def test_finds_what_music_recognition_gets_wrong(tmp_path):
    text = BASIC.read_text()
    # a quarter read as a half (bar 1 overfull), an octave too low (out of the violin's range), a tie lost
    text = text.replace("<step>A</step><octave>4</octave></pitch><duration>2</duration><type>quarter</type>",
                        "<step>A</step><octave>4</octave></pitch><duration>4</duration><type>half</type>", 1)
    text = text.replace("<step>E</step><octave>5</octave></pitch><duration>2</duration>",
                        "<step>E</step><octave>3</octave></pitch><duration>2</duration>", 1)
    text = text.replace('<duration>4</duration><type>half</type><tie type="stop"/>',
                        "<duration>4</duration><type>half</type>", 1)
    path = tmp_path / "omr.musicxml"
    path.write_text(text)
    found = {(p.bar, p.part, p.text) for p in check.check(path)}
    assert ("1", "Violin", "5 beats in a bar of 4 beats") in found
    assert ("2", "Violin", "E3 is out of range") in found
    assert ("2", "Violin", "a tie on F#5 leads nowhere") in found
    assert len(found) == 3
