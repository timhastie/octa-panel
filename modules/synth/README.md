# Synth machine (phase 1: the hollow voice)

**A FLEX track whose sample is named `SYNTH*` plays a generated waveform
instead of the sample.** In this phase the waveform is a clean sine at
**C4 = 261.6256 Hz for PTCH 0** (the semitone reference; TRIG 13 in
CHROMATIC mode); everything else is the Octatrack's own FLEX machinery.
The ColdFire generates the track's *source* sample data every frame and
the DSP does what it does to any sample: PTCH with parameter locks, LFOs,
scenes, the chromatic keys and the quantizer; RATE, retrigs, sample locks;
the AMP envelope; the filter and FX1/FX2; level, pan, mute and cue. A
stock unit plays the file itself (the shipped `SYNTH.wav` is silence), so
a project that uses it degrades to a silent track, not a broken one.

One ColdFire cave (1,068 bytes, floating, position independent) and one
4-byte poke, both in the main-OS section; no displaced instructions, no
hook in a stock routine. **UNFLASHED**; everything below is measured under
`ot_emu` — the lockstep interpreter through the pipe, and the panel's
real-time JIT mode through keys and takes — 22 Sep 2026. Logs, WAVs,
screens and numbers: `out/_agents/synth/`.

To try it: build (`PATH=.venv/bin:$PATH REMIX=synth make cf` or `REMIX=tim
make cf` → `out/mainos_cf.bin`), put any WAV named `SYNTH.wav` (or
`SYNTH-anything.wav`; 16-bit 44.1 kHz, a few seconds, LOOP on for a held
note) in the set's AUDIO folder, load it into a FLEX slot with the unit's
file browser, assign the slot to a FLEX track, place trigs. `PTCH` is the
note, the AMP page the envelope.

## The design, and why

Two ways were on the table: **(A) a sixth machine type, SYNTH**, in the
SELECT MACHINE TYPE list with its own descriptor, or **(B) a FLEX machine
whose sample is a marker the cave recognises**. This is (B), for what the
disassembly says about (A):

- The Part stores machine type 0..4 and lays out the PLAYBACK parameters
  as **five 6-byte blocks per track** (`base + 0x8edaa + track·30 +
  machine·6 + slot`) and the sample slots as **five bytes per track**
  (`base + 0x8f04a + track·5 + machine`): a machine index 5 indexes the
  NEXT track's blocks. Every reader computes that itself — 40 sites read
  the machine byte (`addal #585122` in the listing), 26 read the parameter
  base, in the knob handler, the p-lock editor, the trig routine, the
  frame builder, the pages, the MIDI paths and the project code. Aliasing
  5 → 1 everywhere is a large, error-prone patch set; the descriptor
  table's spare entries (`0x400d5f4c/50` = NEIGHBOR's page, `0x400d5f54`
  = 0) are the only part of (A) that is one poke.
- The trig routine `0x40005030` starts a FLEX voice only for a *loaded*
  slot (settings byte `+0x129 ≠ −1`), so a pseudo-slot with no sample
  never sounds, and the voice-start path (`0x4000f450`, the streamer
  `0x40005c7c`, the retrig and end-of-sample logic) is exactly what a
  synth voice wants to reuse rather than replace.

So: keep the FLEX machine whole, and take over its **audio** at the one
point where it is produced. The per-frame record packer renders each
track through a per-track renderer pointer; the cave sits in front of the
FLEX renderer, lets it run, and then rewrites the source samples it just
shipped. The DSP resamples that source by the voice's rate, which is where
PTCH, the locks and the LFO are already folded in — no pitch arithmetic in
the cave at all.

## What was found (stock 1.40C main OS at `0x40000400`; listing `out/_agents/synth/mainos.dis`)

### The per-frame record and its renderers

