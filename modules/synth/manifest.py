"""SYNTH MACHINE -- phases 2+3: a FLEX track whose sample is named SYNTH* plays a
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

THE PAGE (phase 3). The page-descriptor resolver 0x40031da4 is detoured at
its PLAYBACK-page table load (0x40031ece): for a track whose assigned FLEX
slot's sample is named SYNTH* -- by the settings record's path, loaded into
# flex RAM or not -- it returns a cloned FLEX descriptor (in the
pinned page cave, modules/synth/page.s) whose slots read PTCH RATO INDX RATE
FDBK DEC, whose title makes the footer read FM SYNTH>FLEX, whose formatters
print the ratio table's value, 0..127 and HOLD/ms/s, and whose widgets draw
the M->C operator diagram, a sideband spectrum, the modulator with its
feedback loop and the index envelope over the stock dial. Ranges, defaults
and knob handlers are the stock record's; every other page and every
non-synth track draws as stock.

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


# ---- phase 3: the page (modules/synth/page.s) ----------------------------------
# The PLAYBACK page presents the synth: a detour in the page-descriptor
# resolver (0x40031da4, the kind-0 `tbl[machine]` load at 0x40031ece) returns a
# cloned FLEX descriptor -- names PTCH RATO INDX RATE FDBK DEC, the title
# "FM SYNTH" (the footer reads FM SYNTH>FLEX), formatters (the ratio table's
# value, 0..127, HOLD/ms/s), widgets that draw the operator diagram, the
# sideband spectrum, the feedback loop and the index envelope over the stock
# dial -- when the current track's assigned FLEX sample is named SYNTH*.
# Pinned at the second zero run: the clone holds absolute pointers into the
# cave. One 6-byte poke (`movel %a0@(0,%d0:l:4),%d0; bras` -> `jmp pg_resolve`).
PAGE_AT = 0x400d24d0
PAGE_LEN = 1936
RESOLVER_HOOK = 0x40031ece
RESOLVER_STOCK = bytes.fromhex("20300c00" "6002")
# Ratified bytes: page.s with m68k-elf-as -mcpu=5475, linked at PAGE_AT (23 Sep 2026: the plain icons).
PINNED_PAGE = bytes.fromhex(
    "20300c000c80400d31ae66000078243c000018b24c012800d4892803e58cd883"
    "d4842042d1fc0008f04b75900c820000007f62000050283c000004484c024800"
    "0684100b14f020442248283c000000ff7b98670000140c850000002f66000004"
    "224853846600ffea41fa002078057b987599ba826600000e53846600fff241fa"
    "046c20084ef940031ed653594e5448002f2f00084879400b465d2f2f000c4eb9"
    "40013a084fef000c4e752f02202f000ce48802800000001f41fa035473f00a00"
    "2001e0880281000000ff74644c021000e0896700003e0c81000000326700001c"
    "2f012f00487a02fe2f2f00144eb940013a084fef0010600000302f00487a02ee"
    "2f2f00104eb940013a084fef000c600000182f004879400b465d2f2f00104eb9"
    "40013a084fef000c241f4e752f02202f000c6700007c22004c001000203c0000"
    "07d04c010000068000001f80223c00003f014c4100000c80000003e86400001c"
    "2f004879400b465d2f2f00104eb940013a084fef000c60000048223c000003e8"
    "24004c41200272644c4100002202e789d282d28290812f002f02487a02552f2f"
    "00144eb940013a084fef001060000012487a02462f2f000c4eb940013a08508f"
    "241f4e7570006000001470016000000e7002600000087003600000024fefffd4"
    "48d77cfc2e002f2f00482f2f00482f2f00482f2f00482f2f00482f2f00482f2f"
    "00484eb9400479b44fef001c2c2f003c4a876700001253876700001853876700"
    "00b2600000c841fa0456610001786000012641fa05166100016c220670034c00"
    "1000707f4c401001740b948170087201610001742406700a4c002000707f4c40"
    "200267000012700672016100015a700a72016100015224060482000000146f00"
    "002270084c002000706b4c40200267000012700472016100012e700c72016100"
    "012624060482000000386f0000aa70064c00200070474c4020026700009a7002"
    "720161000102700e7201610000fa6000008641fa03ee610000cc4a8667000078"
    "41fa0424610000ce6000006c41fa045c610000b270017201740b610000ca2a3c"
    "00007fff4a86670000122a06700d4c005000707f4c4050055485780b7e022407"
    "5382e98a4c4520020c82000000106f000004741041fa00c775b0280026422007"
    "2202240461000080280b52870c870000000f6f00ffca202f0040080000006700"
    "001841fa0144701022100a81fff0000020c153806c00fff2202f00345e802f00"
    "202f003452802f002f2f0050487a00c24eb9400128a84fef00104cd77cfc4fef"
    "002c4e7543fa0102701022d853806c00fffa4e7543fa00f27010221883995380"
    "6c00fff84e7541fa00e041f00c00263c80000000e2ab8790e28b5281b4816c00"
    "fff64e7525642e253032640025642e350025642e25647300484f4c44000b0907"
    "06050403030202010101010101010040008000c0010001030140016a018001c0"
    "0200020302800300038004000403048005000580060006800700078008000900"
    "0a000b000c000d000e000f0010000000000000110000000d00000001400d2978"
    "400d2934fff80000fff80000fff80000fff80000fff80000fff80000fff80000"
    "fff80000fff80000fff80000fff80000fff80000fff80000fff80000fff80000"
    "fff80000fff80000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000005042000000464d2053594e5448000000"
    "00005054434800005241544f0000494e445800005241544500004644424b0000"
    "4445430000004c4f4f500000534c494300004c454e0000005241544500005453"
    "5452000054534e5300004000007f004f01000000014000000004000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000007900000080000000800000008000000080000000800000"
    "000400000002000000020000000200000004000000804003b4b0400d257a400d"
    "25604003c7a0400d2560400d25fc4003b64c4003c14c4003b2ec4003b6044003"
    "b6a400000000400479b4400d2694400d269a400479b4400d26a0400d26a64004"
    "6c2840046f1040046f1040046f1040046c280000000040032d08000000000000"
    "0000400328e40000000040032f28000000000000000000000000000000000000"
    "00000000000040038d9440038d9440038d9440038ddc40038d9440038d940000"
    "0000000000000000000000000000000000000000000000001751555515510000"
    "0000000000003f80000020800000208000003f80000004000000040000001500"
    "00000e000000040000003f80000020800000208000003f800000000000000000"
    "000000000000000000000000000000000000000000003f800000208000002080"
    "00002080000020800000208000003f8000000000000000000000000000000000"
    "000000000000000000000000000007f000000e10000004100000001000000010"
    "000000100000001000000010000000100000001000000410000007f000000000"
    "0000000000000000000000000000800000008000000080000000800000008000"
    "0000800000008000000080000000800000008000000080000000800000008000"
    "00008000000080000000000000000000"
)
assert len(PINNED_PAGE) == PAGE_LEN, len(PINNED_PAGE)
assert PINNED_PAGE[:4] == bytes.fromhex("20300c00")     # pg_resolve replays the table load


def emit_page(addr: int):
    """The source is the only truth for the bytes (b""); the resolver's kind-0
    table load becomes a jmp to pg_resolve (+0)."""
    assert addr == PAGE_AT, "the page cave is pinned"
    return b"", ((RESOLVER_HOOK, RESOLVER_STOCK,
                  bytes.fromhex("4ef9") + addr.to_bytes(4, "big")),)


MODULE = Module(
    name="synth",
    key="SYNTH MACHINE",
    kind=Kind.CF_PATCH,
    doc="A FLEX track whose sample is named SYNTH* plays a two-operator FM "
        "voice (STRT/LEN/RTRG/RTIM = ratio/index/feedback/decay); the DSP "
        "shapes and effects it as a sample. Its PLAYBACK page reads RATO/INDX/"
        "FDBK/DEC with icons and the title FM SYNTH.",
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
        CavePatch(
            label="synth page",
            cave_addr=PAGE_AT,                # pinned: the descriptor clone's pointers
            pinned=PINNED_PAGE,
            source="modules/synth/page.s",
            emit=emit_page,
            reference=lambda addr: PINNED_PAGE,
            report_note=" (page-descriptor resolver 0x40031ece -> pg_resolve: the "
                        "PLAYBACK page of a SYNTH track reads RATIO/INDEX/FDBK/DECAY "
                        "with icons; title FM SYNTH)",
        ),
    ),
)
