"""The score engraved for reading at the piano (the iPad view): Verovio draws the MusicXML as one tall
SVG, a system under another, which the page scrolls through as you play.

Each bar's group carries its number (`data-n`), so the page can find, highlight and tap bars by the
same numbers the follower and the cues use. Sized for an iPad Pro 12.9" in portrait: two systems of
the quartet fill the screen.
"""
from __future__ import annotations

import re
from pathlib import Path

import verovio

RESOURCES = Path(verovio.__file__).resolve().parent / "data"
OPTIONS = {
    "pageWidth": 2100, "pageHeight": 60000, "adjustPageHeight": True, "scale": 38, "breaks": "auto",
    "pageMarginTop": 40, "pageMarginBottom": 40, "pageMarginLeft": 60, "pageMarginRight": 60,
    "spacingSystem": 10, "spacingStaff": 6, "header": "auto", "footer": "none", "svgViewBox": True,
    "svgAdditionalAttribute": ["measure@n"],
}


def engrave(path: Path) -> tuple[str, dict[int, list[tuple[float, list[str]]]]]:
    """The whole score as one SVG (about half a second for the Schumann), and for each bar where its
    notes start: (fraction of the bar, ids of the notes and rests starting there), so the playhead can
    pass over the notes as they sound rather than move evenly across the bar (notes aren't spaced
    evenly in time: clefs, grace notes and accidentals take room)."""
    tk = verovio.toolkit(False)  # fonts come from RESOURCES, set next
    tk.setResourcePath(str(RESOURCES))  # the default is only found on the main thread
    tk.setOptions(OPTIONS)
    verovio.enableLog(verovio.LOG_ERROR)  # not the MusicXML import's warnings, during a session
    if not tk.loadFile(str(path)):
        raise ValueError(f"Verovio could not read {path}")
    if tk.getPageCount() != 1:
        raise ValueError(f"{path} engraved to {tk.getPageCount()} pages; expected one tall page")
    svg = tk.renderToSVG(1)
    number = dict(re.findall(r'<g id="([^"]+)" class="measure"[^>]*data-n="(\d+)"', svg))
    timemap = tk.renderToTimemap({"includeMeasures": True, "includeRests": True})
    starts = [(e["qstamp"], e["measureOn"]) for e in timemap if "measureOn" in e]  # in playing order
    anchors: dict[int, list[tuple[float, list[str]]]] = {}
    first: dict[int, int] = {}  # bar -> its first time through (repeats are played twice, drawn once)
    k = -1
    for e in timemap:
        while k + 1 < len(starts) and starts[k + 1][0] <= e["qstamp"]:
            k += 1
        if k < 0 or starts[k][1] not in number:
            continue
        n = int(number[starts[k][1]])
        ids = e.get("on", []) + e.get("restsOn", [])
        if first.setdefault(n, k) != k or not ids:
            continue
        start = starts[k][0]
        end = starts[k + 1][0] if k + 1 < len(starts) else start + 3
        anchors.setdefault(n, []).append(((e["qstamp"] - start) / (end - start), ids))
    return svg, anchors


def bar_numbers(svg: str) -> list[int]:
    """The bar numbers in the engraving, in order."""
    return [int(n) for n in re.findall(r'<g [^>]*class="measure"[^>]*data-n="(\d+)"', svg)]