| what | where |
|---|---|
| the record packer | `0x4000d3fc..0x4000d55e`: for each of 8 tracks, cursor `0x80001c80 := 0x80001c90 + ping·0xa80 + 336·track` (T1–T4's records go to core 1, T5–T8's to core 0 via eDMA ch 0), then TWO renderer calls, `renderer(track, ping, 0, n)` from the current table `0x400d61d0[track]` and `renderer(track, ping, n, 16)` from the next-frame table `0x400d61f0[track]`, where **n = the low nibble of the per-track event byte `0x46104d0c + track`** — the sub-frame position of this frame's event — and **bit 4 of that byte = a voice starts this frame**: the packer then calls the start handler (`0x400d6454[kind]`, `0x4000f450` for STATIC/FLEX), installs the new renderer from the kind table, resets the render state (`0x80004898 + 40·track`) and clears bits 4–7 AFTER the second call |
| the kind table | `0x400d6434`, 8 longs, index = the machine type (byte `0x80000eb4 + ping·8 + track`): 0 STATIC / 1 FLEX / 4 PICKUP → `0x40004008` (the sample renderer), 2 THRU → `0x40004424`, 3 NEIGHBOR → `0x4000466c`, 5–7 → `0x400047f0` (silent). The renderer for track t is installed at `0x4000c004` (`0x400d61f0[t] := table[kind & 7]`) |
| the sample renderer | `0x40004008(track, ping, start, end)`, C convention, d0/d1/a0/a1 scratch: writes a 16-byte header at the cursor, recomputes the rate on the frame's second call (`btst #4` on the `end` argument's low byte — 16 = the full frame), ships the source samples through `0x40007960` in sub-segments (retrigs), advances the cursor. The rate: the pitch word `fp@(10)` of the DSP parameter record (`fp = 0x80000510 + ping·384 + 48·track`) interpolated through the table `0x400aae0c` (a 2^(x/12) curve) into `state+24`, times the RATE word `fp@(0)` through `0x400aa294`, into `state+36`, Q26 (`0x04000000` = 1.0) |
| the record a call writes (measured, `runs/stock_explore2.log`) | header `+0` = source count (bits 0–7) \| out count << 8 [\| out2 << 16 \| out3 << 24], `+4` fractional phase, `+8` rate Q26, `+12` tag; then `src` samples of 8 bytes: L long, R long; **the DSP takes the top 24 bits of each long** (a 16-bit sample sits at bits 31..16). Silent T8 at n = 0: `[0,0,0x04000000,0]` then `[0x1010, 0x20, 0x04000000, 0x8000]` + 16 zero pairs; a sounding track at n = 4: `[0x404, 0x3c, 1.0, 0xf0000000]` + 4 pairs, `[0xc0c, 0, 1.0, 0]` + 12 pairs (`ffbe0084 ffb000a0 …`) |
| the voice struct | `0x800049d8 + 0xa8·track`: `+0` active byte (`0xff` while the CF voice runs, 0 when it ended — after which the renderer ships zeros), `+4` the slot's state record (`0x46c922c4 + 44·slot` FLEX, `0x46c90a78 + 44·slot` STATIC), `+8` its settings record (`0x100b14f0 + 0x448·slot` FLEX, `0x100d5b30 + …` STATIC), both written by the start handler `0x4000f450` at `+0x4dc/+0x4e0` unconditionally, i.e. before the start frame's second call. **The settings record's path string is at `+0`** (`"../AUDIO/SYNTH.wav"` for a slot the unit's own browser loaded; slot numbers are 0-based: FLEX slot 5 = index 4 = `0x100b2610`) |
| the trig → voice path | `0x40005030(track, cmd, flags, slot)`: reads the machine byte, the Part's slot byte (`+0x8f04a + track·5 + type`, or the argument), the settings record (`≤ 128` STATIC, `≤ 135` FLEX/PICKUP), refuses an unloaded slot (`+0x129 == −1`), posts `0x8000186e/0x8000188e/0x800018ae[track]` (`slot \| type << 10`); the frame builder (`0x4000b2ee..`) checks the type against the Part's machine byte and posts the mailbox `0x46c80354[track]` and the slot byte `0x46c80282[track]`; `0x400068e4(track, ping, start, end)` is the per-track voice state machine the packer runs before rendering (`0x4000d322`), not a renderer |
| parameter locks (found on the way, corrects a 35-byte guess) | a pattern's track record (`blob + pattern·0x8ed8 + track·0x91a`; bank A blob `0x400e21e0`) holds **32 bytes per step from `+0x59`**: byte 0 = PTCH … byte 31 = the sample slot lock; the p-lock editor `0x40050e60` writes the blob byte and an SRAM mirror (`0x100161a6 + pattern·stride + track·2330 + 1 + step·32 + param`). T8 step 9's PTCH lock: blob `+0x159`, mirror `+0x101` |
| the FLEX cost, stock (`runs/stock_explore.log`, `hits` on `0x40004008/0x40004266`) | first call 16 instructions; second call 261 idle, 264–694 streaming, **897–1,152 for the playing SYNTH slot** (mean 936) |

### The cave (`synth.s`, 1,068 bytes; `.org` layout in `manifest.py`)

