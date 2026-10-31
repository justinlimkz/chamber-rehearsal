"""Printed accidentals for a score whose notes carry only <alter> (as the page transcriptions do): without
<accidental> elements an engraver (Verovio) prints no sharps, flats or naturals at all.

An accidental is printed where a note's alteration differs from what the key signature, or an earlier accidental
on the same step and octave in the bar and on the same staff, already gives it. A note tied over from the previous
bar prints none (and doesn't count as an earlier accidental).

Usage: python3 omr/accidentals.py in.musicxml out.musicxml
"""
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction

SHARPS = "FCGDAEB"
NAMES = {-2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp"}
AFTER = ("type", "dot")  # <accidental> comes after these in a <note>


def key_alters(fifths: int) -> dict[str, int]:
    if fifths >= 0:
        return {s: 1 for s in SHARPS[:fifths]}
    return {s: -1 for s in SHARPS[::-1][:-fifths]}


def add(root: ET.Element) -> int:
    added = 0
    for part in root.findall("part"):
        fifths, divisions = 0, Fraction(1)
        for m in part.findall("measure"):
            pos, last = Fraction(0), Fraction(0)
            notes = []  # (onset, order, staff, note)
            for e in m:
                if e.tag == "attributes":
                    if e.findtext("divisions"):
                        divisions = Fraction(e.findtext("divisions").strip())
                    if e.findtext("key/fifths"):
                        fifths = int(e.findtext("key/fifths"))
                elif e.tag == "backup":
                    pos -= Fraction(e.findtext("duration").strip()) / divisions
                elif e.tag == "forward":
                    pos += Fraction(e.findtext("duration").strip()) / divisions
                elif e.tag == "note":
                    chord = e.find("chord") is not None
                    start = last if chord else pos
                    if not chord and e.find("grace") is None:
                        last = pos
                        pos += Fraction(e.findtext("duration", "0").strip()) / divisions
                    notes.append((start, len(notes), e.findtext("staff", "1"), e))
            key = key_alters(fifths)
            state: dict[tuple, int] = {}  # (staff, step, octave) -> alteration in force
            for _, _, staff, n in sorted(notes, key=lambda x: (x[0], x[1])):
                p = n.find("pitch")
                if p is None or n.find("accidental") is not None:
                    continue
                step, octave = p.findtext("step").strip(), p.findtext("octave").strip()
                alter = round(float(p.findtext("alter") or 0))
                if "stop" in [t.get("type") for t in n.findall("tie")]:
                    continue
                k = (staff, step, octave)
                if alter != state.get(k, key.get(step, 0)):
                    acc = ET.Element("accidental")
                    acc.text = NAMES.get(alter, "natural")
                    children = list(n)
                    at = max((i for i, c in enumerate(children) if c.tag in AFTER), default=len(children) - 1)
                    n.insert(at + 1, acc)
                    added += 1
                state[k] = alter
    return added


if __name__ == "__main__":
    tree = ET.parse(sys.argv[1])
    print(f"{add(tree.getroot())} accidentals added")
    tree.write(sys.argv[2], encoding="utf-8", xml_declaration=True)
