| SYNTH MACHINE -- ColdFire code cave, phase 1 (22 Sep 2026). GNU as, -mcpu=5475.
|
| A FLEX track whose sample is named SYNTH* (the file name on the card, e.g.
| SYNTH.wav in any FLEX slot) has its sample data GENERATED here every frame
| instead of taken from the flex pool: a sine at C4 (261.6256 Hz) as the
| SOURCE waveform, which the DSP then resamples by the voice's rate exactly
| as it would a sample -- so PTCH (with locks, LFOs, scenes, chromatic keys,
| the quantizer), RATE, retrigs, the AMP envelope, the filter, FX1/FX2,
| level, pan, mute and cue all apply for free. Nothing else changes.
|
| HOW: the per-frame record packer (0x4000d3fc) renders every track's audio
| through a per-track renderer pointer taken from the kind table 0x400d6434
| (kind = machine type: 0 STATIC, 1 FLEX, 2 THRU, 3 NEIGHBOR, 4 PICKUP; 5-7
| silent). This cave replaces the FLEX entry (0x400d6438, stock 0x40004008,
| poked by the manifest's emit()). sy_render has the stock renderer's
| signature -- (track, ping, start, end), C convention, d0/d1/a0/a1 scratch,
| the record cursor at 0x80001c80 -- calls the stock renderer first (so the
| voice lifecycle, positions, retrigs, streaming and the record's headers are
| all stock's), then, for a synth track whose ColdFire voice is active,
| overwrites the SOURCE pairs it shipped with the generated waveform.
|
| The record a call writes (measured, README): a 16-byte header
|   +0  src_count (bits 0-7) | out_count << 8 [| out2 << 16 | out3 << 24]
|   +4  fractional phase   +8  rate, Q26 (0x04000000 = 1.0)   +12 tag
| then src_count source samples of 8 bytes each: L long, R long; the DSP
| takes the top 24 bits of each long (a 16-bit sample sits at bits 31..16).
| The packer calls the renderer twice per frame: [0,n) for the old voice and
| [n,16) for the new one, n = the sub-frame position of this frame's event
| (0x46104d0c + track, low nibble; BIT 4 SET = a voice starts this frame,
| still set during both calls, cleared by the packer afterwards). The voice
| struct 0x800049d8 + 0xa8*track: +0 active byte (0 = the CF voice ended),
| +8 the sample's settings record (0x100b14f0 + 0x448*slot; its path string
| at +0, "../AUDIO/SYNTH.wav"), set by the start handler 0x4000f450 before
| the second call of the start frame.
|
| Position independent: OS absolutes and pc-relative references only.
| Layout (fixed with .org): +0x000 sy_render (the kind-table entry),
| +0x200 sy_tab (257 x s16 sine, amplitude 0x4000 = -6 dBFS), then state.

        .text
        .set    VOICE_BASE, 0x800049d8
        .set    VOICE_STRIDE, 0xa8
        .set    CURSOR, 0x80001c80
        .set    NIBBLE, 0x46104d0c
        .set    STOCK_RENDER, 0x40004008
        .set    PHASE_INC, 25480119         | C4: 261.6256 / 44100 * 2^32

| ---- sy_render(track, ping, start, end) ------------------------------------
sy_render:
        lea     -32(%sp),%sp
        movem.l %d2-%d7/%a2-%a3,(%sp)    | args now at 36 track, 40 ping, 44 start, 48 end
        move.l  CURSOR,%a2               | the header this call writes
        move.l  48(%sp),-(%sp)           | end
        move.l  48(%sp),-(%sp)           | start
        move.l  48(%sp),-(%sp)           | ping
        move.l  48(%sp),-(%sp)           | track
        jsr     STOCK_RENDER
        lea     16(%sp),%sp
        move.l  %d0,%d7                  | the stock return value, handed back
        move.l  36(%sp),%d2              | track
        lea     sy_on(%pc),%a3
        moveq   #16,%d1
        cmp.l   48(%sp),%d1              | the frame's second call?
        bne     sy_check
        lea     NIBBLE,%a0
        lea     (%a0,%d2.l),%a0
        btst    #4,(%a0)                 | a voice starts this frame: resolve the marker
        beq     sy_check
        lea     sy_phase(%pc),%a1
        clr.l   (%a1,%d2.l*4)            | every note starts at phase 0
        move.l  #VOICE_STRIDE,%d3
        muls.l  %d2,%d3
        lea     VOICE_BASE,%a0
        move.l  8(%a0,%d3.l),%a0         | the new voice's settings record
        move.l  %a0,%d4
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
        mvz.b   (%a1)+,%d4
        cmp.l   %d4,%d1
        bne     sy_no
        subq.l  #1,%d3
        bne     sy_cmp
        moveq   #1,%d4
        bra     sy_set
sy_no:
        moveq   #0,%d4
sy_set:
        move.b  %d4,(%a3,%d2.l)          | sy_on[track]
sy_check:
        tst.b   (%a3,%d2.l)
        beq     sy_done
        move.l  #VOICE_STRIDE,%d3
        muls.l  %d2,%d3
        lea     VOICE_BASE,%a0
        tst.b   (%a0,%d3.l)              | the CF voice ended: leave stock's silence
        beq     sy_done
        mvz.b   3(%a2),%d6               | source samples shipped by this call
        beq     sy_done
        lea     16(%a2),%a1              | their first L long
        lea     sy_phase(%pc),%a0
        move.l  (%a0,%d2.l*4),%d0        | phase, Q32 cycles
        lea     sy_tab(%pc),%a0
        move.l  #PHASE_INC,%d5
        moveq   #24,%d3
sy_loop:
        move.l  %d0,%d1
        lsr.l   %d3,%d1                  | table index 0..255
        add.l   %d1,%d1
        mvs.w   (%a0,%d1.l),%d2          | a = tab[i]
        mvs.w   2(%a0,%d1.l),%d4         | b = tab[i+1]
        sub.l   %d2,%d4
        move.l  %d0,%d1
        lsr.l   #8,%d1
        mvz.w   %d1,%d1                  | fraction, Q16
        muls.l  %d1,%d4
        asr.l   #8,%d4
        asr.l   #8,%d4                   | (b - a) * frac >> 16
        add.l   %d4,%d2
        swap    %d2
        clr.w   %d2                      | sample << 16: the DSP's 24-bit word is the top 24 bits
        move.l  %d2,(%a1)+               | L
        move.l  %d2,(%a1)+               | R
        add.l   %d5,%d0
        subq.l  #1,%d6
        bne     sy_loop
        lea     sy_phase(%pc),%a0
        move.l  36(%sp),%d2
        move.l  %d0,(%a0,%d2.l*4)
sy_done:
        move.l  %d7,%d0
        movem.l (%sp),%d2-%d7/%a2-%a3
        lea     32(%sp),%sp
        rts

sy_name:
        .ascii  "SYNTH"
        .align  2

| ---- the sine table: 256 + 1 entries, s16, amplitude 0x4000 ---------------
        .org    0x200
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

| ---- state (RAM: the main OS runs from DRAM) -------------------------------
        .align  4
sy_on:
        .byte   0, 0, 0, 0, 0, 0, 0, 0   | per track: the playing voice is a synth
sy_phase:
        .long   0, 0, 0, 0, 0, 0, 0, 0   | per track: source phase, Q32 cycles
sy_end:
