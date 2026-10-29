"""Objective scorecard: how natural do rendered strings sound, measured against real recordings.

Run:  uv run --no-project --with numpy --with scipy --with soundfile --with librosa \
          python tone/scorecard.py build/tone/renders \
          [--ref build/tone/ref] [--json out.json] [--no-fad] [--selftest]
Layout: <renders>/<candidate>/<clip>.wav (+ optional <clip>.wav.events or <clip>.events, lines
"seconds on|off Instrument midi"). Without events, notes come from pitch tracking (solo only).
Prepare the references first with tone/reference.py.

It finds defects, it does not pick winners: every value is shown next to the range real players
span (5th-95th percentile over the reference items) and '<' / '>' marks values outside it.
Metric limits are listed in METRICS below and in the run report.
"""
import json, sys, time, warnings
from collections import defaultdict
from pathlib import Path
import numpy as np, soundfile as sf, librosa
from scipy.signal import medfilt

warnings.filterwarnings("ignore")
SR, HOP = 48000, 480  # analysis rate; envelope hop = 10 ms
FMIN = dict(violin=180, viola=120, cello=60)
INST = {"Violin": "violin", "Viola": "viola", "Violoncello": "cello", "Cello": "cello"}

# name -> (unit, what it measures / limits, where its reference range comes from)
METRICS = {
    "join_dip_db": ("dB", "level dip at a legato note change (0 = seamless; more negative = audible hole)", "phrases"),
    "join_rise_ms": ("ms", "time from the change until the new note is within 3 dB of its sustain", "phrases"),
    "join_gap_ms": ("ms", "silent time (>30 dB below the notes) around a legato change", "phrases"),
    "attack_ms": ("ms", "onset to within 3 dB of sustain, notes after a rest; event time in renders vs audio threshold in references", "notes+phrases"),
    "loop_flux": ("0-1", "peak autocorrelation of spectral flux at 50-500 ms lag in held notes (sample-loop buzz; vibrato also lands here)", "notes+phrases"),
    "loop_env": ("0-1", "same on the amplitude envelope (tremolo / loop pulsing)", "notes+phrases"),
    "flux_cv": ("", "spectral-flux std/mean in held notes (very low = frozen, very high = chaotic)", "notes+phrases"),
    "vib_rate_hz": ("Hz", "vibrato rate from the f0 track of notes held >= 1 s (n/a where no vibrato)", "phrases"),
    "vib_extent_c": ("cents", "vibrato half-extent (amplitude) of the same", "phrases"),
    "vib_onset_ms": ("ms", "time until vibrato reaches half its peak amplitude", "phrases"),
    "contour_db": ("dB", "level at 70% of a held note minus at 25% (swell +, decay -)", "notes+phrases"),
    "env_range_db": ("dB", "5th-95th percentile spread of the level over the clip (phrase dynamics)", "phrases"),
    "centroid_hz": ("Hz", "spectral centroid of note sustains (dry anechoic reference; reverb and bright EQ shift it)", "notes"),
    "mfcc_dist": ("", "distance of a note's MFCC timbre to the nearest real note of that instrument within 3 semitones", "notes"),
    "tail_ms": ("ms", "after the last bow/key-up: time for the level to fall a further 20 dB (room + release tail)", "notes+phrases"),
}
VIB_RATIO = .4  # share of 0.5-12 Hz f0 wobble power that must sit in one 4-9 Hz peak to call it vibrato


# ---------------------------------------------------------------- signal helpers
def load(path):
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    x = x.mean(1)
    return librosa.resample(x, orig_sr=sr, target_sr=SR) if sr != SR else x


def env_db(x):
    """20 ms RMS level in dB every 10 ms."""
    p = np.convolve(x ** 2, np.ones(2 * HOP) / (2 * HOP), "same")[::HOP]
    return 10 * np.log10(p + 1e-12)


