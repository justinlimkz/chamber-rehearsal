"""A MusicXML score read the same way the iPad reads it (ios/RehearsalCore/Sources/RehearsalCore/MusicXML.swift):
each part's notes in quarter-note beats with ties joined and repeats played out, its bars in playing order, and the
loudness markings dynamics.py reads (dynamics, wedges, "cresc."/"dim." in words, spanned by dashes or not).

It replaced partitura, which dropped some ties (2026-10-30: a tie into the bar a repeat starts at, and two more
in the Schumann's repeated section), so the engine was waiting for notes nobody plays. The two readers (this and
the iPad's) are kept the same by `rehearsal bundle --import-fixtures` and `import-check`.

As partitura did: a direction sits where it is written among the notes (its <offset> is ignored); a grace note starts
with the note it ornaments and lasts nothing; a short first bar is a pickup, so the first full bar starts at beat 0;
every repeat is taken, with first and second endings (D.C. and D.S. are not followed).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
UNSPANNED = None  # a hairpin's end when "cresc." has no span


@dataclass
class Note:
    onset: float
    duration: float
    pitch: int


@dataclass
class Part:
    id: str
    names: list[str]  # part-name, part-abbreviation, instrument names
    notes: list[Note] = field(default_factory=list)  # sorted by (onset, pitch); grace notes last 0
    bars: list[tuple[float, int]] = field(default_factory=list)  # (beat, printed number), in playing order
    marks: list[tuple[float, str]] = field(default_factory=list)  # dynamics, lower case
    hairpins: list[tuple[float, float | None, int]] = field(default_factory=list)  # (start, end or None, +1/-1)

    @property
    def name(self) -> str:
        return self.names[0] if self.names else self.id


@dataclass
class Score:
    title: str
    work: str | None
    movement: str | None
    composer: str
    parts: list[Part]
    tempo: float | None = None  # quarter notes a minute at the start, from the first part's first <sound tempo>


def text(path: Path) -> bytes:
    """The MusicXML of a .musicxml/.xml file, or of the score inside a compressed .mxl."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            try:
                root = ET.fromstring(z.read("META-INF/container.xml"))
                full = root.find("rootfiles/rootfile").get("full-path")
                return z.read(full)
            except (KeyError, AttributeError, ET.ParseError):
                inner = next(n for n in z.namelist() if n.endswith((".xml", ".musicxml")) and not n.startswith("META-INF"))
                return z.read(inner)
    return Path(path).read_bytes()


def _value(node, name: str) -> str | None:
    c = node.find(name)
    return c.text.strip() if c is not None and c.text is not None else (None if c is None else "")


def load(path: Path, unfold: bool = True) -> Score:
    """The score; `unfold=False`: the bars as written, each once."""
    root = ET.fromstring(text(path))
    if root.tag != "score-partwise":
        raise ValueError("not a part-wise MusicXML score")
    work = (_value(root, "work/work-title") or None)
    movement = (_value(root, "movement-title") or None)
    credit = next((_value(c, "credit-words") for c in root.findall("credit") if _value(c, "credit-type") == "title"), None)
    composer = next(((c.text or "").strip() for c in root.findall("identification/creator") if c.get("type") == "composer"), "")
    info = {}
    for sp in root.findall("part-list/score-part"):
        names = [_value(sp, "part-name"), _value(sp, "part-abbreviation")]
        names += [_value(si, "instrument-name") for si in sp.findall("score-instrument")]
        info[sp.get("id")] = [n for n in names if n]
    nodes = root.findall("part")
    order = playing_order(nodes) if unfold else list(range(max((len(p.findall("measure")) for p in nodes), default=0)))
    parts = []
    for node in nodes:
        part = Part(node.get("id", ""), info.get(node.get("id"), []))
        _read(node, order, part)
        parts.append(part)
    return Score(movement or work or credit or "", work, movement, composer, parts, _tempo(nodes))


def _tempo(parts) -> float | None:
    for s in (parts[0].iter("sound") if parts else []):
        try:
            if float(s.get("tempo", "")) > 0:
                return float(s.get("tempo"))
        except ValueError:
            pass
    return None


def playing_order(parts) -> list[int]:
    """The bars in the order they're played: every repeat taken (twice, or as many times as it says), each ending on
    the pass its number names. Read from the barlines of every part (a repeat in any part counts)."""
    n = max((len(p.findall("measure")) for p in parts), default=0)
    forward, backward = [False] * n, [0] * n
    ending_start: list[list[int] | None] = [None] * n
    ending_stop = [False] * n
    for part in parts:
        for i, m in enumerate(part.findall("measure")):
            for b in m.findall("barline"):
                r = b.find("repeat")
                if r is not None:
                    if r.get("direction") == "forward":
                        forward[i] = True
                    if r.get("direction") == "backward":
                        times = r.get("times", "")
                        backward[i] = max(backward[i], int(times) if times.isdigit() else 2)
                e = b.find("ending")
                if e is not None:
                    nums = [int(x) for x in re.split(r"[,; ]", e.get("number", "")) if x.isdigit()]
                    if e.get("type") == "start":
                        ending_start[i] = nums
                    if e.get("type") in ("stop", "discontinue"):
                        ending_stop[i] = True
    ending: list[list[int] | None] = [None] * n
    current = None
    for i in range(n):
        if ending_start[i] is not None:
            current = ending_start[i]
        ending[i] = current
        if ending_stop[i]:
            current = None
    order: list[int] = []
    i, start, pass_, jumped = 0, 0, 1, False
    while i < n and len(order) < 100_000:
        if forward[i] and not (jumped and i == start):
            start, pass_ = i, 1
        jumped = False
        if ending[i] is not None and pass_ not in ending[i]:
            i += 1
            continue
        order.append(i)
        if backward[i] > 0:
            if pass_ < backward[i]:
                pass_ += 1
                i, jumped = start, True
                continue
            start, pass_ = i + 1, 1
        i += 1
    return order


