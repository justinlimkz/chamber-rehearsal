"""Draft a leadership annotation for a whole piece from the score alone.

The rule ensemble players use: the part with the melody shapes the timing, and the parts accompanying
it follow. For each bar, every line gets a melody score from its texture:

- occupancy: how much of the bar it sounds (a melody sings through; interjections and rests don't),
- activity: onsets per beat, up to one (a held bass note a bar is a bass line, not a tune),
- periodicity: how much its pitch sets repeat with a short period, 1 to 4 onsets (repeated chords,
  Alberti basses and steady arpeggios do; tunes move on, though they may repeat a note),
- thickness: notes per onset (accompaniment chords are thick; melodies are single lines),
- markings: "cantabile", "espressivo", "solo", "dolce" on the part, or a recently marked dynamic
  louder than the quietest part's.

Lines are the three strings and the piano's two staves. The strings' best line is compared with the
piano's: whichever side is clearly more melodic leads, and close calls are shared. A side that is
silent can't lead. Bars are then smoothed into passages (Viterbi with a cost per change), so a single
odd bar doesn't flip the setting.

Each passage gets a coupling, how far the strings adjust their timing towards the pianist (presets per
setting). A string melody over a steady piano pulse gets a firmer coupling than a free one: the melody
shapes the phrase, but the pulse holds things together.

The output is a YAML file next to the score, with a reason on each passage, meant as a draft to correct.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import partitura as pt
import yaml

from .score import Score

STRING_PARTS = ("Violin", "Viola", "Violoncello")
COUPLING = {"piano": 0.7, "shared": 0.4, "strings": 0.15}
COUPLING_OVER_PULSE = 0.3  # strings lead, but the piano keeps a steady subdivision under them
PULSE_MISMATCH = 0.3  # cost of the free / over-pulse strings setting where the piano does / doesn't keep a pulse
# Viterbi states: (lead, coupling). Strings-lead comes in two strengths, so they split into passages too.
STATES = [("piano", COUPLING["piano"]), ("shared", COUPLING["shared"]), ("strings", COUPLING["strings"]),
          ("strings", COUPLING_OVER_PULSE)]
SHARED_MARGIN = 0.3  # |strings - piano| melody scores below half this are shared
SWITCH_COST = 0.6  # Viterbi cost of changing setting between bars
MAX_PERIOD = 4  # periodicity looks for patterns repeating every 1-4 onsets...
PERIOD_WEIGHT = 0.7  # ...and a fully periodic line keeps 30% of its melody score
MARK_WORDS = ("cantabile", "cant", "espressivo", "espr", "solo", "dolce")
MARK_BONUS = 0.15  # melody score bonus for an expressive marking, for MARK_BARS bars after it
MARK_BARS = 8
LOUD_BONUS = 0.1  # bonus for a dynamic, marked in the last MARK_BARS bars, louder than the quietest part's
DYNAMICS = {"ppp": 0, "pp": 1, "p": 2, "mp": 3, "mf": 4, "f": 5, "ff": 6, "fff": 7}


@dataclass
class Line:
    name: str  # "Violin", "Piano RH", ...
    occupancy: float = 0.0
    onsets: int = 0
    activity: float = 0.0
    periodicity: float = 0.0
    thickness: float = 0.0
    bonus: float = 0.0
    marks: list[str] = field(default_factory=list)

    @property
    def melody(self) -> float:
        if self.onsets == 0:
            return 0.0
        return (self.occupancy * self.activity * (1 - PERIOD_WEIGHT * self.periodicity) / np.sqrt(self.thickness)
                + self.bonus)

    def texture(self) -> str:
        if self.onsets == 0:
            return "rests"
        if self.periodicity > 0.5:
            return "repeated chords" if self.thickness >= 2 else "figuration"
        if self.occupancy < 0.5:
            return "interjections"
        return "chords" if self.thickness >= 2 else "melody"


@dataclass
class Bar:
    number: int
    beats: float
    strings: list[Line]
    piano: list[Line]
    piano_onsets: int  # distinct piano onset times, both staves

    def best(self, side: list[Line]) -> Line:
        return max(side, key=lambda line: line.melody)

    @property
    def piano_pulse(self) -> bool:
        """The piano keeps a steady subdivision: at least two onsets a beat on average."""
        return self.piano_onsets >= 2 * self.beats


@dataclass
class Passage:
    first_bar: int
    last_bar: int
    lead: str  # "piano" | "shared" | "strings"
    coupling: float
    reason: str

    def as_dict(self) -> dict:
        bars = f"{self.first_bar}-{self.last_bar}" if self.last_bar != self.first_bar else str(self.first_bar)
        return {"bars": bars, "lead": self.lead, "coupling": self.coupling, "reason": self.reason}


def _line_features(name: str, notes: list, start: int, end: int, beats: float,
                   history: list[frozenset[int]]) -> Line:
    """Texture of one line in one bar (times in divs). `history` holds the line's pitch sets of the
    previous bar and is replaced by this bar's, so a pattern is judged across the bar line too."""
    line = Line(name)
    span = end - start
    covered = np.zeros(span, dtype=bool)
    onsets: dict[int, set[int]] = {}
    for n in notes:
        s, e = n.start.t, n.start.t + n.duration_tied
        if e <= start or s >= end:
            continue
        covered[max(s, start) - start:min(e, end) - start] = True
        if s >= start:
            onsets.setdefault(s, set()).add(n.midi_pitch)
    line.occupancy = float(covered.mean())
    line.onsets = len(onsets)
    if not onsets:
        history.clear()
        return line
    chords = [frozenset(onsets[t]) for t in sorted(onsets)]
    seq = history + chords
    line.periodicity = max(
        np.mean([seq[i] == seq[i - k] for i in range(len(history), len(seq)) if i >= k] or [0.0])
        for k in range(1, MAX_PERIOD + 1))
    history[:] = chords
    line.activity = min(1.0, len(onsets) / beats)
    line.thickness = float(np.mean([len(p) for p in onsets.values()]))
    return line


