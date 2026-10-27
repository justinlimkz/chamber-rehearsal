import pytest

from rehearsal import annotate
from rehearsal.score import Score


@pytest.fixture(scope="module")
def score():
    return Score()


@pytest.fixture(scope="module")
def passages(score):
    return annotate.annotate(score)


def passage_at(passages, bar):
    return next(p for p in passages if p.first_bar <= bar <= p.last_bar)


def test_bars_cover_the_movement(score, passages):
    assert passages[0].first_bar == 1
    assert all(a.last_bar + 1 == b.first_bar for a, b in zip(passages, passages[1:]))
    assert passages[-1].last_bar == 132


def test_violin_alone_leads_the_opening(passages):
    p = passage_at(passages, 3)  # piano silent: the violin's line
    assert (p.lead, p.coupling) == ("strings", annotate.COUPLING["strings"])


def test_cello_melody_leads_over_repeated_piano_chords(passages):
    p = passage_at(passages, 4)
    assert p.first_bar == 4 and p.last_bar >= 16
    assert (p.lead, p.coupling) == ("strings", annotate.COUPLING_OVER_PULSE)
    assert "Violoncello" in p.reason and "chords" in p.reason


def test_texture_features(score):
    bars = {b.number: b for b in annotate.bars(score)}
    rh = bars[5].piano[0]  # eighth-note chords, the same chord all bar
    cello = bars[5].strings[2]  # Bb3 C4 D4
    assert rh.periodicity >= 0.8 and rh.thickness >= 3
    assert cello.periodicity < 0.5 and cello.melody > 2 * rh.melody
    assert bars[3].piano_onsets == 0 and bars[4].piano_pulse


def test_write_and_load_round_trip(score, passages, tmp_path):
    path = annotate.write(score, passages, tmp_path / "x.lead.yaml")
    assert annotate.load(path) == passages
