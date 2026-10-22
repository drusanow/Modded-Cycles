#!/usr/bin/env python3
"""Writes tweaks/model-cycles_OS1.13/70-overbridge-exp.json: an EXPERIMENTAL Overbridge mode for the Model:Cycles
(notes/75). Not in the OS (os.json): a standalone build to try with Overwitch, the free Overbridge host for Linux.

The USB AUDIO/MIDI mode becomes an Overbridge 2 device (vendor interfaces, interrupt endpoints 0x83 / 0x03 at high
speed, blocks of 7 frames), with 8 channels each way: to the host Main L/R and the 6 tracks, from the host 8
channels of which 1 and 2 play into the main output. USB MIDI stays. The other USB modes (USB MIDI, and CDC for
CONFIG > UPGRADE) are unchanged.

tools/machines/overbridge/ob.S, one section per freed 48x22 sprite mask (tools/sprites.py); its blocks, FIFO and
state in the free hole above the OS's BSS (0x4233a000..0x4233d0ff, notes/56 par. 3.5.2). The USB descriptors are
built here. Every write carries its stock bytes, checked on the official MAIN OS.

    python3 tools/gen_overbridge.py --cycles model-cycles_OS1.13.syx [--check] [--overwitch device.json]
"""
import argparse
import json
import pathlib
import struct
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "emu"))

import build                       # noqa: E402
import gen_sdvintage_exact as gx   # noqa: E402
import sprites                     # noqa: E402
import test_sdvintage as T         # noqa: E402

SRC = HERE / "machines" / "overbridge"
DEV = HERE.parent / "tweaks" / "model-cycles_OS1.13"
OUT = DEV / "70-overbridge-exp.json"
BASE = gx.BASE
PID = 0x7ff0                       # a product ID of our own (Overwitch: devices.d/model-cycles.json)
RAM = (0x4233a000, 0x4233d100)     # blocks, FIFO, state (ob.S)
# freed 48x22 masks (192 bytes), free in REGISTRY.md: one section of ob.S each
MASKS = {f".ob{i + 1}": a for i, a in enumerate((0x40152d38, 0x401592bc, 0x4015943c, 0x4015f38c, 0x40165fec,
                                                 0x40168a84, 0x40168da4, 0x4016bcd8, 0x4016dab8))}
ROOM = 192
TOKEN = 0x01000080              # a dTD of 256 bytes, active (ob.S)

MODE5 = 0x4013e5d8                 # USB mode table (0x4013e538, 0x28 bytes an entry), entry 4: USB AUDIO/MIDI
DEVDESC = 0x4019b3fe               # its device descriptor (PID 0x001b)
CONFIG = 0x4019b2b6                # its configuration descriptor (328 bytes)
CONFIG_LEN = 0x148

CH_OUT = ["Main L", "Main R"] + [f"Track {t}" for t in range(1, 7)]
CH_IN = [f"Input {c}" for c in range(1, 9)]
BLOCK = 4 + 28 + 7 * 4 * len(CH_OUT)
assert BLOCK == 256 and len(CH_IN) == len(CH_OUT)


def config_descriptor():
    """The configuration: an Overbridge 2 function (interfaces 0..3) and USB MIDI (4, 5), as the Syntakt lays it out
    (notes/75 par. 2), with 8 channels each way."""
    d = []

    def iface(n, alt, neps, cls, sub=0, proto=0):
        d.append(bytes([9, 4, n, alt, neps, cls, sub, proto, 0]))

    def ep(addr, attr, size, interval, audio=False):
        d.append(bytes([9 if audio else 7, 5, addr, attr]) + struct.pack("<H", size) + bytes([interval])
                 + (b"\0\0" if audio else b""))

    d.append(bytes([8, 0x0b, 0, 4, 0xff, 0, 0, 0]))          # IAD: Overbridge, interfaces 0..3
    iface(0, 0, 0, 0xff)                                    # control
    for n, addr, sizes in ((1, 0x83, (32, 88, BLOCK)), (2, 0x03, (32, 88, BLOCK))):
        iface(n, 0, 0, 0xff)
        for alt, size in enumerate(sizes, 1):              # Overwitch takes alt 3, and its packet size
            iface(n, alt, 1, 0xff)
            ep(addr, 0x03, size, 1)                        # interrupt, every micro-frame
    iface(3, 0, 0, 0xff)                                    # (the plugin's pipe: not provided)
    d.append(bytes([8, 0x0b, 4, 2, 1, 1, 0, 0]))            # IAD: audio / MIDI, interfaces 4, 5
    iface(4, 0, 0, 1, 1)                                    # audio control
    d.append(bytes([9, 0x24, 1, 0, 1, 9, 0, 1, 5]))         # AC header 1.00, one collection: interface 5
    iface(5, 0, 2, 1, 3)                                    # MIDI streaming
    jacks = [bytes([6, 0x24, 2, 1, 1, 0]), bytes([6, 0x24, 2, 2, 2, 0]),           # IN jacks: embedded 1, external 2
             bytes([9, 0x24, 3, 1, 3, 1, 2, 1, 0]), bytes([9, 0x24, 3, 2, 4, 1, 1, 1, 0])]   # OUT 3 <- 2, 4 <- 1
    cs_eps = [bytes([5, 0x25, 1, 1, 1]), bytes([5, 0x25, 1, 1, 3])]
    ms_total = 7 + sum(map(len, jacks)) + 2 * 9 + sum(map(len, cs_eps))
    d.append(bytes([7, 0x24, 1, 0, 1]) + struct.pack("<H", ms_total))
    d += jacks
    ep(0x01, 0x02, 512, 0, audio=True)
    d.append(cs_eps[0])
    ep(0x81, 0x02, 512, 0, audio=True)
    d.append(cs_eps[1])
    body = b"".join(d)
    total = 9 + len(body)
    return bytes([9, 2]) + struct.pack("<H", total) + bytes([6, 1, 0, 0xc0, 0]) + body