`sy_render(track, ping, start, end)` at `+0` is the kind table's FLEX
entry (`0x400d6438`: `0x40004008` → the cave, poked by `emit()` — the
build asserts the stock long first). It saves `d2–d7/a2–a3`, notes the
cursor, calls the stock renderer with a copy of its four arguments, keeps
its return value, then:

- on the frame's **second call** (`end == 16`) with **bit 4 of
  `0x46104d0c + track`** set (a voice starts this frame): resets the
  track's phase and resolves the marker from the new voice's settings
  record — the file name after the last `/`, compared with `SYNTH` (five
  characters, case-sensitive, 255-byte scan limit, a null record = no) —
  into `sy_on[track]`. The first call of that frame renders the OLD voice's
  tail with the old flag, so a step that sample-locks from a sample to
  SYNTH (or back) switches exactly at the trig's sub-frame position;
- if `sy_on[track]` and the CF voice is active (`voice+0 ≠ 0`): reads the
  source count from the header it noted (`+3`), and rewrites the L and R
  longs of every shipped source sample with `sine(phase) << 16`, phase +=
  `25,480,119` (Q32 cycles: 261.6256/44100 · 2^32, 0.000 cents off);
  the sine is a 256 + 1 entry s16 table (amplitude `0x4000` = −6 dBFS)
  with linear interpolation (predicted worst spur −64 dB; no
  interpolation would be −48).

Position independent (pc-relative data, OS absolutes): the bytes are
identical linked at `0x400d7000`, `0x400d7300` and `0x400d6b80`
(`out/_agents/synth/asm/`), and `PINNED` in the manifest is the ratified
form the build re-links at the address the cave lands on — `0x400d6b80`
in `synth`, `0x400d6e00` in `tim`, both accepted. State lives in the cave
(`sy_on[8]`, `sy_phase[8]`; the main OS runs from DRAM).

Builds: `REMIX=synth make cf` → 761 bytes changed vs stock, cave at
`0x400d6b80`, 3,216 B of the zero run left (`build_synth.log`); `REMIX=tim
make cf` (DIRECT JUMP + SCALE QUANTIZER + SYNTH MACHINE) → 2,418 bytes
changed, the synth cave at `0x400d6e00`, the quantizer's tables at
`0x400d7800..`, **812 B of cave left** (`build_tim.log`). Both keep the
DSP payloads, dispatch and FX2 chooser byte-identical to stock (the CFONLY
check).

## Measurements (all `out/_agents/synth/`)

**Rig.** `trees/synth8q` = the clean tree2 fixture (`out/_agents/audio/tree2`)
with `SYNTH.wav` (4 s of silence, 16-bit mono 44.1 kHz) as FLEX slot 5
(`LOOPMODE=1`), T8 on it in parts 1 and 5, T3/T4/T7 moved to empty slots
so only T8 sounds, the fixture's step-9 sample-slot lock on T8 cleared
(`mktree.py`, `cards/synth8q.img`); T8 trigs at steps 1 and 9, 120 BPM.
`render.py` boots `out/emu/ot_emu --interactive --dsp` (lockstep) on the
image + card, PLAYs, captures main L/R; `measure.py` gives the FFT peak
(parabolic interpolation, 0.8 s Hann window), zero-crossing frequency,
RMS and a 100 ms envelope. `explore.py` is the peek/watch driver.

**1. PTCH 0** (`runs/ptch0`, `REMIX=synth`): the first note, 0.1–0.9 s:
**FFT 261.634 Hz (+0.1 cents), zero crossings 261.684 Hz (+0.4 cents)**,
−24.0 dBFS L and R, worst spur −51.3 dB. The note holds until the next
trig (the fixture's AMP HOLD/REL are 127; a looped FLEX voice holds):
envelope −23/−24 dBFS throughout, silence after STOP. The step-9 note in
the first run played the fixture's own slot lock (fourth-0.wav, 5 kHz
tonal) — a sample-locked step correctly stays a sample.

**2. PTCH +12** (`runs/ptch12`): T8's Part PTCH byte `0x40171062` poked
to 124 (+12.0) before PLAY: **523.258 Hz (+1200.0 cents)**, both measures,
−24.0 dBFS, spur −49.9 dB. The cave did nothing different: the DSP
consumed 32 source samples a frame at rate 2.0.

