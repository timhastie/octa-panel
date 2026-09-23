| SYNTH MACHINE -- ColdFire code cave, phase 3: the page (22 Sep 2026). GNU as,
| -mcpu=5475. PINNED at 0x400d24d0 (the second zero run): the descriptor clone
| below carries absolute pointers to the formatters and widgets, so the bytes
| depend on the address.
|
| WHAT: when the current track's FLEX slot holds a SYNTH*-named sample, the
| PLAYBACK page presents the FM voice instead of a sample player -- the slot
| names read PTCH RATO INDX RATE FDBK DEC (four characters: the boxes are 19 px), the values format as the voice
| understands them (RATIO from the ratio table "0.25".."16", INDEX and FDBK
| 0..127, DECAY as "HOLD" / ms / s), the four synth slots show their value all
| the time (Digitone style) and draw an icon where the sample dial was: the
| two-operator diagram M->C (RATIO), a sideband spectrum whose bars grow with
| the index (INDEX), the modulator with its feedback loop, drawn when FDBK > 0
| (FDBK), and the index envelope, a falling curve whose length follows the
| value (DECAY). The footer reads FM SYNTH>FLEX. Non-synth tracks and every
| other page draw exactly as stock.
|
| HOW: the page descriptor resolver 0x40031da4(track, page kind) returns
| tbl[machine] for the PLAYBACK page at 0x40031ece (`movel %a0@(0,%d0:l:4),%d0;
| bras 0x40031ed6`, 6 bytes -> `jmp pg_resolve`). Everything that draws or edits
| the page -- the generic parameter-page renderer 0x4004e0xx, the footer
| 0x4003d5xx, the knob handler 0x40055008 -- reaches the descriptor through it
| (0x40031ee0 / 0x40031f28 tail-call it). The stub replays the lookup and, for
| the FLEX descriptor of a track whose assigned FLEX slot (Part + 0x8f04a +
| track*5 + 1) has a settings record (0x100b14f0 + 0x448*slot) whose path's
| basename starts with "SYNTH" -- loaded into flex RAM or not -- returns
| pg_desc: the stock FLEX record (P..P+0x192) with only the names, the title,
| the four A formatters (P+0xca), the four B widgets (P+0xfa) and the enable
| nibbles (P+0x18e: 5/7 = bit 2 "always show the value") changed -- ranges,
| defaults and knob handlers are the stock's, so storage, locks, scenes, LFOs
| and MIDI behave exactly as before.
|
| The renderer draws each box at (x = 60/80/100, y = 36 top row / 8 bottom
| row; y grows UPWARD, screen row = 63 - y): the name centred on x+9 at y+20,
| then widget(x, y, slot, value, flags, fmt, window). With flags bit 3 (the
| nibble's bit 2) the stock dial 0x400479b4 clears the box, draws its circle
| bitmap at (x+4, y+7), the pointer at (x+6, y+9) and the value text centred
| at y+1. Each widget here calls the stock dial first (so the value text, the
| box clear and the lock highlight stay stock's) and then blits a 17 x 13
| column bitmap over the dial at (x+1, y+7) with 0x400128a8(bitmap, window,
| x, y): {width, height, longs per column, columns, mask}, one long per
| column, bit 31 = the bottom row, bit 31-k = row k above it; the blit does
| dst = (dst & ~mask) | (data & mask), so a full mask replaces the area. The
| icon is composed in a RAM scratch (pg_data; the main OS runs from DRAM)
| from static column images (pg_img_*) and a few procedural strokes.
| Formatters have the stock signature fmt(buf, value) -> sprintf.

        .text
        .set    RESOLVER_RET, 0x40031ed6
        .set    FLEX_P, 0x400d31ae       | the stock FLEX PLAYBACK descriptor (P form)
        .set    SLOT_OFF, 0x8f04b        | Part: 0x8f04a + track*5 + machine (1 = FLEX)
        .set    SETTINGS_BASE, 0x100b14f0
        .set    SETTINGS_STRIDE, 0x448
        .set    STOCK_WIDGET, 0x400479b4
        .set    BLIT, 0x400128a8
        .set    SPRINTF, 0x40013a08
        .set    FMT_D, 0x400b465d        | "%d"

| ---- the resolver detour ------------------------------------------------------
| 0x40031ece: `movel %a0@(0,%d0:l:4),%d0; bras 0x40031ed6` -> `jmp pg_resolve`.
| d0 = machine type, a0 = the table 0x400d5f38, d3 = track, d1 = part index,
| a1 = the bank blob; d2-d5 are restored by the resolver's epilogue, d1/a0/a1
| are C scratch, d6/d7/a2-a6 are left alone.
pg_resolve:
        movel   %a0@(0,%d0:l:4),%d0      | replay: the machine's descriptor
        cmpil   #FLEX_P,%d0
        bne     pg_r_done
        movel   #6322,%d2
        mulsl   %d1,%d2                  | part * 6322
        addl    %a1,%d2                  | + the bank blob
        movel   %d3,%d4
        lsll    #2,%d4
        addl    %d3,%d4                  | track * 5
        addl    %d4,%d2
        moveal  %d2,%a0
        addal   #SLOT_OFF,%a0
        mvzb    %a0@,%d2                 | the track's FLEX slot, 0-based
        cmpil   #127,%d2
        bhi     pg_r_done                | 0xff = none; 128.. = the recorder buffers
        movel   #SETTINGS_STRIDE,%d4
        mulsl   %d2,%d4
        addil   #SETTINGS_BASE,%d4
        moveal  %d4,%a0                  | the slot's settings record; its path at +0
        | (nothing else is tested: the byte at +0x129 the first build gated on as
        | a "loaded" flag is the sample's QUANTIZED TRIG attribute, -1 = OFF for
        | a sample loaded through the file browser -- the trig routine 0x40005102
        | only chooses between an immediate and a quantized manual trig on it; an
        | empty slot has an empty path and fails the name compare below)
        moveal  %a0,%a1                  | a1 = the file name (after the last '/')
        movel   #255,%d4
pg_scan:
        mvzb    %a0@+,%d5
        beq     pg_scanned
        cmpil   #'/',%d5
        bne     pg_scan1
        moveal  %a0,%a1
pg_scan1:
        subql   #1,%d4
        bne     pg_scan
pg_scanned:
        lea     pg_name(%pc),%a0
        moveq   #5,%d4
pg_cmp:
        mvzb    %a0@+,%d5
        mvzb    %a1@+,%d2
        cmpl    %d2,%d5
        bne     pg_r_done
        subql   #1,%d4
        bne     pg_cmp
        lea     pg_desc(%pc),%a0
        movel   %a0,%d0                  | the synth's descriptor
pg_r_done:
        jmp     RESOLVER_RET
pg_name:
        .ascii  "SYNTH"
        .align  2

| ---- the formatters: fmt(buf, value), C convention ----------------------------
pg_fmt_plain:                            | "%d" (INDEX, FDBK: 0..127 as stored)
        movel   %sp@(8),%sp@-
        pea     FMT_D
        movel   %sp@(12),%sp@-
        jsr     SPRINTF
        lea     %sp@(12),%sp
        rts

pg_fmt_ratio:                            | the ratio table's entry: "0.25" "1" "1.41" "3.5" "16"
        movel   %d2,%sp@-
        movel   %sp@(12),%d0             | raw
        lsrl    #2,%d0
        andil   #31,%d0
        lea     pg_ratio(%pc),%a0
        mvzw    %a0@(0,%d0:l:2),%d1      | Q8
        movel   %d1,%d0
        lsrl    #8,%d0                   | the integer part
        andil   #255,%d1
        moveq   #100,%d2
        mulul   %d2,%d1
        lsrl    #8,%d1                   | the fraction in hundredths: 0 1 25 41 50 75
        beq     pg_fr_int
        cmpil   #50,%d1
        beq     pg_fr_half
        movel   %d1,%sp@-
        movel   %d0,%sp@-
        pea     pg_f_dd(%pc)             | "%d.%02d"
        movel   %sp@(20),%sp@-
        jsr     SPRINTF
        lea     %sp@(16),%sp
        bra     pg_fr_out
pg_fr_half:
        movel   %d0,%sp@-
        pea     pg_f_d5(%pc)             | "%d.5"
        movel   %sp@(16),%sp@-
        jsr     SPRINTF
        lea     %sp@(12),%sp
        bra     pg_fr_out
pg_fr_int:
        movel   %d0,%sp@-
        pea     FMT_D
        movel   %sp@(16),%sp@-
        jsr     SPRINTF
        lea     %sp@(12),%sp
pg_fr_out:
        movel   %sp@+,%d2
        rts

pg_fmt_decay:                            | tau = 2 s * (raw/127)^2: "HOLD", "32ms", "286ms", "1.1s", "2.0s"
        movel   %d2,%sp@-
        movel   %sp@(12),%d0             | raw
        beq     pg_fd_hold
        movel   %d0,%d1
        mulul   %d0,%d1                  | raw^2
        movel   #2000,%d0
        mulul   %d1,%d0                  | * 2000 ms
        addil   #8064,%d0
        movel   #16129,%d1
        divul   %d1,%d0                  | / 127^2, rounded: ms
        cmpil   #1000,%d0
        bcc     pg_fd_sec
        movel   %d0,%sp@-
        pea     pg_f_ms(%pc)             | "%dms"
        movel   %sp@(16),%sp@-
        jsr     SPRINTF
        lea     %sp@(12),%sp
        bra     pg_fd_out
pg_fd_sec:
        movel   #1000,%d1
        movel   %d0,%d2
        divul   %d1,%d2                  | seconds
        moveq   #100,%d1
        divul   %d1,%d0                  | tenths, total
        movel   %d2,%d1
        lsll    #3,%d1
        addl    %d2,%d1
        addl    %d2,%d1                  | seconds * 10
        subl    %d1,%d0                  | tenths
        movel   %d0,%sp@-
        movel   %d2,%sp@-
        pea     pg_f_s(%pc)              | "%d.%ds"
        movel   %sp@(20),%sp@-
        jsr     SPRINTF
        lea     %sp@(16),%sp
        bra     pg_fd_out
pg_fd_hold:
        pea     pg_f_hold(%pc)           | "HOLD": the index holds
        movel   %sp@(12),%sp@-
        jsr     SPRINTF
        addql   #8,%sp
pg_fd_out:
        movel   %sp@+,%d2
        rts

| ---- the widgets: widget(x, y, slot, value, flags, fmt, window) ---------------
pg_wid_ratio:
        moveq   #0,%d0
        bra     pg_wid
pg_wid_index:
        moveq   #1,%d0
        bra     pg_wid
pg_wid_fdbk:
        moveq   #2,%d0
        bra     pg_wid
pg_wid_decay:
        moveq   #3,%d0
        bra     pg_wid
pg_wid:
        lea     %sp@(-44),%sp
        moveml  %d2-%d7/%a2-%a6,%sp@     | args: 48 x, 52 y, 56 slot, 60 value, 64 flags, 68 fmt, 72 window
        movel   %d0,%d7                  | which icon
        movel   %sp@(72),%sp@-           | the stock dial first: the box clear, the value text,
        movel   %sp@(72),%sp@-           | the lock highlight (each push brings the next arg to 72)
        movel   %sp@(72),%sp@-
        movel   %sp@(72),%sp@-
        movel   %sp@(72),%sp@-
        movel   %sp@(72),%sp@-
        movel   %sp@(72),%sp@-
        jsr     STOCK_WIDGET
        lea     %sp@(28),%sp
        movel   %sp@(60),%d6             | value
        tstl    %d7
        beq     pg_ic_ratio
        subql   #1,%d7
        beq     pg_ic_index
        subql   #1,%d7
        beq     pg_ic_fdbk
        bra     pg_ic_decay

| RATIO: the two operators, M -> C (the ratio itself is the value text)
pg_ic_ratio:
        lea     pg_img_ratio(%pc),%a0
        bsr     pg_copy
        bra     pg_blit

| INDEX: a sideband spectrum -- the carrier bar shrinks a little, the sidebands
| at +-1, +-2, +-3 grow in turn with the index (a Bessel-shaped cartoon)
pg_ic_index:
        lea     pg_img_base(%pc),%a0
        bsr     pg_copy
        movel   %d6,%d1
        moveq   #3,%d0
        mulul   %d0,%d1
        moveq   #127,%d0
        divul   %d0,%d1                  | value * 3 / 127
        moveq   #11,%d2
        subl    %d1,%d2                  | the carrier, 11 .. 8
        moveq   #8,%d0
        moveq   #1,%d1
        bsr     pg_vline
        movel   %d6,%d2
        moveq   #10,%d0
        mulul   %d0,%d2
        moveq   #127,%d0
        divul   %d0,%d2                  | +-1: value * 10 / 127
        beq     pg_ix2
        moveq   #6,%d0
        moveq   #1,%d1
        bsr     pg_vline
        moveq   #10,%d0
        moveq   #1,%d1
        bsr     pg_vline
pg_ix2:
        movel   %d6,%d2
        subil   #20,%d2
        ble     pg_ix3
        moveq   #8,%d0
        mulul   %d0,%d2
        movel   #107,%d0
        divul   %d0,%d2                  | +-2: (value - 20) * 8 / 107
        beq     pg_ix3
        moveq   #4,%d0
        moveq   #1,%d1
        bsr     pg_vline
        moveq   #12,%d0
        moveq   #1,%d1
        bsr     pg_vline
pg_ix3:
        movel   %d6,%d2
        subil   #56,%d2
        ble     pg_blit
        moveq   #6,%d0
        mulul   %d0,%d2
        moveq   #71,%d0
        divul   %d0,%d2                  | +-3: (value - 56) * 6 / 71
        beq     pg_blit
        moveq   #2,%d0
        moveq   #1,%d1
        bsr     pg_vline
        moveq   #14,%d0
        moveq   #1,%d1
        bsr     pg_vline
        bra     pg_blit

| FDBK: the modulator, and its feedback loop when the value is not 0
pg_ic_fdbk:
        lea     pg_img_fdbk(%pc),%a0
        bsr     pg_copy
        tstl    %d6
        beq     pg_blit
        lea     pg_img_loop(%pc),%a0
        bsr     pg_or
        bra     pg_blit

| DECAY: the index envelope -- an instant rise at column 0, then pg_env's
| exponential fall stretched over L = 2 + value * 14 / 127 columns to the
| floor (row 1 = I/16); value 0 holds, a flat top
pg_ic_decay:
        lea     pg_img_base(%pc),%a0
        bsr     pg_copy
        moveq   #0,%d0
        moveq   #1,%d1
        moveq   #11,%d2
        bsr     pg_vline                 | the attack edge
        movel   #0x7fff,%d5              | value 0: i stays 0, h stays 11
        tstl    %d6
        beq     pg_dc_go
        movel   %d6,%d5
        moveq   #14,%d0
        mulul   %d0,%d5
        moveq   #127,%d0
        divul   %d0,%d5
        addql   #2,%d5                   | L, 2 .. 16 columns
pg_dc_go:
        moveq   #11,%d4                  | the previous height
        moveq   #1,%d7                   | column
pg_dc_loop:
        movel   %d7,%d2
        lsll    #4,%d2
        divul   %d5,%d2                  | i = c * 16 / L
        cmpil   #16,%d2
        ble     pg_dc1
        moveq   #16,%d2
pg_dc1:
        lea     pg_env(%pc),%a0
        mvzb    %a0@(0,%d2:l),%d2        | h
        moveal  %d2,%a3
        movel   %d7,%d0
        movel   %d2,%d1
        movel   %d4,%d2
        bsr     pg_vline                 | rows h .. previous: the outline
        movel   %a3,%d4
        addql   #1,%d7
        cmpil   #16,%d7
        ble     pg_dc_loop

| the blit: over the stock dial, at (x+1, y+7); inverted with the box when
| the parameter is locked (flags bit 0: the stock inverts x+1..x+17, y+1..y+18)
pg_blit:
        movel   %sp@(64),%d0
        btst    #0,%d0
        beq     pg_blit1
        lea     pg_data(%pc),%a0
        moveq   #16,%d0
pg_inv:
        movel   %a0@,%d1
        eoril   #0xfff00000,%d1          | rows 0..11 (row 12, y+19, lies outside the highlight)
        movel   %d1,%a0@+
        subql   #1,%d0
        bge     pg_inv
pg_blit1:
        movel   %sp@(52),%d0
        addql   #7,%d0
        movel   %d0,%sp@-                | y + 7
        movel   %sp@(52),%d0
        addql   #1,%d0
        movel   %d0,%sp@-                | x + 1
        movel   %sp@(80),%sp@-           | window
        pea     pg_bmp(%pc)
        jsr     BLIT
        lea     %sp@(16),%sp
        moveml  %sp@,%d2-%d7/%a2-%a6
        lea     %sp@(44),%sp
        rts

| ---- icon helpers (the scratch is pg_data: 17 longs, one per column) -------
pg_copy:                                 | a0 = a 17-column image -> the scratch
        lea     pg_data(%pc),%a1
        moveq   #16,%d0
pg_copy1:
        movel   %a0@+,%a1@+
        subql   #1,%d0
        bge     pg_copy1
        rts
pg_or:                                   | OR the image at a0 into the scratch
        lea     pg_data(%pc),%a1
        moveq   #16,%d0
pg_or1:
        movel   %a0@+,%d1
        orl     %d1,%a1@+
        subql   #1,%d0
        bge     pg_or1
        rts
pg_vline:                                | column d0, rows d1 .. d2 (d1 <= d2 <= 12); clobbers d1, d3, a0
        lea     pg_data(%pc),%a0
        lea     %a0@(0,%d0:l:4),%a0
        movel   #0x80000000,%d3
        lsrl    %d1,%d3
pg_vline1:
        orl     %d3,%a0@
        lsrl    #1,%d3
        addql   #1,%d1
        cmpl    %d1,%d2
        bge     pg_vline1
        rts

| ---- data -------------------------------------------------------------------
pg_f_dd:
        .asciz  "%d.%02d"
pg_f_d5:
        .asciz  "%d.5"
pg_f_ms:
        .asciz  "%dms"
pg_f_s:
        .asciz  "%d.%ds"
pg_f_hold:
        .asciz  "HOLD"
pg_env:                                  | 11 * exp(-i/5), i = 0..16; the floor is row 1
        .byte   11, 9, 7, 6, 5, 4, 3, 3, 2, 2, 1, 1, 1, 1, 1, 1, 1
        .align  2
pg_ratio:                                | synth.s's sy_ratio: STRT raw >> 2 -> the ratio, Q8
        .short  64, 128, 192, 256, 259, 320, 362, 384
        .short  448, 512, 515, 640, 768, 896, 1024, 1027
        .short  1152, 1280, 1408, 1536, 1664, 1792, 1920, 2048
        .short  2304, 2560, 2816, 3072, 3328, 3584, 3840, 4096
        .align  4
pg_bmp:                                  | the blit's record: width, height, longs per column, columns, mask
        .long   17, 13, 1, pg_data, pg_mask
pg_mask:
        .rept   17
        .long   0xfff80000               | rows 0..12
        .endr
pg_data:                                 | the scratch (RAM)
        .fill   17, 4, 0

| ---- the FLEX PLAYBACK descriptor, cloned (stock P = 0x400d31ae, 0x192 bytes) ----
| generated by out/_agents/synth3/gen_page.py from out/raw/section_3_MAIN_OS.bin;
| every byte is the stock record except the fields named below.
pg_desc:
        .byte   0x00, 0x00, 0x00, 0x00, 0x50, 0x42, 0x00, 0x00, 0x00    | P+0x000
        .ascii  "FM SYNTH\0\0\0\0\0"            | P+0x009
        .byte   0x50, 0x54, 0x43, 0x48, 0x00, 0x00    | P+0x016
        .ascii  "RATO\0\0"                      | P+0x01c
        .ascii  "INDX\0\0"                      | P+0x022
        .byte   0x52, 0x41, 0x54, 0x45, 0x00, 0x00    | P+0x028
        .ascii  "FDBK\0\0"                      | P+0x02e
        .ascii  "DEC\0\0\0"                     | P+0x034
        .byte   0x4c, 0x4f, 0x4f, 0x50, 0x00, 0x00, 0x53, 0x4c, 0x49, 0x43, 0x00, 0x00, 0x4c, 0x45, 0x4e, 0x00    | P+0x03a
        .byte   0x00, 0x00, 0x52, 0x41, 0x54, 0x45, 0x00, 0x00, 0x54, 0x53, 0x54, 0x52, 0x00, 0x00, 0x54, 0x53    | P+0x04a
        .byte   0x4e, 0x53, 0x00, 0x00, 0x40, 0x00, 0x00, 0x7f, 0x00, 0x4f, 0x01, 0x00, 0x00, 0x00, 0x01, 0x40    | P+0x05a
        .byte   0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00    | P+0x06a
        .byte   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00    | P+0x07a
        .byte   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00    | P+0x08a
        .byte   0x00, 0x00, 0x00, 0x79, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x00, 0x80    | P+0x09a
        .byte   0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x02    | P+0x0aa
        .byte   0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x80    | P+0x0ba
        .byte   0x40, 0x03, 0xb4, 0xb0    | P+0x0ca
        .long   pg_fmt_ratio                    | P+0x0ce
        .long   pg_fmt_plain                    | P+0x0d2
        .byte   0x40, 0x03, 0xc7, 0xa0    | P+0x0d6
        .long   pg_fmt_plain                    | P+0x0da
        .long   pg_fmt_decay                    | P+0x0de
        .byte   0x40, 0x03, 0xb6, 0x4c, 0x40, 0x03, 0xc1, 0x4c, 0x40, 0x03, 0xb2, 0xec, 0x40, 0x03, 0xb6, 0x04    | P+0x0e2
        .byte   0x40, 0x03, 0xb6, 0xa4, 0x00, 0x00, 0x00, 0x00, 0x40, 0x04, 0x79, 0xb4    | P+0x0f2
        .long   pg_wid_ratio                    | P+0x0fe
        .long   pg_wid_index                    | P+0x102
        .byte   0x40, 0x04, 0x79, 0xb4    | P+0x106
        .long   pg_wid_fdbk                     | P+0x10a
        .long   pg_wid_decay                    | P+0x10e
        .byte   0x40, 0x04, 0x6c, 0x28, 0x40, 0x04, 0x6f, 0x10, 0x40, 0x04, 0x6f, 0x10, 0x40, 0x04, 0x6f, 0x10    | P+0x112
        .byte   0x40, 0x04, 0x6c, 0x28, 0x00, 0x00, 0x00, 0x00, 0x40, 0x03, 0x2d, 0x08, 0x00, 0x00, 0x00, 0x00    | P+0x122
        .byte   0x00, 0x00, 0x00, 0x00, 0x40, 0x03, 0x28, 0xe4, 0x00, 0x00, 0x00, 0x00, 0x40, 0x03, 0x2f, 0x28    | P+0x132
        .byte   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00    | P+0x142
        .byte   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x40, 0x03, 0x8d, 0x94, 0x40, 0x03, 0x8d, 0x94    | P+0x152
        .byte   0x40, 0x03, 0x8d, 0x94, 0x40, 0x03, 0x8d, 0xdc, 0x40, 0x03, 0x8d, 0x94, 0x40, 0x03, 0x8d, 0x94    | P+0x162
        .byte   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00    | P+0x172
        .byte   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x17, 0x51    | P+0x182
        .long   0x55715751                      | P+0x18e
pg_desc_end:

pg_img_ratio:
|   .................
|   .................
|   ######.....######
|   #....#.....#....#
|   #.#.##.....#.##.#
|   #.####...#.#.#..#
|   #.#.#######.#...#
|   #.#.##...#.#.#..#
|   #.#.##.....#.##.#
|   #....#.....#....#
|   ######.....######
|   .................
|   .................
        .long   0x3fe00000, 0x20200000, 0x2fa00000, 0x21200000
        .long   0x2fa00000, 0x3fe00000, 0x02000000, 0x02000000
        .long   0x02000000, 0x07000000, 0x02000000, 0x3de00000
        .long   0x22200000, 0x2da00000, 0x28a00000, 0x20200000
        .long   0x3fe00000

pg_img_fdbk:
|   .................
|   .................
|   .....#######.....
|   .....#.....#.....
|   .....#.#.#.#.....
|   .....#.###.#.....
|   .....#.#.#.#.....
|   .....#.#.#.#.....
|   .....#.#.#.#.....
|   .....#.....#.....
|   .....#######.....
|   .................
|   .................
        .long   0x00000000, 0x00000000, 0x00000000, 0x00000000
        .long   0x00000000, 0x3fe00000, 0x20200000, 0x2fa00000
        .long   0x21200000, 0x2fa00000, 0x20200000, 0x3fe00000
        .long   0x00000000, 0x00000000, 0x00000000, 0x00000000
        .long   0x00000000

pg_img_loop:
|   .................
|   ..#############..
|   ..#...........#..
|   ..#...........#..
|   ..#...........#..
|   ...#..........#..
|   ..###.......###..
|   ...#.............
|   .................
|   .................
|   .................
|   .................
|   .................
        .long   0x00000000, 0x00000000, 0x02f00000, 0x07100000
        .long   0x02100000, 0x00100000, 0x00100000, 0x00100000
        .long   0x00100000, 0x00100000, 0x00100000, 0x00100000
        .long   0x02100000, 0x02100000, 0x03f00000, 0x00000000
        .long   0x00000000

pg_img_base:
|   .................
|   .................
|   .................
|   .................
|   .................
|   .................
|   .................
|   .................
|   .................
|   .................
|   .................
|   .................
|   #################
        .long   0x80000000, 0x80000000, 0x80000000, 0x80000000
        .long   0x80000000, 0x80000000, 0x80000000, 0x80000000
        .long   0x80000000, 0x80000000, 0x80000000, 0x80000000
        .long   0x80000000, 0x80000000, 0x80000000, 0x80000000
        .long   0x80000000

        .align  4
pg_end:
