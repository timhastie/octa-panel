"""SYNTH MACHINE -- phase 2: a FLEX track whose sample is named SYNTH* plays a
two-operator FM voice instead of the sample. The ColdFire generates the
track's SOURCE sample data every frame, at the track's final pitch, and the
DSP does the rest exactly as for a sample -- RATE, the AMP envelope, filter,
FX1/FX2, level, pan, mute, cue -- while PTCH (with locks, LFOs, scenes,
chromatic keys, the quantizer) is folded into the voice's own phase
increments with the stock renderer's rate arithmetic. Nothing changes for a
track whose sample is not named SYNTH*.

THE VOICE: carrier = sin(phi_c + INDEX * sin(phi_m + FEEDBACK * m_prev)),
phi_m at RATIO times the pitch. The FLEX PLAYBACK page's other slots are its
parameters, read per frame from the DSP parameter record (halfwords, raw <<
8): STRT = RATIO (32-step table 0.25..16), LEN = INDEX (0..8 rad), RTRG =
FEEDBACK (0..0.25 cycle), RTIM = DECAY of the index toward 1/16 (time
constant 2 s * (raw/127)^2; 0 = hold). Phase 3 relabels the slots.

WHERE IT HOOKS. The per-frame record packer (0x4000d3fc) renders each track's
audio through a per-track renderer pointer copied from the kind table
0x400d6434 (kind = machine type; 0 STATIC, 1 FLEX, 2 THRU, 3 NEIGHBOR,
4 PICKUP, 5-7 silent). The FLEX entry 0x400d6438 (stock: the sample renderer
0x40004008) is repointed to the cave's sy_render, which, for a synth track,
writes PTCH := 0 semitones and RATE := 1.0 into the record around the stock
call (so the voice lifecycle, streaming, positions and the record's headers
stay stock's, at 16 source samples a frame), restores them, clears the retrig
count the packer latched at a voice start (RTRG is FEEDBACK, not a retrig),
and overwrites the source pairs the stock renderer shipped. One 4-byte poke,
no displaced instructions.

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

# synth.s layout: sy_render at +0, the ratio table at +0x30e, the sine table
# at +0x350 (.org), the per-track state at +0x554.
CAVE_LEN = 0x694

# Ratified bytes: synth.s with m68k-elf-as -mcpu=5475, linked at 0x400d7000
# and 0x400d7300 (identical: OS absolutes and pc-relative references only).
# Pinned from the phase-2 build (22 Sep 2026; linked at 0x400d6b80, 0x400d6e00,
# 0x400d7000 and 0x400d7300: identical).
PINNED = bytes.fromhex(
    "4fefffd048d77cfc247980001c80242f003476284c02380047fa053ad7c32879"
    "800062a878007210b2af0040660000aa41f946104d0c41f02800081000046700"
    "007e263c000000a84c02380041f9800049d82070380826086700005e2248263c"
    "000000ff7398670000140c810000002f66000004224853836600ffea41fa028a"
    "760573987b99b2856600002e53836600fff242ab000442ab002042ab0018223c"
    "01000000274100082079800062a442a800047601600000047600174300244a2b"
    "0024670000147dd47bec000638bc4000397c7f00000678012f2f00402f2f0040"
    "2f2f00402f2f00404eb9400040084fef00102f40002c4a846700014638863945"
    "00062006a3430c4040006f00000a048000003c0076002200ea8041f9400aa294"
    "41f00c00741be5a92418e289a102a4982901a401080075ac001b6600002a2405"
    "0c427f006c000020a1c04842e58a6400000ae28a44826000000ae28a06828000"
    "0000a4000900a1c0e6a0223c0184cbb7a0010800a1c0eb882740000c73ec0002"
    "e089e48941fa018873f01a00e0804c0108002740001073ec0008203c00000204"
    "4c001000e0892741001c73ec000ae0896700004224014c021000203c002ed1e0"
    "4c4100000c80000fffff6f000008203c000fffff222b00080481001000006f00"
    "001ee089e8894c001000e08993ab00086000000c223c010000002741000873ec"
    "0004203c00000a444c001000e089242b0008e08ae88a4c021000e089e8892741"
    "0014222b00180681000010000c81000080006f000008223c0000800027410018"
    "4a2b0024670000b4242f0034263c000000a84c02380041f9800049d84a303800"
    "670000987faa00036700009043ea001020132c2b00042a2b000c246b0010286b"
    "002041fa00cc7618220c4c2b1800001cd2862401e6aad4827970280075702802"
    "9484e08973c14c012800e082e082d88228444c2b48000014d8802404e6aad482"
    "73702800757028029481e08c79c44c042800e082e082d2824c2b18000018d281"
    "424122c122c1d085dc8a53876600ff9a268027460004274c0020202f002c4cd7"
    "7cfc4fef00304e7553594e5448000040008000c0010001030140016a018001c0"
    "0200020302800300038004000403048005000580060006800700078008000900"
    "0a000b000c000d000e000f001000000000000192032404b5064607d609640af1"
    "0c7c0e060f8d11121294141315901709187e19ef1b5d1cc61e2b1f8c20e7223d"
    "238e24da26202760289a29ce2afb2c212d412e5a2f6c30763179327433683453"
    "3537361236e537b03871392b39db3a823b213bb63c423cc53d3f3daf3e153e72"
    "3ec53f0f3f4f3f853fb13fd43fec3ffb40003ffb3fec3fd43fb13f853f4f3f0f"
    "3ec53e723e153daf3d3f3cc53c423bb63b213a8239db392b387137b036e53612"
    "3537345333683274317930762f6c2e5a2d412c212afb29ce289a2760262024da"
    "238e223d20e71f8c1e2b1cc61b5d19ef187e170915901413129411120f8d0e06"
    "0c7c0af1096407d6064604b5032401920000fe6efcdcfb4bf9baf82af69cf50f"
    "f384f1faf073eeeeed6cebedea70e8f7e782e611e4a3e33ae1d5e074df19ddc3"
    "dc72db26d9e0d8a0d766d632d505d3dfd2bfd1a6d094cf8ace87cd8ccc98cbad"
    "cac9c9eec91bc850c78fc6d5c625c57ec4dfc44ac3bec33bc2c1c251c1ebc18e"
    "c13bc0f1c0b1c07bc04fc02cc014c005c000c005c014c02cc04fc07bc0b1c0f1"
    "c13bc18ec1ebc251c2c1c33bc3bec44ac4dfc57ec625c6d5c78fc850c91bc9ee"
    "cac9cbadcc98cd8cce87cf8ad094d1a6d2bfd3dfd505d632d766d8a0d9e0db26"
    "dc72ddc3df19e074e1d5e33ae4a3e611e782e8f7ea70ebeded6ceeeef073f1fa"
    "f384f50ff69cf82af9bafb4bfcdcfe6e00000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000"
)
assert len(PINNED) == CAVE_LEN, len(PINNED)
assert PINNED[:4] == bytes.fromhex("4fefffd0")          # sy_render: lea -48(%sp),%sp


def emit(addr: int):
    """The source is the only truth for the bytes (b""); the one poke depends on
    where the cave lands: the kind table's FLEX entry -> sy_render (+0)."""
    return b"", ((KIND_TABLE_FLEX, STOCK_RENDERER, addr.to_bytes(4, "big")),)


MODULE = Module(
    name="synth",
    key="SYNTH MACHINE",
    kind=Kind.CF_PATCH,
    doc="A FLEX track whose sample is named SYNTH* plays a two-operator FM "
        "voice (STRT/LEN/RTRG/RTIM = ratio/index/feedback/decay); the DSP "
        "shapes and effects it as a sample.",
    cf_patches=(
        CavePatch(
            label="synth cave",
            cave_addr=None,                   # floats: position independent
            pinned=PINNED,
            source="modules/synth/synth.s",
            emit=emit,
            reference=lambda addr: PINNED,    # the same bytes at any address
            report_note=" (FLEX renderer kind-table entry 0x400d6438 -> sy_render; "
                        "SYNTH*-named samples become a 2-op FM voice)",
        ),
    ),
)
