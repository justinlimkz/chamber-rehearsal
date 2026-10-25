"""Accompaniment scheduler: plays the string parts ahead of the predicted beats.

Event-driven and clock-agnostic, so the same object runs live (wall clock) and offline (take time):
feed it piano notes and follower positions, and ask `due(now)` for the string notes to start or stop.

Where the pianist is comes from two sources:
- Each piano chord is matched to a chord of the piano part near the beat clock's prediction, by pitch
  set, preferring the next chord in the score. This keeps the clock steady in bars of repeated
  identical chords, where the follower runs an eighth ahead, and the distinct bass notes on downbeats
  pull it back if it slips.
- The follower corrects gross drift: if it disagrees with the matched chord by more than a beat several
  chords running, the position jumps to the follower.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .score import Score
from .tempo import BeatClock

STRING_PARTS = ("Violin", "Viola", "Violoncello")
CHORD_GAP_S = 0.04  # a piano chord is complete this long after its last note (or as soon as all its notes are in)
ROLLED_FRACTION = 0.3  # a pitch of the last chord this soon (fraction of the time to the next chord) is a late chord note
GRACE_S = 0.07  # grace notes (zero duration in the score) sound this long, just before the beat
LEGATO = 0.95  # string notes stop at this fraction of their written length (min 50 ms)
DRIFT_BEATS = 1.0  # follower/matched-chord disagreement that counts as drift
DRIFT_COUNT = 3  # consecutive drifting chords before the position jumps to the follower
STOP_AFTER_BEATS = 2.0  # silence this long past an expected piano onset means the pianist stopped (M4 refines)

# Chord matching: score = pitch similarity - penalties. Candidates are the next expected chord and any
# from 1 beat before the last match to 2 beats after it.
TIME_PENALTY = 0.5  # per beat between the candidate and the clock's prediction
SKIP_PENALTY = 0.4  # per score chord skipped past the next expected one
BACK_PENALTY = 0.6  # per score chord stepped back (to undo an earlier wrong match)
MIN_SCORE = 0.0  # below this the chord is treated as stray notes


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
    kind: str  # "on" | "off"
    part: str
    pitch: int
    velocity: int = 0
    beat: float = 0.0


@dataclass
class Accompanist:
    score: Score
    first_bar: int = 1
    last_bar: int = 16
    clock: BeatClock = field(default_factory=lambda: BeatClock(spb=60 / 63))
    velocity: int = 80

    def __post_init__(self) -> None:
        starts = self.score.bar_starts()
        self.start_beat = next(b for b, bar in starts if bar == self.first_bar)
        ends = [b for b, bar in starts if bar == self.last_bar + 1]
        self.end_beat = ends[0] if ends else float("inf")
        piano: dict[float, set[int]] = {}
        for n in self.score.notes("Piano"):
            if self.start_beat <= n.onset_beat < self.end_beat:
                piano.setdefault(n.onset_beat, set()).add(n.pitch)
        self.piano_onsets = sorted(piano)
        self.piano_pitches = {b: frozenset(p) for b, p in piano.items()}
        self.notes = sorted(
            (StringNote(name, n.pitch, n.onset_beat, n.duration_beat, self.velocity)
             for name in STRING_PARTS for n in self.score.notes(name)
             if self.start_beat <= n.onset_beat < self.end_beat),
            key=lambda n: (n.onset_beat, n.part, n.pitch),
        )
        self.started = False
        self.paused = False  # the pianist stopped; strings wait for them to play again
        self.finished = False
        self.last_beat: float | None = None  # score beat of the last piano chord matched
        self.last_chord_t = -1.0
        self.pending: tuple[float, float, set[int]] | None = None  # chord being collected: (first t, last t, pitches)
        self.next_note = 0  # index into self.notes of the next note to start
        self.sounding: list[tuple[float, StringNote]] = []  # (end beat, note)
        self.drift = 0
        self.follower_beat: float | None = None  # the follower's latest estimate
        self.log: list[tuple[float, float, float]] = []  # (time, matched beat, asynchrony) per piano chord

    # --- inputs -------------------------------------------------------------------------------------

    def on_piano_onset(self, t: float, pitch: int) -> None:
        if self.finished:
            return
        if self.pending and t - self.pending[1] <= CHORD_GAP_S:
            self.pending[2].add(pitch)
            self.pending = (self.pending[0], t, self.pending[2])
        else:
            self._finish_chord()
            if self._late_chord_note(t, pitch):
                return
            self.pending = (t, t, {pitch})
        # Don't wait out the gap once every note of the expected chord is in.
        nxt = self._next_onset()
        if self.started and not self.paused and nxt is not None and self.piano_pitches[nxt] <= self.pending[2]:
            self._finish_chord()
        elif not self.started or self.paused:
            self._finish_chord()  # (re)starting: come in on the first note, don't wait for the chord

    def on_follower(self, t: float, beat: float) -> None:
        self.follower_beat = beat

    # --- output -------------------------------------------------------------------------------------

    def due(self, now: float) -> list[Out]:
        """String events whose time has come, in time order."""
        if self.pending and now - self.pending[1] > CHORD_GAP_S:
            self._finish_chord()
        if not self.started or self.paused or self.finished:
            return []
        out: list[Out] = []
        if self._pianist_stopped(now):
            self.paused = True
            out += [Out(now, "off", n.part, n.pitch, beat=n.onset_beat) for _, n in self.sounding]
            self.sounding.clear()
            return out
        for end, n in list(self.sounding):
            t_off = self._off_time(n)
            if t_off <= now:
                out.append(Out(t_off, "off", n.part, n.pitch, beat=end))
                self.sounding.remove((end, n))
        while self.next_note < len(self.notes):
            n = self.notes[self.next_note]
            t_on = self.clock.time_of(n.onset_beat) - (GRACE_S if n.is_grace else 0.0)
            if self.last_beat is not None and n.onset_beat <= self.last_beat:
                t_on = min(t_on, now)  # the pianist already played this beat, earlier than predicted: come in now
            if t_on > now:
                break
            out.append(Out(t_on, "on", n.part, n.pitch, n.velocity, n.onset_beat))
            self.sounding.append((n.onset_beat + n.duration_beat, n))
            self.next_note += 1
        if self.next_note >= len(self.notes) and not self.sounding:
            self.finished = True
        out.sort(key=lambda o: o.time)
        return out

    def flush(self, now: float) -> list[Out]:
        """Stop everything (end of run)."""
        out = [Out(now, "off", n.part, n.pitch, beat=n.onset_beat) for _, n in self.sounding]
        self.sounding.clear()
        self.finished = True
        return out

    # --- internals ----------------------------------------------------------------------------------

    def _finish_chord(self) -> None:
        if self.pending is None:
            return
        t, _, pitches = self.pending
        self.pending = None
        if not self.started or self.paused:
            # Starting, or starting again after a stop: the strings come in with the pianist's first note.
            # (Voice cues come in M4.)
            beat = self._resume_beat(frozenset(pitches)) if self.started else self.piano_onsets[0]
            if beat is None:
                return
            self.clock.anchor(t, beat)
            self.started, self.paused = True, False
            self.next_note = next((i for i, n in enumerate(self.notes) if n.onset_beat >= beat), len(self.notes))
            self._matched(t, beat, 0.0)
            return
        beat = self._match(t, frozenset(pitches))
        if beat is None:
            return  # stray notes that fit nothing nearby
        if self.last_beat is not None and beat <= self.last_beat:
            self.clock.anchor(t, beat)  # stepping back to fix an earlier wrong match: phase only
            asyn = 0.0
        else:
            asyn = self.clock.correct(t, beat, self.last_beat)
        self._matched(t, beat, asyn)
        self._check_drift(t)

    def _check_drift(self, t: float) -> None:
        f = self.follower_beat
        if f is None or abs(f - self.last_beat) <= DRIFT_BEATS:
            self.drift = 0
            return
        self.drift += 1
        if self.drift >= DRIFT_COUNT:
            # Trust the follower: jump to the piano chord at or before its estimate, keeping the tempo.
            beat = max((b for b in self.piano_onsets if b <= f), default=self.piano_onsets[0])
            self.clock.anchor(t, beat)
            self.last_beat = beat
            self.drift = 0

    def _matched(self, t: float, beat: float, asyn: float) -> None:
        self.last_beat = beat
        self.last_chord_t = t
        self.log.append((t, beat, asyn))

    def _next_onset(self) -> float | None:
        lo = self.last_beat if self.last_beat is not None else -float("inf")
        return next((b for b in self.piano_onsets if b > lo), None)

    def _resume_beat(self, pitches: frozenset[int]) -> float | None:
        """Where the pianist starts again after a stop: by pitch, from a beat and a half back to a beat on,
        preferring the next chord. Pianists often go back a little to restart."""
        nxt = self._next_onset()
        if nxt is None:
            return None
        last = self.last_beat if self.last_beat is not None else nxt
        cands = [b for b in self.piano_onsets if last - 1.5 <= b <= nxt + 1.0]
        i_next = self.piano_onsets.index(nxt)

        def score(b: float) -> float:
            ref = self.piano_pitches[b]
            return len(pitches & ref) / len(pitches | ref) - 0.05 * abs(self.piano_onsets.index(b) - i_next)

        best = max(cands, key=score)
        return best if score(best) > 0 else nxt

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
        return expected is not None and self.clock.beat_at(now) > expected + STOP_AFTER_BEATS
