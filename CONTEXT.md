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
  oracle 28/28). Audio vs the lockstep interpreter: same music, onset within
  2 samples, ~1 dB quieter on the clipping OTLIVE fixture (the cores'
  interleave), not the same samples. Diagnostics: `rtstatus`, `cfstatus`,
  OT_RT_LEAD / OT_RT_FENCE / OT_RT_DOITER knobs; stalls are hunted with
  out/_agents/rt-fence-build/stall_hunt.py. The fence/poll addresses are
  payload A's (P:0x73/0x97/0x4b). Cue out and core 1 are not captured; the
  crossfader and audio inputs have no panel path.
- `.ot` slice files are not seeded onto the card with their samples; the pool
  is wiped and re-seeded at every server start (files added through the
  page survive only while that server runs, or if they are also in an
  `--audio <dir>`).
- The firmware's file browser lists files in card order, not name order.
- RSS grows while the sequencer plays (pre-existing, ~+34 MB per 2 s slice).
