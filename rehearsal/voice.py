"""Voice restart cues: what the strings are asked to do, and the phrases that ask for it ("from bar 9", "from the
top", "again", "pause", "resume", ...). The iPad hears them with Apple's on-device speech recognition
(ios/App/Rehearsal/Voice.swift) and parses them as `parse` does here, digits too.
"""
from __future__ import annotations

from dataclasses import dataclass

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
