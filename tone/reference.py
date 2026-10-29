"""Download and prepare real string recordings that the scorecard measures renders against.

Run:  uv run --no-project --with numpy --with scipy --with soundfile --with librosa \
          python -I tone/reference.py [--out build/tone/ref]

Sources (all downloads are untrusted data: each goes in its own fresh dir under
<out>/raw/<source>/, and only this script, run with -I, reads them):
  iowa     University of Iowa Musical Instrument Samples, arco mf chromatic scales of violin,
           viola, cello. Anechoic, mono 16/44.1. Site says they "may be downloaded and used for
           any projects, without restrictions" (theremin.music.uiowa.edu/MIS.html).
  commons  Wikimedia Commons recordings (solo phrases, quartet, the Schumann Op. 47 III
           excerpts). Only files whose Commons licence is CC0 / public domain / CC BY / CC BY-SA
           are kept; the licence string and author are recorded per file.
Outputs: <out>/notes/<inst>/*.flac (one sustained note each), <out>/phrases/*.flac (48 kHz mono
excerpts) and <out>/index.json (every item with source URL, licence, sha256).
"""
import hashlib, json, re, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np, soundfile as sf, librosa

UA = {"User-Agent": "chamber-rehearsal-research/1.0 (internal analysis)"}
SR = 48000
IOWA = "https://theremin.music.uiowa.edu/"
IOWA_PAGES = {"violin": "MISviolin", "viola": "MISviola", "cello": "MIScello"}
FMIN = {"violin": 180, "viola": 120, "cello": 60}  # pyin search floors (Hz)
OK_LICENCE = re.compile(r"^(CC0|Public domain|CC BY(-SA)? [0-9.]+)$", re.I)
# (commons title, role, instrument/ensemble, max seconds kept). role: phrase = legato music.
COMMONS = [
    ("J. S. Bach – Violin Sonata No.1 in G minor, BWV 1001, I. Adagio.ogg", "violin", 90),
    ("Johann Sebastian Bach - Chaconne for violin alone.ogg", "violin", 90),
    ("Bach - Cello Suite no. 1 in G major, BWV 1007 - I. Prélude.ogg", "cello", 90),
    ("Bach - Cello Suite no. 1 in G major, BWV 1007 - IV. Sarabande.ogg", "cello", 90),
    ("JOHN MICHEL CELLO-SAINT SAENS CARNIVAL OF ANIMALS THE SWAN.ogg", "cello", 90),
    ("Schubert - Arpeggione Sonata - 3. Allegretto (viola).ogg", "viola", 90),
    ("Beethoven - String Quartet No. 6 in B flat major, Op. 18 No. 6 - II. Adagio, ma non troppo (Musopen String Quartet).flac", "quartet", 120),
    ("Yorkside Quartet Death and the Maiden Mvt 2.ogg", "quartet", 120),
    ("Tsai, Chang, & Han - Mendelssohn's Piano Trio No. 1 in D minor, Op. 49 - II. Andante con molto tranquillo.ogg", "piano-trio", 120),
] + [(f"Schumann - Piano Quartet - III. {s}.opus", "schumann", 60)
     for s in ("Theme", "Var1", "Var2", "Var3", "Var4", "Var5", "Chorale", "Coda")]


