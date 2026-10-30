"""A check of a MusicXML score for the mistakes that music recognition (OMR) and quick note entry leave behind,
so proofreading can start from a list of suspicious bars instead of reading every bar.

Each bar of each part, as written (repeats not played out):
- a bar whose notes run past its time signature, or don't fill it (the usual sign of a misread rhythm, dot or
  tuplet);
- parts with different numbers of bars;
- notes out of the instrument's range (often a misread clef or octave line);
- a tie that isn't continued at the same pitch by the end of the next bar.
A short first bar is a pickup, a short last bar its complement, and a bar marked `implicit` is meant to be short;
none of these is reported.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from . import musicxml
from .score import pitch_name

RANGES = {"Violin": (55, 105), "Viola": (48, 93), "Violoncello": (34, 84), "Piano": (21, 108)}  # MIDI, generous


@dataclass
class Problem:
    part: str
    bar: str  # the printed bar number
    index: int  # 0-based position of the bar in the part
    text: str

    def __str__(self) -> str:
        return f"bar {self.bar:>4}  {self.part:<12} {self.text}"


def _instrument(names: list[str]) -> str | None:
    for n in names:
        n = n.lower().replace(".", " ").strip()
        first = n.split()[0] if n.split() else n
        if "cello" in n or first in ("vc", "vlc"):
            return "Violoncello"
        if n.startswith("viola") or first in ("va", "vla"):
            return "Viola"
        if n.startswith("violin") or first in ("vn", "vln", "vl"):
            return "Violin"
        if "piano" in n or first in ("pno", "pf"):
            return "Piano"
    return None


def check(path: Path) -> list[Problem]:
    root = ET.fromstring(musicxml.text(path))
    names = {}
    for sp in root.findall("part-list/score-part"):
        ns = [sp.findtext("part-name", ""), sp.findtext("part-abbreviation", "")]
        ns += [si.findtext("instrument-name", "") for si in sp.findall("score-instrument")]
        names[sp.get("id")] = [n.strip() for n in ns if n and n.strip()]
    problems: list[Problem] = []
    counts = {}
    for part in root.findall("part"):
        pid = part.get("id", "")
        label = names.get(pid, [pid])[0] if names.get(pid) else pid
        low, high = RANGES.get(_instrument(names.get(pid, [])) or "", (0, 127))
        divisions, nominal = Fraction(1), Fraction(4)
        open_ties: dict[int, tuple[str, int]] = {}  # pitch -> (bar, index) where a tie started
        measures = part.findall("measure")
        counts[label] = len(measures)
        for i, m in enumerate(measures):
            bar = (m.get("number") or str(i + 1)).strip()
            pos = Fraction(0)
            ends: dict[str, Fraction] = {}  # per voice, where its last note ends
            for e in m:
                if e.tag == "attributes":
                    d = e.findtext("divisions")
                    if d and float(d) > 0:
                        divisions = Fraction(d)
                    t = e.find("time")
                    if t is not None and t.findtext("beats") and t.findtext("beat-type"):
                        try:
                            beats = sum(Fraction(x) for x in t.findtext("beats").split("+"))
                            nominal = beats * 4 / Fraction(t.findtext("beat-type"))
                        except (ValueError, ZeroDivisionError):
                            pass
                elif e.tag == "backup":
                    pos -= Fraction(e.findtext("duration", "0")) / divisions
                elif e.tag == "forward":
                    pos += Fraction(e.findtext("duration", "0")) / divisions
                elif e.tag == "note" and e.find("chord") is None and e.find("grace") is None:
                    pos += Fraction(e.findtext("duration", "0")) / divisions
                    voice = e.findtext("voice", "1")
                    ends[voice] = max(ends.get(voice, Fraction(0)), pos)
                if e.tag == "note":
                    p = e.find("pitch")
                    if p is None or e.find("cue") is not None:
                        continue
                    step, octave = p.findtext("step"), p.findtext("octave")
                    if step not in musicxml.STEPS or not (octave or "").lstrip("-").isdigit():
                        problems.append(Problem(label, bar, i, "a note with no readable pitch"))
                        continue
                    pitch = (int(octave) + 1) * 12 + musicxml.STEPS[step] + round(float(p.findtext("alter") or 0))
                    if not low <= pitch <= high:
                        problems.append(Problem(label, bar, i, f"{pitch_name(pitch)} is out of range"))
                    ties = [t.get("type") for t in e.findall("tie")]
                    if "stop" in ties:
                        open_ties.pop(pitch, None)
                    if "start" in ties:
                        open_ties[pitch] = (bar, i)
            for pitch, (start_bar, start_i) in list(open_ties.items()):  # not continued by the end of the next bar
                if start_i < i:
                    del open_ties[pitch]
                    problems.append(Problem(label, start_bar, start_i, f"a tie on {pitch_name(pitch)} leads nowhere"))
            short_ok = i in (0, len(measures) - 1) or m.get("implicit") == "yes"
            # A voice may stop early (rests left out of a second voice), but no voice may run past the bar's end,
            # and some voice must reach it.
            for voice, end in sorted(ends.items()):
                if end > nominal:
                    which = f"voice {voice}: " if len(ends) > 1 else ""
                    problems.append(Problem(label, bar, i, f"{which}{_beats(end)} in a bar of {_beats(nominal)}"))
            full = max(ends.values(), default=nominal)
            if full < nominal and not short_ok:
                problems.append(Problem(label, bar, i, f"{_beats(full)} in a bar of {_beats(nominal)}"))
        for pitch, (start_bar, start_i) in open_ties.items():
            problems.append(Problem(label, start_bar, start_i, f"a tie on {pitch_name(pitch)} leads nowhere"))
    # The same short bar in every part is usually meant (a bar split around a repeat sign or a new section):
    # one line for it, marked so.
    parts = len(counts)
    grouped: dict[tuple[int, str], list[Problem]] = {}
    for p in problems:
        grouped.setdefault((p.index, p.text), []).append(p)
    problems = []
    for (index, text), ps in grouped.items():
        if parts > 1 and len(ps) == parts and " in a bar of " in text:
            problems.append(Problem("all parts", ps[0].bar, index, text + " (fine if it's split around a repeat or a "
                                                                          "new section)"))
        else:
            problems += ps
    if len(set(counts.values())) > 1:
        problems.append(Problem("", "", 0, "parts have different numbers of bars: "
                                + ", ".join(f"{k} {v}" for k, v in counts.items())))
    return sorted(problems, key=lambda p: (p.index, p.part))


def _beats(x: Fraction) -> str:
    """Quarter-note beats, e.g. "2 1/2 beats"."""
    whole, rest = divmod(x, 1)
    text = (f"{whole} " if whole else "") + (f"{rest}" if rest else "") if x else "0"
    return text.strip() + (" beat" if x == 1 else " beats")
