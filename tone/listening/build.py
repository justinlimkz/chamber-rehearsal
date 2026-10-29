"""A blind listening round: the candidates' renders of each clip as hidden versions on one page
(`page.html`), which saves the ratings to the artifact's database for Claude to read.

    uv run python tone/listening/build.py r1 "Round 1: which approach" current current-plus vsco ...
    uv run python tone/listening/build.py r3 "Round 3: dynamics" flat dynamics --clips=violin-1-4,trio-1-4

Reads `build/tone/renders/<candidate>/<clip>.wav`, writes `build/tone/listening/<round>/`: the page
(`index.html`) and the clips as AAC (.mp4, a type the artifact host serves) under names that don't give the candidate away. Publish the page with
the clips as its files. Optional real-player references: `build/tone/ref/listening/<clip>.wav` plus
`<clip>.txt` (one-line caption with source and licence).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERS = ROOT / "build/tone/renders"
REFS = ROOT / "build/tone/ref/listening"
CLIPS = {  # id -> (title, what to listen for)
    "violin-1-4": ("Violin alone, bars 1–4", "The opening line. Does it sound like one bow joining the notes, or separate notes?"),
    "trio-1-4": ("The trio, bars 1–4", "All three together. Do they blend like three players in a room, or like a section?"),
    "cello-6-8": ("Cello, repeated notes", "Repeated notes: each a gentle new bow stroke, without gaps or bumps."),
    "long-note": ("Long held notes: violin, viola, cello", "Does a long note stay alive, or buzz and wobble on a repeating cycle? Is the vibrato natural or seasick?"),
    "faster": ("Quick notes, bars 75–78", "The violin's semiquavers: do they speak cleanly and in time, not smeared or clipped?"),
    "trio-48-51": ("The trio, bars 48–51", "Quiet, with small swells. Does the line breathe, or does every note come out the same?"),
    "trio-66-69": ("The trio, bars 66–69", "A crescendo into forte, then swells. Does it build and fall back like players would?"),
}


def build(round_id: str, title: str, candidates: list[str], only: list[str] | None = None) -> Path:
    out = ROOT / "build/tone/listening" / round_id
    (out / "clips").mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    clips = []
    for clip, (name, listen) in CLIPS.items():
        if only and clip not in only:
            continue
        versions = []
        for cand in candidates:
            wav = RENDERS / cand / f"{clip}.wav"
            if not wav.exists():
                continue
            rel = "clips/" + hashlib.sha256(f"{round_id}/{cand}/{clip}".encode()).hexdigest()[:12] + ".mp4"
            encode(wav, out / rel)
            files[rel] = str(out / rel)
            versions.append({"key": cand, "src": rel})
        if len(versions) < 2:
            continue
        entry = {"id": clip, "title": name, "listen": listen, "versions": versions}
        ref = REFS / f"{clip}.wav"
        if ref.exists():
            rel = f"clips/ref-{clip}.mp4"
            encode(ref, out / rel)
            files[rel] = str(out / rel)
            entry["ref"] = {"src": rel, "caption": (REFS / f"{clip}.txt").read_text().strip()}
        clips.append(entry)
    page = (Path(__file__).parent / "page.html").read_text()
    head, rest = page.split("/*ROUND*/", 1)
    page = head + "/*ROUND*/" + json.dumps({"id": round_id, "title": title, "clips": clips}) + "/*END*/" + rest.split("/*END*/", 1)[1]
    (out / "index.html").write_text(page)
    (out / "files.json").write_text(json.dumps(files, indent=1))
    return out


def encode(wav: Path, m4a: Path) -> None:
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "192000", str(wav), str(m4a)], check=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    only = [a.removeprefix("--clips=").split(",") for a in args if a.startswith("--clips=")]
    rest = [a for a in args if not a.startswith("--clips=")]
    print(build(rest[0], rest[1], rest[2:], only[0] if only else None))
