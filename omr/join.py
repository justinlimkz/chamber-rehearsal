"""Join page transcriptions (score-partwise, four parts in the same order) into one score, bars renumbered, with
printed accidentals and beams added (accidentals.py, beams.py). Writes .musicxml, or .mxl if the name ends so.

Usage: python3 omr/join.py out.musicxml "Work" "Movement" "Composer" page1.musicxml page2.musicxml ...
"""
import copy
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from accidentals import add as add_accidentals  # noqa: E402
from beams import add as add_beams  # noqa: E402
from score import xml_text  # noqa: E402
import zipfile  # noqa: E402

NAMES = ["Violin", "Viola", "Violoncello", "Piano"]


def main():
    out, work, movement, composer, *pages = sys.argv[1:]
    root = ET.Element("score-partwise", version="4.0")
    ET.SubElement(ET.SubElement(root, "work"), "work-title").text = work
    ET.SubElement(root, "movement-title").text = movement
    ET.SubElement(ET.SubElement(root, "identification"), "creator", type="composer").text = composer
    pl = ET.SubElement(root, "part-list")
    parts = []
    for k, name in enumerate(NAMES, 1):
        sp = ET.SubElement(pl, "score-part", id=f"P{k}")
        ET.SubElement(sp, "part-name").text = name
        parts.append(ET.Element("part", id=f"P{k}"))
    number = 0
    for page in pages:
        src = ET.fromstring(xml_text(page))
        src_parts = src.findall("part")
        assert len(src_parts) == 4, f"{page}: {len(src_parts)} parts"
        counts = {len(p.findall("measure")) for p in src_parts}
        assert len(counts) == 1, f"{page}: parts have {counts} bars"
        for i in range(counts.pop()):
            number += 1
            for part, sp in zip(parts, src_parts):
                m = copy.deepcopy(sp.findall("measure")[i])
                m.set("number", str(number))
                part.append(m)
    for part in parts:
        root.append(part)
    add_accidentals(root)
    add_beams(root)
    ET.indent(root, space="  ")
    xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
    if out.endswith(".mxl"):
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("META-INF/container.xml", b'<?xml version="1.0" encoding="UTF-8"?>\n<container><rootfiles>'
                       b'<rootfile full-path="score.musicxml"/></rootfiles></container>\n')
            z.writestr("score.musicxml", xml)
    else:
        Path(out).write_bytes(xml)
    print(f"wrote {out}: {number} bars")


if __name__ == "__main__":
    main()
