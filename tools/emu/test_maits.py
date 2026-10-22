#!/usr/bin/env python3
"""Proof of the MAITS machine (notes/74): ten macro engines after Mutable Instruments Plaits (MIT), as an added
machine, alone (27-maits.json, 7th machine) and with Model-TG (36-maits-tg.json over 30-model-tg-st.json, 8th
machine). The OS's own code, emulated (Unicorn), with the payload as the start-up hook rebuilds it.

  1. Start-up: the bootstrap's depacker reads the enlarged OS; the hook (stub.S, PACK) clears the payload's area,
     copies its pieces there, leaves the SRAM alone, then returns (as test_acid.py).
  2. Interface: the checks of test_syntakt_machines.py (alone) and of test_model_tg_syntakt.py (with Model-TG),
     applied to MAITS: the MACHINES screen, the wheel, the records, the descriptors (CONTOUR: Engine, 0..9).
  3. The bridge (maits.c), the voice loop alone (mcengine), every block:
     - the engines get exactly the dials, note and trig notes/74 par. 3 gives them, computed here from the voice
       and the parameters read at the entry of maits_update: COLOR/SHAPE/SWEEP held to 0..0x7f00, CONTOUR's engine
       0..9 at the middle of its tenth, the note (trig note + PITCH - 64 + FINE, Q8, held to 0..143), the trig on
       the block after the OS's;
     - before the amp chain, the 32 samples are EXACTLY the half-band filter (23 coefficients, MACRO's) of the
       engine's 96 kHz block and of the 22 samples before it;
     - a voice is computed exactly when the OS imposes it (trig, or amp envelope above 2^14);
     - the amp chain (DECAY, GATE) reads in the voice exactly what it reads for TONE.
  4. The engines, at the final output of the track: each of the ten sounds, and differently; the pitched ones
     (WSHP, FM, PD, CHIP, STRG, MODL) at their note within a few cents at notes 36, 60 and 84; a change of engine
     starts it from rest; every dial at both ends and random dials stay within full scale.
  5. With the OS around it: the original machines do not change (no MAITS instruction without a MAITS track);
     machine locks; 6 MAITS tracks; MAITS mixed with the original machines (and Model-TG's Sampler); MAITS with
     Model-TG = MAITS alone.
  6. Cost: instructions per block of a MAITS voice for each engine, of a silent voice, of TONE; the stack.
  7. With other mods (--with): start-up, the original machines identical to the same mods without MAITS, MAITS
     identical to MAITS alone (or with Model-TG alone), machine locks, mixed tracks.

    python3 tools/emu/test_maits.py --cycles model-cycles_OS1.13.syx [--quick] \\
        [--with model-tg-st,sample-preview-st,trig-hold,arp,step-rec,tempo-max,boot-anim,multiline-browser]

Takes about 15 minutes (5 with --quick), plus 5 with --with.
"""
import argparse
import json
import pathlib
import struct
import sys

import numpy as np
from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_UNMAPPED
from unicorn import m68k_const as mk

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import build                        # noqa: E402
import gen_maits as gmt             # noqa: E402
import gen_syntakt_engines as gs    # noqa: E402
import mcengine as E                # noqa: E402
import seqrig as S                  # noqa: E402
import test_acid as TA              # noqa: E402
import test_model_tg as TM          # noqa: E402
import test_model_tg_syntakt as tms  # noqa: E402
import test_sdvintage as T          # noqa: E402
import test_sdvintage_7th as t7     # noqa: E402
import test_syntakt_machines as tsm  # noqa: E402

DEV = HERE.parent.parent / "tweaks" / "model-cycles_OS1.13"
BASE = E.BASE
check = t7.check                    # failures: t7.FAIL (and tms.FAIL for the interface with Model-TG)

MARK = 0x4d414931                   # 'MAI1' (maits.c)
IDLE_LEVEL = 1 << 14
HB = (10327, -3131, 1579, -820, 434, -197)
AMP = TA.AMP
TONE = 4
OFF = 9                             # a machine out of range: the voice is not computed
ENGINES = ("WSHP", "FM", "SWRM", "GRAN", "PD", "CHIP", "NOIS", "MODL", "PART", "STRG")
PITCHED = (0, 1, 4, 5, 7, 9)        # WSHP, FM, PD, CHIP, MODL, STRG: a clear fundamental at the note
KW = dict(note=60, pitch=64, color=64, shape=64, sweep=64, contour=0, punch=0, gate=0, finetune=64, decay=100)


def load(name):
    return json.loads((DEV / name).read_text(encoding="utf-8"))


