"""score.json variants with other dynamics settings (rehearsal/dynamics.py), for tone candidates: the app's bundle
with only its `dynamics` curves redone. The engine is unchanged; render them with `"score"` in a candidate.

    uv run python -m tone.dynamics_variants
"""
from __future__ import annotations

import json
from pathlib import Path

from rehearsal import dynamics
from rehearsal.accompanist import STRING_PARTS
from rehearsal.score import Score

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "ios/Bundles/schumann-op47-iii/score.json"
OUT = ROOT / "build/tone/scores"
VARIANTS = {"gentle": dict(step_db=3.0, ease=8), "gentler": dict(step_db=2.0, ease=8)}


def main() -> None:
    score, data = Score(), json.loads(BUNDLE.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    for name, kw in VARIANTS.items():
        data["dynamics"] = {p: [list(x) for x in pts] for p, pts in dynamics.curves(score, STRING_PARTS, **kw).items()}
        (OUT / f"{name}.json").write_text(json.dumps(data))
        print(OUT / f"{name}.json")


if __name__ == "__main__":
    main()
