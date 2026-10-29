"""SF2 writing for the tone tests: fonts built from sample files, and vibrato added to an existing font.

Fonts are laid out the way Apple's AVAudioUnitSampler reads them correctly (see rehearsal/sf2strings.py): every
generator at instrument level (it takes preset-level times as absolute), no start offsets (it ignores them but
moves the loop), and bank-1 "legato" presets whose samples start past the bow attack, for Strings.swift.
"""
from __future__ import annotations

import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the repo, for rehearsal.sf2strings (runs under -I)
from rehearsal.sf2strings import (GEN, INST, MOD, PAD, PBAG, PHDR, SHDR, chunk, pack, put_gen, read_chunks,
                                  records, zones)

# generators
START_LOOP_FINE, END_LOOP_FINE = 2, 3
VIB_TO_PITCH, DELAY_VIB, FREQ_VIB = 6, 23, 24
ATTACK, HOLD, DECAY, SUSTAIN, RELEASE = 34, 35, 36, 37, 38
KEY_RANGE, VEL_RANGE, ATTENUATION, FINE_TUNE, SAMPLE_ID, SAMPLE_MODES, ROOT_KEY = 43, 44, 48, 52, 53, 54, 58
INSTRUMENT = 41
LEGATO_BANK = 1


def timecents(s: float) -> int:
    return round(1200 * math.log2(max(s, 0.001)))


@dataclass
class Vibrato:
    """The font's vibrato LFO. Depth 0 leaves it to CC1 (SF2's default modulator: up to 50 cents)."""
    rate: float = 5.5  # Hz
    delay: float = 0.25  # s after the note starts
    depth: int = 0  # cents

    def gens(self) -> list[tuple[int, int]]:
        g = [(DELAY_VIB, timecents(self.delay)), (FREQ_VIB, round(1200 * math.log2(self.rate / 8.176)))]
        return g + [(VIB_TO_PITCH, self.depth)] if self.depth else g


@dataclass
class Zone:
    pcm: np.ndarray  # int16 mono
    rate: int
    root: int  # MIDI key the recording is at
    lo: int  # key range
    hi: int
    loop: tuple[int, int]  # frames [start, end)
    cents: int = 0  # tuning correction
    vel: tuple[int, int] = (0, 127)
    gain_db: float = 0.0  # attenuation is generator 48 (cB); only cuts, so gains are applied to the PCM instead
    name: str = ""
    legato_skip: int | None = None  # frames; else the instrument's


@dataclass
class Instrument:
    name: str
    program: int
    zones: list[Zone]
    attack: float = 0.005
    release: float = 0.35
    legato_skip: float = 0.15  # s into each sample where a joined-on note starts (past the bow attack)
    legato_attack: float = 0.06
    vibrato: Vibrato | None = None
    extra: list[tuple[int, int]] = field(default_factory=list)


