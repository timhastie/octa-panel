# Scale quantizer

(24 Sep 2026: polyphonic CHROMATIC keys on a paraphonic synth track -- "Paraphonic keys" below.)

**PROJECT > CONTROL > SEQUENCER gains a fourth row, SCALE** (OFF, then 24
scales). With a scale on, the **PTCH knob** on the PLAYBACK page of a
STATIC / FLEX / PICKUP track steps to the next scale degree in the turn
direction instead of one raw unit — on the Part's value, and on a step's
lock when a [TRIG] key is held — and a [TRIG] key played in **CHROMATIC
trig mode** snaps to the nearest degree (ties to the lower one) before it
becomes the pitch the voice, the recorded lock and the screen see. The
Digitakt / Digitone scale quantizer, on the Octatrack's own parameters.
**OFF is stock**: every detour replays what it displaced and does nothing
else. The setting is saved with the project and comes back after a cold
boot. RATE is a playback rate, not a semitone quantity, and is left alone.

**24 Sep 2026: a fifth row, GLIDE** (OFF, 1..127) — the SYNTH machine's
glide time (`modules/synth`: 10 ms at 1, 100 ms at 64, 1 s at 127) and,
with it on, **303-style legato on the CHROMATIC keys**: a key pressed while
another key of the same track is still held does not restart the voice, it
only moves the pitch (which the synth then glides to); releasing the first
key does nothing, releasing the last one releases the note. Saved as
`#SYNTH_GLIDE=n` after the SCALE line. Section "GLIDE and legato" below.

One linked ColdFire unit (1,484 bytes, floating, linked by the build at
the address it lands on; 1,292 bytes before GLIDE — the unit now assembles
its branches with the `jb<cc>` forms, short where they reach), one pinned
4-byte unit (the GLIDE byte), nine detours, three grown pointer tables, one
poke — all in the main-OS section; the bootstrap and every flash-
programming path are untouched. The SCALE half was **flashed** as
OCTATRICK1..3 (23-24 Sep 2026) and works on the unit; the GLIDE half is
measured under `ot_emu` through the virtual panel only (24 Sep 2026). The
13 Sep measurements below are the SCALE half's; logs and screens:
`out/_agents/quantizer/`.

## The scales

