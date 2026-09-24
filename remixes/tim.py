"""TIM -- DIRECT JUMP + SCALE QUANTIZER + SYNTH MACHINE in one image, no effects.

ColdFire only, no DSP code: the pattern change option (CHAIN AFTER = DIRECT,
its unused value 1 since 24 Sep 2026), the SCALE and GLIDE rows next to it
in PROJECT > CONTROL > SEQUENCER (GLIDE: the synth's glide time and
303-style legato on the CHROMATIC keys), and the SYNTH machine (a FLEX
track whose sample is named SYNTH*.wav plays a two-operator FM voice with
glide; its LFO page's VOIC slot (1..4) makes it paraphonic -- chord shapes
from the CHRD slot, snapped onto SCALE, polyphonic CHROMATIC keys --
modules/synth). Since 24 Sep 2026 the voice engine is a DRAM unit: the build
appends octabam's loader and the packed runtime behind the OS and takes the
platform's 10 MB off the bottom of the audio page arena (the unit shows
FREE MEM 53.5 instead of 63.5 MB on the OTLIVE card); direct-jump, the
quantizer and the synth page stay ROM caves. Build with `REMIX=tim make cf` ->
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
