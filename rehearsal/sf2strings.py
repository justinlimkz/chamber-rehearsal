"""Cut the violin, viola and cello out of MuseScore's SF3 SoundFont and write them as a plain SF2.

The iPad app plays through Apple's AVAudioUnitSampler, which reads SF2 but not SF3 (SF3 stores each sample as an
Ogg Vorbis stream). This keeps GM bank 0 programs 40/41/42 with their instruments and samples, decodes the samples
to 16-bit PCM and rebuilds every index. It also works around two bugs in Apple's SF2 import (fold_preset_times,
bake_offsets) and adds legato copies of the presets (add_legato). Run: uv run --with soundfile python -m rehearsal.sf2strings [src] [out]
"""
from __future__ import annotations

import io
import math
import struct
import sys
from pathlib import Path

# MuseScore Studio's bundled SoundFont (MIT licence). Installed with `brew install --cask musescore`.
DEFAULT_SOUNDFONT = Path("/Applications/MuseScore 4.app/Contents/Resources/sound/MS Basic.sf3")

DEFAULT_OUT = Path("ios/Sounds/strings.sf2")
KEEP = {(0, 40), (0, 41), (0, 42)}  # (bank, program): Violin, Viola, Cello
GEN_INSTRUMENT, GEN_SAMPLE_ID, GEN_START_OFFSET, GEN_ATTACK_VOL = 41, 53, 0, 34
LEGATO_BANK = 1  # the legato copies of the presets (Strings.swift loads them for joined-on notes)
LEGATO_SKIP_S = 0.12  # how far into each sample a legato note starts: past most of the bow attack
LEGATO_ATTACK_S = 0.06  # and the fade-in over which it takes over from the note before
TIME_GENS = {25, 26, 27, 28, 29, 30, 33, 34, 35, 36, 37, 38}  # modulation and volume envelope stages (timecents)
ADDRESS_GENS = {1, 2, 3, 12, 45, 50}  # sample end and loop offsets: not used by these instruments
PAD = 46  # SF2 wants 46 zero frames after every sample
# record sizes and struct layouts of the pdta chunks
PHDR, PBAG, MOD, GEN, INST, SHDR = "<20sHHHLLL", "<HH", "<HHhHH", "<Hh", "<20sH", "<20sLLLLLBbHH"


def read_chunks(data: bytes) -> dict[str, bytes]:
    """Every sub-chunk of the RIFF's LIST chunks, keyed by its id (ids are unique across INFO, sdta, pdta)."""
    out, pos = {}, 12
    while pos < len(data):
        tag, size = struct.unpack_from("<4sI", data, pos)
        if tag == b"LIST":
            p = pos + 12
            while p < pos + 8 + size:
                t, s = struct.unpack_from("<4sI", data, p)
                out[t.decode()] = data[p + 8 : p + 8 + s]
                p += 8 + s + (s & 1)
        pos += 8 + size + (size & 1)
    return out


def records(buf: bytes, fmt: str) -> list[tuple]:
    n = struct.calcsize(fmt)
    return [struct.unpack_from(fmt, buf, i) for i in range(0, len(buf) - n + 1, n)]


def pack(fmt: str, rows) -> bytes:
    return b"".join(struct.pack(fmt, *r) for r in rows)