`qz_masks` in `quantizer.s`: bit k = semitone k above the root (the
track's pitch 0 = raw 64); the same set repeats an octave down. Names are
seven characters at most: the SEQUENCER window's value column is 33 px
wide (`PHRYGIAN` lost its N, `shots/a7_3_phrygian_x4.png` of the first
build).

| # | shown as | semitones |
|---|---|---|
| 0 | OFF | — |
| 1 | MAJOR | 0 2 4 5 7 9 11 |
| 2 | DORIAN | 0 2 3 5 7 9 10 |
| 3 | PHRYGN | 0 1 3 5 7 8 10 |
| 4 | LYDIAN | 0 2 4 6 7 9 11 |
| 5 | MIXOLYD | 0 2 4 5 7 9 10 |
| 6 | MINOR | 0 2 3 5 7 8 10 (natural minor / aeolian) |
| 7 | LOCRIAN | 0 1 3 5 6 8 10 |
| 8 | PENT.MN | 0 3 5 7 10 |
| 9 | PENT.MJ | 0 2 4 7 9 |
| 10 | MEL.MIN | 0 2 3 5 7 9 11 |
| 11 | HRM.MIN | 0 2 3 5 7 8 11 |
| 12 | WHOLE | 0 2 4 6 8 10 |
| 13 | BLUES | 0 3 5 6 7 10 |
| 14 | PHRYDOM | 0 1 4 5 7 8 10 |
| 15 | WH.DIM | 0 2 3 5 6 8 9 11 |
| 16 | HW.DIM | 0 1 3 4 6 7 9 10 |
| 17 | HUNGMIN | 0 2 3 6 7 8 11 |
| 18 | HIRAJOS | 0 2 3 7 8 |
| 19 | IN-SEN | 0 1 5 7 10 |
| 20 | IWATO | 0 1 5 6 10 |
| 21 | PELOG | 0 1 3 7 8 |
| 22 | DBLHARM | 0 1 4 5 7 8 11 |
| 23 | SUPRLOC | 0 1 3 4 6 8 10 |
| 24 | LYD.DOM | 0 2 4 6 7 9 10 |

## Where the setting lives, and how it persists

The SEQUENCER window (`0x40065b14` draws it, `0x40065c7c` creates its
list state at `0x460e43d8` with `pea 3; pea 3` = count / visible,
`0x40065cec` handles keys, `0x40065c98` the LEVEL knob) is a generic list
over three parallel pointer tables — labels `0x400b27d0`, getters
`0x400b27dc`, setters `0x400b282c` (CHAIN AFTER's `0x400659ec` /
`0x40065a40`, the two checkboxes) — with three rows. The build grows all
three to four (`TableGrow`, the new tables at `0x400d7100 / 7180 / 7200`,
the six operands repointed: `0x40065bd8`, `0x40065bde`, `0x40065cc4`,
`0x40065d3e`, `0x40065d58`, `0x40065d72`), the count becomes 4 (poke
`0x40065c7c`: `4878 0003` → `4878 0004`) and the window scrolls, as the
firmware's longer lists do: three rows fit the PATTERN CHANGE frame, a
fourth would land on its bottom edge. The one thing the stock draw loop
lacked is indexing the tables from the scroll offset — it started at 0
(`clrl %d2`), so the fourth row could never come into view; `qz_draw`
replaces that. [DOWN] three times shows `SCALE`, the LEVEL knob steps it
(clamped: −4 from PHRYGN stays OFF, +30 stops at LYD.DOM, a single report
of +3 from OFF is PHRYGN; `remix_menu.log`, `a*`), [YES] steps it with
wrap-around like CHAIN AFTER. The value byte `qz_scale` lives in the unit
(`0x400d6b80`; the main OS runs from DRAM) and is 0 in the image.

**The project file.** `project.work` is text, `KEY=value` lines. The
loader (`0x400866c4`, a strcmp chain per line) **rejects an unknown key**
(`0x40088212` → `0x40086d3a`, error −51), so a new key would make the
project unloadable on stock firmware. But the line loop skips any line
whose first character is `#` (`0x400867a2..0x400867ac`), before the
chain — the file's own header is three such lines — so the setting is
written as a comment: **`#SEQUENCER_SCALE=n`**, right after
`PATTERN_CHANGE_CHAIN_BEHAVIOR`. Stock firmware loads such a project
unchanged. The writer (`0x40088882..`, one sprintf / strlen / write per
key through `a4 / a3 / a2`) is detoured at the start of the SILENCE_TRACKS
line; the loader's `#` check is detoured to compare the line against the
key and parse the digits (0..24, anything else = OFF), storing only in a
storing pass (`58(%sp)` = 0); and the loader's entry resets the byte to
OFF for a storing pass (second argument ≠ 0), so a project without the
line loads as OFF.

## What was hooked, and what is displaced

All addresses are the stock 1.40C main OS at `0x40000400` (listing:
`out/_agents/direct-jump/mainos.dis`). Sites are asserted against the
stock bytes before anything is written (`manifest.py`).

| site | stock bytes (displaced) | kind | stub | replays |
|---|---|---|---|---|
| `0x40055170` knob handler `0x40055008`, the store | `1482 1a82 320e` — `move.b %d2,(%a2); move.b %d2,(%a5); move.w %fp,%d1` | jsr | `qz_knob` | the three, after recomputing d2 |
| `0x40050e60` p-lock editor, the lock store | `1384 8859 73b9 100b14cc` — `move.b %d4,(0x59,%a1,%a0.l); mvz.b 0x100b14cc,%d1` | jsr + 2 nops | `qz_plock` | the two (the store as `(0x59,%a0,%a1.l)`, the same address), after recomputing d4 |
| `0x4004fc58` CHROMATIC key → pitch | `45f2 ac04 71b9 100b14cf` — `lea (4,%a2,%a2.l*4),%a2; mvz.b 0x100b14cf,%d0` | jsr + 2 nops | `qz_chrom` | the two, after snapping a2 |
| `0x4004fbfe` CHROMATIC key handler, the note-off gate | `4ab9 46c7dd26 663e` — `tst.l 0x46c7dd26; bne 0x4004fc44` | jmp + nop | `qz_leg1` | the FUNC test; with GLIDE on and a key held: the old key's MIDI note-off, `jmp 0x4004fc44` (no voice note-off); else `jmp 0x4004fc06` / `0x4004fc44` as stock |
| `0x4004fc94` the same handler, sample trig vs trigless | `4ab9 46c7dd26 6716` — `tst.l 0x46c7dd26; beq 0x4004fcb2` | jmp + nop | `qz_leg2` | the FUNC test; with GLIDE on and a key held: held := the new key, `jmp 0x4004fc9c` (the trigless trig); else `jmp 0x4004fcb2` / `0x4004fc9c` as stock |
| `0x40065bca` SEQUENCER draw loop | `4282 4fef 0020` — `clr.l %d2; lea 32(%sp),%sp` | jmp | `qz_draw` | the `lea`; d2 := scroll offset × 4; `jmp 0x40065bd0` |
| `0x400866cc` project loader entry | `2c2f 05c0 202f 05c4` — `move.l 1472(%sp),%d6; move.l 1476(%sp),%d0` | jmp + nop | `qz_ld_entry` | both; `jmp 0x400866d4` |
| `0x400867a2` project loader, the `#` check | `122f 048f 7101 7a23` — `move.b 1167(%sp),%d1; mvs.b %d1,%d0; moveq #35,%d5` | jmp + nop | `qz_ld_line` | the three; `jmp 0x400867aa` (or `0x40088224`, next line, when the line is ours) |
| `0x400888aa` project writer, SILENCE_TRACKS line | `7339 8000 004f 2f01` — `mvs.b 0x8000004f,%d1; move.l %d1,-(%sp)` | jmp + nop | `qz_wr` | both, after writing our line; `jmp 0x400888b2` |

**The knob.** `0x40055008(slot, delta)` resolves the Part byte (kind 0:
`blob + part*6322 + 0x8edaa + track*30 + machine*6 + slot`), reads it into
d6, calls the slot's own handler (`descriptor+0x12a+4*slot`; PTCH's is
`0x40032d08`, a fractional accumulator that swallows a detent now and
then — stock from 64: `64 65 66 67 68 69 69 70`, `stock_knob_chrom.log`),
clamps to the descriptor's `[min, min+count-1]` (`+0x6a`, `+0x9a`) and
stores at `0x40055170`. `qz_knob` acts when the scale is on, the slot is
A, the page kind (`0x460d1684`) is 0 and the descriptor's slot A is named
`PTCH` (`descriptor+0x16`; STATIC `0x400d301c`, FLEX `0x400d31ae`, PICKUP
`0x400d3664` — THRU / NEIGHBOR have no PTCH, COMB's PTCH is on an FX page):
d2 := d6 stepped |delta| degrees in the direction of delta (`qz_quant`),
where a degree is a raw on a semitone (raw = 64 + 5·n, the table
`qz_pcraw`: raw−4 → pitch class or 0xff) whose class is in the scale's
mask; a start between degrees (a value set with the scale off, or a
fraction) goes to the nearest degree in the turn direction; at the ends
the value stays, which is the stock clamp. The screen, the SRAM mirror
(`a5`), the lock write for a held trig and the CC echo all take d2 after
the hook.

**A held trig.** A turn with a [TRIG] key held never reaches
`0x40055008`: the p-lock editor (found with `ot_emu`'s `watchmem` on the
lock byte, `plock_watch_stock.log`: the only writer is pc `0x40050e60`)
loops over the held steps, takes the step's lock byte — or the Part's
value when it is 0xff — through the same handler and clamp into d4 and
stores it at `track record + 0x59 + flat slot` (a0 = the record, a1 = the
flat slot). `qz_plock` recomputes d4 the same way from the old lock byte
(read at the hook, before the store) or, when there is none, from the
Part's PTCH. Note for the next hook of this kind: `objdump` prints the
8-bit displacement of an indexed operand in hexadecimal (`%a1@(59,%a0:l)`
is 0x59 = 89, the `+0x59` lock offset); the first build replayed it as
decimal 59 and the lock landed 30 bytes short (`plock_watch_remix.log`).
`plock_watch_remix_fixed.log` is the shipped build.

**Chromatic.** `0x4004fb94(track, key index 0..24, press)`: the index
(TRIG 13 = 12 = the root, TRIG 1 = −12, TRIG 16 = +3 at octave 0) becomes
the raw pitch `5·idx + 4` at `0x4004fc58`, the lock byte `0x46c7dfda +
t*32` the voice is trigged with (`0x40005030` / `0x46c80354`), the PTCH
lock a held or live-recorded trig receives (`0x40042158(track, 0, a2,
step)` at `0x4004fd1a`) and the box on the screen (`0x4004f5f8`).
`qz_chrom` snaps the index to the nearest in-scale class (`qz_pc25`, the
lower candidate checked first at each distance) before the `lea`. The
MIDI note the key sends out stays the key's own (`d3 + 71`, matched on
release).