**3. A p-locked PTCH on one step** (panel, `takes/take-plock.wav`, take 7;
GRID RECORDING, [TRIG 9] held, PTCH encoder +120 detents, the lock clamps
at 124): notes at 0.1–0.9 / 1.1–1.9 / 2.1–2.9 / 3.1–3.9 s = **261.634 /
523.258 / 261.634 / 523.256 Hz** — step 9 alone at +12 (the earlier
take 6 measures the same). The lock was written to the SRAM mirror and
the blob (`+0x159`); a poke of the blob byte alone before PLAY did not
change the pitch (`runs/plock`), so the sequencer reads the mirror or a
later copy — the unit's own editor is the path to use.

**4. The AMP page shapes it** (panel, `takes/amp.wav`, take 8): AMP page,
ATK 0 → 60, HOLD 127 → 30, REL 127 → 40 (Part bytes `0x40171128..2a` =
`3c 1e 28`, read back). Each note now rises from −35 to −24 dBFS over
~340 ms and is released to silence by ~0.5 s (20 ms bins: `-35 -32 -31 …
-24 -30 -51 -75 -81 -86 -90 -92 -999`), repeating at every 1.0 s trig;
the unshaped take holds a flat −24. Knobs reset afterwards with
`/knob/reset` (`0 127 127`).

**5. FX1 = FILTER audibly changes it** (panel, `takes/fx1.wav`, take 9):
FX1 page, BASE 0 → 100 (`0x4017112e` = `64`; a high-pass rising above the
tone). Level −24.0 dBFS → **−69.7 dBFS on the 261 Hz steps and −57 dBFS
on the +12 (523 Hz) steps** — the lower note is 13 dB deeper into the
slope, as a filter should. Reset to 0 afterwards.

**6. Mute silences it** (panel, `takes/mute.wav`, take 10): FUNC + [T8] at
2.0 s of play (mute mask `0x8000000a` `7f` → `ff`): −24.0 dBFS until the
2.0 s bin, **digital silence (−999 dBFS) after it**.

**7. Selecting it on the unit with keys** (panel `--port 8593 --image
out/_agents/synth/mainos_synth.bin --project out/_projects/otlive/OTLIVE/PROJECT
--set OTLIVE --name PROJECT --audio out/_agents/synth/audio --sound on`,
rt 0.999; `panelctl.py`, `shots/`): double-tap [T8] → the FLEX slot list
`« MACHINE:FLEX` (`01_slotlist`), DOWN to slot 5, RIGHT → `LOAD FILE TO
FLEX 5` (`03_browser`), DOWN ×36 to `SYNTH.wav` in the name-ordered list
(`04_browser_synth`), YES loads it — the list reads **`5>SYNTH.wav 0.34`**
(`05_loaded.png`) and the slot's settings record reads `../AUDIO/SYNTH.wav`
— YES assigns it (T8's slot byte `0x4017124e` `03` → `04`,
`06_assigned`), NO leaves. Then FUNC + [T1..T7] muted the fixture's other
tracks, REC (grid recording), [TRIG 9] + PLAY cleared the fixture's
step-9 locks (`ffffffffffffff03` → all `ff`), PLAY/STOP gave take 5: 3.9 s
of the tone at **261.634 Hz (+0.1 cents), spur −52.6 dB** under the panel's
`--dsp-rt` JIT mode (2.5–3.3 s and 3.5–4.3 s windows) — the same numbers
as lockstep. A retrigger (each note restarts at phase 0) shows as a 1 dB
dip in one 10 ms bin.

