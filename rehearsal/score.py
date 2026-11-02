"""Score model: notes, onsets in beats, bar numbers, as musicxml.py reads them (the same way the iPad does)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import musicxml

SCORE_DIR = Path(__file__).resolve().parent.parent / "scores"
DEFAULT_SCORE = SCORE_DIR / "schumann-op47-iii.mxl"
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def pitch_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


@dataclass(frozen=True)
class ScoreNote:
    part: str
    bar: int
    beat_in_bar: float  # 1-based, in quarter notes
    onset_beat: float  # absolute quarter-note position
    duration_beat: float
    pitch: int

    def __str__(self) -> str:
        return f"bar {self.bar:>3} beat {self.beat_in_bar:5.2f}  {pitch_name(self.pitch):<4} dur {self.duration_beat:g}"


class Score:
    def __init__(self, path: Path = DEFAULT_SCORE, unfold: bool = True):
        """`unfold`: repeats played out (the bars in playing order, so a bar number can come twice)."""
        self.path = Path(path)
        loaded = musicxml.load(self.path, unfold)
        self.title = loaded.title or self.path.stem
        self.composer = loaded.composer
        self.tempo = loaded.tempo
        self.tempos = loaded.tempos  # (beat, quarter notes a minute): every tempo mark, in playing order
        self.read_parts = loaded.parts

    def read(self, name: str) -> musicxml.Part:
        """musicxml.py's reading of a part: its notes, bars and loudness markings."""
        for p in self.read_parts:  # the part that is that instrument, else one whose name starts so
            if musicxml.instrument(p.names, p.program) == name:
                return p
        for p in self.read_parts:
            if p.name.lower().startswith(name.lower()):
                return p
        raise KeyError(f"no part {name!r}; have {self.part_names}")

    def has(self, name: str) -> bool:
        try:
            self.read(name)
            return True
        except KeyError:
            return False

    @property
    def part_names(self) -> list[str]:
        return [p.name for p in self.read_parts]

    def notes(self, name: str) -> list[ScoreNote]:
        part = self.read(name)
        res = []
        for n in part.notes:
            bar, beat = self.bar_at(n.onset, name)
            res.append(ScoreNote(part=part.name, bar=bar, beat_in_bar=beat, onset_beat=n.onset,
                                 duration_beat=n.duration, pitch=n.pitch))
        return res  # already in (onset, pitch) order

    def bars(self, name: str, first: int, last: int) -> dict[int, list[ScoreNote]]:
        by_bar: dict[int, list[ScoreNote]] = {b: [] for b in range(first, last + 1)}
        for n in self.notes(name):
            if first <= n.bar <= last:
                by_bar[n.bar].append(n)
        return by_bar

    def bar_starts(self, name: str = "Piano") -> list[tuple[float, int]]:
        """(quarter-note position, written bar number) for every bar of the unfolded part, in order."""
        return list(self.read(name).bars)

    def bar_at(self, beat: float, name: str = "Piano") -> tuple[int, float]:
        """Map an absolute (unfolded) beat position to (written bar, 1-based beat in bar)."""
        starts = self.bar_starts(name)
        i = max(0, int(np.searchsorted([b for b, _ in starts], beat, side="right")) - 1)
        return starts[i][1], beat - starts[i][0] + 1
