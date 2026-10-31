"""Where two transcriptions of the same page disagree, bar by bar (the bars to look at again).

Usage: python3 omr/agree.py a.musicxml b.musicxml [--quiet]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from score import bars, fmt  # noqa: E402

NAMES = ["Violin", "Viola", "Cello", "Piano"]


def disagreements(a_path, b_path):
    a, b = bars(a_path), bars(b_path)
    out = []
    for k, name in enumerate(NAMES):
        n = max(len(a[k]), len(b[k]))
        for i in range(n):
            x = a[k][i] if i < len(a[k]) else None
            y = b[k][i] if i < len(b[k]) else None
            if x != y:
                out.append((i + 1, name, x, y))
    return a, b, sorted(out)


def main():
    a, b, out = disagreements(sys.argv[1], sys.argv[2])
    counts = [len(p) for p in a], [len(p) for p in b]
    cells = sum(max(x, y) for x, y in zip(*counts))
    print(f"bars per part: {counts[0]} vs {counts[1]}; agree on {cells - len(out)}/{cells} part-bars")
    if "--quiet" not in sys.argv:
        for bar, name, x, y in out:
            print(f"bar {bar:>3} {name:<7} A: {fmt(x) if x is not None else '(none)'}")
            print(f"{'':>15} B: {fmt(y) if y is not None else '(none)'}")


if __name__ == "__main__":
    main()