class Fw:
    """A firmware: MAIN OS (with what is appended after it), and the payload of MAITS as in memory."""

    def __init__(self, stock, tweaks):
        p, _ = build.apply_writes(stock, tweaks)
        pl, _ = build.build_payload(tweaks, stock, None)
        self.img, self.tweaks = bytes(p) + pl, tweaks
        tg = [t for t in tweaks if t["id"].startswith("model-tg")]
        self.blob_end = BASE + len(stock) + tg[0]["append"]["size"] if tg else None
        self.keep = [(TM.BLOB, self.blob_end)] if tg else []
        ours = [t for t in tweaks if t["id"] in ("maits", "maits-tg")]
        self.payload = self.mine = None
        if not ours:
            return
        self.mine = ours[0]
        ap_ = self.mine["append"]
        self.pay = int(ap_["dest"], 16)
        self.runtime = build.payload_runtime(self.mine, stock, None)
        self.payload = (self.pay, self.runtime)
        self.code_end = self.pay + len(ap_["parts"][0]["hex"]) // 2          # code and tables of MAITS
        self.nm = gs.TG_FIRST + 1 if tg else 7
        self.index = self.nm - 1                                            # MAITS
        self.sym = {k: int(v, 16) for k, v in self.mine["symbols"].items()}
        data = self.pay + gs.LAYOUT["DATA"]
        u32 = lambda va: struct.unpack_from(">I", self.runtime, va - self.pay)[0]
        self.upd = u32(data + 4 * self.nm + 4 * self.index)
        self.rnd = u32(data + 8 * self.nm + 4 * self.index)
        check(self.upd == self.sym["maits_update"] and self.rnd == self.sym["maits_render"],
              f"{self.mine['id']}: the machine table calls maits_update and maits_render (machine {self.nm})")

    engine = TA.Fw.engine


# --- 1. start-up ----------------------------------------------------------------------------------------------
def boot(fw, tg=None):
    """Le crochet de démarrage, exécuté pour de vrai : seul, au début de la remise à zéro du BSS (0x400004b2, jusqu'à
    son retour) ; avec Model-TG, par le jsr de 0x40000530, jusqu'à boot_extra_hook de Model-TG."""
    uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
    uc.ctl_set_cpu_model(mk.UC_CPU_M68K_ANY)
    uc.mem_map(0x40000000, 0x02400000)
    uc.mem_write(BASE, fw.img)
    dst, rt = fw.payload
    uc.mem_map(dst, 0x00100000)
    uc.mem_write(dst, b"\xa5" * 0x00100000)              # ce que la SDRAM contient avant : n'importe quoi
    uc.mem_map(0x80000000, 0x00010000)
    sram = bytearray(0x10000)                             # SRAM après son initialisation par l'OS (0x4000045c)
    for lo, hi, d in gs.CY_SRAM_INIT:
        sram[d - 0x80000000:d - 0x80000000 + hi - lo] = fw.img[lo - BASE:hi - BASE]
    uc.mem_write(0x80000000, bytes(sram))
    uc.mem_map(0x90000000, 0x00020000)
    uc.mem_write(E.STOP, b"\x4e\x71\x4e\x71")
    bad = []
    uc.hook_add(UC_HOOK_MEM_UNMAPPED, lambda u, a, addr, s, v, d: bad.append(addr) or False)
    keep = {r: 0x11110000 + i for i, r in enumerate((mk.UC_M68K_REG_D2, mk.UC_M68K_REG_D3, mk.UC_M68K_REG_D4,
                                                     mk.UC_M68K_REG_D5, mk.UC_M68K_REG_D6, mk.UC_M68K_REG_D7,
                                                     mk.UC_M68K_REG_A2, mk.UC_M68K_REG_A3, mk.UC_M68K_REG_A4,
                                                     mk.UC_M68K_REG_A5, mk.UC_M68K_REG_A6))}
    for r, v in keep.items():
        uc.reg_write(r, v)
    sp0 = 0x90010000
    if tg:
        uc.reg_write(mk.UC_M68K_REG_A7, sp0)
        uc.emu_start(gs.BOOT_CALL, tg["boot_extra_hook"], count=5_000_000)
        pc, sp = uc.reg_read(mk.UC_M68K_REG_PC), uc.reg_read(mk.UC_M68K_REG_A7)
        ret = struct.unpack(">I", uc.mem_read(sp, 4))[0]
        regs = all(uc.reg_read(r) == v for r, v in keep.items())
        check(pc == tg["boot_extra_hook"] and sp == sp0 - 4 and ret == gs.BOOT_CALL + 6 and regs and not bad,
              f"jsr 0x40000530 -> notre crochet -> boot_extra_hook {tg['boot_extra_hook']:#x}, pile et d2..d7/a2..a6 "
              "intactes")
        check(bytes(uc.mem_read(BASE, len(fw.img))) == fw.img, "image (OS, Model-TG, nos morceaux) inchangée")
    else:
        uc.mem_write(sp0, struct.pack(">I", E.STOP))
        uc.reg_write(mk.UC_M68K_REG_A7, sp0)
        uc.emu_start(0x400004b2, E.STOP, count=100_000_000)
        regs = all(uc.reg_read(r) == v for r, v in keep.items())
        tail = BASE + E.IMAGE_LEN
        cleared = not any(uc.mem_read(tail, len(fw.img) - E.IMAGE_LEN)) and not any(uc.mem_read(0x42338000, 0xb0))
        check(uc.reg_read(mk.UC_M68K_REG_PC) == E.STOP and uc.reg_read(mk.UC_M68K_REG_A7) == sp0 + 4 and regs
              and cleared and not bad,
              "0x400004b2 -> notre crochet -> remise à zéro du BSS de l'OS (nos morceaux rangés compris), retour "
              "normal, d2..d7/a2..a6 intacts")
    got = bytes(uc.mem_read(dst, len(rt)))
    packed = sum(n for _, n in fw.mine["append"]["pack"])
    check(got == rt, f"charge utile reconstituée à {dst:#x} : {len(rt)} o (code et tables de MAITS, détours, données, "
                     f"puis ses variables à zéro), rangée en {packed} o dans l'image")
    check(bytes(uc.mem_read(0x80000000, 0x10000)) == bytes(sram), "SRAM intacte")
    end = BASE + len(fw.img)
    check(end <= gs.END_LIMIT, f"image décompressée jusqu'à {end:#x} (limite {gs.END_LIMIT:#x})")


