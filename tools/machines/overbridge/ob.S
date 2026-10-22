/*
 * Overbridge mode for the Model:Cycles (OS 1.13), EXPERIMENT (notes/75). The USB AUDIO/MIDI mode (mode 5, entry 4
 * of the mode table 0x4013e538) becomes an Overbridge 2 device: vendor interfaces, interrupt endpoints 0x83 (to the
 * host) and 0x03 (from the host) at high speed, blocks of 7 frames, 8 channels each way, big-endian int32, as the
 * Syntakt's (notes/75 par. 3). USB MIDI stays (interfaces 4 and 5).
 *
 * Block (256 bytes): 0x07, 0x00, frame counter (16 bits, +7 a block), 28 bytes we leave at zero, then 7 frames x 8
 * channels x 4 bytes. To the host: Main L, Main R, tracks 1..6 (the voices' outputs before the mixer, 0x80001858).
 * From the host: channels 1 and 2 go where the stock USB input went (mixed into the main output by the OS);
 * channels 3..8 are received and kept for later (Sidecar returns), not played yet.
 *
 * Contexts. ob_act, ob_deact, ob_in_*, ob_out_*, ob_vendor: the USB interrupt (control requests). ob_write and
 * ob_read: the audio render interrupt (0x40058c5e), once per block of 32 frames. The start/stop routines mask
 * interrupts while they reset what the audio side reads. Calls into the OS: arguments on the stack, d0-d1/a0-a1
 * free, d2-d7/a2-a6 kept.
 *
 * Memory: transfer descriptors (dTD, 64 bytes) in the stock pools of the internal SRAM, unused in this mode
 * (0x80008000: to the host, 0x80008800: from the host); blocks in the free hole above the OS's BSS (notes/56
 * par. 3.5.2) through its uncached alias (+0x08000000, as the OS's own DMA buffers), so the controller and the CPU
 * see the same bytes; the incoming FIFO and the state in the same hole, cached (CPU only). None of it is cleared at
 * start-up: the start routines set everything they read.
 */
    RAMB    = 0x4233a000            /* our part of the hole: 0x4233a000..0x4233d0ff */
    UNC     = 0x08000000            /* the uncached alias */
    RING_IN = RAMB + UNC            /* 8 blocks of 256 bytes, to the host */
    RING_OUT = RAMB + 0x800 + UNC   /* 6 blocks of 256 bytes, from the host */
    FIFO    = RAMB + 0x1000         /* 256 frames x 8 channels x 4 bytes, from the host */
    ST      = RAMB + 0x3000         /* state, below */
    IN_ON   = ST + 0                /* the stream to the host runs */
    IN_W    = ST + 4                /* its block being filled, 0..7 */
    IN_NF   = ST + 8                /* frames already in it, 0..6 */
    IN_CNT  = ST + 12               /* the frame counter */
    IN_SKIP = ST + 16               /* this block's buffer is still queued: the block is dropped */
    OUT_ON  = ST + 20               /* the stream from the host runs */
    OUT_R   = ST + 24               /* the next receive dTD to read back, 0..5 */
    FIFO_W  = ST + 28               /* write frame, 0..255 */
    FIFO_R  = ST + 32               /* read frame, 0..255 */
    FIFO_N  = ST + 36               /* frames in the FIFO */
    OUT_RUN = ST + 40               /* playing (the FIFO was filled to PREFILL once) */
    N_OVER  = ST + 44               /* blocks to the host dropped (the host did not read) */
    N_UNDER = ST + 48               /* blocks of silence played (the FIFO ran dry) */
    N_RX    = ST + 52               /* blocks received */
    N_TX    = ST + 56               /* blocks sent */
    PREFILL = 64                    /* frames in the FIFO before playing (1.3 ms) */

    DTD_IN  = 0x80008000            /* 8 dTD, to the host (endpoint 7 = EP3 IN) */
    DTD_OUT = 0x80008800            /* 6 dTD, from the host (endpoint 6 = EP3 OUT) */
    DQH_IN  = 0x800001c0
    DQH_OUT = 0x80000180
    ENDPTFLUSH = 0xfc0b01b4
    ENDPTCTRL3 = 0xfc0b01cc
    USB_MIX = 0x40fe4b90            /* the OS's mix for the USB, 32 frames x (L, R) */
    TRACKS  = 0x80001858            /* the 6 voices' outputs, 32 frames each, stride 0x80 */
    QUEUE   = 0x40003f94            /* the OS: queue a dTD (endpoint, dTD) */
    TOKEN   = 0x01000080            /* 256 bytes, active */

