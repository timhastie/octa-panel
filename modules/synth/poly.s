| SYNTH MACHINE -- the FM voice engine as a DRAM unit, phase 5: paraphonic
| chords (24 Sep 2026). GNU as, -mcpu=5475. Linked by the build into the
| platform runtime at the base of the arena reserve (docs/remixer/
| PLACEMENT.md); the kind table's FLEX entry 0x400d6438 is pointed at
| sy_render by a "ptr" detour (modules/synth/manifest.py). synth.s, the ROM
| cave this grew out of, is kept beside it for the record: everything under
| "the mono voice" below is that cave's code, and with POLY off the samples
| it produces are the same, bit for bit.
|
| WHAT (phases 1-4, unchanged): a FLEX track whose sample is named SYNTH* has
| its sample data GENERATED here every frame instead of taken from the flex
| pool -- a two-operator FM voice at the final pitch, 16 samples a frame at
| rate 1.0 (the DSP's resampler is an identity), carrier = sin(phi_c + I *
| sin(phi_m + fb * m_prev)); the PLAYBACK page's other slots are RATIO
| (STRT), INDEX (LEN), FEEDBACK (RTRG), DECAY (RTIM); PTCH and RATE go
| through the stock renderer's own rate arithmetic; GLIDE (the project
| setting at GLIDE_AT) slews the pitch. The stock renderer is still called
| around every frame (the voice lifecycle, streaming and the record's
| headers are its), with PTCH := 0 and RATE := 1.0 for the call.
|
| POLY (phase 5). With the project's POLY setting on (PROJECT > CONTROL >
| SEQUENCER > POLY, the byte at POLY_AT = GLIDE_AT + 1, modules/quantizer)
| a synth track has FOUR VOICES, each with its own phases, index envelope,
| slewed pitch and amplitude envelope, summed into the track's one source
| stream at half level with saturation:
|   * a voice START (the packer's event bit 4: a sequencer trig or a live
|     CHROMATIC key) allocates one voice per note of the CHORD SHAPE --
|     the track's LFO page slot 5 byte (DEP3's storage, the "current value"
|     byte 0x80000810 + t*72 + 11, so a step lock on that slot picks the
|     shape per step), 0..127 -> po_shapes[raw >> 2], 32 shapes of up to
|     four semitone offsets -- at the record's PTCH word plus the offset,
|     each note snapped onto the quantizer's SCALE when one is set (po_snap:
|     the mask through the pinned accessor SCALE_AT, nearest degree, ties
|     down -- a MAJ shape in a minor scale comes out MIN). VOIC, the LFO
|     page's slot 2 (SPD3's byte, free because LFO 3 is muted here; current
|     value 0x80000810 + t*72 + 8, locks honoured), caps the notes a trig
|     takes at 1..4 -- the root first, dropped from the top; 1 = the root
|     alone, mono with release tails. A byte outside 1..4 (a Part's stock
|     SPD3 default, 32) reads as 4;
|     A free voice is taken first, else the oldest releasing one, else the
|     oldest sounding one (a global allocation stamp, V_AGE);
|   * a live key (the quantizer's key hooks leave the key's index+1 in
|     qz_pkey[t] at KEYS_AT and keep the mask of held keys in qz_pmask[t])
|     tags its voices with the key; releasing the key (its bit leaves the
|     mask) puts them into RELEASE. A sequencer trig (qz_pkey[t] == 0)
|     first releases every sounding voice of the track: notes sustain
|     until the next trig;
|   * RELEASE: the voice's gain decays exponentially with the time constant
|     of the AMP page REL byte (0x80000810 + t*72 + 14, locks applied):
|     tau = 5 ms * 1000^(rel/126) -- 5 ms at 0, 29 ms at 32, 167 ms at 64,
|     0.97 s at 96, 5 s at 126; 127 = INF (the DSP's INF), the voice
|     sustains until stolen (po_relk). Attack is the same 8-frame ramp the
|     mono voice has;
|   * PITCH per voice: target = V_ROOT + (PTCH word - the word at the last
|     start), so a lock or slide or LFO on PTCH moves every voice by the
|     same interval (and each voice slews it with GLIDE, sy_slew on its own
|     V_CUR) while a new key's word does not move the voices already
|     sounding (V_ROOT absorbs the delta at each start). The word is folded
|     into the stock curve's 24-semitone range by octaves before the rate
|     arithmetic and the increment shifted back, so a chord note up to three
|     octaves above PTCH +12 keeps the stock's tuning;
|   * LEVEL: each voice is c * gain with gain Q14 (1/2 of the mono voice's
|     0x4000 full scale), the sum doubled into the mono format's Q15 and
|     saturated at +-0x7fff: one note is 6 dB below the mono voice, four in
|     phase reach full scale.
| The stock voice lifecycle is untouched: a new key restarts the DSP voice
| (the AMP and filter envelopes run over the whole mix as for a sample), a
| released last key posts the AMP release as stock. Musically: AMP ATK 0,
| HOLD INF, REL to taste -- the per-voice envelopes shape the notes.
|
| ALSO HERE: po_lfo3 / po_lfo3b (detours at the depth read of the LFO engine's
| two copies, 0x40003ca4 and 0x4000d03e: LFO 3's depth is read as 0 on a track
| whose playing voice is a synth, POLY on or off -- the slot is the chord byte
| there, and LFO 3's default PMTR is PTCH, so with POLY off a chord left in
| the slot was a pitch LFO: the same key gave a different pitch each press)
| and po_lfopage (a detour at the page resolver's LFO-descriptor load
| 0x40031e62: a synth track with POLY on gets a clone of the LFO descriptor
| built here on first use, slot 5 named CHRD with a formatter printing the
| shape's name, slot 2 named VOIC printing 1..4 with that range and default).
|
| Layout: code, then the tables (ratio, shapes, names, po_relk, the sine
| table) and the RAM state (per-track records, 32 voice records, the LFO
| descriptor clone). The main OS runs from DRAM and so does this.

        .text
        .global sy_render, po_lfo3, po_lfo3b, po_lfopage
        .set    VOICE_BASE, 0x800049d8
        .set    VOICE_STRIDE, 0xa8
        .set    CURSOR, 0x80001c80
        .set    NIBBLE, 0x46104d0c
        .set    STOCK_RENDER, 0x40004008
        .set    FP_PTR, 0x800062a8       | the packer's per-track DSP parameter record
        .set    RS_PTR, 0x800062a4       | the packer's per-track render state
        .set    SETTINGS_BASE, 0x100b14f0 | the sample settings records, 0x448 each, slots 0..135
        .set    SETTINGS_SPAN, 0x24640    | 136 * 0x448
        .set    SETTINGS_STRIDE, 0x448
        .set    PITCH_TAB, 0x400aa294    | the stock 2^(x/12) curve, longs Q26 (index = PTCH word >> 5)
        .set    C4_INC, 25480119         | C4: 261.6256 / 44100 * 2^32
        .set    ENV_ONE, 0x01000000      | the index envelope's 1.0 (Q24)
        .set    ENV_FLOOR, 0x00100000    | it decays toward 1/16
        .set    K_NUM, 3068384           | k = K_NUM / RTIM^2, Q20 per frame: tau = 2 s at 127
        .set    K_MAX, 0xfffff
        .set    INDEX_SCALE, 2628        | 8 rad / 127 in cycles * 2^18 (offset = m_Q14 * I)
        .set    FB_SCALE, 516            | 0.25 cycle / 127 * 2^16
        .set    RAMP_STEP, 4096          | gain Q15: 0 -> 1.0 over 8 frames (2.9 ms)
        .set    RAMP4, 2048              | the same ramp for the poly voices' Q14 gain
        .set    GAIN4, 16384             | a poly voice's full gain (Q14 = 1/2 of the mono voice)
        .set    GLIDE_AT, 0x400d2cdc     | the GLIDE byte (modules/quantizer/manifest.py GLIDE_AT; 0 = off)
        .set    POLY_AT, 0x400d2cdd      | the POLY byte, next to it (glide.s qz_poly)
        .set    KEYS_AT, 0x400d2cb0      | modules/quantizer/keys.s: qz_pkey[8] bytes, qz_pmask[8] longs at +8
        .set    SCALE_AT, 0x400d2ca8     | modules/quantizer/scale.s: `jmp qz_scale_mask` -- d0 := the scale's pitch-class mask, 0 = OFF
        .set    CURVALS, 0x80000810      | the frame builder's per-track current values, 72 B a track, locks applied
        .set    CV_STRIDE, 72
        .set    CV_CHRD, 11              | flat slot 11 = LFO page slot 5 (DEP3): the chord shape
        .set    CV_VOIC, 8               | flat slot 8 = LFO page slot 2 (SPD3): the voice count, 1..4
        .set    CV_REL, 14               | flat slot 14 = AMP page slot 2 (REL)
        .set    LFO_STATE, 0x80004858    | the LFO engine's per-track state, 8 B a track (a4 in its loop)
        .set    LFO_P, 0x400d37f6        | the stock LFO page descriptor (P form), 0x192 bytes
        .set    LFO_DESC_LEN, 0x192
        .set    RESOLVER_RET, 0x40031ed6
        .set    SLOT_OFF, 0x8f04b        | Part: 0x8f04a + track*5 + machine (1 = FLEX)
        .set    SPRINTF, 0x40013a08
        .set    SEMI, 0x500              | one semitone of the PTCH word (5 raw units << 8)
        .set    SHAPE_END, -128          | ends a shape's offsets
| ---- the per-track record, 128 bytes: the mono voice (0..43, synth.s's layout) and the poly frame
        .set    ST_STRIDE, 128
        .set    S_PHC, 0                 | carrier phase, Q32 cycles
        .set    S_PHM, 4                 | modulator phase
        .set    S_ENV, 8                 | index envelope, Q24
        .set    S_INC, 12                | carrier increment per sample (the true pitch)
        .set    S_INCM, 16               | modulator increment
        .set    S_IEFF, 20               | index * envelope, the per-sample multiplier
        .set    S_GAIN, 24               | start ramp, Q15
        .set    S_FB, 28                 | feedback multiplier
        .set    S_LASTM, 32              | the modulator's last sample, Q14
        .set    S_ON, 36                 | the playing voice is a synth
        .set    S_CUR, 40                | the slewed PTCH word, Q12 (GLIDE)
        .set    T_REF, 44                | POLY: the PTCH word at the last voice start
        .set    T_LAST, 48               | POLY: the PTCH word seen last frame
        .set    T_MASK, 52               | POLY: the held-key mask seen last frame
        .set    T_DK, 56                 | POLY: the index decay k this frame (0 = hold)
        .set    T_RK, 60                 | POLY: the release k this frame (Q16)
        .set    T_RATIO, 64              | POLY: the ratio this frame (Q8)
        .set    T_I, 68                  | POLY: the index I this frame
        .set    T_W, 72                  | POLY: this frame's PTCH word
        .set    T_SCALE, 76              | POLY: the scale mask at the last start (0 = OFF)
| ---- a voice record, 64 bytes (the first 36 match the mono voice's fields)
        .set    V_STRIDE, 64
        .set    V_PHC, 0
        .set    V_PHM, 4
        .set    V_ENV, 8
        .set    V_INC, 12
        .set    V_INCM, 16
        .set    V_IEFF, 20
        .set    V_GAIN, 24               | Q14
        .set    V_FB, 28
        .set    V_LASTM, 32
        .set    V_STATE, 36              | 0 free, 1 sounding, 2 releasing
        .set    V_KEY, 37                | the live key's index + 1, 0 = a sequencer note
        .set    V_CUR, 40                | the slewed word, Q12 (sy_slew's S_CUR)
        .set    V_ROOT, 44               | the note's word, Q8, relative to T_REF
        .set    V_AGE, 48                | the allocation stamp

| ---- sy_render(track, ping, start, end) ------------------------------------
sy_render:
        lea     -48(%sp),%sp
        movem.l %d2-%d7/%a2-%a6,(%sp)    | 44(sp) the stock return; args 52 track, 56 ping, 60 start, 64 end
        move.l  CURSOR,%a2               | the header this call writes
        move.l  52(%sp),%d2              | track
        move.l  %d2,%d3
        lsl.l   #7,%d3
        lea     sy_state(%pc),%a3
        add.l   %d3,%a3                  | a3 = this track's record
        move.l  FP_PTR,%a4               | a4 = its DSP parameter record
        moveq   #0,%d4                   | 1 = the fp words are neutralised for the stock call
        moveq   #16,%d1
        cmp.l   64(%sp),%d1              | the frame's second call?
        bne     sy_call
        lea     NIBBLE,%a0
        lea     (%a0,%d2.l),%a0
        btst    #4,(%a0)                 | a voice starts this frame: resolve the marker
        beq     sy_ison
        move.l  #VOICE_STRIDE,%d3
        muls.l  %d2,%d3
        lea     VOICE_BASE,%a0
        move.l  8(%a0,%d3.l),%a0         | the new voice's settings record
        move.l  %a0,%d3
        beq     sy_no
        subi.l  #SETTINGS_BASE,%d3       | only a pointer INTO the settings table is scanned
        cmpi.l  #SETTINGS_SPAN,%d3       | (after power-on the unit's RAM holds garbage and a
        bhs     sy_no                    | refused start leaves the old value)
        move.l  %a0,%a1                  | a1 = start of the file name
        move.l  #255,%d3
sy_scan:
        mvz.b   (%a0)+,%d1
        beq     sy_scanned
        cmpi.l  #'/',%d1
        bne     sy_scan1
        move.l  %a0,%a1                  | after the last '/'
sy_scan1:
        subq.l  #1,%d3
        bne     sy_scan
sy_scanned:
        lea     sy_name(%pc),%a0
        moveq   #5,%d3
sy_cmp:
        mvz.b   (%a0)+,%d1
        mvz.b   (%a1)+,%d5
        cmp.l   %d5,%d1
        bne     sy_no
        subq.l  #1,%d3
        bne     sy_cmp
        clr.l   S_PHM(%a3)               | a synth starts: modulator, feedback, envelope, ramp restart
        clr.l   S_LASTM(%a3)             | (the carrier phase runs on)
        clr.l   S_GAIN(%a3)
        move.l  #ENV_ONE,%d1
        move.l  %d1,S_ENV(%a3)
        move.l  RS_PTR,%a0
        clr.l   4(%a0)                   | the retrig count the packer just latched: no stock retrigs
        mvz.w   (%a4),%d1                | a fresh note starts at its own pitch: no slide (GLIDE)
        lsl.l   #8,%d1
        lsl.l   #4,%d1
        move.l  %d1,S_CUR(%a3)
        moveq   #1,%d3
        bra     sy_set
sy_no:
        moveq   #0,%d3
sy_set:
        move.b  %d3,S_ON(%a3)
sy_ison:
        tst.b   S_ON(%a3)
        beq     sy_call
        mvz.w   (%a4),%d6                | PTCH word (raw << 8; 0x4000 = 0 semitones)
        tst.b   POLY_AT
        bne     sy_neutral               | POLY: the voices slew on their own (po_frame)
        bsr     sy_slew                  | GLIDE: d6 := the word slewed toward it
sy_neutral:
        mvz.w   6(%a4),%d5               | RATE word (0x7f00 = 1.0)
        move.w  #0x4000,(%a4)            | the stock renderer computes rate 1.0 from these
        move.w  #0x7f00,6(%a4)
        moveq   #1,%d4
sy_call:
        move.l  64(%sp),-(%sp)           | end
        move.l  64(%sp),-(%sp)           | start
        move.l  64(%sp),-(%sp)           | ping
        move.l  64(%sp),-(%sp)           | track
        jsr     STOCK_RENDER
        lea     16(%sp),%sp
        move.l  %d0,44(%sp)              | the stock return value, handed back
        tst.l   %d4
        beq     sy_check
        move.w  %d6,(%a4)                | restore PTCH and RATE for the DSP
        move.w  %d5,6(%a4)
        tst.b   POLY_AT
        beq     sy_mono_frame
        bsr     po_frame                 | POLY: the voices' frame (preserves a2, a3, d2)
        bra     sy_check

| ---- the mono voice's frame: the rate, as the stock renderer computes it -----
sy_mono_frame:
        bsr     po_rate                  | d0 = C4_INC * rate(d6, d5) (clobbers d0/d1/d3/d4/a0)
        move.l  %d0,S_INC(%a3)           | carrier increment: the true pitch

| ---- the parameters ---------------------------------------------------------
        mvz.w   2(%a4),%d1               | STRT -> ratio
        lsr.l   #8,%d1
        lsr.l   #2,%d1
        lea     sy_ratio(%pc),%a0
        mvz.w   (%a0,%d1.l*2),%d1        | Q8
        asr.l   #8,%d0
        muls.l  %d1,%d0
        move.l  %d0,S_INCM(%a3)          | modulator increment = ratio * pitch
        mvz.w   8(%a4),%d1               | RTRG -> feedback
        move.l  #FB_SCALE,%d0
        mulu.l  %d0,%d1
        lsr.l   #8,%d1
        move.l  %d1,S_FB(%a3)
        mvz.w   10(%a4),%d1              | RTIM -> the index envelope
        lsr.l   #8,%d1
        beq     sy_nodecay
        move.l  %d1,%d2
        mulu.l  %d2,%d1                  | raw^2
        move.l  #K_NUM,%d0
        divu.l  %d1,%d0                  | k, Q20 per frame
        cmpi.l  #K_MAX,%d0
        ble     sy_env1
        move.l  #K_MAX,%d0
sy_env1:
        move.l  S_ENV(%a3),%d1
        subi.l  #ENV_FLOOR,%d1           | E - floor
        ble     sy_envdone               | at the floor: stays
        lsr.l   #8,%d1
        lsr.l   #4,%d1
        mulu.l  %d0,%d1
        lsr.l   #8,%d1                   | (E - floor) * k
        sub.l   %d1,S_ENV(%a3)
        bra     sy_envdone
sy_nodecay:
        move.l  #ENV_ONE,%d1             | RTIM 0: the index holds
        move.l  %d1,S_ENV(%a3)
sy_envdone:
        mvz.w   4(%a4),%d1               | LEN -> index
        move.l  #INDEX_SCALE,%d0
        mulu.l  %d0,%d1
        lsr.l   #8,%d1                   | I (raw * 2628, fractions count)
        move.l  S_ENV(%a3),%d2
        lsr.l   #8,%d2
        lsr.l   #4,%d2                   | E, 0..4096
        mulu.l  %d2,%d1
        lsr.l   #8,%d1
        lsr.l   #4,%d1
        move.l  %d1,S_IEFF(%a3)          | I * E
        move.l  S_GAIN(%a3),%d1          | the start ramp
        addi.l  #RAMP_STEP,%d1
        cmpi.l  #32768,%d1
        ble     sy_gain1
        move.l  #32768,%d1
sy_gain1:
        move.l  %d1,S_GAIN(%a3)

| ---- the samples --------------------------------------------------------------
sy_check:
        tst.b   S_ON(%a3)
        beq     sy_done
        move.l  52(%sp),%d2              | track (the rate block used d2)
        move.l  #VOICE_STRIDE,%d3
        muls.l  %d2,%d3
        lea     VOICE_BASE,%a0
        tst.b   (%a0,%d3.l)              | the CF voice ended: leave stock's silence
        beq     sy_done
        mvz.b   3(%a2),%d7               | source samples shipped by this call
        beq     sy_done
        lea     16(%a2),%a1              | their first L long
        tst.b   POLY_AT
        bne     po_fill                  | POLY: sum the voices (ends at sy_done)
        move.l  S_PHC(%a3),%d0
        move.l  S_PHM(%a3),%d6
        move.l  S_INC(%a3),%d5
        move.l  S_INCM(%a3),%a2
        move.l  S_LASTM(%a3),%a4
        lea     sy_tab(%pc),%a0
        moveq   #24,%d3
sy_loop:
        move.l  %a4,%d1
        muls.l  S_FB(%a3),%d1            | feedback: m_prev * fb
        add.l   %d6,%d1                  | modulator phase
        move.l  %d1,%d2
        lsr.l   %d3,%d2
        add.l   %d2,%d2
        mvs.w   (%a0,%d2.l),%d4          | a = tab[i]
        mvs.w   2(%a0,%d2.l),%d2         | b = tab[i+1]
        sub.l   %d4,%d2
        lsr.l   #8,%d1
        mvz.w   %d1,%d1                  | fraction, Q16
        muls.l  %d1,%d2
        asr.l   #8,%d2
        asr.l   #8,%d2
        add.l   %d2,%d4                  | m, Q14
        move.l  %d4,%a4                  | m_prev
        muls.l  S_IEFF(%a3),%d4          | m * I: the phase offset, Q32 cycles (wraps: it is a phase)
        add.l   %d0,%d4                  | carrier phase, modulated
        move.l  %d4,%d2
        lsr.l   %d3,%d2
        add.l   %d2,%d2
        mvs.w   (%a0,%d2.l),%d1          | a
        mvs.w   2(%a0,%d2.l),%d2         | b
        sub.l   %d1,%d2
        lsr.l   #8,%d4
        mvz.w   %d4,%d4
        muls.l  %d4,%d2
        asr.l   #8,%d2
        asr.l   #8,%d2
        add.l   %d2,%d1                  | c, Q14 (+-0x4000 = -6 dBFS)
        muls.l  S_GAIN(%a3),%d1          | * gain, Q15
        add.l   %d1,%d1                  | (c * g) << 1: the high word is (c * g) >> 15
        clr.w   %d1                      | sample << 16: the DSP's 24-bit word is the top 24 bits
        move.l  %d1,(%a1)+               | L
        move.l  %d1,(%a1)+               | R
        add.l   %d5,%d0
        add.l   %a2,%d6
        subq.l  #1,%d7
        bne     sy_loop
        move.l  %d0,S_PHC(%a3)
        move.l  %d6,S_PHM(%a3)
        move.l  %a4,S_LASTM(%a3)
sy_done:
        move.l  44(%sp),%d0
        movem.l (%sp),%d2-%d7/%a2-%a6
        lea     48(%sp),%sp
        rts

| ---- po_rate: d0 := C4_INC * rate, the rate as the stock renderer computes it ----
| (0x4000409e..0x40004104): d6 = the PTCH word (raw << 8, 0x0400..0x7c00), d5 =
| the RATE word, a4 = the parameter record (its RATE-mode byte at +27).
| Clobbers d0, d1, d3, d4, a0 and acc0; d2, d5, d6, d7 survive.
po_rate:
        move.l  %d6,%d0
        mov3q.l #1,%d3                   | PTCH <= 0 semitones: the curve's lower half, halved
        cmpi.w  #0x4000,%d0
        ble     po_rate1
        subi.l  #0x3c00,%d0
        moveq   #0,%d3
po_rate1:
        move.l  %d0,%d1
        asr.l   #5,%d0
        lea     PITCH_TAB,%a0
        lea     (%a0,%d0.l*4),%a0
        moveq   #27,%d4
        lsl.l   %d4,%d1
        move.l  (%a0)+,%d4
        lsr.l   #1,%d1                   | the 5-bit fraction, Q31
        move.l  %d4,%acc0
        msac.l  %d1,%d4,(%a0)+,%d4,%acc0 | t0 - frac*t0, then t1
        mac.l   %d1,%d4,%acc0            | + frac*t1
        mvz.b   27(%a4),%d4              | the RATE-mode byte: 0 = RATE scales the pitch
        bne     po_rate2
        move.l  %d5,%d4
        cmpi.w  #0x7f00,%d4
        bge     po_rate2
        movclr.l %acc0,%d0
        swap    %d4
        lsl.l   #2,%d4
        bcc     po_rate3
        lsr.l   #1,%d4
        neg.l   %d4
        bra     po_rate4
po_rate3:
        lsr.l   #1,%d4
        addi.l  #0x80000000,%d4
po_rate4:
        msac.l  %d0,%d4,%acc0
po_rate2:
        movclr.l %acc0,%d0
        asr.l   %d3,%d0                  | rate, Q26 (0x04000000 = 1.0)
        move.l  #C4_INC,%d1
        mac.l   %d1,%d0,%acc0            | C4_INC * rate / 32 (fractional mode: >> 31)
        movclr.l %acc0,%d0
        lsl.l   #5,%d0
        rts

| ---- sy_glide: d0 := the track's GLIDE value, 0 = off, 1..127 -----------------
sy_glide:
        mvz.b   GLIDE_AT,%d0
        rts

| ---- sy_slew: d6 := the PTCH word slewed toward d6 (GLIDE) ---------------------
| a3 = the record whose +40 (S_CUR / V_CUR) is the current word, Q12. GLIDE
| off: S_CUR := the target. GLIDE g: cur += (target - cur) * k, k = T / tau,
| tau = 10 ms * 100^((g - 1) / 126) (10 ms at 1, 100 ms at 64, 1 s at 127); k
| in Q16 from the stock 2^x curve PITCH_TAB (entry i = 2 * 2^((i - 512) /
| 480), Q26, so 2^f = entry 32 + 480 f): e = 127 - g, x = e * log2(100) / 126
| = n + f, k = 23.78 * 2^n * 2^f. The step is (|diff| >> 8) * k >> 8.
| Clobbers d0, d1, d3, d7, a0.
sy_slew:
        bsr     sy_glide
        move.l  %d0,%d1                  | g
        move.l  %d6,%d0
        lsl.l   #8,%d0
        lsl.l   #4,%d0                   | the target, Q12
        tst.l   %d1
        beq     sy_sl_snap
        moveq   #127,%d3
        sub.l   %d1,%d3                  | e = 127 - g
        move.l  #3456,%d7                | log2(100) / 126 * 2^16
        mulu.l  %d7,%d3                  | x, Q16
        move.l  %d3,%d1
        swap    %d1
        mvz.w   %d1,%d1                  | n = 0..6
        mvz.w   %d3,%d3                  | f, Q16
        move.l  #480,%d7
        mulu.l  %d7,%d3
        swap    %d3
        mvz.w   %d3,%d3                  | 480 f = the table index above entry 32
        lea     PITCH_TAB+128,%a0        | entry 32 = 2^0
        move.l  (%a0,%d3.l*4),%d3        | 2^f, Q26
        lsr.l   #8,%d3
        lsr.l   #2,%d3                   | Q16
        move.l  #6087,%d7                | 23.78 * 256
        mulu.l  %d7,%d3
        moveq   #24,%d7
        sub.l   %d1,%d7
        lsr.l   %d7,%d3                  | k = 23.78 * 2^n * 2^f, Q16
        move.l  S_CUR(%a3),%d1           | the current word, Q12
        sub.l   %d1,%d0                  | diff = target - current
        bpl     sy_sl_up
        neg.l   %d0
        lsr.l   #8,%d0
        mulu.l  %d3,%d0
        lsr.l   #8,%d0
        sub.l   %d0,%d1
        bra     sy_sl_store
sy_sl_up:
        lsr.l   #8,%d0
        mulu.l  %d3,%d0
        lsr.l   #8,%d0
        add.l   %d0,%d1
        bra     sy_sl_store
sy_sl_snap:
        move.l  %d0,%d1                  | off: no lag
sy_sl_store:
        move.l  %d1,S_CUR(%a3)
        lsr.l   #8,%d1
        lsr.l   #4,%d1
        move.l  %d1,%d6                  | the slewed PTCH word
        rts

| ==== POLY: the voices' frame (the second call, after the stock call) ============
| In: d2 = track, a3 = the track record, a4 = fp, d6 = the PTCH word, d5 = the
| RATE word. Preserves a2, a3, d2 (sy_check needs them). Uses a5 = the track
| record, a6 = the voice being updated, a2 = the end of the track's voices.
po_frame:
        lea     -8(%sp),%sp
        movem.l %a2/%a3,(%sp)
        move.l  %a3,%a5
        move.l  %d6,T_W(%a5)
| ---- keys released since last frame -> their voices release ----------------
        lea     KEYS_AT+8,%a0
        move.l  (%a0,%d2.l*4),%d0        | the held-key mask now
        move.l  T_MASK(%a5),%d1
        move.l  %d0,T_MASK(%a5)
        not.l   %d0
        and.l   %d0,%d1                  | gone = held then & ~held now
        beq     po_fr_start
        bsr     po_voices_of             | a6 = the track's voices, a2 = their end
po_fr_gone:
        mvz.b   V_STATE(%a6),%d0
        cmpi.l  #1,%d0
        bne     po_fr_gone1
        mvz.b   V_KEY(%a6),%d0
        beq     po_fr_gone1
        subq.l  #1,%d0
        btst    %d0,%d1
        beq     po_fr_gone1
        move.b  #2,V_STATE(%a6)          | that key's note releases
po_fr_gone1:
        lea     V_STRIDE(%a6),%a6
        cmp.l   %a2,%a6
        bne     po_fr_gone
po_fr_start:
| ---- a voice start this frame: the chord ------------------------------------
        lea     NIBBLE,%a0
        lea     (%a0,%d2.l),%a0
        btst    #4,(%a0)
        beq     po_fr_params
        bsr     po_start
po_fr_params:
| ---- the track's parameters this frame ----------------------------------------
        mvz.w   2(%a4),%d1               | STRT -> ratio
        lsr.l   #8,%d1
        lsr.l   #2,%d1
        lea     sy_ratio(%pc),%a0
        mvz.w   (%a0,%d1.l*2),%d1
        move.l  %d1,T_RATIO(%a5)
        mvz.w   8(%a4),%d1               | RTRG -> feedback
        move.l  #FB_SCALE,%d0
        mulu.l  %d0,%d1
        lsr.l   #8,%d1
        move.l  %d1,S_FB(%a5)
        mvz.w   10(%a4),%d1              | RTIM -> the index decay k (0 = hold)
        lsr.l   #8,%d1
        beq     po_fr_hold
        move.l  %d1,%d0
        mulu.l  %d0,%d1                  | raw^2
        move.l  #K_NUM,%d0
        divu.l  %d1,%d0
        cmpi.l  #K_MAX,%d0
        ble     po_fr_dk
        move.l  #K_MAX,%d0
        bra     po_fr_dk
po_fr_hold:
        moveq   #0,%d0
po_fr_dk:
        move.l  %d0,T_DK(%a5)
        mvz.w   4(%a4),%d1               | LEN -> index I
        move.l  #INDEX_SCALE,%d0
        mulu.l  %d0,%d1
        lsr.l   #8,%d1
        move.l  %d1,T_I(%a5)
        move.l  #CV_STRIDE,%d0           | AMP REL -> the release k
        muls.l  %d2,%d0
        lea     CURVALS,%a0
        mvz.b   CV_REL(%a0,%d0.l),%d0
        lea     po_relk(%pc),%a0
        mvz.w   (%a0,%d0.l*2),%d0
        move.l  %d0,T_RK(%a5)
| ---- every sounding voice: pitch, increments, envelopes ----------------------
        bsr     po_voices_of             | a6 = the voices, a2 = their end
po_fr_v:
        tst.b   V_STATE(%a6)
        beq     po_fr_next
        move.l  %a6,%a3                  | sy_slew works on +40 of a3
        move.l  T_W(%a5),%d6
        sub.l   T_REF(%a5),%d6
        add.l   V_ROOT(%a6),%d6          | the target word: the note plus what PTCH moved since its start
        bsr     sy_slew                  | d6 := slewed (clobbers d0, d1, d3, d7, a0)
        moveq   #0,%d7                   | octaves folded out of the word
po_fr_fold1:
        cmpi.l  #0x7c00,%d6              | above +12 semitones: an octave down, shift the increment up
        ble     po_fr_fold2
        subi.l  #0x3c00,%d6
        addq.l  #1,%d7
        bra     po_fr_fold1
po_fr_fold2:
        cmpi.l  #0x0400,%d6              | below -12: an octave up
        bge     po_fr_fold3
        addi.l  #0x3c00,%d6
        subq.l  #1,%d7
        bra     po_fr_fold2
po_fr_fold3:
        bsr     po_rate                  | d0 = the increment at the folded word (clobbers d0/d1/d3/d4/a0)
        tst.l   %d7
        beq     po_fr_inc
        bmi     po_fr_down
        lsl.l   %d7,%d0
        bra     po_fr_inc
po_fr_down:
        neg.l   %d7
        asr.l   %d7,%d0
po_fr_inc:
        move.l  %d0,V_INC(%a6)
        asr.l   #8,%d0
        muls.l  T_RATIO(%a5),%d0
        move.l  %d0,V_INCM(%a6)          | modulator increment = ratio * pitch
        move.l  T_DK(%a5),%d0            | the index envelope
        beq     po_fr_envhold
        move.l  V_ENV(%a6),%d1
        subi.l  #ENV_FLOOR,%d1
        ble     po_fr_env
        lsr.l   #8,%d1
        lsr.l   #4,%d1
        mulu.l  %d0,%d1
        lsr.l   #8,%d1
        sub.l   %d1,V_ENV(%a6)
        bra     po_fr_env
po_fr_envhold:
        move.l  #ENV_ONE,%d1
        move.l  %d1,V_ENV(%a6)
po_fr_env:
        move.l  T_I(%a5),%d1
        move.l  V_ENV(%a6),%d0
        lsr.l   #8,%d0
        lsr.l   #4,%d0
        mulu.l  %d0,%d1
        lsr.l   #8,%d1
        lsr.l   #4,%d1
        move.l  %d1,V_IEFF(%a6)          | I * E
        move.l  S_FB(%a5),%d0
        move.l  %d0,V_FB(%a6)
        move.l  V_GAIN(%a6),%d1          | the amplitude envelope
        mvz.b   V_STATE(%a6),%d0
        cmpi.l  #2,%d0
        beq     po_fr_rel
        addi.l  #RAMP4,%d1               | sounding: the attack ramp, then full
        cmpi.l  #GAIN4,%d1
        ble     po_fr_gain
        move.l  #GAIN4,%d1
        bra     po_fr_gain
po_fr_rel:
        move.l  T_RK(%a5),%d0            | releasing: gain -= gain * k
        move.l  %d1,%d3
        mulu.l  %d0,%d3
        lsr.l   #8,%d3
        lsr.l   #8,%d3
        bne     po_fr_rel1
        tst.l   %d0
        beq     po_fr_rel1               | INF: holds
        moveq   #1,%d3                   | a step too small to show: still make progress
po_fr_rel1:
        sub.l   %d3,%d1
        cmpi.l  #64,%d1
        bgt     po_fr_gain
        moveq   #0,%d1                   | gone: the voice is free
        clr.b   V_STATE(%a6)
po_fr_gain:
        move.l  %d1,V_GAIN(%a6)
po_fr_next:
        lea     V_STRIDE(%a6),%a6
        cmp.l   %a2,%a6
        bne     po_fr_v
        move.l  T_W(%a5),%d0
        move.l  %d0,T_LAST(%a5)
        movem.l (%sp),%a2/%a3
        lea     8(%sp),%sp
        rts

| ---- po_voices_of: a6 = track d2's four voice records, a2 = their end ---------
po_voices_of:
        lea     po_voices(%pc),%a6
        move.l  %d2,%d0
        lsl.l   #8,%d0                   | track * 4 * 64
        add.l   %d0,%a6
        lea     4*V_STRIDE(%a6),%a2
        rts

| ---- po_start: a voice starts -- allocate the chord's voices --------------------
| a5 = the track record, d2 = track, T_W = this frame's PTCH word (the new
| note's pitch), T_MASK = the held keys now. Clobbers d0, d1, d3, d4, d6, d7,
| a0, a1, a6, a2.
po_start:
        bsr     po_voices_of             | (clobbers d0)
        move.l  T_LAST(%a5),%d1
        sub.l   T_REF(%a5),%d1           | what PTCH moved since the last start:
po_st_fold:                              | fold it into the sounding voices' roots
        tst.b   V_STATE(%a6)
        beq     po_st_fold1
        add.l   %d1,V_ROOT(%a6)
po_st_fold1:
        lea     V_STRIDE(%a6),%a6
        cmp.l   %a2,%a6
        bne     po_st_fold
        move.l  T_W(%a5),%d1
        move.l  %d1,T_REF(%a5)
        lea     KEYS_AT,%a0
        mvz.b   (%a0,%d2.l),%d7          | the live key (index + 1) this trig came from, 0 = the sequencer
        clr.b   (%a0,%d2.l)              | (clr sets Z: test d7 after it)
        tst.l   %d7
        bne     po_st_chord
        bsr     po_voices_of             | a sequencer trig: the sounding notes release
po_st_rel:
        mvz.b   V_STATE(%a6),%d0
        cmpi.l  #1,%d0
        bne     po_st_rel1
        move.b  #2,V_STATE(%a6)
po_st_rel1:
        lea     V_STRIDE(%a6),%a6
        cmp.l   %a2,%a6
        bne     po_st_rel
po_st_chord:
        jsr     SCALE_AT                 | the quantizer's SCALE: its pitch-class mask, 0 = OFF (clobbers d0, a0)
        move.l  %d0,T_SCALE(%a5)
        move.l  #CV_STRIDE,%d0
        muls.l  %d2,%d0
        lea     CURVALS,%a0
        mvz.b   CV_VOIC(%a0,%d0.l),%d6   | VOIC: the notes a trig may take, locks applied; the byte
        bne     po_st_v1                 | is 1..4 once the knob has touched it, and a Part's stock
        moveq   #1,%d6                   | SPD3 default (32) or anything above reads as 4
po_st_v1:
        cmpi.l  #4,%d6
        ble     po_st_v2
        moveq   #4,%d6
po_st_v2:
        mvz.b   CV_CHRD(%a0,%d0.l),%d0   | the chord byte, locks applied
        lsr.l   #2,%d0                   | 0..31
        lea     po_shapes(%pc),%a1
        lea     (%a1,%d0.l*4),%a1        | the shape's offsets: the root first, so a count below
po_st_note:                              | the shape's size keeps the root and drops from the top
        mvs.b   (%a1)+,%d3               | semitones above the note; SHAPE_END ends the shape
        cmpi.l  #SHAPE_END,%d3
        beq     po_st_done
        bsr     po_alloc                 | a0 = the voice to use
        clr.l   V_PHM(%a0)               | as the mono voice's start: modulator, feedback, envelope,
        clr.l   V_LASTM(%a0)             | ramp restart; the carrier phase runs on
        clr.l   V_GAIN(%a0)
        move.l  #ENV_ONE,%d0
        move.l  %d0,V_ENV(%a0)
        move.l  #SEMI,%d0
        muls.l  %d3,%d0
        add.l   T_W(%a5),%d0             | the note's word
        bsr     po_snap                  | ... on the scale, when one is set
        move.l  %d0,V_ROOT(%a0)
        lsl.l   #8,%d0
        lsl.l   #4,%d0
        move.l  %d0,V_CUR(%a0)           | a fresh note starts at its own pitch
        move.b  %d7,V_KEY(%a0)
        moveq   #1,%d0
        tst.l   %d7
        beq     po_st_state
        move.l  %d7,%d1
        subq.l  #1,%d1
        move.l  T_MASK(%a5),%d4
        btst    %d1,%d4                  | the key already went before its voice started:
        bne     po_st_state
        moveq   #2,%d0                   | release at once
po_st_state:
        move.b  %d0,V_STATE(%a0)
        lea     po_seq(%pc),%a6
        addq.l  #1,(%a6)
        move.l  (%a6),V_AGE(%a0)
        subq.l  #1,%d6
        bne     po_st_note
po_st_done:
        rts

| ---- po_snap: d0 := the note word d0 snapped onto the scale (T_SCALE, 0 = as is) --
| The quantizer's rule (quantizer.s qz_chrom): the note's pitch class -- its
| semitone number from PTCH raw 64, the scale's root -- is moved to the nearest
| degree of the mask, the lower candidate first at each distance (ties go
| down). The word keeps its fraction: only whole semitones are added. Uses
| d1, d4 (d2, d6 saved); a5 = the track record.
po_snap:
        move.l  T_SCALE(%a5),%d4
        beq     po_sn_ret                | SCALE OFF
        lea     -8(%sp),%sp
        move.l  %d2,(%sp)                | (po_start's track and note count)
        move.l  %d6,4(%sp)
        move.l  %d0,%d6
        subi.l  #0x4000,%d6
        addi.l  #0x280+120*SEMI,%d6      | word - root + half a semitone, biased by 120 semitones
        move.l  #SEMI,%d2
        divu.l  %d2,%d6                  | n + 120: the nearest semitone number from the root
        addi.l  #1080,%d6                | n + 1200
        move.l  %d6,%d1
        moveq   #12,%d2
        divu.l  %d2,%d1                  | / 12 ...
        move.l  %d1,%d2
        add.l   %d2,%d2
        add.l   %d1,%d2                  | 3 q ...
        lsl.l   #2,%d2                   | ... * 4 = 12 q
        sub.l   %d2,%d6
        move.l  %d6,%d2                  | pc = 0..11
        moveq   #0,%d1                   | the distance
po_sn_dist:
        move.l  %d2,%d6
        sub.l   %d1,%d6                  | the lower candidate first
        bpl     po_sn_lo
        addi.l  #12,%d6
po_sn_lo:
        btst    %d6,%d4
        bne     po_sn_down
        move.l  %d2,%d6
        add.l   %d1,%d6                  | then the upper
        cmpi.l  #12,%d6
        blt     po_sn_hi
        subi.l  #12,%d6
po_sn_hi:
        btst    %d6,%d4
        bne     po_sn_up
        addq.l  #1,%d1
        cmpi.l  #6,%d1
        ble     po_sn_dist
        moveq   #0,%d1                   | no degree at all (cannot happen: every mask has the root)
        bra     po_sn_out
po_sn_down:
        neg.l   %d1
po_sn_up:
        move.l  #SEMI,%d6
        muls.l  %d1,%d6
        add.l   %d6,%d0                  | the word moved by whole semitones, its fraction kept
po_sn_out:
        move.l  (%sp),%d2
        move.l  4(%sp),%d6
        lea     8(%sp),%sp
po_sn_ret:
        rts

| ---- po_alloc: a0 := the voice of track d2 to take for a new note ---------------
| A free voice (state 0) first, else the oldest releasing one, else the oldest
| sounding one: the least (rank << 28 | age) with rank 0 / 1 / 2. Clobbers d0,
| d1, d4, a6; saves d3, d6 and a1 for po_start.
po_alloc:
        lea     -12(%sp),%sp
        movem.l %d3/%d6/%a1,(%sp)
        lea     po_voices(%pc),%a6
        move.l  %d2,%d0
        lsl.l   #8,%d0
        add.l   %d0,%a6
        move.l  %a6,%a0
        moveq   #-1,%d4                  | the best key so far (unsigned)
        moveq   #4,%d6
po_al_loop:
        mvz.b   V_STATE(%a6),%d0
        lea     po_rank(%pc),%a1
        mvz.b   (%a1,%d0.l),%d0
        lsl.l   #8,%d0
        lsl.l   #8,%d0
        lsl.l   #8,%d0
        lsl.l   #4,%d0                   | rank << 28
        move.l  V_AGE(%a6),%d3
        andi.l  #0x0fffffff,%d3
        or.l    %d0,%d3
        cmp.l   %d4,%d3
        bcc     po_al_next               | not better
        move.l  %d3,%d4
        move.l  %a6,%a0
po_al_next:
        lea     V_STRIDE(%a6),%a6
        subq.l  #1,%d6
        bne     po_al_loop
        movem.l (%sp),%d3/%d6/%a1
        lea     12(%sp),%sp
        rts
po_rank:
        .byte   0, 2, 1, 0
        .align  2

| ==== POLY: the samples -- every sounding voice summed into the record ===========
| From sy_check: a1 = the first L long, d7 = the source count, a3 = the track
| record, 52(sp) = track. Each voice adds c * gain (Q14) into the L long; the
| final pass doubles (the mono voice's Q15 format), saturates and copies to R.
po_fill:
        move.l  52(%sp),%d2              | track
        lea     -16(%sp),%sp
        move.l  %a1,(%sp)                | the first L long
        move.l  %d7,4(%sp)               | the count
        move.l  %a1,%a0
        move.l  %d7,%d0
po_fi_clear:
        clr.l   (%a0)
        addq.l  #8,%a0
        subq.l  #1,%d0
        bne     po_fi_clear
        bsr     po_voices_of             | a6 = the voices, a2 = their end
        move.l  %a2,8(%sp)
po_fi_voice:
        tst.b   V_STATE(%a6)
        beq     po_fi_next
        move.l  %a6,%a3
        move.l  (%sp),%a1
        move.l  4(%sp),%d7
        move.l  V_PHC(%a3),%d0
        move.l  V_PHM(%a3),%d6
        move.l  V_INC(%a3),%d5
        move.l  V_INCM(%a3),%a2
        move.l  V_LASTM(%a3),%a4
        lea     sy_tab(%pc),%a0
        moveq   #24,%d3
po_fi_loop:
        move.l  %a4,%d1
        muls.l  V_FB(%a3),%d1            | feedback: m_prev * fb
        add.l   %d6,%d1                  | modulator phase
        move.l  %d1,%d2
        lsr.l   %d3,%d2
        add.l   %d2,%d2
        mvs.w   (%a0,%d2.l),%d4          | a = tab[i]
        mvs.w   2(%a0,%d2.l),%d2         | b = tab[i+1]
        sub.l   %d4,%d2
        lsr.l   #8,%d1
        mvz.w   %d1,%d1                  | fraction, Q16
        muls.l  %d1,%d2
        asr.l   #8,%d2
        asr.l   #8,%d2
        add.l   %d2,%d4                  | m, Q14
        move.l  %d4,%a4                  | m_prev
        muls.l  V_IEFF(%a3),%d4          | m * I: the phase offset (wraps: it is a phase)
        add.l   %d0,%d4                  | carrier phase, modulated
        move.l  %d4,%d2
        lsr.l   %d3,%d2
        add.l   %d2,%d2
        mvs.w   (%a0,%d2.l),%d1          | a
        mvs.w   2(%a0,%d2.l),%d2         | b
        sub.l   %d1,%d2
        lsr.l   #8,%d4
        mvz.w   %d4,%d4
        muls.l  %d4,%d2
        asr.l   #8,%d2
        asr.l   #8,%d2
        add.l   %d2,%d1                  | c, Q14
        muls.l  V_GAIN(%a3),%d1          | * gain, Q14
        add.l   %d1,(%a1)                | into the sum
        addq.l  #8,%a1
        add.l   %d5,%d0
        add.l   %a2,%d6
        subq.l  #1,%d7
        bne     po_fi_loop
        move.l  %d0,V_PHC(%a3)
        move.l  %d6,V_PHM(%a3)
        move.l  %a4,V_LASTM(%a3)
po_fi_next:
        lea     V_STRIDE(%a6),%a6
        cmp.l   8(%sp),%a6
        bne     po_fi_voice
        move.l  (%sp),%a1                | the final pass: sum * 2, saturated, L and R
        move.l  4(%sp),%d7
po_fi_out:
        move.l  (%a1),%d1
        add.l   %d1,%d1
        bvs     po_fi_sat
po_fi_put:
        clr.w   %d1
        move.l  %d1,(%a1)+               | L
        move.l  %d1,(%a1)+               | R
        subq.l  #1,%d7
        bne     po_fi_out
        lea     16(%sp),%sp
        bra     sy_done
po_fi_sat:
        bmi     po_fi_satp               | the doubled sum wrapped: its sign says which way
        move.l  #0x80000000,%d1
        bra     po_fi_put
po_fi_satp:
        move.l  #0x7fff0000,%d1
        bra     po_fi_put

| ==== po_lfo3: the LFO engine's depth read (jmp, 8 bytes), twice ==================
| The engine exists twice in the OS, the same code: a routine at 0x40003b90
| and a copy inlined in the frame builder at 0x4000cf40 (the one that runs
| for the audio tracks' frames). At the depth read of both -- `mvsw
| %a2@(0x12,%d2:l:2),%d0; lea %a0@(0,%d4:l:2),%a1` at 0x40003ca4 and at
| 0x4000d03e -- d2 = the LFO (2 = LFO 3), a4 = the track's LFO state
| (LFO_STATE + 8 * track), a2 = its fp record, a0 = its staging record. With
| POLY on and the track playing a synth voice, LFO 3's depth reads as 0: the
| chord byte modulates nothing. d1/d3/d4/d7 are dead here (reloaded by the
| code that follows); a0 is done with. The stubs share po_lfo3_depth.
po_lfo3:
        bsr     po_lfo3_depth
        jmp     0x40003cac
po_lfo3b:
        bsr     po_lfo3_depth
        jmp     0x4000d046
po_lfo3_depth:
        mvs.w   0x12(%a2,%d2.l*2),%d0    | displaced: the depth
        lea     (%a0,%d4.l*2),%a1        | displaced
        cmpi.l  #2,%d2
        bne     po_l3_back
        move.l  %a4,%d1                  | (POLY on or off: slot 5 is the chord byte on a synth track,
        subi.l  #LFO_STATE,%d1
        lsr.l   #3,%d1                   | the track
        lsl.l   #7,%d1
        lea     sy_state(%pc),%a0        | and stock's depth on it would be a pitch LFO, 24 Sep 2026)
        tst.b   S_ON(%a0,%d1.l)          | its playing voice is a synth?
        beq     po_l3_back
        moveq   #0,%d0                   | muted
po_l3_back:
        rts

| ==== po_lfopage: the page resolver's LFO-descriptor load, 0x40031e62 (jmp, 8 B) ==
| `movel #0x400d37f6,%d0; bras 0x40031ed6`. d3 = track, d1 = part index, a1 =
| the bank blob, d5 = the machine byte (0x40031e2a); d2-d5 are restored by the
| epilogue at RESOLVER_RET, d0/d1/a0/a1 are C scratch. For a FLEX track whose
| assigned FLEX slot's sample is named SYNTH* (page.s's test) with POLY on, the
| clone -- built from the stock descriptor on first use -- is returned.
po_lfopage:
        move.l  #LFO_P,%d0               | displaced: the stock descriptor
        tst.b   POLY_AT
        beq     po_lp_done
        cmpi.l  #1,%d5                   | FLEX?
        bne     po_lp_done
        move.l  #6322,%d2
        muls.l  %d1,%d2                  | part * 6322
        add.l   %a1,%d2                  | + the bank blob
        move.l  %d3,%d4
        lsl.l   #2,%d4
        add.l   %d3,%d4                  | track * 5
        add.l   %d4,%d2
        move.l  %d2,%a0
        add.l   #SLOT_OFF,%a0
        mvz.b   (%a0),%d2                | the track's FLEX slot, 0-based
        cmpi.l  #127,%d2
        bhi     po_lp_done               | 0xff = none; 128.. = the recorder buffers
        move.l  #SETTINGS_STRIDE,%d4
        muls.l  %d2,%d4
        addi.l  #SETTINGS_BASE,%d4
        move.l  %d4,%a0                  | the slot's settings record; its path at +0
        move.l  %a0,%a1
        move.l  #255,%d4
po_lp_scan:
        mvz.b   (%a0)+,%d5
        beq     po_lp_scanned
        cmpi.l  #'/',%d5
        bne     po_lp_scan1
        move.l  %a0,%a1
po_lp_scan1:
        subq.l  #1,%d4
        bne     po_lp_scan
po_lp_scanned:
        lea     sy_name(%pc),%a0
        moveq   #5,%d4
po_lp_cmp:
        mvz.b   (%a0)+,%d5
        mvz.b   (%a1)+,%d2
        cmp.l   %d2,%d5
        bne     po_lp_done
        subq.l  #1,%d4
        bne     po_lp_cmp
        lea     po_lfodesc(%pc),%a1
        tst.b   po_lfo_built
        bne     po_lp_have
        lea     LFO_P,%a0                | the clone: the stock record ...
        move.l  #LFO_DESC_LEN,%d4
po_lp_copy:
        move.b  (%a0)+,(%a1)+
        subq.l  #1,%d4
        bne     po_lp_copy
        lea     po_lfodesc(%pc),%a1
        lea     po_chrd(%pc),%a0         | ... slot 5 named CHRD (6 bytes) ...
        move.l  (%a0)+,0x16+30(%a1)
        move.w  (%a0),0x16+34(%a1)
        lea     po_fmt_chord(%pc),%a0    | ... its formatter the shape's name ...
        move.l  %a0,0xca+20(%a1)
        lea     po_voic(%pc),%a0         | ... slot 2 (SPD3, LFO 3 being muted here) named VOIC ...
        move.l  (%a0)+,0x16+12(%a1)
        move.w  (%a0),0x16+16(%a1)
        lea     po_fmt_voic(%pc),%a0     | ... printing 1..4 ...
        move.l  %a0,0xca+8(%a1)
        moveq   #1,%d4                   | ... range 1..4, default 4 (the knob clamps to them;
        move.l  %d4,0x6a+8(%a1)          | a Part's stock byte 32 reads as 4 until turned) ...
        moveq   #4,%d4
        move.l  %d4,0x9a+8(%a1)
        move.b  %d4,0x5e+2(%a1)
        clr.l   0x12a+8(%a1)             | ... the stock enum stepper (handler 0, as PMTR/WAVE: one
                                         | step a detent; the accumulator handlers scale by the range) ...
        move.l  0x18e(%a1),%d4           | ... both always shown (nibbles 2 and 5, bit 2)
        andi.l  #0xff0ff0ff,%d4
        ori.l   #0x00500500,%d4
        move.l  %d4,0x18e(%a1)
        lea     po_lfo_built(%pc),%a0
        move.b  #1,(%a0)
po_lp_have:
        move.l  %a1,%d0
po_lp_done:
        jmp     RESOLVER_RET

| ---- po_fmt_voic: fmt(buf, value) -> "1".."4" (the byte clamped, as the engine reads it) --
po_fmt_voic:
        move.l  8(%sp),%d0
        bne     po_fv1
        moveq   #1,%d0
po_fv1:
        cmpi.l  #4,%d0
        ble     po_fv2
        moveq   #4,%d0
po_fv2:
        move.l  %d0,-(%sp)
        pea     po_f_d(%pc)
        move.l  12(%sp),-(%sp)           | buf
        jsr     SPRINTF
        lea     12(%sp),%sp
        rts

| ---- po_fmt_chord: fmt(buf, value) -> the shape's name, sprintf "%s" -----------
po_fmt_chord:
        move.l  8(%sp),%d0               | the value
        lsr.l   #2,%d0
        andi.l  #31,%d0
        move.l  %d0,%d1
        lsl.l   #2,%d1
        add.l   %d1,%d0                  | * 5
        lea     po_names(%pc),%a0
        lea     (%a0,%d0.l),%a0
        move.l  %a0,-(%sp)
        pea     po_f_s(%pc)
        move.l  12(%sp),-(%sp)           | buf
        jsr     SPRINTF
        lea     12(%sp),%sp
        rts

| ---- data ----------------------------------------------------------------------
sy_name:
        .ascii  "SYNTH"
po_chrd:
        .ascii  "CHRD\0\0"
po_voic:
        .ascii  "VOIC\0\0"
po_f_s:
        .asciz  "%s"
po_f_d:
        .asciz  "%d"
        .align  2

| ---- the ratio table: STRT raw >> 2 -> modulator/carrier ratio, Q8 ---------
sy_ratio:
        .short  64, 128, 192, 256, 259, 320, 362, 384      | 0.25 0.5 0.75 1 1.01 1.25 1.41 1.5
        .short  448, 512, 515, 640, 768, 896, 1024, 1027   | 1.75 2 2.01 2.5 3 3.5 4 4.01
        .short  1152, 1280, 1408, 1536, 1664, 1792, 1920, 2048   | 4.5 5 5.5 6 6.5 7 7.5 8
        .short  2304, 2560, 2816, 3072, 3328, 3584, 3840, 4096   | 9 10 11 12 13 14 15 16

| ---- the chord shapes: CHRD raw >> 2 -> up to four semitone offsets, -128 = end ----
| 0 single, the two-note intervals, the triads, their inversions and spreads,
| sixths and sevenths, extensions, the octave stacks -- four values of the
| knob per shape, so a sweep walks the table.
po_shapes:
        .byte   0, -128, -128, -128     |  0 ----  a single note
        .byte   0, 5, -128, -128        |  1 4TH
        .byte   0, 7, -128, -128        |  2 5TH
        .byte   0, 12, -128, -128       |  3 OCT
        .byte   0, 3, -128, -128        |  4 MI3   minor third
        .byte   0, 4, -128, -128        |  5 MA3   major third
        .byte   0, 10, -128, -128       |  6 MI7   minor seventh (the interval)
        .byte   0, 11, -128, -128       |  7 MA7   major seventh (the interval)
        .byte   0, 7, 12, -128          |  8 PWR   root, fifth, octave
        .byte   0, 4, 7, -128           |  9 MAJ
        .byte   0, 3, 7, -128           | 10 MIN
        .byte   0, 2, 7, -128           | 11 SUS2
        .byte   0, 5, 7, -128           | 12 SUS4
        .byte   0, 3, 6, -128           | 13 DIM
        .byte   0, 4, 8, -128           | 14 AUG
        .byte   0, 3, 8, -128           | 15 MAJ1  major, first inversion
        .byte   0, 5, 9, -128           | 16 MAJ2  major, second inversion
        .byte   0, 4, 9, -128           | 17 MIN1  minor, first inversion
        .byte   0, 5, 8, -128           | 18 MIN2  minor, second inversion
        .byte   0, 7, 16, -128          | 19 MAJS  major spread: root, fifth, tenth
        .byte   0, 7, 15, -128          | 20 MINS  minor spread
        .byte   0, 4, 7, 9              | 21 MAJ6
        .byte   0, 3, 7, 9              | 22 MIN6
        .byte   0, 4, 7, 11             | 23 MAJ7
        .byte   0, 3, 7, 10             | 24 MIN7
        .byte   0, 4, 7, 10             | 25 DOM7
        .byte   0, 3, 6, 10             | 26 M7B5
        .byte   0, 3, 6, 9              | 27 DIM7
        .byte   0, 4, 7, 14             | 28 ADD9
        .byte   0, 7, 12, 19            | 29 5OCT  fifths and octaves
        .byte   0, 12, 24, -128         | 30 OCT2  three octaves
        .byte   0, 12, 24, 36           | 31 OCT3  four octaves
po_names:                                | 5 bytes each: the four-character name, NUL
        .ascii  "----\0" "4TH\0\0" "5TH\0\0" "OCT\0\0" "MI3\0\0" "MA3\0\0" "MI7\0\0" "MA7\0\0"
        .ascii  "PWR\0\0" "MAJ\0\0" "MIN\0\0" "SUS2\0" "SUS4\0" "DIM\0\0" "AUG\0\0" "MAJ1\0"
        .ascii  "MAJ2\0" "MIN1\0" "MIN2\0" "MAJS\0" "MINS\0" "MAJ6\0" "MIN6\0" "MAJ7\0"
        .ascii  "MIN7\0" "DOM7\0" "M7B5\0" "DIM7\0" "ADD9\0" "5OCT\0" "OCT2\0" "OCT3\0"
        .align  2

| ---- po_relk: AMP REL raw -> the release k, Q16 per frame ---------------------
| gain -= gain * k: tau = 5 ms * 1000^(rel / 126), 127 = INF (k = 0).
po_relk:
        .short  4755, 4502, 4262, 4034, 3819, 3615, 3422, 3240
        .short  3067, 2903, 2749, 2602, 2463, 2332, 2207, 2090
        .short  1978, 1873, 1773, 1678, 1589, 1504, 1424, 1348
        .short  1276, 1208, 1143, 1082, 1025, 970, 918, 869
        .short  823, 779, 737, 698, 661, 626, 592, 561
        .short  531, 502, 476, 450, 426, 403, 382, 362
        .short  342, 324, 307, 290, 275, 260, 246, 233
        .short  221, 209, 198, 187, 177, 168, 159, 150
        .short  142, 135, 128, 121, 114, 108, 102, 97
        .short  92, 87, 82, 78, 74, 70, 66, 63
        .short  59, 56, 53, 50, 48, 45, 43, 40
        .short  38, 36, 34, 32, 31, 29, 27, 26
        .short  25, 23, 22, 21, 20, 19, 18, 17
        .short  16, 15, 14, 13, 13, 12, 11, 11
        .short  10, 10, 9, 9, 8, 8, 7, 7
        .short  7, 6, 6, 6, 5, 5, 5, 0

| ---- the sine table: 256 + 1 entries, s16, amplitude 0x4000 ---------------
        .balign 4
sy_tab:
        .short  0, 402, 804, 1205, 1606, 2006, 2404, 2801
        .short  3196, 3590, 3981, 4370, 4756, 5139, 5520, 5897
        .short  6270, 6639, 7005, 7366, 7723, 8076, 8423, 8765
        .short  9102, 9434, 9760, 10080, 10394, 10702, 11003, 11297
        .short  11585, 11866, 12140, 12406, 12665, 12916, 13160, 13395
        .short  13623, 13842, 14053, 14256, 14449, 14635, 14811, 14978
        .short  15137, 15286, 15426, 15557, 15679, 15791, 15893, 15986
        .short  16069, 16143, 16207, 16261, 16305, 16340, 16364, 16379
        .short  16384, 16379, 16364, 16340, 16305, 16261, 16207, 16143
        .short  16069, 15986, 15893, 15791, 15679, 15557, 15426, 15286
        .short  15137, 14978, 14811, 14635, 14449, 14256, 14053, 13842
        .short  13623, 13395, 13160, 12916, 12665, 12406, 12140, 11866
        .short  11585, 11297, 11003, 10702, 10394, 10080, 9760, 9434
        .short  9102, 8765, 8423, 8076, 7723, 7366, 7005, 6639
        .short  6270, 5897, 5520, 5139, 4756, 4370, 3981, 3590
        .short  3196, 2801, 2404, 2006, 1606, 1205, 804, 402
        .short  0, -402, -804, -1205, -1606, -2006, -2404, -2801
        .short  -3196, -3590, -3981, -4370, -4756, -5139, -5520, -5897
        .short  -6270, -6639, -7005, -7366, -7723, -8076, -8423, -8765
        .short  -9102, -9434, -9760, -10080, -10394, -10702, -11003, -11297
        .short  -11585, -11866, -12140, -12406, -12665, -12916, -13160, -13395
        .short  -13623, -13842, -14053, -14256, -14449, -14635, -14811, -14978
        .short  -15137, -15286, -15426, -15557, -15679, -15791, -15893, -15986
        .short  -16069, -16143, -16207, -16261, -16305, -16340, -16364, -16379
        .short  -16384, -16379, -16364, -16340, -16305, -16261, -16207, -16143
        .short  -16069, -15986, -15893, -15791, -15679, -15557, -15426, -15286
        .short  -15137, -14978, -14811, -14635, -14449, -14256, -14053, -13842
        .short  -13623, -13395, -13160, -12916, -12665, -12406, -12140, -11866
        .short  -11585, -11297, -11003, -10702, -10394, -10080, -9760, -9434
        .short  -9102, -8765, -8423, -8076, -7723, -7366, -7005, -6639
        .short  -6270, -5897, -5520, -5139, -4756, -4370, -3981, -3590
        .short  -3196, -2801, -2404, -2006, -1606, -1205, -804, -402
        .short  0

| ---- state (RAM) ---------------------------------------------------------------
        .align  4
sy_state:                                | 8 tracks x 128 bytes
        .fill   8 * ST_STRIDE, 1, 0
po_voices:                               | 8 tracks x 4 voices x 64 bytes
        .fill   8 * 4 * V_STRIDE, 1, 0
po_seq:                                  | the allocation stamp
        .long   0
po_lfo_built:
        .byte   0
        .align  4
po_lfodesc:                              | the LFO descriptor clone (built on first use)
        .fill   LFO_DESC_LEN, 1, 0
        .align  4
po_end:
