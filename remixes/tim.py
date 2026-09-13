"""TIM -- DIRECT JUMP + SCALE QUANTIZER in one image, no effects.

Both are ColdFire caves that touch only the main-OS section: the pattern
change option (CHAIN AFTER = DIRECT) and the SCALE row next to it in
PROJECT > CONTROL > SEQUENCER. Build with `REMIX=tim make bus`, boot the
image in the emulator (Virtual Panel: File > Open Firmware Image...) and
verify per the two modules' READMEs before flashing.
"""
from remix.schema import Remix

REMIX = Remix(
    name="tim",
    doc="DIRECT JUMP + SCALE QUANTIZER, no effects.",
    modules=("DIRECT JUMP", "SCALE QUANTIZER"),
    fallback="NONE",
)
