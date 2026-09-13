"""DIRECT JUMP -- the minimal build: the ColdFire cave alone.

Adds no effect and touches no DSP, so the smallest image that carries it
is one module, on the shape of `remixes/midi-scenes.py`: no chooser row,
no FX2 id, unimplemented ids resolve to the firmware's own NONE.

Build with `REMIX=direct-jump make bus`; verify in the emulator per
modules/direct-jump/README.md (never on hardware first).
"""

from remix.schema import Remix

REMIX = Remix(
    name="direct-jump",
    doc="Reference minimal build: the DIRECT JUMP ColdFire cave, alone.",
    modules=("DIRECT JUMP",),
    fallback="NONE",
)
