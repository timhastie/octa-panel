"""DIRECT JUMP -- CHAIN AFTER gains an 18th value, DIRECT: a pattern selected
while the sequencer runs starts at the next step, at the step count the
old pattern had reached (the Analog Four / Rytm behaviour), instead of at
the old pattern's end or after its CHAIN AFTER length. Off by default:
the value is one more position of an existing project setting, so a
project that never selects it plays exactly as stock.

WHERE THE OPTION LIVES, AND WHY. The Octatrack's own "change length" is
CHAIN AFTER -- PROJECT > CONTROL > SEQUENCER, the LEVEL knob steps it
through PAT.LEN, 2/16 .. 256/16 -- stored as one byte (0x8000004e, mirror
0x100b14ae, 0..16 indexing the step table at 0x400d80dc) and written to
the project file as PATTERN_CHANGE_CHAIN_BEHAVIOR=<n>, so a 17th index
persists with the project for free: the loader clamp (0x4008780c) is
widened by one, the menu setter's three clamps (0x40065a02/1c/3a) and the
getter's bound (0x40065a4e) likewise, and the getter's label table is
relocated into the cave with stock's own "DIRECT" string (0x400b6912)
appended. The other candidate, a PERSONALIZE row, needs the same three
relocated arrays as the historical patch_menu.s and lives in a RAM block no
serializer writes (docs/history/NOTES.md); CHAIN AFTER is the parameter
the manual documents for this exact purpose and it is already per-project
and per-pattern (USE PAT SET.).

THE SEQUENCER SIDE, from the disassembly (all measured in ot_emu, README):
  * a pattern selected while playing goes 0x40056b30 ([PATTERN]+[TRIG]) ->
    0x400a1030(bank, pattern) -> the setter 0x400a0570 with (start step 0,
    change-at -1), whose transport-running branch at 0x400a06d6 stores the
    QUEUED pair 0x800065bf/c0, the start step to 0x80006630 and the change-at
    to 0x80006634 -- the arranger's OFFSET / LENGTH mechanism, shared;
  * the tick handler (0x400a3fdc) at tick 0 of every step increments the
    step counter 0x800065b2 and switches when the counter reaches the
    playing pattern's end (0x400a4388), its CHAIN AFTER multiple
    (0x400a4352, table 0x400d80dc[idx]) or the absolute change-at
    0x8000662c (0x400a439a); the apply block then takes the queued pair
    (0x400a44d0) and positions every track from 0x80006630 (0x400a44e2..).
  * DIRECT is therefore two hooks and no new mechanism: dj_queue (at
    0x400a06d6) sets change-at := counter + 1 and start step := that count
    wrapped to the new pattern's length; dj_apply (at 0x400a44e2) clears
    the start step once it has been latched so later restarts begin at 0
    as stock, and sends the MIDI program change stock would have sent two
    ticks earlier if the selection came after that tick.
  * every OTHER reader of the step table (eight `lea 0x400d80dc`) is
    repointed to the cave's 18-entry copy, whose 18th entry is -1: for
    chains, the PATTERN SETTINGS blink check and the countdown, DIRECT
    reads as PAT.LEN. Without that, index 17 would read the next table's
    first word (1) and chained patterns would advance every step.

WHAT IS NOT CHANGED. The per-pattern CHAIN BEHAVIOR (PATTERN SETTINGS >
USE PAT SET.) keeps its 0..16 range: DIRECT is selected per project, and a
pattern whose own setting is in use keeps it. Chains ([PATTERN] + several
[TRIG]s) change at the pattern end as before. Arranger rows are untouched
(dj_queue steps aside for any absolute change-at and while the arranger
plays). A project saved with DIRECT and loaded by STOCK firmware clamps
the value to 256/16.

MEASURED (README): stock and the remix with CHAIN AFTER = PAT.LEN switch
at the pattern end, byte-identical sequencer traces; with DIRECT the
switch lands at the next step and the step counter continues; the value
survives SAVE PROJECT + a cold boot on the persistent card; the oracle
battery's UART/peek streams are identical between the stock image and the
remix. UNFLASHED.
"""

from remix.schema import CavePatch, Kind, Module

# ---- the two hook sites ------------------------------------------------------
# The setter 0x400a0570, transport running: the two moveb that publish the
# queued bank/pattern. Twelve bytes, two instructions, replayed in the cave.
QUEUE_HOOK = 0x400a06d6
QUEUE_HOOK_STOCK = bytes.fromhex("13c4800065bf" "13c5800065c0")
# The tick handler's boundary apply, right after the playing pair took the
# queued pair: three longword moves that latch the start step. Eighteen
# bytes, replayed in the cave; planted by emit() because the cave floats.
APPLY_HOOK = 0x400a44e2
APPLY_HOOK_STOCK = bytes.fromhex("203980006630" "23c080006638" "23c080006628")

# ---- the cave's fixed layout (direct_jump.s .org) --------------------------
OFF_APPLY = 0x100          # dj_apply
OFF_LENS = 0x180           # 18 longs: CHAIN AFTER index -> steps
OFF_LABELS = 0x200         # 18 longs: CHAIN AFTER index -> label string
CAVE_LEN = 0x248

