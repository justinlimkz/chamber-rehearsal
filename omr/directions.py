"""Dynamics, hairpins and words (read off a page into JSON) written into that page's transcription as MusicXML
directions, each at its beat among the notes, so the app's reader (musicxml.py, and the iPad's) and engravers find
them where they belong.

JSON: a list of {"part": "Violin"|"Viola"|"Violoncello"|"Piano", "bar": n, "beat": quarter beats from the bar's
start, "type": "dynamic"|"wedge"|"words", "value": "p" / "crescendo" / "cresc.", and for wedges (and words with
dashes) "end_bar", "end_beat"}; bars numbered from 1 on the page.

Usage: python3 omr/directions.py page.musicxml dyn.json out.musicxml
"""
import json
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction

PARTS = ["Violin", "Viola", "Violoncello", "Piano"]
DYNAMICS = {"pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff", "fp", "sf", "sfp", "sfpp", "fz", "sfz", "rf",
            "rfz", "sffz", "pf"}


def _direction(kinds: list[ET.Element], piano: bool, below: bool = True) -> ET.Element:
    d = ET.Element("direction", placement="below" if below else "above")
    for k in kinds:
        ET.SubElement(d, "direction-type").append(k)
    if piano:
        ET.SubElement(d, "staff").text = "1"
    return d


def _place(m: ET.Element, beat: Fraction, divisions: Fraction, d: ET.Element) -> None:
    """Before the first note of the bar's first stream that starts at `beat`, or else at `beat` by backing up
    from the end of the bar (and going forward again)."""
    pos = Fraction(0)
    for i, e in enumerate(list(m)):
        if e.tag == "backup":
            break
        if e.tag == "forward":
            pos += Fraction(e.findtext("duration").strip()) / divisions
        elif e.tag == "note" and e.find("chord") is None and e.find("grace") is None:
            if pos == beat:
                m.insert(i, d)
                return
            if pos > beat:
                break
            pos += Fraction(e.findtext("duration", "0").strip()) / divisions
    end = Fraction(0)
    p = Fraction(0)
    for e in m:
        if e.tag == "backup":
            p -= Fraction(e.findtext("duration").strip()) / divisions
        elif e.tag == "forward":
            p += Fraction(e.findtext("duration").strip()) / divisions
        elif e.tag == "note" and e.find("chord") is None and e.find("grace") is None:
            p += Fraction(e.findtext("duration", "0").strip()) / divisions
        end = max(end, p)
    if p != end:  # finish at the bar's end first
        f = ET.SubElement(m, "forward")
        ET.SubElement(f, "duration").text = str(int((end - p) * divisions))
    back = end - beat
    if back > 0:
        b = ET.SubElement(m, "backup")
        ET.SubElement(b, "duration").text = str(int(back * divisions))
    m.append(d)
    if back > 0:
        f = ET.SubElement(m, "forward")
        ET.SubElement(f, "duration").text = str(int(back * divisions))


def add(root: ET.Element, items: list[dict]) -> int:
    parts = root.findall("part")
    divs = []
    for part in parts:  # divisions in force at each bar
        d, per = Fraction(1), []
        for m in part.findall("measure"):
            for e in m.iter("divisions"):
                d = Fraction(e.text.strip())
            per.append(d)
        divs.append(per)
    added = 0
    for it in items:
        k = PARTS.index(it["part"])
        measures = parts[k].findall("measure")
        n = len(measures)
        piano = it["part"] == "Piano"

        def put(bar, beat, kinds):
            bar = min(max(int(bar), 1), n)
            _place(measures[bar - 1], Fraction(str(beat)), divs[k][bar - 1], _direction(kinds, piano))

        kind, value = it["type"], str(it["value"]).strip()
        if kind == "dynamic":
            if value.lower() not in DYNAMICS:
                continue
            dyn = ET.Element("dynamics")
            ET.SubElement(dyn, value.lower())
            put(it["bar"], it["beat"], [dyn])
        elif kind == "wedge":
            if value not in ("crescendo", "diminuendo") or "end_bar" not in it:
                continue
            put(it["bar"], it["beat"], [ET.Element("wedge", type=value, number="1")])
            put(it["end_bar"], it.get("end_beat", 0), [ET.Element("wedge", type="stop", number="1")])
        elif kind == "words":
            w = ET.Element("words")
            w.text = value
            if "end_bar" in it:
                put(it["bar"], it["beat"], [w, ET.Element("dashes", type="start", number="1")])
                put(it["end_bar"], it.get("end_beat", 0), [ET.Element("dashes", type="stop", number="1")])
            else:
                put(it["bar"], it["beat"], [w])
        else:
            continue
        added += 1
    return added


if __name__ == "__main__":
    tree = ET.parse(sys.argv[1])
    print(f"{add(tree.getroot(), json.loads(open(sys.argv[2]).read()))} markings added")
    tree.write(sys.argv[3], encoding="utf-8", xml_declaration=True)
