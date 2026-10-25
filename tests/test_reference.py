import pytest

from rehearsal import reference, synth
from rehearsal.score import Score


@pytest.fixture(scope="module")
def score():
    return Score()


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("kw", [dict(), dict(wrong_rate=0.05, drop_rate=0.10, extra_rate=0.08, rubato=0.15, jitter_ms=25)])
def test_reference_recovers_synthetic_truth(score, kw, seed):
    take = synth.make_take(score, 1, 16, seed=seed, **kw)
    ref = reference.align(score, take.events, 1, 16)
    truth = {b: t for t, b in take.truth}
    # Every reference match is right, and at most one heavily mangled chord goes unmatched.
    assert all(b in truth and abs(truth[b] - t) < 0.06 for t, b in ref.truth)
    assert len(ref.truth) >= len(take.truth) - 1
