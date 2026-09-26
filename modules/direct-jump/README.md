# Direct jump

**CHAIN AFTER's unused value 1 becomes DIRECT.** With it selected, a pattern
chosen while the sequencer runs ([PATTERN] + [TRIG], [BANK] + [TRIG], MIDI
program change) takes over at the **next step boundary**, at the step count
the old pattern had reached, instead of at the old pattern's end or after
its CHAIN AFTER length. Selected during step 11: the pattern number and the
counter switch at step 12, the old pattern still sounds its step 12 at that
tick, and the new pattern's first sounding step is its step 13 — continuous,
nothing skipped or doubled (audio-measured 26 Sep 2026). The Analog Four / Analog Rytm "direct jump", on the Octatrack's own
change-length parameter. Off by default: DIRECT is a position of a setting
every project already stores, so a project that never selects it plays
exactly as stock.

One ColdFire cave (358 bytes, floating, position independent), two hook
sites and four fixed pokes (two table entries, two branches) — all in the
main-OS section; the bootstrap and every flash-programming path are
untouched. The 13 Sep 2026 form (an 18th index, two relocated 18-entry
tables, eight `lea` repoints and six widened clamps; flashed as
OCTATRICK1..3) was replaced on 24 Sep 2026 by the index-1 form described
here; the sequencer hooks are unchanged. The 13 Sep measurements below
were taken on the 18th-index build; the index-1 build's own measurements
follow them ("24 Sep 2026: index 1").

## Where the option lives, and why

`PROJECT > CONTROL > SEQUENCER > CHAIN AFTER`, LEVEL knob:
`PAT.LEN, DIRECT, 2/16, 3/16 … 256/16`.

CHAIN AFTER is the Octatrack's change length (manual 8.6.3; the per-pattern
override is PATTERN SETTINGS > USE PAT SET. / USE PRJ SET., 12.11.1). It is
stored as one byte, `0x8000004e` (UI mirror `0x100b14ae`), an index 0..16
into the step table at `0x400d80dc` (`-1, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32,
48, 64, 96, 128, 192, 256`), and the project serializer writes it as
`PATTERN_CHANGE_CHAIN_BEHAVIOR=<n>`. **Index 1 ("1 step") is unused by
stock**: the menu setter skips it in the direction of travel (`0x40065a22..2a`:
a result of 1 becomes `delta + 1`, i.e. 2 going up and 0 going down), the
project loader bumps a saved 1 to 2 (`0x40087820..38`, after its 0..16
clamp at `0x4008780c/12`), and the UI mirror's sanitiser (`0x40010212..38`)
only clamps 0..16. So DIRECT takes index 1: the setter's skip and the
loader's bump become plain branches, the stock step table's entry 1
becomes `-1` so every stock reader treats DIRECT as PAT.LEN, and the label
table's entry 1 points at stock's own `DIRECT` string. Nothing is
relocated and no clamp changes. The other candidate, a PERSONALIZE row,
needs three relocated arrays and five repoints (the historical
`patch_menu.s`, `docs/history/NOTES.md`) and lives in a RAM block no
serializer writes; CHAIN AFTER is the parameter the manual documents for
exactly this decision and it already exists at both the project and the
pattern level.

**Migration.** A project saved by the 13 Sep build carries
`PATTERN_CHANGE_CHAIN_BEHAVIOR=17`; this build's loader (the stock 0..16
clamp) loads it as **256/16** — select DIRECT again and save. A project
saved with DIRECT by this build (`=1`) loads on **stock** firmware as 2/16
(the stock bump) and on the 13 Sep build as 2/16 likewise (its setter never
produced a 1, its loader still bumped it).

**Measured under PER TRACK scales (26 Sep 2026, emulator, 41 jumps).** The
positioning after the switch is the stock arranger's (the module only plants
the start step), and it matches the destination pattern's own run at the same
master step in every same-scale case, including odd lengths, MASTER LENGTH
INF, master scale 2X and a change on every step. Two small deviations come
from that stock code, which pattern-end changes never exercise because their
start is 0: a track faster than the master whose step is mid-way at the
switch fires its next step two ticks (~41 ms at 120 BPM) early and re-locks
within a step; a track whose SCALE differs between the two patterns and whose
planted position has a tick residue runs one tick (~21 ms) early until the
next MASTER LENGTH restart (with INF, until the next change).