# Every stock reference to the 17-entry step table 0x400d80dc: six-byte
# `lea abs.l,An`, operand at +2. All eight move to the cave's copy so no
# reader ever indexes past the stock table (index 17 there is the next
# table's first word).
LENS_TABLE = 0x400d80dc
LENS_REFS = (0x4006e85c, 0x40081c60, 0x40081fb2, 0x40082726,
             0x400a29c2, 0x400a3668, 0x400a4154, 0x400a4310)
# The SEQUENCER menu's CHAIN AFTER getter (0x40065a40): its label table.
LABELS_TABLE = 0x400b27e8
LABELS_REF = 0x40065a54

# The clamps that keep the index at 16, each a `moveq #16,Dn`, widened to 17:
# the menu setter 0x400659ec (wrap bound, clamp bound, clamp value), the
# getter's bound (0x40065a40), the project loader (0x400877e0's parse of
# PATTERN_CHANGE_CHAIN_BEHAVIOR).
CLAMPS = (
    (0x40065a02, "7210", "7211", "setter: wrap bound"),
    (0x40065a1c, "7210", "7211", "setter: clamp bound"),
    (0x40065a3a, "7010", "7011", "setter: clamp value"),
    (0x40065a4e, "7410", "7411", "getter: label bound"),
    (0x4008780c, "7410", "7411", "project loader: clamp bound"),
    (0x40087812, "7010", "7011", "project loader: clamp value"),
)

# direct_jump.s assembled with m68k-elf-as -mcpu=5475 and linked at
# 0x400d7000 (identical at 0x400d7300: OS absolutes and pc-relative
# references only). These bytes ARE the contract: the build links the
# source at the address the cave lands on and refuses if it differs.
PINNED = bytes.fromhex(
    "13c4800065bf13c5800065c070ffb082660000da4ab9460d1aec660000d07139"
    "800065beb0856600000e7139800065bdb084670000b82f012f087139800065be"
    "223c00008ed84c0108007339800065bd2040203c0009b3404c001800d1c1d1fc"
    "400eb03671106a00000871398000004e0c80000000116600007071f9800065b2"
    "528023c08000662c26002005223c00008ed84c01080022042040203c0009b340"
    "4c001800d1c1d1fc400eb0344a2800016700000a7168fffc600000067128ffff"
    "6f0000084c403001260141fa002210bc00017139800065b60c80000000026d00"
    "0008117c00010001205f221f4e75000000000000000000000000000000000000"
    "20398000663023c08000663823c0800066282f0841faffd84a10670000464210"
    "71f9800065b2b0b98000662c6600003042b9800066304a280001670000222f01"
    "2f097139800065be2f007139800065bd2f004eb94009e884508f225f221f4228"
    "0001205f4e750000000000000000000000000000000000000000000000000000"
    "ffffffff0000000100000002000000030000000400000006000000080000000c"
    "00000010000000180000002000000030000000400000006000000080000000c0"
    "00000100ffffffff000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "400b5f62400b5771400b5f6b400b5780400b5f77400b5f71400b5f96400b5f6a"
    "400b5f70400b5f76400b5f7c400b5f82400b5f88400b5f8e400b5f94400b5f9b"
    "400b5fa2400b6912"
)
assert len(PINNED) == CAVE_LEN, len(PINNED)
assert PINNED[OFF_APPLY:OFF_APPLY + 18] == APPLY_HOOK_STOCK       # dj_apply replays them
assert PINNED[OFF_LENS:OFF_LENS + 4] == b"\xff\xff\xff\xff"      # PAT.LEN
assert PINNED[OFF_LENS + 17 * 4:OFF_LENS + 18 * 4] == b"\xff\xff\xff\xff"   # DIRECT reads as PAT.LEN
assert PINNED[OFF_LABELS + 17 * 4:OFF_LABELS + 18 * 4] == (0x400b6912).to_bytes(4, "big")


def emit(addr: int):
    """The source is the only truth for the bytes (b""); the pokes depend on
    where the cave lands: the second hook and the two table repoints."""
    pokes = [
        (APPLY_HOOK, APPLY_HOOK_STOCK,
         b"\x4e\xb9" + (addr + OFF_APPLY).to_bytes(4, "big") + b"\x4e\x71" * 6),
        (LABELS_REF + 2, LABELS_TABLE.to_bytes(4, "big"),
         (addr + OFF_LABELS).to_bytes(4, "big")),
    ]
    for ref in LENS_REFS:
        pokes.append((ref + 2, LENS_TABLE.to_bytes(4, "big"),
                      (addr + OFF_LENS).to_bytes(4, "big")))
    for site, expect, write, _note in CLAMPS:
        pokes.append((site, bytes.fromhex(expect), bytes.fromhex(write)))
    return b"", tuple(pokes)


MODULE = Module(
    name="direct-jump",
    key="DIRECT JUMP",
    kind=Kind.CF_PATCH,
    doc="CHAIN AFTER: DIRECT -- a pattern change lands at the next step, "
        "the step count continuing (A4/Rytm direct jump).",
    cf_patches=(
        CavePatch(
            label="direct jump cave",
            cave_addr=None,                   # floats: position independent
            pinned=PINNED,
            source="modules/direct-jump/direct_jump.s",
            hook_addr=QUEUE_HOOK,
            hook_stock=QUEUE_HOOK_STOCK,
            emit=emit,
            reference=lambda addr: PINNED,    # the same bytes at any address
            report_note=" (CHAIN AFTER gains DIRECT; second hook at "
                        "0x400a44e2, 9 table repoints, 6 clamps)",
        ),
    ),
)
