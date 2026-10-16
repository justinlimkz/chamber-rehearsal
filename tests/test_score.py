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