**Index 1 elsewhere (checked, unchanged).** The per-pattern CHAIN BEHAVIOR
setter in PATTERN SETTINGS (`0x40081d74..0x40081e30`) skips 1 the same way
the project setter did and prints its own label tables (`0x400b2fae`
PLEN/1/16/…, `0x400b2f52` and `0x400b2ff2` TR.LEN/1/16/…), so DIRECT stays
a project-level choice: a pattern on USE PAT SET. keeps its own length, one
on USE PRJ SET. (byte `+0x8e56` < 0) follows the project. The MIDI
program-change receiver (`0x4000dada`), the [PATTERN]/[BANK] + [TRIG] keys
(`0x40056b68`, `0x40055f84`), the arranger (`0x4004a652`, with its row's
absolute change-at) and the other callers of `0x400a1030` all reach the
same setter `0x400a0570` that `dj_queue` hooks; none of them reads the
index itself. The eight readers of the step table (below) read entry 1
through the table, so the `-1` covers them all.

**Precedence is the firmware's own.** The tick handler reads the *playing*
pattern's byte at `pattern+0x8e56` and uses the project's `0x8000004e`
only when that byte is negative (`0x400a42fa`, and the same rule at
`0x400a413e`); `-1` is what PATTERN SETTINGS > USE PRJ SET. writes. DIRECT
therefore applies to patterns that follow the project setting. A pattern
with its own CHAIN BEHAVIOR keeps it (its range stays 0..16 — DIRECT is
per project). The OTLIVE fixture's patterns carry `0` (USE PAT SET. =
PLEN, the checkbox unchecked), which is why the measurement below checks
USE PRJ SET. on the pattern being left first.

## What was hooked, and what is displaced

All addresses are the stock 1.40C main OS at `0x40000400`
(`m68k-elf-objdump -D -b binary -m m68k:cfv4e --adjust-vma=0x40000400
out/raw/section_3_MAIN_OS.bin`; the listing used is
`out/_agents/direct-jump/mainos.dis`).

### The sequencer's pattern-change queue (stock)

| what | where |
|---|---|
| [PATTERN] + [TRIG n] while held | `0x40056b30`: first trig of the hold → `0x4009a404` (clear chain), `0x400a1030(bank, n)`; later trigs → `0x4009c634(n)` (chain add) |
| `0x400a1030(bank, pattern)` | pushes `(bank, pattern, start 0, change-at -1, 0)` and calls the setter |
| the setter `0x400a0570(bank, pattern, start, change_at, x)` | transport running (`[0x800065b8] == 1`, `0x400a05aa`) → **`0x400a06d6`**: queued pair `0x800065bf/c0 := bank/pattern`, `0x80006630/38 := start`, `0x80006634 := change_at` (or `start+2` when a length was given), posts the UI notice; stopped → writes the playing pair `0x800065bd/be` directly and tail-calls `0x4009e884` |
| the arranger's row play | the same setter with the row's OFFSET as `start` and LENGTH as `change_at` (`0x400a0eaa`, `0x4004a652`): the mechanism DIRECT rides |
| the tick handler | `0x400a3fdc`: tick `0x800065b6` 0..5; at tick 0 (`0x400a4220`) `0x800065b4 := 0x800065b2`, `0x800065b2 += 1`, then the boundary decision at `0x400a42fa..0x400a439c`: switch when `counter % CHAIN_AFTER == 0` with a change queued (`0x400a4352`, table `0x400d80dc[idx]`), when the absolute change-at `0x8000662c` is reached (`0x400a439a`), or when the counter reaches the pattern's length (`0x400a4388`, `pattern+0x8e53` / MASTER LENGTH `+0x8e50`); the tick-2 pre-check `0x400a413e..0x400a4216` is the same decision one step ahead and only sends the MIDI program change (`0x4009e884`) |
| the boundary apply | `0x400a43a0..`: previous pair → `0x800065c1/c2`, playing pair := queued pair (`0x400a44d0`), **`0x400a44e2`**: `0x80006638/28 := 0x80006630` (start step), `0x8000662c := 0x80006634`; chain advance; the per-track positions from `ticks_per_step × 0x80006628` (`0x400a47f6..0x400a4b7c`), `0x800065b2 := low word of 0x80006628` (`0x400a483a`); UI notices `0x400d8164/67/69/6b` |
| the UI's `CUR_PATTERN` `0x80000004` | written by the sys "pattern applied" case `0x40062108` on the notice, i.e. at the switch — not at the selection (measured) |
| CHAIN AFTER menu | labels `0x400b27d0`, getters `0x400b27dc` (`0x40065a40`), setter `0x400659ec`, value labels `0x400b27e8` (17), title `0x400b5f16` |
| project file | loader `0x400877e0` (`PATTERN_CHANGE_CHAIN_BEHAVIOR`, clamps at `0x4008780c/12`, `1 → 2`), writer `0x40088882` |

