import numpy as np
import pytest

from rehearsal import simulate, synth
from rehearsal.accompanist import Accompanist
from rehearsal.score import Score
from rehearsal.tempo import BeatClock
from rehearsal.voice import Cue


@pytest.fixture(scope="module")
def score():
    return Score()


def test_clock_converges_to_steady_tempo():
    clock = BeatClock(spb=60 / 63)
    clock.anchor(0.0, 0.0)
    prev = 0.0
    for i in range(1, 40):
        beat = i * 0.5
        clock.correct(beat * 0.75, beat, prev)  # pianist at 80 bpm
        prev = beat
    assert clock.spb == pytest.approx(0.75, abs=0.01)
    assert clock.time_of(20.0) == pytest.approx(15.0, abs=0.02)


def test_strings_wait_for_first_chord_then_come_in_with_it(score):
    acc = Accompanist(score, 1, 16)
    assert acc.due(5.0) == []
    for i, pitch in enumerate(sorted(acc.piano_pitches[acc.piano_onsets[0]])):
        acc.on_piano_onset(5.0 + 0.005 * i, pitch)
    first = acc.due(5.02)
    assert first and all(o.kind == "on" and o.beat == acc.piano_onsets[0] for o in first)


@pytest.mark.parametrize("kw", [dict(), dict(rubato=0.15, jitter_ms=20)])
def test_strings_stay_together_with_synthetic_pianist(score, kw):
    take = synth.make_take(score, 1, 16, seed=0, **kw)
    res = simulate.run(score, take.events, 1, 16)
    # Bars 4-16, where the piano carries the pulse (bars 1-3 are the strings' own).
    a = np.array([x for b, x in res.asyn if b >= 9.0])
    assert len(a) > 50
    assert abs(np.median(a)) < 0.03
    assert np.mean(np.abs(a) <= 0.06) >= 0.9
    assert np.mean(a) > -0.06  # never consistently ahead...
    assert np.mean(a) < 0.03  # ...and never consistently late


def spliced_take(score, stop_beat: float, back_to: float, pause_s: float = 5.0):
    """A synthetic take of bars 1-16 that stops after `stop_beat`, pauses, then starts again at `back_to`."""
    from rehearsal.midi_io import MidiEvent

    take = synth.make_take(score, 1, 16, seed=0)
    t_of = {b: t for t, b in take.truth}
    t_stop, t_back = t_of[stop_beat], t_of[back_to]
    shift = t_stop + pause_s - t_back
    events = [e for e in take.events if e.time <= t_stop + 0.01]
    events += [MidiEvent(e.time + shift, e.kind, e.note, e.value) for e in take.events if e.time >= t_back - 0.01]
    return events, t_stop, t_stop + pause_s


def restarts(res):
    return [b for t, b, a in res.accompanist.log if a == 0.0 and t > 1.0]


def test_restart_after_a_stop_finds_the_bar_by_pitch(score):
    """Stop in bar 8, wait, then go back to bar 8's downbeat: the strings stop, then restart there."""
    events, _, _ = spliced_take(score, 22.5, 21.0)
    assert restarts(simulate.run(score, events, 1, 16)) == [21.0]


def test_stopping_carries_on_then_trails_off_without_cutting(score):
    events, t_stop, _ = spliced_take(score, 22.5, 21.0)
    res = simulate.run(score, events, 1, 16)
    acc = res.accompanist
    t_trail = next(t for t, text in acc.notices if text.startswith("you stopped"))
    t_wait = next(t for t, text in acc.notices if text.startswith("waiting"))
    beats_carried = (t_trail - t_stop) / acc.clock.spb
    assert 1.5 <= beats_carried <= 2.5  # carried on past the missing chord at 23.0
    assert t_wait - t_trail == pytest.approx(0.8, abs=0.01)
    fade = [o.velocity for o in res.outs if o.kind == "expr" and t_trail <= o.time < t_wait]
    offs_at_wait = [o for o in res.outs if o.kind == "off" and abs(o.time - t_wait) < 0.002]
    if offs_at_wait:  # anything still sounding at the end of the trail was faded first
        assert fade and min(fade) < 20 and fade == sorted(fade, reverse=True)


def test_going_back_further_needs_a_cue(score):
    """Stop in bar 8 and start again at bar 5 without a cue: the strings don't guess. They come back in
    only when the pianist plays on into where they stopped."""
    events, _, _ = spliced_take(score, 22.5, 12.0)
    res = simulate.run(score, events, 1, 16)
    notices = [text for _, text in res.accompanist.notices]
    assert any(text.startswith("not sure") for text in notices)
    assert not any(text.startswith("lost you") for text in notices)  # no false starts
    assert restarts(res) and all(b >= 21.0 for b in restarts(res))


@pytest.mark.parametrize("cue,expected", [(Cue("bar", 5), 12.0), (Cue("bar", 5, pickup=True), 12.0), (Cue("top"), 1.0)])
def test_cue_restarts_where_asked(score, cue, expected):
    events, _, t_resume = spliced_take(score, 22.5, expected)
    res = simulate.run(score, events, 1, 16, cues=[(t_resume - 2.0, cue)])
    assert restarts(res) == [expected]
    played = [o for o in res.outs if o.kind == "on" and o.time > t_resume - 0.01]
    assert played and min(o.beat for o in played) >= expected


def test_again_returns_to_the_last_cue(score):
    acc = Accompanist(score, 1, 16)
    acc.on_cue(0.0, Cue("bar", 9))
    acc.last_start = 24.0  # as if we had since cued bar 9 and moved on
    assert "bar 9" in acc.on_cue(1.0, Cue("again"))
    assert acc.restart_from == 24.0


def test_session_analysis_splits_passes(score, tmp_path):
    from rehearsal import session

    events, _, t_resume = spliced_take(score, 22.5, 12.0)
    res = simulate.run(score, events, 1, 16, cues=[(t_resume - 2.0, Cue("bar", 5))])
    path = simulate.write_midi(events, res.outs, tmp_path / "s.mid", res.accompanist.notices)
    passes = session.analyze(score, path).passes
    assert [score.bar_at(p.first_beat)[0] for p in passes] == [1, 5]
    assert any("bar 5" in text for _, text in passes[1].notices)
    a = np.array([x for p in passes for b, _, x in p.rows if b >= 9.0])
    assert np.mean(np.abs(a) <= 0.06) >= 0.9
