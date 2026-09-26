# CONTEXT.md — how to pick this project back up

Written 12 Sep 2026 by Claude (Fable 5.1) for Tim Hastie, so a fresh session
can recover the state of the work without the original conversation.
Read this first, then `tools/panel/README.md`, `tools/panel/KEYMAP.md`,
`tools/panel/PANEL_LINK.md`, `docs/firmware/COLDFIRE_PORT.md` (O14i/O14j).

## What this repository is

A fork of `sambanks/octabam` (an Elektron Octatrack OS "remixer": community
firmware mods composed into one image from the user's own 1.40C OS, plus two
emulators of the unit) on branch **`panel-ui`**, carrying Tim's additions:

- **`tools/panel/` — a virtual front panel.** A clickable Octatrack in a
  browser or a native macOS app (`tools/panel/app/`, `out/Virtual Panel.app`)
  that drives the *real firmware* under an emulator: the LCD is decoded from
  the firmware's own CPU→panel UART stream (`panel_link.py`), keys/knobs are
  injected as the panel's matrix reports on that UART, LEDs come back the
  same way. 47 keys / 7 encoders / 40 LEDs measured (`key_map.json`).
- **`tools/emu/ot_emu` additions** (Sam's C++ ColdFire port): `--interactive`
  line protocol over pipes, an RTC on the DSPI (`--rtc host|off|<epoch>`),
  the MCF5445x DMA-timer block (LED countdowns, UI/sys ticks), instrument
  commands. Batch behaviour byte-identical (except sample stamps once the
  timers run).
- `tools/verify/verify_ccpage2.py` fix (branch `fix-verify-ccpage2`).

This fork lives at **https://github.com/timhastie/octa-panel** (remote `tim`,
default branch `panel-ui`; push with `git push tim panel-ui`). Upstream is
`sambanks/octabam` (remote `origin`).

Tim has **no Octatrack to hand**; everything is validated in emulation. Real
projects saved on units come from public test fixtures (see README §"No unit
to hand"): `out/_projects/otlive/OTLIVE/{PROJECT,AUDIO}` is the default.

## How to run it

```sh
cd ~/Downloads/octabam && export PATH=/opt/homebrew/bin:$PATH
# native app (spawns the server; File > Open Project…):
bash tools/panel/app/build.sh && open "out/Virtual Panel.app"
# or the server alone, then http://localhost:8563/
.venv/bin/python3 tools/panel/panel_server.py --project out/_projects/otlive/OTLIVE/PROJECT --set OTLIVE --name PROJECT
```
Boot + fixture load ≈ 40 s on the port backend (the page dims with a phase
note). The SET DATE/TIME dialog is closed automatically with YES (the server
waits for the dialog's popup geometry before pressing; `/status clock` says
what happened).

**Samples** (12 Sep 2026): click the COMPACT FLASH slot on the rear edge (or
the AUDIO POOL button, or in the app File ▸ Add Samples to Card…, or drop
files on the window/Dock icon) → files go into the per-port pool
`out/_panel_pool_<port>/` (converted with `afconvert` to 16-bit 44.1 kHz WAV
when the unit could not read them) → **RE-INSERT CARD** rebuilds the card
image and reboots the unit (~40 s). Then on the unit, as on the hardware:
**double-click a track key** (T1–T8) → the sample slot list → ▲/▼ pick a
slot → ▶ (or YES) opens the file browser → ▲/▼ find the file → YES loads
it → NO leaves. README §"Loading samples" has the measured sequence and the
endpoints (`/samples`, `/samples/add`, `/samples/upload`, `/samples/commit`,
`/tap`).

**Sound** (12 Sep 2026, commit 1e76ac5): the server starts the port child
with `--dsp` by default (`--sound off` to skip the cores: faster, silent),
drains the child's main L/R (`audio start main` / `audio read`, O14k) into
a 180 s ring and, from PLAY to STOP, into a **take**
`out/_panel_takes_<port>/take-NNN.wav`. In the page, **click the HEAD-PHONES
jack** to listen (WebAudio; the VOLUME pot is the monitor's gain, the meter
beside the jack shows level), the MONITOR drawer lists the takes (REPLAY at
real time, SAVE) and has SOUND ON/OFF; in the app, Audio ▸ Save Main Out
Recording… / Show Takes Folder / Sound. Endpoints `/audio/status`,
`/audio/pcm?from&max`, `/audio.wav?take=N|?from&to`, `/audio/enable?on=`.
Honest limit: with the cores the unit plays ~9× slower than real time
(~110 emulated ms per wall s; ~350 without), so live listening is behind
and in bursts; a take replays at real time. The OTLIVE fixture itself
clips (slots 1/2 loop at GAIN 75/72); `out/_agents/audio/tree2` is the
clean reference (third-0.wav ×0.70).
**The card persists** (13 Sep 2026, milestone O19 in COLDFIRE_PORT.md):
`--card <file.img>` boots that image as it is with the child's write-back
on (`ot_emu --card-rw`: WRITE SECTORS are `pwrite()`n to the file as they
complete; `card flush` / `card status` on the pipe), so the unit's own
SAVE PROJECT (FUNC+MIXER, RIGHT, DOWN, YES, YES: 22,752 sectors into the
file on the OTLIVE fixture) and the samples put on the card survive a quit
-- the file is the CF card. A missing image is created once from
`--project` with a sidecar `<file.img>.json` (set/project names; the
firmware does not reload its last project by itself in emulation, tested);
the pool is `<file.img>.pool/`; RE-INSERT copies the pool onto the card
through an `hdiutil attach` while the child is stopped (no rebuild);
`/card/eject` mounts the card on the Mac for Finder, `/card/insert` boots
it again; `/samples` reads the image's AUDIO folder directly (`Fat16Image`
in the server). The app: File ▸ New Card from Project… (→
`out/cards/<Set>-<Project>.img`, remembered in UserDefaults `cardPath`),
Open Card…, Show Card in Finder, Eject/Insert Card; `VIRTUAL_PANEL_CARD`,
`VIRTUAL_PANEL_PORT_BIN`. Without `--card` everything is as before (fresh
per-port image; the oracle stays 28/28). Verification and numbers:
`out/_agents/persist/` (`verify.py`, `verify.json`).
Backends: `--backend auto|port|routea` (port = `out/emu/ot_emu --interactive`,
built when missing with `cmake --fresh -B out/emu -S tools/emu/ot_emu &&
cmake --build out/emu -j8`; routea = `tools/emu/emu_rtos.py`, 100× slower).

