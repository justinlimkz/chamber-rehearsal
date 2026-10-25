"""Offline accuracy of the follower on synthetic takes, and on real takes against a reference alignment."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from . import midi_io, reference, synth
from .follower import MatchmakerFollower
from .score import Score

SCENARIOS = {
    "clean": dict(),
    "wrong-notes": dict(wrong_rate=0.05),
    "dropped-notes": dict(drop_rate=0.10),
    "extra-notes": dict(extra_rate=0.08),
    "messy": dict(wrong_rate=0.04, drop_rate=0.06, extra_rate=0.05, rubato=0.15, jitter_ms=25),
}


def score_alignment(path: np.ndarray, truth: list[tuple[float, float]], window=(-0.03, 0.15)) -> dict:
    """Compare the follower's (score beat, performance time) path to ground-truth onsets.

    For each true onset group, take the follower's last estimate in a short window around it.
    """
    beats, times = path[0].astype(float), path[1].astype(float)
    errs, missing = [], 0
    for t, b in truth:
        sel = np.flatnonzero((times >= t + window[0]) & (times <= t + window[1]))
        if len(sel) == 0:
            missing += 1
            continue
        errs.append(beats[sel[-1]] - b)
    e = np.abs(np.array(errs)) if errs else np.array([np.inf])
    return {
        "onsets": len(truth),
        "no_estimate": missing,
        "within_0.25_beat": round(float(np.mean(e <= 0.25)), 3),
        "within_1_beat": round(float(np.mean(e <= 1.0)), 3),
        "median_abs_err_beats": round(float(np.median(e)), 3),
        "max_abs_err_beats": round(float(np.max(e)), 3),
        "final_err_beats": round(float(errs[-1]), 3) if errs else None,
    }


def run_take(score: Score, events, truth, method: str, workdir: Path = Path("build")) -> dict:
    workdir.mkdir(exist_ok=True)
    take_path = workdir / "eval_take.mid"
    midi_io.write_take(events, take_path)
    t0 = time.perf_counter()
    f = MatchmakerFollower(score, take_path, method=method, workdir=workdir)
    for _ in f.positions():
        pass
    out = score_alignment(f.mm.score_follower.alignment_path, truth)
    out["wall_s"] = round(time.perf_counter() - t0, 2)
    return out


def _log(log: Path | None, row: dict) -> None:
    if log:
        with open(log, "a") as fh:
            fh.write(json.dumps({**row, "date": time.strftime("%Y-%m-%d %H:%M")}) + "\n")


def run_recorded(score: Score, takes_dir: Path, first_bar: int, last_bar: int, method: str, log: Path | None = None) -> list[dict]:
    """Score the follower on recorded takes; ground truth comes from `reference.align`."""
    rows = []
    for path in sorted(Path(takes_dir).glob("*.mid")):
        events = midi_io.read_take(path)
        ref = reference.align(score, events, first_bar, last_bar)
        row = {"method": method, "bars": f"{first_bar}-{last_bar}", "scenario": path.stem, "seed": "-",
               "ref_skipped": len(ref.skipped), "ref_extra": len(ref.extra)}
        row.update(run_take(score, events, ref.truth, method))
        rows.append(row)
        _log(log, row)
    return rows


def run_scenarios(score: Score, first_bar: int, last_bar: int, method: str, seeds=(0, 1, 2), log: Path | None = None) -> list[dict]:
    rows = []
    for name, kw in SCENARIOS.items():
        for seed in seeds:
            take = synth.make_take(score, first_bar, last_bar, seed=seed, **kw)
            row = {"method": method, "bars": f"{first_bar}-{last_bar}", "scenario": name, "seed": seed}
            row.update(run_take(score, take.events, take.truth, method))
            rows.append(row)
            _log(log, row)
    return rows
