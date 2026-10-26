import shutil
import subprocess

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


@pytest.mark.skipif(not voice.MODEL_DIR.exists() or not shutil.which("say"), reason="needs the Vosk model and macOS say")
@pytest.mark.parametrize("spoken,cue", [
    ("from bar nine", Cue("bar", 9)),
    ("from the pickup to bar five", Cue("bar", 5, pickup=True)),
    ("from the top", Cue("top")),
    ("again", Cue("again")),
    ("okay, let's go from measure sixteen", Cue("bar", 16)),
    ("could you play louder", None),
])
def test_recognises_synthetic_speech(tmp_path, spoken, cue):
    aiff, wav = tmp_path / "s.aiff", tmp_path / "s.wav"
    subprocess.run(["say", "-o", str(aiff), spoken], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
    cues = [c for c in map(voice.parse, voice.recognise_file(wav)) if c]
    assert cues == ([cue] if cue else [])