def _words(part) -> list[tuple[int, str]]:
    """(time, lower-case text) of the part's text directions and words."""
    res = [(d.start.t, (getattr(d, "text", "") or "").lower())
           for d in part.iter_all(pt.score.Direction, include_subclasses=True)]
    res += [(w.start.t, (w.text or "").lower()) for w in part.iter_all(pt.score.Words)]
    return res


def bars(score: Score) -> list[Bar]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return _bars(score)


def _bars(score: Score) -> list[Bar]:
    parts = {p.part_name: p for p in score.written}
    piano = next(p for name, p in parts.items() if name.lower().startswith("piano"))
    measures = list(piano.iter_all(pt.score.Measure))
    q = piano.quarter_map

    lines: list[tuple[str, object, list]] = []
    for name in STRING_PARTS:
        part = next(p for n, p in parts.items() if n.lower().startswith(name.lower()))
        lines.append((name, part, list(part.notes_tied)))
    rh = [n for n in piano.notes_tied if n.staff == 1]
    lh = [n for n in piano.notes_tied if n.staff != 1]
    lines += [("Piano RH", piano, rh), ("Piano LH", piano, lh)]

    # Expressive marks and dynamics per part, as (bar index, value).
    part_measures = {name: list(part.iter_all(pt.score.Measure)) for name, part, _ in lines}
    marks: dict[str, list[tuple[int, str]]] = {}
    dyn: dict[str, list[tuple[int, int]]] = {}
    for name, part, _ in lines:
        starts = [m.start.t for m in part_measures[name]]
        ws = [(max(0, int(np.searchsorted(starts, t, side="right")) - 1), w) for t, w in _words(part)]
        marks[name] = [(i, w) for i, w in ws if any(w.startswith(k) for k in MARK_WORDS)]
        dyn[name] = [(i, DYNAMICS[w]) for i, w in ws if w in DYNAMICS]

    def dynamic_at(name: str, i: int) -> tuple[int, int] | None:
        """(bar index where marked, level) of the part's current dynamic."""
        past = [(j, v) for j, v in dyn[name] if j <= i]
        return past[-1] if past else None

    histories = {name: [] for name, _, _ in lines}
    res = []
    for i, m in enumerate(measures):
        beats = float(q(m.end.t) - q(m.start.t))
        levels = {name: dynamic_at(name, i) for name, _, _ in lines}
        quietest = min((v for _, v in filter(None, levels.values())), default=None)
        feats = []
        for name, _, notes in lines:
            pm = part_measures[name][i]
            line = _line_features(name, notes, pm.start.t, pm.end.t, beats, histories[name])
            line.marks = [w for j, w in marks[name] if i - MARK_BARS < j <= i]
            line.bonus = MARK_BONUS * bool(line.marks)
            level = levels[name]
            if level is not None and i - level[0] < MARK_BARS and level[1] > quietest:
                line.bonus += LOUD_BONUS
            feats.append(line)
        piano_onsets = len({n.start.t for n in piano.notes_tied if m.start.t <= n.start.t < m.end.t})
        res.append(Bar(int(m.number), beats, feats[:3], feats[3:], piano_onsets))
    return res