def lv(e, a, b):
    """Median level (dB) between times a and b (nan if empty)."""
    a, b = max(0, int(round(a * 100))), int(round(b * 100))
    return float(np.median(e[a:b])) if b > a and a < len(e) else np.nan


def f0_midi(x, fmin=55, fmax=1600):
    """pyin f0 as fractional MIDI per 10 ms (nan where unvoiced); run at 16 kHz for speed."""
    y = librosa.resample(x, orig_sr=SR, target_sr=16000)
    f, v, _ = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=16000, frame_length=2048, hop_length=160)
    m = np.where(v, librosa.hz_to_midi(np.where(v, f, 100)), np.nan)
    return m


def notes_from_audio(x, fmin=55):
    """Split a monophonic line into notes where pitch steps by >0.7 semitone or goes unvoiced."""
    m = f0_midi(x, fmin); notes, cur, gap, pend = [], None, 0, 0
    close = lambda c, end: notes.append(dict(on=c[0] / 100, off=end / 100, midi=float(np.median(c[1])))) if end - c[0] >= 12 else None
    for i, v in enumerate(m):
        if np.isnan(v):
            gap += 1
            if gap >= 8 and cur: close(cur, i - gap + 1); cur = None
            continue
        gap = 0
        if cur is None: cur, pend = [i, [v]], 0
        elif abs(v - np.median(cur[1][-15:])) > 0.7:
            pend += 1
            if pend >= 4: close(cur, i - pend + 1); cur, pend = [i - pend + 1, [v] * pend], 0
        else: pend = 0; cur[1].append(v)
    if cur: close(cur, len(m))
    return notes


def read_events(path):
    """{instrument: [note dicts]} from a '.events' file beside the wav, or None."""
    for ev in (Path(str(path) + ".events"), Path(path).with_suffix(".events")):
        if ev.exists():
            open_, out = {}, defaultdict(list)
            for l in ev.read_text().splitlines():
                t, k, name, p = l.split()[:4]; key = (name, int(p))
                if k == "on": open_.setdefault(key, []).append(float(t))
                elif open_.get(key): out[INST.get(name, name.lower())].append(dict(on=open_[key].pop(0), off=float(t), midi=int(p)))
            return {k: sorted(v, key=lambda n: n["on"]) for k, v in out.items()}
    return None