/* ---- ob_act(arg) / ob_deact(): the mode's activation and its end (entry +0, +4) ----------------------------- */
    .section .ob1, "ax"
    .globl  ob_act, ob_deact
ob_act:
    jsr     0x40002c56              /* the stock end: endpoints off, the stock streams stopped */
    jsr     0x40002da8              /* the SRAM pools cleared */
    clrl    IN_ON
    clrl    OUT_ON
    movel   #0x04000000,%d0         /* the two queue heads: max packet 1024, mult 0, no dTD */
    lea     DQH_IN,%a0
    bsr.s   1f
    lea     DQH_OUT,%a0
    bsr.s   1f
    movel   #0x00cc00cc,%d0         /* EP3 both ways: interrupt, enabled, toggles reset */
    movel   %d0,ENDPTCTRL3
    movel   %sp@(4),%sp@-
    jsr     0x4000311c              /* the stock activation's MIDI part */
    addql   #4,%sp
    rts
1:  movel   %d0,%a0@
    clrl    %a0@(4)
    movel   #0xdead0001,%d1
    movel   %d1,%a0@(8)
    clrl    %a0@(12)
    clrl    %a0@(48)
    clrl    %a0@(52)
    rts
ob_deact:
    clrl    IN_ON
    clrl    OUT_ON
    jmp     0x40002c56

/* ---- ob_in_start(alt) / ob_in_stop(): SET_INTERFACE 1 (the stream to the host) ------------------------------ */
    .section .ob2, "ax"
    .globl  ob_in_start, ob_in_stop
ob_in_start:
    movew   %sr,%d1
    movew   #0x2700,%sr
    clrl    IN_ON
    bsr.s   ob_in_flush
    lea     RING_IN,%a0             /* the blocks zeroed (their 28 spare bytes stay so) */
    movel   #511,%d0
1:  clrl    %a0@+
    subql   #1,%d0
    bpls    1b
    lea     DTD_IN,%a0              /* the dTD idle */
    moveq   #7,%d0
2:  clrl    %a0@(4)
    lea     %a0@(64),%a0
    subql   #1,%d0
    bpls    2b
    clrl    IN_W
    clrl    IN_NF
    clrl    IN_CNT
    clrl    IN_SKIP
    moveq   #1,%d0
    movel   %d0,IN_ON
    movew   %d1,%sr
    rts
ob_in_stop:
    clrl    IN_ON
ob_in_flush:                        /* what was queued is dropped, as the stock stop does */
    movel   #0x00080000,%d0
    movel   %d0,ENDPTFLUSH
    clrl    DQH_IN+48
    clrl    DQH_IN+52
    rts

/* ---- ob_out_start(alt) / ob_out_stop(): SET_INTERFACE 2 (the stream from the host) -------------------------- */
    .section .ob3, "ax"
    .globl  ob_out_start, ob_out_stop
ob_out_start:
    movel   %d2,%sp@-
    movew   %sr,%d2
    movew   #0x2700,%sr
    clrl    OUT_ON
    bsr.s   ob_out_flush
    clrl    OUT_R
    clrl    FIFO_W
    clrl    FIFO_R
    clrl    FIFO_N
    clrl    OUT_RUN
    moveq   #0,%d0
