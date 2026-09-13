| DIRECT JUMP -- ColdFire code cave (13 Sep 2026). GNU as, -mcpu=5475.
|
| CHAIN AFTER gains an 18th value, DIRECT (index 17): a pattern selected
| while the sequencer runs starts at the NEXT STEP, at the step count the
| old pattern had reached, instead of at the old pattern's end. Everything
| below rides the firmware's own arranger mechanism -- a queued pattern
| carries a START STEP (0x80006630) and the tick handler carries an absolute
| CHANGE-AT step (0x8000662c) -- so the switch itself (part, per-track
| positions, LEDs, UI notices) is stock code.
|
| Position independent: OS absolutes and pc-relative references only. The
| two tables at +0x180 / +0x200 are read by stock code through `lea`
| operands the manifest repoints (emit()); nothing in here names itself.
|
| Layout (fixed with .org; the manifest's OFF_* constants are these):
|   +0x000 dj_queue   hook target, jsr planted at 0x400a06d6 (the setter's
|                     "transport running" branch), 12 bytes displaced
|   +0x100 dj_apply   hook target, jsr planted at 0x400a44e2 (the tick
|                     handler's boundary apply), 18 bytes displaced
|   +0x180 dj_lens    CHAIN AFTER index -> steps, 18 longs (stock's 17 at
|                     0x400d80dc, then -1 so every other reader treats
|                     DIRECT as PAT.LEN)
|   +0x200 dj_labels  CHAIN AFTER index -> label, 18 longs (stock's 17 at
|                     0x400b27e8, then stock's own "DIRECT" at 0x400b6912)
|
| Sequencer state (docs/firmware/RTOS_FORK.md 8.3, and this module's README):
|   0x800065bd/be  playing bank / pattern      0x800065bf/c0  queued bank / pattern
|   0x800065b2     step counter (word)          0x800065b6     tick 0..5 within the step
|   0x80006630     start step of the queued pattern (the arranger's OFFSET)
|   0x80006634     its change-at (-1 = PAT.LEN)  0x8000662c     the PLAYING pattern's change-at
|   0x8000004e     project CHAIN AFTER index; pattern+0x8e56 = the pattern's own (bit 7: use project)
|   0x460d1aec     arranger playing (0x40033968 returns it)

        .text

| ---- dj_queue: the setter 0x400a0570, transport running ---------------------
| Registers here (read off the setter): d4 = bank, d5 = pattern, d3 = start
| step (0 from every UI path), d2 = change-at (-1 from every UI path; the
| arranger passes its row length), d6 = arg 5. After the hook the setter
| stores d3 to 0x80006630/0x80006638 and d2 (-1) to 0x80006634, then
| rewrites d0. So: set d3 and the tick handler's change-at, touch nothing
| else. d1/a0 are saved; d0 is the setter's scratch.
dj_queue:
        move.b  %d4,0x800065bf          | displaced: queued bank
        move.b  %d5,0x800065c0          | displaced: queued pattern
        moveq   #-1,%d0
        cmp.l   %d2,%d0                 | an absolute change-at = an arranger row: stock
        bne   dj_q_done
        tst.l   0x460d1aec              | arranger playing: stock
        bne   dj_q_done
        mvs.b   0x800065be,%d0
        cmp.l   %d5,%d0
        bne   dj_q_change
        mvs.b   0x800065bd,%d0
        cmp.l   %d4,%d0
        beq   dj_q_done               | same bank and pattern: nothing is queued
dj_q_change:
        move.l  %d1,-(%sp)
        move.l  %a0,-(%sp)
        | the PLAYING pattern's effective CHAIN AFTER: its own byte at +0x8e56
        | when >= 0, else the project's (the tick handler's own rule, 0x400a42fa)
        mvs.b   0x800065be,%d0
        move.l  #36568,%d1
        muls.l  %d1,%d0
        mvs.b   0x800065bd,%d1
        move.l  %d0,%a0
        move.l  #635712,%d0
        muls.l  %d0,%d1
        adda.l  %d1,%a0
        adda.l  #0x400eb036,%a0         | blob base 0x400e21e0 + 0x8e56
        mvs.b   (%a0),%d0
        bpl   dj_q_idx
        mvs.b   0x8000004e,%d0
dj_q_idx:
        cmpi.l  #17,%d0                 | DIRECT?
        bne   dj_q_rest
        mvz.w   0x800065b2,%d0          | steps the playing pattern has counted
        addq.l  #1,%d0                  | -> the next step boundary
        move.l  %d0,0x8000662c          | the tick handler switches there (0x400a439a)
        move.l  %d0,%d3                 | and the new pattern starts at that count
        | wrap the start to the NEW pattern's length (PER TRACK: MASTER LENGTH;
        | INF or 0: leave it, the tracks wrap on their own)
        move.l  %d5,%d0
        move.l  #36568,%d1
        muls.l  %d1,%d0
        move.l  %d4,%d1
        move.l  %d0,%a0
        move.l  #635712,%d0
        muls.l  %d0,%d1
        adda.l  %d1,%a0
        adda.l  #0x400eb034,%a0         | blob base + 0x8e54
        tst.b   1(%a0)                  | +0x8e55: PER TRACK scale mode
        beq   dj_q_normal
        mvs.w   -4(%a0),%d0             | +0x8e50: MASTER LENGTH (word, -1 = INF)
        bra   dj_q_len
dj_q_normal:
        mvs.b   -1(%a0),%d0             | +0x8e53: PATTERN SCALE steps
dj_q_len:
        ble   dj_q_arm
        remul   %d0,%d1,%d3             | d1 = d3 mod length (Dr must differ from Dq:
        move.l  %d1,%d3                 |  the same register would encode DIVU.L, a quotient)
dj_q_arm:
        lea     dj_flags(%pc),%a0
        move.b  #1,(%a0)                | armed: dj_apply clears the start step after use
        mvs.b   0x800065b6,%d0          | tick within the step
        cmpi.l  #2,%d0
        blt   dj_q_rest               | the tick-2 pass still comes: it sends the MIDI program change
        move.b  #1,1(%a0)               | it is gone: dj_apply sends it at the switch
dj_q_rest:
        move.l  (%sp)+,%a0
        move.l  (%sp)+,%d1
dj_q_done:
        rts
dj_flags:
        .byte   0                       | +0: a direct jump is armed
        .byte   0                       | +1: its MIDI program change is still owed
        .align  2

| ---- dj_apply: the tick handler 0x400a3fdc, boundary apply ------------------
| Right after the playing pair took the queued pair (0x400a44d0). The three
| displaced longword moves latch the start step for the per-track maths
| that follows (0x400a47f6..); 0x8000662c is latched from 0x80006634 AFTER
| this hook (0x400a44f4), so it still holds our trigger here. Only d0 is
| free (reloaded at 0x400a4508 / 0x400a456e); everything else is preserved.
        .org    0x100
dj_apply:
        move.l  0x80006630,%d0          | displaced
        move.l  %d0,0x80006638          | displaced
        move.l  %d0,0x80006628          | displaced: the start step every track is positioned from
        move.l  %a0,-(%sp)
        lea     dj_flags(%pc),%a0
        tst.b   (%a0)
        beq   dj_a_done               | not armed: stock
        clr.b   (%a0)
        mvz.w   0x800065b2,%d0
        cmp.l   0x8000662c,%d0          | the boundary we armed?
        bne   dj_a_owed               | no: STOP came first, the flag was stale
        clr.l   0x80006630              | consumed: later restarts begin at 0, as stock
        tst.b   1(%a0)
        beq   dj_a_owed
        move.l  %d1,-(%sp)              | the MIDI program change stock sends two ticks early
        move.l  %a1,-(%sp)
        mvs.b   0x800065be,%d0
        move.l  %d0,-(%sp)
        mvs.b   0x800065bd,%d0
        move.l  %d0,-(%sp)
        jsr     0x4009e884              | (bank, pattern): bank select + program change, if enabled
        addq.l  #8,%sp
        move.l  (%sp)+,%a1
        move.l  (%sp)+,%d1
dj_a_owed:
        clr.b   1(%a0)
dj_a_done:
        move.l  (%sp)+,%a0
        rts

| ---- tables ------------------------------------------------------------------
        .org    0x180
dj_lens:                                | 0x400d80dc's seventeen, then DIRECT = PAT.LEN
        .long   -1, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256
        .long   -1

        .org    0x200
dj_labels:                              | 0x400b27e8's seventeen, then "DIRECT" (0x400b6912)
        .long   0x400b5f62, 0x400b5771, 0x400b5f6b, 0x400b5780, 0x400b5f77
        .long   0x400b5f71, 0x400b5f96, 0x400b5f6a, 0x400b5f70, 0x400b5f76
        .long   0x400b5f7c, 0x400b5f82, 0x400b5f88, 0x400b5f8e, 0x400b5f94
        .long   0x400b5f9b, 0x400b5fa2
        .long   0x400b6912
dj_end:
