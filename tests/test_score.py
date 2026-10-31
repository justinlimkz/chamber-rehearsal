from pathlib import Path

import pytest

from rehearsal.score import Score


@pytest.fixture(scope="module")
def score():
    return Score()


def test_parts_and_length(score):
    assert score.part_names == ["Violin", "Viola", "Violoncello", "Piano"]
    assert max(n.bar for n in score.notes("Piano")) >= 131


def test_cello_melody_from_bar_4(score):
    bar4 = [n for n in score.notes("Violoncello") if n.bar == 4]
    assert [n.pitch for n in bar4] == [50, 60, 58]  # D3 C4 Bb3
    assert [n.beat_in_bar for n in bar4] == [1.0, 2.0, 3.5]


def test_beats_within_bar_after_unfolding(score):
    for name in score.part_names:
        assert all(1.0 <= n.beat_in_bar < 5.0 for n in score.notes(name))


def test_repeat_unfolded(score):
    folded = Score(unfold=False)
    assert len(score.notes("Piano")) > len(folded.notes("Piano"))


def test_parts_told_apart_in_other_languages_and_by_midi_program():
    from rehearsal.musicxml import instrument
    assert instrument(["Viool"]) == "Violin"
    assert instrument(["Violoncelle"]) == "Violoncello"
    assert instrument(["Cello"]) == "Violoncello"
    assert instrument(["Violino        \n(flauto traverso)"]) == "Violin"
    assert instrument(["Pianoforte"]) == "Piano"
    assert instrument(["ヴィオラ ダ ガンバ"], 43) == "Violoncello"  # names no help: the MIDI program says
    assert instrument(["ハープシーコード"], 7) == "Piano"
    assert instrument(["Flute"], 74) is None


def test_outside_scores_read_with_their_strings_and_real_titles():
    trio = Score(Path("tests/musicxml/outside/beethoven-op1-1-trio-i.mxl"))  # "Viool", "Cello", "Piano"
    assert trio.has("Violin") and trio.has("Violoncello") and not trio.has("Viola")
    kreutzer = Score(Path("tests/musicxml/outside/beethoven-kreutzer-i.mxl"))  # work title "Title", creator "Composer"
    assert kreutzer.title.startswith("Violin Sonata No. 9")
    assert kreutzer.composer == "Ludwig van Beethoven"
