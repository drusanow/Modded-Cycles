/*
 * MAITS: an added machine of the Model:Cycles (OS 1.13), ten macro engines after Mutable Instruments Plaits
 * (notes/74). The sound is in engine.s (fixed-point remakes of Plaits' algorithms, MIT: LICENSE-Plaits); this file
 * connects it to the voice loop.
 *
 * Voice loop contract (notes/14): update(pmod, v, p) then render(out, v), once per block of 32 frames at 48 kHz.
 * pmod = the trig's note (semitones << 16); v = the Cycles voice (0x31c bytes); p = the track's parameters (8.8
 * words: +0x12 MACHINE, +0x14 PITCH, +0x16 COLOR, +0x18 SHAPE, +0x1a SWEEP, +0x1c CONTOUR, +0x1e PUNCH,
 * +0x20 GATE, +0x22 FINE, +0x24 DECAY); v+0x34 / v+0x38: a trig this block / the block before; out: 32 x int32.
 *
 * Knobs: PITCH and FINE tune it, as the original machines; COLOR = HARMONICS, SHAPE = TIMBRE, SWEEP = MORPH,
 * CONTOUR = the engine (0..9, its number on the screen); DECAY and GATE: the original amp chain, set as TONE's (its decay table), which
 * plays the part of Plaits' low-pass gate; PUNCH as on TONE. The velocity (VEL) sets the note's volume, through the
 * OS, as on the original machines.
 *
 * The engines render 64 samples at 96 kHz (mi_fill); a half-band filter of 23 coefficients, MACRO's (notes/43
 * par. 3.2), keeps every other one: 32 at 48 kHz, the engines' full scale at half the Cycles' (Q31), as MACRO.
 *
 * State: one voice per track, in engine.s and here (the payload's BSS, zero at start-up), reset at the first note
 * after a machine change (mark at v+0x2c, cleared by the voice reset 0x400a7ab8). A silent voice (amp envelope
 * closed, no trig) is not computed.
 */
typedef int s32;
typedef unsigned int u32;
typedef short s16;
typedef signed char s8;

#define CYC_VOICE0  0x42308828
#define CYC_VSTRIDE 0x31c
#define MARK_OFF    0x2c
#define MARK        0x4d414931                       /* 'MAI1' */

#define AMP_ENV(v)  ((void (*)(char *))0x400a9252)(v)
#define VCA(o, v)   ((void (*)(s32 *, char *))0x400a9430)(o, v)
#define PUNCH(o, v) ((void (*)(s32 *, char *))0x400a967a)(o, v)
#define LUT_DECAY   ((const s32 *)0x4011d84c)        /* DECAY -> multiplier per block (TONE's) */

#define V32(o)      (*(s32 *)(v + (o)))
#define P16(o)      (*(const s16 *)(p + (o)))

#define IDLE_LEVEL  (1 << 14)                        /* amp envelope under -102 dB: a silent voice */
#define HIST        22                               /* the half-band filter's history, at 96 kHz */

/* engine.s */
extern void mi_fill(s32 track, s32 note, s32 machine);
extern s32 mi_p[6][4];                               /* the four dials, 0..0x7f00 */
extern s32 mi_eng[6];                                /* the engine each track's state is for, + 1 (0: none) */
extern unsigned char mi_trig[8];                     /* a new note this block */
extern s32 mi_obuf[64];                              /* the block, 96 kHz, a sample << 15 */

struct track {
	s32 note;                                        /* Q8 semitones, the trig's note and the tuning */
	s16 x[HIST + 64];                                /* the half-band filter's input: its history, then the block */
};

static struct track trk[6];

/* half-band filter, 23 coefficients (Q15, minimax, sum 1/2: gain 1 at DC): 1/2 at the centre, then +-1, +-3, ...
 * +-11 (MACRO's, notes/43 par. 3.2) */
static const s16 HB[6] = { 10327, -3131, 1579, -820, 434, -197 };

