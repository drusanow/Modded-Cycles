#!/usr/bin/env python3
"""Proof of the EXPERIMENTAL Overbridge mode (70-overbridge-exp.json, tools/machines/overbridge/ob.S, notes/75).

The patched MAIN OS runs in Unicorn: the OS's own control-request dispatcher (0x4000448c) and mode handling, and our
code. The USB controller is modelled in Python: a dTD queued on an endpoint (0x40003f94, intercepted) joins that
endpoint's queue; a simulated host, doing what Overwitch does (notes/75 par. 3), reads one block from the head of
the IN queue per micro-frame (125 us), writes one block into the head of the OUT queue per micro-frame when it has
one, and clears the token's Active bit as the controller would. The audio side is called as the render interrupt
calls it, once per block of 32 frames (0.667 ms).

  1. the writes: the configuration Overwitch reads (interfaces 1 and 2, alt 3: interrupt endpoints 0x83 / 0x03,
     256 bytes, every micro-frame; interfaces 4 and 5: USB MIDI; total length) and the product ID;
  2. control requests through the OS's dispatcher: Overwitch's name and version reads, the stock requests still
     going where they went (class request 0xA1 0x01, unknown request -> stall), GET_DESCRIPTOR returning ours;
  3. SET_INTERFACE in mode 5: interface 1 starts and stops the stream to the host, interface 2 the one from it;
  4. activation: endpoint 3 programmed as interrupt both ways, the queue heads;
  5. to the host: every frame of every block (Main L/R, tracks 1..6) arrives once, in order, in blocks of 7 with
     the counter, through host stalls (blocks dropped while the host does not read, never rewritten while queued);
  6. from the host: channels 1 and 2 play into the main input buffer, every frame once, in order, after the
     prefill, with a host that sends in bursts (24 blocks, as Overwitch) at a slightly different clock, a host that
     always has data (flow control by NAK), and a host that stops (silence, then playing again);
  7. no stream: silence in, nothing sent (as the stock OS with no host).

    python3 tools/emu/test_overbridge.py --cycles model-cycles_OS1.13.syx
"""
import argparse
import json
import pathlib
import random
import struct
import sys

from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_UNMAPPED
from unicorn import m68k_const as mk

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import build                        # noqa: E402
import gen_overbridge as G          # noqa: E402
import test_sdvintage as T          # noqa: E402

BASE = build.BASE
STOP = 0x90000000
STACK = 0x9f000000
PRIME = 0x40003f94
SEND0 = 0x40004438                  # (length, data) on EP0
ACK0 = 0x4000444a                   # zero-length status on EP0
STALL = 0x400049a6
MIX, TRACKS, INBUF = 0x40fe4b90, 0x80001858, 0x40fe4cf0
FAIL = []


def check(ok, msg):
    print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
    if not ok:
        FAIL.append(msg)


