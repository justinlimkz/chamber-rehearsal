"""Voice restart cues, recognised locally and offline with Vosk.

Recognition is constrained to a small grammar of rehearsal phrases ("from bar 9", "from the top",
"again", "pause", "resume", ...), which makes it fast and robust with the small English model. The listener only feeds
audio to the recogniser while `active` is set (the strings set it while waiting), so the piano and
strings don't trigger it.

Model: vosk-model-small-en-us-0.15 (Apache 2.0), unpacked into `models/` (not in git):
    https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
"""
from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "vosk-model-small-en-us-0.15"
SAMPLE_RATE = 16000
MAX_BAR = 199

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
PAUSE = ("pause", "stop")  # the strings sit out while you practise alone
RESUME = ("resume", "continue", "come back")


@dataclass(frozen=True)
class Cue:
    kind: str  # "bar" | "top" | "again" | "pause" (strings sit out) | "resume"
    bar: int = 0
    pickup: bool = False  # "from the pickup to bar N"

    def __str__(self) -> str:
        if self.kind == "bar":
            return f"from the pickup to bar {self.bar}" if self.pickup else f"from bar {self.bar}"
        return {"top": "from the top", "again": "again", "pause": "pause", "resume": "resume"}[self.kind]


def number_words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    rest = n % 100
    return ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + number_words(rest))


def words_number(words: list[str]) -> int | None:
    """Inverse of `number_words` (also accepts "a hundred", "and")."""
    total, current = 0, 0
    for w in words:
        if w in ("and",):
            continue
        if w in ONES:
            current += ONES.index(w)
        elif w in TENS and w:
            current += 10 * TENS.index(w)
        elif w == "hundred":
            current = max(current, 1) * 100
        elif w == "a":
            current += 1  # "a hundred"
        else:
            return None
    total += current
    return total or None


def grammar(max_bar: int = MAX_BAR) -> list[str]:
    phrases = ["from the top", "from the beginning", "again", *PAUSE, *RESUME, "[unk]"]
    for n in range(1, max_bar + 1):
        w = number_words(n)
        phrases += [f"from bar {w}", f"from measure {w}", f"from the pickup to bar {w}", f"from the pickup to measure {w}"]
    return phrases


def parse(text: str) -> Cue | None:
    words = text.lower().replace("[unk]", "").split()
    if not words:
        return None
    if words == ["again"]:
        return Cue("again")
    if " ".join(words) in PAUSE:
        return Cue("pause")
    if " ".join(words) in RESUME:
        return Cue("resume")
    if words[:3] in (["from", "the", "top"], ["from", "the", "beginning"]):
        return Cue("top")
    pickup = words[:4] == ["from", "the", "pickup", "to"]
    rest = words[4:] if pickup else words[1:] if words[:1] == ["from"] else None
    if not rest or rest[0] not in ("bar", "measure"):
        return None
    n = words_number(rest[1:])
    return Cue("bar", n, pickup) if n else None


class Listener:
    """Microphone -> Vosk -> `on_cue(cue)` in a background thread, while `active` is set."""

    def __init__(self, on_cue: Callable[[Cue], None], max_bar: int = MAX_BAR, model_dir: Path = MODEL_DIR):
        import vosk

        vosk.SetLogLevel(-1)
        if not model_dir.exists():
            raise FileNotFoundError(f"Vosk model not found at {model_dir}; see rehearsal/voice.py for the download.")
        self.rec = vosk.KaldiRecognizer(vosk.Model(str(model_dir)), SAMPLE_RATE, json.dumps(grammar(max_bar)))
        self.on_cue = on_cue
        self.active = threading.Event()
        self.q: queue.Queue[bytes] = queue.Queue()
        self._was_active = False
        self._stream = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        import sounddevice as sd

        self._stream = sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=1600, dtype="int16", channels=1,
                                         callback=lambda data, frames, t, status: self.q.put(bytes(data)))
        self._stream.start()
        self._thread.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

    def _run(self) -> None:
        while True:
            data = self.q.get()
            if not self.active.is_set():
                self._was_active = False
                continue
            if not self._was_active:
                self.rec.Reset()  # forget audio from before we started listening
                self._was_active = True
            if self.rec.AcceptWaveform(data):
                cue = parse(json.loads(self.rec.Result()).get("text", ""))
                if cue is not None:
                    self.on_cue(cue)


def recognise_file(path: Path, max_bar: int = MAX_BAR, model_dir: Path = MODEL_DIR) -> list[str]:
    """Recognise a 16 kHz mono 16-bit WAV with the cue grammar (for testing)."""
    import wave

    import vosk

    vosk.SetLogLevel(-1)
    rec = vosk.KaldiRecognizer(vosk.Model(str(model_dir)), SAMPLE_RATE, json.dumps(grammar(max_bar)))
    texts = []
    with wave.open(str(path)) as w:
        while data := w.readframes(4000):
            if rec.AcceptWaveform(data):
                texts.append(json.loads(rec.Result()).get("text", ""))
    texts.append(json.loads(rec.FinalResult()).get("text", ""))
    return [t for t in texts if t]