def hairpin_word(s: str) -> int | None:
    """+1 for "cresc." (and "crescendo", "poco a poco cresc." ...), -1 for "dim.", "decresc." and the like."""
    words = [w for w in re.split(r"[^a-zà-ÿ]+", s.lower()) if w]
    if any(w.startswith("cresc") for w in words):
        return 1
    if any(w.startswith("dim") or w.startswith("decresc") for w in words):
        return -1
    return None


def _read(node, order: list[int], part: Part) -> None:
    divisions, time_length = 1.0, 4.0
    bars = []
    for index, m in enumerate(node.findall("measure")):
        label = (m.get("number") or "").strip()
        bar = {"number": int(label) if label.lstrip("-").isdigit() else index + 1,
               "notes": [], "marks": [], "wedges": [], "words": [], "dashes": []}
        pos = last = end = 0.0
        for e in m:
            if e.tag == "attributes":
                d = _value(e, "divisions")
                if d and float(d) > 0:
                    divisions = float(d)
                t = e.find("time")
                if t is not None and _value(t, "beats") and _value(t, "beat-type"):
                    beats = sum(float(x) for x in _value(t, "beats").split("+") if x.replace(".", "", 1).isdigit())
                    bt = float(_value(t, "beat-type"))
                    if beats > 0 and bt > 0:
                        time_length = beats * 4 / bt
            elif e.tag == "backup":
                pos -= float(_value(e, "duration") or 0) / divisions
            elif e.tag == "forward":
                pos += float(_value(e, "duration") or 0) / divisions
            elif e.tag == "note":
                grace = e.find("grace") is not None
                dur = 0.0 if grace else float(_value(e, "duration") or 0) / divisions
                chord = e.find("chord") is not None
                on = last if chord else pos
                if not chord:
                    last, pos = pos, pos + dur
                p = e.find("pitch")
                if p is not None and e.find("cue") is None and _value(p, "step") in STEPS and (_value(p, "octave") or "").isdigit():
                    alter = round(float(_value(p, "alter") or 0))
                    ties = [t.get("type") for t in e.findall("tie")]
                    pitch = (int(_value(p, "octave")) + 1) * 12 + STEPS[_value(p, "step")] + alter
                    bar["notes"].append((on, dur, pitch, "start" in ties, "stop" in ties))
            elif e.tag == "direction":
                for dt in e.findall("direction-type"):
                    d = dt.find("dynamics")
                    if d is not None:
                        for k in d:
                            bar["marks"].append((pos, ((k.text or "") if k.tag == "other-dynamics" else k.tag).strip().lower()))
                    w = dt.find("wedge")
                    if w is not None:
                        bar["wedges"].append((pos, w.get("type", ""), w.get("number", "1")))
                    for w in dt.findall("words"):
                        sign = hairpin_word(w.text or "")
                        if sign is not None:
                            bar["words"].append((pos, sign))
                    for da in dt.findall("dashes"):
                        bar["dashes"].append((pos, da.get("type", ""), da.get("number", "1")))
            end = max(end, pos)
        bar["length"], bar["nominal"] = end, time_length
        bars.append(bar)
    if not bars:
        return
    t = -bars[0]["length"] if bars[0]["length"] < bars[0]["nominal"] - 1e-9 else 0.0  # a pickup
    open_: dict[int, int] = {}  # pitch -> index of a note whose tie is still open
    wedges: dict[str, tuple[float, int]] = {}
    words: dict[str, tuple[float, int]] = {}  # "cresc." waiting for the end of its dashes
    for i in order:
        if i >= len(bars):
            continue
        bar = bars[i]
        part.bars.append((t, bar["number"]))
        for on, dur, pitch, tie_start, tie_stop in sorted(bar["notes"], key=lambda n: n[0]):
            on += t
            k = open_.get(pitch)
            if tie_stop and k is not None and abs(part.notes[k].onset + part.notes[k].duration - on) < 1e-6:
                part.notes[k].duration += dur
                if not tie_start:
                    del open_[pitch]
                continue
            part.notes.append(Note(on, dur, pitch))
            if tie_start:
                open_[pitch] = len(part.notes) - 1
            else:
                open_.pop(pitch, None)
        part.marks += [(t + p, text) for p, text in bar["marks"]]
        # "cresc." with dashes ends where they do; without, it has no span
        dashed = [(p, num) for p, typ, num in bar["dashes"] if typ == "start"]
        for p, sign in bar["words"]:
            num = next((num for q, num in dashed if q == p), None)
            if num is None:
                part.hairpins.append((t + p, UNSPANNED, sign))
            else:
                words[num] = (t + p, sign)
        for p, typ, num in bar["dashes"]:
            if typ == "stop" and num in words:
                s, sign = words.pop(num)
                part.hairpins.append((s, t + p, sign))
        for p, typ, num in bar["wedges"]:
            if typ in ("crescendo", "diminuendo"):
                wedges[num] = (t + p, 1 if typ == "crescendo" else -1)
            elif typ == "stop" and num in wedges:
                s, sign = wedges.pop(num)
                part.hairpins.append((s, t + p, sign))
        t += bar["length"]
    part.hairpins += [(s, UNSPANNED, sign) for s, sign in list(wedges.values()) + list(words.values())]
    part.notes.sort(key=lambda n: (n.onset, n.pitch))
    part.hairpins.sort(key=lambda h: (h[0], h[2]))