# ---------------------------------------------------------------- the metrics
def measure(x, notes, solo=True, inst=None):
    """Per-unit metric lists for one signal and its notes (events or pitch-tracked)."""
    e = env_db(x); out = defaultdict(list); notes = sorted(notes, key=lambda n: n["on"])
    fr = np.concatenate([e[int((n["on"] + .1) * 100):int((n["off"] - .1) * 100)] for n in notes] or [e[:0]])
    if len(fr) > 20: out["env_range_db"].append(float(np.percentile(fr, 95) - np.percentile(fr, 5)))  # level spread while notes sound
    for i, n in enumerate(notes):
        d = n["off"] - n["on"]; prev = notes[i - 1] if i else None
        nxt = notes[i + 1] if i + 1 < len(notes) else None
        dd = min(d, 1.0)
        sus = lv(e, n["on"] + .4 * dd, n["on"] + .7 * dd)
        legato = prev is not None and n["on"] - prev["off"] <= .25 and n["on"] - prev["on"] >= .2
        if d < .3 or np.isnan(sus): continue
        t = n["on"]
        if legato:  # dip below the level just before the change, silent time, rise to sustain
            before = lv(e, t - .3, t - .08); win = e[int((t - .06) * 100):int((t + .12) * 100)]
            if not win.size or np.isnan(before): continue
            out["join_dip_db"].append(max(-60.0, float(win.min() - before)))
            out["join_gap_ms"].append(10.0 * float((e[int((t - .25) * 100):int((t + .15) * 100)] < min(before, sus) - 30).sum()))
            if solo: out["join_rise_ms"].append(next((k * 10 for k in range(0, 40) if lv(e, t + k * .01, t + k * .01 + .02) > sus - 3), 400.0))
        elif solo and (prev is None or n["on"] - prev["off"] > .15):  # attack from silence
            if n.get("audio"):  # audio-derived onset: back up to where the level crosses sus-30
                k = int(t * 100)
                while k > max(0, int(t * 100) - 20) and e[k] > sus - 30: k -= 1
                t = k / 100
            out["attack_ms"].append(next((k * 10 for k in range(0, 80) if lv(e, t + k * .01, t + k * .01 + .02) > sus - 3), 800.0))
        # tail after the bow/key lifts: only when a rest follows
        if nxt is None or nxt["on"] - n["off"] > .5:
            body = lv(e, n["on"] + .4 * d, n["on"] + .7 * d); end = min(n["off"] + .5, nxt["on"] if nxt else 1e9, len(e) / 100)
            ks = [k for k in range(int((n["on"] + .5 * d) * 100), int(end * 100)) if e[k] >= body - 6]
            if ks:
                k0 = ks[-1]; k1 = next((k for k in range(k0, min(len(e), k0 + 300)) if e[k] < body - 26), None)
                if k1 is not None: out["tail_ms"].append(10.0 * (k1 - k0))
        if not solo or d < 1.0: continue
        a, b = n["on"] + .4, n["off"] - .15
        seg = x[int(a * SR):int(b * SR)]
        if len(seg) < SR // 2: continue
        # held-note life: spectral flux regularity, envelope regularity, contour
        L = librosa.power_to_db(librosa.feature.melspectrogram(y=seg, sr=SR, n_fft=2048, hop_length=512, n_mels=64, fmax=8000), top_db=60)
        fl = np.sqrt((np.diff(L, axis=1) ** 2).mean(0)); out["flux_cv"].append(float(fl.std() / (fl.mean() + 1e-9)))
        out["loop_flux"].append(acpeak(fl, 512 / SR)); ee = e[int(a * 100):int(b * 100)]
        out["loop_env"].append(acpeak(ee - np.convolve(ee, np.ones(30) / 30, "same"), .01))
        out["contour_db"].append(lv(e, n["on"] + .7 * d, n["on"] + .75 * d) - lv(e, n["on"] + .25 * d, n["on"] + .3 * d))
        vib = vibrato(x, n)
        for k, v in vib.items():
            if not k.startswith("_"): out[k].append(v)
    return out


def acpeak(s, hop, lo=.05, hi=.5):
    """Periodicity of s: highest normalised autocorrelation at lags lo..hi seconds, searched after the
    first zero crossing so that mere smoothness (slow drift, a single hump) does not count."""
    s = s - s.mean(); den = float((s ** 2).sum())
    if den < 1e-9 or len(s) < hi / hop + 4: return np.nan
    ac = np.correlate(s, s, "full")[len(s) - 1:] / den
    a, b = int(lo / hop), int(hi / hop) + 1
    neg = np.flatnonzero(ac[:b] < 0)
    return float(max(0.0, ac[max(a, neg[0]):b].max())) if len(neg) else 0.0