def chunk(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def zones(bags, gens) -> list[list[tuple]]:
    """Each zone's generators (`bags` without its terminal record)."""
    ends = [b[0] for b in bags[1:]] + [len(gens)]
    return [list(gens[b[0] : e]) for b, e in zip(bags, ends)]


def unzones(zs, bags, gens) -> None:
    """Write zones back into `gens` in place, keeping each bag's modulator index."""
    gens[:] = [g for z in zs for g in z]
    pos = 0
    for i, z in enumerate(zs):
        bags[i] = (pos, bags[i][1])
        pos += len(z)


def fold_preset_times(phdr, pbag, pgen, inst, ibag, igen) -> None:
    """Move preset-level envelope times into the instruments, where Apple's sampler reads them right.

    SF2 adds a preset's timecents to the instrument's, but Apple's import adds them to the default (-12000) too:
    MuseScore's violin release (0.4 s, +1.5 s at preset level) came out as 0.6 ms, so every note stopped dead."""
    pz, iz = zones(pbag, pgen), zones(ibag, igen)
    p_first, i_first = [p[3] for p in phdr] + [len(pbag)], [i[1] for i in inst] + [len(ibag)]
    adds: dict[int, dict[int, int]] = {}
    for i in range(len(phdr)):
        glob: dict[int, int] = {}
        for z in range(p_first[i], p_first[i + 1]):
            times = {g: a for g, a in pz[z] if g in TIME_GENS}
            target = next((a for g, a in pz[z] if g == GEN_INSTRUMENT), None)
            if target is None:
                glob = times
            elif {**glob, **times}:
                assert adds.setdefault(target, {**glob, **times}) == {**glob, **times}, "instrument shared by presets"
            pz[z] = [(g, a) for g, a in pz[z] if g not in TIME_GENS]
    for i, add in adds.items():
        first = i_first[i]
        assert all(g != GEN_SAMPLE_ID for g, _ in iz[first]), f"instrument {i} has no global zone"
        for z in range(first, i_first[i + 1]):
            have = dict(iz[z])
            for g, a in add.items():
                if g in have or z == first:
                    iz[z] = put_gen(iz[z], g, have.get(g, -12000) + a)
    unzones(pz, pbag, pgen)
    unzones(iz, ibag, igen)


def bake_offsets(inst, ibag, igen, shdr) -> None:
    """Replace sample start offsets with sample headers that start there (in the same sample data).

    Apple's sampler ignores startAddrsOffset but still moves the loop by it, so such a zone loops the wrong stretch
    of its sample (MuseScore's mezzo-forte cello has offsets of up to 0.13 s)."""
    iz, i_first = zones(ibag, igen), [i[1] for i in inst] + [len(ibag)]
    derived: dict[tuple[int, int], int] = {}
    for i in range(len(inst)):
        glob = {}
        for z in range(i_first[i], i_first[i + 1]):
            have = dict(iz[z])
            assert not ADDRESS_GENS & have.keys(), f"zone {z}: unsupported address offsets"
            if GEN_SAMPLE_ID not in have:
                glob = have
            else:
                offset = have.get(0, glob.get(0, 0)) + 32768 * have.get(4, glob.get(4, 0))
                if offset:
                    s = have[GEN_SAMPLE_ID]
                    if (s, offset) not in derived:
                        n, start, end, ls, le, *rest = shdr[s]
                        assert start + offset < ls, f"{n!r}: offset {offset} past the loop"
                        derived[s, offset] = len(shdr)
                        shdr.append((n.rstrip(b"\0")[:13] + b"+%d" % offset, start + offset, end, ls, le, *rest))
                    iz[z] = put_gen(iz[z], GEN_SAMPLE_ID, derived[s, offset])
            iz[z] = [(g, a) for g, a in iz[z] if g not in (0, 4)]
    unzones(iz, ibag, igen)


def put_gen(g: list[tuple], k: int, a: int) -> list[tuple]:
    """Set generator k in a zone: keyRange and velRange stay first, instrument/sampleID last."""
    if any(kk == k for kk, _ in g):
        return [(kk, a if kk == k else aa) for kk, aa in g]
    end = len(g) - sum(1 for kk, _ in g if kk in (GEN_INSTRUMENT, GEN_SAMPLE_ID))
    return [(k, a)] + g if k in (43, 44) else g[:end] + [(k, a)] + g[end:]


def add_legato(phdr, pbag, pgen, pmod, inst, ibag, igen, imod, shdr) -> None:
    """Append a bank-1 copy of every preset, for notes that join on to the one before (legato).

    Every sample starts with its bow attack, which takes 0.1-0.2 s to reach full level; replaying it on each note of
    a slurred line is what makes the notes sound separate. The copies share the samples but start LEGATO_SKIP_S
    into them and fade in over LEGATO_ATTACK_S, so the new note grows out of the old one's release. Attack layers
    (the cello's) are left out, and velocity-to-attack modulators dropped so the fade-in is the same at any velocity.
    """
    # snapshots with end sentinels, so zone ranges don't move while we append
    ib, pb = ibag + [(len(igen), len(imod))], pbag + [(len(pgen), len(pmod))]
    ig, im, pg, pm = list(igen), list(imod), list(pgen), list(pmod)
    i_first, p_first = [z[1] for z in inst] + [len(ibag)], [p[3] for p in phdr] + [len(pbag)]
    n_inst, n_presets = len(inst), len(phdr)
    attack = round(1200 * math.log2(LEGATO_ATTACK_S))

    def gens(z, src, bags):
        return src[bags[z][0] : bags[z + 1][0]]

    def mods(z, src, bags):
        return [m for m in src[bags[z][1] : bags[z + 1][1]] if m[1] != GEN_ATTACK_VOL]

    copies: dict[int, int] = {}
    for i in range(n_inst):
        zones = range(i_first[i], i_first[i + 1])
        samples = [a for z in zones for g, a in gens(z, ig, ib) if g == GEN_SAMPLE_ID]
        if any(b"attack" in shdr[s][0].lower() for s in samples):
            continue  # an attack layer: no part of a legato note
        copies[i] = len(inst)
        inst.append((inst[i][0].rstrip(b"\0")[:13] + b" Legato", len(ibag)))
        for z in zones:
            g = put_gen(gens(z, ig, ib), GEN_ATTACK_VOL, attack)  # the fade-in, in every zone (some set their own)
            sample = next((a for k, a in g if k == GEN_SAMPLE_ID), None)
            if sample is not None:  # start further in (on top of any offset the zone has), staying before the loop
                _, start, _, loop_start, _, rate, *_ = shdr[sample]
                offset = dict(g).get(GEN_START_OFFSET, 0)
                skip = min(offset + round(LEGATO_SKIP_S * rate), loop_start - start - 1, 32767)
                g = put_gen(g, GEN_START_OFFSET, skip)
            ibag.append((len(igen), len(imod)))
            igen += g
            imod += mods(z, im, ib)
    for i in range(n_presets):
        name, program, bank, *_ = phdr[i]
        phdr.append((name.rstrip(b"\0")[:13] + b" Legato", program, LEGATO_BANK, len(pbag), 0, 0, 0))
        for z in range(p_first[i], p_first[i + 1]):
            g = gens(z, pg, pb)
            target = next((a for k, a in g if k == GEN_INSTRUMENT), None)
            if target is not None and target not in copies:
                continue
            pbag.append((len(pgen), len(pmod)))
            pgen += [(k, copies[a] if k == GEN_INSTRUMENT else a) for k, a in g]
            pmod += mods(z, pm, pb)


def build_sf2(src: Path, name: str = "Chamber Rehearsal Strings") -> tuple[bytes, dict]:
    import numpy as np
    import soundfile as sf

    ch = read_chunks(src.read_bytes())
    phdr, pbag, pmod, pgen = (records(ch[k], f) for k, f in [("phdr", PHDR), ("pbag", PBAG), ("pmod", MOD), ("pgen", GEN)])
    inst, ibag, imod, igen = (records(ch[k], f) for k, f in [("inst", INST), ("ibag", PBAG), ("imod", MOD), ("igen", GEN)])
    shdr = records(ch["shdr"], SHDR)

    # presets: keep the wanted ones; the terminal record (last) bounds the final preset's zones
    presets = [(i, p) for i, p in enumerate(phdr[:-1]) if (p[2], p[1]) in KEEP]  # p = (name, preset, bank, bag, ...)
    new_pbag, new_pgen, new_pmod, new_phdr = [], [], [], []
    inst_map: dict[int, int] = {}
    for i, p in presets:
        new_phdr.append((p[0], p[1], p[2], len(new_pbag), 0, 0, 0))
        for z in range(p[3], phdr[i + 1][3]):
            new_pbag.append((len(new_pgen), len(new_pmod)))
            for g, amt in pgen[pbag[z][0] : pbag[z + 1][0]]:
                if g == GEN_INSTRUMENT:
                    amt = inst_map.setdefault(amt, len(inst_map))
                new_pgen.append((g, amt))
            new_pmod += pmod[pbag[z][1] : pbag[z + 1][1]]
    # instruments: same trick, remapping sampleID; samples are numbered in order of first use
    sample_map: dict[int, int] = {}
    new_inst, new_ibag, new_igen, new_imod = [], [], [], []
    for old in sorted(inst_map, key=inst_map.get):
        new_inst.append((inst[old][0], len(new_ibag)))
        for z in range(inst[old][1], inst[old + 1][1]):
            new_ibag.append((len(new_igen), len(new_imod)))
            for g, amt in igen[ibag[z][0] : ibag[z + 1][0]]:
                if g == GEN_SAMPLE_ID:
                    amt = sample_map.setdefault(amt, len(sample_map))
                new_igen.append((g, amt))
            new_imod += imod[ibag[z][1] : ibag[z + 1][1]]

    # samples: decode each Ogg stream (shdr start/end are byte offsets into smpl; loops are relative to the
    # sample's own start, in decoded frames) and lay the PCM end to end with PAD zero frames after each
    smpl, pcm, new_shdr, pos = ch["smpl"], [], [], 0
    for old in sorted(sample_map, key=sample_map.get):
        n, start, end, ls, le, rate, pitch, corr, link, typ = shdr[old]
        audio, sr = sf.read(io.BytesIO(smpl[start:end]), dtype="int16")
        assert audio.ndim == 1 and sr == rate, f"{n!r}: expected mono at {rate} Hz, got {audio.shape} at {sr}"
        assert 0 <= ls < le <= len(audio), f"{n!r}: loop {ls}-{le} outside {len(audio)} frames"
        # FluidSynth plays SF3 samples peak-normalised to full scale (measured: gain per sample = its decoded peak, 0.72-0.86);
        # do the same so the iPad matches the Mac, rather than playing 1.2-1.4x quieter
        audio = np.round(audio * (32767 / np.abs(audio).max())).astype(np.int16)
        pcm += [audio, np.zeros(PAD, np.int16)]
        link = sample_map.get(link, 0) if typ & 0x10 and link in sample_map else 0
        typ &= ~0x10
        if typ in (2, 4, 8) and not link:
            typ = 1  # partner not kept: play as mono
        new_shdr.append((n, pos, pos + len(audio), pos + ls, pos + le, rate, pitch, corr, link, typ))
        pos += len(audio) + PAD

    fold_preset_times(new_phdr, new_pbag, new_pgen, new_inst, new_ibag, new_igen)
    add_legato(new_phdr, new_pbag, new_pgen, new_pmod, new_inst, new_ibag, new_igen, new_imod, new_shdr)
    bake_offsets(new_inst, new_ibag, new_igen, new_shdr)

    # terminal records
    t_name = b"EOP".ljust(20, b"\0")
    new_phdr.append((t_name, 0, 0, len(new_pbag), 0, 0, 0))
    new_pbag.append((len(new_pgen), len(new_pmod)))
    new_pgen.append((0, 0))
    new_pmod.append((0, 0, 0, 0, 0))
    new_inst.append((b"EOI".ljust(20, b"\0"), len(new_ibag)))
    new_ibag.append((len(new_igen), len(new_imod)))
    new_igen.append((0, 0))
    new_imod.append((0, 0, 0, 0, 0))
    new_shdr.append((b"EOS".ljust(20, b"\0"), 0, 0, 0, 0, 0, 0, 0, 0, 0))

    def zstr(s: str) -> bytes:
        b = s.encode() + b"\0"
        return b + b"\0" * (len(b) & 1)

    info = (chunk(b"ifil", struct.pack("<HH", 2, 1)) + chunk(b"isng", zstr("EMU8000")) + chunk(b"INAM", zstr(name))
            + chunk(b"ICOP", zstr("Subset of MuseScore MS Basic (MIT licence)"))
            + chunk(b"ISFT", zstr("rehearsal.sf2strings")))
    sdta = chunk(b"smpl", np.concatenate(pcm).astype("<i2").tobytes())
    pdta = b"".join(chunk(t, pack(f, r)) for t, f, r in [
        (b"phdr", PHDR, new_phdr), (b"pbag", PBAG, new_pbag), (b"pmod", MOD, new_pmod), (b"pgen", GEN, new_pgen),
        (b"inst", INST, new_inst), (b"ibag", PBAG, new_ibag), (b"imod", MOD, new_imod), (b"igen", GEN, new_igen),
        (b"shdr", SHDR, new_shdr)])
    lists = [chunk(b"LIST", t + body) for t, body in [(b"INFO", info), (b"sdta", sdta), (b"pdta", pdta)]]
    riff = b"sfbk" + b"".join(lists)
    stats = {"presets": [p[0].rstrip(b"\0").decode() for p in new_phdr[:-1]], "instruments": len(new_inst) - 1,
             "samples": len(new_shdr) - 1, "frames": pos}
    return chunk(b"RIFF", riff), stats


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOUNDFONT
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    data, stats = build_sf2(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"{out}: {len(data) / 1e6:.1f} MB, {stats}")


if __name__ == "__main__":
    main()
