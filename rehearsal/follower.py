"""Score follower interface. Matchmaker sits behind it so it can be swapped for our own follower later."""
from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

from .score import Score


@dataclass(frozen=True)
class Position:
    beat: float  # absolute position in the unfolded score, in quarter notes
    bar: int
    beat_in_bar: float
    wall_time: float  # time.perf_counter() when the estimate was produced


class Follower(Protocol):
    def positions(self) -> Iterator[Position]:
        """Yield score-position estimates as the performance arrives."""


class MatchmakerFollower:
    """Matchmaker in MIDI mode, following the piano part only.

    `performance_file=None` listens to a live MIDI port; otherwise the file is streamed through.
    """

    def __init__(
        self,
        score: Score,
        performance_file: str | Path | None = None,
        method: str = "outerhmm",
        port: str | None = None,
        workdir: Path = Path("build"),
    ):
        from matchmaker import Matchmaker

        workdir.mkdir(exist_ok=True)
        piano_xml = score.export_part("Piano", workdir / "piano.musicxml")
        self.score = score
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # Matchmaker reloads the score with partitura, which chatters about slurs
            self.mm = Matchmaker(
                score_file=str(piano_xml),
                performance_file=str(performance_file) if performance_file else None,
                input_type="midi",
                method=method,
                device_name_or_index=port,
            )

    def stop(self) -> None:
        """Stop a live follower running in another thread (its MIDI stream thread would keep the process alive)."""
        stream = self.mm.stream
        if stream is not None and stream.is_alive():
            stream.stop()

    def positions(self) -> Iterator[Position]:
        for beat in self.mm.run(verbose=False):
            bar, bib = self.score.bar_at(float(beat))
            yield Position(float(beat), bar, bib, time.perf_counter())
