"""Download the sample sets of the tone tests, each into its own build/tone/dl-<name>/ (untrusted data: only read
as audio by tone/fonts.py). Skips files already there. Run: python -I tone/fetch.py [vsco2 iowa bigcat]

Licences (read at the source, 2026-10-29):
  vsco2  github.com/sgossner/VSCO-2-CE LICENSE: CC0 1.0 Universal
  iowa   theremin.music.uiowa.edu/MIS.html: "may be downloaded and used for any projects, without restrictions"
  bigcat github.com/sfzinstruments/karoryfer-bigcat.cello LICENSE: CC0 1.0 Universal
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DL = Path(__file__).resolve().parents[1] / "build/tone"
NOTE = {"C": 0, "Db": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "Gb": 6, "G": 7, "Ab": 8, "A": 9, "Bb": 10, "B": 11}


def get(url: str, to: Path) -> None:
    if to.exists() and to.stat().st_size:
        return
    to.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as r:
        to.write_bytes(r.read())


def github(repo: str, branch: str, keep, d: Path) -> None:
    tree = d / "tree.json"
    get(f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1", tree)
    for e in json.loads(tree.read_text())["tree"]:
        p = e["path"]
        if e["type"] == "blob" and (keep(p) or p.lower().split("/")[-1] in ("license", "readme.md", "readme.txt")):
            get(f"https://raw.githubusercontent.com/{repo}/{branch}/{urllib.parse.quote(p)}",
                d / "files" / p.replace("/", "__"))


def vsco2() -> None:
    dirs = ("Strings/Solo Violin/Arco Vib/", "Strings/Viola Section/susvib/", "Strings/Cello Section/susvib/")
    github("sgossner/VSCO-2-CE", "master", lambda p: p.startswith(dirs), DL / "dl-vsco2")


def bigcat() -> None:  # sustains at mf, one of the two takes
    github("sfzinstruments/karoryfer-bigcat.cello", "HEAD", lambda p: re.fullmatch(r"Samples/sus/\w+_mf_d\.wav", p),
           DL / "dl-bigcat")


IOWA_STRINGS = {"Violin": {"G": "G3", "D": "D4", "A": "A4", "E": "E5"}, "Viola": {"C": "C3", "G": "G3", "D": "D4", "A": "A4"},
                "Cello": {"C": "C2", "G": "G2", "D": "D3", "A": "A3"}}  # string: its open note
IOWA_RANGE = {"Violin": (55, 96), "Viola": (48, 84), "Cello": (36, 72)}


def key(n: str) -> int:
    m = re.fullmatch(r"([A-G]b?)(\d)", n)
    return 12 * (int(m[2]) + 1) + NOTE[m[1]]


def iowa() -> None:
    """Per-note ff arco (the 2012 set): a note every 3 semitones, on the string a player would normally use for
    it (the highest whose open note is at or below it)."""
    d = DL / "dl-iowa"
    links = []
    for inst in IOWA_STRINGS:
        page = d / f"{inst}.html"
        get(f"https://theremin.music.uiowa.edu/MIS-Pitches-2012/MIS{inst}2012.html", page)
        links += re.findall(r'href="([^"]*\.arco\.[^"]*\.aif)"', page.read_text(errors="replace"))
    have = {}
    for l in links:
        inst, _, dyn, s, note = l.rsplit("/", 1)[1].split(".")[:5]
        have[inst, s[3:], key(note)] = l
    for inst, opens in IOWA_STRINGS.items():
        lo, hi = IOWA_RANGE[inst]
        for k in range(lo, hi + 1, 3):
            for s in sorted(opens, key=lambda s: key(opens[s]), reverse=True):
                if key(opens[s]) <= k and (inst, s, k) in have:
                    url = urllib.parse.urljoin("https://theremin.music.uiowa.edu/MIS-Pitches-2012/", have[inst, s, k])
                    get(url.replace(" ", "%20"), d / "files" / url.rsplit("/", 1)[1])
                    break
            else:
                print(f"iowa: no {inst} key {k}")
    get("https://theremin.music.uiowa.edu/MIS.html", d / "MIS.html")


if __name__ == "__main__":
    for name in sys.argv[1:] or ["vsco2", "iowa", "bigcat"]:
        print(name)
        globals()[name]()
