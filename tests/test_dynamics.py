import pytest

from rehearsal import dynamics, simulate, synth
from rehearsal.accompanist import STRING_PARTS, Accompanist
from rehearsal.score import Score


@pytest.fixture(scope="module")
def score():
    return Score()


def test_velocity_is_80_at_mf_and_follows_the_samplers_curve():
    assert dynamics.velocity(0.0) == 80
    assert dynamics.velocity(-12.0) == 40  # pp: 40 * log10(40 / 80) = -12 dB
    assert dynamics.velocity(20.0) == 127


def test_level_ramps_between_points_and_steps_where_two_share_a_beat():
    pts = [(float("-inf"), 0.0), (2.0, 0.0), (4.0, -4.0), (4.0, 4.0)]
    assert dynamics.level_at(pts, 1.0) == 0.0
    assert dynamics.level_at(pts, 3.0) == pytest.approx(-2.0)
    assert dynamics.level_at(pts, 4.0) == 4.0  # the later of two points at one beat
    assert dynamics.level_at(pts, 9.0) == 4.0


def test_opening_f_diminuendo_to_p(score):
    """Bar 1: f with a diminuendo to the p marked in bar 2, in every string part."""
    for name, pts in dynamics.curves(score, STRING_PARTS).items():
        assert dynamics.level_at(pts, 1.0) == dynamics.LEVELS_DB["f"]
        assert dynamics.level_at(pts, 3.0) == dynamics.LEVELS_DB["p"]
        assert dynamics.LEVELS_DB["p"] < dynamics.level_at(pts, 2.0) < dynamics.LEVELS_DB["f"]


def test_hairpins_without_a_target_stay_within_a_step_of_the_marking(score):
    for name in STRING_PARTS:
        pts = dynamics.curve(score.part(name))
        assert max(v for _, v in pts) <= dynamics.LEVELS_DB["ff"]
        assert min(v for _, v in pts) >= dynamics.LEVELS_DB["pp"] - dynamics.STEP_DB


def test_expression_follows_the_diminuendo_through_the_held_opening(score):
    """Bar 1 is one held note per part under a diminuendo: the expression falls while it sounds, in small
    steps, instead of the next note starting a step quieter."""
    take = synth.make_take(score, 1, 4, rubato=0.0, jitter_ms=0.0)
    res = simulate.run(score, take.events, 1, 4, follow=False)
    on = min(o.time for o in res.outs if o.kind == "on" and o.part == "Violin")
    second = sorted(o.time for o in res.outs if o.kind == "on" and o.part == "Violin")[1]
    expr = [o.velocity for o in res.outs if o.kind == "expr" and o.part == "Violin" and on <= o.time < second]
    assert expr[0] == dynamics.velocity(dynamics.LEVELS_DB["f"])
    assert len(expr) > 10 and expr == sorted(expr, reverse=True)
    assert max(a - b for a, b in zip(expr, expr[1:])) <= 2
    assert {o.velocity for o in res.outs if o.kind == "on"} == {dynamics.TOP_VELOCITY}


def test_accompanist_without_dynamics_plays_one_velocity(score):
    assert {n.velocity for n in Accompanist(score, 1, 16, dynamics=False).notes} == {80}