def _emissions(bar: Bar) -> list[float]:
    """Score of each state in STATES for one bar (higher is better)."""
    s, p = bar.best(bar.strings).melody, bar.best(bar.piano).melody
    strings_play = any(line.onsets for line in bar.strings)
    piano_plays = any(line.onsets for line in bar.piano)
    if strings_play and not piano_plays:
        lead = {"piano": -9.0, "shared": -9.0, "strings": 1.0}
    elif piano_plays and not strings_play:
        lead = {"piano": 1.0, "shared": -9.0, "strings": -9.0}
    elif not (strings_play or piano_plays):
        lead = {"piano": 0.0, "shared": 0.0, "strings": 0.0}
    else:
        d = s - p
        lead = {"piano": -d, "shared": SHARED_MARGIN - abs(d), "strings": d}
    res = []
    for name, coupling in STATES:
        e = lead[name]
        if name == "strings" and (coupling == COUPLING_OVER_PULSE) != bar.piano_pulse:
            e -= PULSE_MISMATCH
        res.append(e)
    return res


def leadership(bs: list[Bar]) -> list[tuple[str, float]]:
    """Best (lead, coupling) per bar, smoothed: Viterbi over the per-bar scores with a cost per change."""
    labels = STATES
    em = np.array([_emissions(b) for b in bs])
    score = em[0].copy()
    back = np.zeros((len(bs), len(labels)), dtype=int)
    for i in range(1, len(bs)):
        trans = score[:, None] - SWITCH_COST * (1 - np.eye(len(labels)))
        back[i] = trans.argmax(axis=0)
        score = trans.max(axis=0) + em[i]
    path = [int(score.argmax())]
    for i in range(len(bs) - 1, 0, -1):
        path.append(back[i][path[-1]])
    return [labels[k] for k in reversed(path)]


def _reason(lead: str, run: list[Bar]) -> str:
    def common(items: list[str]) -> str:
        return max(set(items), key=items.count)

    def _who(run: list[Bar]) -> str:
        """The string lines that carry the melody in at least a quarter of the bars, in order of entry."""
        names = [b.best(b.strings).name for b in run]
        main = [n for n in dict.fromkeys(names) if names.count(n) >= max(1, len(names) / 4)]
        return main[0] if len(main) == 1 else ", ".join(main[:-1]) + " and " + main[-1]

    strings_play = [b for b in run if any(line.onsets for line in b.strings)]
    piano_plays = [b for b in run if any(line.onsets for line in b.piano)]
    if not piano_plays:
        return "strings alone"
    if not strings_play:
        return "piano alone"
    s_line = _who(strings_play)
    s_tex = common([b.best(b.strings).texture() for b in strings_play])
    p_line = common([b.best(b.piano).name for b in piano_plays])
    p_tex = common([b.best(b.piano).texture() for b in piano_plays])
    marks = sorted({w for b in run for line in b.strings + b.piano for w in line.marks})
    marked = f" (marked {', '.join(marks)})" if marks else ""
    if lead == "strings":
        return f"{s_line} {s_tex} over piano {p_tex}{marked}"
    if lead == "piano":
        return f"piano {p_tex} ({p_line}) over strings {s_tex}{marked}"
    return f"{s_line} {s_tex} with piano {p_tex}{marked}"


def annotate(score: Score) -> list[Passage]:
    bs = bars(score)
    labels = leadership(bs)
    passages = []
    i = 0
    while i < len(bs):
        j = i
        while j + 1 < len(bs) and labels[j + 1] == labels[i]:
            j += 1
        run = bs[i:j + 1]
        lead, coupling = labels[i]
        passages.append(Passage(run[0].number, run[-1].number, lead, coupling, _reason(lead, run)))
        i = j + 1
    return passages


def annotation_path(score_path: Path) -> Path:
    return score_path.with_suffix(".lead.yaml")


def write(score: Score, passages: list[Passage], path: Path | None = None) -> Path:
    path = path or annotation_path(score.path)
    header = (f"# Leadership for {score.path.name}, drafted by `rehearsal annotate` from the score.\n"
              f"# lead: piano | shared | strings. coupling: how far the strings adjust their timing towards\n"
              f"# the pianist (0-1; presets piano {COUPLING['piano']}, shared {COUPLING['shared']}, "
              f"strings {COUPLING['strings']}, strings over a steady piano pulse {COUPLING_OVER_PULSE}).\n"
              f"# Edit freely: correct the passages you hear differently.\n")
    body = yaml.safe_dump([p.as_dict() for p in passages], sort_keys=False, allow_unicode=True, width=120)
    path.write_text(header + body)
    return path


def load(path: Path) -> list[Passage]:
    res = []
    for d in yaml.safe_load(path.read_text()) or []:
        first, _, last = str(d["bars"]).partition("-")
        lead = d["lead"]
        res.append(Passage(int(first), int(last or first), lead, float(d.get("coupling", COUPLING[lead])), d.get("reason", "")))
    return res
