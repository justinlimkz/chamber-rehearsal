import pytest

from rehearsal import voice
from rehearsal.voice import Cue


@pytest.mark.parametrize("n", [1, 9, 13, 20, 47, 100, 101, 123, 132])
def test_number_words_round_trip(n):
    assert voice.words_number(voice.number_words(n).split()) == n


@pytest.mark.parametrize("text,cue", [
    ("from bar nine", Cue("bar", 9)),
    ("from measure twelve", Cue("bar", 12)),
    ("from the pickup to bar five", Cue("bar", 5, pickup=True)),
    ("from the pickup to measure one hundred twenty three", Cue("bar", 123, pickup=True)),
    ("from the top", Cue("top")),
    ("from the beginning", Cue("top")),
    ("again", Cue("again")),
    ("[unk] [unk] from bar sixteen", Cue("bar", 16)),
    ("[unk]", None),
    ("from bar", None),
    ("", None),
])
def test_parse(text, cue):
    assert voice.parse(text) == cue


def test_pause_and_resume_phrases():
    for text in ("pause", "stop"):
        assert voice.parse(text) == Cue("pause")
    for text in ("resume", "continue", "come back"):
        assert voice.parse(text) == Cue("resume")


@pytest.mark.parametrize("text", ["go", "Go", "count in", "count us in"])
def test_go(text):
    assert voice.parse(text) == Cue("go")
