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


def test_tempo_marks_bend_the_clock():
    clock = BeatClock(spb=1.0, marks=[(4.0, 0.25), (8.0, 1.0)])  # four times as fast for bars 2 (beats 4 to 8)
    clock.anchor(0.0, 0.0)
    assert [clock.time_of(b) for b in (4, 6, 8, 9)] == pytest.approx([4.0, 4.5, 5.0, 6.0])
    for b in (-1.0, 2.0, 5.0, 8.0, 12.0):
        assert clock.beat_at(clock.time_of(b)) == pytest.approx(b)
    clock.anchor(4.5, 6.0)  # anchored inside the fast stretch, backwards too
    assert clock.beat_at(3.0) == pytest.approx(3.0) and clock.beat_at(4.25) == pytest.approx(5.0)
    assert clock.spb_at(6.0) == 0.25 and clock.spb_at(9.0) == 1.0


def test_a_presto_mark_speeds_the_strings_up_where_it_says():
    """The Kreutzer: Adagio at 55, Presto at 270 from bar 6. The piano comes in at bar 7 with whole notes under the
    violin's quarters; the quarters must be at your Presto, not the Adagio's pace (they were 0.3-0.7 s late)."""
    from pathlib import Path

    kreutzer = Score(Path(__file__).parent / "musicxml" / "outside" / "beethoven-kreutzer-i.mxl")
    acc = Accompanist(kreutzer, 1, 12, clock=BeatClock(spb=60 / 55))
    acc.on_cue(0.0, Cue("go"))
    presto = 60 / 270
    entry = acc.clock.time_of(acc.restart_from)
    out, t = run_until(acc, 0.0, entry), entry
    for b in [b for b in acc.piano_onsets if b < acc.bar_start[10]]:  # your chords, exactly at the Presto
        t_b = entry + (b - acc.restart_from) * presto
        out += run_until(acc, t, t_b)
        play_chord(acc, t_b, b)
        t = t_b
    out += run_until(acc, t, t + 4 * presto)
    quarters = [o for o in out if o.kind == "on" and o.part == "Violin" and acc.bar_start[8] <= o.beat < acc.bar_start[10]]
    assert len(quarters) >= 8
    assert all(abs(o.time - (entry + (o.beat - acc.restart_from) * presto)) < 0.03 for o in quarters), quarters


def test_strings_wait_for_first_chord_then_come_in_with_it(score):
    acc = Accompanist(score, 1, 16)
    assert acc.due(5.0) == []
    for i, pitch in enumerate(sorted(acc.piano_pitches[acc.piano_onsets[0]])):
        acc.on_piano_onset(5.0 + 0.005 * i, pitch)
    first = [o for o in acc.due(5.02) if o.kind != "expr"]  # the dynamics' expression comes with them
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


def test_pause_ignores_the_piano_until_resume(score):
    take = synth.make_take(score, 1, 16, seed=0)
    acc = Accompanist(score, 1, 16)
    onsets = [e for e in take.events if e.is_onset]
    half = len(onsets) // 2
    t_pause = onsets[half].time - 0.01
    played_while_paused = []
    paused = False
    for e in onsets:
        if not paused and e.time >= t_pause:
            acc.on_cue(t_pause, Cue("pause"))
            paused = True
        acc.on_piano_onset(e.time, e.note)
        outs = acc.due(e.time + 0.001)
        if paused:
            played_while_paused += [o for o in outs if o.kind == "on"]
    assert acc.paused and acc.waiting and played_while_paused == []
    assert acc.on_cue(99.0, Cue("resume")).startswith("resume: strings come in with you at bar")
    assert not acc.paused and acc.waiting



def run_until(acc, t0, t1, step=0.001):
    out, t = [], t0
    while t < t1:
        out += acc.due(t)
        t += step
    return out


def play_chord(acc, t, beat):
    for i, pitch in enumerate(sorted(acc.piano_pitches[beat])):
        acc.on_piano_onset(t + 0.002 * i, pitch)