def vibrato(x, n):
    """Rate (Hz), half-extent (cents) and onset delay of vibrato in a held note via its f0 track."""
    m = f0_midi(x[int(max(0, n["on"] - .05) * SR):int((n["off"] + .05) * SR)], 55)
    ok = ~np.isnan(m)
    if ok.sum() < 60: return {}
    c = np.interp(np.arange(len(m)), np.flatnonzero(ok), m[ok]) * 100
    c = c[15:-5]; c = c - np.convolve(np.pad(c, 25, "edge"), np.ones(51) / 51, "valid")  # drop the slow trend
    c = np.clip(c, -150, 150)
    f = np.fft.rfftfreq(1024, .01); P = np.abs(np.fft.rfft(c * np.hanning(len(c)), 1024)) ** 2
    band = (f >= 4) & (f <= 9); tot = P[(f >= .5) & (f <= 12)].sum() + 1e-9
    pk = f[band][P[band].argmax()]; share = P[(f >= pk - .75) & (f <= pk + .75)].sum() / tot  # power near the peak
    ext = float(np.sqrt(2) * 1.4826 * np.median(np.abs(c - np.median(c)))); rate = float(f[band][P[band].argmax()])
    out = {"vib_extent_c": ext, "_ratio": float(share), "_rate": rate}
    if ext >= 6 and share >= VIB_RATIO:  # a real periodic wobble, not drift or tracker noise
        out["vib_rate_hz"] = rate
        amp = np.sqrt(2) * np.array([c[max(0, i - 15):i + 15].std() for i in range(len(c))])
        out["vib_onset_ms"] = 10.0 * float(np.argmax(amp >= amp.max() / 2))
    return out


def timbre(x, notes):
    """(midi, centroid, mfcc vector) from the middle of each note >= 0.4 s."""
    res = []
    for n in notes:
        d = n["off"] - n["on"]
        if d < .4: continue
        s = x[int((n["on"] + .35 * d) * SR):int((n["on"] + .75 * d) * SR)]
        res.append((n["midi"], float(librosa.feature.spectral_centroid(y=s, sr=SR, n_fft=4096).mean()), mfcc_vec(s)))
    return res


def mfcc_vec(s):
    M = librosa.feature.mfcc(y=s, sr=SR, n_mfcc=20, n_fft=4096, hop_length=1024, n_mels=64, fmax=8000)
    return M[1:].mean(1)


# ---------------------------------------------------------------- references
def ref_items(ref):
    return json.loads((Path(ref) / "index.json").read_text())


def one_ref(args):
    """Analyse one reference item (runs in a worker process)."""
    ref, it = args
    x = load(Path(ref) / it["path"]); inst = it["instrument"]
    if it["kind"] == "note":
        e = env_db(x); on = int(np.argmax(e > e.max() - 40)) / 100
        body = lv(e, on + .2, on + .8)  # off = last moment within 6 dB of the body level (the bow stop)
        off = max(k for k in range(len(e)) if e[k] >= body - 6) / 100
        ns = [dict(on=on, off=max(off, on + .5), midi=it["midi"])]
        m = measure(x, ns, True, inst)
        return it, {k: v[0] for k, v in m.items() if v}, [(a, b, c.tolist()) for a, b, c in timbre(x, ns)]
    if inst in FMIN: ns = [dict(n, audio=True) for n in notes_from_audio(x, FMIN[inst])]
    else: ns = [dict(on=0.5, off=len(x) / SR - .5, midi=60)]  # ensembles: one pseudo-voice, clip-level metrics only
    m = measure(x, ns, inst in FMIN, inst)
    return it, dict(instrument=inst, source=it["source"], n_notes=len(ns), **{k: [float(a) for a in v if np.isfinite(a)] for k, v in m.items()}), []