### The cave (`direct_jump.s`, layout fixed by `.org`)

| offset | symbol | reached from | displaced (replayed first) |
|---|---|---|---|
| `+0x000` | `dj_queue` | `jsr` planted at **`0x400a06d6`** (`hook_addr`), 12 bytes | `13c4 800065bf` `13c5 800065c0` — `move.b %d4,0x800065bf; move.b %d5,0x800065c0` |
| `+0x100` | `dj_apply` | `jsr` planted at **`0x400a44e2`** by `emit()` + 6 `nop`s, 18 bytes | `2039 80006630` `23c0 80006638` `23c0 80006628` — the three longword moves that latch the start step |

(The 13 Sep form also carried `dj_lens` at `+0x180` and `dj_labels` at
`+0x200`, 18-entry copies of the step and label tables that eight `lea`
operands and the getter were repointed to; gone since 24 Sep 2026.)

`dj_queue` (in the setter, transport running): replay; leave any absolute
change-at (an arranger row) and the arranger (`0x460d1aec`) alone; leave
a selection of the playing pattern alone; compute the playing pattern's
effective CHAIN AFTER; if it is 1 (DIRECT): `0x8000662c := counter + 1` (the tick
handler's absolute change-at — it fires at the next tick 0), `d3 :=
(counter + 1) mod new pattern's length` (the stock code after the hook
stores `d3` to `0x80006630`; INF and PER TRACK MASTER LENGTH handled, a
length ≤ 0 leaves the count unwrapped), arm a flag byte inside the cave,
and note whether the tick-2 program-change pass has already gone by.

`dj_apply` (in the tick handler, at the switch): replay; if armed and this
is the boundary we armed (`counter == 0x8000662c`, which is still our
trigger — the stock latch `0x8000662c := 0x80006634` comes after the
hook), `clr.l 0x80006630` so the pattern's later restarts begin at 0
exactly as stock, and send the MIDI program change (`0x4009e884(bank,
pattern)`, what the tick-2 pass does) if the selection came after tick 2.
A stale flag (STOP between the two) is dropped without acting.

Register discipline: `dj_queue` uses the setter's scratch `d0` and saves
`d1/a0`; `dj_apply` saves `a0` and, around the program-change call,
`d1/a1` (`0x4009e884` saves `d2-d4/a2` itself). ColdFire `movem` has no
`-(sp)` form, so the saves are plain pushes; `remu.l` is written with
distinct `Dr`/`Dq` (the same register would encode `divu.l`).

Pokes (asserted against the stock bytes before every write; all in
`manifest.py`, fixed addresses):

| site | stock | written | what |
|---|---|---|---|
| `0x400d80e0` | `00000001` | `ffffffff` | step table `0x400d80dc` entry 1: DIRECT reads as PAT.LEN in every stock reader — the tick handler (`0x400a4154`, `0x400a4310`), the chain advance, PATTERN SETTINGS' "exceeds the pattern" blink (`0x4006e85c`, `0x40081c60`, `0x40081fb2`, `0x40082726`), the countdown/LED page (`0x400a29c2`, `0x400a3668`) |
| `0x400b27ec` | `400b5771` (`1/16`) | `400b6912` (`DIRECT`) | the SEQUENCER getter's label table `0x400b27e8` entry 1 |
| `0x40065a26` | `6604` (`bne`) | `6004` (`bra`) | the setter `0x400659ec` no longer replaces a result of 1 by `delta + 1` |
| `0x40087826` | `6600` (`bne.w`) | `6000` (`bra.w`) | the loader `0x400877e0` no longer bumps a loaded 1 to 2 |

`PINNED` in the manifest is the ratified cave (`m68k-elf-as -mcpu=5475`,
linked at `0x400d7000` and `0x400d7300`, identical, 358 bytes); the build
links the source at the address it lands on and refuses on any difference.

## Measurements (all `out/_agents/direct-jump/`)