Machine setup that was needed (Tim's M5 Mac, macOS 26): native arm64 Homebrew
at /opt/homebrew (an old Intel brew lives at /usr/local — keep /opt/homebrew
first in PATH), CLT updated, `uv` venv on **aarch64** CPython
(`uv sync --extra emu --python cpython-3.13-macos-aarch64-none`), the
EMAC-fixed Unicorn (`scripts/build_unicorn.sh`), `vendor/dsp56300` pinned to
`3c01813f` (octabam's patch does not apply to upstream HEAD),
`vendor/elektron-firmware-tool` at upstream HEAD (its patch is upstreamed).

## Hard-won facts (all measured in emulation)

- Firmware image loads at **0x40000400** (`emu_bringup.BASE`); disassemble with
  `m68k-elf-objdump -D -b binary -m m68k:cfv4e --adjust-vma=0x40000400
  out/raw/section_3_MAIN_OS.bin`.
- **Panel link** = UART@0xfc064000. CPU→panel: `0x1p <col> <8B>` LCD blocks
  (page = op&7, page 7 on top, bit 0 top pixel), `0x2r <mask>` LED rows,
  `0x3n <id>` LED levels, `0x43` hello. Panel→CPU: `<row> <bitmask>` key
  matrix (set bit = held): trigs rows 0x20/0x21, tracks 0x22, TEMPO 0x23.0,
  scenes 0x23.1/2, page keys 0x24.2-6, STOP 0x24.7, arrows down 0x24.0 /
  right 0x24.1 / up 0x26.3 / left 0x26.4, PLAY 0x25.0, REC 0x25.1, CUE
  0x25.2, FUNC 0x25.5, PATTERN 0x25.6, BANK 0x25.7, MIXER 0x26.0, YES 0x26.1,
  NO 0x26.2, MIDI 0x26.5; encoders rows 0x30-0x35 = A-F, 0x36 = LEVEL, second
  byte = signed detent delta.
- **RTC** = DS1390-style SPI chip on DSPI chip-select 2 (regs 0x01 sec…0x07
  year BCD, 0x04 weekday 1=Mon, 0x0e status; `<reg> 00` CONT-delimited).
  YES on the boot dialog stores a 7-byte record at RAM 0x80000080 and closes
  it; "LAST SET" stays 0000-00-00 (source unknown; possibly card-side).
- **Sequencer:** pattern byte `+84 + 2330*t` is **PLAYS FREE**, not "active" —
  the PLAY key (`FW_TRANSPORT`, 0x4009bc76) only sets up tracks whose byte is
  0; setting it silences them (the old `activate_tracks` mistake). The "trigs
  fired" note is sys command 22 (handler 0x400622da); the trig-row running
  light follows the UI's current track (0x100b14cc). LED flashes need the DMA
  timer 1 interrupt (vector 0x61, 120 Hz) — now modelled in the port.
- **SETUP pages** (2nd press of a page key): encoders edit the Part bytes but
  the window does not redraw until closed and reopened; their enum editor
  accumulates detents and runs backwards past ±9 (server chunks to ±7).
- **Speed (12 Sep 2026, milestones O15a–O15f in COLDFIRE_PORT.md):** the port
  was 0.22× real time because it stepped ONE instruction at a time with
  bookkeeping around each (Intc::top/deliver 43 %, timers 9 %, pc() 6 %;
  Musashi itself < 5 %). Now: event-horizon bursts (run to the next timer /
  frame / DMA / peripheral event, exact tail), LTO, a 4 KB page table for
  memory, opt-in PGO (`bash tools/emu/ot_emu/pgo.sh` → out/emu/ot_emu, +28 %;
  a plain cmake build is LTO-only), bursts through boot/load/batch, and
  wall-clock pacing (`pace on [rate]`, `run <ms> wall <s>`, `pacestatus`;
  the server sends `pace on 1` and shows `rt` = × real time in /status and
  the page). Measured: **1.47–1.6× real time without the cores** (paced to
  1.00; LED chase 125.0 ms per 16th by wall clock), **0.15× with --dsp**
  (the two DSP interpreters are ~85 % of that wall; exact interleave keeps
  audio bit-identical — see O12), boot to ready 5.4 s (was 39), 23 s with
  --dsp (was 57). THE GATE for any core change is in the repo:
  `tools/emu/ot_emu/oracle/oracle.sh <ref> <cand> --build-dir <tree>` = 28
  byte-identical checks (README beside it; cache/reports under out/_oracle/),
  and `tools/emu/ot_emu/oracle/phase_b.sh <cand>` = the Phase B contract
  against BOTH frozen references (audio within --audio-tol 2 / --wav-tol 8 /
  --audio-frac 0.5, everything else strict). Frozen references (untracked,
  keep): out/emu/ot_emu.ref-1e76ac5 (pre-speed) and out/emu/ot_emu.ref-73c2815
  (end of Phase A, PGO).
  Speed bench: `OT_EMU=<bin> .venv/bin/python3 out/_agents/speed/bench.py
  <tag> [--dsp [--dsp-rt]]` (macOS `sample` hangs on the rt process: put
  out/_agents/jit-verify/stubbin on PATH). Diagnostics: OT_BURST=0 (old loop), OT_STEPFAST=0,
  OT_BURST_STATS=1. Never enable `rt.exact_clock()` on route A (100×).
- LCD framebuffer in RAM (0x460d1f80) is NOT what the screen shows (page
  order rotates) — always render from the UART stream.
- **Popups** are drawn from the record at 0x46c7d34c while the UI slot
  0x460d175c points at it (+0x08 x0, +0x0c y0, +0x18 x1, +0x20 flags 0x21
  open, +0x28 rows). Geometry tells them apart: clock dialog 0xf/7/0xe6/0x32,
  page SETUP windows 7/0/0xf4/0x40, ARM ALL 0x25/0x17/0xb8/0x12 (the rest in
  panel_server.py). YES/NO on the bare main screen open ARM ALL/DISARM ALL.
- **Double-tap chords** (a track key twice → its sample slot list) are timed
  in firmware time: ~200 emulated ms press to press lands, 375 does not. The
  server slows its idle pump for 0.5 s after a track key is released so two
  page clicks land (0.15 and 0.30 s wall apart open the list, 0.45 s does
  not); `/tap?row&bit&n=2` does it as one action for scripts. A third double
  tap closes the list again.
- The idle pump runs ~0.8× real time (25 emulated ms per ~30 ms wall);
  `/status speed` reads high while idle because idle runs return early.

- **Recording into a DAW (13 Sep 2026, commit 899425b):** Audio ▸ Output
  Device sends MAIN L/R to channels 1-2 and CUE L/R to 3-4 of any CoreAudio
  device (BlackHole etc.) via sounddevice; `/audio/devices`, `/audio/output`.

- **Panel UI (13 Sep 2026 late):** the top bar is collapsible (chevron tab;
  hidden = the stage moves to the top) and holds only the status pills, the
  phase text and SOUND (the drawer = SOUND ON/OFF); MAP KEYS/EXPORT MAP/RUN
  buttons and map mode are gone (key_map.json + BUILTIN are the map); the
  AUDIO POOL drawer opens from the COMPACT FLASH slot; the headphones jack
  + VOLUME pot are the in-app monitor, takes are saved from the app's Audio
  menu. Encoder PUSH = key-matrix row 0x27, bit = encoder (A-F 0-5, LEVEL
  6): `/knob/press?row=` — a double-click on an encoder while a trig is
  held (Shift-latched) sends it, which removes that step's parameter lock
  (manual 12.5); a push on an unlocked parameter sets a lock (firmware
  toggle). Rear edge shows only HEAD-PHONES, MAIN/CUE OUT, INPUT A B/C D
  and the card slot.

- **Effects fidelity + firmware modules (13-14 Sep 2026, O20-O23):** the
  time-based effects were silent/wrong in BOTH DSP modes until three
  emulator defects were fixed: memory-to-memory eDMA moved no data (the
  Echo Freeze delay's ring), the TCD ATTR/SOFF fields were read swapped,
  and the EMAC -1x-1 product overflowed; then two JIT defects (MPYI/MACI
  immediate sign; bset/bclr on an M register corrupting its modulo mask)
  made --dsp-rt match the interpreter (clean/chorus/comb bit-identical).
  Known: MACRI is a no-op in both engines (chorus/phaser/flanger at
  P:0x779) -- implementing it will change those modules' output; the
  flex-slot-1/2 tracks (T5/T6 of OTLIVE) are silent in emulation. The
  effects rig: out/_agents/fx2 (cards per effect on T7, render.py in both
  modes, wet.py). Per-track outputs (O23): stems tapped at P:0x2d5 from
  X:$204+32k, `audio start tracks`, output map main 1-2 / cue 3-4 /
  tracks 5-20. FIRMWARE MODULES (source only, never flashed):
  modules/direct-jump (CHAIN AFTER = DIRECT), modules/quantizer (SCALE
  row, #SEQUENCER_SCALE=n), remixes/tim.py = both; build with
  `PATH=.venv/bin:$PATH REMIX=tim make cf` -> out/mainos_cf.bin (the
  build script needs Python >= 3.12); boot it in the app with File > Open
  Firmware Image.... `make cf` (15 Sep 2026) is the ColdFire-only build:
  the modules' caves/detours/pokes on the stock OS with both DSP payloads
  and the FX2 chooser byte-identical to stock. `REMIX=tim make bus` also
  builds but rebuilds the chooser around a remix with no rows, so the unit
  offers NONE as the only EFFECT 2 effect (the owner's report).

- **SYNTH machine (22-23 Sep 2026, modules/synth, commits 588799d/dc9d2f4/
  5866a90):** a FLEX track whose loaded sample is named SYNTH*.wav is a
  two-operator FM synth (a ColdFire cave generates the source samples the
  packer 0x4000d3fc ships each frame: the kind-table FLEX entry 0x400d6438
  is poked to it); PLAYBACK page = PTCH, RATO (STRT), INDX (LEN), RATE,
  FDBK (RTRG), DEC (RTIM), with icons and FM SYNTH in the footer via a
  detour at the page resolver 0x40031ece; the DSP envelope/FX/locks/scenes
  apply as to a sample. Param record fp = 0x80000510+384*ping+48*t
  ([0] PTCH [1] STRT [2] LEN [3] RATE [4] RTRG [5] RTIM). Build with the
  tim remix (`REMIX=tim make cf`, 4,010 bytes changed, 172 B of cave left).
  O24: the voice-start "burst" was the OTLIVE fixture's markers trims
  (slots 1/2 = 0..64 frames), not the emulator (tools/hw/ot_project.py
  trims/trim). A SYNTH.wav marker file (2 s of silence) lives in the rig
  at out/_agents/synth/audio/.
  23 Sep 2026: plain icons (no letters, two columns clear of the dividers), DEC prints the ms alone (`598`, `1.1s`, `HOLD`), the always-show nibble moved from RATE to FDBK, the STRT-LEN / RTRG-RTIM arches (nibble bit 1) cleared, no icon in the compact CHROMATIC/SLOTS layout (widget flags bit 1); `out/mainos_cf.bin` rebuilt (4,000 B changed).
  **Manual [TRIG] trigs (23 Sep 2026, resolved):** two things at once. (1) The
  panel server ran frame mode only from PLAY to STOP, and the DSP frame
  interrupt runs the firmware's frame builder (0x4000b2ee..), the only
  consumer of the trig mailbox 0x46c80354 that a [TRIG] key posts through
  0x40005030 -> 0x4000515c; with it off the press's 0x1d sat in the mailbox
  until the release overwrote it with the note-off 0x40 and no voice started
  (mailbox[4] = 0x40 through /peek, voice struct idle, exact digital
  silence). Fix in panel_server.py: `frame_always` -- with the cores the port
  keeps frame mode on from boot (the unit never masks it); `playing` is
  PLAY..STOP for the pump rate and the take; /status reports both. (2) The
  key map of TRACKS mode: the firmware's key split at 0x40044584 sends
  [TRIG 1-8] to the recorder state machine (recorder trigs, as the manual
  says) and [TRIG 9-16] to the sample trig of tracks 1-8 (0x4004476a, gated
  on 0x8000004c bit 0 and 0x80000012 == 0), so [TRIG 7] never plays T7;
  [TRIG 15] does (-38 dBFS, third-0), and [TRIG 13] plays T5's clipping loop
  for ever (-11.9 dBFS, the "constant tone" of the earlier direct-drive runs).
  CHROMATIC (FUNC+DOWN held ~0.5 s) pitches correctly: -4 st = 0.794x,
  -12 st = 0.5x; T2's FM synth (tim image) -22.7 dBFS at every key.
  QUANTIZED TRIG (+0x129) plays no part while stopped: 0x800065b8 == 0 posts
  the mailbox directly.

## Repo / process rules that matter

- **No Elektron bytes in git**: `out/`, `downloads/`, `vendor/`, `.venv/` are
  ignored; never commit an image, `.syx`, `.bin` or a built remix.
- Nothing has been posted publicly (no PRs, no Discord posts). Two branches are
  PR material for `sambanks/octabam`: `panel-ui`, `fix-verify-ccpage2` — only
  with Tim's explicit go-ahead.
- Others' agents drive the emulator CLIs headless: keep CLI contracts.
- Octahackers Discord (read-only sweep 11 Sep 2026, dumps in
  `out/_agents/discord/`): nobody else has a panel/LED/key map; Sam built
  ot_emu for headless batch use; Elektronauts thread is closed.

## Open items

- "LAST SET" record source; SETUP-page redraw; crossfader input path; encoder
  push (not found in the matrix); trig LEDs 9-16 colour vs hardware.
- Upstream octabam main has moved (recfix, PR #97/#129, Workbench); this fork
  is a 10 Sep clone — merging upstream is pending.
- **Sound in real time (13 Sep 2026, O17/O17b, commits 15c54ac/02ace27):**
  `--dsp-rt` runs both DSP56303 cores under the vendored dsp56300 JIT on
  their own threads as WORKERS ON THE LOCKSTEP SCHEDULE — the ColdFire stays
  master of emulated time and books DSP ticks; the workers run up to a
  frame ahead but never past the due count; the one timing-assumptive point
  of the firmware's frame protocol (core 0's bank-word write at P:0x73, read
  by the handler with no ready check) is FENCED until the ColdFire has
  finished the previous frame. Five JIT/library fixes + lock-free HDI08
  rings + burst DMA live in tools/patches/dsp56300.patch (applies to the
  pinned 3c01813f). Free-running cores were proven a dead end (the protocol
  halts on a few samples of jitter). The panel spawns `--dsp-rt` with sound
  on (fallback to lockstep `--dsp` with a note). Measured on the PGO binary:
  **1208 emulated ms per wall s flat out with sound, paced 1.000× in the
  panel (90 s PLAY, dropped 0), boot 7 s**; old modes byte-identical (strict
  oracle 28/28). Audio vs the lockstep interpreter: O17c (13 Sep 2026)
  made it THE SAME SAMPLES on the clean fixture — three runs bit-identical
  to the lockstep capture, fit −33.0 dB — where O17b had "the same music,
  ~1 dB quieter, not the same samples": six timing mechanisms fixed (the
  DSP's clock at its waits, the exact boot, the ISR's drain times;
  COLDFIRE_PORT.md O17c). `OT_RT_POLLLEAD` is 0 now, `OT_RT_BOOTEXACT` /
  `OT_RT_DRAINPACE` are new knobs, `OT_DSP_FRAMETRACE=1` the frame
  timeline. Diagnostics: `rtstatus`, `cfstatus`,
  OT_RT_LEAD / OT_RT_FENCE / OT_RT_DOITER knobs; stalls are hunted with
  out/_agents/rt-fence-build/stall_hunt.py. The fence/poll addresses are
  payload A's (P:0x73/0x97/0x4b). Cue out and core 1 are not captured; the
  crossfader and audio inputs have no panel path.
- `.ot` slice files are not seeded onto the card with their samples. Without
  `--card` the pool is still wiped and re-seeded at every server start
  (files added through the page survive only while that server runs, or if
  they are also in an `--audio <dir>`) -- with `--card` they persist.
- The firmware's file browser lists files in card order, not name order.
- Memory: the child's RSS is flat during play since O18 (13 Sep 2026: the
  peripheral-write seed log was unbounded); the panel UART tx buffer still
  grows ~0.7 MB per hour (needs a cursor change in main.cpp).

- **23 Sep 2026, first hardware candidate: OCTATRICK1 = DIRECT JUMP + SCALE QUANTIZER** (Tim named the firmware Octatrick; `remixes/jumpquant.py`; `BUILD=1 VERSION=OCTATRICK1 REMIX=jumpquant make image-cf`, outputs renamed): `out/OCTATRACK_OCTATRICK1.bin` (card path, sha256 see below) / `out/OCTATRACK_OS1.40C_OCTATRICK1.syx` (MIDI path), copy of the OS section at `out/mainos_octatrick1.bin`. The container's version field is exactly 10 bytes (`ELEK0178` + 10), so `OCTATRICK1` is the longest form: next builds are OCTATRICK2.. 9, then shorten. The OS section is byte-identical to the BUILD 81 build verified below (only the version field differs). Verified before hand-over: the official .syx holds ONE section (id 3 MAIN OS; no bootstrap in the file), the packer round-trips it byte-identically, ours has the same single section with checksums ok and section 3 == the build; 1,657 bytes differ from stock at 36 sites, highest 0x400d7490 (< SAFE_CAVE_CEIL), none in the DSP payloads; the container version field reads ` OCTABAM80`-style (10 chars, the tool's report prints `?` for it, upstream's builds show it in SYSTEM STATUS). Emulator: boots, PLAY -25.9 dBFS, T7 [TRIG 15] -35.8, CHAIN AFTER reaches DIRECT and clamps, SCALE OFF -> PHRYGN -> OFF. **FLASHED by Tim on 23 Sep 2026 (the project's first hardware flash of these modules): he reports DIRECT JUMP and the SCALE QUANTIZER working on his unit.** (BUILD 80, quantizer alone, was built and discarded the same evening.)
- **23 Sep 2026, OCTATRICK2 = DIRECT JUMP + SCALE QUANTIZER + SYNTH MACHINE** (`BUILD=2 VERSION=OCTATRICK2 REMIX=tim make image-cf`, outputs renamed): `out/OCTATRACK_OCTATRICK2.bin` / `out/OCTATRACK_OS1.40C_OCTATRICK2.syx`, OS section copy `out/mainos_octatrick2.bin` == `out/mainos_cf.bin` (the tim build the app runs). Container: one section, checksums ok, 4,000 bytes differ from stock, 0x40031ece..0x400d7b8f, 28 hook sites outside the cave region. Copied with SYNTH.wav (the 4 s silent marker sample) to `~/Desktop/Octatrick/`. Emulator smoke on the packaged section: see the session log (PLAY, synth page, FM trig, CHROMATIC pitches). Hand-over notes to Tim: put SYNTH.wav in the set's AUDIO folder, load it into a FLEX slot, assign it to a FLEX track; first things to check on hardware are dropouts with several synth tracks (CPU cost unmeasured on hardware) and the retrigger click at voice start (the emulator's fixture trims hid it). `out/mainos_cf.bin` is back to the full tim build.
- **23 Sep 2026, OCTATRICK2 ON HARDWARE:** flashed by Tim. Straight after the upgrade the sequencer ran step 1, reached step 2 and restarted every step, with any sample, FDBK/DEC 0 or not; a POWER CYCLE cleared it completely (docs/remixer/FLASHING.md 3a step 5: never judge before a reboot -- state survives an OS upgrade). After the reboot: sequencer, direct jump, quantizer and the FM synth all work on the unit. OPEN: the synth page's ICONS do not show on hardware -- the boxes show the four-letter names and the values only (the emulator shows the icons). Not yet diagnosed; candidates: the icon blit's data (pg_data, composed in the page cave) reads as zero on the unit, or something redraws the box; waiting for a photo and whether the stock PTCH/RATE dials still draw.
- **24 Sep 2026, DIRECT = index 1 + GLIDE/legato (emulator only, not flashed; working tree left uncommitted for Tim):**
  (a) `modules/direct-jump`: DIRECT is now CHAIN AFTER's unused value 1 (stock's setter skipped it at 0x40065a22, its loader bumped 1 -> 2 at 0x40087820): the two 18-entry tables, the 8 `lea` repoints and the 6 clamp pokes are gone; instead four fixed pokes -- the stock step table entry 1 `0x400d80e0` 1 -> -1 (every reader sees PAT.LEN), the label entry `0x400b27ec` -> "DIRECT" 0x400b6912, `0x40065a26` bne -> bra, `0x40087826` bne -> bra -- and `dj_queue` tests `#1`. Cave 358 B (PINNED regenerated). Menu order PAT.LEN, DIRECT, 2/16 ... 256/16. MIGRATION: a project saved by OCTATRICK1..3 with DIRECT (=17) loads as 256/16 (stock clamp) -- set DIRECT again and save; stock firmware loads a saved 1 as 2/16. The per-pattern setter (0x40081d74) still skips 1 (DIRECT stays project-level; one poke + a label if wanted); MIDI program change, [PATTERN]/[BANK]+[TRIG] and the arranger all go through the hooked setter, none reads the index.
  (b) `modules/quantizer`: fifth SEQUENCER row GLIDE (OFF, 1..127; count poke 3 -> 5; `#SYNTH_GLIDE=n` saved after `#SEQUENCER_SCALE`), the byte PINNED at 0x400d2cdc (`glide.s`, the tail of the second zero run) because the position-independent synth cave reads it as an absolute; one accessor per unit (`qz_glide_of`, `sy_glide`). Legato: two jmp detours in the CHROMATIC key handler (0x4004fbfe `qz_leg1`, 0x4004fc94 `qz_leg2`): with GLIDE on, a key pressed while another is held on the track takes stock's FUNC+key trigless path (mailbox |= 0x119, no voice start), the old key's voice note-off (mailbox 0x40 -> AMP release) is suppressed (its MIDI note-off still goes out), the new key becomes the held key (0x460d171d + track) so releasing the first key does nothing and the last release ends the note. Unit 1,484 B (branches now `jb<cc>`, short where they reach).
  (c) `modules/synth`: `sy_slew` -- a per-track current PTCH word (S_CUR, Q12, state 44 B/track) lagged toward the record's word once a frame, tau = 10 ms * 100^((g-1)/126) (10 ms at 1, 100 ms at 64, 1 s at 127; k from the stock 2^x table 0x400aa294), the stock rate arithmetic then runs on the slewed word; a voice start snaps. Cave 1,892 B (PINNED regenerated; sy_tab at +0x400 by .balign). `REMIX=tim make cf`: 4,301 B changed, 168 B of the third run left (caves 0x400d6b80 / 0x400d6d00 / qz 0x400d7480 / tables 0x400d7a80..7b90), the page cave untouched.
  MEASURED (panel 8593, a copy of the OTLIVE card, T2 = SYNTH): GLIDE 64 legato C4 -> +2 st (MIXOLYD snapped +3): t63/t95 126/326 ms; GLIDE 1: ~40 ms; 127: 947/2,387 ms; OFF: a step (the stock restart); a single key starts at its own pitch; releasing the first key keeps the second's pitch, releasing the last releases; the AMP envelope is NOT retriggered (ATK 64 test) -- but a 2.3 dB / 200 ms level step follows every trig word on this track (stock FUNC+key too; the FX1 FILTER envelope suspected, unresolved); a sequenced trigless trig with PTCH +12 glides 130.8 -> 523 Hz with the same 120/320 ms. DIRECT: selection during step ~11 -> the next step, `0x80006628` = 12, LEDs/counter continue 13, 14, 15; PAT.LEN switches at the end. Persistence: SYNC TO CARD writes `PATTERN_CHANGE_CHAIN_BEHAVIOR=1` / `#SYNTH_GLIDE=64`; eject/insert reloads them; file edits 17 -> 256/16, 1 -> DIRECT, 5 -> 6/16, glide 100/33/0.
  AMP SLOT F (XVOL) -- looked at for a later chord parameter, NOT enabled: a scene-only MIN/MAX switch (nibble 8, knob handler 0x40032ba4, formatter MIN/MAX); the crossfader morph reads a separate A/B array 0x800010d4 with 0x7f00 standing in for "no scene lock" -- the Part byte is never read by the morph; the packer does copy the Part byte / a step lock on flat slot 17 into the DSP AMP word 5 (whether the DSP uses it: untraced); the knob handler and the CC/scene writer refuse a slot whose nibble bit 0 is clear. Details in modules/synth/README.md "AMP slot F".
  TEST-RIG GOTCHAS: PATTERN SETTINGS = FUNC+BANK, the focus starts in the LEFT column (PAT/T1..T8; DOWN there changes the track) -- press RIGHT to enter the rows, then DOWN x3 = USE PRJ SET., YES toggles; `/card/eject` is asynchronous, poll `/status` `phase` for "mounted at <path>"; twice a chromatic trig within ~1 min of a boot on this card produced a stuck 3,974 Hz tone (44100/11: the DSP looping stale samples, the FLEX slot presumably still loading) -- wait a minute after a boot before trusting audio; T2's Part STRT 21 / LEN 0 is a degenerate region (the same tone). The card copy's pattern A01 now has T3 PLAYS FREE on and USE PRJ SET. on A01 (test leftovers).