@pytest.mark.parametrize("late_s", [0.02, 2.5])
def test_go_counts_the_strings_in_where_they_play_first(score, late_s):
    acc = Accompanist(score, 1, 16)
    assert acc.lead_in is None  # bar 1: the piano starts
    assert "press the pedal" in acc.on_cue(0.0, Cue("bar", 3))  # bar 3: three beats of strings before the piano
    assert acc.can_count_in
    assert "counting in" in acc.on_cue(1.0, Cue("go"))
    entry = acc.clock.time_of(acc.restart_from)
    out = run_until(acc, 1.0, entry + late_s)
    clicks = [o for o in out if o.kind == "click"]
    assert [o.beat for o in clicks] == [3.0, 4.0, 5.0]  # a bar of 3/4 before bar 3
    assert clicks[0].time == pytest.approx(1.0 + 0.3, abs=0.002) and [o.velocity for o in clicks] == [100, 70, 70]
    first = [o for o in out if o.kind == "on"]
    assert min(o.beat for o in first) == 6.0  # bar 3 beat 1, on the beat after the last click
    want = {(n.part, n.pitch, n.onset_beat) for n in acc.notes if 6.0 <= n.onset_beat <= acc.restart_from}
    assert {(o.part, o.pitch, o.beat) for o in first} == want  # all of theirs up to your first chord, none after
    assert not any("stopped" in text for _, text in acc.notices)  # however late you are, they wait
    t, entry_beat = entry + late_s, acc.restart_from
    play_chord(acc, t, entry_beat)
    later = [o for o in run_until(acc, t, t + 1.0) if o.kind == "on"]
    assert later and all(o.beat > entry_beat for o in later)  # nothing played twice
    assert acc.log[0][1] == entry_beat and not acc.leading


def test_without_go_the_strings_wait_for_your_first_chord(score):
    acc = Accompanist(score, 1, 16)
    acc.on_cue(0.0, Cue("bar", 3))
    assert [o for o in run_until(acc, 0.0, 5.0) if o.kind in ("on", "click")] == []
    play_chord(acc, 5.0, acc.restart_from)
    assert min(o.beat for o in run_until(acc, 5.0, 6.0) if o.kind == "on") >= acc.restart_from


def test_go_is_ignored_where_the_piano_starts(score):
    acc = Accompanist(score, 1, 16)
    acc.on_cue(0.0, Cue("bar", 5))
    assert not acc.can_count_in
    assert "ignored" in acc.on_cue(1.0, Cue("go"))
    assert acc.waiting and run_until(acc, 1.0, 4.0) == []


def test_a_strings_first_opening_can_be_counted_in_from_the_start():
    from pathlib import Path

    kreutzer = Score(Path(__file__).parent / "musicxml" / "outside" / "beethoven-kreutzer-i.mxl")
    acc = Accompanist(kreutzer, 1, 30)
    assert acc.lead_in == acc.start_beat and acc.can_count_in  # the violin's solo opening


def test_a_bar_of_eighths_is_counted_in_eighths():
    from pathlib import Path

    larghetto = Score(Path(__file__).parent.parent / "scores" / "mozart-k493-ii.mxl")  # 3/8
    acc = Accompanist(larghetto, 1, 20)
    acc.on_cue(0.0, Cue("bar", 11))
    acc.on_cue(1.0, Cue("go"))
    assert acc.clicks == [13.5, 14.0, 14.5] and acc.count_from + 3 * acc.count_unit == acc.notes[acc.next_note].onset_beat


def test_counted_in_with_no_piano_the_strings_hold_at_your_entry(score):
    acc = Accompanist(score, 1, 16)
    acc.on_cue(0.0, Cue("bar", 3))
    acc.on_cue(1.0, Cue("go"))
    entry = acc.clock.time_of(acc.restart_from)
    run_until(acc, 1.0, entry + 0.5)
    assert acc.leading and not acc.holding  # at your entry, not yet late
    held = acc.clock.time_of(acc.restart_from + 2.0)
    out = run_until(acc, entry + 0.5, held)
    assert acc.holding and not any(o.kind == "on" for o in out)
    assert any("wait for you at bar" in text for _, text in acc.notices)
    assert not any("stopped" in text for _, text in acc.notices)
    assert "paused" in acc.on_cue(held, Cue("pause"))  # the controls work from here
    assert acc.paused and not acc.leading and not acc.holding


def test_holding_strings_come_in_with_a_late_first_chord(score):
    acc = Accompanist(score, 1, 16)
    acc.on_cue(0.0, Cue("bar", 3))
    acc.on_cue(1.0, Cue("go"))
    t = acc.clock.time_of(acc.restart_from + 4.0)
    run_until(acc, 1.0, t)
    assert acc.holding
    entry_beat = acc.restart_from
    play_chord(acc, t, entry_beat)
    later = [o for o in run_until(acc, t, t + 1.0) if o.kind == "on"]
    assert later and all(o.beat > entry_beat for o in later) and not acc.holding


def test_a_cue_while_holding_starts_again(score):
    acc = Accompanist(score, 1, 16)
    acc.on_cue(0.0, Cue("bar", 3))
    acc.on_cue(1.0, Cue("go"))
    t = acc.clock.time_of(acc.restart_from + 4.0)
    run_until(acc, 1.0, t)
    assert "press the pedal" in acc.on_cue(t, Cue("again"))
    assert acc.waiting and acc.can_count_in and not acc.holding
