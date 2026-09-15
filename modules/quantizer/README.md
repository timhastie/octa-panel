# Scale quantizer

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

One linked ColdFire unit (1,292 bytes, floating, linked by the build at
the address it lands on), seven detours, three grown pointer tables, one
poke — all in the main-OS section; the bootstrap and every flash-
programming path are untouched. **UNFLASHED**; everything below is
measured under `ot_emu` through the virtual panel, 13 Sep 2026. Logs and
screens: `out/_agents/quantizer/`.

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
- **Not flashed.**

Background: `docs/firmware/PARAM_PAGES.md` (the descriptor layout),
`docs/firmware/midi_re_note.md` (the chromatic lock block),
`tools/panel/KEYMAP.md` (the knob store, the lock bytes), `CONTEXT.md`,
`modules/direct-jump/README.md` (the CHAIN AFTER menu and the project
file).
