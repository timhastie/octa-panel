# SYNTH MACHINE

A two-operator FM synth machine for FLEX tracks, built from
[timhastie/octatrick-modules](https://github.com/timhastie/octatrick-modules)
(submodule `upstream/`, pinned to `v10`). `Kind.CF_PATCH`: the voice engine as
a DRAM unit (`poly.s`), the page as a pinned ROM cave (`page.s`), a
`SymbolRef` on the kind table's FLEX renderer entry, detours and pokes. No
DSP code, no FX2 row.

## What it does

Any FLEX track whose sample is named SYNTH*.wav becomes a synth (a silent
4 s marker file will do): the DSP shapes and effects the voice as a sample.
Its PLAYBACK page reads PTCH RATO INDX RATE FDBK DEC (title FM SYNTH); on
the LFO page VOIC (1 = mono, 2..4 = paraphonic) and CHRD (32 chord shapes
x their inversions, shape << 2 | inversion on the knob, lockable per step,
snapped onto SCALE QUANTIZER's scale); GLIDE from the quantizer's row. A
chord fingered on the CHROMATIC keys while live recording is recorded as
PTCH (the bass) / CHRD (the shape and inversion) / VOIC (the voices heard)
locks: a 150 ms rolling join window at any tempo, a legato hand-over
(the earlier key up within 50 ms) recorded as single notes, two keys in
one panel scan both sounding (26 Sep 2026, the second pass). MIDI notes into a synth track behave like the panel keys
(30 Sep 2026): one voice a note with the VOIC cap, a note-off releasing
that note, the full 20..127 range (84 = 0 semitones), chord memory with a
shape, fingered MIDI chords recorded as PTCH / CHRD / VOIC locks; sample
tracks stock. `upstream/synth/README.md` is the full description (the
five phases, the voice model, what was measured and what was inferred).

## How it is built

Source: `upstream/` is Tim's repository (submodule, pinned to `v10`).
Nothing inside `upstream/` is edited here. The manifest here
(`manifest.py`) executes `upstream/synth/manifest.py` from the source on
disk and re-exports its `MODULE`; that manifest derives its source paths
from its own directory, so the same file builds at `modules/synth/` in
Tim's tree and at `modules/synth/upstream/synth/` here. The engine is
`Linked(dram=True)`: linked into the platform runtime with the other DRAM
units and depacked at boot into the arena reserve
(`docs/remixer/PLACEMENT.md`). The page is pinned at `0x400d24d0`, the
start of the second free gap.

## Collisions

`tempo-bus` also uses the second free gap, so the ledger refuses that pair.
The engine shares the platform reserve with the other DRAM modules (USB
MIDI, USB AUDIO, MIDI SCENES) inside one runtime.

## Measured

- Remixes `octatrick` and `octatrick-usb` build byte-identical with the
  module sources as a plain `modules/<name>/` directory and as this
  wrapper over the submodule (same base, same build reports), and
  byte-identical to the OCTATRICK9 images Tim flashed (built on
  upstream `0e93543`; upstream's changes since touch modules these
  remixes do not carry).
- **On hardware 26 Sep 2026** as `OCTATRICK9` (remix `octatrick-usb`) on
  Tim's Octatrack MKI: the synth, the quantizer and direct jump, and USB
  audio on all 20 channels on the MKI.
- Chord recording's second pass measured 26 Sep 2026 on the OCTATRICK2.6
  BUILD 16 bus (remix `octatrick-tuner`) in ot_emu at 130 BPM: rolls up to
  140 ms a key one chord, inversions of MAJ and MA7 recorded and played
  back, AD2 / MD2 / AD9 / MAS, a legato hand-over as single trigs, VOIC 2
  + C E G live and back as C E, two keys in one scan both sounding, MIDI
  chords, DORIAN; `upstream/synth/README.md` has the table. Not flashed.
- MIDI IN measured 30 Sep 2026 on the OCTATRICK2.5 BUILD 15 bus (remix
  `octatrick-tuner`) in ot_emu with MIDI fed onto UART0 through the
  emulator's `OT_MIDI_IN` test rig; the sample track T7 byte-identical in
  behaviour to the OCTATRICK9 image on the same rig. Not flashed.
- At `v10` the image differs from OCTATRICK9 (the FM SYNTH page descriptor is a runtime clone, no stock bytes in the repository); emulator-verified
  26 Sep 2026, not yet flashed.

## Updating

Bump the submodule pin and rebuild; the pinned page bytes and the engine's
reads of SCALE QUANTIZER's pinned addresses either hold or the build
refuses.
