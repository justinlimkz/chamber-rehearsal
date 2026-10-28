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
