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
note). The SET DATE/TIME dialog is closed automatically with YES.
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
- **Speed:** port backend ≈ 0.36–0.4 s wall per 16th step at 120 BPM (~3×
  slower than real time); never enable `rt.exact_clock()` on route A (100×).
- LCD framebuffer in RAM (0x460d1f80) is NOT what the screen shows (page
  order rotates) — always render from the UART stream.

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

- LEDs in the page: the server's `/leds` rows chase while playing (verified),
  but the page showed none lit on 12 Sep — element ids (`led-…`) vs map keys
  (`led_…`) suspected; fix in `panel.html` `applyLeds`.
- "LAST SET" record source; SETUP-page redraw; crossfader input path; encoder
  push (not found in the matrix); trig LEDs 9-16 colour vs hardware.
- Upstream octabam main has moved (recfix, PR #97/#129, Workbench); this fork
  is a 10 Sep clone — merging upstream is pending.
- Playback still ~3× slower than real time (port); the DSP audio path is not
  wired to the panel (no sound).
