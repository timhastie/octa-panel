"""SCALE QUANTIZER -- the minimal build: the ColdFire unit alone.

Adds no effect and touches no DSP, so the smallest image that carries it
is one module, on the shape of `remixes/direct-jump.py`: no chooser row,
no FX2 id, unimplemented ids resolve to the firmware's own NONE.

Build with `REMIX=quantizer make bus`; verify in the emulator per
modules/quantizer/README.md (never on hardware first).
"""

from remix.schema import Remix

REMIX = Remix(
    name="quantizer",
    doc="Reference minimal build: the SCALE QUANTIZER ColdFire unit, alone.",
    modules=("SCALE QUANTIZER",),
    fallback="NONE",
)
