| SYNTH MACHINE -- ColdFire code cave, phase 2: the two-operator FM voice
| (22 Sep 2026). GNU as, -mcpu=5475.
|
| A FLEX track whose sample is named SYNTH* (the file name on the card, e.g.
| SYNTH.wav in any FLEX slot) has its sample data GENERATED here every frame
| instead of taken from the flex pool. Phase 1 wrote a sine at C4 and let the
| DSP resample it by the voice's rate; phase 2 generates a two-operator FM
| voice AT THE FINAL PITCH -- 16 samples a frame at rate 1.0 whatever PTCH
| and RATE are -- so the DSP's resampler is an identity (no interpolation
| images, no decimation aliasing, a constant cost) and PTCH with its locks,
| LFOs, scenes, chromatic keys and the quantizer still apply, because the
| rate is computed here from the same parameter record with the stock
| renderer's own arithmetic and tables. RATE, the AMP envelope, the filter,
| FX1/FX2, level, pan, mute and cue are the DSP's, as for a sample.
|
| THE VOICE. carrier = sin(phi_c + I * sin(phi_m + fb * m_prev)); m_prev is
| the modulator's previous sample (feedback). phi_c advances at the track's
| pitch, phi_m at ratio * pitch. The PLAYBACK page's remaining slots are the
| voice's parameters, read from the per-frame DSP parameter record fp
| (0x800062a8, the packer's per-track pointer; halfwords, each raw << 8):
|   fp[1] STRT  -> RATIO   sy_ratio[raw >> 2], 32 steps 0.25 .. 16 (Q8)
|   fp[2] LEN   -> INDEX   I = 8 rad * raw / 127 (fractions from LFOs count)
|   fp[4] RTRG  -> FEEDBACK  0 .. 0.25 cycle of the modulator's own output
|   fp[5] RTIM  -> DECAY   the index decays exponentially from I toward
|                          I/16; time constant 2 s * (raw/127)^2; 0 = none
|   fp[0] PTCH, fp[3] RATE -> the pitch, exactly as the stock renderer
| Locks, scenes and LFOs on those slots reach the voice every frame.
|
| HOW: the per-frame record packer (0x4000d3fc) renders every track's audio
| through a per-track renderer pointer taken from the kind table 0x400d6434
| (kind = machine type: 0 STATIC, 1 FLEX, 2 THRU, 3 NEIGHBOR, 4 PICKUP; 5-7
| silent). This cave replaces the FLEX entry (0x400d6438, stock 0x40004008,
| poked by the manifest's emit()). sy_render has the stock renderer's
| signature -- (track, ping, start, end), C convention, d0/d1/a0/a1 scratch,
| the record cursor at 0x80001c80 -- and, for a synth track, writes PTCH :=
| 0 semitones and RATE := 1.0 into fp around the stock call (the voice
| lifecycle, positions, streaming and the record's headers stay stock's, at
| rate 1.0: 16 source samples a frame), restores them, computes the true
| rate itself, and overwrites the source pairs the stock renderer shipped.
|
| The record a call writes (measured, README): a 16-byte header
|   +0  src_count (bits 0-7) | out_count << 8 [| out2 << 16 | out3 << 24]
|   +4  fractional phase   +8  rate, Q26 (0x04000000 = 1.0)   +12 tag
| then src_count source samples of 8 bytes each: L long, R long; the DSP
| takes the top 24 bits of each long (a 16-bit sample sits at bits 31..16).
| The packer calls the renderer twice per frame: [0,n) for the old voice and
| [n,16) for the new one, n = the sub-frame position of this frame's event
| (0x46104d0c + track, low nibble; BIT 4 SET = a voice starts this frame,
| still set during both calls, cleared by the packer afterwards). Between
| the two calls of a start frame the packer runs the start handler and
| latches the retrig count into the render state (0x800062a4, +4 := fp[4]);
| a synth voice clears it on the second call, so RTRG never retrigs the
| stock stream and is free to be FEEDBACK. The voice struct 0x800049d8 +
| 0xa8*track: +0 active byte (0 = the CF voice ended), +8 the sample's
| settings record (0x100b14f0 + 0x448*slot; its path string at +0).
|
| Position independent: OS absolutes and pc-relative references only.
| Layout (fixed with .org): +0x000 sy_render (the kind-table entry) and the
| ratio table (+0x30e), +0x350 sy_tab (257 x s16 sine, amplitude 0x4000 =
| -6 dBFS), +0x554 the per-track state (8 x 40 bytes); 1,684 bytes.

        .text
        .set    VOICE_BASE, 0x800049d8
        .set    VOICE_STRIDE, 0xa8
        .set    CURSOR, 0x80001c80
        .set    NIBBLE, 0x46104d0c
        .set    STOCK_RENDER, 0x40004008
        .set    FP_PTR, 0x800062a8       | the packer's per-track DSP parameter record
        .set    RS_PTR, 0x800062a4       | the packer's per-track render state
        .set    PITCH_TAB, 0x400aa294    | the stock 2^(x/12) curve, longs Q26 (index = PTCH word >> 5)
        .set    C4_INC, 25480119         | C4: 261.6256 / 44100 * 2^32
        .set    ENV_ONE, 0x01000000      | the index envelope's 1.0 (Q24)
        .set    ENV_FLOOR, 0x00100000    | it decays toward 1/16
        .set    K_NUM, 3068384           | k = K_NUM / RTIM^2, Q20 per frame: tau = 2 s at 127
        .set    K_MAX, 0xfffff
        .set    INDEX_SCALE, 2628        | 8 rad / 127 in cycles * 2^18 (offset = m_Q14 * I)
        .set    FB_SCALE, 516            | 0.25 cycle / 127 * 2^16
        .set    RAMP_STEP, 4096          | gain Q15: 0 -> 1.0 over 8 frames (2.9 ms)
        .set    ST_STRIDE, 40
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

| ---- sy_render(track, ping, start, end) ------------------------------------
sy_render:
        lea     -48(%sp),%sp
        movem.l %d2-%d7/%a2-%a6,(%sp)    | 44(sp) the stock return; args 52 track, 56 ping, 60 start, 64 end
        move.l  CURSOR,%a2               | the header this call writes
        move.l  52(%sp),%d2              | track
        move.l  #ST_STRIDE,%d3
        muls.l  %d2,%d3
        lea     sy_state(%pc),%a3
        add.l   %d3,%a3                  | a3 = this track's state
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

| ---- the rate, as the stock renderer computes it (0x4000409e..0x40004104) --
        move.l  %d6,%d0
        mov3q.l #1,%d3                   | PTCH <= 0 semitones: the curve's lower half, halved
        cmpi.w  #0x4000,%d0
        ble     sy_rate1
        subi.l  #0x3c00,%d0
        moveq   #0,%d3
sy_rate1:
        move.l  %d0,%d1
        asr.l   #5,%d0
        lea     PITCH_TAB,%a0
        lea     (%a0,%d0.l*4),%a0
        moveq   #27,%d2
        lsl.l   %d2,%d1
        move.l  (%a0)+,%d2
        lsr.l   #1,%d1                   | the 5-bit fraction, Q31
        move.l  %d2,%acc0
        msac.l  %d1,%d2,(%a0)+,%d2,%acc0 | t0 - frac*t0, then t1
        mac.l   %d1,%d2,%acc0            | + frac*t1
        mvz.b   27(%a4),%d2              | the RATE-mode byte: 0 = RATE scales the pitch
        bne     sy_rate2
        move.l  %d5,%d2
        cmpi.w  #0x7f00,%d2
        bge     sy_rate2
        movclr.l %acc0,%d0
        swap    %d2
        lsl.l   #2,%d2
        bcc     sy_rate3
        lsr.l   #1,%d2
        neg.l   %d2
        bra     sy_rate4
sy_rate3:
        lsr.l   #1,%d2
        addi.l  #0x80000000,%d2
sy_rate4:
        msac.l  %d0,%d2,%acc0
sy_rate2:
        movclr.l %acc0,%d0
        asr.l   %d3,%d0                  | rate, Q26 (0x04000000 = 1.0)
        move.l  #C4_INC,%d1
        mac.l   %d1,%d0,%acc0            | C4_INC * rate / 32 (fractional mode: >> 31)
        movclr.l %acc0,%d0
        lsl.l   #5,%d0
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

sy_name:
        .ascii  "SYNTH"
        .align  2

| ---- the ratio table: STRT raw >> 2 -> modulator/carrier ratio, Q8 ---------
sy_ratio:
        .short  64, 128, 192, 256, 259, 320, 362, 384      | 0.25 0.5 0.75 1 1.01 1.25 1.41 1.5
        .short  448, 512, 515, 640, 768, 896, 1024, 1027   | 1.75 2 2.01 2.5 3 3.5 4 4.01
        .short  1152, 1280, 1408, 1536, 1664, 1792, 1920, 2048   | 4.5 5 5.5 6 6.5 7 7.5 8
        .short  2304, 2560, 2816, 3072, 3328, 3584, 3840, 4096   | 9 10 11 12 13 14 15 16

| ---- the sine table: 256 + 1 entries, s16, amplitude 0x4000 ---------------
        .org    0x350
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

| ---- state (RAM: the main OS runs from DRAM): 8 tracks x 40 bytes ----------
        .align  4
sy_state:
        .fill   8 * 40, 1, 0
sy_end:
