"""octatrick-tuner -- octatrick-usb plus the TUNER window.

remixes/octatrick-usb.py (SYNTH MACHINE, SCALE QUANTIZER, DIRECT JUMP, USB
MIDI, USB AUDIO on the stock effects) with modules/tuner: UP + TEMPO opens
a tuner for the current audio track. Build with `make bus
REMIX=octatrick-tuner BUILD=N` (`make image ...` for a flashable file).
"""

from remix.schema import Remix

REMIX = Remix(
    name="octatrick-tuner",
    doc="SYNTH MACHINE + SCALE QUANTIZER + DIRECT JUMP + USB MIDI + USB AUDIO + TUNER on the stock effects.",
    modules=("DIRECT JUMP", "SCALE QUANTIZER", "SYNTH MACHINE",
             "USB MIDI", "USB AUDIO", "TUNER",
             "FILTER", "EQUALIZER", "DJ EQ", "PHASER", "FLANGER", "CHORUS",
             "SPATIALIZER", "COMB FILTER", "COMPRESSOR", "LO-FI", "DELAY",
             "PLATE REV", "SPRING REV", "DARK REV"),
    fallback="NONE",
)
