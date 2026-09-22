"""SYNTH MACHINE -- the minimal build: the ColdFire cave alone.

Adds no effect and touches no DSP: one cave and one 4-byte poke in the
main-OS section (modules/synth). Build with `REMIX=synth make cf` ->
out/mainos_cf.bin; verify in the emulator per modules/synth/README.md
(never on hardware first). A FLEX track whose sample is named SYNTH*.wav
plays a two-operator FM voice (STRT/LEN/RTRG/RTIM = ratio/index/feedback/
decay, PTCH the pitch), shaped and effected by the DSP as a sample.
"""

from remix.schema import Remix

REMIX = Remix(
    name="synth",
    doc="Reference minimal build: the SYNTH MACHINE ColdFire cave, alone.",
    modules=("SYNTH MACHINE",),
    fallback="NONE",
)
