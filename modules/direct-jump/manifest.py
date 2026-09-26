"""DIRECT JUMP -- CHAIN AFTER's value 1 becomes DIRECT: a pattern selected
while the sequencer runs starts at the next step, at the step count the
old pattern had reached (the Analog Four / Rytm behaviour), instead of at
the old pattern's end or after its CHAIN AFTER length. Off by default:
the value is an existing, unused position of an existing project setting,
so a project that never selects it plays exactly as stock.

WHERE THE OPTION LIVES, AND WHY. The Octatrack's own "change length" is
CHAIN AFTER -- PROJECT > CONTROL > SEQUENCER, the LEVEL knob steps it
through PAT.LEN, 2/16 .. 256/16 -- stored as one byte (0x8000004e, mirror
0x100b14ae, 0..16 indexing the step table at 0x400d80dc: -1, 1, 2, 3, 4, 6,
8 .. 256) and written to the project file as PATTERN_CHANGE_CHAIN_BEHAVIOR=<n>.
INDEX 1 ("1 step") IS UNUSED BY STOCK: the menu setter skips it in the
direction of travel (0x40065a22..2a: a result of 1 becomes delta + 1, i.e.
2 or 0), the project loader bumps a saved 1 to 2 (0x40087820..38, after its
0..16 clamp at 0x4008780c/12), and the UI mirror's sanitiser (0x40010212..38)
only clamps 0..16. So DIRECT takes index 1 (24 Sep 2026; the 13 Sep build
used an 18th index, which needed two relocated 18-entry tables, eight `lea`
repoints and six widened clamps): the setter's skip and the loader's bump
become plain branches (two 2-byte pokes), the stock step table's entry 1
(0x400d80e0) becomes -1 so every stock reader -- the tick handler
(0x400a4154/0x400a4310), the chain advance, PATTERN SETTINGS' "exceeds the
pattern" blink (0x4006e85c, 0x40081c60, 0x40081fb2, 0x40082726), the
countdown/LED page (0x400a29c2, 0x400a3668) -- treats DIRECT as PAT.LEN,
and the label table's entry 1 (0x400b27ec) points at stock's own "DIRECT"
string (0x400b6912). The menu reads PAT.LEN, DIRECT, 2/16, 3/16 .. 256/16.

WHAT ELSE TREATS INDEX 1 SPECIALLY (checked, unchanged): the PATTERN
SETTINGS per-pattern CHAIN BEHAVIOR setter (0x40081d74..0x40081e30) skips
1 the same way and shows its own label tables (0x400b2fae PLEN/1/16/..,
0x400b2f52 and 0x400b2ff2 TR.LEN/1/16/..), so DIRECT stays a PROJECT-level
choice: a pattern on USE PAT SET. keeps its own length, one on USE PRJ SET.
(byte +0x8e56 < 0) follows the project. The MIDI program-change path
(0x4000dada), the [PATTERN]/[BANK] + [TRIG] keys (0x40056b68, 0x40055f84),
the arranger (0x4004a652, with its row's absolute change-at) all reach the
same setter 0x400a0570 that dj_queue hooks; none reads the index itself.
MIGRATION: a project saved by the 13 Sep build (PATTERN_CHANGE_CHAIN_BEHAVIOR
=17) loads as 256/16 through the stock clamp; a stock unit loads a project
saved with DIRECT (=1) as 2/16 through its own bump.

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

MEASURED (README): stock and the remix with CHAIN AFTER = PAT.LEN switch
at the pattern end, byte-identical sequencer traces; with DIRECT the
switch lands at the next step and the step counter continues; the value
survives SAVE PROJECT + a cold boot on the persistent card. Flashed as
OCTATRICK1/2/3 in its 18th-index form (23 Sep 2026); the index-1 form is
verified in the emulator only.
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
CAVE_LEN = 0x166

# Index 1 = DIRECT: four asserted pokes, all fixed addresses.
STEPS_ENTRY1 = 0x400d80e0     # the stock step table 0x400d80dc, entry 1: 1 -> -1 (= PAT.LEN)
LABELS_ENTRY1 = 0x400b27ec    # the label table 0x400b27e8, entry 1: "1/16" -> "DIRECT"
DIRECT_STR = 0x400b6912       # stock's own "DIRECT\0"
POKES = (
    (STEPS_ENTRY1, "00000001", "ffffffff", "step table entry 1: DIRECT reads as PAT.LEN"),
    (LABELS_ENTRY1, "400b5771", "400b6912", "label table entry 1: DIRECT"),
    (0x40065a26, "6604", "6004", "menu setter: no longer skips index 1 (bne -> bra)"),
    (0x40087826, "6600", "6000", "project loader: no longer bumps 1 to 2 (bne -> bra)"),
)

# direct_jump.s assembled with m68k-elf-as -mcpu=5475 and linked at
# 0x400d7000 (identical at 0x400d7300: OS absolutes and pc-relative
# references only; 24 Sep 2026, the index-1 form: 358 bytes). These bytes ARE
# the contract: the build links the source at the address the cave lands on
# and refuses if it differs.
PINNED = bytes.fromhex(
    "13c4800065bf13c5800065c070ffb082660000da4ab9460d1aec660000d07139"
    "800065beb0856600000e7139800065bdb084670000b82f012f087139800065be"
    "223c00008ed84c0108007339800065bd2040203c0009b3404c001800d1c1d1fc"
    "400eb03671106a00000871398000004e0c80000000016600007071f9800065b2"
    "528023c08000662c26002005223c00008ed84c01080022042040203c0009b340"
    "4c001800d1c1d1fc400eb0344a2800016700000a7168fffc600000067128ffff"
    "6f0000084c403001260141fa002210bc00017139800065b60c80000000026d00"
    "0008117c00010001205f221f4e75000000000000000000000000000000000000"
    "20398000663023c08000663823c0800066282f0841faffd84a10670000464210"
    "71f9800065b2b0b98000662c6600003042b9800066304a280001670000222f01"
    "2f097139800065be2f007139800065bd2f004eb94009e884508f225f221f4228"
    "0001205f4e75"
)
assert len(PINNED) == CAVE_LEN, len(PINNED)
assert PINNED[:12] == QUEUE_HOOK_STOCK                            # dj_queue replays them
assert PINNED[OFF_APPLY:OFF_APPLY + 18] == APPLY_HOOK_STOCK       # dj_apply replays them
assert bytes.fromhex("0c8000000001") in PINNED                    # cmpi.l #1: DIRECT is index 1


def emit(addr: int):
    """The source is the only truth for the bytes (b""); one poke depends on
    where the cave lands (the second hook), the four index-1 pokes do not."""
    pokes = [
        (APPLY_HOOK, APPLY_HOOK_STOCK,
         b"\x4e\xb9" + (addr + OFF_APPLY).to_bytes(4, "big") + b"\x4e\x71" * 6),
    ]
    for site, expect, write, _note in POKES:
        pokes.append((site, bytes.fromhex(expect), bytes.fromhex(write)))
    return b"", tuple(pokes)


MODULE = Module(
    name="direct-jump",
    key="DIRECT JUMP",
    kind=Kind.CF_PATCH,
    doc="CHAIN AFTER: DIRECT (its unused value 1) -- a pattern change lands at "
        "the next step, the step count continuing (A4/Rytm direct jump).",
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
            report_note=" (CHAIN AFTER's value 1 = DIRECT; second hook at "
                        "0x400a44e2, 4 fixed pokes: step/label entry 1, setter, loader)",
        ),
    ),
)
