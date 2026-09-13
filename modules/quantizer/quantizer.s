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
| Linked by the build at the address it lands on (modules/quantizer/
| manifest.py names the sites); the only absolute references to itself are
| the qz_names entries, which the linker resolves.

        .text
        .global qz_knob, qz_plock, qz_chrom, qz_draw, qz_ld_entry, qz_ld_line, qz_wr
        .global qz_get, qz_set, qz_lbl_scale, qz_scale

| ---- the setting ---------------------------------------------------------------
qz_scale:
        .byte   0                       | 0 = OFF, 1..24 = index into qz_masks / qz_names
        .balign 2

| ---- the PTCH knob: jsr planted at 0x40055170 (the knob handler's store) -------
| Registers there (read off 0x40055008): d2 = the clamped new value, d6 = the
| value the knob was turned from (byte), a4 = the encoder delta, d5 = slot,
| a3 = the page descriptor (min at +0x6a, count at +0x9a, slot names at
| +0x16), a2 / a5 = the Part byte and its SRAM mirror. The three displaced
| instructions store d2 and load d1, so d1 is free here.
qz_knob:
        lea     qz_scale(%pc),%a0
        mvz.b   (%a0),%d0
        beq     qz_k_store              | OFF: stock
        tst.l   %d5                     | slot A only
        bne     qz_k_store
        tst.l   0x460d1684              | page kind 0 = PLAYBACK
        bne     qz_k_store
        move.l  0x16(%a3),%d1           | the slot's name: "PTCH" (not THRU / NEIGHBOR)
        cmpi.l  #0x50544348,%d1
        bne     qz_k_store
        move.l  %a4,%d1                 | delta: detents, signed
        beq     qz_k_store
        mvz.b   %d6,%d2                 | start from the value the knob was turned from
        bsr     qz_quant
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
        beq     qz_p_done
        tst.l   %d5                     | slot A only
        bne     qz_p_done
        tst.l   0x460d1684              | PLAYBACK page only
        bne     qz_p_done
        move.l  0x16(%a3),%d1
        cmpi.l  #0x50544348,%d1         | "PTCH"
        bne     qz_p_done
        move.l  %fp,%d1                 | delta
        beq     qz_p_done
        move.l  %d2,-(%sp)
        mvz.b   (0x59,%a0,%a1.l),%d2    | the step's lock before the store
        cmpi.l  #0xff,%d2
        bne     qz_p_go                 | a lock: step from it
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
        bsr     qz_quant
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
        bpl     qz_k_loop
        neg.l   %d0
qz_k_loop:
        tst.l   %d1
        bmi     qz_k_down
        bsr     qz_k_up
        bra     qz_k_next
qz_k_down:
        bsr     qz_k_dn
qz_k_next:
        subq.l  #1,%d0
        bne     qz_k_loop
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
        bgt     qz_k_ret
        move.l  %d6,%d7
        sub.l   %d4,%d7
        mvz.b   (%a0,%d7.l),%d7         | pitch class, 0xff between semitones
        cmpi.l  #12,%d7
        bhs     qz_k_up1
        btst    %d7,%d3
        beq     qz_k_up1
        move.l  %d6,%d2
qz_k_ret:
        rts
qz_k_dn:
        move.l  %d2,%d6
qz_k_dn1:
        subq.l  #1,%d6
        cmp.l   %d4,%d6
        blt     qz_k_ret
        move.l  %d6,%d7
        sub.l   %d4,%d7
        mvz.b   (%a0,%d7.l),%d7
        cmpi.l  #12,%d7
        bhs     qz_k_dn1
        btst    %d7,%d3
        beq     qz_k_dn1
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
        beq     qz_c_replay
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
        bmi     qz_c_hi
        mvz.b   (%a0,%d1.l),%d0
        btst    %d0,%d3
        bne     qz_c_found
qz_c_hi:
        move.l  %d2,%d1
        add.l   %d4,%d1
        cmpi.l  #24,%d1
        bhi     qz_c_more
        mvz.b   (%a0,%d1.l),%d0
        btst    %d0,%d3
        bne     qz_c_found
qz_c_more:
        addq.l  #1,%d4
        cmpi.l  #24,%d4
        bls     qz_c_loop
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

| ---- the SEQUENCER window ------------------------------------------------------
| Its draw loop (0x40065b14) draws min(visible, count) rows but indexes the
| label / getter tables from 0, so a fourth row could never scroll into
| view: with the count grown to 4 (manifest poke) and 3 visible, start the
| index at the list's scroll offset instead. jmp planted at 0x40065bca.
qz_draw:
        move.l  0x460e43d8,%d2          | the list state's scroll offset
        lsl.l   #2,%d2                  | -> byte index into the pointer tables
        lea     32(%sp),%sp             | displaced
        jmp     0x40065bd0

| The fourth row's getter and setter, reached through the grown tables
| (labels 0x400b27d0, getters 0x400b27dc, setters 0x400b282c). The setter is
| jumped to with (delta, wrap) at 4(%sp) / 8(%sp) exactly like CHAIN AFTER's
| 0x400659ec: the LEVEL knob passes its detents (x7 with FUNC) and wrap = 0,
| [YES] passes (1, 1).
qz_get:
        lea     qz_scale(%pc),%a0
        mvz.b   (%a0),%d0
        lea     qz_names(%pc),%a0
        move.l  (%a0,%d0.l*4),%d0
        rts
qz_set:
        lea     qz_scale(%pc),%a0
        mvz.b   (%a0),%d0
        add.l   4(%sp),%d0
        moveq   #24,%d1
        tst.l   8(%sp)
        beq     qz_s_clamp
        cmp.l   %d1,%d0                 | wrap: past the last scale -> OFF, before OFF -> the last
        bgt     qz_s_zero
        tst.l   %d0
        bge     qz_s_store
        move.l  %d1,%d0
        bra     qz_s_store
qz_s_clamp:
        cmp.l   %d1,%d0
        ble     qz_s_low
        move.l  %d1,%d0
qz_s_low:
        tst.l   %d0
        bge     qz_s_store
qz_s_zero:
        moveq   #0,%d0
qz_s_store:
        move.b  %d0,(%a0)
        rts

| ---- the project file ------------------------------------------------------------
| The loader 0x400866c4 reads project.work line by line; a line starting
| with '#' is skipped at 0x400867a2 before any key is compared, on stock
| firmware too. Ours: "#SEQUENCER_SCALE=n". Entry (jmp at 0x400866cc): a
| storing pass (second argument != 0) starts from OFF, so a project saved
| without the line loads as OFF. Line (jmp at 0x400867a2): d3 = the line;
| d0/d1 must hold its first character when stock continues at 0x400867aa.
qz_ld_entry:
        move.l  1472(%sp),%d6           | displaced
        move.l  1476(%sp),%d0           | displaced
        beq     qz_e_back               | parse-only pass: leave the setting
        lea     qz_scale(%pc),%a0
        clr.b   (%a0)
qz_e_back:
        jmp     0x400866d4

qz_ld_line:
        move.b  1167(%sp),%d1           | displaced: the line's first character
        mvs.b   %d1,%d0                 | displaced
        moveq   #35,%d5                 | displaced: '#'
        cmp.l   %d0,%d5
        bne     qz_l_back
        move.l  %d3,%a0
        lea     qz_key(%pc),%a1
qz_l_cmp:
        mvz.b   (%a1)+,%d0
        beq     qz_l_ours
        mvz.b   (%a0)+,%d5
        cmp.l   %d0,%d5
        beq     qz_l_cmp
        moveq   #35,%d5                 | another comment: stock skips it
        mvs.b   %d1,%d0
qz_l_back:
        jmp     0x400867aa
qz_l_ours:
        moveq   #0,%d0                  | decimal after the '='
        moveq   #10,%d5
qz_l_dig:
        mvz.b   (%a0)+,%d1
        subi.l  #48,%d1
        cmpi.l  #9,%d1
        bhi     qz_l_num
        mulu.l  %d5,%d0
        add.l   %d1,%d0
        bra     qz_l_dig
qz_l_num:
        cmpi.l  #24,%d0
        bls     qz_l_ok
        moveq   #0,%d0                  | out of range: OFF
qz_l_ok:
        tst.l   58(%sp)                 | parse-only pass: nothing is stored
        bne     qz_l_next
        lea     qz_scale(%pc),%a0
        move.b  %d0,(%a0)
qz_l_next:
        jmp     0x40088224              | the loop's next line

| The writer 0x40088882.. prints one "KEY=%d\r\n" per setting: a4 = sprintf
| (buffer d2, format, value), a3 = strlen, a2 = write (file d3). jmp planted
| at 0x400888aa, the start of PATTERN_CHANGE_AUTO_SILENCE_TRACKS's line, so
| ours follows PATTERN_CHANGE_CHAIN_BEHAVIOR. A failed write is not checked
| here; the stock line that follows checks its own.
qz_wr:
        lea     qz_scale(%pc),%a0
        mvz.b   (%a0),%d0
        move.l  %d0,-(%sp)
        pea     qz_fmt(%pc)
        move.l  %d2,-(%sp)
        jsr     (%a4)
        move.l  %d2,-(%sp)
        jsr     (%a3)
        move.l  %d0,-(%sp)
        move.l  %d2,-(%sp)
        move.l  %d3,-(%sp)
        jsr     (%a2)
        lea     28(%sp),%sp
        mvs.b   0x8000004f,%d1          | displaced
        move.l  %d1,-(%sp)              | displaced
        jmp     0x400888b2

| ---- data ------------------------------------------------------------------------
qz_key:         .asciz  "#SEQUENCER_SCALE="
qz_fmt:         .asciz  "#SEQUENCER_SCALE=%d\r\n"
qz_lbl_scale:   .asciz  "SCALE"
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