# --- 2. interface ---------------------------------------------------------------------------------------------
def interface_alone(stock, fw):
    """The checks of test_syntakt_machines.py, for one added machine: MAITS."""
    tsm.SAFE = True
    tsm.ADDED = [dict(gmt.MACHINE, code="maits", index=6, label="MAITS")]
    tsm.N, tsm.NM, tsm.TOP = 1, 7, 6
    tsm.FIRST, tsm.REC = {6: 76}, {6: gs.RECS}
    tsm.ROWS, tsm.CCROWS = gs.ROWSN, gs.CCROWSN
    tsm.NAMES = ["Kick", "Snare", "Metal", "Perc", "Tone", "Chord", gmt.MACHINE["name"]]
    patched, payload = fw.img, fw.runtime
    a, b = tsm.tables(stock, patched, payload)
    tsm.lookups(a, b)
    tsm.accessors(a, b, stock, patched)
    tsm.screens(stock, patched, payload)
    tsm.setter_and_wheel(stock, patched, payload)
    tsm.records_and_change(stock, patched, payload)
    tsm.out_of_range(stock, patched, payload)
    tsm.small_icon(stock, patched, payload)


# --- 3. the bridge ----------------------------------------------------------------------------------------------
def spec(pmod, vb, pb):
    """What maits_update must hand the engines (notes/74 par. 3), from the voice and the parameters."""
    v32 = lambda o: struct.unpack_from(">i", vb, o)[0]
    p16 = lambda o: struct.unpack_from(">h", pb, o)[0]
    dial = lambda x: min(max(x, 0), 0x7f00)
    n = min(max(pmod, 0), 127 << 16) + (p16(0x14) << 8) + ((p16(0x22) - 0x4000) << 3) - (64 << 16)
    n >>= 8
    eng = min(dial(p16(0x1c)) >> 8, 9)
    return dict(note=min(max(n, 0), 143 * 256),
                dials=(dial(p16(0x16)), dial(p16(0x18)), dial(p16(0x1a)), (eng * 32768 + 16384) // 10),
                trig=int(v32(0x38) != 0), init=v32(0x2c) != MARK, engine=eng)


def halfband(x):
    """32 samples from 86 (22 of history, then the block), as maits.c and MACRO."""
    out = []
    for i in range(32):
        y = x[2 * i:2 * i + 23]
        acc = int(y[11]) << 14
        for k, c in enumerate(HB):
            acc += c * (int(y[10 - 2 * k]) + int(y[12 + 2 * k]))
        out.append(((acc + 2 ** 31) % 2 ** 32) - 2 ** 31)
    return out


class Probe:
    """At each block: what maits_update reads (voice, parameters, note), what mi_fill receives (dials, note,
    trig), the engine's block (mi_obuf when mi_fill returns), and what the bridge hands the amp chain (the entry
    of AMP_ENV called from the payload); the amp chain's reads of the voice (reads)."""

    def __init__(self, fw, e, tracks=(0,), reads=False):
        self.tracks, self.block = tuple(tracks), 0
        self.recs = {t: [] for t in self.tracks}
        self.hist = {t: [0] * 22 for t in self.tracks}
        self.out_at, self.reads, self.bad = {}, [] if reads else None, []
        uc, s = e.uc, fw.sym
        arg = lambda k: struct.unpack(">I", uc.mem_read(uc.reg_read(mk.UC_M68K_REG_A7) + 4 * k, 4))[0]
        r32 = lambda a: struct.unpack(">i", uc.mem_read(a, 4))[0]
        track = lambda v: (v - E.VOICE0) // E.VSTRIDE if (v - E.VOICE0) % E.VSTRIDE == 0 else -1
        self.fill_ret = None

        def upd(uc_, a, s_, u):
            t = track(arg(2))
            if t not in self.tracks:
                return
            r = spec(struct.unpack(">i", struct.pack(">I", arg(1)))[0], bytes(uc.mem_read(arg(2), E.VSTRIDE)),
                     bytes(uc.mem_read(arg(3), 0x40)))
            r["block"] = self.block
            vb = bytes(uc.mem_read(arg(2), E.VSTRIDE))
            quiet = abs(struct.unpack_from(">i", vb, 0x230)[0]) < IDLE_LEVEL \
                and abs(struct.unpack_from(">i", vb, 0x234)[0]) < IDLE_LEVEL
            r["quiet_in"] = quiet
            if r["init"]:
                self.hist[t] = [0] * 22
            self.recs[t].append(r)

        def rnd(uc_, a, s_, u):
            t = track(arg(2))
            if t not in self.tracks:
                return
            self.out_at[t] = arg(1)
            r = self.recs[t][-1]
            vb = bytes(uc.mem_read(arg(2), E.VSTRIDE))
            v32 = lambda o: struct.unpack_from(">i", vb, o)[0]
            pending = uc.mem_read(s["mi_trig"] + t, 1)[0]
            quiet = abs(v32(0x230)) < IDLE_LEVEL and abs(v32(0x234)) < IDLE_LEVEL
            r["render"] = int(not (not v32(0x34) and not v32(0x38) and not pending and quiet))

        def fill(uc_, a, s_, u):
            t = arg(1)
            if t not in self.tracks:
                return
            r = self.recs[t][-1]
            dials = struct.unpack(">4i", uc.mem_read(s["mi_p"] + 16 * t, 16))
            got = dict(note=struct.unpack(">i", struct.pack(">I", arg(2)))[0], dials=dials,
                       trig=uc.mem_read(s["mi_trig"] + t, 1)[0])
            r["fill"] = got
            self.fill_ret = t

        def after(uc_, a, s_, u):                   # mi_out: the block is written
            if self.fill_ret is not None:
                self.recs[self.fill_ret][-1]["obuf"] = np.frombuffer(bytes(uc.mem_read(s["mi_obuf"], 256)),
                                                                     ">i4").copy()
                self.fill_ret = None

        def amp(uc_, a, s_, u):
            t = track(arg(1))
            if fw.pay <= arg(0) < fw.code_end and t in self.tracks:
                r = self.recs[t][-1]
                r["done"] = 1
                r["out"] = np.frombuffer(bytes(uc.mem_read(self.out_at[t], 128)), ">i4").astype(np.int64)
        uc.hook_add(UC_HOOK_CODE, upd, begin=fw.upd, end=fw.upd)
        uc.hook_add(UC_HOOK_CODE, rnd, begin=fw.rnd, end=fw.rnd)
        uc.hook_add(UC_HOOK_CODE, fill, begin=s["mi_fill"], end=s["mi_fill"])
        uc.hook_add(UC_HOOK_CODE, after, begin=s["mi_out"], end=s["mi_out"])
        uc.hook_add(UC_HOOK_CODE, amp, begin=AMP[0][0], end=AMP[0][0])
        if reads:
            v0 = E.VOICE0 + self.tracks[0] * E.VSTRIDE
            uc.hook_add(UC_HOOK_MEM_READ, self._read, begin=v0, end=v0 + E.VSTRIDE - 1, user_data=v0)

    def _read(self, uc, access, addr, size, value, v0):
        pc = uc.reg_read(mk.UC_M68K_REG_PC)
        if any(lo <= pc < hi for lo, hi in AMP):
            self.reads.append((self.block, addr - v0, bytes(uc.mem_read(addr, size))))

    def verify(self):
        """[] if everything is as specified; else what is not."""
        bad = []
        for t in self.tracks:
            trig = 0
            for r in self.recs[t]:
                trig |= r["trig"]
                want_render = r.get("render", 0)
                if r.get("done", 0) != want_render:
                    bad.append(f"track {t + 1} block {r['block']}: computed {r.get('done', 0)}, expected "
                               f"{want_render}")
                    break
                if not want_render:
                    continue
                f = r.get("fill")
                if f is None or f["note"] != r["note"] or tuple(f["dials"]) != r["dials"] or f["trig"] != trig:
                    bad.append(f"track {t + 1} block {r['block']}: mi_fill got {f}, expected note {r['note']} "
                               f"dials {r['dials']} trig {trig}")
                    break
                trig = 0
                x = self.hist[t] + [int(v) >> 15 for v in r["obuf"]]
                x = [((v + 2 ** 15) % 2 ** 16) - 2 ** 15 for v in x]     # the s16 store of maits.c
                want = halfband(x)
                self.hist[t] = x[64:]
                if list(r["out"]) != want:
                    bad.append(f"track {t + 1} block {r['block']}: the 48 kHz block is not the half-band filter "
                               "of the engine's")
                    break
        return bad


def play(fw, kw, blocks, trigs=(1,), changes=None, track=0, rels=(), reads=False, idx=None, probe=True):
    """One track on MAITS (or idx), the others off. changes: {block: settings}; rels: blocks with a note end."""
    e = fw.engine(solo=track)
    e.set(track, machine=fw.index if idx is None else idx, **kw)
    pr = Probe(fw, e, (track,), reads) if (fw.payload and (probe or reads)) else None
    out = []
    for b in range(blocks):
        for k, v in (changes or {}).get(b, {}).items():
            e.set(track, **{k: v})
        if pr:
            pr.block = b
        out.append(e.block(1 << track if b in trigs else 0, 1 << track if b in rels else 0)[track])
    return np.stack(out), pr, e.unmapped


def eng_kw(k, **kw):
    """Settings with CONTOUR on engine k (its dial value: k, as the descriptor's range 0..9 gives it)."""
    return dict(KW, contour=k, **kw)


def cases(quick):
    out = []
    for k in range(10):
        out.append((f"{ENGINES[k]}", eng_kw(k), dict(trigs=(1, 70), blocks=120)))
    if not quick:
        for k in range(10):
            out.append((f"{ENGINES[k]}, dials at 0", eng_kw(k, color=0, shape=0, sweep=0, note=36),
                        dict(blocks=60)))
            out.append((f"{ENGINES[k]}, dials at 127", eng_kw(k, color=127, shape=127, sweep=127, note=84),
                        dict(blocks=60)))
    out += [
        ("PITCH 0, note 0: held at the bottom", eng_kw(0, pitch=0, note=0), dict(blocks=40)),
        ("PITCH 127, note 127: held at the top", eng_kw(9, pitch=127, note=127), dict(blocks=40)),
        ("FINE 0 and 127", eng_kw(1, finetune=0), dict(blocks=40, changes={20: dict(finetune=127)})),
        ("dials moving", eng_kw(3), dict(blocks=100, changes={b: dict(color=b, shape=127 - b, sweep=(3 * b) % 128)
                                                             for b in range(0, 100, 5)})),
        ("engine change while playing", eng_kw(0), dict(blocks=120, trigs=(1, 60),
                                                        changes={30: dict(contour=5), 60: dict(contour=9),
                                                                 90: dict(contour=2)})),
        ("CONTOUR above 9: engine 9", eng_kw(9), dict(blocks=40, changes={10: dict(contour=127)})),
        ("GATE: the note ends", eng_kw(7, gate=1, decay=60), dict(blocks=150, rels=(40,))),
        ("a voice that falls silent, then plays again", eng_kw(9, decay=10), dict(blocks=400, trigs=(1, 350))),
    ]
    return out


def exact(fw, todo, label):
    bad, peak = [], 0
    for name, kw, extra in todo:
        x, pr, unm = play(fw, kw, **extra)
        b = pr.verify()
        if unm:
            b.append("access outside memory")
        if b:
            bad.append(f"{name}: {b[0]}")
        peak = max(peak, int(np.abs(x).max()))
    check(not bad, f"{label}: the engines get the dials, note and trig of notes/74, and the 48 kHz block is the "
                   f"half-band filter of theirs, exactly, every block ({len(todo)} cases, peak {peak / 2 ** 31:.2f} "
                   f"of full scale){': ' + bad[0] if bad else ''}")


def amp_like_tone(fw):
    """The amp chain (envelope, VCA, PUNCH) reads in the MAITS voice exactly what it reads in a TONE voice, block
    by block, while the MAITS voice is computed."""
    res = []
    for kw in (dict(decay=30), dict(decay=90), dict(decay=60, gate=1), dict(decay=127, gate=1)):
        k = eng_kw(1, **kw)
        rels = (60,) if kw.get("gate") else ()
        _, pt, _ = play(fw, k, 120, (1, 40), rels=rels, reads=True, idx=TONE)
        _, pm, unm = play(fw, k, 120, (1, 40), rels=rels, reads=True)
        stop = next((r["block"] for r in pm.recs[0] if not r.get("render")), 120)
        rt = [x for x in pt.reads if x[0] < stop]
        rm = [x for x in pm.reads if x[0] < stop]
        res.append((rt == rm and len(rm) > 0 and not unm, stop))
    check(all(ok for ok, _ in res), f"amp chain: the same reads of the voice as TONE (DECAY, GATE), block by block "
                                    f"until silence (blocks {[s for _, s in res]})")


# --- 4. the engines ---------------------------------------------------------------------------------------------
def peak_hz(x, lo, hi, fs=48000):
    y = (x - x.mean()) * np.hanning(len(x))
    s = np.abs(np.fft.rfft(y, 1 << 18))
    f = np.fft.rfftfreq(1 << 18, 1 / fs)
    m = (f > lo) & (f < hi)
    k = np.flatnonzero(m)[np.argmax(s[m])]
    a, b, c = np.log(s[k - 1:k + 2] + 1e-9)
    return float(f[k] + (a - c) / (2 * (a - 2 * b + c)) * (f[1] - f[0]))


def engines(fw, quick):
    """Final output of the track (after the amp chain), DECAY at 127: every engine sounds, differently; the
    pitched ones at their note; a change of engine starts it from rest; all within full scale."""
    rows, ok, outs = [], True, []
    for k in range(10):
        kw = eng_kw(k, decay=127)
        if k == 8:                                         # PART: dense dust (TIMB 64 is a few grains a second)
            kw.update(shape=127)
        x, _, unm = play(fw, kw, 200, (1,), probe=False)
        y = x[4:].ravel().astype(float)
        outs.append(y)
        r = np.sqrt(np.mean(y ** 2))
        ok &= r > 2 ** 31 * 1e-3 and not unm
        rows.append(f"{ENGINES[k]} {20 * np.log10(r / 2 ** 31):.0f} dB")
    same = [(ENGINES[a], ENGINES[b]) for a in range(10) for b in range(a + 1, 10)
            if np.corrcoef(outs[a], outs[b])[0, 1] > 0.99]
    check(ok and not same, f"the ten engines sound (RMS {', '.join(rows)}), all different {same[:1]}")
    devs = []
    for k in PITCHED:
        for n in ((36, 60, 84) if not quick else (60,)):
            kw = eng_kw(k, note=n, decay=127)
            if k == 5:                                     # CHIP: the chord's root, no arpeggio
                kw.update(color=0, shape=0)
            if k == 1:                                     # FM: ratio 1, little index
                kw.update(color=64, shape=20, sweep=64)
            x, _, _ = play(fw, kw, 300, (1,), probe=False)
            hz = 440 * 2 ** ((n - 69) / 12)
            got = peak_hz(x[20:].ravel().astype(float), hz * 0.8, hz * 1.25)
            devs.append((ENGINES[k], n, 1200 * np.log2(got / hz)))
    worst = max(devs, key=lambda d: abs(d[2]))
    check(abs(worst[2]) < 10, f"pitched engines (WSHP, FM, PD, CHIP, MODL, STRG) at their note: within "
                              f"{abs(worst[2]):.1f} cents ({worst[0]} at note {worst[1]})")
    # a change of engine starts the new one from rest: the same as a fresh voice on it
    a, _, _ = play(fw, eng_kw(9, decay=127), 120, (1, 60), changes={60: dict(contour=7)}, probe=False)
    b, _, _ = play(fw, eng_kw(7, decay=127), 60, (1,), probe=False)
    check(np.array_equal(a[60:].ravel()[:32 * 40], b[1:41].ravel()) or
          np.corrcoef(a[61:100].ravel(), b[1:40].ravel())[0, 1] > 0.999,
          "a change of engine (STRG -> MODL) on the next trig: the same note as a fresh MODL voice")
    rng = np.random.default_rng(7)
    top, unm_any = 0, False
    for _ in range(20 if not quick else 6):
        kw = eng_kw(int(rng.integers(0, 10)), color=int(rng.integers(0, 128)), shape=int(rng.integers(0, 128)),
                    sweep=int(rng.integers(0, 128)), note=int(rng.integers(0, 128)), decay=int(rng.integers(0, 128)))
        x, _, unm = play(fw, kw, 40, (1, 20), probe=False)
        top, unm_any = max(top, int(np.abs(x).max())), unm_any or unm
    check(top < 2 ** 31 and not unm_any, f"random dials, notes and engines: within full scale (peak "
                                         f"{top / 2 ** 31:.2f}), no access outside memory")


# --- 5. with the OS around it -------------------------------------------------------------------------------------
def stock_unchanged(ref_fw, fw, label):
    """6 tracks on the original machines (2 trigs): identical, and no MAITS code runs."""
    def run(f, hook):
        e = f.engine()
        for t in range(6):
            e.set(t, **dict(KW, machine=t, color=64, shape=64, contour=64, decay=60))
        ran = []
        if hook:
            e.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: ran.append(a), begin=f.pay, end=f.code_end - 1)
        x = np.stack([e.block(0x3f if b in (1, 150) else 0) for b in range(300)])
        return x, ran, e.unmapped
    r, _, _ = run(ref_fw, False)
    x, ran, unm = run(fw, True)
    check(np.array_equal(r, x) and np.abs(x).max() > 1e7 and not ran and not unm,
          f"6 original machines: identical to {label}, sample for sample, without a single MAITS instruction")