1:  movel   %d0,%sp@-
    jsr     ob_rx_prime
    movel   %sp@+,%d0
    addql   #1,%d0
    moveq   #6,%d1
    cmpl    %d1,%d0
    bnes    1b
    moveq   #1,%d0
    movel   %d0,OUT_ON
    movew   %d2,%sr
    movel   %sp@+,%d2
    rts
ob_out_stop:
    clrl    OUT_ON
ob_out_flush:
    moveq   #8,%d0                  /* EP3 RX flushed, its queue forgotten */
    movel   %d0,ENDPTFLUSH
    clrl    DQH_OUT+48
    clrl    DQH_OUT+52
    rts

/* ---- ob_rx_prime(j): receive dTD j ready for a block, queued (d0-d1/a0-a1) ---------------------------------- */
    .section .ob4, "ax"
    .globl  ob_rx_prime
ob_rx_prime:
    movel   %sp@(4),%d0
    movel   %d0,%d1
    lsll    #6,%d1
    lea     DTD_OUT,%a0
    addal   %d1,%a0                 /* a0 = dTD j */
    lsll    #8,%d0
    addil   #RING_OUT,%d0           /* d0 = block j */
    bsr.s   ob_dtd
    movel   %a0,%sp@-
    pea     6
    jsr     QUEUE
    addql   #8,%sp
    rts
/* a0 = dTD, d0 = buffer: one 256-byte transfer, active, no callback. Keeps a0, d0. */
    .globl  ob_dtd
ob_dtd:
    lea     %a0@(16),%a1
    moveq   #11,%d1
1:  clrl    %a1@+
    subql   #1,%d1
    bpls    1b
    movel   #0xdead0001,%d1
    movel   %d1,%a0@
    movel   #TOKEN,%d1
    movel   %d1,%a0@(4)
    movel   %d0,%a0@(8)
    movel   %d0,%d1
    addil   #0x1000,%d1
    andil   #0xfffff000,%d1
    movel   %d1,%a0@(12)
    movel   #256,%d1
    movel   %d1,%a0@(32)
    rts

/* ---- ob_write(src, n): the block to the host, in place of 0x40002912(src, n) -------------------------------- */
/* Called with the OS's USB mix (0x40059392), and with a silent buffer when the sound is cut (0x400593ce): only the
 * first is sent, the frames of a block are sent once. */
    .section .ob5, "ax"
    .globl  ob_write
ob_write:
    movel   %sp@(4),%d0
    cmpil   #USB_MIX,%d0
    bnew    9f
    tstl    IN_ON
    beqw    9f
    lea     %sp@(-40),%sp
    moveml  %d2-%d7/%a2-%a5,%sp@
    moveal  %sp@(44),%a2            /* the mix, (L, R) per frame */
    lea     TRACKS,%a3
    movel   %sp@(48),%d7            /* frames */
    moveq   #0,%d6                  /* this frame */
1:  movel   IN_NF,%d2
    bnes    2f
    jsr     ob_tx_begin             /* a new block: is its buffer free? */
2:  tstl    IN_SKIP
    bnes    3f
    movel   IN_W,%d0                /* a5 = this frame's place in the block */
    lsll    #8,%d0
    movel   %d2,%d1
    lsll    #5,%d1
    addl    %d1,%d0
    addil   #RING_IN+32,%d0
    moveal  %d0,%a5
    movel   %d6,%d0
    lsll    #3,%d0
    movel   %a2@(0,%d0:l),%a5@+     /* Main L */
    movel   %a2@(4,%d0:l),%a5@+     /* Main R */
    movel   %d6,%d0
    lsll    #2,%d0
    lea     %a3@(0,%d0:l),%a0
    movel   %a0@,%a5@+              /* track 1 */
    movel   %a0@(0x80),%a5@+
    movel   %a0@(0x100),%a5@+
    movel   %a0@(0x180),%a5@+
    movel   %a0@(0x200),%a5@+
    movel   %a0@(0x280),%a5@+       /* track 6 */
3:  addql   #1,%d2
    movel   %d2,IN_NF
    moveq   #7,%d0
    cmpl    %d0,%d2
    bnes    4f
    jsr     ob_tx_end               /* the block is full: send it */
