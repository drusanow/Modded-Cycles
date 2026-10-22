#!/usr/bin/env python3
"""Generates the tweaks of the MAITS machine: ten macro engines after Mutable Instruments Plaits, as an added
machine of the Model:Cycles (notes/74).

  - 27-maits.json: 7th machine, after the 6 original ones;
  - 36-maits-tg.json: with Model-TG, 8th machine after its Sampler; goes after model-tg-st (notes/31).

The sound is our code (tools/machines/maits/: engine.s, the ten engines, and maits.c, which connects them to the
voice loop), fixed-point remakes of the algorithms of Plaits by Emilie Gillet (MIT licence: LICENSE-Plaits, copied
next to the flasher), ported from DavidCaruso's Model-TG Plaits Edition. Some tables (mi_tables.inc) are computed
here from Plaits' formulas. Of the OS, the tweak carries only the short original bytes ("old") that check the
official file; the descriptor table is copied at build time from YOUR file ("cycles" recipe, as for MACRO).

The mechanics are MACRO's and ACID's (gen_macro.added_machine, notes/43, notes/54): the same detours, the same moved
tables, the same place in the payload. Not yet combined with MACRO, ACID and the Syntakt engines: that needs the
eighth added machine of gen_syntakt_engines.py (notes/74 par. 6), so MAITS is not in the OS (os.json) yet.

    python3 tools/gen_maits.py --cycles model-cycles_OS1.13.syx [--check]

The versioned JSON are built by m68k-linux-gnu-gcc 13.3 (Ubuntu 24.04): another GCC gives other bytes, and --check
says so (mi_tables.inc and LICENSE-Plaits do not depend on the compiler).
"""
import argparse
import json
import math
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "emu"))

import gen_acid as ga              # noqa: E402
import gen_macro as gm             # noqa: E402
import gen_sdvintage_exact as gx   # noqa: E402
import gen_sdvintage_7th as g7     # noqa: E402
import gen_syntakt_engines as gs   # noqa: E402
import usb_steady                  # noqa: E402
import voice_loop                  # noqa: E402

DEV = HERE.parent / "tweaks" / "model-cycles_OS1.13"
OUT = DEV / "27-maits.json"
OUT_TG = DEV / "36-maits-tg.json"
LICENSE_OUT = DEV / "LICENSE-Plaits"
SRC = HERE / "machines" / "maits"
TABLES = SRC / "mi_tables.inc"

CFLAGS = ga.CFLAGS
LINK = """SECTIONS
{{
  .text {code:#x} : {{ *(.text.maits_update) *(.text.maits_render) *(.text*) }}
  .rodata ALIGN(4) : {{ *(.rodata*) }}
  .data ALIGN(4) : {{ *(.data*) }}
  .bss {bss:#x} (NOLOAD) : {{ *(.bss*) *(COMMON) }}
  /DISCARD/ : {{ *(.comment) *(.note*) *(.eh_frame*) }}
}}
"""
BSS_OFF = gm.BSS_OFF               # as MACRO and ACID: variables above the moved tables (gs.LAYOUT)

# --- the machine (as gm.MACHINE): COLOR, SHAPE, SWEEP, CONTOUR = (long name, short name, default[, min, max]);
# decay: DECAY's default; image: TONE's (the OS has 6 only), whose amp chain MAITS takes
NENG = 10
MACHINE = dict(name="MAITS", image=4, decay=60,
               knobs=(("Harmonic", "HARM", 64), ("Timbre", "TIMB", 64), ("Morph", "MORP", 64),
                      ("Engine", "ENG", 0, 0, NENG - 1)))

PLAITS = "pichenettes/eurorack"
FS = 96000.0                       # the engines' rate (mi_fill: 64 samples a block)