class Os:
    def __init__(self, img, syms, intercept=True):
        self.s = syms
        uc = self.uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(mk.UC_CPU_M68K_ANY)
        uc.reg_write(mk.UC_M68K_REG_SR, 0x2700)
        uc.mem_map(0x40000000, 0x02400000)                    # image, BSS, the hole above it
        uc.mem_write(BASE, img)
        uc.mem_map(0x4a338000, 0x8000)                        # the hole's uncached alias (blocks)
        uc.mem_map(0x80000000, 0x10000)                       # SRAM
        uc.mem_map(0x90000000, 0x10000000)
        uc.mem_map(0xfc000000, 0x100000)                      # registers
        uc.mem_write(STOP, b"\x4e\x71\x4e\x71")
        self.bad = []
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, lambda u, a, addr, s, v, d: self.bad.append(addr) or False)
        # ENDPTFLUSH (0xfc0b01b4) and ENDPTPRIME: the controller is done at once (the OS waits on their bits)
        uc.hook_add(UC_HOOK_MEM_READ, lambda u, a, addr, s, v, d: u.mem_write(addr & ~3, bytes(4)),
                    begin=0xfc0b01b0, end=0xfc0b01b7)
        self.q = {6: [], 7: [], 4: []}
        self.sent0, self.acks, self.stalls, self.hit = [], 0, 0, set()
        if intercept:
            uc.hook_add(UC_HOOK_CODE, self._prime, begin=PRIME, end=PRIME)
        uc.hook_add(UC_HOOK_CODE, self._send0, begin=SEND0, end=SEND0)
        uc.hook_add(UC_HOOK_CODE, self._ack0, begin=ACK0, end=ACK0)
        uc.hook_add(UC_HOOK_CODE, self._stall, begin=STALL, end=STALL)
        for a in (0x400048c6, 0x40002836, 0x40002778, 0x400028d2, 0x40002862, 0x40002ae0, 0x40002912):
            uc.hook_add(UC_HOOK_CODE, lambda u, addr, s, d: self.hit.add(addr), begin=a, end=a)

    def w32(self, a, v):
        self.uc.mem_write(a, struct.pack(">I", v & 0xffffffff))

    def r32(self, a):
        return struct.unpack(">I", self.uc.mem_read(a, 4))[0]

    def _ret(self, d0=0):
        sp = self.uc.reg_read(mk.UC_M68K_REG_A7)
        self.uc.reg_write(mk.UC_M68K_REG_D0, d0)
        self.uc.reg_write(mk.UC_M68K_REG_PC, struct.unpack(">I", self.uc.mem_read(sp, 4))[0])
        self.uc.reg_write(mk.UC_M68K_REG_A7, sp + 4)

    def _prime(self, uc, addr, size, ud):
        sp = uc.reg_read(mk.UC_M68K_REG_A7)
        ep, dtd = struct.unpack(">II", uc.mem_read(sp + 4, 8))
        q = self.q.setdefault(ep, [])
        if dtd in q:
            FAIL.append(f"dTD {dtd:#x} queued twice on endpoint {ep}")
        q.append(dtd)
        self.snap = getattr(self, "snap", {})
        if ep == 7:                                           # what the block holds when queued
            buf = self.r32(dtd + 8)
            self.snap[dtd] = bytes(uc.mem_read(buf, 256))
        self._ret()

    def _send0(self, uc, addr, size, ud):
        sp = uc.reg_read(mk.UC_M68K_REG_A7)
        n, data = struct.unpack(">II", uc.mem_read(sp + 4, 8))
        self.sent0.append(bytes(uc.mem_read(data, n)))
        self._ret()

    def _ack0(self, uc, addr, size, ud):
        self.acks += 1
        self._ret()

    def _stall(self, uc, addr, size, ud):
        self.stalls += 1

    def call(self, fn, *args):
        sp = STACK - 0x400
        self.uc.mem_write(sp, struct.pack(">I" + "I" * len(args), STOP, *[x & 0xffffffff for x in args]))
        self.uc.reg_write(mk.UC_M68K_REG_A7, sp)
        for r in range(2, 8):                                 # callee-saved: must come back unchanged
            self.uc.reg_write(getattr(mk, f"UC_M68K_REG_D{r}"), 0x1110 + r)
        self.uc.emu_start(fn, STOP, count=2_000_000)
        if self.uc.reg_read(mk.UC_M68K_REG_A7) != sp + 4:
            FAIL.append(f"stack unbalanced after {fn:#x}")
        if any(self.uc.reg_read(getattr(mk, f"UC_M68K_REG_D{r}")) != 0x1110 + r for r in range(2, 8)):
            FAIL.append(f"d2-d7 not kept by {fn:#x}")
        return self.uc.reg_read(mk.UC_M68K_REG_D0)

    def setup(self, rtype, req, value=0, index=0, length=0):
        self.uc.mem_write(0x422ff12c, struct.pack("<BBHHH", rtype, req, value, index, length))   # USB: little-endian
        self.call(0x4000448c)

    # the controller: a block read from / written into the head of a queue (bytes left 0, Active cleared)
    def host_in(self):
        q = self.q[7]
        if not q:
            return None
        d = q.pop(0)
        buf = self.r32(d + 8)
        data = bytes(self.uc.mem_read(buf, 256))
        if self.snap.get(d) is not None and data[:4] != self.snap[d][:4]:
            FAIL.append("a queued block was rewritten before the host read it")
        tok = self.r32(d + 4)
        self.w32(d + 4, tok & ~0x7fff0080)
        return data

    def host_out(self, block):
        q = self.q[6]
        if not q:
            return False                                      # NAK: nothing queued, the host waits
        d = q.pop(0)
        if not self.r32(d + 4) & 0x80:
            FAIL.append("an OUT dTD queued but not active")
        self.uc.mem_write(self.r32(d + 8), block)
        self.w32(d + 4, self.r32(d + 4) & ~0x7fff0080)
        return True