def write_font(instruments: list[Instrument], out: Path, name: str, copyright: str) -> dict:
    """Write an SF2 with bank 0 and bank-1 legato presets for each instrument; returns size stats."""
    pcm, shdr, frames = [], [], 0
    inst, ibag, igen, imod = [], [], [], []
    phdr, pbag, pgen, pmod = [], [], [], []

    def sample(z: Zone, skip: int, tag: bytes) -> int:
        nonlocal frames
        if skip == 0:  # new sample data
            data = z.pcm
            if z.gain_db:
                data = np.clip(np.round(data * 10 ** (z.gain_db / 20)), -32768, 32767).astype(np.int16)
            pcm.extend([data, np.zeros(PAD, np.int16)])
            z._pos = frames  # type: ignore[attr-defined]
            frames += len(data) + PAD
        pos = z._pos  # type: ignore[attr-defined]
        nm = (z.name.encode()[:16] or b"s%d" % len(shdr)) + tag
        shdr.append((nm[:20], pos + skip, pos + len(z.pcm), pos + z.loop[0], pos + z.loop[1], z.rate, z.root,
                     0, 0, 1))
        return len(shdr) - 1

    for ins in instruments:
        for bank in (0, LEGATO_BANK):
            legato = bank == LEGATO_BANK
            inst.append(((ins.name + (" Leg" if legato else "")).encode()[:20], len(ibag)))
            glob = [(ATTACK, timecents(ins.legato_attack if legato else ins.attack)),
                    (RELEASE, timecents(ins.release)), *(ins.vibrato.gens() if ins.vibrato else []), *ins.extra]
            ibag.append((len(igen), len(imod)))
            igen.extend(glob)
            for z in ins.zones:
                own = z.legato_skip if z.legato_skip is not None else round(ins.legato_skip * z.rate)
                skip = min(own, z.loop[0] - 1) if legato else 0
                sid = sample(z, skip, b"L" if legato else b"")
                g = [(KEY_RANGE, z.lo | z.hi << 8), (VEL_RANGE, z.vel[0] | z.vel[1] << 8), (SAMPLE_MODES, 1),
                     (ROOT_KEY, z.root)]
                if z.cents:
                    g.append((FINE_TUNE, z.cents))
                ibag.append((len(igen), len(imod)))
                igen.extend(g + [(SAMPLE_ID, sid)])
            phdr.append(((ins.name + (" Leg" if legato else "")).encode()[:20], ins.program, bank, len(pbag), 0, 0, 0))
            pbag.append((len(pgen), len(pmod)))
            pgen.append((INSTRUMENT, len(inst) - 1))

    phdr.append((b"EOP", 0, 0, len(pbag), 0, 0, 0)); pbag.append((len(pgen), len(pmod)))
    pgen.append((0, 0)); pmod.append((0, 0, 0, 0, 0))
    inst.append((b"EOI", len(ibag))); ibag.append((len(igen), len(imod))); igen.append((0, 0)); imod.append((0, 0, 0, 0, 0))
    shdr.append((b"EOS", 0, 0, 0, 0, 0, 0, 0, 0, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_riff(np.concatenate(pcm), phdr, pbag, pmod, pgen, inst, ibag, imod, igen, shdr, name, copyright))
    return {"bytes": out.stat().st_size, "frames": frames, "zones": sum(len(i.zones) for i in instruments)}


def _riff(pcm, phdr, pbag, pmod, pgen, inst, ibag, imod, igen, shdr, name, copyright) -> bytes:
    def zstr(s: str) -> bytes:
        b = s.encode() + b"\0"
        return b + b"\0" * (len(b) & 1)

    pad20 = lambda rows: [(r[0].ljust(20, b"\0")[:20], *r[1:]) for r in rows]
    info = (chunk(b"ifil", struct.pack("<HH", 2, 1)) + chunk(b"isng", zstr("EMU8000")) + chunk(b"INAM", zstr(name))
            + chunk(b"ICOP", zstr(copyright)) + chunk(b"ISFT", zstr("chamber-rehearsal tone/sf2.py")))
    sdta = chunk(b"smpl", pcm.astype("<i2").tobytes())
    pdta = b"".join(chunk(t, pack(f, r)) for t, f, r in [
        (b"phdr", PHDR, pad20(phdr)), (b"pbag", PBAG, pbag), (b"pmod", MOD, pmod), (b"pgen", GEN, pgen),
        (b"inst", INST, pad20(inst)), (b"ibag", PBAG, ibag), (b"imod", MOD, imod), (b"igen", GEN, igen),
        (b"shdr", SHDR, pad20(shdr))])
    lists = [chunk(b"LIST", t + body) for t, body in [(b"INFO", info), (b"sdta", sdta), (b"pdta", pdta)]]
    return chunk(b"RIFF", b"sfbk" + b"".join(lists))


def add_vibrato(src: Path, out: Path, vib: Vibrato) -> None:
    """Copy a font, setting the vibrato LFO in every instrument's global zone (all of rehearsal/sf2strings' have one)."""
    data = src.read_bytes()
    ch = read_chunks(data)
    inst, ibag, igen = records(ch["inst"], INST), records(ch["ibag"], PBAG), records(ch["igen"], GEN)
    iz = zones(ibag[:-1], igen[:-1])
    for i in range(len(inst) - 1):
        first = inst[i][1]
        assert all(g != SAMPLE_ID for g, _ in iz[first]), f"instrument {i} has no global zone"
        for g, a in vib.gens():
            iz[first] = put_gen(iz[first], g, a)
    gens = [g for z in iz for g in z] + [igen[-1]]
    pos, bags = 0, []
    for b, z in zip(ibag[:-1], iz):
        bags.append((pos, b[1])); pos += len(z)
    bags.append((pos, ibag[-1][1]))
    pd = {k: ch[k] for k in ("phdr", "pbag", "pmod", "pgen", "inst", "imod", "shdr")}
    pd["ibag"], pd["igen"] = pack(PBAG, bags), pack(GEN, gens)
    pdta = b"".join(chunk(k.encode(), pd[k]) for k in ("phdr", "pbag", "pmod", "pgen", "inst", "ibag", "imod", "igen", "shdr"))
    info = b"".join(chunk(k.encode(), ch[k]) for k in ("ifil", "isng", "INAM", "ICOP", "ISFT") if k in ch)
    lists = [chunk(b"LIST", t + body) for t, body in [(b"INFO", info), (b"sdta", chunk(b"smpl", ch["smpl"])), (b"pdta", pdta)]]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(chunk(b"RIFF", b"sfbk" + b"".join(lists)))
