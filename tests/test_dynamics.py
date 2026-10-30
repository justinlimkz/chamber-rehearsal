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
    for name, pts in dynamics.curves(score, STRING_PARTS, ex=None).items():
        assert dynamics.level_at(pts, 1.0) == dynamics.LEVELS_DB["f"]
        assert dynamics.level_at(pts, 3.0) == dynamics.LEVELS_DB["p"]
        assert dynamics.LEVELS_DB["p"] < dynamics.level_at(pts, 2.0) < dynamics.LEVELS_DB["f"]


def test_hairpins_without_a_target_stay_within_a_step_of_the_marking(score):
    for name in STRING_PARTS:
        pts = dynamics.curve(score.part(name))
        assert max(v for _, v in pts) <= dynamics.LEVELS_DB["ff"]
        assert min(v for _, v in pts) >= dynamics.LEVELS_DB["pp"] - dynamics.STEP_DB


def test_expression_shapes_the_held_opening(score):
    """Bar 1 is one held chord per part under f > p: the expression moves while it sounds, in small steps,
    instead of the next note starting at a new level. The chord starts a phrase at f: the loud sample layer."""
    take = synth.make_take(score, 1, 4, rubato=0.0, jitter_ms=0.0)
    res = simulate.run(score, take.events, 1, 4, follow=False)
    ons = sorted(o.time for o in res.outs if o.kind == "on" and o.part == "Violin")
    expr = [o.velocity for o in res.outs if o.kind == "expr" and o.part == "Violin" and ons[0] <= o.time < ons[1]]
    assert len(expr) > 10 and max(abs(a - b) for a, b in zip(expr, expr[1:])) <= 2
    assert expr[-1] < expr[0]  # f to p
    first = min((o for o in res.outs if o.kind == "on" and o.part == "Violin"), key=lambda o: o.time)
    assert first.velocity == 127


def test_phrase_rises_to_its_highest_note_and_layers_follow_the_level(score):
    notes = score.notes("Violin")
    ph = max(dynamics.phrases(notes), key=len)
    top = max(ph, key=lambda n: (n.pitch, n.duration_beat, -n.onset_beat))
    plain = dynamics.curves(score, ["Violin"], ex=None)["Violin"]
    shaped = dynamics.curves(score, ["Violin"])["Violin"]
    lift = lambda b: dynamics.level_at(shaped, b) - dynamics.EXPRESSION.range * dynamics.level_at(plain, b)
    if top.onset_beat > ph[0].onset_beat:
        assert lift(top.onset_beat) > lift(ph[0].onset_beat)
    assert [dynamics.layer_velocity(db) for db in (-9, -2, 3)] == [79, 102, 127]
    assert dynamics.expression(0.0, 127) == 80 and dynamics.expression(0.0, 102) == 100  # a softer layer: more expression


def test_layer_holds_for_a_phrase(score):
    """Joined notes stop a little early (LEGATO), so the part is briefly silent before each one: that gap is not a
    rest, and the sample layer doesn't drop there (it was picked again at each note, and CC11 jumped up on the old
    note's tail). It may move up as the phrase gets louder."""
    take = synth.make_take(score, 1, 4, rubato=0.0, jitter_ms=0.0)
    res = simulate.run(score, take.events, 1, 4, follow=False)
    cello = sorted((o for o in res.outs if o.kind == "on" and o.part == "Violoncello"), key=lambda o: o.time)
    assert cello[0].beat < cello[1].beat and cello[1].velocity == cello[0].velocity  # bar 1's E into bar 2's F
    for part in ("Violin", "Viola", "Violoncello"):  # within a phrase, a layer only ever moves up
        ons = sorted((o for o in res.outs if o.kind == "on" and o.part == part), key=lambda o: o.time)
        phrase_of = {n.onset_beat: i for i, ph in enumerate(dynamics.phrases(score.notes(part))) for n in ph}
        for a, b in zip(ons, ons[1:]):
            if phrase_of[a.beat] == phrase_of[b.beat]:
                assert b.velocity >= a.velocity


def test_accompanist_without_dynamics_plays_one_velocity(score):
    assert {n.velocity for n in Accompanist(score, 1, 16, dynamics=False).notes} == {80}