def descriptors(img):
    print("descriptors")
    at = lambda va, n: img[va - BASE:va - BASE + n]
    dev = at(G.DEVDESC, 18)
    check(dev[:2] == b"\x12\x01" and struct.unpack("<HH", dev[8:12]) == (0x1935, G.PID),
          f"device descriptor: Elektron's vendor ID, product ID {G.PID:#06x}")
    n = struct.unpack(">I", at(G.MODE5 + 16, 4))[0]
    cfg = at(G.CONFIG, n)
    total = struct.unpack("<H", cfg[2:4])[0]
    i, ifs, eps, cur = 0, {}, {}, None
    while i < len(cfg):
        L, t = cfg[i], cfg[i + 1]
        if t == 4:
            cur = (cfg[i + 2], cfg[i + 3])
            ifs[cur] = cfg[i + 5:i + 8]
        if t == 5:
            eps[cur] = (cfg[i + 2], cfg[i + 3], struct.unpack("<H", cfg[i + 4:i + 6])[0], cfg[i + 6])
        i += L
    check(i == total == n and cfg[4] == 6, f"configuration: {n} bytes, its walk ends at its total length, 6 interfaces")
    check(eps.get((1, 3)) == (0x83, 3, 256, 1) and eps.get((2, 3)) == (0x03, 3, 256, 1),
          "interface 1 alt 3: EP 0x83 interrupt 256 bytes every micro-frame; interface 2 alt 3: EP 0x03 the same")
    check(all(ifs[(k, 0)][0] == 0xff for k in range(4)) and ifs[(4, 0)][:2] == b"\x01\x01"
          and ifs[(5, 0)][:2] == b"\x01\x03" and eps.get((5, 0), (0,))[0] == 0x81,
          "interfaces 0..3 vendor (Overbridge), 4 audio control, 5 MIDI streaming (EP 0x01 / 0x81)")
    blk = (eps[(1, 3)][2] - 32) // 28
    check(blk == len(G.CH_OUT) == len(G.overwitch_device()["output_tracks"]),
          f"block size = 32 + 7 x 4 x {blk} channels, as the Overwitch device file says")


def control(os_):
    print("control requests (the OS's dispatcher)")
    os_.w32(0x404a09c4, G.MODE5)
    os_.w32(0x404a09c8, 5)
    os_.sent0.clear()
    os_.setup(0xc0, 1, 0, 0, 32)
    os_.setup(0xc0, 1, 0, 0, 8)
    os_.setup(0xc0, 2, 0, 0, 16)
    check(os_.sent0 == [b"Model:Cycles".ljust(32, b"\0"), b"Model:Cy", b"0001        0.01"],
          f"Overwitch's reads: name (32 bytes, or wLength), version (16) {os_.sent0[:1]}")
    os_.hit.clear()
    os_.setup(0xa1, 1, 0, 0, 4)
    check(0x400048c6 in os_.hit, "class request 0xA1 0x01: to the stock code, as before")
    st = os_.stalls
    os_.setup(0xc0, 5, 0, 0, 4)
    os_.setup(0x40, 1, 0, 0, 32)
    check(os_.stalls == st + 2, "other vendor requests (0xC0 5, 0x40 1): stalled, as before")
    os_.sent0.clear()
    os_.setup(0x80, 6, 0x0100, 0, 18)
    os_.setup(0x80, 6, 0x0200, 0, 512)
    n = struct.unpack(">I", os_.uc.mem_read(G.MODE5 + 16, 4))[0]
    check(len(os_.sent0) == 2 and os_.sent0[0][10:12] == struct.pack("<H", G.PID) and len(os_.sent0[1]) == n,
          f"GET_DESCRIPTOR: our device descriptor, our configuration ({n} bytes)")


def interfaces(os_):
    print("SET_INTERFACE (mode 5)")
    s = os_.s
    on = lambda: (os_.r32(s["IN_ON"]), os_.r32(s["OUT_ON"]))
    os_.hit.clear()
    os_.q[6].clear()
    os_.setup(0x01, 0x0b, 3, 1)
    a = on()
    os_.setup(0x01, 0x0b, 3, 2)
    b = on()
    queued = len(os_.q[6])
    os_.setup(0x01, 0x0b, 0, 1)
    c = on()
    os_.setup(0x01, 0x0b, 0, 2)
    d = on()
    check((a, b, c, d) == ((1, 0), (1, 1), (0, 1), (0, 0)) and queued == 6,
          f"interface 1: the stream to the host on/off; interface 2: from the host on/off, 6 dTD queued {a, b, c, d}")
    check(not os_.hit & {0x40002836, 0x40002778, 0x400028d2, 0x40002862} and os_.acks >= 4,
          "the stock stream routines are no longer called; each request acknowledged")


def activation(os_):
    print("activation")
    os_.w32(0xfc0b01cc, 0)
    midi = []

    def stand_in(uc, addr, size, ud):                      # the stock activation's MIDI part (0x4000311c): its
        sp = uc.reg_read(mk.UC_M68K_REG_A7)                 # dTD pool is set up by the OS's USB start, which
        midi.append(struct.unpack(">I", uc.mem_read(sp + 4, 4))[0])   # this rig does not run
        os_._ret()
    h = os_.uc.hook_add(UC_HOOK_CODE, stand_in, begin=0x4000311c, end=0x4000311c)
    os_.call(struct.unpack(">I", os_.uc.mem_read(G.MODE5, 4))[0], 0x1234)
    os_.uc.hook_del(h)
    check(midi == [0x1234], "the stock MIDI activation still runs, with the activation's argument")
    check(os_.r32(0xfc0b01cc) == 0x00cc00cc, "ENDPTCTRL3 = 0x00cc00cc: EP3 in and out, interrupt, enabled")
    check(all(os_.r32(q) == 0x04000000 and os_.r32(q + 8) == 0xdead0001 for q in (0x800001c0, 0x80000180)),
          "queue heads of EP3 IN and OUT: max packet 1024, no dTD")


def mk_mix(os_, n):
    f0 = n * 32
    os_.uc.mem_write(MIX, b"".join(struct.pack(">ii", (f0 + i) << 4 | 0, -((f0 + i) << 4 | 1)) for i in range(32)))
    os_.uc.mem_write(TRACKS, b"".join(struct.pack(">i", (f0 + i) << 4 | 2 + t) for t in range(6) for i in range(32)))


def to_host(img, syms, stalls):
    """blocks of audio, the host reading one block per micro-frame except during stalls (micro-frame ranges)"""
    os_ = Os(img, syms)
    os_.w32(0x404a09c4, G.MODE5)
    os_.w32(0x404a09c8, 5)
    os_.call(syms["ob_in_start"], 3)
    got, uf, nblocks = [], 0, 3000
    for n in range(nblocks):
        mk_mix(os_, n)
        os_.call(syms["ob_write"], MIX, 32)
        os_.call(syms["ob_write"], 0x4a3ed080, 32)            # the silent call: nothing
        t_end = (n + 1) * 32 * 8000 / 48000
        while uf < t_end:
            if not any(a <= uf < b for a, b in stalls):
                p = os_.host_in()
                if p:
                    got.append(p)
            uf += 1
    return os_, got, nblocks


