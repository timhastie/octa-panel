| SCALE QUANTIZER -- ColdFire unit (13 Sep 2026). GNU as, -mcpu=5475.
|
| A per-project SCALE setting (PROJECT > CONTROL > SEQUENCER, fourth row,
| OFF by default) and three places it acts:
|   * the PTCH knob on the PLAYBACK page (STATIC / FLEX / PICKUP -- any page
|     of kind 0 whose slot A is named "PTCH") steps to the next scale degree
|     instead of one raw unit (raw = 64 + 5 * semitones, 4..124), on the
|     Part's value and, with a [TRIG] key held, on the step's lock;
|   * a [TRIG] key in CHROMATIC trig mode snaps to the nearest degree (ties
|     to the lower one) before the key becomes a pitch, so the voice, the
|     PTCH lock a held or live-recorded trig receives and the box on the
|     screen all carry the snapped value;
|   * the setting rides the project file as a comment line the stock loader
|     skips ("#SEQUENCER_SCALE=n" after PATTERN_CHANGE_CHAIN_BEHAVIOR).
| With SCALE = OFF every detour replays what it displaced and does nothing
| else; the setting byte lives in this unit (the main OS runs from DRAM).
|
| GLIDE (24 Sep 2026): a fifth SEQUENCER row, OFF / 1..127, the synth
| machine's glide time (modules/synth: 10 ms at 1 .. 1 s at 127), saved as
| "#SYNTH_GLIDE=n" after the SCALE line. The byte is qz_glide, pinned at a
| fixed address in glide.s (the synth cave reads it as an OS absolute); this
| unit reaches it by symbol, through qz_glide_of -- the ONE reader, so the
| storage can move. With GLIDE on, a CHROMATIC key pressed while another key
| of the track is held is a LEGATO note: the stock's trigless-trig path
| (the FUNC + key path) is taken, so the voice is not restarted and only the
| pitch changes (the synth glides to it), the old key's voice note-off is
| suppressed, and the new key becomes the track's held key, so releasing the
| first key does nothing and releasing the last one releases the note (the
| 303 rule). Two more detours in the chromatic key handler, qz_leg1 / qz_leg2.
|
| PARAPHONIC KEYS (24 Sep 2026): on a FLEX track whose assigned slot is a
| SYNTH* sample (qz_is_synth) and whose Part's VOIC byte -- the LFO page's
| slot 2, SPD3's byte, which the synth's page names VOIC -- is 2..4
| (qz_polytrack), the CHROMATIC keys are polyphonic: a press while keys are
| held starts a fresh voice and ends nothing (qz_leg1); every press posts the
| key for the engine (qz_pkey) and sets its bit in the track's held-key mask
| (qz_pmask, keys.s); a release clears the bit -- the engine releases that
| key's voices -- sends the key's MIDI note-off, and only the LAST key's
| release takes the stock note-off path, which posts the track's AMP release
| (qz_leg0, a third detour on the handler's release path). Legato is off on
| such a track. VOIC 1 (or a byte outside 1..4, the stock 32) and every
| other track: stock, byte for byte -- the mono synth with GLIDE legato.
|
| Linked by the build at the address it lands on (modules/quantizer/
| manifest.py names the sites); the only absolute references to itself are
| the qz_names entries, which the linker resolves.

        .text
        .global qz_knob, qz_plock, qz_chrom, qz_draw, qz_ld_entry, qz_ld_line, qz_wr
        .global qz_get, qz_set, qz_lbl_scale, qz_scale
        .global qz_get_glide, qz_set_glide, qz_lbl_glide, qz_leg1, qz_leg2
        .global qz_leg0, qz_leg3, qz_scale_mask
        .set    HELD, 0x460d171d          | the chromatic key handler's held key per track (key + 1; 0 = none)
        .set    FUNC_HELD, 0x46c7dd26
        .set    MIDI_NOTE, 0x4003f3a8     | (track, note, velocity): the key's MIDI note out
        .set    SPRINTF, 0x40013a08
        .set    FMT_D, 0x400b465d         | "%d"

| ---- the setting ---------------------------------------------------------------
qz_scale:
        .byte   0                       | 0 = OFF, 1..24 = index into qz_masks / qz_names
        .balign 2

| qz_glide_of: d0 := the GLIDE value, 0 = OFF, 1..127 (d2 = track, unused: the
| setting is per project). The one place this unit reads the storage
| (glide.s qz_glide, a fixed address; synth.s sy_glide is its twin).
qz_glide_of:
        mvz.b   qz_glide,%d0
        rts

| qz_scale_mask: d0 := the SCALE's pitch-class mask (bit k = semitone k above
| the root, qz_masks), 0 = OFF. The synth's paraphonic engine reaches it
| through the pinned trampoline scale.s to snap chord notes the way qz_chrom
| snaps a key. Clobbers a0 and d0.
qz_scale_mask:
        lea     qz_scale(%pc),%a0
        mvz.b   (%a0),%d0
        jbeq    qz_sm_ret
        lea     qz_masks(%pc),%a0
        add.l   %d0,%d0
        mvz.w   -2(%a0,%d0.l),%d0
qz_sm_ret:
        rts

