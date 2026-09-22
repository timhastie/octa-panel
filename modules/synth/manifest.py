"""SYNTH MACHINE -- phase 1: a FLEX track whose sample is named SYNTH* plays a
generated waveform instead of the sample. The skeleton for a two-operator FM
voice: the ColdFire generates the track's SOURCE sample data every frame (a
sine at C4 = 261.6256 Hz in this phase) and the DSP does the rest exactly as
for a sample -- PTCH (with locks, LFOs, scenes, chromatic keys, the
quantizer), RATE, retrigs, the AMP envelope, filter, FX1/FX2, level, pan,
mute, cue. Nothing changes for a track whose sample is not named SYNTH*.

WHERE IT HOOKS. The per-frame record packer (0x4000d3fc) renders each track's
audio through a per-track renderer pointer copied from the kind table
0x400d6434 (kind = machine type; 0 STATIC, 1 FLEX, 2 THRU, 3 NEIGHBOR,
4 PICKUP, 5-7 silent). The FLEX entry 0x400d6438 (stock: the sample renderer
0x40004008) is repointed to the cave's sy_render, which calls the stock
renderer first -- the voice lifecycle, streaming, positions, retrigs and the
record's headers stay stock's -- and then, for a synth track whose ColdFire
voice is active, overwrites the source pairs it shipped. The DSP resamples
the record by the header's rate (0x04000000 = 1.0), which is where PTCH etc.
are already folded in. One 4-byte poke, no displaced instructions.

THE MARKER. The voice struct (0x800049d8 + 0xa8 * track) holds the slot's
settings record at +8 (0x100b14f0 + 0x448 * slot, its path string at +0);
on the frame a voice starts (bit 4 of 0x46104d0c + track, the packer's
event byte) the file name after the last '/' is compared with "SYNTH" and
the result cached per track. Any WAV named SYNTH*.wav in any FLEX slot is
the machine; sample locks choose it per step. A stock unit plays the file
itself (the shipped SYNTH.wav is silence).

Verified in ot_emu through the virtual panel and the pipe (README).
UNFLASHED.
"""

from remix.schema import CavePatch, Kind, Module

# The kind table's FLEX entry: kind -> renderer, 8 longs at 0x400d6434.
KIND_TABLE_FLEX = 0x400d6438
STOCK_RENDERER = bytes.fromhex("40004008")

# synth.s layout (.org): sy_render at +0, the sine table at +0x200, state after it.
CAVE_LEN = 0x42c

# Ratified bytes: synth.s with m68k-elf-as -mcpu=5475, linked at 0x400d7000
# and 0x400d7300 (identical: OS absolutes and pc-relative references only).
# Pinned from the first verified build (22 Sep 2026, cave at 0x400d6b80).
PINNED = bytes.fromhex(
    "4fefffe048d70cfc247980001c802f2f00302f2f00302f2f00302f2f00304eb9"
    "400040084fef00102e00242f002447fa03d47210b2af00306600007841f94610"
    "4d0c41f02800081000046700006643fa03bc42b12c00263c000000a84c023800"
    "41f9800049d82070380828086700003e2248263c000000ff7398670000140c81"
    "0000002f66000004224853836600ffea41fa00a8760573987999b2846600000e"
    "53836600fff27801600000047800178428004a33280067000076263c000000a8"
    "4c02380041f9800049d84a3038006700005e7daa00036700005643ea001041fa"
    "032c20302c0041fa01182a3c0184cbb776182200e6a9d2817570180079701802"
    "98822200e08973c14c014800e084e084d4844842424222c222c2d08553866600"
    "ffd241fa02e8242f002421802c0020074cd70cfc4fef00204e7553594e544800"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "00000192032404b5064607d609640af10c7c0e060f8d11121294141315901709"
    "187e19ef1b5d1cc61e2b1f8c20e7223d238e24da26202760289a29ce2afb2c21"
    "2d412e5a2f6c307631793274336834533537361236e537b03871392b39db3a82"
    "3b213bb63c423cc53d3f3daf3e153e723ec53f0f3f4f3f853fb13fd43fec3ffb"
    "40003ffb3fec3fd43fb13f853f4f3f0f3ec53e723e153daf3d3f3cc53c423bb6"
    "3b213a8239db392b387137b036e536123537345333683274317930762f6c2e5a"
    "2d412c212afb29ce289a2760262024da238e223d20e71f8c1e2b1cc61b5d19ef"
    "187e170915901413129411120f8d0e060c7c0af1096407d6064604b503240192"
    "0000fe6efcdcfb4bf9baf82af69cf50ff384f1faf073eeeeed6cebedea70e8f7"
    "e782e611e4a3e33ae1d5e074df19ddc3dc72db26d9e0d8a0d766d632d505d3df"
    "d2bfd1a6d094cf8ace87cd8ccc98cbadcac9c9eec91bc850c78fc6d5c625c57e"
    "c4dfc44ac3bec33bc2c1c251c1ebc18ec13bc0f1c0b1c07bc04fc02cc014c005"
    "c000c005c014c02cc04fc07bc0b1c0f1c13bc18ec1ebc251c2c1c33bc3bec44a"
    "c4dfc57ec625c6d5c78fc850c91bc9eecac9cbadcc98cd8cce87cf8ad094d1a6"
    "d2bfd3dfd505d632d766d8a0d9e0db26dc72ddc3df19e074e1d5e33ae4a3e611"
    "e782e8f7ea70ebeded6ceeeef073f1faf384f50ff69cf82af9bafb4bfcdcfe6e"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000"
)
assert len(PINNED) == CAVE_LEN, len(PINNED)
assert PINNED[:4] == bytes.fromhex("4fefffe0")          # sy_render: lea -32(%sp),%sp


def emit(addr: int):
    """The source is the only truth for the bytes (b""); the one poke depends on
    where the cave lands: the kind table's FLEX entry -> sy_render (+0)."""
    return b"", ((KIND_TABLE_FLEX, STOCK_RENDERER, addr.to_bytes(4, "big")),)


MODULE = Module(
    name="synth",
    key="SYNTH MACHINE",
    kind=Kind.CF_PATCH,
    doc="A FLEX track whose sample is named SYNTH* plays a generated sine "
        "(C4 at PTCH 0); the DSP pitches, shapes and effects it as a sample.",
    cf_patches=(
        CavePatch(
            label="synth cave",
            cave_addr=None,                   # floats: position independent
            pinned=PINNED,
            source="modules/synth/synth.s",
            emit=emit,
            reference=lambda addr: PINNED,    # the same bytes at any address
            report_note=" (FLEX renderer kind-table entry 0x400d6438 -> sy_render; "
                        "SYNTH*-named samples become a generated sine)",
        ),
    ),
)