def verify_in(got, nblocks, label, allow_drops):
    frames, bad, last, drops = [], [], None, 0
    for p in got:
        if p[:2] != b"\x07\x00" or any(p[4:32]):
            bad.append("header")
        c = struct.unpack(">H", p[2:4])[0]
        if last is not None and c != (last + 7) & 0xffff:
            drops += ((c - last) & 0xffff) // 7 - 1
        last = c
        for k in range(7):
            v = struct.unpack(">8i", p[32 + 32 * k:64 + 32 * k])
            fr = v[0] >> 4
            want = (fr << 4, -(fr << 4 | 1)) + tuple(fr << 4 | 2 + t for t in range(6))
            if v != want:
                bad.append(f"frame {fr}")
            frames.append((c + k, fr))
    seq = [fr for _, fr in frames]
    inorder = all(b > a for a, b in zip(seq, seq[1:]))
    # the counter numbers the frames: frame f has counter f (mod 2^16), drops included
    counted = all((c & 0xffff) == (fr & 0xffff) for c, fr in frames)
    sent = nblocks * 32 // 7
    check(not bad and inorder and counted and (drops == 0 or allow_drops) and len(seq) + 7 * drops >= sent * 7 - 7,
          f"{label}: {len(got)} blocks, every frame (Main L/R, tracks 1..6) once and in order, the counter "
          f"follows them, {drops} blocks dropped {bad[:2]}")
    return drops


def from_host(img, syms, mode, seconds=2.0, ppm=50.0, seed=1):
    """the host sends blocks with numbered frames: 'burst' 24 blocks at a time at its own clock (+ppm), 'eager'
    whenever the device takes one, 'stop' bursts with a 0.2 s pause"""
    rnd = random.Random(seed)
    os_ = Os(img, syms)
    os_.w32(0x404a09c4, G.MODE5)
    os_.w32(0x404a09c8, 5)
    os_.call(syms["ob_out_start"], 3)
    nblocks = int(seconds * 1500)
    pending, made, cnt, out, uf = [], 0, 0, [], 0
    host_rate = 48000 * (1 + ppm * 1e-6)

    def block():
        nonlocal made, cnt
        b = struct.pack(">HH", 0x07ff, cnt & 0xffff) + bytes(28)
        for k in range(7):
            f = made + k
            b += struct.pack(">8i", *[f << 4 | ch for ch in range(8)])
        made += 7
        cnt += 7
        return b
    for n in range(nblocks):
        t_end = (n + 1) * 32 * 8000 / 48000
        while uf < t_end:
            t = uf / 8000
            if mode == "eager":
                if not pending:
                    pending.append(block())
            else:
                stopped = mode == "stop" and 0.8 <= t < 1.0
                # the host makes 24 blocks when its own clock says so (Overwitch's transfer), with jitter
                if not stopped and made < (t + rnd.uniform(0, 0.0005)) * host_rate + 168 and len(pending) < 24:
                    pending += [block() for _ in range(24)]
            if pending and os_.host_out(pending[0]):
                pending.pop(0)
            uf += 1
        os_.call(syms["ob_read"], INBUF, 32)
        out.append(struct.unpack(">64i", os_.uc.mem_read(INBUF, 256)))
    return os_, out


def verify_out(os_, out, label, expect_under):
    s = os_.s
    seq, bad = [], []
    for blk in out:
        for k in range(32):
            l, r = blk[2 * k], blk[2 * k + 1]
            if l == r == 0:
                seq.append(None)
            elif l & 15 == 0 and r == l | 1:
                seq.append(l >> 4)
            else:
                bad.append((l, r))
    played = [x for x in seq if x is not None]
    first = played[0] if played else None
    contiguous = all(b == a + 1 for a, b in zip(played, played[1:]))
    under = os_.r32(s["N_UNDER"])
    lead = seq.index(first) if played else -1
    check(not bad and played and first == 0 and (contiguous or expect_under) and (under > 0) == expect_under,
          f"{label}: channels 1, 2 played, {len(played)} frames from frame 0, "
          f"{'in order with no gap or repeat' if contiguous else 'gaps only where the host stopped'}, "
          f"after {lead} frames of prefill silence, underruns {under} {bad[:1]}")
    return played


