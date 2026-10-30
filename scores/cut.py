"""Cut one movement out of a MusicXML score of a whole work, as a score of its own.

Bars `first`..`last` (0-based positions, not printed numbers) of every part, with the attributes in force where
the movement starts (divisions, key, time, staves, clefs) written into its first bar, and new titles. Each
`--dim <part id>:<bar number>` corrects a crescendo hairpin in that bar to a diminuendo.

Run: uv run python scores/cut.py <in.mxl> <first> <last> <out.mxl> "<work title>" "<movement title>" [--dim P1:1 ...]
"""
from __future__ import annotations

import copy
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rehearsal import musicxml  # noqa: E402

ORDER = ["footnote", "level", "divisions", "key", "time", "staves", "part-symbol", "instruments", "clef",
         "staff-details", "transpose", "directive", "measure-style"]


def in_force(measures: list[ET.Element]) -> dict[str, ET.Element]:
    """The attributes last set before the movement: one per kind (a clef per staff, a key per staff if numbered)."""
    now: dict[str, ET.Element] = {}
    for m in measures:
        for a in m.findall("attributes"):
            for e in a:
                if e.tag in ("divisions", "key", "time", "staves", "clef", "transpose", "part-symbol"):
                    now[f"{e.tag}:{e.get('number', '')}"] = e
    return now


def cut(src: Path, first: int, last: int, work: str, movement: str) -> ET.ElementTree:
    root = ET.fromstring(musicxml.text(src))
    for part in root.findall("part"):
        measures = part.findall("measure")
        start = measures[first]
        attrs = start.find("attributes")
        if attrs is None:
            attrs = ET.Element("attributes")
            start.insert(0, attrs)
        have = {f"{e.tag}:{e.get('number', '')}" for e in attrs}
        for key, e in in_force(measures[:first]).items():
            if key not in have:
                attrs.append(copy.deepcopy(e))
        attrs[:] = sorted(attrs, key=lambda e: ORDER.index(e.tag) if e.tag in ORDER else len(ORDER))
        for i, m in enumerate(measures):
            if not first <= i <= last:
                part.remove(m)
    for c in root.findall("credit"):
        root.remove(c)
    w = root.find("work")
    if w is None:
        w = ET.Element("work")
        root.insert(0, w)
    for c in list(w):
        w.remove(c)
    ET.SubElement(w, "work-title").text = work
    mt = root.find("movement-title")
    if mt is None:
        mt = ET.Element("movement-title")
        root.insert(list(root).index(w) + 1, mt)
    mt.text = movement
    return ET.ElementTree(root)


def dim(tree: ET.ElementTree, part: str, bar: str) -> None:
    for p in tree.getroot().findall("part"):
        if p.get("id") == part:
            for m in p.findall("measure"):
                if m.get("number") == bar:
                    for w in m.iter("wedge"):
                        if w.get("type") == "crescendo":
                            w.set("type", "diminuendo")


def write_mxl(tree: ET.ElementTree, out: Path) -> None:
    ET.indent(tree, space="  ")
    xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tree.getroot(), encoding="utf-8")
    container = (b'<?xml version="1.0" encoding="UTF-8"?>\n<container><rootfiles>'
                 b'<rootfile full-path="score.musicxml"/></rootfiles></container>\n')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml", container)
        z.writestr("score.musicxml", xml)


if __name__ == "__main__":
    src, first, last, out, work, movement = sys.argv[1:7]
    tree = cut(Path(src), int(first), int(last), work, movement)
    rest = sys.argv[7:]
    for flag, fix in zip(rest[::2], rest[1::2]):
        if flag == "--dim":
            dim(tree, *fix.split(":"))
    write_mxl(tree, Path(out))
    print(f"wrote {out}")
