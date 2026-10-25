import numpy as np
import pytest

from rehearsal import simulate, synth
from rehearsal.accompanist import Accompanist
from rehearsal.score import Score
from rehearsal.tempo import BeatClock


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


def test_strings_wait_for_first_note_then_come_in_with_it(score):
    acc = Accompanist(score, 1, 16)
    assert acc.due(5.0) == []
    acc.on_piano_onset(5.0, 52)
    first = acc.due(5.0)
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


def test_restart_after_a_stop_finds_the_bar_by_pitch(score):
    """Stop in bar 8, wait, then go back to bar 8's downbeat: the strings stop, then restart there."""
    from rehearsal.midi_io import MidiEvent

    take = synth.make_take(score, 1, 16, seed=0)
    t_of = {b: t for t, b in take.truth}
    t_stop, t_back = t_of[22.5], t_of[21.0]  # bar 8 beat 2.5, bar 8 beat 1
    shift = t_stop + 5.0 - t_back
    events = [e for e in take.events if e.time <= t_stop + 0.01]
    events += [MidiEvent(e.time + shift, e.kind, e.note, e.value) for e in take.events if e.time >= t_back - 0.01]
    res = simulate.run(score, events, 1, 16)
    restarts = [(round(t, 2), b) for t, b, a in res.accompanist.log if a == 0.0 and t > 1.0]
    assert [b for _, b in restarts] == [21.0]
