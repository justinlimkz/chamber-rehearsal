import json

from rehearsal import bundle
from rehearsal.score import Score


def test_bundle_has_bars_parts_and_the_score(tmp_path):
    out = bundle.export(Score(), tmp_path / "b")
    data = json.loads((out / "score.json").read_text())
    assert data["barStarts"][0] == [0.0, 1] and len(data["barStarts"]) == 137  # repeats unfolded
    assert set(data["parts"]) == {"Violin", "Viola", "Violoncello", "Piano"}
    assert all(len(n) == 3 for n in data["parts"]["Piano"])
    assert data["work"] == "Piano Quartet in E-flat major, Op. 47"
    assert "<score-partwise" in (out / "score.musicxml").read_text()  # the iPad engraves it


def test_starting_tempo_from_the_piece_file_else_the_score(tmp_path):
    assert json.loads((bundle.export(Score(), tmp_path / "b") / "score.json").read_text())["tempo"] == 63  # piece file
    assert bundle.score_data(Score())["tempo"] == 74  # the score's first metronome mark
    assert "tempo" not in bundle.score_data(Score("tests/musicxml/basic.musicxml"))  # none: the app starts at 63