def compile_ob():
    with tempfile.TemporaryDirectory() as t:
        t = pathlib.Path(t)
        obj, elf, ld = t / "ob.o", t / "ob.elf", t / "ob.ld"
        ld.write_text("SECTIONS {\n" + "".join(f"  {s} {a:#x} : {{ *({s}) }}\n" for s, a in MASKS.items())
                      + "  /DISCARD/ : { *(.text) *(.data) *(.bss) }\n}\n")
        gx.run([gx.CROSS + "gcc", "-mcpu=54418", "-c", str(SRC / "ob.S"), "-o", str(obj)])
        gx.run([gx.CROSS + "ld", "-T", str(ld), "--no-warn-rwx-segments", "-o", str(elf), str(obj)])
        syms = {}
        for line in gx.run([gx.CROSS + "nm", str(elf)]).splitlines():
            p = line.split()
            if len(p) == 3 and p[2].startswith("ob_"):
                syms[p[2]] = int(p[0], 16)
        secs = {}
        for s in MASKS:
            out = t / (s.strip(".") + ".bin")
            gx.run([gx.CROSS + "objcopy", "-O", "binary", "-j", s, str(elf), str(out)])
            secs[s] = out.read_bytes()
    return secs, syms


def jsr(target):
    return struct.pack(">HI", 0x4eb9, target).hex()