def ref_stats(ref, cache):
    """Metric values from every reference item, cached by a hash of the metric code:
    {'notes': {inst: [metric dict]}, 'phrases': [dict], 'timbre': {inst: [(midi, centroid, mfcc)]}}."""
    import hashlib, inspect
    from concurrent.futures import ProcessPoolExecutor
    code = "".join(inspect.getsource(g) for g in (env_db, lv, f0_midi, notes_from_audio, measure, acpeak, vibrato, timbre, mfcc_vec, one_ref)) + str(VIB_RATIO)
    ver = hashlib.sha1(code.encode()).hexdigest()[:10]; ck = Path(cache)  # reporting/FAD edits keep the cache
    if ck.exists():
        c = json.loads(ck.read_text())
        if c.get("version") == ver: return c
    res = dict(version=ver, notes=defaultdict(list), phrases=[], timbre=defaultdict(list))
    with ProcessPoolExecutor() as ex:
        for it, m, tim in ex.map(one_ref, [(ref, it) for it in ref_items(ref)], chunksize=4):
            if it["kind"] == "note":
                res["notes"][it["instrument"]].append(m); res["timbre"][it["instrument"]] += tim
            else: res["phrases"].append(m)
    for k, v in res["timbre"].items():  # leave-one-out nearest-note MFCC distance = real-vs-real spread
        for i, (mi, c, vec) in enumerate(v):
            ds = [np.linalg.norm(np.array(vec) - np.array(w)) for j, (mj, _, w) in enumerate(v) if j != i and abs(mj - mi) <= 3]
            if len(res["notes"][k]) > i and ds: res["notes"][k][i]["mfcc_dist"] = float(min(ds))
            if len(res["notes"][k]) > i: res["notes"][k][i]["centroid_hz"] = c
    out = json.loads(json.dumps(res)); ck.parent.mkdir(parents=True, exist_ok=True); ck.write_text(json.dumps(out)); return out


def ref_values(rs, met, inst=None):
    """Reference values of a metric: per Iowa note and per solo-phrase median (or ensemble phrases if inst='ens')."""
    src = METRICS[met][2]; vals = []
    if inst == "ens":
        vals = [float(np.median(p[met])) for p in rs["phrases"] if p["instrument"] not in FMIN and p.get(met)]
    else:
        if "notes" in src: vals += [d[met] for i, v in rs["notes"].items() if inst in (None, i) for d in v if met in d]
        if "phrases" in src:  # vibrato is rare and per-note, so pool its notes; other metrics use one median per recording
            ps = [p for p in rs["phrases"] if p["instrument"] in FMIN and inst in (None, p["instrument"]) and p.get(met)]
            vals += [q for p in ps for q in p[met]] if met.startswith("vib_") else [float(np.median(p[met])) for p in ps]
    return [v for v in vals if np.isfinite(v)]


def ranges(rs):
    """{group: {metric: (p5, p95, n)}} for 'solo' (all solo instruments), each instrument, and 'ens' (ensemble recordings, some with piano)."""
    R = {}
    for g in ["solo", "ens"] + list(FMIN):
        for met in METRICS:
            v = ref_values(rs, met, None if g == "solo" else g)
            if len(v) >= (2 if g == "ens" else 4 if g == "solo" else 8): R.setdefault(g, {})[met] = (float(np.percentile(v, 5)), float(np.percentile(v, 95)), len(v))
    return R


# ---------------------------------------------------------------- candidates
def clip_analysis(path, rs, ref_tim):
    """{label: {metric: value}} for one rendered clip; label is an instrument, or 'mix' for ensembles."""
    x = load(path); ev = read_events(path); res = {}
    if ev is None:  # no events: pitch-track as one voice; a trio mix will be poor, so call it 'mix'
        ns = [dict(n, audio=True) for n in notes_from_audio(x)]; ev = {"mix": ns}
    if len(ev) == 1:
        inst, ns = next(iter(ev.items()))
        m = measure(x, ns, True, inst); res[inst] = {k: float(np.nanmedian(v)) for k, v in m.items() if v and not np.all(np.isnan(v))}
        res[inst]["vib_frac"] = len(m.get("vib_rate_hz", [])) / max(1, len(m.get("vib_extent_c", [])))
        t = timbre(x, ns)
        if t:
            res[inst]["centroid_hz"] = float(np.median([c for _, c, _ in t]))
            refs = ref_tim.get(inst, [])
            ds = [min([np.linalg.norm(v - np.array(w)) for mj, _, w in refs if abs(mj - mi) <= 3] or [np.nan]) for mi, _, v in t]
            if not np.all(np.isnan(ds)): res[inst]["mfcc_dist"] = float(np.nanmedian(ds))
    else:  # ensemble mix: all voices merged, clip-level metrics only
        allns = sorted([n for v in ev.values() for n in v], key=lambda n: n["on"])
        m = measure(x, allns, False)
        res["mix"] = {k: float(np.nanmedian(v)) for k, v in m.items() if v}
    return res


