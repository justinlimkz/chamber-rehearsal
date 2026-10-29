"""Cut the violin, viola and cello out of MuseScore's SF3 SoundFont and write them as a plain SF2.

The iPad app plays through Apple's AVAudioUnitSampler, which reads SF2 but not SF3 (SF3 stores each sample as an
Ogg Vorbis stream). This keeps GM bank 0 programs 40/41/42 with their instruments and samples, decodes the samples
to 16-bit PCM and rebuilds every index. Run: uv run --with soundfile python -m rehearsal.sf2strings [src] [out]
"""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from rehearsal.sound import DEFAULT_SOUNDFONT

DEFAULT_OUT = Path("ios/Sounds/strings.sf2")
KEEP = {(0, 40), (0, 41), (0, 42)}  # (bank, program): Violin, Viola, Cello
GEN_INSTRUMENT, GEN_SAMPLE_ID = 41, 53
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