- **24 Sep 2026, OCTATRICK3** (`BUILD=3 VERSION=OCTATRICK3 REMIX=tim make image-cf`): the synth wrapper now scans a voice's sample name only when the settings-record pointer lies inside the settings table (0x100b14f0 + 136 x 0x448); after power-on the unit's RAM is garbage and a refused voice start leaves the old pointer, so a wild scan could fault the audio interrupt (the emulator's RAM is zero, so it never showed). Cave 1,700 B (tables moved to +0x360 / +0x564), 4,013 bytes changed. Built because the unit had two 'bad boots' out of a handful (project came up with STATIC machines, sequencer stuck on step 1, fixed by another reboot) -- cause NOT identified; this removes the one wild read in the boot/trig path. Files in ~/Desktop/Octatrick/. Stack depth measured in the emulator with the frame interrupt on: the audio ISR reaches at most ~420 B into any task stack (2 KB smallest), the wrapper adds 68 B -- stack overflow ruled out.
- **24 Sep 2026, OCTATRICK4** (`BUILD=4 VERSION=OCTATRICK4 REMIX=tim make image-cf`): DIRECT at CHAIN AFTER index 1 (option 2 of the list), GLIDE row (fifth SEQUENCER row, #SYNTH_GLIDE) with 303-style legato in CHROMATIC mode, plus everything in OCTATRICK3. Verified on a fresh emulator boot by me (menu order, glide trajectory 260->320 Hz over ~400 ms at GLIDE 64, no retrigger, OFF retriggers, early/late trig probes clean, PLAY -20 dBFS). Files in ~/Desktop/Octatrick/. Migration: a project saved with DIRECT under OCTATRICK1-3 (value 17) loads as 256/16 once. NOT flashed yet. Agent-reported oddity not reproduced by me: twice a chromatic trig within a minute of boot gave a stuck 3,974 Hz tone in the emulator.
- **24 Sep 2026, OCTATRICK5** (`BUILD=5 VERSION=OCTATRICK5 REMIX=tim make image-cf`): the paraphonic build -- POLY row, four voices per synth track, CHRD (DEP3 byte) and VOIC (SPD3 byte) on a cloned LFO page, chords snapped to SCALE, the engine as a DRAM unit in the 10 MB platform reserve (FREE MEM 53.5). The OS section is 1,115,838 B (3,278 B appended: loader + packed runtime) -- the FIRST of Tim's flashes to carry the loader; the same mechanism midi-scenes users flashed. Verified on a fresh emulator boot by me (see the commit). NOT flashed yet. Rule change to know: LFO 3 is muted on synth tracks in BOTH modes (a leftover CHRD value in DEP3 became a pitch LFO with POLY off; one-line revert noted in the synth README). (SUPERSEDED the same day, uncommitted: the POLY row, its project line and byte are gone -- VOIC is the switch, the entry above.)
- **24 Sep 2026, OCTATRICK6** (`BUILD=6 VERSION=OCTATRICK6 REMIX=tim make image-cf`): supersedes OCTATRICK5 (removed from the Desktop folder, never flashed): no POLY row -- VOIC 1..4 in the synth track's LFO page is the switch (1 = the mono synth, default), CHRD next to it, LFO 3 muted on synth tracks, chords snap to SCALE, two safety nets. Verified by me through the sequencer (see the commit). NOT flashed yet. Test-rig lesson: FUNCTION+DOWN/UP through /key steps the trig mode erratically (0..3 steps per press) and the byte 0x460d16f0 did not reflect the mode for me -- verify CHROMATIC from the screen, or test chords through a sequencer trig.
- **24 Sep 2026, OCTATRICK7** (`BUILD=7 VERSION=OCTATRICK7 REMIX=tim make image-cf`): OCTATRICK6 + legato presses recorded as trigless trigs in live recording (qz_leg3 at 0x4004fce0), so recorded acid slides glide on playback. Replaces OCTATRICK6 in ~/Desktop/Octatrick/ (6 never flashed). NOT flashed yet.
- **25 Sep 2026, OCTATRICK8** (`BUILD=8 VERSION=OCTATRICK8 REMIX=tim make image-cf`): OCTATRICK7 + live recording on synth tracks writes the played note length as an AMP HOLD lock (qz_leg4 at 0x4004fd06, the HOLD table 0x400d18d0, lock slot 13; poly voices gated by HOLD). Replaces OCTATRICK7 on the Desktop (7 was flashed by Tim and reported the drone). NOT flashed yet. 680 B of cave left.
- **25 Sep 2026, repositories split:** `timhastie/octa-panel` (this fork: panel + real-time emulator + modules) is PRIVATE at Tim's request while the Octatrack is a current Elektron product. The modules alone are PUBLIC at `timhastie/octatrick` = upstream octabam 9a49f21 + modules/{direct-jump,quantizer,synth} + the five remixes + Makefile cf/image-cf + build_bus.py/schema.py (Detour ptr) + MODULES/FLASHING docs + a modules-only README; module docs there had their virtual-panel references reworded to 'the emulator rig'. Local branch `octatrick` (remote `public`) tracks it: to publish a module change, cherry-pick or re-checkout the module paths onto `octatrick`, re-apply the wording scrub, build-check with `REMIX=tim make cf`, push to `public`.
- **24 Sep 2026, PARAPHONIC CHORDS + the synth engine in DRAM (emulator only, not flashed; the design revised the same day: VOIC is the switch, no project setting):**
  (a) `modules/synth/poly.s` is the FM voice engine as a DRAM unit (`Linked(dram=True)`, 7,512 B linked at the arena reserve's base 0x40a955e0; `synth.s` kept for the record, no longer built). `make cf` took the DRAM unit as it was: the platform's 1,707 pages (10 MB) come off the bottom of the audio page arena (base -> 0x41495de0, 75 MB left), the loader + packed runtime are appended (3,347 B at 0x4010fdf0), `verify_dram_boot` PASSES for tim (loader 1x, fatal 0x, the reserve == the linked runtime); the unit shows FREE MEM 53.5 MB in the FLEX slot list against 63.5 on stock. ONE build change: `Detour(kind="ptr")` (build_bus.py, schema.py, MODULES.md) rewrites a 4-byte stock pointer to a symbol -- the kind table's FLEX entry 0x400d6438 -> sy_render (a Poke's write is bytes fixed before the link). The third zero run has 1,704 B free (was 168).
  (b) A synth track's LFO page is ALWAYS a clone (a detour at the resolver's kind-1 load 0x40031e62, built from the stock descriptor at first use): slot 2 = VOIC (1..4, default 1, the stock enum stepper as knob handler ~4 detents a step; SPD3's byte, a stock/out-of-range byte reads 1), slot 5 = CHRD (32 shapes x 4 knob values, `po_shapes`/`po_names`; prints "----" at VOIC 1). LFO 3 is muted on a synth track whatever VOIC (detours at BOTH copies of the LFO engine's depth read, the routine 0x40003ca4 and the frame builder's inlined copy 0x4000d03e -- its default PMTR is PTCH, and a chord byte read as its depth was a slow pitch LFO: the same key gave a different pitch each press). VOIC 1 = the exact mono synth of OCTATRICK4 (the mono path, GLIDE legato, the stock lifecycle). VOIC 2..4, latched per note at the voice start from the current value (locks honoured): that many voices of the chord shape (root first, dropped from the top) at the PTCH word; per-voice index envelope, glide (target = V_ROOT + the PTCH delta since the voice's start) and amplitude (instant attack; sustain while the key is held or until the next trig; release tau = 5 ms * 1000^(REL/126), 127 = INF); free voice, else the oldest releasing, else the oldest sounding; each voice at 1/2, the sum saturated; every note snapped onto SCALE (`po_snap`, the mask through the quantizer's new accessor `qz_scale_mask` via `scale.s`, 6 B pinned at 0x400d2ca8; nearest degree, ties down). Live keys: the quantizer's handler hooks (`qz_leg0` new at 0x4004fbde on the release path, `qz_leg1/2` extended, gated on qz_is_synth + the Part's VOIC 2..4) keep a held-key mask and post each key through `keys.s` (pinned at 0x400d2cb0: qz_pkey[8], qz_pmask[8]); only the last key's release posts the AMP release; legato is off on such a track; non-synth tracks and VOIC 1 are stock. The stock lifecycle stays (a live key restarts the DSP voice: use AMP ATK 0 / HOLD INF, REL to taste).
  NOTE LENGTH (OCTATRICK7 hardware report, fixed in the tree, 25 Sep 2026): a recorded note droned for the whole AMP HOLD. Now a live-played note on a synth track is written as a HOLD lock (flat slot 13, the stock writer 0x40042158) on the step its press recorded: qz_leg4 at 0x4004fd06 notes the press (key, step, ticks), the release (qz_leg0) or the stock note-off (qz_leg1) writes it; HOLD's unit is sequencer steps through the firmware's table 0x400d18d0 (0.0078..128.0, INF; 1/128-step copies qz_hold128/po_hold128), rounded up (a 60 ms tap -> 0.5000). Time = the engine's clock po_clock (a monotonic count of (step 0x800065b2, tick 0x800065b6) changes, ticks a step = the largest tick + 1, frames a step), ticked once a frame from the frame builder's LFO pass (po_lfo3b, track 0) and published at KEYS_AT+40 (qz_clock). The DSP's HOLD runs from the voice START and a trigless step's HOLD re-lengthens it from there (measured), so a legato/FUNC chain gets its whole length on every step (qz_chain_of / qz_holdall). VOIC 2..4: a sequencer-started voice is gated for the step's HOLD (V_HOLD from the lock else the Part byte, frames a step measured). Measured: legato 13 (0.55 s) + 6 (to 1.2 s) -> both steps HOLD 9.50, playback ends at 1.25 s like the live take with the glide continuous; GLIDE off 4.0/5.75; a tap 0.5000 still sounds; HOLD 40 programmed = 0.40 s unchanged; a VOIC 3 chord held 0.5 s ends at 0.60 s (live 0.55). 680 B of cave left.
  LEGATO NOT RECORDED (OCTATRICK6 hardware report, fixed in the tree): the chromatic key press is handed to the live recorder at 0x4004fcd8.. -- FUNC held -> 0x4004271c (a trigless trig), else 0x40042d1c (a sample trig), then the PTCH lock (0x40042158); a legato press played trigless by qz_leg2 was recorded as a sample trig. Fix: qz_leg2 sets qz_legato on the legato path, a fourth detour qz_leg3 at 0x4004fce0 sends such a press down the trigless branch. Measured: the record gets the trigless bit (byte 15 of the track record) + the lock, playback glides 261.4 -> 252 218 200 189 183 180 178 177 Hz (the same numbers as FUNC + key), GLIDE off records two sample trigs and steps 261 -> 175 at once.
  STUCK TONE (reported as a release blocker, resolved): a constant 6,201.6 Hz tone at -11.9 dBFS "from the legato step onward" is T5's fixture loop -- in TRACKS mode [TRIG 13] is T5's sample trig and OTLIVE's T5 loops for ever (the 23 Sep note); the driver's blind FUNC+DOWN had not left the unit in CHROMATIC (the reporter's own screenshot with the tone shows the full LFO page layout, not the compact one). Peeked while stuck: T2's voice struct inactive, the engine idle, T5's active 0xff; FUNC+T5 mutes it (-90 dBFS), STOP ends it. Scripts must verify the trig mode by peeking 0x460d16f0. Safety nets added anyway (INC_MAX ~8 kHz guard on the mono and paraphonic increments; po_free at a mono start, a non-synth start and when the stock voice ends). With them: the lockstep VOIC 1 A/B vs OCTATRICK4 is still 0 samples differ (both fixtures, one-sample capture alignment), and the reproduction loop ran 10 fresh boots clean (the blind FUNC+DOWN slipped in 3 of them and was corrected by the peek).
  MEASURED (panel 8593, a copy of the OTLIVE card, T2 = SYNTH): VOIC 1 vs OCTATRICK4 on the lockstep synth8q rig: the sequencer fixture 120,090 frames and a pipe-driven CHROMATIC key sequence 233,954 frames, 0 samples differ at a one-sample capture alignment; VOIC 1 with CHRD 36 left in the slot: key 13 x5 = 261.6 x5 (the LFO 3 mute). Chords (key 13 = C4, CHRD MAJ): VOIC 2 = 261.6/329.6, VOIC 3-4 = 261.6/329.6/392.0 (1 : 1.26 : 1.498); PHRYGN: VOIC 2 = 261.6/311.1, 3-4 = 1 : 1.189 : 1.498; a sequencer trig with a CHRD MAJ lock at VOIC 3 = 130.8/164.8/196.0, the same step at VOIC 1 = 130.8 alone; two keys held at VOIC 2 = two notes; the earlier build's numbers (unchanged code): the 13/9/6 stack, REL 20 cuts within 0.1 s, REL 100 rings under the next key, LFO 3 on T7 untouched (4,718..5,520 Hz swing), persistence of VOIC/CHRD/PTCH locks across SYNC TO CARD + eject/insert, FM SYNTH page and PLAY (-19.7 dBFS) fine. CPU per frame (engine's own, both calls, T8): VOIC 1 832, 1 voice 1,172, 4 voices 3,304 mean / 3,956 max, against the stock renderer's 1,273 (418 + 855) inside. Build: out/mainos_cf.bin 1,115,907 B, 3,477 changed, the quantizer 1,888 B. Caveats: a four-note chord steals every voice; the DSP AMP envelope retriggers at each live key; the mode is latched per note; a refused key trig leaves qz_pkey set once; MIDI note-offs per key untested; hardware cost and the instruction cache over the arena reserve unmeasured. Scripts/screens: scratchpad `poly/` (voic_final.py, chromrig.py, chrom/, voicF/, cost/).
- **26 Sep 2026, OCTATRICK9 = the port onto upstream octabam main + USB MIDI/AUDIO (branch `upstream-port`, tag `octatrick9` = cc5d782, pushed to `tim`; panel-ui untouched, tag `octatrick8` = bd23952 stays the hardware fallback):** modules/{direct-jump,quantizer,synth} copied verbatim onto origin/main 0e93543 (pinned bytes unchanged); the fork's `Detour(kind="ptr")` is upstream's `SymbolRef` (synth manifest `symbol_refs=(SymbolRef(0x400d6438, 0x40004008, "poly", "sy_render"),)`), no build-tool changes; `remixes/octatrick.py` (three modules + 14 stock effects, fallback NONE, usb-lean pattern) and `remixes/octatrick-usb.py` (+ USB MIDI, USB AUDIO); selftest table lists both as giving up no DSP words. No re-pinning needed: the second zero run is upstream's OVERFLOW_RUN (unused by these remixes), no hook overlaps USB (0x4001d4b2..0x4001e606, 0x40010bc8, 0x400108b0, 0x4000d9a0). Built in the worktree with `make image REMIX=octatrick-usb BUILD=9 VERSION=OCTATRICK9` (1,118,433 B, 4,290 changed, +5,873 appended, 552 B cave left; five DRAM units in one runtime at 0x40a955e0: poly then usbmidi/usbaudio, 114,528 B runtime, verify_dram_boot ok) and `REMIX=octatrick BUILD=9 VERSION=OCTATRIK9N` (no USB, 1,116,368 B). DSP payloads A/B, FX2 id table, stock FX lists, cursor table byte-identical to stock (checked twice). `make check` ALL GATES PASSED for both remixes; verify_usb: UAC2 20 ch 24-bit, 800 polls, 0 under/overruns. Panel script (upstream's ot_emu via `--port-bin`, OTLIVE card copy) 10/10 on both images: boot, PLAY, FM SYNTH>FLEX page, VOIC 1 pitches, VOIC 3 CHRD MAJ 1 : 1.263 : 1.5, SCALE snapping, GLIDE 64 legato (t63 72 ms USB / 127 ms no-USB), CHAIN AFTER option 2 = DIRECT, SYNC TO CARD persistence across eject/insert. Files: ~/Desktop/Octatrick/OCTATRACK_OCTATRICK9.bin (sha256 264988b6...), _OS1.40C_OCTATRICK9.syx (bcb41ca3...), OCTATRACK_OCTATRIK9N.bin (fab17979...), _OS1.40C_OCTATRIK9N.syx (f8532d2a...), WHICH_FILE.txt; OS-body copies out/mainos_octatrick9.bin / out/mainos_octatrik9n.bin. NOT flashed. Unverified: USB on an MKI (upstream's USB hardware runs were all MKII; full-speed USB streams the stereo sum only), Windows/Linux hosts, DISK MODE with a session open; upstream's open items (MAIN lags the tracks, a reordered burst 0.5-1.5 s after stream start). Toolchain in a fresh checkout of `upstream-port`: BUILDING.md (make setup / recon / uv sync / emu-cf); octakit needed a forced submodule checkout, midi-scenes pin 63ca127 comes from this checkout's submodule (GitHub refuses the ref). Publishing the port to `timhastie/octatrick` (its base is 9a49f21) is the natural next step: replace that branch with upstream-port + the wording scrub.
- **26 Sep 2026, OCTATRICK9 ON HARDWARE:** flashed by Tim on his MKI. FM synth, quantizer and direct jump work; USB audio works on the MKI -- his Mac sees the multichannel input and each track comes up on its own channel pair (the first MKI run of the USB stream; upstream's runs were MKIIs). Channel count as listed by the Mac not yet reported. The port branch `upstream-port` (now with the Octatrick README section, local commit in the session worktree) is pushed only to octa-panel; moving it into timhastie/octatrick (push as `main`, switch the default branch) was blocked by the permission classifier and left for Tim to run.
- **26 Sep 2026, USB channels 17-20 (MAIN/CUE) SILENT on Tim's MKI (OCTATRICK9), tracks 1-16 fine.** Investigated (workflow, three agents): (1) under the ColdFire port booted as an MKI, our image streams MAIN on 17/18 (-22.2 dBFS, = the sum of the track-L channels one 16-sample block later) and CUE on 19/20 once a track is cued, with NO recorder involved; upstream's usb-lean image streams identically, so the three modules are not the cause; (2) firmware audit: the ch1 -> ch6 -> ch7 eDMA chain that lands MAIN/CUE at 0x80005e60 is straight-line in frame_isr (0x4000ab42..0x4000ac12, DADDR literal at 0x4000abf2), load-bearing (ch7's completion drives the DSP transfer machine, so tracks working proves the chain completes), the DSP packing at P:0x2d5..0x2eb is unconditional, and the only model branch in the audio path is the headphone slot order 4/5 (P:0x33f) -- no MKI branch can leave the buffer empty; the stock recorder SRC3 = MAIN/CUE reads the same buffer (0x40007600..); (3) no prior report anywhere (Discord dumps to 24 Sep, upstream git/issues, octemu, octalab-notes); Bryan's MKII run was a listening check on a stock project. Tim: SRC3 = MAIN with and without recorder trigs recording changed nothing on 17-18 (whether the recording itself has audio: not yet answered). DIAGNOSTIC BUILD `OCTATRIK9D` (usbaudio.s producer patched, source restored after the build, copy at the worktree's out/_usbaudio_diag.s): ch17 = MAIN L + 0x04000000 DC marker, ch18 = MAIN R, ch19 = track 1 L from the arena, ch20 = CUE L; emulator check: ch17 DC -30.1 dBFS + MAIN, ch19 300 Hz -27 dBFS, ch20 = cued T1 300 Hz; DSP payloads stock. Files in ~/Desktop/Octatrick/. Decision table: DC on 17 but no audio + T1 on 19 = buffer empty on the MKI (DSP/DMA side); nothing on any of 17-20 = host/DAW channel mapping; DC + MAIN audio on 17 = MAIN was there all along. Scripts: scratchpad usbmc/ (run_variant.sh, bench_capture.py, analyze.py, card_cue.img).
