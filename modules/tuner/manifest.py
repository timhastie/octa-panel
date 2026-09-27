"""TUNER -- a guitar-tuner readout of the current audio track: hold UP and
press TEMPO for a window with the note, octave, a +-50 cent needle and the
frequency, from the track's post-FX pre-fader audio, detected on the
ColdFire (McLeod NSDF + YIN refine, integer only) in the UI task. TEMPO,
YES, NO or the chord close it; TEMPO alone, FUNC + TEMPO and UP alone stay
stock.

Source: `upstream/` is Tim Hastie's repository (timhastie/octatrick-modules,
submodule, pinned to v10). The declaration is `upstream/tuner/manifest.py`:
one DRAM unit (`tuner.s`, `Linked(dram=True)`, in the platform reserve with
the other DRAM modules) and three detours (the TEMPO opener's first
instruction, frame_isr's tail, the UI task's loop head); no ROM cave, no
pokes. Its source paths are derived from its own directory, so it is
executed here from the source on disk, as the registry does for every
manifest, and this file only re-exports its MODULE. Nothing inside
`upstream/` is edited here.

Emulator-verified (26 Sep 2026); not yet on hardware.
"""

import pathlib
import runpy

_UPSTREAM = pathlib.Path(__file__).resolve().parent / "upstream" / "tuner" / "manifest.py"

MODULE = runpy.run_path(str(_UPSTREAM), run_name="remix_manifest_tuner")["MODULE"]