def fad_scores(renders, ref, rs_items, seed=0, k=8):
    """Frechet Audio Distance on PANN (Cnn14, 16 kHz) embeddings of 3 s windows (1 s hop), per candidate
    vs the non-piano references. Embeddings are 2048-d, far more than our few hundred windows, so they
    are first projected on the top-k principal axes of the reference windows; with that, the number is
    only a coarse relative indicator. Returns a bootstrap 90% CI and the real-vs-real floor (two random
    halves of the reference files). PANN rather than VGGish: VGGish loads unpinned code from GitHub."""
    import torch
    from frechet_audio_distance import FrechetAudioDistance
    import numpy as np  # numpy<2 in this env: the checkpoint pickles numpy.core globals, which we allow-list instead of unpickling arbitrary code
    with torch.serialization.safe_globals([np.core.multiarray._reconstruct, np.ndarray, np.dtype, np.dtypes.Float32DType, np.dtypes.Float64DType, np.dtypes.Int64DType, np.dtypes.Int32DType]):
        f = FrechetAudioDistance(model_name="pann", sample_rate=16000, verbose=False)
    def emb(x):
        y = librosa.resample(x, orig_sr=SR, target_sr=16000); y = y / (np.sqrt(np.mean(y[np.abs(y) > 1e-3] ** 2)) + 1e-9) * .05
        w = [y[i:i + 48000] for i in range(0, max(1, len(y) - 48000 + 1), 16000)]
        with torch.no_grad(): out = f.model(torch.tensor(np.stack(w)).float().to(f.device), None)["embedding"]
        return out.cpu().numpy()
    refE = []
    for it in rs_items:
        if it["instrument"] in ("schumann", "piano-trio") or it["kind"] == "note": continue  # piano is not in the renders
        refE.append(emb(load(Path(ref) / it["path"])))
    notes = defaultdict(list)  # glue each instrument's notes into 20-note runs separated by 0.3 s of silence
    for it in rs_items:
        if it["kind"] == "note": notes[it["instrument"]].append(load(Path(ref) / it["path"]))
    for v in notes.values():
        for i in range(0, len(v), 20): refE.append(emb(np.concatenate([np.concatenate([q, np.zeros(SR // 3, np.float32)]) for q in v[i:i + 20]])))
    R = np.vstack(refE); mu = R.mean(0); V = np.linalg.svd(R - mu, full_matrices=False)[2][:k]
    proj = lambda E: (E - mu) @ V.T
    rng = np.random.default_rng(seed)
    fd = lambda a, b: float(f.calculate_frechet_distance(a.mean(0), np.cov(a, rowvar=False), b.mean(0), np.cov(b, rowvar=False)))
    floors = []
    for _ in range(10):
        idx = rng.permutation(len(refE)); h = len(idx) // 2
        floors.append(fd(proj(np.vstack([refE[i] for i in idx[:h]])), proj(np.vstack([refE[i] for i in idx[h:]]))))
    out = {"_floor": (float(np.mean(floors)), float(np.std(floors)), len(R))}
    Rp = proj(R)
    for cand, clips in renders.items():
        C = proj(np.vstack([emb(load(p)) for p in clips]))
        bs = [fd(C[rng.integers(0, len(C), len(C))], Rp[rng.integers(0, len(Rp), len(Rp))]) for _ in range(50)]
        out[cand] = (fd(C, Rp), float(np.percentile(bs, 5)), float(np.percentile(bs, 95)), len(C))
    return out


# ---------------------------------------------------------------- reporting
def fmt(v, rng):
    if v is None or (isinstance(v, float) and np.isnan(v)): return "    -"
    flag = "<" if rng and v < rng[0] else ">" if rng and v > rng[1] else " "
    return f"{v:7.1f}{flag}" if abs(v) >= 10 or v == 0 else f"{v:7.2f}{flag}"


def selftest(rs, R, ref_tim):
    """Sanity checks that the metrics rank obvious cases correctly."""
    t = np.arange(0, 4, 1 / SR)
    def tone(vib=0, trem=0, loop=0):
        f0 = 440 * (1 + vib * np.sin(2 * np.pi * 5.5 * t))  # vib = fractional depth (0.01 = ~17 cents)
        s = np.sin(2 * np.pi * np.cumsum(f0) / SR) + .3 * np.sin(4 * np.pi * np.cumsum(f0) / SR)
        s *= np.minimum(1, t / .1) * np.minimum(1, (4 - t) / .1) * (1 - trem * (0.5 + .5 * np.sin(2 * np.pi * 6 * t)))
        if loop: s *= 1 + .5 * (np.mod(t, loop) < .03)  # a short bump every 'loop' seconds, like a sample loop point
        return (s * .1).astype(np.float32)
    note = [dict(on=0.0, off=4.0, midi=69)]
    rows = {}
    for name, x in dict(flat=tone(), vibrato=tone(vib=.012), tremolo=tone(trem=.5), loopbuzz=tone(loop=.25)).items():
        m = measure(x, note, True); rows[name] = {k: float(np.nanmedian(v)) for k, v in m.items() if v}
    print("\nSELFTEST synthetic sines (4 s held note)")
    cols = ["vib_rate_hz", "vib_extent_c", "loop_env", "loop_flux", "flux_cv", "contour_db"]
    print(f"{'':10}" + "".join(f"{c:>14}" for c in cols))
    for n, r in rows.items(): print(f"{n:10}" + "".join(f"{r.get(c, np.nan):14.2f}" for c in cols))
    ok = lambda b: "PASS" if b else "FAIL"
    print(" vibrato detected only in 'vibrato' (rate ~5.5 Hz):", ok("vib_rate_hz" in rows["vibrato"] and "vib_rate_hz" not in rows["flat"] and abs(rows["vibrato"]["vib_rate_hz"] - 5.5) < .7))
    print(" tremolo (6 Hz) raises loop_env over flat:", ok(rows["tremolo"]["loop_env"] > rows["flat"]["loop_env"] + .3))
    print(" loop bump every 250 ms raises loop_flux and loop_env over flat:", ok(rows["loopbuzz"]["loop_flux"] > rows["flat"]["loop_flux"] + .3 and rows["loopbuzz"]["loop_env"] > rows["flat"]["loop_env"] + .15))
    print(" flat sine has no vibrato and tremolo/loop do not read as vibrato:", ok(all("vib_rate_hz" not in rows[k] for k in ("flat", "tremolo", "loopbuzz"))))
    # real items vs their own ranges: fraction inside
    print("\nSELFTEST real recordings inside their own 5-95% ranges")
    for met, (lo, hi, n) in sorted(R["solo"].items()):
        vals = ref_values(rs, met)
        print(f" {met:14} {100 * np.mean([lo <= v <= hi for v in vals]):5.0f}% of {len(vals)} items inside [{lo:.2f}, {hi:.2f}]")


def run_fad(renders, ref):
    """Run fad_scores in its own uv env: frechet_audio_distance pins old numpy/numba that crash librosa's pyin."""
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as d:
        job, out = Path(d, "job.json"), Path(d, "out.json")
        job.write_text(json.dumps(dict(ref=str(ref), renders={c: [str(p) for p in ps] for c, ps in renders.items()})))
        subprocess.run(["uv", "run", "--no-project", "--with", "numpy<2", "--with", "scipy", "--with", "soundfile", "--with", "librosa",
                        "--with", "frechet_audio_distance", "python", str(Path(__file__).resolve()), "--fad-worker", str(job), str(out)], check=True)
        return json.loads(out.read_text())


def main():
    a = sys.argv[1:]
    if a and a[0] == "--fad-worker":
        j = json.loads(Path(a[1]).read_text())
        Path(a[2]).write_text(json.dumps(fad_scores({c: [Path(p) for p in ps] for c, ps in j["renders"].items()}, j["ref"], ref_items(j["ref"]))))
        return
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    renders_dir = Path(a[0]) if a and not a[0].startswith("--") else Path("build/tone/renders")
    ref = Path(opt("--ref", "build/tone/ref")); t0 = time.time()
    if not (ref / "index.json").exists(): sys.exit(f"no {ref}/index.json: run tone/reference.py first")
    rs = ref_stats(ref, ref / "ref_stats.json"); R = ranges(rs)
    ref_tim = {k: [(m, c, np.array(v)) for m, c, v in vs] for k, vs in rs["timbre"].items()}
    if "--selftest" in a: selftest(rs, R, ref_tim)
    renders = {d.name: sorted(d.glob("*.wav")) for d in sorted(renders_dir.iterdir()) if d.is_dir() and list(d.glob("*.wav"))}
    allres = {}
    cols = list(METRICS)
    short = dict(join_dip_db="dip_dB", join_rise_ms="rise_ms", join_gap_ms="gap_ms", attack_ms="atk_ms", loop_flux="loopF", loop_env="loopE", flux_cv="fluxCV",
                 vib_rate_hz="vibHz", vib_extent_c="vibCent", vib_onset_ms="vibOns", contour_db="contour", env_range_db="range", centroid_hz="centr", mfcc_dist="mfcc", tail_ms="tail_ms")
    W = 11
    print(f"\n{'candidate':24}{'clip':13}{'who':7}" + "".join(short[c].rjust(W) for c in cols))
    # note-level metrics use the instrument's own range; phrase-level ones pool all solo recordings
    rk = lambda met, who: R["ens"].get(met) if who == "mix" else (R.get(who, {}).get(met) if METRICS[met][2] == "notes" or met == "attack_ms" else R["solo"].get(met))
    for cand, clips in renders.items():
        for p in clips:
            for who, m in clip_analysis(p, rs, ref_tim).items():
                allres.setdefault(cand, {}).setdefault(p.stem, {})[who] = m
                print(f"{cand[:23]:24}{p.stem[:12]:13}{who[:6]:7}" + "".join(fmt(m.get(c), rk(c, who)).rjust(W) for c in cols), flush=True)
    print("\nreference ranges, p5..p95 (n items)   solo = all solo recordings; per-instrument ranges for attack/timbre/loop/contour are in the json")
    for c in cols:
        row = [f"{g} {R[g][c][0]:.3g}..{R[g][c][1]:.3g} (n={R[g][c][2]})" for g in ("solo", "ens") if c in R.get(g, {})]
        print(f"  {c:14}{METRICS[c][0]:6}" + ("   ".join(row) if row else "no reference"))
    print("legend: '<' below / '>' above the real range; '-' not measurable or no reference. attack/centroid/mfcc ranges are per instrument (see json).")
    fad = None
    if "--no-fad" not in a and renders:
        fad = run_fad(renders, ref)
        fl = fad.pop("_floor")
        print(f"\nFAD (PANN embeddings) vs non-piano references: real-vs-real floor {fl[0]:.1f} +- {fl[1]:.1f} ({fl[2]} ref windows)")
        for c, (v, lo, hi, n) in fad.items(): print(f"  {c:28}{v:7.1f}  90% CI [{lo:.1f}, {hi:.1f}]  ({n} windows)")
    print(f"\nrun time {time.time() - t0:.0f}s")
    if opt("--json"):
        Path(opt("--json")).write_text(json.dumps(dict(ranges=R, candidates=allres, fad=fad), indent=1, default=float))


if __name__ == "__main__":
    main()