**8. Cost** (`runs/cost`, `runs/cost12`; `cost.py` over `hits` on the
cave's entry `0x400d6b80` and return `0x400d6cb8` and the stock pair):

| per frame, T8 = SYNTH | first call [0,0) | second call [0,16) |
|---|---|---|
| PTCH 0 (16 source samples): cave's own | 28 | **361 mean, 518 max** |
| … stock renderer inside | 16 | 935 mean, 1,152 max |
| … wrapper total | 44 | 1,296 mean, 1,513 max |
| PTCH +12 (32 source samples): cave's own | 28 | **681 mean, 818 max** |
| … stock renderer inside | 16 | 1,265 mean, 1,619 max |
| … wrapper total | 44 | 1,946 mean, 2,300 max |
| a FLEX track that is NOT a synth: cave's own | 21 | 25 mean (45 max, a start frame) |

~19 instructions per source sample in the loop; the max of the cave's own
figure is the start frame (the name scan). The stock renderer's part is
what the unit already pays for a playing FLEX track (it streams and
resamples the file whose audio the cave then discards); phase 2 may skip
it once the voice lifecycle it carries has been re-read, which would
halve the total.

**9. Stock behaviour untouched** (`ab/`): `tools/emu/ot_emu/oracle/drive.py
--emu out/emu/ot_emu --image <stock | remix>` (boot on the OTLIVE card, YES,
MIXER, NO, T1 double tap, DOWN, RIGHT, NO, NO, PLAY 20 × 100 ms, STOP
5 × 100 ms): `ready.txt`, `steps.txt`, `stamps.txt`, `peeks.txt`,
`txlen.txt`, **`tx.bin` (18,297 UART bytes)** and `stderr.txt` are
byte-identical; `boot.log` differs only in the image path line. No text
differs because nothing new is drawn.

**10. Gates** (`gates.log`, `REMIX=synth`): `make bus`, `cycle_count`,
`verify_slots`, `label_fmt`, `verify_octakit`, `verify_midiscenes`
(SKIP: submodule), `verify_dram_boot`, `verify_labels` (SKIP: no selects),
`verify_menushortcut`, `verify_cfprobe`, `verify_busscreen`,
`verify_ccpage2`, `verify_hidden`, `verify_grains`, `verify_menu`,
`verify_burn` (its usual SKIP), `verify_twocore`, `verify_onebus`,
`make cf` all exit 0; `verify_modenames` reports "no module declares
mode_views" (the Makefile's SKIP); `verify_replaces` fails only on the
eight MIDI SCENES remixes whose submodule is not checked out
(pre-existing; `make check` stops there). `REMIX=tim make cf` boots and
plays the tone (`runs/tim`: 261.634 Hz).

## What does not work, and what is left for phase 2

- **The machine list has no SYNTH row.** The marker is the sample's file
  name; the QUICK ASSIGN / PLAYBACK SETUP lists show `SYNTH.wav` as a
  FLEX sample, and the main screen's track icon is F. A `SYNTH` row in
  SELECT MACHINE TYPE that stores FLEX + the slot is menu work on the
  handler at `0x40077b00` (`« MACHINE:%s`, `0x400b7222`; `SELECT MACHINE
  TYPE` `0x400b7286`), not attempted here.
- **A sample must be loaded in the slot** (any WAV): the file's length is
  the note's maximum unless LOOP is on; its audio is discarded. The name
  test is `SYNTH` at the start of the basename, case-sensitive.
- **Every note restarts the sine at phase 0**, as a sample restarts at its
  start — a small click on a retrigger of a sounding note (1 dB in a
  10 ms bin). Phase 2 can choose phase continuity per voice.
- **The level is fixed** (−6 dBFS source; −24 dBFS at the fixture's
  track/main levels): no VOL of its own; the slot GAIN, AMP VOL and LEVEL
  apply as for a sample.
- **RATE and timestretch apply as to a sample**: RATE scales the pitch;
  the timestretch modes reposition the source stream (harmless for a
  sine, untested for BEAT grains). RTRG/RTIM retrigs work through the
  stock sub-segments (each sub-segment restarts the source read, our
  phase runs on).
- **Cave room**: 812 B left in `tim`; a bigger voice or a larger sine
  table wants the DRAM platform unit (`Linked(dram=True)`, 10 MB) or the
  second zero run (`0x400d24d0`, 2,064 B). The per-frame parameter record
  `fp = 0x80000510 + ping·384 + 48·track` (halfword `[0]` RATE, `[5]`
  PTCH, the rest inferred) is where phase 2 reads its RATIO/INDEX from the
  FLEX page's remaining slots (STRT/LEN/RTRG/RTIM — p-lockable, scene- and
  LFO-able for free) — relabelling them per slot means a clone of the
  FLEX descriptor (`0x400d31ae`) and one poke of `0x400d5f3c`, which is
  where (A)'s descriptor half becomes cheap.
- **Emulation only**: T1/T2/T5/T6 (DSP positions 0/1) are silent in
  emulation (pre-existing, O21), so the measurements use T8. Not flashed.
- The boot A/B never plays a SYNTH slot (the battery has none); the
  per-frame cost of the wrapper on non-synth FLEX tracks (21–25
  instructions a call) is the only thing a stock project pays.

Background: `docs/firmware/DSP.md` §6c (the frame transfer),
`docs/firmware/COLDFIRE_PORT.md` O10/O21/O23 (the record's audio, the
DSP's join and mixdown), `tools/panel/README.md` (loading samples,
parameter locks), `modules/direct-jump/README.md` and
`modules/quantizer/README.md` (the sibling caves).
