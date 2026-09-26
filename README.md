# octabam

> **A personal research project, shared in case it is useful to you.** I
> work on this for my own unit and publish it so others can build on it.
> Pull requests are very welcome — a module, a port of someone's mod, a
> fix, a doc correction. Issues and feature requests are not something I
> can take on — this is a spare-time project and the queue is already my
> own. If there is something you want the remixer to do, the way to get
> it is to build it (`CONTRIBUTING.md`, `docs/remixer/MODULES.md`) and
> send the PR; I will gladly review it. And if you would like to run a
> supported version of this — one that takes requests, tracks issues and
> answers questions — please fork it and do exactly that. The licence
> allows it and I would be glad to see it.

A remixer for the Elektron Octatrack's operating system: pick the
modifications you want and build them into one firmware image from your
own copy of OS 1.40C.

A modification is a **module** (`modules/<name>/`), a selection of modules
is a **remix** (`remixes/<name>.py`), and `make image REMIX=<name>` composes
a remix into a card-flashable image: placing code, wiring hooks by symbol,
refusing collisions by name, and proving every ported module against its
author's own build byte for byte. No firmware is distributed here; every
image is derived from the user's own 1.40C on the user's machine.

**[docs/remixes/BUILDING.md](docs/remixes/BUILDING.md)** is the step-by-step
guide from a fresh machine to a flashed unit.
**[docs/remixes/README.md](docs/remixes/README.md)** lists every remix with
its contents and hardware status.

---

## Octatrick: three firmware modules, built with octabam

[![Octatrick demo video: the FM synth, scale quantizer and direct jump running on a real Octatrack MKI](https://img.youtube.com/vi/1DqUzvs8J3U/maxresdefault.jpg)](https://www.youtube.com/watch?v=1DqUzvs8J3U)

**Demo video:** [Octatrick running on a real Octatrack MKI (YouTube)](https://www.youtube.com/watch?v=1DqUzvs8J3U) -- the FM synth, scale quantizer and direct jump in use. Click the picture to watch.

This repository is [sambanks/octabam](https://github.com/sambanks/octabam)
at commit `0e93543` (26 Sep 2026) plus three modules and two remixes.
Everything below this section is Sam's project as it was then. The build
system is unchanged: the modules use upstream's `SymbolRef` and its DRAM
platform as they are. The module sources live in
[timhastie/octatrick-modules](https://github.com/timhastie/octatrick-modules),
consumed here as a git submodule under each module directory
(`modules/<name>/upstream`, pinned to tag `v9.1` = OCTATRICK9); the
`manifest.py` in each directory executes the submodule's manifest and
re-exports its `MODULE`, the shape octabam uses for MIDI SCENES and Octakit.
The additions:

- **`modules/synth`** -- a two-operator FM synth machine: any FLEX track whose
  sample is named SYNTH*.wav becomes a synth (a silent 4 s marker file will
  do), with its own PLAYBACK page (PTCH RATO INDX RATE FDBK DEC), and on the
  LFO page VOIC (1 = mono, 2..4 = paraphonic) and CHRD (32 chord shapes,
  lockable per step, snapped to SCALE). The engine is a DRAM unit in
  octabam's sample-RAM reserve (10 MB off the sample pool).
- **`modules/quantizer`** -- a SCALE row in PROJECT > CONTROL > SEQUENCER
  (24 scales): the PTCH knob, parameter locks and CHROMATIC trig keys snap to
  the scale; a GLIDE row with 303-style legato for the synth; live recording
  on synth tracks writes the played note length as an AMP HOLD lock.
- **`modules/direct-jump`** -- CHAIN AFTER gains a DIRECT option (option 2 of
  the list): a pattern chosen while the sequencer runs starts at the next
  step, at the step count the old pattern had reached.
- **`remixes/octatrick.py`** -- the three modules plus the fourteen stock
  effects (fallback NONE), so the DSP payloads and the effect chooser stay
  stock. **`remixes/octatrick-usb.py`** adds markandrus's USB MIDI and USB
  AUDIO: the unit becomes a class-compliant MIDI port and a 20-channel
  24-bit audio input (tracks 1-16 post-FX pre-fader, MAIN 17-18, CUE 19-20).

**Build:** clone with the submodules --

```
git clone --recurse-submodules https://github.com/timhastie/octatrick
```

(or, in an existing clone, `git submodule update --init`; without it the
three `modules/*/upstream` directories are empty and the registry finds no
SYNTH MACHINE, SCALE QUANTIZER or DIRECT JUMP) -- then follow octabam's
quick start below (`make setup`, `make os` with your own OS 1.40C file,
`make recon`), then

```
make image REMIX=octatrick-usb BUILD=1 VERSION=OCTATRICK1
```

(`REMIX=octatrick` for the build without USB; VERSION is the name the unit
shows, ten characters at most; bump BUILD every flash.)

**Status:** `octatrick-usb` is flashed and in use on the author's Octatrack
MKI (26 Sep 2026): the synth, the quantizer and direct jump work as before,
and USB audio works on the MKI: all 20 channels, each track on its own
channel pair, MAIN on 17-18 and CUE on 19-20 (the earlier USB audio runs
were on MKIIs). MAIN and CUE were silent right after the OS upgrade until
the unit was fully powered off and on, so power-cycle before judging a
flash. Every feature was verified in an
emulator before flashing: the same modules, a real-time build of octabam's
emulator and a virtual front panel for it live in the companion repository
[timhastie/octa-panel](https://github.com/timhastie/octa-panel). Read
`docs/remixer/FLASHING.md` first, power-cycle the unit after an OS upgrade,
and SAVE or SYNC TO CARD after changing project settings. Combining with
other modules: the synth page is pinned at the start of the second free gap
(`0x400d24d0`), which `tempo-bus` also uses, so the ledger refuses that
pair; the synth engine shares the sample-RAM reserve with the other DRAM
modules (USB, MIDI SCENES) inside one runtime.

**Unofficial.** Not affiliated with, endorsed by or supported by Elektron.
No firmware is distributed here: every image is built on your machine from
your own copy of OS 1.40C, and modifying your unit's firmware is outside
Elektron's licence terms and warranty.

## What it carries

Every module, with its author. Those with a repository are built from it.

| module | author | what it does | proof |
|---|---|---|---|
| **MIDI SCENES** | [bkkbrls-del/midisc](https://github.com/bkkbrls-del/midisc) | per-scene parameter locks driven over MIDI | his sources (submodule, GNU-as form), twelve units in DRAM, 38 detours, 4 pokes; every region equals his encoder's bytes |
| **USB MIDI** | [markandrus/octemu](https://github.com/markandrus/octemu) | class-compliant USB-MIDI in and out on the OT's own USB port, mirroring DIN: the firmware's dormant transmit encoder wired in, a receive decoder into its MIDI path | his shims as a DRAM unit, seven detours, four pointer rewrites; enumerates, receives and transmits under the ColdFire port (`verify_usb`); not on hardware |
| **USB AUDIO** | [markandrus/octemu](https://github.com/markandrus/octemu) | the tracks (post-FX pre-fader), MAIN and CUE over USB: 20 channels of 24-bit UAC2, the tracks' stereo sum at full speed | his producer, packet builder and servo as a DRAM unit on the loader instead of his card payload; streams 22/23-frame packets at the device's poll cadence under the port; not on hardware |
| **OCTAKIT** | [emuyia/ems-octakit](https://github.com/emuyia/ems-octakit) | 256 Kits per Project in place of 64 bank-tied Parts, with names, copy/paste, undo, migration of old projects | her recipe (submodule) compiled, packed and appended by the build; stock + her writes + her append reproduces her own OS image |
| **LOFI AMF FIX** | [bryantysinger/octa-bt-pt](https://github.com/bryantysinger/octa-bt-pt) | stock LO-FI's AMF knob jumps the pitch backwards at some settings; two DSP words fix it | both words disassembled against stock |
| **REPITCH** | [repeat98](https://github.com/repeat98) | a fifth TSTR value: the track follows the project tempo by playback speed, no grains; PTCH off on that track | ColdFire unit + a 5-position TSTR widget; on an MKII (OCTABAM81, 16 Sep 2026); `tools/verify/verify_repitch.py` |
| **BusVerb / BusDelay / Send** | [sambanks](https://github.com/sambanks) | one aux bus: every track's SEND knob → a multi-mode delay → an eight-line FDN reverb, each engine's wet on the track that hosts it. A route the stock firmware has no path for | on Sam's MKII |
| **Spectrum / Character / Modulation** | [sambanks](https://github.com/sambanks) | three FX1 stations replacing FILTER, LO-FI and CHORUS: a filter pedal, a saturation/compressor/width chain, a modulation pedal | on Sam's MKII |
| **WarpFold, Ripple, Rungs, Streamz, BodeShift, Nimbus** | [sambanks](https://github.com/sambanks) | six Mutable-Instruments-flavoured per-track inserts that stack | local render; never flashed |
| **TEMPO SYNC, CC MAP, MODE DEFAULTS** | [sambanks](https://github.com/sambanks) | ColdFire patches: BusDelay's TIME reads as a division; MIDI CC 62–73 reach page-2 knobs; a MODE turned on the panel (or over CC) re-defaults the knobs around it from the module's views | on the unit |
| **FLEX SEEK BIND, FLEX SEEK BIND CTR, RECORDER SPACING** | [sambanks](https://github.com/sambanks) | the recorder click fix: three ColdFire caves that remove the click at a recorder loop's seam; remix `recfix` carries them beside the stock chooser | on hardware (OCTABAM83, 12 Sep 2026) |
| **RECORDER HOLD** | [sambanks](https://github.com/sambanks) | the sound-on-sound click (SRC3 = the track): a recorder-buffer voice reading one sample past its recording repeats the last sample instead of reading zero; in remix `recfix` | port-gated (26 Sep 2026) |
| **KITS RELOAD, SCENES KITS** | [sambanks](https://github.com/sambanks) | bridges that let byte-disjoint but behaviourally colliding modules share an image: MIDI SCENES' Part Reload beside Octakit's kit reload (the first `ok-ms` image trapped on the first reload without it); CC MAP and Octakit sharing the MIDI CC dispatch entry. `make modules` marks a pair that needs one with `✓*` | `ok-ms` on hardware 14 Sep 2026, confirmed by midisc's author on his unit |
| **HELLO WORLD, HELLO DRAM** | [sambanks](https://github.com/sambanks) | the two reference modules, one DSP knob and one DRAM unit, kept building as canaries | `make check` |

`make modules` prints the index, the compatibility matrix (which ColdFire
modules can share an image, from the same check the build makes; `✓*` is a
pair that needs the named bridge) and every remix.

## Quick start

```bash
git clone --recurse-submodules https://github.com/sambanks/octabam
cd octabam
make setup                          # toolchain (macOS + Homebrew; docs/WSL.md for Linux)
make os && make recon               # your own 1.40C -> out/raw/section_3_MAIN_OS.bin
make image REMIX=ok-ms BUILD=1      # -> out/OCTATRACK_OCTABAM1.bin
```

`make check REMIX=<name>` runs every gate and boots the image under the
local ColdFire emulator. `make remix` opens the TUI remixer
(`docs/remixer/REMIXER.md`).

## How it works

```
modules/<name>/manifest.py   what a module is and what it claims (yours, or a pointer into an author's repo)
remixes/<name>.py            which modules, in which chooser order
tools/remix/ledger.py        refuses two modules that claim one address, hook, id or buffer, by name
tools/build/build_bus.py     the build: assembles, links, places, wires, verifies -> out/mainos_bus.bin
tools/verify/*               the gates: oracles, the boot under the ColdFire port, menu, cycles, identity
```

A module's code lands in one of three places; the build decides which bytes
go where, and a module declares what it is, not an address:

| class | declared as | where |
|---|---|---|
| ROM cave | `CavePatch`: a `.s` source, or ratified hex | one of the OS image's free zero runs, ~8 KB total shared by everyone |
| DRAM unit | `Linked(..., dram=True)`: a GNU-as unit | linked with every other DRAM unit in the remix into one runtime, packed, appended behind octabam's loader, depacked at boot into a 10 MB reserve carved off stock's 85.5 MB sample/recorder pool |
| appended runtime | `Runtime`: a recipe (Octakit's `firmware.json`) | its own reserve of the same pool, as a second payload of the same loader |

The OS-image edits every class needs — a detour at a stock instruction, a
poke, a grown table — are `Detour`, `Poke`, `TableGrow`, wired by symbol and
asserted against stock before a byte is written. `docs/remixer/PLACEMENT.md`
is the map of what is free and what was measured.

**A port is a proof.** The build re-links every unit at the author's own
address and compares, rebuilds Octakit's runtime to the identities her
recipe pins, and refuses on any drift. `CONTRIBUTING.md` is the contract;
`docs/remixer/MODULES.md` the guide to writing a module.

## Checking without a flash

The DSP side renders locally on the assembled instruction stream (`make
render`, `make render-rig`; `docs/remixer/HARNESS.md`). The whole machine
— ColdFire, both DSP cores, the card, the panel, MIDI, USB — runs under a
port of it (`tools/emu/ot_emu`, `make emu-cf`):

```bash
make check REMIX=<name>             # boots the image under the port; OT_PROJECT=<dir> adds a real project
make panel REMIX=<name>             # the virtual front panel with sound at localhost:8563 (tools/panel/README.md)
make emu-live REMIX=<name>          # the screen and keys in a window, no sound
```

`docs/remixer/EMU.md` covers all of them and the Unicorn routes the
label gates use. What the emulators cannot see — caches, the recorder,
cross-core timing — is listed beside every gate that is blind to it.

## Before you flash anything

**Writing a non-official OS to an Octatrack can leave it unusable and puts
your warranty in question.** Nothing here is endorsed by, supported by, or
affiliated with Elektron. `docs/remixer/FLASHING.md` has the recovery path;
`docs/remixer/FAILURE_MODES.md` is the register of what has gone wrong on a
unit and why. Back up projects before flashing anything that changes them
(Octakit migrates Parts to Kits on load; downgrading may lose Kit data).

MKI and MKII run the same 1.40C image (hash-verified). sambanks's effects
have only been tested on an MKII; the DRAM platform has run on an MKI ([octalab](https://github.com/nordseele/octalab-notes), 11 Sep 2026)
and on midisc's author's unit (`ok-ms`, 14 Sep 2026).

**No Elektron binary is redistributed here, and none may be.** A built
`.bin` or `.syx` contains Elektron's OS: do not share built images. Share
the repo; everyone builds their own.

*Octatrack* and *Elektron* are trademarks of Elektron Music Machines MAV
AB, used here only to identify the hardware this project targets.

## Repository layout

```
PLAN.md            what octabam is, where it stands, the work order
CONTRIBUTING.md    the module contract, the oracle rule, the gates, submodule etiquette
modules/           the contributions, one directory each
remixes/           named selections of modules, in chooser order
docs/remixes/      one page per remix, and the build guide
tools/remix/       the toolkit: schema, registry, ledger, the loader, the DRAM platform, the TUI
tools/build/       the image build (build_bus.py) and the tools that understand the OS layout
tools/verify/      the gates
tools/harness/     hear and measure the DSP side locally (dsp_host, send_probe, rig_render)
tools/emu/         the ColdFire emulators: the headless port (ot_emu) and the Unicorn bring-up
tools/hw/          the unit and its card: MIDI control, capture, project files, MIDI flashing
tools/patches/     local patches to the vendored toolchains
scripts/           toolchain setup, OS fetch and recon, the bit-identity gate
dsp/               shared DSP infrastructure: the null stub and the probes
docs/remixer/      using and extending the remixer: MODULES, PLACEMENT, REMIXER, TOOLING, FLASHING
docs/firmware/     the firmware, reverse-engineered: ARCHITECTURE, KERNEL, DSP, CHIP, TABLES, PARAM_PAGES, MAINMENU, PANEL, MIDI, LFO, LEVEL_LAW, COLDFIRE_DELAY, RECORDER, STORAGE; CONTRIBUTIONS is the dated index of what each contributor sent
docs/effects/      the effects: XBUS (the bus), REVERB, MASTER, PORTS
```

## Credit

**Em** ([emuyia](https://github.com/emuyia)) designed Octakit and the
loader-appended DRAM runtime octabam adopted as its large-payload placement;
`tools/remix/loader.S` is derived from hers with attribution. Her repository
invites use as a submodule to combine with other efforts.

This began as a fork of [mxldyn/octamax](https://github.com/mxldyn/octamax)
by Maxolydian, whose reverse engineering of the OS format, memory map and
parameter tables made any of this reachable; the upstream history is in
this repository's log.

`vendor/` pulls in [dsp56300](https://github.com/dsp56300/dsp56300),
[mc68k](https://github.com/joelanders/mc68k-md-mm) and
[elektron-firmware-tool](https://github.com/mischa85/elektron-firmware-tool).

## License

[MIT](LICENSE) for this repository's own code and documentation. It does
not extend to Elektron's firmware, which is not distributed here, nor to
the repositories referenced as submodules, which remain their authors'
under their own terms.
[THIRD_PARTY.md](THIRD_PARTY.md) lists every transcribed DSP source
(Airwindows, JClones, Mutable Instruments, ChowDSP, jpcima, audiojs), the
submodules and the vendored tools, each with its licence.
