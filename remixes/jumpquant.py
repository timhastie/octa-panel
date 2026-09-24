"""JUMPQUANT -- DIRECT JUMP + SCALE QUANTIZER in one image, no effects, no synth.

The first hardware candidate (23 Sep 2026): the two small ColdFire caves that
touch only the main-OS section -- the pattern change option (CHAIN AFTER =
DIRECT; since 24 Sep 2026 the list's unused value 1, so a project saved by
OCTATRICK1..3 with DIRECT (=17) loads as 256/16 and must be set again) and
the SCALE row next to it in PROJECT > CONTROL > SEQUENCER (plus, since 24 Sep
2026, the GLIDE row: without the synth module it only gives the CHROMATIC
keys legato -- a second key while one is held changes the pitch without a
restart -- there is no voice to glide). Build
with `REMIX=jumpquant make cf` -> out/mainos_cf.bin, `BUILD=<n> VERSION=OCTATRICK<n>
REMIX=jumpquant make image-cf` for the card / MIDI images (Tim calls the
firmware OCTATRICK; the container's version field holds 10 characters, so
OCTATRICK1..9): the ColdFire-only build leaves both
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