Image: `REMIX=direct-jump make bus` → `out/mainos_bus.bin`, 1,112,560
bytes, **429 bytes changed** vs `out/raw/section_3_MAIN_OS.bin`, cave at
`0x400d6b80` (`build.log`).

⚠️ **For a unit, build it with `make cf`, not `make bus`** (15 Sep 2026).
`make bus` rebuilds the FX2 chooser from the remix's rows and this remix
has none, so its image offers NONE as the only EFFECT 2 effect: the
fourteen stock effects keep their code and dispatch (the report's `KEPT
STOCK`; both DSP payloads are byte-identical to stock) but cannot be
selected. Eleven of the 429 bytes are exactly that — the three `lea` sites
that find the chooser list (`0x400375f6`, `0x40052498`, `0x40059a44`:
`0x400d6090` → a one-row list at `0x400d6b00`), the viewport literal at
`0x40059a57` (7 → 1) and the row itself. `REMIX=direct-jump make cf` →
`out/mainos_cf.bin`, **418 bytes changed**: the same cave at `0x400d6b80`,
the two hooks, nine repoints and six clamps, with the chooser, the FX2 id
and cursor tables and both DSP payloads byte-identical to stock (the build
compares those spans against the stock image before writing; `make
image-cf` packs it). The `tim` image (this module + SCALE QUANTIZER, 1,657
bytes) booted under the panel shows the stock chooser — NONE, FILTER, EQ,
DJ EQ, PHASER, FLANGER, CHORUS, SPATIALIZER, COMB, COMPRESSOR, LOFI, DELAY,
PLATE, SPRING, DARK — and CHAIN AFTER still turns to DIRECT
(`out/_agents/cfbuild/`). Panel: `tools/panel/panel_server.py --image
<remix> --project out/_projects/otlive/OTLIVE/PROJECT --set OTLIVE --name
PROJECT --sound off` on ports 8593–8595, driven through `/key`, `/tap`,
`/knob`, `/peek`, `/screen.png` (`seqwatch.py`, `panelctl.py` in the
scratch dir; logs `*_run*.log`, screens `shots/`). 120 BPM, 16-step
patterns, one step = 6 ticks ≈ 125 ms emulated (paced 1.0× real time).
Columns: `b2` = step counter `0x800065b2`, `b5` = the step byte the LEDs
follow (`0x800065b5`), pattern bytes as named above.

**1. Option off = stock.** PLAY, [PATTERN] + [TRIG 3] during step 11
(`b5 = 10`), watched every 50 ms:

| image | selection seen | switch (`0x800065be`, `CUR_PATTERN`) |
|---|---|---|
| stock (`stock_select_run1.log`) | t=1.359 b2=11 b5=10 queued 00/02 | t=1.919 b2=0 b5=15 → 00/02, CUR_PATTERN 00→02 |
| remix, CHAIN AFTER = PAT.LEN (`remix_off_run1.log`) | t=1.369 b2=11 b5=10 queued 00/02 | t=1.907 b2=0 b5=15 → 00/02, CUR_PATTERN 00→02 |

Both switch at the pattern end (the counter wraps 16 → 0, the new pattern
starts at step 0); `0x80006630/34/2c` stay `0 / -1 / -1` throughout in
both. `CUR_PATTERN` (`0x80000004`) changes at the switch in stock too — it
is written by the sys notice case, not by the selection.

**2. Option on.** CHAIN AFTER turned to DIRECT with the LEVEL knob
(`0x8000004e`: 0 → 2 → 3 … → 16 → **17**, a further detent stays 17;
screens `shots/m5_sequencer_window.png` PAT.LEN, `m7` 256/16, **`m8`
DIRECT**), USE PRJ SET. checked on the playing pattern A04
(`shots/p5_use_prj_checked.png`, `pattern+0x8e56` 00 → ff). PLAY on A04,
[PATTERN] + [TRIG 1] during step 11 (`remix_on_run3.log`):

```
t=1.275  b2=11 b5=10 tick=0  playing 00/03  queued 00/03   662c=-1  6630=0
t=1.323  b2=11 b5=10 tick=3  playing 00/03  queued 00/00   662c=12  6630=12  6634=-1   <- selection, armed
t=1.377  b2=12 b5=11 tick=0  playing 00/00  queued 00/00   662c=-1  6630=0   6628=12   <- next step: switched
t=1.425  b2=12 b5=11 ... 13/12, 14/13, 15/14 ...
t=1.883  b2=0  b5=15                                                                  <- the new pattern's end: restart at 0, as stock
```

`CUR_PATTERN` 03 → 00 at t=1.377, one step (54 ms) after the selection;
the step byte continued 10, 11, 12, 13, 14, 15 across the switch; the
start step latched as 12 and was cleared by `dj_apply`; the following
restart began at 0. The same selection on a pattern whose own CHAIN
BEHAVIOR is PLEN (`remix_on_run2.log`, A03 → A04 before USE PRJ SET. was
checked) switched at the pattern end — the precedence rule, unchanged.

**3. Persistence.** SAVE PROJECT on the unit (PROJECT > SAVE, YES, YES;
`shots/s15_save_confirm.png`) on the persistent card
(`--card out/_agents/direct-jump/card.img`): 22,752 sectors written, the
card's project file reads `PATTERN_CHANGE_CHAIN_BEHAVIOR=17`. The server
was killed and the card cold-booted on port 8595 with `--card` alone:
the reloaded project reads `0x8000004e = 0x11` (17 = DIRECT, mirror
`0x100b14ae = 0x11`), pattern A04's byte `ff`, `CUR_PATTERN 00` (the saved
A01); the SEQUENCER window shows **CHAIN AFTER DIRECT**
(`shots/r2_sequencer_after_reboot.png`); and the jump repeats on the
reloaded state (`remix_on_after_reboot.log`: A04 selected while stopped,
PLAY, [PATTERN] + [TRIG 1] during step 11 at t=1.354 → switched at t=1.407,
`b5` 10 → 11 → 12 …, `0x8000662c`/`0x80006630` 12 then cleared).

**4. Boot A/B.** `tools/emu/ot_emu/oracle/drive.py --emu out/emu/ot_emu
--image <stock | remix>` (the oracle's `inter` battery: boot on the OTLIVE
card, YES, MIXER, NO, T1 double tap, DOWN, RIGHT, NO, NO, PLAY, 20 × 100 ms,
STOP, 5 × 100 ms; `ab/stock`, `ab/remix`): `ready.txt`, `steps.txt`
(170 replies), `stamps.txt`, **`tx.bin` (18,297 UART bytes)**, `txlen.txt`
and `peeks.txt` (STEP/TICK/TRANSPORT, clock record, UI window, popup,
current track, page kind, PART_PTR, CUR_PATTERN, gain table after every
step) are byte-identical; `boot.log` differs only in the image path line.
The battery never opens the SEQUENCER menu, so no text differs either.

**5. `make check REMIX=direct-jump`** (`make_check.log`): the remix builds
and passes every selftest check that names it (`direct-jump: system,
tracks none`, `remix 'direct-jump' is clean`, `every stock id is stock's,
or declared`), then the selftest fails on **eight remixes that carry MIDI
SCENES** because `modules/midi-scenes/upstream/gas/msc.s` is missing —
the submodule is not checked out in this tree (pre-existing, unrelated;
`make check` stops there). Every remaining gate was run by hand for
`REMIX=direct-jump` (`verify_steps.log`): `make bus`, `cycle_count`,
`verify_slots`, `label_fmt`, `verify_octakit`, `verify_midiscenes`
(SKIP: submodule), `verify_dram_boot`, `verify_labels`,
`verify_menushortcut`, `verify_cfprobe`, `verify_busscreen`,
`verify_ccpage2`, `verify_hidden`, `verify_grains`, `verify_menu`,
`verify_burn` (its usual SKIP), `verify_twocore`, `verify_onebus` all exit
0; `verify_replaces` passes every direct-jump line and fails only on the
same eight MIDI SCENES remixes; `verify_modenames` reports "no module
declares mode_views" (the Makefile's SKIP).

## 24 Sep 2026: index 1 (the build this README now describes)

`REMIX=tim make cf` → `out/mainos_cf.bin`, **4,301 bytes changed** vs
stock (this module: the 358-byte cave at `0x400d6b80`, the two hooks, the
four pokes), the panel on 8593 with a copy of the OTLIVE card and
`--sound on`; scripts, logs and screens in the session scratchpad
(`glide_menu.py` → `gm/`, `dj_test5/6.py` → `dj5/`, `dj6/`,
`persist_test3.py` → `pt3/`, `bc/`). One step = 6 ticks = 125 ms at
120 BPM; `b2` = the step counter `0x800065b2`, `b5` = the LED step byte
`0x800065b5`, `cur` = `CUR_PATTERN 0x80000004`.

**1. The menu.** PROJECT > CONTROL > SEQUENCER, CHAIN AFTER: LEVEL +1 from
PAT.LEN → **DIRECT** (`0x8000004e` 00 → 01, mirror `0x100b14ae` 01), +1 →
2/16, −1 → DIRECT, −1 → PAT.LEN, −1 → PAT.LEN (clamped), +20 → 256/16
(0x10, clamped), −16 → PAT.LEN; [YES] from DIRECT → 2/16 → 3/16 → 4/16
(the wrap path steps by one, index 1 included). The window reads
`CHAIN AFTER DIRECT` (`gm/m2_direct.png` is the clamped 256/16 frame,
`pt3/m_chain_1_and_glide33.png` DIRECT).

**2. The jump** (`dj6/`). PATTERN SETTINGS ([FUNC]+[BANK], [RIGHT] into
the rows, [DOWN] ×3, [YES]: `USE PRJ SET.` ☒, pattern A01's `+0x8e56` 00 →
ff), CHAIN AFTER = DIRECT, PLAY on A01, [PATTERN] + [TRIG 2] during step
~11: the next sample after the chord already reads `playing = 00/01`,
`cur = 01`, **`0x80006628 = 12`** (the latched start step) with `b2 = 13,
14, 15`, `b5 = 12, 13, 14` and the trig LEDs walking 13 → 14 → 15 — the
new pattern took over at the next step at the old pattern's count and
did not restart; it then wrapped at its own length 16 (`b2 = 0`, `6628 =
0`) as stock. A second selection late in the bar switched at the wrap
(`b2 = 1, 2, 3`, LEDs 1-2, 3-4, 5-6 on A02). The same script with
CHAIN AFTER = PAT.LEN (`dj5/dj_patlen_trig2.log`): queued `00/01` at the
selection, the switch at the pattern end (`b2 = 0`, `cur = 01` at
t = 1.88 s, LEDs restarting at 1), stock. With 6/16 and 3/16 the switch
came at the next multiple (both had switched by the first sample after
the chord), stock values unchanged.

**3. Persistence and migration** (`pt3/`, `bc/`). SYNC TO CARD with
DIRECT: the card's `project.work` reads `PATTERN_CHANGE_CHAIN_BEHAVIOR=1`;
`/card/insert` (a power cycle) reloads `0x8000004e = 01`. Editing the
mounted card and re-inserting: `=17` (a project saved by the 13 Sep
build) → `0x10` = **256/16** on the window; `=1` → **DIRECT**; `=5` →
**6/16** (stock). The mirror followed each time.

**4. The synth's PLAY, the page and the quantizer** on the same image are
in `modules/synth/README.md` and `modules/quantizer/README.md`.

## What does not work, and what is left

- **A pattern with its own CHAIN BEHAVIOR does not jump** — by the
  firmware's precedence. Its setter (`0x40081d74`) skips index 1 exactly
  as the project setter did; making DIRECT selectable per pattern is now
  one branch poke (`0x40081dde`) plus the PLEN label table's entry 1
  (`0x400b2fb2`), since the shared step table already reads `-1` there and
  `dj_queue` tests the effective index; not done.
- **Chains** ([PATTERN] + several [TRIG]s) still change at the pattern
  end: their queue path (`0x4009c634`) bypasses the setter, and the step
  table's `-1` makes DIRECT read as PAT.LEN for them.
- **The MIDI program change** for a direct jump goes out at the switch
  when the selection landed after tick 2 of its step (stock sends it two
  ticks early); for a selection at tick 0 or 1 the stock pass sends it.
  Not measured (no MIDI out capture in the panel).
- **Stock firmware loads** a project saved with DIRECT (`=1`) as 2/16 (its
  own 1 → 2 bump); a project saved by the 13 Sep build (`=17`) loads here
  as 256/16 (the migration case, measured below).
- **The 18th-index form was flashed** (OCTATRICK1..3, 23-24 Sep 2026); the
  index-1 form is emulator-verified only. The arranger paths, BANK + TRIG
  across banks, PER TRACK patterns with INF master length and MIDI
  program-change selections are reasoned from the disassembly, not
  measured.

Background: `docs/firmware/RTOS_FORK.md` §8.3 (the sequencer's bank/pattern
bytes), `CONTEXT.md` (the RAM facts and the panel), `tools/panel/KEYMAP.md`.
