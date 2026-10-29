import json

from rehearsal import bundle
from rehearsal.score import Score


def test_bundle_has_bars_parts_and_engraving(tmp_path):
    out = bundle.export(Score(), tmp_path / "b")
    data = json.loads((out / "score.json").read_text())
    assert data["barStarts"][0] == [0.0, 1] and len(data["barStarts"]) == 137  # repeats unfolded
    assert set(data["parts"]) == {"Violin", "Viola", "Violoncello", "Piano"}
    assert all(len(n) == 3 for n in data["parts"]["Piano"])
    assert data["leadership"][0]["firstBar"] == 1
    assert len(json.loads((out / "anchors.json").read_text())) == 132
    assert (out / "engraving.svg").read_text().startswith("<svg")