def build_tweak(stock):
    secs, syms = compile_ob()
    at = lambda va, n: stock[va - BASE:va - BASE + n]
    writes, used = [], {}
    for s, mask in MASKS.items():
        code = secs[s]
        if len(code) > ROOM:
            raise SystemExit(f"!! {s}: {len(code)} bytes, room {ROOM}")
        kept = sprites.kept_of(mask)
        if at(mask, ROOM) != at(kept, ROOM):
            raise SystemExit(f"!! {s}: mask {mask:#x} differs from its kept copy {kept:#x}")
        writes.append({"off": mask - BASE, "old": at(mask, len(code)).hex(), "new": code.hex()})
        writes.append(sprites.redirect_write(mask))
        used[s] = len(code)
    cfg = config_descriptor()
    hooks = [
        (MODE5, "40002ca840002c56", struct.pack(">II", syms["ob_act"], syms["ob_deact"]).hex(),
         "USB mode 5 (USB AUDIO/MIDI): its activation and its end"),
        (MODE5 + 8, "4019b3fe4019b2b600000148", struct.pack(">III", DEVDESC, CONFIG, len(cfg)).hex(),
         "USB mode 5: the same descriptors' places, the configuration's new length"),
        (DEVDESC, at(DEVDESC, 18).hex(), (at(DEVDESC, 10) + struct.pack("<H", PID) + at(DEVDESC + 12, 6)).hex(),
         "device descriptor: our product ID"),
        (CONFIG, at(CONFIG, len(cfg)).hex(), cfg.hex(), "configuration: Overbridge 2 + USB MIDI"),
        (0x400047ea, "123c0002", "123c0001", "SET_INTERFACE in mode 5: interface 1 is the stream to the host"),
        (0x40004818, "7201", "7202", "SET_INTERFACE in mode 5: interface 2 is the stream from the host"),
        (0x40004806, jsr(0x40002836), jsr(syms["ob_in_stop"]), "interface 1, alt 0"),
        (0x40004810, jsr(0x40002778), jsr(syms["ob_in_start"]), "interface 1, alt > 0"),
        (0x40004844, jsr(0x400028d2), jsr(syms["ob_out_stop"]), "interface 2, alt 0"),
        (0x4000484e, jsr(0x40002862), jsr(syms["ob_out_start"]), "interface 2, alt > 0"),
        (0x4000453c, "0c80a10100006600043e6000037e", struct.pack(">HI", 0x4ef9, syms["ob_vendor"]).hex() + "4e71" * 4,
         "control requests: cmpi #0xa1010000 ; bne stall ; bra 0x400048c6 -> jmp ob_vendor (+ Overwitch's reads)"),
        (0x40059338, jsr(0x40002ae0), jsr(syms["ob_read"]), "render: the host's audio for this block"),
        (0x40059392, jsr(0x40002912), jsr(syms["ob_write"]), "render: the block to the host"),
        (0x400593ce, jsr(0x40002912), jsr(syms["ob_write"]), "render: the silent block (sound cut), not sent"),
    ]
    for va, old, new, _ in hooks:
        if at(va, len(old) // 2).hex() != old:
            raise SystemExit(f"!! unexpected stock bytes at {va:#x}")
        writes.append({"off": va - BASE, "old": old, "new": new})
    writes.sort(key=lambda w: w["off"])
    mine = {(w["off"], len(w["new"]) // 2) for w in writes}
    conflicts = []
    for p in sorted(DEV.glob("*.json")):
        if p.name in ("device.json", OUT.name):
            continue
        t = json.loads(p.read_text(encoding="utf-8"))
        if any(o < w["off"] + len(w["new"]) // 2 and w["off"] < o + n for w in t.get("writes", []) for o, n in mine):
            conflicts.append(t["id"])
    tweak = {
        "id": "overbridge-exp",
        "order": 70,
        "name": "EXPÉRIENCE : mode Overbridge (pour Overwitch)",
        "description": [
            "Expérience, hors de l'OS (notes/75). Le mode USB AUDIO/MIDI devient un appareil Overbridge 2 (interfaces",
            "vendor, points d'accès interrupt 0x83 / 0x03 en haute vitesse, blocs de 7 trames), 8 canaux dans chaque",
            "sens : vers l'hôte Main L/R et les 6 pistes, depuis l'hôte 8 canaux dont 1 et 2 dans la sortie",
            f"principale. PID {PID:#06x} ; l'hôte est Overwitch (Linux), avec le fichier d'appareil de",
            "tools/gen_overbridge.py --overwitch. Le MIDI USB reste ; les autres modes USB ne changent pas.",
            f"Code dans neuf masques de sprites libérés ({sum(used.values())} o), blocs, FIFO et état en "
            f"{RAM[0]:#x}..{RAM[1]:#x}, au-dessus du BSS. Généré par tools/gen_overbridge.py.",
        ],
        "device": "Model:Cycles",
        "os": "1.13",
        "section": 3,
        "conflicts": conflicts,
        "symbols": {k: f"{v:#x}" for k, v in sorted(syms.items())},
        "writes": writes,
    }
    return tweak, used, cfg


def overwitch_device():
    return {"pid": PID, "name": "Model:Cycles", "version": 2,
            "input_tracks": [{"name": n, "size": 4} for n in CH_IN],
            "output_tracks": [{"name": n, "size": 4} for n in CH_OUT]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", required=True, help="official model-cycles_OS1.13.syx")
    ap.add_argument("--check", action="store_true", help="checks that the committed JSON is up to date")
    ap.add_argument("--overwitch", help="also write Overwitch's device file here")
    args = ap.parse_args()
    stock = T.main_os_from_syx(args.cycles)
    if build.sha(stock) != json.loads((DEV / "device.json").read_text(encoding="utf-8"))["section_sha256"]:
        raise SystemExit("!! not the official MAIN OS 1.13")
    tweak, used, cfg = build_tweak(stock)
    build.apply_writes(stock, [tweak])
    print("  " + ", ".join(f"{s} {n}/{ROOM}" for s, n in used.items()) + f"; configuration {len(cfg)} bytes")
    if args.overwitch:
        pathlib.Path(args.overwitch).write_text(json.dumps(overwitch_device(), indent=2) + "\n", encoding="utf-8")
        print(f"  written: {args.overwitch}")
    text = json.dumps(tweak, indent=1) + "\n"
    if args.check:
        ok = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print(f"  {OUT.name} {'is up to date' if ok else 'DOES NOT MATCH'}")
        raise SystemExit(0 if ok else 1)
    OUT.write_text(text, encoding="utf-8")
    print(f"  written: {OUT.relative_to(HERE.parent)} ({len(tweak['writes'])} writes, conflicts {tweak['conflicts']})")


if __name__ == "__main__":
    main()