**Persistence.** `qz_wr` pushes the byte, `pea qz_fmt(%pc)`
(`"#SEQUENCER_SCALE=%d\r\n"`) and the buffer, calls the writer's
sprintf / strlen / write exactly as the stock lines do, then replays.
`qz_ld_line` replays the `#` test; on a `#` line it compares the line
(d3) with `"#SEQUENCER_SCALE="`, parses the decimal, clamps 0..24 (else
OFF), stores unless `58(%sp)` (the parse-only flag) is set, and jumps to
the loop's next line; any other line continues into stock. `qz_ld_entry`
clears the byte when the load's second argument is non-zero (a storing
pass).

Register discipline: every stub saves what it uses beyond the registers
the displaced instructions write (`qz_knob`: d1; `qz_plock`: d1, and a2
pushed; `qz_chrom`: d0, d1; `qz_quant` saves all but d2); ColdFire
`movem` has no `-(sp)` form, so the saves are plain pushes. The unit
uses only ISA_A+ forms the stock code itself uses (`mvs/mvz`, `btst
Dn,Dy`, long compares, `mulu.l Dy,Dx`).

## GLIDE and legato (24 Sep 2026)

**The row.** `PROJECT > CONTROL > SEQUENCER`, [DOWN] ×4: `GLIDE OFF`; the
LEVEL knob steps it 1..127 (clamped), [YES] steps with wrap-around
(127 → OFF). The count poke is now `pea 3` → `pea 5` (the window scrolls
its three rows over five: CHAIN AFTER, SILENCE TRACKS, LFO AUTO CHANGE,
SCALE, GLIDE), the three grown tables carry two entries each
(`qz_lbl_glide` / `qz_get_glide` / `qz_set_glide`; `qz_set_any` is the
shared clamp-or-wrap tail, a0 = the byte, d1 = its maximum). The getter
prints the number with the firmware's `sprintf` (`0x40013a08`, `"%d"` at
`0x400b465d`) into a buffer in the unit.

