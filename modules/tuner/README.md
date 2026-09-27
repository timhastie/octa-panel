# TUNER

A guitar-tuner window for the current audio track, built from
[timhastie/octatrick-modules](https://github.com/timhastie/octatrick-modules)
(submodule `upstream/`, pinned to `v10`). `Kind.CF_PATCH`: one DRAM unit
(`tuner.s`) and three detours. No ROM cave, no pokes, no DSP code, no FX2
row.

## What it does

Hold **UP** and press **TEMPO**: a window the size of the stock TEMPO
window opens over the page with the track number, the note name and
octave, a +-50 cent needle, the cents and the frequency in Hz, about seven
readings a second, from the track's post-FX pre-fader audio (the read-back
arena USB AUDIO streams too). Below -54 dBFS or without a confident lobe
the last reading stays with HOLD. TEMPO, YES, NO or UP + TEMPO again close
it; TEMPO alone, FUNC + TEMPO (tap tempo) and UP alone stay stock (UP first,
then TEMPO). `upstream/tuner/README.md` is the full description (the
detector, what was measured and what is open).

## How it is built

Source: `upstream/` is Tim's repository (submodule, pinned to `v10`).
Nothing inside `upstream/` is edited here. The manifest here
(`manifest.py`) executes `upstream/tuner/manifest.py` from the source on
disk and re-exports its `MODULE`; that manifest derives its source paths
from its own directory, so the same file builds at `modules/tuner/` in
Tim's tree and at `modules/tuner/upstream/tuner/` here. The unit is
`Linked(dram=True)`: linked into the platform runtime with the other DRAM
units and depacked at boot into the arena reserve
(`docs/remixer/PLACEMENT.md`). The detours sit on the TEMPO opener's first
instruction (`0x40059ef0`), frame_isr's tail (`0x4000d99a`, the instruction
before USB AUDIO's site) and the UI task's loop head (`0x40056c72`); each
stub replays what it displaced.

## Collisions

None known. It shares the platform reserve with the other DRAM modules
(SYNTH MACHINE's engine, USB MIDI, USB AUDIO, MIDI SCENES) inside one
runtime; `remixes/octatrick-tuner.py` carries it beside `octatrick-usb`'s
modules.

## Measured

- In the emulator (26 Sep 2026): steady tones 55 Hz .. 2 kHz read within
  1 cent; the FM synth at PTCH -12 .. +12 reads the note the page shows;
  the chord, the closes and the stock TEMPO / tap-tempo paths as listed
  in `upstream/tuner/README.md`; audio through the window unchanged.
- **Not yet on hardware.**

## Updating

Bump the submodule pin and rebuild; the hook sites are read from the image
and the build refuses on a difference.