def like(alone, fw, label):
    bad = []
    for kw in (eng_kw(0), eng_kw(5, note=48), eng_kw(9, gate=1, decay=100), eng_kw(6, color=100)):
        a, _, _ = play(alone, kw, 200, (1, 120), probe=False)
        b, _, unm = play(fw, kw, 200, (1, 120), probe=False)
        if not np.array_equal(a, b) or unm or not a.any():
            bad.append(kw["contour"])
    check(not bad, f"{label}, final output of the track (WSHP, CHIP, STRG with GATE, NOIS) {bad[:1]}")


def locks(fw, label):
    """Machine locks on one track: SNARE, MAITS, TONE, MAITS (another engine), KICK: everything plays."""
    seq = [dict(machine=1), dict(machine=fw.index, contour=0), dict(machine=TONE),
           dict(machine=fw.index, contour=9), dict(machine=0)]
    e = fw.engine(solo=0)
    e.set(0, **dict(KW, machine=1, decay=40))
    out = []
    for b in range(60 * len(seq)):
        if b % 60 == 0:
            e.set(0, **seq[b // 60])
        out.append(e.block(1 if b % 60 == 1 else 0)[0])
    x = np.stack(out)
    parts = [int(np.abs(x[60 * k + 2:60 * k + 60]).max()) for k in range(len(seq))]
    check(all(p > 1e6 for p in parts) and not e.unmapped,
          f"{label}: machine locks SNARE -> MAITS (WSHP) -> TONE -> MAITS (STRG) -> KICK on one track: everything "
          f"plays (peaks {[f'{p:.2g}' for p in parts]})")


def six_tracks(fw, label):
    kws = [eng_kw((2 * t + 1) % 10, note=36 + 7 * t, color=20 * t, shape=127 - 15 * t) for t in range(6)]
    e = fw.engine()
    for t, kw in enumerate(kws):
        e.set(t, **dict(kw, machine=fw.index))
    x = np.stack([e.block(0x3f if b in (1, 100) else 0) for b in range(160)])
    ok = not e.unmapped
    for t, kw in enumerate(kws):
        y, _, unm = play(fw, kw, 160, (1, 100), track=t, probe=False)
        ok &= np.array_equal(x[:, t], y) and not unm and y.any()
    check(ok, f"{label}: 6 MAITS tracks together (six engines), each identical to the same track played alone")


def mixed(fw, ref_fw, label, sampler=False):
    trigs = {1: 0x3f, 150: 0x3f}
    mach = [0, fw.index, 6 if sampler else 2, 5, fw.index, TONE]
    ours = {1: eng_kw(4, decay=40), 4: eng_kw(8, note=72, decay=40)}
    setup = {t: dict(ours.get(t, dict(KW, color=64, shape=64, decay=40)), machine=m) for t, m in enumerate(mach)}
    e = fw.engine()
    for t, kw in setup.items():
        e.set(t, **kw)
    x = np.stack([e.block(trigs.get(b, 0)) for b in range(300)])
    unm = e.unmapped
    er = ref_fw.engine()
    for t, kw in setup.items():
        er.set(t, **dict(kw, machine=0 if t in ours else kw["machine"]))
    r = np.stack([er.block(trigs.get(b, 0)) for b in range(300)])
    ok = not unm and not er.unmapped
    for t in range(6):
        if t in ours:
            y, _, unm_s = play(fw, ours[t], 300, (1, 150), track=t, probe=False)
            ok &= np.array_equal(x[:, t], y) and y.any() and not unm_s
        else:
            ok &= np.array_equal(x[:, t], r[:, t])
    check(ok, f"{label}: tracks {[m + 1 for m in mach]} together; original machines"
              f"{', Sampler without a sample' if sampler else ''} identical without MAITS, each MAITS track "
              "identical to the same track played alone")


# --- 6. cost ------------------------------------------------------------------------------------------------------
def cost(fw, label):
    """Instructions per block of a voice (the whole voice loop, minus the same loop without a voice), stack."""
    def run(idx, kw, blocks, trigs=(1,)):
        e = fw.engine(solo=0)
        e.set(0, machine=idx, **kw)
        if idx == OFF:
            e.uc.mem_write(E.VOICE0, struct.pack(">II", OFF, OFF))
        n = {"i": 0, "sp": 1 << 32}

        def hk(uc, a, s, u):
            n["i"] += 1
            if fw.pay <= a < fw.code_end:
                n["sp"] = min(n["sp"], uc.reg_read(mk.UC_M68K_REG_A7))
        e.uc.hook_add(UC_HOOK_CODE, hk)
        per = []
        for b in range(blocks):
            i0 = n["i"]
            e.block(1 if b in trigs else 0)
            per.append(n["i"] - i0)
        return np.array(per), n["sp"]
    base, _ = run(OFF, KW, 4, trigs=())
    zero = int(np.median(base))
    tone, _ = run(TONE, dict(KW, color=40, shape=38, sweep=52, contour=42, decay=42), 24)
    rows, sps = [], []
    for k in range(10):
        x, sp = run(fw.index, eng_kw(k, decay=127), 24)
        rows.append(f"{ENGINES[k]} {int(np.median(x[4:])) - zero}")
        sps.append(sp)
    idle, _ = run(fw.index, eng_kw(0, decay=0), 200)
    deep = E.STACK - 0x100 - min(sps)
    check(True, f"{label}: cost of a MAITS voice, instructions per block: {', '.join(rows)}; TONE "
                f"{int(np.median(tone[4:])) - zero}; silent voice {int(np.median(idle[150:])) - zero}; stack {deep} "
                "bytes under the voice loop")


# --- 7. with other mods ---------------------------------------------------------------------------------------------
def with_others(stock, cycles, ids, alone, tg_fw, tg):
    by_id = {t["id"]: t for t in (load(f.name) for f in sorted(DEV.glob("[0-9]*.json")))}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise SystemExit(f"!! unknown tweaks: {unknown}")
    others = [by_id[i] for i in ids]
    with_tg = any(t["id"].startswith("model-tg") for t in others)
    mine = by_id["maits-tg" if with_tg else "maits"]
    clash = [t["id"] for t in others if mine["id"] in t.get("conflicts", []) or t["id"] in mine["conflicts"]]
    if clash:
        raise SystemExit(f"!! not compatible with {mine['id']}: {clash}")
    fw = Fw(stock, sorted(others + [mine], key=lambda t: t["order"]))
    ref_fw = Fw(stock, sorted(others, key=lambda t: t["order"]))
    label = ", ".join(ids)
    print(f"\n== {mine['id']} with {label}: MAITS as machine {fw.index + 1}")
    print("start-up")
    t7.X.bootstrap_depack_ok(cycles, fw.tweaks, None) or t7.FAIL.append("bootstrap with " + label)
    boot(fw, tg if with_tg else None)
    print("sound")
    stock_unchanged(ref_fw, fw, "these mods without MAITS")
    like(tg_fw if with_tg else alone, fw, f"MAITS with {label} = MAITS {'with Model-TG alone' if with_tg else 'alone'}")
    locks(fw, f"MAITS with {label}")
    mixed(fw, ref_fw, f"MAITS with {label}", sampler=with_tg)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", required=True, help="official model-cycles_OS1.13.syx")
    ap.add_argument("--quick", action="store_true", help="fewer cases")
    ap.add_argument("--with", dest="others", default="", help="ids of other tweaks: MAITS with them (section 7)")
    args = ap.parse_args()
    stock = T.main_os_from_syx(args.cycles)
    check((gmt.SRC / "mi_tables.inc").read_text(encoding="utf-8") == gmt.tables_inc(),
          "mi_tables.inc: the generator's tables (tools/gen_maits.py)")
    mt, tg_tw, mt_tg = load("27-maits.json"), load("30-model-tg-st.json"), load("36-maits-tg.json")
    off = Fw(stock, [])
    alone = Fw(stock, [mt])
    todo = cases(args.quick)

    print(f"== {mt['id']}: MAITS as machine {alone.index + 1}")
    print("start-up")
    t7.X.bootstrap_depack_ok(args.cycles, mt, None) or t7.FAIL.append("bootstrap")
    boot(alone)
    print("interface")
    interface_alone(stock, alone)
    print("sound")
    exact(alone, todo, "MAITS alone")
    amp_like_tone(alone)
    engines(alone, args.quick)
    stock_unchanged(off, alone, "the original OS")
    locks(alone, "MAITS alone")
    six_tracks(alone, "MAITS alone")
    mixed(alone, off, "MAITS alone")
    print("cost")
    cost(alone, "MAITS alone")

    gs.set_base(gs.PAY_TG)
    gs.CATALOG["maits"] = gmt.MACHINE
    fw, ref_tg = Fw(stock, [tg_tw, mt_tg]), Fw(stock, [tg_tw])
    tg = {k: int(v, 16) for k, v in tg_tw["symbols"].items()}
    tg["knob_vec"] = tms.knob_vec_at(mt_tg)
    print(f"\n== {mt_tg['id']}: with Model-TG, MAITS as machine {fw.index + 1}")
    print("start-up")
    t7.X.bootstrap_depack_ok(args.cycles, [tg_tw, mt_tg], None) or t7.FAIL.append("bootstrap with Model-TG")
    boot(fw, tg)
    print("interface")
    tms.interface(ref_tg, fw, ["maits"], tg)
    print("sound")
    exact(fw, todo if not args.quick else todo[:3] + todo[-4:], "MAITS with Model-TG")
    stock_unchanged(ref_tg, fw, "Model-TG alone")
    like(alone, fw, "MAITS with Model-TG = MAITS alone")
    locks(fw, "MAITS with Model-TG")
    six_tracks(fw, "MAITS with Model-TG")
    mixed(fw, ref_tg, "MAITS with Model-TG", sampler=True)
    print("cost")
    cost(fw, "MAITS with Model-TG")
    if args.others:
        with_others(stock, args.cycles, args.others.split(","), alone, fw, tg)
    fail = t7.FAIL + tms.FAIL
    print("\nTOUT OK" if not fail else f"\n{len(fail)} ÉCHEC(S)")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