static inline s32 dial(s32 x)                        /* 8.8, 0..127 */
{
	return x < 0 ? 0 : (x > 0x7f00 ? 0x7f00 : x);
}

static inline u32 mag(s32 x)
{
	return x < 0 ? 0u - (u32)x : (u32)x;
}

void maits_update(s32 pmod, char *v, const char *p)
{
	s32 t = ((u32)v - CYC_VOICE0) / CYC_VSTRIDE;
	struct track *tr = &trk[t];
	s32 dec, n, i;

	if (V32(MARK_OFF) != MARK) {                     /* the first note on this machine: from rest */
		mi_eng[t] = 0;
		for (i = 0; i < HIST; i++)
			tr->x[i] = 0;
		V32(MARK_OFF) = MARK;
	}
	/* the original amp chain, set as TONE's (0x400aa7b8) */
	dec = (s8)p[0x24];
	dec = dec < 0 ? 0 : (dec > 127 ? 127 : dec);
	V32(0x250) = LUT_DECAY[dec];
	if (V32(0x38)) {
		V32(0x274) = V32(0x278) = 0x012c0f26;
		V32(0x280) = V32(0x284) = 0x001f7c5c;
		V32(0x290) = V32(0x294) = 0x7fffffff;
		V32(0x27c) = 0x82581e4b;
		V32(0x288) = 0x803ef8b8;
		V32(0x29c) = 0x7fffffff; V32(0x2a0) = 0x20000000; V32(0x2a4) = 0x20000000;
		V32(0x48) = 0;
		mi_trig[t] = 1;                          /* a new note for the engine */
	}
	/* the note: the trig's, plus PITCH - 64 and FINE (+-2 semitones), as the original machines */
	n = (pmod < 0 ? 0 : (pmod > (127 << 16) ? 127 << 16 : pmod))
	    + ((s32)P16(0x14) << 8) + (((s32)P16(0x22) - 0x4000) << 3) - (64 << 16);
	n >>= 8;
	tr->note = n < 0 ? 0 : (n > 143 * 256 ? 143 * 256 : n);
	mi_p[t][0] = dial(P16(0x16));
	mi_p[t][1] = dial(P16(0x18));
	mi_p[t][2] = dial(P16(0x1a));
	n = dial(P16(0x1c)) >> 8;                        /* CONTOUR: the engine, 0..9 */
	n = n > 9 ? 9 : n;
	mi_p[t][3] = (n * 32768 + 16384) / 10;           /* the middle of its tenth of the dial (mi_eidx) */
}

void maits_render(s32 *out, char *v)
{
	s32 t = ((u32)v - CYC_VOICE0) / CYC_VSTRIDE;
	struct track *tr = &trk[t];
	const s16 *x;
	int i;

	if (!V32(0x34) && !V32(0x38) && !mi_trig[t]
	    && mag(V32(0x230)) < IDLE_LEVEL && mag(V32(0x234)) < IDLE_LEVEL) {
		for (i = 0; i < 32; i++)                     /* a silent voice: not computed */
			out[i] = 0;
		return;
	}
	mi_fill(t, tr->note, 0);
	for (i = 0; i < 64; i++)
		tr->x[HIST + i] = (s16)(mi_obuf[i] >> 15);
	x = tr->x;
	for (i = 0; i < 32; i++, x += 2) {
		s32 acc = (s32)x[11] << 14;
		acc += HB[0] * ((s32)x[10] + x[12]);
		acc += HB[1] * ((s32)x[8] + x[14]);
		acc += HB[2] * ((s32)x[6] + x[16]);
		acc += HB[3] * ((s32)x[4] + x[18]);
		acc += HB[4] * ((s32)x[2] + x[20]);
		acc += HB[5] * ((s32)x[0] + x[22]);
		out[i] = acc;                            /* the engines' full scale -> 1/2 full scale (Q31) */
	}
	for (i = 0; i < HIST; i++)                       /* the history slides to the start */
		tr->x[i] = tr->x[64 + i];
	AMP_ENV(v);
	VCA(out, v);
	PUNCH(out, v);
}
