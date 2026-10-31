"""Score a transcription against the reference, bar by bar.

Each part's bars as written (no repeats unfolded), each bar a multiset of notes (onset in the bar, MIDI pitch,
duration), both in quarter-note beats; rests, grace notes and cue notes ignored; ties not joined (a tied note is
a note in each bar). Piano staves merged. Parts matched by order.

Usage: python omr/score.py truth.mxl pred.musicxml TRUTH_FIRST_BAR N_BARS [--show]
  bar TRUTH_FIRST_BAR (1-based position in the truth) is the pred's first bar.
"""
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from fractions import Fraction

STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def xml_text(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("META-INF/container.xml"))
            return z.read(root.find(".//rootfile").get("full-path"))
    return open(path, "rb").read()


def bars(path):
    root = ET.fromstring(xml_text(path))
    out = []
    for part in root.findall("part"):
        divisions = Fraction(1)
        pbars = []
        for m in part.findall("measure"):
            pos, last = Fraction(0), Fraction(0)
            notes = Counter()
            for e in m:
                if e.tag == "attributes" and e.findtext("divisions"):
                    divisions = Fraction(e.findtext("divisions").strip())
                elif e.tag == "backup":
                    pos -= Fraction(e.findtext("duration").strip()) / divisions
                elif e.tag == "forward":
                    pos += Fraction(e.findtext("duration").strip()) / divisions
                elif e.tag == "note":
                    if e.find("grace") is not None:
                        continue
                    d = Fraction(e.findtext("duration", "0").strip()) / divisions
                    start = last if e.find("chord") is not None else pos
                    if e.find("chord") is None:
                        last = pos
                        pos += d
                    p = e.find("pitch")
                    if p is None or e.find("cue") is not None:
                        continue
                    midi = (int(p.findtext("octave")) + 1) * 12 + STEPS[p.findtext("step").strip()] + round(
                        float(p.findtext("alter") or 0))
                    notes[(start, midi, d)] += 1
            pbars.append(notes)
        out.append(pbars)
    return out


def fmt(notes):
    return " ".join(f"{float(o):g}:{NAMES[p % 12]}{p // 12 - 1}/{float(d):g}" for (o, p, d) in sorted(notes.elements()))


def main():
    truth_path, pred_path, first, n = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    show = "--show" in sys.argv
    truth, pred = bars(truth_path), bars(pred_path)
    names = ["Violin", "Viola", "Cello", "Piano"]
    if len(pred) != len(truth):
        print(f"! pred has {len(pred)} parts, truth {len(truth)}")
    cells = right = 0
    tp = fp = fn = 0
    loose_tp = loose_fp = loose_fn = 0
    whole_bars = [True] * n
    per_part = {}
    for k, name in enumerate(names):
        ok = 0
        for i in range(n):
            t = truth[k][first - 1 + i]
            p = pred[k][i] if k < len(pred) and i < len(pred[k]) else Counter()
            cells += 1
            same = t == p
            ok += same
            whole_bars[i] &= same
            tp += sum((t & p).values()); fp += sum((p - t).values()); fn += sum((t - p).values())
            lt = Counter((o, m) for (o, m, d) in t.elements()); lp = Counter((o, m) for (o, m, d) in p.elements())
            loose_tp += sum((lt & lp).values()); loose_fp += sum((lp - lt).values()); loose_fn += sum((lt - lp).values())
            if show and not same:
                print(f"bar {first + i:>3} {name:<7} missing: {fmt(t - p)}")
                print(f"{'':>8}{'':<8} extra:   {fmt(p - t)}")
        per_part[name] = ok
        right += ok
    extra_bars = [len(pred[k]) - n for k in range(min(len(pred), 4)) if len(pred[k]) != n]
    f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
    lf1 = 2 * loose_tp / (2 * loose_tp + loose_fp + loose_fn) if loose_tp else 0.0
    print(f"part-bars exactly right: {right}/{cells} ({right / cells:.0%})  "
          + "  ".join(f"{k} {v}/{n}" for k, v in per_part.items()))
    print(f"bars right in every part: {sum(whole_bars)}/{n}")
    print(f"notes: F1 {f1:.1%} (pitch+onset+duration; {tp} right, {fp} extra, {fn} missing)  "
          f"pitch+onset only F1 {lf1:.1%}")
    if extra_bars:
        print(f"! bar count differs from {n} in some parts: {extra_bars}")


if __name__ == "__main__":
    main()
