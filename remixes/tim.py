"""TIM -- DIRECT JUMP + SCALE QUANTIZER + SYNTH MACHINE in one image, no effects.

All three are ColdFire caves that touch only the main-OS section: the pattern
change option (CHAIN AFTER = DIRECT), the SCALE row next to it in
PROJECT > CONTROL > SEQUENCER, and the SYNTH machine (a FLEX track whose
sample is named SYNTH*.wav plays a generated sine, modules/synth). Build with `REMIX=tim make cf` ->
out/mainos_cf.bin (`make image-cf` for the card): the ColdFire-only build
leaves both DSP payloads and the FX2 chooser stock, so every stock effect
stays selectable. `REMIX=tim make bus` also builds, but rebuilds the
chooser around a remix with no rows -- EFFECT 2 then offers NONE alone
(15 Sep 2026; docs/remixer/MODULES.md). Boot the image in the emulator
(Virtual Panel: File > Open Firmware Image...) and verify per the two
modules' READMEs before flashing.
"""
from remix.schema import Remix

REMIX = Remix(
    name="tim",
    doc="DIRECT JUMP + SCALE QUANTIZER + SYNTH MACHINE, no effects.",
    modules=("DIRECT JUMP", "SCALE QUANTIZER", "SYNTH MACHINE"),
    fallback="NONE",
)
