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

# synth.s layout (24 Sep 2026, GLIDE): sy_render at +0, sy_glide +0x326,
# sy_slew +0x32e, the ratio table +0x3be, the sine table +0x400 (.balign),
# the per-track state +0x604 (8 x 44 bytes; +40 = the slewed PTCH word).
CAVE_LEN = 0x764

# Ratified bytes: synth.s with m68k-elf-as -mcpu=5475, linked at 0x400d7000
# and 0x400d7300 (identical: OS absolutes and pc-relative references only).
# Pinned 24 Sep 2026 (GLIDE; linked at 0x400d7000 and 0x400d7300: identical).
PINNED = bytes.fromhex(
    "4fefffd048d77cfc247980001c80242f0034762c4c02380047fa05ead7c32879"
    "800062a878007210b2af0040660000c841f946104d0c41f02800081000046700"
    "0098263c000000a84c02380041f9800049d8207038082608670000780483100b"
    "14f00c8300024640640000682248263c000000ff7398670000140c810000002f"
    "66000004224853836600ffea41fa032a760573987b99b2856600003853836600"
    "fff242ab000442ab002042ab0018223c01000000274100082079800062a442a8"
    "000473d4e189e989274100287601600000047600174300244a2b002467000018"
    "7dd46100024a7bec000638bc4000397c7f00000678012f2f00402f2f00402f2f"
    "00402f2f00404eb9400040084fef00102f40002c4a8467000146388639450006"
    "2006a3430c4040006f00000a048000003c0076002200ea8041f9400aa29441f0"
    "0c00741be5a92418e289a102a4982901a401080075ac001b6600002a24050c42"
    "7f006c000020a1c04842e58a6400000ae28a44826000000ae28a068280000000"
    "a4000900a1c0e6a0223c0184cbb7a0010800a1c0eb882740000c73ec0002e089"
    "e48941fa021a73f01a00e0804c0108002740001073ec0008203c000002044c00"
    "1000e0892741001c73ec000ae0896700004224014c021000203c002ed1e04c41"
    "00000c80000fffff6f000008203c000fffff222b00080481001000006f00001e"
    "e089e8894c001000e08993ab00086000000c223c010000002741000873ec0004"
    "203c00000a444c001000e089242b0008e08ae88a4c021000e089e88927410014"
    "222b00180681000010000c81000080006f000008223c00008000274100184a2b"
    "0024670000b4242f0034263c000000a84c02380041f9800049d84a3038006700"
    "00987faa00036700009043ea001020132c2b00042a2b000c246b0010286b0020"
    "41fa015e7618220c4c2b1800001cd2862401e6aad48279702800757028029484"
    "e08973c14c012800e082e082d88228444c2b48000014d8802404e6aad4827370"
    "2800757028029481e08c79c44c042800e082e082d2824c2b18000018d2814241"
    "22c122c1d085dc8a53876600ff9a268027460004274c0020202f002c4cd77cfc"
    "4fef00304e7571b9400d2cdc4e756100fff622002006e188e9884a816700006c"
    "767f96812e3c00000d804c0730002203484173c177c32e3c000001e04c073000"
    "484377c341f9400aa31426303c00e08be48b2e3c000017c74c0730007e189e81"
    "eeab222b002890816a0000124480e0884c030000e088928060000012e0884c03"
    "0000e088d28060000004220027410028e089e8892c014e7553594e5448000040"
    "008000c0010001030140016a018001c002000203028003000380040004030480"
    "050005800600068007000780080009000a000b000c000d000e000f0010000000"
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
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "00000000"
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
PAGE_LEN = 1948
RESOLVER_HOOK = 0x40031ece
RESOLVER_STOCK = bytes.fromhex("20300c00" "6002")
# Ratified bytes: page.s with m68k-elf-as -mcpu=5475, linked at PAGE_AT (23 Sep 2026: the plain icons).
PINNED_PAGE = bytes.fromhex(
    "20300c000c80400d31ae66000078243c000018b24c012800d4892803e58cd883"
    "d4842042d1fc0008f04b75900c820000007f62000050283c000004484c024800"
    "0684100b14f020442248283c000000ff7b98670000140c850000002f66000004"
    "224853846600ffea41fa002078057b987599ba826600000e53846600fff241fa"
    "047820084ef940031ed653594e5448002f2f00084879400b465d2f2f000c4eb9"
    "40013a084fef000c4e752f02202f000ce48802800000001f41fa036073f00a00"
    "2001e0880281000000ff74644c021000e0896700003e0c81000000326700001c"
    "2f012f00487a030a2f2f00144eb940013a084fef0010600000302f00487a02fa"
    "2f2f00104eb940013a084fef000c600000182f004879400b465d2f2f00104eb9"
    "40013a084fef000c241f4e752f02202f000c6700007c22004c001000203c0000"
    "07d04c010000068000001f80223c00003f014c4100000c80000003e86400001c"
    "2f004879400b465d2f2f00104eb940013a084fef000c60000048223c000003e8"
    "24004c41200272644c4100002202e789d282d28290812f002f02487a02612f2f"
    "00144eb940013a084fef001060000012487a02522f2f000c4eb940013a08508f"
    "241f4e7570006000001470016000000e7002600000087003600000024fefffd4"
    "48d77cfc2e002f2f00482f2f00482f2f00482f2f00482f2f00482f2f00482f2f"
    "00484eb9400479b44fef001c202f004008000001660001902c2f003c4a876700"
    "00125387670000185387670000b2600000c841fa0456610001786000012641fa"
    "05166100016c220670034c001000707f4c401001740b94817008720161000174"
    "2406700a4c002000707f4c40200267000012700672016100015a700a72016100"
    "015224060482000000146f00002270084c002000706b4c402002670000127004"
    "72016100012e700c72016100012624060482000000386f0000aa70064c002000"
    "70474c4020026700009a7002720161000102700e7201610000fa6000008641fa"
    "03ee610000cc4a866700007841fa0424610000ce6000006c41fa045c610000b2"
    "70017201740b610000ca2a3c00007fff4a86670000122a06700d4c005000707f"
    "4c4050055485780b7e0224075382e98a4c4520020c82000000106f0000047410"
    "41fa00c775b02800264220072202240461000080280b52870c870000000f6f00"
    "ffca202f0040080000006700001841fa0144701022100a81fff0000020c15380"
    "6c00fff2202f00345e802f00202f003452802f002f2f0050487a00c24eb94001"
    "28a84fef00104cd77cfc4fef002c4e7543fa0102701022d853806c00fffa4e75"
    "43fa00f270102218839953806c00fff84e7541fa00e041f00c00263c80000000"
    "e2ab8790e28b5281b4816c00fff64e7525642e253032640025642e350025642e"
    "25647300484f4c44000b090706050403030202010101010101010040008000c0"
    "010001030140016a018001c00200020302800300038004000403048005000580"
    "0600068007000780080009000a000b000c000d000e000f001000000000000011"
    "0000000d00000001400d2984400d2940fff80000fff80000fff80000fff80000"
    "fff80000fff80000fff80000fff80000fff80000fff80000fff80000fff80000"
    "fff80000fff80000fff80000fff80000fff80000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000050420000"
    "00464d2053594e544800000000005054434800005241544f0000494e44580000"
    "5241544500004644424b00004445430000004c4f4f500000534c494300004c45"
    "4e00000052415445000054535452000054534e5300004000007f004f01000000"
    "0140000000040000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000007900000080000000800000"
    "0080000000800000008000000004000000020000000200000002000000040000"
    "00804003b4b0400d257a400d25604003c7a0400d2560400d25fc4003b64c4003"
    "c14c4003b2ec4003b6044003b6a400000000400479b4400d2694400d269a4004"
    "79b4400d26a0400d26a640046c2840046f1040046f1040046f1040046c280000"
    "000040032d080000000000000000400328e40000000040032f28000000000000"
    "00000000000000000000000000000000000040038d9440038d9440038d944003"
    "8ddc40038d9440038d9400000000000000000000000000000000000000000000"
    "0000000017515555155100000000000000003f80000020800000208000003f80"
    "00000400000004000000150000000e000000040000003f800000208000002080"
    "00003f8000000000000000000000000000000000000000000000000000000000"
    "00003f80000020800000208000002080000020800000208000003f8000000000"
    "000000000000000000000000000000000000000000000000000007f000000e10"
    "0000041000000010000000100000001000000010000000100000001000000010"
    "00000410000007f0000000000000000000000000000000000000800000008000"
    "0000800000008000000080000000800000008000000080000000800000008000"
    "00008000000080000000800000008000000080000000000000000000"
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