4:  addql   #1,%d6
    cmpl    %d7,%d6
    bnes    1b
    moveml  %sp@,%d2-%d7/%a2-%a5
    lea     %sp@(40),%sp
9:  rts

/* ob_tx_begin: IN_SKIP = the block's dTD still queued (the host has not read it yet). d0-d1/a0. */
/* ob_tx_end: header, dTD, queued; the next block; the counter. d0-d1/a0-a1. */
    .section .ob6, "ax"
    .globl  ob_tx_begin, ob_tx_end
ob_tx_begin:
    movel   IN_W,%d0
    lsll    #6,%d0
    addil   #DTD_IN,%d0
    moveal  %d0,%a0
    moveq   #0,%d0
    btst    #7,%a0@(7)              /* token: active */
    beqs    1f
    moveq   #1,%d0
    addql   #1,N_OVER
1:  movel   %d0,IN_SKIP
    rts
ob_tx_end:
    tstl    IN_SKIP
    bnes    2f
    movel   IN_W,%d1
    movel   %d1,%d0
    lsll    #8,%d0
    addil   #RING_IN,%d0            /* d0 = the block */
    moveal  %d0,%a1
    movew   #0x0700,%a1@            /* 7 frames */
    movew   IN_CNT+2,%d0            /* the counter's low 16 bits */
    movew   %d0,%a1@(2)
    movel   %a1,%d0                 /* the block, for ob_dtd */
    lsll    #6,%d1
    addil   #DTD_IN,%d1
    moveal  %d1,%a0
    jsr     ob_dtd
    movel   %a0,%sp@-
    pea     7
    jsr     QUEUE
    addql   #8,%sp
    addql   #1,N_TX
    movel   IN_W,%d0
    addql   #1,%d0
    moveq   #7,%d1
    andl    %d1,%d0
    movel   %d0,IN_W
2:  clrl    IN_NF
    addql   #7,IN_CNT               /* frames go on even when a block is dropped */
    rts

/* ---- ob_read(dst, n): the host's audio for this block, in place of 0x40002ae0(dst, n) ----------------------- */
/* dst: n frames x (L, R), what the OS mixes into its output. The received blocks are moved to the FIFO (only when
 * it has room: a dTD is queued again only then, so the host waits, NAK, rather than overruns it). */
    .section .ob7, "ax"
    .globl  ob_read
ob_read:
    lea     %sp@(-40),%sp
    moveml  %d2-%d7/%a2-%a5,%sp@
    tstl    OUT_ON
    beqw    5f
    moveq   #5,%d7                  /* at most the 6 dTD */
1:  movel   FIFO_N,%d0
    cmpil   #256-7,%d0
    bgts    4f                      /* no room: leave it, the host waits */
    movel   OUT_R,%d6
    movel   %d6,%d0
    lsll    #6,%d0
    addil   #DTD_OUT,%d0
    moveal  %d0,%a4
    btst    #7,%a4@(7)
    bnes    4f                      /* still waiting for its block */
    movel   %a4@(4),%d0             /* bytes left: bits 30..16 */
    swap    %d0
    andil   #0x7fff,%d0
    bnes    2f                      /* a short block: dropped */
    jsr     ob_rx_take
2:  movel   %d6,%sp@-
    jsr     ob_rx_prime
    addql   #4,%sp
    addql   #1,%d6
    moveq   #6,%d0
    cmpl    %d0,%d6
    bnes    3f
    moveq   #0,%d6
3:  movel   %d6,OUT_R
    subql   #1,%d7
    bpls    1b
4:  movel   OUT_RUN,%d0             /* play */
    bnes    6f
    movel   FIFO_N,%d0
    cmpil   #PREFILL,%d0
    blts    5f
    moveq   #1,%d0
    movel   %d0,OUT_RUN
6:  movel   FIFO_N,%d0
    cmpl    %sp@(48),%d0
    bges    7f
    clrl    OUT_RUN                 /* ran dry: silence, then fill again */
    addql   #1,N_UNDER
