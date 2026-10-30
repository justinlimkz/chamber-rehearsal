"""Score model: notes, onsets in beats, bar numbers. Notes, bars and dynamics are read by musicxml.py (as the iPad
reads them); partitura's reading is kept for the leadership guess (annotate.py) and the follower's piano part."""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import partitura as pt

from . import musicxml

SCORE_DIR = Path(__file__).resolve().parent.parent / "scores"
DEFAULT_SCORE = SCORE_DIR / "piano-quartet-op47-3rd-mov-robert-schumann.mxl"
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
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # partitura chatters about unparsed text directions and slurs
            loaded = pt.load_musicxml(str(path))
            self.path = Path(path)
            self.title = loaded.title or self.path.stem
            self.composer = loaded.composer or ""
            # Repeats (bars 64-70) are not unfolded by default; unfold for score following.
            # Unfolded parts repeat bar numbers, so `bar` below is the written bar number.
            self.written = list(loaded.parts)
            self.parts = [pt.score.unfold_part_maximal(p) for p in self.written] if unfold else self.written
        self.read_parts = musicxml.load(self.path, unfold).parts

    def read(self, name: str) -> musicxml.Part:
        """musicxml.py's reading of a part: its notes, bars and loudness markings."""
        for p in self.read_parts:
            if p.name.lower().startswith(name.lower()):
                return p
        raise KeyError(f"no part {name!r}; have {self.part_names}")

    @property
    def part_names(self) -> list[str]:
        return [p.part_name for p in self.parts]

    def part(self, name: str):
        for p in self.parts:
            if p.part_name.lower().startswith(name.lower()):
                return p
        raise KeyError(f"no part {name!r}; have {self.part_names}")

    def notes(self, name: str) -> list[ScoreNote]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return self._notes(name)

    def _notes(self, name: str) -> list[ScoreNote]:
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

    def note_array(self, name: str) -> np.ndarray:
        return pt.utils.music.note_array_from_part(self.part(name))

    def bar_starts(self, name: str = "Piano") -> list[tuple[float, int]]:
        """(quarter-note position, written bar number) for every bar of the unfolded part, in order."""
        return list(self.read(name).bars)

    def bar_at(self, beat: float, name: str = "Piano") -> tuple[int, float]:
        """Map an absolute (unfolded) beat position to (written bar, 1-based beat in bar)."""
        starts = self.bar_starts(name)
        i = max(0, int(np.searchsorted([b for b, _ in starts], beat, side="right")) - 1)
        return starts[i][1], beat - starts[i][0] + 1

    def export_part(self, name: str, path: Path) -> Path:
        """Write one (folded) part as its own MusicXML file, e.g. for a follower that wants only the piano."""
        written = next(p for p in self.written if p.part_name.lower().startswith(name.lower()))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pt.save_musicxml(written, str(path))
        return Path(path)
