import json
import threading
import urllib.request

import pytest

from rehearsal import app
from rehearsal.accompanist import Accompanist
from rehearsal.score import Score


@pytest.fixture(scope="module")
def score():
    return Score()


def test_score_layout_has_every_bar_with_notes_in_lanes(score):
    acc = Accompanist(score, 1, 16)
    layout = app.score_layout(score, acc)
    assert [b["n"] for b in layout["bars"]] == list(range(1, 17))
    bar4 = layout["bars"][3]
    lanes = {n["lane"] for n in bar4["notes"]}
    assert lanes == {0, 1, 2, 3}  # violin, viola, cello and piano all play in bar 4
    assert all(0 <= n["x"] < 1 and 0 <= n["y"] <= 1 for b in layout["bars"] for n in b["notes"])
    assert bar4["beats"] == pytest.approx(3)


def test_server_serves_page_score_events_and_takes_cues(score):
    cues, ended, scans = [], threading.Event(), []
    state = {"state": "ready", "now": 0.0}
    server = app.AppServer({"bars": []}, lambda: dict(state), cues.append, ended.set, lambda: scans.append(1), port=0)
    url = server.start()
    try:
        assert b"Rehearsal" in urllib.request.urlopen(url).read()
        assert json.loads(urllib.request.urlopen(url + "score").read()) == {"bars": []}
        with urllib.request.urlopen(url + "events", timeout=5) as r:
            line = r.readline()
        assert json.loads(line.decode()[len("data: "):]) == state
        post = lambda path, body: urllib.request.urlopen(urllib.request.Request(
            url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
        post("cue", {"text": "9"})
        post("scan", {})
        post("end", {})
        assert cues == ["9"] and scans == [1] and ended.is_set()
    finally:
        server.stop()


def test_engraving_numbers_every_bar(score):
    from rehearsal import engrave

    svg, anchors = engrave.engrave(score.path)
    assert engrave.bar_numbers(svg) == list(range(1, 133))
    assert sorted(anchors) == list(range(1, 133))
    assert [round(f, 3) for f, _ in anchors[3]] == [0, 0.167, 0.333, 0.5, 0.667, 0.833]  # eighths in 3/4
    assert all(f'id="{i}"' in svg for f, ids in anchors[3] for i in ids)
    assert svg.count('class="system"') > 20  # one tall page, a system under another


def test_server_serves_ipad_page_and_engraving(score):
    import gzip

    server = app.AppServer({"bars": []}, lambda: {"state": "ready", "now": 0.0}, lambda t: None, lambda: None,
                           port=0, score_path=score.path)
    url = server.start()
    try:
        assert b"Rehearsal score" in urllib.request.urlopen(url + "ipad").read()
        with urllib.request.urlopen(urllib.request.Request(url + "engraving.svg", headers={"Accept-Encoding": "gzip"})) as r:
            assert r.headers["Content-Encoding"] == "gzip"
            assert b'data-n="132"' in gzip.decompress(r.read())
        assert len(json.loads(urllib.request.urlopen(url + "anchors").read())) == 132
        assert b'data-n="1"' in urllib.request.urlopen(url + "engraving.svg").read()  # plain for clients without gzip
    finally:
        server.stop()