5:  jsr     ob_silence
    bras    9f
7:  jsr     ob_play
9:  moveml  %sp@,%d2-%d7/%a2-%a5
    lea     %sp@(40),%sp
    rts

/* ob_rx_take: a4 = a full receive dTD, d6 = its index: its 7 frames into the FIFO. d0-d2/a0-a1. */
/* ob_silence: n frames of silence into dst (ob_read's arguments, as ob_play). d0/a0. */
/* ob_play: n frames of channels 1, 2 from the FIFO into dst (ob_read's arguments). d0-d2/a0-a1. */
    .section .ob8, "ax"
    .globl  ob_rx_take, ob_play, ob_silence
ob_silence:
    moveal  %sp@(48),%a0
    movel   %sp@(52),%d0
    subql   #1,%d0
1:  clrl    %a0@+
    clrl    %a0@+
    subql   #1,%d0
    bpls    1b
    rts
ob_rx_take:
    movel   %d6,%d0
    lsll    #8,%d0
    addil   #RING_OUT+32,%d0
    moveal  %d0,%a0                 /* its frames */
    movel   FIFO_W,%d0
    moveq   #6,%d1
1:  movel   %d0,%d2                 /* a1 = FIFO + 32 x frame */
    lsll    #5,%d2
    addil   #FIFO,%d2
    moveal  %d2,%a1
    movel   %a0@+,%a1@+
    movel   %a0@+,%a1@+
    movel   %a0@+,%a1@+
    movel   %a0@+,%a1@+
    movel   %a0@+,%a1@+
    movel   %a0@+,%a1@+
    movel   %a0@+,%a1@+
    movel   %a0@+,%a1@+
    addql   #1,%d0
    andil   #255,%d0
    subql   #1,%d1
    bpls    1b
    movel   %d0,FIFO_W
    addql   #7,FIFO_N
    addql   #1,N_RX
    rts
ob_play:
    moveal  %sp@(48),%a0            /* dst (ob_read's argument; +4 for this call's return address) */
    movel   %sp@(52),%d1            /* n */
    sub.l   %d1,FIFO_N
    movel   FIFO_R,%d0
    subql   #1,%d1
1:  movel   %d0,%d2
    lsll    #5,%d2
    addil   #FIFO,%d2
    moveal  %d2,%a1
    movel   %a1@+,%a0@+             /* channel 1 -> L */
    movel   %a1@,%a0@+              /* channel 2 -> R */
    addql   #1,%d0
    andil   #255,%d0
    subql   #1,%d1
    bpls    1b
    movel   %d0,FIFO_R
    rts

/* ---- ob_vendor: control requests the stock dispatcher (0x4000448c) did not know, from 0x4000453c ------------- */
/* Entered by jmp with d0 = bmRequestType.bRequest << 16, d1 = wLength. Answers Overwitch's two reads (0xC0, 1: the
 * name, 32 bytes; 0xC0, 2: the version, 16 bytes), sends everything else where the stock code did. */
    .section .ob9, "ax"
    .globl  ob_vendor
ob_vendor:
    cmpil   #0xa1010000,%d0         /* the stock compare this replaces */
    bnes    1f
    jmp     0x400048c6
1:  lea     ob_name,%a0
    cmpil   #0xc0010000,%d0
    beqs    2f
    lea     ob_vers,%a0
    cmpil   #0xc0020000,%d0
    beqs    3f
    jmp     0x40004982              /* stall, as before */
2:  moveq   #32,%d0
    bras    4f
3:  moveq   #16,%d0
4:  cmpl    %d1,%d0
    bles    5f
    movel   %d1,%d0
5:  movel   %a0,%sp@-
    movel   %d0,%sp@-
    jsr     0x40004438              /* the stock answer on EP0 (length, data) */
    addql   #8,%sp
    jmp     0x400049b6
ob_name:
    .ascii  "Model:Cycles"
    .space  20
ob_vers:
    .ascii  "0001        0.01"