**The byte.** `qz_glide` is **pinned at `0x400d2cdc`** (`glide.s`, a
4-byte `Linked` unit; the last long of the second zero run
`0x400d24d0..0x400d2ce0`, whose start holds the synth's page cave) because
two units read it: this one by symbol (the build's link resolves it) and
the synth voice cave, which is position independent with ratified bytes and
therefore reads an OS absolute. Each unit has ONE accessor (`qz_glide_of`
here, `sy_glide` in `synth.s`: d2 = track → d0 = 0..127), so the storage can
move — the AMP page's XVOL slot was considered and rejected for this
build (`modules/synth/README.md`, "AMP slot F").

**The project line.** `qz_wr` prints `#SEQUENCER_SCALE=%d` then
`#SYNTH_GLIDE=%d` (a shared `qz_wr_line`); `qz_ld_line` matches either key
(`qz_l_cmp`) and clamps 0..24 / 0..127 (else OFF); `qz_ld_entry` clears
both bytes for a storing load. A stock unit skips both lines.

**Legato.** The CHROMATIC key handler `0x4004fb94(track, key 0..24,
press)` keeps ONE held key per track at `0x460d171d + track` (key + 1, 0 =
none; the MIDI note out is key + 71, sent at `0x4004fd62` on the press and
matched on release at `0x4004fbe6`: a release of any other key returns at
once). Stock, a press while a key is held first ends that key at
`0x4004fbfe..0x4004fc40` — mailbox `0x46c80354[track] |= 0x40` (the frame
builder's note-off, `0x4000b4e4`: the voice state `0x80004858[...] := 4`,
i.e. the AMP release), the MIDI note-off, `held := 0` — then starts a new
voice at `0x4004fcb2` (`0x40005030`, cmd `0x1d`: bit 2 = start). With FUNC
held (`0x46c7dd26`) stock skips the note-off and posts a TRIGLESS trig at
`0x4004fc9c` instead (mailbox `|= 0x119`, no bit 2: the frame builder
applies the staged PTCH lock `0x46c7dfda + track*32` to the track's lock
block at `0x4000b75c` and starts no voice — `0x4000b5a8` starts one only
on bit 2). `qz_leg1` / `qz_leg2` take that path without FUNC when GLIDE is
on and a key is held: the MIDI note-off of the old key still goes out
(external gear sees stock's note sequence), the voice note-off does not,
and the new key becomes the held key (its release ends the note as stock;
the old key's release matches nothing). GLIDE OFF, FUNC held, a release, a
first key, or audio-track trigs off (`0x8000004c` bit 0): the stock bytes'
paths, unchanged. Legato is not limited to synth tracks: a sample track
changes pitch without a restart the way FUNC + key does. Live recording
records the legato press as stock records a key press (a sample trig with
the PTCH lock, `0x40042d1c`), not as a trigless trig.

### Measurements (24 Sep 2026, `out/mainos_cf.bin` = `REMIX=tim make cf`, the panel on 8593 with a copy of the OTLIVE card, `--sound on`; scripts and rows in the session scratchpad `glide_*.py`, `ga2/`, `ga3/`, `gs2/`)

**1. The rows** (`gm/`): boot (the card's project: CHAIN AFTER 256/16 —
a 17 saved by the 13 Sep build, clamped; SCALE MIXOLYD; GLIDE OFF), PROJECT
> CONTROL > SEQUENCER, [DOWN] ×4 → `GLIDE OFF` (list state offset 2,
cursor 4, count 5, visible 3); LEVEL +1 → 1, +63 → 64, +100 → 127
(clamped), −127 → OFF, −3 → OFF, +64 → 64, [YES] → 65, at 127 [YES] → OFF
(wrap); [UP] → SCALE, [UP] → LFO AUTO CHANGE. The byte at `0x400d2cdc`
followed every step. CHAIN AFTER on the same window: PAT.LEN → **DIRECT**
→ 2/16 by LEVEL +1, DIRECT + [YES] → 2/16, −20 → PAT.LEN; the byte
`0x8000004e` / mirror `0x100b14ae` read 0 → 1 → 2.

**2. Legato and glide, T2 = the FM synth** (`ga2/`, `ga3/`; T2's Part set
to STRT 0 / LEN 0 = a clean carrier, AMP HOLD INF / REL 40; CHROMATIC
mode; the card's SCALE = MIXOLYD snaps [TRIG 16] (+3) to +2 semitones —
the quantizer at work). Hold [TRIG 13] (C4), press [TRIG 16] 0.6 s later,
release 13 after 1.2 s, release 16 after 0.6 s more; zero-crossing pitch
and 10 ms RMS from the panel's main out:

| GLIDE | f(A) → f(B) | pitch after B's press: 63 % / 95 % of the way | A released, B held | B released |
|---|---|---|---|---|
| OFF | 261.6 → 293.7 Hz (+2.00 st) | a step (16 / 16 ms: the stock restart) | 293.7 Hz | −82 dB in 100 ms, silence |
| 1 (10 ms) | 261.6 → 293.7 | 41 / 61 ms | 293.7 | released |
| 64 (100 ms) | 261.6 → 293.6 | 126 / 326 ms | 293.7 | −71 dB in 100 ms, silence |
| 127 (1 s) | 261.6 → 291.9 (still moving at 3.2 s) | 947 / 2,387 ms | 292.5 | released |

(the wall-clock press marks carry ~20 ms of panel latency, so 126/326 ms
is τ ≈ 100 ms, 947/2,387 ms τ ≈ 1 s.) The held-key byte `0x460d171e`
read 0 after every run. A single [TRIG 16] with GLIDE 64 sounds at
293.6 Hz from its first 10 ms bin (no slide from the previous note). With
AMP ATK 64 (a 600 ms attack ramp, −50 → −19 dB on a fresh note) the
legato press showed no ramp: the AMP envelope is not retriggered.
What does move: a **2.3 dB level step 200 ms after the second key** (−17.4
→ −19.7 → −16.6 dB across the GLIDE 64 transition, the same at GLIDE 1
once the pitch had settled) — and exactly the same step follows every
fresh note start and every stock FUNC + key trigless trig on this track
(`ga3/`, `ga4/FUNC_key_stock_trigless`: −19..−20 dB for 200 ms, then
−17). It is not the AMP envelope (above) and not the pitch (GLIDE 1); it
is the DSP's own response to a trig word — T2's FX1 is a FILTER with a
0.5 s envelope (DEPTH 111, DEC 49), the likely consumer, but DEPTH 0 left
the resonant filter (Q 94) ringing through the sweep and settled nothing.
Posting the trig word without mailbox bit 3 (`0x111`; frame flag 0x20 /
event-byte bit 3) changed neither the step nor the glide (t63 113 ms), so
the legato keeps stock's own trigless word (`0x4004fc9c`, `0x119`): the
legato behaves exactly as FUNC + key does, minus the FUNC.

**3. Sequenced** (`gs2/`, GLIDE 64): the pattern cleared, T2 trig on step 1
(PTCH −12 from the Part: 130.8 Hz) and a trigless trig on step 9 with a
PTCH lock of +12 (`0x7c`): PLAY → 130.8 Hz, then from step 9 (1.0 s at
120 BPM) a two-octave glide to 523.0 Hz, 63 % / 95 % at 120 / 320 ms;
GLIDE OFF: the same pattern jumps in one 10 ms bin (t63 = t95 = 40 ms, the
step-time estimate). (The level rises 14 dB with the pitch: T2's FX1 is
a FILTER with BASE 0 / WIDTH 72, its response, not the voice.)

**4. Persistence** (`pt/`): PROJECT > SYNC TO CARD with CHAIN AFTER =
DIRECT, SCALE MIXOLYD, GLIDE 64 → the card's `OTLIVE/PROJECT/project.work`
reads `PATTERN_CHANGE_CHAIN_BEHAVIOR=1` / `#SEQUENCER_SCALE=5` /
`#SYNTH_GLIDE=64` (read on the Mac through `/card/eject`); `/card/insert`
(a power cycle) reloads `0x8000004e = 01`, `0x400d2cdc = 40`.
Then the migration cases, editing the mounted card's `project.work` and
`/card/insert`-ing (`pt3/`, `bc/`): `PATTERN_CHANGE_CHAIN_BEHAVIOR=17` +
`#SYNTH_GLIDE=100` (a project saved by the 13 Sep DIRECT JUMP build) →
`0x8000004e = 0x10` (the window shows **256/16**), GLIDE **100**;
`=1` + `#SYNTH_GLIDE=33` → `0x01` (**DIRECT**), GLIDE **33**; `=5` +
`#SYNTH_GLIDE=0` → `0x05` (**6/16**, a stock value), GLIDE **OFF**. The
mirror `0x100b14ae` followed each time. A storing load without the GLIDE
line starts from OFF (`qz_ld_entry`, as SCALE's).

## Legato and the live recorder (24 Sep 2026, OCTATRICK6 report)

**Reported on hardware:** GLIDE on, CHROMATIC, live recording, a second key
pressed while the first is held slides live but playback restarts the note.
**Cause:** the same key press is handed to the live recorder a few
instructions after the trig -- `0x4004fcd8..0x4004fd24`, run while
`0x460d172a` is set (live recording, or a trig held): with FUNC held
(`0x46c7dd26`) stock calls `0x4004271c(track, 0x46c7e956)`, which places a
**trigless** trig on the current step, otherwise `0x40042d1c(...)`, a
**sample** trig; then `0x40042158(track, 0, raw pitch, step, ...)` writes
the PTCH lock on the step returned. `qz_leg2` played the legato press
trigless but the recorder still took the no-FUNC branch. **The fix:** a
third detour on the same press, `qz_leg3` at `0x4004fce0` (`tstl
0x46c7dd26; beqs 0x4004fcf8`, 8 bytes): `qz_leg2` sets `qz_legato` when
and only when it takes the GLIDE legato path (cleared at every press;
FUNC held and the paraphonic VOIC 2..4 path leave it clear), and `qz_leg3`
sends a legato press down the trigless branch -- exactly what FUNC + key
records. GLIDE off, FUNC held, VOIC 2..4 and non-synth tracks: stock.

Measured (`poly/liverec.py`, T2 = SYNTH, VOIC 1, INDX 0, SCALE OFF, 120 BPM,
the pattern cleared before each; T2's track record `0x400e21e0 + 0x91a`:
byte 7 = the sample-trig mask of steps 1-8, byte 15 the trigless mask of
steps 1-8 (byte 14 steps 9-16), locks at `+0x59 + step*32`):

| recording | record bytes changed | playback (pitch per 25 ms) |
|---|---|---|
| key 13 alone | `[7] = 0x08` (sample trig, step 4), lock PTCH 64 | 261.4 Hz steady |
| FUNC + key 6 while 13 held (stock trigless) | `[7] = 0x08`, `[14] = 0x02` (trigless, step 10), lock 29 on step 10 | 261.4 then 252.6 218.4 199.9 188.9 183.0 180.0 178.2 177.0 -> 174.6: a glide, no burst |
| key 6 while 13 held, GLIDE 64 (the fix) | `[7] = 0x08`, `[15] = 0x80` (trigless, step 8), lock 29 on step 8 | 261.4 then 252.1 218.2 199.7 188.8 183.0 180.0 178.1 177.0 176.3 175.8 175.4: the same glide, no burst, no restart |
| the same with GLIDE off | `[7] = 0x88` (sample trigs, steps 4 and 8), lock 29 on step 8 | 261.3 then 175.2 at once: a retrigger, as stock |

(The stock and fixed steps differ -- 10 vs 8 -- only because the FUNC press
in the driver adds 0.15 s.)

## The played note length (25 Sep 2026, OCTATRICK7 report)

**Reported on hardware:** the recorded slide plays back, but the notes drone
for the whole AMP HOLD -- audio tracks have no note length and live recording
never writes the key release. **Now, on a synth track (`qz_is_synth`) during
live recording, a key's played length is written as a HOLD lock on the step
its press recorded.** The HOLD byte's unit is sequencer steps, through the
firmware's own 128-entry table of strings (`0x400d18d0`: 0.0078 = 1/128
step, 1.0000 at raw 14, 4.0000 at 46, 128.0 at 126, INF = 127; measured:
HOLD 40 = "3.2500" runs 0.40 s at 120 BPM); `qz_hold128` holds the same
values in 1/128 steps and the lock is the smallest entry that is not shorter
than what was played (a tap is never silent: 60 ms recorded as 0.5000).

The path: `0x4004fd06` (`addql #8,%sp; tstl %d0; blts`, right after the
recorder returned the step in d0) -> `qz_leg4` notes the press in one of the
track's four slots (`qz_press`: key, step, in use, the engine's tick count).
Time comes from the synth engine's clock (`po_clock`, published at
`qz_clock` = `KEYS_AT + 40`: a monotonic count of the sequencer's (step,
tick) changes -- the step word `0x800065b2` wraps at the pattern end, the
count does not -- and ticks a step, the largest tick byte `0x800065b6` seen
+ 1; 6 at 1x), ticked once a frame from the frame builder's LFO pass, so it
runs from boot. `qz_holdrel` turns elapsed ticks into 1/128 steps
(`* 128 / ticks a step`), looks the raw value up and calls the stock lock
writer `0x40042158(track, 13, raw, step, 0x46c7e956)` (flat slot 13 = AMP
HOLD; the writer itself refuses once live recording has stopped). Who calls
it: a key's release (`qz_leg0`, every key on a paraphonic track; on a VOIC 1
track only the HELD key's release, which ends the note); a press that ends
the held note with the stock note-off (`qz_leg1`, GLIDE off). **A legato or
FUNC + key press does not end the held note**: the DSP's HOLD runs from the
voice START and a trigless step's HOLD lock re-lengthens it from there
(measured: a trigless HOLD of 5.75 ended the note 5.75 steps after the first
press, not after the trigless step), so the new step inherits the chain's
start (`qz_chain_of` -> `qz_chain` -> `qz_leg4`) and the held key's release
writes the chain's whole length on every step of it (`qz_holdall`).
Non-synth tracks, programmed trigs and playing without recording: untouched.

Measured (`poly/holdrec.py`, T2 = SYNTH, VOIC 1, GLIDE 64, INDX 0, AMP ATK 0
HOLD INF REL 20, 120 BPM = 8 steps a second, fresh boot; level per 50 ms
from the note's onset, "ends" = the bin that drops below -40 dBFS):

| recording | HOLD locks | live take ends | playback ends |
|---|---|---|---|
| key 13 held 0.55 s, legato key 6 to 1.2 s (GLIDE 64) | step 4 sample 9.50, step 8 trigless 9.50 | bin 25 (1.25 s) | bin 25, the pitch 260 -> 240 220 200 180 continuous, no restart |
| the same with GLIDE off | step 4 sample 4.0000, step 8 sample 5.75 | bin 25 | bin 25 (a restart at bin 11, as stock) |
| the same with FUNC + key 6 (stock trigless) | 5.25 on both (the HELD key 13's release ends the note, stock's rule) | -- | bin 14 |
| a 60 ms tap | step 4 sample 0.5000 | bin 2 | bin 2-3 |
| HOLD knob 40 = 3.2500, a programmed trig, no recording | none | -- | bin 8 (0.40 s): the stock DSP envelope, unchanged |
| VOIC 3, CHRD MAJ, a chord held 0.5 s | step 4 sample 4.25 (+ the CHRD lock 36) | bin 11 (0.55 s) | bin 12 (0.60 s), lines 261.6 / 329.6 / 391.9 |

Sizes: `quantizer.s` 2,916 B (`REMIX=tim make cf`, at `0x400d6d00`), `keys.s`
44 B, **680 B of the third run left**.

## Paraphonic keys (24 Sep 2026)

The synth machine's LFO page has a VOIC slot (`modules/synth/README.md`
"Phase 5": 1..4, the Part's LFO page byte `+ track*24 + 2`, SPD3's storage);
on a FLEX track whose assigned FLEX slot's sample is named SYNTH*
(`qz_is_synth`, the synth page's own test, run on the UI thread at each key
event) with VOIC 2..4 (`qz_polytrack`), the CHROMATIC handler `0x4004fb94`
keeps every held key instead of one:

| site | stock | with VOIC 2..4 on a synth track |
|---|---|---|
| `0x4004fbde` (new, `qz_leg0`, 6 bytes `mvzb %a0@(0,%d2:l),%d1; movel %a2,%d0`) -- the release path | a release of a key that is not HELD returns at once; the HELD key's release runs the note-off block | the key's bit leaves `qz_pmask[t]` (the engine releases that key's voices); with keys still held its MIDI note-off goes out and nothing else happens; the LAST key makes itself HELD first, so stock's block runs -- the voice note-off (mailbox `|= 0x40`, the AMP release), the MIDI note-off, HELD := 0 |
| `0x4004fbfe` (`qz_leg1`) -- a press while a key is held | ends the held key first (voice note-off, MIDI note-off, HELD := 0), then trigs | ends nothing: straight to the trig (checked before the GLIDE legato test, so legato is off on such a track) |
| `0x4004fc94` (`qz_leg2`) -- the trig | FUNC held: trigless; else the stock trig, HELD := the key | FUNC held: trigless as stock; else the key's bit joins `qz_pmask[t]`, `qz_pkey[t]` := the key's index + 1 for the engine (read and cleared at the voice start; 0 there = a sequencer trig), the stock trig starts a fresh voice, HELD := the key |

`qz_pkey[8]` (bytes) and `qz_pmask[8]` (longs, bit = key index 0..24) are
`keys.s`, pinned at `KEYS_AT = 0x400d2cb0` -- 40 bytes of the second zero
run between the synth page cave (ends `0x400d2c6c`) and the GLIDE byte.
**Chords obey SCALE**: `qz_scale_mask` (d0 := the current scale's
pitch-class mask from `qz_masks`, 0 = OFF) is the accessor the synth's
engine calls to snap every chord note the way `qz_chrom` snaps a key
(nearest degree, the lower candidate first); it reaches it through
`scale.s`, six bytes pinned at `SCALE_AT = 0x400d2ca8` (`jmp qz_scale_mask`,
linked after this unit so the symbol resolves; the engine checks the `jmp`
is there, so a remix without this module snaps nothing) -- the mask table
has one copy. VOIC 1 (a stock byte reads 1), audio-track trigs off, every
other track: stock, byte for byte -- the mono synth with GLIDE legato.
Measured (the synth README's numbers): at VOIC 2, keys 13 + 9 held together
play C4 and G#3; at VOIC 4 keys 13, 9, 6 pressed in turn and released in
turn play `[C4] [C4 G#3] [C4 G#3 F3] [G#3 F3] [F3]` then silence; a MAJ
chord under DORIAN or PHRYGN comes out 1 : 1.189 : 1.498 (minor), under
SCALE OFF 1 : 1.26 : 1.498. Unit size: `quantizer.s` 1,888 B (`REMIX=tim
make cf`, at `0x400d6d00`), `scale.s` 6 B, `keys.s` 40 B, `glide.s` 4 B.

## Measurements (all `out/_agents/quantizer/`)

Image: `REMIX=quantizer make bus` → `out/mainos_bus.bin`, 1,112,560
bytes, **1,250 bytes changed** vs `out/raw/section_3_MAIN_OS.bin`
(`build.log`; the unit at `0x400d6b80`, the tables at `0x400d7100..`).

⚠️ **For a unit, build it with `make cf`, not `make bus`** (15 Sep 2026).
`make bus` rebuilds the FX2 chooser from the remix's rows and this remix
has none, so its image offers NONE as the only EFFECT 2 effect: the
fourteen stock effects keep their code and dispatch (the report's `KEPT
STOCK`; both DSP payloads are byte-identical to stock) but cannot be
selected. Eleven of the 1,250 bytes are exactly that — the three `lea`
sites that find the chooser list (`0x400d6090` → a one-row list at
`0x400d6b00`), the viewport literal (7 → 1) and the row itself.
`REMIX=quantizer make cf` → `out/mainos_cf.bin`, **1,239 bytes changed**:
the same unit, tables, detours and poke, with the chooser, the FX2 id and
cursor tables and both DSP payloads byte-identical to stock (the build
compares those spans against the stock image before writing; `make
image-cf` packs it). The `tim` image (this module + DIRECT JUMP, 1,657
bytes) booted under the panel shows the stock chooser — NONE, FILTER, EQ,
DJ EQ, PHASER, FLANGER, CHORUS, SPATIALIZER, COMB, COMPRESSOR, LOFI, DELAY,
PLATE, SPRING, DARK — and the SCALE row still turns OFF → PHRYGN
(`out/_agents/cfbuild/`). Panel: `tools/panel/panel_server.py --image <remix> --project
out/_projects/otlive/OTLIVE/PROJECT --set OTLIVE --name PROJECT --sound
off --card out/_agents/quantizer/card.img` on port 8596, stock on 8597,
driven through `/key`, `/tap`, `/knob`, `/peek`, `/screen.txt`
(`shots/*_x4.png` are the text screens at 4×). PTCH slot of T1 (STATIC,
part 0) `0x40170f8a`, of T5 (FLEX) `0x40171008`; T5 step 1's PTCH lock
`0x400e46a1`; the chromatic staging byte `0x46c7dfda + t*32` (with
`--sound off` no frame consumes it, so it keeps the pitch the key
produced).

**1. The menu** (`remix_menu.log`, `a*`, `m*`, `s1_scale_3`). Fresh boot:
`qz_scale` = 0. PROJECT > CONTROL > SEQUENCER opens with CHAIN AFTER /
SILENCE TRACKS / LFO AUTO CHANGE as stock (`a3_sequencer_window_x4.png`);
[DOWN] ×3: the list state reads offset 1, cursor 3, visible 3, count 4 and
the window shows SILENCE TRACKS / LFO AUTO CHANGE / **SCALE OFF**
(`a6_down3_scale_row_x4.png`); LEVEL +1 ×3 → MAJOR, DORIAN, PHRYGN (byte
1, 2, 3; `a7_*`); −4 → OFF and stays; +30 → 24 (LYD.DOM, `a9`); one +3
report from OFF → 3.

**2. PTCH, PHRYGIAN, T1** (`remix_knob_phrygian.log`, `k*`). From raw 64
(+0.0), +1 per report ×8: `69 79 89 99 104 114 124 124` = **+1, +3, +5,
+7, +8, +10, +12, +12** (the screen reads `+1.0` after the first,
`k1_up1_raw69_x4.png`); −1 ×15 from 124: `114 104 99 89 79 69 64 54 44 39
29 19 9 4 4` = +10 … 0, **−2, −4, −5, −7, −9, −11, −12, −12**; one report
of +3 from 4 → 29 (−7, three degrees), −3 → 4. A value set with the scale
OFF (`remix_locks_chrom.log`): raw 84 (+4.0, 24 stock detents) then
PHRYGIAN: +1 → 89 (+5), −1 → 79 (+3), −1 → 69 (+1); a fraction, raw 86
(+4.4): −1 → 79 (+3), +1 → 89 (+5). RATE (slot D, `0x40170f8d`, default
127): −1 −1 −1 +1 −5 +5 → `126 125 124 125 120 125`, one unit per detent,
untouched — the manual's RATE is a playback speed (0 = stopped, negative
= backwards; the SETUP page's RATE mode PTCH/TSTR only chooses whether the
speed change also changes pitch), not a semitone quantity.

**3. A held trig** (`final_pass.log`, `f2_trig1_held_lock`;
`plock_watch_remix_fixed.log` is the same under a bare `ot_emu`). T5,
GRID RECORDING, [TRIG 1] held, PHRYGIAN, from no lock (0xff) and Part 64:
+1 +1 +1 → lock `69 79 89` (+1, +3, +5); −1 ×4 → `79 69 64 54` (+3, +1,
0, −2); +5 → 99 (+7, five degrees); −20 → 4 (−12, clamped); the Part byte
stays 64 throughout. Knob push removes the lock (0xff); −1 with no lock
→ 54 (−2, from the Part's 0). With OFF the same run writes `41 41 42 42
41 45 33` — the stock editor's own sequence (`plock_watch_stock.log`).

**4. CHROMATIC** (`remix_locks_chrom.log`, `c*`; stock in
`stock_knob_chrom.log`). Trig mode via [FUNC] held + [DOWN] ×2
(`s8_list_chromatic`). Stock / OFF: TRIG 13 14 15 16 12 11 10 9 8 7 1 →
raw `64 69 74 79 59 54 49 44 39 34 4` = 0 +1 +2 +3 −1 −2 −3 −4 −5 −6 −12.
PHRYGIAN: `64 69 69 79 54 54 44 44 39 29 4` = 0, +1, **+1** (from +2:
+1 and +3 tie, the lower wins), +3, **−2** (from −1: −2 and 0 tie),
−2, **−4** (from −3), −4, −5, **−7** (from −6), −12. Live recording
(REC+PLAY, TRIG 15, STOP): with PHRYGIAN the T1 track record changes at
`+0x139`: `ff → 45` (the recorded PTCH lock = 69 = +1, the snapped
value) and the trig bit; with OFF `+0x119`: `ff → 4a` (74 = +2, stock).

**5. OFF vs stock, boot A/B** (`ab/`). `tools/emu/ot_emu/oracle/drive.py
--emu out/emu/ot_emu --image <stock | remix>` (the `inter` battery:
boot, YES, MIXER, NO, T1 ×2, DOWN, RIGHT, NO, NO, PLAY 20×100 ms, STOP
5×100 ms). `ready.txt`, `stamps.txt`, `peeks.txt`, `stderr.txt` are
byte-identical; `tx.bin` (the UART stream) is **18,297 vs 18,289 bytes**:
with every LED-level pair (`0x3n <id>`) removed the two streams are
identical (16,873 bytes — every LCD block, every LED row), and the
difference is 712 vs 708 pairs, one fewer `0x3d`/`0x3f` toggle each of
LEDs `0x24` and `0x25` — a breathing pair whose phase against the
battery's fixed windows shifted, because the project load now runs the
two loader detours on every line (`boot.log`: 65,402 vs 65,410 vectors
acknowledged over the boot; a stock-vs-stock re-run, `ab/stock2`, is
byte-identical, so the shift is real, not noise). No screen and no state
peek differs. The battery never opens the SEQUENCER menu.

**6. Persistence** (`persist_pass.log`, `s*`, `r*`). PHRYGN set, PROJECT
> SAVE (the PROJECT menu remembers its column: [LEFT] first), YES, YES
(`s5_save_confirm`): the persistent card's `project.work` and
`project.strd` both read `PATTERN_CHANGE_CHAIN_BEHAVIOR=0` /
`#SEQUENCER_SCALE=3` / `PATTERN_CHANGE_AUTO_SILENCE_TRACKS=0` (the
original file's cluster survives as stale data without the line). The
server was killed and the card cold-booted with `--card` alone: `qz_scale`
= 3, the SEQUENCER window shows **SCALE PHRYGN**
(`r1_sequencer_after_reboot_x4.png`), and PTCH +1 ×3 from the saved 65
→ `69 79 89`.

**7. Gates** (`verify_steps.log`, `REMIX=quantizer`): `make bus`,
`cycle_count`, `verify_slots`, `label_fmt`, `verify_octakit`,
`verify_midiscenes` (SKIP: submodule), `verify_dram_boot`,
`verify_labels`, `verify_menushortcut`, `verify_cfprobe`,
`verify_busscreen`, `verify_ccpage2`, `verify_hidden`, `verify_grains`,
`verify_menu`, `verify_burn` (its usual SKIP), `verify_twocore`,
`verify_onebus` all exit 0; `verify_replaces` fails only on the eight
MIDI SCENES remixes whose submodule is not checked out (pre-existing,
`make check` stops there); `verify_modenames` reports "no module declares
mode_views" (the Makefile's SKIP).

## What does not work, and what is left

- **NEW PROJECT** does not run the text loader, so the scale byte keeps
  its value until a project is loaded or the setting is changed; a saved
  project without the line loads as OFF (measured after the first, wrong
  save: cold boot → 0).
- **Scene locks** on PTCH (a SCENE key held) and the **MIDI CC-in** path
  (`0x40054cd8`, `0x40062550`) write PTCH unquantized; only the two knob
  paths and the chromatic keys are hooked. The MIDI-in chromatic notes
  (`0x4000e6e2`, `5·note − 100`) are not snapped either.
- The chromatic key's **MIDI note out** is the key's own, not the snapped
  degree (the release matches on it).
- With MIDI AUDIO TRK CC OUT set to EXT only (`0x8000004a` bit 0 clear)
  the knob handler takes its accumulator path and stores nothing itself;
  not measured.
- The boot A/B differs by the phase of one breathing LED pair (above);
  the two per-line loader detours are the cost of a file format whose
  stock reader rejects unknown keys.
- **The GLIDE half is not flashed** (the SCALE half is, OCTATRICK1..3).
- **Legato and live recording**: a legato key press is recorded as a
  normal trig with its PTCH lock, not as a trigless trig (stock's
  recording path is untouched); place trigless trigs by hand for slides.
- **The old key's MIDI note-off** goes out at the legato press (as stock
  sends it before a fresh trig), so an external mono synth does not see a
  MIDI legato.

Background: `docs/firmware/PARAM_PAGES.md` (the descriptor layout),
`docs/firmware/midi_re_note.md` (the chromatic lock block),
`tools/panel/KEYMAP.md` (the knob store, the lock bytes), `CONTEXT.md`,
`modules/direct-jump/README.md` (the CHAIN AFTER menu and the project
file).
