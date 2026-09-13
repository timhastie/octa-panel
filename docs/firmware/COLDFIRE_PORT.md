# The ColdFire port (`tools/emu/ot_emu`) — a headless Octatrack in C++

> **What this is for.** `tools/emu/emu_rtos.py` (route A) runs the firmware's own
> scheduler and is the **oracle** every claim here is measured against — but it
> costs about 120× real time, models no audio, and stops at the DSP host port.
> `tools/harness/dsp_host` runs both DSP cores at roughly real time and knows nothing
> of the ColdFire. This is the join: one process, both halves, fast enough to
> drive interactively. Deliberately **not** a plugin — no JUCE, no UI, no audio
> device. A library and a CLI you can script and intercept.

Confidence markers as in `CHIP.md`: ✅ measured, 🟡 inferred, ❌ retracted.

## Where it came from

Sam found `joelanders/gearmulator-md-mm` (7 Sep 2026), a full-system
Machinedrum / Monomachine emulator that runs its firmware in **real time** as a
plugin: a Musashi-based ColdFire, two DSP56303s over HI08, one deterministic
interleave scheduler paced by the mixer DSP's frame counter. It proves the
architecture on the cousin machine. What it does **not** give us is the
Octatrack's CPU: its Musashi is ColdFire **V2** (`MCF5206E`, ISA_A), with no
EMAC, none of the V4e instructions this firmware uses everywhere, and none of
the MCF5445x peripherals.

So we vendor the small piece that helps — `vendor/mc68k`, its submodule: a
standalone static library with Musashi, ColdFire mode, an HI08 host-port
register file and peripheral scaffolding, no JUCE anywhere — and write the rest
against route A as the specification. Licences: mc68k is GPLv3 and Musashi is
Karl Stenerud's; the same posture as `vendor/dsp56300`, which this repo has
vendored all along. **Tooling and patches are shared; built binaries are not.**

## Milestone O1 — boot to the RTOS handoff ✅ (7 Sep 2026)

```sh
make emu-cf                    # or: cmake -B out/emu -S tools/emu/ot_emu && cmake --build out/emu -j8
./out/emu/ot_emu --image out/raw/section_3_MAIN_OS.bin --profile --periph
```

**It reaches `trap #0`, and it agrees with the oracle:**

| | route A (`emu_bringup.boot`) | `ot_emu` |
|---|---|---|
| handoff PC | `0x40000e46` | `0x40000e46` ✅ |
| auto-poke 1 | loop `0x4000f9e2`, wrote `0xffff` to `0x0` | identical ✅ |
| auto-poke 2 | loop `0x4000fa02`, wrote `0xffff` to `0x2000` | identical ✅ |
| instructions | 10,000,000 (counted in 500k bursts) | 10,170,953 exactly |

**Speed, measured on this machine: 39.1 M instructions/s**, boot to handoff in
**0.26 s** — about **26× route A's ~1.5 M/s**. For scale, a 16-sample DSP frame
is ≈63,800 ColdFire instructions, so this is ≈613 frames/s against the 2,756
real time needs: **≈4.5× slower than real time today**, before the one obvious
optimisation below. Real time is genuinely in reach, which was the open
question when the port was scoped.

## Milestone O2 — the EMAC, and its gate ✅ (7 Sep 2026)

**The gate was written FIRST and watched to fail**, which is the whole
discipline: `tools/emu/ot_emu/test_emac.cpp` encodes hardware's semantics from
`emu_bringup.emac_selftest` (fractional `macl 0xc00 x 0x200000` -> 3, the
negative operand -> -3, `msacl` SUBTRACTS -> -3) and reported all three FAIL
against an emulator with no EMAC before a line of it existed. It passes now.
`ctest` in the build dir runs it beside the vendored core's own ColdFire
timing, divide and HI08 tests: **4 tests, all passing.**

What the implementation had to get right, each of which is a defect Unicorn
shipped (`RTOS_FORK.md` §10.16):

- **Fractional mode is a signed product shifted LEFT ONE, upper 40 bits
  accumulated** — so `movclrl` yields `(a*b) >> 31`, not `>> 32`. Off by that
  one bit is what wrote 10,336 for Bryan's 20,672 and got explained away as
  "2-sample units" for a day.
- **MAC vs MSAC comes from bit 8 of the EXTENSION word**, never the opcode
  word. Reading it from the opcode is what made every `msac` add.
- The EMAC's whole state (four accumulators, their extension bytes, MACSR)
  lives in this layer, because Musashi's ColdFire state has none.

✅ **And one structural finding, measured the moment the gate ran:** every EMAC
opcode is `0xAxxx`, i.e. **A-line**, and Musashi routes A-line to
`m68ki_exception_1010` — a *different* path from the illegal-instruction
callback, with no callback of its own. So the V4e layer never saw them: the
test failed with D0 still holding the value the program's first instruction
loaded. The EMAC is dispatched from `Machine::run`'s own loop instead, reusing
the opcode fetch already made for the handoff check, which leaves the vendored
Musashi unpatched. `mov3q` (`a340`) rides the same path.

⚠️ Encodings for all of it came out of `m68k-elf-as -mcpu=5475`, listed in the
source beside each handler. ❌ **Retracted from O1's work list:** `byterev` and
`ff1` are **not** V4e — the assembler refuses them for `-mcpu=5475` and names
the parts that do have them (ISA_C). Nothing needs them.

### What had to be built, and what each cost

**1. The V4e instructions, as trap-and-emulate (`v4e.cpp`).** Musashi's opcode
tables are GENERATED, and ✅ the generator checked into the tree is **not** the
one that produced the checked-in tables: regenerating rewrites every handler
signature (the upstream fork threads a `m68ki_cpu_core*` through them, the
shipped generator does not), an 18,984-line diff. So the tables are frozen.
Instead, `m68ki_exception_illegal` calls the illegal-instruction callback
**first** and takes no exception if it returns nonzero, which makes that
callback a legal extension point: decode, execute, advance the PC over the
extension words, return 1.

Implemented so far: **MVS/MVZ** (`0111 rrr 1 oo eeeeee`, `oo` = MVS.B / MVS.W /
MVZ.B / MVZ.W) with a full effective-address reader. ✅ The encoding was pinned
against `m68k-elf-objdump -m m68k:cfv4e` on the real image, never against a
reading of the manual — the boot's own first two are
`4000043e: 73c1 mvzw %d1,%d1` and `40000440: 71c0 mvzw %d0,%d0`. That covers
**961,786 of the boot's 10.2M instructions**, and the boot needs nothing else.

⚠️ **Still to come**: `mov3q`, `byterev`, `ff1`, and the whole **EMAC**. The
EMAC is the one to be careful with — `RTOS_FORK.md` §10.16 is a week lost to
three defects in *Unicorn's* EMAC, each producing a confident wrong finding for
a day. `emu_bringup.emac_selftest` already encodes the measured contract
(`macl` fractional `0xc00 × 0x200000` → 3, negative → −3, `msacl` → −3) and
should be ported as a CTest **before** any EMAC handler is trusted.

⚠️ **And watch the cost model**: an exception round trip per instruction is
fine for moves scattered through the code, and may not be for the frame
builder, which runs EMAC in bulk (~7,400 instructions per frame). If it bites,
these handlers become the reference an opcode-table implementation is diffed
against.

**2. Byte-addressable peripheral overrides.** Musashi composes a 32-bit
peripheral read from two 16-bit reads, so an override stored whole and returned
per access is truncated to the access width. The PLL register read back
`0x0000ffff` instead of `0x16000000` and the firmware spun forever in its clock
check at `0x4000f9e8`. Overrides are per byte now.

**3. The stall detector and auto-poke, ported from route A field for field.**
A PC confined to a 64-byte window across four 500k bursts, with fewer than
2,000 stores in between, is a poll rather than a memset; `tryAutoPoke` then
looks for `move.w (abs),d0 … cmpi #imm,d0` around it and writes the immediate
to the flag **at the load width** — two bytes, not the compare's width, or the
low word reads back wrong. Both flags it finds match route A's exactly. These
are the places the emulator stands in for hardware nobody has modelled yet, and
the CLI prints them every run rather than hiding them.

## Milestone O3 — the first peripherals, gated ✅ (7 Sep 2026)

`periph.{h,cpp}`: the **PIT** and the **INTC**, translated from route A rule
for rule, with each rule's measurement or failure carried across rather than
summarised. `tools/emu/ot_emu/test_periph.cpp` checks fourteen of them; `ctest`
now runs **5 tests, all passing** (emac, periph, and the vendored core's
timing, divide and HI08 tests).

The rules worth naming, because none is obvious and each was a silent failure
in route A first:

- **A FORCED interrupt ignores the mask** (MCF54455RM rev 5 §17.2.3). The
  sequencer tick is source 32, installed with ICR 3 and never unmasked
  anywhere in the image; masking it left route A running 400 frames with
  **zero ticks**. ✅ measured there, gated here.
- **CIMR clears MASKALL along with its source.** 🟡 inferred, and the reason
  is carried too: nothing in the image ever writes IMRH/IMRL (a literal scan
  found no site), the firmware unmasks only through CIMR, and the unit
  plainly takes interrupts.
- **A source with ICR 0 is never delivered**, whatever else is true.
- **PIT PIF is write-1-to-clear.** Treat the write as a set and the ISR
  re-enters forever.
- **The PIT prescaler input is a KNOB, not a fact** (264 MHz by default; off
  the 132 MHz bus clock every period is 2× longer). What pins it is the
  sequencer's own tick count, which is M6c's gate.

## Milestone O4 — the run loop: the kernel runs ✅ (8 Sep 2026)

`rtos.{h,cpp}`: the sample clock, the peripheral models installed and seeded,
interrupt delivery, and the loop. **The firmware's own scheduler runs**: ten
tasks created with the exact fields route A measured, eleven TCBs dispatched,
first switch boot → main, the M6a gate reached at **204.88 ms** against route
A's 204.95. `ctest` is **6 tests, all passing** (the new `rtos` one is route
A's M6a gate, self-contained).

**The oracle diff passes** (`tools/emu/ot_emu/oracle.py`).

> ❌ **RETRACTED 8 Sep 2026: "8 of 8 fields".** The diff's summary counted
> every field in the golden, including three it has never compared —
> `gate_ms`, `pit0_fired` and (later) `serial_sent`, all of which track the
> `ips` knob. O4 actually agreed on **6 compared fields**: `handoff_pc`,
> `auto_pokes`, `created`, `ran`, `first_switch`, `dispatches`. The claim was
> inflated by the script, not by the port — nothing about O4's result changes,
> only what it is honest to say about it. `oracle.py` now counts only what it
> compared and prints a `REPORTED` line for the rest. Same defect as a watch
> that prints nothing: a gate that takes credit for fields it did not check
> (RTOS_FORK §10.3b).

### The one deliberate divergence from route A

Route A hand-rolls exception entry and exit because Unicorn's CFV4E will not
dispatch them — its VBR is a no-op and its `rte` never arrives. ✅ The vendored
Musashi does both: `m68ki_stack_frame_0000` carries the ColdFire 2-longword
frame (format `4 | A7[1:0]`, vector, SR, PC — MCF5206e UM 3.4) and
`m68ki_jump_vector` reads REG_VBR, which the firmware sets itself with a
`movec %a0,%vbr` at 0x40000db6 (checked: it reads 0x40000000 after a boot). So
this port lets the CPU take its own exceptions — the hardware mechanism rather
than a model of it — and the oracle is what proves the two agree.

### What it cost, each measured

- **32-bit accesses must arrive whole.** Musashi composes a longword from two
  16-bit halves unless the machine provides `read32`/`write32`, and a
  peripheral register is not two halves: the DSPI status word came back as
  `0x0000ffff`, so the firmware's `(SR >> 4) & 15 == 2` wait at 0x4001c504
  could never match and main parked there forever with **no task ever
  created**. The same class as the PLL truncation that stalled the boot in O1.
- **DSPI and the UARTs moved from O5 into O4**, because the gate cannot be
  reached without them — that wait above is on main's path to its init list.
  O5 shrinks accordingly.
- **A queued interrupt is not a level-sensitive line.** The core holds an
  injected vector until it is acknowledged, so a source that asserts and then
  deasserts before the CPU can take it — the PIT's PIF, which the scheduler
  clears at 0x40000588 while running at mask 7 — was still delivered
  afterwards, firing the handler again for an expiry that no longer existed.
  It showed as **twice the oracle's dispatches**, every other one resuming at
  the scheduler's own entry (0x40000550), because the stale interrupt landed
  in the one-instruction window before `movew #0x2700,%sr` raises the mask.
  Fixed by withdrawing a line that has gone away (`removePendingInterrupt`).
- ❌ **O2's retraction is itself retracted: `byterev` and `ff1` ARE used.** The
  assembler refuses them for `-mcpu=5475` and objdump prints `.short 0x04c2`
  rather than decoding it — but the firmware contains one at 0x4004098e, in
  the task-creation path, and the run loop stopped there. *A toolchain that
  will not assemble an opcode is not evidence the part lacks it; the image
  is.* Route A had already met this and written `_isa_c_shim`; its semantics
  (ff1 counts leading zeros and sets N and Z from the **source**) are what the
  port implements.

### What the gate can and cannot assert — measured, not assumed

The first version of the gate compared the resumed PC of every dispatch and
the whole dispatch sequence. ✅ **Both are functions of the `ips` knob**, which
route A itself calls a guess (`RTOS_FORK.md` §6). Swept on the same image:

| | pc[1] | pc[2] | dispatches | tail |
|---|---|---|---|---|
| ips 3990 | `0x4001fab6` | `0x400209ac` | 51 | …storage, **sys**, keyrepeat, ui |
| ips 3995 | `0x4001faae` | `0x400209a8` | 51 | …storage, **sys**, keyrepeat, ui |
| ips 4100 | `0x4001faae` | `0x4009acf0` | 49 | …storage, keyrepeat, ui |
| route A | `0x4001fab6` | `0x400209a4` | 50 | …storage, keyrepeat, ui |

Where a *preempted* task resumes, and whether one extra timer preemption slips
between two switches, both move with the clock. What does **not** move is the
order in which each task first runs — ✅ identical in the oracle and at both
clock settings:

```
main, voice, p2a, p2b, p2c, p1b, engine, sys, storage, keyrepeat, ui
```

which is route A's own documented cascade (strict priority after the first
tick). So that is the strict criterion, along with the created fields, the set
that ran, the first switch and each task's first-run time within one PIT
period; the resumed PCs and the preemption count are **reported as notes**.
The gate is still sharp: at ips 4100 it fails on first-run times.

## Milestone O5 — the rest of the memory, and what the oracle really does ✅ (8 Sep 2026)

O5 was written as "the remaining peripherals" and its list was nearly empty:
the UARTs and the DSPI had already moved into O4, leaving two memory spans
route A maps in `install` and a serial byte count to compare. Both were done
in minutes, **and the gate passed on the first run** — which, by the standing
rule that a gate which has never failed proves nothing, is where the milestone
actually started.

### The two maps, translated

`Rtos::install` now adds route A's own two spans, with its reasons:
`0x00010000+0x1f0000` so the test-mode magic word at `0x1ffffe` reads **zero**
(`0x4003232c` takes `0xdcba` as a test-mode flash), and `0x10100000+0x1000`
for the settings reset's off-by-four past the SRAM window. ✅ Both are visible
in the port's own access log: exactly **one read at `0x1ffffe`** (pc
`0x4003233e`) and exactly **four byte-writes at `0x10100000`**, which is the
off-by-four reproduced rather than assumed. Neither changed the gate.

> ❌ **RETRACTED 8 Sep 2026 (O7): the MECHANISM below is wrong.** The four
> spans are real and the measurement stands, but route A does **not** grow
> them through `_prime_menu`'s auto-mapping hook. That hook is installed only
> by the menu RENDER helpers (`menu_children`, `render_menu`, `render_fx2`,
> `render_playback`, `render_fx1`), **none of which run on the golden path** —
> so it is never installed there, and route A really does fault on unmapped
> memory. What actually maps the four spans is `emu_card.attach`, explicitly,
> with its own comment naming what lives in them (the PCM pool, the sector
> buffers, the delay rings, the on-chip SRAM around the boot's window). The
> two O5 added in `install` are route A's too. So the golden run has them
> because it has a **card**, which is also why route A faults at
> `0x100fff04` without one: that address is inside the card's own
> `0x100c0000+0x40000` map.
>
> **Why the difference matters, and it is not pedantic:** an explicit map has
> KNOWN BOUNDS. Route A faults on a wild pointer outside them; this port's
> auto-map absorbs it silently. O7 adds the four spans explicitly
> (`Rtos::mapCardMemory`), and with a card attached the auto-mapped count
> falls from **20,348,051 to 4** — the residual three addresses
> (`0x04020000`, `0x100a0000`, `0xffff0000`) are boot-time touches outside
> every map route A has, and so are places the two emulators still differ
> silently. That is the honest work list the auto-map was hiding.

### ⚠️ The finding: route A does not fault on unmapped memory, and this port was not the same machine

The port answers **all-ones** for an address in no region and drops the write;
Unicorn raises `UC_ERR_READ_UNMAPPED`. That difference was assumed to be
harmless because route A "always faults". It does not. `_prime_menu`
(`emu_bringup`) installs an unmapped-access hook that **maps a zero page and
returns True** — a workaround for a stale formatter pointer in the menu
render — and it stays installed for the rest of the run. So in the golden
configuration route A *grows*:

| | measured |
|---|---|
| regions after the boot | 9 |
| regions at the M6a gate | 15 |
| grown by `install`'s two explicit maps | `0x10000-0x1fffff`, `0x10100000-0x10100fff` |
| grown by the auto-map hook | `0x10000000-0x100affff`, `0x100c0000-0x100fffff`, `0x42000000-0x45ffffff`, `0x48100000-0x4fffffff` |

Those four are, page for page, the four spans this port was answering all-ones
for — **20,348,069 accesses** (14,073 read, 20,333,996 written). Their two
largest sources are both plain literal loops, disassembled rather than
inferred:

- `0x400209a4` is a `moveml`-based `bzero`, called on **64 MB at
  `0x42000000`** — precisely the gap between route A's two SDRAM regions.
- `0x40002fb4` is `lea 0x4f502c10,%a0` / `movel #705664,%d0` and a 16-byte
  clear loop: **10.8 MB**, both bounds hard-coded in the image.

✅ Without a project route A really does fault (`unmapped read 0x100fff04 at
pc 0x4001fa4e`), which is the behaviour its own note in the work order
describes — but the oracle is the golden configuration, and there it grows. So
the port now grows too, at route A's granularity (`addr & ~0xfff`, 4 KB): a
first touch allocates a **zeroed** page and the access proceeds. `19,385`
pages, 77 MB, on the run to the gate. `setAutoMap(false)` restores the
all-ones stub, and then the counter is a work list again.

### ⚠️ The serial byte count was an artefact of the missing memory

The count agreed at **4831** before the auto-map change and disagreed
(**5731** against route A's 4831) after it. The temptation is to read that as
the change breaking something. It is the opposite: the port's transmit ring
lived in memory that was being dropped, so the bytes were never counted.

The streams themselves are **identical**: route A's 4831 bytes are an exact
prefix of the port's 5731, and at ips 4100 the port's stream is byte-for-byte
route A's whole stream. The difference is one ~900-byte ring drain landing
either side of the gate, and it tracks the clock knob — the same shape as O4's
resumed PCs:

| ips | 3900 | 3990 | 4100 | 4200 | 4300 |
|---|---|---|---|---|---|
| bytes sent | 5731 | 5731 | 4831 | 4831 | 4831 |

❌ **Retracted the same day it was written:** an earlier sweep in this session
found 4831 at every `ips` and concluded the count was clock-independent. That
sweep was run on the all-ones machine, where the ring writes were being
discarded — it measured the absence of the memory, not a property of the
firmware. **A knob sweep on an instrument that cannot see the thing is not
evidence**, which is the `send_probe` THD lesson in a new costume.

So the gate compares the **bytes over the length both runs reached** — one
must be a prefix of the other — and reports the totals. That is 4831 bytes of
content instead of one integer, and it is clock-independent by construction.
`test_rtos` checks the same thing self-contained, as an FNV-1a over the first
4831 bytes (`0x208868fc`).

### The negative control

`test_rtos` boots a second machine with `Rtos::Quirks::clearTransmitInterrupt`
false — the one thing in `install` that changes which serial writes happen —
and **requires the count to move**. It goes 4831 → 0. Without that, the serial
comparison would be decoration.

**Gate:** `ctest` 6/6; the oracle diff reports **8 compared fields agree, 0
disagreements**; `make check` green.

## Milestone O6 — the eDMA and the frame clock ✅ (8 Sep 2026)

**The sequencer runs, and its trig is byte-identical to route A's.** With the
project loaded, the transport started through the real tasks and a trig poked
on track 1 step 2, 400 frames of the port produce exactly route A's log:

| | route A (the oracle) | `ot_emu` |
|---|---|---|
| frames since the transport start | 400 | 400 ✅ |
| sequencer ticks (vector `0x60`) | 28 | 28 ✅ |
| transport-start writes at frame 0 | `0x10` on tracks 0, 1, 2, 4, 7 | the same five, same order ✅ |
| the trig | frame **344**, track 0, bytes `0x08` then `0x18` | identical ✅ |
| saved / final / sequencer bank / pattern | 1 / 1 / 1 / 0 | identical ✅ |
| eDMA transfers started | 16,801 | 16,800 (reported, not compared) |

```sh
scripts/o6_gate.sh                     # stages ONE card image, runs both, diffs
python3 tools/emu/ot_emu/oracle.py out/oracle/m6c.json out/oracle/port_m6c.json
# oracle: 5 compared field(s) agree
```

❌ **RETRACTED: `RTOS_FORK.md` §8.4's "byte `0xd3`" and its six
transport-start writes on tracks 0, 1, 1, 2, 4, 7.** Re-measured today, route
A **and** the cold tool (`emu_frames.py`, the same command §8.4 quotes) both
give **five** writes of `0x10` at frame 0 (tracks 0, 1, 2, 4, 7) and `0x08`
then `0x18` at frame 344. Two independent instruments agree, so the current
numbers are the reference. 🟡 The likely cause is the EMAC fix of 7 Sep
(§10.16): §8.4 was measured on 6 Sep, before it, and the byte the trig writes
is computed by an EMAC chain (below). Inferred, not measured — nobody has
re-run §8.4's exact tree on the stock library to confirm.

### The gate, and the shape of it

`--sequencer` on both tools walks the same steps, and every one of them is
route A's, including the two compensations it documents as compensations (the
bank switch and the sequencer re-select, which stand in for a load-ordering
defect the unit does not have — RTOS_FORK.md §7/§8.3): park at main's spin,
mount, load, switch to the file's bank through `sys`, re-issue the load's own
last step, clear CLOCK RECEIVE, **then** turn the frame clock on, start the
transport, poke the trig, and run 400 frames. `tools/emu/ot_emu/stage_card.py`
builds the card image with route A's own `stage_project`, so both emulators
read byte-identical media — the FAT16 builder is still deliberately not
ported (O7).

`oracle.py` compares `m6c_trig`, `m6c_trig_words`, `m6c_ticks`, `m6c_frames`
and `m6c_bank` **strictly**. None of them tracks the `ips` knob the way the
dispatch PCs and the serial count do: a trig either fires on the frame the
other emulator fires it on or it does not.

### Five defects, and none of them was in the frame model

The eDMA and the frame latch were written in the previous session and gated
by `test_periph`'s 19 assertions; that model needed no change. What stood
between it and the gate was five other things, each measured:

**1. ✅ The DSP host port needs route A's two stand-in replies, and without
them the frame handler never returns.** `0x20000004` must read `0x0000`: the
handler writes 140 there at `0x4000ab1e` and then polls
`movew 0x20000004,%d0 / tstb %d0 / blts` — it waits for bit 7 of the low byte
to clear, which is the DSP's handshake. An unmodelled window answers all-ones
and the bit never clears. The port took **exactly one** frame interrupt,
entered `0x4000aad0`, and burned **352 M instructions** in that
three-instruction loop: 0 eDMA transfers, 0 ticks, 0 trigs. It reads as "the
frame model is wrong" and it is a missing peripheral reply. `0x2000001c` (the
ping index, read one instruction earlier) toggles 0/1. Both are route A's
`EXTRA_OVERRIDES`, "the DSP host port as M5 faked it", and both are stand-ins
for the DSP that O8 will put behind the window.

**2. ✅ `Region::contains` overflowed, and a legitimate unmapped write became
a 4 GB memory smash.** `(_a - base) + _size <= data.size()` in 32-bit
arithmetic: with the region at `0x00000000`, an access at `0xffffffff` gives
offset `0xffffffff`, and `0xffffffff + 1 == 0`, so the region claimed the
address. The frame handler reaches a `moveb %d0,%a0@-` with `a0 = 0` and the
emulator died inside Musashi with a bare SIGSEGV — **and printed nothing**,
because stdout redirected to a file is block-buffered. Three runs went into
that. Fixed, and with it three instruments that make the next one legible:

- `Machine::badWrite` stops the MACHINE, not the process, on a write the
  region model cannot honour, naming the address and the PC;
- `setAutoMapLimit` (default 65,536 pages = 256 MB) does the same for a
  runaway auto-map, naming the busiest unmapped-access PC — route A's own
  growth on the golden path is ~200 MB, so the ceiling is well clear of
  anything faithful;
- `main` sets **line-buffered stdout**. Same family as the panic printer O7
  could not finish.

**3. ✅ `SATS` (`0x4c80 | Dn`) was unimplemented**, and it is on the M6c path
only — the per-frame EMAC routine at `0x4000346a`, which the eDMA completion
handler's state 5 calls. The boot and the whole project load never meet it.
Semantics measured in route A's engine (six cases in `test_emac`): with V set,
a result whose sign bit is clear saturates to `0x80000000` and one whose sign
bit is set to `0x7fffffff`; with V clear the value is untouched.
⚠️ **Its flags disagree with the CFPRM and the port follows route A**: the
manual says N and Z are set from the result and V and C cleared, and route A's
engine updates **N only**. Nothing between the `sats` and the next
flag-setting instruction reads the CCR at the only site the firmware reaches.
What would falsify it: a firmware site that branches on Z or V after a SATS.

**4. ✅ `movel #imm,%macsr` (`a93c`) was unimplemented** — the frame handler's
own, at `0x4000aeda`. It fell into the MAC path, which consumed one extension
word instead of the immediate's two, and the instruction stream desynchronised
inside the handler. (That is what produced defect 2's write to `0xffffffff`.)

**5. ✅ THE EMAC HAD FIVE FIELDS WRONG, AND THE OLD GATE COVERED NONE OF THEM.**
Every case the O2 gate tested used acc0, two data registers, the long form and
no parallel load — the one combination in which all five are invisible.

- The **accumulator number** was read as ext bit 4 | ext bit 9. It is opcode
  **bit 7** (low) and ext **bit 4** (high) — and in the load form the low bit
  is **inverted** (`a498` is acc0, `a418` is acc1; route A's
  `_emac_load_shim` carries the same `((~op >> 7) & 1)`). The frame builder
  uses all four accumulators.
- An **address-register source** was read as the data register of the same
  number: `macl %a2,%d0` (`a08a`) multiplied d2.
- The two **operand-half bits** were applied to the wrong operands
  (`macw %d0u,%d1l` is ext `0x0040`: bit 6 is the FIRST operand's).
- The **parallel load** handled only `(An)+`. The frame routine at
  `0x40003738` uses `(An)` and `(d16,An)` as well, and the `(d16,An)` form is
  **six bytes** — skipping its displacement word desynchronised the stream
  and invented an opcode two instructions later.
- ⚠️ **And the one that survived all of those: the accumulator is 48 bits and
  holds the product at `>>24`, not `>>32`.** The port shifted each product
  all the way down before accumulating, which is right for one product and
  off by one LSB for a subtract whose discarded low bits are non-zero:
  `floor(-floor(q/2^24)/2^8)` is one less than `-floor(q/2^32)`. Route A's
  model (QEMU's EMAC plus `tools/patches/unicorn_emac_fractional.patch`) accumulates
  at `>>24` and shifts down by 8 only when the accumulator is READ
  (`get_macf`). **That single bit was the whole remaining difference in the
  M6c gate**: the frame handler's `msacl` came out −15 where route A had −16,
  an `spl` floor two instructions later turned it into 1 instead of 0, and
  the sequencer's live nibble read `0x09` where route A reads `0x08` — on
  every write, on every frame. `movclrl` now returns `acc >> 8`, `movel
  Rn,%accN` writes `(int32)v << 8`, and the accumulate sign-extends from 48
  bits (`macsatf`), all as route A does.

`test_emac` now carries **23** assertions covering all of it, every one
watched failing first. ✅ Encodings from `m68k-elf-as -mcpu=5475`, listed
beside each rule; the accumulator alignment has a paired control (`macl` with
the same operands, which agrees under either model, so only the `msacl` case
is evidence).

### How the last bit was found, in order

Worth keeping, because none of it was reasoning:

1. `--watch-mem` on the port (route A's own flag, ported) said the live
   nibble is written at `0x4000b910` and `0x4000b9bc` in both.
2. objdump on the image: `0x4000b910` is `moveb %a2@,%a1@(0,%a3:l)` with
   `a2 = a0 + 62`, and route A's `--watch-pc` said `a0 = 0x80001798 + track`.
3. So the byte comes from `0x800017d6 + track`, and the only writer of that
   table is the frame handler's own loop at `0x4000aef6`:
   `msacl / movclrl %acc0,%d2 / addl #16,%d2 / spl %d3 / andl %d3,%d2 /
   moveb %d2,%a2@+`.
4. `--watch-mem` on both emulators for the two inputs: the frame timebase
   (`0x46104cf0`) is **identical** (`0x16800`, `0x21c00`, `0x2d000` written at
   `0x4000aec4`) and so are the per-track words at `0x80001904`. Two inputs
   the same and the output different leaves the arithmetic.
5. Reading route A's actual model — the patch and QEMU's `macmulf` /
   `get_macf` — gave the alignment, and the arithmetic predicts the sign of
   the error before the fix was written.

### No regression

`ctest` 6/6; the M6a oracle diff still reports **8 compared fields agree, 0
disagreements**; `make check` green.

## Milestone O7 — the card and the project load ✅ (8 Sep 2026; the stall was a fault)

The card model, its memory map and the live-call machinery are in and gated;
**the mount does not complete**, so the milestone's own gate (6,189 ATA
commands / 30,467 sectors) is nowhere near. What is measured:

**What works.** `card.{h,cpp}` is route A's `AtaCard` — the task file at
`0x90000000`, IDENTIFY/READ/WRITE/CFA-TRANSLATE, and the ATA rule that a task
file count of 0 means 256. ⚠️ **The FAT16 image builder is deliberately not
ported**: route A builds the image from a directory tree in Python, and that
is a build-time tool producing bytes, not machine behaviour — this port reads
the same `.img`, so both emulators are guaranteed to be looking at identical
media. `Rtos` carries `attachCard` (the INTRQ rules, one interrupt per
sector, cleared by a read of the STATUS register), `callAsMain`,
`postMessage`, `requestCardMount`, `setNames` and `loadProjectLive`.

**✅ The ATA host-status byte, without which nothing happens at all.**
`0xfc0a4039` bit 3 must read CLEAR. An unmodelled peripheral answers all-ones,
the bit is set, the driver concludes there is no card — and the mount request
posts, SYS runs the card case, and **zero ATA commands** are issued, with no
error anywhere. Route A carries it in `EXTRA_OVERRIDES` from the boot.

**✅ The card's four memory maps are what O5 misattributed** — see the
retraction in the O5 section above. With them, the auto-mapped access count
falls from 20,348,051 to **4**.

### ⚠️ THE MOUNT'S REAL DEFECT: INTRQ IS NOT INSTANTANEOUS, AND THE FIRMWARE DEPENDS ON THAT

✅ **Measured 8 Sep 2026, and it is the finding of the milestone.** The port
asserted INTRQ on the same instruction that wrote the ATA command. A PC ring
armed at the first command shows what that costs, in order:

```
0x40015a50   the driver writes the command
0x40015308   the ISR runs ON THE VERY NEXT INSTRUCTION
   ...       256 words streamed, byte-for-byte route A's
0x40000968   the ISR SIGNALS the event
0x40015a58   only now does the caller execute its next instruction
0x40000818   ... and call the RTOS event WAIT
0x40000550   the scheduler: nothing to run but main
0x4001fc9c   main's spin, forever
```

The signal arrives **before the waiter waits**, so the wait blocks on an event
that already happened. ⚠️ **Route A survives this only by accident of
granularity** — it delivers interrupts at burst boundaries, which happens to
give the caller time to reach the wait. A real CF card takes tens of
microseconds to fetch a sector, so a completion LATENCY is the physical
behaviour and instantaneous assertion is the artefact. `Rtos::m_ataLatency`
(1 sample, ~23 µs) books the assertion forward; `tickTimers` fires it.

This is the "a lock-step emulator cannot show you a race" lesson inverted: the
port's finer granularity **exposed** a race route A's coarse bursts hide.

### ✅ `mvz` takes an ADDRESS REGISTER source, and the storage stack needs it

With the latency in, the mount died at `unimplemented opcode 71c8 at
0x40017c10`. That disassembles under `m68k:cfv4e` as **`mvzw %a0,%d0`** (and
another at `0x40017c2c`): the V4e layer's effective-address reader handled
`Dn` but not `An` direct. Same family as O4's `ff1` — the image is the
evidence, not the toolchain.

### Where it stops NOW

With the latency, the `An` source, route A's own park condition (the PC alone —
requiring nothing pending as well never comes true once the card is live) and a
step budget that survives preemption, **the mount succeeds**:

| | port | route A |
|---|---|---|
| card ready (`0x460d1cb8`) | **1** | 1 |
| ATA commands | **1,407** | 6,189 |
| sectors read | **10,695** | 30,467 |
| sectors written | **297** | **297** ✅ |
| `LOAD PROJECT` posted | **yes** | yes |

The written count matches exactly, and one ATA interrupt arrives per sector
(10,994 for 10,695 sectors). ⛔ The load then **stops** rather than running
slowly: 30,000 ms of emulated time produces the same 1,407 commands as 6,000.
It ends with the engine task cycling at **`0x400165dc`**, with `main` and `sys`,
and the in-flight word (`0x46c8c58a`) clear. That is the next thing to chase.

<details><summary>the earlier stopping point, before the race was found</summary>

**⛔ Where it stopped.** With the card attached and the host-status
byte modelled, the machine parks at main's spin, the mount request posts, and:

| | |
|---|---|
| ATA commands issued | **1** (IDENTIFY) — route A's load issues 6,189 |
| ATA interrupts taken | **1**, and the line is left deasserted |
| card-ready (`0x460d1cb8`) | **0** — never set, so `LOAD PROJECT` is never posted |
| the machine afterwards | **idle**, not spinning: a profile over the mount window is indistinguishable from a boot-only profile |

So the IDENTIFY completes and its interrupt is delivered and acknowledged, and
then SYS is **blocked waiting for something that never arrives** — a missing
wake-up, not a wrong loop. (✅ It was the INTRQ race above.)

</details>

**Route A's own numbers for this project, re-measured today:** 6,189 commands,
30,467 sectors read, 297 written, `saved_bank=1`, `final_bank=0`. ⚠️ Note that
`PART_PTR` reads `0x400e21e0` **before any load** — it is the bank blob's base,
so a non-null `PART_PTR` is NOT evidence a project loaded. The work order's
`0x4017d520` is from a different project; the count pair is the gate that
travels.

**No regression:** the oracle diff is still 8 compared fields with zero
disagreements, `ctest` 6/6, `make check` green.

### ✅ THE "STALL" AT 1,407 COMMANDS WAS A LINE-A EXCEPTION, AND THE UART HID IT

Measured 8 Sep 2026, third session. The port ended the load with the engine
task cycling at `0x400165dc`, 1,407 ATA commands at 6,000 ms and at 30,000 ms,
and it was written up as a stall to chase. It was not a stall.

**How it was found, in order — each step an instrument, not a theory:**

1. `--cmd-log` dumps every ATA command in route A's own log order, and route
   A's log was dumped the same way. `diff` said the port's 1,407 were
   **byte-identical to route A's first 1,407**. So nothing the card did was
   wrong; whatever stopped the load stopped it *between* commands.
2. The PC ring was rewritten as a true ring (the last N instructions, not the
   first N — a stall asks what was running, not what the ISR did). The tail
   was a three-instruction spin at `0x4003afa0`:
   `moveb 0xfc064004,%d0 / movew %d0,%ccr / bpls` — **polling bit 3 of the
   panel UART's status, TXEMP,** which `Uart::read` never set (it reported
   TXRDY only, as route A's model does).
3. Setting TXEMP (a model that consumes every byte at once is always both
   READY and EMPTY; TXRDY-only describes a shift register with a byte stuck
   in it forever) turned the spin into a **`halt`** at `0x4003b108`, and the
   load report — which now names how its run ENDED, not just the counts —
   said `ILLEGAL -- unimplemented opcode 4ac8 at 4003b108`. Halt is the
   last instruction of a printer. `--serial-out` showed what it had printed:

   ```
   EXCEPTION
   SSP:4 VEC:0A
   FS:0 SR:2004
   ADDR:4009D8D0
   ```

   VEC:0A is the line-A exception. The firmware's own panic handler had
   named the faulting instruction; with TXRDY only it could never finish
   saying so.
4. `0x4009d8d0` is `mov3ql #-1,%a0@+` (`a158`), eight in a row clearing a
   structure. `v4e.cpp`'s MOV3Q handled **Dn only** — "only Dn is reached
   by this firmware" — and returned Unhandled for everything else, which
   the A-line dispatch turned into a real exception.

**The fix, attributed separately:** `writeEaLong` + MOV3Q to every
alterable destination. On its own it takes the load from 1,407 commands to
**12,373**, and the whole of route A's 6,189-command log is an exact prefix
of the port's. TXEMP on its own changes nothing about the load; it is the
change that made the fault *legible*. Both are kept.

**Why route A never saw it:** Unicorn's m68k implements MOV3Q natively, so
route A has no shim to get wrong, and it never reaches the exception
printer, so its identical TXEMP gap costs it nothing. "Route A does not have
it either" was true of both and evidence of neither — same family as the
`byterev`/`ff1` retraction in O4 (a toolchain that will not assemble an
opcode is not evidence the part lacks it; the image is).

**Gate:** `test_emac` now holds MOV3Q's memory forms (`#-1,%a0@+`,
`#1,%a0@+`, and the post-increment), watched failing on the old code.
`ctest` 6/6; oracle diff 8 compared fields, zero disagreements; the serial
stream 5,731 bytes identical.

> ✅ **CLOSED 8 Sep 2026 by O7b (below): it was the harness's project-name
> ordering, not a firmware divergence, and the two now issue the same 6,189
> commands line for line. The 🟡 "select bank 0" hypothesis below is
> RETRACTED.**

**⚠️ OPEN (superseded) — the port runs PAST route A's end, and that is a
divergence, not a budget.** Route A stops at 6,189 commands with `M6b load: PASS` at a
6,000 ms budget **and at a 12,000 ms budget** (re-measured 8 Sep 2026: same
6,189 / 30,467 / 297). The port's load runs on to **12,373 commands /
60,677 sectors / 562 written** and parks in main, the same total at 6,000
and at 20,000 ms. The extra 6,184 are 5,919 READs and 265 WRITEs over the
same FAT and bank sectors (2301, 2333, 22889 …) — the shape of a second
bank parse. 🟡 Inferred, not measured: the port performs a bank (re)load
that route A's SYS skips — route A ends "saved_bank=1, final_bank=0" with
SYS applying the engine's reset-time "select bank 0" (RTOS_FORK.md §7), and
in the port that select may be going to the card. Nothing on hardware says
which is right. **The next measurement is which task and PC issue command
6,190 in the port** (`--ata-trace` carries the PC; its 200,000-access cap
will need raising or arming at a command index), then the same site in
route A to see why it does not.

## Milestone O7b — why the port loaded twice ✅ (8 Sep 2026)

**It was a race in the HARNESS, not a difference in the firmware, and the two
emulators now issue the same 6,189 ATA commands line for line.**

```
diff <(cut -d'|' -f1 out/oracle/o7b_port_cmds2.txt) out/oracle/o7b_routeA_cmds.txt
# (no output)
6189 ATA command(s): 1 IDENTIFY, 5921 READ, 30467 sector(s) read, 297 written
```

### The mechanism, measured

The port's extra 6,184 commands were a **second, complete project load** — its
tail is its own head again, differing only in the writes the first pass had
already made. Both loads are issued by the `engine`, and both arrive the same
way: something posts LOAD PROJECT. So the question was who posts it twice.

✅ **`--watch-pc` on `0x40023c7c` (the post) and `0x40085336` (the engine's
LOAD PROJECT case) answers it directly.** The port hits each **twice**, route A
**once**. The port's second post comes from **`sys`**, with the return address
`0x4002576a` — a call site the image contains exactly once:

```
4006203a: jsr 0x40056600
40062040: jsr 0x40056744
40062046: tstl %d0
40062048: bnes 0x40062050      ; non-zero: do nothing
4006204a: jsr 0x4002574c       ; zero: "reload the current project"
40062050: ...                  ; the join point

4002574c: pea 0x100f8378       ; <- the PROJECT NAME
40025752: jsr 0x40013db0
40025758: addql #4,%sp
4002575a: tstl %d0
4002575c: bles 0x4002576c      ; nothing named: return
4002575e: pea 0x100f8378
40025764: jsr 0x40023c7c       ; post LOAD PROJECT
```

`0x40013db0` is **`strlen`** (`clrl %d0 / addql #1,%d0 / tstb %a0@(0,%d0:l) /
bnes / rts`). So `sys`'s media case says, in as many instructions: **a card
appeared — if a project is named, load it.**

✅ **Both emulators run that path identically, instruction for instruction,
and split on one value.** `--watch-pc` on `0x40062000`, `0x4006200c`,
`0x40062046`, `0x4006204a`, `0x40025752`, `0x4002575e` gives the same registers
at the same five sites in both (`d0 = 0`, then `2`, then `0`) — and then:

| | route A | `ot_emu` (before the fix) |
|---|---|---|
| reaches `0x40025752` (the `strlen`) | ✅ at sample 10,361 | ✅ at sample 10,210 |
| `strlen(0x100f8378)` returns | **0** — nothing named yet | **3** — `"RIG"` |
| reaches `0x4002575e` (the post) | ❌ never | ✅ |
| ATA commands | 6,189 | 12,373 |

The whole divergence is **whether the harness has written the project name by
the time `sys` next gets the CPU**. Route A's mount tail runs about 200 samples
longer, so its media case fires *before* `set_names`; this port's fired after.

✅ **The falsifier, run:** told to write the name first (`emu_rtos.py
--load-project --names-early`, a flag added for exactly this and off by
default), **route A reproduces the port's numbers exactly** — 12,373 commands,
60,677 sectors, 562 written — and its `--watch-pc` shows the same
`0x4002575e` → `0x40023c7c` from `sys` with `d0 = 3`. That is what turns this
account from a story into a measurement.

❌ **RETRACTED: the work order's 🟡 hypothesis that this was SYS's reset-time
"select bank 0" going to the card.** The select-bank case is not on this path
at all; the second load is a media-mount response, and the deciding value is a
string length.

### What the port does about it

Neither order is the firmware's — the firmware's rule is unambiguous and both
emulators agree on it. What differed was the harness, so the harness now
*chooses*:

- **Default (route A's order, and what the O7 gate measures):**
  `loadProjectLive` waits for `sys`'s media case to reach its join point
  (`g_mediaCaseJoin`, `0x40062050`) before writing the names, so the name is
  provably not yet set when the case runs. One load, 6,189 commands,
  byte-identical to route A's log.
- **`--names-early`:** write the names before requesting the mount. Two loads,
  12,373 commands. ⚠️ **This is arguably what HARDWARE does** — on the unit the
  project name is set long before a card goes in — so the flag is not a
  mistake to be avoided, it is the other real configuration. Nothing on
  hardware has been measured either way.

The load report now says which order ran and whether the media case was seen:

```
names after the mount; sys's media case ran before the name
```

### Instruments added

`--watch-pc ADDR[,ADDR…]` on the C++ port (route A's own flag: registers and
the top of the stack at each hit, so a hit on a callee names its caller and its
arguments), `--cmd-log` on **route A** (the counterpart of the port's, so the
two logs diff line for line), and the port's `--cmd-log` now carries the PC and
the task per command after a `|` — `cut -d'|' -f1` still reproduces the old
form exactly, so O7's diff method is unchanged.

⚠️ **And a third instance of the silent-instrument trap.** `_watch_report()` was
called from route A's M6c branch and (since 8 Sep) its plain branch, but **not
from `--load-project`** — so `--watch-pc` on a load run printed nothing whether
the address fired twice or never, which is precisely the question O7b exists to
ask. Fixed. `RTOS_FORK.md` §10.3b records this trap; this is its third
appearance, in the third branch.

### No regression

The O6 fidelity gate still reports **5 compared fields agree** (400 frames, 28
ticks, the trig at frame 344 with bytes `0x08`/`0x18`); the M6a oracle diff
still **8 compared fields agree, 0 disagreements**; `ctest` 6/6; `make check`
green.

## Milestone O8 — the DSP cores and the host port 🟡 BUILT THROUGH STEP 3 (8 Sep 2026)

**The two real DSP cores sit behind the host port (`--dsp`), the firmware boots
them itself, the boot's upload is verified byte for byte by a ctest, and the
M6a gate passes with the cores live. Steps 4 and 5 of the work order are open**
(what they stopped on is at the end of this section). The session before this
one decoded the join from the firmware's code (kept below, under "What runs, and
when"); this one wired it and measured what the wiring exposed — six things in
the vendored DSP emulator, one in the model of the shared window, and one in
this port's own first guess at the handshake.

### ❌ The host-port readings that had stood since ARCHITECTURE.md §6 — retracted

The window `0x20000000`–`0x20000fff` is the **HI08 host-side register file** of
whichever core the GPIO byte at `0xfc0a400c` selects: one byte register per
4-byte stride, in the LOW byte of the 16-bit access.

| offset | register | what the firmware does with it |
|---|---|---|
| +0x00 | ICR (RREQ 0, TREQ 1, HF0 3, HF1 4, INIT 7) | writes `0x81` = **INIT\|RREQ, an interface reset** — ❌ not "start the DSP" |
| +0x04 | CVR (HV 6:0, HC 7) | the loader clears it; the frame handler writes `0x8c` = **HC \| vector 0x0c → P:0x18, a host command**, and polls bit 7 until the DSP takes it — ❌ not "swap frame" |
| +0x08 | ISR (RXDF 0, TXDE 1, TRDY 2, HF2 3, HF3 4, HREQ 7) | the loaders spin on `& 6` = TXDE\|TRDY before every word and `btst #0` = RXDF for the echo — ❌ not "bit 6 = DSP ready" |
| +0x14/18/1c | TXH/TXM/TXL, RXH/RXM/RXL | bits 23:16, 15:8, 7:0; the write of TXL sends, the read of RXL takes |

✅ All from the firmware's own code (`0x40001d4c`, `0x40001b18`, `0x4000aad0`),
and confirmed by the join working: the vendored HDI08 answers those registers
and the firmware's upload runs to completion against it. ARCHITECTURE.md §6 and
DSP.md §1 are corrected in place.

### ✅ The gate, and it is self-checking: `ot_dsp_test` (ctest `dsp`, under a second)

The firmware boots with the pair attached; a step hook fires at the instruction
after each upload returns (`0x40001e96` for core 0, `0x40001edc` for core 1) and
compares DSP memory with the image's bytes, parsed independently (DSP.md §2: the
uploader's own walk, mirrored):

```
[PASS] core 0: the boot ROM took 50 words for P:0x31000 and jumped
[PASS] core 0: P:0x31000 holds the bootstrap the image carries  50/50 words
[PASS] core 1: the boot ROM took 58 words for P:0x32000 and jumped
[PASS] core 1: P:0x32000 holds the bootstrap the image carries  58/58 words
[PASS] core 0: the payload parses to its last byte  98 records, 79563 of 79563 bytes, jump 0x30000
[PASS] core 0: every payload record had landed where it names  26221/26221 words
[PASS] core 0: the host sent and took back exactly what the walk predicts  26569 sent (26569 predicted), 99 echoed (99 predicted)
[PASS] core 1: the payload parses to its last byte  91 records, 77061 of 77061 bytes, jump 0x38000
[PASS] core 1: every payload record had landed where it names  25408/25408 words
[PASS] core 1: the host sent and took back exactly what the walk predicts  25743 sent (25743 predicted), 92 echoed (92 predicted)
[PASS] core 0: left the bootstrap and is running the payload  pc 0x30010, no fault
[PASS] core 1: left the bootstrap and is running the payload  pc 0x57, no fault
```

Two mechanisms that cannot fake each other agree: the firmware's ColdFire-side
loader with its TXDE/RXDF polls and echo check, and the vendored DSP running
first the chip's bootstrap ROM (`DspBoot`: count, address, words, jump) and then
the firmware's own 50-word HDI08 loader, which reads a space word, echoes it,
reads an address into r0 and a count into b1, and `dor`-loops
`movep x:<<M_HORX,p/x/y:(r0)+` — disassembled with the vendored
`dsp56kDisassemble`, `out/oracle/o8_blob0.dis`.

⚠️ **Why the check runs at upload time and not at the handoff:** the first
version compared at the handoff and found 19 words of payload A at P:0x38000
reading zero. That range is core 1's entry stub, and core 1 had run it and then
cleared Y:0x38000.. as its delay buffer — the window is one memory (next
section). "Harmless once booted", as CHIP.md predicted; a gate has to look
before that.

### ✅ THE SHARED WINDOW IS ONE MEMORY, and the firmware depends on it

With P private per core (the vendored patch's dsp_host form: X with X, Y with
Y), core 1 jumped to its entry P:0x38000 — an address only core 0's upload had
written — found zeros, and ran off the end of P memory: the PC ring read
`7ffc1 7ffc2 … 7ffff`, then a fault at 0x80000. CHIP.md had this measured on
hardware already (`dsp/alias_probe.asm`: P, X and Y are the same words in
`0x30000`–`0x3FFFF`, reachable by both cores); the port now models it that way,
`Memory::setSharedWindow(lo, hi, all, hook)` in `tools/patches/dsp56300.patch`, one
64K array behind both cores' P, X and Y, the hook dropping both cores' decoded
opcode for any word written there. `dsp_host` keeps its two-way window — it
renders bit-identically with it and, as DSP.md says, cannot answer aliasing
questions; that is now a documented difference between the two machines.

### ✅ Six things the vendored emulator does that the firmware cannot live with (nine by 8 Sep — the ninth, the DMA ring that never reloaded, is under "the ESAI rate" below)

Each one first presented as a hang or a crash with no diagnostic, and each was
found by an instrument, not by reading — recorded with the instrument:

1. **A hardware DO loop runs to completion inside one `execInterpreter()`
   call** (`do_exec` nests the loop). The 50-word loader polls the host port
   INSIDE a `dor`, so the call could never return to the ColdFire that had to
   feed it. *Stack sample:* `op_Dor_S → do_exec → op_Brclr_pp`, forever.
   Patch: with `setHostStepped(true)`, `do_exec` pushes the loop state and
   returns; the host applies `doLoopEnd()` after every instruction (the same
   test, on the same registers).
2. **Interrupts always dispatch through the JIT** when it is compiled in, even
   with the interpreter driving — `execInterrupt` reads `g_useJIT`, which is
   compile-time. `dsp_host` never takes a DSP interrupt, so it never met this.
   *lldb:* `EXC_BAD_ACCESS` in `funcCreate`, called from `execOp` with a
   garbage `this`. Patch: a host-stepped core interprets its interrupts, and
   `exec()` too.
3. **A masked pending interrupt starves the peripheral clock.**
   `execInterrupts()` returns early on a masked head and never re-hooks the
   peripheral exec, so a core that masks interrupts for a while — core A boots
   with `ori #3,mr` — freezes its own ESAI. *`--dsp-trace`:* the peripheral
   target clock stuck at 845,824 while the instruction counter ran to 4 M;
   SAISR at RDF\|ROE; 3 frames ever. Patch: service the peripherals under a
   masked head (a masked interrupt waits; the peripherals do not).
4. **The ESAI blocks on an empty input ring** (a condition-variable wait, meant
   for an audio thread). *Stack sample:* `EsxiClock::exec → Esai::execRX →
   RingBuffer::pop_front → ConditionVariable::wait`. No patch: the pair
   installs non-blocking callbacks — silence in, output frames counted — and a
   clock of ONE ESAI FRAME PER SAMPLE at the pair's own instructions-per-sample,
   so the DSP's audio clock and the ColdFire's sample clock cannot drift apart.
5. **A PC past P memory is a garbage member-function pointer** (the opcode
   cache is indexed by the PC into a table sized to P). *lldb:* the same
   `funcCreate` frame, from `execOp`, `this` = a DSP data word. The pair stops
   the core, records it as a fault, and prints the last 64 PCs — which is what
   found the window finding above.
6. **A fast interrupt never sets the PC to its vector** (the vector's two words
   run inline), so "HC clears when the PC lands on P:0x18" never fired and the
   frame handler polled HC forever: 0 frames, 0 ticks, `host commands 1`.
   Patch: an interrupt-taken hook (`setInterruptTakenHook`); HC and HCP clear
   from it.
7. **The DSP's own audio DMA silently moved nothing.** Core A's main loop
   (P:0x4b..0x53) polls DMA channel 2's source pointer for 0x8070 / 0x80f0 —
   the halves of a 256-word ring, X:0x8000.. → ESAI TX0, one word per TDE
   request (DCR2 `0xcc6220`: source mode DualCounterDOR2, destination fixed);
   channel 3 is the mirror for audio in (ESAI RX0 → X:0x8100.., DualCounterDOR3,
   DCO3 `0x23f` with DOR3 = −575). *`--dsp-trace`:* DSR2 stuck at `0x8000`
   while the ESAI put out 2,334 frames. The vendored `execTransfer` handles
   neither address-mode pair and, in a release build, reaches an
   `assert(false)` that is compiled out and returns "finished". Patch: the two
   pairs, one word per request over the dual-counter ring — and **DCOL is the
   low TWELVE bits of DCO, not eight**: `0x23f` with an offset of −0x23f only
   returns the pointer to its base if DCOL counts 0x23f words (the firmware's
   constants decide it, the way the reciprocal tables decided the EMAC). With
   it DSR2 sweeps the ring, core A sends the host its first word after the
   boot and core B its first three mailbox words, and both cores advance to
   their next waits (P:0x97, P:0x8d).
8. **The host DMA on the DSP side had two more.** Its receive requests are
   rate-limited to one word per 200 instructions (a throttle for a threaded
   host; a 672-word block at that pace is 30 samples, twice a frame), and a
   channel armed while its request condition already holds never fires —
   `checkTrigger` returns false unconditionally upstream — so core B's
   transmit DMA, armed with HTDE already set, never started and its 256-word
   read-back timed out word by word (`read-back words 256 (256 not in time)`,
   51 M instructions spent in the pull). The pair sets the rate limit to zero;
   the patch re-enables the initial trigger for a host-stepped core.

The patch file is regenerated with `git -C vendor/dsp56300 diff >
tools/patches/dsp56300.patch`; `scripts/setup.sh` applies it; `dsp_host` is unchanged
by every part of it (its window form and its non-host-stepped mode are the
defaults).

### 🟡 The inter-core mailbox — inferred from both payloads' code

Core 1 parks at P:0x57 on `brclr #1,y:<<$ffffd3` and reads `y:<<$ffffd4`;
core 0 writes `movep r3,y:<<$ffffd7` and waits on `brset #1,y:<<$ffffd6`, then
sends `#2`. No vendored peripheral maps those, so the pair models a symmetric
one-register channel (transmit data at $D7, "still unread" at $D6 bit 1;
receive data at $D4, "one waiting" at $D3 bit 1) through a hook on unmapped
Y-side registers. The addresses are the firmware's; the bit semantics are
inferred from the two wait loops and nothing else. Core 0 has not sent a word
on it yet in any run — that happens on the frame-exchange path.

### ✅ M6a with the cores live, and the idle fast-forward validated

`ot_emu --dsp --ms 1000 --golden`: **the M6a gate passes** (10 created, 11 ran,
gate at 205.97 ms against 205.39 without the cores) and `oracle.py` reports
**8 compared fields agree** against route A. The boot sends the cores one word
after the handoff (`0x030000`); core 0 takes it and goes on running with its
ESAI ticking.

The cores are stepped in lockstep (1.14 instructions per ColdFire instruction
in the boot, 4535 per sample under the RTOS; both knobs, neither measured), and
a 20-second project load is 4 G instructions per core in an interpreter. So a
core found polling — eight instructions inside a three-word window, no hardware
loop open, no interrupt pending — is advanced to its next peripheral event the
way the chip's own `wait` is emulated (`idleStep`, the arithmetic is
`op_Wait`'s), then executes the poll once so it can see what changed. ⚠️ The
first version `continue`d without that execution, and the uploader waited for
an echo that a never-executed poll could not produce. ✅ **Validated by A/B**,
`--dsp` against `--dsp --dsp-no-idle` over the M6a run: ESAI frames 2688 / 2686
both, host words 26570 / 99 both, all 8 oracle fields agree between the two.

### Instruments added

`--dsp`, `--dsp-log FILE` (every host-side event: sel/icr/cvr/tx/rx/mail/
hc-taken, with the DSP due count), `--dsp-trace N` (a status line per core:
PC, SR, mode, pending, peripheral target, ESAI counts, SAISR/RCR/TCR/HSR/HCR),
`--dsp-no-idle`, `--dsp-verbose` (the vendored log lines, off by default: 3,260
per boot), `--edma-log FILE` (every kick with its whole TCD), the per-core PC
ring and fault in the report, `DSP=1 scripts/o6_gate.sh`. And the build is now
native: `/opt/homebrew/bin/cmake` — the x86 cmake at `/usr/local/bin` had been
building `out/emu` for Rosetta.

### ✅ The sequencer gate passes with the cores live

`DSP=1 scripts/o6_gate.sh` (the port with `--dsp`, the same staged card, the
same route A oracle): **400 frames, 28 ticks, the trig at frame 344 on track 0,
bytes `0x08` then `0x18` — 5 compared fields agree.** Over those frames core A
took 2,400 host commands and core B 1,200; the host wrote 6,400 words and took
back 500; the pair's event log shows the frame protocol the tape had recorded
(sel/icr/cvr/tx/rx/hc-taken), and the ColdFire's 16,800 eDMA kicks are in
`--edma-log` with their TCDs. Before the DMA fix (item 7 above) this run
stopped at frame 0 with 0 ticks; before the HC fix (item 6) at the first host
command.

### ✅ Step 4 — the blocks, decoded from the tape, and the lanes corrected

Route A's tape (`emu_rtos.py --tape`, the `hostw` and `edma` records) gives
the frame protocol per core, in order, and it decides the word width:

| host command | words to +0x1c before it | eDMA that follows | bytes | DSP count |
|---|---|---|---|---|
| `0x8c` (vector 0x18, "frame") | — | — | — | — |
| `0x89` (0x12, "DMA a block OUT") | `0x6600`, `0x1ff` | ch1 paced → ch6 → ch7 | 512+256+256 | 512 |
| `0x89` | `0x6600`, `0x0ff` | ch1 burst | 512 | 256 |
| `0x88` (0x10, "DMA a block IN") | `0x6080`, `0x29f` | ch0, NBYTES 0x150 × 4 | 1344 | 672 |
| `0x88` | `0x6800`, `0x03f` | ch0, 0x20 × 4 | 128 | 64 |
| `0x88` | `0x6000`, `0x07f` | ch0, 0x40 × 4 | 256 | 128 |
| `0x88` | `0x6400`, `0x1ff` | ch0, 0x100 × 4 | 1024 | 512 |

(two of the IN blocks repeat per frame, one per core, the GPIO byte toggling
between them; the DSP's handlers at P:0x588 / P:0x597 read the two words,
mask them to 16 bits, and arm DMA0 from HORX / DMA1 into HOTX for `count+1`
words.) **Every count is exactly half the byte count.** So one DSP word rides
each 16-bit bus cycle, a 32-bit eDMA access at +0x1c is two words (high
halfword first), and ❌ the pair's first lane model — the odd byte of each
halfword is the register, the even byte nothing — made every frame word 8
bits wide. The corrected rule: on this 16-bit port the odd byte is the
register the stride names and the even byte the register before it, so a
halfword at +0x1c lands TXM:TXL and sends, at +0x18 TXH:TXM, at +0x14 TXH.
The loader's byte-at-a-time upload is consistent with it (its `movew` at
+0x1c carries mm:ll, and TXM was already mm), and so is the DSP masking a
count sent by a single `movew` to 16 bits. The DSP words carry TXH stale
(0x03, the last upload byte) above the 16 payload bits. DSP.md's "336-word
records" were bytes: the record is 672 words.

`Rtos::installHostPortMover` then moves the data the eDMA carries: a block
whose DADDR is the window is pushed whole at the kick, halfword by halfword,
as the bus cycles the eDMA would make (the vendored HDI08's receive ring
holds it until the DSP's DMA0 drains it, a word per peripheral tick); a block
whose SADDR is the window is pulled at completion from the core the kick was
made against, running that core until each word is in HOTX (its DMA1 puts
them there one at a time, since HOTX is a single register). The block size
is NBYTES × the minor-loop count (CITER bit 15 = a minor link, count in bits
8-0), the RAM side contiguous. `--dsp-peek core:space:addr,len` reads DSP
memory after the run.

⚠️ **And one of route A's completion rules changes when the cores are
attached.** "A host-port burst completes at once" was right for a model that
moved nothing; with the DSP draining a real ring it let the frame handler issue
the next block's destination and count while the DSP was still taking the
previous block, and the DSP's handler read data words as its arguments: 7
frames in the window, the receive ring overflowing, 36 of 64 host commands
never taken. On the chip the completion interrupt fires when the DSP has taken
the last word, and that is what lets the handler continue — so with the pair
attached a burst INTO the port completes at the first tick the selected core's
receive ring is empty (`Edma::setCompletionGate`), and without it every rule is
route A's (the non-DSP O6 report is byte-identical before and after).

✅ **With the mover, the gate still passes and the blocks flow**: 400 frames,
28 ticks, the trig at frame 344 (`0x08`/`0x18`), 5 compared fields agree;
**2,400 blocks / 870,400 words to the DSPs, 1,600 blocks / 307,200 words
back, 0 not in time**, 60,820 ticks spent holding a burst for the DSP to
drain; per core 2,400 / 1,200 host commands taken, 204,800 / 102,400 read-back
words produced by the DSPs' own transmit DMA. 

### ✅ And the frames carry content — outbound. The DSP returns silence.

The counter that decides whether any of the above means anything
(`--block-log`, non-zero words counted at the moment of the move, not peeked
afterwards). Over the 400-frame run:

| direction | blocks | words | non-zero |
|---|---|---|---|
| ColdFire → DSPs | 2,400 | 870,400 | **40,853** |
| DSPs → ColdFire | 1,600 | 307,200 | **0** |

✅ **The outbound path is live and it tracks the sequencer.** Non-zero words
per 50 frames sit at 5,088 and rise to 5,100 across the trig at frame 344,
then 5,238 — the parameter frames change when the sequencer fires. The 672-,
128- and 64-word records carry 14, 30-33 and 11 non-zero words each: sparse,
which is what a parameter frame with most slots idle looks like. And the words
reach DSP memory: the landing peek reads `030004 030009 030800 … 030b40`
where the block ended.

❌ **The inbound path is zeros in every frame band, including after the trig**
— 0 of 307,200 words, with the pull never timing out, so the DSP genuinely put
zeros in HOTX rather than the port failing to collect them. Its own DMA1 is
sourcing from X:0x4700/0x4780/0x4800 and those hold zeros. The DSP is
computing silence, which is what a machine with no sample audio and a silent
ESAI input should compute. **What has NOT been established is why** — no
sample data reaching the DSP, or a voice that never starts, are both open and
both sit on the audio-in path that is O9's ground.

### ⚠️ The destination word is the host's, the address is the DSP's

`--dsp-peek` of X:0x6080 read zeros and briefly looked like "the frames are
empty". It was the instrument: **0x6080 is the address the HOST names, not the
one the DSP uses.** Measured, with the command's arguments snapshotted at the
command and both candidates peeked at the same instant:

```
cmd args 036080 03029f | DMA0 ddr 004320 dco 00029f | X@6080 000000 000000 | X@4080 030000 030000
```

The firmware sends dest `0x6080`, count `0x29f`; the DSP arms its DMA0 at
`0x4080` and the block lands there. Every block is the same 0x2000 apart:
0x6080 → 0x4080, 0x6000 → 0x4000, 0x6400 → 0x4400, 0x6800 → 0x4800.

✅ **The landing addresses are the payload's own bank pointers, exactly.** The
dispatcher at P:0x40 loads two banks and takes one per frame:

```
bank A:  r6 = $4000   r7 = $4080   r2 = $4400   r5 = $4600   r4 = $4800
bank B:  r6 = $2000   r7 = $2080   r2 = $2400   r5 = $2600   r4 = $2800
```

Every block landed on a bank-A pointer, and each host word is exactly the two
banks' addresses OR'd together (`0x4080 | 0x2080 = 0x6080`). 🟡 **Inferred,
not located:** that bits 14 and 13 are a bank select the DSP masks down to the
bank it is running. The masking instruction has not been found — the handler
at P:0x588 is the only write to DMA0's destination register in the payload and
it masks to 16 bits only, which would leave 0x6080. Falsifier: a run in which
the DSP takes bank B should land the same words at 0x2xxx.

### ✅ The bank is the audio ring's phase — and one half is never used

The dispatcher's bank choice is not a flag, it is a wait on the audio-out
DMA's own source pointer (P:0x4a, read from the payload):

```
P:0004a  clr   b
P:0004b  movep x:<<M_DSR2,a          ; where the audio-out DMA is playing from
P:0004c  cmp   #>$80f0,a
P:0004e  beq   ...                   ; -> bank B: r0 = $8000, r2/r5/r6/r7 = $2400/$2600/$2000/$2080
P:0004f  cmp   #>$8070,a
P:00051  beq   ...                   ; -> bank A: r0 = $8080, r2/r5/r6/r7 = $4400/$4600/$4000/$4080
P:00052  add   #<$1,b                ; else spin, counting the wait
P:00053  bra   P:0004b
```

That is a textbook double buffer: the audio ring is X:0x8000-0x80ff (DMA2,
`DOR2 = -255`, `DCO2 = 0xff`), and the DSP waits until the play pointer is
about to leave one half, then works into the other — `r0 = 0x8080` when the
pointer is at 0x8070, `r0 = 0x8000` when it is at 0x80f0.

⚠️ **In 400 frames it took bank A every time.** The only landing addresses in
the whole run are 0x4078, 0x4318, 0x45f8 and 0x4838 — the DSP never once saw
`DSR2 == 0x80f0`, so half of its double buffer is dead and the audio ring sits
in a fixed phase against the frame clock. That is a **timing knob nobody has
measured**: the pair drives the ESAI at one frame per sample at the same
instructions-per-sample the cores run at (`DspPair`'s `_ips`, defaulted from
`docs/firmware/CHIP.md`'s clocks), and neither that ratio nor the resulting ring rate
has been checked against anything. The parameter path does not care — it
passed every gate — but an audio comparison would be measuring a machine whose
output buffer alternation never happens. **Settle the ESAI rate before
believing any audio the port produces**, and treat "bank B is never taken" as
the falsifier for having got it right.

⚠️ **And DMA0 cannot be read at the eDMA kick to settle it** — a third
instance of the same lesson, and it nearly went into this document as a
finding. The command's two argument words and the CVR write all precede the
kick, but the CVR only *injects* the interrupt: at the kick the DSP has not
taken it, so its handler has not read the arguments, and DMA0 still holds the
PREVIOUS block's arming (measured: DCO0 always one block behind). That is not
a defect — the ring is a FIFO, so the arguments sit ahead of the data and the
DSP reads them first, arms, then drains. It does mean the armed value has no
readable moment in this model, and the destination has to be read from where
the pointer ends up.

⚠️ Two instrument lessons, both the project's usual family. **A peek after the
run cannot tell "nothing was sent" from "the DSP consumed it"** — count at the
move. And **a note taken at the eDMA kick lags a whole block**: read at the
kick, DCO0 still held the PREVIOUS block's count and it read as if the
firmware's destination were being ignored entirely. The note is taken at the
completion the drain gate holds, which is when the DSP-side state means
something.

### ✅ The ESAI rate, settled from the firmware's own constants (8 Sep 2026)

The "unmeasured knob" above had two halves, and the payload pins both.

**1. The port's ESAI ran eight times slow, and that is why bank B never
came.** The vendored clock's `setCyclesPerSample` is per SLOT, not per frame:
`Esai::execTX` advances `m_txSlotCounter` once per call and the frame
callback fires when it wraps, and `EsxiClock::updateCyclesPerSample`'s own
derivation halves the per-sample count with the comment "2 samples = 1 frame
(stereo)". `DspPair` passed `ips` (4535) straight through, so with the
payload's `TDC = 7` (eight slots) one audio frame cost 8 × 4535 instructions
— the 256-word ring at X:0x8000 advanced 16 words per 16-sample host frame
instead of 128, and the dispatcher's `DSR2 == 0x80f0` never arrived before
the next `0x8070`. ✅ Read from the vendored source, not inferred from the
symptom. The fix is one division: one slot per `ips / 8`, and a report line
that counts ESAI frames between consecutive `0x8c` host commands, whose
value the ring needs to be exactly **16**.

**2. The rate itself.** Payload A's setup (`out/dsp/payload_A.asm`,
`P:0x30024..0x3007f`, `tools/build/dsp_disasm_all.py`):

```
030024: movep #>$aa0000,x:<<$ffff98      ; PDRH: ETI1 ERI1 ETI0 ERI0 = 1
030026: movep #>$40,x:<<M_SAICR          ; SYN = 1
03002a: movep #>$f40f00,x:<<M_TCCR       ; THCKD TFSD TCKD; TPSR=1 TPM=0 TFP=0 TDC=7
03002c: movep #>$37d01,x:<<M_TCR         ; network, TSWS=$1f (32-bit slot), TE0
03002e: movep #>$f40f00,x:<<M_RCCR
03006d: movep #>$f00f00,y:<<M_TCCR_1     ; the second port: the same dividers, TSWS=$1e
```

`X:$FFFF98` on the DSP56720 is the Port H data register, whose top byte is
the **ESAI/EXTAL clock control** (DSP56720RM §8.2.2.4, Table 8-4): with
`ETI0`/`ERI0`/`ETI1`/`ERI1` set, *"the EXTAL clock can be used to generate
the ESAI transmitter/receiver clocks"* — the audio clock is derived from the
crystal, not from the core clock. The chain (RM Figure 9-3) is EXTAL → ÷2 →
prescale ÷1 (`TPSR = 1`) → ÷(TPM+1) = 1 → ÷(TFP+1) = 1 → bit clock; the frame
is 8 slots × 32 bits = 256 bit clocks. So

    fs = EXTAL / (2 × 256) = EXTAL / 512       (bit clock = EXTAL / 2)

🟡 The manual's prose says the *maximum* internally generated bit clock is
"Fsys/4", one ÷2 more than its own block diagram shows, and it is not
self-consistent (its stated minimum, Fsys/(2 × 8 × 256), counts only one). The
÷2 reading is the one the rest of the chip allows — see the PLL below; the
÷4 reading needs a 45.16 MHz crystal and a 367 MHz core, above the part's
200 MHz. **And the frame arithmetic does not depend on which**: the DSP's
instruction count per sample is fixed by the PLL alone.

**3. The core clock.** ✅ Neither payload, nor the 50-word bootstrap, writes
`PCTL` (`X:$FFFF7D` on this part — on the shared peripheral bus, not a
`<<` short address; grep `ffff7d` over both listings and `o8_blob0.dis`: no
hit). So the PLL keeps its reset value, RM §7.3.3.2: **`0x2B60C2` when
PINIT = 1** — `R = 11` (NR = 12), `OD = 1` (NO = 2), `F = 0xC2` (NF = 195),
`DF = 0`, PEN = 1:

    Fsys = EXTAL × NF / (NR × NO) = EXTAL × 195 / 24 = EXTAL × 8.125

(PINIT = 0 would be bypass, Fsys = EXTAL: ~512 instructions per sample, and
the burn probe has measured over 3,000 — so PINIT = 1.) Divide the two:

    Fsys / fs = 512 × 8.125 = **4160 instructions per sample, exactly**,
    520 per ESAI slot — whatever the crystal is.

With the ÷2 reading and fs = 44.1 kHz the crystal is **22.5792 MHz = 512 fs**
(the common audio crystal; `Fref` = 1.88 MHz sits just under the RM's 2 MHz
floor, and `Fvco` = 367 MHz inside 200–400) and **Fsys = 183.456 MHz**. 🟡
Inferred — no one has photographed the crystal or measured the SCKT pin;
either would settle it. What it would take to falsify 4160 itself: a
`PCTL` write we have not found (none exists in the uploaded code; a host
command could not reach it, the payloads have no such handler), or PINIT = 0.

❌ **4535 (200 MIPS ÷ 44.1 kHz) was the datasheet's ceiling, not this
board's clock.** `CHIP.md` and `PLAN.md` carry the 🟡 4160 beside it;
`DspPair` now defaults to 4160 per sample (ratio 4160/3990 in the boot) and
520 instructions per ESAI slot. Nothing measured on hardware changes — the
burn-probe ceilings were counted in instructions, and 4160 sits above every
one of them (`CHIP.md` §2: 3,120 static floor) with 1,040 for the stock
dispatcher instead of 1,415.

**The gate** — the same O6 run (`DSP=1 scripts/o6_gate.sh`, 400 frames) with
`--block-log`, and the falsifier is "bank B is taken": ✅ **Passed, and the falsifier turned up the next thing.** Same O6 run,
`--block-log`, cores live: **400 frames, 28 ticks, trig at 344 — 5 compared
fields agree**, M6a 8 compared fields agree, `ctest` 7/7, `make check` green,
and the landing addresses now split **bank A 1,193 / bank B 1,207** of 2,400
outbound blocks (`landed@2078/2318/25f8/2838` beside `4078/4318/45f8/4838`),
where every one of the 2,400 before was bank A. ESAI frames in = out
(1,870,163 / 1,870,161; before the DMA fix out fell behind in and the
transmitter died), and the read-back carries **196 non-zero words** where it
carried none.

❌ **But the new report line reads `ESAI frames per host frame (0x8c to
0x8c): min 160 max 194, exactly 16 on 0 of 399`** — not 16. The ring makes
5.5 passes between host frames, so the bank alternation is a *random* phase
against the frame clock, not the locked double buffer the dispatcher
expects. The DSP's clock is the firmware's; what is stretched is the PORT's
host frame period: the frame interrupt is a latch (`Rtos::tickTimers`,
"remembers ONE edge"), so a handler that outlives its 16 samples coalesces
the missed frames, and the handler spends its time inside the eDMA drain
gate (`65,327` gated waits over 400 frames; the same order before the
pacing fix, when it read 22 ESAI frames per host frame — the stretch was
there all along and no parameter gate can see it). Where the samples go —
the DSP's own per-frame work, the vendored HDI08's one-word-per-exec drain,
or the port's idle stepping — is the next measurement (stamped block log:
`kicked@`/`done@`/`at@` in samples). 🟡 Until it reads 16, no audio the port
produces has the chip's timing.

✅ **Measured, same day (stamped block log, `kicked@`/`done@` in samples):**
each of the frame handler's SIX serial host-port bursts is held to the *next
16-sample boundary* by route A's completion rule, so a frame costs **80–96
samples** (frame 200: pulls at 914840.6, bursts done at 914856.6, 914872.6,
914888.6, 914904.6, 914904.6, 914920.6, next frame at 914936.6). The 160–194
was that period counted on BOTH ESAI ports; the instrument now counts the
X-side port only. Not the DSP's clock, and not the drain — the drain finishes
inside a sample; the boundary rule holds it. `--dsp-drain-paced` (complete
when the DSP has drained the burst) ran ONE frame at **exactly 16** ESAI
frames per host frame and then the firmware's completion ISR lost an edge and
the port stalled at frame 2 with source 1 asserting untaken — route A's
"at once" symptom, reproduced with the cores. So the boundary rule stays the
default (the O6 gate passes with it, 5/5, and without the cores 5/5) and the
chip's own number is the open item: **a burst takes the FlexBus's cycle time
× its words** — `CSCR2 = 0x180` for the DSP window (`ARCHITECTURE.md` §6) and
the FlexBus clock give it, and 2,176 words per frame against 16 samples says
it is not small. Derive that, book each burst's completion at kick + words ×
t_cycle behind the gate, and the instrument should read 16 on 399 of 399.

### ✅ A ninth vendored-emulator defect, found by the falsifier: the receive DMA's ring never reloaded (8 Sep 2026)

The first run at the corrected pacing did not alternate banks — it took **zero
frames**: the frame handler's first 672-word push was never drained, the
read-back pulls came back "not in time", and core 0 sat at `P:0x97` (the
`HTDE` wait) with **`TCR = 000000` and DMA2 finished** (`DCR2 4c6220`,
`DSR2 8000`) long before the first `0x8c`. The transmitter was dead, so the
audio ring never moved and the dispatcher's bank wait could not end.

Nothing in either payload writes `TCR` after setup (grep `M_TCR` over both
listings: the two setup `movep`s only), and the vendored `Esai::reset` runs
only from the constructor. A `LOG` with the PC in
`writeTransmitControlRegister` said: **`Write ESAI TCR 000000 at pc 000097`**
— the idle loop, so not an instruction; a DMA. The widened `--dsp-trace`
(every channel's DSR/DDR/DCO/DCR) showed **`DDR3` wandering over the whole
24-bit space with `DCO3 = 0`**: `4f186a, ac3264, 0947e0, 665d5c, …` — the
ESAI-in ring's destination, which should cycle X:0x8100–0x833f.

The cause is in `DmaChannel::dualModeIncrement`: on the word that ends the
block it adds `DOR` and returns "finished" **without reloading `DCOL`/`DCOH`**.
For a channel the DSP re-arms itself (DMA2, mode 001, DE cleared) the arming
reloads them; for the firmware's ESAI-in channel — DMA3, `DCR3 = ac59c0`
(mode 101: line, DE **not** cleared), `DCO3 = 0x23f`, `DOR3 = −575` — nothing
re-arms, so after its first pass every received word added −575: the pointer
wrapped below zero into the peripheral space, sprayed silent samples across X
memory at a 575-word stride, and after **1,215,032 words** landed on
`X:$FFFFB5` = `TCR`. ✅ The arithmetic reproduces the observation: DMA3 moves
two words per RX frame here (trace: ΔDDR3 = −575 × 2 × Δframes mod 2²⁴), so
the hit falls at ~160,800 RX frames; the trace has it between 160,000 and
160,966. The firmware's own `DOR3 = −DCO3` is the constant that only makes
sense one way: a ring, i.e. **the counters reload at the end of the block**,
which is what `tools/patches/dsp56300.patch` now does.

⚠️ **This was live in every O8 run**, spraying zeros through X memory at −575
per received sample — including the shared window and the parameter banks —
and the parameter gates could not see it (they compare the ColdFire's
sequencer, and the DSP never returned anything but silence). At the old
pacing the walk was eight times slower and the transmitter happened to
survive to frame 400; at the right pacing it died during the project load.
Same family as the seven before it: found by an instrument, not by reading.

### ✅ O8b — the host-port burst time is the FlexBus's own, and the frame is 16 samples again (8 Sep 2026)

The open item above ("the port's host frame is 80–96 samples") is closed, and
the number came out of the firmware's own constants rather than a knob.

**What the chip is programmed to do.** Two register words decide it, both read
off the image with `scripts/disasm.sh`:

| where | what | reading |
|---|---|---|
| `0x400e165c` (and three identical sites) | `PCR = 0x16777731` | PFDR 22, OUTDIV1 1, OUTDIV2 3, OUTDIV3 7 |
| `0x40001eee`, between a CSMR2 disable and re-enable, immediately before the ICR reset that starts an upload | `CSCR2 = 0x180` | **WS = 0**, AA = 1, PS = 1x (16-bit) |
| `0x400e0e0a`, the boot's own chip-select init | `CSCR2 = 0x1180` | **WS = 4** — the value the loader overwrites |

`CSAR2 = 0x20000000` at `0x400e0dfe` is what makes CS2 the DSP window, so
these are the DSP's own bus terms and nothing else's.

The clock tree they sit in is in `CHIP.md` §1 — crystal 24 MHz, VCO 528, CPU
264, internal bus 132, **FlexBus 66 MHz** — and the load-bearing step is that
the firmware's stored 264,000,000 is the CPU clock, which the UART's baud
setup proves by shifting it right one before dividing (a ColdFire UART divides
the internal bus clock). Read as the VCO instead, every figure below halves.

**The burst time.** RM Figures 20-16 and 20-18: a no-wait-state FlexBus
transfer is S0–S1–S2–S3, **four FB_CLK cycles**, and each wait state repeats
S1 once more. One DSP word is one 16-bit bus cycle (O8, above), so

    one word = 4 / 66 MHz = 60.6 ns = 2.673e-3 samples

✅ **And the constants only make sense one way** — the test this project keeps
coming back to. The frame exchange moves **2,944 words** (2,176 out, 768 back,
measured), which is **178 µs against the 363 µs frame period: 49% bus
occupancy**, half the frame for audio and half for everything else. At the
BOOT's `WS = 4` the same exchange is 357 µs — **98% of the frame**, which
cannot work. That is why the firmware reprograms the chip select before it
ever speaks to a DSP, and it is why the wait states go to zero on a port that
had four.

**What changed in the model.** `Edma::start` books a host-port burst at
`kick + words × 2.673e-3 samples` instead of at the next 16-sample boundary,
still behind the drain gate, so a burst takes **max(bus, DSP)**.
`--dsp-drain-paced` keeps the pure-drain rule for A/B, and without the cores
route A's boundary rule is untouched (its own gate still passes 5/5).

⚠️ **The honest reading of why that fixed it**: what was wrong was the
QUANTISATION, not the magnitude. The drain gate binds more often than the bus
does (1,014,303 gated waits against 65,327 under the boundary rule), so the
period is usually the DSP's drain — but the drain resolves to a fraction of a
sample where the boundary rounded every one of six serial bursts up to a whole
frame.

**The gate.**

| | boundary rule | bus time |
|---|---|---|
| ESAI frames per host frame | 160–194, **16 on 0 of 399** | **16–17, 16 on 382 of 399** |
| frames run / ticks / trig | 400 / 28 / 344 | 400 / 28 / 344 |
| oracle diff (O6, cores live) | 5/5 agree | **5/5 agree** |
| blocks landing in bank A / B | 1,193 / 1,207 | 1,550 / 850 |
| outbound non-zero words | 40,949 | 54,087 |

M6a with the cores 8/8, `ctest` 7/7, and the O6 gate without the cores
unchanged at 5/5.

🟡 **The residual: 17 host frames of 399 take 17 ESAI frames, a 0.27% drift**
(one every 23.5 frames, never 15, so the DSP's clock runs slightly fast rather
than jittering). ✅ **What it is not**: `--dsp-no-idle` reproduces the figure
EXACTLY (16 on 382 of 399, min 16 max 17), so the DSP's idle fast-forward is
ruled out; the host frame interval is **exactly 16.000 samples on all 399**
(measured off the block log's `kicked@` stamps), so the frame clock is not
drifting; and an X-side ESAI transmit frame measures **4160.3 DSP
instructions**, one sample, over the load. So both clocks are right and it is
their PHASE that walks, one sample every 23.5 frames, always in the same
direction. 🟡 The candidate — not yet tested — is that the read-back pull runs
a core OUTSIDE the sample budget (`runCoreUntil`, up to 200,000 instructions
until the DSP puts a word in HOTX), which advances the DSP's audio clock
during a pull; the falsifier is to bound the pull to the budget, or to count
ESAI frames inside pulls and see whether they account for the 17. **O9 is
where this has to be settled** — an audio path resamples by exactly this
error, and 0.27% is about five cents of pitch.

⚠️ **Not fixed by any of this, and not a regression: the read-back is still
silence.** Inbound non-zero words moved 196 → 9 with the new pacing, which is
a different phase of the same nothing — the DSP has no audio in. That is O9's
ground, unchanged.

### What step 5 needs

`verify_twocore` drives the effect ABI directly (`r0`/`r6`/`r7`/`n7` and a
`proc` call); the firmware drives whole frames through the packer at
`0x4000d3fc`, and with the mover those frames now reach the DSP's X:0x6080
records. Making the two render the same audio means (a) audio IN: the ESAI
receives silence here — the ColdFire's own audio path (the sample pool → the
DSP) is the eDMA/ESAI-in side, untraced; (b) audio OUT: the ESAI transmit
frames are counted, not kept; (c) a comparison of the firmware's per-track
records against the knob values the harness passes by hand. That is O9's
ground as much as O8's, and it was not started.

### No regression

`ctest` 7/7 (the new `dsp` gate included); `make check` green; the O6 gate
without `--dsp` unchanged (5 compared fields agree, the trig at frame 344);
the M6a oracle diff without `--dsp` 8 compared fields agree. The models are
seeded from the same 8,235 boot writes as before — a write the co-processor
owns is not replayed into them.

## Milestone O9 — the audio path: input proven, output silent, the clock walk closed (8 Sep 2026, branch `coldfire-o9`)

O9 was "the ESAI path, untraced". It is traced now, in both directions, with
instruments that stay in the tree; half of it works and the other half stops
at a place that is not the port's.

### The instruments

| flag | what |
|---|---|
| `--audio-out PREFIX` | every X-side ESAI TX0 frame a core puts out, eight slots, to `PREFIX_core<k>.wav` (24-bit, 44.1 kHz), with the transport start's frame index in the report |
| `--audio-in FILE.wav` / `--audio-in tones` | RX0's slots from the transport start on: the file's channels onto slots 0..n−1, or slot k = a sine at 500·(k+1) Hz, −20 dBFS |
| `--dsp-map FILE` | at every frame command, the count of non-zero words per 4K chunk of both cores' X and Y — where anything LIVES |
| `--dsp-writes FILE` | at every frame command, the count of NON-ZERO WRITES per 256-word region of both cores' X and Y since the last one — where anything PASSES THROUGH. Needs the tenth vendored patch: a write hook in `Memory::dspWrite` (one branch per write, unset by default) |
| report lines | `audio (O9)`: transport start frame, TX0/RX0 non-zero per slot, the DSP's own instruction counter and its surplus over interpreter calls; `shared window ... non-zero words now`; `non-zero writes into the ESAI-out ring` with the first one's address and value |
| `stage_card.py --audio SRC:CARDPATH` | a sample on the card (route A's own `--stage-audio`, exposed) |

⚠️ Two instrument corrections found on the way: `--dsp-peek` (and the map)
read the shared window through `Memory::get`, which answers 0 for any offset
past the core's own size BEFORE it looks at the window — so a peek at 0x30000+
was blind and read as "empty"; it reads the pair's array now. And the first
activity maps were taken at the frame command, where the window is always
zero (below) — a snapshot instrument cannot see a buffer that is consumed
inside the frame; the write map can.

### ✅ The clock walk of O8b is closed: `rep` iterations

O8b left 17 host frames of 399 taking 17 ESAI frames, a 0.27 % drift "always
one way". The cause was the budget's unit: `runDue` counted ONE per
interpreter call, while the ESAI clock reads the DSP's own instruction
counter, which a `rep` advances once per ITERATION (`DSP::rep_exec`). The
payload's `rep`s put the DSP's audio clock ahead of the ColdFire's sample
clock by the iteration surplus. The budget now spends the DSP's own counter
delta per call (idle steps included), and the report prints the surplus:

| | before | after |
|---|---|---|
| ESAI frames per host frame, exactly 16 | 382 of 399 | **399 of 399**, and **1599 of 1599** |
| surplus over interpreter calls, 400 frames | — | 83,398 = 208 per frame = 0.31 % of 66,560 |

The candidate O8b named (the read-back pull running outside the budget) was
wrong: the pull's instructions were already counted. ✅ Measured; the number
that only makes sense one way is the surplus per frame matching the drift.

### ✅ Audio IN works, end to end

With `--audio-in tones`, over 400 frames (the RIG fixture, T1 THRU trigged at
frame 344 by the poke):

- the ESAI-in ring X:0x8100–0x833f holds the sines (`--dsp-peek 0:X:8100`),
  DMA3 writing ~128 non-zero words per frame (the write map's `0X08200/0X08300`);
- core 0 copies 64 input words into the shared window each frame (`0X30000`
  non-zero writes 72 with tones, 8 without) and core 1 takes them (its
  `1Y00200` 66 vs 1); the frame command sees the window at zero every time,
  so the traffic is transient — written and consumed inside the frame;
- **the ColdFire gets the inputs back**: eDMA channel 7's 128-word block per
  frame (a ring of buffers `0x80005460..0x80005e60`, page-stepped) reads
  126–128 non-zero words = 8 slots × 16 samples, **50,298 words over 400
  frames against 9 without tones**. That is the input-capture staging Bryan
  inferred from count and shape (`EXTERNAL.md` §8) — measured by content now
  for channel 7; channel 6's fixed block at `0x80005e60` stayed zero and is
  still 🟡.

So O8's "the DSP computes silence" on the inbound direction was the absence of
input, not a defect: feed the ESAI and the read-back carries it.

### The frame's audio topology, measured from the block log

Per host frame, from the log's directions, sizes and addresses (✅), with
what each block carries (🟡 unless said):

| direction | eDMA ch | words | ColdFire side | carries |
|---|---|---|---|---|
| → DSP | 0 | 672 | `0x800021d0`/`0x80002c50` (A), `0x80001c90`/`0x80002710` (B), ping | 8 × 84-word track records (`DSP.md`); ~14 non-zero |
| → DSP | 0 | 64 | `0x80005460 + n·0x80`, rotating | a slot record (`DSP.md`); ~11 non-zero |
| → DSP | 0 | 128 | `0x80000110`/`0x80000310` (B), `0x80000210`/`0x80000410` (A) | per-voice records; ~30 non-zero |
| → DSP, **core 0 only** | 0 | 512 | `0x80003190`/`0x80003590`, ping | ✅ the two cores' 256-word read-backs of the previous ping, forwarded; **zero in every run** |
| ← DSP, each core | 1 | 256 | core 1 → `0x80003190`/`0x80003590`, core 0 → `0x80003390`/`0x80003790` | 🟡 the core's track output mix; **zero in every run** |
| ← DSP, core 0 | 6 | 128 | `0x80005e60`, fixed | unknown; zero |
| ← DSP, core 0 | 7 | 128 | `0x80005460..0x80005e60` ring | ✅ the eight input slots × 16 samples |

❌ `DSP.md`'s table has `0x80003190` as "read-back (DSP → CPU), 256 words":
the address is right, the direction is half the story — core 1's read-back
lands there and the ColdFire then sends 512 words FROM it to core 0. The
reading that fits (🟡): core 0 owns the ESAI, so tracks 1–4's mix goes core 1
→ ColdFire → core 0 to be summed into the output ring.

### ❌ Audio OUT is silent, and the place it stops is not the port's

TX0 is zero on all eight slots in every run, and the write hook says why in
the narrowest possible terms: **core 0 never writes a non-zero word into the
ESAI-out ring X:0x8000–0x80ff** during the run (186 writes at boot, the
payload's init; none after). Tried, all silent, all with the sequencer
running and the poked trig firing at frame 344:

| fixture | what it would have shown |
|---|---|
| the RIG as staged (T1 THRU, master track on), tones in | a THRU track passing the inputs, tracks 1–4 → core 1 → ColdFire → core 0 |
| the same, `MASTER_TRACK=0` | the master track was the gate |
| T5 THRU with a trig in A01 step 2, tones in, master on and off | a THRU on the ESAI core itself, no inter-core hop |
| T1 FLEX on slot 1 with `KICK.WAV` staged (`stage_card.py --audio`), `TSMODE=0` | the ColdFire rendering a sample into the 512-word block — ❌ the WRONG block (it is the forwarded read-backs); the sample goes into the track's 84-word record and, with the main level posted, it renders sample-exact (O10) |

What the instruments say about where it stops:

- the sample IS loaded: the card log shows 47 READ commands covering all 176
  sectors of `KICK.WAV` during the load, and the load does more work (forces
  6,488 vs 6,424) — but the 512-word outbound block and both 256-word
  read-backs stay zero before and after the trig;
- the trig reaches the DSP as ONE changed word in core 1's 128-word voice
  block (30 → 31 non-zero) and nothing follows on core 1 — no region of
  track size is written after frame 344 (`--dsp-writes`, frames 343/346/351/399
  compared) and core 1 never writes the shared window;
- the T5 file trig (A01, TRAC mask 0, step 2 — set after finding `pattern-trig`'s
  pattern index is 0-based, so the first attempt landed in A02) does not fire
  at all: the live-nibble log shows only the poked T1 trig. Whether file trigs
  fire under the emulators is untested beyond this (🟡);
- **route A does the same on the same card** (the O6 oracle on the KICK card:
  the same seven live-nibble writes, nothing at `0x80004f1c`), so this is not a
  port/oracle disagreement — it is a path neither emulator drives: the
  sequencer's trig never becomes a DSP voice. `FW_TRIG_WORDS` (`0x46104d26`)
  is zero in every plain-trig run, as it has been since M5 (`RTOS_FORK.md`
  §10.14's control shows the recorder masks reaching it).

Reading the dispatcher's output stage (P:0x1cb–0x203: per output channel,
16 squared samples, a peak, a one-pole envelope and a gain that ramps toward
0 or 0x80 on a threshold compare) says the ring is written by a
limiter-shaped stage — but that is reading, and the write hook says the
stage's input is zero, so nothing about it has been measured. Do not start
there.

**Gate for the remaining half (O9b), not passing:** a THRU track trigged
with `--audio-in tones` puts the tones on TX0 (the WAV carries them, the
ring's non-zero write count is non-zero). It waits on the trig → voice path
on the ColdFire, which is oracle-side work (route A first, `RTOS_FORK.md`
§10), not the port's.

### What the port can do NOW that it could not before

The recorder records the INPUTS, and the inputs now carry content all the way
into the ColdFire's capture buffers. Bryan's click question (the seam-patch
falsification, 8 Sep) asked for exactly "content injected at the source, the
packed pool blocks and the outbound flex stream read across the arm" — the
port can inject it at the ESAI, which is the true source, with the recorder
fixture (`~/octa/backups/RECTRIG_20260906_step9`). Playing the recording back
still needs the voice path above.

### Cost

A 400-frame sequencer run with the cores is **~1 minute wall** (51–61 s
measured, 1,600 frames in 56 s; the load dominates). The "~25 minutes" in the
O8 notes is stale.

### No regression

`ctest` 7/7; O6 with the cores 5/5 against the stored oracle; M6a with the
cores; `make check` — see the PR.

## Milestone O9b — the trig → voice path: audio out of the DSP (8 Sep 2026, branch `coldfire-o9b`)

O9 ended with "no track ever starts under either emulator". It did start; four
things between the trig and the ESAI were wrong, none of them the trig path,
and each was found by an instrument added for it. **Gate: a THRU track trigged
with `--audio-in tones` puts audio on TX0 — passes.** 400 frames, 28 ticks,
the O6 oracle's 5 fields agree, TX0 slots 1–4 carry the mix on 6,213 of 6,399
frames after the transport start (two stereo pairs, the second 3.7 dB lower),
no fault, no stall.

### 1. Coverage said the trig starts a voice; the voice rendered at gain zero

`--coverage FILE` (every ColdFire PC from the transport start, with counts;
`Machine::step` now counts instructions, which the PC watch's timestamps
needed too) and a diff of a trig run against a no-trig run: the trig's own
footprint is 124 PCs (the p-lock applier `0x4000c42c–0x4000c5a0`, an
armed-bitmask check at `0x4000bd14`, a compare, a counter), and one more
activity per frame from then on — a per-voice EMAC mixer at `0x40004444` that
sums four input-capture streams into stereo, 16 samples a call, with gains
from a voice record at `0x80000510 + 384·ping + 48·track` (byte 0 = routing
mode, word +2 = level). Its jump table took case 0 — all gains cleared — for
every voice because the record's level read 0.

The level is written mode:level by the frame builder (`0x4000cb2e`),
smoothed by an EMAC chain at MACSR `0xb0` (`0x4000cbfc`), then scaled by a
**main gain table at `0x80003c60`** in a second chain at MACSR `0x60`
(`0x4000ccae–0x4000ccfc`). ✅ That table is written by exactly two sys
commands — **command 4 = SET MAIN LEVEL** (`msg[1]` = 0..127, handler case
`0x40061e0a`, ten longwords of gain:(0x8000−gain) from the curve at
`0x400bcd90`) and command 68 (`127 − msg[3]`) — and NEITHER emulator's load
posts either: 0 writes to the table in the port and in route A. Every voice
was multiplied by zero. `--main-level N` posts command 4 after the load
(`Rtos::setMainLevelLive`, the same scratch-message path as select-bank); the
report prints the table's first entry (`0xbf7fc081` at 64). Route A got the
same post the same day (the parallel session's `set_main_level_live`).

### 2. MACSR S/U is bit 6, and in fractional mode it is not signed/unsigned at all

With the table filled the second chain still stored 0. Its inputs were right
(record `0x0100:0x7f00`, gain `0xbf7f:0xc081`); the port's `movclrl` returned
the products left-aligned and the firmware keeps the LOW words (`movclrl
acc0,d3; swap d3; movclrl acc1,d2; movew d2,d3`). ❌ The port had S/U as bit 4
(that is R/T) and read every accumulator out as `ACC[39:8]`. ✅ The CFPRM
(Rev. 3, ch. 6, MOVCLR pseudocode; `MOVE from ACC` says the same) for
`F/I = 1`:

    OMC,S/U,R/T == 000  → ACC[39:8] → Rx
    OMC,S/U,R/T == 001  → ACC[39:8] rounded by [7:0] → Rx
    OMC,S/U     == 01   → 0 → Rx[31:16]; ACC[39:24] rounded by [23:0] → Rx[15:0]

So at `0x60` the chip hands the 16-bit-rounded top of the accumulator back in
the LOW word, which is precisely what the chain keeps. `v4e.cpp`'s `accRead`
is now the pseudocode, every branch (integer signed/unsigned with OMC
saturation; fractional with OMC, S/U and R/T), and `g_macsrSigned = 0x40`.
QEMU (route A) has the S/U branch and `MACSR_SU = 0x40` already. `ctest`
7/7 before and after — the O2 self-test never exercised S/U. **The number
that only makes sense one way:** the record's mode byte survives the scaling
(`0x0100 × 1.0 → 0x0100` in the low word) only under this read-out; under
`ACC[39:8]` it is destroyed, which is why both chains cannot be satisfied by
any operand alignment (an experiment that was tried and reverted).

With that: `0x80000510` reads `0x01007f00` after the chain, outbound non-zero
words 43,222 → 177,690 per 400 frames, inbound 50,298 → 129,619, **the
ESAI-out ring gets non-zero writes and TX0 slots 1–4 carry the THRU tracks'
tones** — for 348 frames, then the firmware executed `halt`.

### 3. The frame interrupt is the DSP's bank word, not a timer

`0x4000ab40: halt` sits under `cmpl 0x800000e0,%d0; bcc`: the frame handler
reads the DSP's bank id from `0x2000001c` **with no ready check**
(`0x4000aafa`) and halts unless it is 0 or 1. Under silence the read-back
data words were 0 and a mistimed read passed by luck; with audio a data word
landed there (`0xffffd500`). So on hardware the interrupt that runs the
handler must be what announces the bank word. The port now raises the frame
edge when core 0 executes its bank write — payload A's `000073: movep
r3,x:<<M_HOTX`, once per ring half, `g_bankIdPc` — instead of the
free-running 16-sample timer (`--frame-timer` restores it). The DSP's first
bank id, written during the load and waiting at P:0x97 for the host, is
delivered where route A fires its first frame (one period after the frame
clock comes on) so the transport start keeps the oracle's phase; a ColdFire
idle skip ends at the edge (`tickSamples` returns the samples it advanced).
Measured: bank write → host take 0.002 samples, bank write → `0x8c` 0.004
samples, DSP frame-to-frame 16.00 (min 15.27, max 16.95). "ESAI frames per
host frame" now reads 16 on 395 of 399 with min 14 / max 17 — that is the
ColdFire's own latency jitter on the command, visible now that the edge is
the DSP's; the DSP's frames themselves are exact.

### 4. A modulo pre-decrement that left the buffer — the eleventh vendored defect

Then the DSP stalled 13–38 frames after the trig: core 0 at P:0xa3 waiting on
core 1's mailbox for a whole ring half, DMA2 finished un-re-armed (the
dispatcher only re-arms after catching `DSR2 == 0x8070/0x80f0`, a one-word
window), core 1 in a 196,608-iteration voice loop (`do y1,>$20b` at P:0x205
with `y1 = 0x030000` from the record's count field at Y:0x42). Instruments on
the way: `--dsp-writes`' per-word watch (`--dsp-watch core:space:addr`: the
last 16 writers with PC, the executed-PC ring, r0/r4/r6 and the area), a DSP
PC watch with a register dump (`--dsp-pcwatch core:pc[:fromExecuted]`), a
trace window (`--dsp-trace-from`), the interrupt-vector histogram in the
report, and a trap for the first `TCSR0.TE` with the PCs and address registers
before it. The chain, all measured:

- core 1 took **vector 0x54 = TIMER0 Compare 230,027 times**; neither payload
  writes a timer register. Payload B's word at P:0x54 is `move x0,y:(r4)+` and
  a fast interrupt runs the two words at its vector inline — with whatever r4
  the voice builder had, so the count field got a raw host word;
- TCSR0 was written with `0x0c2687` (an audio sample) by the interpolator's
  `move b,x:(r2)+` at P:0x1a78 with `r2 = 0xffff8f`;
- r2 came from `x:-(r2)` at P:0x1a66 with **r2 = 0 and m2 = 0x3f**: the
  vendored AGU (`agu.h updateAddressRegister`) holds r unsigned, the
  decrement underflowed to `0xffffffff`, the lower-bound test could not see
  it and the upper-bound test subtracted the modulo: **`0xffffbf` where the
  chip gives `0x3f`**. The next sixteen writes walked `0xffffc0–0xffffff` and
  the timer block.

Fixed in `agu.h` (the update is done relative to the buffer base in signed
arithmetic), `tools/patches/dsp56300.patch` regenerated. ⚠️ `dsp_host` renders every
effect on this AGU and a `x:-(rN)` at the base of a modulo delay line is an
ordinary idiom, so the shipped effects were audited directly: `make check`'s
bit-identity gates cannot see a change common to both sides of a comparison,
but `send_probe --layout RS --wav` rendered with and without the fix is
**byte-identical** (529,244 bytes, `cmp`), and its peak/THD/spur lines match.
✅ No shipped effect was rendered on the underflow path.

Also in this milestone: the two cores are interleaved in 64-instruction
quanta (`stepCore`; `runCoreUntil` steps core 1 alongside core 0 inside a
pull) — hardware runs them in parallel, and a whole budget slice each in turn
could hold core 0's mailbox wait past the ring window. The stall's root cause
was the AGU, not this, but the interleaving stays as the nearer model.

### What the port can do now

`--dsp --main-level 64 --audio-in FILE|tones --audio-out PREFIX` renders the
firmware's own mix of the inputs through the DSP to a WAV. Step 5 of O8 (the
firmware driving `verify_twocore`'s layouts) is reachable: audio in, the
sequencer, the per-track records and the ESAI out all carry content. Open:
which input slot is which physical input and which output slot is main/cue
(a per-slot spectral pass or eight single-tone runs); the FLEX sample path
(a staged `KICK.WAV` is read from the card but the 512-word block stays zero
— the sample loader / voice start for FLEX is the next locate, with
`--coverage` and the block log as the instruments); the `0x8c` jitter.

## Milestone O9c — the slot map (started 8 Sep 2026, branch `coldfire-o9c`)

Eight runs, each with a 1 kHz tone at −20 dBFS on ONE of the eight RX0 slots
(`--audio-in out/o9c/tone_slotN.wav`, the RIG project, `--main-level 64`,
poked trig at step 2), reading TX0 per slot and the DSP's capture staging
block (`--dsp-peek 0:X:4700,128;0:X:2700,128`, the 128 words eDMA ch 7 takes
back each frame) at the end. ✅ Measured:

| tone on RX0 slot | lands in the capture block at | TX0 slot 1 / 2 / 3 / 4 |
|---|---|---|
| 0 | words 0,1 (+8k): pair at +0, L | −78 / — / −82 / — dBFS |
| 1 | words 2,3 (+8k): pair at +0, R | — / −78 / — / −82 |
| 2 | words 0,1 (+8k): pair at +0, L | **−35** / — / −39 / — |
| 3 | words 2,3 (+8k): pair at +0, R | — / **−35** / — / −39 |
| 4–7 | nowhere: the ESAI-in ring holds four words per sample (slots 0–3) | silence |

So: **TX0 slots 1/2 are one stereo pair and 3/4 a second, 3.7 dB lower**
(main and cue is the natural reading, 🟡 unmeasured which is which); the
capture block's pair at +0 — C/D in the ColdFire's own reading
(`RTOS_FORK.md` §10.18, "A/B at +0x80, C/D at +0") — is fed by RX0 slots 2
and 3 at full level, and **the +0x80 pair (A/B) is never written by the port's
DSP**; RX0 slots 0 and 1 reach the same +0 positions and the same outputs
**43 dB down**, which is not a THRU gain anyone would set. 🟡 Two readings,
neither measured: the A/B copy uses a gain that another never-posted sys
command sets (the family the main level belongs to — the project's `DIR_AB`
is 0, and the input-gain/direct paths are all ColdFire state), or the port's
ESAI delivers the pairs to the wrong half of the DSP's input stage. The
falsifier is a write watch on the +0x80 words of the staging block and a
`--coverage` diff between a slot-0 run and a slot-2 run: the code that
differs is the A/B path. RX0 slots 4–7 not reaching the ring is 🟡 the
vendored ESAI/DMA (the ring stride is 4 where the payload enables 8 slots);
it costs nothing today because the unit has four inputs.

✅ **Measured next (a write watch on word 64 of the staging block and a PC
watch at the copy's entry, `--dsp-pcwatch 0:55e`):** the +0x80 pair IS written
every frame, with zeros, by the copy at P:0x55a called three times a frame
from P:0x2df/0x2e2/0x2e6 — and its source for the +0x80 half is **the
ESAI-OUT ring, `r1 = 0x8080` with `n1 = 7`**: the pair is TX0 slots 0 and 7
of each output sample, which nothing in the port writes (the mix goes to TX0
slots 1–4). The other two calls read the ESAI-in ring (`r1 = 0x8280` and
`0x8282`, `n1 = 3`). So "A/B" in the ColdFire's capture block is not a raw
input pair at all on this path: it is whatever the DSP puts on output slots 0
and 7 — 🟡 the direct-monitoring / input-thru placement, which some ColdFire
state the emulated load never sets would switch on (the project's `DIR_AB`
is 0), the same family as the main level. The 43 dB-down leak of RX0 slots
0/1 into the C/D positions is still 🟡. And `RSMA = 0x0f` is the firmware's
own (payload A `P:0x30032`): four receive slots is the chip's configuration,
not the vendored ESAI's.

✅ **The input map, settled by the project's own direct levels.** A scratch
copy of the RIG with `DIR_AB=100` and `DIR_CD=100` (the mixer's direct
monitoring, 0 in the project) staged and run with the same single-tone files:

| tone on RX0 slot | TX0 slot 1 at DIR 0 | at DIR 100 |
|---|---|---|
| 0 | −77.9 dBFS | **−34.9** |
| 2 | −35.1 | −30.0 |

DIR AB moves slot 0 by 43 dB and DIR CD moves slot 2 by 5 dB, so **RX0 slots
0/1 are inputs A/B and 2/3 are C/D**, and the "43 dB down" leak was the direct
path at level 0 (🟡 a floor, or T1's INAB at a low setting in the part). The
direct level does NOT travel in the 64-word track record: its `+0x32` field
is the track LEVEL (the ColdFire writes `0x6c00` = 108 there every frame at
`0x80005492`), and the DSP's copy of that field at X:0x4632 is overwritten
with 0 by the staging copy at P:0x568 each frame — the pointer table at
X:0x202–0x209 is not decoded here and the reading of `x:(r0+0x32)` as a gain
at P:0x2f4 is left 🟡. **TX0 slots 0 and 7 stay silent at DIR 100 too**, so
the capture block's +0x80 pair (the ColdFire's A/B) still has no source in
the port; what places audio on those two output slots is the open item.

❌ **"TX0 slots 1–4" was an artefact, and so was "master track on pans it
right".** With the master track off the same tone came out on slots 0/2
instead of 1/3, yet a `--dsp-peek` of the ESAI-out ring showed it at **ring
words 2 and 4 in both runs**: the ESAI's slot counter and DMA2's ring index
are not in a fixed phase — the rotation between them differed between runs
and moved within a run (0..7 over 400 frames, as DMA2 is re-armed). The
ring is the truth; `--audio-out` now keeps each frame by RING WORD, reading
DSR2 at the frame's end (`(DSR2 − 9) & 7`, minus nine because the DMA has
already loaded the next frame's first word; checked against the peek), and
the report says "non-zero per RING WORD (slot + rotation min..max)". ✅ So,
in ring words per sample: **(2,3) is one stereo pair and (4,5) the other,
3.7 dB lower; 0, 1, 6, 7 are never written by the mix**, and the capture
block's +0x80 pair reads ring words 0 and 7 — which is why the ColdFire's
"A/B" is empty here: nothing in this project's state (DIR at 100, CUE 127
with main-to-cue, master track on or off — all tried) writes those words.
🟡 Which pair is main and which cue, and which physical output ring words
0/1/6/7 reach, is hardware's to say (the codec's slot assignment); the
WAV's channel order is now stable enough to compare against `dsp_host`.

### The THRU path's baseline, measured (the comparison's precondition)

T2 (THRU on C/D, FX2 = SEND, so no effect in the path), the RIG as staged,
main level 64, no trig needed (THRU tracks start at frame 0):

| probe on input C | ring word 2 | ring word 4 |
|---|---|---|
| sines at 60 / 300 / 4,000 / 12,000 Hz, −20 dBFS | −34.1 / −34.1 / −34.3 / −34.2 dBFS: **gain −11.1 dB, flat** | −37.4 (−3.3 dB below) |
| a full-scale kick (`out/test_audio/kick.wav`) | first output sample **155 samples** after the first input sample; peak −17.5 dB below the input; a least-squares scaled-copy fit leaves a residual only 2.3 dB under the output | −3.2 dB below |

So the THRU path is flat and linear at −20 dBFS and a **full-scale input is
limited** (the output stage at P:0x1cb–0x203 is a limiter; the kick loses
6 dB more than the tones and stops fitting a scaled copy) — any comparison
must keep the probe well under full scale. The 155-sample latency is 🟡
unexplained in parts: 128 of it is the input-capture lag the ColdFire side
also sees (`RTOS_FORK.md` §10.18), the rest the DSP's frame pipeline.
T1 (THRU on A/B, FX2 = BusDelay) passes the same kick at **−52 dB** — its
input level in the part is what the "43 dB down" leak was — so the RIG's T1
is not usable as the effect fixture without editing its machine page, which
the project tools do not do yet.

### The comparison, attempted: the effect runs, the knobs do not arrive

A fixture built from the tools: the RIG with **T2's FX2 = FILTER (stock id
0x04)** written into part 1 and its saved copy (`ot_project` internals,
checksum re-read), FILTER's WDTH stamped to 64 on T2 (`stamp-slot ... filter
WDTH 64 --track 2`; ⚠️ called from a shell loop it reported "no knob 'WDTH
64'" — call it plainly), staged and run with the five sines. Measured:

| | 60 / 300 / 1k / 4k / 12k Hz, ring word 2, relative to the FX2 = SEND baseline |
|---|---|
| FX2 FILTER, page all zero | +0.0 / −0.0 / −0.0 / −0.2 / −0.1 dB |
| FX2 FILTER, WDTH stamped 64 | +0.0 / −0.0 / −0.0 / −0.2 / −0.1 dB |

Flat both times. Not because the effect is skipped: a DSP PC watch on core 1
shows FILTER's init (payload B `P:0x591`) entered 3× at the load and its proc
(`P:0x59d`) every frame. ❌ **First reading, retracted: "the page byte never
reaches the DSP".** It does — the WDTH byte I stamped (64) lands in the
per-voice block at **X:0x4000 word 39 = 0x034000** (`0x40 << 8`), the one
word that differs between the WIDTH-0 and WIDTH-64 cards across every landed
block. The response was flat because **the page I stamped was an open filter**
(BASE 0), and WDTH does nothing to a steady sine through an open filter — a
test that could not see the thing (`send_probe` THD, again). ✅ The stamped
byte DOES cross via the ColdFire's page publish (`0x80000ec4/0x80000ecc`,
`DSP.md`; the RAM part page at `0x4017109e` reads `0x7f40007f` = BASE 127,
WDTH 64…). The remaining question is narrower: WDTH landed in the block but
FILTER's proc reads its coefficients from X:0x2c0/0x3a0, which did not change
— i.e. whether a *companion* field is unpacked to the proc's block, not
whether the page crosses.

❌ **RETRACTED 8 Sep 2026 (O9d): this was T2's FX1 path, not FX2's.**
`stamp-slot` stamps every part/track naming the module in FX1 OR FX2, T2's
FX1 is FILTER in every part, and the part that PLAYS had T2's FX2 = SEND
(the fixture had put FILTER only in part 1 — see O9d). X:0x4000 word 39 is
T2's FX1 WDTH (record = 32 words per track, FX1 page at +6, FX2 at +12), so
X:0x2c0 is the FX1 instance. The FX2 page path is proven in O9d, on T1.
~~✅ **Re-measured with BASE (page-1 slot 0, the cutoff): the parameter path
is proven end to end.** Stamping FILTER BASE to 40 on T2's FX2 lands
**0x28 in FILTER's own coefficient block at X:0x2c0 word 7** (the FX2
instance; X:0x3a0 is the FX1 instance, unchanged), which the WDTH-only card
does not have.~~ So a part's page byte travels: part data → the ColdFire's
`0x80000ecc` publish → the per-voice block at X:0x4000 → unpacked into the
effect's parameter block where its proc reads it. The 300 Hz…12 kHz response
is still flat at −34 dB because BASE 40 / WDTH 64 is a transparent band for
these tones (the mode/HP/LP that would attenuate live on page 2, a count-3
select the tools do not stamp yet), not because the parameter is missing.

**Where O9c stands:** the port renders the firmware's mix of real ESAI
inputs through both DSP cores, with each track's FX1/FX2 running and reading
its part's parameters — the machine O8's step 5 needs. The bit-exact
comparison against `dsp_host` is now a bounded job, not an unknown: it needs
(a) a part setting that makes the effect non-transparent, and (b) the gain
structure reconciled. Both are now measured facts, not locates:

- ❌ **RETRACTED 8 Sep 2026 (O9d)** — every measurement in this bullet was
  taken on T2, which in the port has NO INPUT (its FX1 FILTER at BASE 127 /
  WDTH 0 closes the path, exactly as `rig_render` said) and whose FX2 in the
  playing part was SEND, so "identical" was inevitable. Page-2 selects DO
  cross under the load (O9d: FILTER HP/LP on T1, record low byte 0→1, TX0
  changes). ~~**A page-1 FILTER setting cannot make it filter, and the page-2
  select that would does not reach the DSP under the load.**~~ Four page-1 (BASE, WDTH)
  combinations all give the same flat output; FILTER's response is gated by
  its page-2 HP/LP selects. `stamp-slot` DOES write page 2 (my earlier "page 1
  only" was wrong — it computes `P2_OFF + track·30 + 6 + slot−6` and the LP=2
  byte lands in the part), but a card with LP = 24 dB renders **identically**
  to LP = 0, and FILTER's coefficient block at X:0x2c0 is **byte-identical**
  between the two — so **the page-2 select never crosses to the DSP**, where
  a page-1 knob (BASE → X:0x2c0 word 7) does. ✅ **The locate, sharp now:**
  the per-frame packer forwards page-1 knobs positionally (`x:(r6+i)`,
  `DSP.md` §) but not the page-2 selects; on the real unit a page-2 select is
  applied when the effect is (re)selected / the part is applied, a path the
  emulated load does not run — the SAME family as the main level (sys command
  4) and the −1 per-track init bytes. So O9c's finish is not a `stamp-slot`
  feature; it is to drive that apply path after the load.

  ✅ **And the apply path is already located — it is the CC→page-2 work.**
  `modules/ccpage2` (PR #98, hardware-confirmed) had to replicate the
  firmware's page-2 editor `P2EDIT` (`0x4003a474`) exactly, and
  `docs/firmware/midi_re_cc.md` §7 has the whole publish path: a page-2 value reaches
  the DSP ONLY through the **live lane `0x80000830 + track*72 + 0x20 + slot2`**,
  which the copier `0x4000cae8` ships every frame — there is no DSP post; the
  Part store and shadow do not reach the DSP by themselves. ✅ Measured here:
  the emulated load leaves that lane byte **zero** even with the part carrying
  LP=2, which is exactly why the select does not cross. ⚠️ A `--poke` of the
  lane at the ccpage2 offset did NOT move stock FILTER's block (equal-length
  compare identical) — because ccpage2's `slot2` layout and counts are the BUS
  ENGINES' (`VERB_COUNTS`/`DLY_COUNTS`), and stock FILTER's page-2 lane offset
  is its own. The mechanism is known for the bus engines; the
  per-effect lane offset for a STOCK effect is not pinned (poking every byte
  of the copier window 0x80000898–0x800008af left FILTER's block unchanged),
  and `--poke` (added this milestone) is the lever once it is.

❌ **RETRACTED 8 Sep 2026 (O9d): a THRU track's TX0 output DOES carry its
FX2.** EQUALIZER on T1's FX2 (the track that actually has input in the port)
changes T1's read-back, the forwarded block and TX0 alike; on T2 nothing
could change because T2 renders nothing. The "reframing" below is the
fixture, not the firmware. ~~**But a bigger reframing, strongly supported: a
THRU track's TX0 output is a PRE-FX2 monitor, so the comparison cannot read
the effect there.**~~ Every
FX2 setting tried — page-1 BASE/WDTH across four combinations, a page-2 LP
select in the part, a live-lane poke — leaves the TX0 ring word 2 output
**flat/identical**, even though FILTER's proc runs every frame (`P:0x59d`)
and page-1 BASE provably reaches its coefficient block (X:0x2c0 word 7 =
0x28 for BASE 40). An effect whose input reaches it and whose output changes
nothing downstream is not in the measured path. So O9c's comparison tap is
wrong, not its parameters: a THRU track monitors its raw input, and FX2's
output goes to the bus / the recording, i.e. the **read-back block
`0x80003190`** (DSP→CPU, `DSP.md` §), not the ESAI monitor. ✅ **Confirmed with a second effect:** EQUALIZER (id 0x0c) on T2's FX2 with
extreme page-1 gains (0,127,0,127,64,127) renders **byte-for-byte the same
TX0 output as EQ with page 1 all zero** — 0.0 dB delta at 300 Hz and 8 kHz.
Two different inserts, extreme settings, no change at TX0: a THRU track's TX0
monitor does not carry FX2 output. The parameter path (page 1 proven, page 2
= the ccpage2 lane) stands; what O9c had wrong was where to listen. 🟡 Next:
the FX2 output goes to the recording / bus path — read the DSP-side read-back
source (`X:0x400` → `0x80003190`) with a recorder armed, or use a machine
that plays into FX2 (the FLEX loader). Both tie O9c's comparison to the
recorder path, i.e. to the same DSP-in-the-loop work Bryan's click needs.

⚠️ **Tool note:** `ot_project.py stamp-slot` crashes on a bare id
(`mod.params[slot]` index error when `mod` resolves but the slot is out of
its manifest range) — the EQ page-1 bytes here were written directly. Worth
a guard before the next fixture round.
- ✅ **The THRU monitor gain is the track's own input level, NOT the main
  level.** Sweeping `--main-level` 0/32/64/100/127 leaves the THRU output at
  −34.5 dB throughout (the gain table[0] goes `0x8000`→`0x80000000` and the
  monitor does not care). So the main gain table O9b had to post scales
  TRIGGED VOICES; a THRU track passes its input at its INAB level (−11.5 dB
  here for T2's part). `dsp_host` (`rig_render`) applies the effect to the
  stem at `--amp` with no mixer, so the comparison divides out a KNOWN
  constant per path — the THRU's INAB gain, not a main-level-dependent one.

`rig_render.py` on the same part (stock image, `--stem T2=`) rendered T2 at
−138 dBFS: its FX1 FILTER at BASE 127 / WIDTH 0 closes the path ~~where the
port's does not — a second disagreement, harness versus firmware-driven,
recorded here and not chased (the port is the one running the ColdFire)~~.
✅ **O9d: the port's does too** — T2's chain output in core 1's read-back is
316 rms against 3.0 M in its input record (−80 dB). The tone O9c heard at
TX0 was T1's. No disagreement.

**So the comparison's precondition is the parameter path, not a fixture.**
Next: watch the ColdFire's page publish for T2's FX2 slots (`--watch-mem`
on `0x80000ec4`/`0x80000ecc`, `--coverage` diff of a stamped against an
unstamped load), find what posts it, post it after the load the way the
main level is posted, then re-run the five sines — the FILTER response
against `dsp_host`'s is the gate.

## Milestone O9d — the comparison fixture was measuring the wrong track (8 Sep 2026, branch `coldfire-o9d`)

O9c ended on "a THRU track's TX0 is a pre-FX2 monitor; the FX2 page-2
select never crosses; the comparison tap must be the recorder". All three
were one fixture defect. What settled it was a new instrument — every
host-port block's CONTENT at the move (`--block-dump FILE`, binary;
`tools/scratch/blockdump.py summary|diff|wav`) — and the rule it enforces:
two runs that differ only in a part byte must differ somewhere on the host
port before any DSP-side "identical" means anything.

### ✅ The O9c EQ pair was byte-identical on the host port

Re-running O9c's EQUALIZER cards (page 1 extreme vs zero on T2's FX2) with
the dump: **every block of every class identical in all 250 frames** —
including the per-voice records that carry the pages. The EQ bytes were in
the card (bank 1 part 1 and its saved copy 5, slots 1/3/4/5) and in the
ColdFire's live lane after the load (`0x80000870` = `00 7f 00 7f 40 7f`,
`0x80000ecc[1] = 0x0c`) and never reached the DSP. Cause, measured with
`--watch-mem` on the id arrays and the lane:

- the LOAD applies **bank 1 part 1** (`0x40009384/0x4000938e` in `engine`
  at 43,401 samples: T1 FX1 = 0x12, T2 FX2 = 0x0c);
- the TRANSPORT START re-applies the **saved bank's pattern part**
  (`0x4000c40e/0x4000c41e` in `main` at 896,464: T1 FX1 = 0x1c, T2 FX2 =
  **0x09 = SEND**), and the load-time refresher `0x4000c19c`
  (`0x4017107a + bank·635712 + part·6322 + track·24` → lane
  `0x80000816 + track·72`, pre-image `0x80000a5c + track·64`) overwrites the
  lane and the pre-image from THAT part.

So every O9c FX2 fixture — FILTER on T2 in "part 1 and its saved copy", the
EQ pair, the page-2 LP card, the lane pokes — ran with T2's FX2 = SEND.
`stamp-slot` writes all eight parts of every bank, which is why its FILTER
BASE/WDTH bytes DID land — in T2's **FX1** (FILTER in every part), i.e.
X:0x4000 word 39 = T2's FX1 WDTH and X:0x2c0 = the FX1 instance.
`ot_project.py set-fx` now writes an id (+ pages) into all eight parts of
every bank for exactly this reason.

### ✅ The frame builder's record, decoded from the dump

Per-voice DSP record (`0x80000110`/`0x80000310` → core 1, `0x80000210`/
`0x80000410` → core 0; 128 halfwords = **32 per track**, one DSP word per
halfword): +0..5 AMP, +6..11 FX1 page 1, +12..17 FX2 page 1 (`value<<8`,
page-2 selects in the LOW byte of the same halfword — the "flag word" of
`DSP.md` §9), +27 = FX1 id, +28 = FX2 id. Assembled by the copier
`0x4000cae8` from the pre-image `0x80000a50 + track·64` (halfwords 12..29)
and the page-2 lane `0x80000830 + track·72`, then slewed (`0x4000cc20`,
MACSR 0xb0) and scene-modulated (`0x4000ccfc`, gain table `0x80003c60`,
scene buffer `0x80000ed4 + track·0x40`). The 672-halfword track records
(`0x80001c90` → core 1, `0x800021d0` → core 0; 4 × 84 words) carry the
**THRU audio**: words 8..38 even = 16 L samples (R zero for a mono input).

### ✅ With the EQ on the track that has input, everything moves

T1 FX2 = EQUALIZER in every part, page 1 `40 127 64 100 0 64` (B) against
`64 ×6` (A), tone on RX0 slot 2 at −20 dBFS:

| block class | A vs B |
|---|---|
| per-voice records → core 1 | differ from frame 2 (the page) |
| core 1 read-back `0x80003190/0x80003590` | differ from frame 9 |
| forwarded 512-word block → core 0 | differ |
| core 0 read-back, ch 6 block | differ |
| TX0 (`--audio-out`) | differs |

Same with FILTER on T1's FX2, page 1 BASE 100 / WDTH 0, **page-2 slots 6/7
= 1 (HP/LP 24 dB) against 0**: the record halfwords differ by exactly 0x0101
(the low byte), read-backs and TX0 differ. **Page 1 and page 2 both cross
under the emulated load; the THRU monitor carries FX2; the comparison tap is
TX0 (or the read-back, per track).** EQ's proc (payload B `P:0x972`, from
the X:0x235 table; init `P:0x96d`) runs every frame on core 1.

### ✅ Why T2 renders nothing, and where each tone lands

Tone on RX0 slot 2 → **T1's** 672-record (rms 3.0 M) → T1's read-back slot
(610 k) → TX0 −38 dBFS. Tone on RX0 slot 0 → **T2's** record (3.0 M) →
T2's read-back **316** → TX0 −102 dBFS: T2's FX1 FILTER (BASE 127 / WDTH 0,
in the part) closes the path, as `rig_render` said. 🟡 Which physical input
each THRU listens to is the part's INAB/INCD selects (`-, A B, A, B, A+B`;
T1's PB page `00 00 40 …`, T2's `00 01 7f …`) and is not decoded here; the
measured fact is slot 2 → T1's chain, slot 0 → T2's chain. The O9c DIR
measurement (RX0 0/1 = A/B on the DSP's direct path) is unaffected.

### What this leaves

The O9c gate (a THRU track's FX rendering bit-identical to `dsp_host`) now
has a working fixture on T1 and a decided tap; the remaining work is the
comparison itself: `dsp_host` with EQUALIZER at the same page on the same
tone, level-matched to the port's T1 read-back (the per-track chain output,
before the master mix), and the THRU gain structure (VOL, INAB, the −11 dB)
divided out.

### ✅ The comparison — O8's step 5 / the O9c gate — PASSES (T1, EQUALIZER, stock image)

T1's chain INPUT is the audio in its 84-word record (16 L samples per
frame, −11.2 dBFS) and its OUTPUT is its slot of core 1's read-back (the
per-track chain output, before the master mix); `tools/scratch/
o9d_compare.py extract|fit` pulls both out of a `--block-dump` and fits
lag, scale and residual. The same input goes through `rig_render` on the
stock image (`--project … --bank 2 --part 1 --stem T1=… --amp K`), which
takes T1's ids and all twelve knobs from the part. Measured, 300 Hz tone,
samples 1500..3900 of 4000:

| T1 | fit | residual |
|---|---|---|
| FX1 = stock 0x1c LO-FI, FX2 = EQ flat / boosted | −13 dB, lag −32 | −16.6 / −16.9 dB, gain stepping ±1.5 dB per period |
| FX1 = SEND, FX2 = EQ flat (`64 ×6`) | **−11.906 dB** (k = 0.253931 = 2130129/2^23), lag −32 | **−125.6 dB** |
| FX1 = SEND, FX2 = EQ boosted (`40 127 64 100 0 64`), stem × k | 0.000 dB, lag −32 | **−95.7 dB with NO fit** (the float pre-scale's rounding) |

Three things fell out on the way, each measured:

- **The parameter words the DSP sees are dsp_host's, word for word.** Core 1's
  per-instance block (`X:0x208` → 32 words per track, T1 at X:0x25d):
  +0..5 AMP `00 7f 7f 40 40 7f`<<16, +6..b FX1 page 1, +c..11 FX2 page 1
  `28 7f 40 64 00 40`<<16, +12..14 FX1 page 2 as `knob<<16 | companion<<8`
  (`7f0000 000000 400000`), +15..17 AMP page 2 (`010100 …`), +18..1a FX2
  page 2 (`003000 400100 000000` = the part's `0 48 64 1 0 0`), +1b/+1c the
  ids (`000900 000c00`). That is exactly `dsp_host`'s `setParams`
  composition (+$c/$d/$e knob|companion) — the DSP's unpack turns the
  record's low-byte packing into it.
- **The track gain is applied BEFORE the FX chain, and it is a constant**:
  k = 2130129/2^23 (−11.906 dB) for this part (AMP VOL 64, LEVEL 108;
  🟡 not derived from those — measured as the fit). `rig_render` at
  `--amp 1.0` drove the EQ 12 dB hotter than the unit does, and the stock
  EQ saturates there: the boost read +4.95 dB in the harness against
  +6.22 dB in the port until the stem was pre-scaled by k, after which the
  two agree to the rounding of the pre-scale.
- **Stock LO-FI (0x1c, the RIG's T1 FX1) is nonlinear**, which is what the
  "stepwise gain" of the first pass was; with FX1 = SEND it is gone.

So the port, driven by the firmware from the card, renders a THRU track's
FX chain as `dsp_host` does, to the bit for a linear page and to −96 dB
with a float pre-scale for a saturating one; the one thing `rig_render`
does not model is the pre-FX track gain k (its docstring's "AMP VOL … not
modelled"), and the comparison must feed it the stem at k. Falsifiers:
another effect (FILTER with page 2 live) or another track/core giving a
residual above −90 dB with the stem at its own k.

Instrument rule added to the port: **a DSP-side "byte-identical" between
two cards is not evidence until the host-port dump shows the cards differed
on the way in.** Three O9c retractions rode on skipping that check.

## Milestone O10 — FLEX playback renders, sample-exact; the recorder loop under the port (8 Sep 2026, branch `coldfire-o10`)

### ✅ "A FLEX voice never renders in either emulator" is retracted for the port

O9 staged `KICK.WAV` on T1's FLEX slot and watched the 512-word block to
core 0 and the read-backs; O9b/O9c repeated "the FLEX loader is unlocated".
Both watched the wrong block, and O9's run had no main level (O9b's root
cause A). The audio of a voice — THRU or FLEX — travels in the track's
84-word record (O9d). Fixture: the RIG with T1 = machine type 1 (FLEX) on
slot 1 in banks 1–2, all parts and mirrors, T1 FX1/FX2 = SEND, the kick
(`scripts/make_test_audio.py kick`, 16-bit mono) staged at slot 1's own
card path, `--poke-trig 2 --main-level 64`, no audio in:

- T1's record carries audio from frame 1 (the pattern's own step-1 trig),
  0 dBFS peak, retriggered at the poked trig (the kick restarts at record
  sample 5,528 = frame 345.5 for a trig at 344); the read-back and TX0 ring
  words 2–5 (both channels, a centred voice) carry it.
- With the slot's `TSMODE=0` the record IS the file: `kick.wav << 8`,
  residual **−100.2 dB**, k = 1.000, lag −16 samples (one frame). With the
  RIG's `TSMODE=2` (timestretch) the fit is −3.9 dB — grains, as expected.

So the ColdFire's sample renderer runs under the port from the card, sample
for sample; the card read (176 sectors) O9 saw was the load doing its job.

### ✅ The capture pairs, measured from the ch 7 blocks (corrects O9c)

Each ch 7 block is one page of the ColdFire's input-capture ring
(`0x80005460 + page·0x100`, 64 words): **+0 = the C/D pair, +0x80 = the A/B
pair**, 16 × (L,R) each, exactly RTOS_FORK §10.18's layout. A tone on RX0
slot 2 lands at **+0x80 (A/B, L)**; on slot 0 at **+0 (C/D, L)**. O9c's
"+0x80 is fed from TX0 ring words 0/7 and never written" is ❌ retracted —
it is written, from the ESAI-in ring, and carries what the DSP calls
RX0 slot 2. 🟡 The DSP's DIR path called slot 0 "A" (O9c); the two names
disagree by two slots, which is the receive ring's phase against the ESAI
slot counter (the same rotation O9c found on the transmit side). The
ColdFire's naming is what the recorder uses: **INAB records the +0x80 pair
= RX0 slot 2/3 in the port.** ✅ Stable, not a rotation: three runs with
different load lengths (20.0/20.5/21.0 s) all land the slot-2 tone in the
+0x80 pair — the receive ring's phase is fixed where the transmit side's
rotates (O9c), so the two-slot offset between the DSP's DIR naming and the
ColdFire's capture naming is a fixed fact of the port, 🟡 unmeasured on the
unit. `--audio-in tones` (500·(k+1) Hz on slot k)
sidesteps the question: the recorded frequency says which slot was taken.

### ✅ The track record's audio is a list of segments (corrects O9d's "words 8..38")

Decoded from the FLEX run (`tools/scratch/o10_recloop.py record_audio`):
an 84-word track record carries **16 stereo pairs in segments**, each a
4-word header `(count, 0, 0x40000, tag)` followed by `count` (L,R) pairs. A
THRU voice ships two empty headers then the 16 pairs at words 8–39 (what
O9d read); a FLEX voice ships `(15 pairs)(1 pair)`, `(14)(2)`, `(13)(3)`,
`(12)(4)`, `(11)(5)` — the split walks one sample per frame with the first
header's count (0x0f, 0x0e, …) and the tag word steps `0x040000 +
n·0x400000`. 🟡 What the split means (a source-position/interpolation
boundary the DSP consumes) is not decoded; what is measured is that parsing
the segments gives the voice's audio sample-exact (the kick fit above, and
the −104 dB below), and reading a fixed window does not — two "seams" were
found and retracted inside an hour before the parse (frame-periodic
residuals against a fitted sine are the tell).

### ✅ Bryan's 128 BPM / RLEN 4 loop under the port: sample-continuous for 32 passes

Fixture = route A's own (`tools/scratch/make_seam_fixtures.py` → `r4_128`:
16-step 1X A01, T1 = recorder trigs REC1/INAB at steps 2/6/10/14, RLEN 4,
T2 = FLEX on R1 with play trigs at the same steps, 128 BPM), staged as a
card, `--audio-in tones` (500·(k+1) Hz on RX0 slot k), `--main-level 64`,
8 bars = 42,000 frames (≈45 min wall), `--block-dump`.

- The recorder records **input A = the 1500 Hz tone on RX0 slot 2** (the
  +0x80 capture pair), and T2 plays R1 from the first play trig at step 2
  (record sample 5,056 = frame 316; the trig write is at frame 322) at
  −20 dBFS, the input's own level. Play trigs land every 20,672 samples.
- **One sine, fitted on passes 2–8, fits all 32 passes to −104.4 dB rms
  (24-bit rounding), maximum deviation 0.0% at every sample** — through
  every pass boundary and every retrigger, pass 9, 17 and 25 included.
  `o10_seam.py` (residual phase of the tone) and a per-sample residual at
  each trig agree. The voice's audio that reaches the DSP has no seam.
- This is CONSISTENT with route A's recording-level finding (§10.16.5: the
  ninth arm lands on the eighth recording's last sample), not against it:
  the play trigs walk the same fractional step grid as the arms
  (4 steps = 20,671.875 samples → 20,672 ×7 then 20,671), so a recording that
  starts one input sample early is also played one sample early, and the
  stream stays continuous. On this grid, at this tempo, with play trigs on
  the arm steps, there is no click by construction.
- 🟡 TX0 in this run sits at −75 dBFS (the RECTRIG project's levels) with a
  −18 dB residual against one sine in every pass alike — an instrument
  limit at that level (the O9c ring-rotation capture, the output stage), not
  a seam; the DSP chain's continuity was proven on the THRU comparison at
  normal levels. The voice tap is the decisive one here.

### ✅ Three more of Bryan's shapes, four bars each, same result

| fixture (`make_seam_fixtures.py`, 4 bars = 21,000 frames) | voice tap, one sine over every pass |
|---|---|
| RLEN MAX, 128 BPM, play trigs on the REC steps (his test 3) | −104.4 dB rms, max 0.00 % |
| RLEN 4, 120 BPM (his clean control) | −104.4 dB, 0.00 % (pass = 22,050) |
| RLEN 4, 128 BPM, `--self`: play trigs on T1, the recording track | −104.4 dB, 0.00 % (T1 plays and records a 1500 Hz tone; INAB records the inputs, not its own output) |
| RLEN 4, 128 BPM, play trigs OFF the record steps (4/8/12/16 against REC at 2/6/10/14) | −104.4 dB, 0.00 % from the first trig at step 4 (`r4_128_off`) |

And the read-back tap (the chain OUTPUT, T2's slot of core 1's 256-word
read-back, `o10_continuity.py --readback`) on the 8-bar run: −69 dBFS (the
RECTRIG part's own T2 level, 49 dB under the voice), residual −56 dB rms
and 0.5–0.7 % max **in every pass alike** — the DSP's arithmetic floor at
that level, nothing localised at any trig or boundary. ✅ Done: with T2's AMP VOL at 127 (the AMP page is the six bytes BEFORE
the track's FX1 row in `ot_project`'s P1_OFF layout — lane flat 12..17; the
`+0x1b` "level" byte did nothing to the read-back) the read-back rises to
−57.1 dBFS and the residual against one sine is −58.9 dB rms, **broadband**
(every harmonic below −100 dB relative: it is T2's FX1 FILTER's own
low-level noise, not distortion) and **proportional to the signal**, with a
single-sample −0.5 % dip (−103 dBFS absolute) at each retrigger and no phase
step anywhere. The DSP side of a retrigger is clean to that level.

**So the port does not reproduce Bryan's click at 128 / RLEN 4 with play
trigs on the record steps.** What the port cannot see, and where the click
can still live: the unit's own trig-to-arm timing (the RTOS tick is 2× off
in both emulators — `--pit-clock`, recorded in O8; the 0x8c jitter is real
but the port's frame-lock is idealised), the DSP-side retrigger of a FLEX
voice on a buffer being written (the port's DSP runs the real code, but its
ESAI/DMA phase against the ColdFire frame is the port's), and any hardware
path outside the host port. All four falsifiers the port can run — a play trig NOT on a REC step, RLEN
MAX, the sound-on-sound shape, the 120 control — came back continuous: with
both mechanisms on the same sample grid the played stream is the input
delayed by the arm-to-trig offset, and that offset never changes. Bryan's
click therefore needs the two to ROUND DIFFERENTLY on the unit (the pass is
20,671.875 samples at 128 BPM — a fraction; at 120 it is 22,050 exactly,
which is why 120 is clean), 🟡 inferred from the arithmetic, and the port
cannot show which side rounds which way because it rounds both the same.

✅ **Measured, the port's own timing of the two:** the play trig's sub-frame
nibble (`FW_LIVE_NIBBLE`) is `f` for passes 1–8 (frames 322, 1614, …, 9366),
`e` for passes 9–16 (10658 …), `d` for 17–24 (20994 …) — one sample earlier
every eight passes, in lockstep with the arm offset route A measured
(15 ×8 then 14, §10.16.5). Both derive from the frame builder's event
arithmetic, so in the port they cannot drift apart. 🟡 **What would make the
unit click on exactly Bryan's pattern:** a stage on one side that works in
2-sample units (stereo pairs). At 120 BPM a pass is 1,378 frames + 2
samples, so the nibble walks by two per pass and a 2-sample stage tracks it
exactly — clean; at 128 it walks by ONE every eight passes, which a 2-sample
stage cannot follow — one click per eight passes, i.e. every two bars,
starting at the ninth pass (Bryan: "~6th repeat" onset, clicks at RLEN 4,
16 and MAX alike, 120 clean). Hardware falsifier that costs one capture: a
tempo whose pass fraction walks by an ODD number of samples per pass but
not every eight (e.g. 125 BPM: 21,168 samples/pass exactly = clean
predicted; 130: 20,353.85 → walks; the prediction is click iff the
per-pass walk is odd), against a tempo with an even walk.

### Upstream check (8 Sep 2026, after a prior-art note from a parallel session)

joelanders/mc68k-md-mm PR #5 ("Expose shared HI08 command acceptance and
receive status", open, based on our pin 4a6d0d1) adds optional callbacks so
CVR HC follows DSP acceptance. The port already does exactly that on its own
side — `dsp.cpp` clears HC/HCP from the dsp56300 interrupt-taken hook (O8,
measured) and does not use mc68k's HI08 acceptance path — so the PR neither
changes what the port relies on nor duplicates code the port would drop.
Bump the pin when it merges; nothing to port. dsp56300/dsp56300 issue #5 is
the AGU modulo pre-decrement defect O9b fixed in `agu.h`; still open
upstream, our patch stands.

## Milestone O11 — the first hardware failure diagnosed under the port: the one-aux return (8 Sep 2026, branch `coldfire-o11-return`)

`FAILURE_MODES.md` "the one-aux return never reaches T8" (flash 6, tag 20):
sending AUX produced wet on T1 and T5, the engines' own hosts, and nothing
on T8; the local gate was green on exactly the property the unit falsified,
and the cause was guessed as a liveness stamp lost across cores. The port
runs the shipping remix (`make bus REMIX=bamsep27`) from a card with the
firmware driving both cores, so it can reproduce the unit.

### ✅ Reproduced, in the read-backs

Fixture: `ot_project.py rigproj` on the cleared RIG backup (every part of
every bank gets the rig: T1 CHARACTER + DELAY SERVER, T5 MODULATION + REVERB
SERVER, T8 CHARACTER in BUS mode as the return, SENDs with AUX 30–50), staged
as a card, a 300 Hz tone on RX0 slot 0 (T2's THRU input, AUX 40), 2,000
frames. Per-track read-backs (the chain outputs):

| | T1 (delay host) | T5 (reverb host) | T8 (return) |
|---|---|---|---|
| port, shipping image | prints wet from frame 500 (−43 dBFS L, −48 R) | prints the reverb from frame 200, rising to −53 | L −26 (the master mix's dry; T8 is the master), R only the reverb rising to −48 |
| `rig_render` (dsp_host), same part and stem | silent | silent | −58 L/R, the reverb, a tail |

The hosts printing their own wet IS the hardware symptom. A DSP write watch
on the return's stamp word (`Y:0x360d8`) showed only the clear-on-read
writes; a PC watch on the station's stamp instruction (`P:0x183b`) never
fired: the station never stamped.

### ✅ The cause, measured on the dispatcher: r7 has THREE blocks per track

The station pins itself to track 8 by `r7 & 0xff00 == $6700/$6800`. A PC
watch after its `move r7,a` read **r7 = 0x6a00** for track 8's FX1, every
frame, init and proc. A PC watch on both `jsr (r2)` sites of the stock
dispatcher (P:0x4d7 FX1, P:0x50d FX2 on payload A; P:0x2cc/0x302 on B) gave
the whole map on both cores:

| position | FX1 r7 | FX2 r7 |
|---|---|---|
| 0 (T5 / T1) | 0x6100 | 0x6200 |
| 1 (T6 / T2) | 0x6400 | 0x6500 |
| 2 (T7 / T3) | 0x6700 | 0x6800 |
| 3 (T8 / T4) | **0x6a00** | **0x6b00** |

A write watch on the counter `X:0x20a` names the three bumps per track:
P:0x4b4 (FX1), P:0x4ea (FX2) and **P:0x524, unconditional, after FX2**
(`move x:>$20a,b / add #>$100,b / move b,x:>$20a` at 0x518–0x524, identical
in the stock image), reset to 0x6000 at the frame start (P:0x379). So
**r7 = 0x6100 + 0x300·pos + 0x100·(fx−1)**. The harness had
`1 + 2·pos + (fx−1)` (dsp_host's r7probe comment; its "track 2 FX2 = 0x6400"
🟡 reads as position 1's FX1 under the real stride), which is right for
position 0 only. The return's pin and the send client's track-8 refusal
(`$6800`) were both derived from that model: they matched in `dsp_host`,
never on the unit — the gate was green and the unit was wrong for the same
reason. Not a cross-core race: the reverb host and the return share core 0.

Also seen on the way: the DSP holds THREE copies of the four per-instance
parameter blocks (X:0x25d, +0x80, +0x100; r6 cycles 0x2c3/0x343/0x3c3 for
the same instance across frames) — one instance, triple-buffered
parameters, not three instances.

### ✅ The fix, proven both ways

`character.asm` pins `$6a00/$6b00`; `send_client.asm` refuses `$6b00`;
`rig_render`, `verify_onebus` and `send_probe` pass `-r7 1 + 3·pos + (fx−1)`;
dsp_host's comment corrected (patch regenerated). Under the port with the
rebuilt image: the stamp instruction fires every frame, **T1 and T5 print
nothing for 2,000 frames, T8 returns** (R = the reverb building; L = the
master mix's dry plus the reverb, since T8 is the master and `rig_render`
does not model that routing). `make verify-onebus`: every property holds on
both cores — and with the r7 model fixed but the old pin it failed 10 of
25, which is the gate finally seeing what the unit saw. UNFLASHED; it is
the flash-7 candidate (stamp-defaults before play, as ever).

**What this milestone establishes:** a hardware failure the lock-step
harness could not show was reproduced and located in one session with no
flash, from the card, with the firmware's own dispatcher as the instrument.
The rule it leaves: **any module logic keyed on a dispatcher fact (r7, r6,
X:0x213, block addresses) is measured under the port, not modelled in
`dsp_host`.** ✅ **The fix built against this r7 reading is hardware-confirmed
(9 Sep 2026, flash 7, tag OCTABAM21): the hosts stay quiet and the return
reaches T8 on the unit — see the O12 hardware subsection below.**

## Milestone O12 — the bus itself under the firmware: bit-identical to `dsp_host` (8 Sep 2026, branch `coldfire-o12-bus`)

O9c's gate, on the shipping rig's AUX bus instead of a THRU insert: T2 sends
AUX into the delay host on core 1, the return station on core 0 reads the
last live stage. Fixture: the one-aux rig (`rigproj`, image
`bamsep27` with the O11 pins), `MASTER_TRACK=0` so T8's read-back is the
return alone, `rig_render` fed T2's own chain input (the 84-word record's
audio) at the port's pre-FX gain k = 0.253931 (O9d). Three fixture facts
had to be learned first, each measured:

- **The send client mono-sums the block** (`x:(r0)` + `x:(r0+1)`, `asr #1`)
  before scaling by AUX. `dsp_host` writes a mono stem into BOTH channels;
  the port's THRU with a tone on one input is left-only, so every send
  deposited 6 dB less. With the tone on both inputs of T2's pair the
  steady-state return matched to 0.0 dB (−29.7 / −29.7 dBFS).
- **The ColdFire slews the knobs** (the 160-frame marker slew), so a send's
  AUX ramps from 0 after the transport start; `dsp_host` has the value from
  block 0. Under a steady tone the port's return simply runs ~4,000 samples
  behind the harness's envelope. A probe that starts after the slew (the
  kick from sample 4,000) removes it.
- **The delay's modulation LFO is history.** With MDEP 48 the repeats
  wander ±30 samples window to window between the two (the LFO's phase
  after thousands of frames of load versus block 0). MDEP 0 for the gate.

Not the cause, each checked on the way: the tempo (`--tempo 120` changed
nothing), the auto-gain (the delay reads count 1 and reciprocal 1.0 under
the port, PC-watched at `P:0x79e/0x7a8`; with one client both sides have
counts 1,1,1,0), the parameters (T1's DELAY SERVER page 1 and 2 words on
the DSP equal `rig_render`'s), the bus scratch (rotation, reciprocal table,
counts identical; the buffers 8 dB lower = the mono-sum above).

**Result, kick on both inputs after the slew, one client, MDEP 0:**

| | lag | scale | residual |
|---|---|---|---|
| T2's chain (SPECTRUM + SEND) | −32 | 0.000 dB | **−131 dB** |
| the return, samples 7,000–17,500 (the repeats) | −36 | 0.000 dB | **−120 to −200 dB** per window |
| the return, whole run | −36 | −0.04 dB | −23 dB (the onset window and the render's end edge) |

The 36 = the 32-sample record pipeline (O9d) + 4: four block-latency stages
on the bus (send→accumulator, accumulator→delay, delay→chain, chain→return,
each "two buffers back") at the firmware's 16-sample frame against
`dsp_host`'s 15-sample block. The bus's cross-core mechanism — the rotation
each core tracks privately, the four-deep buffers, the liveness stamps, the
chain buffer — produces the same words under the firmware's real ordering
of the two cores as under lock-step. BUS.md's "what only hardware can show"
(a return that flickers, repeats missing from the reverb) does not show
here either; 🟡 the port's core interleave is still not the chip's timing
(O8), so this is the strongest local statement, not a hardware one. The
reverb stage was not bit-compared (its allpass modulation is history, like
MDEP); its level matched within 1 dB in the both-engines run.

### 🟡 The reverb stage: level-matched, not bit-compared, and why (8 Sep 2026, later)

Same fixture with the reverb instead of the delay (T1's FX2 = SEND, one
client, MOD 0): the port's return is **deterministic** — identical to
−200 dB across load lengths and across core-interleave quanta of 1, 64,
1,000 and 100,000 instructions (`--dsp-quantum`, added for this; the delay
fixture is likewise quantum-independent) — so it is NOT a cross-core race.
Against `dsp_host` it is −7.5 dB residual with the envelope within ±3 dB
per window and one extra frame of lag (52 = 36 + 16: the reverb's
accumulator read crosses cores, one more block back).

The difference is in what T5's engine is GIVEN, not in the engine: the
DSP's per-instance block for T5 carries FX1 slots 2 and 5 and FX2 slot 2
as fractional, time-varying values (`0x321a`, `0x3f`, `0x28ad`) where the
part holds 0, 0, 0 — on Sam's RIG project. Ruled out by measurement:
the scene crossfader (poked to 0 at `0x460d16c8`: unchanged), the track's
LFO depths (zeroed in the part: unchanged), the pattern (the PATTERN= key
did not move the port off pattern 0 — 🟡 the loader's pattern source is
not that key). The pre-image `0x80000a50` for T5's FX2 slot 2 is written
0x7f00 by the load, 0 by the refresher and 0x6400 by the slew packer
(`0x4000d688`) at the transport start, none of them 0x28ad — the modulated
value enters between the pre-image and the block, i.e. in the frame
builder's slew/scene stage (`0x4000cc20`/`0x4000ccfc`, O9d), from per-track
state this project carries and the toolkit cannot yet read or clear.

The alternative fixture — the rig on the RECTRIG backup, which has none
of that (T5's words come out exact: `28 30 00 00 00 00`, `00 40 00 64 40
7f`) — cannot hear anything: its T2, set to THRU with the RIG's THRU page
bytes copied in, never gets the transport-start write (only tracks 0 and
5 do), so it renders silence. ✅ Sam: T1 and T2 are THRU on his projects
(drums and synths in), which is why the RIG-based fixture hears and the
RECTRIG-based one does not. **Toolkit gap (the way through, per Sam):
stamp a fresh project** — `ot_project` can set a machine type and a PB
page but not whatever else makes a THRU track start; that is the next
tool, and with it the reverb comparison is one run.

### ✅ Found: the rotation word flips in the middle of core 1's frame, and the clients read it directly (9 Sep 2026)

With the reverb path still −13 dB off on the clean project (T2 trigged
at step 1 — a THRU machine on the unit passes nothing until it is trigged,
which is why the RECTRIG-based rig had been silent; T5's words exact;
the port deterministic across interleaves), the cross-core read was
instrumented directly:

- `--dsp-watch 0:Y:36000`: core 0's housekeeping (P:0x8fd, in the reverb
  host's block) flips the shared rotation word once per frame, 0x30 → 0x00
  → 0x10 → 0x20, spacing 66,546 / 66,574 instructions.
- `--dsp-pcwatch 1:623` (the send client's `move y:>$36000,a` + `and #>$30`,
  every bus client on core 1 runs it per block): **within one core-1 frame
  the four clients see 0x30, 0x30, 0x00, 0x00** — the flip lands between
  the second and third client, every frame, at the firmware's own serving
  order (the port's interleave quantum does not move it: 1, 64, 1,000 and
  100,000 give bit-identical output).

So half of a frame's senders deposit into buffer N and half into buffer
N+1. The delay reads the accumulator on the same core with the same phase
each block and is bit-exact regardless; the reverb, the only cross-core
reader, gets a frame's deposits split across two of its "two back" reads —
a persistent residual with a matched envelope — ❌ this link is retracted
below: fixing the split leaves the reverb residual untouched; the split IS
the mechanism behind FAILURE_MODES' "cross-core bus glitch — the
accumulators' race 🟡" (T4 + delay MODE 1). `XBUS.md`'s rule ("clients never read the shared
rotation word directly; each core tracks the rotation privately, advancing
once per block") is not what the one-aux code does: the send client,
both engines and the return all read `y:$36000` at their own block time.
`dsp_host` in lock-step flips the word between whole core frames and can
never split one.

**The fix is a module change, not a port one:** a rotation each core
derives from its own frame count (the dispatcher's per-frame counter at
`x:$41c`/`x:$41e`, if it is one — measured below) or latched once per core
per frame, so every client on a core writes the same buffer for a given
frame; the four-deep buffers already absorb a constant one-block offset
between the cores. The port is the gate: after the fix the reverb path
must come out bit-identical like the delay path. 🟡 Whether the unit's
serving order lands the flip mid-frame exactly as the port's does is
hardware's to say; that it lands mid-frame at all is enough to split.

### ✅ The fix: one rotation tracker per core (9 Sep 2026, branch `bus-private-rotation`)

Measured first, with `--dsp-pcwatch` on the RESOLVED write offset
(`x:(r7+$69)` as each client consumes it): under the old per-instance
tracking core 1's four clients resolved **3/1 per frame** — three on
buffer N, the fourth (T4, the last dispatched) on N+1 — every frame. ⚠️
Correction to the paragraph above: with T2 the only sender in the reverb
fixture, its deposits landed consistently, so the split did NOT cause the
reverb residual (which survives the fix, below); the split is the standing
"T4 + delay MODE 1" hardware residual, T4 being that fourth client.

The fix is in `build_bus.py`'s ROTLATCH body for payload B: the design's
tracker (advance one step per frame; keep it when the shared word reads
one step behind, i.e. a pre-flip read; snap otherwise) is kept, but ONE
per core in a bus-scratch word only core 1 writes (`+0xc6`), advanced by
position 0's FX2 (r7 == `$6200`, the rig's delay host) and checked by every
client against the shared word. Measured after: **all four core-1 clients
resolve the same buffer every frame** (0x30 ×4, 0x00 ×4, 0x10 ×4, 0x20 ×4).

A first version latched the shared word as READ at position 0 and the
port killed it within the hour: a pre-flip read puts the whole core one
buffer BEHIND core 0, and its "two back" read is then exactly the buffer
core 0's housekeeper is clearing (`R+1 ≡ R−3`): the delay path went
SILENT under the port and `verify_onebus` lost its skew identity. So the
four-deep scheme tolerates no cross-core offset at all — both cores must
resolve the post-flip value — which is precisely what the tracker's
asymmetric keep provides and a latch does not.

Gates: `make verify-onebus` every property on both cores, skew identity
included; `REMIX=bamsep27 make check` exit 0; the port's delay path
bit-exact in the steady windows (−120 to −200 dB, lag now −21); the
reverb path unchanged at −13 dB — still open, and now demonstrably NOT the
rotation. UNFLASHED, alongside the O11 pins.
### ✅ The harness ran 15-sample blocks; the unit runs 16 (9 Sep 2026, branch `harness-whole-block`)

Chasing the reverb residual through the dispatcher: `dsp_host` seeds the
frame-context word `x:(x:0x415+0x1e)` with a "frames" nibble and capped it
at 15 ("the & 0xf in setup"). Under the firmware that nibble is the
track's SPLIT: at every unsplit dispatch `x:$20c = 0` and `x:$20d = 16`
(peeked), the a=0 sub-block call is skipped and the a=1 call runs 16
samples. `dsp_host` seeded 15, took `x:$20c` (=15) as the count, and so
every harness render since the harness existed processed **15 samples per
block** — self-consistent, but 16/15 on every per-block rate (an LFO
advanced once per block, a per-block ramp) and a sub-frame offset on every
bus latency (the −36 / −34 / −21 lags above: four bus stages at one sample
each). `dsp_host -frames 16` now seeds the nibble as 0 and takes the a=1
call's count (`x:$20d`) when `x:$20c` is 0; at the legacy 15 nothing
changes (the bit-identity gates stand). `rig_render --frames 16`.

| at 16-sample blocks (`--frames 16`) | lag | residual |
|---|---|---|
| the delay path (kick, one client, MDEP 0) | **−16 = exactly one frame** | −120 to −200 dB per steady window |
| the reverb path, modulators live | −14 | −14 dB |
| the reverb path, allpass modulator frozen (test build) | −16 | −16 dB early → −31 dB late |

**The reverb residual is not a defect the port can name.** Its output is
statistically the harness's (levels within 0.1 dB, L/R correlation 0.545
vs 0.541; L↔L −14 dB, cross-channel −2 dB, so no swap) and it is
FREE-RUNNING: the same kick 1,000 samples later gives a tail −22 dB
different from the earlier tail time-shifted — the fixed-depth allpass
modulator (`$200000`, "never zero" by design) runs on its own phase, and
`dsp_host` meets the input at a different phase than the port. Freezing it
in a test build halves the residual and leaves a component that DECAYS
with the tail (−16 dB at 0.1 s, −31 dB at 0.5 s): an initial-state
difference at the kick's arrival, 🟡 unlocated (candidates: a per-block
counter or warm-up artefact the port's thousands of load frames leave
differently from the harness's 256-block warm-up; the tank's persistent
one-pole states). Everything static about the reverb — input, parameters,
bus timing, block length, channels — is now proven equal; what is left is
its own history.

### ✅ Flash 6's remaining claims, run under the port (9 Sep 2026, branch `bus-claims-under-port`)

The master-off rig, T2 sending, the flash-7 image, one project variant per
claim (`set-fx`/`stamp-slot`), 1,200 frames each, T8's read-back as the
return:

| claim | fixture | result |
|---|---|---|
| iii, neither engine | T1 and T5 FX2 = NONE | T8 peak 0: digital silence, not garbage ✅ |
| iv, delay MIX 0 | | the reverb of the DRY sends: same onset, −44 dBFS against the repeats-only −58 ✅ |
| iv, reverb MIX 64 | | repeats under the tail: a different, non-silent return ✅ |
| v, the refusal | SEND on T8's FX2, AUX 127 | T8's return **bit-identical** to the baseline ✅ |
| vii, a BUS-mode station on T4 | | T4 returns nothing ✅ — **and T8's return went SILENT from its first sample** ❌ |
| vii, the same on T7 | | T7 returns nothing ✅ — **T8 silent** ❌ |
| viii, 6,000 frames | | no block under −90 dBFS after frame 1,000; the return's slow ±5 dB wander is the modulated tank on a pure tone ✅ |

**The vii defect:** the engines' liveness stamps are clear-on-read with a
single reader by design, and a BUS-mode station on ANY track ran the
reads — a station that failed the pin returned nothing, exactly as flash
6's claim asked, while stealing the stamps from the real return on T8,
which then saw nothing live. `verify_onebus` checks "returns nothing from
T4/T7" and "T8 returns" as separate cases and never both at once. Fixed in
`character.asm`: a station whose RET level is 0 (not track 8, or the knob
down) skips the stamp reads and the RETV/RETD stamps. Re-run: T8's return
bit-identical to the baseline with the station on T4; with it on T7
identical until frame 641 then −34 dB different — 🟡 that frame is one of
the port's jittered host frames (a 15-sample frame among 16s; the frame
edge is the DSP's own bank write, so a heavier core-0 load moves the
jitter), the port's timing model rather than the bus. `verify_onebus` and
`make check` green. ✅ **Falsifier run (9 Sep 2026):** the baseline and
the T7-station case re-run at core-interleave quanta 2,000 and 50,000
(default 64). At BOTH quanta T8's return first differs at sample 10,262 =
frame 641 — and the BASELINE differs from its own default-quantum run at
exactly that sample too, with nothing on T7 changed. The difference is
−144 dBFS rms against a −74 dBFS return, i.e. a few samples one LSB apart.
So frame 641 is where this run's host-frame phase is decided by the
interleave, not where a station does anything: any two runs that differ
in anything diverge there, at the LSB. The bus reading stands; the
mechanism (the jittered frame) is still the inference.

Tools: `rig_render --extra '<dsp_host args>'` (e.g. `-dumpy 36000,360d3,f`
to dump the bus scratch after a render, which is how the two scratches
were diffed word for word).

### ✅ The port's diagnoses confirmed ON HARDWARE (9 Sep 2026, tag `OCTABAM21`, flash 7)

The whole point of O11/O12 was to fix on the unit what the lock-step
harness could not see. Flash 7 is that image (`REMIX=bamsep27 make bus`,
byte-identical to the O12/O13 test image bar the build tag), flashed and
measured on Sam's Octatrack over MIDI (`docs/effects/FLASHPLAN.md` "Flash 7"; a
Mac-generated 1 kHz burst into inputs A/B, the main outs captured, the Mac
the clock master, `tools/hw/hw_flash7.py` + `hw_flash7_liveclaim.py`). **Every
one-aux bus claim passed**, and the two things the port found are now
hardware-proven:

- **The r7-stride fix (O11).** Flash 6's failure was "the hosts keep
  printing their wet, nothing on T8", because the harness modelled two r7
  blocks per track where the dispatcher bumps r7 three times, so the return
  and the track-8 refusal matched in `dsp_host` and never on the unit. On
  hardware now: muting the reverb host T5 does nothing to the return while
  it is live (+0.3 dB), and −14 dB only with the return's level at 0 — the
  hosts are quiet, the wet is on T8. The r7 the port read (`$6a00`/`$6b00`,
  three blocks per track) is the unit's.
- **The station stolen-stamp fix.** A BUS-mode Character on T4 returns
  nothing (muting it moves the return 0 dB) AND T8 returns a full −34.6
  dBFS beside it — before the `character.asm` guard (a station with RET 0
  touches no clear-on-read stamp) T8 would have gone silent, which is the
  defect O12's claim-vii run found under the port with T8 present.
- **The send refused on the master T8.** Toggling T8's own AUX does nothing
  to the return (the position pin `$6b00`), while T8's RET moves it.
- **The chain and the fall-through.** Both engines shape the return, and
  with the reverb removed the delay's repeats still return (the last live
  stage), no dropout over 90 s.

So the port earned its keep: it named a hardware failure the lock-step
harness reported clean, the fix built against the port's r7 reading holds
on silicon, and O12's own claim-vii defect and its fix are both confirmed.
What the harness still cannot see (the cross-core skew, the cycle cliff)
is unchanged; what the port measured about the dispatcher was right.

## Milestone O13 — the cycle count under the firmware: the port's stopwatch and `dsp_host`'s meter agree within 2 % (9 Sep 2026, branch `port-cycle-meter`)

The question the port could answer that nothing else could: what does the
firmware's OWN dispatch cost per frame on each core — every effect it
actually calls, with the knob state the part actually publishes, plus
whatever it dispatches into an empty slot — in the same unit `dsp_host`'s
meter reads (`docs/remixer/HARNESS.md` "The meter": executed instructions, no
stall modelled). Two instruments that agree validate the meter as the
load instrument for the rig; two that disagree mean one is blind.

**The instrument.** `--dsp-stopwatch core:start:stop` — arm at the first
PC, count instructions executed by that core until the second, one pair
per arm/stop; prints the count, mean, min, max and the last 24. Semantics
that matter: a second arrival at `start` before `stop` RE-ARMS (the window
becomes the last start→stop), and `start == stop` never pairs. So the
right window for "what does this call cost" is the dispatcher's `jsr` and
the instruction after it — the callee is everything in between — not a
guessed whole-dispatcher span: core 0's head at P:0x41e is entered THREE
times a frame (906 arms per 300 frames against one exit at 0x559), so a
0x41e→0x559 window read 7,050 per frame for a frame whose calls sum to
24,654, and core 1's 0x221→0x34e window (1,880 per frame) does not
contain its effect calls at all. Call sites, from the dispatcher listing in
O9c: core 0 FX1 `jsr` at P:0x4d7, FX2 at P:0x50d; core 1 FX1 at P:0x2cc,
FX2 at P:0x302 — four arrivals per frame each, tracks in dispatch order.

**The measurement** (Sam's RIG, `card_oneaux.img`, `mainos_flash7b.bin`,
300 frames, `--audio-in tones`, the played part's knobs; instructions per
16-sample frame, every frame identical once the chain is warm):

| core | call | per frame | per sample |
|---|---|---|---|
| 0 (T5–T8) | T5 FX1 MODULATION | 480 | 30 |
| | T5 FX2 **BusVerb** | **17,746** | **1,109** |
| | T6, T7 FX1 SPECTRUM | 549 each | 34 |
| | T6, T7 FX2 SEND | 284 each | 18 |
| | T8 FX1 CHARACTER (BUS station, RET 127) | 4,710 | 294 |
| | T8 FX2, the empty slot's fallback SEND | 52 | 3 |
| | **core 0 total** | **24,654** | **1,541** |
| 1 (T1–T4) | T1 FX1 CHARACTER (live, see below) | 4,665 | 292 |
| | T1 FX2 **BusDelay** | **7,989** | **499** |
| | T2–T4 FX1 SPECTRUM | 564 each | 35 |
| | T2–T4 FX2 SEND | 277 each | 17 |
| | **core 1 total** | **15,177** | **949** |

✅ measured (`out/o9d/r_swc_*.txt`). The dispatcher's own work between the
calls is not in the sums; it is bounded by the 1,880 of core 1's
0x221→0x34e segment and is the same on hardware.

**Against `dsp_host`'s meter for the same rig** (`rig_render --frames 16
--project proj_oneaux --bank 2 --part 1`, `out/o9d/rig_meter16*.log`):

| core | port, calls summed | `dsp_host` meter, max block | agreement |
|---|---|---|---|
| 0 | 24,654 | 24,971 (mean 22,690) | 1.3 % |
| 1 | 15,177 | 11,126 as rendered; **14,880** with T1's RET forced to 127 | 2 % once the knob state matches |

The core-1 gap was a knob, not a defect in either instrument: CHARACTER
takes its bypass loop (about 600 a frame) when every knob is neutral,
including the page-2 side gain at exactly 64, and `dsp_host` drives the
part's page-2 values into the companion bytes while the port's emulated
load leaves page 2 unpublished (O9c: the page-2 publish path is the
`ccpage2` copier) — the peek of T1's FX1 block (`x:0x363`) under the port
shows six page-1 words of `000000`, low bytes included ✅. So the port
runs T1's CHARACTER live, which is the worst case and the one the wall
cares about. (🟡 whether the hardware's load publishes page 2 before the
first knob touch is the O9c question, unchanged.)

**What this settles.** `dsp_host`'s meter reads the firmware's real load
to within 2 % when its knob state matches the part's — the effects the
firmware dispatches, in its order, with the fallback SEND in the empty
slot, cost what the harness says they cost. The rig's load in meter
units is core 0 ≈ 1,540 instructions/sample, core 1 ≈ 950. What it does
NOT settle: the unit is instructions, and the 3,120 wall
(`docs/firmware/CHIP.md` §2) is in `tools/build/cycle_count.py`'s static words with
the hardware's stalls on top; the port models no stall either. BusVerb
reads 1,109 here, 1,130 on the meter, 1,652 static — the same ~0.68
ratio `HARNESS.md` records — so the port is a third floor in the meter's
unit, not a ceiling. Only the burn sweep measures the ceiling.

**Reproduce:**

```
./out/emu/ot_emu --image out/o9d/mainos_flash7b.bin --card out/o9d/card_oneaux.img \
  --set OCTABAM --project RIG --sequencer --internal-clock --frames 300 --load-ms 20000 \
  --dsp --main-level 64 --audio-in tones --dsp-stopwatch 0:50d:50e     # core 0 FX2 calls
```

## Milestone O14i — `--interactive`: the port as the panel's emulator, with a real-time clock (11 Sep 2026, branch `panel-ui`)

The virtual front panel (`tools/panel`) drives route A one key at a time:
push `<row> <mask>` into UART A's receive queue, run 50 ms, decode what the
firmware sent the panel. `--interactive` gives the port the same surface
over a pipe, so the panel (or any script) can drive it instead of the
Python emulator without a change of contract. The boot, the mount and the
load are the batch code paths, unchanged, flag for flag; the loop starts
where the batch reports would have, and `quit` returns before them.

### The protocol (`main.cpp`, `serveInteractive`)

```
out/emu/ot_emu --interactive --image ... [--card IMG --mount --set S --project P] [--dsp] [--frame] [...]
```

boots exactly as today, prints one line `ready sample=<double> frames=<u64>`
to stdout, then reads one command per line from stdin and answers one line
per command, flushed. Integer arguments are decimal or `0x..` -- a leading
zero is NOT octal (`key 0x26 08` is mask 8; the first cut parsed with
`strtoull` base 0, so it answered `err usage` and `010` meant 8 -- fixed 11
Sep 2026, later the same day); `run`'s ms is any non-negative decimal
(`50`, `50.0`, `1e+03` as the panel's `:g` prints it); hex payloads
lowercase, no spaces.

| command | reply | does |
|---|---|---|
| `run <ms>` | `ok sample= frames= stop=<time\|gate\|fault\|illegal>` | `Rtos::run(ms, untilGate=false)`, idle skip included |
| `key <row> <mask>` | `ok` | `Uart::rxPush(row); rxPush(mask)` into UART A; the receive interrupt follows `Uart::irq()` |
| `knob <row> <delta>` | `ok` | row, then `delta & 0xff` (signed detent delta) |
| `tx` | `tx <hex>` | UART A's transmit bytes since the previous `tx` (the first answers everything since boot: the panel's whole screen is in it) |
| `peek <addr> <len>` | `peek <hex>` | `len` 1..4096; `Machine::mapped` first -- an unmapped address answers `err` instead of growing a zero page |
| `poke <addr> <hex>` | `ok` | same mapping rule |
| `frame on\|off` | `ok` | `Rtos::setFrame`; with `--dsp` the DSP-driven edge stays as configured (`frame on` is what arms it) |
| `status` | `status sample= ms= frames= frame= idle= wall=` | `wall` = seconds of wall clock spent INSIDE `run` since ready |
| `quit` | `ok` | exit 0 |

Anything else answers `err <message>` and the loop keeps serving -- an
empty line, an unknown word, the wrong number of arguments (`tx`, `status`
and `quit` take none: `tx extra` / `status now` / `quit please` answer
`err usage: ...` instead of ignoring the word, as the first cut did), a
number out of range or with a stray character; EOF on stdin exits 0.
Measured 11 Sep 2026: 33 malformed lines all answer `err`, `key 0x26 08` /
`0x26 010` / `38 0X1A` / `knob 0x30 -3` land `26 08` / `26 0a` / `26 1a` /
`30 fd` in UART A (read back through a peek of its data register), and
the queue is empty after the rejected ones. `run` is the only command that costs
emulated time. A `peek`/`poke` of a peripheral register is a real bus
access (a peek of UART A's data register consumes a received byte).

### ✅ The RTC on the DSPI (`periph.h`, `Dspi`)

Found 10 Sep 2026 under route A (`panel_server.install_rtc`), ported here:
on DSPI chip-select 2 the far end is a DS1390-style SPI real-time clock.
PUSHR is CONT (bit 31) | PCS (bits 21-16) | data (bits 15-0); the firmware
reads one register per transaction, `<reg> 00` with CONT held between the
two frames: 0x01 sec, 0x02 min, 0x03 hour, 0x04 weekday (1 = Monday), 0x05
date, 0x06 month, 0x07 year, all BCD, plus 0x00 hundredths and 0x0e status,
and one write `9e 07` at boot. Transactions are delimited by CONT, not by
counting frames (a boot-time one is three frames). Every other chip select
keeps the loopback (reply 0); on chip-select 2 the reply is the clock's
time, and a written register is remembered and answered back from then on
(the dialog's own SET sticks; that register no longer advances).

**`--rtc host|off|<epoch>`, and OFF is the batch default** (11 Sep 2026,
later the same day, after the verifier's diff). The first cut answered the
host clock unconditionally, in the batch too, and that broke the repo's
own oracle rule: the standard card-loaded batch (`--card otlive.img
--mount --set OTLIVE --project PROJECT --ms 1000 --serial-out --golden`)
had byte-identical stdout but its `serial_a` differed from the pre-RTC
binary at byte 5141 of 7325 (158 bytes: the dialog's date), two runs of
the same binary seconds apart differed from each other (44 bytes from
5211: the seconds), `oracle.py` on the two goldens exited 1 with `DIFFERS
serial_a`, and route A's stock `emu_rtos.Dspi` still answers 0 (only
`panel_server.install_rtc` has the model) -- so a route-A-vs-port oracle
on any loaded project reported a false fatal serial divergence. Now
`Dspi::RtcClock` is `Off` unless `--interactive` (then `Host`), and
`--rtc` picks explicitly (`main.cpp`, wired through `Dspi::setRtcClock`
before `Rtos::install`):

| `--rtc` | chip-select 2 | the dialog, OTLIVE loaded (looked at) | YES stores at `0x80000080` |
|---|---|---|---|
| `off` (the batch default) | the loopback: reply 0, nothing remembered -- the pre-RTC `Dspi::write`, byte for byte | `SUN 2000-00-00 00:00:00` | `07d0 00 00 00 00 00` |
| `host` (the `--interactive` default) | DS1390 registers from the host's local time | `FRIDAY 2026-09-11 20:58:15` (`otlive_boot.png`) | `07ea 09 0b 14 3a 0f` |
| `<epoch>` (seconds since 1970) | the same registers from that instant, decoded as UTC and frozen -- the same dialog on every machine, every run | `--rtc 1000000000`: `SUNDAY 2001-09-09 01:46:40` | `07d1 09 09 01 2e 28` |

A non-default mode prints one `rtc        :` line in the boot log; the
default batch prints nothing. Measured after the fix, the same batch
invocation, the fixed binary run twice: `serial_a` is byte-identical to
the pre-RTC binary's and run to run (`cmp` silent, 7325 bytes), stdout
identical (output paths aside), `oracle.py` base-vs-fixed and
run-vs-run both `8 compared field(s) agree`, exit 0; 19.9 s wall.

Under `--interactive` (`out/_agents/port/smoke.py --card
out/_agents/port/otlive.img`, `otlive_boot.png`): the SET DATE/TIME
dialog the boot opens reads **FRIDAY / 2026-09-11 / 20:58:15**, the host
clock at the time, over `LAST SET: 0000-00-00 00:00:00`; before this (and
in the batch, still) it reads 2000-00-00. YES writes the 7-byte clock
record at RAM `0x80000080` as **u16 year, u8 month, day, hour, minute,
second, binary** -- `07ea 09 0b 14 3a 0f`, the boot-time read, 17 s behind
the host by the time YES lands -- and draws DATE/TIME STORED
(`otlive_main.png`). `ctest` still passes 7/7: the rtos test's 4831-byte
serial prefix ends before the dialog's date is drawn (and the tests run
the batch default, the loopback).

### ✅ Speed, OTLIVE loaded, frame on (`out/_agents/port/measure.py`, 11 Sep 2026)

The M1 that runs the port; boot + load 19.2-19.3 s wall to `ready`
(277,821 samples = 6.3 s emulated). Tracks activated by poking pattern +84
+ 2330·t := 1 (pattern base = PART_PTR `[0x46c82456]` + CUR_PATTERN
`[0x80000004]` × 0x8ed8, here 0x400e21e0 + 0), CLOCK RECEIVE bit clear
(it already was), PLAY as the matrix key `0x25 0x01` / `0x25 0x00`.

| edge | idle, emulated ms per wall s | playing, emulated ms per wall s | emulated ms per 16th | wall s per 16th | sequencer |
|---|---|---|---|---|---|
| `--frame` (16-sample timer, `frame on` after the load) | 357 (5000 ms in 13.99 s; 13,782 frames = 2756/emulated s) | 356 (6008 ms in 16.88 s; 16,559 frames) | **125.2** (48 steps) | **0.352** | ✅ runs: the tick byte `0x800065b6` changed in 60/60 100-ms slices, the step byte 0..15 wraps every 2 s |
| `--dsp` (the cores' bank word is the edge; `frame on` arms it) | 134 (5000 ms in 37.36 s; 13,781 frames) | 132 (6007 ms in 45.43 s; 16,557 frames) | **125.2** (48 steps) | **0.946** | ✅ runs, identically: tick byte changed in 60/60 slices, same step trace; boot + load 30.3 s wall (the cores run through the boot) |
| `--dsp --frame-timer` (the cores run, the 16-sample timer is the edge) | 133 (5000 ms in 37.50 s; 13,782 frames) | 134 (6008 ms in 44.68 s; 16,559 frames) | **125.2** (48 steps) | **0.931** | ✅ runs: tick byte changed in 60/60 slices, same step trace; the timer's frame count (13,782, as `--frame`) at the cores' cost (boot + load 30.6 s); `out/_agents/port/dsptimer_measure.txt` |

Nominal at 120 BPM is 20.8 ms per tick and 125 ms per 16th, so under the
timer edge the port's sequencer runs at the firmware's own rate in emulated
time -- every one of the 2756 frame interrupts per emulated second is
delivered (the CPU is 3990 instructions per sample and the frame handler
fits). Route A at the same point delivers ~414 of them (`KEYMAP.md` "PLAY
through `/key`": ~840 emulated ms per step, ~15 wall s per step at 54-59
emulated ms per wall second); the port is **43x faster per wall second of
play and 6.7x closer to real time** on top of that. Idle with the frame
clock on costs the same as playing -- the frame handler is the load, not
the sequencer -- and `status idle=` shows the idle skip still runs between
frames (37,383 skips over the 5 s).

### What it does not do

- Nothing paces the receive bytes: a `key` lands both bytes at once, and
  the firmware's own double-tap and long-press timers see whatever spacing
  the client's `run`s give them (a tap is row+mask, ~60 ms, row+0, ~100 ms
  -- `smoke.py`'s `tap`).
- `run` blocks the pipe for its whole length; there is no interrupt, and a
  `fault`/`illegal` stop is permanent.
- Only UART A is exposed; the LED bitmap and levels, the LCD, are the
  client's to decode (`tools/panel/panel_link.py`).
- The sequencer-to-UI "trigs fired" message and the trig-row running light
  are the same open item as under route A (`KEYMAP.md`); the LCD position
  bar under the BPM redraws (8639 panel bytes over 6 s of play) but the trig
  LEDs do not chase.
- A written RTC register freezes; nothing advances it or rolls it over,
  and a pinned `--rtc <epoch>` is a constant: its seconds do not advance
  with emulated time (reproducibility over realism, by design).
- The batch does not read the RTC at all unless told to (`--rtc host`):
  a `--golden`/`--serial-out` capture past the dialog draw (~400 ms) is
  only reproducible because of that.

**Reproduce:** `.venv/bin/python3 out/_agents/port/smoke.py --card
out/_agents/port/otlive.img` (boot, ready, the protocol's error and
argument-parsing checks, YES, MIXER, tx -> PanelLink -> PNG, PLAY, 3 s, tx,
status, quit; ~28 s wall, exit 0; `--rtc off` / `--rtc 1000000000` pass
the mode through and the clock-record check follows it) and `measure.py
--tag frame` / `--tag dsp --dsp` / `--tag dsptimer --dsp --frame-timer`.
The card image is `stage_card.py` over `out/_projects/otlive/OTLIVE/PROJECT`
with its AUDIO pool (`--audio` per WAV, options before the positionals).

## What is NOT here yet

- **The rest of the peripherals.** The eDMA with its completion-timing rules
  (three wrong versions in route A, each with its own reproducible symptom),
  FlexBus/ATA and the card. (DSPI and the UARTs landed in O4 — the M6a gate
  could not be reached without them.) All are modelled in route A's
  Python, commented rule by rule with each failure mode recorded — that is the
  specification, and translating it is the bulk of the mechanical work.
- **The DSP side.** `dsp56kEmu` is already vendored, already patched for the
  shared window (`tools/patches/dsp56300.patch`), and `tools/harness/dsp_host` already runs both
  cores. Joining them needs the host-port protocol, which was decoded on
  7 Sep 2026 from the tape recorder (`emu_rtos.py --tape`): per frame the
  ColdFire alternates the cores, writes `0x81`, sends a destination/count pair
  to `0x2000001c`, then DMAs — 336-word per-track records plus a 128-word and a
  64-word block per core, with one 64-word block read back.
- **Audio out.** ~~The ESAI path is untraced.~~ ~~Traced in O9: input proven,
  output silent because no track ever starts.~~ O9b: THRU tracks pass the
  inputs to TX0 through the whole chain. ~~FLEX playback (the sample loader)
  is the open half.~~ ✅ O10: FLEX playback renders, sample-exact.

## The oracle, made concrete (7 Sep 2026)

`tools/emu/emu_rtos.py --golden FILE` writes route A's M6a facts as JSON —
handoff PC, auto-pokes, every created task with its fields, which TCBs ran,
the first switch, the first 200 dispatches with their sample times, the gate
time. `out/oracle/m6a.json` is that file for the ONEAUX project: **10 tasks
created, 11 ran, first switch boot → main, gate at 204.95 ms** ✅.
`tools/emu/ot_emu/oracle.py A B` diffs two such files field by field with no
tolerance except one PIT period on dispatch times, and reports a field the
port does not produce yet as MISSING rather than as a failure, so the port's
report can grow milestone by milestone. `docs/firmware/COLDFIRE_WORKORDER.md` is the
queue that uses it.

## The order to do it in

1. Port `emac_selftest` as a CTest, then the EMAC handlers. Nothing that
   computes should be trusted before that gate exists.
2. The peripherals, translated from route A, each with route A as the diff.
3. The DSP cores and the host port; audio last.

The rule that makes the rest delegable: **route A is the oracle.** Any
disagreement between the two emulators is a finding, not a nuisance, and the
one to trust is whichever can point at a firmware constant that only makes
sense one way (`RTOS_FORK.md` §10.16's reciprocal tables are the worked
example).


## Milestone O14j — the DMA timers, and the trig-row running light ✅ (12 Sep 2026)

The sequencer's running light never showed under any emulator. Two causes,
neither in the DSP path (identical LED timelines with `--frame` and `--dsp`):

- **Pattern byte +84 + 2330·t is PLAYS FREE, not "active".** `FW_TRANSPORT(0)`
  (the PLAY key, `0x4009bc76`) sets a track up only while that byte is ZERO;
  `FW_START_TRACK` (`0x4009b630`) is the trig-key start of a track whose byte
  is SET. The panel used to set the byte on all eight tracks before PLAY
  ("activate"), which is exactly what silenced the sequencer; the fixture
  project plays as saved with the bytes left clear. The "trigs fired" note is
  SYS command 22 (handler `0x400622da` = table[21] of the 78-entry sys
  dispatcher at `0x40061cfa`), built by the frame builder at
  `0x4000c832/0x4000c858`, drawn by `0x40043fdc` as timed `set_led` flashes;
  the running light follows the UI's CURRENT track (`0x100b14cc`).
- **The LED countdowns need the MCF5445x DMA timers.** `set_led(id, n)`
  (`0x40013784`) is a countdown of n ticks decremented by `0x4001387c` in the
  task pending on `0x46c7e0e2`, whose only signaller is the DMA-timer-1
  interrupt (vector `0x61`, DTRR 68750 / DTMR 0x1d = 8.333 ms at the 132 MHz
  bus; every second tick also posts 0x01 to the UI queue and 0x05 to sys). The
  port had no `0xfc07xxxx` model: flashes never cleared, LEDs 9-16 stayed lit.
  `DmaTimer` (periph.h/.cpp: four channels at `0xfc070000 + 0x4000·n`, INTC0
  sources 32-35, DTMR/DTXMR/DTER/DTRR/DTCR/DTCN, restart and free-run; gated in
  `test_periph.cpp`) fixes it. Cost: the firmware's own boot mount now runs
  (boot + fixture load 37.5 s wall, was 21 s; `--boot-logo` restores the
  faithful logo wait, `Rtos::Quirks::skipBootLogo` is the default) and play is
  ~11% slower from the 60 Hz UI/sys ticks. Batch stamps move ~140 samples
  earlier than pre-timer logs. DTIM0 counts the (unmodelled) DTIN0 pin.

Measured after: the lit trig pair equals the STEP byte `0x800065b5` in 84/84
25 ms slices at 125.1-125.2 ms per step (120 BPM); the eight fired-track
flashes at PLAY clear 83 ms later; STOP leaves the LED table all zero.

## Milestone O14k — the DSP main output over the `--interactive` pipe ✅ (12 Sep 2026, branch `panel-ui`)

The 12 Sep audio spike (`out/_agents/audio/`) proved the batch renders the
sequenced sample sample-exact with `--dsp`: `run3_core0.wav` slots 2/3 =
`third-0.wav` x 0.70 at `--main-level 64`. None of it reached the pipe, for
two reasons in `main.cpp`: `--audio-out` is written only after the batch
reports (`writeWav24`, one-shot, needs the total), and `--interactive`
returns into `serveInteractive` before them; and `--main-level` was posted
only inside `if(sequencer)`, so under `--interactive` the gain table
`0x80003c60` stayed zero and every voice rendered silent (O9b's trap).

### What changed (`main.cpp`, `dsp.h/.cpp`; the batch untouched)

- **`--main-level` defaults to 64 under `--interactive`** and is posted
  after the load, before `ready` (the same `Rtos::setMainLevelLive`, the
  same `main level :` boot-log line, up to 200 ms emulated); `--main-level
  off` (or a number, `0` included) overrides. The batch default stays -1
  (never posted): the reference command's `run3_core0.wav` and its log are
  byte-identical before and after (`cmp` silent; `out/_agents/port-audio/
  base_run3_core0.wav` from the pre-change binary vs `new_run3_core0.wav`,
  69 s wall each), and ctest passes 7/7.
- **A second, bounded capture on the same ESAI TX sink** (`dsp.cpp`, the
  de-rotated ring words of O9c; `DspPair::setAudioStream`): core 0 only,
  16-bit (the 24-bit word >> 8, `writeWav24`'s top two bytes), in a ring of
  `g_streamCapFrames` = 60 s x 44100 frames (10.6 MB for a pair, 42.3 MB
  for all eight), allocated at `audio start`, freed at `audio stop`. Full
  = the OLDEST frame is overwritten and counted. The batch's
  `setAudioCapture` vector is untouched beside it.

### The commands (`serveInteractive`; `err audio needs --dsp` without the cores)

| command | reply | does |
|---|---|---|
| `audio start [main\|cue\|all]` | `ok` | starts empty (restarts if on). `main` (default) = ring words 2/3 as L,R; `cue` = words 4/5 (the second pair, 3.1 dB lower); `all` = the eight words per frame (0/1/6/7 are zero on the fixture) |
| `audio read [<maxframes>]` | `audio <frames> <hex>` | everything pending (at most `<maxframes>`, 1..cap), little-endian signed 16-bit interleaved PCM, 2 (or 8) words per frame, lowercase hex; those frames are released. Never blocks: it answers what is there (`audio 0 ` when nothing is) |
| `audio status` | `audio status on=0\|1 mode=off\|main\|cue\|all captured=<frames since start> pending=<unread> rate=44100 dropped=<overwritten> cap=2646000` | |
| `audio stop` | `ok` | also when off |

Bad input answers `err` and the loop keeps serving (`audio` alone, `audio
start bogus`, `audio start main extra`, `audio read 0`, `audio read`
before a start). A 25 ms `run` is ~1100 frames = 4.4 KB = 8.8 KB of hex on
one line; a 100 ms one 4410 frames = 35 KB of hex.

### Measured (`out/_agents/port-audio/smoke_audio.py`)

`.venv/bin/python3 out/_agents/port-audio/smoke_audio.py` boots
`--interactive --dsp` on the spike's card (`otlive2.img`, OTLIVE/PROJECT),
checks the gain table at `ready`, the error answers, then YES, `frame on`,
the batch's trig (track 1 step 5 poked into the PART_PTR blob as
`--poke-trig 5` does), `audio start main`, PLAY as the matrix key `0x25
0x01`/`0x25 0x00` (the pattern's +84 bytes left clear, O14j), and `run 100`
+ `audio read` for 4 s emulated (one read split with `audio read 500`).
It writes `pipe_main.wav` (16-bit stereo) and `pipe_metrics.txt`, and
asserts: frames per emulated second = 44100 within 0.5 %, `captured = read
+ pending`, nothing dropped, no read over 1 s wall, the capture not silent,
`third-0.wav` L fits at the onset with a residual under -20 dB, and the
capture equals the batch's `run3_core0.wav` slot 2 after onset alignment
(residual under -20 dB). `--cap-test` instead runs 61 s emulated with no
read and asserts `pending = cap`, `captured = cap + dropped`, RSS growth
under 40 MB, and that exactly `cap` frames read back afterwards.

Measured 12 Sep 2026 (the M5 Mac of O14j; `pipe_run.txt`, `pipe_metrics.txt`,
`cap_run.txt`, `cap_cap_metrics.txt` beside the script):

| measurement | value |
|---|---|
| boot + fixture load to `ready`, `--dsp` | 58.6 s wall (`ready sample=277688`; the batch's same boot is O14j's 37.5 s plus the cores) |
| gain table `0x80003c60` at ready | `0xbf7fc081` (the boot log's `main level : sys command 4 posted with 64 -> gain table[0] = 0xbf7fc081`) |
| frames captured, 4.064 s emulated of PLAY | 179,213 over 40 reads = **44,099.9 per emulated second** (status `captured=179213 pending=0 dropped=0`) |
| wall, playing and reading every 100 ms | 37.4 s / 36.7 s (two runs) for 4.064 s = **9.0-9.2 wall s per emulated s** (109-111 emulated ms per wall s; O14i's `--dsp` play figure was 132, before O14j's timers) |
| longest `audio read` | 0.2 ms wall (4410 frames, 35 KB of hex); the `audio read 500` split answers exactly 500 |
| first sound | frame 81 = 1.8 ms after the PLAY key (the fixture pattern has a saved trig on step 1: PART_PTR blob bytes 6/7 = `01 01`) |
| `third-0.wav` L in `pipe_main.wav` | gain-only fit over the whole sample: frame 80 x 0.7134, residual -18.5 dB -- and the batch's own `run3_core0.wav` slot 2 fits the same, x 0.7140, -18.4 dB: the DSP voice FADES IN over ~256 samples (residual rms 1666 in the first 1024 samples, 40-160 after). From sample 256 on: **x 0.7032, -33.0 dB** (pipe) vs x 0.7032, -33.2 dB (batch) -- the spike's "x 0.70" |
| pipe vs the batch reference | the pipe's 9551 samples inside `run3_core0.wav` slot 2 at frame 282745: x 1.0005, **-30.1 dB**, 481 samples bit-identical; the residual is the attack (rms 446 in the first 1024, 1-42 in the rest against a signal of 2000-7600) -- the same voice, started by the PLAY key instead of `startTransportLive` |
| 61 s emulated with no read (`--cap-test`, `frame` off) | 26 s wall; `captured=2690147 pending=2646000 dropped=44147` = cap + dropped; RSS 1010 -> 1012 MB; the 2,646,000 frames read back afterwards in 500,000-frame reads; `audio stop` frees the ring |

The first run's fit was asserted on the whole sample and failed at -18.5 dB
on BOTH the pipe and the batch; the attack is the DSP's, not the pipe's,
and the script fits from sample 256 (`ATTACK`) and finds the batch lag by
search (its first non-zero sample is one frame before the pipe's). The
second run, with those fits, passes every check (`PASS`, exit 0, 98 s wall).

### What it does not do

- Core 1 is not captured (it puts out no ESAI frames on the fixture).
- 16-bit only over the pipe; the batch's `--audio-out` keeps the 24-bit
  words, and the two capture paths are independent (`--audio-out` under
  `--interactive` still writes nothing, as before).
- Nothing paces playback to wall time: at 9 wall s per emulated s the
  client that wants sound in real time buffers (60 s of ring) and plays
  what it has.
- `out/_agents/port/smoke.py` without a card fails 7 checks on this binary
  AND on the pre-change one (the O14j boot change: `ready` at sample 9083,
  the dialog not yet drawn); the only difference here is `ready` 139
  samples later (the main-level post).

Note (verifier, 12 Sep 2026): the `--main-level` default under `--interactive`
also moves the firmware's own main-level tick on the status bar (bottom right,
cols 104-108 of rows 59-61: col 108 = off/0, 107 = 32, 106 = 64, 105 = 100,
104 = 127) and adds one panel message; `--main-level off` reproduces the
pre-change screens byte for byte. RSS grows in bursts while the sequencer
plays regardless of the audio ring (pre-existing; ~+34 MB per 2 s slice
observed) -- a long playing session has not been measured.

## Milestone O15a — event-horizon bursts: the run loop 4.2x faster, bit for bit ✅ (12 Sep 2026, branch `panel-ui`)

The first step of the speed plan (`out/_agents/speed-plan/PLAN.md`, the
architect's plan from the read-only investigation of the same day). Before
it the port played at **215-220 emulated ms per wall s** without `--dsp` and
**97** with (`out/_agents/speed/bench.py`: boot on `otlive.img`, PLAY with
`frame on`, 16 x `run 250`); real time is 1000. Nothing the firmware does
may change: the gate is `out/_agents/speed-oracle/oracle.sh` (28 checks
against the frozen pre-speed binary `out/emu/ot_emu.ref-1e76ac5`: boot
logs, serial bytes, goldens, the O14k render WAV, the `--interactive` UART
stream, peeks and run stamps, the `--dsp` pipe PCM, ctest).

### What changed (`rtos.cpp/.h`, `periph.h`, `machine.cpp/.h`; no CLI change)

The old loop called `tickTimers()` and `deliver()` after EVERY instruction:
two PIT advances, four DTIM advances, the frame edge, the eDMA's due list,
the ATA latency, then the two INTCs' `top()` -- 26 `std::function` line
probes -- to re-offer or withdraw an interrupt. Both are pure functions of
the models' state and the sample clock, so between two instructions that
change neither they are no-ops. Every way that state CAN change is now
either flagged or timed, and `Rtos::runInternal` runs **bursts**:

- `nextEvent()` = the earliest of: every armed PIT expiry (PIE or not --
  PCSR reads back PIF), every armed DTIM reference match (`DmaTimer::
  nextMatch`, new: ORRI or not -- DTER.REF reads back), `m_nextFrame` while
  `m_frame || m_frameFromDsp` (otherwise `tickTimers()` never touches it and
  the eDMA boundary it publishes is a constant), `m_ataIrqDue`, and
  `Edma::nextDue` (new) -- the earliest booked completion, gated ones
  included: a due-but-gated entry answers a sample already past, which
  forces exact stepping until it clears, because the gate is the DSP's ring
  and is re-asked after every instruction.
- A burst is up to N = min(4096, floor((nextEvent - m_sample) * ips) - 2,
  the run's end) instructions doing only stepOnce's own per-instruction work
  (the PC ring when armed, the create record at `g_create`, the dispatch
  record after the scheduler's `rte`, the first-handoff record, `m_sample +=
  1/ips` -- the same add in the same order, so every stamp is bit-identical
  -- and `Machine::stepFast()`); `tickTimers()` + `deliver()` once at its
  end. The exact tail then steps instruction by instruction across the
  event, so every timer fires on the same instruction as before.
- A burst ends early on: any peripheral access (`Machine::peripheralRead/
  Write` set `m_periphTouched` -- the models, the boot's override table, the
  card window, the co-processor), an interrupt acknowledgement (the ack
  hook sets `m_wake`: the core consumed the injected vector and `deliver()`
  must re-offer or withdraw), the DSP's host-word hook (`m_wake`: a frame
  edge the horizon cannot see), the PC landing on main's spin after at
  least one instruction (the idle skip gets its look, as the old loop
  checked before every instruction), and `runInternal` entry (`m_wake =
  true`: keys pushed, memory poked, the frame switched between runs are
  delivered after the run's first instruction, as before). An early end is
  always exact -- it calls the pair where the old loop called it too.
- **The list of everything that can change deliverable state** (beside
  `Intc::addLine` in the constructor, as the plan asked; also the comment on
  `Rtos::nextEvent`): a peripheral WRITE (INTC masks/forces/ICRs, PIT and
  DTIM control and acks, eDMA kicks/CINT/CDNE, UART masks, the card's
  command and data registers, the DSPI, the host port) -> touched; a
  peripheral READ with a side effect (UART +0x0c pops the receive queue
  and its line, the card's STATUS clears INTRQ and a DATA read re-arms it,
  DSPI POPR, the host port) -> touched; the CPU acknowledging a vector ->
  wake; the DSP's bank word -> wake; the outside world between runs -> wake
  at entry; TIME (the six sources above) -> the horizon. The DSP cores'
  state reaches the ColdFire only through the host port (a read), the
  eDMA gate (a due entry) and the host-word hook; their per-instruction
  tick is unchanged inside a burst, so the O12 interleave does not move.
- **The mandatory fix the architect found in the prototype**: `Edma::
  setBoundary(m_nextFrame)` and `setNow(m_sample)` are refreshed before
  EVERY eDMA register access in BOTH `Rtos::peripheralRead` and
  `peripheralWrite`. The prototype did it on reads only; a CSR.START kick
  is a WRITE and `Edma::start` books its bus-paced completion from
  `m_now`, so with `m_now` stale by up to a burst the DSP took its block
  early and the `--dsp` pipe PCM drifted by +-1 LSB in 280 samples
  (`interdsp.pcm` FAILED, 27/28). The refreshed value is the exact path's:
  `tickTimers()` set it from `m_sample` after the previous instruction, and
  `m_sample` has not moved since (the increment follows the instruction).
- `Machine::stepFast()`: `step()` minus three costs -- the PC through
  `m68k_get_reg` (now `pcFast()`: a pointer to the CPU state's `pc` field,
  taken once at construction; measured at ~12 % of the burst loop's samples
  when it was an out-of-line call into an out-of-line `getCpuState()` three
  times per instruction), the opcode through the region walk (the SDRAM
  region's bytes are read directly while the PC is inside it; `read16`
  otherwise, so the alias window, a grown page and a PC in a peripheral
  behave as before), and `Mc68k::exec()`'s legacy GPT/SIM/QSM pass
  (`execInstruction()` runs the core alone). ⚠️ That last one is exact
  only because the legacy models are unreachable on this machine: they are
  addressed through `Mc68k::read*/write*`, which `Machine` overrides
  wholesale and never forwards to, so TMSK1 stays 0, PITR is never written
  and there is no SCI/QSPI traffic -- none of them can ever inject an
  interrupt (documented in `machine.h`; `OT_STEPFAST=0` is the bisect knob
  and the architect's bisect of the prototype cleared it). The instruction
  count, PC watch, profile, A-line pre-decode into the V4e layer and the
  co-processor's one tick per instruction are kept exactly.
- **Kept exact per instruction**: `run(_ms, untilGate=true)` (the batch's
  M6a gate), `runUntil` (the render's frame predicate, `runToPc`),
  `callAsMain`, `runToMainSpin`, `loadProjectLive`'s mount wait -- plan
  step 6 extends them. The bursts apply to `run(_ms, false)`: every
  `--interactive` `run`, and the load's own 6 s run.
- **Knobs and stats, stderr only, opt-in** (the batch stdout is diffed byte
  for byte): `OT_BURST=<quantum>` (default 4096; `0` = the pre-O15a loop),
  `OT_STEPFAST=0` (`Machine::step` inside bursts), `OT_BURST_STATS=1`
  (one `burst stats:` line on stderr when the `Rtos` is destroyed:
  bursts, instructions inside them, exact instructions, how bursts ended
  -- periph / wake / horizon / spin -- idle skips, the instruction count,
  the knobs). `Rtos::burstStats()` exposes the same counters. No command,
  reply or log line changed.

### Measured (12 Sep 2026, the M5 Mac of O14j, macOS 26.5; logs under `out/_agents/impl-1-bursts/`)

Reference = `out/emu/ot_emu.ref-1e76ac5`, candidate = this tree, both run
in the same session with no other emulator running (an unrelated 25-day-old
Python process at 100 % of one core was present throughout, as it was for
the baselines). `bench.py` unless said otherwise.

| measurement | reference | candidate | ratio |
|---|---|---|---|
| play, no `--dsp` (emulated ms per wall s) | 217, 220, 215 | **904, 913** (920 with `bench_cmp.py`, no `sample` profiler) | **4.2x** |
| play, `--dsp` | 97 | **132** (138 with `bench_cmp.py`) | **1.36-1.42x** |
| boot + fixture load to `ready`, no `--dsp` | 39.2-39.3 s | **10.3-10.5 s** | 3.8x |
| boot + fixture load to `ready`, `--dsp` | 58.2 s | **28.8-29.0 s** | 2.0x |
| oracle `card` batch (`--ms 1000`, under the parallel battery) | 49.6 s | 12.0 s | 4.1x |
| oracle `render` (O14k reference, 3000 frames `--dsp`) | 91.1 s | 41.7 s | 2.2x |
| oracle `inter` boot / 4490 ms of `run` | 45.4 s / 21.1 s | 11.9 s / 4.6 s | 3.8x / 4.6x |
| oracle `interdsp` boot / `run` | 68.4 s / 52.6 s | 30.8 s / 31.4 s | 2.2x / 1.7x |

The first pass (before `pcFast` was inlined) measured 709-766 no-dsp and
130-134 `--dsp`, ready at 12.2-12.7 s; the second pass is what ships. The
`--dsp` figure is the plan's ceiling for this step: the cores are ~50 % of
the wall and their interleave cannot change (O12).

**The gate: 28 PASS, 0 FAIL, twice** (`out/_agents/speed-oracle/reports/
20260912-070039-impl1-bursts.txt` for pass 1, `20260912-071233-impl1-bursts-b.txt`
for pass 2): boot logs identical, `serial_a` 5731 / 9257 bytes identical,
goldens 12,757 / 26,367 bytes identical, `run3_core0.wav` 7,936,292 bytes
identical, the UART A stream 18,297 / 18,309 bytes identical step by step,
109 peeks identical, 47 run stamps with max |dsample| = 0 and |dframes| =
0, **`interdsp.pcm` 497,788 bytes identical**, ctest 7/7 with the tests
built in the candidate tree (`out/_agents/impl-1-bursts/build`, `rtos`
run from the repo root). `bench_cmp.py`'s fingerprint (every reply, the
whole UART stream hashed, the peeks, a MIXER key pushed in the middle of
play for the rxPush-then-wake path) is IDENTICAL to the pre-speed control's
(`out/_agents/speed-cpu/fp_control_rtc.fp.json`, `fp_dsp_control_rtc.fp.json`),
with and without `--dsp`.

**What the bursts do** (`OT_BURST_STATS=1`, the whole `bench_cmp.py`
session, boot + 4 s of play): without `--dsp`, 14,258,883 bursts covered
1,084,851,615 of 1,146,675,308 instructions (94.6 %; the rest is the boot
before the handoff and the load's borrowed calls), 11,500 went through the
exact tails; bursts ended on a peripheral access 13,810,549 times (96.9 %),
a wake 167,644, the horizon 227,059, main's spin 53,631; 53,665 idle
skips. **The mean burst is 76 instructions**: the firmware polls its
peripherals constantly (UART status, INTC IPR, DTIM3's timestamp, the host
port), and every such read ends a burst. With `--dsp`: 14,171,369 bursts,
1,051,929,979 instructions, and 32,933,949 exact instructions (2.9 %) --
the gated eDMA completions holding the exact path while the DSP drains.
The counts are identical between the two passes (the loop restructure
changed no decision).

### What it does not do

- A side-effect-free peripheral read (a status poll, an IPR read, a DTCN
  timestamp) still ends the burst; letting those through needs a
  per-register classification and is not in the plan.
- The gated, predicate and borrowed-call runs are still exact per
  instruction (plan step 6); memory access still walks the region list
  (step 3); no LTO/PGO (steps 2, 4).
- Nothing paces playback to wall time (plan step 7); at 0.9x real time the
  panel's idle pump and `run`s simply finish sooner.
- Route A is not the oracle here; the 28 checks are port-vs-port against
  the frozen binary, as the speed-oracle README says.

## Milestone O15b — link-time optimisation: +11 % on top of the bursts, bit for bit ✅ (12 Sep 2026, branch `panel-ui`)

Step 2 of the speed plan; `tools/emu/ot_emu/CMakeLists.txt` only, no
source change. The run loop crosses a library boundary on every
instruction (`m68kops.c` -> `m68k_read_memory_*` -> `Machine::read*`,
Musashi's `execute_one` -> `Machine::stepFast`), and a per-translation-unit
build cannot inline across it. The speed-mem investigation measured full
`-flto` at 1.17x on the pre-burst code; this lands it in the build.

**What changed.** `option(OT_LTO ON)` + `OT_LTO_MODE full|thin` (cache
string), set BEFORE the `add_subdirectory` calls so the vendored targets
inherit it: `include(CheckIPOSupported)` / `check_ipo_supported(LANGUAGES C
CXX)` guards it (a toolchain without LTO builds as before and says so at
configure), then `CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE` (and
`_RELWITHDEBINFO`) `ON`. CMake's AppleClang default for IPO is
`-flto=thin` (`Modules/Compiler/Clang.cmake`); in `full` mode
`CMAKE_C/CXX_COMPILE_OPTIONS_IPO` are overridden to `-flto` so the whole
program is one module at link time, as the investigation measured. Verified
on the verbose build (`out/_agents/impl-2-lto/build.build.log`): `-flto` on
109 of 167 compile lines -- all of `68kEmu` (`m68kops.c`, `m68kcpu.c`,
`mc68k.cpp`, ...), `dsp56kEmu`, `dsp56kBase`, `ot_machine`, `ot_emu` and
the test programs -- and on every link line (`-O3 -DNDEBUG -flto -arch
arm64 ... -o ot_emu`). The 58 without it are `asmjit` (its own
`cmake_minimum_required(VERSION 3.5)` leaves policy CMP0069 OLD in that
subtree, so the property is ignored there; asmjit is the JIT's assembler and
is not on the interpreter's path). `OT_LTO=OFF` reproduces the pre-change
build exactly (no `-flto` anywhere, `ot_emu` 2,789,704 bytes as before) --
the bisect knob. Default `cmake --fresh -B out/emu -S tools/emu/ot_emu &&
cmake --build out/emu -j8` now builds with full LTO; nothing else changed:
no CLI, command, reply or log line.

**Measured** (12 Sep 2026, the same M5 Mac, macOS 26.5, AppleClang 21.0.0,
CMake 4.4.3; `bench.py`, all four binaries in one session, one after the
other, no other emulator running; logs `out/_agents/impl-2-lto/impl2_*.log`,
`bench_series.txt`). Reference = `out/emu/ot_emu.ref-1e76ac5`; Step 1 =
this tree at commit `cada0f2`, rebuilt as `build-pre` with `-DOT_LTO=OFF`;
LTO = `build` (full); thin = `build-thin` (`-DOT_LTO_MODE=thin`).

| measurement | reference | Step 1 (no LTO) | **full LTO** | thin LTO |
|---|---|---|---|---|
| play, no `--dsp` (emulated ms per wall s) | 217 | 879, 874 | **977, 977** | 978, 976 |
| play, `--dsp` | 96 | 132, 135 | **147, 147** | 144 |
| boot + fixture load to `ready`, no `--dsp` | 39.5 s | 10.4-10.7 s | **9.8-10.1 s** | 9.6-9.7 s |
| boot + fixture load to `ready`, `--dsp` | 60.9 s | 28.6-29.0 s | **26.7-27.4 s** | 26.9 s |
| `ot_emu` size (bytes) | 2,788,712 | 2,789,704 | **2,273,512** (-18.5 %) | 2,384,360 |
| link step of `ot_emu` alone (re-link, warm) | -- | 0.35-0.56 s | **5.4-5.7 s** | 1.8-2.1 s |
| full `--fresh` configure + build, `-j8` | -- | 15.3-16.7 s | **19.3 s** | 16.7 s |

Ratios: full LTO over Step 1 **1.115x** without `--dsp` (977 / 876.5) and
**1.10x** with (147 / 133.5) -- the plan asked for >= 1.08x on the burst
binary and estimated >= 1.1x; over the reference in the same session
**4.5x** (977 / 217) and **1.53x** (147 / 96). Thin LTO ties without the
cores (976-978) and is 2 % slower with them (144 vs 147) for a 3x faster
link; full stays the default, as the plan says. The `--dsp` gain is
smaller, as O15a's was: the cores are ~50 % of the wall (speed-dsp's
profile) and their interleave cannot change, so only the ColdFire half
speeds up. `lib68kEmu.a` grows 1,097,752 -> 1,500,856 bytes (bitcode, not
machine code, until the final link).

**The gate: 28 PASS, 0 FAIL** (`out/_agents/speed-oracle/reports/
20260912-075538-impl2-lto.txt`, copy in `out/_agents/impl-2-lto/oracle1-
report.txt`; 57 s wall): boot logs identical, `serial_a` 5731 / 9257 bytes
identical, goldens 12,757 / 26,367 bytes identical, `run3_core0.wav`
7,936,292 bytes identical, the UART A stream 18,297 / 18,309 bytes
identical step by step, 109 peeks identical, 47 run stamps with max
|dsample| = 0 and |dframes| = 0, `interdsp.pcm` 497,788 bytes identical,
ctest 7 / 7 in the LTO tree (also 7 / 7 in `build-pre` and `build-thin`).
Under the parallel battery the candidate's jobs took: `card` 10.6 s (Step 1:
12.0 s), `render` 37.7 s (41.7), `inter` boot 10.9 s + 4.2 s of `run` (11.9
+ 4.6), `interdsp` 28.0 s + 28.2 s (30.8 + 31.4). Floating-point results
did not move: the DSP pipe PCM and the render WAV are the sensitive
outputs, and both are byte-identical (`-flto` adds no fast-math or
reassociation flag; the compile lines are otherwise unchanged).

**What it does not do.** No PGO yet (step 4 of the plan adds
`OT_PGO_PROFILE=<path>` in this file); no page-table memory path (step 3);
asmjit is not LTO'd (not needed -- the JIT is not used). Every build now
pays 5-6 s at the link for `ot_emu` and again for each test program (four
in this tree, three in `vendor/mc68k`); a tree that iterates on one source
file re-links everything through LTO -- configure with `-DOT_LTO=OFF` for
that. Debug builds (`-DCMAKE_BUILD_TYPE=Debug`) are untouched. Route A and
the panel server are not affected (the server builds `out/emu` with the
default configure, so it gets the LTO binary).

**Verified** (`out/_agents/impl-2-lto-verify0/`, the same Mac, later the
same morning): a fresh default configure + build reproduces the builder's
`ot_emu` byte for byte (sha256 `b25c12b6…`, 2,273,512 bytes; LLVM bitcode
in every `68kEmu`, `dsp56kEmu`, `dsp56kBase`, `ot_machine` and `ot_emu`
object, Mach-O only in asmjit); a build from a source copy with the
`option(OT_LTO ...)` line commented out reproduces the Step 1 binary byte
for byte (2,789,704 bytes, no `-flto`); a Debug configure carries `-flto`
in nothing but CMake's own IPO probe. Oracle rerun with `--fresh` (both
sides re-executed): **28 PASS, 0 FAIL**, 111 s. Bench, serial, in one
session: no `--dsp` reference 218, no LTO 942 / 911, **LTO 1027 / 1010**
(1.10x, 4.7x the reference; ready 10.3-10.4 s -> 9.4 s); `--dsp` reference
99, no LTO 135 / 136, **LTO 147 / 148** (1.09x, 1.49x; ready 28.3-28.7 s ->
26.4-26.5 s). ctest 7 / 7 in both trees; the vendored `dsp56kTestRunner`
(EXCLUDE_FROM_ALL) also links under LTO.

## Milestone O15c — the page-table memory fast path: +28 % on top of bursts + LTO, bit for bit ✅ (12 Sep 2026, branch `panel-ui`)

Step 3 of the speed plan; `machine.h` / `machine.cpp` only, no CLI change.
Every memory access the core makes -- the opcode fetch, the immediates,
every operand read and write -- went through `Machine::read*`/`write*`,
and each of those walked the region list: `isPeripheral` over three
windows, the `alias()` fold, then `find` over up to twelve `Region::
contains` checks (the boot map's six plus the six `Rtos::install` and
`mapCardMemory` add). The speed-mem investigation clocked that at 2.6 ns
per opcode fetch and 15-31 ns per data access on the pre-burst binary and
found the memory callbacks at 28 % of the burst loop's samples; its
prototype (`out/_agents/speed-mem/patch_fast.py`) measured 1.11x on the
pre-burst code with the oracle at 28 PASS. This lands it.

### What changed (`machine.h/.cpp`)

- **`m_pages`**: one host pointer per 4 KB page of the 4 GB address
  space (1 << 20 entries, 8 MB), rebuilt by the constructor after the
  boot map is laid out and by every `mapRegion`. The inline `read8/16/32`,
  `readImm16` and `write8/16/32` in the header index it with `addr >> 12`
  and, when the entry is non-null, read or write the bytes in place
  (`memcpy` + `bswap`, big-endian as the region bytes are). `stepFast`'s
  O15a special case -- the SDRAM region's bytes read directly while the
  PC was inside it -- is gone: the inline `read16` IS that direct read,
  now for every page in the table.
- **The rule for an entry, `find`'s page by page** (`rebuildPageTable`):
  the FIRST region in list order that touches a page claims it, and the
  page gets an entry only if that region covers all of it -- so no
  non-straddling access inside the page could resolve to any other
  region. A region that covers a page only partly claims it with NO
  entry, and a later region cannot take it: `find` would have answered
  the earlier one for the bytes both hold. (On this machine's map no two
  regions overlap, so the rule reduces to "one region covers the page";
  it is written the conservative way so a future `mapRegion` cannot make
  the table disagree with `find`.)
- **Never in the table**: a peripheral window (`isPeripheral` is checked
  per page; `assert`ed, and the guard leaves the entry null in a Release
  build regardless), a page this machine grew on its own (every access
  to one goes through `noteUnmapped`, whose count is in the boot log and
  in `test_rtos` -- so a grown page must keep taking the slow body), and
  anything unmapped.
- **The alias window** 0x48000000-0x4fffffff: the first pass skips the
  two regions mapped inside it (the boot's 1 MB at 0x48000000 and the
  card's 127 MB at 0x48100000 -- unreachable since 9 Sep 2026, because
  `alias()` folds every access out of the window before `find` runs), the
  second pass copies each alias page's entry from the page 0x08000000
  below. A write through 0x4fxxxxxx lands in the 0x47xxxxxx bytes as
  before; the O6 ring-clear and loader findings hold.
- **Everything else takes the ORIGINAL body, renamed `*Slow`**: a null
  entry (peripheral, grown, unmapped), an access that straddles a page
  (`(a & 0xfff) > 0xffe` for 16-bit, `> 0xffc` for 32-bit -- `find`
  answers those with the region-end and auto-map rules, so they are left
  to it), and every write while a write watch is armed (`addWriteWatch`
  sets `m_writeSlow`; watches are never removed). The Slow bodies are the
  pre-O15c code line for line: `isPeripheral` -> `peripheralRead/Write`
  (with `m_periphTouched`, the logs, the trace, the host-port log), the
  fold, `find`, `noteUnmapped`, the auto-map with its limit and
  `badWrite`. The only edit inside them is that `++m_writes` (the boot's
  stall detector's store counter) moved into the inline wrappers, their
  sole callers, so it counts every write as before.
- `readImm16` is the inline `read16` (it was an out-of-line call to it).
  `fastPages()` reports the table's population; nothing prints it.

### Checked beyond the gate (`out/_agents/impl-3-memory/pagecheck.cpp`)

A standalone program against the built `libot_machine.a`: the boot map,
then `Rtos::install`'s two spans, then `mapCardMemory`'s four, checking
the table's population against the map by hand (36,896 pages after the
boot map = 20,512 region pages + 16,384 alias copies; 37,393 after
install; **70,401** with the card: 37,633 + 32,768 -- the whole
0x40000000-0x47ffffff span is then covered, so every alias page has an
entry), then 2,000,000 random accesses over every region and through the
alias, one in seven placed to straddle a page: `read8/16/32` equal to
`read8/16/32Slow` at every one, writes through the inline path read back
through the Slow body and vice versa, the PLL override whole through
`read32` and `read16`, an unmapped address counted on every access and
grown once (and not entering the table), a write watch hit through the
inline wrappers including via the alias. **0 mismatches.**

### Measured (12 Sep 2026, the same M5 Mac, macOS 26.5, AppleClang 21.0.0; logs under `out/_agents/impl-3-memory/`)

`bench.py`, all three binaries in one session, interleaved, one after the
other, no other emulator running (`bench_series.sh` -> `bench_series.txt`,
`impl3_*.log`). Reference = `out/emu/ot_emu.ref-1e76ac5`; Step 2 = this
tree at commit `3055b36` (bursts + LTO), built here as `build-pre` before
the patch landed; Step 3 = `build`. The `ready sample=277688.167` stamp is
identical in all ten runs.

| measurement | reference | Step 2 (bursts + LTO) | **Step 3 (+ page table)** |
|---|---|---|---|
| play, no `--dsp` (emulated ms per wall s) | 221 | 1026, 984 | **1292, 1287** |
| play, `--dsp` | 99 | 144, 148 | **150, 149** |
| boot + fixture load to `ready`, no `--dsp` | 39.0 s | 9.8-9.9 s | **7.4 s** |
| boot + fixture load to `ready`, `--dsp` | 57.5 s | 26.5-27.7 s | **25.1-25.7 s** |
| `ot_emu` size (bytes) | 2,788,712 | 2,273,512 | 2,522,328 |

Ratios: Step 3 over Step 2 **1.28x** without `--dsp` (1289.5 / 1005) --
the plan asked for >= 1.12x and estimated 1.15-1.25x on the burst binary
-- and **1.02x** with (149.5 / 146); over the reference **5.8x** (1289.5 /
221) and **1.51x** (149.5 / 99). Playback without the cores is now
**1.29x real time** (a 16th at 120 BPM in 0.097 s wall). The `--dsp` gain
is exactly the ColdFire's share: at Step 2 an emulated second with the
cores costs 6.85 wall s, of which the ColdFire side is ~1.0 s (the no-dsp
figure) and the two DSP interpreters the rest; cutting the ColdFire side
by 1.28x predicts 151 emulated ms per wall s, measured 149.5. The cores'
interleave cannot change (O12), so the `--dsp` figure stays bounded by
them, as O15a and O15b said. Ready is 1.33x faster (the boot and the
6 s load are the same instructions, now with the cheaper fetch).

**The gate: 28 PASS, 0 FAIL** (`out/_agents/speed-oracle/reports/
20260912-082136-impl3-memory.txt`, copy in `out/_agents/impl-3-memory/
oracle1-report.txt`; 55 s wall): boot logs identical, `serial_a` 5731 /
9257 bytes identical, goldens 12,757 / 26,367 bytes identical,
`run3_core0.wav` 7,936,292 bytes identical, the UART A stream 18,297 /
18,309 bytes identical step by step, 109 peeks identical, 47 run stamps
with max |dsample| = 0 and |dframes| = 0, `interdsp.pcm` 497,788 bytes
identical, ctest 7 / 7 in the candidate tree (also 7 / 7 standalone,
`ctest.txt`). Under the parallel battery the candidate's jobs took:
`card` 8.9 s (Step 2: 10.6 s; reference 46.1 s), `render` 36.9 s (37.7),
`inter` boot 8.6 s + 3.2 s of `run` (10.9 + 4.2), `interdsp` 26.5 s +
27.7 s (28.0 + 28.2). The build carries only the vendored `-Wswitch`
warnings; nothing from `machine.*`.

Verified independently (`out/_agents/impl-3-memory-verify0/`): a fresh
build of this tree (byte-identical binary, sha `e298c877…`) and of commit
`3055b36` in the same session, `bench.py` interleaved with nothing else
running: no `--dsp` reference 223, Step 2 1008 / 1004, **Step 3 1375 /
1312** (1.34x); `--dsp` 99, 147 / 149, **151 / 151** (1.02x); ready
7.2-7.4 s vs 9.6-10.0 s, the stamp identical in all ten runs. Oracle
`--fresh` (both sides rerun) 28 PASS / 0 FAIL (`reports/20260912-084127-
impl3-verify0.txt`); ctest 7 / 7; an adversarial program over the table
(`adv.cpp`, 175 checks: every peripheral page null and every access to the
three windows reaching `peripheralRead/Write`, the overrides, both alias
ends, region-end and in-region straddles proven slow by swapping the page
entry for a scratch buffer, grown pages counted per access and entering the
table only through `mapRegion`, a later region overlapping an earlier one
losing the shared pages, write watches through the wrappers and the alias)
0 failures; the `watchmem` / `writes` / `watch` / `hits` / `poke` replies
of a YES-PLAY-STOP script byte-identical to the reference's (312 lines).

### What it does not do

- A grown (auto-mapped) page is still a slow access, by design: its
  per-access count is a finding, not overhead. With a card attached the
  golden path makes 4 such accesses at three boot-time addresses (O7:
  `0x04020000`, `0x100a0000`, `0xffff0000`), so nothing on the oracle's
  path is left slow except the peripherals and those four.
- A peripheral access is exactly as expensive as before, and still ends
  the burst (O15a).
- No PGO (step 4), no `m68k_execute(N)` hook (step 5); the gated and
  predicate runs are still exact per instruction (step 6).
- The table is rebuilt whole on every `mapRegion` (8 MB written, seven
  times per boot: the constructor and the six `mapRegion`s); it costs
  nothing measurable and keeps the rule in one place.

## Milestone O15d — profile-guided optimisation, opt-in: +28 % on top of bursts + LTO + page table, bit for bit ✅ (12 Sep 2026, branch `panel-ui`)

Step 4 of the speed plan; `tools/emu/ot_emu/CMakeLists.txt` and a new
`tools/emu/ot_emu/pgo.sh`, no source change, no CLI change. The compiler
already inlines across the whole program (O15b) and the memory path is a
table lookup (O15c); what it still guesses at is which branches are hot --
which of Musashi's 1,968 opcode handlers the firmware actually runs, which
of `DspPair::stepCore`'s paths the cores take, where `Rtos::runInternal`'s
burst loop leaves. A profile answers that. The speed-mem investigation
measured +12 % over LTO on the pre-burst code (1.31x vs 1.17x) and 1.33x
with `--dsp` on its own fast+LTO prototype; this lands the build option and
the ritual that makes the profile.

### What changed (`CMakeLists.txt`, `pgo.sh`)

Two cache knobs, both OFF by default, set BEFORE the `add_subdirectory`
calls so the vendored cores inherit them (directory-scoped
`add_compile_options` / `add_link_options`; nothing here names a vendored
target):

- `-DOT_PGO_GENERATE=ON`: `-fprofile-instr-generate` on every compile and
  link line (verified on the verbose build: 167 of 167 compile lines, the
  `ot_emu` link line). The binary writes an LLVM `.profraw` to
  `$LLVM_PROFILE_FILE` when `main` returns -- `quit`, EOF on stdin, the end
  of a batch run (`main.cpp` returns, it never `_exit`s).
- `-DOT_PGO_PROFILE=<.profdata>`: `-fprofile-instr-use=<path>` on every
  compile and link line (167 / 167 and the links), FATAL_ERROR if the file
  does not exist, exclusive with GENERATE. Clang's per-file noise is
  silenced -- `-Wno-profile-instr-unprofiled` (a file the training never
  ran: 40 lines, all asmjit, the DSP JIT `jit*.cpp`, the vendored unit
  tests, `test_emac` / `test_dsp`) and `-Wno-backend-plugin` (the
  per-function "hash mismatch" of a stale profile) -- while
  `-Wprofile-instr-out-of-date` stays visible: one line per file whose
  functions no longer match the profile, i.e. "regenerate". With a FRESH
  profile 22 such lines remain and are expected: the 19 `jit*.cpp` files
  (header-defined functions the JIT shares by name with the interpreter and
  compiles differently; the JIT is never run) and the test programs whose
  `main` collides with `ot_emu`'s. `-DOT_PGO_WARNINGS=ON` shows all three
  (the acceptance check of the plan: NO `profile-instr-unprofiled` on
  `machine` / `rtos` / `periph` / `v4e` / `dsp` / `card` / `main` /
  `m68kops` / `m68kcpu` / `mc68k` -- verified on a `-j1 --verbose` build,
  `out/_agents/impl-4-pgo/build-warn.build.log`).
- `-fprofile-instr-use` changes code placement only -- inlining decisions,
  block layout, branch weights, register allocation hints; it adds no
  fast-math, no reassociation, no flag the DSP's floating point could see.
  The oracle is run on the result all the same (below).

`tools/emu/ot_emu/pgo.sh` is the whole ritual in one command (`bash
tools/emu/ot_emu/pgo.sh`, 2 min 19 s wall on this Mac): (1) the
instrumented build into `out/emu-pgo-gen` (23 s); (2) two training runs on
that binary with `LLVM_PROFILE_FILE=out/emu-pgo/raw/<tag>-%p.profraw` --
the `bench.py` sequence (boot on `out/_agents/port/otlive.img` with the
OTLIVE project, `frame on`, PLAY, 16 x `run 250`, `quit`), once without
the cores (ready 12.9 s + 4.7 s of play, instrumented) and once with
`--dsp` plus `audio start main` / `audio read` after every run so the pipe
path is in the profile too (38.6 s + 37.7 s); (3) `llvm-profdata merge`
(found via `xcrun -f llvm-profdata`, overridable with `LLVM_PROFDATA=`)
into `out/emu-pgo/ot_emu.profdata` -- 2 x 944,272 B raw, 1,443,552 B
merged, 9,819 functions, 26,091 blocks, 3.28 x 10^11 counts; (4) the
optimised build into `out/emu` with `-DOT_PGO_PROFILE=` (18 s), which is
the operator's binary: the panel server picks it up unchanged. Options:
`--dest` / `--gen` / `--prof` / `--card` / `--image`, `--no-dsp` (skip the
long training run), `--skip-train` (rebuild with the profile on disk),
`--clean-gen` (the instrumented tree is kept by default so a different
training sequence needs no rebuild), `-j N`. It refuses to finish with an
instrumented binary in `--dest` (`otool -l` for `__llvm_prf_*`) or one of
the wrong architecture, and it refuses to finish with an object in
`--dest` older than the profile (the re-run finding below).

**The profile is build-host specific and goes stale.** It belongs to this
compiler (AppleClang 21.0.0), these flags and the source bytes it was
collected on; clang matches it function by function by name and CFG hash.
After ANY change under `tools/emu/ot_emu` or `vendor/{mc68k,dsp56300}`
run `pgo.sh` again (re-running it into a tree that already holds a PGO
build is the normal case and is safe -- since fix1 below; the first
version of the script failed exactly there) -- a function whose hash
moved simply gets no weights, so a stale profile loses speed and nothing
else. That claim was measured,
not assumed: `out/_agents/impl-4-pgo/build-stale` is this tree built with
the speed-mem investigation's profile from the PRE-BURST source (its
`src-fast` copy of `1e76ac5` + the page-table patch, collected on a
different binary hours earlier: `speed-mem/pgo/fastlto-both.profdata`).
Clang reported 25 out-of-date files (the 22 above plus `machine.cpp`,
4 of 82 functions mismatched, `rtos.cpp`, 1 of 128, and `main.cpp`, 1 of
132 -- the page table, the burst loop and the interactive loop are what
changed since; `periph`, `v4e`, `dsp`, `card` still match); the binary is
2,333,704 bytes; **oracle 28
PASS, 0 FAIL** (`reports/20260912-090429-impl4-stale.txt`, ctest 7 / 7);
bench 1500 / 161 -- still +17 % / +7 % over LTO because Musashi's handlers
and the DSP interpreter did not change, 9 % / 6 % short of the fresh
profile. Nothing is committed: the profile lives under `out/` with the
binaries, and a default configure (no option) reproduces the O15c binary
byte for byte (sha `e298c877…`, 2,522,328 bytes, `build-lto`).

### Measured (12 Sep 2026, the same M5 Mac, macOS 26.5, AppleClang 21.0.0; logs under `out/_agents/impl-4-pgo/`)

`bench.py`, all binaries in one session, interleaved, one after the other,
nothing else running (`bench_series.sh` -> `bench_series.txt`,
`impl4_*.log`). Reference = `out/emu/ot_emu.ref-1e76ac5`; LTO = the
default configure of this tree (= O15c, `build-lto`); **PGO** =
`out/emu/ot_emu` as the first `pgo.sh` left it (sha `2d75bde6…`,
2,351,544 bytes, -6.8 % vs LTO; the binary in `out/emu` now is the one
fix1's re-run left, sha `1634501f…`, same size, same speed -- its own
series is below). The `ready sample=277688.167` stamp is identical in all
fourteen runs.

| measurement | reference | LTO (O15c) | **PGO + LTO** | stale profile |
|---|---|---|---|---|
| play, no `--dsp` (emulated ms per wall s) | 220 | 1336, 1239 | **1665, 1619** | 1500 |
| play, `--dsp` | 100 | 151, 151 | **171, 171** | 161 |
| boot + fixture load to `ready`, no `--dsp` | 39.0 s | 7.9, 7.4 s | **6.6, 6.8 s** | 6.9 s |
| boot + fixture load to `ready`, `--dsp` | 57.8 s | 24.9, 25.0 s | **18.4, 18.5 s** | 19.7 s |
| `ot_emu` size (bytes) | 2,788,712 | 2,522,328 | **2,351,544** | 2,333,704 |

Ratios: PGO over LTO **1.28x** without `--dsp` (1642 / 1287.5; the plan
asked for >= 1.10x and a landing >= 1000) and **1.13x** with (171 / 151);
over the reference **7.5x** (1642 / 220) and **1.71x** (171 / 100).
Playback without the cores is **1.64x real time** (a 16th at 120 BPM in
0.075-0.077 s wall; O15c: 0.097 s); with the cores 0.17x, exactly the
plan's "~0.17-0.20x" landing for the exact single-thread design (O12: the
cores' interleave cannot change, and their interpreters are what the
profile speeds up here -- the ColdFire half is now < 0.7 s of the 5.85 wall
s an emulated second costs with `--dsp`). Ready with `--dsp` is 1.35x
faster (25.0 -> 18.45 s: the DSP boot and the sample load are core-bound),
without 1.14x. The instrumented binary itself (5,926,040 bytes) runs at
848 / 106 -- 0.66x / 0.70x of LTO -- which is why it lives in
`out/emu-pgo-gen` and never in `out/emu`.

Profile-to-profile variance: a second, independent training run (below,
the Rosetta accident) produced a profile of 9,840 functions and a
different binary (2,360,440 bytes, sha `724c5763…`) that benches 1652 /
176 -- the same speed within noise. The same profile always gives the
same bytes: three trees (`-j1` verbose, `-j8`, `OT_PGO_WARNINGS` on and
off) were byte-identical, and a fresh configure from the final profile
(`build-pgo3`) reproduces `out/emu/ot_emu` byte for byte.

**The gate: 28 PASS, 0 FAIL** on the PGO binary (`out/_agents/speed-
oracle/reports/20260912-091014-impl4-pgo-arm64.txt`, copy in
`out/_agents/impl-4-pgo/oracle_pgo_arm64.txt`; 49 s wall): boot logs
identical, `serial_a` 5731 / 9257 bytes identical, goldens 12,757 /
26,367 bytes identical, `run3_core0.wav` 7,936,292 bytes identical, the
UART A stream 18,297 / 18,309 bytes identical step by step, 109 peeks
identical, 47 run stamps with max |dsample| = 0 and |dframes| = 0,
`interdsp.pcm` 497,788 bytes identical, ctest 7 / 7 in `out/emu`. Under
the battery (with a build running alongside) the candidate's jobs took:
`card` 8.25 s (O15c: 8.87 s; reference 44.2 s), `render` 31.7 s (36.9),
`inter` boot 8.2 s + 3.0 s of `run` (8.6 + 3.2), `interdsp` 23.6 s +
24.5 s (26.5 + 27.7). The plain binary: 28 PASS (`…-090416-impl4-plain.
txt`; its bytes are O15c's verified binary, so the oracle served the
candidate side from its cache and re-ran ctest, 7 / 7 in `build-lto`).
The stale-profile binary: 28 PASS, as above.

**The Rosetta accident, kept as a finding.** The first run of `pgo.sh`
was started as `bash tools/emu/ot_emu/pgo.sh` from a shell whose PATH has
the Intel Homebrew's `/usr/local/bin` ahead of `/bin`: that `bash` is an
x86_64 binary, runs under Rosetta, and every child (cmake, clang) inherits
the translation, so clang targeted x86_64 by default and the whole ritual
-- instrumented build, training, optimised build -- produced an **x86_64
`ot_emu`** (`-arch x86_64` in `flags.make`, 2,426,472 bytes). It passed the
oracle, 28 / 28 (`…-090149-impl4-pgo.txt`: the render WAV and the pipe PCM
byte-identical across two instruction sets -- the port's arithmetic does
not depend on the host ISA), but it is not the operator's binary. `pgo.sh`
now re-executes itself under `arch -arm64 /bin/bash` when
`sysctl.proc_translated` says it is translated, and checks `lipo -archs`
of both builds against `uname -m`. The re-run through the same
`bash pgo.sh` invocation is the binary measured above. (CONTEXT.md's
"keep /opt/homebrew first in PATH" is this in another form.)

### Re-running `pgo.sh` failed: the tree kept the old profile's objects (fix1, 12 Sep 2026)

The verifier ran `bash tools/emu/ot_emu/pgo.sh` twice from a clean
state. The first run built `out/emu/ot_emu` (2 min 17 s); the second
(same command, nothing changed) died at 1 min 51 s in step 4's link, for
`ot_emu` and every test binary: `ld: LTO codegen error: linking module
flags 'ProfileSummary': IDs have conflicting values ... from
out/emu/mc68k/lib68kEmu.a[2](gpt.cpp.o), and ... from ld-temp.o` -- and
left `out/emu` WITHOUT `ot_emu`, the operator's binary destroyed by the
script meant to make it (`out/_agents/impl-4-pgo-verify0/pgo_run2.log`).

The cause is in the build system, not in clang: `cmake --fresh` clears
the top-level `CMakeCache.txt` and `CMakeFiles/`, nothing else. The
vendored subtrees' objects (`out/emu/mc68k`, `out/emu/dsp56300`: 156 of
the 171 `.o` files) had been compiled against the first profile, and
make saw no reason to recompile them: their sources had not changed and
their `flags.make` was byte-identical -- the same
`-fprofile-instr-use=<same path>`, whatever the bytes at that path. Only
`ot_machine` and `main.cpp` (whose `CMakeFiles/` `--fresh` had removed)
were recompiled against the second profile, and two profiles cannot be
LTO-linked into one module: every object carries its profile's summary as
a module flag and the linker refuses to merge two different ones. Any
second run into a tree holding a PGO build hit this -- i.e. the documented
rule "run `pgo.sh` after every source change" described the failing path.

The fix (`CMakeLists.txt`, the `OT_PGO_PROFILE` branch) makes the tree
follow the profile's BYTES rather than its path:

- `file(SHA256 …)` of the profile goes into every compile line as
  `-DOT_PGO_PROFILE_SHA256=<hex>` (`add_compile_definitions` before the
  `add_subdirectory` calls, so the cores get it too). Nothing reads the
  macro; its only job is to change every `flags.make` when the profile
  changes, which recompiles every object -- the same mechanism that makes
  a `-D` change rebuild a tree. `pgo.sh` still does not `rm -rf out/emu`
  (the frozen reference lives there) and still uses `--fresh` for the
  cache; the recompile is now the CMakeLists' guarantee, not the script's.
- `CMAKE_CONFIGURE_DEPENDS` on the profile file: a plain `cmake --build`
  after the profile changed re-runs the configure step by itself, so the
  hash is recomputed without anyone remembering to reconfigure.
- `pgo.sh` step 4 checks its own work afterwards: `find "$DEST" -name
  '*.o' ! -newer "$PROFDATA"` must be empty (it was 156 in the failing
  run), else exit 1 with the count.

Measured (logs `out/_agents/impl-4-pgo-fix1/pgo_run{1,2,3}.log`,
`ritual.log`, `checks.log`), started from the state the verifier left --
`out/emu` holding a PGO build from ANOTHER profile, compiled by the
pre-fix CMakeLists, the failing precondition:

- run 1: exit 0, 2 min 9 s (instrumented build 12 s, objects reused;
  training 97 s; optimised build 20 s, 167 compile lines) -> `out/emu/ot_emu` sha
  `dbe419dd…`, 171 of 171 objects newer than the profile;
- run 2 (same command, nothing changed -- the verifier's failing case):
  exit 0, 2 min 7 s, 167 compile lines in step 4 = every object
  recompiled, link fine -> sha `1634501f…`, 2,351,544 bytes, 171 / 171;
- run 3, `--skip-train` (same profile): exit 0, 8 s, only the 11
  top-level objects recompiled (`--fresh` clears their `CMakeFiles/`; the
  cores' 156 are kept), the binary byte-identical to run 2's -- the same profile still gives the
  same bytes in the same tree, and (`build-pgo2`) in a fresh tree;
- the `CMAKE_CONFIGURE_DEPENDS` path (`build-swap`): configure + build
  with run 1's profile (-> `dbe419dd…`, the run 1 bytes), overwrite the
  profile FILE with run 2's bytes, plain `cmake --build` with no
  reconfigure: cmake re-ran itself once, recompiled 167 files, linked ->
  `1634501f…`, the run 2 bytes. The old CMakeLists would have linked
  nothing here;
- the define does not reach the bytes: the stale-profile build
  (`build-stale`, speed-mem's pre-burst profile) is byte-identical to the
  one measured above (sha `5672b791…`, 25 out-of-date warnings), and the
  default configure (`build-plain`, no `-fprofile` anywhere) is still
  O15c's `e298c877…`.

**The gate on the binary left in `out/emu` (run 2's): 28 PASS, 0 FAIL**
(`reports/20260912-095805-fix1-outemu.txt`, 45 s, `--build-dir out/emu`,
ctest 7 / 7); the plain and stale binaries 28 PASS each (their bytes were
already verified, so the oracle served them from its cache and re-ran
ctest: `…-095851-fix1-plain.txt`, `…-095902-fix1-stale.txt`). `bench.py`
on it, same session, interleaved, nothing else running (`checks.log`):

| measurement | reference | LTO (`build-plain`) | **PGO, `out/emu` (fix1)** |
|---|---|---|---|
| play, no `--dsp` (emulated ms per wall s) | 222 | 1320, 1371 | **1652, 1655** |
| play, `--dsp` | 100 | 150, 151 | **168, 172** |
| boot + fixture load to `ready`, no `--dsp` | 38.5 s | 8.0, 7.2 s | **6.6, 6.6 s** |
| boot + fixture load to `ready`, `--dsp` | 56.8 s | 25.0, 24.9 s | **19.2, 18.5 s** |

PGO over LTO 1.23x without `--dsp` (1653.5 / 1345.5), 1.13x with (170 /
150.5); over the reference 7.4x and 1.70x; 1.65x real time without the
cores (a 16th at 120 BPM in 0.076 s wall), 0.17x with. `ready
sample=277688.167` in all nine runs. The instrumented binary trained at
843-844 / 105-106, as before.

Verified independently (verify1, `out/_agents/impl-4-pgo-verify1/`): from
a clean state (no instrumented tree, no profile, `out/emu` cleaned of its
build products) `bash pgo.sh` exit 0 in 138 s -> sha `75df7057…`; the same
command again exit 0 in 127 s, 167 compile lines in step 4 -> sha
`ccff1e46…` (2,351,544 bytes, arm64, no `__llvm_prf`, 171 / 171 objects
newer than the profile); `--skip-train` 8 s, byte-identical to that. A
touched source rebuilds one object and the same bytes; the profile file
swapped for run 1's under a plain `cmake --build` reconfigures once,
recompiles 167 and gives run 1's bytes back, and restoring it gives run
2's. Oracle 28 PASS / 0 FAIL on both profiles' binaries (`…-101606-
verify1-outemu.txt`, `…-101852-verify1-run1.txt`, ctest 7 / 7 in
`out/emu`), on the default build (`e298c877…`, no `-fprofile` in any
`flags.make`) and on the stale-profile build (`5672b791…`). `bench.py`,
one session, interleaved, nothing else running: reference 221 / 98, LTO
1263, 1242 / 149, 151, PGO 1615, 1632 / 176, 175 -- PGO over LTO 1.30x /
1.17x, over the reference 7.3x / 1.79x; ready 38.9 / 7.9, 7.7 / 7.1, 6.8 s
without the cores, 57.1 / 25.7, 24.6 / 17.8, 18.4 s with.

### What it does not do

- Nothing changes without the option: a default configure is O15c's LTO
  build, byte for byte. Neither the panel server's auto-build (`cmake
  --fresh -B out/emu …` when the binary is missing) nor anyone's script
  gets PGO unless `pgo.sh` is run; that is deliberate -- the profile is a
  local, perishable artefact, and a build must never fail for want of it.
- The training is `bench.py`'s sequence on the OTLIVE fixture: playback
  with and without the cores, the boot and the fixture load. Knobs, SETUP
  pages, the file browser, sample loading and `--audio-in` are not in the
  profile; their code runs with static heuristics, as before, not slower.
- The DSP JIT and asmjit are compiled with the profile flag but have no
  data (never run); the vendored unit tests likewise.
- The profile is not committed and not portable: a different compiler
  version refuses or ignores it, a different source changes the hashes
  (measured above: it costs speed, never bytes). `pgo.sh` after every
  source change is the rule; the `-Wprofile-instr-out-of-date` lines in a
  build are the reminder. Re-running it into `out/emu` recompiles the
  whole tree (19 s), never less: the profile's hash is on every compile
  line, and there is no partial rebuild against a new profile.
- `cmake --fresh` is not a clean: it clears the cache, not the objects.
  Nothing in the ritual deletes `out/emu` (the reference binary lives
  beside the build); the recompile against a new profile rests on the
  hash define, and step 4's object-age check is the tripwire if that
  ever stops being enough.
- The remaining `--dsp` distance to real time (0.17x) is the two
  interpreters' own work under the exact interleave; the plan's step 5
  (`m68k_execute(N)` with an instruction hook) is in reserve for the
  ColdFire side, and nothing exact reaches the cores' ceiling (plan:
  <= 0.4-0.55x).

## Milestone O15e — bursts under the gate, the borrowed calls and the waits: `ready` 7.4 → 5.5 s, `test_rtos` 3.7x, bit for bit ✅ (12 Sep 2026, branch `panel-ui`)

Step 5 of this run of the speed plan (the architect's step 6); `rtos.cpp`,
`rtos.h`, `main.cpp`; no CLI change, no vendored change. O15a left five
loops stepping exactly, one `stepOnce()` -- `tickTimers()` + `deliver()`
-- per instruction: `run(_ms, true)` (the M6a gate: the batch's and
`test_rtos`'s first second), `runUntil` (the render's frame predicates,
`runToPc`), `runToMainSpin`, `callAsMain` (the borrowed slot: LOAD PROJECT's
post, SET MAIN LEVEL, the transport start) and the memory waits inside
`loadProjectLive` (card ready), `selectBankLive` (the bank byte) and
`setMainLevelLive` (the gain table). Measured on the Step 4 state before
this change (`OT_BURST_STATS=1`, `out/_agents/impl-5-bootbursts/`): the
OTLIVE boot to `ready` executes 787,138,663 instructions, of which
725,319,505 were in bursts and 10,170,953 in the boot before the handoff;
the other **51.6 M** (6.6 %) went through the exact loops -- 35.8 M in the
gated run to 205.96 ms and ~16 M in the load's borrowed calls and waits --
at roughly a quarter of the burst rate, so they cost about a quarter of
the wall. `test_rtos` was the extreme: its negative-control machine runs
the full 1000 ms with the transmit interrupt storming (186 M instructions,
bursts of ~10 between acknowledgements) and every one of its 232 M
instructions was exact.

### What changed (`rtos.cpp/.h`, `main.cpp`)

- **One loop.** `runInternal` is now `Rtos::runLoop(const RunSpec&)`, and
  every way the machine is run is a `RunSpec`: a sample budget (`ms`,
  `hasEnd`), an instruction budget (`budget`, `callAsMain`'s `n <
  _budget`), the gate (`untilGate`), a PC to stop BEFORE (`pc`, `pcArmed`),
  a caller's condition (`stop`) with whether it changes only on an event
  (`stopOnEvent`), the idle skip (`idleSkip`), the install check
  (`needInstall`, the public `run`/`runUntil` only, as before) and the two
  `m_why` strings each old loop set (`whyGate`, `whyTime`; null = leave
  it). The loop's top asks, in the old order, time/budget, the gate (only
  when `m_gateDirty`), the PC, the condition, then the idle skip; the
  stepping is O15a's burst body, the exact entry step and the exact tail
  across the horizon, unchanged. So what O15a proved for the plain run --
  the same `m_sample += 1/ips` in the same order, the pair called only
  where the old loop called it -- holds for all of them.
- **The gate ends a burst** (`endGate`): a create (`recordCreate` at
  `g_create`) and a dispatch (the record after the scheduler's `rte`) are
  the only writers of `m_gateDirty`, both are already detected per
  instruction inside the burst, and the burst now breaks on the flag after
  that instruction -- the pair, then the loop's top asks `gate()` exactly
  where the old loop asked it (once per create/dispatch: 61 evaluations in
  the boot, as before). `Stop::Gate` lands on the same instruction and the
  same sample: `gate_ms` 205.965 in the stock golden, 6296.63 in the card
  golden, `test_rtos`'s "205.964903 ms".
- **A PC condition is compared inside the burst** (`endPc`): `runToPc`,
  `runToMainSpin` and `callAsMain`'s return (the return address IS main's
  park) stop before the instruction at the address, where the old loops
  asked their predicate -- after the previous instruction's pair, which
  runs at the break. `callAsMain` counts instructions as its old `n` did
  (a burst is cut to the remaining budget; the budget is checked before
  the PC, so a call that returns on its last permitted instruction is
  still "did not return", as before).
- **A memory condition gets a write watch that wakes** (`wakeOnWrite`):
  `loadProjectLive`'s card-ready word, `selectBankLive`'s bank byte and
  `setMainLevelLive`'s gain-table longword are watched (once each, at
  first use; `Machine` watches are never removed) with a callback that
  sets `m_wake`, so the store that satisfies the condition ends its
  burst on that instruction and the condition is asked there. `runUntil`
  gained `Changes` -- `Anything` (the default: asked before every
  instruction, no bursts, the pre-O15e loop) or `OnEvent` (the condition
  can change only on a burst-ending instruction: an acknowledged vector,
  a peripheral access, a watched write) -- and the caller is answerable
  for the classification; `main.cpp`'s two frame-count predicates
  (`--pre-roll`, `--frames`) pass `OnEvent` because `m_frameCount` moves
  only in the ack hook, which wakes. A condition on unwatched memory
  stays `Anything`.
- **No idle skip where there was none.** The borrowed calls and the
  waits stepped through main's park (`bras .`) between the ATA
  interrupts; a skip would land the clock ON the expiry where stepping
  lands it a fraction of a sample past, and every later stamp would
  move. `idleSkip` is off for them, and without it the burst does not
  break at the park either (it would be a burst of one instruction): the
  spin runs to the horizon in bursts of 4096, the exact tail takes the
  timer on the same instruction.
- The per-instruction cost added to the burst body is three predictable
  compares (the PC target -- an odd sentinel when none is armed --, the
  gate flag under the gated run, the spin under the idle skip), hoisted
  as constants; the loop's top reads the PC through `pcFast()`. `OT_BURST=0`
  is still the exact loop for all of them (the card batch under it: golden
  byte-identical, 31.9 s). `OT_BURST_STATS=1` prints two more counters,
  `endGate` and `endPc`; `BurstStats` gained the same two fields.

### Measured (12 Sep 2026, the same M5 Mac, macOS 26.5, AppleClang 21.0.0; logs under `out/_agents/impl-5-bootbursts/`)

Same session, interleaved, nothing else running (`pgrep -x ot_emu` = 0
before each series). **LTO pair**: `build-base` = the Step 4 state at HEAD,
default configure (= O15c's binary, sha `e298c877…`) vs `build-cand` = this
tree, default configure. **PGO pair**: `out/emu/ot_emu` as O15d's `pgo.sh`
left it (sha `1634501f…`) vs `emu-pgo/ot_emu` = this tree through
`pgo.sh --dest/--gen/--prof` into the log dir (`pgo_run.log`). `bench.py`
unless said otherwise; `ready.py` = boot to `ready` with `--rtc
1000000000` and the burst stats.

| measurement | Step 4, LTO | **this step, LTO** | ratio | Step 4, PGO | **this step, PGO** | ratio |
|---|---|---|---|---|---|---|
| boot + fixture load to `ready`, no `--dsp` | 7.5, 7.3, 7.28 s | **5.5, 5.4, 5.62 s** | **1.34x** | 6.6, 6.6 s | **5.7, 5.3 s** | 1.20x |
| … `--dsp` | 25.4, 25.09 s | **23.4, 23.38 s** | 1.08x | 18.8 s | **17.4 s** | 1.08x |
| play, no `--dsp` (emulated ms per wall s) | 1341, 1387 | 1402, 1460 | 1.05x | 1714, 1720 | 1679, 1671 | 0.98x |
| play, `--dsp` | 153 | 151 | 0.99x | 174 | 175 | 1.01x |
| `card` batch (`--mount … --ms 1000 --golden`, standalone) | 7.36 s | **5.48 s** | **1.34x** | 6.57 s | **5.42 s** | 1.21x |
| `stock` batch (`--ms 1000 --golden`) | 1.62 s | **0.82 s** | **2.0x** | | | |
| `--sequencer --golden --bank 1 --frames 400` (no `--dsp`) | 7.85 s | **5.66 s** | 1.39x | | | |
| `test_rtos` (two boots, the gate, the negative control's full second) | 8.62 s | **2.32 s** | **3.7x** | 7.70 s | **2.78 s** | 2.8x |

The play rate is the untouched path (`run(_ms, false)`): 1.05x and 0.98x
are the LTO series' noise and the profile-to-profile variance O15d
measured (1652 vs 1665 on two profiles). The gains are where the exact
loops were: `ready` from 7.36 s (mean) to 5.51 s, `test_rtos` 3.7x, the
stock batch 2.0x. The `card` batch's 1.34x is what its instruction
accounting allows -- 52 M of its 787 M instructions were exact at Step 4
and the rest already burst -- so the plan's "3-4x" for it (estimated
before Steps 2-4 shrank everything else) was never on the table; 3.7x
on `test_rtos`, the plan's other case, is. Acceptance: `ready` <= 9 s
(5.5 s), `gate_ms` unchanged (205.964903 in `test_rtos`, 205.965 /
6296.63 in the goldens), oracle 28 PASS, ctest 7/7.

**What the bursts do now** (`OT_BURST_STATS=1`): the OTLIVE boot to
`ready`, 787,138,663 instructions as before (the same instructions --
the count is unchanged in every run below): 776,958,966 in 11,618,842
bursts (98.7 %; Step 4: 725.3 M in 11.42 M), 8,744 exact (Step 4:
6,965), the rest the boot before the handoff; bursts ended on a
peripheral access 11,330,746 times, a wake 65,421, the horizon 180,587,
main's spin 42,021, **the gate 61, a PC 6** (5 in the card batch: the
load's borrowed calls and PC waits; the sixth is SET MAIN LEVEL's, which
`--interactive` posts by default -- a PC wait that finds the PC already
there at its top, or on its exact entry step, is not a burst end and is
not counted). `test_rtos`: the first
machine 43,931 bursts / 35,830,945 instructions / 121 exact, 61 gate
ends; the negative control 17,595,901 bursts / 175,958,997 instructions,
17,595,900 of them ended by a wake -- the transmit interrupt it was
built to leave storming, ten instructions apart, and still 3.7x faster
than a pair per instruction.

**The gate: 28 PASS, 0 FAIL, twice** -- the LTO candidate
(`out/_agents/speed-oracle/reports/20260912-104149-impl5-bootbursts.txt`,
copy `oracle1.txt`, 53 s wall) and the PGO candidate
(`…-105258-impl5-bootbursts-pgo.txt`, `oracle_pgo.txt`, 43 s): boot logs
identical, `serial_a` 5731 / 9257 bytes identical, goldens 12,757 /
26,367 bytes identical, `run3_core0.wav` 7,936,292 bytes identical, the
UART A stream 18,297 / 18,309 bytes identical step by step, 109 peeks
identical, 47 run stamps with max |dsample| = 0 and |dframes| = 0,
`interdsp.pcm` 497,788 bytes identical, ctest 7 / 7 in `build-cand` /
`emu-pgo`. Under the battery the LTO candidate's jobs took: `stock`
0.60 s (reference 2.37), `card` 6.34 s (44.19), `render` 31.6 s (75.3),
`inter` boot 6.35 s + 3.0 s of `run` (44.1 + 19.8), `interdsp` 24.7 s +
27.6 s (66.0 + 42.4); the PGO candidate: `card` 6.41, `render` 25.0,
`inter` 6.36 + 2.68, `interdsp` 18.6 + 23.8. Beyond the gate, standalone
against the Step 4 binary: the `stock`, `card` and `--sequencer --bank 1
--frames 400 --main-level 64` goldens and batch logs byte-identical
(`selectBankLive` exercised: saved bank 0, played bank 1), and the card
batch under `OT_BURST=0` byte-identical to both.

### What it does not do

- A `runUntil` condition on memory nobody watches, or any condition a
  caller does not classify, runs the exact loop as before (`Changes::
  Anything` is the default); the three watched words are the only memory
  conditions in the tree. Watches cost: every write takes `Machine`'s
  slow body once one is armed (O15c), and the card-ready watch arms it
  at the mount rather than at the PART_PTR watch a few hundred ms later
  -- the same state every card session was already in for the whole of
  its play.
- The `--dsp` `ready` gains 8 %: the DSP boot and the sample load are
  the cores' work, ticked per instruction inside a burst as outside it.
- The stock path's remaining cost is not the loop: 17.5 M of its 46 M
  instructions write into the 0x42000000 span the stock run never maps
  (auto-mapped, the slow body); a mapped span there is `machine.*`'s
  business, not this step's.
- Nothing paces anything (plan step 7); the plan's step 5 (`m68k_execute
  (N)` with an instruction hook) stays in reserve.

## Milestone O15f — real-time pacing: the child tracks its wall clock, 1.00x whenever the core keeps up, opt-in, bit for bit ✅ (12 Sep 2026, branch `panel-ui`)

Step 6 of this run of the speed plan (the architect's step 7); `main.cpp`
(`serveInteractive` only), `tools/panel/panel_server.py`,
`tools/panel/panel.html`, `tools/panel/README.md`; no change to the run
loop, the batch, the vendored cores or any existing command. O15a–e made
the ColdFire side faster than the unit (1.4x real time playing without
the DSP cores on the M5) but nothing held it *at* the unit's rate: the
panel server pumped `run 25` and slept 30 ms, which macOS stretched to
33.9, so idle the firmware's clocks ran at 0.69x without the cores and,
with them, at 1.02x in bursts of 0.85–2.0x (the 10 ms `run 25` skipped
the sleep on 58 % of pumps); playing, at whatever the core did. The
double-tap chord (a track key twice opens its sample slot list; the
firmware measures the gap in ITS time, window between 191 and 242
emulated ms) only landed from the page because the server slowed its
pump for 0.5 s after a track key (`SLOW_PUMP_MS`), compressing 0.45 s of
wall into 191 emulated ms. The pacing investigation
(`out/_agents/speed-pacing/`, `REPORTS.md` "pacing") measured all of that
and prototyped both a server-side and a child-side pacer; this milestone
lands the child-side one the plan prefers, with the commands interleaved.

### What changed (`main.cpp`: three commands and one argument, all opt-in)

- **`pace on [rate]` / `pace off`.** While `pace` is on and no command
  line is pending on stdin, `serveInteractive` free-runs: it takes an
  anchor (wall time, emulated ms) at `pace on` and before every step
  compares the lead = emulated − (anchor + wall elapsed × rate). A slice
  (10 ms) or more ahead, it sleeps the excess, capped at one slice,
  **inside `poll()` on stdin** so a command wakes it at once (the first
  cut of the prototype used `sleep_for` and every command waited up to a
  slice: 13.7 ms median key round trip against 0.09). Otherwise it runs
  `_rtos.run(10, false)` — the ordinary run loop, idle skip and bursts
  included, so idle a slice is a handful of idle skips and playing it
  costs what the core costs; a core faster than real time builds a lead
  and sleeps it off, a slower one never leads and runs flat out. More
  than 250 ms behind (a slower core, a long command) it re-anchors and
  counts it, so the lag never turns into a catch-up burst later. A
  pending line is served between slices, one reply per command, exactly
  as unpaced; a `run` from the client advances on top of the pacer (the
  pacer then waits for the wall clock). A slice that stops on
  fault/illegal ends the free run; `pacestatus stop=` says so and the
  next `run` answers as it always did. `pace on` re-anchors; `rate` is
  emulated seconds per wall second (0.5 = half speed; 1.0 default).
- **A 1 ms grace after every reply.** A client sends its commands in
  rounds (the panel's `tx`, `audio read`, `pacestatus`, ~100 µs apart),
  and a slice begun in that gap made every command of the round wait for
  a slice — four slices, 231 ms, per page click while playing with the
  cores. After a reply the loop polls stdin for 1 ms before the next
  slice, so a round goes through in one; at 1.0x the millisecond comes
  out of the sleep, flat out it is under 1 % (a round per 200 ms).
- **`pacestatus`** → `on= rate= ratio= lag= slices= reanchors= slept=
  busy= stop= ms=`: `ratio` is emulated ms per wall s over the last
  closed window of at least a second (commands served inside it included)
  / 1000 — the honest "x real time"; `lag` how far emulated time is
  behind its wall target now (0 when ahead); `slept` wall seconds inside
  `poll()`, `busy` inside slices (also added to `status wall=`); `stop`
  the last slice's Stop word; `ms` the emulated clock. Pending stdin is
  detected through iostream's buffer, stdio's (`stdin->_r`: `cin` is
  synced with stdio, whose FILE may hold a line `poll()` cannot see) and
  `poll()`.
- **`run <ms> wall <seconds>`.** The plain run, also ended when the wall
  budget is spent: `Rtos::runUntil` with `Changes::OnEvent`, whose
  condition is asked at every burst end and exact step, and the
  condition reads the clock once per 4096 instructions
  (`Machine::instructions()`; bursts average ~70 instructions, so a
  `steady_clock` read per call would have cost ~5 %). Where the run ends
  moves no firmware event (timers, frames and the panel UART advance by
  sample count); `stop=wall` when the budget ended it, else the usual
  word. `run <ms>` alone is the untouched path (`_rtos.run(ms, false)`),
  and every old input — `run`, `run 10 20` — gets the old reply
  byte-for-byte; only a `run <ms> wall ...` attempt (a four-word line
  whose third word is `wall`) has the new usage text -- `run 10 x 1` is an
  old input and keeps `err usage: run <ms>`.

### What changed (`panel_server.py`, `panel.html`)

- **`Panel._loop_paced`** replaces the pump for the port backend: after
  the boot the loop sends `pace on 1` and from then on only serves
  actions (a blocking `actions.get(timeout=0.02)`, so a click is served
  the moment it arrives) and, every 20 ms when none is queued, `tx`, the
  audio drain, `pacestatus` (which sets `rt.sample`, `ran_ms`, the
  meters) and the render. A fresh child (respawn, card re-insert, sound
  switch) is armed again when the loop sees a new `rt`; a child without
  `pace` (an older `--port-bin`) makes `pace on` answer `err` and the
  loop falls back to the old pump, with the reason in `backend_note`.
  Route A keeps the pump loop, unchanged. `SLOW_PUMP_MS`/`slow_until`
  are ignored under the pacer (the route A pump still uses them).
- **An ordinary key edge does no `run` of its own** under the paced
  child (`_key_act(run_ms=None)`): the pacer's next slice delivers it
  within 10 ms of emulated time anyway, and the old 50 ms run cost the
  click 50 emulated ms at the core's rate (40 ms of wall playing without
  the cores, ~330 with them). PLAY down and STOP down keep their 50 ms so
  the frame-mode switch and the take open/close still sit around a
  processed key; `tap()` (hold/gap inside one action) and `transport()`
  (down + up as one action) pass their runs explicitly, as before.
- **`run_ms`** (the page's RUN 1s/5s, `/run`) passes the remaining wall
  budget to each slice as `run <ms> wall <s>`, so a slice, not just the
  loop, is bounded in wall time; `stop=wall` ends it like the budget did.
- **`/status`** gains `rt` (x real time by the wall clock: emulated ms
  per wall s over the last second of `pacestatus` readings / 1000 — the
  `RtMeter`; null under route A and before the pacer is up) and `pace`
  (the last `pacestatus`, as numbers). `speed` is kept with its old
  meaning — emulated ms per wall second *inside* the emulation, fed from
  the child's `busy` deltas — and still reads high idle (thousands: idle
  slices are instant); scripts that want the honest figure read `rt`.
  The sound note no longer claims "~9x slower".
- **The page** shows `rt` as a tiny badge beside the phase (`1.00x`,
  green at ≥ 0.97, yellow below, hidden while it is unknown), from the
  same 350 ms status poll.

### Measured (12 Sep 2026, the same M5 Mac, macOS 26.5, AppleClang 21.0.0; logs under `out/_agents/impl-6-pacing/`)

Nothing else running (`pgrep -x ot_emu` empty before each series), every
run started at nice 0 (see the QoS paragraph: a zsh `&` job is not). The
candidate is `build/ot_emu` (sha `261ffec46498…`), this tree at the
default configure; the reference `out/emu/ot_emu.ref-1e76ac5`.

**The child alone** (`pace_child.py`: the OTLIVE card, `--rtc 1000000000`,
YES on the dialog, then `pace on`; `pacestatus` once a wall second):

| | no `--dsp` | `--dsp` |
|---|---|---|
| boot + load to `ready` | 5.8 s | 24.1 s |
| idle, 20 s: emulated ms per wall s | **1000.5** (per second 990–1012; `ratio` 0.988–1.012) | **1000.2** (992–1007; 0.991–1.009) |
| … slices / re-anchors / slept / busy | 1613 / 0 / 19.18 s / 0.89 s | 1617 / 0 / 13.74 s / 6.41 s |
| … child CPU | 3.8 % | 29.7 % |
| `key` down + `key` up round trip, idle | 0.24 ms median, 4.99 max | 2.64 ms, 8.31 max |
| the same with a `run 50` after each, idle | 7.08 ms | 33.4 ms |
| playing (frame on, PLAY), 20 s | **999.6** (994–1004; `ratio` 0.999–1.002, `lag` 0) | **151.0** flat out (137–164; 0.137–0.164; `lag` 57–284 ms, 70 re-anchors) |
| … child CPU | 69.1 % | 100 % |
| key round trip, playing | 2.3 ms median, 13.6 max | 103.1 ms, 146.5 max (before the grace poll) |
| `run 250` vs `run 250 wall 100` (never hit), 8 pairs interleaved, playing | 177.0 vs 171.9 ms (−2.9 %: noise) | 1651.8 vs 1647.6 ms (−0.3 %) |
| `run 250 wall 0.05` / `wall 0.02`, playing | 50.1 ms wall, 75.6 emulated ms, `stop=wall` / 20.1 ms, 30.9 | 50.6 ms, 8.44 / 20.4 ms, 3.34 |

Idle, the pacer holds the unit's clock to ±1 % second by second with and
without the cores (the acceptance: 1.00 ± 0.01) at 4 % of a core without
them; playing without the cores it holds 1.00x too, because the O15a–e
core is 1.4x real time and the pacer sleeps the difference (busy 16.7 of
20 s); with the cores it is flat out at the core's own rate (bench.py on
the same binary: 150–152) and says so. The wall predicate costs nothing
measurable and a budget that is hit ends the run within 0.1–0.6 ms of it.
The error paths answer `err usage` for `pace`, `pace maybe`, `pace on 0`,
`pace on 1 2`, `pacestatus x`, `run 10 wall`, `run 10 wall -1`, `run 10
wall x`; `run 10 x 1` gets the old `err usage: run <ms>` (the new text is keyed
on the third word being `wall`, not on the word count; the verifier's
`child_cmds.py` diff of every old input -- `run`, `run 10 20`, `run 10 x 1`,
`run abc`, `run -1`, `status`, `tx`, `frame`, `key`, `quit x` -- against the
reference is empty apart from the bare word `pace`, a new command;
`out/_agents/impl-6-pacing-fix1/cmds_{cand,ref}.log`).

**The panel server end to end** (`srv_measure.py`: `panel_server.py
--port 8590/8591 --port-bin build/ot_emu`, the OTLIVE project, then
`/status` once a wall second, 30 `/key` edges (MIXER), PLAY, STOP,
`/run?ms=1000`, and the double tap by wall time; `srv_nodsp3` /
`srv_dsp2`, the final code):

| | `--sound off` | `--sound on` (`--dsp`) |
|---|---|---|
| boot to `phase ready` | 6.1 s | 24.2 s |
| idle, 20 s: `ran_ms` per wall s | **1000.3** (964–1017); `/status rt` 0.991–1.008, median 0.999 | **1000.2** (978–1029); `rt` 0.992–1.008, median 1.000 |
| … CPU child / server | 3.8 % / 0.5 % | 29.5 % / 0.4 % |
| `/key` round trip, idle | **1.7 ms** median, p90 1.9, max 1.9 | **1.0 ms**, p90 3.5, max 3.8 |
| playing, 30 s | **1000.2** (981–1019); `rt` 0.997–1.004, median 1.000; lag 0 | **149.5** (130–161); `rt` 0.133–0.161, median 0.148; lag ≤ 319 ms, 98 re-anchors |
| … CPU child | 74.4 % | 97.8 % |
| `/key` round trip, playing | **2.0 ms** median, p90 8.3, max 11.2 | **35.7 ms** median, p90 44.1, max 79.4 |
| `/run?ms=1000`, idle | 1004 ms in 0.02 s | 1004 ms in 0.28 s |
| two page clicks on T1, 0.15 s of wall apart (2 trials) | OPEN, 150 / 150 emulated ms press to press | OPEN, 150 / 150 |
| … 0.30 s apart | miss, 300 / 311 | miss, 290 / 300 |
| the take from PLAY to STOP | — | take-003.wav, 6.3 s, `dropped` 0 |

Against the pump it replaces (the pacing report's measurements on the
same fixture: idle 686 emulated ms per wall s without the cores, 1025 in
bursts of 0.85–2.0x with them; `/key` 29.7 ms idle, 327 ms playing;
double taps landing only through `SLOW_PUMP`): idle is now 1.000x either
way, a click waits ~1–2 ms idle and about the slice in progress playing
(2 ms at 1.0x, 36 ms with the cores at 0.15x, where a slice is ~65 ms of
wall), and the double-tap window is the unit's own — 0.15 s of wall
lands, 0.30 s does not, cores or no cores. Two intermediate runs are in
the log dir for the record: `srv_nodsp` (the key's 50 ms run still in
place: `/key` 40.2 ms median playing, 4.2 idle) and `srv_dsp` (before
the grace poll: 230.7 ms median playing — four slices — with the same
rates and the same double-tap result); `srv_nodsp2`/`srv_dsp` also ran
at nice 5 by accident (a zsh `&` chain) with rates indistinguishable
from the nice 0 runs.

**Ratio to the reference, same session** (`bench.py`, the fixed `run
250` protocol, pacing never on): no `--dsp` **1414 vs 221** emulated ms
per wall s (6.4x; `ready` 5.4 vs 38.8 s), `--dsp` **150 vs 100** (1.5x;
`ready` 23.5 vs 57.4 s) — the O15a–e rates; this step adds nothing to the
fixed-run path and takes nothing from it (1423, 1341, 1414 across the
session's three no-`--dsp` runs of the candidate).

**The QoS question** (the speed-mem report's "backgrounded runs of a
byte-identical binary differed by 1.5x"). Explained and measured: a job
started with `&` in zsh runs at nice 5 (`BG_NICE`, on by default), which
on an idle machine costs nothing (1357 vs 1341/1423 without the cores,
151 vs 151/152 with) and under contention gives way — the 1.5x was
another emulator on the machine. The class that does matter is the
darwin background policy (`taskpolicy -b`: efficiency cores, throttled
I/O — what a background-QoS or napped app and its children get):
**385** emulated ms per wall s without the cores (boot 21.4 s) and **43**
with them (boot 90.9 s), 3.7x / 3.5x slower. A process may leave that
class itself — `setpriority(PRIO_DARWIN_PROCESS, 0, PRIO_DARWIN_NORMAL)`
in the child before `exec` (`bench.py` variant `OT_DARWIN_NORMAL=1` under
`taskpolicy -b`): **1469**, boot 5.5 s — so `PortProc` now spawns the
child with exactly that `preexec_fn` (a no-op when nothing is inherited;
niceness cannot be lowered without privilege, so `/status` reports it as
`nice` and the server prints a warning at start when it is > 0). The
app-bundle half (`open -a` on a re-identified copy of `Virtual
Panel.app`, bundle id `io.octabam.virtual-panel.qos-test`, port from
`VIRTUAL_PANEL_PORT`, `projectDir` in its own defaults) could not be
measured: the copy blocked in `Log.open` → `open()` on
`out/panel_app.log` (`app_stuck.sample.txt`) — macOS's consent prompt for
`~/Downloads`, which the new identifier triggers and only the user can
answer; the launched app itself ran at nice 0, priority 46. The
terminal-launched control on the same code path (`qos_terminal`, the
pre-O15f binary through the fallback pump): 179.5 emulated ms per wall s
playing with the cores.

**The gate: 28 PASS, 0 FAIL** (`out/_agents/speed-oracle/reports/
20260912-120131-impl6-pacing.txt`, copy `oracle1.txt`, 52 s wall): boot
logs identical, `serial_a` 5731 / 9257 bytes identical, goldens 12,757 /
26,367 bytes identical, `run3_core0.wav` 7,936,292 bytes identical, the
UART A stream 18,297 / 18,309 bytes identical step by step, 109 peeks
identical, 47 run stamps with max |dsample| = 0 and |dframes| = 0,
`interdsp.pcm` 497,788 bytes identical, ctest 7 / 7 in `build`. Under
the battery the candidate's jobs took `stock` 0.55 s (reference 2.33),
`card` 6.07 s (44.08), `render` 31.6 s (74.2), `inter` boot 6.12 s +
2.96 s of `run` (43.6 + 19.3), `interdsp` 23.5 s + 27.5 s (63.2 + 41.6)
-- the O15e figures: the oracle never says `pace on` or `wall`, and the
paths it drives did not change.

**Verified (12 Sep 2026, the verifier's own build of the same tree, sha
`089bd73fb869…`, byte-identical to the fixed binary; logs under
`out/_agents/impl-6-pacing-verify1/`).** Oracle `--fresh`, both sides
rerun: 28 PASS / 0 FAIL, 106 s wall; ctest 7 / 7. The old inputs answer
byte-for-byte as the reference (`child_cmds.py`: only the bare word
`pace`, a new command, differs). Same-session `bench.py`: **1473 vs 224**
without the cores (6.6x; `ready` 5.4 vs 38.9 s), **155 vs 101** with
(1.5x; 22.6 vs 56.9 s). The server end to end (`srv_verify.py`, ports
8590/8591, the OTLIVE fixture, nice 0): idle 60 s **1000.2** emulated ms
per wall s both with and without the cores (per second 979–1029 /
976–1029; `/status rt` 0.990–1.014 / 0.991–1.009, median 0.999 / 1.000;
0 re-anchors), child CPU 4.7 % / 30.5 %; playing 30 s **1000.2** without
the cores (`rt` 0.996–1.003, lag 0) and **150.7** with them (`rt`
0.136–0.164, median 0.150, 89 re-anchors, lag ≤ 279 ms), i.e. what
`bench.py` measured on the same binary; `/key` round trip 1.66 / 0.98 ms
idle, 1.72 / 24.2 ms playing; double taps 0.15 s of wall apart OPEN (150,
163 / 150, 150 emulated ms press to press), 0.30 s miss (300, 300 / 315,
300), twice each, `/tap n=2` opens; the take from PLAY to STOP recorded
(take-005.wav, 8.74 s, `dropped` 0), `/audio/pcm` streamed +7553 frames
per wall s while playing with the cores. **The LEDs in wall time**
(`led_chase.py`: `/leds` polled ~250 times a second for 20 s of play,
sound off): the running light enters trig row 1 (trigs 5–8) once per
16-step sweep every **2000.2 ms** (10 crossings, stdev 15.7, min 1971.6,
max 2021.3) = **125.01 ms per 16th** at the fixture's 120 BPM, and the id
`0x48` LED blinks every **250.0 ms** (79 intervals, stdev 13.2) — the
unit's tempo, on the wall clock; with the cores the same blink comes
every 1646 ms (18 intervals) = 0.152x, as `rt` says. QoS reproduced:
`taskpolicy -b` 381 emulated ms per wall s (boot 21.3 s) vs 1520 with
the child's `setpriority` (boot 5.4 s); the server itself started under
`taskpolicy -b` booted in 6.0 s and paced at 1.000x (its `preexec_fn`
takes the child out of the class; the server process stays in it, so
its own polling is slower — 11 `/leds` a second against ~250). `nice 5`
on the idle machine: 1475.

### What it does not do

- It does not make the cores faster: with `--dsp` the pacer is a
  reporter (0.15x, re-anchoring every ~0.3 s of wall) until the DSP work
  in the plan lands; the "honest playback note" in the README stands.
- Idle with `--dsp` costs ~30 % of a core: the cores render silence
  through every idle slice (the DSP idle fast-forward the pacing report
  hands to the core team, proposal E, is not done).
- Nothing in the batch, the fixed `run`, the run loop or the vendored
  cores changed: the oracle's 28 checks drive fixed runs and are
  byte-identical; the pacer is off unless a client says `pace on`.
- The app-bundle half of the QoS question could not be measured (see
  Measured): a re-identified copy of the bundle hits a TCC consent
  prompt for `~/Downloads` that only the user can answer.
- A `run <ms> wall <s>` that ends on the budget is still one `run`: the
  frames it advanced are what it advanced, and a script that relies on
  `run` advancing exactly `<ms>` must not pass `wall`.
- The `hits` record line is formatted into a 160-byte buffer and can be
  cut short when every register is 8 hex digits (up to 215 bytes); a
  pre-existing limit, left as it is because changing it would change an
  existing command's output. (Fixed in O15g, below: the record has its
  own buffer; the reply's grammar did not change.)

## Milestone O16a — the oracle in the repo, a second frozen reference, and the Phase B audio contract ✅ (12 Sep 2026, branch `panel-ui`)

Phase B (the DSP pair: lazy batching / a thread per core, E2/E3 in
`out/_agents/speed-plan/REPORTS.md`) cannot be held to bit-identical audio:
O12 measured that ANY change of the core interleave (quanta 1 / 64 / 2,000 /
50,000) moves a few samples of a reverb return by one LSB at the host-frame
edge (frame 641 on that fixture), while the hardware runs the two cores
truly in parallel — a different interleave is not less faithful. What must
not move is the firmware's observable behaviour: screens, LEDs, sequencer
timing, the goldens' dispatch order. This step makes that contract a tool.

### What changed (`tools/emu/ot_emu/oracle/`, new; nothing in the emulator)

- `out/_agents/speed-oracle/` (Phase A's gate, O15a–O15f) is now
  `tools/emu/ot_emu/oracle/` — `oracle.sh`, `drive.py`, `tmo.py`,
  `cmp_text.py`, `cmp_stamps.py`, the README — as a maintained tool. Every
  input is a flag or an environment variable with the Phase A path as its
  default: `--ref`/`OT_ORACLE_REF` (`out/emu/ot_emu.ref-73c2815`; a first
  positional still overrides), `--image`, `--card` (card/inter/interdsp),
  `--card2` (render), `--set`/`--project` (`OTLIVE`/`PROJECT`), `--out`
  (cache + reports, `out/_oracle/`), `OT_ORACLE_PY`. No firmware byte and no
  fixture is in git: the image and the two card images stay under `out/`.
  `drive.py` takes `--image/--card/--set/--project` and finds the repo root
  from its new depth; `tmo.py`, `cmp_text.py`, `cmp_stamps.py` are unchanged.
- The cache is keyed on the binary's sha256 AND on the job's inputs
  (`inputs.txt`: path, size, mtime of the image and card, set, project; the
  interactive jobs also the driver's sha) — a changed fixture reruns.
- **`cmp_audio.py`** replaces `cmp` for the two audio captures.
  `cmp_audio.py A B [--fmt s16|wav24|auto] [--tol L] [--frac P] [--len-tol N]`
  reports on one line: frames per side, max |diff|, the differing count and
  percent, the first differing frame (channel, both values), the onset frame
  (first frame with any non-zero sample) per side, a trailing length
  difference, and a SHIFT HINT when B equals A displaced by ±1..3 frames; then
  PASS/FAIL. PASS needs the WAV header identical, |frames_A − frames_B| ≤
  len-tol, max |diff| ≤ tol, differing ≤ P % of the compared samples, and the
  onset frame identical (alignment is never tolerated). Byte-identical files
  short-circuit without decoding. Pure Python (the venv has no numpy):
  2,645,416 24-bit samples decode and compare in 0.4 s.
- `oracle.sh` gains `--audio-tol <lsb16>` (interdsp.pcm), `--wav-tol <lsb24>`
  (render.wav), `--audio-frac <percent>`; `--frame-tol N` now also bounds the
  audio captures' LENGTH (a frame edge on a run boundary). All default 0 =
  the strict Phase A gate. Everything else — logs, serial, goldens,
  `oracle.py`, UART stream, per-step sizes, peeks, replies, `ready` — stays
  byte-strict whatever the flags.
- `phase_b.sh CAND [--build-dir DIR]` runs the candidate against BOTH
  references with the Phase B tolerances (`--audio-tol 2 --wav-tol 8
  --audio-frac 0.5 --frame-tol 1`); the second pass only compares (the
  candidate's runs are cached).
- **The second frozen reference:** `out/emu/ot_emu.ref-73c2815` =
  `out/emu/ot_emu` at HEAD `73c2815` (Phase A's PGO binary, sha256
  `3c7d2111891c…`, the binary the last Phase A report gated 28/28), copied
  with `cp -p` and made read-only. Untracked, like `ref-1e76ac5`; keep both.

### The Phase B contract (decided for the DSP work; B0 and B1 held at 0)

Everything the oracle checks stays byte-identical — boot logs, serial,
goldens (dispatch order and stamps), the UART A stream (LCD/LEDs), peeks,
`run` stamps — EXCEPT the two audio artefacts, which may differ from the
reference within: `interdsp.pcm` (16-bit) max |diff| ≤ 2 LSB and ≤ 0.5 % of
samples differing; `run3_core0.wav` (24-bit words) max |diff| ≤ 8 and
≤ 0.5 % differing; the audio must stay sample-ALIGNED (the onset frame
identical); the frame counts in `run` replies and the captures' length may
differ by at most 1 (`--frame-tol 1`), only where a frame edge lands on a
run boundary. A step whose diff exceeds that FAILS. Phase B steps are gated
against BOTH references: strict against `ref-73c2815` on the non-audio
checks (and, since the two references are byte-identical on every check,
equally against `ref-1e76ac5`); the audio tolerance against either.

### Measured (12 Sep 2026, the same M5 Mac, macOS 26.5; reports under `out/_oracle/reports/`, logs under `out/_agents/speed-b0/`)

| run | result | wall | report |
|---|---|---|---|
| `ref-73c2815` vs itself (determinism; ctest on a fresh plain-LTO HEAD tree, `out/_agents/speed-b0/build`) | **28 PASS, 0 FAIL** | 47 s | `20260912-130930-b0-refref-73c2815` |
| `ref-1e76ac5` vs `ref-73c2815`, strict | **28 PASS, 0 FAIL** | 106 s (the 73c2815 side cached; the pre-speed side: card 42 s, render 73 s, interdsp boot 63 s + 43 s of `run`) | `20260912-131027-b0-1e76ac5-vs-73c2815` |
| negative control: `ref-73c2815` wrapped with `--rtc 1000000001`, WITH the Phase B tolerances on | **14 PASS, 13 FAIL** (27 checks, no ctest) | 44 s | `20260912-131239-b0-negctrl` |
| `phase_b.sh` on the plain-LTO HEAD build (sha `089bd73fb869`) | **28 PASS vs `ref-1e76ac5`, 28 PASS vs `ref-73c2815`** | 54 s + 4 s | `20260912-131324-…`, `20260912-131418-b0-phaseb-headlto` |

The negative control fails where Phase A's did — the `rtc` boot-log line,
the goldens at char 4870 (dispatch stamps moved 0.08 samples; `card.oracle_py`
one disagreement), `card.serial_a` at byte 5147, the UART stream at byte
5234 (the dialog's seconds digit), the clock record `..2e28` → `..2e29` —
and the two audio captures stay identical (the RTC does not reach the DSP):
the tolerance flags loosen nothing outside the audio. Per job on the Phase A
binary under the full parallel load: stock 1.0 s, card 6.6 s, render 27 s,
inter boot 6.6 s + 2.9 s of `run` (4,490 emulated ms), interdsp boot 20.5 s
+ 25 s of `run`, ctest 5.8 s.

`cmp_audio.py` checked on the real captures (the Phase A `interdsp.pcm`,
124,447 frames, onset frame 98; `run3_core0.wav`, 330,677 frames x 8 slots,
onset frame 282,744): identical → PASS in 0.02 / 0.04 s; 300 PCM samples
moved by ±1..2 → `max 2, 0.1125 %`, FAIL strict, PASS at `--tol 2 --frac
0.5`; the same moved by +3 → FAIL `max 3 > 2`; the PCM shifted by one frame
→ FAIL `onset moved: frame 98 vs 99` (+ `SHIFT HINT: B == A shifted −1
frame(s)`); one frame shorter → FAIL at `--len-tol 0`, PASS at `--len-tol 1`
with 0 samples differing; 500 WAV words moved by ±3..8 → `max 8, 0.0189 %`.
The shift hint is only emitted when the unshifted window is itself out of
tolerance (a first version fired on every shift inside digital silence).

### What it does not do

- The emulator is untouched: no source under `tools/emu/ot_emu/*.cpp/.h`
  changed, no CLI flag or output moved; the HEAD tree built for the ctest
  half is byte-identical to both references on all 28 checks.
- `out/_agents/speed-oracle/` is left in place (its reports and cached runs
  are Phase A's record; CONTEXT.md's "THE GATE" line still names it — the
  maintained copy is `tools/emu/ot_emu/oracle/`, and CONTEXT.md should be
  pointed at it with the next CONTEXT edit).
- The audio tolerance bounds |diff| and the differing fraction; it does not
  judge audibility or structure. A Phase B step that uses it must say where
  the diff sits (`cmp_audio.py`'s first differing frame) and why (O12).
- Nothing in the battery exercises `pace on`, threads, or shutdown timing;
  the Phase B steps that add threads must prove those separately (a
  `-fsanitize=thread` Debug build through `drive.py`, a measured quit/EOF/
  SIGTERM).

## Milestone O15g — the `hits` record, uncut: its own buffer ✅ (12 Sep 2026, branch `panel-ui`)

`main.cpp` (`serveInteractive`, the `hits` command only); no other reply,
no run-loop, batch or vendored change. O15f's last "does not do" item,
closed. Every `serveInteractive` reply was formatted through one shared
`char buf[160]`, and the `hits` record is the one reply that can exceed
it: 23 fields, ` %llx` for the instruction count (up to 16 hex digits)
and `:%x` for the 22 registers (up to 8 each), 1 + 16 + 22 × 9 = **215**
bytes when every value is wide. `snprintf` truncates silently, so the
client got a 158-character record with the trailing registers missing,
the last one shortened (a wrong value, not a missing one) or, when the
cut fell just after a `:`, an empty last field -- which the
`int(x, 16)` parse every instrument script does
(`out/_agents/seqled/probe.py`, `ledtimer.py`, `verifier/gate.py`)
raises on. The frame handler `0x4000ab1a` reaches it routinely: with
`a1`, `sp` and the five stack words all 8 digits, the record is over
160 before `d2..d7` start.

### What changed (`main.cpp`)

- **The record has its own `char rec[256]`** in the `hits` handler; the
  format string and the 23 arguments are as they were. The shared
  `buf[160]` still serves every other reply (`ready`, `status`, `ok`,
  `pacestatus`, `audio status`, the `err unmapped` lines and the
  six-field `writes` record), so no other command's output can change.
- **The protocol is unchanged**: `hits n=<count> <rec> ...` on one line,
  `<rec>` =
  `<instr>:<pc>:<d0>:<d1>:<a0>:<a1>:<sp>:<stack0..4>:<d2..d7>:<a2..a6>`,
  hex without `0x`. What changed is that a record is now always the 23
  fields the comment above the command promises. The parsers in
  `tools/panel` (none read `hits`; `KEYMAP.md` documents the format) and
  `out/_agents` (`seqled/probe.py`, `seqled/ledtimer.py`,
  `seqled/seqcheck.py`, `verifier/gate.py`, `verifier/seqcheck.py`,
  `samples/scan.py`, `impl-1-bursts-verify0/adv.py`,
  `impl-3-memory-verify0/instr_cmp.py`) all split the reply on spaces
  and each record on `:`; a longer record is what they were written for.

### Measured (12 Sep 2026, the same M5 Mac, macOS 26.5; logs under `out/_agents/fix-hits-buf/`)

`hits_len.py` boots the OTLIVE fixture (`--card out/_agents/port/otlive.img
--mount --set OTLIVE --project PROJECT --internal-clock --rtc 1000000000`),
watches `0x400622da,0x4009bc76,0x4000ab1a`, taps YES / MIXER / NO / PLAY,
runs 600 ms and reads `hits` once -- on this tree's build (`build/ot_emu`,
`cmake --fresh -B out/_agents/fix-hits-buf/build -S tools/emu/ot_emu`,
Release + LTO as the default configure) and, run only, on the pre-fix
`out/emu/ot_emu`:

| | `out/emu/ot_emu` (before) | `build/ot_emu` (after) |
|---|---|---|
| records | 3982 | 3982 |
| longest record (chars) | 158 | 181 |
| records cut at 158 | 36 | 0 |
| records with fewer than 23 fields | 34 (15 × 20, 11 × 21, 8 × 22) | 0 |
| records the scripts' `int(x, 16)` parse rejects | 8 | 0 |

(`hits_len_ref.txt`, `hits_len_fix.txt`; the record dumps beside them.)
The two sets are the same hits in the same order: record by record,
3946 are byte-identical and the other 36 -- every one a 158-character
before-record -- are proper prefixes of their after-record, which runs
159 to 181 characters (`prefix_check.txt`). The first cut one is the
frame handler with `a6 = 1`: before, `...:ffffff00:` (an empty 23rd
field); after, `...:ffffff00:1`.
The earlier instrument logs show the same defect in the wild:
`out/_agents/impl-3-memory-verify0/instr_ref.txt` has 85 of its 6363
`hits` records with fewer than 23 fields, and its candidate log the same
85 -- both binaries of that comparison were cut identically, which is why
the O15c gate could not see it. `ctest` in `build/`: 7/7 (`ctest.txt`).

### What it does not do

- It does not touch `out/emu/ot_emu` or `out/emu/ot_emu.ref-1e76ac5`:
  the build is under `out/_agents/fix-hits-buf/build/`; the operator's
  binary is rebuilt as before
  (`cmake -B out/emu -S tools/emu/ot_emu && cmake --build out/emu -j8`).
- A `hits` reply is still one line of unbounded length (the log is capped
  at 2M records, each now up to 215 bytes); a client needs a line reader,
  as every script above has.
- The other replies keep the shared 160-byte `buf`; none was measured
  against it here. The change is the `hits` record only.

## Milestone O16b — the DSP step, exact: the pair's per-instruction wrapper trimmed without moving a single interpreted instruction ✅ (12 Sep 2026, branch `panel-ui`)

Phase B's step B1 (proposal E1 of `out/_agents/speed-plan/REPORTS.md`,
prototyped as `out/_agents/speed-dsp/build1`): the part of the pair's cost
that is NOT the interpreter's work, taken out where it can be taken out
without changing the order or count of interpreted instructions, the ESAI
clock, a host-port event or a hook. Held to the STRICT gate (0 tolerance,
both references) because it changes no schedule. `dsp.cpp`, `dsp.h` only.

### What the wrapper cost, measured before the change

With `tickInstructions(1)` per ColdFire instruction, `m_due` grows by 1.043
per call and `runDue`'s quantum (64) is never reached in the RTOS phase:
each tick was one `runDue` and ~6 `stepCore` calls of which 2 executed an
instruction and 4 returned `false` on their first test (core at the due
count) -- and every one of those calls paid the prologue of a function that
also held a 256-byte trace line, a 24-hit PC-watch record, the TIMER0
capture and the fault message. On the O14k render command cut to 300 frames
(6.45 emulated s from the boot: `OT_DSP_STATS=1`, `stat-build.err`):
799.4 M `runDue` calls made 1,231.4 M passes and 859.0 M interpreter steps
(435.1 M core 0, 423.8 M core 1) -- i.e. the shipped code made 3.32 G
`stepCore` calls for 0.86 G instructions (one returning `false` per core
per pass, 2,462.8 M, plus one per instruction). **831.8 M of the 859.0 M steps
(96.8 %) are idle steps**: a poll executed after an `idleStep` whose room
was 1 or 2 instructions (core 0 95.3 %, core 1 98.4 %). Under the exact
schedule that is the shape of the work: the fast-forward advances by the
room the due count gives it, and that room is what the ColdFire's tick is.

### What changed (`dsp.cpp`, `dsp.h`)

- **`runDue` skips a core that is already at the due count** on a compare
  (`!c.faulted && executed >= due`): `stepCore` would have returned `false`
  at once with no side effect. A FAULTED core keeps its call, because that
  call has one (`executed := limit`; O8's fault path); a core still in the
  bootstrap ROM is skipped only when at the due count, where its
  `max(executed, limit)` is a no-op. Same passes, same order (core 0 to its
  quantum, core 1 to its, again until a pass runs nothing).
- **The per-instruction body is `stepBody`** (stepCore minus its three
  runnable checks), and `runDue` loops on it with the limit test inline:
  `while(executed < lim) stepBody()`. The calls that only returned `false`
  are gone; the count of bodies executed is the count of instructions
  interpreted before (the `interp` counters, below, match the O9b
  `executed` arithmetic call for call). `stepCore` itself is unchanged for
  `runCoreUntil` (the read-back pull) and now delegates to `stepBody`.
- **The cold parts are out of the body** as `noinline` helpers, in the same
  places in the same order: `faultPc` (the message, `executed := limit`),
  `timerCapture` (the TIMER0 first-enable record: the batch report prints
  it, so the `readTCSR(0) & 1` test -- an inline load -- stays on every
  instruction; only the capture moved), `instrumentBefore` (the stopwatch,
  then the PC watch) and `traceLine`, the last two behind ONE flag
  `m_instrumented` = trace armed or PC watch armed or stopwatch on core
  0/1, refreshed by the setters. The PC ring stays on every instruction
  (the fault report's "last PCs", the write watch's `last[4]` and the
  timer capture read it).
- **`doLoopEnd` is called only with SR_LF set** (`regs().sr & 0x8000`):
  its own first test is `sr_test_noCache(SR_LF)` and it returns `false`
  without touching a register otherwise, so the gate is exact.
- **Counters** (`DspPair::Stats`: `runDue` calls, passes, `stepCore`
  wrapper calls, interpreter steps and idle steps per core; one add each,
  always on) and an opt-in dump on stderr at exit: `OT_DSP_STATS=1`. No
  new command, nothing on stdout, nothing without the variable.
- NOT changed: the double `m_due` arithmetic (the prototype's integer
  version moved the idle horizon: `idle=` 63749 → 63755), `room`
  (`limit - executed`, the same limit), the idle-window detection, the
  bank-word hook, `tickInstructions`/`tickSamples`, `runCoreUntil`, every
  hook and every report line.

### Measured (12 Sep 2026, the same M5 Mac, macOS 26.5, with another agent's Python at ~99 % of a core throughout; logs under `out/_agents/speed-b1/`)

**The strict gate (0 tolerance, `tools/emu/ot_emu/oracle/oracle.sh`, no
tolerance flag, `--build-dir` for the ctest half; reports under
`out/_oracle/reports/`):**

| check | result | report |
|---|---|---|
| B1 (`build/ot_emu`, plain LTO, sha `372b19b0a8b8`) vs `ref-1e76ac5`, strict | **28 PASS, 0 FAIL** (ctest 7/7), 50 s | `20260912-133355-b1-vs-1e76ac5` |
| the same vs `ref-73c2815`, strict | **28 PASS, 0 FAIL**, 3 s (the candidate's runs cached) | `20260912-133445-b1-vs-73c2815` |
| `idle=` in the four `status` replies (stripped by the oracle; the prototype's integer due arithmetic had moved it) | 42022 / 42023 / 54842 / 66357 on both sides (`out/_oracle/runs/{3c7d2111891c,372b19b0a8b8}/{inter,interdsp}/wall.txt`) | – |
| the render command cut to 300 frames, HEAD build vs B1 (`stat-*.log`, `stat-*_core0.wav`) | WAV byte-identical (284,261 frames), log identical bar the output path | – |
| B1 vs itself (the oracle's determinism rerun into `runs/<sha>-b/`) | **27 PASS, 0 FAIL**, 44 s (no `--build-dir`: no ctest row); `20260912-134318-b1-determinism` | – |
| B1's PGO build (`build-pgo`, `pgo.sh --dest/--gen/--prof` under `speed-b1/`) vs `ref-73c2815`, strict, no ctest | **27 PASS, 0 FAIL**, 41 s; `20260912-135047-b1-pgo-vs-73c2815` | – |


**Speed (`out/_agents/speed/bench.py`: boot on the OTLIVE card, PLAY,
4 emulated s in 16 x `run 250`; emulated ms per wall s, wall of the 16 runs
in brackets; HEAD and B1 alternated round by round so both saw the same
load; HEAD = `git archive HEAD tools/emu/ot_emu` built in `build-head/` with
the default configure = Release + LTO, B1 = `build/` the same way):**

| round | HEAD `--dsp` | B1 `--dsp` | HEAD no `--dsp` | B1 no `--dsp` |
|---|---|---|---|---|
| r1 | 151 (26.53 s) | **170** (23.55 s) | 1432 | 1362 |
| r2 | 153 (26.08 s) | **170** (23.58 s) | 1451 | 1483 |
| r3 | 152 (26.30 s) | **168** (23.77 s) | 1388 | 1380 |
| mean | 152.0 | **169.3 (+11.4 %)**; wall 26.30 → 23.63 s (−10.2 %) | 1424 | 1408 (noise: the pair is not constructed without `--dsp`) |
| boot to `ready`, `--dsp` | 23.5 / 22.7 / 22.9 s | **19.2 / 19.3 / 19.2 s (−16 %)** | – | – |
| PGO, alternated: `ref-73c2815` (= HEAD's `pgo.sh` binary) vs B1 through `pgo.sh --dest/--gen/--prof` under `speed-b1/` (its own training runs) | `ref-73c2815` 173 (23.17 s) / 172 (23.20 s) / 174 (22.98 s) / 174 (22.92 s), mean 173.2 | B1 `build-pgo` **178** (22.49 s) / **179** (22.30 s), mean **178.5 (+3.0 %)**; wall 23.07 → 22.39 s | – | – |

The 300-frame render above, run while the oracle's eight jobs loaded the
machine: 26.83 → 22.23 s of wall (−17 %; the boot and the idle-heavy
pre-roll are where the wrapper was the largest share).


**Where the time went (bench.py's 10 s `sample` mid-play, round 2,
`b1_build-head_dsp_r2.sample.txt` / `b1_build_dsp_r2.sample.txt`,
`out/_agents/speed-dsp/agg.py`; self time as a share of the same 10 wall
seconds, which on B1 cover 11 % more emulated time):** HEAD `stepCore`
35.9 % + `runDue` 1.2 % = **37.1 %**; B1 `stepBody` 23.7 % + `runDue` 7.8 %
= **31.5 %** — the wrapper's self samples fell 2,881 → 2,441 (−15 %) while
the interpreter's own rose as a share (`op_Parallel` 3.9 → 4.5 %,
`alu_multiply` 3.6 → 3.7 %, `op_Mac_S1S2` 1.3 → 1.9 %), which is the work
that was waiting behind it. Inclusive, `runDue` is 67.7 % on both. What is
left in `stepBody`'s self time is the interpreter's dispatch inlined into
it (`m_interruptFunc`, `fetchPC`, the `execOp` member call), the idle path
(`idleStep`, 96.8 % of the steps) and the four compares per tick — all per
instruction whatever wraps them.


### What it does not do

- The `--dsp-trace` line keeps its 256-byte buffer (`traceLine`; clang
  warns the format can reach 311): the truncation is O9b's and the trace
  output must not change here.
- It does not touch the interleave, so it does not touch the ceiling: the
  pair still executes every idle poll the exact schedule gives it, 831.8 M
  idle steps for 27 M of real work on the render fixture. That is E2/E3's
  ground (the lazy batch / the thread), under the Phase B audio tolerance.
- The gain is what the wrapper had to give under exactness, not the
  investigation's 10-15 % estimate: **+11.4 % on the plain-LTO build,
  +3.0 % on the PGO build** (the operator's binary, `out/emu/ot_emu` via
  `pgo.sh`), because PGO had already inlined `stepCore` into `runDue` and
  made the returning-false calls cheap; the architect's +2.6 % on the
  prototype was that figure. The interpreter's own dispatch
  (`m_interruptFunc`, `fetchPC`, `execOp`) and the ESAI clock are per
  instruction whatever wraps them.
- `OT_DSP_STATS` prints at destruction: a run that ends through
  `std::exit` or a signal prints nothing. Batch and `--interactive quit`
  both destroy the pair.
- No thread, no new flag, no CLI change: batch mode and every script that
  drives `ot_emu` see the same bytes (the 28 checks, twice).

## Milestone O16c — lazy batching of the DSP pair: the ticks booked and replayed in chunks, the frame edge guarded, bit for bit ✅ (12 Sep 2026, branch `panel-ui`)

Phase B's step B2 (proposal E2 of `out/_agents/speed-plan/REPORTS.md`,
prototyped as `out/_agents/speed-dsp/build1`'s `--dsp-lazy`). The pair no
longer runs after every ColdFire instruction: a tick only BOOKS the due
count, and the backlog runs in one chunk at the points where the ColdFire
can observe or affect the cores. Held to the STRICT gate in the end (0
tolerance, both references, 28/28), not the Phase B audio tolerance it was
allowed: the two designs that moved the schedule failed the gate by far
more than an LSB, and the one that ships is the exact schedule replayed.
`dsp.cpp`, `dsp.h`, `rtos.cpp`, `rtos.h`, `machine.h`, `main.cpp`. Default
on in every mode; `--dsp-lazy 0` restores the per-tick path.

### What changed

- **`Coprocessor::sync()`** (`machine.h`, default no-op): "bring the
  co-processor up to everything booked so far". `Rtos::runLoop` calls it
  before `tickTimers()`/`deliver()` at every burst end and `stepOnce` after
  every exact instruction -- the point where the pair's state becomes
  observable (the frame latch, the eDMA gate) and where the old loop had
  already run it (inside each instruction's tick). `OT_DSP_SYNC=0` drops
  those two calls (a measurement knob: what the per-burst sync costs).
- **`DspPair::setLazy(N)`** (`--dsp-lazy N`, default
  `g_lazyDefault` = 4160 = one sample; 0 = exact): `tickInstructions`
  adds `ratio` to `m_due` as before and counts the tick; the backlog runs
  through **`runChunk`** when it reaches N inside a burst, at `sync()`, at
  every host-port touch point (`read`/`write` of the window, the eDMA's
  `pushHalfwords`/`pullHalfwords`/`hostRingEmpty` gate, `runCoreUntil`,
  `blockNote`, `peekWord`), before the idle skip's `tickSamples` (which
  then steps one sample at a time as before), and inside every probe
  (`peekP/X/Y`, `pc`, `executed`, `idleSkipped`, `report`; const, so they
  cast -- what they observe is the pair NOW). The boot (before the Rtos)
  is exact: its report is in every boot log.
- **`runChunk` REPLAYS the tick sequence**: the same `+= m_ratio`
  additions from the same value (every limit the same double), and for
  each, core 0 to it then core 1 to it -- `runDue`'s one pass per tick
  (its 64-quantum never bites on a tick, O16b). So the cross-core
  interleave, every idle step's room and every peripheral event fall on
  the same DSP instruction as under the per-tick schedule; the chunk saves
  the per-tick call and its pass bookkeeping, nothing else. `tickSamples`
  (the ColdFire idle skip, whole samples, 64-quanta) and the exact
  `--dsp-lazy 0` path still go through `runDue`.
- **THE EDGE GUARD** (`predictEdge`): the one event the DSP raises on its
  own that the ColdFire must see at the instruction is the bank word
  (P:0x73, the frame edge). It is predictable: the dispatcher writes it
  when DMA2's source pointer equals 0x8070 or 0x80f0 (a one-slot equality
  window, O8), DSR2 advances one word per ESAI slot exec (`writeSlotToFrame`
  triggers the DMA before the frame callback), and the slot cadence is
  `esaiCyclesPerSlot` = 520 instructions on the DSP's own counter. The
  frame sink records (counter, DSR2) at each callback -- a slot exec -- and
  from that grid point the boundary slot is `togo` slots on; from three
  slots before it until one slot after (or until the write fires) every
  tick is exact (`m_due >= m_edgeGuard` in `tickInstructions`), lazily
  elsewhere; a prediction that finds no new callback retries one ESAI frame
  (8 slots) later. `stepBody`'s bank-write path reports an edge that fires
  outside a window as `edges-late`. Edges inside idle skips are the skip's
  own (whole samples, as before) and counted apart.
- **Counters** on the O16b `OT_DSP_STATS=1` line: `hostR/hostW`,
  `syncs`, `chunks` with the mean and max backlog, `edges` with their
  lateness, `edges-late`, `edges-in-idle-skips`, `guardticks`,
  `predictions`. `OT_DSP_EDGELOG=1`: one stderr line per bank write seen
  from a chunk (the guard's evidence, `out/_agents/speed-b2/edgelog*.txt`).
  No command, reply or stdout line changed.

### The two designs that failed the gate, measured (`out/_agents/speed-b2/`)

1. **The chunk through `runDue` (64-instruction quanta), no guard** -- the
   E2 prototype's shape. Bench **244 / 246 / 244** ms per wall s (+46 %),
   `ready` 7.6 s. Gate: `interdsp.stamps` max |dsample| 16.63 (a run end
   moved a frame period), the PCM 16 frames short and **max |diff| 4301,
   16.6 % of samples differing** (`20260912-142635-b2-lazy`). Not a shift
   (the best frame shift is 0 in every window) and not an LSB drift: at
   frame 49703 the candidate reads 6806, 12851, 22777 where the reference
   has 6689, 11889, 20756 -- a gain ramp one frame ahead, converging to
   1-LSB tails. The frame interrupt was delivered up to a chunk late
   (mean 1141 DSP instructions for the 6 % of edges that fall in bursts,
   max 4158), the block reached the DSP that much later, and where that
   crossed a bank boundary the port's known 15/17-frame jitter moved: a
   parameter block met its audio a frame off.
2. **The same chunk with the guard.** `edges-late` 152 of 413 on the first
   build (the retry after a window waited 128 slots = the boundaries' own
   period, so every retry landed past the next boundary; `edgelog.txt`),
   0 of 412 once the retry was one ESAI frame -- and the gate still failed:
   UART 18309 vs 18301 bytes (a block crossed the PLAY step), stamps
   max |dsample| 46.82, PCM 42 % differing (`20260912-144242-b2-guard2-
   interdsp`); the batch WAV byte-identical but its log's ack tail with
   vector 0x48 (the eDMA completion) acknowledged at other PCs. The
   cross-core interleave: inside a 64-quantum chunk core 0's mailbox
   waits on core 1 end up to a quantum early or late, and with them the
   bank write and the host ring's drain -- by up to 61 ColdFire
   instructions, enough to move both interrupts.

Replaying the tick sequence (above) removed every difference:
`20260912-144621-b2-replay-interdsp` 8/8 with the PCM byte-identical.

### Measured (12 Sep 2026, the same M5 Mac, macOS 26.5; nothing else running; logs under `out/_agents/speed-b2/`)

**The gate** (`tools/emu/ot_emu/oracle/`, final binary sha `0605bde48918`,
`--build-dir` for ctest):

| run | result | report |
|---|---|---|
| `phase_b.sh` vs `ref-1e76ac5` (Phase B tolerances) | **28 PASS, 0 FAIL**, 36 s | `20260912-145609-b2-final` |
| `phase_b.sh` vs `ref-73c2815` | **28 PASS, 0 FAIL**, 3 s (cached) | `20260912-145646-b2-final` |
| strict, no tolerance flag, vs `ref-73c2815` | **28 PASS, 0 FAIL** | `20260912-145702-b2-final-strict` |
| B2 vs itself (determinism) | **27 PASS, 0 FAIL**, 36 s | `20260912-145706-b2-determinism` |

Every check byte-identical, the audio included: `interdsp.pcm` 124,447
frames identical (max |diff| 0, 0 % differing, onset frame 98),
`run3_core0.wav` 330,677 frames identical (onset 282,744), the UART stream
18,309 bytes, 47 run stamps with |dsample| = 0, the boot logs and the batch
log with the pair's own report (executed, idle-skipped, bank latency)
identical -- which is why the default is on in the batch too.
`edges-late` **0** of 741 chunk edges over the bench session (+10,727 in
idle skips), 0 of 413 in the edge log.

**Speed** (`out/_agents/speed/bench.py --dsp`, HEAD = `git archive HEAD`
built in `build-head/`, both plain LTO, alternated round by round;
emulated ms per wall s, the 16 runs' wall in brackets):

| round | HEAD (B1) | B2 | `ready` HEAD / B2 |
|---|---|---|---|
| r1 | 169 (23.69 s) | **203** (19.72 s) | 18.5 / 14.9 s |
| r2 | 166 (24.05 s) | **204** (19.60 s) | 19.6 / 14.5 s |
| r3 | 168 (23.87 s) | **203** (19.70 s) | 18.8 / 14.4 s |
| mean | 167.7 | **203.3 (+21 %)**; wall 23.87 → 19.67 s (−18 %) | 19.0 → **14.6 s (−23 %)** |
| `--dsp-lazy 64` / `1024` | – | 199 / 202 | – |
| `OT_DSP_SYNC=0` | – | 206 (the per-burst sync is free: the host port syncs it first) | – |
| `--dsp-lazy 0` (the per-tick path) | – | 166 = HEAD | 20.1 s |
| no `--dsp` | 1434 | 1418 (noise; the pair is not constructed) | 5.4 / 5.4 s |

**Where the ceiling is** (`stats.py`, the play phase = boot→PLAY + 4 s):
the pair's work is identical to HEAD's -- core 0 763.2 M due, 26.0 M
idle-skipped, **733.2 M interpreted** (HEAD 733.6 M), core 1 324.3 M
interpreted with 61.3 M idle steps (HEAD 324.3 M / 61.3 M) -- and what the
chunk removed is the per-tick wrapper: `runDue` calls 356.3 M → 0.10 M,
passes 709.5 M → 6.2 M, 38.1 M chunks of 9.4 ticks on average (the
firmware makes 8.2 M host-port accesses per emulated second while playing:
8.83 M reads, 25.3 M writes over the phase, most of them the eDMA's
halfwords), 2.3 M guard ticks (0.65 %). Per emulated second the pair now
costs ~4.2 wall s of the 4.9 (the ColdFire ~0.7): ~1 G interpreted DSP
instructions per 4.16 s at ~4 ns each is the vendored interpreter's own
throughput, E4's ground and outside this plan. Profile (`sample`, 10 s
mid-play, `agg.py`): `stepBody` self 25.0 %, `runChunk` 3.8 % (was
`runDue` 7.7 %), `op_Parallel` 5.2 %, the pair 69 % inclusive.

**The panel end to end** (`panel_e2e.py`, `panel_server.py --port 8584
--sound on` on the OTLIVE fixture, this binary): `ready` after 14.7 s;
idle 999.7 emulated ms per wall s, `/status rt` median 1.000 (0.993-1.007),
child 31 % of a core; PLAY: the trig rows chase (31 LED-state changes in
12 s), **playing 206 emulated ms per wall s, `rt` median 0.203**
(0.190-0.226; O15f/B1 measured ~0.15-0.17), child 97 %; `/audio/pcm` of
the last second 44,100 frames, 88,200 non-zero samples; STOP closed
`take-003.wav`, 406,020 frames = 9.2 s, 1,624,124 bytes; `rt` 0.993 back
at idle, no fault, no restart.

**Shutdown** (`shutdown.py`, `--interactive --dsp` with `audio start
main`): `quit` 22 ms, EOF 22 ms, SIGTERM 22 ms, SIGTERM with `run 2000` in
flight 23 ms. No thread was added (the chunk runs on the caller's thread),
so there is nothing for `-fsanitize=thread` to find; the determinism run
above is the deterministic-handshake proof.

### What it does not do

- It does not batch across the host port: a chunk ends at every host-port
  access and burst end, and while playing the firmware touches the port
  34.1 M times in 356 M instructions (the eDMA's halfwords included), so
  the mean chunk is 9.4 ticks and N (64..4160) barely matters.
  The gain is the wrapper, not the interpreter; the interpreter is ~85 % of
  the `--dsp` wall.
- The schedule-moving designs are not shipped, not even opt-in: they
  failed the Phase B contract by orders of magnitude (16-42 % of samples,
  thousands of LSB), because the port's frame/bank phase is one slot from
  a boundary and any lateness of the frame interrupt or any cross-core
  skew moves it. The contract's LSB expectation (O12) was about the
  interleave inside a chunk with the ColdFire's view held; that view is
  what has to stay exact.
- The edge guard is payload A's: it knows the ring (X:0x8000-0x80ff), the
  two boundaries and P:0x73. A payload that moves them gets no window and
  the edge lands a chunk late (`edges-late` says so); `--dsp-lazy 0` is
  the fallback.
- `OT_DSP_STATS` prints at destruction, as in O16b; `OT_DSP_EDGELOG` is a
  diagnostic and prints nothing without the variable.

## Milestone O17 — `--dsp-rt`: the DSP cores as JIT workers on the lockstep schedule ✅ built, ⚠️ not real time (12–13 Sep 2026, branch `panel-ui`)

The spike (`out/_agents/jit-spike/SPIKE.md`) proved the vendored JIT
(dsp56300 at `3c01813f`, HAVE_ARM64) executes both of this firmware's DSP
programs once five defects are patched, and that free-running cores are a
dead end (the frame protocol reads the bank id with no ready check and
halts on thread-level jitter). This milestone builds the design it
recommended instead: **the ColdFire stays the master of emulated time and
O16c's booking is kept exactly, but the backlog is executed by two worker
threads under the JIT** — one per core, each run to the booked count and
never past it — so the ColdFire's bursts and the cores' chunks overlap in
wall time and the ordering the firmware depends on is lockstep's by
construction. `--dsp-rt` is opt-in, `--interactive` only; every other mode
is untouched (the strict oracle: 28/28 byte-identical, ctest 7/7). The
panel spawns it with `--sound on` and falls back to `--dsp` when it cannot
start. **Real time was the target and is not reached: the unit plays at
0.62–0.65x (3.2x the lockstep 0.2x), for reasons measured below.**
`dsp.cpp`, `dsp.h`, `machine.h`, `rtos.cpp/.h`, `main.cpp`,
`tools/patches/dsp56300.patch` (vendor/dsp56300 in place),
`tools/panel/panel_server.py`, `tools/panel/README.md`.

### What changed — the design as built (`dsp.cpp`, "THE REAL-TIME MODE")

- **Workers on the lockstep schedule.** `DspPair(ratio, ips, rt=true)`
  starts two pthreads (16 MB stacks — the JIT compiles on the core's
  thread; `ThreadPriority::High` = QoS user-initiated; the ColdFire's
  thread joins that band under `--dsp-rt`). Each waits for its boot ROM
  to jump (`sendWord` says so and records the core's **offset**: lockstep's
  `executed := limit` while held, so `executed = counter + offset` from
  then on), then loops: read the posted due count (one shared cache
  line, `m_rtDue`/`m_rtGen`; the worker subtracts its offset and adds the
  lead), run `execJit()` — the peripheral service, then one block and its
  linked children — while its counter is below the target, publish the
  counter every 256 instructions and exactly when it stops (`reached`,
  its own line), spin 200 µs on an empty target, then park on a condition
  variable (the poster stores the count, bumps the generation and looks
  at `parked`, both seq_cst, so a post is never lost).
- **The touch points post; observing ones wait for nothing new.** Every
  O16c touch point (sync at burst ends and exact steps, host-port
  read/write, `pushHalfwords`/`pullHalfwords`/`hostRingEmpty`,
  `tickSamples`, the probes) goes through `rtCatchUp`: a post when the
  count moved by a quantum (writes 32, reads 32), and a wait only when a
  core LAGS the count by more than `OT_RT_LAG` (4160 = one sample; read at
  the burst ends every 512 counts). A core behind the count only makes the
  host look faster to it — a word lands earlier in DSP time, a command is
  taken earlier — which the protocol lives with; a core AHEAD is what
  broke the spike, and the target forbids it. Tick booking is unchanged
  (`m_due`, the boot's per-instruction ratio and the RTOS's per-sample
  clock); the boot posts every 512 ticks (it has no sync).
- **A bounded lead.** `OT_RT_LEAD` (default 8320 = 2 samples): the workers
  may run that far ahead of the count. Traced through the frame protocol
  the DSP's own timeline is unchanged by it — it sees the host's actions
  L later in its time, the ColdFire sees the bank-word edge L earlier —
  and the double-buffered blocks land well inside the frame; **4 samples
  measured healthy, 8 stalls the protocol** (`edgesinpull` 13 at 16, core
  0 parked at P:0x97 with its bank word untaken: the next bank word lands
  while the ColdFire is still inside the previous frame's bus-paced
  read-back pull), which is the firmware's timing assumption the spike
  named.
- **The bank-word edge.** Every peripheral write made by JIT code carries
  the PC of the instruction (the vendored `Jitmem::storePcCurrentOp`), so
  the HDI08 transmit callback on core 0's thread identifies P:0x73 →
  HOTX and raises an atomic count (`m_edgePending`, raised inside
  read-back pulls too, as `stepBody` has it); `Rtos::runLoop` asks
  `Coprocessor::edgePending()` every 64 instructions of a burst and ends
  it, and `sync()` applies the existing host-word hook on the ColdFire's
  thread (`rtApplyEdges`, with the lateness recorded: the count minus the
  edge's executed count). O16c's edge guard is not the default: as a
  per-tick rendezvous (`OT_RT_GUARD=1`, 29.9 M guard ticks in 4 s) it
  costs nothing measurable and changes nothing measurable — 583 vs 581
  emulated ms per wall s, mean lateness 85 vs 116 DSP instructions —
  because the lead already keeps the workers ahead of the count.
- **The two cores' interleave.** Each worker holds itself within
  `OT_RT_SKEW` (512) instructions of the other (`rtSkewWait`: the mailbox
  handshake at P:0x74/0xa3 and 0x57/0x8d would otherwise spend a core's
  budget spinning on the other), except when the other is idle for the
  current post (at its target, stopped at a skip's edge, its pull's word
  there) — ❌ without that exception core 1 waited the full 200 ms
  timeout 728 times in one `run 250`. A read-back pull runs its core past
  the count until HOTX holds the word (`RtCore::pred`), and the other core
  follows it to the same point, as `runCoreUntil` steps the other core.
- **The idle skip** (`rtTickSamples`): the whole skip is posted with the
  workers armed to stop at the first bank-word edge inside it
  (`m_skipArmed`/`m_skipEdge`); the ColdFire's clock lands on the sample
  boundary after the edge (lockstep's per-sample grain, `skipedges`), the
  targets come down to it, and the edge is delivered there.
- **Host commands** go through the library's cross-thread door
  (`injectExternalInterrupt`) and a per-core `kick` the worker turns into
  the core's interrupt queue before its next block — ❌ a zero peripheral
  delay stored from the ColdFire's thread was overwritten by the core's
  own service and the command waited for the next ESAI slot, up to 520
  instructions: 29 M extra exact ColdFire steps behind the eDMA's drain
  gate in 2 s. ICR host flags via `setPendingHostFlags01`; HCP is not
  raised in HSR (the core's register); an INIT with TREQ (never written by
  the firmware) does not clear the receive ring from this thread
  (`treqdropped`).
- **The shared window** is one memory through the MMU: after the two
  `Memory` objects exist, one 256 KB shm object is mapped `MAP_FIXED` over
  words 0x30000–0x3ffff of all six views (2 cores × P/X/Y), verified
  word for word at setup; the mode is refused when `Memory` is not
  MMU-backed. `setSharedWindow`'s redirect and cross-core opcode-cache
  hook and the write hook are not installed (the JIT reads and writes
  through host pointers; the hook would touch the other core's JIT from
  the wrong thread).
- **The audio pipe**: the ESAI sink on core 0's thread pushes de-rotated
  frames into the ring under `m_streamMx`; `setAudioStream`,
  `takeAudioStream` and `streamStatus` lock it. No pacer thread: wall
  pacing is `pace on` as before.
- **The gate in bursts.** Behind the eDMA's drain gate the exact loop
  steps one instruction at a time (~100 ns each, bit-exact completion
  timing for the lockstep modes: 18 M steps in 4 s); the rt mode steps the
  gate in bursts of 32 (`rtos.cpp`, the completion lands at most 32
  instructions late).
- **The eDMA mover's block notes** (`Rtos::installHostPortMover`) read
  DMA0's pointer and the landed words through `peekWord` at every
  completion; they are the block log's and the block dump's, and are now
  made only when one of those is on (the note string was built and
  dropped otherwise) -- under `--dsp-rt` those are the core's thread's
  registers and memory, and every one of the thread sanitizer's reports
  was that read against the DMA's write (below). Byte-neutral: the log's
  content when on is what it was.
- **The poll fast-forward, JIT edition.** A block re-entered eight times
  in a row with no DO loop open, nothing pending and no pull running is a
  poll (P:0x57, 0x8d, 0x97, 0xa3: one instruction on itself), and payload
  A's three-block DSR2 poll re-entered at its head P:0x4b is the one
  multi-block loop allowed (its iteration count in b1 — the DSP's own idle
  meter, written to X:$3f80/$3f81 after the bank word — is kept faithful,
  seven instructions per skipped iteration); the worker then advances to
  its next peripheral event through `DSP::idleStep` and executes the poll
  once. Measured: it matters at idle (the cores cost ~0.2 of a core each
  at 1.00x idle, `xmips` 0.4) and hardly while playing — on this fixture
  core 0 executes 180–190 M of its 183.5 M instructions per emulated
  second, i.e. the DSP is loaded ~95 % and there is nothing to skip.
- **Faults never block the ColdFire**: a PC outside P memory stops the
  core (`faulted`, the last 64 block-entry PCs in the report); a core that
  does not reach the count within 2 s of a ColdFire wait is faulted by the
  waiter; a read-back word not there within 100 ms is `not in time`.
  `~DspPair` sets stop, wakes and joins the workers before anything they
  touch is destroyed; `quit`/EOF go through the existing path.
- **Commands and knobs**: `rtstatus` (`--dsp-rt` only; MIPS per core and
  executed MIPS, busy fraction, worker CPU seconds, core 0's ESAI frames,
  the count and each core's lag, posts/wakes/parks, waits, edges raised /
  applied / inside pulls with their lateness, skip edges, skew waits,
  fast-forwarded instructions, pulls and their time, read-back words not
  in time, dropped host words, faults, the knobs, the first read-back
  words not in time with both workers' state), `cfstatus` (any mode: the
  ColdFire's instruction count and clock — the rate meter that settled the
  bottleneck below), and `OT_RT_LAG / LEAD / SKEW / POSTQ / READQ /
  READWAIT / CHECKQ / TICKPOST / SPIN_US / FF / GUARD / DOITER /
  WORKER_QOS / TRACE` (diagnostics; the defaults are the mode) plus
  `OT_SELFPROF=<hz>` (an in-process sampler of the main thread, because
  the macOS `sample` tool, `lldb -p` and TSan's symbolizer all hang on
  this process — its MMU-backed DSP memory maps three 64 MB views per
  core). The interpreter's per-instruction instruments (`--dsp-trace`,
  `--dsp-pcwatch`, `--dsp-stopwatch`, `--dsp-watch`, `--dsp-map`,
  `--dsp-writes`, `--dsp-no-idle`) do not observe the JIT workers (a note
  in the boot log says so).
- **The panel** (`tools/panel/panel_server.py`, `tools/panel/README.md`
  "Hearing the unit"): `--sound on` spawns the child with `--dsp-rt`; an
  rt child that reports `dsp-rt : cannot start` or exits before `ready`
  is respawned once with the lockstep `--dsp`, `backend_note` says why and
  `sound_note` says what to expect (~0.65x rt, ~0.2x lockstep); `/status`
  adds `sound_rt`; `GET /rtstatus` relays the child's `rtstatus` (`ok`
  false with the reason when the child has none). The owner's
  `out/emu/ot_emu` predates `--dsp-rt`, so until `pgo.sh` rebuilds it the
  panel takes the fallback (measured below).

### Every vendor fix (`tools/patches/dsp56300.patch`, 864 lines, 20 files; proven with `git apply --check` on a scratch worktree of `3c01813f`, the applied diff byte-identical to the patch)

The spike's five, each found by an instrument (SPIKE.md): (1) the JIT
function tables pre-sized to all of P at setup (`notifyProgramMemWrite
(sizeP-1)`: they grow only with the P addresses the same core writes, and
core 1's entry P:0x38000 is written by core 0 through the window — a null
call on the core 1 thread); (2) `setDmaTriggerOnArm` (`dma.cpp`
`checkTrigger`: a channel armed with its request already holding fires
once for a non-host-stepped core too — DMA2 armed with TDE set never
started); (3) `JitConfig::dynamicFastInterrupts = true` (payload A's
dispatcher lives inside the vector area P:0x40–0xaf, reached by a plain
`jmp`; the JIT compiled it as two-word fast-interrupt blocks returning to
the interrupted PC); (4) `pushPCSR` pushes the next PC in Dynamic mode
unless the processing mode is FastInterrupt (`jitops_helper.cpp`, a csel;
`jsr` from such a block pushed the interrupted PC and re-entered forever);
(5) the peripheral-write PC (`jitblock.h/.cpp` `m_pcCurrentOp`,
`jitmem.h/.cpp` `storePcCurrentOp` in both `writePeriph` emitters,
`callDSPMemWritePeriph` invalidates it after; `dsp.h`
`get/setPcCurrentInstruction`). Plus `setPeripheralsUnderMaskedInterrupt`
(O8's defect 3 as a flag for a non-host-stepped core), the HDI08 transmit
FIFO (`setTransmitFifoDepth`, 1024 here) with the receive-side burst
drain, and three of this milestone's own: (6) **the FIFO transmit
request is level-sensitive** (`hdi08.cpp`: with a FIFO, room is a standing
request and the DMA fills it as a burst in one service — ❌ the spike's
edge-triggered shape delivered one word per service whenever the host
drained the FIFO between two services, and every core-1 read-back pull ran
its core 5000–8300 instructions past the count; then every eDMA push
found both cores ahead and its drain gate stepped 1100+ instructions
instead of lockstep's 494); (7) the DRS bound check (`dma.cpp`: the
request source is a 5-bit field indexing a 21-entry array; the spike
crashed in `setDCR` on a garbage DCR); (8) `setDelayCycles(0)` stores a
zero target instead of reading the DSP's instruction counter (it is
called from the host's thread by `writeRX`/`readTX`/`clearRX`); and a
diagnostic getter (`getExecPeripheralsFunc`). The spike's
diagnostics-only hunks (`peripherals.cpp` DSR2/peripheral-write logs,
`jitblockchain.cpp` compile timer) are not carried.

### Measured (12–13 Sep 2026, the M5 Max, macOS 26; logs under `out/_agents/jit-build/`; a stuck spike process, `build-tsan/ot_emu --dsp-rt` pid 51757 at 98 % of a core, ran throughout — not this session's to kill)

**The gate.** `oracle.sh out/emu/ot_emu.ref-73c2815 <cand> --build-dir`,
strict, no tolerance: **28 PASS, 0 FAIL** (ctest 7/7) on the LTO-off build
(`20260912-230157-jit-build-early`) and on the LTO build
(`20260912-235112-jit-build-lto`, then `20260913-000250-jit-build-lto-final`,
`-final2` and `20260913-001015-jit-build-lto-final3` on the final source,
sha `b98fd9b51b9e`). Batch, `--interactive --dsp` and no-DSP are
byte-identical.

**Speed** (`bench.py <tag> --dsp --dsp-rt`, the extra arguments forward as
they are; 4 emulated s of PLAY in 16 × `run 250`, emulated ms per wall s):

| build | `--dsp --dsp-rt` | `--dsp` (lockstep) | no `--dsp` | `ready` rt / lockstep |
|---|---|---|---|---|
| LTO (`build-lto`) | **664** (6.03 s) | 197 (20.33 s) | 1326 (3.02 s) | 7.9 s / 15.4 s |
| LTO off (`build`) | 578–602 | 180–186 | 1072 | 9.9 s / 16.7 s |

Paced (`pace on 1`, the panel's shape): **rt median 0.647 (0.594–0.684)**
over 3 minutes of PLAY (port 8582, below), 0.999 (0.989–1.010) idle. The
target — ≥ 1000 flat out, 1.00 ± 0.02 paced — is not met.

**Where the wall goes** (LTO off, 4 s of PLAY = 6.9 s wall, `rtstatus`
and `cfstatus`): the ColdFire executes the same 85.0 M instructions per
emulated second in all three modes (`cfstatus`: no-DSP 340215712 in 4000
ms, lockstep 340211222, rt 342733498), so its own emulation is the no-DSP
3.7 s (LTO: ~2.8 s) plus ~1.1 s of posts (5.6 M, ~200 ns each: two
seq_cst atomics on a line both workers spin on); the waits are the idle
skips (1.3–1.6 s: the cores' work the ColdFire cannot overlap) and the
read-back pulls (0.7 s = 14 µs each: the command's handoff, the handler,
the 256-word FIFO burst at ~30 ns a word on the DSP side and the 256 pops
on the ColdFire's); explicit lag waits 0.01 s. Core 0 is busy 0.37 of the
wall at ~300 MIPS (115 executed MIPS: 190 M executed instructions per
emulated second, the DSP is loaded), core 1 0.35. Per frame (0.363 ms
real) that is ~0.34 ms of ColdFire (0.25 with LTO) plus ~0.24 ms of core
0, serialized except within the lead — which the protocol caps at ~4
samples (above) — so the practical ceiling of this design on this build
is ~0.65x, ~0.8x with LTO+PGO, and real time would need either the
cores a frame ahead of the ColdFire's clock (the protocol forbids it) or
a ColdFire that runs its 85 M instructions in well under 0.6 s.

**The rendezvous (the design's go/no-go).** A post is one store and one
generation bump (~200 ns with the workers spinning on the line, 1.4 M per
emulated second while playing); a wait that has to run a lagging core
the last sample costs 30–60 µs and happens 20–350 times in 4 s; the
per-tick guard costs and gains nothing measurable (above); the first
read-back word of a pull is there 14 µs after the kick on average.
Wakes: 100–300 per 4 s with the 200 µs spin (with no spin, 4.5 M wakes
and 213 emulated ms per wall s: every pull waited for a parked worker).

**The panel** (`panel_e2e_rt.py`, `panel_server.py --port 8582 --sound
on` on the OTLIVE fixture, the LTO binary as `--port-bin`): `ready` after
7.6 s, `sound_rt` true; **idle** 999.7 emulated ms per wall s, `/status rt`
median 0.999 (0.989–1.010), child 67 % of a core (`ps -M`: ColdFire 17 %,
the two workers 24 % each spinning idle); **PLAY 181 s**: 117110 emulated
ms in 181.18 s wall = 646 per wall s (per-second 577–692), `/status rt`
n=180 median 0.647 (0.594–0.684), child 283 % (93 / 95 / 95 %); frames
kept coming for the whole run (`audio` captured 6,615,700 frames, no
halt); `rtstatus` after PLAY: edges 347,574 applied 347,574,
**edges-in-pull 0, faults 00, dropped 0, pullshort 0**, waits 266, edge
lateness mean 160 DSP instructions (max 5180), skew timeouts 0; the take
`out/_panel_takes_8582/take-001.wav` 5,564,210 frames = 126.2 s, 22.3 MB,
decodes. LED chase by wall clock (LED-state changes on `/leds`, two per
16th): 122 ms mean in the first 12 s of PLAY (~1x: the first bar, before
the load builds) and 122 ms mean / 123 ms median again after 60 s of
steady PLAY on a second run (port 8583, rt 0.619) — at 0.62x a 16th takes
~200 ms of wall; the running light's cadence does not follow it, which is
a question for the panel's LED decoding, not this milestone.
**Fallback** (`panel_fallback_8584.py`: `panel_server.py --port 8584
--sound on` with `--port-bin out/emu/ot_emu.ref-73c2815`, a binary without
`--dsp-rt`): the rt child prints its usage and exits rc 2 on the unknown
flag before `ready`, the server respawns it with the lockstep `--dsp`,
`ready` after 18.8 s, `/status` `sound` true, `sound_rt` false,
`backend_note` "the --dsp-rt child did not boot (exited (rc 2) before
ready; last: usage: ...); respawned with the lockstep --dsp",
`sound_note` says the lockstep child plays at ~0.2x, `/rtstatus`
`{ok: false, result: "PortError: unknown command rtstatus"}`, `rt` 1.00
idle; SIGTERM on the server leaves no child. Until `out/emu/ot_emu` is
rebuilt from this tree (`pgo.sh`) that is what the owner's panel does.

**A/B** (`out/_agents/jit-build/ab/`: `lockstep-ref-73c2815.wav` = the
spike's `run-ref-4s` capture on the frozen reference, `rt-lto.wav` = the
same `rtdrive.py` script on this binary with `--dsp-rt`, 199,358 vs
199,373 frames, `cmpwav2.py` and `perwindow.py`): onset frame **78 vs 82
(4 samples)**; RMS **−5.13 vs −4.39 dBFS (0.74 dB)**, both peak at
32767 (the fixture clips); per 0.25 s window the best lag is −4 samples
in 13 of 17 windows with signal and +60 / −36 in the others (the
15/17-frame jitter O16c documented, a loop restart moved a frame), the
gain fit 0.55–0.98, the residual median −5.4 dB (−15.7 dB in the last
second, −10 dB in the first): the same loops at the same level and time,
not the same samples — the functional contract, not the Phase B bytes.
Both WAVs are in the log dir for listening.

**Shutdown** (`shutdown_rt.py`, during a paced PLAY with `audio start`):
`quit` 47 ms, EOF 37 ms, SIGTERM 18 ms; no process left.

**Thread sanitizer.** A Debug build with `-fsanitize=thread -O1 -DNDEBUG`
(`build-tsan`; NDEBUG because the vendored `Jitmem::writeDspMemory`
asserts on a static out-of-range address that the MMU scratch area
absorbs in Release): the JIT-emitted code is not instrumentable, only the
C++ handshakes are. With TSan's own symbolizer on, the child hung in it
after `ready` (the same macOS symbolication that hangs `sample` and
`lldb -p` on this process); with `symbolize=0` (`tsan-run/`, addresses
symbolized offline with `atos -l 0x100000000`) it booted to `ready` in
322 s and played, and every report -- 10 in the first 2.5 s of PLAY --
was one pattern: the eDMA mover's `peekWord` reads (DMA0's DDR through
`Peripherals56362::read`, the landed words through `Memory::get`) on the
ColdFire's thread against core 0's DMA writes (`DmaChannel::execTransfer`
from `HDI08::exec` in the worker's peripheral service) -- 13 reports by
the time that run was stopped, 7 at `Memory::get` and 6 at
`Peripherals56362::read`, no other pattern. Fixed as above; the rerun on
the fixed binary (`tsan-run2/`, same launch) booted to `ready` in 343 s
and played the whole 20 s of PLAY (1344 s wall, 930,822 frames captured,
the take decodes, rc 0) with **no report**: no `tsan.log.*` written and
no ThreadSanitizer line on the child's stderr.

### What it does not do

- **Real time.** 0.62–0.65x paced, 664 emulated ms per wall s flat out
  with LTO. The lockstep-schedule premise — the cores never past the
  ColdFire's count, at most a few samples ahead — serializes the cores'
  frame work with the ColdFire's own emulation; the counters above say
  which part is which. A larger lead breaks the frame protocol (8
  samples measured), so the next lever is the ColdFire itself (its 85 M
  instructions per emulated second at ~90–120 M per wall second) or a
  read-back path that does not cost 14 µs a block.
- The audio is functional, not byte-identical: the O16a Phase B contract
  cannot be met by any threaded design (O16c), and `phase_b.sh` is not
  run on `--dsp-rt` captures.
- `--dsp-rt` is `--interactive` only (the batch keeps the interpreter);
  cue and core 1 are still not captured; the DSP-side instruments do not
  see the workers.
- The workers spin 200 µs before parking: ~0.2 of a core each at 1.00x
  idle.
- `rtstatus` reads its counters without a rendezvous (racy by design,
  diagnostic); the report at exit and the probes rendezvous first.
- The macOS `sample`, `lldb -p` and TSan's symbolizer hang on this
  process (the MMU-backed DSP memory's six 64 MB views); `OT_SELFPROF` is
  what profiles it.

## Milestone O17b — the fenced deep lead: `--dsp-rt` plays faster than real time ✅ (13 Sep 2026, branch `panel-ui`)

O17 left the unit at 0.62–0.65x with sound, and named the cost: the cores
could run at most ~2 samples ahead of the ColdFire's clock (8 stalled the
frame protocol), so the ColdFire waited for them through every idle skip
(1.3–1.6 s per 4 s of play) and every read-back pull (0.7 s), and its own
emulation (~0.7 s per emulated second) barely overlapped the cores' frame
work. This milestone lets the workers run **a whole frame ahead** (16
samples = 66,560 DSP instructions, `OT_RT_LEAD=frame`, the numeric knob
kept) and puts **one fence** at the only timing-assumptive point of the
protocol instead of a fixed lead. With it, plus the host port's rings and
DMA made cheap, the same binary plays the OTLIVE fixture at **1015–1058
emulated ms per wall s flat out with LTO** (`bench.py --dsp --dsp-rt`,
four runs; O17: 664), and the panel's paced child holds `rt` 0.999 over a
10 s PLAY and a median 0.981 over a 3-minute one with the test harness
polling it. The strict oracle is 28/28 byte-identical on every old mode
(the lockstep modes got faster too), the C++ handshakes are TSan-clean
after one fix, shutdown is 22 ms. What is not met, both by a little: the
A/B against the lockstep take is 1.34 dB quieter in RMS (the contract
said 1 dB; O17's own take was 0.74 dB), and the paced 3-minute `rt` is
0.98, not 1.00 ± 0.02 — for reasons measured below. `dsp.cpp`, `dsp.h`, `machine.h`, `rtos.cpp/.h`,
`main.cpp`, `tools/patches/dsp56300.patch` (vendor/dsp56300 in place:
`hdi08.h/.cpp`, `dma.h/.cpp`, `dsp.h`, `jit.h`), `tools/panel/README.md`.

### The protocol, measured (`OT_FENCE_TRACE=1`, lockstep `--dsp`, the OTLIVE fixture playing)

Every host-port transaction, every INTC0 mask change of the frame source
and every INTC0 acknowledgement on stderr with its sample. One frame,
identical in all 110 traced:

| Δ samples from the bank word | who | what |
|---|---|---|
| 0.000 | core 0 | `P:0x73` writes the bank id into HOTX (the edge) |
| 0.004 | ColdFire | ack vector 0x41; the handler **masks INTC0 source 1** (`pc 4000aae0`) |
| 0.004 / 0.015 | ColdFire | `0x8c` (frame) and `0x89` (read-back, 512 words) to core 0 |
| 0.70 | ColdFire | pulls 256 + 128 + 128 words from core 0 (eDMA ch 1 → 6 → 7), ack 0x4f |
| 0.71 | ColdFire | `0x89` to core 1, pulls 256 words (ch 1), ack 0x49 |
| 0.72 … 1.30 | ColdFire | `0x88` + push 672 (core 0), 672 (core 1), 64 (core 0), 128 (core 1), 128 (core 0) halfwords; ch 0 completes and acks after each |
| 1.30 … 3.2 | ColdFire | eDMA channels 2–5 kicked and completed 30 times (the EMAC mixer's own moves) |
| 3.24 | ColdFire | `0x88` + push 512 halfwords (core 0, the forwarded read-backs), ack 0x48 |
| 3.37 | ColdFire | **unmasks source 1** (`pc 40004bc8`) — the exchange is over |
| 16.00 | core 0 | the next bank word |

So the ColdFire's whole exchange is **3.4 samples** of its clock, ISR-driven
(the eDMA completion chain), and the firmware's own end-of-frame mark is
the unmask of the source it masked at entry. That unmask is what the
fence opens on. The dispatcher's other side, read off the payloads: at
each ring boundary core 0 selects a bank (`P:0x54`/`0x64`) and **patches
the host handlers' masks** (`move x0,p:>$58c` / `p:>$59b` at `P:0x75`/
`0x77`, `x0 = $3fff` or `$5fff`), so a host word `0x6080` lands in the
bank the DSP is NOT processing — the frame it processes reads the bank the
previous exchange filled, and every command taken after the patch lands in
the other bank. Core 1 does the same from the mailbox word (`P:0x59`,
`p:>$371`/`p:>$380`). So "finished handling frame N" has to cover the
whole exchange, not only the pulls: a command taken after the patch would
be masked into the wrong bank.

### The design, as built (`dsp.cpp`, THE FENCE AND THE SERVICE; `Rtos::peripheralWrite`)

- **The fence.** `Rtos::peripheralWrite` watches INTC0 source 1's mask
  across every INTC0 write and tells the co-processor `frameHandling()`
  (masked: the handler entered) and `frameHandled()` (unmasked: the
  exchange is over; `Coprocessor`, `machine.h`). Core 0's bank-word write
  (`P:0x73`) **closes** the fence (the HDI08 write callback, as O17
  identifies the edge); `frameHandled()` opens it. A worker whose next
  block starts at `P:0x73` while the fence is closed stops there: its
  clock is frozen (its ESAI does not tick), it publishes its position, and
  the lag bound, the skew bound and the idle skip count it as idle — it
  cannot come until the ColdFire opens it, and the ColdFire must go on to
  do so. `P:0x73` is declared a **volatile P address** in core 0's JIT
  (`Jit::addVolatileP`, vendored): a volatile address is never linked as
  a child block and every block generated later stops before it, so the
  worker regains control exactly there (the `bra int_000073` at `P:0x63`
  used to jump straight into it).
- **The fence applies only while the ColdFire takes frames** — the frame
  clock on (`Coprocessor::setFrameClock`, from `Rtos::setFrame`) or an
  exchange in flight (`frameHandling` without its `frameHandled`; STOP
  lands inside an exchange one time in five). ❌ Measured before this
  rule: the firmware's `ICR = 0x81` (INIT, an interface reset) drains the
  port during the boot, core 0 looped through frames nobody took, froze at
  its second bank word for the whole load, and spent the first seconds of
  PLAY 1.1 G instructions behind the ColdFire's clock with the sequencer
  running 4.7x too fast (`exec0 52 M` against `due 1169 M` at `ready`).
- **The service.** A held or idle worker still serves the host. A host
  command (`kick`), pushed words (`svc`, new) or a pull (`pred`) makes it
  run its pending interrupts through their handlers to their `rti`
  (`DSP::execInterrupts` per vector, then `exec()` until the PC is back
  and the mode is not `LongInterrupt`, then `execDefaultPreventInterrupt`)
  and its peripheral service (the vendored `execPeripherals`: the host
  DMA's drain and FIFO fill, the ESAI and the timers as of the counter),
  with the main line held where it is (`rtService`). The chip's DMA and
  interrupts run beside the core; nothing a handler does depends on the
  main line. A worker in the skew wait (spinning for the other core)
  yields to the same three requests and serves them with one block. The
  bank take (`rxTake` outside a pull) sends a `svc` too: HTDE, the bit
  `P:0x97` polls, is set by the core's own service.
- **Gated edge delivery.** An edge is no longer applied when raised. The
  worker puts its executed count (the DSP's own clock, in due units) on a
  16-deep SPSC ring; `Coprocessor::edgePending()` — asked every 64
  instructions of a burst — is true only when the ColdFire's clock has
  reached the oldest edge, and `rtApplyEdges` applies exactly those. The
  idle skip (`rtTickSamples`) is bounded by a produced edge: at or behind
  it the edge is delivered first (no skip), ahead of it the skip ends on
  the sample boundary after it; with no edge yet the whole skip is posted
  with the workers armed to stop at the first bank word inside it, as O17
  had it. So the frame interrupt arrives at the sample the DSP's clock
  says — lockstep's timing — however far ahead the DSP produced it
  (`edgelate mean 25.7, max 67` DSP instructions in bursts: the burst's
  grain; `skipedgelate mean 2127`: the sample grain). ❌ Without the gate a
  frame-deep lead delivers frame N+1 as soon as the handler finishes frame
  N: the sequencer would run at the exchange's rate (3.4 samples a frame),
  not the DSP's.
- **The poll lead** (`OT_RT_POLLLEAD`, 2 samples). The lead is for
  executed work; the poll fast-forward (an idle step is ~40 ns for 520
  instructions, i.e. the DSP's clock races at ~13 G instructions per wall
  second inside a poll) is bounded differently. The one poll whose end is
  the ColdFire's act — the HTDE wait after the bank word, `P:0x97`, the
  take — never carries core 0's clock more than the poll lead past the
  ColdFire's clock, and at that bound the core waits idle for the ColdFire
  to move. ❌ Measured with the frame lead on it (three stalls in ~200 s
  of play, `stall_hunt.py`): in the microseconds the ColdFire took to get
  to the take, core 0 burned a whole frame of its own clock at `P:0x97`,
  missed its next ring boundary (the DSR2 equality window is one word),
  took a 32-sample frame, DMA2 ran dry (`dsr2=008070 dco2=0000ff`, the
  dispatcher's poll never saw the boundary again) and the frame protocol
  stopped for good. The DSP's own waits — the ring boundary at `P:0x4b`,
  the mailbox at `P:0xa3` / `0x57` / `0x8d` — keep the work lead (the
  skew bound already ties the mailbox waits to the other core). `P:0x4b`
  is a volatile address too: the fast-forward must fire at the poll's
  head, before the `movep DSR2` read, and the JIT linked the loop's `bra`
  into it — O17 had executed that poll, ~30k instructions a frame.
- **The host port's rings and DMA** (vendored, `tools/patches/dsp56300.patch`,
  22 files, 56 hunks, `git apply --check` on a scratch worktree of
  `3c01813f` clean and the applied diff byte-identical to the patch):
  - `HDI08`'s two rings are the lock-free `RingBuffer<TWord, 8192, false>`:
    both are single-producer / single-consumer and no caller pushes into
    a full ring or pops an empty one unasked, so the locking ring's
    semaphore pair (two contended atomic read-modify-writes per word, on
    both threads) was pure cost — the frame's 2,944 words paid ~70 ns a
    word each side. The lockstep modes use the same rings and got faster
    (below); their bytes did not move (the oracle).
  - **Burst transfers on the host port** (`DmaChannel::burstFromHost` /
    `burstToHost`, driven from `HDI08::exec` when a FIFO is configured):
    a request-triggered single-counter channel reading HORX or writing
    HOTX moves up to the FIFO's depth of words in one call with the
    per-word peripheral dispatch, callbacks and delay resets taken out
    (`popRXFast` / `pushTXFast`); the block-end bookkeeping is
    `execTransfer`'s. The per-word path is kept for any other shape.
  - The host side pushes a block's words into the ring in one call (the
    lane model resolved once: every halfword lands on TXM:TXL and sends)
    and pops a pull's words in one call (`readTXBulk`: one delay reset for
    the batch instead of one per word on a line the core's thread reads at
    every block). `OT_RT_BULK=0` is the per-word path.
- **Posts** every sample (`OT_RT_POSTQ`/`READQ` 4160, were 32): with a
  frame of lead the workers do not need the count more often, and O17's
  1.4 M posts per emulated second (~200 ns each on a line both workers
  spin on) become 28 k.
- **The DO-loop time slice** (`OT_RT_DOITER` 64, was 0): a DO loop yields
  its block every 64 iterations so a stopped-core service or a periph
  service is never further away than a few microseconds; measured 1033
  against 993–1016 unbounded, interleaved.
- **Workers spin while the host is active** (a command, push or pull
  within 2 ms) instead of parking after 200 µs: a parked core's wake cost
  core 1's read-back pull tens of microseconds once a frame.
- **Diagnostics.** `rtstatus` grew: the fence's state (`fence= inexch=
  frameon= edgesq= c0fenced=`), `fenceopens`/`fencewaits`, the services
  (`svc0/1`, `svcint0/1`), the ColdFire's waits by cause in wall seconds
  (`cfwait skip= lag= pull= pullword= kick= push= post= fenceopen=`), each
  worker's wall time split (`busy= idle= fence=`), core 0's HSR/HCR/HPCR,
  peripheral target, TCR, DCR2/DSR2/DCO2 and counter (as the worker last
  published them, with the MIPS and at every idle transition — never a
  look at the worker's data from the ColdFire's thread), the knobs, and
  the last 200 protocol events (`proto:` —
  commands, pushes, pulls, eDMA kicks and completions, the frame and eDMA
  acks, edges with their lateness, handled/masked, the frame clock; a
  1024-deep ring always on in the rt mode). `cfstatus` adds `pc=`.
  `edmastatus` (new) prints the eDMA's booked completions, IRQ lines,
  gated-wait count and INTC0's mask on the frame source. Env knobs:
  `OT_FENCE_TRACE=1` (the per-frame trace above, any mode),
  `OT_RT_WAITLOG=1` (every ColdFire wait over 5 ms and every skew timeout
  with both cores' state), `OT_RT_POLLHIST=1` (where the fast-forward
  fired, per core, at exit), `OT_RT_FENCE=0`, `OT_RT_DSR2FF=0`.

### Measured (13 Sep 2026, the M5, macOS 26.5; LTO builds unless said; logs under `out/_agents/rt-fence-build/`)

**The O17 baseline with the new instrumentation** (this binary with O17's
knobs: lead 2 samples, no fence, posts every 32, per-word host paths,
`P:0x4b` executed, DO loops unbounded — `fin-o17knobs-4s`), 4 s of PLAY in
16 × `run 250`, the deltas over the play: **771 emulated ms per wall s**;
the ColdFire's waits **1.71 s of the 5.19 s wall**: idle-skip catch-up
1.133 s (22,926 skips), pulls 0.214 s (49,812; 0.004 s of it waiting for a
word), pushes 0.271 s, posts 0.079 s (1.44 M), kicks 0.014 s, lag 0.001 s;
core 0 busy 2.78 s, core 1 2.07 s. (The frozen O17 binary itself: 664;
its pulls 1.370 s per 49,828 = 27.5 µs each, its posts 5.65 M.)

**The steps**, `--dsp-rt` flat out, each on the LTO-off build unless said
(`rtdrive.py`, 4 s of PLAY):

| step | emulated ms per wall s | what moved |
|---|---|---|
| O17 knobs, LTO off | 597 | — |
| the fence + the frame lead (and the frame-clock gating) | 663 | skip waits 1.24 → 0.25 s; pushes 1.60 s (!) |
| + lock-free rings | 832 | pushes 1.60 → 0.30 s, pulls 0.56 → 0.44 s, core 0 busy 3.44 → 2.80 s |
| + burst DMA | 929 | pulls 0.44 → 0.08 s, waiting for a word 0.110 → 0.006 s |
| the same, LTO | 976 → **1086 / 1109** | (`bench.py`) |
| + the poll lead on every poll (the stall fix, first cut) | 970–995 | the DSR2 poll idled at the bound |
| + the poll lead on the host wait only, `P:0x4b` fast-forwarded | 889–990 | core 1 sat in the skew wait through the pulls |
| + the skew wait yields to the host, DO loops sliced | **1018–1044** (hunts), **1042 / 1058** (`bench.py`) | — |

**Final, flat out** (`bench.py`, four runs on the last two builds of the
tree — the second differs only in `rtstatus`'s published fields and the
`edmastatus` reply): **1058, 1042, 1030 and 1015 emulated ms per wall s**
(0.118–0.123 s wall per 16th at 120 BPM); the lockstep `--dsp` 208 (O17:
197), no `--dsp` 1357. Boot to `ready`: **6.8–7.1 s** with
`--dsp-rt` (14.8 s lockstep, 5.8 s without the cores). Eight-second PLAY
cycles with STOP/PLAY in between (`stall_hunt.py`, 3 × 8 cycles): 981–1044
per cycle.

**The wait breakdown after** (`fin-rt-4s`, the deltas over 4 s of PLAY,
3.82 s wall = 1048): the ColdFire's waits **0.263 s**: pulls 0.089 s
(49,836; 0.006 s waiting for a word, 12,260 waits), pushes 0.152 s
(74,754), kicks 0.014 s (112,131), posts 0.007 s (111,615), idle-skip
catch-up **0.000 s** (1 skip waited), lag 0.001 s. The other 3.55 s is the
ColdFire's own emulation of 388.8 M instructions (110 M per wall s). Core 0
busy 1.26 s, fenced 0.77 s (12,451 of 12,459 frames reached the fence
before the exchange ended), idle 2.46 s; core 1 busy 1.31 s. Per frame:
0.285 ms of ColdFire emulation + 0.021 ms of waits = 0.306 ms against the
0.363 ms the unit has. **The pull wait before/after**: 27.5 µs per pull on
the O17 binary → 1.8 µs (0.12 µs of it waiting for the DSP).

**The fence is what keeps the protocol in order.** The same binary with
`OT_RT_FENCE=0` and the frame lead: 1059 ms per wall s, and **454 bank
words inside read-back pulls in 4 s** (`edgesinpull`; 0 with the fence).

**The stall that the poll lead removed** (three captures, `hunt-2/4/13`):
the bank words 32 samples apart, the exchange with two ~30-sample gaps at
pushes (drain gates held), the next edge 48 samples late, then core 0 at
`P:0x4b` or `P:0x97` for good — DMA2 finished, the ESAI dead. **The stall
that the yielding skew wait removed** (`hunt-bg-3`, the 1024-event ring):
core 0 spinning in `rtSkewWait` for core 1 did not drain the 64-word push,
its gated completion came 8.4 samples late (after the EMAC phase that
normally follows it within 0.04 samples), and the handler's ISR chain
never issued the last push: `outstanding=0`, source 1 still masked, the
ColdFire idle at main's spin (one capture in 8 hunts, 336 s of PLAY, with
the poll lead alone). After both fixes: **0 stalls in 3 hunts of 8 cycles
(192 s of PLAY with STOP/PLAY cycles)**, the 3-minute panel PLAY below,
its three 10-second probes, and the 20 s under TSan.

**The gate: 28 PASS, 0 FAIL, twice** (`tools/emu/ot_emu/oracle/oracle.sh
out/emu/ot_emu.ref-73c2815 <cand> --build-dir <lto>`, strict, no
tolerance; reports `out/_oracle/reports/20260913-030632-rt-fence-final.txt`
and `…-033122-rt-fence-final2.txt` for the final source (sha
`32b162a40ade…`), 37–38 s wall, ctest 7/7): boot logs, serial, goldens, `run3_core0.wav`, the
UART stream, peeks, run stamps and `interdsp.pcm` byte-identical. The
lockstep jobs got faster from the rings alone: `interdsp` boot 23.8 →
15.8 s and its 4.49 s of `run` 28.2 → 20.7 s, `render` 30.8 → 21.3 s.
⚠️ The x86 Homebrew `bash` (`/usr/local/bin/bash`, under Rosetta) ran the
script for 13 minutes without printing a line; `/bin/bash` ran it in 37 s
— the O15d Rosetta accident in another costume.

**A/B against the lockstep take** (`rtdrive.py` with and without `--rt` on
this binary, `abcorr.py`; `fin-ab-lockstep`, `fin-ab-rt`): 199,358 frames
both; onset 82 vs 80 (**−2 samples**); the music itself sits 126 samples
later in the rt take (the best lag); RMS over the overlap **−4.39 vs
−5.73 dBFS (1.34 dB)**, both peaking at full scale; envelope correlation
0.939 over the whole overlap in 50 ms windows, 0.31 in 10 ms windows over
the first 2 s. Where the level goes: the lockstep take has 20 % of its
samples at full scale in every 0.5 s window (the fixture clips, the
sample at level 64), the rt take 3–17 % — the same loops with lower peaks,
not gaps: no 10 ms window with signal is under half the lockstep level, and
0.45 % of the 16-sample frames are under 0.35x (O17's own take: 0.54 %,
−5.12 dBFS, a 0.74 dB difference). 🟡 Not located: the DSP's output
limiter runs on its own history and the cores' interleave differs, and the
A/B cannot tell that from a frame of the mix landing a frame off. It is a
miss on the contract's 1 dB; the audio is the same music at the same
time.

**Thread sanitizer** (`-fsanitize=thread -O1 -DNDEBUG`, Debug, LTO off;
`TSAN_OPTIONS=symbolize=0 log_path=…`, addresses symbolized with `atos -l
0x100000000`; `tsan_drive.py`: boot, `frame on`, `audio start`, PLAY, 80 ×
`run 250` with `audio read` and three `rtstatus`, STOP, `quit`): the first
run (`tsan-run/`) booted to `ready` in 257 s, played its 20 s (55,295 edges,
none inside a pull) and reported **two races, both in the new `rtstatus`
peeks** — the ColdFire's thread reading DSR2 through
`Peripherals56362::read` against `DmaChannel::dualModeIncrement` on core
0's thread, and reading the instruction counter against `DSP::idleStep` —
then aborted at exit as macOS TSan does with reports pending (rc −6).
Fixed: the worker publishes those registers with its MIPS
(`RtCore::hsr/hpcr/tgt/tcr/dcr2/dsr2/dco2/ctr`) and `rtstatus` reads the
atomics. The second run on the fixed binary (`tsan-run2/`): `ready` in
258 s, 20 s of PLAY in 740 s of wall (55,292 edges, `edgesinpull` 0,
`faulted` 00, `dropped` 0, 884,732 audio frames captured), `quit`, **rc 0,
no `tsan.log` written and no ThreadSanitizer line on stderr**.

**Shutdown** (`shutdown_verify.py`, during a paced PLAY with audio on):
`quit` 22 ms, EOF 22 ms, SIGTERM 23 ms; no process left with the card in
its arguments.

**The panel, paced, 3 minutes** (`panel_verify.py 8582`, `panel_server.py
--port 8582 --sound on`, this binary as `--port-bin`, the OTLIVE fixture;
`panel-rt-8582.log`): `ready` after 7.1 s with `sound_rt` true; idle
`/status rt` median 1.000 (0.996–1.008); **PLAY 180 s**: 178,860 emulated
ms in 181.36 s wall = **986 per wall s** (per-second 913–1104, median
984), `/status rt` n=180 **median 0.981 (0.905–1.080)** — under the
target's 1.00 ± 0.02, with the test's own load on the same machine
(`/leds` polled 29,638 times, the headphones-monitor mirror fetching
`/audio/pcm`, a status poll per second; the server's own pump on top);
the pacer re-anchored 10 times and ended with `lag` 0. The same child
over a 10 s PLAY after the MIXER / double-tap / encoder items: **1000
emulated ms per wall s, `rt` median 0.999 (0.995–1.003)**. Audio: end
613,750 → 8,501,474 frames = **44,100 per emulated second** (target
44,100), captured 8,501,474, **dropped 0**; the take
`out/_panel_takes_8582/take-002.wav`, 7,896,152 frames = 179.05 s,
decodes. `rtstatus` after the PLAY: edges 418,514 applied 418,514,
**edgesinpull 0, faulted 00, dropped 0, pullshort 0**, `waitto` 0.
**The LED chase in wall time** (`/leds` at ~165 polls a second, every
LED-state change stamped; `panel-rt-8582.leds.tsv`): 1,633 changes in
181 s, the trig-row running light's 16-step sweep recurring every
**1,941 / 1,944 / 2,027 ms (medians of the three sweep patterns, 69–75
sweeps each, 1,852–2,125 ms)** = 121–127 ms of wall per 16th against the
fixture's 125 ms (120 BPM; the sequencer's own step period in emulated
time is unchanged at 125.1 ms). MIXER opens and closes the mixer page,
the T1 double tap opens the slot list, the LEVEL encoder redraws, the
card re-insert reboots to `ready` in 7.6 s with `--dsp-rt` and
`sound_rt` true.

### What it does not do

- **The A/B's level.** 1.34 dB quieter than the lockstep take in RMS on
  the clipping fixture (the contract asked for 1 dB; O17's take was 0.74
  dB). Same onset (−2 samples), same loops at the same time, no gaps; the
  DSP's output limiter and the cores' interleave are the suspects, not
  located. A non-clipping fixture would separate "quieter" from "less
  clipped".
- **Paced under the panel with a test harness polling it**, `rt` sits at
  0.98 (median over 3 minutes), not 1.00 ± 0.02; the same child without
  the harness holds 0.999. Flat out the margin over real time is 4–6 %
  with LTO (a PGO build, `pgo.sh`, was not measured here), so any other
  load on the machine shows up in `rt`.
- **The stall proofs are statistical**: 0 in 576 s of hunting plus the
  3-minute panel PLAY after the two fixes, against three and one
  captures before them. The captures are in `hunt-2/4/13` and
  `hunt-bg-3`; `stall_hunt.py` (8-second PLAY/STOP cycles, stopping at
  the first frozen frame count with `rtstatus`, `cfstatus` and
  `edmastatus`) is the instrument to run again.
- The fence, the poll lead and the volatile blocks know payload A
  (`P:0x73`, `P:0x97`, `P:0x4b`); a payload that moves them gets O17's
  behaviour (the lead then only as safe as the fixed lead was).
- `rtstatus`'s counters are the workers' published atomics, read without a
  rendezvous: a consistent-enough snapshot for a diagnostic, not a
  rendezvous (as O17's were).
- The idle-skip catch-up, the lag bound and the fence cost the ColdFire
  nothing measurable while playing; what remains is its own emulation
  (0.285 ms of the 0.306 ms a frame takes). The next lever is the
  ColdFire (PGO: O15d measured +13–17 % on the `--dsp` rate), not the
  cores.
- The batch keeps the lockstep interpreter; `--dsp-rt` is `--interactive`
  only, cue and core 1 are not captured, the DSP-side instruments do not
  observe the workers — all as O17.

## Milestone O17c — `--dsp-rt` renders the lockstep bytes: the DSP's clock at every wait, the exact boot, the ISR's drain times ✅ (13 Sep 2026, branch `panel-ui`)

O17b left the real-time mode "the same music, not the same samples": on
the clean fixture (`out/_agents/audio/otlive2.img`, the O14k reference
project, T1 playing `third-0.wav` at 0.7032) the driver
`out/_agents/jit-spike/rtdrive.py` + `out/_agents/audioq/fit.py` fit the
lockstep `--dsp` capture to the sample at **gain 0.7032, residual −33.0 dB**
and the `--dsp-rt` capture at **gain 0.577, −10.7 dB** (a second run −11.8;
`OT_RT_LEAD=2` −7.4, `OT_RT_POLLLEAD=0` −15.1, `OT_RT_DSR2FF=0` −20.5,
`OT_RT_BULK=0` −8.1, `OT_RT_DOITER=0` −9.5; every run different) — the
owner hears crackling on clean kicks and dropouts. This milestone finds
six mechanisms with instruments, fixes them in `tools/emu/ot_emu` alone
(the vendored patch is untouched), and ends with the rt capture
**bit-identical to the lockstep capture: 0 mismatches of 199,358 samples,
L and R, in three consecutive runs (−33.0 dB, gain 0.7032 each)**. Logs,
captures and the scripts under `out/_agents/audiofix/`.

### The instruments (all kept; the scripts under `out/_agents/audiofix/`)

- **The per-block diff** (`perframe.py`, `hits.py`, `slips.py`, `corr.py`):
  the rt capture aligned to the lockstep capture, the error per 16-sample
  block in dB, the wrong samples by position, the stale test (a wrong
  sample equal to the lockstep sample 32 earlier = the ring word from the
  previous revolution), a per-block shift search. It said: one stale
  sample at the block's first position in a third of the blocks, whole
  stale blocks now and then, and long stretches in each hit's tail where
  every sample is wrong by −10 dB with a per-block gain 0.5–1.0.
- **The frame trace** (`OT_DSP_FRAMETRACE=1`, any mode; `ftr.py`): one
  stderr line at core 0's marker PCs — the bank word `P:0x73`, the take seen
  `P:0x99`, core 1's reply `P:0xa5`, the output stage `P:0x1cb`/`0x205`
  (made volatile P addresses under the trace), the return to the DSR2 poll
  `P:0x4b` — and core 1's `0x57/0x59/0x8d/0x8f`, with the DSP's own clock,
  the ColdFire's due count and DSR2. Lockstep: the take at +0.79 samples
  after the bank word (the lazy chunk's grain), the output stage at +1.26,
  DSR2 = 0x79 there — **the ring write has ~0.5 samples of slack before
  DMA2 enters the half it writes**. rt: the take at +0.37 median, p99 1.8,
  max 3.0; the output stage past +2.0 in 255 of 11,400 frames.
- **The exchange timeline** (`OT_FENCE_TRACE=1`; `exch.py`, `pushlat.py`):
  per frame, the ColdFire's ack, commands, pulls, pushes and acks in its
  own clock. Lockstep: the whole exchange 3.36 samples, every push's
  completion a constant of its shape (`pushlat.py` over 12,360 frames:
  core 0's 336-word push 0.340, core 1's 0.165, the 256-word forward 0.126,
  the 64-word ones 0.032, the 32-word one 0.017; ≤ 10 distinct values each,
  within 0.005). rt: 2.85 samples, every push done 0.015 after its kick,
  with a wall-dependent tail up to 2 samples.
- **The block dump under rt** (`--block-dump`, `blockdiff*.py`): every
  host-port block of every frame against the lockstep dump. It separated
  "the DSP was given different data" from "the DSP computed differently",
  and found the ColdFire's own blocks differing (below). The dump had
  stalled the rt protocol (the mover's `peekWord`/`blockNote` peeks into
  the core's registers from the ColdFire's thread); they are skipped under
  `--dsp-rt` now, the dump's words are the ColdFire's own RAM.
- **The ESAI-vs-ring check** (`rtstatus esaimism=`): in the sink, the eight
  words the ESAI put out against the ring words the −9 rule says DMA2 took
  them from. 0 mismatches until a stall — the DMA2/ESAI/sink path is
  faithful; what differed was the ring's content.
- **Determinism**: two rt runs were not bit-identical to each other (66k
  mismatches), so a JIT arithmetic divergence (the spike's CCR U bit) was
  ruled out; every mechanism below is timing.

### What was wrong, in the order found (`dsp.cpp` THE WAITS / THE RENDEZVOUS AT THE TAKE / THE COMMAND RENDEZVOUS / THE EXACT BOOT / THE GATE WAITS FOR THE DRAIN; `rtos.cpp` THE DRAIN TIMES; `periph.h` `setHostDrainTime`)

1. **The DSP's clock at the take.** Under lockstep core 0 leaves its HTDE
   wait (`P:0x97`) at the ColdFire's clock of the take. Under rt the poll
   lead (2 samples) let the worker's clock run to E+2 while the ColdFire
   caught up its 13-sample lead, so the take was seen at E+0…2.2 of the
   DSP's own clock, and the ring write that follows (`P:0x25b`, r1 = the
   other half) landed after DMA2 had entered that half: the block's first
   ring words came out stale (the previous revolution's samples: the
   crackle), whole blocks when the wait was longer (the dropouts). Fix:
   `OT_RT_POLLLEAD` defaults to 0 (the wait's fast-forward never passes the
   ColdFire's clock); the bank take and the end of a read-back pull post
   the exact count (`m_hostEventAt`); at the wait's exit (`P:0x97 → 0x99`,
   a volatile address) the worker idle-steps its clock up to that count
   through its peripheral events.
2. **The read-back refill race.** After the take the ColdFire's `0x89`
   refills HOTX with the read-back within 45 of its instructions (~0.5 µs
   of wall); the worker's service and poll take microseconds, so HTDE was
   clear again before the poll ran and core 0 stayed at `P:0x97` through
   the whole pull (0.7 samples: `waitcu0` ≈ one per frame of ~1,500
   instructions) — on the chip and under lockstep it leaves at the take.
   Fix: THE RENDEZVOUS AT THE TAKE — `rxTake` holds (its clock frozen,
   ~1 µs, `take=` in `rtstatus`) until the worker has left the wait
   (`m_takeSeen`).
3. **The mailbox waits.** Core 0's wait for core 1 to take its word
   (`P:0xa3`) and core 1's for core 0's two words (`P:0x57`, `0x8d`) were
   fast-forwarded to the work lead; with the other core idle at its own
   bound the wait left 3–24 samples late (the frame's work, ring write and
   all: a whole stale block, a skipped frame, DMA2 dry — the O17b stall,
   reproduced once in 4 s). Fix: a mailbox wait's fast-forward is bounded
   by the other core's published clock plus the skew, the mailbox hooks
   record the sending/taking core's clock (`Mailbox::sentAt/takenAt`) and
   the wait's exit catches up to it; a wait whose condition already holds
   runs its poll instead of idling (❌ the first cut deadlocked both cores
   idle at each other's bound with the word already there: a 2 s wait
   timeout and a faulted core).
4. **The boot's phase.** The DSP's due count is booked per ColdFire
   instruction from the first one, the RTOS's sample clock starts at the
   handoff, so the DSP's frame clock sits `boot instructions / 3990`
   samples ahead of the RTOS clock — 2877.39 under lockstep, **3213–3329
   per rt run**, because the loader's 53,627 host-port polls (TXDE/RXDF for
   every uploaded word) spun a wall-dependent number of times. The
   sequencer's bookkeeping against the frame grid moved with it: the
   fixture's second voice (core 1's track, a 64-sample position that walks
   16 a frame) started 7–8 samples off the reference, and the two voices
   comb-filtered to −10 dB from ~30 ms into each hit. Fix: THE EXACT BOOT
   (`OT_RT_BOOTEXACT=1`, default) — until the frame clock is on the
   workers run with no lead and every host-port read is a rendezvous at
   the exact count (`bootreads=53627`, the offset **2877.418, identical
   run to run**; boot to `ready` 8.0–8.9 s LTO-off, was 7).
5. **The ISR's drain times.** The frame handler's pushes are kicked
   through SSRT (unpaced) and complete at the drain gate: the lockstep
   interpreter's DMA0 drains a word per service, a constant per block
   shape (above); the JIT worker drains a block in one service, so the
   completion came 0.015 samples after the kick — the ISR chain 0.5
   samples shorter, and its length following the wall (350–430 of 74,000
   completions per run later than that, up to a sample; every divergence
   from the reference began at one, `blockdiff*.py`: the first differing
   block was the ColdFire's own record for core 1, rendered inside that
   chain, its 8-sample position bucket flipped). Fix: THE DRAIN TIMES —
   under rt `Edma::start` books an SSRT-kicked host-port burst at the kick
   plus the lockstep drain time of its shape (`installHostPortMover`: the
   six measured shapes, two instructions a word for any other,
   `OT_RT_DRAINPACE=0` to switch off); THE GATE WAITS FOR THE DRAIN —
   `hostRingEmpty` holds (clock frozen, `gatedrain=` in `rtstatus`, ~400
   holds of ~10 µs per 4 s) instead of letting the ColdFire step past the
   booked time; and the rt gate-stepping rule (32 instructions) applies only
   to a completion already past due, so a booked one lands on its
   instruction. Measured after: 0.340 / 0.166 / 0.126 / 0.032 / 0.032 /
   0.017 with max = median.
6. **The command poll.** The handler polls CVR's HC until the DSP takes
   the command — at its next instruction under lockstep, at its next block
   (wall) under rt, a wall-dependent number of iterations. Fix: THE COMMAND
   RENDEZVOUS — a CVR read with a command pending holds until the worker
   has taken it (`hcwait=` in `rtstatus`, ~0.3 µs each).

### Measured (13 Sep 2026, the M5, macOS 26.5; `out/_agents/audiofix/`)

| build / run | gain | residual (fit.py) | vs the lockstep capture |
|---|---|---|---|
| lockstep `--dsp` (`ls1`, `ls2`) | 0.7032 | −33.0 dB | — (byte-identical to the O14k reference and to each other) |
| `--dsp-rt` HEAD (`rt2`…`rt7`) | 0.54–0.65 | −7.2 … −14.0 dB | 17–64 % of the samples wrong |
| + the take's clock (fix1) | 0.59 / 0.70 | −11.5 / −32.8 dB | one run stalled at 3.3 s |
| + the mailbox waits, the take rendezvous (fix3) | 0.42–0.60 | −5.2 … −13.3 dB | the DSP's timeline lockstep's; the ColdFire's blocks differ from frame 3 |
| + the exact boot (fix4) | 0.54–0.70 | −10.1 … −32.8 dB | one run in three right; the rest a coin flip per trig |
| + the drain times, the command rendezvous (fix6) | 0.60–0.70 | −11.7 / −20.6 / −32.7 dB | the held gates at the divergences |
| + the gate waits for the drain (fix7, three consecutive runs) | **0.7032** | **−33.0 / −33.0 / −33.0 dB** | **0 mismatches of 199,358 samples, L and R** |

Flat out (`bench.py --dsp --dsp-rt`, the LTO build): **1042 and 1020 emulated ms per wall s** (two runs, 4 s of PLAY in 16 × `run 250`; O17b measured 1015–1058 on its LTO build).
The same on the LTO binary with no instrument on (`final-lto-1..3`): **−33.0 dB,
gain 0.7032, three of three; inside the 4 s of PLAY 0 mismatching frames
against the lockstep capture in all three** (`rtdrive.py` itself timed the
play at 1164–1218 emulated ms per wall s); after the driver's STOP, two of
the three differ from lockstep in 1,447 frames (4.06–4.24 s, max |diff|
1292, the two identical to each other) — a binary post-STOP outcome, not
measured further. Boot to `ready`: 6.8–7.4 s LTO, 8.0–8.9 s LTO off (was
7 / 7.9). Three PLAY/STOP cycles of 6 s (`stall_hunt.py`, `hunt-lto/`):
1016–1027 per cycle, no stall. The strict oracle on the old modes: **28 PASS, 0 FAIL** (ctest 7/7, 37 s; `out/_oracle/reports/20260913-071250.txt`). The vendored tree and
`tools/patches/dsp56300.patch` are unchanged (`git -C vendor/dsp56300
diff` equals the patch byte for byte).

### What it does not do

- The drain-time table knows payload A's six block shapes (and the
  interpreter's base rate for any other); a payload with other shapes gets
  the two-instructions-a-word rate — deterministic, not lockstep's.
- The sink's de-rotation (`rot = (DSR2 − 9) & 7`) still takes ring words
  0..rot−1 of a de-rotated frame from the next ring sample; with the exact
  boot the rt phase is lockstep's (rot ≤ 2, main L/R inside one sample)
  and the captures agree, but a boot with rot ≥ 3 would put main R one
  sample behind L in the capture (seen on the random-phase runs before
  the exact boot: R = lockstep's R delayed by one sample, L exact). The
  batch capture has the same rule and the oracle pins its bytes, so it is
  left as it is.
- The waits are bounded (2 ms) for a dead core and counted (`take=`,
  `hcwait=`, `gatedrain=`, `waitto`); the diagnostics (`OT_DSP_FRAMETRACE`,
  `esaimism=`, `waitcu0/1=`) stay in.

## Milestone O18 — the panel child's memory: the peripheral-write record ended at the seed, the dispatch record bounded ✅ (13 Sep 2026, branch `panel-ui`)

The port's process grew while the sequencer played and never gave the
memory back: **11-13 MB per emulated second of PLAY**, in bursts, with the
DSP cores off as well as under `--dsp-rt` (CONTEXT.md's "+34 MB per 2 s
slice"; the O15a verifier's +48 MB for 6 s, +175 MB for 13 s). The panel
runs one child for hours, so an hour of play was 40 GB of address space.
The growth was measured, not guessed: `MallocStackLogging=1` on a 20 s
play and `malloc_history -allBySize` while the child was still playing,
then a memory instrument inside the emulator (`OT_MEMSTAT=1`, below) for
the rates of every record; the driver and every log are under
`out/_agents/memfix/` (`memdrive.py`: boot on `otlive.img`, `frame on`,
PLAY, `run 250` + `tx` per slice the way the server pumps, `ps -o rss`
every few seconds, `vmmap`/`malloc_history` before STOP).

### What grew, measured

| record | rate during play | before | after |
|---|---|---|---|
| `Machine::m_periphWrites` — one 12-byte record per peripheral write the models take (the boot's seed for `Rtos::install`) | 0.4-0.8 M writes per emulated s (the vector's 2^24-entry block was live 20.5 s into play: 201,342,976 bytes, plus its 206 MB predecessor freed but still resident — the doubling that made the growth bursty); **the whole leak in both modes** | unbounded, never read after the seed | ended at the seed: `Machine::endPeripheralWriteLog()` after `Rtos::install` has replayed it (and captured `m_seeded`); the vector is freed |
| `Rtos::m_dispatches` — one 24-byte record per scheduler `rte` | ~720 per emulated s (24,643 after the load, 164,531 at 200 s of play: 17 KB/s, 62 MB/h) | unbounded | `Rtos::DispatchLog`: the true count, the first 65,536 whole, a ring of the last 4,096, the TCB set for `ran()` (1.7 MB at most) |
| `AtaCard::m_log` — one entry per ATA command | 10,339 after the load, +12 over 200 s of play (the fixture's flex slots live in RAM) | unbounded | capped at 262,144 entries, the rest counted (`logDropped()`) |
| `Rtos::m_acks` | at its existing 100,000 cap 19 s into play (3.2 MB) | capped (O7) | unchanged |
| the panel UART's `tx` (`Uart::m_tx`) | 187 B/s during play (45,707 bytes at 200 s; 0.7 MB/h) | unbounded | unchanged — `main.cpp`'s `tx` cursor indexes it absolutely and the batch's `serial_a` golden is the whole stream (see below) |
| everything else (`m_periphLog` 4,096, `m_periphTrace` / `m_hostPortLog` / `m_memWrites` / the DSP's `m_log`, `m_trace`, maps, watch hits — all opt-in and capped; the audio ring 60 s fixed; the eDMA due list; `m_created` 10) | flat | bounded or opt-in | unchanged |

The `--dsp-rt` mode adds nothing of its own: the JIT's block chains were
21 MB in four allocations at 20 s and did not grow; the HDI08 rings held
one word; the ESAI capture is off outside the render.

### What changed (`machine.h/.cpp`, `rtos.h/.cpp`, `card.h/.cpp`, `dsp.h/.cpp`; no CLI change, `main.cpp` untouched)

- `Machine::peripheralWrite` records into `m_periphWrites` only while
  `m_periphWriteLogOn`; `Rtos::install` calls `endPeripheralWriteLog()`
  right after `m_seeded = peripheralWrites().size()` — the record's one
  reader is the seed replay above that line (`grep` finds no other), so
  every printed count is taken before it is dropped.
- `Rtos::DispatchLog` replaces `std::vector<Dispatch>`: `size()` is the
  true count (the batch's "N dispatches", the load's delta), `operator[]`
  answers the first 65,536 and the last 4,096 (`writeGoldenJson` reads the
  first 200, the batch's "dispatch tail" the last 14; a batch run never
  passes 65,536 — boot 51, load ~24,400, a 3,000-frame render ~10,000
  more), `ran()` is kept incrementally. An index that fell out answers a
  zero record; no reader asks for one. `kept()` says how many are held.
- `AtaCard::note()` keeps the first `g_logCap` = 262,144 entries and
  counts the rest (`logDropped()`); `stampLastCommand` stamps only an
  unstamped last entry, so a dropped command never re-stamps a kept one.
- **The instrument**: `OT_MEMSTAT=1` prints one `memstat <ms>: ...` line on
  stderr at the end of every `Rtos::run()` and at destruction with the
  size of every record above — `Rtos::memStat()`, `Machine::memStat()`,
  `Coprocessor::memStat()` (a default, `DspPair` overrides it with its log,
  trace, maps, stream, watch hits, capture and HDI08 rings). stderr only,
  opt-in: stdout is diffed byte for byte by the oracles.

### Measured (13 Sep 2026, the M5, macOS 26.5; Release, `-DOT_LTO=OFF`, `out/_agents/memfix/build` = before, `build-fix` = after; logs under `out/_agents/memfix/`)

300 emulated seconds of PLAY on the OTLIVE fixture, `run 250` + `tx` per
slice, RSS at PLAY and at its end (the peak is the last play sample; the
drop at STOP in the "before" rows is the compressor, not a release):

| mode | before: PLAY → 300 s | after: PLAY → 300 s | speed (emulated ms per wall s) |
|---|---|---|---|
| no `--dsp` | 422 → **3,856 MB** (+3,434 MB, 11.4 MB per emulated s; 2,323 MB after STOP) | 418.4 → **418.7 MB** (+0.3 MB; 362.7 after STOP) | 1,009 before, 1,008 after |
| `--dsp-rt` | 1,056 → **4,890 MB** (+3,834 MB, 12.8 MB per emulated s; 2,589 after STOP) | 1,052.0 → **1,052.8 MB** (+0.8 MB, 0.6 of it in the first slice; flat after STOP) | 839 before, 792 after (the after run shared the machine with the oracle's ten jobs for its first minute — not a speed measurement) |

The remaining growth is the UART stream (187 B/s), the dispatch ring's
one-off 1.7 MB and the acks' 3.2 MB cap — a 5-minute play adds under 1 MB,
an hour under 4 MB. `malloc_history` before the fix (20 s of play,
`base-mh-nodsp/malloc_history.txt`): the one live block from
`Machine::peripheralWrite` at 201,342,976 bytes, then only construction
(the card region 133 MB + 67 MB, the image 67 MB, the SDRAM 33 MB × 2,
the acks 3,162,112, the dispatches 1,064,960); the same picture under
`--dsp-rt` with the DSP's memory and audio buffers on top
(`base-mh-rt/malloc_history.txt`).

**The gate: 28 PASS, 0 FAIL** (`out/_oracle/reports/20260913-080435-memfix2.txt`
against `out/emu/ot_emu.ref-73c2815`: boot logs, `serial_a` 5,731 / 9,257
bytes, goldens 12,757 / 26,367 bytes, `run3_core0.wav` identical, the
`--interactive` UART 18,297 / 18,309 bytes step by step, 109 peeks, 47 run
stamps at |dsample| = 0, `interdsp.pcm` 497,788 bytes identical, ctest
7/7; the first pass, `20260913-080310-memfix.txt`, was 27/1 only because
the candidate tree had not built the test executables yet). **The rt
audio is unchanged**: `rtdrive.py --rt --seconds 4` on `otlive2.img` →
`fit.py`: onset 82, lag 81, gain 0.7032, **residual −33.0 dB**
(`out/_agents/memfix/fit-rt/`).

### What it does not do

- The panel UART's transmit record still keeps every byte the firmware
  ever sent (187 B/s during play; 0.7 MB an hour). Trimming it needs
  `main.cpp`'s `tx` cursor to become an offset into a ring (three lines
  there, plus a `txBase()` on the `Uart`); the batch's `--serial-out` and
  the goldens want the whole stream, so the trim would be the interactive
  loop's alone. Left for the `main.cpp` owner.
- The acks stop at 100,000 as before (O7); the "ack tail" of an
  interactive session's end report is the tail of the first 100,000, as
  it was.
- Past 65,536 dispatches or 262,144 ATA commands the interactive
  session's end report still prints the true dispatch count, but its ATA
  command count and per-type histogram are the kept entries' (the batch
  runs never get there; nothing compares an interactive end report).
- The `--dsp` lockstep mode shares every record here; it was checked for
  60 s of play (0.1x real time), not five minutes.

## Milestone O19 — the card persists: `--card-rw` write-back, `card flush` / `card status` on the pipe; the panel boots a card file as it is ✅ (13 Sep 2026, branch `panel-ui`)

The owner's words: *"if I save the project, but then quit the program, not
only are all the samples I added to the flash gone, but my project is not
saved, and I cannot continue my previous work."* Two causes, both in the
code: `panel_server.py` rebuilt `out/_panel_card_<port>.img` from the
fixture at every start (and wiped the per-port sample pool), and the port's
`AtaCard` held the whole image in `m_img` — WRITE SECTORS landed in that
vector and were never written back to the file, so the firmware's own SAVE
PROJECT (which does write: 22,752 sectors on the OTLIVE fixture) went to
RAM. The design that fixes both is the hardware's: **the CF card is a real,
persistent file.**

### What changed in the port (`card.h`, `card.cpp`, `main.cpp`)

- `AtaCard::setWriteBack(path)` opens the image file `O_RDWR` and keeps the
  descriptor; `commitSector` — after the copy into `m_img`, as before —
  `pwrite()`s the same 512 bytes at the same offset, synchronously, before
  the WRITE completes (`writtenThrough()` counts the sectors, `writeErrors()`
  the short writes, never retried). Nothing is buffered on this side: after
  a commit the bytes are the kernel's, so a `kill -9` of the child loses
  nothing that was written. `flush()` is an `fsync`; the destructor fsyncs
  and closes. Reads are from memory as they always were (the file is never
  re-read); the class is copy-deleted.
- `--card-rw` (needs `--card`, else `card rw : --card-rw needs --card`,
  exit 2): after the card is attached, `setWriteBack(cardImage)` and one
  boot-log line `card rw    : write-back on -- WRITE SECTORS go through to
  <file> (O19)`. Without the flag the class and every line of output are
  the O18 ones — the gate below.
- Two interactive commands (`serveInteractive` now takes the card):
  `card status` → `card ok rw=0|1 path=<file> written=<sectors the firmware
  wrote> through=<sectors in the file> errors=<n>`; `card flush` → `card ok
  rw=0|1 through=<n> errors=<n>` (fsync; `card fsync-failed ...` if it
  fails); `err no card` without a card, `err usage: card status | card
  flush` otherwise. `quit` flushes before its `ok`. EOF and a fault leave
  the file as it is (already written through); SIGTERM/SIGKILL the same.

### What changed in the panel (`panel_server.py`, `panel.html`, the app)

- `--card <file.img>`: the image is booted **as it is** (no rebuild, no pool
  wipe), the child spawned with `--card-rw` when the binary knows the flag
  (`/status card_rw`; an older binary boots it read-only and says so in
  `backend_note`). A missing file is created once from `--project` + its
  sibling AUDIO + `--audio` — `build_card` as before — and a sidecar
  `<file.img>.json` keeps the set/project names, the removals marked for
  the next re-insert, the pool path; later starts read it (the firmware
  does not reload its last project by itself in emulation: a bare boot of
  the saved card, no `--mount`, leaves `0x100f8480`/`0x100f8378` zero with
  the card ready, `out/_agents/persist/bare-boot` run). The names the
  firmware holds are read at every flush and the sidecar follows a
  PROJECT > CHANGE on the unit. Without `--card` the per-port path is
  byte-for-byte what it was.
- `card flush` after every action batch and every 3 s idle (`_card_flush`);
  `_stop_child` = `card flush`, `quit`, kill — used by every reboot path
  (respawn, re-insert, sound switch) so a stopped child never leaves the
  file behind its memory. A SIGTERM handler in `main()` ends
  `serve_forever` through the `finally` (flush, quit, sidecar) — Python's
  default action killed the interpreter outright before, the app's README
  said so.
- The pool is `<file.img>.pool/`, never wiped. **RE-INSERT** on a
  persistent card (`_commit_persistent`): flush + stop the child, `hdiutil
  attach -imagekey diskimage-class=CRawDiskImage -nobrowse` (mounts the
  builder's image as FDisk + DOS_FAT_16 at `/Volumes/<label>`;
  `-mountpoint` answers "no mountable file systems" for it), copy the pool
  into `<SET>/AUDIO` (VFAT long names by macOS), delete the marked files,
  `card_clean` (`.fseventsd`, `.Spotlight-V100`, `.Trashes`, `._*`,
  `.DS_Store`, `.metadata_never_index` — the volume is asked not to index or
  log first), `hdiutil detach`, re-list, boot. Never a mount while the
  child runs.
- `Fat16Image`: a read-only walk of the image (MBR partition, BPB, the FAT,
  8.3 + LFN entries, cluster chains) — `/samples` lists `<SET>/AUDIO` from
  the file directly (`on_card: true`, the format from each file's first
  16 KB through `sample_header`, which takes bytes now), `/card` the sets
  and projects on it, and the sidecar-less card is booted into the first
  set/project found.
- `/card/eject` (flush, stop, mount browsable, `open` in Finder unless
  `?open=0`; every action answers `ok: false` "the card is ejected", the
  loop idles, nothing respawns) and `/card/insert` (clean, detach — `-force`
  on a second try — re-list, boot); `/status` gains `card`, `card_mode`,
  `card_rw`, `card_ejected`, `card_mount`, `project`. A server started on
  a card the previous one left mounted detaches it first
  (`card_mounted_at` from `hdiutil info -plist`).
- `panel.html`: the ejected state only — the slot shows the card out, the
  drawer says where the volume is and has INSERT CARD.
- The app: File > New Card from Project… (`out/cards/<Set>-<Project>.img`,
  remembered in `cardPath`, Open Existing / Replace when it exists), Open
  Card…, Show Card in Finder, Eject Card / Insert Card (one item, the
  title from `/status`), Open Project (scratch card)… keeps the old
  behaviour and forgets the card; `VIRTUAL_PANEL_CARD=<img>` (one launch),
  `VIRTUAL_PANEL_PORT_BIN=<bin>` (`--port-bin`). Quit → SIGTERM → the
  server's handler.

### Measured (13 Sep 2026, the M5, macOS 26.5; `out/_agents/persist/`: `verify.py`, `verify.log`, `verify.json`, `shots-verify/`, `oracle.out`, `fit-rt.out`)

One run of `verify.py` on port 8597 (its own `cards/VERIFY.img`, the
patched LTO build `build/ot_emu`, `--dsp-rt` on):

| step | measured |
|---|---|
| A new card from the OTLIVE fixture | ready in **7.1 s**; `/status` `card_mode persistent, card_rw true, project OTLIVE/PROJECT`; the child's argv carries `--card-rw`; 37 files listed from the image's AUDIO, none pending; sidecar `{set, project, image_bytes 67108864}` |
| B REC, TRIG 3 | REC LED on; the trig-3 LED (row 0 bit 4) 0 → 1, row 0 = `0x11` (bit 0 the fixture's trig 1) |
| C FUNC+MIXER, RIGHT, DOWN, YES, YES | the PROJECT menu popup 5/0/0xf6/0x40 (KEYMAP.md's), the `SAVE PROJECT ... CONTINUE?` box (shots C1–C4); `card flush` `through=0` → **`through=20030 errors=0`** (10.3 MB) 3.3 s after the second YES (a first burst at once, the bank files ~2 s later; the by-hand run wrote 22,752 — the save's size depends on what changed), the image's md5 changed |
| D SIGTERM | the server exits rc 0 in **78 ms**, no child left on the card, md5 unchanged |
| E `--card` alone | ready in 7 s, `project` from the sidecar; the trig-3 LED off until REC, **on in GRID RECORDING** — the trig is on the card |
| F a generated WAV | `/samples/add` → pending; `/samples/commit` → re-insert + boot **9.1 s**; listed `on_card: true`, pending `[]`, the pool empty, 38 files in the image's AUDIO; the firmware's file browser (T1 ×2, RIGHT, UP to the top of the list — `verify_browser.py`, shots J2/J3) lists it first: `AAA persist 440.wav 0.08`, footer `44.1k 16b 2Ch`, then the WAV copied in through the eject below |
| G SIGTERM + restart | still listed on the card (38) |
| H eject / insert | ejected in 0.5 s, mounted at `/Volumes/OCTABAM`, root = `[OTLIVE]`, `/key` → `ok: false` "the card is ejected", 0 children; a second WAV copied into `OTLIVE/AUDIO` by hand; `/card/insert` → booted in **8.1 s**, mount point gone, both files `on_card`, 39 in AUDIO, the image's root still `[OTLIVE]` (no `.fseventsd`/`.Spotlight-V100`/`.Trashes`) |
| the app (port 8598, `VIRTUAL_PANEL_CARD`, `VIRTUAL_PANEL_PORT_BIN`) | spawned `--port-bin ... --card ...`, `card item: Eject Card, enabled` at ready (+8 s); `/card/eject` → `Insert Card, enabled`, `EJECTED at /Volumes/OCTABAM`; `/card/insert` → back; SIGTERM → the app gone in 0.13 s, `server pid ... stopped (status 0)`, no child, nothing mounted; `build.sh` 0 warnings, `codesign -vv` valid |

**The gate: 28 PASS, 0 FAIL** on the patched build against
`out/emu/ot_emu.ref-73c2815` (`out/_oracle/reports/20260913-083439-persist.txt`:
boot logs, serial, goldens, the `--interactive` UART step by step, 109
peeks, 47 run stamps at |dsample| = 0, `interdsp.pcm` identical, ctest 7/7),
and **the rt audio fit −33.0 dB** (`rtdrive.py --rt --seconds 4` on
`otlive2.img` → `fit.py`: onset 82, lag 81, gain 0.7032).

### What it does not do

- Route A has no write-back (`--card` needs the port backend; the server
  exits saying so instead of falling back).
- `out/emu/ot_emu` is rebuilt by the orchestrator: until then the app's
  default spawn boots a persistent card **read-only** (`/status card_rw`
  false, the reason in `backend_note`); `VIRTUAL_PANEL_PORT_BIN` points a
  launch at `out/_agents/persist/build/ot_emu`.
- The "last set / last project" record the unit keeps is not on the card in
  emulation; the sidecar stands in for it. A card copied without its
  `.json` boots into the first set/project found on it.
- An eject leaves the volume mounted if the server dies; the next start
  detaches it (`card_mounted_at`). Finder's `._*` and `.DS_Store` are removed
  at insert; files a user leaves open in Finder make the detach fall back to
  `-force`.
- The write-through is per sector (~22,750 `pwrite`s for a project save,
  inside the emulation's own `run`); it was not measured against the pacer
  beyond "the count stood still 3.3 s after YES". A `pwrite` that fails is
  counted (`errors=`), not retried.

## Milestone O20 — the effects that use delay memory: an effects rig in both DSP modes, the Echo Freeze Delay's eDMA copies, and what the chorus/comb/delay still lack 🟡 (13 Sep 2026, branch `panel-ui`)

The owner's report: "the reverbs and the delay, and the chorus, and I'm
sure others like comb filter, don't sound good at all; the chorus sounds
like a very small glitchy loop of the audio repeating" — heard through the
panel with sound on (`--dsp-rt`), where plain sample playback is proven
bit-identical to the lockstep interpreter (O17c, `fit.py` −33.0 dB). This
milestone builds a rig that puts each effect on the playing track through
the firmware's own UI, saves the card, and renders the same card under both
DSP modes; measures every effect against the clean render of the same
emulator; and separates the two hypotheses with instruments. **Findings:
the DELAY is silent in BOTH modes (H1: two ColdFire-side causes, one fixed
here, one located); the CHORUS and COMB add nothing to the dry in BOTH modes
(H1: the module runs, writes its delay lines with audio, and multiplies its
input by a per-instance word the emulated DSP drives to zero — located to
the word, not fixed); the DARK REVERB works (a real, decaying wet in both
modes); and `--dsp-rt` is no longer bit-identical to lockstep once an
effect or the delay is on (H2: whole 16-sample blocks re-rendered
differently about once a second, run to run — small, documented, not
fixed).** Nothing in the rt scheduler was changed. Everything under
`out/_agents/fx/` (scripts, cards, takes, renders, logs, `analyze_*.txt`).

### The rig (`out/_agents/fx/rig.py`, `rig2.py`, `panctl.py`, `render.py`, `analyze.py`, `wet.py`, `anatomy.py`)

- **Setup through the panel**, on an own server (`--port 8593`, own
  binary `--port-bin out/_agents/fx/build/ot_emu`, LTO off), a fresh
  `--card out/_agents/fx/cards/<name>.img` made from the clean tree2 project
  (`out/_agents/audio/tree2/OTLIVE/PROJECT`, `third-0.wav` on the current
  track). The playing track is **T5** (the UI's current track at boot,
  `0x100b14cc` = 4, a FLEX machine); T1 is a STATIC machine. **The EFFECT
  SETUP chooser opens with FUNC + [EFFECT 1/2]** (`/key` row 0x25 bit 5 held
  around row 0x24 bit 5/6; the popup slot `0x460d175c` reads `0x46c7d34c`);
  a second bare press of the page key did NOT open it through `/key` on the
  paced child (the first rig run assigned nothing and YES on the main
  screen opened ARM ALL — `chorus/01..17_*.png`). The list is NONE, FILTER,
  EQ, DJ EQ, PHASER, FLANGER, CHORUS, SPATIALIZER, COMB, COMPRESSOR, LOFI
  (+ DELAY, PLATE, SPRING, DARK on FX2), the current effect highlighted, so
  CHORUS = 5 × DOWN from FILTER, COMB = 2 more, DARK = 3 × DOWN from DELAY;
  YES assigns (the right pane shows the effect's SETUP boxes) and leaves the
  window open; NO closes it; the page key once shows page 1 with the new
  names. Page-1 knobs A–F are the six slots (`/knob?row=0x30..0x35`,
  deltas applied exactly); the value is read back from the Part
  (`0x400e21e0 + 0x8ee9a + 4·24 + 12/18`, ids at `+0x8ed80/+0x8ed88 + 4`),
  which is how every state below was confirmed. PLAY 6 s (the pacer held
  `rt` 0.93 on this LTO-off build for every effect, reverb included), STOP,
  the take, then SAVE PROJECT (FUNC+MIXER, RIGHT, DOWN, YES, YES; 22,752 →
  47,355 sectors flushed) and a copy of the card per effect.
- **The cards** (`out/_agents/fx/cards/`): `clean` (untouched);
  `chorus` (FX1 0x12: DEL 64 DEP 94 SPD 13 FB 0 WID 127 MIX 40); `chorusmax`
  (DEL 127 DEP 127 SPD 13 FB 64 WID 127 MIX 64); `comb` (FX1 0x13: PTCH 26
  TUNE 64 LP 127 FB 127 MIX 90); `delay` (FX1 NONE; FX2 0x08 DELAY: TIME 47
  FB 70 VOL 127 BASE 0 WDTH 127 SEND 100 — at 120 BPM TIME 47 = 47/256 of a
  whole note = 367 ms = 16,193 samples); `dark` (FX2 0x16: TIME 84 SHVG 0
  SHVF 127 HP 0 LP 127 MIX 100, the delay's SEND back to 0).
- **The renders**: `render.py --card X --out D [--rt] --seconds 6` boots
  `ot_emu --interactive --mount --set OTLIVE --project PROJECT --dsp[-rt]`
  on the saved card (rtdrive.py's shape), PLAYs 6 s in `run 250` slices and
  writes `main.wav` (core 0 main L/R, 16-bit). The panel's own take is
  bit-identical to `render.py --rt` on the same card before STOP (chorus:
  0 mismatches of 261,132 frames), so the take is the rt render.
- **The measures**: `analyze.py` (onset, RMS, autocorrelation lags,
  spectrum, 100 ms envelope, lockstep-vs-rt alignment: max |diff|,
  mismatching samples, correlation, per-second dB); `wet.py` (the WET =
  effect render − clean render of the SAME emulator and mode, onset-aligned:
  its level, envelope, autocorrelation, cross-correlation with the dry over
  0–1000 ms, spectrum); `anatomy.py` (the wrong samples of rt against
  lockstep by position mod 16, magnitude, the stale tests, run lengths).
  A dry-fit against `third-0.wav` cannot separate a chorus from the dry on
  this tonal sample (a 6,075 Hz tone; a copy delayed by a few ms is
  absorbed into the fit gain) — the wet against the clean render is the
  measure that works.

### Measured (13 Sep 2026, the M5, macOS 26.5; `out/_agents/fx/rig2/analyze_*.txt`, `wet_ls.txt`, `anatomy_ls_vs_rt.txt`)

| card | wet vs dry (lockstep) | what the wet is | lockstep vs rt | rt vs rt (2 runs) |
|---|---|---|---|---|
| clean | — | — | **bit-identical** (0 of 287,500) | bit-identical (O17c) |
| chorus (MIX 40) | | | first hit identical, then −42 dB (0.37 % of samples, max 927) | |
| chorusmax | **−61.5 dBFS** vs dry −23.6 (−38 dB: nothing) | the same residual as the no-FX1 delay card | −36 dB, 3.6 % of samples, max 9,225; 11 whole 16-sample blocks | 103 wrong samples: 7 whole blocks (2.03, 3.14, 3.19, 3.21, 4.09 s) |
| comb | **−60.6 dBFS** (nothing; a feedback comb whose line reads zeros outputs exactly the dry) | as above | −37 dB, 3.6 %, max 5,901 | |
| delay (FX1 NONE) | **−63.2 dBFS**: no energy between the hits (−83…−240 dBFS with SEND 100 / FB 70): **no repeats** | the second voice / LSB noise | −34 dB but only 0.03 %: **6 whole blocks** (1.06, 2.17, 3.18, 4.06, 4.16, 5.17 s), not stale copies, a different mix of the block | the same 6 blocks in one run, none in the other |
| dark | **−24.1 dBFS**: a tail −47 → −56 dBFS over 0.75 s after each hit, decaying | a reverb | −28 dB, 84 % of samples (a modulated reverb's history: the O12 class) | |

The panel takes measure the same (`analyze_takes.txt`): the delay take
has silence between hits, the dark take a tail, the chorus/comb takes the
dry-fit residual of the clean fixture (−18.5 dB, the DSP's fade-in).

### H1 for the delay, cause 1 ✅ fixed: memory-to-memory eDMA channels moved no data (`rtos.cpp` `Rtos::copyMemToMem`, `installHostPortMover`; `rtos.h`; `main.cpp`)

The Echo Freeze Delay is not on the DSP (EXTERNAL.md §1): the ColdFire's
frame routine at `0x400031a0` points the eDMA at per-track rings in SDRAM
at `0x4f502c10` (8 × 1,411,328 bytes, cleared at boot). Read off the image
(`out/_agents/fx/delay_routine.lst`, the TCD init at `0x40002fd4`):
channels 2/3 fetch this frame's and last frame's delay positions from the
ring (SADDR = the 16-byte-aligned read position, DADDR = the block minus
the misalignment: `0x80003ad8` / block+160−8, ATTR `0x0402` = 16-byte
source bursts and 32-bit destination, SOFF 16, DOFF 4, NBYTES 144, CITER
1, no modulo; CSR `0x321` = START + link to ch 3), the routine busy-waits
ch 3's DONE at `0x400035a8`; channels 4/5 write the mixed block
(`*(0x800000e8)` = `0x80003ae0`) into the ring at the write position (ATTR
`0x0404`, NBYTES 128; a mirror at ring + 1,411,200 when the write lands at
the base; CSR `0x521`), waited at `0x40003780`. Route A's model moves no
data on any channel and the port's mover (O8) only ever carried the
host-port blocks (`installHostPortMover` returns for a DADDR outside
`0x20000000-0x20000fff`) — so the taps were never fetched and the ring
never written: the delay mixed zeros, in both modes.

The fix: at the kick, a channel with neither end in the host-port window
is copied per its TCD (`copyMemToMem`: NBYTES per minor loop as SSIZE
reads at SOFF and DSIZE writes at DOFF, SMOD/DMOD honoured, CITER minor
loops; the TCD's words left as the firmware wrote them — it reprograms the
addresses every frame and never reads them back). Counted:
`memToMemBlocks/Bytes`, reported only under `--block-log` or
`OT_M2M_REPORT=1` (the strict oracle compares the batch log line for line).
Measured: **12,800 blocks / 1,740,800 bytes per 400 frames** (32 per
frame: the routine runs for all eight tracks), the reference render
byte-identical (below), the clean card's lockstep render bit-identical to
the unfixed binary's, the rt fit gate unchanged.

### H1 for the delay, cause 2 🟡 located, not fixed: the mix loop's gains for T5 are zero

With the copies in, the delay is still silent: T5's ring is zero across
the whole span of a hit (`fix/delay/peek3.out`: seven 64-byte peeks from
ring + 0x94000 to + 0xac000, all zero), while T5's delay state record
(`0x8000609c`: read `0x4fb0ba88`, write offset `0xc6f80`) says the
geometry is right — **read = write − 16,545 frames = 375 ms** for TIME 47
(the 367 ms expected plus a frame or two). The pipe's `watchmem` on the
block the ring is written from (`0x80003ae0`, `watchblk.py`, 700 ms of
play): the eDMA fetch lands there (my copies: 277 non-zero bytes of
320,256), and the mix loop's two output stores (`movel %d1,%a0@+` at
`0x4000376c` / `0x40003772` = dry×SEND + taps×FB per sample) wrote
**15,440 zeros and nothing else**. Those gains come from the per-track
coefficient records at `0x80006180` (68 bytes a track, `moveml
%a4@(20),%d1-%d2/%a1-%a4`, ramped per sample): T1–T3's records carry
gain words (`0x00800000`, `0x007fffff`, `0x08ff00ff`), **T5's
(`0x80006290`) holds `8, 0x10, 0x10, 0x10, 0x800049d8, 0x80000690, 0,
0xc, 0…` and T6–T8's are all zero** (`fix/delay/peek4.out`). So the
firmware's parameter path that fills the delay's coefficient record for
T5 (SEND 100 / FB 70 / VOL 127 in the Part, confirmed) never ran under
the emulator, or the record walker reads a different table for tracks
5–8 — the next step is to read `0x400031e4–0x400033c0` (the walker,
`0x80005f8c`/`0x5f98`/`0x5fa0`/`0x5fa2` + 68·t) and `0x40003284` (the
routine's own staged-time write, EXTERNAL.md) with a `watchmem` on
`0x80006290`, and to check whether the record's writer is on a UI/menu
task path (the O12 "slew/scene stage" family) that a saved-and-reloaded
project should have run at the load. Not the eDMA, not the ring, not the
DSP.

### H1 for the chorus and the comb 🟡 located to the word, not fixed

The chorus (payload A, init `P:0xeb7`, process `P:0xed7`) is dispatched
every frame for T5 (`--dsp-pcwatch 0:ed7`: r7 = `0x6100` = position 0's
FX1 block, x0 = 0x12, r6 cycling the three parameter copies 0x263/0x2e3/
0x363, r0 = `x:$20e` = 0 = the 16-sample stereo block at X:0x0000), its
parameter block is right (`X:0x263..`: 7f 7f 0d 40 7f 40 = DEL DEP SPD FB
WID MIX), its instance base is `Y:0x1000` (`X:0x611{3,4}` = 0x1000 /
0x1600), and it **writes its two 1,532-word delay lines with the block's
audio** (`--dsp-writes`: non-zero writes in Y:0x1000–0x15ff and
0x1600–0x1bff at the hits' duty cycle; the line-write loop `P:0xfe4–0xfed`
forms `Y:base+idx` with the AGU's "N a multiple of 2^k is linear" rule,
which interpreter and JIT both apply). What it does not do is hear its
input: at `P:0xf43–0xf4b` the block is scaled by `y0 = x:(r7+$1d)` into
the wet scratch (`Y:0xc0`/`0x110`), and **`x:0x611d` is 0** — the scratch
gets zeros (`--dsp-watch 0:Y:c5`: pc 0xf49 ← 000000 every frame), the tap
sums are 0/−1 LSB (pc 0xfa1), and the shared mixer `func_7b0` mixes a zero
wet at MIX. The word is one of the module's own per-tap state words
(`x:(r7+$1a)..` — the loops at `P:0xf24` (stride 4) and `P:0xf62` (stride
3) smooth them toward payload tables at `X:0x8d79..0x8d8c` and
`X:0x6c00/0x6d00`, all present and non-zero in the emulated memory): at
frame 60 it reads `0x039b9a` and is growing, its siblings `0x6121/0x6125`
are `0x493782/0x771123` at frame 400, and by frame 400 it is back to 0
(`rig2/chorusmax/batch_state.log`, `batch_tbl.log`). The interpreter and
the JIT agree to −36 dB on this card, so it is a DSP-model defect common to
both (candidates, all in this module's path and none used by Sam's
modules that `dsp_host` renders bit-exactly: the nested `do` loops over
`(r3)+n3` with two strides, `tge`/`ifmi`/`ifgt` conditional transfers,
`lsr #$10,a` on the accumulator, `mpyi`/`macri`, `l:(r4)+` long moves,
`bset #$14,sr` scaling in `func_773`), not a memory-size or window defect
(the pair allocates 2 M words per space; the FX1 slot is internal Y). The
COMB (`P:0x1eca/0x1edc`) shows the identical wet (−60.6 dBFS) and was not
traced separately. Falsifier for the next session: a `--dsp-watch
0:X:611d` (the last 16 writers with PC and value) over the first 100
frames, then the writing instruction's semantics against the DSP56300FM.

### H2: `--dsp-rt` diverges from lockstep once the frame carries an effect (not fixed)

The clean card stays bit-identical (0 of 287,500 samples, ls vs rt, fixed
and unfixed binaries), but with an effect or the delay's SEND on, rt runs
differ from lockstep and from each other by **whole 16-sample blocks
re-rendered with a different mix** (`anatomy_ls_vs_rt.txt`: the delay
card 6 blocks in 6.5 s, all runs of exactly 16, not stale copies
(`cand==ref[i±16/32]` 0), |diff| up to 9,194; the chorus card 7–11 such
blocks plus the wet's own LSB drift; a second rt run of the delay card
had none of the six and the chorus card a different seven). That is the
O17c fix-5 class — the ColdFire's own record for a voice rendered inside
the frame ISR with a flipped 8-sample position bucket — reappearing when
the ISR chain is longer (the delay's EMAC work, the effect's heavier DSP
frame). `OT_FENCE_TRACE` / `OT_DSP_FRAMETRACE` and `blockdiff*.py` are the
instruments; not pursued here because it is −30 dB and inaudible next to
the H1 findings, and the fix belongs with the drain-time table (`rtos.cpp`
THE DRAIN TIMES).

### The gates

- Strict oracle, the fixed binary against `out/emu/ot_emu.ref-73c2815`
  (`out/_agents/fx/build-fix`, LTO off): **28 PASS, 0 FAIL**
  (`out/_oracle/reports/20260913-111137-o20-m2m-2.txt`). The first run
  failed only `render.log` on the new report line (`20260913-110823-o20-m2m`);
  the reference `render.wav` (8 ch, 330,677 frames) and `interdsp.pcm` are
  byte-identical with the copies on — the reference fixture's rings hold
  silence, so moving them changes nothing it renders.
- `fit.py` on the clean fixture in rt (`rtdrive.py --rt --extra "--card
  out/_agents/audio/otlive2.img"`, the fixed binary): **gain 0.7032,
  −33.0 dB** (`out/_agents/fx/fit-rt2/`); the tree2 clean card −33.0 dB in
  both modes.
- ctest 7/7 (inside the oracle).

### What it does not do

- The delay still has no repeats (cause 2 above); the chorus and comb
  still add nothing (their word); the dark reverb was not compared to a
  hardware reference (none exists here). Plate and spring were not run.
- The reverb on a shared-window FX2 slot (T7/T8, `Y:0x30000/0x34000`) was
  not exercised: the fixture has no audio on those tracks.
- The panel's monitor was not the cause on this machine (`rt` 0.93 with
  every effect, no starvation-specific symptom), but the owner's PGO
  binary and machine were not measured under the reverb.
- The rig's one-shot chooser navigation assumes the current effect
  (FILTER / DELAY on a fresh part); `rig2.py` tracks it across steps.