def queue_real(img, syms):
    """the stream to the host with the OS's queue routine: our dTD chained on endpoint 7 in order, EP3 TX primed"""
    o = Os(img, syms, intercept=False)
    o.call(syms["ob_in_start"], 3)
    o.w32(0xfc0b01b0, 0)
    for n in range(3):                                      # 96 frames: 13 blocks, the ring wraps once
        mk_mix(o, n)
        o.call(syms["ob_write"], MIX, 32)
    first, last = o.r32(0x800001c0 + 48), o.r32(0x800001c0 + 52)
    chain, d = [], first
    while d and len(chain) < 20:
        chain.append(d)
        if d == last:
            break
        d = o.r32(d) & ~1
    want = [0x80008000 + 64 * (i % 8) for i in range(13)][:8]
    toks = [o.r32(d + 4) for d in chain]
    check(chain == want and last == chain[-1] and all(t == G.TOKEN for t in toks) and not o.bad,
          f"blocks chained on endpoint 7 in order ({len(chain)} dTD from {first:#x} to {last:#x}, each 256 bytes "
          "and active; the 5 more blocks, with no host reading, dropped), nothing outside memory")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", required=True, help="official model-cycles_OS1.13.syx")
    args = ap.parse_args()
    stock = T.main_os_from_syx(args.cycles)
    tw = json.loads(G.OUT.read_text(encoding="utf-8"))
    img, _ = build.apply_writes(stock, [tw])
    img = bytes(img)
    syms = {k: int(v, 16) for k, v in tw["symbols"].items()}
    st = G.RAM[0] + 0x3000
    for i, n in enumerate(("IN_ON", "IN_W", "IN_NF", "IN_CNT", "IN_SKIP", "OUT_ON", "OUT_R", "FIFO_W", "FIFO_R",
                           "FIFO_N", "OUT_RUN", "N_OVER", "N_UNDER", "N_RX", "N_TX")):
        syms[n] = st + 4 * i
    descriptors(img)
    os_ = Os(img, syms)
    control(os_)
    interfaces(os_)
    activation(os_)

    print("the OS's own queue routine (0x40003f94, not intercepted)")
    queue_real(img, syms)
    print("to the host")
    o, got, nb = to_host(img, syms, ())
    verify_in(got, nb, "host reading every micro-frame", False)
    check(o.r32(syms["N_OVER"]) == 0 and not o.bad, "no block dropped, no access outside memory")
    o, got, nb = to_host(img, syms, ((4000, 4400), (9000, 9017)))
    d = verify_in(got, nb, "host stalling 50 ms, then 2 ms", True)
    check(d == o.r32(syms["N_OVER"]) > 0, f"the blocks dropped are those counted ({d}); none rewritten while queued")

    print("from the host")
    o, out = from_host(img, syms, "burst")
    verify_out(o, out, "bursts of 24 blocks, host clock +50 ppm", False)
    o, out = from_host(img, syms, "burst", ppm=-80, seed=2)
    verify_out(o, out, "bursts of 24 blocks, host clock -80 ppm", False)
    o, out = from_host(img, syms, "eager")
    check(o.r32(syms["FIFO_N"]) <= 256, f"host always ready: the FIFO stays bounded ({o.r32(syms['FIFO_N'])} frames, "
                                         "the host waits on NAK)")
    verify_out(o, out, "host always ready", False)
    o, out = from_host(img, syms, "stop")
    verify_out(o, out, "host stopping 0.2 s", True)

    print("no stream")
    o = Os(img, syms)
    o.w32(syms["IN_ON"], 0)
    o.w32(syms["OUT_ON"], 0)
    o.uc.mem_write(INBUF, b"\x55" * 256)
    mk_mix(o, 0)
    o.call(syms["ob_write"], MIX, 32)
    o.call(syms["ob_read"], INBUF, 32)
    check(not o.q[7] and not any(o.uc.mem_read(INBUF, 256)), "nothing sent, silence in")

    print("\nALL OK" if not FAIL else f"\n{len(FAIL)} FAILURE(S): {FAIL[:5]}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