| qz_polytrack: d0 := 1 (flags NE) when track d2 is a FLEX track whose
| assigned FLEX slot holds a SYNTH*-named sample AND its Part's VOIC byte
| (the LFO page's slot 2: Part + 0x8ee9a + track*24 + 2) is 2..4, else 0
| (EQ). Preserves everything but d0.
qz_polytrack:
        jbsr    qz_is_synth
        jbeq    qz_pt_ret
        move.l  %a0,-(%sp)
        move.l  %d1,-(%sp)
        movea.l 0x46c82456,%a0
        mvz.b   0x100b14cf,%d0
        move.l  #6322,%d1
        muls.l  %d1,%d0
        adda.l  %d0,%a0                 | the Part
        adda.l  #0x8ee9a,%a0            | its LFO page bytes
        move.l  %d2,%d1
        lsl.l   #3,%d1
        add.l   %d1,%d1
        add.l   %d1,%d1                 | track * 24 ... (8 + 16)
        move.l  %d2,%d0
        lsl.l   #3,%d0
        add.l   %d0,%d1                 | (16 + 8 = 24) ...
        mvz.b   2(%a0,%d1.l),%d0        | VOIC
        subq.l  #2,%d0
        cmpi.l  #2,%d0                  | 2..4?
        jbls    qz_pt_yes
        moveq   #0,%d0
        jbra    qz_pt_out
qz_pt_yes:
        moveq   #1,%d0
qz_pt_out:
        move.l  (%sp)+,%d1
        movea.l (%sp)+,%a0
        tst.l   %d0
qz_pt_ret:
        rts

| qz_is_synth: d0 := 1 (NE) when track d2's machine is FLEX and its assigned
| FLEX slot's settings record names a SYNTH* file -- the synth page's own test
| (modules/synth/page.s pg_resolve): slot = Part + 0x8f04a + track*5 + 1, its
| record 0x100b14f0 + 0x448*slot, the path at +0 scanned for the basename.
| Preserves everything but d0.
qz_is_synth:
        lea     -16(%sp),%sp
        movem.l %d1/%d3/%a0/%a1,(%sp)
        movea.l 0x46c82456,%a0
        mvz.b   0x100b14cf,%d0
        move.l  #6322,%d1
        muls.l  %d1,%d0
        adda.l  %d0,%a0                 | the Part
        move.l  %a0,%a1
        adda.l  #0x8eda2,%a1
        mvz.b   (%a1,%d2.l),%d0         | the track's machine
        cmpi.l  #1,%d0                  | FLEX
        jbne    qz_is_no
        move.l  %d2,%d0
        lsl.l   #2,%d0
        add.l   %d2,%d0                 | track * 5
        adda.l  %d0,%a0
        adda.l  #0x8f04b,%a0
        mvz.b   (%a0),%d0               | its FLEX slot, 0-based
        cmpi.l  #127,%d0
        jbhi    qz_is_no                | none, or a recorder buffer
        move.l  #0x448,%d1
        mulu.l  %d1,%d0
        addi.l  #0x100b14f0,%d0
        move.l  %d0,%a0                 | the settings record; its path at +0
        move.l  %a0,%a1
        move.l  #255,%d3
qz_is_scan:
        mvz.b   (%a0)+,%d0
        jbeq    qz_is_scanned
        cmpi.l  #'/',%d0
        jbne    qz_is_scan1
        move.l  %a0,%a1                 | after the last '/'
qz_is_scan1:
        subq.l  #1,%d3
        jbne    qz_is_scan
qz_is_scanned:
        lea     qz_synthname(%pc),%a0
        moveq   #5,%d3
qz_is_cmp:
        mvz.b   (%a0)+,%d0
        mvz.b   (%a1)+,%d1
        cmp.l   %d1,%d0
        jbne    qz_is_no
        subq.l  #1,%d3
        jbne    qz_is_cmp
        moveq   #1,%d0
        jbra    qz_is_out
qz_is_no:
        moveq   #0,%d0
qz_is_out:
        movem.l (%sp),%d1/%d3/%a0/%a1
        lea     16(%sp),%sp
        tst.l   %d0
        rts

| ---- the PTCH knob: jsr planted at 0x40055170 (the knob handler's store) -------
| Registers there (read off 0x40055008): d2 = the clamped new value, d6 = the
| value the knob was turned from (byte), a4 = the encoder delta, d5 = slot,
| a3 = the page descriptor (min at +0x6a, count at +0x9a, slot names at
| +0x16), a2 / a5 = the Part byte and its SRAM mirror. The three displaced
| instructions store d2 and load d1, so d1 is free here.
qz_knob:
        lea     qz_scale(%pc),%a0
        mvz.b   (%a0),%d0
        jbeq     qz_k_store              | OFF: stock
        tst.l   %d5                     | slot A only
        jbne     qz_k_store
        tst.l   0x460d1684              | page kind 0 = PLAYBACK
        jbne     qz_k_store
        move.l  0x16(%a3),%d1           | the slot's name: "PTCH" (not THRU / NEIGHBOR)
        cmpi.l  #0x50544348,%d1
        jbne     qz_k_store
        move.l  %a4,%d1                 | delta: detents, signed
        jbeq     qz_k_store
        mvz.b   %d6,%d2                 | start from the value the knob was turned from
        jbsr     qz_quant
qz_k_store:
        move.b  %d2,(%a2)               | displaced: the Part byte
        move.b  %d2,(%a5)               | displaced: its mirror
        move.w  %fp,%d1                 | displaced
        rts

| ---- the PTCH knob with a [TRIG] key held: jsr planted at 0x40050e60 --------------
| The p-lock editor (loops over the held steps; d6 = step, d7 = the rest) takes
| the step's lock byte -- or the Part's value when there is none (0xff) --
| through the slot's own handler and the descriptor clamp into d4, then
| stores it at (0x59,%a0,%a1.l) = track record + 0x59 + flat slot (a0 = the
| record, a1 = flat slot) and into the SRAM mirror after the hook. Same
| registers as above: d5 = slot, a3 = descriptor, fp = delta. The lock byte
| is still the old one when the hook runs, so the start value is read there.
qz_plock:
        move.l  %a2,-(%sp)
        lea     qz_scale(%pc),%a2
        mvz.b   (%a2),%d0
        jbeq     qz_p_done
        tst.l   %d5                     | slot A only
        jbne     qz_p_done
        tst.l   0x460d1684              | PLAYBACK page only
        jbne     qz_p_done
        move.l  0x16(%a3),%d1
        cmpi.l  #0x50544348,%d1         | "PTCH"
        jbne     qz_p_done
        move.l  %fp,%d1                 | delta
        jbeq     qz_p_done
        move.l  %d2,-(%sp)
        mvz.b   (0x59,%a0,%a1.l),%d2    | the step's lock before the store
        cmpi.l  #0xff,%d2
        jbne     qz_p_go                 | a lock: step from it
        move.l  %d3,-(%sp)              | none: from the Part's PTCH
        move.l  %d4,-(%sp)
        movea.l 0x46c82456,%a2
        mvz.b   0x100b14cf,%d2
        move.l  #6322,%d3
        muls.l  %d3,%d2
        adda.l  %d2,%a2                 | the Part
        adda.l  #0x8eda2,%a2            | its machine bytes
        mvz.b   0x100b14cc,%d4          | track
        mvz.b   (%a2,%d4.l),%d2         | machine
        move.l  %d2,%d3
        lsl.l   #3,%d3
        sub.l   %d2,%d3
        sub.l   %d2,%d3                 | machine * 6
        addq.l  #8,%a2                  | +0x8edaa: the PLAYBACK slots
        adda.l  %d3,%a2
        move.l  %d4,%d3
        lsl.l   #5,%d3
        sub.l   %d4,%d3
        sub.l   %d4,%d3                 | track * 30
        mvz.b   (%a2,%d3.l),%d2         | the Part's PTCH
        move.l  (%sp)+,%d4
        move.l  (%sp)+,%d3
qz_p_go:
        jbsr     qz_quant
        move.l  %d2,%d4                 | the value the store and the mirror take
        move.l  (%sp)+,%d2
qz_p_done:
        move.l  (%sp)+,%a2
        move.b  %d4,(0x59,%a0,%a1.l)    | displaced: the lock byte
        mvz.b   0x100b14cc,%d1          | displaced
        rts

| ---- qz_quant: d2 := d2 stepped |d1| degrees in the direction of d1 -------------
| d0 = the scale index (1..24), a3 = the descriptor (min at +0x6a, count at
| +0x9a for slot A). Each step goes to the next raw value on a semitone of the
| scale (raw = 64 + 5 * n; qz_pcraw maps raw - min to a pitch class); a value
| between degrees snaps to the nearest one in the turn direction; at the ends
| the value stays, which is the stock clamp. Preserves everything but d2.
qz_quant:
        move.l  %d0,-(%sp)
        move.l  %d1,-(%sp)
        move.l  %d3,-(%sp)
        move.l  %d4,-(%sp)
        move.l  %d5,-(%sp)
        move.l  %d6,-(%sp)
        move.l  %d7,-(%sp)
        move.l  %a0,-(%sp)
        move.l  %a1,-(%sp)
        lea     qz_masks(%pc),%a1
        add.l   %d0,%d0
        move.w  -2(%a1,%d0.l),%d3       | d3 = the scale's pitch-class mask
        move.l  0x6a(%a3),%d4           | d4 = the parameter's minimum (4)
        move.l  0x9a(%a3),%d5
        add.l   %d4,%d5
        subq.l  #1,%d5                  | d5 = its maximum (124)
        lea     qz_pcraw(%pc),%a0
        move.l  %d1,%d0                 | d0 = |delta| degrees to step
        jbpl     qz_k_loop
        neg.l   %d0
qz_k_loop:
        tst.l   %d1
        jbmi     qz_k_down
        jbsr     qz_k_up
        jbra     qz_k_next
qz_k_down:
        jbsr     qz_k_dn
qz_k_next:
        subq.l  #1,%d0
        jbne     qz_k_loop
        move.l  (%sp)+,%a1
        move.l  (%sp)+,%a0
        move.l  (%sp)+,%d7
        move.l  (%sp)+,%d6
        move.l  (%sp)+,%d5
        move.l  (%sp)+,%d4
        move.l  (%sp)+,%d3
        move.l  (%sp)+,%d1
        move.l  (%sp)+,%d0
        rts

| d2 := the first raw above (qz_k_up) / below (qz_k_dn) d2 that sits on a
| semitone of the scale, within [d4, d5]; unchanged when there is none.
| d6/d7 scratch, a0 = qz_pcraw.
qz_k_up:
        move.l  %d2,%d6
qz_k_up1:
        addq.l  #1,%d6
        cmp.l   %d5,%d6
        jbgt     qz_k_ret
        move.l  %d6,%d7
        sub.l   %d4,%d7
        mvz.b   (%a0,%d7.l),%d7         | pitch class, 0xff between semitones
        cmpi.l  #12,%d7
        jbcc     qz_k_up1
        btst    %d7,%d3
        jbeq     qz_k_up1
        move.l  %d6,%d2
qz_k_ret:
        rts
qz_k_dn:
        move.l  %d2,%d6
qz_k_dn1:
        subq.l  #1,%d6
        cmp.l   %d4,%d6
        jblt     qz_k_ret
        move.l  %d6,%d7
        sub.l   %d4,%d7
        mvz.b   (%a0,%d7.l),%d7
        cmpi.l  #12,%d7
        jbcc     qz_k_dn1
        btst    %d7,%d3
        jbeq     qz_k_dn1
        move.l  %d6,%d2
        rts

| ---- CHROMATIC trig mode: jsr planted at 0x4004fc58 (10 bytes) ----------------
| In 0x4004fb94 (track d2, key index a2 = 0..24 with 12 = the root, TRIG 13),
| the press path is about to turn the index into the raw pitch (5 * idx + 4)
| that becomes the lock byte the voice is trigged with (0x46c7dfda + t*32),
| the PTCH lock of a held or live-recorded trig (0x40042158 with a2) and the
| box on the screen. Snap the index first; the MIDI note the key sends out
| stays the key's own (d3, matched on release).
qz_chrom:
        lea     qz_scale(%pc),%a0
        mvz.b   (%a0),%d0
        jbeq     qz_c_replay
        move.l  %d2,-(%sp)
        move.l  %d3,-(%sp)
        move.l  %d4,-(%sp)
        lea     qz_masks(%pc),%a0
        add.l   %d0,%d0
        move.w  -2(%a0,%d0.l),%d3       | the scale's mask
        lea     qz_pc25(%pc),%a0
        move.l  %a2,%d2                 | the key index
        moveq   #0,%d4                  | distance: 0, 1, 2 ... -- lower candidate first
qz_c_loop:
        move.l  %d2,%d1
        sub.l   %d4,%d1
        jbmi     qz_c_hi
        mvz.b   (%a0,%d1.l),%d0
        btst    %d0,%d3
        jbne     qz_c_found
qz_c_hi:
        move.l  %d2,%d1
        add.l   %d4,%d1
        cmpi.l  #24,%d1
        jbhi     qz_c_more
        mvz.b   (%a0,%d1.l),%d0
        btst    %d0,%d3
        jbne     qz_c_found
qz_c_more:
        addq.l  #1,%d4
        cmpi.l  #24,%d4
        jbls     qz_c_loop
        move.l  %d2,%d1                 | no degree at all: cannot happen (every mask has the root)
qz_c_found:
        move.l  %d1,%a2
        move.l  (%sp)+,%d4
        move.l  (%sp)+,%d3
        move.l  (%sp)+,%d2
qz_c_replay:
        lea     (4,%a2,%a2.l*4),%a2     | displaced: raw = 5 * idx + 4
        mvz.b   0x100b14cf,%d0          | displaced: the part
        rts

| ---- GLIDE legato: two jmp detours in the same handler --------------------------
| 0x4004fb94 keeps ONE held key per track (HELD + track = key + 1). Stock, a
| press while a key is held first ends that key -- 0x4004fbfe..0x4004fc40: the
| voice note-off (mailbox 0x46c80354[track] |= 0x40, which the frame builder
| turns into the AMP release, 0x4000b4e4), the MIDI note-off (key + 71) and
| held := 0 -- and then starts a new voice (0x4004fcb2: 0x40005030, cmd 0x1d,
| bit 2 = start); a release of a key that is not the held one does nothing
| (0x4004fbe6). With FUNC held (FUNC_HELD) stock skips the note-off and posts
| a TRIGLESS trig instead (0x4004fc9c: mailbox |= 0x119, no bit 2: the staged
| PTCH lock 0x46c7dfda + track*32 is applied at the next frame, 0x4000b75c,
| and the voice is not restarted -- 0x4000b5a8 starts one only on bit 2).
|
| With GLIDE on, a press while a key is held takes that trigless path without
| FUNC: the old key's MIDI note-off goes out as stock, the voice note-off does
| not, and the NEW key becomes the held key (releasing the first key does
| nothing, releasing the last one releases the note as stock). GLIDE off, FUNC
| held, a release, or nothing held: stock, byte for byte.

| 0x4004fbfe: `tstl 0x46c7dd26; bnes 0x4004fc44` (8 bytes) -> jmp qz_leg1.
| d1 = the held key + 1 (nonzero here), d2 = track, d3 = the new key + 1
| (0 on a release); d0/a0 are free (reloaded by the stock code that follows).
qz_leg1:
        tst.l   FUNC_HELD
        jbne     qz_g1_skip              | FUNC held: stock skips the note-off block
        tst.l   %d3
        jbeq     qz_g1_stock             | a release: stock
        mvs.b   0x8000004c,%d0
        btst    #0,%d0
        jbeq     qz_g1_stock             | audio-track trigs off: stock
        jbsr     qz_polytrack            | a paraphonic synth track (VOIC 2..4): the held keys' voices
        jbne     qz_g1_skip              | keep sounding -- no note-off at all (theirs come with their releases)
        jbsr     qz_glide_of
        tst.l   %d0
        jbeq     qz_g1_stock             | GLIDE OFF: stock (the note-off, then a fresh trig)
        clr.l   -(%sp)                  | the old key's MIDI note-off, as stock's 0x4004fc24
        move.l  %d1,%a0
        pea     71(%a0)
        move.l  %d2,-(%sp)
        jsr     MIDI_NOTE
        lea     12(%sp),%sp
qz_g1_skip:
        jmp     0x4004fc44              | no voice note-off; the held key stays for qz_leg2
qz_g1_stock:
        jmp     0x4004fc06

| 0x4004fc94: `tstl 0x46c7dd26; beqs 0x4004fcb2` (8 bytes) -> jmp qz_leg2.
| d2 = track, d3 = the new key + 1; the pitch is staged (0x4004fc84..fc90).
| The legato press posts stock's own trigless word through 0x4004fc9c
| (`mailbox |= 0x119`). Measured 24 Sep 2026: that word does not restart the
| voice or the AMP envelope (a 64-step attack does not recur), but every
| trig word -- this one, FUNC + key, a fresh note -- is followed by the same
| 2.3 dB / 200 ms level step on the synth voice; posting 0x111 (mailbox bit
| 3 dropped: frame flag 0x20, 0x4000b5fc, event-byte bit 3, 0x4000c662)
| changed nothing about it, so the stock word is kept.
qz_leg2:
        lea     qz_legato(%pc),%a0
        clr.b   (%a0)                   | this press is not a legato one until decided below
        tst.l   FUNC_HELD
        jbne    qz_g2_trigless          | FUNC held: the stock trigless trig
        jbsr    qz_polytrack
        jbeq    qz_g2_glide
        lea     qz_pmask,%a0            | paraphonic: the key joins the held set, the engine is told
        move.l  %d3,%d0                 | which key this trig is (index + 1), and the stock trig
        subq.l  #1,%d0                  | starts a fresh voice -- no legato on such a track
        move.l  (%a0,%d2.l*4),%d1
        bset    %d0,%d1
        move.l  %d1,(%a0,%d2.l*4)
        lea     qz_pkey,%a0
        move.b  %d3,(%a0,%d2.l)
        jbra    qz_g2_trig
qz_g2_glide:
        jbsr    qz_glide_of
        tst.l   %d0
        jbeq    qz_g2_trig
        lea     HELD,%a0
        tst.b   (%a0,%d2.l)             | a key still held on this track (qz_leg1 kept it)?
        jbeq    qz_g2_trig              | no: a fresh note, the stock trig
        move.b  %d3,(%a0,%d2.l)         | legato: the new key is the held key
        lea     qz_legato(%pc),%a0
        move.b  %d3,(%a0)               | ... and the live recorder records it as trigless (qz_leg3)
qz_g2_trigless:
        jmp     0x4004fc9c
qz_g2_trig:
        jmp     0x4004fcb2

| 0x4004fce0: `tstl 0x46c7dd26; beqs 0x4004fcf8` (8 bytes) -> jmp qz_leg3. The
| same press, a few instructions on, is handed to the LIVE RECORDER (the
| block 0x4004fcd8..0x4004fd24 runs while 0x460d172a is set: live recording,
| or a trig held): stock records a TRIGLESS trig (0x4004271c) when FUNC is
| held and a sample trig (0x40042d1c) otherwise, then the PTCH lock on the
| step it returns (0x40042158 with a2). Without this hook a legato press --
| played trigless by qz_leg2 -- was recorded as a sample trig, so playback
| restarted the note (the user's report, OCTATRICK6). The legato press takes
| the trigless branch, exactly what FUNC + key records; everything else is
| stock (qz_legato is set only on the legato path, cleared at every press).
qz_leg3:
        tst.l   FUNC_HELD
        jbne    qz_g3_trigless
        lea     qz_legato(%pc),%a0
        tst.b   (%a0)
        jbeq    qz_g3_sample
qz_g3_trigless:
        jmp     0x4004fce8              | records a trigless trig
qz_g3_sample:
        jmp     0x4004fcf8              | records a sample trig

| 0x4004fbde, the release path: `mvzb %a0@(0,%d2:l),%d1; movel %a2,%d0` (6
| bytes) -> jmp qz_leg0. a0 = HELD, d2 = track, a2 = the key index; stock
| goes on at 0x4004fbe4 with d0 = the index and d1 = the held key + 1, and
| returns at once unless they match. On a paraphonic synth track the mask
| decides: the key leaves qz_pmask (the engine releases its voices); with keys
| still held the key's MIDI note-off goes out and nothing else happens (HELD
| stays, whichever key it names, so the next release still gets here); the
| LAST key makes itself the held key first, so stock's note-off block runs --
| the voice note-off (the AMP release), the MIDI note-off, HELD := 0. d4 is
| free here (set before every use in the handler).
qz_leg0:
        mvz.b   (%a0,%d2.l),%d1         | displaced
        move.l  %a2,%d0                 | displaced
        jbsr    qz_polytrack
        jbeq    qz_g0_stock
        lea     qz_pmask,%a0
        move.l  (%a0,%d2.l*4),%d0       | the held keys
        move.l  %a2,%d4
        btst    %d4,%d0
        jbeq    qz_g0_stock             | a key never seen pressed (VOIC was 1 then): stock
        bclr    %d4,%d0
        move.l  %d0,(%a0,%d2.l*4)
        jbne    qz_g0_more
        lea     HELD,%a0                | the last key: HELD := it, and stock ends the note
        addq.l  #1,%d4
        move.b  %d4,(%a0,%d2.l)
qz_g0_stock:
        lea     HELD,%a0
        mvz.b   (%a0,%d2.l),%d1         | as displaced
        move.l  %a2,%d0
        jmp     0x4004fbe4
qz_g0_more:
        clr.l   -(%sp)                  | this key's MIDI note-off (stock's form, 0x4004fc24)
        move.l  %a2,%d1
        addi.l  #72,%d1
        move.l  %d1,-(%sp)
        move.l  %d2,-(%sp)
        jsr     MIDI_NOTE
        lea     12(%sp),%sp
        jmp     0x4004fd68              | done: no voice note-off, the other keys sound on

| ---- the SEQUENCER window ------------------------------------------------------
| Its draw loop (0x40065b14) draws min(visible, count) rows but indexes the
| label / getter tables from 0, so a fourth row could never scroll into
| view: with the count grown to 5 (manifest poke; SCALE, GLIDE) and 3
| visible, start the index at the list's scroll offset instead. jmp planted
| at 0x40065bca.
qz_draw:
        move.l  0x460e43d8,%d2          | the list state's scroll offset
        lsl.l   #2,%d2                  | -> byte index into the pointer tables
        lea     32(%sp),%sp             | displaced
        jmp     0x40065bd0

| The fourth and fifth rows' getters and setters, reached through the grown
| tables (labels 0x400b27d0, getters 0x400b27dc, setters 0x400b282c). A getter
| returns the value's string (C scratch d0/d1/a0/a1). A setter is jumped to
| with (delta, wrap) at 4(%sp) / 8(%sp) exactly like CHAIN AFTER's 0x400659ec:
| the LEVEL knob passes its detents (x7 with FUNC) and wrap = 0, [YES] passes
| (1, 1); qz_set_any takes the byte in a0 and its maximum in d1.
qz_get:
        lea     qz_scale(%pc),%a0
        mvz.b   (%a0),%d0
        lea     qz_names(%pc),%a0
        move.l  (%a0,%d0.l*4),%d0
        rts
qz_get_glide:                           | "OFF", or the number printed into qz_gbuf
        jbsr     qz_glide_of             | (d2 is the draw loop's index, not a track: ignored)
        tst.l   %d0
        jbne     qz_gg_num
        lea     qz_n0(%pc),%a0
        move.l  %a0,%d0
        rts
qz_gg_num:
        move.l  %d0,-(%sp)
        pea     FMT_D
        pea     qz_gbuf(%pc)
        jsr     SPRINTF
        lea     12(%sp),%sp
        lea     qz_gbuf(%pc),%a0
        move.l  %a0,%d0
        rts
qz_set:
        lea     qz_scale(%pc),%a0
        moveq   #24,%d1
        jbra     qz_set_any
qz_set_glide:
        lea     qz_glide,%a0
        moveq   #127,%d1
qz_set_any:
        mvz.b   (%a0),%d0
        add.l   4(%sp),%d0
        tst.l   8(%sp)
        jbeq     qz_s_clamp
        cmp.l   %d1,%d0                 | wrap: past the maximum -> OFF, before OFF -> the maximum
        jbgt     qz_s_zero
        tst.l   %d0
        jbge     qz_s_store
        move.l  %d1,%d0
        jbra     qz_s_store
qz_s_clamp:
        cmp.l   %d1,%d0
        jble     qz_s_low
        move.l  %d1,%d0
qz_s_low:
        tst.l   %d0
        jbge     qz_s_store
qz_s_zero:
        moveq   #0,%d0
qz_s_store:
        move.b  %d0,(%a0)
        rts

| ---- the project file ------------------------------------------------------------
| The loader 0x400866c4 reads project.work line by line; a line starting
| with '#' is skipped at 0x400867a2 before any key is compared, on stock
| firmware too. Ours: "#SEQUENCER_SCALE=n" and "#SYNTH_GLIDE=n". Entry (jmp at
| 0x400866cc): a storing pass (second argument != 0) starts from OFF, so a
| project saved without the lines loads as OFF. Line (jmp at 0x400867a2):
| d3 = the line; d0/d1 must hold its first character when stock continues at
| 0x400867aa.
qz_ld_entry:
        move.l  1472(%sp),%d6           | displaced
        move.l  1476(%sp),%d0           | displaced
        jbeq     qz_e_back               | parse-only pass: leave the settings
        lea     qz_scale(%pc),%a0
        clr.b   (%a0)
        lea     qz_glide,%a0
        clr.b   (%a0)
qz_e_back:
        jmp     0x400866d4

qz_ld_line:
        move.b  1167(%sp),%d1           | displaced: the line's first character
        mvs.b   %d1,%d0                 | displaced
        moveq   #35,%d5                 | displaced: '#'
        cmp.l   %d0,%d5
        jbne     qz_l_back
        move.l  %d3,%a0
        lea     qz_key(%pc),%a1
        jbsr     qz_l_cmp
        jbeq     qz_l_scale
        move.l  %d3,%a0
        lea     qz_key2(%pc),%a1
        jbsr     qz_l_cmp
        jbeq     qz_l_glide
        moveq   #35,%d5                 | another comment: stock skips it
        mvs.b   %d1,%d0
qz_l_back:
        jmp     0x400867aa
qz_l_cmp:                               | Z := the line at a0 starts with the key at a1 (a0 past it)
        mvz.b   (%a1)+,%d0
        jbeq     qz_l_c_ret
        mvz.b   (%a0)+,%d5
        cmp.l   %d0,%d5
        jbeq     qz_l_cmp
qz_l_c_ret:
        rts
qz_l_scale:
        jbsr     qz_l_dec
        cmpi.l  #24,%d0
        jbls     qz_l_ok
        moveq   #0,%d0                  | out of range: OFF
qz_l_ok:
        lea     qz_scale(%pc),%a0
        jbra     qz_l_store
qz_l_glide:
        jbsr     qz_l_dec
        cmpi.l  #127,%d0
        jbls     qz_l_gok
        moveq   #0,%d0
qz_l_gok:
        lea     qz_glide,%a0
qz_l_store:
        tst.l   58(%sp)                 | parse-only pass: nothing is stored
        jbne     qz_l_next
        move.b  %d0,(%a0)
qz_l_next:
        jmp     0x40088224              | the loop's next line
qz_l_dec:                               | d0 := the decimal at a0 (after the '=')
        moveq   #0,%d0
        moveq   #10,%d5
qz_l_dig:
        mvz.b   (%a0)+,%d1
        subi.l  #48,%d1
        cmpi.l  #9,%d1
        jbhi     qz_l_d_ret
        mulu.l  %d5,%d0
        add.l   %d1,%d0
        jbra     qz_l_dig
qz_l_d_ret:
        rts

| The writer 0x40088882.. prints one "KEY=%d\r\n" per setting: a4 = sprintf
| (buffer d2, format, value), a3 = strlen, a2 = write (file d3). jmp planted
| at 0x400888aa, the start of PATTERN_CHANGE_AUTO_SILENCE_TRACKS's line, so
| ours follow PATTERN_CHANGE_CHAIN_BEHAVIOR. A failed write is not checked
| here; the stock line that follows checks its own.
qz_wr:
        lea     qz_scale(%pc),%a0
        mvz.b   (%a0),%d0
        lea     qz_fmt(%pc),%a0
        jbsr     qz_wr_line
        jbsr     qz_glide_of             | (d2 = the writer's buffer, not a track: the accessor ignores it)
        lea     qz_fmt2(%pc),%a0
        jbsr     qz_wr_line
        mvs.b   0x8000004f,%d1          | displaced
        move.l  %d1,-(%sp)              | displaced
        jmp     0x400888b2
qz_wr_line:                             | sprintf(buf, a0, d0); write(file, buf, strlen(buf))
        move.l  %d0,-(%sp)
        move.l  %a0,-(%sp)
        move.l  %d2,-(%sp)
        jsr     (%a4)
        move.l  %d2,-(%sp)
        jsr     (%a3)
        move.l  %d0,-(%sp)
        move.l  %d2,-(%sp)
        move.l  %d3,-(%sp)
        jsr     (%a2)
        lea     28(%sp),%sp
        rts

| ---- data ------------------------------------------------------------------------
qz_key:         .asciz  "#SEQUENCER_SCALE="
qz_fmt:         .asciz  "#SEQUENCER_SCALE=%d\r\n"
qz_key2:        .asciz  "#SYNTH_GLIDE="
qz_fmt2:        .asciz  "#SYNTH_GLIDE=%d\r\n"
qz_lbl_scale:   .asciz  "SCALE"
qz_lbl_glide:   .asciz  "GLIDE"
qz_synthname:   .ascii  "SYNTH"
        .balign 4
qz_gbuf:        .fill   8, 1, 0          | the GLIDE row's number (RAM)
qz_legato:      .byte   0                | the press in flight is a legato one (qz_leg2 -> qz_leg3)
        .balign 4
qz_names:                               | index 0..24 -> label, 7 characters at most (the value column is 33 px wide)
        .long   qz_n0, qz_n1, qz_n2, qz_n3, qz_n4, qz_n5, qz_n6, qz_n7, qz_n8, qz_n9, qz_n10, qz_n11, qz_n12, qz_n13, qz_n14, qz_n15, qz_n16, qz_n17, qz_n18, qz_n19, qz_n20, qz_n21, qz_n22, qz_n23, qz_n24
qz_n0:  .asciz  "OFF"
qz_n1:  .asciz  "MAJOR"
qz_n2:  .asciz  "DORIAN"
qz_n3:  .asciz  "PHRYGN"
qz_n4:  .asciz  "LYDIAN"
qz_n5:  .asciz  "MIXOLYD"
qz_n6:  .asciz  "MINOR"
qz_n7:  .asciz  "LOCRIAN"
qz_n8:  .asciz  "PENT.MN"
qz_n9:  .asciz  "PENT.MJ"
qz_n10:  .asciz  "MEL.MIN"
qz_n11:  .asciz  "HRM.MIN"
qz_n12:  .asciz  "WHOLE"
qz_n13:  .asciz  "BLUES"
qz_n14:  .asciz  "PHRYDOM"
qz_n15:  .asciz  "WH.DIM"
qz_n16:  .asciz  "HW.DIM"
qz_n17:  .asciz  "HUNGMIN"
qz_n18:  .asciz  "HIRAJOS"
qz_n19:  .asciz  "IN-SEN"
qz_n20:  .asciz  "IWATO"
qz_n21:  .asciz  "PELOG"
qz_n22:  .asciz  "DBLHARM"
qz_n23:  .asciz  "SUPRLOC"
qz_n24:  .asciz  "LYD.DOM"
        .balign 2
qz_masks:                               | scale 1..24 -> pitch-class mask, bit k = semitone k above the root
        .word   0x0ab5                    |  1 MAJOR    {0,2,4,5,7,9,11}
        .word   0x06ad                    |  2 DORIAN   {0,2,3,5,7,9,10}
        .word   0x05ab                    |  3 PHRYGIAN {0,1,3,5,7,8,10}
        .word   0x0ad5                    |  4 LYDIAN   {0,2,4,6,7,9,11}
        .word   0x06b5                    |  5 MIXOLYD  {0,2,4,5,7,9,10}
        .word   0x05ad                    |  6 MINOR    {0,2,3,5,7,8,10}
        .word   0x056b                    |  7 LOCRIAN  {0,1,3,5,6,8,10}
        .word   0x04a9                    |  8 PENT.MIN {0,3,5,7,10}
        .word   0x0295                    |  9 PENT.MAJ {0,2,4,7,9}
        .word   0x0aad                    | 10 MEL.MIN  {0,2,3,5,7,9,11}
        .word   0x09ad                    | 11 HARM.MIN {0,2,3,5,7,8,11}
        .word   0x0555                    | 12 WHOLE    {0,2,4,6,8,10}
        .word   0x04e9                    | 13 BLUES    {0,3,5,6,7,10}
        .word   0x05b3                    | 14 PHRYGDOM {0,1,4,5,7,8,10}
        .word   0x0b6d                    | 15 WH.DIM   {0,2,3,5,6,8,9,11}
        .word   0x06db                    | 16 HW.DIM   {0,1,3,4,6,7,9,10}
        .word   0x09cd                    | 17 HUNG.MIN {0,2,3,6,7,8,11}
        .word   0x018d                    | 18 HIRAJOSH {0,2,3,7,8}
        .word   0x04a3                    | 19 IN-SEN   {0,1,5,7,10}
        .word   0x0463                    | 20 IWATO    {0,1,5,6,10}
        .word   0x018b                    | 21 PELOG    {0,1,3,7,8}
        .word   0x09b3                    | 22 DBL.HARM {0,1,4,5,7,8,11}
        .word   0x055b                    | 23 SUPERLOC {0,1,3,4,6,8,10}
        .word   0x06d5                    | 24 LYD.DOM  {0,2,4,6,7,9,10}
qz_pc25:                                | chromatic key index 0..24 -> pitch class (12 = the root, TRIG 13)
        .byte   0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 0
qz_pcraw:                               | PTCH raw - 4 (0..120) -> pitch class when on a semitone (raw = 64 + 5*n), else 0xff
        .byte   0, 0xff, 0xff, 0xff, 0xff, 1, 0xff, 0xff, 0xff, 0xff
        .byte   2, 0xff, 0xff, 0xff, 0xff, 3, 0xff, 0xff, 0xff, 0xff
        .byte   4, 0xff, 0xff, 0xff, 0xff, 5, 0xff, 0xff, 0xff, 0xff
        .byte   6, 0xff, 0xff, 0xff, 0xff, 7, 0xff, 0xff, 0xff, 0xff
        .byte   8, 0xff, 0xff, 0xff, 0xff, 9, 0xff, 0xff, 0xff, 0xff
        .byte   10, 0xff, 0xff, 0xff, 0xff, 11, 0xff, 0xff, 0xff, 0xff
        .byte   0, 0xff, 0xff, 0xff, 0xff, 1, 0xff, 0xff, 0xff, 0xff
        .byte   2, 0xff, 0xff, 0xff, 0xff, 3, 0xff, 0xff, 0xff, 0xff
        .byte   4, 0xff, 0xff, 0xff, 0xff, 5, 0xff, 0xff, 0xff, 0xff
        .byte   6, 0xff, 0xff, 0xff, 0xff, 7, 0xff, 0xff, 0xff, 0xff
        .byte   8, 0xff, 0xff, 0xff, 0xff, 9, 0xff, 0xff, 0xff, 0xff
        .byte   10, 0xff, 0xff, 0xff, 0xff, 11, 0xff, 0xff, 0xff, 0xff
        .byte   0
        .balign 4
qz_end:
