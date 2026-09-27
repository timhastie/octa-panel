# SCALE QUANTIZER

A SCALE row and a GLIDE row in PROJECT > CONTROL > SEQUENCER, built from
[timhastie/octatrick-modules](https://github.com/timhastie/octatrick-modules)
(submodule `upstream/`, pinned to `v10`). `Kind.CF_PATCH`: three ROM units
(`keys.s`, `quantizer.s`, `scale.s`), detours, pokes and a
`TableGrow` for the menu rows. No DSP code, no FX2 row.

## What it does

SCALE (OFF, then 24 scales): the PTCH knob on the PLAYBACK page of a
STATIC / FLEX / PICKUP track steps to the next scale degree, on the Part's
value and on a step's lock; a [TRIG] key in CHROMATIC trig mode snaps to the
nearest degree before it becomes the pitch the voice, the recorded lock and
the screen see. GLIDE (OFF, 1..127): the synth's glide time and 303-style
legato on the chromatic keys; polyphonic chromatic keys on a synth track
whose VOIC is 2..4. Live recording on a synth track writes the played note
length as an AMP HOLD lock. `upstream/quantizer/README.md` is the full
description, with what was measured and what was inferred.

## How it is built

Source: `upstream/` is Tim's repository (submodule, pinned to `v10`).
Nothing inside `upstream/` is edited here. The manifest here
(`manifest.py`) executes `upstream/quantizer/manifest.py` from the source on
disk and re-exports its `MODULE`; that manifest derives its source paths
from its own directory, so the same file builds at `modules/quantizer/` in
Tim's tree and at `modules/quantizer/upstream/quantizer/` here. The key
trampoline and the scale trampoline are at pinned addresses (`KEYS_AT`,
`SCALE_AT`) that SYNTH MACHINE's engine reads; the main unit floats. The
SCALE and GLIDE bytes themselves are battery-backed RAM (`0x100b14ec` /
`0x100b14ed`, `GLIDE_AT`), so they survive a power cycle like CHAIN AFTER
does -- until 26 Sep 2026 they lived in the OS image and came back OFF at
every boot although SAVE had written them to the project files (the boot
restores the unit from battery RAM and reads no project file).

## Measured

- Remixes `octatrick` and `octatrick-usb` build byte-identical with the
  module sources as a plain `modules/<name>/` directory and as this
  wrapper over the submodule (same base, same build reports), and
  byte-identical to the OCTATRICK9 images Tim flashed (built on
  upstream `0e93543`; upstream's changes since touch modules these
  remixes do not carry).
- **On hardware 26 Sep 2026** as `OCTATRICK9` (remix `octatrick-usb`) on
  Tim's Octatrack MKI.
- At `v10` the image differs from OCTATRICK9 (SCALE and GLIDE moved into
  battery RAM, `glide.s` gone; two jsr detours keep the bytes sane);
  emulator-verified 26 Sep 2026 (SAVE, then a warm boot with the battery
  RAM carried over, keeps SCALE and GLIDE), not yet flashed.

## Updating

Bump the submodule pin and rebuild; SYNTH MACHINE reads the pinned
addresses, so a pin that moves them needs both modules bumped together.
