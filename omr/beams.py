"""Beams for a score whose notes have none (as the page transcriptions do): without <beam> elements an engraver
(Verovio) draws every eighth and sixteenth with its own flag.

Per voice in each bar, eighths and shorter are beamed together within a half bar if they're all eighths, else
within each quarter beat (the usual grouping in 2/4, 4/4 and 3/4 at this level); a rest breaks a beam. Secondary
beams join the notes that are short enough, with hooks for one on its own.

Usage: python3 omr/beams.py in.musicxml out.musicxml
"""
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction

LEVELS = {"eighth": 1, "16th": 2, "32nd": 3, "64th": 4}
BEFORE = ("notations", "lyric", "play", "listen")  # <beam> comes before these in a <note>


def _insert(note: ET.Element, beam: ET.Element) -> None:
    children = list(note)
    at = next((i for i, c in enumerate(children) if c.tag in BEFORE), len(children))
    note.insert(at, beam)


def add(root: ET.Element) -> int:
    groups = 0
    for part in root.findall("part"):
        divisions, beats = Fraction(1), Fraction(4)
        for m in part.findall("measure"):
            if m.find(".//beam") is not None:
                continue
            pos = Fraction(0)
            voices: dict[str, list] = {}  # voice -> [(onset, duration, level or 0 for a rest/long note, note)]
            for e in m:
                if e.tag == "attributes":
                    if e.findtext("divisions"):
                        divisions = Fraction(e.findtext("divisions").strip())
                    t = e.find("time")
                    if t is not None and t.findtext("beats"):
                        beats = Fraction(t.findtext("beats")) * 4 / Fraction(t.findtext("beat-type"))
                elif e.tag == "backup":
                    pos -= Fraction(e.findtext("duration").strip()) / divisions
                elif e.tag == "forward":
                    pos += Fraction(e.findtext("duration").strip()) / divisions
                elif e.tag == "note" and e.find("chord") is None and e.find("grace") is None:
                    d = Fraction(e.findtext("duration", "0").strip()) / divisions
                    level = 0 if e.find("rest") is not None else LEVELS.get(e.findtext("type", ""), 0)
                    voices.setdefault(e.findtext("voice", "1"), []).append((pos, d, level, e))
                    pos += d
            half = beats / 2 if beats % 2 == 0 else beats
            for notes in voices.values():
                spans = {}
                for onset, d, level, n in notes:
                    spans.setdefault(onset // half, []).append((onset, level))
                all_eighths = {k: all(lv in (0, 1) for _, lv in v) for k, v in spans.items()}
                group, key = [], None
                runs = []
                for onset, d, level, n in notes:
                    k = onset // half if all_eighths[onset // half] else (onset // half, onset // 1)
                    if level == 0 or k != key:
                        if len(group) > 1:
                            runs.append(group)
                        group, key = ([], None) if level == 0 else ([(level, n)], k)
                        continue
                    group.append((level, n))
                if len(group) > 1:
                    runs.append(group)
                for run in runs:
                    groups += 1
                    for lv in range(1, max(level for level, _ in run) + 1):
                        has = [level >= lv for level, _ in run]
                        for i, (level, n) in enumerate(run):
                            if not has[i]:
                                continue
                            prev, nxt = i > 0 and has[i - 1], i + 1 < len(run) and has[i + 1]
                            if lv == 1:
                                kind = "begin" if i == 0 else "end" if i == len(run) - 1 else "continue"
                            elif prev and nxt:
                                kind = "continue"
                            elif nxt:
                                kind = "begin"
                            elif prev:
                                kind = "end"
                            else:
                                kind = "forward hook" if i == 0 else "backward hook"
                            b = ET.Element("beam", number=str(lv))
                            b.text = kind
                            _insert(n, b)
    return groups


if __name__ == "__main__":
    tree = ET.parse(sys.argv[1])
    print(f"{add(tree.getroot())} beamed groups")
    tree.write(sys.argv[2], encoding="utf-8", xml_declaration=True)