def tables_inc():
    """mi_tables.inc: the waveshaper, folder, FM-ratio and chord tables computed with the formulas of Plaits
    (plaits/resources/lookup_tables.py, chord_bank.cc; Copyright 2016 Emilie Gillet, MIT), rescaled to integers;
    the sine and pitch tables are ours. Same values as the Plaits Edition's tools/gen_mi_tables.py.
      mi_sin    256 longs: sin(2 pi k/256) x 32767 in the high word, the step to the next in the low word
      mi_semi   the increment (Q32 at 96 kHz) of MIDI notes 0..12
      mi_ws     Plaits' five waveshapers (inverse tan, inverse sin, linear, bump, double bump) and a sentinel
                copy of the last, 257 words each, Q15
      mi_fold   Plaits' wavefolder (lut_fold + 1), 514 words, Q15
      mi_fmq    Plaits' FM ratio quantizer (lut_fm_frequency_quantizer), as ratios, 130 words, Q12
      mi_e2     2^(k/12) for k = 0..12, Q14
      mi_chord  Plaits' eleven chords, four ratios each, Q12; mi_chn: how many notes the arpeggiator walks
    """
    sv = [round(32767 * math.sin(2 * math.pi * k / 256)) for k in range(257)]
    sin_l = [((sv[k] & 0xffff) << 16) | ((sv[k + 1] - sv[k]) & 0xffff) for k in range(256)]
    n = 128
    xs = [i / n for i in range(n + 1)]
    tanv = [math.atan(8 * math.cos(math.pi * x)) for x in xs]
    scale = max(tanv)
    linear = xs
    bump = [(1 - math.cos(math.pi * x * 1.5)) * (1 - math.cos(math.pi * min(1.0, 4 - 4 * x))) / 4.5 for x in xs]
    double_bump = [math.sin(math.pi * x * 1.5) for x in xs]
    inverse_sin = [math.acos(1 - 2 * x) / math.pi for x in xs]
    inverse_tan = [math.acos(math.tan(scale * (1 - 2 * x)) / 8) / math.pi for x in xs]

    def flip(v):
        w = [-a for a in v[n:0:-1]] + list(v)
        return [round(a * 32767) for a in w]
    ws = [flip(t) for t in (inverse_tan, inverse_sin, linear, bump, double_bump, double_bump)]
    assert all(len(t) == 257 for t in ws)
    m = 512
    fx = [i / (m / 2) - 1 for i in range(m + 4)]
    fx[-1] = fx[-2]
    fold = []
    for x in fx:
        w = math.exp(-x * x * 4) ** 2
        fold.append(math.sin(8 * math.pi * x) * w + math.atan(3 * x) * (1 - w))
    fm_ = max(abs(a) for a in fold)
    fold = [round(32767 * a / fm_) for a in fold][1:515]
    assert len(fold) == 514
    ratios = [0.5, 0.5 * 2 ** (16 / 1200), math.sqrt(2) / 2, math.pi / 4, 1.0, 2 ** (16 / 1200),
              math.sqrt(2), math.pi / 2, 7 / 4, 2, 2 * 2 ** (16 / 1200), 9 / 4, 11 / 4,
              2 * math.sqrt(2), 3, math.pi, math.sqrt(3) * 2, 4, math.sqrt(2) * 3,
              math.pi * 3 / 2, 5, math.sqrt(2) * 4, 8]
    sc = []
    for r in ratios:
        s = 12 * math.log2(r)
        sc += [s, s, s]
    target = 2 ** math.ceil(math.log2(len(sc)))
    while len(sc) < target:
        d = [b - a for a, b in zip(sc, sc[1:])]
        g = d.index(max(d))
        sc = sc[:g + 1] + [(sc[g] + sc[g + 1]) / 2] + sc[g + 1:]
    sc += [sc[-1], sc[-1]]
    fmq = [round(4096 * 2 ** (s / 12)) for s in sc]
    assert len(fmq) == 130 and max(fmq) <= 32768
    semi = [round(440 * 2 ** ((k - 69) / 12) / FS * 2 ** 32) for k in range(13)]
    e2 = [round(16384 * 2 ** (k / 12)) for k in range(13)]
    chords = [(0, .01, 11.99, 12), (0, 7, 7.01, 12), (0, 5, 7, 12), (0, 3, 7, 12), (0, 3, 7, 10),
              (0, 3, 10, 14), (0, 3, 10, 17), (0, 2, 9, 16), (0, 4, 11, 14), (0, 4, 7, 11),
              (0, 4, 7, 12)]
    chq = [round(4096 * 2 ** (s / 12)) for c in chords for s in c]
    chn = [sum(1 for s in c if s not in (.01, 7.01, 11.99, 12)) for c in chords]
    assert all(1 <= k <= 4 for k in chn)

    def rows(vals, per, d=".long"):
        return [f"    {d} " + ", ".join(map(str, vals[i:i + per])) for i in range(0, len(vals), per)]
    out = ["",
           "| mi_tables.inc - generated by tools/gen_maits.py (notes/74). Do not edit.",
           "| Waveshaper, folder, FM-ratio and chord tables: formulas of Mutable Instruments",
           "| Plaits, (c) 2016 Emilie Gillet, MIT licence (LICENSE-Plaits).",
           "    .align 2",
           "mi_sin:                   | sin x 32767 << 16 | step to the next"]
    out += rows([f"0x{v:08x}" for v in sin_l], 8)
    out.append("mi_semi:                  | MIDI notes 0..12: increment, Q32 at 96 kHz")
    out += rows(semi, 7)
    out.append("mi_ws:                    | Plaits' waveshapers, 6 x 257, Q15")
    for t in ws:
        out += rows(t, 16, ".word")
    out.append("mi_fold:                  | Plaits' wavefolder (lut_fold + 1), Q15")
    out += rows(fold, 16, ".word")
    out.append("mi_fmq:                   | Plaits' FM ratio quantizer, ratios Q12")
    out += rows(fmq, 16, ".word")
    out.append("mi_e2:                    | 2^(k/12), Q14")
    out += rows(e2, 13, ".word")
    out.append("mi_chord:                 | Plaits' chords: four ratios each, Q12")
    out += rows(chq, 4, ".word")
    out.append("mi_chn:                   | notes the arpeggiator walks, per chord")
    out += rows(chn, 11, ".byte")
    out += ["    .align 2", ""]
    return "\n".join(out)