def get(url, dest):
    """Download url to dest unless already there; return sha256."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        for k in range(6):  # Wikimedia answers 429 when hit quickly: back off and stay polite
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
                    dest.write_bytes(r.read())
                if "wikimedia" in url: time.sleep(5)
                break
            except Exception as e:
                if k == 5: raise
                time.sleep(20 * (k + 1))
    return hashlib.sha256(dest.read_bytes()).hexdigest()


def commons_info(title):
    q = urllib.parse.urlencode(dict(action="query", titles="File:" + title, prop="imageinfo",
                                    iiprop="url|extmetadata", format="json"))
    for k in range(4):
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request("https://commons.wikimedia.org/w/api.php?" + q, headers=UA), timeout=60))
            p = next(iter(d["query"]["pages"].values()))
            i = p["imageinfo"][0]; m = i["extmetadata"]
            strip = lambda s: re.sub(r"<[^>]+>", "", s or "").strip()
            return dict(url=i["url"].split("?")[0], licence=strip(m.get("LicenseShortName", {}).get("value")),
                        author=strip(m.get("Artist", {}).get("value"))[:80],
                        page="https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(title.replace(" ", "_")))
        except Exception:
            time.sleep(10 * (k + 1))
    return None


def to_mono48(path):
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    x = x.mean(1)
    return librosa.resample(x, orig_sr=sr, target_sr=SR) if sr != SR else x


def save(x, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), x / max(1e-9, np.abs(x).max()) * 0.9, SR, subtype="PCM_24")


def split_notes(x, thr_db=-45, min_gap=0.12, min_dur=0.6):
    """Cut a chromatic-scale recording into notes at silent gaps (RMS 10 ms hops)."""
    h = SR // 100
    r = 20 * np.log10(np.sqrt(np.mean(x[:len(x) // h * h].reshape(-1, h) ** 2, 1)) + 1e-9)
    on = r > r.max() + thr_db
    segs, i, n = [], 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and (on[j] or on[j:j + int(min_gap * 100)].any()): j += 1
            if (j - i) / 100 >= min_dur: segs.append((i * h, j * h))
            i = j
        else: i += 1
    return segs


def main():
    out = Path(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "build/tone/ref")
    if not sys.flags.isolated: print("warning: run with python -I (downloaded files are untrusted)")
    index = []
    # --- Iowa single notes -------------------------------------------------------------------
    iowa_done = out / "index.iowa.json"  # the pitch tracking is slow: reuse it on re-runs
    for inst, page in ({} if iowa_done.exists() else IOWA_PAGES).items():
        raw = out / "raw" / f"iowa-{inst}"
        html = urllib.request.urlopen(urllib.request.Request(f"{IOWA}{page}.html", headers=UA), timeout=60).read().decode("latin-1")
        files = sorted({f for f in re.findall(r'sound files/MIS/Strings/[a-z]+/[^"]+\.aiff', html) if ".arco." in f and ".mf." in f})
        urls = {f: IOWA + urllib.parse.quote(f) for f in files}
        with ThreadPoolExecutor(4) as ex:
            hashes = dict(zip(files, ex.map(lambda f: get(urls[f], raw / Path(f).name), files)))
        for f in files:
            x = to_mono48(raw / Path(f).name)
            for k, (a, b) in enumerate(split_notes(x)):
                seg = x[max(0, a - SR // 50):b]
                f0, vf, _ = librosa.pyin(seg, fmin=FMIN[inst], fmax=2400, sr=SR, frame_length=4096)
                midi = int(round(float(np.median(librosa.hz_to_midi(f0[vf]))))) if vf.sum() > 5 else None
                if midi is None: continue
                p = out / "notes" / inst / f"{Path(f).stem}.{k:02d}.m{midi}.flac"
                save(seg, p)
                index.append(dict(kind="note", instrument=inst, midi=midi, dynamic="mf", path=str(p.relative_to(out)),
                                  source=urls[f], licence="Iowa MIS: free for any use, no restrictions", sha256=hashes[f]))
        print(inst, sum(1 for e in index if e["instrument"] == inst), "notes", flush=True)
    if iowa_done.exists(): index = json.loads(iowa_done.read_text())
    else: iowa_done.write_text(json.dumps(index))
    # --- Commons phrases ---------------------------------------------------------------------
    for title, inst, maxsec in COMMONS:
        info = commons_info(title); time.sleep(3)
        if not info or not OK_LICENCE.match(info["licence"]):
            print("skip (licence/lookup):", title, info and info["licence"]); continue
        slug = re.sub(r"\W+", "-", Path(title).stem)[:60].strip("-")
        raw = out / "raw" / f"commons-{slug}"
        try: h = get(info["url"], raw / Path(info["url"]).name)
        except Exception as e: print("skip (download failed):", title, e, flush=True); continue
        x = to_mono48(raw / Path(info["url"]).name)
        if len(x) > maxsec * SR:  # keep a stretch from the body, not the opening silence
            a = int(len(x) * 0.15); x = x[a:a + maxsec * SR]
        p = out / "phrases" / f"{inst}-{slug}.flac"
        save(x, p)
        index.append(dict(kind="phrase", instrument=inst, path=str(p.relative_to(out)), seconds=round(len(x) / SR, 1),
                          source=info["page"], url=info["url"], licence=info["licence"], author=info["author"], sha256=h))
        print("phrase", inst, slug, info["licence"], flush=True)
    (out / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))
    print("wrote", out / "index.json", len(index), "items")


if __name__ == "__main__":
    main()
