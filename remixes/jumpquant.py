"""JUMPQUANT -- DIRECT JUMP + SCALE QUANTIZER in one image, no effects, no synth.

The first hardware candidate (23 Sep 2026): the two small ColdFire caves that
touch only the main-OS section -- the pattern change option (CHAIN AFTER =
DIRECT) and the SCALE row next to it in PROJECT > CONTROL > SEQUENCER. Build
with `REMIX=jumpquant make cf` -> out/mainos_cf.bin, `BUILD=<n> REMIX=jumpquant
make image-cf` for the card / MIDI images: the ColdFire-only build leaves both
DSP payloads and the FX2 chooser stock, so every stock effect stays selectable.
Verify in the emulator per the two modules' READMEs before flashing
(docs/remixer/FLASHING.md).
"""
from remix.schema import Remix

REMIX = Remix(
    name="jumpquant",
    doc="DIRECT JUMP + SCALE QUANTIZER, no effects, no synth.",
    modules=("DIRECT JUMP", "SCALE QUANTIZER"),
    fallback="NONE",
)
