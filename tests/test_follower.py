import pytest

from rehearsal import synth
from rehearsal.evaluate import run_take
from rehearsal.score import Score


@pytest.fixture(scope="module")
def score():
    return Score()


@pytest.mark.parametrize("kw", [dict(), dict(wrong_rate=0.05, drop_rate=0.05, extra_rate=0.05)])
def test_matchmaker_outerhmm_tracks_opening(score, kw):
    take = synth.make_take(score, 1, 16, seed=0, **kw)
    r = run_take(score, take.events, take.truth, "outerhmm")
    assert r["within_1_beat"] >= 0.97
    assert r["within_0.25_beat"] >= 0.75