LICENSE = """Machine MAITS of Modded-Cycles: fixed-point code written after the algorithms of Plaits by Emilie Gillet
(https://github.com/{repo}, plaits/dsp/engine: its waveshaping, FM, grain, phase distortion, chiptune, noise,
modal, particle, swarm and string engines; tables computed from plaits/resources/lookup_tables.py and the chords of
chord_bank.cc), under the MIT license:

Copyright 2016 Emilie Gillet.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
""".format(repo=PLAITS)


def compile_machine(tmp, pay, tables, bss_at=None, code_end=None):
    """maits.c and engine.s linked at pay (code, tables) and bss_at (variables; pay + BSS_OFF by default). Code
    and tables must end before code_end (gs.STUBS by default). Returns (bytes, symbols, end of the BSS)."""
    (tmp / "mi_tables.inc").write_text(tables)            # this generator's tables, not the repository's
    for f in ("maits.c", "engine.s"):
        (tmp / f).write_bytes((SRC / f).read_bytes())
    bss_at = pay + BSS_OFF if bss_at is None else bss_at
    code_end = gs.STUBS if code_end is None else code_end
    oc, oe = tmp / "maits.o", tmp / "engine.o"
    gx.run([gx.CROSS + "gcc", *CFLAGS, "-c", str(tmp / "maits.c"), "-o", str(oc)])
    gx.run([gx.CROSS + "gcc", "-mcpu=54418", f"-Wa,-I{tmp}", "-c", str(tmp / "engine.s"), "-o", str(oe)])
    ld, elf, out = tmp / "link.ld", tmp / "maits.elf", tmp / "maits.bin"
    ld.write_text(LINK.format(code=pay, bss=bss_at))
    gx.run([gx.CROSS + "ld", "-T", str(ld), "--gc-sections", "--no-warn-rwx-segments", "-e", "maits_update",
            "-u", "maits_render", "-o", str(elf), str(oc), str(oe)])
    secs = {}
    for line in gx.run([gx.CROSS + "objdump", "-h", str(elf)]).splitlines():
        f = line.split()
        if len(f) > 4 and f[0].isdigit():
            secs[f[1]] = (int(f[3], 16), int(f[2], 16))
    if set(secs) - {".text", ".rodata", ".data", ".bss"}:
        raise SystemExit(f"!! unexpected sections: {sorted(secs)}")
    if secs.get(".data", (0, 0))[1]:
        raise SystemExit("!! initialised variables: the start-up hook would not copy them separately")
    gx.run([gx.CROSS + "objcopy", "-O", "binary", "-R", ".bss", str(elf), str(out)])
    blob = out.read_bytes()
    syms = {p[2]: int(p[0], 16) for p in (l.split() for l in gx.run([gx.CROSS + "nm", str(elf)]).splitlines())
            if len(p) == 3}
    if pay + len(blob) > code_end:
        raise SystemExit(f"!! code too large: {len(blob)} bytes")
    bss = secs.get(".bss", (bss_at, 0))
    if bss[0] != bss_at:
        raise SystemExit("!! BSS")
    return blob, syms, (bss[0] + bss[1] + 3) & ~3


