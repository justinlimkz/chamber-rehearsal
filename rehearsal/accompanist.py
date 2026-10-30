"""Accompaniment scheduler: plays the string parts ahead of the predicted beats.

Event-driven and clock-agnostic, so the same object runs live (wall clock) and offline (take time):
feed it piano notes, follower positions and voice cues, and ask `due(now)` for what to play.

Where the pianist is comes from two sources:
- Each piano chord is matched to a chord of the piano part near the beat clock's prediction, by pitch
  set, preferring the next chord in the score. This keeps the clock steady in bars of repeated
  identical chords, where the follower runs an eighth ahead, and the distinct bass notes on downbeats
  pull it back if it slips.
- The follower corrects gross drift: if it disagrees with the matched chord by more than a beat several
  chords running, the position jumps to the follower.

Who leads (milestone 5): by default the piano always leads and the clock follows it closely. Given a
leadership annotation (`annotate.py`), each piano chord is coupled by its passage's setting instead:
the clock moves `coupling` of the way to the pianist, waits a little more when the pianist is late than
it hurries when they are early, and where the strings lead it is pulled back towards the strings' own
tempo (`home_spb`: the starting tempo, then whatever tempo the last piano-led passage ended in).

Stopping and starting again:
- When the pianist stops, the strings carry on for a beat and a half, then trail off: notes that end
  soon finish, longer ones fade out. Then they wait.
- While waiting, playing on (or going back a couple of chords) restarts them where the notes fit.
  Going back further needs a voice cue ("from bar 9", "from the top", "again"): after a cue they wait
  for the pianist's first notes there.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from . import dynamics
from .annotate import COUPLING, Passage
from .score import Score
from .tempo import BeatClock
from .voice import Cue

STRING_PARTS = ("Violin", "Viola", "Violoncello")
CHORD_GAP_S = 0.04  # a piano chord is complete this long after its last note (or as soon as all its notes are in)
ROLLED_FRACTION = 0.3  # a pitch of the last chord this soon (fraction of the time to the next chord) is a late chord note
GRACE_S = 0.07  # grace notes (zero duration in the score) sound this long, just before the beat
LEGATO = 0.95  # string notes stop at this fraction of their written length (min 50 ms)
DRIFT_BEATS = 1.0  # follower/matched-chord disagreement that counts as drift
DRIFT_COUNT = 3  # consecutive drifting chords before the position jumps to the follower

CARRY_BEATS = 1.5  # past an expected piano chord with no piano, the strings carry on this long...
TRAIL_S = 0.8  # ...then trail off over this long: notes ending sooner finish, the rest fade out
FADE_STEP = 8  # expression (CC11) change per fade message
RESTART_BACK_BEATS = 1.5  # without a cue, a restart may go back this far from the last chord played
CUE_BACK_BEATS = 0.5  # after a cue, slack before its first piano chord
RESTART_MIN_SIM = 0.6  # a restart needs a chord this similar (Jaccard) to a score chord in the window
# (0.5 let bar 6's G-D-Eb pass for bar 8's G-C-Eb; 0.6 still allows one wrong note in a four-note chord)
PROBATION_CHORDS = 2  # after a restart, chords that must also fit, or the strings stop again
PROBATION_MIN_SIM = 0.34

# Chord matching: score = pitch similarity - penalties. Candidates are the next expected chord and any
# from 1 beat before the last match to 2 beats after it.
TIME_PENALTY = 0.5  # per beat between the candidate and the clock's prediction
SKIP_PENALTY = 0.4  # per score chord skipped past the next expected one
BACK_PENALTY = 0.6  # per score chord stepped back (to undo an earlier wrong match)
MIN_SCORE = 0.0  # below this the chord is treated as stray notes

# Coupling by passage (only with a leadership annotation). A coupling of COUPLING["piano"] is the
# clock's own alpha and beta, so piano-led passages behave exactly as without an annotation.
BETA_PER_COUPLING = 0.3 / 0.7  # period correction scales with the coupling
WAIT_EXTRA = 0.15  # extra phase correction when the pianist is late: the strings wait a little
HOME_PULL = 0.3  # per chord, at coupling 0, the period moves this fraction back to the strings' tempo


@dataclass(frozen=True)
class StringNote:
    part: str
    pitch: int
    onset_beat: float
    duration_beat: float
    velocity: int

    @property
    def is_grace(self) -> bool:
        return self.duration_beat <= 0


@dataclass(frozen=True)
class Out:
    """A string event the sound engine should play. `time` is when it was meant to sound."""
    time: float
    kind: str  # "on" | "off" | "expr" (expression, CC11, value in `velocity`)
    part: str
    pitch: int = 0
    velocity: int = 0
    beat: float = 0.0


@dataclass
class Accompanist:
    score: Score
    first_bar: int = 1
    last_bar: int = 16
    clock: BeatClock = field(default_factory=lambda: BeatClock(spb=60 / 63))
    velocity: int = 80  # every note, without dynamics
    leadership: list[Passage] | None = None  # None: the piano always leads
    dynamics: bool = True  # loudness from the score's dynamics (dynamics.py): expression (CC11) following the
                           # marked levels, hairpins and phrasing, through held notes too; velocity picks the layer

    def __post_init__(self) -> None:
        starts = self.score.bar_starts()
        self.bar_starts = starts
        self.bar_start = {bar: b for b, bar in reversed(starts)}  # first occurrence if repeats unfold
        self.start_beat = self.bar_start[self.first_bar]
        self.end_beat = self.bar_start.get(self.last_bar + 1, float("inf"))
        piano: dict[float, set[int]] = {}
        for n in self.score.notes("Piano"):
            if self.start_beat <= n.onset_beat < self.end_beat:
                piano.setdefault(n.onset_beat, set()).add(n.pitch)
        self.piano_onsets = sorted(piano)
        self.piano_pitches = {b: frozenset(p) for b, p in piano.items()}
        self.levels = dynamics.curves(self.score, STRING_PARTS) if self.dynamics else None
        velocity = dynamics.TOP_VELOCITY if self.dynamics else self.velocity
        self.notes = sorted(
            (StringNote(name, n.pitch, n.onset_beat, n.duration_beat, velocity)
             for name in STRING_PARTS for n in self.score.notes(name)
             if self.start_beat <= n.onset_beat < self.end_beat),
            key=lambda n: (n.onset_beat, n.part, n.pitch),
        )
        self.started = False  # the clock has been set at least once
        self.waiting = True  # strings silent, waiting for the pianist to (re)start
        self.finished = False
        self.paused = False  # the pianist practises alone: piano ignored until "resume" or a cue
        self.trail_start: float | None = None  # when the strings began trailing off
        self.faded: dict[str, int] = {}  # current expression of parts being faded
        self.dyn: dict[str, int] = {}  # per part, the expression the dynamics ask for (127 if not set)
        self.expr: dict[str, int] = {}  # per part, the expression last sent: dynamics times fade (127 if not sent)
        self.layer: dict[str, int] = {}  # per part, the velocity of its notes (the sample layer), set as a phrase starts
        # per part, the onset of its last note and the written end of its phrase so far: a phrase starts after a
        # written rest (not the gap LEGATO leaves between joined notes) or when the strings go back
        self.phrase: dict[str, tuple[float, float]] = {}
        self.restart_from = self.piano_onsets[0]  # piano chord a restart is expected at...
        self.restart_back = 0.0  # ...or up to this many beats before it
        self.last_start = self.start_beat  # for "again"
        self.last_beat: float | None = None  # score beat of the last piano chord matched
        self.last_chord_t = -1.0
        self.last_note_t = -1.0  # last note that went into a chord
        self.pending: tuple[float, float, set[int]] | None = None  # chord being collected: (first t, last t, pitches)
        self.next_note = 0  # index into self.notes of the next note to start
        self.sounding: list[tuple[float, StringNote]] = []  # (end beat, note)
        self.queued: list[Out] = []  # produced by inputs, emitted by the next `due`
        self.drift = 0
        self.follower_beat: float | None = None  # the follower's latest estimate
        self.follower_trusted = False  # set once the follower agrees with us; cleared on every restart
        self.probation = 0  # chords left that must fit after a restart
        self.log: list[tuple[float, float, float]] = []  # (time, matched beat, asynchrony) per piano chord
        self.notices: list[tuple[float, str]] = []  # state changes, for the live display
        self.home_spb = self.clock.spb  # the strings' own tempo, where they lead
        self.passage: Passage | None = None  # the leadership passage of the last chord matched

    # --- inputs -------------------------------------------------------------------------------------

    def on_piano_onset(self, t: float, pitch: int) -> None:
        if self.finished or self.paused:
            return
        if self.pending and t - self.pending[1] <= CHORD_GAP_S:
            self.pending[2].add(pitch)
            self.pending = (self.pending[0], t, self.pending[2])
            self.last_note_t = t
        else:
            self._finish_chord()
            if t - self.last_note_t <= CHORD_GAP_S:
                self.last_note_t = t
                return  # the rest of a chord already matched (it was complete enough to act on early)
            if not self._restarting() and self._late_chord_note(t, pitch):
                return
            self.pending = (t, t, {pitch})
            self.last_note_t = t
        if self._restarting():
            # Restart on the whole chord (it is the only evidence of where the pianist is), but don't wait
            # out the gap once a candidate chord is complete, or (four notes or more) all but one in and fitting.
            got = frozenset(self.pending[2])
            if any(self.piano_pitches[b] <= got
                   or (len(got) >= 3 and len(got) >= len(self.piano_pitches[b]) - 1
                       and self._sim(got, self.piano_pitches[b]) >= RESTART_MIN_SIM)
                   for b in self._restart_candidates()):
                self._finish_chord()
            return
        # Don't wait out the gap once every note of the expected chord is in.
        nxt = self._next_onset()
        if nxt is not None and self.piano_pitches[nxt] <= self.pending[2]:
            self._finish_chord()

    def on_follower(self, t: float, beat: float) -> None:
        self.follower_beat = beat

    def on_cue(self, t: float, cue: Cue) -> str:
        """A voice cue. Returns (and posts as a notice) what the strings will do."""
        msg = self._cue(t, cue)
        self.notices.append((t, msg))
        return msg

    def _cue(self, t: float, cue: Cue) -> str:
        if cue.kind == "pause":
            return self._pause(t)
        if cue.kind == "resume":
            if not self.paused:
                return '"resume" ignored: not paused'
            self.paused = False
            bar, bib = self.score.bar_at(self.restart_from)
            return f"resume: strings come in with you at bar {bar} beat {bib:g}"
        if not (self.waiting or self.trailing):
            return f'"{cue}" ignored: only while stopped'
        if cue.kind == "again":
            target = self.last_start
        elif cue.kind == "top":
            target = self.start_beat
        elif cue.bar in self.bar_start and self.start_beat <= self.bar_start[cue.bar] < self.end_beat:
            target = self.bar_start[cue.bar] - (1.0 if cue.pickup else 0.0)
        else:
            return f'"{cue}" ignored: bars {self.first_bar}-{self.last_bar} only'
        first = next((b for b in self.piano_onsets if b >= target), None)
        if first is None:
            return f'"{cue}" ignored: no piano after that point'
        self._stop_sounding(t)
        self.waiting, self.trail_start, self.paused = True, None, False
        self.restart_from, self.restart_back = first, CUE_BACK_BEATS
        self.last_start = max(target, self.start_beat)
        self.last_beat = None
        bar, bib = self.score.bar_at(first)
        return f"{cue}: strings come in with you at bar {bar} beat {bib:g}"

    def _pause(self, t: float) -> str:
        """Strings stop at once and sit out, the piano ignored, until "resume" (back where you were) or a cue."""
        if self.paused:
            return "already paused"
        if not (self.waiting or self.trailing):  # playing: a restart from where you are, as when you stop
            nxt = self._next_onset()
            if nxt is not None and self.last_beat is not None:
                self.restart_from, self.restart_back = nxt, nxt - self.last_beat + RESTART_BACK_BEATS
        self._stop_sounding(t)
        self.waiting, self.trail_start, self.paused, self.pending = True, None, True, None
        return 'paused: practise on your own; say "resume" or "from bar …" for the strings'

    # --- output -------------------------------------------------------------------------------------

    @property
    def trailing(self) -> bool:
        return self.trail_start is not None

    def due(self, now: float) -> list[Out]:
        """String events whose time has come, in time order."""
        if self.pending and now - self.pending[1] > CHORD_GAP_S:
            self._finish_chord()
        out, self.queued = self.queued, []
        if self.waiting or self.finished:
            return out
        self._end_notes(now, out)
        if self.trailing:
            self._trail(now, out)
            return out
        if self._pianist_stopped(now):
            self.trail_start = now
            # A restart may go back RESTART_BACK_BEATS from the last chord played.
            nxt = self._next_onset()
            self.restart_from, self.restart_back = nxt, nxt - self.last_beat + RESTART_BACK_BEATS
            self.notices.append((now, "you stopped: strings trailing off"))
            self._trail(now, out)
            return out
        while self.next_note < len(self.notes):
            n = self.notes[self.next_note]
            t_on = self.clock.time_of(n.onset_beat) - (GRACE_S if n.is_grace else 0.0)
            if self.last_beat is not None and n.onset_beat <= self.last_beat:
                t_on = min(t_on, now)  # the pianist already played this beat, earlier than predicted: come in now
            if t_on > now:
                break
            velocity = n.velocity
            if self.levels:
                last, end = self.phrase.get(n.part, (math.inf, -math.inf))
                if n.onset_beat < last or n.onset_beat - end >= dynamics.EXPRESSION.phrase_gap:  # a phrase starts
                    self.layer[n.part] = dynamics.layer_velocity(dynamics.level_at(self.levels[n.part], n.onset_beat))
                    end = -math.inf
                self.phrase[n.part] = (n.onset_beat, max(end, n.onset_beat + n.duration_beat))
                velocity = self.layer[n.part]
                self._dynamics(t_on, n.part, n.onset_beat, out)
            out.append(Out(t_on, "on", n.part, n.pitch, velocity, n.onset_beat))
            self.sounding.append((n.onset_beat + n.duration_beat, n))
            self.next_note += 1
        if self.levels:  # hairpins go on through held notes
            beat = self.clock.beat_at(now)
            for part in STRING_PARTS:
                onsets = [n.onset_beat for _, n in self.sounding if n.part == part]
                if onsets:
                    self._dynamics(now, part, max(beat, *onsets), out)
        if self.next_note >= len(self.notes) and not self.sounding:
            self.finished = True
        out.sort(key=lambda o: o.time)
        return out

    def flush(self, now: float) -> list[Out]:
        """Stop everything (end of run)."""
        out = self.queued + [Out(now, "off", n.part, n.pitch, beat=n.onset_beat) for _, n in self.sounding]
        self.queued, self.sounding = [], []
        self.finished = True
        return out

    # --- internals ----------------------------------------------------------------------------------

    def _restarting(self) -> bool:
        return self.waiting or self.trailing

    def _end_notes(self, now: float, out: list[Out]) -> None:
        for end, n in list(self.sounding):
            t_off = self._off_time(n)
            if t_off <= now:
                out.append(Out(t_off, "off", n.part, n.pitch, beat=end))
                self.sounding.remove((end, n))

    def _trail(self, now: float, out: list[Out]) -> None:
        """Let notes that end within the trail finish; fade the rest; then wait."""
        elapsed = now - self.trail_start
        if elapsed >= TRAIL_S:
            self._stop_sounding(now)
            out += self.queued
            self.queued = []
            self.waiting, self.trail_start = True, None
            self.notices.append((now, "waiting: play on, or say where to start"))
            return
        level = int(127 * (1 - elapsed / TRAIL_S))
        for _, n in self.sounding:
            if self._off_time(n) > self.trail_start + TRAIL_S and self.faded.get(n.part, 127) - level >= FADE_STEP:
                self.faded[n.part] = level
                self.expr[n.part] = _scale(self.dyn.get(n.part, 127), level)
                out.append(Out(now, "expr", n.part, velocity=self.expr[n.part]))

    def _dynamics(self, t: float, part: str, beat: float, out: list[Out]) -> None:
        """Send the part's expression for the dynamics at `beat`, if it changed."""
        self.dyn[part] = dynamics.expression(dynamics.level_at(self.levels[part], beat), self.layer.get(part, 127))
        value = _scale(self.dyn[part], self.faded.get(part, 127))
        if value != self.expr.get(part, 127):
            self.expr[part] = value
            out.append(Out(t, "expr", part, velocity=value))

    def _stop_sounding(self, now: float) -> None:
        """Queue note-offs for everything sounding and reset any faded expression."""
        self.queued += [Out(now, "off", n.part, n.pitch, beat=n.onset_beat) for _, n in self.sounding]
        self.queued += [Out(now, "expr", part, velocity=127) for part in self.faded]
        self.expr.update({part: 127 for part in self.faded})
        self.sounding, self.faded = [], {}

    def _finish_chord(self) -> None:
        if self.pending is None:
            return
        t, _, pitches = self.pending
        self.pending = None
        if self._restarting():
            self._restart(t, frozenset(pitches))
            return
        beat = self._match(t, frozenset(pitches))
        if self.probation and beat is not None and len(pitches) >= 2:
            if self._sim(frozenset(pitches), self.piano_pitches[beat]) < PROBATION_MIN_SIM:
                self._lost(t)
                return
            self.probation -= 1
        if beat is None:
            return  # stray notes that fit nothing nearby
        if self.last_beat is not None and beat <= self.last_beat:
            self.clock.anchor(t, beat)  # stepping back to fix an earlier wrong match: phase only
            asyn = 0.0
        else:
            asyn = self._correct(t, beat)
        self._matched(t, beat, asyn)
        self._check_drift(t)

    def _restart(self, t: float, pitches: frozenset[int]) -> None:
        beat = self._restart_beat(pitches)
        if beat is None:
            if not self.notices or not self.notices[-1][1].startswith("not sure"):
                self.notices.append((t, "not sure where you are: play on from where you stopped, or say where to start"))
            return
        self._stop_sounding(t)  # anything still trailing belongs to where we were
        self.clock.anchor(t, beat)  # tempo carries over from before the stop
        self.started, self.waiting, self.trail_start = True, False, None
        self.next_note = next((i for i, n in enumerate(self.notes) if n.onset_beat >= beat), len(self.notes))
        self.follower_trusted = False
        self.drift = 0
        self.probation = PROBATION_CHORDS
        bar, bib = self.score.bar_at(beat)
        self.notices.append((t, f"with you at bar {bar} beat {bib:g}"))
        self._matched(t, beat, 0.0)

    def _lost(self, t: float) -> None:
        """A restart turned out wrong: stop the strings and wait again, with the same restart window."""
        self._stop_sounding(t)
        self.waiting, self.probation = True, 0
        self.notices.append((t, "lost you: play on from where you stopped, or say where to start"))

    def _restart_candidates(self) -> list[float]:
        nxt = self.restart_from
        return [b for b in self.piano_onsets if nxt - self.restart_back <= b <= nxt + 1.0]

    @staticmethod
    def _sim(a: frozenset[int], b: frozenset[int]) -> float:
        return len(a & b) / len(a | b)

    def _restart_beat(self, pitches: frozenset[int]) -> float | None:
        """Where the pianist (re)starts: a chord whose pitches fit well, from `restart_back` beats before
        the expected chord to a beat after it, preferring the expected one. None if nothing fits."""
        i_next = self.piano_onsets.index(self.restart_from)
        scored = [(self._sim(pitches, self.piano_pitches[b]) - 0.05 * abs(self.piano_onsets.index(b) - i_next), b)
                  for b in self._restart_candidates()]
        score, best = max(scored)
        return best if self._sim(pitches, self.piano_pitches[best]) >= RESTART_MIN_SIM else None

    def _check_drift(self, t: float) -> None:
        f = self.follower_beat
        if f is None:
            return
        close = abs(f - self.last_beat) <= DRIFT_BEATS
        if not self.follower_trusted:
            # After a restart the follower may still be elsewhere (it cannot jump back); ignore it until
            # it agrees with us again.
            self.follower_trusted = close
            return
        if close:
            self.drift = 0
            return
        self.drift += 1
        if self.drift >= DRIFT_COUNT:
            # Trust the follower: jump to the piano chord at or before its estimate, keeping the tempo.
            beat = max((b for b in self.piano_onsets if b <= f), default=self.piano_onsets[0])
            self.clock.anchor(t, beat)
            self.last_beat = beat
            self.drift = 0

    def passage_at(self, beat: float) -> Passage | None:
        if self.leadership is None:
            return None
        i = max(0, bisect.bisect_right([b for b, _ in self.bar_starts], beat) - 1)
        bar = self.bar_starts[i][1]
        return next((p for p in self.leadership if p.first_bar <= bar <= p.last_bar), None)

    def _correct(self, t: float, beat: float) -> float:
        """Correct the clock for a piano chord, coupled as the passage says (if there is an annotation)."""
        p = self.passage_at(beat)
        if p is None:
            return self.clock.correct(t, beat, self.last_beat)
        c = p.coupling
        late = t > self.clock.time_of(beat)
        free = max(0.0, 1 - c / COUPLING["piano"])  # 0 where the piano leads, 1 at coupling 0
        asyn = self.clock.correct(t, beat, self.last_beat, alpha=min(1.0, c + WAIT_EXTRA * late * free),
                                  beta=BETA_PER_COUPLING * c, home_spb=self.home_spb, pull=HOME_PULL * free)
        if free == 0.0:
            self.home_spb = self.clock.spb  # the strings take over at the tempo the pianist set
        return asyn

    def _matched(self, t: float, beat: float, asyn: float) -> None:
        p = self.passage_at(beat)
        if p is not None and p != self.passage:
            self.passage = p
            who = {"piano": "piano leads", "strings": "strings lead", "shared": "shared"}[p.lead]
            self.notices.append((t, f"bars {p.first_bar}-{p.last_bar}: {who} (coupling {p.coupling:g})"))
        self.last_beat = beat
        self.last_chord_t = t
        self.log.append((t, beat, asyn))

    def _next_onset(self) -> float | None:
        lo = self.last_beat if self.last_beat is not None else -float("inf")
        return next((b for b in self.piano_onsets if b > lo), None)

    def _late_chord_note(self, t: float, pitch: int) -> bool:
        """Is this a note of the chord just played, arriving a little late (a rolled or uneven chord)?"""
        nxt = self._next_onset()
        if self.last_beat is None or nxt is None or pitch not in self.piano_pitches[self.last_beat]:
            return False
        return t - self.last_chord_t <= ROLLED_FRACTION * (nxt - self.last_beat) * self.clock.spb

    def _match(self, t: float, pitches: frozenset[int]) -> float | None:
        """The score chord this piano chord most plausibly is, or None for stray notes."""
        predicted = self.clock.beat_at(t)
        last = self.last_beat if self.last_beat is not None else -float("inf")
        nxt = self._next_onset()
        if nxt is None:
            return None
        # The next expected chord is always a candidate, however long the rest before it.
        cands = sorted({nxt} | {b for b in self.piano_onsets if last - 1.0 <= b <= last + 2.0})
        i_next = self.piano_onsets.index(nxt)

        def score(b: float) -> float:
            ref = self.piano_pitches[b]
            sim = len(pitches & ref) / len(pitches | ref)
            steps = self.piano_onsets.index(b) - i_next
            penalty = SKIP_PENALTY * max(0, steps) + BACK_PENALTY * max(0, -steps)
            # After a rest the clock may have run far from the pianist; time says little then.
            if b != nxt:
                penalty += TIME_PENALTY * abs(b - predicted)
            return sim - penalty

        best = max(cands, key=score)
        return best if score(best) > MIN_SCORE else None

    def _off_time(self, n: StringNote) -> float:
        if n.is_grace:
            return self.clock.time_of(n.onset_beat)
        on, end = self.clock.time_of(n.onset_beat), self.clock.time_of(n.onset_beat + n.duration_beat)
        return on + max(0.05, (end - on) * LEGATO)

    def _pianist_stopped(self, now: float) -> bool:
        expected = self._next_onset()
        return expected is not None and self.clock.beat_at(now) > expected + CARRY_BEATS


def _scale(dyn: int, fade: int) -> int:
    """Expression for a dynamics level faded to `fade`/127 (rounding half up, as the Swift port does)."""
    return dyn if fade == 127 else int(dyn * fade / 127 + 0.5)