def other_ids():
    """Tweaks that add machines, or the same block after the image: never with MAITS."""
    import gen_acid_combos as gac
    return ga.other_ids() | gac.all_ids(True) | {"acid", "acid-tg"}


def build_tweak(img, tg=None):
    """Tweak of the MAITS machine. tg: Model-TG (gs.tg_context) for the combined version."""
    pay = gs.PAY_TG if tg else gs.PAY_ALONE
    gs.set_base(pay)
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        blob, syms, bss_end = compile_machine(tmp, pay, tables_inc())
        writes, append = gm.added_machine(img, tg, [dict(MACHINE, update=syms["maits_update"],
                                                         render=syms["maits_render"])], tmp, [(pay, blob)], bss_end)
    tid = "maits-tg" if tg else "maits"
    ids = other_ids() | {"maits", "maits-tg", "model-tg"} | (set() if tg else {"model-tg-st"})
    out = {
        "id": tid,
        "order": 36 if tg else 27,
        "name": ("Model-TG + " if tg else "") + "Machine MAITS : dix moteurs d'après Plaits",
        "description": ([
            "Version combinée avec Model-TG (notes/31, notes/74) : s'ajoute après model-tg-st, le Sampler reste la",
            "7e machine, MAITS est la 8e. Sa sortie passe par l'étage d'amplitude de Model-TG (Attack, filtre).",
        ] if tg else [
            "Machine MAITS en 7e machine, après les 6 d'origine (notes/74).",
        ]) + [
            "COLOR = HARMONICS, SHAPE = TIMBRE, SWEEP = MORPH, CONTOUR = le moteur (0..9) : WSHP, FM, SWRM, GRAN, PD,",
            "CHIP, NOIS, MODL, PART, STRG. Algorithmes de Plaits (Emilie Gillet, MIT : LICENSE-Plaits) réécrits en",
            "virgule fixe (tools/machines/maits/, d'après la Plaits Edition de DavidCaruso). Chaîne d'ampli de TONE.",
            "Une voix muette n'est pas calculée. Généré par tools/gen_maits.py. De l'OS, seuls les octets d'origine (old)",
            "qui vérifient le fichier officiel ; la table des descripteurs est recopiée au build (recette cycles).",
        ],
        "device": "Model:Cycles",
        "os": "1.13",
        "section": 3,
    }
    if tg:
        out["requires"] = [tg["id"]]
    out.update({"conflicts": sorted(ids - {tid}), "writes": writes, "append": append})
    # for the proof (tools/emu/test_maits.py): the entry points and the engines' state
    out["symbols"] = {k: f"{syms[k]:#x}" for k in ("maits_update", "maits_render", "mi_fill", "mi_out", "mi_p",
                                                   "mi_eng", "mi_trig", "mi_obuf", "mi_seed", "mi_seeds", "trk")}
    if not tg:                    # as MACRO and ACID alone: USB sent at a fixed time (notes/35), voice loop
        out = usb_steady.add_to(out)          # (notes/36); the combined version has them through model-tg-st
        out = voice_loop.add_to(out)
    segs = sum(n for _, n in append["pack"])
    return out, dict(code=len(blob), bss=bss_end - pay - BSS_OFF, image=segs, syms=syms)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", help="official model-cycles_OS1.13.syx (without it: mi_tables.inc and the licence)")
    ap.add_argument("--check", action="store_true", help="check that the versioned files match")
    args = ap.parse_args()
    bad = gm.emit(TABLES, tables_inc(), args.check)
    bad += gm.emit(LICENSE_OUT, LICENSE, args.check)
    if args.cycles:
        img = g7.cycles_main(args.cycles)
        if len(img) != gx.IMAGE_LEN:
            raise SystemExit("!! this is not the official MAIN OS 1.13")
        for path, tg in ((OUT, None), (OUT_TG, gs.tg_context(img))):
            tweak, info = build_tweak(img, tg)
            text = json.dumps(tweak, indent=1) + "\n"
            print(f"  {tweak['id']}: {len(tweak['writes'])} writes, code and tables {info['code']} bytes, variables "
                  f"{info['bss']} bytes, added to the image {info['image']} bytes")
            bad += gm.emit(path, text, args.check)
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
